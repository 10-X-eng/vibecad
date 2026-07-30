# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded exact geometry evidence for internal mechanism evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
from typing import Any

STATIC_PAIR_EVIDENCE_SCHEMA = "vibecad-mechanism-static-pair-evidence-v1"
STATIC_MECHANISM_EVIDENCE_SCHEMA = "vibecad-mechanism-static-evidence-v1"

_COMPONENT_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_DECLARATION_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_INTERFACE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SUBELEMENT = re.compile(r"^(Face|Edge|Vertex)([1-9][0-9]*)$")
_MAX_COMPONENTS = 64
_MAX_PAIRS = (_MAX_COMPONENTS * (_MAX_COMPONENTS - 1)) // 2
_MAX_WITNESSES_PER_PAIR = 8
_MAX_CONTACT_WITNESSES = 4096


class MechanismGeometryError(ValueError):
    """A static mechanism geometry request cannot be evaluated exactly."""


def _error(path: str, message: str) -> MechanismGeometryError:
    return MechanismGeometryError(f"{path}: {message}")


def _number(value: Any, *, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(path, "must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise _error(path, "must be a finite number")
    return result


def _vector(value: Any, *, path: str, size: int) -> list[float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != size
    ):
        raise _error(path, f"must contain exactly {size} numbers")
    return [
        _number(item, path=f"{path}[{index}]")
        for index, item in enumerate(value)
    ]


def _placement(value: Any, *, path: str) -> Any:
    if not isinstance(value, Mapping) or set(value) != {
        "position",
        "rotation",
    }:
        raise _error(
            path,
            "must contain exactly position and quaternion rotation",
        )
    position = _vector(value["position"], path=f"{path}.position", size=3)
    rotation = _vector(value["rotation"], path=f"{path}.rotation", size=4)
    magnitude = math.sqrt(sum(item * item for item in rotation))
    if magnitude <= 1.0e-12:
        raise _error(f"{path}.rotation", "must be a non-zero quaternion")
    rotation = [item / magnitude for item in rotation]

    import FreeCAD as App

    native = App.Placement()
    native.Base = App.Vector(*position)
    native.Rotation = App.Rotation(*rotation)
    return native


def _placed_shape(
    value: Any,
    *,
    path: str,
    require_solid: bool = True,
) -> Any:
    if not isinstance(value, Mapping) or set(value) != {"shape", "placement"}:
        raise _error(path, "must contain exactly shape and placement")
    shape = value["shape"]
    try:
        if (
            shape is None
            or bool(shape.isNull())
            or not bool(shape.isValid())
            or (
                require_solid
                and len(list(getattr(shape, "Solids", []) or [])) < 1
            )
        ):
            expectation = (
                "at least one valid solid"
                if require_solid
                else "valid topology"
            )
            raise _error(path, f"shape must contain {expectation}")
        result = shape.copy()
        # An App::Link occurrence applies its Placement to the linked topology;
        # it does not multiply the source object's stored Shape placement.
        result.Placement = _placement(
            value["placement"],
            path=f"{path}.placement",
        )
    except MechanismGeometryError:
        raise
    except Exception as exc:
        raise _error(path, f"could not detach and place the shape: {exc}") from exc
    if bool(result.isNull()) or not bool(result.isValid()):
        raise _error(path, "placed shape is null or invalid")
    return result


def _bounds(shape: Any, *, path: str) -> dict[str, list[float]]:
    box = shape.BoundBox
    return {
        "minimum_mm": [
            _number(box.XMin, path=f"{path}.minimum_mm[0]"),
            _number(box.YMin, path=f"{path}.minimum_mm[1]"),
            _number(box.ZMin, path=f"{path}.minimum_mm[2]"),
        ],
        "maximum_mm": [
            _number(box.XMax, path=f"{path}.maximum_mm[0]"),
            _number(box.YMax, path=f"{path}.maximum_mm[1]"),
            _number(box.ZMax, path=f"{path}.maximum_mm[2]"),
        ],
    }


def _aabb_evidence(
    first: Mapping[str, Sequence[float]],
    second: Mapping[str, Sequence[float]],
) -> dict[str, Any]:
    first_minimum = list(first["minimum_mm"])
    first_maximum = list(first["maximum_mm"])
    second_minimum = list(second["minimum_mm"])
    second_maximum = list(second["maximum_mm"])
    gaps = [
        max(
            float(first_minimum[index]) - float(second_maximum[index]),
            float(second_minimum[index]) - float(first_maximum[index]),
            0.0,
        )
        for index in range(3)
    ]
    return {
        "overlaps_or_touches": all(gap == 0.0 for gap in gaps),
        "axis_gap_mm": gaps,
        "distance_mm": math.sqrt(sum(gap * gap for gap in gaps)),
    }


def _point(value: Any, *, path: str) -> list[float]:
    try:
        return [
            _number(value.x, path=f"{path}[0]"),
            _number(value.y, path=f"{path}[1]"),
            _number(value.z, path=f"{path}[2]"),
        ]
    except AttributeError as exc:
        raise _error(path, "must be an OCCT witness point") from exc


def _prepared_components(
    components: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, list[float]]]]:
    if (
        not isinstance(components, Mapping)
        or not 1 <= len(components) <= _MAX_COMPONENTS
    ):
        raise _error(
            "components",
            f"must contain 1-{_MAX_COMPONENTS} named solid shapes",
        )
    placed: dict[str, Any] = {}
    bounds: dict[str, dict[str, list[float]]] = {}
    for raw_name, value in components.items():
        if not isinstance(raw_name, str) or not _COMPONENT_ID.fullmatch(raw_name):
            raise _error(
                "components",
                "component names must be stable identifiers",
            )
        shape = _placed_shape(value, path=f"components.{raw_name}")
        placed[raw_name] = shape
        bounds[raw_name] = _bounds(
            shape,
            path=f"components.{raw_name}.bounds",
        )
    return placed, bounds


def _component_pairs(
    pairs: Sequence[Sequence[str]],
    *,
    component_names: set[str],
) -> list[tuple[str, str]]:
    if (
        not isinstance(pairs, Sequence)
        or isinstance(pairs, (str, bytes))
        or not 1 <= len(pairs) <= _MAX_PAIRS
    ):
        raise _error(
            "pairs",
            f"must contain 1-{_MAX_PAIRS} explicit component pairs",
        )
    clean_pairs: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for index, value in enumerate(pairs):
        path = f"pairs[{index}]"
        if (
            not isinstance(value, Sequence)
            or isinstance(value, (str, bytes))
            or len(value) != 2
        ):
            raise _error(path, "must contain exactly two component names")
        first_name, second_name = value
        if (
            not isinstance(first_name, str)
            or not isinstance(second_name, str)
            or not _COMPONENT_ID.fullmatch(first_name)
            or not _COMPONENT_ID.fullmatch(second_name)
        ):
            raise _error(path, "component names must be stable identifiers")
        if first_name not in component_names or second_name not in component_names:
            raise _error(path, "names an unknown component")
        if first_name == second_name:
            raise _error(path, "cannot compare a component with itself")
        key = tuple(sorted((first_name, second_name)))
        if key in seen_pairs:
            raise _error(path, "duplicates an unordered component pair")
        seen_pairs.add(key)
        clean_pairs.append((first_name, second_name))
    return clean_pairs


def _distance_evidence(
    first_shape: Any,
    second_shape: Any,
    *,
    path: str,
) -> tuple[float, list[Any], list[dict[str, list[float]]]]:
    try:
        distance, raw_witnesses, _supports = first_shape.distToShape(second_shape)
    except Exception as exc:
        raise _error(path, f"exact OCCT distance evaluation failed: {exc}") from exc
    clean_distance = _number(distance, path=f"{path}.minimum_distance_mm")
    if clean_distance < 0.0:
        raise _error(f"{path}.minimum_distance_mm", "must not be negative")
    try:
        raw_witness_list = list(raw_witnesses or [])
        witnesses = []
        for witness_index, pair in enumerate(
            raw_witness_list[:_MAX_WITNESSES_PER_PAIR]
        ):
            if (
                not isinstance(pair, Sequence)
                or isinstance(pair, (str, bytes))
                or len(pair) != 2
            ):
                raise _error(
                    f"{path}.witnesses[{witness_index}]",
                    "must contain two OCCT points",
                )
            witnesses.append(
                {
                    "first_point_mm": _point(
                        pair[0],
                        path=(
                            f"{path}.witnesses[{witness_index}].first_point_mm"
                        ),
                    ),
                    "second_point_mm": _point(
                        pair[1],
                        path=(
                            f"{path}.witnesses[{witness_index}].second_point_mm"
                        ),
                    ),
                }
            )
    except MechanismGeometryError:
        raise
    except Exception as exc:
        raise _error(
            f"{path}.witnesses",
            f"could not normalize exact OCCT witnesses: {exc}",
        ) from exc
    return clean_distance, raw_witness_list, witnesses


def _common_evidence(
    first_shape: Any,
    second_shape: Any,
    *,
    path: str,
) -> dict[str, Any]:
    try:
        common = first_shape.common(second_shape)
        if not bool(common.isNull()) and not bool(common.isValid()):
            raise RuntimeError("OCCT returned an invalid common shape")
        shape_type = "" if bool(common.isNull()) else str(common.ShapeType)
        volume = _number(common.Volume, path=f"{path}.volume_mm3")
        area = _number(common.Area, path=f"{path}.area_mm2")
        length = _number(common.Length, path=f"{path}.length_mm")
        if volume < 0.0 or area < 0.0 or length < 0.0:
            raise RuntimeError("OCCT returned a negative common measure")
        return {
            "shape_type": shape_type,
            "volume_mm3": volume,
            "area_mm2": area,
            "length_mm": length,
            "solid_count": len(list(common.Solids)),
            "face_count": len(list(common.Faces)),
            "edge_count": len(list(common.Edges)),
            "vertex_count": len(list(common.Vertexes)),
        }
    except MechanismGeometryError:
        raise
    except Exception as exc:
        raise _error(path, f"exact OCCT common evaluation failed: {exc}") from exc


def _body_pair_evidence(
    first_name: str,
    second_name: str,
    *,
    placed: Mapping[str, Any],
    bounds: Mapping[str, Mapping[str, Sequence[float]]],
    path: str,
) -> tuple[dict[str, Any], list[Any]]:
    first_shape = placed[first_name]
    second_shape = placed[second_name]
    broad_phase = _aabb_evidence(bounds[first_name], bounds[second_name])
    distance, raw_witnesses, witnesses = _distance_evidence(
        first_shape,
        second_shape,
        path=path,
    )
    common_evaluated = bool(broad_phase["overlaps_or_touches"])
    common = {
        "shape_type": "",
        "volume_mm3": 0.0,
        "area_mm2": 0.0,
        "length_mm": 0.0,
        "solid_count": 0,
        "face_count": 0,
        "edge_count": 0,
        "vertex_count": 0,
    }
    if common_evaluated:
        common = _common_evidence(
            first_shape,
            second_shape,
            path=f"{path}.common",
        )
    return (
        {
            "first_component": first_name,
            "second_component": second_name,
            "first_bounds": bounds[first_name],
            "second_bounds": bounds[second_name],
            "broad_phase": broad_phase,
            "minimum_distance_mm": distance,
            "witness_count": len(raw_witnesses),
            "witnesses_truncated": (
                len(raw_witnesses) > _MAX_WITNESSES_PER_PAIR
            ),
            "witnesses": witnesses,
            "common_evaluated": common_evaluated,
            "common_shape_type": common["shape_type"],
            "common_volume_mm3": common["volume_mm3"],
            "common_solid_count": common["solid_count"],
            "common_face_count": common["face_count"],
        },
        raw_witnesses,
    )


def _subelement(shape: Any, name: str, *, path: str) -> Any:
    match = _SUBELEMENT.fullmatch(name)
    if match is None:
        raise _error(path, "must name one FaceN, EdgeN, or VertexN")
    collection_name = {
        "Face": "Faces",
        "Edge": "Edges",
        "Vertex": "Vertexes",
    }[match.group(1)]
    values = list(getattr(shape, collection_name, []) or [])
    index = int(match.group(2)) - 1
    if not 0 <= index < len(values):
        raise _error(
            path,
            f"{name} is outside the source topology ({len(values)} {collection_name})",
        )
    return values[index].copy()


def _prepared_interfaces(
    components: Mapping[str, Mapping[str, Any]],
    interfaces: Mapping[str, Mapping[str, Sequence[str]]],
    requested: set[tuple[str, str]],
) -> dict[tuple[str, str], Any]:
    if not isinstance(interfaces, Mapping):
        raise _error("interfaces", "must be an object keyed by component")
    if any(name not in components for name in interfaces):
        raise _error("interfaces", "contains an unknown component")
    result: dict[tuple[str, str], Any] = {}
    for component_name, interface_name in sorted(requested):
        component_interfaces = interfaces.get(component_name)
        path = f"interfaces.{component_name}.{interface_name}"
        if not isinstance(component_interfaces, Mapping):
            raise _error(path, "is not published by the component")
        if not _INTERFACE_NAME.fullmatch(interface_name):
            raise _error(path, "must be a stable semantic interface name")
        raw_subelements = component_interfaces.get(interface_name)
        if (
            not isinstance(raw_subelements, Sequence)
            or isinstance(raw_subelements, (str, bytes))
            or not 1 <= len(raw_subelements) <= 64
        ):
            raise _error(
                path,
                "must resolve to 1-64 exact contact subelements",
            )
        subelements = [str(item) for item in raw_subelements]
        if len(subelements) != len(set(subelements)):
            raise _error(path, "contains duplicate contact subelements")
        component = components[component_name]
        source_shape = component.get("shape")
        if source_shape is None:
            raise _error(path, "component has no source shape")
        selected = [
            _subelement(
                source_shape,
                subelement,
                path=f"{path}[{index}]",
            )
            for index, subelement in enumerate(subelements)
        ]
        if len(selected) == 1:
            interface_shape = selected[0]
        else:
            try:
                import Part

                interface_shape = Part.makeCompound(selected)
            except Exception as exc:
                raise _error(
                    path,
                    f"could not build the contact-interface compound: {exc}",
                ) from exc
        result[(component_name, interface_name)] = _placed_shape(
            {
                "shape": interface_shape,
                "placement": component.get("placement"),
            },
            path=path,
            require_solid=False,
        )
    return result


def _point_to_shape_distance(point: Any, shape: Any, *, path: str) -> float:
    try:
        import Part

        distance, _witnesses, _supports = Part.Vertex(point).distToShape(shape)
    except Exception as exc:
        raise _error(path, f"could not measure witness-to-interface distance: {exc}") from exc
    result = _number(distance, path=path)
    if result < 0.0:
        raise _error(path, "must not be negative")
    return result


def _section_coverage(
    section: Any,
    interface: Any,
    *,
    tolerance_mm: float,
    path: str,
) -> dict[str, Any]:
    edge_length = _number(section.Length, path=f"{path}.edge_length_mm")
    face_area = _number(section.Area, path=f"{path}.face_area_mm2")
    common = _common_evidence(
        section,
        interface,
        path=f"{path}.common",
    )
    covered_edge_length = min(edge_length, float(common["length_mm"]))
    covered_face_area = min(face_area, float(common["area_mm2"]))
    area_tolerance = tolerance_mm * tolerance_mm
    uncovered_edges = int(covered_edge_length + tolerance_mm < edge_length)
    uncovered_faces = int(covered_face_area + area_tolerance < face_area)

    vertex_distances = [
        _point_to_shape_distance(
            vertex.Point,
            interface,
            path=f"{path}.vertices[{index}].distance_mm",
        )
        for index, vertex in enumerate(list(section.Vertexes))
    ]
    uncovered_vertices = sum(
        distance > tolerance_mm for distance in vertex_distances
    )
    return {
        "edge_length_mm": edge_length,
        "covered_edge_length_mm": covered_edge_length,
        "uncovered_edge_count": uncovered_edges,
        "face_area_mm2": face_area,
        "covered_face_area_mm2": covered_face_area,
        "uncovered_face_count": uncovered_faces,
        "vertex_count": len(vertex_distances),
        "uncovered_vertex_count": uncovered_vertices,
        "maximum_vertex_distance_mm": max(vertex_distances, default=0.0),
        "complete": (
            uncovered_edges == 0
            and uncovered_faces == 0
            and uncovered_vertices == 0
        ),
    }


def _interface_evidence(
    first_shape: Any,
    second_shape: Any,
    first_interface: Any,
    second_interface: Any,
    raw_body_witnesses: Sequence[Any],
    *,
    first_name: str,
    second_name: str,
    first_interface_name: str,
    second_interface_name: str,
    tolerance_mm: float,
    body_distance_mm: float,
    path: str,
) -> dict[str, Any]:
    interface_distance, raw_interface_witnesses, interface_witnesses = (
        _distance_evidence(
            first_interface,
            second_interface,
            path=f"{path}.interface_distance",
        )
    )
    interface_common = _common_evidence(
        first_interface,
        second_interface,
        path=f"{path}.interface_common",
    )

    body_witnesses_complete = len(raw_body_witnesses) <= _MAX_CONTACT_WITNESSES
    body_witness_distances: list[dict[str, float]] = []
    if body_witnesses_complete:
        for index, witness in enumerate(raw_body_witnesses):
            if (
                not isinstance(witness, Sequence)
                or isinstance(witness, (str, bytes))
                or len(witness) != 2
            ):
                raise _error(
                    f"{path}.body_witnesses[{index}]",
                    "must contain two OCCT points",
                )
            body_witness_distances.append(
                {
                    "first_interface_distance_mm": _point_to_shape_distance(
                        witness[0],
                        first_interface,
                        path=(
                            f"{path}.body_witnesses[{index}]."
                            "first_interface_distance_mm"
                        ),
                    ),
                    "second_interface_distance_mm": _point_to_shape_distance(
                        witness[1],
                        second_interface,
                        path=(
                            f"{path}.body_witnesses[{index}]."
                            "second_interface_distance_mm"
                        ),
                    ),
                }
            )
    witnesses_on_interfaces = (
        None
        if not body_witnesses_complete or not body_witness_distances
        else all(
            item["first_interface_distance_mm"] <= tolerance_mm
            and item["second_interface_distance_mm"] <= tolerance_mm
            for item in body_witness_distances
        )
    )

    try:
        section = first_shape.section(second_shape)
        if not bool(section.isNull()) and not bool(section.isValid()):
            raise RuntimeError("OCCT returned an invalid section shape")
    except Exception as exc:
        raise _error(path, f"exact OCCT section evaluation failed: {exc}") from exc
    section_is_null = bool(section.isNull())
    section_shape_type = "" if section_is_null else str(section.ShapeType)
    section_edge_count = 0 if section_is_null else len(list(section.Edges))
    section_face_count = 0 if section_is_null else len(list(section.Faces))
    section_vertex_count = 0 if section_is_null else len(list(section.Vertexes))
    section_has_topology = (
        section_edge_count + section_face_count + section_vertex_count > 0
    )
    first_coverage = (
        None
        if not section_has_topology
        else _section_coverage(
            section,
            first_interface,
            tolerance_mm=tolerance_mm,
            path=f"{path}.section.first_interface",
        )
    )
    second_coverage = (
        None
        if not section_has_topology
        else _section_coverage(
            section,
            second_interface,
            tolerance_mm=tolerance_mm,
            path=f"{path}.section.second_interface",
        )
    )
    section_on_interfaces = (
        None
        if first_coverage is None or second_coverage is None
        else bool(first_coverage["complete"] and second_coverage["complete"])
    )
    contact_locus_on_interfaces = (
        None
        if witnesses_on_interfaces is None
        else bool(
            witnesses_on_interfaces
            and section_on_interfaces is not False
            and interface_distance <= tolerance_mm
        )
    )
    return {
        "first_component": first_name,
        "second_component": second_name,
        "first_interface": first_interface_name,
        "second_interface": second_interface_name,
        "minimum_distance_mm": interface_distance,
        "witness_count": len(raw_interface_witnesses),
        "witnesses_truncated": (
            len(raw_interface_witnesses) > _MAX_WITNESSES_PER_PAIR
        ),
        "witnesses": interface_witnesses,
        "common": interface_common,
        "body_witness_count": len(raw_body_witnesses),
        "body_witnesses_complete": body_witnesses_complete,
        "body_witness_distances": body_witness_distances[
            :_MAX_WITNESSES_PER_PAIR
        ],
        "body_witnesses_on_interfaces": witnesses_on_interfaces,
        "section": {
            "evaluated": True,
            "shape_type": section_shape_type,
            "edge_count": section_edge_count,
            "face_count": section_face_count,
            "vertex_count": section_vertex_count,
            "has_topology": section_has_topology,
            "first_interface_coverage": first_coverage,
            "second_interface_coverage": second_coverage,
            "all_on_interfaces": section_on_interfaces,
        },
        "body_within_tolerance": body_distance_mm <= tolerance_mm,
        "interfaces_within_tolerance": interface_distance <= tolerance_mm,
        "contact_locus_on_interfaces": contact_locus_on_interfaces,
    }


def measure_static_component_pairs(
    components: Mapping[str, Mapping[str, Any]],
    pairs: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Return raw broad-phase and exact OCCT evidence for explicit pairs.

    The function does not apply a clearance tolerance, contact policy, or
    pass/fail interpretation. Source shapes are copied before placements are
    applied and are never mutated.
    """

    placed, bounds = _prepared_components(components)
    clean_pairs = _component_pairs(pairs, component_names=set(placed))
    results = []
    common_evaluated_count = 0
    for index, (first_name, second_name) in enumerate(clean_pairs):
        evidence, _raw_witnesses = _body_pair_evidence(
            first_name,
            second_name,
            placed=placed,
            bounds=bounds,
            path=f"pairs[{index}]",
        )
        common_evaluated_count += int(bool(evidence["common_evaluated"]))
        results.append(evidence)
    return {
        "schema": STATIC_PAIR_EVIDENCE_SCHEMA,
        "component_count": len(placed),
        "pair_count": len(results),
        "common_evaluated_count": common_evaluated_count,
        "broad_phase_rejected_common_count": (
            len(results) - common_evaluated_count
        ),
        "pairs": results,
    }


def measure_static_mechanism_pairs(
    components: Mapping[str, Mapping[str, Any]],
    declarations: Sequence[Mapping[str, Any]],
    *,
    interfaces: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> dict[str, Any]:
    """Measure exact static evidence for normalized mechanism declarations.

    Each declaration contains a stable ``declaration_id``, two component names,
    an explicit ``tolerance_mm``, and either both semantic interface names or
    neither. The returned evidence is raw geometry evidence; policy verdicts
    are assigned by :mod:`VibeCADMechanismEngine`.
    """

    import Part

    placed, bounds = _prepared_components(components)
    if (
        not isinstance(declarations, Sequence)
        or isinstance(declarations, (str, bytes))
        or not 1 <= len(declarations) <= 128
    ):
        raise _error("declarations", "must contain 1-128 explicit pair declarations")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    requested_interfaces: set[tuple[str, str]] = set()
    for index, value in enumerate(declarations):
        path = f"declarations[{index}]"
        if not isinstance(value, Mapping) or set(value) != {
            "declaration_id",
            "first_component",
            "second_component",
            "tolerance_mm",
            "first_interface",
            "second_interface",
        }:
            raise _error(path, "has malformed fields")
        declaration_id = value["declaration_id"]
        if (
            not isinstance(declaration_id, str)
            or not _DECLARATION_ID.fullmatch(declaration_id)
            or declaration_id in seen_ids
        ):
            raise _error(f"{path}.declaration_id", "must be one unique stable identifier")
        seen_ids.add(declaration_id)
        first_name = value["first_component"]
        second_name = value["second_component"]
        _component_pairs(
            [[first_name, second_name]],
            component_names=set(placed),
        )
        pair = tuple(sorted((str(first_name), str(second_name))))
        if pair in seen_pairs:
            raise _error(path, "duplicates an unordered component pair")
        seen_pairs.add(pair)
        tolerance = _number(value["tolerance_mm"], path=f"{path}.tolerance_mm")
        if not 0.0 < tolerance <= 1.0e3:
            raise _error(
                f"{path}.tolerance_mm",
                "must be greater than zero and at most 1000",
            )
        first_interface = value["first_interface"]
        second_interface = value["second_interface"]
        if (first_interface is None) != (second_interface is None):
            raise _error(path, "must name both semantic interfaces or neither")
        if first_interface is not None:
            for component_name, interface_name, field in (
                (str(first_name), first_interface, "first_interface"),
                (str(second_name), second_interface, "second_interface"),
            ):
                if (
                    not isinstance(interface_name, str)
                    or not _INTERFACE_NAME.fullmatch(interface_name)
                ):
                    raise _error(
                        f"{path}.{field}",
                        "must be a stable semantic interface name",
                    )
                requested_interfaces.add((component_name, interface_name))
        normalized.append(
            {
                "declaration_id": declaration_id,
                "first_component": str(first_name),
                "second_component": str(second_name),
                "tolerance_mm": tolerance,
                "first_interface": first_interface,
                "second_interface": second_interface,
            }
        )
    prepared_interfaces = _prepared_interfaces(
        components,
        interfaces or {},
        requested_interfaces,
    )

    results: list[dict[str, Any]] = []
    complete_count = 0
    for index, declaration in enumerate(normalized):
        first_name = declaration["first_component"]
        second_name = declaration["second_component"]
        try:
            body, raw_witnesses = _body_pair_evidence(
                first_name,
                second_name,
                placed=placed,
                bounds=bounds,
                path=f"declarations[{index}].body",
            )
            interface_evidence = None
            first_interface_name = declaration["first_interface"]
            second_interface_name = declaration["second_interface"]
            if first_interface_name is not None:
                interface_evidence = _interface_evidence(
                    placed[first_name],
                    placed[second_name],
                    prepared_interfaces[(first_name, first_interface_name)],
                    prepared_interfaces[(second_name, second_interface_name)],
                    raw_witnesses,
                    first_name=first_name,
                    second_name=second_name,
                    first_interface_name=first_interface_name,
                    second_interface_name=second_interface_name,
                    tolerance_mm=declaration["tolerance_mm"],
                    body_distance_mm=float(body["minimum_distance_mm"]),
                    path=f"declarations[{index}].interfaces",
                )
            results.append(
                {
                    **declaration,
                    "status": "complete",
                    "error": "",
                    "body": body,
                    "interfaces": interface_evidence,
                }
            )
            complete_count += 1
        except Exception as exc:
            results.append(
                {
                    **declaration,
                    "status": "indeterminate",
                    "error": str(exc)[:512],
                    "body": None,
                    "interfaces": None,
                }
            )
    return {
        "schema": STATIC_MECHANISM_EVIDENCE_SCHEMA,
        "geometry_engine": {
            "name": "OpenCASCADE",
            "version": str(getattr(Part, "OCC_VERSION", "") or "unknown"),
        },
        "component_count": len(placed),
        "declaration_count": len(results),
        "complete_count": complete_count,
        "indeterminate_count": len(results) - complete_count,
        "declarations": results,
    }
