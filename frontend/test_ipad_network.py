import unittest
from unittest.mock import patch


class IPadNetworkTests(unittest.TestCase):
    def test_active_lan_interfaces_exclude_loopback_vpn_and_duplicates(self):
        from ipad_network import parse_lan_addresses
        sample = '''lo0: flags=8049<UP,LOOPBACK,RUNNING>
    inet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,RUNNING>
    inet 192.168.1.42 netmask 0xffffff00 broadcast 192.168.1.255
    status: active
en1: flags=8863<UP,BROADCAST,RUNNING>
    inet 10.0.0.12 netmask 0xffffff00
    status: inactive
utun2: flags=8051<UP,POINTOPOINT,RUNNING>
    inet 10.9.0.1 netmask 0xffffff00
bridge100: flags=8863<UP,BROADCAST,RUNNING>
    inet 192.168.1.42 netmask 0xffffff00
    status: active
'''
        self.assertEqual(parse_lan_addresses(sample), ['192.168.1.42'])

    def test_manual_override_is_validated(self):
        from ipad_network import detect_lan_addresses
        with patch.dict('os.environ', {'KAANH_IPAD_IP':'192.168.2.15'}):
            self.assertEqual(detect_lan_addresses(), ['192.168.2.15'])
        for invalid in ['127.0.0.1','0.0.0.0','192.168.1.3:8080','not-an-ip']:
            with self.subTest(invalid=invalid), patch.dict('os.environ', {'KAANH_IPAD_IP':invalid}):
                with self.assertRaises(ValueError):
                    detect_lan_addresses()

    def test_no_network_has_actionable_error(self):
        from ipad_network import detect_lan_addresses
        with patch.dict('os.environ', {'KAANH_IPAD_IP':''}), patch('ipad_network.subprocess.run') as run:
            run.return_value.stdout='lo0: flags=8049<UP,LOOPBACK>\n    inet 127.0.0.1\n'
            with self.assertRaisesRegex(RuntimeError,'Wi-Fi'):
                detect_lan_addresses()
