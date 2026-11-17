import json
import unittest
import test_air_http as base

class ManualBatchHTTPTests(unittest.TestCase):
    setUp=base.HTTPTests.setUp
    tearDown=base.HTTPTests.tearDown
    request=base.HTTPTests.request
    def post(self,path,body):return self.request(path,'POST',body,{'X-Control-Token':self.s.token})
    def payload(self):
        return dict(action='batch_recipe',request_id='http-batch',recipes=[
            dict(sample=2,items=[dict(source=14,volume_ml=1.)]),
            dict(sample=5,items=[dict(source=15,volume_ml=.2)])])
    def test_offline_preview_requires_token_and_freezes_every_bottle(self):
        self.assertEqual(400,self.request('/api/batch-recipe-preview','POST',self.payload())[0])
        code,raw=self.post('/api/batch-recipe-preview',self.payload())
        self.assertEqual(200,code,raw)
        run=json.loads(raw)['plan']
        self.assertEqual(2,run['total_bottles'])
        self.assertEqual(714,run['recipes'][0]['items'][0]['total_steps'])
        self.assertIsNone(self.c.hardware);self.assertIsNone(self.c.manual_batch_run)
    def test_action_and_retry_return_completed_record_without_project(self):
        self.post('/api/connect',{})
        code,raw=self.post('/api/action',self.payload());self.assertEqual(200,code,raw)
        self.c.worker.join(5)
        self.assertEqual([14,3,3,15,15,7,7,15],[m['port'] for m in self.c.hardware.moves])
        self.assertTrue(self.c.manual_batch_run['completed'])
        self.post('/api/action',self.payload());self.c.worker.join(5)
        self.assertEqual(8,len(self.c.hardware.moves))
        self.assertEqual([],self.s.lab.data['projects'])
    def test_invalid_last_bottle_does_not_start_first(self):
        self.post('/api/connect',{})
        payload=self.payload();payload['recipes'][1]['sample']=6
        self.post('/api/action',payload);self.c.worker.join(5)
        self.assertTrue(self.c.connected);self.assertEqual([],self.c.hardware.moves)
        self.assertIn('操作未执行',self.c.message)
