#!/usr/bin/env python3
"""
Stealth-only product crawl: minimal setup for speed.

- Playwright stealth only (no cookies, no saved session, no headed)
- Minimum delay between requests
- Two-phase flow:
  1. Phase 1: Crawl URLs, extract product info (no country), save to JSON
  2. Phase 2: Add country from JSON (batch), write final JSON and CSV

Usage (run with project venv activated: source .venv/bin/activate):

  # Product URLs from sitemap XML (optional: add categories via --category-mapping)
  python crawl_products_stealth.py --limit 10 saigoncenter_en_sitemap.xml --out products.json --csv products.csv
  python crawl_products_stealth.py --category-mapping category_product_mapping.json saigoncenter_en_sitemap.xml --out products.json

  # Product URLs (and categories) from mapping JSON — no sitemap, no separate category step
  python crawl_products_stealth.py --from-mapping category_product_mapping.json --out products.json --csv products.csv

Options:
  --workers N      Concurrent pages (default 1)
  --delay SECS     Seconds between requests (default 0.5, min ~0.3)
  --limit N        Max products to extract
  --skip N         Skip first N URLs
  --out FILE       Output JSON path (default products_stealth.json)
  --csv FILE       Also write CSV in final step
  --category-mapping FILE  When using sitemaps: set product category from this JSON
  --from-mapping FILE      Use product URLs and categories from this JSON instead of sitemaps
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from category_mapping import (
    add_countries_and_categories_to_products,
    load_urls_and_category_map_from_mapping,
)
from config import (
    BROWSER_LOCALE,
    BROWSER_USER_AGENT,
    BROWSER_VIEWPORT,
    CONTENT_READY_SELECTOR,
    FALLBACK_SLEEP_AFTER_SELECTOR_TIMEOUT_SEC,
    PAGE_LOAD_TIMEOUT_MS,
    PER_URL_TIMEOUT_SEC,
    SELECTOR_WAIT_TIMEOUT_MS,
)
from product_extract import apply_quantity_to_product, extract_product_async, get_meta_content_async
from utils import delay_async, is_product_url, normalize_product_url, parse_sitemap, write_products_csv


async def _load_page_and_get_og_type(page, url: str):
    """Load URL, wait for content selector (or fallback), return og:type or None. May raise."""
    await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
    try:
        await page.wait_for_selector(
            CONTENT_READY_SELECTOR,
            timeout=SELECTOR_WAIT_TIMEOUT_MS,
            state="attached",
        )
    except Exception:
        await asyncio.sleep(FALLBACK_SLEEP_AFTER_SELECTOR_TIMEOUT_SEC)
    return await get_meta_content_async(page, "og:type")


async def _crawl_async(
    all_urls: list[str],
    *,
    delay_sec: float,
    limit: int | None,
    products: list,
    product_count: list,
    workers: int,
) -> None:
    """Phase 1: Crawl URLs, extract products (no country)."""
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    url_index = [0]
    lock = asyncio.Lock()

    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(headless=True)
        ctx_opts: dict = {
            "user_agent": BROWSER_USER_AGENT,
            "viewport": BROWSER_VIEWPORT,
            "locale": BROWSER_LOCALE,
        }
        context = await browser.new_context(**ctx_opts)

        async def fetch_one(_worker_id: int) -> None:
            while True:
                async with lock:
                    if limit is not None and product_count[0] >= limit:
                        return
                    if url_index[0] >= len(all_urls):
                        return
                    url = all_urls[url_index[0]]
                    pos = url_index[0] + 1
                    url_index[0] += 1

                print(f"[{pos}/{len(all_urls)}] {url}", file=sys.stderr)
                t0 = time.perf_counter()
                page = await context.new_page()
                load_task = asyncio.create_task(_load_page_and_get_og_type(page, url))
                try:
                    og_type = await asyncio.wait_for(
                        load_task,
                        timeout=PER_URL_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    load_task.cancel()
                    await page.close()
                    try:
                        await load_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    print(f"  Skip (timeout) ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                    await delay_async(delay_sec)
                    continue
                except Exception as e:
                    print(f"  Skip (load error): {e} ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                    await page.close()
                    await delay_async(delay_sec)
                    continue

                if og_type is None:
                    print(f"  Skip (no og:type) ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                    await page.close()
                    await delay_async(delay_sec)
                    continue
                if og_type != "product":
                    print(f"  Skip (og:type={og_type}) ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                    await page.close()
                    await delay_async(delay_sec)
                    continue

                product = await extract_product_async(page)
                await page.close()
                # Skip products without details (only name, no description)
                if not (product.get("description") or "").strip():
                    print(f"  Skip (no details) ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                    await delay_async(delay_sec)
                    continue
                # Phase 2 adds country and category; store canonical URL for lookup
                product["url"] = normalize_product_url(url)
                elapsed = time.perf_counter() - t0

                async with lock:
                    products.append(product)
                    product_count[0] += 1
                print(f"  -> product: {product.get('name', '')[:50]}... ({elapsed:.1f}s)", file=sys.stderr)
                await delay_async(delay_sec)

        await asyncio.gather(*[fetch_one(w) for w in range(workers)])
        await browser.close()


def crawl(
    sitemap_paths: list[Path],
    *,
    delay_sec: float = 0.5,
    limit: int | None = None,
    skip: int = 0,
    out_path: Path,
    csv_path: Path | None = None,
    workers: int = 1,
    category_mapping_path: Path | None = None,
    from_mapping_path: Path | None = None,
) -> None:
    category_reverse_map: dict[str, str] | None = None

    if from_mapping_path is not None and from_mapping_path.exists():
        # Product URLs and categories from mapping JSON; skip sitemap and separate category step
        all_urls, category_reverse_map = load_urls_and_category_map_from_mapping(from_mapping_path)
        print(f"Loaded {len(all_urls)} product URLs from {from_mapping_path.name} (categories included)", file=sys.stderr)
    else:
        all_urls = []
        for path in sitemap_paths:
            if not path.exists():
                print(f"Warning: sitemap not found: {path}", file=sys.stderr)
                continue
            urls = parse_sitemap(path)
            all_urls.extend(urls)
            print(f"Parsed {path.name}: {len(urls)} URLs", file=sys.stderr)
        all_urls = list(dict.fromkeys(all_urls))
        before = len(all_urls)
        all_urls = [u for u in all_urls if is_product_url(u)]
        if len(all_urls) < before:
            print(
                f"Filtered to product URLs: {len(all_urls)} (dropped {before - len(all_urls)})",
                file=sys.stderr,
            )

    if skip > 0:
        all_urls = all_urls[skip:]
        print(f"Skipped first {skip} URLs; {len(all_urls)} left", file=sys.stderr)
    print(f"Phase 1: crawl {len(all_urls)} URLs (stealth, headless, delay={delay_sec}s)", file=sys.stderr)

    products: list[dict] = []
    product_count = [0]
    asyncio.run(
        _crawl_async(
            all_urls,
            delay_sec=delay_sec,
            limit=limit,
            products=products,
            product_count=product_count,
            workers=workers,
        )
    )

    # Phase 2: add country and category (with workers), then quantity
    # When from_mapping was used, category_reverse_map is already set; else use category_mapping_path
    print(f"Phase 2: extract country and category for {len(products)} products (workers={workers})", file=sys.stderr)
    add_countries_and_categories_to_products(
        products,
        None if category_reverse_map is not None else category_mapping_path,
        workers=workers,
        category_reverse_map=category_reverse_map,
    )
    for p in products:
        apply_quantity_to_product(p)

    out = {
        "source_sitemaps": [str(p) for p in sitemap_paths] if from_mapping_path is None else [],
        "source_mapping": str(from_mapping_path) if from_mapping_path is not None else None,
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "total_products": len(products),
        "products": products,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(out, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {out_path} ({len(products)} products)", file=sys.stderr)
    if csv_path is not None:
        write_products_csv(products, csv_path)
        print(f"Wrote {csv_path} ({len(products)} rows)", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Stealth-only crawl: two-phase (crawl → country), minimal delay.",
    )
    parser.add_argument(
        "sitemaps",
        nargs="*",
        type=Path,
        help="Sitemap XML file(s). Default: saigoncenter_en_sitemap.xml",
    )
    parser.add_argument("--workers", type=int, default=1, help="Concurrent pages (default 1)")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between requests (default 0.5, min ~0.3)")
    parser.add_argument("--limit", type=int, default=None, help="Max products to extract")
    parser.add_argument("--skip", type=int, default=0, help="Skip first N URLs")
    parser.add_argument("--out", type=Path, default=Path("products_stealth.json"), help="Output JSON path")
    parser.add_argument("--csv", type=Path, default=None, metavar="FILE", help="Also write CSV in final step")
    parser.add_argument(
        "--category-mapping",
        type=Path,
        default=None,
        metavar="FILE",
        help="When using sitemaps: JSON from build_category_product_mapping.py to set product category",
    )
    parser.add_argument(
        "--from-mapping",
        type=Path,
        default=None,
        metavar="FILE",
        help="Use product URLs (and categories) from this mapping JSON instead of sitemaps; skip --category-mapping",
    )
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers must be >= 1")
    if args.skip < 0:
        parser.error("--skip must be >= 0")

    base = Path(__file__).parent
    if args.from_mapping is not None:
        if not args.from_mapping.exists():
            print(f"Error: --from-mapping file not found: {args.from_mapping}", file=sys.stderr)
            sys.exit(1)
        sitemap_paths = []  # ignored when from_mapping is set
    else:
        if args.sitemaps:
            sitemap_paths = args.sitemaps
        else:
            sitemap_paths = [base / "saigoncenter_en_sitemap.xml"]
        if not sitemap_paths:
            print("No sitemap files. Use sitemap path(s) or --from-mapping FILE.", file=sys.stderr)
            sys.exit(1)

    crawl(
        sitemap_paths,
        delay_sec=args.delay,
        limit=args.limit,
        skip=args.skip,
        out_path=args.out,
        csv_path=args.csv,
        workers=args.workers,
        category_mapping_path=args.category_mapping,
        from_mapping_path=args.from_mapping,
    )


if __name__ == "__main__":
    main()
