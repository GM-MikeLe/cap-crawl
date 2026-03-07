"""
Sitemap parsing and URL filtering for product crawl.
Reused by crawl_products.py and crawl_products_headed.py.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse


# Category slugs (one segment after hcm-taka/) to skip — e.g. .../hcm-taka/sweet-grocery.html
HCM_TAKA_CATEGORY_SLUGS = frozenset({
    "sweet-grocery",
    "salty-grocery",
    "fresh-food",
    "frozen-food",
    "pastry-and-bakery",
    "beverages",
    "non-food",
    "health-and-beauty",
    "lounge",
    "pet-care",
    "main-health",
    "collection",
})


def is_product_url(url: str) -> bool:
    """True if URL is a product page: one segment after hcm-taka/, and not a known category slug."""
    path = urlparse(url).path.rstrip("/")
    if not path.startswith("/hcm-taka/") or path == "/hcm-taka":
        return False
    after = path[len("/hcm-taka/"):]
    if not after or "/" in after:
        return False
    slug = after.removesuffix(".html") if after.endswith(".html") else after
    return slug not in HCM_TAKA_CATEGORY_SLUGS


def parse_sitemap(path: Path) -> list[str]:
    """Collect all <loc> URLs from a sitemap XML file."""
    tree = ET.parse(path)
    root = tree.getroot()
    urls = []
    for loc in root.iter():
        if loc.tag.endswith("}loc") and loc.text and loc.text.strip():
            urls.append(loc.text.strip())
    return urls
