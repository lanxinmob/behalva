"""Provider setup must protect keys and survive hot switching and restart.

The upstream is a loopback fixture. No personal credentials or paid calls.
"""
import json
import threading
import unittest
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
import test_core


class Catalog(test_core.Provider):
    def do_GET(self):
        self.server.credentials.append((self.path, self.headers.get('Authorization')))
        raw = json.dumps({'data': [{'id': 'fixture'}, {'id': 'fixture'}, {'id': 'alternate'}]}).encode()
        self.send_response(self.server.catalog_status)
        if self.server.catalog_status==302:
            self.send_header('Location',f'http://127.0.0.1:{self.server.server_port}/redirected')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        self.server.credentials.append((self.path, self.headers.get('Authorization')))
        if self.server.pause:
            self.server.started.set()
            self.server.release.wait(10)
        if self.path.endswith('/chat/completions'):
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.server.calls.append(request)
            raw=json.dumps({'choices':[{'message':{'content':'Fixture chat reply'},'finish_reason':'stop'}],
                            'usage':{'prompt_tokens':100,'completion_tokens':20}}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        else:
            super().do_POST()


class Providers(unittest.TestCase):
    def setUp(self):
        self.f = test_core.CoreContracts()
        self.f.setUp()
        self.f.provider.RequestHandlerClass = Catalog
        self.f.provider.credentials = []
        self.f.provider.catalog_status = 200
        self.f.provider.pause = False
        self.f.provider.started = threading.Event()
        self.f.provider.release = threading.Event()
        self.call = self.f.call
        self.base = f'http://127.0.0.1:{self.f.provider.server_port}/v1'

    def tearDown(self):
        self.f.provider.release.set()
        self.f.tearDown()

    def values(self, **changes):
        p = dict(id=uuid.uuid4().hex, name='Fixture supplier', api_key='fixture-secret-one',
                 base_url=self.base, model='fixture', activate=True, expected_revision=0,
                 input_usd_per_million=1, output_usd_per_million=2)
        p.update(changes)
        return p

    def message(self, cid=None, sequence=0):
        m = self.call('messages', dict(conversation_id=cid or uuid.uuid4().hex,
                body='Fixture project', expected_sequence=sequence, request_id=uuid.uuid4().hex))
        task = self.call('tasks/'+m['task_id']+'/claim', {}, role='worker')
        return dict(task_id=task['id'], attempt_id=task['attempt_id'], source_ids=[])

    def test_save_restart_dispatch_and_credentials_are_never_synced(self):
        p = self.call('providers', self.values(base_url=self.base+'/responses/'))
        self.assertEqual(self.base, p['base_url'])
        self.assertEqual('responses', p['protocol'])
        self.call('providers/models', dict(id=p['id'], base_url=self.base))
        self.assertEqual((self.base.removeprefix(f'http://127.0.0.1:{self.f.provider.server_port}')+'/models',
                          'Bearer fixture-secret-one'), self.f.provider.credentials[-1])
        self.f.stop(); self.f.start()
        self.assertTrue(self.call('state')['model_configured'])
        self.call('model/run', self.message(), role='worker')
        self.assertEqual('Bearer fixture-secret-one', self.f.provider.credentials[-1][1])
        public = json.dumps([self.call('providers'), self.call('state'), self.call('setup')])
        self.assertNotIn('fixture-secret-one', public)
        with test_core.database(self.f.data/'assistant.db') as db:
            for table in ['resources', 'events', 'model_providers']:
                self.assertNotIn('fixture-secret-one', str(db.execute('SELECT * FROM '+table).fetchall()))
        self.assertNotIn('fixture-secret-one', (self.f.data/'service.log').read_text())

    def test_unrecoverable_migrated_key_stays_blocked_until_replaced(self):
        p = self.call('providers', self.values())
        self.f.stop()
        with test_core.database(self.f.data/'assistant.db') as db:
            p.update(has_key=False, credential_unavailable=True)
            db.execute('UPDATE model_providers SET metadata=? WHERE id=?', (json.dumps(p),p['id']))
        self.f.start()
        self.assertFalse(self.call('state')['model_configured'])
        self.call('providers/models', dict(id=p['id'],base_url=self.base), status=503)
        self.call('model/run', {}, role='worker', status=503)
        self.assertEqual([],self.f.provider.credentials)
        with test_core.database(self.f.data/'assistant.db') as db:
            self.assertEqual(0,db.execute('SELECT count(*) FROM usage').fetchone()[0])
        self.call('providers', self.values(id=p['id'],expected_revision=p['revision'],api_key=''), status=400)
        self.assertTrue(self.call('providers')['providers'][0]['credential_unavailable'])
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':390,'height':844})
                page.goto(f'http://127.0.0.1:{self.f.port}/')
                page.locator('#token').fill((self.f.data/'owner.token').read_text());page.locator('#connect').click()
                page.get_by_role('button',name='打开导航',exact=True).click()
                page.get_by_role('button',name='连接与授权',exact=True).click()
                expect(page.locator('.provider-row')).to_contain_text('原系统密钥无法恢复')
                page.get_by_role('button',name='编辑 Fixture supplier',exact=True).click()
                dialog=page.locator('#provider-editor')
                expect(dialog.get_by_label('API Key',exact=True)).to_have_attribute('placeholder','需要重新填写 API Key')
                dialog.get_by_label('API Key',exact=True).fill('fixture-restored-key')
                dialog.get_by_role('button',name='保存并使用',exact=True).click()
                expect(dialog).to_have_count(0)
            finally:browser.close()
        self.assertTrue(self.call('state')['model_configured'])
        self.assertNotIn('credential_unavailable',self.call('providers')['providers'][0])
        self.call('providers/models',dict(id=p['id'],base_url=self.base))
        self.assertEqual('Bearer fixture-restored-key',self.f.provider.credentials[-1][1])

    def test_http_hostname_catalog_restart_and_chat(self):
        # localhost is deliberately outside the old 127.0.0.1-only HTTP allowance.
        base = f'http://localhost:{self.f.provider.server_port}/v1'
        p = self.call('providers', self.values(base_url=base+'/responses'))
        self.assertEqual(base, p['base_url'])
        catalog = self.call('providers/models', dict(id=p['id'], base_url=base))
        self.assertEqual(['alternate', 'fixture'], catalog['models'])
        self.assertEqual(('/v1/models', 'Bearer fixture-secret-one'), self.f.provider.credentials[-1])
        self.f.stop(); self.f.start()
        result = self.call('model/run', self.message(), role='worker')
        self.assertIn('Conversation:', result['body'])
        self.assertEqual(('/v1/responses', 'Bearer fixture-secret-one'), self.f.provider.credentials[-1])
        self.assertGreater(self.call('state')['budget']['committed_usd'], 0)
        # Accept a remote HTTP address without connecting to a real upstream.
        remote = self.call('providers', self.values(base_url='http://api.example.test:9191/v1/chat/completions', activate=False))
        self.assertEqual('http://api.example.test:9191/v1', remote['base_url'])
        self.assertEqual('chat_completions', remote['protocol'])

    def test_only_computer_owner_can_manage_and_bad_url_cannot_receive_old_key(self):
        p = self.call('providers', self.values())
        device = self.call('devices', dict(name='Fixture phone'))
        (self.f.data/'device.token').write_text(device['token'])
        for role in ['worker', 'device']:
            self.call('providers', role=role, status=403)
            self.call('providers/models', dict(id=p['id'], base_url=self.base), role=role, status=403)
            self.call('providers', self.values(), role=role, status=403)
            self.call('providers/activate', dict(id=p['id'],expected_revision=p['revision']),role=role,status=403)
        for url in ['ftp://example.org/v1', 'http://user:pass@example.org/v1', 'https://user:pass@example.org/v1',
                    'https://example.org/v1?key=foo', 'https://example.org:99999/v1']:
            self.call('providers/models', dict(base_url=url,api_key='fixture-secret'), status=400)
        self.call('providers/models', dict(id=p['id'],base_url='https://example.org/v1'),status=400)
        self.call('providers', {**p, 'base_url':'https://example.org/v1','api_key':'',
                              'expected_revision':p['revision']},status=400)
        self.assertEqual([],self.f.provider.credentials)
        self.call('providers', self.values(id=p['id']),status=409)

    def test_unknown_prices_estimate_and_probe_does_not_claim_chat_works(self):
        p = self.call('providers', self.values(input_usd_per_million=None,output_usd_per_million=None))
        self.assertTrue(self.call('state')['model_configured'])
        self.assertEqual('estimate',p['pricing_mode'])
        self.assertNotIn('input_usd_per_million',p,'a planning rate must not be stored as a supplier quote')
        models=self.call('providers/models',dict(id=p['id'],base_url=self.base))
        self.assertEqual(['alternate','fixture'],models['models'])
        self.assertEqual([],self.f.provider.calls)
        self.assertEqual([],self.call('state')['model_runs'])
        self.f.stop();self.f.start()
        reply=self.call('model/run',self.message(),role='worker')
        self.assertEqual('estimate',reply['cost_basis'])
        self.assertAlmostEqual(.002,reply['cost_usd'])
        budget=self.call('state')['budget']
        self.assertAlmostEqual(.002,budget['committed_usd'])
        self.assertAlmostEqual(.002,budget['estimated_usd'])
        with test_core.database(self.f.data/'assistant.db') as db:
            self.assertEqual('estimated',db.execute('SELECT status FROM usage').fetchone()[0])
        self.f.provider.catalog_status=401
        error=self.call('providers/models',dict(id=p['id'],base_url=self.base),status=502)
        self.assertNotIn('fixture-secret-one',json.dumps(error))
        self.f.provider.catalog_status=302
        before=len(self.f.provider.credentials)
        self.call('providers/models',dict(id=p['id'],base_url=self.base),status=502)
        self.assertEqual(before+1,len(self.f.provider.credentials),'API key must not follow redirects')

    def test_estimates_enforce_budget_and_retain_unknown_reservations(self):
        self.call('providers',self.values(input_usd_per_million=None,output_usd_per_million=None))
        self.f.stop()
        config=json.loads(self.f.config.read_text());config['weekly_budget_usd']=.000001
        self.f.config.write_text(json.dumps(config));self.f.start()
        self.call('model/run',self.message(),role='worker',status=402)
        self.assertEqual([],self.f.provider.calls,'no-price setup must not bypass the weekly budget')
        self.assertEqual([],self.call('state')['model_runs'])
        self.f.stop();config['weekly_budget_usd']=1
        self.f.config.write_text(json.dumps(config));self.f.start()
        self.f.provider.reject=True
        request=self.message()
        self.call('model/run',request,role='worker',status=502)
        held=self.call('state')['budget']['committed_usd']
        self.assertGreater(held,0)
        self.assertEqual('estimate',self.call('state')['model_runs'][0]['pricing_mode'])
        self.call('model/run',request,role='worker',status=409)
        self.assertEqual(1,len(self.f.provider.calls),'unknown estimated calls must not be retried automatically')
        self.f.stop();self.f.start()
        self.assertEqual(held,self.call('state')['budget']['committed_usd'])

    def test_legacy_no_price_setup_and_explicit_custom_price_validation(self):
        self.f.stop();config=json.loads(self.f.config.read_text())
        for field in ['input_usd_per_million','output_usd_per_million']:config['provider'].pop(field)
        self.f.config.write_text(json.dumps(config));self.f.start()
        self.assertTrue(self.call('state')['model_configured'])
        reply=self.call('model/run',self.message(),role='worker')
        self.assertEqual('estimate',reply['cost_basis'])
        for changes in [dict(pricing_mode='custom',output_usd_per_million=None),
                        dict(pricing_mode='other'),dict(pricing_mode=2),
                        dict(pricing_mode='custom',input_usd_per_million=-1)]:
            self.call('providers',self.values(**changes),status=400)
        self.assertEqual([],self.call('providers')['providers'])
        saved=self.call('providers',self.values())
        self.f.stop()
        # Simulate the persisted provider format from before pricing modes existed.
        with test_core.database(self.f.data/'assistant.db') as db:
            old=json.loads(db.execute('SELECT metadata FROM model_providers WHERE id=?',(saved['id'],)).fetchone()[0])
            old.pop('pricing_mode')
            db.execute('UPDATE model_providers SET metadata=? WHERE id=?',(json.dumps(old),saved['id']))
        self.f.start()
        reply=self.call('model/run',self.message(),role='worker')
        self.assertEqual('custom_price',reply['cost_basis'])
        self.assertAlmostEqual(.00014,reply['cost_usd'])
        self.assertAlmostEqual(.002,self.call('state')['budget']['estimated_usd'])
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                page=browser.new_page();page.goto(f'http://127.0.0.1:{self.f.port}/')
                page.locator('#token').fill((self.f.data/'owner.token').read_text());page.locator('#connect').click()
                page.locator('#nav').get_by_role('button',name='连接与授权',exact=True).click()
                expect(page.locator('.provider-row')).to_contain_text('费用按手动报价计算')
                page.get_by_role('button',name='编辑 Fixture supplier',exact=True).click()
                page.locator('#provider-editor summary').click()
                self.assertEqual('custom',page.get_by_label('费用计算',exact=True).input_value())
                self.assertEqual('1',page.get_by_label('输入单价',exact=True).input_value())
            finally:browser.close()

    def test_full_chat_endpoint_is_normalized_and_reaches_the_right_protocol(self):
        p=self.call('providers',self.values(base_url=self.base+'/chat/completions'))
        self.assertEqual('chat_completions',p['protocol'])
        result=self.call('model/run',self.message(),role='worker')
        self.assertEqual('Fixture chat reply',result['body'])
        self.assertEqual('custom_price',result['cost_basis'])
        self.assertEqual('/v1/chat/completions',self.f.provider.credentials[-1][0])
        self.assertIn('messages',self.f.provider.calls[-1])
        self.assertNotIn('input',self.f.provider.calls[-1])
        run=self.call('state')['model_runs'][0]
        self.assertEqual(p['id'],run['provider_id'])
        self.assertEqual(p['revision'],run['provider_revision'])

    def test_switch_while_call_is_in_flight_keeps_original_key_and_price(self):
        first=self.call('providers',self.values())
        second=self.call('providers',self.values(name='Second',api_key='fixture-secret-two',
                                                activate=False,input_usd_per_million=5,output_usd_per_million=7))
        self.f.provider.pause=True
        request=self.message();results=[]
        def run():
            try:results.append(self.call('model/run',request,role='worker'))
            except Exception as error:results.append(error)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(self.f.provider.started.wait(5))
            self.call('providers/activate',dict(id=second['id'],expected_revision=second['revision']))
        finally:
            self.f.provider.release.set();thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(1,len(results));self.assertIsInstance(results[0],dict)
        self.assertEqual('Bearer fixture-secret-one',self.f.provider.credentials[-1][1])
        self.assertAlmostEqual(.00014,self.call('state')['budget']['committed_usd'])
        self.f.provider.pause=False
        self.call('model/run',self.message(),role='worker')
        self.assertEqual('Bearer fixture-secret-two',self.f.provider.credentials[-1][1])
        self.assertAlmostEqual(.00078,self.call('state')['budget']['committed_usd'])

    def test_browser_add_edit_switch_and_save_without_restart(self):
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{self.f.port}/')
                page.locator('#token').fill((self.f.data/'owner.token').read_text());page.locator('#connect').click()
                page.locator('#nav').get_by_role('button',name='连接与授权',exact=True).click()
                page.get_by_role('button',name='添加供应商',exact=True).click()
                dialog=page.locator('#provider-editor')
                dialog.get_by_label('名称',exact=True).fill('Fixture visible supplier')
                dialog.get_by_label('API Key',exact=True).fill('fixture-secret-one')
                notice=dialog.get_by_text('HTTP：API Key 和资料会明文传输，建议使用 HTTPS。',exact=True)
                expect(notice).to_be_hidden()
                dialog.get_by_label('API 请求地址',exact=True).fill(f'http://localhost:{self.f.provider.server_port}/v1')
                expect(notice).to_be_visible()
                dialog.get_by_label('API 请求地址',exact=True).fill('https://api.example.test/v1')
                expect(notice).to_be_hidden()
                dialog.get_by_label('API 请求地址',exact=True).fill(f'http://localhost:{self.f.provider.server_port}/v1')
                dialog.get_by_role('button',name='获取可用模型',exact=True).click()
                expect(dialog.get_by_role('status').first).to_contain_text('2 个模型')
                dialog.get_by_label('模型 ID',exact=True).fill('fixture')
                dialog.locator('summary').click()
                expect(dialog.get_by_label('输入单价',exact=True)).to_be_hidden()
                expect(dialog.get_by_label('输出单价',exact=True)).to_be_hidden()
                dialog.get_by_label('费用计算',exact=True).select_option('custom')
                dialog.get_by_label('输入单价',exact=True).fill('1')
                dialog.get_by_label('输出单价',exact=True).fill('2')
                dialog.get_by_label('模型 ID',exact=True).fill('alternate')
                self.assertEqual('',dialog.get_by_label('输入单价',exact=True).input_value())
                self.assertEqual('',dialog.get_by_label('输出单价',exact=True).input_value())
                self.assertEqual('estimate',dialog.get_by_label('费用计算',exact=True).input_value())
                expect(dialog.get_by_label('输入单价',exact=True)).to_be_hidden()
                dialog.get_by_label('模型 ID',exact=True).fill('fixture')
                evidence=Path(__file__).resolve().parents[1]/'build/evidence/providers';evidence.mkdir(parents=True,exist_ok=True)
                # Clear the fake key before screenshots so the workflow does not normalize recording secrets.
                dialog.get_by_label('API Key',exact=True).fill('')
                dialog.locator('summary').click()
                dialog.evaluate('(e)=>e.scrollTop=0')
                for width,height in [(1440,900),(390,844)]:
                    page.set_viewport_size(dict(width=width,height=height))
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                    self.assertGreaterEqual(dialog.get_by_role('heading',name='添加供应商',exact=True).bounding_box()['y'],0)
                    page.screenshot(path=str(evidence/f'editor-{width}.png'))
                dialog.get_by_label('API Key',exact=True).fill('fixture-secret-one')
                held=[]
                def hold_save(route):
                    if route.request.method=='POST':held.append(route)
                    else:route.continue_()
                page.route('**/api/providers',hold_save)
                dialog.get_by_role('button',name='保存并使用',exact=True).click()
                self.assertEqual(1,len(held))
                expect(dialog.get_by_role('button',name='仅保存',exact=True)).to_be_disabled()
                held[0].continue_()
                expect(dialog).to_have_count(0)
                page.unroute('**/api/providers',hold_save)
                self.assertEqual(1,len(self.call('providers')['providers']))
                self.assertEqual('estimate',self.call('providers')['providers'][0]['pricing_mode'])
                expect(page.locator('.provider-row')).to_contain_text('Fixture visible supplier')
                self.assertTrue(self.call('state')['model_configured'])
                self.assertNotIn('fixture-secret-one',page.content())
                page.get_by_role('button',name='编辑 Fixture visible supplier',exact=True).click()
                dialog=page.locator('#provider-editor')
                self.assertEqual('',dialog.get_by_label('API Key',exact=True).input_value())
                dialog.get_by_label('名称',exact=True).fill('Renamed supplier')
                dialog.get_by_role('button',name='保存并使用',exact=True).click()
                expect(page.locator('.provider-row')).to_contain_text('Renamed supplier')
                page.screenshot(path=str(evidence/'settings-mobile.png'))
                page.get_by_role('button',name='编辑 Renamed supplier',exact=True).click()
                dialog=page.locator('#provider-editor');dialog.get_by_label('API 请求地址',exact=True).fill(self.base+'/other')
                self.assertEqual('',dialog.get_by_label('输入单价',exact=True).input_value())
                self.assertEqual('',dialog.get_by_label('输出单价',exact=True).input_value())
                expect(dialog.get_by_role('status').last).to_contain_text('费用自动估算')
                dialog.get_by_role('button',name='取消',exact=True).click()
                self.assertEqual([],errors)
                reply=self.call('model/run',self.message(),role='worker')
                self.assertEqual('estimate',reply['cost_basis'])
                self.assertEqual('Bearer fixture-secret-one',self.f.provider.credentials[-1][1])
            finally:browser.close()
