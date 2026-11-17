"""Actual new HTTP routes, isolated data directory, no device connection."""
import json
import unittest
import test_air_http as base


class SourceVolumeHTTPTests(unittest.TestCase):
    setUp = base.HTTPTests.setUp
    tearDown = base.HTTPTests.tearDown
    request = base.HTTPTests.request

    def post(self, path, body):
        return self.request(path, 'POST', body, {'X-Control-Token': self.s.token})

    def test_source_calibration_save_and_preview_are_nonmoving_and_separate_from_mass(self):
        body = dict(source=14, sample=1, doses=5, temperature_c=25,
                    aspirate=dict(steps=45, rpm=5, timeout_s=20),
                    chase=dict(steps=2000, rpm=30, timeout_s=35),
                    groups_ml=[.75, .74, .76], min_steps=1, max_steps=240)
        self.assertEqual(400, self.request('/api/source-volume-calibration', 'POST', body)[0])
        code, raw = self.post('/api/source-volume-calibration', body)
        self.assertEqual(200, code, raw)
        ref = json.loads(raw)['reference']
        code, raw = self.post('/api/source-volume-preview', dict(source=14, sample=1, volume_ml=.5))
        self.assertEqual(200, code, raw)
        plan = json.loads(raw)['plan']
        self.assertEqual(ref['id'], plan['reference']['id'])
        self.assertEqual([150], plan['chunks'])
        self.assertEqual(30, plan['parameters'][0]['chase']['turns'])
        self.assertEqual(60, plan['parameters'][0]['chase']['rpm'])
        self.assertEqual(2000,plan['reference']['chase']['steps'])
        self.assertTrue(plan['chase_conditions_changed'])
        state = json.loads(self.request('/api/state')[1])
        self.assertEqual(1, len(state['source_volume_calibrations']))
        self.assertFalse(state['connected'])
        self.assertFalse(state['busy'])
        self.assertIsNone(self.c.hardware)
        self.assertEqual([], self.s.lab.data['calibrations'])

    def test_mass_readings_without_solution_density_return_actionable_error(self):
        code, raw = self.post('/api/source-volume-calibration', dict(source=14, sample=1,
            doses=1, temperature_c=25, groups_g=[.2],
            aspirate=dict(steps=45, rpm=5, timeout_s=20),
            chase=dict(steps=2000, rpm=30, timeout_s=35)))
        self.assertEqual(400, code)
        self.assertIn('密度', json.loads(raw)['error'])
        self.assertIsNone(self.c.hardware)

    def test_real_server_serves_new_calibration_ui_module(self):
        code, raw = self.request('/lab-source-volume.js')
        self.assertEqual(200, code, raw)
        self.assertIn(b'window.ATPSSourceVolume', raw)
        self.assertIsNone(self.c.hardware)
