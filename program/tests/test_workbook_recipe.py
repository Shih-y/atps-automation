"""Isolated recipe commands; fake transport is not physical validation."""
import copy
import threading
import unittest
from controller import Controller
import test_air_controller as base


class WorkbookRecipeTests(unittest.TestCase):
    setUp = base.AirControllerTest.setUp
    tearDown = base.AirControllerTest.tearDown

    def run_recipe(self, **extra):
        self.c.config['free_debug'] = True
        self.c._ask = lambda *a, **kw: self.fail('Recipe must not prompt for prerequisites')
        payload = dict(action='recipe', sample=2, items=[dict(source=14, volume_ml=.5)])
        payload.update(extra)
        self.c.start(payload)
        self.c.worker.join(5)
        self.assertFalse(self.c.busy)
        self.assertEqual('IDLE', self.c.state, self.c.message)
        self.assertFalse(self.c.message.startswith('操作未执行'), self.c.message)
        return self.c.snapshot()['recipe_run']

    def test_recipe_mixes_once_after_all_components_then_purges_once(self):
        run = self.run_recipe(items=[dict(source=14, volume_ml=1.), dict(source=13, volume_ml=.3)])
        moves = self.c.hardware.moves
        self.assertEqual([14, 2, 14, 2, 13, 2, 2, 16], [m['port'] for m in moves])
        self.assertEqual([150, 1105, 150, 1105, 90, 1045, 20], [m['steps'] for m in moves[:-1]])
        self.assertEqual('dispense', moves[-2]['direction'])
        self.assertEqual(15, moves[-1]['turns'])
        self.assertTrue(run['completed'])
        self.assertTrue(run['mixing_performed'])
        self.assertTrue(run['purge_performed'])
        self.assertEqual(8, run['completed_commands'])
        self.assertEqual(3, run['completed_plugs'])
        self.assertIsNone(run['actual_ml'])
        self.assertEqual(run['mixing_finished_at'], self.c.samples['2']['last_gas_stop'])
        self.assertFalse(run['volume_verified'])

    def test_split_boundaries_and_requested_versus_quantized(self):
        for ml, chunks in [(.8, [240]), (.801, [120,120]), (1., [150,150]),
                           (1.6, [240,240]), (2., [200,200,200]), (1.003, [151,150])]:
            with self.subTest(ml=ml):
                run = self.run_recipe(items=[dict(source=14, volume_ml=ml)])
                item = run['items'][0]
                self.assertEqual(chunks, item['chunks'])
                self.assertEqual(sum(chunks), item['total_steps'])
                self.assertEqual(ml, item['requested_ml'])
                self.assertAlmostEqual(sum(chunks)/300, item['estimated_ml'])
                self.assertTrue(all(n <= 240 for n in chunks))

    def test_invalid_later_item_mix_and_limits_cause_zero_initial_motion(self):
        self.c.config['free_debug'] = True
        good = dict(action='recipe', sample=1, items=[dict(source=14, volume_ml=.3)])
        cases = [dict(items=good['items']+[dict(source=7, volume_ml=.2)]),
                 dict(items=good['items']+[dict(source=13, volume_ml=float('nan'))]),
                 dict(items=good['items']+[None]), dict(sample=True),
                 dict(items=[dict(source=14, volume_ml=6.),dict(source=13, volume_ml=5.)]),
                 dict(mixing=dict(steps=20,rpm=101,timeout_s=10)),
                 dict(mixing=dict(turns=1,rpm=5,timeout_s=10)), dict(purge_after='yes'),
                 dict(mix_after='yes')]
        for extra in cases:
            with self.subTest(extra=extra):
                self.c.start(dict(good, **extra)); self.c.worker.join(2)
                self.assertEqual([], self.c.hardware.moves)
                self.assertIn('操作未执行', self.c.message)
                self.assertTrue(self.c.connected)

    def test_explicit_no_mix_or_purge_and_configured_mixing(self):
        run = self.run_recipe(mix_after=False, purge_after=False)
        self.assertEqual([14,2], [m['port'] for m in self.c.hardware.moves])
        self.assertFalse(run['mixing_performed'])
        self.assertFalse(run['purge_performed'])
        self.run_recipe(mixing=dict(steps=30,rpm=4,timeout_s=15))
        self.assertEqual(30,self.c.hardware.moves[-2]['steps'])

    def test_invalid_new_request_clears_previous_completed_run(self):
        self.run_recipe()
        self.c.start(dict(action='recipe',sample=2,items=[])); self.c.worker.join(2)
        self.assertIsNone(self.c.snapshot()['recipe_run'])

    def test_stop_cancels_mid_recipe_and_preserves_connection_without_resuming(self):
        self.c.config['free_debug'] = True
        started = threading.Event(); original = self.c.hardware.move
        def blocked(direction, steps, rpm, event):
            started.set(); event.wait(2); self.c.hardware._check(event)
            return original(direction, steps, rpm, event)
        hardware = self.c.hardware
        hardware.move = blocked
        self.c.start(dict(action='recipe',sample=2,items=[dict(source=14,volume_ml=2.)]))
        self.assertTrue(started.wait(1))
        self.c.stop_keep_connection()
        self.assertIs(hardware,self.c.hardware)
        self.assertTrue(self.c.connected)
        self.assertFalse(self.c.worker.is_alive())
        self.assertEqual([], hardware.moves)
        run = self.c.snapshot()['recipe_run']
        self.assertTrue(run['cancelled'])
        self.assertFalse(run['completed'])
        self.assertEqual(0,run['completed_commands'])
        hardware.move = original
        self.run_recipe(items=[dict(source=13,volume_ml=.15)])
        self.assertEqual([13,2,2,16],[m['port'] for m in hardware.moves])

    def reference_payload(self, **extra):
        return dict(mean_per_dose_ml=.2, sample=1, aspirate_steps=45,
                    aspirate_rpm=5, chase_steps=1000, chase_rpm=15, **extra)

    def test_reference_is_persisted_offline_and_used_without_invented_repeatability(self):
        self.assertTrue(hasattr(self.c, 'update_dose_reference'), 'Reference API missing')
        self.c.disconnect()
        result = self.c.update_dose_reference(self.reference_payload())
        restarted = Controller(self.tmp.name)
        ref = restarted.water_dose_reference
        self.assertEqual(result['reference'], ref)
        self.assertTrue(ref['user_reported'])
        self.assertFalse(ref['individual_repeatability_verified'])
        self.assertIsNone(ref['group_rsd_percent'])
        self.assertEqual([], ref['readings_g'])
        self.c.connect()
        run = self.run_recipe(items=[dict(source=14,volume_ml=1.)])
        self.assertEqual([113,112], run['items'][0]['chunks'])
        self.assertAlmostEqual(1., run['items'][0]['estimated_ml'])

    def test_invalid_reference_and_busy_leave_file_and_current_reference_unchanged(self):
        self.assertTrue(hasattr(self.c, 'update_dose_reference'), 'Reference API missing')
        self.c.update_dose_reference(self.reference_payload())
        path = self.c.directory/'water_dose_reference.json'
        before = path.read_bytes(); reference = copy.deepcopy(self.c.water_dose_reference)
        for extra in [dict(mean_per_dose_ml=0),dict(sample=True),dict(aspirate_steps=1.5),
                      dict(chase_rpm=.1),dict(chase_rpm=1.23)]:
            with self.assertRaises(ValueError):
                self.c.update_dose_reference(dict(self.reference_payload(), **extra))
        self.c.busy=True
        try:
            with self.assertRaises(ValueError): self.c.update_dose_reference(self.reference_payload())
        finally: self.c.busy=False
        self.assertEqual(before,path.read_bytes())
        self.assertEqual(reference,self.c.water_dose_reference)

    def test_recalibrated_parts_never_round_above_segment_limit(self):
        self.c.update_dose_reference(dict(self.reference_payload(),mean_per_dose_ml=.203))
        run=self.run_recipe(items=[dict(source=14,volume_ml=1.6)])
        self.assertEqual([119,118,118],run['items'][0]['chunks'])
        self.assertEqual(355,run['items'][0]['total_steps'])
        self.assertTrue(all(steps*.203/45<=.8 for steps in run['items'][0]['chunks']))


if __name__ == '__main__': unittest.main()
