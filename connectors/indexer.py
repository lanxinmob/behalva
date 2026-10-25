"""Local extraction only: no embedding, OCR or document body is sent to a cloud."""
from pathlib import Path
import hashlib
import json
import os
import zipfile
from xml.etree import ElementTree

EXCLUDE = {'.git', '.svn', '.ssh', '.aws', '.azure', '.codex', '.venv', 'venv', 'node_modules',
           'windows', 'program files', 'program files (x86)', 'programdata', 'appdata', '$recycle.bin',
           'system volume information', 'build', 'dist', 'target', '__pycache__', '.gradle', '.tools',
           'credentials', 'runtime', 'browser', 'vendor'}
EXTENSIONS = {'.md', '.txt', '.rst', '.pdf', '.docx', '.cpp', '.hpp', '.h', '.c', '.py', '.kt', '.java', '.js', '.ts'}

def eligible(path):
    return path.suffix.lower() in EXTENSIONS and not any(p.lower() in EXCLUDE for p in path.parts) and not any(
        word in path.name.lower() for word in ['secret', 'credential', 'password', 'token', '.env'])

def extract(path):
    if path.suffix.lower() == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return '\n\n'.join(f'[页 {i+1}]\n{p.extract_text() or "[无可提取文字，需 OCR]"}' for i,p in enumerate(reader.pages))
    if path.suffix.lower() == '.docx':
        with zipfile.ZipFile(path) as z:
            node = ElementTree.fromstring(z.read('word/document.xml'))
            return '\n'.join(''.join(p.itertext()) for p in node.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'))
    raw = path.read_bytes()
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError('Unsupported text encoding')

def scan(client, roots, manifest_path):
    manifest_path = Path(manifest_path)
    old = json.loads(manifest_path.read_text('utf-8')) if manifest_path.exists() else {}
    changed = 0
    def unavailable(key, reason):
        record = old.get(key)
        if record and record.get('availability_reason') != reason:
            client.call('sources/'+record['source_id']+'/availability',
                        {'available': False, 'reason': reason})
            record.pop('stamp', None)
            record['availability_reason'] = reason
    for root in roots:
        root = Path(root).resolve()
        if not root.is_dir():
            continue
        seen, errors = set(), []
        for parent, dirs, files in os.walk(root, followlinks=False, onerror=errors.append):
            dirs[:] = [d for d in dirs if d.lower() not in EXCLUDE and not Path(parent,d).is_symlink()
                       and not getattr(Path(parent,d), 'is_junction', lambda: False)()]
            for name in files:
                p = Path(parent, name)
                if not eligible(p) or p.is_symlink():
                    continue
                key = str(p.resolve())
                seen.add(key)
                try:
                    st = p.stat()
                    if st.st_size > 20 * 1024 * 1024:
                        unavailable(key, 'File exceeds the local extraction size limit')
                        continue
                    stamp = f'{st.st_mtime_ns}:{st.st_size}'
                    if old.get(key, {}).get('stamp') == stamp:
                        continue
                    text = extract(p)
                    if not text.strip():
                        unavailable(key, 'File contains no extractable text')
                        continue
                    if len(text.encode('utf-8')) > 4*1024*1024:
                        unavailable(key, 'Extracted text exceeds the index size limit')
                        continue
                    source = client.call('documents', {'uri': key, 'kind': 'file', 'title': p.name,
                                                       'body': text, 'coverage': 'Local text extraction'})
                    old[key] = {'stamp': stamp, 'source_id': source['id']}
                    changed += 1
                except (OSError, ValueError, RuntimeError) as e:
                    unavailable(key, 'Local extraction unavailable: '+type(e).__name__)
                    # Log locations, not contents or credential values.
                    print('Index skipped:', str(p), type(e).__name__, flush=True)
        # Only a completed traversal may infer disappearance. A disconnected
        # drive or unreadable subtree must not erase the current index.
        if not errors:
            for key in list(old):
                if key not in seen and Path(key).is_relative_to(root):
                    unavailable(key, 'File no longer present in the scanned location')
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(old, ensure_ascii=False), 'utf-8')
    temporary.replace(manifest_path)
    return changed
