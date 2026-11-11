"""Fixed single-camera frame + ROI archive. Pure stdlib; no image analysis."""
import base64
import binascii
import copy
import json
import re
import secrets
import struct
import zlib
from datetime import datetime,timezone,timedelta
from pathlib import Path

MAX_PNG_BYTES=16*1024*1024
MAX_REQUEST_BYTES=24*1024*1024
BATCH_RE=re.compile(r'^\d{8}_\d{6}_\d{6}_[0-9a-f]{8}$')
FILE_RE=re.compile(r'^F_\d{8}_\d{6}_\d{6}_[0-9a-f]{8}\.png$')

def utc_now():return datetime.now(timezone.utc)
def timestamp():return utc_now().isoformat(timespec='milliseconds')
def parse_time(value):
    if not isinstance(value,str) or len(value)>50:raise ValueError('采集时间必须为带时区的ISO8601时间')
    try:result=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc:raise ValueError('采集时间格式无效') from exc
    if result.tzinfo is None:raise ValueError('采集时间必须包含时区')
    return result.astimezone(timezone.utc)

def validate_png(encoded,width,height):
    if not isinstance(encoded,str) or not encoded or len(encoded)>((MAX_PNG_BYTES+2)//3)*4:
        raise ValueError('PNG编码为空或超过16MiB限制')
    if type(width) is not int or type(height) is not int or not 1<=width<=8192 or not 1<=height<=8192 or width*height>16000000:
        raise ValueError('图像尺寸无效或超过1600万像素')
    try:raw=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error) as exc:raise ValueError('PNG必须使用有效的裸base64编码') from exc
    if len(raw)>MAX_PNG_BYTES or not raw.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('图片必须为PNG且不超过16MiB')
    offset=8;header=None;data=[];ended=False
    while offset<len(raw):
        if len(raw)-offset<12:raise ValueError('PNG分块截断')
        size=struct.unpack('>I',raw[offset:offset+4])[0];kind=raw[offset+4:offset+8]
        if size>MAX_PNG_BYTES or offset+12+size>len(raw):raise ValueError('PNG分块长度错误')
        payload=raw[offset+8:offset+8+size]
        crc=struct.unpack('>I',raw[offset+8+size:offset+12+size])[0]
        if zlib.crc32(kind+payload)&0xffffffff!=crc:raise ValueError('PNG校验失败')
        if header is None:
            if kind!=b'IHDR' or size!=13:raise ValueError('PNG缺少合法IHDR')
            header=struct.unpack('>IIBBBBB',payload)
            if header[:2]!=(width,height):raise ValueError('声明尺寸与PNG实际尺寸不同')
            if header[2]!=8 or header[3] not in (2,6) or header[4:]!=(0,0,0):
                raise ValueError('仅接受浏览器输出的8位RGB/RGBA非隔行PNG')
        elif kind==b'IHDR':raise ValueError('PNG包含重复IHDR')
        elif kind==b'IDAT':data.append(payload)
        elif kind==b'IEND':
            if size or not data:raise ValueError('PNG缺少图像数据或IEND无效')
            ended=True
        elif kind and 65<=kind[0]<=90 and kind!=b'PLTE':raise ValueError('PNG含不支持的关键分块')
        offset+=12+size
        if ended:
            if offset!=len(raw):raise ValueError('PNG尾部存在额外数据')
            break
    if not ended:raise ValueError('PNG没有完整结束')
    stride=width*(4 if header[3]==6 else 3)+1;expected=height*stride
    try:
        decoder=zlib.decompressobj();pixels=decoder.decompress(b''.join(data),expected+1)
        if len(pixels)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('PNG解压数据与尺寸不符')
    except zlib.error as exc:raise ValueError('PNG像素数据损坏') from exc
    if any(pixels[i]>4 for i in range(0,expected,stride)):raise ValueError('PNG滤波类型无效')
    return raw

class CaptureStore:
    def __init__(self,directory):
        self.root=Path(directory)/'images';self.root.mkdir(parents=True,exist_ok=True)
        self.latest=self.root/'latest.json'
        self.current={'batch_id':None,'targets':[],'records':[],'frames':[],'complete':False,'discarded':False,'historical':False}
        if self.latest.exists():
            data=json.loads(self.latest.read_text(encoding='utf-8'))
            if not isinstance(data,dict) or not BATCH_RE.fullmatch(str(data.get('batch_id',''))):
                raise ValueError('采集历史文件无效；请保留证据并人工检查')
            data['historical']=True;self.current=data

    @property
    def pending(self):
        return bool(self.current['batch_id']) and not self.current['complete'] and not self.current['discarded'] and not self.current['historical']

    def _save(self,value):
        batch=self.root/value['batch_id'];batch.mkdir(exist_ok=True)
        for path in [batch/'manifest.json',self.latest]:
            temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
        self.current=value

    def begin(self,targets,hardware_mode,mix_stopped_at=None,nominal_settle_seconds=None,source='automatic'):
        if self.pending:raise ValueError('上一批图像尚未完成；请完成采集或明确放弃')
        if not isinstance(targets,list) or not 1<=len(targets)<=6 or any(type(i) is not int or not 1<=i<=6 for i in targets) or len(set(targets))!=len(targets):
            raise ValueError('采集目标必须为不重复的1～6号样品')
        if hardware_mode not in ['demo','real']:raise ValueError('模式无效')
        if source not in ['automatic','manual']:raise ValueError('采集任务来源无效')
        if mix_stopped_at is not None:parse_time(mix_stopped_at)
        bid=utc_now().strftime('%Y%m%d_%H%M%S_%f')+'_'+secrets.token_hex(4)
        self._save({'batch_id':bid,'targets':list(targets),'records':[],'frames':[],
                    'complete':False,'discarded':False,'historical':False,
                    'hardware_mode':hardware_mode,'ready_at':timestamp(),
                    'mix_stopped_at':mix_stopped_at,'nominal_settle_seconds':nominal_settle_seconds,'source':source})
        return bid

    def archive(self):
        if self.current['batch_id'] and not self.current['historical']:
            value=copy.deepcopy(self.current);value['historical']=True;self._save(value)

    def capture_frame(self,payload):
        current=self.current
        if not isinstance(payload,dict):raise ValueError('全景采集请求必须为JSON对象')
        if not current['batch_id'] or payload.get('batch_id')!=current['batch_id']:raise ValueError('采集批次不存在或已过期')
        if current['discarded'] or current['historical']:raise ValueError('本批次已关闭，只可查看历史图像')
        if payload.get('alignment_confirmed') is not True:raise ValueError('先确认固定光路和全部目标样品ROI')
        captured=parse_time(payload.get('captured_at'))
        if captured<parse_time(current['ready_at']) or captured>utc_now()+timedelta(seconds=10):raise ValueError('采集时间早于批次就绪或晚于本机时间；请核对时钟')
        raw=validate_png(payload.get('png_base64'),payload.get('width'),payload.get('height'))
        rois=payload.get('rois')
        if not isinstance(rois,list) or len(rois)!=len(current['targets']):raise ValueError('必须在同一全景帧提交当前批次全部目标的ROI')
        checked=[];seen=set()
        for roi in rois:
            if not isinstance(roi,dict) or any(type(roi.get(k)) is not int for k in ['sample','x','y','width','height']):raise ValueError('ROI样品号和像素坐标必须为整数')
            sample,x,y,w,h=(roi[k] for k in ['sample','x','y','width','height'])
            if sample not in current['targets'] or sample in seen:raise ValueError('ROI样品号须与当前批次目标一一对应')
            if x<0 or y<0 or w<=0 or h<=0 or x+w>payload['width'] or y+h>payload['height']:raise ValueError('ROI必须完整位于全景图像边界内')
            for other in checked:
                if x<other['x']+other['width'] and other['x']<x+w and y<other['y']+other['height'] and other['y']<y+h:raise ValueError('各样品ROI不可重叠')
            checked.append({'sample':sample,'x':x,'y':y,'width':w,'height':h});seen.add(sample)
        checked.sort(key=lambda r:r['sample'])
        frame_id=utc_now().strftime('%Y%m%d_%H%M%S_%f')+'_'+secrets.token_hex(4)
        filename='F_'+frame_id+'.png'
        received=utc_now()
        elapsed=(received-parse_time(current['mix_stopped_at'])).total_seconds() if current.get('mix_stopped_at') else None
        frame={'frame_id':frame_id,'filename':filename,'captured_at':captured.isoformat(timespec='milliseconds'),
                'received_at':received.isoformat(timespec='milliseconds'),'width':payload['width'],'height':payload['height'],
                'view_direction':'fixed_front_row','hardware_mode':current['hardware_mode'],
                'geometry_signature':current.get('geometry_signature'),
                'source':current['source'],'mix_stopped_at':current.get('mix_stopped_at'),
                'nominal_settle_seconds':current.get('nominal_settle_seconds'),
                'alignment_confirmed':True,'rois':checked,
                'elapsed_settle_seconds':round(elapsed,3) if elapsed is not None else None,
                'timing_basis':'server_receive_minus_operator_stop_confirmation',
                'exposure_note':'one_video_frame_not_global_shutter_guarantee',
                'url':f"/api/images/{current['batch_id']}/{filename}"}
        value=copy.deepcopy(current);value['frames'].append(frame)
        shared={k:v for k,v in frame.items() if k!='rois'}
        value['records']=[dict(shared,sample=roi['sample'],roi={k:v for k,v in roi.items() if k!='sample'}) for roi in checked]
        value['complete']=True
        # No image or per-bottle record is written until the entire request passes validation.
        path=self.root/current['batch_id']/filename
        with path.open('xb') as out:out.write(raw)
        self._save(value)
        return {'ok':True,'frame':copy.deepcopy(frame),'imaging':copy.deepcopy(value)}

    def discard(self,batch_id):
        if batch_id!=self.current['batch_id'] or not batch_id:raise ValueError('采集批次不存在或已过期')
        value=copy.deepcopy(self.current);value['discarded']=True;value['discarded_at']=timestamp();self._save(value)
        return {'ok':True,'imaging':copy.deepcopy(value)}

    def image_path(self,batch_id,filename):
        if not BATCH_RE.fullmatch(batch_id) or not FILE_RE.fullmatch(filename):raise ValueError('图像路径无效')
        folder=(self.root/batch_id).resolve();path=(folder/filename).resolve()
        if not path.is_relative_to(self.root.resolve()):raise ValueError('图像路径越界')
        manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        if filename not in [r['filename'] for r in manifest.get('frames',[])]:raise FileNotFoundError('图像未登记')
        if not path.is_file():raise FileNotFoundError('图像文件不存在')
        return path
