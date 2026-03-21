# Spritmonitor.de Scraper

Scrapes real-world fuel consumption data from
[spritmonitor.de](https://www.spritmonitor.de) for all available vehicle
makes, models, and engine variants. Each output record represents a
single vehicle with data taken from its detail page. Outputs CSV + JSON
files ready for import into BigQuery (or any other warehouse).

---

## Table of Contents

1. [Installation](#installation)
2. [Configuration](#configuration)
3. [Usage](#usage)
4. [How It Works](#how-it-works)
5. [Output Format](#output-format)
6. [Sample Output](#sample-output)
7. [Troubleshooting](#troubleshooting)

---

## Installation

### Prerequisites

- **Python 3.11+** (3.10 works but 3.11+ is recommended)
- **pip** or **pipenv / poetry**

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/<your-org>/spritmonitor-scraper.git
cd spritmonitor-scraper

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate    # Linux / macOS
# .venv\Scripts\activate     # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and review configuration
cp .env.example .env
# Edit .env if needed (defaults are sensible)
```

---

## Configuration

All settings live in `.env` (or environment variables). Defaults are
fine for most use cases.

| Variable | Default | Description |
| ----------------------- | ------------------------------------------------ | ----------------------------------------- |
| `USER_AGENT` | `VroomBroom-DataBot/1.0 (data@vroombroom.app)` | HTTP User-Agent header |
| `REQUEST_DELAY_MIN` | `2.0` | Base delay in seconds between requests |
| `REQUEST_DELAY_MAX` | `3.0` | Max delay (legacy, kept for reference) |
| `REQUEST_DELAY_JITTER` | `1.0` | Random jitter ±N seconds added to base |
| `RATE_LIMIT_WAIT` | `600` | Seconds to wait on HTTP 429 (10 minutes) |
| `MAX_RETRIES` | `3` | Retry count per request |
| `REQUEST_TIMEOUT` | `30` | HTTP timeout in seconds |
| `CACHE_DIR` | `./cache` | Local HTML cache directory |
| `CACHE_TTL_DAYS` | `7` | Cache validity in days |
| `OUTPUT_DIR` | `./output` | Where CSV/JSON are written |
| `LOGS_DIR` | `./logs` | Where log files are written |
| `PROGRESS_FILE` | `./progress.json` | Resumable-progress state file |
| `MAX_PAGES_PER_MODEL` | `50` | Max paginated pages per model |

### Rate limiting explained

The delay between requests is calculated as:

```
delay = REQUEST_DELAY_MIN + random.uniform(-REQUEST_DELAY_JITTER, +REQUEST_DELAY_JITTER)
delay = max(delay, 1.0)   # safety floor — never less than 1 second
```

With the defaults (`REQUEST_DELAY_MIN=2.0`, `REQUEST_DELAY_JITTER=1.0`):

- **Base wait:** 2 seconds
- **Jitter:** ±1 second
- **Effective range:** 1–3 seconds between requests
- **On HTTP 429:** full stop for 10 minutes (`RATE_LIMIT_WAIT=600`)

---

## Usage

### Mode 1 — Full import

Downloads **everything**: all makes → all models → every vehicle detail page.

```bash
python main.py --mode full
```

Expected runtime: **8–24 hours** (every vehicle detail page is visited
individually). Safe to interrupt — progress is saved to `progress.json`
and the scraper resumes from where it left off.

To restart from scratch:

```bash
python main.py --mode full --reset-progress
```

### Mode 2 — Update stale records

Re-scrapes vehicle detail pages for records whose `scraped_at` is older
than N days.

```bash
python main.py --mode update                     # default: 30 days
python main.py --mode update --older-than-days 14 # 14 days
```

### Mode 3 — Specific make / model

Scrape a single make or make+model combination.

```bash
# All Volkswagen models
python main.py --mode model --make "Volkswagen"

# Only the Golf
python main.py --mode model --make "Volkswagen" --model "Golf"

# By make ID
python main.py --mode model --make-id 50
```

### Mode 4 — New models only

Discovers and scrapes models that are not yet in the local data.

```bash
python main.py --mode new
```

---

## How It Works

```
main.py (CLI)
│
▼
SpritmonitorSpider ── orchestrates the chosen mode
│
├── HttpClient ── GET with caching, rate-limiting, retry
│   └── ./cache/ (SHA-256-hashed filenames, 7-day TTL)
│
├── Parser ── BeautifulSoup HTML parsing
│   ├── parse_makes()                → list of {make_id, make_name, …}
│   ├── parse_models_ajax()          → list of {model_id, model_name, …}
│   ├── parse_vehicles()             → list entries from model overview page
│   ├── parse_vehicle_detail()       → header + fuel sections from /detail/{id}.html
│   └── parse_vehicle_detail_expanded() → cdetail route/tire/fuel-grade data
│
├── Validator ── check ranges, required fields
│
├── Storage ── UPSERT into CSV + JSON (keyed by vehicle ID)
│   └── ./output/
│
└── ProgressTracker ── ./progress.json (resumable)
```

### Scraping flow per model

1. **Fetch model overview** page(s) (paginated list of vehicles)
2. For **each vehicle** in the list:
   a. **Follow the detail link** (`/en/detail/{vehicle_id}.html`)
   b. Parse vehicle header (year, power, fuel type, transmission)
   c. Parse `<table class="detailtable">` for consumption, CO₂, fuel cost
   d. **Follow each `td.showhide` link** (`?cdetail=N`) for expanded data
      (route breakdown, fuel grade, tyre type)
   e. Build **one record** with `id = vehicle_id` (the numeric ID from the URL)
3. Validate and store via UPSERT (no duplicates)

### Responsible scraping

| Rule | Implementation |
| ----------------------- | ----------------------------------------------------------------- |
| Base delay | 2 seconds between requests (`REQUEST_DELAY_MIN=2.0`) |
| Jitter | ±1 second random (`REQUEST_DELAY_JITTER=1.0`) |
| Effective delay | 1–3 seconds (2s base ± 1s jitter, with 1s safety floor) |
| Max concurrency | 1 (sequential — never parallel) |
| On HTTP 429 | Stop and wait 10 minutes, then retry |
| On HTTP 403 / 503 | Stop immediately — do not circumvent |
| User-Agent | Identifies as `VroomBroom-DataBot/1.0 (data@vroombroom.app)` |
| No UA rotation | Single honest User-Agent — never rotated |
| Caching | HTML cached locally for 7 days — avoids repeat requests |

---

## Output Format

After a run, the `./output/` directory contains:

```
output/
├── spritmonitor_full_20250101.csv
├── spritmonitor_full_20250101.json
├── spritmonitor_update_20250108.csv
├── spritmonitor_update_20250108.json
└── spritmonitor_errors_20250101.log
```

### Record identity

Each row represents **one vehicle**. The `id` column is the numeric
vehicle ID extracted from the Spritmonitor detail URL. For example,
vehicle `https://www.spritmonitor.de/en/detail/1583120.html` has
`id = "1583120"`. This guarantees uniqueness — no duplicate rows.

### CSV columns

| Column | Type | Required | Description |
| --------------------------- | ------- | -------- | ---------------------------------------------- |
| `id` | STRING | ✓ | Vehicle detail page ID (e.g. `"1583120"`) |
| `make_id` | INT | ✓ | Spritmonitor make ID |
| `model_id` | INT | ✓ | Spritmonitor model ID |
| `make_name` | STRING | ✓ | e.g. "Volkswagen" |
| `model_name` | STRING | ✓ | e.g. "Golf" |
| `generation_years` | STRING | | e.g. "2024-2024" |
| `year_from` | INT | | Model year from detail page |
| `year_to` | INT | | Same as year_from (single vehicle) |
| `engine_name` | STRING | ✓ | Variant name, e.g. "GTE" or "7R" |
| `engine_ccm` | INT | | Displacement in ccm |
| `power_kw` | INT | | Power in kW |
| `fuel_type` | STRING | ✓ | petrol / diesel / lpg / cng / electric / hybrid |
| `transmission` | STRING | | manual / automatic |
| `avg_consumption` | FLOAT | ✓ | l/100 km or kWh/100 km |
| `min_consumption` | FLOAT | | Same as avg (single vehicle) |
| `max_consumption` | FLOAT | | Same as avg (single vehicle) |
| `sample_count` | INT | ✓ | Always 1 (one vehicle per row) |
| `tank_count` | INT | | Number of fuel-up records |
| `low_confidence` | BOOL | ✓ | Always `True` (single vehicle) |
| `source_url` | STRING | ✓ | Detail page URL |
| `scraped_at` | STRING | ✓ | ISO 8601 UTC timestamp |
| `first_seen_at` | STRING | ✓ | When record was first created |
| `pct_motorway` | FLOAT | | % motorway driving |
| `pct_city` | FLOAT | | % city driving |
| `pct_country` | FLOAT | | % country road driving |
| `consumption_summer` | FLOAT | | Apr–Sep average l/100 km |
| `consumption_winter` | FLOAT | | Oct–Mar average l/100 km |
| `fuel_grade_pct_premium` | FLOAT | | % users using premium fuel |
| `co2_g_per_km` | FLOAT | | CO₂ g/km from detail page |
| `fuel_cost_eur_per_100km` | FLOAT | | Fuel cost EUR/100 km from detail page |
| `histogram_buckets` | STRING | | JSON distribution (if available) |

Encoding: **UTF-8**. Delimiter: **comma**. Decimal separator: **dot**.

---

## Sample Output

*(Illustrative — actual values depend on live Spritmonitor data.)*

### First 10 rows

| id | make_name | model_name | engine_name | fuel_type | avg_consumption | power_kw | year | co2_g_per_km | source_url |
| ------- | ---------- | ---------- | ------------------ | --------- | --------------- | -------- | ---- | ------------ | ---------------------------------------------------- |
| 892020 | Volkswagen | Golf | 7R | petrol | 0.24 | 221 | | | https://www.spritmonitor.de/en/detail/892020.html |
| 818245 | Volkswagen | Golf | GTI | petrol | 0.25 | 250 | | | https://www.spritmonitor.de/en/detail/818245.html |
| 1583120 | Volkswagen | Golf | GTE | hybrid | 0.37 | 200 | 2024 | 9.0 | https://www.spritmonitor.de/en/detail/1583120.html |
| 1193973 | Volkswagen | Golf | GTE | hybrid | 0.62 | 149 | | | https://www.spritmonitor.de/en/detail/1193973.html |
| 1274496 | Volkswagen | Golf | GTE | hybrid | 0.63 | 110 | | | https://www.spritmonitor.de/en/detail/1274496.html |
| 1221699 | Volkswagen | Golf | Golf 7 R Facalift | petrol | 0.80 | 228 | | | https://www.spritmonitor.de/en/detail/1221699.html |
| 1622618 | Volkswagen | Golf | 8 GTE | hybrid | 0.83 | 180 | | | https://www.spritmonitor.de/en/detail/1622618.html |
| 1614893 | Volkswagen | Golf | GTE 2025 | hybrid | 0.86 | 200 | | | https://www.spritmonitor.de/en/detail/1614893.html |
| 1191550 | Volkswagen | Golf | egolf | electric | 0.88 | 74 | | | https://www.spritmonitor.de/en/detail/1191550.html |
| 1213240 | Volkswagen | Golf | Style ehybrid | hybrid | 0.89 | 150 | | | https://www.spritmonitor.de/en/detail/1213240.html |

### Summary statistics (single make+model run)

```
Total records:         ~150 per model (one per vehicle)
Number of makes:       ~361 available on site
Number of models:      ~51 per major make (e.g. Volkswagen)
Each record:           one vehicle, low_confidence = True
```

### After a full import

```
Total records:         50,000+ (one per vehicle across all makes/models)
Number of makes:       ~361
Number of models:      ~5,000+
```

---

## Data pipeline

The scraper outputs local files. To load into BigQuery:

1. Run the scraper to produce `output/spritmonitor_full_*.csv`
2. Upload to BigQuery using `bq load` or the BigQuery console
3. The `id` column (vehicle detail page ID) serves as the primary key
4. For updates, use UPSERT logic on the `id` column — `first_seen_at`
   is preserved across runs automatically in the local JSON

---

## Troubleshooting

### Scraper finds 0 makes

The Spritmonitor HTML structure may have changed. Steps:

1. Open `https://www.spritmonitor.de/en/` in a browser
2. Inspect the page (F12 → Elements)
3. Look for how makes are listed (links, `<select>`, JavaScript)
4. Update selectors in `scraper/parser.py → parse_makes()`

### HTTP 403 / 503 errors

The site may be blocking automated access. The scraper will **stop**
automatically. Do not circumvent this — contact the site operator.

### HTTP 429 (rate limit)

The scraper stops and waits 10 minutes automatically before retrying. If this
happens frequently, increase `REQUEST_DELAY_MIN` in `.env` (e.g. from `2.0` to
`3.0`). You can also reduce the jitter range by lowering `REQUEST_DELAY_JITTER`
(e.g. from `1.0` to `0.5` for a tighter 2.5–3.5s window).

### Scraper interrupted / crashed

Progress is saved automatically. Just re-run the same command — it will
resume from where it stopped:

```bash
python main.py --mode full    # continues from progress.json
```

### Scraper is slow

This is by design. Each vehicle requires 2–4 HTTP requests (detail page
\+ cdetail expansions), and we wait 2 seconds (±1s jitter) between
requests to be respectful to the community-run Spritmonitor site. For a
model with 150 vehicles across 10 list pages, expect ~10–15 minutes per
model.

To speed up testing, use `--mode model` with a specific model:

```bash
python main.py --mode model --make "Volkswagen" --model "Golf"
```

### Empty consumption values

Some vehicle detail pages may not have consumption data (e.g., only a
first fueling entry). These vehicles are skipped with a debug log
message.

### Cache is stale / corrupted

```bash
rm -rf ./cache/*
```

### Output has duplicates

This should not happen. Each record's `id` is the numeric vehicle ID
from the detail page URL (e.g., `1583120`). The Storage class uses
UPSERT logic keyed on `id`. If duplicates appear, check the JSON output
— the vehicle ID must appear only once.

### Vehicle detail pages use JavaScript

The current scraper uses `requests` (no JavaScript execution). The
Spritmonitor detail pages serve data in server-rendered HTML, so this
works. If the site adds client-side rendering, you would need
Playwright or Selenium.

Verify with:

```bash
curl -s "https://www.spritmonitor.de/en/detail/1583120.html" | grep "detailtable"
```

---

## Project Structure

```
spritmonitor-scraper/
├── main.py                  # CLI entry point
├── requirements.txt         # Python dependencies
├── .env.example             # Configuration template
├── README.md                # This file
├── scraper/
│   ├── __init__.py
│   ├── config.py            # Central configuration
│   ├── http_client.py       # HTTP with cache, rate-limit (2s ± 1s jitter), retry
│   ├── parser.py            # HTML parsers (makes, models, vehicles,
│   │                        #   detail pages, cdetail expansions)
│   ├── spider.py            # Scraping orchestration (4 modes)
│   ├── aggregator.py        # (Legacy — not used in current flow)
│   ├── validator.py         # Record validation
│   ├── storage.py           # CSV + JSON output with UPSERT
│   └── progress.py          # Resumable progress tracking
├── output/                  # Generated CSV + JSON files
├── cache/                   # Cached HTML responses
└── logs/                    # Run logs
```

---

## Quick Start

```bash
# Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Test with a single make+model first
python main.py --mode model --make "Volkswagen" --model "Golf"

# Check output
ls -la output/
head output/spritmonitor_model_*.csv

# Count records
wc -l output/spritmonitor_model_*.csv

# Full import (run overnight — will take many hours)
python main.py --mode full
```