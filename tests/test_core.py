"""Exercise a real compiled core with isolated data and a fake billed provider.

These are failure-path contracts: source privacy, evidence history, offline
replay, stale approvals, process recovery, and durable accounting. No real
emails, admissions forms, personal data, or paid model requests are used.
"""
import base64
from contextlib import contextmanager
import http.server
import json
import os
from pathlib import Path
import socket
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]

@contextmanager
def database(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Provider(http.server.BaseHTTPRequestHandler):
    calls = []
    invalid_usage = False

    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.calls.append(request)
        if self.server.reject:
            self.send_response(401)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        context = json.loads(request['input'])
        source = context['sources'][0] if context['sources'] else None
        ref = f"[source:{source['source_id']}@{source['version_id']}]" if source else ''
        answer = ('截止与活动开始不同。'+ref) if source else ('Conversation: '+json.dumps(context['conversation_history'],ensure_ascii=False))
        response = {'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': answer}]}],
            'usage': {'input_tokens': -100 if self.server.invalid_usage else 100,
                      'output_tokens': 20}}
        raw = json.dumps(response).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class CoreContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='assistant-contract-')
        self.data = Path(self.temp.name)
        self.port = free_port()
        self.provider = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        self.provider.calls = []
        self.provider.invalid_usage = False
        self.provider.reject = False
        self.thread = threading.Thread(target=self.provider.serve_forever, daemon=True)
        self.thread.start()
        self.config = self.data/'config.json'
        self.config.write_text(json.dumps({'weekly_budget_usd': 1, 'provider': {
            'base_url': f'http://127.0.0.1:{self.provider.server_port}/v1',
            'model': 'fixture', 'api_key_env': 'PA_TEST_KEY', 'protocol': 'responses',
            'input_usd_per_million': 1, 'output_usd_per_million': 2}}))
        self.log = open(self.data/'service.log', 'ab')
        self.proc = None
        self.start()

    def command(self, data=None, port=None):
        return [os.environ.get('PA_TEST_EXECUTABLE',str(ROOT/'build/assistant.exe')), '--data', str(data or self.data),
                '--port', str(port or self.port), '--web', str(ROOT/'web'),
                '--config', str(self.config)]

    def start(self):
        env = os.environ.copy()
        env['PA_TEST_KEY'] = 'not-a-real-key'
        self.proc = subprocess.Popen(self.command(), stdout=self.log, stderr=self.log, env=env)
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(100):
            if self.proc.poll() is not None:
                self.fail((self.data/'service.log').read_text())
            try:
                self.http.open(f'http://127.0.0.1:{self.port}/health', timeout=.2).close()
                return
            except OSError:
                time.sleep(.05)
        self.fail('core did not start')

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(10)

    def tearDown(self):
        self.stop()
        self.provider.shutdown()
        self.provider.server_close()
        self.thread.join()
        self.log.close()
        self.temp.cleanup()

    def call(self, path, body=None, *, role='owner', method=None, status=200):
        token = (self.data/(role+'.token')).read_text().strip()
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}/api/{path}',
            data=None if body is None else json.dumps(body).encode(),
            method=method or ('GET' if body is None else 'POST'),
            headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
        try:
            response = self.http.open(req, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read().decode()
            self.assertEqual(status, response.status, raw)
            return json.loads(raw)

    def document(self, text='项目原文\n报名截止 6 月 5 日\n活动开始 7 月 1 日', uri=None):
        return self.call('documents', {'kind': 'file', 'uri': str(uri or self.data/'资料.txt'),
                                      'title': '机器人项目', 'body': text}, role='worker')

    def authorize(self, source):
        return self.call('grants', {'scope': 'source', 'target': source['id']})

    def test_private_sources_and_historical_evidence(self):
        source = self.document()
        self.call('context', {'source_ids': [source['id']]}, role='worker', status=403)
        self.call('grants', {'scope': 'source', 'target': source['id']}, role='worker', status=403)
        self.call('documents', {'kind': 'web', 'uri': 'https://example.org', 'body': 'private'}, role='worker', status=403)
        self.authorize(source)
        self.assertEqual(1, len(self.call('context', {'source_ids': [source['id']]})))
        newer = self.document('新版本\n活动开始 7 月 2 日')
        self.assertNotEqual(source['version_id'], newer['version_id'])
        old = self.call('versions/'+source['version_id'])
        self.assertIn('7 月 1 日', old['body'])
        hits = self.call('search?q='+urllib.parse.quote('7 月 2 日'))
        self.assertEqual(newer['version_id'], hits[0]['id'])
        self.assertEqual(2, hits[0]['line'])
        self.assertEqual([], self.call('search?q='+urllib.parse.quote('7 月 1 日')))
        self.document('intro\nMixedCaseNeedle\nend')
        self.assertEqual('MixedCaseNeedle', self.call('search?q=mixedcaseneedle')[0]['excerpt'])

    def test_offline_replay_and_manual_conflict_resolution(self):
        body = {'title': 'draft', 'body': 'original', 'request_id': 'offline-create'}
        draft = self.call('drafts', body)
        self.assertEqual(draft, self.call('drafts', body))
        self.call('drafts', {**body, 'body': 'changed'}, status=409)
        remote = self.call('drafts/'+draft['id'], {'title': 'draft', 'body': 'server edit',
            'expected_revision': 1, 'request_id': 'server-edit'}, method='PUT')
        edit = {'title': 'draft', 'body': 'phone edit', 'expected_revision': 1, 'request_id': 'phone-edit'}
        conflict = self.call('drafts/'+draft['id'], edit, method='PUT', status=409)['details']['conflict']
        replay = self.call('drafts/'+draft['id'], edit, method='PUT', status=409)
        self.assertEqual(conflict['id'], replay['details']['conflict']['id'])
        self.assertEqual(1, len(self.call('state')['conflicts']))
        resolved = self.call('conflicts/'+conflict['id']+'/resolve',
            {'choice': 'local', 'expected_revision': remote['revision']})
        self.assertEqual('phone edit', resolved['body'])
        self.assertEqual('resolved', self.call('state')['conflicts'][0]['state'])
        self.stop(); self.start()
        self.assertEqual(draft, self.call('drafts', body), 'lost responses survive process restart')

    def test_conflict_choice_uses_displayed_continuation_and_preserves_remote(self):
        draft = self.call('drafts', {'title': 'Fixture', 'body': 'Base'})
        remote = self.call('drafts/'+draft['id'], {'title': 'Server title', 'body': 'Server C',
            'expected_revision': 1}, method='PUT')
        def conflict(request):
            return self.call('drafts/'+draft['id'], {'title': 'Phone title', 'body': 'Attempted A',
                'expected_revision': 1, 'request_id': request}, method='PUT', status=409)['details']['conflict']
        first = conflict('phone-a')
        kept = self.call('conflicts/'+first['id']+'/resolve',
            {'choice': 'remote', 'expected_revision': remote['revision']})
        self.assertEqual('Server C', kept['body'])
        self.assertEqual(remote['revision'], kept['revision'])
        second = conflict('phone-b')
        self.call('conflicts/'+second['id']+'/resolve', {'choice': 'local', 'expected_revision': 1,
            'local': {'title': 'Displayed latest title', 'body': 'Latest B'}}, status=409)
        selected = self.call('conflicts/'+second['id']+'/resolve', {'choice': 'local',
            'expected_revision': remote['revision'],
            'local': {'title': 'Displayed latest title', 'body': 'Latest B'}})
        self.assertEqual('Latest B', selected['body'])
        self.assertEqual('Displayed latest title', selected['title'])
        self.stop(); self.start()
        records = self.call('state')['conflicts']
        chosen = next(c for c in records if c['id'] == second['id'])
        self.assertEqual('Latest B', chosen['selected_local']['body'])

    def test_approval_binds_payload_attachment_and_unknown_never_reclaims(self):
        attachment = self.call('attachments', {'name': 'evidence.txt', 'base64': base64.b64encode(b'proof').decode()})
        payload = {'from': 'sender@example.org', 'to': 'recipient@example.org', 'subject': 'Review',
                   'body': 'Initial', 'attachment_ids': [attachment['id']]}
        op = self.call('operations', {'kind': 'gmail', 'payload': payload})
        self.call('operations/'+op['id']+'/approve', {'revision': op['revision'], 'payload_hash': op['payload_hash']}, role='worker', status=403)
        approved = self.call('operations/'+op['id']+'/approve', {'revision': op['revision'], 'payload_hash': op['payload_hash']})
        changed = self.call('operations/'+op['id'], {'kind': 'gmail', 'payload': {**payload, 'body': 'Actual'},
            'expected_revision': approved['revision']}, method='PUT')
        self.call('operations/'+op['id']+'/claim', {}, role='worker', status=409)
        self.call('operations/'+op['id']+'/approve', {'revision': op['revision'], 'payload_hash': op['payload_hash']}, status=409)
        self.call('operations/'+op['id']+'/approve', {'revision': changed['revision'], 'payload_hash': changed['payload_hash']})
        executing = self.call('operations/'+op['id']+'/claim', {}, role='worker')
        self.assertEqual(attachment['sha256'], executing['payload']['attachments'][0]['sha256'])
        self.call('operations/'+op['id']+'/claim', {}, status=409)
        self.stop(); self.start()
        self.assertEqual('unknown', self.call('state')['operations'][0]['state'])
        self.call('operations/'+op['id']+'/claim', {}, status=409)
        result = self.call('operations/'+op['id']+'/result', {'state': 'succeeded',
            'attempt_id': executing['attempt_id'], 'evidence': 'Fixture sent-folder receipt'})
        self.assertEqual('succeeded', result['state'])

    def test_model_privacy_rule_growth_and_billed_call_not_replayed(self):
        source = self.document()
        task = self.call('tasks', {'title': '比较日期', 'request_id': 'task-one'})
        self.assertEqual(task, self.call('tasks', {'title': '比较日期', 'request_id': 'task-one'}))
        task = self.call('tasks/'+task['id']+'/claim', {}, role='worker')
        request = {'task_id': task['id'], 'attempt_id': task['attempt_id'], 'source_ids': [source['id']]}
        self.call('model/run', request, role='worker', status=403)
        self.assertEqual([], self.provider.calls)
        self.authorize(source)
        rule = self.call('rules', {'title': '日期规则', 'body': '截止不是开始'})
        self.call('rules/'+rule['id']+'/approve', {'revision': rule['revision'], 'hash': rule['hash']})
        (self.data/'rules'/f"{rule['id']}.md").write_text('用户编辑：分别列出截止和开始，缺少日期标为待核实。', 'utf-8')
        result = self.call('model/run', request, role='worker')
        self.call('tasks/'+task['id']+'/heartbeat', {'attempt_id': task['attempt_id']}, role='worker')
        self.assertEqual(result, self.call('model/run', request, role='worker'))
        self.assertEqual(1, len(self.provider.calls))
        self.assertIn('用户编辑', json.loads(self.provider.calls[0]['input'])['approved_rules'][0]['body'])
        self.assertEqual('references_validated', result['citation_status'])
        self.assertGreater(result['cost_usd'], 0)
        self.assertEqual(result['body'], self.call('state')['drafts'][0]['body'])
        self.assertEqual(result['completion_status'],'completed')
        old_attempt=task['attempt_id']
        self.stop();self.start()
        self.assertEqual(result['body'], self.call('state')['drafts'][0]['body'])
        self.call('tasks/'+task['id']+'/resume', {})
        task=self.call('tasks/'+task['id']+'/claim', {})
        self.call('model/run', {**request,'attempt_id':old_attempt},status=409)
        request['attempt_id']=task['attempt_id']
        self.assertEqual(result,self.call('model/run',request))
        self.assertEqual(1,len(self.provider.calls),'recovery reuses the saved paid result')
        self.call('tasks/'+task['id']+'/result', {'state': 'completed', 'attempt_id': 'stale'}, status=409)
        self.call('tasks/'+task['id']+'/result', {'state': 'completed', 'attempt_id': task['attempt_id'], 'result': result})
        second = self.call('tasks', {'title': '另一份通知'})
        second = self.call('tasks/'+second['id']+'/claim', {})
        self.provider.invalid_usage = True
        result = self.call('model/run', {'task_id': second['id'], 'attempt_id': second['attempt_id'], 'source_ids': [source['id']]})
        self.assertIsNone(result['cost_usd'], 'invalid usage must retain a reservation, not credit the budget')
        self.assertGreater(self.call('state')['budget']['committed_usd'], .00014)

    def test_qq_revocation_keeps_old_evidence_and_blocks_new(self):
        event = {'group_id': '1234', 'uri': 'message:42', 'body': '公告', 'title': '群消息'}
        self.call('qq/events', event, role='qq', status=403)
        self.call('groups', {'id': '1234', 'active': True})
        old = self.call('qq/events', event, role='qq')
        self.call('groups', {'id': '1234', 'active': False})
        self.call('qq/events', {**event, 'uri': 'message:43'}, role='qq', status=403)
        self.assertEqual('公告', self.call('context', {'source_ids': [old['id']]})[0]['body'])

    def test_resume_receipt_replay_does_not_restart_a_later_task_round(self):
        task=self.call('tasks',{'title':'Fixture'})
        task=self.call('tasks/'+task['id']+'/claim',{})
        def wait(task):
            return self.call('tasks/'+task['id']+'/result',{'state':'waiting_input',
                'attempt_id':task['attempt_id'],'result':{'message':'Next question'}})
        wait(task)
        reply={'reply':'First answer','request_id':'fixture-reply'}
        receipt=self.call('tasks/'+task['id']+'/resume',reply)
        active=self.call('tasks/'+task['id']+'/claim',{})
        self.assertEqual(receipt,self.call('tasks/'+task['id']+'/resume',reply))
        wait(active)
        self.stop();self.start()
        self.assertEqual(receipt,self.call('tasks/'+task['id']+'/resume',reply))
        self.assertEqual('waiting_input',self.call('state')['tasks'][0]['state'])

    def test_failed_model_call_requires_explicit_retry_and_keeps_original_cost(self):
        source=self.document();self.authorize(source)
        task=self.call('tasks',{'title':'Fixture'})
        task=self.call('tasks/'+task['id']+'/claim',{})
        request={'task_id':task['id'],'attempt_id':task['attempt_id'],'source_ids':[source['id']]}
        self.provider.reject=True
        self.call('model/run',request,status=502)
        self.call('tasks/'+task['id']+'/result',{'state':'blocked','attempt_id':task['attempt_id'],'result':{}})
        state=self.call('state');run=state['model_runs'][0]
        self.assertEqual('unknown',run['state'])
        held=state['budget']['committed_usd']
        approval={'expected_revision':run['revision'],'accept_unknown_cost':True,'reason':'Fixture: corrected configuration; accept possible prior charge'}
        self.call('model-runs/'+run['id']+'/retry',approval,role='worker',status=403)
        self.call('model-runs/'+run['id']+'/retry',{**approval,'expected_revision':0},status=409)
        self.call('tasks/'+task['id']+'/resume',{})
        resumed=self.call('tasks/'+task['id']+'/claim',{})
        self.call('model/run',{**request,'attempt_id':resumed['attempt_id']},status=409)
        self.assertEqual(1,len(self.provider.calls),'No automatic second paid request')
        self.call('tasks/'+task['id']+'/result',{'state':'blocked','attempt_id':resumed['attempt_id'],'result':{}})
        self.call('model-runs/'+run['id']+'/retry',approval)
        self.assertEqual(held,self.call('state')['budget']['committed_usd'])
        self.call('model-runs/'+run['id']+'/retry',approval,status=409)
        self.provider.reject=False
        next_attempt=self.call('tasks/'+task['id']+'/claim',{})
        result=self.call('model/run',{**request,'attempt_id':next_attempt['attempt_id']})
        self.assertEqual(2,len(self.provider.calls))
        self.assertGreaterEqual(self.call('state')['budget']['committed_usd'],held+result['cost_usd'])

    def test_local_index_tracks_empty_and_deleted_files_without_losing_history(self):
        sys.path.insert(0,str(ROOT/'connectors'))
        import indexer
        fixture=self
        class Adapter:
            def call(self,path,body=None):return fixture.call(path,body,role='worker')
        # Windows' normal temp directory is in AppData, which the production
        # scanner intentionally excludes. Use an owned eligible fixture folder.
        with tempfile.TemporaryDirectory(prefix='assistant-index-fixture-',dir=ROOT.parent) as folder:
            root=Path(folder)
            path=root/'project.txt';path.write_text('Fixture historical evidence','utf-8')
            manifest=self.data/'index.json'
            self.assertEqual(1,indexer.scan(Adapter(),[root],manifest))
            source=self.call('state')['sources'][0];self.authorize(source)
            self.assertEqual(1,len(self.call('search?q=historical')))
            moved=root.with_name(root.name+'-offline')
            root.rename(moved)
            try:
                indexer.scan(Adapter(),[root],manifest)
                self.assertEqual(1,len(self.call('search?q=historical')),'An unavailable root cannot imply deletion')
            finally:
                moved.rename(root)
            def incomplete_walk(_root, *, onerror, **_kwargs):
                onerror(PermissionError('Fixture subtree inaccessible'))
                return iter(())
            with mock.patch.object(indexer.os,'walk',incomplete_walk):
                indexer.scan(Adapter(),[root],manifest)
            self.assertEqual(1,len(self.call('search?q=historical')),'A traversal error cannot imply deletion')
            path.write_text('','utf-8');indexer.scan(Adapter(),[root],manifest)
            self.assertEqual([],self.call('search?q=historical'))
            self.call('context',{'source_ids':[source['id']]},status=409)
            self.assertEqual('Fixture historical evidence',self.call('versions/'+source['version_id'])['body'])
            path.write_text('Fixture current evidence','utf-8');indexer.scan(Adapter(),[root],manifest)
            self.assertEqual(1,len(self.call('search?q=current')))
            path.unlink();indexer.scan(Adapter(),[root],manifest)
            self.assertEqual([],self.call('search?q=current'))
            self.assertFalse(self.call('state')['sources'][0]['available'])

    def test_shared_text_becomes_visible_searchable_material_without_cloud_permission(self):
        sys.path.insert(0,str(ROOT/'connectors'))
        import materials
        content='虚构项目经历\n实现了机器人仿真，尚未形成论文。'
        attachment=self.call('attachments',{'name':'project.txt','base64':base64.b64encode(content.encode()).decode()})
        visible=self.call('state')['attachments'][0]
        self.assertEqual(attachment['id'],visible['id'])
        self.assertNotIn('base64',visible)
        fixture=self
        class Adapter:
            def call(self,path,body=None):return fixture.call(path,body,role='worker')
        materials.process(Adapter(),[visible],self.data)
        state=self.call('state')
        self.assertEqual('indexed',state['attachments'][0]['extraction_status'])
        source=state['sources'][0]
        self.assertEqual('shared',source['kind'])
        self.assertEqual(source['id'],state['attachments'][0]['source_id'])
        self.assertFalse(source['cloud_default'])
        self.call('context',{'source_ids':[source['id']]},status=403)
        hit=self.call('search?q='+urllib.parse.quote('尚未形成论文'))[0]
        self.assertEqual(2,hit['line'])
        self.assertEqual(content,self.call('versions/'+source['version_id'])['body'])
        replay=self.call('attachments',{'name':'project.txt','base64':base64.b64encode(content.encode()).decode()})
        self.assertEqual('indexed',replay['extraction_status'])

    def test_device_revocation_and_browser_actions_are_not_replayed(self):
        device = self.call('devices', {'name': 'Fixture phone'})
        (self.data/'phone.token').write_text(device['token'])
        state = self.call('state', role='phone')
        self.assertNotIn('credential_hash', json.dumps(state))
        self.call('browser/view', role='worker', status=403)
        frame = {'session_id': 'fixture-session', 'url': 'https://example.org',
                 'image_base64': base64.b64encode(b'fixture screenshot').decode()}
        self.call('browser/publish', frame, role='worker')
        view = self.call('browser/view', role='phone')
        click = {'session_id': view['session_id'], 'generation': view['generation'],
                 'action': 'click', 'x': 10, 'y': 20}
        command = self.call('browser/command', click, role='phone')
        self.call('browser/command', click, role='phone', status=409)
        self.call('browser/publish', {**frame,'command_id':command['id'],'image_base64':'bad'}, role='worker',status=400)
        self.assertTrue(self.call('browser/view',role='phone')['pending'])
        self.assertEqual(command['id'], self.call('browser/poll', {}, role='worker')['command']['id'])
        self.assertIsNone(self.call('browser/poll', {}, role='worker')['command'])
        self.call('browser/publish', {**frame, 'command_id': command['id']}, role='worker')
        self.call('browser/command', click, role='phone', status=409)
        newer=self.call('browser/view',role='phone')
        self.call('browser/command',{**click,'generation':newer['generation']},role='phone')
        self.call('browser/publish',{**frame,'image_base64':base64.b64encode(b'changed').decode()},role='worker')
        self.assertIsNone(self.call('browser/poll',{},role='worker')['command'],'obsolete coordinates are discarded')
        self.call('devices/'+device['id']+'/revoke', {})
        self.call('state', role='phone', status=401)

    def test_atomic_writes_single_instance_and_consistent_backup(self):
        self.call('drafts', {'title': 'Keep', 'body': 'Durable'})
        second = subprocess.run(self.command(port=free_port()), capture_output=True, timeout=10)
        self.assertNotEqual(0, second.returncode)
        with database(self.data/'assistant.db') as db:
            db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'fixture disk failure'); END")
        self.call('drafts', {'title': 'Should rollback', 'body': 'No orphan resource'}, status=500)
        self.assertEqual(1, len(self.call('state')['drafts']))
        with database(self.data/'assistant.db') as db:
            db.execute('DROP TRIGGER reject_event')
        backup = self.call('backup', {})
        with database(Path(backup['path'])/'assistant.db') as db:
            self.assertEqual('ok', db.execute('PRAGMA integrity_check').fetchone()[0])
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM resources WHERE kind='drafts'").fetchone()[0])
        self.stop()
        original_data=self.data
        restored=original_data/'restored'
        restored.mkdir()
        shutil.copy2(Path(backup['path'])/'assistant.db',restored/'assistant.db')
        try:
            self.data=restored
            self.start()
            self.assertEqual('Durable',self.call('state')['drafts'][0]['body'])
            created=self.call('drafts',{'title':'After restore','body':'New work remains writable'})
            self.assertEqual(1,created['revision'])
        finally:
            self.stop()
            self.data=original_data
        with database(self.data/'assistant.db') as db:
            db.execute('PRAGMA user_version=2')
        refused = subprocess.run(self.command(), capture_output=True, timeout=10)
        self.assertNotEqual(0, refused.returncode)
        with database(self.data/'assistant.db') as db:
            self.assertEqual(2, db.execute('PRAGMA user_version').fetchone()[0])

    def test_recurring_check_in_survives_restart_without_duplicate_backlog(self):
        due={'kind':'check_in','title':'Project progress check-in','interval_seconds':900,'next_at':0}
        self.call('schedules',due,role='worker',status=403)
        schedule=self.call('schedules',due)
        for _ in range(70):
            tasks=self.call('state')['tasks']
            if tasks:break
            time.sleep(.1)
        self.assertEqual(1,len(tasks))
        self.assertEqual('waiting_input',tasks[0]['state'])
        self.stop();self.start()
        state=self.call('state')
        self.assertEqual(tasks[0]['id'],state['tasks'][0]['id'])
        current=state['schedules'][0]
        self.call('schedules',{**due,'id':schedule['id'],'expected_revision':current['revision']})
        for _ in range(70):
            state=self.call('state')
            if state['schedules'][0]['next_at']>0:break
            time.sleep(.1)
        self.assertEqual(1,len(state['tasks']),'An unanswered check-in coalesces missed intervals')


if __name__ == '__main__':
    unittest.main(verbosity=2)
