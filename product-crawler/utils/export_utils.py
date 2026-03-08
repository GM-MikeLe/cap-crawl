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
    "quantity_value",
    "quantity_unit",
)


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
            out = {k: "" if row.get(k) is None else str(row.get(k)) for k in CSV_FIELDNAMES}
            w.writerow(out)
