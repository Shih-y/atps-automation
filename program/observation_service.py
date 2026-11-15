"""Bounded observation lifecycle; no liquid motion and no inferred equilibrium."""
import copy
import secrets
from capture_store import timestamp, parse_time
from observation_policy import evaluate_observation, validate_observation_policy


class ObservationMixin:
    def _observation_begin(self, sample):
        policy = sample.get('observation_policy')
        if policy is None:
            return
        previous = sample.get('auto_observation')
        if previous:
            sample.setdefault('auto_observation_history', []).append(copy.deepcopy(previous))
        sample['auto_observation'] = dict(status='pending', generation=sample['generation'],
            classification='uncertain', reason_codes=['awaiting_photographs'], evidence_frame_ids=[],
            retries_used=0, policy=copy.deepcopy(policy), started_at=sample['mixed_at'])

    def _automatic_label_valid(self, sample, project):
        label = sample.get('label') or {}
        if label.get('source') != 'automatic_optical':
            return True
        policy = sample.get('observation_policy') or {}
        if policy.get('mode') != 'automatic' or label.get('generation') != sample.get('generation'):
            return False
        decision = evaluate_observation(sample, project, policy)
        return (decision['status'] == 'candidate'
                and decision['classification'] == label.get('classification')
                and decision.get('evidence_frame_ids') == label.get('evidence_frame_ids'))

    def _observation_tick(self, now):
        for sample in self.data['samples']:
            if (not sample.get('observation_policy') or not sample.get('mixed_at')
                    or sample['status'] not in ('settling', 'observation_complete')
                    or sample.get('released_at') or sample.get('continuation_replaced_at')):
                continue
            project = self._get('projects', sample['project_id'])
            policy = sample['observation_policy']
            observation = sample.setdefault('auto_observation', dict(status='pending', retries_used=0))
            if observation.get('generation', sample['generation']) != sample['generation']:
                self._observation_begin(sample)
                observation = sample['auto_observation']
            human = sample.get('label')
            if (human and human.get('source', 'human') == 'human'
                    and human.get('generation',0) == sample.get('generation',0)):
                observation.update(status='human_reviewed', classification=human['classification'],
                    evidence_frame_ids=[human['frame_id']], reason_codes=['human_review_authoritative'])
                continue
            decision = evaluate_observation(sample, project, policy)
            observation.update(copy.deepcopy(decision))
            observation['evidence_status'] = decision['status']
            if decision['status'] == 'candidate':
                sample['observation_result'] = 'optical_' + decision['classification'] + '_candidate'
                if policy['mode'] == 'automatic':
                    previous = sample.get('label')
                    same = previous and previous.get('source') == 'automatic_optical' and (
                        previous.get('evidence_frame_ids') == decision['evidence_frame_ids']
                        and previous.get('classification') == decision['classification'])
                    if not same:
                        if previous:
                            sample.setdefault('review_history', []).append(copy.deepcopy(previous))
                        sample['label'] = dict(classification=decision['classification'], clear=True,
                            frame_id=decision['evidence_frame_ids'][-1], source='automatic_optical',
                            evidence_frame_ids=copy.deepcopy(decision['evidence_frame_ids']),
                            version=len(sample.get('review_history', []))+1, reviewed_at=timestamp(),
                            generation=sample['generation'], method=decision['method'],
                            basis='engineering_optical_candidate_not_equilibrium_or_independent_assay',
                            policy=copy.deepcopy(policy))
                    observation.setdefault('decided_at', timestamp())
                continue
            # An uncertain image is not a terminal observation while scheduled
            # photos or allowed retries remain. Only the finite lifecycle below
            # sets terminal unknown, which the liquid scheduler may use to end.
            observation['status'] = 'pending'
            # New conflicting evidence must remove the old automatic label's
            # eligibility immediately, while keeping its historical version.
            if (sample.get('label') or {}).get('source') == 'automatic_optical':
                sample.setdefault('review_history', []).append(copy.deepcopy(sample['label']))
                sample['label'] = None
            mixed = parse_time(sample['mixed_at']).timestamp()
            maximum = sample.get('observation_max_s', project['max_observe_s'])
            deadline = mixed + maximum
            jobs = [j for j in self.data['photo_jobs'] if j['sample_id'] == sample['id']
                    and j['generation'] == sample['generation']]
            # A live heartbeat is not proof a particular task returned a frame.
            for job in jobs:
                if job['status'] == 'due' and now > job['due_at'] + max(30, policy['retry_interval_s']):
                    job.update(status='failed', failure_reason='到期照片在有限等待内未返回', failed_at=timestamp())
            pending = [j for j in jobs if j['status'] in ('waiting', 'due')]
            attempts = sum(bool(j.get('automatic_retry')) for j in jobs)
            observation['retries_used'] = attempts
            exhausted = attempts >= policy['max_auto_retries'] and not pending
            if now >= deadline or exhausted:
                code = 'observation_window_expired' if now >= deadline else 'retry_budget_exhausted'
                observation.update(status='unknown', classification='uncertain',
                    reason_codes=list(dict.fromkeys(decision.get('reason_codes', [])+[code])))
                observation.setdefault('finished_at', timestamp())
                sample['observation_result'] = 'unknown'
                self._set_state(sample, 'observation_complete')
                for job in pending:
                    job.update(status='missed', failure_reason='观察窗口结束；未采集到有效照片')
                continue
            if pending:
                continue
            due = max(now + policy['retry_interval_s'], mixed + project['min_settle_s'])
            if due > deadline:
                # Remain finite even when the retry interval exceeds remaining time.
                observation.update(status='unknown', classification='uncertain',
                    reason_codes=list(dict.fromkeys(decision.get('reason_codes', [])+['no_retry_within_window'])))
                observation.setdefault('finished_at', timestamp())
                sample['observation_result'] = 'unknown'
                self._set_state(sample, 'observation_complete')
                continue
            job = dict(id=secrets.token_hex(16), sample_id=sample['id'], bottle=sample['bottle'],
                generation=sample['generation'], offset_s=due-mixed, due_at=due, status='waiting',
                mapping_version=self.data['camera']['mapping_version'], automatic_retry=True,
                retry_index=attempts+1, retry_reason_codes=copy.deepcopy(decision.get('reason_codes', [])))
            self.data['photo_jobs'].append(job)
            observation.update(retries_used=attempts+1, next_capture_at=due)
