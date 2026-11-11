"""Regression cases based on the 2026-11-09 dry hardware trace; fake serial only."""
import copy, threading, time, unittest
from protocols import Reply
from transport import SerialHardware, HardwareError
from test_transport import CONFIG, reply
from test_shared_transport import FakeBus

class ObservedBus(FakeBus):
    def write(self, data):
        result=super().write(data)
        if data[1]==1 and self.buffer:
            state={0x20:0,0x21:4}.get(self.buffer[2],self.buffer[2])
            if data[2] in (0x40,0x41,0x49):state=254
            param=int.from_bytes(self.buffer[3:7],'little')
            self.buffer[:]=reply(1,state,param,4)
        return result

class ObservedProtocolTests(unittest.TestCase):
    def setUp(self):
        self.cfg=copy.deepcopy(CONFIG)
        self.cfg['valve']['address']=2
        self.cfg['pump'].update(status_ok=[0],status_busy=[4],status_ack=[254])
        self.bus=ObservedBus()
        self.hw=SerialHardware(self.cfg,'COM6','COM6',serial_factory=lambda **kw:self.bus)
        self.hw.connect()
    def tearDown(self):self.hw.close()
    def test_pending_stop_and_motion_ack_then_busy_query_and_completion(self):
        self.hw.select(16)
        self.hw.move('aspirate',1,1)
        self.hw.select(1)
        self.hw.move('dispense',1,1)
        self.assertEqual(self.hw.port,1)
        self.assertEqual(self.bus.devices[1].remaining,0)
    def test_ack_code_is_not_a_query_state_or_completion(self):
        with self.assertRaises(HardwareError):self.hw._pump_state(Reply(1,254,0))
    def test_busy_code_is_not_command_acceptance(self):
        with self.assertRaises(HardwareError):self.hw._pump_ack(Reply(1,4,0))

class DelayedStopTests(unittest.TestCase):
    def test_stop_waits_for_delayed_reply_before_next_frame(self):
        cfg=copy.deepcopy(CONFIG);cfg['valve']['address']=2
        bus=FakeBus();times=[];timers=[];original=bus.write
        def write(data):
            result=original(data)
            raw=bytes(bus.buffer);bus.buffer.clear();times.append(time.monotonic())
            timer=threading.Timer(.03,lambda:bus.buffer.extend(raw))
            timers.append(timer);timer.start();return result
        bus.write=write
        h=SerialHardware(cfg,'COM6','COM6',serial_factory=lambda **kw:bus);h.connect()
        try:
            h.stop()
            self.assertGreaterEqual(times[1]-times[0],.03)
        finally:
            for timer in timers:timer.join()
            h.close()
