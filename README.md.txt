# Spritmonitor.de Scraper

Scrapes aggregated real-world fuel consumption data from
[spritmonitor.de](https://www.spritmonitor.de) for all available vehicle
makes, models, and engine variants. Outputs CSV + JSON files ready for
import into BigQuery (or any other warehouse).

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
source .venv/bin/activate   # Linux / macOS
# .venv\Scripts\activate    # Windows

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

| Variable             | Default                                          | Description                            |
| -------------------- | ------------------------------------------------ | -------------------------------------- |
| `USER_AGENT`         | `VroomBroom-DataBot/1.0 (data@vroombroom.app)`   | HTTP User-Agent header                 |
| `REQUEST_DELAY_MIN`  | `1.0`                                            | Min seconds between requests           |
| `REQUEST_DELAY_MAX`  | `3.0`                                            | Max seconds between requests           |
| `RATE_LIMIT_WAIT`    | `600`                                            | Seconds to wait on HTTP 429            |
| `MAX_RETRIES`        | `3`                                              | Retry count per request                |
| `REQUEST_TIMEOUT`    | `30`                                             | HTTP timeout in seconds                |
| `CACHE_DIR`          | `./cache`                                        | Local HTML cache directory             |
| `CACHE_TTL_DAYS`     | `7`                                              | Cache validity in days                 |
| `OUTPUT_DIR`         | `./output`                                       | Where CSV/JSON are written             |
| `LOGS_DIR`           | `./logs`                                         | Where log files are written            |
| `PROGRESS_FILE`      | `./progress.json`                                | Resumable-progress state file          |
| `MAX_PAGES_PER_MODEL`| `50`                                             | Max paginated pages per model          |

---

## Usage

### Mode 1 — Full import

Downloads **everything**: all makes → all models → all engine variants.

```bash
python main.py --mode full
```

Expected runtime: **4–12 hours** (depending on site load and number of
models). Safe to interrupt — progress is saved to `progress.json` and
the scraper resumes from where it left off.

To restart from scratch:

```bash
python main.py --mode full --reset-progress
```

### Mode 2 — Update stale records

Re-scrapes records whose `scraped_at` is older than N days.

```bash
python main.py --mode update                        # default: 30 days
python main.py --mode update --older-than-days 14   # 14 days
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
SpritmonitorSpider  ── orchestrates the chosen mode
  │
  ├── HttpClient    ── GET with caching, rate-limiting, retry
  │     └── ./cache/   (SHA-256-hashed filenames, 7-day TTL)
  │
  ├── Parser        ── BeautifulSoup HTML parsing
  │     ├── parse_makes()      → list of {make_id, make_name, …}
  │     ├── parse_models()     → list of {model_id, model_name, …}
  │     ├── parse_vehicles()   → list of raw vehicle dicts
  │     └── parse_model_context() → route profile, CO₂, …
  │
  ├── Aggregator    ── group vehicles by engine+fuel → avg/min/max
  │
  ├── Validator     ── check ranges, required fields
  │
  ├── Storage       ── UPSERT into CSV + JSON
  │     └── ./output/
  │
  └── ProgressTracker ── ./progress.json (resumable)
```

### Responsible scraping

| Rule                    | Implementation                                          |
| ----------------------- | ------------------------------------------------------- |
| Delay between requests  | 1–3 s random jitter                                     |
| Max concurrency         | 1 (sequential)                                          |
| On HTTP 429             | Wait 10 minutes, then retry                             |
| On HTTP 403 / 503       | Stop immediately                                        |
| User-Agent              | Identifies as `VroomBroom-DataBot/1.0`                  |
| Caching                 | HTML cached locally for 7 days                          |
| No UA rotation          | Single honest User-Agent                                |

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

### CSV columns

| Column                      | Type    | Required | Description                            |
| --------------------------- | ------- | -------- | -------------------------------------- |
| `id`                        | STRING  | ✓        | Unique key `{make_id}_{model_id}_{fuel}_{engine}` |
| `make_id`                   | INT     | ✓        | Spritmonitor make ID                   |
| `model_id`                  | INT     | ✓        | Spritmonitor model ID                  |
| `make_name`                 | STRING  | ✓        | e.g. "Volkswagen"                      |
| `model_name`                | STRING  | ✓        | e.g. "Golf"                            |
| `generation_years`          | STRING  |          | e.g. "2013-2020"                       |
| `year_from`                 | INT     |          | First model year in sample             |
| `year_to`                   | INT     |          | Last model year in sample              |
| `engine_name`               | STRING  | ✓        | e.g. "1.6 TDI"                         |
| `engine_ccm`                | INT     |          | Displacement in ccm                    |
| `power_kw`                  | INT     |          | Power in kW                            |
| `fuel_type`                 | STRING  | ✓        | petrol / diesel / lpg / cng / electric / hybrid |
| `transmission`              | STRING  |          | manual / automatic                     |
| `avg_consumption`           | FLOAT   | ✓        | l/100 km or kWh/100 km                 |
| `min_consumption`           | FLOAT   |          | Lowest recorded value                  |
| `max_consumption`           | FLOAT   |          | Highest recorded value                 |
| `sample_count`              | INT     | ✓        | Number of vehicles in aggregate        |
| `tank_count`                | INT     |          | Total fuel-up records                  |
| `low_confidence`            | BOOL    | ✓        | `True` if sample_count < 5             |
| `source_url`                | STRING  | ✓        | URL scraped                            |
| `scraped_at`                | STRING  | ✓        | ISO 8601 UTC timestamp                 |
| `first_seen_at`             | STRING  | ✓        | When record was first created          |
| `pct_motorway`              | FLOAT   |          | % motorway driving                     |
| `pct_city`                  | FLOAT   |          | % city driving                         |
| `pct_country`               | FLOAT   |          | % country road driving                 |
| `consumption_summer`        | FLOAT   |          | Apr–Sep average l/100 km               |
| `consumption_winter`        | FLOAT   |          | Oct–Mar average l/100 km               |
| `fuel_grade_pct_premium`    | FLOAT   |          | % users using premium fuel             |
| `co2_g_per_km`              | FLOAT   |          | Average CO₂ g/km                       |
| `fuel_cost_eur_per_100km`   | FLOAT   |          | Fuel cost EUR/100 km                   |
| `histogram_buckets`         | STRING  |          | JSON array of consumption distribution |

Encoding: **UTF-8**. Delimiter: **comma**. Decimal separator: **dot**.

---

## Sample Output

*(Illustrative — actual values depend on live Spritmonitor data.)*

### First 10 rows

| id | make_name | model_name | engine_name | fuel_type | avg_consumption | sample_count | low_confidence |
|----|-----------|------------|-------------|-----------|-----------------|--------------|----------------|
| 50_452_diesel_16_TDI | Volkswagen | Golf | 1.6 TDI | diesel | 5.21 | 1243 | False |
| 50_452_petrol_15_TSI | Volkswagen | Golf | 1.5 TSI | petrol | 6.74 | 567 | False |
| 50_452_petrol_20_GTI | Volkswagen | Golf | 2.0 GTI | petrol | 8.92 | 312 | False |
| 47_387_diesel_20_TDI | Skoda | Octavia | 2.0 TDI | diesel | 5.45 | 987 | False |
| 47_387_petrol_14_TSI | Skoda | Octavia | 1.4 TSI | petrol | 7.12 | 432 | False |
| 6_120_diesel_20d | BMW | 3 Series | 2.0d | diesel | 5.89 | 876 | False |
| 6_120_petrol_20i | BMW | 3 Series | 2.0i | petrol | 8.23 | 654 | False |
| 36_280_diesel_220d | Mercedes | C-Class | 220d | diesel | 5.67 | 543 | False |
| 4_89_diesel_20_TDI | Audi | A4 | 2.0 TDI | diesel | 5.78 | 765 | False |
| 49_430_petrol_12_PureTech | Peugeot | 208 | 1.2 PureTech | petrol | 5.95 | 234 | False |

### Summary statistics

```
Total records:          ~8,000+
Number of makes:        ~80+
Number of models:       ~1,500+
Low-confidence records: ~15-25%
```

---

## Troubleshooting

### Scraper finds 0 makes

The Spritmonitor HTML structure may have changed. Steps:

1. Open `https://www.spritmonitor.de/en/overview.html` in a browser
2. Inspect the page (F12 → Elements)
3. Look for how makes are listed (links, `<select>`, JavaScript)
4. Update selectors in `scraper/parser.py → parse_makes()`

### HTTP 403 / 503 errors

The site may be blocking automated access. The scraper will **stop**
automatically. Do not circumvent this — contact the site operator.

### HTTP 429 (rate limit)

The scraper waits 10 minutes automatically and retries. If this happens
frequently, increase `REQUEST_DELAY_MIN` and `REQUEST_DELAY_MAX` in
`.env`.

### Scraper interrupted / crashed

Progress is saved automatically. Just re-run the same command — it will
resume from where it stopped:

```bash
python main.py --mode full    # continues from progress.json
```

### Empty consumption values

Some model pages may use JavaScript to render data (SPAs). If this is
the case, you would need a headless browser (Playwright/Selenium).
Check by comparing `curl` output with browser output:

```bash
curl -s "https://www.spritmonitor.de/en/overview/50-Volkswagen/452-Golf.html" | head -100
```

### Cache is stale / corrupted

```bash
rm -rf ./cache/*
```

### Output has duplicates

This should not happen due to UPSERT logic (keyed by `id`). If it does,
check for records with different `id` values that represent the same
vehicle variant — the engine name normalisation in
`scraper/parser.py → extract_engine_name()` may need tuning.