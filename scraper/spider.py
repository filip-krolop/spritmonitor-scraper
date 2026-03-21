"""
Core scraping orchestration.

Coordinates the HTTP client, parsers, aggregator, validator, storage
and progress tracker to implement all four CLI modes.

Per the implementation instructions every vehicle's detail page is
visited and each ``td.showhide`` link is followed so that per-vehicle
records (keyed by the numeric vehicle-detail ID) are produced.
"""

import logging
import re
import time
from datetime import datetime
from typing import Any

from scraper.config import Config
from scraper.http_client import HttpClient
from scraper.parser import (
    parse_makes,
    parse_models,
    parse_models_ajax,
    parse_vehicles,
    parse_max_page,
    parse_model_context,
    parse_total_vehicles,
    parse_vehicle_detail,
    parse_vehicle_detail_expanded,
)
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
            source_url = record.get("source_url", "")
            vehicle_id = record.get("id")

            if not source_url or not vehicle_id:
                continue

            log.info(
                "  Refreshing %d/%d: vehicle %s (%s %s)",
                i, len(stale),
                vehicle_id,
                record.get("make_name", "?"),
                record.get("model_name", "?"),
            )

            self._scrape_single_vehicle(
                vehicle_id=int(vehicle_id),
                detail_url=source_url,
                make_id=record.get("make_id", 0),
                make_name=record.get("make_name", ""),
                model_id=record.get("model_id", 0),
                model_name=record.get("model_name", ""),
                list_entry={},
                storage=storage,
            )

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
        make_id = make["make_id"]
        make_slug = make.get("make_slug", "")
        ajax_url = (
            f"{self.cfg.AJAX_MODEL_URL}"
            f"?manuf={make_id}&allowempty=false"
        )
        response_text = self.http.get(ajax_url)
        if response_text is None:
            log.warning(
                "Could not fetch models for make_id=%d (AJAX failed).",
                make_id,
            )
            return []
        return parse_models_ajax(response_text, make_id, make_slug)

    # ══════════════════════════════════════════════════════════════════
    # Internal: scrape a single model  (REWRITTEN)
    # ══════════════════════════════════════════════════════════════════

    def _scrape_model(
        self,
        make: dict[str, Any],
        model: dict[str, Any],
        storage: Storage,
    ):
        """
        Fetch the model's vehicle-list pages, then follow every
        vehicle's detail link (and its cdetail expansions) to build
        one record per vehicle.
        """
        make_id = make["make_id"]
        make_name = make["make_name"]
        model_id = model["model_id"]
        model_name = model["model_name"]
        base_url = self._abs_url(model["url"])

        # ── 1. Collect vehicle entries from list pages ────────────────
        all_entries: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        html = self.http.get(base_url)
        if html is None:
            log.warning("  Could not fetch model page: %s", base_url)
            self.progress.increment_errors()
            return

        for v in parse_vehicles(html):
            vid = v.get("vehicle_id")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                all_entries.append(v)

        max_page = min(parse_max_page(html), self.cfg.MAX_PAGES_PER_MODEL)
        if max_page > 1:
            log.info("    Pagination: %d pages detected.", max_page)
            for page in range(2, max_page + 1):
                sep = "&" if "?" in base_url else "?"
                page_url = f"{base_url}{sep}page={page}"
                page_html = self.http.get(page_url)
                if page_html is None:
                    break
                page_vehicles = parse_vehicles(page_html)
                if not page_vehicles:
                    break
                for v in page_vehicles:
                    vid = v.get("vehicle_id")
                    if vid and vid not in seen_ids:
                        seen_ids.add(vid)
                        all_entries.append(v)

        if not all_entries:
            log.info(
                "    No vehicle entries for %s %s.", make_name, model_name
            )
            return

        log.info(
            "    %d unique vehicles found for %s %s — fetching details …",
            len(all_entries), make_name, model_name,
        )

        # ── 2. For each vehicle, fetch detail page + cdetail ─────────
        records: list[dict[str, Any]] = []

        for idx, entry in enumerate(all_entries, 1):
            vehicle_id = entry.get("vehicle_id")
            detail_url = entry.get("detail_url")
            if not vehicle_id or not detail_url:
                continue

            self._scrape_single_vehicle(
                vehicle_id=vehicle_id,
                detail_url=detail_url,
                make_id=make_id,
                make_name=make_name,
                model_id=model_id,
                model_name=model_name,
                list_entry=entry,
                storage=storage,
            )

        log.info(
            "    Done with %s %s.", make_name, model_name,
        )

    # ══════════════════════════════════════════════════════════════════
    # Internal: scrape one vehicle detail page
    # ══════════════════════════════════════════════════════════════════

    def _scrape_single_vehicle(
        self,
        vehicle_id: int,
        detail_url: str,
        make_id: int,
        make_name: str,
        model_id: int,
        model_name: str,
        list_entry: dict[str, Any],
        storage: Storage,
    ):
        """Fetch a vehicle's detail page (+ cdetail pages) and store
        one record keyed by *vehicle_id*."""

        abs_detail_url = self._abs_url(detail_url)

        detail_html = self.http.get(abs_detail_url)
        if detail_html is None:
            self.progress.increment_errors()
            return

        detail = parse_vehicle_detail(detail_html, vehicle_id)

        # Follow each cdetail link (expanded fuel-type sections)
        expanded: dict[str, Any] = {}
        for clink in detail.get("cdetail_links", []):
            abs_clink = self._abs_url(clink)
            chtml = self.http.get(abs_clink)
            if chtml:
                exp = parse_vehicle_detail_expanded(chtml)
                # Merge without overwriting existing keys
                for k, v in exp.items():
                    if k not in expanded:
                        expanded[k] = v

        record = self._build_record(
            vehicle_id=vehicle_id,
            make_id=make_id,
            make_name=make_name,
            model_id=model_id,
            model_name=model_name,
            list_entry=list_entry,
            detail=detail,
            expanded=expanded,
            source_url=abs_detail_url,
        )

        if record is None:
            self.progress.increment_errors()
            return

        valid, invalid = validate_records([record], self.cfg)
        storage.add_records(valid)
        storage.add_errors(invalid)
        self.progress.increment_records(len(valid))
        self.progress.increment_errors(len(invalid))

    # ══════════════════════════════════════════════════════════════════
    # Internal: build one output record
    # ══════════════════════════════════════════════════════════════════

    def _build_record(
        self,
        vehicle_id: int,
        make_id: int,
        make_name: str,
        model_id: int,
        model_name: str,
        list_entry: dict[str, Any],
        detail: dict[str, Any],
        expanded: dict[str, Any],
        source_url: str,
    ) -> dict[str, Any] | None:
        """Assemble a flat output record from parsed data."""

        # ── engine / variant name from detail h1 ─────────────────────
        title = detail.get("title", "")
        engine_name = self._extract_variant_from_title(
            title, make_name, model_name
        )

        # ── fuel type ────────────────────────────────────────────────
        raw_fuel = detail.get("fuel_type_raw") or list_entry.get("fuel_type_raw")
        fuel_type = self._normalise_fuel(raw_fuel)

        # ── consumption (prefer detail page, fall back to list) ──────
        consumption = list_entry.get("consumption")
        consumption_unit = list_entry.get("consumption_unit")
        co2 = None
        fuel_cost = None

        fuel_sections = detail.get("fuel_sections", [])
        if fuel_sections:
            primary = fuel_sections[0]
            if primary.get("consumption") is not None:
                consumption = primary["consumption"]
                consumption_unit = primary.get(
                    "consumption_unit", consumption_unit
                )
            co2 = primary.get("co2_g_per_km")
            fuel_cost = primary.get("fuel_cost_eur_per_100km")

        if consumption is None:
            log.debug("No consumption for vehicle %d — skipping.", vehicle_id)
            return None

        # ── power / year / transmission from detail ──────────────────
        power_kw = detail.get("power_kw") or list_entry.get("power_kw")
        power_ps = detail.get("power_ps") or list_entry.get("power_ps")
        if power_kw is None and power_ps is not None:
            power_kw = int(round(power_ps * 0.7355))
        transmission = (
            detail.get("transmission") or list_entry.get("transmission")
        )
        year = detail.get("year") or list_entry.get("year")
        engine_ccm = (
            detail.get("engine_ccm") or list_entry.get("engine_ccm")
        )
        fuelings = list_entry.get("fuelings")

        record: dict[str, Any] = {
            "id": str(vehicle_id),
            "make_id": make_id,
            "model_id": model_id,
            "make_name": make_name,
            "model_name": model_name,
            "generation_years": f"{year}-{year}" if year else None,
            "year_from": year,
            "year_to": year,
            "engine_name": engine_name or "unknown",
            "engine_ccm": engine_ccm,
            "power_kw": power_kw,
            "fuel_type": fuel_type,
            "transmission": transmission,
            "avg_consumption": round(consumption, 2),
            "min_consumption": round(consumption, 2),
            "max_consumption": round(consumption, 2),
            "sample_count": 1,
            "tank_count": fuelings,
            "low_confidence": True,
            "source_url": source_url,
            # contextual
            "pct_motorway": None,
            "pct_city": None,
            "pct_country": None,
            "consumption_summer": None,
            "consumption_winter": None,
            "fuel_grade_pct_premium": None,
            "co2_g_per_km": co2,
            "fuel_cost_eur_per_100km": fuel_cost,
            "histogram_buckets": None,
        }

        # ── merge expanded cdetail data ──────────────────────────────
        if expanded:
            if "fuel_grade" in expanded:
                record["fuel_grade_pct_premium"] = None  # store name instead
                # store raw grade in histogram_buckets as a note
            # Route-specific consumption (not percentages, but values)
            # Store as contextual info if available
            if "consumption_motorway" in expanded:
                record["consumption_motorway"] = expanded["consumption_motorway"]
            if "consumption_city" in expanded:
                record["consumption_city"] = expanded["consumption_city"]
            if "consumption_country" in expanded:
                record["consumption_country"] = expanded["consumption_country"]

        return record

    # ══════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════

    def _abs_url(self, url: str) -> str:
        if url.startswith("http"):
            return url
        return self.cfg.BASE_URL + url

    def _normalise_fuel(self, raw: str | None) -> str:
        if not raw:
            return "unknown"
        key = raw.strip().lower()
        return self.cfg.FUEL_TYPE_MAP.get(key, key)

    @staticmethod
    def _extract_variant_from_title(
        title: str, make_name: str, model_name: str,
    ) -> str:
        """
        Detail-page ``<h1>`` typically reads
        ``"Make - Model - Variant"``; return the variant part.
        """
        if not title:
            return "unknown"

        parts = [p.strip() for p in title.split(" - ")]

        # Drop the make part
        if parts and parts[0].lower() == make_name.lower():
            parts = parts[1:]

        # Drop the model part
        if parts and parts[0].lower() == model_name.lower():
            parts = parts[1:]

        variant = " ".join(parts).strip()

        # If nothing left, try removing make/model from the raw string
        if not variant:
            cleaned = title
            for word in (make_name, model_name):
                if word:
                    cleaned = re.sub(
                        re.escape(word), "", cleaned, flags=re.I
                    ).strip()
            cleaned = cleaned.strip(" -–—")
            variant = cleaned

        return variant if variant else "unknown"

    def _log_summary(self, storage: Storage, elapsed: float):
        mins = elapsed / 60
        log.info("=" * 60)
        log.info("SCRAPING COMPLETE")
        log.info("  Total records:  %d", storage.record_count)
        log.info("  HTTP requests:  %d", self.http.request_count)
        log.info("  Elapsed time:   %.1f minutes", mins)
        log.info("  Progress stats: %s", self.progress.stats)
        log.info("=" * 60)