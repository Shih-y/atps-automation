import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from controller import Controller
from server import Server
from fakes import DemoHardware

class HTTPTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'test_output'; root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root); self.c=Controller(self.temp.name,hardware_factory=lambda cfg:DemoHardware(cfg))
        self.s=Server(('127.0.0.1',0),self.c); self.thread=threading.Thread(target=self.s.serve_forever,daemon=True); self.thread.start()
    def tearDown(self):
        self.c.stop()
        if self.c.worker: self.c.worker.join(2)
        self.s.shutdown(); self.s.server_close(); self.thread.join(2); self.temp.cleanup()
    def request(self,path,method='GET',payload=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.s.server_port,timeout=3)
        body=json.dumps(payload) if payload is not None else None
        h={'Content-Type':'application/json'}; h.update(headers or {})
        conn.request(method,path,body,h); r=conn.getresponse(); result=(r.status,r.read()); conn.close(); return result
    def test_token_and_default_no_hardware(self):
        status,raw=self.request('/api/token'); self.assertEqual(200,status); self.assertEqual(self.s.token,json.loads(raw)['token'])
        status,raw=self.request('/api/state'); self.assertFalse(json.loads(raw)['connected']); self.assertIsNone(self.c.hardware)
    def test_missing_token_denies_motion(self):
        status,_=self.request('/api/connect','POST',{'mode':'demo'}); self.assertEqual(400,status); self.assertFalse(self.c.connected)
    def test_wrong_origin_and_dns_rebinding_host_rejected(self):
        for hdr in [{'Host':'attacker.example'},{'Origin':'https://attacker.example'},{'Sec-Fetch-Site':'cross-site'}]:
            status,_=self.request('/api/token',headers=hdr); self.assertEqual(400,status)
    def test_connect_authenticated_and_stop(self):
        hdr={'X-Control-Token':self.s.token}
        self.assertEqual(200,self.request('/api/connect','POST',{},hdr)[0]); self.assertTrue(self.c.connected)
        self.assertEqual(200,self.request('/api/stop','POST',{},hdr)[0]); self.assertTrue(self.c.connected)
        self.assertFalse(self.c.cancel.is_set())
    def test_simulation_mode_is_not_available(self):
        status,raw=self.request('/api/connect','POST',{'mode':'demo'},{'X-Control-Token':self.s.token})
        self.assertEqual(400,status); self.assertIn('仅支持实机',json.loads(raw)['error'])
        self.assertFalse(self.c.connected)
    def test_default_shared_port_and_no_startup_checkbox(self):
        status,raw=self.request('/api/config'); cfg=json.loads(raw)
        self.assertEqual('COM6',cfg['communication']['port'])
        self.assertNotIn('preflight_verified',cfg)
        status,raw=self.request('/api/state'); self.assertEqual('real',json.loads(raw)['mode'])
    def test_nonloopback_server_forbidden(self):
        with self.assertRaises(ValueError): Server(('0.0.0.0',0),self.c)
    def test_image_path_traversal_rejected(self):
        status,_=self.request('/api/images/../config.default.json'); self.assertEqual(400,status)
    def test_guided_calibration_endpoint_saves_without_unlocking(self):
        payload={'measurement_mode':'water_gravimetry','profile_key':'water-15-1-first','source':15,'sample':1,'condition':'first',
                 'assay_method':'pure water balance test','density_g_ml':1,'delivery_values':[.2]*10,
                 'water_carry_values_ml':[.001]*3,'cross_carry_values_ml':[.001]*3,'target_ml':.2}
        status,raw=self.request('/api/calibration/report','POST',payload,{'X-Control-Token':self.s.token})
        self.assertEqual(200,status,raw); result=json.loads(raw)
        self.assertFalse(result['report']['hardware_authorized']); self.assertEqual(16,len(result['rows']))
        self.assertEqual(self.c.snapshot()['geometry_signature'],result['report']['geometry_signature'])
        saved=json.loads(Path(result['saved_file']).read_text(encoding='utf-8'))
        self.assertEqual(payload,saved['raw_input']); self.assertEqual(self.c.config,saved['config'])
        self.assertEqual([],self.c.config['profiles']); self.assertIsNone(self.c.hardware)
    def test_calibration_auth_and_invalid_stock_mass_denied(self):
        payload={'measurement_mode':'water_gravimetry','profile_key':'wrong','source':11,'sample':1,'condition':'first',
                 'assay_method':'mass','density_g_ml':1,'delivery_values':[.2]*10}
        self.assertEqual(400,self.request('/api/calibration/report','POST',payload)[0])
        self.assertEqual(400,self.request('/api/calibration/report','POST',payload,{'X-Control-Token':self.s.token})[0])

if __name__=='__main__': unittest.main()
