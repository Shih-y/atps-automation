"""Operator-requested manual method; fake hardware is not dose validation."""
import copy
import unittest
from unittest.mock import patch
import test_air_controller as base


class PegSingleShotTests(unittest.TestCase):
    setUp = base.AirControllerTest.setUp
    tearDown = base.AirControllerTest.tearDown

    def plan(self, source=14, ml=1.):
        return self.c._volume_plan(dict(source=source, sample=1, volume_ml=ml))

    def run_recipe(self, **extra):
        self.c.config['free_debug'] = True
        payload = dict(action='recipe', sample=1, items=[dict(source=14, volume_ml=1.), dict(source=13, volume_ml=.3)])
        self.c.start(dict(payload, **extra)); self.c.worker.join(5)
        self.assertFalse(self.c.busy)
        self.assertEqual('IDLE', self.c.state, self.c.message)
        self.assertNotIn('操作未执行', self.c.message)
        return self.c.recipe_run

    def test_peg_report_scales_original_steps_once_and_chases_native_turns(self):
        plan = self.plan()
        self.assertEqual([714], plan['chunks'])
        self.assertEqual(5, plan['parameters'][0]['aspirate']['rpm'])
        self.assertEqual(dict(turns=30, rpm=60, timeout_s=46), plan['parameters'][0]['chase'])
        self.assertAlmostEqual(.9996, plan['estimated_ml'])
        self.assertEqual('peg_operator_report_trial', plan['calibration_basis'])
        self.assertFalse(plan['volume_verified'])
        self.assertIsNone(plan['actual_ml'])
        self.c.water_dose_reference['mean_per_dose_ml'] = .2
        self.assertEqual([714], self.plan()['chunks'])

    def test_salt_and_water_no_longer_split_above_point_eight(self):
        for source in (10, 11, 12, 13, 15):
            for ml, steps in ((.801, 240), (1., 300), (1.6, 480), (2., 600)):
                with self.subTest(source=source, ml=ml):
                    plan = self.plan(source, ml)
                    self.assertEqual([steps], plan['chunks'])
                    self.assertEqual('single_shot', plan['delivery_mode'])

    def test_combined_recipe_has_one_pair_per_source_then_mix_and_water_no_air(self):
        run = self.run_recipe(purge_after=True)  # An older page must not restore air purge.
        moves = self.c.hardware.moves
        self.assertEqual([14, 1, 13, 1, 1, 15], [m['port'] for m in moves])
        self.assertEqual((30,60), (moves[1]['turns'], moves[1]['rpm']))
        self.assertEqual(('aspirate',328), (moves[-1]['direction'],moves[-1]['steps']))
        self.assertEqual(6,run['completed_commands'])
        self.assertEqual(2,run['completed_plugs'])
        self.assertFalse(run['purge_performed'])
        self.assertTrue(run['rinse_performed'])
        self.assertEqual('waste',run['rinse']['destination'])
        self.assertEqual(1.5,run['rinse']['requested_ml'])
        self.assertEqual('wet',self.c.common)
        self.assertEqual(run['mixing_finished_at'],self.c.samples['1']['last_gas_stop'])

    def test_standalone_volume_has_no_auto_rinse_or_air(self):
        self.c.config['free_debug']=True
        self.c.start(dict(action='volume',sample=1,source=14,volume_ml=1.)); self.c.worker.join(5)
        self.assertEqual([14,1],[m['port'] for m in self.c.hardware.moves])

    def test_rinse_failure_preserves_completed_delivery_without_claiming_clean(self):
        original=self.c.hardware.move
        def move(direction,steps,rpm,event):
            if self.c.selected_port==15:raise RuntimeError('rinse failed')
            return original(direction,steps,rpm,event)
        self.c.hardware.move=move
        self.c.config['free_debug']=True
        self.c.start(dict(action='recipe',sample=1,items=[dict(source=13,volume_ml=.3)])); self.c.worker.join(5)
        run=self.c.recipe_run
        self.assertEqual(3,run['completed_commands'])
        self.assertTrue(run['items'][0]['completed'])
        self.assertFalse(run['completed']); self.assertFalse(run['rinse_performed'])
        self.assertEqual('rinse',run['current']['phase'])

    def test_invalid_rinse_reference_fails_before_any_motion(self):
        self.c.bulk_water_reference['report']['mean_ml_per_turn']=0
        with self.assertRaises(ValueError):
            self.c._recipe_plan(dict(sample=1,items=[dict(source=13,volume_ml=.3)]))
        self.assertEqual([],self.c.hardware.moves)

    def test_duplicate_source_rows_merge_into_single_pair(self):
        run=self.run_recipe(items=[dict(source=13,volume_ml=.3),dict(source=13,volume_ml=.7)])
        self.assertEqual(1,len(run['items']))
        self.assertEqual([300],run['items'][0]['chunks'])

    def test_large_single_shot_cannot_silently_split_to_bypass_timeout(self):
        with self.assertRaisesRegex(ValueError,'超时|单次'):
            self.plan(14,3.)
        self.assertEqual([],self.c.hardware.moves)


if __name__=='__main__':unittest.main()
