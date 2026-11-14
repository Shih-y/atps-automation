"""Real continuation solver/scheduler with in-memory fake motion, never hardware."""
import copy
import io
import json
import sqlite3
from contextlib import closing
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from capture_store import timestamp
from experiment_service import ExperimentService
from test_service_integrity import FakeController


class ContinuationServiceTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).parents[1]/'test_output';root.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=root)
        self.c=FakeController(self.tmp.name)
        with patch.object(ExperimentService,'_loop',return_value=None):
            self.s=ExperimentService(self.c)
        for source in (13,14,15):
            self.s.handle('calibration',dict(source=source,groups_g=[.75]*3,doses_per_group=5,
                aspirate_steps=45,aspirate_rpm=5,chase_steps=1000,chase_rpm=15,
                status='validated',min_steps=1,max_steps=45,sample_bottles=[1,2,3,4,5],
                validation_evidence='synthetic range only',bottle_scope_evidence='synthetic bottle scope only'))
        self.p=self.s.handle('project',dict(salt_port=13,total_mass_g=1,safe_mass_limit_g=3,
            photo_offsets_s=[0],min_settle_s=0,max_observe_s=1800))['project']
        self.online()
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def online(self):
        self.s.handle('camera',dict(ready=True,width=100,height=40,
            rois=[dict(sample=i,x=(i-1)*20,y=0,width=20,height=40) for i in range(1,6)]))
    def empty(self,*bottles):
        for bottle in bottles:self.s.handle('release',dict(bottle=bottle,confirmed_empty=True))
    def create_run(self,bottles=(1,),**extra):
        body=dict(project_id=self.p['id'],bottles=list(bottles),peg_min=.05,peg_max=.1,
            salt_min=.05,salt_max=.1,step=.05,round_budget=4,added_mass_budget_g=4,
            time_budget_s=3600,objective='boundary');body.update(extra)
        return self.s.handle('continuation_run',body)['run']
    def start(self,run):self.s.handle('continuation_start',dict(run_id=run['id']))
    def stored(self,run):return self.s._get('continuation_runs',run['id'])
    def last_sample(self):return self.s._get('samples',self.c.calls[-1]['experiment_sample_id'])
    def complete(self):
        payload=self.c.calls[-1];plan=dict(payload['plan'],completed=True,mixing_finished_at=timestamp())
        self.s.on_action_event('completed',payload,plan);self.c.busy=False
        return self.last_sample()
    def review(self,sample,classification='single',settle=1):
        frame=dict(id='synthetic-'+sample['id'],sample_id=sample['id'],generation=sample['generation'],settle_s=settle)
        sample['frames'].append(frame)
        self.s.handle('review',dict(sample_id=sample['id'],frame_id=frame['id'],classification=classification,clear=True))
        return frame

    def test_creation_and_preview_never_move_and_unconfirmed_empty_is_unknown(self):
        preview=self.s.handle('continuation_preview',dict(project_id=self.p['id'],bottle=1,peg_fraction=.05,salt_fraction=.05))['preview']
        self.assertFalse(preview['executable']);self.assertEqual([],self.c.calls)
        run=self.create_run();self.assertEqual('draft',run['status']);self.assertEqual([],self.c.calls)
        self.empty(1)
        preview=self.s.handle('continuation_preview',dict(project_id=self.p['id'],bottle=1,peg_fraction=.05,salt_fraction=.05))['preview']
        self.assertTrue(preview['executable'],preview['issues']);self.assertEqual([],self.c.calls)

    def test_two_rounds_same_bottle_new_ids_parent_and_cumulative_material(self):
        self.empty(1);run=self.create_run(round_budget=2);self.start(run);self.s.tick()
        first=self.complete();first_inventory=copy.deepcopy(self.s._bottle(1)['inventory']);frame=self.review(first)
        old_recipe=copy.deepcopy(first['recipe']);self.s.tick()
        second=self.complete()
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(first['id'],second['parent_sample_id'])
        self.assertEqual(first['id'],second['lineage_root_id']);self.assertEqual(1,second['bottle'])
        self.assertEqual(old_recipe,first['recipe']);self.assertEqual(frame['id'],first['frames'][0]['id'])
        inventory=self.s._bottle(1)['inventory']
        self.assertGreater(inventory['total_mass_g'],first_inventory['total_mass_g'])
        self.assertEqual(second['id'],inventory['sample_id']);self.assertTrue(inventory['calibration_valid'])
        self.assertAlmostEqual(first_inventory['total_mass_g']+second['recipe']['added_mass_g'],inventory['total_mass_g'])
        self.s.tick();self.assertEqual('budget_complete',self.stored(run)['status'])

    def test_other_bottle_runs_while_first_waits_for_review_and_initial_points_differ(self):
        self.empty(1,2);run=self.create_run((1,2),round_budget=3);self.start(run);self.s.tick();first=self.complete()
        self.s.tick();second=self.complete()
        self.assertEqual([1,2],[s['bottle'] for s in self.s.data['samples']])
        self.assertNotEqual(first['recipe']['estimated']['peg_fraction'],second['recipe']['estimated']['peg_fraction'])
        self.s.tick();self.assertEqual(2,len(self.c.calls));self.assertEqual('waiting_review',self.stored(run)['status'])
        self.review(first);self.s.tick();third=self.last_sample()
        self.assertEqual(1,third['bottle']);self.assertEqual(first['id'],third['parent_sample_id'])
        self.assertEqual([],second['frames'])

    def test_unknown_review_never_triggers_next_addition(self):
        self.empty(1);run=self.create_run();self.start(run);self.s.tick();first=self.complete();self.review(first,'uncertain')
        self.s.tick();self.assertEqual(1,len(self.c.calls));self.assertEqual('waiting_review',self.stored(run)['status'])

    def test_inventory_only_written_once_after_complete_not_progress(self):
        self.empty(1);run=self.create_run();self.start(run);self.s.tick();sample=self.last_sample()
        self.assertEqual(0,self.s._bottle(1)['inventory']['total_mass_g'])
        self.s.on_action_event('progress',self.c.calls[-1],dict(completed_commands=1))
        self.assertEqual(0,self.s._bottle(1)['inventory']['total_mass_g'])
        self.complete();inventory=copy.deepcopy(self.s._bottle(1)['inventory']);completed=self.stored(run)['used']['completed_rounds']
        history_count=len(sample['observation_history'])
        self.complete();self.assertEqual(inventory,self.s._bottle(1)['inventory'])
        self.assertEqual(completed,self.stored(run)['used']['completed_rounds'])
        self.assertEqual(history_count,len(sample['observation_history']))

    def test_stop_during_planning_cannot_claim_or_replay(self):
        self.empty(1);run=self.create_run();self.start(run);choose=self.s._continuation_choose
        def stopped(*args):
            result=choose(*args);self.s.handle('stop',{});return result
        with patch.object(self.s,'_continuation_choose',side_effect=stopped):self.s.tick()
        self.assertEqual([],self.c.calls);self.assertEqual([],self.stored(run)['sample_ids'])
        self.assertEqual(0,self.stored(run)['used']['rounds']);self.assertEqual('stopped',self.stored(run)['status'])

    def test_stop_in_motion_unknown_inventory_no_resume_or_late_commit(self):
        self.empty(1);run=self.create_run();self.start(run);self.s.tick();sample=self.last_sample()
        charged=self.stored(run)['used']['added_mass_g'];self.s.handle('stop',{})
        self.complete()
        self.assertFalse(self.s._bottle(1)['inventory']['known']);self.assertEqual('interrupted',sample['status'])
        self.assertEqual(charged,self.stored(run)['used']['added_mass_g'])
        with self.assertRaises(ValueError):self.s.handle('continuation_resume',dict(run_id=run['id']))
        self.s.tick();self.assertEqual(1,len(self.c.calls))

    def test_mass_and_time_budget_prevent_new_motion(self):
        self.empty(1);run=self.create_run(added_mass_budget_g=.2);self.start(run);self.s.tick()
        self.assertEqual([],self.c.calls);self.assertEqual('exhausted',self.stored(run)['status'])
        another=self.create_run(time_budget_s=1);self.start(another);self.s.tick()
        self.assertEqual([],self.c.calls);self.assertLessEqual(self.stored(another)['used']['added_mass_g'],4)

    def test_budget_expiry_does_not_stop_current_finite_action(self):
        self.empty(1);run=self.create_run();self.start(run);self.s.tick();stops=self.c.stops
        self.stored(run)['started_epoch']-=4000;self.s.tick()
        self.assertEqual('budget_complete',self.stored(run)['status']);self.assertEqual(stops,self.c.stops)
        self.complete();self.assertTrue(self.s._bottle(1)['inventory']['known'])
        self.s.tick();self.assertEqual(1,len(self.c.calls))

    def test_pause_resume_and_late_review_include_wall_clock(self):
        self.empty(1);run=self.create_run();self.start(run);self.s.tick();sample=self.complete()
        self.s.handle('continuation_pause',dict(run_id=run['id']));self.stored(run)['started_epoch']-=4000
        self.review(sample);self.s.handle('continuation_resume',dict(run_id=run['id']));self.s.tick()
        self.assertEqual(1,len(self.c.calls));self.assertEqual('budget_complete',self.stored(run)['status'])
        elapsed=self.s.snapshot()['continuation_runs'][0]['used']['elapsed_s']
        with patch('continuation_service.time.time',return_value=time.time()+500):
            self.assertEqual(elapsed,self.s.snapshot()['continuation_runs'][0]['used']['elapsed_s'])

    def test_restart_preserves_records_but_invalidates_occupied_inventory(self):
        self.empty(1);run=self.create_run();self.start(run);self.s.tick();sample=self.complete();self.review(sample)
        self.s.close()
        with patch.object(ExperimentService,'_loop',return_value=None):self.s=ExperimentService(self.c)
        self.online();self.c.busy=False;self.s.tick()
        self.assertFalse(self.s.data['running']);self.assertFalse(self.s._bottle(1)['inventory']['known'])
        self.assertEqual('interrupted',self.stored(run)['status']);self.assertEqual(1,len(self.c.calls))
        self.assertEqual(1,len(self.s._get('samples',sample['id'])['frames']))

    def test_manual_composition_change_unknown_and_parent_photos_unchanged(self):
        self.empty(1);run=self.create_run(round_budget=2);self.start(run);self.s.tick();first=self.complete();self.review(first)
        self.s.tick();second=self.complete();old=copy.deepcopy(first)
        self.s.before_manual(dict(action='dose',sample=1,source=15))
        self.assertFalse(self.s._bottle(1)['inventory']['known'])
        self.assertEqual(old,first);self.assertEqual('interrupted',second['status'])

    def test_run_creation_idempotency_and_export_lineage_closure(self):
        self.empty(1);run=self.create_run(round_budget=2,request_id='same-run');same=self.create_run(round_budget=2,request_id='same-run')
        self.assertEqual(run['id'],same['id']);self.start(run);self.s.tick();first=self.complete();self.review(first)
        self.s.tick();second=self.complete()
        with zipfile.ZipFile(io.BytesIO(self.s.export_zip(self.p['id']))) as archive:
            data=json.loads(archive.read('data.json'))
            self.assertEqual([run['id']],[r['id'] for r in data['continuation_runs']])
            self.assertEqual({first['id'],second['id']},{s['id'] for s in data['samples']})
            self.assertEqual(first['id'],data['samples'][1]['parent_sample_id'])
            self.assertEqual(second['id'],data['bottles'][0]['inventory']['sample_id'])

    def test_complete_uses_compiled_mass_not_mutable_estimated_cache(self):
        self.empty(1);run=self.create_run();self.start(run);self.s.tick();sample=self.last_sample()
        expected=sample['recipe']['estimated']['total_mass_g'];sample['recipe']['estimated']['total_mass_g']=99
        self.complete();self.assertAlmostEqual(expected,self.s._bottle(1)['inventory']['total_mass_g'])

    def test_manual_liquid_on_confirmed_empty_invalidates_even_without_sample(self):
        self.empty(1);self.assertIsNone(self.s._bottle(1)['sample_id'])
        self.s.before_manual(dict(action='read_status'));self.assertTrue(self.s._bottle(1)['inventory']['known'])
        self.s.before_manual(dict(action='manual_motion',port=1,rotation='cw',turns=1,rpm=5))
        self.assertFalse(self.s._bottle(1)['inventory']['known'])
        preview=self.s.handle('continuation_preview',dict(project_id=self.p['id'],bottle=1,peg_fraction=.05,salt_fraction=.05))['preview']
        self.assertFalse(preview['executable'])

    def test_manual_valve_selection_pauses_run_and_explicit_resume_works(self):
        self.empty(1);run=self.create_run();self.start(run)
        self.s.before_manual(dict(action='select_port',port=16))
        self.assertEqual('paused',self.stored(run)['status']);self.assertTrue(self.s._bottle(1)['inventory']['known'])
        self.s.handle('continuation_resume',dict(run_id=run['id']));self.s.tick();self.assertEqual(1,len(self.c.calls))

    def test_parent_cannot_receive_new_capture_retry_or_extension(self):
        self.empty(1);run=self.create_run(round_budget=2);self.start(run);self.s.tick();first=self.complete();self.review(first)
        old_job=next(j for j in self.s.data['photo_jobs'] if j['sample_id']==first['id'])
        self.s.tick()
        with self.assertRaises(ValueError):self.s.handle('capture',dict(job_id=old_job['id']))
        with self.assertRaises(ValueError):self.s.handle('retry_photo',dict(sample_id=first['id']))
        with self.assertRaises(ValueError):self.s.handle('retry_photo',dict(job_id=old_job['id']))
        with self.assertRaises(ValueError):self.s.handle('extend_observation',dict(sample_id=first['id'],max_observe_s=3600,reason='old stage'))

    def test_storage_failure_after_claim_does_not_send_motion_or_replay(self):
        self.empty(1);run=self.create_run();self.start(run)
        with patch.object(self.s,'_save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.s.tick()
        self.assertEqual([],self.c.calls);self.assertEqual('interrupted',self.stored(run)['status'])
        self.assertFalse(self.s._bottle(1)['inventory']['known']);self.s.tick();self.assertEqual([],self.c.calls)

    def test_schema2_upgrade_backs_up_and_never_invents_occupied_empty(self):
        self.empty(1);sample=self.s.handle('enqueue',dict(project_id=self.p['id'],recipes=[dict(peg_fraction=.05,salt_fraction=.05)]))['samples'][0]
        self.s.close();path=Path(self.tmp.name)/'experiments'/'experiments.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            state=json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            state['schema_version']=2;state.pop('continuation_runs');state.pop('active_continuation_run_id')
            for bottle in state['bottles']:bottle.pop('inventory');bottle.pop('inventory_history')
            db.execute('UPDATE state SET body=? WHERE id=1',(json.dumps(state),));db.commit()
        with patch.object(ExperimentService,'_loop',return_value=None):self.s=ExperimentService(self.c)
        self.assertEqual(3,self.s.data['schema_version']);self.assertFalse(self.s._bottle(1)['inventory']['known'])
        self.assertEqual(sample['id'],self.s._bottle(1)['sample_id'])
        backups=list((self.s.directory/'backups').glob('before-schema-3-*.sqlite3'));self.assertEqual(1,len(backups))
        with closing(sqlite3.connect(backups[0])) as db:
            before=json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0]);self.assertEqual(2,before['schema_version'])

    def test_independent_recipe_with_unknown_initial_bottle_cannot_invent_inventory(self):
        self.s.handle('enqueue',dict(project_id=self.p['id'],recipes=[dict(peg_fraction=.05,salt_fraction=.05)]))
        self.s.handle('start',dict(project_id=self.p['id']));self.s.tick();self.complete()
        self.assertFalse(self.s._bottle(1)['inventory']['known'])
        self.assertIn('空瓶起点',self.s._bottle(1)['inventory']['reason'])
        sample=self.last_sample();self.review(sample)
        self.assertFalse(self.s._eligible(sample,self.p))
        self.assertTrue(any(e['code']=='unknown_initial_inventory' and e['hard'] for e in sample['exclusions']))

    def test_independent_recipe_with_confirmed_empty_can_supply_continuation_parent(self):
        self.empty(1)
        self.s.handle('enqueue',dict(project_id=self.p['id'],recipes=[dict(peg_fraction=.05,salt_fraction=.05)]))
        self.s.handle('start',dict(project_id=self.p['id']));self.s.tick();sample=self.complete();self.review(sample)
        self.assertTrue(self.s._bottle(1)['inventory']['known']);self.s.tick()
        run=self.create_run(peg_min=.1,peg_max=.1,salt_min=.1,salt_max=.1,round_budget=1)
        self.start(run);self.s.tick();self.assertEqual(sample['id'],self.last_sample()['parent_sample_id'])

    def test_measured_fractions_do_not_validate_trial_absolute_delivery_mass(self):
        for calibration in self.s.data['calibrations']:calibration['status']='trial'
        self.p=self.s.handle('project',dict(salt_port=13,total_mass_g=1,safe_mass_limit_g=3,
            photo_offsets_s=[0],min_settle_s=0,max_observe_s=1800))['project']
        self.empty(1)
        self.s.handle('enqueue',dict(project_id=self.p['id'],recipes=[dict(peg_fraction=.05,salt_fraction=.05)]))
        self.s.handle('start',dict(project_id=self.p['id']));self.s.tick();sample=self.last_sample()
        sample['measured_composition']=dict(peg_fraction=.05,salt_fraction=.05,
            method='synthetic independent component assay',evidence='fraction measurement; no absolute mass evidence')
        self.complete();self.review(sample)
        self.assertTrue(self.s._eligible(sample,self.p))
        self.assertFalse(self.s._bottle(1)['inventory']['calibration_valid'])
        preview=self.s.handle('continuation_preview',dict(project_id=self.p['id'],bottle=1,peg_fraction=.1,salt_fraction=.1))['preview']
        self.assertFalse(preview['executable'])

    def test_round_freezes_model_parameters_and_pending_coverage(self):
        self.empty(1,2);run=self.create_run((1,2));self.start(run);self.s.tick();first=self.complete()
        self.s.tick();records=self.stored(run)['rounds']
        self.assertEqual(.08,records[1]['model_parameters']['rbf_length_scale'])
        self.assertEqual('heuristic_not_calibrated_probability',records[1]['model_parameters']['classification'])
        self.assertIn(first['id'],[v['sample_id'] for v in records[1]['model_parameters']['coverage_coordinates']])
        self.assertEqual([],records[1]['training_records'])
        self.assertFalse(self.last_sample()['is_independent_repeat'])
        self.assertEqual('additive_continuation',self.stored(run)['method_details']['sampling_strategy'])

    def test_budget_elapsed_includes_in_flight_tail_before_freezing(self):
        self.empty(1);run=self.create_run();self.start(run);self.s.tick()
        started=self.stored(run)['started_epoch'];deadline=started+3600
        with patch('continuation_service.time.time',return_value=deadline):
            self.s.tick()
            state=self.s.snapshot()['continuation_runs'][0]
            self.assertEqual('budget_complete',state['status']);self.assertTrue(state['finite_action_in_progress'])
            self.assertEqual(3600,state['used']['elapsed_s']);self.assertNotIn('finished_epoch',state)
        with patch('continuation_service.time.time',return_value=deadline+10):
            self.complete();state=self.s.snapshot()['continuation_runs'][0]
            self.assertEqual(3610,state['used']['elapsed_s']);self.assertFalse(state['finite_action_in_progress'])
            self.assertEqual(deadline,state['scheduling_stopped_epoch']);self.assertEqual(deadline+10,state['finished_epoch'])
        with patch('continuation_service.time.time',return_value=deadline+100):
            self.assertEqual(3610,self.s.snapshot()['continuation_runs'][0]['used']['elapsed_s'])

    def test_boolean_bottle_does_not_alias_physical_bottle_one(self):
        with self.assertRaises(ValueError):self.create_run((True,))
        with self.assertRaises(ValueError):self.s.handle('continuation_preview',dict(project_id=self.p['id'],bottle=True,peg_fraction=.1,salt_fraction=.1))
        self.assertEqual([],self.c.calls)

    def test_failed_start_save_is_paused_and_requires_explicit_resume(self):
        self.empty(1);run=self.create_run()
        request=dict(run_id=run['id'],request_id='start-write-failed')
        with patch.object(self.s,'_save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.s.handle('continuation_start',request)
        self.assertEqual('paused',self.stored(run)['status']);self.assertFalse(self.s.data['running'])
        self.assertNotIn('continuation_start:start-write-failed',self.s.data['idempotency'])
        with self.assertRaises(ValueError):self.s.handle('continuation_start',request)
        self.s.tick();self.assertEqual([],self.c.calls)
        self.s.handle('continuation_resume',dict(run_id=run['id'],request_id='explicit-recovery'))
        self.s.tick();self.assertEqual(1,len(self.c.calls))

    def test_busy_start_retry_returns_committed_result_without_device_recheck(self):
        self.empty(1);run=self.create_run()
        request=dict(run_id=run['id'],request_id='network-retry')
        first=self.s.handle('continuation_start',request);self.s.tick();self.assertTrue(self.c.busy)
        with patch.object(self.c,'snapshot',side_effect=AssertionError('cached request must not recheck busy')):
            repeated=self.s.handle('continuation_start',request)
        self.assertEqual(first,repeated);self.assertEqual(1,len(self.c.calls))
        self.assertEqual(1,self.stored(run)['used']['rounds'])

    def test_old_claim_cannot_borrow_new_same_project_run_dispatch(self):
        self.empty(1,2);a=self.create_run((1,));b=self.create_run((2,));self.start(a)
        original=self.s._save;triggered=[False]
        def save_then_switch():
            original()
            if not triggered[0] and self.stored(a)['sample_ids']:
                triggered[0]=True
                self.s.handle('continuation_pause',dict(run_id=a['id']))
                self.s.handle('continuation_start',dict(run_id=b['id']))
        with patch.object(self.s,'_save',side_effect=save_then_switch):self.s.tick()
        self.assertEqual([],self.c.calls);self.assertNotEqual('running',self.stored(a)['status'])
        self.assertTrue(self.s.data['running']);self.assertEqual(b['id'],self.s.data['active_continuation_run_id'])
        self.s.tick();self.assertEqual(1,len(self.c.calls));self.assertEqual(b['id'],self.last_sample()['continuation_run_id'])
        self.assertEqual(2,self.last_sample()['bottle'])

    def test_run_switch_inside_controller_start_failure_does_not_pause_new_run(self):
        self.empty(1,2);a=self.create_run((1,));b=self.create_run((2,));self.start(a)
        def switch_and_reject(payload):
            self.s.handle('continuation_pause',dict(run_id=a['id']))
            self.s.handle('continuation_start',dict(run_id=b['id']))
            raise ValueError('controller rejected old task before motion')
        with patch.object(self.c,'start',side_effect=switch_and_reject):self.s.tick()
        self.assertEqual([],self.c.calls);self.assertTrue(self.s.data['running'])
        self.assertEqual('running',self.stored(b)['status']);self.assertEqual(b['id'],self.s.data['active_continuation_run_id'])
        self.s.tick();self.assertEqual(b['id'],self.last_sample()['continuation_run_id'])

    def test_current_run_controller_start_failure_still_stops_its_dispatch(self):
        self.empty(1);run=self.create_run();self.start(run)
        with patch.object(self.c,'start',side_effect=RuntimeError('hardware failure')):self.s.tick()
        self.assertFalse(self.s.data['running']);self.assertTrue(self.s.cancel_dispatch.is_set())
        self.assertEqual('interrupted',self.stored(run)['status']);self.assertEqual([],self.c.calls)

    def test_time_budget_is_rechecked_after_claim_persistence_before_motion(self):
        self.empty(1);run=self.create_run();self.start(run);original=self.s._save
        clock=[time.time()]
        def delayed_save():
            original()
            if self.stored(run)['sample_ids']:clock[0]=self.stored(run)['started_epoch']+4000
        with patch('continuation_service.time.time',side_effect=lambda:clock[0]),patch.object(self.s,'_save',side_effect=delayed_save):
            self.s.tick()
        self.assertEqual([],self.c.calls);self.assertEqual('budget_complete',self.stored(run)['status'])
        self.assertEqual(1,self.stored(run)['used']['rounds'])
        self.s.tick();self.assertEqual([],self.c.calls)

    def test_old_independent_claim_cannot_borrow_new_same_project_batch(self):
        self.empty(1,2)
        first=self.s.handle('enqueue',dict(project_id=self.p['id'],recipes=[dict(peg_fraction=.05,salt_fraction=.05)]))['samples'][0]
        self.s.handle('start',dict(project_id=self.p['id']))
        original=self.s._save;triggered=[False];second=[]
        def save_then_switch():
            original()
            if not triggered[0] and self.s._get('samples',first['id'])['status']=='preparing':
                triggered[0]=True
                self.s.handle('pause',{})
                second.extend(self.s.handle('enqueue',dict(project_id=self.p['id'],recipes=[dict(peg_fraction=.1,salt_fraction=.1)]))['samples'])
                self.s.handle('start',dict(project_id=self.p['id']))
        with patch.object(self.s,'_save',side_effect=save_then_switch):self.s.tick()
        self.assertEqual([],self.c.calls);self.assertTrue(self.s.data['running'])
        self.assertNotIn(first['id'],self.s.data['active_batch']['sample_ids'])
        self.s.tick();self.assertEqual(1,len(self.c.calls));self.assertEqual(second[0]['id'],self.last_sample()['id'])
        self.complete();self.s.tick()
        self.s.handle('start',dict(project_id=self.p['id']))
        with patch.object(self.c,'start',side_effect=RuntimeError('later real hardware failure')):self.s.tick()
        self.assertEqual('interrupted',self.s._get('samples',first['id'])['status'])
        self.assertFalse(self.s.data['running'])


if __name__=='__main__':unittest.main()
