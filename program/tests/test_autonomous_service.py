"""Autonomous-loop regression with fake motion and synthetic evidence only."""
import copy
import csv
import io
import json
import time
import unittest
import zipfile
from unittest.mock import patch
import test_continuation_service as base


class AutonomousServiceTests(unittest.TestCase):
    setUp=base.ContinuationServiceTests.setUp
    tearDown=base.ContinuationServiceTests.tearDown
    online=base.ContinuationServiceTests.online
    empty=base.ContinuationServiceTests.empty
    create_run=base.ContinuationServiceTests.create_run
    start=base.ContinuationServiceTests.start
    stored=base.ContinuationServiceTests.stored
    complete=base.ContinuationServiceTests.complete
    last_sample=base.ContinuationServiceTests.last_sample
    review=base.ContinuationServiceTests.review

    def auto_run(self, **extra):
        return self.create_run(observation_policy=dict(mode='automatic',max_auto_retries=1,
            retry_interval_s=1,min_frame_spacing_s=1),**extra)

    def test_policy_frozen_and_default_human_preserved(self):
        human=self.create_run();auto=self.auto_run()
        self.assertEqual('human',human['observation_policy']['mode'])
        self.assertEqual('automatic',auto['observation_policy']['mode'])
        self.assertFalse(auto['observation_policy']['single_phase_validated'])
        self.assertEqual([],self.c.calls)

    def test_automatic_candidate_drives_second_round_with_machine_provenance(self):
        self.empty(1);run=self.auto_run();self.start(run);self.s.tick();first=self.complete()
        first['frames'].append(dict(id='evidence-2',generation=0,settle_s=2))
        decision=dict(status='candidate',classification='two_phase',reason_codes=[],
            evidence_frame_ids=['evidence-1','evidence-2'],phase_height_fraction=.5,method='bounded-optical-series-v1')
        with patch('observation_service.evaluate_observation',return_value=decision):
            self.s.tick()
        self.assertEqual(2,len(self.c.calls))
        self.assertEqual('automatic_optical',first['label']['source'])
        self.assertEqual(first['id'],self.last_sample()['parent_sample_id'])
        self.assertFalse(first.get('is_independent_repeat',False))

    def test_uncertain_exhausts_observation_without_repeating_liquid(self):
        self.empty(1);run=self.auto_run();self.start(run);self.s.tick();sample=self.complete()
        now=time.time();sample['mixed_at']='2000-01-01T00:00:00+00:00'
        self.s.tick()
        self.assertEqual(1,len(self.c.calls))
        self.assertEqual('unknown',sample['auto_observation']['status'])
        self.assertIsNone(sample['label'])
        self.assertEqual('exhausted',self.stored(run)['status'])
        self.assertTrue(self.s._bottle(1)['inventory']['known'])

    def test_failure_schedules_bounded_photo_retries_not_new_recipe(self):
        self.empty(1);run=self.auto_run();self.start(run);self.s.tick();sample=self.complete()
        sample['frames'].append(dict(id='bad-image',generation=sample['generation'],analysis=None))
        for job in self.s.data['photo_jobs']:job.update(status='failed',failure_reason='synthetic camera failure')
        self.s.tick()
        retries=[j for j in self.s.data['photo_jobs'] if j.get('automatic_retry')]
        self.assertEqual(1,len(retries))
        self.assertEqual('waiting_observation',self.stored(run)['status'])
        self.assertEqual('pending',sample['auto_observation']['status'])
        retries[0].update(status='failed',failure_reason='again')
        self.s.tick();self.s.tick()
        self.assertEqual(1,len([j for j in self.s.data['photo_jobs'] if j.get('automatic_retry')]))
        self.assertEqual(1,len(self.c.calls))
        self.assertEqual('unknown',sample['auto_observation']['status'])

    def test_human_label_is_not_replaced_by_automatic_observation(self):
        self.empty(1);run=self.auto_run();self.start(run);self.s.tick();sample=self.complete()
        self.review(sample,'uncertain')
        with patch('observation_service.evaluate_observation',return_value=dict(status='candidate',
                classification='two_phase',reason_codes=[],evidence_frame_ids=['x'],method='test')):
            self.s.tick()
        self.assertEqual('uncertain',sample['label']['classification'])
        self.assertEqual('human',sample['label']['source'])
        self.assertEqual(1,len(self.c.calls))

    def test_new_human_run_does_not_treat_old_automatic_label_as_human_review(self):
        self.empty(1);run=self.auto_run(round_budget=1);self.start(run);self.s.tick();sample=self.complete()
        sample['frames'].append(dict(id='evidence-2',generation=0,settle_s=2))
        decision=dict(status='candidate',classification='two_phase',reason_codes=[],
            evidence_frame_ids=['evidence-1','evidence-2'],phase_height_fraction=.5,method='test')
        with patch('observation_service.evaluate_observation',return_value=decision):
            self.s.tick()
            human=self.create_run();self.start(human);self.s.tick()
        self.assertEqual(1,len(self.c.calls))
        self.assertEqual('waiting_review',self.stored(human)['status'])

    def test_old_human_label_does_not_override_new_observation_generation(self):
        self.empty(1);run=self.auto_run();self.start(run);self.s.tick();sample=self.complete()
        self.review(sample,'single')
        sample['generation']+=1
        self.s._mix_completed(sample,sample['mixed_at'])
        sample['frames'].append(dict(id='new-frame',generation=1,settle_s=2))
        decision=dict(status='candidate',classification='two_phase',reason_codes=[],
            evidence_frame_ids=['new-frame'],phase_height_fraction=.5,method='test')
        with patch('observation_service.evaluate_observation',return_value=decision):self.s.tick()
        self.assertEqual('automatic_optical',sample['label']['source'])
        self.assertEqual(1,sample['label']['generation'])
        self.assertEqual('human',sample['review_history'][0]['source'])

    def test_fault_pauses_following_liquid_actions(self):
        self.empty(1);run=self.auto_run();self.start(run)
        with patch.object(self.c,'snapshot',return_value=dict(connected=True,busy=False,state='FAULT',common='unknown')):
            self.s.tick()
        self.assertEqual([],self.c.calls)
        self.assertEqual('paused',self.stored(run)['status'])

    def test_material_budget_is_frozen_reserved_and_exported(self):
        self.empty(1);run=self.auto_run(source_mass_budget_g={'13':1,'14':1,'15':2},reserve_mass_g=.2,min_initial_mass_g=1)
        self.start(run);self.s.tick();sample=self.complete()
        saved=self.stored(run)
        self.assertEqual({'13':1,'14':1,'15':2},saved['budgets']['source_mass_budget_g'])
        self.assertAlmostEqual(sample['recipe']['added_mass_g'],sum(saved['used']['source_mass_g'].values()))
        with zipfile.ZipFile(io.BytesIO(self.s.export_zip(self.p['id']))) as z:
            self.assertIn('additions.csv',z.namelist())
            self.assertIn('observations.csv',z.namelist())
            self.assertIn('action_events.csv',z.namelist())

    def test_invalid_observation_policy_rejected_without_motion(self):
        for policy in ({'mode':'magic'},{'max_auto_retries':-1},{'stable_frames':1},
                       {'mode':'automatic','single_phase_validated':True,'single_phase_evidence':''}):
            with self.assertRaises(ValueError):self.create_run(observation_policy=policy)
        self.assertEqual([],self.c.calls)

    def test_tie_line_requires_independent_method_and_does_not_replace_overall_composition(self):
        self.empty(1);run=self.auto_run();self.start(run);self.s.tick();sample=self.complete()
        body=dict(sample_id=sample['id'],upper=dict(peg_fraction=.2,salt_fraction=.02),
                  lower=dict(peg_fraction=.01,salt_fraction=.15),method='independent chemical assay',
                  evidence='synthetic external assay record',request_id='tie-test')
        old=copy.deepcopy(sample['recipe'])
        result=self.s.handle('tie_line',body)
        repeated=self.s.handle('tie_line',body)
        self.assertEqual(result,repeated)
        self.assertEqual(1,len(sample['tie_line_records']))
        self.assertEqual(old,sample['recipe'])
        self.assertNotIn('measured_composition',sample)
        with self.assertRaises(ValueError):self.s.handle('tie_line',dict(body,request_id='bad',evidence=''))
        sample['generation']=1
        with zipfile.ZipFile(io.BytesIO(self.s.export_zip(self.p['id']))) as z:
            rows=list(csv.DictReader(io.StringIO(z.read('tie_lines.csv').decode('utf-8-sig'))))
            self.assertEqual({'0'},{row['generation'] for row in rows})
            action_rows=list(csv.DictReader(io.StringIO(z.read('action_events.csv').decode('utf-8-sig'))))
            self.assertEqual({'0'},{row['generation'] for row in action_rows})
