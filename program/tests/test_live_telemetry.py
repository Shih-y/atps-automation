import copy
import threading
import time
import unittest
from transport import SerialHardware
from test_transport import CONFIG
from test_native_turns import NativeTurnBus

class LiveTelemetryTests(unittest.TestCase):
    def setUp(self):
        cfg=copy.deepcopy(CONFIG)
        cfg['pump'].update(status_ok=[0],status_busy=[4],status_ack=[254],aspirate_direction='ccw')
        cfg['valve']['address']=2
        self.bus=NativeTurnBus()
        self.hw=SerialHardware(cfg,'TEST','TEST',serial_factory=lambda **kw:self.bus)
        self.hw.connect()
    def tearDown(self):self.hw.close()
    def test_connection_does_not_invent_stopped_or_valve_position(self):
        t=self.hw.get_telemetry()
        self.assertEqual('unknown',t['pump_status'])
        self.assertIsNone(t['valve_port'])
        self.assertEqual([],self.bus.writes)
    def test_manual_status_refresh_only_queries_and_reports_idle(self):
        self.bus.devices[1].turn_replies=[(0,0)]
        self.hw.read_status()
        self.assertEqual('stopped',self.hw.get_telemetry()['pump_status'])
        self.assertEqual('confirmed',self.hw.get_telemetry()['valve_status'])
        self.assertTrue(self.bus.writes)
        self.assertFalse(any(frame[1]==1 and frame[2] in (0x40,0x41,0x44,0x45,0x49,0x4B) for frame in self.bus.writes))
    def test_stop_recovery_retains_same_serial_handle(self):
        self.bus.devices[1].turn_replies=[(0,0)]
        link=self.hw._links['pump'].serial
        self.hw.stop();self.hw.recover_after_stop()
        self.assertIs(link,self.hw._links['pump'].serial)
        self.assertTrue(self.hw.connected)
        self.assertFalse(self.hw._stopped.is_set())
        self.assertEqual('stopped',self.hw.get_telemetry()['pump_status'])
    def test_readbacks_show_both_directions_then_confirmed_stop(self):
        for flow,port,direction in [('aspirate',15,'ccw'),('dispense',1,'cw')]:
            self.hw.select(port)
            self.assertEqual(port,self.hw.get_telemetry()['valve_port'])
            self.assertEqual('confirmed',self.hw.get_telemetry()['valve_status'])
            pump=self.bus.devices[1];pump.endless_motion=True;pump.turn_replies=[(0,0)]
            errors=[]
            def run():
                try:self.hw.move_turns(flow,2,20)
                except Exception as e:errors.append(e)
            worker=threading.Thread(target=run);worker.start()
            try:
                deadline=time.monotonic()+2
                while time.monotonic()<deadline:
                    t=self.hw.get_telemetry()
                    if t['pump_status']=='running':break
                    time.sleep(.005)
                self.assertEqual('running',t['pump_status'])
                self.assertEqual(direction,t['direction'])
                self.assertEqual(flow,t['flow'])
                self.assertEqual(20,t['rpm'])
            finally:pump.endless_motion=False;worker.join(3)
            self.assertFalse(worker.is_alive());self.assertFalse(errors)
            self.assertEqual('stopped',self.hw.get_telemetry()['pump_status'])
            self.assertEqual(0,self.hw.get_telemetry()['remaining'])
    def test_priority_stop_is_unconfirmed_not_false_idle(self):
        self.hw.select(16);self.hw.stop()
        t=self.hw.get_telemetry()
        self.assertEqual('stop_requested',t['pump_status'])
        self.assertEqual('unknown',t['valve_status'])
        self.assertIsNone(t['valve_port'])
