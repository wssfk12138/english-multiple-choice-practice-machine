import unittest
from unittest.mock import Mock, patch

from backend.app import lan_discovery as discovery


class DiscoveryTests(unittest.TestCase):
    def test_address_changes_republish_and_stop_closes_sockets(self):
        publisher = Mock()
        ad = discovery.LanAdvertisement('12345678-1234-1234-1234-123456789abc', 8767)
        ad.stopped = Mock()
        ad.stopped.is_set.side_effect = [False, False, False, False, True]
        with patch.object(discovery, '_local_host_urls', side_effect=[
            ['http://192.168.1.2:8766'], ['http://192.168.1.9:8766']
        ]), patch.object(discovery, 'Zeroconf', return_value=publisher):
            ad._run()
        self.assertEqual(publisher.register_service.call_count, 2)
        self.assertEqual(publisher.close.call_count, 2)
        info = publisher.register_service.call_args.args[0]
        self.assertEqual(info.parsed_addresses(), ['192.168.1.9'])
        self.assertEqual(info.port, 8767)
        self.assertEqual(set(info.properties), {b'host_id', b'version'})

    def test_discovery_failure_is_optional_and_closes_partial_publisher(self):
        publisher = Mock()
        publisher.register_service.side_effect = OSError('synthetic network failure')
        ad = discovery.LanAdvertisement('12345678-1234-1234-1234-123456789abc', 8767)
        ad.stopped = Mock()
        ad.stopped.is_set.side_effect = [False, False, True]
        with (patch.object(discovery, '_local_host_urls', return_value=['http://10.0.0.2:8766']),
              patch.object(discovery, 'Zeroconf', return_value=publisher)):
            ad._run()
        publisher.close.assert_called_once()

    def test_each_interface_has_one_address_and_a_distinct_srv_target(self):
        wifi, tunnel = Mock(), Mock()
        ad = discovery.LanAdvertisement('12345678-1234-1234-1234-123456789abc', 8777)
        ad.stopped.wait = Mock(side_effect=lambda _: ad.stopped.set())
        with (patch.object(discovery, '_local_host_urls', return_value=[
            'http://192.168.3.16:8776', 'http://10.99.0.1:8776'
        ]), patch.object(discovery, 'Zeroconf', side_effect=[wifi, tunnel]) as factory):
            ad._run()
        infos = [publisher.register_service.call_args.args[0] for publisher in (wifi, tunnel)]
        self.assertEqual([call.kwargs['interfaces'] for call in factory.call_args_list],
                         [['192.168.3.16'], ['10.99.0.1']])
        self.assertEqual([info.parsed_addresses() for info in infos],
                         [['192.168.3.16'], ['10.99.0.1']])
        self.assertNotEqual(infos[0].server, infos[1].server)
        self.assertNotEqual(infos[0].name, infos[1].name)
        self.assertEqual(infos[0].properties, infos[1].properties)
        for info, publisher in zip(infos, (wifi, tunnel)):
            self.assertTrue(info.name.startswith('epm-' + ad.host_id))
            self.assertLessEqual(len(info.name.split('.')[0]), 63)
            publisher.close.assert_called_once()

    def test_failed_interface_does_not_remove_healthy_service_and_retries(self):
        wifi, failed, recovered = Mock(), Mock(), Mock()
        failed.register_service.side_effect = OSError('synthetic interface failure')
        ad = discovery.LanAdvertisement('12345678-1234-1234-1234-123456789abc', 8777)
        ad.stopped.wait = Mock(side_effect=[None, None])
        def wait(_):
            if ad.stopped.wait.call_count == 2:
                ad.stopped.set()
        ad.stopped.wait.side_effect = wait
        with (patch.object(discovery, '_local_host_urls', return_value=[
            'http://192.168.3.16:8776', 'http://10.99.0.1:8776'
        ]), patch.object(discovery, 'Zeroconf', side_effect=[wifi, failed, recovered]) as factory):
            ad._run()
        self.assertEqual(factory.call_count, 3)
        wifi.register_service.assert_called_once()
        for publisher in (wifi, failed, recovered):
            publisher.close.assert_called_once()

    def test_unchanged_addresses_do_not_republish(self):
        publisher = Mock()
        ad = discovery.LanAdvertisement('12345678-1234-1234-1234-123456789abc', 8777)
        ad.stopped = Mock()
        ad.stopped.is_set.side_effect = [False, False, False, False, True]
        with (patch.object(discovery, '_local_host_urls', return_value=['http://192.168.3.16:8776']),
              patch.object(discovery, 'Zeroconf', return_value=publisher)):
            ad._run()
        publisher.register_service.assert_called_once()
        publisher.close.assert_called_once()
