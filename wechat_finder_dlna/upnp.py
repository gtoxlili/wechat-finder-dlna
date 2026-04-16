"""UPnP HTTP handler for device description and SOAP control.

Serves the device/service XML descriptors over HTTP, and handles
incoming SOAP actions — most importantly ``SetAVTransportURI`` which
carries the video URL we want to capture.
"""

from __future__ import annotations

import html
import logging
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler
from typing import Callable
from urllib.request import Request, urlopen

from . import descriptors

log = logging.getLogger(__name__)

# ── UPnP LastChange event XML ─────────────────────────────────────

_LAST_CHANGE_STOPPED = (
    '&lt;Event xmlns=&quot;urn:schemas-upnp-org:metadata-1-0/AVT/&quot;&gt;'
    '&lt;InstanceID val=&quot;0&quot;&gt;'
    '&lt;TransportState val=&quot;STOPPED&quot;/&gt;'
    '&lt;/InstanceID&gt;'
    '&lt;/Event&gt;'
)

_NOTIFY_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">\n'
    '  <e:property>\n'
    '    <LastChange>{last_change}</LastChange>\n'
    '  </e:property>\n'
    '</e:propertyset>'
)


def _send_notify(callback_url: str, sid: str) -> None:
    """Send a UPnP NOTIFY event with TransportState=STOPPED."""
    body = _NOTIFY_BODY.format(last_change=_LAST_CHANGE_STOPPED).encode()
    try:
        req = Request(
            callback_url,
            data=body,
            method="NOTIFY",
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "NT": "upnp:event",
                "NTS": "upnp:propchange",
                "SID": sid,
                "SEQ": "0",
                "Content-Length": str(len(body)),
            },
        )
        urlopen(req, timeout=3)
    except Exception:
        log.debug("Failed to send NOTIFY to %s", callback_url, exc_info=True)


class UPnPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for a fake DLNA MediaRenderer."""

    # Set by the caller before starting the server.
    device_uuid: str = ""
    friendly_name: str = ""
    on_url: Callable[[str], None] | None = None
    _captured: bool = False
    # {sid: callback_url} — subscribers for AVTransport events.
    _subscribers: dict[str, str] = {}
    _subscribers_lock = threading.Lock()

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
        sid = f"uuid:{uuid.uuid4()}"
        callback = self.headers.get("CALLBACK", "")
        # CALLBACK header looks like: <http://192.168.1.5:12345/event>
        m = re.search(r"<(.+?)>", callback)
        if m and "AVTransport" in self.path:
            with UPnPHandler._subscribers_lock:
                UPnPHandler._subscribers[sid] = m.group(1)
            log.debug("SUBSCRIBE from %s (SID=%s)", m.group(1), sid)

        self.send_response(200)
        self.send_header("SID", sid)
        self.send_header("TIMEOUT", "Second-1800")
        self.end_headers()

    def do_UNSUBSCRIBE(self):
        sid = self.headers.get("SID", "")
        with UPnPHandler._subscribers_lock:
            UPnPHandler._subscribers.pop(sid, None)
        self.send_response(200)
        self.end_headers()

    # ── internals ───────────────────────────────────────────────────

    def _on_set_uri(self, body: str) -> None:
        m = re.search(r"<CurrentURI[^>]*>(.*?)</CurrentURI>", body, re.DOTALL)
        if m and self.on_url:
            url = html.unescape(m.group(1).strip())
            UPnPHandler._captured = True
            self.on_url(url)
            # Push STOPPED event to all AVTransport subscribers.
            self._notify_stopped()
        self._xml(200, descriptors.soap_response("SetAVTransportURI", "AVTransport"))

    @staticmethod
    def _notify_stopped() -> None:
        """Send LastChange STOPPED event to all AVTransport subscribers."""
        with UPnPHandler._subscribers_lock:
            subs = dict(UPnPHandler._subscribers)
        for sid, url in subs.items():
            threading.Thread(
                target=_send_notify, args=(url, sid), daemon=True,
            ).start()

    def _xml(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", 'text/xml; charset="utf-8"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
