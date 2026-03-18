"""Save records to CSV and JSON output files."""

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from scraper.config import Config

log = logging.getLogger(__name__)

CSV_COLUMNS = [
    "id",
    "make_id",
    "model_id",
    "make_name",
    "model_name",
    "generation_years",
    "year_from",
    "year_to",
    "engine_name",
    "engine_ccm",
    "power_kw",
    "fuel_type",
    "transmission",
    "avg_consumption",
    "min_consumption",
    "max_consumption",
    "sample_count",
    "tank_count",
    "low_confidence",
    "source_url",
    "scraped_at",
    "first_seen_at",
    "pct_motorway",
    "pct_city",
    "pct_country",
    "consumption_summer",
    "consumption_winter",
    "fuel_grade_pct_premium",
    "co2_g_per_km",
    "fuel_cost_eur_per_100km",
    "histogram_buckets",
]


class Storage:
    """Handles writing records to CSV and JSON files."""

    def __init__(self, config: Config, mode: str = "full"):
        self.cfg = config
        self.mode = mode
        self._timestamp = datetime.utcnow()
        self._date_str = self._timestamp.strftime("%Y%m%d")
        self._records: list[dict[str, Any]] = []
        self._errors: list[dict[str, Any]] = []
        self._existing: dict[str, dict[str, Any]] = {}

        # Load existing data for UPSERT / dedup
        self._load_existing()

    # ── public API ────────────────────────────────────────────────────

    def add_records(self, records: list[dict[str, Any]]):
        """Add validated records (UPSERT by id)."""
        now = self._timestamp.isoformat() + "Z"
        for r in records:
            rid = r["id"]
            if rid in self._existing:
                # Update — preserve first_seen_at
                r["first_seen_at"] = self._existing[rid].get("first_seen_at", now)
            else:
                r["first_seen_at"] = now
            r["scraped_at"] = now
            self._existing[rid] = r

    def add_errors(self, errors: list[dict[str, Any]]):
        """Add invalid records for the error log."""
        self._errors.extend(errors)

    def save(self):
        """Write all accumulated data to output files."""
        self._records = list(self._existing.values())
        log.info("Saving %d records to output files …", len(self._records))

        prefix = f"spritmonitor_{self.mode}_{self._date_str}"
        csv_path = self.cfg.OUTPUT_DIR / f"{prefix}.csv"
        json_path = self.cfg.OUTPUT_DIR / f"{prefix}.json"
        error_path = self.cfg.OUTPUT_DIR / f"spritmonitor_errors_{self._date_str}.log"

        self._write_csv(csv_path)
        self._write_json(json_path)
        if self._errors:
            self._write_errors(error_path)

        log.info("CSV:  %s  (%d rows)", csv_path, len(self._records))
        log.info("JSON: %s", json_path)
        if self._errors:
            log.info("Errors: %s  (%d entries)", error_path, len(self._errors))

    @property
    def record_count(self) -> int:
        return len(self._existing)

    def get_existing_ids(self) -> set[str]:
        return set(self._existing.keys())

    def get_stale_records(self, older_than_days: int) -> list[dict[str, Any]]:
        """Return records whose scraped_at is older than *older_than_days*."""
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(days=older_than_days)
        stale = []
        for r in self._existing.values():
            scraped = r.get("scraped_at", "")
            if scraped:
                try:
                    dt = datetime.fromisoformat(scraped.rstrip("Z"))
                    if dt < cutoff:
                        stale.append(r)
                except (ValueError, TypeError):
                    stale.append(r)  # Can't parse → treat as stale
            else:
                stale.append(r)
        return stale

    # ── private ───────────────────────────────────────────────────────

    def _load_existing(self):
        """Load the most recent full output file for UPSERT."""
        import glob

        pattern = str(self.cfg.OUTPUT_DIR / "spritmonitor_full_*.json")
        files = sorted(glob.glob(pattern))
        if not files:
            # Also check for update files
            pattern = str(self.cfg.OUTPUT_DIR / "spritmonitor_update_*.json")
            files = sorted(glob.glob(pattern))

        if files:
            latest = files[-1]
            log.info("Loading existing data from %s", latest)
            try:
                with open(latest, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for r in data:
                    if "id" in r:
                        self._existing[r["id"]] = r
                log.info("Loaded %d existing records.", len(self._existing))
            except (json.JSONDecodeError, IOError) as exc:
                log.warning("Could not load existing data: %s", exc)

    def _write_csv(self, path: Path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=CSV_COLUMNS, extrasaction="ignore"
            )
            writer.writeheader()
            for record in sorted(self._records, key=lambda r: r.get("id", "")):
                writer.writerow(record)

    def _write_json(self, path: Path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                sorted(self._records, key=lambda r: r.get("id", "")),
                f,
                indent=2,
                ensure_ascii=False,
                default=str,
            )

    def _write_errors(self, path: Path):
        with open(path, "w", encoding="utf-8") as f:
            for err in self._errors:
                f.write(json.dumps(err, ensure_ascii=False, default=str))
                f.write("\n")