# Product Crawler — Refactoring Implementation Plan

This document summarizes a code review of the product-crawler module and proposes a refactoring plan for cleaner, more maintainable code. The reviewed files are: `utils/sitemap_utils.py`, `utils/quantity_utils.py`, `utils/export_utils.py`, `build_category_product_mapping.py`, `config.py`, `crawl_products_stealth.py`, and `product_extract.py`.

---

## Implemented (refactor completed)

- **Config + crawl_utils:** `BROWSER_USER_AGENT`, `BROWSER_VIEWPORT`, `BROWSER_LOCALE`, `DELAY_MIN_SEC`, `DELAY_FACTOR_LO/HI`, `CATEGORY_PAGE_READY_SELECTOR`, `CATEGORY_PAGE_READY_TIMEOUT_MS` in `config.py`; `utils/crawl_utils.py` with `delay_async()`.
- **category_mapping.py:** New module with `build_product_to_category_map`, `add_countries_and_categories_to_products`, `load_urls_and_category_map_from_mapping`; `crawl_products_stealth.py` uses it.
- **URL helpers in sitemap_utils:** `normalize_pagination_url`, `url_path_base`, `url_store_segment`, `is_parent_category_url`; exported from `utils/__init__.py`; `build_category_product_mapping.py` uses them.
- **build_category_product_mapping.py:** Uses config and crawl_utils; `run_category_crawl()` extracted; `main()` slimmed.
- **product_extract.py:** Selector constants; `_build_product_dict()`; `_CountryExtractor` with lazy caches; `extract_country()` wrapper; `normalize_price` return type annotated.
- **Polish:** `export_utils._csv_cell()`; `quantity_utils.QuantityResult` TypedDict; sitemap namespace docstring.

---

## 1. Summary: What’s Already Good

- **utils/** modules are focused and single-purpose; docstrings and types are clear.
- **config.py** is a single place for timeouts and selectors (with one exception noted below).
- **product_extract.py** clearly separates sync vs async extraction and documents the product schema.
- **Two-phase flow** in `crawl_products_stealth.py` (crawl → add country/category) is easy to follow.
- **utils/__init__.py** provides a clear public API for the package.

---

## 2. Issues and Refactoring Proposals

### 2.1 Duplication and Magic Values

| Issue | Location | Proposal |
|-------|----------|----------|
| **USER_AGENT** identical in `build_category_product_mapping.py` and `crawl_products_stealth.py` | Both files | Move to `config.py` (e.g. `BROWSER_USER_AGENT`) and import in both. |
| **_delay_async** identical implementation in both scripts | Both files | Move to a shared module, e.g. `utils/crawl_utils.py` or `utils/delay.py`, and reuse. |
| **Delay floor 0.3** and delay range (0.3×–1.0×) | Both files | Add `DELAY_MIN_SEC` (and optionally `DELAY_FACTOR_LO/HI`) to `config.py` and use in the shared delay helper. |
| **Viewport 1920×1080** and **locale "en-US"** | Both files | Add to `config.py` (e.g. `BROWSER_VIEWPORT`, `BROWSER_LOCALE`) for consistency and tuning. |
| **CONTENT_READY_SELECTOR** in `build_category_product_mapping.py` differs from `config.CONTENT_READY_SELECTOR` | build script vs config | Document in config that category builder may need a different selector (e.g. `.product-item-link, .products.list, .page-title`). Either add `CATEGORY_PAGE_READY_SELECTOR` in config or a short comment in config referencing the build script. |

**Implementation steps**

1. Add to `config.py`: `BROWSER_USER_AGENT`, `DELAY_MIN_SEC`, `BROWSER_VIEWPORT`, `BROWSER_LOCALE`; optionally `CATEGORY_PAGE_READY_SELECTOR` and `CONTENT_READY_TIMEOUT_MS` for category builder.
2. Add `utils/crawl_utils.py` with `async def delay_async(delay_sec: float) -> None` using config values.
3. In `crawl_products_stealth.py` and `build_category_product_mapping.py`: remove local `USER_AGENT` and `_delay_async`, import from config and `utils.crawl_utils`.
4. In both scripts, use config for viewport/locale when creating the browser context.

---

### 2.2 URL and Sitemap Logic Spread Across Modules

| Issue | Location | Proposal |
|-------|----------|----------|
| **normalize_pagination_url** (keeps query) vs **normalize_product_url** (strips query) | build_category_product_mapping | Keep both behaviors; consider moving `normalize_pagination_url` into `sitemap_utils.py` (or a small `url_utils.py`) so URL normalization lives in one place and is reusable. |
| **_url_path_base**, **_url_store_segment**, **is_parent_category_url** | build_category_product_mapping | Move to `sitemap_utils.py` (or `url_utils.py`) so category vs product URL rules are centralized and testable without Playwright. |

**Implementation steps**

1. In `sitemap_utils.py` (or new `utils/url_utils.py`): add `normalize_pagination_url`, `url_path_base`, `url_store_segment`, `is_parent_category_url`. Keep `is_product_url` and `normalize_product_url` in sitemap_utils if they stay; otherwise colocate in the same module.
2. Update `utils/__init__.py` to export the new helpers if they live under utils.
3. In `build_category_product_mapping.py`: remove local implementations and import from utils.

---

### 2.3 Category Mapping Logic in the Crawler

| Issue | Location | Proposal |
|-------|----------|----------|
| **_build_product_to_category_map**, **_load_urls_and_category_map_from_mapping**, **add_countries_and_categories_to_products** | crawl_products_stealth | Move to a dedicated module, e.g. `category_mapping.py`, so the stealth crawler focuses on orchestration and the mapping format is documented in one place. |

**Implementation steps**

1. Create `category_mapping.py` with:
   - `build_product_to_category_map(mapping: dict) -> dict[str, str]`
   - `load_urls_and_category_map_from_mapping(mapping_path: Path) -> tuple[list[str], dict[str, str]]`
   - `add_countries_and_categories_to_products(products, category_mapping_path=None, workers=1, category_reverse_map=None)` (and optionally `_assign_country_and_category` as a private helper).
2. In `crawl_products_stealth.py`: import these from `category_mapping` and call them; remove the in-file implementations.
3. Add a short docstring or comment in `category_mapping.py` describing the two supported JSON formats (list of `{url, category_name}` vs list of URL strings).

---

### 2.4 product_extract.py — Structure and Testability

| Issue | Location | Proposal |
|-------|----------|----------|
| **Duplicate logic** between `extract_product` and `extract_product_async` (same fields and conditionals) | product_extract | Introduce a single internal function that builds the product dict from already-fetched values (name, description_meta, price_meta, etc.); have both sync and async entry points only fetch data and then call this builder. |
| **Long CSS selectors** repeated in multiple `get_body_text_async` calls | product_extract | Define module-level constants, e.g. `SELECTOR_DESCRIPTION`, `SELECTOR_INGREDIENTS`, etc., and use them in both sync and async paths. |
| **Global caches** for stopwords and place names in **extract_country** | product_extract | Replace with a small class (e.g. `CountryExtractor`) that holds caches as instance attributes and is constructed once, or pass an optional cache object. This improves testability and avoids global state. |
| **Lazy import** in **apply_quantity_to_product** | product_extract | Keep if necessary to avoid circular imports; otherwise move `from utils.quantity_utils import parse_quantity_from_text` to top and add a brief comment that product_extract depends on utils. |
| **normalize_price** return type unclear | product_extract | Annotate as `int | float | str | None` and document that string is returned when parsing fails. |

**Implementation steps**

1. Add constants for description/ingredients/instruction/storage selectors.
2. Add an internal `_build_product_dict(...)` that takes raw values and returns the product dict; use it from both `extract_product` and `extract_product_async`.
3. Refactor country extraction into a `CountryExtractor` (or similar) with lazy-loaded caches; keep a module-level default instance and `extract_country(product)` as a thin wrapper for backward compatibility.
4. Add type hint for `normalize_price` and short docstring note on return behavior.

---

### 2.5 export_utils.py — Minor Cleanup

| Issue | Location | Proposal |
|-------|----------|----------|
| **Redundant row.get(k)** in list comprehension | export_utils | Use a one-line helper, e.g. `def _csv_cell(row, key): return "" if row.get(key) is None else str(row[key])`, and build the row with it for readability. |

**Implementation steps**

1. Add a small helper (or inline once) so each key is only looked up once per row when writing CSV.

---

### 2.6 build_category_product_mapping.py — Structure and Testability

| Issue | Location | Proposal |
|-------|----------|----------|
| **Large main()** with nested async and worker definition | build_category_product_mapping | Extract an async function, e.g. `async def run_category_crawl(urls, workers, limit, delay_sec) -> dict`, that takes URLs and options and returns the category→products dict. Keep CLI (argparse, file I/O) in `main()` and call `asyncio.run(run_category_crawl(...))`. |
| **Mutable index list [0]** for worker coordination | build_category_product_mapping | Keep as-is for simplicity, or replace with a simple shared counter class; avoid changing behavior, only clarity. |
| **Exception handler** defined inside main | build_category_product_mapping | Can stay inside `run_category_crawl` (or equivalent) so it’s still co-located with the loop that uses it. |

**Implementation steps**

1. Extract `run_category_crawl(...)` that contains Playwright setup, worker loop, and result aggregation; return `dict[str, list]`.
2. In `main()`: parse args, validate inputs, call `result = asyncio.run(run_category_crawl(...))`, then write `result` to `args.out`.
3. Optionally use a small class or dataclass for “current index” if you want to avoid the list mutation; not required for the first refactor.

---

### 2.7 crawl_products_stealth.py — Orchestration Clarity

| Issue | Location | Proposal |
|-------|----------|----------|
| **crawl()** does URL loading, phase 1, phase 2, and file writing | crawl_products_stealth | Split into: (1) `load_crawl_urls(sitemap_paths, from_mapping_path, skip, limit)` → `list[str]` and optional category map; (2) `run_phase1_crawl(all_urls, ...)` → `list[dict]`; (3) `run_phase2_enrich(products, category_reverse_map, workers)` (in-place); (4) `crawl()` orchestrates these and writes JSON/CSV. This improves testability and readability. |
| **fetch_one** defined inside **_crawl_async** | crawl_products_stealth | Acceptable for now; if you later extract `run_phase1_crawl` to a separate module, you can pass a “fetch_one_url” strategy or keep the worker logic inside that module. |

**Implementation steps**

1. Add `load_crawl_urls(...)` that handles `--from-mapping` vs sitemaps, filtering, skip, and limit; return `(all_urls, category_reverse_map_or_none)`.
2. Add `run_phase2_enrich(products, category_mapping_path=None, category_reverse_map=None, workers=1)` (can delegate to `category_mapping.add_countries_and_categories_to_products` and then apply quantity).
3. Refactor `crawl()` to: call `load_crawl_urls` → `run_phase1_crawl` (current `_crawl_async` body) → `run_phase2_enrich` → write JSON/CSV. Keep `_crawl_async` as the phase-1 implementation detail or rename to `run_phase1_crawl`.

---

### 2.8 sitemap_utils.py and quantity_utils.py — Optional Improvements

| Issue | Location | Proposal |
|-------|----------|----------|
| **parse_sitemap** namespace handling | sitemap_utils | Document that `tag.endswith("}loc")` is intentional for sitemap XML namespaces. No code change required. |
| **HCM_TAKA_CATEGORY_SLUGS** is site-specific | sitemap_utils | Optional: move to config or a small `sites.py` if you expect multiple sites; otherwise leave as-is with a one-line comment. |
| **quantity_utils** return type | quantity_utils | Consider a TypedDict or dataclass for the return value of `parse_quantity_from_text` so callers get clearer types. |

**Implementation steps**

1. Add a one-line comment in `parse_sitemap` about namespace handling.
2. (Optional) Introduce `QuantityResult` TypedDict/dataclass and use it as the return type of `parse_quantity_from_text`.

---

## 3. Suggested Order of Work

1. **Low-risk, high-impact**  
   - Centralize USER_AGENT, delay, viewport, locale in config + crawl_utils.  
   - Move category mapping helpers to `category_mapping.py`.  
   - Move URL helpers from build script to sitemap_utils (or url_utils).

2. **Medium**  
   - Refactor product_extract: selector constants, shared product-dict builder, CountryExtractor (or cache abstraction), type for normalize_price.  
   - Extract `run_category_crawl` in build_category_product_mapping and slim down main().

3. **Orchestration**  
   - Split `crawl()` in crawl_products_stealth into load_crawl_urls, phase1, phase2, and write.

4. **Polish**  
   - export_utils CSV row helper; sitemap/quantity docstrings and optional types.

---

## 4. Files to Touch (Checklist)

| File | Actions |
|------|--------|
| **config.py** | Add BROWSER_USER_AGENT, DELAY_MIN_SEC, BROWSER_VIEWPORT, BROWSER_LOCALE; optionally category-page selector/timeout. |
| **utils/crawl_utils.py** | New file: `delay_async(delay_sec)`. |
| **utils/sitemap_utils.py** | Add URL helpers from build script; optional comment/constant for category slugs; document namespace in parse_sitemap. |
| **utils/__init__.py** | Export new sitemap/URL helpers and, if desired, delay_async. |
| **category_mapping.py** | New file: build map, load from file, add_countries_and_categories. |
| **crawl_products_stealth.py** | Use config + crawl_utils; use category_mapping module; optional split of crawl() into load/phase1/phase2/write. |
| **build_category_product_mapping.py** | Use config + crawl_utils; use sitemap_utils (or url_utils) for URL helpers; extract run_category_crawl; slim main(). |
| **product_extract.py** | Selector constants; _build_product_dict; CountryExtractor or cache; normalize_price type; optional TypedDict for quantity result in quantity_utils. |
| **utils/export_utils.py** | Small CSV row helper. |
| **utils/quantity_utils.py** | Optional TypedDict/dataclass for return type. |

---

## 5. Testing and Backward Compatibility

- **CLI**: After each step, run the same commands you use in production (sitemap, from-mapping, category builder) and compare outputs (e.g. JSON keys and row count).
- **Imports**: Keep `from utils import ...` and `from config import ...` working; add new exports in __all__ where appropriate.
- **No change to** product schema (CSV_FIELDNAMES, JSON structure) or category mapping JSON format unless you explicitly plan a versioned change.

This plan prioritizes maintainability and testability without changing external behavior or data formats.
