"""LAN IP auto-detection, skipping VPN/proxy virtual interfaces."""

from __future__ import annotations

import socket
import subprocess


def get_lan_ip() -> str:
    """Return the first private LAN IP found on a real network interface.

    Parses ``ifconfig`` output to find 192.168.*, 10.*, or 172.* addresses,
    skipping loopback and virtual IPs (e.g. Surge/Clash VPN gateways).

    Falls back to the default-route IP if no private address is found.
    """
    try:
        out = subprocess.run(
            ["ifconfig"], capture_output=True, text=True, timeout=5,
        ).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet ") and "127.0.0.1" not in line:
                ip = line.split()[1]
                if ip.startswith(("192.168.", "10.", "172.")):
                    return ip
    except Exception:
        pass

    # Fallback: default route (may hit VPN).
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()
