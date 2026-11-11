"""CH340 discovery and connection contracts; only OS/serial boundaries are fake."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vendor'))
from serial.tools.list_ports_common import ListPortInfo

from controller import Controller
from server import Server
from test_shared_transport import FakeBus
from test_support import install_test_profiles


def port(device, description, vid=None, pid=None, hwid='n/a'):
    item = ListPortInfo(device)
    item.description, item.vid, item.pid, item.hwid = description, vid, pid, hwid
    return item


class SerialDiscoveryTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / 'test_output'
        output.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=output)
        self.addCleanup(self.temp.cleanup)
        self.opened = []

        def open_serial(**kwargs):
            bus = FakeBus(**kwargs)
            self.opened.append(bus)
            return bus

        serial_patch = patch('serial.Serial', side_effect=open_serial)
        self.serial = serial_patch.start()
        self.addCleanup(serial_patch.stop)
        enumeration_patch = patch('serial.tools.list_ports.comports', return_value=[])
        self.enumeration = enumeration_patch.start()
        self.addCleanup(enumeration_patch.stop)
        self.c = Controller(self.temp.name)
        # A previously saved number must never win over fresh OS discovery.
        self.c.config['communication']['port'] = 'COM6'
        self.server = Server(('127.0.0.1', 0), self.c)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        if self.c.hardware:
            self.c.hardware.close()

    def request(self, path, payload=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Content-Type': 'application/json', 'X-Control-Token': self.server.token}
        connection.request('GET' if payload is None else 'POST', path,
                           None if payload is None else json.dumps(payload), headers)
        response = connection.getresponse()
        status, result = response.status, json.loads(response.read())
        connection.close()
        return status, result

    def ch340(self, device='COM5'):
        return port(device, 'USB-SERIAL CH340', 0x1A86, 0x7523,
                    r'USB\VID_1A86&PID_7523\SERIAL')

    def test_startup_does_not_scan_open_or_request_preflight(self):
        self.assertEqual([], self.opened)
        self.enumeration.assert_not_called()
        self.assertFalse(self.c.connected)
        self.assertIsNone(self.c.hardware)
        self.assertIsNone(self.c.prompt)

    def test_scan_returns_real_port_metadata_without_opening_ports(self):
        self.enumeration.return_value = [port('COM10', 'Bluetooth Serial'), self.ch340()]
        status, result = self.request('/api/serial-ports')
        self.assertEqual(200, status)
        self.assertEqual(['COM5', 'COM10'], [p['device'] for p in result['ports']])
        self.assertEqual([{'device': 'COM5', 'description': 'USB-SERIAL CH340',
                           'hwid': r'USB\VID_1A86&PID_7523\SERIAL', 'vid': 6790,
                           'pid': 29987, 'is_ch340': True}], result['ch340_ports'])
        self.assertEqual('COM5', result['suggested_port'])
        self.assertFalse(result['ports'][1]['is_ch340'])
        self.assertEqual([], self.opened)
        self.assertFalse(self.c.connected)
        self.assertIsNone(self.c.hardware)

    def test_vid_pid_and_description_and_hwid_fallback_identify_chips(self):
        fixtures = [
            port('COM5', 'USB Serial', 0x1A86, 0x7523),
            port('COM5', 'USB Serial', 0x1A86, 0x5523),
            port('COM5', 'usb-serial ch341A'),
            port('COM5', 'USB Serial', hwid=r'USB\VID_1A86&PID_7523\A'),
            port('COM5', 'USB Serial', hwid='USB VID:PID=1A86:5523 SER=A'),
        ]
        for item in fixtures:
            with self.subTest(description=item.description, hwid=item.hwid, pid=item.pid):
                self.enumeration.return_value = [item]
                status, result = self.request('/api/serial-ports')
                self.assertEqual(200, status)
                self.assertEqual('COM5', result['suggested_port'])
        self.assertEqual([], self.opened)

    def test_no_ch340_and_bluetooth_do_not_offer_an_arbitrary_port(self):
        for items in ([], [port('COM3', 'Standard Serial over Bluetooth link')],
                      [port('COM8', 'USB Serial', 0x1A86, 0x55D4)]):
            with self.subTest(items=items):
                self.enumeration.return_value = items
                status, result = self.request('/api/serial-ports')
                self.assertEqual(200, status)
                self.assertIsNone(result['suggested_port'])
                self.assertEqual([], result['ch340_ports'])
                self.assertIn('未发现', result['message'])
        self.assertEqual([], self.opened)

    def test_multiple_ch340_requires_manual_choice(self):
        self.enumeration.return_value = [self.ch340('COM7'), self.ch340()]
        status, result = self.request('/api/serial-ports')
        self.assertEqual(200, status)
        self.assertIsNone(result['suggested_port'])
        self.assertEqual(['COM5', 'COM7'], [p['device'] for p in result['ch340_ports']])
        self.assertIn('手动', result['message'])
        status, result = self.request('/api/connect', {'auto_detect': True})
        self.assertEqual(400, status)
        self.assertIn('COM5', result['error'])
        self.assertIn('COM7', result['error'])
        self.assertEqual([], self.opened)
        self.assertFalse(self.c.connected)

    def test_auto_connect_rescans_and_replaces_stale_config_without_transmission(self):
        self.enumeration.return_value = [self.ch340('COM4')]
        self.request('/api/serial-ports')
        self.enumeration.return_value = [self.ch340()]
        # Unverified motion mappings must not turn connecting into a preflight.
        self.c.config['pump']['status_ok'] = []
        status, result = self.request('/api/connect', {'auto_detect': True, 'port': 'COM6'})
        self.assertEqual(200, status, result)
        self.assertEqual('COM5', result['state']['config']['communication']['port'])
        self.assertEqual(['COM5'], [bus.kwargs['port'] for bus in self.opened])
        self.assertEqual([], self.opened[0].writes)
        self.assertEqual((1, 2), (self.c.hardware.pump.address, self.c.hardware.valve.address))
        self.assertIn('COM5', result['state']['message'])
        self.assertEqual('COM5', result['state']['logs'][-1]['port'])
        self.assertIsNone(result['state']['prompt'])
        archive = next((Path(self.temp.name) / 'config_history').glob('*.json'))
        self.assertEqual('COM5', json.loads(archive.read_text(encoding='utf-8'))['config']['communication']['port'])

    def test_auto_connect_with_no_ch340_never_opens_stale_or_bluetooth_port(self):
        self.enumeration.return_value = [port('COM3', 'Bluetooth Serial')]
        status, result = self.request('/api/connect', {'auto_detect': True, 'port': 'COM6'})
        self.assertEqual(400, status)
        self.assertIn('未发现', result['error'])
        self.assertEqual([], self.opened)
        self.assertFalse(self.c.connected)
        self.assertEqual('COM6', self.c.config['communication']['port'])

    def test_manual_port_still_connects_and_records_actual_port(self):
        status, result = self.request('/api/connect', {'port': ' com9 ', 'auto_detect': False})
        self.assertEqual(200, status, result)
        self.assertEqual('COM9', self.c.config['communication']['port'])
        self.assertEqual(['COM9'], [bus.kwargs['port'] for bus in self.opened])
        self.assertEqual([], self.opened[0].writes)

    def test_legacy_connect_without_auto_uses_configured_port(self):
        status, result = self.request('/api/connect', {})
        self.assertEqual(200, status, result)
        self.assertEqual('COM6', result['state']['config']['communication']['port'])
        self.assertEqual(['COM6'], [bus.kwargs['port'] for bus in self.opened])
        self.assertEqual([], self.opened[0].writes)

    def test_failed_open_does_not_publish_selected_config_or_connected_state(self):
        install_test_profiles(self.c)
        self.enumeration.return_value = [self.ch340()]
        self.serial.side_effect = OSError('port busy')
        status, result = self.request('/api/connect', {'auto_detect': True})
        self.assertNotEqual(200, status)
        self.assertIn('port busy', result['error'])
        self.assertFalse(self.c.connected)
        self.assertIsNone(self.c.hardware)
        self.assertEqual('DISCONNECTED', self.c.state)
        self.assertEqual('COM6', self.c.config['communication']['port'])
        self.assertTrue(self.c.config['reset']['validated'])
        self.assertTrue(self.c.config['profiles'][0]['validated'])
        self.assertIn('COM5', self.c.message)
        self.assertFalse(any(record['event'] == 'connected' for record in self.c.logs))
        self.assertEqual([], list((Path(self.temp.name) / 'config_history').glob('*.json')))

    def test_changed_port_revokes_existing_calibration_claims(self):
        install_test_profiles(self.c)
        self.enumeration.return_value = [self.ch340()]
        status, result = self.request('/api/connect', {'auto_detect': True})
        self.assertEqual(200, status, result)
        self.assertFalse(self.c.config['reset']['validated'])
        self.assertTrue(all(not p['validated'] and not p['bubble_validated']
                            for p in self.c.config['profiles']))

    def test_already_connected_does_not_reopen_close_or_send_commands(self):
        self.enumeration.return_value = [self.ch340()]
        status, result = self.request('/api/connect', {'auto_detect': True})
        self.assertEqual(200, status, result)
        self.enumeration.return_value = [self.ch340('COM7')]
        status, result = self.request('/api/connect', {'auto_detect': True})
        self.assertEqual(400, status)
        self.assertIn('断开', result['error'])
        self.assertTrue(self.c.connected)
        self.assertEqual('COM5', self.c.config['communication']['port'])
        self.assertEqual(1, len(self.opened))
        self.assertEqual(0, self.opened[0].close_count)
        self.assertEqual([], self.opened[0].writes)

    def test_auto_flag_must_be_boolean(self):
        self.enumeration.return_value = [self.ch340()]
        status, result = self.request('/api/connect', {'auto_detect': 'false'})
        self.assertEqual(400, status)
        self.assertIn('布尔', result['error'])
        self.assertEqual([], self.opened)

    def test_enumeration_failure_is_actionable_and_never_opens_port(self):
        self.enumeration.side_effect = OSError('enumeration unavailable')
        for path, payload in [('/api/serial-ports', None), ('/api/connect', {'auto_detect': True})]:
            status, result = self.request(path, payload)
            self.assertEqual(400, status)
            self.assertIn('扫描', result['error'])
            self.assertIn('enumeration unavailable', result['error'])
        self.assertEqual([], self.opened)


if __name__ == '__main__':
    unittest.main()
