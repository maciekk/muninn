"""Fixed-width, shared-scale price-range graphics (no ANSI escapes)."""
from decimal import Decimal, ROUND_HALF_UP

DEFAULT_BOUNDS = (Decimal(500), Decimal(1000))
RANGE_WIDTH = 17
RANGE_LEGEND = "● now · T target · ◎ both · < > outside"


def price_range(prices: list[Decimal], target: Decimal | None, bounds: tuple[Decimal, Decimal] | None = None) -> str:
    if bounds is not None:
        validate_bounds(bounds)
    if not prices:
        return "—".center(RANGE_WIDTH)
    low, high, current = min(prices), max(prices), prices[-1]
    start = min(low, target) if target is not None else low
    end = max(high, target) if target is not None else high
    if bounds is not None:
        start, end = bounds

    def position(value: Decimal) -> int:
        if start == end:
            return RANGE_WIDTH // 2
        value = min(end, max(start, value))
        return int(((value - start) / (end - start) * (RANGE_WIDTH - 1)).to_integral_value(rounding=ROUND_HALF_UP))

    # A single value has no observed interval. With a distinct target, dots
    # still show its distance from the current/only observed price.
    cells = list(" " * RANGE_WIDTH if start == end else "·" * RANGE_WIDTH)
    left, right = position(low), position(high)
    if left < right:
        cells[left:right + 1] = list("├" + "─" * (right - left - 1) + "┤")
    current_at = position(current)
    cells[current_at] = "●"
    if target is not None:
        target_at = position(target)
        cells[target_at] = "◎" if target_at == current_at else "T"
    # Overflow markers take precedence over other markers at the edges.
    points = prices + ([target] if target is not None else [])
    if min(points) < start:
        cells[0] = "<"
    if max(points) > end:
        cells[-1] = ">"
    return "".join(cells)


def price_history_plot(prices: list[Decimal], width: int = 25) -> list[str]:
    """One ASCII point per observation on a shared historical min/max scale.

    Rows retain input order; horizontal position encodes price, not elapsed time.
    Constant prices sit at the centre rather than suggesting an upward trend.
    """
    if width < 1:
        raise ValueError('plot width must be positive')
    if not prices:
        return []
    low, high = min(prices), max(prices)
    rows = []
    for price in prices:
        position = width // 2 if low == high else int(
            ((price - low) / (high - low) * (width - 1)).to_integral_value(rounding=ROUND_HALF_UP))
        cells = ['.'] * width
        cells[position] = '*'
        rows.append('|' + ''.join(cells) + '|')
    return rows


def validate_bounds(bounds: tuple[Decimal, Decimal]) -> None:
    low, high = bounds
    if not low.is_finite() or not high.is_finite() or low < 0 or low >= high:
        raise ValueError("range bounds must be finite, non-negative, and range-min < range-max")


def price_sparkline(prices: list[Decimal]) -> str:
    """One bar per observation, on a local min/max scale (not a time axis)."""
    if not prices:
        return "—"
    low, high = min(prices), max(prices)
    if low == high:
        return "▄" * len(prices)
    bars = "▁▂▃▄▅▆▇█"
    return "".join(bars[int(((price - low) / (high - low) * 7).to_integral_value(
        rounding=ROUND_HALF_UP))] for price in prices)
