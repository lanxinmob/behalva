from pathlib import Path
import requests

class APIError(RuntimeError):
    def __init__(self, response):
        self.status = response.status_code
        try:
            self.data = response.json()
        except ValueError:
            self.data = {'error': 'Invalid core response'}
        super().__init__(self.data.get('error', 'Core API failed'))

class Client:
    def __init__(self, url, token_file, verify=True):
        self.url = url.rstrip('/')
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({'Authorization': 'Bearer ' + Path(token_file).read_text().strip()})
        self.session.verify = verify

    def call(self, route, data=None, method=None):
        r = self.session.request(method or ('POST' if data is not None else 'GET'),
                                 self.url + '/api/' + route, json=data, timeout=(10, 210))
        if not r.ok:
            raise APIError(r)
        return r.json()
