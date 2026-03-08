"""
Sitemap parsing and URL filtering for product crawl.
Reused by crawl_products.py and crawl_products_headed.py.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse


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


def normalize_product_url(url: str, base: str | None = None) -> str:
    """Canonical form for product URLs: no fragment, no query, path without trailing slash."""
    if base:
        url = urljoin(base, url)
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme, p.netloc, path, "", "", ""))


def normalize_pagination_url(url: str, base: str | None = None) -> str:
    """Keep query (?p=2) for pagination; strip fragment only."""
    if base:
        url = urljoin(base, url)
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, p.query, ""))


def url_path_base(url: str) -> tuple[str, str, str]:
    """Return (scheme, netloc, path) for URL so we can restrict pagination to same category path."""
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    return (p.scheme, p.netloc, path)


def url_store_segment(url: str) -> str | None:
    """First path segment after host (e.g. 'hcm-taka'). None if path has no segments."""
    p = urlparse(url)
    parts = [s for s in p.path.split("/") if s]
    return parts[0] if parts else None


def is_parent_category_url(url: str) -> bool:
    """True if URL is a first-level (parent) category, e.g. .../hcm-taka/sweet-grocery.html."""
    p = urlparse(url)
    parts = [s for s in p.path.split("/") if s]
    return len(parts) == 2 and parts[1].endswith(".html")


def parse_sitemap(path: Path) -> list[str]:
    """Collect all <loc> URLs from a sitemap XML file. Handles XML namespace (tag.endswith('}loc'))."""
    tree = ET.parse(path)
    root = tree.getroot()
    urls = []
    for loc in root.iter():
        if loc.tag.endswith("}loc") and loc.text and loc.text.strip():
            urls.append(loc.text.strip())
    return urls
