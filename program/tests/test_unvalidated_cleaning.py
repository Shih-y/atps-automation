"""Unvalidated manual cleaning must operate without granting quantitative readiness."""
import unittest
import test_air_controller as base

class CleaningTrialTest(unittest.TestCase):
    setUp=base.AirControllerTest.setUp
    tearDown=base.AirControllerTest.tearDown
    run_action=base.AirControllerTest.run_action
    def unvalidated(self):
        self.c.config['reset'].update(validated=False,covers_initial_air_filled_water_branch=False,evidence='')
        for key in ('wash','purge'):self.c.config['reset'][key]={'steps':300,'rpm':5,'timeout_s':30}
    def test_manual_wash_runs_without_certifying_water_branch(self):
        self.unvalidated();s=self.run_action({'action':'wash'})
        self.assertEqual([15],[x['port'] for x in self.c.hardware.moves])
        self.assertEqual('washed_unverified',s['reset_sequence'])
        self.assertFalse(s['sources']['15']['primed'])
        self.assertFalse(s['config']['reset']['validated'])
    def test_manual_wash_then_purge_remains_unverified(self):
        self.unvalidated();self.run_action({'action':'wash'});s=self.run_action({'action':'purge'})
        self.assertEqual([15,16],[x['port'] for x in self.c.hardware.moves])
        self.assertEqual('air_unverified',s['common'])
        self.assertEqual('purged_unverified',s['reset_sequence'])
        self.run_action({'action':'confirm_reset'})
        self.assertNotEqual('air_reset_verified',self.c.common)
    def test_manual_combined_trial_is_serial_and_does_not_grant_ready(self):
        self.unvalidated();s=self.run_action({'action':'reset'})
        self.assertEqual([15,16],[x['port'] for x in self.c.hardware.moves])
        self.assertEqual('air_unverified',s['common'])
    def test_internal_quantitative_reset_still_requires_evidence(self):
        self.unvalidated()
        with self.assertRaises(ValueError):self.c._reset()
        self.assertEqual([],self.c.hardware.moves)
    def test_trial_rejects_excessive_speed_before_motion(self):
        self.unvalidated();self.c.config['reset']['wash']['rpm']=101
        s=self.run_action({'action':'wash'})
        self.assertEqual([],self.c.hardware.moves)
        self.assertIn('操作未执行',s['message'])
    def test_air_only_commission_accepts_bounded_ten_rpm(self):
        self.unvalidated()
        s=self.run_action({'action':'commission','port':16,'direction':'aspirate','pulse':{'steps':500,'rpm':10,'timeout_s':30}})
        self.assertEqual(1,len(self.c.hardware.moves))
        self.assertEqual(16,self.c.hardware.moves[0]['port'])
        self.assertNotEqual('air_reset_verified',s['common'])
    def test_air_only_manual_purge_accepts_bounded_ten_rpm(self):
        self.unvalidated();self.c.config['reset']['purge']={'steps':500,'rpm':10,'timeout_s':30}
        s=self.run_action({'action':'purge'})
        self.assertEqual(1,len(self.c.hardware.moves))
        self.assertEqual('air_unverified',s['common'])
    def test_manual_moves_still_reject_overall_speed_budget(self):
        for port,direction in [(15,'aspirate'),(1,'dispense'),(16,'aspirate')]:
            with self.subTest(port=port):
                self.run_action({'action':'commission','port':port,'direction':direction,'pulse':{'steps':500,'rpm':100.1,'timeout_s':30}})
                self.assertEqual([],self.c.hardware.moves)
    def test_no_per_connection_twenty_pulse_gate(self):
        self.c.commission_count=20
        self.run_action({'action':'commission','port':16,'direction':'aspirate','pulse':{'steps':10,'rpm':10,'timeout_s':5}})
        self.assertEqual(1,len(self.c.hardware.moves))
    def test_unvalidated_source_prime_can_follow_observed_trial_purge(self):
        self.unvalidated();self.run_action({'action':'purge'})
        s=self.run_action({'action':'prime','source':14,'pulse':{'steps':20,'rpm':2,'timeout_s':10}})
        self.assertTrue(s['sources']['14']['primed'])
        self.assertNotEqual('air_reset_verified',s['common'])
        self.assertFalse(s['config']['reset']['validated'])
