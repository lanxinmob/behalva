"""Migration tests preserve real WAL state, secret protection and rollback boundaries.

Run this module on Linux for import coverage. On Windows, setting
PA_MIGRATION_LINUX_PYTHON enables a real DPAPI -> WSL AES-GCM pipe round trip.
"""
import base64
from contextlib import closing
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('migration', ROOT / 'tools/migrate_workspace.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def verify_core_key(target, core, expected):
    """The actual C++ credential reader must authorize a loopback-only model catalog."""
    seen = []
    class Catalog(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            seen.append(self.headers.get('Authorization') == 'Bearer ' + expected.decode())
            payload = b'{"data":[{"id":"fixture"}]}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Catalog)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = 'http://127.0.0.1:' + str(server.server_port)
    import socket
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    with closing(sqlite3.connect(target / 'assistant.db')) as db, db:
        db.execute('UPDATE model_providers SET metadata=?', (json.dumps({'base_url': url}),))
    proc = subprocess.Popen([core, '--data', str(target), '--port', str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for _ in range(100):
            if proc.poll() is not None:
                raise AssertionError('Fixture C++ service failed to start')
            try:
                opener.open(f'http://127.0.0.1:{port}/health', timeout=.2).close()
                break
            except OSError:
                time.sleep(.05)
        body = json.dumps({'id': 'provider', 'base_url': url, 'protocol': 'responses'}).encode()
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/providers/models', data=body,
                  headers={'Authorization': 'Bearer ' + (target / 'owner.token').read_text(),
                           'Content-Type': 'application/json'})
        with opener.open(request, timeout=10) as response:
            assert response.status == 200
        assert seen == [True], 'C++ must decrypt the migrated credential correctly'
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        server.shutdown()
        server.server_close()
        thread.join()


class Migration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'windows'
        self.source.mkdir()
        self.secret = b'fixture-private-model-key-123'
        self.old_master = os.urandom(32)
        self.nonce = os.urandom(12)
        protected = self.nonce + AESGCM(self.old_master).encrypt(self.nonce, self.secret, None)
        self.decrypt = lambda data: AESGCM(self.old_master).decrypt(data[:12], data[12:], None)
        self.db = sqlite3.connect(self.source / 'assistant.db')
        self.addCleanup(self.db.close)
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA wal_autocheckpoint=0;
            CREATE TABLE model_providers(id TEXT PRIMARY KEY,revision INTEGER,metadata TEXT,credential TEXT);
            CREATE TABLE resources(kind TEXT,id TEXT,rev INTEGER,data TEXT,updated INTEGER,PRIMARY KEY(kind,id));
            CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,id TEXT,rev INTEGER,at INTEGER);
            CREATE TABLE usage(id TEXT,status TEXT,week INTEGER,reserved REAL,actual REAL,at INTEGER);
            CREATE TABLE versions(id TEXT,source_id TEXT,body TEXT,meta TEXT,hash TEXT,at INTEGER);
        ''')
        self.db.execute('INSERT INTO model_providers VALUES(?,?,?,?)', ('provider', 2, '{"model":"fixture"}', protected.hex()))
        fixtures = [('sources', 'original-source', {'kind': 'file', 'uri': 'D:\\notes\\a.md'}),
                    ('grants', 'original-grant', {'scope': 'directory', 'target': 'D:\\notes', 'active': True}),
                    ('messages', 'message', {'body': 'Historical quotation D:\\notes\\a.md'})]
        for kind, rid, value in fixtures:
            self.db.execute('INSERT INTO resources VALUES(?,?,1,?,1)', (kind, rid, json.dumps(value)))
        self.db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?)',
                        ('old-version', 'original-source', 'Original body', '{"uri":"D:/notes/a.md"}', 'hash', 1))
        self.db.commit()
        # WAL remains live to expose incorrectly copying only assistant.db.
        (self.source / 'instance.lock').touch()
        for name in ('owner.token', 'worker.token', 'qq.token', 'server.pem', 'server.key'):
            (self.source / name).write_text('fixture-' + name)
        (self.source / 'local.json').write_text(json.dumps({'scan_roots': ['D:\\notes'],
            'ca_bundle': 'C:\\python\\cacert.pem', 'weekly_budget_usd': 50,
            'gmail_credentials': str(self.source / 'gmail.json')}))
        (self.source / 'index-manifest.json').write_text(json.dumps({'D:\\notes\\a.md': {'source_id': 'original-source'}}))
        (self.source / 'browser-profile').mkdir()
        (self.source / 'browser-profile' / 'Cookies').write_text('Windows encrypted browser session')
        (self.source / 'objects').mkdir()
        (self.source / 'objects' / 'evidence.bin').write_bytes(b'archived screenshot')
        (self.source / 'rules').mkdir()
        (self.source / 'rules' / 'rule.md').write_text('Keep approved rule')

    def packet(self):
        return migration.snapshot(self.source, self.decrypt)

    def fingerprints(self):
        return {p.relative_to(self.source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.source.rglob('*') if p.is_file()}

    def test_consistent_wal_snapshot_keeps_source_bytes_and_does_not_serialize_plaintext_key(self):
        before = self.fingerprints()
        packet = self.packet()
        self.assertEqual(before, self.fingerprints())
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(packet['archive']))) as archive:
            self.assertNotIn('browser-profile/Cookies', archive.namelist())
            self.assertNotIn('assistant.db-wal', archive.namelist())
            content = archive.read('assistant.db')
            self.assertNotIn(self.secret, content)
            copied = self.root / 'snapshot.db'
            copied.write_bytes(content)
            with closing(sqlite3.connect(copied)) as copy:
                self.assertEqual(copy.execute('SELECT count(*) FROM resources').fetchone()[0], 3)
        self.assertEqual(base64.b64decode(packet['keys']['provider']), self.secret)

    def test_inflight_external_operation_rejected_without_source_changes(self):
        self.db.execute('INSERT INTO resources VALUES(?,?,1,?,1)',
                        ('operations', 'send', json.dumps({'state': 'unknown'})))
        self.db.commit()
        before = self.fingerprints()
        with self.assertRaisesRegex(RuntimeError, 'Unresolved activity'):
            self.packet()
        self.assertEqual(before, self.fingerprints())

    def test_source_lock_refuses_second_owner_and_releases_on_failure(self):
        # The C++ core uses this exact OS lock: another owner must be denied.
        with migration.source_lock(self.source):
            with self.assertRaises(RuntimeError):
                with migration.source_lock(self.source):
                    self.fail('Second instance acquired the workspace')
        with migration.source_lock(self.source):
            self.assertTrue(self.packet()['archive'])

    def test_unreadable_credentials_default_to_refusal(self):
        before = self.fingerprints()
        def unavailable(_):
            raise RuntimeError('Credential export failed on this Windows account')
        with self.assertRaisesRegex(RuntimeError, 'Credential export failed'):
            migration.snapshot(self.source, unavailable)
        self.assertEqual(before, self.fingerprints())

    @unittest.skipIf(os.name == 'nt', 'Linux filesystem import contract')
    def test_explicit_unavailable_credential_import_preserves_sealed_bytes_and_requires_recovery(self):
        original = self.db.execute('SELECT credential FROM model_providers').fetchone()[0]
        before = self.fingerprints()
        def unavailable(_):
            raise RuntimeError('Credential export failed on this Windows account')
        packet = migration.snapshot(self.source, unavailable, allow_unavailable_credentials=True)
        self.assertIsNone(packet['keys']['provider'])
        target = self.root / 'recoverable'
        report = migration.receive(packet, target)
        self.assertEqual(report['unavailable_provider_ids'], ['provider'])
        self.assertEqual(json.loads((target / 'migration.json').read_text())['unavailable_provider_ids'], ['provider'])
        with closing(sqlite3.connect(target / 'assistant.db')) as db:
            revision, raw, sealed = db.execute('SELECT revision,metadata,credential FROM model_providers').fetchone()
            metadata = json.loads(raw)
            self.assertEqual(sealed, original)
            self.assertEqual(revision, 3)
            self.assertEqual(metadata['revision'], revision)
            self.assertFalse(metadata['has_key'])
            self.assertTrue(metadata['credential_unavailable'])
        self.assertEqual(before, self.fingerprints())

    @unittest.skipIf(os.name == 'nt', 'Linux filesystem import contract')
    def test_import_reencrypts_preserves_identity_history_and_runtime_paths(self):
        target = self.root / 'linux'
        before = self.fingerprints()
        report = migration.receive(self.packet(), target)
        self.assertEqual(before, self.fingerprints())
        self.assertIn('Retained on Windows', report['browser_session'])
        self.assertFalse((target / 'browser-profile').exists())
        self.assertEqual((target / 'server.key').read_bytes(), (self.source / 'server.key').read_bytes())
        self.assertEqual((target / 'owner.token').read_bytes(), (self.source / 'owner.token').read_bytes())
        self.assertEqual((target / 'objects/evidence.bin').read_bytes(), b'archived screenshot')
        self.assertEqual((target / 'rules/rule.md').read_text(), 'Keep approved rule')
        master = (target / 'provider-master.key').read_bytes()
        self.assertEqual(len(master), 32)
        self.assertEqual((target / 'provider-master.key').stat().st_mode & 0o777, 0o600)
        with closing(sqlite3.connect(target / 'assistant.db')) as db:
            sealed = bytes.fromhex(db.execute('SELECT credential FROM model_providers').fetchone()[0])
            restored = AESGCM(master).decrypt(sealed[:12], sealed[28:] + sealed[12:28], None)
            self.assertEqual(restored, self.secret)
            source = json.loads(db.execute("SELECT data FROM resources WHERE kind='sources'").fetchone()[0])
            self.assertEqual(source['uri'], '/mnt/d/notes/a.md')
            grant = json.loads(db.execute("SELECT data FROM resources WHERE kind='grants'").fetchone()[0])
            self.assertEqual(grant['target'], '/mnt/d/notes')
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT source_id,body,meta FROM versions').fetchone(),
                             ('original-source', 'Original body', '{"uri":"D:/notes/a.md"}'))
            self.assertIn('Historical quotation', db.execute("SELECT data FROM resources WHERE kind='messages'").fetchone()[0])
        config = json.loads((target / 'local.json').read_text())
        self.assertEqual(config['scan_roots'], ['/mnt/d/notes'])
        self.assertEqual(config['gmail_credentials'], str(target / 'gmail.json'))
        self.assertTrue(Path(config['ca_bundle']).is_file())
        self.assertIn('/mnt/d/notes/a.md', json.loads((target / 'index-manifest.json').read_text()))
        for file in target.rglob('*'):
            if file.is_file():
                self.assertNotIn(self.secret, file.read_bytes())
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            migration.receive(self.packet(), target)
        if os.environ.get('PA_MIGRATION_LINUX_CORE'):
            verify_core_key(target, os.environ['PA_MIGRATION_LINUX_CORE'], self.secret)

    @unittest.skipIf(os.name == 'nt', 'Linux filesystem import contract')
    def test_failed_import_does_not_publish_partial_workspace_or_change_source(self):
        target = self.root / 'failed'
        before = self.fingerprints()
        with patch.object(migration, 'rewrite_database', side_effect=RuntimeError('Injected failure')):
            with self.assertRaisesRegex(RuntimeError, 'Injected failure'):
                migration.receive(self.packet(), target)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob('.failed*')))
        self.assertEqual(before, self.fingerprints())

    @unittest.skipUnless(os.name == 'nt' and os.environ.get('PA_MIGRATION_LINUX_PYTHON'),
                         'Set PA_MIGRATION_LINUX_PYTHON for real Windows -> WSL integration')
    def test_dpapi_to_wsl_pipe_round_trip(self):
        sealed = migration.dpapi(self.secret, encrypt=True)
        self.db.execute('UPDATE model_providers SET credential=?', (sealed.hex(),))
        self.db.commit()
        packet = migration.snapshot(self.source)
        distro = os.environ.get('PA_MIGRATION_DISTRO', 'Ubuntu-22.04')
        python = os.environ['PA_MIGRATION_LINUX_PYTHON']
        prefix = ['wsl.exe', '-d', distro, '--exec', python]
        target = '/tmp/pa-migration-test-' + uuid.uuid4().hex
        command = prefix + [migration.windows_path(str(ROOT / 'tools/migrate_workspace.py')),
                            '--receive', '--target', target]
        result = subprocess.run(command, input=json.dumps(packet).encode(), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertNotIn(self.secret, result.stdout + result.stderr)
        # Verify ciphertext with the independent AES library on Linux and remove only our unique fixture.
        check = '''import pathlib,sqlite3,sys,shutil,importlib.util
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
p=pathlib.Path(sys.argv[1])
assert p.parent==pathlib.Path('/tmp') and p.name.startswith('pa-migration-test-')
try:
 with sqlite3.connect(p/'assistant.db') as db:
  s=bytes.fromhex(db.execute('SELECT credential FROM model_providers').fetchone()[0])
 key=(p/'provider-master.key').read_bytes()
 expected=sys.stdin.buffer.read()
 assert AESGCM(key).decrypt(s[:12],s[28:]+s[12:28],None)==expected
 assert (p/'provider-master.key').stat().st_mode&0o777==0o600
 if len(sys.argv)>3:
  spec=importlib.util.spec_from_file_location('fixture',sys.argv[2])
  module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  module.verify_core_key(p,sys.argv[3],expected)
finally: shutil.rmtree(p)
'''
        command = prefix + ['-c', check, target]
        if os.environ.get('PA_MIGRATION_LINUX_CORE'):
            command += [migration.windows_path(str(Path(__file__).resolve())), os.environ['PA_MIGRATION_LINUX_CORE']]
        verified = subprocess.run(command, input=self.secret, capture_output=True)
        self.assertEqual(verified.returncode, 0, verified.stderr.decode(errors='replace'))


if __name__ == '__main__':
    unittest.main()
