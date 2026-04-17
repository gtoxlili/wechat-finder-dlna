"""LAN IP auto-detection, skipping VPN/proxy virtual interfaces."""

from __future__ import annotations

import socket

import ifaddr

# Only consider physical network interfaces (Ethernet / WiFi).
# Virtual interfaces (Tailscale utun*, VPN tun*, Docker veth*, etc.) are skipped.
_PHYSICAL_PREFIXES = ("en", "eth", "wlan")


def _is_physical(name: str) -> bool:
    return name.lower().startswith(_PHYSICAL_PREFIXES)


def _is_private(ip: str) -> bool:
    if ip.startswith(("192.168.", "10.")):
        return True
    if ip.startswith("172."):
        octet2 = int(ip.split(".")[1])
        return 16 <= octet2 <= 31
    return False


def get_lan_ip() -> str:
    """Return the first private LAN IP found on a physical network interface.

    Only considers ``en*`` (macOS Ethernet/WiFi), ``eth*`` and ``wlan*``
    (Linux).  Skips VPN (utun/tun), Tailscale, Docker, and other virtual
    interfaces.

    Falls back to the default-route IP if no match is found.
    """
    # Pass 1: physical interfaces only.
    for adapter in ifaddr.get_adapters():
        if not _is_physical(adapter.name):
            continue
        for ip_info in adapter.ips:
            if not isinstance(ip_info.ip, str):
                continue
            if _is_private(ip_info.ip):
                return ip_info.ip

    # Pass 2: any interface (in case naming doesn't match).
    for adapter in ifaddr.get_adapters():
        for ip_info in adapter.ips:
            if not isinstance(ip_info.ip, str):
                continue
            if ip_info.ip.startswith("127."):
                continue
            if _is_private(ip_info.ip):
                return ip_info.ip

    # Fallback: default route (may hit VPN).
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()
