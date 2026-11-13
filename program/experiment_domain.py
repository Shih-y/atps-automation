"""Pure mass-domain planning. Estimates are not measured deliveries."""
import math, statistics, uuid, copy
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
    groups = [num(v, 1e-06) for v in groups]
    if not groups:
        raise ValueError('缺少原始称量')
    doses = integer(d['doses_per_group'], 1, 100000)
    if doses != int(doses):
        raise ValueError('剂数须整数')
    a = pulse(d['aspirate_steps'], d['aspirate_rpm'])
    c = pulse(d['chase_steps'], d['chase_rpm'])
    cap = integer(d.get('max_segment_steps', a['steps']), 1, a['steps'])
    return dict(d, id=uuid.uuid4().hex, created_at=timestamp(), groups_g=groups, mass_per_step_g=statistics.mean(groups) / doses / a['steps'], group_rsd=statistics.stdev(groups) / statistics.mean(groups) if len(groups) > 1 else None, stock_version=s['version'], stock_batch=s['batch'], temperature_c=s['temperature_c'], geometry_signature=geometry_signature, max_segment_steps=cap, aspirate_steps=a['steps'], aspirate_rpm=a['rpm'], chase_steps=c['steps'], chase_rpm=c['rpm'], raw_evidence=copy.deepcopy(data))

def preview_recipe(request, stocks, calibrations, geometry_signature):
    r = request
    sp = integer(r['salt_port'], 10, 13)
    ss = {s['port']: validate_stock(s) for s in stocks}
    m = num(r.get('total_mass_g', 1), 1e-06, 10)
    p = num(r['peg_fraction'], 0, 1)
    q = num(r['salt_fraction'], 0, 1)
    masses = {14: m * p / ss[14]['mass_fraction'], sp: m * q / ss[sp]['mass_fraction']}
    water = m - sum(masses.values())
    if water < -1e-10:
        raise ValueError('补水质量为负')
    masses[15] = max(0, water)
    issues = []
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
        valid = [c for c in calibrations if c['source'] == port and c['stock_version'] == s['version'] and (c['geometry_signature'] == geometry_signature) and (c.get('stock_batch', '') == s.get('batch', '')) and (c.get('temperature_c') == r.get('temperature_c', 25) == s['temperature_c'])]
        if not valid:
            issues.append('来源 %s 缺少兼容标定' % port)
            continue
        c = valid[-1]
        rate = num(c['mass_per_step_g'], 1e-12)
        steps = math.floor(mass / rate + 0.5)
        cap = min(c['max_segment_steps'], c['aspirate_steps'])
        if s.get('density_g_ml'):
            cap = min(cap, math.floor(0.8 * s['density_g_ml'] / rate))
        if steps < 1 or cap < 1:
            issues.append('来源量不足一步或分段上限不足')
            continue
        chunks = []
        remaining = steps
        while remaining and len(chunks) <= 60:
            chunk = min(cap, remaining)
            chunks.append(chunk)
            remaining -= chunk
        total += len(chunks)
        est[port] = steps * rate
        rows.append(dict(reference=copy.deepcopy(c), requested_mass_g=mass, estimated_mass_g=steps * rate, total_steps=steps, actual_mass_g=None, source=port, sample=1, chunks=chunks, parameters=[dict(source=port, sample=1, aspirate=pulse(k, c['aspirate_rpm']), chase=pulse(c['chase_steps'], c['chase_rpm'])) for k in chunks], completed_plugs=0, completed=False))
    if total > 60:
        issues.append('单瓶最多60段')
    mixing = r.get('mixing') or dict(steps=20, rpm=5, timeout_s=10)
    mixing = pulse(mixing['steps'], mixing['rpm'], mixing.get('timeout_s'))
    from controller import pulse_checked
    from tube_workflows import AIR
    plan = dict(sample=1, items=rows, total_plugs=total, completed_plugs=0, total_commands=total * 2 + 2, completed_commands=0, current=None, mix_after=True, purge_after=True, mixing=mixing, purge=pulse_checked(AIR), mixing_performed=False, purge_performed=False, completed=False, cancelled=False,
                actual_mass_g=None,uncertainty_note='按本来源样品端质量标定换算；本瓶实际交付、残水与交叉污染仍待实测')
    complete = len(est) == len(masses)
    em = sum(est.values())
    if complete and limit is not None and em > limit + 1e-10:
        issues.append('整数步换算后的估计总质量超过安全上限，请调整目标或重新标定')
    estimated = dict(stock_masses=est, total_mass_g=em if complete else None, peg_fraction=est.get(14, 0) * ss[14]['mass_fraction'] / em if em and complete else None, salt_fraction=est.get(sp, 0) * ss[sp]['mass_fraction'] / em if em and complete else None)
    return dict(target=dict(safe_mass_limit_g=limit, mixing=copy.deepcopy(mixing), salt_port=sp, peg_fraction=p, salt_fraction=q, total_mass_g=m, temperature_c=r.get('temperature_c', 25)), stock_masses=masses, executable=not issues, issues=issues, estimated=estimated, plan=plan)

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
            grid.append(dict(peg_fraction=p, salt_fraction=q, probability=prob if len(classes) == 2 else None, uncertainty=1 - abs(2 * prob - 1) if len(classes) == 2 else 1))
    suggestions = []
    anchors = [(s['peg_fraction'], s['salt_fraction']) for s in samples]
    pool = [g for g in grid if (g['peg_fraction'], g['salt_fraction']) not in seen]
    while pool and len(suggestions) < count:

        def score(g):
            distance = min(((g['peg_fraction'] - a) ** 2 + (g['salt_fraction'] - b) ** 2 for a, b in anchors), default=1)
            return distance * (0.25 + g['uncertainty'] if method == 'active' and len(classes) == 2 else 1)
        g = max(pool, key=score)
        pool.remove(g)
        suggestions.append(dict(g, reason='uncertainty_and_coverage' if len(classes) == 2 and method == 'active' else 'uniform_coverage'))
        anchors.append((g['peg_fraction'], g['salt_fraction']))
    return dict(training_ids=[s['id'] for s in train], model=dict(kind='rbf_distance_weighted', bandwidth=0.08, heuristic=True, two_class_boundary=len(classes) == 2), grid=grid, suggestions=suggestions, summary=dict(training_count=len(train), candidate_count=len(grid), method=method))
