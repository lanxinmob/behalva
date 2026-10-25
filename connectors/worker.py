"""Persistent worker, independent of any desktop conversation window."""
import argparse
import json
import time
import threading
from pathlib import Path
from client import Client, APIError
import research
import indexer

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text('utf-8-sig')) if args.config else {}
    client = Client(args.url, args.data/'worker.token', cfg.get('server_ca', True))
    last_scan = 0
    while True:
        try:
            state = client.call('state')
            queued = sorted((t for t in state['tasks'] if t['state']=='queued'), key=lambda t:(-t.get('priority',1),t['created_at']))
            for task in queued[:1]:
                claimed = client.call('tasks/'+task['id']+'/claim', {})
                stop = threading.Event()
                def heartbeat():
                    pulse = Client(args.url, args.data/'worker.token', cfg.get('server_ca', True))
                    while not stop.wait(60):
                        try:
                            pulse.call('tasks/'+claimed['id']+'/heartbeat', {'attempt_id': claimed['attempt_id']})
                        except Exception:
                            return
                monitor = threading.Thread(target=heartbeat, daemon=True)
                monitor.start()
                try:
                    if claimed.get('kind') == 'chat':
                        result = client.call('model/run', {'task_id': claimed['id'],
                            'attempt_id': claimed['attempt_id'], 'source_ids': []})
                        result_state = 'completed'
                    else:
                        result_state, result = research.run(client, claimed)
                except APIError as e:
                    result_state = 'waiting_authorization' if e.status==403 else 'blocked'
                    result = {'message': str(e), **e.data.get('details', {})}
                except Exception as e:
                    result_state, result = 'blocked', {'message': str(e)}
                finally:
                    stop.set()
                    monitor.join()
                client.call('tasks/'+task['id']+'/result', {'state': result_state, 'result': result, 'attempt_id': claimed['attempt_id']})
            if cfg.get('gmail_credentials'):
                import gmail
                for check in state.get('connection_checks', []):
                    if check['id']=='gmail' and check.get('state')=='requested':
                        try:
                            gmail.verify(client, cfg['gmail_credentials'])
                        except Exception as e:
                            print('Gmail verification not completed:', type(e).__name__, str(e), flush=True)
                for op in state['operations']:
                    if op['kind']=='gmail' and op['state'] in ('approved','unknown','executing'):
                        try:
                            gmail.execute(client, op, cfg['gmail_credentials'])
                        except Exception as e:
                            print('Mail needs attention:', op['id'], type(e).__name__, str(e), flush=True)
            if not queued and cfg.get('scan_roots') and time.time()-last_scan > cfg.get('scan_interval_seconds', 300):
                indexer.scan(client, cfg['scan_roots'], args.data/'index-manifest.json')
                last_scan = time.time()
            if not queued:
                import materials
                materials.process(client,state.get('attachments',[])[:20],args.data)
        except Exception as e:
            print('Worker unavailable:', type(e).__name__, str(e), flush=True)
        if args.once:
            return
        time.sleep(5)

if __name__ == '__main__':
    main()
