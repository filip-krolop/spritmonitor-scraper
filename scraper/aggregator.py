"""Aggregate individual vehicle data into model-level records."""

import logging
import re
from collections import defaultdict
from typing import Any

from scraper.config import Config
from scraper.parser import extract_engine_name

log = logging.getLogger(__name__)


def aggregate_vehicles(
    vehicles: list[dict[str, Any]],
    make_id: int,
    make_name: str,
    model_id: int,
    model_name: str,
    source_url: str,
    config: Config,
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Group *vehicles* by (fuel_type, engine_name) and produce one
    aggregated record per group.
    """
    if not vehicles:
        return []

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for v in vehicles:
        fuel = _normalise_fuel(v.get("fuel_type_raw"), config)
        engine = extract_engine_name(
            v.get("title", ""), make_name, model_name
        )
        # Normalise engine name for grouping
        engine_key = _normalise_engine_key(engine)
        groups[(fuel, engine_key)].append({**v, "_fuel_norm": fuel, "_engine": engine})

    records: list[dict[str, Any]] = []
    for (fuel, engine_key), group in groups.items():
        consumptions = [
            v["consumption"] for v in group if v.get("consumption") is not None
        ]
        if not consumptions:
            continue

        # Pick the most common raw engine name in the group
        engine_name = _most_common([v["_engine"] for v in group])
        # Power — pick the most common non-None value
        power_kw = _most_common_non_none([v.get("power_kw") for v in group])
        power_ps = _most_common_non_none([v.get("power_ps") for v in group])
        if power_kw is None and power_ps is not None:
            power_kw = int(round(power_ps * 0.7355))
        engine_ccm = _most_common_non_none([v.get("engine_ccm") for v in group])
        transmission = _most_common_non_none([v.get("transmission") for v in group])

        # Year range
        years = [v["year"] for v in group if v.get("year")]
        year_from = min(years) if years else None
        year_to = max(years) if years else None
        generation_years = (
            f"{year_from}-{year_to}" if year_from and year_to else None
        )

        total_fuelings = sum(
            v.get("fuelings") or 0 for v in group
        )

        avg_c = round(sum(consumptions) / len(consumptions), 2)
        min_c = round(min(consumptions), 2)
        max_c = round(max(consumptions), 2)
        sample_count = len(consumptions)

        record_id = f"{make_id}_{model_id}_{fuel}_{_safe_id(engine_name)}"

        record: dict[str, Any] = {
            "id": record_id,
            "make_id": make_id,
            "model_id": model_id,
            "make_name": make_name,
            "model_name": model_name,
            "generation_years": generation_years,
            "year_from": year_from,
            "year_to": year_to,
            "engine_name": engine_name,
            "engine_ccm": engine_ccm,
            "power_kw": power_kw,
            "fuel_type": fuel,
            "transmission": transmission,
            "avg_consumption": avg_c,
            "min_consumption": min_c,
            "max_consumption": max_c,
            "sample_count": sample_count,
            "tank_count": total_fuelings if total_fuelings > 0 else None,
            "low_confidence": sample_count < config.LOW_CONFIDENCE_THRESHOLD,
            "source_url": source_url,
        }

        # Merge contextual data
        if context:
            for key in (
                "pct_motorway", "pct_city", "pct_country",
                "consumption_summer", "consumption_winter",
                "fuel_grade_pct_premium",
                "co2_g_per_km", "fuel_cost_eur_per_100km",
                "histogram_buckets",
            ):
                record[key] = context.get(key)
        else:
            for key in (
                "pct_motorway", "pct_city", "pct_country",
                "consumption_summer", "consumption_winter",
                "fuel_grade_pct_premium",
                "co2_g_per_km", "fuel_cost_eur_per_100km",
                "histogram_buckets",
            ):
                record[key] = None

        records.append(record)

    log.debug(
        "Aggregated %d vehicles into %d records for %s %s.",
        len(vehicles), len(records), make_name, model_name,
    )
    return records


# ── helpers ───────────────────────────────────────────────────────────


def _normalise_fuel(raw: str | None, config: Config) -> str:
    if not raw:
        return "unknown"
    key = raw.strip().lower()
    return config.FUEL_TYPE_MAP.get(key, key)


def _normalise_engine_key(engine: str) -> str:
    """Create a grouping key from an engine name."""
    # Keep only digits, dots and uppercase letters
    return re.sub(r"[^A-Za-z0-9.]", "", engine).lower()


def _safe_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", s).strip("_")[:60]


def _most_common(items: list) -> Any:
    if not items:
        return None
    from collections import Counter
    c = Counter(items)
    return c.most_common(1)[0][0]


def _most_common_non_none(items: list) -> Any:
    filtered = [x for x in items if x is not None]
    return _most_common(filtered)