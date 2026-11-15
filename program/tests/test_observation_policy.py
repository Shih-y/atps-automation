"""Pure optical policy: provenance, quality, and independent temporal evidence."""
import copy
from datetime import datetime, timedelta, timezone
import unittest

from observation_policy import evaluate_observation, validate_observation_policy


class ObservationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.mixed = datetime(2026, 11, 15, tzinfo=timezone.utc)
        self.sample = dict(id='s1', bottle=1, generation=2, mixed_at=self.mixed.isoformat(), frames=[])
        self.project = dict(min_settle_s=30, max_observe_s=300)
        self.policy = dict(mode='automatic', stable_frames=2, min_frame_spacing_s=10)

    def frame(self, seconds=60, **updates):
        roi = dict(sample=1, x=0, y=0, width=100, height=200)
        bottle = dict(sample=1, classification='two_phase', interface_y=100,
                      inspected_region=dict(x=20, y=50, width=60, height=120),
                      quality=dict(flags=[], glare_fraction=.001, texture_fraction=.01,
                                   edge_sharpness=30, contrast_range=100, interface_contrast=20,
                                   horizontal_support=1, stability='not_checked', signature=[50.] * 27))
        value = dict(id='f'+str(seconds), job_id='j'+str(seconds), sample_id='s1', bottle=1,
                     generation=2, mixed_at=self.mixed.isoformat(),
                     captured_at=(self.mixed + timedelta(seconds=seconds)).isoformat(),
                     settle_s=seconds, valid_observation_time=True, mapping_version=3, rois=[roi],
                     analysis=dict(method='local-optical-heuristic-v1', validated=False,
                                   rois=[copy.deepcopy(roi)], bottles=[bottle]))
        value.update(updates)
        return value

    def evaluate(self, frames):
        self.sample['frames'] = frames
        return evaluate_observation(self.sample, self.project, self.policy)

    def test_defaults_require_human_review(self):
        self.assertEqual(validate_observation_policy({})['mode'], 'human')

    def test_policy_rejects_unbounded_nonfinite_and_wrong_types(self):
        for key, value in [('mode', 'auto'), ('stable_frames', 1), ('stable_frames', True),
                           ('max_auto_retries', 11), ('retry_interval_s', 0),
                           ('min_frame_spacing_s', float('nan')),
                           ('max_interface_shift_fraction', float('inf')),
                           ('min_phase_fraction', .46), ('single_phase_validated', 1),
                           ('single_phase_evidence', ['proof'])]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_observation_policy({key: value})

    def test_validated_single_requires_nonempty_evidence(self):
        with self.assertRaises(ValueError):
            validate_observation_policy(dict(single_phase_validated=True, single_phase_evidence='  '))

    def test_no_photograph_is_pending_not_single(self):
        result = self.evaluate([])
        self.assertEqual((result['status'], result['classification']), ('pending', 'uncertain'))
        self.assertIn('missing_frames', result['reason_codes'])

    def test_two_spaced_quality_frames_produce_optical_candidate(self):
        frames = [self.frame(60), self.frame(90)]
        before = copy.deepcopy(frames)
        result = self.evaluate(frames)
        self.assertEqual((result['status'], result['classification']), ('candidate', 'two_phase'))
        self.assertEqual(result['evidence_frame_ids'], ['f60', 'f90'])
        self.assertGreater(result['phase_height_fraction'], 0)
        self.assertLess(result['phase_height_fraction'], 1)
        self.assertEqual(result['method'], 'local-optical-heuristic-v1')
        self.assertEqual(frames, before)

    def test_single_requires_explicit_criterion_and_quality_evidence(self):
        frames = [self.frame(60), self.frame(90)]
        for frame in frames:
            b = frame['analysis']['bottles'][0]
            b.update(classification='single', interface_y=None)
            b['quality'].update(interface_contrast=0, horizontal_support=0)
        result = self.evaluate(frames)
        self.assertEqual((result['status'], result['classification']), ('unknown', 'uncertain'))
        self.assertIn('single_phase_criterion_unvalidated', result['reason_codes'])
        self.policy.update(single_phase_validated=True, single_phase_evidence='Bench validated uniform-color criterion, protocol P1')
        self.assertEqual(self.evaluate(frames)['classification'], 'single')

    def test_duplicate_id_job_or_timestamp_cannot_supply_second_frame(self):
        for field in ('id', 'job_id', 'captured_at'):
            frames = [self.frame(60), self.frame(90)]
            frames[1][field] = frames[0][field]
            with self.subTest(field=field):
                self.assertNotEqual(self.evaluate(frames)['status'], 'candidate')

    def test_frame_spacing_and_required_count_are_enforced(self):
        self.assertEqual(self.evaluate([self.frame(60), self.frame(65)])['status'], 'pending')
        self.policy['stable_frames'] = 3
        self.assertEqual(self.evaluate([self.frame(60), self.frame(90)])['status'], 'pending')
        self.assertEqual(self.evaluate([self.frame(60), self.frame(90), self.frame(120)])['status'], 'candidate')

    def test_generation_mapping_and_roi_must_agree(self):
        for field, value in [('generation', 1), ('mapping_version', 4), ('sample_id', 'other')]:
            frames = [self.frame(60), self.frame(90, **{field: value})]
            with self.subTest(field=field):
                self.assertNotEqual(self.evaluate(frames)['status'], 'candidate')
        frames = [self.frame(60), self.frame(90)]
        frames[1]['rois'][0]['x'] = 1
        self.assertNotEqual(self.evaluate(frames)['status'], 'candidate')

    def test_observation_window_and_capture_time_are_checked(self):
        for updates in [dict(settle_s=301), dict(settle_s=20), dict(valid_observation_time=False),
                        dict(captured_at='invalid'), dict(mixed_at='2025-01-01T00:00:00Z')]:
            with self.subTest(updates=updates):
                self.assertNotEqual(self.evaluate([self.frame(60), self.frame(90, **updates)])['status'], 'candidate')

    def test_bad_quality_flags_and_numeric_values_reject(self):
        for key, value in [('flags', ['glare']), ('flags', ['texture_or_bubbles']),
                           ('flags', ['blur_or_weak_edges']), ('flags', ['unstable_frame']),
                           ('glare_fraction', .5), ('texture_fraction', .5),
                           ('edge_sharpness', 1), ('contrast_range', 5),
                           ('horizontal_support', float('nan')), ('interface_contrast', float('inf')),
                           ('signature', [float('nan')] * 27), ('stability', 'unstable')]:
            frames = [self.frame(60), self.frame(90)]
            frames[1]['analysis']['bottles'][0]['quality'][key] = value
            with self.subTest(key=key, value=value):
                result = self.evaluate(frames)
                self.assertEqual(result['status'], 'unknown')
                self.assertTrue(result['reason_codes'])

    def test_missing_quality_method_or_analysis_cannot_be_candidate(self):
        for field in ('quality',):
            frames = [self.frame(60), self.frame(90)]
            del frames[1]['analysis']['bottles'][0][field]
            self.assertEqual(self.evaluate(frames)['status'], 'unknown')
        for updates in [dict(analysis=None), dict(analysis={'method': 'invented'})]:
            self.assertEqual(self.evaluate([self.frame(60), self.frame(90, **updates)])['status'], 'unknown')

    def test_unstable_interface_signature_or_classification_reject(self):
        for update in ('interface', 'signature', 'classification'):
            frames = [self.frame(60), self.frame(90)]
            b = frames[1]['analysis']['bottles'][0]
            if update == 'interface': b['interface_y'] = 145
            if update == 'signature': b['quality']['signature'] = [100.] * 27
            if update == 'classification': b.update(classification='single', interface_y=None)
            with self.subTest(update=update):
                self.assertEqual(self.evaluate(frames)['status'], 'unknown')

    def test_phase_fraction_must_be_inside_observed_liquid_region(self):
        frames = [self.frame(60), self.frame(90)]
        for frame in frames: frame['analysis']['bottles'][0]['interface_y'] = 51
        self.assertEqual(self.evaluate(frames)['status'], 'unknown')

    def test_latest_bad_frame_cannot_reuse_an_older_good_pair(self):
        for updates in [dict(analysis=None), dict(captured_at='invalid')]:
            with self.subTest(updates=updates):
                result = self.evaluate([self.frame(60), self.frame(90), self.frame(120, **updates)])
                self.assertEqual(result['status'], 'unknown')

    def test_all_required_signatures_must_be_consistent(self):
        self.policy['stable_frames'] = 3
        frames = [self.frame(60), self.frame(90), self.frame(120)]
        for frame, shade in zip(frames, [50, 60, 70]):
            frame['analysis']['bottles'][0]['quality']['signature'] = [shade] * 27
        self.assertNotEqual(self.evaluate(frames)['status'], 'candidate')

    def test_clean_later_pair_can_recover_after_bad_frame(self):
        frames = [self.frame(60, analysis=None), self.frame(90), self.frame(120)]
        result = self.evaluate(frames)
        self.assertEqual(result['status'], 'candidate')
        self.assertEqual(result['evidence_frame_ids'], ['f90', 'f120'])


if __name__ == '__main__':
    unittest.main()
