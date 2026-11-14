import unittest
from types import SimpleNamespace
from unittest.mock import patch
from desktop import shutdown


class DesktopShutdownTests(unittest.TestCase):
    def test_storage_and_logger_failure_cannot_block_device_cleanup(self):
        events = []
        def broken_archive():
            events.append('archive')
            raise OSError('disk unavailable')
        controller = SimpleNamespace(disconnect=lambda: events.append('disconnect'))
        server = SimpleNamespace(shutdown=lambda: events.append('http'), server_close=broken_archive)
        thread = SimpleNamespace(join=lambda seconds: events.append('join'))
        with patch('desktop.logging.info', side_effect=OSError('logger unavailable')):
            shutdown(controller, server, thread)
        self.assertEqual(['disconnect', 'http', 'archive', 'join'], events)

    def test_failed_stop_still_closes_server_and_does_not_claim_confirmation(self):
        events = []
        def failed_stop():
            events.append('stop requested')
            raise OSError('serial disconnected')
        controller = SimpleNamespace(disconnect=failed_stop)
        server = SimpleNamespace(shutdown=lambda: events.append('http'), server_close=lambda: events.append('archive'))
        thread = SimpleNamespace(join=lambda seconds: events.append('join'))
        shutdown(controller, server, thread)
        self.assertEqual(['stop requested', 'http', 'archive', 'join'], events)
