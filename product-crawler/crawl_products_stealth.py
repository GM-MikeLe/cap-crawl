#!/usr/bin/env python3
"""
Stealth-only product crawl: minimal setup for speed.

- Playwright stealth only (no cookies, no saved session, no headed)
- Minimum delay between requests
- Two-phase flow:
  1. Phase 1: Crawl URLs, extract product info (no country), save to JSON
  2. Phase 2: Add country from JSON (batch), write final JSON and CSV

Usage:
  python crawl_products_stealth.py --limit 10 saigoncenter_en_sitemap.xml --out products.json --csv products.csv

Options:
  --workers N   Concurrent pages (default 1)
  --delay SECS  Seconds between requests (default 0.5, min ~0.3)
  --limit N     Max products to extract
  --skip N      Skip first N URLs
  --out FILE    Output JSON path (default products_stealth.json)
  --csv FILE    Also write CSV in final step
"""

import argparse
import asyncio
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from product_extract import (
    extract_country,
    extract_product_async,
    get_meta_content_async,
)
from config import (
    CONTENT_READY_SELECTOR,
    FALLBACK_SLEEP_AFTER_SELECTOR_TIMEOUT_SEC,
    PAGE_LOAD_TIMEOUT_MS,
    PER_URL_TIMEOUT_SEC,
    SELECTOR_WAIT_TIMEOUT_MS,
)
from utils import is_product_url, parse_sitemap, write_products_csv

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


async def _delay_async(delay_sec: float) -> None:
    """Minimum delay (floor 0.3s) for speed."""
    lo = max(0.3, delay_sec * 0.3)
    hi = max(lo, delay_sec * 1.0)
    await asyncio.sleep(random.uniform(lo, hi))


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
            "user_agent": USER_AGENT,
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
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
                    await _delay_async(delay_sec)
                    continue
                except Exception as e:
                    print(f"  Skip (load error): {e} ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                    await page.close()
                    await _delay_async(delay_sec)
                    continue

                if og_type is None:
                    print(f"  Skip (no og:type) ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                    await page.close()
                    await _delay_async(delay_sec)
                    continue
                if og_type != "product":
                    print(f"  Skip (og:type={og_type}) ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                    await page.close()
                    await _delay_async(delay_sec)
                    continue

                product = await extract_product_async(page)
                await page.close()
                # Skip products without details (only name, no description)
                if not (product.get("description") or "").strip():
                    print(f"  Skip (no details) ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                    await _delay_async(delay_sec)
                    continue
                # No country here - phase 2 adds it
                elapsed = time.perf_counter() - t0

                async with lock:
                    products.append(product)
                    product_count[0] += 1
                print(f"  -> product: {product.get('name', '')[:50]}... ({elapsed:.1f}s)", file=sys.stderr)
                await _delay_async(delay_sec)

        await asyncio.gather(*[fetch_one(w) for w in range(workers)])
        await browser.close()


def add_countries_to_products(products: list[dict]) -> None:
    """Phase 2: Add country to each product (in-place)."""
    for p in products:
        p["country"] = extract_country(p)


def crawl(
    sitemap_paths: list[Path],
    *,
    delay_sec: float = 0.5,
    limit: int | None = None,
    skip: int = 0,
    out_path: Path,
    csv_path: Path | None = None,
    workers: int = 1,
) -> None:
    all_urls: list[str] = []
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

    # Phase 2: add country (batch, no network)
    print(f"Phase 2: extract country for {len(products)} products", file=sys.stderr)
    add_countries_to_products(products)

    out = {
        "source_sitemaps": [str(p) for p in sitemap_paths],
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
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers must be >= 1")
    if args.skip < 0:
        parser.error("--skip must be >= 0")

    base = Path(__file__).parent
    if args.sitemaps:
        sitemap_paths = args.sitemaps
    else:
        sitemap_paths = [base / "saigoncenter_en_sitemap.xml"]
    if not sitemap_paths:
        print("No sitemap files.", file=sys.stderr)
        sys.exit(1)

    crawl(
        sitemap_paths,
        delay_sec=args.delay,
        limit=args.limit,
        skip=args.skip,
        out_path=args.out,
        csv_path=args.csv,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
