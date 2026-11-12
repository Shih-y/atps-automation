"""Observable water trials with no fabricated formal calibration status."""
import copy
import json
import math
import secrets
from capture_store import timestamp
from motion_planning import motion_plan,analyze_bench_readings,_number

class BenchMixin:
    def _reset_bench(self):
        if not hasattr(self,'bulk_water_reference'):
            path=self.directory/'bulk_water_reference.json'
            record=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
                'at':'2026-11-11','source':'用户提供的C端5圈净质量，内置默认；水密度按1.000近似',
                'input':{'method':'bulk_turns','readings_g':[4.61,4.55,4.57],
                         'density_g_ml':1.,'balance_resolution_g':.01,'turns':5}}
            if record['input'].get('method')!='bulk_turns':raise ValueError('充水参考记录类型错误')
            record['report']=analyze_bench_readings(record['input'])
            self.bulk_water_reference=record
        if not hasattr(self,'water_dose_reference'):
            dose_path=self.directory/'water_dose_reference.json'
            if not dose_path.exists():dose_path=self.default_path.with_name('water_dose_reference.default.json')
            self.water_dose_reference=json.loads(dose_path.read_text(encoding='utf-8'))
        self.bench={'water_ready':False,'air_ready':False,'delivery_seen':False,
                    'delivery_seen_context':None,'sample_counts':{},'last_run':None,'reports':[]}

    def _bench_action(self,payload):
        operation=payload.get('operation'); b=self.bench; last=b.get('last_run') or {}
        plan=motion_plan(self.config)['recipes']
        if operation in ('skip_fill','skip_purge'):
            if operation=='skip_fill':
                b['water_ready']=True
                self.sources['15']={'primed':True,'primed_at':timestamp()}
            else:
                b['air_ready']=True;self.common='air_unverified';self.reset_sequence=None
            b['last_run']={'operation':operation,'completed':True,'motion_executed':False,'operator_asserted':True,'finished_at':timestamp()}
            self.log('bench_step_skipped_by_operator',operation=operation,motion_executed=False,formal_calibration_validated=False)
            self._status('BENCH_OBSERVE','已按操作者确认的现场状态跳过；未执行泵阀动作，未验证洗净。')
            return
        if operation=='water_seen':
            if not self.free_debug and (last.get('operation') not in ('fill_water','collect_water') or not last.get('completed')):raise ValueError('先执行引水，并现场看到C端连续出水，再点此按钮')
            b['water_ready']=True;self.sources['15']={'primed':True,'primed_at':timestamp()}
            self.log('bench_water_front_observed',scope='仅确认水已贯通，未确认洗净');return
        if operation=='air_seen':
            if not self.free_debug and (last.get('operation') not in ('purge','dose_water') or not last.get('completed')):raise ValueError('先完成吹扫，再观察并确认')
            b['air_ready']=True;self.log('bench_no_visible_water_column_observed',residual_water_quantified=False);return
        if operation=='dose_seen':
            if not self.free_debug and (last.get('operation')!='dose_water' or last.get('count')!=1 or not last.get('completed')):raise ValueError('先完成一次试加并看到液体进入指定样品瓶')
            b['delivery_seen']=True;b['delivery_seen_context']={k:payload.get(k,last.get(k)) for k in ('sample','chase_steps','aspirate_rpm','chase_rpm')}
            self.log('bench_delivery_observed',context=b['delivery_seen_context'],volume_verified=False);return
        if operation not in ('fill_water','collect_water','purge','dose_water'):raise ValueError('未知清水校准步骤')
        # Validate everything before invalidating prior readiness or moving.
        if not self.free_debug and operation=='collect_water' and (not b['water_ready'] or self.common!='wet'):
            raise ValueError('整圈称重前，先引水至C端连续出水并点击已出水；公共管必须充水')
        if operation=='dose_water':
            sample=_number(payload.get('sample'),1,6,'样品瓶号',True)
            count=_number(payload.get('count',1),1,5,'本组剂数',True)
            if count not in (1,5):raise ValueError('先试1剂，成组称量每组5剂')
            steps=_number(payload.get('aspirate_steps',45),1,100,'液段吸取步数',True)
            chase=_number(payload.get('chase_steps',300),1,3000,'低速追送步数',True)
            max_rpm=min(100,self.config['pump']['max_rpm'])
            aspirate_rpm=_number(payload.get('aspirate_rpm',5),.1,max_rpm,'吸取转速rpm')
            chase_rpm=_number(payload.get('chase_rpm',10),.1,max_rpm,'追送转速rpm')
            aspirate_timeout=max(20,math.ceil(steps*.36/aspirate_rpm+10))
            chase_timeout=max(35,math.ceil(chase*.36/chase_rpm+10))
            if max(aspirate_timeout,chase_timeout)>120:raise ValueError('所选步数与转速预计超出120秒动作上限；减少步数或提高转速后再试')
            if not self.free_debug and (not b['water_ready'] or not b['air_ready'] or self.common!='air_unverified'):raise ValueError('先确认15口水管已充满，并完成公共管吹扫及观察')
            if not self.free_debug and count==5 and (not b['delivery_seen'] or b.get('delivery_seen_context')!={'sample':sample,'chase_steps':chase,'aspirate_rpm':aspirate_rpm,'chase_rpm':chase_rpm}):
                raise ValueError('先对同一瓶、同一追送步数和转速试1剂并确认已经送入；再运行5剂一组')
        b['last_run']={'operation':operation,'started_at':timestamp(),'completed':False}
        self.current_delivery=None;self.reset_sequence=None;b['air_ready']=False
        if operation=='fill_water':
            b['water_ready']=False;b['delivery_seen']=False;b['delivery_seen_context']=None
            self._pulse(15,'aspirate',(dict(turns=6,rpm=50,timeout_s=20) if self.free_debug else plan['first_flush']));self.common='wet'
            self._status('BENCH_OBSERVE','引水动作已结束；看到C端连续出水后点“已连续出水”。未出水可再引水，异常立即停止。')
        elif operation=='collect_water':
            self._pulse(15,'aspirate',{'turns':5,'rpm':50,'timeout_s':20});self.common='wet'
            self._status('BENCH_WEIGH','5圈收集已结束。称接液瓶净增质量并填写；C端恢复对准废液。')
        elif operation=='purge':
            self._pulse(16,'aspirate',plan['purge']);self.common='air_unverified'
            self._status('BENCH_OBSERVE','吹扫已结束；没有连续水柱后点对应确认。该确认不代表无水膜。')
        else:
            self.samples[str(sample)].update(condition='unknown',uncertain_delivery=True,
                                             uncertainty_note='已进行清水称重试验，不能沿用正式样品状态')
            b['last_run'].update(sample=sample,count=count,aspirate_steps=steps,chase_steps=chase,
                                 aspirate_rpm=aspirate_rpm,chase_rpm=chase_rpm,
                                 tube_prior_trials=b['sample_counts'].get(str(sample),0),completed_doses=0)
            for i in range(count):
                self._status('BENCH_DOSE',f'清水试验第{i+1}/{count}剂：先吸取、再送到{sample}号瓶；完成后16口排水。')
                self._pulse(15,'aspirate',{'steps':steps,'rpm':aspirate_rpm,'timeout_s':aspirate_timeout})
                # Low-speed bench evidence is approximately 200 steps/revolution.
                # Allow 20% timing margin + 10 s, retaining the old 35 s minimum.
                self._pulse(sample,'dispense',{'steps':chase,'rpm':chase_rpm,'timeout_s':chase_timeout})
                b['sample_counts'][str(sample)]=b['sample_counts'].get(str(sample),0)+1
                b['last_run']['completed_doses']=i+1
                self.log('bench_dose_trial_completed',sample=sample,trial_index=i+1,aspirate_steps=steps,chase_steps=chase,aspirate_rpm=aspirate_rpm,chase_rpm=chase_rpm,measured_ml=None)
                self._pulse(16,'aspirate',plan['purge'])
            self.common='air_unverified'
            self._status('BENCH_WEIGH','试加已结束。记录净增质量；样品管已使用，倒空瓶子不会使管子恢复初次空管。')
        b['last_run'].update(completed=True,finished_at=timestamp())
        self.log('bench_step_completed',run=copy.deepcopy(b['last_run']),formal_calibration_validated=False)

    def bench_report(self,payload):
        with self.lock:
            if self.busy or self.pending_plug:raise ValueError('先结束当前动作或待送液段，再保存称重记录')
            report=analyze_bench_readings(payload)
            folder=self.directory/'bench_reports';folder.mkdir(exist_ok=True)
            path=folder/(secrets.token_hex(12)+'.json')
            value={'at':timestamp(),'input':copy.deepcopy(payload),'report':report,'config':copy.deepcopy(self.config),
                   'last_run':copy.deepcopy(self.bench['last_run']),'operator_entered_measurements':True}
            path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
            if payload.get('method')=='bulk_turns':
                reference={'at':value['at'],'source':'用户通过可选校准更新的C端充水参考',
                           'input':copy.deepcopy(payload),'report':report,'saved_file':str(path)}
                reference_path=self.directory/'bulk_water_reference.json'
                temporary=reference_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(reference,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
                temporary.replace(reference_path)
                self.bulk_water_reference=reference
            elif payload.get('method')=='batch_water':
                reference={**copy.deepcopy(report),'at':value['at'],'source':'用户重新测量并保存的清水成组参考',
                           'saved_file':str(path),'parameters':{k:payload[k] for k in
                           ('sample','aspirate_steps','chase_steps','aspirate_rpm','chase_rpm') if k in payload}}
                reference_path=self.directory/'water_dose_reference.json'
                temporary=reference_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(reference,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
                temporary.replace(reference_path)
                self.water_dose_reference=reference
            self.bench['reports'].append({'saved_file':str(path),'report':report});self.bench['reports']=self.bench['reports'][-20:]
            self.log('bench_report_saved',path=str(path),formal_calibration_validated=False)
            return {'ok':True,'report':report,'saved_file':str(path)}
