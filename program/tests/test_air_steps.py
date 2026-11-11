"""Public step actions must not guess fluid state or deliver a stale liquid plug."""
import copy
import json
import time
import unittest
import base64
import struct
import zlib
from capture_store import timestamp
from unittest.mock import patch
import test_air_controller as base


class StepActionsTest(unittest.TestCase):
    setUp=base.AirControllerTest.setUp
    tearDown=base.AirControllerTest.tearDown
    ready=base.AirControllerTest.ready
    run_action=base.AirControllerTest.run_action
    def test_separate_wash_then_purge_and_purge_without_wash(self):
        s=self.run_action({'action':'purge'})
        self.assertNotEqual('air_reset_verified',s['common'])
        self.assertEqual([16],[m['port'] for m in self.c.hardware.moves])
        s=self.run_action({'action':'wash'})
        self.assertEqual('wet',s['common'])
        s=self.run_action({'action':'purge'})
        self.assertEqual('air_reset_verified',s['common'])
        self.assertEqual([16,15,16],[m['port'] for m in self.c.hardware.moves])

    def test_standalone_selection_preserves_unknown_without_pumping(self):
        s=self.run_action({'action':'select_port','port':11})
        self.assertEqual(11,s.get('selected_port'))
        self.assertEqual('unknown',s['common'])
        self.assertEqual([],self.c.hardware.moves)

    def test_aspirate_records_pending_and_only_matching_chase_delivers(self):
        self.ready(); n=len(self.c.hardware.moves)
        s=self.run_action({'action':'aspirate','profile_id':'demo-11-1-first'})
        pending=s.get('pending_plug')
        self.assertIsNotNone(pending)
        self.assertEqual((11,1,'demo-11-1-first'),(pending['source'],pending['target'],pending['profile_id']))
        self.assertEqual(0,s['samples']['1']['dose_count'])
        self.assertEqual([11],[m['port'] for m in self.c.hardware.moves[n:]])
        s=self.run_action({'action':'select_port','port':1})
        self.assertEqual(pending['id'],s['pending_plug']['id'])
        s=self.run_action({'action':'chase','pending_id':pending['id']})
        self.assertIsNone(s['pending_plug'])
        self.assertEqual(1,s['samples']['1']['dose_count'])
        self.assertEqual('wet',s['common'])
        self.assertEqual([11,1],[m['port'] for m in self.c.hardware.moves[n:]])

    def test_pending_rejects_all_other_liquid_mutations_without_losing_plug(self):
        self.ready(); s=self.run_action({'action':'aspirate','profile_id':'demo-11-1-first'})
        self.assertIsNotNone(s.get('pending_plug'))
        pending=s['pending_plug']; n=len(self.c.hardware.moves)
        for action in [{'action':'select_port','port':2},{'action':'select_port','port':11},
                       {'action':'wash'},{'action':'purge'},{'action':'reset'},
                       {'action':'aspirate','profile_id':'demo-11-2-first'},
                       {'action':'prime','source':12},{'action':'new_samples'},
                       {'action':'commission'},{'action':'run','items':[]},
                       {'action':'confirm_reset'},{'action':'bubble','profile_id':'demo-11-1-first'}]:
            with self.subTest(action=action):
                with self.assertRaises(ValueError): self.c.start(action)
                self.assertEqual(pending['id'],self.c.snapshot()['pending_plug']['id'])
        self.assertEqual(n,len(self.c.hardware.moves))
        with self.assertRaises(ValueError): self.c.connect('demo')

    def test_expired_pending_is_retained_until_explicit_discard(self):
        self.ready(); self.run_action({'action':'aspirate','profile_id':'demo-11-1-first'})
        self.assertIsNotNone(self.c.snapshot().get('pending_plug'))
        self.c.pending_plug['expires_monotonic']=time.monotonic()-1
        n=len(self.c.hardware.moves)
        with self.assertRaises(ValueError): self.c.start({'action':'chase'})
        with self.assertRaises(ValueError): self.c.start({'action':'select_port','port':1})
        self.assertEqual(n,len(self.c.hardware.moves)); self.assertTrue(self.c.snapshot()['pending_plug']['expired'])
        s=self.run_action({'action':'discard_loaded'})
        self.assertIsNone(s['pending_plug']); self.assertEqual(0,s['samples']['1']['dose_count'])
        self.assertEqual('air_reset_verified',s['common'])
        self.assertEqual([15,16],[m['port'] for m in self.c.hardware.moves[n:]])

    def test_wrong_pending_id_rejects_without_motion(self):
        self.ready(); self.run_action({'action':'aspirate','profile_id':'demo-11-1-first'})
        self.assertIsNotNone(self.c.snapshot().get('pending_plug')); n=len(self.c.hardware.moves)
        with self.assertRaises(ValueError): self.c.start({'action':'chase','pending_id':'stale'})
        self.assertEqual(n,len(self.c.hardware.moves))

    def test_untracked_reset_confirmation_does_not_unlock(self):
        s=self.run_action({'action':'confirm_reset'})
        self.assertEqual('unknown',s['common']); self.assertEqual([],self.c.hardware.moves)

    def test_manual_selection_invalidates_ready_state_but_not_priming(self):
        self.ready(); s=self.run_action({'action':'select_port','port':11})
        self.assertEqual('unknown',s['common']); self.assertTrue(s['sources']['11']['primed'])
        n=len(self.c.hardware.moves)
        s=self.run_action({'action':'aspirate','profile_id':'demo-11-1-first'})
        self.assertIsNone(s['pending_plug']); self.assertEqual(n,len(self.c.hardware.moves))

    def test_chase_expiry_during_valve_positioning_never_moves_pump(self):
        self.ready(); self.run_action({'action':'aspirate','profile_id':'demo-11-1-first'})
        select=self.c.hardware.select
        def expire_on_select(port,cancel=None):
            select(port,cancel); self.c.pending_plug['expires_monotonic']=time.monotonic()-1
        self.c.hardware.select=expire_on_select; n=len(self.c.hardware.moves)
        s=self.run_action({'action':'chase'})
        self.assertEqual(n,len(self.c.hardware.moves)); self.assertEqual(0,s['samples']['1']['dose_count'])
        self.assertIsNotNone(s['pending_plug']); self.assertTrue(s['connected'])

    def test_step_and_combo_use_same_pump_sequence(self):
        self.ready(); n=len(self.c.hardware.moves)
        self.run_action({'action':'aspirate','profile_id':'demo-11-1-first'}); self.run_action({'action':'chase'})
        individual=copy.deepcopy(self.c.hardware.moves[n:])
        self.run_action({'action':'reset'}); n=len(self.c.hardware.moves)
        self.run_action({'action':'dose','profile_id':'demo-11-2-first'})
        combined=self.c.hardware.moves[n:]
        individual[1]['port']=2
        self.assertEqual(individual,combined)

    def test_target_preselection_timestamp_keeps_original_expiry(self):
        self.ready(); s=self.run_action({'action':'aspirate','profile_id':'demo-11-1-first'})
        expiry=s['pending_plug']['expires_at']
        s=self.run_action({'action':'select_port','port':1})
        self.assertIsNotNone(s['pending_plug'].get('target_selected_at'))
        self.assertEqual(expiry,s['pending_plug']['expires_at'])

    def test_discard_after_target_connection_marks_sample_uncertain(self):
        self.ready(); self.run_action({'action':'aspirate','profile_id':'demo-11-1-first'})
        self.run_action({'action':'select_port','port':1})
        s=self.run_action({'action':'discard_loaded'})
        self.assertEqual('unknown',s['samples']['1']['condition'])
        self.assertEqual(0,s['samples']['1']['dose_count'])
        self.assertTrue(s['samples']['1'].get('uncertain_delivery'))
        n=len(self.c.hardware.moves)
        s=self.run_action({'action':'aspirate','profile_id':'demo-11-1-first'})
        self.assertIsNone(s['pending_plug']); self.assertEqual(n,len(self.c.hardware.moves))

    def test_uncertain_continued_sample_loses_old_capture_timing(self):
        self.ready(); self.run_action({'action':'dose','profile_id':'demo-11-1-first'})
        self.run_action({'action':'reset'})
        self.run_action({'action':'aspirate','profile_id':'demo-11-1-continued'})
        self.run_action({'action':'select_port','port':1}); s=self.run_action({'action':'discard_loaded'})
        self.assertEqual(1,s['samples']['1']['dose_count'])
        self.assertIsNone(s['samples']['1']['last_gas_stop']); self.assertIsNone(s['samples']['1']['settle_s'])
        with self.assertRaises(ValueError): self.c._prepare_capture([1])
        s=self.run_action({'action':'new_samples'})
        self.assertFalse(s['samples']['1']['uncertain_delivery']); self.assertIsNone(s['samples']['1']['uncertainty_note'])

    def test_expiry_while_observing_before_chase_keeps_plug_and_no_delivery(self):
        self.ready(); self.run_action({'action':'aspirate','profile_id':'demo-11-1-first'})
        self.assertIsNotNone(self.c.snapshot().get('pending_plug')); n=len(self.c.hardware.moves)
        self.c.start({'action':'chase'})
        deadline=time.monotonic()+2
        while not self.c.prompt and time.monotonic()<deadline: time.sleep(.005)
        self.assertIsNotNone(self.c.prompt)
        self.c.pending_plug['expires_monotonic']=time.monotonic()-1
        self.c.acknowledge({'id':self.c.prompt['id'],'choice':'confirm'}); self.c.worker.join(2)
        self.assertTrue(self.c.connected); self.assertIsNotNone(self.c.snapshot()['pending_plug'])
        self.assertEqual(n,len(self.c.hardware.moves)); self.assertEqual(0,self.c.samples['1']['dose_count'])

    def test_run_from_ready_omits_redundant_reset_and_resets_each_delivery(self):
        self.ready(); n=len(self.c.hardware.moves)
        self.run_action({'action':'run','items':[{'profile_id':'demo-11-1-first'},{'profile_id':'demo-11-1-continued'}]})
        self.assertEqual([11,1,15,16,11,1,15,16],[m['port'] for m in self.c.hardware.moves[n:]])
        self.assertEqual(2,self.c.samples['1']['dose_count'])

    def test_wash_preserves_other_source_priming_and_sample_wet_condition(self):
        self.ready(); self.run_action({'action':'dose','profile_id':'demo-11-1-first'})
        self.run_action({'action':'wash'})
        self.assertTrue(self.c.sources['11']['primed']); self.assertEqual('continued',self.c.samples['1']['condition'])

    def test_stop_discards_pending_metadata_without_dose_credit(self):
        self.ready(); s=self.run_action({'action':'aspirate','profile_id':'demo-11-1-first'})
        self.assertIsNotNone(s.get('pending_plug')); self.c.stop()
        self.assertIsNone(self.c.snapshot()['pending_plug']); self.assertFalse(self.c.connected)
        self.assertEqual('unknown',self.c.samples['1']['condition'])

    def test_calibration_raw_report_persisted_without_unlock(self):
        row={'profile_key':'11-1-first','source':11,'sample':1,'condition':'first','kind':'delivery','stock_equiv_ml':.2,'assay_method':'independent validated assay'}
        self.assertTrue(hasattr(self.c,'calibration_report'))
        old_profiles=copy.deepcopy(self.c.config['profiles']); result=self.c.calibration_report({'rows':[row]*10})
        self.assertFalse(result['report']['hardware_authorized']); self.assertEqual(old_profiles,self.c.config['profiles'])
        saved=list((self.c.directory/'calibration_reports').glob('*.json'))
        self.assertEqual(1,len(saved)); data=json.loads(saved[0].read_text(encoding='utf-8'))
        self.assertEqual(10,len(data['raw_input']['rows'])); self.assertEqual(self.c.snapshot()['geometry_signature'],data['geometry_signature'])

    def test_capture_saves_front_row_and_geometry_on_raw_frame(self):
        self.ready(); self.run_action({'action':'dose','profile_id':'demo-11-1-first'})
        self.run_action({'action':'clear','sample':1}); self.run_action({'action':'capture','targets':[1]})
        def chunk(kind,data):
            return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
        png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\xff\xff\xff'))+chunk(b'IEND',b'')
        result=self.c.capture_frame({'batch_id':self.c.capture.current['batch_id'],'png_base64':base64.b64encode(png).decode(),
            'width':1,'height':1,'captured_at':timestamp(),'alignment_confirmed':True,
            'rois':[{'sample':1,'x':0,'y':0,'width':1,'height':1}]})
        self.assertEqual('fixed_front_row',result['frame']['view_direction'])
        self.assertEqual(self.c.snapshot()['geometry_signature'],result['frame']['geometry_signature'])
        self.assertEqual('unreviewed',result['imaging']['records'][0]['classification'])


if __name__=='__main__':
    import unittest
    unittest.main()
