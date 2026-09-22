import unittest
from unittest.mock import patch

from ifaddr import Adapter, IP
from backend.app.lan_addresses import local_ipv4_addresses


def adapter(name, *addresses):
    return Adapter(name, name, [IP(address, 24, name) for address in addresses])


class LanAddressTests(unittest.TestCase):
    def test_wifi_precedes_tunnel_without_hostname_resolution(self):
        interfaces = [adapter("sing-tun Tunnel", "10.99.0.1"),
                      adapter("Intel Wi-Fi", "192.168.3.16")]
        with patch("ifaddr.get_adapters", return_value=interfaces):
            self.assertEqual(local_ipv4_addresses(), ["192.168.3.16", "10.99.0.1"])

    def test_private_ranges_only_and_deduplicated(self):
        interfaces = [adapter("Ethernet", "10.0.0.4", "172.16.0.2", "172.31.0.2",
                              "192.168.0.2", "172.32.0.2", "127.0.0.1",
                              "169.254.1.2", "8.8.8.8", ("::1", 0, 0)),
                      adapter("Virtual", "10.0.0.4")]
        with patch("ifaddr.get_adapters", return_value=interfaces):
            self.assertEqual(local_ipv4_addresses(),
                             ["10.0.0.4", "172.16.0.2", "172.31.0.2", "192.168.0.2"])

    def test_address_changes_are_not_cached(self):
        with patch("ifaddr.get_adapters", side_effect=[
                [adapter("Wi-Fi", "192.168.1.2")], [adapter("Wi-Fi", "192.168.1.3")]]):
            self.assertEqual(local_ipv4_addresses(), ["192.168.1.2"])
            self.assertEqual(local_ipv4_addresses(), ["192.168.1.3"])

    def test_no_adapter_and_enumeration_failure(self):
        with patch("ifaddr.get_adapters", return_value=[]):
            self.assertEqual(local_ipv4_addresses(), [])
        with patch("ifaddr.get_adapters", side_effect=OSError("unavailable")):
            self.assertEqual(local_ipv4_addresses(), [])
