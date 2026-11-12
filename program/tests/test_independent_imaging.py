import base64
import struct
import unittest
import zlib
import test_air_controller as base
from capture_store import timestamp
from datetime import datetime,timezone,timedelta


class IndependentImagingTests(unittest.TestCase):
    setUp=base.AirControllerTest.setUp
    tearDown=base.AirControllerTest.tearDown

    @staticmethod
    def png():
        def chunk(kind,payload):
            return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
        raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\xff\xff\xff'))+chunk(b'IEND',b'')
        return base64.b64encode(raw).decode()

    def archive_payload(self,**extra):
        self.c.config['free_debug']=True
        batch=self.c.begin_imaging(dict(targets=[1]))['batch_id']
        return dict(batch_id=batch,png_base64=self.png(),width=1,height=1,
                    rois=[dict(sample=1,x=0,y=0,width=1,height=1)],**extra)

    def test_camera_frame_frozen_before_archive_begin_preserves_actual_timestamp(self):
        frozen=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat(timespec='milliseconds')
        payload=self.archive_payload(frame_origin='camera',captured_at=frozen)
        self.assertTrue(self.c.capture.current.get('archive_only'), 'Independent batch must be an explicit archive')
        saved=self.c.capture_frame(payload)
        self.assertEqual(frozen,saved['frame']['captured_at'])
        self.assertEqual('camera',saved['frame']['frame_origin'])
        self.assertIsNone(saved['frame']['loaded_at'])
        self.assertIn('captured',saved['frame']['timing_basis'])

    def test_import_records_load_time_without_inventing_capture_or_settling_time(self):
        past=(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat(timespec='milliseconds')
        loaded=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat(timespec='milliseconds')
        self.c.samples['1'].update(last_gas_stop=past,clear_at=past)
        payload=self.archive_payload(frame_origin='import',captured_at=None,loaded_at=loaded)
        saved=self.c.capture_frame(payload)
        frame=saved['frame'];record=saved['imaging']['records'][0]
        self.assertIsNone(frame['captured_at'])
        self.assertEqual(loaded,frame['loaded_at'])
        self.assertEqual('import',frame['frame_origin'])
        self.assertEqual('import_acquisition_time_unknown',frame['timing_basis'])
        for key in ('elapsed_since_stop_s','elapsed_since_clear_s','elapsed_settle_seconds'):
            self.assertIsNone(record[key])

    def test_old_camera_frame_cannot_produce_negative_settling_time(self):
        now=datetime.now(timezone.utc)
        frozen=(now-timedelta(minutes=3)).isoformat(timespec='milliseconds')
        stop=(now-timedelta(minutes=2)).isoformat(timespec='milliseconds')
        self.c.samples['1'].update(last_gas_stop=stop,clear_at=stop)
        saved=self.c.capture_frame(self.archive_payload(frame_origin='camera',captured_at=frozen))
        record=saved['imaging']['records'][0]
        self.assertIsNone(record['elapsed_since_stop_s'])
        self.assertIsNone(record['elapsed_since_clear_s'])

    def test_camera_elapsed_time_uses_freeze_time_instead_of_later_save(self):
        now=datetime.now(timezone.utc)
        frozen=(now-timedelta(minutes=2)).isoformat(timespec='milliseconds')
        stopped=(now-timedelta(minutes=3)).isoformat(timespec='milliseconds')
        self.c.samples['1'].update(last_gas_stop=stopped,clear_at=stopped)
        payload=self.archive_payload(frame_origin='camera',captured_at=frozen)
        self.c.capture.current['mix_stopped_at']=stopped
        saved=self.c.capture_frame(payload)
        self.assertEqual(60.,saved['frame']['elapsed_settle_seconds'])
        record=saved['imaging']['records'][0]
        self.assertEqual(60.,record['elapsed_since_stop_s'])
        self.assertEqual(60.,record['elapsed_since_clear_s'])

    def test_archive_rejects_invented_import_capture_and_future_times_before_file_write(self):
        future=(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat()
        cases=[dict(frame_origin='import',captured_at=timestamp(),loaded_at=timestamp()),
               dict(frame_origin='import',captured_at=None),
               dict(frame_origin='import',captured_at=None,loaded_at=future),
               dict(frame_origin='camera',captured_at=future),
               dict(frame_origin='camera',captured_at=None),
               dict(frame_origin='other',captured_at=timestamp())]
        payload=self.archive_payload()
        for extra in cases:
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                self.c.capture_frame(dict(payload,**extra))
            self.assertEqual([],list((self.c.capture.root/payload['batch_id']).glob('*.png')))

    def test_legacy_and_strict_requests_cannot_bypass_ready_time_with_origin(self):
        frozen=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()
        payload=self.archive_payload(captured_at=frozen)
        with self.assertRaises(ValueError):self.c.capture_frame(payload)
        self.c.capture.current['archive_only']=False
        self.c.capture.current['timing_prerequisites_bypassed']=False
        for extra in [dict(frame_origin='camera',captured_at=frozen),
                      dict(frame_origin='import',captured_at=None,loaded_at=timestamp())]:
            with self.assertRaises(ValueError):
                self.c.capture_frame(dict(payload,alignment_confirmed=True,**extra))

    def test_begin_and_save_frame_without_serial_or_timing_prerequisites(self):
        self.assertTrue(hasattr(self.c,'begin_imaging'),'Independent imaging API missing')
        self.c.config['free_debug']=True
        self.c.disconnect()
        result=self.c.begin_imaging(dict(targets=[1]))
        batch=result['imaging']['batch_id']
        self.assertTrue(result['imaging']['timing_prerequisites_bypassed'])
        saved=self.c.capture_frame(dict(batch_id=batch,captured_at=timestamp(),png_base64=self.png(),
                                      width=1,height=1,rois=[dict(sample=1,x=0,y=0,width=1,height=1)]))
        self.assertTrue(saved['imaging']['complete'])
        self.assertIsNone(saved['imaging']['records'][0]['elapsed_since_stop_s'])
        self.assertFalse(self.c.connected)
        self.assertEqual([],self.c.hardware.moves)

    def test_offline_partial_and_empty_rois_require_explicit_partial_archive(self):
        self.c.config['free_debug']=True
        self.c.disconnect()
        for rois,missing in [([],[1,2]),([dict(sample=2,x=0,y=0,width=1,height=1)],[1])]:
            batch=self.c.begin_imaging(dict(targets=[1,2]))['batch_id']
            payload=dict(batch_id=batch,captured_at=timestamp(),png_base64=self.png(),width=1,height=1,rois=rois)
            with self.assertRaises(ValueError):self.c.capture_frame(payload)
            saved=self.c.capture_frame(dict(payload,allow_partial_rois=True))
            self.assertFalse(saved['frame']['roi_complete'])
            self.assertEqual(missing,saved['frame']['unassigned_targets'])
            self.assertEqual(len(rois),len(saved['imaging']['records']))

    def test_begin_rejects_pending_batch_busy_and_stopping_without_overwrite(self):
        self.assertTrue(hasattr(self.c,'begin_imaging'),'Independent imaging API missing')
        self.c.begin_imaging(dict(targets=[1,2]))
        batch=self.c.capture.current['batch_id']
        with self.assertRaises(ValueError):self.c.begin_imaging(dict(targets=[3]))
        self.assertEqual(batch,self.c.capture.current['batch_id'])
        self.c.capture.discard(batch)
        for attr in ('busy','_user_stopping'):
            setattr(self.c,attr,True)
            try:
                with self.assertRaises(ValueError):self.c.begin_imaging(dict(targets=[3]))
            finally:setattr(self.c,attr,False)
        self.assertEqual(batch,self.c.capture.current['batch_id'])


if __name__=='__main__':unittest.main()
