# wechat-finder-dlna

Capture WeChat Video Channel (视频号) live stream URLs by pretending to be a TV on your network.

**No proxy. No certificate. No MITM. No hacking the WeChat client.**

Just standard DLNA screen casting — the same protocol your smart TV uses.

## How it works

```
┌──────────┐    DLNA cast     ┌─────────────────────┐
│  WeChat   │ ──────────────► │ wechat-finder-dlna  │
│  (phone)  │  "cast to TV"   │ (your computer)     │
└──────────┘                  └────────┬────────────┘
                                       │
                              Captures m3u8 URL
                                       │
                                       ▼
                              ffmpeg / VLC / mpv
```

1. This tool advertises itself as a DLNA MediaRenderer (like a smart TV)
2. You open a live stream in WeChat and tap "投屏" (cast)
3. WeChat sends the real stream URL to our fake TV via standard DLNA protocol
4. We capture the URL and either print it or start recording with ffmpeg

## Install

```bash
# uv (recommended)
uv tool install wechat-finder-dlna

# or pip
pip install wechat-finder-dlna
```

Or just download the single file — zero dependencies, pure Python 3.10+ stdlib:

```bash
curl -O https://raw.githubusercontent.com/user/wechat-finder-dlna/main/wechat_finder_dlna.py
python wechat_finder_dlna.py
```

## Usage

### Capture URL only

```bash
$ wechat-finder-dlna

  📺 "wechat-finder-dlna" ready on 192.168.1.100:9090
     Open WeChat > live/video > cast > select "wechat-finder-dlna"

  Captured: http://pull-l1.wxlivecdn.com/...m3u8?...

http://pull-l1.wxlivecdn.com/...m3u8?...
```

### Record with ffmpeg

```bash
wechat-finder-dlna --record live.mp4 --duration 01:00:00
```

### Pipe to VLC

```bash
wechat-finder-dlna | xargs vlc
```

### Custom device name

```bash
wechat-finder-dlna --name "Living Room TV"
```

### Use as a library

```python
from wechat_finder_dlna import capture

url = capture(name="My Recorder")
print(f"Stream URL: {url}")
# → ffmpeg, requests, or whatever you want
```

## Requirements

- Python 3.10+
- Phone and computer on the **same local network** (same WiFi)
- ffmpeg (only if using `--record`)

## FAQ

**Q: Does this work with WeChat Video Channel (视频号) replays/VODs?**

A: This tool is for **live streams**. For VOD/replay downloads, see other tools that parse the WeChat Web sync API.

**Q: Can WeChat detect this?**

A: No. This uses the standard DLNA/UPnP protocol — exactly the same as any smart TV. WeChat has no way to distinguish our tool from a real TV.

**Q: Does this work with other apps?**

A: Yes! Any app that supports DLNA casting (Bilibili, iQiyi, Youku, etc.) will work. The captured URL can be used with ffmpeg, VLC, or any player.

## License

GPL-3.0
