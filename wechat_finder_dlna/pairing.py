"""Minimal AirPlay authentication: transient pair-setup, pair-verify,
FairPlay stub, and HAP encrypted-socket wrapper.

Ported from openairplay/airplay2-receiver (GPLv2) — only the subset
required for video-URL capture is included.  The heavyweight
``fairplay3.py`` (AES stream decryption) is **not** needed.

Sole new dependency: ``cryptography`` (Ed25519, X25519, ChaCha20-Poly1305,
HKDF — replaces the original project's pycryptodome + hkdf + srptools).
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import logging
import struct
from hashlib import sha512

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

log = logging.getLogger(__name__)

# ── HKDF-SHA-256 helpers (matching the hkdf library defaults) ────


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return _hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """Single-block HKDF-Expand (SHA-256).  length ≤ 32."""
    return _hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


# ── ChaCha20-Poly1305 helpers ───────────────────────────────────


def _cc_encrypt(key: bytes, nonce: bytes, pt: bytes) -> tuple[bytes, bytes]:
    nonce = nonce.rjust(12, b"\x00")
    ct = ChaCha20Poly1305(key).encrypt(nonce, pt, None)
    return ct[:-16], ct[-16:]


def _cc_decrypt(key: bytes, nonce: bytes, ct: bytes, tag: bytes) -> bytes:
    nonce = nonce.rjust(12, b"\x00")
    return ChaCha20Poly1305(key).decrypt(nonce, ct + tag, None)


# ── TLV8 codec ──────────────────────────────────────────────────


class _Tag:
    METHOD = 0
    SALT = 2
    PUBLICKEY = 3
    PROOF = 4
    ENCRYPTEDDATA = 5
    STATE = 6
    ERROR = 7
    FLAGS = 19
    IDENTIFIER = 1
    SIGNATURE = 10


def _tlv_decode(data: bytes) -> dict[int, bytes]:
    res: dict[int, bytes] = {}
    ptr = 0
    while ptr < len(data):
        tag, length = data[ptr], data[ptr + 1]
        value = data[ptr + 2 : ptr + 2 + length]
        res[tag] = res.get(tag, b"") + value
        ptr += 2 + length
    return res


def _tlv_encode(items: list) -> bytes:
    out = b""
    for i in range(0, len(items), 2):
        tag, value = items[i], items[i + 1]
        length = len(value)
        if length <= 255:
            out += bytes([tag, length]) + value
        else:
            for j in range(length // 255):
                out += bytes([tag, 0xFF]) + value[j * 255 : (j + 1) * 255]
            left = length % 255
            out += bytes([tag, left]) + value[-left:]
    return out


# ── SRP-6a server (3072-bit, SHA-512) ───────────────────────────

_N = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E08"
    "8A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B"
    "302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9"
    "A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE6"
    "49286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8"
    "FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C"
    "180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718"
    "3995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D"
    "04507A33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7D"
    "B3970F85A6E1E4C7ABF5AE8CDB0933D71E8C94E04A25619DCEE3D226"
    "1AD2EE6BF12FFA06D98A0864D87602733EC86A64521F2B18177B200C"
    "BBE117577A615D6C770988C0BAD946E208E24FA074E5AB3143DB5BFC"
    "E0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF",
    16,
)
_PAD = _N.bit_length() // 8
_g = 5


def _H(*args: int | bytes | str, pad: bool = False, sep: bytes = b"") -> int:
    parts: list[bytes] = []
    for a in args:
        if isinstance(a, int):
            b = a.to_bytes(max(1, (a.bit_length() + 7) // 8), "big")
        elif isinstance(a, str):
            b = a.encode()
        else:
            b = a
        if pad:
            b = b.rjust(_PAD, b"\x00")
        parts.append(b)
    return int(sha512(sep.join(parts)).hexdigest(), 16)


def _to_bytes(v: int) -> bytes:
    return v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big")


def _rand(bits: int = 512) -> int:
    import random

    return random.SystemRandom().getrandbits(bits) % _N


class _SRPServer:
    def __init__(self, username: bytes, password: bytes):
        self._u = username
        k = _H(_N, _g, pad=True)
        self._s = _rand(128)
        x = _H(self._s, _H(username, password, sep=b":"))
        self._v = pow(_g, x, _N)
        self._b = _rand()
        self._B = (k * self._v + pow(_g, self._b, _N)) % _N
        self._A = 0
        self._K = 0
        self._M1 = 0
        self._M2 = 0

    @property
    def salt(self) -> bytes:
        return _to_bytes(self._s)

    @property
    def public_key(self) -> bytes:
        return _to_bytes(self._B)

    @property
    def proof(self) -> bytes:
        return _to_bytes(self._M2)

    @property
    def session_key(self) -> bytes:
        # SHA-512 output is always 64 bytes; pad to fixed length so both
        # sides derive identical HKDF keys regardless of leading zeros.
        return self._K.to_bytes(64, "big")

    def set_client_public(self, A_bytes: bytes) -> None:
        self._A = int.from_bytes(A_bytes, "big")
        u = _H(self._A, self._B, pad=True)
        S = pow(self._A * pow(self._v, u, _N), self._b, _N) % _N
        self._K = _H(S)
        self._M1 = _H(_H(_N) ^ _H(_g), _H(self._u), self._s, self._A, self._B, self._K)

    def verify(self, proof: bytes) -> bool:
        if self._M1 != int.from_bytes(proof, "big"):
            raise ValueError("SRP authentication failed")
        self._M2 = _H(self._A, self._M1, self._K)
        return True


# ── FairPlay stub ───────────────────────────────────────────────

_FP_REPLIES = [
    b"\x46\x50\x4c\x59\x03\x01\x02\x00\x00\x00\x00\x82\x02\x00\x0f\x9f\x3f\x9e\x0a\x25\x21\xdb\xdf\x31\x2a\xb2\xbf\xb2\x9e\x8d\x23\x2b\x63\x76\xa8\xc8\x18\x70\x1d\x22\xae\x93\xd8\x27\x37\xfe\xaf\x9d\xb4\xfd\xf4\x1c\x2d\xba\x9d\x1f\x49\xca\xaa\xbf\x65\x91\xac\x1f\x7b\xc6\xf7\xe0\x66\x3d\x21\xaf\xe0\x15\x65\x95\x3e\xab\x81\xf4\x18\xce\xed\x09\x5a\xdb\x7c\x3d\x0e\x25\x49\x09\xa7\x98\x31\xd4\x9c\x39\x82\x97\x34\x34\xfa\xcb\x42\xc6\x3a\x1c\xd9\x11\xa6\xfe\x94\x1a\x8a\x6d\x4a\x74\x3b\x46\xc3\xa7\x64\x9e\x44\xc7\x89\x55\xe4\x9d\x81\x55\x00\x95\x49\xc4\xe2\xf7\xa3\xf6\xd5\xba",
    b"\x46\x50\x4c\x59\x03\x01\x02\x00\x00\x00\x00\x82\x02\x01\xcf\x32\xa2\x57\x14\xb2\x52\x4f\x8a\xa0\xad\x7a\xf1\x64\xe3\x7b\xcf\x44\x24\xe2\x00\x04\x7e\xfc\x0a\xd6\x7a\xfc\xd9\x5d\xed\x1c\x27\x30\xbb\x59\x1b\x96\x2e\xd6\x3a\x9c\x4d\xed\x88\xba\x8f\xc7\x8d\xe6\x4d\x91\xcc\xfd\x5c\x7b\x56\xda\x88\xe3\x1f\x5c\xce\xaf\xc7\x43\x19\x95\xa0\x16\x65\xa5\x4e\x19\x39\xd2\x5b\x94\xdb\x64\xb9\xe4\x5d\x8d\x06\x3e\x1e\x6a\xf0\x7e\x96\x56\x16\x2b\x0e\xfa\x40\x42\x75\xea\x5a\x44\xd9\x59\x1c\x72\x56\xb9\xfb\xe6\x51\x38\x98\xb8\x02\x27\x72\x19\x88\x57\x16\x50\x94\x2a\xd9\x46\x68\x8a",
    b"\x46\x50\x4c\x59\x03\x01\x02\x00\x00\x00\x00\x82\x02\x02\xc1\x69\xa3\x52\xee\xed\x35\xb1\x8c\xdd\x9c\x58\xd6\x4f\x16\xc1\x51\x9a\x89\xeb\x53\x17\xbd\x0d\x43\x36\xcd\x68\xf6\x38\xff\x9d\x01\x6a\x5b\x52\xb7\xfa\x92\x16\xb2\xb6\x54\x82\xc7\x84\x44\x11\x81\x21\xa2\xc7\xfe\xd8\x3d\xb7\x11\x9e\x91\x82\xaa\xd7\xd1\x8c\x70\x63\xe2\xa4\x57\x55\x59\x10\xaf\x9e\x0e\xfc\x76\x34\x7d\x16\x40\x43\x80\x7f\x58\x1e\xe4\xfb\xe4\x2c\xa9\xde\xdc\x1b\x5e\xb2\xa3\xaa\x3d\x2e\xcd\x59\xe7\xee\xe7\x0b\x36\x29\xf2\x2a\xfd\x16\x1d\x87\x73\x53\xdd\xb9\x9a\xdc\x8e\x07\x00\x6e\x56\xf8\x50\xce",
    b"\x46\x50\x4c\x59\x03\x01\x02\x00\x00\x00\x00\x82\x02\x03\x90\x01\xe1\x72\x7e\x0f\x57\xf9\xf5\x88\x0d\xb1\x04\xa6\x25\x7a\x23\xf5\xcf\xff\x1a\xbb\xe1\xe9\x30\x45\x25\x1a\xfb\x97\xeb\x9f\xc0\x01\x1e\xbe\x0f\x3a\x81\xdf\x5b\x69\x1d\x76\xac\xb2\xf7\xa5\xc7\x08\xe3\xd3\x28\xf5\x6b\xb3\x9d\xbd\xe5\xf2\x9c\x8a\x17\xf4\x81\x48\x7e\x3a\xe8\x63\xc6\x78\x32\x54\x22\xe6\xf7\x8e\x16\x6d\x18\xaa\x7f\xd6\x36\x25\x8b\xce\x28\x72\x6f\x66\x1f\x73\x88\x93\xce\x44\x31\x1e\x4b\xe6\xc0\x53\x51\x93\xe5\xef\x72\xe8\x68\x62\x33\x72\x9c\x22\x7d\x82\x0c\x99\x94\x45\xd8\x92\x46\xc8\xc3\x59",
]

_FP_HEADER = bytes.fromhex("46504c590301040000000014")


def fairplay_setup(request: bytes) -> bytes | None:
    """Return hardcoded FairPlay response, or *None* on unknown request."""
    if len(request) < 15 or request[4] != 3:
        return None
    type_, seq = request[5], request[6]
    if type_ == 1:  # SETUP message
        if seq == 1:
            mode = request[14]
            if mode < len(_FP_REPLIES):
                return _FP_REPLIES[mode]
        elif seq == 3:
            return _FP_HEADER + request[-20:]
    return None


# ── HAP session (transient pair-setup + pair-verify) ─────────────


class HapSession:
    """Per-connection AirPlay pairing state."""

    def __init__(
        self,
        identifier: bytes = b"AirPlayReceiver",
        ltsk: ed25519.Ed25519PrivateKey | None = None,
    ):
        self._id = identifier
        # Use caller-supplied keypair so mDNS pk and session pk match,
        # or generate a fresh one.
        self._ltsk = ltsk or ed25519.Ed25519PrivateKey.generate()
        self._ltpk = self._ltsk.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.encrypted = False
        self.shared_key: bytes | None = None
        self._srp: _SRPServer | None = None
        # pair-verify state
        self._cv_priv: x25519.X25519PrivateKey | None = None
        self._cv_pub: bytes = b""
        self._client_cv_pub: bytes = b""

    @property
    def public_key_hex(self) -> str:
        return self._ltpk.hex()

    # ── pair-setup (transient, 2 round-trips) ────────────────────

    def pair_setup(self, body: bytes) -> bytes:
        req = _tlv_decode(body)
        if _Tag.STATE not in req:
            log.warning("pair-setup: no STATE tag in request (%d bytes)", len(body))
            return _tlv_encode([_Tag.STATE, b"\x02", _Tag.ERROR, b"\x01"])
        state = req[_Tag.STATE]
        if state == b"\x01":
            return self._ps_m2()
        if state == b"\x03":
            return self._ps_m4(req)
        return _tlv_encode([_Tag.STATE, b"\x02", _Tag.ERROR, b"\x01"])

    def _ps_m2(self) -> bytes:
        self._srp = _SRPServer(b"Pair-Setup", b"3939")
        return _tlv_encode(
            [
                _Tag.STATE,
                b"\x02",
                _Tag.SALT,
                self._srp.salt,
                _Tag.PUBLICKEY,
                self._srp.public_key,
            ]
        )

    def _ps_m4(self, req: dict[int, bytes]) -> bytes:
        assert self._srp is not None
        self._srp.set_client_public(req[_Tag.PUBLICKEY])
        try:
            self._srp.verify(req[_Tag.PROOF])
        except (ValueError, AssertionError):
            log.warning("pair-setup: SRP verification failed")
            return _tlv_encode([_Tag.STATE, b"\x04", _Tag.ERROR, b"\x02"])
        self.shared_key = self._srp.session_key
        self.encrypted = True
        log.debug("pair-setup: transient pairing succeeded")
        return _tlv_encode(
            [
                _Tag.STATE,
                b"\x04",
                _Tag.PROOF,
                self._srp.proof,
            ]
        )

    # ── pair-verify (2 round-trips) ──────────────────────────────

    def pair_verify(self, body: bytes) -> bytes:
        req = _tlv_decode(body)
        state = req[_Tag.STATE]
        if state == b"\x01":
            return self._pv_m2(req[_Tag.PUBLICKEY])
        if state == b"\x03":
            return self._pv_m4(req[_Tag.ENCRYPTEDDATA])
        return _tlv_encode([_Tag.STATE, b"\x02", _Tag.ERROR, b"\x01"])

    def _pv_m2(self, client_pub: bytes) -> bytes:
        self._client_cv_pub = client_pub
        self._cv_priv = x25519.X25519PrivateKey.generate()
        self._cv_pub = self._cv_priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.shared_key = self._cv_priv.exchange(
            x25519.X25519PublicKey.from_public_bytes(client_pub)
        )
        info = self._cv_pub + self._id + client_pub
        sig = self._ltsk.sign(info)
        sub_tlv = _tlv_encode(
            [
                _Tag.IDENTIFIER,
                self._id,
                _Tag.SIGNATURE,
                sig,
            ]
        )
        prk = _hkdf_extract(b"Pair-Verify-Encrypt-Salt", self.shared_key)
        skey = _hkdf_expand(prk, b"Pair-Verify-Encrypt-Info", 32)
        enc, tag = _cc_encrypt(skey, b"PV-Msg02", sub_tlv)
        return _tlv_encode(
            [
                _Tag.STATE,
                b"\x02",
                _Tag.PUBLICKEY,
                self._cv_pub,
                _Tag.ENCRYPTEDDATA,
                enc + tag,
            ]
        )

    def _pv_m4(self, encrypted: bytes) -> bytes:
        prk = _hkdf_extract(b"Pair-Verify-Encrypt-Salt", self.shared_key)
        skey = _hkdf_expand(prk, b"Pair-Verify-Encrypt-Info", 32)
        try:
            _cc_decrypt(skey, b"PV-Msg03", encrypted[:-16], encrypted[-16:])
            self.encrypted = True
            log.debug("pair-verify: succeeded")
        except Exception:
            log.warning("pair-verify: decryption failed")
            return _tlv_encode([_Tag.STATE, b"\x04", _Tag.ERROR, b"\x02"])
        return _tlv_encode([_Tag.STATE, b"\x04"])


# ── HAPSocket — encrypted socket wrapper ────────────────────────


class HAPSocket:
    """Wraps a TCP socket with HAP (ChaCha20-Poly1305) encryption.

    After pair-setup completes, all further HTTP traffic is encrypted
    using per-direction keys derived from the shared secret.
    """

    MAX_BLOCK = 0x400
    LEN_SIZE = 2

    def __init__(self, sock, shared_key: bytes):
        self.socket = sock
        self.out_count = 0
        self.in_count = 0

        # HKDF-SHA-512 for control channel keys (per HAP spec / shairport-sync)
        prk = _hmac.new(b"Control-Salt", shared_key, hashlib.sha512).digest()
        self.out_key = _hmac.new(
            prk, b"Control-Read-Encryption-Key\x01", hashlib.sha512
        ).digest()[:32]
        self.in_key = _hmac.new(
            prk, b"Control-Write-Encryption-Key\x01", hashlib.sha512
        ).digest()[:32]

        self._in_buf: bytes | None = None
        self._in_total = 0
        self._in_got = 0

    # ── proxy unknown attrs to the real socket ───────────────────

    def __getattr__(self, name):
        return getattr(self.socket, name)

    def _get_io_refs(self):
        return self.socket._io_refs

    def _set_io_refs(self, v):
        self.socket._io_refs = v

    _io_refs = property(_get_io_refs, _set_io_refs)

    def makefile(self, *args, **kwargs):
        import socket as _sock_mod

        return _sock_mod.socket.makefile(self, *args, **kwargs)

    # ── receive (decrypt) ────────────────────────────────────────

    def recv_into(self, buf, nbytes=1042, flags=0):
        data = self.recv(nbytes, flags)
        for i, b in enumerate(data):
            buf[i] = b
        return len(data)

    def recv(self, buflen=1042, flags=0):
        result = b""
        while buflen > 1:
            if self._in_buf is None:
                if buflen < self.LEN_SIZE:
                    return result
                raw = self.socket.recv(self.LEN_SIZE)
                if not raw:
                    return result
                if len(raw) < self.LEN_SIZE:
                    raw += self.socket.recv(self.LEN_SIZE - len(raw))
                block_len = struct.unpack("<H", raw)[0]
                self._in_total = block_len + 16
                self._in_got = 0
                self._in_buf = b""
                buflen -= self.LEN_SIZE
            else:
                part = self.socket.recv(min(buflen, self._in_total - self._in_got))
                if not part:
                    return result
                self._in_buf += part
                buflen -= len(part)
                self._in_got += len(part)
                if self._in_got == self._in_total:
                    nonce = struct.pack("Q", self.in_count).rjust(12, b"\x00")
                    block_len = self._in_total - 16
                    from Crypto.Cipher import ChaCha20_Poly1305 as CC

                    c = CC.new(key=self.in_key, nonce=nonce)
                    c.update(struct.pack("H", block_len))
                    result += c.decrypt_and_verify(
                        self._in_buf[:-16], self._in_buf[-16:]
                    )
                    self.in_count += 1
                    self._in_buf = None
                    break
        return result

    # ── send (encrypt) ───────────────────────────────────────────

    def send(self, data, flags=0):
        return self.sendall(data, flags)

    def sendall(self, data, flags=0):
        from Crypto.Cipher import ChaCha20_Poly1305 as CC

        result = b""
        offset = 0
        total = len(data)
        while offset < total:
            length = min(total - offset, self.MAX_BLOCK)
            nonce = struct.pack("Q", self.out_count).rjust(12, b"\x00")
            c = CC.new(key=self.out_key, nonce=nonce)
            c.update(struct.pack("H", length))
            enc, tag = c.encrypt_and_digest(bytearray(data[offset : offset + length]))
            result += struct.pack("H", length) + enc + tag
            offset += length
            self.out_count += 1
        self.socket.sendall(result)
        return total
