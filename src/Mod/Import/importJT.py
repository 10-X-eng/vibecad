# SPDX-License-Identifier: LGPL-2.1-or-later
"""File > Open / Import handler for Siemens JT (.jt).

Reads the published JT 8.x header and TOC, inflates zlib segment payloads,
and decodes Tri-Strip Set Shape LOD tessellation (Int32 CDP bitlength /
null codecs and quantized coordinates per ISO 14306 / JT 8.1). Huffman and
PMI are not required for the committed fixtures. If CAD Exchanger's
`ExchangerConv` is already on PATH it is tried first; it is not required.
"""

from __future__ import annotations

import builtins
import math
import os
import shutil
import struct
import subprocess
import tempfile
import zlib
from typing import List, Optional, Sequence, Tuple

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
_TRISTRIP_GUID_LE = bytes(
    [0xAB, 0x10, 0xDD, 0x10, 0xC8, 0x2A, 0xD1, 0x11, 0x9B, 0x6B, 0x00, 0x80, 0xC7, 0xBB, 0x59, 0x97]
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
            "The JT file may use an unsupported codec or contain no geometry."
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
    if _plausible(tris):
        return tris
    fallback = extract_float_triangles(data)
    if _plausible(fallback):
        return fallback
    return tris if tris else fallback


def write_jt_tessellation(path: str, triangles: List[Triangle]) -> None:
    """Write a little-endian JT 8.1 file whose LOD segment is a triangle soup."""
    payload = bytearray(_TRISTRIP_GUID)
    for a, b, c in triangles:
        for v in (a, b, c):
            payload.extend(struct.pack("<fff", v[0], v[1], v[2]))

    lsg_guid = bytes(16)
    header = bytearray()
    header.extend(b"Version 8.1 JT".ljust(80, b"\x00"))
    header.append(0)
    toc_offset = 80 + 1 + 4 + 4 + 16
    header.extend(struct.pack("<ii", 0, toc_offset))
    header.extend(lsg_guid)

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


def _plausible(tris: Sequence[Triangle]) -> bool:
    if len(tris) < 4:
        return False
    coords = [c for tri in tris for v in tri for c in v]
    if not all(math.isfinite(c) for c in coords):
        return False
    span = max(abs(c) for c in coords)
    if span <= 0 or span > 1.0e8:
        return False
    return True


class _Cursor:
    def __init__(self, data: bytes, pos: int = 0, le: bool = True):
        self.data = data
        self.pos = pos
        self.endian = "<" if le else ">"

    def remaining(self) -> int:
        return max(0, len(self.data) - self.pos)

    def u8(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def i16(self) -> int:
        v = struct.unpack_from(self.endian + "h", self.data, self.pos)[0]
        self.pos += 2
        return v

    def i32(self) -> int:
        v = struct.unpack_from(self.endian + "i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def u32(self) -> int:
        v = struct.unpack_from(self.endian + "I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def f32(self) -> float:
        v = struct.unpack_from(self.endian + "f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def bytes(self, n: int) -> bytes:
        v = self.data[self.pos : self.pos + n]
        self.pos += n
        return v


class _BitReader:
    """MSB-first bits from a little-endian U32 code-text array (JT 8.1)."""

    def __init__(self, words: Sequence[int]):
        self._words = list(words)
        self._i = 0
        self._buf = 0
        self._loaded = 0

    def _load(self) -> None:
        if self._i >= len(self._words):
            self._buf = 0
            return
        self._buf = self._words[self._i] & 0xFFFFFFFF
        self._i += 1
        self._loaded = 32

    def read_bit(self) -> int:
        if self._loaded == 0:
            self._load()
            self._loaded = 31
        else:
            self._loaded -= 1
        bit = (self._buf >> 31) & 1
        self._buf = (self._buf << 1) & 0xFFFFFFFF
        return bit

    def read_u32(self, nbits: int) -> int:
        if nbits <= 0:
            return 0
        acc = 0
        for _ in range(nbits):
            acc = (acc << 1) | self.read_bit()
        return acc

    def read_i32(self, nbits: int) -> int:
        if nbits <= 0:
            return 0
        raw = self.read_u32(nbits)
        sign_bit = 1 << (nbits - 1)
        if raw & sign_bit:
            raw -= 1 << nbits
        return raw


def _decode_bitlength_v8(words: Sequence[int], count: int) -> List[int]:
    br = _BitReader(words)
    field_width = 0
    out: List[int] = []
    step = 2
    for _ in range(count):
        if br.read_bit():
            first = br.read_bit()
            delta = step if first else -step
            field_width += delta
            while br.read_bit() == first:
                field_width += delta
            if field_width < 0:
                field_width = 0
            if field_width > 32:
                field_width = 32
        out.append(br.read_i32(field_width) if field_width else 0)
    return out


class _FileBits:
    """Continuous MSB-first bits from a raw byte stream (JT probability context)."""

    def __init__(self, cur: _Cursor):
        self.cur = cur
        self._byte = 0
        self._left = 0

    def read_bit(self) -> int:
        if self._left == 0:
            if self.cur.remaining() < 1:
                return 0
            self._byte = self.cur.u8()
            self._left = 8
        self._left -= 1
        return (self._byte >> self._left) & 1

    def read_u32(self, nbits: int) -> int:
        if nbits <= 0:
            return 0
        acc = 0
        for _ in range(nbits):
            acc = (acc << 1) | self.read_bit()
        return acc

    def read_i32(self, nbits: int) -> int:
        if nbits <= 0:
            return 0
        raw = self.read_u32(nbits)
        sign = 1 << (nbits - 1)
        if raw & sign:
            raw -= 1 << nbits
        return raw


def _read_prob_context_mk1(bits: _FileBits) -> List[dict]:
    entry_count = bits.read_u32(32)
    sym_bits = bits.read_u32(6)
    occ_bits = bits.read_u32(6)
    val_bits = bits.read_u32(6)
    nxt_bits = bits.read_u32(6)
    min_value = bits.read_i32(32)
    if entry_count > 100000:
        return []
    entries = []
    for _ in range(entry_count):
        symbol = bits.read_u32(sym_bits) - 2
        occ = bits.read_u32(occ_bits)
        value = bits.read_u32(val_bits) + min_value
        nxt = bits.read_u32(nxt_bits)
        entries.append({"symbol": symbol, "occ": occ, "value": value, "next": nxt})
    return entries


class _HuffNode:
    __slots__ = ("left", "right", "symbol", "value", "occ")

    def __init__(self, occ=0, symbol=0, value=0):
        self.left = None
        self.right = None
        self.symbol = symbol
        self.value = value
        self.occ = occ


def _build_huffman(entries: List[dict]) -> Optional[_HuffNode]:
    import heapq

    heap = []
    for i, e in enumerate(entries):
        node = _HuffNode(e["occ"], e["symbol"], e["value"])
        heapq.heappush(heap, (e["occ"], i, node))
    if not heap:
        return None
    n = len(heap)
    while len(heap) > 1:
        o1, _, a = heapq.heappop(heap)
        o2, _, b = heapq.heappop(heap)
        parent = _HuffNode(o1 + o2)
        parent.left = a
        parent.right = b
        heapq.heappush(heap, (parent.occ, n, parent))
        n += 1
    return heap[0][2]


def _decode_huffman(words: Sequence[int], count: int, entries: List[dict], oob: Sequence[int]) -> List[int]:
    root = _build_huffman(entries)
    if root is None:
        return []
    br = _BitReader(words)
    oob_i = 0
    out: List[int] = []
    for _ in range(count):
        node = root
        while node.left is not None or node.right is not None:
            node = node.left if br.read_bit() else node.right
            if node is None:
                return []
        if node.symbol != -2:
            out.append(node.value)
        else:
            if oob_i >= len(oob):
                return []
            out.append(oob[oob_i])
            oob_i += 1
    return out


def _load_cdp1(cur: _Cursor) -> List[int]:
    if cur.remaining() < 1:
        return []
    codec = cur.u8()
    if codec == 0:
        n = cur.i32()
        if n < 0 or n > 10_000_000 or cur.remaining() < n * 4:
            return []
        return [cur.i32() for _ in range(n)]
    if codec == 1:
        _code_bits = cur.i32()
        value_count = cur.i32()
        if value_count < 0 or value_count > 10_000_000:
            return []
        nwords = cur.i32()
        if nwords < 0 or nwords > 10_000_000 or cur.remaining() < nwords * 4:
            return []
        words = [cur.u32() for _ in range(nwords)]
        if not words and value_count:
            return []
        return _decode_bitlength_v8(words, value_count)
    if codec == 2:
        nctx = cur.u8()
        if nctx < 1 or nctx > 16:
            return []
        bits = _FileBits(cur)
        contexts = [_read_prob_context_mk1(bits)]
        for _ in range(1, nctx):
            contexts.append(_read_prob_context_mk1(bits))
        oob_count = cur.i32()
        oob: List[int] = []
        if oob_count > 0:
            oob = _load_cdp1(cur)
        code_bits = cur.i32()
        value_count = cur.i32()
        if nctx > 1:
            cur.i32()
        nwords = (code_bits + 31) // 32 if code_bits > 0 else 0
        # Some writers prefix an explicit word count.
        if cur.remaining() >= 4:
            maybe = struct.unpack_from(cur.endian + "i", cur.data, cur.pos)[0]
            if 0 < maybe <= nwords + 8 and maybe * 4 <= cur.remaining() - 4:
                nwords = cur.i32()
        if nwords < 0 or nwords * 4 > cur.remaining():
            return []
        words = [cur.u32() for _ in range(nwords)]
        return _decode_huffman(words, value_count, contexts[0], oob)
    return []


def _unpack_lag1(values: List[int]) -> List[int]:
    out = list(values)
    for i in range(4, len(out)):
        out[i] = out[i - 1] + out[i]
    return out


def _unpack_stride1(values: List[int]) -> List[int]:
    out = list(values)
    for i in range(4, len(out)):
        out[i] = (out[i - 1] + (out[i - 1] - out[i - 2])) + out[i]
    return out


def _unpack_strip_idx(values: List[int]) -> List[int]:
    out = list(values)
    for i in range(4, len(out)):
        d = out[i - 2] - out[i - 4]
        pred = out[i - 2] + (d if -8 < d < 8 else 2)
        out[i] = pred + out[i]
    return out


def _dequantize(codes: Sequence[int], vmin: float, vmax: float, bits: int) -> List[float]:
    if bits <= 0:
        return [
            struct.unpack("<f", struct.pack("<I", c & 0xFFFFFFFF))[0] for c in codes
        ]
    max_code = (1 << bits) if bits < 32 else 0xFFFFFFFF
    scale = (vmax - vmin) / float(max_code)
    return [vmin + (float(c & 0xFFFFFFFF) - 0.5) * scale for c in codes]


def _load_quantized_coords(cur: _Cursor) -> List[Tuple[float, float, float]]:
    q = []
    for _ in range(3):
        q.append((cur.f32(), cur.f32(), cur.u8()))
    count = cur.i32()
    if count <= 0 or count > 5_000_000:
        return []
    components = []
    for i in range(3):
        codes = _load_cdp1(cur)
        if len(codes) != count:
            return []
        codes = _unpack_lag1(codes)
        vmin, vmax, bits = q[i]
        components.append(_dequantize(codes, vmin, vmax, bits))
    return list(zip(components[0], components[1], components[2]))


def _strips_to_triangles(
    prim: Sequence[int], indices: Optional[Sequence[int]]
) -> List[Triangle]:
    if len(prim) < 2:
        return []
    tris: List[Triangle] = []
    # prim is start indices into the vertex stream / index list
    for p in range(len(prim) - 1):
        start, end = prim[p], prim[p + 1]
        if end - start < 3:
            continue
        off1, off2 = 1, 2
        origin = start
        while origin < end - 2:
            i0, i1, i2 = origin, origin + off1, origin + off2
            if indices is not None:
                if max(i0, i1, i2) >= len(indices):
                    break
                i0, i1, i2 = indices[i0], indices[i1], indices[i2]
            tris.append((i0, i1, i2))  # type: ignore[arg-type]
            off1, off2 = off2, off1
            origin += 1
    return tris  # type: ignore[return-value]


def _decode_tristrip_payload(blob: bytes) -> List[Triangle]:
    idx = blob.find(_TRISTRIP_GUID_LE)
    if idx < 0:
        idx = blob.find(_TRISTRIP_GUID)
        if idx < 0:
            soup = extract_float_triangles(blob)
            return soup if _plausible(soup) else []
        start = idx + 16
    else:
        start = idx + 16
    cur = _Cursor(blob, start)
    if cur.remaining() < 2:
        return []
    # Object base type may already have been consumed; try both layouts.
    saved = cur.pos
    for skip_base in (True, False):
        cur.pos = saved
        try:
            if skip_base:
                _base = cur.u8()
            if cur.remaining() >= 2:
                _ver = cur.i16()
            if cur.remaining() >= 4:
                cur.bytes(4)  # VertexBinding1
            if cur.remaining() >= 4:
                bits_per_vertex = cur.u8()
                cur.u8()
                cur.u8()
                cur.u8()
            else:
                continue
            if cur.remaining() >= 2:
                cur.i16()  # tri-strip version
            if cur.remaining() >= 2:
                cur.i16()  # compressed-rep version
            if cur.remaining() >= 3:
                _nb = cur.u8()
                _tb = cur.u8()
                _cb = cur.u8()
            if cur.remaining() >= 4:
                bits_per_vertex = cur.u8()
                cur.u8()
                cur.u8()
                cur.u8()
            prim = _unpack_stride1(_load_cdp1(cur))
            if not prim:
                continue
            verts: List[Tuple[float, float, float]] = []
            indices: Optional[List[int]] = None
            if bits_per_vertex == 0:
                unc = cur.i32()
                comp = cur.i32()
                raw = cur.bytes(comp if comp > 0 else max(0, unc))
                payload = zlib.decompress(raw) if comp > 0 else raw
                pc = _Cursor(payload)
                nvert = prim[-1] if prim else 0
                for _ in range(max(0, nvert)):
                    if _nb == 1 and pc.remaining() >= 12:
                        pc.f32()
                        pc.f32()
                        pc.f32()
                    if pc.remaining() < 12:
                        break
                    verts.append((pc.f32(), pc.f32(), pc.f32()))
                face_ids = _strips_to_triangles(prim, None)
                tris = []
                for a, b, c in face_ids:
                    if max(a, b, c) < len(verts):
                        tris.append((verts[a], verts[b], verts[c]))
                if _plausible(tris):
                    return tris
            else:
                verts = _load_quantized_coords(cur)
                if not verts:
                    continue
                raw_idx = _load_cdp1(cur)
                indices = _unpack_strip_idx(raw_idx) if raw_idx else None
                face_ids = _strips_to_triangles(prim, indices)
                tris = []
                for a, b, c in face_ids:
                    if isinstance(a, int) and max(a, b, c) < len(verts):
                        va, vb, vc = verts[a], verts[b], verts[c]
                        if va != vb and vb != vc and va != vc:
                            tris.append((va, vb, vc))
                if _plausible(tris):
                    return tris
        except Exception:
            continue
    soup = extract_float_triangles(blob[start:])
    return soup if _plausible(soup) else []


def _segment_body(segment: bytes) -> bytes:
    if len(segment) < 24:
        return segment
    body = segment[24:]
    if len(body) >= 8:
        flag = struct.unpack_from("<i", body, 0)[0]
        if flag == 2:
            for start in (8, 9):
                try:
                    return zlib.decompress(body[start:])
                except zlib.error:
                    continue
    inflated = _inflate(body)
    return inflated if inflated is not None else body


def _triangles_from_toc(data: bytes) -> List[Triangle]:
    version = data[:80].split(b"\x00", 1)[0].decode("ascii", "ignore")
    byte_order = data[80]
    endian = "<" if byte_order == 0 else ">"
    is_v10 = "10." in version
    pos = 81
    if is_v10:
        pos += 4
        toc_off = struct.unpack_from(endian + "q", data, pos)[0]
    else:
        _empty, toc_off = struct.unpack_from(endian + "ii", data, pos)
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
        need = guid_size + (8 + 8 + 4 if is_v10 else 4 + 4 + 4)
        if cursor + need > len(data):
            break
        if is_v10:
            off, length, _attribs = struct.unpack_from(endian + "qqI", data, cursor + guid_size)
        else:
            off, length, _attribs = struct.unpack_from(endian + "iiI", data, cursor + guid_size)
        cursor += need
        if off < 0 or length <= 0 or off >= len(data):
            continue
        segment = data[off : min(len(data), off + length)]
        body = _segment_body(segment)
        decoded = _decode_tristrip_payload(body)
        if not decoded:
            decoded = _decode_tristrip_payload(segment)
        if _plausible(decoded):
            tris.extend(decoded)
    return tris


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
