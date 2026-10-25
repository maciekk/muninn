from datetime import datetime, timezone
from decimal import Decimal

import pytest

from snipe.cli import Checked, track_overview, metrics, money, product_report
from snipe.config import Product, Retailer
from snipe.store import Observation
from snipe.visualization import price_range, RANGE_WIDTH


def graphic(prices, target=None):
    return price_range([Decimal(str(p)) for p in prices], Decimal(str(target)) if target is not None else None)


@pytest.mark.parametrize('prices,target', [([], None), ([], 50), ([100], None), ([100], 100),
    ([100], 50), ([100, 200, 150], 50), ([100, 200, 150], 250),
    ([100, 200, 150], 125), ([100, 200, 150], 150), ([0, 1, 0], 0)])
def test_fixed_width(prices, target):
    assert len(graphic(prices, target)) == RANGE_WIDTH


def test_geometry_and_collisions():
    assert graphic([100, 200, 150]) == '├───────●───────┤'
    assert graphic([100, 200, 150], 150) == '├───────◎───────┤'
    assert graphic([100, 200, 150], 50).startswith('T····')
    assert graphic([100, 200, 150], 250).endswith('····T')
    assert graphic([100]).strip() == '●'
    assert graphic([100], 100).strip() == '◎'
    assert graphic([100], 50) == 'T' + '·' * 15 + '●'
    assert graphic([100, 200, 100]).startswith('●')
    assert graphic([100, 200]).endswith('●')


def test_rounding():
    assert money(Decimal('709.99')) == '$710'
    assert money(Decimal('100.50')) == '$101'
    assert money(Decimal('100.49')) == '$100'
    assert money(Decimal('1099.99')) == '$1,100'


def test_report_alignment_details_and_exact_comparison(capsys, monkeypatch):
    import snipe.cli as cli
    monkeypatch.setattr(cli, '_COLOR', False)
    monkeypatch.setattr(cli, 'report_width', lambda: 120)
    retailer = Retailer('Vendor', 'https://example.com', 7200)
    product = Product('Drive', Decimal('100'), (retailer,))
    history = [Observation(datetime.now(timezone.utc), Decimal(p), True, None) for p in ('90', '110', '100.01')]
    item = Checked(retailer, history, False, None)
    # Rounding must not incorrectly make a target met.
    assert metrics(item, product.target_price)[1] == '$100'
    assert metrics(item, product.target_price)[-1] != 'TARGET MET!'
    missing = Checked(retailer, [], False, None)
    track_overview('nvme', [(product, [item, missing])])
    lines = capsys.readouterr().out.splitlines()
    table = [line for line in lines if line.startswith(('Product ', 'Drive '))]
    assert len(table) == 3
    assert all(len(line) == 120 for line in table)
    assert 'Low' not in table[0] and 'High' not in table[0]
    assert 'Range' in table[0]
    product_report('nvme', product, [item], long=True)
    output = capsys.readouterr().out
    assert 'Low: $90; High: $110' in output


def test_shared_scale_places_equal_prices_in_same_column():
    bounds = (Decimal(500), Decimal(900))
    first = price_range([Decimal(600)], Decimal(500), bounds)
    second = price_range([Decimal(700)], Decimal(550), bounds)
    third = price_range([Decimal(600)], Decimal(550), bounds)
    assert first.index('●') == third.index('●') == 4
    assert second.index('●') == 8
    assert all(len(row) == RANGE_WIDTH for row in (first, second, third))


def test_overview_uses_configured_scale(capsys):

    retailer = Retailer('Vendor', 'https://example.com', 7200)
    product = Product('Drive', Decimal(500), (retailer,))
    history = [Observation(datetime.now(timezone.utc), Decimal(p), True, None) for p in ('900', '600')]
    item = Checked(retailer, history, False, None)
    other = Product('Other', Decimal(550), (retailer,))
    other_item = Checked(retailer, [Observation(datetime.now(timezone.utc), Decimal(700), True, None)], False, None)
    results = [(product, [item]), (other, [other_item])]
    track_overview('nvme', results, bounds=(Decimal(500), Decimal(900)), legend=True)
    output = capsys.readouterr().out
    assert 'Range scale: $500 to $900' in output
    assert price_range([Decimal(700)], Decimal(550), (Decimal(500), Decimal(900))) in output


@pytest.mark.parametrize('prices,target,left,right', [
    ([2750], 600, False, True),
    ([100], 400, True, False),
    ([400, 2750, 750], 600, True, True),
    ([750], 1100, False, True),
    ([750], 400, True, False),
    ([500, 1000, 750], 600, False, False),
])
def test_overflow(prices, target, left, right):
    bar = price_range([Decimal(p) for p in prices], Decimal(target), (Decimal(500), Decimal(1000)))
    assert len(bar) == RANGE_WIDTH
    assert bar.startswith('<') == left
    assert bar.endswith('>') == right


@pytest.mark.parametrize('low,high', [('1000', '500'), ('500', '500'), ('-1', '1000'), ('NaN', '1000'), ('500', 'Infinity')])
def test_invalid_bounds(low, high):
    from snipe.visualization import validate_bounds
    with pytest.raises(ValueError, match='range'):
        validate_bounds((Decimal(low), Decimal(high)))


def test_cli_range_options(monkeypatch, tmp_path):
    import snipe.cli as cli
    from unittest.mock import Mock
    monkeypatch.setattr(cli, 'track_files', lambda *_: [])
    report = Mock(return_value=0)
    monkeypatch.setattr(cli, 'report', report)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(['check', 'nvme', '--range-min', '400', '--range-max', '900'])
    assert exit_info.value.code == 0
    assert report.call_args.kwargs['bounds'] == (Decimal(400), Decimal(900))
    with pytest.raises(SystemExit):
        cli.main([])
    assert report.call_args.kwargs['bounds'] == (Decimal(500), Decimal(1000))
    report.reset_mock()
    with pytest.raises(SystemExit) as exit_info:
        cli.main(['--range-min', 'nonsense'])
    assert exit_info.value.code == 2
    report.assert_not_called()
