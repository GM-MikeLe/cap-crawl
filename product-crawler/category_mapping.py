"""
Category mapping: load JSON from build_category_product_mapping.py and assign category (and country) to products.

Supports two JSON formats:
- New: value = [ {"url": product_url, "category_name": "MIX NUTS"}, ... ] -> use category_name
- Old: value = [ "product_url", ... ] -> use category page URL (key) as label
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from product_extract import extract_country


def build_product_to_category_map(mapping: dict) -> dict[str, str]:
    """
    Build product URL -> category (name or URL). Supports:
    - New format: value = [ {"url": product_url, "category_name": "MIX NUTS"}, ... ] -> use category_name
    - Old format: value = [ "product_url", ... ] -> use category page URL (key)
    """
    reverse: dict[str, str] = {}
    for category_url, product_list in mapping.items():
        for item in product_list:
            if isinstance(item, dict):
                url = item.get("url")
                name = (item.get("category_name") or "").strip()
                label = name if name else category_url
            else:
                url = item
                label = category_url
            if url and url not in reverse:
                reverse[url] = label
    return reverse


def _assign_country_and_category(p: dict, reverse_map: dict[str, str]) -> None:
    """In-place: set country and category for one product."""
    p["country"] = extract_country(p)
    p["category"] = reverse_map.get(p.get("url") or "", "")


def add_countries_and_categories_to_products(
    products: list[dict],
    category_mapping_path: Path | None,
    workers: int = 1,
    category_reverse_map: dict[str, str] | None = None,
) -> None:
    """Phase 2: Add country and category to each product (in-place), with optional workers."""
    reverse_map: dict[str, str] = category_reverse_map if category_reverse_map is not None else {}
    if not reverse_map and category_mapping_path is not None and category_mapping_path.exists():
        raw = json.loads(category_mapping_path.read_text(encoding="utf-8"))
        reverse_map = build_product_to_category_map(raw)

    if workers <= 1:
        for p in products:
            _assign_country_and_category(p, reverse_map)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(
                lambda p: _assign_country_and_category(p, reverse_map),
                products,
            ))


def _url_from_item(item) -> str | None:
    """Extract product URL from mapping item (dict with 'url' or plain string)."""
    if isinstance(item, dict):
        return item.get("url")
    return item if item else None


def load_urls_and_category_map_from_mapping(mapping_path: Path) -> tuple[list[str], dict[str, str]]:
    """Load category mapping JSON; return (unique product URLs in order of first occurrence, product_url -> category name/URL)."""
    raw = json.loads(mapping_path.read_text(encoding="utf-8"))
    reverse_map = build_product_to_category_map(raw)
    all_urls = []
    for urls in raw.values():
        for item in urls:
            u = _url_from_item(item)
            if u:
                all_urls.append(u)
    all_urls = list(dict.fromkeys(all_urls))
    return all_urls, reverse_map
