"""Pure mass-domain planning. Estimates are not measured deliveries."""
import math, statistics, uuid, copy, hashlib, json
from capture_store import timestamp

def num(x, lo=0, hi=1000000000.0):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError('需要有限数值')
    x = float(x)
    if not math.isfinite(x) or not lo <= x <= hi:
        raise ValueError('数值超出范围')
    return x

def integer(x, lo, hi):
    value = num(x, lo, hi)
    if value != int(value):
        raise ValueError('需要整数')
    return int(value)


def plumbing_signature(config, source=None):
    """Physical delivery context, independent of unrelated stocks or COM naming."""
    geometry = copy.deepcopy(config.get('geometry', {}))
    branches = geometry.pop('source_cm', {})
    if source is not None:
        geometry['source_cm'] = {str(source): branches.get(str(source))}
    else:
        geometry['source_cm'] = branches
    context = dict(geometry=geometry, pump=config.get('pump'), valve=config.get('valve'),
                   reset_pulses={k: config.get('reset', {}).get(k) for k in ('wash', 'purge')})
    return hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def execution_key(plan):
    """Identity of the exact fluid commands; independent repeats retain their IDs."""
    def motion(value):
        return {key: value.get(key) for key in ('steps', 'turns', 'rpm', 'direction')}
    commands = []
    for row in plan.get('items', []):
        for part in row.get('parameters', []):
            commands.append(dict(source=row['source'], aspirate=motion(part['aspirate']), chase=motion(part['chase'])))
    context = dict(commands=commands, mix_after=plan.get('mix_after'),
                   mixing=motion(plan.get('mixing') or {}), purge_after=plan.get('purge_after'), purge=motion(plan.get('purge') or {}))
    return hashlib.sha256(json.dumps(context, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _signature_for(signatures, source):
    if isinstance(signatures, dict):
        return signatures.get(str(source), signatures.get(source))
    return signatures


def _coordinate_pair(value):
    if not isinstance(value, dict):
        raise ValueError('缺少组分分析数据')
    peg = num(value.get('peg_fraction'), 0, 1)
    salt = num(value.get('salt_fraction'), 0, 1)
    if peg + salt > 1 + 1e-12:
        raise ValueError('组分质量分数之和超过1')
    return peg, salt


def _compiled_estimate(recipe, target, project):
    """Rebuild estimate from frozen stock references and actual integer commands."""
    rows = recipe.get('plan', {}).get('items', [])
    if not rows:
        return None
    masses, solutes = {}, {}
    try:
        for row in rows:
            port = integer(row['source'], 10, 15)
            ref = row['reference']
            stock = (row.get('stock_snapshot') or ref.get('stock_snapshot') or
                     next((s for s in project.get('stocks', []) if s['port']==port), None))
            if not stock:
                return None
            steps = sum(integer(part['aspirate']['steps'], 1, 20000) for part in row['parameters'])
            mass = steps * num(ref['mass_per_step_g'], 1e-12)
            masses[port] = masses.get(port, 0) + mass
            solutes[port] = solutes.get(port, 0) + mass * num(stock['mass_fraction'], 0, 1)
        needed = {int(port) for port, mass in recipe.get('stock_masses', {}).items() if mass>0}
        if not needed.issubset(masses):
            return None
        total = sum(masses.values())
        if not total:
            return None
        salt_port = integer(target['salt_port'], 10, 13)
        return dict(stock_masses=masses, total_mass_g=total,
                    peg_fraction=solutes.get(14, 0)/total,
                    salt_fraction=solutes.get(salt_port, 0)/total)
    except (KeyError, TypeError, ValueError):
        return None


def composition_record(sample, project=None):
    """One coordinate policy for training, plots, deduplication and exports.

    Target fractions are never promoted into adopted coordinates. Composition
    validity does not certify sample completion or optical review eligibility.
    """
    project = project or {}
    target = sample.get('target') or sample.get('recipe', {}).get('target') or {}
    recipe = sample.get('recipe') or {}
    compiled_estimate = _compiled_estimate(recipe, target, project)
    estimate = compiled_estimate or recipe.get('estimated') or sample.get('estimated') or {}
    tolerance = num(project.get('composition_tolerance_fraction',
                                target.get('composition_tolerance_fraction', .01)), 0, 1)
    basis = 'unavailable'
    reasons, warnings = [], []
    peg = salt = None
    measured = sample.get('measured_composition')
    if measured:
        kind = measured.get('measurement_kind', 'component_assay')
        try:
            if kind != 'component_assay' or not isinstance(measured.get('method'), str) or not measured['method'].strip() or not isinstance(measured.get('evidence'), str) or not measured['evidence'].strip():
                raise ValueError('实测组成须有独立组分分析方法和依据；整瓶总质量不能推导组分')
            peg, salt = _coordinate_pair(measured)
            basis = 'measured'
        except (ValueError, TypeError):
            warnings.append('实测组成缺少独立组分分析依据，不能采用')
    if basis == 'unavailable':
        try:
            peg, salt = _coordinate_pair(estimate)
            basis = 'estimated'
        except (ValueError, TypeError):
            reasons.append('缺少可用估计或实测组成；不使用目标组成替代')
    if basis == 'estimated':
        plan = recipe.get('plan') or {}
        rows = plan.get('items', [])
        bottle = sample.get('bottle') or plan.get('sample')
        if not rows:
            reasons.append('估计组成缺少已编译动作与来源标定记录')
        elif compiled_estimate is None:
            reasons.append('缺少完整的冻结来源组成，不能核对整数步估计组成')
        for row in rows:
            ref = row.get('reference') or {}
            prefix = '来源%s：' % row.get('source', '?')
            if ref.get('status', 'trial') != 'validated':
                reasons.append(prefix + '标定处于试用或失效状态')
            elif not ref.get('validation_evidence'):
                reasons.append(prefix + '缺少标定范围验证依据')
            minimum, maximum = ref.get('min_steps'), ref.get('max_steps')
            chunks = [p.get('aspirate', {}).get('steps') for p in row.get('parameters', [])]
            if (not chunks or type(minimum) is not int or type(maximum) is not int or
                    any(type(n) is not int or not minimum <= n <= maximum for n in chunks)):
                reasons.append(prefix + '动作不在有记录的标定步数范围内')
            if bottle not in ref.get('sample_bottles', []):
                reasons.append(prefix + '此瓶位未纳入标定适用范围')
            if len(ref.get('sample_bottles', [])) > 1 and not ref.get('bottle_scope_evidence'):
                reasons.append(prefix + '缺少跨瓶位验证依据')
            for part in row.get('parameters', []):
                aspirate, chase = part.get('aspirate', {}), part.get('chase', {})
                if (aspirate.get('rpm') != ref.get('aspirate_rpm') or
                        chase.get('steps') != ref.get('chase_steps') or
                        chase.get('rpm') != ref.get('chase_rpm')):
                    reasons.append(prefix + '实际吸取转速或追送动作偏离标定条件')
                if part.get('source') != row.get('source') or part.get('sample') != bottle:
                    reasons.append(prefix + '实际动作来源或瓶位不一致')
            signatures = plan.get('geometry_signatures')
            if signatures and ref.get('geometry_signature') != _signature_for(signatures, row.get('source')):
                reasons.append(prefix + '编译时液路条件偏离标定条件')
            stocks = project.get('stocks', [])
            stock = next((s for s in stocks if s['port'] == row.get('source')), None)
            if stock and (ref.get('stock_version') != stock.get('version') or
                          ref.get('stock_batch', '') != stock.get('batch', '') or
                          ref.get('temperature_c') != target.get('temperature_c', project.get('temperature_c', 25))):
                reasons.append(prefix + '来源版本、批次或温度不兼容')
        reasons.extend(recipe.get('issues') or [])
    deviation = dict(peg_fraction=None, salt_fraction=None, max_abs_fraction=None,
                     tolerance_fraction=tolerance)
    if peg is not None and salt is not None:
        try:
            target_peg, target_salt = _coordinate_pair(target)
            deviation.update(peg_fraction=peg-target_peg, salt_fraction=salt-target_salt,
                             max_abs_fraction=max(abs(peg-target_peg), abs(salt-target_salt)))
            if deviation['max_abs_fraction'] > tolerance + 1e-12:
                reasons.append('采用组成与目标组成的偏差超过项目误差限')
        except (ValueError, TypeError):
            reasons.append('缺少合法目标组成，不能核对偏差')
    return dict(basis=basis, peg_fraction=peg, salt_fraction=salt, deviation=deviation,
                valid=basis!='unavailable' and not reasons, reasons=list(dict.fromkeys(reasons)),
                warnings=warnings, units='mass_fraction_w_w',
                method=measured.get('method') if basis=='measured' else 'integer_steps_and_source_delivery_calibration' if basis=='estimated' else None,
                evidence=measured.get('evidence') if basis=='measured' else None,
                execution_key=execution_key(recipe['plan']) if recipe.get('plan') else None)

def default_stocks():
    return [dict(port=p, name=n, solute=s, mass_fraction=c, batch='', temperature_c=25, density_g_ml=None, density_evidence='', version=1) for p, n, s, c in [(10, '柠檬酸三钠', 'sodium_citrate', 0.3), (11, '硫酸铵', 'ammonium_sulfate', 0.4), (12, '硫酸钠', 'sodium_sulfate', 0.15), (13, '磷酸氢二钾 K₂HPO₄', 'K₂HPO₄', 0.4), (14, 'PEG4000', 'PEG4000', 0.4), (15, '水', 'water', 0)]]

def validate_stock(data):
    s = copy.deepcopy(data)
    s['port'] = integer(s['port'], 10, 15)
    s['mass_fraction'] = num(s['mass_fraction'], 0, 1)
    s['temperature_c'] = num(s.get('temperature_c', 25), 0, 100)
    if s.get('density_g_ml') is not None:
        s['density_g_ml'] = num(s['density_g_ml'], 0.001, 10)
        if not s.get('density_evidence'):
            raise ValueError('密度需要实测依据')
    s.setdefault('version', 1)
    s.setdefault('batch', '')
    s['version'] = integer(s['version'], 1, 1000000000)
    for key, maximum in [('name', 200), ('solute', 200), ('batch', 200), ('density_evidence', 2000)]:
        s.setdefault(key, '')
        if not isinstance(s[key], str) or len(s[key]) > maximum or (key in ('name', 'solute') and (not s[key].strip())):
            raise ValueError('母液文本字段无效')
    if s['port'] == 15 and s['mass_fraction'] != 0 or (s['port'] != 15 and s['mass_fraction'] <= 0):
        raise ValueError('母液浓度无效')
    return s

def pulse(steps, rpm, timeout=None):
    from controller import pulse_checked
    steps = integer(steps, 1, 20000)
    rpm = num(rpm, 0.1, 100)
    p = dict(steps=steps, rpm=rpm, timeout_s=num(timeout, 1, 120) if timeout is not None else math.ceil(steps * 0.36 / rpm + 10))
    pulse_checked(p)
    if p['timeout_s'] < steps * 0.36 / float(rpm):
        raise ValueError('超时不能短于动作时长')
    return p

def make_calibration(data, stock, geometry_signature):
    d = copy.deepcopy(data)
    s = validate_stock(stock)
    if integer(d['source'], 10, 15) != s['port']:
        raise ValueError('标定来源不匹配')
    groups = d.get('groups_g')
    if groups is None:
        if not s.get('density_g_ml') or not s.get('density_evidence'):
            raise ValueError('体积标定需要实测密度')
        groups = [num(v, 1e-06) * s['density_g_ml'] for v in d['groups_ml']]
    if not isinstance(groups, list) or not 1 <= len(groups) <= 1000:
        raise ValueError('缺少原始称量')
    groups = [num(v, 1e-06, 1000) for v in groups]
    doses = integer(d['doses_per_group'], 1, 1000)
    a = pulse(d['aspirate_steps'], d['aspirate_rpm'])
    c = pulse(d['chase_steps'], d['chase_rpm'])
    cap = integer(d.get('max_segment_steps', a['steps']), 1, a['steps'])
    status = d.get('status', 'trial')
    if status not in ('trial', 'validated', 'invalid'):
        raise ValueError('标定状态必须为试用、已验证或失效')
    minimum = integer(d.get('min_steps', 1), 1, 20000)
    maximum = integer(d.get('max_steps', cap), minimum, 20000)
    if not minimum <= a['steps'] <= maximum:
        raise ValueError('称量参考点须处于声明的步数范围内')
    bottles = d.get('sample_bottles', [1])
    if not isinstance(bottles, list) or not bottles or len(set(bottles)) != len(bottles) or any(type(n) is not int or n not in range(1, 7) for n in bottles):
        raise ValueError('瓶位适用范围须为1—6中的不重复编号')
    validation = d.get('validation_evidence', '')
    scope = d.get('bottle_scope_evidence', '')
    if not isinstance(validation, str) or not isinstance(scope, str) or len(validation)>5000 or len(scope)>5000:
        raise ValueError('标定依据文字无效')
    if status == 'validated' and (not validation.strip() or 'min_steps' not in d or 'max_steps' not in d):
        raise ValueError('已验证标定须声明最小/最大步数及验证依据')
    if status == 'validated' and len(bottles)>1 and not scope.strip():
        raise ValueError('跨瓶位共用标定需要验证依据')
    return dict(d, id=uuid.uuid4().hex, created_at=timestamp(), groups_g=groups, mass_per_step_g=statistics.mean(groups) / doses / a['steps'], group_rsd=statistics.stdev(groups) / statistics.mean(groups) if len(groups) > 1 else None, stock_version=s['version'], stock_batch=s['batch'], stock_snapshot=copy.deepcopy(s), temperature_c=s['temperature_c'], geometry_signature=geometry_signature, max_segment_steps=cap, min_steps=minimum, max_steps=maximum, status=status, sample_bottles=bottles, validation_evidence=validation, bottle_scope_evidence=scope, range_basis='operator_validated_range' if status=='validated' else 'trial_proportional_estimate_not_validated', aspirate_steps=a['steps'], aspirate_rpm=a['rpm'], chase_steps=c['steps'], chase_rpm=c['rpm'], raw_evidence=copy.deepcopy(data))

def preview_recipe(request, stocks, calibrations, geometry_signature):
    r = request
    sp = integer(r['salt_port'], 10, 13)
    bottle = integer(r.get('sample', 1), 1, 6)
    tolerance = num(r.get('composition_tolerance_fraction', .01), 0, 1)
    allow_trial = r.get('allow_trial_calibration', True)
    if not isinstance(allow_trial, bool):
        raise ValueError('试用标定选项须为布尔值')
    ss = {s['port']: validate_stock(s) for s in stocks}
    m = num(r.get('total_mass_g', 1), 1e-06, 10)
    p = num(r['peg_fraction'], 0, 1)
    q = num(r['salt_fraction'], 0, 1)
    masses = {14: m * p / ss[14]['mass_fraction'], sp: m * q / ss[sp]['mass_fraction']}
    water = m - sum(masses.values())
    if water < -1e-10:
        raise ValueError('补水质量为负')
    masses[15] = max(0, water)
    issues, warnings = [], []
    rows = []
    est = {}
    total = 0
    limit = r.get('safe_mass_limit_g')
    if limit is None or m > num(limit, 1e-06):
        issues.append('需要足够的明确安全总质量上限')
    for port, mass in masses.items():
        if mass == 0:
            est[port] = 0
            continue
        s = ss[port]
        valid = [c for c in calibrations if c['source'] == port and c['stock_version'] == s['version'] and (c['geometry_signature'] == _signature_for(geometry_signature, port)) and (c.get('stock_batch', '') == s.get('batch', '')) and (c.get('temperature_c') == r.get('temperature_c', 25) == s['temperature_c'])]
        if not valid:
            issues.append('来源 %s 缺少兼容标定' % port)
            continue
        valid = [c for c in valid if bottle in c.get('sample_bottles', [1])]
        if not valid:
            issues.append('来源 %s 的标定未覆盖 %s 号瓶' % (port, bottle))
            continue
        c = valid[-1]
        status = c.get('status', 'trial')
        if status not in ('trial', 'validated'):
            issues.append('来源 %s 标定已失效' % port)
            continue
        if status == 'trial':
            warnings.append('来源 %s 使用试用比例估计；未经验证，不能作为训练坐标' % port)
            if not allow_trial:
                issues.append('来源 %s 只有试用标定，当前项目未允许试用配样' % port)
        rate = num(c['mass_per_step_g'], 1e-12)
        steps = math.floor(mass / rate + 0.5)
        minimum = integer(c.get('min_steps', 1), 1, 20000)
        cap = integer(c.get('max_steps', c.get('max_segment_steps', c['aspirate_steps'])), minimum, 20000)
        cap = min(cap, integer(c.get('max_segment_steps', c['aspirate_steps']), 1, 20000))
        if s.get('density_g_ml'):
            cap = min(cap, math.floor(0.8 * s['density_g_ml'] / rate))
        if steps < minimum or cap < minimum:
            issues.append('来源 %s 的剂量低于标定最小步数或分段上限不足' % port)
            continue
        count = math.ceil(steps / cap)
        if count > 60:
            issues.append('来源 %s 单瓶所需段数超过60，请调整总量或标定范围' % port)
            continue
        # Spread integer rounding across parts, avoiding an unvalidated tiny tail.
        base, extra = divmod(steps, count)
        if base < minimum:
            issues.append('来源 %s 无法在标定最小/最大步数内分段，请调整配方' % port)
            continue
        chunks = [base + (i < extra) for i in range(count)]
        total += len(chunks)
        est[port] = steps * rate
        rows.append(dict(reference=copy.deepcopy(c), stock_snapshot=copy.deepcopy(s), requested_mass_g=mass, estimated_mass_g=steps * rate, total_steps=steps, actual_mass_g=None, source=port, sample=bottle, chunks=chunks, parameters=[dict(source=port, sample=bottle, aspirate=pulse(k, c['aspirate_rpm']), chase=pulse(c['chase_steps'], c['chase_rpm'])) for k in chunks], completed_plugs=0, completed=False))
    if total > 60:
        issues.append('单瓶最多60段')
    mixing = r.get('mixing') or dict(steps=20, rpm=5, timeout_s=10)
    mixing = pulse(mixing['steps'], mixing['rpm'], mixing.get('timeout_s'))
    from controller import pulse_checked
    from tube_workflows import AIR
    plan = dict(sample=bottle, items=rows, total_plugs=total, completed_plugs=0, total_commands=total * 2 + 2, completed_commands=0, current=None, mix_after=True, purge_after=True, mixing=mixing, purge=pulse_checked(AIR), mixing_performed=False, purge_performed=False, completed=False, cancelled=False,
                geometry_signatures={str(row['source']): _signature_for(geometry_signature, row['source']) for row in rows}, actual_mass_g=None,uncertainty_note='按本来源样品端质量标定换算；本瓶实际交付、残水与交叉污染仍待实测')
    complete = len(est) == len(masses)
    em = sum(est.values())
    if complete and limit is not None and em > limit + 1e-10:
        issues.append('整数步换算后的估计总质量超过安全上限，请调整目标或重新标定')
    estimated = dict(stock_masses=est, total_mass_g=em if complete else None, peg_fraction=est.get(14, 0) * ss[14]['mass_fraction'] / em if em and complete else None, salt_fraction=est.get(sp, 0) * ss[sp]['mass_fraction'] / em if em and complete else None)
    deviation = dict(peg_fraction=estimated['peg_fraction']-p if complete and em else None,
                     salt_fraction=estimated['salt_fraction']-q if complete and em else None,
                     tolerance_fraction=tolerance)
    if complete and em and max(abs(deviation['peg_fraction']), abs(deviation['salt_fraction'])) > tolerance + 1e-12:
        issues.append('整数步换算后的组成偏差超过项目误差限，请调整总质量、目标配方或标定方法')
    result = dict(target=dict(safe_mass_limit_g=limit, mixing=copy.deepcopy(mixing), salt_port=sp, peg_fraction=p, salt_fraction=q, total_mass_g=m, temperature_c=r.get('temperature_c', 25), composition_tolerance_fraction=tolerance, allow_trial_calibration=allow_trial), stock_masses=masses, executable=not issues, issues=issues, warnings=warnings, estimated=estimated, deviation=deviation, plan=plan, execution_key=execution_key(plan))
    result['composition'] = composition_record(dict(recipe=result, bottle=bottle), dict(stocks=stocks, composition_tolerance_fraction=tolerance))
    return result

def suggest_points(samples, request):
    r = request
    port = integer(r.get('salt_port', 13), 10, 13)
    concentration = num(r.get('salt_stock_fraction', {10: 0.3, 11: 0.4, 12: 0.15, 13: 0.4}[port]), 1e-06, 1)
    peg_concentration = num(r.get('peg_stock_fraction', 0.4), 1e-06, 1)
    step = num(r.get('step', 0.02), 0.001, 1)
    count = integer(r.get('count', 5), 1, 5)
    method = r.get('method', 'active')
    if method not in ('active', 'uniform'):
        raise ValueError('未知选点方法')

    def bounds(key):
        v = r.get(key + '_range', [r.get(key + '_min', 0), r.get(key + '_max', 0.4 if key == 'peg' else concentration)])
        if not isinstance(v, (list, tuple)) or len(v) != 2 or num(v[0], 0, 1) > num(v[1], 0, 1):
            raise ValueError('范围无效')
        return (num(v[0], 0, 1), num(v[1], 0, 1))
    pl, ph = bounds('peg')
    sl, sh = bounds('salt')
    if (int((ph - pl) / step) + 1) * (int((sh - sl) / step) + 1) > 10000:
        raise ValueError('候选网格过大')
    for sample in samples:
        num(sample['peg_fraction'], 0, 1)
        num(sample['salt_fraction'], 0, 1)
        if sample.get('label') not in (None, 'single', 'two_phase', 'uncertain'):
            raise ValueError('标签无效')
    conflicts = {(s['peg_fraction'], s['salt_fraction']) for s in samples if len({t['label'] for t in samples if t['peg_fraction'] == s['peg_fraction'] and t['salt_fraction'] == s['salt_fraction'] and t.get('label') in ('single','two_phase')}) > 1}
    seen = {(round(s['peg_fraction'], 9), round(s['salt_fraction'], 9)) for s in samples}
    train = [s for s in samples if s.get('label') in ('single', 'two_phase') and (s['peg_fraction'], s['salt_fraction']) not in conflicts]
    classes = {s['label'] for s in train}
    grid = []
    for i in range(int((ph - pl) / step + 1e-08) + 1):
        for j in range(int((sh - sl) / step + 1e-08) + 1):
            p = round(pl + i * step, 9)
            q = round(sl + j * step, 9)
            if p / peg_concentration + q / concentration > 1 + 1e-09:
                continue
            weights = [math.exp(-((p - s['peg_fraction']) ** 2 + (q - s['salt_fraction']) ** 2) / 0.08 ** 2) for s in train]
            den = sum(weights)
            prob = sum((w for w, s in zip(weights, train) if s['label'] == 'two_phase')) / den if den else 0.5
            # Compatibility alias `probability` is a score, never a calibrated probability.
            grid.append(dict(peg_fraction=p, salt_fraction=q, classification_score=prob if len(classes) == 2 else None, probability=prob if len(classes) == 2 else None, uncertainty=1 - abs(2 * prob - 1) if len(classes) == 2 else 1))
    suggestions = []
    anchors = [(s['peg_fraction'], s['salt_fraction']) for s in samples]
    pool = [g for g in grid if (g['peg_fraction'], g['salt_fraction']) not in seen]
    while pool and len(suggestions) < count:

        def score(g):
            distance = min(((g['peg_fraction'] - a) ** 2 + (g['salt_fraction'] - b) ** 2 for a, b in anchors), default=1)
            return distance * (0.25 + g['uncertainty'] if method == 'active' and len(classes) == 2 else 1)
        g = max(pool, key=score)
        pool.remove(g)
        suggestions.append(dict(g, selection_score=score(g), reason='uncertainty_and_coverage' if len(classes) == 2 and method == 'active' else 'uniform_coverage'))
        anchors.append((g['peg_fraction'], g['salt_fraction']))
    return dict(training_ids=[s['id'] for s in train], model=dict(kind='rbf_distance_weighted', bandwidth=0.08, heuristic=True, score_kind='heuristic_classification_score_not_calibrated_probability', two_class_boundary=len(classes) == 2), grid=grid, suggestions=suggestions, conflicts=[dict(peg_fraction=p, salt_fraction=q) for p,q in sorted(conflicts)], summary=dict(training_count=len(train), conflict_count=len(conflicts), candidate_count=len(grid), method=method))
