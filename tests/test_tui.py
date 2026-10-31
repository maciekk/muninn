import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

pytest.importorskip('textual')

from textual.widgets import DataTable, OptionList, Static
from muninn.config import Product, Retailer
from muninn.store import Store
from muninn.tui import ProductScreen, ResultsApp


@pytest.fixture(autouse=True)
def no_real_appearance_probe(monkeypatch):
    from muninn import tui

    async def unavailable():
        return None

    monkeypatch.setattr(tui, 'read_macos_appearance', unavailable)


def make_app(tmp_path):
    retailers = (Retailer('Expensive [shop]', 'https://example.com/a', 1),
                 Retailer('Cheap', 'https://example.com/b', 1),
                 Retailer('Empty', 'https://example.com/c', 1))
    products = [Product('Same name', Decimal(200), retailers, ('Buying notes [literal]',),
                        Decimal(8), tmp_path / 'one.yaml'),
                Product('Same name', None, (), source_path=tmp_path / 'two.yaml')]
    tracks = [('first', products), ('second', [Product('Other', None, ())])]
    store = Store(tmp_path)
    for retailer, price in zip(retailers, [300, 150]):
        store.record_success(retailer.url, 'Same name', retailer.name, Decimal(price), '', datetime.now(timezone.utc))
    store.close()
    return ResultsApp(tracks, tmp_path, {})


def test_initial_results_sidebar_navigation_and_details(tmp_path):
    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            table = app.query_one('#results', DataTable)
            assert table.row_count == 3  # two priced retailers and a no-link candidate
            assert table.has_focus
            assert 'Cheap' in str(table.get_row_at(0))
            await pilot.press('enter')
            assert isinstance(app.screen, ProductScreen)
            screen = app.screen
            assert screen.selected_url == 'https://example.com/b'
            report = screen.query_one('#retailer-details', Static)
            report_text = str(report.render())
            assert 'Cheap' in report_text
            assert 'Expensive' not in report_text
            assert 'https://example.com/a' not in report_text
            assert 'https://example.com/c' not in report_text
            assert report.styles.border.top[0] == ''
            assert report.renderable.spans  # Semantic styles reach the widget intact.
            assert screen.query_one('#product-heading', Static).renderable.spans
            assert 'Buying notes [literal]' in str(screen.query_one('#product-heading', Static).render())
            assert screen.query_one('#details').scroll_y == 0
            await pilot.press('escape', 'down')
            assert table.cursor_row == 1
            await pilot.press('t', 'down')
            assert app.track_index == 1
            assert table.row_count == 1
            await pilot.press('up', 'enter')
            assert app.track_index == 0
            assert table.cursor_row == 1
            assert table.has_focus
            await pilot.press('m')
            assert table.row_count == 4
            await pilot.press('q')
    asyncio.run(scenario())


def test_reload_overview_and_details_and_narrow_layout(tmp_path):
    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=(60, 20)) as pilot:
            table = app.query_one('#results', DataTable)
            assert table.row_count == 3
            await pilot.press('enter')
            store = Store(tmp_path)
            store.record_failure('https://example.com/b', 'Same name', 'Cheap', 'offline', datetime.now(timezone.utc))
            store.close()
            await pilot.press('r')
            assert isinstance(app.screen, ProductScreen)
            text = str(app.screen.query_one('#retailer-details', Static).render())
            assert 'offline' in text
            assert 'WARNING' in text
            await pilot.press('escape')
            assert table.cursor_row == 0
            assert app.query_one('#tracks', OptionList).region.width < 25
    asyncio.run(scenario())


def test_empty_cache_can_reveal_and_open_missing_rows(tmp_path):
    async def scenario():
        product = Product('Uncached', None, (Retailer('Shop', 'https://example.com/uncached', 1),))
        app = ResultsApp([('empty', [product])], tmp_path, {})
        async with app.run_test(size=(80, 24)) as pilot:
            table = app.query_one('#results', DataTable)
            assert table.row_count == 0
            assert 'No visible results' in str(app.query_one('#status', Static).render())
            await pilot.press('m')
            assert table.row_count == 1
            await pilot.press('enter')
            assert isinstance(app.screen, ProductScreen)
            assert 'NO_CACHE' in str(app.screen.query_one('#retailer-details', Static).render())
    asyncio.run(scenario())


def test_mouse_track_switch_and_row_drilldown(tmp_path):
    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.click('#tracks', offset=(2, 2))
            assert app.track_index == 1
            await pilot.click('#results', offset=(5, 1))
            assert isinstance(app.screen, ProductScreen)
            assert app.screen.product.product.name == 'Other'
            await pilot.press('t')
            await pilot.pause()
            assert not isinstance(app.screen, ProductScreen)
            assert app.query_one('#tracks', OptionList).has_focus
    asyncio.run(scenario())


def test_columns_fit_viewport_and_resize_without_losing_product_identity(tmp_path):
    async def scenario():
        retailer = Retailer('A very long retailer name [marketplace]', 'https://example.com/long', 1)
        name = 'A very long product name with capacity, model, and specifications '
        products = [Product(f'{name}{index:02}', Decimal(200), (retailer,)) for index in range(30)]
        store = Store(tmp_path)
        store.record_success(retailer.url, name, retailer.name, Decimal(150), '', datetime.now(timezone.utc))
        store.close()
        app = ResultsApp([('first', products), ('second', [])], tmp_path, {})
        async with app.run_test(size=(120, 24)) as pilot:
            table = app.query_one('#results', DataTable)
            await pilot.press('down', 'down')
            for width in (80, 60, 160, 100):
                await pilot.resize_terminal(width, 24)
                await pilot.pause()
                assert len(table.columns) == 8
                assert sum(column.width + 2 * table.cell_padding for column in table.ordered_columns) <= table.size.width
                assert not table.show_horizontal_scrollbar
                assert table.cursor_row == 2
            assert table.get_row_at(2)[0].plain.endswith('…')
            await pilot.press('enter')
            assert app.screen.product.product.name == f'{name}02'
            assert f'{name}02' in str(app.screen.query_one('#product-heading', Static).render())
            await pilot.resize_terminal(80, 24)
            await pilot.press('escape')
            await pilot.pause()
            assert not table.show_horizontal_scrollbar
            assert table.cursor_row == 2
            assert sum(column.width + 2 * table.cell_padding for column in table.ordered_columns) <= table.size.width
    asyncio.run(scenario())


def test_zebra_stripes_group_retailers_by_product_identity(tmp_path):
    from rich.style import Style

    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            table = app.query_one('#results', DataTable)

            def check_stripes():
                styles = {}
                groups = {}
                for index, (product, _) in enumerate(app.rows.values()):
                    style = table._get_row_style(index, Style())
                    if product.key in styles:
                        assert style == styles[product.key]
                        assert table.product_groups[index] == groups[product.key]
                    styles[product.key] = style
                    groups[product.key] = table.product_groups[index]
                # These products share a display name, but have different source
                # paths: each must still receive its own stripe.
                assert len(styles) == 2
                first, second = styles.values()
                assert first != second

            check_stripes()
            assert table.product_groups == [0, 0, 1]
            await pilot.press('m')
            check_stripes()
            assert table.product_groups == [0, 0, 0, 1]
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            check_stripes()
            await pilot.press('t', 'down', 'up', 'enter', 'r')
            check_stripes()
    asyncio.run(scenario())


def test_listing_report_retains_full_history_and_configuration(tmp_path):
    from datetime import timedelta
    from muninn.report_data import Checked, ProductSnapshot, TrackSnapshot
    from muninn.store import Observation
    from muninn.tui import retailer_details

    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    history = [Observation(at + timedelta(days=index), Decimal(price), True, None)
               for index, price in enumerate([1, 9999, *range(100, 143)])]
    retailer = Retailer('Selected [shop]', 'https://example.com/selected', 7200,
                        price_selector='.price', fetcher='browser', retry_seconds=3600,
                        seller='Marketplace Seller', seller_type='third_party', browser_engine='firefox')
    product = ProductSnapshot(Product('Drive', Decimal(150), (retailer,)), [])
    latest = Observation(at + timedelta(days=46), None, False, 'Detailed failure message')
    checked = Checked(retailer, history, False, 'FETCH_FAILED: detailed warning', latest)
    report = retailer_details(checked, TrackSnapshot('nvme', [product], (Decimal(50), Decimal(500))), product).plain
    assert 'Historical low:          $1.00' in report
    assert 'Historical high:         $9,999.00' in report
    assert 'Priced observations:     45' in report
    assert 'last 40 of 45 observations' in report
    for observation in history:
        stamp = observation.fetched_at.astimezone().isoformat(sep=' ', timespec='seconds')
        row = next(line for line in report.splitlines() if line.startswith(stamp))
        assert f'${observation.price:,.2f}' in row
        assert row.count('*') == 1
    for value in ('Selected [shop] [3P]', 'Marketplace Seller', 'third party',
                  'https://example.com/selected', 'firefox', '.price', '7200 seconds',
                  '3600 seconds', 'Detailed failure message', 'FETCH_FAILED: detailed warning'):
        assert value in report


def test_no_link_candidate_has_product_report_without_listing(tmp_path):
    async def scenario():
        notes = 'Detailed buying rationale. ' * 100
        product = Product('Candidate', None, (), notes=(notes,))
        app = ResultsApp([('track', [product])], tmp_path, {})
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press('enter')
            assert isinstance(app.screen, ProductScreen)
            heading = str(app.screen.query_one('#product-heading', Static).render())
            assert notes in heading
            assert 'NO LINKS' in heading
            assert not app.screen.query_one('#retailer-details', Static).display
    asyncio.run(scenario())


def test_details_use_semantic_emphasis_without_parsing_user_markup(tmp_path):
    from rich.console import Console
    from rich.style import Style
    from muninn.report_data import Checked, ProductSnapshot, TrackSnapshot
    from muninn.store import Observation
    from muninn.tui import product_details, retailer_details

    retailer = Retailer('Shop [literal]', 'https://example.com/listing?ref=[link]', 7200)
    product = ProductSnapshot(Product('Drive [red]literal[/red]', Decimal(150), (retailer,),
                                      notes=('[bold]Keep this note literal[/bold]',),
                                      source_path=tmp_path / 'product.yaml'), [])
    track = TrackSnapshot('nvme', [product], (Decimal(50), Decimal(500)))
    observation = Observation(datetime.now(timezone.utc), Decimal(100), True, None)
    attempt = Observation(datetime.now(timezone.utc), None, False, 'Connection failed')
    checked = Checked(retailer, [observation], False, 'FETCH_FAILED: stale cached price', attempt)
    product.checked = [checked]
    heading = product_details(track, product)
    report = retailer_details(checked, track, product)
    console = Console()

    def style_at(text, fragment):
        return text.get_style_at_offset(console, text.plain.index(fragment))

    assert '[red]literal[/red]' in heading.plain
    assert '[bold]Keep this note literal[/bold]' in heading.plain
    assert style_at(heading, 'Drive').bold
    assert style_at(heading, 'Cached listing report').dim
    assert style_at(heading, str(tmp_path / 'product.yaml')).dim
    assert not style_at(heading, '[bold]Keep').bold
    assert style_at(report, 'PRICE SUMMARY') == Style.parse('bold cyan')
    assert style_at(report, 'Shop [literal]').bold
    assert style_at(report, retailer.url).dim
    assert style_at(report, '7200 seconds').dim
    assert style_at(report, '$100.00') == Style.parse('bold blue')
    assert style_at(report, 'TARGET MET!') == Style.parse('bold green')
    assert style_at(report, 'Historical low:').color is None
    assert style_at(report, 'failed') == Style.parse('bold red')
    assert style_at(report, 'Connection failed').color == Style.parse('red').color
    assert style_at(report, 'WARNING') == Style.parse('bold yellow')
    assert style_at(report, 'FETCH_FAILED:').color == Style.parse('yellow').color
    ledger = report[report.plain.index('Timestamp (local'):]
    assert style_at(ledger, observation.fetched_at.astimezone().isoformat(sep=' ', timespec='seconds')).dim
    assert style_at(ledger, '$100.00') == Style.parse('bold blue')


@pytest.mark.parametrize('status, expected', [
    ('TARGET MET!', 'bold green'), ('NEW LOW!', 'bold green'),
    ('↓ $20.00', 'bold green'), ('↑ $20.00', 'bold red'),
    ('NO PRICE', 'bold yellow'), ('—', 'dim'),
])
def test_detail_status_emphasis(status, expected):
    from muninn.tui import detail_status_style
    assert detail_status_style(status) == expected


def test_ledger_aligns_columns_and_plots_prices_on_one_axis():
    from datetime import timedelta
    from muninn.store import Observation
    from muninn.tui import observation_ledger

    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    history = [Observation(at + timedelta(days=index), price, True, None)
               for index, price in enumerate(map(Decimal, ['9', '5004.50', '10000']))]
    for width in (54, 74, 100):
        lines = observation_ledger(history, width=width).plain.splitlines()
        header = next(line for line in lines if line.startswith('Timestamp (local'))
        rows = [line for line in lines if '|' in line]
        assert len(rows) == len(history)
        price_right = header.index('Price') + len('Price')
        plot_left = header.index('Price plot')
        positions = []
        for row, observation in zip(rows, history):
            assert len(row) <= width
            assert row.index('|') == plot_left
            assert row[:plot_left].rstrip().endswith(f'${observation.price:,.2f}')
            assert len(row[:plot_left].rstrip()) == price_right
            positions.append(row.index('*') - plot_left - 1)
        plot_width = len(rows[0]) - plot_left - 2
        assert positions == [0, plot_width // 2, plot_width - 1]


def test_legacy_reset_timestamp_is_unknown_without_hiding_price(tmp_path):
    from muninn.report_data import Checked, ProductSnapshot, TrackSnapshot, data_age
    from muninn.store import Observation
    from muninn.tui import retailer_details

    retailer = Retailer('Shop', 'https://example.com/legacy', 7200)
    product = ProductSnapshot(Product('Drive', None, (retailer,)), [])
    # This is the exact timestamp written by the old reset command; local time
    # conversion used to display it as 1969-12-31 in negative UTC offsets.
    observation = Observation(datetime(1970, 1, 1, tzinfo=timezone.utc), Decimal(100), True, None)
    checked = Checked(retailer, [observation], False, None, observation)
    report = retailer_details(checked, TrackSnapshot('nvme', [product], (Decimal(50), Decimal(500))), product).plain
    assert '1969' not in report and '1970' not in report
    assert 'unknown (legacy reset)' in report
    assert 'prices remain in recording order' in report
    assert 'Priced observations:     1' in report
    assert '$100.00' in report
    assert report.count('*') == 1
    assert data_age(checked, datetime.now(timezone.utc)) == '—'


def test_detail_ledger_plot_resizes_with_terminal(tmp_path):
    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press('enter')
            widths = []
            for terminal_width in (120, 60, 80):
                await pilot.resize_terminal(terminal_width, 24)
                await pilot.pause()
                report = app.screen.query_one('#retailer-details', Static).renderable.plain
                row = next(line for line in report.splitlines() if '|' in line)
                assert len(row) <= terminal_width - 6
                widths.append(len(row))
            assert widths[0] > widths[1]
            assert widths[2] > widths[1]
    asyncio.run(scenario())


def test_system_appearance_updates_live_without_losing_navigation(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from rich.console import Console
    from muninn import tui
    from textual.containers import VerticalScroll

    async def scenario():
        probe = AsyncMock(return_value='light')
        monkeypatch.setattr(tui, 'read_macos_appearance', probe)
        frozen_time = datetime.now(timezone.utc)
        monkeypatch.setattr(tui, 'now', lambda: frozen_time)
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            assert app.theme == 'textual-light'
            assert probe.await_count >= 1  # initial check, not just the timer
            table = app.query_one('#results', DataTable)
            await pilot.press('down', 'enter')
            screen = app.screen
            scroll = screen.query_one('#details', VerticalScroll)
            scroll.scroll_to(y=12, animate=False)
            await pilot.pause()
            position = scroll.scroll_y
            report = screen.query_one('#retailer-details', Static)
            before = report.renderable
            before_color = before.get_style_at_offset(Console(), before.plain.index('$300.00')).color
            assert before_color.get_truecolor().hex == tui.DETAIL_PALETTES['light']['blue']

            probe.return_value = 'dark'
            app.poll_appearance()  # same callback used by the five-second timer
            await pilot.pause()
            assert app.theme == 'textual-dark'
            assert app.screen is screen
            assert scroll.scroll_y == position
            after = report.renderable
            assert after.plain == before.plain
            after_color = after.get_style_at_offset(Console(), after.plain.index('$300.00')).color
            assert after_color.get_truecolor().hex == tui.DETAIL_PALETTES['dark']['blue']
            assert before_color != after_color

            probe.return_value = None
            await app.sync_appearance()
            assert app.theme == 'textual-dark'  # failed probes retain the last theme
            probe.return_value = 'light'
            app.poll_appearance()
            await pilot.pause()
            assert app.theme == 'textual-light'
            assert scroll.scroll_y == position
            await pilot.press('escape')
            assert table.cursor_row == 1
            assert table.product_groups == [0, 0, 1]
    asyncio.run(scenario())


def test_unchanged_system_appearance_allows_manual_theme_choice(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from muninn import tui

    async def scenario():
        probe = AsyncMock(return_value='light')
        monkeypatch.setattr(tui, 'read_macos_appearance', probe)
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.theme == 'textual-light'
            app.theme = 'nord'
            await app.sync_appearance()
            assert app.theme == 'nord'
            probe.return_value = 'dark'
            await app.sync_appearance()
            assert app.theme == 'textual-dark'
    asyncio.run(scenario())


@pytest.mark.parametrize('dark', [False, True])
def test_report_palette_preserves_content_and_emphasis(dark):
    from rich.console import Console
    from rich.text import Text
    from muninn.tui import adapt_report_palette, DETAIL_PALETTES

    report = Text()
    report.append('Heading [literal]', style='bold cyan')
    report.append('\nhttps://example.com', style='dim')
    report.append('\nWarning', style='bold yellow')
    original_spans = report.spans.copy()
    themed = adapt_report_palette(report, dark=dark)
    assert themed.plain == report.plain
    assert report.spans == original_spans
    assert themed.spans != original_spans
    style = themed.get_style_at_offset(Console(), 0)
    assert style.bold
    assert style.color.get_truecolor().hex == DETAIL_PALETTES['dark' if dark else 'light']['cyan']
    assert themed.get_style_at_offset(Console(), themed.plain.index('https://')).dim


def test_slow_appearance_check_does_not_block_navigation(tmp_path, monkeypatch):
    from muninn import tui

    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_probe():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(tui, 'read_macos_appearance', slow_probe)
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await started.wait()
            await pilot.press('down', 'enter')
            assert isinstance(app.screen, ProductScreen)
            assert app.screen.selected_name == 'Expensive [shop]'
            await pilot.press('escape', 'q')
        assert cancelled.is_set()
    asyncio.run(scenario())


@pytest.mark.parametrize('theme', ['textual-light', 'textual-dark'])
def test_product_masthead_distinguishes_it_from_unframed_listing(tmp_path, theme):
    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            app.theme = theme
            await pilot.press('enter')
            await pilot.pause()
            product = app.screen.query_one('#product-heading', Static)
            listing = app.screen.query_one('#retailer-details', Static)
            assert product.styles.border.left[0] == 'thick'
            assert all(edge[0] == '' for edge in (
                product.styles.border.top, product.styles.border.right, product.styles.border.bottom))
            assert product.styles.background.a > 0
            assert product.styles.background != listing.styles.background
            assert product.styles.margin.bottom == 2
            assert all(edge[0] == '' for edge in listing.styles.border)
            assert 'Buying notes [literal]' in product.renderable.plain
            assert 'Cheap' in listing.renderable.plain
            assert 'Expensive' not in listing.renderable.plain
    asyncio.run(scenario())


def test_diagonal_trackpad_input_does_not_reverse_details(tmp_path):
    from textual._xterm_parser import XTermParser
    from textual.containers import VerticalScroll
    from muninn.tui_compat import vertical_wheel_only

    async def scenario():
        app = make_app(tmp_path)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.press('enter')
            scroll = app.screen.query_one('#details', VerticalScroll)
            parser = XTermParser()

            async def wheel(*buttons):
                for code in buttons:
                    for event in parser.feed(f'\x1b[<{code};10;10M'):
                        scroll._forward_event(event)
                await pilot.pause()

            scroll.scroll_to(y=10, animate=False, immediate=True)
            await wheel(65, 66, 67, 66, 65)
            assert scroll.scroll_y == 10 + 2 * app.scroll_sensitivity_y
            for boundary, outward, inward, expected in (
                (scroll.max_scroll_y, 65, 64, scroll.max_scroll_y - app.scroll_sensitivity_y),
                (0, 64, 65, app.scroll_sensitivity_y),
            ):
                scroll.scroll_to(y=boundary, animate=False, immediate=True)
                await wheel(*([outward, 66, 67] * 5))
                assert scroll.scroll_y == scroll.scroll_target_y == boundary
                assert app.screen.scroll_y == 0
                await wheel(inward)
                assert scroll.scroll_y == expected

    with vertical_wheel_only():
        asyncio.run(scenario())


def test_wrapped_listing_click_and_keyboard_open_full_url(tmp_path, monkeypatch):
    async def scenario():
        url = 'https://example.com/' + 'long-path-segment/' * 8 + '?a=1&b=[literal]'
        retailer = Retailer('Shop', url, 1)
        product = Product('Product', None, (retailer,))
        app = ResultsApp([('track', [product])], tmp_path, {})
        opened = []
        monkeypatch.setattr(app, 'open_url', opened.append)
        async with app.run_test(size=(60, 40)) as pilot:
            await pilot.press('m', 'enter')
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ProductScreen)
            report = screen.query_one('#retailer-details', Static).renderable
            assert url in report.plain
            # Inspect actual rendered cells, not just the unwrapped Text spans.
            segments = {}
            for y in range(screen.size.height):
                for x in range(screen.size.width):
                    style = screen.get_style_at(x, y)
                    if style.meta.get('@click') == 'screen.open_listing':
                        segments.setdefault(y, (x, y))
            assert len(segments) >= 3
            for point in segments.values():
                await pilot.click(offset=point)
                assert opened[-1] == url
            assert len(opened) == len(segments)
            await pilot.press('o')
            assert opened == [url] * (len(segments) + 1)
            # A listing removed on reload must not leave a stale open action.
            screen.product.checked.clear()
            await screen.show_details()
            await pilot.press('o')
            assert len(opened) == len(segments) + 1
    asyncio.run(scenario())


def test_open_listing_without_retailer_is_noop(tmp_path, monkeypatch):
    async def scenario():
        app = ResultsApp([('track', [Product('Candidate', None, ())])], tmp_path, {})
        opened = []
        monkeypatch.setattr(app, 'open_url', opened.append)
        async with app.run_test() as pilot:
            await pilot.press('enter', 'o')
            assert isinstance(app.screen, ProductScreen)
            assert opened == []
    asyncio.run(scenario())
