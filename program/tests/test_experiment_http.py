"""Lab HTTP routes against the real server and isolated fake hardware."""
import io
import json
import unittest
import zipfile
import test_air_http as base


class ExperimentHTTPTests(unittest.TestCase):
    setUp=base.HTTPTests.setUp
    tearDown=base.HTTPTests.tearDown
    request=base.HTTPTests.request
    def post(self,path,payload):
        return self.request(path,'POST',payload,{'X-Control-Token':self.s.token})
    def test_lab_offline_state_and_export_never_connect_hardware(self):
        status,raw=self.request('/api/lab/state')
        self.assertEqual(200,status,raw)
        state=json.loads(raw)
        self.assertEqual([10,11,12,13,14,15],[s['port'] for s in state['stocks']])
        self.assertIn('K₂HPO₄',next(s for s in state['stocks'] if s['port']==13)['name'])
        self.assertEqual([1,2,3,4,5],[b['bottle'] for b in state['bottles']])
        self.assertFalse(state['running']);self.assertIsNone(self.c.hardware)
        status,raw=self.request('/api/lab/export')
        self.assertEqual(200,status,raw[:200])
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(raw)))
    def test_lab_mutations_require_control_token_and_reject_direct_compiled_motion(self):
        self.assertEqual(400,self.request('/api/lab/pause','POST',{})[0])
        self.assertEqual(400,self.post('/api/action',dict(action='experiment_recipe',plan={}))[0])
        self.assertIsNone(self.c.hardware)
    def test_lab_stop_keeps_connection(self):
        self.assertEqual(200,self.post('/api/connect',{})[0])
        self.assertEqual(200,self.post('/api/stop',{})[0])
        self.assertTrue(self.c.connected)
        self.assertFalse(self.s.lab.snapshot()['running'])
    def test_lab_image_rejects_paths(self):
        for path in ['/api/lab/image/../config.json','/api/lab/image/%2e%2e']:
            self.assertEqual(400,self.request(path)[0])


if __name__=='__main__':unittest.main()
