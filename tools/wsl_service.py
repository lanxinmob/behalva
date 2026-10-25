"""Install a user systemd unit; keep WSL alive independently of a terminal/chat."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
import signal


def quoted(value):
    value = str(value)
    if any(c in value for c in '\r\n\x00'):
        raise ValueError('Invalid service path')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def install(release, data, port, name):
    release, data = Path(release).resolve(), Path(data).resolve()
    if not all(re.fullmatch(r'/[A-Za-z0-9_./-]+', str(p)) for p in (release, data)):
        raise ValueError('Use simple Linux deployment paths without spaces or shell metacharacters')
    if str(data).startswith('/mnt/'):
        raise ValueError('SQLite workspace must be on the Linux filesystem, not a Windows mount')
    for p in (release/'build/assistant', release/'.venv/bin/python', release/'tools/workspace.py', data/'local.json'):
        if not p.is_file():
            raise ValueError('Missing deployment file: ' + str(p))
    unit = Path.home()/'.config/systemd/user'/f'{name}.service'
    if unit.exists():
        raise ValueError('Unit already exists; stop and archive the previous unit before replacing it')
    unit.parent.mkdir(parents=True, exist_ok=True)
    command = [release/'.venv/bin/python', release/'tools/workspace.py', 'run', '--data', data,
               '--host', '127.0.0.1', '--port', str(port)]
    # No automatic core restart: unknown external results must not be replayed.
    contents = ('[Unit]\nDescription=Personal Assistant WSL2 workspace\n'
                '[Service]\nType=simple\nUMask=0077\n'
                f'WorkingDirectory={release}\nExecStart={" ".join(map(quoted, command))}\n'
                'Restart=no\nKillMode=control-group\nTimeoutStopSec=30\n'
                '[Install]\nWantedBy=default.target\n')
    with unit.open('x') as stream:
        stream.write(contents)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    print('Installed ' + str(unit) + '; not started or enabled')


def hold(name, owner):
    import fcntl
    unit = name + '.service'
    runtime = Path(os.environ['XDG_RUNTIME_DIR'])
    record = runtime/(name+'.keeper.json')
    with (runtime/(name+'.keeper.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = subprocess.run(['systemctl','--user','show',unit,'-p','ActiveState','--value'],
                                check=True, capture_output=True, text=True).stdout.strip()
        if status not in ('inactive', 'failed'):
            raise RuntimeError('Service already active; stop its existing launcher first')
        record.write_text(json.dumps({'owner':owner}))
        record.chmod(0o600)
        def stopped(_signal, _frame):
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, stopped)
        signal.signal(signal.SIGINT, stopped)
        try:
            subprocess.run(['systemctl', '--user', 'start', unit], check=True)
            # A foreground wsl.exe session keeps the VM alive. systemd alone does not.
            while subprocess.run(['systemctl', '--user', 'is-active', '--quiet', unit]).returncode == 0:
                time.sleep(2)
            raise RuntimeError('Workspace stopped; inspect systemd journal and workspace logs')
        finally:
            subprocess.run(['systemctl', '--user', 'stop', unit], check=True)
            record.unlink(missing_ok=True)


def stop_owned(name, owner):
    record = Path(os.environ['XDG_RUNTIME_DIR'])/(name+'.keeper.json')
    if record.exists() and json.loads(record.read_text()).get('owner') == owner:
        subprocess.run(['systemctl', '--user', 'stop', name+'.service'], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['install', 'hold', 'stop-owned'])
    parser.add_argument('--name', default='personal-assistant')
    parser.add_argument('--release', type=Path)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--owner')
    args = parser.parse_args()
    if os.name == 'nt' or not re.fullmatch(r'[a-z][a-z0-9-]{0,50}', args.name):
        parser.error('Run inside Linux with a simple lowercase unit name')
    if not 1 <= args.port <= 65535:
        parser.error('Invalid port')
    if args.action == 'install':
        if args.release is None or args.data is None:
            parser.error('install requires --release and --data')
        install(args.release, args.data, args.port, args.name)
    elif not args.owner or not re.fullmatch(r'[a-f0-9]{32}', args.owner):
        parser.error('A launcher ownership ID is required')
    elif args.action == 'hold':
        hold(args.name, args.owner)
    else:
        stop_owned(args.name, args.owner)
