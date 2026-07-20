# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native Draft evaluator for production VibeScript programs."""

from __future__ import annotations

from collections.abc import Mapping
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

from vibescript_domain_api import DomainValue


_GRAPH_ID = re.compile(r"^d[1-9][0-9]*$")
_SHAPE_TYPES = frozenset({"wire", "circle", "rectangle", "bspline", "array"})
_OPERATIONS = frozenset({*_SHAPE_TYPES, "text"})
_DRAFT_REFERENCES: Mapping[tuple[str, str], Mapping[str, Any]] = MappingProxyType({})
_GRAPH_CORRECTION = (
    "Return the direct value produced by the active Draft api; do not construct, "
    "copy, or edit serialized graph dictionaries."
)


class DraftCandidateError(RuntimeError):
    """A model-facing Draft failure with structured corrective details."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.details = dict(details or {})
        super().__init__(message)


def configure_draft_references(root: Path, entries: list[dict[str, Any]]) -> None:
    """Authenticate detached source BREPs and retain their bounded metadata."""

    from vibescript_part_worker import configure_part_references

    configure_part_references(root, entries)
    references: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise DraftCandidateError(
                f"document_references[{index}] must be an object.",
                details={
                    "stage": "reference_authentication",
                    "index": index,
                    "correction": (
                        "Copy one exact stable reference from the injected Draft domain context."
                    ),
                },
            )
        key = (
            str(raw.get("document_uid") or ""),
            str(raw.get("object_name") or ""),
        )
        if not all(key) or key in references:
            raise DraftCandidateError(
                f"document_references[{index}] has missing or duplicate identity.",
                details={
                    "stage": "reference_authentication",
                    "index": index,
                    "document_uid": key[0],
                    "object_name": key[1],
                    "correction": (
                        "Use one unique document_uid/object_name pair copied exactly from "
                        "the injected Draft array_source_candidates."
                    ),
                },
            )
        facts = raw.get("facts")
        if not isinstance(facts, dict):
            raise DraftCandidateError(
                f"document_references[{index}] has no authenticated topology facts.",
                details={
                    "stage": "reference_authentication",
                    "index": index,
                    "correction": (
                        "Retry on current document state so VibeCAD can recapture the "
                        "selected reference and its topology facts."
                    ),
                },
            )
        references[key] = MappingProxyType(
            {
                "document_uid": key[0],
                "object_name": key[1],
                "label": str(raw.get("label") or ""),
                "type_id": str(raw.get("type_id") or ""),
                "shape_type": str(raw.get("shape_type") or ""),
                "brep_sha256": str(raw.get("brep_sha256") or ""),
                "facts": dict(facts),
            }
        )
    global _DRAFT_REFERENCES
    _DRAFT_REFERENCES = MappingProxyType(references)


def _payload(value: Any, *, context: str) -> dict[str, Any]:
    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise DraftCandidateError(
            f"{context} must be a value returned by the active Draft api.",
            details={
                "stage": "graph_contract",
                "path": context,
                "correction": _GRAPH_CORRECTION,
            },
        )
    expected = {"domain", "operation", "output_type", "arguments", "properties"}
    if set(payload) != expected:
        raise DraftCandidateError(
            f"{context} has malformed Draft graph fields.",
            details={
                "stage": "graph_contract",
                "path": context,
                "missing": sorted(expected - set(payload)),
                "unexpected": sorted(set(payload) - expected),
                "correction": _GRAPH_CORRECTION,
            },
        )
    if payload.get("domain") != "draft":
        raise DraftCandidateError(
            f"{context} belongs to another VibeScript domain.",
            details={
                "stage": "graph_contract",
                "path": context,
                "correction": _GRAPH_CORRECTION,
            },
        )
    operation = str(payload.get("operation") or "")
    output_type = str(payload.get("output_type") or "")
    if operation not in _OPERATIONS or output_type != operation:
        raise DraftCandidateError(
            f"{context} has unsupported Draft operation/type "
            f"{operation!r}/{output_type!r}.",
            details={
                "stage": "graph_contract",
                "path": context,
                "operation": operation,
                "output_type": output_type,
                "supported_operations": sorted(_OPERATIONS),
                "correction": _GRAPH_CORRECTION,
            },
        )
    if not isinstance(payload.get("arguments"), list) or not isinstance(
        payload.get("properties"), dict
    ):
        raise DraftCandidateError(
            f"{context} arguments and properties must be serialized containers.",
            details={
                "stage": "graph_contract",
                "path": context,
                "correction": _GRAPH_CORRECTION,
            },
        )
    return payload


def _arguments(payload: Mapping[str, Any], *, count: int, context: str) -> list[Any]:
    values = list(payload.get("arguments") or [])
    if len(values) != count:
        raise DraftCandidateError(
            f"{context} must serialize exactly {count} positional argument(s).",
            details={
                "stage": "graph_contract",
                "path": context,
                "expected_argument_count": count,
                "received_argument_count": len(values),
                "correction": _GRAPH_CORRECTION,
            },
        )
    return values


def _properties(
    payload: Mapping[str, Any],
    *,
    names: set[str],
    context: str,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    values = dict(payload.get("properties") or {})
    optional = dict(defaults or {})
    allowed = names | set(optional)
    if not names <= set(values) or not set(values) <= allowed:
        raise DraftCandidateError(
            f"{context} has malformed immutable properties.",
            details={
                "stage": "graph_contract",
                "path": context,
                "missing": sorted(names - set(values)),
                "unexpected": sorted(set(values) - allowed),
                "optional": sorted(optional),
                "correction": _GRAPH_CORRECTION,
            },
        )
    for name, default in optional.items():
        values.setdefault(name, default)
    graph_id = str(values.get("graph_id") or "")
    if not _GRAPH_ID.fullmatch(graph_id):
        raise DraftCandidateError(
            f"{context}.graph_id is invalid.",
            details={
                "stage": "graph_identity",
                "path": context,
                "graph_id": graph_id,
                "correction": _GRAPH_CORRECTION,
            },
        )
    return values


def _number(value: Any, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DraftCandidateError(
            f"{context} must be a finite number.",
            details={
                "stage": "graph_contract",
                "path": context,
                "received_type": type(value).__name__,
                "correction": _GRAPH_CORRECTION,
            },
        )
    result = float(value)
    if not math.isfinite(result):
        raise DraftCandidateError(
            f"{context} must be finite.",
            details={
                "stage": "graph_contract",
                "path": context,
                "correction": _GRAPH_CORRECTION,
            },
        )
    return result


def _vector(value: Any, *, context: str):
    import FreeCAD as App

    if not isinstance(value, list) or len(value) != 3:
        raise DraftCandidateError(
            f"{context} must be a validated [x,y,z] vector.",
            details={
                "stage": "graph_contract",
                "path": context,
                "correction": _GRAPH_CORRECTION,
            },
        )
    return App.Vector(*(_number(item, context=f"{context}[{index}]") for index, item in enumerate(value)))


def _placement(value: Any, *, context: str):
    import FreeCAD as App

    if not isinstance(value, dict) or set(value) != {"position", "rotation"}:
        raise DraftCandidateError(
            f"{context} must contain exactly position and rotation.",
            details={
                "stage": "graph_contract",
                "path": context,
                "correction": _GRAPH_CORRECTION,
            },
        )
    position = _vector(value.get("position"), context=f"{context}.position")
    rotation = value.get("rotation")
    if not isinstance(rotation, list) or len(rotation) != 4:
        raise DraftCandidateError(
            f"{context}.rotation must be quaternion [x,y,z,w].",
            details={
                "stage": "graph_contract",
                "path": f"{context}.rotation",
                "correction": _GRAPH_CORRECTION,
            },
        )
    quaternion = [
        _number(item, context=f"{context}.rotation[{index}]")
        for index, item in enumerate(rotation)
    ]
    return App.Placement(position, App.Rotation(*quaternion))


def _placement_data(value: Any) -> dict[str, list[float]]:
    quaternion = list(value.Rotation.Q)
    return {
        "position": [float(value.Base.x), float(value.Base.y), float(value.Base.z)],
        "rotation": [float(item) for item in quaternion],
    }


def _placement_matrix(value: Any) -> list[float]:
    matrix = value.toMatrix()
    return [
        float(getattr(matrix, name))
        for name in (
            "A11",
            "A12",
            "A13",
            "A14",
            "A21",
            "A22",
            "A23",
            "A24",
            "A31",
            "A32",
            "A33",
            "A34",
            "A41",
            "A42",
            "A43",
            "A44",
        )
    ]


def _native_angle(value: Any, *, context: str) -> float:
    angle = _number(value, context=context)
    return math.copysign(abs(angle) % 360.0, angle)


def _draft_type(obj: Any) -> str:
    from draftutils.utils import get_type

    return str(get_type(obj) or "")


def _base_data(obj: Any, properties: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "graph_id": str(properties["graph_id"]),
        "native_type": str(getattr(obj, "TypeId", "") or ""),
        "proxy_class": type(getattr(obj, "Proxy", None)).__name__,
        "draft_type": _draft_type(obj),
        "label": str(getattr(obj, "Label", "") or ""),
        "placement": _placement_data(obj.Placement),
    }


def _local_points(obj: Any) -> list[list[float]]:
    return [
        [float(point.x), float(point.y), float(point.z)]
        for point in list(getattr(obj, "Points", []) or [])
    ]


def _create_primitive(document: Any, payload: Mapping[str, Any], name: str) -> Any:
    import Draft

    operation = str(payload["operation"])
    context = f"api.{operation}"
    common = {"placement", "label", "graph_id"}
    try:
        if operation == "wire":
            arguments = _arguments(payload, count=1, context=context)
            properties = _properties(
                payload,
                names=common
                | {
                    "closed",
                    "make_face",
                    "fillet_radius",
                    "chamfer_size",
                    "subdivisions",
                },
                context=context,
            )
            obj = Draft.make_wire(
                [_vector(point, context=f"{context}.points") for point in arguments[0]],
                closed=bool(properties["closed"]),
                face=bool(properties["make_face"]),
            )
            if obj is not None:
                obj.FilletRadius = _number(
                    properties["fillet_radius"], context=f"{context}.fillet_radius"
                )
                obj.ChamferSize = _number(
                    properties["chamfer_size"], context=f"{context}.chamfer_size"
                )
                obj.Subdivisions = int(properties["subdivisions"])
        elif operation == "circle":
            arguments = _arguments(payload, count=1, context=context)
            properties = _properties(
                payload,
                names=common | {"start_angle", "end_angle", "make_face"},
                context=context,
            )
            obj = Draft.make_circle(
                _number(arguments[0], context=f"{context}.radius"),
                face=bool(properties["make_face"]),
                startangle=_native_angle(
                    properties["start_angle"], context=f"{context}.start_angle"
                ),
                endangle=_native_angle(
                    properties["end_angle"], context=f"{context}.end_angle"
                ),
            )
        elif operation == "rectangle":
            arguments = _arguments(payload, count=2, context=context)
            properties = _properties(
                payload,
                names=common | {"make_face", "fillet_radius", "chamfer_size"},
                context=context,
            )
            obj = Draft.make_rectangle(
                _number(arguments[0], context=f"{context}.length"),
                _number(arguments[1], context=f"{context}.height"),
                face=bool(properties["make_face"]),
            )
            if obj is not None:
                obj.FilletRadius = _number(
                    properties["fillet_radius"], context=f"{context}.fillet_radius"
                )
                obj.ChamferSize = _number(
                    properties["chamfer_size"], context=f"{context}.chamfer_size"
                )
        elif operation == "bspline":
            arguments = _arguments(payload, count=1, context=context)
            properties = _properties(
                payload,
                names=common | {"closed", "make_face", "parameterization"},
                context=context,
            )
            obj = Draft.make_bspline(
                [_vector(point, context=f"{context}.points") for point in arguments[0]],
                closed=bool(properties["closed"]),
                face=bool(properties["make_face"]),
            )
            if obj is not None:
                obj.Parameterization = _number(
                    properties["parameterization"],
                    context=f"{context}.parameterization",
                )
        elif operation == "text":
            arguments = _arguments(payload, count=1, context=context)
            properties = _properties(
                payload,
                names=common | {"screen", "height", "line_spacing"},
                context=context,
            )
            lines = arguments[0]
            if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
                raise DraftCandidateError(
                    f"{context}.lines must contain only strings.",
                    details={
                        "stage": "graph_contract",
                        "path": f"{context}.lines",
                        "correction": _GRAPH_CORRECTION,
                    },
                )
            obj = Draft.make_text(
                list(lines),
                placement=_placement(properties["placement"], context=f"{context}.placement"),
                screen=bool(properties["screen"]),
                height=_number(properties["height"], context=f"{context}.height"),
                line_spacing=_number(
                    properties["line_spacing"], context=f"{context}.line_spacing"
                ),
            )
        else:
            raise DraftCandidateError(
                f"Unsupported primitive operation {operation!r}.",
                details={
                    "stage": "graph_contract",
                    "operation": operation,
                    "correction": _GRAPH_CORRECTION,
                },
            )
        if obj is None:
            raise DraftCandidateError(
                f"The native Draft factory returned no object for {context}.",
                details={
                    "stage": "native_factory",
                    "operation": operation,
                    "correction": (
                        f"Inspect api.{operation} dimensions and options, then simplify "
                        "only the failing object definition."
                    ),
                },
            )
        if str(getattr(obj, "Document", None).Name) != str(document.Name):
            raise DraftCandidateError(
                f"{context} was created outside the isolated candidate document.",
                details={
                    "stage": "native_factory",
                    "operation": operation,
                    "correction": (
                        "Retry this candidate; source cannot create or select another document."
                    ),
                },
            )
        obj.Label = str(properties["label"] or name)
        if operation != "text":
            obj.Placement = _placement(
                properties["placement"], context=f"{context}.placement"
            )
        return obj
    except DraftCandidateError:
        raise
    except Exception as exc:
        raise DraftCandidateError(
            f"FreeCAD rejected {context}: {exc}",
            details={
                "stage": "native_factory",
                "operation": operation,
                "exception_type": type(exc).__name__,
                "correction": (
                    "Check the operation's dimensions, local points, placement, and face settings."
                ),
            },
        ) from exc


def _reference_key(value: Any, *, context: str) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {"document_uid", "object_name"}:
        raise DraftCandidateError(
            f"{context} must be a returned Draft graph value or authenticated reference.",
            details={
                "stage": "array_source",
                "path": context,
                "correction": (
                    "Pass a shape-producing Draft api value that is also returned in "
                    "result, or copy one exact stable reference from array_source_candidates."
                ),
            },
        )
    key = (str(value["document_uid"] or ""), str(value["object_name"] or ""))
    if key not in _DRAFT_REFERENCES:
        raise DraftCandidateError(
            f"{context} reference {key[1]!r} was not authenticated from inputs.",
            details={
                "stage": "array_source",
                "document_uid": key[0],
                "object_name": key[1],
                "available_objects": sorted(name for _uid, name in _DRAFT_REFERENCES),
                "correction": (
                    "Replace this source with one exact reference from the injected "
                    "array_source_candidates; never invent document_uid or object_name."
                ),
            },
        )
    return key


def _external_source(document: Any, key: tuple[str, str], cache: dict[tuple[str, str], Any]) -> Any:
    source = cache.get(key)
    if source is not None:
        return source
    from vibescript_part_worker import detached_reference_shape

    source = document.addObject("Part::Feature", f"DraftReference{len(cache) + 1}")
    source.Shape = detached_reference_shape(
        {"document_uid": key[0], "object_name": key[1]}
    )
    source.Label = str(_DRAFT_REFERENCES[key].get("label") or key[1])
    cache[key] = source
    return source


def _array_source(
    document: Any,
    payload: Mapping[str, Any],
    graph_objects: Mapping[str, Any],
    graph_outputs: Mapping[str, str],
    reference_objects: dict[tuple[str, str], Any],
) -> tuple[Any, dict[str, Any]]:
    source = _arguments(payload, count=1, context="api.array")[0]
    if isinstance(source, dict) and {
        "domain",
        "operation",
        "output_type",
        "arguments",
        "properties",
    } <= set(source):
        nested = _payload(source, context="api.array.source")
        nested_properties = dict(nested["properties"])
        graph_id = str(nested_properties.get("graph_id") or "")
        obj = graph_objects.get(graph_id)
        if obj is None:
            raise DraftCandidateError(
                "api.array.source must be returned as a stable shape output before it "
                "can become a native Draft Base link.",
                details={
                    "stage": "array_source",
                    "source_graph_id": graph_id,
                    "returned_shape_graph_ids": sorted(graph_outputs),
                    "correction": (
                        "Add the source value to result and expected_outputs, or use a stable input reference."
                    ),
                },
            )
        return obj, {
            "kind": "program_output",
            "graph_id": graph_id,
            "output_name": str(graph_outputs[graph_id]),
        }
    key = _reference_key(source, context="api.array.source")
    obj = _external_source(document, key, reference_objects)
    metadata = _DRAFT_REFERENCES[key]
    return obj, {
        "kind": "document_reference",
        "document_uid": key[0],
        "object_name": key[1],
        "brep_sha256": str(metadata.get("brep_sha256") or ""),
        "shape_type": str(metadata.get("shape_type") or ""),
    }


def _create_array(
    document: Any,
    payload: Mapping[str, Any],
    name: str,
    graph_objects: Mapping[str, Any],
    graph_outputs: Mapping[str, str],
    reference_objects: dict[tuple[str, str], Any],
) -> tuple[Any, dict[str, Any]]:
    import Draft

    properties = _properties(
        payload,
        names={
            "kind",
            "interval_x",
            "interval_y",
            "interval_z",
            "count_x",
            "count_y",
            "count_z",
            "count",
            "total_angle_degrees",
            "center",
            "axis",
            "interval_axis",
            "radial_distance",
            "tangential_distance",
            "number_circles",
            "symmetry",
            "use_link",
            "fuse",
            "label",
            "graph_id",
        },
        context="api.array",
    )
    source, source_data = _array_source(
        document,
        payload,
        graph_objects,
        graph_outputs,
        reference_objects,
    )
    try:
        if properties["kind"] == "orthogonal":
            obj = Draft.make_ortho_array(
                source,
                v_x=_vector(properties["interval_x"], context="api.array.interval_x"),
                v_y=_vector(properties["interval_y"], context="api.array.interval_y"),
                v_z=_vector(properties["interval_z"], context="api.array.interval_z"),
                n_x=int(properties["count_x"]),
                n_y=int(properties["count_y"]),
                n_z=int(properties["count_z"]),
                use_link=bool(properties["use_link"]),
            )
        elif properties["kind"] == "polar":
            obj = Draft.make_polar_array(
                source,
                number=int(properties["count"]),
                angle=_number(
                    properties["total_angle_degrees"],
                    context="api.array.total_angle_degrees",
                ),
                center=_vector(properties["center"], context="api.array.center"),
                axis=_vector(properties["axis"], context="api.array.axis"),
                use_link=bool(properties["use_link"]),
            )
        elif properties["kind"] == "circular":
            obj = Draft.make_circular_array(
                source,
                r_distance=_number(
                    properties["radial_distance"],
                    context="api.array.radial_distance",
                ),
                tan_distance=_number(
                    properties["tangential_distance"],
                    context="api.array.tangential_distance",
                ),
                number=int(properties["number_circles"]),
                symmetry=int(properties["symmetry"]),
                axis=_vector(properties["axis"], context="api.array.axis"),
                center=_vector(properties["center"], context="api.array.center"),
                use_link=bool(properties["use_link"]),
            )
        else:
            raise DraftCandidateError(
                f"api.array.kind {properties['kind']!r} is unsupported.",
                details={
                    "stage": "graph_contract",
                    "path": "api.array.kind",
                    "supported_kinds": ["orthogonal", "polar", "circular"],
                    "correction": _GRAPH_CORRECTION,
                },
            )
        if obj is None:
            raise DraftCandidateError(
                "The native Draft array factory returned no object.",
                details={
                    "stage": "native_array_factory",
                    "array_kind": properties.get("kind"),
                    "source": source_data,
                    "correction": (
                        "Check the Base shape, active kind's counts and spacing, axis, "
                        "center, and link/fuse compatibility."
                    ),
                },
            )
        # Persist the complete immutable schema, including parameters hidden by
        # the active array kind.  Native defaults are version-dependent; exact
        # assignment keeps save/reopen and later inspection deterministic.
        obj.NumberX = int(properties["count_x"])
        obj.NumberY = int(properties["count_y"])
        obj.NumberZ = int(properties["count_z"])
        obj.IntervalX = _vector(
            properties["interval_x"], context="api.array.interval_x"
        )
        obj.IntervalY = _vector(
            properties["interval_y"], context="api.array.interval_y"
        )
        obj.IntervalZ = _vector(
            properties["interval_z"], context="api.array.interval_z"
        )
        obj.NumberPolar = int(properties["count"])
        obj.Angle = _number(
            properties["total_angle_degrees"],
            context="api.array.total_angle_degrees",
        )
        obj.Center = _vector(properties["center"], context="api.array.center")
        obj.Axis = _vector(properties["axis"], context="api.array.axis")
        obj.IntervalAxis = _vector(
            properties["interval_axis"], context="api.array.interval_axis"
        )
        obj.RadialDistance = _number(
            properties["radial_distance"], context="api.array.radial_distance"
        )
        obj.TangentialDistance = _number(
            properties["tangential_distance"],
            context="api.array.tangential_distance",
        )
        obj.NumberCircles = int(properties["number_circles"])
        obj.Symmetry = int(properties["symmetry"])
        obj.Fuse = bool(properties["fuse"])
        obj.Label = str(properties["label"] or name)
        return obj, source_data
    except DraftCandidateError:
        raise
    except Exception as exc:
        raise DraftCandidateError(
            f"FreeCAD rejected api.array: {exc}",
            details={
                "stage": "native_array_factory",
                "array_kind": properties.get("kind"),
                "source": source_data,
                "exception_type": type(exc).__name__,
                "correction": (
                    "Check the array kind-specific counts, spacing, axis, center, "
                    "link/fuse mode, and that the returned source is shape-producing."
                ),
            },
        ) from exc


def _primitive_data(obj: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    operation = str(payload["operation"])
    properties = dict(payload["properties"])
    data = _base_data(obj, properties)
    if operation in {"wire", "bspline"}:
        data.update(
            {
                "points": _local_points(obj),
                "closed": bool(obj.Closed),
                "make_face": bool(obj.MakeFace),
            }
        )
        if operation == "bspline":
            data["parameterization"] = float(obj.Parameterization)
        else:
            data.update(
                {
                    "fillet_radius": float(obj.FilletRadius.Value),
                    "chamfer_size": float(obj.ChamferSize.Value),
                    "subdivisions": int(obj.Subdivisions),
                }
            )
    elif operation == "circle":
        data.update(
            {
                "radius": float(obj.Radius.Value),
                "start_angle": float(obj.FirstAngle.Value),
                "end_angle": float(obj.LastAngle.Value),
                "make_face": bool(obj.MakeFace),
            }
        )
    elif operation == "rectangle":
        data.update(
            {
                "length": float(obj.Length.Value),
                "height": float(obj.Height.Value),
                "make_face": bool(obj.MakeFace),
                "fillet_radius": float(obj.FilletRadius.Value),
                "chamfer_size": float(obj.ChamferSize.Value),
            }
        )
    elif operation == "text":
        data.update(
            {
                "lines": [str(value) for value in list(obj.Text or [])],
                "screen": bool(properties["screen"]),
                "height": float(properties["height"]),
                "line_spacing": float(properties["line_spacing"]),
                "display_validation": "deferred_to_gui_publication",
            }
        )
    return data


def _array_data(
    obj: Any,
    payload: Mapping[str, Any],
    source_data: Mapping[str, Any],
) -> dict[str, Any]:
    properties = dict(payload["properties"])
    data = _base_data(obj, properties)
    placements = list(getattr(obj, "PlacementList", []) or [])
    data.update(
        {
            "source": dict(source_data),
            "array_kind": str(obj.ArrayType),
            "use_link": bool(getattr(obj.Proxy, "use_link", False)),
            "fuse": bool(obj.Fuse),
            "count": int(obj.Count),
            "placement_matrices": [_placement_matrix(value) for value in placements],
            "number_x": int(obj.NumberX),
            "number_y": int(obj.NumberY),
            "number_z": int(obj.NumberZ),
            "interval_x": [float(value) for value in obj.IntervalX],
            "interval_y": [float(value) for value in obj.IntervalY],
            "interval_z": [float(value) for value in obj.IntervalZ],
            "interval_axis": [float(value) for value in obj.IntervalAxis],
            "number_polar": int(obj.NumberPolar),
            "angle_degrees": float(obj.Angle.Value),
            "center": [float(value) for value in obj.Center],
            "axis": [float(value) for value in obj.Axis],
            "radial_distance": float(obj.RadialDistance.Value),
            "tangential_distance": float(obj.TangentialDistance.Value),
            "number_circles": int(obj.NumberCircles),
            "symmetry": int(obj.Symmetry),
        }
    )
    return data


def _wire_turn_count(points: list[list[float]], *, closed: bool) -> int:
    point_count = len(points)
    vertices = range(point_count) if closed else range(1, point_count - 1)
    count = 0
    for index in vertices:
        previous = points[(index - 1) % point_count]
        current = points[index]
        following = points[(index + 1) % point_count]
        before = [previous[axis] - current[axis] for axis in range(3)]
        after = [following[axis] - current[axis] for axis in range(3)]
        before_length = math.sqrt(sum(value * value for value in before))
        after_length = math.sqrt(sum(value * value for value in after))
        cosine = sum(left * right for left, right in zip(before, after)) / (
            before_length * after_length
        )
        angle = math.acos(max(-1.0, min(1.0, cosine)))
        if angle > 1.0e-10 and abs(math.pi - angle) > 1.0e-10:
            count += 1
    return count


def _validate_parametric_effect(
    output_name: str,
    operation: str,
    obj: Any,
    data: Mapping[str, Any],
) -> None:
    """Reject native Draft properties that were retained but silently had no effect."""

    if operation not in {"wire", "rectangle"}:
        return
    edges = list(getattr(getattr(obj, "Shape", None), "Edges", []) or [])
    edge_count = len(edges)
    circular_edges = sum(
        "circle" in type(getattr(edge, "Curve", None)).__name__.lower()
        for edge in edges
    )
    fillet_radius = float(data.get("fillet_radius") or 0.0)
    chamfer_size = float(data.get("chamfer_size") or 0.0)
    subdivisions = int(data.get("subdivisions") or 0)
    if operation == "rectangle":
        base_segments = 4
        turn_count = 4
    else:
        points = list(data.get("points") or [])
        closed = bool(data.get("closed"))
        base_segments = len(points) if closed else len(points) - 1
        turn_count = _wire_turn_count(points, closed=closed)
    expected_edges = base_segments * (subdivisions + 1)
    if fillet_radius > 0.0:
        expected_edges = base_segments + turn_count
        effect_ok = edge_count == expected_edges and circular_edges == turn_count
        parameter = "fillet_radius"
    elif chamfer_size > 0.0:
        expected_edges = base_segments + turn_count
        effect_ok = edge_count == expected_edges and circular_edges == 0
        parameter = "chamfer_size"
    else:
        effect_ok = edge_count == expected_edges
        parameter = "subdivisions"
    if not effect_ok:
        raise DraftCandidateError(
            f"Draft output {output_name!r} retained {parameter} but its native topology "
            "does not show the requested parametric effect.",
            details={
                "stage": "native_parametric_effect",
                "output_name": output_name,
                "operation": operation,
                "parameter": parameter,
                "requested_value": data.get(parameter),
                "expected_edge_count": expected_edges,
                "observed_edge_count": edge_count,
                "expected_circular_edge_count": (
                    turn_count if fillet_radius > 0.0 else 0
                ),
                "observed_circular_edge_count": circular_edges,
                "correction": (
                    f"Reduce api.{operation} {parameter!r}, simplify adjacent corners, "
                    "or set it to 0; the prior accepted revision remains live."
                ),
            },
        )


def _validate_shape_contract(
    output_name: str,
    operation: str,
    properties: Mapping[str, Any],
    facts: Mapping[str, Any],
) -> None:
    shape_type = str(facts.get("shape_type") or "")
    expected: set[str]
    if operation == "wire":
        expected = {"Face"} if properties["make_face"] else {"Wire"}
    elif operation == "circle":
        start = _native_angle(properties["start_angle"], context="circle.start_angle")
        end = _native_angle(properties["end_angle"], context="circle.end_angle")
        full = math.isclose(start, end, rel_tol=0.0, abs_tol=1.0e-12)
        expected = ({"Face"} if properties["make_face"] else {"Wire"}) if full else {"Edge"}
    elif operation == "rectangle":
        expected = {"Face"} if properties["make_face"] else {"Wire"}
    elif operation == "bspline":
        if properties["closed"]:
            expected = {"Face"} if properties["make_face"] else {"Wire"}
        else:
            expected = {"Edge"}
    else:
        expected = {"Compound"} if not properties["fuse"] else {
            "Compound",
            "CompSolid",
            "Solid",
            "Shell",
            "Face",
            "Wire",
        }
    if shape_type not in expected:
        raise DraftCandidateError(
            f"Draft output {output_name!r} produced OCC {shape_type or '<missing>'}; "
            f"expected one of {sorted(expected)} for api.{operation}.",
            details={
                "stage": "native_shape_contract",
                "output_name": output_name,
                "operation": operation,
                "expected_shape_types": sorted(expected),
                "observed_shape_type": shape_type,
                "correction": (
                    "For face output, use a closed planar profile; otherwise disable make_face."
                ),
            },
        )


def validate_and_build_draft(
    document: Any,
    raw_result: Mapping[str, Any],
    expected_outputs: list[dict[str, Any]],
    root: Path,
    *,
    max_shape_subelements: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build real native Draft objects, recompute, validate, and export artifacts."""

    from vibescript_part_worker import part_shape_facts

    definitions: dict[str, dict[str, Any]] = {}
    graph_outputs: dict[str, str] = {}
    for expected in expected_outputs:
        name = str(expected.get("name") or "")
        payload = _payload(raw_result[name], context=f"result[{name!r}]")
        if payload["output_type"] != expected.get("type"):
            raise DraftCandidateError(
                f"Output {name!r} returned {payload['output_type']!r}; "
                f"expected {expected.get('type')!r}.",
                details={
                    "stage": "result_contract",
                    "output_name": name,
                    "expected_type": expected.get("type"),
                    "observed_type": payload["output_type"],
                    "correction": (
                        f"Return a Draft api value of type {expected.get('type')!r} "
                        f"for result[{name!r}], or reconfigure expected_outputs together."
                    ),
                },
            )
        properties = dict(payload["properties"])
        graph_id = str(properties.get("graph_id") or "")
        if not _GRAPH_ID.fullmatch(graph_id) or graph_id in graph_outputs:
            raise DraftCandidateError(
                "Every returned Draft output must have one unique stable graph id.",
                details={
                    "stage": "graph_identity",
                    "output_name": name,
                    "graph_id": graph_id,
                    "existing_output": graph_outputs.get(graph_id),
                    "correction": (
                        "Create each semantic output with its own direct Draft api call; "
                        "do not return the same graph value under multiple result names."
                    ),
                },
            )
        graph_outputs[graph_id] = name
        definitions[name] = payload

    graph_objects: dict[str, Any] = {}
    source_data: dict[str, dict[str, Any]] = {}
    for expected in expected_outputs:
        name = str(expected["name"])
        payload = definitions[name]
        if payload["operation"] == "array":
            continue
        obj = _create_primitive(document, payload, name)
        graph_objects[str(payload["properties"]["graph_id"])] = obj

    reference_objects: dict[tuple[str, str], Any] = {}
    remaining = [
        str(expected["name"])
        for expected in expected_outputs
        if definitions[str(expected["name"])]["operation"] == "array"
    ]
    while remaining:
        progress = False
        deferred: list[str] = []
        for name in remaining:
            payload = definitions[name]
            raw_source = list(payload["arguments"])[0]
            if isinstance(raw_source, dict) and "properties" in raw_source:
                source_graph_id = str(dict(raw_source.get("properties") or {}).get("graph_id") or "")
                if source_graph_id not in graph_objects:
                    if source_graph_id in graph_outputs:
                        deferred.append(name)
                        continue
                    raise DraftCandidateError(
                        f"Array output {name!r} uses graph {source_graph_id!r}, which is not "
                        "a returned stable shape output.",
                        details={
                            "stage": "array_source",
                            "output_name": name,
                            "source_graph_id": source_graph_id,
                            "returned_shape_graph_ids": sorted(graph_outputs),
                            "correction": (
                                "Return the array Base as a shape output with a stable result "
                                "name, or replace it with an authenticated input reference."
                            ),
                        },
                    )
            obj, resolution = _create_array(
                document,
                payload,
                name,
                graph_objects,
                graph_outputs,
                reference_objects,
            )
            graph_objects[str(payload["properties"]["graph_id"])] = obj
            source_data[name] = resolution
            progress = True
        if not progress:
            raise DraftCandidateError(
                "Draft arrays contain a cyclic or unresolved output dependency.",
                details={
                    "stage": "array_dependency_graph",
                    "unresolved_outputs": deferred,
                    "correction": (
                        "Break the cycle so every array Base chain terminates at a returned "
                        "primitive or one authenticated input reference."
                    ),
                },
            )
        remaining = deferred

    try:
        document.recompute()
    except Exception as exc:
        raise DraftCandidateError(
            f"The isolated Draft document failed to recompute: {exc}",
            details={
                "stage": "native_recompute",
                "exception_type": type(exc).__name__,
                "correction": (
                    "Inspect the named primitive and array definitions, then simplify the "
                    "smallest geometry or dependency that caused native recompute to fail."
                ),
            },
        ) from exc

    outputs: list[dict[str, Any]] = []
    validation_outputs: list[dict[str, Any]] = []
    for index, expected in enumerate(expected_outputs):
        name = str(expected["name"])
        payload = definitions[name]
        properties = dict(payload["properties"])
        graph_id = str(properties["graph_id"])
        obj = graph_objects[graph_id]
        operation = str(payload["operation"])
        data = (
            _array_data(obj, payload, source_data[name])
            if operation == "array"
            else _primitive_data(obj, payload)
        )
        _validate_parametric_effect(name, operation, obj, data)
        item: dict[str, Any] = {
            "name": name,
            "type": str(expected["type"]),
            "definition": payload,
            "draft_data": data,
        }
        if operation in _SHAPE_TYPES:
            shape = getattr(obj, "Shape", None)
            if shape is None or shape.isNull() or not shape.isValid():
                raise DraftCandidateError(
                    f"Draft output {name!r} did not produce a valid native Shape.",
                    details={
                        "stage": "native_shape_validation",
                        "output_name": name,
                        "operation": operation,
                        "native_type": data["native_type"],
                        "draft_type": data["draft_type"],
                        "correction": (
                            f"Simplify api.{operation} inputs or disable an incompatible "
                            "face, corner, fuse, or array option until it produces a valid Shape."
                        ),
                    },
                )
            facts = part_shape_facts(
                shape,
                max_subelements=max_shape_subelements,
            )
            _validate_shape_contract(name, operation, properties, facts)
            relative = Path("outputs") / f"output-{index:03d}.brep"
            target = root / relative
            shape.exportBrep(str(target))
            if not target.is_file() or target.stat().st_size <= 0:
                raise DraftCandidateError(
                    f"Could not export validated Draft output {name!r}.",
                    details={
                        "stage": "artifact_export",
                        "output_name": name,
                        "operation": operation,
                        "correction": (
                            "Retry the candidate; if export fails again, simplify only this "
                            "output while the accepted revision remains live."
                        ),
                    },
                )
            item.update(
                {
                    "artifact_kind": "brep",
                    "artifact_path": str(relative),
                    "facts": facts,
                }
            )
        validation_outputs.append(
            {
                "name": name,
                "type": operation,
                "graph_id": graph_id,
                "native_type": data["native_type"],
                "proxy_class": data["proxy_class"],
                "draft_type": data["draft_type"],
            }
        )
        outputs.append(item)
    return outputs, {
        "native_object_count": len(outputs),
        "shape_output_count": sum(item["type"] in _SHAPE_TYPES for item in outputs),
        "text_output_count": sum(item["type"] == "text" for item in outputs),
        "array_output_count": sum(item["type"] == "array" for item in outputs),
        "referenced_object_count": len(reference_objects),
        "outputs": validation_outputs,
    }
