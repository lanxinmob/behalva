"""Offline Windows -> WSL workspace transfer. Secrets travel only on an anonymous pipe.

Run with the Windows workspace Python; the receiver uses the Linux release Python.
Neither command starts services. Keep the source stopped until activation or rollback.
"""
import argparse
import base64
from contextlib import contextmanager, closing
import ctypes
import errno
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_PACKAGE = 512 * 1024 * 1024
OMIT = {'assistant.db', 'assistant.db-wal', 'assistant.db-shm', 'assistant.db-journal',
        'instance.lock', 'provider-master.key', 'worker.generated.json'}


def windows_path(value):
    if isinstance(value, str) and re.match(r'^[A-Za-z]:[\\/]', value):
        return '/mnt/' + value[0].lower() + '/' + value[3:].replace('\\', '/')
    return value


def dpapi(value, *, encrypt=False):
    """Same per-user DPAPI protection as src/providers.hpp; no secret subprocess args."""
    if os.name != 'nt':
        raise RuntimeError('DPAPI export must run on the original Windows account')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(value)
    incoming = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    api = ctypes.windll.crypt32
    if encrypt:
        ok = api.CryptProtectData(ctypes.byref(incoming), 'Personal assistant model key',
                                 None, None, None, 1, ctypes.byref(outgoing))
    else:
        ok = api.CryptUnprotectData(ctypes.byref(incoming), None, None, None, None, 1,
                                   ctypes.byref(outgoing))
    if not ok:
        raise RuntimeError('Credential export failed on this Windows account')
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
        ctypes.windll.kernel32.LocalFree(outgoing.pbData)


@contextmanager
def source_lock(source):
    """Hold the existing core lock without creating or changing a source file."""
    path = source / 'instance.lock'
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise RuntimeError('Source lock unavailable; stop the source core first')
        try:
            yield
        finally:
            kernel.CloseHandle(handle)
    else:
        import fcntl
        with path.open('rb') as file:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Source is still running') from None
            yield


def require_stopped(source):
    """Also reject workers/supervisors that could restart the locked core."""
    if os.name != 'nt':
        raise RuntimeError('Export requires Windows; receiver requires Linux')
    script = r'''$ErrorActionPreference='Stop'
$p=$env:PA_MIGRATION_SOURCE.Replace('/','\').TrimEnd('\')
$active=@(Get-CimInstance Win32_Process | Where-Object {
 $_.ProcessId -ne [int]$env:PA_MIGRATION_PID -and $_.CommandLine -and
 $_.CommandLine.Replace('/','\').IndexOf($p,[StringComparison]::OrdinalIgnoreCase) -ge 0 -and
 ($_.Name -match '^assistant.*\.exe$' -or
  ($_.Name -match '^python(?:w)?\.exe$' -and
   $_.CommandLine -match '(?:[\\/]|[" ])(?:workspace|worker|browser_worker)\.py(?:[" ]|$)'))
})
if($active.Count){exit 9}
'''
    env = dict(os.environ, PA_MIGRATION_SOURCE=str(source), PA_MIGRATION_PID=str(os.getpid()))
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            env=env, capture_output=True)
    if result.returncode:
        raise RuntimeError('Stop all source supervisors and workers before migrating')


def check_idle(db):
    for kind, raw in db.execute('SELECT kind,data FROM resources'):
        state = json.loads(raw).get('state')
        if (kind == 'tasks' and state == 'running' or
                kind == 'operations' and state in ('executing', 'unknown') or
                kind == 'model_runs' and state in ('pending', 'unknown')):
            raise RuntimeError('Unresolved activity exists; verify task or external outcome first')
    if db.execute("SELECT count(*) FROM usage WHERE status IN ('pending','unknown')").fetchone()[0]:
        raise RuntimeError('Unresolved model usage exists; verify it before migrating')


def snapshot(source, decrypt=dpapi, *, allow_unavailable_credentials=False):
    """Caller holds source_lock. Copy WAL before SQLite backup, never open source SQLite."""
    for name in ('assistant.db', 'assistant.db-wal', 'assistant.db-journal'):
        if (source / name).is_symlink():
            raise RuntimeError('Workspace database must not be a link')
    with tempfile.TemporaryDirectory(prefix='pa-migration-db-') as temp:
        copied = Path(temp) / 'assistant.db'
        for suffix in ('', '-wal', '-journal'):
            file = source / ('assistant.db' + suffix)
            if file.exists():
                shutil.copyfile(file, Path(str(copied) + suffix))
        consistent = Path(temp) / 'consistent.db'
        with closing(sqlite3.connect(copied)) as old, closing(sqlite3.connect(consistent)) as db:
            old.backup(db)
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Source database integrity check failed')
            check_idle(db)
            keys = {}
            for pid, sealed in db.execute('SELECT id,credential FROM model_providers'):
                try:
                    keys[pid] = base64.b64encode(decrypt(bytes.fromhex(sealed))).decode('ascii')
                except RuntimeError:
                    if not allow_unavailable_credentials:
                        raise
                    keys[pid] = None
        database = consistent.read_bytes()
    archive = io.BytesIO()
    total = len(database)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as package:
        package.writestr('assistant.db', database)
        for parent, dirs, files in os.walk(source, followlinks=False):
            # Browser cookies are Windows-encrypted. The Windows adapter keeps this session.
            for name in dirs + files:
                path = Path(parent) / name
                if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                    raise RuntimeError('Workspace contains links; inspect before migrating')
            if Path(parent) == source:
                dirs[:] = [name for name in dirs if name != 'browser-profile']
            for name in files:
                path = Path(parent) / name
                relative = path.relative_to(source).as_posix()
                if relative in OMIT:
                    continue
                total += path.stat().st_size
                if total > MAX_PACKAGE:
                    raise RuntimeError('Workspace exceeds the in-memory migration limit (512 MiB)')
                package.write(path, relative)
    return {'source': str(source), 'keys': keys,
            'archive': base64.b64encode(archive.getvalue()).decode('ascii')}


def relocate(value, source, target):
    if not isinstance(value, str):
        return value
    normalized = value.replace('\\', '/')
    origin = source.replace('\\', '/').rstrip('/')
    if normalized.lower() == origin.lower():
        return str(target)
    if normalized.lower().startswith(origin.lower() + '/'):
        return str(target) + normalized[len(origin):]
    return windows_path(value)


def rewrite_database(db, master, packet, target):
    rows = list(db.execute('SELECT id FROM model_providers'))
    if set(packet['keys']) != {row[0] for row in rows}:
        raise RuntimeError('Credential transfer does not match the database')
    for (pid,) in rows:
        if packet['keys'][pid] is None:
            revision, raw = db.execute('SELECT revision,metadata FROM model_providers WHERE id=?', (pid,)).fetchone()
            metadata = json.loads(raw)
            metadata.update(has_key=False, credential_unavailable=True, revision=revision + 1)
            # Retain the original Windows-sealed bytes for recovery, never expose them to clients.
            db.execute('UPDATE model_providers SET revision=?,metadata=? WHERE id=?',
                       (revision + 1, json.dumps(metadata, ensure_ascii=False), pid))
            continue
        plain = base64.b64decode(packet['keys'][pid], validate=True)
        iv = os.urandom(12)
        sealed = AESGCM(master).encrypt(iv, plain, None)
        # C++ format is IV || tag || ciphertext; cryptography emits ciphertext || tag.
        credential = (iv + sealed[-16:] + sealed[:-16]).hex()
        db.execute('UPDATE model_providers SET credential=? WHERE id=?', (credential, pid))
    for kind, rid, revision, raw in list(db.execute('SELECT kind,id,rev,data FROM resources')):
        obj = json.loads(raw)
        field = ('uri' if kind == 'sources' and obj.get('kind') == 'file' else
                 'target' if kind == 'grants' and obj.get('scope') == 'directory' else None)
        if not field:
            continue
        old = obj[field]
        obj[field] = relocate(old, packet['source'], target)
        if old == obj[field]:
            continue
        now = int(time.time())
        obj['revision'], obj['updated_at'] = revision + 1, now
        db.execute('UPDATE resources SET rev=?,data=?,updated=? WHERE kind=? AND id=?',
                   (revision + 1, json.dumps(obj, ensure_ascii=False), now, kind, rid))
        db.execute('INSERT INTO events(kind,id,rev,at) VALUES(?,?,?,?)', (kind, rid, revision + 1, now))


def receive(packet, target):
    """Only publish a completely validated workspace, retaining no plaintext model key."""
    if os.name == 'nt':
        raise RuntimeError('The target must be on a Linux filesystem')
    target = Path(target)
    if not target.is_absolute() or str(target.resolve()).startswith('/mnt/'):
        raise RuntimeError('Choose an absolute Linux-local target, outside /mnt')
    if target.exists() or target.is_symlink():
        raise RuntimeError('Target already exists; nothing was overwritten')
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Serialize concurrent migrations of the same target, also blocking empty-dir replacement.
    reservation = target.parent / ('.' + target.name + '.migration-lock')
    lockfd = os.open(reservation, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    staging = Path(tempfile.mkdtemp(prefix='.' + target.name + '-migrating-', dir=target.parent))
    try:
        data = base64.b64decode(packet['archive'], validate=True)
        if len(data) > MAX_PACKAGE:
            raise RuntimeError('Migration package exceeds size limit')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            seen, total = set(), 0
            for entry in archive.infolist():
                path = PurePosixPath(entry.filename)
                total += entry.file_size
                if (path.is_absolute() or '..' in path.parts or '\\' in entry.filename or
                        entry.filename in seen or entry.is_dir() or total > MAX_PACKAGE):
                    raise RuntimeError('Invalid migration archive')
                seen.add(entry.filename)
                output = staging.joinpath(*path.parts)
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with output.open('xb') as file:
                    os.chmod(output, 0o600)
                    file.write(archive.read(entry))
        for required in ('assistant.db', 'local.json', 'owner.token', 'worker.token', 'server.pem', 'server.key'):
            if not (staging / required).is_file():
                raise RuntimeError('Migration package is missing a required workspace file')
        master = os.urandom(32)
        fd = os.open(staging / 'provider-master.key', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as file:
            file.write(master)
        with closing(sqlite3.connect(staging / 'assistant.db')) as db, db:
            check_idle(db)
            rewrite_database(db, master, packet, target)
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Migrated database integrity check failed')
        config_file = staging / 'local.json'
        config = json.loads(config_file.read_text('utf-8-sig'))
        import certifi
        config['ca_bundle'] = certifi.where()
        if isinstance(config.get('provider'), dict):
            config['provider'].pop('ca_bundle', None)
        config['scan_roots'] = [relocate(p, packet['source'], target) for p in config.get('scan_roots', [])]
        for field in ('gmail_credentials', 'server_ca'):
            if field in config:
                config[field] = relocate(config[field], packet['source'], target)
        config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2), 'utf-8')
        manifest = staging / 'index-manifest.json'
        if manifest.exists():
            old = json.loads(manifest.read_text('utf-8'))
            manifest.write_text(json.dumps({relocate(k, packet['source'], target): v for k, v in old.items()},
                                           ensure_ascii=False), 'utf-8')
        report = {'source': packet['source'], 'target': str(target), 'provider_count': len(packet['keys']),
                  'unavailable_provider_ids': [pid for pid, key in packet['keys'].items() if key is None],
                  'browser_session': 'Retained on Windows; use Windows browser adapter, not Linux Chrome',
                  'activation': 'Not started; source must remain stopped when target is enabled'}
        (staging / 'migration.json').write_text(json.dumps(report, indent=2), 'utf-8')
        # Flush every file before publishing; a power loss must not publish an incomplete copy.
        for path in staging.rglob('*'):
            if path.is_file():
                path.chmod(0o600)
                with path.open('rb') as file:
                    os.fsync(file.fileno())
        directories = [staging] + [path for path in staging.rglob('*') if path.is_dir()]
        for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
            directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        if target.exists() or target.is_symlink():
            raise RuntimeError('Target appeared during migration; nothing was overwritten')
        # renameat2 prevents replacing even an empty directory created by another process.
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        if rename(-100, os.fsencode(staging), -100, os.fsencode(target), 1):
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise RuntimeError('Target appeared during migration; nothing was overwritten')
            raise OSError(error, 'Cannot publish migrated workspace')
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return report
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        os.close(lockfd)
        reservation.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--distro', default='Ubuntu-22.04')
    parser.add_argument('--target', required=True)
    parser.add_argument('--linux-python', default='python3')
    parser.add_argument('--allow-unavailable-credentials', action='store_true',
                        help='Preserve unreadable Windows credentials as unavailable; re-enter keys after migration')
    parser.add_argument('--receive', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.receive:
            packet = json.loads(sys.stdin.buffer.read(MAX_PACKAGE * 2 + 1))
            report = receive(packet, args.target)
            print(json.dumps(report))
        else:
            if not args.source:
                raise RuntimeError('--source is required')
            source = args.source.resolve(strict=True)
            require_stopped(source)
            with source_lock(source):
                packet = snapshot(source, allow_unavailable_credentials=args.allow_unavailable_credentials)
                require_stopped(source)
                command = ['wsl.exe', '--distribution', args.distro, '--exec', args.linux_python,
                           windows_path(str(Path(__file__).resolve())), '--receive', '--target', args.target]
                result = subprocess.run(command, input=json.dumps(packet).encode('utf-8'), capture_output=True)
                if result.returncode:
                    raise RuntimeError('Linux import failed; inspect target and rerun after fixing prerequisites')
                report = json.loads(result.stdout)
                print(json.dumps(report))
    except Exception as error:
        # Never echo a traceback, packet, provider response, config or secret-containing exception.
        print('Migration failed: ' + (str(error) if type(error) is RuntimeError else type(error).__name__),
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
