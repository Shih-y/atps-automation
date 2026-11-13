import tempfile,unittest,sys,zipfile,io
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from experiment_service import ExperimentService
class Fake:
 def __init__(self,path):self.directory=path;self.config={};self.stopped=0
 def snapshot(self):return dict(connected=False,busy=False)
 def stop_keep_connection(self):self.stopped+=1
class ServiceTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]);self.c=Fake(self.tmp.name);self.s=ExperimentService(self.c)
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def test_restart_preserves_lease_and_interrupts(self):
  self.s.data['samples'].append(dict(id='a',status='preparing',exclusion=''));self.s.data['bottles'][0]['sample_id']='a';self.s._save();self.s.close();self.s=ExperimentService(self.c)
  self.assertEqual(self.s.snapshot()['samples'][0]['status'],'interrupted');self.assertEqual(self.s.snapshot()['bottles'][0]['sample_id'],'a');self.assertFalse(self.s.snapshot()['running'])
 def test_stop_retains_lease(self):
  self.s.data['bottles'][0]['sample_id']='a';self.s.handle('stop',{});self.assertEqual(self.c.stopped,1);self.assertEqual(self.s.snapshot()['bottles'][0]['sample_id'],'a')
 def test_export_and_snapshot_isolation(self):
  d=self.s.snapshot();d['stocks'].clear();self.assertTrue(self.s.snapshot()['stocks'])
  z=zipfile.ZipFile(io.BytesIO(self.s.export_zip()));self.assertEqual(set(z.namelist()),{'data.json','samples.csv','report.html'})
 def test_bad_camera_rejected(self):
  with self.assertRaises(ValueError):self.s.handle('camera',dict(ready=True,rois=[]))
 def sample(self,status='settling'):
  sample=dict(id='a',project_id='p',bottle=1,status=status,target={},recipe={},mixed_at=None,frames=[],label=None,exclusion='',generation=0)
  self.s.data['samples'].append(sample);self.s.data['bottles'][0]['sample_id']='a';return sample
 def test_missing_id_valueerror(self):
  with self.assertRaises(ValueError):self.s._get('samples','absent')
 def test_release_requires_confirmation(self):
  self.sample()
  with self.assertRaises(ValueError):self.s.handle('release',dict(bottle=1))
 def test_release_preparing_rejected(self):
  self.sample('preparing')
  with self.assertRaises(ValueError):self.s.handle('release',dict(bottle=1,confirmed_empty=True))
 def test_release_cancels_photo(self):
  self.sample();self.s.data['photo_jobs'].append(dict(id='j',sample_id='a',status='due'))
  self.s.handle('release',dict(bottle=1,confirmed_empty=True));self.assertEqual(self.s.data['photo_jobs'][0]['status'],'cancelled')
 def test_valve_does_not_disturb(self):
  a=self.sample();self.s.before_manual(dict(action='select_port',port=1));self.assertEqual(a['generation'],0)
 def test_targeted_disturbance(self):
  a=self.sample();self.s.before_manual(dict(action='bubble',sample=2));self.assertFalse(a['exclusion'])
  self.s.before_manual(dict(action='bubble',sample=1));self.assertTrue(a['exclusion']);self.assertEqual(a['generation'],1)
 def test_queued_untouched(self):
  a=self.sample('queued');self.s.before_manual(dict(action='bubble',sample=1));self.assertEqual(a['generation'],1)
 def test_failed_preserves_lease(self):
  a=self.sample('preparing');self.s.on_action_event('failed',dict(experiment_sample_id='a'),error='failure');self.assertEqual(a['status'],'interrupted');self.assertEqual(self.s.data['bottles'][0]['sample_id'],'a')
 def test_completed_requires_mixing_evidence(self):
  a=self.sample('preparing');self.s.on_action_event('completed',dict(experiment_sample_id='a'),plan={});self.assertEqual(a['status'],'interrupted');self.assertEqual(self.s.data['photo_jobs'],[])
 def test_start_disconnected_rejected(self):
  with self.assertRaises(ValueError):self.s.handle('start',{})
 def test_old_generation_retry_rejected(self):
  a=self.sample();a['generation']=1;self.s.data['photo_jobs'].append(dict(id='j',sample_id='a',status='missed',generation=0))
  with self.assertRaises(ValueError):self.s.handle('retry_photo',dict(job_id='j'))
 def project(self):
  p=dict(id='p',salt_port=13,temperature_c=25,total_mass_g=1,safe_mass_limit_g=2,stocks=self.s.data['stocks'],calibrations=[],photo_offsets_s=[60,300],min_settle_s=300,max_observe_s=1800);self.s.data['projects'].append(p);return p
 def test_independent_mix_timing(self):
  self.project();a=self.sample('preparing');b=dict(a,id='b',bottle=2,frames=[]);self.s.data['samples'].append(b)
  self.s.on_action_event('completed',dict(experiment_sample_id='a'),dict(mixing_finished_at='2026-11-14T10:00:00+00:00'))
  self.s.on_action_event('completed',dict(experiment_sample_id='b'),dict(mixing_finished_at='2026-11-14T10:01:00+00:00'))
  jobs=self.s.data['photo_jobs'];self.assertEqual(jobs[2]['due_at']-jobs[0]['due_at'],60);self.assertEqual(len(jobs),4)
 def test_two_round_feedback(self):
  self.project();a=self.sample();a['target']=dict(peg_fraction=.1,salt_fraction=.1);a['mixed_at']='2026-11-14T10:00:00+00:00';a['frames']=[dict(id='f',generation=0,settle_s=300)];self.s.handle('review',dict(sample_id='a',frame_id='f',clear=True,classification='single'))
  r=self.s.handle('suggest',dict(project_id='p',count=2,step=.1))['round'];self.assertEqual(r['training_ids'],['a']);self.assertFalse(r['model']['two_class_boundary'])
  b=dict(a,id='b',target=dict(peg_fraction=.2,salt_fraction=.1),label=dict(frame_id='f',clear=True,classification='two_phase'));self.s.data['samples'].append(b)
  r2=self.s.handle('suggest',dict(project_id='p',count=2,step=.1))['round'];self.assertEqual(set(r2['training_ids']),{'a','b'});self.assertTrue(r2['model']['two_class_boundary']);self.assertNotEqual(r['id'],r2['id'])
 def test_released_capture_rejected(self):
  a=self.sample('released');self.s.data['camera'].update(ready=True,heartbeat=__import__('time').time());self.s.data['photo_jobs'].append(dict(id='j',sample_id='a',status='cancelled',generation=0))
  with self.assertRaises(ValueError):self.s.handle('capture',dict(job_id='j'))
 def test_failed_capture_keeps_job_retryable(self):
  a=self.sample();a['mixed_at']='2026-11-14T10:00:00+00:00';self.s.data['camera'].update(ready=True,heartbeat=__import__('time').time(),mapping_version=1);j=dict(id='j',sample_id='a',status='due',generation=0,mapping_version=1,offset_s=0);self.s.data['photo_jobs'].append(j)
  with self.assertRaises(ValueError):self.s.handle('capture',dict(job_id='j',mapping_version=1,captured_at='invalid',png_base64='bad'))
  self.assertEqual(j['status'],'due');self.assertEqual(a['frames'],[])

 def test_nested_manual_remix_restarts_independent_photo_clock(self):
  self.project();a=self.sample();a['mixed_at']='2026-11-14T10:00:00+00:00'
  action=dict(action='bubble',debug_parameters=dict(sample=1))
  self.s.before_manual(action);self.s.on_action_event('completed',action)
  self.assertNotEqual('2026-11-14T10:00:00+00:00',a['mixed_at'])
  self.assertEqual(2,len(self.s.data['photo_jobs']))
  self.assertTrue(all(j['generation']==a['generation'] for j in self.s.data['photo_jobs']))

 def test_repeated_retry_reuses_replacement(self):
  self.project();a=self.sample();self.s.data['camera']['mapping_version']=2
  self.s.data['photo_jobs'].append(dict(id='j',sample_id='a',status='missed',generation=0,mapping_version=1))
  one=self.s.handle('retry_photo',dict(job_id='j'))['job']
  two=self.s.handle('retry_photo',dict(job_id='j'))['job']
  self.assertEqual(one['id'],two['id']);self.assertEqual(2,len(self.s.data['photo_jobs']))
 def test_exclude_empty_rejected(self):
  self.sample()
  with self.assertRaises(ValueError):self.s.handle('exclude',dict(sample_id='a',reason=' '))
 def test_retry_rebinds_mapping_with_history(self):
  self.sample();j=dict(id='j',sample_id='a',status='missed',generation=0,mapping_version=1);self.s.data['photo_jobs'].append(j);self.s.data['camera']['mapping_version']=2
  result=self.s.handle('retry_photo',dict(job_id='j'));self.assertEqual(j['status'],'superseded');self.assertEqual(result['job']['mapping_version'],2);self.assertNotEqual(result['job']['id'],'j')
 def test_nested_queued_disturbance(self):
  a=self.sample('queued');self.s.before_manual(dict(action='dose',debug_parameters=dict(sample=1)));self.assertEqual(a['status'],'interrupted')
 def test_wash_does_not_disturb_settling(self):
  a=self.sample();self.s.before_manual(dict(action='wash'));self.assertFalse(a['exclusion'])
 def test_review_released_archived_frame(self):
  self.project();a=self.sample('released');a['mixed_at']='2026-11-14T10:00:00+00:00';a['frames']=[dict(id='f',generation=0,settle_s=300)]
  self.s.handle('review',dict(sample_id='a',classification='single',frame_id='f',clear=True));self.assertTrue(self.s._eligible(a,self.s.data['projects'][0]))
 def test_cached_label_generation_invalid(self):
  self.project();a=self.sample();a.update(mixed_at='2026-11-14T10:00:00+00:00',frames=[dict(id='f',generation=0,settle_s=300)],label=dict(frame_id='f',classification='single',clear=True,eligible=True));a['generation']=1;a['target']=dict(peg_fraction=.1,salt_fraction=.1)
  r=self.s.handle('suggest',dict(project_id='p',count=1))['round'];self.assertEqual(r['training_ids'],[])
 def test_invalid_project_offsets(self):
  for offsets in ([],[1,1],[1801]):
   with self.assertRaises(ValueError):self.s.handle('project',dict(photo_offsets_s=offsets))
if __name__=='__main__':unittest.main()
