from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from snipe.config import load, parse_duration
from snipe.extract import extract_price
from snipe.store import Store


def test_duration_and_config(tmp_path: Path):
    config = tmp_path / "snipe.yaml"
    config.write_text("""products:
  - name: Drive
    target_price: 100
    refresh_after: 2h
    retailers:
      - name: Newegg
        url: https://www.newegg.ca/p/example
        refresh_after: 30m
        retry_after: 15m
        seller: Marketplace Shop
        seller_type: third_party
""")
    product = load(config)[0]
    assert parse_duration("2h") == 7200
    assert product.target_price == Decimal("100")
    assert product.retailers[0].refresh_seconds == 1800
    assert product.retailers[0].retry_seconds == 900
    assert product.retailers[0].seller == "Marketplace Shop"
    assert product.retailers[0].seller_type == "third_party"


def test_single_product_file(tmp_path: Path):
    config = tmp_path / "drive.yaml"
    config.write_text("""name: Drive\nnotes:\n  - Fast and efficient\nretailers:\n  - name: Shop\n    url: https://shop.example/drive\n""")
    product = load(config)[0]
    assert product.name == "Drive"
    assert product.notes == ("Fast and efficient",)


def test_extract_json_ld_and_selector():
    assert extract_price('<script type="application/ld+json">{"offers":{"price":"123.45"}}</script>', "https://example.test") == Decimal("123.45")
    assert extract_price('<span class="cost">CAD $1,299.99</span>', "https://example.test", ".cost") == Decimal("1299.99")


def test_failed_attempt_uses_shorter_retry_window(tmp_path: Path):
    from unittest.mock import Mock
    import requests
    from snipe.cli import refresh_if_needed
    from snipe.config import Product, Retailer

    store = Store(tmp_path)
    session = Mock()
    session.get.side_effect = requests.Timeout("blocked")
    retailer = Retailer("Shop", "https://example.test/a", 24 * 3600, retry_seconds=3600)
    product = Product("Drive", None, (retailer,))
    at = datetime.now(timezone.utc)
    try:
        store.record_failure(retailer.url, product.name, retailer.name, "blocked", at)
        # A failure is not retried at the normal 24-hour interval.
        refresh_if_needed(store, product, retailer, session, at + timedelta(minutes=59))
        assert session.get.call_count == 0
        refresh_if_needed(store, product, retailer, session, at + timedelta(hours=1, seconds=1))
        assert session.get.call_count == 1
    finally:
        store.close()


def test_store_retains_success_after_failure(tmp_path: Path):
    store = Store(tmp_path)
    at = datetime.now(timezone.utc)
    cache_path, _ = store.write_html("https://example.test/a", "price page", at)
    store.record_success("https://example.test/a", "Product", "Shop", Decimal("99.99"), cache_path, at)
    store.record_failure("https://example.test/a", "Product", "Shop", "timeout", at + timedelta(hours=2))
    assert store.cache_html("https://example.test/a") == "price page"
    assert [x.price for x in store.successful("https://example.test/a")] == [Decimal("99.99")]
    assert store.latest_attempt("https://example.test/a").success is False
    store.close()


def test_reset_marks_matching_retailers_due(tmp_path: Path):
    from snipe.cli import reset_refreshes
    from snipe.config import Product, Retailer

    newegg = Retailer("Newegg", "https://www.newegg.ca/p/example", 86400)
    amazon = Retailer("Amazon", "https://www.amazon.ca/dp/example", 86400)
    product = Product("Drive", None, (newegg, amazon))
    store = Store(tmp_path)
    at = datetime.now(timezone.utc)
    for retailer in (newegg, amazon):
        cache_path, _ = store.write_html(retailer.url, "page", at)
        store.record_success(retailer.url, product.name, retailer.name, Decimal("100"), cache_path, at)
    store.close()

    assert reset_refreshes([("nvme", [product])], tmp_path, "newegg.ca") == (1, 0)
    store = Store(tmp_path)
    try:
        assert store.latest_attempt(newegg.url).fetched_at.year == 1970
        assert store.latest_attempt(amazon.url).fetched_at == at
    finally:
        store.close()


def test_marketplace_seller_is_flagged_in_reports(capsys):
    from snipe.cli import Checked, product_report
    from snipe.config import Product, Retailer

    retailer = Retailer("Newegg Canada", "https://newegg.ca/p/example", 7200,
                        seller="Marketplace Shop", seller_type="third_party")
    product = Product("Drive", None, (retailer,))
    item = Checked(retailer, [], False, None)
    product_report("nvme", product, [item], long=True)
    output = capsys.readouterr().out
    assert "Newegg Canada [3P]" in output
    assert "Seller: Marketplace Shop (third party)" in output


def test_report_detail_modes(capsys):
    from snipe.cli import product_report
    from snipe.config import Product

    product = Product("Drive", None, (), ("Buying rationale",))
    product_report("nvme", product, [])
    short = capsys.readouterr().out
    assert "Vendor" in short
    assert "Product Details" not in short
    assert "Buying rationale" not in short

    product_report("nvme", product, [], long=True)
    detailed = capsys.readouterr().out
    assert "Product Details" in detailed
    assert "Buying rationale" in detailed


def test_short_report_only_shows_overview(tmp_path, capsys):
    from snipe.cli import report
    from snipe.config import Product

    tracks = [("nvme", [Product("Drive", None, (), ("Buying rationale",))])]
    report(tracks, tmp_path)
    short = capsys.readouterr().out
    assert "TRACK: NVME" in short
    assert "NVME: DRIVE" not in short
    assert "Product Details" not in short
    assert "WARNINGS" in short
    assert "  - NO_VENDORS: Drive" in short

    report(tracks, tmp_path, long=True)
    detailed = capsys.readouterr().out
    assert "TRACK: NVME" in detailed
    assert "NVME: DRIVE" in detailed
    assert "Product Details" in detailed
    assert "Buying rationale" in detailed


def test_desirability_validation(tmp_path):
    import pytest

    path = tmp_path / "drive.yaml"
    for rating in ("9.5", "0", "10"):
        path.write_text(f"name: Drive\ndesirability: {rating}\nretailers: []\n")
        assert load(path)[0].desirability == Decimal(rating)
    for rating in ("-1", "10.1", "NaN", "Infinity", "true", "excellent"):
        path.write_text(f"name: Drive\ndesirability: {rating}\nretailers: []\n")
        with pytest.raises(ValueError, match="desirability"):
            load(path)


def test_overview_rating_order(capsys):
    from snipe.cli import Checked, track_overview
    from snipe.config import Product, Retailer
    from snipe.store import Observation

    def entry(name, rating, price):
        retailer = Retailer("Shop", "https://example.com/" + name, 7200)
        product = Product(name, None, (retailer,), desirability=Decimal(rating) if rating is not None else None)
        history = [Observation(datetime.now(timezone.utc), Decimal(price), True, None)] if price else []
        return product, [Checked(retailer, history, False, None)]

    track_overview("nvme", [entry("Unrated", None, "1"), entry("Lower", "8", "10"),
                           entry("Premium", "9.5", "600"), entry("BetterDeal", "9.5", "500"),
                           entry("MissingPrice", "9.5", None)])
    output = capsys.readouterr().out
    assert "Rating" in output
    assert output.index("BetterDeal") < output.index("MissingPrice") < output.index("Premium") < output.index("Lower") < output.index("Unrated")


def test_overview_groups_retailers_by_product_name(capsys):
    from snipe.cli import Checked, track_overview
    from snipe.config import Product, Retailer
    from snipe.store import Observation

    timestamp = datetime.now(timezone.utc)
    alpha_retailers = (
        Retailer("Expensive Shop", "https://example.com/alpha-expensive", 7200),
        Retailer("Cheap Shop", "https://example.com/alpha-cheap", 7200),
    )
    alpha = Product("Alpha", None, alpha_retailers, desirability=Decimal("9"))
    beta_retailer = Retailer("Middle Shop", "https://example.com/beta", 7200)
    beta = Product("Beta", None, (beta_retailer,), desirability=Decimal("9"))
    results = [
        (alpha, [
            Checked(alpha_retailers[0], [Observation(timestamp, Decimal("900"), True, None)], False, None),
            Checked(alpha_retailers[1], [Observation(timestamp, Decimal("100"), True, None)], False, None),
        ]),
        (beta, [Checked(beta_retailer, [Observation(timestamp, Decimal("500"), True, None)], False, None)]),
    ]

    track_overview("nvme", results)
    rows = [line for line in capsys.readouterr().out.splitlines() if "Shop" in line]
    assert ["Alpha" in row for row in rows] == [True, True, False]


def test_candidate_without_links(capsys):
    from snipe.cli import track_overview
    from snipe.config import Product

    track_overview("nvme", [(Product("Pending", Decimal(575), (), desirability=Decimal(9)), [])])
    output = capsys.readouterr().out
    assert "Pending" in output
    assert "NO LINKS" in output
    assert "$575" in output
