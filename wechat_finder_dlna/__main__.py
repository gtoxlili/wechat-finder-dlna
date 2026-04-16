"""CLI entry point — ``python -m wechat_finder_dlna`` or ``wechat-finder-dlna``."""

from __future__ import annotations

import argparse
import logging
import shutil
import signal
import subprocess
import sys

from . import PROTOCOLS, capture


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Capture WeChat Video Channel (视频号) live stream URLs "
            "via fake screen casting (DLNA / AirPlay / Chromecast)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s                              # all protocols, print captured URL
  %(prog)s --protocol dlna              # DLNA only
  %(prog)s --protocol airplay cast      # AirPlay + Chromecast
  %(prog)s --record live.mp4            # record with ffmpeg
  %(prog)s --name "Living Room TV"      # custom device name
  %(prog)s | xargs vlc                  # pipe to VLC
""",
    )
    parser.add_argument(
        "--name",
        default="MAGI",
        help="device name shown in cast list (default: MAGI)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9090,
        help="base HTTP port for DLNA (default: 9090)",
    )
    parser.add_argument(
        "--protocol",
        nargs="+",
        choices=PROTOCOLS,
        default=None,
        metavar="PROTO",
        help=f"protocols to enable (choices: {', '.join(PROTOCOLS)}; default: all)",
    )
    parser.add_argument(
        "--record",
        metavar="FILE",
        help="auto-record to FILE with ffmpeg after capture",
    )
    parser.add_argument(
        "--duration",
        metavar="HH:MM:SS",
        help="recording duration (ffmpeg format, e.g. 01:00:00)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )
    else:
        logging.basicConfig(level=logging.WARNING)

    # Parse --duration HH:MM:SS to seconds for audio capture
    audio_dur = None
    if args.duration:
        parts = args.duration.split(":")
        audio_dur = sum(float(p) * m for p, m in zip(parts, [3600, 60, 1]))

    url = capture(
        name=args.name,
        port=args.port,
        protocols=args.protocol,
        audio_output=args.record,
        audio_duration=audio_dur,
    )
    print(f"\n  Captured: {url}\n", file=sys.stderr)

    # AirPlay audio capture returns the file path directly — no ffmpeg needed.
    if args.record and url == args.record:
        print(f"  Saved to {url}", file=sys.stderr)
    elif args.record:
        _record(url, args.record, args.duration)
    else:
        print(url)


def _record(url: str, output: str, duration: str | None) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("Error: ffmpeg not found in PATH", file=sys.stderr)
        sys.exit(1)

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "info", "-re", "-i", url, "-c", "copy"]
    if duration:
        cmd.extend(["-t", duration])
    cmd.extend(["-y", output])

    print(f"  Recording to {output}...\n", file=sys.stderr)
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
    signal.signal(signal.SIGINT, lambda *_: proc.send_signal(signal.SIGINT))
    proc.wait()
    print(f"\n  Saved to {output}", file=sys.stderr)


if __name__ == "__main__":
    main()
