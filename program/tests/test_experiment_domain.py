import unittest
from experiment_domain import *

class QuantizedCapacityTests(unittest.TestCase):
    def test_rounded_delivery_must_also_fit_safe_mass_limit(self):
        stocks=default_stocks()
        calibration=make_calibration(dict(source=15,groups_g=[1.2],doses_per_group=1,
            aspirate_steps=2,aspirate_rpm=5,chase_steps=1000,chase_rpm=15),stocks[-1],'g')
        preview=preview_recipe(dict(salt_port=13,peg_fraction=0,salt_fraction=0,
            total_mass_g=1,safe_mass_limit_g=1,temperature_c=25),stocks,[calibration],'g')
        self.assertAlmostEqual(1.2,preview['estimated']['total_mass_g'])
        self.assertFalse(preview['executable'])

class DomainTests(unittest.TestCase):

    def test_negative_water(self):
        with self.assertRaises(ValueError):
            preview_recipe(dict(salt_port=13, peg_fraction=0.4, salt_fraction=0.4), default_stocks(), [], 'g')

    def test_zero_missing(self):
        p = preview_recipe(dict(salt_port=13, peg_fraction=0, salt_fraction=0), default_stocks(), [], 'g')
        self.assertEqual(p['stock_masses'][14], 0)
        self.assertFalse(p['executable'])

    def test_calibration_cap(self):
        s = default_stocks()[-1]
        with self.assertRaises(ValueError):
            make_calibration(dict(source=15, groups_g=[1, 1], doses_per_group=10, aspirate_steps=20, aspirate_rpm=5, chase_steps=100, chase_rpm=10, max_segment_steps=21), s, 'g')

    def test_diversity(self):
        r = suggest_points([], dict(salt_port=13, count=5))
        self.assertEqual(len(r['suggestions']), 5)
        self.assertEqual(len({(p['peg_fraction'], p['salt_fraction']) for p in r['suggestions']}), 5)

    def test_bool(self):
        with self.assertRaises(ValueError):
            num(True)

    def test_fraction_steps(self):
        with self.assertRaises(ValueError):
            pulse(1.2, 5)

    def test_zero_rpm(self):
        with self.assertRaises(ValueError):
            pulse(20, 0)

    def test_timeout(self):
        with self.assertRaises(ValueError):
            pulse(100, 1, 1)

    def test_stock_water(self):
        s = default_stocks()[-1]
        s['mass_fraction'] = 0.1
        with self.assertRaises(ValueError):
            validate_stock(s)

    def test_stock_density(self):
        s = default_stocks()[0]
        s['density_g_ml'] = 1
        with self.assertRaises(ValueError):
            validate_stock(s)

    def test_stock_port(self):
        s = default_stocks()[0]
        s['port'] = 10.5
        with self.assertRaises(ValueError):
            validate_stock(s)

    def test_grid_limit(self):
        with self.assertRaises(ValueError):
            suggest_points([], dict(step=0.001))

    def test_range(self):
        with self.assertRaises(ValueError):
            suggest_points([], dict(peg_range=[0.4, 0]))

    def test_count(self):
        with self.assertRaises(ValueError):
            suggest_points([], dict(count=1.5))

    def test_no_confidence(self):
        self.assertTrue(all((g['probability'] is None for g in suggest_points([], dict())['grid'])))

    def test_conflicting(self):
        samples = [dict(id=i, peg_fraction=0.1, salt_fraction=0.1, label=l) for i, l in enumerate(['single', 'two_phase'])]
        self.assertEqual(suggest_points(samples, {})['training_ids'], [])

    def test_editable_domain(self):
        self.assertTrue(all((g['peg_fraction'] / 0.2 + g['salt_fraction'] / 0.1 <= 1.000001 for g in suggest_points([], dict(peg_stock_fraction=0.2, salt_stock_fraction=0.1))['grid'])))

    def test_partial_estimate(self):
        p = preview_recipe(dict(salt_port=13, peg_fraction=0.1, salt_fraction=0.1), default_stocks(), [], 'g')
        self.assertIsNone(p['estimated']['total_mass_g'])

    def test_purge(self):
        from tube_workflows import AIR
        p = preview_recipe(dict(salt_port=13, peg_fraction=0, salt_fraction=0), default_stocks(), [], 'g')
        self.assertEqual(p['plan']['purge'], AIR)

    def test_stale_and_plan(self):
        stocks = default_stocks()
        cal = make_calibration(dict(source=15, groups_g=[1, 1], doses_per_group=10, aspirate_steps=20, aspirate_rpm=5, chase_steps=100, chase_rpm=10), stocks[-1], 'g')
        r = dict(salt_port=13, peg_fraction=0, salt_fraction=0, safe_mass_limit_g=2)
        p = preview_recipe(r, stocks, [cal], 'g')
        self.assertTrue(p['executable'])
        self.assertEqual(p['plan']['total_plugs'], 10)
        self.assertEqual(p['plan']['items'][0]['total_steps'], 200)
        self.assertFalse(preview_recipe(r, stocks, [cal], 'stale')['executable'])
