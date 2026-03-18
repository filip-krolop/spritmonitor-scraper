"""Validate aggregated records before saving."""

import logging
from typing import Any

from scraper.config import Config

log = logging.getLogger(__name__)


def validate_record(record: dict[str, Any], config: Config) -> tuple[bool, str]:
    """
    Validate a single record.

    Returns ``(is_valid, reason)`` where *reason* is empty on success.
    """
    # Required fields
    for field in ("make_name", "model_name", "engine_name", "fuel_type", "avg_consumption", "sample_count"):
        if not record.get(field):
            return False, f"Missing required field: {field}"

    # avg_consumption range
    avg = record["avg_consumption"]
    if not isinstance(avg, (int, float)):
        return False, f"avg_consumption is not a number: {avg}"
    if avg < config.MIN_CONSUMPTION or avg > config.MAX_CONSUMPTION:
        return False, f"avg_consumption out of range: {avg}"

    # sample_count
    sc = record["sample_count"]
    if not isinstance(sc, int) or sc < 1:
        return False, f"sample_count invalid: {sc}"

    # make_name / model_name not empty
    if not record["make_name"].strip():
        return False, "make_name is empty"
    if not record["model_name"].strip():
        return False, "model_name is empty"

    return True, ""


def validate_records(
    records: list[dict[str, Any]], config: Config
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Validate a list of records.

    Returns ``(valid_records, invalid_records)``.
    """
    valid = []
    invalid = []
    for r in records:
        ok, reason = validate_record(r, config)
        if ok:
            valid.append(r)
        else:
            log.warning("Invalid record %s: %s", r.get("id", "?"), reason)
            invalid.append({**r, "_rejection_reason": reason})
    return valid, invalid