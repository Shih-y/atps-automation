"""Current console contracts against the actual HTTP server and fake hardware."""
import base64
import json
import unittest
from controller import Controller
import test_air_http as base
from test_phase_archive import test_png
from capture_store import timestamp


class ConsoleHTTPTests(unittest.TestCase):
    setUp=base.HTTPTests.setUp
    tearDown=base.HTTPTests.tearDown
    request=base.HTTPTests.request

    def post(self,path,payload):
        status,raw=self.request(path,'POST',payload,{'X-Control-Token':self.s.token})
        self.assertEqual(200,status,raw)
        return json.loads(raw)

    def test_current_home_loads_only_current_assets_without_connecting(self):
        status,raw=self.request('/')
        self.assertEqual(200,status)
        self.assertIn(b'/console.js',raw)
        self.assertNotIn(b'src="/app.js"',raw)
        for path in ['/console.js','/console.css','/phase_analysis.js']:
            status,raw=self.request(path)
            self.assertEqual(200,status,path)
            self.assertGreater(len(raw),100)
        self.assertFalse(self.c.connected)
        self.assertIsNone(self.c.hardware)

    def test_reference_route_persists_offline_and_is_authenticated(self):
        payload=dict(mean_per_dose_ml=.16,sample=1,aspirate_steps=45,
                     aspirate_rpm=5,chase_steps=1000,chase_rpm=15)
        self.assertEqual(400,self.request('/api/reference','POST',payload)[0])
        result=self.post('/api/reference',payload)
        restarted=Controller(self.temp.name)
        self.assertEqual(result['reference'],restarted.water_dose_reference)
        self.assertFalse(restarted.water_dose_reference['individual_repeatability_verified'])
        self.assertIsNone(self.c.hardware)

    def test_recipe_route_delivers_all_components_before_mix_and_single_purge(self):
        self.post('/api/connect',{})
        self.post('/api/action',dict(action='recipe',sample=1,
            items=[dict(source=15,volume_ml=.5),dict(source=14,volume_ml=1),dict(source=13,volume_ml=.3)],
            mix_after=True,purge_after=True,mixing=dict(steps=20,rpm=5,timeout_s=10)))
        self.c.worker.join(5)
        self.assertFalse(self.c.busy)
        status,raw=self.request('/api/state');state=json.loads(raw)
        self.assertEqual(200,status)
        self.assertTrue(state['recipe_run']['completed'],state['message'])
        self.assertEqual([15,1,14,1,14,1,13,1,1,16],[m['port'] for m in self.c.hardware.moves])
        self.assertEqual(10,state['recipe_run']['completed_commands'])
        self.assertIsNone(state['recipe_run']['actual_ml'])

    def test_offline_image_routes_save_raw_frame_and_partial_analysis(self):
        result=self.post('/api/imaging/begin',dict(targets=[1,2,3,4,5]))
        analysis=dict(method='synthetic-contract-test',validated=False,rois=[],bottles=[])
        saved=self.post('/api/capture',dict(batch_id=result['batch_id'],width=100,height=40,
            png_base64=base64.b64encode(test_png()).decode(),captured_at=timestamp(),
            alignment_confirmed=False,allow_partial_rois=True,rois=[],analysis=analysis))
        self.assertEqual([],saved['imaging']['records'])
        self.assertEqual([1,2,3,4,5],saved['frame']['unassigned_targets'])
        status,raw=self.request(saved['frame']['url'])
        self.assertEqual((200,test_png()),(status,raw))
        self.assertIsNone(self.c.hardware)

    def test_invalid_algorithm_does_not_write_png(self):
        result=self.post('/api/imaging/begin',dict(targets=[1]))
        status,raw=self.request('/api/capture','POST',dict(batch_id=result['batch_id'],width=100,height=40,
            png_base64=base64.b64encode(test_png()).decode(),captured_at=timestamp(),
            alignment_confirmed=False,allow_partial_rois=True,rois=[],
            analysis=dict(method='wrong',validated=True,rois=[],bottles=[])),{'X-Control-Token':self.s.token})
        self.assertEqual(400,status,raw)
        self.assertEqual([],list(self.c.directory.rglob('*.png')))


if __name__=='__main__':unittest.main()
