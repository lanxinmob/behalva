"""Adapter recovery must never turn a read-back into another send."""
import http.server
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'connectors'))
import client
import gmail
import research

class Response:
    def __init__(self,data):self.data=data
    def raise_for_status(self):pass
    def json(self):return self.data

class ReadOnlyGmail:
    def __init__(self):self.gets=[]
    def get(self,url,**kwargs):
        self.gets.append((url,kwargs))
        return Response({'emailAddress':'fixture@example.org'} if url.endswith('profile') else {'messages':[{'id':'verified-receipt'}]})
    def post(self,*args,**kwargs):raise AssertionError('Recovery tried sending again')

class Recorder:
    def __init__(self):self.calls=[]
    def call(self,path,body=None):
        self.calls.append((path,body))
        if path.endswith('/claim'):raise AssertionError('Recovery claimed another send')
        return {}

class ConnectorRecovery(unittest.TestCase):
    def test_worker_restart_reconciles_executing_and_unknown_mail_without_send(self):
        for state in ('executing','unknown'):
            client=Recorder();session=ReadOnlyGmail()
            with patch.object(gmail,'session',return_value=session):
                gmail.execute(client,{'id':'fixture','attempt_id':'attempt-one','state':state},'unused')
            self.assertEqual(1,len(client.calls))
            path,result=client.calls[0]
            self.assertEqual('operations/fixture/result',path)
            self.assertEqual('succeeded',result['state'])
            self.assertIn('verified-receipt',result['evidence'])
            self.assertIn('rfc822msgid:pa.fixture@personal-assistant.local',session.gets[-1][1]['params']['q'])

    def test_incomplete_model_response_remains_waiting_and_saved_table_keys_are_stable(self):
        class ResearchClient:
            def __init__(self):self.tables={}
            def call(self,path,body):
                if path=='model/run':return {'run_id':'paid-run','draft_id':'persisted-draft',
                    'body':'| Item | Evidence |\n| --- | --- |\n| A | citation |',
                    'completion_status':'incomplete','citation_status':'references_validated',
                    'citations':[],'invalid_citations':[]}
                if path=='tables':
                    self.tables[body['request_id']]=body
                    return {'id':body['request_id']}
                raise AssertionError(path)
        client=ResearchClient()
        task={'id':'fixture','attempt_id':'attempt-one','title':'Compare','source_ids':['fixture-source']}
        state,result=research.run(client,task)
        self.assertEqual('waiting_input',state)
        self.assertEqual('persisted-draft',result['draft_id'])
        research.run(client,{**task,'attempt_id':'recovery-attempt'})
        self.assertEqual(1,len(client.tables))

class KeepAliveRace(http.server.BaseHTTPRequestHandler):
    """Closes a reused connection as the next request arrives, like the core's idle keep-alive expiry."""
    protocol_version='HTTP/1.1'
    def setup(self):
        super().setup();self.served=0
    def log_message(self,*args):pass
    def respond(self):
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        self.server.attempts.append((self.command,self.path,self.served>0))
        if self.served>0:
            self.close_connection=True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.served+=1
        raw=b'{"ok": true}'
        self.send_response(200);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    do_GET=do_POST=respond

class CoreConnectionReuse(unittest.TestCase):
    """Connection drops must not become log noise, and must never replay a side-effecting call."""
    def setUp(self):
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),KeepAliveRace)
        self.server.attempts=[]
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.temp=tempfile.TemporaryDirectory();token=Path(self.temp.name)/'worker.token';token.write_text('fixture')
        self.client=lambda idle:client.Client(f'http://127.0.0.1:{self.server.server_port}',token,reuse_idle_seconds=idle)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.temp.cleanup()

    def test_post_after_idle_uses_fresh_connection_instead_of_racing_server_close(self):
        stale=self.client(60)
        stale.call('state');time.sleep(.3)
        with self.assertRaises(requests.ConnectionError):
            stale.call('tasks/fixture/result',{'state':'completed'})
        self.assertEqual([('POST','/api/tasks/fixture/result',True)],[a for a in self.server.attempts if a[0]=='POST'])
        self.server.attempts.clear()
        fresh=self.client(.2)
        fresh.call('state');time.sleep(.3)
        self.assertEqual({'ok':True},fresh.call('tasks/fixture/result',{'state':'completed'}))
        self.assertEqual([('GET','/api/state',False),('POST','/api/tasks/fixture/result',False)],self.server.attempts)

    def test_dropped_read_is_retried_once_but_dropped_write_is_not(self):
        reused=self.client(60)
        reused.call('state')
        self.assertEqual({'ok':True},reused.call('state'))
        self.assertEqual([('GET','/api/state',False),('GET','/api/state',True),('GET','/api/state',False)],self.server.attempts)
        self.server.attempts.clear()
        with self.assertRaises(requests.ConnectionError):
            reused.call('tasks/fixture/claim',{})
        self.assertEqual([('POST','/api/tasks/fixture/claim',True)],self.server.attempts)

if __name__=='__main__':unittest.main(verbosity=2)
