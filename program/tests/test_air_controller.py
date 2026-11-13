import copy
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from controller import Controller, pulse_checked, signature, validate_config, validate_profile
from fakes import DemoHardware
from test_support import make_test_controller, demo_profiles

class AirControllerTest(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'test_output'; root.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=root); self.c=make_test_controller(self.tmp.name); self.c.connect()
    def tearDown(self):
        self.c.stop()
        if self.c.worker: self.c.worker.join(2)
        self.tmp.cleanup()
    def run_action(self,payload,prime_choice='reached'):
        self.c.start(payload); deadline=time.monotonic()+5
        while self.c.busy and time.monotonic()<deadline:
            s=self.c.snapshot()
            if s['prompt']:
                p=s['prompt']; self.c.acknowledge({'id':p['id'],'choice':prime_choice if p['kind']=='prime_front' else 'confirm'})
            time.sleep(.005)
        self.assertFalse(self.c.busy)
        return self.c.snapshot()
    def ready(self):
        self.run_action({'action':'new_samples'}); self.run_action({'action':'reset'})
        self.run_action({'action':'prime','source':11,'pulse':{'steps':100,'rpm':1,'timeout_s':5}})
        self.run_action({'action':'reset'})
    def test_default_is_real_empty_calibration_and_no_startup_checks(self):
        c=Controller(self.tmp.name,hardware_factory=lambda cfg:DemoHardware(cfg))
        self.assertEqual('real',c.mode); self.assertEqual([],c.profiles())
        self.assertNotIn('preflight_verified',c.config)
        c.config['geometry']['measured']=False
        c.connect()
        self.assertTrue(c.connected); self.assertEqual([],c.hardware.moves)
        c.disconnect()
        with self.assertRaisesRegex(ValueError,'仅支持实机'): c.connect('demo')
    def test_ABC_91cm_and_all_sources80cm(self):
        g=self.c.config['geometry']; self.assertEqual((10,10,71),(g['A_cm'],g['B_cm'],g['C_cm']))
        self.assertEqual(91,g['A_cm']+g['B_cm']+g['C_cm'])
        self.assertEqual({str(i):80 for i in range(10,16)},g['source_cm'])
        self.assertEqual({str(i):50 for i in range(1,7)},g['sample_cm'])
        self.run_action({'action':'reset'})
        self.assertEqual([15,16],[m['port'] for m in self.c.hardware.moves]); self.assertTrue(self.c.sources['15']['primed'])
        self.assertEqual('air_reset_verified',self.c.common)
    def test_connect_opens_shared_COM6_without_status_preflight(self):
        c=Controller(self.tmp.name)
        with patch('controller.SerialHardware') as factory:
            c.connect()
            args=factory.call_args.args
            self.assertEqual(('COM6','COM6'),args[1:3])
            factory.return_value.connect.assert_called_once()
            factory.return_value._check_config.assert_not_called()
            factory.return_value.move.assert_not_called()
            c.disconnect()
    def test_connect_rejects_two_distinct_ports(self):
        with self.assertRaisesRegex(ValueError,'同一 RS485'): self.c.connect('real','COM6','COM7')
    def test_missing_motion_evidence_still_blocks_quantitative_steps(self):
        c=Controller(self.tmp.name,hardware_factory=lambda cfg:DemoHardware(cfg)); c.connect()
        with self.assertRaisesRegex(ValueError,'预实验'): c._reset_recipe_checked()
        c.disconnect()
    def test_dose_first_then_continued_no_automatic_dry_state(self):
        self.ready(); s=self.run_action({'action':'dose','profile_id':'demo-11-1-first'})
        self.assertEqual('IDLE',s['state']); self.assertEqual('continued',s['samples']['1']['condition'])
        self.assertEqual('wet',s['common']); self.assertIsNotNone(s['samples']['1']['last_gas_stop'])
        self.assertEqual([11,1],[m['port'] for m in self.c.hardware.moves[-2:]])
        self.assertEqual(['aspirate','dispense'],[m['direction'] for m in self.c.hardware.moves[-2:]])
        self.run_action({'action':'reset'}); s=self.run_action({'action':'dose','profile_id':'demo-11-1-continued'})
        self.assertEqual(2,s['samples']['1']['dose_count'])
    def test_first_profile_rejected_after_use(self):
        self.ready(); self.run_action({'action':'dose','profile_id':'demo-11-1-first'}); self.run_action({'action':'reset'})
        n=len(self.c.hardware.moves); s=self.run_action({'action':'dose','profile_id':'demo-11-1-first'})
        self.assertEqual('IDLE',s['state']); self.assertIn('操作未执行',s['message']); self.assertTrue(s['connected']); self.assertEqual(n,len(self.c.hardware.moves))
    def test_dose_without_reset_or_prime_rejected(self):
        s=self.run_action({'action':'dose','profile_id':'demo-11-1-first'}); self.assertEqual('IDLE',s['state']); self.assertIn('操作未执行',s['message']); self.assertEqual([],self.c.hardware.moves)
    def test_busy_reject_and_stale_ack(self):
        self.c.start({'action':'new_samples'})
        while not self.c.prompt: time.sleep(.001)
        with self.assertRaisesRegex(ValueError,'已有操作'): self.c.start({'action':'reset'})
        with self.assertRaisesRegex(ValueError,'过期'): self.c.acknowledge({'id':'wrong','ok':True})
    def test_stop_invalidates_states_and_reconnect_required(self):
        self.ready(); self.c.stop(); s=self.c.snapshot()
        self.assertEqual('unknown',s['common']); self.assertFalse(s['sources']['11']['primed'])
        with self.assertRaises(ValueError): self.c.start({'action':'reset'})
    def test_reverse_sources_disabled_and_no_sample_aspiration(self):
        p={'steps':1,'rpm':1,'timeout_s':1}
        for port,direction in [(11,'dispense'),(15,'dispense'),(16,'dispense'),(1,'aspirate'),(7,'aspirate')]:
            with self.assertRaises(ValueError): self.c._pulse(port,direction,p)
        self.assertEqual([],self.c.hardware.moves)
    def test_nan_infinite_and_commission_caps(self):
        for p in [{'steps':20001,'rpm':1,'timeout_s':1},{'steps':1,'rpm':100.1,'timeout_s':1},{'steps':1,'rpm':1,'timeout_s':121},{'steps':True,'rpm':1,'timeout_s':1},{'steps':1,'rpm':float('nan'),'timeout_s':1},{'turns':1.5,'rpm':50,'timeout_s':10},{'turns':1,'steps':200,'rpm':50,'timeout_s':10}]:
            with self.assertRaises(ValueError): pulse_checked(p,True)
    def test_validated_automatic_queue_confirms_once_for_whole_batch(self):
        self.ready()
        before=sum(x['event']=='observation_required' for x in self.c.logs)
        self.run_action({'action':'run','items':[{'profile_id':'demo-11-1-first','bubble':True}]})
        after=sum(x['event']=='observation_required' for x in self.c.logs)
        self.assertEqual(1,after-before)
        self.assertFalse(self.c.batch_running)
    def test_geometry_change_revokes_profiles_and_reset(self):
        cfg=copy.deepcopy(self.c.config); p=demo_profiles()[0]; p['validated']=True; p['geometry_signature']=signature(cfg); cfg['profiles']=[p]; cfg['reset']['validated']=True
        self.c.disconnect(); self.c.configure(cfg)
        cfg=copy.deepcopy(self.c.config); cfg['geometry']['B_cm']=11; self.c.configure(cfg)
        self.assertFalse(self.c.config['profiles'][0]['validated']); self.assertFalse(self.c.config['reset']['validated'])
    def test_signature_changes_with_concentration(self):
        cfg=copy.deepcopy(self.c.config); old=signature(cfg); cfg['liquids']['14']='PEG20%'; self.assertNotEqual(old,signature(cfg))
    def test_old_waterconfig_and100cm_rejected(self):
        with self.assertRaises(ValueError): validate_config({'fluid':{'max_reverse_ml':1.4}})
        cfg=copy.deepcopy(self.c.config); cfg['geometry'].update(A_cm=30,B_cm=40,C_cm=30)
        with self.assertRaises(ValueError): validate_config(cfg)
    def test_profile_requires_measured_recovery_not_gas_steps(self):
        p=demo_profiles()[0]; p.update(validated=True,geometry_signature=signature(self.c.config))
        with self.assertRaises(ValueError): validate_profile(p,self.c.config)
        p['evidence']=dict(n=10,mean_ml=.2,rsd_percent=4,residual_water_ml=.004,carryover_percent=.5,water_blank_n=3,carryover_n=3,note='test fixture only reference documented observations')
        validate_profile(p,self.c.config)
        p['evidence']['mean_ml']=.22
        with self.assertRaises(ValueError): validate_profile(p,self.c.config)
    def test_autoqueue_order_and_individual_times(self):
        self.ready(); s=self.run_action({'action':'run','items':[{'profile_id':'demo-11-1-first','bubble':True},{'profile_id':'demo-11-2-first','bubble':True}]})
        self.assertEqual('IDLE',s['state']); self.assertNotEqual(s['samples']['1']['last_gas_stop'],s['samples']['2']['last_gas_stop'])
        self.assertEqual('air_reset_verified',s['common'])
        moves=self.c.hardware.moves; reverse=[m['port'] for m in moves if m['direction']=='dispense']; self.assertEqual([1,1,1,2,2,2],reverse)
    def test_capture_requires_individual_clear(self):
        self.ready(); self.run_action({'action':'dose','profile_id':'demo-11-1-first'})
        with self.assertRaisesRegex(ValueError,'尚未确认'): self.c._prepare_capture([1])
        self.run_action({'action':'clear','sample':1}); self.c._prepare_capture([1]); self.assertEqual(['1'],list(self.c.capture.current['sample_timing']))
    def test_cannot_bubble_different_sample_after_delivery(self):
        self.ready(); self.run_action({'action':'dose','profile_id':'demo-11-1-first'})
        self.c.samples['2']=copy.deepcopy(self.c.samples['1'])
        with self.assertRaisesRegex(ValueError,'同一样品'): self.c._bubble(self.c._profile('demo-11-2-first'))
    def test_max5doses_target1ml_guard(self):
        self.ready(); self.c.samples['1']['dose_count']=5
        with self.assertRaisesRegex(ValueError,'5次'): self.c._check_dose(self.c._profile('demo-11-1-first'))
    def test_restart_does_not_restore_fluid_state(self):
        self.ready(); other=Controller(self.tmp.name); self.assertEqual('unknown',other.common); self.assertFalse(other.sources['11']['primed'])
    def test_prime_maxpulse_abort(self):
        self.run_action({'action':'reset'}); s=self.run_action({'action':'prime','source':11,'pulse':{'steps':1,'rpm':1,'timeout_s':1},'max_pulses':2},'more')
        self.assertEqual('FAULT',s['state']); self.assertFalse(s['sources']['11']['primed'])

    def test_wash_change_revokes_profile_and_reset(self):
        cfg=copy.deepcopy(self.c.config); p=demo_profiles()[0]; p['validated']=True; p['geometry_signature']=signature(cfg); cfg['profiles']=[p]; cfg['reset']['validated']=True
        self.c.disconnect(); self.c.configure(cfg)
        cfg=copy.deepcopy(self.c.config); unit='turns' if 'turns' in cfg['reset']['wash'] else 'steps';cfg['reset']['wash'][unit]+=1; self.c.configure(cfg)
        self.assertFalse(self.c.config['profiles'][0]['validated']); self.assertFalse(self.c.config['reset']['validated'])
    def test_A_too_small_for_point2ml_rejected(self):
        cfg=copy.deepcopy(self.c.config); cfg['geometry']['A_cm']=2
        with self.assertRaisesRegex(ValueError,'A段裸容积'): validate_config(cfg)
    def test_empty_first_vial_allowed_bubbling_still_requires_submersion(self):
        p=demo_profiles()[0]; self.assertEqual(0,p['immersion_mm']); validate_profile(p,self.c.config,False)
        p['bubble_immersion_mm']=0
        with self.assertRaises(ValueError): self.c._validate_bubble(p)
    def test_invalid_bubbling_rejected_before_any_dose(self):
        self.ready(); p=self.c._profile('demo-11-1-first'); p['bubble_immersion_mm']=0; n=len(self.c.hardware.moves)
        with self.assertRaises(ValueError): self.c._dose(p,True)
        self.assertEqual(n,len(self.c.hardware.moves))
    def test_stop_sent_even_when_archive_or_log_fails(self):
        calls=[]; self.c.hardware.stop=lambda:calls.append('stop')
        self.c.capture.archive=lambda:(_ for _ in ()).throw(OSError('full disk'))
        self.c.log=lambda *a,**kw:(_ for _ in ()).throw(OSError('full disk'))
        self.c.stop(); self.assertEqual(['stop'],calls)
    def test_stopped_capture_archived_and_not_pending(self):
        self.ready(); self.run_action({'action':'dose','profile_id':'demo-11-1-first'}); self.run_action({'action':'clear','sample':1}); self.c._prepare_capture([1])
        self.assertTrue(self.c.capture.pending); self.c.stop(); self.assertFalse(self.c.capture.pending)
        with self.assertRaisesRegex(ValueError,'停止/断开'): self.c.capture_frame({})
    def test_profile_action_edit_revokes_same_id_validation(self):
        cfg=copy.deepcopy(self.c.config); p=demo_profiles()[0]; p['validated']=True; p['bubble_validated']=True; cfg['profiles']=[p]
        self.c.disconnect(); self.c.configure(cfg)
        cfg=copy.deepcopy(self.c.config); cfg['profiles'][0]['chase']['steps']+=1; self.c.configure(cfg)
        self.assertFalse(self.c.config['profiles'][0]['validated']); self.assertFalse(self.c.config['profiles'][0]['bubble_validated'])
        snapshots=list((Path(self.tmp.name)/'config_history').glob('*.json'))
        self.assertGreaterEqual(len(snapshots),3); self.assertTrue(all('config' in json.loads(p.read_text(encoding='utf-8')) for p in snapshots))
    def test_initial_dose_range_is_not_all_continued_levels(self):
        self.ready(); p=self.c._profile('demo-11-1-continued'); p['initial_dose_count_min']=1; p['initial_dose_count_max']=1
        self.c.samples['1'].update(condition='continued',dose_count=2)
        with self.assertRaisesRegex(ValueError,'初始|液位'): self.c._check_dose(p)
    def test_A_plug_hold_separate_from_source_age(self):
        self.ready(); p=self.c._profile('demo-11-1-first'); p['max_plug_hold_s']=1
        self.c._pulse=lambda *a,**k:None; self.c._ask=lambda *a,**k:'confirm'
        with patch('controller.time.monotonic',side_effect=[100,102]):
            with self.assertRaisesRegex(Exception,'液段后等待'): self.c._dose(p)
    def test_bubble_long_pulse_rejected(self):
        for key,value in [('steps',501),('rpm',5.1),('timeout_s',6)]:
            p=demo_profiles()[0]; p['bubble'][key]=value
            with self.assertRaises(ValueError): validate_profile(p,self.c.config,False)
    def test_watchdog_includes_valve_select(self):
        class Slow(DemoHardware):
            def select(self,port,cancel_event=None):
                while not cancel_event.wait(.01): pass
                self._check(cancel_event)
        self.c.hardware.close(); self.c.hardware=Slow(self.c.config); self.c.hardware.connect()
        start=time.monotonic()
        s=self.run_action({'action':'commission','port':16,'direction':'aspirate','pulse':{'steps':1,'rpm':1,'timeout_s':1}})
        self.assertLess(time.monotonic()-start,2); self.assertEqual('FAULT',s['state']); self.assertEqual([],self.c.hardware.moves)

if __name__=='__main__': unittest.main()
