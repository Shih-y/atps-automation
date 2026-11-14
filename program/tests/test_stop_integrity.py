import tempfile
import unittest
from pathlib import Path
from controller import Controller
from fakes import DemoHardware

class StopIntegrity(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'test_output';root.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=root)
        self.c=Controller(self.tmp.name,hardware_factory=lambda cfg:DemoHardware(cfg));self.c.connect()
    def tearDown(self):
        self.c.disconnect();self.tmp.cleanup()
    def test_archive_and_log_failure_do_not_undo_confirmed_stop(self):
        def fail(*a,**kw):raise OSError('disk unavailable')
        self.c.capture.archive=fail;self.c.log=fail
        self.c.stop_keep_connection()
        self.assertTrue(self.c.connected)
        self.assertEqual('confirmed',self.c.stop_status)
    def test_failed_device_confirmation_is_not_stopped(self):
        def fail():raise OSError('no readback')
        self.c.hardware.recover_after_stop=fail
        with self.assertRaises(Exception):self.c.stop_keep_connection()
        self.assertEqual('unconfirmed',self.c.stop_status)
        self.assertNotEqual('STOPPED',self.c.state)
    def test_invalid_manual_recipe_does_not_mark_sample_disturbed(self):
        class Observer:
            def __init__(self):self.calls=[]
            def before_manual(self,p):self.calls.append(p)
            def on_action_event(self,*a,**kw):pass
        observer=Observer();self.c.lab=observer
        self.c.start(dict(action='recipe',sample=1,items=[dict(source=15,volume_ml=.15),dict(source=7,volume_ml=.1)]))
        self.c.worker.join(2)
        self.assertEqual([],observer.calls)
        self.assertEqual([],self.c.hardware.moves)
