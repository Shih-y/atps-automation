"""Persistent experiment scheduling. Hardware stop never depends on storage."""
import copy
import csv
import html
import io
import json
import math
import secrets
import sqlite3
import threading
import time
import zipfile
from contextlib import closing
from pathlib import Path
from capture_store import timestamp, parse_time, validate_png, validate_analysis
import experiment_domain as domain

SCHEMA_VERSION = 2
HARD_EXCLUSIONS = {'interrupted', 'restart_interrupted', 'manual_composition', 'missing_mixing'}


def uid():
    return secrets.token_hex(16)


class ExperimentService:
    def __init__(self, controller):
        self.controller = controller
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.cancel_dispatch = threading.Event()
        self.cancel_dispatch.set()
        self.stop_epoch = 0
        self.active_stop_epoch = -1
        self.directory = Path(controller.directory) / 'experiments'
        self.directory.mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.directory / 'experiments.sqlite3', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        row = self.db.execute('SELECT body FROM state WHERE id=1').fetchone()
        self.data = json.loads(row[0]) if row else {}
        if row and self.data.get('schema_version', 1) < SCHEMA_VERSION:
            backup_dir = self.directory / 'backups'
            backup_dir.mkdir(exist_ok=True)
            with closing(sqlite3.connect(backup_dir / ('before-schema-%s-%s.sqlite3' % (SCHEMA_VERSION, uid())))) as backup:
                self.db.backup(backup)
        self._normalize()
        self.data.update(running=False, active_project_id=None)
        self.data['camera'].update(ready=False, heartbeat=0)
        for sample in self.data['samples']:
            if sample['status'] == 'preparing':
                self._set_state(sample, 'interrupted')
                self._exclude(sample, 'restart_interrupted', '程序重启，配液交付量无法确认', hard=True)
        self._save()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _normalize(self):
        defaults = dict(stocks=domain.default_stocks(), calibrations=[], projects=[], samples=[],
                        bottles=[dict(bottle=i, sample_id=None) for i in range(1, 6)],
                        photo_jobs=[], rounds=[], camera=dict(ready=False, mapping_version=0),
                        running=False, message='', stop_status='unknown', idempotency={}, bottle_history=[], backups=[])
        for key, value in defaults.items():
            self.data.setdefault(key, value)
        for sample in self.data['samples']:
            for key, value in dict(frames=[], label=None, exclusion='', exclusions=[], generation=0,
                                  observation_history=[], review_history=[], action_events=[], target={}, recipe={}).items():
                sample.setdefault(key, copy.deepcopy(value))
            if sample.get('exclusion') and not sample['exclusions']:
                reason = sample['exclusion']
                active = reason != 'observation window expired without human review'
                sample['exclusions'].append(dict(id=uid(), code='legacy', reason=reason, hard=active,
                                                active=active, at=timestamp(), generation=sample['generation']))
                if not active:
                    sample['exclusion'] = ''
            if sample.get('status') == 'released':
                sample['released_at'] = sample.get('released_at', timestamp())
                sample['status'] = 'observation_complete' if sample.get('mixed_at') else 'cancelled'
            sample['execution_state'] = sample.get('status', 'interrupted')
        for cal in self.data['calibrations']:
            cal.setdefault('status', 'trial')
        for project in self.data['projects']:
            project.setdefault('version', 1)
            project.setdefault('composition_tolerance_fraction', .01)
            for cal in project.get('calibrations', []):
                cal.setdefault('status', 'trial')
        self.data['schema_version'] = SCHEMA_VERSION

    def _save(self):
        # Online status is transient; a heartbeat must not rewrite history.
        persistent = dict(self.data)
        persistent['camera'] = dict(self.data['camera'], ready=False, heartbeat=0)
        encoded = json.dumps(persistent, ensure_ascii=False, allow_nan=False)
        if getattr(self, '_last_saved', None) == encoded:
            return
        self.db.execute('INSERT OR REPLACE INTO state VALUES (1,?)', (encoded,))
        self.db.commit()
        self._last_saved = encoded

    def _best_effort_save(self):
        try:
            self._save()
        except Exception as error:
            self.data['storage_error'] = str(error)

    def _get(self, key, record_id):
        value = next((x for x in self.data[key] if x['id'] == record_id), None)
        if value is None:
            raise ValueError('记录不存在')
        return value

    @staticmethod
    def _set_state(sample, state):
        sample['status'] = sample['execution_state'] = state

    def _exclude(self, sample, code, reason, hard=False):
        exclusions = sample.setdefault('exclusions', [])
        if not any(e['active'] and e['code'] == code and e['reason'] == reason for e in exclusions):
            exclusions.append(dict(id=uid(), code=code, reason=str(reason), hard=hard or code in HARD_EXCLUSIONS,
                                   active=True, at=timestamp(), generation=sample.get('generation', 0)))
        sample['exclusion'] = '; '.join(e['reason'] for e in exclusions if e['active'])

    def _training(self, sample, project, check_conflicts=True):
        reasons = []
        label = sample.get('label') or {}
        frame = next((f for f in sample.get('frames', []) if f['id'] == label.get('frame_id')), None)
        if label.get('classification') not in ('single', 'two_phase') or label.get('clear') is not True:
            reasons.append('尚无清晰的人工单相或双相标签')
        if not frame:
            reasons.append('缺少复核照片')
        else:
            if frame.get('generation') != sample.get('generation', 0):
                reasons.append('复核照片属于旧观察阶段')
            maximum = sample.get('observation_max_s', project.get('max_observe_s', 1800))
            if not project.get('min_settle_s', 300) <= frame.get('settle_s', -1) <= maximum:
                reasons.append('照片拍摄时间不在有效观察窗口')
        if not sample.get('mixed_at'):
            reasons.append('缺少混合结束记录')
        if sample.get('status') not in ('settling', 'observation_complete', 'released'):
            reasons.append('样品未完成配液或已中断')
        reasons += [e['reason'] for e in sample.get('exclusions', []) if e.get('active')]
        if sample.get('exclusion') and not sample.get('exclusions'):
            reasons.append(sample['exclusion'])
        composition = domain.composition_record(sample, project)
        if not composition['valid']:
            reasons.extend(composition['reasons'])
        if not reasons and check_conflicts:
            for other in self.data['samples']:
                if other['id'] == sample['id'] or other.get('project_id') != sample.get('project_id'):
                    continue
                if (other.get('label') or {}).get('classification') == label.get('classification'):
                    continue
                adopted = domain.composition_record(other, project)
                if (adopted['valid'] and abs(adopted['peg_fraction'] - composition['peg_fraction']) < 1e-10
                        and abs(adopted['salt_fraction'] - composition['salt_fraction']) < 1e-10
                        and self._training(other, project, check_conflicts=False)['eligible']):
                    reasons.append('相同采用组成的独立重复标签冲突，需要复核')
                    break
        return dict(eligible=not reasons, reasons=list(dict.fromkeys(reasons)))

    def _eligible(self, sample, project):
        return self._training(sample, project)['eligible']

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.data)
            projects = {p['id']: p for p in self.data['projects']}
            for sample in result['samples']:
                project = projects.get(sample.get('project_id'), {})
                sample['composition'] = domain.composition_record(sample, project)
                sample['training_eligibility'] = self._training(sample, project)
                sample['execution_state'] = sample['status']
                sample['review_state'] = (sample.get('label') or {}).get('classification', 'unreviewed')
                if sample.get('label'):
                    sample['label']['eligible'] = sample['training_eligibility']['eligible']
            by_id = {s['id']: s for s in result['samples']}
            for bottle in result['bottles']:
                sample = by_id.get(bottle.get('sample_id'))
                bottle['state'] = 'free' if not sample else 'assigned' if sample['status'] == 'queued' else 'needs_replacement' if sample['status'] in ('interrupted', 'observation_complete', 'cancelled') else 'occupied'
            result['start_previews'] = {p['id']: self._start_preview(p) for p in self.data['projects']}
            result['compatible_calibrations'] = {p['id']: self._compatible_calibrations(p) for p in self.data['projects']}
            return result

    def close(self):
        if self.closed.is_set():
            return
        self.cancel_dispatch.set()
        self.data['running'] = False
        self.closed.set()
        self.thread.join(2)
        with self.lock:
            self._best_effort_save()
            try:
                self.db.close()
            except sqlite3.Error:
                pass

    def _signature(self, source=None):
        return domain.plumbing_signature(self.controller.config, source)

    @staticmethod
    def _ports(project):
        return {14, 15, project['salt_port']}

    def _preview(self, project, recipe):
        request = dict(recipe, salt_port=project['salt_port'], temperature_c=project.get('temperature_c', 25),
                       total_mass_g=recipe.get('total_mass_g', project.get('total_mass_g', 1)),
                       safe_mass_limit_g=project.get('safe_mass_limit_g'),
                       composition_tolerance_fraction=project.get('composition_tolerance_fraction', .01),
                       allow_trial_calibration=project.get('allow_trial_calibration', True))
        return domain.preview_recipe(request, project['stocks'], project['calibrations'],
                                     {str(p): self._signature(p) for p in self._ports(project)})

    def _compatibility_issues(self, project):
        issues = []
        for port in self._ports(project):
            old = next((s for s in project.get('stocks', []) if s['port'] == port), None)
            current = next((s for s in self.data['stocks'] if s['port'] == port), None)
            if old != current:
                issues.append('来源%s的母液批次或参数已变化，请创建项目新版本' % port)
            frozen = [c for c in project.get('calibrations', []) if c.get('source') == port]
            now = [c for c in self.data['calibrations'] if c.get('source') == port]
            if frozen != now:
                issues.append('来源%s的标定已变化，请创建项目新版本' % port)
        return issues

    def _compatible_calibrations(self, project):
        results = []
        for cal in project.get('calibrations', []):
            if cal.get('source') not in self._ports(project):
                continue
            stock = next((s for s in project['stocks'] if s['port'] == cal.get('source')), {})
            compatible = (cal.get('stock_version') == stock.get('version')
                          and cal.get('stock_batch', '') == stock.get('batch', '')
                          and cal.get('temperature_c') == project.get('temperature_c', 25)
                          and cal.get('geometry_signature') == self._signature(cal['source'])
                          and cal.get('status', 'trial') != 'invalid')
            results.append(dict(cal, compatible=compatible))
        return results

    def _start_preview(self, project):
        samples = [s for s in self.data['samples'] if s.get('project_id') == project['id'] and s['status'] == 'queued']
        return dict(project_id=project['id'], name=project.get('name', ''), salt_port=project['salt_port'],
                    count=len(samples), bottles=sorted({s['bottle'] for s in samples if s.get('bottle')}),
                    sample_ids=[s['id'] for s in samples], issues=self._compatibility_issues(project))

    def _new_project(self, body, parent=None):
        project = dict(body, id=uid(), created_at=timestamp(), version=1 if not parent else parent.get('version', 1) + 1)
        project.pop('request_id', None)
        if parent:
            project['parent_project_id'] = parent['id']
        for key, default in dict(name='实验', salt_port=10, temperature_c=25, measured_temperature_c=None,
                                 total_mass_g=1, photo_offsets_s=[60, 180, 300, 600], min_settle_s=300,
                                 max_observe_s=1800, composition_tolerance_fraction=.01).items():
            project.setdefault(key, default)
        if project['salt_port'] not in (10, 11, 12, 13):
            raise ValueError('盐来源无效')
        domain.num(project['temperature_c'], -20, 100)
        if project['measured_temperature_c'] is not None:
            domain.num(project['measured_temperature_c'], -20, 100)
        domain.num(project['total_mass_g'], .000001, 100)
        domain.num(project['min_settle_s'], 0, 86400)
        domain.num(project['max_observe_s'], project['min_settle_s'], 86400)
        domain.num(project['composition_tolerance_fraction'], 0, 1)
        offsets = project['photo_offsets_s']
        if not isinstance(offsets, list) or not 1 <= len(offsets) <= 20:
            raise ValueError('拍照时间必须为1至20个')
        for offset in offsets:
            domain.num(offset, 0, project['max_observe_s'])
        if len(set(offsets)) != len(offsets):
            raise ValueError('拍照时间必须唯一')
        project['photo_offsets_s'] = sorted(offsets)
        ports = self._ports(project)
        project['stocks'] = copy.deepcopy([s for s in self.data['stocks'] if s['port'] in ports])
        project['calibrations'] = copy.deepcopy([c for c in self.data['calibrations'] if c.get('source') in ports])
        project['method'] = dict(independent_samples=True, intermediate_wash=False, intermediate_purge=False,
                                 mixing='air_after_all_components', final_common_purge_port=16,
                                 timing_basis='engineering_settings_pending_experimental_validation', temperature_control=False)
        self.data['projects'].append(project)
        return project

    def _enqueue(self, project, recipes, bottles=None, allow_repeat=False):
        if not isinstance(recipes, list) or not recipes or any(not isinstance(r, dict) for r in recipes):
            raise ValueError('配方必须为非空对象列表')
        if bottles is not None and (not isinstance(bottles, list) or any(type(b) is not int or b not in range(1, 6) for b in bottles)):
            raise ValueError('自动实验瓶位仅允许1至5')
        free = [b for b in self.data['bottles'] if b['sample_id'] is None and (bottles is None or b['bottle'] in bottles)]
        expanded = []
        for recipe in recipes:
            count = recipe.get('replicates', 1)
            if type(count) is not int or not 1 <= count <= 5:
                raise ValueError('独立重复数必须为1至5')
            group = uid()
            for replicate in range(count):
                expanded.append((recipe, group, replicate + 1, count))
        if not 1 <= len(expanded) <= 100:
            raise ValueError('队列必须为1至100个样品')
        prepared = []
        seen = {s.get('execution_key'): s['id'] for s in self.data['samples'] if s.get('project_id') == project['id'] and s.get('execution_key') and s['status'] != 'cancelled'}
        for index, (recipe, group, replicate, count) in enumerate(expanded):
            bottle = free[index]['bottle'] if index < len(free) else None
            preview = self._preview(project, dict(recipe, **({'sample': bottle} if bottle else {})))
            if not preview['executable']:
                raise ValueError('配方不能执行：' + '; '.join(preview.get('issues', [])))
            key = domain.execution_key(preview['plan'])
            if recipe.get('expected_execution_key') and recipe['expected_execution_key'] != key:
                raise ValueError('当前瓶位编译动作与已审核候选不同，请重新预览候选或创建项目新版本')
            expected_composition = recipe.get('expected_composition')
            if expected_composition and any(abs(preview['composition'][axis] - expected_composition[axis]) > 1e-10
                                            for axis in ('peg_fraction', 'salt_fraction')):
                raise ValueError('当前瓶位估计组成与已审核候选不同，请重新预览')
            repeat = allow_repeat or recipe.get('independent_repeat') is True or count > 1
            if key in seen and not repeat:
                raise ValueError('目标量化后与已有样品是相同可执行点；请明确选择独立重复')
            prepared.append((preview, bottle, key, group, replicate, count, seen.get(key)))
            seen.setdefault(key, 'pending')
        result = []
        for preview, bottle, key, group, replicate, count, duplicate in prepared:
            sample = dict(id=uid(), created_at=timestamp(), project_id=project['id'], bottle=bottle,
                          status='queued', execution_state='queued', target=preview['target'], recipe=preview,
                          mixed_at=None, frames=[], label=None, exclusion='', exclusions=[], generation=0,
                          observation_history=[], execution_key=key, replicate_group=group,
                          replicate_index=replicate, replicate_count=count, same_executable_point_as=duplicate)
            self.data['samples'].append(sample)
            if bottle:
                next(b for b in self.data['bottles'] if b['bottle'] == bottle)['sample_id'] = sample['id']
            result.append(sample)
        return result

    def dispatch_allowed(self, sample_id):
        # Controller calls this under its lock. Never invert the lock order.
        if self.closed.is_set() or self.cancel_dispatch.is_set() or not self.data.get('running') or self.active_stop_epoch != self.stop_epoch:
            return False
        return any(s['id'] == sample_id and s['status'] == 'preparing'
                   and s.get('project_id') == self.data.get('active_project_id') for s in self.data['samples'])

    def _stop(self):
        self.stop_epoch += 1
        self.cancel_dispatch.set()
        self.data['running'] = False
        self.data['stop_status'] = 'requested'
        # No database, log, controller lock, or service lock may precede stop.
        try:
            self.controller.stop_keep_connection()
        except Exception:
            self.data['stop_status'] = 'unconfirmed'
            raise
        else:
            self.data['stop_status'] = getattr(self.controller, 'stop_status', 'unconfirmed')
        finally:
            with self.lock:
                self.cancel_dispatch.set()
                self.data['running'] = False
                for sample in self.data['samples']:
                    if sample['status'] == 'preparing':
                        self._set_state(sample, 'interrupted')
                        self._exclude(sample, 'interrupted', '软件停止后交付量未确定，禁止自动重做', hard=True)
                self._best_effort_save()
        return dict(ok=True, stop_status=self.data['stop_status'], storage_error=self.data.get('storage_error'))

    def handle(self, op, body):
        start_epoch = self.stop_epoch
        if op == 'stop':
            return self._stop()
        if op == 'backup':
            content = self.export_zip(body.get('project_id'))
            backup_id = uid()
            directory = self.directory / 'backups'
            directory.mkdir(exist_ok=True)
            (directory / (backup_id + '.zip')).write_bytes(content)
            record = dict(id=backup_id, created_at=timestamp(), project_id=body.get('project_id'), download_url='/api/lab/backup/' + backup_id)
            with self.lock:
                self.data['backups'].append(record)
                self._save()
            return dict(ok=True, backup=record)
        if op == 'start':
            if not body.get('project_id'):
                raise ValueError('开始当前项目必须指定project_id')
            state = self.controller.snapshot()
            if not state.get('connected') or state.get('busy'):
                raise ValueError('设备未连接或忙碌')
        with self.lock:
            request_id = body.get('request_id')
            key = '%s:%s' % (op, request_id) if request_id else None
            fingerprint = json.dumps(body, sort_keys=True, ensure_ascii=False)
            if key and key in self.data['idempotency']:
                saved = self.data['idempotency'][key]
                if saved['fingerprint'] != fingerprint:
                    raise ValueError('同一个请求编号不能用于不同操作内容')
                # A previous attempt may have mutated memory but failed to
                # commit. Reuse the operation, never its unpersisted success.
                self._save()
                return copy.deepcopy(saved['result'])
            result, persist = self._handle(op, body, start_epoch=start_epoch)
            result = dict(result, ok=True)
            if key:
                self.data['idempotency'][key] = dict(fingerprint=fingerprint, result=copy.deepcopy(result))
                persist = True
            if persist:
                try:
                    self._save()
                except Exception:
                    # An API failure must never leave a new automatic run armed.
                    self.cancel_dispatch.set()
                    self.data['running'] = False
                    raise
            return copy.deepcopy(result)

    def _handle(self, op, body, start_epoch=None):
        result = {}
        if op == 'stock':
            stock = domain.validate_stock(body)
            old = next((s for s in self.data['stocks'] if s['port'] == stock['port']), {})
            stock['version'] = old.get('version', 0) + 1
            self.data['stocks'] = [s for s in self.data['stocks'] if s['port'] != stock['port']] + [stock]
        elif op == 'calibration':
            stock = next((s for s in self.data['stocks'] if s['port'] == body['source']), None)
            if stock is None:
                raise ValueError('来源不存在')
            cal = domain.make_calibration(body, stock, self._signature(body['source']))
            self.data['calibrations'].append(cal)
            result['calibration'] = cal
        elif op in ('project', 'project_version'):
            parent = self._get('projects', body['project_id']) if op == 'project_version' else None
            values = dict(parent or {}, **body)
            values.pop('project_id', None)
            result['project'] = self._new_project(values, parent)
        elif op == 'preview':
            project = self._get('projects', body['project_id']) if body.get('project_id') else dict(body, stocks=self.data['stocks'], calibrations=self.data['calibrations'])
            result['preview'] = self._preview(project, body)
            return result, False
        elif op == 'enqueue':
            result['samples'] = self._enqueue(self._get('projects', body['project_id']), body['recipes'], body.get('bottles'))
        elif op == 'start':
            if start_epoch != self.stop_epoch or self.data['stop_status'] == 'requested':
                raise ValueError('停止请求已取消本次启动，请确认设备状态后重新开始')
            project = self._get('projects', body['project_id'])
            preview = self._start_preview(project)
            if self.data['running']:
                raise ValueError('已有批次正在运行，请先暂停后续任务')
            if preview['issues']:
                raise ValueError('; '.join(preview['issues']))
            if not preview['count']:
                raise ValueError('当前项目没有排队样品')
            if not self._camera_online():
                raise ValueError('相机未在线')
            for sample in self.data['samples']:
                if sample.get('project_id') == project['id'] and sample['status'] == 'queued':
                    recipe = dict(sample['target'], **({'sample': sample['bottle']} if sample.get('bottle') else {}))
                    if not self._preview(project, recipe)['executable']:
                        raise ValueError('配方条件已失效')
            self.data.update(running=True, active_project_id=project['id'], active_batch=copy.deepcopy(preview), message='', stop_status='unknown')
            self.active_stop_epoch = start_epoch
            self.cancel_dispatch.clear()
            result['start_preview'] = preview
        elif op == 'pause':
            self.cancel_dispatch.set()
            self.data['running'] = False
        elif op == 'cancel':
            sample = self._get('samples', body['sample_id'])
            if sample['status'] != 'queued':
                raise ValueError('只能取消尚未执行的排队样品')
            self._set_state(sample, 'cancelled')
            sample['cancelled_at'] = timestamp()
            for bottle in self.data['bottles']:
                if bottle['sample_id'] == sample['id']:
                    bottle['sample_id'] = None
        elif op == 'remake':
            original = self._get('samples', body['sample_id'])
            if not str(body.get('reason', '')).strip():
                raise ValueError('请记录重新配制的原因')
            result['samples'] = self._enqueue(self._get('projects', original['project_id']), [original['target']], allow_repeat=True)
            result['samples'][0].update(remake_of=original['id'], remake_reason=body['reason'])
        elif op == 'release':
            self._release(body)
        elif op == 'camera':
            return self._camera(body)
        elif op == 'retry_photo':
            result['job'] = self._retry_photo(body)
        elif op == 'photo_failure':
            job = self._get('photo_jobs', body['job_id'])
            if job['status'] != 'captured':
                job.update(status='failed', failed_at=timestamp(), failure_reason=str(body.get('reason', '拍照或保存失败')))
        elif op == 'capture':
            result['frame'] = self._capture(body)
        elif op == 'extend_observation':
            self._extend_observation(body)
        elif op == 'review':
            sample = self._get('samples', body['sample_id'])
            if body['classification'] not in ('single', 'two_phase', 'uncertain'):
                raise ValueError('分类无效')
            if not any(f['id'] == body.get('frame_id') for f in sample['frames']):
                raise ValueError('请选择该样品的照片')
            if sample.get('label'):
                sample.setdefault('review_history', []).append(copy.deepcopy(sample['label']))
            sample['label'] = dict(body, version=len(sample.get('review_history', [])) + 1,
                                   reviewed_at=timestamp(), generation=sample['generation'])
            sample['label']['eligible'] = self._eligible(sample, self._get('projects', sample['project_id']))
        elif op == 'exclude':
            sample = self._get('samples', body['sample_id'])
            reason = str(body.get('reason', '')).strip()
            if not reason:
                raise ValueError('排除原因不能为空')
            self._exclude(sample, body.get('code', 'manual_review'), reason)
        elif op == 'correct_exclusion':
            sample = self._get('samples', body['sample_id'])
            exclusion = next((e for e in sample.get('exclusions', []) if e['id'] == body['exclusion_id']), None)
            if exclusion is None or not str(body.get('reason', '')).strip():
                raise ValueError('请选择排除记录并填写更正依据')
            if exclusion.get('hard'):
                raise ValueError('交付中断或组成扰动不能简单清除，请重新配制或独立核实后另建记录')
            exclusion.update(active=False, corrected_at=timestamp(), correction_reason=body['reason'])
            sample['exclusion'] = '; '.join(e['reason'] for e in sample['exclusions'] if e['active'])
        elif op == 'measured_composition':
            sample = self._get('samples', body['sample_id'])
            value = dict(body, recorded_at=timestamp())
            probe = dict(sample, measured_composition=value)
            record = domain.composition_record(probe, self._get('projects', sample['project_id']))
            if record['basis'] != 'measured':
                raise ValueError('需要独立组分分析方法及依据，整瓶称量不能作为组分实测')
            if sample.get('measured_composition'):
                sample.setdefault('composition_history', []).append(copy.deepcopy(sample['measured_composition']))
            sample['measured_composition'] = value
        elif op == 'suggest':
            result['round'] = self._suggest(body)
        elif op == 'approve':
            result['samples'] = self._approve(body)
        else:
            raise ValueError('未知实验操作')
        return result, True

    def _release(self, body):
        if body.get('confirmed_empty') is not True:
            raise ValueError('请确认已更换干净空瓶；管路排液不等于清洗样品瓶')
        bottle = next((b for b in self.data['bottles'] if b['bottle'] == body['bottle']), None)
        if bottle is None:
            raise ValueError('自动瓶位仅为1至5')
        old_id = bottle.get('sample_id')
        if old_id:
            sample = self._get('samples', old_id)
            if sample['status'] == 'preparing':
                raise ValueError('运行瓶不可释放')
            if sample['status'] == 'queued':
                self._set_state(sample, 'cancelled')
            elif sample['status'] == 'settling':
                self._set_state(sample, 'observation_complete')
            sample['released_at'] = timestamp()
            self._cancel_photos(sample)
        self.data['bottle_history'].append(dict(bottle=bottle['bottle'], old_sample_id=old_id, at=timestamp(), confirmed_clean_empty=True))
        bottle['sample_id'] = None

    def _camera_online(self):
        camera = self.data['camera']
        return camera.get('ready') and time.time() - camera.get('heartbeat', 0) <= 15

    def _camera(self, body):
        camera = self.data['camera']
        changed = False
        if 'rois' in body:
            rois = body['rois']
            if body.get('ready') and (len(rois) != 5 or {r.get('sample') for r in rois} != {1, 2, 3, 4, 5}):
                raise ValueError('需要五瓶ROI')
            checked = []
            for roi in rois:
                values = [roi.get(k) for k in ('x', 'y', 'width', 'height')]
                if any(type(v) is not int for v in values):
                    raise ValueError('ROI必须为整数')
                x, y, width, height = values
                if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > body.get('width', 0) or y + height > body.get('height', 0):
                    raise ValueError('ROI越界')
                if any(x < o['x'] + o['width'] and o['x'] < x + width and y < o['y'] + o['height'] and o['y'] < y + height for o in checked):
                    raise ValueError('ROI重叠')
                checked.append(roi)
            changed = any(camera.get(k) != body.get(k) for k in ('rois', 'width', 'height'))
            if changed:
                camera['mapping_version'] += 1
                camera.update({k: copy.deepcopy(body.get(k)) for k in ('rois', 'width', 'height')})
        if body.get('ready') and len(camera.get('rois', [])) != 5:
            raise ValueError('需要五瓶ROI')
        camera.update(ready=bool(body.get('ready')), heartbeat=time.time())
        return dict(mapping_version=camera['mapping_version']), changed

    def _cancel_photos(self, sample):
        for job in self.data['photo_jobs']:
            if job['sample_id'] == sample['id'] and job['status'] not in ('captured', 'cancelled', 'superseded'):
                job.update(status='cancelled', cancellation_reason='瓶位释放或样品观察阶段改变')

    def _schedule(self, sample, offsets):
        for offset in offsets:
            if any(j['sample_id'] == sample['id'] and j['generation'] == sample['generation'] and j['offset_s'] == offset and j['status'] != 'cancelled' for j in self.data['photo_jobs']):
                continue
            self.data['photo_jobs'].append(dict(id=uid(), sample_id=sample['id'], bottle=sample['bottle'],
                                               generation=sample['generation'], offset_s=offset,
                                               due_at=parse_time(sample['mixed_at']).timestamp() + offset,
                                               status='waiting', mapping_version=self.data['camera']['mapping_version']))

    def _retry_photo(self, body):
        if body.get('job_id'):
            job = self._get('photo_jobs', body['job_id'])
            sample = self._get('samples', job['sample_id'])
            if job['generation'] != sample['generation']:
                raise ValueError('旧观察阶段不能补拍')
            existing = next((j for j in self.data['photo_jobs'] if j.get('replaces') == job['id']), None)
            if existing:
                return existing
            if job['status'] == 'captured':
                return job
            replacement = dict(job, id=uid(), status='due', mapping_version=self.data['camera']['mapping_version'], replaces=job['id'])
        else:
            sample = self._get('samples', body['sample_id'])
            if not sample.get('mixed_at') or sample.get('released_at'):
                raise ValueError('请选择仍在瓶位上的已混合样品')
            elapsed = max(0, time.time() - parse_time(sample['mixed_at']).timestamp())
            replacement = dict(id=uid(), sample_id=sample['id'], bottle=sample['bottle'], generation=sample['generation'],
                               status='due', offset_s=elapsed, due_at=time.time(), mapping_version=self.data['camera']['mapping_version'], extra=True)
        if sample.get('released_at') or sample['status'] not in ('settling', 'observation_complete'):
            raise ValueError('样品已释放或不在观察阶段')
        if body.get('job_id'):
            job['status'] = 'superseded'
        self.data['photo_jobs'].append(replacement)
        return replacement

    def _extend_observation(self, body):
        sample = self._get('samples', body['sample_id'])
        project = self._get('projects', sample['project_id'])
        reason = str(body.get('reason', '')).strip()
        if not reason or not sample.get('mixed_at') or sample.get('released_at'):
            raise ValueError('请提供延长依据，并保留原样品在瓶位上')
        old_max = sample.get('observation_max_s', project['max_observe_s'])
        new_max = domain.num(body['max_observe_s'], old_max, 86400)
        offsets = body.get('offsets_s', [])
        if not isinstance(offsets, list) or len(offsets) > 20:
            raise ValueError('补充拍照计划无效')
        for value in offsets:
            domain.num(value, 0, new_max)
        sample.setdefault('observation_history', []).append(dict(at=timestamp(), generation=sample['generation'],
                                                                 old_max_s=old_max, max_observe_s=new_max,
                                                                 offsets_s=offsets, reason=reason))
        sample['observation_max_s'] = new_max
        sample['observation_expired'] = False
        self._set_state(sample, 'settling')
        self._schedule(sample, offsets)

    def _suggest(self, body):
        project = self._get('projects', body['project_id'])
        inputs, records, groups = [], [], {}
        for sample in self.data['samples']:
            if sample.get('project_id') != project['id'] or sample['status'] == 'cancelled':
                continue
            composition = domain.composition_record(sample, project)
            eligibility = self._training(sample, project, check_conflicts=False)
            label = (sample.get('label') or {}).get('classification') if eligibility['eligible'] else None
            if composition['peg_fraction'] is not None:
                inputs.append(dict(id=sample['id'], peg_fraction=composition['peg_fraction'], salt_fraction=composition['salt_fraction'], label=label))
            if label:
                key = (round(composition['peg_fraction'], 10), round(composition['salt_fraction'], 10))
                groups.setdefault(key, []).append((sample['id'], label))
                records.append(dict(id=sample['id'], target=copy.deepcopy(sample['target']),
                                    composition=composition, estimated=copy.deepcopy(sample.get('recipe', {}).get('estimated')),
                                    label=copy.deepcopy(sample['label']), execution_key=sample.get('execution_key')))
        conflicts = [dict(peg_fraction=k[0], salt_fraction=k[1], sample_ids=[x[0] for x in values]) for k, values in groups.items() if len({x[1] for x in values}) > 1]
        conflict_ids = {i for conflict in conflicts for i in conflict['sample_ids']}
        for value in inputs:
            if value['id'] in conflict_ids:
                value['label'] = None
        parameters = dict(body, salt_port=project['salt_port'],
                          peg_stock_fraction=next(s['mass_fraction'] for s in project['stocks'] if s['port'] == 14),
                          salt_stock_fraction=next(s['mass_fraction'] for s in project['stocks'] if s['port'] == project['salt_port']))
        model = domain.suggest_points(inputs, parameters)
        self._compile_suggestions(project, model, inputs, parameters)
        model.update(id=uid(), project_id=project['id'], approved=[], approvals=[], created_at=timestamp(),
                     training_records=[r for r in records if r['id'] in model['training_ids']],
                     conflicts=conflicts, parameters=copy.deepcopy(parameters), coordinate_policy='measured_then_calibrated_estimate',
                     score_interpretation='heuristic_classification_and_selection_scores_not_calibrated_probabilities')
        model['summary']['conflict_count'] = len(conflicts)
        self.data['rounds'].append(model)
        return model

    def _compile_suggestions(self, project, model, inputs, parameters):
        """Recommend executable, quantized points, not duplicate ideal targets.

        The model grid remains a continuous composition prediction. Selection
        is rescored at the adopted compiled coordinates. A proposed command
        identity is rechecked on approval against the actual assigned bottle.
        """
        bottle = next((b['bottle'] for b in self.data['bottles'] if b['sample_id'] is None), 1)
        existing_keys = set()
        for sample in self.data['samples']:
            if sample.get('project_id') != project['id'] or sample['status'] == 'cancelled':
                continue
            plan = sample.get('recipe', {}).get('plan')
            key = domain.execution_key(plan) if plan else sample.get('execution_key')
            if key:
                existing_keys.add(key)
        known_coordinates = {(round(s['peg_fraction'], 9), round(s['salt_fraction'], 9)) for s in inputs}
        rejected = dict(unexecutable=0, existing_execution=0, duplicate_execution=0,
                        existing_composition=0, duplicate_composition=0)
        pool, command_keys, coordinates = [], set(), set()
        train = [s for s in inputs if s.get('label') in ('single', 'two_phase')]
        two_classes = len({s['label'] for s in train}) == 2
        for point in model['grid']:
            try:
                preview = self._preview(project, dict(point, sample=bottle))
            except ValueError:
                rejected['unexecutable'] += 1
                continue
            composition = preview.get('composition') or domain.composition_record(
                dict(recipe=preview, target=preview['target'], bottle=bottle), project)
            if not preview['executable'] or composition['peg_fraction'] is None or composition['salt_fraction'] is None:
                rejected['unexecutable'] += 1
                continue
            key = domain.execution_key(preview['plan'])
            adopted = (round(composition['peg_fraction'], 9), round(composition['salt_fraction'], 9))
            if key in existing_keys:
                rejected['existing_execution'] += 1
                continue
            if key in command_keys:
                rejected['duplicate_execution'] += 1
                continue
            if adopted in known_coordinates:
                rejected['existing_composition'] += 1
                continue
            if adopted in coordinates:
                rejected['duplicate_composition'] += 1
                continue
            command_keys.add(key)
            coordinates.add(adopted)
            weights = [math.exp(-((adopted[0] - s['peg_fraction']) ** 2 + (adopted[1] - s['salt_fraction']) ** 2) / .08 ** 2) for s in train]
            denominator = sum(weights)
            score = sum(w for w, s in zip(weights, train) if s['label'] == 'two_phase') / denominator if denominator else .5
            classification = score if two_classes else None
            pool.append(dict(point, target=copy.deepcopy(preview['target']), composition=copy.deepcopy(composition),
                             quantized_target=dict(peg_fraction=composition['peg_fraction'], salt_fraction=composition['salt_fraction'],
                                                   units='mass_fraction_w_w'),
                             expected_composition=dict(peg_fraction=composition['peg_fraction'], salt_fraction=composition['salt_fraction']),
                             execution_key=key, expected_execution_key=key, planned_bottle=bottle,
                             classification_score=classification, probability=classification,
                             uncertainty=1-abs(2*score-1) if two_classes else 1,
                             source_calibration_ids=[row['reference']['id'] for row in preview['plan']['items']]))
        anchors = [(s['peg_fraction'], s['salt_fraction']) for s in inputs]
        active = parameters.get('method', 'active') == 'active' and two_classes
        def selection_score(point):
            adopted = point['composition']
            distance = min(((adopted['peg_fraction']-a)**2 + (adopted['salt_fraction']-b)**2 for a, b in anchors), default=1)
            return distance * (.25 + point['uncertainty'] if active else 1)
        suggestions = []
        executable_count = len(pool)
        while pool and len(suggestions) < parameters.get('count', 5):
            candidate = max(pool, key=selection_score)
            pool.remove(candidate)
            candidate.update(selection_score=selection_score(candidate), reason='uncertainty_and_coverage' if active else 'uniform_coverage')
            candidate['compiled_plan'] = self._preview(project, dict(candidate['target'], sample=bottle))['plan']
            suggestions.append(candidate)
            anchors.append((candidate['composition']['peg_fraction'], candidate['composition']['salt_fraction']))
        model['suggestions'] = suggestions
        model['candidate_filter'] = rejected
        model['summary'].update(executable_candidate_count=executable_count, suggested_count=len(suggestions),
                                deduplication_basis='compiled_execution_key_and_adopted_composition',
                                candidate_reference_bottle=bottle)

    def _approve(self, body):
        round_record = self._get('rounds', body['round_id'])
        indices = body['indices']
        if not isinstance(indices, list) or not indices or len(set(indices)) != len(indices) or any(type(i) is not int or not 0 <= i < len(round_record['suggestions']) for i in indices):
            raise ValueError('候选选择重复或无效')
        old = next((a for a in round_record.get('approvals', []) if a['indices'] == indices), None)
        if old:
            return [self._get('samples', i) for i in old['sample_ids']]
        if any(i in round_record['approved'] for i in indices):
            raise ValueError('候选已批准，不能重复执行')
        selected = [round_record['suggestions'][i] for i in indices]
        samples = self._enqueue(self._get('projects', round_record['project_id']), selected)
        for sample, index in zip(samples, indices):
            sample['selection'] = dict(round_id=round_record['id'], method=round_record['summary']['method'], candidate_index=index)
            round_record.setdefault('sample_ids', []).append(sample['id'])
        round_record['approved'] += indices
        round_record.setdefault('approvals', []).append(dict(at=timestamp(), indices=indices, sample_ids=[s['id'] for s in samples], request_id=body.get('request_id')))
        return samples

    def before_manual(self, payload):
        # Controller calls only after command validation, at the first motion.
        action = payload.get('action')
        if action in ('prepare_capture', 'capture', 'clear', 'confirm_reset', 'new_samples', 'read_status'):
            return
        self.cancel_dispatch.set()
        self.data['running'] = False
        self.data['stop_status'] = 'unknown'
        with self.lock:
            if action == 'select_port':
                self._save()
                return
            target = payload.get('sample') or payload.get('target') or payload.get('debug_parameters', {}).get('sample')
            if action == 'manual_motion':
                target = payload.get('port')
            if action in ('wash', 'purge', 'prime', 'aspirate', 'reset'):
                self._save()
                return
            operation = payload.get('operation')
            if ((action == 'tubes' and operation in ('refill', 'purge', 'drain_sources'))
                    or (action == 'bench' and operation != 'dose_water') or action == 'discard_loaded'):
                self._save()
                return
            targets = payload.get('samples') if action == 'tubes' and operation in ('drain_samples', 'finish') else None
            for sample in self.data['samples']:
                if sample.get('released_at') or sample['status'] in ('released', 'cancelled'):
                    continue
                if target is not None and target != sample.get('bottle'):
                    continue
                if targets is not None and sample.get('bottle') not in targets:
                    continue
                sample['generation'] = sample.get('generation', 0) + 1
                sample['observation_expired'] = False
                sample.pop('observation_max_s', None)
                self._cancel_photos(sample)
                if action == 'bubble':
                    self._exclude(sample, 'remixing', '手动重新混合，等待新的观察阶段')
                else:
                    self._set_state(sample, 'interrupted')
                    self._exclude(sample, 'manual_composition', '手动加料或抽液，累计组成未经核实', hard=True)
            self._save()

    def _mix_completed(self, sample, mixed_at):
        self._set_state(sample, 'settling')
        sample['mixed_at'] = mixed_at
        sample['observation_expired'] = False
        sample.pop('observation_max_s', None)
        for exclusion in sample.get('exclusions', []):
            if exclusion['code'] == 'remixing':
                exclusion.update(active=False, resolved_at=mixed_at)
        sample['exclusion'] = '; '.join(e['reason'] for e in sample.get('exclusions', []) if e['active'])
        project = self._get('projects', sample['project_id'])
        sample.setdefault('observation_history', []).append(dict(at=timestamp(), generation=sample['generation'],
                                                                 mixed_at=mixed_at, max_observe_s=project['max_observe_s'],
                                                                 offsets_s=copy.deepcopy(project['photo_offsets_s']), reason='混合结束'))
        self._schedule(sample, project['photo_offsets_s'])

    def on_action_event(self, event, payload, plan=None, error=None):
        with self.lock:
            if not payload.get('experiment_sample_id'):
                if event == 'completed' and payload.get('action') == 'bubble':
                    target = payload.get('sample') or payload.get('debug_parameters', {}).get('sample', 1)
                    for sample in self.data['samples']:
                        if sample.get('bottle') == target and not sample.get('released_at') and sample['status'] not in ('released', 'queued', 'cancelled'):
                            self._mix_completed(sample, timestamp())
                    self._save()
                return
            sample = self._get('samples', payload['experiment_sample_id'])
            sample['progress'] = copy.deepcopy(plan)
            sample.setdefault('action_events', []).append(dict(at=timestamp(), event=event, error=str(error) if error else None,
                                                               current=copy.deepcopy((plan or {}).get('current')),
                                                               completed_commands=(plan or {}).get('completed_commands', 0),
                                                               amount_basis='command_progress_not_measured_delivery'))
            if event == 'failed':
                self._set_state(sample, 'interrupted')
                self._exclude(sample, 'interrupted', str(error or '设备动作中断'), hard=True)
                self.data['running'] = False
                self.cancel_dispatch.set()
            elif event == 'completed':
                if sample['status'] == 'interrupted':
                    self._exclude(sample, 'interrupted', '停止后收到结束回调，仍需核实交付量', hard=True)
                elif not (plan or {}).get('mixing_finished_at'):
                    self._set_state(sample, 'interrupted')
                    self._exclude(sample, 'missing_mixing', '缺少混合完成依据', hard=True)
                else:
                    self._mix_completed(sample, plan['mixing_finished_at'])
            self._save()

    def _loop(self):
        while not self.closed.wait(.25):
            try:
                self.tick()
            except Exception as error:
                self.data['running'] = False
                self.cancel_dispatch.set()
                self.data['message'] = str(error)
                with self.lock:
                    self._best_effort_save()

    def tick(self):
        # No controller lock is held during any SQLite operation.
        state = self.controller.snapshot()
        payload = None
        with self.lock:
            now = time.time()
            for sample in self.data['samples']:
                if sample['status'] == 'settling' and sample.get('mixed_at'):
                    project = self._get('projects', sample['project_id'])
                    maximum = sample.get('observation_max_s', project['max_observe_s'])
                    if now - parse_time(sample['mixed_at']).timestamp() > maximum:
                        sample['observation_expired'] = True
                        self._set_state(sample, 'observation_complete')
                        valid = any(f.get('generation') == sample['generation'] and project['min_settle_s'] <= f.get('settle_s', -1) <= maximum for f in sample['frames'])
                        sample['observation_result'] = 'awaiting_review' if valid else 'uncertain_missing_valid_photo'
            for job in self.data['photo_jobs']:
                if job['status'] == 'waiting' and job['due_at'] <= now:
                    online = self._camera_online()
                    job.update(status='due' if online else 'missed', lateness_s=max(0, now - job['due_at']))
                    if not online:
                        job['failure_reason'] = '相机、浏览器或电脑未在线，未采集到计划照片'
                elif job['status'] == 'due' and not self._camera_online():
                    job.update(status='missed', failure_reason='到期任务未保存且相机心跳中断', lateness_s=max(0, now - job.get('due_at', now)))
            if self.data['running'] and not self.cancel_dispatch.is_set() and self.active_stop_epoch == self.stop_epoch and state.get('connected') and not state.get('busy'):
                project_id = self.data.get('active_project_id')
                batch_ids = set(self.data.get('active_batch', {}).get('sample_ids', []))
                queued = [s for s in self.data['samples'] if s['status'] == 'queued'
                          and s.get('project_id') == project_id and s['id'] in batch_ids]
                for sample in queued:
                    if sample.get('bottle') is None:
                        free = next((b for b in self.data['bottles'] if b['sample_id'] is None), None)
                        if free:
                            sample['bottle'] = free['bottle']
                            free['sample_id'] = sample['id']
                sample = next((s for s in queued if s.get('bottle')), None)
                if sample:
                    project = self._get('projects', project_id)
                    issues = self._compatibility_issues(project)
                    if not self._camera_online():
                        issues.append('相机未在线')
                    preview = self._preview(project, dict(sample['target'], sample=sample['bottle']))
                    if not preview['executable']:
                        issues.extend(preview.get('issues', ['配方失效']))
                    if issues:
                        self.data.update(running=False, message='; '.join(issues))
                        self.cancel_dispatch.set()
                    else:
                        sample['recipe'] = preview
                        sample['execution_key'] = domain.execution_key(preview['plan'])
                        plan = copy.deepcopy(preview['plan'])
                        plan['sample'] = sample['bottle']
                        for row in plan.get('items', []):
                            row['sample'] = sample['bottle']
                            for parameter in row.get('parameters', []):
                                parameter['sample'] = sample['bottle']
                        self._set_state(sample, 'preparing')
                        payload = dict(action='experiment_recipe', experiment_sample_id=sample['id'], plan=plan)
                elif not queued:
                    self.data['running'] = False
                    self.cancel_dispatch.set()
            self._save()
        if payload and self.dispatch_allowed(payload['experiment_sample_id']):
            try:
                self.controller.start(payload)
            except Exception as error:
                self.on_action_event('failed', payload, error=error)
        elif payload:
            with self.lock:
                sample = self._get('samples', payload['experiment_sample_id'])
                if sample['status'] == 'preparing':
                    self._set_state(sample, 'queued')
                    self._best_effort_save()

    def _tick_locked(self):
        return self.tick()

    def _capture(self, body):
        job = self._get('photo_jobs', body['job_id'])
        sample = self._get('samples', job['sample_id'])
        camera = self.data['camera']
        if job['status'] == 'captured':
            return next(f for f in sample['frames'] if f['job_id'] == job['id'])
        if not self._camera_online():
            raise ValueError('相机离线')
        if job['status'] not in ('due', 'missed', 'failed') or sample['status'] not in ('settling', 'observation_complete') or sample.get('released_at'):
            raise ValueError('样品不在有效拍照状态')
        if job['generation'] != sample['generation'] or body['mapping_version'] != job['mapping_version'] or body['mapping_version'] != camera['mapping_version']:
            raise ValueError('映射或样品观察阶段已变化，请选择补拍')
        captured = parse_time(body['captured_at'])
        settle = captured.timestamp() - parse_time(sample['mixed_at']).timestamp()
        if settle < job['offset_s'] or abs(time.time() - captured.timestamp()) > 30:
            raise ValueError('采集时间不符合当前拍照任务')
        raw = validate_png(body['png_base64'], camera['width'], camera['height'])
        analysis = validate_analysis(body['analysis'], camera['rois']) if body.get('analysis') is not None else None
        image_id = uid()
        (self.directory / (image_id + '.png')).write_bytes(raw)
        project = self._get('projects', sample['project_id'])
        frame = dict(id=image_id, job_id=job['id'], sample_id=sample['id'], bottle=sample['bottle'],
                     generation=sample['generation'], mixed_at=sample['mixed_at'], captured_at=body['captured_at'],
                     scheduled_at=job.get('due_at'), settle_s=settle, lateness_s=max(0, settle - job['offset_s']),
                     valid_observation_time=project['min_settle_s'] <= settle <= sample.get('observation_max_s', project['max_observe_s']),
                     url='/api/lab/image/' + image_id, rois=copy.deepcopy(camera['rois']), analysis=analysis,
                     mapping_version=camera['mapping_version'])
        sample['frames'].append(frame)
        job.update(status='captured', captured_at=body['captured_at'], lateness_s=frame['lateness_s'], frame_id=image_id)
        return frame

    def image_path(self, image_id):
        with self.lock:
            if not any(f['id'] == image_id for s in self.data['samples'] for f in s.get('frames', [])):
                raise ValueError('图片不存在')
        return self.directory / (image_id + '.png')

    def backup_path(self, backup_id):
        if len(backup_id) != 32 or any(c not in '0123456789abcdef' for c in backup_id):
            raise ValueError('备份不存在')
        with self.lock:
            if not any(b['id'] == backup_id for b in self.data['backups']):
                raise ValueError('备份不存在')
        path = self.directory / 'backups' / (backup_id + '.zip')
        if not path.is_file():
            raise ValueError('备份文件不存在')
        return path

    def export_zip(self, project_id=None):
        data = self.snapshot()
        if project_id is not None and not any(p['id'] == project_id for p in data['projects']):
            raise ValueError('导出项目不存在')
        projects = [p for p in data['projects'] if project_id is None or p['id'] == project_id]
        for project in projects:
            # Older projects froze the entire global list; export only the
            # historical sources this project's method actually references.
            project['stocks'] = [s for s in project.get('stocks', []) if s['port'] in self._ports(project)]
            project['calibrations'] = [c for c in project.get('calibrations', []) if c.get('source') in self._ports(project)]
        project_ids = {p['id'] for p in projects}
        samples = [s for s in data['samples'] if s.get('project_id') in project_ids]
        sample_ids = {s['id'] for s in samples}
        data['projects'], data['samples'] = projects, samples
        data['rounds'] = [r for r in data['rounds'] if r.get('project_id') in project_ids]
        data['photo_jobs'] = [j for j in data['photo_jobs'] if j.get('sample_id') in sample_ids]
        data['bottles'] = [b for b in data['bottles'] if b.get('sample_id') in sample_ids]
        data['bottle_history'] = [b for b in data.get('bottle_history', []) if b.get('old_sample_id') in sample_ids]
        data['backups'] = [b for b in data.get('backups', []) if project_id is None or b.get('project_id') == project_id]
        data['start_previews'] = {k: v for k, v in data['start_previews'].items() if k in project_ids}
        data['compatible_calibrations'] = {k: v for k, v in data['compatible_calibrations'].items() if k in project_ids}
        data.pop('idempotency', None)
        if data.get('active_project_id') not in project_ids:
            data['active_project_id'] = None
            data.pop('active_batch', None)
        stocks = {(s['port'], s.get('version', 1)): s for s in data['stocks']} if project_id is None else {}
        calibrations = {c['id']: c for c in data['calibrations']} if project_id is None else {}
        for project in projects:
            for stock in project.get('stocks', []):
                stocks[(stock['port'], stock.get('version', 1))] = stock
            for cal in project.get('calibrations', []):
                calibrations[cal['id']] = cal
        data['stocks'] = list(stocks.values())
        data['calibrations'] = list(calibrations.values())
        try:
            from software_version import version_info
            version = version_info()
        except ImportError:
            version = dict(version='unknown', commit='unknown')
        metadata = dict(version, exported_at=timestamp(), schema_version=SCHEMA_VERSION,
                        scope='all_projects' if project_id is None else 'current_project', project_ids=sorted(project_ids),
                        units=dict(composition='mass_fraction_w_w', mass='g', volume='mL', time='s'),
                        coordinate_policy='independent_component_measurement_then_calibrated_integer_step_estimate',
                        note='命令完成数不是实测质量；相图分数是启发式分数；等待时间需实验验证')
        table = io.StringIO(newline='')
        writer = csv.writer(table)
        writer.writerow(['sample_id', 'project_id', 'bottle', 'execution_state', 'review_state', 'composition_basis',
                         'target_peg_fraction', 'target_salt_fraction', 'adopted_peg_fraction', 'adopted_salt_fraction',
                         'deviation_peg_fraction', 'deviation_salt_fraction', 'composition_units', 'training_eligible',
                         'training_exclusion_reasons', 'label_version', 'frame_id', 'mixed_at', 'execution_key',
                         'replicate_group', 'replicate_index', 'software_version', 'source_commit'])
        for sample in samples:
            composition, target, label = sample['composition'], sample.get('target', {}), sample.get('label') or {}
            values = [sample['id'], sample['project_id'], sample.get('bottle'), sample['execution_state'], sample['review_state'],
                      composition['basis'], target.get('peg_fraction'), target.get('salt_fraction'), composition['peg_fraction'],
                      composition['salt_fraction'], composition['deviation']['peg_fraction'], composition['deviation']['salt_fraction'],
                      composition['units'], sample['training_eligibility']['eligible'], '; '.join(sample['training_eligibility']['reasons']),
                      label.get('version'), label.get('frame_id'), sample.get('mixed_at'), sample.get('execution_key'),
                      sample.get('replicate_group'), sample.get('replicate_index'), version.get('version'), version.get('commit')]
            writer.writerow(["'" + str(v) if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v for v in values])
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('data.json', json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))
            archive.writestr('metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2))
            archive.writestr('samples.csv', '\ufeff' + table.getvalue())
            archive.writestr('report.html', '<!doctype html><meta charset="utf-8"><h1>实验记录</h1><p>工程等待时间不代表已验证分相平衡；总质量不代表组分实测。</p><pre>' + html.escape(table.getvalue()) + '</pre>')
            missing = []
            for sample in samples:
                for frame in sample.get('frames', []):
                    path = self.directory / (frame['id'] + '.png')
                    if path.exists():
                        archive.write(path, 'images/' + frame['id'] + '.png')
                    else:
                        missing.append(frame['id'])
            if missing:
                archive.writestr('missing_images.json', json.dumps(missing))
        return output.getvalue()
