"""Mass conservation and finite addition planning, without hardware."""
import copy
import unittest

from experiment_domain import composition_record, default_stocks, make_calibration, suggest_points, execution_key
from continuation_domain import inventory_key, preview_addition, theoretical_addition


class ContinuationDomainTests(unittest.TestCase):
    def setUp(self):
        self.stocks = default_stocks()
        self.inventory = dict(known=True, revision=2, total_mass_g=1., peg_mass_g=.10,
                              salt_mass_g=.05, water_mass_g=.85, salt_port=13,
                              sample_id='parent', calibration_valid=True,
                              calibration_ids=['parent-peg','parent-salt','parent-water'])
        self.calibrations = [make_calibration(dict(
            source=port, groups_g=[.5,.5,.5], doses_per_group=5,
            aspirate_steps=100, aspirate_rpm=5, chase_steps=1000, chase_rpm=15,
            status='validated', min_steps=1, max_steps=100, sample_bottles=[1,2],
            validation_evidence='synthetic dose validation', bottle_scope_evidence='synthetic bottle validation'),
            next(s for s in self.stocks if s['port']==port), 'g') for port in (13,14,15)]
        self.request = dict(sample=1, salt_port=13, peg_fraction=.12, salt_fraction=.06,
                            safe_mass_limit_g=3, temperature_c=25,
                            composition_tolerance_fraction=.0002, allow_trial_calibration=False)

    def theory(self, **extra):
        return theoretical_addition(dict(self.request, **extra), self.inventory, self.stocks)

    def preview(self, **extra):
        return preview_addition(dict(self.request, **extra), self.inventory,
                                self.stocks, self.calibrations, 'g')

    def test_user_concentration_example_has_exact_theoretical_minimum(self):
        p=self.theory()
        self.assertTrue(p['reachable'],p['issues'])
        self.assertAlmostEqual(1.1363636363636365,p['final_mass_g'])
        self.assertAlmostEqual(.09090909090909094,p['theoretical_additions_g'][14])
        self.assertAlmostEqual(.04545454545454547,p['theoretical_additions_g'][13])
        self.assertAlmostEqual(0,p['theoretical_additions_g'][15])

    def test_user_dilution_example_adds_only_water(self):
        p=self.theory(peg_fraction=.08,salt_fraction=.04)
        self.assertTrue(p['reachable'])
        self.assertAlmostEqual(.25,p['added_mass_g'])
        self.assertEqual(0,p['stock_masses'][14]);self.assertEqual(0,p['stock_masses'][13])
        self.assertAlmostEqual(.25,p['stock_masses'][15])

    def test_unknown_or_inconsistent_inventory_is_not_an_empty_bottle(self):
        for updates in ({'known':False},{'total_mass_g':2},{'peg_mass_g':-.1},
                        {'calibration_valid':False}):
            inventory=dict(self.inventory,**updates)
            self.assertFalse(theoretical_addition(self.request,inventory,self.stocks)['reachable'])

    def test_cross_salt_is_rejected_even_with_available_other_stock(self):
        self.assertFalse(self.theory(salt_port=11)['reachable'])

    def test_existing_solute_cannot_reach_zero_by_finite_addition(self):
        self.assertFalse(self.theory(peg_fraction=0)['reachable'])
        self.assertFalse(self.theory(salt_fraction=0)['reachable'])

    def test_negative_water_and_forced_negative_stock_are_rejected(self):
        self.assertFalse(self.theory(peg_fraction=.3,salt_fraction=.1)['reachable'])
        self.assertFalse(self.theory(final_mass_g=1)['reachable'])

    def test_no_change_is_not_a_new_motion(self):
        self.assertFalse(self.preview(peg_fraction=.1,salt_fraction=.05)['executable'])

    def test_capacity_and_mass_budget_apply_to_final_not_added_mass(self):
        self.assertFalse(self.preview(safe_mass_limit_g=1.1)['executable'])
        self.assertFalse(self.preview(max_added_mass_g=.1)['executable'])

    def test_compile_additions_only_and_estimate_final_inventory(self):
        before=copy.deepcopy(self.inventory)
        p=self.preview()
        self.assertTrue(p['executable'],p['issues'])
        self.assertEqual('continuation',p['kind'])
        self.assertLess(p['added_mass_g'],.2)
        self.assertAlmostEqual(1+p['added_mass_g'],p['estimated']['total_mass_g'])
        self.assertLessEqual(abs(p['estimated']['peg_fraction']-.12),.0002)
        self.assertLessEqual(abs(p['estimated']['salt_fraction']-.06),.0002)
        self.assertAlmostEqual(p['estimated']['total_mass_g'],sum(
            p['estimated'][key] for key in ('peg_mass_g','salt_mass_g','water_mass_g')))
        self.assertEqual(before,self.inventory)
        self.assertEqual(before,p['initial_inventory'])
        for row in p['plan']['items']:
            for part in row['parameters']:
                self.assertEqual(5,part['aspirate']['rpm'])
                self.assertEqual(1000,part['chase']['steps'])
                self.assertEqual(15,part['chase']['rpm'])

    def test_accumulated_coordinates_recomputed_from_steps_not_cached_estimate(self):
        p=self.preview();p['estimated']['peg_fraction']=.99
        s=dict(target=p['target'],recipe=p,bottle=1)
        c=composition_record(s,dict(stocks=self.stocks))
        self.assertTrue(c['valid'],c['reasons'])
        self.assertAlmostEqual(.12,c['peg_fraction'],delta=.0002)
        self.assertEqual('estimated',c['basis'])

    def test_parent_calibration_cannot_be_upgraded_by_new_validated_water(self):
        p=self.preview(peg_fraction=.08,salt_fraction=.04)
        p['initial_inventory']['calibration_valid']=False
        c=composition_record(dict(recipe=p,bottle=1),dict(stocks=self.stocks))
        self.assertFalse(c['valid'])

    def test_zero_inventory_uses_explicit_seed_mass(self):
        empty=dict(known=True,revision=0,total_mass_g=0,peg_mass_g=0,salt_mass_g=0,
                   water_mass_g=0,salt_port=13,sample_id=None,calibration_valid=True)
        p=preview_addition(dict(self.request,total_mass_g=.5),empty,self.stocks,self.calibrations,'g')
        self.assertTrue(p['executable'],p['issues'])
        self.assertAlmostEqual(.5,p['target']['total_mass_g'])
        self.assertAlmostEqual(.5,p['estimated']['total_mass_g'],delta=.004)

    def test_confirmed_empty_inventory_is_neutral_until_first_completed_dose(self):
        empty=dict(known=True,revision=0,total_mass_g=0,peg_mass_g=0,salt_mass_g=0,
                   water_mass_g=0,salt_port=None,sample_id=None,calibration_valid=True)
        key=inventory_key(empty)
        p=preview_addition(dict(self.request,total_mass_g=.5),empty,self.stocks,self.calibrations,'g')
        self.assertTrue(p['executable'],p['issues'])
        self.assertTrue(p['composition']['valid'],p['composition']['reasons'])
        self.assertEqual(key,p['inventory_key'])
        self.assertIsNone(p['initial_inventory']['salt_port'])

    def test_finite_neighbor_search_never_extrapolates_below_calibration_range(self):
        for c in self.calibrations:c.update(min_steps=100,max_steps=100)
        p=self.preview()
        if p['executable']:
            for row in p['plan']['items']:
                self.assertTrue(all(n==100 for n in row['chunks']))
        else:
            self.assertTrue(p['issues'])

    def test_uncovered_bottle_and_old_geometry_rejected(self):
        self.assertFalse(self.preview(sample=3)['executable'])
        p=preview_addition(self.request,self.inventory,self.stocks,self.calibrations,'other')
        self.assertFalse(p['executable'])

    def test_zero_time_budget_never_starts_an_addition(self):
        p=self.preview(remaining_time_s=0)
        self.assertFalse(p['executable'])

    def test_inventory_identity_changes_after_revision_or_parent_change(self):
        self.assertEqual(inventory_key(self.inventory),inventory_key(dict(reversed(list(self.inventory.items())))))
        self.assertNotEqual(inventory_key(self.inventory),inventory_key(dict(self.inventory,revision=3)))
        self.assertNotEqual(inventory_key(self.inventory),inventory_key(dict(self.inventory,sample_id='new')))

    def test_second_round_preserves_first_record_and_uses_cumulative_material(self):
        first=self.preview();saved=copy.deepcopy(first)
        estimated=first['estimated']
        current=dict(self.inventory,**{key:estimated[key] for key in
                     ('total_mass_g','peg_mass_g','salt_mass_g','water_mass_g')},revision=3,sample_id='child')
        second=preview_addition(dict(self.request,peg_fraction=.10,salt_fraction=.05),
                                current,self.stocks,self.calibrations,'g')
        self.assertTrue(second['executable'],second['issues'])
        self.assertGreater(second['estimated']['total_mass_g'],first['estimated']['total_mass_g'])
        self.assertEqual(saved,first)

    def test_initial_more_concentrated_than_stocks_can_use_negative_denominator(self):
        rich=dict(self.inventory,peg_mass_g=.30,salt_mass_g=.20,water_mass_g=.50)
        result=theoretical_addition(dict(self.request,peg_fraction=.28,salt_fraction=.18),rich,self.stocks)
        self.assertTrue(result['reachable'],result['issues'])
        self.assertAlmostEqual(1/.9,result['final_mass_g'])
        self.assertTrue(all(value>=0 for value in result['stock_masses'].values()))

    def test_optional_unused_stock_does_not_require_delivery_calibration(self):
        water=[c for c in self.calibrations if c['source']==15]
        result=preview_addition(dict(self.request,peg_fraction=.08,salt_fraction=.04),
                                self.inventory,self.stocks,water,'g')
        self.assertTrue(result['executable'],result['issues'])
        self.assertEqual([15],[row['source'] for row in result['plan']['items']])

    def test_zero_error_neighbor_search_can_add_small_water_to_correct_rounding(self):
        result=self.preview(composition_tolerance_fraction=0)
        self.assertTrue(result['executable'],result['issues'])
        self.assertAlmostEqual(.12,result['estimated']['peg_fraction'])
        self.assertAlmostEqual(.06,result['estimated']['salt_fraction'])
        self.assertEqual(0,result['theoretical_additions_g'][15])
        self.assertGreater(result['selected_additions_g'][15],0)
        self.assertTrue(result['composition']['valid'])

    def test_quantized_final_capacity_rejected_after_theory_passes(self):
        request=dict(self.request,composition_tolerance_fraction=0,safe_mass_limit_g=1.139)
        self.assertTrue(theoretical_addition(request,self.inventory,self.stocks)['reachable'])
        self.assertFalse(preview_addition(request,self.inventory,self.stocks,self.calibrations,'g')['executable'])

    def test_calibrated_minimum_cannot_be_evaded_by_tiny_tail(self):
        for c in self.calibrations:c.update(min_steps=100,max_steps=100)
        result=self.preview(safe_mass_limit_g=1.2)
        self.assertFalse(result['executable'])

    def test_default_citrate_candidate_domain_uses_effective_anhydrous_fraction(self):
        effective=next(s['mass_fraction'] for s in self.stocks if s['port']==10)
        point=effective+.005
        result=suggest_points([],dict(salt_port=10,peg_range=[0,0],salt_range=[point,point]))
        self.assertEqual([],result['grid'])

    def test_addition_identity_includes_starting_material_but_not_requested_target(self):
        plan=self.preview()['plan']
        same=copy.deepcopy(plan);same['target']=dict(peg_fraction=.1201,salt_fraction=.0601)
        self.assertEqual(execution_key(plan),execution_key(same))
        other=copy.deepcopy(plan);other['inventory_key']='another material inventory'
        self.assertNotEqual(execution_key(plan),execution_key(other))

    def test_independent_execution_identity_does_not_use_unrelated_inventory_metadata(self):
        plan=self.preview()['plan'];plan.pop('kind');plan.pop('inventory_key')
        other=copy.deepcopy(plan);other['inventory_key']='ignored for independent methods'
        self.assertEqual(execution_key(plan),execution_key(other))


if __name__=='__main__':unittest.main()
