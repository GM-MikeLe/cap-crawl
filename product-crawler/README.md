# Product crawler

Three modes:

1. **Single URL** (`extract_one_product.py`) — test one product page (no sitemap).
2. **Full crawl** (`crawl_products.py`, `crawl_products_headed.py`) — parse sitemap(s), visit each URL, extract products + country, write JSON (and optionally CSV).
3. **Stealth crawl** (`crawl_products_stealth.py`) — minimal setup: playwright-stealth only, headless, low delay. Two-phase: crawl product info (no country per URL) → batch extract country → JSON + CSV. Skips products without details.

### Module layout

Entry points and core logic live at the project root; shared helpers live in `utils/`:

| Module | Purpose |
|--------|---------|
| `config.py` | Timeouts, selectors (content-ready, etc.). |
| `product_extract.py` | Extract product fields from HTML; country via pycountry + spaCy. |
| **`utils/`** | Shared utilities (use `from utils import ...`): |
| → `utils/sitemap_utils.py` | `parse_sitemap()`, `is_product_url()` — sitemap XML and URL filtering. |
| → `utils/cookie_utils.py` | `load_cookies()` — load and normalize cookies for Playwright. |
| → `utils/export_utils.py` | `write_products_csv()` — export products to CSV. |
| → `utils/quantity_utils.py` | `parse_quantity_from_text()` — parse quantity value + unit (g, kg, ml, L, pcs) from text. |
| `crawl_products.py` | Crawl orchestration (headless). |
| `crawl_products_headed.py` | Same as above with `--headed` (visible browser). |
| `crawl_products_stealth.py` | Stealth-only, two-phase (crawl → country), minimal delay. No cookies/saved session. |

## Setup (run in WSL)

Use a **virtual environment** (required on Debian/Ubuntu; system Python is externally managed):

```bash
cd /home/dmin/workspace/mike/cap-crawl/product-crawler
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python -m spacy download en_core_web_sm
```

To run later, activate the venv first: `source .venv/bin/activate`, then `python extract_one_product.py`.

If `python3 -m venv .venv` fails, install: `sudo apt install python3-venv` (or `python3-full`).

## Run

### Single URL (test one page)

```bash
cd /home/dmin/workspace/mike/cap-crawl/product-crawler
source .venv/bin/activate

# Default test URL
python extract_one_product.py

# Or pass a product URL
python extract_one_product.py "https://shop.annam-gourmet.com/hcm-est/bu-i-da-xanh-b-n-tre-lo-i-1-1000g.html"
```

Output: JSON to stdout and `product_sample.json`.

### Full crawl (sitemap → products.json, optional CSV)

```bash
source .venv/bin/activate

# Default: saigoncenter_en_sitemap.xml in same dir, 5s delay, output products.json
python crawl_products.py

# Also write CSV (e.g. for spreadsheets)
python crawl_products.py --csv products.csv --limit 50 saigoncenter_en_sitemap.xml

# Safer: higher delay and limit (e.g. first 50 products)
python crawl_products.py --delay 8 --limit 50 saigoncenter_en_sitemap.xml

# Parallel: 3 workers (faster; use with higher --delay to reduce block risk)
python crawl_products.py --workers 3 --delay 10 --limit 100 saigoncenter_en_sitemap.xml

# Batching: first run gets first 50 products; second run skips first 50 URLs and gets next 50 products
python crawl_products.py --delay 8 --limit 50 saigoncenter_en_sitemap.xml --out batch1.json
python crawl_products.py --delay 8 --skip 50 --limit 50 saigoncenter_en_sitemap.xml --out batch2.json

# Multiple sitemaps, custom output path
python crawl_products.py --out my_products.json sitemap1.xml sitemap2.xml

# Headed browser (visible window; often helps if the site blocks headless)
python crawl_products_headed.py --headed --delay 8 --limit 20 saigoncenter_en_sitemap.xml
```

Options: `--workers N` (default 1), `--delay SECS` (default 5), `--limit N` (max products), `--skip N` (skip first N URLs for batching), `--out FILE` (default `products.json`), `--csv FILE` (optional: also export products to CSV). Output JSON has `source_sitemaps`, `crawled_at`, `total_products`, and `products` (array). Note: `--skip` is by URL index in the sitemap, not by product count.

### Stealth crawl (minimal, two-phase)

Uses playwright-stealth only (no cookies, no saved session, headless). Phase 1: crawl product info without country; Phase 2: batch extract country from JSON. Keeps only products with details (description).

```bash
python crawl_products_stealth.py --limit 50 saigoncenter_en_sitemap.xml --out products.json --csv products.csv

# Batching with skip
python crawl_products_stealth.py --skip 500 --limit 100 --out batch2.json --csv batch2.csv saigoncenter_en_sitemap.xml

# Parallel + custom delay
python crawl_products_stealth.py --workers 3 --delay 0.5 --limit 100 saigoncenter_en_sitemap.xml
```

Options: `--workers` (default 1), `--delay` (default 0.5), `--limit`, `--skip`, `--out` (default `products_stealth.json`), `--csv`.

**Experimental:** `crawl_products_experimental.py` — warmup URL, restart-every, storage-state/solve-once (latter commented out). Stealth + headless; for testing.

### Reducing Cloudflare / IP blocking risk

No method is 100% safe; these reduce the chance of being blocked:

| Approach | What to do |
|----------|------------|
| **Slower, random timing** | Use `--delay 8` or `--delay 10`. The script already adds random extra wait (delay to delay×1.5) so requests aren’t at fixed intervals. |
| **Small batches** | Use `--limit 100` or `--limit 200`, run once per day, merge the output JSONs later. |
| **Off-peak** | Run at night or low-traffic times; spread a large sitemap over several days. |
| **Use installed Chrome** | Run with `--use-chrome` so Playwright uses your installed Chrome instead of its Chromium. That often looks less like automation and can get past some checks. (Requires Chrome installed; on Linux you may need `playwright install chrome` or install Chrome yourself.) |
| **Proxy** | Use `--proxy http://host:port` (or your provider’s URL with user:pass if they support it in the URL). **Residential proxies** (e.g. Bright Data, Oxylabs, Smartproxy) rotate IPs so traffic comes from many addresses; they’re the strongest option but cost money. **Datacenter proxies** are cheaper but more likely to be flagged. |
| **Workers** | Keep `--workers 1` (default) to minimize block risk. If you use `--workers 2` or `3`, use a higher `--delay` (e.g. 10) so total request rate stays low. |
| **If blocked** | Stop the crawl, wait several hours (or switch network/VPN), then resume with a higher `--delay` and `--limit`. |

**Examples:**

```bash
# Safer: Chrome + 10s delay + first 50 products
python crawl_products.py --use-chrome --delay 10 --limit 50

# With a proxy (e.g. from a proxy provider)
python crawl_products.py --proxy http://user:pass@proxy.example.com:8080 --delay 8 --limit 100
```

### Using cookies to get past Cloudflare

If the site shows a “Verify you are human” / Cloudflare challenge in the crawler but loads normally in your browser, you can reuse your browser’s cookies so the crawler shares the same “passed” session:

1. In Chrome (or another browser), open the shop (e.g. `https://shop.annam-gourmet.com`), complete the challenge, then browse to any product page so the session is set.
2. Export cookies to a JSON file. Each cookie must have at least `name`, `value`, and `domain` (e.g. `".shop.annam-gourmet.com"`). You can use an extension like “EditThisCookie” or “Get cookies.txt” and convert to JSON, or from DevTools → Application → Cookies → copy the list into a file in this shape:

```json
[
  { "name": "cf_clearance", "value": "…", "domain": ".shop.annam-gourmet.com", "path": "/" }
]
```

3. Run the crawler with `--cookies` pointing to that file:

```bash
python crawl_products.py --cookies cookies.json --delay 8 --limit 50 saigoncenter_en_sitemap.xml --out batch1.json
```

The script loads the cookies into the browser context before visiting product URLs. Sessions expire (often after some hours or after many requests), so if you start seeing challenges again, export a fresh cookie file and re-run.

## Schema (extracted fields)

- `name`, `price`, `currency`, `description` (Details tab, fallback meta)
- `ingredients`, `instruction_for_use`, `storage_instructions` (optional)
- `country` (pycountry + spaCy NER on name/description/ingredients; `""` if none or if model not available)
- `quantity_value`, `quantity_unit` (parsed from name: g, kg, ml, L, pcs; first match; empty if none)

See `PRODUCT_CRAWL_PLAN.md` for the full plan, file layout, and selectors.
