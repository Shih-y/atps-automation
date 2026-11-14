import io
import base64
import copy
import csv
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from experiment_service import ExperimentService
from capture_store import timestamp
from datetime import datetime, timezone
from test_phase_archive import test_png


class FakeController:
    def __init__(self, directory):
        self.directory=Path(directory)
        self.config=json.loads(Path(__file__).parents[1].joinpath('config.default.json').read_text(encoding='utf8'))
        self.connected=True;self.busy=False;self.stop_status='unknown';self.stops=0;self.calls=[]
    def snapshot(self):return dict(connected=self.connected,busy=self.busy,stop_status=self.stop_status)
    def stop_keep_connection(self):self.stops+=1;self.stop_status='confirmed'
    def start(self,payload):self.calls.append(payload);self.busy=True


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parents[1]/'test_output')
        self.c=FakeController(self.tmp.name)
        with patch.object(ExperimentService,'_loop',return_value=None):self.s=ExperimentService(self.c)
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def test_stop_reaches_hardware_when_database_closed(self):
        self.s.db.close()
        result=self.s.handle('stop',{})
        self.assertEqual(1,self.c.stops);self.assertEqual('confirmed',result['stop_status'])
    def test_stop_reaches_hardware_before_save_and_reports_failure_truthfully(self):
        with patch.object(self.s,'_save',side_effect=OSError('disk full')):
            result=self.s.handle('stop',{})
        self.assertEqual(1,self.c.stops);self.assertFalse(self.s.data['running'])
        self.assertEqual('confirmed',result['stop_status'])
        def failed():self.c.stops+=1;raise RuntimeError('no acknowledgement')
        self.c.stop_keep_connection=failed
        with self.assertRaises(RuntimeError):self.s.handle('stop',{})
        self.assertEqual('unconfirmed',self.s.snapshot()['stop_status'])
    def test_start_requires_project_id(self):
        with self.assertRaises(ValueError):self.s.handle('start',{})
    def test_stop_sends_command_before_waiting_for_state_lock(self):
        invoked=threading.Event();completed=threading.Event()
        def hardware_stop():invoked.set();self.c.stop_status='confirmed'
        self.c.stop_keep_connection=hardware_stop
        def request():
            self.s.handle('stop',{});completed.set()
        with self.s.lock:
            worker=threading.Thread(target=request);worker.start()
            self.assertTrue(invoked.wait(1));self.assertFalse(completed.is_set())
        worker.join(2);self.assertTrue(completed.is_set())
    def test_export_is_closed_over_selected_project(self):
        a=self.s.handle('project',dict(name='A',salt_port=13))['project']
        b=self.s.handle('project',dict(name='B',salt_port=12))['project']
        self.s.data['rounds']=[dict(id='ra',project_id=a['id']),dict(id='rb',project_id=b['id'])]
        with zipfile.ZipFile(io.BytesIO(self.s.export_zip(b['id']))) as z:
            d=json.loads(z.read('data.json'))
            self.assertEqual([b['id']],[p['id'] for p in d['projects']])
            self.assertEqual(['rb'],[r['id'] for r in d['rounds']])
            self.assertIn('metadata.json',z.namelist())
    def test_heartbeat_does_not_write_history(self):
        with patch.object(self.s,'_save') as save:
            self.s.handle('camera',dict(ready=False))
            self.s.handle('camera',dict(ready=False))
        self.assertEqual(0,save.call_count)

    def calibrate(self,ports=(13,14,15),groups=None,status='validated'):
        for port in ports:
            self.s.handle('calibration',dict(source=port,groups_g=groups or [.75,.74,.76],doses_per_group=5,
                aspirate_steps=45,aspirate_rpm=5,chase_steps=1000,chase_rpm=15,status=status,
                min_steps=1,max_steps=45,sample_bottles=[1,2,3,4,5],
                validation_evidence='synthetic dose-range study only',bottle_scope_evidence='synthetic cross-bottle study only'))
    def project(self,**extra):
        return self.s.handle('project',dict(name='fixture',salt_port=13,safe_mass_limit_g=2,**extra))['project']
    def enqueue(self,project,**extra):
        return self.s.handle('enqueue',dict(project_id=project['id'],recipes=[dict(peg_fraction=.05,salt_fraction=.05,**extra)]))['samples'][0]
    def sample(self,sample_id):return self.s._get('samples',sample_id)
    def online(self):
        self.rois=[dict(sample=i,x=(i-1)*20,y=0,width=20,height=40) for i in range(1,6)]
        self.s.handle('camera',dict(ready=True,rois=self.rois,width=100,height=40))
    def complete(self,sample,age=601):
        sample=self.sample(sample['id']);sample['status']='settling'
        sample['mixed_at']=datetime.fromtimestamp(time.time()-age,timezone.utc).isoformat()
        sample['frames']=[dict(id='frame-'+sample['id'],sample_id=sample['id'],generation=0,settle_s=600)]
        self.s.handle('review',dict(sample_id=sample['id'],frame_id=sample['frames'][0]['id'],classification='single',clear=True))
        return sample

    def test_start_B_only_dispatches_B_and_does_not_mutate_A(self):
        self.calibrate();a=self.project();b=self.project()
        sa=self.enqueue(a);sb=self.enqueue(b);self.online()
        self.s.handle('start',dict(project_id=b['id']));self.s.tick()
        self.assertEqual([sb['id']],[c['experiment_sample_id'] for c in self.c.calls])
        self.assertEqual('queued',self.sample(sa['id'])['status'])
        self.assertEqual(b['id'],self.s.snapshot()['active_project_id'])
        self.assertEqual([sb['id']],self.s.snapshot()['active_batch']['sample_ids'])

    def test_stop_cancels_start_that_was_already_in_flight(self):
        self.calibrate();project=self.project();self.enqueue(project);self.online()
        checked=threading.Event();release=threading.Event();errors=[]
        def snapshot():checked.set();release.wait(2);return dict(connected=True,busy=False)
        self.c.snapshot=snapshot
        def start():
            try:self.s.handle('start',dict(project_id=project['id']))
            except ValueError as error:errors.append(str(error))
        worker=threading.Thread(target=start);worker.start();self.assertTrue(checked.wait(1))
        self.s.handle('stop',{});release.set();worker.join(2)
        self.assertEqual(1,len(errors));self.assertFalse(self.s.data['running']);self.assertEqual([],self.c.calls)

    def test_batch_does_not_silently_absorb_newly_enqueued_samples(self):
        self.calibrate();project=self.project();first=self.enqueue(project);self.online()
        self.s.handle('start',dict(project_id=project['id']))
        second=self.enqueue(project,independent_repeat=True)
        self.s.tick();self.c.busy=False
        self.s.on_action_event('completed',dict(experiment_sample_id=first['id']),dict(mixing_finished_at=timestamp()))
        self.s.tick()
        self.assertEqual(1,len(self.c.calls));self.assertEqual('queued',self.sample(second['id'])['status'])
        self.assertFalse(self.s.data['running'])

    def test_failed_start_persistence_never_arms_motion(self):
        self.calibrate();project=self.project();self.enqueue(project);self.online()
        with patch.object(self.s,'_save',side_effect=OSError('full disk')):
            with self.assertRaises(OSError):self.s.handle('start',dict(project_id=project['id']))
        self.s.tick();self.assertEqual([],self.c.calls);self.assertFalse(self.s.data['running'])

    def test_project_can_disallow_trial_calibrations(self):
        self.calibrate(status='trial');project=self.project(allow_trial_calibration=False)
        preview=self.s.handle('preview',dict(project_id=project['id'],peg_fraction=.05,salt_fraction=.05))['preview']
        self.assertFalse(preview['executable'])

    def test_common_only_aspiration_does_not_disturb_sample(self):
        self.calibrate();project=self.project();sample=self.complete(self.enqueue(project))
        self.s.before_manual(dict(action='aspirate',sample=1,source=14))
        self.assertEqual(0,sample['generation']);self.assertTrue(self.s._eligible(sample,project))

    def test_common_maintenance_does_not_invalidate_samples(self):
        self.calibrate();project=self.project();sample=self.complete(self.enqueue(project))
        for payload in (dict(action='tubes',operation='refill',sources=[13]),dict(action='tubes',operation='drain_sources',sources=[13]),dict(action='bench',operation='fill_water')):
            self.s.before_manual(payload)
        self.assertEqual(0,sample['generation']);self.assertTrue(self.s._eligible(sample,project))

    def test_sample_drain_only_invalidates_selected_bottles(self):
        self.calibrate();project=self.project();a=self.complete(self.enqueue(project));b=self.complete(self.enqueue(project,independent_repeat=True))
        self.s.before_manual(dict(action='tubes',operation='drain_samples',samples=[2]))
        self.assertEqual(0,a['generation']);self.assertEqual(1,b['generation'])

    def test_conflicting_replicates_are_visible_and_not_training(self):
        self.calibrate();project=self.project();a=self.complete(self.enqueue(project));b=self.complete(self.enqueue(project,independent_repeat=True))
        self.s.handle('review',dict(sample_id=b['id'],frame_id=b['frames'][0]['id'],classification='two_phase',clear=True))
        self.assertFalse(self.s._eligible(a,project));self.assertFalse(self.s._eligible(b,project))
        model=self.s.handle('suggest',dict(project_id=project['id'],count=1))['round']
        self.assertEqual([],model['training_ids']);self.assertEqual(1,model['summary']['conflict_count'])
        self.assertEqual({a['id'],b['id']},set(model['conflicts'][0]['sample_ids']))

    def test_backup_is_frozen_and_all_data_keeps_unreferenced_calibration(self):
        self.calibrate((10,))
        result=self.s.handle('backup',{})['backup'];path=self.s.backup_path(result['id'])
        with zipfile.ZipFile(path) as archive:
            data=json.loads(archive.read('data.json'))
            self.assertEqual(1,len(data['calibrations']));self.assertEqual(6,len(data['stocks']))
        self.assertEqual('/api/lab/backup/'+result['id'],result['download_url'])
        with self.assertRaises(ValueError):self.s.backup_path('../experiments')

    def test_unrelated_stock_and_calibration_do_not_invalidate_project(self):
        self.calibrate();project=self.project();self.enqueue(project)
        unused=next(s for s in self.s.data['stocks'] if s['port']==10)
        self.s.handle('stock',dict(unused,batch='new unrelated batch'))
        self.calibrate((10,));self.online();self.s.handle('start',dict(project_id=project['id']));self.s.tick()
        self.assertEqual(1,len(self.c.calls))
        self.assertEqual([],self.s._compatibility_issues(project))

    def test_used_source_change_requires_new_project_version(self):
        self.calibrate();project=self.project();self.enqueue(project)
        used=next(s for s in self.s.data['stocks'] if s['port']==13)
        self.s.handle('stock',dict(used,batch='changed used source'))
        self.online()
        with self.assertRaises(ValueError):self.s.handle('start',dict(project_id=project['id']))
        new=self.s.handle('project_version',dict(project_id=project['id']))['project']
        self.assertNotEqual(project['id'],new['id']);self.assertEqual(project['id'],new['parent_project_id'])
        self.assertEqual('',next(s for s in project['stocks'] if s['port']==13)['batch'])

    def test_estimate_coordinates_identical_in_model_snapshot_and_export(self):
        self.calibrate(groups=[22.5]*3);project=self.project();sample=self.complete(self.enqueue(project))
        sample['recipe']['estimated']['peg_fraction']=.99  # compiled steps remain authoritative
        model=self.s.handle('suggest',dict(project_id=project['id'],count=1))['round']
        coordinates=model['training_records'][0]['composition']
        self.assertEqual('estimated',coordinates['basis']);self.assertAlmostEqual(.04,coordinates['peg_fraction'])
        self.assertAlmostEqual(.04,self.s.snapshot()['samples'][0]['composition']['peg_fraction'])
        with zipfile.ZipFile(io.BytesIO(self.s.export_zip(project['id']))) as z:
            row=next(csv.DictReader(io.StringIO(z.read('samples.csv').decode('utf-8-sig'))))
            self.assertEqual('estimated',row['composition_basis']);self.assertAlmostEqual(.04,float(row['adopted_peg_fraction']))
            self.assertEqual('.05',row['target_peg_fraction'].replace('0.05','.05'))

    def test_next_day_review_uses_capture_window_not_review_clock(self):
        self.calibrate();project=self.project();sample=self.enqueue(project)
        stored=self.sample(sample['id']);stored['status']='settling'
        stored['mixed_at']=datetime.fromtimestamp(time.time()-86400,timezone.utc).isoformat()
        stored['frames']=[dict(id='valid-frame',generation=0,settle_s=600)]
        self.s.tick()
        self.assertEqual('observation_complete',stored['status'])
        self.s.handle('review',dict(sample_id=stored['id'],frame_id='valid-frame',classification='single',clear=True))
        self.assertTrue(self.s._eligible(stored,project));self.assertFalse(stored['exclusion'])

    def test_single_project_export_includes_only_its_jobs_images_and_history(self):
        self.calibrate();a=self.project();b=self.project();sa=self.complete(self.enqueue(a));sb=self.complete(self.enqueue(b))
        for sample in (sa,sb):
            frame=sample['frames'][0];(self.s.directory/(frame['id']+'.png')).write_bytes(b'fixture')
            self.s.data['photo_jobs'].append(dict(id='job-'+sample['id'],sample_id=sample['id'],status='captured'))
        with zipfile.ZipFile(io.BytesIO(self.s.export_zip(b['id']))) as z:
            data=json.loads(z.read('data.json'))
            self.assertEqual([sb['id']],[s['id'] for s in data['samples']])
            self.assertEqual([sb['id']],[j['sample_id'] for j in data['photo_jobs']])
            self.assertEqual(1,len([n for n in z.namelist() if n.startswith('images/')]))
            self.assertFalse(any(sa['id'] in n for n in z.namelist()))
            self.assertEqual({13,14,15},{s['port'] for s in data['stocks']})
            self.assertEqual({c['id'] for c in b['calibrations']},{c['id'] for c in data['calibrations']})

    def test_enqueue_idempotency_and_different_payload_rejection(self):
        self.calibrate();project=self.project()
        request=dict(project_id=project['id'],request_id='same-submit',recipes=[dict(peg_fraction=.05,salt_fraction=.05)])
        first=self.s.handle('enqueue',request);again=self.s.handle('enqueue',request)
        self.assertEqual(first,again);self.assertEqual(1,len(self.s.data['samples']))
        changed=copy.deepcopy(request);changed['recipes'][0]['peg_fraction']=.06
        with self.assertRaises(ValueError):self.s.handle('enqueue',changed)

    def test_idempotent_retry_after_failed_save_retries_persistence_without_duplicates(self):
        self.calibrate();project=self.project()
        request=dict(project_id=project['id'],request_id='storage-retry',recipes=[dict(peg_fraction=.05,salt_fraction=.05)])
        with patch.object(self.s,'_save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.s.handle('enqueue',request)
            with self.assertRaises(OSError):self.s.handle('enqueue',request)
        self.assertEqual(1,len(self.s.data['samples']))
        recovered=self.s.handle('enqueue',request)
        stored=json.loads(self.s.db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
        self.assertEqual([recovered['samples'][0]['id']],[s['id'] for s in stored['samples']])
        self.assertIn('enqueue:storage-retry',stored['idempotency'])

    def test_equal_integer_actions_require_explicit_independent_repeat(self):
        self.calibrate(groups=[22.5]*3);project=self.project();self.enqueue(project)
        with self.assertRaises(ValueError):self.s.handle('enqueue',dict(project_id=project['id'],recipes=[dict(peg_fraction=.0499,salt_fraction=.0499)]))
        rows=self.s.handle('enqueue',dict(project_id=project['id'],recipes=[dict(peg_fraction=.0499,salt_fraction=.0499,independent_repeat=True)]))['samples']
        self.assertEqual(1,len(rows));self.assertTrue(rows[0]['same_executable_point_as'])

    def test_approve_retry_does_not_create_more_samples(self):
        self.calibrate();project=self.project()
        model=self.s.handle('suggest',dict(project_id=project['id'],count=1,step=.1))['round']
        request=dict(round_id=model['id'],indices=[0])
        one=self.s.handle('approve',request);two=self.s.handle('approve',request)
        self.assertEqual(one,two);self.assertEqual(1,len(self.s.data['samples']))

    def test_suggestions_exclude_targets_quantized_to_existing_commands(self):
        self.calibrate(groups=[22.5]*3);project=self.project();existing=self.enqueue(project)
        model=self.s.handle('suggest',dict(project_id=project['id'],count=2,peg_range=[.049,.05],salt_range=[.05,.05],step=.001))['round']
        self.assertEqual([],model['suggestions'])
        self.assertGreater(model['candidate_filter']['existing_execution'],0)

    def test_suggestion_round_deduplicates_compiled_actions_and_freezes_basis(self):
        self.calibrate(groups=[22.5]*3);project=self.project()
        model=self.s.handle('suggest',dict(project_id=project['id'],count=5,peg_range=[.049,.05],salt_range=[.049,.05],step=.001))['round']
        self.assertEqual(1,len(model['suggestions']))
        candidate=model['suggestions'][0]
        self.assertEqual('estimated',candidate['composition']['basis'])
        self.assertAlmostEqual(.04,candidate['composition']['peg_fraction'])
        self.assertEqual(candidate['composition']['peg_fraction'],candidate['quantized_target']['peg_fraction'])
        self.assertEqual(candidate['execution_key'],candidate['expected_execution_key'])
        self.assertIn('items',candidate['compiled_plan'])
        approved=self.s.handle('approve',dict(round_id=model['id'],indices=[0]))['samples'][0]
        self.assertEqual(candidate['execution_key'],approved['execution_key'])

    def test_suggestions_filter_unexecutable_quantized_candidates(self):
        self.calibrate(groups=[22.5]*3);project=self.project(composition_tolerance_fraction=.005)
        model=self.s.handle('suggest',dict(project_id=project['id'],count=1,peg_range=[.05,.05],salt_range=[.05,.05]))['round']
        self.assertEqual([],model['suggestions'])
        self.assertEqual(1,model['candidate_filter']['unexecutable'])

    def test_cancel_queued_releases_without_motion(self):
        self.calibrate();project=self.project();sample=self.enqueue(project)
        self.s.handle('cancel',dict(sample_id=sample['id']))
        self.assertIsNone(self.s.data['bottles'][0]['sample_id']);self.assertEqual([],self.c.calls)
        self.assertEqual('cancelled',self.sample(sample['id'])['status'])

    def test_stop_and_restart_does_not_resume_and_hard_exclusion_not_clearable(self):
        self.calibrate();project=self.project();sample=self.enqueue(project);self.online()
        self.s.handle('start',dict(project_id=project['id']));self.s.tick();self.s.handle('stop',{})
        stored=self.sample(sample['id']);self.assertEqual('interrupted',stored['status'])
        exclusion=stored['exclusions'][0]
        with self.assertRaises(ValueError):self.s.handle('correct_exclusion',dict(sample_id=stored['id'],exclusion_id=exclusion['id'],reason='clear'))
        self.s.close()
        with patch.object(ExperimentService,'_loop',return_value=None):self.s=ExperimentService(self.c)
        self.c.busy=False;self.s.tick()
        self.assertEqual(1,len(self.c.calls));self.assertFalse(self.s.data['running'])
        self.assertEqual('interrupted',self.sample(sample['id'])['status'])

    def test_soft_exclusion_correction_is_audited(self):
        self.calibrate();project=self.project();sample=self.complete(self.enqueue(project))
        self.s.handle('exclude',dict(sample_id=sample['id'],reason='mistaken reflection'))
        exclusion=sample['exclusions'][0]
        self.s.handle('correct_exclusion',dict(sample_id=sample['id'],exclusion_id=exclusion['id'],reason='reviewed original frame'))
        self.assertFalse(exclusion['active']);self.assertTrue(exclusion['correction_reason'])
        self.assertTrue(self.s._eligible(sample,project))

    def test_whole_bottle_weight_cannot_become_measured_composition(self):
        self.calibrate();project=self.project();sample=self.enqueue(project)
        with self.assertRaises(ValueError):self.s.handle('measured_composition',dict(sample_id=sample['id'],peg_fraction=.05,salt_fraction=.05,method='weigh bottle',evidence='1g',measurement_kind='total_mass'))

    def test_new_observation_generation_preserves_old_images_but_rejects_old_evidence(self):
        self.calibrate();project=self.project();sample=self.complete(self.enqueue(project));old=copy.deepcopy(sample['frames'])
        self.s.before_manual(dict(action='bubble',sample=sample['bottle']))
        self.s.on_action_event('completed',dict(action='bubble',sample=sample['bottle']))
        self.assertEqual(old,sample['frames']);self.assertFalse(self.s._eligible(sample,project))
        self.assertTrue(all(j['generation']==1 for j in self.s.data['photo_jobs']))

    def test_photo_failure_is_isolated_and_retry_mapping_is_versioned(self):
        self.calibrate();project=self.project();a=self.enqueue(project);b=self.enqueue(project,independent_repeat=True)
        for item in (a,b):
            self.s._mix_completed(self.sample(item['id']),datetime.fromtimestamp(time.time()-601,timezone.utc).isoformat())
        self.online();self.s.tick();jobs=[j for j in self.s.data['photo_jobs'] if j['status']=='due']
        self.s.handle('photo_failure',dict(job_id=jobs[0]['id'],reason='disk error'))
        self.assertEqual('due',jobs[-1]['status'])
        self.rois[0]['width']=19;self.s.handle('camera',dict(ready=True,rois=self.rois,width=100,height=40))
        replacement=self.s.handle('retry_photo',dict(job_id=jobs[0]['id']))['job']
        self.assertNotEqual(jobs[0]['mapping_version'],replacement['mapping_version'])
        self.assertEqual(jobs[0]['sample_id'],replacement['sample_id'])

    def test_explicit_observation_extension_retains_original_plan(self):
        self.calibrate();project=self.project();sample=self.complete(self.enqueue(project))
        self.s.handle('extend_observation',dict(sample_id=sample['id'],max_observe_s=3600,offsets_s=[2400],reason='delayed phase separation'))
        self.assertEqual(1800,project['max_observe_s']);self.assertEqual(3600,sample['observation_max_s'])
        self.assertEqual(1800,sample['observation_history'][-1]['old_max_s'])

    def test_legacy_schema_backup_precedes_upgrade(self):
        self.s.close();path=Path(self.tmp.name)/'experiments'/'experiments.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            data=json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0]);data.pop('schema_version')
            db.execute('UPDATE state SET body=? WHERE id=1',(json.dumps(data),));db.commit()
        with patch.object(ExperimentService,'_loop',return_value=None):self.s=ExperimentService(self.c)
        backups=list((self.s.directory/'backups').glob('before-schema-*.sqlite3'))
        self.assertEqual(1,len(backups))
        with closing(sqlite3.connect(backups[0])) as backup:
            previous=json.loads(backup.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            self.assertNotIn('schema_version',previous)
        self.assertEqual(3,self.s.data['schema_version'])


if __name__=='__main__':unittest.main()
