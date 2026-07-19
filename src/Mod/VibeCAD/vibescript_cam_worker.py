# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native worker implementation for CAM VibeScript candidates."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from vibescript_cam_api import CAMDomainAPI, _EXPORTS, _OUTPUT_TYPES
from vibescript_domain_api import DomainValue


VALIDATION_SCHEMA = "vibecad-vibescript-cam-validation-v2"
_OPERATION_OUTPUT = {
    "job": "job",
    "stock": "stock",
    "tool": "tool",
    "operation": "operation",
    "generate_toolpath": "toolpath",
    "postprocess": "toolpath",
}
_SHAPE_IDS = {
    "endmill": "endmill",
    "ballend": "ballend",
    "drill": "drill",
    "chamfer": "chamfer",
    "vbit": "v-bit",
}
_NATIVE_STRATEGY = {
    "profile": ("Path.Op.Profile", "ObjectProfile", "Create"),
    "pocket": ("Path.Op.PocketShape", "ObjectPocket", "Create"),
    "drilling": ("Path.Op.Drilling", "ObjectDrilling", "Create"),
    "face": ("Path.Op.MillFace", "ObjectFace", "Create"),
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
_FACE = re.compile(r"Face([1-9][0-9]*)\Z")
_MAX_DEFINITION_BYTES = 4 * 1024 * 1024
_MAX_NATIVE_READBACK_BYTES = 256 * 1024 * 1024
_MAX_REFERENCES = 128
_MAX_COMMANDS = 2_000_000
_MAX_COMMAND_PARAMETERS = 64
_MAX_COMMAND_ANNOTATIONS = 64
_MAX_GCODE_BYTES = 64 * 1024 * 1024
_MAX_GCODE_LINES = 5_000_000
_REFERENCES: Mapping[tuple[str, str], Mapping[str, Any]] = MappingProxyType({})


def _default_correction(details: Mapping[str, Any]) -> str:
    """Return one bounded source repair for every CAM candidate failure stage."""

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
            f"Rebuild only the malformed value{location} with the matching CAM api "
            "operation; never construct, copy, or mutate serialized definitions."
        )
    if stage == "source_validation":
        return (
            "Change only the named api argument while preserving units, exact returned "
            "graph values, stable references, and expected output declarations."
        )
    if stage == "reference_resolution":
        return (
            "Copy the exact current document_uid/object_name reference from CAM domain "
            "context; do not use a label, stale object, filesystem path, or another document."
        )
    if stage == "semantic_selection":
        return (
            "Choose one reported available FaceN on stable native topology, or copy one "
            "available published_interface name for regenerating VibeScript geometry."
        )
    if stage == "face_suitability":
        strategy = str(details.get("strategy") or "the operation")
        return (
            f"Change only {strategy!r} selection or machining setup: pocket faces must be "
            "planar and normal to job Z; drilling faces must identify an axial cylinder or "
            "concentric circular boundaries normal to job Z."
        )
    if stage in {"graph_membership", "output_identity"}:
        return (
            "Return every stock, tool, operation, final postprocessed toolpath, and job "
            "member once, then reuse those exact values in the same ordered graph."
        )
    if stage.startswith("native_tool"):
        return (
            "Correct only the reported tool kind/geometry, tool number, spindle setting, "
            "or feed. Keep the operation graph unchanged and retry the retained revision."
        )
    if stage.startswith("native_stock"):
        return (
            "Correct only the stock model references or six nonnegative bounding-box "
            "margins so the native FromBase stock is one valid positive-volume solid."
        )
    if stage in {
        "native_job_construction",
        "native_operation_construction",
        "native_type_contract",
        "native_property_contract",
        "native_property_assignment",
        "native_property_readback",
        "native_readback",
    }:
        return (
            "Correct only the reported job, stock, tool, strategy, depth, selection, or "
            "native property and preserve every exact returned graph identity."
        )
    if stage == "native_generation":
        return (
            "Use the reported native generation diagnostic to change only the operation's "
            "selection, start/final depth, step-down/step-over, side, boundary, peck, or tool."
        )
    if stage == "native_simulation":
        if details.get("collision"):
            return (
                "Inspect the reported collision commands/bounds and change only the unsafe "
                "tool geometry, selection, profile side, or cutting depths; keep "
                "require_collision_free=true and regenerate before postprocessing."
            )
        return (
            "Use the native simulation error to change only simulation_resolution_mm or the "
            "unsupported path/tool geometry; use any reported minimum resolution exactly."
        )
    if stage in {"toolpath_assembly", "toolpath_readback", "path_readback"}:
        return (
            "Repair only the named operation or tool binding that produced an empty, malformed, "
            "or changed native Path, then regenerate the same ordered operation sequence."
        )
    if stage == "native_postprocessing":
        return (
            "Keep the validated path unchanged and correct only the allowlisted processor, "
            "units, comments, or line-number option named by the native diagnostic."
        )
    if stage in {"artifact_export", "artifact_readback", "native_shape_validation"}:
        return (
            "Retry the retained revision after correcting only the reported invalid stock/tool "
            "shape or bounded project-artifact failure; never supply a filesystem path."
        )
    return (
        "Correct only the reported CAM reference, selector, tool, stock, operation setting, "
        "simulation setting, graph member, or postprocessor option and retry the failed "
        "working revision; do not recreate the program."
    )


class CAMCandidateError(RuntimeError):
    """A model-correctable CAM failure with structured diagnostics."""

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
            self.details["correction"] = correction or _default_correction(
                self.details
            )
        super().__init__(message)


def _fail(message: str, *, stage: str, **details: Any) -> CAMCandidateError:
    return CAMCandidateError(message, details={"stage": stage, **details})


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
            f"A CAM {label} is not bounded JSON: {exc}",
            stage="definition_contract",
            exception_type=type(exc).__name__,
        ) from exc
    if len(payload) > limit:
        raise _fail(
            f"A CAM {label} exceeds {limit} JSON bytes.",
            stage="definition_contract",
            json_bytes=len(payload),
        )
    return payload


def _definition_key(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _sha256_file(path: Path, *, stage: str = "reference_resolution") -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise _fail(
            f"A CAM artifact could not be authenticated: {exc}",
            stage=stage,
            exception_type=type(exc).__name__,
        ) from exc
    return digest.hexdigest()


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
    expected_output_type: str,
    *,
    context: str,
) -> DomainValue:
    return _inflate(
        validate_cam_definition(
            value,
            expected_output_type=expected_output_type,
            require_domain_value=False,
            context=context,
        )
    )


def validate_cam_definition(
    value: Any,
    *,
    expected_output_type: str | None = None,
    require_domain_value: bool = True,
    context: str = "definition",
) -> dict[str, Any]:
    """Replay one untrusted definition through the exact provider API."""

    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif not require_domain_value and isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise _fail(
            f"{context} must be returned by the active CAM api.",
            stage="definition_contract",
            path=context,
        )
    fields = {"domain", "operation", "output_type", "arguments", "properties"}
    if set(payload) != fields or payload.get("domain") != "cam":
        raise _fail(
            f"{context} has malformed CAM definition fields.",
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
    api = CAMDomainAPI(_EXPORTS, _OUTPUT_TYPES)
    try:
        if operation == "stock":
            required = {
                "x_negative_mm",
                "x_positive_mm",
                "y_negative_mm",
                "y_positive_mm",
                "z_negative_mm",
                "z_positive_mm",
                "label",
            }
            if len(arguments) != 1 or set(properties) != required:
                raise ValueError("stock fields are malformed")
            rebuilt = api.stock(arguments[0], **properties)
        elif operation == "tool":
            required = {
                "diameter_mm",
                "length_mm",
                "flutes",
                "tool_number",
                "spindle_rpm",
                "horizontal_feed_mm_per_min",
                "vertical_feed_mm_per_min",
                "cutting_edge_height_mm",
                "shank_diameter_mm",
                "tip_angle_deg",
                "cutting_edge_angle_deg",
                "tip_diameter_mm",
                "spindle_direction",
                "label",
            }
            if len(arguments) != 1 or set(properties) != required:
                raise ValueError("tool fields are malformed")
            rebuilt = api.tool(arguments[0], **properties)
        elif operation == "operation":
            required = {
                "start_depth_mm",
                "final_depth_mm",
                "step_down_mm",
                "step_over_percent",
                "side",
                "boundary",
                "peck_depth_mm",
                "coolant",
                "label",
            }
            if len(arguments) != 3 or set(properties) != required:
                raise ValueError("operation fields are malformed")
            tool = _nested_definition(
                arguments[1],
                "tool",
                context=f"{context}.arguments[1]",
            )
            rebuilt = api.operation(
                arguments[0],
                tool,
                selections=arguments[2],
                **properties,
            )
        elif operation == "generate_toolpath":
            required = {
                "simulation_resolution_mm",
                "require_collision_free",
                "label",
            }
            if len(arguments) != 2 or set(properties) != required:
                raise ValueError("generate_toolpath fields are malformed")
            stock = _nested_definition(
                arguments[0],
                "stock",
                context=f"{context}.arguments[0]",
            )
            if not isinstance(arguments[1], list):
                raise ValueError("generate_toolpath operations must be an array")
            operations = [
                _nested_definition(
                    item,
                    "operation",
                    context=f"{context}.arguments[1][{index}]",
                )
                for index, item in enumerate(arguments[1])
            ]
            rebuilt = api.generate_toolpath(stock, operations, **properties)
        elif operation == "postprocess":
            required = {"processor", "units", "comments", "line_numbers", "label"}
            if len(arguments) != 1 or set(properties) != required:
                raise ValueError("postprocess fields are malformed")
            toolpath = _nested_definition(
                arguments[0],
                "toolpath",
                context=f"{context}.arguments[0]",
            )
            rebuilt = api.postprocess(toolpath, **properties)
        else:
            required = {
                "geometry_tolerance_mm",
                "fixtures",
                "description",
                "label",
            }
            if len(arguments) != 5 or set(properties) != required:
                raise ValueError("job fields are malformed")
            stock = _nested_definition(
                arguments[1], "stock", context=f"{context}.arguments[1]"
            )
            if not isinstance(arguments[2], list) or not isinstance(arguments[3], list):
                raise ValueError("job tools/operations must be arrays")
            tools = [
                _nested_definition(
                    item, "tool", context=f"{context}.arguments[2][{index}]"
                )
                for index, item in enumerate(arguments[2])
            ]
            operations = [
                _nested_definition(
                    item,
                    "operation",
                    context=f"{context}.arguments[3][{index}]",
                )
                for index, item in enumerate(arguments[3])
            ]
            toolpath = _nested_definition(
                arguments[4], "toolpath", context=f"{context}.arguments[4]"
            )
            rebuilt = api.job(
                arguments[0],
                stock,
                tools,
                operations,
                toolpath,
                **properties,
            )
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


def _bounded_artifact_path(root: Path, relative: Any, *, context: str) -> Path:
    resolved_root = Path(root).resolve()
    if not isinstance(relative, str) or not relative:
        raise _fail(
            f"{context} has no artifact path.",
            stage="reference_resolution",
            path=context,
        )
    path = (resolved_root / relative).resolve()
    if resolved_root not in path.parents or not path.is_file() or path.is_symlink():
        raise _fail(
            f"{context} artifact is missing, symlinked, or outside staging.",
            stage="reference_resolution",
            path=context,
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _fail(
            f"{context} artifact could not be inspected: {exc}",
            stage="reference_resolution",
            path=context,
            exception_type=type(exc).__name__,
        ) from exc
    if not 1 <= size <= 256 * 1024 * 1024:
        raise _fail(
            f"{context} artifact has invalid size {size}.",
            stage="reference_resolution",
            path=context,
            artifact_bytes=size,
        )
    return path


def _reference_key(entry: Mapping[str, Any], *, context: str) -> tuple[str, str]:
    values = []
    for name in ("document_uid", "object_name"):
        raw = entry.get(name)
        if (
            not isinstance(raw, str)
            or not raw
            or raw != raw.strip()
            or len(raw) > 256
            or "\0" in raw
        ):
            raise _fail(
                f"{context}.{name} is invalid.",
                stage="reference_resolution",
                path=f"{context}.{name}",
            )
        values.append(raw)
    return values[0], values[1]


def configure_cam_references(
    root: Path,
    document_references: list[dict[str, Any]],
) -> None:
    """Authenticate and import exact detached BREP inputs for CAM."""

    if len(document_references) > _MAX_REFERENCES:
        raise _fail(
            f"CAM accepts at most {_MAX_REFERENCES} document references.",
            stage="reference_resolution",
            reference_count=len(document_references),
        )
    import Part

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
                path=context,
            )
        key = _reference_key(entry, context=context)
        if key in references:
            raise _fail(
                f"{context} duplicates document object {key[1]!r}.",
                stage="reference_resolution",
                path=context,
                object_name=key[1],
            )
        path = _bounded_artifact_path(root, entry.get("artifact_path"), context=context)
        digest = entry.get("brep_sha256")
        if not isinstance(digest, str) or _sha256_file(path) != digest:
            raise _fail(
                f"{context} SHA-256 does not match its descriptor.",
                stage="reference_resolution",
                path=context,
            )
        shape = Part.Shape()
        try:
            shape.importBrep(str(path))
        except Exception as exc:
            raise _fail(
                f"{context} BREP import failed: {exc}",
                stage="reference_resolution",
                path=context,
                exception_type=type(exc).__name__,
            ) from exc
        if shape.isNull() or not shape.isValid() or not list(shape.Solids):
            raise _fail(
                f"{context} must contain a valid solid-bearing BREP.",
                stage="reference_resolution",
                path=context,
            )
        expected_shape_type = str(entry.get("shape_type") or "")
        if expected_shape_type and str(shape.ShapeType) != expected_shape_type:
            raise _fail(
                f"{context} changed shape type during transfer.",
                stage="reference_resolution",
                path=context,
                expected_shape_type=expected_shape_type,
                observed_shape_type=str(shape.ShapeType),
            )
        interfaces = entry.get("published_interfaces") or {}
        if not isinstance(interfaces, Mapping) or len(interfaces) > 64:
            raise _fail(
                f"{context}.published_interfaces is invalid.",
                stage="reference_resolution",
                path=f"{context}.published_interfaces",
            )
        references[key] = MappingProxyType(
            {
                "shape": shape,
                "artifact_sha256": digest,
                "label": str(entry.get("label") or ""),
                "source_type_id": str(entry.get("type_id") or ""),
                "source_kind": str(entry.get("source_kind") or "shape"),
                "source_revision": str(entry.get("source_revision") or ""),
                "transient_topology": bool(entry.get("transient_topology")),
                "requires_semantic_interfaces": bool(
                    entry.get("requires_semantic_interfaces")
                ),
                "published_interfaces": {
                    str(name): dict(item)
                    for name, item in interfaces.items()
                    if isinstance(name, str) and isinstance(item, Mapping)
                },
            }
        )
    global _REFERENCES
    _REFERENCES = MappingProxyType(references)


def cam_publication_references() -> list[dict[str, Any]]:
    """Return detached authenticated input shapes for bounded live publication."""

    return [
        {
            "document_uid": key[0],
            "object_name": key[1],
            "label": str(source["label"]),
            "shape": source["shape"].copy(),
            "identity": _source_identity(key),
        }
        for key, source in _REFERENCES.items()
    ]


def _source_key(value: Any, *, context: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "document_uid",
        "object_name",
    }:
        raise _fail(
            f"{context} must contain exactly document_uid and object_name.",
            stage="reference_resolution",
            path=context,
        )
    key = _reference_key(value, context=context)
    if key not in _REFERENCES:
        raise _fail(
            f"{context} refers to unauthenticated object {key[1]!r}.",
            stage="reference_resolution",
            path=context,
            object_name=key[1],
        )
    return key


def _source_identity(key: tuple[str, str]) -> dict[str, Any]:
    source = _REFERENCES[key]
    return {
        "document_uid": key[0],
        "object_name": key[1],
        "artifact_sha256": str(source["artifact_sha256"]),
        "source_type_id": str(source["source_type_id"]),
        "source_kind": str(source["source_kind"]),
        "source_revision": str(source["source_revision"]),
    }


def _resolve_selection(
    key: tuple[str, str],
    selector: Mapping[str, Any],
    *,
    context: str,
) -> list[str]:
    source = _REFERENCES[key]
    kind = str(selector.get("type") or "")
    if kind == "subelement":
        if bool(source["transient_topology"]) or bool(
            source["requires_semantic_interfaces"]
        ):
            raise _fail(
                f"{context} cannot use an exact face on regenerating source "
                f"{key[1]!r}; use a published_interface selector.",
                stage="semantic_selection",
                path=context,
                object_name=key[1],
                available_interfaces=sorted(source["published_interfaces"]),
            )
        names = [str(selector.get("name") or "")]
    elif kind == "published_interface":
        interface_name = str(selector.get("interface_name") or "")
        interface = dict(source["published_interfaces"]).get(interface_name)
        if not isinstance(interface, Mapping):
            raise _fail(
                f"{context} interface {interface_name!r} is unavailable on {key[1]!r}.",
                stage="semantic_selection",
                path=context,
                available_interfaces=sorted(source["published_interfaces"]),
            )
        names = list(interface.get("subelements") or [])
    else:
        raise _fail(
            f"{context} has unsupported selector type {kind!r}.",
            stage="semantic_selection",
            path=context,
        )
    if not names or len(names) > 64:
        raise _fail(
            f"{context} must resolve to 1-64 faces.",
            stage="semantic_selection",
            path=context,
        )
    shape = source["shape"]
    result = []
    for index, raw in enumerate(names):
        name = str(raw or "")
        match = _FACE.fullmatch(name)
        if match is None or int(match.group(1)) > len(shape.Faces):
            available = [
                f"Face{face_index}"
                for face_index in range(1, min(len(shape.Faces), 128) + 1)
            ]
            raise _fail(
                f"{context}[{index}] does not resolve to an available FaceN.",
                stage="semantic_selection",
                path=f"{context}[{index}]",
                requested=name,
                face_count=len(shape.Faces),
                available_faces=available,
                available_faces_truncated=len(shape.Faces) > len(available),
            )
        result.append(name)
    if len(result) != len(set(result)):
        raise _fail(
            f"{context} resolves duplicate faces.",
            stage="semantic_selection",
            path=context,
        )
    return result


def path_to_records(path: Any) -> list[dict[str, Any]]:
    """Return the canonical bounded JSON representation of a native Path."""

    commands = list(getattr(path, "Commands", []) or [])
    if not 1 <= len(commands) <= _MAX_COMMANDS:
        raise _fail(
            f"A generated CAM path must contain 1-{_MAX_COMMANDS} commands; "
            f"received {len(commands)}.",
            stage="path_readback",
        )
    records = []
    for index, command in enumerate(commands):
        name = str(command.Name or "")
        if not name or len(name) > 256 or "\0" in name:
            raise _fail(
                f"CAM command {index} has an invalid name.",
                stage="path_readback",
                command_index=index,
            )
        raw_parameters = dict(command.Parameters or {})
        if len(raw_parameters) > _MAX_COMMAND_PARAMETERS:
            raise _fail(
                f"CAM command {index} has too many parameters.",
                stage="path_readback",
                command_index=index,
            )
        parameters = {}
        for key, value in raw_parameters.items():
            clean_key = str(key)
            if not clean_key or len(clean_key) > 32 or "\0" in clean_key:
                raise _fail(
                    f"CAM command {index} has an invalid parameter name.",
                    stage="path_readback",
                    command_index=index,
                )
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise _fail(
                    f"CAM command {index} parameter {clean_key!r} is not numeric.",
                    stage="path_readback",
                    command_index=index,
                )
            clean_value = float(value)
            if not math.isfinite(clean_value) or abs(clean_value) > 1.0e18:
                raise _fail(
                    f"CAM command {index} parameter {clean_key!r} is not bounded.",
                    stage="path_readback",
                    command_index=index,
                )
            parameters[clean_key] = clean_value
        raw_annotations = dict(command.Annotations or {})
        if len(raw_annotations) > _MAX_COMMAND_ANNOTATIONS:
            raise _fail(
                f"CAM command {index} has too many annotations.",
                stage="path_readback",
                command_index=index,
            )
        annotations: dict[str, str | float] = {}
        for key, value in raw_annotations.items():
            clean_key = str(key)
            if not clean_key or len(clean_key) > 128 or "\0" in clean_key:
                raise _fail(
                    f"CAM command {index} has an invalid annotation name.",
                    stage="path_readback",
                    command_index=index,
                )
            if isinstance(value, str):
                if len(value) > 4096 or "\0" in value:
                    raise _fail(
                        f"CAM command {index} annotation {clean_key!r} is too large.",
                        stage="path_readback",
                        command_index=index,
                    )
                annotations[clean_key] = value
            elif not isinstance(value, bool) and isinstance(value, (int, float)):
                clean_value = float(value)
                if not math.isfinite(clean_value) or abs(clean_value) > 1.0e18:
                    raise _fail(
                        f"CAM command {index} annotation {clean_key!r} is not bounded.",
                        stage="path_readback",
                        command_index=index,
                    )
                annotations[clean_key] = clean_value
            else:
                raise _fail(
                    f"CAM command {index} annotation {clean_key!r} is unsupported.",
                    stage="path_readback",
                    command_index=index,
                )
        records.append(
            {"name": name, "parameters": parameters, "annotations": annotations}
        )
    _encoded(records, limit=_MAX_NATIVE_READBACK_BYTES, label="path command readback")
    return records


# Compatibility for existing internal and downstream imports.  There is one
# implementation; new host-side code uses the public descriptive name.
_path_records = path_to_records


def path_from_records(records: Any) -> Any:
    """Reconstruct one bounded native Path value from authenticated records."""

    if not isinstance(records, list):
        raise ValueError("CAM path commands must be an array.")
    import Path as PathModule

    commands = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping) or set(record) != {
            "name",
            "parameters",
            "annotations",
        }:
            raise ValueError(f"CAM path command {index} is malformed.")
        name = record.get("name")
        parameters = record.get("parameters")
        annotations = record.get("annotations")
        if not isinstance(name, str) or not isinstance(parameters, Mapping) or not isinstance(
            annotations, Mapping
        ):
            raise ValueError(f"CAM path command {index} has malformed values.")
        command = PathModule.Command(name, dict(parameters))
        command.Annotations = dict(annotations)
        commands.append(command)
    rebuilt = PathModule.Path(commands)
    if _path_records(rebuilt) != [dict(item) for item in records]:
        raise ValueError("CAM path changed during native command reconstruction.")
    return rebuilt


def _path_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    command_types: dict[str, int] = {}
    cutting = 0
    for item in records:
        name = str(item["name"])
        command_types[name] = command_types.get(name, 0) + 1
        if name.upper().lstrip("0") in {"G1", "G2", "G3"} or name.upper() in {
            "G01",
            "G02",
            "G03",
            "G73",
            "G81",
            "G82",
            "G83",
            "G85",
        }:
            cutting += 1
    return {
        "command_count": len(records),
        "cutting_command_count": cutting,
        "command_types": dict(sorted(command_types.items())),
    }


def _finite_quantity(value: Any, *, context: str) -> float:
    raw = getattr(value, "Value", value)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise _fail(
            f"{context} is not a native numeric quantity.",
            stage="native_readback",
            path=context,
        )
    clean = float(raw)
    if not math.isfinite(clean) or abs(clean) > 1.0e18:
        raise _fail(
            f"{context} is not finite and bounded.",
            stage="native_readback",
            path=context,
        )
    return clean


def _quantity_in(value: Any, unit: str, *, context: str) -> float:
    converter = getattr(value, "getValueAs", None)
    if not callable(converter):
        raise _fail(
            f"{context} is not a native quantity convertible to {unit!r}.",
            stage="native_readback",
            path=context,
        )
    try:
        converted = converter(unit)
    except Exception as exc:
        raise _fail(
            f"{context} could not be converted to {unit!r}: {exc}",
            stage="native_readback",
            path=context,
            exception_type=type(exc).__name__,
        ) from exc
    return _finite_quantity(converted, context=context)


def _close_number(left: Any, right: Any, *, tolerance: float = 1.0e-9) -> bool:
    return math.isclose(
        _finite_quantity(left, context="native value"),
        _finite_quantity(right, context="requested value"),
        rel_tol=tolerance,
        abs_tol=tolerance,
    )


def _assert_native_values_match(left: Any, right: Any, *, path: str) -> None:
    """Compare native JSON readback with exact structure and tight float tolerance."""

    if left is None or right is None or isinstance(left, (str, bool)) or isinstance(
        right, (str, bool)
    ):
        if type(left) is not type(right) or left != right:
            raise _fail(
                f"Native CAM value changed at {path}.",
                stage="artifact_readback",
                path=path,
                before=left,
                after=right,
            )
        return
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if isinstance(left, int) and isinstance(right, int):
            matches = left == right
        else:
            first = float(left)
            second = float(right)
            matches = (
                math.isfinite(first)
                and math.isfinite(second)
                and math.isclose(first, second, rel_tol=1.0e-10, abs_tol=1.0e-9)
            )
        if not matches:
            raise _fail(
                f"Native CAM numeric value changed at {path}.",
                stage="artifact_readback",
                path=path,
                before=left,
                after=right,
            )
        return
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if set(left) != set(right):
            raise _fail(
                f"Native CAM object fields changed at {path}.",
                stage="artifact_readback",
                path=path,
                before=sorted(str(item) for item in left),
                after=sorted(str(item) for item in right),
            )
        for name in left:
            _assert_native_values_match(
                left[name],
                right[name],
                path=f"{path}.{name}",
            )
        return
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            raise _fail(
                f"Native CAM array length changed at {path}.",
                stage="artifact_readback",
                path=path,
                before=len(left),
                after=len(right),
            )
        for index, (first, second) in enumerate(zip(left, right, strict=True)):
            _assert_native_values_match(
                first,
                second,
                path=f"{path}[{index}]",
            )
        return
    raise _fail(
        f"Native CAM value type changed at {path}.",
        stage="artifact_readback",
        path=path,
        before_type=type(left).__name__,
        after_type=type(right).__name__,
    )


def _clear_expression(obj: Any, name: str) -> None:
    if name not in list(getattr(obj, "PropertiesList", []) or []):
        raise _fail(
            f"Native CAM object {obj.Name!r} lacks required property {name!r}.",
            stage="native_property_contract",
            object_name=str(obj.Name),
            property_name=name,
        )
    try:
        obj.setExpression(name, None)
    except Exception as exc:
        raise _fail(
            f"Could not clear native expression {obj.Name}.{name}: {exc}",
            stage="native_property_assignment",
            object_name=str(obj.Name),
            property_name=name,
            exception_type=type(exc).__name__,
        ) from exc
    expressions = {
        str(path): str(expression)
        for path, expression in list(getattr(obj, "ExpressionEngine", []) or [])
    }
    if name in expressions:
        raise _fail(
            f"Native expression {obj.Name}.{name} remained active.",
            stage="native_property_readback",
            object_name=str(obj.Name),
            property_name=name,
        )


def _proxy_identity(obj: Any) -> dict[str, str]:
    proxy = getattr(obj, "Proxy", None)
    if proxy is None:
        raise _fail(
            f"Native CAM object {obj.Name!r} has no Python proxy.",
            stage="native_type_contract",
            object_name=str(obj.Name),
        )
    return {
        "native_type": str(obj.TypeId),
        "proxy_module": str(proxy.__class__.__module__),
        "proxy_class": str(proxy.__class__.__name__),
    }


def _shape_artifact(
    shape: Any,
    root: Path,
    *,
    index: int,
    role: str,
) -> dict[str, Any]:
    """Export and read back one exact worker-built BREP artifact."""

    from vibescript_part_worker import part_shape_facts
    import Part

    if (
        shape is None
        or shape.isNull()
        or not shape.isValid()
        or len(list(shape.Solids)) != 1
    ):
        raise _fail(
            f"Native CAM {role} must be exactly one valid solid.",
            stage="native_shape_validation",
            role=role,
        )
    output_directory = (Path(root) / "outputs").resolve()
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _fail(
            f"Native CAM {role} artifact directory creation failed: {exc}",
            stage="artifact_export",
            role=role,
            exception_type=type(exc).__name__,
        ) from exc
    path = output_directory / f"cam_{index:03d}_{role}.brep"
    try:
        shape.exportBrep(str(path))
    except Exception as exc:
        raise _fail(
            f"Native CAM {role} BREP export failed: {exc}",
            stage="artifact_export",
            role=role,
            exception_type=type(exc).__name__,
        ) from exc
    if path.is_symlink() or not path.is_file():
        raise _fail(
            f"Native CAM {role} BREP was not written.",
            stage="artifact_export",
            role=role,
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _fail(
            f"Native CAM {role} BREP size readback failed: {exc}",
            stage="artifact_readback",
            role=role,
            exception_type=type(exc).__name__,
        ) from exc
    if not 1 <= size <= 256 * 1024 * 1024:
        raise _fail(
            f"Native CAM {role} BREP has invalid size {size}.",
            stage="artifact_export",
            role=role,
            artifact_bytes=size,
        )
    restored = Part.Shape()
    try:
        restored.importBrep(str(path))
    except Exception as exc:
        raise _fail(
            f"Native CAM {role} BREP readback failed: {exc}",
            stage="artifact_readback",
            role=role,
            exception_type=type(exc).__name__,
        ) from exc
    before = part_shape_facts(shape, max_subelements=64)
    after = part_shape_facts(restored, max_subelements=64)
    # ``Part.Shape`` imported from BREP does not expose CenterOfMass through
    # every generic wrapper even though its volume, bounds, topology, and all
    # face/edge geometry are present.  Compare every transferable kernel fact;
    # bounds_center_mm remains an independently derived center check.
    comparable_before = dict(before)
    comparable_after = dict(after)
    comparable_before.pop("center_of_mass_mm", None)
    comparable_after.pop("center_of_mass_mm", None)
    _assert_native_values_match(
        comparable_before,
        comparable_after,
        path=f"{role}.facts",
    )
    return {
        "artifact_kind": "brep",
        "artifact_path": str(path.relative_to(Path(root).resolve())),
        "artifact_sha256": _sha256_file(path, stage="artifact_readback"),
        "artifact_bytes": size,
        "facts": after,
    }


def _build_source_objects(document: Any) -> dict[tuple[str, str], Any]:
    source_objects: dict[tuple[str, str], Any] = {}
    for index, (key, source) in enumerate(_REFERENCES.items()):
        obj = document.addObject("Part::Feature", f"CAMSource{index:03d}")
        obj.Label = str(source.get("label") or key[1])
        obj.Shape = source["shape"].copy()
        obj.purgeTouched()
        source_objects[key] = obj
    if not source_objects:
        raise _fail(
            "A CAM program requires at least one authenticated model reference.",
            stage="reference_resolution",
        )
    return source_objects


def _job_clone_map(
    job: Any,
    model_keys: Sequence[tuple[str, str]],
    source_objects: Mapping[tuple[str, str], Any],
) -> dict[tuple[str, str], Any]:
    clones = list(getattr(getattr(job, "Model", None), "Group", []) or [])
    if len(clones) != len(model_keys):
        raise _fail(
            "Native Path Job did not create exactly one model resource per input.",
            stage="native_job_construction",
            expected_models=len(model_keys),
            observed_models=len(clones),
        )
    by_source: dict[int, Any] = {}
    for clone in clones:
        sources = list(getattr(clone, "Objects", []) or [])
        if len(sources) == 1:
            by_source[id(sources[0])] = clone
    result: dict[tuple[str, str], Any] = {}
    for index, key in enumerate(model_keys):
        source = source_objects[key]
        clone = by_source.get(id(source), clones[index])
        if clone in result.values():
            raise _fail(
                "Native Path Job model resource mapping is ambiguous.",
                stage="native_job_construction",
                object_name=key[1],
            )
        shape = getattr(clone, "Shape", None)
        if shape is None or shape.isNull() or not shape.isValid():
            raise _fail(
                f"Native Path Job clone for {key[1]!r} has no valid shape.",
                stage="native_job_construction",
                object_name=key[1],
            )
        result[key] = clone
    return result


def _build_job_and_stock(
    document: Any,
    job_definition: Mapping[str, Any],
    stock_definition: Mapping[str, Any],
    source_objects: Mapping[tuple[str, str], Any],
) -> tuple[Any, Any, dict[tuple[str, str], Any], dict[str, Any]]:
    import Path.Main.Job as PathJob
    import Path.Main.Stock as PathStock

    model_keys = [
        _source_key(item, context=f"job.models[{index}]")
        for index, item in enumerate(job_definition["arguments"][0])
    ]
    try:
        job = PathJob.Create(
            "CAMJob",
            [source_objects[key] for key in model_keys],
            createDefaultToolController=False,
            createDefaultStock=False,
        )
    except Exception as exc:
        raise _fail(
            f"Native Path Job construction failed: {exc}",
            stage="native_job_construction",
            exception_type=type(exc).__name__,
        ) from exc
    if str(job.TypeId) != "Path::FeaturePython":
        raise _fail(
            "Native Path Job factory returned the wrong document type.",
            stage="native_type_contract",
            observed_type=str(job.TypeId),
        )
    clone_map = _job_clone_map(job, model_keys, source_objects)
    job_properties = dict(job_definition["properties"])
    job.Label = str(job_properties["label"] or "CAM Job")
    job.GeometryTolerance = f"{float(job_properties['geometry_tolerance_mm']):.17g} mm"
    job.Fixtures = list(job_properties["fixtures"])
    job.Description = str(job_properties["description"])
    job.SplitOutput = False

    stock_properties = dict(stock_definition["properties"])
    try:
        stock = PathStock.CreateFromBase(job)
        job.Stock = stock
        assignments = {
            "ExtXneg": stock_properties["x_negative_mm"],
            "ExtXpos": stock_properties["x_positive_mm"],
            "ExtYneg": stock_properties["y_negative_mm"],
            "ExtYpos": stock_properties["y_positive_mm"],
            "ExtZneg": stock_properties["z_negative_mm"],
            "ExtZpos": stock_properties["z_positive_mm"],
        }
        for name, value in assignments.items():
            setattr(stock, name, f"{float(value):.17g} mm")
        stock.Label = str(stock_properties["label"] or "Stock")
        document.recompute()
    except Exception as exc:
        raise _fail(
            f"Native FromBase stock construction failed: {exc}",
            stage="native_stock_construction",
            exception_type=type(exc).__name__,
        ) from exc
    if str(stock.TypeId) != "Part::FeaturePython":
        raise _fail(
            "Native FromBase stock factory returned the wrong document type.",
            stage="native_type_contract",
            observed_type=str(stock.TypeId),
        )
    for name, requested in assignments.items():
        if not _close_number(getattr(stock, name), requested):
            raise _fail(
                f"Native stock property {name} changed during assignment.",
                stage="native_property_readback",
                property_name=name,
            )
    if (
        stock.Shape.isNull()
        or not stock.Shape.isValid()
        or len(list(stock.Shape.Solids)) != 1
        or float(stock.Shape.Volume) <= 0.0
    ):
        raise _fail(
            "Native FromBase stock is not exactly one valid positive-volume solid.",
            stage="native_stock_validation",
        )
    if getattr(stock, "Base", None) is not job.Model:
        raise _fail(
            "Native FromBase stock is not linked to the Path Job model group.",
            stage="native_stock_validation",
        )
    observed_job = {
        **_proxy_identity(job),
        "label": str(job.Label),
        "geometry_tolerance_mm": _finite_quantity(
            job.GeometryTolerance, context="job.GeometryTolerance"
        ),
        "fixtures": [str(item) for item in list(job.Fixtures)],
        "description": str(job.Description),
        "model_references": [_source_identity(key) for key in model_keys],
        "model_count": len(model_keys),
        "operations_group_type": str(job.Operations.TypeId),
        "setup_sheet_type": str(job.SetupSheet.TypeId),
        "model_group_type": str(job.Model.TypeId),
        "tools_group_type": str(job.Tools.TypeId),
        "stock_output": "",
        "tool_outputs": [],
        "operation_outputs": [],
        "toolpath_output": "",
    }
    if not _close_number(
        observed_job["geometry_tolerance_mm"],
        job_properties["geometry_tolerance_mm"],
    ) or observed_job["fixtures"] != list(job_properties["fixtures"]):
        raise _fail(
            "Native Path Job properties changed during assignment.",
            stage="native_property_readback",
        )
    return job, stock, clone_map, observed_job


def _tool_geometry_assignments(definition: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(definition["arguments"][0])
    properties = dict(definition["properties"])
    assignments: dict[str, Any] = {
        "Diameter": f"{float(properties['diameter_mm']):.17g} mm",
        "Length": f"{float(properties['length_mm']):.17g} mm",
        "Flutes": int(properties["flutes"]),
        "SpindleDirection": (
            "Forward" if properties["spindle_direction"] == "forward" else "Reverse"
        ),
    }
    optional = {
        "CuttingEdgeHeight": properties["cutting_edge_height_mm"],
        "ShankDiameter": properties["shank_diameter_mm"],
        "TipAngle": properties["tip_angle_deg"],
        "CuttingEdgeAngle": properties["cutting_edge_angle_deg"],
        "TipDiameter": properties["tip_diameter_mm"],
    }
    for name, value in optional.items():
        if value is not None:
            unit = "deg" if name.endswith("Angle") else "mm"
            assignments[name] = f"{float(value):.17g} {unit}"
    expected_names = {
        "endmill": {"Diameter", "Length", "Flutes", "SpindleDirection", "CuttingEdgeHeight", "ShankDiameter"},
        "ballend": {"Diameter", "Length", "Flutes", "SpindleDirection", "CuttingEdgeHeight", "ShankDiameter"},
        "drill": {"Diameter", "Length", "Flutes", "SpindleDirection", "TipAngle"},
        "chamfer": {"Diameter", "Length", "Flutes", "SpindleDirection", "CuttingEdgeHeight", "ShankDiameter", "CuttingEdgeAngle", "TipDiameter"},
        "vbit": {"Diameter", "Length", "Flutes", "SpindleDirection", "CuttingEdgeHeight", "ShankDiameter", "CuttingEdgeAngle", "TipDiameter"},
    }[kind]
    if set(assignments) != expected_names:
        raise _fail(
            f"CAM {kind} tool geometry did not normalize to its exact native property set.",
            stage="native_tool_contract",
        )
    return assignments


def _build_tool(
    document: Any,
    job: Any,
    definition: Mapping[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    from Path.Tool.toolbit import ToolBit
    import Path.Tool.Controller as PathToolController

    kind = str(definition["arguments"][0])
    properties = dict(definition["properties"])
    controller_label = str(
        properties["label"] or f"Tool {properties['tool_number']}"
    )
    bit_label = (
        f"{properties['label']} Tool Bit"
        if properties["label"]
        else f"{kind.title()} Tool Bit"
    )
    try:
        tool_bit = ToolBit.from_shape_id(f"{_SHAPE_IDS[kind]}.fcstd")
        bit = tool_bit.attach_to_doc(doc=document)
        for name, value in _tool_geometry_assignments(definition).items():
            setattr(bit, name, value)
        bit.Label = bit_label
        document.recompute()
        controller = PathToolController.Create(
            name=controller_label,
            tool=bit,
            toolNumber=int(properties["tool_number"]),
            assignViewProvider=False,
            assignTool=True,
        )
        controller.Label = controller_label
        controller.SpindleSpeed = float(properties["spindle_rpm"])
        controller.SpindleDir = (
            "Forward" if properties["spindle_direction"] == "forward" else "Reverse"
        )
        controller.HorizFeed = (
            f"{float(properties['horizontal_feed_mm_per_min']):.17g} mm/min"
        )
        controller.VertFeed = (
            f"{float(properties['vertical_feed_mm_per_min']):.17g} mm/min"
        )
        job.Proxy.addToolController(controller)
        document.recompute()
    except Exception as exc:
        raise _fail(
            f"Native {kind} tool construction failed: {exc}",
            stage="native_tool_construction",
            tool_kind=kind,
            exception_type=type(exc).__name__,
        ) from exc
    expected_proxy = {
        "endmill": "ToolBitEndmill",
        "ballend": "ToolBitBallend",
        "drill": "ToolBitDrill",
        "chamfer": "ToolBitChamfer",
        "vbit": "ToolBitVBit",
    }[kind]
    bit_identity = _proxy_identity(bit)
    controller_identity = _proxy_identity(controller)
    if (
        str(bit.TypeId) != "Part::FeaturePython"
        or bit_identity["proxy_class"] != expected_proxy
        or str(controller.TypeId) != "Path::FeaturePython"
        or controller_identity["proxy_class"] != "ToolController"
        or getattr(controller, "Tool", None) is not bit
        or controller not in list(job.Tools.Group)
        or str(controller.Label) != controller_label
        or str(bit.Label) != bit_label
    ):
        raise _fail(
            f"Native {kind} tool/controller graph has the wrong exact types or links.",
            stage="native_type_contract",
            tool_kind=kind,
        )
    geometry = {}
    for name, requested in _tool_geometry_assignments(definition).items():
        observed = getattr(bit, name)
        if isinstance(requested, int):
            matches = type(observed) is int and observed == requested
            geometry[name] = int(observed)
        elif name == "SpindleDirection":
            matches = str(observed) == str(requested)
            geometry[name] = str(observed)
        else:
            requested_number = float(str(requested).split()[0])
            matches = _close_number(observed, requested_number)
            geometry[name] = _finite_quantity(observed, context=f"tool.{name}")
        if not matches:
            raise _fail(
                f"Native tool property {name} changed during assignment.",
                stage="native_property_readback",
                tool_kind=kind,
                property_name=name,
            )
    controller_data = {
        "tool_number": int(controller.ToolNumber),
        "spindle_rpm": _finite_quantity(
            controller.SpindleSpeed, context="controller.SpindleSpeed"
        ),
        "spindle_direction": str(controller.SpindleDir),
        "horizontal_feed_mm_per_min": _finite_quantity(
            controller.HorizFeed.getValueAs("mm/min"),
            context="controller.HorizFeed",
        ),
        "vertical_feed_mm_per_min": _finite_quantity(
            controller.VertFeed.getValueAs("mm/min"),
            context="controller.VertFeed",
        ),
    }
    expected_controller = {
        "tool_number": int(properties["tool_number"]),
        "spindle_rpm": float(properties["spindle_rpm"]),
        "spindle_direction": (
            "Forward" if properties["spindle_direction"] == "forward" else "Reverse"
        ),
        "horizontal_feed_mm_per_min": float(
            properties["horizontal_feed_mm_per_min"]
        ),
        "vertical_feed_mm_per_min": float(properties["vertical_feed_mm_per_min"]),
    }
    for name, expected in expected_controller.items():
        observed = controller_data[name]
        matches = observed == expected if isinstance(expected, str) else _close_number(observed, expected)
        if not matches:
            raise _fail(
                f"Native tool controller property {name} changed during assignment.",
                stage="native_property_readback",
                tool_kind=kind,
                property_name=name,
            )
    if (
        bit.Shape.isNull()
        or not bit.Shape.isValid()
        or len(list(bit.Shape.Solids)) != 1
        or float(bit.Shape.Volume) <= 0.0
    ):
        raise _fail(
            f"Native {kind} tool bit is not exactly one valid positive-volume solid.",
            stage="native_tool_validation",
            tool_kind=kind,
        )
    return {
        "controller": controller,
        "bit": bit,
        "data": {
            **controller_identity,
            "tool_bit_native_type": bit_identity["native_type"],
            "tool_bit_proxy_module": bit_identity["proxy_module"],
            "tool_bit_proxy_class": bit_identity["proxy_class"],
            "kind": kind,
            "label": str(controller.Label),
            "tool_bit_label": str(bit.Label),
            "shape_id": str(bit.ShapeID),
            "shape_type": str(bit.ShapeType),
            "geometry": geometry,
            "controller": controller_data,
        },
    }


def _canonical_graph(
    result: Mapping[str, Any],
    expected_outputs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    expected_names = [
        str(item.get("name") or "")
        for item in expected_outputs
        if isinstance(item, Mapping)
    ]
    if len(expected_names) != len(expected_outputs) or list(result) != expected_names:
        raise _fail(
            "CAM result keys must exactly match expected_outputs in declared order.",
            stage="result_contract",
            expected=expected_names,
            received=list(result),
        )
    definitions: dict[str, dict[str, Any]] = {}
    keys: dict[str, str] = {}
    by_key: dict[str, tuple[str, str]] = {}
    counts = {output_type: 0 for output_type in _OUTPUT_TYPES}
    for index, expected in enumerate(expected_outputs):
        if not isinstance(expected, Mapping) or set(expected) != {"name", "type"}:
            raise _fail(
                f"expected_outputs[{index}] must contain exactly name and type.",
                stage="result_contract",
            )
        name = expected.get("name")
        output_type = expected.get("type")
        if (
            not isinstance(name, str)
            or not name
            or name not in result
            or output_type not in _OUTPUT_TYPES
        ):
            raise _fail(
                f"expected_outputs[{index}] is not a declared CAM output.",
                stage="result_contract",
            )
        definition = validate_cam_definition(
            result[name],
            expected_output_type=str(output_type),
            context=f"result.{name}",
        )
        key = _definition_key(definition)
        if key in by_key:
            raise _fail(
                f"Outputs {by_key[key][0]!r} and {name!r} return duplicate CAM definitions.",
                stage="output_identity",
                output=name,
            )
        definitions[name] = definition
        keys[name] = key
        by_key[key] = (name, str(output_type))
        counts[str(output_type)] += 1
    for output_type in ("job", "stock", "toolpath"):
        if counts[output_type] != 1:
            raise _fail(
                f"A CAM program must return exactly one {output_type} output; "
                f"received {counts[output_type]}.",
                stage="graph_membership",
            )
    for output_type in ("tool", "operation"):
        if counts[output_type] < 1:
            raise _fail(
                f"A CAM program must return at least one {output_type} output.",
                stage="graph_membership",
            )

    def one(output_type: str) -> str:
        return next(
            name for name, definition in definitions.items()
            if definition["output_type"] == output_type
        )

    def member(
        raw_definition: Any,
        expected_type: str,
        *,
        context: str,
    ) -> str:
        if not isinstance(raw_definition, Mapping):
            raise _fail(
                f"{context} is not a CAM definition.",
                stage="graph_membership",
                path=context,
            )
        key = _definition_key(dict(raw_definition))
        found = by_key.get(key)
        if found is None or found[1] != expected_type:
            raise _fail(
                f"{context} must reference a returned {expected_type} output.",
                stage="graph_membership",
                path=context,
            )
        return found[0]

    job_name = one("job")
    stock_name = one("stock")
    toolpath_name = one("toolpath")
    job_definition = definitions[job_name]
    stock_definition = definitions[stock_name]
    toolpath_definition = definitions[toolpath_name]
    if job_definition["operation"] != "job":
        raise _fail(
            "The job output must be returned by api.job.",
            stage="graph_membership",
            output=job_name,
        )
    if stock_definition["operation"] != "stock":
        raise _fail(
            "The stock output must be returned by api.stock.",
            stage="graph_membership",
            output=stock_name,
        )
    if toolpath_definition["operation"] != "postprocess":
        raise _fail(
            "The toolpath output must be returned by api.postprocess.",
            stage="graph_membership",
            output=toolpath_name,
        )
    linked_stock = member(
        job_definition["arguments"][1],
        "stock",
        context=f"result.{job_name}.stock",
    )
    if linked_stock != stock_name:
        raise _fail(
            "The returned job and stock outputs do not form one exact graph.",
            stage="graph_membership",
        )
    linked_tools = [
        member(item, "tool", context=f"result.{job_name}.tools[{index}]")
        for index, item in enumerate(job_definition["arguments"][2])
    ]
    linked_operations = [
        member(item, "operation", context=f"result.{job_name}.operations[{index}]")
        for index, item in enumerate(job_definition["arguments"][3])
    ]
    linked_toolpath = member(
        job_definition["arguments"][4],
        "toolpath",
        context=f"result.{job_name}.toolpath",
    )
    if linked_toolpath != toolpath_name:
        raise _fail(
            "The returned job and toolpath outputs do not form one exact graph.",
            stage="graph_membership",
        )
    returned_tools = {
        name for name, definition in definitions.items()
        if definition["output_type"] == "tool"
    }
    returned_operations = {
        name for name, definition in definitions.items()
        if definition["output_type"] == "operation"
    }
    if set(linked_tools) != returned_tools or len(linked_tools) != len(returned_tools):
        raise _fail(
            "api.job.tools must contain every returned tool exactly once.",
            stage="graph_membership",
        )
    if (
        set(linked_operations) != returned_operations
        or len(linked_operations) != len(returned_operations)
    ):
        raise _fail(
            "api.job.operations must contain every returned operation exactly once.",
            stage="graph_membership",
        )
    generated_definition = toolpath_definition["arguments"][0]
    if (
        not isinstance(generated_definition, Mapping)
        or generated_definition.get("operation") != "generate_toolpath"
    ):
        raise _fail(
            "api.postprocess must directly wrap one api.generate_toolpath definition.",
            stage="graph_membership",
        )
    generated_stock = member(
        generated_definition["arguments"][0],
        "stock",
        context=f"result.{toolpath_name}.generated.stock",
    )
    generated_operations = [
        member(
            item,
            "operation",
            context=f"result.{toolpath_name}.generated.operations[{index}]",
        )
        for index, item in enumerate(generated_definition["arguments"][1])
    ]
    if generated_stock != stock_name or generated_operations != linked_operations:
        raise _fail(
            "The generated toolpath must use the returned stock and exact ordered job operations.",
            stage="graph_membership",
        )
    model_references = list(job_definition["arguments"][0])
    model_keys = {
        _source_key(item, context=f"result.{job_name}.models[{index}]")
        for index, item in enumerate(model_references)
    }
    for operation_name in linked_operations:
        operation_definition = definitions[operation_name]
        tool_name = member(
            operation_definition["arguments"][1],
            "tool",
            context=f"result.{operation_name}.tool",
        )
        if tool_name not in returned_tools:
            raise _fail(
                f"Operation {operation_name!r} uses a tool outside its job.",
                stage="graph_membership",
            )
        for index, selection in enumerate(operation_definition["arguments"][2]):
            key = _source_key(
                selection["target"],
                context=f"result.{operation_name}.selections[{index}].target",
            )
            if key not in model_keys:
                raise _fail(
                    f"Operation {operation_name!r} selects an object outside its job models.",
                    stage="graph_membership",
                    object_name=key[1],
                )
    return {
        "definitions": definitions,
        "keys": keys,
        "by_key": by_key,
        "job_name": job_name,
        "stock_name": stock_name,
        "tool_names": linked_tools,
        "operation_names": linked_operations,
        "toolpath_name": toolpath_name,
        "generated_definition": dict(generated_definition),
    }


def _point(value: Any) -> list[float]:
    return [float(value.x), float(value.y), float(value.z)]


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(
        sum((float(left[index]) - float(right[index])) ** 2 for index in range(3))
    )


def _face_descriptor(
    key: tuple[str, str],
    face_name: str,
    face: Any,
) -> dict[str, Any]:
    surface = getattr(face, "Surface", None)
    surface_type = type(surface).__name__ if surface is not None else ""
    descriptor: dict[str, Any] = {
        "source": _source_identity(key),
        "face": face_name,
        "surface_type": surface_type,
        "area_mm2": float(face.Area),
        "edge_count": len(list(face.Edges)),
        "wire_count": len(list(face.Wires)),
    }
    if surface_type == "Plane":
        u_min, u_max, v_min, v_max = (float(item) for item in face.ParameterRange)
        descriptor["normal"] = _point(
            face.normalAt((u_min + u_max) / 2.0, (v_min + v_max) / 2.0)
        )
    if surface_type == "Cylinder":
        descriptor["cylinder"] = {
            "radius_mm": float(surface.Radius),
            "axis": _point(surface.Axis),
            "center": _point(surface.Center),
        }
    circles = []
    for edge_index, edge in enumerate(face.Edges, start=1):
        try:
            curve = edge.Curve
        except (AttributeError, RuntimeError, TypeError):
            continue
        if type(curve).__name__ == "Circle":
            circles.append(
                {
                    "edge_index": edge_index,
                    "radius_mm": float(curve.Radius),
                    "center": _point(curve.Center),
                    "axis": _point(curve.Axis),
                }
            )
    descriptor["circular_edges"] = circles
    return descriptor


def _validate_face_suitability(
    strategy: str,
    descriptor: Mapping[str, Any],
    *,
    context: str,
) -> None:
    if strategy == "pocket":
        normal = list(descriptor.get("normal") or [0.0, 0.0, 0.0])
        if descriptor.get("surface_type") != "Plane" or abs(float(normal[2])) < (
            1.0 - 1.0e-7
        ):
            raise _fail(
                f"{context} must be a planar face normal to machining Z for pocketing.",
                stage="face_suitability",
                path=context,
                strategy=strategy,
                surface_type=str(descriptor.get("surface_type") or ""),
                normal=normal,
            )
    if strategy != "drilling":
        return
    if descriptor.get("surface_type") == "Cylinder":
        axis = list(dict(descriptor.get("cylinder") or {}).get("axis") or [])
        if len(axis) == 3 and abs(float(axis[2])) >= 1.0 - 1.0e-7:
            return
    circles = list(descriptor.get("circular_edges") or [])
    if circles:
        center = list(circles[0]["center"])
        concentric = all(
            _distance(center, list(circle["center"])) <= 1.0e-7
            for circle in circles[1:]
        )
        axial = all(
            abs(float(list(circle["axis"])[2])) >= 1.0 - 1.0e-7
            for circle in circles
        )
        if concentric and axial:
            return
    raise _fail(
        f"{context} must be an axial cylindrical face or have concentric circular "
        "boundaries normal to machining Z for drilling.",
        stage="face_suitability",
        path=context,
        strategy=strategy,
        surface_type=str(descriptor.get("surface_type") or ""),
        circular_edges=list(descriptor.get("circular_edges") or [])[:16],
    )


def _validated_operation_selections(
    definition: Mapping[str, Any],
    *,
    output_name: str,
) -> tuple[dict[tuple[str, str], list[str]], list[dict[str, Any]]]:
    """Resolve and validate exact operation faces without constructing live objects."""

    grouped: dict[tuple[str, str], list[str]] = {}
    descriptors = []
    strategy = str(definition["arguments"][0])
    for index, entry in enumerate(definition["arguments"][2]):
        context = f"result.{output_name}.selections[{index}]"
        key = _source_key(entry["target"], context=f"{context}.target")
        names = _resolve_selection(key, entry["selection"], context=f"{context}.selection")
        for name in names:
            face = _REFERENCES[key]["shape"].getElement(name)
            descriptor = _face_descriptor(key, name, face)
            _validate_face_suitability(strategy, descriptor, context=f"{key[1]}.{name}")
            descriptor["selector"] = dict(entry["selection"])
            descriptors.append(descriptor)
        grouped.setdefault(key, []).extend(names)
    for key, names in grouped.items():
        if len(names) != len(set(names)):
            raise _fail(
                f"Operation {output_name!r} resolves duplicate faces on {key[1]!r}.",
                stage="semantic_selection",
            )
    return grouped, descriptors


def _resolved_operation_base(
    definition: Mapping[str, Any],
    clone_map: Mapping[tuple[str, str], Any],
    *,
    output_name: str,
) -> tuple[list[tuple[Any, list[str]]], list[dict[str, Any]]]:
    grouped, descriptors = _validated_operation_selections(
        definition,
        output_name=output_name,
    )
    result = [(clone_map[key], names) for key, names in grouped.items()]
    return result, descriptors


def _native_generation_diagnostics(operation: Any) -> dict[str, Any]:
    getter = getattr(getattr(operation, "Proxy", None), "getGenerationDiagnostics", None)
    if not callable(getter):
        raise _fail(
            f"Native CAM operation {operation.Name!r} has no generation diagnostics.",
            stage="native_generation",
        )
    value = getter(operation)
    try:
        encoded = _encoded(
            value,
            limit=_MAX_NATIVE_READBACK_BYTES,
            label="generation diagnostics",
        )
        result = json.loads(encoded.decode("utf-8"))
    except CAMCandidateError:
        raise
    except Exception as exc:
        raise _fail(
            f"Native CAM generation diagnostics are malformed: {exc}",
            stage="native_generation",
            exception_type=type(exc).__name__,
        ) from exc
    if result.get("status") != "succeeded":
        raise _fail(
            f"Native CAM operation {operation.Name!r} did not generate successfully.",
            stage="native_generation",
            native_error=result.get("error"),
            diagnostics=result,
        )
    return result


def _build_operation(
    document: Any,
    job: Any,
    definition: Mapping[str, Any],
    clone_map: Mapping[tuple[str, str], Any],
    tool_record: Mapping[str, Any],
    *,
    index: int,
    output_name: str,
    simulation_resolution_mm: float,
    require_collision_free: bool,
) -> dict[str, Any]:
    strategy = str(definition["arguments"][0])
    properties = dict(definition["properties"])
    module_name, proxy_class, _factory_name = _NATIVE_STRATEGY[strategy]
    try:
        if strategy == "profile":
            import Path.Op.Profile as NativeOperation
        elif strategy == "pocket":
            import Path.Op.PocketShape as NativeOperation
        elif strategy == "drilling":
            import Path.Op.Drilling as NativeOperation
        else:
            import Path.Op.MillFace as NativeOperation
        operation = NativeOperation.Create(
            f"CAM{strategy.title()}{index:03d}",
            parentJob=job,
        )
    except Exception as exc:
        raise _fail(
            f"Native {strategy} operation construction failed: {exc}",
            stage="native_operation_construction",
            strategy=strategy,
            exception_type=type(exc).__name__,
        ) from exc
    base, selection_data = _resolved_operation_base(
        definition,
        clone_map,
        output_name=output_name,
    )
    try:
        operation.Base = base
        operation.ToolController = tool_record["controller"]
        for name in ("StartDepth", "FinalDepth"):
            _clear_expression(operation, name)
        operation.StartDepth = f"{float(properties['start_depth_mm']):.17g} mm"
        operation.FinalDepth = f"{float(properties['final_depth_mm']):.17g} mm"
        if strategy in {"profile", "pocket", "face"}:
            _clear_expression(operation, "StepDown")
            operation.StepDown = f"{float(properties['step_down_mm']):.17g} mm"
        if strategy in {"pocket", "face"}:
            operation.StepOver = int(properties["step_over_percent"])
        if strategy == "profile":
            operation.Side = (
                "Outside" if properties["side"] == "outside" else "Inside"
            )
        if strategy == "face":
            operation.BoundaryShape = {
                "boundbox": "Boundbox",
                "stock": "Stock",
                "perimeter": "Perimeter",
            }[str(properties["boundary"])]
        if strategy == "drilling":
            _clear_expression(operation, "PeckDepth")
            operation.PeckEnabled = float(properties["peck_depth_mm"]) > 0.0
            operation.PeckDepth = f"{float(properties['peck_depth_mm']):.17g} mm"
            operation.Strategy = "Drilling"
        operation.CoolantMode = {
            "none": "None",
            "flood": "Flood",
            "mist": "Mist",
        }[str(properties["coolant"])]
        operation.Label = str(properties["label"] or f"{strategy.title()} Operation")
        document.recompute()
    except CAMCandidateError:
        raise
    except Exception as exc:
        raise _fail(
            f"Native {strategy} property assignment or generation failed: {exc}",
            stage="native_generation",
            strategy=strategy,
            exception_type=type(exc).__name__,
        ) from exc
    identity = _proxy_identity(operation)
    if (
        str(operation.TypeId) != "Path::FeaturePython"
        or identity["proxy_module"] != module_name
        or identity["proxy_class"] != proxy_class
        or operation not in list(job.Operations.Group)
        or operation.ToolController is not tool_record["controller"]
    ):
        raise _fail(
            f"Native {strategy} operation has the wrong exact type, proxy, or job links.",
            stage="native_type_contract",
            strategy=strategy,
        )
    actual_base = [
        (linked.Name, [str(name) for name in list(names)])
        for linked, names in list(operation.Base or [])
    ]
    expected_base = [
        (linked.Name, [str(name) for name in names]) for linked, names in base
    ]
    if actual_base != expected_base:
        raise _fail(
            f"Native {strategy} operation changed its exact base selections.",
            stage="native_property_readback",
            strategy=strategy,
        )
    requested_readback: dict[str, Any] = {
        "start_depth_mm": float(properties["start_depth_mm"]),
        "final_depth_mm": float(properties["final_depth_mm"]),
        "coolant": str(operation.CoolantMode),
    }
    numeric_properties = {
        "start_depth_mm": "StartDepth",
        "final_depth_mm": "FinalDepth",
    }
    if strategy in {"profile", "pocket", "face"}:
        numeric_properties["step_down_mm"] = "StepDown"
        requested_readback["step_down_mm"] = float(properties["step_down_mm"])
    if strategy == "drilling":
        numeric_properties["peck_depth_mm"] = "PeckDepth"
        requested_readback["peck_depth_mm"] = float(properties["peck_depth_mm"])
        requested_readback["peck_enabled"] = bool(operation.PeckEnabled)
    for key, property_name in numeric_properties.items():
        observed = _finite_quantity(
            getattr(operation, property_name),
            context=f"operation.{property_name}",
        )
        if not _close_number(observed, properties[key]):
            raise _fail(
                f"Native {strategy} property {property_name} changed during generation.",
                stage="native_property_readback",
                strategy=strategy,
                property_name=property_name,
            )
        requested_readback[key] = observed
    if strategy in {"pocket", "face"}:
        requested_readback["step_over_percent"] = int(operation.StepOver)
        if requested_readback["step_over_percent"] != int(
            properties["step_over_percent"]
        ):
            raise _fail(
                f"Native {strategy} StepOver changed during generation.",
                stage="native_property_readback",
                strategy=strategy,
            )
    if strategy == "profile":
        requested_readback["side"] = str(operation.Side)
    if strategy == "face":
        requested_readback["boundary"] = str(operation.BoundaryShape)
    generation = _native_generation_diagnostics(operation)
    records = _path_records(operation.Path)
    summary = _path_summary(records)
    if summary["cutting_command_count"] <= 0:
        raise _fail(
            f"Native {strategy} operation generated no cutting commands.",
            stage="native_generation",
            strategy=strategy,
        )
    try:
        import Path.Main.Simulation as NativeSimulation

        simulation = NativeSimulation.analyze_operation(
            job,
            operation,
            simulation_resolution_mm=float(simulation_resolution_mm),
        )
        simulation = json.loads(
            _encoded(
                simulation,
                limit=_MAX_NATIVE_READBACK_BYTES,
                label="CAM simulation readback",
            ).decode("utf-8")
        )
    except CAMCandidateError:
        raise
    except Exception as exc:
        raise _fail(
            f"Native {strategy} simulation failed: {exc}",
            stage="native_simulation",
            strategy=strategy,
            exception_type=type(exc).__name__,
        ) from exc
    if simulation.get("complete") is not True:
        raise _fail(
            f"Native {strategy} simulation did not complete.",
            stage="native_simulation",
            strategy=strategy,
            native_error=simulation.get("error"),
        )
    collision = dict(simulation.get("collision") or {})
    if require_collision_free and collision.get("protected_model_collision") is not False:
        raise _fail(
            f"Native {strategy} simulation detected protected-model collision.",
            stage="native_simulation",
            strategy=strategy,
            collision=collision,
        )
    return {
        "object": operation,
        "data": {
            **identity,
            "strategy": strategy,
            "label": str(operation.Label),
            "tool_output": "",
            "selections": selection_data,
            "properties": requested_readback,
            "path_commands": records,
            "path_summary": summary,
            "generation": generation,
            "simulation": simulation,
        },
    }


def _combined_native_path(
    document: Any,
    job: Any,
    operation_records: Sequence[Mapping[str, Any]],
) -> tuple[Any, Any, list[dict[str, Any]]]:
    import Path as PathModule

    commands = []
    active_controller = None
    for record in operation_records:
        operation = record["object"]
        controller = getattr(operation, "ToolController", None)
        if controller is None:
            raise _fail(
                f"Native operation {operation.Name!r} lost its tool controller.",
                stage="toolpath_assembly",
            )
        if controller is not active_controller:
            controller_path = getattr(controller, "Path", None)
            controller_commands = list(
                getattr(controller_path, "Commands", []) or []
            )
            if not controller_commands:
                try:
                    controller.Proxy.execute(controller)
                except Exception as exc:
                    raise _fail(
                        f"Native tool-change generation failed: {exc}",
                        stage="toolpath_assembly",
                        exception_type=type(exc).__name__,
                    ) from exc
                controller_commands = list(
                    getattr(getattr(controller, "Path", None), "Commands", []) or []
                )
            if not controller_commands:
                raise _fail(
                    "A native tool controller generated no tool-change commands.",
                    stage="toolpath_assembly",
                )
            commands.extend(controller_commands)
            active_controller = controller
        operation_commands = list(
            getattr(getattr(operation, "Path", None), "Commands", []) or []
        )
        if not operation_commands:
            raise _fail(
                f"Native operation {operation.Name!r} lost its generated path.",
                stage="toolpath_assembly",
            )
        commands.extend(operation_commands)
    combined = PathModule.Path(commands)
    records = _path_records(combined)
    if _path_summary(records)["cutting_command_count"] <= 0:
        raise _fail(
            "The combined native CAM path contains no cutting commands.",
            stage="toolpath_assembly",
        )
    job.Path = combined
    toolpath = document.addObject("Path::Feature", "CAMToolpath")
    toolpath.Path = path_from_records(records)
    toolpath.Label = "Validated CAM Toolpath"
    toolpath.purgeTouched()
    if _path_records(job.Path) != records or _path_records(toolpath.Path) != records:
        raise _fail(
            "The combined native CAM path changed during document assignment.",
            stage="toolpath_readback",
        )
    return combined, toolpath, records


def _postprocess_artifact(
    job: Any,
    definition: Mapping[str, Any],
    root: Path,
    *,
    index: int,
) -> dict[str, Any]:
    from Path.Post.Processor import PostProcessorFactory

    properties = dict(definition["properties"])
    processor_name = str(properties["processor"])
    arguments = [
        "--no-show-editor",
        "--no-header",
        "--metric" if properties["units"] == "metric" else "--inches",
        "--comments" if properties["comments"] else "--no-comments",
        "--line-numbers"
        if properties["line_numbers"]
        else "--no-line-numbers",
    ]
    try:
        available = [
            str(item) for item in list(job.getEnumerationsOfProperty("PostProcessor"))
        ]
        job.PostProcessorArgs = " ".join(arguments)
        # Machine-aware postprocessors are intentionally omitted from a
        # machine-less Job's GUI enumeration even though their native class is
        # available and supports deterministic default configuration.  The
        # VibeScript contract already supplies the exact allowlist and options,
        # so resolve that canonical implementation directly.  Keep the native
        # Job property in sync only when its own enumeration accepts the value.
        if processor_name in available:
            job.PostProcessor = processor_name
        processor = PostProcessorFactory.get_post_processor(job, processor_name)
        expected_class = {"grbl": "Grbl", "linuxcnc": "Linuxcnc"}[
            processor_name
        ]
        if (
            processor is None
            or processor.__class__.__module__ != f"{processor_name}_post"
            or processor.__class__.__name__ != expected_class
        ):
            raise RuntimeError(
                "native postprocessor factory returned an unexpected implementation"
            )
        sections = processor.export()
    except Exception as exc:
        raise _fail(
            f"Native {processor_name} postprocessing failed: {exc}",
            stage="native_postprocessing",
            processor=processor_name,
            exception_type=type(exc).__name__,
        ) from exc
    if not isinstance(sections, list) or not sections:
        raise _fail(
            f"Native {processor_name} postprocessor returned no sections.",
            stage="native_postprocessing",
            processor=processor_name,
        )
    text_parts = []
    section_data = []
    for section_index, section in enumerate(sections):
        if not isinstance(section, tuple) or len(section) != 2:
            raise _fail(
                f"Native postprocessor section {section_index} is malformed.",
                stage="native_postprocessing",
                processor=processor_name,
            )
        name, code = section
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 1024
            or "\0" in name
            or not isinstance(code, str)
            or not code
            or "\0" in code
        ):
            raise _fail(
                f"Native postprocessor section {section_index} has invalid text.",
                stage="native_postprocessing",
                processor=processor_name,
            )
        encoded = code.encode("utf-8")
        section_data.append(
            {
                "name": name,
                "bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
        text_parts.append(code if code.endswith("\n") else f"{code}\n")
    gcode = "".join(text_parts)
    payload = gcode.encode("utf-8")
    line_count = len(gcode.splitlines())
    if not 1 <= len(payload) <= _MAX_GCODE_BYTES:
        raise _fail(
            f"Native postprocessed G-code has invalid size {len(payload)}.",
            stage="native_postprocessing",
            processor=processor_name,
        )
    if not 1 <= line_count <= _MAX_GCODE_LINES:
        raise _fail(
            f"Native postprocessed G-code has invalid line count {line_count}.",
            stage="native_postprocessing",
            processor=processor_name,
        )
    output_directory = (Path(root) / "outputs").resolve()
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _fail(
            f"CAM G-code artifact directory creation failed: {exc}",
            stage="artifact_export",
            processor=processor_name,
            exception_type=type(exc).__name__,
        ) from exc
    path = output_directory / f"cam_{index:03d}_toolpath.nc"
    try:
        path.write_bytes(payload)
        observed_payload = path.read_bytes()
    except OSError as exc:
        raise _fail(
            f"CAM G-code artifact write/readback failed: {exc}",
            stage="artifact_readback",
            processor=processor_name,
            exception_type=type(exc).__name__,
        ) from exc
    if path.is_symlink() or not path.is_file() or observed_payload != payload:
        raise _fail(
            "Postprocessed CAM artifact changed during worker write/readback.",
            stage="artifact_readback",
            processor=processor_name,
        )
    return {
        "artifact_kind": "gcode",
        "artifact_path": str(path.relative_to(Path(root).resolve())),
        "artifact_sha256": _sha256_file(path, stage="artifact_readback"),
        "artifact_bytes": len(payload),
        "line_count": line_count,
        "processor": processor_name,
        "processor_module": str(processor.__class__.__module__),
        "processor_class": str(processor.__class__.__name__),
        "units": str(properties["units"]),
        "comments": bool(properties["comments"]),
        "line_numbers": bool(properties["line_numbers"]),
        "arguments": arguments,
        "sections": section_data,
        "machine_configured": False,
        "machine_name": "",
        "machine_limits_checked": False,
        "configuration_scope": "generic_postprocessor_defaults",
    }


def validate_and_build_cam(
    document: Any,
    result: Mapping[str, Any],
    expected_outputs: Sequence[Mapping[str, Any]],
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build, simulate, postprocess, and serialize one exact native CAM graph."""

    graph = _canonical_graph(result, expected_outputs)
    definitions: dict[str, dict[str, Any]] = graph["definitions"]
    keys: dict[str, str] = graph["keys"]
    by_key: dict[str, tuple[str, str]] = graph["by_key"]
    source_objects = _build_source_objects(document)
    job_definition = definitions[graph["job_name"]]
    stock_definition = definitions[graph["stock_name"]]
    job, stock, clone_map, job_data = _build_job_and_stock(
        document,
        job_definition,
        stock_definition,
        source_objects,
    )

    tool_records: dict[str, dict[str, Any]] = {}
    for index, output_name in enumerate(graph["tool_names"]):
        record = _build_tool(
            document,
            job,
            definitions[output_name],
            index=index,
        )
        tool_records[output_name] = record

    generated_properties = dict(graph["generated_definition"]["properties"])
    simulation_resolution = float(generated_properties["simulation_resolution_mm"])
    require_collision_free = bool(generated_properties["require_collision_free"])
    operation_records: dict[str, dict[str, Any]] = {}
    for index, output_name in enumerate(graph["operation_names"]):
        definition = definitions[output_name]
        tool_key = _definition_key(dict(definition["arguments"][1]))
        tool_member = by_key.get(tool_key)
        if tool_member is None or tool_member[1] != "tool":
            raise _fail(
                f"Operation {output_name!r} lost its returned tool binding.",
                stage="graph_membership",
            )
        tool_name = tool_member[0]
        record = _build_operation(
            document,
            job,
            definition,
            clone_map,
            tool_records[tool_name],
            index=index,
            output_name=output_name,
            simulation_resolution_mm=simulation_resolution,
            require_collision_free=require_collision_free,
        )
        record["data"]["tool_output"] = tool_name
        operation_records[output_name] = record

    _combined, toolpath_object, toolpath_records = _combined_native_path(
        document,
        job,
        [operation_records[name] for name in graph["operation_names"]],
    )
    toolpath_definition = definitions[graph["toolpath_name"]]
    toolpath_index = next(
        index
        for index, expected in enumerate(expected_outputs)
        if expected["name"] == graph["toolpath_name"]
    )
    gcode_artifact = _postprocess_artifact(
        job,
        toolpath_definition,
        root,
        index=toolpath_index,
    )
    toolpath_summary = _path_summary(toolpath_records)
    simulation_summaries = [
        {
            "operation_output": name,
            "complete": bool(
                operation_records[name]["data"]["simulation"]["complete"]
            ),
            "protected_model_collision": bool(
                operation_records[name]["data"]["simulation"]["collision"][
                    "protected_model_collision"
                ]
            ),
        }
        for name in graph["operation_names"]
    ]
    collision_free = all(
        item["protected_model_collision"] is False for item in simulation_summaries
    )
    job_data.update(
        {
            "stock_output": graph["stock_name"],
            "tool_outputs": list(graph["tool_names"]),
            "operation_outputs": list(graph["operation_names"]),
            "toolpath_output": graph["toolpath_name"],
            "combined_path_summary": toolpath_summary,
        }
    )
    stock_properties = dict(stock_definition["properties"])
    stock_data = {
        **_proxy_identity(stock),
        "label": str(stock.Label),
        "model_references": [
            _source_identity(
                _source_key(item, context=f"stock.models[{index}]")
            )
            for index, item in enumerate(stock_definition["arguments"][0])
        ],
        "margins_mm": {
            "x_negative": float(stock_properties["x_negative_mm"]),
            "x_positive": float(stock_properties["x_positive_mm"]),
            "y_negative": float(stock_properties["y_negative_mm"]),
            "y_positive": float(stock_properties["y_positive_mm"]),
            "z_negative": float(stock_properties["z_negative_mm"]),
            "z_positive": float(stock_properties["z_positive_mm"]),
        },
        "job_output": graph["job_name"],
    }
    toolpath_data = {
        "native_type": str(toolpath_object.TypeId),
        "label": str(toolpath_object.Label),
        "job_output": graph["job_name"],
        "stock_output": graph["stock_name"],
        "operation_outputs": list(graph["operation_names"]),
        "path_commands": toolpath_records,
        "path_summary": toolpath_summary,
        "simulation_resolution_mm": simulation_resolution,
        "require_collision_free": require_collision_free,
        "collision_free": collision_free,
        "simulations": simulation_summaries,
        "postprocess": gcode_artifact,
    }

    outputs = []
    summaries = []
    for index, expected in enumerate(expected_outputs):
        name = str(expected["name"])
        output_type = str(expected["type"])
        if output_type == "job":
            data = dict(job_data)
            artifact = {}
        elif output_type == "stock":
            data = dict(stock_data)
            artifact = _shape_artifact(
                stock.Shape,
                root,
                index=index,
                role="stock",
            )
        elif output_type == "tool":
            record = tool_records[name]
            data = dict(record["data"])
            data["job_output"] = graph["job_name"]
            artifact = _shape_artifact(
                record["bit"].Shape,
                root,
                index=index,
                role="tool",
            )
        elif output_type == "operation":
            data = dict(operation_records[name]["data"])
            data["job_output"] = graph["job_name"]
            artifact = {}
        else:
            data = dict(toolpath_data)
            artifact = {}
        _encoded(data, limit=_MAX_NATIVE_READBACK_BYTES, label="CAM native readback")
        item = {
            "name": name,
            "type": output_type,
            "definition": definitions[name],
            "cam_data": data,
            **artifact,
        }
        outputs.append(item)
        summaries.append(
            {
                "name": name,
                "type": output_type,
                "operation": str(definitions[name]["operation"]),
                "definition_sha256": keys[name],
                "native_state_sha256": hashlib.sha256(
                    _encoded(
                        data,
                        limit=_MAX_NATIVE_READBACK_BYTES,
                        label="CAM native readback",
                    )
                ).hexdigest(),
                "artifact_sha256": str(artifact.get("artifact_sha256") or ""),
                "native_type": str(data["native_type"]),
                "status": "validated",
            }
        )
    validation = {
        "schema": VALIDATION_SCHEMA,
        "output_count": len(outputs),
        "outputs": summaries,
        "job_output": graph["job_name"],
        "stock_output": graph["stock_name"],
        "toolpath_output": graph["toolpath_name"],
        "tool_count": len(graph["tool_names"]),
        "operation_count": len(graph["operation_names"]),
        "collision_free": collision_free,
        "postprocessor": str(gcode_artifact["processor"]),
    }
    _encoded(validation)
    return outputs, validation
