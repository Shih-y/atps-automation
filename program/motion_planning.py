"""Engineering starting points, never a substitute for endpoint dose calibration."""
import math
import statistics

def _number(value,low,high,name,integer=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high or (integer and type(value) is not int):
        raise ValueError(f'{name}应在{low}至{high}之间'+('，并且为整数' if integer else ''))
    return value

def motion_plan(config):
    g=config['geometry']
    volume=lambda diameter,length:math.pi*(diameter/2)**2*length*10/1000
    common=sum(volume(3.2,g[k]) for k in ('A_cm','B_cm','C_cm'))
    water_branch=volume(1.6,g['source_cm']['15']); valve=.0198
    # The fixed current geometry yields 20/16/15 turns. Larger future paths
    # receive proportional finite budgets, with a little rounding margin.
    first=math.ceil(2*(common+water_branch+valve)/.9)
    wash=math.ceil(1.95*(common+valve)/.9)
    purge=math.ceil(1.75*(common+volume(1.6,g['air16_cm'])+valve)/.9)
    recipes={'first_flush':{'turns':first,'rpm':50,'timeout_s':40},
             'wash':{'turns':wash,'rpm':50,'timeout_s':35},
             'purge':{'turns':purge,'rpm':60,'timeout_s':30}}
    for p in recipes.values():
        seconds=60*p['turns']/p['rpm'];p['timeout_s']=max(p['timeout_s'],math.ceil(seconds*1.25+10))
    return {'recipes':recipes,'times_s':{k:60*p['turns']/p['rpm'] for k,p in recipes.items()},
            'volumes_ml':{'A':volume(3.2,g['A_cm']),'common':common,'water_branch':water_branch,
                          'sample':volume(1.6,50),'first_water_path':common+water_branch+valve},
            'reference_ml_per_turn':.9,'reference_basis':'厂家YZ1515X-3、16#、50rpm水参考45mL/min，仅作规划',
            'steps_per_turn_estimate':200,'steps_basis':'本机5/10rpm剩余步数日志约200步/圈；高转速未证明',
            'physical_high_speed_verified':False,'native_turns_physical_verified':False}

def analyze_bench_readings(payload):
    method=payload.get('method')
    if method not in ('bulk_turns','batch_water'):raise ValueError('选择整圈收集或五剂成组清水称量')
    values=payload.get('readings_g')
    if not isinstance(values,list) or not 1<=len(values)<=100:raise ValueError('至少填1个净增质量，最多100个')
    for value in values:_number(value,0,1000,'净增质量g')
    density=_number(payload.get('density_g_ml',1.),.95,1.05,'清水密度')
    resolution=_number(payload.get('balance_resolution_g',.01),.00001,1,'天平分度g')
    mean=statistics.mean(values);sd=statistics.stdev(values) if len(values)>1 else None
    report={'method':method,'groups':len(values),'readings_g':values,'mean_group_g':mean,
            'group_rsd_percent':100*sd/mean if sd is not None and mean else None,
            'density_g_ml':density,'balance_resolution_g':resolution,
            'individual_repeatability_verified':False,'formal_calibration_validated':False,
            'residual_water_ml':None,'carryover_percent':None,
            'scope':'仅清水、当前动作和管路的初步结果；组间离散程度不是单剂重复性。',
            'notes':['密度1.000为近似输入，非实测密度。','0.01g天平采用5剂一组，不能证明每剂0.20mL的精度。']}
    if method=='bulk_turns':
        turns=_number(payload.get('turns',5),1,100,'收集圈数',True)
        report.update(turns=turns,mean_ml_per_turn=mean/density/turns,
                      resolution_bound_ml_per_turn=2*resolution/density/turns)
    else:
        count=_number(payload.get('doses_per_group',5),1,5,'每组剂数',True)
        steps=_number(payload.get('aspirate_steps'),1,100,'当前吸取步数',True)
        dose=mean/density/count
        suggestion=None
        if .1<=dose<=.3:
            proposed=round(steps*.2/dose)
            if 1<=proposed<=100:suggestion=proposed
        report.update(doses_per_group=count,mean_per_dose_ml=dose,
                      suggested_aspirate_steps=suggestion,current_aspirate_steps=steps,
                      deviation_percent=100*(dose-.2)/.2,
                      resolution_bound_per_dose_ml=2*resolution/density/count,
                      mean_tuning_within_5_percent=abs(dose-.2)<=.01,
                      recommendation='只将建议用于下一组试验；追送步数保持不变。' if suggestion else '出液量异常或偏差过大：先查是否送完、漏气、称重和管口，不自动加大吸取。')
    return report
