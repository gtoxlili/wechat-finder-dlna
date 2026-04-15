# wechat-finder-dlna

[中文文档](README_CN.md)

[![PyPI](https://img.shields.io/pypi/v/wechat-finder-dlna)](https://pypi.org/project/wechat-finder-dlna/)
[![Python](https://img.shields.io/pypi/pyversions/wechat-finder-dlna)](https://pypi.org/project/wechat-finder-dlna/)
[![License](https://img.shields.io/github/license/gtoxlili/wechat-finder-dlna)](LICENSE)

Grab WeChat Video Channel (视频号) live stream URLs by faking a TV on your LAN.

No proxy, no certificate, no MITM — just plain DLNA, the same protocol your smart TV speaks.

```
┌──────────┐    DLNA cast     ┌─────────────────────┐
│  WeChat   │ ──────────────► │ wechat-finder-dlna  │
│  (phone)  │  "投屏"         │ (your computer)     │
└──────────┘                  └────────┬────────────┘
                                       │
                              captures the m3u8 URL
                                       │
                                       ▼
                              ffmpeg / VLC / mpv / ...
```

The tool advertises itself as a UPnP MediaRenderer on the local network.
When you cast a live stream from WeChat (or Bilibili, iQiyi, Youku — anything that supports DLNA),
the app sends the real stream URL over via the standard `SetAVTransportURI` SOAP action.
We grab it and either print it or pipe it straight into ffmpeg.

WeChat can't tell the difference between this and a real TV — there's nothing to detect.

## Install

```bash
# uv (recommended)
uv tool install wechat-finder-dlna

# pip
pip install wechat-finder-dlna
```

Pure Python 3.10+, zero external dependencies.

## Quick start

```bash
# Print the captured URL to stdout
wechat-finder-dlna

# Record to file (needs ffmpeg)
wechat-finder-dlna --record live.mp4 --duration 01:00:00

# Pipe to VLC
wechat-finder-dlna | xargs vlc

# Show up as "Living Room TV" in the cast list
wechat-finder-dlna --name "Living Room TV"
```

As a library:

```python
from wechat_finder_dlna import capture

url = capture(name="My Recorder")
# do whatever you want with the m3u8 URL
```

## Requirements

- Python 3.10+
- Phone and computer on the **same WiFi / LAN**
- `ffmpeg` only if you use `--record`

## How it works

1. **SSDP multicast** — the tool announces a MediaRenderer on `239.255.255.250:1900` so cast-capable apps can discover it.
2. **UPnP device description** — when an app queries the device, we reply with a minimal XML descriptor that looks like a TV.
3. **SOAP control** — the app sends `SetAVTransportURI` with the real stream URL. We extract the URL from the SOAP body and we're done.

The entire implementation is ~500 lines of stdlib Python (http.server, socket, xml, threading). No C extensions, no compiled bits.

## FAQ

**Does this work with replays / VODs?**
This is for live streams. For VOD downloads, look into tools that use the WeChat Web sync protocol.

**Does this work with apps other than WeChat?**
Yes — any app that supports DLNA casting works. Bilibili, iQiyi, Youku, Tencent Video, etc.

**Can WeChat detect or block this?**
No. The protocol is standard UPnP/DLNA. From WeChat's perspective this is just another TV on the network.

## See also

- [dlnap](https://github.com/ttymck/dlnap) — control DLNA renderers from the command line (the other direction: you push *to* a TV)
- [macast](https://github.com/xfangfang/Macast) — full-featured DLNA renderer with GUI, uses mpv for playback

## License

GPL-3.0
