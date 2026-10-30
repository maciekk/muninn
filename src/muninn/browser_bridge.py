"""Authenticated loopback transport between checks and an opted-in Firefox worker."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlparse

import requests

from .browser import FetchError

MAX_BODY = 10 * 1024 * 1024
PORT = 8765
FETCH_TIMEOUT = 180


class BridgeUnavailable(FetchError):
    """The bridge server or its Firefox worker is not ready."""


class WorkerUnavailable(ValueError):
    """No Firefox worker is polling the bridge."""


BRIDGE_HOSTS = frozenset(
    host for vendor in ("newegg.ca", "bestbuy.ca", "memoryexpress.com")
    for host in (vendor, f"www.{vendor}")
)


def is_bridge_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return (parsed.scheme == "https" and parsed.hostname in BRIDGE_HOSTS
                and parsed.username is None and parsed.password is None
                and parsed.port in {None, 443})
    except ValueError:
        return False


class Jobs:
    def __init__(self, allowed_urls):
        self.allowed = set(allowed_urls)
        self.lock = threading.Lock()
        self.job = None
        self.last_poll = 0.0

    def fetch(self, url, timeout=FETCH_TIMEOUT):
        if url not in self.allowed or not is_bridge_url(url):
            raise ValueError('URL is not an allowed bridge URL in the bridge tracks')
        with self.lock:
            if time.monotonic() - self.last_poll > 10:
                raise WorkerUnavailable('Firefox worker is not connected; click Connect in the extension')
            if self.job is not None:
                raise ValueError('Firefox bridge is busy with another check')
            job = {'id': secrets.token_urlsafe(24), 'url': url, 'sent': False,
                   'done': threading.Event(), 'html': None, 'error': None}
            self.job = job
        try:
            if not job['done'].wait(timeout):
                raise ValueError('Firefox timed out; complete verification in the dedicated Muninn tab and retry')
            if job['error']:
                raise ValueError(job['error'])
            return job['html']
        finally:
            with self.lock:
                if self.job is job:
                    self.job = None

    def next(self):
        with self.lock:
            self.last_poll = time.monotonic()
            if self.job and not self.job['sent']:
                self.job['sent'] = True
                return {key: self.job[key] for key in ('id', 'url')}
        return None

    def complete(self, data):
        with self.lock:
            job = self.job
            if not job or data.get('id') != job['id'] or job['done'].is_set():
                raise ValueError('Unknown or expired Firefox job')
            error = data.get('error')
            if error is not None and not isinstance(error, str):
                raise ValueError('Invalid worker error')
            if not error:
                if not isinstance(data.get('url'), str) or data['url'].split('#')[0] != job['url'].split('#')[0]:
                    error = 'Firefox landed on a different URL; refusing a possible variant mismatch'
                elif not isinstance(data.get('html'), str) or not data['html']:
                    raise ValueError('Expected rendered HTML')
            self.last_poll = time.monotonic()  # A long verification still proves the worker is alive.
            job['html'] = data.get('html')
            job['error'] = error
            job['done'].set()


def make_server(import_page, token: str, port: int = PORT, allowed_urls=()) -> ThreadingHTTPServer:
    jobs = Jobs(allowed_urls)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authorized(self):
            if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                self.reply(403, {'error': 'invalid host'})
                return False
            if not secrets.compare_digest(self.headers.get('Authorization', ''), f'Bearer {token}'):
                self.reply(403, {'error': 'invalid pairing token'})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            if self.path == '/status':
                self.reply(200, {'ok': True})
            elif self.path == '/next':
                self.reply(200, {'job': jobs.next()})
            else:
                self.reply(404, {'error': 'not found'})

        def do_POST(self):
            if not self.authorized():
                return
            if self.path not in {'/import', '/fetch', '/result'}:
                self.reply(404, {'error': 'not found'})
                return
            try:
                if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding'):
                    raise ValueError('expected JSON with Content-Length')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    raise ValueError('page must be between 1 byte and 10 MiB')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('expected a JSON object')
                if self.path == '/result':
                    jobs.complete(data)
                    result = {'ok': True}
                elif self.path == '/fetch':
                    if not isinstance(data.get('url'), str):
                        raise ValueError('expected URL string')
                    result = {'html': jobs.fetch(data['url'])}
                else:
                    if not isinstance(data.get('url'), str) or not isinstance(data.get('html'), str):
                        raise ValueError('expected URL and HTML strings')
                    result = {'price': str(import_page(data['url'], data['html']))}
            except (ValueError, OSError) as exc:
                self.reply(400, {'error': str(exc), **({'kind': 'unavailable'} if isinstance(exc, WorkerUnavailable) else {})})
                return
            self.reply(200, result)

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.jobs = jobs
    return server


def fetch_html(url: str, state_dir: Path) -> str:
    try:
        credentials = json.loads((state_dir / 'bridge.json').read_text())
        port, token = credentials['port'], credentials['token']
        if type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str):
            raise ValueError('invalid credentials')
        response = requests.post(f'http://127.0.0.1:{port}/fetch', json={'url': url},
                                 headers={'Authorization': f'Bearer {token}'}, timeout=(3, FETCH_TIMEOUT + 15))
        data = response.json()
        if not response.ok:
            if data.get('kind') == 'unavailable' or response.status_code == 403:
                raise BridgeUnavailable(data.get('error', 'Firefox bridge unavailable'))
            raise FetchError(data.get('error', 'Firefox bridge request failed'))
        if not isinstance(data.get('html'), str):
            raise ValueError('invalid HTML response')
        return data['html']
    except requests.Timeout as exc:
        raise FetchError(f'Firefox bridge request timed out ({exc})') from exc
    except (OSError, ValueError, KeyError, requests.RequestException) as exc:
        raise BridgeUnavailable(f'Firefox bridge unavailable; run muninn browser-bridge and connect the extension ({exc})') from exc


def serve(import_page, state_dir: Path, allowed_urls) -> None:
    token = secrets.token_urlsafe(32)
    with make_server(import_page, token, allowed_urls=allowed_urls) as server:
        state_dir.mkdir(parents=True, exist_ok=True)
        credentials = state_dir / 'bridge.json'
        temporary = state_dir / f'bridge-{secrets.token_hex(8)}.tmp'
        try:
            temporary.touch(mode=0o600)
            temporary.write_text(json.dumps({'port': server.server_port, 'token': token}))
            temporary.replace(credentials)
            print(f'Firefox bridge listening on http://127.0.0.1:{server.server_port}', flush=True)
            print(f'Pairing token: {token}', flush=True)
            print('Paste the token and click Connect in the Firefox extension; then run muninn check. Ctrl-C stops the bridge.', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
        finally:
            credentials.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
