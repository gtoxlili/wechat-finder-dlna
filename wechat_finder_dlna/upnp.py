"""UPnP HTTP handler for device description and SOAP control.

Serves the device/service XML descriptors over HTTP, and handles
incoming SOAP actions — most importantly ``SetAVTransportURI`` which
carries the video URL we want to capture.
"""

from __future__ import annotations

import html
import re
import uuid
from http.server import BaseHTTPRequestHandler
from typing import Callable

from . import descriptors


class UPnPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for a fake DLNA MediaRenderer."""

    # Set by the caller before starting the server.
    device_uuid: str = ""
    friendly_name: str = ""
    on_url: Callable[[str], None] | None = None
    _captured: bool = False

    def log_message(self, *_):
        pass

    # ── GET: serve XML descriptors ──────────────────────────────────

    def do_GET(self):
        routes = {
            "/device.xml": descriptors.DEVICE.format(
                friendly_name=self.friendly_name, uuid=self.device_uuid,
            ),
            "/AVTransport/scpd.xml": descriptors.AVTRANSPORT_SCPD,
            "/RenderingControl/scpd.xml": descriptors.RENDERING_SCPD,
            "/ConnectionManager/scpd.xml": descriptors.CONNMGR_SCPD,
        }
        body = routes.get(self.path)
        if body:
            self._xml(200, body.encode())
        else:
            self._xml(404, b"Not Found")

    # ── POST: handle SOAP actions ───────────────────────────────────

    def do_POST(self):
        body = self.rfile.read(
            int(self.headers.get("Content-Length", 0))
        ).decode("utf-8", errors="replace")
        action = self.headers.get("SOAPAction", "")

        if "SetAVTransportURI" in action:
            self._on_set_uri(body)
        elif "GetTransportInfo" in action:
            state = "STOPPED" if self._captured else "PLAYING"
            self._xml(200, descriptors.soap_response(
                "GetTransportInfo", "AVTransport",
                f"<CurrentTransportState>{state}</CurrentTransportState>"
                "<CurrentTransportStatus>OK</CurrentTransportStatus>"
                "<CurrentSpeed>1</CurrentSpeed>",
            ))
        elif "GetPositionInfo" in action:
            self._xml(200, descriptors.soap_response(
                "GetPositionInfo", "AVTransport",
                "<Track>1</Track><TrackDuration>00:00:00</TrackDuration>"
                "<TrackMetaData/><TrackURI/>"
                "<RelTime>00:00:00</RelTime><AbsTime>00:00:00</AbsTime>"
                "<RelCount>0</RelCount><AbsCount>0</AbsCount>",
            ))
        elif "GetVolume" in action:
            self._xml(200, descriptors.soap_response(
                "GetVolume", "RenderingControl",
                "<CurrentVolume>50</CurrentVolume>",
            ))
        elif "GetProtocolInfo" in action:
            self._xml(200, descriptors.soap_response(
                "GetProtocolInfo", "ConnectionManager",
                "<Source/><Sink>http-get:*:video/mp4:*,"
                "http-get:*:application/vnd.apple.mpegurl:*</Sink>",
            ))
        else:
            # Play / Stop / Pause / anything else → 200 OK.
            name = next(
                (n for n in ("Play", "Stop", "Pause") if n in action),
                "Unknown",
            )
            svc = (
                "AVTransport" if "AVTransport" in self.path
                else "RenderingControl" if "Rendering" in self.path
                else "ConnectionManager"
            )
            self._xml(200, descriptors.soap_response(name, svc))

    # ── SUBSCRIBE / UNSUBSCRIBE ─────────────────────────────────────

    def do_SUBSCRIBE(self):
        self.send_response(200)
        self.send_header("SID", f"uuid:{uuid.uuid4()}")
        self.send_header("TIMEOUT", "Second-1800")
        self.end_headers()

    def do_UNSUBSCRIBE(self):
        self.send_response(200)
        self.end_headers()

    # ── internals ───────────────────────────────────────────────────

    def _on_set_uri(self, body: str) -> None:
        m = re.search(r"<CurrentURI[^>]*>(.*?)</CurrentURI>", body, re.DOTALL)
        if m and self.on_url:
            url = html.unescape(m.group(1).strip())
            UPnPHandler._captured = True
            self.on_url(url)
        self._xml(200, descriptors.soap_response("SetAVTransportURI", "AVTransport"))

    def _xml(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", 'text/xml; charset="utf-8"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
