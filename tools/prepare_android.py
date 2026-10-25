"""Download project-local build tools. No global PATH changes."""
from pathlib import Path
import hashlib
import urllib.request
import zipfile
import subprocess
import os

root = Path(__file__).resolve().parents[1] / '.tools'
root.mkdir(exist_ok=True)
def fetch(url, path, checksum=None):
    if not path.exists():
        urllib.request.urlretrieve(url, path)
    if checksum and hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise RuntimeError('Checksum mismatch: ' + path.name)

sdk = root / 'android-sdk'
sdk.mkdir(exist_ok=True)
archive = root / 'android-commandline.zip'
fetch('https://dl.google.com/android/repository/commandlinetools-win-15859902_latest.zip', archive,
      '90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a')
if not (sdk / 'cmdline-tools/bin/sdkmanager.bat').exists():
    with zipfile.ZipFile(archive) as z:
        z.extractall(sdk)
gradle = root / 'gradle-8.9-bin.zip'
fetch('https://services.gradle.org/distributions/gradle-8.9-bin.zip', gradle)
expected = urllib.request.urlopen('https://services.gradle.org/distributions/gradle-8.9-bin.zip.sha256').read().decode().strip()
if hashlib.sha256(gradle.read_bytes()).hexdigest() != expected:
    raise RuntimeError('Gradle checksum mismatch')
if not (root / 'gradle-8.9').exists():
    with zipfile.ZipFile(gradle) as z:
        z.extractall(root)
env = os.environ.copy()
env['JAVA_HOME'] = str(Path(subprocess.check_output(['where.exe', 'java'], text=True).splitlines()[0]).parents[1])
tool = sdk / 'cmdline-tools/bin/sdkmanager.bat'
subprocess.run([str(tool), f'--sdk_root={sdk}', '--licenses'], input='y\n'*30, text=True, env=env, check=True)
subprocess.run([str(tool), f'--sdk_root={sdk}', 'platform-tools', 'platforms;android-35', 'build-tools;34.0.0'], env=env, check=True)
print('Android SDK ready:', sdk)
