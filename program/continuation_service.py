"""Same-vial scheduling and conservative mass inventory, without hardware I/O."""
import copy
import math
import secrets
import time
from capture_store import timestamp, parse_time
import experiment_domain as domain
from observation_policy import validate_observation_policy


class ContinuationMixin:
    def _continuation_normalize(self, restarting=False):
        self.data.setdefault('continuation_runs', [])
        self.data.setdefault('active_continuation_run_id', None)
        for run in self.data['continuation_runs']:
            run.setdefault('observation_policy',validate_observation_policy({}))
            run.setdefault('min_initial_mass_g',1.)
            run.setdefault('reserve_mass_g',0.)
            run['budgets'].setdefault('source_mass_budget_g',{})
            run['used'].setdefault('source_mass_g',{})
        for bottle in self.data['bottles']:
            bottle.setdefault('inventory_history', [])
            if 'inventory' not in bottle:
                confirmed = not bottle.get('sample_id') and any(
                    h.get('bottle') == bottle['bottle'] and h.get('confirmed_clean_empty')
                    for h in self.data.get('bottle_history', []))
                bottle['inventory'] = self._empty_inventory(known=confirmed)
            if restarting and bottle.get('sample_id'):
                self._inventory_unknown(bottle, '程序重启后不能核实当前瓶内物料；确认更换干净空瓶后再续加')
        if restarting:
            for run in self.data['continuation_runs']:
                if run['status'] in ('running', 'waiting_review', 'waiting_observation', 'paused', 'blocked'):
                    run.update(status='interrupted', reason='程序重启；不会重放或自动继续加料', finished_epoch=time.time(), finished_at=timestamp())
        self.data['active_continuation_run_id'] = None

    @staticmethod
    def _empty_inventory(known=True, revision=0):
        return dict(known=known, revision=revision, total_mass_g=0., peg_mass_g=0., salt_mass_g=0.,
                    water_mass_g=0., salt_port=None, sample_id=None, project_id=None,
                    calibration_valid=known, reason='' if known else '请确认已换上干净空瓶',
                    basis='confirmed_empty' if known else 'unknown', updated_at=timestamp())

    def _bottle(self, number):
        number = domain.integer(number,1,5)
        bottle = next((b for b in self.data['bottles'] if b['bottle'] == number), None)
        if bottle is None:
            raise ValueError('自动实验只使用1至5号瓶')
        return bottle

    def _inventory_unknown(self, bottle, reason):
        old = bottle.get('inventory') or self._empty_inventory(known=False)
        bottle.setdefault('inventory_history', []).append(copy.deepcopy(old))
        bottle['inventory'] = dict(old, known=False, calibration_valid=False,
                                   revision=old.get('revision', 0)+1, reason=str(reason), updated_at=timestamp())

    def _inventory_reset(self, bottle):
        old = bottle.get('inventory') or self._empty_inventory(known=False)
        bottle.setdefault('inventory_history', []).append(copy.deepcopy(old))
        bottle['inventory'] = self._empty_inventory(revision=old.get('revision', 0)+1)

    def _continuation_view(self, result):
        now = time.time()
        for run in result.get('continuation_runs', []):
            elapsed = max(0., run.get('finished_epoch', now)-run['started_epoch']) if run.get('started_epoch') else 0.
            used, budgets = run['used'], run['budgets']
            used['elapsed_s'] = elapsed
            run['remaining'] = dict(rounds=max(0, budgets['round_budget']-used['rounds']),
                                    added_mass_g=max(0., budgets['added_mass_budget_g']-used['added_mass_g']),
                                    time_s=max(0., budgets['time_budget_s']-elapsed),
                                    source_mass_g={p:max(0.,m-used.get('source_mass_g',{}).get(p,0.))
                                                   for p,m in budgets.get('source_mass_budget_g',{}).items()})
        return result

    def _continuation_preview(self, body):
        from continuation_domain import preview_addition
        project = self._get('projects', body['project_id'])
        bottle = self._bottle(body['bottle'])
        request = dict(body, sample=bottle['bottle'], salt_port=project['salt_port'],
                       total_mass_g=project.get('total_mass_g', 1),
                       safe_mass_limit_g=project.get('safe_mass_limit_g'),
                       temperature_c=project.get('temperature_c', 25),
                       composition_tolerance_fraction=project.get('composition_tolerance_fraction', .01),
                       allow_trial_calibration=False)
        preview = preview_addition(request, copy.deepcopy(bottle['inventory']), project['stocks'],
                                   project['calibrations'], {str(p): self._signature(p) for p in self._ports(project)})
        problems = self._continuation_stock_issues(project)
        if problems:
            preview['issues'] = list(preview.get('issues', [])) + problems
            preview['executable'] = False
        return preview

    def _continuation_stock_issues(self, project):
        issues = self._compatibility_issues(project)
        if project['salt_port'] == 10:
            salt = next(s for s in project['stocks'] if s['port'] == 10)
            if salt.get('composition_basis') != 'anhydrous_equivalent':
                issues.append('10口二水柠檬酸三钠须记录无水当量组成；请更新母液和项目版本，不改写历史')
        return issues

    def _continuation_handle(self, op, body, start_epoch):
        if op == 'continuation_preview':
            return dict(preview=self._continuation_preview(body)), False
        if op == 'continuation_run':
            project = self._get('projects', body['project_id'])
            bottles = body.get('bottles')
            if not isinstance(bottles, list) or not bottles or len(set(bottles)) != len(bottles):
                raise ValueError('请选择不重复的自动瓶位')
            for bottle in bottles:
                self._bottle(bottle)
            step = domain.num(body.get('step', .02), .001, 1)
            bounds = {}
            for component in ('peg', 'salt'):
                lower = domain.num(body.get(component+'_min', 0), 0, 1)
                upper = domain.num(body.get(component+'_max', .4), lower, 1)
                bounds[component+'_min'], bounds[component+'_max'] = lower, upper
            grid_size = (int((bounds['peg_max']-bounds['peg_min'])/step)+1)*(int((bounds['salt_max']-bounds['salt_min'])/step)+1)
            if grid_size > 2500:
                raise ValueError('自动续加网格最多2500点，请增大步长或缩小范围')
            objective = body.get('objective', 'boundary')
            if objective not in ('boundary', 'two_phase_neighborhood'):
                raise ValueError('研究目标应为相边界或双相邻域')
            budgets = dict(round_budget=domain.integer(body.get('round_budget', 10), 1, 1000),
                           added_mass_budget_g=domain.num(body.get('added_mass_budget_g', 10), .000001, 10000),
                           time_budget_s=domain.num(body.get('time_budget_s', 3600), 1, 604800))
            source_budgets=body.get('source_mass_budget_g',{})
            if not isinstance(source_budgets,dict) or any(str(p) not in {str(x) for x in self._ports(project)} for p in source_budgets):
                raise ValueError('来源预算只能填写本项目的PEG、盐、水来源口')
            budgets['source_mass_budget_g']={str(p):domain.num(m,0,10000) for p,m in source_budgets.items()}
            policy=validate_observation_policy(body.get('observation_policy',{}))
            min_initial=domain.num(body.get('min_initial_mass_g',project.get('total_mass_g',1)),.000001,10)
            reserve=domain.num(body.get('reserve_mass_g',0),0,10)
            run = dict(id=secrets.token_hex(16), project_id=project['id'], created_at=timestamp(),
                       bottles=bottles, step=step, objective=objective, bounds=bounds, budgets=budgets,
                       observation_policy=policy,min_initial_mass_g=min_initial,reserve_mass_g=reserve,
                       used=dict(rounds=0, added_mass_g=0., completed_rounds=0,source_mass_g={}), status='draft',
                       reason='已创建，尚未启动', rounds=[], sample_ids=[], next_candidate=None,
                       bottle_states={}, waiting_reasons={}, planning_limits=dict(grid=2500, exact_per_bottle=20),
                       review_policy='valid_human_label_before_same_vial_addition' if policy['mode']=='human' else 'bounded_optical_candidate_before_same_vial_addition', method='bounded_reachable_rbf_heuristic',
                       method_details=dict(sampling_strategy='additive_continuation',independent_replicate=False,
                                           initial_condition='confirmed_empty_or_validated_parent_inventory',
                                           next_addition='after_valid_human_review' if policy['mode']=='human' else 'after_evidence_backed_optical_candidate',temperature_c=project.get('temperature_c',25),
                                           measured_temperature_c=project.get('measured_temperature_c'),temperature_control=False),
                       model_parameters=dict(version='continuation-rbf-v1',rbf_length_scale=.08,
                           classification='heuristic_not_calibrated_probability',
                           boundary_score='min_squared_coverage_distance * (0.25 + uncertainty)',
                           two_phase_score='min_squared_coverage_distance * (0.25 + phase_score + 0.5 * uncertainty)',
                           initial_score='1 - squared_distance_to_requested_bounds_center',
                           uncertainty='1-abs(2*phase_score-1); 1 when fewer than two labelled classes',
                           tie_break=['smaller_added_mass_g','shorter_estimated_duration_s']))
            self.data['continuation_runs'].append(run)
            return dict(run=run), True
        run = self._get('continuation_runs', body['run_id'])
        if op == 'continuation_pause':
            if run['status'] in ('running', 'waiting_review', 'waiting_observation', 'blocked'):
                run.update(status='paused', reason='已暂停后续加料；当前有限动作继续完成，墙钟预算继续计时')
                self.cancel_dispatch.set()
                self.data['running'] = False
            return dict(run=run), True
        if op not in ('continuation_start', 'continuation_resume'):
            raise ValueError('未知续加操作')
        allowed = ('draft',) if op == 'continuation_start' else ('paused',)
        if run['status'] not in allowed:
            raise ValueError('仅可启动新任务或显式恢复已暂停任务；中断任务不会重放')
        if start_epoch != self.stop_epoch or self.data['stop_status'] == 'requested':
            raise ValueError('停止请求已取消本次启动')
        if self.data['running']:
            raise ValueError('已有批次正在运行，请先暂停')
        project = self._get('projects', run['project_id'])
        issues = self._continuation_stock_issues(project)
        if issues:
            raise ValueError('; '.join(issues))
        if not self._camera_online():
            raise ValueError('相机未在线')
        run.setdefault('started_epoch', time.time())
        run.setdefault('started_at', timestamp())
        run.update(status='running', reason='等待选择可达的瓶位与目标')
        self.data.update(running=True, active_project_id=project['id'], active_continuation_run_id=run['id'],
                         active_batch=None, stop_status='unknown', message='')
        self.active_stop_epoch = start_epoch
        self.cancel_dispatch.clear()
        return dict(run=run), True

    def _continuation_bottle_state(self, run, bottle):
        inventory = bottle['inventory']
        if not inventory.get('known'):
            return 'unknown_inventory', inventory.get('reason', '瓶内物料未知')
        if inventory.get('total_mass_g', 0) > 1e-12:
            if inventory.get('project_id') != run['project_id']:
                return 'occupied', '瓶内物料属于另一项目，不能自动混用'
            if not inventory.get('calibration_valid'):
                return 'unknown_inventory', '前序质量交付依据不满足续加要求'
            if bottle.get('sample_id') != inventory.get('sample_id'):
                return 'occupied', '瓶位已被其他任务预约'
            parent = self._get('samples', inventory['sample_id'])
            if (run.get('observation_policy',{}).get('mode','human')=='human'
                    and (parent.get('label') or {}).get('source')=='automatic_optical'):
                return 'waiting_review','本次采用人工复核模式；上一阶段的自动候选须由人复核后才能续加'
            if not self._eligible(parent, self._get('projects', run['project_id'])):
                if run.get('observation_policy',{}).get('mode')=='automatic':
                    observation=parent.get('auto_observation') or {}
                    if observation.get('status') in ('unknown','human_reviewed'):
                        return 'observation_unknown','本瓶观察未得有效相态：'+', '.join(observation.get('reason_codes',[]))
                    return 'waiting_observation','等待当前瓶稳定光学证据；其他瓶可继续'
                return 'waiting_review', '等待本瓶有效人工单相/双相复核；不确定结果不能触发补加'
        elif bottle.get('sample_id'):
            return 'occupied', '空瓶已由其他排队任务预约'
        return 'ready', ''

    def _continuation_budget(self, run, now):
        remaining = dict(rounds=run['budgets']['round_budget']-run['used']['rounds'],
                         added_mass_g=run['budgets']['added_mass_budget_g']-run['used']['added_mass_g'],
                         time_s=run['budgets']['time_budget_s']-(now-run.get('started_epoch', now)),
                         source_mass_g={p:max(0.,m-run['used'].get('source_mass_g',{}).get(p,0.))
                                        for p,m in run['budgets'].get('source_mass_budget_g',{}).items()})
        return remaining

    def _continuation_finish(self, run, status, reason):
        run.update(status=status, reason=reason)
        run.setdefault('scheduling_stopped_at',timestamp())
        run.setdefault('scheduling_stopped_epoch',time.time())
        in_flight = status=='budget_complete' and any(
            s.get('continuation_run_id')==run['id'] and s['status']=='preparing' for s in self.data['samples'])
        run['finite_action_in_progress'] = in_flight
        if in_flight:
            run.pop('finished_at',None)
            run.pop('finished_epoch',None)
        else:
            run.update(finished_at=timestamp(),finished_epoch=time.time())
        if self.data.get('active_continuation_run_id') == run['id']:
            self.data['running'] = False
            self.cancel_dispatch.set()

    @staticmethod
    def _continuation_wait_for_other_bottles(run):
        """Check the same waiting states regardless of which planning stage failed."""
        states = {s['state'] for s in run['bottle_states'].values()}
        if 'waiting_observation' in states:
            run.update(status='waiting_observation', reason='等待其他瓶完成有限观察；局部规划失败只影响对应瓶')
        elif 'waiting_review' in states:
            run.update(status='waiting_review', reason='等待其他瓶人工复核；局部规划失败只影响对应瓶')
        else:
            return False
        return True

    def _continuation_tick(self, device_state, now):
        run = self._get('continuation_runs', self.data['active_continuation_run_id'])
        if run['status'] not in ('running', 'waiting_review', 'waiting_observation', 'blocked'):
            return None
        remaining = self._continuation_budget(run, now)
        if remaining['rounds'] <= 0 or remaining['added_mass_g'] <= 1e-12 or remaining['time_s'] <= 0:
            self._continuation_finish(run, 'budget_complete', '预算已到；不会开启新剂，已执行的有限动作照常完成')
            return None
        if device_state.get('busy'):
            return None
        if device_state.get('state')=='FAULT' or device_state.get('stop_status')=='unconfirmed' or device_state.get('common')=='unknown':
            run.update(status='paused',reason='设备或公共液路状态无法确认，已暂停后续液体动作；核实后显式恢复')
            self.data['running']=False
            self.cancel_dispatch.set()
            return None
        if not device_state.get('connected'):
            run.update(status='paused',reason='设备通信已断开，后续液体动作暂停；重新连接后显式恢复')
            self.data['running']=False
            self.cancel_dispatch.set()
            return None
        camera_online=self._camera_online()
        project = self._get('projects', run['project_id'])
        issues = self._continuation_stock_issues(project)
        if issues:
            self._continuation_finish(run, 'interrupted', '; '.join(issues))
            return None
        eligible_offsets = [o for o in project['photo_offsets_s'] if o >= project['min_settle_s']]
        if not eligible_offsets:
            self._continuation_finish(run, 'blocked', '拍照计划没有位于有效观察窗口的时间点')
            return None
        observation_s = min(eligible_offsets)
        policy=run.get('observation_policy',{})
        if policy.get('mode')=='automatic':
            observation_s += (policy['stable_frames']-1)*max(policy['min_frame_spacing_s'],policy['retry_interval_s'])
        if remaining['time_s'] <= observation_s:
            self._continuation_finish(run, 'budget_complete', '剩余墙钟预算不足以完成新一剂及首次有效观察')
            return None
        ready = []
        run['bottle_states'], run['waiting_reasons'] = {}, {}
        for number in run['bottles']:
            bottle = self._bottle(number)
            state, reason = self._continuation_bottle_state(run, bottle)
            run['bottle_states'][str(number)] = dict(state=state, reason=reason, sample_id=bottle.get('sample_id'))
            if reason:
                run['waiting_reasons'][str(number)] = reason
            if state == 'ready':
                ready.append(bottle)
        if not ready:
            if self._continuation_wait_for_other_bottles(run):
                return None
            if policy.get('mode')=='automatic':
                self._continuation_finish(run,'exhausted','所有瓶均无可执行下一点：'+'; '.join(run['waiting_reasons'].values()))
            else:
                run.update(status='blocked',reason='所选瓶位尚无已知物料，请处理瓶位或确认更换空瓶')
            return None
        if not camera_online:
            run.update(status='blocked',reason='相机离线，暂停新加料；已配样品继续有限观察计时')
            return None
        epoch = self.stop_epoch
        candidate, training = self._continuation_choose(run, project, ready, remaining, observation_s)
        # Stop can arrive while bounded planning holds the service lock. The
        # hardware stop is sent first and this epoch prevents a later claim.
        if epoch != self.stop_epoch or self.cancel_dispatch.is_set() or not self.data['running']:
            return None
        if candidate is None:
            diagnostics = run['planning_diagnostics']
            if self._continuation_wait_for_other_bottles(run):
                return None
            if diagnostics['theoretically_feasible'] == 0:
                self._continuation_finish(run,'exhausted','当前有限网格、容量及质量预算内没有可达新目标：'+'; '.join(diagnostics.get('rejection_reasons',{})))
            else:
                reason='有界精编译未找到可执行方案；未证明全局无解：'+'; '.join(diagnostics.get('rejection_reasons',{}))
                if policy.get('mode')=='automatic' and not diagnostics['exact_limit_reached']:
                    self._continuation_finish(run,'exhausted','所选瓶在当前有限网格及标定下均无可执行新点：'+'; '.join(diagnostics.get('rejection_reasons',{})))
                else:
                    if diagnostics['exact_limit_reached']:
                        reason='本轮有界精编译限额已到，尚有未检查点；请调整网格或运行设置后再规划。'+reason
                    run.update(status='paused',reason=reason)
                    self.data['running'] = False
                    self.cancel_dispatch.set()
            return None
        run.update(status='running', next_candidate=copy.deepcopy(candidate), reason='正在执行已冻结的补加方案')
        return self._continuation_claim(run, project, candidate, training, epoch)

    def _continuation_choose(self, run, project, bottles, remaining, observation_s):
        from continuation_domain import theoretical_addition, within_research_bounds
        training, anchors, coverage = [], [], []
        for sample in self.data['samples']:
            if sample.get('project_id') != project['id'] or sample['status']=='cancelled':
                continue
            composition = domain.composition_record(sample,project)
            if composition['peg_fraction'] is not None:
                anchors.append((composition['peg_fraction'],composition['salt_fraction']))
                coverage.append(dict(sample_id=sample['id'],status=sample['status'],basis=composition['basis'],
                                     peg_fraction=composition['peg_fraction'],salt_fraction=composition['salt_fraction']))
            if self._eligible(sample, project):
                training.append(dict(id=sample['id'], composition=composition,label=copy.deepcopy(sample['label'])))
        classes = {v['label']['classification'] for v in training}
        def score(peg, salt):
            center = ((run['bounds']['peg_min']+run['bounds']['peg_max'])/2,
                      (run['bounds']['salt_min']+run['bounds']['salt_max'])/2)
            distance = min(((peg-p)**2+(salt-q)**2 for p,q in anchors),
                           default=max(0.,1-(peg-center[0])**2-(salt-center[1])**2))
            weights = [math.exp(-((peg-v['composition']['peg_fraction'])**2+(salt-v['composition']['salt_fraction'])**2)/.08**2) for v in training]
            den = sum(weights)
            phase = sum(w for w,v in zip(weights,training) if v['label']['classification']=='two_phase')/den if den else .5
            uncertainty = 1-abs(2*phase-1) if len(classes)==2 else 1.
            factor = (.25+phase+.5*uncertainty) if run['objective']=='two_phase_neighborhood' else (.25+uncertainty)
            return distance*factor, phase, uncertainty
        bounds, step = run['bounds'], run['step']
        all_candidates = []
        diagnostics = dict(theoretically_feasible=0, exact_evaluated=0, exact_limit_reached=False, coverage_anchor_count=len(anchors),rejection_reasons={},by_bottle={})
        def rejected(issues):
            for issue in issues:
                diagnostics['rejection_reasons'][issue]=diagnostics['rejection_reasons'].get(issue,0)+1
                bottle_diagnostics['rejection_reasons'][issue]=bottle_diagnostics['rejection_reasons'].get(issue,0)+1
        for bottle in bottles:
            feasible = []
            bottle_diagnostics = dict(theoretically_feasible=0, exact_evaluated=0, executable_candidates=0,
                                      exact_limit_reached=False, rejection_reasons={})
            diagnostics['by_bottle'][str(bottle['bottle'])] = bottle_diagnostics
            for i in range(int((bounds['peg_max']-bounds['peg_min'])/step+1e-8)+1):
                peg = round(bounds['peg_min']+i*step, 9)
                for j in range(int((bounds['salt_max']-bounds['salt_min'])/step+1e-8)+1):
                    salt = round(bounds['salt_min']+j*step, 9)
                    if any(abs(peg-p)<1e-9 and abs(salt-q)<1e-9 for p,q in anchors):
                        continue
                    request = dict(project_id=project['id'], bottle=bottle['bottle'], sample=bottle['bottle'],
                                   peg_fraction=peg, salt_fraction=salt, salt_port=project['salt_port'],
                                   total_mass_g=project.get('total_mass_g',1), safe_mass_limit_g=project.get('safe_mass_limit_g'),
                                   min_initial_mass_g=run.get('min_initial_mass_g',project.get('total_mass_g',1)),reserve_mass_g=run.get('reserve_mass_g',0),
                                   source_mass_remaining_g=remaining.get('source_mass_g',{}),
                                   research_bounds=copy.deepcopy(bounds),
                                   max_added_mass_g=remaining['added_mass_g'], remaining_time_s=remaining['time_s']-observation_s)
                    try:
                        theoretical = theoretical_addition(request, bottle['inventory'], project['stocks'])
                    except ValueError as exc:
                        rejected([str(exc)])
                        continue
                    if not theoretical.get('reachable'):
                        rejected(theoretical.get('issues',[]))
                        continue
                    preliminary = score(peg, salt)[0]
                    feasible.append((preliminary, -theoretical['added_mass_g'], request))
            feasible.sort(key=lambda v:(v[0],v[1]), reverse=True)
            bottle_diagnostics['theoretically_feasible'] = len(feasible)
            bottle_diagnostics['exact_limit_reached'] = len(feasible)>run['planning_limits']['exact_per_bottle']
            diagnostics['theoretically_feasible'] += len(feasible)
            diagnostics['exact_limit_reached'] |= bottle_diagnostics['exact_limit_reached']
            for _, _, request in feasible[:run['planning_limits']['exact_per_bottle']]:
                diagnostics['exact_evaluated'] += 1
                bottle_diagnostics['exact_evaluated'] += 1
                preview = self._continuation_preview(request)
                if not preview['executable'] or preview['added_mass_g'] <= 0:
                    rejected(preview.get('issues',[]))
                    continue
                composition = preview['composition']
                peg, salt = composition['peg_fraction'], composition['salt_fraction']
                if not within_research_bounds(peg, salt, bounds):
                    rejected(['编译后预计组成超出研究范围'])
                    continue
                if any(abs(peg-p)<1e-9 and abs(salt-q)<1e-9 for p,q in anchors):
                    rejected(['取整后与已有采用组成相同，不重复自动建点'])
                    continue
                value, phase, uncertainty = score(peg,salt)
                bottle_diagnostics['executable_candidates'] += 1
                all_candidates.append(dict(bottle=bottle['bottle'], target=preview['target'], composition=composition,
                                           preview=preview, inventory_key=preview['inventory_key'],
                                           selection_score=value, classification_score=phase, uncertainty=uncertainty,
                                           reason=('initial_space_coverage' if not training else 'seek_other_phase_class' if len(classes)<2 else 'reachable_boundary_coverage') if run['objective']=='boundary' else 'reachable_two_phase_neighborhood',
                                           data_regime='no_valid_labels' if not training else 'one_class' if len(classes)<2 else 'two_classes',
                                           initial_empty=bottle['inventory'].get('total_mass_g',0)<=1e-12,
                                           estimated_duration_s=preview['estimated_duration_s'], added_mass_g=preview['added_mass_g']))
            if not bottle_diagnostics['executable_candidates']:
                reason = '; '.join(bottle_diagnostics['rejection_reasons']) or '有限网格内无未探索的可达新点'
                number = str(bottle['bottle'])
                run['bottle_states'].setdefault(number, dict(sample_id=bottle.get('sample_id'))).update(
                    state='planning_blocked' if feasible else 'no_reachable_point', reason=reason)
                run['waiting_reasons'][number] = reason
        run['planning_diagnostics'] = diagnostics
        run['coverage_snapshot'] = coverage
        if not all_candidates:
            return None, training
        return max(all_candidates, key=lambda c:(c['initial_empty'],c['selection_score'], -c['added_mass_g'], -c['estimated_duration_s'])), training

    def _continuation_claim(self, run, project, candidate, training, epoch):
        from continuation_domain import inventory_key
        bottle = self._bottle(candidate['bottle'])
        if inventory_key(bottle['inventory']) != candidate['inventory_key'] or epoch != self.stop_epoch:
            return None
        remaining = self._continuation_budget(run, time.time())
        observation = min(o for o in project['photo_offsets_s'] if o >= project['min_settle_s'])
        policy=run.get('observation_policy',{})
        if policy.get('mode')=='automatic':observation+=(policy['stable_frames']-1)*max(policy['min_frame_spacing_s'],policy['retry_interval_s'])
        if remaining['rounds'] < 1 or candidate['added_mass_g'] > remaining['added_mass_g']+1e-12 or candidate['estimated_duration_s']+observation > remaining['time_s']:
            self._continuation_finish(run,'budget_complete','规划期间剩余预算已不足，未发送动作')
            return None
        parent_id = bottle['inventory'].get('sample_id')
        sample_id = secrets.token_hex(16)
        parent = self._get('samples', parent_id) if parent_id else None
        sample = dict(id=sample_id, project_id=project['id'], bottle=bottle['bottle'], created_at=timestamp(),
                      parent_sample_id=parent_id, lineage_root_id=parent.get('lineage_root_id', parent_id) if parent else sample_id,
                      continuation_run_id=run['id'], kind='continuation', status='preparing', execution_state='preparing',
                      observation_policy=copy.deepcopy(run['observation_policy']),
                      is_independent_repeat=False,
                      target=copy.deepcopy(candidate['target']), recipe=copy.deepcopy(candidate['preview']),
                      execution_key=domain.execution_key(candidate['preview']['plan']), mixed_at=None,
                      frames=[], label=None, exclusions=[], exclusion='', generation=0, observation_history=[])
        if parent:
            parent.update(continuation_replaced_at=timestamp(), continued_by=sample_id)
            if parent['status']=='settling':
                self._set_state(parent,'observation_complete')
            self._cancel_photos(parent)
        self.data['samples'].append(sample)
        bottle['sample_id'] = sample_id
        run['sample_ids'].append(sample_id)
        run['used']['rounds'] += 1
        run['used']['added_mass_g'] += candidate['added_mass_g']
        for row in candidate['preview']['plan']['items']:
            port=str(row['source'])
            run['used']['source_mass_g'][port]=run['used']['source_mass_g'].get(port,0.)+row['estimated_mass_g']
        run['rounds'].append(dict(id=secrets.token_hex(16), index=run['used']['rounds'], sample_id=sample_id,
                                  parent_sample_id=parent_id, bottle=bottle['bottle'], status='reserved', at=timestamp(),
                                  candidate=copy.deepcopy(candidate), training_records=copy.deepcopy(training),
                                  charged_mass_g=candidate['added_mass_g'], initial_inventory=copy.deepcopy(bottle['inventory']),
                                  model_parameters=dict(copy.deepcopy(run['model_parameters']),coverage_coordinates=copy.deepcopy(run['coverage_snapshot'])),
                                  observation_policy=copy.deepcopy(run['observation_policy']),
                                  approval_basis=('automatic_after_optical_candidate' if (parent.get('label') or {}).get('source')=='automatic_optical' else 'automatic_after_valid_human_parent_review') if parent else 'explicit_run_start_on_confirmed_empty'))
        return dict(action='experiment_recipe', experiment_sample_id=sample_id, plan=copy.deepcopy(sample['recipe']['plan']))

    def _continuation_action_event(self, event, sample, plan, error=None):
        bottle = self._bottle(sample['bottle']) if sample.get('bottle') else None
        run = self._get('continuation_runs', sample['continuation_run_id']) if sample.get('continuation_run_id') else None
        record = next((r for r in run['rounds'] if r['sample_id']==sample['id']),None) if run else None
        if event == 'failed':
            if bottle:
                self._inventory_unknown(bottle,'配液中断或设备响应不确定，禁止定量续加')
            if record:
                record.update(status='interrupted',error=str(error),finished_at=timestamp())
                self._continuation_finish(run,'interrupted','有限加料中断；预算保守保留，不重放')
            return
        if event != 'completed' or sample.get('inventory_committed'):
            return
        if not plan or plan.get('completed') is not True or sample['status'] != 'settling':
            if bottle:
                self._inventory_unknown(bottle,'缺少完整动作和混合结束依据')
            if record:
                record.update(status='interrupted',finished_at=timestamp())
                self._continuation_finish(run,'interrupted','缺少完整动作和混合结束依据；不会重放')
            return
        old = bottle['inventory']
        if sample.get('kind') == 'continuation':
            from continuation_domain import inventory_key
            if not old.get('known') or inventory_key(old) != sample['recipe']['inventory_key']:
                self._inventory_unknown(bottle,'完成回调与冻结起点不一致，未覆盖库存')
                if record:
                    record.update(status='interrupted',finished_at=timestamp())
                    self._continuation_finish(run,'interrupted','完成回调与库存冻结起点不一致')
                return
        else:
            from continuation_domain import inventory_key
            initial = sample.get('independent_start_inventory') or {}
            if (not initial.get('known') or initial.get('total_mass_g') != 0 or initial.get('sample_id') is not None
                    or inventory_key(old) != inventory_key(initial)):
                self._inventory_unknown(bottle,'独立配液缺少已确认空瓶起点；完成动作不能推断旧残液为零，禁止续加')
                self._exclude(sample,'unknown_initial_inventory','未确认空瓶起点，累计组成无效，不能用于训练或定量续加',hard=True)
                return
        project = self._get('projects',sample['project_id'])
        estimate_sample = dict(sample)
        estimate_sample.pop('measured_composition',None)
        composition = domain.composition_record(estimate_sample,project)
        estimated = domain._compiled_estimate(sample['recipe'],sample['target'],project) or {}
        total = estimated.get('total_mass_g')
        if total is None or composition['peg_fraction'] is None or composition['salt_fraction'] is None:
            self._inventory_unknown(bottle,'缺少累计质量估计依据')
            if record:
                record.update(status='interrupted',finished_at=timestamp())
                self._continuation_finish(run,'interrupted','缺少累计质量估计依据')
            return
        peg, salt = estimated['peg_mass_g'], estimated['salt_mass_g']
        bottle.setdefault('inventory_history',[]).append(copy.deepcopy(old))
        bottle['inventory'] = dict(known=True,revision=old.get('revision',0)+1,total_mass_g=total,
                                   peg_mass_g=peg,salt_mass_g=salt,water_mass_g=total-peg-salt,
                                   salt_port=project['salt_port'],sample_id=sample['id'],project_id=project['id'],
                                   calibration_valid=composition['valid'],composition_evidence=copy.deepcopy(composition),
                                   calibration_ids=list(dict.fromkeys(old.get('calibration_ids',[])+[r['reference']['id'] for r in sample['recipe']['plan']['items']])),
                                   basis='calibrated_command_estimate_not_independent_measurement',reason='',updated_at=timestamp())
        sample['inventory_committed'] = True
        sample['inventory_after'] = copy.deepcopy(bottle['inventory'])
        sample.setdefault('lineage_root_id',sample['id'])
        if record:
            record.update(status='completed',finished_at=timestamp(),inventory_after=copy.deepcopy(bottle['inventory']))
            run['used']['completed_rounds'] += 1
            if run.get('finite_action_in_progress'):
                run.update(finite_action_in_progress=False,finished_at=timestamp(),finished_epoch=time.time())

    def _continuation_stop(self, reason):
        for bottle in self.data['bottles']:
            if bottle.get('sample_id'):
                self._inventory_unknown(bottle,reason)
        for run in self.data['continuation_runs']:
            if run['status'] in ('running','waiting_review','waiting_observation','blocked','paused') or run.get('finite_action_in_progress'):
                run.update(status='stopped',reason=reason,finished_at=timestamp(),finished_epoch=time.time(),finite_action_in_progress=False)
                run.setdefault('scheduling_stopped_epoch',time.time())
                run.setdefault('scheduling_stopped_at',timestamp())
