from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest

from snipe import browser
from snipe.cli import refresh_if_needed
from snipe.config import Product, Retailer, load
from snipe.store import Store


def test_challenge_detection():
    for html in ('<input id="captchacharacters">', '<p>Click the button below to continue shopping</p>'):
        with pytest.raises(browser.FetchError):
            browser.check_page(html)
    browser.check_page('<h1>Drive</h1><span>$709.00</span>')


def test_browser_config(tmp_path):
    path = tmp_path / 'drive.yaml'
    path.write_text('name: Drive\nretailers:\n  - name: Shop\n    url: https://example.com\n    fetcher: browser\n')
    assert load(path)[0].retailers[0].fetcher == 'browser'
    path.write_text(path.read_text().replace('fetcher: browser', 'fetcher: invalid'))
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
    page.content.return_value = '<input id="captchacharacters">'
    with pytest.raises(browser.FetchError):
        browser.fetch_html('https://example.com', tmp_path)
    assert context.close.call_count == 2
