"""Scientific coordinate and calibration provenance regressions; no hardware."""
import copy
import unittest
from experiment_domain import (composition_record, execution_key, make_calibration,
                               default_stocks, preview_recipe, plumbing_signature)


class IntegrityDomainTests(unittest.TestCase):
    def calibration(self, source=15, **extra):
        stock=next(s for s in default_stocks() if s['port']==source)
        data=dict(source=source,groups_g=[.75,.74,.76],doses_per_group=5,
                  aspirate_steps=45,aspirate_rpm=5,chase_steps=1000,chase_rpm=15,
                  status='validated',min_steps=1,max_steps=45,sample_bottles=[1,2],
                  validation_evidence='Synthetic dose-range study only',
                  bottle_scope_evidence='Synthetic cross-bottle study only')
        data.update(extra)
        return make_calibration(data,stock,'g')

    def test_adopt_estimate_never_target(self):
        s=dict(target=dict(peg_fraction=.05,salt_fraction=.05),
               recipe=dict(estimated=dict(peg_fraction=.04,salt_fraction=.04)))
        c=composition_record(s,{})
        self.assertEqual('estimated',c['basis'])
        self.assertEqual(.04,c['peg_fraction'])
        self.assertAlmostEqual(-.01,c['deviation']['peg_fraction'])
        self.assertFalse(c['valid'])  # Missing calibration evidence cannot train.

    def test_missing_estimate_does_not_fall_back_to_target(self):
        c=composition_record(dict(target=dict(peg_fraction=.05,salt_fraction=.05)),{})
        self.assertEqual('unavailable',c['basis']);self.assertIsNone(c['peg_fraction'])

    def test_independent_component_measurement_has_precedence(self):
        s=dict(target=dict(peg_fraction=.05,salt_fraction=.05),
               measured_composition=dict(peg_fraction=.049,salt_fraction=.051,
                   method='Independent component assay',evidence='assay-file-123'))
        c=composition_record(s,{})
        self.assertEqual('measured',c['basis']);self.assertTrue(c['valid'])

    def test_bottle_total_mass_never_becomes_component_measurement(self):
        s=dict(target=dict(peg_fraction=.05,salt_fraction=.05),
               measured_composition=dict(peg_fraction=.05,salt_fraction=.05,
                   measurement_kind='total_mass',method='weighed bottle',evidence='1.00 g'))
        c=composition_record(s,{})
        self.assertFalse(c['valid']);self.assertNotEqual('measured',c['basis'])

    def test_tolerance_excludes_large_measured_deviation(self):
        s=dict(target=dict(peg_fraction=.05,salt_fraction=.05),
               measured_composition=dict(peg_fraction=.04,salt_fraction=.04,method='assay',evidence='report'))
        self.assertFalse(composition_record(s,dict(composition_tolerance_fraction=.005))['valid'])

    def test_trial_is_explicit_and_not_validated(self):
        c=self.calibration(status='trial')
        self.assertEqual('trial',c['status'])

    def test_validated_requires_evidence(self):
        with self.assertRaises(ValueError):self.calibration(validation_evidence='')

    def test_shared_bottles_require_scope_evidence(self):
        with self.assertRaises(ValueError):self.calibration(bottle_scope_evidence='')

    def test_calibration_range_requires_actual_reference_point_inside(self):
        with self.assertRaises(ValueError):self.calibration(min_steps=46,max_steps=50)

    def test_tiny_dose_does_not_extrapolate_below_minimum(self):
        c=self.calibration(min_steps=30)
        p=preview_recipe(dict(salt_port=13,peg_fraction=0,salt_fraction=0,
            total_mass_g=.05,safe_mass_limit_g=1,sample=1),default_stocks(),[c],'g')
        self.assertFalse(p['executable'])

    def test_equal_total_steps_split_inside_minmax_instead_of_tiny_last_piece(self):
        c=self.calibration(min_steps=20)
        p=preview_recipe(dict(salt_port=13,peg_fraction=0,salt_fraction=0,
            total_mass_g=.2,safe_mass_limit_g=1,sample=1),default_stocks(),[c],'g')
        self.assertTrue(p['executable'],p['issues'])
        self.assertEqual([30,30],p['plan']['items'][0]['chunks'])

    def test_uncovered_bottle_is_rejected(self):
        p=preview_recipe(dict(salt_port=13,peg_fraction=0,salt_fraction=0,
            total_mass_g=.1,safe_mass_limit_g=1,sample=3),default_stocks(),[self.calibration()],'g')
        self.assertFalse(p['executable'])

    def test_validated_recipe_uses_actual_compiled_steps_and_rejects_changed_chase(self):
        stocks=default_stocks()
        calibrations=[self.calibration(port) for port in (13,14,15)]
        p=preview_recipe(dict(salt_port=13,peg_fraction=.05,salt_fraction=.05,
            total_mass_g=1,safe_mass_limit_g=2,sample=1),stocks,calibrations,'g')
        s=dict(recipe=p,bottle=1)
        self.assertTrue(composition_record(s,dict(stocks=stocks))['valid'])
        p['estimated']['peg_fraction']=.99
        record=composition_record(s,dict(stocks=stocks))
        self.assertLess(record['peg_fraction'],.06)
        p['plan']['items'][0]['parameters'][0]['chase']['rpm']=10
        self.assertFalse(composition_record(s,dict(stocks=stocks))['valid'])

    def test_trial_can_commission_but_cannot_train(self):
        request=dict(salt_port=13,peg_fraction=0,salt_fraction=0,
            total_mass_g=.1,safe_mass_limit_g=1,sample=1)
        p=preview_recipe(request,default_stocks(),[self.calibration(status='trial')],'g')
        self.assertTrue(p['executable']);self.assertFalse(p['composition']['valid'])
        self.assertFalse(preview_recipe(dict(request,allow_trial_calibration=False),
            default_stocks(),[self.calibration(status='trial')],'g')['executable'])

    def test_invalid_latest_calibration_is_not_silently_skipped(self):
        c=self.calibration()
        p=preview_recipe(dict(salt_port=13,peg_fraction=0,salt_fraction=0,
            total_mass_g=.1,safe_mass_limit_g=1),default_stocks(),
            [c,dict(c,status='invalid')],'g')
        self.assertFalse(p['executable'])

    def test_per_source_geometry_signatures(self):
        p=preview_recipe(dict(salt_port=13,peg_fraction=0,salt_fraction=0,
            total_mass_g=.1,safe_mass_limit_g=1),default_stocks(),[self.calibration()],
            {'14':'unrelated','15':'g'})
        self.assertTrue(p['executable'])

    def test_other_bottle_calibration_does_not_hide_this_bottle_reference(self):
        first=self.calibration(sample_bottles=[1])
        second=self.calibration(sample_bottles=[2])
        p=preview_recipe(dict(salt_port=13,peg_fraction=0,salt_fraction=0,
            total_mass_g=.1,safe_mass_limit_g=1,sample=1),default_stocks(),[first,second],'g')
        self.assertTrue(p['executable'])
        self.assertEqual(first['id'],p['plan']['items'][0]['reference']['id'])

    def test_rounding_tolerance_blocks_recipe(self):
        # 0.05 target at 0.10 g/step resolves to 0.04 after integer rounding.
        calibrations=[self.calibration(port,groups_g=[22.5]*3) for port in (13,14,15)]
        p=preview_recipe(dict(salt_port=13,peg_fraction=.05,salt_fraction=.05,
            total_mass_g=1,safe_mass_limit_g=2,composition_tolerance_fraction=.005),
            default_stocks(),calibrations,'g')
        self.assertAlmostEqual(.04,p['estimated']['peg_fraction'])
        self.assertFalse(p['executable']);self.assertFalse(p['composition']['valid'])

    def test_execution_identity_ignores_target_and_bottle(self):
        p=preview_recipe(dict(salt_port=13,peg_fraction=0,salt_fraction=0,
            total_mass_g=.1,safe_mass_limit_g=1),default_stocks(),[self.calibration()],'g')['plan']
        other=copy.deepcopy(p);other['sample']=2;other['items'][0]['sample']=2
        other['items'][0]['requested_mass_g']=.101
        for x in other['items'][0]['parameters']:x['sample']=2
        self.assertEqual(execution_key(p),execution_key(other))
        other['purge']['turns']+=1
        self.assertNotEqual(execution_key(p),execution_key(other))
        other['purge']['turns']-=1
        other['items'][0]['parameters'][0]['aspirate']['steps']+=1
        self.assertNotEqual(execution_key(p),execution_key(other))

    def test_signature_ignores_unrelated_source_and_com(self):
        config=dict(geometry=dict(A_cm=10,B_cm=10,C_cm=71,sample_cm={'1':50},source_cm={'10':80,'13':80}),
                    pump={'address':1},valve={'address':2},reset={},liquids={},communication={'port':'COM6'})
        changed=copy.deepcopy(config);changed['geometry']['source_cm']['10']=100
        changed['communication']['port']='COM5';changed['liquids']['10']='changed unused'
        self.assertEqual(plumbing_signature(config,13),plumbing_signature(changed,13))
        changed['geometry']['source_cm']['13']=100
        self.assertNotEqual(plumbing_signature(config,13),plumbing_signature(changed,13))


if __name__=='__main__':unittest.main()
