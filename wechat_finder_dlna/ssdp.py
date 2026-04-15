"""SSDP (Simple Service Discovery Protocol) advertiser.

Handles multicast advertisement (NOTIFY) and M-SEARCH responses so
that DLNA controllers on the local network can discover our renderer.
"""

from __future__ import annotations

import socket
import struct
import threading

MULTICAST_ADDR = "239.255.255.250"
MULTICAST_PORT = 1900

_NOTIFY_TYPES = [
    "upnp:rootdevice",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:service:AVTransport:1",
    "urn:schemas-upnp-org:service:RenderingControl:1",
    "urn:schemas-upnp-org:service:ConnectionManager:1",
]

_SEARCH_TARGETS = [
    "upnp:rootdevice",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:service:AVTransport:1",
]


class SSDPAdvertiser:
    """Broadcasts SSDP presence and answers discovery queries."""

    def __init__(self, device_uuid: str, location: str, local_ip: str):
        self._uuid = device_uuid
        self._location = location
        self._local_ip = local_ip
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass  # Windows

        sock.bind(("", MULTICAST_PORT))
        mreq = struct.pack(
            "4s4s",
            socket.inet_aton(MULTICAST_ADDR),
            socket.inet_aton(self._local_ip),
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(2.0)

        self._notify(sock)

        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(4096)
                msg = data.decode("utf-8", errors="replace")
                if "M-SEARCH" in msg and (
                    "MediaRenderer" in msg
                    or "ssdp:all" in msg
                    or "rootdevice" in msg
                ):
                    self._respond(sock, addr)
            except socket.timeout:
                self._notify(sock)

        sock.close()

    def _notify(self, sock: socket.socket) -> None:
        for nt in [self._uuid] + _NOTIFY_TYPES:
            msg = (
                f"NOTIFY * HTTP/1.1\r\n"
                f"HOST: {MULTICAST_ADDR}:{MULTICAST_PORT}\r\n"
                f"CACHE-CONTROL: max-age=1800\r\n"
                f"LOCATION: {self._location}\r\n"
                f"NT: {nt}\r\n"
                f"NTS: ssdp:alive\r\n"
                f"USN: {self._uuid}::{nt}\r\n"
                f"SERVER: wechat-finder-dlna/1.0 UPnP/1.0\r\n"
                f"\r\n"
            )
            sock.sendto(msg.encode(), (MULTICAST_ADDR, MULTICAST_PORT))

    def _respond(self, sock: socket.socket, addr: tuple) -> None:
        for st in _SEARCH_TARGETS:
            msg = (
                f"HTTP/1.1 200 OK\r\n"
                f"CACHE-CONTROL: max-age=1800\r\n"
                f"LOCATION: {self._location}\r\n"
                f"ST: {st}\r\n"
                f"USN: {self._uuid}::{st}\r\n"
                f"SERVER: wechat-finder-dlna/1.0 UPnP/1.0\r\n"
                f"EXT:\r\n"
                f"\r\n"
            )
            sock.sendto(msg.encode(), addr)
