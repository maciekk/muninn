from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from snipe import rate_limit
from snipe.cli import refresh_if_needed
from snipe.config import Product, Retailer
from snipe.store import Store


@pytest.fixture
def clock(monkeypatch):
    clock = Mock()
    clock.value = 100.0

    def sleep(seconds):
        clock.value += seconds

    clock.sleep.side_effect = sleep
    monkeypatch.setattr(rate_limit.time, "time", lambda: clock.value)
    monkeypatch.setattr(rate_limit.time, "sleep", clock.sleep)
    return clock


def test_persistent_spacing_and_vendor_independence(tmp_path, clock):
    store = Store(tmp_path)
    rate_limit.wait_for_vendor(store.db, "https://www.amazon.ca/dp/first", 2)
    clock.sleep.assert_not_called()
    clock.value += 0.5
    store.close()
    store = Store(tmp_path)
    try:
        rate_limit.wait_for_vendor(store.db, "https://amazon.ca/dp/second", 2)
        clock.sleep.assert_called_once_with(1.5)
        rate_limit.wait_for_vendor(store.db, "https://www.newegg.ca/p/first", 2)
        assert clock.sleep.call_count == 1
        clock.value += 5
        rate_limit.wait_for_vendor(store.db, "https://www.amazon.ca/dp/third", 2)
        assert clock.sleep.call_count == 1
    finally:
        store.close()


def test_zero_delay_and_validation(tmp_path, clock):
    store = Store(tmp_path)
    try:
        for _ in range(2):
            rate_limit.wait_for_vendor(store.db, "https://amazon.ca/a", 0)
        clock.sleep.assert_not_called()
        for value in (-1, float("nan"), float("inf")):
            with pytest.raises(ValueError, match="vendor delay"):
                rate_limit.wait_for_vendor(store.db, "https://amazon.ca/a", value)
    finally:
        store.close()


def test_cache_hits_do_not_claim_and_failures_do(tmp_path, clock):
    import requests

    store = Store(tmp_path)
    session = Mock()
    session.get.return_value.text = '<meta itemprop="price" content="10.00">'
    retailer = Retailer("Shop", "https://example.com/a", 7200)
    product = Product("Drive", None, (retailer,))
    at = datetime.now(timezone.utc)
    try:
        refresh_if_needed(store, product, retailer, session, at)
        clock.value += 0.5
        refresh_if_needed(store, product, retailer, session, at)
        clock.sleep.assert_not_called()
        assert session.get.call_count == 1
        assert store.db.execute("SELECT last_started_at FROM vendor_fetches").fetchone()[0] == 100.0
        other = Retailer("Different label, same vendor", "https://example.com/b", 7200)
        session.get.side_effect = requests.Timeout("timeout")
        result = refresh_if_needed(store, product, other, session, at, vendor_delay=3)
        assert result.warning
        clock.sleep.assert_called_once_with(2.5)
        assert store.db.execute("SELECT last_started_at FROM vendor_fetches").fetchone()[0] == 103.0
    finally:
        store.close()


def test_vendor_keys():
    assert rate_limit.vendor_key("https://WWW.AMAZON.CA/a") == "amazon.ca"
    assert rate_limit.vendor_key("https://m.amazon.ca/b") == "amazon.ca"
    assert rate_limit.vendor_key("https://notamazon.ca/a") == "notamazon.ca"
