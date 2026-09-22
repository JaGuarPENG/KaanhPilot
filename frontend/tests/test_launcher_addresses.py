import contextlib
import io
import unittest
from unittest.mock import patch

import launcher


class LauncherAddressTests(unittest.TestCase):
    def output(self, host):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            launcher.print_access_addresses(host, 9090)
        return output.getvalue()

    def test_all_interfaces_print_detected_addresses_and_port(self):
        with patch('frontend.ipad_network.detect_lan_addresses', return_value=['192.168.1.20', '10.0.0.8']):
            text = self.output('0.0.0.0')
        self.assertIn('http://192.168.1.20:9090', text)
        self.assertIn('http://10.0.0.8:9090', text)
        self.assertNotIn('http://0.0.0.0', text)

    def test_loopback_does_not_advertise_unreachable_lan_url(self):
        with patch('frontend.ipad_network.detect_lan_addresses') as detect:
            text = self.output('127.0.0.1')
        detect.assert_not_called()
        self.assertIn('仅允许本机访问', text)

    def test_specific_binding_only_advertises_bound_address(self):
        with patch('frontend.ipad_network.detect_lan_addresses') as detect:
            text = self.output('192.168.1.20')
        detect.assert_not_called()
        self.assertIn('http://192.168.1.20:9090', text)

    def test_detection_failure_is_nonfatal(self):
        with patch('frontend.ipad_network.detect_lan_addresses', side_effect=RuntimeError('没有可用网卡')):
            text = self.output('0.0.0.0')
        self.assertIn('没有可用网卡', text)
        self.assertIn('http://127.0.0.1:9090', text)
