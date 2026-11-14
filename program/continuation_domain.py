"""Conservative, bounded same-bottle additions. All masses are estimates in g.

The analytic problem finds a minimum *exact* final composition. The bounded
integer search then checks nearby executable points against the configured
absolute composition tolerance; it does not claim global integer optimality.
"""
import copy
import hashlib
import itertools
import json
import math

import experiment_domain as domain

EPS = 1e-10
MAX_SEGMENTS = 60
NEIGHBOR_RADIUS = 2


def inventory_key(inventory):
    """Freeze all supplied provenance as well as the material/revision values."""
    return hashlib.sha256(json.dumps(inventory, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _inventory(inventory, salt_port):
    if not isinstance(inventory, dict) or inventory.get('known') is not True:
        raise ValueError('瓶内物料未知；请处理并确认换空瓶，不能假定为空瓶续加')
    values = {key: domain.num(inventory.get(key), 0, 10000) for key in
              ('total_mass_g', 'peg_mass_g', 'salt_mass_g', 'water_mass_g')}
    domain.integer(inventory.get('revision'), 0, 1000000000)
    if abs(sum(values[k] for k in ('peg_mass_g','salt_mass_g','water_mass_g'))-values['total_mass_g']) > max(1e-8,values['total_mass_g']*1e-8):
        raise ValueError('库存组分质量之和与总质量不符')
    empty_neutral = inventory.get('salt_port') is None and all(value==0 for value in values.values())
    if not empty_neutral and domain.integer(inventory.get('salt_port'), 10, 13) != salt_port:
        raise ValueError('同瓶补加不允许跨盐体系')
    if values['total_mass_g'] > EPS and inventory.get('calibration_valid') is not True:
        raise ValueError('父样品的组成或标定依据无效，不能自动定量续加')
    return values


def _stock_map(stocks, salt_port):
    selected = {}
    for port in (14, salt_port, 15):
        found = next((s for s in stocks if s.get('port') == port), None)
        if found is None:
            raise ValueError('缺少来源%s的母液配置' % port)
        selected[port] = domain.validate_stock(found)
    return selected


def theoretical_addition(request, inventory, stocks):
    """Cheap reachability filter, before a candidate is scored or compiled.

    `total_mass_g` only sets the initial empty-bottle seed (default 1 g).
    A nonempty bottle uses the smallest reachable final mass unless the caller
    explicitly supplies `final_mass_g`. Physical unreachability returns issues;
    malformed requested fractions and limits raise ValueError.
    """
    salt_port = domain.integer(request['salt_port'], 10, 13)
    peg, salt = domain._coordinate_pair(request)
    limit = request.get('safe_mass_limit_g')
    limit = domain.num(limit, 1e-6, 10) if limit is not None else None
    budget = domain.num(request.get('max_added_mass_g', 10000), 0, 10000)
    tolerance = domain.num(request.get('composition_tolerance_fraction', .01), 0, 1)
    target = dict(salt_port=salt_port, peg_fraction=peg, salt_fraction=salt,
                  temperature_c=request.get('temperature_c', 25),
                  safe_mass_limit_g=limit, composition_tolerance_fraction=tolerance,
                  total_mass_g=None, allow_trial_calibration=request.get('allow_trial_calibration',True))
    result = dict(reachable=False, executable=False, issues=[], target=target,
                  initial_inventory=copy.deepcopy(inventory), inventory_key=inventory_key(inventory),
                  final_mass_g=None, theoretical_added_mass_g=None, added_mass_g=None,
                  theoretical_additions_g={}, stock_masses={})
    try:
        material = _inventory(inventory, salt_port)
        ss = _stock_map(stocks, salt_port)
    except (TypeError, ValueError, KeyError) as exc:
        result['issues'].append(str(exc))
        return result
    if limit is None:
        result['issues'].append('需要明确的安全最终总质量上限')
        return result
    mass, pg, sg = (material[k] for k in ('total_mass_g','peg_mass_g','salt_mass_g'))
    cp, cs = ss[14]['mass_fraction'], ss[salt_port]['mass_fraction']
    denominator = 1-peg/cp-salt/cs
    remainder = mass-pg/cp-sg/cs
    lower = mass
    upper = min(limit, mass+budget)
    if (peg == 0 and pg > EPS) or (salt == 0 and sg > EPS):
        result['issues'].append('已有溶质不能仅靠有限补加降为零质量分数')
        return result
    if peg > 0:
        lower = max(lower, pg/peg)
    if salt > 0:
        lower = max(lower, sg/salt)
    if denominator > EPS:
        lower = max(lower, remainder/denominator)
    elif denominator < -EPS:
        upper = min(upper, remainder/denominator)
    elif remainder > EPS:
        result['issues'].append('该目标需要负补水或移除瓶内物料，不能只靠补加达到')
        return result
    if mass <= EPS:
        lower = max(lower, domain.num(request.get('seed_mass_g',request.get('total_mass_g',1)),1e-6,10))
    final = domain.num(request['final_mass_g'],1e-6,10) if 'final_mass_g' in request else lower
    if final < lower-EPS:
        result['issues'].append('指定最终质量会要求负补量；请增大最终质量')
        return result
    if final > upper+EPS:
        result['issues'].append('理论最终质量超出安全容量或剩余补加质量预算')
        return result
    additions = {14:(peg*final-pg)/cp, salt_port:(salt*final-sg)/cs,
                 15:denominator*final-remainder}
    if any(v < -EPS for v in additions.values()):
        result['issues'].append('目标需要负补量，不能执行抽走物料来代替补加')
        return result
    additions = {port:0. if abs(value)<EPS else value for port,value in additions.items()}
    added = sum(additions.values())
    if added <= EPS:
        result['issues'].append('目标与当前库存相同，没有补加动作；不会重复混合或建新实验点')
        return result
    target['total_mass_g'] = final
    result.update(reachable=True, executable=True, final_mass_g=final,
                  theoretical_added_mass_g=added, added_mass_g=added,
                  theoretical_additions_g=additions, stock_masses=copy.deepcopy(additions),
                  max_final_mass_g=upper)
    return result


def _calibration(source, stock, bottle, request, calibrations, signatures):
    compatible = [c for c in calibrations if c.get('source')==source and
                  c.get('stock_version')==stock.get('version') and
                  c.get('stock_batch','')==stock.get('batch','') and
                  c.get('temperature_c')==request.get('temperature_c',25)==stock['temperature_c'] and
                  c.get('geometry_signature')==domain._signature_for(signatures,source) and
                  bottle in c.get('sample_bottles',[1])]
    if not compatible:
        return None, '来源%s缺少适用于当前瓶位、批次、温度及液路的标定' % source
    ref = compatible[-1]
    if ref.get('status','trial') not in ('trial','validated'):
        return None, '来源%s标定已失效' % source
    if ref.get('status','trial') != 'validated' and request.get('allow_trial_calibration',True) is not True:
        return None, '来源%s只有试用标定，当前项目未允许试用配样' % source
    rate = domain.num(ref['mass_per_step_g'], 1e-12)
    minimum = domain.integer(ref.get('min_steps',1),1,20000)
    maximum = min(domain.integer(ref.get('max_steps',ref.get('max_segment_steps',ref['aspirate_steps'])),minimum,20000),
                  domain.integer(ref.get('max_segment_steps',ref['aspirate_steps']),1,20000))
    if stock.get('density_g_ml'):
        maximum = min(maximum,math.floor(.8*stock['density_g_ml']/rate))
    maximum = min(maximum,math.floor(110*ref['aspirate_rpm']/.36))
    if maximum < minimum:
        return None, '来源%s的标定步数范围无法满足有限动作或分段上限' % source
    chase = domain.pulse(ref['chase_steps'],ref['chase_rpm'])
    return dict(ref=ref, rate=rate, minimum=minimum, maximum=maximum, chase=chase), None


def _chunks(steps, calibration):
    if steps == 0:
        return []
    if calibration is None or steps < calibration['minimum']:
        return None
    count = math.ceil(steps/calibration['maximum'])
    if count > MAX_SEGMENTS:
        return None
    base, extra = divmod(steps,count)
    if base < calibration['minimum']:
        return None
    return [base+(i<extra) for i in range(count)]


def _neighbor_steps(mass, calibration):
    if mass <= EPS:
        return [0]
    if calibration is None:
        return []
    ideal = mass/calibration['rate']
    low = math.floor(ideal)
    # Include a range boundary for very small additions, never proportionally
    # extrapolate a command below the calibration's declared minimum.
    values = {max(0,low+delta) for delta in range(-NEIGHBOR_RADIUS,NEIGHBOR_RADIUS+2)}
    values.add(calibration['minimum'])
    return [n for n in sorted(values) if _chunks(n,calibration) is not None]


def _motion_seconds(pulse):
    return (.36*pulse['steps'] if 'steps' in pulse else 60*pulse['turns'])/pulse['rpm']+2


def _empty_result(theory, bottle, request, signatures):
    from controller import pulse_checked
    from tube_workflows import AIR
    mixing = request.get('mixing') or dict(steps=20,rpm=5,timeout_s=10)
    mixing = domain.pulse(mixing['steps'],mixing['rpm'],mixing.get('timeout_s'))
    plan = dict(sample=bottle,items=[],total_plugs=0,completed_plugs=0,total_commands=0,
                completed_commands=0,current=None,mix_after=True,purge_after=True,
                mixing=mixing,purge=pulse_checked(AIR),mixing_performed=False,
                purge_performed=False,completed=False,cancelled=False,actual_mass_g=None,
                geometry_signatures={},kind='continuation',inventory_key=theory['inventory_key'])
    result = dict(theory,kind='continuation',executable=False,warnings=[],plan=plan,
                  estimated=dict(total_mass_g=None,peg_mass_g=None,salt_mass_g=None,water_mass_g=None,
                                 peg_fraction=None,salt_fraction=None,stock_masses={}),
                  added_mass_g=None,estimated_duration_s=None,
                  duration_basis='engineering_estimate_0.36_s_per_step_at_1rpm_plus_2s_each_command',
                  search=dict(method='bounded_neighbor_integer_search',radius=NEIGHBOR_RADIUS,
                              global_integer_optimum=False,evaluated=0))
    result['target'] = dict(theory['target'],mixing=copy.deepcopy(mixing))
    return result


def preview_addition(request, inventory, stocks, calibrations, geometry_signature):
    theory = theoretical_addition(request,inventory,stocks)
    bottle = domain.integer(request.get('sample',1),1,5)
    if not isinstance(request.get('allow_trial_calibration',True),bool):
        raise ValueError('试用标定选项须为布尔值')
    remaining_time = domain.num(request.get('remaining_time_s',1000000000),0)
    result = _empty_result(theory,bottle,request,geometry_signature)

    def finish():
        result['execution_key'] = domain.execution_key(result['plan'])
        result['composition'] = domain.composition_record(dict(recipe=result,bottle=bottle),
            dict(stocks=stocks,composition_tolerance_fraction=result['target']['composition_tolerance_fraction']))
        result['deviation'] = copy.deepcopy(result['composition']['deviation'])
        return result

    if not theory['reachable']:
        return finish()
    salt_port = theory['target']['salt_port']
    ss = _stock_map(stocks,salt_port)
    ports = (14,salt_port,15)
    references = {}
    for port in ports:
        try:
            references[port], issue = _calibration(port,ss[port],bottle,request,calibrations,geometry_signature)
        except (ValueError,KeyError,TypeError) as exc:
            references[port], issue = None, '来源%s标定参数不可执行：%s' % (port,exc)
        if references[port] is None and theory['stock_masses'][port]>EPS:
            result['issues'].append(issue)
    if result['issues']:
        return finish()
    mass = inventory['total_mass_g']
    pg, sg = inventory['peg_mass_g'], inventory['salt_mass_g']
    peg, salt = request['peg_fraction'], request['salt_fraction']
    cp, cs = ss[14]['mass_fraction'], ss[salt_port]['mass_fraction']
    d, k = 1-peg/cp-salt/cs, mass-pg/cp-sg/cs
    final_masses = {theory['final_mass_g']}
    # At most four analytic seeds: the exact minimum and the first final mass
    # at which each source can receive its minimum validated nonzero dose.
    if 'final_mass_g' not in request:
        for port,slope,intercept in ((14,peg/cp,-pg/cp),(salt_port,salt/cs,-sg/cs),(15,d,-k)):
            ref = references[port]
            if ref and slope>EPS:
                threshold=(ref['minimum']*ref['rate']-intercept)/slope
                if theory['final_mass_g']<threshold<=theory['max_final_mass_g']+EPS:
                    final_masses.add(threshold)
    constant_time = _motion_seconds(result['plan']['mixing'])+_motion_seconds(result['plan']['purge'])
    tolerance = result['target']['composition_tolerance_fraction']
    best = None
    seen = set()
    rejected_time = rejected_capacity = False
    for final in sorted(final_masses):
        ideal = {14:(peg*final-pg)/cp,salt_port:(salt*final-sg)/cs,15:d*final-k}
        if any(v < -EPS for v in ideal.values()):
            continue
        for chosen in itertools.product(*[_neighbor_steps(ideal[p],references[p]) for p in ports]):
            if chosen in seen or not any(chosen):
                continue
            seen.add(chosen)
            result['search']['evaluated'] += 1
            masses = {p:n*references[p]['rate'] if n else 0. for p,n in zip(ports,chosen)}
            added = sum(masses.values())
            total = mass+added
            if mass<=EPS and total<theory['final_mass_g']-EPS:
                # An empty-bottle seed is an explicit minimum sample amount;
                # composition tolerance must not silently shrink that seed.
                continue
            if total>theory['max_final_mass_g']+EPS:
                rejected_capacity=True
                continue
            peg_mass, salt_mass = pg+masses[14]*cp, sg+masses[salt_port]*cs
            adopted_peg, adopted_salt = peg_mass/total,salt_mass/total
            error = max(abs(adopted_peg-peg),abs(adopted_salt-salt))
            if error>tolerance+1e-12:
                continue
            chunks = {p:_chunks(n,references[p]) for p,n in zip(ports,chosen)}
            if sum(len(v) for v in chunks.values())>MAX_SEGMENTS:
                continue
            duration = constant_time
            for port,n in zip(ports,chosen):
                if n:
                    ref=references[port]
                    duration += .36*n/ref['ref']['aspirate_rpm']+len(chunks[port])*(2+_motion_seconds(ref['chase']))
            if duration>remaining_time+EPS:
                rejected_time=True
                continue
            rank=(added,duration,error,chosen)
            if best is None or rank<best[0]:
                best=(rank,masses,chunks,dict(stock_masses=masses,total_mass_g=total,
                     peg_mass_g=peg_mass,salt_mass_g=salt_mass,water_mass_g=total-peg_mass-salt_mass,
                     peg_fraction=adopted_peg,salt_fraction=adopted_salt))
    if best is None:
        result['issues'].append('有界整数步搜索内无可执行补加：请调整目标、剂量范围或组成误差限')
        if rejected_capacity:result['issues'].append('量化后的最终质量超出容量或剩余补加质量预算')
        if rejected_time:result['issues'].append('预计补加动作时间超过剩余墙钟时间预算')
        return finish()
    rank,masses,chunks,estimated = best
    rows = []
    for port in ports:
        if not chunks[port]:
            continue
        ref = references[port]['ref']
        parameters = [dict(source=port,sample=bottle,
                           aspirate=domain.pulse(n,ref['aspirate_rpm']),
                           chase=copy.deepcopy(references[port]['chase'])) for n in chunks[port]]
        rows.append(dict(source=port,sample=bottle,reference=copy.deepcopy(ref),
                         stock_snapshot=copy.deepcopy(ss[port]),chunks=chunks[port],parameters=parameters,
                         requested_mass_g=theory['stock_masses'][port],estimated_mass_g=masses[port],
                         total_steps=sum(chunks[port]),actual_mass_g=None,completed=False,completed_plugs=0))
        if ref.get('status','trial')!='validated':
            result['warnings'].append('来源%s使用试用比例估计；本点不可用于训练' % port)
    total_plugs = sum(len(row['chunks']) for row in rows)
    result['plan'].update(items=rows,total_plugs=total_plugs,total_commands=2*total_plugs+2,
        geometry_signatures={str(row['source']):domain._signature_for(geometry_signature,row['source']) for row in rows})
    result.update(executable=True,estimated=estimated,added_mass_g=rank[0],estimated_duration_s=rank[1],
                  selected_additions_g=masses)
    return finish()
