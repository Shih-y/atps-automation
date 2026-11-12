"""Test-only hardware double, never imported by the production server."""
import copy
import threading
from protocols import PumpProtocol, ValveProtocol
from transport import HardwareError, OperationCancelled, _cancelled


class DemoHardware:
    """In-memory simulation, visibly separate from physical serial operation."""
    def __init__(self, config, logger=None):
        self.config = copy.deepcopy(config)
        self.logger = logger
        self.connected = False
        self.port = None
        self.moves = []
        self._stopped = threading.Event()

    def connect(self):
        self.connected = True
        self.port = None
        self._stopped.clear()

    def close(self):
        self.stop()
        self.connected = False

    def _check(self, cancel_event):
        if not self.connected:
            raise HardwareError("demo is not connected")
        if self._stopped.is_set() or _cancelled(cancel_event):
            raise OperationCancelled("demo stopped; reconnect before another operation")

    def select(self, port, cancel_event=None):
        ValveProtocol().select(port)
        self._check(cancel_event)
        self._stopped.wait(.02)
        self._check(cancel_event)
        self.port = port

    def read_status(self,cancel_event=None):
        self._check(cancel_event)

    def recover_after_stop(self):
        if not self.connected:raise HardwareError('Not connected')
        self._stopped.clear()

    def move(self, direction, steps, rpm, cancel_event=None):
        if direction not in ("aspirate", "dispense"):
            raise HardwareError("direction must be aspirate or dispense")
        PumpProtocol().move_steps("cw", steps)
        PumpProtocol().set_speed(rpm)
        self._check(cancel_event)
        if self.port is None:
            raise HardwareError("select a port first")
        self._stopped.wait(.02)
        self._check(cancel_event)
        self.moves.append({"port": self.port, "direction": direction, "steps": steps, "rpm": rpm})

    def stop(self):
        self._stopped.set()
        self.port = None

    def move_turns(self,direction,turns,rpm,cancel_event=None):
        PumpProtocol().move_turns('cw',turns);PumpProtocol().set_speed(rpm)
        self._check(cancel_event)
        if self.port is None:raise HardwareError('select a port first')
        self.moves.append({'port':self.port,'direction':direction,'turns':turns,'rpm':rpm})
