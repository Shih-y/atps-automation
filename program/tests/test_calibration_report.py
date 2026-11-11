import unittest
from calibration_report import summarize, analyze_input


class CalibrationReportTests(unittest.TestCase):
    def row(self, **kw):
        return dict(profile_key='PEG_S1_first', kind='delivery', stock_equiv_ml=.2,
                    water_carry_ml=.001, cross_carry_ml=.0001, source=14, sample=1,
                    condition='first', assay_method='validated independent assay', **kw)

    def test_independent_equivalent_required(self):
        rows=[{'profile_key':'x','kind':'delivery','net_mass_g':.2,'bulk_density_g_ml':1}]*10
        r=summarize(rows)['profiles']['x']
        self.assertEqual(r['n_equivalent'],0)
        self.assertFalse(r['screening_pass'])

    def test_missing_residual_is_not_zero(self):
        rows=[{'profile_key':'x','kind':'delivery','stock_equiv_ml':.2}]*10
        r=summarize(rows)['profiles']['x']
        self.assertIsNone(r['max_water_carry_ml'])
        self.assertFalse(r['screening_pass'])

    def test_full_independent_inputs_screen_only(self):
        r=summarize([self.row() for _ in range(10)])
        self.assertTrue(r['profiles']['PEG_S1_first']['screening_pass'])
        self.assertFalse(r['hardware_authorized'])

    def test_groups_never_pool_first_and_repeat(self):
        rows=[self.row() for _ in range(5)]
        rows += [dict(self.row(),profile_key='PEG_S1_repeat') for _ in range(5)]
        self.assertTrue(all(not p['screening_pass'] for p in summarize(rows)['profiles'].values()))

    def test_invalid_finite_and_negative(self):
        for v in [float('nan'),float('inf'),-.1]:
            with self.assertRaises(ValueError): summarize([dict(self.row(),stock_equiv_ml=v)])

    def test_bias_and_repeatability(self):
        r=summarize([dict(self.row(),stock_equiv_ml=.23)]*10)['profiles']['PEG_S1_first']
        self.assertAlmostEqual(r['bias_percent'],15)
        self.assertFalse(r['screening_pass'])

    def test_blank_not_counted_as_delivery(self):
        rows=[self.row() for _ in range(9)] + [dict(self.row(),kind='rinse_blank',stock_equiv_ml=0)]
        self.assertEqual(summarize(rows)['profiles']['PEG_S1_first']['n_equivalent'],9)

    def test_cannot_pool_inconsistent_physical_states_under_same_key(self):
        for field,value in [('source',11),('sample',2),('condition','continued')]:
            with self.assertRaises(ValueError):
                summarize([self.row(),dict(self.row(),**{field:value})])

    def test_exact_five_percent_boundaries(self):
        for volume in [.19,.21]:
            self.assertTrue(summarize([dict(self.row(),stock_equiv_ml=volume)]*10)['profiles']['PEG_S1_first']['screening_pass'])

    def test_missing_group_metadata_cannot_pass(self):
        rows=[{k:v for k,v in self.row().items() if k!='condition'}]*10
        self.assertFalse(summarize(rows)['profiles']['PEG_S1_first']['screening_pass'])

    def guided(self,**kw):
        p=dict(measurement_mode='stock_equivalent',profile_key='stock14-s1-first',
               source=14,sample=1,condition='first',assay_method='已核验的独立组分测定法',
               delivery_values=[.2]*10,water_carry_values_ml=[.001]*3,cross_carry_values_ml=[.0001]*3,target_ml=.2)
        p.update(kw);return p

    def test_guided_equivalent_report_and_raw_rows(self):
        r=analyze_input(self.guided())
        self.assertTrue(r['report']['profiles']['stock14-s1-first']['screening_pass'])
        self.assertEqual(len(r['rows']),16)
        self.assertFalse(r['report']['hardware_authorized'])

    def test_water_gravimetry_density_conversion(self):
        r=analyze_input(self.guided(measurement_mode='water_gravimetry',source=15,
                                  density_g_ml=.998,delivery_values=[.1996]*10))
        self.assertAlmostEqual(r['report']['profiles']['stock14-s1-first']['mean_stock_equiv_ml'],.2)
        self.assertAlmostEqual(r['rows'][0]['net_mass_g'],.1996)

    def test_stock_gross_mass_cannot_be_called_stock_equivalent(self):
        with self.assertRaisesRegex(ValueError,'15'):
            analyze_input(self.guided(measurement_mode='water_gravimetry',density_g_ml=1.1))

    def test_guided_missing_blanks_does_not_pass(self):
        r=analyze_input(self.guided(water_carry_values_ml=[],cross_carry_values_ml=[]))
        self.assertFalse(r['report']['profiles']['stock14-s1-first']['screening_pass'])

    def test_guided_bad_density_and_array_limits(self):
        for p in [self.guided(measurement_mode='water_gravimetry',source=15,density_g_ml=0),
                  self.guided(delivery_values=[.2]*1001),self.guided(delivery_values=[True]),
                  self.guided(target_ml=1),self.guided(assay_method='')]:
            with self.assertRaises(ValueError):analyze_input(p)

if __name__=='__main__': unittest.main()
