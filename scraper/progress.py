"""Track scraping progress for resumable runs."""

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class ProgressTracker:
    """
    Persist progress to a JSON file so scraping can resume after
    interruption.
    """

    def __init__(self, path: Path):
        self.path = path
        self._state: dict[str, Any] = {
            "completed_makes": [],
            "completed_models": [],  # list of "{make_id}_{model_id}"
            "current_make": None,
            "current_model": None,
            "total_makes": 0,
            "total_models": 0,
            "total_records": 0,
            "errors": 0,
        }
        self._load()

    # ── persistence ───────────────────────────────────────────────────

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._state.update(saved)
                log.info(
                    "Resumed progress: %d makes, %d models completed.",
                    len(self._state["completed_makes"]),
                    len(self._state["completed_models"]),
                )
            except (json.JSONDecodeError, IOError) as exc:
                log.warning("Could not load progress file: %s", exc)

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2, ensure_ascii=False)

    # ── queries ───────────────────────────────────────────────────────

    def is_make_done(self, make_id: int) -> bool:
        return make_id in self._state["completed_makes"]

    def is_model_done(self, make_id: int, model_id: int) -> bool:
        key = f"{make_id}_{model_id}"
        return key in self._state["completed_models"]

    # ── updates ───────────────────────────────────────────────────────

    def mark_make_done(self, make_id: int):
        if make_id not in self._state["completed_makes"]:
            self._state["completed_makes"].append(make_id)
        self.save()

    def mark_model_done(self, make_id: int, model_id: int):
        key = f"{make_id}_{model_id}"
        if key not in self._state["completed_models"]:
            self._state["completed_models"].append(key)
        self._state["total_models"] = len(self._state["completed_models"])
        self.save()

    def set_current(self, make_name: str = None, model_name: str = None):
        self._state["current_make"] = make_name
        self._state["current_model"] = model_name

    def set_total_makes(self, n: int):
        self._state["total_makes"] = n

    def increment_records(self, n: int = 1):
        self._state["total_records"] = self._state.get("total_records", 0) + n

    def increment_errors(self, n: int = 1):
        self._state["errors"] = self._state.get("errors", 0) + n

    def reset(self):
        """Reset all progress (for a fresh full run)."""
        self._state = {
            "completed_makes": [],
            "completed_models": [],
            "current_make": None,
            "current_model": None,
            "total_makes": 0,
            "total_models": 0,
            "total_records": 0,
            "errors": 0,
        }
        self.save()

    @property
    def stats(self) -> dict:
        return dict(self._state)