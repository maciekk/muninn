from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

_PRICE = re.compile(r"(?:CAD\s*)?\$?\s*([0-9]{1,3}(?:[,\s][0-9]{3})*(?:\.\d{2})?|[0-9]+(?:\.\d{2})?)", re.I)
SELECTORS = {
    "amazon.": ["#corePrice_feature_div .a-offscreen", ".apexPriceToPay .a-offscreen", "#priceblock_ourprice", "#priceblock_dealprice"],
    "canadacomputers.": [".h2-big", ".product-price", "[itemprop='price']"],
    "newegg.": [".price-current", ".price-current strong", "[itemprop='price']"],
    "memoryexpress.": [".c-capr-pricing__now", ".product-price", "[itemprop='price']"],
}


def parse_price(text: str) -> Decimal | None:
    match = _PRICE.search(text.replace("\xa0", " "))
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", "").replace(" ", ""))
    except InvalidOperation:
        return None


def _json_ld_price(soup: BeautifulSoup) -> Decimal | None:
    for tag in soup.select("script[type='application/ld+json']"):
        try:
            values = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue
        stack = values if isinstance(values, list) else [values]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                offers = item.get("offers")
                if isinstance(offers, dict) and "price" in offers:
                    result = parse_price(str(offers["price"]))
                    if result is not None:
                        return result
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return None


def extract_price(html: str, url: str, selector: str | None = None) -> Decimal | None:
    soup = BeautifulSoup(html, "html.parser")
    selectors = [selector] if selector else []
    host = urlparse(url).netloc.lower()
    for domain, choices in SELECTORS.items():
        if domain in host:
            selectors.extend(choices)
            break
    selectors.extend(["meta[property='product:price:amount']", "meta[itemprop='price']", "[itemprop='price']"])
    for choice in selectors:
        try:
            node = soup.select_one(choice)
        except Exception:  # invalid user-supplied selector
            continue
        if node:
            value = node.get("content") or node.get("value") or node.get_text(" ", strip=True)
            result = parse_price(value)
            if result is not None:
                return result
    return _json_ld_price(soup)
