"""Offline screening of independent bench data; NEVER unlocks a hardware profile.

Run: python calibration_report.py measurements.csv --output screening.json
stock_equiv_ml must come from an independent validated compositional assay or
demonstrably undiluted gravimetry. Mixed discharge mass is not stock volume.
"""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path

FIELDS = ['profile_key','kind','stock_equiv_ml','water_carry_ml','cross_carry_ml',
          'net_mass_g','bulk_density_g_ml','source','sample','condition','temperature_C',
          'aspirate_steps','chase_steps','hold_seconds','assay_method','note']

def number(value, name):
    if value is None or value=='': return None
    if isinstance(value,bool): raise ValueError(name+'不能为布尔值')
    try: n=float(value)
    except (ValueError,TypeError) as e: raise ValueError(name+'必须为数值') from e
    if not math.isfinite(n) or n<0: raise ValueError(name+'必须为非负有限数')
    return n

def summarize(rows, target_ml=.20):
    target_ml=number(target_ml,'target_ml')
    if not target_ml: raise ValueError('目标体积必须大于零')
    groups={}; metadata={}; metadata_complete={}
    for row in rows:
        key=str(row.get('profile_key','')).strip()
        if not key: raise ValueError('每行须有profile_key，且首次/连续加料分别分组')
        kind=row.get('kind','delivery')
        if kind not in ('delivery','rinse_blank','cross_blank'): raise ValueError('kind无效')
        md=tuple(str(row.get(k,'')).strip() for k in ('source','sample','condition'))
        if md[0] and md[0] not in [str(i) for i in range(11,16)]: raise ValueError('source须为11-15')
        if md[1] and md[1] not in [str(i) for i in range(1,7)]: raise ValueError('sample须为1-6')
        if md[2] and md[2] not in ('first','continued'): raise ValueError('condition须为first或continued')
        if key in metadata and metadata[key]!=md: raise ValueError('同一profile_key不能混合来源、样品口或首次/连续状态：'+key)
        metadata[key]=md
        metadata_complete[key]=metadata_complete.get(key,True) and all(md) and bool(str(row.get('assay_method','')).strip())
        values={k:number(row.get(k),k) for k in FIELDS[2:7]}
        groups.setdefault(key,[]).append((kind,values))
    profiles={}
    for key,rows_ in groups.items():
        deliveries=[v for k,v in rows_ if k=='delivery']
        eq=[v['stock_equiv_ml'] for v in deliveries if v['stock_equiv_ml'] is not None]
        water=[v['water_carry_ml'] for _,v in rows_ if v['water_carry_ml'] is not None]
        cross=[v['cross_carry_ml'] for _,v in rows_ if v['cross_carry_ml'] is not None]
        mean=statistics.mean(eq) if eq else None
        bias=100*(mean/target_ml-1) if mean is not None else None
        rsd=100*statistics.stdev(eq)/mean if len(eq)>1 and mean else None
        watermax=max(water) if water else None
        crossmax=max(cross) if cross else None
        complete=len(eq)==len(deliveries) and len(eq)>=10
        passed=bool(complete and metadata_complete[key] and abs(bias)<=5+1e-10 and rsd is not None and rsd<=5+1e-10
                    and len(water)>=3 and watermax<=.005
                    and len(cross)>=3 and crossmax<=target_ml*.01)
        profiles[key]={'n_delivery':len(deliveries),'n_equivalent':len(eq),
            'mean_stock_equiv_ml':mean,'bias_percent':bias,'rsd_percent':rsd,
            'max_water_carry_ml':watermax,'max_cross_carry_ml':crossmax,
            'n_water':len(water),'n_cross':len(cross),'screening_pass':passed,
            'source_sample_condition':metadata[key],'metadata_complete':bool(metadata_complete[key])}
    return {'strategy':'air_plug_16_air','target_ml':target_ml,
        'hardware_authorized':False,'profiles':profiles,
        'criteria':{'n_delivery_min':10,'abs_bias_max_percent':5,'rsd_max_percent':5,
                    'water_carry_max_ml':.005,'cross_carry_max_ml':target_ml*.01,
                    'blank_repeats_min':3},
        'limitations':['筛选阈值是本方案建议，不是已达到的性能，也不自动授权硬件。',
                      '分组必须区分母液、样品口、首次/连续、等待时间和实测管路配置。',
                      'stock_equiv_ml须独立测定；混合液质量不得直接除以母液密度冒充母液当量。',
                      '未计入测量不确定度、检出限或系统误差；低于检出限不可直接填写零。',
                      '残水与交叉污染需代表最不利条件；结果不能证明鼓泡充分或自动判相可靠。']}

def analyze_input(payload):
    """Translate the beginner form into auditable, separately grouped records.

    Water weighing is intentionally restricted to port 15: dividing a mixed
    stock/rinse effluent mass by stock density cannot identify stock recovery.
    Blank values are independent assay inputs, never inferred from tube size.
    """
    if not isinstance(payload,dict):raise ValueError('校准输入必须为对象')
    mode=payload.get('measurement_mode')
    if mode not in ('stock_equivalent','water_gravimetry'):raise ValueError('选择母液当量或清水称重模式')
    if payload.get('target_ml',.2)!=.2:raise ValueError('当前向导仅验证0.20 mL单次目标')
    source=payload.get('source');sample=payload.get('sample');condition=payload.get('condition')
    if type(source) is not int or source not in range(11,16):raise ValueError('来源口必须为11-15整数')
    if type(sample) is not int or sample not in range(1,7):raise ValueError('样品口必须为1-6整数')
    if condition not in ('first','continued'):raise ValueError('选择首次或连续状态')
    key=str(payload.get('profile_key','')).strip();method=str(payload.get('assay_method','')).strip()
    if not key or len(key)>100:raise ValueError('填写不超过100字符的记录编号')
    if not method or len(method)>2000:raise ValueError('填写实际测量方法、条件和记录位置')
    arrays={}
    for name in ('delivery_values','water_carry_values_ml','cross_carry_values_ml'):
        value=payload.get(name,[])
        if not isinstance(value,list) or len(value)>1000:raise ValueError(name+'须为不超过1000项的数值列表')
        arrays[name]=[number(v,name) for v in value]
        if any(v is None for v in arrays[name]):raise ValueError(name+'不接受空值；缺测请留空整个列表')
    if not arrays['delivery_values']:raise ValueError('至少填写一次真实交付测量值')
    density=None
    if mode=='water_gravimetry':
        if source!=15:raise ValueError('称重直接换算仅用于15口清水；母液混合出液不能直接当作母液当量')
        density=number(payload.get('density_g_ml'),'清水密度g/mL')
        if density is None or not .9<=density<=1.1:raise ValueError('填写实验温度下合理的清水密度g/mL（0.9-1.1）')
    common=dict(profile_key=key,source=source,sample=sample,condition=condition,
                assay_method=method,measurement_mode=mode)
    rows=[]
    for value in arrays['delivery_values']:
        row=dict(common,kind='delivery',stock_equiv_ml=value if density is None else value/density)
        if density is not None:row.update(net_mass_g=value,bulk_density_g_ml=density)
        rows.append(row)
    rows.extend(dict(common,kind='rinse_blank',water_carry_ml=v) for v in arrays['water_carry_values_ml'])
    rows.extend(dict(common,kind='cross_blank',cross_carry_ml=v) for v in arrays['cross_carry_values_ml'])
    report=summarize(rows,.2)
    report['measurement_mode']=mode
    report['limitations'].append('清水称重只验证清水交付；不能把结果移作PEG或其他母液的校准。')
    return {'report':report,'rows':rows}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('csv_file',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--target',type=float,default=.20);a=p.parse_args()
    with a.csv_file.open(encoding='utf-8-sig',newline='') as f:r=summarize(list(csv.DictReader(f)),a.target)
    s=json.dumps(r,ensure_ascii=False,indent=2,allow_nan=False)
    if a.output:a.output.write_text(s,encoding='utf-8')
    else:print(s)

if __name__=='__main__':main()
