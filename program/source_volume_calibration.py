"""Sample-endpoint volume estimates, deliberately separate from mass experiments."""
import copy
import json
import math
import os
import secrets
import statistics
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING
from bottle_routing import sample_port
from capture_store import timestamp
from experiment_domain import default_stocks, plumbing_signature
from stock_density import measured_density_usable


class SourceVolumeMixin:
    def _load_source_volumes(self):
        path=self.directory/'source_volume_calibrations.json'
        self.source_volume_calibrations=json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        if not isinstance(self.source_volume_calibrations,list):raise ValueError('来源体积标定档案格式无效')

    def _volume_stock(self,source):
        # Stock updates replace the list and its records. Capture that published
        # list without acquiring lab.lock while controller.lock is held.
        lab=getattr(self,'lab',None)
        stocks=copy.deepcopy(lab.data['stocks'] if lab is not None else default_stocks())
        stock=next((s for s in stocks if s.get('port')==source),None)
        if stock is None:raise ValueError('当前来源母液记录缺失，不能使用体积标定')
        return stock

    def save_source_volume_calibration(self,payload):
        from controller import number,pulse_checked
        if not isinstance(payload,dict):raise ValueError('体积标定记录须为对象')
        payload=copy.deepcopy(payload)
        with self.lock:
            if self.busy or self._user_stopping or self.pending_plug:raise ValueError('操作、停止或待追送液段期间不能保存体积标定')
            source=number(payload.get('source'),10,15,'来源口',True)
            sample=number(payload.get('sample'),1,6,'收集瓶',True)
            sample_port(self.config,sample)
            doses=number(payload.get('doses'),1,20,'本组剂数',True)
            stock=self._volume_stock(source)
            if 'stock_version' in payload and payload['stock_version']!=stock.get('version'):
                raise ValueError('收集后母液版本已改变，请重新标定')
            temperature=number(payload.get('temperature_c'),0,100,'温度')
            if temperature!=stock.get('temperature_c'):raise ValueError('标定温度与当前母液温度不一致')
            motion={k:pulse_checked(payload.get(k)) for k in ('aspirate','chase')}
            if 'steps' not in motion['aspirate']:raise ValueError('体积标定吸取必须使用有限步数')
            for p in motion.values():
                number(p['rpm'],.1,self.config['pump']['max_rpm'],'配置允许转速')
                duration=(60*p['turns'] if 'turns' in p else .3*p['steps'])/p['rpm']
                if p['timeout_s']<duration+2:raise ValueError('标定动作超时不足')
            collection_id=payload.get('collection_run_id')
            if collection_id is not None:
                run=self.recipe_run or {}
                context=run.get('collection_context',{})
                expected=dict(source=source,sample=sample,doses=doses,stock_snapshot=stock,
                              geometry_signature=plumbing_signature(self.config,source),**motion)
                if (not isinstance(collection_id,str) or collection_id!=run.get('id') or
                        not run.get('completed') or run.get('cancelled') or context!=expected):
                    raise ValueError('收集记录未完成、已改变或与本次母液/动作条件不一致，请重新收集')
            bottles=payload.get('sample_bottles',[sample])
            if not isinstance(bottles,list) or not bottles or len(bottles)>6:raise ValueError('填写适用瓶口范围')
            for b in bottles:
                number(b,1,6,'适用瓶口',True);sample_port(self.config,b)
            if len(set(bottles))!=len(bottles) or sample not in bottles:raise ValueError('瓶口范围须含实际收集瓶且不重复')
            evidence=payload.get('bottle_scope_evidence','')
            if not isinstance(evidence,str) or len(evidence)>2000:raise ValueError('瓶口依据格式无效')
            if len(bottles)>1 and not evidence.strip():raise ValueError('扩展瓶口范围须记录实测依据')
            ml=payload.get('groups_ml');grams=payload.get('groups_g')
            if (ml is None)==(grams is None):raise ValueError('体积读数与质量读数只能填写一项')
            raw=ml if ml is not None else grams
            if not isinstance(raw,list) or not 1<=len(raw)<=100:raise ValueError('填写1—100组实际终点读数')
            readings=[number(v,.000001,200,'实际终点读数') for v in raw]
            if grams is not None:
                density=payload.get('density_g_ml');note=payload.get('density_evidence')
                if not measured_density_usable(stock) or not isinstance(note,str) or not note.strip():
                    raise ValueError('质量转体积须明确提供当前母液批次实测密度和依据')
                number(density,.001,10,'明确提供的密度')
                if density!=stock['density_g_ml']:raise ValueError('提供密度与当前母液实测密度不一致')
                readings=[v/density for v in readings]
            mean=statistics.mean(readings)/doses
            rate=mean/motion['aspirate']['steps']
            if not math.isfinite(rate) or rate<=0 or mean>10:raise ValueError('标定单剂实际体积须在0至10 mL内')
            minimum=number(payload.get('min_steps',motion['aspirate']['steps']),1,20000,'试用步数下限',True)
            maximum=number(payload.get('max_steps',motion['aspirate']['steps']),minimum,20000,'试用步数上限',True)
            if not minimum<=motion['aspirate']['steps']<=maximum:raise ValueError('试用范围须包含实测步数')
            note=payload.get('note','')
            if not isinstance(note,str) or len(note)>4000:raise ValueError('标定说明格式无效')
            ref=dict(id=secrets.token_hex(16),version=1+max((r['version'] for r in self.source_volume_calibrations if r['source']==source),default=0),
                     source=source,sample=sample,doses=doses,created_at=timestamp(),stock_snapshot=stock,
                     stock_version=stock.get('version'),stock_batch=stock.get('batch'),temperature_c=temperature,
                     geometry_signature=plumbing_signature(self.config,source),geometry=copy.deepcopy(self.config['geometry']),
                     sample_bottles=bottles,bottle_scope_evidence=evidence,**motion,groups_ml=readings,
                     raw_evidence=copy.deepcopy(payload),ml_per_step=rate,mean_per_dose_ml=mean,min_steps=minimum,max_steps=maximum,
                     status='trial',range_basis='measured_single_dose_only' if minimum==maximum else 'trial_proportional_estimate_not_validated',
                     volume_verified=False,repeatability_verified=False,n_groups=len(readings),note=note,
                     collection_basis='completed_controller_collection' if collection_id else 'operator_entered_endpoint_reading',
                     uncertainty_note='实际母液样品出口单点比例估算；精度、重复性和缩放范围均未经独立验证')
            if self._volume_stock(source)!=stock:raise ValueError('保存期间母液记录已改变，请重新标定')
            records=self.source_volume_calibrations+[ref]
            path=self.directory/'source_volume_calibrations.json'
            temp=path.with_name(path.name+'.'+secrets.token_hex(8)+'.tmp')
            try:
                with temp.open('x',encoding='utf-8') as f:
                    json.dump(records,f,ensure_ascii=False,allow_nan=False,indent=2);f.flush();os.fsync(f.fileno())
                os.replace(temp,path)
            finally:
                if temp.exists():temp.unlink()
            self.source_volume_calibrations=records
            return dict(ok=True,reference=copy.deepcopy(ref))

    def preview_source_volume(self,payload):
        with self.lock:return dict(ok=True,plan=self._volume_plan(payload))

    def _source_volume_plan(self,source,sample,ml):
        from controller import pulse_checked,number
        refs=[r for r in self.source_volume_calibrations if r['source']==source]
        if not refs:return None
        scoped=[r for r in refs if sample in r['sample_bottles']]
        if not scoped:raise ValueError('本来源标定不适用于此瓶口，请在实际出口重新标定')
        ref=scoped[-1]
        if (ref['stock_snapshot']!=self._volume_stock(source) or
                ref['geometry_signature']!=plumbing_signature(self.config,source)):
            raise ValueError('本来源体积标定已失效：母液批次、身份、温度、几何或运动配置已改变；请重新标定')
        if sample not in ref['sample_bottles']:raise ValueError('本来源标定不适用于此瓶口，请在实际出口重新标定')
        rate=Decimal(str(ref['ml_per_step']))
        total=int((Decimal(str(ml))/rate).to_integral_value(rounding=ROUND_HALF_UP))
        rpm=Decimal(str(ref['aspirate']['rpm']))
        if total<1 or not ref['min_steps']<=total<=ref['max_steps']:
            raise ValueError('单次请求体积超出标定步数试用范围；不再拆段，请记录适用范围或重新实测')
        chunks=[total]
        params=[]
        for chunk in chunks:
            timeout=int((Decimal(chunk)*Decimal('.36')/rpm+10).to_integral_value(rounding=ROUND_CEILING))
            aspirate=dict(ref['aspirate'],steps=chunk,timeout_s=max(ref['aspirate']['timeout_s'],timeout))
            chase=copy.deepcopy(ref['chase'])
            for p in (aspirate,chase):
                pulse_checked(p);number(p['rpm'],.1,self.config['pump']['max_rpm'],'配置允许转速')
            params.append(dict(source=source,sample=sample,aspirate=aspirate,chase=chase))
        return dict(requested_ml=ml,estimated_ml=float(total*rate),total_steps=total,chunks=chunks,
                    source=source,sample=sample,parameters=params,reference=copy.deepcopy(ref),
                    calibration_basis='source_endpoint_trial',uncertainty_note=ref['uncertainty_note'],
                    delivery_mode='single_shot',mixing_performed=False,
                    actual_ml=None,volume_verified=False,completed=False,completed_plugs=0,started_at=timestamp())
