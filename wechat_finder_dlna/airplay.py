"""AirPlay receiver — emulates an Apple TV for URL-based video casting.

Advertises via mDNS (Bonjour) and implements the AirPlay 2 pairing
handshake (transient pair-setup + FairPlay stub) required by modern
iOS (16+).  Captures video URLs via POST /play and optionally records
the audio stream pushed by the sender.
"""

from __future__ import annotations

import logging
import plistlib
import re
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

from cryptography.hazmat.primitives.asymmetric import ed25519
from zeroconf import ServiceInfo, Zeroconf

from .audio_capture import AudioCapture
from .pairing import HAPSocket, HapSession, fairplay_setup

log = logging.getLogger(__name__)

# ── AirPlay 2 features (64-bit) ──────────────────────────────────
_FEATURES = (
    (1 << 48)  # TransientPairing
    | (1 << 47)  # PeerManagement
    | (1 << 46)  # HomeKitPairing
    | (1 << 41)  # PTPClock
    | (1 << 40)  # BufferedAudio
    | (1 << 30)  # UnifiedAdvertisingInfo
    | (1 << 22)  # AudioUnencrypted
    | (1 << 20)  # ReceiveAudioAAC_LC
    | (1 << 19)  # ReceiveAudioALAC
    | (1 << 18)  # ReceiveAudioPCM
    | (1 << 17)  # AudioMetaTxtDAAP
    | (1 << 16)  # AudioMetaProgress
    | (1 << 14)  # MFiSoft_FairPlay
    | (1 << 9)  # AirPlayAudio
    | (1 << 4)  # VideoHTTPLiveStreaming
    | (1 << 0)  # Video
)
_FEATURES_MDNS = f"{hex(_FEATURES & 0xFFFFFFFF)},{hex((_FEATURES >> 32) & 0xFFFFFFFF)}"

def _get_device_id() -> str:
    """Derive a stable device ID from the machine's real MAC address."""
    import uuid as _uuid
    mac = _uuid.getnode()
    return ":".join(f"{(mac >> (8 * i)) & 0xFF:02X}" for i in reversed(range(6)))

_DEVICE_ID = _get_device_id()
_PI = "2e388006-13ba-4041-9a67-25dd4a43d536"
_SRCVERS = "366.0"


class AirPlayHandler(BaseHTTPRequestHandler):
    """HTTP/RTSP handler for AirPlay receiver endpoints."""

    protocol_version = "HTTP/1.1"
    server_version = "AirTunes/366.0"
    sys_version = ""

    on_url: Callable[[str], None] | None = None
    friendly_name: str = ""
    _captured: bool = False
    _ltsk: ed25519.Ed25519PrivateKey | None = None
    _audio_output: str | None = None
    _audio_duration: float | None = None

    def setup(self):
        super().setup()
        self._hap = HapSession(ltsk=self._ltsk)
        self._is_encrypted = False
        self._audio_cap: AudioCapture | None = None

    def parse_request(self):
        self._rtsp = bool(self.raw_requestline and b"RTSP/" in self.raw_requestline)
        if self._rtsp:
            self.raw_requestline = self.raw_requestline.replace(
                b"RTSP/1.0", b"HTTP/1.1"
            )
        ok = super().parse_request()
        if ok and self._rtsp:
            self.request_version = "RTSP/1.0"
        return ok

    def finish(self):
        log.debug("AirPlay: connection closed from %s", self.client_address)
        super().finish()

    def log_message(self, fmt, *args):
        log.debug("AirPlay HTTP: %s", fmt % args)

    # ── GET ────────────────────────────────────────────────────────

    def do_GET(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        if self.path in ("/server-info", "/info"):
            self._send_device_info()
        elif self.path == "/playback-info":
            self._handle_playback_info()
        else:
            self._respond(200, b"")

    # ── POST ───────────────────────────────────────────────────────

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        try:
            if self.path == "/play":
                self._handle_play(body)
            elif self.path == "/info":
                self._send_device_info()
            elif self.path == "/action":
                self._handle_action(body)
            elif self.path == "/pair-setup":
                self._handle_pair_setup(body)
            elif self.path == "/pair-verify":
                self._handle_pair_verify(body)
            elif self.path in ("/fp-setup", "/fp-setup2"):
                self._handle_fp_setup(body)
            elif self.path in ("/pair-setup-pin", "/pair-pin-start"):
                self._respond(200, b"")
            elif self.path in (
                "/scrub",
                "/rate",
                "/stop",
                "/photo",
                "/slideshows",
                "/authorize",
                "/setProperty",
                "/feedback",
                "/command",
            ):
                self._respond(200, b"")
            else:
                self._respond(200, b"")
        except Exception:
            log.exception("AirPlay: error handling POST %s", self.path)
            try:
                self._respond(500, b"")
            except Exception:
                pass

    # ── PUT ────────────────────────────────────────────────────────

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._respond(200, b"")

    # ── RTSP methods ──────────────────────────────────────────────

    def do_SETUP(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        setup_plist = None
        if body:
            try:
                setup_plist = plistlib.loads(body)
            except Exception:
                pass

        if setup_plist and "streams" in setup_plist:
            streams_resp = []
            for s in setup_plist["streams"]:
                shk = s.get("shk")
                if self._audio_output and not self._audio_cap:
                    self._audio_cap = AudioCapture(on_done=self.on_url)
                    self._audio_cap.start(
                        self._audio_output, shk=shk, duration=self._audio_duration
                    )
                    print(
                        f"  🎙️ Recording audio → {self._audio_output}", file=sys.stderr
                    )
                data_port = self._audio_cap.port if self._audio_cap else 7100
                streams_resp.append(
                    {
                        "type": s.get("type", 96),
                        "dataPort": data_port,
                        "controlPort": 7101,
                    }
                )
            resp = plistlib.dumps({"streams": streams_resp})
            self._respond(200, resp, "application/x-apple-binary-plist")
        elif setup_plist and "timingProtocol" in setup_plist:
            resp = plistlib.dumps({"eventPort": 0, "timingPort": 0})
            self._respond(200, resp, "application/x-apple-binary-plist")
        else:
            self._respond(200, b"")

    def do_OPTIONS(self):
        self.send_response(200)
        self._echo_cseq()
        self.send_header(
            "Public",
            "ANNOUNCE, SETUP, RECORD, PAUSE, FLUSH, "
            "TEARDOWN, OPTIONS, GET_PARAMETER, SET_PARAMETER, "
            "POST, GET",
        )
        self.end_headers()

    def do_GET_PARAMETER(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        if b"volume" in body:
            self._respond(200, b"volume: 0.000000\r\n")
        else:
            self._respond(200, b"")

    def do_SET_PARAMETER(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._respond(200, b"")

    def do_RECORD(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._respond(200, b"")

    def do_TEARDOWN(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        if self._audio_cap:
            n = self._audio_cap.stop()
            print(f"  🎙️ Audio capture stopped ({n} packets)", file=sys.stderr)
            self._audio_cap = None
        self._respond(200, b"")

    def do_FLUSH(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._respond(200, b"")

    def do_SETPEERS(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._respond(200, b"")

    # ── Pairing handlers ──────────────────────────────────────────

    def _handle_pair_setup(self, body: bytes) -> None:
        res = self._hap.pair_setup(body)
        self._respond(200, res, "application/octet-stream")
        if self._hap.encrypted and not self._is_encrypted:
            self._upgrade_to_encrypted()

    def _handle_pair_verify(self, body: bytes) -> None:
        res = self._hap.pair_verify(body)
        self._respond(200, res, "application/octet-stream")
        if self._hap.encrypted and not self._is_encrypted:
            self._upgrade_to_encrypted()

    def _handle_fp_setup(self, body: bytes) -> None:
        res = fairplay_setup(body)
        if res:
            self._respond(200, res, "application/octet-stream")
        else:
            self._respond(200, b"")

    def _upgrade_to_encrypted(self) -> None:
        assert self._hap.shared_key is not None
        self.wfile.flush()
        self.connection = HAPSocket(self.connection, self._hap.shared_key)
        self.request = self.connection
        self.rfile = self.connection.makefile("rb", self.rbufsize)
        self.wfile = self.connection.makefile("wb", self.wbufsize)
        self._is_encrypted = True

    # ── Payload handlers ──────────────────────────────────────────

    def _send_device_info(self) -> None:
        d = {
            "deviceID": _DEVICE_ID,
            "features": _FEATURES,
            "model": "AppleTV6,2",
            "protocolVersion": "1.1",
            "sourceVersion": _SRCVERS,
            "sdk": "AirPlay;2.0.2",
            "name": self.friendly_name,
            "macAddress": _DEVICE_ID,
            "pi": _PI,
            "pk": self._hap.public_key_hex,
            "statusFlags": 4,
            "keepAliveLowPower": True,
            "keepAliveSendStatsAsBody": True,
            "vv": 2,
        }
        self._respond(200, plistlib.dumps(d), "application/x-apple-binary-plist")

    def _handle_play(self, body: bytes) -> None:
        url = None
        content_type = self.headers.get("Content-Type", "")

        if "binary-plist" in content_type or "x-apple" in content_type:
            try:
                plist = plistlib.loads(body)
                url = plist.get("Content-Location")
            except Exception:
                pass

        if not url:
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
        if self._captured:
            info = {
                "duration": 0.0,
                "position": 0.0,
                "rate": 0.0,
                "readyToPlay": False,
                "playbackBufferEmpty": True,
                "playbackBufferFull": False,
                "playbackLikelyToKeepUp": False,
            }
        else:
            info = {
                "duration": 0.0,
                "position": 0.0,
                "rate": 1.0,
                "readyToPlay": True,
                "playbackBufferEmpty": True,
                "playbackBufferFull": False,
                "playbackLikelyToKeepUp": True,
            }
        body = plistlib.dumps(info, fmt=plistlib.FMT_XML)
        self._respond(200, body, "text/x-apple-plist+xml")

    # ── Response helpers ──────────────────────────────────────────

    def _echo_cseq(self) -> None:
        cseq = self.headers.get("CSeq")
        if cseq is not None:
            self.send_header("CSeq", cseq)

    def _respond(
        self, code: int, body: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        self.send_response(code)
        self._echo_cseq()
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
        audio_output: str | None = None,
        audio_duration: float | None = None,
    ):
        self._name = friendly_name
        self._ip = local_ip
        self._port = port
        self._on_url = on_url
        self._audio_output = audio_output
        self._audio_duration = audio_duration
        self._zc: Zeroconf | None = None
        self._info: ServiceInfo | None = None
        self._server: HTTPServer | None = None

    def start(self) -> None:
        ltsk = ed25519.Ed25519PrivateKey.generate()
        pk_hex = HapSession(ltsk=ltsk).public_key_hex

        AirPlayHandler.on_url = staticmethod(self._on_url)
        AirPlayHandler.friendly_name = self._name
        AirPlayHandler._captured = False
        AirPlayHandler._ltsk = ltsk
        AirPlayHandler._audio_output = self._audio_output
        AirPlayHandler._audio_duration = self._audio_duration

        self._server = HTTPServer(("", self._port), AirPlayHandler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

        self._info = ServiceInfo(
            "_airplay._tcp.local.",
            f"{self._name}._airplay._tcp.local.",
            addresses=[socket.inet_aton(self._ip)],
            port=self._port,
            properties={
                "deviceid": _DEVICE_ID,
                "features": _FEATURES_MDNS,
                "flags": "0x04",
                "model": "AppleTV6,2",
                "srcvers": _SRCVERS,
                "pk": pk_hex,
                "pi": _PI,
                "protovers": "1.1",
                "vv": "2",
                "acl": "0",
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
