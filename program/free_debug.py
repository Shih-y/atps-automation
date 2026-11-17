"""Explicit unvalidated manual operations; never invent liquid observations."""
import copy
import math
import secrets
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from capture_store import timestamp
from bottle_routing import sample_port, sample_port_map
from tube_workflows import AIR
from experiment_domain import plumbing_signature

class FreeDebugMixin:
    def _stock_calibration_plan(self,payload):
        from controller import number,pulse_checked
        source=number(payload.get('source'),10,15,'标定来源口',True)
        sample=number(payload.get('sample'),1,6,'称重收集瓶',True)
        sample_port(self.config,sample)
        count=number(payload.get('doses',5),1,20,'本组剂数',True)
        parameters={key:pulse_checked(payload.get(key)) for key in ('aspirate','chase')}
        if 'steps' not in parameters['aspirate']:raise ValueError('标定吸取使用步数；追送可使用原生整圈')
        for p in parameters.values():
            number(p['rpm'],.1,self.config['pump']['max_rpm'],'配置允许转速')
            duration=(60*p['turns'] if 'turns' in p else .3*p['steps'])/p['rpm']
            if p['timeout_s']<duration+2:raise ValueError('超时不足以完成本次标定动作')
        purge=pulse_checked(AIR)
        number(purge['rpm'],.1,self.config['pump']['max_rpm'],'配置允许吹扫转速')
        row=dict(source=source,sample=sample,chunks=[parameters['aspirate']['steps']]*count,
                 parameters=[dict(source=source,sample=sample,**copy.deepcopy(parameters)) for _ in range(count)],
                 completed=False,completed_plugs=0,actual_ml=None)
        collection_context=dict(source=source,sample=sample,doses=count,stock_snapshot=self._volume_stock(source),
                                geometry_signature=plumbing_signature(self.config,source),**copy.deepcopy(parameters))
        return dict(sample_port_map=sample_port_map(self.config),id=secrets.token_hex(16),collection_context=collection_context,sample=sample,items=[row],total_plugs=count,completed_plugs=0,
                    total_commands=2*count+1,completed_commands=0,current=None,mix_after=False,purge_after=True,
                    mixing=None,purge=purge,mixing_performed=False,purge_performed=False,
                    completed=False,cancelled=False,started_at=timestamp(),actual_ml=None,volume_verified=False,
                    uncertainty_note='来源样品出口标定收集动作；实际质量或体积读数尚未录入',method='sample_endpoint_collection')

    @property
    def free_debug(self):
        return self.config.get('free_debug',True) is True

    def _debug_parameters(self,payload):
        from controller import number,pulse_checked
        value=payload.get('debug_parameters',{})
        if not isinstance(value,dict): raise ValueError('调试参数须为对象')
        for key,lo,hi in (('source',10,15),('sample',1,6)):
            if key in payload:number(payload[key],lo,hi,key,True)
            if key in value:number(value[key],lo,hi,key,True)
            if key in payload and key in value and payload[key]!=value[key]:
                raise ValueError('顶层与调试参数中的%s不一致' % key)
        p=dict(source=value.get('source',payload.get('source',15)),sample=value.get('sample',payload.get('sample',1)),
               aspirate=value.get('aspirate',dict(steps=45,rpm=5,timeout_s=20)),
               chase=value.get('chase',dict(steps=1000,rpm=15,timeout_s=35)),
               bubble=value.get('bubble',dict(steps=20,rpm=5,timeout_s=10)))
        number(p['source'],10,15,'来源口',True);number(p['sample'],1,6,'样品口',True)
        sample_port(self.config,p['sample'])
        for key in ('aspirate','chase','bubble'):p[key]=pulse_checked(p[key])
        used={'aspirate':['aspirate'],'chase':['chase'],'dose':['aspirate','chase'],
              'bubble':['bubble']}.get(payload.get('action'),['aspirate','chase','bubble'])
        if payload.get('bubble') is True and 'bubble' not in used:used=used+['bubble']
        for key in used:number(p[key]['rpm'],.1,self.config['pump']['max_rpm'],'配置允许转速')
        return p

    def _debug_delivery(self,p,action,bubble=False):
        if action in ('aspirate','dose'):
            self._pulse(p['source'],'aspirate',p['aspirate']);self.common='stock'
        if action in ('chase','dose'):
            self._sample_pulse(p['sample'],'dispense',p['chase']);self.common='wet'
            self.samples[str(p['sample'])].update(condition='unknown',uncertain_delivery=True,
                uncertainty_note='自由调试，交付量与液路状态未验证',last_gas_stop=timestamp(),clear_at=None,settle_s=None)
        if action=='bubble' or bubble:
            self._sample_pulse(p['sample'],'dispense',p['bubble'])
            self.samples[str(p['sample'])].update(last_gas_stop=timestamp(),clear_at=None,settle_s=None)

    def _volume_plan(self,payload):
        from controller import number,pulse_checked
        source=number(payload.get('source'),10,15,'来源口',True)
        sample=number(payload.get('sample'),1,6,'样品口',True)
        sample_port(self.config,sample)
        ml=number(payload.get('volume_ml'),.001,10,'本次体积mL（10 mL瓶，需自行扣除已有液体）')
        source_plan=self._source_volume_plan(source,sample,ml)
        if source_plan is not None:return source_plan
        # Freeze the actual baseline used for the reported PEG observation.
        # Later water edits must not change this factor; a later source endpoint
        # reference takes precedence above and is never multiplied again.
        peg=source==14
        if peg:
            ref=dict(id='peg-operator-2026-11-17',source=14,nominal_ml=1.,reported_actual_ml=.42,
                     baseline_aspirate_steps=300,aspirate_rpm=5,correction_factor=1/.42,
                     old_chase=dict(steps=2105,rpm=30,segments=2),
                     chase=dict(turns=30,rpm=60,timeout_s=46),
                     scope='40% PEG4000；原1 mL指令共300吸取步，实收0.42 mL；单点比例试用',
                     method_changed=True,delivery_reverification_required=True)
            rate=Decimal('.42')/300
            steps=300;ar=5
        else:
            ref=self.water_dose_reference;refp=ref.get('parameters',{})
            mean=number(ref.get('mean_per_dose_ml'),.01,1,'单剂体积参考mL')
            steps=number(refp.get('aspirate_steps'),1,100,'参考吸取步数',True)
            ar=number(refp.get('aspirate_rpm'),.1,100,'参考吸取rpm')
            cr=number(refp.get('chase_rpm'),.1,100,'参考追送rpm')
            cs=number(refp.get('chase_steps'),1,3000,'参考追送步数',True)
            rate=Decimal(str(mean))/steps
        total=int((Decimal(str(ml))/rate).to_integral_value(rounding=ROUND_HALF_UP))
        if total<1:raise ValueError('体积换算不足1步，请增大输入量')
        if peg:chase=copy.deepcopy(ref['chase'])
        else:
            chase_steps=cs+max(0,total-steps)
            chase=dict(steps=chase_steps,rpm=cr,timeout_s=max(35,math.ceil(chase_steps*.36/cr+10)))
        timeout=int((Decimal(total)*Decimal('.36')/Decimal(str(ar))+10).to_integral_value(rounding=ROUND_CEILING))
        p=dict(source=source,sample=sample,aspirate=dict(steps=total,rpm=ar,timeout_s=max(20,timeout)),chase=chase)
        for key in ('aspirate','chase'):
            pulse_checked(p[key]);number(p[key]['rpm'],.1,min(100,self.config['pump']['max_rpm']),'配置允许转速')
        return dict(requested_ml=ml,estimated_ml=float(total*rate),total_steps=total,chunks=[total],
                    calibration_basis='peg_operator_report_trial' if peg else 'legacy_water_unvalidated',
                    uncertainty_note=('PEG按原1 mL实收0.42 mL修正吸取；改为一次吸取及60 rpm/30圈追送后须复测，比例估计不是实测交付量'
                                      if peg else '旧清水参考比例估算，当前来源母液实际交付量未经验证'),
                    delivery_mode='single_shot',mixing_performed=False,
                    source=source,sample=sample,parameters=[p],reference=copy.deepcopy(ref),
                    actual_ml=None,volume_verified=False,completed=False,completed_plugs=0,started_at=timestamp())

    def _waste_rinse_plan(self):
        from controller import number,pulse_checked
        ref=copy.deepcopy(self.bulk_water_reference)
        rate=number(ref.get('report',{}).get('mean_ml_per_turn'),.000001,100,'C端清水参考mL/圈')
        steps=int((Decimal('1.5')/Decimal(str(rate))*200).to_integral_value(rounding=ROUND_HALF_UP))
        rpm=number(self.config['reset']['wash']['rpm'],.1,self.config['pump']['max_rpm'],'清洗rpm')
        pulse=pulse_checked(dict(steps=steps,rpm=rpm,timeout_s=max(15,math.ceil(steps*.36/rpm+10))))
        return dict(source=15,destination='waste',direction='aspirate',requested_ml=1.5,
                    estimated_ml=steps/200*rate,pulse=pulse,reference=ref,actual_ml=None,
                    volume_verified=False,common_after='wet',
                    uncertainty_note='按C端清水参考估算引入量；不保证本次C端回收1.5 mL，不代表整管洗净或排空；残水会影响下一次配液')

    def _recipe_plan(self,payload):
        from controller import number,pulse_checked
        sample=number(payload.get('sample'),1,6,'样品口',True)
        items=payload.get('items')
        if not isinstance(items,list) or not 1<=len(items)<=60 or any(not isinstance(i,dict) for i in items):
            raise ValueError('配方须包含1—60项来源口和体积')
        mix_after=payload.get('mix_after',True);rinse_after=payload.get('rinse_after',True)
        if any(type(v) is not bool for v in (mix_after,rinse_after,payload.get('purge_after',False))):
            raise ValueError('混合和清洗选项须为布尔值')
        mixing=pulse_checked(payload.get('mixing',dict(steps=20,rpm=5,timeout_s=10)))
        if 'steps' not in mixing:raise ValueError('混合须使用有限步数')
        number(mixing['rpm'],.1,min(100,self.config['pump']['max_rpm']),'配置允许混合转速')
        merged={}
        for item in items:
            source=number(item.get('source'),10,15,'来源口',True)
            volume=number(item.get('volume_ml'),.001,10,'来源体积mL')
            merged[source]=merged.get(source,Decimal(0))+Decimal(str(volume))
        rows=[self._volume_plan(dict(source=source,volume_ml=float(volume),sample=sample)) for source,volume in merged.items()]
        rinse=self._waste_rinse_plan() if rinse_after else None
        requested=sum(Decimal(str(r['requested_ml'])) for r in rows)
        if requested>10:raise ValueError('单瓶本次配方总体积不得超过10 mL；请自行扣除瓶内已有液体')
        total_plugs=sum(len(r['chunks']) for r in rows)
        if total_plugs>60:raise ValueError('单瓶配方最多60个液段，请拆为明确的独立操作')
        return dict(sample_port_map=sample_port_map(self.config),sample=sample,items=rows,requested_ml=float(requested),
                    estimated_ml=sum(r['estimated_ml'] for r in rows),actual_ml=None,volume_verified=False,
                    segment_limit_ml=None,delivery_mode='one_pair_per_source',method='manual_single_shot_water_rinse_v1',
                    total_plugs=total_plugs,completed_plugs=0,total_commands=total_plugs*2+int(mix_after)+int(rinse_after),
                    completed_commands=0,current=None,mix_after=mix_after,purge_after=False,
                    mixing=mixing,purge=None,mixing_performed=False,purge_performed=False,
                    rinse_after=rinse_after,rinse=rinse,rinse_performed=False,
                    legacy_purge_request_ignored=payload.get('purge_after',False),
                    uncertainty_note='手动mL比例试用；单次液段及追送条件改变，实际交付与连续加料残水影响须复测',
                    completed=False,cancelled=False,started_at=timestamp())

    def _execute_recipe(self,plan):
        if plan.get('sample_port_map', {str(i):i for i in range(1,7)}) != sample_port_map(self.config):
            raise ValueError('配方瓶口映射/接管配置已改变，请重新编译配方')
        sample_port(self.config,plan['sample'])
        self.recipe_run=plan
        self.log('recipe_started',plan=copy.deepcopy(plan))
        try:
            for item_index,row in enumerate(plan['items']):
                for chunk_index,p in enumerate(row['parameters']):
                    self._status('RECIPE',f"来源{row['source']}→{plan['sample']}号瓶，第{plan['completed_plugs']+1}/{plan['total_plugs']}段")
                    for phase,port,direction in [('aspirate',row['source'],'aspirate'),('chase',plan['sample'],'dispense')]:
                        plan['current']=dict(item_index=item_index,chunk_index=chunk_index,phase=phase)
                        (self._sample_pulse if phase=='chase' else self._pulse)(port,direction,p[phase]);plan['completed_commands']+=1
                        self.common='stock' if phase=='aspirate' else 'wet'
                        self._lab_event('progress',plan=plan)
                    row['completed_plugs']+=1;plan['completed_plugs']+=1
                    self.samples[str(plan['sample'])].update(condition='unknown',uncertain_delivery=True,
                        uncertainty_note=plan.get('uncertainty_note','按清水参考换算的配方，实际母液交付量未验证'),last_gas_stop=timestamp(),clear_at=None,settle_s=None,
                        timing_basis='server_confirmed_finite_command_completion')
                row.update(completed=True,finished_at=timestamp())
            if plan['mix_after']:
                plan['current']=dict(item_index=None,chunk_index=None,phase='mixing')
                self._sample_pulse(plan['sample'],'dispense',plan['mixing']);plan['completed_commands']+=1
                plan.update(mixing_performed=True,mixing_finished_at=timestamp())
                self.samples[str(plan['sample'])].update(last_gas_stop=plan['mixing_finished_at'],clear_at=None,settle_s=None)
                self._lab_event('progress',plan=plan)
            if plan['purge_after']:
                plan['current']=dict(item_index=None,chunk_index=None,phase='purge')
                self._pulse(16,'aspirate',plan['purge']);plan['completed_commands']+=1
                plan['purge_performed']=True;self.common='air_unverified'
                self._lab_event('progress',plan=plan)
            if plan.get('rinse_after'):
                plan['current']=dict(item_index=None,chunk_index=None,phase='rinse')
                self._status('RECIPE','整瓶完成：15口引入约1.5 mL清水，朝C端废液方向清洗；不做空气吹扫')
                self._pulse(15,'aspirate',plan['rinse']['pulse']);plan['completed_commands']+=1
                plan.update(rinse_performed=True,rinse_finished_at=timestamp())
                self.common='wet'
                self._lab_event('progress',plan=plan)
            self._check()
            plan.update(completed=True,current=None,finished_at=timestamp())
            self.log('recipe_completed',plan=copy.deepcopy(plan))
        except Exception as exc:
            plan.update(completed=False,cancelled=self.cancel.is_set(),error=str(exc),finished_at=timestamp())
            self.log('recipe_interrupted',plan=copy.deepcopy(plan))
            raise

    def _free_action(self,payload):
        from controller import number,pulse_checked
        action=payload['action']
        if action=='batch_recipe':
            self._manual_batch_action(payload)
            return True
        supported={'manual_motion','read_status','volume','recipe','stock_calibration','aspirate','chase','dose','bubble','run','prime','wash','purge','reset','discard_loaded','new_samples','confirm_reset','clear','magnetic_stop','prepare_capture','capture'}
        if action not in supported:return False
        # Validate the complete recipe before the first physical action.
        p=None
        if action=='read_status':
            self.hardware.read_status(self.cancel)
            self._status('IDLE','已刷新泵阀读回状态；本次没有切阀或转泵。')
            return True
        if action=='manual_motion':
            if payload.get('rotation') not in ('cw','ccw'):raise ValueError('选择CW正转或CCW反转')
            turns=number(payload.get('turns'),1,100,'圈数',True)
            rpm=number(payload.get('rpm'),.1,100,'转速rpm')
            p=pulse_checked(dict(turns=turns,rpm=rpm,timeout_s=max(15,math.ceil(60*turns/rpm*1.25+10))))
            flow='aspirate' if payload['rotation']==self.config['pump']['aspirate_direction'] else 'dispense'
        if action in ('volume','recipe'):
            self.recipe_run=None;self.volume_run=None
            recipe_payload=payload if action=='recipe' else dict(payload,items=[dict(source=payload.get('source'),volume_ml=payload.get('volume_ml'))],mix_after=payload.get('mix_after',False),rinse_after=payload.get('rinse_after',False))
            plan=self._recipe_plan(recipe_payload)
        if action=='stock_calibration':
            self.recipe_run=None;self.volume_run=None
            plan=self._stock_calibration_plan(payload)
        if action in ('aspirate','chase','dose','bubble'):
            p=self._debug_parameters(payload)
            # The controller's active payload and completion callbacks reference
            # this dict. Persist the resolved endpoint before any disturbance.
            payload.update(sample=p['sample'],source=p['source'],debug_parameters=copy.deepcopy(p))
        if action=='run':
            raise ValueError('旧调试队列已停用；请使用recipe单瓶配方，所有组分完成后统一混合和吹扫')
        if action in ('wash','purge','reset','discard_loaded'):
            for key in ('wash','purge'):pulse_checked(self.config['reset'][key])
        if action=='prime':
            number(payload.get('source'),10,15,'来源口',True);pulse_checked(payload.get('pulse'))
        self.pending_plug=None;self.current_delivery=None;self.reset_sequence=None
        self.bench.update(water_ready=False,air_ready=False,delivery_seen=False,delivery_seen_context=None,last_run=None)
        self._reset_tubes()
        self.log('free_debug_action',action=action,parameters=copy.deepcopy(payload),observations_required=False,volume_verified=False)
        if action=='manual_motion':
            self._pulse(payload.get('port'),flow,p,manual_direction=True)
            self.common='unknown'
        elif action in ('volume','recipe','stock_calibration'):
            if action=='volume':self.volume_run=plan['items'][0]
            self._execute_recipe(plan)
            if action=='volume':self.volume_run['mixing_performed']=plan['mixing_performed']
        elif action in ('aspirate','chase','dose','bubble'):self._debug_delivery(p,action,payload.get('bubble') is True)
        elif action=='prime':
            self._pulse(payload['source'],'aspirate',payload['pulse']);self.common='stock'
            self.sources[str(payload['source'])]={'primed':False,'primed_at':None,'motion_completed_at':timestamp()}
        elif action in ('wash','purge','reset','discard_loaded'):
            if action in ('wash','reset','discard_loaded'):
                self._pulse(15,'aspirate',self.config['reset']['wash']);self.common='wet'
            if action in ('purge','reset','discard_loaded'):
                self._pulse(16,'aspirate',self.config['reset']['purge']);self.common='air_unverified'
        elif action=='new_samples':
            for s in self.samples.values():s.update(condition='unknown',dose_count=0,last_gas_stop=None,clear_at=None,settle_s=None)
        elif action=='confirm_reset':self.common='air_unverified'
        elif action in ('clear','magnetic_stop'):
            sample=number(payload.get('sample'),1,6,'样品',True)
            if action=='clear':self.samples[str(sample)]['clear_at']=timestamp()
            else:self.samples[str(sample)].update(last_gas_stop=timestamp(),clear_at=None,settle_s=number(payload.get('settle_s',0),0,86400,'静置秒数'),timing_basis='operator_recorded')
        else:
            targets=payload.get('targets')
            if not isinstance(targets,list) or not targets or any(type(n)is not int or n not in range(1,7) for n in targets) or len(set(targets))!=len(targets):raise ValueError('选择1—6不重复样品号')
            self.capture.begin(targets,self.mode,source='manual')
            value=copy.deepcopy(self.capture.current)
            value.update(sample_timing={str(n):copy.deepcopy(self.samples[str(n)]) for n in targets},geometry=copy.deepcopy(self.config['geometry']),view_direction='fixed_front_row',timing_prerequisites_bypassed=True)
            self.capture._save(value)
        self._status('IDLE','自由调试动作已结束；不要求前序步骤，也不将动作完成当作已充满、已排空或体积已验证。')
        return True
