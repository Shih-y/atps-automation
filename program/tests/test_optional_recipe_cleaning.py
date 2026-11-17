import copy
import threading
import unittest
import test_air_controller as base


class OptionalCleaningTests(unittest.TestCase):
    setUp=base.AirControllerTest.setUp
    tearDown=base.AirControllerTest.tearDown

    def run_recipe(self,clean=True):
        self.c.config['free_debug']=True
        self.c.start(dict(action='recipe',sample=2,items=[dict(source=13,volume_ml=.3)],rinse_after=clean))
        self.c.worker.join(5)
        self.assertFalse(self.c.busy)
        return self.c.recipe_run

    def test_cleaning_rinses_then_purges_toward_waste_without_claiming_dry(self):
        run=self.run_recipe()
        self.assertEqual([13,2,2,15,16],[m['port'] for m in self.c.hardware.moves])
        self.assertEqual(['aspirate','aspirate'],[m['direction'] for m in self.c.hardware.moves[-2:]])
        self.assertEqual(dict(port=16,direction='aspirate',turns=30,rpm=60),self.c.hardware.moves[-1])
        self.assertTrue(run['rinse_performed'] and run['purge_performed'] and run['completed'])
        self.assertEqual(5,run['completed_commands'])
        self.assertEqual('air_unverified',self.c.common)
        self.assertFalse(self.c.tubes['fully_dry_verified'])

    def test_no_cleaning_skips_both_rinse_and_air(self):
        run=self.run_recipe(False)
        self.assertEqual([13,2,2],[m['port'] for m in self.c.hardware.moves])
        self.assertFalse(run['rinse_after'] or run['purge_after'])
        self.assertEqual('wet',self.c.common)

    def test_every_manual_source_uses_native_30_turns_at_60_rpm(self):
        for source in range(10,16):
            plan=self.c._volume_plan(dict(source=source,sample=1,volume_ml=.2))
            self.assertEqual(dict(turns=30,rpm=60,timeout_s=46),plan['parameters'][0]['chase'])

    def test_old_source_reference_preserved_but_motion_change_is_explicit(self):
        data=dict(source=13,sample=2,doses=1,aspirate=dict(steps=40,rpm=5,timeout_s=20),
                  chase=dict(steps=900,rpm=15,timeout_s=35),groups_ml=[.2],temperature_c=25)
        ref=self.c.save_source_volume_calibration(data)['reference'];before=copy.deepcopy(ref)
        plan=self.c._volume_plan(dict(source=13,sample=2,volume_ml=.2))
        self.assertEqual(30,plan['parameters'][0]['chase'].get('turns'))
        self.assertEqual(before,plan['reference'])
        self.assertEqual(before,self.c.source_volume_calibrations[-1])
        self.assertTrue(plan['delivery_reverification_required'])
        self.assertTrue(plan['chase_conditions_changed'])

    def test_batch_cleaning_choice_applies_to_every_bottle(self):
        body=dict(recipes=[dict(sample=1,items=[dict(source=13,volume_ml=.2)]),
                           dict(sample=2,items=[dict(source=15,volume_ml=.2)])],rinse_after=False)
        p=self.c.preview_manual_batch(body)['plan']
        self.assertTrue(all(not r['rinse_after'] and not r['purge_after'] for r in p['recipes']))
        body['rinse_after']=True
        p=self.c.preview_manual_batch(body)['plan']
        self.assertTrue(all(r['rinse_after'] and r['purge_after'] for r in p['recipes']))

    def test_bad_batch_cleaning_flag_rejected_before_motion(self):
        with self.assertRaises(ValueError):
            self.c.preview_manual_batch(dict(recipes=[dict(sample=1,items=[dict(source=15,volume_ml=.2)])],rinse_after='yes'))
        self.assertEqual([],self.c.hardware.moves)

    def test_stop_during_purge_does_not_start_next_bottle_or_claim_dry(self):
        self.c.config['free_debug']=True
        entered=threading.Event();original=self.c.hardware.move_turns
        def block(direction,turns,rpm,event):
            if self.c.hardware.port==16:
                entered.set();event.wait(3);self.c.hardware._check(event)
            return original(direction,turns,rpm,event)
        self.c.hardware.move_turns=block
        self.c.start(dict(action='batch_recipe',request_id='purge-stop',rinse_after=True,recipes=[
            dict(sample=1,items=[dict(source=13,volume_ml=.2)]),dict(sample=2,items=[dict(source=14,volume_ml=.2)])]))
        reached=entered.wait(2)
        if self.c.busy:self.c.stop_keep_connection()
        self.c.worker.join(3)
        self.assertTrue(reached,'cleanup did not execute a port16 purge')
        self.assertTrue(self.c.manual_batch_run['cancelled'])
        self.assertFalse(self.c.manual_batch_run['completed'])
        self.assertNotIn(14,[m['port'] for m in self.c.hardware.moves])
        self.assertFalse(self.c.tubes['fully_dry_verified'])
