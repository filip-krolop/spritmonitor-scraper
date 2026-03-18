#!/usr/bin/env python3
"""
Spritmonitor.de Scraper — CLI Entry Point
==========================================

Scrapes aggregated fuel consumption data from spritmonitor.de and saves
it to local CSV + JSON files.

Usage examples:
    python main.py --mode full
    python main.py --mode update --older-than-days 30
    python main.py --mode model --make "Volkswagen"
    python main.py --mode model --make "Volkswagen" --model "Golf"
    python main.py --mode model --make-id 25
    python main.py --mode new
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime

from scraper.config import Config
from scraper.spider import SpritmonitorSpider

log = logging.getLogger()


def setup_logging(config: Config):
    """Configure logging to both file and stdout."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = config.LOGS_DIR / f"scrape_{timestamp}.log"

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)
    root.addHandler(ch)

    log.info("Log file: %s", log_file)


def main():
    parser = argparse.ArgumentParser(
        description="Spritmonitor.de fuel consumption data scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mode",
        choices=["full", "update", "model", "new"],
        required=True,
        help="Scraping mode: full | update | model | new",
    )
    parser.add_argument(
        "--make",
        help="Make name filter (for mode=model)",
    )
    parser.add_argument(
        "--model",
        help="Model name filter (for mode=model)",
    )
    parser.add_argument(
        "--make-id",
        type=int,
        help="Make ID on Spritmonitor (alternative to --make)",
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=30,
        help="Refresh records older than N days (for mode=update, default 30)",
    )
    parser.add_argument(
        "--reset-progress",
        action="store_true",
        help="Reset the progress file before starting",
    )

    args = parser.parse_args()

    # Validate mode-specific arguments
    if args.mode == "model" and not args.make and args.make_id is None:
        parser.error("--make or --make-id is required for mode=model")

    # Initialise
    config = Config()
    setup_logging(config)

    log.info("Spritmonitor.de Scraper starting …")
    log.info("Mode: %s", args.mode)
    log.info("Config: delay=%.1f–%.1fs, cache_ttl=%dd, max_pages=%d",
             config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX,
             config.CACHE_TTL_DAYS, config.MAX_PAGES_PER_MODEL)

    spider = SpritmonitorSpider(config)

    if args.reset_progress:
        spider.progress.reset()
        log.info("Progress reset.")

    # Dispatch
    try:
        if args.mode == "full":
            spider.run_full()
        elif args.mode == "update":
            spider.run_update(older_than_days=args.older_than_days)
        elif args.mode == "model":
            spider.run_model(
                make_name=args.make,
                model_name=args.model,
                make_id=args.make_id,
            )
        elif args.mode == "new":
            spider.run_new()
    except KeyboardInterrupt:
        log.warning("Interrupted by user. Progress has been saved.")
        sys.exit(1)
    except Exception:
        log.exception("Fatal error")
        sys.exit(2)


if __name__ == "__main__":
    main()