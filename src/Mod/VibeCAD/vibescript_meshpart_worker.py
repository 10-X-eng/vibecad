# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native converter for production MeshPart VibeScript programs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any

from vibescript_domain_api import DomainValue
from vibescript_meshpart_api import MeshPartDomainAPI


VALIDATION_SCHEMA = "vibecad-vibescript-meshpart-validation-v1"
_EXPORTS = ("mesh_from_shape", "shape_from_mesh")
_OUTPUT_TYPES = ("mesh", "solid", "shell", "face", "wire", "compound")
_BREP_TYPES = frozenset(_OUTPUT_TYPES[1:])
_MAX_DEFINITION_BYTES = 1_000_000
_MAX_REFERENCE_COUNT = 128
_MAX_REFERENCE_SEGMENTS = 4096
_MAX_FACETS = 5_000_000
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_MAX_SUBELEMENT_FACTS = 256
_REFERENCE_SHAPES: Mapping[tuple[str, str], Any] = MappingProxyType({})
_REFERENCE_MESHES: Mapping[tuple[str, str], Any] = MappingProxyType({})
_REFERENCE_METADATA: Mapping[tuple[str, str], Mapping[str, Any]] = MappingProxyType({})
_NETGEN_AVAILABLE: bool | None = None


def _default_correction(details: Mapping[str, Any]) -> str:
    """Return one bounded model repair for every MeshPart failure stage."""

    stage = str(details.get("stage") or "")
    operation = str(details.get("operation") or "")
    path = str(details.get("path") or "")
    output_name = str(details.get("output_name") or "")
    location = f" at {path}" if path else (f" {output_name!r}" if output_name else "")
    if stage == "result_contract":
        return (
            "Return exactly the declared expected_outputs names, types, and order. Replace "
            "only the mismatched result value and keep the declarations unchanged."
        )
    if stage == "definition_contract":
        api_name = f"api.{operation}" if operation else "the active MeshPart api"
        return (
            f"Rebuild only the malformed value{location} with {api_name}; never construct, "
            "copy, or mutate a serialized conversion dictionary."
        )
    if stage == "reference_selection":
        return (
            "Copy one exact eligible reference from document_shape_sources for "
            "api.mesh_from_shape or document_mesh_sources for api.shape_from_mesh into an "
            "x-vibecad-reference input; keep document_uid/object_name unchanged."
        )
    if stage == "shape_selection":
        return (
            "Use the current source facts to choose one live FaceN, ShellN, or SolidN class, "
            "or omit subelements to mesh the whole BREP; never guess a stale topology index."
        )
    if stage == "native_mesher_capability":
        return (
            "Keep the BREP reference and output unchanged, then choose method='standard' or "
            "one reported available Mefisto method; do not retry an unavailable Netgen method."
        )
    if stage == "native_mesher":
        return (
            "Keep one mesher method, inspect the source scale/topology, and change only that "
            "method's relevant positive size or deflection parameter; do not hedge by adding "
            "multiple conversion outputs."
        )
    if stage == "mesh_selection":
        return (
            "Choose one reported 1-based segment, use only human-supplied live facet indices, "
            "or omit both selectors for the full mesh; never infer indices from a visual label."
        )
    if stage == "normal_harmonization":
        return (
            "Repair the source orientation in the Mesh workbench when required, or set "
            "harmonize_normals=False only when orientation is irrelevant to the requested "
            "non-solid result."
        )
    if stage in {"mesh_to_faces", "shape_sewing", "shape_normalization"}:
        return (
            "Repair the referenced mesh first, then change only surface tolerance or selection "
            "to produce valid connected faces; preserve the requested output class."
        )
    if stage == "solid_construction":
        return (
            "Use a single connected, closed, consistently oriented mesh and output_type='solid'; "
            "repair the source in Mesh before retrying rather than weakening solid semantics."
        )
    if stage == "shape_refinement":
        return (
            "Set refine=False if refinement is not required, otherwise repair the source and "
            "keep only the reported surface conversion settings."
        )
    if stage == "boundary_extraction":
        return (
            "Select a region with the intended boundary count; request output_type='wire' for "
            "exactly one loop or output_type='compound' with representation='boundary' to keep "
            "multiple loops."
        )
    if stage == "shape_typing":
        return (
            "Change only output_type/representation or the selected region so the native OCC "
            "result is exactly one declared face, shell, solid, wire, or compound."
        )
    if stage == "shape_validation":
        return (
            "Repair only the referenced mesh or its selection until the converted OCC shape is "
            "non-null and valid; retain the declared output type."
        )
    if stage == "artifact_export":
        return (
            "Keep the validated conversion source unchanged and retry only after the isolated "
            "worker can write and authenticate its bounded BMS/BREP staging artifact."
        )
    return (
        "Correct only the reported MeshPart source, method, selection, or output type and retry "
        "the failed working revision; do not recreate the program."
    )


class MeshPartCandidateError(RuntimeError):
    """A model-correctable MeshPart failure with structured diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.details = dict(details or {})
        if not str(self.details.get("correction") or "").strip():
            changes = self.details.get("required_changes")
            correction = (
                next(
                    (str(item).strip() for item in changes if str(item).strip()),
                    "",
                )
                if isinstance(changes, list)
                else ""
            )
            self.details["correction"] = correction or _default_correction(self.details)
        super().__init__(message)


def _fail(
    message: str,
    *,
    stage: str,
    **details: Any,
) -> MeshPartCandidateError:
    return MeshPartCandidateError(message, details={"stage": stage, **details})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reference_key(value: Any, *, context: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "document_uid",
        "object_name",
    }:
        raise _fail(
            f"{context} must contain exactly document_uid and object_name.",
            stage="reference_selection",
            path=context,
        )
    clean: list[str] = []
    for name in ("document_uid", "object_name"):
        raw = value.get(name)
        if (
            not isinstance(raw, str)
            or not raw
            or raw != raw.strip()
            or len(raw) > 256
            or "\0" in raw
        ):
            raise _fail(
                f"{context}.{name} must be a non-empty string of at most 256 "
                "characters without surrounding whitespace or nulls.",
                stage="reference_selection",
                path=f"{context}.{name}",
            )
        clean.append(raw)
    key = (clean[0], clean[1])
    return key


def _bounded_segments(
    value: Any,
    *,
    facet_count: int,
    context: str,
    stage: str = "reference_selection",
) -> list[list[int]]:
    if not isinstance(value, list) or len(value) > _MAX_REFERENCE_SEGMENTS:
        raise _fail(
            f"{context} must be an array with at most {_MAX_REFERENCE_SEGMENTS} segments.",
            stage=stage,
            path=context,
        )
    result: list[list[int]] = []
    total = 0
    for segment_index, raw_segment in enumerate(value):
        if not isinstance(raw_segment, list) or not raw_segment:
            raise _fail(
                f"{context}[{segment_index}] must be a non-empty array.",
                stage=stage,
                path=f"{context}[{segment_index}]",
            )
        segment = []
        seen = set()
        for item_index, raw_index in enumerate(raw_segment):
            if (
                isinstance(raw_index, bool)
                or type(raw_index) is not int
                or not 0 <= raw_index < facet_count
            ):
                raise _fail(
                    f"{context}[{segment_index}][{item_index}] is outside the native "
                    f"facet range 0-{max(0, facet_count - 1)}.",
                    stage=stage,
                    path=f"{context}[{segment_index}][{item_index}]",
                )
            if raw_index in seen:
                raise _fail(
                    f"{context}[{segment_index}] contains duplicate facet {raw_index}.",
                    stage=stage,
                    path=f"{context}[{segment_index}]",
                )
            seen.add(raw_index)
            segment.append(raw_index)
        total += len(segment)
        if total > max(facet_count * 4, facet_count + 1024):
            raise _fail(
                f"{context} contains an excessive number of memberships.",
                stage=stage,
                path=context,
            )
        result.append(segment)
    return result


def configure_meshpart_references(
    root: Path,
    entries: list[dict[str, Any]],
) -> None:
    """Load and authenticate host-staged BREP and BMS snapshots."""

    import Mesh
    import Part

    if len(entries) > _MAX_REFERENCE_COUNT:
        raise _fail(
            f"MeshPart accepts at most {_MAX_REFERENCE_COUNT} document references.",
            stage="reference_selection",
        )
    resolved_root = Path(root).resolve()
    shapes: dict[tuple[str, str], Any] = {}
    meshes: dict[tuple[str, str], Any] = {}
    metadata: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise _fail(
                f"document_references[{index}] must be an object.",
                stage="reference_selection",
                path=f"document_references[{index}]",
            )
        identity = {
            "document_uid": entry.get("document_uid"),
            "object_name": entry.get("object_name"),
        }
        key = _reference_key(
            identity,
            context=f"document_references[{index}]",
        )
        if key in metadata:
            raise _fail(
                f"document_references[{index}] has a duplicate identity.",
                stage="reference_selection",
                reference=identity,
            )
        artifact_kind = str(entry.get("artifact_kind") or "")
        if not artifact_kind:
            artifact_kind = "brep" if entry.get("brep_sha256") else "mesh_bms"
        if artifact_kind not in {"brep", "mesh_bms"}:
            raise _fail(
                f"document_references[{index}] has unsupported artifact kind "
                f"{artifact_kind!r}.",
                stage="reference_selection",
                reference=identity,
                actual_artifact_kind=artifact_kind,
            )
        path = (resolved_root / str(entry.get("artifact_path") or "")).resolve()
        if resolved_root not in path.parents or not path.is_file():
            raise _fail(
                f"document_references[{index}] artifact is missing or outside staging.",
                stage="reference_selection",
                path=f"document_references[{index}].artifact_path",
            )
        artifact_bytes = path.stat().st_size
        if not 1 <= artifact_bytes <= _MAX_ARTIFACT_BYTES:
            raise _fail(
                f"document_references[{index}] artifact size must be 1-"
                f"{_MAX_ARTIFACT_BYTES} bytes.",
                stage="reference_selection",
                artifact_bytes=artifact_bytes,
            )
        if artifact_kind == "brep":
            expected_digest = str(entry.get("brep_sha256") or "")
        else:
            expected_digest = str(
                entry.get("mesh_sha256") or entry.get("artifact_sha256") or ""
            )
        observed_digest = _sha256_file(path)
        if expected_digest != observed_digest:
            raise _fail(
                f"document_references[{index}] {artifact_kind} SHA-256 does not "
                "match the host snapshot.",
                stage="reference_selection",
                reference=identity,
            )
        clean_metadata = {
            "document_uid": key[0],
            "object_name": key[1],
            "label": str(entry.get("label") or ""),
            "type_id": str(entry.get("type_id") or ""),
            "artifact_kind": artifact_kind,
            "artifact_sha256": observed_digest,
            "source_kind": str(entry.get("source_kind") or ""),
            "source_program_id": str(entry.get("source_program_id") or ""),
            "source_program_domain": str(entry.get("source_program_domain") or ""),
            "source_revision": str(entry.get("source_revision") or ""),
            "transient_topology": bool(entry.get("transient_topology")),
        }
        if artifact_kind == "brep":
            try:
                shape = Part.Shape()
                shape.importBrep(str(path))
            except Exception as exc:
                raise _fail(
                    f"document_references[{index}] could not import its BREP: {exc}",
                    stage="reference_selection",
                    reference=identity,
                    exception_type=type(exc).__name__,
                ) from exc
            if shape.isNull() or not shape.isValid():
                raise _fail(
                    f"document_references[{index}] is not a valid BREP shape.",
                    stage="reference_selection",
                    reference=identity,
                )
            expected_type = str(entry.get("shape_type") or "")
            if expected_type and str(shape.ShapeType) != expected_type:
                raise _fail(
                    f"document_references[{index}] changed ShapeType during transfer: "
                    f"expected {expected_type}, received {shape.ShapeType}.",
                    stage="reference_selection",
                    reference=identity,
                    expected_shape_type=expected_type,
                    actual_shape_type=str(shape.ShapeType),
                )
            shapes[key] = shape
            clean_metadata["shape_type"] = str(shape.ShapeType)
            clean_metadata["facts"] = dict(entry.get("facts") or {})
        else:
            try:
                mesh = Mesh.Mesh(str(path))
            except Exception as exc:
                raise _fail(
                    f"document_references[{index}] could not import its BMS: {exc}",
                    stage="reference_selection",
                    reference=identity,
                    exception_type=type(exc).__name__,
                ) from exc
            if not 1 <= int(mesh.CountFacets) <= _MAX_FACETS:
                raise _fail(
                    f"document_references[{index}] mesh must contain 1-{_MAX_FACETS} facets.",
                    stage="reference_selection",
                    reference=identity,
                    facet_count=int(mesh.CountFacets),
                )
            segments = _bounded_segments(
                entry.get("mesh_segments", []),
                facet_count=int(mesh.CountFacets),
                context=f"document_references[{index}].mesh_segments",
            )
            imported_segment_count = int(mesh.countSegments())
            if imported_segment_count > _MAX_REFERENCE_SEGMENTS:
                raise _fail(
                    f"document_references[{index}] imported BMS contains "
                    f"{imported_segment_count} segments; the limit is "
                    f"{_MAX_REFERENCE_SEGMENTS}.",
                    stage="reference_selection",
                    segment_count=imported_segment_count,
                )
            imported_segments = _bounded_segments(
                [
                    [int(item) for item in list(mesh.getSegment(segment_index) or [])]
                    for segment_index in range(imported_segment_count)
                ],
                facet_count=int(mesh.CountFacets),
                context=f"document_references[{index}].imported_mesh_segments",
            )
            if imported_segments and imported_segments != segments:
                raise _fail(
                    f"document_references[{index}] BMS segment groups differ from "
                    "the authenticated host metadata.",
                    stage="reference_selection",
                    reference=identity,
                )
            if not imported_segments:
                for segment in segments:
                    mesh.addSegment(segment)
            restored_segments = _bounded_segments(
                [
                    [int(item) for item in list(mesh.getSegment(segment_index) or [])]
                    for segment_index in range(int(mesh.countSegments()))
                ],
                facet_count=int(mesh.CountFacets),
                context=f"document_references[{index}].restored_mesh_segments",
            )
            if restored_segments != segments:
                raise _fail(
                    f"document_references[{index}] could not restore the exact "
                    "authenticated BMS segment groups.",
                    stage="reference_selection",
                    reference=identity,
                )
            meshes[key] = mesh
            clean_metadata["mesh_segments"] = segments
            clean_metadata["facts"] = dict(entry.get("facts") or {})
        metadata[key] = MappingProxyType(clean_metadata)
    global _REFERENCE_SHAPES, _REFERENCE_MESHES, _REFERENCE_METADATA
    _REFERENCE_SHAPES = MappingProxyType(shapes)
    _REFERENCE_MESHES = MappingProxyType(meshes)
    _REFERENCE_METADATA = MappingProxyType(metadata)


def detached_reference_shape(
    reference: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    key = _reference_key(reference, context="shape reference")
    shape = _REFERENCE_SHAPES.get(key)
    if shape is None:
        actual = (_REFERENCE_METADATA.get(key) or {}).get("artifact_kind")
        correction = (
            "Reference a Part/OCC object with a non-null Shape."
            if actual is None
            else f"The selected object is {actual}; mesh_from_shape requires BREP."
        )
        raise _fail(
            f"Reference {key[1]!r} is not an authenticated BREP input.",
            stage="reference_selection",
            reference={"document_uid": key[0], "object_name": key[1]},
            actual_artifact_kind=actual,
            required_changes=[correction],
        )
    return shape.copy(), dict(_REFERENCE_METADATA[key])


def detached_reference_mesh(reference: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    key = _reference_key(reference, context="mesh reference")
    mesh = _REFERENCE_MESHES.get(key)
    if mesh is None:
        actual = (_REFERENCE_METADATA.get(key) or {}).get("artifact_kind")
        correction = (
            "Reference a Mesh::Feature containing at least one facet."
            if actual is None
            else f"The selected object is {actual}; shape_from_mesh requires a mesh."
        )
        raise _fail(
            f"Reference {key[1]!r} is not an authenticated mesh input.",
            stage="reference_selection",
            reference={"document_uid": key[0], "object_name": key[1]},
            actual_artifact_kind=actual,
            required_changes=[correction],
        )
    return mesh.copy(), dict(_REFERENCE_METADATA[key])


def _encoded(value: Any) -> bytes:
    try:
        result = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"A MeshPart definition is not bounded JSON: {exc}",
            stage="definition_contract",
            exception_type=type(exc).__name__,
        ) from exc
    if len(result) > _MAX_DEFINITION_BYTES:
        raise _fail(
            f"A MeshPart definition exceeds {_MAX_DEFINITION_BYTES} JSON bytes.",
            stage="definition_contract",
            json_bytes=len(result),
        )
    return result


def _payload(
    value: Any,
    *,
    context: str,
    require_domain_value: bool,
    definition_domain: str,
) -> dict[str, Any]:
    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif isinstance(value, Mapping) and not require_domain_value:
        payload = dict(value)
    else:
        raise _fail(
            f"{context} must be a value returned by the active MeshPart api.",
            stage="definition_contract",
            path=context,
        )
    fields = {"domain", "operation", "output_type", "arguments", "properties"}
    if set(payload) != fields:
        raise _fail(
            f"{context} has malformed MeshPart definition fields.",
            stage="definition_contract",
            path=context,
            missing=sorted(fields - set(payload)),
            unexpected=sorted(set(payload) - fields),
        )
    operation = str(payload.get("operation") or "")
    if payload.get("domain") != definition_domain or operation not in _EXPORTS:
        raise _fail(
            f"{context} is not a supported MeshPart graph value.",
            stage="definition_contract",
            path=context,
            domain=payload.get("domain"),
            operation=operation,
        )
    arguments = payload.get("arguments")
    properties = payload.get("properties")
    if not isinstance(arguments, list) or len(arguments) != 1:
        raise _fail(
            f"{context} must contain exactly one source reference argument.",
            stage="definition_contract",
            path=f"{context}.arguments",
        )
    if not isinstance(properties, dict):
        raise _fail(
            f"{context}.properties must be an object.",
            stage="definition_contract",
            path=f"{context}.properties",
        )
    _encoded(payload)
    api = MeshPartDomainAPI(
        _EXPORTS,
        _OUTPUT_TYPES,
        domain=definition_domain,
    )
    try:
        kwargs = dict(properties)
        if operation == "shape_from_mesh":
            kwargs["output_type"] = str(payload.get("output_type") or "")
        rebuilt = getattr(api, operation)(arguments[0], **kwargs).to_payload()
    except (TypeError, ValueError) as exc:
        raise _fail(
            str(exc),
            stage="definition_contract",
            path=context,
            operation=operation,
            exception_type=type(exc).__name__,
        ) from exc
    if rebuilt != payload:
        raise _fail(
            f"{context} is not the canonical result of api.{operation}.",
            stage="definition_contract",
            path=context,
            required_changes=[
                f"Recreate this value with api.{operation}; do not edit serialized fields."
            ],
        )
    return payload


def validate_meshpart_definition(
    value: Any,
    *,
    expected_output_type: str | None = None,
    require_domain_value: bool = True,
    context: str = "result",
    definition_domain: str = "meshpart",
) -> dict[str, Any]:
    clean_domain = str(definition_domain or "").strip().lower()
    if clean_domain not in {"mesh", "meshpart"}:
        raise _fail(
            f"{context} has unsupported conversion domain {definition_domain!r}.",
            stage="definition_contract",
            path=f"{context}.domain",
        )
    payload = _payload(
        value,
        context=context,
        require_domain_value=require_domain_value,
        definition_domain=clean_domain,
    )
    output_type = str(payload.get("output_type") or "")
    operation = str(payload["operation"])
    expected_operation_type = "mesh" if operation == "mesh_from_shape" else None
    if expected_operation_type is not None and output_type != expected_operation_type:
        raise _fail(
            f"{context} api.{operation} must return type {expected_operation_type!r}.",
            stage="definition_contract",
            path=f"{context}.output_type",
        )
    if operation == "shape_from_mesh" and output_type not in _BREP_TYPES:
        raise _fail(
            f"{context} api.shape_from_mesh has unsupported output type {output_type!r}.",
            stage="definition_contract",
            path=f"{context}.output_type",
        )
    if expected_output_type is not None and output_type != expected_output_type:
        raise _fail(
            f"{context} returned type {output_type!r}; expected {expected_output_type!r}.",
            stage="definition_contract",
            path=f"{context}.output_type",
            expected=expected_output_type,
            received=output_type,
        )
    return payload


def _select_shape(shape: Any, selectors: Sequence[str]) -> Any:
    if not selectors:
        # Normalize the generic Part.Shape wrapper returned by BREP import to a
        # detached typed TopoShape. Besides preventing accidental registry
        # mutation, this makes mass properties available consistently in both
        # the worker and the independent host validator.
        return shape.copy()
    import Part

    selected = []
    collections = {
        "Face": list(shape.Faces),
        "Shell": list(shape.Shells),
        "Solid": list(shape.Solids),
    }
    for selector in selectors:
        kind = next(name for name in collections if selector.startswith(name))
        index = int(selector[len(kind) :])
        values = collections[kind]
        if index > len(values):
            raise _fail(
                f"mesh_from_shape selector {selector!r} is outside the available "
                f"{kind} range 1-{len(values)}.",
                stage="shape_selection",
                selector=selector,
                available={name: len(items) for name, items in collections.items()},
                required_changes=[
                    "Inspect the current MeshPart domain context and choose a live 1-based selector."
                ],
            )
        selected.append(values[index - 1])
    result = selected[0].copy() if len(selected) == 1 else Part.makeCompound(selected)
    if result.isNull() or not result.isValid():
        raise _fail(
            "mesh_from_shape subelement selection produced invalid OCC topology.",
            stage="shape_selection",
            selectors=list(selectors),
        )
    return result


def _netgen_available() -> bool:
    global _NETGEN_AVAILABLE
    if _NETGEN_AVAILABLE is not None:
        return _NETGEN_AVAILABLE
    import MeshPart
    import Part

    probe = Part.makeBox(1.0, 1.0, 1.0)
    try:
        MeshPart.meshFromShape(
            Shape=probe,
            Fineness=0,
            SecondOrder=0,
            Optimize=0,
            AllowQuad=0,
            MinLength=0.0,
            MaxLength=0.0,
        )
    except RuntimeError as exc:
        if "WITHOUT NETGEN" not in str(exc).upper():
            raise
        _NETGEN_AVAILABLE = False
    else:
        _NETGEN_AVAILABLE = True
    return bool(_NETGEN_AVAILABLE)


def _build_mesh(shape: Any, properties: Mapping[str, Any]) -> tuple[Any, str]:
    import MeshPart

    method = str(properties["method"])
    try:
        if method == "standard":
            mesh = MeshPart.meshFromShape(
                Shape=shape,
                LinearDeflection=float(properties["linear_deflection"]),
                AngularDeflection=math.radians(
                    float(properties["angular_deflection_degrees"])
                ),
                Relative=bool(properties["relative"]),
                Segments=bool(properties["preserve_face_groups"]),
            )
            backend = "opencascade"
        elif method == "max_length":
            mesh = MeshPart.meshFromShape(
                Shape=shape,
                MaxLength=float(properties["max_length"]),
            )
            backend = "mefisto"
        elif method == "max_area":
            mesh = MeshPart.meshFromShape(
                Shape=shape,
                MaxArea=float(properties["max_area"]),
            )
            backend = "mefisto"
        elif method == "local_length":
            mesh = MeshPart.meshFromShape(
                Shape=shape,
                LocalLength=float(properties["local_length"]),
            )
            backend = "mefisto"
        elif method == "deflection":
            mesh = MeshPart.meshFromShape(
                Shape=shape,
                Deflection=float(properties["deflection"]),
            )
            backend = "mefisto"
        elif method == "min_max_length":
            mesh = MeshPart.meshFromShape(
                Shape=shape,
                MinLength=float(properties["min_length"]),
                MaxLength=float(properties["max_length"]),
            )
            backend = "mefisto"
        else:
            if not _netgen_available():
                raise _fail(
                    f"MeshPart method {method!r} requires Netgen, but this FreeCAD "
                    "build was compiled without Netgen support.",
                    stage="native_mesher_capability",
                    method=method,
                    netgen_available=False,
                    required_changes=[
                        "Use method='standard' or one of the Mefisto methods in this build."
                    ],
                )
            common = {
                "Shape": shape,
                "SecondOrder": int(bool(properties["second_order"])),
                "Optimize": int(bool(properties["optimize"])),
                "AllowQuad": int(bool(properties["allow_quad"])),
                "MinLength": float(properties["min_length"] or 0.0),
                "MaxLength": float(properties["max_length"] or 0.0),
            }
            if method == "netgen_fineness":
                fineness = {
                    "very_coarse": 0,
                    "coarse": 1,
                    "moderate": 2,
                    "fine": 3,
                    "very_fine": 4,
                }[str(properties["fineness"])]
                mesh = MeshPart.meshFromShape(Fineness=fineness, **common)
            else:
                mesh = MeshPart.meshFromShape(
                    GrowthRate=float(properties["growth_rate"]),
                    SegPerEdge=float(properties["segments_per_edge"]),
                    SegPerRadius=float(properties["segments_per_radius"]),
                    **common,
                )
            backend = "netgen"
    except MeshPartCandidateError:
        raise
    except Exception as exc:
        raise _fail(
            f"Native MeshPart meshing failed for method {method!r}: "
            f"{type(exc).__name__}: {exc}",
            stage="native_mesher",
            method=method,
            exception_type=type(exc).__name__,
            required_changes=[
                "Check source topology and choose positive mesher parameters appropriate to its scale."
            ],
        ) from exc
    if not 1 <= int(mesh.CountFacets) <= _MAX_FACETS:
        raise _fail(
            f"Native MeshPart meshing produced {int(mesh.CountFacets)} facets; "
            f"the accepted range is 1-{_MAX_FACETS}.",
            stage="native_mesher",
            method=method,
            points=int(mesh.CountPoints),
            facets=int(mesh.CountFacets),
        )
    return mesh, backend


def _mesh_segments(mesh: Any) -> list[list[int]]:
    count = int(mesh.countSegments())
    if count > _MAX_REFERENCE_SEGMENTS:
        raise _fail(
            f"Native meshing produced {count} face groups; the limit is "
            f"{_MAX_REFERENCE_SEGMENTS}.",
            stage="native_mesher",
            segment_count=count,
        )
    result = []
    for index in range(count):
        raw = [int(item) for item in list(mesh.getSegment(index) or [])]
        result.append(raw)
    return _bounded_segments(
        result,
        facet_count=int(mesh.CountFacets),
        context="native_mesh.segments",
        stage="native_mesher",
    )


def _export_mesh(mesh: Any, path: Path, *, output_name: str) -> str:
    try:
        mesh.write(str(path))
    except Exception as exc:
        raise _fail(
            f"Could not export MeshPart mesh output {output_name!r}: "
            f"{type(exc).__name__}: {exc}",
            stage="artifact_export",
            output_name=output_name,
        ) from exc
    if (
        not path.is_file()
        or path.stat().st_size <= 0
        or path.stat().st_size > _MAX_ARTIFACT_BYTES
    ):
        raise _fail(
            f"MeshPart mesh output {output_name!r} has an invalid BMS artifact size.",
            stage="artifact_export",
            output_name=output_name,
            artifact_bytes=path.stat().st_size if path.is_file() else 0,
        )
    return _sha256_file(path)


def _brep_roundtrip(shape: Any, scratch: Path, *, context: str) -> Any:
    """Strip transient element maps before closed-shell and refinement operations.

    This build can terminate FreeCADCmd when Part.makeSolid is called directly on
    a shell produced by mesh sewing. A BREP round trip is the native GUI command's
    stable topology boundary and keeps the fatal path out of provider-controlled
    source.
    """

    import Part

    try:
        shape.exportBrep(str(scratch))
        if (
            not scratch.is_file()
            or scratch.stat().st_size <= 0
            or scratch.stat().st_size > _MAX_ARTIFACT_BYTES
        ):
            raise RuntimeError("BREP scratch artifact has an invalid size")
        result = Part.Shape()
        result.importBrep(str(scratch))
        if result.isNull() or not result.isValid():
            raise RuntimeError("BREP round trip produced invalid topology")
        return result
    except Exception as exc:
        raise _fail(
            f"{context} topology normalization failed: {type(exc).__name__}: {exc}",
            stage="shape_normalization",
            exception_type=type(exc).__name__,
        ) from exc
    finally:
        try:
            scratch.unlink(missing_ok=True)
        except OSError:
            pass


def _selected_mesh(
    mesh: Any,
    metadata: Mapping[str, Any],
    properties: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    facet_count = int(mesh.CountFacets)
    facet_indices = properties.get("facet_indices")
    segment_index = properties.get("segment_index")
    if facet_indices is not None:
        native = [int(index) - 1 for index in list(facet_indices)]
        invalid = [index + 1 for index in native if not 0 <= index < facet_count]
        if invalid:
            raise _fail(
                f"shape_from_mesh facet selection is outside the available 1-{facet_count} range.",
                stage="mesh_selection",
                invalid_indices=invalid[:64],
                invalid_indices_truncated=len(invalid) > 64,
                available_facet_count=facet_count,
                required_changes=[
                    "Inspect the current MeshPart mesh source and use live 1-based facet indices."
                ],
            )
        selected = mesh.meshFromSegment(native)
        selection = {"kind": "facets", "facet_indices": list(facet_indices)}
    elif segment_index is not None:
        segments = list(metadata.get("mesh_segments") or [])
        index = int(segment_index)
        if not 1 <= index <= len(segments):
            raise _fail(
                f"shape_from_mesh segment_index {index} is outside the available "
                f"1-{len(segments)} range.",
                stage="mesh_selection",
                requested_segment=index,
                available_segment_count=len(segments),
                required_changes=[
                    "Choose a reported source segment, or omit segment_index to convert the full mesh."
                ],
            )
        selected = mesh.meshFromSegment(list(segments[index - 1]))
        selection = {
            "kind": "segment",
            "segment_index": index,
            "source_facet_indices": [item + 1 for item in segments[index - 1]],
        }
    else:
        selected = mesh.copy()
        selection = {"kind": "all"}
    if int(selected.CountFacets) <= 0:
        raise _fail(
            "shape_from_mesh selection contains no facets.",
            stage="mesh_selection",
            selection=selection,
        )
    selection["selected_facet_count"] = int(selected.CountFacets)
    return selected, selection


def _convert_mesh_to_shape(
    mesh: Any,
    properties: Mapping[str, Any],
    output_type: str,
    scratch_root: Path,
    output_index: int,
) -> tuple[Any, dict[str, Any]]:
    import MeshPart
    import Part

    representation = str(properties["representation"])
    raw_tolerance = properties["tolerance"]
    tolerance = float(raw_tolerance) if raw_tolerance is not None else None
    stages: list[str] = []
    if representation == "boundary":
        try:
            wires = list(MeshPart.wireFromMesh(mesh) or [])
        except Exception as exc:
            raise _fail(
                f"Native MeshPart boundary extraction failed: "
                f"{type(exc).__name__}: {exc}",
                stage="boundary_extraction",
                exception_type=type(exc).__name__,
            ) from exc
        if not wires:
            raise _fail(
                "The selected mesh has no boundary wires (it may be closed).",
                stage="boundary_extraction",
                selected_facets=int(mesh.CountFacets),
                required_changes=[
                    "Choose an open mesh or a facet/segment subset with a boundary.",
                    "Use representation='surface' for a closed shell or solid.",
                ],
            )
        invalid = [
            index
            for index, wire in enumerate(wires, start=1)
            if wire.isNull() or not wire.isValid() or str(wire.ShapeType) != "Wire"
        ]
        if invalid:
            raise _fail(
                "Native MeshPart returned invalid boundary wires.",
                stage="boundary_extraction",
                invalid_wire_indices=invalid,
            )
        if output_type == "wire":
            if len(wires) != 1:
                raise _fail(
                    f"A wire output requires exactly one boundary loop; native MeshPart "
                    f"found {len(wires)}.",
                    stage="boundary_extraction",
                    boundary_count=len(wires),
                    required_changes=[
                        "Use output_type='compound' to retain multiple boundary wires.",
                        "Select a facet region with exactly one boundary loop.",
                    ],
                )
            result = wires[0]
        else:
            result = Part.makeCompound(wires)
        stages.append("MeshPart.wireFromMesh")
        return result, {
            "representation": representation,
            "boundary_count": len(wires),
            "boundary_edge_counts": [len(wire.Edges) for wire in wires],
            "boundary_lengths_mm": [float(wire.Length) for wire in wires],
            "normal_harmonized": False,
            "refined": False,
            "safe_native_stages": stages,
        }

    normal_harmonized = bool(properties["harmonize_normals"])
    if normal_harmonized:
        try:
            mesh.harmonizeNormals()
        except Exception as exc:
            raise _fail(
                f"Native Mesh normal harmonization failed: {type(exc).__name__}: {exc}",
                stage="normal_harmonization",
                exception_type=type(exc).__name__,
            ) from exc
        stages.append("Mesh.harmonizeNormals")
    try:
        result = Part.Shape()
        # Do not use makeShapeFromMesh(..., sew=True): it can terminate this
        # FreeCADCmd build for ordinary closed meshes. Sewing is a separate,
        # verified worker stage below.
        if tolerance is None:
            raise _fail(
                "Surface conversion requires a validated sewing tolerance.",
                stage="definition_contract",
                path="properties.tolerance",
            )
        result.makeShapeFromMesh(mesh.Topology, tolerance, False)
    except Exception as exc:
        raise _fail(
            f"Native mesh-to-face conversion failed: {type(exc).__name__}: {exc}",
            stage="mesh_to_faces",
            exception_type=type(exc).__name__,
        ) from exc
    stages.append("Part.Shape.makeShapeFromMesh(sew=False)")
    if result.isNull() or not result.isValid() or not result.Faces:
        raise _fail(
            "Mesh-to-face conversion produced no valid OCC faces.",
            stage="mesh_to_faces",
            selected_facets=int(mesh.CountFacets),
        )
    if output_type == "face":
        if len(result.Faces) != 1:
            raise _fail(
                f"A face output requires exactly one converted facet; received "
                f"{len(result.Faces)} faces.",
                stage="shape_typing",
                converted_face_count=len(result.Faces),
                required_changes=[
                    "Select exactly one facet, or choose shell/compound output_type."
                ],
            )
        result = result.Faces[0]
    elif output_type in {"shell", "solid"}:
        try:
            result.sewShape(tolerance)
        except Exception as exc:
            raise _fail(
                f"Native OCC sewing failed: {type(exc).__name__}: {exc}",
                stage="shape_sewing",
                tolerance=tolerance,
                exception_type=type(exc).__name__,
            ) from exc
        stages.append("TopoShape.sewShape")
        result = _brep_roundtrip(
            result,
            scratch_root / f"meshpart-normalize-{output_index:03d}.brep",
            context="Sewn mesh",
        )
        stages.append("BREP topology normalization")
        if str(result.ShapeType) != "Shell":
            raise _fail(
                f"A {output_type} output requires exactly one sewn Shell; OCC returned "
                f"{result.ShapeType} containing {len(result.Shells)} shells.",
                stage="shape_typing",
                shape_type=str(result.ShapeType),
                shell_count=len(result.Shells),
                required_changes=[
                    "Select one connected component, or use output_type='compound'."
                ],
            )
        closed = bool(result.isClosed())
        if bool(properties["require_closed"]) and not closed:
            raise _fail(
                f"The converted Shell is open but output_type={output_type!r} requires "
                "closed topology.",
                stage="shape_typing",
                open_shell=True,
                required_changes=[
                    "Repair or close the source mesh in the Mesh workbench before conversion.",
                    "Use output_type='shell' with require_closed=False when an open shell is intentional.",
                ],
            )
        if output_type == "solid":
            try:
                result = Part.makeSolid(result)
            except Exception as exc:
                raise _fail(
                    f"Native OCC solid construction failed: "
                    f"{type(exc).__name__}: {exc}",
                    stage="solid_construction",
                    exception_type=type(exc).__name__,
                ) from exc
            stages.append("Part.makeSolid")
            result = _brep_roundtrip(
                result,
                scratch_root / f"meshpart-solid-{output_index:03d}.brep",
                context="Converted solid",
            )
            stages.append("BREP solid normalization")
    refined = bool(properties["refine"])
    before_refine_type = str(result.ShapeType)
    before_refine_faces = len(result.Faces)
    if refined:
        result = _brep_roundtrip(
            result,
            scratch_root / f"meshpart-refine-input-{output_index:03d}.brep",
            context="Refinement input",
        )
        try:
            result = result.removeSplitter()
        except Exception as exc:
            raise _fail(
                f"Native OCC refinement failed: {type(exc).__name__}: {exc}",
                stage="shape_refinement",
                exception_type=type(exc).__name__,
            ) from exc
        stages.append("TopoShape.removeSplitter")
    # Normalize generic Part.Shape wrappers returned by BREP import and
    # makeShapeFromMesh. Typed copies expose mass properties consistently in
    # the worker, independent host validator, publication, and save/reopen path.
    result = result.copy()
    if result.isNull() or not result.isValid():
        raise _fail(
            "MeshPart conversion produced invalid OCC topology.",
            stage="shape_validation",
        )
    expected_shape_type = {
        "solid": "Solid",
        "shell": "Shell",
        "face": "Face",
        "compound": "Compound",
    }[output_type]
    if str(result.ShapeType) != expected_shape_type:
        raise _fail(
            f"MeshPart conversion declared {output_type!r} but produced OCC "
            f"{result.ShapeType!r}.",
            stage="shape_typing",
            expected_shape_type=expected_shape_type,
            received_shape_type=str(result.ShapeType),
        )
    return result, {
        "representation": representation,
        "boundary_count": 0,
        "boundary_edge_counts": [],
        "boundary_lengths_mm": [],
        "normal_harmonized": normal_harmonized,
        "refined": refined,
        "before_refine_shape_type": before_refine_type,
        "before_refine_face_count": before_refine_faces,
        "safe_native_stages": stages,
    }


def _export_shape(shape: Any, path: Path, *, output_name: str) -> str:
    try:
        shape.exportBrep(str(path))
    except Exception as exc:
        raise _fail(
            f"Could not export MeshPart BREP output {output_name!r}: "
            f"{type(exc).__name__}: {exc}",
            stage="artifact_export",
            output_name=output_name,
        ) from exc
    if (
        not path.is_file()
        or path.stat().st_size <= 0
        or path.stat().st_size > _MAX_ARTIFACT_BYTES
    ):
        raise _fail(
            f"MeshPart BREP output {output_name!r} has an invalid artifact size.",
            stage="artifact_export",
            output_name=output_name,
            artifact_bytes=path.stat().st_size if path.is_file() else 0,
        )
    return _sha256_file(path)


def _source_identity(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: metadata.get(key)
        for key in (
            "document_uid",
            "object_name",
            "label",
            "type_id",
            "artifact_kind",
            "artifact_sha256",
            "source_kind",
            "source_program_id",
            "source_program_domain",
            "source_revision",
            "transient_topology",
        )
        if metadata.get(key) not in (None, "")
    }


def validate_and_convert_meshpart(
    result: Mapping[str, Any],
    expected_outputs: Sequence[Mapping[str, Any]],
    root: Path,
    *,
    max_shape_subelements: int,
    definition_domain: str = "meshpart",
    output_indices: Sequence[int] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate, execute, diagnose, and serialize all MeshPart outputs."""

    from vibescript_mesh_worker import mesh_diagnostics
    from vibescript_part_worker import part_shape_facts

    expected_names = [str(item.get("name") or "") for item in expected_outputs]
    if list(result) != expected_names:
        raise _fail(
            "MeshPart result keys must exactly match expected_outputs in declared order.",
            stage="result_contract",
            expected=expected_names,
            received=list(result),
        )
    detail_limit = max(0, min(_MAX_SUBELEMENT_FACTS, int(max_shape_subelements)))
    indices = (
        list(range(len(expected_outputs)))
        if output_indices is None
        else list(output_indices)
    )
    if len(indices) != len(expected_outputs) or any(
        isinstance(index, bool) or not isinstance(index, int) or index < 0
        for index in indices
    ):
        raise _fail(
            "MeshPart output_indices must contain one non-negative integer per output.",
            stage="result_contract",
        )
    outputs: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    mesh_output_count = 0
    shape_output_count = 0
    total_input_facets = 0
    total_output_facets = 0
    output_root = Path(root) / "outputs"
    for declaration, artifact_index in zip(expected_outputs, indices, strict=True):
        name = str(declaration.get("name") or "")
        expected_type = str(declaration.get("type") or "")
        definition = validate_meshpart_definition(
            result[name],
            expected_output_type=expected_type,
            context=f"result[{name!r}]",
            definition_domain=definition_domain,
        )
        operation = str(definition["operation"])
        properties = dict(definition["properties"])
        reference = dict(definition["arguments"][0])
        if operation == "mesh_from_shape":
            source_shape, source_metadata = detached_reference_shape(reference)
            selected_shape = _select_shape(
                source_shape,
                list(properties["subelements"]),
            )
            source_facts = part_shape_facts(
                selected_shape,
                max_subelements=min(32, detail_limit),
            )
            if source_facts["null"] or not source_facts["valid"]:
                raise _fail(
                    f"MeshPart BREP source {source_metadata['object_name']!r} is invalid.",
                    stage="shape_selection",
                    source=_source_identity(source_metadata),
                )
            mesh, backend = _build_mesh(selected_shape, properties)
            diagnostics = mesh_diagnostics(mesh)
            segments = _mesh_segments(mesh)
            relative = Path("outputs") / f"output-{artifact_index:03d}.bms"
            digest = _export_mesh(mesh, Path(root) / relative, output_name=name)
            data = {
                "schema": VALIDATION_SCHEMA,
                "operation": operation,
                "label": str(properties["label"]),
                "source": _source_identity(source_metadata),
                "source_shape_facts": source_facts,
                "subelements": list(properties["subelements"]),
                "method": str(properties["method"]),
                "parameters": {
                    key: properties[key]
                    for key in (
                        "linear_deflection",
                        "angular_deflection_degrees",
                        "relative",
                        "preserve_face_groups",
                        "max_length",
                        "max_area",
                        "local_length",
                        "deflection",
                        "min_length",
                        "fineness",
                        "growth_rate",
                        "segments_per_edge",
                        "segments_per_radius",
                        "second_order",
                        "optimize",
                        "allow_quad",
                    )
                },
                "mesher_backend": backend,
                "segments": segments,
                "artifact_sha256": digest,
                "diagnostics": diagnostics,
            }
            item = {
                "name": name,
                "type": "mesh",
                "definition": definition,
                "artifact_kind": "mesh_bms",
                "artifact_path": str(relative),
                "artifact_sha256": digest,
                "facts": diagnostics,
                "meshpart_data": data,
            }
            summary = {
                "name": name,
                "type": "mesh",
                "operation": operation,
                "source_object": str(source_metadata["object_name"]),
                "artifact_sha256": digest,
                "points": int(diagnostics["points"]),
                "facets": int(diagnostics["facets"]),
                "segments": len(segments),
                "method": str(properties["method"]),
            }
            mesh_output_count += 1
            total_output_facets += int(diagnostics["facets"])
        else:
            source_mesh, source_metadata = detached_reference_mesh(reference)
            source_diagnostics = mesh_diagnostics(source_mesh)
            selected, selection = _selected_mesh(
                source_mesh,
                source_metadata,
                properties,
            )
            selected_diagnostics = mesh_diagnostics(selected)
            total_input_facets += int(selected_diagnostics["facets"])
            shape, conversion = _convert_mesh_to_shape(
                selected,
                properties,
                expected_type,
                output_root,
                artifact_index,
            )
            facts = part_shape_facts(shape, max_subelements=detail_limit)
            if facts["null"] or not facts["valid"]:
                raise _fail(
                    f"MeshPart shape output {name!r} is invalid.",
                    stage="shape_validation",
                    output_name=name,
                )
            relative = Path("outputs") / f"output-{artifact_index:03d}.brep"
            digest = _export_shape(shape, Path(root) / relative, output_name=name)
            data = {
                "schema": VALIDATION_SCHEMA,
                "operation": operation,
                "label": str(properties["label"]),
                "source": _source_identity(source_metadata),
                "source_mesh_diagnostics": source_diagnostics,
                "selection": selection,
                "selected_mesh_diagnostics": selected_diagnostics,
                "parameters": {
                    key: properties[key]
                    for key in (
                        "representation",
                        "tolerance",
                        "harmonize_normals",
                        "refine",
                        "require_closed",
                    )
                },
                "conversion": conversion,
                "artifact_sha256": digest,
                "shape_facts": facts,
            }
            item = {
                "name": name,
                "type": expected_type,
                "definition": definition,
                "artifact_kind": "brep",
                "artifact_path": str(relative),
                "artifact_sha256": digest,
                "facts": facts,
                "meshpart_data": data,
            }
            summary = {
                "name": name,
                "type": expected_type,
                "operation": operation,
                "source_object": str(source_metadata["object_name"]),
                "artifact_sha256": digest,
                "selected_facets": int(selected_diagnostics["facets"]),
                "shape_type": str(facts["shape_type"]),
                "faces": int(facts["faces"]),
                "representation": str(properties["representation"]),
            }
            shape_output_count += 1
        _encoded(data)
        outputs.append(item)
        summaries.append(summary)
    return outputs, {
        "schema": VALIDATION_SCHEMA,
        "output_count": len(outputs),
        "mesh_output_count": mesh_output_count,
        "shape_output_count": shape_output_count,
        "total_input_facets": total_input_facets,
        "total_output_facets": total_output_facets,
        "outputs": summaries,
    }
