"""Run locally after creating a Google OAuth desktop client, never commit tokens."""
import argparse
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

p = argparse.ArgumentParser()
p.add_argument('--client-secrets', required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
flow = InstalledAppFlow.from_client_secrets_file(a.client_secrets, [
    'https://www.googleapis.com/auth/gmail.send', 'https://www.googleapis.com/auth/gmail.readonly'])
credentials = flow.run_local_server(port=0)
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(credentials.to_json(), 'utf-8')
print('Gmail credentials saved in the specified private location.')
