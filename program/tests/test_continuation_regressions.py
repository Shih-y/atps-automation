"""User-reproduced multi-bottle and quantization defects; fake motion only."""
import copy
import unittest
from unittest.mock import patch

import test_continuation_service as fixtures


class ContinuationRegressionTests(unittest.TestCase):
    setUp = fixtures.ContinuationServiceTests.setUp
    tearDown = fixtures.ContinuationServiceTests.tearDown
    online = fixtures.ContinuationServiceTests.online
    empty = fixtures.ContinuationServiceTests.empty
    create_run = fixtures.ContinuationServiceTests.create_run
    start = fixtures.ContinuationServiceTests.start
    stored = fixtures.ContinuationServiceTests.stored
    complete = fixtures.ContinuationServiceTests.complete
    last_sample = fixtures.ContinuationServiceTests.last_sample
    review = fixtures.ContinuationServiceTests.review

    def waiting_bottle_survives(self, mode):
        project = self.s._get('projects', self.p['id'])
        for records in (self.s.data['calibrations'], project['calibrations']):
            for ref in records:
                ref['sample_bottles'] = [2]
        self.empty(1, 2)
        run = self.create_run(bottles=(1, 2), observation_policy={'mode': mode})
        self.start(run)
        self.s.tick()
        first = self.complete()
        self.assertEqual(2, first['bottle'])
        self.s.tick()
        current = self.stored(run)
        self.assertEqual('waiting_observation' if mode == 'automatic' else 'waiting_review', current['status'])
        self.assertTrue(self.s.data['running'])
        self.assertNotIn('finished_at', current)
        self.assertEqual('planning_blocked', current['bottle_states']['1']['state'])
        self.assertTrue(current['planning_diagnostics']['by_bottle']['1']['rejection_reasons'])
        self.review(first, 'two_phase')
        self.s.tick()
        self.assertEqual(2, len(self.c.calls))
        self.assertEqual(2, self.last_sample()['bottle'])
        self.assertEqual(first['id'], self.last_sample()['parent_sample_id'])

    def test_exact_compilation_failure_keeps_other_bottle_observing(self):
        self.waiting_bottle_survives('automatic')

    def test_exact_compilation_failure_keeps_other_bottle_awaiting_review(self):
        self.waiting_bottle_survives('human')

    def test_incomplete_bounded_search_does_not_claim_all_points_exhausted(self):
        self.empty(1)
        run = self.create_run(observation_policy={'mode': 'automatic'})
        self.stored(run)['planning_limits']['exact_per_bottle'] = 1
        self.start(run)
        original = self.s._continuation_preview
        def first_candidate_blocked(body):
            result = original(body)
            result.update(executable=False, issues=['synthetic candidate-specific rejection'])
            return result
        with patch.object(self.s, '_continuation_preview', side_effect=first_candidate_blocked):
            self.s.tick()
        self.assertEqual('paused', self.stored(run)['status'])
        self.assertTrue(self.stored(run)['planning_diagnostics']['exact_limit_reached'])
        self.assertEqual(1, self.stored(run)['planning_diagnostics']['exact_evaluated'])
        later = original(dict(project_id=self.p['id'], bottle=1, peg_fraction=.1, salt_fraction=.1,
                              research_bounds=run['bounds']))
        self.assertTrue(later['executable'])
        self.assertEqual(0, len(self.c.calls))
        self.assertNotIn('finished_at', self.stored(run))

    def test_integer_search_uses_in_range_neighbors_at_both_edges(self):
        self.empty(1)
        bounds = dict(peg_min=.05, peg_max=.1, salt_min=.05, salt_max=.1)
        for peg, salt in ((.05, .05), (.1, .05), (.1, .1)):
            with self.subTest(peg=peg, salt=salt):
                preview = self.s._continuation_preview(dict(project_id=self.p['id'], bottle=1,
                    peg_fraction=peg, salt_fraction=salt, research_bounds=bounds))
                self.assertTrue(preview['executable'], preview['issues'])
                for component in ('peg', 'salt'):
                    value = preview['composition'][component+'_fraction']
                    self.assertGreaterEqual(value, bounds[component+'_min']-1e-12)
                    self.assertLessEqual(value, bounds[component+'_max']+1e-12)
                self.assertEqual(bounds, preview['target']['research_bounds'])

    def test_unattainable_narrow_research_range_is_not_relaxed_by_error_tolerance(self):
        self.empty(1)
        preview = self.s._continuation_preview(dict(project_id=self.p['id'], bottle=1,
            peg_fraction=.050001, salt_fraction=.050001,
            research_bounds=dict(peg_min=.050001, peg_max=.050001, salt_min=.050001, salt_max=.050001)))
        self.assertFalse(preview['executable'])
        self.assertTrue(any('研究范围' in issue for issue in preview['issues']))

    def test_malformed_bounds_or_target_outside_range_rejected(self):
        self.empty(1)
        valid = dict(peg_min=.05, peg_max=.1, salt_min=.05, salt_max=.1)
        for bounds in (dict(valid, peg_min=float('nan')), dict(valid, peg_max=.04),
                       dict(peg_min=.05), dict(valid, salt_max=.09)):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                self.s._continuation_preview(dict(project_id=self.p['id'], bottle=1,
                    peg_fraction=.1, salt_fraction=.1, research_bounds=bounds))

    def test_scheduler_defensively_rejects_compiler_out_of_range_result(self):
        self.empty(1)
        run = self.create_run()
        original = self.s._continuation_preview
        def out_of_range(body):
            preview = copy.deepcopy(original(body))
            if preview['executable']:
                preview['composition']['peg_fraction'] = .048
            return preview
        with patch.object(self.s, '_continuation_preview', side_effect=out_of_range):
            self.start(run)
            self.s.tick()
        self.assertEqual(0, len(self.c.calls))
        reasons = self.stored(run)['planning_diagnostics']['rejection_reasons']
        self.assertTrue(any('研究范围' in reason for reason in reasons))

    def test_run_frozen_recipe_carries_bounds_and_adopted_in_range_point(self):
        self.empty(1)
        run = self.create_run()
        self.start(run)
        self.s.tick()
        sample = self.last_sample()
        self.assertEqual(run['bounds'], sample['recipe']['target']['research_bounds'])
        for component in ('peg', 'salt'):
            value = sample['recipe']['composition'][component+'_fraction']
            self.assertGreaterEqual(value, run['bounds'][component+'_min']-1e-12)
            self.assertLessEqual(value, run['bounds'][component+'_max']+1e-12)
