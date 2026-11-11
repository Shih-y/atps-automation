"""Deterministic fake-serial verification; no real hardware is opened."""
import copy
import threading
import time
import unittest

from transport import HardwareError, OperationCancelled, SerialHardware
from fakes import DemoHardware


CONFIG = {
    "pump": {"address": 1, "baud": 9600, "aspirate_direction": "cw",
             "status_ok": [0x20], "status_busy": [0x21],
             "status_mapping_note": "TEST FIXTURE ONLY; not real LM60A statuses",
             "max_rpm": 100, "step_timeout": .5, "transaction_timeout": .05,
             "poll_interval": .001},
    "valve": {"address": 0, "baud": 9600, "timeout": .5,
              "transaction_timeout": .05, "poll_interval": .001},
}


def reply(address, state, parameter, width):
    body = bytes([0xCC, address, state]) + parameter.to_bytes(width, "little") + b"\xDD"
    return body + sum(body).to_bytes(2, "little")


class FakePort:
    def __init__(self, kind, **kwargs):
        self.kind, self.kwargs = kind, kwargs
        self.buffer = bytearray()
        self.writes = []
        self.closed = False
        self.port = 1
        self.rpm = 10
        self.remaining = 0
        self.busy_once = False
        self.bad_position = False
        self.no_reply_to_move = False
        self.unknown_pump_state = False
        self.bad_speed_readback = False
        self.endless_motion = False
        self.motion_started = threading.Event()

    @property
    def in_waiting(self):
        return len(self.buffer)

    def reset_input_buffer(self):
        self.buffer.clear()

    def read(self, count):
        if not self.buffer:
            time.sleep(.001)
            return b""
        # Fragment every reply to exercise accumulation across partial reads.
        data = self.buffer[:min(count, 3)]
        del self.buffer[:len(data)]
        return bytes(data)

    def write(self, data):
        self.writes.append(bytes(data))
        func = data[2]
        param = int.from_bytes(data[3:-3], "little")
        if self.kind == "pump":
            state, result = 0x20, 0
            if func == 0x25:
                result = 3000
            elif func == 0x4B:
                self.rpm = param
            elif func == 0x4C:
                result = self.rpm + int(self.bad_speed_readback)
            elif func in (0x40, 0x41):
                self.remaining = param
                self.motion_started.set()
                state = 0x20  # command accepted; later queries expose busy/remaining
                if self.no_reply_to_move:
                    return len(data)
            elif func == 0x49:
                self.remaining = 0
            elif func == 0x4A:
                if self.remaining:
                    state, result = 0x21, self.remaining
                    if not self.endless_motion:
                        self.remaining = 0
            if self.unknown_pump_state:
                state = 0x77
            self.buffer.extend(reply(1, state, result, 4))
        else:
            state, result = 0, 0
            if func == 0x44:
                self.port, self.busy_once, state = param, True, 0xFE
            elif func == 0x4A and self.busy_once:
                self.busy_once, state = False, 4
            elif func == 0x3E:
                result = self.port + int(self.bad_position)
            self.buffer.extend(reply(0, state, result, 2))
        return len(data)

    def close(self):
        self.closed = True


class SerialTests(unittest.TestCase):
    def setUp(self):
        self.ports = {}
        def factory(**kwargs):
            kind = "pump" if kwargs["port"] == "COM_TEST_PUMP" else "valve"
            item = FakePort(kind, **kwargs)
            self.ports[kind] = item
            return item
        self.config = copy.deepcopy(CONFIG)
        self.hardware = SerialHardware(self.config, "COM_TEST_PUMP", "COM_TEST_VALVE", serial_factory=factory)
        self.hardware.connect()

    def tearDown(self):
        self.hardware.close()

    def test_connect_does_not_move_and_uses_independent_bounded_serial_ports(self):
        for item in self.ports.values():
            self.assertEqual(item.writes, [])
            self.assertEqual(item.kwargs["timeout"], .2)
            self.assertEqual(item.kwargs["write_timeout"], .2)
            self.assertEqual(item.kwargs["bytesize"], 8)
            self.assertEqual(item.kwargs["parity"], "N")

    def test_select_requires_pump_idle_then_poll_and_exact_position(self):
        self.hardware.select(16, threading.Event())
        self.assertEqual(self.hardware.port, 16)
        self.assertEqual([f[2] for f in self.ports["pump"].writes], [0x49, 0x4A])
        self.assertEqual([f[2] for f in self.ports["valve"].writes], [0x44, 0x4A, 0x4A, 0x3E])

    def test_finite_move_maps_direction_checks_speed_and_waits_remaining_zero(self):
        self.hardware.select(1, threading.Event())
        self.hardware.move("dispense", 123, 20, threading.Event())
        motions = [f for f in self.ports["pump"].writes if f[2] in (0x40, 0x41)]
        self.assertEqual(len(motions), 1)
        self.assertEqual(motions[0][2], 0x41)
        self.assertEqual(int.from_bytes(motions[0][3:7], "little"), 123)
        self.assertGreaterEqual([f[2] for f in self.ports["pump"].writes].count(0x4A), 4)
        self.assertFalse(any(f[2] in (0x42, 0x43, 0x47, 0x48) for f in self.ports["pump"].writes))

    def test_wrong_position_or_unknown_pump_state_aborts(self):
        self.ports["valve"].bad_position = True
        with self.assertRaises(HardwareError):
            self.hardware.select(2, threading.Event())
        self.assertEqual(self.ports["pump"].writes[-1][2], 0x49)
        self.assertEqual(self.ports["valve"].writes[-1][2], 0x49)
        with self.assertRaises(HardwareError):
            self.hardware.move("aspirate", 100, 10, threading.Event())

    def test_unknown_pump_status_not_interpreted_as_valve_status(self):
        self.ports["pump"].unknown_pump_state = True
        with self.assertRaises(HardwareError):
            self.hardware.select(1, threading.Event())
        self.assertFalse(any(f[2] == 0x44 for f in self.ports["valve"].writes))

    def test_speed_readback_mismatch_prevents_motion(self):
        self.hardware.select(1, threading.Event())
        self.ports["pump"].bad_speed_readback = True
        with self.assertRaises(HardwareError):
            self.hardware.move("aspirate", 50, 20, threading.Event())
        self.assertFalse(any(f[2] in (0x40, 0x41) for f in self.ports["pump"].writes))

    def test_timeout_never_retries_motion_and_latches_stop(self):
        self.hardware.select(1, threading.Event())
        self.ports["pump"].no_reply_to_move = True
        with self.assertRaises(HardwareError):
            self.hardware.move("aspirate", 50, 20, threading.Event())
        self.assertEqual(sum(f[2] == 0x40 for f in self.ports["pump"].writes), 1)
        with self.assertRaises(HardwareError):
            self.hardware.select(2, threading.Event())

    def test_stale_data_aborts_before_new_motion(self):
        self.ports["valve"].buffer.extend(reply(0, 0, 1, 2))
        with self.assertRaises(HardwareError):
            self.hardware.select(2, threading.Event())
        self.assertFalse(any(f[2] == 0x44 for f in self.ports["valve"].writes))

    def test_missing_status_confirmation_blocks_before_any_command(self):
        for field, value in (("status_ok", []), ("status_busy", [True]), ("status_mapping_note", "")):
            original = self.hardware.config["pump"][field]
            self.hardware.config["pump"][field] = value
            with self.subTest(field=field), self.assertRaises(HardwareError):
                self.hardware.select(1, threading.Event())
            self.hardware.config["pump"][field] = original
        self.assertEqual(self.ports["pump"].writes, [])

    def test_unobserved_busy_map_can_be_empty_but_unknown_status_still_faults(self):
        from protocols import Reply
        self.hardware.config['pump']['status_busy'] = []
        self.hardware._check_config()
        self.hardware._pump_state(Reply(1, 0x20, 0), require_idle=True)
        with self.assertRaises(HardwareError):
            self.hardware._pump_state(Reply(1, 0x21, 5))

    def test_normal_status_with_remaining_steps_is_not_completion(self):
        from protocols import Reply
        self.hardware.config['pump']['status_busy'] = []
        responses = iter([Reply(1, 0x20, 8), Reply(1, 0x20, 0)])
        seen = []
        def exchange(*args):
            item = next(responses)
            seen.append(item.parameter)
            return item
        self.hardware._exchange = exchange
        self.hardware._wait_pump_idle(threading.Event())
        self.assertEqual(seen, [8, 0])

    def test_emergency_stop_does_not_wait_for_whole_motion(self):
        self.hardware.select(1, threading.Event())
        self.ports["pump"].endless_motion = True
        errors = []
        def operation():
            try:
                self.hardware.move("aspirate", 1000, 10, threading.Event())
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=operation)
        worker.start()
        self.assertTrue(self.ports["pump"].motion_started.wait(.5))
        started = time.monotonic()
        self.hardware.stop()
        worker.join(.3)
        self.assertLess(time.monotonic() - started, .3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        self.assertIsInstance(errors[0], OperationCancelled)
        self.assertEqual(self.ports["pump"].writes[-1][2], 0x49)


class DemoTests(unittest.TestCase):
    def test_demo_has_same_actions_and_cancellation_without_hardware(self):
        demo = DemoHardware(CONFIG)
        demo.connect()
        event = threading.Event()
        demo.select(11, event)
        demo.move("aspirate", 200, 10, event)
        self.assertEqual(demo.moves[-1], {"port": 11, "direction": "aspirate", "steps": 200, "rpm": 10})
        event.set()
        with self.assertRaises(OperationCancelled):
            demo.move("dispense", 20, 10, event)
        demo.close()


if __name__ == "__main__":
    unittest.main()
