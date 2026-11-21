"""Encodeur PNG from scratch (zlib + struct seulement).

Sert au rendu CPU de validation : rasteriser la mesh sans dependre de PIL.
Ecrit un PNG 8 bits RGB (filtre 0 sur chaque ligne, compression zlib par defaut).
"""
import struct
import zlib


def write_png(path, img):
    """img : np.ndarray (H, W, 3) uint8 -> fichier PNG RGB 8 bits."""
    import numpy as np

    img = np.ascontiguousarray(img.astype(np.uint8))
    h, w = img.shape[0], img.shape[1]
    # chaque ligne precedee du filtre 0
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))
    idat = zlib.compress(raw, 6)

    def chunk(ctype, data):
        c = ctype + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8 bits, RGB
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", idat))
        f.write(chunk(b"IEND", b""))
    return path
