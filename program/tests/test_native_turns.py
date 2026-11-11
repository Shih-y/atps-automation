"""Documented native-turn commands exercised with fake serial only.

These tests do not establish native-turn firmware behavior on the real pump.
"""
import copy
import threading
import time
import unittest

from protocols import PumpProtocol
from transport import HardwareError, OperationCancelled, SerialHardware
from test_shared_transport import FakeBus
from test_transport import CONFIG, FakePort, reply


class NativeTurnPort(FakePort):
    def __init__(self):
        super().__init__('pump')
        self.turn_replies = [(4, 1), (0, 0)]
        self.turn_query_results = []
        self.turn_ack = 254

    def write(self, data):
        function = data[2]
        if function not in (0x44, 0x45, 0x46):
            return super().write(data)
        self.writes.append(bytes(data))
        if function in (0x44, 0x45):
            self.remaining = int.from_bytes(data[3:7], 'little')
            self.motion_started.set()
            if self.no_reply_to_move:
                return len(data)
            state, result = self.turn_ack, 0
        else:
            if self.endless_motion:
                state, result = 4, self.remaining
            else:
                state, result = self.turn_replies.pop(0)
            self.turn_query_results.append((state, result))
            self.remaining = result
        self.buffer.extend(reply(1, state, result, 4))
        return len(data)


class NativeTurnBus(FakeBus):
    def __init__(self):
        super().__init__()
        self.devices[1] = NativeTurnPort()

    def write(self, data):
        result = super().write(data)
        if data[1] == 1 and self.buffer:
            state = {0x20: 0, 0x21: 4}.get(self.buffer[2], self.buffer[2])
            if data[2] in (0x40, 0x41, 0x49):
                state = 254
            parameter = int.from_bytes(self.buffer[3:7], 'little')
            self.buffer[:] = reply(1, state, parameter, 4)
        return result


class NativeTurnProtocolTests(unittest.TestCase):
    def test_manual_native_turn_frames_use_unsigned_little_endian_turns(self):
        pump = PumpProtocol(1)
        self.assertEqual(pump.move_turns('cw', 20).hex(), 'cc014414000000dd0202')
        self.assertEqual(pump.move_turns('ccw', 0x12345678).hex(), 'cc014578563412dd0303')
        self.assertEqual(pump.move_turns('cw', 0xFFFFFFFF).hex(), 'cc0144ffffffffddea05')
        self.assertEqual(pump.query_remaining_turns().hex(), 'cc014600000000ddf001')

    def test_turn_count_is_positive_integer_and_direction_is_motor_direction(self):
        pump = PumpProtocol(1)
        for turns in (0, -1, 2**32, True, 1.0, 1.2, '2', None):
            with self.subTest(turns=turns), self.assertRaises(ValueError):
                pump.move_turns('cw', turns)
        for direction in ('aspirate', 'CW', 'forward', 1, None):
            with self.subTest(direction=direction), self.assertRaises(ValueError):
                pump.move_turns(direction, 1)


class NativeTurnTransportTests(unittest.TestCase):
    def setUp(self):
        config = copy.deepcopy(CONFIG)
        config['valve']['address'] = 2
        config['pump'].update(status_ok=[0], status_busy=[4], status_ack=[254])
        self.bus = NativeTurnBus()
        self.pump = self.bus.devices[1]
        self.logs = []
        self.hw = SerialHardware(config, 'COM_TEST', 'COM_TEST', logger=self.logs.append,
                                 serial_factory=lambda **kwargs: self.bus)
        self.hw.connect()

    def tearDown(self):
        self.hw.close()

    def test_native_completion_polls_turns_and_preserves_verified_speed_and_valve(self):
        self.hw.select(16)
        self.hw.move_turns('aspirate', 20, 50)
        self.assertEqual(self.pump.turn_query_results, [(4, 1), (0, 0)])
        self.assertEqual([f[2] for f in self.pump.writes],
                         [0x49, 0x4A, 0x4A, 0x25, 0x4B, 0x4C, 0x44, 0x46, 0x46])
        self.assertEqual(self.pump.writes[-3].hex(), 'cc014414000000dd0202')
        self.assertEqual(self.pump.rpm, 500)
        self.assertEqual(self.hw.port, 16)
        self.assertFalse(self.bus.collision)
        completion = [item for item in self.logs if item['event'] == 'finite_move_completed']
        self.assertEqual(completion[-1]['turns'], 20)

    def test_direction_mapping_uses_physical_aspirate_configuration(self):
        self.hw.select(1)
        for aspirate_direction, direction, function in (
                ('cw', 'aspirate', 0x44), ('cw', 'dispense', 0x45),
                ('ccw', 'aspirate', 0x45), ('ccw', 'dispense', 0x44)):
            with self.subTest(physical=aspirate_direction, direction=direction):
                self.hw.config['pump']['aspirate_direction'] = aspirate_direction
                self.pump.turn_replies = [(0, 0)]
                self.hw.move_turns(direction, 1, 1)
                self.assertEqual(self.pump.writes[-2][2], function)

    def test_busy_zero_and_idle_nonzero_both_wait_for_idle_zero(self):
        self.hw.select(1)
        self.pump.turn_replies = [(4, 0), (0, 1), (0, 0)]
        self.hw.move_turns('aspirate', 1, 1)
        self.assertEqual(self.pump.turn_query_results, [(4, 0), (0, 1), (0, 0)])

    def test_pending_ack_is_never_accepted_as_query_completion(self):
        self.hw.select(1)
        self.pump.turn_replies = [(254, 0)]
        with self.assertRaisesRegex(HardwareError, 'unconfirmed pump state'):
            self.hw.move_turns('aspirate', 1, 1)
        self.assertEqual(self.pump.writes[-1][2], 0x49)
        self.assertFalse(any(item['event'] == 'finite_move_completed' for item in self.logs))

    def test_busy_motion_ack_fails_without_completion_poll_or_retry(self):
        self.hw.select(1)
        self.pump.turn_ack = 4
        with self.assertRaisesRegex(HardwareError, 'not accepted'):
            self.hw.move_turns('aspirate', 1, 1)
        self.assertEqual(sum(f[2] == 0x44 for f in self.pump.writes), 1)
        self.assertEqual(self.pump.turn_query_results, [])

    def test_response_timeout_stops_and_latches_without_repeating_turns(self):
        self.hw.select(1)
        self.pump.no_reply_to_move = True
        with self.assertRaisesRegex(HardwareError, 'NOT retried'):
            self.hw.move_turns('aspirate', 1, 1)
        self.assertEqual(sum(f[2] == 0x44 for f in self.pump.writes), 1)
        self.assertEqual([(f[1], f[2]) for f in self.bus.writes[-2:]], [(1, 0x49), (2, 0x49)])
        with self.assertRaises(OperationCancelled):
            self.hw.move_turns('aspirate', 1, 1)

    def test_completion_timeout_stops_even_if_remaining_turns_stay_nonzero(self):
        self.hw.select(1)
        self.hw.config['pump']['step_timeout'] = .02
        self.pump.endless_motion = True
        with self.assertRaisesRegex(HardwareError, 'completion timeout'):
            self.hw.move_turns('aspirate', 1, 1)
        self.assertEqual(sum(f[2] == 0x44 for f in self.pump.writes), 1)
        self.assertEqual(self.pump.writes[-1][2], 0x49)

    def test_priority_stop_interrupts_native_motion_and_blocks_future_commands(self):
        self.hw.select(1)
        self.pump.endless_motion = True
        errors = []
        def move():
            try:
                self.hw.move_turns('aspirate', 100, 1)
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=move)
        worker.start()
        try:
            self.assertTrue(self.pump.motion_started.wait(.5))
            started = time.monotonic()
            self.hw.stop()
            worker.join(1)
            self.assertLess(time.monotonic() - started, 1)
            self.assertFalse(worker.is_alive())
            self.assertIsInstance(errors[0], OperationCancelled)
            self.assertEqual([(f[1], f[2]) for f in self.bus.writes[-2:]], [(1, 0x49), (2, 0x49)])
            with self.assertRaises(OperationCancelled):
                self.hw.select(2)
        finally:
            self.hw.stop()
            worker.join(1)

    def test_invalid_count_or_configured_speed_limit_transmits_nothing(self):
        for turns, rpm in ((0, 1), (1.5, 1), (True, 1), (2**32, 1), (1, 101)):
            with self.subTest(turns=turns, rpm=rpm), self.assertRaises(HardwareError):
                self.hw.move_turns('aspirate', turns, rpm)
        self.assertEqual(self.bus.writes, [])

    def test_missing_or_changed_selected_valve_blocks_native_motion(self):
        with self.assertRaises(HardwareError):
            self.hw.move_turns('aspirate', 1, 1)
        self.assertFalse(any(f[2] in (0x44, 0x45) for f in self.pump.writes))
        self.hw.close()
        self.hw.connect()
        self.hw.select(1)
        self.bus.devices[2].bad_position = True
        with self.assertRaises(HardwareError):
            self.hw.move_turns('aspirate', 1, 1)
        self.assertFalse(any(f[2] in (0x44, 0x45) for f in self.pump.writes))

    def test_failed_speed_readback_blocks_native_motion(self):
        self.hw.select(1)
        self.pump.bad_speed_readback = True
        with self.assertRaisesRegex(HardwareError, 'readback'):
            self.hw.move_turns('aspirate', 1, 1)
        self.assertFalse(any(f[2] in (0x44, 0x45) for f in self.pump.writes))


if __name__ == '__main__':
    unittest.main()
