import tempfile
import threading
import unittest
from controller import Controller
from fakes import DemoHardware


class MotionMonitorTests(unittest.TestCase):
    def test_active_command_lifecycle_includes_port_amount_and_phase(self):
        entered, release = threading.Event(), threading.Event()
        class HeldHardware(DemoHardware):
            def move_turns(self, direction, turns, rpm, cancel_event=None):
                entered.set()
                release.wait(3)
                super().move_turns(direction, turns, rpm, cancel_event)
        with tempfile.TemporaryDirectory() as root:
            c = Controller(root, hardware_factory=HeldHardware)
            c.connect()
            try:
                c.start(dict(action='manual_motion', port=13, rotation='ccw', turns=2, rpm=60))
                self.assertTrue(entered.wait(2))
                s = c.snapshot()
                self.assertEqual(s.get('active_action', {}).get('action'), 'manual_motion')
                command = s.get('active_command') or {}
                self.assertEqual(command.get('phase'), 'moving')
                self.assertEqual(command.get('port'), 13)
                self.assertEqual(command.get('amount'), 2)
                self.assertEqual(command.get('nominal_duration_s'), 2)
            finally:
                release.set()
                if c.worker: c.worker.join(3)
                c.disconnect()
            self.assertIsNone(c.snapshot().get('active_command'))
            self.assertIsNone(c.snapshot().get('active_action'))

if __name__ == '__main__': unittest.main()
