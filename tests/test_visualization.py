from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from muninn.cli import Checked, track_overview, metrics, money, product_report, data_age
from muninn.config import Product, Retailer
from muninn.store import Observation
from muninn.visualization import price_range, RANGE_WIDTH


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


def test_money_preserves_nonzero_cents():
    assert money(Decimal('710')) == '$710.00'
    assert money(Decimal('709.99')) == '$709.99'
    assert money(Decimal('100.50')) == '$100.50'
    assert money(Decimal('100.49')) == '$100.49'
    assert money(Decimal('1099.99')) == '$1,099.99'


def test_price_drop_status_includes_small_changes():
    retailer = Retailer('Vendor', 'https://example.com', 7200)

    def status(previous, current):
        history = [
            Observation(datetime.now(timezone.utc), Decimal(previous), True, None),
            Observation(datetime.now(timezone.utc), Decimal(current), True, None),
        ]
        return metrics(Checked(retailer, history, False, None), None)[-1]

    assert status('100', '90.01') == '↓ $9.99'
    assert status('100', '90') == '↓ $10.00'


def test_report_alignment_details_and_exact_comparison(capsys, monkeypatch):
    import muninn.cli as cli
    monkeypatch.setattr(cli, '_COLOR', False)
    monkeypatch.setattr(cli, 'report_width', lambda: 120)
    retailer = Retailer('Vendor', 'https://example.com', 7200)
    product = Product('Drive', Decimal('100'), (retailer,))
    history = [Observation(datetime.now(timezone.utc), Decimal(p), True, None) for p in ('90', '110', '100.01')]
    item = Checked(retailer, history, False, None)
    # Rounding must not incorrectly make a target met.
    assert metrics(item, product.target_price)[1] == '$100.01'
    assert metrics(item, product.target_price)[-1] != 'TARGET MET!'
    missing = Checked(retailer, [], False, None)
    track_overview('nvme', [(product, [item, missing])])
    lines = capsys.readouterr().out.splitlines()
    table = [line for line in lines if line.startswith(('Product ', 'Drive '))]
    assert '$100.01' not in '\n'.join(table)
    assert len(table) == 3
    assert all(len(line) == 120 for line in table)
    assert 'Low' not in table[0] and 'High' not in table[0]
    assert 'Range' in table[0]
    product_report('nvme', product, [item], long=True)
    output = capsys.readouterr().out
    assert '$100.01' in output
    assert 'History  ▁█▅  latest $100.01' in output
    assert '$90.00–$110.00' in output
    assert '3 obs' in output
    assert 'Observations:' not in output and 'Low:' not in output


def test_shared_scale_places_equal_prices_in_same_column():
    bounds = (Decimal(500), Decimal(900))
    first = price_range([Decimal(600)], Decimal(500), bounds)
    second = price_range([Decimal(700)], Decimal(550), bounds)
    third = price_range([Decimal(600)], Decimal(550), bounds)
    assert first.index('●') == third.index('●') == 4
    assert second.index('●') == 8
    assert all(len(row) == RANGE_WIDTH for row in (first, second, third))


def test_overview_uses_configured_scale(capsys, monkeypatch):
    import muninn.cli as cli
    monkeypatch.setattr(cli, '_COLOR', False)

    retailer = Retailer('Vendor', 'https://example.com', 7200)
    product = Product('Drive', Decimal(500), (retailer,))
    history = [Observation(datetime.now(timezone.utc), Decimal(p), True, None) for p in ('900', '600')]
    item = Checked(retailer, history, False, None)
    other = Product('Other', Decimal(550), (retailer,))
    other_item = Checked(retailer, [Observation(datetime.now(timezone.utc), Decimal(700), True, None)], False, None)
    results = [(product, [item]), (other, [other_item])]
    track_overview('nvme', results, bounds=(Decimal(500), Decimal(900)), legend=True)
    output = capsys.readouterr().out
    assert 'Range: $500.00–$900.00  ● now · T target · ◎ both · < > outside' in output
    assert output.splitlines()[-3:] == ['', 'Range: $500.00–$900.00  ● now · T target · ◎ both · < > outside', '']
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
    from muninn.visualization import validate_bounds
    with pytest.raises(ValueError, match='range'):
        validate_bounds((Decimal(low), Decimal(high)))


def test_help_subcommand_matches_help_option(capsys):
    import muninn.cli as cli

    with pytest.raises(SystemExit) as exit_info:
        cli.main(['help'])
    subcommand_output = capsys.readouterr().out

    with pytest.raises(SystemExit) as option_exit_info:
        cli.main(['--help'])
    option_output = capsys.readouterr().out

    assert exit_info.value.code == option_exit_info.value.code == 0
    assert subcommand_output == option_output


def test_track_config_bounds_override_defaults_and_cli(monkeypatch, tmp_path):
    import muninn.cli as cli
    from unittest.mock import Mock

    track = tmp_path / 'tracks' / 'nvme'
    track.mkdir(parents=True)
    (track / 'track.yaml').write_text('range_min: 400\nrange_max: 900\n')
    (track / 'drive.yaml').write_text('name: Drive\nretailers: []\n')
    report = Mock(return_value=0)
    monkeypatch.setattr(cli, 'report', report)

    with pytest.raises(SystemExit) as exit_info:
        cli.main(['--tracks-dir', str(tmp_path / 'tracks'), 'check', 'nvme'])
    assert exit_info.value.code == 0
    assert report.call_args.kwargs['track_bounds'] == {'nvme': (Decimal(400), Decimal(900))}

    report.reset_mock()
    with pytest.raises(SystemExit):
        cli.main(['--tracks-dir', str(tmp_path / 'tracks'), 'check', 'nvme', '--range-max', '950'])
    assert report.call_args.kwargs['track_bounds'] == {'nvme': (Decimal(400), Decimal(950))}


def test_cli_range_options(monkeypatch, tmp_path):
    import muninn.cli as cli
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


@pytest.mark.parametrize("prices,expected", [
    ([], "—"), ([100], "▄"), ([100, 100], "▄▄"),
    ([0, 1, 2, 3, 4, 5, 6, 7], "▁▂▃▄▅▆▇█"),
    ([200, 100, 200], "█▁█"),
])
def test_price_sparkline(prices, expected):
    from muninn.visualization import price_sparkline
    assert price_sparkline([Decimal(p) for p in prices]) == expected


def test_history_window_and_empty_report(capsys):
    retailer = Retailer('Vendor', 'https://example.com', 7200)
    product = Product('Drive', None, (retailer,))
    history = [Observation(datetime(2025, 6, 2, 12, tzinfo=timezone.utc), Decimal(p), True, None)
               for p in range(45)]
    product_report('nvme', product, [Checked(retailer, history, False, None)], long=True)
    output = capsys.readouterr().out
    assert 'last 40 of 45 obs' in output
    assert '$5.00–$44.00' in output
    assert 'latest $44.00' in output
    assert len(output.split('History  ')[1].split('  latest')[0]) == 40
    product_report('nvme', product, [Checked(retailer, [], False, None)], long=True)
    assert 'History  —  no observations' in capsys.readouterr().out


def test_long_report_sorts_vendors_by_current_price(capsys, monkeypatch):
    import muninn.cli as cli
    monkeypatch.setattr(cli, '_COLOR', False)
    monkeypatch.setattr(cli, 'report_width', lambda: 120)
    timestamp = datetime.now(timezone.utc)
    checked = []
    for name, prices in [('Missing', []), ('Expensive', ['1', '200']),
                         ('Cheap', ['300', '50']), ('Equal', ['50']), ('Free', ['0'])]:
        retailer = Retailer(name, f'https://example.com/{name}', 7200)
        history = [Observation(timestamp, Decimal(price), True, None) for price in prices]
        checked.append(Checked(retailer, history, False, None))
    original = checked.copy()
    product = Product('Drive', None, tuple(item.retailer for item in checked))

    product_report('nvme', product, checked, long=True)

    table, details = capsys.readouterr().out.split('Product Details')
    expected = ['Free', 'Cheap', 'Equal', 'Expensive', 'Missing']
    for section in (table, details):
        names = [line.split()[0] for line in section.splitlines()
                 if line and line.split()[0] in expected]
        assert names == expected
    assert checked == original


@pytest.mark.parametrize('seconds,expected', [
    (-10, '0s'), (0, '0s'), (10, '10s'), (59, '59s'), (60, '60s'),
    (75, '75s'), (119, '119s'), (120, '2m'), (300, '5m'),
    (3600, '60m'), (4500, '75m'), (7199, '119m'), (7200, '2h'),
    (14400, '4h'), (86400, '24h'), (90000, '25h'), (172799, '47h'),
    (172800, '2d'), (259200, '3d'),
])
def test_data_age_units(seconds, expected):
    at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    retailer = Retailer('Shop', 'https://example.com/drive', 7200)
    item = Checked(retailer, [Observation(at - timedelta(seconds=seconds), Decimal('10'), True, None)], False, None)
    assert data_age(item, at) == expected


@pytest.mark.parametrize('history', [[], [Observation(datetime.now(timezone.utc), None, True, None)]])
def test_data_age_missing_price(history):
    retailer = Retailer('Shop', 'https://example.com/drive', 7200)
    assert data_age(Checked(retailer, history, False, None), datetime.now(timezone.utc)) == '—'


@pytest.mark.parametrize('width', [100, 120])
@pytest.mark.parametrize('overview', [True, False])
def test_tables_include_aligned_age_of_displayed_price(monkeypatch, capsys, width, overview):
    from muninn import cli
    at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(cli, 'now', lambda: at)
    monkeypatch.setattr(cli, 'report_width', lambda: width)
    monkeypatch.setattr(cli, '_COLOR', False)
    retailer = Retailer('Shop', 'https://example.com/drive', 7200)
    product = Product('Drive', None, (retailer,))
    item = Checked(retailer, [Observation(at - timedelta(minutes=75), Decimal('10'), True, None)],
                   False, 'FETCH_FAILED: using stale data')
    missing = Checked(Retailer('Other', 'https://example.com/other', 7200), [], False, 'NO_CACHE')
    if overview:
        cli.track_overview('nvme', [(product, [item, missing]), (Product('Unlisted', None, ()), [])])
    else:
        cli.product_report('nvme', product, [item, missing])
    lines = capsys.readouterr().out.splitlines()
    header = next(line for line in lines if 'Age' in line)
    age_start = header.index('Age') - 1  # Four-character right-aligned column.
    row = next(line for line in lines if 'Shop' in line)
    assert row[age_start:age_start + 4] == ' 75m'
    assert len(header) == len(row) == width
    missing_row = next(line for line in lines if 'Other' in line)
    assert missing_row[age_start:age_start + 4] == '   —'
    assert 'NO PRICE' in missing_row
    if overview:
        unlisted = next(line for line in lines if 'Unlisted' in line)
        assert unlisted[age_start:age_start + 4] == '   —'
        assert 'NO LINKS' in unlisted


@pytest.mark.parametrize('age,codes', [
    ('0s', '2'), ('119s', '2'), ('2m', '2'), ('119m', '2'),
    ('2h', None), ('23h', None), ('24h', '33'), ('47h', '33'),
    ('2d', '33;1'), ('999d', '33;1'), ('100…', '33;1'), ('—', None),
])
@pytest.mark.parametrize('background', [None, '48;5;240'])
def test_age_cell_emphasis(monkeypatch, age, codes, background):
    from muninn import cli
    monkeypatch.setattr(cli, '_COLOR', True)
    value = age.rjust(4)
    all_codes = ';'.join(code for code in (background, codes) if code)
    expected = f'\033[{all_codes}m{value}\033[0m' if all_codes else value
    assert cli.style_cell(value, 'Age', background) == expected
    monkeypatch.setattr(cli, '_COLOR', False)
    assert cli.style_cell(value, 'Age', background) == value


@pytest.mark.parametrize('overview', [False, True])
@pytest.mark.parametrize('hours,codes', [(1, '2'), (2, None), (24, '33'), (48, '33;1')])
def test_tables_style_age(monkeypatch, capsys, overview, hours, codes):
    from muninn import cli
    at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(cli, 'now', lambda: at)
    monkeypatch.setattr(cli, '_COLOR', True)
    retailer = Retailer('Shop', 'https://example.com/drive', 7200)
    product = Product('Drive', None, (retailer,))
    item = Checked(retailer, [Observation(at - timedelta(hours=hours), Decimal('10'), True, None)], False, None)
    if overview:
        cli.track_overview('nvme', [(product, [item])])
    else:
        cli.product_report('nvme', product, [item])
    age = cli.data_age(item, at).rjust(4)
    expected = f'\033[{codes}m{age}\033[0m' if codes else age
    assert expected in capsys.readouterr().out


def test_history_plot_uses_shared_price_axis_in_input_order():
    from muninn.visualization import price_history_plot

    prices = [Decimal(100), Decimal(300), Decimal(200), Decimal(100)]
    assert price_history_plot(prices, width=9) == [
        '|*........|', '|........*|', '|....*....|', '|*........|',
    ]
    assert price_history_plot([Decimal(123)] * 3, width=9) == ['|....*....|'] * 3
    assert price_history_plot([], width=9) == []
    assert price_history_plot(prices, width=1) == ['|*|'] * 4
    with pytest.raises(ValueError, match='positive'):
        price_history_plot(prices, width=0)


def test_history_plot_does_not_round_prices_before_scaling():
    from muninn.visualization import price_history_plot

    prices = [Decimal('100.001'), Decimal('100.002'), Decimal('100.003')]
    assert price_history_plot(prices, width=9) == ['|*........|', '|....*....|', '|........*|']


def test_cli_long_report_labels_legacy_reset_timestamp_unknown(capsys, monkeypatch):
    from muninn import cli

    retailer = Retailer('Shop', 'https://example.com/legacy', 7200)
    product = Product('Drive', None, (retailer,))
    history = [Observation(datetime(1970, 1, 1, tzinfo=timezone.utc), Decimal(100), True, None)]
    monkeypatch.setattr(cli, '_COLOR', False)
    product_report('nvme', product, [Checked(retailer, history, False, None)], long=True)
    report = capsys.readouterr().out
    assert 'unknown (legacy reset)' in report
    assert '1969' not in report and '1970' not in report
    assert data_age(Checked(retailer, history, False, None), datetime.now(timezone.utc)) == '—'


@pytest.mark.parametrize('prices,target,refreshed,expected', [
    (['100', '90'], None, True, '↓ $10.00\nNEW LOW!'),
    (['100', '90'], '95', True, '↓ $10.00\nTARGET MET!'),
    (['90', '95'], '100', False, '↑ $5.00\nTARGET MET!'),
    (['90', '90'], '100', False, '—\nTARGET MET!'),
    (['90'], None, True, '—\nNEW LOW!'),
    (['90', '90'], None, False, '—'),
    (['90', '89.99'], None, False, '↓ $0.01'),
])
def test_status_preserves_delta_with_special_status(prices, target, refreshed, expected):
    retailer = Retailer('Vendor', 'https://example.com', 7200)
    history = [Observation(datetime.now(timezone.utc), Decimal(p), True, None) for p in prices]
    item = Checked(retailer, history, refreshed, None)
    for cents in (True, False):
        status = metrics(item, Decimal(target) if target else None, cents=cents)[-1]
        assert status == (expected if cents else expected.replace('.00', ''))
        compact = metrics(item, Decimal(target) if target else None, cents=cents, detailed=False)[-1]
        assert compact == status.split('\n')[0] + (' ★' if '\n' in status else '')


@pytest.mark.parametrize('overview', [True, False])
def test_special_status_on_aligned_second_line(capsys, monkeypatch, overview):
    import muninn.cli as cli
    monkeypatch.setattr(cli, '_COLOR', False)
    monkeypatch.setattr(cli, 'report_width', lambda: 120)
    retailer = Retailer('Vendor', 'https://example.com', 7200)
    product = Product('Drive', Decimal(100), (retailer,))
    history = [Observation(datetime.now(timezone.utc), Decimal(p), True, None) for p in ('110', '90')]
    item = Checked(retailer, history, True, None)
    if overview:
        track_overview('nvme', [(product, [item])], cents=True, detailed=True)
    else:
        product_report('nvme', product, [item], long=True)
    lines = capsys.readouterr().out.splitlines()
    index = next(i for i, line in enumerate(lines) if '↓ $20.00' in line)
    assert lines[index + 1].strip() == 'TARGET MET!'
    assert lines[index].index('↓') == lines[index + 1].index('TARGET MET!')
    assert len(lines[index]) == len(lines[index + 1]) == 120


@pytest.mark.parametrize('overview', [True, False])
def test_compact_status_uses_star_without_extra_line(capsys, monkeypatch, overview):
    import muninn.cli as cli
    monkeypatch.setattr(cli, '_COLOR', False)
    retailer = Retailer('Vendor', 'https://example.com', 7200)
    product = Product('Drive', Decimal(100), (retailer,))
    history = [Observation(datetime.now(timezone.utc), Decimal(p), True, None) for p in ('110', '90')]
    item = Checked(retailer, history, True, None)
    if overview:
        track_overview('nvme', [(product, [item])])
    else:
        product_report('nvme', product, [item])
    output = capsys.readouterr().out
    assert '↓ $20' in output and '★' in output
    assert 'TARGET MET!' not in output
