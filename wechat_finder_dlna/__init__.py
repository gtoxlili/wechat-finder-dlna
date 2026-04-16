"""wechat-finder-dlna — Capture WeChat Video Channel live stream URLs via DLNA.

Pretends to be a TV on your local network. When you cast a WeChat Video
Channel (视频号) live stream to it, the real m3u8 stream URL is captured.

Supports multiple casting protocols: DLNA/UPnP, AirPlay, and Google Cast.

Example::

    from wechat_finder_dlna import capture

    url = capture(name="My TV")
    print(f"Stream: {url}")

    # AirPlay only
    url = capture(name="My TV", protocols=["airplay"])

    # All protocols
    url = capture(name="My TV", protocols=["dlna", "airplay", "cast"])
"""

from __future__ import annotations

import logging
import sys
import threading
import uuid
from http.server import HTTPServer
from typing import Callable

log = logging.getLogger(__name__)

from .net import get_lan_ip
from .ssdp import SSDPAdvertiser
from .upnp import UPnPHandler

__all__ = ["capture"]

PROTOCOLS = ("dlna", "airplay", "cast")


def capture(
    *,
    name: str = "wechat-finder-dlna",
    port: int = 9090,
    on_url: Callable[[str], None] | None = None,
    protocols: list[str] | None = None,
) -> str:
    """Start fake casting receivers and block until a URL is captured.

    Args:
        name: Device name shown in the cast list.
        port: Base HTTP port. DLNA uses *port*, AirPlay uses *port+1*,
              Cast uses 8009.
        on_url: Optional callback fired when a URL is captured.
        protocols: List of protocols to enable. Defaults to all:
                   ``["dlna", "airplay", "cast"]``.

    Returns:
        The captured stream/video URL.
    """
    if protocols is None:
        protocols = list(PROTOCOLS)
    for p in protocols:
        if p not in PROTOCOLS:
            raise ValueError(f"Unknown protocol {p!r}, expected one of {PROTOCOLS}")

    local_ip = get_lan_ip()
    dev_uuid = f"uuid:{uuid.uuid4()}"

    result: list[str] = []
    event = threading.Event()

    def _handle(url: str) -> None:
        if result:
            return  # already captured
        result.append(url)
        if on_url:
            on_url(url)
        event.set()

    cleanups: list[Callable[[], None]] = []
    started: list[str] = []

    # ── DLNA ───────────────────────────────────────────────────────
    if "dlna" in protocols:
        try:
            location = f"http://{local_ip}:{port}/device.xml"
            UPnPHandler.device_uuid = dev_uuid
            UPnPHandler.friendly_name = name
            UPnPHandler.on_url = staticmethod(_handle)

            server = HTTPServer(("", port), UPnPHandler)
            ssdp = SSDPAdvertiser(dev_uuid, location, local_ip)
            ssdp.start()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            cleanups.extend([server.shutdown, ssdp.stop])
            started.append("dlna")
            print(f"  📺 DLNA    \"{name}\" on {local_ip}:{port}", file=sys.stderr)
        except Exception:
            log.warning("Failed to start DLNA", exc_info=True)
            print(f"  ⚠️  DLNA   failed to start (see --verbose)", file=sys.stderr)

    # ── AirPlay ────────────────────────────────────────────────────
    if "airplay" in protocols:
        try:
            from .airplay import AirPlayReceiver
            airplay_port = port + 1 if "dlna" in protocols else port
            airplay_recv = AirPlayReceiver(name, local_ip, airplay_port, _handle)
            airplay_recv.start()
            cleanups.append(airplay_recv.stop)
            started.append("airplay")
            print(f"  🍎 AirPlay \"{name}\" on {local_ip}:{airplay_port}", file=sys.stderr)
        except Exception:
            log.warning("Failed to start AirPlay", exc_info=True)
            print(f"  ⚠️  AirPlay failed to start (see --verbose)", file=sys.stderr)

    # ── Google Cast ────────────────────────────────────────────────
    if "cast" in protocols:
        try:
            from .cast import CastReceiver
            cast_port = 8009
            cast_recv = CastReceiver(name, local_ip, cast_port, _handle)
            cast_recv.start()
            cleanups.append(cast_recv.stop)
            started.append("cast")
            print(f"  📡 Cast    \"{name}\" on {local_ip}:{cast_port}", file=sys.stderr)
        except Exception:
            log.warning("Failed to start Cast", exc_info=True)
            print(f"  ⚠️  Cast   failed to start (see --verbose)", file=sys.stderr)

    if not started:
        raise RuntimeError("All protocols failed to start")

    enabled = ", ".join(p.upper() for p in started)
    print(f"\n  Protocols: {enabled}", file=sys.stderr)
    print(f"  Open your app > cast > select \"{name}\"\n", file=sys.stderr)

    try:
        event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for fn in cleanups:
            try:
                fn()
            except Exception:
                pass

    if not result:
        raise RuntimeError("No URL captured")
    return result[0]
