#!/usr/bin/env python3
"""
Full product crawl: parse sitemap → fetch each URL with Playwright → extract product + country → products.json.

Usage:
  python crawl_products.py [sitemap.xml ...]
  python crawl_products.py --workers 3 --delay 8 --limit 100 sitemap.xml

Options:
  --workers N     Concurrent pages (default: 1). Higher = faster but more block risk.
  --delay SECS    Seconds between requests per worker (default: 5).
  --limit N       Stop after N product URLs (default: no limit).
  --skip N        Skip first N URLs from sitemap (for batching: run 1 --limit 50, run 2 --skip 50 --limit 50).
  --out FILE      Output JSON path (default: products.json).
  --csv FILE      Also write products to CSV (optional).
  --proxy URL     Proxy server (e.g. http://host:port or http://user:pass@host:port).
  --use-chrome    Use installed Chrome instead of Playwright Chromium (often less detected).
  --cookies FILE  JSON file of cookies from a browser session that passed Cloudflare (see README).
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
from utils import is_product_url, load_cookies, parse_sitemap, write_products_csv

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


async def _delay_async(delay_sec: float) -> None:
    """Variable delay so requests aren’t at a fixed interval (can help avoid session/challenge)."""
    # Range 0.3x–1.0x of delay_sec (e.g. --delay 8 → 2.4–8s) so we’re not always waiting the full time
    lo = max(1.0, delay_sec * 0.3)
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
    out_path: Path,
    sitemap_paths: list[Path],
    proxy_url: str | None,
    use_chrome: bool,
    workers: int,
    cookies_path: Path | None = None,
    csv_path: Path | None = None,
) -> None:
    from playwright.async_api import async_playwright

    products: list[dict] = []
    url_index = [0]
    product_count = [0]
    lock = asyncio.Lock()
    cookies = load_cookies(cookies_path)

    async with async_playwright() as p:
        launch_opts: dict = {"headless": True}
        if use_chrome:
            launch_opts["channel"] = "chrome"
        browser = await p.chromium.launch(**launch_opts)
        ctx_opts: dict = {"user_agent": USER_AGENT}
        if proxy_url:
            ctx_opts["proxy"] = {"server": proxy_url}
        # Realistic viewport/locale so the site is less likely to treat us as a bot
        ctx_opts["viewport"] = {"width": 1920, "height": 1080}
        ctx_opts["locale"] = "en-US"
        context = await browser.new_context(**ctx_opts)
        if cookies:
            try:
                await context.add_cookies(cookies)
                print(f"Loaded {len(cookies)} cookie(s) from {cookies_path}", file=sys.stderr)
            except Exception as e:
                print(f"Warning: could not add cookies: {e}", file=sys.stderr)

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
                load_task = asyncio.create_task(
                    _load_page_and_get_og_type(page, url)
                )
                try:
                    og_type = await asyncio.wait_for(
                        load_task,
                        timeout=PER_URL_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    load_task.cancel()
                    await page.close()  # close first so in-flight locator wait fails and task can finish
                    try:
                        await load_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    print(f"  Skip (timeout) ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                    print(f"  (delay ~{delay_sec:.0f}s)", file=sys.stderr)
                    await _delay_async(delay_sec)
                    continue
                except Exception as e:
                    print(f"  Skip (load error): {e} ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                    await page.close()
                    print(f"  (delay ~{delay_sec:.0f}s)", file=sys.stderr)
                    await _delay_async(delay_sec)
                    continue

                if og_type is None:
                    print(f"  Skip (no og:type) ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                    await page.close()
                    print(f"  (delay ~{delay_sec:.0f}s)", file=sys.stderr)
                    await _delay_async(delay_sec)
                    continue
                if og_type != "product":
                    print(f"  Skip (og:type={og_type}) ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
                    await page.close()
                    print(f"  (delay ~{delay_sec:.0f}s)", file=sys.stderr)
                    await _delay_async(delay_sec)
                    continue

                product = await extract_product_async(page)
                await page.close()
                product["country"] = extract_country(product)
                elapsed = time.perf_counter() - t0

                async with lock:
                    products.append(product)
                    product_count[0] += 1
                print(f"  -> product: {product.get('name', '')[:50]}... ({elapsed:.1f}s)", file=sys.stderr)
                print(f"  (delay ~{delay_sec:.0f}s)", file=sys.stderr)
                await _delay_async(delay_sec)

        await asyncio.gather(*[fetch_one(w) for w in range(workers)])
        await browser.close()

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


def crawl(
    sitemap_paths: list[Path],
    *,
    delay_sec: float = 5,
    limit: int | None = None,
    skip: int = 0,
    out_path: Path,
    proxy_url: str | None = None,
    use_chrome: bool = False,
    workers: int = 1,
    cookies_path: Path | None = None,
    csv_path: Path | None = None,
) -> None:
    # Step 1: Parse sitemap(s) → list of URLs
    all_urls: list[str] = []
    for path in sitemap_paths:
        if not path.exists():
            print(f"Warning: sitemap not found: {path}", file=sys.stderr)
            continue
        urls = parse_sitemap(path)
        all_urls.extend(urls)
        print(f"Parsed {path.name}: {len(urls)} URLs", file=sys.stderr)
    all_urls = list(dict.fromkeys(all_urls))
    # Only keep URLs that have a path after .../hcm-taka/ (those are likely product/category pages)
    before = len(all_urls)
    all_urls = [u for u in all_urls if is_product_url(u)]
    if len(all_urls) < before:
        print(f"Filtered to product URLs (one segment after hcm-taka/): {len(all_urls)} (dropped {before - len(all_urls)})", file=sys.stderr)
    if skip > 0:
        all_urls = all_urls[skip:]
        print(f"Skipped first {skip} URLs; {len(all_urls)} left to check", file=sys.stderr)
    print(f"Total URLs to check: {len(all_urls)} (workers={workers})", file=sys.stderr)
    if not use_chrome:
        print("Tip: if many timeouts, try --use-chrome (site may block or slow down headless browser).", file=sys.stderr)

    asyncio.run(
        _crawl_async(
            all_urls,
            delay_sec=delay_sec,
            limit=limit,
            out_path=out_path,
            sitemap_paths=sitemap_paths,
            proxy_url=proxy_url,
            use_chrome=use_chrome,
            workers=workers,
            cookies_path=cookies_path,
            csv_path=csv_path,
        )
    )


def main():
    parser = argparse.ArgumentParser(description="Crawl product pages from sitemap(s).")
    parser.add_argument(
        "sitemaps",
        nargs="*",
        type=Path,
        help="Sitemap XML file(s). Default: saigoncenter_en_sitemap.xml",
    )
    parser.add_argument("--workers", type=int, default=1, help="Concurrent pages (default 1; higher = faster, more block risk)")
    parser.add_argument("--delay", type=float, default=5, help="Seconds between requests per worker (default 5)")
    parser.add_argument("--limit", type=int, default=None, help="Max number of products to extract")
    parser.add_argument("--skip", type=int, default=0, help="Skip first N URLs (for batching: run 2 use --skip 50 --limit 50)")
    parser.add_argument("--out", type=Path, default=Path("products.json"), help="Output JSON path")
    parser.add_argument("--csv", type=Path, default=None, metavar="FILE", help="Also write products to CSV")
    parser.add_argument("--proxy", type=str, default=None, help="Proxy URL (e.g. http://host:port)")
    parser.add_argument("--use-chrome", action="store_true", help="Use installed Chrome (less detected than Chromium)")
    parser.add_argument("--cookies", type=Path, default=None, metavar="FILE", help="JSON file of cookies from a session that passed Cloudflare")
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
        proxy_url=args.proxy,
        use_chrome=args.use_chrome,
        workers=args.workers,
        cookies_path=args.cookies,
        csv_path=args.csv,
    )


if __name__ == "__main__":
    main()
