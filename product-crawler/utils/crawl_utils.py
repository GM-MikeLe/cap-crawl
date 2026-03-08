"""
Shared crawl helpers: async delay between requests.
Used by crawl_products_stealth.py and build_category_product_mapping.py.
"""

import asyncio
import random

from config import DELAY_FACTOR_HI, DELAY_FACTOR_LO, DELAY_MIN_SEC


async def delay_async(delay_sec: float) -> None:
    """Sleep a random duration in [delay_sec * LO, delay_sec * HI], with floor DELAY_MIN_SEC."""
    lo = max(DELAY_MIN_SEC, delay_sec * DELAY_FACTOR_LO)
    hi = max(lo, delay_sec * DELAY_FACTOR_HI)
    await asyncio.sleep(random.uniform(lo, hi))
