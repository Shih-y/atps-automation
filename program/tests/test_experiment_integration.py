"""Real controller integration with test hardware only; never a physical validation."""
import copy
import tempfile
import unittest
from controller import Controller
from fakes import DemoHardware


class LabRecorder:
    def __init__(self): self.events=[]; self.manual=[]
    def before_manual(self, payload): self.manual.append(copy.deepcopy(payload))
    def on_action_event(self, event, payload, plan=None, error=None):
        self.events.append((event, copy.deepcopy(payload), copy.deepcopy(plan), error))


class ExperimentControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.c=Controller(self.tmp.name, hardware_factory=lambda cfg:DemoHardware(cfg))
        self.c.connect(); self.lab=LabRecorder(); self.c.lab=self.lab
    def tearDown(self): self.c.disconnect(); self.tmp.cleanup()
    def run_action(self, payload):
        self.c.start(payload); self.c.worker.join(5)
        self.assertFalse(self.c.busy)
    def test_port_ten_is_available_for_manual_and_recipe(self):
        self.run_action(dict(action='recipe',sample=6,items=[dict(source=10,volume_ml=.15)]))
        self.assertEqual([10,6,6,16],[r['port'] for r in self.c.hardware.moves])
        self.assertIn('10',self.c.sources)
        self.assertEqual(80,self.c.config['geometry']['source_cm']['10'])
    def test_compiled_recipe_records_each_command_and_completion(self):
        plan=self.c._recipe_plan(dict(sample=2,items=[dict(source=15,volume_ml=.15)]))
        self.run_action(dict(action='experiment_recipe',experiment_sample_id='test-sample',plan=plan))
        self.assertEqual([15,2,2,16],[r['port'] for r in self.c.hardware.moves])
        self.assertEqual([],self.lab.manual)
        progress=[e[2]['completed_commands'] for e in self.lab.events if e[0]=='progress']
        self.assertEqual([1,2,3,4],progress)
        self.assertEqual('completed',self.lab.events[-1][0])
        self.assertTrue(self.lab.events[-1][2]['completed'])
    def test_invalid_compiled_recipe_never_moves_any_partial_item(self):
        plan=self.c._recipe_plan(dict(sample=1,items=[dict(source=15,volume_ml=.15)]))
        plan['items'].append(copy.deepcopy(plan['items'][0]))
        plan['items'][1]['parameters'][0]['aspirate']['steps']=-1
        self.run_action(dict(action='experiment_recipe',experiment_sample_id='test-sample',plan=plan))
        self.assertEqual([],self.c.hardware.moves)
        self.assertEqual('failed',self.lab.events[-1][0])
    def test_manual_actions_notify_lab(self):
        self.run_action(dict(action='select_port',port=10))
        self.assertEqual('select_port',self.lab.manual[0]['action'])

    def test_stock_calibration_collects_each_dose_at_sample_with_one_final_purge(self):
        self.run_action(dict(action='stock_calibration',source=10,sample=6,doses=3,
                             aspirate=dict(steps=45,rpm=5,timeout_s=20),
                             chase=dict(steps=1000,rpm=15,timeout_s=35)))
        self.assertEqual([10,6,10,6,10,6,16],[m['port'] for m in self.c.hardware.moves])
        self.assertFalse(self.c.recipe_run['mixing_performed'])
        self.assertTrue(self.c.recipe_run['completed'])
        self.assertIsNone(self.c.recipe_run['actual_ml'])


if __name__=='__main__': unittest.main()
