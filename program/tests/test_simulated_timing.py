import threading
import time
import unittest
from simulated_hardware import SimulatedHardware
from transport import HardwareError


class TimingTests(unittest.TestCase):
    def hardware(self):
        return SimulatedHardware({'pump': {'aspirate_direction': 'ccw'}})

    def test_turn_command_uses_wall_clock_and_remaining(self):
        h = self.hardware()
        started = time.monotonic()
        worker = threading.Thread(target=h.move_turns, args=('dispense', 2, 60))
        worker.start()
        time.sleep(1.25)
        running = h.get_telemetry()
        worker.join(3)
        self.assertEqual(running['pump_status'], 'running')
        self.assertEqual(running['direction'], 'cw')
        self.assertGreater(running['remaining'], 0)
        self.assertLessEqual(running['remaining'], 1)
        self.assertGreaterEqual(time.monotonic() - started, 2)
        self.assertFalse(worker.is_alive())

    def test_steps_use_existing_200_steps_per_turn_basis(self):
        h = self.hardware()
        started = time.monotonic()
        h.move('aspirate', 100, 60)
        self.assertAlmostEqual(time.monotonic() - started, .5, delta=.2)

    def test_large_motion_stops_promptly_and_never_resumes(self):
        h = self.hardware()
        errors = []
        def run():
            try: h.move_turns('dispense', 30, 60)
            except HardwareError as exc: errors.append(str(exc))
        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(.1)
        h.stop()
        worker.join(.5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        self.assertEqual(h.get_telemetry()['pump_status'], 'stopped')

    def test_cancel_event_prevents_completion(self):
        h = self.hardware()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(HardwareError): h.move('aspirate', 100, 30, cancel)

if __name__ == '__main__': unittest.main()
