from pathlib import Path
import time
import requests

# The core (cpp-httplib) closes idle keep-alive connections after 5 seconds
# (set_keep_alive_timeout in src/main.cpp). Reusing a connection near that
# deadline races the close and surfaces as RemoteDisconnected/ConnectionReset,
# even for side-effecting calls that must not be retried. Connections idle for
# longer than this are discarded so the request starts on a fresh connection.
REUSE_IDLE_SECONDS = 2.0

class APIError(RuntimeError):
    def __init__(self, response):
        self.status = response.status_code
        try:
            self.data = response.json()
        except ValueError:
            self.data = {'error': 'Invalid core response'}
        super().__init__(self.data.get('error', 'Core API failed'))

class Client:
    def __init__(self, url, token_file, verify=True, reuse_idle_seconds=REUSE_IDLE_SECONDS):
        self.url = url.rstrip('/')
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({'Authorization': 'Bearer ' + Path(token_file).read_text().strip()})
        self.session.verify = verify
        self.reuse_idle_seconds = reuse_idle_seconds
        self._last_used = None

    def _send(self, method, route, data):
        if self._last_used is not None and time.monotonic() - self._last_used > self.reuse_idle_seconds:
            self.session.close()
        try:
            return self.session.request(method, self.url + '/api/' + route, json=data, timeout=(10, 210))
        finally:
            self._last_used = time.monotonic()

    def call(self, route, data=None, method=None):
        method = method or ('POST' if data is not None else 'GET')
        try:
            r = self._send(method, route, data)
        except requests.ConnectionError:
            # Only bodiless GET reads may be repeated. Any other request may
            # already have changed core state; its outcome stays with the caller.
            if method != 'GET' or data is not None:
                raise
            self.session.close()
            r = self._send(method, route, data)
        if not r.ok:
            raise APIError(r)
        return r.json()
