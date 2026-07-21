# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native evaluator for the Part Design VibeScript domain."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from vibescript_domain_api import DomainValue
from vibescript_part_worker import configure_part_references, part_shape_facts
from vibescript_sketcher_worker import (
    configure_sketcher_references,
    populate_sketch_without_solving,
)


class PartDesignCandidateError(RuntimeError):
    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None):
        self.details = dict(details or {})
        super().__init__(message)


def configure_partdesign_references(root: Path, entries: list[dict[str, Any]]) -> None:
    """Authenticate the same detached references for profile and shape readers."""

    configure_part_references(root, entries)
    configure_sketcher_references(root, entries)


def _payload(value: Any, *, context: str) -> dict[str, Any]:
    if isinstance(value, DomainValue):
        result = value.to_payload()
    elif isinstance(value, Mapping):
        result = dict(value)
    else:
        raise PartDesignCandidateError(
            f"{context} must be a Part Design api value.",
            details={"stage": "graph_contract", "context": context},
        )
    if set(result) != {"domain", "operation", "output_type", "arguments", "properties"}:
        raise PartDesignCandidateError(
            f"{context} has malformed graph fields.",
            details={"stage": "graph_contract", "context": context},
        )
    if str(result.get("domain") or "") != "partdesign":
        raise PartDesignCandidateError(
            f"{context} belongs to another domain.",
            details={"stage": "graph_contract", "context": context},
        )
    return result


def _argument(payload: Mapping[str, Any], index: int, *, context: str) -> Any:
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or index >= len(arguments):
        raise PartDesignCandidateError(
            f"{context} is missing argument {index}.",
            details={"stage": "graph_contract", "context": context},
        )
    return arguments[index]


def _properties(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = payload.get("properties")
    if not isinstance(value, Mapping):
        raise PartDesignCandidateError(
            "A Part Design graph node has malformed properties.",
            details={"stage": "graph_contract"},
        )
    return dict(value)


def _graph_id(payload: Mapping[str, Any], *, context: str) -> str:
    value = str(_properties(payload).get("graph_id") or "")
    if not value:
        raise PartDesignCandidateError(
            f"{context} has no stable graph id.",
            details={"stage": "graph_identity", "context": context},
        )
    return value


def _origin_feature(body: Any, role: str) -> Any:
    origin = getattr(body, "Origin", None)
    matches = [
        item
        for item in list(getattr(origin, "OriginFeatures", []) or [])
        if str(getattr(item, "Role", "") or "") == role
    ]
    if len(matches) != 1:
        raise PartDesignCandidateError(
            f"Body origin does not expose exactly one {role}.",
            details={"stage": "origin_reference", "role": role},
        )
    return matches[0]


def _profile_axis(profile: Any, axis: str) -> tuple[Any, list[str]]:
    key = str(axis or "").upper()
    if key in {"H", "V", "N"}:
        return profile, [f"{key}_Axis"]
    return _origin_feature(profile.getParentGeoFeatureGroup(), f"{key}_Axis"), [""]


def _set_label(obj: Any, properties: Mapping[str, Any], fallback: str) -> None:
    obj.Label = str(properties.get("label") or fallback)


def _as_sketcher_payload(value: Any) -> Any:
    """Translate the embedded profile graph to the Sketcher evaluator contract."""

    if isinstance(value, Mapping):
        return {
            str(key): (
                "sketcher"
                if key == "domain" and item == "partdesign"
                else _as_sketcher_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_as_sketcher_payload(item) for item in value]
    return value


def _sketch_validation(sketch: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    properties = _properties(payload)
    try:
        solver_code = int(sketch.solve())
    except Exception as exc:
        raise PartDesignCandidateError(
            f"Native Sketcher solve failed: {exc}",
            details={
                "stage": "sketch_solver",
                "graph_id": properties.get("graph_id"),
                "native_error": str(exc),
            },
        ) from exc
    sketch.Document.recompute()
    conflicts = sorted(int(value) for value in list(getattr(sketch, "ConflictingConstraints", []) or []))
    redundant = sorted(int(value) for value in list(getattr(sketch, "RedundantConstraints", []) or []))
    malformed = sorted(int(value) for value in list(getattr(sketch, "MalformedConstraints", []) or []))
    shape = getattr(sketch, "Shape", None)
    wires = list(getattr(shape, "Wires", []) or []) if shape is not None else []
    closed = sum(bool(wire.isClosed()) for wire in wires)
    dof = int(getattr(sketch, "DoF", getattr(sketch, "SolverDOF", 0)) or 0)
    result = {
        "graph_id": str(properties.get("graph_id") or ""),
        "object_name": str(getattr(sketch, "Name", "") or ""),
        "label": str(getattr(sketch, "Label", "") or ""),
        "solver_code": solver_code,
        "geometry_count": int(getattr(sketch, "GeometryCount", 0) or 0),
        "constraint_count": int(getattr(sketch, "ConstraintCount", 0) or 0),
        "degrees_of_freedom": dof,
        "fully_constrained": dof == 0,
        "conflicting_constraints": conflicts,
        "redundant_constraints": redundant,
        "malformed_constraints": malformed,
        "wire_count": len(wires),
        "closed_wire_count": closed,
        "profile_ready": bool(wires and closed == len(wires)),
        "plane": str(properties.get("plane") or ""),
        "z_offset_mm": float(properties.get("z_offset_mm") or 0.0),
    }
    if solver_code != 0 or conflicts or redundant or malformed:
        raise PartDesignCandidateError(
            "The Part Design profile contains rejected Sketcher constraints.",
            details={"stage": "sketch_validation", **result},
        )
    if bool(properties.get("require_fully_constrained")) and dof != 0:
        raise PartDesignCandidateError(
            "The profile is not fully constrained.",
            details={"stage": "sketch_underconstrained", **result},
        )
    if bool(properties.get("require_closed_profile")) and not result["profile_ready"]:
        raise PartDesignCandidateError(
            "The profile does not contain only closed wires.",
            details={"stage": "sketch_profile", **result},
        )
    return result


def _build_sketch(
    body: Any,
    payload: Mapping[str, Any],
    memo: dict[str, Any],
    sketch_evidence: list[dict[str, Any]],
) -> Any:
    graph_id = _graph_id(payload, context="api.sketch")
    if graph_id in memo:
        return memo[graph_id]
    properties = _properties(payload)
    name = f"Profile_{graph_id}"
    sketch = body.newObject("Sketcher::SketchObject", name)
    if sketch is None:
        raise PartDesignCandidateError("FreeCAD did not create the profile sketch.")
    _set_label(sketch, properties, name)
    plane = str(properties.get("plane") or "XY")
    support = (_origin_feature(body, f"{plane}_Plane"), [""])
    if hasattr(sketch, "AttachmentSupport"):
        sketch.AttachmentSupport = [support]
    else:
        sketch.Support = [support]
    sketch.MapMode = "FlatFace"
    offset = float(properties.get("z_offset_mm") or 0.0)
    if offset:
        import FreeCAD as App

        sketch.AttachmentOffset = App.Placement(
            App.Vector(0.0, 0.0, offset), App.Rotation()
        )
    populate_sketch_without_solving(
        sketch,
        _as_sketcher_payload(payload),
        replace_existing=False,
    )
    body.Document.recompute()
    sketch_evidence.append(_sketch_validation(sketch, payload))
    memo[graph_id] = sketch
    return sketch


def _subshape_geometry(shape: Any, kind: str, index: int, subshape: Any) -> dict[str, Any]:
    center = getattr(subshape, "CenterOfMass", None)
    geometry = None
    try:
        geometry = getattr(subshape, "Surface" if kind == "face" else "Curve")
    except Exception:
        pass
    result: dict[str, Any] = {
        "name": f"{kind.title()}{index}",
        "element_type": kind,
        "geometry_type": type(geometry).__name__.removeprefix("Part.") if geometry is not None else "Undefined",
        "center_mm": (
            [float(center.x), float(center.y), float(center.z)]
            if center is not None
            else None
        ),
    }
    if kind == "face":
        result["area_mm2"] = float(subshape.Area)
        try:
            u_min, u_max, v_min, v_max = (float(value) for value in subshape.ParameterRange)
            normal = subshape.normalAt((u_min + u_max) / 2.0, (v_min + v_max) / 2.0)
            result["normal"] = [float(normal.x), float(normal.y), float(normal.z)]
        except Exception:
            result["normal"] = None
    else:
        result["length_mm"] = float(subshape.Length)
        try:
            first, last = (float(value) for value in subshape.ParameterRange)
            tangent = subshape.tangentAt((first + last) / 2.0)
            result["direction"] = [float(tangent.x), float(tangent.y), float(tangent.z)]
        except Exception:
            result["direction"] = None
        radius = getattr(geometry, "Radius", None)
        if radius is not None:
            result["radius_mm"] = float(radius)
    return result


def _unit(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    length = math.sqrt(sum(float(item) ** 2 for item in value))
    if length <= 1.0e-12:
        return None
    return [float(item) / length for item in value]


def _angle_matches(actual: Any, requested: Any, tolerance: float) -> bool:
    left = _unit(actual)
    right = _unit(requested)
    if left is None or right is None:
        return False
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))
    return math.degrees(math.acos(dot)) <= tolerance


def _query_subelements(shape: Any, selection: Mapping[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    mode = str(selection.get("type") or "")
    if mode == "all_edges":
        details = [
            _subshape_geometry(shape, "edge", index, edge)
            for index, edge in enumerate(list(shape.Edges), start=1)
        ]
        if not details:
            raise PartDesignCandidateError("The selected feature has no edges.")
        return [item["name"] for item in details], details
    kind = str(selection.get("element_type") or "")
    values = list(shape.Faces if kind == "face" else shape.Edges)
    details = [
        _subshape_geometry(shape, kind, index, value)
        for index, value in enumerate(values, start=1)
    ]

    def matches(item: Mapping[str, Any]) -> bool:
        geometry_type = str(selection.get("geometry_type") or "")
        if geometry_type and str(item.get("geometry_type") or "").lower() != geometry_type.lower():
            return False
        if "normal" in selection and not _angle_matches(
            item.get("normal"),
            selection["normal"],
            float(selection.get("normal_tolerance_degrees", 1.0)),
        ):
            return False
        if "direction" in selection and not _angle_matches(
            item.get("direction"),
            selection["direction"],
            float(selection.get("direction_tolerance_degrees", 1.0)),
        ):
            return False
        if "radius" in selection:
            radius = item.get("radius_mm")
            if radius is None or abs(float(radius) - float(selection["radius"])) > float(
                selection.get("radius_tolerance", 1.0e-6)
            ):
                return False
        area = item.get("area_mm2")
        if "min_area" in selection and (area is None or float(area) < float(selection["min_area"])):
            return False
        if "max_area" in selection and (area is None or float(area) > float(selection["max_area"])):
            return False
        length = item.get("length_mm")
        if "min_length" in selection and (
            length is None or float(length) < float(selection["min_length"])
        ):
            return False
        if "max_length" in selection and (
            length is None or float(length) > float(selection["max_length"])
        ):
            return False
        if "near_point" in selection:
            center = item.get("center_mm")
            if center is None or math.dist(center, selection["near_point"]) > float(
                selection.get("max_distance", 1.0e-6)
            ):
                return False
        return True

    selected = [item for item in details if matches(item)]
    expected = int(selection.get("expected_count") or 0)
    if len(selected) != expected:
        raise PartDesignCandidateError(
            "A geometric Part Design selection did not match its declared cardinality.",
            details={
                "stage": "topology_selection",
                "selection": dict(selection),
                "expected_count": expected,
                "actual_count": len(selected),
                "matches": selected,
                "available": details[:256],
            },
        )
    return [str(item["name"]) for item in selected], selected


def _build_feature(
    body: Any,
    payload: Mapping[str, Any],
    memo: dict[str, Any],
    sketch_evidence: list[dict[str, Any]],
) -> Any:
    operation = str(payload.get("operation") or "")
    if operation == "sketch":
        return _build_sketch(body, payload, memo, sketch_evidence)
    graph_id = _graph_id(payload, context=f"api.{operation}")
    if graph_id in memo:
        return memo[graph_id]
    properties = _properties(payload)
    name = f"Feature_{graph_id}"
    additive_base: Any | None = None
    subtractive_base: Any | None = None
    if operation == "pad":
        base_value = properties.get("base")
        if base_value is not None:
            additive_base = _build_feature(
                body,
                _payload(base_value, context="api.pad.base"),
                memo,
                sketch_evidence,
            )
        profile = _build_sketch(
            body,
            _payload(_argument(payload, 0, context="api.pad"), context="api.pad.profile"),
            memo,
            sketch_evidence,
        )
        feature = body.newObject("PartDesign::Pad", name)
        feature.Profile = profile
        feature.Length = float(_argument(payload, 1, context="api.pad"))
        feature.Reversed = bool(properties.get("reverse"))
        feature.SideType = "Symmetric" if bool(properties.get("midplane")) else "One side"
        feature.Refine = bool(properties.get("refine", True))
    elif operation == "pocket":
        subtractive_base = _build_feature(
            body,
            _payload(_argument(payload, 0, context="api.pocket"), context="api.pocket.base"),
            memo,
            sketch_evidence,
        )
        profile = _build_sketch(
            body,
            _payload(_argument(payload, 1, context="api.pocket"), context="api.pocket.profile"),
            memo,
            sketch_evidence,
        )
        feature = body.newObject("PartDesign::Pocket", name)
        feature.Profile = profile
        if bool(properties.get("through_all")):
            feature.Type = "ThroughAll"
        else:
            feature.Length = float(_argument(payload, 2, context="api.pocket"))
        feature.Reversed = bool(properties.get("reverse"))
        feature.SideType = "Symmetric" if bool(properties.get("midplane")) else "One side"
        feature.Refine = bool(properties.get("refine", True))
    elif operation == "revolve":
        base_value = properties.get("base")
        if base_value is not None:
            additive_base = _build_feature(
                body,
                _payload(base_value, context="api.revolve.base"),
                memo,
                sketch_evidence,
            )
        profile = _build_sketch(
            body,
            _payload(_argument(payload, 0, context="api.revolve"), context="api.revolve.profile"),
            memo,
            sketch_evidence,
        )
        feature = body.newObject("PartDesign::Revolution", name)
        feature.Profile = profile
        feature.ReferenceAxis = _profile_axis(profile, str(properties.get("axis") or "V"))
        feature.Angle = float(_argument(payload, 1, context="api.revolve"))
        feature.Reversed = bool(properties.get("reverse"))
        feature.Midplane = bool(properties.get("midplane"))
        feature.Refine = bool(properties.get("refine", True))
    elif operation == "groove":
        subtractive_base = _build_feature(
            body,
            _payload(_argument(payload, 0, context="api.groove"), context="api.groove.base"),
            memo,
            sketch_evidence,
        )
        profile = _build_sketch(
            body,
            _payload(_argument(payload, 1, context="api.groove"), context="api.groove.profile"),
            memo,
            sketch_evidence,
        )
        feature = body.newObject("PartDesign::Groove", name)
        feature.Profile = profile
        feature.ReferenceAxis = _profile_axis(profile, str(properties.get("axis") or "V"))
        feature.Angle = float(_argument(payload, 2, context="api.groove"))
        feature.Reversed = bool(properties.get("reverse"))
        feature.Midplane = bool(properties.get("midplane"))
        feature.Refine = bool(properties.get("refine", True))
    elif operation == "loft":
        base_value = properties.get("base")
        if base_value is not None:
            material_base = _build_feature(
                body,
                _payload(base_value, context="api.loft.base"),
                memo,
                sketch_evidence,
            )
            if bool(properties.get("subtractive")):
                subtractive_base = material_base
            else:
                additive_base = material_base
        raw_sections = _argument(payload, 0, context="api.loft")
        if not isinstance(raw_sections, list):
            raise PartDesignCandidateError("api.loft.sections must be an array.")
        sections = [
            _build_sketch(body, _payload(item, context="api.loft.sections"), memo, sketch_evidence)
            for item in raw_sections
        ]
        native_type = (
            "PartDesign::SubtractiveLoft"
            if bool(properties.get("subtractive"))
            else "PartDesign::AdditiveLoft"
        )
        feature = body.newObject(native_type, name)
        feature.Profile = sections[0]
        feature.Sections = sections[1:]
        feature.Ruled = bool(properties.get("ruled"))
        feature.Closed = bool(properties.get("closed"))
        feature.Refine = bool(properties.get("refine", True))
    elif operation == "polar_pattern":
        base = _build_feature(
            body,
            _payload(_argument(payload, 0, context="api.polar_pattern"), context="api.polar_pattern.base"),
            memo,
            sketch_evidence,
        )
        feature = body.newObject("PartDesign::PolarPattern", name)
        feature.Originals = [base]
        profile = getattr(base, "Profile", None)
        if isinstance(profile, tuple):
            profile = profile[0]
        axis_target = profile or base
        feature.Axis = _profile_axis(axis_target, str(properties.get("axis") or "N"))
        feature.Angle = float(properties.get("angle_degrees") or 360.0)
        feature.Occurrences = int(_argument(payload, 1, context="api.polar_pattern"))
    elif operation == "mirror":
        base = _build_feature(
            body,
            _payload(_argument(payload, 0, context="api.mirror"), context="api.mirror.base"),
            memo,
            sketch_evidence,
        )
        feature = body.newObject("PartDesign::Mirrored", name)
        feature.Originals = [base]
        feature.MirrorPlane = (_origin_feature(body, f"{properties.get('plane', 'XY')}_Plane"), [""])
    elif operation in {"fillet", "chamfer"}:
        base = _build_feature(
            body,
            _payload(_argument(payload, 0, context=f"api.{operation}"), context=f"api.{operation}.base"),
            memo,
            sketch_evidence,
        )
        body.Document.recompute()
        selection = _argument(payload, 1, context=f"api.{operation}")
        names, _details = _query_subelements(base.Shape, selection)
        feature = body.newObject(
            "PartDesign::Fillet" if operation == "fillet" else "PartDesign::Chamfer",
            name,
        )
        feature.Base = (base, names)
        if operation == "fillet":
            feature.Radius = float(_argument(payload, 2, context="api.fillet"))
        else:
            feature.ChamferType = "Equal distance"
            feature.Size = float(_argument(payload, 2, context="api.chamfer"))
    else:
        raise PartDesignCandidateError(
            f"Unsupported Part Design operation {operation!r}.",
            details={"stage": "operation_dispatch", "operation": operation},
        )
    if feature is None:
        raise PartDesignCandidateError(f"FreeCAD did not create api.{operation}.")
    _set_label(feature, properties, name)
    body.Tip = feature
    body.Document.recompute()
    shape = getattr(feature, "Shape", None)
    if shape is None or shape.isNull() or not shape.isValid():
        raise PartDesignCandidateError(
            f"api.{operation} did not produce a valid feature shape.",
            details={
                "stage": "feature_validation",
                "operation": operation,
                "graph_id": graph_id,
                "object_name": str(getattr(feature, "Name", "") or ""),
                "error": str(getattr(feature, "getStatusString", lambda: "")() or ""),
            },
        )
    material_base = additive_base if additive_base is not None else subtractive_base
    if material_base is not None:
        base_shape = getattr(material_base, "Shape", None)
        base_volume = float(getattr(base_shape, "Volume", 0.0) or 0.0)
        result_volume = float(getattr(shape, "Volume", 0.0) or 0.0)
        tolerance = max(1.0e-9, abs(base_volume) * 1.0e-9)
        removes_material = subtractive_base is not None
        changed_material = (
            result_volume < base_volume - tolerance
            if removes_material
            else result_volume > base_volume + tolerance
        )
        if not changed_material:
            effect = "remove" if removes_material else "add"
            profile_role = "subtractive" if removes_material else "additive"
            raise PartDesignCandidateError(
                f"api.{operation} did not {effect} material on its base feature.",
                details={
                    "stage": "feature_postcondition",
                    "operation": operation,
                    "graph_id": graph_id,
                    "base_volume_mm3": base_volume,
                    "result_volume_mm3": result_volume,
                    "correction": (
                        f"Place the {profile_role} profile so its sweep intersects the "
                        "current solid, and choose its attachment, reverse, midplane, "
                        "length, or angle semantics so it changes the body."
                    ),
                },
            )
    memo[graph_id] = feature
    return feature


def _resolved_interfaces(shape: Any, raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise PartDesignCandidateError("api.body interfaces must be an object.")
    result: dict[str, dict[str, Any]] = {}
    for name, definition in raw.items():
        if not isinstance(definition, Mapping):
            raise PartDesignCandidateError(f"Interface {name!r} is malformed.")
        selection = definition.get("selection")
        if not isinstance(selection, Mapping):
            raise PartDesignCandidateError(f"Interface {name!r} has no selection.")
        if selection.get("type") == "origin":
            subelements: list[str] = []
            geometry: list[dict[str, Any]] = []
        else:
            subelements, geometry = _query_subelements(shape, selection)
        result[str(name)] = {
            "selection": dict(selection),
            **(
                {"description": str(definition.get("description") or "")}
                if definition.get("description")
                else {}
            ),
            "subelements": subelements,
            "geometry": geometry,
        }
    return result


def validate_and_build_partdesign(
    document: Any,
    raw_result: Mapping[str, Any],
    expected_outputs: list[dict[str, Any]],
    root: Path,
    *,
    max_shape_subelements: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build native Body histories and export only validated single-solid tips."""

    if not isinstance(raw_result, Mapping) or list(raw_result) != [
        str(item["name"]) for item in expected_outputs
    ]:
        raise PartDesignCandidateError(
            "Part Design result order must exactly match expected_outputs.",
            details={"stage": "result_contract"},
        )
    outputs: list[dict[str, Any]] = []
    validation_outputs: list[dict[str, Any]] = []
    for index, expected in enumerate(expected_outputs):
        name = str(expected["name"])
        if str(expected.get("type") or "") != "solid":
            raise PartDesignCandidateError(
                f"Part Design output {name!r} must declare type 'solid'."
            )
        definition = _payload(raw_result[name], context=f"result[{name!r}]")
        if definition.get("operation") != "body" or definition.get("output_type") != "solid":
            raise PartDesignCandidateError(
                f"Part Design output {name!r} must be returned by api.body."
            )
        properties = _properties(definition)
        body = document.addObject("PartDesign::Body", f"CandidateBody{index + 1}")
        if body is None:
            raise PartDesignCandidateError("FreeCAD did not create PartDesign::Body.")
        _set_label(body, properties, name)
        memo: dict[str, Any] = {}
        sketch_evidence: list[dict[str, Any]] = []
        final_feature = _build_feature(
            body,
            _payload(_argument(definition, 0, context="api.body"), context="api.body.feature"),
            memo,
            sketch_evidence,
        )
        body.Tip = final_feature
        document.recompute()
        native_shape = getattr(body, "Shape", None)
        shape = native_shape
        if (
            native_shape is not None
            and not native_shape.isNull()
            and str(getattr(native_shape, "ShapeType", "") or "") != "Solid"
        ):
            children = list(native_shape.childShapes())
            if (
                len(children) == 1
                and str(getattr(children[0], "ShapeType", "") or "") == "Solid"
            ):
                shape = children[0]
        facts = (
            part_shape_facts(shape, max_subelements=max_shape_subelements)
            if shape is not None
            else {}
        )
        if (
            shape is None
            or shape.isNull()
            or not shape.isValid()
            or str(getattr(shape, "ShapeType", "") or "") != "Solid"
            or int(facts.get("solids") or 0) != 1
        ):
            raise PartDesignCandidateError(
                f"Part Design output {name!r} is not exactly one valid solid.",
                details={
                    "stage": "body_validation",
                    "output": name,
                    "facts": facts,
                    "tip": str(getattr(final_feature, "Name", "") or ""),
                    "native_shape_type": str(
                        getattr(native_shape, "ShapeType", "") or ""
                    ),
                },
            )
        relative = Path("outputs") / f"output-{index:03d}.brep"
        target = root / relative
        shape.exportBrep(str(target))
        if not target.is_file() or target.stat().st_size <= 0:
            raise PartDesignCandidateError(
                f"Could not export Part Design output {name!r}."
            )
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        interfaces = _resolved_interfaces(shape, properties.get("interfaces") or {})
        feature_history = [
            {
                "object_name": str(getattr(obj, "Name", "") or ""),
                "label": str(getattr(obj, "Label", "") or ""),
                "type_id": str(getattr(obj, "TypeId", "") or ""),
            }
            for obj in list(getattr(body, "Group", []) or [])
        ]
        data = {
            "body_label": str(getattr(body, "Label", "") or ""),
            "tip_type_id": str(getattr(final_feature, "TypeId", "") or ""),
            "tip_label": str(getattr(final_feature, "Label", "") or ""),
            "feature_count": len(feature_history),
            "feature_history": feature_history,
            "sketches": sketch_evidence,
            "interfaces": interfaces,
            "brep_sha256": digest,
        }
        outputs.append(
            {
                "name": name,
                "type": "solid",
                "definition": definition,
                "artifact_kind": "brep",
                "artifact_path": str(relative),
                "facts": facts,
                "partdesign_data": data,
            }
        )
        validation_outputs.append(
            {
                "name": name,
                "facts": facts,
                "partdesign_data": data,
            }
        )
    return outputs, {"outputs": validation_outputs}
