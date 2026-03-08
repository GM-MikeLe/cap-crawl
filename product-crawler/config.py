"""
Shared config for product crawler (timeouts, selectors). Change here to tune behavior.
"""

# --- Page load ---
# Max ms to wait for page.goto (domcontentloaded)
PAGE_LOAD_TIMEOUT_MS = 20_000

# Max ms to wait for content selector (meta/product elements in DOM)
SELECTOR_WAIT_TIMEOUT_MS = 10_000

# Max seconds for the whole "load page + check og:type" per URL. If exceeded, skip to next.
PER_URL_TIMEOUT_SEC = 35

# Selector used to detect page is ready (meta never "visible", so we use state=attached)
CONTENT_READY_SELECTOR = "meta[property='og:type'], .product.data.items, h1.page-title"

# If selector wait times out (e.g. homepage), sleep this many seconds then try reading og:type once
FALLBACK_SLEEP_AFTER_SELECTOR_TIMEOUT_SEC = 2

# --- Category page (build_category_product_mapping.py) ---
# Different from CONTENT_READY_SELECTOR: category listing page has product grid, not og:meta
CATEGORY_PAGE_READY_SELECTOR = ".product-item-link, .products.list, .page-title"
CATEGORY_PAGE_READY_TIMEOUT_MS = 12_000  # with multiple workers pages can load slower

# --- Browser ---
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
BROWSER_VIEWPORT = {"width": 1920, "height": 1080}
BROWSER_LOCALE = "en-US"

# --- Delay (crawl_utils.delay_async) ---
# Minimum delay between requests (seconds); delay range is [delay_sec * DELAY_FACTOR_LO, delay_sec * DELAY_FACTOR_HI]
DELAY_MIN_SEC = 0.3
DELAY_FACTOR_LO = 0.3
DELAY_FACTOR_HI = 1.0
