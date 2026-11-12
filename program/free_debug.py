"""Explicit unvalidated manual operations; never invent liquid observations."""
import copy
import math
from capture_store import timestamp
from tube_workflows import AIR

class FreeDebugMixin:
    @property
    def free_debug(self):
        return self.config.get('free_debug',True) is True

    def _debug_parameters(self,payload):
        from controller import number,pulse_checked
        value=payload.get('debug_parameters',{})
        if not isinstance(value,dict): raise ValueError('调试参数须为对象')
        p=dict(source=value.get('source',15),sample=value.get('sample',1),
               aspirate=value.get('aspirate',dict(steps=45,rpm=5,timeout_s=20)),
               chase=value.get('chase',dict(steps=1000,rpm=15,timeout_s=35)),
               bubble=value.get('bubble',dict(steps=20,rpm=5,timeout_s=10)))
        number(p['source'],11,15,'来源口',True);number(p['sample'],1,6,'样品口',True)
        for key in ('aspirate','chase','bubble'):p[key]=pulse_checked(p[key])
        return p

    def _debug_delivery(self,p,action,bubble=False):
        if action in ('aspirate','dose'):
            self._pulse(p['source'],'aspirate',p['aspirate']);self.common='stock'
        if action in ('chase','dose'):
            self._pulse(p['sample'],'dispense',p['chase']);self.common='wet'
            self.samples[str(p['sample'])].update(condition='unknown',uncertain_delivery=True,
                uncertainty_note='自由调试，交付量与液路状态未验证',last_gas_stop=timestamp(),clear_at=None,settle_s=None)
        if action=='bubble' or bubble:
            self._pulse(p['sample'],'dispense',p['bubble'])
            self.samples[str(p['sample'])].update(last_gas_stop=timestamp(),clear_at=None,settle_s=None)

    def _volume_plan(self,payload):
        from controller import number
        ml=number(payload.get('volume_ml'),.001,10,'本次体积mL（10 mL瓶，需自行扣除已有液体）')
        ref=self.water_dose_reference;refp=ref.get('parameters',{})
        mean=number(ref.get('mean_per_dose_ml'),.01,1,'单剂体积参考mL')
        steps=number(refp.get('aspirate_steps'),1,100,'参考吸取步数',True)
        ar=number(refp.get('aspirate_rpm'),.1,100,'参考吸取rpm')
        cr=number(refp.get('chase_rpm'),.1,100,'参考追送rpm')
        cs=number(refp.get('chase_steps'),1,3000,'参考追送步数',True)
        total=int(math.floor(ml*steps/mean+.5))
        if total<1:raise ValueError('体积换算不足1步，请增大输入量；当前约0.00333 mL/步')
        # One aspiration and one delivery, with no automatic subdivision.
        # Preserve the reference chase allowance after returning the extra intake.
        chase_steps=cs+max(0,total-steps)
        p=self._debug_parameters(dict(debug_parameters=dict(source=payload.get('source'),sample=payload.get('sample'),
            aspirate=dict(steps=total,rpm=ar,timeout_s=max(20,math.ceil(total*.36/ar+10))),
            chase=dict(steps=chase_steps,rpm=cr,timeout_s=max(35,math.ceil(chase_steps*.36/cr+10))))))
        return dict(requested_ml=ml,estimated_ml=total*mean/steps,total_steps=total,chunks=[total],
                    delivery_mode='single_shot',mixing_performed=False,
                    source=p['source'],sample=p['sample'],parameters=p,reference=copy.deepcopy(ref),
                    actual_ml=None,volume_verified=False,completed=False,completed_plugs=0,started_at=timestamp())

    def _free_action(self,payload):
        from controller import number,pulse_checked
        action=payload['action']
        supported={'manual_motion','read_status','volume','aspirate','chase','dose','bubble','run','prime','wash','purge','reset','discard_loaded','new_samples','confirm_reset','clear','magnetic_stop','prepare_capture','capture'}
        if action not in supported:return False
        # Validate all queue parameters before the first physical action.
        checked=None;p=None
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
        if action=='volume':plan=self._volume_plan(payload)
        if action in ('aspirate','chase','dose','bubble'):p=self._debug_parameters(payload)
        if action=='run':
            items=payload.get('items')
            if not isinstance(items,list) or not 1<=len(items)<=60:raise ValueError('队列须包含1—60项调试参数')
            checked=[(self._debug_parameters(item),item.get('bubble') is True) for item in items]
        if action in ('wash','purge','reset','discard_loaded','run'):
            for key in ('wash','purge'):pulse_checked(self.config['reset'][key])
        if action=='prime':
            number(payload.get('source'),11,15,'来源口',True);pulse_checked(payload.get('pulse'))
        self.pending_plug=None;self.current_delivery=None;self.reset_sequence=None
        self.bench.update(water_ready=False,air_ready=False,delivery_seen=False,delivery_seen_context=None,last_run=None)
        self._reset_tubes()
        self.log('free_debug_action',action=action,parameters=copy.deepcopy(payload),observations_required=False,volume_verified=False)
        if action=='manual_motion':
            self._pulse(payload.get('port'),flow,p,manual_direction=True)
            self.common='unknown'
        elif action=='volume':
            self.volume_run=plan
            self.log('volume_trial_started',plan=copy.deepcopy(plan))
            for steps in plan['chunks']:
                self._status('VOLUME_DEBUG',f"指定体积调试 {plan['source']}→{plan['sample']}：一次吸取{steps}步、一次追送；不追加混合吹气。")
                p=copy.deepcopy(plan['parameters']);p['aspirate']['steps']=steps
                self._debug_delivery(p,'dose')
                plan['completed_plugs']+=1
                self._pulse(16,'aspirate',AIR);self.common='air_unverified'
            plan.update(completed=True,finished_at=timestamp())
            self.log('volume_trial_completed',plan=copy.deepcopy(plan))
        elif action in ('aspirate','chase','dose','bubble'):self._debug_delivery(p,action,payload.get('bubble') is True)
        elif action=='prime':
            self._pulse(payload['source'],'aspirate',payload['pulse']);self.common='stock'
            self.sources[str(payload['source'])]={'primed':False,'primed_at':None,'motion_completed_at':timestamp()}
        elif action in ('wash','purge','reset','discard_loaded'):
            if action in ('wash','reset','discard_loaded'):
                self._pulse(15,'aspirate',self.config['reset']['wash']);self.common='wet'
            if action in ('purge','reset','discard_loaded'):
                self._pulse(16,'aspirate',self.config['reset']['purge']);self.common='air_unverified'
        elif action=='run':
            for p,bubble in checked:
                self._debug_delivery(p,'dose',bubble)
                self._pulse(15,'aspirate',self.config['reset']['wash'])
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
