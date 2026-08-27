# SPDX-License-Identifier: LGPL-2.1-or-later
"""File > Open / Import handler for Autodesk Fusion archives (.f3d, .f3z).

Fusion native files are ZIP archives. Modern Fusion writes Zstandard
(method 93) members. Geometry is taken from the OGS display mesh
(`Fusion_mesh_*`) which every Fusion archive includes; parametric history
is not reconstructed.
"""

from __future__ import annotations

import io
import json
import os
from typing import List, Sequence

from cad_geometry import (
    Triangle,
    add_triangles_to_document,
    scale_triangles,
)
from cadzip import is_zip, open_zip
from fusion_ogs import OgsError, pack_ogs_cache, parse_ogs_cache

IMPORT_TYPE = "Autodesk Fusion Design (*.f3d *.F3D *.f3z *.F3Z)"
FUSION_LENGTH_TO_MM = 10.0


def open(filename):
    """Create a new document and load *filename* into it (File > Open)."""
    import FreeCAD

    name = os.path.splitext(os.path.basename(filename))[0] or "Fusion"
    doc = FreeCAD.newDocument(name)
    try:
        insert(filename, doc.Name)
    except Exception:
        FreeCAD.closeDocument(doc.Name)
        raise
    return doc


def insert(filename, docname):
    """Load *filename* into the named document (File > Import)."""
    import FreeCAD

    doc = _document(docname)
    label = os.path.splitext(os.path.basename(filename))[0] or "Fusion"
    groups = read_fusion_meshes(filename)
    if not groups:
        raise RuntimeError(
            f"No importable Fusion geometry in '{filename}'. "
            "The archive has no OGS display mesh."
        )
    last = None
    for i, triangles in enumerate(groups):
        part_label = label if len(groups) == 1 else f"{label}_{i + 1}"
        last = add_triangles_to_document(doc, triangles, part_label)
        _mark_flattened_fusion_import(last, filename, i + 1, len(groups))
    try:
        doc.recompute()
    except Exception:
        pass
    return last


def read_fusion_meshes(filename: str) -> List[List[Triangle]]:
    """Return one triangle list per OGS mesh found in a .f3d or .f3z file."""
    path = os.fspath(filename)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".f3z":
        return _read_f3z(path)
    if ext == ".f3d" or is_zip(path):
        return _read_f3d(path)
    raise RuntimeError(f"Not a Fusion archive: {filename}")


def _read_f3z(path: str) -> List[List[Triangle]]:
    groups: List[List[Triangle]] = []
    with open_zip(path) as zf:
        inner = [
            n
            for n in zf.namelist()
            if n.lower().endswith(".f3d") and not n.endswith("/")
        ]
        if not inner:
            # Some .f3z files are themselves a Fusion document zip.
            return _read_f3d(path)
        for name in _f3z_root_members(zf, inner):
            with open_zip_bytes(zf.read(name)) as inner_archive:
                groups.extend(_read_f3d_archive(inner_archive, f"{path}:{name}"))
    return groups


def _read_f3d(path: str) -> List[List[Triangle]]:
    if not is_zip(path):
        raise RuntimeError(f"Fusion file is not a ZIP archive: {path}")
    with open_zip(path) as zf:
        return _read_f3d_archive(zf, path)


def _read_f3d_archive(zf, source: str) -> List[List[Triangle]]:
    names = zf.namelist()
    worlds = sorted(
        name
        for name in names
        if os.path.basename(name) == "world"
        and "/ogs.blobfolder/ogs/defaultscene/" in name.lower().replace("\\", "/")
    )
    mesh_names = [name for name in names if _is_ogs_mesh_name(name)]
    if mesh_names and not worlds:
        raise RuntimeError(
            f"Fusion graphics cache in '{source}' has mesh data but no OGS world; "
            "refusing to guess its indexed geometry."
        )

    groups: List[List[Triangle]] = []
    for world_name in worlds:
        scene = os.path.dirname(world_name).replace("\\", "/") + "/"
        blobs = sorted(
            name for name in mesh_names if name.replace("\\", "/").startswith(scene)
        )
        if len(blobs) != 1:
            raise RuntimeError(
                f"Fusion OGS scene in '{source}' references {len(blobs)} mesh blobs; "
                "only one fully validated blob is currently supported."
            )
        try:
            scene_groups = parse_ogs_cache(
                zf.read(world_name),
                zf.read(blobs[0]),
                scale=FUSION_LENGTH_TO_MM,
            )
        except OgsError as exc:
            raise RuntimeError(
                f"Invalid Fusion OGS cache in '{source}': {exc}"
            ) from exc
        groups.extend(scene_groups)
    return _deduplicate_groups(groups)


def _is_ogs_mesh_name(name: str) -> bool:
    base = os.path.basename(name).lower()
    if "stream_mesh" in base:
        return False
    return base.startswith("fusion_mesh_") or base.startswith("mesh_")


def write_f3d(path: str, triangles: Sequence[Triangle]) -> None:
    """Write a STORE-compressed Fusion-layout archive with one OGS mesh."""
    import zipfile

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        _write_f3d_members(zf, triangles)


def write_f3z(path: str, parts: Sequence[Sequence[Triangle]]) -> None:
    """Write a .f3z ZIP containing one .f3d per part."""
    import io
    import zipfile

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        for i, tris in enumerate(parts):
            buf = io.BytesIO()
            write_f3d_fp(buf, tris)
            zf.writestr(f"part{i + 1}.f3d", buf.getvalue())


def write_f3d_fp(fp, triangles: Sequence[Triangle]) -> None:
    import zipfile

    with zipfile.ZipFile(fp, "w", compression=zipfile.ZIP_STORED) as zf:
        _write_f3d_members(zf, triangles)


def _write_f3d_members(zf, triangles: Sequence[Triangle]) -> None:
    stored = scale_triangles(triangles, 1.0 / FUSION_LENGTH_TO_MM)
    world, mesh = pack_ogs_cache(stored)
    scene = "FusionAssetName[Active]/OGS.BlobFolder/OGS/DefaultScene/"
    zf.writestr("Manifest.dat", b"FusionDocument")
    zf.writestr(scene + "world", world)
    zf.writestr(scene + "Fusion_mesh_000", mesh)


def _f3z_root_members(zf, inner: Sequence[str]) -> List[str]:
    manifests = {name.lower(): name for name in zf.namelist()}
    manifest_name = manifests.get("manifest.json")
    if manifest_name:
        try:
            root = json.loads(zf.read(manifest_name).decode("utf-8"))["root"]
        except (KeyError, TypeError, ValueError, UnicodeDecodeError):
            root = None
        if isinstance(root, str):
            normalized = root.replace("\\", "/").lower()
            matches = [
                name for name in inner if name.replace("\\", "/").lower() == normalized
            ]
            if len(matches) == 1:
                return matches
            raise RuntimeError(f"Fusion assembly root '{root}' is missing or ambiguous")
    return list(inner)


def open_zip_bytes(data: bytes):
    """Open an embedded .f3d after cadzip enabled method-93 support."""
    import zipfile

    return zipfile.ZipFile(io.BytesIO(data))


def _deduplicate_groups(groups: Sequence[Sequence[Triangle]]) -> List[List[Triangle]]:
    unique: List[List[Triangle]] = []
    seen = set()
    for group in groups:
        signature = tuple(
            sorted(
                tuple(
                    sorted(
                        tuple(round(value, 7) for value in vertex)
                        for vertex in triangle
                    )
                )
                for triangle in group
            )
        )
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(list(group))
    return unique


def _mark_flattened_fusion_import(obj, filename, index: int, count: int) -> None:
    try:
        obj.addProperty("App::PropertyString", "FusionImportMode", "Import")
        obj.FusionImportMode = (
            "Flattened current display snapshot (no editable Fusion history)"
        )
        obj.addProperty("App::PropertyString", "FusionSourceFile", "Import")
        obj.FusionSourceFile = os.path.abspath(os.fspath(filename))
        obj.addProperty("App::PropertyString", "FusionBody", "Import")
        obj.FusionBody = f"{index} of {count}"
        obj.setEditorMode("FusionImportMode", 1)
        obj.setEditorMode("FusionSourceFile", 1)
        obj.setEditorMode("FusionBody", 1)
    except Exception:
        pass


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
