"""The connections page must not report health it has no evidence for.

Each test targets a way the status could lie: configuration shown as verified,
evidence from an old key or a previous process carried forward, a lost Gmail
verification silently re-run, or credential contents leaking into the report.
Loopback fixtures only; no real accounts, messages or paid calls.
"""
import json
from pathlib import Path
import sys
import time
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch
from playwright.sync_api import sync_playwright, expect
import test_core

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'connectors'))
import gmail

SEND_SCOPE = 'https://www.googleapis.com/auth/gmail.send'
FIXTURE_REFRESH = 'fixture-refresh-token-not-real'


class Connections(unittest.TestCase):
    def setUp(self):
        self.f = test_core.CoreContracts()
        self.f.setUp()
        self.call = self.f.call

    def tearDown(self):
        self.f.tearDown()

    def report(self):
        return {c['id']: c for c in self.call('connections')['connections']}

    def raw(self, path, token, body=None, headers=None):
        req = urllib.request.Request(f'http://127.0.0.1:{self.f.port}/api/{path}',
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json', **(headers or {})})
        try:
            with self.f.http.open(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def restart_with(self, **config):
        data = json.loads(self.f.config.read_text())
        data.update(config)
        self.f.config.write_text(json.dumps(data))
        self.f.stop(); self.f.start()

    def credential(self, scopes=(SEND_SCOPE,), refresh=FIXTURE_REFRESH):
        path = self.f.data/'gmail-fixture.json'
        path.write_text(json.dumps({'refresh_token': refresh, 'client_id': 'fixture', 'scopes': list(scopes)}))
        return path

    def chat_run(self, status=200):
        m = self.call('messages', dict(conversation_id=uuid.uuid4().hex, body='Fixture', expected_sequence=0,
                                       request_id=uuid.uuid4().hex))
        task = self.call('tasks/'+m['task_id']+'/claim', {}, role='worker')
        self.call('model/run', dict(task_id=task['id'], attempt_id=task['attempt_id'], source_ids=[]),
                  role='worker', status=status)

    def test_model_is_verified_only_by_a_real_reply_for_the_current_endpoint(self):
        self.assertTrue(self.call('state')['model_configured'])
        self.assertEqual('unverified', self.report()['model']['state'])
        self.f.provider.reject = True
        self.chat_run(status=502)
        model = self.report()['model']
        self.assertEqual('error', model['state'])
        self.assertIn('HTTP 401', model['detail'])
        self.f.provider.reject = False
        self.chat_run()
        self.assertEqual('verified', self.report()['model']['state'])
        # A different key or endpoint has no evidence yet, whatever the old one proved.
        self.call('providers', dict(id=uuid.uuid4().hex, name='Replacement', api_key='fixture-key',
            base_url=f'http://127.0.0.1:{self.f.provider.server_port}/v1', model='fixture',
            activate=True, expected_revision=0))
        model = self.report()['model']
        self.assertEqual('unverified', model['state'])
        self.assertEqual('Replacement', model['provider'])

    def test_gmail_check_runs_once_and_a_lost_answer_is_not_rerun(self):
        self.assertEqual('not_configured', self.report()['gmail']['state'])
        self.call('connections/gmail/check', {}, status=409)
        self.restart_with(gmail_credentials=str(self.f.data/'missing.json'))
        gm = self.report()['gmail']
        self.assertEqual(('error', None), (gm['state'], gm.get('verifiable')))
        self.restart_with(gmail_credentials=str(self.credential(scopes=['https://www.googleapis.com/auth/gmail.readonly'])))
        self.assertIn('发送', self.report()['gmail']['detail'])
        self.credential()
        gm = self.report()['gmail']
        self.assertEqual(('unverified', True), (gm['state'], gm['verifiable']))

        first = self.call('connections/gmail/check', {})
        self.assertEqual('requested', first['state'])
        self.assertEqual(first['requested_at'], self.call('connections/gmail/check', {})['requested_at'])
        self.call('connections/gmail/check/claim', {}, status=403)
        claimed = self.call('connections/gmail/check/claim', {}, role='worker')
        self.call('connections/gmail/check/claim', {}, role='worker', status=409)
        self.call('connections/gmail/check/result', {'attempt_id': claimed['attempt_id'], 'state': 'verified',
                  'account': 'fixture@example.org'}, role='worker')
        gm = self.report()['gmail']
        self.assertEqual(('verified', 'fixture@example.org'), (gm['state'], gm['account']))

        self.call('connections/gmail/check', {})
        lost = self.call('connections/gmail/check/claim', {}, role='worker')
        with test_core.database(self.f.data/'assistant.db') as db:
            db.execute("UPDATE resources SET data=json_set(data,'$.claimed_at',?) WHERE kind='connection_checks'",
                       (int(time.time())-600,))
        gm = self.report()['gmail']
        self.assertEqual('unanswered', gm['check']['state'])
        self.assertEqual('verified', gm['state'], 'previous evidence stays until a new answer arrives')
        self.call('connections/gmail/check/claim', {}, role='worker', status=409)
        self.assertEqual('requested', self.call('connections/gmail/check', {})['state'])
        self.call('connections/gmail/check/result', {'attempt_id': lost['attempt_id'], 'state': 'verified',
                  'account': 'late@example.org'}, role='worker', status=409)

        for path in ('connections', 'state'):
            self.assertNotIn(FIXTURE_REFRESH, json.dumps(self.call(path)))

    def test_worker_reports_one_failed_verification_without_retrying(self):
        from google.auth.exceptions import RefreshError

        class Client:
            def __init__(self): self.calls = []
            def call(self, path, body=None):
                self.calls.append(path)
                return {'attempt_id': 'attempt-one'} if path.endswith('/claim') else {}

        client = Client()
        with patch.object(gmail, 'session', side_effect=RefreshError('revoked')) as session:
            gmail.verify(client, 'unused')
        self.assertEqual(1, session.call_count)
        self.assertEqual(['connections/gmail/check/claim', 'connections/gmail/check/result'], client.calls)

    def test_liveness_comes_from_this_process_and_reports_rejections(self):
        report = self.report()
        self.assertEqual(('not_configured', 'unverified'), (report['phone']['state'], report['worker']['state']))
        device = self.call('devices', {'name': 'Fixture phone'})
        self.assertEqual('unverified', self.report()['phone']['state'])
        self.assertEqual(200, self.raw('state', device['token'], headers={'Host': 'desk.fixture-net.ts.net'}))
        phone = self.report()['phone']
        self.assertEqual('verified', phone['state'])
        self.assertTrue(phone['devices'][0]['tailnet'])
        self.call('state', role='worker')
        self.assertEqual('verified', self.report()['worker']['state'])

        self.call(f"devices/{device['id']}/revoke", {})
        self.assertEqual(401, self.raw('state', device['token']))
        phone = self.report()['phone']
        self.assertEqual('error', phone['state'])
        self.assertIn('Fixture phone', phone['detail'])

        self.call('groups', {'id': '10001', 'title': 'Fixture group', 'active': True})
        self.assertEqual('unverified', self.report()['qq']['state'])
        event = {'group_id': '10001', 'uri': 'msg-1', 'body': 'fixture message'}
        self.assertEqual(401, self.raw('qq/events', 'wrong-token', event))
        self.assertEqual('error', self.report()['qq']['state'])
        qq_token = (self.f.data/'qq.token').read_text().strip()
        self.assertEqual(200, self.raw('qq/events', qq_token, event))
        self.assertEqual('verified', self.report()['qq']['state'])

        self.f.stop(); self.f.start()
        report = self.report()
        self.assertEqual('unverified', report['qq']['state'], 'a previous run proves nothing about now')
        self.assertIn('last_message_at', report['qq'])
        self.assertEqual('unverified', report['worker']['state'])
        self.assertEqual('not_configured', report['phone']['state'])

    def test_settings_page_shows_each_state_and_requests_one_check(self):
        self.restart_with(gmail_credentials=str(self.credential()))
        self.chat_run()
        self.call('groups', {'id': '10001', 'title': 'Fixture group', 'active': True})
        self.raw('qq/events', 'wrong-token', {'group_id': '10001', 'uri': 'm', 'body': 'x'})
        self.call('devices', {'name': 'Fixture phone'})
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{self.f.port}/')
                page.locator('#token').fill((self.f.data/'owner.token').read_text()); page.locator('#connect').click()
                page.locator('#nav').get_by_role('button', name='连接与授权', exact=True).click()
                expected = {'model': '已验证', 'gmail': '未验证', 'qq': '异常', 'phone': '未验证'}
                for cid, label in expected.items():
                    expect(page.locator(f'#connection-{cid} .conn-state')).to_have_text(label)
                expect(page.locator('#connection-qq .conn-detail')).to_contain_text('qq.token')
                # A second read of the same report must leave an open row in place.
                model_row = page.locator('#connection-model')
                model_row.locator('summary').click()
                held = model_row.element_handle()
                refresh = page.get_by_role('button', name='重新读取连接状态', exact=True)
                refresh.click()
                expect(refresh).to_be_enabled()
                self.assertTrue(held.evaluate('e => e.isConnected'))
                expect(model_row).to_have_attribute('open', '')
                gmail_row = page.locator('#connection-gmail')
                gmail_row.locator('summary').click()
                gmail_row.get_by_role('button', name='验证连接', exact=True).click()
                expect(gmail_row.get_by_role('status')).to_contain_text('等待本机执行进程')
                expect(gmail_row.get_by_role('button', name='正在验证…', exact=True)).to_be_disabled()
                claimed = self.call('connections/gmail/check/claim', {}, role='worker')
                self.call('connections/gmail/check/result', {'attempt_id': claimed['attempt_id'], 'state': 'verified',
                          'account': 'fixture@example.org'}, role='worker')
                expect(gmail_row.locator('.conn-state')).to_have_text('已验证', timeout=10000)
                expect(gmail_row).to_contain_text('fixture@example.org')
                with test_core.database(self.f.data/'assistant.db') as db:
                    checks = db.execute("SELECT rev FROM resources WHERE kind='connection_checks'").fetchall()
                self.assertEqual([(3,)], checks, 'one request, one claim, one result')
                self.assertEqual([], errors)
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
