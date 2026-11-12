import time
import unittest
import test_air_controller as base


class TubeWorkflowTests(unittest.TestCase):
    setUp = base.AirControllerTest.setUp
    tearDown = base.AirControllerTest.tearDown
    run_action = base.AirControllerTest.run_action

    def action(self, operation, **extra):
        return self.run_action(dict(action='tubes', operation=operation, **extra))

    def wait_prompt(self):
        deadline=time.monotonic()+2
        while self.c.busy and not self.c.prompt and time.monotonic()<deadline: time.sleep(.005)
        self.assertIsNotNone(self.c.prompt)
        return self.c.prompt

    def test_refill_only_selected_sources_then_common_air(self):
        s=self.action('refill',sources=[13,14,15])
        self.assertEqual([13,14,15,16],[x['port'] for x in self.c.hardware.moves])
        self.assertEqual([13,14,15],s['tubes']['primed_ports'])
        self.assertTrue(s['tubes']['common_air_seen'])
        self.assertFalse(s['sources']['11']['primed'])
        self.assertNotEqual('air_reset_verified',s['common'])

    def test_first_refill_observation_not_inferred_from_motor_completion(self):
        self.c.start(dict(action='tubes',operation='refill',sources=[13]))
        p=self.wait_prompt()
        self.assertEqual([13],[x['port'] for x in self.c.hardware.moves])
        self.assertFalse(self.c.sources['13']['primed'])
        self.c.acknowledge(dict(id=p['id'],ok=False)); self.c.worker.join(2)
        self.assertFalse(self.c.connected)
        self.assertFalse(self.c.sources['13']['primed'])

    def test_invalid_selected_ports_do_not_move(self):
        for op,fields in [('refill',{'sources':[]}),('refill',{'sources':[13,13]}),('refill',{'sources':[1]}),('drain_samples',{'samples':[True]}),('finish',{'sources':[13],'samples':[7]})]:
            self.action(op,**fields)
        self.assertEqual([],self.c.hardware.moves)

    def test_source_drain_requires_tips_lifted_before_any_motion(self):
        self.c.start(dict(action='tubes',operation='drain_sources',sources=[13,14]))
        p=self.wait_prompt(); self.assertIn('提出液面',p['message'])
        self.assertEqual([],self.c.hardware.moves)
        self.c.acknowledge(dict(id=p['id'],ok=False)); self.c.worker.join(2)
        self.assertEqual([],self.c.hardware.moves)

    def test_sample_drain_requires_tips_in_waste_and_never_aspirates_samples(self):
        s=self.action('drain_samples',samples=[1,3])
        moves=self.c.hardware.moves
        self.assertEqual([16,1,3], [x['port'] for x in moves])
        self.assertTrue(all(x['direction']=='dispense' for x in moves if x['port']<7))
        self.assertEqual([1,3],s['tubes']['sample_drained'])
        self.assertEqual([],s['tubes']['source_drained'])

    def test_finish_drains_selected_only_and_never_claims_dry(self):
        s=self.action('finish',sources=[13,14,15],samples=[1])
        self.assertEqual([13,14,15,16,1],[x['port'] for x in self.c.hardware.moves])
        self.assertEqual([13,14,15],s['tubes']['source_drained'])
        self.assertEqual([1],s['tubes']['sample_drained'])
        self.assertFalse(s['tubes']['fully_dry_verified'])
        self.assertEqual([],s['tubes']['primed_ports'])

    def test_old_recipe_is_retired_even_with_water_checkbox(self):
        for water_only in (True,False):
            self.action('water_recipe',water_only=water_only)
            self.assertIn('已停用',self.c.message)
            self.assertIn('recipe',self.c.message)
        self.assertEqual([],self.c.hardware.moves)

    def test_old_recipe_remains_retired_after_refill(self):
        self.action('refill',sources=[13,14,15]); before=len(self.c.hardware.moves)
        s=self.action('water_recipe',water_only=True)
        moves=self.c.hardware.moves[before:]
        self.assertEqual([],moves)
        self.assertIn('已停用',self.c.message)

    def test_unrelated_manual_action_invalidates_trial_readiness(self):
        self.action('refill',sources=[13,14,15])
        self.run_action(dict(action='select_port',port=16))
        self.assertFalse(self.c.snapshot()['tubes']['common_air_seen'])

    def test_reconnect_does_not_restore_tube_observations(self):
        self.action('refill',sources=[13])
        self.c.disconnect(); self.c.connect()
        self.assertEqual([],self.c.snapshot()['tubes']['primed_ports'])


if __name__=='__main__': unittest.main()
