# SPDX-License-Identifier: LGPL-2.1-or-later
"""File > Open / Import handler for Siemens JT (.jt).

Parses the published JT 8.1 / 9.5 file header and TOC, inflates zlib/lzma
segment payloads, and extracts tessellated triangles. Quantized / Huffman
JT codecs and PMI are out of scope. If CAD Exchanger's `ExchangerConv` is
on PATH it is tried first, then the bundled reader always runs as the
required no-extra-install path.
"""

from __future__ import annotations

import builtins
import os
import shutil
import struct
import subprocess
import tempfile
import zlib
from typing import List, Optional

from cad_geometry import Triangle, add_triangles_to_document, extract_float_triangles

IMPORT_TYPE = "Siemens JT (*.jt *.JT)"

# Tri-Strip Set Shape LOD Element (JT 8.1 / 9.5 spec)
_TRISTRIP_GUID = bytes(
    [
        0x10,
        0xDD,
        0x10,
        0xAB,
        0x2A,
        0xC8,
        0x11,
        0xD1,
        0x9B,
        0x6B,
        0x00,
        0x80,
        0xC7,
        0xBB,
        0x59,
        0x97,
    ]
)


def open(filename):
    """Create a new document and load *filename* into it (File > Open)."""
    import FreeCAD

    name = os.path.splitext(os.path.basename(filename))[0] or "JT"
    doc = FreeCAD.newDocument(name)
    insert(filename, doc.Name)
    return doc


def insert(filename, docname):
    """Load *filename* into the named document (File > Import)."""
    import FreeCAD

    doc = _document(docname)
    label = os.path.splitext(os.path.basename(filename))[0] or "JT"
    if _try_cad_exchanger(filename, doc, label):
        try:
            doc.recompute()
        except Exception:
            pass
        return doc

    triangles = read_jt_triangles(filename)
    if not triangles:
        raise RuntimeError(
            f"No importable tessellation in '{filename}'. "
            "The JT file may be quantized-only, empty, or an unsupported version."
        )
    obj = add_triangles_to_document(doc, triangles, label)
    try:
        doc.recompute()
    except Exception:
        pass
    return obj


def read_jt_triangles(filename: str) -> List[Triangle]:
    """Return triangles from a JT file using the bundled reader."""
    with builtins.open(filename, "rb") as fh:
        data = fh.read()
    if len(data) < 80 or not data[:80].lstrip().startswith(b"Version"):
        raise RuntimeError(f"Not a JT file: {filename}")
    tris = _triangles_from_toc(data)
    if tris:
        return tris
    return extract_float_triangles(data)


def write_jt_tessellation(path: str, triangles: List[Triangle]) -> None:
    """Write a little-endian JT 8.1 file whose LOD segment is a triangle soup.

    The layout matches the public JT 8.1 header + TOC + type-7 segment so the
    bundled reader (and the tests) exercise the same TOC path used on real
    files. Vertex data is uncompressed IEEE-754 floats.
    """
    payload = bytearray(_TRISTRIP_GUID)
    for a, b, c in triangles:
        for v in (a, b, c):
            payload.extend(struct.pack("<fff", v[0], v[1], v[2]))

    lsg_guid = bytes(16)
    header = bytearray()
    header.extend(b"Version 8.1 JT".ljust(80, b"\x00"))
    header.append(0)  # little endian
    toc_offset = 80 + 1 + 4 + 4 + 16
    header.extend(struct.pack("<ii", 0, toc_offset))
    header.extend(lsg_guid)
    assert len(header) == toc_offset

    toc_size = 4 + (16 + 4 + 4 + 4)
    seg_offset = toc_offset + toc_size
    seg_type = 7
    seg_length = 16 + 4 + 4 + len(payload)
    attributes = (seg_type << 24) & 0xFFFFFFFF

    buf = bytearray(header)
    buf.extend(struct.pack("<i", 1))
    buf.extend(_TRISTRIP_GUID)
    buf.extend(struct.pack("<iiI", seg_offset, seg_length, attributes))
    buf.extend(_TRISTRIP_GUID)
    buf.extend(struct.pack("<ii", seg_type, seg_length))
    buf.extend(payload)
    with builtins.open(path, "wb") as fh:
        fh.write(buf)


def _triangles_from_toc(data: bytes) -> List[Triangle]:
    version = data[:80].split(b"\x00", 1)[0].decode("ascii", "ignore")
    byte_order = data[80]
    endian = "<" if byte_order == 0 else ">"
    is_v10 = "10." in version
    pos = 81
    if is_v10:
        # JT 10: reserved I32, TOC offset I64
        _reserved = struct.unpack_from(endian + "i", data, pos)[0]
        pos += 4
        toc_off = struct.unpack_from(endian + "q", data, pos)[0]
        pos += 8
    else:
        _empty, toc_off = struct.unpack_from(endian + "ii", data, pos)
        pos += 8
    if toc_off <= 0 or toc_off >= len(data) - 4:
        return []
    try:
        entry_count = struct.unpack_from(endian + "i", data, toc_off)[0]
    except struct.error:
        return []
    if entry_count < 0 or entry_count > 100000:
        return []
    cursor = toc_off + 4
    tris: List[Triangle] = []
    guid_size = 16
    for _ in range(entry_count):
        need = guid_size + 4 + 4 + 4
        if is_v10:
            need = guid_size + 8 + 8 + 4  # I64 offset/length
        if cursor + need > len(data):
            break
        if is_v10:
            off, length, attribs = struct.unpack_from(endian + "qqI", data, cursor + guid_size)
            cursor += need
        else:
            off, length, attribs = struct.unpack_from(endian + "iiI", data, cursor + guid_size)
            cursor += need
        seg_type = (attribs >> 24) & 0xFF
        if off < 0 or length < 0 or off >= len(data):
            continue
        end = min(len(data), off + max(length, 0))
        segment = data[off:end] if length > 0 else data[off:]
        # Type 7 is Shape LOD. Scan every segment so uncompressed
        # visualization meshes in other slots still import.
        if length == 0:
            continue
        tris.extend(_triangles_from_segment(segment))
    return tris


def _triangles_from_segment(segment: bytes) -> List[Triangle]:
    if len(segment) < 24:
        return extract_float_triangles(segment)
    # Skip segment header (GUID + type I32 + length I32)
    body = segment[24:] if len(segment) > 24 else segment
    inflated = _inflate(body)
    blob = inflated if inflated is not None else body
    idx = blob.find(_TRISTRIP_GUID)
    target = blob[idx + 16 :] if idx >= 0 else blob
    tris = extract_float_triangles(target)
    if tris:
        return tris
    return extract_float_triangles(blob)


def _inflate(body: bytes) -> Optional[bytes]:
    if not body:
        return None
    try:
        return zlib.decompress(body)
    except zlib.error:
        pass
    try:
        return zlib.decompress(body, -zlib.MAX_WBITS)
    except zlib.error:
        pass
    try:
        import lzma

        return lzma.decompress(body)
    except Exception:
        return None


def _try_cad_exchanger(filename, doc, label) -> bool:
    """Optional: convert via ExchangerConv if the user already installed it."""
    exe = os.environ.get("VIBECAD_EXCHANGERCONV") or shutil.which("ExchangerConv")
    if not exe:
        return False
    with tempfile.TemporaryDirectory(prefix="vibecad-jt-") as tmp:
        out = os.path.join(tmp, "converted.step")
        commands = [
            [exe, "-i", filename, "-e", out],
            [exe, "-i", filename, "-o", out],
            [exe, filename, out],
        ]
        for cmd in commands:
            try:
                proc = subprocess.run(
                    cmd,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=120,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if proc.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0:
                return _insert_step(out, doc, label)
    return False


def _insert_step(path, doc, label) -> bool:
    try:
        import Import as ImportMod

        ImportMod.insert(path, doc.Name)
        return True
    except Exception:
        try:
            import Part

            shape = Part.Shape()
            shape.read(path)
            if shape.isNull():
                return False
            obj = doc.addObject("Part::Feature", label)
            obj.Shape = shape
            return True
        except Exception:
            return False


def _document(docname):
    import FreeCAD

    if docname:
        try:
            return FreeCAD.getDocument(docname)
        except Exception:
            return FreeCAD.newDocument(docname)
    doc = FreeCAD.ActiveDocument
    if doc is None:
        doc = FreeCAD.newDocument()
    return doc
