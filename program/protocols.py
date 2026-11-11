"""RUNZE ordinary-frame codecs; no I/O and no implicit hardware assumptions.

Sources: LM60A V2.2 PDF pp.16-18; full-ceramic SV-07M V1.0 pp.11-15.
These are custom additive-checksum frames, NOT Modbus RTU or Modbus registers.
Only unicast ordinary frames are supported. Factory/protocol-switch commands
are deliberately not exposed as named operations.

Replies have a STATUS byte, not an echoed FUNCTION byte. A serial driver must
permit only one outstanding transaction per device, reject stale/unsolicited
data, enforce deadlines, and never retry a timed-out movement automatically.
LM60A's supplied manual does not define status values: Reply never interprets
pump status as success. Host software needs a separately verified status map.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import IntEnum


class ProtocolError(ValueError):
    """A received frame has invalid structure, checksum, or device address."""


class DeviceStateError(ProtocolError):
    """A valid valve reply does not establish the required ready state."""


@dataclass(frozen=True)
class Reply:
    address: int
    status: int
    parameter: int


class ValveStatus(IntEnum):
    NORMAL = 0x00
    FRAME_ERROR = 0x01
    PARAMETER_ERROR = 0x02
    OPTOCOUPLER_ERROR = 0x03
    BUSY = 0x04
    STALLED = 0x05
    UNKNOWN_POSITION = 0x06
    PENDING = 0xFE
    UNKNOWN_ERROR = 0xFF


def _integer(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _frame(address, function, parameter, parameter_bytes):
    _integer(function, 0, 255, "function")
    _integer(parameter, 0, (1 << (8 * parameter_bytes)) - 1, "parameter")
    body = bytes((0xCC, address, function)) + parameter.to_bytes(parameter_bytes, "little") + b"\xDD"
    return body + (sum(body) & 0xFFFF).to_bytes(2, "little")


def _parse(data, address, parameter_bytes):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ProtocolError("response must be binary bytes")
    data = bytes(data)
    expected_length = parameter_bytes + 6
    if len(data) != expected_length:
        raise ProtocolError(f"expected {expected_length} response bytes, received {len(data)}")
    if data[0] != 0xCC or data[-3] != 0xDD:
        raise ProtocolError("invalid CC header or DD tail")
    if int.from_bytes(data[-2:], "little") != sum(data[:-2]) & 0xFFFF:
        raise ProtocolError("additive checksum mismatch")
    if data[1] != address:
        raise ProtocolError(f"response address {data[1]} differs from configured address {address}")
    return Reply(data[1], data[2], int.from_bytes(data[3:-3], "little"))


class PumpProtocol:
    """LM60A 10-byte ordinary frames. ``cw``/``ccw`` are motor directions.

    Motor direction is NOT a synonym for aspirate/dispense: determine the
    physical mapping using water after mounting the pump tube. A command step
    has no documented revolution conversion; calibrate mL per command step.
    The 400 rpm protocol limit does not override the fitted head/tube limit.
    """
    response_length = 10

    def __init__(self, address=1):
        self.address = _integer(address, 1, 247, "pump unicast address")

    def encode(self, function, parameter=0):
        """Encode an ordinary command; host must authorize the function."""
        return _frame(self.address, function, parameter, 4)

    def decode(self, data):
        """Return raw status. This does not mean the pump accepted a command."""
        return _parse(data, self.address, 4)

    def move_steps(self, direction, steps):
        _integer(steps, 1, 0xFFFFFFFF, "steps")
        if direction not in ("cw", "ccw"):
            raise ValueError("direction must be 'cw' or 'ccw'")
        # 40/41 have no automatic suckback; 42/43 are intentionally avoided.
        return self.encode(0x40 if direction == "cw" else 0x41, steps)

    def move_turns(self, direction, turns):
        """Manual p.18: finite integer turns, without automatic suckback."""
        _integer(turns, 1, 0xFFFFFFFF, "turns")
        if direction not in ("cw", "ccw"):
            raise ValueError("direction must be 'cw' or 'ccw'")
        return self.encode(0x44 if direction == "cw" else 0x45, turns)

    def set_speed(self, rpm):
        if isinstance(rpm, bool) or not isinstance(rpm, (int, float, Decimal)):
            raise ValueError("rpm must be a finite number, 0.1 to 400 in 0.1 increments")
        try:
            value = Decimal(str(rpm))
            if not value.is_finite() or not Decimal("0.1") <= value <= Decimal("400"):
                raise ValueError("rpm must be in [0.1, 400]")
            tenths = value * 10
            if tenths != tenths.to_integral_value():
                raise ValueError("rpm must use exact 0.1 increments")
        except InvalidOperation as exc:
            raise ValueError("rpm must be finite") from exc
        return self.encode(0x4B, int(tenths))

    def stop(self):
        return self.encode(0x49)

    def query_remaining_steps(self):
        return self.encode(0x4A)

    def query_remaining_turns(self):
        return self.encode(0x46)

    def query_speed(self):
        return self.encode(0x4C)

    def query_max_speed(self):
        return self.encode(0x25)


class ValveProtocol:
    """SV07M RUNZE custom protocol, ordinary 8-byte unicast frames.

    Do not move the valve while the pump is running. After select(), wait for
    query_status() NORMAL and independently verify query_position() matches.
    A FE response only acknowledges that execution is pending/in progress.
    """
    response_length = 8

    def __init__(self, address=0, ports=16):
        self.address = _integer(address, 0, 127, "valve unicast address")
        self.ports = _integer(ports, 1, 16, "installed valve port count")

    def encode(self, function, parameter=0):
        """Encode an ordinary command; host must authorize the function."""
        return _frame(self.address, function, parameter, 2)

    def decode(self, data):
        return _parse(data, self.address, 2)

    def select(self, port):
        return self.encode(0x44, _integer(port, 1, self.ports, "target valve port"))

    def home(self):
        # Homing location is not assumed to be an open numbered liquid port.
        return self.encode(0x45)

    def stop(self):
        return self.encode(0x49)

    def query_position(self):
        return self.encode(0x3E)

    def query_status(self):
        return self.encode(0x4A)


def is_pending(status):
    """Valve-only helper; never apply this map to LM60A reply statuses."""
    return status in (ValveStatus.PENDING, ValveStatus.BUSY)


def validate_valve_ready(reply, expected_port=None):
    """Raise unless a valve status/position reply meets the ready condition.

    Use expected_port only with a fresh response to query_position(). Since
    replies do not echo function, the codec cannot establish that association.
    """
    if reply.status != ValveStatus.NORMAL:
        try:
            reason = ValveStatus(reply.status).name
        except ValueError:
            reason = "UNDOCUMENTED_STATUS"
        raise DeviceStateError(f"valve not ready: {reason} (0x{reply.status:02X})")
    if expected_port is not None:
        _integer(expected_port, 1, 16, "expected valve port")
        if reply.parameter != expected_port:
            raise DeviceStateError(f"valve position {reply.parameter} differs from target {expected_port}")
