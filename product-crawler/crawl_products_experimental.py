#!/usr/bin/env python3
"""
Experimental product crawl: same as crawl_products_headed.py but adds strategies
to try to reduce Cloudflare CAPTCHA blocks.

**Stealth only (saved session commented out for testing)**

  # Crawl with stealth only (headless)
  python crawl_products_experimental.py --limit 10 saigoncenter_en_sitemap.xml --out batch_exp.json

# [Saved session approach - commented to test stealth only]
# **Option 4: Solve once, reuse session**
#   1. Run --solve-once state.json: Opens browser, solve CAPTCHA, save session
#   2. Run with --storage-state state.json: Crawl using that session
#
# Usage (commented):
#   python crawl_products_experimental.py --solve-once state.json
#   python crawl_products_experimental.py --storage-state state.json --headed --limit 10 ...

Other options:
  --warmup-url [URL]  Load this URL first before products
  --restart-every N   Restart browser every N URLs
  playwright-stealth  Applied automatically (masks automation signals)
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
    apply_quantity_to_product,
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

DEFAULT_WARMUP_URL = "https://shop.annam-gourmet.com/hcm-taka/"


# [Saved session - commented to test stealth only]
# async def _solve_once_async(
#     warmup_url: str,
#     state_path: Path,
#     *,
#     proxy_url: str | None,
#     use_chrome: bool,
# ) -> None:
#     """Open headed browser, load URL, wait for user to solve CAPTCHA, save storage state."""
#     from playwright.async_api import async_playwright
#     from playwright_stealth import Stealth
#
#     async with Stealth().use_async(async_playwright()) as p:
#         launch_opts: dict = {"headless": False}
#         if use_chrome:
#             launch_opts["channel"] = "chrome"
#         browser = await p.chromium.launch(**launch_opts)
#         ctx_opts: dict = {"user_agent": USER_AGENT}
#         if proxy_url:
#             ctx_opts["proxy"] = {"server": proxy_url}
#         ctx_opts["viewport"] = {"width": 1920, "height": 1080}
#         ctx_opts["locale"] = "en-US"
#         context = await browser.new_context(**ctx_opts)
#         page = await context.new_page()
#         print(f"Loading {warmup_url} ...", file=sys.stderr)
#         await page.goto(warmup_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
#         print("Solve the CAPTCHA in the browser window.", file=sys.stderr)
#         print("When you see the real page (not the challenge), press Enter here.", file=sys.stderr)
#         loop = asyncio.get_event_loop()
#         await loop.run_in_executor(None, lambda: input())
#         state_path.parent.mkdir(parents=True, exist_ok=True)
#         await context.storage_state(path=str(state_path))
#         await browser.close()
#         print(f"Saved session to {state_path}", file=sys.stderr)
#
#
# def solve_once(
#     state_path: Path,
#     *,
#     warmup_url: str = DEFAULT_WARMUP_URL,
#     proxy_url: str | None = None,
#     use_chrome: bool = False,
# ) -> None:
#     """Interactive: open browser, user solves CAPTCHA, save storage state for reuse."""
#     asyncio.run(
#         _solve_once_async(
#             warmup_url,
#             state_path,
#             proxy_url=proxy_url,
#             use_chrome=use_chrome,
#         )
#     )


async def _delay_async(delay_sec: float) -> None:
    """Variable delay so requests aren't at a fixed interval (can help avoid session/challenge)."""
    lo = max(1.0, delay_sec * 0.3)
    hi = max(lo, delay_sec * 1.0)
    await asyncio.sleep(random.uniform(lo, hi))


async def _warmup_session(page, warmup_url: str) -> None:
    """Load warmup URL (category/homepage) to establish session before product fetches."""
    print(f"Warming up: {warmup_url}", file=sys.stderr)
    await page.goto(warmup_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
    try:
        await page.wait_for_selector(
            CONTENT_READY_SELECTOR,
            timeout=SELECTOR_WAIT_TIMEOUT_MS,
            state="attached",
        )
    except Exception:
        await asyncio.sleep(FALLBACK_SLEEP_AFTER_SELECTOR_TIMEOUT_SEC)
    print("  Warmup done.", file=sys.stderr)


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


async def _crawl_batch_async(
    batch_urls: list[str],
    *,
    delay_sec: float,
    limit: int | None,
    products: list,
    product_count: list,
    sitemap_paths: list[Path],
    proxy_url: str | None,
    use_chrome: bool,
    workers: int,
    headed: bool,
    cookies: list,
    cookies_path: Path | None,
    warmup_url: str | None,
    storage_state_path: Path | None = None,
) -> None:
    """Process one batch of URLs. Launches browser, optionally warmup, then fetches products."""
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    url_index = [0]
    lock = asyncio.Lock()

    async with Stealth().use_async(async_playwright()) as p:
        launch_opts: dict = {"headless": True}  # headless on; ignore --headed
        if use_chrome:
            launch_opts["channel"] = "chrome"
        browser = await p.chromium.launch(**launch_opts)
        ctx_opts: dict = {"user_agent": USER_AGENT}
        if proxy_url:
            ctx_opts["proxy"] = {"server": proxy_url}
        ctx_opts["viewport"] = {"width": 1920, "height": 1080}
        ctx_opts["locale"] = "en-US"
        # [Saved session - commented to test stealth only]
        # if storage_state_path and storage_state_path.exists():
        #     ctx_opts["storage_state"] = str(storage_state_path)
        #     print(f"Using saved session from {storage_state_path}", file=sys.stderr)
        context = await browser.new_context(**ctx_opts)
        if cookies:
            try:
                await context.add_cookies(cookies)
                print(f"Loaded {len(cookies)} cookie(s) from {cookies_path}", file=sys.stderr)
            except Exception as e:
                print(f"Warning: could not add cookies: {e}", file=sys.stderr)

        hub_page = None
        if warmup_url:
            hub_page = await context.new_page()
            try:
                await _warmup_session(hub_page, warmup_url)
            except Exception as e:
                print(f"Warning: warmup failed: {e}", file=sys.stderr)
                await hub_page.close()
                hub_page = None

        async def fetch_one(_worker_id: int) -> None:
            while True:
                async with lock:
                    if limit is not None and product_count[0] >= limit:
                        return
                    if url_index[0] >= len(batch_urls):
                        return
                    url = batch_urls[url_index[0]]
                    pos = url_index[0] + 1
                    url_index[0] += 1

                print(f"[{pos}/{len(batch_urls)}] {url}", file=sys.stderr)
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
                    await page.close()
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
                apply_quantity_to_product(product)
                elapsed = time.perf_counter() - t0

                async with lock:
                    products.append(product)
                    product_count[0] += 1
                print(f"  -> product: {product.get('name', '')[:50]}... ({elapsed:.1f}s)", file=sys.stderr)
                print(f"  (delay ~{delay_sec:.0f}s)", file=sys.stderr)
                await _delay_async(delay_sec)

        await asyncio.gather(*[fetch_one(w) for w in range(workers)])
        if hub_page:
            await hub_page.close()
        await browser.close()


def crawl(
    sitemap_paths: list[Path],
    *,
    delay_sec: float = 2,
    limit: int | None = None,
    skip: int = 0,
    out_path: Path,
    proxy_url: str | None = None,
    use_chrome: bool = False,
    workers: int = 1,
    headed: bool = False,
    cookies_path: Path | None = None,
    csv_path: Path | None = None,
    warmup_url: str | None = None,
    restart_every: int | None = None,
    storage_state_path: Path | None = None,
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
        print(f"Filtered to product URLs (one segment after hcm-taka/): {len(all_urls)} (dropped {before - len(all_urls)})", file=sys.stderr)
    if skip > 0:
        all_urls = all_urls[skip:]
        print(f"Skipped first {skip} URLs; {len(all_urls)} left to check", file=sys.stderr)
    print(f"Total URLs to check: {len(all_urls)} (workers={workers})", file=sys.stderr)

    # [Saved session - commented to test stealth only]
    # if storage_state_path and storage_state_path.exists():
    #     print(f"Option 4: using saved session from {storage_state_path}", file=sys.stderr)
    if warmup_url:
        print(f"Experimental: warmup URL enabled ({warmup_url})", file=sys.stderr)
    if restart_every is not None:
        print(f"Experimental: restart browser every {restart_every} URLs", file=sys.stderr)
    print("Running headless.", file=sys.stderr)
    if not use_chrome:
        print("Tip: if many timeouts, try --use-chrome.", file=sys.stderr)

    products: list[dict] = []
    product_count = [0]
    cookies = load_cookies(cookies_path)

    batches: list[list[str]] = []
    if restart_every is not None and restart_every > 0:
        for i in range(0, len(all_urls), restart_every):
            batches.append(all_urls[i:i + restart_every])
    else:
        batches = [all_urls]

    for i, batch in enumerate(batches):
        if limit is not None and product_count[0] >= limit:
            break
        if len(batches) > 1:
            print(f"--- Batch {i + 1}/{len(batches)} ({len(batch)} URLs) ---", file=sys.stderr)
        asyncio.run(
            _crawl_batch_async(
                batch,
                delay_sec=delay_sec,
                limit=limit,
                products=products,
                product_count=product_count,
                sitemap_paths=sitemap_paths,
                proxy_url=proxy_url,
                use_chrome=use_chrome,
                workers=workers,
                headed=headed,
                cookies=cookies,
                cookies_path=cookies_path,
                warmup_url=warmup_url,
                storage_state_path=storage_state_path,
            )
        )

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
        description="Experimental crawl: solve-once (Option 4), warmup, restart-every.",
    )
    parser.add_argument(
        "sitemaps",
        nargs="*",
        type=Path,
        help="Sitemap XML file(s). Default: saigoncenter_en_sitemap.xml",
    )
    parser.add_argument("--workers", type=int, default=1, help="Concurrent pages (default 1)")
    parser.add_argument("--delay", type=float, default=2, help="Seconds between requests per worker (default 2)")
    parser.add_argument("--limit", type=int, default=None, help="Max number of products to extract")
    parser.add_argument("--skip", type=int, default=0, help="Skip first N URLs (for batching)")
    parser.add_argument("--out", type=Path, default=Path("products_experimental.json"), help="Output JSON path")
    parser.add_argument("--csv", type=Path, default=None, metavar="FILE", help="Also write products to CSV")
    parser.add_argument("--proxy", type=str, default=None, help="Proxy URL (e.g. http://host:port)")
    parser.add_argument("--use-chrome", action="store_true", help="Use installed Chrome")
    parser.add_argument("--headed", action="store_true", help="Show browser window (headless=False)")
    parser.add_argument("--cookies", type=Path, default=None, metavar="FILE", help="JSON file of cookies from a session that passed Cloudflare")
    parser.add_argument(
        "--warmup-url",
        nargs="?",
        const=DEFAULT_WARMUP_URL,
        default=None,
        metavar="URL",
        help="Load this URL first (redirect link). Default: " + DEFAULT_WARMUP_URL,
    )
    parser.add_argument(
        "--restart-every",
        type=int,
        default=None,
        metavar="N",
        help="Restart browser every N URLs (disconnect agent)",
    )
    parser.add_argument(
        "--solve-once",
        type=Path,
        default=None,
        metavar="FILE",
        help="Interactive: open browser, solve CAPTCHA, save session to FILE. Use with --headed.",
    )
    parser.add_argument(
        "--storage-state",
        type=Path,
        default=None,
        metavar="FILE",
        help="Load saved session from FILE (from --solve-once). Uses full storage state, not just cookies.",
    )
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers must be >= 1")
    if args.skip < 0:
        parser.error("--skip must be >= 0")
    if args.restart_every is not None and args.restart_every < 1:
        parser.error("--restart-every must be >= 1")

    base = Path(__file__).parent
    # [Saved session - commented to test stealth only]
    # if args.solve_once is not None:
    #     solve_once(
    #         args.solve_once,
    #         warmup_url=args.warmup_url or DEFAULT_WARMUP_URL,
    #         proxy_url=args.proxy,
    #         use_chrome=args.use_chrome,
    #     )
    #     return

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
        headed=args.headed,
        cookies_path=args.cookies,
        csv_path=args.csv,
        warmup_url=args.warmup_url,
        restart_every=args.restart_every,
        storage_state_path=args.storage_state,
    )


if __name__ == "__main__":
    main()
