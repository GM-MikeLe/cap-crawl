#!/usr/bin/env python3
"""
Build parent category → product URL mapping by crawling first-level category pages only.

Reads a category sitemap XML, keeps only parent category URLs (e.g. .../hcm-taka/sweet-grocery.html),
visits each parent page and its pagination (?p=2, …), collects all product links from that page.
No subcategory URLs are visited; the parent page lists all products (and subcategory links).

Usage (run with project venv activated: source .venv/bin/activate):
  python build_category_product_mapping.py category.xml --out category_product_mapping.json
  python build_category_product_mapping.py category.xml --limit 5 --workers 2

Options:
  --out FILE    Output JSON path (default category_product_mapping.json)
  --delay SECS  Seconds between requests (default 0.5, min ~0.3)
  --limit N     Max parent category pages to crawl (for testing)
  --workers N   Concurrent pages (default 2)
"""

import argparse
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urljoin

from config import (
    BROWSER_LOCALE,
    BROWSER_USER_AGENT,
    BROWSER_VIEWPORT,
    CATEGORY_PAGE_READY_SELECTOR,
    CATEGORY_PAGE_READY_TIMEOUT_MS,
    PAGE_LOAD_TIMEOUT_MS,
)
from utils import (
    delay_async,
    is_parent_category_url,
    normalize_pagination_url,
    normalize_product_url,
    parse_sitemap,
    url_path_base,
    url_store_segment,
)

PRODUCT_LINK_SELECTOR = "a.product-item-link"
CATEGORIES_DIV_SELECTOR = "div.categories"  # category name per product block, e.g. "MIX NUTS"
PAGINATION_LINK_SELECTOR = "a[href*='?p=']"


async def crawl_one_category(
    page,
    category_url: str,
    delay_sec: float,
) -> list[dict]:
    """
    Visit category_url (parent category) and its pagination pages; collect product links
    and category name from each product block's <div class="categories"> (e.g. "MIX NUTS").
    Returns list of {"url": product_url, "category_name": "MIX NUTS"} (first occurrence per URL wins).
    """
    # product_url -> category_name (first wins when same product on multiple pages)
    product_to_name: dict[str, str] = {}
    to_visit: set[str] = {normalize_pagination_url(category_url)}
    visited: set[str] = set()
    path_base = url_path_base(category_url)  # only follow pagination for same path
    store_segment = url_store_segment(category_url)  # only collect products from same store
    page_count = 0

    while to_visit:
        current = to_visit.pop()
        if current in visited:
            continue
        visited.add(current)
        page_count += 1
        print(f"    Page {page_count}: {current}", file=sys.stderr, flush=True)

        try:
            await page.goto(
                current,
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT_MS,
            )
        except Exception as e:
            err_msg = str(e)
            # net::ERR_ABORTED is common for high ?p= (e.g. p=37) when server/CDN aborts
            if "ERR_ABORTED" in err_msg or "net::" in err_msg:
                print(f"    Navigation aborted {current} (skipping)", file=sys.stderr, flush=True)
            else:
                print(f"    goto error {current}: {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
            await delay_async(delay_sec)
            continue

        try:
            await page.wait_for_selector(
                CATEGORY_PAGE_READY_SELECTOR,
                timeout=CATEGORY_PAGE_READY_TIMEOUT_MS,
                state="attached",
            )
        except Exception as e:
            # Category index pages may have no product grid; continue and try to collect links anyway
            print(f"    wait_for_selector timeout or error ({current}): {e}", file=sys.stderr)
            # no traceback for timeout to avoid noise; uncomment for debugging:
            # traceback.print_exc(file=sys.stderr)

        # Product links + category name from <div class="categories"> in same product block
        try:
            cat_divs = await page.locator(CATEGORIES_DIV_SELECTOR).all()
            for cat_el in cat_divs:
                category_name = (await cat_el.text_content() or "").strip()
                container = cat_el.locator("xpath=..")
                link_el = container.locator("a.product-item-link").first
                href = await link_el.get_attribute("href")
                if href and href.strip():
                    full = urljoin(current, href.strip())
                    norm = normalize_product_url(full, current)
                    if norm and url_store_segment(norm) == store_segment and norm not in product_to_name:
                        product_to_name[norm] = category_name
            # Fallback: if no div.categories found, collect by product-item-link only (category_name "")
            if not product_to_name:
                links = await page.locator(PRODUCT_LINK_SELECTOR).all()
                for link in links:
                    href = await link.get_attribute("href")
                    if href and href.strip():
                        full = urljoin(current, href.strip())
                        norm = normalize_product_url(full, current)
                        if norm and url_store_segment(norm) == store_segment and norm not in product_to_name:
                            product_to_name[norm] = ""
        except Exception as e:
            print(f"    Product links error: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

        # Pagination: collect next-page URLs for this subcategory only (same path; ignore other stores)
        try:
            next_links = await page.locator(PAGINATION_LINK_SELECTOR).all()
            for link in next_links:
                href = await link.get_attribute("href")
                if href and href.strip():
                    full = urljoin(current, href.strip())
                    norm = normalize_pagination_url(full, current)
                    if norm and norm not in visited and url_path_base(norm) == path_base:
                        to_visit.add(norm)
        except Exception as e:
            print(f"    Pagination links error: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

        await delay_async(delay_sec)

    return [{"url": u, "category_name": product_to_name[u]} for u in sorted(product_to_name)]


async def run_category_crawl(
    urls: list[str],
    workers: int,
    limit: int | None,
    delay_sec: float,
) -> dict[str, list]:
    """Crawl parent category URLs; return mapping category_url -> list of {url, category_name}."""
    result: dict[str, list] = {}
    lock = asyncio.Lock()

    def _handle_task_exception(loop, context):
        exc = context.get("exception")
        msg = context.get("message", "")
        if exc is not None:
            err_str = str(exc)
            if "ERR_ABORTED" in err_str or "net::" in err_str:
                print(f"    (Navigation aborted in background: {err_str[:80]}...)", file=sys.stderr, flush=True)
                return
        print(f"    Unhandled task: {msg}", file=sys.stderr, flush=True)
        if exc is not None:
            print(f"      {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    from playwright.async_api import async_playwright

    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_handle_task_exception)

    try:
        from playwright_stealth import Stealth
        p_cm = Stealth().use_async(async_playwright())
    except ImportError:
        p_cm = async_playwright()

    index = [0]

    async with p_cm as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=BROWSER_USER_AGENT,
            viewport=BROWSER_VIEWPORT,
            locale=BROWSER_LOCALE,
        )

        async def worker(_wid: int) -> None:
            page = await context.new_page()
            try:
                while True:
                    async with lock:
                        if limit is not None and index[0] >= limit:
                            return
                        if index[0] >= len(urls):
                            return
                        url = urls[index[0]]
                        pos = index[0] + 1
                        index[0] += 1
                    print(f"[{pos}/{len(urls)}] {url}", file=sys.stderr)
                    t0 = time.perf_counter()
                    try:
                        product_list = await asyncio.wait_for(
                            crawl_one_category(page, url, delay_sec),
                            timeout=120,
                        )
                    except asyncio.TimeoutError:
                        print(f"  Timeout ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                        product_list = []
                    except Exception as e:
                        print(f"  Error: {type(e).__name__}: {e} ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                        traceback.print_exc(file=sys.stderr)
                        product_list = []
                    async with lock:
                        result[url] = product_list
                    print(f"  -> {len(product_list)} products ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
            finally:
                await page.close()

        await asyncio.gather(*[worker(w) for w in range(workers)])
        await browser.close()

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build subcategory → product URL mapping from category XML.",
    )
    parser.add_argument(
        "category_xml",
        type=Path,
        help="Category sitemap XML file (e.g. category.xml)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("category_product_mapping.json"),
        help="Output JSON path",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds between requests (default 0.5)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max subcategory pages to crawl (for testing)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Concurrent category pages (default 1; use 1 to avoid empty results from rate limiting)",
    )
    args = parser.parse_args()

    if not args.category_xml.exists():
        print(f"Error: file not found: {args.category_xml}", file=sys.stderr)
        sys.exit(1)
    if args.delay < 0.3:
        args.delay = 0.3
    if args.workers < 1:
        parser.error("--workers must be >= 1")

    all_urls = parse_sitemap(args.category_xml)
    urls = [u for u in all_urls if is_parent_category_url(u)]
    if len(urls) < len(all_urls):
        print(f"Filtered to parent categories only: {len(urls)} (dropped {len(all_urls) - len(urls)} subcategory URLs)", file=sys.stderr)
    if args.limit is not None:
        urls = urls[: args.limit]
    print(f"Parent category URLs to crawl: {len(urls)}", file=sys.stderr)

    result = asyncio.run(run_category_crawl(urls, args.workers, args.limit, args.delay))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {args.out} ({len(result)} parent categories)", file=sys.stderr)


if __name__ == "__main__":
    main()
