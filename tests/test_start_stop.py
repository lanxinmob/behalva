"""Stopping one explicitly named workspace must not kill another local process."""
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name=='nt','Windows process supervisor contract')
class SupervisorControl(unittest.TestCase):
    def test_stop_targets_only_the_matching_workspace_tree(self):
        with tempfile.TemporaryDirectory(prefix='assistant-stop-contract-') as directory:
            data=Path(directory)/'owned';data.mkdir()
            other=Path(directory)/'other';other.mkdir()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            unrelated=subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'),'-c','import time; time.sleep(90)'])
            pid=None
            def powershell(script,*args):
                return subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'tools'/script),*args],cwd=ROOT,capture_output=True,text=True,timeout=30)
            try:
                started=powershell('start.ps1','-Data',str(data),'-Port',str(port))
                self.assertEqual(0,started.returncode,started.stderr)
                pid=int(re.search(r'Supervisor process (\d+)',started.stdout)[1])
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    try:
                        context=ssl.create_default_context(cafile=str(data/'server.pem'))
                        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
                        opener.open(f'https://127.0.0.1:{port}/health',timeout=.5).close();break
                    except (OSError,FileNotFoundError):time.sleep(.1)
                else:self.fail('Owned workspace did not become healthy')
                absent=powershell('stop.ps1','-Data',str(other))
                self.assertNotEqual(0,absent.returncode)
                self.assertIn('No process was stopped',absent.stderr)
                self.assertIsNone(unrelated.poll())
                opener.open(f'https://127.0.0.1:{port}/health',timeout=1).close()
                stopped=powershell('stop.ps1','-Data',str(data))
                self.assertEqual(0,stopped.returncode,stopped.stderr)
                pid=None
                self.assertIsNone(unrelated.poll())
                with socket.socket() as sock:
                    self.assertNotEqual(0,sock.connect_ex(('127.0.0.1',port)))
            finally:
                if pid is not None:subprocess.run(['taskkill.exe','/PID',str(pid),'/T','/F'],capture_output=True,timeout=15)
                unrelated.terminate();unrelated.wait(timeout=15)
