# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native worker for production TechDraw VibeScript programs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any

from vibescript_domain_api import DomainValue
from vibescript_techdraw_api import TechDrawDomainAPI, _EXPORTS, _OUTPUT_TYPES


VALIDATION_SCHEMA = "vibecad-vibescript-techdraw-validation-v1"
PROJECTION_SCHEMA = "vibecad-vibescript-techdraw-projection-v1"
DIMENSION_SCHEMA = "vibecad-vibescript-techdraw-dimension-v1"
_OPERATION_OUTPUT = {
    "page": "page",
    "template": "template",
    "view": "view",
    "projection": "projection",
    "dimension": "dimension",
    "annotation": "annotation",
}
_REFERENCE_OPTIONAL_FIELDS = frozenset(
    {
        "label",
        "type_id",
        "shape_type",
        "facts",
        "source_kind",
        "source_program_id",
        "source_program_domain",
        "source_revision",
        "transient_topology",
        "requires_semantic_interfaces",
        "published_interfaces",
        "reference_contract_sha256",
    }
)
_SHEET_DIMENSIONS = {
    "a0": (1189.0, 841.0),
    "a1": (841.0, 594.0),
    "a2": (594.0, 420.0),
    "a3": (420.0, 297.0),
    "a4": (297.0, 210.0),
    "a5": (210.0, 148.0),
    "letter": (279.4, 215.9),
    "ledger": (431.8, 279.4),
}
_VIEW_DIRECTIONS = {
    "front": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
    "rear": ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)),
    "left": ((-1.0, 0.0, 0.0), (0.0, -1.0, 0.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "top": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    "bottom": ((0.0, 0.0, -1.0), (1.0, 0.0, 0.0)),
    "isometric": (
        (1.0, -1.0, 1.0),
        (0.7071067811865476, 0.7071067811865476, 0.0),
    ),
}
_PROJECTION_TYPES = {
    "front": "Front",
    "left": "Left",
    "right": "Right",
    "rear": "Rear",
    "top": "Top",
    "bottom": "Bottom",
    "front_top_left": "FrontTopLeft",
    "front_top_right": "FrontTopRight",
    "front_bottom_left": "FrontBottomLeft",
    "front_bottom_right": "FrontBottomRight",
}
_DIMENSION_TYPES = {
    "distance": "Distance",
    "distance_x": "DistanceX",
    "distance_y": "DistanceY",
    "radius": "Radius",
    "diameter": "Diameter",
    "angle": "Angle",
    "angle_3_point": "Angle3Pt",
    "area": "Area",
}
_MAX_DEFINITION_BYTES = 4 * 1024 * 1024
_MAX_NATIVE_READBACK_BYTES = 64 * 1024 * 1024
_MAX_REFERENCES = 128
_MAX_PROJECTION_EDGES = 200_000
_MAX_PROJECTION_FACES = 50_000
_MAX_DESCRIPTOR_ITEMS = 250_000
_MAX_MODEL_REFERENCE_SAMPLES = 64
_REFERENCES: Mapping[tuple[str, str], Mapping[str, Any]] = MappingProxyType({})


def _default_correction(details: Mapping[str, Any]) -> str:
    """Return one bounded source repair for every TechDraw failure stage."""

    stage = str(details.get("stage") or "")
    path = str(details.get("path") or "")
    output = str(details.get("output") or "")
    location = f" at {path}" if path else (f" {output!r}" if output else "")
    if stage in {"result_contract", "output_contract"}:
        return (
            "Return exactly the declared expected_outputs names, types, and insertion "
            "order. Remove only the extra/reordered entry or restore the missing one."
        )
    if stage == "definition_contract":
        return (
            f"Rebuild only the malformed value{location} with the matching TechDraw "
            "api operation; never construct, copy, or mutate serialized definitions."
        )
    if stage == "source_validation":
        return (
            "Change only the named api argument while preserving exact returned graph "
            "values, stable references, page ownership, and expected output declarations."
        )
    if stage == "reference_resolution":
        return (
            "Copy an eligible document_uid/object_name pair exactly from the current "
            "TechDraw domain context; do not use a label, stale object, raw path, or "
            "reference from another document."
        )
    if stage in {"graph_membership", "output_identity"}:
        return (
            "Return every template, page, view/projection, dimension, and annotation once; "
            "reuse those exact values so each non-page output belongs to exactly one page."
        )
    if stage == "dimension_reference":
        return (
            "Choose exact EdgeN, VertexN, or FaceN names from the reported authenticated "
            "dimension_reference_inventory for that view/direction and satisfy the "
            "reported dimension-kind reference rule."
        )
    if stage == "native_projection":
        return (
            "Change only the reported source reference, orientation/direction, scale, line "
            "visibility, or projection-group setting so the isolated worker produces "
            "non-empty bounded projected geometry."
        )
    if stage == "native_dimension":
        return (
            "Keep the accepted projection inventory and change only the dimension kind, "
            "projected references, measure mode, format, tolerance, or placement named by "
            "the native diagnostic."
        )
    if stage == "native_page_graph":
        return (
            "Reuse the exact returned template and content values in one page, preserve "
            "their declared order, and keep every dimension beside its source view."
        )
    if stage in {"projection_artifact", "artifact_export", "artifact_readback"}:
        return (
            "Retry the retained revision after correcting only the reported projection "
            "complexity or bounded project-artifact failure; never supply a filesystem path."
        )
    if stage in {"native_readback", "native_graph"}:
        return (
            "Use the reported native object, property, and exception to change only the "
            "responsible TechDraw definition, then retry the failed working revision."
        )
    return (
        "Correct only the reported TechDraw reference, selector, graph member, projected "
        "element, dimension setting, or page property and retry the failed working "
        "revision; do not recreate the program."
    )


class TechDrawCandidateError(RuntimeError):
    """A model-correctable TechDraw failure with structured diagnostics."""

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
                if isinstance(changes, Sequence) and not isinstance(changes, str | bytes)
                else ""
            )
            self.details["correction"] = correction or _default_correction(
                self.details
            )
        super().__init__(message)


def _fail(message: str, *, stage: str, **details: Any) -> TechDrawCandidateError:
    return TechDrawCandidateError(message, details={"stage": stage, **details})


def _encoded(
    value: Any,
    *,
    limit: int = _MAX_DEFINITION_BYTES,
    label: str = "definition",
) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"A TechDraw {label} is not bounded JSON: {exc}",
            stage="definition_contract",
            exception_type=type(exc).__name__,
        ) from exc
    if len(payload) > limit:
        raise _fail(
            f"A TechDraw {label} exceeds {limit} JSON bytes.",
            stage="definition_contract",
            json_bytes=len(payload),
        )
    return payload


def _definition_key(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _inflate(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_inflate(item) for item in value)
    if not isinstance(value, Mapping):
        return value
    if set(value) == {
        "domain",
        "operation",
        "output_type",
        "arguments",
        "properties",
    }:
        return DomainValue(
            domain=str(value.get("domain") or ""),
            operation=str(value.get("operation") or ""),
            output_type=str(value.get("output_type") or ""),
            arguments=tuple(_inflate(item) for item in list(value.get("arguments") or [])),
            properties={
                str(name): _inflate(item)
                for name, item in dict(value.get("properties") or {}).items()
            },
        )
    return {str(name): _inflate(item) for name, item in value.items()}


def _nested_definition(
    value: Any,
    output_types: str | Sequence[str],
    *,
    context: str,
) -> DomainValue:
    expected = (output_types,) if isinstance(output_types, str) else tuple(output_types)
    payload = validate_techdraw_definition(
        value,
        require_domain_value=False,
        context=context,
    )
    if str(payload["output_type"]) not in expected:
        raise _fail(
            f"{context} must have output type in {list(expected)!r}.",
            stage="definition_contract",
            path=f"{context}.output_type",
        )
    return _inflate(payload)


def validate_techdraw_definition(
    value: Any,
    *,
    expected_output_type: str | None = None,
    require_domain_value: bool = True,
    context: str = "definition",
) -> dict[str, Any]:
    """Replay one untrusted definition through the exact canonical API."""

    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif not require_domain_value and isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise _fail(
            f"{context} must be returned by the active TechDraw api.",
            stage="definition_contract",
            path=context,
        )
    fields = {"domain", "operation", "output_type", "arguments", "properties"}
    if set(payload) != fields or payload.get("domain") != "techdraw":
        raise _fail(
            f"{context} has malformed TechDraw definition fields.",
            stage="definition_contract",
            path=context,
        )
    operation = str(payload.get("operation") or "")
    output_type = str(payload.get("output_type") or "")
    if operation not in _OPERATION_OUTPUT or output_type != _OPERATION_OUTPUT[operation]:
        raise _fail(
            f"{context} has unsupported operation/type {operation!r}/{output_type!r}.",
            stage="definition_contract",
            path=context,
        )
    if expected_output_type is not None and output_type != expected_output_type:
        raise _fail(
            f"{context} must publish {expected_output_type!r}, not {output_type!r}.",
            stage="definition_contract",
            path=f"{context}.output_type",
        )
    arguments = payload.get("arguments")
    properties = payload.get("properties")
    if not isinstance(arguments, list) or not isinstance(properties, Mapping):
        raise _fail(
            f"{context} arguments/properties must be an array and object.",
            stage="definition_contract",
            path=context,
        )
    properties = dict(properties)
    api = TechDrawDomainAPI(_EXPORTS, _OUTPUT_TYPES)
    try:
        if operation == "template":
            if arguments or set(properties) != {"sheet_size", "editable_texts", "label"}:
                raise ValueError("template fields are malformed")
            rebuilt = api.template(**properties)
        elif operation == "view":
            required = {
                "orientation",
                "x_mm",
                "y_mm",
                "scale",
                "hidden_lines",
                "smooth_lines",
                "label",
            }
            if len(arguments) != 1 or set(properties) != required:
                raise ValueError("view fields are malformed")
            rebuilt = api.view(arguments[0], **properties)
        elif operation == "projection":
            required = {
                "directions",
                "convention",
                "x_mm",
                "y_mm",
                "scale",
                "spacing_x_mm",
                "spacing_y_mm",
                "hidden_lines",
                "smooth_lines",
                "label",
            }
            if len(arguments) != 1 or set(properties) != required:
                raise ValueError("projection fields are malformed")
            rebuilt = api.projection(arguments[0], **properties)
        elif operation == "dimension":
            required = {
                "references",
                "projection_direction",
                "measure",
                "x_mm",
                "y_mm",
                "format_spec",
                "over_tolerance",
                "under_tolerance",
                "show_units",
                "label",
            }
            if len(arguments) != 2 or set(properties) != required:
                raise ValueError("dimension fields are malformed")
            source = _nested_definition(
                arguments[0],
                ("view", "projection"),
                context=f"{context}.arguments[0]",
            )
            rebuilt = api.dimension(source, arguments[1], **properties)
        elif operation == "annotation":
            required = {
                "x_mm",
                "y_mm",
                "text_size_mm",
                "alignment",
                "label",
            }
            if len(arguments) != 1 or set(properties) != required:
                raise ValueError("annotation fields are malformed")
            rebuilt = api.annotation(arguments[0], **properties)
        else:
            required = {"convention", "scale", "label"}
            if len(arguments) != 2 or set(properties) != required:
                raise ValueError("page fields are malformed")
            template = _nested_definition(
                arguments[0], "template", context=f"{context}.arguments[0]"
            )
            if not isinstance(arguments[1], list):
                raise ValueError("page contents must be an array")
            contents = [
                _nested_definition(
                    item,
                    ("view", "projection", "dimension", "annotation"),
                    context=f"{context}.arguments[1][{index}]",
                )
                for index, item in enumerate(arguments[1])
            ]
            rebuilt = api.page(template, contents, **properties)
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"{context} is invalid: {exc}",
            stage="definition_contract",
            path=context,
            operation=operation,
        ) from exc
    canonical = rebuilt.to_payload()
    if canonical != payload:
        raise _fail(
            f"{context} is not the canonical api.{operation} representation.",
            stage="definition_contract",
            path=context,
        )
    _encoded(canonical)
    return canonical


def _reference_key(entry: Mapping[str, Any], *, context: str) -> tuple[str, str]:
    values = []
    for field, maximum in (("document_uid", 256), ("object_name", 128)):
        raw = entry.get(field)
        if (
            not isinstance(raw, str)
            or not raw
            or raw != raw.strip()
            or len(raw) > maximum
            or "\0" in raw
        ):
            raise _fail(
                f"{context}.{field} is invalid.",
                stage="reference_resolution",
                path=f"{context}.{field}",
            )
        values.append(raw)
    return values[0], values[1]


def configure_techdraw_references(
    root: Path,
    document_references: list[dict[str, Any]],
) -> None:
    """Authenticate exact detached BREP inputs for worker-only projection."""

    if len(document_references) > _MAX_REFERENCES:
        raise _fail(
            f"TechDraw accepts at most {_MAX_REFERENCES} document references.",
            stage="reference_resolution",
            reference_count=len(document_references),
            maximum=_MAX_REFERENCES,
        )
    from vibescript_part_worker import (
        configure_part_references,
        detached_reference_shape,
    )

    try:
        configure_part_references(root, document_references)
    except Exception as exc:
        raise _fail(
            f"TechDraw could not authenticate detached BREP references: {exc}",
            stage="reference_resolution",
            exception_type=type(exc).__name__,
        ) from exc
    references: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, entry in enumerate(document_references):
        context = f"document_references[{index}]"
        if not isinstance(entry, dict):
            raise _fail(
                f"{context} must be an object.",
                stage="reference_resolution",
                path=context,
            )
        required = {
            "document_uid",
            "object_name",
            "artifact_kind",
            "artifact_path",
            "brep_sha256",
        }
        if not required <= set(entry) or set(entry) - required - _REFERENCE_OPTIONAL_FIELDS:
            raise _fail(
                f"{context} has malformed fields.",
                stage="reference_resolution",
                path=context,
            )
        if entry.get("artifact_kind") != "brep":
            raise _fail(
                f"{context} must contain a BREP shape snapshot.",
                stage="reference_resolution",
                path=f"{context}.artifact_kind",
            )
        key = _reference_key(entry, context=context)
        if key in references:
            raise _fail(
                f"{context} duplicates document object {key[1]!r}.",
                stage="reference_resolution",
                path=context,
                object_name=key[1],
            )
        reference = {"document_uid": key[0], "object_name": key[1]}
        shape = detached_reference_shape(reference)
        if shape.isNull() or not shape.isValid():
            raise _fail(
                f"{context} contains an invalid authenticated BREP.",
                stage="reference_resolution",
                path=context,
                object_name=key[1],
            )
        references[key] = MappingProxyType(
            {
                "shape": shape,
                "artifact_sha256": str(entry.get("brep_sha256") or ""),
                "label": str(entry.get("label") or key[1]),
                "source_type_id": str(entry.get("type_id") or ""),
                "source_kind": str(entry.get("source_kind") or "shape"),
                "source_revision": str(entry.get("source_revision") or ""),
            }
        )
    global _REFERENCES
    _REFERENCES = MappingProxyType(references)


def techdraw_publication_references() -> list[dict[str, Any]]:
    """Return authenticated shapes already detached for bounded publication."""

    return [
        {
            "document_uid": key[0],
            "object_name": key[1],
            "label": str(value["label"]),
            "shape": value["shape"].copy(),
            "identity": {
                "document_uid": key[0],
                "object_name": key[1],
                "artifact_sha256": str(value["artifact_sha256"]),
                "source_type_id": str(value["source_type_id"]),
                "source_kind": str(value["source_kind"]),
                "source_revision": str(value["source_revision"]),
            },
        }
        for key, value in _REFERENCES.items()
    ]


def _source_keys(value: Any, *, context: str) -> tuple[tuple[str, str], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(
            f"{context} must be a sequence of authenticated references.",
            stage="reference_resolution",
            path=context,
        )
    keys = []
    for index, reference in enumerate(value):
        path = f"{context}[{index}]"
        if not isinstance(reference, Mapping) or set(reference) != {
            "document_uid",
            "object_name",
        }:
            raise _fail(
                f"{path} must contain exactly document_uid and object_name.",
                stage="reference_resolution",
                path=path,
            )
        key = _reference_key(reference, context=path)
        if key not in _REFERENCES:
            raise _fail(
                f"{path} refers to unauthenticated object {key[1]!r}.",
                stage="reference_resolution",
                path=path,
                requested={"document_uid": key[0], "object_name": key[1]},
                available_references=[
                    {"document_uid": item[0], "object_name": item[1]}
                    for item in list(_REFERENCES)[:32]
                ],
                available_references_truncated=len(_REFERENCES) > 32,
            )
        keys.append(key)
    return tuple(keys)


def _sheet_geometry(sheet_size: str) -> tuple[float, float, str]:
    family, orientation = sheet_size.rsplit("_", 1)
    landscape_width, landscape_height = _SHEET_DIMENSIONS[family]
    if orientation == "landscape":
        return landscape_width, landscape_height, "Landscape"
    return landscape_height, landscape_width, "Portrait"


def _finite_vector(value: Any, *, context: str) -> list[float]:
    values = [float(item) for item in value]
    if len(values) != 3 or any(not math.isfinite(item) for item in values):
        raise _fail(
            f"{context} is not a finite three-coordinate vector.",
            stage="native_readback",
            path=context,
        )
    return values


def _sha256_file(path: Path, *, stage: str = "artifact_readback") -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise _fail(
            f"TechDraw artifact hashing failed: {exc}",
            stage=stage,
            exception_type=type(exc).__name__,
        ) from exc
    return digest.hexdigest()


def _write_shape_artifact(
    root: Path,
    shape: Any,
    *,
    output_index: int,
    suffix: str,
) -> dict[str, Any]:
    relative = Path("outputs") / f"output-{output_index:03d}-{suffix}.brep"
    target = root / relative
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shape.exportBrep(str(target))
    except Exception as exc:
        raise _fail(
            f"TechDraw projection artifact export failed: {exc}",
            stage="artifact_export",
            artifact=str(relative),
            exception_type=type(exc).__name__,
        ) from exc
    if target.is_symlink() or not target.is_file():
        raise _fail(
            "TechDraw projection artifact was not written as a regular file.",
            stage="artifact_export",
            artifact=str(relative),
        )
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise _fail(
            f"TechDraw projection artifact size readback failed: {exc}",
            stage="artifact_readback",
            artifact=str(relative),
            exception_type=type(exc).__name__,
        ) from exc
    if not 1 <= size <= 256 * 1024 * 1024:
        raise _fail(
            "A precomputed TechDraw projection artifact has an invalid byte count.",
            stage="projection_artifact",
            artifact=str(relative),
            artifact_bytes=size,
        )
    return {
        "artifact_kind": "brep",
        "artifact_path": str(relative),
        "artifact_sha256": _sha256_file(target),
        "artifact_bytes": size,
        "shape_type": str(shape.ShapeType),
    }


def _shape_bounds_2d(shape: Any) -> dict[str, list[float]]:
    bounds = shape.BoundBox
    if bounds.isValid():
        minimum = [float(bounds.XMin), float(bounds.YMin)]
        maximum = [float(bounds.XMax), float(bounds.YMax)]
    else:
        minimum = [0.0, 0.0]
        maximum = [0.0, 0.0]
    return {
        "min": minimum,
        "max": maximum,
        "size": [maximum[0] - minimum[0], maximum[1] - minimum[1]],
    }


def _source_objects(document: Any) -> dict[tuple[str, str], Any]:
    result = {}
    for index, (key, source) in enumerate(_REFERENCES.items()):
        obj = document.addObject("Part::Feature", f"TechDrawSource{index:03d}")
        obj.Label = str(source["label"] or key[1])
        obj.Shape = source["shape"].copy()
        result[key] = obj
    return result


def _source_identities(keys: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "document_uid": key[0],
            "object_name": key[1],
            "artifact_sha256": str(_REFERENCES[key]["artifact_sha256"]),
            "source_type_id": str(_REFERENCES[key]["source_type_id"]),
            "source_kind": str(_REFERENCES[key]["source_kind"]),
            "source_revision": str(_REFERENCES[key]["source_revision"]),
        }
        for key in keys
    ]


def _configure_view_style(obj: Any, properties: Mapping[str, Any]) -> None:
    obj.ScaleType = "Custom"
    obj.Scale = float(properties["scale"])
    obj.X = float(properties["x_mm"])
    obj.Y = float(properties["y_mm"])
    hidden = bool(properties["hidden_lines"])
    smooth = bool(properties["smooth_lines"])
    for name in ("HardHidden", "SmoothHidden", "SeamHidden", "IsoHidden"):
        if hasattr(obj, name):
            setattr(obj, name, hidden)
    for name in ("SmoothVisible", "SeamVisible", "IsoVisible"):
        if hasattr(obj, name):
            setattr(obj, name, smooth)


def _projection_snapshot(
    obj: Any,
    root: Path,
    *,
    output_index: int,
    suffix: str,
    source_keys: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    snapshot = obj.getPrecomputedProjection()
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "edges",
        "faces",
        "edge_classes",
        "edge_visibility",
        "source_indices",
        "centroid",
    }:
        raise _fail(
            "Native TechDraw returned a malformed projection snapshot.",
            stage="native_projection",
            native_type=str(obj.TypeId),
        )
    edges_shape = snapshot["edges"]
    faces_shape = snapshot["faces"]
    edge_count = len(edges_shape.Edges)
    face_count = len(faces_shape.Faces)
    classes = [int(value) for value in snapshot["edge_classes"]]
    visibility = [bool(value) for value in snapshot["edge_visibility"]]
    source_indices = [int(value) for value in snapshot["source_indices"]]
    if not 1 <= edge_count <= _MAX_PROJECTION_EDGES:
        raise _fail(
            "Native TechDraw produced no edges or exceeded the projection edge limit.",
            stage="native_projection",
            edge_count=edge_count,
        )
    if face_count > _MAX_PROJECTION_FACES:
        raise _fail(
            "Native TechDraw exceeded the projection face limit.",
            stage="native_projection",
            face_count=face_count,
        )
    if not (
        len(classes) == len(visibility) == len(source_indices) == edge_count
    ):
        raise _fail(
            "Native TechDraw projection metadata is not edge-aligned.",
            stage="native_projection",
            edge_count=edge_count,
            class_count=len(classes),
            visibility_count=len(visibility),
            source_index_count=len(source_indices),
        )
    descriptors = obj.getProjectedElementDescriptors()
    if not isinstance(descriptors, dict) or set(descriptors) != {
        "coordinate_space",
        "view_scale",
        "edges",
        "vertices",
    }:
        raise _fail(
            "Native TechDraw returned malformed projected-element descriptors.",
            stage="native_projection",
        )
    descriptor_edges = list(descriptors["edges"])
    descriptor_vertices = list(descriptors["vertices"])
    if (
        len(descriptor_edges) != edge_count
        or len(descriptor_vertices) > _MAX_DESCRIPTOR_ITEMS
    ):
        raise _fail(
            "Native TechDraw projected-element inventory is inconsistent.",
            stage="native_projection",
            projected_edges=len(descriptor_edges),
            projected_vertices=len(descriptor_vertices),
            snapshot_edges=edge_count,
        )
    direction = _finite_vector(obj.Direction, context="projection.direction")
    x_direction = _finite_vector(obj.XDirection, context="projection.x_direction")
    result = {
        "schema": PROJECTION_SCHEMA,
        "native_type": str(obj.TypeId),
        "direction": direction,
        "x_direction": x_direction,
        "position_mm": [float(obj.X), float(obj.Y)],
        "scale": float(obj.Scale),
        "edge_count": edge_count,
        "face_count": face_count,
        "vertex_count": len(descriptor_vertices),
        "bounds_2d": _shape_bounds_2d(edges_shape),
        "edge_classes": classes,
        "edge_visibility": visibility,
        "source_indices": source_indices,
        "centroid": _finite_vector(
            snapshot["centroid"], context="projection.centroid"
        ),
        "descriptors": {
            "coordinate_space": str(descriptors["coordinate_space"]),
            "view_scale": float(descriptors["view_scale"]),
            "edges": descriptor_edges,
            "vertices": descriptor_vertices,
        },
        "source_identities": _source_identities(source_keys),
        "edges_artifact": _write_shape_artifact(
            root,
            edges_shape,
            output_index=output_index,
            suffix=f"{suffix}-edges",
        ),
        "faces_artifact": _write_shape_artifact(
            root,
            faces_shape,
            output_index=output_index,
            suffix=f"{suffix}-faces",
        ),
    }
    _encoded(result, limit=_MAX_NATIVE_READBACK_BYTES, label="projection readback")
    return result


def _references_readback(dimension: Any) -> list[dict[str, str]]:
    result = []
    for value in list(dimension.References2D or []):
        if not isinstance(value, (tuple, list)) or len(value) < 2:
            raise _fail(
                "Native TechDraw returned malformed dimension references.",
                stage="native_dimension",
            )
        target = value[0]
        raw_names = value[1]
        names = [raw_names] if isinstance(raw_names, str) else list(raw_names or [])
        result.extend(
            {
                "view": str(getattr(target, "Name", "") or ""),
                "subelement": str(name),
            }
            for name in names
        )
    return result


def _dimension_snapshot(dimension: Any) -> dict[str, Any]:
    snapshot = dimension.getPrecomputedDimension()
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "vectors",
        "scalars",
        "flags",
    }:
        raise _fail(
            "Native TechDraw returned a malformed dimension snapshot.",
            stage="native_dimension",
        )
    vectors = [
        _finite_vector(value, context=f"dimension.vectors[{index}]")
        for index, value in enumerate(snapshot["vectors"])
    ]
    scalars = [float(value) for value in snapshot["scalars"]]
    flags = [bool(value) for value in snapshot["flags"]]
    if (
        len(vectors) != 18
        or len(scalars) != 3
        or len(flags) != 4
        or any(not math.isfinite(value) for value in scalars)
    ):
        raise _fail(
            "Native TechDraw dimension snapshot has the wrong fixed shape.",
            stage="native_dimension",
            vector_count=len(vectors),
            scalar_count=len(scalars),
            flag_count=len(flags),
        )
    try:
        raw_value = float(dimension.getRawValue())
        display_text = str(dimension.getText())
    except Exception as exc:
        raise _fail(
            f"Native TechDraw dimension readback failed: {exc}",
            stage="native_dimension",
            native_error=str(exc),
        ) from exc
    if not math.isfinite(raw_value):
        raise _fail(
            "Native TechDraw dimension value is not finite.",
            stage="native_dimension",
        )
    result = {
        "schema": DIMENSION_SCHEMA,
        "native_type": str(dimension.TypeId),
        "vectors": vectors,
        "scalars": scalars,
        "flags": flags,
        "raw_value": raw_value,
        "display_text": display_text,
        "references": _references_readback(dimension),
        "native_state": [str(value) for value in list(dimension.State or [])],
    }
    _encoded(result, label="dimension readback")
    return result


def _validate_graph(
    raw_result: Mapping[str, Any],
    expected_outputs: list[dict[str, str]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, str],
    dict[str, tuple[str, str]],
    dict[str, str],
]:
    if not isinstance(raw_result, Mapping):
        raise _fail(
            "TechDraw result must be a mapping in expected_outputs order.",
            stage="result_contract",
            received_type=type(raw_result).__name__,
        )
    expected_names = [str(item["name"]) for item in expected_outputs]
    received_names = [str(name) for name in raw_result]
    if received_names != expected_names:
        raise _fail(
            "TechDraw result names/order do not exactly match expected_outputs.",
            stage="result_contract",
            expected=expected_names,
            received=received_names,
            missing=[name for name in expected_names if name not in raw_result],
            extra=[name for name in received_names if name not in expected_names],
        )
    definitions: dict[str, dict[str, Any]] = {}
    keys: dict[str, str] = {}
    output_by_key: dict[str, tuple[str, str]] = {}
    for expected in expected_outputs:
        name = str(expected["name"])
        output_type = str(expected["type"])
        definition = validate_techdraw_definition(
            raw_result[name],
            expected_output_type=output_type,
            context=f"result.{name}",
        )
        key = _definition_key(definition)
        if key in output_by_key:
            raise _fail(
                f"Outputs {output_by_key[key][0]!r} and {name!r} return duplicate "
                "TechDraw definitions.",
                stage="output_identity",
                output=name,
            )
        definitions[name] = definition
        keys[name] = key
        output_by_key[key] = (name, output_type)

    page_by_key: dict[str, str] = {}
    for expected in expected_outputs:
        page_name = str(expected["name"])
        definition = definitions[page_name]
        if definition["operation"] != "page":
            continue
        template_definition = definition["arguments"][0]
        contents = list(definition["arguments"][1])
        members = [template_definition, *contents]
        for index, member in enumerate(members):
            key = _definition_key(member)
            output = output_by_key.get(key)
            if output is None:
                path = "template" if index == 0 else f"contents[{index - 1}]"
                raise _fail(
                    f"Page {page_name!r} {path} is not returned as a stable output.",
                    stage="graph_membership",
                    output=page_name,
                    path=path,
                )
            if key in page_by_key:
                raise _fail(
                    f"TechDraw output {output[0]!r} belongs to both "
                    f"{page_by_key[key]!r} and {page_name!r}.",
                    stage="graph_membership",
                    output=output[0],
                )
            page_by_key[key] = page_name

    for expected in expected_outputs:
        name = str(expected["name"])
        output_type = str(expected["type"])
        if output_type != "page" and keys[name] not in page_by_key:
            raise _fail(
                f"TechDraw output {name!r} is not owned by exactly one returned page.",
                stage="graph_membership",
                output=name,
            )
        if output_type != "dimension":
            continue
        source_key = _definition_key(definitions[name]["arguments"][0])
        source = output_by_key.get(source_key)
        if source is None or source[1] not in {"view", "projection"}:
            raise _fail(
                f"Dimension {name!r} must reference a returned view or projection.",
                stage="graph_membership",
                output=name,
            )
        if page_by_key[source_key] != page_by_key[keys[name]]:
            raise _fail(
                f"Dimension {name!r} and source {source[0]!r} must belong to the same page.",
                stage="graph_membership",
                output=name,
                source_output=source[0],
            )
    return definitions, keys, output_by_key, page_by_key


def _page_for(
    key: str,
    page_by_key: Mapping[str, str],
    records: Mapping[str, Mapping[str, Any]],
) -> Any:
    page_name = page_by_key[key]
    return records[page_name]["object"]


def _build_template(
    document: Any,
    definition: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    properties = dict(definition["properties"])
    width, height, orientation = _sheet_geometry(str(properties["sheet_size"]))
    obj = document.addObject("TechDraw::DrawTemplate", f"TemplateCandidate{index:03d}")
    obj.Width = width
    obj.Height = height
    obj.Orientation = orientation
    obj.EditableTexts = dict(properties["editable_texts"])
    return {
        "object": obj,
        "data": {
            "schema": VALIDATION_SCHEMA,
            "operation": "template",
            "native_type": str(obj.TypeId),
            "sheet_size": str(properties["sheet_size"]),
            "width_mm": float(obj.Width),
            "height_mm": float(obj.Height),
            "orientation": str(obj.Orientation),
            "editable_texts": dict(obj.EditableTexts),
        },
    }


def _build_page(
    document: Any,
    definition: Mapping[str, Any],
    index: int,
    template: Any,
) -> dict[str, Any]:
    properties = dict(definition["properties"])
    obj = document.addObject("TechDraw::DrawPage", f"PageCandidate{index:03d}")
    obj.Template = template
    obj.ProjectionType = (
        "First angle"
        if properties["convention"] == "first_angle"
        else "Third angle"
    )
    obj.Scale = float(properties["scale"])
    obj.KeepUpdated = True
    return {"object": obj}


def _build_view(
    document: Any,
    definition: Mapping[str, Any],
    index: int,
    page: Any,
    sources: Mapping[tuple[str, str], Any],
) -> dict[str, Any]:
    import FreeCAD as App

    properties = dict(definition["properties"])
    source_keys = _source_keys(definition["arguments"][0], context="view.sources")
    obj = document.addObject("TechDraw::DrawViewPart", f"ViewCandidate{index:03d}")
    obj.Source = [sources[key] for key in source_keys]
    direction, x_direction = _VIEW_DIRECTIONS[str(properties["orientation"])]
    obj.Direction = App.Vector(*direction)
    obj.XDirection = App.Vector(*x_direction)
    page.addView(obj)
    # DrawPage centers newly added views. Apply the declarative placement only
    # after membership exists so the worker readback matches the program.
    _configure_view_style(obj, properties)
    return {"object": obj, "source_keys": source_keys}


def _build_projection(
    document: Any,
    definition: Mapping[str, Any],
    index: int,
    page: Any,
    sources: Mapping[tuple[str, str], Any],
) -> dict[str, Any]:
    properties = dict(definition["properties"])
    source_keys = _source_keys(
        definition["arguments"][0], context="projection.sources"
    )
    obj = document.addObject("TechDraw::DrawProjGroup", f"ProjectionCandidate{index:03d}")
    obj.Source = [sources[key] for key in source_keys]
    page.addView(obj)
    obj.ProjectionType = (
        "First angle"
        if properties["convention"] == "first_angle"
        else "Third angle"
    )
    obj.ScaleType = "Custom"
    obj.Scale = float(properties["scale"])
    obj.X = float(properties["x_mm"])
    obj.Y = float(properties["y_mm"])
    obj.spacingX = float(properties["spacing_x_mm"])
    obj.spacingY = float(properties["spacing_y_mm"])
    obj.AutoDistribute = True
    children = {}
    for direction in properties["directions"]:
        child = obj.addProjection(_PROJECTION_TYPES[str(direction)])
        if child is None:
            raise _fail(
                f"Native TechDraw did not create projection {direction!r}.",
                stage="native_projection",
                direction=direction,
            )
        _configure_view_style(child, properties)
        children[str(direction)] = child
    return {
        "object": obj,
        "children": children,
        "source_keys": source_keys,
    }


def _build_annotation(
    document: Any,
    definition: Mapping[str, Any],
    index: int,
    page: Any,
) -> dict[str, Any]:
    properties = dict(definition["properties"])
    obj = document.addObject(
        "TechDraw::DrawViewAnnotation", f"AnnotationCandidate{index:03d}"
    )
    page.addView(obj)
    obj.Text = [str(value) for value in definition["arguments"][0]]
    obj.X = float(properties["x_mm"])
    obj.Y = float(properties["y_mm"])
    obj.TextSize = float(properties["text_size_mm"])
    if "TextAlignment" not in set(obj.PropertiesList):
        obj.addProperty(
            "App::PropertyEnumeration",
            "TextAlignment",
            "Annotation",
            "Horizontal alignment of annotation text.",
        )
        obj.TextAlignment = ["Left", "Center", "Right"]
    obj.TextAlignment = str(properties["alignment"]).title()
    return {"object": obj}


def _build_dimension(
    document: Any,
    definition: Mapping[str, Any],
    index: int,
    page: Any,
    source: Any,
) -> dict[str, Any]:
    properties = dict(definition["properties"])
    kind = str(definition["arguments"][1])
    obj = document.addObject(
        "TechDraw::DrawViewDimension", f"DimensionCandidate{index:03d}"
    )
    obj.Type = _DIMENSION_TYPES[kind]
    obj.MeasureType = "True" if properties["measure"] == "true" else "Projected"
    obj.References2D = [
        (source, str(reference)) for reference in properties["references"]
    ]
    obj.X = float(properties["x_mm"])
    obj.Y = float(properties["y_mm"])
    if properties["format_spec"]:
        obj.FormatSpec = str(properties["format_spec"])
    obj.OverTolerance = float(properties["over_tolerance"])
    obj.UnderTolerance = float(properties["under_tolerance"])
    obj.ShowUnits = bool(properties["show_units"])
    page.addView(obj)
    return {"object": obj}


def _compact_projected_mapping(value: Any) -> dict[str, Any]:
    mapping = dict(value) if isinstance(value, Mapping) else {}
    candidates = []
    for raw in list(mapping.get("candidates") or [])[:8]:
        if not isinstance(raw, Mapping):
            continue
        object_name = str(raw.get("object_name") or "")
        subelement = str(raw.get("subelement") or "")
        if object_name and subelement:
            candidates.append(
                {"object_name": object_name, "subelement": subelement}
            )
    return {
        "status": str(mapping.get("status") or "unmapped"),
        "candidates": candidates,
        "candidates_truncated": len(list(mapping.get("candidates") or [])) > 8,
    }


def _compact_projected_point(value: Any) -> dict[str, float] | None:
    if not isinstance(value, Mapping) or set(value) != {"x", "y"}:
        return None
    try:
        x_value = float(value["x"])
        y_value = float(value["y"])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x_value) or not math.isfinite(y_value):
        return None
    return {"x": x_value, "y": y_value}


def _dimension_reference_inventory(
    projection_data: Mapping[str, Any],
    *,
    sample_limit: int = _MAX_MODEL_REFERENCE_SAMPLES,
) -> dict[str, Any]:
    """Produce a bounded, model-facing inventory from authenticated HLR readback."""

    limit = max(1, min(int(sample_limit), _MAX_MODEL_REFERENCE_SAMPLES))
    descriptors = dict(projection_data.get("descriptors") or {})
    raw_edges = [
        dict(item)
        for item in list(descriptors.get("edges") or [])
        if isinstance(item, Mapping)
    ]
    raw_vertices = [
        dict(item)
        for item in list(descriptors.get("vertices") or [])
        if isinstance(item, Mapping)
    ]

    def edge_priority(item: Mapping[str, Any]) -> tuple[int, int, int]:
        geometry = str(item.get("geometry_type") or "").lower()
        geometry_rank = 0 if any(mark in geometry for mark in ("circle", "arc")) else (
            1 if "line" in geometry else 2
        )
        mapping_rank = (
            0
            if str(dict(item.get("source_mapping") or {}).get("status") or "")
            == "exact"
            else 1
        )
        visible_rank = 0 if bool(item.get("visible")) else 1
        return geometry_rank, mapping_rank, visible_rank

    ranked_edges = sorted(raw_edges, key=edge_priority)
    ranked_vertices = sorted(
        raw_vertices,
        key=lambda item: (
            0
            if str(dict(item.get("source_mapping") or {}).get("status") or "")
            == "exact"
            else 1,
            0 if bool(item.get("visible")) else 1,
        ),
    )
    edge_samples = []
    for item in ranked_edges[:limit]:
        sample: dict[str, Any] = {
            "name": str(item.get("name") or ""),
            "geometry_type": str(item.get("geometry_type") or ""),
            "edge_class": str(item.get("edge_class") or ""),
            "visible": bool(item.get("visible")),
            "closed": bool(item.get("closed")),
            "source_mapping": _compact_projected_mapping(
                item.get("source_mapping")
            ),
        }
        length = item.get("length_view_mm")
        if isinstance(length, (int, float)) and not isinstance(length, bool):
            sample["length_view_mm"] = float(length)
        for field in ("start_2d", "end_2d", "midpoint_2d", "center_2d"):
            point = _compact_projected_point(item.get(field))
            if point is not None:
                sample[field] = point
        radius = item.get("radius_view_mm")
        if isinstance(radius, (int, float)) and not isinstance(radius, bool):
            sample["radius_view_mm"] = float(radius)
        edge_samples.append(sample)
    vertex_samples = []
    for item in ranked_vertices[:limit]:
        sample = {
            "name": str(item.get("name") or ""),
            "visible": bool(item.get("visible")),
            "is_center": bool(item.get("is_center")),
            "is_reference": bool(item.get("is_reference")),
            "source_mapping": _compact_projected_mapping(
                item.get("source_mapping")
            ),
        }
        point = _compact_projected_point(item.get("point_2d"))
        if point is not None:
            sample["point_2d"] = point
        vertex_samples.append(sample)
    face_count = int(projection_data.get("face_count") or 0)
    face_samples = [
        f"Face{index}"
        for index in range(min(face_count, limit))
    ]
    circular = [
        str(item.get("name") or "")
        for item in ranked_edges
        if any(
            marker in str(item.get("geometry_type") or "").lower()
            for marker in ("circle", "arc")
        )
    ][:limit]
    straight = [
        str(item.get("name") or "")
        for item in ranked_edges
        if "line" in str(item.get("geometry_type") or "").lower()
    ][:limit]
    return {
        "coordinate_space": str(descriptors.get("coordinate_space") or ""),
        "view_scale": float(descriptors.get("view_scale") or 0.0),
        "index_base": 0,
        "edge_count": len(raw_edges),
        "vertex_count": len(raw_vertices),
        "face_count": face_count,
        "edge_samples": edge_samples,
        "edge_samples_truncated": len(raw_edges) > len(edge_samples),
        "vertex_samples": vertex_samples,
        "vertex_samples_truncated": len(raw_vertices) > len(vertex_samples),
        "face_samples": face_samples,
        "face_samples_truncated": face_count > len(face_samples),
        "recommended_by_kind": {
            "distance": [str(item.get("name") or "") for item in ranked_edges][
                :limit
            ],
            "radius_or_diameter": circular,
            "angle": straight,
            "angle_3_point": [
                str(item.get("name") or "") for item in ranked_vertices
            ][:limit],
            "area": face_samples,
        },
        "sample_order": (
            "dimension utility first (circular, straight, other), then exact source "
            "mapping and visibility; names retain native EdgeN/VertexN identity"
        ),
    }


def _validate_dimension_references(
    kind: str,
    references: Sequence[str],
    projection_data: Mapping[str, Any],
    *,
    output_name: str,
) -> None:
    descriptors = dict(projection_data["descriptors"])
    edges = {
        str(item.get("name") or ""): dict(item)
        for item in list(descriptors.get("edges") or [])
        if isinstance(item, Mapping)
    }
    vertices = {
        str(item.get("name") or ""): dict(item)
        for item in list(descriptors.get("vertices") or [])
        if isinstance(item, Mapping)
    }
    faces = {f"Face{index}" for index in range(int(projection_data["face_count"]))}
    resolved = []
    for reference in references:
        if reference in edges:
            resolved.append(("edge", edges[reference]))
        elif reference in vertices:
            resolved.append(("vertex", vertices[reference]))
        elif reference in faces:
            resolved.append(("face", {"name": reference}))
        else:
            raise _fail(
                f"Dimension {output_name!r} selects unavailable projected element "
                f"{reference!r}.",
                stage="dimension_reference",
                output=output_name,
                reference=reference,
                available_edge_count=len(edges),
                available_vertex_count=len(vertices),
                available_face_count=len(faces),
                dimension_reference_inventory=_dimension_reference_inventory(
                    projection_data
                ),
            )
    kinds = [item[0] for item in resolved]
    valid = False
    if kind in {"distance", "distance_x", "distance_y"}:
        valid = kinds == ["edge"] or kinds == ["vertex", "vertex"]
    elif kind in {"radius", "diameter"}:
        geometry = str(resolved[0][1].get("geometry_type") or "").lower()
        valid = kinds == ["edge"] and any(
            marker in geometry for marker in ("circle", "arc")
        )
    elif kind == "angle":
        valid = kinds == ["edge", "edge"] and all(
            "line" in str(item[1].get("geometry_type") or "").lower()
            for item in resolved
        )
    elif kind == "angle_3_point":
        valid = kinds == ["vertex", "vertex", "vertex"]
    elif kind == "area":
        valid = kinds == ["face"]
    if not valid:
        raise _fail(
            f"Dimension {output_name!r} references do not satisfy {kind!r}.",
            stage="dimension_reference",
            output=output_name,
            dimension_kind=kind,
            resolved_types=kinds,
            resolved_references=list(references),
            dimension_reference_inventory=_dimension_reference_inventory(
                projection_data
            ),
        )


def _validate_and_build_techdraw(
    document: Any,
    raw_result: Mapping[str, Any],
    expected_outputs: list[dict[str, str]],
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build and fully evaluate one native TechDraw graph in FreeCADCmd."""

    definitions, keys, output_by_key, page_by_key = _validate_graph(
        raw_result, expected_outputs
    )
    expected_index = {
        str(expected["name"]): index for index, expected in enumerate(expected_outputs)
    }
    records: dict[str, dict[str, Any]] = {}
    sources = _source_objects(document)

    for expected in expected_outputs:
        name = str(expected["name"])
        if str(expected["type"]) != "template":
            continue
        records[name] = _build_template(
            document, definitions[name], expected_index[name]
        )
        records[name]["object"].Label = str(definitions[name]["properties"]["label"])

    for expected in expected_outputs:
        name = str(expected["name"])
        if str(expected["type"]) != "page":
            continue
        template_key = _definition_key(definitions[name]["arguments"][0])
        template_output = output_by_key[template_key][0]
        records[name] = _build_page(
            document,
            definitions[name],
            expected_index[name],
            records[template_output]["object"],
        )
        records[name]["object"].Label = str(definitions[name]["properties"]["label"])

    for expected in expected_outputs:
        name = str(expected["name"])
        output_type = str(expected["type"])
        if output_type not in {"view", "projection", "annotation"}:
            continue
        page = _page_for(keys[name], page_by_key, records)
        if output_type == "view":
            record = _build_view(
                document,
                definitions[name],
                expected_index[name],
                page,
                sources,
            )
        elif output_type == "projection":
            record = _build_projection(
                document,
                definitions[name],
                expected_index[name],
                page,
                sources,
            )
        else:
            record = _build_annotation(
                document,
                definitions[name],
                expected_index[name],
                page,
            )
        record["object"].Label = str(definitions[name]["properties"]["label"])
        records[name] = record

    if document.recompute() is False:
        raise _fail(
            "Native TechDraw failed while generating page projections.",
            stage="native_projection",
        )

    for expected in expected_outputs:
        name = str(expected["name"])
        output_type = str(expected["type"])
        index = expected_index[name]
        definition = definitions[name]
        properties = dict(definition["properties"])
        if output_type == "view":
            projection_data = _projection_snapshot(
                records[name]["object"],
                root,
                output_index=index,
                suffix="view",
                source_keys=records[name]["source_keys"],
            )
            records[name]["data"] = {
                **projection_data,
                "operation": "view",
                "orientation": str(properties["orientation"]),
                "hidden_lines": bool(properties["hidden_lines"]),
                "smooth_lines": bool(properties["smooth_lines"]),
            }
        elif output_type == "projection":
            child_data = {}
            for direction, child in records[name]["children"].items():
                child_data[direction] = _projection_snapshot(
                    child,
                    root,
                    output_index=index,
                    suffix=f"projection-{direction}",
                    source_keys=records[name]["source_keys"],
                )
            group = records[name]["object"]
            records[name]["data"] = {
                "schema": VALIDATION_SCHEMA,
                "operation": "projection",
                "native_type": str(group.TypeId),
                "convention": str(properties["convention"]),
                "position_mm": [float(group.X), float(group.Y)],
                "scale": float(group.Scale),
                "spacing_mm": [float(group.spacingX), float(group.spacingY)],
                "directions": list(properties["directions"]),
                "children": child_data,
                "source_identities": _source_identities(records[name]["source_keys"]),
            }
        elif output_type == "annotation":
            annotation = records[name]["object"]
            records[name]["data"] = {
                "schema": VALIDATION_SCHEMA,
                "operation": "annotation",
                "native_type": str(annotation.TypeId),
                "text": [str(value) for value in annotation.Text],
                "position_mm": [float(annotation.X), float(annotation.Y)],
                "text_size_mm": float(annotation.TextSize),
                "alignment": str(annotation.TextAlignment).lower(),
                "native_state": [str(value) for value in list(annotation.State or [])],
            }

    for expected in expected_outputs:
        name = str(expected["name"])
        if str(expected["type"]) != "dimension":
            continue
        definition = definitions[name]
        properties = dict(definition["properties"])
        source_key = _definition_key(definition["arguments"][0])
        source_name, source_type = output_by_key[source_key]
        source_record = records[source_name]
        if source_type == "projection":
            direction = str(properties["projection_direction"])
            source_object = source_record["children"][direction]
            projection_data = source_record["data"]["children"][direction]
        else:
            source_object = source_record["object"]
            projection_data = source_record["data"]
        _validate_dimension_references(
            str(definition["arguments"][1]),
            list(properties["references"]),
            projection_data,
            output_name=name,
        )
        record = _build_dimension(
            document,
            definition,
            expected_index[name],
            _page_for(keys[name], page_by_key, records),
            source_object,
        )
        record["object"].Label = str(properties["label"])
        record["source_output"] = source_name
        record["source_direction"] = str(properties["projection_direction"])
        records[name] = record

    if document.recompute() is False:
        raise _fail(
            "Native TechDraw failed while evaluating dimensions.",
            stage="native_dimension",
        )

    for expected in expected_outputs:
        name = str(expected["name"])
        if str(expected["type"]) != "dimension":
            continue
        dimension = records[name]["object"]
        state = {str(value) for value in list(dimension.State or [])}
        if {"Invalid", "Error"} & state:
            raise _fail(
                f"Native TechDraw dimension {name!r} is invalid: {sorted(state)!r}.",
                stage="native_dimension",
                output=name,
                native_state=sorted(state),
            )
        properties = dict(definitions[name]["properties"])
        data = _dimension_snapshot(dimension)
        data.update(
            {
                "operation": "dimension",
                "kind": str(definitions[name]["arguments"][1]),
                "measure": str(properties["measure"]),
                "source_output": records[name]["source_output"],
                "projection_direction": records[name]["source_direction"],
                "position_mm": [float(dimension.X), float(dimension.Y)],
                "format_spec": str(dimension.FormatSpec),
                "over_tolerance": float(dimension.OverTolerance),
                "under_tolerance": float(dimension.UnderTolerance),
                "show_units": bool(dimension.ShowUnits),
            }
        )
        records[name]["data"] = data

    for expected in expected_outputs:
        page_name = str(expected["name"])
        if str(expected["type"]) != "page":
            continue
        page = records[page_name]["object"]
        contents = list(definitions[page_name]["arguments"][1])
        content_names = [output_by_key[_definition_key(item)][0] for item in contents]
        for view in list(page.Views or []):
            page.removeView(view)
        for name in content_names:
            page.addPrecomputedView(records[name]["object"])
        native_members = [str(value.Name) for value in list(page.Views or [])]
        expected_members = [str(records[name]["object"].Name) for name in content_names]
        if native_members != expected_members:
            raise _fail(
                f"Native TechDraw page {page_name!r} changed its declared view graph.",
                stage="native_page_graph",
                output=page_name,
                expected_members=expected_members,
                native_members=native_members,
            )
        template_name = output_by_key[
            _definition_key(definitions[page_name]["arguments"][0])
        ][0]
        records[page_name]["data"] = {
            "schema": VALIDATION_SCHEMA,
            "operation": "page",
            "native_type": str(page.TypeId),
            "template_output": template_name,
            "content_outputs": content_names,
            "native_template": str(page.Template.Name),
            "native_members": native_members,
            "convention": str(definitions[page_name]["properties"]["convention"]),
            "scale": float(page.Scale),
            "keep_updated": bool(page.KeepUpdated),
        }

    # Reordering exact page membership invokes DrawPage::addView again, which
    # may center unowned views or select automatic scale. Restore the canonical
    # declarative state without another projection or dimension evaluation.
    for expected in expected_outputs:
        name = str(expected["name"])
        output_type = str(expected["type"])
        properties = dict(definitions[name]["properties"])
        obj = records[name]["object"]
        if output_type == "view":
            _configure_view_style(obj, properties)
        elif output_type == "projection":
            obj.ProjectionType = (
                "First angle"
                if properties["convention"] == "first_angle"
                else "Third angle"
            )
            obj.ScaleType = "Custom"
            obj.Scale = float(properties["scale"])
            obj.X = float(properties["x_mm"])
            obj.Y = float(properties["y_mm"])
            obj.spacingX = float(properties["spacing_x_mm"])
            obj.spacingY = float(properties["spacing_y_mm"])
            obj.AutoDistribute = False
        elif output_type == "annotation":
            obj.X = float(properties["x_mm"])
            obj.Y = float(properties["y_mm"])
            obj.TextSize = float(properties["text_size_mm"])
            obj.TextAlignment = str(properties["alignment"]).title()
        elif output_type == "dimension":
            obj.X = float(properties["x_mm"])
            obj.Y = float(properties["y_mm"])

    outputs = []
    summaries = []
    for expected in expected_outputs:
        name = str(expected["name"])
        output_type = str(expected["type"])
        data = dict(records[name]["data"])
        _encoded(data, limit=_MAX_NATIVE_READBACK_BYTES, label="native readback")
        outputs.append(
            {
                "name": name,
                "type": output_type,
                "definition": definitions[name],
                "techdraw_data": data,
            }
        )
        summaries.append(
            {
                "name": name,
                "type": output_type,
                "operation": str(definitions[name]["operation"]),
                "definition_sha256": keys[name],
                "native_type": str(records[name]["object"].TypeId),
            }
        )
    validation = {
        "schema": VALIDATION_SCHEMA,
        "output_count": len(outputs),
        "outputs": summaries,
    }
    _encoded(validation)
    return outputs, validation


def validate_and_build_techdraw(
    document: Any,
    raw_result: Mapping[str, Any],
    expected_outputs: list[dict[str, str]],
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build one native drawing graph and normalize unexpected native failures."""

    try:
        return _validate_and_build_techdraw(
            document,
            raw_result,
            expected_outputs,
            root,
        )
    except TechDrawCandidateError:
        raise
    except Exception as exc:
        raise _fail(
            f"Native TechDraw graph construction failed: {exc}",
            stage="native_graph",
            exception_type=type(exc).__name__,
            native_error=str(exc),
        ) from exc
