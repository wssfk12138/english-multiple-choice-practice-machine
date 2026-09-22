"""Untrusted mDNS locators. Trust remains exclusively in the paired TLS identity."""
from __future__ import annotations

import socket
import threading
from urllib.parse import urlsplit

from zeroconf import IPVersion, ServiceInfo, Zeroconf

from .services.sync import _local_host_urls

SERVICE_TYPE = "_epm-sync._tcp.local."


class LanAdvertisement:
    def __init__(self, host_id: str, port: int):
        self.host_id, self.port = host_id, port
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="lan-mdns", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=5)

    def _run(self) -> None:
        publishers = {}
        try:
            while not self.stopped.is_set():
                addresses = tuple(dict.fromkeys(urlsplit(url).hostname for url in _local_host_urls()))
                for ip in tuple(publishers):
                    if ip not in addresses:
                        publishers.pop(ip).close()
                for ip in addresses:
                    if self.stopped.is_set():
                        break
                    if ip in publishers:
                        continue
                    publisher = None
                    try:
                        # A distinct SRV target per interface keeps single-address Android
                        # resolvers from selecting a tunnel address for a Wi-Fi service.
                        # https://python-zeroconf.readthedocs.io/en/latest/api.html
                        name = f"epm-{self.host_id}-{socket.inet_aton(ip).hex()}"
                        publisher = Zeroconf(interfaces=[ip], ip_version=IPVersion.V4Only)
                        info = ServiceInfo(SERVICE_TYPE, f"{name}.{SERVICE_TYPE}",
                                           addresses=[socket.inet_aton(ip)],
                                           port=self.port, properties={"host_id": self.host_id, "version": "2"},
                                           server=f"{name}.local.")
                        publisher.register_service(info, allow_name_change=True)
                        publishers[ip] = publisher
                    except Exception:
                        # Discovery is optional; a paired address or a fresh QR remains usable.
                        if publisher:
                            publisher.close()
                self.stopped.wait(10)
        finally:
            for publisher in publishers.values():
                publisher.close()
