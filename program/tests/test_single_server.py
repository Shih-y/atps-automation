"""A second controller must never share the first controller's listening port."""
import unittest
import tempfile
from pathlib import Path
from controller import Controller
from server import Server

class SingleServerTest(unittest.TestCase):
    def test_second_listener_is_refused(self):
        folder=Path(__file__).resolve().parents[1]/'test_output'
        folder.mkdir(exist_ok=True)
        temp=tempfile.TemporaryDirectory(dir=folder)
        self.addCleanup(temp.cleanup)
        first=Server(('127.0.0.1',0),Controller(temp.name))
        self.addCleanup(first.server_close)
        def open_second():
            second=Server(first.server_address,object())
            self.addCleanup(second.server_close)
        with self.assertRaises(OSError):
            open_second()
