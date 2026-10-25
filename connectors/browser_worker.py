"""Dedicated persistent browser. Only direct owner input controls this session.

No autonomous clicks, arbitrary JavaScript, or replay of unacknowledged actions.
The session is private; screenshots and input are not sent to a model or logged.
"""
import argparse
import base64
import json
import hashlib
import time
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright
from client import Client


def run(client, profile, *, headed=False):
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile), headless=not headed, viewport={'width': 1024, 'height': 768},
            accept_downloads=False)
        context.set_default_timeout(10000)
        page = context.pages[0] if context.pages else context.new_page()
        session_id = uuid.uuid4().hex
        while True:
            # Use the newest popup, preserving the same browser profile/session.
            pages = [p for p in context.pages if not p.is_closed()]
            if not pages:
                page = context.new_page()
            elif page.is_closed() or pages[-1] != page:
                page = pages[-1]
            command = None
            error = ''
            try:
                command = client.call('browser/poll', {})['command']
                if command and command['session_id'] == session_id:
                    action = command['action']
                    current_frame = base64.b64encode(page.screenshot(type='jpeg', quality=65)).decode()
                    if action not in ('navigate', 'refresh') and hashlib.sha256(current_frame.encode()).hexdigest() != command['frame_hash']:
                        raise RuntimeError('The page changed before input; inspect the latest frame')
                    if action == 'navigate':
                        page.goto(command['url'], wait_until='domcontentloaded', timeout=25000)
                    elif action == 'click':
                        page.mouse.click(command['x'], command['y'])
                    elif action == 'text':
                        page.keyboard.insert_text(command['text'])
                    elif action == 'key':
                        page.keyboard.press(command['key'])
                    elif action == 'scroll':
                        page.mouse.wheel(0, command['dy'])
                    page.wait_for_timeout(250)
            except Exception as exc:
                # Exception strings can contain typed secrets or page content.
                error = type(exc).__name__ + ': refresh and inspect the page; action is not retried'
            try:
                update = {'session_id': session_id, 'url': page.url, 'title': page.title(),
                          'image_base64': base64.b64encode(page.screenshot(type='jpeg', quality=65)).decode(),
                          'error': error}
                if command:
                    update['command_id'] = command['id']
                client.call('browser/publish', update)
            except Exception:
                # A lost acknowledgment leaves the core pending. A fresh session
                # explicitly discards it; the browser action itself never repeats.
                session_id = uuid.uuid4().hex
            time.sleep(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--ca')
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args()
    run(Client(args.url, args.data/'worker.token', args.ca or True), args.data/'browser-profile', headed=args.headed)
