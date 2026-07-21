# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native Arch/Draft evaluator for production BIM VibeScript."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
from pathlib import Path
import re
from typing import Any

from vibescript_domain_api import DomainValue


VALIDATION_SCHEMA = "vibecad-vibescript-bim-validation-v1"
_GRAPH_ID = re.compile(r"^bim([1-9][0-9]*)$")
_OPERATIONS = ("site", "building", "level", "wall", "slab", "structure", "opening")
_PARENT_TYPES = {
    "building": "site",
    "level": "building",
    "wall": "level",
    "slab": "level",
    "structure": "level",
    "opening": "wall",
}
_ARGUMENT_COUNTS = {
    "site": 0,
    "building": 1,
    "level": 1,
    "wall": 2,
    "slab": 2,
    "structure": 4,
    "opening": 1,
}
_PROPERTY_NAMES = {
    "site": {
        "address",
        "postal_code",
        "city",
        "region",
        "country",
        "latitude",
        "longitude",
        "elevation",
        "label",
        "graph_id",
    },
    "building": {"label", "graph_id"},
    "level": {"elevation", "height", "label", "graph_id"},
    "wall": {
        "closed",
        "width",
        "height",
        "alignment",
        "offset",
        "label",
        "graph_id",
    },
    "slab": {"thickness", "top_offset", "label", "graph_id"},
    "structure": {"placement", "role", "label", "graph_id"},
    "opening": {
        "width",
        "height",
        "segment",
        "offset",
        "sill",
        "hole_depth",
        "label",
        "graph_id",
    },
}
_ROLE_IFC = {"column": "Column", "beam": "Beam", "member": "Member"}
_EXPECTED_NATIVE = {
    "site": ("Part::FeaturePython", "_Site", "Site", "Site"),
    "building": ("App::GeometryPython", "BuildingPart", "BuildingPart", "Building"),
    "level": (
        "App::GeometryPython",
        "BuildingPart",
        "BuildingPart",
        "Building Storey",
    ),
    "wall": ("Part::FeaturePython", "_Wall", "Wall", "Wall"),
    "slab": ("Part::FeaturePython", "_Structure", "Structure", "Slab"),
    "structure": ("Part::FeaturePython", "_Structure", "Structure", ""),
    "opening": ("Part::FeaturePython", "_Window", "Window", "Opening Element"),
}
_EPSILON = 1.0e-7


def _default_correction(details: Mapping[str, Any]) -> str:
    """Return one bounded source or environment repair for every BIM stage."""

    stage = str(details.get("stage") or "")
    output_name = str(details.get("output_name") or "")
    operation = str(details.get("operation") or "")
    path = str(details.get("path") or "")
    output = f" {output_name!r}" if output_name else ""
    api_name = f"api.{operation}" if operation else "the reported BIM operation"
    if stage == "result_contract":
        return (
            "Return exactly the declared expected_outputs names, types, and order. Keep "
            "those declarations unchanged and replace only the mismatched result value."
        )
    if stage == "graph_contract":
        location = f" at {path}" if path else output
        return (
            f"Rebuild only the malformed BIM value{location} by calling the active BIM "
            "api directly; never construct, copy, or mutate serialized graph dictionaries."
        )
    if stage == "graph_identity":
        return (
            "Create every BIM graph value exactly once in ancestor-before-child source order, "
            "reuse that immutable value for children, and return it under one stable result name."
        )
    if stage == "hierarchy_contract":
        return (
            f"Create the exact required parent before {api_name}, return both parent and child, "
            "and pass the same immutable parent variable rather than a copied or hidden value."
        )
    if stage == "api_revalidation":
        return (
            f"Correct only the reported {api_name} argument for BIM output{output}, then "
            "return the direct immutable API result under the unchanged output name."
        )
    if stage == "opening_fit":
        return (
            f"Correct only opening{output}: choose a listed zero-based host segment and adjust "
            "offset/width or sill/height to fit it without overlapping another opening."
        )
    if stage == "native_input":
        location = f" {path!r}" if path else ""
        return (
            f"Correct only native BIM input{location} using the documented finite millimetre "
            "coordinates or normalized placement form, then retry the failed revision."
        )
    if stage in {"native_object_contract", "native_object_state"}:
        return (
            f"Keep output{output} on its documented {api_name} native type and IFC role; "
            "remove only the unsupported definition that changed or invalidated that object."
        )
    if stage == "native_recompute":
        return (
            f"Repair only the reported BIM node{output} or its ancestor geometry, then retry; "
            "the accepted live hierarchy remains unchanged."
        )
    if stage == "native_opening_cut":
        return (
            f"Correct the hosted openings for wall{output}: keep them inside non-overlapping "
            "segment intervals and use hole_depth=0 for the validated wall-width default."
        )
    if stage in {"native_shape_validation", "native_base_validation"}:
        return (
            f"Correct only geometry for BIM output{output}: use non-self-intersecting points, "
            "positive dimensions, and the documented level-local coordinate convention."
        )
    if stage == "artifact_export":
        return (
            "Keep the validated BIM source unchanged and retry only after the isolated worker "
            "can write and read its bounded project staging artifacts."
        )
    if stage == "native_factory_or_recompute":
        return (
            f"Correct only the reported BIM node{output} using supported native parameters; "
            "if the native exception is environmental, keep source unchanged and repair FreeCAD."
        )
    return (
        "Correct only the reported BIM node and retry the failed working revision; do not "
        "recreate the program or change unrelated hierarchy outputs."
    )


class BIMCandidateError(RuntimeError):
    """A provider-correctable BIM failure with bounded structured details."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.details = dict(details or {})
        if not str(self.details.get("correction") or "").strip():
            self.details["correction"] = _default_correction(self.details)
        super().__init__(message)


def _fail(message: str, *, stage: str, **details: Any) -> BIMCandidateError:
    return BIMCandidateError(message, details={"stage": stage, **details})


def _payload(value: Any, *, context: str, require_domain_value: bool = False) -> dict[str, Any]:
    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif isinstance(value, Mapping) and not require_domain_value:
        payload = dict(value)
    else:
        raise _fail(
            f"{context} must be a value returned by the active BIM api.",
            stage="graph_contract",
            path=context,
        )
    expected = {"domain", "operation", "output_type", "arguments", "properties"}
    if set(payload) != expected:
        raise _fail(
            f"{context} has malformed BIM graph fields.",
            stage="graph_contract",
            path=context,
            missing=sorted(expected - set(payload)),
            unexpected=sorted(set(payload) - expected),
        )
    operation = str(payload.get("operation") or "")
    if (
        payload.get("domain") != "bim"
        or operation not in _OPERATIONS
        or payload.get("output_type") != operation
    ):
        raise _fail(
            f"{context} is not a supported BIM graph value.",
            stage="graph_contract",
            path=context,
            domain=payload.get("domain"),
            operation=operation,
            output_type=payload.get("output_type"),
        )
    if not isinstance(payload.get("arguments"), list) or not isinstance(
        payload.get("properties"), dict
    ):
        raise _fail(
            f"{context} arguments and properties must be serialized containers.",
            stage="graph_contract",
            path=context,
        )
    return payload


def _encoded(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"A BIM definition is not bounded JSON: {exc}",
            stage="graph_contract",
            exception_type=type(exc).__name__,
        ) from exc


def _graph_number(graph_id: str, *, context: str) -> int:
    match = _GRAPH_ID.fullmatch(graph_id)
    if match is None:
        raise _fail(
            f"{context}.graph_id is invalid.",
            stage="graph_identity",
            path=context,
            graph_id=graph_id,
        )
    return int(match.group(1))


def _value_from_payload(payload: Mapping[str, Any]) -> DomainValue:
    arguments = []
    for value in list(payload.get("arguments") or []):
        if isinstance(value, dict) and set(value) == {
            "domain",
            "operation",
            "output_type",
            "arguments",
            "properties",
        }:
            arguments.append(_value_from_payload(value))
        else:
            arguments.append(value)
    return DomainValue(
        domain="bim",
        operation=str(payload["operation"]),
        output_type=str(payload["output_type"]),
        arguments=tuple(arguments),
        properties=dict(payload["properties"]),
    )


def _canonical_from_api(
    api: Any,
    payload: Mapping[str, Any],
    parent: DomainValue | None,
) -> dict[str, Any]:
    operation = str(payload["operation"])
    arguments = list(payload["arguments"])
    properties = dict(payload["properties"])
    graph_id = str(properties.pop("graph_id"))
    if operation == "site":
        value = api.site(**properties)
    elif operation == "building":
        value = api.building(parent, **properties)
    elif operation == "level":
        value = api.level(parent, properties.pop("elevation"), **properties)
    elif operation == "wall":
        value = api.wall(parent, arguments[1], **properties)
    elif operation == "slab":
        value = api.slab(parent, arguments[1], **properties)
    elif operation == "structure":
        value = api.structure(parent, arguments[1], arguments[2], arguments[3], **properties)
    else:
        value = api.opening(
            parent,
            properties.pop("width"),
            properties.pop("height"),
            **properties,
        )
    canonical = value.to_payload()
    canonical["properties"]["graph_id"] = graph_id
    return canonical


def validate_bim_graph(
    raw_result: Mapping[str, Any],
    expected_outputs: Sequence[Mapping[str, Any]],
    *,
    require_domain_values: bool,
) -> dict[str, Any]:
    """Independently canonicalize the complete returned BIM graph."""

    from vibescript_bim_api import BIMDomainAPI

    expected_names = [str(item.get("name") or "") for item in expected_outputs]
    if list(raw_result) != expected_names:
        raise _fail(
            "BIM result keys must exactly match expected_outputs in declared order.",
            stage="result_contract",
            expected_names=expected_names,
            received_names=list(raw_result),
        )
    definitions: dict[str, dict[str, Any]] = {}
    graph_names: dict[str, str] = {}
    graph_numbers: dict[str, int] = {}
    for expected in expected_outputs:
        name = str(expected.get("name") or "")
        payload = _payload(
            raw_result[name],
            context=f"result[{name!r}]",
            require_domain_value=require_domain_values,
        )
        operation = str(payload["operation"])
        if operation != str(expected.get("type") or ""):
            raise _fail(
                f"BIM output {name!r} returned {operation!r}; expected "
                f"{expected.get('type')!r}.",
                stage="result_contract",
                output_name=name,
                operation=operation,
                expected_type=expected.get("type"),
            )
        arguments = list(payload["arguments"])
        properties = dict(payload["properties"])
        expected_arguments = _ARGUMENT_COUNTS[operation]
        expected_properties = _PROPERTY_NAMES[operation]
        if len(arguments) != expected_arguments or set(properties) != expected_properties:
            raise _fail(
                f"BIM output {name!r} has a malformed api.{operation} definition.",
                stage="graph_contract",
                output_name=name,
                operation=operation,
                expected_argument_count=expected_arguments,
                received_argument_count=len(arguments),
                missing_properties=sorted(expected_properties - set(properties)),
                unexpected_properties=sorted(set(properties) - expected_properties),
            )
        graph_id = str(properties.get("graph_id") or "")
        number = _graph_number(graph_id, context=f"result[{name!r}]")
        if graph_id in graph_names:
            raise _fail(
                f"BIM outputs {graph_names[graph_id]!r} and {name!r} reuse graph id "
                f"{graph_id!r}.",
                stage="graph_identity",
                graph_id=graph_id,
                first_output=graph_names[graph_id],
                second_output=name,
            )
        graph_names[graph_id] = name
        graph_numbers[graph_id] = number
        definitions[name] = payload

    parent_by_name: dict[str, str] = {}
    parent_graph_by_name: dict[str, str] = {}
    for name, payload in definitions.items():
        operation = str(payload["operation"])
        if operation == "site":
            continue
        raw_parent = list(payload["arguments"])[0]
        parent_payload = _payload(
            raw_parent,
            context=f"result[{name!r}].arguments[0]",
            require_domain_value=False,
        )
        parent_type = _PARENT_TYPES[operation]
        if parent_payload["operation"] != parent_type:
            raise _fail(
                f"api.{operation} output {name!r} requires a {parent_type} parent.",
                stage="hierarchy_contract",
                output_name=name,
                expected_parent_type=parent_type,
                observed_parent_type=parent_payload["operation"],
            )
        parent_graph = str(dict(parent_payload["properties"]).get("graph_id") or "")
        parent_name = graph_names.get(parent_graph)
        if parent_name is None:
            raise _fail(
                f"BIM output {name!r} refers to graph {parent_graph!r}, which is not "
                "a returned stable output.",
                stage="hierarchy_contract",
                output_name=name,
                parent_graph_id=parent_graph,
                returned_graph_ids=sorted(graph_names),
            )
        if _encoded(parent_payload) != _encoded(definitions[parent_name]):
            raise _fail(
                f"BIM output {name!r} embeds a modified copy of parent {parent_name!r}.",
                stage="hierarchy_contract",
                output_name=name,
                parent_output=parent_name,
            )
        if graph_numbers[parent_graph] >= graph_numbers[
            str(dict(payload["properties"])["graph_id"])
        ]:
            raise _fail(
                f"BIM output {name!r} was created before its parent {parent_name!r}.",
                stage="hierarchy_contract",
                output_name=name,
                parent_output=parent_name,
            )
        parent_by_name[name] = parent_name
        parent_graph_by_name[name] = parent_graph

    # Public API reconstruction makes the host and worker enforce the same
    # explicit signatures without trusting either serialized property set.
    api = BIMDomainAPI(_OPERATIONS, _OPERATIONS)
    values_by_graph: dict[str, DomainValue] = {}
    for graph_id, name in sorted(graph_names.items(), key=lambda item: graph_numbers[item[0]]):
        payload = definitions[name]
        parent_value = (
            values_by_graph[parent_graph_by_name[name]] if name in parent_graph_by_name else None
        )
        try:
            canonical = _canonical_from_api(api, payload, parent_value)
        except (TypeError, ValueError) as exc:
            raise _fail(
                f"BIM output {name!r} failed api.{payload['operation']} validation: {exc}",
                stage="api_revalidation",
                output_name=name,
                operation=payload["operation"],
                exception_type=type(exc).__name__,
            ) from exc
        if _encoded(canonical) != _encoded(payload):
            raise _fail(
                f"BIM output {name!r} differs from the canonical api definition.",
                stage="api_revalidation",
                output_name=name,
                operation=payload["operation"],
            )
        values_by_graph[graph_id] = _value_from_payload(canonical)

    # Openings must fit one host segment and cannot overlap another opening in
    # the same wall/segment.  This turns native cut ambiguity into a source error.
    opening_intervals: dict[tuple[str, int], list[tuple[float, float, str]]] = {}
    for name, payload in definitions.items():
        if payload["operation"] != "opening":
            continue
        host_name = parent_by_name[name]
        host = definitions[host_name]
        host_arguments = list(host["arguments"])
        host_properties = dict(host["properties"])
        points = list(host_arguments[1])
        segment_count = len(points) if bool(host_properties["closed"]) else len(points) - 1
        properties = dict(payload["properties"])
        segment = int(properties["segment"])
        if not 0 <= segment < segment_count:
            raise _fail(
                f"Opening {name!r} selects segment {segment}, but wall {host_name!r} has "
                f"segments 0-{segment_count - 1}.",
                stage="opening_fit",
                output_name=name,
                host_output=host_name,
                segment=segment,
                segment_count=segment_count,
            )
        first = points[segment]
        second = points[(segment + 1) % len(points)]
        segment_length = math.hypot(
            float(second[0]) - float(first[0]),
            float(second[1]) - float(first[1]),
        )
        start = float(properties["offset"])
        end = start + float(properties["width"])
        if end > segment_length + _EPSILON:
            raise _fail(
                f"Opening {name!r} extends beyond wall segment {segment}.",
                stage="opening_fit",
                output_name=name,
                host_output=host_name,
                segment=segment,
                segment_length=segment_length,
                opening_interval=[start, end],
            )
        top = float(properties["sill"]) + float(properties["height"])
        if top > float(host_properties["height"]) + _EPSILON:
            raise _fail(
                f"Opening {name!r} extends above wall {host_name!r}.",
                stage="opening_fit",
                output_name=name,
                host_output=host_name,
                wall_height=float(host_properties["height"]),
                opening_top=top,
            )
        key = (parent_graph_by_name[name], segment)
        for other_start, other_end, other_name in opening_intervals.setdefault(key, []):
            if start < other_end - _EPSILON and other_start < end - _EPSILON:
                raise _fail(
                    f"Openings {other_name!r} and {name!r} overlap on wall segment {segment}.",
                    stage="opening_fit",
                    output_name=name,
                    other_output=other_name,
                    host_output=host_name,
                    segment=segment,
                )
        opening_intervals[key].append((start, end, name))

    ordered_names = [
        graph_names[graph_id]
        for graph_id in sorted(graph_names, key=lambda value: graph_numbers[value])
    ]
    return {
        "definitions": definitions,
        "graph_names": graph_names,
        "graph_numbers": graph_numbers,
        "parent_by_name": parent_by_name,
        "parent_graph_by_name": parent_graph_by_name,
        "ordered_names": ordered_names,
    }


def _number(value: Any, *, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"{path} must be a finite number.", stage="native_input", path=path)
    clean = float(value)
    if not math.isfinite(clean):
        raise _fail(f"{path} must be finite.", stage="native_input", path=path)
    return clean


def _vector2(value: Any, *, path: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise _fail(f"{path} must be [x, y].", stage="native_input", path=path)
    return _number(value[0], path=f"{path}[0]"), _number(value[1], path=f"{path}[1]")


def _placement(value: Any):
    import FreeCAD as App

    if not isinstance(value, dict) or set(value) != {"position", "rotation"}:
        raise _fail(
            "A BIM placement must contain exactly position and rotation.",
            stage="native_input",
        )
    position = value["position"]
    rotation = value["rotation"]
    if not isinstance(position, list) or len(position) != 3:
        raise _fail("placement.position must be [x,y,z].", stage="native_input")
    if not isinstance(rotation, list) or len(rotation) != 4:
        raise _fail(
            "placement.rotation must be quaternion [x,y,z,w].", stage="native_input"
        )
    return App.Placement(
        App.Vector(*(_number(item, path="placement.position") for item in position)),
        App.Rotation(*(_number(item, path="placement.rotation") for item in rotation)),
    )


def _placement_data(value: Any) -> dict[str, list[float]]:
    return {
        "position": [float(value.Base.x), float(value.Base.y), float(value.Base.z)],
        "rotation": [float(item) for item in value.Rotation.Q],
    }


def _opening_placement(
    wall_payload: Mapping[str, Any],
    opening_properties: Mapping[str, Any],
):
    import FreeCAD as App

    points = list(wall_payload["arguments"])[1]
    segment = int(opening_properties["segment"])
    first = _vector2(points[segment], path="opening.host.points")
    second = _vector2(
        points[(segment + 1) % len(points)], path="opening.host.points"
    )
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    length = math.hypot(dx, dy)
    tangent_x = dx / length
    tangent_y = dy / length
    offset = float(opening_properties["offset"])
    matrix = App.Matrix()
    # Columns map local profile X to wall tangent, local Y to global +Z, and
    # local Z to the wall normal.  For a +X wall this is the native +90deg-X
    # placement used by Arch's own Opening-only preset.
    matrix.A11 = tangent_x
    matrix.A21 = tangent_y
    matrix.A31 = 0.0
    matrix.A12 = 0.0
    matrix.A22 = 0.0
    matrix.A32 = 1.0
    matrix.A13 = tangent_y
    matrix.A23 = -tangent_x
    matrix.A33 = 0.0
    matrix.A14 = first[0] + tangent_x * offset
    matrix.A24 = first[1] + tangent_y * offset
    matrix.A34 = float(opening_properties["sill"])
    matrix.A44 = 1.0
    return App.Placement(matrix)


def _native_type(obj: Any) -> tuple[str, str, str, str]:
    try:
        from draftutils.utils import get_type

        arch_type = str(get_type(obj) or "")
    except Exception:
        arch_type = ""
    return (
        str(getattr(obj, "TypeId", "") or ""),
        type(getattr(obj, "Proxy", None)).__name__,
        arch_type,
        str(getattr(obj, "IfcType", "") or ""),
    )


def _assert_native(obj: Any, output_name: str, operation: str, *, role: str = "") -> None:
    observed = _native_type(obj)
    expected = list(_EXPECTED_NATIVE[operation])
    if operation == "structure":
        expected[3] = _ROLE_IFC[role]
    if observed != tuple(expected):
        raise _fail(
            f"Native api.{operation} output {output_name!r} changed object contract.",
            stage="native_object_contract",
            output_name=output_name,
            operation=operation,
            expected={
                "native_type": expected[0],
                "proxy_class": expected[1],
                "arch_type": expected[2],
                "ifc_type": expected[3],
            },
            observed={
                "native_type": observed[0],
                "proxy_class": observed[1],
                "arch_type": observed[2],
                "ifc_type": observed[3],
            },
        )
    states = [str(item) for item in list(getattr(obj, "State", []) or [])]
    if any(item.lower() in {"invalid", "error"} for item in states):
        raise _fail(
            f"Native api.{operation} output {output_name!r} is invalid.",
            stage="native_object_state",
            output_name=output_name,
            state=states,
        )


def _shape_facts(shape: Any, *, max_subelements: int) -> dict[str, Any]:
    from vibescript_part_worker import part_shape_facts

    return part_shape_facts(shape, max_subelements=max_subelements)


def _export_shape(
    shape: Any,
    target: Path,
    *,
    output_name: str,
    role: str,
) -> None:
    try:
        shape.exportBrep(str(target))
    except Exception as exc:
        raise _fail(
            f"Could not export {role} for BIM output {output_name!r}: {exc}",
            stage="artifact_export",
            output_name=output_name,
            artifact_role=role,
            exception_type=type(exc).__name__,
        ) from exc
    if not target.is_file() or target.stat().st_size <= 0:
        raise _fail(
            f"Could not export {role} for BIM output {output_name!r}.",
            stage="artifact_export",
            output_name=output_name,
            artifact_role=role,
        )


def _shape_volume(obj: Any) -> float:
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return 0.0
    return float(shape.Volume)


def _group_graph_ids(obj: Any, object_graph: Mapping[int, str]) -> list[str]:
    return [
        object_graph[id(child)]
        for child in list(getattr(obj, "Group", []) or [])
        if id(child) in object_graph
    ]


def validate_and_build_bim(
    document: Any,
    raw_result: Mapping[str, Any],
    expected_outputs: list[dict[str, Any]],
    root: Path,
    *,
    max_shape_subelements: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Create, recompute, validate, and export a complete native BIM graph."""

    import Arch
    import Draft
    import FreeCAD as App
    import Part

    graph = validate_bim_graph(
        raw_result,
        expected_outputs,
        require_domain_values=True,
    )
    definitions: dict[str, dict[str, Any]] = graph["definitions"]
    parent_by_name: dict[str, str] = graph["parent_by_name"]
    objects: dict[str, Any] = {}
    bases: dict[str, Any] = {}
    graph_objects: dict[str, Any] = {}
    level_for_element: dict[str, str] = {}
    active_name = ""
    active_operation = ""

    def add_to_parent(name: str, obj: Any) -> None:
        parent_name = parent_by_name[name]
        parent = objects[parent_name]
        parent.addObject(obj)

    def recompute_native(*, output_name: str, operation: str) -> None:
        try:
            recomputed = document.recompute()
        except Exception as exc:
            raise _fail(
                f"Native BIM recompute failed for {output_name or 'the candidate graph'}: {exc}",
                stage="native_recompute",
                output_name=output_name,
                operation=operation,
                native_exception=type(exc).__name__,
                native_error=str(exc),
            ) from exc
        if type(recomputed) is not int or recomputed < 0:
            raise _fail(
                f"Native BIM recompute returned an invalid feature count for "
                f"{output_name or 'the candidate graph'}.",
                stage="native_recompute",
                output_name=output_name,
                operation=operation,
                recompute_result=recomputed,
            )

    try:
        for name in graph["ordered_names"]:
            payload = definitions[name]
            operation = str(payload["operation"])
            active_name = name
            active_operation = operation
            arguments = list(payload["arguments"])
            properties = dict(payload["properties"])
            label = str(properties.get("label") or name)
            if operation == "opening":
                continue
            if operation == "site":
                obj = Arch.makeSite(name=label)
                obj.Address = str(properties["address"])
                obj.PostalCode = str(properties["postal_code"])
                obj.City = str(properties["city"])
                obj.Region = str(properties["region"])
                obj.Country = str(properties["country"])
                obj.Latitude = float(properties["latitude"])
                obj.Longitude = float(properties["longitude"])
                obj.Elevation = float(properties["elevation"])
            elif operation == "building":
                obj = Arch.makeBuilding(name=label)
                add_to_parent(name, obj)
            elif operation == "level":
                obj = Arch.makeFloor(name=label)
                obj.Height = float(properties["height"])
                obj.Placement = App.Placement(
                    App.Vector(0.0, 0.0, float(properties["elevation"])),
                    App.Rotation(),
                )
                add_to_parent(name, obj)
            elif operation == "wall":
                points = [
                    App.Vector(*_vector2(point, path=f"api.wall.points[{index}]"), 0.0)
                    for index, point in enumerate(arguments[1])
                ]
                base = Draft.make_wire(points, closed=bool(properties["closed"]), face=False)
                if base is None:
                    raise RuntimeError("Draft.make_wire returned no wall baseline.")
                base.Label = f"{label} Baseline"
                obj = Arch.makeWall(
                    base,
                    height=float(properties["height"]),
                    width=float(properties["width"]),
                    align=str(properties["alignment"]).title(),
                    offset=float(properties["offset"]),
                    name=label,
                )
                bases[name] = base
                add_to_parent(name, obj)
                level_for_element[name] = parent_by_name[name]
            elif operation == "slab":
                points = [
                    App.Vector(
                        *_vector2(point, path=f"api.slab.boundary[{index}]"),
                        float(properties["top_offset"]),
                    )
                    for index, point in enumerate(arguments[1])
                ]
                base = Draft.make_wire(points, closed=True, face=True)
                if base is None:
                    raise RuntimeError("Draft.make_wire returned no slab profile.")
                base.Label = f"{label} Profile"
                obj = Arch.makeStructure(
                    base,
                    height=float(properties["thickness"]),
                    name=label,
                )
                obj.IfcType = "Slab"
                obj.Normal = App.Vector(0.0, 0.0, -1.0)
                bases[name] = base
                add_to_parent(name, obj)
                level_for_element[name] = parent_by_name[name]
            else:
                obj = Arch.makeStructure(
                    length=float(arguments[1]),
                    width=float(arguments[2]),
                    height=float(arguments[3]),
                    name=label,
                )
                obj.IfcType = _ROLE_IFC[str(properties["role"])]
                obj.Placement = _placement(properties["placement"])
                add_to_parent(name, obj)
                level_for_element[name] = parent_by_name[name]
            if obj is None:
                raise RuntimeError(f"The native api.{operation} factory returned no object.")
            obj.Label = label
            objects[name] = obj
            graph_id = str(properties["graph_id"])
            graph_objects[graph_id] = obj

        recompute_native(output_name="", operation="hierarchy")
        pre_opening_volumes = {
            name: _shape_volume(obj)
            for name, obj in objects.items()
            if definitions[name]["operation"] == "wall"
        }

        for name in graph["ordered_names"]:
            payload = definitions[name]
            if payload["operation"] != "opening":
                continue
            active_name = name
            active_operation = "opening"
            properties = dict(payload["properties"])
            host_name = parent_by_name[name]
            host = objects[host_name]
            host_payload = definitions[host_name]
            width = float(properties["width"])
            height = float(properties["height"])
            base = document.addObject("Part::Feature", f"CandidateOpeningBase{len(bases)}")
            base.Shape = Part.makePolygon(
                [
                    App.Vector(0.0, 0.0, 0.0),
                    App.Vector(width, 0.0, 0.0),
                    App.Vector(width, height, 0.0),
                    App.Vector(0.0, height, 0.0),
                    App.Vector(0.0, 0.0, 0.0),
                ]
            )
            base.Placement = _opening_placement(host_payload, properties)
            base.Label = f"{str(properties.get('label') or name)} Profile"
            recompute_native(output_name=name, operation="opening_base")
            obj = Arch.makeWindow(
                base,
                width=width,
                height=height,
                parts=[],
                name=str(properties.get("label") or name),
            )
            if obj is None:
                raise RuntimeError("Arch.makeWindow returned no Opening Element.")
            obj.IfcType = "Opening Element"
            obj.Hosts = [host]
            resolved_depth = float(properties["hole_depth"])
            if resolved_depth <= 0.0:
                resolved_depth = float(dict(host_payload["properties"])["width"]) + 100.0
            obj.HoleDepth = resolved_depth
            level_name = level_for_element[host_name]
            objects[level_name].addObject(obj)
            obj.Label = str(properties.get("label") or name)
            objects[name] = obj
            bases[name] = base
            level_for_element[name] = level_name
            graph_objects[str(properties["graph_id"])] = obj

        recompute_native(output_name="", operation="completed_hierarchy")
    except BIMCandidateError:
        raise
    except Exception as exc:
        raise _fail(
            f"The isolated native BIM graph failed: {exc}",
            stage="native_factory_or_recompute",
            output_name=active_name,
            operation=active_operation,
            native_exception=type(exc).__name__,
            native_error=str(exc),
        ) from exc

    object_graph = {id(obj): graph_id for graph_id, obj in graph_objects.items()}
    opening_names_by_wall: dict[str, list[str]] = {}
    for name, parent_name in parent_by_name.items():
        if definitions[name]["operation"] == "opening":
            opening_names_by_wall.setdefault(parent_name, []).append(name)
    wall_volume_diagnostics: dict[str, dict[str, float]] = {}
    for name, obj in objects.items():
        if definitions[name]["operation"] != "wall":
            continue
        properties = dict(definitions[name]["properties"])
        before = float(pre_opening_volumes[name])
        after = _shape_volume(obj)
        expected_delta = sum(
            float(dict(definitions[opening]["properties"])["width"])
            * float(dict(definitions[opening]["properties"])["height"])
            * float(properties["width"])
            for opening in opening_names_by_wall.get(name, [])
        )
        observed_delta = before - after
        tolerance = max(1.0e-4, abs(expected_delta) * 1.0e-7)
        if abs(observed_delta - expected_delta) > tolerance:
            raise _fail(
                f"Wall {name!r} did not receive the exact validated opening cuts.",
                stage="native_opening_cut",
                output_name=name,
                pre_opening_volume=before,
                final_volume=after,
                expected_delta=expected_delta,
                observed_delta=observed_delta,
                tolerance=tolerance,
                openings=opening_names_by_wall.get(name, []),
            )
        wall_volume_diagnostics[name] = {
            "pre_opening_volume": before,
            "final_volume": after,
            "expected_opening_volume_delta": expected_delta,
            "observed_opening_volume_delta": observed_delta,
        }

    outputs: list[dict[str, Any]] = []
    validation_outputs: list[dict[str, Any]] = []
    for index, expected in enumerate(expected_outputs):
        name = str(expected["name"])
        payload = definitions[name]
        operation = str(payload["operation"])
        properties = dict(payload["properties"])
        obj = objects[name]
        _assert_native(
            obj,
            name,
            operation,
            role=str(properties.get("role") or ""),
        )
        native_type, proxy_class, arch_type, ifc_type = _native_type(obj)
        data: dict[str, Any] = {
            "graph_id": str(properties["graph_id"]),
            "native_type": native_type,
            "proxy_class": proxy_class,
            "arch_type": arch_type,
            "ifc_type": ifc_type,
            "label": str(obj.Label),
            "placement": _placement_data(obj.Placement),
            "parent_graph_id": str(graph["parent_graph_by_name"].get(name) or ""),
            "group_graph_ids": _group_graph_ids(obj, object_graph),
            "shape_present": bool(
                hasattr(obj, "Shape") and not getattr(obj, "Shape").isNull()
            ),
        }
        if operation == "site":
            data.update(
                {
                    "address": str(obj.Address),
                    "postal_code": str(obj.PostalCode),
                    "city": str(obj.City),
                    "region": str(obj.Region),
                    "country": str(obj.Country),
                    "latitude": float(obj.Latitude),
                    "longitude": float(obj.Longitude),
                    "elevation": float(obj.Elevation.Value),
                }
            )
        elif operation == "building":
            data["building_type"] = str(obj.BuildingType)
        elif operation == "level":
            data.update(
                {
                    "elevation": float(obj.Placement.Base.z),
                    "height": float(obj.Height.Value),
                    "level_offset": float(obj.LevelOffset.Value),
                }
            )
        elif operation == "wall":
            base = bases[name]
            points = [
                [float(point.x), float(point.y), float(point.z)]
                for point in list(base.Points or [])
            ]
            data.update(
                {
                    "base_native_type": str(base.TypeId),
                    "base_proxy_class": type(getattr(base, "Proxy", None)).__name__,
                    "base_arch_type": "Wire",
                    "base_placement": _placement_data(base.Placement),
                    "points": points,
                    "closed": bool(base.Closed),
                    "width": float(obj.Width.Value),
                    "height": float(obj.Height.Value),
                    "alignment": str(obj.Align).lower(),
                    "offset": float(obj.Offset.Value),
                    "opening_graph_ids": [
                        str(dict(definitions[opening]["properties"])["graph_id"])
                        for opening in opening_names_by_wall.get(name, [])
                    ],
                    **wall_volume_diagnostics[name],
                }
            )
        elif operation == "slab":
            base = bases[name]
            data.update(
                {
                    "base_native_type": str(base.TypeId),
                    "base_proxy_class": type(getattr(base, "Proxy", None)).__name__,
                    "base_arch_type": "Wire",
                    "base_placement": _placement_data(base.Placement),
                    "boundary": [
                        [float(point.x), float(point.y), float(point.z)]
                        for point in list(base.Points or [])
                    ],
                    "thickness": float(obj.Height.Value),
                    "normal": [float(value) for value in obj.Normal],
                    "shape_volume": _shape_volume(obj),
                }
            )
        elif operation == "structure":
            data.update(
                {
                    "length": float(obj.Length.Value),
                    "width": float(obj.Width.Value),
                    "height": float(obj.Height.Value),
                    "role": str(properties["role"]),
                    "shape_volume": _shape_volume(obj),
                }
            )
        else:
            base = bases[name]
            host_name = parent_by_name[name]
            host_properties = dict(definitions[host_name]["properties"])
            data.update(
                {
                    "level_graph_id": str(
                        dict(definitions[level_for_element[name]]["properties"])["graph_id"]
                    ),
                    "host_graph_id": str(
                        dict(definitions[host_name]["properties"])["graph_id"]
                    ),
                    "host_count": len(list(obj.Hosts or [])),
                    "base_native_type": str(base.TypeId),
                    "base_proxy_class": type(getattr(base, "Proxy", None)).__name__,
                    "base_placement": _placement_data(base.Placement),
                    "width": float(obj.Width.Value),
                    "height": float(obj.Height.Value),
                    "segment": int(properties["segment"]),
                    "offset": float(properties["offset"]),
                    "sill": float(properties["sill"]),
                    "hole_depth": float(obj.HoleDepth.Value),
                    "requested_hole_depth": float(properties["hole_depth"]),
                    "host_wall_width": float(host_properties["width"]),
                    "expected_host_volume_delta": float(properties["width"])
                    * float(properties["height"])
                    * float(host_properties["width"]),
                    "opening_shape_null": bool(obj.Shape.isNull()),
                }
            )

        item: dict[str, Any] = {
            "name": name,
            "type": operation,
            "definition": payload,
            "bim_data": data,
        }
        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull():
            if not shape.isValid():
                raise _fail(
                    f"BIM output {name!r} produced an invalid native Shape.",
                    stage="native_shape_validation",
                    output_name=name,
                    operation=operation,
                )
            facts = _shape_facts(shape, max_subelements=max_shape_subelements)
            if operation in {"wall", "slab", "structure"} and (
                int(facts.get("solids") or 0) < 1 or float(shape.Volume) <= 0.0
            ):
                raise _fail(
                    f"BIM output {name!r} did not produce a positive solid.",
                    stage="native_shape_validation",
                    output_name=name,
                    operation=operation,
                    facts=facts,
                )
            relative = Path("outputs") / f"output-{index:03d}.brep"
            _export_shape(
                shape,
                root / relative,
                output_name=name,
                role="published Shape",
            )
            item.update(
                {
                    "artifact_kind": "brep",
                    "artifact_path": str(relative),
                    "facts": facts,
                }
            )
        if name in bases:
            base_shape = bases[name].Shape
            if base_shape.isNull() or not base_shape.isValid():
                raise _fail(
                    f"BIM output {name!r} has an invalid native base profile.",
                    stage="native_base_validation",
                    output_name=name,
                    operation=operation,
                )
            base_facts = _shape_facts(base_shape, max_subelements=max_shape_subelements)
            relative = Path("outputs") / f"base-{index:03d}.brep"
            _export_shape(
                base_shape,
                root / relative,
                output_name=name,
                role="parametric base profile",
            )
            data["base_artifact_path"] = str(relative)
            data["base_facts"] = base_facts

        validation_outputs.append(
            {
                "name": name,
                "type": operation,
                "graph_id": str(properties["graph_id"]),
                "native_type": native_type,
                "proxy_class": proxy_class,
                "arch_type": arch_type,
                "ifc_type": ifc_type,
                "parent_graph_id": str(graph["parent_graph_by_name"].get(name) or ""),
                "group_graph_ids": list(data["group_graph_ids"]),
                "shape_present": bool(data["shape_present"]),
            }
        )
        outputs.append(item)

    return outputs, {
        "schema": VALIDATION_SCHEMA,
        "native_object_count": len(outputs),
        "native_base_count": len(bases),
        "site_count": sum(item["type"] == "site" for item in validation_outputs),
        "building_count": sum(item["type"] == "building" for item in validation_outputs),
        "level_count": sum(item["type"] == "level" for item in validation_outputs),
        "wall_count": sum(item["type"] == "wall" for item in validation_outputs),
        "slab_count": sum(item["type"] == "slab" for item in validation_outputs),
        "structure_count": sum(
            item["type"] == "structure" for item in validation_outputs
        ),
        "opening_count": sum(item["type"] == "opening" for item in validation_outputs),
        "outputs": validation_outputs,
    }
