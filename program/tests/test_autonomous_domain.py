"""Autonomous additions retain capacity, imaging and source constraints."""
import unittest

from continuation_domain import preview_addition, theoretical_addition
from experiment_domain import default_stocks, make_calibration


class AutonomousDomainTests(unittest.TestCase):
    def setUp(self):
        self.stocks = default_stocks()
        self.inventory = dict(known=True, revision=1, total_mass_g=1., peg_mass_g=.1,
                              salt_mass_g=.05, water_mass_g=.85, salt_port=13,
                              calibration_valid=True)
        self.empty = dict(self.inventory, total_mass_g=0, peg_mass_g=0, salt_mass_g=0,
                          water_mass_g=0, salt_port=None)
        self.request = dict(sample=1, salt_port=13, peg_fraction=.12, salt_fraction=.06,
                            safe_mass_limit_g=3, composition_tolerance_fraction=0)
        self.calibrations = [make_calibration(dict(source=p, groups_g=[.5]*3,
            doses_per_group=5, aspirate_steps=100, aspirate_rpm=5,
            chase_steps=1000, chase_rpm=15, status='validated', min_steps=1,
            max_steps=100, sample_bottles=[1], validation_evidence='synthetic',
            bottle_scope_evidence='synthetic'),
            next(s for s in self.stocks if s['port']==p), 'g') for p in (13,14,15)]

    def theory(self, inventory=None, **changes):
        return theoretical_addition(dict(self.request, **changes),
                                    self.inventory if inventory is None else inventory, self.stocks)

    def preview(self, inventory=None, **changes):
        return preview_addition(dict(self.request, **changes),
            self.inventory if inventory is None else inventory, self.stocks, self.calibrations, 'g')

    def test_missing_capacity_preserves_theory_without_executable_plan(self):
        p = self.theory(safe_mass_limit_g=None)
        self.assertTrue(p['reachable'], p['issues'])
        self.assertFalse(p['executable'])
        self.assertAlmostEqual(1.13636363636, p['final_mass_g'])
        self.assertAlmostEqual(.0909090909, p['theoretical_additions_g'][14])
        preview = self.preview(safe_mass_limit_g=None)
        self.assertFalse(preview['executable'])
        self.assertEqual([], preview['plan']['items'])
        self.assertTrue(preview['reachable'])

    def test_missing_calibration_preserves_dilution_theory(self):
        p = preview_addition(dict(self.request, peg_fraction=.08, salt_fraction=.04),
                             self.inventory, self.stocks, [], 'g')
        self.assertTrue(p['reachable'])
        self.assertFalse(p['executable'])
        self.assertAlmostEqual(.25, p['theoretical_additions_g'][15])

    def test_initial_floor_applies_before_and_after_integer_rounding(self):
        p = self.preview(self.empty, seed_mass_g=.1, min_initial_mass_g=.501,
                         composition_tolerance_fraction=.002)
        self.assertTrue(p['executable'], p['issues'])
        self.assertAlmostEqual(.501, p['target']['total_mass_g'])
        self.assertGreaterEqual(p['estimated']['total_mass_g'], .501-1e-10)
        self.assertFalse(self.theory(self.empty, seed_mass_g=.1,
            min_initial_mass_g=.501, final_mass_g=.5)['reachable'])

    def test_initial_reserve_survives_rounding_but_later_stage_can_use_it(self):
        p = self.theory(self.empty, seed_mass_g=.5, min_initial_mass_g=.5,
                        safe_mass_limit_g=.8, reserve_mass_g=.4)
        self.assertFalse(p['reachable'])
        p = self.preview(self.empty, seed_mass_g=.501, safe_mass_limit_g=.703,
                         reserve_mass_g=.2)
        self.assertTrue(p['reachable'], p['issues'])
        self.assertFalse(p['executable'])
        p = self.preview(safe_mass_limit_g=1.3, reserve_mass_g=.4)
        self.assertTrue(p['executable'], p['issues'])
        self.assertGreater(p['estimated']['total_mass_g'], .9)

    def test_source_budget_applies_to_theoretical_concentration_and_dilution(self):
        self.assertFalse(self.theory(source_mass_remaining_g={'14': .09})['reachable'])
        self.assertFalse(self.theory(peg_fraction=.08, salt_fraction=.04,
            source_mass_remaining_g={'15': .249})['reachable'])
        p = self.theory(source_mass_remaining_g={'14': .1})
        self.assertTrue(p['reachable'], p['issues'])

    def test_source_budget_handles_decreasing_water_dose(self):
        rich = dict(self.inventory, peg_mass_g=.3, salt_mass_g=.2, water_mass_g=.5)
        p = self.theory(rich, peg_fraction=.28, salt_fraction=.18,
                        source_mass_remaining_g={'15': 0})
        self.assertTrue(p['reachable'], p['issues'])
        self.assertAlmostEqual(5/3, p['final_mass_g'])
        self.assertEqual(0, p['theoretical_additions_g'][15])

    def test_source_budget_rejects_quantization_after_exact_theory_passes(self):
        request = dict(source_mass_remaining_g={'14': .091})
        self.assertTrue(self.theory(**request)['reachable'])
        self.assertFalse(self.preview(**request)['executable'])
        # Exact integer composition needs PEG .092, salt .046 and water .002.
        p = self.preview(source_mass_remaining_g={'14': .092, '13': .046, '15': .002})
        self.assertTrue(p['executable'], p['issues'])
        for port, mass in p['selected_additions_g'].items():
            self.assertLessEqual(mass, {'14': .092, '13': .046, '15': .002}[str(port)]+1e-10)

    def test_small_water_slope_still_obeys_source_budget(self):
        p = self.theory(self.empty, peg_fraction=.2, salt_fraction=.19999999998,
                        seed_mass_g=10, safe_mass_limit_g=10,
                        source_mass_remaining_g={'15': 0})
        self.assertFalse(p['reachable'])

    def test_tiny_seed_does_not_shrink_initial_imaging_floor(self):
        p = self.preview(self.empty, seed_mass_g=.000001, min_initial_mass_g=.003,
                         composition_tolerance_fraction=.1)
        self.assertTrue(p['executable'], p['issues'])
        self.assertGreaterEqual(p['estimated']['total_mass_g'], .003-1e-10)

    def test_imaging_floor_above_capacity_never_compiles_commands(self):
        p = self.preview(self.empty, seed_mass_g=.1, min_initial_mass_g=.6,
                         safe_mass_limit_g=.5)
        self.assertFalse(p['reachable'])
        self.assertFalse(p['executable'])
        self.assertEqual([], p['plan']['items'])

    def test_constraint_values_reject_bad_types_negative_and_nonfinite(self):
        for field in ('min_initial_mass_g', 'reserve_mass_g', 'max_added_mass_g'):
            for value in (True, None, '1', -1, float('nan'), float('inf')):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.theory(**{field: value})
        for value in (True, '1', -1, float('nan'), float('inf')):
            with self.subTest(capacity=value), self.assertRaises(ValueError):
                self.theory(safe_mass_limit_g=value)
        for value in (None, [], {'14': True}, {'14': '1'}, {'14': -1},
                      {'14': float('nan')}, {'14': float('inf')}, {14: 1}, {'x': 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.theory(source_mass_remaining_g=value)


if __name__ == '__main__':
    unittest.main()
