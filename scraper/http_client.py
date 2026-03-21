"""HTTP client with caching, rate-limiting, and retry logic."""
import hashlib
import logging
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from scraper.config import Config

log = logging.getLogger(__name__)


class HttpClient:
    """Responsible HTTP client for Spritmonitor scraping."""

    def __init__(self, config: Config):
        self.cfg = config
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
        })
        self._last_request_time: float = 0
        self.request_count: int = 0

    # ── public API ────────────────────────────────────────────────────

    def get(self, url: str, *, bypass_cache: bool = False) -> str | None:
        """
        Fetch *url* and return the response body as a string.

        Checks local cache first (unless *bypass_cache*).
        Respects rate-limiting and retry rules.
        Returns ``None`` on unrecoverable failure.
        """
        # 1. Try cache
        if not bypass_cache:
            cached = self._read_cache(url)
            if cached is not None:
                log.debug("Cache hit: %s", url)
                return cached

        # 2. Rate-limit
        self._wait()

        # 3. Request with retries
        for attempt in range(1, self.cfg.MAX_RETRIES + 1):
            try:
                log.info(
                    "GET %s  (attempt %d/%d)", url, attempt, self.cfg.MAX_RETRIES
                )
                resp = self.session.get(url, timeout=self.cfg.REQUEST_TIMEOUT)
                self._last_request_time = time.time()
                self.request_count += 1

                if resp.status_code == 200:
                    html = resp.text
                    self._write_cache(url, html)
                    return html

                if resp.status_code == 429:
                    wait = self.cfg.RATE_LIMIT_WAIT
                    log.warning(
                        "Rate-limited (429). Waiting %d seconds …", wait
                    )
                    time.sleep(wait)
                    continue

                if resp.status_code in (403, 503):
                    log.error(
                        "Server returned %d for %s — stopping to avoid ban.",
                        resp.status_code,
                        url,
                    )
                    return None

                log.warning(
                    "HTTP %d for %s (attempt %d)", resp.status_code, url, attempt
                )

            except requests.RequestException as exc:
                log.warning("Request error: %s (attempt %d)", exc, attempt)

            # back-off between retries
            time.sleep(2 ** attempt)

        log.error("All %d attempts failed for %s", self.cfg.MAX_RETRIES, url)
        return None

    # ── rate limiting ─────────────────────────────────────────────────

    def _wait(self):                                             # CHANGED
        """
        Enforce responsible request rate.

        Base delay of REQUEST_DELAY_MIN (default 2 s) with random
        jitter of ±REQUEST_DELAY_JITTER (default ±1 s).
        Effective range with defaults: 1–3 seconds between requests.
        """
        elapsed = time.time() - self._last_request_time
        jitter = random.uniform(
            -self.cfg.REQUEST_DELAY_JITTER,
            self.cfg.REQUEST_DELAY_JITTER,
        )
        delay = self.cfg.REQUEST_DELAY_MIN + jitter
        delay = max(delay, 1.0)                                  # safety floor
        if elapsed < delay:
            sleep_for = delay - elapsed
            log.debug("Rate-limit sleep %.1fs", sleep_for)
            time.sleep(sleep_for)

    # ── caching ───────────────────────────────────────────────────────

    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha256(url.encode()).hexdigest()
        return self.cfg.CACHE_DIR / f"{h}.html"

    def _read_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        if age > timedelta(days=self.cfg.CACHE_TTL_DAYS):
            log.debug("Cache stale for %s", url)
            return None
        return path.read_text(encoding="utf-8")

    def _write_cache(self, url: str, html: str):
        path = self._cache_path(url)
        path.write_text(html, encoding="utf-8")