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
