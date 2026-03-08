"""
Shared product extraction and country detection.
Used by extract_one_product.py and crawl_products.py.
"""

import re
import unicodedata
from typing import Any

# CSS selectors for product attribute blocks (body text)
SELECTOR_DESCRIPTION = "#description .product.attribute.description .value"
SELECTOR_INGREDIENTS = "#ingredients .product.attribute.ingredients .value"
SELECTOR_INSTRUCTION_FOR_USE = "#instruction_for_use .product.attribute.instruction_for_use .value"
SELECTOR_STORAGE_INSTRUCTIONS = "#storage_instructions .product.attribute.storage_instructions .value"


def normalize_accents(s: str) -> str:
    """Strip accents for matching (e.g. 'Bến Tre' -> 'Ben Tre')."""
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def get_meta_content(page, property_value: str) -> str | None:
    """Get content attribute of meta[property="..."] or None if missing."""
    loc = page.locator(f'meta[property="{property_value}"]').first
    value = loc.get_attribute("content")
    return value.strip() if value else None


def get_body_text(page, selector: str) -> str | None:
    """Get trimmed text content of first match, or None if missing."""
    loc = page.locator(selector).first
    text = loc.text_content()
    return text.strip() if text else None


def normalize_price(value: str | None) -> int | float | str | None:
    """Return numeric price if possible, else original string. Returns None for empty input."""
    if value is None or value == "":
        return None
    cleaned = re.sub(r"\s+", "", value)
    try:
        return int(cleaned)
    except ValueError:
        pass
    try:
        return float(cleaned.replace(",", "."))
    except ValueError:
        return value


def _build_product_dict(
    name: str | None,
    description_meta: str | None,
    price_meta: str | None,
    currency: str | None,
    description_body: str | None,
    ingredients: str | None,
    instruction_for_use: str | None,
    storage_instructions: str | None,
) -> dict[str, Any]:
    """Build product dict from already-fetched values (shared by sync and async extractors)."""
    description = description_body if description_body else description_meta
    product: dict[str, Any] = {
        "name": name,
        "price": normalize_price(price_meta) if price_meta else None,
        "currency": currency,
        "description": description,
    }
    if ingredients is not None:
        product["ingredients"] = ingredients
    if instruction_for_use is not None:
        product["instruction_for_use"] = instruction_for_use
    if storage_instructions is not None:
        product["storage_instructions"] = storage_instructions
    return product


def extract_product(page) -> dict:
    """Extract product fields per PRODUCT_CRAWL_PLAN.md."""
    name = get_meta_content(page, "og:title")
    description_meta = get_meta_content(page, "og:description")
    price_meta = get_meta_content(page, "product:price:amount")
    currency = get_meta_content(page, "product:price:currency")
    description_body = get_body_text(page, SELECTOR_DESCRIPTION)
    ingredients = get_body_text(page, SELECTOR_INGREDIENTS)
    instruction_for_use = get_body_text(page, SELECTOR_INSTRUCTION_FOR_USE)
    storage_instructions = get_body_text(page, SELECTOR_STORAGE_INSTRUCTIONS)
    return _build_product_dict(
        name, description_meta, price_meta, currency,
        description_body, ingredients, instruction_for_use, storage_instructions,
    )


# --- Async versions for concurrent crawl (playwright.async_api) ---


async def get_meta_content_async(
    page, property_value: str, timeout_ms: int | None = None
) -> str | None:
    """Async: get content attribute of meta[property="..."]. Optional timeout_ms to avoid long waits."""
    loc = page.locator(f'meta[property="{property_value}"]').first
    value = await loc.get_attribute("content")
    return value.strip() if value else None


async def get_body_text_async(page, selector: str, timeout_ms: int = 5000) -> str | None:
    """Async: get trimmed text content of first match. Returns None if missing or timeout."""
    try:
        loc = page.locator(selector).first
        text = await loc.text_content(timeout=timeout_ms)
        return text.strip() if text else None
    except Exception:
        return None


async def extract_product_async(page) -> dict:
    """Async: extract product fields (same schema as extract_product)."""
    name = await get_meta_content_async(page, "og:title")
    description_meta = await get_meta_content_async(page, "og:description")
    price_meta = await get_meta_content_async(page, "product:price:amount")
    currency = await get_meta_content_async(page, "product:price:currency")
    description_body = await get_body_text_async(page, SELECTOR_DESCRIPTION)
    ingredients = await get_body_text_async(page, SELECTOR_INGREDIENTS)
    instruction_for_use = await get_body_text_async(page, SELECTOR_INSTRUCTION_FOR_USE)
    storage_instructions = await get_body_text_async(page, SELECTOR_STORAGE_INSTRUCTIONS)
    return _build_product_dict(
        name, description_meta, price_meta, currency,
        description_body, ingredients, instruction_for_use, storage_instructions,
    )


def _get_place_names_from_pycountry() -> tuple[list[str], list[str]]:
    try:
        import pycountry
    except ImportError:
        return [], []
    countries = set()
    for c in pycountry.countries:
        if c.name:
            countries.add(c.name)
        if getattr(c, "official_name", None) and c.official_name != c.name:
            countries.add(c.official_name)
    subdivisions = set()
    for s in pycountry.subdivisions:
        if s.name:
            subdivisions.add(s.name)
    sort_by_len = lambda x: sorted(x, key=len, reverse=True)
    return sort_by_len(countries), sort_by_len(subdivisions)


def _get_stopwords() -> frozenset[str]:
    try:
        import nltk
        nltk.data.find("corpora/stopwords")
    except (ImportError, LookupError):
        try:
            import nltk
            nltk.download("stopwords", quiet=True)
        except Exception:
            return frozenset()
    try:
        from nltk.corpus import stopwords
        return frozenset(stopwords.words("english"))
    except Exception:
        return frozenset()


class _CountryExtractor:
    """Lazy-loaded caches for country/place extraction from product text."""

    def __init__(self) -> None:
        self._stopwords: frozenset[str] | None = None
        self._place_names: tuple[list[str], list[str]] | None = None

    def _first_place_in_text(self, text: str, place_names: list[str]) -> str:
        if self._stopwords is None:
            self._stopwords = _get_stopwords()
        text_norm = normalize_accents(text)
        for name in place_names:
            if not name:
                continue
            name_norm = normalize_accents(name)
            if name_norm.lower() in self._stopwords:
                continue
            if re.search(r"\b" + re.escape(name_norm) + r"\b", text_norm, re.IGNORECASE):
                return name
        return ""

    def extract_country(self, product: dict) -> str:
        """Country/place from name+description+ingredients (pycountry then spaCy). Returns English/ASCII form or ""."""
        parts = [
            product.get("name") or "",
            product.get("description") or "",
            product.get("ingredients") or "",
        ]
        text = " ".join(p for p in parts if p).strip()
        if not text:
            return ""

        if self._place_names is None:
            self._place_names = _get_place_names_from_pycountry()
        country_names, subdivision_names = self._place_names
        found = self._first_place_in_text(text, country_names)
        if found:
            return normalize_accents(found)
        found = self._first_place_in_text(text, subdivision_names)
        if found:
            return normalize_accents(found)

        try:
            import spacy
            nlp = spacy.load("en_core_web_sm")
            doc = nlp(text)
            for ent in doc.ents:
                if ent.label_ in ("GPE", "LOC"):
                    return normalize_accents(ent.text.strip() or "")
        except (ImportError, OSError):
            pass
        return ""


_default_country_extractor = _CountryExtractor()


def extract_country(product: dict) -> str:
    """Country/place from name+description+ingredients. Returns English/ASCII form or "" (backward-compatible wrapper)."""
    return _default_country_extractor.extract_country(product)


def apply_quantity_to_product(product: dict) -> None:
    """Set quantity_value and quantity_unit on product from product name. Mutates product in place."""
    from utils.quantity_utils import parse_quantity_from_text

    parsed = parse_quantity_from_text(product.get("name") or "")
    product["quantity_value"] = parsed.get("quantity_value")
    product["quantity_unit"] = parsed.get("quantity_unit") or ""
