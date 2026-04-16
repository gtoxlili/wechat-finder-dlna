"""Google Cast (Chromecast) receiver — emulates a Chromecast device.

Advertises via mDNS and runs a TLS server implementing the Cast V2
protocol (length-prefixed protobuf CastMessage over TLS on port 8009).

When a sender loads media, we capture the URL.

Protocol wire format (same as pychromecast):
  [4-byte big-endian length][CastMessage protobuf bytes]

Uses the official ``cast_channel.proto`` definition (from Chromium source,
via pychromecast) with the ``protobuf`` library for correct serialization.
"""

from __future__ import annotations

import json
import logging
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

from zeroconf import ServiceInfo, Zeroconf

from .generated.cast_channel_pb2 import CastMessage

log = logging.getLogger(__name__)

NS_CONNECTION = "urn:x-cast:com.google.cast.tp.connection"
NS_HEARTBEAT = "urn:x-cast:com.google.cast.tp.heartbeat"
NS_RECEIVER = "urn:x-cast:com.google.cast.receiver"
NS_MEDIA = "urn:x-cast:com.google.cast.media"


def _build_message(
    source: str,
    dest: str,
    namespace: str,
    payload: str,
) -> bytes:
    """Build a length-prefixed CastMessage ready to send on the wire."""
    msg = CastMessage()
    msg.protocol_version = CastMessage.CASTV2_1_0
    msg.source_id = source
    msg.destination_id = dest
    msg.namespace = namespace
    msg.payload_type = CastMessage.STRING
    msg.payload_utf8 = payload
    serialized = msg.SerializeToString()
    return struct.pack(">I", len(serialized)) + serialized


def _parse_message(data: bytes) -> CastMessage:
    """Parse raw protobuf bytes into a CastMessage."""
    msg = CastMessage()
    msg.ParseFromString(data)
    return msg


def _payload_dict(msg: CastMessage) -> dict:
    """Extract the JSON payload from a CastMessage as a dict."""
    try:
        data = json.loads(msg.payload_utf8)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _generate_self_signed_cert(tmpdir: str) -> tuple[str, str]:
    """Generate a self-signed cert+key pair using openssl CLI."""
    cert = str(Path(tmpdir) / "cast.crt")
    key = str(Path(tmpdir) / "cast.key")
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            key,
            "-out",
            cert,
            "-days",
            "365",
            "-nodes",
            "-subj",
            "/CN=CastReceiver",
        ],
        capture_output=True,
        check=True,
        timeout=10,
    )
    return cert, key


class CastReceiver:
    """Google Cast receiver: mDNS + TLS protobuf server on port 8009."""

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
        self._stop_event = threading.Event()
        self._server_sock: socket.socket | None = None
        self._tmpdir: tempfile.TemporaryDirectory | None = None

    def start(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        cert, key = _generate_self_signed_cert(self._tmpdir.name)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)

        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw.bind(("", self._port))
        raw.listen(5)
        raw.settimeout(2.0)
        self._server_sock = ctx.wrap_socket(raw, server_side=True)

        threading.Thread(target=self._accept_loop, daemon=True).start()

        device_uuid = "aabbccdd"
        self._info = ServiceInfo(
            "_googlecast._tcp.local.",
            f"Chromecast-{device_uuid}._googlecast._tcp.local.",
            addresses=[socket.inet_aton(self._ip)],
            port=self._port,
            properties={
                "id": device_uuid,
                "cd": device_uuid,
                "rm": "",
                "ve": "05",
                "md": "Chromecast",
                "ic": "/setup/icon.png",
                "fn": self._name,
                "ca": "4101",
                "st": "0",
                "bs": "FA8FCA771B27",
                "nf": "1",
                "rs": "",
            },
        )
        self._zc = Zeroconf(interfaces=[self._ip])
        self._zc.register_service(self._info)
        log.debug("Cast advertised on %s:%d", self._ip, self._port)

    def stop(self) -> None:
        self._stop_event.set()
        if self._zc and self._info:
            self._zc.unregister_service(self._info)
            self._zc.close()
        if self._server_sock:
            self._server_sock.close()
        if self._tmpdir:
            self._tmpdir.cleanup()

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                conn, addr = self._server_sock.accept()
                log.debug("Cast connection from %s", addr)
                threading.Thread(
                    target=self._handle_client,
                    args=(conn,),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
            except (OSError, ssl.SSLError):
                if not self._stop_event.is_set():
                    log.debug("Cast accept error", exc_info=True)
                break

    def _handle_client(self, conn: ssl.SSLSocket) -> None:
        try:
            while not self._stop_event.is_set():
                header = self._recv_exact(conn, 4)
                if not header:
                    break
                length = struct.unpack(">I", header)[0]
                if length > 65536:
                    break
                data = self._recv_exact(conn, length)
                if not data:
                    break

                msg = _parse_message(data)
                payload = _payload_dict(msg)
                source = msg.source_id
                dest = msg.destination_id
                ns = msg.namespace

                if ns == NS_CONNECTION:
                    resp = json.dumps({"type": "CONNECTED"})
                    conn.sendall(_build_message(dest, source, NS_CONNECTION, resp))

                elif ns == NS_HEARTBEAT:
                    if payload.get("type") == "PING":
                        resp = json.dumps({"type": "PONG"})
                        conn.sendall(_build_message(dest, source, NS_HEARTBEAT, resp))

                elif ns == NS_RECEIVER:
                    self._handle_receiver(conn, payload, source, dest)

                elif ns == NS_MEDIA:
                    self._handle_media(conn, payload, source, dest)

        except (OSError, ssl.SSLError, ConnectionResetError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handle_receiver(
        self,
        conn: ssl.SSLSocket,
        payload: dict,
        source: str,
        dest: str,
    ) -> None:
        req_type = payload.get("type", "")
        request_id = payload.get("requestId", 0)
        status = {
            "applications": [
                {
                    "appId": payload.get("appId", "CC1AD845"),
                    "displayName": "Default Media Receiver",
                    "isIdleScreen": False,
                    "sessionId": "session-1",
                    "statusText": "",
                    "transportId": "transport-1",
                }
            ],
            "volume": {
                "controlType": "attenuation",
                "level": 1.0,
                "muted": False,
                "stepInterval": 0.05,
            },
        }
        if req_type in ("GET_STATUS", "LAUNCH"):
            resp = json.dumps(
                {
                    "requestId": request_id,
                    "type": "RECEIVER_STATUS",
                    "status": status,
                }
            )
            conn.sendall(_build_message(dest, source, NS_RECEIVER, resp))

    def _handle_media(
        self,
        conn: ssl.SSLSocket,
        payload: dict,
        source: str,
        dest: str,
    ) -> None:
        req_type = payload.get("type", "")
        request_id = payload.get("requestId", 0)

        if req_type == "LOAD":
            media = payload.get("media", {})
            url = media.get("contentId") or media.get("contentUrl", "")
            if url:
                log.debug("Cast captured URL: %s", url)
                self._on_url(url)
            # Respond with IDLE/FINISHED so the sender exits casting UI.
            resp = json.dumps(
                {
                    "requestId": request_id,
                    "type": "MEDIA_STATUS",
                    "status": [
                        {
                            "mediaSessionId": 1,
                            "playbackRate": 1,
                            "playerState": "IDLE",
                            "idleReason": "FINISHED",
                            "currentTime": 0,
                            "supportedMediaCommands": 15,
                            "volume": {"level": 1, "muted": False},
                        }
                    ],
                }
            )
            conn.sendall(_build_message(dest, source, NS_MEDIA, resp))

        elif req_type == "GET_STATUS":
            resp = json.dumps(
                {
                    "requestId": request_id,
                    "type": "MEDIA_STATUS",
                    "status": [],
                }
            )
            conn.sendall(_build_message(dest, source, NS_MEDIA, resp))

    @staticmethod
    def _recv_exact(conn: ssl.SSLSocket, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = conn.recv(n - len(buf))
            except (OSError, ssl.SSLError):
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)
