"""Capture AirPlay RTP audio stream to a raw AAC file.

Listens on a UDP port, receives RTP packets from the AirPlay sender,
optionally decrypts with ChaCha20-Poly1305 (using the stream key from
SETUP), strips RTP framing, and writes raw AAC frames to disk.

The output file can be played with::

    ffplay -f aac output.aac
"""

from __future__ import annotations

import logging
import socket
import threading

log = logging.getLogger(__name__)


class AudioCapture:
    """UDP listener that captures AirPlay RTP audio to a file."""

    def __init__(self, bind_addr: str = "", on_done: callable | None = None):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((bind_addr, 0))
        self.port = self._sock.getsockname()[1]
        self._shk: bytes | None = None
        self._running = False
        self._file = None
        self._thread: threading.Thread | None = None
        self._pkt_count = 0
        self._on_done = on_done
        self._output_path: str | None = None

    def start(
        self, output_path: str, shk: bytes | None = None, duration: float | None = None
    ) -> None:
        self._shk = shk
        self._duration = duration
        self._output_path = output_path
        self._file = open(output_path, "wb")
        self._pkt_count = 0
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.debug(
            "AudioCapture: listening on UDP :%d → %s (shk=%s, duration=%s)",
            self.port,
            output_path,
            "yes" if shk else "no",
            duration,
        )

    def stop(self) -> int:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._file:
            self._file.close()
            self._file = None
        n = self._pkt_count
        log.debug("AudioCapture: stopped, %d packets captured", n)
        return n

    @property
    def active(self) -> bool:
        return self._running

    def _loop(self) -> None:
        import time

        start = time.monotonic()
        self._sock.settimeout(1.0)
        while self._running:
            if self._duration and (time.monotonic() - start) >= self._duration:
                log.debug("AudioCapture: duration limit reached")
                break
            try:
                data, _ = self._sock.recvfrom(8192)
                self._handle_packet(data)
            except socket.timeout:
                continue
            except OSError:
                break
        self._running = False
        if self._file:
            self._file.close()
            self._file = None
        if self._on_done and self._output_path:
            self._on_done(self._output_path)

    def _handle_packet(self, data: bytes) -> None:
        # Minimum: 12 (RTP header) + 1 (payload) + 16 (tag) + 8 (nonce)
        if len(data) < 37:
            return

        nonce = data[-8:]
        tag = data[-24:-8]
        aad = data[4:12]  # timestamp + SSRC
        payload = data[12:-24]

        if not payload:
            return

        if self._shk:
            from Crypto.Cipher import ChaCha20_Poly1305

            try:
                c = ChaCha20_Poly1305.new(key=self._shk, nonce=nonce)
                c.update(aad)
                audio_data = c.decrypt_and_verify(payload, tag)
            except ValueError:
                # Decryption failed — might be a control packet
                return
        else:
            # No stream key — audio may be unencrypted.
            # Try treating the full payload (without tag/nonce) as audio.
            audio_data = payload

        if audio_data and self._file:
            # Wrap raw AAC frame in ADTS header so the file is playable
            self._file.write(self._adts_header(len(audio_data)))
            self._file.write(audio_data)
            self._pkt_count += 1

    @staticmethod
    def _adts_header(frame_len: int) -> bytes:
        """7-byte ADTS header for AAC-LC, 44100 Hz, stereo."""
        total = frame_len + 7
        return bytes(
            [
                0xFF,
                0xF1,  # MPEG-4, no CRC
                0x50,  # AAC-LC, 44100 Hz
                0x80 | ((total >> 11) & 0x03),
                (total >> 3) & 0xFF,
                ((total & 0x07) << 5) | 0x1F,
                0xFC,  # VBR
            ]
        )
