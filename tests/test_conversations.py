"""Paid-turn persistence, cross-device races and discussion-to-task context."""
import json
import os
import subprocess
import sys
import unittest
import uuid
import test_core

class Conversations(unittest.TestCase):
    def setUp(self):
        self.f=test_core.CoreContracts('test_offline_replay_and_manual_conflict_resolution')
        self.f.setUp()
        self.call=self.f.call
        self.cid=uuid.uuid4().hex

    def tearDown(self):self.f.tearDown()

    def message(self,body,seq=0,cid=None,**kwargs):
        request=dict(conversation_id=cid or self.cid,body=body,expected_sequence=seq,request_id=uuid.uuid4().hex)
        return request,self.call('messages',request,**kwargs)

    def answer(self,message):
        task=self.call('tasks/'+message['task_id']+'/claim',{},role='worker')
        return self.call('model/run',dict(task_id=task['id'],attempt_id=task['attempt_id'],source_ids=[]),role='worker')

    def test_history_survives_restart_replay_and_unrelated_conversations(self):
        original,m=self.message('Remember fixture project Aurora; discuss a reminder feature.')
        self.message('This must not be a forged owner message',role='worker',status=403)
        _,other=self.message('Unrelated private conversation',cid=uuid.uuid4().hex)
        first=self.answer(m)
        self.assertIn('Aurora',first['body'])
        self.f.stop();self.f.start()
        _,second=self.message('What did I call the project?',2)
        second_answer=self.answer(second)
        sent=json.loads(self.f.provider.calls[-1]['input'])['conversation_history']
        self.assertEqual(['user','assistant','user'],[x['role'] for x in sent])
        self.assertIn('Aurora',sent[0]['content'])
        self.assertNotIn('Unrelated',json.dumps(sent))
        self.assertEqual(m,self.call('messages',original))
        self.assertEqual(2,len(self.f.provider.calls))
        state=self.call('state')
        messages=sorted((x for x in state['messages'] if x['conversation_id']==self.cid),key=lambda x:x['sequence'])
        self.assertEqual([1,2,3,4],[x['sequence'] for x in messages])
        self.assertEqual(second_answer['body'],messages[-1]['body'])
        self.assertEqual('completed',next(t for t in state['tasks'] if t['id']==second['task_id'])['state'])

    def test_stale_offline_message_and_unknown_paid_call_are_not_replayed(self):
        request,m=self.message('Discuss fixture planning')
        self.message('Concurrent stale input',0,status=409)
        self.assertEqual(1,len(self.call('state')['messages']))
        self.f.provider.reject=True
        task=self.call('tasks/'+m['task_id']+'/claim',{},role='worker')
        self.call('model/run',dict(task_id=task['id'],attempt_id=task['attempt_id'],source_ids=[]),role='worker',status=502)
        self.call('tasks/'+task['id']+'/result',dict(state='blocked',result={},attempt_id=task['attempt_id']),role='worker')
        self.message('Cannot silently skip an unresolved paid turn',1,status=409)
        self.call('tasks/'+task['id']+'/resume',dict(reply='',request_id=uuid.uuid4().hex))
        task=self.call('tasks/'+task['id']+'/claim',{},role='worker')
        self.call('model/run',dict(task_id=task['id'],attempt_id=task['attempt_id'],source_ids=[]),role='worker',status=409)
        self.assertEqual(1,len(self.f.provider.calls))
        self.assertEqual(m,self.call('messages',request))

    def test_discussion_snapshot_creates_task_and_feature_without_code_execution(self):
        _,m=self.message('Plan a fixture data organizer')
        self.answer(m)
        request=dict(title='Research fixture formats',kind='research',expected_sequence=2,request_id=uuid.uuid4().hex)
        task=self.call(f'conversations/{self.cid}/tasks',request)
        self.assertEqual('queued',task['state'])
        self.assertEqual(2,len(task['conversation_context']))
        self.assertEqual(task,self.call(f'conversations/{self.cid}/tasks',request))
        _,next_message=self.message('Add offline export',2)
        self.answer(next_message)
        feature=self.call(f'conversations/{self.cid}/tasks',dict(title='Add offline export with conflict handling',kind='feature',expected_sequence=4,request_id=uuid.uuid4().hex))
        self.assertEqual('waiting_input',feature['state'])
        self.call('tasks/'+feature['id']+'/resume',dict(reply='go'),status=409)
        saved=next(t for t in self.call('state')['tasks'] if t['id']==task['id'])
        self.assertEqual(2,len(saved['conversation_context']))
        request['request_id']=uuid.uuid4().hex
        self.call(f'conversations/{self.cid}/tasks',request,status=409)

    def test_worker_answers_chat_without_web_search(self):
        _,m=self.message('Help discuss a fixture feature')
        env=os.environ.copy();env['NO_PROXY']='127.0.0.1,localhost'
        result=subprocess.run([sys.executable,str(test_core.ROOT/'connectors/worker.py'),
            '--data',str(self.f.data),'--url',f'http://127.0.0.1:{self.f.port}','--once'],env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(0,result.returncode,result.stderr)
        state=self.call('state')
        task=next(t for t in state['tasks'] if t['id']==m['task_id'])
        self.assertEqual('completed',task['state'],result.stdout)
        self.assertEqual(2,len(state['messages']))
        self.assertEqual([],state['sources'])

if __name__=='__main__':unittest.main(verbosity=2)
