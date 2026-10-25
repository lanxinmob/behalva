"""Real browser interaction against the compiled core, with fixture-only data."""
from pathlib import Path
import unittest
from playwright.sync_api import sync_playwright, expect
import test_core


class WorkbenchFlow(unittest.TestCase):
    def test_chat_keyboard_send_preserves_composition_multiline_and_pending_input(self):
        fixture=test_core.CoreContracts();fixture.setUp()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                try:
                    page=browser.new_page(viewport={'width':1100,'height':850})
                    page.clock.install()
                    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    posts=[];page.on('request',lambda r:posts.append(r) if r.method=='POST' and r.url.endswith('/api/messages') else None)
                    page.goto(f'http://127.0.0.1:{fixture.port}/')
                    page.locator('#token').fill((fixture.data/'owner.token').read_text());page.locator('#connect').click()
                    page.get_by_role('button',name='聊天',exact=True).click()
                    input=page.get_by_label('消息',exact=True)
                    input.fill('   ');input.press('Enter');input.press('Control+Enter')
                    self.assertEqual([],posts)
                    input.fill('输入法待确认')
                    original_input=input.element_handle()
                    held_poll=[];page.route('**/api/state',lambda route:held_poll.append(route))
                    with page.expect_request('**/api/state'):
                        page.clock.fast_forward(4001)
                    input.dispatch_event('compositionstart')
                    fixture.call('goals',{'title':'A change arriving during composition'})
                    with page.expect_response('**/api/state') as poll:
                        held_poll[0].fulfill(json=fixture.call('state'))
                    poll.value.json();page.clock.run_for(50)
                    self.assertTrue(original_input.evaluate('(e)=>e.isConnected'),'polling must not replace an active IME input')
                    page.unroute('**/api/state')
                    input.press('Enter')
                    input.dispatch_event('compositionend')
                    input.dispatch_event('keydown',{'key':'Enter','isComposing':True})
                    input.dispatch_event('keydown',{'key':'Enter','keyCode':229})
                    self.assertEqual([],posts,'IME confirmation must not send a message')
                    input.fill('Line one');input.press('Shift+Enter');input.press('L')
                    body=input.input_value()
                    self.assertEqual('Line one\nL',body)
                    input.press('Enter')
                    expect(page.locator('.chat-log .user')).to_have_count(1)
                    self.assertEqual(body,fixture.call('state')['messages'][0]['body'])
                    input.fill('Next thought');input.press('Control+Enter');input.press('Enter')
                    self.assertEqual(1,len(posts),'busy chat must not submit another message')
                    expect(input).to_have_value('Next thought')
                    task=fixture.call('state')['tasks'][0]
                    claimed=fixture.call('tasks/'+task['id']+'/claim',{},role='worker')
                    fixture.call('model/run',dict(task_id=task['id'],attempt_id=claimed['attempt_id'],source_ids=[]),role='worker')
                    page.get_by_role('button',name='同步',exact=True).click()
                    expect(page.get_by_role('button',name='发送',exact=True)).to_be_enabled()
                    held=[];page.route('**/api/messages',lambda route:held.append(route))
                    input.press('Control+Enter')
                    expect(page.get_by_role('button',name='发送',exact=True)).to_be_disabled()
                    input.press('Control+Enter');input.dispatch_event('keydown',{'key':'Enter','repeat':True})
                    self.assertEqual(1,len(held),'keyboard sending must share the button in-flight guard')
                    input.fill('Draft typed during send')
                    next_input=input.element_handle();input.dispatch_event('compositionstart')
                    with page.expect_response('**/api/state') as send_sync:
                        held[0].continue_()
                    send_sync.value.json();page.clock.run_for(50)
                    self.assertTrue(next_input.evaluate('(e)=>e.isConnected'),'the previous send response must not interrupt the next IME session')
                    expect(input).to_have_value('Draft typed during send')
                    expect(page.get_by_role('button',name='发送',exact=True)).to_be_disabled()
                    input.dispatch_event('compositionend')
                    expect(page.locator('.chat-log .user')).to_have_count(2)
                    expect(input).to_have_value('Draft typed during send')
                    page.unroute('**/api/messages')
                    mobile=browser.new_page(viewport={'width':390,'height':844},has_touch=True,is_mobile=True)
                    mobile.on('pageerror',lambda e:errors.append(str(e)))
                    mobile.goto(f'http://127.0.0.1:{fixture.port}/')
                    mobile.locator('#token').fill((fixture.data/'owner.token').read_text());mobile.locator('#connect').click()
                    mobile.get_by_role('button',name='打开导航',exact=True).click()
                    mobile.get_by_role('button',name='聊天',exact=True).click()
                    mobile.get_by_role('button',name='＋ 新对话',exact=True).click()
                    phone_input=mobile.get_by_label('消息',exact=True)
                    phone_input.fill('Mobile draft');phone_input.press('Enter')
                    expect(phone_input).to_have_value('Mobile draft\n')
                    self.assertEqual(2,sum(m['role']=='user' for m in fixture.call('state')['messages']),'mobile newline must not send')
                    mobile.get_by_role('button',name='发送',exact=True).click()
                    expect(mobile.locator('.chat-log .user')).to_have_count(1)
                    self.assertEqual([],errors)
                finally:browser.close()
        finally:fixture.tearDown()

    def test_chat_preserves_next_input_and_stale_conversion_form(self):
        fixture=test_core.CoreContracts('test_offline_replay_and_manual_conflict_resolution');fixture.setUp()
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                try:
                    page=browser.new_page(viewport={'width':1100,'height':850})
                    page.goto(f'http://127.0.0.1:{fixture.port}/')
                    page.locator('#token').fill((fixture.data/'owner.token').read_text());page.locator('#connect').click()
                    page.get_by_role('button',name='聊天',exact=True).click()
                    # Hold the POST response while the user types the next thought.
                    intercepted=[]
                    page.route('**/api/messages',lambda route:intercepted.append(route))
                    page.get_by_label('消息',exact=True).fill('First fixture message')
                    page.get_by_role('button',name='发送',exact=True).click()
                    page.get_by_label('消息',exact=True).fill('Next thought must survive')
                    self.assertEqual(1,len(intercepted));intercepted[0].continue_()
                    expect(page.locator('.chat-log .user')).to_have_count(1)
                    expect(page.get_by_label('消息',exact=True)).to_have_value('Next thought must survive')
                    page.unroute('**/api/messages')
                    c=fixture.call('state')['conversations'][0]
                    page.get_by_role('button',name='保存功能需求',exact=True).click()
                    page.get_by_label('功能目标与验收要求',exact=True).fill('Preserve this detailed feature specification')
                    # The assistant's reply arrives while the conversion form is open.
                    task=fixture.call(f"tasks/{c['last_task_id']}/claim",{},role='worker')
                    fixture.call('model/run',dict(task_id=task['id'],attempt_id=task['attempt_id'],source_ids=[]),role='worker')
                    page.locator('#editor-save').click()
                    expect(page.locator('#editor')).to_be_visible()
                    expect(page.get_by_label('功能目标与验收要求',exact=True)).to_have_value('Preserve this detailed feature specification')
                    page.get_by_role('button',name='查看最新对话并更新任务依据',exact=True).click()
                    expect(page.get_by_role('heading',name='确认本次任务将附带的最新对话',exact=True)).to_be_visible()
                    page.locator('#editor-save').click()
                    expect(page.locator('#editor')).not_to_be_visible()
                    feature=next(t for t in fixture.call('state')['tasks'] if t.get('kind')=='feature')
                    self.assertEqual('Preserve this detailed feature specification',feature['title'])
                    self.assertEqual(2,len(feature['conversation_context']))
                finally:browser.close()
        finally:fixture.tearDown()

    def test_onboarding_continuous_chat_and_feature_request(self):
        fixture=test_core.CoreContracts('test_offline_replay_and_manual_conflict_resolution')
        fixture.setUp()
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                try:
                    page=browser.new_page(viewport={'width':1100,'height':850})
                    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(f'http://127.0.0.1:{fixture.port}/')
                    expect(page.get_by_role('heading',name='从这里开始',exact=True)).to_be_visible()
                    page.locator('#token').fill((fixture.data/'owner.token').read_text())
                    page.locator('#connect').click()
                    page.get_by_role('button',name='聊天',exact=True).click()
                    page.get_by_label('消息',exact=True).fill('Plan fixture project Aurora')
                    page.get_by_role('button',name='发送',exact=True).click()
                    expect(page.locator('.chat-log .user')).to_contain_text('Aurora')
                    first=fixture.call('state')['conversations'][0]
                    def answer(task_id):
                        task=fixture.call(f'tasks/{task_id}/claim',{},role='worker')
                        fixture.call('model/run',dict(task_id=task_id,attempt_id=task['attempt_id'],source_ids=[]),role='worker')
                    answer(first['last_task_id'])
                    page.get_by_role('button',name='同步',exact=True).click()
                    expect(page.locator('.chat-log .assistant')).to_contain_text('Aurora')
                    page.get_by_label('消息',exact=True).fill('Discuss offline export next')
                    page.get_by_role('button',name='发送',exact=True).click()
                    expect(page.locator('.chat-log .user')).to_have_count(2)
                    answer(fixture.call('state')['conversations'][0]['last_task_id'])
                    page.reload()
                    page.get_by_role('button',name='聊天',exact=True).click()
                    expect(page.locator('.chat-log .assistant')).to_have_count(2)
                    page.get_by_role('button',name='保存功能需求',exact=True).click()
                    page.get_by_label('功能目标与验收要求',exact=True).fill('Export fixture conversations offline')
                    page.locator('#editor-save').click()
                    expect(page.get_by_text('待开发需求 · 不会自动修改程序',exact=True)).to_be_visible()
                    feature=next(t for t in fixture.call('state')['tasks'] if t.get('kind')=='feature')
                    self.assertEqual(4,len(feature['conversation_context']))
                    self.assertEqual('waiting_input',feature['state'])
                    page.get_by_role('button',name='聊天',exact=True).click()
                    page.get_by_label('消息',exact=True).fill('Keep this unsent thought')
                    page.get_by_role('button',name='同步',exact=True).click()
                    expect(page.get_by_label('消息',exact=True)).to_have_value('Keep this unsent thought')
                    output=Path(__file__).resolve().parents[1]/'build/evidence';output.mkdir(parents=True,exist_ok=True)
                    for name,width in [('desktop',1100),('mobile',393)]:
                        page.set_viewport_size({'width':width,'height':852})
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                        page.screenshot(path=str(output/f'conversation-{name}.png'),full_page=True)
                    self.assertEqual([],errors)
                finally:browser.close()
        finally:fixture.tearDown()

    def test_short_sidebar_keeps_footer_clear_and_last_navigation_reachable(self):
        fixture=test_core.CoreContracts('test_offline_replay_and_manual_conflict_resolution')
        fixture.setUp()
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                try:
                    page=browser.new_page()
                    page.goto(f'http://127.0.0.1:{fixture.port}/')
                    page.locator('#token').fill((fixture.data/'owner.token').read_text())
                    page.locator('#connect').click()
                    output=Path(__file__).resolve().parents[1]/'build/evidence'
                    output.mkdir(parents=True,exist_ok=True)
                    # Short desktop matches the effective CSS space in the zoomed screenshot.
                    for width,height in [(1280,650),(1024,480),(1100,850)]:
                        page.set_viewport_size({'width':width,'height':height})
                        rects=page.evaluate('''() => {
                            const nav=document.querySelector('nav').getBoundingClientRect();
                            const footer=document.querySelector('.nav-footer').getBoundingClientRect();
                            return {navBottom:nav.bottom,footerTop:footer.top,footerBottom:footer.bottom};
                        }''')
                        self.assertLessEqual(rects['navBottom'],rects['footerTop'])
                        self.assertLessEqual(rects['footerBottom'],height)
                        settings=page.get_by_role('button',name='连接与授权',exact=True)
                        settings.click()
                        expect(page.get_by_role('heading',name='连接与授权',exact=True)).to_be_visible()
                        self.assertTrue(settings.evaluate('''button => {
                            const r=button.getBoundingClientRect();
                            return button.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
                        }'''))
                        page.screenshot(path=str(output/f'sidebar-{width}x{height}.png'),full_page=True)
                    page.set_viewport_size({'width':393,'height':852})
                    page.get_by_role('button',name='打开导航',exact=True).click()
                    page.get_by_role('button',name='工作台',exact=True).click()
                    expect(page.get_by_role('heading',name='工作台',exact=True)).to_be_visible()
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    page.screenshot(path=str(output/'sidebar-mobile.png'),full_page=True)
                finally:
                    browser.close()
        finally:
            fixture.tearDown()

    def test_user_creates_and_pauses_a_recurring_check_in(self):
        fixture=test_core.CoreContracts('test_recurring_check_in_survives_restart_without_duplicate_backlog')
        fixture.setUp()
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                try:
                    page=browser.new_page(viewport={'width':1100,'height':850})
                    page.goto(f'http://127.0.0.1:{fixture.port}/')
                    page.locator('#token').fill((fixture.data/'owner.token').read_text())
                    page.locator('#connect').click()
                    page.get_by_role('button',name='定期跟进',exact=True).click()
                    page.get_by_role('button',name='＋ 定期跟进',exact=True).click()
                    page.get_by_label('调查任务或追问内容',exact=True).fill('Fixture: ask about project progress')
                    page.get_by_label('类型',exact=True).select_option('check_in')
                    page.get_by_label('间隔（小时，最少 0.25）',exact=True).fill('168')
                    page.locator('#editor-save').click()
                    expect(page.get_by_role('button',name='暂停',exact=True)).to_be_visible()
                    schedule=fixture.call('state')['schedules'][0]
                    self.assertEqual('check_in',schedule['kind'])
                    self.assertEqual(7*86400,schedule['interval_seconds'])
                    page.get_by_role('button',name='暂停',exact=True).click()
                    expect(page.get_by_role('button',name='启用',exact=True)).to_be_visible()
                    self.assertFalse(fixture.call('state')['schedules'][0]['active'])
                    output=Path(__file__).resolve().parents[1]/'build/evidence'
                    output.mkdir(parents=True,exist_ok=True)
                    for name,width,height in [('desktop',1100,850),('mobile',393,852)]:
                        page.set_viewport_size({'width':width,'height':height})
                        expect(page.get_by_role('button',name='启用',exact=True)).to_be_visible()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                        page.screenshot(path=str(output/f'schedules-{name}.png'),full_page=True)
                finally:
                    browser.close()
        finally:
            fixture.tearDown()

    def test_user_creates_edits_and_resolves_draft_conflict(self):
        fixture=test_core.CoreContracts('test_offline_replay_and_manual_conflict_resolution')
        fixture.setUp()
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                try:
                    page=browser.new_page(viewport={'width':1100,'height':850})
                    page.goto(f'http://127.0.0.1:{fixture.port}/')
                    page.locator('#token').fill((fixture.data/'owner.token').read_text())
                    page.locator('#connect').click()
                    page.get_by_role('button',name='草稿',exact=True).click()
                    page.get_by_role('button',name='＋ 新草稿').click()
                    page.get_by_label('标题',exact=True).fill('Fixture project')
                    page.get_by_label('正文',exact=True).fill('Original paragraph')
                    page.locator('#editor-save').click()
                    expect(page.get_by_text('Original paragraph',exact=True)).to_be_visible()
                    draft=fixture.call('state')['drafts'][0]
                    page.get_by_role('button',name='编辑',exact=True).click()
                    page.get_by_label('正文',exact=True).fill('Phone edits remain recoverable')
                    fixture.call('drafts/'+draft['id'], {'title':'Fixture project','body':'Concurrent server edit',
                        'expected_revision':draft['revision']},method='PUT')
                    page.locator('#editor-save').click()
                    expect(page.get_by_role('button',name='选择本地版本',exact=True)).to_be_visible()
                    page.get_by_role('button',name='选择本地版本',exact=True).click()
                    expect(page.get_by_text('Phone edits remain recoverable',exact=True)).to_be_visible()
                    self.assertEqual('resolved',fixture.call('state')['conflicts'][0]['state'])
                    output=Path(__file__).resolve().parents[1]/'build/evidence'
                    output.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(output/'draft-conflict-resolved.png'),full_page=True)
                finally:
                    browser.close()
        finally:
            fixture.tearDown()


if __name__=='__main__':unittest.main(verbosity=2)
