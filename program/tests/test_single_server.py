"""A second controller must never share the first controller's listening port."""
import unittest
from server import Server

class SingleServerTest(unittest.TestCase):
    def test_second_listener_is_refused(self):
        first=Server(('127.0.0.1',0),object())
        self.addCleanup(first.server_close)
        def open_second():
            second=Server(first.server_address,object())
            self.addCleanup(second.server_close)
        with self.assertRaises(OSError):
            open_second()
