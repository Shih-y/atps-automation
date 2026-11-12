import base64
import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
from capture_store import CaptureStore, timestamp


def test_png(width=100,height=40):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\x80\x80\x80'*width)*height))+chunk(b'IEND',b'')


class PhaseArchiveTests(unittest.TestCase):
    def setUp(self):
        folder=Path(__file__).resolve().parents[1]/'test_output';folder.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=folder)
        self.store=CaptureStore(self.temp.name)
        bid=self.store.begin([1,2,3,4,5],'real',source='manual')
        value=copy.deepcopy(self.store.current);value['timing_prerequisites_bypassed']=True;self.store._save(value)
        rois=[dict(sample=i+1,x=80-i*20,y=5,width=15,height=30) for i in range(5)]
        self.analysis=dict(method='optical-interface-v1',validated=False,rois=rois,
            bottles=[dict(sample=i+1,classification='two_phase' if i==0 else 'uncertain',reason='synthetic fixture',interface_y=20 if i==0 else None) for i in range(5)])
        self.payload=dict(batch_id=bid,png_base64=base64.b64encode(test_png()).decode(),width=100,height=40,
            captured_at=timestamp(),alignment_confirmed=False,rois=rois,analysis=self.analysis)

    def tearDown(self):self.temp.cleanup()

    def test_candidate_analysis_saved_with_original_frame_and_rois(self):
        result=self.store.capture_frame(self.payload)
        self.assertEqual(self.analysis,result['frame'].get('analysis'))
        path=self.store.image_path(self.payload['batch_id'],result['frame']['filename'])
        self.assertEqual(test_png(),path.read_bytes())
        saved=json.loads((path.parent/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual('two_phase',saved['records'][0]['analysis']['bottles'][0]['classification'])
        self.assertNotIn('classification',saved['records'][0])

    def test_unconfirmed_alignment_is_not_fabricated(self):
        result=self.store.capture_frame(self.payload)
        self.assertIs(False,result['frame']['alignment_confirmed'])

    def test_invalid_algorithm_labels_rejected_before_any_image_write(self):
        self.payload['analysis']['bottles'][0]['classification']='proven'
        with self.assertRaises(ValueError):self.store.capture_frame(self.payload)
        self.assertEqual([],list(Path(self.temp.name).rglob('*.png')))

    def test_cannot_claim_algorithm_validated(self):
        self.payload['analysis']['validated']=True
        with self.assertRaises(ValueError):self.store.capture_frame(self.payload)

    def test_analysis_must_reference_exact_capture_rois(self):
        self.payload['analysis']=copy.deepcopy(self.analysis)
        self.payload['analysis']['rois'][0]['x']=79
        with self.assertRaises(ValueError):self.store.capture_frame(self.payload)

    def test_duplicate_or_wrong_sample_labels_rejected(self):
        self.payload['analysis']['bottles'][0]['sample']=5
        with self.assertRaises(ValueError):self.store.capture_frame(self.payload)

    def test_raw_image_without_algorithm_remains_supported(self):
        del self.payload['analysis']
        result=self.store.capture_frame(self.payload)
        self.assertNotIn('analysis',result['frame'])

    def test_explicit_partial_capture_preserves_frame_without_invented_bottles(self):
        self.payload.update(rois=[],allow_partial_rois=True)
        self.payload['analysis']=dict(method='optical-interface-v1',validated=False,rois=[],bottles=[],detection={'reason':'no bottles'})
        result=self.store.capture_frame(self.payload)
        self.assertFalse(result['frame']['roi_complete'])
        self.assertEqual([1,2,3,4,5],result['frame']['unassigned_targets'])
        self.assertEqual([],result['imaging']['records'])
        self.assertTrue(self.store.image_path(self.payload['batch_id'],result['frame']['filename']).is_file())

    def test_partial_roi_cannot_bypass_strict_capture(self):
        value=copy.deepcopy(self.store.current);value.pop('timing_prerequisites_bypassed');self.store._save(value)
        self.payload.update(rois=[],allow_partial_rois=True,alignment_confirmed=True)
        with self.assertRaises(ValueError):self.store.capture_frame(self.payload)

if __name__=='__main__':unittest.main()
