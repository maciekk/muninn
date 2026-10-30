from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path
import sys
import shutil
import textwrap
import time

import requests

from . import browser, browser_bridge
from .config import Product, Retailer, load, track_range_bounds
from .extract import extract_price
from .store import Observation, Store
from .rate_limit import validate_delay, vendor_key, wait_for_vendor
from .visualization import price_sparkline, price_range, RANGE_WIDTH, RANGE_LEGEND, DEFAULT_BOUNDS, validate_bounds

USER_AGENT = "muninn-price-monitor/0.1 (+local cache-first price checking)"
# Suppress insignificant retailer-price fluctuations in change indicators.
CHANGE_ALERT_THRESHOLD = Decimal("10")
_COLOR = False
_PROGRESS_COLOR = True
_RESET = "\033[0m"
_STYLES = {"bold": "1", "dim": "2", "inverse": "7", "red": "31", "green": "32", "yellow": "33", "blue": "34", "cyan": "36", "bright_green": "92"}
_BACKGROUND_COLORS = {
    "black": "40", "red": "41", "green": "42", "yellow": "43", "blue": "44", "magenta": "45", "cyan": "46", "white": "47",
    "bright_black": "100", "bright_red": "101", "bright_green": "102", "bright_yellow": "103", "bright_blue": "104", "bright_magenta": "105", "bright_cyan": "106", "bright_white": "107",
}


def paint(text: str, *styles: str, background: str | None = None) -> str:
    if not _COLOR or (not styles and background is None):
        return text
    codes = ([] if background is None else [background]) + [_STYLES[style] for style in styles]
    return f"\033[{';'.join(codes)}m{text}{_RESET}"


def background_code(value: str) -> str:
    """Convert a named ANSI, palette, or true-colour background to an SGR code."""
    color = value.lower()
    if color in {"gray", "grey"}:
        color = "bright_black"
    if color in _BACKGROUND_COLORS:
        return _BACKGROUND_COLORS[color]
    if color.isdecimal() and 0 <= int(color) <= 255:
        return f"48;5;{color}"
    if len(color) == 7 and color.startswith("#"):
        try:
            red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
        except ValueError:
            pass
        else:
            return f"48;2;{red};{green};{blue}"
    raise ValueError("zebra colour must be an ANSI colour name, a palette index (0-255), or #RRGGBB")


@dataclass
class Checked:
    retailer: Retailer
    history: list[Observation]
    refreshed: bool
    warning: str | None

    @property
    def current(self) -> Observation | None:
        return self.history[-1] if self.history else None


def now() -> datetime:
    return datetime.now(timezone.utc)


_progress_active = False


def progress(action: str, *, track: str = "", merchant: str, product: str, detail: str = "") -> None:
    """Render one aligned terminal row, or a complete plain-text log event."""
    global _progress_active
    if sys.stderr.isatty():
        def column(value: str, width: int, style: str | None = None) -> str:
            value = (value[:width - 1] + "…") if len(value) > width else value
            value = value.ljust(width)
            if style and _PROGRESS_COLOR:
                return f"\033[{_STYLES[style]}m{value}{_RESET}"
            return value

        prefix = column("[muninn]", 8, "dim")
        message = (f"{column(action, 12)} {column(track, 12, 'dim')} "
                   f"{column(merchant, 20, 'bold')} {product}{detail}")
        # Clear the complete prior line, including any longer previous suffix.
        sys.stderr.write(f"\r\033[2K{prefix} {message}")
        sys.stderr.flush()
        _progress_active = True
    else:
        label = " / ".join(part for part in (track, merchant, product) if part)
        print(f"[muninn] {action} {label}{detail}", file=sys.stderr, flush=True)


def clear_progress() -> None:
    """Remove the interactive status line before writing normal output."""
    global _progress_active
    if _progress_active:
        sys.stderr.write("\r\033[2K")
        sys.stderr.flush()
        _progress_active = False


@dataclass
class BridgeRecovery:
    """Offer at most one interactive bridge recovery per check run."""
    prompted: bool = False

    def fetch_html(self, url: str, state_dir: Path) -> str:
        try:
            return browser_bridge.fetch_html(url, state_dir)
        except browser_bridge.BridgeUnavailable:
            if self.prompted or not (sys.stdin.isatty() and sys.stderr.isatty()):
                raise
            self.prompted = True
            clear_progress()
            print("[muninn] Bridge unavailable. Run `muninn browser-bridge` in another terminal\n"
                  "and connect the Firefox extension.\n"
                  "Press Enter to retry, or type ‘skip’ to continue without it: ",
                  file=sys.stderr, end="", flush=True)
            try:
                answer = input().strip().lower()
            except EOFError:
                answer = "skip"
            if answer:
                raise
        return browser_bridge.fetch_html(url, state_dir)


def report_width() -> int:
    # 100 remains a usable fallback for redirected output and narrow terminals.
    return max(100, shutil.get_terminal_size(fallback=(100, 24)).columns)


def format_warning(kind: str, detail: str) -> str:
    """Format a warning with a machine-scannable category prefix."""
    return f"{kind}: {detail}"


def format_retailer_warning(kind: str, product: Product, retailer: Retailer, problem: str = "") -> str:
    """Put the vendor first so fetch problems can be recognized at a glance."""
    detail = f"{retailer.name}: {product.name}"
    return format_warning(kind, f"{detail}; {problem}" if problem else detail)


def refresh_if_needed(store: Store, product: Product, retailer: Retailer, session: requests.Session, current_time: datetime, vendor_delay: float = 2.0, *, track_name: str = "", force: bool = False, bridge_recovery: BridgeRecovery | None = None) -> Checked:
    attempt = store.latest_attempt(retailer.url)
    refresh_seconds = retailer.retry_seconds if attempt is not None and not attempt.success else retailer.refresh_seconds
    due = force or attempt is None or (current_time - attempt.fetched_at).total_seconds() >= refresh_seconds
    warning = None

    def status(action: str, detail: str = "") -> None:
        progress(action, track=track_name, merchant=retailer.name, product=product.name, detail=detail)

    if due and retailer.fetcher == "manual":
        warning = format_retailer_warning("MANUAL_REFRESH_REQUIRED", product, retailer, "import a saved product page; using stale data" if store.successful(retailer.url) else "import a saved product page; no cached price")
        status("Manual", "; import needed")
    elif due:
        status("Refreshing", f" via {retailer.fetcher}…")
        started_waiting = time.monotonic()
        wait_for_vendor(store.db, retailer.url, vendor_delay)
        current_time += timedelta(seconds=time.monotonic() - started_waiting)
        status("Fetching", "…")
        try:
            if retailer.fetcher == "bridge":
                html = (bridge_recovery or BridgeRecovery()).fetch_html(retailer.url, store.root)
                browser.check_page(html)
                if extract_price(html, retailer.url, retailer.price_selector) is None:
                    raise browser.FetchError("Firefox page has no main-product price; keeping prior data")
            elif retailer.fetcher in {"browser", "interactive", "browser_interactive"}:
                profile_name = "browser-profile" if retailer.browser_engine == "chromium" else "browser-profile-firefox"
                profile_dir = store.root / profile_name
                try:
                    html = browser.fetch_html(
                        retailer.url,
                        profile_dir,
                        interactive=retailer.fetcher == "interactive",
                        browser_engine=retailer.browser_engine,
                    )
                except browser.VerificationRequired:
                    if retailer.fetcher != "browser_interactive":
                        raise
                    status("Verify", "; verification required; opening browser…")
                    html = browser.fetch_html(
                        retailer.url, profile_dir, interactive=True, browser_engine=retailer.browser_engine,
                    )
            else:
                response = session.get(retailer.url, timeout=30, headers={"User-Agent": USER_AGENT})
                html = response.text
                # Inspect an error response before raising its HTTP status so known
                # bot/security interstitials produce an actionable warning.
                browser.check_page(html)
                response.raise_for_status()
            browser.check_page(html)
            cache_path, cached_html = store.write_html(retailer.url, html, current_time)
            price = extract_price(cached_html, retailer.url, retailer.price_selector)
            store.record_success(retailer.url, product.name, retailer.name, price, cache_path, current_time)
            status("Updated", f": ${price}" if price is not None else "; price not found")
        except (requests.RequestException, browser.FetchError) as exc:
            warning = format_retailer_warning("FETCH_FAILED", product, retailer, f"using stale data ({exc})")
            store.record_failure(retailer.url, product.name, retailer.name, str(exc), current_time)
            status("Failed", "; keeping stale data")
        except OSError as exc:
            warning = format_retailer_warning("CACHE_FAILED", product, retailer, f"using stale data ({exc})")
            store.record_failure(retailer.url, product.name, retailer.name, str(exc), current_time)
            status("Failed", "; keeping stale data")
    else:
        status("Cached", "; using cached data")
    history = store.successful(retailer.url)
    if not history and warning is None:
        warning = format_retailer_warning("NO_CACHE", product, retailer)
    latest = store.latest_attempt(retailer.url)
    if latest and not latest.success and warning is None:
        warning = format_retailer_warning("FETCH_FAILED", product, retailer, f"using stale data ({latest.error})")
    if latest and latest.success and latest.price is None:
        warning = format_retailer_warning("PRICE_NOT_FOUND", product, retailer, "using prior price if available")
    return Checked(retailer, history, due and warning is None, warning)


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


def truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: width - 1] + "…" if width > 1 else "…"


def cell(value: str, width: int, align: str = "left") -> str:
    value = truncate(value, width)
    return value.rjust(width) if align == "right" else value.ljust(width)


def style_cell(value: str, column: str, background: str | None = None) -> str:
    if column == "Range" and ("<" in value or ">" in value):
        # Preserve the fixed-width range graphic while emphasizing clipped values.
        return "".join(
            paint(character, "inverse", background=background) if character in "<>" else paint(character, background=background)
            for character in value
        )

    styles: tuple[str, ...] = ()
    if "TARGET MET!" in value:
        styles = ("bright_green", "bold")
    elif "NEW LOW!" in value or "↓" in value:
        styles = ("green", "bold")
    elif "↑" in value:
        styles = ("red", "bold")
    elif value.strip() in {"NO PRICE", "NO LINKS"}:
        styles = ("yellow", "bold")
    elif column == "Now" and value.strip() != "—":
        styles = ("blue", "bold")
    return paint(value, *styles, background=background)


def range_legend(bounds: tuple[Decimal, Decimal] | None) -> None:
    scale = f"{money(bounds[0])}–{money(bounds[1])}  " if bounds is not None else ""
    print()
    print(paint(f"Range: {scale}{RANGE_LEGEND}", "dim"))
    print()


def product_report(track_name: str, product: Product, checked: list[Checked], long: bool = False, bounds: tuple[Decimal, Decimal] = DEFAULT_BOUNDS, legend: bool = False) -> None:
    # Share the same price order between the table and vendor details; missing prices last.
    checked = sorted(checked, key=lambda item: overview_order(
        product, item.current.price if item.current else None))
    width = report_width()
    print("─" * width)
    heading = f"{track_name.upper()}: {product.name.upper()}"
    if product.target_price is not None:
        heading += f"  |  TARGET: {money(product.target_price)}"
    print(paint(heading, "cyan", "bold"))
    print()
    columns = (("Vendor", width - 61, "left"), ("Target", 10, "right"), ("Now", 10, "right"), ("Range", RANGE_WIDTH, "left"), ("Status", 16, "left"))
    print("  ".join(paint(cell(name, width, align), "bold") for name, width, align in columns))
    for item in checked:
        values = (retailer_label(item.retailer), *metrics(item, product.target_price, bounds))
        print("  ".join(style_cell(cell(value, width, align), name) for (name, width, align), value in zip(columns, values)))
    if legend:
        range_legend(bounds)
    if not long:
        return

    if not legend:
        print()
    print(paint("Product Details", "bold"))
    if product.desirability is not None:
        print(f"  Desirability: {product.desirability}/10")
    if product.notes:
        print("  Notes:")
        for note in product.notes:
            lines = textwrap.wrap(note, width=68) or [""]
            print(f"    - {lines[0]}")
            for line in lines[1:]:
                print(f"      {line}")
    for item in checked:
        current = item.current
        print()
        print(paint(retailer_label(item.retailer), "bold"))
        if item.retailer.seller:
            seller_type = item.retailer.seller_type.replace("_", " ") if item.retailer.seller_type else "unspecified"
            print(f"  Seller: {item.retailer.seller} ({seller_type})")
        elif item.retailer.seller_type:
            print(f"  Seller type: {item.retailer.seller_type.replace('_', ' ')}")
        history = [observation for observation in item.history if observation.price is not None]
        # Keep one bar per observation rather than silently aggregating history.
        shown = history[-40:]
        prices = [observation.price for observation in shown]
        if prices:
            print(f"  History  {price_sparkline(prices)}  latest {money(prices[-1])}")
            dates = [observation.fetched_at.astimezone().strftime("%Y-%m-%d") for observation in shown]
            span = dates[0] if dates[0] == dates[-1] else f"{dates[0]} → {dates[-1]}"
            count = f"{len(shown)} obs" if len(history) == len(shown) else f"last {len(shown)} of {len(history)} obs"
            print(f"           {money(min(prices))}–{money(max(prices))} · {span} · {count}")
        else:
            print("  History  —  no observations")
        timestamp = current.fetched_at.astimezone().strftime("%Y-%m-%d %H:%M") if current else "never"
        print(paint(f"  {item.retailer.url}", "dim"))
        print(paint(f"  Cached at: {timestamp}", "dim"))


def track_overview(name: str, results: list[tuple[Product, list[Checked]]], bounds: tuple[Decimal, Decimal] = DEFAULT_BOUNDS, legend: bool = False, cents: bool = False, skip_no_price: bool = False, zebra_background: str | None = None) -> None:
    width = report_width()
    print("═" * width)
    print(paint(f"TRACK: {name.upper()}", "cyan", "bold"))
    print()
    columns = (("Product", width - 83, "left"), ("Rating", 6, "right"), ("Vendor", 16, "left"), ("Target", 10, "right"), ("Now", 10, "right"), ("Range", RANGE_WIDTH, "left"), ("Status", 12, "left"))
    print("  ".join(paint(cell(title, width, align), "bold") for title, width, align in columns))
    rows = []
    for product, checked in results:
        rating = product.desirability
        rating_text = str(rating) if rating is not None else "—"
        # No verified listing yet: retain the candidate visibly, without fetching
        # search pages or substituting a different capacity/model.
        if not checked:
            values = (product.name, rating_text, "—", money(product.target_price, cents=cents) if product.target_price is not None else "—", "—", price_range([], product.target_price), "NO LINKS")
            rows.append((overview_order(product, None), values))
        for item in checked:
            price = item.current.price if item.current else None
            values = (product.name, rating_text, retailer_label(item.retailer), *metrics(item, product.target_price, bounds, cents=cents))
            if skip_no_price and values[-1] == "NO PRICE":
                continue
            rows.append((overview_order(product, price), values))
    previous_product = None
    product_number = -1
    for _, values in sorted(rows, key=lambda row: row[0]):
        if values[0] != previous_product:
            product_number += 1
            previous_product = values[0]
        background = zebra_background if product_number % 2 else None
        separator = paint("  ", background=background)
        print(separator.join(style_cell(cell(value, width, align), title, background)
                             for (title, width, align), value in zip(columns, values)))

    if legend:
        range_legend(bounds)

def overview_order(product: Product, price: Decimal | None) -> tuple:
    """Rating descending, then product name, then price; unrated/missing prices last."""
    rating = product.desirability
    return (rating is None, -rating if rating is not None else Decimal(0), product.name,
            price is None, price if price is not None else Decimal(0))


def report(tracks: list[tuple[str, list[Product]]], directory: Path, long: bool = False, vendor_delay: float = 2.0, bounds: tuple[Decimal, Decimal] = DEFAULT_BOUNDS, legend: bool = False, track_bounds: dict[str, tuple[Decimal, Decimal]] | None = None, zebra_background: str | None = None, long_file: Path | None = None, force: bool = False) -> int:
    store = Store(directory)
    session = requests.Session()
    warnings: dict[str, list[tuple[str, str | None]]] = {}
    collected = []
    bridge_recovery = BridgeRecovery()

    def render(long: bool) -> None:
        for track_name, results, track_bound in collected:
            if long:
                for product, checked in results:
                    print()
                    product_report(track_name, product, checked, long=True, bounds=track_bound, legend=legend)

            print()
            track_overview(track_name, results, bounds=track_bound, legend=legend, cents=long, skip_no_price=not long,
                           zebra_background=zebra_background)

            track_warnings = warnings.get(track_name, ())
            if track_warnings:
                print()
                heading = f"WARNINGS: {track_name.upper()}" if len(tracks) > 1 else "WARNINGS"
                print(paint(heading, "yellow", "bold"))
                for warning, url in track_warnings:
                    print(f"- {warning}")
                    if url:
                        print(paint(f"  {url}", "dim"))
    try:
        for track_name, products in tracks:
            track_bound = (track_bounds or {}).get(track_name, bounds)
            results = [(product, [refresh_if_needed(store, product, retailer, session, now(), vendor_delay=vendor_delay, track_name=track_name, force=force, bridge_recovery=bridge_recovery) for retailer in product.retailers]) for product in products]
            collected.append((track_name, results, track_bound))
            for product, checked in results:
                if not product.retailers:
                    warnings.setdefault(track_name, []).append((format_warning("NO_VENDORS", product.name), None))
                for item in checked:
                    if item.warning:
                        url = item.retailer.url if item.warning.startswith(("FETCH_FAILED:", "NO_CACHE:", "PRICE_NOT_FOUND:", "MANUAL_REFRESH_REQUIRED:")) else None
                        warnings.setdefault(track_name, []).append((item.warning, url))
        clear_progress()
        render(long and long_file is None)
        if long_file is not None:
            global _COLOR
            color = _COLOR
            try:
                with long_file.open("w", encoding="utf-8") as output, redirect_stdout(output):
                    _COLOR = False
                    render(True)
            finally:
                _COLOR = color
    finally:
        clear_progress()
        session.close()
        store.close()
    return 1 if warnings else 0


def import_saved_page(tracks: list[tuple[str, list[Product]]], directory: Path, url: str, html_path: Path) -> Decimal:
    """Import a user-selected saved page without making any network requests."""
    return import_page_html(tracks, directory, url, html_path.read_text(encoding="utf-8-sig"))


def import_page_html(tracks: list[tuple[str, list[Product]]], directory: Path, url: str, html: str) -> Decimal:
    """Validate and record rendered HTML from a saved file or browser tab."""
    matches = [(product, retailer) for _, products in tracks for product in products
               for retailer in product.retailers if retailer.url == url]
    if not matches:
        raise ValueError("URL is not configured in the selected tracks; copy the exact URL from the product YAML")
    selectors = {retailer.price_selector for _, retailer in matches}
    if len(selectors) != 1:
        raise ValueError("URL has conflicting price selectors; select a single track or fix the configuration")
    try:
        browser.check_page(html)
    except browser.FetchError as exc:
        raise ValueError(f"saved page rejected: {exc}") from exc
    product, retailer = matches[0]
    price = extract_price(html, url, retailer.price_selector)
    if price is None:
        raise ValueError("saved page contains no extractable price; existing cache and history were not changed")
    store = Store(directory)
    try:
        at = now()
        cache_path, _ = store.write_html(url, html, at)
        store.record_success(url, product.name, retailer.name, price, cache_path, at)
    finally:
        store.close()
    return price


def reset_refreshes(tracks: list[tuple[str, list[Product]]], directory: Path, vendor: str | None = None) -> tuple[int, int]:
    """Mark configured URLs due now, optionally restricted to one URL host."""
    selected_vendor = vendor_key(f"https://{vendor}") if vendor else None
    marked = already_due = 0
    store = Store(directory)
    try:
        for _, products in tracks:
            for product in products:
                for retailer in product.retailers:
                    if selected_vendor and vendor_key(retailer.url) != selected_vendor:
                        continue
                    if store.mark_due(retailer.url):
                        marked += 1
                    else:
                        already_due += 1
    finally:
        store.close()
    return marked, already_due


def track_files(tracks_dir: Path, names: list[str]) -> list[tuple[str, list[Path]]]:
    if not tracks_dir.is_dir():
        raise ValueError(f"tracks directory not found: {tracks_dir}")
    directories = [tracks_dir / name for name in names] if names else sorted(path for path in tracks_dir.iterdir() if path.is_dir())
    if not directories:
        raise ValueError(f"no track directories found in {tracks_dir}")
    tracks = []
    for directory in directories:
        if not directory.is_dir():
            raise ValueError(f"track not found: {directory}")
        files = sorted(path for path in [*directory.glob("*.yaml"), *directory.glob("*.yml")]
                       if path.name not in {"track.yaml", "track.yml", "config.yaml", "config.yml"})
        if not files:
            raise ValueError(f"no product YAML files found in {directory}")
        tracks.append((directory.name, files))
    return tracks


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="muninn", allow_abbrev=False, description="Cache-first product price monitor")
    parser.add_argument("--tracks-dir", type=Path, default=Path("tracks"), help="track directory (default: ./tracks)")
    parser.add_argument("--force", action="store_true", help="with check, fetch regardless of cache age (preserves cached data)")
    parser.add_argument("--vendor-delay", type=float, default=2.0, metavar="SECONDS", help="minimum seconds between fetch starts per vendor (default: 2; 0 disables waiting)")
    parser.add_argument("--range-min", metavar="CAD", help="visualization lower bound (overrides track config)")
    parser.add_argument("--range-max", metavar="CAD", help="visualization upper bound (overrides track config)")
    parser.add_argument("--color", choices=["auto", "always", "never"], default="auto", help="terminal color mode (default: auto)")
    parser.add_argument("--zebra", nargs="?", const="#E0E0E0", metavar="COLOR", help="shade alternate products; use --zebra=COLOR (default: #E0E0E0; accepts ANSI name, 0-255, or #RRGGBB)")
    parser.add_argument("--legend", action="store_true", help="show range graphic explanation")
    parser.add_argument("--vendor", metavar="HOST", help="with reset, limit to a vendor host such as newegg.ca")
    parser.add_argument("--url", help="with import, exact configured retailer URL")
    parser.add_argument("--html", type=Path, help="with import, saved product HTML file")
    parser.add_argument("command", nargs="?", help="'check', 'reset', 'import', 'browser-bridge', or 'help'; may be omitted (infers 'check')")
    parser.add_argument("tracks", nargs="*", help="track names")
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--short", dest="long", action="store_false", help="track overview only (default)")
    verbosity.add_argument("--long", dest="long", nargs="?", const=True, type=lambda value: Path(value) if value else True, metavar="FILE", help="include product details; with --long=FILE, save full report there and print short tables")
    parser.set_defaults(long=False)
    # argparse normally consumes a following positional for nargs="?". Bind
    # bare optional-value flags to their defaults so only --flag=value supplies
    # a value, leaving commands and track names unambiguous.
    arguments = list(sys.argv[1:] if argv is None else argv)
    bare_defaults = {"--long": "", "--zebra": "#E0E0E0"}
    for index, argument in enumerate(arguments):
        if argument == "--":
            break
        if argument in bare_defaults:
            arguments[index] = f"{argument}={bare_defaults[argument]}"
    args = parser.parse_intermixed_args(arguments)
    global _COLOR, _PROGRESS_COLOR
    _PROGRESS_COLOR = args.color != "never"
    _COLOR = args.color == "always" or (args.color == "auto" and sys.stdout.isatty())
    command = args.command
    if command == "help":
        parser.print_help()
        raise SystemExit(0)
    names = args.tracks if command in {None, "check", "reset", "import", "browser-bridge"} else [command, *args.tracks]
    try:
        if args.force and command in {"reset", "import", "browser-bridge"}:
            raise ValueError("--force is only valid with check")
        if args.vendor and command != "reset":
            raise ValueError("--vendor is only valid with the reset command")
        if command == "import" and (not args.url or args.html is None):
            raise ValueError("import requires --url and --html")
        if command != "import" and (args.url is not None or args.html is not None):
            raise ValueError("--url and --html are only valid with the import command")
        file_groups = track_files(args.tracks_dir, names)
        tracks: list[tuple[str, list[Product]]] = []
        configured_bounds: dict[str, tuple[Decimal | None, Decimal | None]] = {}
        for track_name, files in file_groups:
            track_directory = files[0].parent
            config_files = [track_directory / name for name in ("track.yaml", "track.yml", "config.yaml", "config.yml") if (track_directory / name).is_file()]
            if len(config_files) > 1:
                raise ValueError(f"track {track_name} has multiple configuration files; use only track.yaml")
            if config_files:
                configured_bounds[track_name] = track_range_bounds(config_files[0])
            products: list[Product] = []
            for path in files:
                configured = load(path)
                if len(configured) != 1:
                    raise ValueError(f"{path} must define exactly one product")
                products.append(configured[0])
            tracks.append((track_name, products))
        if command == "browser-bridge":
            directory = args.tracks_dir.resolve().parent
            allowed_urls = {retailer.url for _, products in tracks for product in products
                            for retailer in product.retailers if retailer.fetcher == "bridge"}
            browser_bridge.serve(
                lambda url, html: import_page_html(tracks, directory, url, html),
                directory / ".muninn" / "v1", allowed_urls,
            )
            status = 0
        elif command == "import":
            price = import_saved_page(tracks, args.tracks_dir.resolve().parent, args.url, args.html)
            print(f"Imported ${price:.2f} for {args.url} (recorded at import time).")
            status = 0
        elif command == "reset":
            marked, already_due = reset_refreshes(tracks, args.tracks_dir.resolve().parent, args.vendor)
            scope = f" for {args.vendor}" if args.vendor else ""
            print(f"Reset {marked} retailer refresh timestamp(s){scope}; {already_due} URL(s) had no attempt and were already due.")
            status = 0
        else:
            try:
                cli_min = Decimal(args.range_min) if args.range_min is not None else None
                cli_max = Decimal(args.range_max) if args.range_max is not None else None
            except InvalidOperation as exc:
                raise ValueError("range bounds must be numbers") from exc
            bounds = (cli_min if cli_min is not None else DEFAULT_BOUNDS[0],
                      cli_max if cli_max is not None else DEFAULT_BOUNDS[1])
            track_bounds: dict[str, tuple[Decimal, Decimal]] = {}
            for track_name, _ in tracks:
                configured_min, configured_max = configured_bounds.get(track_name, (None, None))
                track_bound = (cli_min if cli_min is not None else (configured_min if configured_min is not None else DEFAULT_BOUNDS[0]),
                               cli_max if cli_max is not None else (configured_max if configured_max is not None else DEFAULT_BOUNDS[1]))
                validate_bounds(track_bound)
                track_bounds[track_name] = track_bound
            validate_delay(args.vendor_delay)
            zebra_background = background_code(args.zebra) if args.zebra is not None else None
            status = report(tracks, args.tracks_dir.resolve().parent, long=args.long is True, long_file=args.long if isinstance(args.long, Path) else None, vendor_delay=args.vendor_delay, bounds=bounds, legend=args.legend,
                            track_bounds=track_bounds or None, zebra_background=zebra_background, force=args.force)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    raise SystemExit(status)


if __name__ == "__main__":
    main()
