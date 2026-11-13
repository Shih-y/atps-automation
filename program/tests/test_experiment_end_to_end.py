"""Two closed-loop rounds through the real scheduler/controller and fake pump.

PNG and phase labels are synthetic test fixtures, never experimental evidence.
"""
import base64
import tempfile
import time
import unittest
from pathlib import Path
from controller import Controller
from experiment_service import ExperimentService
from capture_store import timestamp
from fakes import DemoHardware
from test_phase_archive import test_png


class ClosedLoopTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'test_output';root.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=root)
        self.c=Controller(self.tmp.name,hardware_factory=lambda cfg:DemoHardware(cfg))
        self.lab=ExperimentService(self.c);self.c.lab=self.lab;self.c.connect()
        for source in (13,14,15):
            self.lab.handle('calibration',dict(source=source,groups_g=[.75,.74,.76],doses_per_group=5,
                aspirate_steps=45,aspirate_rpm=5,chase_steps=1000,chase_rpm=15,note='synthetic fixture only'))
        self.p=self.lab.handle('project',dict(name='synthetic test',salt_port=13,total_mass_g=.15,
            safe_mass_limit_g=1,temperature_c=25,photo_offsets_s=[0],min_settle_s=0,max_observe_s=1800))['project']
        self.rois=[dict(sample=i,x=(5-i)*20,y=0,width=20,height=40) for i in range(1,6)]
        self.heartbeat()
    def tearDown(self):
        self.lab.handle('pause',{})
        self.c.stop_keep_connection();self.lab.close();self.c.lab=None
        self.c.disconnect();self.tmp.cleanup()
    def heartbeat(self):
        self.lab.handle('camera',dict(ready=True,rois=self.rois,width=100,height=40))
    def wait_for(self,predicate):
        until=time.monotonic()+8
        while time.monotonic()<until:
            state=self.lab.snapshot()
            if predicate(state):return state
            time.sleep(.04)
        self.fail('scheduler timeout: '+str(self.lab.snapshot()))
    def capture_review(self,classification):
        self.heartbeat()
        state=self.wait_for(lambda s:any(j['status']=='due' for j in s['photo_jobs']))
        job=next(j for j in state['photo_jobs'] if j['status']=='due')
        result=self.lab.handle('capture',dict(job_id=job['id'],mapping_version=state['camera']['mapping_version'],
            png_base64=base64.b64encode(test_png()).decode(),captured_at=timestamp(),
            analysis=dict(method='synthetic-test-only',validated=False,rois=self.rois,
                          bottles=[dict(sample=i,classification='uncertain') for i in range(1,6)])))
        frame=result['frame']
        self.lab.handle('review',dict(sample_id=job['sample_id'],frame_id=frame['id'],
                                     classification=classification,clear=True,note='synthetic label only'))
        return job,frame
    def test_two_rounds_include_real_controller_execution_and_idempotent_image(self):
        self.lab.handle('enqueue',dict(project_id=self.p['id'],recipes=[dict(peg_fraction=.04,salt_fraction=.04)]))
        self.lab.handle('start',{})
        first=self.wait_for(lambda s:s['samples'][0]['status']=='settling')
        self.assertTrue(first['samples'][0]['progress']['completed'])
        job,frame=self.capture_review('single')
        again=self.lab.handle('capture',dict(job_id=job['id']))['frame']
        self.assertEqual(frame['id'],again['id'])
        self.lab.handle('pause',{})
        round1=self.lab.handle('suggest',dict(project_id=self.p['id'],count=1,step=.1))['round']
        self.assertEqual(1,len(round1['training_ids']))
        self.assertFalse(round1['model']['two_class_boundary'])
        self.lab.handle('approve',dict(round_id=round1['id'],indices=[0]))
        self.heartbeat();self.lab.handle('start',{})
        second=self.wait_for(lambda s:len([x for x in s['samples'] if x['status']=='settling'])==2)
        self.assertEqual(2,second['samples'][1]['bottle'])
        self.capture_review('two_phase')
        self.lab.handle('pause',{})
        round2=self.lab.handle('suggest',dict(project_id=self.p['id'],count=2,step=.1))['round']
        self.assertEqual(2,len(round2['training_ids']))
        self.assertTrue(round2['model']['two_class_boundary'])
        self.assertNotEqual(round1['id'],round2['id'])
        self.assertEqual([16,16],[m['port'] for m in self.c.hardware.moves if 'turns' in m])
        self.assertTrue(self.c.connected)
    def test_pending_samples_excluded_from_training_without_breaking_suggestions(self):
        self.lab.handle('enqueue',dict(project_id=self.p['id'],recipes=[dict(peg_fraction=.04,salt_fraction=.04)]))
        result=self.lab.handle('suggest',dict(project_id=self.p['id'],count=2,step=.1))['round']
        self.assertEqual([],result['training_ids'])


if __name__=='__main__':unittest.main()
