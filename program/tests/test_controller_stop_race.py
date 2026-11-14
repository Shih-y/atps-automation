"""Deterministic stop/dispatch interleavings using memory-only hardware."""
import tempfile
import threading
import unittest
from pathlib import Path

from controller import Controller
from fakes import DemoHardware
from transport import HardwareError


class ControllerStopRaceTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'test_output'
        root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.c = Controller(self.tmp.name, hardware_factory=lambda cfg: DemoHardware(cfg))
        self.c.connect()

    def tearDown(self):
        self.c.disconnect()
        self.tmp.cleanup()

    def test_stop_covers_request_accepted_before_worker_is_published(self):
        accepted = threading.Event()
        release_start = threading.Event()
        stop_sent = threading.Event()
        worker_entered = threading.Event()
        release_worker = threading.Event()
        stop_done = threading.Event()
        errors = []
        guard = self.c._pending_guard
        stop = self.c.hardware.stop

        def guarded(payload):
            if threading.current_thread().name == 'race-start':
                accepted.set()
                if not release_start.wait(3):
                    raise AssertionError('test did not release start')
            return guard(payload)

        def hardware_stop():
            stop()
            stop_sent.set()

        def event(name, *args, **kwargs):
            if name == 'started':
                worker_entered.set()
                if not release_worker.wait(3):
                    raise AssertionError('test did not release worker')

        def start_request():
            try:
                self.c.start(dict(action='commission', port=15, direction='aspirate',
                                  pulse=dict(steps=20, rpm=5, timeout_s=15)))
            except Exception as exc:
                errors.append(exc)

        def stop_request():
            try:
                self.c.stop_keep_connection()
            except Exception as exc:
                errors.append(exc)
            finally:
                stop_done.set()

        self.c._pending_guard = guarded
        self.c.hardware.stop = hardware_stop
        self.c._lab_event = event
        starter = threading.Thread(target=start_request, name='race-start')
        stopper = threading.Thread(target=stop_request, name='race-stop')
        try:
            starter.start()
            self.assertTrue(accepted.wait(2))
            stopper.start()
            self.assertTrue(stop_sent.wait(2))
            release_start.set()
            starter.join(2)
            self.assertTrue(worker_entered.wait(2))
            # Broken code confirms stop while the newly published old request
            # has not yet checked cancellation. Correct code waits for it.
            stop_done.wait(.25)
            release_worker.set()
            self.c.worker.join(3)
            stopper.join(3)
            self.assertFalse(errors, errors)
            self.assertFalse(stopper.is_alive())
            self.assertEqual([], self.c.hardware.moves)
            self.assertEqual('confirmed', self.c.stop_status)
        finally:
            release_start.set()
            release_worker.set()
            starter.join(3)
            if stopper.ident is not None:
                stopper.join(3)
            if self.c.worker:
                self.c.worker.join(3)

    def test_manual_speed_above_configured_limit_is_rejected_before_disturbance(self):
        class Observer:
            def __init__(self):
                self.calls = []

            def before_manual(self, payload):
                self.calls.append(payload)

            def on_action_event(self, *args, **kwargs):
                pass

        observer = Observer()
        self.c.lab = observer
        self.c.config['pump']['max_rpm'] = 5
        original = self.c.hardware.move_turns

        def limited(direction, turns, rpm, cancel_event=None):
            if rpm > 5:
                raise HardwareError('requested rpm exceeds configured pump/head/tube limit')
            return original(direction, turns, rpm, cancel_event)

        self.c.hardware.move_turns = limited
        self.c.start(dict(action='manual_motion', port=1, rotation='cw', turns=1, rpm=10))
        self.c.worker.join(3)
        self.assertEqual([], observer.calls)
        self.assertEqual([], self.c.hardware.moves)

    def observe(self):
        class Observer:
            def __init__(self):
                self.calls = []
                self.completed = []

            def before_manual(self, payload):
                self.calls.append(dict(payload))

            def on_action_event(self, event, payload, **kwargs):
                if event == 'completed':
                    self.completed.append(dict(payload))

        observer = Observer()
        self.c.lab = observer
        return observer

    def test_top_level_debug_sample_matches_motion_and_completion(self):
        observer = self.observe()
        self.c.start(dict(action='bubble', sample=2))
        self.c.worker.join(3)
        self.assertEqual(2, self.c.hardware.moves[0]['port'])
        self.assertEqual(2, observer.calls[0]['sample'])
        self.assertEqual(2, observer.completed[0]['debug_parameters']['sample'])

    def test_default_debug_sample_is_explicit_in_disturbance(self):
        observer = self.observe()
        self.c.start(dict(action='bubble'))
        self.c.worker.join(3)
        self.assertEqual(1, self.c.hardware.moves[0]['port'])
        self.assertEqual(1, observer.calls[0].get('sample'))

    def test_conflicting_debug_sample_rejects_before_motion(self):
        observer = self.observe()
        self.c.start(dict(action='bubble', sample=2, debug_parameters=dict(sample=1)))
        self.c.worker.join(3)
        self.assertEqual([], self.c.hardware.moves)
        self.assertEqual([], observer.calls)

    def test_invalid_debug_chase_does_not_execute_first_aspiration(self):
        observer = self.observe()
        self.c.config['pump']['max_rpm'] = 5
        self.c.start(dict(action='dose', debug_parameters=dict(
            sample=1, aspirate=dict(steps=45, rpm=5, timeout_s=20),
            chase=dict(steps=1000, rpm=15, timeout_s=35))))
        self.c.worker.join(3)
        self.assertEqual([], self.c.hardware.moves)
        self.assertEqual([], observer.calls)


if __name__ == '__main__':
    unittest.main()
