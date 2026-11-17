import copy
import threading
import unittest
from controller import Controller
import test_air_controller as base


class ManualBatchTests(unittest.TestCase):
    setUp=base.AirControllerTest.setUp
    tearDown=base.AirControllerTest.tearDown

    def payload(self,**changes):
        self.c.config.update(free_debug=True,sample_port_map={'1':1,'2':3,'3':4,'4':6,'5':7})
        body=dict(action='batch_recipe',request_id='test-batch',recipes=[
            dict(sample=2,items=[dict(source=14,volume_ml=1.)]),
            dict(sample=5,items=[dict(source=13,volume_ml=.3),dict(source=15,volume_ml=.2)])])
        body.update(changes);return body

    def run_batch(self,body=None):
        self.c.start(body or self.payload());self.c.worker.join(5)
        self.assertFalse(self.c.busy)
        return self.c.snapshot().get('manual_batch_run')

    def test_sequential_complete_batch_maps_ports_and_cleans_once_per_bottle(self):
        run=self.run_batch()
        self.assertEqual([14,3,3,15,13,7,15,7,7,15],[m['port'] for m in self.c.hardware.moves])
        self.assertTrue(run['completed']);self.assertEqual(2,run['completed_bottles'])
        self.assertEqual(10,run['completed_commands'])
        self.assertEqual([2,5],[p['sample'] for p in run['recipes']])
        self.assertEqual(714,run['recipes'][0]['items'][0]['total_steps'])
        self.assertIsNone(run['recipes'][1]['actual_ml'])

    def test_all_bottles_validated_before_any_motion(self):
        for recipes in (None,[],[None], [dict(sample=6,items=[dict(source=15,volume_ml=.2)])],
                        [dict(sample=1,items=[dict(source=15,volume_ml=.2)]),dict(sample=1,items=[dict(source=13,volume_ml=.2)])],
                        [dict(sample=1,items=[dict(source=15,volume_ml=.2)]),dict(sample=2,items=[dict(source=14,volume_ml=3.)])]):
            with self.subTest(recipes=recipes):
                self.run_batch(self.payload(recipes=recipes))
                self.assertEqual([],self.c.hardware.moves)
                self.assertIn('操作未执行',self.c.message)
                self.assertTrue(self.c.connected)

    def test_preview_is_offline_and_does_not_create_running_batch(self):
        body=self.payload();self.c.disconnect()
        result=self.c.preview_manual_batch(body)
        self.assertEqual(2,result['plan']['total_bottles'])
        self.assertFalse(self.c.busy)
        self.assertIsNone(self.c.manual_batch_run)

    def test_repeat_request_does_not_redeliver_even_after_restart(self):
        body=self.payload();run=self.run_batch(body);count=len(self.c.hardware.moves)
        self.run_batch(body);self.assertEqual(count,len(self.c.hardware.moves))
        from fakes import DemoHardware
        restarted=Controller(self.tmp.name,hardware_factory=lambda cfg:DemoHardware(cfg))
        try:
            restarted.connect();restarted.start(body)
            if restarted.worker:restarted.worker.join(5)
            self.assertEqual([],restarted.hardware.moves)
            self.assertTrue(restarted.manual_batch_run['completed'])
        finally:restarted.disconnect()

    def test_same_request_id_with_changed_content_rejected(self):
        self.run_batch();before=len(self.c.hardware.moves)
        body=self.payload();body['recipes'][0]['items'][0]['volume_ml']=.5
        with self.assertRaisesRegex(ValueError,'请求编号'):self.c.start(body)
        self.assertEqual(before,len(self.c.hardware.moves))

    def test_stop_skips_later_bottles_and_keeps_connection(self):
        started=threading.Event();old=self.c.hardware.move
        def blocked(direction,steps,rpm,event):
            started.set();event.wait(3);self.c.hardware._check(event)
            return old(direction,steps,rpm,event)
        self.c.hardware.move=blocked
        self.c.start(self.payload());self.assertTrue(started.wait(2))
        self.c.stop_keep_connection()
        self.assertTrue(self.c.connected);self.assertFalse(self.c.busy)
        self.assertEqual([],self.c.hardware.moves)
        run=self.c.manual_batch_run
        self.assertTrue(run['cancelled']);self.assertFalse(run['completed'])
        self.assertEqual(0,run['completed_bottles'])
        self.c.hardware.move=old
        self.run_batch(self.payload())
        self.assertEqual([],self.c.hardware.moves)

    def test_disturbance_is_per_reached_logical_bottle(self):
        from experiment_service import ExperimentService
        self.payload();lab=ExperimentService(self.c);self.c.lab=lab
        try:
            samples=[dict(id=str(n),project_id='p',bottle=n,status='settling',generation=0,frames=[],exclusions=[]) for n in (1,2,3,4,5)]
            lab.data['samples']=samples
            self.run_batch()
            self.assertEqual([0,1,0,0,1],[s['generation'] for s in samples])
        finally:self.c.lab=None;lab.close()

    def test_fault_on_second_bottle_keeps_first_completed_record(self):
        old=self.c.hardware.move
        def fail(direction,steps,rpm,event):
            if self.c.selected_port==13:raise RuntimeError('synthetic failure')
            return old(direction,steps,rpm,event)
        self.c.hardware.move=fail;run=self.run_batch()
        self.assertFalse(run['completed']);self.assertEqual(1,run['completed_bottles'])
        self.assertTrue(run['recipes'][0]['completed']);self.assertFalse(run['recipes'][1]['completed'])
        self.assertIn('synthetic failure',run['error'])

    def test_batch_invalidates_old_collection_context_only_after_validation(self):
        self.payload()
        self.c.bench.update(delivery_seen=True,delivery_seen_context={'old':True},last_run={'old':True})
        self.c.current_delivery={'old':True};self.c.reset_sequence={'old':True}
        self.run_batch(self.payload(recipes=[]))
        self.assertTrue(self.c.bench['delivery_seen'])
        self.run_batch()
        self.assertFalse(self.c.bench['delivery_seen']);self.assertIsNone(self.c.bench['delivery_seen_context'])
        self.assertIsNone(self.c.bench['last_run']);self.assertIsNone(self.c.current_delivery)
        self.assertIsNone(self.c.reset_sequence)

    def test_in_flight_same_request_returns_existing_batch_without_busy_error(self):
        started=threading.Event();release=threading.Event();old=self.c.hardware.move
        def held(direction,steps,rpm,event):
            started.set();release.wait(3);return old(direction,steps,rpm,event)
        self.c.hardware.move=held;body=self.payload()
        try:
            self.c.start(body);self.assertTrue(started.wait(2));worker=self.c.worker
            self.c.start(copy.deepcopy(body))
            self.assertIs(worker,self.c.worker)
            changed=copy.deepcopy(body);changed['recipes'][0]['sample']=1
            with self.assertRaises(ValueError):self.c.start(changed)
        finally:release.set();self.c.worker.join(5)
        self.assertEqual(10,len(self.c.hardware.moves))


if __name__=='__main__':unittest.main()
