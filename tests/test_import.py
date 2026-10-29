from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest

from muninn import browser
from muninn.cli import import_saved_page, main, refresh_if_needed
from muninn.config import Product, Retailer, load
from muninn.store import Store


@pytest.fixture
def setup_import(tmp_path):
    retailer = Retailer('Newegg', 'https://www.newegg.ca/p/example', 7200, fetcher='manual')
    product = Product('Drive', None, (retailer,))
    html = tmp_path / 'saved.html'
    html.write_text('<h1>Drive</h1><div class="product-price"><span class="price-current">$709.00</span></div>')
    return [('nvme', [product])], retailer, product, html


def test_import_records_price_and_manual_checks_never_fetch(tmp_path, monkeypatch, setup_import):
    tracks, retailer, product, html = setup_import
    fetch = Mock()
    monkeypatch.setattr(browser, 'fetch_html', fetch)
    session = Mock()
    assert import_saved_page(tracks, tmp_path, retailer.url, html) == Decimal('709.00')
    store = Store(tmp_path)
    try:
        assert store.cache_html(retailer.url) == html.read_text()
        at = store.latest_attempt(retailer.url).fetched_at
        fresh = refresh_if_needed(store, product, retailer, session, at + timedelta(minutes=5))
        assert fresh.warning is None
        stale = refresh_if_needed(store, product, retailer, session, at + timedelta(hours=3))
        assert stale.current.price == Decimal('709.00')
        assert 'MANUAL_REFRESH_REQUIRED' in stale.warning
        assert len(store.successful(retailer.url)) == 1
        assert store.latest_attempt(retailer.url).success
        assert not stale.refreshed
    finally:
        store.close()
    fetch.assert_not_called()
    session.get.assert_not_called()


@pytest.mark.parametrize('content', ['<title>Just a moment...</title>', '<h1>No price</h1>'])
def test_rejected_import_preserves_cache_and_history(tmp_path, setup_import, content):
    tracks, retailer, _, html = setup_import
    import_saved_page(tracks, tmp_path, retailer.url, html)
    original = html.read_text()
    html.write_text(content)
    with pytest.raises(ValueError):
        import_saved_page(tracks, tmp_path, retailer.url, html)
    store = Store(tmp_path)
    try:
        assert store.cache_html(retailer.url) == original
        assert len(store.successful(retailer.url)) == 1
    finally:
        store.close()


def test_import_unknown_url_does_not_create_store(tmp_path, setup_import):
    tracks, _, _, html = setup_import
    with pytest.raises(ValueError, match='not configured'):
        import_saved_page(tracks, tmp_path, 'https://example.com/wrong', html)
    assert not (tmp_path / '.muninn').exists()


def test_manual_without_cache_does_not_fetch(tmp_path, setup_import):
    _, retailer, product, _ = setup_import
    session = Mock()
    store = Store(tmp_path)
    try:
        result = refresh_if_needed(store, product, retailer, session, datetime.now(timezone.utc))
        assert result.current is None
        assert 'MANUAL_REFRESH_REQUIRED' in result.warning
        assert store.latest_attempt(retailer.url) is None
    finally:
        store.close()
    session.get.assert_not_called()


def test_import_cli(tmp_path, setup_import, capsys):
    _, retailer, _, html = setup_import
    directory = tmp_path / 'tracks' / 'nvme'
    directory.mkdir(parents=True)
    path = directory / 'drive.yaml'
    path.write_text(f'name: Drive\nretailers:\n- name: Newegg\n  url: {retailer.url}\n  fetcher: manual\n')
    assert load(path)[0].retailers[0].fetcher == 'manual'
    with pytest.raises(SystemExit) as result:
        main(['import', 'nvme', '--tracks-dir', str(directory.parent), '--url', retailer.url, '--html', str(html)])
    assert result.value.code == 0
    assert 'Imported $709.00' in capsys.readouterr().out


@pytest.mark.parametrize('arguments', [['import'], ['check', '--html', 'saved.html'], ['check', '--url', 'https://example.com']])
def test_import_cli_argument_validation(arguments):
    with pytest.raises(SystemExit) as result:
        main(arguments)
    assert result.value.code == 2


def test_import_uses_custom_selector(tmp_path, setup_import):
    _, retailer, _, html = setup_import
    retailer = Retailer('Shop', retailer.url, 7200, price_selector='.special', fetcher='manual')
    product = Product('Drive', None, (retailer,))
    html.write_text('<span class="special">$123.45</span>')
    assert import_saved_page([('nvme', [product])], tmp_path, retailer.url, html) == Decimal('123.45')
