"""
Parse quantity and unit from product text (e.g. "500ml", "1.5L", "1000g").
Used to derive quantity_value and quantity_unit from product name for JSON/CSV.
"""

import re
from typing import TypedDict


class QuantityResult(TypedDict):
    """Result of parse_quantity_from_text (always has both keys)."""
    quantity_value: int | float | None
    quantity_unit: str


# Supported units: longer forms first so "kg" matches before "g".
QUANTITY_UNITS = ("kg", "ml", "L", "g", "pcs")

# Regex: number (int or decimal) + optional space + unit (word boundary).
# Case-insensitive for unit; we normalize to canonical form below.
_QUANTITY_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(" + "|".join(re.escape(u) for u in QUANTITY_UNITS) + r")\b",
    re.IGNORECASE,
)

# Canonical unit form for output (e.g. "L" not "l", "ml", "g", "kg", "pcs").
_UNIT_CANONICAL = {u.lower(): u if u != "L" else "L" for u in QUANTITY_UNITS}


def parse_quantity_from_text(text: str) -> QuantityResult:
    """
    Extract first quantity + unit from text (e.g. product name).

    Returns QuantityResult with:
      - quantity_value: int or float, or None if no match
      - quantity_unit: str, one of "g", "kg", "ml", "L", "pcs", or "" if no match

    Handles: g, kg, ml, L, pcs. Uses first match in text.
    """
    if not text or not text.strip():
        return {"quantity_value": None, "quantity_unit": ""}
    m = _QUANTITY_PATTERN.search(text)
    if not m:
        return {"quantity_value": None, "quantity_unit": ""}
    raw_value, raw_unit = m.group(1), m.group(2)
    try:
        value = int(raw_value) if "." not in raw_value else float(raw_value)
    except ValueError:
        return {"quantity_value": None, "quantity_unit": ""}
    unit = _UNIT_CANONICAL.get(raw_unit.lower(), raw_unit)
    return {"quantity_value": value, "quantity_unit": unit}
