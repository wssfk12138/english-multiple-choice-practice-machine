"""Enumerate LAN interfaces without relying on hostname DNS or VPN resolution."""
from __future__ import annotations

import ipaddress

import ifaddr

_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                  ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_VIRTUAL_HINTS = ("virtual", "tunnel", "vpn", "tun", "tap",
                  "wireguard", "tailscale", "docker", "hyper-v", "wsl")


def local_ipv4_addresses() -> list[str]:
    # ifaddr exposes every interface, unlike getaddrinfo(gethostname()).
    # https://github.com/ifaddr/ifaddr#usage
    try:
        adapters = ifaddr.get_adapters()
    except OSError:
        return []
    candidates: dict[str, int] = {}
    for adapter in adapters:
        for item in adapter.ips:
            if not item.is_IPv4:
                continue
            try:
                address = ipaddress.IPv4Address(item.ip)
            except ipaddress.AddressValueError:
                continue
            if not any(address in network for network in _NETWORKS):
                continue
            name = f"{adapter.name} {adapter.nice_name} {item.nice_name}".lower()
            rank = int(any(hint in name for hint in _VIRTUAL_HINTS))
            value = str(address)
            candidates[value] = min(rank, candidates.get(value, rank))
    return sorted(candidates, key=lambda value: (candidates[value], int(ipaddress.IPv4Address(value))))
