import unittest
import test_air_controller as base

class FreeDebugTests(unittest.TestCase):
    setUp=base.AirControllerTest.setUp
    tearDown=base.AirControllerTest.tearDown
    def run_free(self,payload):
        self.c.config['free_debug']=True
        self.c.config['profiles']=[]
        self.c._ask=lambda *a,**k: self.fail('Free debugging must not ask for workflow prerequisites')
        self.c.start(payload); self.c.worker.join(5)
        self.assertFalse(self.c.busy)
        self.assertNotEqual('FAULT',self.c.state,self.c.message)
        self.assertFalse(self.c.message.startswith('操作未执行'),self.c.message)
        return self.c.snapshot()

    def test_refill_six_turns_then_direct_purge_without_false_observation(self):
        s=self.run_free(dict(action='tubes',operation='refill',sources=[13,14,15]))
        self.assertEqual([13,14,15,16],[m['port'] for m in self.c.hardware.moves])
        self.assertEqual([6,6,6,15],[m['turns'] for m in self.c.hardware.moves])
        self.assertEqual([],s['tubes']['primed_ports'])
        self.assertFalse(s['tubes']['common_air_seen'])
        self.assertFalse(s['sources']['13']['primed'])

    def test_bench_single_and_group_allow_unknown_pipe_state(self):
        for count in (1,5):
            s=self.run_free(dict(action='bench',operation='dose_water',sample=1,count=count,aspirate_steps=45,chase_steps=1000,aspirate_rpm=5,chase_rpm=15))
            self.assertEqual(count,s['bench']['last_run']['completed_doses'])

    def test_bulk_collection_does_not_require_fill_observation(self):
        self.run_free(dict(action='bench',operation='collect_water'))
        self.assertEqual(5,self.c.hardware.moves[-1]['turns'])

    def test_chase_does_not_require_previous_aspiration_or_profile(self):
        self.run_free(dict(action='chase',debug_parameters=dict(source=14,sample=2)))
        self.assertEqual((2,'dispense'),(self.c.hardware.moves[-1]['port'],self.c.hardware.moves[-1]['direction']))

    def test_aspiration_then_different_step_unlocked(self):
        self.run_free(dict(action='aspirate',debug_parameters=dict(source=14,sample=1)))
        self.run_free(dict(action='select_port',port=13))
        self.run_free(dict(action='aspirate',debug_parameters=dict(source=13,sample=2)))
        self.assertEqual([14,13],[m['port'] for m in self.c.hardware.moves])
        self.assertIsNone(self.c.pending_plug)

    def test_dose_and_bubble_without_prior_state(self):
        self.run_free(dict(action='dose',debug_parameters=dict(source=14,sample=2)))
        self.run_free(dict(action='bubble',debug_parameters=dict(sample=3)))
        self.assertEqual([14,2,3],[m['port'] for m in self.c.hardware.moves])

    def test_prime_is_one_finite_pulse_no_observation_loop(self):
        self.run_free(dict(action='prime',source=13,pulse=dict(steps=20,rpm=2,timeout_s=20)))
        self.assertEqual(1,len(self.c.hardware.moves))

    def test_old_water_recipe_is_explicitly_retired_without_motion(self):
        self.c.config['free_debug']=True
        self.c.start(dict(action='tubes',operation='water_recipe'));self.c.worker.join(2)
        self.assertEqual([],self.c.hardware.moves)
        self.assertIn('已停用',self.c.message)
        self.assertIn('recipe',self.c.message)

    def test_finish_has_no_prompts_or_fake_dry_claim(self):
        s=self.run_free(dict(action='tubes',operation='finish',sources=[13],samples=[1]))
        self.assertEqual([13,16,1],[m['port'] for m in self.c.hardware.moves])
        self.assertFalse(s['tubes']['fully_dry_verified'])
        self.assertEqual([],s['tubes']['source_drained'])

    def test_old_queue_is_retired_and_directs_operator_to_single_bottle_recipe(self):
        self.c.config['free_debug']=True
        self.c.start(dict(action='run',items=[dict(debug_parameters=dict(source=13,sample=1))]));self.c.worker.join(2)
        self.assertEqual([],self.c.hardware.moves)
        self.assertIn('已停用',self.c.message)
        self.assertIn('recipe',self.c.message)

    def test_invalid_pulse_still_rejected_before_motion(self):
        self.c.config['free_debug']=True
        self.c.start(dict(action='dose',debug_parameters=dict(chase=dict(steps=1000,rpm=101,timeout_s=35))))
        self.c.worker.join(2)
        self.assertEqual([],self.c.hardware.moves)
        self.assertIn('操作未执行',self.c.message)

    def test_specified_volume_uses_015_reference_and_selected_route(self):
        s=self.run_free(dict(action='volume',source=14,sample=3,volume_ml=.5))
        moves=self.c.hardware.moves
        self.assertEqual([14,3,16],[m['port'] for m in moves])
        self.assertEqual([150],[m['steps'] for m in moves if m['port']==14])
        self.assertEqual(150,s['volume_run']['total_steps'])
        self.assertAlmostEqual(.5,s['volume_run']['estimated_ml'])
        self.assertTrue(s['volume_run']['completed'])
        self.assertIsNone(s['volume_run']['actual_ml'])

    def test_volume_quantization_is_reported_and_too_small_rejected(self):
        s=self.run_free(dict(action='volume',source=13,sample=1,volume_ml=.151))
        self.assertAlmostEqual(.15,s['volume_run']['estimated_ml'])
        before=len(self.c.hardware.moves)
        self.c.start(dict(action='volume',source=13,sample=1,volume_ml=.001));self.c.worker.join(2)
        self.assertEqual(before,len(self.c.hardware.moves))
        self.assertIn('操作未执行',self.c.message)

    def test_volume_does_not_change_reference_or_other_targets(self):
        self.run_free(dict(action='volume',source=13,sample=6,volume_ml=.3))
        self.assertAlmostEqual(.15,self.c.water_dose_reference['mean_per_dose_ml'])
        self.assertEqual([13,6,16],[m['port'] for m in self.c.hardware.moves])

    def test_one_ml_uses_two_equal_portions_without_automatic_mixing(self):
        s=self.run_free(dict(action='volume',source=14,sample=1,volume_ml=1.))
        moves=self.c.hardware.moves
        self.assertEqual([14,1,14,1,16],[m['port'] for m in moves])
        self.assertEqual(150,moves[0]['steps'])
        self.assertEqual(1105,moves[1]['steps'])
        self.assertEqual([150,150],s['volume_run']['chunks'])
        self.assertFalse(s['volume_run']['mixing_performed'])

    def test_over_ten_ml_is_rejected_before_motion(self):
        self.c.config['free_debug']=True
        self.c.start(dict(action='volume',source=14,sample=1,volume_ml=10.001));self.c.worker.join(2)
        self.assertEqual([],self.c.hardware.moves)
        self.assertIn('操作未执行',self.c.message)

    def test_manual_direction_explicit_cw_and_ccw_on_selected_port(self):
        for direction,flow in [('cw','dispense'),('ccw','aspirate')]:
            self.run_free(dict(action='manual_motion',port=15,rotation=direction,turns=2,rpm=20))
            m=self.c.hardware.moves[-1]
            self.assertEqual((15,flow,2,20),(m['port'],m['direction'],m['turns'],m['rpm']))

    def test_manual_motion_accepts_reverse_on_sample_and_rejects_invalid_rotation(self):
        self.run_free(dict(action='manual_motion',port=1,rotation='ccw',turns=1,rpm=5))
        before=len(self.c.hardware.moves)
        self.c.start(dict(action='manual_motion',port=1,rotation='bad',turns=1,rpm=5));self.c.worker.join(2)
        self.assertEqual(before,len(self.c.hardware.moves))
        self.assertIn('操作未执行',self.c.message)

    def test_user_stop_keeps_same_hardware_connection_and_can_continue(self):
        self.run_free(dict(action='manual_motion',port=16,rotation='ccw',turns=1,rpm=5))
        h=self.c.hardware
        self.c.stop_keep_connection()
        self.assertIs(h,self.c.hardware)
        self.assertTrue(self.c.connected)
        self.assertFalse(self.c.cancel.is_set())
        self.run_free(dict(action='manual_motion',port=16,rotation='ccw',turns=1,rpm=5))
        self.assertEqual(2,len(h.moves))

    def test_user_stop_cancels_recipe_without_resuming_it(self):
        import threading,time
        self.c.config['free_debug']=True
        started=threading.Event();original=self.c.hardware.move
        def slow(direction,steps,rpm,event):
            started.set();event.wait(2);self.c.hardware._check(event)
            return original(direction,steps,rpm,event)
        self.c.hardware.move=slow
        self.c.start(dict(action='recipe',sample=1,items=[dict(source=13,volume_ml=.3)]*3))
        self.assertTrue(started.wait(1))
        self.c.stop_keep_connection()
        self.assertTrue(self.c.connected)
        self.assertFalse(self.c.worker.is_alive())
        self.assertFalse(self.c.busy)
        self.assertEqual([],self.c.hardware.moves)

    def test_failed_stop_confirmation_does_not_enable_motion(self):
        self.c.config['free_debug']=True
        def fail():raise RuntimeError('no stop readback')
        self.c.hardware.recover_after_stop=fail
        with self.assertRaises(Exception):self.c.stop_keep_connection()
        self.assertTrue(self.c.cancel.is_set())
        self.assertFalse(self.c.connected)

if __name__=='__main__':unittest.main()
