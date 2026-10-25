"""UI stress tests for reading, composing, reviewing and navigation, not snapshots alone."""
import base64
import json
from pathlib import Path
import unittest
from playwright.sync_api import sync_playwright, expect
import test_core


class DesignFlows(unittest.TestCase):
    def test_read_long_chat_and_review_materials_across_viewports(self):
        fixture=test_core.CoreContracts();fixture.setUp()
        output=Path(__file__).resolve().parents[1]/'build/evidence/ui'
        output.mkdir(parents=True,exist_ok=True)
        try:
            # Use real server state and a fake provider; no private or paid traffic.
            cid='1234567890abcdef1234567890abcdef'
            fixture.call('messages',dict(conversation_id=cid,body=('Fixture project notes. '*25+'\n')*20,
                expected_sequence=0,request_id='ui-long-history'))
            task=fixture.call('state')['tasks'][0]
            claim=fixture.call('tasks/'+task['id']+'/claim',{},role='worker')
            fixture.call('model/run',dict(task_id=task['id'],attempt_id=claim['attempt_id'],source_ids=[]),role='worker')
            attachment=fixture.call('attachments',dict(name='fixture-project.txt',base64=base64.b64encode(b'Fixture evidence').decode()))
            fixture.call('operations',dict(kind='gmail',payload=dict(
                **{'from':'fixture@example.test','to':'review@example.test'},subject='Fixture review',
                body=('Full approval paragraph.\n'*60),attachment_ids=[attachment['id']])))
            fixture.call('operations',dict(kind='gmail',payload=dict(
                **{'from':'fixture@example.test','to':'plain@example.test'},subject='No attachment',body='Plain message')))
            fixture.call('tables',dict(title='Fixture comparison',columns=['Column '+str(i) for i in range(9)],
                rows=[['A long reference '+str(i) for i in range(9)]]))
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                try:
                    page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(f'http://127.0.0.1:{fixture.port}/')
                    page.locator('#token').fill((fixture.data/'owner.token').read_text());page.locator('#connect').click()
                    def navigate(label):
                        if page.locator('#menu-toggle').is_visible():page.locator('#menu-toggle').click()
                        page.locator('#nav').get_by_role('button',name=label,exact=True).click()
                        if page.locator('#menu-toggle').is_visible():page.wait_for_function('() => document.querySelector("aside").getBoundingClientRect().right<=0.5')
                    for name,width,height,scale in [('desktop',1440,900,1),('short',1024,480,1),
                            ('mobile',390,844,1),('small',360,740,1),('tablet',768,1024,1),('large-text',390,844,2)]:
                        page.set_viewport_size(dict(width=width,height=height))
                        page.evaluate('(size)=>document.documentElement.style.fontSize=size+"px"',15*scale)
                        navigate('聊天');expect(page.locator('.chat-log .assistant')).to_have_count(1)
                        input=page.get_by_label('消息',exact=True);input.fill('Retain the next thought');input.focus()
                        input.evaluate('(e)=>e.setSelectionRange(3,7)')
                        page.locator('.chat-scroll').evaluate('(e)=>e.scrollTop=100')
                        # A refresh while reading older messages must not jump to the end.
                        page.evaluate('sync()');page.wait_for_timeout(200)
                        self.assertEqual('Retain the next thought',input.input_value())
                        self.assertEqual([3,7],input.evaluate('(e)=>[e.selectionStart,e.selectionEnd]'))
                        self.assertTrue(input.evaluate('(e)=>document.activeElement===e'))
                        self.assertAlmostEqual(100,page.locator('.chat-scroll').evaluate('(e)=>e.scrollTop'),delta=2)
                        r=input.bounding_box();self.assertGreaterEqual(r['y'],0)
                        self.assertLessEqual(r['y']+r['height'],height)
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                        page.screenshot(path=str(output/f'chat-{name}.png'))
                        for label in ['工作台','使用引导','连接与授权','任务','资料','草稿','待确认','长期目标','定期跟进','记忆与规则','表格']:
                            navigate(label)
                            self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'),label+' '+name)
                            if label=='待确认':
                                expect(page.get_by_text('review@example.test',exact=True)).to_be_visible()
                                expect(page.get_by_text('fixture-project.txt',exact=False)).to_be_visible()
                                review=page.locator('.card').filter(has=page.get_by_text('Fixture review',exact=True))
                                self.assertNotIn('无',review.locator('.field').filter(has=page.get_by_text('附件',exact=True)).inner_text())
                                self.assertIn(attachment['sha256'],review.inner_text())
                                plain=page.locator('.card').filter(has=page.get_by_text('No attachment',exact=True))
                                self.assertEqual('无',plain.locator('.field').filter(has=page.get_by_text('附件',exact=True)).locator('dd').inner_text())
                                self.assertEqual('none',page.locator('.field dd').filter(has_text='Full approval paragraph').evaluate('(e)=>getComputedStyle(e).maxHeight'))
                            if label=='连接与授权':
                                for summary in page.locator('summary').all():self.assertGreaterEqual(summary.bounding_box()['height'],44)
                            if label in ['工作台','使用引导','连接与授权','待确认','表格']:page.screenshot(path=str(output/f'{label}-{name}.png'))
                    self.assertEqual([],errors)
                finally:browser.close()
        finally:fixture.tearDown()

    def test_phone_pairing_exports_only_one_device_credential_and_validates_address(self):
        fixture=test_core.CoreContracts();fixture.setUp()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                try:
                    page=browser.new_page(viewport=dict(width=390,height=844))
                    page.goto(f'http://127.0.0.1:{fixture.port}/')
                    page.locator('#token').fill((fixture.data/'owner.token').read_text());page.locator('#connect').click()
                    page.locator('#menu-toggle').click();page.locator('#nav').get_by_role('button',name='连接与授权',exact=True).click()
                    page.get_by_role('button',name='配对新手机',exact=True).click()
                    dialog=page.locator('#phone-pairing');address=dialog.get_by_label('手机可达的 HTTPS 地址',exact=True)
                    address.fill('https://127.0.0.1:8765');dialog.get_by_role('button',name='生成连接配置').click()
                    expect(dialog.get_by_role('alert')).to_contain_text('不能使用')
                    self.assertEqual([],fixture.call('state')['devices'])
                    address.fill('https://fixture.tail000.ts.net');dialog.get_by_role('button',name='生成连接配置').click()
                    expect(dialog.get_by_label('手机连接配置')).to_be_visible()
                    exported=json.loads(dialog.get_by_label('手机连接配置').input_value())
                    self.assertEqual('https://fixture.tail000.ts.net',exported['url']);self.assertEqual('',exported['fingerprint'])
                    self.assertEqual(1,exported['version']);self.assertNotEqual((fixture.data/'owner.token').read_text(),exported['token'])
                    self.assertEqual(1,len(fixture.call('state')['devices']))
                    expect(dialog.get_by_role('button',name='生成连接配置')).not_to_be_visible()
                    # A connection blob grants access; normal synchronized metadata must not expose it.
                    self.assertNotIn(exported['token'],json.dumps(fixture.call('state')))
                    dialog.get_by_role('button',name='关闭',exact=True).click()
                    expect(page.locator('#phone-pairing')).to_have_count(0)
                    page.get_by_role('button',name='配对新手机',exact=True).click()
                    dialog=page.locator('#phone-pairing');dialog.get_by_label('手机可达的 HTTPS 地址',exact=True).fill('https://192.168.50.20:8765')
                    dialog.get_by_label('证书方式',exact=True).select_option('pinned')
                    dialog.get_by_label('证书 SHA-256 指纹（自签名时必填）',exact=True).fill('a'*64)
                    dialog.get_by_role('button',name='生成连接配置').click()
                    expect(dialog.get_by_label('手机连接配置')).to_be_visible()
                    self.assertEqual('a'*64,json.loads(dialog.get_by_label('手机连接配置').input_value())['fingerprint'])
                    page.screenshot(path=str(Path(__file__).resolve().parents[1]/'build/evidence/ui/pair-phone-mobile.png'))
                finally:browser.close()
        finally:fixture.tearDown()

    def test_mobile_navigation_focus_is_visible_and_contained(self):
        fixture=test_core.CoreContracts();fixture.setUp()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                try:
                    page=browser.new_page(viewport=dict(width=390,height=844))
                    page.goto(f'http://127.0.0.1:{fixture.port}/')
                    page.locator('#token').fill((fixture.data/'owner.token').read_text());page.locator('#connect').click()
                    menu=page.locator('#menu-toggle');menu.focus();page.keyboard.press('Tab')
                    self.assertFalse(page.evaluate('document.querySelector("aside").contains(document.activeElement)'))
                    menu.click();expect(menu).to_have_attribute('aria-expanded','true')
                    page.keyboard.press('Shift+Tab')
                    self.assertEqual('使用引导',page.evaluate('document.activeElement.textContent'))
                    page.keyboard.press('Tab')
                    self.assertEqual('工作台',page.evaluate('document.activeElement.textContent'))
                    page.keyboard.press('Escape');expect(menu).to_be_focused()
                    self.assertTrue(page.locator('aside').evaluate('(e)=>e.inert'))
                finally:browser.close()
        finally:fixture.tearDown()

    def test_setup_requires_authorization_and_never_returns_secrets(self):
        fixture=test_core.CoreContracts();fixture.setUp()
        try:
            fixture.call('setup',role='worker',status=403)
            info=fixture.call('setup')
            self.assertEqual(fixture.config.resolve(),Path(info['config_path']))
            self.assertEqual(fixture.data.resolve(),Path(info['data_path']))
            self.assertEqual('127.0.0.1',info['listen_host'])
            self.assertNotIn((fixture.data/'owner.token').read_text(),str(info))
            self.assertNotIn('provider',info)
        finally:fixture.tearDown()


if __name__=='__main__':unittest.main(verbosity=2)
