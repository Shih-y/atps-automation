import unittest
from experiment_domain import default_stocks, validate_stock, preview_recipe
from stock_chemistry import CITRATE_FACTOR


class HydratedCitrateTests(unittest.TestCase):
    def test_confirmed_reagent_fraction_converts_once(self):
        stock = default_stocks()[0]
        self.assertEqual(stock['reagent_mass_fraction'], .3)
        self.assertAlmostEqual(stock['mass_fraction'], .3*258.06/294.10)
        self.assertAlmostEqual(stock['mass_fraction']+stock['hydrate_water_mass_fraction'],.3)
        twice = validate_stock(validate_stock(stock))
        self.assertAlmostEqual(twice['mass_fraction'], .3*CITRATE_FACTOR)

    def test_recipe_uses_anhydrous_salt_and_includes_crystal_water(self):
        result = preview_recipe(dict(salt_port=10,peg_fraction=.1,salt_fraction=.05,
            total_mass_g=1,safe_mass_limit_g=5),default_stocks(),[],'g')
        self.assertAlmostEqual(result['stock_masses'][10], .05/(.3*CITRATE_FACTOR))
        self.assertAlmostEqual(sum(result['stock_masses'].values()),1)
        self.assertEqual(default_stocks()[2]['mass_fraction'],.15)

    def test_rejects_hydrate_conversion_for_other_salt(self):
        s = default_stocks()[2]
        s.update(reagent_form='dihydrate',reagent_mass_fraction=.15)
        with self.assertRaises(ValueError): validate_stock(s)

    def test_no_silent_migration_of_old_citrate(self):
        s = default_stocks()[0]
        s = {k:v for k,v in s.items() if k not in ('reagent_form','reagent_mass_fraction',
            'composition_basis','hydrate_water_mass_fraction','concentration_conversion')}
        s['mass_fraction'] = .3
        self.assertEqual(validate_stock(s)['mass_fraction'],.3)


if __name__ == '__main__': unittest.main()
