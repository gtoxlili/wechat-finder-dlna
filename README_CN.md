# wechat-finder-dlna

把你的电脑伪装成一台电视，通过 DLNA 投屏捕获微信视频号的直播流地址。

不用抓包，不用装证书，不用挂代理，不用碰微信客户端 —— 用的就是你家电视投屏那套标准协议。

```
┌──────────┐     DLNA 投屏     ┌─────────────────────┐
│   微信    │ ──────────────► │ wechat-finder-dlna  │
│  (手机)   │   "投屏到电视"   │   (你的电脑)         │
└──────────┘                  └────────┬────────────┘
                                       │
                              截获 m3u8 直播流地址
                                       │
                                       ▼
                           ffmpeg 录制 / VLC 播放 / ...
```

## 原理

微信视频号投屏用的是标准的 DLNA/UPnP 协议，跟投屏到小米电视、索尼电视没有任何区别。

这个工具做的事情很简单：

1. 在局域网里通过 SSDP 组播宣告自己是一台「MediaRenderer」（电视）
2. 你在微信里打开一个直播，点「投屏」，选我们的设备
3. 微信通过标准 SOAP 请求把真实的 m3u8 直播流地址发过来
4. 我们把地址截下来，打印出来或者直接喂给 ffmpeg 录制

微信没有任何办法区分这个工具和一台真电视 —— 因为走的就是同一套协议，不存在「检测」一说。

## 安装

```bash
# 推荐用 uv
uv tool install wechat-finder-dlna

# 或者 pip
pip install wechat-finder-dlna
```

纯 Python 3.10+ 标准库实现，零外部依赖，装完直接用。

## 用法

### 拿直播流地址

```bash
$ wechat-finder-dlna

  📺 "wechat-finder-dlna" ready on 192.168.1.100:9090
     Open WeChat > live/video > cast > select "wechat-finder-dlna"

  Captured: http://pull-l1.wxlivecdn.com/...m3u8?...

http://pull-l1.wxlivecdn.com/...m3u8?...
```

手机和电脑在同一个 WiFi 下，打开视频号直播 → 点投屏 → 选设备，URL 就出来了。

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
# 拿到 URL 之后想怎么处理都行
```

## 不只是微信

虽然名字带 wechat，但所有支持 DLNA 投屏的 App 都能用 —— B 站、爱奇艺、优酷、腾讯视频，底层都是同一套 UPnP 协议。

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

## 相关项目

- [macast](https://github.com/xfangfang/Macast) — 带 GUI 的完整 DLNA 渲染器，用 mpv 播放，功能更全但也更重
- [dlnap](https://github.com/ttymck/dlnap) — 反过来的：从命令行控制局域网里的 DLNA 电视

## 许可证

GPL-3.0
