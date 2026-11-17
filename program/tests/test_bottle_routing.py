import copy
import unittest
import test_air_controller as base
from controller import validate_config, signature
from experiment_domain import plumbing_signature

MAP={'1':1,'2':3,'3':4,'4':6,'5':7}
class BottleRoutingTests(unittest.TestCase):
    setUp=base.AirControllerTest.setUp
    tearDown=base.AirControllerTest.tearDown
    def mapped(self):
        self.c.config.update(sample_port_map=copy.deepcopy(MAP),free_debug=True,profiles=[])
    def run_action(self,payload):
        self.c.start(payload);self.c.worker.join(3)
        self.assertNotEqual('FAULT',self.c.state,self.c.message)
        self.assertNotIn('操作未执行',self.c.message)
    def test_all_five_debug_routes_keep_logical_ids(self):
        self.mapped()
        for bottle,port in MAP.items():
            self.run_action(dict(action='chase',sample=int(bottle)))
            self.assertEqual(port,self.c.hardware.moves[-1]['port'])
            self.assertIsNotNone(self.c.samples[bottle]['last_gas_stop'])
    def test_recipe_mix_routes_to_physical_port(self):
        self.mapped();self.run_action(dict(action='recipe',sample=5,items=[dict(source=15,volume_ml=.2)],mix_after=True))
        self.assertEqual([15,7,7,15],[m['port'] for m in self.c.hardware.moves])
    def test_raw_manual_port_seven_is_not_remapped(self):
        self.mapped();self.run_action(dict(action='select_port',port=7))
        self.run_action(dict(action='manual_motion',port=3,rotation='cw',turns=1,rpm=60))
        self.assertEqual(3,self.c.hardware.moves[-1]['port'])
    def test_undefined_six_rejected_before_motion(self):
        self.mapped()
        with self.assertRaisesRegex(ValueError,'未配置'):
            self.c._volume_plan(dict(source=15,sample=6,volume_ml=.2))
        self.assertEqual([],self.c.hardware.moves)
    def test_invalid_maps_and_source_conflicts(self):
        self.mapped()
        for mapping in ({'1':1,'2':1},{'1':10},{'0':2},{'1':True},{},[] ):
            cfg=copy.deepcopy(self.c.config);cfg['sample_port_map']=mapping
            with self.subTest(mapping=mapping),self.assertRaises(ValueError):validate_config(cfg)
    def test_missing_map_preserves_identity_and_old_signatures(self):
        self.c.config.pop('sample_port_map',None)
        old=signature(self.c.config);plumbing=plumbing_signature(self.c.config,14)
        self.run_action(dict(action='select_port',port=6))
        self.c.config['sample_port_map']=copy.deepcopy(MAP)
        self.assertNotEqual(old,signature(self.c.config));self.assertNotEqual(plumbing,plumbing_signature(self.c.config,14))
    def test_stale_compiled_manual_plan_rejected(self):
        self.mapped();plan=self.c._recipe_plan(dict(sample=2,items=[dict(source=15,volume_ml=.2)]))
        self.c.config['sample_port_map']['2']=2
        with self.assertRaisesRegex(ValueError,'接管|映射|配置'):self.c._execute_recipe(plan)
        self.assertEqual([],self.c.hardware.moves)

    def test_standalone_volume_and_stock_collection_route_bottle_two(self):
        self.mapped();self.run_action(dict(action='volume',source=15,sample=2,volume_ml=.2))
        self.assertEqual([15,3],[m['port'] for m in self.c.hardware.moves])
        self.c.hardware.moves.clear()
        self.run_action(dict(action='stock_calibration',source=14,sample=2,doses=2,
                             aspirate=dict(steps=40,rpm=5,timeout_s=20),chase=dict(steps=900,rpm=15,timeout_s=35)))
        self.assertEqual([14,3,14,3,16],[m['port'] for m in self.c.hardware.moves])
    def test_endpoint_record_survives_but_becomes_stale_after_mapping_change(self):
        self.mapped()
        ref=self.c.save_source_volume_calibration(dict(source=14,sample=2,doses=1,
            aspirate=dict(steps=40,rpm=5,timeout_s=20),chase=dict(steps=900,rpm=15,timeout_s=35),
            groups_ml=[.2],temperature_c=25))['reference']
        self.c.config['sample_port_map']['2']=2
        with self.assertRaisesRegex(ValueError,'失效'):self.c._volume_plan(dict(source=14,sample=2,volume_ml=.2))
        self.assertEqual(ref,self.c.source_volume_calibrations[0])
    def test_tube_draining_routes_logical_bottles(self):
        self.mapped();self.run_action(dict(action='tubes',operation='drain_samples',samples=[4,5]))
        self.assertEqual([16,6,7],[m['port'] for m in self.c.hardware.moves])
    def test_formal_mass_plan_routes_and_rejects_stale_mapping(self):
        from experiment_domain import plumbing_signature
        from tube_workflows import AIR
        self.mapped();plan=self.c._recipe_plan(dict(sample=4,items=[dict(source=15,volume_ml=.2)],rinse_after=False))
        plan.update(purge_after=True,purge=copy.deepcopy(AIR),geometry_signatures={'15':plumbing_signature(self.c.config,15)})
        checked=self.c._check_experiment_plan(plan)
        self.c._execute_recipe(checked)
        self.assertEqual([15,6,6,16],[m['port'] for m in self.c.hardware.moves])
        self.c.config['sample_port_map']['4']=5
        with self.assertRaisesRegex(ValueError,'配置|映射'):self.c._check_experiment_plan(plan)
    def test_raw_manual_motion_invalidates_the_logical_occupant(self):
        from experiment_service import ExperimentService
        self.mapped();lab=ExperimentService(self.c)
        try:
            samples=[dict(id=str(n),project_id='p',bottle=n,status='settling',generation=0,frames=[],exclusions=[]) for n in (2,3,4,5)]
            lab.data['samples']=samples
            lab.before_manual(dict(action='manual_motion',port=3))
            self.assertEqual([1,0,0,0],[s['generation'] for s in samples])
            lab.before_manual(dict(action='manual_motion',port=7))
            self.assertEqual([1,0,0,1],[s['generation'] for s in samples])
            lab.before_manual(dict(action='manual_motion',port=15))
            self.assertEqual([1,0,0,1],[s['generation'] for s in samples])
        finally:lab.close()

    def test_legacy_profile_routes_through_mapping_and_preselect_guard(self):
        self.c.config['sample_port_map']=copy.deepcopy(MAP)
        for profile in self.c.config['profiles']:profile['geometry_signature']=signature(self.c.config)
        base.AirControllerTest.run_action(self,dict(action='new_samples'))
        base.AirControllerTest.run_action(self,dict(action='reset'))
        base.AirControllerTest.run_action(self,dict(action='prime',source=11,pulse=dict(steps=100,rpm=1,timeout_s=5)))
        base.AirControllerTest.run_action(self,dict(action='reset'))
        base.AirControllerTest.run_action(self,dict(action='aspirate',profile_id='demo-11-2-first'))
        self.assertEqual(2,self.c.pending_plug['target'])
        base.AirControllerTest.run_action(self,dict(action='select_port',port=3))
        base.AirControllerTest.run_action(self,dict(action='chase'))
        self.assertEqual(3,self.c.hardware.moves[-1]['port'])
        self.assertEqual(1,self.c.samples['2']['dose_count'])

    def test_malformed_compiled_plan_is_rejected_without_disconnect(self):
        self.mapped()
        template=self.c._recipe_plan(dict(sample=2,items=[dict(source=15,volume_ml=.2)]))
        for changes in (dict(items=None),dict(items=4),dict(items=[None]),dict(items=['bad']),
                        dict(geometry_signatures=None)):
            with self.subTest(changes=changes):
                plan=dict(template,**changes)
                with self.assertRaises(ValueError):self.c._check_experiment_plan(plan)
                self.c.start(dict(action='experiment_recipe',plan=plan));self.c.worker.join(3)
                self.assertEqual('IDLE',self.c.state,self.c.message)
                self.assertTrue(self.c.connected)
                self.assertEqual([],self.c.hardware.moves)
