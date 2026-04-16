"""LAN IP auto-detection, skipping VPN/proxy virtual interfaces."""

from __future__ import annotations

import socket

import ifaddr


def get_lan_ip() -> str:
    """Return the first private LAN IP found on a real network interface.

    Uses ``ifaddr`` (cross-platform) to enumerate adapters, picking the
    first private address (192.168.*, 10.*, 172.16–31.*) and skipping
    loopback and virtual/VPN interfaces.

    Falls back to the default-route IP if no private address is found.
    """
    for adapter in ifaddr.get_adapters():
        for ip_info in adapter.ips:
            if not isinstance(ip_info.ip, str):
                continue  # skip IPv6 tuples
            ip = ip_info.ip
            if ip.startswith("127."):
                continue
            if ip.startswith(("192.168.", "10.")):
                return ip
            if ip.startswith("172."):
                octet2 = int(ip.split(".")[1])
                if 16 <= octet2 <= 31:
                    return ip

    # Fallback: default route (may hit VPN).
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()
