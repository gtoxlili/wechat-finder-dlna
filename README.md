# wechat-finder-dlna

[中文文档](README_CN.md) · **[Rust version →](https://github.com/gtoxlili/wechat-finder-dlna-rs)**

[![PyPI](https://img.shields.io/pypi/v/wechat-finder-dlna)](https://pypi.org/project/wechat-finder-dlna/)
[![Python](https://img.shields.io/pypi/pyversions/wechat-finder-dlna)](https://pypi.org/project/wechat-finder-dlna/)
[![License](https://img.shields.io/github/license/gtoxlili/wechat-finder-dlna)](LICENSE)

Grab WeChat Video Channel (视频号) live stream URLs by faking a TV on your LAN.

No proxy, no certificate, no MITM — just standard casting protocols your smart TV already speaks.

Supports **three casting protocols** simultaneously:

| Protocol | Discovery | How it captures |
|----------|-----------|----------------|
| **DLNA/UPnP** | SSDP multicast | `SetAVTransportURI` SOAP action |
| **AirPlay** | mDNS/Bonjour | HTTP `/play` endpoint |
| **Google Cast** | mDNS | Cast V2 `LOAD` command over TLS |

```
┌──────────┐  DLNA / AirPlay  ┌─────────────────────┐
│  WeChat   │  / Chromecast   │ wechat-finder-dlna  │
│  (phone)  │ ──────────────► │ (your computer)     │
└──────────┘   "投屏"         └────────┬────────────┘
                                       │
                              captures the m3u8 URL
                                       │
                                       ▼
                              ffmpeg / VLC / mpv / ...
```

The tool advertises itself as a media receiver on the local network using all three protocols.
When you cast a live stream from WeChat (or Bilibili, iQiyi, Youku — anything that supports casting),
the app sends the real stream URL. We grab it and either print it or pipe it straight into ffmpeg.

WeChat can't tell the difference between this and a real TV — there's nothing to detect.

## Install

```bash
# uv (recommended)
uv tool install wechat-finder-dlna

# pip
pip install wechat-finder-dlna
```

Python 3.10+, dependencies are installed automatically.

## Quick start

```bash
# All protocols enabled by default
wechat-finder-dlna

# DLNA only (original behavior)
wechat-finder-dlna --protocol dlna

# AirPlay + Chromecast only
wechat-finder-dlna --protocol airplay cast

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

# Specify protocols
url = capture(name="My TV", protocols=["dlna", "airplay"])
```

## Requirements

- Python 3.10+
- Phone and computer on the **same WiFi / LAN**
- `ffmpeg` only if you use `--record`
## How it works

### DLNA/UPnP
1. **SSDP multicast** — announces a MediaRenderer on `239.255.255.250:1900`.
2. **UPnP device description** — replies with XML descriptor that looks like a TV.
3. **SOAP control** — the app sends `SetAVTransportURI` with the stream URL.

### AirPlay
1. **mDNS/Bonjour** — advertises an `_airplay._tcp` service via zeroconf.
2. **HTTP server** — handles the `/play` endpoint where senders POST the video URL.

### Google Cast (Chromecast)
1. **mDNS** — advertises a `_googlecast._tcp` service.
2. **TLS + Cast V2** — runs a TLS server on port 8009 speaking the Cast protobuf protocol.
3. **Media LOAD** — captures the `contentId` URL from the sender's LOAD command.

## FAQ

**Does this work with replays / VODs?**
This is for live streams. For VOD downloads, look into tools that use the WeChat Web sync protocol.

**Does this work with apps other than WeChat?**
Yes — any app that supports DLNA/AirPlay/Chromecast casting works. Bilibili, iQiyi, Youku, Tencent Video, etc.

**Can WeChat detect or block this?**
No. The protocol is standard UPnP/DLNA. From WeChat's perspective this is just another TV on the network.

## Rust version

> **[wechat-finder-dlna-rs](https://github.com/gtoxlili/wechat-finder-dlna-rs)** — same protocols, async Rust, compiles to a single static binary with zero runtime dependencies. Better suited for long-running capture, embedded/NAS deployment, or if you don't want a Python runtime.

## See also

- [dlnap](https://github.com/ttymck/dlnap) — control DLNA renderers from the command line (the other direction: you push *to* a TV)
- [macast](https://github.com/xfangfang/Macast) — full-featured DLNA renderer with GUI, uses mpv for playback

## License

GPL-3.0
