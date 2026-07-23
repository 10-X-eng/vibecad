# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native evaluator for the Part Design VibeScript domain."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from vibescript_domain_api import DomainValue
from vibescript_material_api import MaterialDomainAPI
from vibescript_part_worker import (
    PartOperationError,
    build_part_shape,
    configure_part_references,
    part_shape_facts,
)
from vibescript_part_api import PartDomainAPI
from vibescript_sketcher_worker import (
    _resolve_worker_external_geometry,
    configure_sketcher_references,
    populate_sketch_without_solving,
)


class PartDesignCandidateError(RuntimeError):
    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None):
        self.details = dict(details or {})
        super().__init__(message)


_PART_DIRECT_OPERATIONS = frozenset(PartDomainAPI.exported_names.fget(None))
_PUBLISHABLE_TYPES = frozenset({"solid", "shell", "face", "wire", "compound"})
PARTDESIGN_PRESENTATION_SCHEMA = "vibecad-partdesign-presentation-v1"
_MATERIAL_CARD_PROPERTIES = {
    "require_physical_properties",
    "require_appearance_properties",
}
_APPEARANCE_PROPERTIES = {
    "shape_color",
    "line_color",
    "point_color",
    "transparency",
    "line_width",
    "point_size",
    "display_mode",
    "visibility",
    "selectable",
    "label",
}


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


def _material_api() -> MaterialDomainAPI:
    return MaterialDomainAPI(
        MaterialDomainAPI.exported_names,
        ("material_assignment", "appearance"),
    )


def _material_domain_value(payload: Mapping[str, Any]) -> DomainValue:
    return DomainValue(
        domain="material",
        operation=str(payload["operation"]),
        output_type=str(payload["output_type"]),
        arguments=tuple(payload["arguments"]),
        properties=dict(payload["properties"]),
    )


def _validated_material_card(value: Any, *, context: str) -> dict[str, Any]:
    payload = _payload(value, context=context)
    arguments = payload.get("arguments")
    properties = payload.get("properties")
    if (
        payload.get("operation") != "material"
        or payload.get("output_type") != "material_card"
        or not isinstance(arguments, list)
        or len(arguments) != 1
        or not isinstance(properties, Mapping)
        or set(properties) != _MATERIAL_CARD_PROPERTIES
    ):
        raise PartDesignCandidateError(
            f"{context} must be returned by api.material.",
            details={"stage": "material_graph_contract", "context": context},
        )
    try:
        canonical = _material_api().material(
            arguments[0],
            require_physical_properties=properties["require_physical_properties"],
            require_appearance_properties=properties[
                "require_appearance_properties"
            ],
        ).to_payload()
    except (TypeError, ValueError) as exc:
        raise PartDesignCandidateError(
            f"{context} is invalid: {exc}",
            details={"stage": "material_graph_contract", "context": context},
        ) from exc
    canonical["domain"] = "partdesign"
    if canonical != payload:
        raise PartDesignCandidateError(
            f"{context} is not the canonical api.material value.",
            details={"stage": "material_graph_contract", "context": context},
        )
    return payload


def _validated_appearance(value: Any, *, context: str) -> dict[str, Any]:
    payload = _payload(value, context=context)
    arguments = payload.get("arguments")
    properties = payload.get("properties")
    if (
        payload.get("operation") != "appearance"
        or payload.get("output_type") != "appearance"
        or not isinstance(arguments, list)
        or len(arguments) != 1
        or not isinstance(properties, Mapping)
        or set(properties) != _APPEARANCE_PROPERTIES
    ):
        raise PartDesignCandidateError(
            f"{context} must be returned by api.appearance.",
            details={"stage": "appearance_graph_contract", "context": context},
        )
    card_payload = (
        None
        if arguments[0] is None
        else _validated_material_card(arguments[0], context=f"{context}.card")
    )
    card = (
        None if card_payload is None else _material_domain_value(card_payload)
    )
    try:
        canonical = _material_api().appearance(
            {
                "document_uid": "partdesign-publication",
                "object_name": "Output",
            },
            card,
            **dict(properties),
        ).to_payload()
    except (TypeError, ValueError) as exc:
        raise PartDesignCandidateError(
            f"{context} is invalid: {exc}",
            details={"stage": "appearance_graph_contract", "context": context},
        ) from exc
    expected = {
        "domain": "partdesign",
        "operation": "appearance",
        "output_type": "appearance",
        "arguments": [card_payload],
        "properties": canonical["properties"],
    }
    if expected != payload:
        raise PartDesignCandidateError(
            f"{context} is not the canonical api.appearance value.",
            details={"stage": "appearance_graph_contract", "context": context},
        )
    return payload


class PartDesignMaterialResolver:
    """Resolve shared Material-workbench cards for Part Design output metadata."""

    def __init__(self) -> None:
        self._manager = None
        self._native_cards: dict[str, Any] = {}
        self._records: dict[
            tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ] = {}

    def _resolve_card(
        self,
        definition: Mapping[str, Any],
        *,
        output_name: str,
    ) -> tuple[Any, dict[str, Any]]:
        from vibescript_material_worker import (
            MATERIAL_CATALOG_LOCK,
            material_card_record,
        )

        material_uuid = str(list(definition["arguments"])[0])
        properties = dict(definition["properties"])
        required_physical = tuple(properties["require_physical_properties"])
        required_appearance = tuple(properties["require_appearance_properties"])
        cache_key = (material_uuid, required_physical, required_appearance)
        card = self._native_cards.get(material_uuid)
        record = self._records.get(cache_key)
        try:
            import Materials

            with MATERIAL_CATALOG_LOCK:
                if self._manager is None:
                    self._manager = Materials.MaterialManager()
                if card is None:
                    card = self._manager.getMaterial(material_uuid)
                    if card is not None:
                        self._native_cards[material_uuid] = card
                if card is not None and record is None:
                    record = material_card_record(
                        card,
                        required_physical_properties=required_physical,
                        required_appearance_properties=required_appearance,
                    )
                    self._records[cache_key] = record
        except Exception as exc:
            raise PartDesignCandidateError(
                f"Material card {material_uuid!r} could not be resolved for "
                f"Part Design output {output_name!r}: {exc}",
                details={
                    "stage": "material_catalog",
                    "output": output_name,
                    "material_uuid": material_uuid,
                    "native_error": f"{type(exc).__name__}: {exc}",
                },
            ) from exc
        if card is None or record is None:
            raise PartDesignCandidateError(
                f"Material card {material_uuid!r} does not exist for Part Design "
                f"output {output_name!r}.",
                details={
                    "stage": "material_catalog",
                    "output": output_name,
                    "material_uuid": material_uuid,
                    "correction": (
                        "Choose one exact UUID from material_catalog in the Part Design "
                        "domain context."
                    ),
                },
            )
        return card, record

    def resolve(
        self,
        publication_definition: Mapping[str, Any],
        *,
        output_name: str,
    ) -> tuple[dict[str, Any], Any | None]:
        from vibescript_material_worker import (
            MATERIAL_CATALOG_LOCK,
            appearance_controlled_properties,
            material_card_appearance,
            resolve_material_appearance,
        )

        properties = _properties(publication_definition)
        material_definition = properties.get("material")
        appearance_definition = properties.get("appearance")
        physical_record = None
        native_physical = None
        if material_definition is not None:
            canonical_material = _validated_material_card(
                material_definition,
                context=f"output.{output_name}.material",
            )
            native_physical, physical_record = self._resolve_card(
                canonical_material,
                output_name=output_name,
            )

        appearance_validation = None
        if appearance_definition is not None:
            canonical_appearance = _validated_appearance(
                appearance_definition,
                context=f"output.{output_name}.appearance",
            )
            requested = dict(canonical_appearance["properties"])
            requested.pop("label")
            appearance_card_definition = list(canonical_appearance["arguments"])[0]
            appearance_record = None
            card_appearance = None
            if appearance_card_definition is not None:
                native_appearance, appearance_record = self._resolve_card(
                    appearance_card_definition,
                    output_name=output_name,
                )
                with MATERIAL_CATALOG_LOCK:
                    card_appearance = material_card_appearance(native_appearance)
            resolved = resolve_material_appearance(requested, card_appearance)
            appearance_validation = {
                "requested": requested,
                "resolved": resolved,
                "controlled_properties": appearance_controlled_properties(resolved),
                "material_card": appearance_record,
                "card_appearance": card_appearance,
            }

        return (
            {
                "schema": PARTDESIGN_PRESENTATION_SCHEMA,
                "physical_material": physical_record,
                "appearance": appearance_validation,
            },
            native_physical,
        )


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
    external_targets: dict[tuple[str, str], Any] = {}

    def resolve_external(definition: Mapping[str, Any]):
        target, subelement, resolution = _resolve_worker_external_geometry(
            sketch,
            definition,
            target_cache=external_targets,
        )
        parent = getattr(target, "getParentGeoFeatureGroup", lambda: None)()
        if parent is None:
            body.addObject(target)
        if hasattr(target, "Visibility"):
            target.Visibility = False
        return target, subelement, resolution

    populate_sketch_without_solving(
        sketch,
        _as_sketcher_payload(payload),
        replace_existing=False,
        external_resolver=resolve_external,
    )
    body.Tip = sketch
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


def _retag_serialized_graph(value: Any, domain: str) -> Any:
    if isinstance(value, Mapping):
        result = {
            str(key): _retag_serialized_graph(item, domain)
            for key, item in value.items()
        }
        if set(result) == {
            "domain",
            "operation",
            "output_type",
            "arguments",
            "properties",
        }:
            result["domain"] = domain
        return result
    if isinstance(value, list):
        return [_retag_serialized_graph(item, domain) for item in value]
    return value


def _profile_shape(
    body: Any,
    payload: Mapping[str, Any],
    memo: dict[str, Any],
    sketch_evidence: list[dict[str, Any]],
    *,
    as_face: bool,
) -> tuple[Any, Any]:
    import Part

    sketch = _build_sketch(body, payload, memo, sketch_evidence)
    body.Document.recompute()
    shape = getattr(sketch, "Shape", None)
    wires = list(getattr(shape, "Wires", []) or []) if shape is not None else []
    if not wires:
        raise PartDesignCandidateError(
            "A standalone modeling profile produced no wires.",
            details={
                "stage": "profile_materialization",
                "graph_id": _graph_id(payload, context="api.sketch"),
            },
        )
    if not as_face:
        return sketch, wires[0] if len(wires) == 1 else Part.makeCompound(wires)
    if not all(bool(wire.isClosed()) for wire in wires):
        raise PartDesignCandidateError(
            "A standalone solid operation requires only closed profile wires.",
            details={"stage": "profile_materialization", "wire_count": len(wires)},
        )
    face = Part.makeFace(wires, "Part::FaceMakerBullseye")
    if face is None or face.isNull() or not face.isValid():
        raise PartDesignCandidateError(
            "The closed profile could not form a valid planar face.",
            details={"stage": "profile_materialization", "wire_count": len(wires)},
        )
    faces = list(getattr(face, "Faces", []) or [])
    if len(faces) == 1:
        face = faces[0]
    return sketch, face


def _wire_shape(shape: Any, *, context: str) -> Any:
    import Part

    if str(getattr(shape, "ShapeType", "") or "") == "Wire":
        return shape
    wires = list(getattr(shape, "Wires", []) or [])
    if len(wires) == 1:
        return wires[0]
    edges = list(getattr(shape, "Edges", []) or [])
    if edges:
        try:
            return Part.Wire(edges)
        except Exception as exc:
            raise PartDesignCandidateError(
                f"{context} does not form one connected wire: {exc}",
                details={"stage": "wire_materialization", "context": context},
            ) from exc
    raise PartDesignCandidateError(
        f"{context} contains no usable wire.",
        details={"stage": "wire_materialization", "context": context},
    )


def _profile_axis_geometry(sketch: Any, key: str) -> tuple[Any, Any]:
    import FreeCAD as App

    local = {
        "H": App.Vector(1.0, 0.0, 0.0),
        "V": App.Vector(0.0, 1.0, 0.0),
        "N": App.Vector(0.0, 0.0, 1.0),
        "X": App.Vector(1.0, 0.0, 0.0),
        "Y": App.Vector(0.0, 1.0, 0.0),
        "Z": App.Vector(0.0, 0.0, 1.0),
    }[str(key or "V").upper()]
    placement = sketch.getGlobalPlacement()
    return placement.Base, placement.Rotation.multVec(local)


def _refined(shape: Any, enabled: bool) -> Any:
    if not enabled:
        return shape
    result = shape.removeSplitter()
    return result if result is not None else shape


def _normalized_solid_orientation(shape: Any) -> Any:
    """Return outward-oriented solid topology before boolean composition."""

    if str(getattr(shape, "ShapeType", "") or "") == "Solid" and float(
        getattr(shape, "Volume", 0.0) or 0.0
    ) < 0.0:
        reversed_shape = shape.reversed()
        if reversed_shape is not None:
            shape = reversed_shape
    return shape


def _checked_direction(value: Any, *, context: str) -> Any:
    import FreeCAD as App

    if not isinstance(value, list) or len(value) != 3:
        raise PartDesignCandidateError(
            f"{context} must be a non-zero [x, y, z] vector.",
            details={"stage": "vector_contract", "context": context},
        )
    direction = App.Vector(*(float(item) for item in value))
    if direction.Length <= 1.0e-12:
        raise PartDesignCandidateError(
            f"{context} must be non-zero.",
            details={"stage": "vector_contract", "context": context},
        )
    return direction


def _boolean_sequence(
    shapes: list[Any],
    *,
    intent: str,
    tolerance: float,
    context: str,
) -> Any:
    """Evaluate multi-operand booleans deterministically and validate every step."""

    result = _normalized_solid_orientation(shapes[0].copy())
    for index, raw_tool in enumerate(shapes[1:], start=1):
        tool = _normalized_solid_orientation(raw_tool)
        try:
            if intent == "union":
                result = result.fuse(tool, tolerance)
            elif intent == "subtract":
                result = result.cut(tool, tolerance)
            else:
                result = result.common(tool, tolerance)
        except Exception as exc:
            raise PartDesignCandidateError(
                f"{context} failed while applying operand {index + 1}: {exc}",
                details={
                    "stage": "boolean_materialization",
                    "operation": intent,
                    "operand_index": index,
                    "operand_count": len(shapes),
                    "native_error": f"{type(exc).__name__}: {exc}",
                },
            ) from exc
        if result is None or result.isNull() or not result.isValid():
            raise PartDesignCandidateError(
                f"{context} produced invalid topology at operand {index + 1}.",
                details={
                    "stage": "boolean_materialization",
                    "operation": intent,
                    "operand_index": index,
                    "operand_count": len(shapes),
                },
            )
        result = _normalized_solid_orientation(result)
    return result


def _build_model_shape(
    body: Any,
    payload: Mapping[str, Any],
    memo: dict[str, Any],
    sketch_evidence: list[dict[str, Any]],
) -> Any:
    """Materialize any Body feature or retained standalone OCC graph node."""

    import FreeCAD as App
    import Part

    operation = str(payload.get("operation") or "")
    output_type = str(payload.get("output_type") or "")
    if output_type == "feature":
        feature = _build_feature(body, payload, memo, sketch_evidence)
        return feature.Shape.copy()
    if operation == "sketch":
        _sketch, shape = _profile_shape(
            body,
            payload,
            memo,
            sketch_evidence,
            as_face=False,
        )
        return shape
    if operation in _PART_DIRECT_OPERATIONS:
        part_payload = _retag_serialized_graph(payload, "part")

        def resolve_nested(nested: dict[str, Any]) -> Any:
            return _build_model_shape(
                body,
                _retag_serialized_graph(nested, "partdesign"),
                memo,
                sketch_evidence,
            )

        try:
            shape = build_part_shape(
                part_payload,
                nested_shape_resolver=resolve_nested,
            )
            return _normalized_solid_orientation(shape)
        except PartOperationError as exc:
            raise PartDesignCandidateError(
                str(exc),
                details=dict(getattr(exc, "details", {}) or {}),
            ) from exc
    properties = _properties(payload)
    if operation == "standalone_extrude":
        source_payload = _payload(
            _argument(payload, 0, context="api.extrude"),
            context="api.extrude.profile",
        )
        if source_payload.get("operation") == "sketch":
            _sketch, source = _profile_shape(
                body,
                source_payload,
                memo,
                sketch_evidence,
                as_face=output_type == "solid",
            )
            sketch = _sketch
        else:
            source = _build_model_shape(body, source_payload, memo, sketch_evidence)
            sketch = None
        raw_vector = properties.get("vector")
        if raw_vector is None:
            if sketch is not None:
                _origin, direction = _profile_axis_geometry(sketch, "N")
            else:
                faces = list(getattr(source, "Faces", []) or [])
                if not faces:
                    raise PartDesignCandidateError(
                        "api.extrude.vector is required for a non-profile source.",
                        details={"stage": "standalone_extrude_direction"},
                    )
                face = faces[0]
                u_min, u_max, v_min, v_max = face.ParameterRange
                direction = face.normalAt((u_min + u_max) / 2.0, (v_min + v_max) / 2.0)
        else:
            direction = App.Vector(*(float(item) for item in raw_vector))
        if direction.Length <= 1.0e-12:
            raise PartDesignCandidateError("api.extrude.vector must be non-zero.")
        direction.normalize()
        vector = direction * float(_argument(payload, 1, context="api.extrude"))
        if bool(properties.get("reverse")):
            vector = -vector
        source = source.copy()
        if bool(properties.get("midplane")):
            source.translate(-vector * 0.5)
        return _normalized_solid_orientation(
            _refined(source.extrude(vector), bool(properties.get("refine", True)))
        )
    if operation == "standalone_revolve":
        source_payload = _payload(
            _argument(payload, 0, context="api.revolve"),
            context="api.revolve.profile",
        )
        if source_payload.get("operation") == "sketch":
            sketch, source = _profile_shape(
                body,
                source_payload,
                memo,
                sketch_evidence,
                as_face=output_type == "solid",
            )
        else:
            source = _build_model_shape(body, source_payload, memo, sketch_evidence)
            sketch = None
        raw_direction = properties.get("axis_direction")
        if raw_direction is not None:
            axis_origin = App.Vector(*(float(item) for item in properties["axis_origin"]))
            axis_direction = App.Vector(*(float(item) for item in raw_direction))
        elif sketch is not None:
            axis_origin, axis_direction = _profile_axis_geometry(
                sketch, str(properties.get("axis") or "V")
            )
        else:
            axis_origin = App.Vector(*(float(item) for item in properties["axis_origin"]))
            axis_direction = {
                "H": App.Vector(1.0, 0.0, 0.0),
                "X": App.Vector(1.0, 0.0, 0.0),
                "V": App.Vector(0.0, 1.0, 0.0),
                "Y": App.Vector(0.0, 1.0, 0.0),
                "N": App.Vector(0.0, 0.0, 1.0),
                "Z": App.Vector(0.0, 0.0, 1.0),
            }.get(str(properties.get("axis") or "Z"), App.Vector(0.0, 0.0, 1.0))
        angle = float(_argument(payload, 1, context="api.revolve"))
        if bool(properties.get("reverse")):
            angle = -angle
        source = source.copy()
        if bool(properties.get("midplane")):
            source.rotate(axis_origin, axis_direction, -angle * 0.5)
        return _normalized_solid_orientation(
            _refined(
                source.revolve(axis_origin, axis_direction, angle),
                bool(properties.get("refine", True)),
            )
        )
    if operation == "standalone_loft":
        raw_sections = _argument(payload, 0, context="api.loft")
        if not isinstance(raw_sections, list):
            raise PartDesignCandidateError("api.loft.sections must be an array.")
        wires = []
        for index, raw in enumerate(raw_sections):
            section_payload = _payload(raw, context=f"api.loft.sections[{index}]")
            if section_payload.get("operation") == "sketch":
                _sketch, section = _profile_shape(
                    body,
                    section_payload,
                    memo,
                    sketch_evidence,
                    as_face=False,
                )
            else:
                section = _build_model_shape(
                    body, section_payload, memo, sketch_evidence
                )
            wires.append(_wire_shape(section, context=f"api.loft.sections[{index}]"))
        shape = Part.makeLoft(
            wires,
            solid=bool(properties.get("solid")),
            ruled=bool(properties.get("ruled")),
            closed=bool(properties.get("closed")),
            max_degree=5,
        )
        return _normalized_solid_orientation(
            _refined(shape, bool(properties.get("refine", True)))
        )
    if operation in {"standalone_sweep", "material_sweep"}:
        raw_profiles = _argument(payload, 0, context="api.sweep")
        if not isinstance(raw_profiles, list):
            raise PartDesignCandidateError("api.sweep.profile must be an array.")
        profiles = []
        for index, raw in enumerate(raw_profiles):
            profile_payload = _payload(raw, context=f"api.sweep.profile[{index}]")
            if profile_payload.get("operation") == "sketch":
                _sketch, profile_shape = _profile_shape(
                    body,
                    profile_payload,
                    memo,
                    sketch_evidence,
                    as_face=False,
                )
            else:
                profile_shape = _build_model_shape(
                    body, profile_payload, memo, sketch_evidence
                )
            profiles.append(
                _wire_shape(profile_shape, context=f"api.sweep.profile[{index}]")
            )
        path_payload = _payload(
            _argument(payload, 1, context="api.sweep"), context="api.sweep.path"
        )
        path = _wire_shape(
            _build_model_shape(body, path_payload, memo, sketch_evidence),
            context="api.sweep.path",
        )
        transitions = {"transformed": 0, "right_corner": 1, "round_corner": 2}
        swept = path.makePipeShell(
            profiles,
            True if operation == "material_sweep" else bool(properties.get("solid")),
            bool(properties.get("frenet")),
            transitions[str(properties.get("transition") or "transformed")],
        )
        return _normalized_solid_orientation(
            _refined(swept, bool(properties.get("refine", True)))
        )
    if operation == "material_helix":
        profile_payload = _payload(
            _argument(payload, 0, context="api.helix"), context="api.helix.profile"
        )
        _sketch, profile_shape = _profile_shape(
            body,
            profile_payload,
            memo,
            sketch_evidence,
            as_face=False,
        )
        helix = Part.makeHelix(
            float(_argument(payload, 1, context="api.helix")),
            float(_argument(payload, 2, context="api.helix")),
            float(_argument(payload, 3, context="api.helix")),
            0.0,
            bool(properties.get("left_handed")),
        )
        swept = _wire_shape(helix, context="api.helix.path").makePipeShell(
            [_wire_shape(profile_shape, context="api.helix.profile")],
            True,
            False,
            0,
        )
        if bool(properties.get("reversed")):
            swept = swept.reversed()
        return _normalized_solid_orientation(
            _refined(swept, bool(properties.get("refine", True)))
        )
    if operation in {"boolean", "model_compound"}:
        raw_shapes = _argument(payload, 0, context=f"api.{operation}")
        if not isinstance(raw_shapes, list):
            raise PartDesignCandidateError(f"api.{operation}.shapes must be an array.")
        shapes = [
            _normalized_solid_orientation(_build_model_shape(
                body,
                _payload(raw, context=f"api.{operation}.shapes[{index}]"),
                memo,
                sketch_evidence,
            ))
            for index, raw in enumerate(raw_shapes)
        ]
        if operation == "model_compound":
            return Part.makeCompound(shapes)
        intent = str(properties.get("boolean_operation") or "")
        if intent not in {"union", "subtract", "intersect"}:
            raise PartDesignCandidateError(
                "api.boolean.operation must be union, subtract, or intersect.",
                details={"stage": "boolean_contract", "operation": intent},
            )
        tolerance = float(properties.get("tolerance_mm") or 0.0)
        result = _boolean_sequence(
            shapes,
            intent=intent,
            tolerance=tolerance,
            context="api.boolean",
        )
        return _normalized_solid_orientation(
            _refined(result, bool(properties.get("refine", True)))
        )
    if operation in {"model_subshape", "model_defeature"}:
        source = _build_model_shape(
            body,
            _payload(
                _argument(payload, 0, context=f"api.{operation}"),
                context=f"api.{operation}.shape",
            ),
            memo,
            sketch_evidence,
        )
        selection = _argument(payload, 1, context=f"api.{operation}")
        names, _details = _query_subelements(source, selection)
        if operation == "model_subshape":
            if len(names) != 1:
                raise PartDesignCandidateError(
                    "api.subshape requires a query that resolves to exactly one subelement."
                )
            selected = source.getElement(names[0])
            return selected.copy()
        faces = [source.getElement(name) for name in names]
        return _normalized_solid_orientation(source.defeaturing(faces))
    if operation == "standalone_mirror":
        source = _build_model_shape(
            body,
            _payload(_argument(payload, 0, context="api.mirror"), context="api.mirror.base"),
            memo,
            sketch_evidence,
        )
        return source.mirror(
            App.Vector(*_argument(payload, 1, context="api.mirror")),
            _checked_direction(
                _argument(payload, 2, context="api.mirror"),
                context="api.mirror.plane_normal",
            ),
        )
    if operation in {"standalone_polar_pattern", "linear_pattern", "multi_transform"}:
        return _build_pattern_shape(body, payload, memo, sketch_evidence)
    if operation in {
        "fillet",
        "chamfer",
        "thickness",
        "draft",
        "model_fillet",
        "model_chamfer",
        "model_thickness",
    }:
        return _build_direct_dressup_shape(body, payload, memo, sketch_evidence)
    raise PartDesignCandidateError(
        f"Unsupported consolidated modeling operation {operation!r}.",
        details={"stage": "operation_dispatch", "operation": operation},
    )


def _combine_pattern_shapes(shapes: list[Any], result_mode: str) -> Any:
    import Part

    if result_mode == "union":
        return _refined(
            _boolean_sequence(
                shapes,
                intent="union",
                tolerance=0.0,
                context="Pattern union",
            ),
            True,
        )
    return Part.makeCompound(shapes)


def _build_pattern_shape(
    body: Any,
    payload: Mapping[str, Any],
    memo: dict[str, Any],
    sketch_evidence: list[dict[str, Any]],
) -> Any:
    import FreeCAD as App

    operation = str(payload.get("operation") or "")
    properties = _properties(payload)
    base = _build_model_shape(
        body,
        _payload(
            _argument(payload, 0, context=f"api.{operation}"),
            context=f"api.{operation}.base",
        ),
        memo,
        sketch_evidence,
    )
    shapes = [base.copy()]
    if operation == "linear_pattern":
        count = int(_argument(payload, 1, context="api.linear_pattern"))
        total = float(_argument(payload, 2, context="api.linear_pattern"))
        direction = App.Vector(*(float(item) for item in properties["direction"]))
        if direction.Length <= 1.0e-12:
            raise PartDesignCandidateError("api.linear_pattern.direction must be non-zero.")
        direction.normalize()
        for index in range(1, count):
            copy = base.copy()
            copy.translate(direction * (total * index / (count - 1)))
            shapes.append(copy)
    elif operation == "standalone_polar_pattern":
        count = int(_argument(payload, 1, context="api.polar_pattern"))
        total = float(properties.get("angle_degrees") or 360.0)
        center = App.Vector(*(float(item) for item in properties["center"]))
        direction = _checked_direction(
            properties["axis_direction"],
            context="api.polar_pattern.axis_direction",
        )
        divisor = count if abs(total - 360.0) <= 1.0e-7 else count - 1
        for index in range(1, count):
            copy = base.copy()
            copy.rotate(center, direction, total * index / divisor)
            shapes.append(copy)
    else:
        raw_steps = _argument(payload, 1, context="api.multi_transform")
        if not isinstance(raw_steps, list):
            raise PartDesignCandidateError(
                "api.multi_transform.transformations must be an array."
            )
        current = base.copy()
        for index, raw in enumerate(raw_steps):
            if not isinstance(raw, Mapping):
                raise PartDesignCandidateError(
                    f"api.multi_transform.transformations[{index}] must be an object."
                )
            step = dict(raw)
            kind = str(step.get("type") or "")
            current = current.copy()
            if kind == "translate":
                vector = step.get("vector")
                if not isinstance(vector, list) or len(vector) != 3:
                    raise PartDesignCandidateError(
                        f"api.multi_transform.transformations[{index}].vector must be [x,y,z]."
                    )
                current.translate(App.Vector(*(float(item) for item in vector)))
            elif kind == "rotate":
                origin = step.get("origin", [0.0, 0.0, 0.0])
                axis = step.get("axis")
                angle = step.get("angle_degrees")
                if (
                    not isinstance(origin, list)
                    or len(origin) != 3
                    or not isinstance(axis, list)
                    or len(axis) != 3
                    or isinstance(angle, bool)
                    or not isinstance(angle, (int, float))
                ):
                    raise PartDesignCandidateError(
                        f"api.multi_transform.transformations[{index}] has an invalid rotation."
                    )
                current.rotate(
                    App.Vector(*(float(item) for item in origin)),
                    _checked_direction(
                        axis,
                        context=f"api.multi_transform.transformations[{index}].axis",
                    ),
                    float(angle),
                )
            elif kind == "mirror":
                origin = step.get("origin", [0.0, 0.0, 0.0])
                normal = step.get("normal")
                if (
                    not isinstance(origin, list)
                    or len(origin) != 3
                    or not isinstance(normal, list)
                    or len(normal) != 3
                ):
                    raise PartDesignCandidateError(
                        f"api.multi_transform.transformations[{index}] has an invalid mirror."
                    )
                current = current.mirror(
                    App.Vector(*(float(item) for item in origin)),
                    _checked_direction(
                        normal,
                        context=f"api.multi_transform.transformations[{index}].normal",
                    ),
                )
            else:
                factor = step.get("factor")
                center = step.get("center", [0.0, 0.0, 0.0])
                if (
                    isinstance(factor, bool)
                    or not isinstance(factor, (int, float))
                    or float(factor) <= 0.0
                    or not isinstance(center, list)
                    or len(center) != 3
                ):
                    raise PartDesignCandidateError(
                        f"api.multi_transform.transformations[{index}] has an invalid scale."
                    )
                current.scale(
                    float(factor), App.Vector(*(float(item) for item in center))
                )
            shapes.append(current)
    return _combine_pattern_shapes(shapes, str(properties.get("result") or "compound"))


def _build_direct_dressup_shape(
    body: Any,
    payload: Mapping[str, Any],
    memo: dict[str, Any],
    sketch_evidence: list[dict[str, Any]],
) -> Any:
    graph_operation = str(payload.get("operation") or "")
    operation = graph_operation.removeprefix("model_")
    properties = _properties(payload)
    base = _build_model_shape(
        body,
        _payload(_argument(payload, 0, context=f"api.{operation}"), context=f"api.{operation}.base"),
        memo,
        sketch_evidence,
    )
    selection = _argument(payload, 1, context=f"api.{operation}")
    names, _details = _query_subelements(base, selection)
    if operation in {"fillet", "chamfer"}:
        edges = [base.Edges[int(name.removeprefix("Edge")) - 1] for name in names]
        distance = float(_argument(payload, 2, context=f"api.{operation}"))
        return base.makeFillet(distance, edges) if operation == "fillet" else base.makeChamfer(distance, edges)
    if operation == "thickness":
        faces = [base.Faces[int(name.removeprefix("Face")) - 1] for name in names]
        amount = float(_argument(payload, 2, context="api.thickness"))
        if bool(properties.get("inward")):
            amount = -amount
        joins = {"arc": 0, "tangent": 1, "intersection": 2}
        return base.makeThickness(
            faces,
            amount,
            1.0e-7,
            False,
            False,
            0,
            joins[str(properties.get("join") or "arc")],
        )
    raise PartDesignCandidateError(
        "api.draft requires a Body feature base.",
        details={"stage": "draft_base_contract"},
    )


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
    elif operation in {"material_sweep", "material_helix"}:
        base_value = properties.get("base")
        material_base = None
        if base_value is not None:
            material_base = _build_feature(
                body,
                _payload(base_value, context=f"api.{operation}.base"),
                memo,
                sketch_evidence,
            )
            if bool(properties.get("subtractive")):
                subtractive_base = material_base
            else:
                additive_base = material_base
        detached_payload = dict(payload)
        detached_payload["output_type"] = "solid"
        if operation == "material_sweep":
            detached_payload["operation"] = "standalone_sweep"
            detached_properties = dict(properties)
            detached_properties["solid"] = True
            detached_payload["properties"] = detached_properties
        swept = _build_model_shape(
            body,
            detached_payload,
            memo,
            sketch_evidence,
        )
        result = swept
        if material_base is not None:
            result = (
                material_base.Shape.cut(swept)
                if bool(properties.get("subtractive"))
                else material_base.Shape.fuse(swept)
            )
            result = _refined(result, bool(properties.get("refine", True)))
        feature = body.newObject("PartDesign::Feature", name)
        feature.Shape = result
    elif operation in {"linear_pattern", "multi_transform"}:
        base = _build_feature(
            body,
            _payload(
                _argument(payload, 0, context=f"api.{operation}"),
                context=f"api.{operation}.base",
            ),
            memo,
            sketch_evidence,
        )
        additive_base = base
        detached_payload = dict(payload)
        detached_payload["output_type"] = "solid"
        detached_properties = dict(properties)
        detached_properties["result"] = "union"
        detached_payload["properties"] = detached_properties
        result = _build_pattern_shape(
            body,
            detached_payload,
            memo,
            sketch_evidence,
        )
        feature = body.newObject("PartDesign::Feature", name)
        feature.Shape = result
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
    elif operation == "hole":
        subtractive_base = _build_feature(
            body,
            _payload(_argument(payload, 0, context="api.hole"), context="api.hole.base"),
            memo,
            sketch_evidence,
        )
        profile = _build_sketch(
            body,
            _payload(_argument(payload, 1, context="api.hole"), context="api.hole.profile"),
            memo,
            sketch_evidence,
        )
        body.Tip = subtractive_base
        feature = body.newObject("PartDesign::Hole", name)
        feature.Profile = profile
        feature.Diameter = float(_argument(payload, 2, context="api.hole"))
        feature.DepthType = "ThroughAll" if bool(properties.get("through_all")) else "Dimension"
        if properties.get("depth_mm") is not None:
            feature.Depth = float(properties["depth_mm"])
        countersink = properties.get("countersink_diameter_mm")
        counterbore = properties.get("counterbore_diameter_mm")
        if countersink is not None:
            feature.HoleCutType = "Countersink"
            feature.HoleCutCustomValues = True
            feature.HoleCutDiameter = float(countersink)
            feature.HoleCutCountersinkAngle = float(
                properties.get("countersink_angle_degrees") or 90.0
            )
        elif counterbore is not None:
            feature.HoleCutType = "Counterbore"
            feature.HoleCutCustomValues = True
            feature.HoleCutDiameter = float(counterbore)
            feature.HoleCutDepth = float(properties["counterbore_depth_mm"])
        else:
            feature.HoleCutType = "None"
            feature.HoleCutCustomValues = False
        feature.Threaded = False
        feature.Refine = True
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
    elif operation in {"fillet", "chamfer", "thickness", "draft"}:
        base = _build_feature(
            body,
            _payload(_argument(payload, 0, context=f"api.{operation}"), context=f"api.{operation}.base"),
            memo,
            sketch_evidence,
        )
        body.Document.recompute()
        selection = _argument(payload, 1, context=f"api.{operation}")
        names, _details = _query_subelements(base.Shape, selection)
        native_type = {
            "fillet": "PartDesign::Fillet",
            "chamfer": "PartDesign::Chamfer",
            "thickness": "PartDesign::Thickness",
            "draft": "PartDesign::Draft",
        }[operation]
        feature = body.newObject(native_type, name)
        feature.Base = (base, names)
        if operation == "fillet":
            feature.Radius = float(_argument(payload, 2, context="api.fillet"))
        elif operation == "chamfer":
            feature.ChamferType = "Equal distance"
            feature.Size = float(_argument(payload, 2, context="api.chamfer"))
        elif operation == "thickness":
            feature.Value = float(_argument(payload, 2, context="api.thickness"))
            feature.Reversed = bool(properties.get("inward"))
            feature.Mode = "Skin"
            feature.Join = {
                "arc": "Arc",
                "tangent": "Intersection",
                "intersection": "Intersection",
            }[str(properties.get("join") or "arc")]
            feature.Intersection = str(properties.get("join") or "arc") == "intersection"
        else:
            feature.NeutralPlane = (
                _origin_feature(body, f"{properties.get('neutral_plane', 'XY')}_Plane"),
                [""],
            )
            feature.PullDirection = (
                _origin_feature(
                    body,
                    f"{str(properties.get('pull_direction') or 'Z').upper()}_Axis",
                ),
                [""],
            )
            feature.Angle = float(_argument(payload, 2, context="api.draft"))
            feature.Reversed = bool(properties.get("reversed"))
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
        tolerance = max(1.0e-7, abs(base_volume) * 1.0e-9)
        removes_material = subtractive_base is not None
        try:
            removed_volume = float(base_shape.cut(shape).Volume)
            added_volume = float(shape.cut(base_shape).Volume)
        except Exception as exc:
            raise PartDesignCandidateError(
                f"api.{operation} material effect could not be proven geometrically.",
                details={
                    "stage": "feature_postcondition",
                    "operation": operation,
                    "graph_id": graph_id,
                    "base_volume_mm3": base_volume,
                    "result_volume_mm3": result_volume,
                    "native_error": f"{type(exc).__name__}: {exc}",
                    "correction": (
                        "Repair the base or generated feature so OpenCascade can compare "
                        "their exact material regions."
                    ),
                },
            ) from exc
        changed_material = (
            removed_volume > tolerance and added_volume <= tolerance
            if removes_material
            else added_volume > tolerance and removed_volume <= tolerance
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
                    "removed_material_mm3": removed_volume,
                    "added_material_mm3": added_volume,
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


def _normalize_output_shape(shape: Any, output_type: str, *, output_name: str) -> Any:
    expected = {
        "solid": ("Solid", "Solids"),
        "shell": ("Shell", "Shells"),
        "face": ("Face", "Faces"),
        "wire": ("Wire", "Wires"),
    }
    if shape is None or shape.isNull() or not shape.isValid():
        raise PartDesignCandidateError(
            f"Part Design output {output_name!r} is null or invalid.",
            details={"stage": "output_shape_validation", "output": output_name},
        )
    if output_type == "compound":
        if str(shape.ShapeType) != "Compound":
            raise PartDesignCandidateError(
                f"Part Design output {output_name!r} declared compound but produced {shape.ShapeType}.",
                details={
                    "stage": "output_type_validation",
                    "output": output_name,
                    "declared_type": output_type,
                    "shape_type": str(shape.ShapeType),
                },
            )
        return shape
    expected_shape_type, collection_name = expected[output_type]
    if str(shape.ShapeType) == expected_shape_type:
        return shape
    children = list(getattr(shape, collection_name, []) or [])
    if len(children) == 1 and children[0].isValid() and not children[0].isNull():
        return children[0]
    raise PartDesignCandidateError(
        f"Part Design output {output_name!r} is not exactly one valid {output_type}.",
        details={
            "stage": "output_type_validation",
            "output": output_name,
            "declared_type": output_type,
            "shape_type": str(shape.ShapeType),
            "matching_child_count": len(children),
            "correction": (
                "Use api.compound(...) and declare type='compound' for deliberately "
                "disconnected geometry. Use api.boolean(..., operation='union', "
                "output_type='solid') only when the inputs overlap enough to form one "
                "valid connected solid."
                if output_type == "solid"
                else "Correct the operation or declared output type so they agree exactly."
            ),
        },
    )


def _evaluate_measurement_checks(
    body: Any,
    raw_checks: Any,
    memo: dict[str, Any],
    sketch_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(raw_checks, list):
        raise PartDesignCandidateError("Publication checks must be an array.")
    evidence: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_checks):
        check = _payload(raw, context=f"checks[{index}]")
        if check.get("operation") != "measure" or check.get("output_type") != "check":
            raise PartDesignCandidateError(
                f"checks[{index}] must come from api.measure.",
                details={"stage": "measurement_contract", "index": index},
            )
        shape_payload = _payload(
            _argument(check, 0, context="api.measure"), context="api.measure.shape"
        )
        shape = _build_model_shape(body, shape_payload, memo, sketch_evidence)
        facts = part_shape_facts(shape, max_subelements=0)
        quantity = str(_argument(check, 1, context="api.measure") or "")
        values = {
            "length_mm": float(facts["length_mm"]),
            "area_mm2": float(facts["area_mm2"]),
            "volume_mm3": float(facts["volume_mm3"]),
            "solid_count": float(facts["solids"]),
            "face_count": float(facts["faces"]),
            "edge_count": float(facts["edges"]),
        }
        actual = values[quantity]
        properties = _properties(check)
        tolerance = float(properties.get("tolerance") or 0.0)
        expected = properties.get("expected")
        minimum = properties.get("minimum")
        maximum = properties.get("maximum")
        accepted = True
        if expected is not None:
            accepted = accepted and abs(actual - float(expected)) <= tolerance
        if minimum is not None:
            accepted = accepted and actual >= float(minimum) - tolerance
        if maximum is not None:
            accepted = accepted and actual <= float(maximum) + tolerance
        item = {
            "index": index,
            "label": str(properties.get("label") or ""),
            "quantity": quantity,
            "actual": actual,
            "expected": expected,
            "minimum": minimum,
            "maximum": maximum,
            "tolerance": tolerance,
            "accepted": accepted,
        }
        if not accepted:
            raise PartDesignCandidateError(
                f"api.measure check {index + 1} rejected {quantity}={actual:g}.",
                details={"stage": "measurement_postcondition", **item},
            )
        evidence.append(item)
    return evidence


def validate_and_build_partdesign(
    document: Any,
    raw_result: Mapping[str, Any],
    expected_outputs: list[dict[str, Any]],
    root: Path,
    *,
    max_shape_subelements: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build and validate unified Body or standalone parametric modeling graphs."""

    if not isinstance(raw_result, Mapping) or list(raw_result) != [
        str(item["name"]) for item in expected_outputs
    ]:
        raise PartDesignCandidateError(
            "Part Design result order must exactly match expected_outputs.",
            details={"stage": "result_contract"},
        )
    outputs: list[dict[str, Any]] = []
    validation_outputs: list[dict[str, Any]] = []
    material_resolver = PartDesignMaterialResolver()
    for index, expected in enumerate(expected_outputs):
        name = str(expected["name"])
        output_type = str(expected.get("type") or "")
        if output_type not in _PUBLISHABLE_TYPES:
            raise PartDesignCandidateError(
                f"Part Design output {name!r} has unsupported type {output_type!r}."
            )
        definition = _payload(raw_result[name], context=f"result[{name!r}]")
        publication_operation = str(definition.get("operation") or "")
        if publication_operation not in {"body", "publish"}:
            raise PartDesignCandidateError(
                f"Part Design output {name!r} must be returned by api.body or api.publish."
            )
        if str(definition.get("output_type") or "") != output_type:
            raise PartDesignCandidateError(
                f"Part Design output {name!r} declaration disagrees with its API value.",
                details={
                    "stage": "output_type_contract",
                    "declared_type": output_type,
                    "api_output_type": definition.get("output_type"),
                },
            )
        if publication_operation == "body" and output_type != "solid":
            raise PartDesignCandidateError("api.body can publish only one exact solid.")
        properties = _properties(definition)
        body = document.addObject("PartDesign::Body", f"CandidateBody{index + 1}")
        if body is None:
            raise PartDesignCandidateError("FreeCAD did not create PartDesign::Body.")
        _set_label(body, properties, name)
        memo: dict[str, Any] = {}
        sketch_evidence: list[dict[str, Any]] = []
        source_definition = _payload(
            _argument(definition, 0, context=f"api.{publication_operation}"),
            context=f"api.{publication_operation}.shape",
        )
        final_feature = None
        if publication_operation == "body":
            if source_definition.get("output_type") == "feature":
                final_feature = _build_feature(
                    body, source_definition, memo, sketch_evidence
                )
            elif source_definition.get("output_type") == "solid":
                direct_shape = _build_model_shape(
                    body, source_definition, memo, sketch_evidence
                )
                final_feature = body.newObject(
                    "PartDesign::Feature", f"AdoptedResult_{index + 1}"
                )
                final_feature.Label = str(properties.get("label") or name)
                final_feature.Shape = direct_shape
            else:
                raise PartDesignCandidateError(
                    "api.body requires a Body feature or direct solid graph."
                )
            body.Tip = final_feature
            document.recompute()
            native_shape = getattr(body, "Shape", None)
        else:
            native_shape = _build_model_shape(
                body, source_definition, memo, sketch_evidence
            )
            document.recompute()
        shape = _normalize_output_shape(
            native_shape, output_type, output_name=name
        )
        facts = part_shape_facts(shape, max_subelements=max_shape_subelements)
        checks = _evaluate_measurement_checks(
            body,
            properties.get("checks") or [],
            memo,
            sketch_evidence,
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
        presentation, _native_material = material_resolver.resolve(
            definition,
            output_name=name,
        )
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
            "representation": publication_operation,
            "tip_type_id": str(getattr(final_feature, "TypeId", "") or ""),
            "tip_label": str(getattr(final_feature, "Label", "") or ""),
            "feature_count": len(feature_history),
            "feature_history": feature_history,
            "sketches": sketch_evidence,
            "interfaces": interfaces,
            "checks": checks,
            "presentation": presentation,
            "brep_sha256": digest,
        }
        outputs.append(
            {
                "name": name,
                "type": output_type,
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
