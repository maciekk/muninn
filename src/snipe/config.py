from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from urllib.parse import urlparse

import yaml

DEFAULT_REFRESH_SECONDS = 2 * 60 * 60
DEFAULT_RETRY_SECONDS = 60 * 60
_DURATION = re.compile(r"^(?P<value>[1-9]\d*)\s*(?P<unit>[mhd])$")


@dataclass(frozen=True)
class Retailer:
    name: str
    url: str
    refresh_seconds: int
    price_selector: str | None = None
    fetcher: str = "http"
    retry_seconds: int = DEFAULT_RETRY_SECONDS
    seller: str | None = None
    seller_type: str | None = None


@dataclass(frozen=True)
class Product:
    name: str
    target_price: Decimal | None
    retailers: tuple[Retailer, ...]
    notes: tuple[str, ...] = ()
    desirability: Decimal | None = None


def parse_duration(value: object) -> int:
    if not isinstance(value, str):
        raise ValueError("duration must be a string such as '2h'")
    match = _DURATION.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid duration {value!r}; use a positive number followed by m, h, or d")
    return int(match["value"]) * {"m": 60, "h": 3600, "d": 86400}[match["unit"]]


def _price(value: object, label: str) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not result.is_finite():
        raise ValueError(f"{label} must be finite")
    if result < 0:
        raise ValueError(f"{label} cannot be negative")
    return result


def load(path: Path) -> tuple[Product, ...]:
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise ValueError(f"configuration not found: {path}")
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("configuration must be a YAML mapping")
    # A product file is a single product mapping. The legacy products-list shape
    # remains readable so existing season projects can be migrated gradually.
    raw_products = data["products"] if isinstance(data.get("products"), list) else [data]

    products: list[Product] = []
    for index, raw_product in enumerate(raw_products, 1):
        if not isinstance(raw_product, dict) or not isinstance(raw_product.get("name"), str):
            raise ValueError(f"products[{index}] needs a name")
        product_delay = parse_duration(raw_product.get("refresh_after", "2h"))
        product_retry_delay = parse_duration(raw_product.get("retry_after", "1h"))
        raw_retailers = raw_product.get("retailers")
        if not isinstance(raw_retailers, list):
            raise ValueError(f"products[{index}].retailers must be a list (use [] for a candidate awaiting links)")
        retailers: list[Retailer] = []
        for r_index, raw in enumerate(raw_retailers, 1):
            if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not isinstance(raw.get("url"), str):
                raise ValueError(f"products[{index}].retailers[{r_index}] needs name and url")
            parsed = urlparse(raw["url"])
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"invalid URL: {raw['url']}")
            selector = raw.get("price_selector")
            if selector is not None and not isinstance(selector, str):
                raise ValueError("price_selector must be a string")
            fetcher = raw.get("fetcher", "http")
            if fetcher not in ("http", "browser", "interactive"):
                raise ValueError("fetcher must be http, browser, or interactive")
            seller = raw.get("seller")
            if seller is not None and (not isinstance(seller, str) or not seller.strip()):
                raise ValueError("seller must be a non-empty string")
            seller_type = raw.get("seller_type")
            if seller_type not in (None, "first_party", "third_party"):
                raise ValueError("seller_type must be first_party or third_party")
            retailers.append(Retailer(
                raw["name"], raw["url"],
                parse_duration(raw["refresh_after"]) if "refresh_after" in raw else product_delay,
                selector, fetcher,
                parse_duration(raw["retry_after"]) if "retry_after" in raw else product_retry_delay,
                seller.strip() if seller is not None else None, seller_type,
            ))
        raw_notes = raw_product.get("notes", [])
        if isinstance(raw_notes, str):
            notes = (raw_notes,)
        elif isinstance(raw_notes, list) and all(isinstance(note, str) for note in raw_notes):
            notes = tuple(raw_notes)
        else:
            raise ValueError(f"products[{index}].notes must be a string or list of strings")
        rating = _price(raw_product.get("desirability"), f"products[{index}].desirability")
        if rating is not None and rating > 10:
            raise ValueError(f"products[{index}].desirability must be between 0 and 10")
        products.append(Product(raw_product["name"], _price(raw_product.get("target_price"), f"products[{index}].target_price"), tuple(retailers), notes, rating))
    return tuple(products)
