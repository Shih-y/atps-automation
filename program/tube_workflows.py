"""Tube refill and drainage; legacy water-only recipe is retired."""
import copy
from capture_store import timestamp

FILL = dict(turns=6, rpm=50, timeout_s=20)
AIR = dict(turns=15, rpm=60, timeout_s=30)
SAMPLE_AIR = dict(steps=1000, rpm=15, timeout_s=35)


def ports(value, allowed, name):
    if (not isinstance(value, list) or not value or
            any(type(p) is not int or p not in allowed for p in value) or
            len(set(value)) != len(value)):
        raise ValueError(f'{name}须选择有效且不重复的端口')
    return list(value)


class TubeMixin:
    def _tube_observation(self, message):
        if self.free_debug:
            self.log("tube_observation_not_requested", instruction=message, observed=False)
            return
        self._ask(message)

    def _reset_tubes(self):
        self.tubes=dict(primed_ports=[],common_air_seen=False,source_drained=[],
                        sample_drained=[],fully_dry_verified=False,last_run=None)

    def _tube_purge(self):
        self.tubes['common_air_seen']=False
        self.common='unknown'
        self._status('TUBES_PURGE','16口吸入空气，公共管排向C端废液：15圈／60 rpm。')
        self._pulse(16,'aspirate',AIR)
        self.common='air_unverified'
        self._tube_observation('16口吹扫已停泵。请确认公共软管无可见连续水柱，C端无明显悬滴、无飞溅。仍有水柱请停止，再单独吹扫；水膜尚未验证。')
        self.tubes['common_air_seen']=not self.free_debug

    def _tube_refill(self, selected):
        for port in selected:
            self.sources[str(port)]={'primed':False,'primed_at':None}
            self.tubes['primed_ports']=[p for p in self.tubes['primed_ports'] if p!=port]
            self.tubes['source_drained']=[p for p in self.tubes['source_drained'] if p!=port]
            self.tubes['common_air_seen']=False
            self.common='wet'
            self._status('TUBES_REFILL',f'{port}口来源预充（不保证C端出水）：6圈／50 rpm。吸液端须浸水，C端悬空。')
            self._pulse(port,'aspirate',FILL)
            self._tube_observation(f'{port}口引液已停泵。请沿该支管检查：整根80 cm硬管充满液体、无明显气泡，且本段运行末尾C端连续出水。未达到请停止后单独重做该口；不能仅凭C端有水确认来源管充满。')
            self.sources[str(port)]={'primed':not self.free_debug,'primed_at':timestamp() if not self.free_debug else None,'basis':'motion_only' if self.free_debug else 'operator_observed_full_branch'}
            if not self.free_debug:self.tubes['primed_ports']=sorted(set(self.tubes['primed_ports']+[port]))
            self.log('tube_source_refill_completed',port=port,observed=not self.free_debug)
        self._tube_purge()

    def _tube_drain_sources(self, selected):
        self._tube_observation(f'先把来源口 {selected} 的吸液端全部提出液面，放在清洁空气中，不能仍浸在瓶内。C端悬空对准废液。完成后确认才开始抽气排液。')
        for port in selected:
            self.sources[str(port)]={'primed':False,'primed_at':None}
            self.tubes['primed_ports']=[p for p in self.tubes['primed_ports'] if p!=port]
        self.tubes['common_air_seen']=False
        self.common='unknown'
        for port in selected:
            self._status('TUBES_DRAIN',f'{port}口从已提出的管端吸空气，排向C端废液：15圈／60 rpm。')
            self._pulse(port,'aspirate',AIR)
            self._tube_observation(f'{port}口排液已停泵。确认这根来源硬管及公共软管无可见连续水柱、C端无明显悬滴。未排净请选择停止，再单独重做；不表示管壁无水膜。')
            if not self.free_debug:self.tubes['source_drained']=sorted(set(self.tubes['source_drained']+[port]))
            self.log('tube_source_drain_completed',port=port,observed=not self.free_debug,fully_dry_verified=False)
        self._tube_purge()

    def _tube_drain_samples(self, selected, common_prepared=False):
        self._tube_observation(f'先保存好样品，再将样品口 {selected} 的硬管末端全部从样品瓶取出，悬空朝向废液容器内壁、不浸入废液；不要对着已配好样品吹。C端仍须清洁、悬空，避开废液飞溅。完成后确认。')
        if not common_prepared: self._tube_purge()
        for port in selected:
            self.samples[str(port)].update(condition='unknown',uncertain_delivery=True,
                                          uncertainty_note='末端已移至废液并进行排液；下次重新放回样品瓶')
            self.tubes['common_air_seen']=False
            self._status('TUBES_DRAIN',f'样品{port}管向废液送气：1000步／15 rpm。')
            self._pulse(port,'dispense',SAMPLE_AIR)
            self._tube_observation(f'样品{port}支管排液已停泵。确认硬管无可见连续水柱、末端无明显悬滴且未飞溅。未排净请选择停止后单独重做。样品内容物不应被抽走。')
            if not self.free_debug:self.tubes['sample_drained']=sorted(set(self.tubes['sample_drained']+[port]))
            self.log('tube_sample_drain_completed',port=port,observed=not self.free_debug,fully_dry_verified=False)
        # Isolate the samples after all tips have already been removed from them.
        self._select_port(16)
        self.common='air_unverified'
        self.tubes['common_air_seen']=not self.free_debug

    def _tube_action(self, payload):
        op=payload.get('operation')
        if op=='water_recipe':raise ValueError('旧清水组合配方已停用；请使用recipe单瓶配方')
        if op not in ('refill','purge','drain_sources','drain_samples','finish'):
            raise ValueError('未知管路维护步骤')
        selected=ports(payload.get('sources'),range(11,16),'来源口') if op in ('refill','drain_sources','finish') else []
        samples=ports(payload.get('samples'),range(1,7),'样品口') if op in ('drain_samples','finish') else []
        self.bench.update(water_ready=False,air_ready=False,delivery_seen=False,delivery_seen_context=None,last_run=None)
        self.reset_sequence=None; self.current_delivery=None
        self.tubes['last_run']=dict(operation=op,sources=selected,samples=samples,started_at=timestamp(),completed=False,volume_verified=False)
        self.log('tube_workflow_started',operation=op,sources=selected,samples=samples)
        if op=='refill': self._tube_refill(selected)
        elif op=='purge': self._tube_purge()
        elif op=='drain_sources': self._tube_drain_sources(selected)
        elif op=='drain_samples': self._tube_drain_samples(samples)
        elif op=='finish':
            self._tube_drain_sources(selected)
            self._tube_drain_samples(samples,common_prepared=True)
        self.tubes['last_run'].update(completed=True,finished_at=timestamp())
        self.log('tube_workflow_completed',run=copy.deepcopy(self.tubes['last_run']),fully_dry_verified=False)
        self._status('IDLE','所选管路步骤已结束。观察记录仅限本次所选端口；未选管路未知，水膜／残留／实际交付量仍需测量。')
