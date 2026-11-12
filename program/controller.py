"""Air-plug commissioning controller. Physical observations are explicit gates.

No conversion between gas displacement and liquid volume is implemented.
Profiles are endpoint-specific empirical recipes, never old water coefficients.
"""
import copy
import hashlib
import json
import math
import secrets
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from capture_store import CaptureStore, parse_time, timestamp
from transport import SerialHardware, HardwareError, _port_name
from serial_discovery import scan_serial_ports
from bench_calibration import BenchMixin
from tube_workflows import TubeMixin
from free_debug import FreeDebugMixin
from motion_planning import motion_plan

ALLOWED_PORTS = set(range(1, 7)) | set(range(11, 17))
MAX_STEPS, MAX_RPM, MAX_SECONDS = 20000, 100, 120
COMMISSION_STEPS, COMMISSION_RPM, COMMISSION_SECONDS = 500, 5, 30

class PendingPlugError(ValueError):
    """No pump movement occurred: retain the loaded plug for explicit recovery."""

def number(v, low, high, name, integer=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high or (integer and type(v) is not int):
        raise ValueError(f'{name}必须在 {low}～{high} 范围内' + ('且为整数' if integer else ''))
    return v

def pulse_checked(p, commissioning=False, air_purge=False):
    if not isinstance(p, dict): raise ValueError('必须填写有限脉冲的步数、转速和超时')
    if ('steps' in p)==('turns' in p):raise ValueError('只填步数或原生整圈数其中一种')
    if 'turns' in p:number(p.get('turns'),1,100,'整圈数',True)
    else:number(p.get('steps'),1,MAX_STEPS,'步数',True)
    number(p.get('rpm'), .1, MAX_RPM, 'rpm')
    number(p.get('timeout_s'), 1, MAX_SECONDS, '超时秒数')
    if round(p['rpm'] * 10) != p['rpm'] * 10: raise ValueError('转速最多一位小数')
    return copy.deepcopy(p)

def signature(config):
    payload = {k: config[k] for k in ('geometry', 'liquids', 'pump', 'valve')}
    payload['communication'] = config.get('communication', {'port':'COM6'})
    payload['reset_pulses'] = {k:config['reset'][k] for k in ('wash','purge')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def profile_parameters(p):
    # Validation records are claims about these exact action and initial-state parameters.
    return {k:v for k,v in p.items() if k not in ('validated','bubble_validated','evidence','geometry_signature')}

def validate_config(cfg):
    if not isinstance(cfg, dict) or cfg.get('strategy') != 'air_plug_v1': raise ValueError('只接受空气液段版配置；旧水载液配置无效')
    communication = cfg.get('communication', {'port':'COM6'})
    if not isinstance(communication, dict) or not isinstance(communication.get('port'), str) or not communication['port'].strip(): raise ValueError('填写 RS485 串口，如 COM6')
    g = cfg['geometry']
    for name in ('A_cm', 'B_cm', 'C_cm'): number(g.get(name), 1, 90, name)
    if math.pi*1.6**2*g['A_cm']*10/1000 <= .2: raise ValueError('A段裸容积必须大于0.20mL；实际还须留经验证的阀死体积、回收损失和泵前余量')
    if sum(g[k] for k in ('A_cm', 'B_cm', 'C_cm')) >= 100: raise ValueError('ABC实测总长须小于100 cm')
    for group, keys in [('sample_cm', range(1,7)), ('source_cm', range(11,16))]:
        for k in keys: number(g[group].get(str(k)), 1, 150, f'{group}/{k}')
    number(g.get('air16_cm'), 1, 100, '16口管长')
    if type(g.get('measured')) is not bool: raise ValueError('measured必须为布尔值')
    if not isinstance(cfg.get('profiles'), list): raise ValueError('profiles须为列表')
    if len(cfg['profiles']) > 500: raise ValueError('配置最多500个配方校准档案')
    if not isinstance(cfg.get('liquids'), dict): raise ValueError('填写母液身份/浓度/批次')
    for p in cfg['profiles']: validate_profile(p, cfg, require_validated=False)
    for key in ('wash', 'purge'): pulse_checked(cfg['reset'][key])
    signature(cfg)
    return copy.deepcopy(cfg)

def validate_profile(p, cfg, require_validated=True):
    if not isinstance(p, dict) or not isinstance(p.get('id'), str) or not p['id'] or len(p['id']) > 100: raise ValueError('校准档案必须有短ID')
    number(p.get('source'), 11, 15, '来源口', True); number(p.get('sample'), 1, 6, '样品口', True)
    if p.get('condition') not in ('first', 'continued'): raise ValueError('校准档案须区分首次/连续交付')
    # This delivery deliberately validates only the requested 0.20 mL experiment.
    if p.get('target_ml') != .2: raise ValueError('本版仅接受0.20 mL单次验证档案；其他体积须扩展并验证')
    for key in ('aspirate', 'chase', 'bubble'): pulse_checked(p.get(key))
    pulse_checked(p['bubble'],True)
    number(p['bubble'].get('steps'),1,500,'鼓泡步数',True)
    number(p['bubble'].get('rpm'),.1,5,'鼓泡rpm')
    number(p['bubble']['timeout_s'],1,5,'单次鼓泡动作超时秒数（含通信）')
    number(p.get('bubble_count'), 1, 3, '鼓泡脉冲次数', True)
    number(p.get('bubble_gap_s'), .1, 5, '鼓泡脉冲间隔')
    number(p.get('settle_s'), 0, 86400, '静置秒数')
    number(p.get('max_hold_s'), 1, 86400, '可接受停留秒数')
    number(p.get('max_plug_hold_s'), 1, 600, 'A段液塞最长等待秒数')
    number(p.get('initial_dose_count_min'),0,4,'初始交付次数下限',True)
    number(p.get('initial_dose_count_max'),p['initial_dose_count_min'],4,'初始交付次数上限',True)
    if p['condition']=='first' and (p['initial_dose_count_min']!=0 or p['initial_dose_count_max']!=0): raise ValueError('首次档案只能用于之前未交付的瓶')
    if p['condition']=='continued' and p['initial_dose_count_min']<1: raise ValueError('连续档案必须明确已交付次数范围并覆盖其液位变化')
    number(p.get('immersion_mm'), 0, 100, '交付前管口浸没毫米数，空瓶填0')
    if require_validated:
        if p.get('validated') is not True or p.get('geometry_signature') != signature(cfg): raise ValueError('档案未经本接管/母液/通信配置验证或已失效')
        e = p.get('evidence', {})
        number(e.get('n'), 10, 10000, '端到端重复次数', True)
        number(e.get('mean_ml'), .19, .21, '实际母液等效回收量均值mL')
        number(e.get('rsd_percent'), 0, 5, '回收量RSD%')
        number(e.get('residual_water_ml'), 0, .005, '实测残水mL')
        number(e.get('carryover_percent'), 0, 1, '实测交叉污染%')
        number(e.get('water_blank_n'),3,10000,'残水空白重复次数',True)
        number(e.get('carryover_n'),3,10000,'交叉污染重复次数',True)
        if not isinstance(e.get('note'), str) or len(e['note'].strip()) < 10: raise ValueError('需要可追溯实测记录路径、方法、条件和签核说明')
        if p.get('bubble_validated') is not True: pass  # bubbling is a separate opt-in gate
    return p

class Controller(BenchMixin, TubeMixin, FreeDebugMixin):
    def __init__(self, directory, default_path=None, hardware_factory=None):
        self.directory = Path(directory); self.directory.mkdir(parents=True, exist_ok=True)
        self.default_path = Path(default_path or Path(__file__).with_name('config.default.json'))
        self.config_path = self.directory/'config.json'
        self.config = validate_config(json.loads((self.config_path if self.config_path.exists() else self.default_path).read_text(encoding='utf-8')))
        self.lock = threading.RLock(); self.cancel = threading.Event(); self.ack_event = threading.Event()
        self.hardware = None; self.hardware_factory = hardware_factory
        self.mode = 'real'; self.connected = False; self.busy = False; self.worker = None
        self._user_stopping=False
        self.state = 'DISCONNECTED'; self.message = '扫描 CH340 或手动选择串口，连接后选择要执行的步骤。程序启动不连接或驱动设备。'
        self.prompt = None; self.answer = None; self.logs = []; self.commission_count = 0
        self.capture = CaptureStore(self.directory); self.capture.archive()
        self._unknown()

    def _unknown(self):
        self._reset_bench()
        self._reset_tubes()
        self.volume_run=None
        self.batch_running=False
        self.common = 'unknown'
        self.reset_sequence = None
        self.pending_plug = None
        self.selected_port = None
        self.current_delivery = None
        self.sources = {str(i): {'primed':False,'primed_at':None} for i in range(11,16)}
        self.samples = {str(i): {'condition':'unknown','dose_count':0,'last_gas_stop':None,'clear_at':None,'settle_s':None} for i in range(1,7)}

    def log(self, event, **kw):
        with self.lock:
            record = {'at':timestamp(), 'event':event, **kw}; self.logs.append(record); self.logs = self.logs[-150:]
            with (self.directory/'events.jsonl').open('a',encoding='utf-8') as f: f.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n')

    def _transport_log(self, record): self.log('transport',record=record)

    def _archive_config(self,event,config):
        folder=self.directory/'config_history'; folder.mkdir(exist_ok=True)
        path=folder/(datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'_'+secrets.token_hex(4)+'.json')
        with path.open('x',encoding='utf-8') as f:
            json.dump({'at':timestamp(),'event':event,'geometry_signature':signature(config),'config':config},f,ensure_ascii=False,indent=2,allow_nan=False)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(dict(mode=self.mode,free_debug=self.free_debug,connected=self.connected,busy=self.busy or self._user_stopping,state=self.state,
                message=self.message,next_step=self.prompt['message'] if self.prompt else self.message,prompt=self.prompt,
                common=self.common,reset_sequence=self.reset_sequence,pending_plug=self._pending_view(),selected_port=self.selected_port,
                sources=self.sources,samples=self.samples,logs=self.logs,
                config=self.config,geometry_signature=signature(self.config),profiles=self.profiles(),imaging=self.capture.current,
                bench=self.bench,tubes=self.tubes,volume_run=self.volume_run,bulk_water_reference=self.bulk_water_reference,water_dose_reference=self.water_dose_reference,motion_plan=motion_plan(self.config),
                hardware_status=self.hardware.get_telemetry() if self.hardware and hasattr(self.hardware,'get_telemetry') else None))

    def _pending_view(self):
        if not self.pending_plug: return None
        result=copy.deepcopy(self.pending_plug)
        remaining=result['expires_monotonic']-time.monotonic()
        result.update(remaining_s=max(0,remaining),expired=remaining<=0)
        return result

    def _pending_valid(self,payload=None):
        pending=self.pending_plug
        if not pending: raise PendingPlugError('没有待追送液段；先按档案吸取')
        payload=payload or {}
        for field,key in [('pending_id','id'),('profile_id','profile_id'),('sample','target'),('target','target')]:
            if field in payload and payload[field]!=pending[key]: raise PendingPlugError('液段ID、档案或目标瓶不匹配，禁止追送')
        if time.monotonic()>=pending['expires_monotonic']: raise PendingPlugError('吸取液段后等待超过档案范围；保留待弃液状态，请明确弃液复位或停止，禁止追送')
        if pending['geometry_signature']!=signature(self.config): raise PendingPlugError('液段所用配置已变化；只能明确弃液或停止')
        return pending

    def _pending_guard(self,payload):
        if self.free_debug:return
        if not self.pending_plug:
            if payload.get('action') in ('chase','discard_loaded'): raise ValueError('当前没有待追送液段')
            return
        action=payload.get('action')
        if action=='discard_loaded': return
        if action=='chase': self._pending_valid(payload); return
        if action=='select_port' and type(payload.get('port')) is int and payload['port']==self.pending_plug['target']:
            self._pending_valid(); return
        raise ValueError('A段有待追送液段；只能选择同一目标瓶、追送或明确弃液复位，也可停止')

    def profiles(self): return self.config['profiles']

    def configure(self, cfg):
        with self.lock:
            if self.busy or self.connected: raise ValueError('先停止并断开连接，再修改配置；修改后必须重新确认流路')
            new = validate_config(cfg)
            if signature(new) != signature(self.config):
                for p in new['profiles']: p['validated'] = False; p['bubble_validated'] = False
                new['reset']['validated'] = False
            old_profiles={p['id']:p for p in self.config['profiles']}
            for p in new['profiles']:
                old=old_profiles.get(p['id'])
                if old is not None and profile_parameters(p)!=profile_parameters(old):
                    p['validated']=False; p['bubble_validated']=False
            ids=[p['id'] for p in new['profiles']]
            if len(set(ids)) != len(ids): raise ValueError('校准档案ID不可重复')
            self._archive_config('save',new)
            path = self.config_path.with_suffix('.tmp'); path.write_text(json.dumps(new,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8'); path.replace(self.config_path)
            self.config = new; self._unknown(); self.log('config_saved',signature=signature(new))

    def connect(self, mode='real', pump_port='', valve_port='', auto_detect=False):
        with self.lock:
            if self._user_stopping:raise ValueError('正在确认停止，请稍候')
            if self.busy: raise ValueError('操作仍在运行；先停止并等待退出')
            if self.pending_plug: raise ValueError('仍有待追送液段；明确弃液复位或停止后再连接')
            if mode != 'real': raise ValueError('本版仅支持实机控制，不提供模拟模式')
            if type(auto_detect) is not bool: raise ValueError('auto_detect 必须为布尔值')
            port = self.config.get('communication',{}).get('port','COM6')
            if auto_detect:
                if self.connected: raise ValueError(f"已连接 {port}；切换串口前请先断开连接")
                discovered = scan_serial_ports()
                if not discovered['suggested_port']: raise ValueError(discovered['message'])
                pump_port = valve_port = discovered['suggested_port']
            pump_port = _port_name(str(pump_port or port))
            valve_port = _port_name(str(valve_port or pump_port))
            if pump_port.casefold() != valve_port.casefold(): raise ValueError('本版泵和阀共用同一 RS485 串口')
            if self.connected: raise ValueError(f"已连接 {port}；切换串口前请先断开连接")
            connected_config = copy.deepcopy(self.config)
            connected_config.setdefault('communication', {})['port'] = pump_port
            if signature(connected_config) != signature(self.config):
                for p in connected_config['profiles']: p['validated'] = False; p['bubble_validated'] = False
                connected_config['reset']['validated'] = False
            if self.hardware: self.hardware.close()
            self.hardware = None; self.connected = False; self.state = 'DISCONNECTED'
            self._unknown(); self.cancel.clear(); self.prompt=None
            h = SerialHardware(connected_config,pump_port,valve_port,self._transport_log)
            if self.hardware_factory: h = self.hardware_factory(connected_config)
            try:
                h.connect()
                self._archive_config('connect_'+mode,connected_config)
            except Exception as exc:
                try: h.close()
                except Exception: pass
                self.message = f'连接 {pump_port} 失败：{exc}'
                raise
            self.config=connected_config; self.hardware=h; self.mode=mode; self.connected=True; self.commission_count=0
            self.state='CONNECTED'; self.message=f'已连接 {pump_port}。先确认更换/清洁六个样品瓶和样品管，再执行公共管清洗复位。'; self.log('connected',mode=mode,port=pump_port,auto_detect=auto_detect)

    def disconnect(self):
        if self._user_stopping:raise ValueError('正在确认停止，请稍候再断开')
        self.stop()
        with self.lock:
            if self.hardware: self.hardware.close()
            self.connected=False

    def stop_keep_connection(self):
        """Cancel the task, confirm stopping, then reuse the same open serial port."""
        with self.lock:
            if self._user_stopping:return
            if not self.connected or not self.hardware:
                self.stop();return
            self._user_stopping=True
            self.cancel.set();self.ack_event.set();self.prompt=None
            self.state='STOPPING';self.message='正在停止并核对设备；保留串口连接。'
            worker=self.worker;hardware=self.hardware
        try:
            hardware.stop()
            if worker and worker is not threading.current_thread():
                worker.join(5)
                if worker.is_alive():raise HardwareError('当前任务未及时退出，停止状态未恢复')
            hardware.recover_after_stop()
            with self.lock:
                self._unknown();self.cancel.clear();self.busy=False
                self.state='STOPPED';self.connected=True
                self.message='已确认停止，串口连接保留。当前任务已取消，可直接执行下一项操作。'
            self.capture.archive()
            self.log('user_stop_confirmed_connection_retained',port=self.config.get('communication',{}).get('port'),task_resumed=False)
        except Exception:
            self.stop()
            raise
        finally:
            with self.lock:self._user_stopping=False

    def stop(self):
        self.cancel.set(); self.ack_event.set()
        with self.lock:
            interrupted_plug=copy.deepcopy(self.pending_plug)
            self.prompt=None; self.state='STOPPED'; self.message='已请求软件停止；请确认设备确已停止。流路状态未知，重新连接后复位。'; self._unknown(); self.connected=False
        try:
            if self.hardware: self.hardware.stop()
        finally:
            # Disk failures must never prevent the priority stop request.
            try: self.capture.archive()
            except Exception: pass
            try: self.log('stop_requested_physical_stop_unconfirmed',interrupted_plug=interrupted_plug)
            except Exception: pass

    def _check(self):
        if self.cancel.is_set(): raise HardwareError('操作被停止；需要重新连接')

    def _status(self, state, message):
        with self.lock: self._check(); self.state=state; self.message=message
        self.log('state',state=state,message=message)

    def _wait(self, seconds):
        if self.cancel.wait(seconds): self._check()

    def _ask(self, message, kind='confirm', choices=None):
        self._check()
        with self.lock:
            self.ack_event.clear(); self.answer=None
            self.prompt={'id':secrets.token_hex(12),'message':message,'kind':kind,'choices':choices or ['confirm','abort']}
        self.log('observation_required',message=message)
        if not self.ack_event.wait(600): raise HardwareError('人工观察等待超过10分钟；停止并重新复位')
        self._check()
        with self.lock: answer=self.answer; self.prompt=None
        if answer == 'abort': raise HardwareError('操作者取消；流路状态需重新确认')
        return answer

    def acknowledge(self, payload):
        with self.lock:
            if not self.prompt or payload.get('id') != self.prompt['id'] or self.ack_event.is_set(): raise ValueError('确认已过期或不是当前步骤')
            answer=payload.get('choice')
            if answer is None: answer='confirm' if payload.get('ok') is True else 'abort'
            if answer not in self.prompt['choices']: raise ValueError('请选择当前提示中的有效选项')
            self.answer=answer; self.ack_event.set(); self.log('operator_ack',prompt_id=self.prompt['id'],choice=answer)

    def _routine_observation(self,message):
        if self.batch_running:
            self.log('validated_batch_procedure',message=message,automatic_physical_observation=False)
        else:self._ask(message)

    def _pulse(self, port, direction, p, commissioning=False, before_move=None, manual_direction=False):
        pulse_checked(p,commissioning,air_purge=(port==16 and direction=='aspirate'))
        if type(port) is not int or port not in ALLOWED_PORTS: raise ValueError('端口无效；7—10禁用')
        if direction not in ('aspirate','dispense'): raise ValueError('方向无效')
        if not manual_direction:
            if direction == 'dispense' and port not in range(1,7): raise ValueError('反向只允许送向1—6样品口')
            if direction == 'aspirate' and port in range(1,7): raise ValueError('禁止从样品抽吸进入公共管')
        self._check(); self.movement_attempted=True; timed_out=threading.Event()
        def timeout():
            timed_out.set(); self.cancel.set()
            try: self.hardware.stop()
            except Exception: pass
        timer=threading.Timer(p['timeout_s'],timeout); timer.daemon=True; timer.start()
        try:
            self.hardware.select(port,self.cancel)
            self.selected_port=port
            if self.pending_plug and port==self.pending_plug['target'] and direction=='dispense':
                self.pending_plug.update(target_connected=True,target_selected_at=timestamp())
                self.log('loaded_target_connected',plug_id=self.pending_plug['id'],port=port,target_selected_at=self.pending_plug['target_selected_at'])
            if before_move: before_move()
            if 'turns' in p:self.hardware.move_turns(direction,p['turns'],p['rpm'],self.cancel)
            else:self.hardware.move(direction,p['steps'],p['rpm'],self.cancel)
            self._check()
        finally:
            timer.cancel(); timer.join()
        if timed_out.is_set(): raise HardwareError('有限脉冲超时；停止锁定，禁止盲目重试')
        self.log('pulse_completed',port=port,direction=direction,**p)

    def _select_port(self,port):
        if type(port) is not int or port not in ALLOWED_PORTS: raise ValueError('端口无效；7—10禁用')
        timeout_s=number(self.config['valve'].get('timeout'),1,120,'选口超时秒数')
        self._check(); self.movement_attempted=True
        def timeout():
            self.cancel.set()
            try: self.hardware.stop()
            except Exception: pass
        timer=threading.Timer(timeout_s,timeout); timer.daemon=True; timer.start()
        try:
            self.hardware.select(port,self.cancel); self._check(); self.selected_port=port
        finally:
            timer.cancel(); timer.join()
        if self.pending_plug:
            self.pending_plug.update(target_selected_at=timestamp(),target_preselected=True,target_connected=True)
            self.log('loaded_target_preselected',plug_id=self.pending_plug['id'],port=port,
                     expires_at=self.pending_plug['expires_at'],target_selected_at=self.pending_plug['target_selected_at'])
        else:
            # An arbitrary passive connection is not evidence of the prior fluid state.
            self.common='unknown'; self.reset_sequence=None; self.current_delivery=None
        self.log('port_selected_without_pump_motion',port=port)

    def _reset_recipe_checked(self,allow_trial=False):
        r=self.config['reset']
        complete=r.get('validated') is True and bool(r.get('evidence','').strip()) and r.get('covers_initial_air_filled_water_branch') is True
        if allow_trial and not complete:
            trial=copy.deepcopy(r)
            for key in ('wash','purge'): pulse_checked(trial[key],True,air_purge=(key=='purge'))
            trial['_trial']=True
            return trial
        if self.mode=='real' and (r.get('validated') is not True or not r.get('evidence','').strip()): raise ValueError('先通过有限步数预实验验证清洗、16口吹扫及残水上限，再启用复位程序')
        if self.mode=='real' and r.get('covers_initial_air_filled_water_branch') is not True: raise ValueError('复位验证必须包含15水支管最初充空气的首次清洗条件，不能沿用仅满水支管下的洗涤脉冲')
        return r

    def _wash(self,allow_trial=False):
        r=self._reset_recipe_checked(allow_trial); trial=r.get('_trial',False)
        self._status('WASH','15口正向清水冲洗公共管到废液。')
        self.log('wash_started',trial=trial,setup='15浸水，C端对准废液；点击运行即按页面接管执行')
        self.current_delivery=None; self.reset_sequence=None; self.common='wet'
        self._pulse(15,'aspirate',r['wash'],commissioning=trial)
        self.sources['15']={'primed':not trial,'primed_at':timestamp() if not trial else None}
        self.reset_sequence='washed_unverified' if trial else 'washed'
        if trial:self.log('unvalidated_wash_completed',pulse=r['wash'],cleaning_validated=False)
        self._status('WASHED_UNVERIFIED' if trial else 'WASHED',
                     '15口有限试洗已停泵；观察液头及C端，尚未证明引满或洗净。' if trial else
                     '15口清洗完成，公共管仍湿；需另执行16口吹扫并观察。')

    def _purge(self,allow_trial=False):
        r=self._reset_recipe_checked(allow_trial); trial=r.get('_trial',False)
        washed=self.reset_sequence=='washed'; trial_washed=self.reset_sequence=='washed_unverified'
        self.log('purge_started',trial=trial,setup='16清洁空气，C端对准废液；点击运行即按页面接管执行')
        self.current_delivery=None; self.common='air_unverified'; self.reset_sequence=None
        self._status('PURGE','切16口，正向空气排出公共管内主体残水。')
        self._pulse(16,'aspirate',r['purge'],commissioning=trial)
        if trial:
            self.reset_sequence='purged_unverified'
            self.log('unvalidated_purge_completed',pulse=r['purge'],prior_trial_wash=trial_washed,cleaning_validated=False)
            self._status('PURGED_UNVERIFIED','试吹扫脉冲已完成；检查可见液塞和C端悬滴。仍未验证残水，不会解锁正式吸取。')
            return
        self._routine_observation('确认无可见液塞；C端无悬滴、无飞溅污染；本次符合已验证的残水范围。通气不代表绝对干燥。')
        if washed:
            self.reset_sequence='purged'; self.common='air_reset_verified'
            self._status('RESET_READY','公共管达到已验证的洗后空气复位条件。来源支管仍保持各自液体；样品支管不自动恢复空管。')
        else: self._status('PURGED_UNVERIFIED','已吹扫，但缺少紧邻的已验证15口清洗步骤；公共管尚不可正式吸取。')

    def _reset(self,allow_trial=False):
        self._wash(allow_trial); self._purge(allow_trial)

    def _prime(self, source, p, max_pulses=20):
        number(source,11,15,'来源口',True); pulse_checked(p,True); number(max_pulses,1,20,'预充最多脉冲数',True)
        if self.common not in ('air_reset_verified','air_unverified'): raise ValueError('先排去公共管连续水柱，再做来源引液；试验吹扫后也可引液，不要求先签核定量档案')
        self.sources[str(source)]={'primed':False,'primed_at':None}
        self._ask(f'确认公共管无连续水柱；{source}口液体、液位正确，管口浸没；观察A段标线。若来源支管已满，不要再用整圈大脉冲预充。液段不得进入泵头；此操作不签核清洗质量。')
        self.common='stock'; self.reset_sequence=None; self.current_delivery=None
        self._status('PRIMING',f'正在逐脉冲预充{source}口；每次都停泵等待观察。')
        for _ in range(max_pulses):
            self._pulse(source,'aspirate',p,True)
            result=self._ask('液体前沿是否已到A段预充标线？到达即停止；未到达才能再走一个同样脉冲；接近泵头/发现漏气立即取消。','prime_front',['reached','more','abort'])
            if result=='reached':
                self.sources[str(source)]={'primed':True,'primed_at':timestamp()}
                self._status('PRIMED','来源支管已人工确认预充。执行15清洗＋16吹扫复位，再吸取正式液段。'); return
        raise HardwareError('预充达到最多20次以内的本次上限，仍未确认液面；停止检查，禁止无限抽气')

    def _profile(self, pid):
        found=[p for p in self.profiles() if p['id']==pid]
        if len(found)!=1: raise ValueError('校准档案不存在')
        p=copy.deepcopy(found[0]); validate_profile(p,self.config,self.mode=='real'); return p

    def _check_dose(self,p):
        if not self.sources[str(p['source'])]['primed']: raise ValueError(f"{p['source']}口尚未人工确认预充")
        sample=self.samples[str(p['sample'])]
        if sample['dose_count'] >= 5: raise ValueError('该瓶已达5次0.20mL目标交付；本版1mL目标批次不得继续加料')
        if not p['initial_dose_count_min']<=sample['dose_count']<=p['initial_dose_count_max']: raise ValueError('当前瓶内已交付次数/液位超出本档案验证范围')
        if sample['condition'] != p['condition']: raise ValueError('样品管首次/连续状态与校准档案不匹配；不可假设用后自动空管')
        primed=parse_time(self.sources[str(p['source'])]['primed_at'])
        if (datetime.now(timezone.utc)-primed).total_seconds()>p['max_hold_s']: raise ValueError('来源预充等待已超出档案范围；重新验证液柱/预充')
        if sample['last_gas_stop'] and (datetime.now(timezone.utc)-parse_time(sample['last_gas_stop'])).total_seconds()>p['max_hold_s']: raise ValueError('样品停留时间超出连续加料校准范围；须重新评估回吸状态')

    def _aspirate(self,p):
        if self.pending_plug: raise ValueError('已有待追送液段，不能再次吸取')
        self._check_dose(p)
        if self.common!='air_reset_verified': raise ValueError('公共管还未完成洗后16口空气复位')
        self._routine_observation(f"核对{p['source']}→{p['sample']}、{p['condition']}条件。母液液位、瓶内初始液量、管口浸没{p['immersion_mm']}mm及等待时间须与档案一致；C端清洁干燥无悬滴，瓶盖通气。确认后执行0.20mL目标的经验脉冲，实际量仍以测量为准。")
        self._check_dose(p)
        self.common='stock'; self.reset_sequence=None; self.current_delivery=None
        self._status('ASPIRATE','吸取母液液段到A段，有限步数结束后停泵。')
        self._pulse(p['source'],'aspirate',p['aspirate'])
        now=datetime.now(timezone.utc); mono=time.monotonic()
        self.pending_plug={'id':secrets.token_hex(12),'source':p['source'],'target':p['sample'],
            'profile_id':p['id'],'profile':copy.deepcopy(p),'geometry_signature':signature(self.config),
            'created_at':now.isoformat(timespec='milliseconds'),
            'target_preselected':False,'target_connected':False,
            'expires_at':(now+timedelta(seconds=p['max_plug_hold_s'])).isoformat(timespec='milliseconds'),
            'created_monotonic':mono,'expires_monotonic':mono+p['max_plug_hold_s']}
        self.log('plug_loaded',pending_plug=self.pending_plug)
        self._status('PLUG_PENDING','液段已吸取并停泵；立即观察A段位置，随后追送同一目标或明确弃液。超时不会自动移动。')

    def _chase(self,payload=None):
        pending=self._pending_valid(payload); p=pending['profile']
        self._check_dose(p)
        self._routine_observation(f"观察液段位于A段、与泵头保留已验证距离，未进入泵头。目标必须为{pending['target']}号瓶；确认切到此样品并反向追送，异常请取消。")
        self._pending_valid(payload); self._check_dose(p)
        self._status('CHASE','停泵后切到样品口，反向从C端吸气追送液段。')
        self._pulse(p['sample'],'dispense',p['chase'],before_move=lambda:self._pending_valid(payload))
        s=self.samples[str(p['sample'])]
        s.update(condition='continued',dose_count=s['dose_count']+1,last_gas_stop=timestamp(),clear_at=None,
                 settle_s=p['settle_s'],timing_basis='server_confirmed_finite_command_completion',profile_id=p['id'])
        self.pending_plug=None; self.common='wet'
        self.current_delivery=(p['sample'],p['id'])
        self.log('plug_delivery_completed',plug_id=pending['id'],sample=p['sample'],profile_id=p['id'],target_ml=p['target_ml'])
        self._status('DELIVERED','交付动作结束，实际回收量待实验验证。样品管为已用湿管；下次加料前复位公共管。')

    def _discard_loaded(self):
        if not self.pending_plug: raise ValueError('没有待弃液段')
        self._reset_recipe_checked()
        pending=copy.deepcopy(self.pending_plug)
        self._ask('明确弃去A段内当前液段，不送入样品瓶、不计交付；确认C端废液容量及通气正常。随后15口正向清洗带出液段到废液，再16口吹扫。')
        self._reset()
        self.pending_plug=None
        if pending.get('target_connected'):
            self.samples[str(pending['target'])].update(condition='unknown',uncertain_delivery=True,
                uncertainty_note='液段曾与样品连通后弃去；不能排除被动迁移，不增加成功交付次数，须人工评估或更换该样品及支管。',
                clear_at=None,last_gas_stop=None,settle_s=None,profile_id=None,timing_basis=None)
        self.log('plug_discarded_to_waste',plug_id=pending['id'],pending_plug=pending)

    def _dose(self,p,bubble=False):
        if bubble: self._validate_bubble(p)
        self._aspirate(p); self._chase({'pending_id':self.pending_plug['id']})
        if bubble: self._bubble(p)
        self._status('DELIVERED','组合吸取＋追送已结束（不含清洗）；公共管仍湿，可继续本瓶鼓泡或执行清洗复位。')

    def _validate_bubble(self,p):
        number(p.get('bubble_immersion_mm'), .1, 100, '鼓泡时实际管口浸没毫米数')
        if self.mode=='real' and p.get('bubble_validated') is not True: raise ValueError('鼓泡充分性、飞溅、消泡时间尚未通过与磁搅对照验证')

    def _bubble(self,p):
        self._validate_bubble(p)
        s=self.samples[str(p['sample'])]
        if s['condition']=='unknown' or not s['last_gas_stop']: raise ValueError('先完成该样品交付，才能鼓泡')
        if self.common!='wet': raise ValueError('独立鼓泡须在本样品交付后、公共管清洗前执行；否则残水可能额外交付')
        if self.current_delivery != (p['sample'],p['id']): raise ValueError('只能继续刚完成交付的同一样品和同一校准档案鼓泡')
        self._routine_observation('确认当前样品口管端在液面下、靠近瓶底但不贴底，瓶盖通气；C端清洁；全程观察泡沫/飞溅，无气泡或异常压力立即停止。')
        if (datetime.now(timezone.utc)-parse_time(s['last_gas_stop'])).total_seconds()>p['max_hold_s']: raise ValueError('交付后等待已超过鼓泡档案允许范围')
        self._status('BUBBLE',f"逐瓶鼓泡：样品{p['sample']}，最多{p['bubble_count']}个有限脉冲。")
        for i in range(p['bubble_count']):
            self._pulse(p['sample'],'dispense',p['bubble'])
            s.update(last_gas_stop=timestamp(),clear_at=None,settle_s=p['settle_s'],timing_basis='server_confirmed_finite_command_completion')
            if i+1<p['bubble_count']: self._wait(p['bubble_gap_s'])

    def _run_action(self,payload):
        action=payload.get('action')
        self._pending_guard(payload)
        if self.free_debug and self._free_action(payload):return
        if action not in ('tubes','clear','magnetic_stop','prepare_capture','capture'):
            self._reset_tubes()
        if action in ('new_samples','select_port','wash','purge','reset','aspirate','chase','discard_loaded','prime','dose','bubble','run','commission'):
            self.bench.update(water_ready=False,air_ready=False,delivery_seen=False,delivery_seen_context=None,last_run=None)
        if action=='tubes':self._tube_action(payload)
        elif action=='bench':self._bench_action(payload)
        elif action=='new_samples':
            self._ask('确认1—6样品瓶及支管已清洁并达到已验证初始条件，无水柱/样品残留；瓶盖通气。不得对已装样品直接重置此状态。')
            for s in self.samples.values(): s.update(condition='first',dose_count=0,last_gas_stop=None,clear_at=None,settle_s=None,uncertain_delivery=False,uncertainty_note=None,profile_id=None,timing_basis=None)
            self.current_delivery=None
        elif action=='select_port': self._select_port(payload.get('port'))
        elif action=='wash': self._wash(allow_trial=True)
        elif action=='purge': self._purge(allow_trial=True)
        elif action=='reset': self._reset(allow_trial=True)
        elif action=='aspirate': self._aspirate(self._profile(payload.get('profile_id')))
        elif action=='chase': self._chase(payload)
        elif action=='discard_loaded': self._discard_loaded()
        elif action=='prime': self._prime(payload.get('source'),payload.get('pulse'),payload.get('max_pulses',20))
        elif action in ('dose','bubble'):
            p=self._profile(payload.get('profile_id'))
            if action=='dose': self._dose(p,payload.get('bubble') is True)
            else: self._bubble(p)
        elif action=='run':
            items=payload.get('items')
            if not isinstance(items,list) or not 1<=len(items)<=60: raise ValueError('自动队列须含1—60次交付')
            planned={k:v['condition'] for k,v in self.samples.items()}
            counts={k:v['dose_count'] for k,v in self.samples.items()}
            checked=[]
            for item in items:
                p=self._profile(item.get('profile_id')); key=str(p['sample'])
                if item.get('bubble') is True: self._validate_bubble(p)
                if planned[key]!=p['condition']: raise ValueError('队列首次/连续档案顺序不匹配')
                if not p['initial_dose_count_min']<=counts[key]<=p['initial_dose_count_max']: raise ValueError('队列某次加料的初始液量/次数超出档案验证范围')
                counts[key]+=1
                if counts[key]>5: raise ValueError('队列超过每瓶1mL目标的5次交付上限')
                if not self.sources[str(p['source'])]['primed']: raise ValueError(f"先预充{p['source']}口")
                planned[key]='continued'; checked.append((p,item.get('bubble') is True))
            self._reset_recipe_checked()
            self._ask('确认整批来源、目标瓶、液位、通气及C端清洁符合所选已验证档案；开始后自动执行整批，不逐剂弹窗。请在旁观察，发现未送完、飞溅或异常立即停止。')
            self.batch_running=True
            try:
                if self.common!='air_reset_verified': self._reset()
                for p,b in checked:
                    self._dose(p,b); self._reset()
            finally:self.batch_running=False
        elif action=='commission':
            port=payload.get('port'); direction=payload.get('direction')
            p=pulse_checked(payload.get('pulse'),True,air_purge=(port==16 and direction=='aspirate'))
            self.commission_count+=1; self._unknown(); self._pulse(port,direction,p,True)
        elif action=='confirm_reset':
            self._reset_recipe_checked()
            if self.reset_sequence!='purged': raise ValueError('本连接没有已完成的15清洗→16吹扫记录；人工确认不能替代实际步骤')
            self._ask('确认已按已验证的15清洗＋16吹扫步骤操作，公共管无可见液塞，C端无悬滴，残水/交叉污染界限合格；此确认不等于绝对干燥。')
            self.common='air_reset_verified'
        elif action=='clear':
            sample=payload.get('sample'); number(sample,1,6,'样品',True); s=self.samples[str(sample)]
            if not s['last_gas_stop']: raise ValueError('该样品没有停气或磁搅停止时间')
            self.log('operator_confirmed_clear',sample=sample)
            s['clear_at']=timestamp()
        elif action=='magnetic_stop':
            sample=payload.get('sample'); number(sample,1,6,'样品',True); number(payload.get('settle_s'),1,86400,'静置秒数')
            self._ask(f'手动确认样品{sample}磁力搅拌已停止；软件不能控制此搅拌器。记录的是确认时间，不是硬件测得的停转时间。')
            self.samples[str(sample)].update(last_gas_stop=timestamp(),clear_at=None,settle_s=payload['settle_s'],timing_basis='operator_confirmed_magnetic_stop')
        elif action in ('prepare_capture','capture'): self._prepare_capture(payload.get('targets'))
        else: raise ValueError('未知操作')

    def start(self,payload):
        with self.lock:
            if self._user_stopping:raise ValueError('正在确认停止，请稍候')
            if not isinstance(payload,dict): raise ValueError('操作必须为对象')
            if not self.connected or self.cancel.is_set(): raise ValueError('先连接；停止锁定后必须重新连接')
            if self.busy: raise ValueError('已有操作执行或等待确认；请完成当前步骤')
            self._pending_guard(payload)
            if not self.free_debug and self.capture.pending and payload.get('action') not in ('clear',): raise ValueError('先完成或明确放弃当前图像批次，再开始新的液路操作')
            self.busy=True; self.movement_attempted=False
            def worker():
                try:
                    self._run_action(payload); self._check()
                    if self.pending_plug:
                        self._status('PLUG_PENDING','液段已吸取并停泵；只可追送同一目标或明确弃液。等待计时继续，超时不会自动移动。')
                    elif self.free_debug or payload.get('action') in ('bench','tubes'):
                        self._status('IDLE',self.message if self.message else '校准试验步骤已结束；按页面继续。')
                    elif self.reset_sequence in ('washed_unverified','purged_unverified'):
                        self._status('IDLE','有限清洗/吹扫试验已停泵；请观察液头、C端出水或液塞。残水尚未验证，不会自动成为正式加料就绪。')
                    else: self._status('IDLE','当前操作已结束。按下一步继续；仪器动作完成不代表体积或判相已验证。')
                except Exception as exc:
                    if self._user_stopping:
                        self.log('task_cancelled_by_user_stop',error=str(exc));return
                    if isinstance(exc,PendingPlugError) and self.pending_plug and not self.cancel.is_set():
                        with self.lock: self.state='PLUG_PENDING'; self.message=str(exc)
                        self.log('plug_chase_rejected',error=str(exc),plug_id=self.pending_plug['id']); return
                    if isinstance(exc,ValueError) and not self.movement_attempted and not self.cancel.is_set():
                        with self.lock: self.state='PLUG_PENDING' if self.pending_plug else 'IDLE'; self.message='操作未执行：'+str(exc)
                        self.log('action_rejected_before_motion',error=str(exc)); return
                    try: self.stop()
                    except Exception: pass
                    with self.lock: self.state='FAULT'; self.message=str(exc)
                    self.log('fault',error=str(exc))
                finally:
                    with self.lock: self.busy=False; self.prompt=None
            self.worker=threading.Thread(target=worker,daemon=True); self.worker.start()

    def _prepare_capture(self,targets):
        if self.pending_plug: raise ValueError('先追送或明确弃去待追送液段')
        if not isinstance(targets,list) or not targets or len(set(targets))!=len(targets) or any(type(i)is not int or i not in range(1,7) for i in targets): raise ValueError('选择1—6不重复样品号')
        now=datetime.now(timezone.utc)
        for n in targets:
            s=self.samples[str(n)]
            if not s['last_gas_stop'] or not s['clear_at']: raise ValueError(f'样品{n}尚未确认停气、消泡和液面稳定')
            if (now-parse_time(s['clear_at'])).total_seconds()<s['settle_s']: raise ValueError(f'样品{n}尚未达到独立静置等待时间')
        self.capture.begin(targets,self.mode,source='automatic')
        value=copy.deepcopy(self.capture.current)
        value.update(sample_timing={str(n):copy.deepcopy(self.samples[str(n)]) for n in targets},
                     geometry_signature=signature(self.config),geometry=copy.deepcopy(self.config['geometry']),view_direction='fixed_front_row')
        self.capture._save(value)

    def capture_frame(self,payload):
        with self.lock:
            if self.pending_plug: raise ValueError('先追送或明确弃去待追送液段')
            if not self.connected or self.cancel.is_set(): raise ValueError('停止/断开后旧图像批次只能查看，不能继续采集')
            if self.busy: raise ValueError('先结束液路操作再拍摄')
            # CaptureStore validates PNG, ROI and timestamp before any writes.
            result=self.capture.capture_frame(payload)
            value=copy.deepcopy(self.capture.current)
            for record in value['records']:
                timing=value['sample_timing'][str(record['sample'])]; at=parse_time(record['captured_at'])
                record['sample_timing']=timing
                record['elapsed_since_stop_s']=(at-parse_time(timing['last_gas_stop'])).total_seconds() if timing.get('last_gas_stop') else None
                record['elapsed_since_clear_s']=(at-parse_time(timing['clear_at'])).total_seconds() if timing.get('clear_at') else None
                record['classification']='unreviewed'
            self.capture._save(value); result['imaging']=copy.deepcopy(value); return result

    def calibration_report(self,payload):
        from calibration_report import analyze_input, summarize
        if not isinstance(payload,dict): raise ValueError('校准数据必须为对象')
        if 'rows' in payload:
            rows=payload['rows']
            if not isinstance(rows,list) or not 1<=len(rows)<=20000 or any(not isinstance(row,dict) for row in rows): raise ValueError('rows须为1—20000行对象')
            report=summarize(rows,payload.get('target_ml',.2))
        else:
            analysis=analyze_input(payload); rows=analysis['rows']; report=analysis['report']
        with self.lock:
            report['geometry_signature']=signature(self.config)
            folder=self.directory/'calibration_reports'; folder.mkdir(exist_ok=True)
            path=folder/(datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'_'+secrets.token_hex(4)+'.json')
            record={'at':timestamp(),'geometry_signature':signature(self.config),'config':copy.deepcopy(self.config),
                    'raw_input':copy.deepcopy(payload),'rows':rows,'report':report}
            with path.open('x',encoding='utf-8') as f: json.dump(record,f,ensure_ascii=False,indent=2,allow_nan=False)
        return {'ok':True,'report':report,'rows':rows,'saved_file':str(path)}

    def label(self,payload):
        with self.lock:
            if self.busy: raise ValueError('操作中不可标注')
            if payload.get('classification') not in ('single','two_phase','uncertain'): raise ValueError('判相只能单相/两相/不确定')
            if not self.capture.current.get('complete'): raise ValueError('先保存原始图像')
            if payload.get('batch_id') != self.capture.current['batch_id']: raise ValueError('图像批次已变更')
            value=copy.deepcopy(self.capture.current); found=False
            for r in value['records']:
                if r['sample']==payload.get('sample'):
                    r.update(classification=payload['classification'],review_note=str(payload.get('note',''))[:2000],reviewed_at=timestamp(),classification_method='human_review'); found=True
            if not found: raise ValueError('当前图像没有该样品')
            self.capture._save(value)
