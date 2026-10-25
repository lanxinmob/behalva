"""Validate migrated identities and the real Linux background service lifecycle."""
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import uuid

import test_core
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import wsl_service


class WslRuntime(unittest.TestCase):
    def test_moved_file_keeps_history_and_directory_revocation(self):
        fixture = test_core.CoreContracts(); fixture.setUp()
        try:
            source = fixture.document('Historical evidence', fixture.data/'before'/'note.txt')
            grant = fixture.call('grants', {'scope': 'directory', 'target': str(fixture.data/'before')})
            fixture.stop()
            # Model the migration's stable IDs and rewritten current locations.
            with test_core.database(fixture.data/'assistant.db') as db:
                for kind, item, key, value in [
                    ('sources', source, 'uri', (fixture.data/'after'/'note.txt').as_posix()),
                    ('grants', grant, 'target', (fixture.data/'after').as_posix())]:
                    item[key] = value
                    db.execute('UPDATE resources SET data=? WHERE kind=? AND id=?', (json.dumps(item), kind, item['id']))
            fixture.start()
            newer = fixture.document('Updated evidence', fixture.data/'after'/'note.txt')
            self.assertEqual(source['id'], newer['id'])
            self.assertEqual('Historical evidence', fixture.call('versions/'+source['version_id'])['body'])
            self.assertEqual(1, len(fixture.call('context', {'source_ids': [source['id']]})))
            revoked = fixture.call('grants', {'scope': 'directory', 'target': str(fixture.data/'after'), 'active': False})
            self.assertEqual(grant['id'], revoked['id'])
            fixture.call('context', {'source_ids': [source['id']]}, status=403)
        finally:
            fixture.tearDown()

    @unittest.skipUnless(os.environ.get('PA_TEST_SYSTEMD') == '1', 'requires an explicit Linux systemd test environment')
    def test_service_persists_draft_and_does_not_restart_failed_core(self):
        root = Path(__file__).resolve().parents[1]
        name = 'pa-test-' + uuid.uuid4().hex
        unit = Path.home()/'.config/systemd/user'/f'{name}.service'
        with tempfile.TemporaryDirectory(prefix='pa-wsl-') as temporary:
            base = Path(temporary); release = base/'release'; data = base/'workspace'
            release.mkdir(); data.mkdir(); (release/'build').mkdir(); (release/'tools').mkdir()
            shutil.copyfile(root/'tools/workspace.py', release/'tools/workspace.py')
            shutil.copytree(root/'connectors', release/'connectors')
            shutil.copytree(root/'web', release/'web')
            (release/'build/assistant').symlink_to(Path(os.environ['PA_TEST_EXECUTABLE']).resolve())
            (release/'.venv').symlink_to(Path(sys.prefix))
            (data/'local.json').write_text('{"scan_roots": []}')
            port = test_core.free_port()
            keeper = None
            def ctl(*args):
                return subprocess.run(['systemctl', '--user', *args], check=True, capture_output=True, text=True).stdout.strip()
            def call(path, body=None):
                ctx = ssl.create_default_context(cafile=str(data/'server.pem'))
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))
                req = urllib.request.Request(f'https://127.0.0.1:{port}/'+path,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={'Authorization': 'Bearer '+(data/'owner.token').read_text(), 'Content-Type': 'application/json'})
                return json.loads(opener.open(req, timeout=2).read())
            def ready():
                deadline = time.monotonic()+15
                while time.monotonic()<deadline:
                    try: return call('health')
                    except (OSError, ValueError): time.sleep(.1)
                self.fail('Linux service did not become healthy')
            try:
                wsl_service.install(release, data, port, name)
                owner = uuid.uuid4().hex
                keeper = subprocess.Popen([sys.executable, str(root/'tools/wsl_service.py'), 'hold', '--name', name,
                                           '--owner', owner], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ready()
                wsl_service.stop_owned(name, uuid.uuid4().hex)
                self.assertEqual('active', ctl('show', name, '-p', 'ActiveState', '--value'))
                duplicate = subprocess.run([sys.executable, str(root/'tools/wsl_service.py'), 'hold', '--name', name,
                                            '--owner', uuid.uuid4().hex], capture_output=True, timeout=5)
                self.assertNotEqual(0, duplicate.returncode)
                draft = call('api/drafts', {'title':'Restart fixture', 'body':'Preserve this draft', 'request_id':'create-draft'})
                wsl_service.stop_owned(name, owner)
                keeper.wait(timeout=10)
                ctl('start', name); ready()
                self.assertTrue(any(x['id']==draft['id'] and x['body']=='Preserve this draft' for x in call('api/state')['drafts']))
                supervisor = int(ctl('show', name, '-p', 'MainPID', '--value'))
                children = Path(f'/proc/{supervisor}/task/{supervisor}/children').read_text().split()
                core = next(int(pid) for pid in children if Path(f'/proc/{pid}/comm').read_text().strip()=='assistant')
                os.kill(core, 9)
                deadline = time.monotonic()+10
                while time.monotonic()<deadline and ctl('show', name, '-p', 'ActiveState', '--value')=='active':
                    time.sleep(.1)
                self.assertEqual('failed', ctl('show', name, '-p', 'ActiveState', '--value'))
                self.assertEqual('0', ctl('show', name, '-p', 'NRestarts', '--value'))
                ctl('reset-failed', name); ctl('start', name); ready()
                self.assertEqual(1, len(call('api/state')['drafts']))
            finally:
                subprocess.run(['systemctl','--user','stop',name], capture_output=True)
                if keeper is not None:
                    keeper.wait(timeout=10)
                unit.unlink(missing_ok=True)
                subprocess.run(['systemctl','--user','daemon-reload'], capture_output=True)
