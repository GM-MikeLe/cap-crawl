"""
Load and normalize cookies from JSON for Playwright.
Reused by crawl_products.py and crawl_products_headed.py.
"""

import json
import sys
from pathlib import Path


def _normalize_cookie_for_playwright(c: dict) -> dict:
    """Convert exported cookie (e.g. EditThisCookie) to Playwright format. sameSite must be Strict|Lax|None."""
    out = {
        "name": c["name"],
        "value": c["value"],
        "domain": c.get("domain", ""),
        "path": c.get("path", "/"),
    }
    if "expirationDate" in c:
        out["expires"] = int(c["expirationDate"])
    if "httpOnly" in c:
        out["httpOnly"] = bool(c["httpOnly"])
    if "secure" in c:
        out["secure"] = bool(c["secure"])
    same = (c.get("sameSite") or "Lax").lower()
    if same in ("strict",):
        out["sameSite"] = "Strict"
    elif same in ("none", "no_restriction"):
        out["sameSite"] = "None"
    else:
        out["sameSite"] = "Lax"
    return out


def load_cookies(path: Path | None) -> list[dict]:
    """Load cookies from a JSON file. Accepts array or {cookies: array}. Normalizes sameSite for Playwright."""
    if path is None or not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            cookies = raw
        elif isinstance(raw, dict) and "cookies" in raw:
            cookies = raw["cookies"]
        else:
            return []
        return [_normalize_cookie_for_playwright(c) for c in cookies if c.get("name") is not None]
    except (json.JSONDecodeError, OSError, KeyError) as e:
        print(f"Warning: could not load cookies from {path}: {e}", file=sys.stderr)
        return []
