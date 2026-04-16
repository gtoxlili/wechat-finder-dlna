"""AirPlay receiver — emulates an Apple TV for URL-based video casting.

Advertises via mDNS (Bonjour) and handles the HTTP-based AirPlay 1
protocol. When a sender casts a video URL, we capture it.
"""

from __future__ import annotations

import logging
import plistlib
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

from zeroconf import ServiceInfo, Zeroconf

import socket

log = logging.getLogger(__name__)

_AIRPLAY_FEATURES = (
    "0x5A7FFFF7"  # Standard feature flags for video support
)


class AirPlayHandler(BaseHTTPRequestHandler):
    """HTTP handler for AirPlay receiver endpoints."""

    on_url: Callable[[str], None] | None = None
    friendly_name: str = ""
    _captured: bool = False

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/server-info":
            info = plistlib.dumps({
                "deviceid": "AA:BB:CC:DD:EE:FF",
                "features": 0x5A7FFFF7,
                "model": "AppleTV3,2",
                "protovers": "1.0",
                "srcvers": "220.68",
            })
            self._respond(200, info, "application/x-apple-binary-plist")
        elif self.path == "/playback-info":
            self._handle_playback_info()
        else:
            self._respond(200, b"")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if self.path == "/play":
            self._handle_play(body)
        elif self.path == "/scrub":
            self._respond(200, b"")
        elif self.path == "/rate":
            self._respond(200, b"")
        elif self.path == "/stop":
            self._respond(200, b"")
        elif self.path == "/action":
            self._handle_action(body)
        else:
            self._respond(200, b"")

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._respond(200, b"")

    def _handle_play(self, body: bytes) -> None:
        """Extract URL from /play request body.

        Body format is either:
        1) text/parameters:  Content-Location: <url>\nStart-Position: 0
        2) application/x-apple-binary-plist with Content-Location key
        """
        url = None
        content_type = self.headers.get("Content-Type", "")

        if "binary-plist" in content_type or "x-apple" in content_type:
            try:
                plist = plistlib.loads(body)
                url = plist.get("Content-Location")
            except Exception:
                pass

        if not url:
            # text/parameters format
            text = body.decode("utf-8", errors="replace")
            m = re.search(r"Content-Location:\s*(.+)", text)
            if m:
                url = m.group(1).strip()

        if url and self.on_url:
            log.debug("AirPlay captured URL: %s", url)
            AirPlayHandler._captured = True
            self.on_url(url)

        self._respond(200, b"")

    def _handle_action(self, body: bytes) -> None:
        """Handle /action endpoint (some senders use this for URL-based play)."""
        try:
            plist = plistlib.loads(body)
            url = plist.get("Content-Location") or plist.get("url")
            if url and self.on_url:
                log.debug("AirPlay action captured URL: %s", url)
                AirPlayHandler._captured = True
                self.on_url(url)
        except Exception:
            pass
        self._respond(200, b"")

    def _handle_playback_info(self) -> None:
        """Respond to /playback-info polling from the AirPlay sender.

        After URL capture, report rate=0 (stopped) so the sender
        exits the casting UI on its own.
        """
        if self._captured:
            info = plistlib.dumps({
                "duration": 0.0,
                "position": 0.0,
                "rate": 0.0,
                "readyToPlay": False,
                "playbackBufferEmpty": True,
                "playbackBufferFull": False,
                "playbackLikelyToKeepUp": False,
            })
        else:
            info = plistlib.dumps({
                "duration": 0.0,
                "position": 0.0,
                "rate": 1.0,
                "readyToPlay": True,
                "playbackBufferEmpty": True,
                "playbackBufferFull": False,
                "playbackLikelyToKeepUp": True,
            })
        self._respond(200, info, "text/x-apple-plist+xml")

    def _respond(self, code: int, body: bytes,
                 content_type: str = "text/x-apple-plist+xml") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class AirPlayReceiver:
    """AirPlay receiver: mDNS advertisement + HTTP server."""

    def __init__(
        self,
        friendly_name: str,
        local_ip: str,
        port: int,
        on_url: Callable[[str], None],
    ):
        self._name = friendly_name
        self._ip = local_ip
        self._port = port
        self._on_url = on_url
        self._zc: Zeroconf | None = None
        self._info: ServiceInfo | None = None
        self._server: HTTPServer | None = None

    def start(self) -> None:
        AirPlayHandler.on_url = staticmethod(self._on_url)
        AirPlayHandler.friendly_name = self._name

        self._server = HTTPServer(("", self._port), AirPlayHandler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

        device_id = "AA:BB:CC:DD:EE:FF"
        self._info = ServiceInfo(
            "_airplay._tcp.local.",
            f"{self._name}._airplay._tcp.local.",
            addresses=[socket.inet_aton(self._ip)],
            port=self._port,
            properties={
                "deviceid": device_id,
                "features": _AIRPLAY_FEATURES,
                "model": "AppleTV3,2",
                "srcvers": "220.68",
            },
        )
        self._zc = Zeroconf(interfaces=[self._ip])
        self._zc.register_service(self._info)
        log.debug("AirPlay advertised on %s:%d", self._ip, self._port)

    def stop(self) -> None:
        if self._zc and self._info:
            self._zc.unregister_service(self._info)
            self._zc.close()
        if self._server:
            self._server.shutdown()
