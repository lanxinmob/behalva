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

    def test_browser_adapter_respawn_ends_orphaned_descendants_only(self):
        # start-wsl.ps1 respawns the browser adapter when its root exits. A
        # crashed root can leave Playwright/Chromium children holding the
        # profile; taskkill /T by the dead root PID cannot reach them.
        python=str(ROOT/'.venv/Scripts/python.exe')
        with tempfile.TemporaryDirectory(prefix='assistant-tree-contract-') as directory:
            child=Path(directory)/'orphaning_root.py';marker=Path(directory)/'descendant.pid'
            child.write_text('import subprocess,sys\n'
                             "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\n"
                             'open(sys.argv[1],"w").write(str(p.pid))\n')
            script=Path(directory)/'tree.ps1'
            script.write_text(r'''param($Python,$Child,$Marker)
$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $env:PA_ROOT 'tools\OwnedProcessTree.cs')
$tree=[PersonalAssistant.OwnedProcessTree]::Start($Python,('"'+$Python+'" "'+$Child+'" "'+$Marker+'"'))
$deadline=[DateTime]::UtcNow.AddSeconds(30)
while(-not ($tree.HasExited -and (Test-Path -LiteralPath $Marker))){if([DateTime]::UtcNow -gt $deadline){throw 'root did not exit'};Start-Sleep -Milliseconds 100}
$descendant=[int](Get-Content -LiteralPath $Marker)
Write-Output ("orphan_alive="+[bool](Get-Process -Id $descendant -ErrorAction SilentlyContinue))
Write-Output ("emptied="+$tree.Stop(10000))
$tree.Dispose()
Write-Output ("descendant_alive="+[bool](Get-Process -Id $descendant -ErrorAction SilentlyContinue))
''')
            unrelated=subprocess.Popen([python,'-c','import time; time.sleep(90)'])
            try:
                run=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),
                                    python,str(child),str(marker)],env={**os.environ,'PA_ROOT':str(ROOT)},
                                   capture_output=True,text=True,timeout=60)
                self.assertEqual(0,run.returncode,run.stderr)
                self.assertIn('orphan_alive=True',run.stdout)
                self.assertIn('emptied=True',run.stdout)
                self.assertIn('descendant_alive=False',run.stdout)
                self.assertIsNone(unrelated.poll())
            finally:
                unrelated.terminate();unrelated.wait(timeout=15)
