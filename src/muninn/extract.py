from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

_PRICE = re.compile(r"(?:CAD\s*)?\$?\s*((?:[0-9]{1,3}(?:[,\s][0-9]{3})+|[0-9]+)(?:\.\d{2})?)", re.I)
SELECTORS = {
    "amazon.": ["#corePrice_feature_div .a-offscreen", ".apexPriceToPay .a-offscreen", "#priceblock_ourprice", "#priceblock_dealprice"],
    "bestbuy.ca": ["[data-automation='product-price']:not([itemprop='offers'])"],
    "canadacomputers.": [".h2-big", ".product-price", "[itemprop='price']"],
    "memoryexpress.": [".c-capr-pricing__now", ".c-capr-pricing__grand-total", ".product-price", "[itemprop='price']"],
    "dell.com": [".ps-dell-price-amount", "[data-testid='sharedPSPDellPrice']"],
}


def parse_price(text: str) -> Decimal | None:
    match = _PRICE.search(text.replace("\xa0", " "))
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", "").replace(" ", ""))
    except InvalidOperation:
        return None


def _json_ld_price(
    soup: BeautifulSoup, script_selector: str = "script[type='application/ld+json']"
) -> Decimal | None:
    for tag in soup.select(script_selector):
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
    host = (urlparse(url).hostname or "").lower()
    if host in {"newegg.ca", "newegg.com"} or host.endswith((".newegg.ca", ".newegg.com")):
        # Seller cards, variant comparisons and recommendations carry prices too.
        # Never fall back to those when the main product price is unavailable.
        choices = [selector] if selector else [
            ".product-price--x .price-current_2026",
            ".product-price .price-current_2026",
            ".product-price--x .price-current",
            ".product-price .price-current",
        ]
        for choice in choices:
            try:
                nodes = soup.select(choice)
            except Exception:  # invalid user-supplied selector
                continue
            for node in nodes:
                # Newegg splits dollars and cents into strong/sup elements.
                value = node.get("content") or node.get("value") or node.get_text("", strip=True)
                result = parse_price(value)
                if result is not None:
                    return result
        return None
    for domain, choices in SELECTORS.items():
        if domain in host:
            selectors.extend(choices)
            break
    # Best Buy recommendation cards also expose product-price and price metadata,
    # sometimes without any main-product price element. Never fall back to them.
    best_buy = host == "bestbuy.ca" or host.endswith(".bestbuy.ca")
    if best_buy and not selector:
        result = _json_ld_price(soup, "script#product-json-ld[type='application/ld+json']")
        if result is not None:
            return result
    if not best_buy:
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
    return None if best_buy else _json_ld_price(soup)
