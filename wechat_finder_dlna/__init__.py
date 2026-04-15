"""wechat-finder-dlna — Capture WeChat Video Channel live stream URLs via DLNA.

Pretends to be a TV on your local network. When you cast a WeChat Video
Channel (视频号) live stream to it, the real m3u8 stream URL is captured.

Zero dependencies. Pure Python 3.10+ standard library.

Example::

    from wechat_finder_dlna import capture

    url = capture(name="My TV")
    print(f"Stream: {url}")
"""

from __future__ import annotations

import sys
import threading
import uuid
from http.server import HTTPServer
from typing import Callable

from .net import get_lan_ip
from .ssdp import SSDPAdvertiser
from .upnp import UPnPHandler

__all__ = ["capture"]


def capture(
    *,
    name: str = "wechat-finder-dlna",
    port: int = 9090,
    on_url: Callable[[str], None] | None = None,
) -> str:
    """Start a fake DLNA renderer and block until a URL is captured.

    Args:
        name: Device name shown in the cast list.
        port: HTTP port for the UPnP device server.
        on_url: Optional callback fired when a URL is captured.

    Returns:
        The captured stream/video URL.
    """
    local_ip = get_lan_ip()
    dev_uuid = f"uuid:{uuid.uuid4()}"
    location = f"http://{local_ip}:{port}/device.xml"

    result: list[str] = []
    event = threading.Event()

    def _handle(url: str) -> None:
        result.append(url)
        if on_url:
            on_url(url)
        event.set()

    UPnPHandler.device_uuid = dev_uuid
    UPnPHandler.friendly_name = name
    UPnPHandler.on_url = staticmethod(_handle)

    server = HTTPServer(("", port), UPnPHandler)
    ssdp = SSDPAdvertiser(dev_uuid, location, local_ip)

    print(f'\n  📺 "{name}" ready on {local_ip}:{port}', file=sys.stderr)
    print(f'     Open WeChat > live/video > cast > select "{name}"\n', file=sys.stderr)

    ssdp.start()
    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()

    try:
        event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        ssdp.stop()

    if not result:
        raise RuntimeError("No URL captured")
    return result[0]
