"""Shared report calculations and cache-only snapshots; no terminal or fetch logic."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .config import Product, Retailer
from .store import Observation, Store
from .visualization import DEFAULT_BOUNDS, price_range


# Suppress insignificant retailer-price fluctuations in change indicators.
CHANGE_ALERT_THRESHOLD = Decimal("10")


@dataclass
class Checked:
    retailer: Retailer
    history: list[Observation]
    refreshed: bool
    warning: str | None
    latest_attempt: Observation | None = None

    @property
    def current(self) -> Observation | None:
        return self.history[-1] if self.history else None


def now() -> datetime:
    return datetime.now(timezone.utc)


def format_warning(kind: str, detail: str) -> str:
    """Format a warning with a machine-scannable category prefix."""
    return f"{kind}: {detail}"


def format_retailer_warning(kind: str, product: Product, retailer: Retailer, problem: str = "") -> str:
    """Put the vendor first so fetch problems can be recognized at a glance."""
    detail = f"{retailer.name}: {product.name}"
    return format_warning(kind, f"{detail}; {problem}" if problem else detail)


def money(value: Decimal, cents: bool = True) -> str:
    """Format a monetary value, optionally rounding it for compact tables."""
    if not cents:
        return f"${value.quantize(Decimal(1), rounding=ROUND_HALF_UP):,.0f}"
    value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${value:,.2f}"


def retailer_label(retailer: Retailer) -> str:
    """Return a concise report label that makes marketplace listings visible."""
    return f"{retailer.name} [3P]" if retailer.seller_type == "third_party" else retailer.name


def metrics(item: Checked, target: Decimal | None, bounds: tuple[Decimal, Decimal] | None = None, cents: bool = True) -> tuple[str, str, str, str]:
    current = item.current
    target_text = money(target, cents=cents) if target is not None else "—"
    if not current or current.price is None:
        return target_text, "—", price_range([], target), "NO PRICE"
    prices = [x.price for x in item.history if x.price is not None]
    status = "—"
    if target is not None and current.price <= target:
        status = "TARGET MET!"
    elif item.refreshed and (len(prices) == 1 or current.price < min(prices[:-1])):
        status = "NEW LOW!"
    elif len(prices) > 1 and prices[-2] - current.price >= CHANGE_ALERT_THRESHOLD:
        status = f"↓ {money(prices[-2] - current.price, cents=cents)}"
    elif len(prices) > 1 and current.price > prices[-2]:
        status = f"↑ {money(current.price - prices[-2], cents=cents)}"
    return target_text, money(current.price, cents=cents), price_range(prices, target, bounds), status


def data_age(item: Checked, current_time: datetime) -> str:
    """Age of the displayed price; retain finer units until two larger units."""
    current = item.current
    if current is None or current.price is None or not current.timestamp_known:
        return "—"
    seconds = max(0, int((current_time - current.fetched_at).total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= 2 * size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def observation_timestamp(observation: Observation | None, *, timespec: str = 'seconds') -> str:
    if observation is None:
        return 'never'
    if not observation.timestamp_known:
        return 'unknown (legacy reset)'
    return observation.fetched_at.astimezone().isoformat(sep=' ', timespec=timespec)


def overview_order(product: Product, price: Decimal | None) -> tuple:
    """Rating descending, then product name, then price; unrated/missing prices last."""
    rating = product.desirability
    return (rating is None, -rating if rating is not None else Decimal(0), product.name,
            price is None, price if price is not None else Decimal(0))


def read_checked(store: Store, product: Product, retailer: Retailer, *,
                 refreshed: bool = False, warning: str | None = None) -> Checked:
    """Read cached observations and apply the same warning rules as a CLI check."""
    history = store.successful(retailer.url)
    if not history and warning is None:
        warning = format_retailer_warning("NO_CACHE", product, retailer)
    latest = store.latest_attempt(retailer.url)
    if latest and not latest.success and warning is None:
        warning = format_retailer_warning("FETCH_FAILED", product, retailer, f"using stale data ({latest.error})")
    if latest and latest.success and latest.price is None:
        warning = format_retailer_warning("PRICE_NOT_FOUND", product, retailer, "using prior price if available")
    return Checked(retailer, history, refreshed and warning is None, warning, latest)


@dataclass
class ProductSnapshot:
    product: Product
    checked: list[Checked]

    @property
    def key(self) -> str:
        return str(self.product.source_path or self.product.name)


@dataclass
class TrackSnapshot:
    name: str
    products: list[ProductSnapshot]
    bounds: tuple[Decimal, Decimal]


def read_snapshots(tracks: list[tuple[str, list[Product]]], directory: Path,
                   track_bounds: dict[str, tuple[Decimal, Decimal]]) -> list[TrackSnapshot]:
    """Never refresh, wait, or open a browser. Close SQLite before handing data to UI."""
    store = Store(directory)
    try:
        snapshots = []
        for name, products in tracks:
            results = [ProductSnapshot(product, [read_checked(store, product, retailer)
                       for retailer in product.retailers]) for product in products]
            snapshots.append(TrackSnapshot(name, results, track_bounds.get(name, DEFAULT_BOUNDS)))
        return snapshots
    finally:
        store.close()
