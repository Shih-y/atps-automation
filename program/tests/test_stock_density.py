import copy
import unittest

from experiment_domain import default_stocks, validate_stock, make_calibration
from stock_density import audit_stocks, measured_density_usable


class DensityAuditTests(unittest.TestCase):
    def test_unknown_is_not_water_and_audit_never_mutates(self):
        stocks = default_stocks()
        before = copy.deepcopy(stocks)
        rows = audit_stocks(stocks)
        self.assertEqual(stocks, before)
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r['configured_density_g_ml'] is None for r in rows))
        self.assertTrue(all(r['status'] == 'unknown' for r in rows))
        self.assertTrue(all(r['mass_calibration_independent'] for r in rows))

    def measured(self):
        s = default_stocks()[1]
        s.update(density_g_ml=1.225, density_evidence='25C, 10mL, 三次净质量',
                 density_basis='measured', density_temperature_c=25,
                 density_mass_fraction=.4, density_batch='B1', batch='B1')
        return s

    def test_measured_context_is_checked(self):
        s = self.measured()
        self.assertTrue(measured_density_usable(s))
        for key, value in [('batch','B2'), ('mass_fraction',.3), ('temperature_c',30)]:
            changed = dict(s, **{key:value})
            self.assertFalse(measured_density_usable(changed))
            self.assertEqual(audit_stocks([changed])[0]['status'], 'context_mismatch')

    def test_citrate_context_accepts_ui_display_rounding_not_another_concentration(self):
        s = default_stocks()[0]
        s.update(density_g_ml=1.2,density_basis='measured',density_evidence='synthetic measurement',
                 density_temperature_c=25,density_mass_fraction=.26324,density_batch='')
        self.assertTrue(measured_density_usable(s))
        s['density_mass_fraction'] = .2633
        self.assertFalse(measured_density_usable(s))

    def test_legacy_and_reference_are_not_batch_measurements(self):
        s = self.measured()
        s.pop('density_basis')
        self.assertFalse(measured_density_usable(s))
        self.assertEqual(audit_stocks([s])[0]['status'], 'unverified')
        s['density_basis'] = 'reference'
        self.assertFalse(measured_density_usable(s))
        self.assertEqual(audit_stocks([s])[0]['status'], 'reference')

    def test_close_k2hpo4_concentration_does_not_claim_exact(self):
        row = audit_stocks([default_stocks()[3]])[0]
        self.assertEqual(row['reference']['mass_fraction'], .408)
        self.assertIn('40.8', row['reference']['note'])
        self.assertIsNone(row['configured_density_g_ml'])

    def test_wrong_solute_does_not_get_a_port_based_reference(self):
        s = default_stocks()[3]
        s['solute'] = 'KH2PO4'
        row = audit_stocks([s])[0]
        self.assertIsNone(row['reference']['density_g_ml'])

    def test_volume_calibration_cannot_promote_reference_density(self):
        s = self.measured()
        s['density_basis'] = 'reference'
        args = dict(source=s['port'], groups_ml=[1], doses_per_group=1,
                    aspirate_steps=45, aspirate_rpm=5, chase_steps=1000, chase_rpm=15)
        with self.assertRaisesRegex(ValueError, '实测密度'):
            make_calibration(args, s, 'g')
        args['groups_g'] = [1.225]
        self.assertAlmostEqual(make_calibration(args,s,'g')['mass_per_step_g'],1.225/45)

    def test_measured_metadata_required_and_invalid_enum_rejected(self):
        s = self.measured()
        s.pop('density_mass_fraction')
        with self.assertRaises(ValueError):
            validate_stock(s)
        s['density_basis'] = 'guessed'
        with self.assertRaises(ValueError):
            validate_stock(s)


if __name__ == '__main__':
    unittest.main()
