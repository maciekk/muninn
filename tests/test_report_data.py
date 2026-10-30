from datetime import datetime, timezone
from decimal import Decimal

from muninn.config import Product, Retailer
from muninn.report_data import metrics, read_snapshots
from muninn.store import Store


def test_cache_snapshots_never_fetch_and_retain_failures(tmp_path, monkeypatch):
    import requests
    from muninn import browser, browser_bridge

    def forbidden(*args, **kwargs):
        raise AssertionError('cache viewer must never fetch')

    monkeypatch.setattr(requests.Session, 'get', forbidden)
    monkeypatch.setattr(browser, 'fetch_html', forbidden)
    monkeypatch.setattr(browser_bridge, 'fetch_html', forbidden)
    retailers = tuple(Retailer(name, f'https://example.com/{name}', 1, fetcher=fetcher)
                      for name, fetcher in [('failed', 'http'), ('missing', 'browser'),
                                            ('empty', 'manual'), ('low', 'bridge')])
    product = Product('Drive', Decimal(50), retailers)
    at = datetime.now(timezone.utc)
    store = Store(tmp_path)
    for retailer in (retailers[0], retailers[1], retailers[3]):
        store.record_success(retailer.url, product.name, retailer.name, Decimal(100), '', at)
    store.record_failure(retailers[0].url, product.name, 'failed', 'offline', at)
    store.record_success(retailers[1].url, product.name, 'missing', None, '', at)
    store.close()
    bounds = (Decimal(10), Decimal(500))
    snapshots = read_snapshots([('nvme', [product, Product('Candidate', None, ())])], tmp_path, {'nvme': bounds})
    track = snapshots[0]
    assert track.bounds == bounds
    checked = track.products[0].checked
    assert checked[0].current.price == Decimal(100)
    assert checked[0].latest_attempt.error == 'offline'
    assert checked[0].warning.startswith('FETCH_FAILED:')
    assert checked[1].warning.startswith('PRICE_NOT_FOUND:')
    assert checked[2].warning.startswith('NO_CACHE:')
    assert not any(item.refreshed for item in checked)
    assert metrics(checked[3], None)[-1] != 'NEW LOW!'
    assert track.products[1].checked == []
