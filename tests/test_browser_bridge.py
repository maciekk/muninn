from decimal import Decimal
import threading
from unittest.mock import Mock

import pytest
import requests

from muninn.browser_bridge import MAX_BODY, make_server
from muninn.cli import import_page_html, main
from muninn.config import Product, Retailer
from muninn.store import Store


@pytest.fixture
def bridge():
    importer = Mock(return_value=Decimal('709.00'))
    server = make_server(importer, 'test-token', port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/import', importer
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_authenticated_import(bridge):
    url, importer = bridge
    response = requests.post(url, json={'url': 'https://www.newegg.ca/p/example', 'html': '<html>price</html>'},
                             headers={'Authorization': 'Bearer test-token'}, timeout=5)
    assert response.status_code == 200
    assert response.json() == {'price': '709.00'}
    importer.assert_called_once_with('https://www.newegg.ca/p/example', '<html>price</html>')


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer wrong'},
                                      {'Authorization': 'Bearer test-token', 'Host': 'attacker.example'}])
def test_unauthorized_requests_do_not_import(bridge, headers):
    url, importer = bridge
    response = requests.post(url, json={'url': 'x', 'html': 'x'}, headers=headers, timeout=5)
    assert response.status_code == 403
    importer.assert_not_called()


@pytest.mark.parametrize('payload', [[], {}, {'url': 123, 'html': 'x'}, {'url': 'x', 'html': None}])
def test_invalid_payload(bridge, payload):
    url, importer = bridge
    response = requests.post(url, json=payload, headers={'Authorization': 'Bearer test-token'}, timeout=5)
    assert response.status_code == 400
    importer.assert_not_called()


def test_oversized_request(bridge):
    url, importer = bridge
    response = requests.post(url, data='x' * (MAX_BODY + 1),
                             headers={'Authorization': 'Bearer test-token', 'Content-Type': 'application/json'}, timeout=5)
    assert response.status_code == 400
    importer.assert_not_called()


def test_import_rejection_is_reported(bridge):
    url, importer = bridge
    importer.side_effect = ValueError('URL is not configured')
    response = requests.post(url, json={'url': 'x', 'html': 'x'},
                             headers={'Authorization': 'Bearer test-token'}, timeout=5)
    assert response.status_code == 400
    assert response.json() == {'error': 'URL is not configured'}


def test_rendered_html_uses_existing_history(tmp_path):
    url = 'https://www.newegg.ca/p/example'
    retailer = Retailer('Newegg', url, 7200, fetcher='manual')
    tracks = [('nvme', [Product('Drive', None, (retailer,))])]
    html = '<div class="product-price"><span class="price-current">$709.00</span></div>'
    assert import_page_html(tracks, tmp_path, url, html) == Decimal('709.00')
    with pytest.raises(ValueError):
        import_page_html(tracks, tmp_path, url, '<title>Just a moment...</title>')
    store = Store(tmp_path)
    try:
        assert len(store.successful(url)) == 1
        assert store.cache_html(url) == html
    finally:
        store.close()


def test_bridge_command_loads_selected_tracks(tmp_path, monkeypatch):
    directory = tmp_path / 'tracks' / 'nvme'
    directory.mkdir(parents=True)
    url = 'https://www.newegg.ca/p/example'
    (directory / 'drive.yaml').write_text(f'name: Drive\nretailers:\n- name: Newegg\n  url: {url}\n  fetcher: manual\n')
    serve = Mock()
    monkeypatch.setattr('muninn.browser_bridge.serve', serve)
    with pytest.raises(SystemExit) as result:
        main(['browser-bridge', 'nvme', '--tracks-dir', str(directory.parent)])
    assert result.value.code == 0
    importer = serve.call_args.args[0]
    assert importer(url, '<div class="product-price"><span class="price-current">$709.00</span></div>') == Decimal('709.00')


@pytest.fixture
def worker_bridge(tmp_path):
    import json
    url = 'https://www.newegg.ca/p/example'
    server = make_server(Mock(), 'worker-token', port=0, allowed_urls={url})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (tmp_path / 'bridge.json').write_text(json.dumps({'port': server.server_port, 'token': 'worker-token'}))
    try:
        yield server, tmp_path, url
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def worker_request(server, path, data=None):
    headers = {'Authorization': 'Bearer worker-token'}
    url = f'http://127.0.0.1:{server.server_port}{path}'
    if data is None:
        return requests.get(url, headers=headers, timeout=5)
    return requests.post(url, json=data, headers=headers, timeout=5)


def test_check_fetch_roundtrip_through_http(worker_bridge):
    from concurrent.futures import ThreadPoolExecutor
    import time
    from muninn.browser_bridge import fetch_html
    server, state_dir, url = worker_bridge
    worker_request(server, '/next')
    html = '<div class="product-price--x"><div class="price-current_2026">$<strong>899</strong><sup>.99</sup></div></div>'
    with ThreadPoolExecutor() as executor:
        future = executor.submit(fetch_html, url, state_dir)
        for _ in range(100):
            job = worker_request(server, '/next').json()['job']
            if job:
                break
            time.sleep(.01)
        assert job['url'] == url
        assert worker_request(server, '/next').json()['job'] is None  # single delivery
        assert worker_request(server, '/result', {'id': 'wrong', 'url': url, 'html': html}).status_code == 400
        assert not future.done()
        assert worker_request(server, '/result', {'id': job['id'], 'url': url, 'html': html}).status_code == 200
        assert future.result(timeout=5) == html
        assert worker_request(server, '/result', {'id': job['id'], 'url': url, 'html': html}).status_code == 400


@pytest.mark.parametrize('result', [{'error': 'Verification timed out'}, {'url': 'https://www.newegg.ca/p/other', 'html': 'wrong variant'}])
def test_worker_errors_do_not_deliver_html(worker_bridge, result):
    from concurrent.futures import ThreadPoolExecutor
    import time
    from muninn.browser_bridge import fetch_html
    from muninn.browser import FetchError
    server, state_dir, url = worker_bridge
    worker_request(server, '/next')
    with ThreadPoolExecutor() as executor:
        future = executor.submit(fetch_html, url, state_dir)
        for _ in range(100):
            job = worker_request(server, '/next').json()['job']
            if job:
                break
            time.sleep(.01)
        worker_request(server, '/result', {'id': job['id'], **result})
        with pytest.raises(FetchError):
            future.result(timeout=5)
    assert server.jobs.job is None


def test_missing_worker_and_unconfigured_url(worker_bridge):
    from muninn.browser_bridge import fetch_html
    from muninn.browser import FetchError
    server, state_dir, url = worker_bridge
    with pytest.raises(FetchError, match='not connected'):
        fetch_html(url, state_dir)
    worker_request(server, '/next')
    with pytest.raises(FetchError, match='not an allowed'):
        fetch_html('https://www.newegg.ca/p/unconfigured', state_dir)


def test_jobs_timeout_and_busy_cleanup():
    from muninn.browser_bridge import Jobs
    from concurrent.futures import ThreadPoolExecutor
    import time
    url = 'https://www.newegg.ca/p/example'
    jobs = Jobs({url})
    jobs.next()
    with ThreadPoolExecutor() as executor:
        future = executor.submit(jobs.fetch, url, .1)
        for _ in range(100):
            if jobs.job:
                break
            time.sleep(.001)
        with pytest.raises(ValueError, match='busy'):
            jobs.fetch(url, .1)
        with pytest.raises(ValueError, match='timed out'):
            future.result(timeout=5)
    assert jobs.job is None


@pytest.mark.parametrize('url', ['http://www.newegg.ca/p/example', 'https://newegg.ca.evil.com/p/example', 'https://example.com', 'https://www.newegg.ca:999/p/example'])
def test_bridge_config_rejects_other_hosts(tmp_path, url):
    from muninn.config import load
    path = tmp_path / 'product.yaml'
    path.write_text(f'name: Drive\nretailers:\n- name: Store\n  url: {url}\n  fetcher: bridge\n')
    with pytest.raises(ValueError, match='HTTPS newegg.ca'):
        load(path)


def test_bridge_refresh_cache_rate_limit_and_failure(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from muninn.cli import refresh_if_needed
    from muninn.browser import FetchError
    from muninn import browser_bridge
    url = 'https://www.newegg.ca/p/example'
    retailer = Retailer('Newegg', url, 7200, fetcher='bridge')
    product = Product('Drive', None, (retailer,))
    fetch = Mock(return_value='<div class="product-price"><span class="price-current">$899.99</span></div>')
    monkeypatch.setattr(browser_bridge, 'fetch_html', fetch)
    store = Store(tmp_path)
    session = Mock()
    at = datetime.now(timezone.utc)
    try:
        first = refresh_if_needed(store, product, retailer, session, at, vendor_delay=0)
        assert first.current.price == Decimal('899.99')
        fetch.assert_called_once_with(url, store.root)
        assert store.db.execute('SELECT vendor FROM vendor_fetches').fetchone()[0] == 'newegg.ca'
        refresh_if_needed(store, product, retailer, session, at + timedelta(minutes=1), vendor_delay=0)
        assert fetch.call_count == 1
        original = store.cache_html(url)
        for failure in [FetchError('worker disconnected'), None]:
            store.mark_due(url)
            fetch.side_effect = failure
            fetch.return_value = '<span class="price-current">$369.99</span>'
            result = refresh_if_needed(store, product, retailer, session, at + timedelta(hours=3), vendor_delay=0)
            assert 'FETCH_FAILED' in result.warning
            assert result.current.price == Decimal('899.99')
            assert store.cache_html(url) == original
            assert len(store.successful(url)) == 1
        session.get.assert_not_called()
    finally:
        store.close()


def test_completed_long_job_keeps_worker_heartbeat_fresh():
    from concurrent.futures import ThreadPoolExecutor
    import time
    from muninn.browser_bridge import Jobs
    url = 'https://www.newegg.ca/p/example'
    jobs = Jobs({url})
    jobs.next()
    with ThreadPoolExecutor() as executor:
        future = executor.submit(jobs.fetch, url, 2)
        for _ in range(100):
            job = jobs.next()
            if job:
                break
            time.sleep(.001)
        jobs.last_poll = time.monotonic() - 150
        jobs.complete({'id': job['id'], 'url': url, 'html': 'page'})
        assert future.result(timeout=2) == 'page'
        assert time.monotonic() - jobs.last_poll < 2


def test_serve_private_credentials_and_cleanup(tmp_path, monkeypatch):
    import json
    from muninn import browser_bridge
    create = browser_bridge.make_server
    url = 'https://www.newegg.ca/p/example'
    seen = []

    def make(importer, token, allowed_urls):
        server = create(importer, token, port=0, allowed_urls=allowed_urls)
        def run():
            credentials = tmp_path / 'bridge.json'
            assert credentials.stat().st_mode & 0o777 == 0o600
            assert json.loads(credentials.read_text()) == {'port': server.server_port, 'token': token}
            assert server.jobs.allowed == {url}
            seen.append(True)
        server.serve_forever = run
        return server

    monkeypatch.setattr(browser_bridge, 'make_server', make)
    browser_bridge.serve(Mock(), tmp_path, {url})
    assert seen
    assert not (tmp_path / 'bridge.json').exists()
    assert not list(tmp_path.glob('*.tmp'))


def test_missing_server_is_distinct_unavailability(tmp_path):
    from muninn.browser_bridge import BridgeUnavailable, fetch_html
    with pytest.raises(BridgeUnavailable):
        fetch_html('https://www.newegg.ca/p/example', tmp_path)


def test_missing_worker_is_distinct_unavailability(worker_bridge):
    from muninn.browser_bridge import BridgeUnavailable, fetch_html
    _, state_dir, url = worker_bridge
    with pytest.raises(BridgeUnavailable, match='not connected'):
        fetch_html(url, state_dir)


@pytest.mark.parametrize('answer', ['skip', '', EOFError()])
def test_bridge_prompt_once_after_skip_or_failed_retry(tmp_path, monkeypatch, capsys, answer):
    from muninn.cli import BridgeRecovery
    from muninn.browser_bridge import BridgeUnavailable
    fetch = Mock(side_effect=BridgeUnavailable('offline'))
    prompt = Mock(side_effect=answer) if isinstance(answer, Exception) else Mock(return_value=answer)
    monkeypatch.setattr('muninn.browser_bridge.fetch_html', fetch)
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('sys.stderr.isatty', lambda: True)
    monkeypatch.setattr('builtins.input', prompt)
    recovery = BridgeRecovery()
    for _ in range(3):
        with pytest.raises(BridgeUnavailable):
            recovery.fetch_html('url', tmp_path)
    prompt.assert_called_once_with()
    assert fetch.call_count == (4 if answer == '' else 3)
    output = capsys.readouterr()
    assert output.err.count('Press Enter to retry') == 1
    assert 'muninn browser-bridge' in output.err
    assert not output.out


def test_bridge_prompt_retry_can_recover(tmp_path, monkeypatch):
    from muninn.cli import BridgeRecovery
    from muninn.browser_bridge import BridgeUnavailable
    fetch = Mock(side_effect=[BridgeUnavailable('offline'), 'page', 'next page', BridgeUnavailable('offline again')])
    prompt = Mock(return_value='')
    monkeypatch.setattr('muninn.browser_bridge.fetch_html', fetch)
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('sys.stderr.isatty', lambda: True)
    monkeypatch.setattr('builtins.input', prompt)
    recovery = BridgeRecovery()
    assert recovery.fetch_html('url', tmp_path) == 'page'
    assert recovery.fetch_html('url2', tmp_path) == 'next page'
    with pytest.raises(BridgeUnavailable):
        recovery.fetch_html('url3', tmp_path)
    prompt.assert_called_once_with()


@pytest.mark.parametrize('stdin_tty,stderr_tty', [(False, True), (True, False), (False, False)])
def test_bridge_never_prompts_noninteractive(tmp_path, monkeypatch, stdin_tty, stderr_tty):
    from muninn.cli import BridgeRecovery
    from muninn.browser_bridge import BridgeUnavailable
    monkeypatch.setattr('muninn.browser_bridge.fetch_html', Mock(side_effect=BridgeUnavailable('offline')))
    monkeypatch.setattr('sys.stdin.isatty', lambda: stdin_tty)
    monkeypatch.setattr('sys.stderr.isatty', lambda: stderr_tty)
    prompt = Mock()
    monkeypatch.setattr('builtins.input', prompt)
    with pytest.raises(BridgeUnavailable):
        BridgeRecovery().fetch_html('url', tmp_path)
    prompt.assert_not_called()


def test_ordinary_bridge_fetch_error_does_not_prompt(tmp_path, monkeypatch):
    from muninn.cli import BridgeRecovery
    from muninn.browser import FetchError
    monkeypatch.setattr('muninn.browser_bridge.fetch_html', Mock(side_effect=FetchError('verification failed')))
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('sys.stderr.isatty', lambda: True)
    prompt = Mock()
    monkeypatch.setattr('builtins.input', prompt)
    with pytest.raises(FetchError):
        BridgeRecovery().fetch_html('url', tmp_path)
    prompt.assert_not_called()


def test_report_shares_prompt_across_tracks_but_not_runs(tmp_path, monkeypatch, capsys):
    from muninn.cli import report
    from muninn.browser_bridge import BridgeUnavailable
    from datetime import datetime, timezone
    url = 'https://www.newegg.ca/p/example'
    retailer = Retailer('Newegg', url, 7200, fetcher='bridge')
    product = Product('Drive', None, (retailer,))
    tracks = [('nvme', [product]), ('monitor', [product])]
    store = Store(tmp_path)
    html = '<div class="product-price"><span class="price-current">$709.00</span></div>'
    at = datetime.now(timezone.utc)
    path, _ = store.write_html(url, html, at)
    store.record_success(url, product.name, retailer.name, Decimal('709'), path, at)
    store.close()
    monkeypatch.setattr('muninn.browser_bridge.fetch_html', Mock(side_effect=BridgeUnavailable('offline')))
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('sys.stderr.isatty', lambda: True)
    prompt = Mock(return_value='skip')
    monkeypatch.setattr('builtins.input', prompt)
    for _ in range(2):
        report(tracks, tmp_path, force=True, vendor_delay=0)
    assert prompt.call_count == 2
    assert capsys.readouterr().err.count('Press Enter to retry') == 2
    store = Store(tmp_path)
    try:
        assert store.cache_html(url) == html
        assert len(store.successful(url)) == 1
    finally:
        store.close()
