# wechat-finder-dlna

[![PyPI](https://img.shields.io/pypi/v/wechat-finder-dlna)](https://pypi.org/project/wechat-finder-dlna/)
[![Python](https://img.shields.io/pypi/pyversions/wechat-finder-dlna)](https://pypi.org/project/wechat-finder-dlna/)
[![License](https://img.shields.io/github/license/gtoxlili/wechat-finder-dlna)](LICENSE)

把你的电脑伪装成一台电视，通过投屏捕获微信视频号的直播流地址。

不用抓包，不用装证书，不用挂代理，不用碰微信客户端 —— 用的就是你家电视投屏那套标准协议。

同时支持 **三种投屏协议**：

| 协议 | 设备发现 | 捕获方式 |
|------|---------|---------|
| **DLNA/UPnP** | SSDP 组播 | `SetAVTransportURI` SOAP 请求 |
| **AirPlay** | mDNS/Bonjour | HTTP `/play` 端点 |
| **Google Cast** | mDNS | Cast V2 `LOAD` 命令（TLS） |

```
┌──────────┐  DLNA / AirPlay  ┌─────────────────────┐
│   微信    │  / Chromecast   │ wechat-finder-dlna  │
│  (手机)   │ ──────────────► │   (你的电脑)         │
└──────────┘   "投屏到电视"   └────────┬────────────┘
                                       │
                              截获 m3u8 直播流地址
                                       │
                                       ▼
                           ffmpeg 录制 / VLC 播放 / ...
```

## 原理

微信视频号投屏用的是标准的 DLNA/UPnP 协议，跟投屏到小米电视、索尼电视没有任何区别。

这个工具同时伪装成三种设备：

1. **DLNA**: 通过 SSDP 组播宣告自己是一台「MediaRenderer」，微信通过 SOAP 请求把 m3u8 地址发过来
2. **AirPlay**: 通过 Bonjour 广播自己是一台 Apple TV，发送端 POST 视频 URL 到 `/play` 端点
3. **Chromecast**: 通过 mDNS 广播自己是一台 Chromecast，在 TLS 加密通道上接收 LOAD 命令中的视频 URL

微信没有任何办法区分这个工具和一台真电视 —— 因为走的就是同一套协议，不存在「检测」一说。

## 安装

```bash
# 推荐用 uv
uv tool install wechat-finder-dlna

# 或者 pip
pip install wechat-finder-dlna
```

Python 3.10+，依赖会自动安装。

## 用法

### 拿直播流地址

```bash
$ wechat-finder-dlna

  📺 DLNA   "wechat-finder-dlna" on 192.168.1.100:9090
  🍎 AirPlay "wechat-finder-dlna" on 192.168.1.100:9091
  📡 Cast   "wechat-finder-dlna" on 192.168.1.100:8009

  Protocols: DLNA, AIRPLAY, CAST
  Open your app > cast > select "wechat-finder-dlna"

  Captured: http://pull-l1.wxlivecdn.com/...m3u8?...
```

手机和电脑在同一个 WiFi 下，打开视频号直播 → 点投屏 → 选设备，URL 就出来了。

### 指定协议

```bash
# 只用 DLNA（原有行为）
wechat-finder-dlna --protocol dlna

# AirPlay + Chromecast
wechat-finder-dlna --protocol airplay cast
```

### 直接录制

```bash
wechat-finder-dlna --record live.mp4 --duration 01:00:00
```

需要系统装了 ffmpeg。

### 丢给播放器

```bash
# VLC
wechat-finder-dlna | xargs vlc

# mpv
wechat-finder-dlna | xargs mpv
```

### 自定义设备名

```bash
wechat-finder-dlna --name "客厅电视"
```

微信投屏列表里显示的就是这个名字。

### 作为库使用

```python
from wechat_finder_dlna import capture

url = capture(name="我的录制器")
print(f"直播流: {url}")

# 指定协议
url = capture(name="我的录制器", protocols=["dlna", "airplay"])
```

## 不只是微信

虽然名字带 wechat，但所有支持 DLNA/AirPlay/Chromecast 投屏的 App 都能用 —— B 站、爱奇艺、优酷、腾讯视频等。

## 环境要求

- Python 3.10+
- 手机和电脑在**同一个局域网**（同一个 WiFi）
- 录制功能需要 `ffmpeg`

## 常见问题

**Q: 能录视频号的回放/点播吗？**

这个工具只能抓直播流。回放/点播的下载是另一个场景，需要用解析微信 Web 同步协议的工具。

**Q: 微信能检测到吗？**

不能。DLNA 是标准协议，微信投屏的时候看到的就是一台普通电视，没有任何异常。

**Q: 为什么投屏列表里看不到设备？**

检查手机和电脑是不是在同一个 WiFi 下。有些路由器会隔离设备（AP 隔离），需要在路由器设置里关掉。如果用的是公司网络，可能有防火墙拦截了组播流量。

**Q: 录制下来的文件没有声音 / 画面花屏？**

试试去掉 ffmpeg 的 `-re` 参数（直接用 `ffmpeg -i <url> -c copy output.mp4`），或者换用 `-c:v libx264 -c:a aac` 重新编码。

## Rust 版本

如果你需要单文件部署、不想装 Python 环境，可以看 [wechat-finder-dlna-rs](https://github.com/gtoxlili/wechat-finder-dlna-rs) —— 功能一致，Rust 异步实现，编译成单个二进制文件。

## 相关项目

- [wechat-finder-dlna-rs](https://github.com/gtoxlili/wechat-finder-dlna-rs) — Rust 重写版，单文件，异步运行时
- [macast](https://github.com/xfangfang/Macast) — 带 GUI 的完整 DLNA 渲染器，用 mpv 播放，功能更全但也更重
- [dlnap](https://github.com/ttymck/dlnap) — 反过来的：从命令行控制局域网里的 DLNA 电视

## 许可证

GPL-3.0
