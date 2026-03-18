"""
Core scraping orchestration.

Coordinates the HTTP client, parsers, aggregator, validator, storage
and progress tracker to implement all four CLI modes.
"""

import logging
import time
from datetime import datetime
from typing import Any

from scraper.config import Config
from scraper.http_client import HttpClient
from scraper.parser import (
    parse_makes,
    parse_models,
    parse_models_ajax,          # NEW
    parse_vehicles,
    parse_max_page,
    parse_model_context,
    parse_total_vehicles,
)
from scraper.aggregator import aggregate_vehicles
from scraper.validator import validate_records
from scraper.storage import Storage
from scraper.progress import ProgressTracker

log = logging.getLogger(__name__)


class SpritmonitorSpider:
    """Top-level scraper that implements the four run modes."""

    def __init__(self, config: Config):
        self.cfg = config
        self.http = HttpClient(config)
        self.progress = ProgressTracker(config.PROGRESS_FILE)

    # ══════════════════════════════════════════════════════════════════
    # Mode 1: Full import
    # ══════════════════════════════════════════════════════════════════

    def run_full(self):
        """Download everything — all makes, all models."""
        log.info("=" * 60)
        log.info("MODE: full import")
        log.info("=" * 60)
        start = time.time()
        storage = Storage(self.cfg, mode="full")

        makes = self._fetch_makes()
        if not makes:
            log.error("No makes found. Aborting.")
            return

        self.progress.set_total_makes(len(makes))
        log.info("Found %d makes to process.", len(makes))

        for i, make in enumerate(makes, 1):
            make_id = make["make_id"]
            make_name = make["make_name"]
            log.info(
                "── Make %d/%d: %s (id=%d) ──",
                i, len(makes), make_name, make_id,
            )
            self.progress.set_current(make_name=make_name)

            if self.progress.is_make_done(make_id):
                log.info("  ↳ already completed — skipping.")
                continue

            models = self._fetch_models(make)
            if not models:
                log.warning("  No models found for %s.", make_name)
                self.progress.mark_make_done(make_id)
                continue

            for j, model in enumerate(models, 1):
                model_id = model["model_id"]
                model_name = model["model_name"]
                log.info(
                    "  Model %d/%d: %s (id=%d)",
                    j, len(models), model_name, model_id,
                )
                self.progress.set_current(
                    make_name=make_name, model_name=model_name,
                )

                if self.progress.is_model_done(make_id, model_id):
                    log.info("    ↳ already completed — skipping.")
                    continue

                self._scrape_model(make, model, storage)
                self.progress.mark_model_done(make_id, model_id)

                # Periodic save every 20 models
                if j % 20 == 0:
                    storage.save()

            self.progress.mark_make_done(make_id)

        storage.save()
        elapsed = time.time() - start
        self._log_summary(storage, elapsed)

    # ══════════════════════════════════════════════════════════════════
    # Mode 2: Update stale records
    # ══════════════════════════════════════════════════════════════════

    def run_update(self, older_than_days: int = 30):
        log.info("=" * 60)
        log.info("MODE: update (older than %d days)", older_than_days)
        log.info("=" * 60)
        start = time.time()
        storage = Storage(self.cfg, mode="update")

        stale = storage.get_stale_records(older_than_days)
        if not stale:
            log.info("No stale records found. Nothing to update.")
            return

        log.info("Found %d stale records to refresh.", len(stale))

        for i, record in enumerate(stale, 1):
            url = record.get("source_url")
            if not url:
                continue

            log.info(
                "  Refreshing %d/%d: %s %s — %s",
                i, len(stale),
                record.get("make_name", "?"),
                record.get("model_name", "?"),
                record.get("engine_name", "?"),
            )

            # Re-scrape the model page
            make_info = {
                "make_id": record["make_id"],
                "make_name": record["make_name"],
                "make_slug": "",
            }
            model_info = {
                "model_id": record["model_id"],
                "model_name": record["model_name"],
                "model_slug": "",
                "url": url,
            }
            self._scrape_model(make_info, model_info, storage)

        storage.save()
        elapsed = time.time() - start
        self._log_summary(storage, elapsed)

    # ══════════════════════════════════════════════════════════════════
    # Mode 3: Specific make / model
    # ══════════════════════════════════════════════════════════════════

    def run_model(
        self,
        make_name: str | None = None,
        model_name: str | None = None,
        make_id: int | None = None,
    ):
        log.info("=" * 60)
        log.info(
            "MODE: model (make=%s, model=%s, make_id=%s)",
            make_name, model_name, make_id,
        )
        log.info("=" * 60)
        start = time.time()
        storage = Storage(self.cfg, mode="model")

        makes = self._fetch_makes()
        if not makes:
            log.error("No makes found. Aborting.")
            return

        # Filter to requested make
        target_makes = []
        for m in makes:
            if make_id is not None and m["make_id"] == make_id:
                target_makes.append(m)
            elif make_name and make_name.lower() in m["make_name"].lower():
                target_makes.append(m)

        if not target_makes:
            log.error("Make not found: name=%s id=%s", make_name, make_id)
            return

        for make in target_makes:
            log.info(
                "Processing make: %s (id=%d)",
                make["make_name"], make["make_id"],
            )
            models = self._fetch_models(make)

            if model_name:
                models = [
                    m for m in models
                    if model_name.lower() in m["model_name"].lower()
                ]

            if not models:
                log.warning("No matching models found.")
                continue

            for model in models:
                log.info(
                    "  Model: %s (id=%d)",
                    model["model_name"], model["model_id"],
                )
                self._scrape_model(make, model, storage)

        storage.save()
        elapsed = time.time() - start
        self._log_summary(storage, elapsed)

    # ══════════════════════════════════════════════════════════════════
    # Mode 4: New models only
    # ══════════════════════════════════════════════════════════════════

    def run_new(self):
        log.info("=" * 60)
        log.info("MODE: new (only models not yet scraped)")
        log.info("=" * 60)
        start = time.time()
        storage = Storage(self.cfg, mode="new")
        existing_ids = storage.get_existing_ids()

        makes = self._fetch_makes()
        if not makes:
            log.error("No makes found. Aborting.")
            return

        new_count = 0
        for i, make in enumerate(makes, 1):
            make_id = make["make_id"]
            make_name = make["make_name"]
            log.info("── Make %d/%d: %s ──", i, len(makes), make_name)

            models = self._fetch_models(make)

            for model in models:
                model_id = model["model_id"]
                # Check if ANY record for this model exists
                prefix = f"{make_id}_{model_id}_"
                if any(eid.startswith(prefix) for eid in existing_ids):
                    continue

                log.info(
                    "  NEW model: %s %s", make_name, model["model_name"],
                )
                self._scrape_model(make, model, storage)
                new_count += 1

        storage.save()
        elapsed = time.time() - start
        log.info("Found and scraped %d new models.", new_count)
        self._log_summary(storage, elapsed)

    # ══════════════════════════════════════════════════════════════════
    # Internal: fetch makes / models
    # ══════════════════════════════════════════════════════════════════

    def _fetch_makes(self) -> list[dict[str, Any]]:
        html = self.http.get(self.cfg.OVERVIEW_URL)
        if html is None:
            return []
        return parse_makes(html)

    def _fetch_models(self, make: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Fetch models for a given make via the Spritmonitor AJAX endpoint.

        The homepage uses an AJAX call to populate the model <select>
        when the user picks a manufacturer.  We replicate that call:
        GET /en/ajaxModel.action?manuf={make_id}&allowempty=false

        Response format is semicolon-separated CSV:
            "1106,181;1988,914-4;1321,Amarok;452,Golf;"
        """
        make_id = make["make_id"]
        make_slug = make.get("make_slug", "")

        ajax_url = (
            f"{self.cfg.AJAX_MODEL_URL}"
            f"?manuf={make_id}&allowempty=false"
        )
        response_text = self.http.get(ajax_url)
        if response_text is None:
            log.warning(
                "Could not fetch models for make_id=%d (AJAX request failed).",
                make_id,
            )
            return []

        return parse_models_ajax(response_text, make_id, make_slug)

    # ══════════════════════════════════════════════════════════════════
    # Internal: scrape a single model
    # ══════════════════════════════════════════════════════════════════

    def _scrape_model(
        self,
        make: dict[str, Any],
        model: dict[str, Any],
        storage: Storage,
    ):
        make_id = make["make_id"]
        make_name = make["make_name"]
        model_id = model["model_id"]
        model_name = model["model_name"]
        base_url = self._abs_url(model["url"])

        all_vehicles: list[dict[str, Any]] = []
        context: dict[str, Any] = {}

        # Fetch page 1
        html = self.http.get(base_url)
        if html is None:
            log.warning("  Could not fetch model page: %s", base_url)
            self.progress.increment_errors()
            return

        vehicles = parse_vehicles(html)
        all_vehicles.extend(vehicles)
        context = parse_model_context(html)

        # Handle pagination
        max_page = parse_max_page(html)
        max_page = min(max_page, self.cfg.MAX_PAGES_PER_MODEL)

        if max_page > 1:
            log.info("    Pagination: %d pages detected.", max_page)
            for page in range(2, max_page + 1):
                sep = "&" if "?" in base_url else "?"
                page_url = f"{base_url}{sep}page={page}"
                page_html = self.http.get(page_url)
                if page_html is None:
                    log.warning("    Failed to fetch page %d", page)
                    break
                page_vehicles = parse_vehicles(page_html)
                if not page_vehicles:
                    log.debug(
                        "    No vehicles on page %d — stopping.", page,
                    )
                    break
                all_vehicles.extend(page_vehicles)

        if not all_vehicles:
            log.info(
                "  No vehicle data found for %s %s.",
                make_name, model_name,
            )
            return

        log.info(
            "  Parsed %d vehicles for %s %s.",
            len(all_vehicles), make_name, model_name,
        )

        # Aggregate
        records = aggregate_vehicles(
            vehicles=all_vehicles,
            make_id=make_id,
            make_name=make_name,
            model_id=model_id,
            model_name=model_name,
            source_url=base_url,
            config=self.cfg,
            context=context,
        )

        # Validate
        valid, invalid = validate_records(records, self.cfg)
        storage.add_records(valid)
        storage.add_errors(invalid)

        self.progress.increment_records(len(valid))
        self.progress.increment_errors(len(invalid))

        log.info(
            "    → %d valid records, %d invalid.",
            len(valid), len(invalid),
        )

    # ══════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════

    def _abs_url(self, url: str) -> str:
        if url.startswith("http"):
            return url
        return self.cfg.BASE_URL + url

    def _log_summary(self, storage: Storage, elapsed: float):
        mins = elapsed / 60
        log.info("=" * 60)
        log.info("SCRAPING COMPLETE")
        log.info("  Total records:   %d", storage.record_count)
        log.info("  HTTP requests:   %d", self.http.request_count)
        log.info("  Elapsed time:    %.1f minutes", mins)
        log.info("  Progress stats:  %s", self.progress.stats)
        log.info("=" * 60)