"""Adapter recovery must never turn a read-back into another send."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'connectors'))
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

if __name__=='__main__':unittest.main(verbosity=2)
