import unittest
from pathlib import Path
import test_air_controller as base
from motion_planning import motion_plan, analyze_bench_readings

class BenchTests(unittest.TestCase):
    setUp=base.AirControllerTest.setUp
    tearDown=base.AirControllerTest.tearDown
    run_action=base.AirControllerTest.run_action
    def ready_bench(self):
        self.c.config['reset']['validated']=False
        self.run_action({'action':'bench','operation':'fill_water'})
        self.run_action({'action':'bench','operation':'water_seen'})
        self.run_action({'action':'bench','operation':'purge'})
        self.run_action({'action':'bench','operation':'air_seen'})
    def test_planning_geometry_native_turns_and_time(self):
        p=motion_plan(self.c.config)
        self.assertAlmostEqual(7.31865,p['volumes_ml']['common'],places=4)
        self.assertEqual(20,p['recipes']['first_flush']['turns'])
        self.assertEqual(24,p['times_s']['first_flush'])
        self.assertEqual(15,p['times_s']['purge'])
        self.assertFalse(p['physical_high_speed_verified'])
    def test_user_bulk_water_reference_is_default_without_granting_delivery_validation(self):
        r=self.c.snapshot()['bulk_water_reference']['report']
        self.assertEqual([4.61,4.55,4.57],r['readings_g'])
        self.assertAlmostEqual(13.73/15,r['mean_ml_per_turn'])
        self.assertFalse(r['formal_calibration_validated'])
        self.assertFalse(self.c.bench['water_ready'])
    def test_optional_bulk_recalibration_persists_across_restart(self):
        self.c.bench_report({'method':'bulk_turns','readings_g':[4.5,4.5,4.5],'density_g_ml':1.,'balance_resolution_g':.01,'turns':5})
        from controller import Controller
        restarted=Controller(self.tmp.name)
        self.assertAlmostEqual(.9,restarted.snapshot()['bulk_water_reference']['report']['mean_ml_per_turn'])
        self.assertFalse(restarted.bench['water_ready'])
        self.assertFalse(restarted.config['reset']['validated'])
    def test_saved_single_group_is_not_three_group_calibration(self):
        ref=self.c.snapshot()['water_dose_reference']
        self.assertEqual([],ref['readings_g'])
        self.assertAlmostEqual(.15,ref['mean_per_dose_ml'])
        self.assertEqual(0,ref['groups'])
        self.assertEqual([1.02],ref['previous_reference']['readings_g'])
        self.assertEqual(45,ref['parameters']['aspirate_steps'])
        self.assertIsNone(ref['group_rsd_percent'])
        self.assertFalse(ref['formal_calibration_validated'])
    def test_new_group_calibration_reference_survives_restart(self):
        self.c.bench_report({'method':'batch_water','readings_g':[1.,1.01,.99],'density_g_ml':1.,'balance_resolution_g':.01,'doses_per_group':5,'aspirate_steps':45,'chase_steps':1000,'aspirate_rpm':5,'chase_rpm':15,'sample':1})
        from controller import Controller
        ref=Controller(self.tmp.name).snapshot()['water_dose_reference']
        self.assertEqual([1.,1.01,.99],ref['readings_g'])
        self.assertEqual(15,ref['parameters']['chase_rpm'])
        self.assertAlmostEqual(.2,ref['mean_per_dose_ml'])
        self.assertFalse(ref['formal_calibration_validated'])
    def test_first_calibration_has_no_formal_calibration_deadlock(self):
        self.ready_bench()
        s=self.run_action({'action':'bench','operation':'dose_water','sample':1,'count':1,'aspirate_steps':45,'chase_steps':300})
        self.assertTrue(s['bench']['last_run']['completed'])
        self.assertFalse(s['config']['reset']['validated'])
        self.assertEqual(0,s['samples']['1']['dose_count'])
        self.assertFalse(s['bench']['air_ready'])
    def test_no_observation_can_be_claimed_before_actual_step(self):
        s=self.run_action({'action':'bench','operation':'water_seen'})
        self.assertFalse(s['bench']['water_ready'])
        self.assertEqual([],self.c.hardware.moves)
    def test_long_chase_is_finite_and_timeout_scales(self):
        self.ready_bench()
        s=self.run_action({'action':'bench','operation':'dose_water','sample':1,'count':1,'aspirate_steps':45,'chase_steps':3000})
        self.assertTrue(s['bench']['last_run']['completed'])
        pulse=[e for e in s['logs'] if e['event']=='pulse_completed' and e.get('port')==1][-1]
        self.assertEqual(3000,pulse['steps'])
        self.assertEqual(10,pulse['rpm'])
        self.assertEqual(118,pulse['timeout_s'])
        self.assertEqual([15,1,16],[m['port'] for m in self.c.hardware.moves[-3:]])
    def test_chase_above_new_limit_rejected_before_any_motion(self):
        self.ready_bench();before=len(self.c.hardware.moves)
        self.run_action({'action':'bench','operation':'dose_water','sample':1,'count':1,'aspirate_steps':45,'chase_steps':3001})
        self.assertEqual(before,len(self.c.hardware.moves))
    def test_adjustable_speeds_are_executed_recorded_and_bound_to_observation(self):
        self.ready_bench()
        payload={'action':'bench','operation':'dose_water','sample':1,'count':1,'aspirate_steps':45,'chase_steps':1200,'aspirate_rpm':8,'chase_rpm':30}
        s=self.run_action(payload)
        self.assertEqual([8,30,60],[m['rpm'] for m in self.c.hardware.moves[-3:]])
        self.assertEqual(30,s['bench']['last_run']['chase_rpm'])
        self.run_action({'action':'bench','operation':'dose_seen'})
        self.run_action({'action':'bench','operation':'air_seen'})
        before=len(self.c.hardware.moves)
        self.run_action(dict(payload,count=5,chase_rpm=40))
        self.assertEqual(before,len(self.c.hardware.moves))
    def test_invalid_speed_or_overlong_duration_rejected_before_absorbing(self):
        self.ready_bench();before=len(self.c.hardware.moves)
        for extra in ({'chase_rpm':101},{'aspirate_rpm':0},{'chase_rpm':.1,'chase_steps':3000}):
            self.run_action({'action':'bench','operation':'dose_water','sample':1,'count':1,'aspirate_steps':45,'chase_steps':300,**extra})
        self.assertEqual(before,len(self.c.hardware.moves))
    def test_explicit_skip_records_operator_state_without_pump_motion(self):
        self.c.config['reset']['validated']=False
        before=len(self.c.hardware.moves)
        s=self.run_action({'action':'bench','operation':'skip_fill'})
        self.assertTrue(s['bench']['water_ready'])
        self.assertNotEqual('wet',s['common'])
        s=self.run_action({'action':'bench','operation':'skip_purge'})
        self.assertTrue(s['bench']['air_ready'])
        self.assertEqual('air_unverified',s['common'])
        self.assertEqual(before,len(self.c.hardware.moves))
        self.assertFalse(s['config']['reset']['validated'])
        self.assertEqual('skip_purge',s['bench']['last_run']['operation'])
    def test_five_dose_group_requires_successful_observed_single_same_route(self):
        self.ready_bench();before=len(self.c.hardware.moves)
        self.run_action({'action':'bench','operation':'dose_water','sample':1,'count':5,'aspirate_steps':45,'chase_steps':300})
        self.assertEqual(before,len(self.c.hardware.moves))
        self.run_action({'action':'bench','operation':'dose_water','sample':1,'count':1,'aspirate_steps':45,'chase_steps':300})
        self.run_action({'action':'bench','operation':'dose_seen'})
        self.run_action({'action':'bench','operation':'air_seen'})
        before=len(self.c.hardware.moves)
        self.run_action({'action':'bench','operation':'dose_water','sample':2,'count':5,'aspirate_steps':45,'chase_steps':300})
        self.assertEqual(before,len(self.c.hardware.moves))
        s=self.run_action({'action':'bench','operation':'dose_water','sample':1,'count':5,'aspirate_steps':45,'chase_steps':300})
        self.assertEqual(6,s['bench']['sample_counts']['1'])
        self.assertEqual([15,1,16]*5,[m['port'] for m in self.c.hardware.moves[before:]])
    def test_coarse_balance_report_is_batch_mean_not_single_dose_validation(self):
        payload={'method':'batch_water','readings_g':[1.,1.01,.99],'density_g_ml':1.,'balance_resolution_g':.01,'doses_per_group':5,'aspirate_steps':45,'chase_steps':300,'sample':1}
        report=analyze_bench_readings(payload)
        self.assertAlmostEqual(.2,report['mean_per_dose_ml'])
        self.assertEqual(34,report['suggested_aspirate_steps'])
        self.assertFalse(report['individual_repeatability_verified'])
        self.assertFalse(report['formal_calibration_validated'])
        self.assertEqual(3,report['groups'])
        result=self.c.bench_report(payload)
        self.assertTrue(Path(result['saved_file']).exists())
        self.assertTrue(self.c.connected)
    def test_no_blind_step_scaling_for_zero_or_missing_delivery(self):
        for readings in [[0,0,0],[.01,.02,.01]]:
            r=analyze_bench_readings({'method':'batch_water','readings_g':readings,'density_g_ml':1.,'balance_resolution_g':.01,'doses_per_group':5,'aspirate_steps':45})
            self.assertIsNone(r['suggested_aspirate_steps'])
    def test_bulk_turn_readings_calculate_water_only(self):
        r=analyze_bench_readings({'method':'bulk_turns','readings_g':[4.5,4.5,4.5],'density_g_ml':1.,'balance_resolution_g':.01,'turns':5})
        self.assertAlmostEqual(.9,r['mean_ml_per_turn'])
        self.assertFalse(r['formal_calibration_validated'])
    def test_new_015_target_keeps_45_steps_for_075g_groups(self):
        r=analyze_bench_readings({'method':'batch_water','readings_g':[.75,.75,.75],
                                'density_g_ml':1.,'doses_per_group':5,'aspirate_steps':45})
        self.assertAlmostEqual(.15,r['target_ml'])
        self.assertEqual(45,r['suggested_aspirate_steps'])
        self.assertEqual(0,r['deviation_percent'])
        self.assertTrue(r['mean_tuning_within_5_percent'])
    def test_other_fluid_action_invalidates_bench_readiness(self):
        self.ready_bench();self.run_action({'action':'wash'})
        before=len(self.c.hardware.moves)
        s=self.run_action({'action':'bench','operation':'dose_water','sample':1,'count':1,'aspirate_steps':45,'chase_steps':300})
        self.assertEqual(before,len(self.c.hardware.moves))
        self.assertFalse(s['bench']['air_ready'])
