"""
Export crawled products to CSV (and optionally other formats).
Reused by crawl_products.py and crawl_products_headed.py.
"""

import csv
from pathlib import Path


# Column order for product CSV; must match product schema.
CSV_FIELDNAMES = (
    "name",
    "price",
    "currency",
    "description",
    "ingredients",
    "instruction_for_use",
    "storage_instructions",
    "country",
    "category",
    "quantity_value",
    "quantity_unit",
    "url",
)


def _csv_cell(row: dict, key: str) -> str:
    """One cell for CSV: empty string if value is None, else str(value)."""
    val = row.get(key)
    return "" if val is None else str(val)


def write_products_csv(products: list[dict], path: Path) -> None:
    """Write product list to CSV with UTF-8 and consistent column order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=CSV_FIELDNAMES,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )
        w.writeheader()
        for row in products:
            out = {k: _csv_cell(row, k) for k in CSV_FIELDNAMES}
            w.writerow(out)
