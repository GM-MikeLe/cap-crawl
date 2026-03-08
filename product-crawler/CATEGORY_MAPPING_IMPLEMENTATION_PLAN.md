# Category Mapping Implementation Plan

Products are attached to **subcategories** (not top-level categories). Product detail URLs do not contain category info. This plan builds a **product URL → subcategory** mapping by crawling subcategory listing pages, then uses that mapping when crawling products in `crawl_products_stealth.py`.

---

## Prerequisites

- **Category XML file**: Sitemap-style XML with `<loc>` URLs for category/subcategory pages (e.g. `category.xml`). Each `<loc>` is a category or subcategory listing page.
- **Product link selector**: On listing pages, product links use class `product-item-link` (Magento-style).
- **Run order**: Step 1 must be run before Step 2 so the mapping JSON exists.

---

## Step 1: Build subcategory → product links mapping (Python script)

### Goal

- **Input**: Path to a category XML file (e.g. `category.xml`).
- **Behavior**: For each subcategory URL in the XML, visit the page, find all product links (by selector `.product-item-link`), optionally follow pagination, and collect product URLs.
- **Output**: A JSON file that maps **subcategory page URL** → **list of product page URLs** for later reverse lookup.

### 1.1 Script interface

- **CLI**: e.g. `python build_category_product_mapping.py category.xml --out category_product_mapping.json`
- **Arguments**:
  - Positional: path to category XML file.
  - `--out`: output JSON path (default e.g. `category_product_mapping.json`).
  - `--delay`: seconds between requests (default e.g. `0.5`, min ~`0.3`) to avoid blocking.
  - `--limit`: optional max number of subcategory pages to crawl (for testing).
  - `--workers`: number of concurrent subcategory pages to crawl (default e.g. `2`–`4` for speed; use with `--delay` to avoid blocking).

### 1.2 Parse category XML

- Use the same approach as `utils/sitemap_utils.parse_sitemap()`: parse XML, collect all `<loc>` text (strip whitespace).
- Result: list of category/subcategory page URLs. No need to filter by “subcategory only” at this stage; every `<loc>` is a listing page that may contain product links.

### 1.3 Crawl each subcategory page

- Use Playwright (async, headless). Reuse patterns from `crawl_products_stealth.py` (e.g. stealth, delay, timeouts) if desired.
- For each URL:
  - Navigate to the page.
  - Wait for content (e.g. a selector that indicates the product grid or `.product-item-link`).
  - Collect all product links: query `a.product-item-link`, get `href` for each, resolve to absolute URL (same domain), normalize (e.g. strip fragment, optional trailing slash) so they match product URLs used in the product sitemap/crawler.
  - **Pagination**: Use `?p=2`-style pagination. Multiple elements may link to the same next page (e.g. page number "2" and "Next" both point to `?p=2`). Collect all next-page URLs (e.g. links containing `?p=2`, `?p=3` or a "Next" link to the same URL), **dedupe by normalized URL**, and visit each distinct page **only once** so the same `?p=2` is never crawled twice. Merge product links from every visited page into one set per subcategory so each product is listed once per subcategory.

### 1.4 Output JSON format

- **Structure**: One object: keys = subcategory page URLs (string), values = list of product page URLs (strings).
- **Example**:
  ```json
  {
    "https://shop.annam-gourmet.com/hcm-taka/sweet-grocery/biscuits-and-cakes/butter-biscuits.html": [
      "https://shop.annam-gourmet.com/hcm-taka/product-a.html",
      "https://shop.annam-gourmet.com/hcm-taka/product-b.html"
    ],
    "https://shop.annam-gourmet.com/hcm-taka/sweet-grocery/breakfast/jam-and-jellies.html": [
      "https://shop.annam-gourmet.com/hcm-taka/product-c.html"
    ]
  }
  ```
- A product may appear under multiple subcategories; that is fine. Step 2 uses **first subcategory encountered** when building the reverse map (see §2.2).

### 1.5 Error and robustness

- If a subcategory page fails (timeout, non-200), log and skip; store an empty list or omit the key for that URL.
- Deduplicate product URLs within each subcategory list.
- Write the JSON only after all crawls (or periodically if you add checkpointing later).

### 1.6 Deliverable

- New file: `build_category_product_mapping.py` (or similar name) in `product-crawler/`.
- Output: `category_product_mapping.json` (or path given by `--out`).

---

## Step 2: Use mapping in crawl_products_stealth and add `category` to product schema

### Goal

- Ensure each product object in the stealth crawler has a **product page URL** so we can look up category.
- After mapping **country**, map **category** using the JSON from Step 1 and set `product["category"]` (subcategory URL or a human-readable label).

### 2.1 Add product URL to the product dict (Phase 1)

- In `crawl_products_stealth.py`, inside `_crawl_async`, when a product is successfully extracted and appended:
  - Set **`product["url"] = url`** (the product page URL that was crawled).
  - Use the same normalization as in Step 1 (e.g. no fragment, consistent trailing slash) so the string matches the keys in the reverse mapping built in 2.2.
- So the in-memory product schema after Phase 1 includes: `name`, `price`, `currency`, `description`, optional `ingredients`, `instruction_for_use`, `storage_instructions`, and **`url`**.

### 2.2 Build reverse lookup: product URL → subcategory

- In Phase 2 (after crawl, before or after country mapping), load the mapping JSON from Step 1 (path can be a new CLI flag, e.g. `--category-mapping category_product_mapping.json`; if not provided, skip category step).
- Build a **reverse map**: for each subcategory URL and each product URL in its list, set `reverse_map[product_url] = subcategory_url`. If a product appears in multiple subcategories, use **first subcategory encountered** (order of iteration over the mapping JSON); do not overwrite once a product has been assigned. Store a single string in `product["category"]`.


### 2.3 Set `product["category"]` in Phase 2 (with workers)

- After Phase 1, run Phase 2 with **workers** so country and category mapping run in parallel over the product list for speed (e.g. `ThreadPoolExecutor` or `ProcessPoolExecutor`: for each product, run `extract_country(p)` and category lookup; then assign `p["country"]` and `p["category"]`). Reuse or expose the same `--workers` (or a Phase-2 worker count) so users can tune parallelism.
- Logic: for each `p` in `products`, set `p["url"]` (already set in Phase 1), then `p["country"] = extract_country(p)`, then `p["category"] = reverse_map.get(p.get("url"), "")` (or omit/skip category if `--category-mapping` was not provided).
- If `--category-mapping` was not provided, set `p["category"]` to `""` or omit the field.

### 2.4 CLI and config

- Add argument: `--category-mapping PATH` (optional). When provided, load JSON, build reverse map, and set `category` on each product.
- Ensure the path is relative to CWD or absolute; document in script docstring.

### 2.5 Output schema

- Final product schema must include:
  - Existing: `name`, `price`, `currency`, `description`, `ingredients`, `instruction_for_use`, `storage_instructions`, `country`.
  - New: **`url`** (product page URL), **`category`** (subcategory page URL or chosen label, or empty string if not found).
- `utils/export_utils.write_products_csv` (or equivalent) should be updated to include a **`url`** column and a **`category`** column so exports stay consistent.

### 2.6 Files to touch

- **`crawl_products_stealth.py`**:
  - In `_crawl_async`: set `product["url"] = url` before appending.
  - In `crawl()`: add Phase 2 step to load category mapping (when `--category-mapping` is set), build reverse map, then run country and category assignment **with workers** (e.g. `ThreadPoolExecutor`/`ProcessPoolExecutor`) over the product list for speed.
  - Add `--category-mapping` CLI argument and pass path into `crawl()`. Reuse or expose `--workers` for Phase 2 so users can tune parallelism.
- **`utils/export_utils.py`** (or wherever CSV columns are defined): add `url` and `category` to the list of columns so they appear in the written CSV.

### 2.7 Summary flow

1. Phase 1: Crawl product URLs (with `--workers`) → extract product dict → set `product["url"] = url` → append to `products`.
2. Phase 2: Load category mapping JSON (if `--category-mapping` set) and build reverse map; then run **country + category assignment with workers** over the product list (e.g. `extract_country(p)` and category lookup in parallel); then `apply_quantity_to_product` for each product.
3. Write JSON and CSV with `url` and `category` included.

---

## Step-by-step checklist

- [ ] **Step 1.1** Implement CLI for `build_category_product_mapping.py` (input XML, `--out`, `--delay`, `--limit`, `--workers`).
- [ ] **Step 1.2** Parse category XML and collect all `<loc>` URLs.
- [ ] **Step 1.3** Crawl each URL with Playwright (use `--workers` for parallel subcategory pages); collect `a.product-item-link` hrefs; resolve and normalize; handle pagination (dedupe next-page URLs, visit each page once).
- [ ] **Step 1.4** Write JSON: `{ "subcategory_url": [ "product_url", ... ], ... }`.
- [ ] **Step 1.5** Handle errors and dedupe; test with a small `--limit`.
- [ ] **Step 2.1** In `crawl_products_stealth.py`, set `product["url"] = url` when appending in Phase 1.
- [ ] **Step 2.2** Add `--category-mapping`; load JSON and build reverse map (product URL → subcategory).
- [ ] **Step 2.3** In Phase 2, run country and category mapping with workers; set `product["category"]` from the reverse map.
- [ ] **Step 2.4** Add CSV columns `url` and `category` in export utils.
- [ ] **Step 2.5** Run: first `python build_category_product_mapping.py category.xml --out category_product_mapping.json`, then `python crawl_products_stealth.py ... --category-mapping category_product_mapping.json` and verify `products.json` and CSV contain `url` and `category`.

---

## Notes

- **URL normalization**: Use the same rules in Step 1 (when storing product URLs) and in Step 2 (when setting `product["url"]` and when looking up). E.g. `urlparse` + strip fragment, lowercase domain, no trailing slash for path, so that `reverse_map.get(product["url"])` finds a match.
- **Subcategory label**: The plan stores subcategory **URL** in `product["category"]`. If you prefer a human-readable label (e.g. path segment like `butter-biscuits` or full path `sweet-grocery/biscuits-and-cakes/butter-biscuits`), derive it from the subcategory URL when building the reverse map or when writing the product (e.g. path component of URL or a separate mapping from subcategory URL to label).
