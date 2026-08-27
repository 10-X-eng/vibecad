# SPDX-License-Identifier: LGPL-2.1-or-later
"""Read ZIP members including Fusion's Zstandard (method 93) compression."""

from __future__ import annotations

import zipfile

ZIP_ZSTANDARD = 93
_PATCHED = False


def _zstd_decompressor_factory():
    try:
        from backports.zstd import ZstdDecompressor
    except ImportError:
        try:
            from compression.zstd import ZstdDecompressor  # type: ignore[attr-defined]
        except ImportError as exc:
            raise RuntimeError(
                "This archive uses Zstandard ZIP compression (method 93), "
                "but neither backports.zstd nor compression.zstd is available."
            ) from exc

    class _ZipZstdDecompressor:
        def __init__(self):
            self._d = ZstdDecompressor()
            self.eof = False
            self.unused_data = b""
            self.unconsumed_tail = b""

        def decompress(self, data, max_length=0):
            if max_length:
                try:
                    return self._d.decompress(data, max_length)
                except TypeError:
                    return self._d.decompress(data)
            return self._d.decompress(data)

    return _ZipZstdDecompressor


def enable_zstd_zip() -> None:
    """Teach stdlib zipfile how to inflate method-93 (Zstandard) members."""
    global _PATCHED
    if _PATCHED:
        return
    orig_check = zipfile._check_compression
    orig_get = zipfile._get_decompressor

    def _check_compression(compress_type):
        if compress_type == ZIP_ZSTANDARD:
            return
        return orig_check(compress_type)

    def _get_decompressor(compress_type):
        if compress_type == ZIP_ZSTANDARD:
            return _zstd_decompressor_factory()()
        return orig_get(compress_type)

    zipfile._check_compression = _check_compression
    zipfile._get_decompressor = _get_decompressor
    zipfile.ZIP_ZSTANDARD = ZIP_ZSTANDARD  # type: ignore[attr-defined]
    _PATCHED = True


def open_zip(path: str) -> zipfile.ZipFile:
    enable_zstd_zip()
    return zipfile.ZipFile(path)


def is_zip(path: str) -> bool:
    return zipfile.is_zipfile(path)
