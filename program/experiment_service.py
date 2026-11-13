"""Persistent experiment scheduler; hardware calls are made outside the state lock."""
import copy, csv, html, io, json, math, secrets, sqlite3, threading, time, zipfile
from contextlib import nullcontext
from pathlib import Path
from datetime import datetime, timezone
from capture_store import timestamp, parse_time, validate_png, validate_analysis
import experiment_domain as domain

def uid():
    return secrets.token_hex(16)

class ExperimentService:

    def __init__(self, controller):
        self.controller = controller
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.directory = Path(controller.directory) / 'experiments'
        self.directory.mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.directory / 'experiments.sqlite3', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        row = self.db.execute('SELECT body FROM state WHERE id=1').fetchone()
        self.data = json.loads(row[0]) if row else dict(stocks=domain.default_stocks(), calibrations=[], projects=[], samples=[], bottles=[dict(bottle=i, sample_id=None) for i in range(1, 6)], photo_jobs=[], rounds=[], camera=dict(ready=False, mapping_version=0), running=False, message='')
        self.data['running'] = False
        self.data['camera']['ready'] = False
        for s in self.data['samples']:
            if s['status'] == 'preparing':
                s['status'] = 'interrupted'
                s['exclusion'] = 'restart interrupted'
        self._save()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _save(self):
        encoded = json.dumps(self.data, ensure_ascii=False, allow_nan=False)
        if getattr(self, '_last_saved', None) == encoded:
            return
        self.db.execute('INSERT OR REPLACE INTO state VALUES (1,?)', (encoded,))
        self.db.commit()
        self._last_saved = encoded

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        self.thread.join(2)
        with self.lock:
            self.data['running'] = False
            self._save()
            self.db.close()

    def _signature(self):
        from controller import signature
        return signature(self.controller.config)

    def _get(self, key, id):
        value = next((x for x in self.data[key] if x['id'] == id), None)
        if value is None:
            raise ValueError('记录不存在')
        return value

    def _preview(self, p, r):
        return domain.preview_recipe(dict(r, salt_port=p['salt_port'], temperature_c=p.get('temperature_c', 25), total_mass_g=r.get('total_mass_g', p.get('total_mass_g', 1)), safe_mass_limit_g=p.get('safe_mass_limit_g')), p['stocks'], p['calibrations'], self._signature())

    def _enqueue(self, p, recipes, bottles=None):
        free = [b for b in self.data['bottles'] if b['sample_id'] is None and (bottles is None or b['bottle'] in bottles)]
        expanded = []
        for r in recipes:
            n = r.get('replicates', 1)
            if type(n) is not int or not 1 <= n <= 5:
                raise ValueError('replicates must be 1..5')
            expanded.extend([r] * n)
        if not expanded or len(expanded) > 100:
            raise ValueError('队列必须为1至100个样品')
        previews = [self._preview(p, r) for r in expanded]
        if any((not x['executable'] for x in previews)):
            raise ValueError('配方缺少有效标定或安全条件')
        result = []
        for i, v in enumerate(previews):
            b = free[i] if i < len(free) else dict(bottle=None, sample_id=None)
            s = dict(id=uid(), project_id=p['id'], bottle=b['bottle'], status='queued', target=v['target'], recipe=v, mixed_at=None, frames=[], label=None, exclusion='', generation=0)
            self.data['samples'].append(s)
            b['sample_id'] = s['id']
            result.append(s)
        return result

    def handle(self, op, body):
        if op == 'stop':
            with getattr(self.controller, 'lock', nullcontext()):
                with self.lock:
                    self.data['running'] = False
                    self._save()
            self.controller.stop_keep_connection()
            return dict(ok=True)
        if op in ('start', 'pause'):
            with getattr(self.controller, 'lock', nullcontext()):
                return self._handle(op, body)
        return self._handle(op, body)

    def _handle(self, op, body):
        if op == 'start':
            state = self.controller.snapshot()
            if not state.get('connected') or state.get('busy'):
                raise ValueError('设备未连接或忙碌')
        if op == 'stop':
            with self.lock:
                self.data['running'] = False
                self._save()
            self.controller.stop_keep_connection()
            return dict(ok=True)
        with self.lock:
            result = {}
            if op == 'stock':
                v = domain.validate_stock(body)
                old = next((s for s in self.data['stocks'] if s['port'] == v['port']), None)
                v['version'] = (old or {}).get('version', 0) + 1
                self.data['stocks'] = [s for s in self.data['stocks'] if s['port'] != v['port']] + [v]
            elif op == 'calibration':
                stock = next((s for s in self.data['stocks'] if s['port'] == body['source']))
                v = domain.make_calibration(body, stock, self._signature())
                self.data['calibrations'].append(v)
            elif op == 'project':
                v = dict(body, id=uid(), stocks=copy.deepcopy(self.data['stocks']), calibrations=copy.deepcopy(self.data['calibrations']))
                for k, d in dict(name='实验', salt_port=10, temperature_c=25, total_mass_g=1, photo_offsets_s=[60, 180, 300, 600], min_settle_s=300, max_observe_s=1800).items():
                    v.setdefault(k, d)
                if v['salt_port'] not in (10, 11, 12, 13):
                    raise ValueError('盐来源无效')
                for k in ('total_mass_g', 'min_settle_s', 'max_observe_s'):
                    if not isinstance(v[k], (int, float)) or not math.isfinite(v[k]) or v[k] < 0:
                        raise ValueError('设置必须为有限非负数')
                if not isinstance(v['photo_offsets_s'], list) or any((not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0 for x in v['photo_offsets_s'])):
                    raise ValueError('拍照时间无效')
                if not -20 <= v['temperature_c'] <= 100 or not 0 < v['total_mass_g'] <= 100 or (not 0 <= v['min_settle_s'] <= v['max_observe_s'] <= 86400):
                    raise ValueError('实验条件越界')
                offsets = v['photo_offsets_s']
                if not 1 <= len(offsets) <= 20 or len(set(offsets)) != len(offsets) or any((x > v['max_observe_s'] for x in offsets)):
                    raise ValueError('拍照时间必须唯一并位于观察窗口')
                v['photo_offsets_s'] = sorted(offsets)
                self.data['projects'].append(v)
                result['project'] = v
            elif op == 'preview':
                p = self._get('projects', body['project_id']) if body.get('project_id') else dict(body, stocks=self.data['stocks'], calibrations=self.data['calibrations'])
                result['preview'] = self._preview(p, body)
            elif op == 'enqueue':
                result['samples'] = self._enqueue(self._get('projects', body['project_id']), body['recipes'], body.get('bottles'))
            elif op == 'start':
                cam = self.data['camera']
                if not cam.get('ready') or time.time() - cam.get('heartbeat', 0) > 15:
                    raise ValueError('相机未在线')
                for s in self.data['samples']:
                    if s['status'] == 'queued':
                        p = self._get('projects', s['project_id'])
                        if p['stocks'] != self.data['stocks']:
                            raise ValueError('母液已变化，请新建实验项目')
                        if not self._preview(p, s['target'])['executable']:
                            raise ValueError('配方条件已失效')
                self.data['running'] = True
            elif op == 'pause':
                self.data['running'] = False
            elif op == 'release':
                if body.get('confirmed_empty') is not True:
                    raise ValueError('请确认已更换干净空瓶')
                b = next((b for b in self.data['bottles'] if b['bottle'] == body['bottle']))
                if b['sample_id'] and self._get('samples', b['sample_id'])['status'] == 'preparing':
                    raise ValueError('运行瓶不可释放')
                if b['sample_id']:
                    self._get('samples', b['sample_id'])['status'] = 'released'
                for j in self.data['photo_jobs']:
                    if j['sample_id'] == b['sample_id'] and j['status'] != 'captured':
                        j['status'] = 'cancelled'
                b['sample_id'] = None
            elif op == 'camera':
                c = self.data['camera']
                rois = body.get('rois', [])
                if body.get('ready') and (len(rois) != 5 or {r.get('sample') for r in rois} != {1, 2, 3, 4, 5}):
                    raise ValueError('需要五瓶ROI')
                checked = []
                for r in rois:
                    vals = [r.get(k) for k in ('x', 'y', 'width', 'height')]
                    if any((type(v) is not int for v in vals)):
                        raise ValueError('ROI必须为整数')
                    x, y, w, h = vals
                    if x < 0 or y < 0 or w <= 0 or (h <= 0) or (x + w > body.get('width', 0)) or (y + h > body.get('height', 0)):
                        raise ValueError('ROI越界')
                    if any((x < o['x'] + o['width'] and o['x'] < x + w and (y < o['y'] + o['height']) and (o['y'] < y + h) for o in checked)):
                        raise ValueError('ROI重叠')
                    checked.append(r)
                if any((c.get(k) != body.get(k) for k in ('rois', 'width', 'height'))):
                    c['mapping_version'] += 1
                c.update({k: body.get(k) for k in ('ready', 'rois', 'width', 'height')})
                c['heartbeat'] = time.time()
                result['mapping_version'] = c['mapping_version']
            elif op == 'retry_photo':
                j = self._get('photo_jobs', body['job_id'])
                s = self._get('samples', j['sample_id'])
                if j['generation'] != s['generation']:
                    raise ValueError('旧状态不能补拍')
                existing = next((v for v in self.data['photo_jobs'] if v.get('replaces')==j['id']),None)
                if existing:
                    result['job']=existing
                elif j['status'] != 'captured':
                    j['status'] = 'superseded'
                    replacement = dict(j, id=uid(), status='due', mapping_version=self.data['camera']['mapping_version'], replaces=j['id'])
                    self.data['photo_jobs'].append(replacement)
                    result['job'] = replacement
            elif op == 'capture':
                result['frame'] = self._capture(body)
            elif op in ('review', 'exclude'):
                s = self._get('samples', body['sample_id'])
                if op == 'exclude':
                    reason = body.get('reason', '').strip()
                    if not reason:
                        raise ValueError('排除原因不能为空')
                    s['exclusion'] = reason
                else:
                    if body['classification'] not in ('single', 'two_phase', 'uncertain'):
                        raise ValueError('分类无效')
                    f = next((f for f in s['frames'] if f['id'] == body['frame_id']),None)
                    if f is None:raise ValueError('请选择该样品的照片')
                    p = self._get('projects', s['project_id'])
                    if s.get('label'):s.setdefault('review_history',[]).append(copy.deepcopy(s['label']))
                    s['label'] = dict(body,reviewed_at=timestamp(),generation=s['generation'], eligible=body.get('clear') is True and body['classification'] != 'uncertain' and (f['generation'] == s['generation']) and (f['settle_s'] >= p['min_settle_s']) and (s['status'] in ('settling', 'released')) and bool(s.get('mixed_at')) and (not s['exclusion']))
            elif op == 'suggest':
                p = self._get('projects', body['project_id'])
                samples = []
                for s in self.data['samples']:
                    if s['project_id'] == p['id']:
                        if s.get('label'):
                            s['label']['eligible'] = self._eligible(s, p)
                        label = s['label']['classification'] if not s['exclusion'] and (s.get('label') or {}).get('eligible') else None
                        samples.append(dict(s['target'], id=s['id'], label=label))
                v = domain.suggest_points(samples, dict(body, salt_port=p['salt_port'], peg_stock_fraction=next((x['mass_fraction'] for x in p['stocks'] if x['port'] == 14)), salt_stock_fraction=next((x['mass_fraction'] for x in p['stocks'] if x['port'] == p['salt_port']))))
                v.update(id=uid(), project_id=p['id'], approved=[])
                v['created_at']=timestamp()
                v['training_records']=[dict(id=s['id'],target=copy.deepcopy(s['target']),
                    estimated=copy.deepcopy(s.get('recipe',{}).get('estimated')),label=copy.deepcopy(s['label']))
                    for s in self.data['samples'] if s['id'] in v['training_ids']]
                self.data['rounds'].append(v)
                result['round'] = v
            elif op == 'approve':
                r = self._get('rounds', body['round_id'])
                indices = body['indices']
                if not indices or len(set(indices)) != len(indices) or any((type(i) is not int or i < 0 or i >= len(r['suggestions']) or (i in r['approved']) for i in indices)):
                    raise ValueError('候选选择重复或无效')
                selected = [r['suggestions'][i] for i in indices]
                points = [(v['peg_fraction'], v['salt_fraction']) for v in selected]
                existing = {(v['target']['peg_fraction'], v['target']['salt_fraction']) for v in self.data['samples'] if v['project_id'] == r['project_id']}
                if len(set(points)) != len(points) or any((point in existing for point in points)):
                    raise ValueError('候选点已存在')
                result['samples'] = self._enqueue(self._get('projects', r['project_id']), selected)
                for sample,index in zip(result['samples'],indices):
                    sample['selection']=dict(round_id=r['id'],method=r['summary']['method'],candidate_index=index)
                    r.setdefault('sample_ids',[]).append(sample['id'])
                r['approved'] += indices
            else:
                raise ValueError('未知实验操作')
            self._save()
            return dict(result, ok=True)

    def _eligible(self, s, p):
        label = s.get('label') or {}
        frame = next((f for f in s['frames'] if f['id'] == label.get('frame_id')), None)
        return bool(label.get('clear') is True and label.get('classification') in ('single', 'two_phase') and frame and (frame['generation'] == s['generation']) and (frame['settle_s'] >= p['min_settle_s']) and s.get('mixed_at') and (s['status'] in ('settling', 'released')) and (not s['exclusion']))

    def before_manual(self, payload):
        with self.lock:
            self.data['running'] = False
            if payload.get('action') in ('select_port', 'prepare_capture', 'capture', 'clear', 'confirm_reset', 'new_samples', 'read_status'):
                self._save()
                return
            for s in self.data['samples']:
                target = payload.get('sample') or payload.get('target') or payload.get('debug_parameters', {}).get('sample')
                if payload.get('action') == 'manual_motion':
                    target = payload.get('port')
                if payload.get('action') in ('wash', 'purge', 'prime', 'aspirate', 'reset') and target is None:
                    continue
                if s['status'] != 'released' and (target is None or target == s['bottle']):
                    s['exclusion'] = 'manual mixing' if payload.get('action') == 'bubble' else 'manual composition disturbance'
                    s['generation'] += 1
                    if s.get('label'):
                        s['label']['eligible'] = False
                    if payload.get('action') != 'bubble':
                        s['status'] = 'interrupted'
                    for j in self.data['photo_jobs']:
                        if j['sample_id'] == s['id'] and j['status'] != 'captured':
                            j['status'] = 'cancelled'
            self._save()

    def on_action_event(self, event, payload, plan=None, error=None):
        if not payload.get('experiment_sample_id'):
            if event == 'completed' and payload.get('action') == 'bubble':
                with self.lock:
                    target=payload.get('sample') or payload.get('debug_parameters',{}).get('sample',1)
                    for sample in self.data['samples']:
                        if sample['bottle'] == target and sample['status'] not in ('released', 'queued'):
                            sample['mixed_at'] = timestamp()
                            sample['status'] = 'settling'
                            p = self._get('projects', sample['project_id'])
                            for offset in p['photo_offsets_s']:
                                self.data['photo_jobs'].append(dict(id=uid(), sample_id=sample['id'], bottle=sample['bottle'], generation=sample['generation'], offset_s=offset, due_at=parse_time(sample['mixed_at']).timestamp() + offset, status='waiting', mapping_version=self.data['camera']['mapping_version']))
                    self._save()
            return
        with self.lock:
            s = self._get('samples', payload['experiment_sample_id'])
            s['progress'] = copy.deepcopy(plan)
            s.setdefault('action_events',[]).append(dict(at=timestamp(),event=event,error=str(error) if error else None,
                current=copy.deepcopy((plan or {}).get('current')),completed_commands=(plan or {}).get('completed_commands',0)))
            if event == 'failed':
                s['status'] = 'interrupted'
                s['exclusion'] = str(error)
                self.data['running'] = False
            elif event == 'completed':
                s['status'] = 'settling'
                s['mixed_at'] = (plan or {}).get('mixing_finished_at')
                if not s['mixed_at']:
                    s['status'] = 'interrupted'
                    s['exclusion'] = 'missing mixing evidence'
                    self._save()
                    return
                p = self._get('projects', s['project_id'])
                for offset in p['photo_offsets_s']:
                    self.data['photo_jobs'].append(dict(id=uid(), sample_id=s['id'], bottle=s['bottle'], generation=s['generation'], offset_s=offset, due_at=parse_time(s['mixed_at']).timestamp() + offset, status='waiting', mapping_version=self.data['camera']['mapping_version']))
            self._save()

    def _loop(self):
        while not self.closed.wait(0.25):
            try:
                self.tick()
            except Exception as e:
                with self.lock:
                    self.data['running'] = False
                    self.data['message'] = str(e)
                    self._save()

    def tick(self):
        with getattr(self.controller, 'lock', nullcontext()):
            self._tick_locked()

    def _tick_locked(self):
        state = self.controller.snapshot()
        payload = None
        with self.lock:
            for sample in self.data['samples']:
                if sample['status'] == 'settling' and sample.get('mixed_at'):
                    p = self._get('projects', sample['project_id'])
                    if time.time() - parse_time(sample['mixed_at']).timestamp() > p['max_observe_s'] and sample.get('label') is None:
                        sample['observation_expired'] = True
                        sample['exclusion'] = 'observation window expired without human review'
            for j in self.data['photo_jobs']:
                if j['status'] == 'waiting' and j['due_at'] <= time.time():
                    j['status'] = 'due' if self.data['camera'].get('ready') and time.time() - self.data['camera'].get('heartbeat', 0) < 15 else 'missed'
            if self.data['running'] and state.get('connected') and (not state.get('busy')):
                for queued in self.data['samples']:
                    if queued['status'] == 'queued' and queued['bottle'] is None:
                        free = next((b for b in self.data['bottles'] if b['sample_id'] is None), None)
                        if free:
                            queued['bottle'] = free['bottle']
                            free['sample_id'] = queued['id']
                s = next((s for s in self.data['samples'] if s['status'] == 'queued' and s['bottle'] is not None), None)
                if s:
                    p = self._get('projects', s['project_id'])
                    camera = self.data['camera']
                    if p['stocks'] != self.data['stocks'] or p['calibrations'] != self.data['calibrations'] or (not camera.get('ready')) or (time.time() - camera.get('heartbeat', 0) > 15):
                        self.data['running'] = False
                        self.data['message'] = '母液、标定或相机条件已变化'
                        self._save()
                        return
                    preview = self._preview(p, s['target'])
                    if not preview['executable']:
                        self.data['running'] = False
                        self.data['message'] = '配方条件失效'
                        self._save()
                        return
                    s['recipe'] = preview
                    plan = copy.deepcopy(preview['plan'])
                    plan['sample'] = s['bottle']
                    for row in plan.get('items', []):
                        row['sample'] = s['bottle']
                        for param in row.get('parameters', []):
                            param['sample'] = s['bottle']
                    s['status'] = 'preparing'
                    payload = dict(action='experiment_recipe', experiment_sample_id=s['id'], plan=plan)
            self._save()
        if payload:
            try:
                self.controller.start(payload)
            except Exception as e:
                self.on_action_event('failed', payload, error=e)

    def _capture(self, b):
        j = self._get('photo_jobs', b['job_id'])
        s = self._get('samples', j['sample_id'])
        c = self.data['camera']
        if j['status'] == 'captured':
            return next((f for f in s['frames'] if f['job_id'] == j['id']))
        if not c.get('ready') or time.time() - c.get('heartbeat', 0) > 15:
            raise ValueError('相机离线')
        if j['status'] not in ('due', 'missed') or s['status'] != 'settling':
            raise ValueError('样品不在有效拍照状态')
        if j['generation'] != s['generation'] or b['mapping_version'] != j['mapping_version'] or b['mapping_version'] != c['mapping_version']:
            raise ValueError('映射或样品状态已变化')
        t = parse_time(b['captured_at'])
        settle = t.timestamp() - parse_time(s['mixed_at']).timestamp()
        if settle < j['offset_s'] or abs(time.time() - t.timestamp()) > 30:
            raise ValueError('采集时间不符合当前拍照任务')
        raw = validate_png(b['png_base64'], c['width'], c['height'])
        analysis=validate_analysis(b['analysis'], c['rois']) if b.get('analysis') is not None else None
        id = uid()
        (self.directory / (id + '.png')).write_bytes(raw)
        f = dict(id=id, job_id=j['id'], sample_id=s['id'], bottle=s['bottle'], generation=s['generation'], mixed_at=s['mixed_at'], captured_at=b['captured_at'], settle_s=settle, lateness_s=max(0, settle - j['offset_s']), url='/api/lab/image/' + id, rois=copy.deepcopy(c['rois']), analysis=analysis, mapping_version=c['mapping_version'])
        s['frames'].append(f)
        j['status'] = 'captured'
        return f

    def image_path(self, id):
        if not any((f['id'] == id for s in self.snapshot()['samples'] for f in s['frames'])):
            raise ValueError('图片不存在')
        return self.directory / (id + '.png')

    def export_zip(self, project_id=None):
        d = self.snapshot()
        samples = [s for s in d['samples'] if project_id is None or s['project_id'] == project_id]
        d['samples'] = samples
        out = io.BytesIO()
        table = io.StringIO(newline='')
        w = csv.writer(table)
        w.writerow(['id', 'project_id', 'bottle', 'status', 'target', 'label', 'exclusion'])
        for s in samples:
            vals = [s.get(k, '') for k in ('id', 'project_id', 'bottle', 'status', 'target', 'label', 'exclusion')]
            w.writerow(["'" + str(v) if str(v).startswith(('=', '+', '-', '@')) else str(v) for v in vals])
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('data.json', json.dumps(d, ensure_ascii=False, indent=2))
            z.writestr('samples.csv', '\ufeff' + table.getvalue())
            z.writestr('report.html', '<!doctype html><meta charset="utf-8"><h1>实验记录</h1><p>启发式判相；清洗、交叉污染、分相时间与流体精度尚待验证。</p><pre>' + html.escape(table.getvalue()) + '</pre>')
            for s in samples:
                for f in s['frames']:
                    z.write(self.image_path(f['id']), 'images/' + f['id'] + '.png')
        return out.getvalue()
