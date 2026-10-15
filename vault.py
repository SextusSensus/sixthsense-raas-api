"""Per-booking encrypted file storage with crypto-shred purge.

Each booking has its own AES-256 key, stored only wrapped by VAULT_MASTER_KEY. Files are written as
1 MiB AES-GCM chunks; the chunk index and a last-chunk flag are bound into the AAD, so reordering
or truncation fails to decrypt. Purge deletes the blobs and the wrapped key (the DB row is cleared
with PRAGMA secure_delete), which leaves nothing that can be decrypted.
"""
import os
import shutil
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CHUNK = 1 << 20


class TooLarge(Exception):
    pass


def new_key():
    return AESGCM.generate_key(256)


def wrap(master, key):
    nonce = os.urandom(12)
    return nonce + AESGCM(master).encrypt(nonce, key, b"raas-key")


def unwrap(master, blob):
    return AESGCM(master).decrypt(blob[:12], blob[12:], b"raas-key")


def blob_path(root, booking_id, file_id):
    return Path(root) / "vault" / booking_id / f"{file_id}.bin"


def _aad(file_id, i, last):
    return f"{file_id}:{i}:{int(last)}".encode()


def _emit(f, aes, file_id, i, data, last):
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, bytes(data), _aad(file_id, i, last))
    f.write(bytes([last]) + len(ct).to_bytes(4, "big") + nonce + ct)


async def write_stream(dst, key, file_id, chunks, limit):
    """Encrypt an async byte stream to `dst`. Plaintext never touches disk. Returns plaintext size."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part")
    aes, buf, i, size = AESGCM(key), bytearray(), 0, 0
    try:
        with open(tmp, "wb") as f:
            async for piece in chunks:
                size += len(piece)
                if size > limit:
                    raise TooLarge()
                buf += piece
                while len(buf) > CHUNK:  # keep >=1 byte back so the final chunk is known
                    _emit(f, aes, file_id, i, buf[:CHUNK], False)
                    del buf[:CHUNK]
                    i += 1
            _emit(f, aes, file_id, i, buf, True)
        os.replace(tmp, dst)
        return size
    finally:
        tmp.unlink(missing_ok=True)


def read_stream(src, key, file_id):
    aes, i = AESGCM(key), 0
    with open(src, "rb") as f:
        while True:
            hdr = f.read(5)
            if len(hdr) < 5:
                raise ValueError("vault blob truncated")
            last, n = hdr[0] == 1, int.from_bytes(hdr[1:], "big")
            nonce, ct = f.read(12), f.read(n)
            yield aes.decrypt(nonce, ct, _aad(file_id, i, last))
            if last:
                return
            i += 1


def delete_booking(root, booking_id):
    shutil.rmtree(Path(root) / "vault" / booking_id, ignore_errors=True)
