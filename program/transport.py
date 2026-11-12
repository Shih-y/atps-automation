"""Real hardware transport with finite motion and shared RS485 support.

Real serial access occurs ONLY in SerialHardware.connect(). pyserial is optional
until then. No probing, broadcast, factory writes or automatic motion retries.
8N1 is an initial configuration requiring bench confirmation (see findings).
The public stop is a software stop request, not a hardwired emergency circuit.
Priority stop latches interrupted I/O. A user stop can retain the open handle
after the old worker exits, stale replies are drained, and idle is queried.
Failed recovery leaves the fault latch set; no old motion is resumed.
"""
import copy
import json
import math
import re
import sys
import threading
import time
from pathlib import Path

from protocols import (
    ProtocolError, PumpProtocol, ValveProtocol, ValveStatus,
    is_pending, validate_valve_ready,
)


class HardwareError(RuntimeError):
    pass


class OperationCancelled(HardwareError):
    pass


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise HardwareError(f"{name} must be a positive finite number")
    return value


def _cancelled(event):
    return event is not None and event.is_set()


def _port_name(value):
    if not isinstance(value, str) or not value.strip():
        raise HardwareError("填写设备串口号，例如COM6")
    port = value.strip()
    if port.startswith('\\\\.\\'):
        port = port[4:]
    return port.upper() if re.fullmatch(r'COM\d+', port, re.IGNORECASE) else port


class _SerialLink:
    def __init__(self, serial, codec, settings, lock=None):
        self.serial, self.codec, self.settings = serial, codec, settings
        self.lock = lock if lock is not None else threading.Lock()


class SerialHardware:
    def __init__(self, config, pump_port, valve_port, logger=None, serial_factory=None):
        self.config = copy.deepcopy(config)
        self.pump_port, self.valve_port = pump_port, valve_port
        self.logger, self.serial_factory = logger, serial_factory
        self.pump = PumpProtocol(self.config.get("pump", {}).get("address", 1))
        self.valve = ValveProtocol(self.config.get("valve", {}).get("address", 0), ports=16)
        self.connected = False
        self.port = None
        self._links = {}
        self._action_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._stopped = threading.Event()
        self._stop_sent = False
        self._shared_bus = False
        self._telemetry_lock = threading.RLock()
        self._telemetry = self._empty_telemetry()

    @staticmethod
    def _empty_telemetry():
        return dict(pump_status='unknown',direction=None,flow=None,rpm=None,
                    remaining=None,unit=None,pump_at=None,valve_status='unknown',
                    valve_port=None,target_port=None,valve_at=None)

    def get_telemetry(self):
        with self._telemetry_lock:return copy.deepcopy(self._telemetry)

    def _update_telemetry(self, **values):
        with self._telemetry_lock:
            # Never overwrite a priority stop with an in-flight old reply.
            if self._stopped.is_set() and values.get('pump_status') not in ('unknown','stop_requested'):
                return
            self._telemetry={**self._telemetry,**values}

    def _log(self, event, **values):
        if self.logger is None:
            return
        record = {"time": time.time(), "event": event, **values}
        try:
            if callable(self.logger):
                self.logger(record)
            else:
                self.logger.info(json.dumps(record, ensure_ascii=False))
        except Exception:
            # Logging failure must never prevent a stop command.
            pass

    def connect(self):
        ports = {'pump': _port_name(self.pump_port), 'valve': _port_name(self.valve_port)}
        shared = ports['pump'].casefold() == ports['valve'].casefold()
        formats = {}
        for name in ('pump', 'valve'):
            settings = self.config.get(name, {})
            baud, stopbits = settings.get('baud', 9600), settings.get('stopbits', 1)
            if type(baud) is not int or baud not in (9600, 19200, 38400, 57600, 115200):
                raise HardwareError(f"unsupported configured {name} baud")
            if isinstance(stopbits, bool) or stopbits not in (1, 1.5, 2):
                raise HardwareError(f"invalid {name} stopbits")
            formats[name] = (baud, stopbits)
        if shared:
            if formats['pump'] != formats['valve']:
                raise HardwareError('共享RS485串口的泵和阀必须使用相同波特率、数据位、校验和停止位')
            # Pump address 0 is broadcast; valve addresses 128..254 are multicast.
            # Avoid addressing one device through the other protocol's group range.
            if not 1 <= self.pump.address <= 127 or not 1 <= self.valve.address <= 127:
                raise HardwareError('共享RS485总线两台设备地址均须为1—127；阀0与泵广播冲突，泵128以上与阀组播范围冲突')
            if self.pump.address == self.valve.address:
                raise HardwareError('共享RS485总线的泵和阀地址必须不同；程序不会自动修改设备地址')
        self.close()
        factory = self.serial_factory
        if factory is None:
            vendor = Path(__file__).with_name('vendor')
            if vendor.is_dir() and str(vendor) not in sys.path:
                sys.path.insert(0, str(vendor))
            try:
                import serial
            except ImportError as exc:
                raise HardwareError("实机通信需要pyserial，请先运行install_serial_windows.bat") from exc
            factory = serial.Serial
        links, opened = {}, {}
        try:
            for name, codec in (("pump", self.pump), ("valve", self.valve)):
                port = ports[name]
                settings = self.config.get(name, {})
                baud, stopbits = formats[name]
                key = port.casefold()
                if key not in opened:
                    stream = factory(port=port, baudrate=baud, bytesize=8, parity="N", stopbits=stopbits,
                                     timeout=.2, write_timeout=.2)
                    opened[key] = (stream, threading.Lock())
                    # Only session creation discards bytes before a normal transaction.
                    discarded = stream.in_waiting
                    stream.reset_input_buffer()
                    self._log("new_session", device=name, discarded_bytes=discarded,
                              port=port, baud=baud, framing=f"8N{stopbits}", shared_bus=shared)
                stream, lock = opened[key]
                links[name] = _SerialLink(stream, codec, settings, lock)
        except Exception as exc:
            for stream, _ in opened.values():
                try: stream.close()
                except Exception: pass
            raise HardwareError(f"serial connection failed: {exc}") from exc
        self._links = links
        self._shared_bus = shared
        self.connected = True
        self.port = None
        self._stop_sent = False
        self._stopped.clear()
        with self._telemetry_lock:self._telemetry=self._empty_telemetry()

    def close(self):
        if self.connected:
            try:
                self.stop()
            except HardwareError as exc:
                self._log("close_stop_unconfirmed", error=str(exc))
        closed = set()
        for link in self._links.values():
            if id(link.serial) in closed:
                continue
            closed.add(id(link.serial))
            try:
                link.serial.close()
            except Exception as exc:
                self._log("close_error", error=str(exc))
        self._links = {}
        self.connected = False
        self.port = None
        self._shared_bus = False

    def _check_interrupt(self, cancel_event=None):
        if self._stopped.is_set() or _cancelled(cancel_event):
            raise OperationCancelled("operation stopped; reconnect before running another hardware operation")

    def _check_config(self):
        cfg = self.config.get("pump", {})
        ok, busy = cfg.get("status_ok", []), cfg.get("status_busy", [])
        ack = cfg.get("status_ack", [])
        for name, states in (("status_ok", ok), ("status_busy", busy), ("status_ack", ack)):
            if not isinstance(states, list) or (name == "status_ok" and not states) or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 255 for v in states):
                raise HardwareError(f"verified LM60A {name} list is required; supplied manual does not define statuses")
        # An empty busy map does not authorize unknown statuses. Some firmware
        # may use the same normal status while remaining_steps > 0; completion
        # still requires a recognized normal status AND zero remaining steps.
        # Any other status continues to fault until separately verified.
        if set(ok) & set(busy):
            raise HardwareError("LM60A idle and busy state sets must be disjoint")
        if set(ack) & (set(ok) | set(busy)):
            raise HardwareError("LM60A pending acknowledgement must be separate from query states")
        note = cfg.get("status_mapping_note", "")
        if not isinstance(note, str) or not note.strip():
            raise HardwareError("record manufacturer/bench evidence in status_mapping_note before real motion")
        if cfg.get("aspirate_direction") not in ("cw", "ccw"):
            raise HardwareError("confirm physical aspirate_direction as cw or ccw using water")
        _positive(cfg.get("max_rpm", 100), "pump.max_rpm")
        _positive(cfg.get("step_timeout", 120), "pump.step_timeout")
        for name in ("pump", "valve"):
            settings = self.config.get(name, {})
            _positive(settings.get("transaction_timeout", 2), f"{name}.transaction_timeout")
            _positive(settings.get("poll_interval", .1), f"{name}.poll_interval")
        _positive(self.config.get("valve", {}).get("timeout", 10), "valve.timeout")

    def _exchange(self, name, frame, cancel_event=None):
        if not self.connected:
            raise HardwareError("serial hardware is not connected")
        self._check_interrupt(cancel_event)
        link = self._links[name]
        with link.lock:
            self._check_interrupt(cancel_event)
            if link.serial.in_waiting:
                raise HardwareError(f"{name}: stale or unsolicited bytes before request; session aborted")
            self._log("tx", device=name, hex=frame.hex(" "))
            if link.serial.write(frame) != len(frame):
                raise HardwareError(f"{name}: incomplete serial write")
            deadline = time.monotonic() + link.settings.get("transaction_timeout", 2)
            received = bytearray()
            while len(received) < link.codec.response_length:
                self._check_interrupt(cancel_event)
                if time.monotonic() >= deadline:
                    self._log("rx_incomplete", device=name, hex=received.hex(" "))
                    raise HardwareError(f"{name}: response timeout; command is NOT retried")
                received.extend(link.serial.read(link.codec.response_length - len(received)))
            self._log("rx", device=name, hex=received.hex(" "))
            if link.serial.in_waiting:
                raise HardwareError(f"{name}: extra unsolicited reply bytes; session aborted")
            return link.codec.decode(received)

    def _pump_state(self, reply, require_idle=False):
        cfg = self.config["pump"]
        if reply.status not in cfg["status_ok"] + cfg["status_busy"]:
            raise HardwareError(f"undocumented/unconfirmed pump state 0x{reply.status:02X}")
        if require_idle and reply.status not in cfg["status_ok"]:
            raise HardwareError(f"pump not in verified normal state: 0x{reply.status:02X}")

    def _pump_ack(self, reply):
        # Observed on this LM60A: stop and finite motion return FE, while
        # subsequent 4A queries return 04/remaining or 00/zero. Acceptance
        # alone is never completion, and a busy query code is not acceptance.
        cfg = self.config['pump']
        if reply.status not in cfg['status_ok'] + cfg.get('status_ack', []):
            raise HardwareError(f"pump command not accepted: 0x{reply.status:02X}")

    def _pause(self, name, cancel_event):
        deadline = time.monotonic() + self.config.get(name, {}).get("poll_interval", .1)
        while time.monotonic() < deadline:
            self._check_interrupt(cancel_event)
            self._stopped.wait(min(.02, max(0, deadline - time.monotonic())))
        self._check_interrupt(cancel_event)

    def _wait_pump_idle(self, cancel_event, *, turns=False):
        deadline = time.monotonic() + self.config["pump"].get("step_timeout", 120)
        query = self.pump.query_remaining_turns if turns else self.pump.query_remaining_steps
        while True:
            state = self._exchange("pump", query(), cancel_event)
            self._pump_state(state)
            normal = state.status in self.config["pump"]["status_ok"]
            idle = normal and (not turns or state.status == 0) and state.parameter == 0
            values=dict(pump_status='stopped' if idle else 'running',
                        remaining=state.parameter,unit='turns' if turns else 'steps',pump_at=time.time())
            if idle:values.update(direction=None,flow=None,rpm=0)
            self._update_telemetry(**values)
            if normal and (not turns or state.status == 0) and state.parameter == 0:
                return
            if time.monotonic() >= deadline:
                unit = "turns" if turns else "steps"
                raise HardwareError(f"pump completion timeout; remaining {unit} or state not idle")
            self._pause("pump", cancel_event)

    def _perform(self, action, cancel_event):
        # Configuration omissions are reported before transmitting even a stop.
        self._check_config()
        with self._action_lock:
            try:
                self._check_interrupt(cancel_event)
                return action()
            except Exception as exc:
                try:
                    self.stop()
                except Exception as stop_error:
                    self._log("stop_unconfirmed", error=str(stop_error))
                if isinstance(exc, HardwareError):
                    raise
                raise HardwareError(str(exc)) from exc

    def read_status(self, cancel_event=None):
        """Explicit refresh, only query frames on success; does not move or select."""
        def action():
            steps=self._exchange('pump',self.pump.query_remaining_steps(),cancel_event)
            turns=self._exchange('pump',self.pump.query_remaining_turns(),cancel_event)
            self._pump_state(steps);self._pump_state(turns)
            idle=all(r.status in self.config['pump']['status_ok'] and r.parameter==0 for r in (steps,turns))
            speed=self._exchange('pump',self.pump.query_speed(),cancel_event);self._pump_state(speed)
            self._update_telemetry(pump_status='stopped' if idle else 'running',direction=None,flow=None,
                rpm=0 if idle else speed.parameter/10,remaining=0 if idle else None,unit=None,pump_at=time.time())
            status=self._exchange('valve',self.valve.query_status(),cancel_event)
            if is_pending(status.status):
                self._update_telemetry(valve_status='switching',valve_port=None,target_port=None,valve_at=time.time())
                self.port=None
            else:
                validate_valve_ready(status)
                position=self._exchange('valve',self.valve.query_position(),cancel_event)
                validate_valve_ready(position)
                if not 1<=position.parameter<=16:raise HardwareError('Invalid valve position readback')
                self.port=position.parameter
                self._update_telemetry(valve_status='confirmed',valve_port=position.parameter,target_port=None,valve_at=time.time())
            self._log('manual_status_refreshed',motion_sent=False)
        return self._perform(action,cancel_event)

    def select(self, port, cancel_event=None):
        command = self.valve.select(port)
        def action():
            self._update_telemetry(valve_status='switching',valve_port=None,target_port=port,valve_at=time.time())
            # Normal acknowledged stop, separate from public emergency stop().
            stopped = self._exchange("pump", self.pump.stop(), cancel_event)
            self._pump_ack(stopped)
            self._wait_pump_idle(cancel_event)
            ack = self._exchange("valve", command, cancel_event)
            if ack.status not in (ValveStatus.NORMAL, ValveStatus.PENDING):
                raise HardwareError(f"valve positioning was not accepted: state 0x{ack.status:02X}")
            self.port = None
            deadline = time.monotonic() + self.config.get("valve", {}).get("timeout", 10)
            while True:
                state = self._exchange("valve", self.valve.query_status(), cancel_event)
                if state.status == ValveStatus.NORMAL:
                    break
                if not is_pending(state.status):
                    validate_valve_ready(state)
                if time.monotonic() >= deadline:
                    raise HardwareError("valve positioning timeout")
                self._pause("valve", cancel_event)
            position = self._exchange("valve", self.valve.query_position(), cancel_event)
            validate_valve_ready(position, expected_port=port)
            self.port = port
            self._update_telemetry(valve_status='confirmed',valve_port=port,target_port=None,valve_at=time.time())
            self._log("valve_position_verified", port=port)
        return self._perform(action, cancel_event)

    def move(self, direction, steps, rpm, cancel_event=None):
        return self._move_finite(direction, steps, rpm, cancel_event, turns=False)

    def move_turns(self, direction, turns, rpm, cancel_event=None):
        """Run documented 44/45 finite turns and poll 46 for idle/zero.

        Native-turn commands are documented but not yet verified on this real
        device. They do not establish a steps/turn or a liquid-volume calibration.
        """
        return self._move_finite(direction, turns, rpm, cancel_event, turns=True)

    def _move_finite(self, direction, amount, rpm, cancel_event, *, turns):
        if direction not in ("aspirate", "dispense"):
            raise HardwareError("direction must be aspirate or dispense")
        physical = self.config.get("pump", {}).get("aspirate_direction", "")
        if direction == "dispense":
            physical = {"cw": "ccw", "ccw": "cw"}.get(physical, "")
        try:
            encode_motion = self.pump.move_turns if turns else self.pump.move_steps
            motion = encode_motion(physical, amount)
            speed_command = self.pump.set_speed(rpm)
        except ValueError as exc:
            raise HardwareError(str(exc)) from exc
        if rpm > self.config.get("pump", {}).get("max_rpm", 100):
            raise HardwareError("requested rpm exceeds configured pump/head/tube limit")
        def action():
            if self.port is None:
                raise HardwareError("verify a selected valve port before pump motion")
            validate_valve_ready(self._exchange("valve", self.valve.query_status(), cancel_event))
            validate_valve_ready(self._exchange("valve", self.valve.query_position(), cancel_event), self.port)
            self._wait_pump_idle(cancel_event)
            limit = self._exchange("pump", self.pump.query_max_speed(), cancel_event)
            self._pump_state(limit, require_idle=True)
            if limit.parameter <= 0 or rpm * 10 > limit.parameter:
                raise HardwareError("requested rpm exceeds device-reported maximum or maximum is invalid")
            self._pump_state(self._exchange("pump", speed_command, cancel_event), require_idle=True)
            speed = self._exchange("pump", self.pump.query_speed(), cancel_event)
            self._pump_state(speed, require_idle=True)
            if speed.parameter != int.from_bytes(speed_command[3:7], "little"):
                raise HardwareError("pump dynamic-speed readback does not match requested speed")
            self._pump_ack(self._exchange("pump", motion, cancel_event))
            self._update_telemetry(pump_status='starting',direction=physical,flow=direction,
                                   rpm=rpm,remaining=amount,unit='turns' if turns else 'steps',pump_at=time.time())
            self._wait_pump_idle(cancel_event, turns=turns)
            details = {"turns": amount, "motion_unit": "turns"} if turns else {"steps": amount}
            self._log("finite_move_completed", direction=direction, rpm=rpm, port=self.port, **details)
        return self._perform(action, cancel_event)

    def stop(self):
        """Priority best-effort stop; interrupts reads/polls, then latches session.

        Sending a frame does not prove physical stopping. Replies are not reused;
        recover_after_stop may clear the latch after the old task exits and fresh idle
        readback succeeds, without reopening the serial handle. Use independent hardware power isolation if
        a communication failure prevents physical stopping.
        """
        self._stopped.set()
        self._update_telemetry(pump_status='stop_requested',direction=None,flow=None,rpm=None,
                               remaining=None,pump_at=time.time(),valve_status='unknown',
                               valve_port=None,target_port=None,valve_at=time.time())
        self.port = None
        with self._stop_lock:
            if self._stop_sent or not self.connected:
                return
            self._stop_sent = True
            failures = []
            drained = False
            for name, command in (("pump", self.pump.stop()), ("valve", self.valve.stop())):
                link = self._links.get(name)
                if link is None:
                    continue
                try:
                    with link.lock:
                        if self._shared_bus and not drained:
                            # The interrupted request may have left a partial reply.
                            # Do not treat these bytes as a response to either stop.
                            self._drain_stopped_bus(link)
                            drained = True
                        self._log("stop_tx_unconfirmed", device=name, hex=command.hex(" "))
                        if link.serial.write(command) != len(command):
                            raise HardwareError("incomplete stop write")
                        if self._shared_bus:
                            # Give the addressed device a bounded response interval
                            # before putting the other stop frame on the same wires.
                            self._drain_stopped_bus(link)
                except Exception as exc:
                    failures.append(f"{name}: {exc}")
            if failures:
                raise HardwareError("stop could not be delivered to all devices: " + "; ".join(failures))

    def recover_after_stop(self):
        """Called only after the cancelled controller worker has exited. No reopen."""
        with self._action_lock:
            with self._stop_lock:
                if not self.connected:raise HardwareError('Serial connection is not open')
                for link in {id(v.serial):v for v in self._links.values()}.values():
                    self._drain_stopped_bus(link)
                self._stop_sent=False
                self._stopped.clear()
        try:
            self.read_status()
            state=self.get_telemetry()
            if state['pump_status']!='stopped' or state['valve_status']!='confirmed':
                raise HardwareError('Pump/valve stop could not be confirmed')
            self._log('stop_confirmed_connection_retained',serial_reopened=False)
        except Exception:
            self.stop()
            raise

    def _drain_stopped_bus(self, link):
        """Bounded best-effort drain while the session is latched.

        Drained bytes are NOT accepted as stop confirmation: an earlier reply may
        have arrived late. A quiet interval reduces reply overlap, but cannot
        establish physical stopping on an unresponsive or faulty bus.
        """
        started = time.monotonic()
        deadline = started + .25
        quiet_since = started
        count = 0
        while time.monotonic() < deadline:
            waiting = link.serial.in_waiting
            if waiting:
                chunk = link.serial.read(min(waiting, 256))
                count += len(chunk)
                quiet_since = time.monotonic()
            elif time.monotonic() - started >= .1 and time.monotonic() - quiet_since >= .02:
                break
            else:
                time.sleep(.002)
        if count:
            self._log('stopped_session_reply_discarded', bytes=count)

