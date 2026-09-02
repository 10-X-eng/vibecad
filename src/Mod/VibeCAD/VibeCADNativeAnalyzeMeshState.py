# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded state for FEM mesh definitions and generated meshes."""

from __future__ import annotations

from io import BytesIO
import hashlib
import json
import math
from typing import Any, Mapping
import zipfile

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeSnapshot import concise_object


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise NativeAnalyzeError("A FEM mesh definition contains a non-finite value.")
    return float(format(number, ".15g"))


def fem_mesher_kind(obj: Any) -> str:
    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    if proxy_type == "Fem::FemMeshGmsh":
        return "gmsh"
    if proxy_type == "Fem::FemMeshNetgen":
        return "netgen"
    try:
        if obj.isDerivedFrom("Fem::FemMeshShapeNetgenObject"):
            return "netgen_legacy"
    except Exception:
        pass
    raise NativeAnalyzeError(
        "The exact target is not a supported FEM mesh definition.",
        error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
    )


def _source(obj: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    source = getattr(obj, "Shape", None)
    if source is None:
        raise NativeAnalyzeError("The FEM mesh definition has no geometry source.")
    try:
        source_state = mesh_object_state(source)
    except Exception as exc:
        raise NativeAnalyzeError("The FEM mesh geometry source is not usable.") from exc
    visible = {
        "object_name": str(source.Name),
        "state_sha256": source_state["state_sha256"],
        "shape_type": str(source.Shape.ShapeType),
        "topology": dict(source_state.get("topology") or {}),
    }
    return visible, {
        **visible,
        "object_id": int(source.ID),
    }


def _stable_property(value: Any) -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if isinstance(value, float):
        return _finite(value)
    if hasattr(value, "getValueAs"):
        try:
            return _finite(value.Value)
        except Exception:
            return str(value)
    if all(hasattr(value, axis) for axis in ("x", "y", "z")):
        return [_finite(getattr(value, axis)) for axis in ("x", "y", "z")]
    if hasattr(value, "Name") and hasattr(value, "ID"):
        return [str(value.Name), int(value.ID)]
    if isinstance(value, (list, tuple)):
        return [_stable_property(item) for item in value]
    try:
        return _finite(value)
    except Exception:
        return str(value)


def _native_parameters(obj: Any) -> dict[str, Any]:
    result = {}
    for name in tuple(getattr(obj, "PropertiesList", ()) or ()):
        try:
            if str(obj.getGroupOfProperty(name)) != "Mesh Parameters":
                continue
            result[str(name)] = _stable_property(obj.getPropertyByName(name))
        except Exception:
            continue
    return result


def _settings(obj: Any, kind: str) -> dict[str, Any]:
    if kind == "gmsh":
        return {
            "maximum_size_mm": _finite(obj.CharacteristicLengthMax.getValueAs("mm").Value),
            "minimum_size_mm": _finite(obj.CharacteristicLengthMin.getValueAs("mm").Value),
            "element_dimension": {
                "From Shape": "from_shape",
                "1D": "1d",
                "2D": "2d",
                "3D": "3d",
            }[str(obj.ElementDimension)],
            "element_order": {"1st": "first", "2nd": "second"}[
                str(obj.ElementOrder)
            ],
        }
    if kind == "netgen":
        fineness = {
            "VeryCoarse": "very_coarse",
            "Coarse": "coarse",
            "Moderate": "moderate",
            "Fine": "fine",
            "VeryFine": "very_fine",
            "UserDefined": "user_defined",
        }.get(str(obj.Fineness))
        if fineness is None:
            raise NativeAnalyzeError(
                "The Netgen mesh definition contains an unsupported fineness preset."
            )
        result = {
            "maximum_size_mm": _finite(obj.MaxSize.getValueAs("mm").Value),
            "minimum_size_mm": _finite(obj.MinSize.getValueAs("mm").Value),
            "fineness": fineness,
            "second_order": bool(obj.SecondOrder),
        }
        if str(obj.Fineness) == "UserDefined":
            result["user_fineness"] = {
                "growth_rate": _finite(obj.GrowthRate),
                "curvature_safety": _finite(obj.CurvatureSafety),
                "segments_per_edge": _finite(obj.SegmentsPerEdge),
            }
        return result
    return {"legacy_backend": True}


def _mesh_content_sha256(fem_mesh: Any) -> str | None:
    if int(fem_mesh.NodeCount) == 0:
        return None
    try:
        payload = bytes(fem_mesh.dumpContent())
        digest = hashlib.sha256()
        canonical_groups = []
        with zipfile.ZipFile(BytesIO(payload), "r") as archive:
            for name in sorted(archive.namelist()):
                content = archive.read(name)
                if name.lower().endswith(".unv"):
                    canonical_groups.extend(_canonical_unv_groups(content))
                    content = _unv_without_group_datasets(content)
                digest.update(name.encode("utf-8"))
                digest.update(b"\0")
                digest.update(content)
        digest.update(b"\0canonical-groups\0")
        digest.update(
            json.dumps(
                _sorted_group_records(canonical_groups),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        return digest.hexdigest()
    except Exception as exc:
        raise NativeAnalyzeError("The generated FEM mesh could not be hashed exactly.") from exc


def _unv_without_group_datasets(payload: bytes) -> bytes:
    """Remove volatile UNV group records while retaining exact mesh records.

    FreeCAD may renumber and reorder UNV 2467/2477 group records after a file
    round trip. Group semantics are hashed separately as canonical UNV records.
    """

    lines = payload.splitlines(keepends=True)
    stable = []
    index = 0
    while index < len(lines):
        if (
            lines[index].strip() == b"-1"
            and index + 1 < len(lines)
            and lines[index + 1].strip() in {b"2467", b"2477"}
        ):
            index += 2
            while index < len(lines) and lines[index].strip() != b"-1":
                index += 1
            if index < len(lines):
                index += 1
            continue
        stable.append(lines[index])
        index += 1
    return b"".join(stable)


def _canonical_unv_groups(payload: bytes) -> list[dict[str, Any]]:
    lines = payload.splitlines()
    groups = []
    index = 0
    while index + 1 < len(lines):
        if (
            lines[index].strip() != b"-1"
            or lines[index + 1].strip() not in {b"2467", b"2477"}
        ):
            index += 1
            continue
        dataset = lines[index + 1].strip().decode("ascii")
        index += 2
        while index < len(lines) and lines[index].strip() != b"-1":
            header = [int(value) for value in lines[index].split()]
            if len(header) != 8 or header[-1] < 0 or index + 1 >= len(lines):
                raise ValueError("The UNV mesh contains an invalid group header.")
            count = header[-1]
            name = lines[index + 1].decode("utf-8", errors="replace")
            index += 2
            members = []
            while len(members) < count:
                if index >= len(lines) or lines[index].strip() == b"-1":
                    raise ValueError("The UNV mesh group has incomplete membership.")
                values = [int(value) for value in lines[index].split()]
                if len(values) % 4 != 0:
                    raise ValueError("The UNV mesh group membership is malformed.")
                members.extend(
                    tuple(values[offset : offset + 4])
                    for offset in range(0, len(values), 4)
                )
                index += 1
            if len(members) != count:
                raise ValueError("The UNV mesh group contains excess membership.")
            groups.append(
                {
                    "dataset": dataset,
                    "name": name,
                    "attributes": header[1:7],
                    "members": sorted(members),
                }
            )
        index += 1
    return groups


def _sorted_group_records(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        groups,
        key=lambda value: json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _topology(fem_mesh: Any) -> dict[str, int]:
    return {
        "nodes": int(fem_mesh.NodeCount),
        "edges": int(fem_mesh.EdgeCount),
        "faces": int(fem_mesh.FaceCount),
        "volumes": int(fem_mesh.VolumeCount),
    }


def _fem_mesh_definition_state(
    obj: Any,
    *,
    include_content_hash: bool,
) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError("The FEM mesh definition is no longer live.")
    kind = fem_mesher_kind(obj)
    source, exact_source = _source(obj)
    fem_mesh = obj.FemMesh
    topology = _topology(fem_mesh)
    content_sha = _mesh_content_sha256(fem_mesh) if include_content_hash else None
    settings = _settings(obj, kind)
    result = {
        **concise_object(obj),
        "mesher": "netgen" if kind == "netgen_legacy" else kind,
        "backend": kind,
        "source": source,
        "settings": settings,
        "generated": topology["nodes"] > 0,
        "topology": topology,
    }
    if content_sha is not None:
        result["mesh_content_sha256"] = content_sha
    result["state_sha256"] = _digest(
        {
            "object_name": str(obj.Name),
            "object_id": int(obj.ID),
            "label": str(obj.Label),
            "backend": kind,
            "source": exact_source,
            "native_parameters": _native_parameters(obj),
            "topology": topology,
            "mesh_content_sha256": content_sha,
        }
    )
    return result


def fem_mesh_definition_state(obj: Any) -> dict[str, Any]:
    """Return the legacy exact FEM mesh-definition state.

    This deliberately retains full serialized mesh hashing for callers that
    explicitly require byte-exact generated mesh identity.
    """

    return _fem_mesh_definition_state(obj, include_content_hash=True)


def fem_mesh_definition_context_state(obj: Any) -> dict[str, Any]:
    """Return bounded provider context without serializing generated mesh data."""

    return _fem_mesh_definition_state(obj, include_content_hash=False)


def is_fem_mesh_definition(obj: Any) -> bool:
    """Return whether *obj* is a supported FEM mesher definition."""

    try:
        fem_mesher_kind(obj)
        return True
    except NativeAnalyzeError:
        return False


def fem_mesh_definition_still_exact(obj: Any, expected_sha256: str) -> bool:
    """Accept current context fingerprints and legacy exact fingerprints."""

    try:
        if (
            fem_mesh_definition_context_state(obj)["state_sha256"]
            == expected_sha256
        ):
            return True
        return fem_mesh_definition_state(obj)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False


def _fem_mesh_object_state(
    obj: Any,
    *,
    include_content_hash: bool,
) -> dict[str, Any]:
    """Return bounded or exact state for any supported FEM mesh object.

    Mesher definitions retain their richer existing state shape.  Baked and
    filtered ``Fem::FemMeshObject`` instances use a compact mesh-only shape so
    they can be inspected, filtered again, or converted on a later turn.
    """

    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError("The FEM mesh is no longer live.")
    try:
        derived = bool(obj.isDerivedFrom("Fem::FemMeshObject"))
    except Exception:
        derived = False
    if not derived:
        raise NativeAnalyzeError(
            "The exact target is not a FEM mesh object.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )

    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    if proxy_type in {"Fem::FemMeshGmsh", "Fem::FemMeshNetgen"}:
        return _fem_mesh_definition_state(
            obj,
            include_content_hash=include_content_hash,
        )
    try:
        if obj.isDerivedFrom("Fem::FemMeshShapeNetgenObject"):
            return _fem_mesh_definition_state(
                obj,
                include_content_hash=include_content_hash,
            )
    except Exception:
        pass

    fem_mesh = obj.FemMesh
    topology = _topology(fem_mesh)
    content_sha = _mesh_content_sha256(fem_mesh) if include_content_hash else None
    role = str(getattr(obj, "VibeCADTimelineRole", "") or "")
    owner = getattr(obj, "VibeCADTimelineOwner", None)
    replaced = tuple(getattr(obj, "VibeCADTimelineReplacedInputs", ()) or ())
    exact_history = {
        "role": role,
        "owner": (
            [str(owner.Name), int(owner.ID)]
            if owner is not None and is_live(document, owner)
            else None
        ),
        "replaced_inputs": [
            [str(value.Name), int(value.ID)]
            for value in replaced
            if is_live(document, value)
        ],
    }
    result = {
        **concise_object(obj),
        "backend": "baked",
        "generated": topology["nodes"] > 0,
        "topology": topology,
    }
    if content_sha is not None:
        result["mesh_content_sha256"] = content_sha
    if role:
        result["timeline_role"] = role
    if exact_history["owner"] is not None:
        result["timeline_owner"] = exact_history["owner"][0]
    if exact_history["replaced_inputs"]:
        result["replaced_inputs"] = [
            value[0] for value in exact_history["replaced_inputs"]
        ]
    result["state_sha256"] = _digest(
        {
            "object_name": str(obj.Name),
            "object_id": int(obj.ID),
            "label": str(obj.Label),
            "topology": topology,
            "mesh_content_sha256": content_sha,
            "history": exact_history,
        }
    )
    return result


def fem_mesh_object_state(obj: Any) -> dict[str, Any]:
    """Return legacy exact state for any FEM mesh object accepted by the ribbon."""

    return _fem_mesh_object_state(obj, include_content_hash=True)


def fem_mesh_object_context_state(obj: Any) -> dict[str, Any]:
    """Return bounded provider context without serializing FEM mesh content."""

    return _fem_mesh_object_state(obj, include_content_hash=False)


def fem_mesh_object_still_exact(obj: Any, expected_sha256: str) -> bool:
    try:
        if fem_mesh_object_context_state(obj)["state_sha256"] == expected_sha256:
            return True
        return fem_mesh_object_state(obj)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False
