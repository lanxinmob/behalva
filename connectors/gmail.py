"""Single-attempt Gmail execution. Unknown sends are reconciled, never resent."""
from pathlib import Path
from email.message import EmailMessage
import base64
import hashlib
import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

BASE = 'https://gmail.googleapis.com/gmail/v1/users/me/'
SCOPES = ['https://www.googleapis.com/auth/gmail.send', 'https://www.googleapis.com/auth/gmail.readonly']

def session(credentials_path):
    p = Path(credentials_path)
    if not p.exists():
        raise RuntimeError('Gmail OAuth is not configured')
    creds = Credentials.from_authorized_user_file(str(p), SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        p.write_text(creds.to_json(), 'utf-8')
    s = requests.Session()
    s.headers['Authorization'] = 'Bearer ' + creds.token
    return s

def verify(client, credentials_path):
    """One owner-requested, read-only identity check. Sends nothing; never re-run."""
    claimed = client.call('connections/gmail/check/claim', {})
    try:
        s = session(credentials_path)
        r = s.get(BASE+'profile', timeout=20)
        r.raise_for_status()
        result = {'state': 'verified', 'account': r.json()['emailAddress']}
    except Exception as e:
        result = {'state': 'error', 'reason': failure_reason(e)}
    client.call('connections/gmail/check/result', {'attempt_id': claimed['attempt_id'], **result})

def failure_reason(error):
    from google.auth.exceptions import RefreshError
    if isinstance(error, RefreshError):
        return '授权已失效或被撤销；请在电脑重新运行 Gmail 授权。'
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f'Gmail 拒绝读取账号资料（HTTP {error.response.status_code}）。'
    if isinstance(error, (requests.ConnectionError, requests.Timeout)):
        return '无法连接 Gmail；请检查网络或代理。'
    if isinstance(error, RuntimeError) and 'not configured' in str(error):
        return '找不到 Gmail 授权文件。'
    return '验证失败（' + type(error).__name__ + '）。'

def reconcile(client, operation, s):
    message_id = f"pa.{operation['id']}@personal-assistant.local"
    r = s.get(BASE+'messages', params={'q': f'in:sent rfc822msgid:{message_id}'}, timeout=20)
    r.raise_for_status()
    found = r.json().get('messages', [])
    if len(found) == 1:
        client.call(f"operations/{operation['id']}/result", {'attempt_id': operation['attempt_id'],
                    'state': 'succeeded', 'evidence': 'Gmail sent message ID: '+found[0]['id']})
        return True
    return False

def execute(client, proposed, credentials_path):
    s = session(credentials_path)
    profile = s.get(BASE+'profile', timeout=20)
    profile.raise_for_status()
    sender = profile.json()['emailAddress']
    if proposed['state'] in ('unknown', 'executing'):
        reconcile(client, proposed, s)
        return
    payload = proposed['payload']
    if payload['from'].lower() != sender.lower():
        raise RuntimeError('Approved sender does not match authenticated Gmail account')
    message = EmailMessage()
    message['From'], message['To'], message['Subject'] = sender, payload['to'], payload['subject']
    message['Message-ID'] = f"<pa.{proposed['id']}@personal-assistant.local>"
    message.set_content(payload['body'])
    for attachment in payload.get('attachments', []):
        blob = client.call('attachments/'+attachment['id'])
        content = base64.b64decode(blob['base64'], validate=True)
        if hashlib.sha256(content).hexdigest() != attachment['sha256']:
            raise RuntimeError('Approved attachment integrity failure')
        message.add_attachment(content, maintype='application', subtype='octet-stream', filename=attachment['name'])
    operation = client.call(f"operations/{proposed['id']}/claim", {})
    # Revalidate the exact prepared payload after atomically acquiring execution.
    if operation['payload_hash'] != proposed['payload_hash']:
        client.call(f"operations/{operation['id']}/result", {'attempt_id': operation['attempt_id'], 'state': 'failed',
                    'evidence': 'Prepared message became stale; nothing sent'})
        return
    try:
        r = s.post(BASE+'messages/send', json={'raw': base64.urlsafe_b64encode(message.as_bytes()).decode()}, timeout=45)
        if r.ok and r.json().get('id'):
            client.call(f"operations/{operation['id']}/result", {'attempt_id': operation['attempt_id'],
                        'state': 'succeeded', 'evidence': 'Gmail send response ID: '+r.json()['id']})
            return
    except (requests.RequestException, ValueError):
        pass
    client.call(f"operations/{operation['id']}/result", {'attempt_id': operation['attempt_id'],
                'state': 'unknown', 'evidence': 'No definitive send receipt; do not resend'})
    reconcile(client, operation, s)
