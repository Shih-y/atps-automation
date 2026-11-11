"""Golden-frame and failure tests. No serial port is opened by these tests."""
import unittest

from protocols import (
    DeviceStateError, ProtocolError, PumpProtocol, Reply, ValveProtocol,
    ValveStatus, is_pending, validate_valve_ready,
)


class PumpProtocolTests(unittest.TestCase):
    def setUp(self):
        self.pump = PumpProtocol(1)

    def test_finite_move_uses_four_little_endian_parameter_bytes(self):
        self.assertEqual(self.pump.move_steps("cw", 200).hex(), "cc0140c8000000ddb202")
        self.assertEqual(self.pump.move_steps("ccw", 0x12345678).hex(), "cc014178563412ddff02")

    def test_speed_is_tenths_of_rpm_not_ieee_float_or_modbus(self):
        self.assertEqual(self.pump.set_speed(100).hex(), "cc014be8030000dde002")
        self.assertEqual(self.pump.set_speed(0.1)[3:7], bytes.fromhex("01000000"))
        self.assertEqual(self.pump.set_speed(400)[3:7], bytes.fromhex("a00f0000"))

    def test_stop_and_queries(self):
        vectors = [
            (self.pump.stop(), "cc014900000000ddf301"),
            (self.pump.query_remaining_steps(), "cc014a00000000ddf401"),
            (self.pump.query_speed(), "cc014c00000000ddf601"),
            (self.pump.query_max_speed(), "cc012500000000ddcf01"),
        ]
        for actual, expected in vectors:
            self.assertEqual(actual.hex(), expected)

    def test_reply_preserves_undocumented_status_without_guessing(self):
        result = self.pump.decode(bytes.fromhex("cc01a578563412dd6303"))
        self.assertEqual(result, Reply(1, 0xA5, 0x12345678))
        self.assertFalse(hasattr(result, "ok"))

    def test_non_unicast_addresses_rejected(self):
        for value in (0, 248, 255, -1, True, 1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PumpProtocol(value)

    def test_invalid_motion_and_speed_rejected(self):
        for steps in (0, -1, 2**32, True, 1.2):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                self.pump.move_steps("cw", steps)
        for direction in ("forward", "reverse", "CW", 1, None):
            with self.subTest(direction=direction), self.assertRaises(ValueError):
                self.pump.move_steps(direction, 10)
        for rpm in (0, -1, 400.1, 1.25, float("nan"), float("inf"), True, "20"):
            with self.subTest(rpm=rpm), self.assertRaises(ValueError):
                self.pump.set_speed(rpm)


class ValveProtocolTests(unittest.TestCase):
    def setUp(self):
        self.valve = ValveProtocol(address=0, ports=16)

    def test_address_zero_is_valid_and_motion_frames_match_document_format(self):
        vectors = [
            (self.valve.select(16), "cc00441000ddfd01"),
            (self.valve.home(), "cc00450000ddee01"),
            (self.valve.stop(), "cc00490000ddf201"),
            (self.valve.query_status(), "cc004a0000ddf301"),
            (self.valve.query_position(), "cc003e0000dde701"),
        ]
        for actual, expected in vectors:
            self.assertEqual(actual.hex(), expected)

    def test_pending_is_not_ready_and_position_requires_match(self):
        pending = self.valve.decode(bytes.fromhex("cc00fe0000dda702"))
        self.assertTrue(is_pending(pending.status))
        self.assertTrue(is_pending(ValveStatus.BUSY))
        self.assertFalse(is_pending(ValveStatus.NORMAL))
        with self.assertRaises(DeviceStateError):
            validate_valve_ready(pending)
        ready = self.valve.decode(bytes.fromhex("cc00001000ddb901"))
        self.assertIsNone(validate_valve_ready(ready, expected_port=16))
        with self.assertRaises(DeviceStateError):
            validate_valve_ready(ready, expected_port=1)

    def test_every_error_and_unknown_status_blocks_readiness(self):
        for status in (1, 2, 3, 4, 5, 6, 0xFE, 0xFF, 0x77):
            with self.subTest(status=status), self.assertRaises(DeviceStateError):
                validate_valve_ready(Reply(0, status, 1), expected_port=1)

    def test_ports_and_multicast_are_rejected(self):
        for port in (0, 17, -1, True, 2.0):
            with self.subTest(port=port), self.assertRaises(ValueError):
                self.valve.select(port)
        for address in (-1, 128, 255, True):
            with self.subTest(address=address), self.assertRaises(ValueError):
                ValveProtocol(address)


class FrameValidationTests(unittest.TestCase):
    def test_corruption_wrong_address_and_wrong_lengths_are_rejected(self):
        cases = [
            (PumpProtocol(1), bytes.fromhex("cc010000000000ddaa01")),
            (ValveProtocol(0), bytes.fromhex("cc00000000dda901")),
        ]
        for codec, good in cases:
            self.assertEqual(codec.response_length, len(good))
            self.assertEqual(codec.decode(good).parameter, 0)
            corruptions = [good[:-1], good + b"\x00", b"", good[1:],
                           bytes([0]) + good[1:], good[:-3] + b"\x00" + good[-2:],
                           good[:-1] + bytes([good[-1] ^ 1])]
            for bad in corruptions:
                with self.subTest(codec=codec, frame=bad), self.assertRaises(ProtocolError):
                    codec.decode(bad)
            wrong = bytearray(good)
            wrong[1] += 1
            wrong[-2:] = sum(wrong[:-2]).to_bytes(2, "little")
            with self.assertRaises(ProtocolError):
                codec.decode(wrong)

    def test_only_binary_frames_accepted(self):
        with self.assertRaises(ProtocolError):
            PumpProtocol().decode("cc010000000000ddaa01")

    def test_parameter_width_and_function_byte_validation(self):
        for codec, maximum in ((PumpProtocol(), 2**32 - 1), (ValveProtocol(), 65535)):
            for value in (-1, maximum + 1, True, 0.5):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    codec.encode(0x4A, value)
            for function in (-1, 256, True, 1.0):
                with self.subTest(function=function), self.assertRaises(ValueError):
                    codec.encode(function)


if __name__ == "__main__":
    unittest.main()
