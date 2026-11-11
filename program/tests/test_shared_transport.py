"""Shared COM6 tests use a fake half-duplex bus only; no physical port is opened."""
import copy
import threading
import time
import unittest

from protocols import ProtocolError
from transport import HardwareError, OperationCancelled, SerialHardware
from test_transport import CONFIG, FakePort, reply


class FakeBus:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.devices = {1: FakePort('pump'), 2: FakePort('valve')}
        self.buffer = bytearray()
        self.writes = []
        self.close_count = 0
        self.reset_count = 0
        self.collision = False
        self.bad_address = False
        self.bad_checksum = False
        self.read_delay = 0

    @property
    def in_waiting(self): return len(self.buffer)

    def reset_input_buffer(self):
        self.reset_count += 1
        self.buffer.clear()

    def read(self, count):
        time.sleep(self.read_delay or .001)
        data = self.buffer[:min(3, count)]
        del self.buffer[:len(data)]
        return bytes(data)

    def write(self, data):
        if self.buffer: self.collision = True
        self.writes.append(bytes(data))
        device = self.devices[data[1]]
        device.write(data)
        if device.buffer:
            raw = device.buffer
            width = 4 if data[1] == 1 else 2
            address = 3 if self.bad_address else data[1]
            state = raw[2]
            result = int.from_bytes(raw[3:3+width], 'little')
            raw = bytearray(reply(address, state, result, width))
            if self.bad_checksum: raw[-1] ^= 0x01
            self.buffer.extend(raw)
            device.buffer.clear()
        return len(data)

    def close(self): self.close_count += 1


class SharedSerialTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(CONFIG)
        self.config['valve']['address'] = 2
        self.opened = []
        def factory(**kwargs):
            bus = FakeBus(**kwargs)
            self.opened.append(bus)
            return bus
        self.factory = factory
        self.hw = SerialHardware(self.config, 'COM6', 'com6', serial_factory=factory)

    def tearDown(self): self.hw.close()

    def test_one_open_one_shared_lock_and_no_startup_transmission(self):
        self.config['pump']['status_ok'] = []
        self.hw = SerialHardware(self.config, ' COM6 ', r'\\.\COM6', serial_factory=self.factory)
        self.hw.connect()
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(self.opened[0].kwargs['port'], 'COM6')
        self.assertEqual(self.opened[0].reset_count, 1)
        self.assertEqual(self.opened[0].writes, [])
        self.assertIs(self.hw._links['pump'].serial, self.hw._links['valve'].serial)
        self.assertIs(self.hw._links['pump'].lock, self.hw._links['valve'].lock)

    def test_shared_bus_routes_two_reply_widths_by_address(self):
        self.hw.connect()
        self.hw.select(14)
        self.hw.move('aspirate', 40, 1)
        self.assertEqual(self.hw.port, 14)
        bus = self.opened[0]
        self.assertFalse(bus.collision)
        self.assertEqual({frame[1] for frame in bus.writes}, {1, 2})
        self.assertEqual([len(f) for f in bus.writes if f[1] == 1], [10]*sum(f[1] == 1 for f in bus.writes))
        self.assertEqual([len(f) for f in bus.writes if f[1] == 2], [8]*sum(f[1] == 2 for f in bus.writes))

    def test_incompatible_baud_or_framing_fails_before_open(self):
        for key, value in [('baud', 19200), ('stopbits', 2)]:
            cfg = copy.deepcopy(self.config)
            cfg['valve'][key] = value
            h = SerialHardware(cfg, 'COM6', 'COM6', serial_factory=self.factory)
            with self.subTest(key=key), self.assertRaises(HardwareError): h.connect()
        self.assertEqual(self.opened, [])

    def test_overlapping_or_cross_protocol_broadcast_addresses_fail_before_open(self):
        for pump, valve in [(1, 1), (1, 0), (128, 2), (247, 2)]:
            cfg = copy.deepcopy(self.config)
            cfg['pump']['address'], cfg['valve']['address'] = pump, valve
            h = SerialHardware(cfg, 'COM6', 'COM6', serial_factory=self.factory)
            with self.subTest(pump=pump,valve=valve), self.assertRaises(HardwareError): h.connect()
        self.assertEqual(self.opened, [])

    def test_transactions_are_serialized_even_for_different_devices(self):
        self.hw.connect()
        bus = self.opened[0]
        bus.read_delay = .002
        replies, errors = [], []
        def query(name, frame):
            try: replies.append((name, self.hw._exchange(name, frame)))
            except Exception as exc: errors.append(exc)
        workers = [threading.Thread(target=query, args=('pump',self.hw.pump.query_remaining_steps())),
                   threading.Thread(target=query, args=('valve',self.hw.valve.query_status()))]
        for t in workers: t.start()
        for t in workers: t.join(.5)
        self.assertFalse(any(t.is_alive() for t in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(replies), 2)
        self.assertFalse(bus.collision)

    def test_bad_address_or_checksum_does_not_become_success(self):
        for flag in ['bad_address', 'bad_checksum']:
            self.hw.connect()
            bus = self.opened[-1]
            setattr(bus, flag, True)
            with self.subTest(flag=flag), self.assertRaises(HardwareError): self.hw.select(1)
            self.assertIsNone(self.hw.port)
            self.assertFalse(any(f[2] == 0x44 for f in bus.writes))
            self.hw.close()

    def test_close_closes_shared_handle_once_and_stops_each_device(self):
        self.hw.connect()
        bus = self.opened[0]
        self.hw.close()
        self.hw.close()
        self.assertEqual(bus.close_count, 1)
        self.assertEqual([(f[1], f[2]) for f in bus.writes], [(1, 0x49), (2, 0x49)])
        self.assertFalse(bus.collision)

    def test_shared_stop_interrupts_motion_and_latches_session(self):
        self.hw.connect()
        self.hw.select(1)
        bus = self.opened[0]
        bus.devices[1].endless_motion = True
        errors = []
        def motion():
            try: self.hw.move('aspirate', 300, 1)
            except Exception as exc: errors.append(exc)
        t = threading.Thread(target=motion)
        t.start()
        self.assertTrue(bus.devices[1].motion_started.wait(.5))
        started = time.monotonic()
        self.hw.stop()
        t.join(1)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic()-started, 1)
        self.assertIsInstance(errors[0], OperationCancelled)
        self.assertEqual([(f[1],f[2]) for f in bus.writes[-2:]], [(1,0x49),(2,0x49)])
        with self.assertRaises(OperationCancelled): self.hw.select(2)

    def test_partial_open_failure_closes_first_handle(self):
        opened = []
        def factory(**kwargs):
            if opened: raise OSError('second port not present')
            port = FakeBus(**kwargs)
            opened.append(port)
            return port
        h = SerialHardware(self.config, 'COM6', 'COM7', serial_factory=factory)
        with self.assertRaises(HardwareError): h.connect()
        self.assertEqual(opened[0].close_count, 1)
        self.assertFalse(h.connected)
        self.assertEqual(opened[0].writes, [])


if __name__ == '__main__': unittest.main()
