import json
import time
import unittest

import test_air_http


class SimulationWorkspaceTests(unittest.TestCase):
    setUp = test_air_http.HTTPTests.setUp
    tearDown = test_air_http.HTTPTests.tearDown
    request = test_air_http.HTTPTests.request

    def test_virtual_batch_and_priority_stop_leave_real_controller_disconnected(self):
        status, raw = self.request('/simulation/api/token')
        self.assertEqual(status, 200)
        headers = {'X-Control-Token': json.loads(raw)['token']}
        self.assertNotEqual(headers['X-Control-Token'], self.s.token)
        self.assertEqual(400, self.request('/api/stop', 'POST', {}, headers)[0])
        sim = self.s.simulation.controller
        recipe = dict(action='batch_recipe', request_id='simulation-qa', rinse_after=False,
                      recipes=[dict(sample=i, items=[dict(source=13, volume_ml=.2)]) for i in (1, 2)])
        self.assertEqual(200, self.request('/simulation/api/action', 'POST', recipe, headers)[0])
        # Production rehearsal is real time: two 30-turn chases at 60 rpm
        # alone take 60 seconds. Do not accelerate the production simulator.
        sim.worker.join(90)
        self.assertEqual(sim.snapshot()['manual_batch_run']['completed_bottles'], 2)
        self.assertFalse(self.c.connected)
        self.assertIsNone(self.c.manual_batch_run)
        sim.start(dict(action='manual_motion', rotation='cw', turns=2, rpm=30, port=13))
        deadline = time.monotonic() + 3
        while sim.hardware.get_telemetry()['pump_status'] != 'running' and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(sim.hardware.get_telemetry()['direction'], 'cw')
        self.assertEqual(200, self.request('/simulation/api/stop', 'POST', {}, headers)[0])
        self.assertFalse(sim.busy)
        self.assertEqual(sim.stop_status, 'confirmed')
        self.assertEqual(sim.hardware.get_telemetry()['pump_status'], 'stopped')
        self.assertIsNone(self.c.hardware)
