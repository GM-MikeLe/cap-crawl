# Product metadata crawl plan

Plan for parsing sitemap URLs, visiting each page, extracting **product-only** metadata from HTML, and exporting to JSON.

**Implementation status:** Implemented. Entry points: `crawl_products.py`, `crawl_products_headed.py`, `extract_one_product.py`. Core: `product_extract.py`, `config.py`. Shared utilities: `utils/` (sitemap, cookies, CSV export). See README for module layout.

---

## 1. Product schema (output shape)

Each product in the final JSON is one object. Fields:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Product title/name |
| `price` | string or number | Price (e.g. "65.000₫") or numeric |
| `currency` | string | e.g. "VND", "USD" |
| `description` | string | Product description (from **Details** tab) |
| `ingredients` | string | Ingredients list (optional) |
| `instruction_for_use` | string | Instructions for use (optional) |
| `storage_instructions` | string | Storage instructions (optional) |
| `country` | string | Country/place of origin (pycountry + spaCy NER on name/description/ingredients; empty string if not found) |

---

## 2. Step-by-step plan

### Step 1: Parse sitemap XML

- **Input:** One or more sitemap XML files (e.g. `saigoncenter_en_sitemap.xml`).
- **Action:** Parse XML and collect all `<loc>` URLs into a single list. Dedupe. Pre-filter: keep only URLs with one path segment after `/hcm-taka/` and exclude known category slugs (e.g. `sweet-grocery`, `beverages`) so many category pages are skipped before any fetch.
- **Output:** List of URLs (strings).
- **Implemented:** `parse_sitemap()` and `is_product_url()` in `utils/sitemap_utils.py`; crawlers import via `from utils import parse_sitemap, is_product_url`.
- **Note:** Sitemap index (nested child sitemaps) is not resolved; use a single-level sitemap or pre-merged URL list.

---

**Chosen approach:** After fetching each URL, read `meta[property="og:type"]`; if `content="product"`, treat as product page and run extraction; otherwise skip.

### Step 2 & 3: Fetch each URL, check type, extract if product

- **Input:** List of URLs from Step 1 (with optional `--skip` / `--limit` for batching).
- **Action:** For each URL:
  - Open in browser (Playwright; optional `--use-chrome`, `--proxy`, `--cookies` for Cloudflare/blocking).
  - Wait for content (`CONTENT_READY_SELECTOR` in `config.py`: `meta[property='og:type'], .product.data.items, h1.page-title`), then read `meta[property="og:type"]` content. If `"product"`, run extraction and add `country`; otherwise skip.
  - Timeouts: `PAGE_LOAD_TIMEOUT_MS`, `SELECTOR_WAIT_TIMEOUT_MS`, `PER_URL_TIMEOUT_SEC` in `config.py`.
- **Rate limiting:** `--delay SECS` with random 0.3–1.0× multiplier between requests; `--workers N` for concurrency (default 1).
- **Implemented:** `_load_page_and_get_og_type()`, `_crawl_async()` in `crawl_products.py`.

---

### Step 4: Extract product metadata from HTML

- **Input:** Loaded HTML/DOM for one product URL.
- **Action:** For each field in the product schema, extract using the selectors in Section 3 (meta tags + body tabs).
- **Output:** One object per product; missing fields `null` or omitted.
- **Implemented:** `extract_product()` / `extract_product_async()` in `product_extract.py`.

---

### Step 5: Normalize (optional)

- **Action:** Price normalized via `normalize_price()` (numeric when possible); text trimmed. No formal validation step.
- **Implemented:** In `product_extract.py`; optional validation not added.

---

### Step 6: Collect results and export to JSON

- **Input:** List of product objects (each already includes `country` from Step 2/3).
- **Action:** Write one JSON file with `source_sitemaps`, `crawled_at`, `total_products`, `products`.
- **Output:** e.g. `products.json` (or `--out FILE`). Products already have `country` (computed inline per product).
- **Implemented:** In `crawl_products.py` at end of `_crawl_async()`.

---

### Step 7: Country extraction (inline, not post-crawl)

- **Input:** Product dict (name, description, ingredients) right after extraction.
- **Action:** `extract_country(product)` in `product_extract.py`: concatenate name + description + ingredients; try pycountry (country/subdivision name match) first, then spaCy NER (GPE, LOC). Accent normalization; English stopwords filtered. Set `product["country"]` to result or `""`.
- **Output:** Same product object with `country` field before appending to results.
- **Implemented:** Called immediately after `extract_product_async()` in `crawl_products.py`; no separate post-crawl JSON pass.

---

### Step 8: Export to CSV (optional)

- **Input:** Same product list as Step 6 (after JSON is written).
- **Action:** If `--csv FILE` is given, write a CSV file with one row per product. Columns (in order): `name`, `price`, `currency`, `description`, `ingredients`, `instruction_for_use`, `storage_instructions`, `country`. UTF-8 encoding; missing/optional fields written as empty; values quoted as needed for commas/newlines.
- **Output:** e.g. `products.csv` for use in spreadsheets or other tools.
- **Implemented:** `write_products_csv()` in `utils/export_utils.py`; both crawlers call it when `--csv FILE` is passed (import: `from utils import write_products_csv`).

---

## 3. Extraction rules

**Source:** `<head>` meta tags and body (product tabs). For meta tags, get the **`content`** attribute value. Implemented in `product_extract.py`; timeouts/selectors in `config.py`.

### Page-type check (product vs not)

- **Selector:** `meta[property="og:type"]`
- **Get:** attribute `content`. If value is `"product"`, treat as product page and extract; otherwise skip.

### Fields from meta tags (head)

| Schema field | Selector | Get value |
|--------------|----------|-----------|
| `name` | `meta[property="og:title"]` | attribute `content` |
| `description` | `meta[property="og:description"]` | attribute `content` (fallback if body Details missing) |
| `price` (numeric) | `meta[property="product:price:amount"]` | attribute `content` |
| `currency` | `meta[property="product:price:currency"]` | attribute `content` |

### Fields from body (product tabs)

**Description** is filled from the **Details** tab (richer than meta); use meta `og:description` as fallback if the Details block is missing.

| Schema field | Selector | Get value |
|--------------|----------|-----------|
| `description` (primary) | `#description .product.attribute.description .value` | text content (Details tab) |
| `ingredients` | `#ingredients .product.attribute.ingredients .value` | text content |
| `instruction_for_use` | `#instruction_for_use .product.attribute.instruction_for_use .value` | text content |
| `storage_instructions` | `#storage_instructions .product.attribute.storage_instructions .value` | text content |

---

## 4. Flow summary

```
Sitemap XML
    → Step 1: Parse + URL filter (hcm-taka segment, exclude category slugs) → List of URLs
    → Step 2/3: For each URL: fetch (Playwright) → og:type check → if product: extract + country → add to list
    → Step 6: Write JSON (source_sitemaps, crawled_at, total_products, products with country)
    → Step 8: If --csv FILE: write CSV (one row per product, same columns as schema)
```

CLI: `--workers`, `--delay`, `--limit`, `--skip`, `--out`, `--csv`, `--proxy`, `--use-chrome`, `--cookies` (see README).

---

## 5. Out of scope for this plan

- Crawling category pages for product links (we use sitemap for URL discovery).
- Login, cart, or checkout.
- Handling pagination inside a product page (e.g. reviews). Can be added later if needed.
- Deduplication of products across multiple sitemaps (can be added as a post-step if needed).

---

## 6. Implementation status and file layout

- **Steps 1–8** are implemented.
- **Extraction:** `product_extract.py` (selectors, country via pycountry + spaCy). **Config:** `config.py` (timeouts, content-ready selector).
- **Utils** (in `utils/`): sitemap parsing and URL filter (`utils/sitemap_utils.py`), cookie loading (`utils/cookie_utils.py`), CSV export (`utils/export_utils.py`). Crawlers use `from utils import ...`.
- **Crawl orchestration:** `crawl_products.py` (headless), `crawl_products_headed.py` (optional `--headed`). Optional CSV via `--csv FILE`.
- Run instructions and full module layout: **README.md**.
