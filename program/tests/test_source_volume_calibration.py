import unittest
import test_air_controller as base
from types import SimpleNamespace
from experiment_domain import default_stocks

class SourceVolumeTests(unittest.TestCase):
    setUp=base.AirControllerTest.setUp
    tearDown=base.AirControllerTest.tearDown

    def payload(self, **changes):
        if not hasattr(self.c,'lab'): self.c.lab=SimpleNamespace(data=dict(stocks=default_stocks()))
        data=dict(source=14,sample=2,doses=1,aspirate=dict(steps=40,rpm=5,timeout_s=20),
                  chase=dict(steps=900,rpm=15,timeout_s=35),groups_ml=[.2],temperature_c=25)
        data.update(changes)
        return data

    def save(self,**changes):return self.c.save_source_volume_calibration(self.payload(**changes))['reference']

    def test_exact_duration_boundary_does_not_round_up_to_121_seconds(self):
        self.save(aspirate=dict(steps=1000,rpm=18.9,timeout_s=30),groups_ml=[.1],
                  min_steps=1,max_steps=6000)
        plan=self.c.preview_source_volume(dict(source=14,sample=2,volume_ml=.5775))['plan']
        self.assertEqual([5775],plan['chunks'])
        self.assertEqual(120,plan['parameters'][0]['aspirate']['timeout_s'])

    def test_save_persistent_versioned_without_motion(self):
        a=self.save();b=self.save()
        self.assertNotEqual(a['id'],b['id']);self.assertEqual(2,b['version'])
        self.assertEqual([],self.c.hardware.moves)
        from controller import Controller
        self.assertEqual(a,Controller(self.tmp.name).snapshot()['source_volume_calibrations'][0])

    def test_source_reference_scales_with_frozen_chase(self):
        ref=self.save(min_steps=10,max_steps=240)
        p=self.c.preview_source_volume(dict(source=14,sample=2,volume_ml=1.))['plan']
        self.assertEqual([200],p['chunks']);self.assertEqual(ref['id'],p['reference']['id'])
        self.assertEqual([900],[q['chase']['steps'] for q in p['parameters']])
        self.assertEqual('source_endpoint_trial',p['calibration_basis']);self.assertFalse(p['volume_verified'])
        self.assertEqual([],self.c.hardware.moves)

    def test_default_range_is_measured_dose(self):
        self.save()
        self.assertEqual([40],self.c._volume_plan(dict(source=14,sample=2,volume_ml=.2))['chunks'])
        with self.assertRaises(ValueError):self.c._volume_plan(dict(source=14,sample=2,volume_ml=.3))

    def test_stale_ref_never_falls_back_unrelated_stock_is_ok(self):
        self.save();stocks=self.c.lab.data['stocks'];stocks[0]['version']+=1
        self.c._volume_plan(dict(source=14,sample=2,volume_ml=.2))
        stock=next(s for s in stocks if s['port']==14)
        for field,value in [('batch','new'),('version',2),('name','new'),('temperature_c',26)]:
            old=stock[field];stock[field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'失效'):
                self.c._volume_plan(dict(source=14,sample=2,volume_ml=.2))
            stock[field]=old
        with self.assertRaises(ValueError):self.c._volume_plan(dict(source=14,sample=1,volume_ml=.2))
        self.c.config['pump']['aspirate_direction']='cw'
        with self.assertRaisesRegex(ValueError,'失效'):self.c._volume_plan(dict(source=14,sample=2,volume_ml=.2))

    def test_invalid_readings_scope_and_range_rejected(self):
        for changes in [dict(groups_ml=[]),dict(groups_ml=[float('nan')]),dict(groups_ml=[0]),dict(doses=True),dict(min_steps=60,max_steps=20),dict(sample_bottles=[1,2]),dict(temperature_c=24)]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.save(**changes)

    def test_grams_need_explicit_current_trustworthy_density(self):
        data=self.payload();data.pop('groups_ml');data.update(groups_g=[.24])
        with self.assertRaisesRegex(ValueError,'密度'):self.c.save_source_volume_calibration(data)
        stock=next(s for s in self.c.lab.data['stocks'] if s['port']==14)
        stock.update(density_g_ml=1.2,density_basis='measured',density_evidence='batch measured',density_temperature_c=25,density_mass_fraction=.4,density_batch='')
        with self.assertRaisesRegex(ValueError,'密度'):self.c.save_source_volume_calibration(data)
        data.update(density_g_ml=1.2,density_evidence='operator confirmation')
        ref=self.c.save_source_volume_calibration(data)['reference']
        self.assertAlmostEqual(.005,ref['ml_per_step']);self.assertFalse(ref['repeatability_verified'])

    def test_legacy_is_visibly_unvalidated(self):
        p=self.c._volume_plan(dict(source=13,sample=1,volume_ml=.3))
        self.assertEqual('legacy_water_unvalidated',p['calibration_basis'])

    def test_independent_bottle_refs_remain_available(self):
        first=self.save();self.save(sample=1)
        p=self.c._volume_plan(dict(source=14,sample=2,volume_ml=.2))
        self.assertEqual(first['id'],p['reference']['id'])

    def test_pending_motion_and_changed_collection_version_rejected(self):
        self.payload()
        self.c.pending_plug={'id':'pending'}
        with self.assertRaises(ValueError):self.save()
        self.c.pending_plug=None
        with self.assertRaises(ValueError):self.save(stock_version=999)

    def test_collection_id_must_match_completed_unchanged_collection(self):
        data=self.payload();data['collection_run_id']='missing'
        with self.assertRaises(ValueError):self.c.save_source_volume_calibration(data)

    def test_atomic_save_failure_keeps_previous_history(self):
        from unittest.mock import patch
        first=self.save()
        with patch('source_volume_calibration.os.replace',side_effect=OSError('disk error')):
            with self.assertRaises(OSError):self.save()
        self.assertEqual([first],self.c.snapshot()['source_volume_calibrations'])
        from controller import Controller
        self.assertEqual([first],Controller(self.tmp.name).snapshot()['source_volume_calibrations'])

    def test_source_plan_used_in_actual_fake_recipe_without_segment_cap(self):
        self.save(min_steps=1,max_steps=400)
        del self.c.lab
        self.c.config['free_debug']=True
        self.c.start(dict(action='volume',source=14,sample=2,volume_ml=1.6));self.c.worker.join(5)
        self.assertEqual([320],[m['steps'] for m in self.c.hardware.moves if m['port']==14])
        self.assertEqual([900],[m['steps'] for m in self.c.hardware.moves if m['port']==2])
        self.assertTrue(self.c.volume_run['completed'])

    def test_saved_reference_does_not_alias_operator_payload(self):
        data=self.payload(sample_bottles=[2])
        ref=self.c.save_source_volume_calibration(data)['reference']
        data['sample_bottles'].append(3)
        self.assertEqual([2],self.c.snapshot()['source_volume_calibrations'][0]['sample_bottles'])
        self.assertEqual([2],ref['sample_bottles'])

    def test_completed_collection_binding_and_changed_geometry(self):
        data=self.payload()
        plan=self.c._stock_calibration_plan(data)
        self.c.recipe_run=plan
        data['collection_run_id']=plan['id']
        with self.assertRaises(ValueError):self.c.save_source_volume_calibration(data)
        plan['completed']=True
        ref=self.c.save_source_volume_calibration(data)['reference']
        self.assertEqual('completed_controller_collection',ref['collection_basis'])
        self.c.config['geometry']['source_cm']['14']+=1
        with self.assertRaises(ValueError):self.c.save_source_volume_calibration(data)

    def test_slow_single_aspiration_fails_instead_of_splitting(self):
        self.save(aspirate=dict(steps=10,rpm=.1,timeout_s=40),groups_ml=[.1],min_steps=1,max_steps=80)
        with self.assertRaisesRegex(ValueError,'超时'):
            self.c.preview_source_volume(dict(source=14,sample=2,volume_ml=.8))

    def test_native_turn_chase_calibration_supersedes_peg_default_without_double_scaling(self):
        ref=self.save(aspirate=dict(steps=714,rpm=5,timeout_s=62),
                      chase=dict(turns=30,rpm=60,timeout_s=46),groups_ml=[1.],min_steps=1,max_steps=1500)
        p=self.c._volume_plan(dict(source=14,sample=2,volume_ml=1.))
        self.assertEqual([714],p['chunks'])
        self.assertEqual(ref['chase'],p['parameters'][0]['chase'])
        self.assertEqual('source_endpoint_trial',p['calibration_basis'])
        collection=self.c._stock_calibration_plan(self.payload(aspirate=ref['aspirate'],chase=ref['chase']))
        self.assertEqual(ref['chase'],collection['items'][0]['parameters'][0]['chase'])

    def test_native_chase_timeout_still_validated(self):
        with self.assertRaisesRegex(ValueError,'超时'):
            self.save(chase=dict(turns=30,rpm=60,timeout_s=10))
