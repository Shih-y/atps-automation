"""In-memory pump and selector for the isolated rehearsal workspace."""
import threading
import time
import math

from controller import Controller
from transport import HardwareError


class SimulatedHardware:
    def __init__(self, config):
        self.aspirate_direction = config['pump']['aspirate_direction']
        self.lock = threading.RLock()
        self.interrupt = threading.Event()
        # Nominal timing only: existing engineering basis is 200 steps/turn.
        # No rehearsal speed multiplier. Mechanical/serial delays need measurement.
        self.valve_switch_s = float(config.get('simulation', {}).get('valve_switch_s', .5))
        if not math.isfinite(self.valve_switch_s) or not 0 <= self.valve_switch_s <= 10:
            raise ValueError('模拟阀切换时间须为0—10秒')
        self.status = dict(valve_status='confirmed', valve_port=1, target_port=None)
        self._idle()

    def _idle(self):
        self.status.update(pump_status='stopped', pump_at=time.time(), direction=None,
                           flow=None, rpm=None, remaining=0, unit=None)

    def get_telemetry(self):
        with self.lock:
            return dict(self.status)

    def _check(self, cancel):
        if self.interrupt.is_set() or (cancel and cancel.is_set()):
            raise HardwareError('模拟动作已停止')

    def select(self, port, cancel=None):
        with self.lock:
            self._check(cancel)
            self.status.update(valve_status='switching', target_port=port, valve_port=None)
        deadline = time.monotonic() + self.valve_switch_s
        while time.monotonic() < deadline:
            self._check(cancel)
            self.interrupt.wait(min(.05, max(0, deadline - time.monotonic())))
        with self.lock:
            self._check(cancel)
            self.status.update(valve_status='confirmed', target_port=None, valve_port=port, valve_at=time.time())

    def _move(self, flow, amount, rpm, unit, cancel):
        if flow not in ('aspirate', 'dispense') or amount <= 0 or not math.isfinite(amount) or not .1 <= rpm <= 100:
            raise ValueError('无效的有限动作参数')
        direction = self.aspirate_direction
        if flow == 'dispense':
            direction = 'cw' if direction == 'ccw' else 'ccw'
        duration = (amount if unit == 'turns' else amount / 200) * 60 / rpm
        started = time.monotonic()
        deadline = started + duration
        while True:
            with self.lock:
                self._check(cancel)
                remaining_s = max(0, deadline - time.monotonic())
                if not remaining_s:
                    self._idle()
                    return
                self.status.update(pump_status='running', pump_at=time.time(), direction=direction,
                                   flow=flow, rpm=rpm, remaining=math.ceil(amount * remaining_s / duration),
                                   unit=unit, nominal_duration_s=duration)
            self.interrupt.wait(min(.05, remaining_s))

    def move(self, direction, steps, rpm, cancel=None):
        self._move(direction, steps, rpm, 'steps', cancel)

    def move_turns(self, direction, turns, rpm, cancel=None):
        self._move(direction, turns, rpm, 'turns', cancel)

    def stop(self):
        with self.lock:
            self.interrupt.set()
            self._idle()
            if self.status['valve_status'] == 'switching':
                self.status.update(valve_status='unknown', valve_port=None, target_port=None)

    close = stop

    def recover_after_stop(self):
        self.interrupt.clear()

    def read_status(self, cancel=None):
        with self.lock:
            self._check(cancel)
            self.status['pump_at'] = time.time()
            return dict(self.status)


class SimulationController(Controller):
    def connect(self, mode='demo', pump_port='', valve_port='', auto_detect=False):
        if mode != 'demo':
            raise ValueError('模拟工作区仅使用虚拟设备')
        with self.lock:
            if self.connected or self.busy or self._user_stopping:
                raise ValueError('模拟设备已启用或正在执行操作')
            self._unknown()
            self.cancel.clear()
            self.hardware = SimulatedHardware(self.config)
            self.mode = 'demo'
            self.connected = True
            self.commission_count = 0
            self.state = 'CONNECTED'
            self.stop_status = 'unknown'
            self.message = '模拟设备就绪，可执行配液、拍摄和实验流程。'
            self.log('connected', mode=self.mode)
