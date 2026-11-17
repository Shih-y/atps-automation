"""Serial manual mL batches; no project or automatic observation prerequisite."""
import copy
import json
import os
import re
import secrets
from capture_store import timestamp
from bottle_routing import sample_port_map


class ManualBatchMixin:
    def _load_manual_batch(self):
        self.manual_batch_run=None
        path=self.directory/'manual_batches'/'latest.json'
        if path.exists():
            run=json.loads(path.read_text(encoding='utf-8'))
            if not run.get('completed'):
                run.update(cancelled=True,restart_interrupted=True,
                           error=run.get('error','程序已重启；本批不自动恢复，交付不确定部分须核实'))
            self.manual_batch_run=run

    def _manual_batch_view(self):
        run=copy.deepcopy(self.manual_batch_run)
        if run is not None:
            run['completed_commands']=sum(r['completed_commands'] for r in run['recipes'])
        return run

    def _save_manual_batch(self):
        run=self._manual_batch_view()
        folder=self.directory/'manual_batches';folder.mkdir(exist_ok=True)
        for filename in ('batch-'+run['id']+'.json','latest.json'):
            path=folder/filename;temp=folder/(filename+'.'+secrets.token_hex(8)+'.tmp')
            try:
                with temp.open('x',encoding='utf-8') as f:
                    json.dump(run,f,ensure_ascii=False,allow_nan=False,indent=2);f.flush();os.fsync(f.fileno())
                os.replace(temp,path)
            finally:
                if temp.exists():temp.unlink()

    def _manual_batch_plan(self,payload):
        recipes=payload.get('recipes')
        if not isinstance(recipes,list) or not 1<=len(recipes)<=len(sample_port_map(self.config)) or any(not isinstance(r,dict) for r in recipes):
            raise ValueError('多瓶配方须包含1至已接管瓶数的有效配方')
        clean=payload.get('rinse_after',True)
        if type(clean) is not bool:raise ValueError('清洗选项须为布尔值')
        plans=[];seen=set()
        for recipe in recipes:
            data=dict(recipe,mix_after=True,rinse_after=clean,purge_after=False)
            if 'mixing' in payload:data['mixing']=payload['mixing']
            plan=self._recipe_plan(data)
            if plan['sample'] in seen:raise ValueError('同一批不能重复选择同一瓶，请合并该瓶毫升数')
            seen.add(plan['sample']);plans.append(plan)
        return dict(recipes=plans,total_bottles=len(plans),completed_bottles=0,current_index=None,
                    total_commands=sum(p['total_commands'] for p in plans),completed_commands=0,
                    completed=False,cancelled=False,started_at=None,
                    method='manual_multi_bottle_ml_v2',rinse_after=clean,sample_port_map=sample_port_map(self.config))

    def preview_manual_batch(self,payload):
        with self.lock:return dict(ok=True,plan=self._manual_batch_plan(payload))

    def _manual_batch_action(self,payload):
        request_id=payload.get('request_id')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',request_id) or request_id=='latest':
            raise ValueError('多瓶请求编号无效，请重新发起请求')
        request=copy.deepcopy(payload)
        path=self.directory/'manual_batches'/('batch-'+request_id+'.json')
        if path.exists():
            run=json.loads(path.read_text(encoding='utf-8'))
            if run.get('request')!=request:raise ValueError('同一请求编号对应的配方已改变，请使用新的请求编号')
            if not run.get('completed'):
                run.update(cancelled=True,error=run.get('error','旧批次未完成；不会自动重放'))
            self.manual_batch_run=run
            self._status('IDLE','已找到该批记录，本次未重复加料；中断批次请先核实已交付量。')
            return
        run=self._manual_batch_plan(payload)  # All bottles before the first movement.
        run.update(id=request_id,request=request,started_at=timestamp())
        self.manual_batch_run=run
        self.pending_plug=None;self.current_delivery=None;self.reset_sequence=None
        self.volume_run=None;self.recipe_run=None
        self.bench.update(water_ready=False,air_ready=False,delivery_seen=False,delivery_seen_context=None,last_run=None)
        self._reset_tubes()
        original=self._active_payload
        try:
            self._save_manual_batch()
            self.log('manual_batch_started',batch=copy.deepcopy(run))
            for index,plan in enumerate(run['recipes']):
                self._check()
                run['current_index']=index
                # Only the actually reached bottle is disturbed. Logical identity
                # is forwarded to the existing lab invalidation hook.
                self._active_payload=dict(payload,sample=plan['sample'])
                self._manual_notified=False
                self._execute_recipe(plan)
                run['completed_bottles']+=1
                run['completed_commands']=sum(r['completed_commands'] for r in run['recipes'])
                self._save_manual_batch()
            self._check()
            run.update(completed=True,current_index=None,finished_at=timestamp())
            self._save_manual_batch()
            self.log('manual_batch_completed',batch=copy.deepcopy(run))
            cleanup='已清洗并吹扫，请检查残水；未验证完全干燥' if run['rinse_after'] else '未安排清洗与吹扫，公共管仍湿'
            self._status('IDLE',f"多瓶毫升配方已完成：{run['completed_bottles']}瓶；实际交付量未验证。{cleanup}。")
        except Exception as exc:
            run.update(completed=False,cancelled=self.cancel.is_set(),error=str(exc),finished_at=timestamp())
            # Persistence failure must not conceal cancellation or block the
            # controller's stop path. Completed pulse events also remain in logs.
            try:self._save_manual_batch()
            except Exception as save_error:run['save_error']=str(save_error)
            raise
        finally:
            self._active_payload=original
