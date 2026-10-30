from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from muninn.config import load, parse_duration
from muninn.extract import extract_price
from muninn.store import Store


def test_duration_and_config(tmp_path: Path):
    config = tmp_path / "muninn.yaml"
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


def test_store_migrates_legacy_cache(tmp_path: Path):
    legacy_page = tmp_path / ".snipe" / "v1" / "pages" / "cached.html"
    legacy_page.parent.mkdir(parents=True)
    legacy_page.write_text("cached price")

    store = Store(tmp_path)

    assert store.root == tmp_path / ".muninn" / "v1"
    assert (store.pages / "cached.html").read_text() == "cached price"
    assert not (tmp_path / ".snipe" / "v1").exists()
    store.close()


def test_single_product_file(tmp_path: Path):
    config = tmp_path / "drive.yaml"
    config.write_text("""name: Drive\nnotes:\n  - Fast and efficient\nretailers:\n  - name: Shop\n    url: https://shop.example/drive\n""")
    product = load(config)[0]
    assert product.name == "Drive"
    assert product.notes == ("Fast and efficient",)


def test_extract_json_ld_and_selector():
    assert extract_price('<script type="application/ld+json">{"offers":{"price":"123.45"}}</script>', "https://example.test") == Decimal("123.45")
    assert extract_price('<span class="cost">CAD $1,299.99</span>', "https://example.test", ".cost") == Decimal("1299.99")


def test_extract_lg_unformatted_json_ld_price():
    html = '''
        <script type="application/ld+json">
          {"@type":"Product","offers":{"priceCurrency":"CAD","price":"1399.99"}}
        </script>
    '''
    assert extract_price(html, "https://www.lg.com/ca_en/monitors/gaming/32gx870b-b/") == Decimal("1399.99")


def test_extract_dell_canada_price():
    html = '<span class="ps-dell-price-amount">CAD $1,349.99</span>'
    assert extract_price(html, "https://www.dell.com/en-ca/shop/product") == Decimal("1349.99")


def test_extract_memory_express_grand_total_price():
    html = '<div class="GrandTotal c-capr-pricing__grand-total"><div><small>Only</small>$1,099.99</div></div>'
    assert extract_price(html, "https://www.memoryexpress.com/Products/MX00128835") == Decimal("1099.99")


def test_extract_best_buy_primary_price_before_recommendations():
    html = '''
        <span data-automation="product-price">$1199.99 $1,199.99</span>
        <meta itemprop="price" content="349.99">
    '''
    assert extract_price(html, "https://www.bestbuy.ca/en-ca/product/example") == Decimal("1199.99")


def test_failed_attempt_uses_shorter_retry_window(tmp_path: Path):
    from unittest.mock import Mock
    import requests
    from muninn.cli import refresh_if_needed
    from muninn.config import Product, Retailer

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
    from muninn.cli import reset_refreshes
    from muninn.config import Product, Retailer

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
    from muninn.cli import Checked, product_report
    from muninn.config import Product, Retailer

    retailer = Retailer("Newegg Canada", "https://newegg.ca/p/example", 7200,
                        seller="Marketplace Shop", seller_type="third_party")
    product = Product("Drive", None, (retailer,))
    item = Checked(retailer, [], False, None)
    product_report("nvme", product, [item], long=True)
    output = capsys.readouterr().out
    assert "Newegg Canada [3P]" in output
    assert "  https://newegg.ca/p/example" in output
    assert "Seller: Marketplace Shop (third party)" in output


def test_report_detail_modes(capsys):
    from muninn.cli import product_report
    from muninn.config import Product

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
    from muninn.cli import report
    from muninn.config import Product

    tracks = [("nvme", [Product("Drive", None, (), ("Buying rationale",))])]
    report(tracks, tmp_path)
    short = capsys.readouterr().out
    assert "TRACK: NVME" in short
    assert "NVME: DRIVE" not in short
    assert "Product Details" not in short
    assert "WARNINGS" in short
    assert "- NO_VENDORS: Drive" in short

    report(tracks, tmp_path, long=True)
    detailed = capsys.readouterr().out
    assert "TRACK: NVME" in detailed
    assert "NVME: DRIVE" in detailed
    assert "Product Details" in detailed
    assert "Buying rationale" in detailed


def test_short_report_skips_no_price_rows_but_keeps_their_warnings(tmp_path, capsys, monkeypatch):
    from muninn import cli
    from muninn.config import Product, Retailer

    retailer = Retailer("Shop", "https://example.test/drive", 7200)
    product = Product("Drive", None, (retailer,))
    warning = "PRICE_NOT_FOUND: Shop: Drive; using prior price if available"
    monkeypatch.setattr(
        cli,
        "refresh_if_needed",
        lambda store, product, retailer, session, current_time, vendor_delay, track_name: cli.Checked(
            retailer, [], False, warning
        ),
    )

    cli.report([("nvme", [product])], tmp_path)
    short = capsys.readouterr().out
    assert not any(line.startswith("Drive ") for line in short.splitlines())
    assert f"- {warning}" in short

    cli.report([("nvme", [product])], tmp_path, long=True)
    detailed = capsys.readouterr().out
    assert "NO PRICE" in detailed
    assert "NVME: DRIVE" in detailed


def test_retailer_warnings_include_clickable_url(tmp_path, capsys, monkeypatch):
    from muninn import cli
    from muninn.config import Product, Retailer

    retailer = Retailer("Shop", "https://example.test/drive", 7200)
    product = Product("Drive", None, (retailer,))
    for warning in (
        "FETCH_FAILED: Shop: Drive; using stale data (timed out)",
        "NO_CACHE: Shop: Drive",
        "PRICE_NOT_FOUND: Shop: Drive; using prior price if available",
    ):
        monkeypatch.setattr(
            cli,
            "refresh_if_needed",
            lambda store, product, retailer, session, current_time, vendor_delay, track_name: cli.Checked(
                retailer, [], False, warning
            ),
        )
        cli.report([("nvme", [product])], tmp_path)
        output = capsys.readouterr().out
        assert f"- {warning}\n  https://example.test/drive" in output


def test_retailer_warning_lists_vendor_before_product():
    from muninn.cli import format_retailer_warning
    from muninn.config import Product, Retailer

    retailer = Retailer("Amazon", "https://example.test/drive", 7200)
    product = Product("Drive", None, (retailer,))
    assert format_retailer_warning("FETCH_FAILED", product, retailer, "using stale data (timed out)") == (
        "FETCH_FAILED: Amazon: Drive; using stale data (timed out)"
    )


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
    from muninn.cli import Checked, track_overview
    from muninn.config import Product, Retailer
    from muninn.store import Observation

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
    from muninn.cli import Checked, track_overview
    from muninn.config import Product, Retailer
    from muninn.store import Observation

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
    from muninn.cli import track_overview
    from muninn.config import Product

    track_overview("nvme", [(Product("Pending", Decimal(575), (), desirability=Decimal(9)), [])])
    output = capsys.readouterr().out
    assert "Pending" in output
    assert "NO LINKS" in output
    assert "$575" in output


def test_range_overflow_markers_are_inverse(monkeypatch):
    from muninn import cli

    monkeypatch.setattr(cli, "_COLOR", True)
    assert cli.style_cell("<───────────────>", "Range") == "\033[7m<\033[0m───────────────\033[7m>\033[0m"


def test_short_overview_zebra_uses_configured_background(capsys, monkeypatch):
    from muninn import cli
    from muninn.config import Product

    monkeypatch.setattr(cli, "_COLOR", True)
    products = [
        (Product("First", None, (), desirability=Decimal(9)), []),
        (Product("Second", None, (), desirability=Decimal(8)), []),
    ]
    cli.track_overview("nvme", products, zebra_background=cli.background_code("bright_black"))
    output = capsys.readouterr().out
    first, second = (line for line in output.splitlines() if "NO LINKS" in line)
    assert "\033[100m" not in first
    assert "\033[100mSecond" in second


def test_zebra_background_color_formats():
    import pytest
    from muninn.cli import background_code

    assert background_code("grey") == "100"
    assert background_code("196") == "48;5;196"
    assert background_code("#123abc") == "48;2;18;58;188"
    with pytest.raises(ValueError, match="zebra colour"):
        background_code("not-a-colour")


def test_cli_zebra_default_and_explicit_color(tmp_path, monkeypatch):
    import pytest
    from unittest.mock import Mock
    from muninn import cli

    track = tmp_path / "tracks" / "nvme"
    track.mkdir(parents=True)
    (track / "drive.yaml").write_text("name: Drive\nretailers: []\n")
    report = Mock(return_value=0)
    monkeypatch.setattr(cli, "report", report)
    monkeypatch.setattr(cli, "_COLOR", False)
    for flags, expected in (
        ([], None),
        (["--zebra"], "48;2;224;224;224"),
        (["--zebra=#303030"], "48;2;48;48;48"),
    ):
        with pytest.raises(SystemExit) as result:
            cli.main(["--tracks-dir", str(tmp_path / "tracks"), "check", "nvme", *flags])
        assert result.value.code == 0
        assert report.call_args.kwargs["zebra_background"] == expected


def test_zebra_alternates_products_not_retailer_rows(capsys, monkeypatch):
    from muninn import cli
    from muninn.config import Product, Retailer
    from muninn.store import Observation

    monkeypatch.setattr(cli, "_COLOR", True)
    results = []
    for name, count in (("Alpha", 2), ("Beta", 3), ("Gamma", 1)):
        retailers = tuple(Retailer(f"Shop {i}", f"https://example.test/{name}/{i}", 7200)
                          for i in range(count))
        product = Product(name, None, retailers)
        checked = [cli.Checked(retailer, [Observation(datetime.now(timezone.utc), Decimal(100), True, None)], False, None)
                   for retailer in retailers]
        results.append((product, checked))

    cli.track_overview("nvme", list(reversed(results)), zebra_background="48;2;224;224;224")
    rows = [line for line in capsys.readouterr().out.splitlines() if "Shop" in line]
    assert len(rows) == 6
    assert ["\033[48;2;224;224;224" in row for row in rows] == [False, False, True, True, True, False]


def test_report_zebra_in_short_and_long_modes(tmp_path, capsys, monkeypatch):
    from muninn import cli
    from muninn.config import Product

    products = [Product(name, None, ()) for name in ("Alpha", "Beta", "Gamma")]
    for long in (False, True):
        for color in (False, True):
            monkeypatch.setattr(cli, "_COLOR", color)
            cli.report([("nvme", products)], tmp_path, long=long,
                       zebra_background="48;2;48;48;48")
            output = capsys.readouterr().out
            rows = [line for line in output.splitlines() if "NO LINKS" in line]
            assert len(rows) == 3
            assert ["\033[48;2;48;48;48" in row for row in rows] == [False, color, False]
            assert ("Product Details" in output) == long
            if not color:
                assert "\033[" not in output


def test_cli_long_file_keeps_stdout_short(tmp_path, capsys, monkeypatch):
    import pytest
    from muninn import cli

    track = tmp_path / "tracks" / "nvme"
    track.mkdir(parents=True)
    (track / "drive.yaml").write_text("name: Drive\nretailers: []\nnotes: [Buying rationale]\n")
    output = tmp_path / "full report.txt"
    output.write_text("old contents")
    monkeypatch.setattr(cli, "_COLOR", False)
    with pytest.raises(SystemExit) as result:
        cli.main(["--tracks-dir", str(tmp_path / "tracks"), "--color", "always",
                  "check", "nvme", f"--long={output}"])
    assert result.value.code == 1  # No vendors remains a warning.
    short = capsys.readouterr().out
    full = output.read_text()
    assert "TRACK: NVME" in short
    assert "Product Details" not in short
    assert "Buying rationale" not in short
    assert "TRACK: NVME" in full
    assert "Product Details" in full
    assert "Buying rationale" in full
    assert "NO_VENDORS: Drive" in full
    assert "old contents" not in full
    assert "\033[" not in full
    assert cli._COLOR is True


def test_optional_flag_values_require_equals(tmp_path, monkeypatch):
    import pytest
    from unittest.mock import Mock
    from muninn import cli

    track = tmp_path / "tracks" / "nvme"
    track.mkdir(parents=True)
    (track / "drive.yaml").write_text("name: Drive\nretailers: []\n")
    report = Mock(return_value=0)
    monkeypatch.setattr(cli, "report", report)
    monkeypatch.setattr(cli, "_COLOR", False)
    for arguments in (["--long", "check", "nvme"],
                      ["check", "--long", "nvme"],
                      ["--zebra", "check", "nvme"],
                      ["check", "--zebra", "nvme"]):
        with pytest.raises(SystemExit) as result:
            cli.main(["--tracks-dir", str(tmp_path / "tracks"), *arguments])
        assert result.value.code == 0
        assert report.call_args.args[0][0][0] == "nvme"
        assert report.call_args.kwargs["long_file"] is None
        assert report.call_args.kwargs["long"] == ("--long" in arguments)
        assert report.call_args.kwargs["zebra_background"] == (
            "48;2;224;224;224" if "--zebra" in arguments else None)


def test_report_groups_each_track_warnings_with_its_sections(tmp_path, capsys, monkeypatch):
    from muninn import cli
    from muninn.config import Product

    monkeypatch.setattr(cli, "_COLOR", False)
    tracks = [("first", [Product("Alpha", None, ())]),
              ("second", [Product("Beta", None, ())])]
    for long, long_file in ((False, None), (True, None), (True, tmp_path / "report.txt")):
        assert cli.report(tracks, tmp_path, long=long, long_file=long_file) == 1
        outputs = [capsys.readouterr().out]
        if long_file:
            outputs.append(long_file.read_text())
        for output in outputs:
            assert output.count("TRACK: FIRST") == 1
            assert output.count("TRACK: SECOND") == 1
            first_overview = output.index("TRACK: FIRST")
            first_warnings = output.index("WARNINGS: FIRST")
            second_warnings = output.index("WARNINGS: SECOND")
            assert first_overview < first_warnings < output.index("TRACK: SECOND")
            assert first_warnings < second_warnings
            if "Product Details" in output:
                assert output.index("FIRST: ALPHA") < first_overview
                assert first_warnings < output.index("SECOND: BETA")
            first = output.split("WARNINGS: FIRST")[1].splitlines()
            second = output.split("WARNINGS: SECOND")[1].splitlines()
            assert first[1] == "- NO_VENDORS: Alpha"
            assert second[1] == "- NO_VENDORS: Beta"


def test_extract_best_buy_product_json_ld_without_main_price():
    # The AW3225QF snapshot had only recommendation prices in the visible DOM.
    html = '''
        <span data-automation="product-price" itemprop="offers">
          <meta itemprop="price" content="56"><span>$56</span>
        </span>
        <script type="application/ld+json">
          {"@type":"Product","offers":{"price":56}}
        </script>
        <script id="product-json-ld" type="application/ld+json">
          {"@type":"Product","sku":"18188524","offers":{"price":1199.99}}
        </script>
    '''
    url = "https://www.bestbuy.ca/en-ca/product/example/18188524"
    assert extract_price(html, url) == Decimal("1199.99")
    assert extract_price(html, url, "meta[itemprop='price']") == Decimal("56")


def test_extract_best_buy_does_not_use_recommendations_as_fallback():
    html = '''
        <span data-automation="product-price" itemprop="offers">
          <meta itemprop="price" content="56"><span>$56</span>
        </span>
        <script id="product-json-ld" type="application/ld+json">invalid</script>
        <script type="application/ld+json">{"offers":{"price":56}}</script>
    '''
    assert extract_price(html, "https://www.bestbuy.ca/en-ca/product/example") is None
