from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock

import pytest

from muninn import browser
from muninn.cli import clear_progress, progress, refresh_if_needed
from muninn.config import Product, Retailer, load
from muninn.store import Store


def test_challenge_detection():
    for html in (
        '<input id="captchacharacters">',
        '<p>Click the button below to continue shopping</p>',
        '<title>Just a moment...</title><p>Enable JavaScript and cookies to continue</p>',
    ):
        with pytest.raises(browser.FetchError):
            browser.check_page(html)
    browser.check_page('<h1>Drive</h1><span>$709.00</span>')


def test_refresh_reports_progress_to_stderr(tmp_path, capsys):
    store = Store(tmp_path)
    session = Mock()
    session.get.return_value.text = '<meta itemprop="price" content="10.00">'
    retailer = Retailer('Shop', 'https://example.com/drive', 7200)
    product = Product('Drive', None, (retailer,))
    try:
        refresh_if_needed(store, product, retailer, session, datetime.now(timezone.utc))
        output = capsys.readouterr()
        assert 'Refreshing Drive / Shop via http' in output.err
        assert 'Fetching Drive / Shop' in output.err
        assert 'Updated Drive / Shop: $10.00' in output.err
        assert not output.out
    finally:
        store.close()


def test_interactive_progress_replaces_one_status_line(monkeypatch):
    class Terminal(StringIO):
        def isatty(self):
            return True

    terminal = Terminal()
    monkeypatch.setattr('sys.stderr', terminal)
    progress('Fetching Drive / Shop…')
    progress('Updated Drive / Shop: $10.00')
    clear_progress()
    assert terminal.getvalue() == (
        '\r\033[2K[muninn] Fetching Drive / Shop…'
        '\r\033[2K[muninn] Updated Drive / Shop: $10.00'
        '\r\033[2K'
    )


def test_http_challenge_is_reported_before_status_error(tmp_path, monkeypatch):
    store = Store(tmp_path)
    session = Mock()
    response = session.get.return_value
    response.text = '<title>Just a moment...</title><p>Enable JavaScript and cookies to continue</p>'
    retailer = Retailer('Newegg Canada', 'https://www.newegg.ca/p/example', 7200)
    product = Product('Drive', None, (retailer,))
    try:
        result = refresh_if_needed(store, product, retailer, session, datetime.now(timezone.utc))
        assert 'security challenge' in result.warning
        response.raise_for_status.assert_not_called()
    finally:
        store.close()


def test_browser_config(tmp_path):
    path = tmp_path / 'drive.yaml'
    path.write_text('name: Drive\nretailers:\n  - name: Shop\n    url: https://example.com\n    fetcher: browser\n')
    assert load(path)[0].retailers[0].fetcher == 'browser'
    path.write_text(path.read_text().replace('fetcher: browser', 'fetcher: interactive'))
    assert load(path)[0].retailers[0].fetcher == 'interactive'
    path.write_text(path.read_text().replace('fetcher: interactive', 'fetcher: browser_interactive'))
    assert load(path)[0].retailers[0].fetcher == 'browser_interactive'
    path.write_text(path.read_text().replace('fetcher: browser_interactive', 'fetcher: invalid'))
    with pytest.raises(ValueError, match='fetcher'):
        load(path)


def test_browser_cache_and_failed_refresh(tmp_path, monkeypatch):
    store = Store(tmp_path)
    session = Mock()
    retailer = Retailer('Shop', 'https://example.com', 7200, '.price', 'browser')
    product = Product('Drive', None, (retailer,))
    fetch = Mock(return_value='<span class="price">$709.00</span>')
    monkeypatch.setattr(browser, 'fetch_html', fetch)
    at = datetime.now(timezone.utc)
    try:
        first = refresh_if_needed(store, product, retailer, session, at)
        assert first.current.price == Decimal('709.00')
        cached = store.cache_html(retailer.url)
        refresh_if_needed(store, product, retailer, session, at + timedelta(minutes=5))
        assert fetch.call_count == 1
        session.get.assert_not_called()
        fetch.side_effect = browser.FetchError('blocked')
        failed = refresh_if_needed(store, product, retailer, session, at + timedelta(hours=3))
        assert failed.current.price == Decimal('709.00')
        assert 'using stale data' in failed.warning
        assert not failed.refreshed
        assert store.cache_html(retailer.url) == cached
        assert not store.latest_attempt(retailer.url).success
        again = refresh_if_needed(store, product, retailer, session, at + timedelta(hours=3, minutes=1))
        assert 'using stale data' in again.warning
        assert fetch.call_count == 2
    finally:
        store.close()


def test_browser_interactive_retries_a_challenge_visibly(tmp_path, monkeypatch):
    store = Store(tmp_path)
    session = Mock()
    retailer = Retailer('Memory Express', 'https://www.memoryexpress.com/Products/MX1', 7200, '.price', 'browser_interactive')
    product = Product('Monitor', None, (retailer,))
    fetch = Mock(side_effect=[browser.VerificationRequired('challenge'), '<span class="price">$999.99</span>'])
    monkeypatch.setattr(browser, 'fetch_html', fetch)
    try:
        result = refresh_if_needed(store, product, retailer, session, datetime.now(timezone.utc))
        assert result.current.price == Decimal('999.99')
        assert fetch.call_args_list[0].kwargs['interactive'] is False
        assert fetch.call_args_list[1].kwargs['interactive'] is True
        session.get.assert_not_called()
    finally:
        store.close()


def test_browser_transport_cleanup(tmp_path, monkeypatch):
    # Exercise the browser lifecycle without downloading Chromium or contacting a retailer.
    import sys
    from types import ModuleType
    api = ModuleType('playwright.sync_api')
    manager = Mock()
    manager.__enter__ = Mock(return_value=manager)
    manager.__exit__ = Mock(return_value=False)
    context = manager.chromium.launch_persistent_context.return_value
    page = context.new_page.return_value
    page.goto.return_value.status = 200
    page.content.return_value = '<h1>Drive</h1>'
    api.sync_playwright = Mock(return_value=manager)
    api.Error = RuntimeError
    monkeypatch.setitem(sys.modules, 'playwright', ModuleType('playwright'))
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', api)
    assert browser.fetch_html('https://example.com', tmp_path) == '<h1>Drive</h1>'
    context.close.assert_called_once()
    prompt = Mock()
    monkeypatch.setattr('builtins.input', prompt)
    assert browser.fetch_html('https://example.com', tmp_path, interactive=True) == '<h1>Drive</h1>'
    assert manager.chromium.launch_persistent_context.call_args.kwargs['headless'] is False
    prompt.assert_called_once_with('\n--Press Return once page renders--')
    page.content.return_value = '<input id="captchacharacters">'
    with pytest.raises(browser.FetchError):
        browser.fetch_html('https://example.com', tmp_path)
    assert context.close.call_count == 3
