"""Optional, cache-only Textual frontend. Imported only for --tui."""
from __future__ import annotations

import json
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.message import Message
from textual.widgets import DataTable, Footer, Header, OptionList, Static
from rich.text import Span, Text
from rich.style import Style

from .appearance import read_macos_appearance
from .report_data import (Checked, ProductSnapshot, TrackSnapshot, data_age, metrics,
                          money, now, overview_order, read_snapshots, retailer_label, observation_timestamp)
from .store import Observation
from .visualization import RANGE_LEGEND, price_sparkline, price_history_plot


TABLE_COLUMNS = ('Product', 'Rating', 'Vendor', 'Target', 'Now', 'Age', 'Range', 'Status')


def fitted_widths(values: list[tuple[str, ...]], available: int) -> list[int]:
    """Spend spare space on labels, shrinking those before prices and status."""
    widths = [max([Text(title).cell_len] + [max(Text(line).cell_len for line in row[index].split("\n")) for row in values])
              for index, title in enumerate(TABLE_COLUMNS)]
    widths[0] = min(widths[0], 48)
    widths[2] = min(widths[2], 24)
    # Each column also has one cell of padding on either side.
    budget = max(len(widths), available - 2 * len(widths))
    for minimums in ((10, 3, 8, 6, 6, 3, 5, 6), (1,) * len(widths)):
        for index in (0, 2, 6, 7, 1, 5, 3, 4):
            reduction = min(max(0, sum(widths) - budget), max(0, widths[index] - minimums[index]))
            widths[index] -= reduction
    return widths


def row_key(product: ProductSnapshot, item: Checked | None) -> str:
    return json.dumps([product.key, item.retailer.name if item else None,
                       item.retailer.url if item else None])


DETAIL_PALETTES = {
    'dark': {'cyan': '#66d9ef', 'blue': '#82aaff', 'green': '#7bd88f',
             'red': '#ff8080', 'yellow': '#ffd866'},
    'light': {'cyan': '#006b80', 'blue': '#1758a8', 'green': '#176b35',
              'red': '#aa2436', 'yellow': '#805500'},
}


def adapt_report_palette(report: Text, *, dark: bool) -> Text:
    """Retain semantic emphasis and literal content with legible theme colours."""
    palette = DETAIL_PALETTES['dark' if dark else 'light']
    result = report.copy()
    spans = []
    for span in result.spans:
        style = Style.parse(span.style) if isinstance(span.style, str) else span.style
        if style.color is not None and style.color.name in palette:
            style += Style(color=palette[style.color.name])
        spans.append(Span(span.start, span.end, style))
    result.spans = spans
    return result


class DetailReport(Text):
    """Unframed report text with explicit semantic styles, never markup parsing."""

    def line(self, value: str = '', style: str = '') -> None:
        self.append(value, style=style)
        self.append('\n')

    def section(self, title: str, *, secondary: bool = False) -> None:
        if self.plain:
            self.line()
        self.line(title, 'bold dim' if secondary else 'bold cyan')
        self.line('─' * 40, 'dim')

    def field(self, label: str, value: str, style: str = '', *, secondary: bool = False) -> None:
        self.append(f'{label:<25}', style='dim' if secondary else '')
        self.line(value, f'dim {style}'.strip() if secondary else style)


def detail_status_style(status: str) -> str:
    if status == 'TARGET MET!' or status == 'NEW LOW!' or status.startswith('↓'):
        return 'bold green'
    if status.startswith('↑'):
        return 'bold red'
    if status == 'NO PRICE':
        return 'bold yellow'
    return 'dim'


def product_details(track: TrackSnapshot, snapshot: ProductSnapshot) -> Text:
    product = snapshot.product
    report = DetailReport()
    report.line(f'{track.name.upper()}: {product.name}', 'bold cyan')
    report.line('Cached listing report — no live fetching', 'dim')
    report.section('PRODUCT DETAILS')
    if product.target_price is not None:
        report.field('Target price:', money(product.target_price), 'bold')
    if product.desirability is not None:
        report.field('Desirability:', f'{product.desirability}/10', 'bold')
    if product.source_path:
        report.field('Product configuration:', str(product.source_path), secondary=True)
    if product.notes:
        report.section('NOTES')
        for note in product.notes:
            report.append('• ', style='cyan')
            report.line(note)
    report.line()
    report.line('Refresh with the CLI, then press r to reload cached data.', 'dim')
    if not snapshot.checked:
        report.line()
        report.line('NO LINKS: no retailers configured.', 'bold yellow')
    return report


def observation_ledger(history: list[Observation], *, width: int = 96) -> Text:
    """Aligned financial columns and a shared price axis, with time down the rows."""
    report = DetailReport()
    history = [observation for observation in history if observation.success and observation.price is not None]
    if not history:
        return report
    prices = [observation.price for observation in history]
    timestamps = [observation_timestamp(observation) for observation in history]
    amounts = [money(price) for price in prices]
    timestamp_title = 'Timestamp (local, UTC offset)'
    timestamp_width = max(len(timestamp_title), *(len(stamp) for stamp in timestamps))
    price_width = max(len('Price'), *(len(amount) for amount in amounts))
    plot_width = max(8, min(31, width - timestamp_width - price_width - 6))
    low, high = min(prices), max(prices)
    report.field('Plot scale:', f'{money(low)} (left) → {money(high)} (right)', secondary=True)
    report.line('Time runs downward in observation order; row spacing is not elapsed time.', 'dim')
    if low == high:
        report.line('Constant price — all points centred on the price axis.', 'dim')
    if any(not observation.timestamp_known for observation in history):
        report.line('Unknown timestamps were overwritten by a legacy reset; prices remain in recording order.', 'yellow')
    report.line()
    report.line(f'{timestamp_title:<{timestamp_width}}  {"Price":>{price_width}}  Price plot', 'bold dim')
    report.line(f'{"─" * timestamp_width}  {"─" * price_width}  {"─" * (plot_width + 2)}', 'dim')
    for index, (timestamp, amount, plot) in enumerate(zip(timestamps, amounts, price_history_plot(prices, plot_width))):
        report.append(f'{timestamp:<{timestamp_width}}  ', style='dim')
        report.append(f'{amount:>{price_width}}', style='bold blue' if index == len(history) - 1 else '')
        report.append('  ')
        point = plot.index('*')
        report.append(plot[:point], style='dim')
        report.append('*', style='bold cyan')
        report.line(plot[point + 1:], 'dim')
    return report


def retailer_details(item: Checked, track: TrackSnapshot, product: ProductSnapshot, *, ledger_width: int = 96) -> Text:
    """A complete listing report; only the sparkline uses a recent-history window."""
    target, price, graphic, status = metrics(item, product.product.target_price, track.bounds)
    retailer = item.retailer
    report = DetailReport()
    report.section('RETAILER / LISTING')
    report.field('Vendor:', retailer_label(retailer), 'bold')
    report.field('URL:', retailer.url, secondary=True)
    if retailer.seller:
        report.field('Seller:', retailer.seller)
    if retailer.seller_type:
        report.field('Seller type:', retailer.seller_type.replace('_', ' '))
    report.section('PRICE SUMMARY')
    report.field('Current cached price:', price, 'bold blue' if item.current else 'bold yellow')
    report.field('Target price:', target, 'bold')
    for index, line in enumerate(status.split('\n')):
        report.field('Status:' if index == 0 else '', line, detail_status_style(line))
    report.field('Price age:', data_age(item, now()), secondary=True)
    report.field('Price range:', graphic, 'cyan')
    report.field('Range scale:', f'{money(track.bounds[0])}–{money(track.bounds[1])}', secondary=True)
    report.field('Range legend:', RANGE_LEGEND, secondary=True)
    report.section('CACHE AND LATEST ATTEMPT', secondary=True)
    stamp = observation_timestamp(item.current)
    report.field('Cached price observed:', stamp, secondary=True)
    if item.latest_attempt:
        attempt = item.latest_attempt
        report.field('Latest attempt:', observation_timestamp(attempt), secondary=True)
        report.field('Attempt result:', 'success' if attempt.success else 'failed',
                     'green' if attempt.success else 'bold red', secondary=attempt.success)
        if attempt.success and attempt.price is None:
            report.field('Attempt price:', 'not found (prior cached price retained, if available)', 'yellow')
        elif attempt.price is not None:
            report.field('Attempt price:', money(attempt.price), secondary=True)
        if attempt.error:
            report.field('Error:', attempt.error, 'red')
    else:
        report.field('Latest attempt:', 'never', secondary=True)
    if item.warning:
        report.line()
        report.line('WARNING', 'bold yellow')
        report.line(item.warning, 'yellow')
    report.section('COLLECTION SETTINGS', secondary=True)
    report.field('Fetcher:', retailer.fetcher, secondary=True)
    report.field('Refresh interval:', f'{retailer.refresh_seconds} seconds', secondary=True)
    report.field('Failure retry interval:', f'{retailer.retry_seconds} seconds', secondary=True)
    if retailer.fetcher in {'browser', 'interactive', 'browser_interactive'}:
        report.field('Browser engine:', retailer.browser_engine, secondary=True)
    if retailer.price_selector:
        report.field('Price selector:', retailer.price_selector, secondary=True)
    history = [observation for observation in item.history if observation.price is not None]
    report.section('PRICE HISTORY')
    report.field('Priced observations:', str(len(history)), 'bold')
    if history:
        prices = [observation.price for observation in history]
        report.field('Historical low:', money(min(prices)), 'bold green')
        report.field('Historical high:', money(max(prices)), 'bold red')
        report.field('First observation:', observation_timestamp(history[0]), secondary=True)
        report.field('Latest observation:', observation_timestamp(history[-1]), secondary=True)
        report.field('Recent trend:', price_sparkline(prices[-40:]), 'cyan')
        report.field('Trend window:', f'last {min(40, len(history))} of {len(history)} observations', secondary=True)
        report.section('OBSERVATION LEDGER — ALL SUCCESSFUL PRICED OBSERVATIONS')
        report.append(observation_ledger(history, width=ledger_width))
    else:
        report.line('No priced observations recorded.', 'dim')
    return report


class ProductScreen(Screen):
    BINDINGS = [('escape', 'back', 'Back')]

    def __init__(self, track: TrackSnapshot, product: ProductSnapshot, selected: Checked | None):
        super().__init__()
        self.track = track
        self.product = product
        self.selected_url = selected.retailer.url if selected else None
        self.selected_name = selected.retailer.name if selected else None

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id='details'):
            yield Static('', id='product-heading', markup=False)
            yield Static('', id='retailer-details', markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        self.app.theme_changed_signal.subscribe(self, self.on_theme_changed)
        await self.show_details()

    async def on_theme_changed(self, _theme) -> None:
        await self.show_details()

    def on_resize(self) -> None:
        if self.is_mounted:
            self.call_after_refresh(self.show_details)

    async def show_details(self) -> None:
        dark = self.app.current_theme.dark
        self.query_one('#product-heading', Static).update(
            adapt_report_palette(product_details(self.track, self.product), dark=dark))
        item = next((item for item in self.product.checked
                     if item.retailer.url == self.selected_url and item.retailer.name == self.selected_name), None)
        report = self.query_one('#retailer-details', Static)
        report.display = item is not None or self.selected_url is not None
        if item:
            report.update(adapt_report_palette(
                retailer_details(item, self.track, self.product, ledger_width=max(1, self.size.width - 6)), dark=dark))
        elif self.selected_url is not None:
            notice = Text('Selected listing is no longer available.')
            notice.stylize('bold yellow')
            report.update(adapt_report_palette(notice, dark=dark))
        else:
            report.update(Text(''))
        self.query_one('#details', VerticalScroll).focus()

    def action_back(self) -> None:
        self.app.pop_screen()


class ResultsTable(DataTable):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.product_groups: list[int] = []

    def _get_row_style(self, row_index: int, base_style: Style) -> Style:
        # Textual exposes zebra components but no public per-row style callback.
        # Keep its full-row styling (including padding and cursor overlays), using
        # product parity instead of retailer-row parity at this single hook.
        if self.zebra_stripes and self.fixed_rows <= row_index < len(self.product_groups):
            component = 'datatable--odd-row' if self.product_groups[row_index] % 2 else 'datatable--even-row'
            return self.get_component_styles(component).rich_style
        return super()._get_row_style(row_index, base_style)

    class Resized(Message):
        """The table's actual viewport has changed, including after screen resume."""

    def on_resize(self) -> None:
        self.post_message(self.Resized())


class ResultsApp(App):
    TITLE = 'Muninn — cached results'
    CSS = '''
    #sidebar { width: 20; min-width: 12; max-width: 25%; border-right: solid $primary; }
    #sidebar-title { height: auto; padding: 1; text-style: bold; }
    #tracks { height: 1fr; }
    #main { width: 1fr; }
    #status { height: auto; padding: 0 1; }
    #results { height: 1fr; }
    #details { padding: 1 2; }
    #product-heading {
        height: auto;
        margin-bottom: 2;
        padding: 1 2;
        background: $surface;
        border-left: thick $primary;
    }
    #retailer-details { height: auto; }
    '''
    BINDINGS = [('q', 'quit', 'Quit'), ('t', 'tracks', 'Tracks'),
                ('r', 'reload', 'Reload cache'), ('m', 'missing', 'Missing prices'),
                ('escape', 'tracks', 'Tracks')]

    def __init__(self, tracks, directory: Path, track_bounds):
        super().__init__()
        self.configured_tracks = tracks
        self.directory = directory
        self.track_bounds = track_bounds
        self.snapshots = read_snapshots(tracks, directory, track_bounds)
        self.track_index = 0
        self.show_missing = False
        self.rows: dict[str, tuple[ProductSnapshot, Checked | None]] = {}
        self.selections: dict[str, str] = {}
        self.system_appearance: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id='sidebar'):
                yield Static('Tracks', id='sidebar-title')
                yield OptionList(*(Text(track.name) for track in self.snapshots), id='tracks')
            with Vertical(id='main'):
                yield Static('', id='status', markup=False)
                yield ResultsTable(id='results', cursor_type='row', zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.overview = self.screen
        table = self.overview.query_one('#results', DataTable)
        self.overview.query_one('#tracks', OptionList).highlighted = 0
        self.populate()
        table.focus()
        self.call_after_refresh(self.fit_table)
        self.poll_appearance()
        self.set_interval(5, self.poll_appearance)

    def poll_appearance(self) -> None:
        self.run_worker(self.sync_appearance(), group='appearance', exclusive=True, exit_on_error=False)

    async def sync_appearance(self) -> None:
        appearance = await read_macos_appearance()
        if appearance in {'light', 'dark'} and appearance != self.system_appearance:
            self.system_appearance = appearance
            self.theme = f'textual-{appearance}'

    def on_results_table_resized(self) -> None:
        if hasattr(self, 'overview'):
            self.call_after_refresh(self.fit_table)

    def fit_table(self) -> None:
        table = self.overview.query_one('#results', DataTable)
        if table.size.width != self.table_width:
            self.remember_selection()
            self.populate()

    def remember_selection(self) -> None:
        table = self.overview.query_one('#results', DataTable)
        if table.row_count:
            self.selections[self.snapshots[self.track_index].name] = str(
                table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def populate(self) -> None:
        table = self.overview.query_one('#results', DataTable)
        table.clear(columns=True)
        self.table_width = table.size.width
        track = self.snapshots[self.track_index]
        rows = []
        hidden = 0
        current_time = now()
        for snapshot in track.products:
            product = snapshot.product
            for item in snapshot.checked or [None]:
                price = item.current.price if item and item.current else None
                if item and price is None and not self.show_missing:
                    hidden += 1
                    continue
                target, price_text, graphic, status = metrics(item, product.target_price, track.bounds, cents=False, detailed=False) if item else (
                    money(product.target_price, cents=False) if product.target_price is not None else '—',
                    '—', '—', 'NO LINKS')
                if item and item.warning:
                    status += ' ⚠'
                values = (product.name, str(product.desirability) if product.desirability is not None else '—',
                          retailer_label(item.retailer) if item else '—', target, price_text,
                          data_age(item, current_time) if item else '—', graphic, status)
                rows.append((overview_order(product, price), row_key(snapshot, item), snapshot, item, values))
        # Reserve a scrollbar gutter even before Textual has laid out the rows.
        # This avoids a horizontal scrollbar appearing when the vertical one does.
        widths = fitted_widths([row[-1] for row in rows], max(0, self.table_width - table.scrollbar_size_vertical))
        for title, width in zip(TABLE_COLUMNS, widths):
            label = Text(title)
            label.truncate(width, overflow='ellipsis')
            table.add_column(label, width=width, key=title)
        self.rows = {}
        table.product_groups = []
        product_groups: dict[str, int] = {}
        for _, key, snapshot, item, values in sorted(rows, key=lambda row: row[0]):
            table.product_groups.append(product_groups.setdefault(snapshot.key, len(product_groups)))
            self.rows[key] = (snapshot, item)
            cells = []
            for value, width in zip(values, widths):
                text = Text(value, no_wrap=True)
                text.truncate(width, overflow='ellipsis')
                cells.append(text)
            table.add_row(*cells, key=key, height=max(value.count('\n') + 1 for value in values))
        key = self.selections.get(track.name)
        if key in self.rows:
            table.move_cursor(row=list(self.rows).index(key))
        warnings = sum(bool(item.warning) for product in track.products for item in product.checked)
        message = (f'{track.name} · CACHED ONLY · loaded {current_time.astimezone():%H:%M:%S} · '
                   f'{warnings} warning(s) · {hidden} missing-price row(s) hidden (m to toggle)')
        if not rows:
            message += '\nNo visible results. Press m to show missing prices; run CLI check to fetch data.'
        self.overview.query_one('#status', Static).update(Text(message))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == 'tracks' and event.option_index != self.track_index:
            self.remember_selection()
            self.track_index = event.option_index
            self.populate()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.overview.query_one('#results', DataTable).focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        snapshot, item = self.rows[str(event.row_key.value)]
        self.push_screen(ProductScreen(self.snapshots[self.track_index], snapshot, item))

    def action_tracks(self) -> None:
        if isinstance(self.screen, ProductScreen):
            self.pop_screen()
        self.overview.query_one('#tracks', OptionList).focus()

    def action_missing(self) -> None:
        if isinstance(self.screen, ProductScreen):
            return
        self.remember_selection()
        self.show_missing = not self.show_missing
        self.populate()

    async def action_reload(self) -> None:
        self.remember_selection()
        try:
            snapshots = read_snapshots(self.configured_tracks, self.directory, self.track_bounds)
        except (OSError, ValueError) as exc:
            self.notify(str(exc), title='Cache reload failed', severity='error')
            return
        self.snapshots = snapshots
        self.populate()
        if isinstance(self.screen, ProductScreen):
            screen = self.screen
            screen.track = snapshots[self.track_index]
            screen.product = next(product for product in screen.track.products if product.key == screen.product.key)
            await screen.show_details()


def run_viewer(tracks, directory: Path, track_bounds) -> int:
    ResultsApp(tracks, directory, track_bounds).run()
    return 0
