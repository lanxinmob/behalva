"""Cache pinned Robolectric runtimes with bounded downloads and SHA-512 checks."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import tempfile
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'https://repo.maven.apache.org/maven2/org/robolectric/android-all-instrumented'
VERSIONS = ('9-robolectric-4913185-2-i7', '15-robolectric-12650502-i7')
CHUNK = 4 * 1024 * 1024


def get(url, **kwargs):
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=(15, 30), **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def prepare(version):
    name = f'android-all-instrumented-{version}.jar'
    url = f'{REPOSITORY}/{version}/{name}'
    expected = get(url+'.sha512').text.strip().split()[0].lower()
    if len(expected) != 128 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('Invalid repository SHA-512')
    directory = ROOT/'.tools/robolectric'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/name
    def valid(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha512').hexdigest() == expected
    if target.exists() and valid(target):
        return
    maven = Path.home()/'.m2/repository/org/robolectric/android-all-instrumented'/version/name
    if maven.exists() and valid(maven):
        import shutil
        shutil.copyfile(maven, target)
        print(f'Copied verified {name}', flush=True)
        return
    probe = get(url, headers={'Range': 'bytes=0-0'})
    if probe.status_code != 206 or not probe.headers.get('Content-Range', '').startswith('bytes 0-0/'):
        raise ValueError('Repository does not support bounded range downloads')
    size = int(probe.headers['Content-Range'].split('/')[-1])
    if not 0 < size < 400 * 1024 * 1024:
        raise ValueError('Unexpected Android runtime size')
    with tempfile.TemporaryDirectory(prefix='download-', dir=directory) as temporary:
        temporary = Path(temporary)
        def chunk(start):
            end = min(start + CHUNK, size) - 1
            response = get(url, headers={'Range': f'bytes={start}-{end}'})
            if (response.status_code != 206 or
                    response.headers.get('Content-Range') != f'bytes {start}-{end}/{size}' or
                    len(response.content) != end-start+1):
                raise ValueError('Invalid runtime download range')
            path = temporary/str(start)
            path.write_bytes(response.content)
            return path
        combined = temporary/'runtime.jar'
        with ThreadPoolExecutor(max_workers=4) as executor, combined.open('wb') as output:
            for path in executor.map(chunk, range(0, size, CHUNK)):
                with path.open('rb') as source:
                    import shutil
                    shutil.copyfileobj(source, output)
        if not valid(combined):
            raise ValueError('Android runtime SHA-512 mismatch')
        combined.replace(target)
    print(f'Downloaded and verified {name}', flush=True)


if __name__ == '__main__':
    for version in VERSIONS:
        prepare(version)
