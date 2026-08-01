# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native Assembly evaluator for production VibeScript programs."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

from VibeCADDocumentReferences import (
    DocumentReferenceError,
    normalize_document_reference,
)
from VibeCADMechanismEngine import (
    MECHANISM_SCENARIO_SCHEMA,
    MECHANISM_SOLVE_REPORT_SCHEMA,
    MECHANISM_STATIC_CHECK_SCHEMA,
    evaluate_static_mechanism_check,
    evaluate_mechanism_scenario,
    mechanism_scenario_sha256,
    normalize_mechanism_static_check,
    normalize_mechanism_scenario,
)
from VibeCADMechanismGeometry import measure_static_mechanism_pairs
from vibescript_domain_api import DomainValue
from vibescript_part_worker import (
    configure_part_references,
    detached_reference_shape,
    part_shape_facts,
)


_JOINT_NATIVE = {
    "fixed": "Fixed",
    "revolute": "Revolute",
    "cylindrical": "Cylindrical",
    "slider": "Slider",
    "ball": "Ball",
    "distance": "Distance",
    "parallel": "Parallel",
    "perpendicular": "Perpendicular",
    "angle": "Angle",
    "rack_pinion": "RackPinion",
    "screw": "Screw",
    "gears": "Gears",
    "belt": "Belt",
}
_SOLVER_VERDICTS = {
    0: "solved",
    -1: "solver_error",
    -2: "redundant_constraints",
    -3: "conflicting_constraints",
    -4: "over_constrained",
    -5: "malformed_constraints",
    -6: "no_grounded_component",
}
_SUBELEMENT = re.compile(r"^(Face|Edge|Vertex)([1-9][0-9]*)$")
_REFERENCE_METADATA: Mapping[tuple[str, str], Mapping[str, Any]] = MappingProxyType({})
_SIMULATION_TRACE_SCHEMA = "vibecad-assembly-simulation-trace-v1"
_MAX_SIMULATION_TRACE_BYTES = 64 * 1024 * 1024
_EXPLODED_VIEW_SCHEMA = "vibecad-assembly-exploded-view-v1"
_ASSEMBLY_BOM_SCHEMA = "vibecad-assembly-bom-v1"
_ASSEMBLY_HIERARCHY_SCHEMA = "vibecad-assembly-source-hierarchy-v1"
_MAX_HIERARCHY_NODES = 512
_MAX_HIERARCHY_OCCURRENCES = 2048
_MAX_HIERARCHY_JOINTS = 1024
_MAX_HIERARCHY_SHAPES = 256
_MAX_HIERARCHY_DEPTH = 16
_EXPECTED_HIERARCHY_LIMITS = {
    "maximum_depth": _MAX_HIERARCHY_DEPTH,
    "nodes": _MAX_HIERARCHY_NODES,
    "occurrences": _MAX_HIERARCHY_OCCURRENCES,
    "joints": _MAX_HIERARCHY_JOINTS,
    "shape_artifacts": _MAX_HIERARCHY_SHAPES,
}
_REFERENCE_HIERARCHIES: Mapping[tuple[str, str], Mapping[str, Any]] = MappingProxyType(
    {}
)


class AssemblyCandidateError(RuntimeError):
    """A model-facing native Assembly failure with structured diagnostics."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.details = dict(details or {})
        super().__init__(message)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_matrix_payload(value: Any, *, context: str) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != 16
        or any(isinstance(item, bool) for item in value)
    ):
        raise ValueError(f"{context} must contain exactly 16 finite numbers.")
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context} must contain exactly 16 finite numbers.") from exc
    if any(not math.isfinite(item) for item in result):
        raise ValueError(f"{context} must contain exactly 16 finite numbers.")
    return result


def _validate_hierarchy_property(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object.")
    common = {"name", "property_type", "kind", "value"}
    kind = str(value.get("kind") or "")
    expected = common | ({"choices"} if kind == "enumeration" else set())
    if kind == "quantity":
        expected.add("assignment")
    if set(value) != expected:
        raise ValueError(f"{context} has malformed fields.")
    name = str(value.get("name") or "")
    property_type = str(value.get("property_type") or "")
    if (
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name)
        or not property_type.startswith("App::Property")
        or kind not in {"string", "enumeration", "boolean", "integer", "float", "quantity"}
    ):
        raise ValueError(f"{context} has an invalid scalar property contract.")
    clean = dict(value)
    if kind in {"string", "enumeration"}:
        text = str(value.get("value") or "")
        if len(text) > 4096:
            raise ValueError(f"{context}.value exceeds 4096 characters.")
        clean["value"] = text
    elif kind == "boolean":
        if not isinstance(value.get("value"), bool):
            raise ValueError(f"{context}.value must be boolean.")
    elif kind == "integer":
        if type(value.get("value")) is not int:
            raise ValueError(f"{context}.value must be an integer.")
    else:
        raw_number = value.get("value")
        if isinstance(raw_number, bool):
            raise ValueError(f"{context}.value must be finite.")
        try:
            number = float(raw_number)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{context}.value must be finite.") from exc
        if not math.isfinite(number):
            raise ValueError(f"{context}.value must be finite.")
        clean["value"] = number
    if kind == "enumeration":
        choices = value.get("choices")
        if (
            not isinstance(choices, list)
            or not 1 <= len(choices) <= 256
            or any(not isinstance(item, str) or len(item) > 4096 for item in choices)
            or len(set(choices)) != len(choices)
            or clean["value"] not in choices
        ):
            raise ValueError(f"{context}.choices is invalid.")
        clean["choices"] = list(choices)
    if kind == "quantity":
        assignment = str(value.get("assignment") or "")
        if not assignment or len(assignment) > 4096:
            raise ValueError(f"{context}.assignment is invalid.")
        clean["assignment"] = assignment
    return clean


def _bounded_json(
    value: Any,
    *,
    context: str,
    depth: int = 0,
    budget: list[int] | None = None,
) -> Any:
    """Authenticate a small JSON value without silently stringifying it."""

    if budget is None:
        budget = [4096]
    budget[0] -= 1
    if budget[0] < 0 or depth > 12:
        raise ValueError(f"{context} exceeds the bounded JSON complexity limit.")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > 4096:
            raise ValueError(f"{context} contains a string longer than 4096 characters.")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{context} contains a non-finite number.")
        return value
    if isinstance(value, list):
        return [
            _bounded_json(
                item,
                context=f"{context}[{index}]",
                depth=depth + 1,
                budget=budget,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                raise ValueError(f"{context} contains an invalid object key.")
            result[key] = _bounded_json(
                item,
                context=f"{context}.{key}",
                depth=depth + 1,
                budget=budget,
            )
        return result
    raise ValueError(f"{context} must contain JSON values only.")


def _validate_hierarchy_reference_contract(
    value: Any, *, context: str
) -> dict[str, Any]:
    expected = {
        "source_kind",
        "source_program_id",
        "source_program_domain",
        "source_revision",
        "transient_topology",
        "requires_semantic_interfaces",
        "published_interfaces",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{context} has malformed fields.")
    clean = _bounded_json(value, context=context)
    for name in (
        "source_kind",
        "source_program_id",
        "source_program_domain",
        "source_revision",
    ):
        if not isinstance(clean[name], str):
            raise ValueError(f"{context}.{name} must be a string.")
    if not clean["source_kind"] or len(clean["source_kind"]) > 128:
        raise ValueError(f"{context}.source_kind is invalid.")
    for name in ("transient_topology", "requires_semantic_interfaces"):
        if not isinstance(clean[name], bool):
            raise ValueError(f"{context}.{name} must be boolean.")
    interfaces = clean["published_interfaces"]
    if not isinstance(interfaces, dict) or len(interfaces) > 64:
        raise ValueError(f"{context}.published_interfaces is invalid.")
    for name, interface in interfaces.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
            raise ValueError(f"{context}.published_interfaces has invalid name {name!r}.")
        if not isinstance(interface, dict):
            raise ValueError(
                f"{context}.published_interfaces.{name} must be an object."
            )
    return clean


def _validate_hierarchy_joint(
    value: Any,
    *,
    context: str,
    available_paths: set[str],
) -> dict[str, Any]:
    expected = {
        "name",
        "label",
        "native_type",
        "suppressed",
        "distance",
        "distance2",
        "angle",
        "offset1_matrix",
        "offset2_matrix",
        "placement1_matrix",
        "placement2_matrix",
        "length_limits",
        "angle_limits",
        "reference1",
        "reference2",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{context} has malformed fields.")
    name = str(value.get("name") or "")
    label = str(value.get("label") or "")
    native_type = str(value.get("native_type") or "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"{context}.name is not a FreeCAD object name.")
    if len(label) > 4096:
        raise ValueError(f"{context}.label exceeds 4096 characters.")
    if native_type not in set(_JOINT_NATIVE.values()):
        raise ValueError(f"{context}.native_type {native_type!r} is unsupported.")
    if not isinstance(value.get("suppressed"), bool):
        raise ValueError(f"{context}.suppressed must be boolean.")
    clean = dict(value)
    for field in ("distance", "distance2", "angle"):
        raw = value.get(field)
        if isinstance(raw, bool):
            raise ValueError(f"{context}.{field} must be finite.")
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{context}.{field} must be finite.") from exc
        if not math.isfinite(number):
            raise ValueError(f"{context}.{field} must be finite.")
        clean[field] = number
    for field in (
        "offset1_matrix",
        "offset2_matrix",
        "placement1_matrix",
        "placement2_matrix",
    ):
        clean[field] = _finite_matrix_payload(
            value.get(field), context=f"{context}.{field}"
        )
    for field in ("length_limits", "angle_limits"):
        raw_limits = value.get(field)
        if not isinstance(raw_limits, list) or len(raw_limits) != 2:
            raise ValueError(f"{context}.{field} must contain exactly [minimum, maximum].")
        limits: list[float | None] = []
        for limit_index, raw_limit in enumerate(raw_limits):
            if raw_limit is None:
                limits.append(None)
                continue
            if isinstance(raw_limit, bool):
                raise ValueError(f"{context}.{field}[{limit_index}] must be finite or null.")
            try:
                number = float(raw_limit)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{context}.{field}[{limit_index}] must be finite or null."
                ) from exc
            if not math.isfinite(number):
                raise ValueError(f"{context}.{field}[{limit_index}] must be finite or null.")
            limits.append(number)
        if limits[0] is not None and limits[1] is not None and limits[0] > limits[1]:
            raise ValueError(f"{context}.{field} minimum exceeds its maximum.")
        clean[field] = limits
    for field in ("reference1", "reference2"):
        raw_reference = value.get(field)
        if not isinstance(raw_reference, dict) or set(raw_reference) != {
            "occurrence_path",
            "subelements",
        }:
            raise ValueError(f"{context}.{field} has malformed fields.")
        occurrence_path = str(raw_reference.get("occurrence_path") or "")
        if occurrence_path not in available_paths:
            raise ValueError(
                f"{context}.{field}.occurrence_path {occurrence_path!r} is not present "
                "in this source Assembly."
            )
        subelements = raw_reference.get("subelements")
        if (
            not isinstance(subelements, list)
            or len(subelements) != 2
            or any(
                not isinstance(item, str)
                or len(item) > 4096
                or "/" in item
                for item in subelements
            )
        ):
            raise ValueError(
                f"{context}.{field}.subelements must contain exactly two bounded native names."
            )
        clean[field] = {
            "occurrence_path": occurrence_path,
            "subelements": list(subelements),
        }
    return clean


def _load_assembly_hierarchy(root: Path, value: Any, *, context: str) -> dict[str, Any]:
    """Authenticate the host-staged source graph and every nested BREP."""

    import Part

    if not isinstance(value, dict) or value.get("schema") != _ASSEMBLY_HIERARCHY_SCHEMA:
        raise ValueError(f"{context} has an unsupported Assembly hierarchy schema.")
    if set(value) != {
        "schema",
        "root_node_id",
        "nodes",
        "occurrence_paths",
        "counts",
        "limits",
    }:
        raise ValueError(f"{context} has malformed top-level fields.")
    nodes_raw = value.get("nodes")
    if not isinstance(nodes_raw, list) or not 1 <= len(nodes_raw) <= _MAX_HIERARCHY_NODES:
        raise ValueError(f"{context}.nodes must contain 1-{_MAX_HIERARCHY_NODES} nodes.")
    nodes: list[dict[str, Any]] = []
    node_by_id: dict[str, dict[str, Any]] = {}
    occurrence_ids: set[str] = set()
    occurrence_count = 0
    joint_count = 0
    shape_count = 0
    loaded_shapes: dict[str, Any] = {}
    resolved_root = Path(root).resolve()
    for node_index, raw in enumerate(nodes_raw):
        node_context = f"{context}.nodes[{node_index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{node_context} must be an object.")
        required = {
            "node_id",
            "kind",
            "identity",
            "document_name",
            "document_file_name",
            "label",
            "type_id",
            "bom_properties",
            "occurrences",
        }
        optional = {
            "reference_contract",
            "shape_artifact",
            "grounded_occurrence_paths",
            "joints",
        }
        if not required <= set(raw) or set(raw) - required - optional:
            raise ValueError(f"{node_context} has malformed fields.")
        node_id = str(raw.get("node_id") or "")
        kind = str(raw.get("kind") or "")
        identity = raw.get("identity")
        document_uid = (
            identity.get("document_uid") if isinstance(identity, dict) else None
        )
        object_name = identity.get("object_name") if isinstance(identity, dict) else None
        if (
            not re.fullmatch(r"n[0-9]{4}", node_id)
            or node_id in node_by_id
            or kind not in {"assembly", "part", "shape"}
            or not isinstance(identity, dict)
            or set(identity) != {"document_uid", "object_name"}
            or not isinstance(document_uid, str)
            or not document_uid
            or len(document_uid) > 256
            or not isinstance(object_name, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", object_name)
        ):
            raise ValueError(f"{node_context} has an invalid identity or kind.")
        for field, maximum in (("document_name", 256), ("label", 4096), ("type_id", 256)):
            if not isinstance(raw.get(field), str) or len(raw[field]) > maximum:
                raise ValueError(f"{node_context}.{field} is invalid.")
        file_name = str(raw.get("document_file_name") or "")
        if (
            not isinstance(raw.get("document_file_name"), str)
            or len(file_name) > 4096
            or (file_name and Path(file_name).name != file_name)
        ):
            raise ValueError(f"{node_context}.document_file_name must be a basename.")
        properties_raw = raw.get("bom_properties")
        if not isinstance(properties_raw, list) or len(properties_raw) > 64:
            raise ValueError(f"{node_context}.bom_properties exceeds 64 entries.")
        properties = [
            _validate_hierarchy_property(item, context=f"{node_context}.bom_properties[{index}]")
            for index, item in enumerate(properties_raw)
        ]
        if len({str(item["name"]) for item in properties}) != len(properties):
            raise ValueError(f"{node_context}.bom_properties contains duplicate names.")
        occurrences_raw = raw.get("occurrences")
        if not isinstance(occurrences_raw, list):
            raise ValueError(f"{node_context}.occurrences must be a list.")
        occurrences = []
        occurrence_names: set[str] = set()
        for occurrence_index, occurrence_raw in enumerate(occurrences_raw):
            occurrence_context = f"{node_context}.occurrences[{occurrence_index}]"
            if not isinstance(occurrence_raw, dict) or set(occurrence_raw) != {
                "occurrence_id",
                "name",
                "label",
                "type_id",
                "link_mode",
                "rigid",
                "placement_matrix",
                "scale",
                "source_node_id",
            }:
                raise ValueError(f"{occurrence_context} has malformed fields.")
            occurrence_id = str(occurrence_raw.get("occurrence_id") or "")
            name = str(occurrence_raw.get("name") or "")
            link_mode = str(occurrence_raw.get("link_mode") or "")
            if (
                not re.fullmatch(r"o[0-9]{5}", occurrence_id)
                or occurrence_id in occurrence_ids
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                or name in occurrence_names
                or link_mode not in {"assembly_link", "link", "direct"}
                or not isinstance(occurrence_raw.get("rigid"), bool)
                or not isinstance(occurrence_raw.get("label"), str)
                or len(occurrence_raw["label"]) > 4096
                or not isinstance(occurrence_raw.get("type_id"), str)
                or len(occurrence_raw["type_id"]) > 256
                or not isinstance(occurrence_raw.get("source_node_id"), str)
                or not re.fullmatch(
                    r"n[0-9]{4}", str(occurrence_raw.get("source_node_id") or "")
                )
            ):
                raise ValueError(f"{occurrence_context} has invalid occurrence identity.")
            try:
                scale = float(occurrence_raw.get("scale"))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{occurrence_context}.scale is invalid.") from exc
            if not math.isfinite(scale) or abs(scale) <= 1.0e-12:
                raise ValueError(f"{occurrence_context}.scale is invalid.")
            occurrence = dict(occurrence_raw)
            occurrence["placement_matrix"] = _finite_matrix_payload(
                occurrence_raw.get("placement_matrix"),
                context=f"{occurrence_context}.placement_matrix",
            )
            occurrence["scale"] = scale
            occurrences.append(occurrence)
            occurrence_ids.add(occurrence_id)
            occurrence_names.add(name)
            occurrence_count += 1
            if occurrence_count > _MAX_HIERARCHY_OCCURRENCES:
                raise ValueError(
                    f"{context} exceeds {_MAX_HIERARCHY_OCCURRENCES} occurrences."
                )
        clean = dict(raw)
        clean["bom_properties"] = properties
        clean["occurrences"] = occurrences
        if "reference_contract" in raw:
            clean["reference_contract"] = _validate_hierarchy_reference_contract(
                raw["reference_contract"], context=f"{node_context}.reference_contract"
            )
        shape_artifact = raw.get("shape_artifact")
        if shape_artifact is not None:
            if not isinstance(shape_artifact, dict) or set(shape_artifact) != {
                "artifact_path",
                "artifact_sha256",
                "artifact_bytes",
                "shape_type",
                "facts",
            }:
                raise ValueError(f"{node_context}.shape_artifact is malformed.")
            path = (resolved_root / str(shape_artifact.get("artifact_path") or "")).resolve()
            if resolved_root not in path.parents or not path.is_file():
                raise ValueError(f"{node_context} BREP is missing or outside worker staging.")
            artifact_bytes = int(shape_artifact.get("artifact_bytes") or -1)
            if path.stat().st_size != artifact_bytes or _sha256_path(path) != str(
                shape_artifact.get("artifact_sha256") or ""
            ):
                raise ValueError(f"{node_context} BREP identity changed during transfer.")
            shape = Part.Shape()
            shape.importBrep(str(path))
            if shape.isNull() or not shape.isValid():
                raise ValueError(f"{node_context} BREP is not a valid Shape.")
            facts = part_shape_facts(shape, max_subelements=32)
            reported = shape_artifact.get("facts")
            if not isinstance(reported, dict):
                raise ValueError(f"{node_context} BREP has no topology facts.")
            for field in ("shape_type", "solids", "shells", "faces", "wires", "edges", "vertices"):
                if reported.get(field) != facts.get(field):
                    raise ValueError(
                        f"{node_context} BREP topology changed during transfer ({field})."
                    )
            if str(shape_artifact.get("shape_type") or "") != str(shape.ShapeType):
                raise ValueError(f"{node_context} BREP changed shape type during transfer.")
            loaded_shapes[node_id] = shape
            shape_count += 1
            if shape_count > _MAX_HIERARCHY_SHAPES:
                raise ValueError(f"{context} exceeds {_MAX_HIERARCHY_SHAPES} shapes.")
        elif kind == "shape":
            raise ValueError(f"{node_context} is a shape leaf without a BREP artifact.")
        grounded = raw.get("grounded_occurrence_paths", [])
        joints = raw.get("joints", [])
        if kind == "assembly":
            if not isinstance(grounded, list) or any(
                not isinstance(item, str) or not item for item in grounded
            ):
                raise ValueError(f"{node_context}.grounded_occurrence_paths is invalid.")
            if not isinstance(joints, list):
                raise ValueError(f"{node_context}.joints must be a list.")
            joint_count += len(joints)
            if joint_count > _MAX_HIERARCHY_JOINTS:
                raise ValueError(f"{context} exceeds {_MAX_HIERARCHY_JOINTS} joints.")
        elif grounded or joints:
            raise ValueError(f"{node_context} is not an Assembly but contains joint state.")
        nodes.append(clean)
        node_by_id[node_id] = clean
    root_node_id = str(value.get("root_node_id") or "")
    root_node = node_by_id.get(root_node_id)
    if root_node is None or str(root_node.get("kind") or "") not in {
        "assembly",
        "part",
    }:
        raise ValueError(
            f"{context}.root_node_id must identify an Assembly or App::Part node."
        )
    for node in nodes:
        for occurrence in list(node["occurrences"]):
            source_node_id = str(occurrence.get("source_node_id") or "")
            source = node_by_id.get(source_node_id)
            if source is None:
                raise ValueError(
                    f"{context} occurrence {occurrence['name']!r} has no source node."
                )
            if occurrence["link_mode"] == "assembly_link" and source["kind"] != "assembly":
                raise ValueError(
                    f"{context} occurrence {occurrence['name']!r} is an AssemblyLink "
                    "without an Assembly source."
                )

    paths: list[dict[str, Any]] = []
    active: set[str] = set()
    reachable: set[str] = set()

    def flatten(node_id: str, prefix: tuple[str, ...], depth: int) -> None:
        if depth > _MAX_HIERARCHY_DEPTH or node_id in active:
            raise ValueError(f"{context} contains a cyclic or over-deep hierarchy.")
        active.add(node_id)
        reachable.add(node_id)
        node = node_by_id[node_id]
        for occurrence in list(node["occurrences"]):
            source = node_by_id[str(occurrence["source_node_id"])]
            reachable.add(str(source["node_id"]))
            path = (*prefix, str(occurrence["name"]))
            paths.append(
                {
                    "path": "/".join(path),
                    "label": str(occurrence["label"]),
                    "type_id": str(occurrence["type_id"]),
                    "link_mode": str(occurrence["link_mode"]),
                    "rigid": bool(occurrence["rigid"]),
                    "source_node_id": str(source["node_id"]),
                    "source_kind": str(source["kind"]),
                    "source_label": str(source["label"]),
                    "source_type_id": str(source["type_id"]),
                    "bom_properties": list(source["bom_properties"]),
                    "depth": depth + 1,
                }
            )
            if len(paths) > _MAX_HIERARCHY_OCCURRENCES:
                raise ValueError(
                    f"{context} expands beyond {_MAX_HIERARCHY_OCCURRENCES} stable "
                    "occurrence paths."
                )
            if source["kind"] in {"assembly", "part"}:
                flatten(str(source["node_id"]), path, depth + 1)
        active.remove(node_id)

    flatten(root_node_id, (), 0)
    if reachable != set(node_by_id):
        raise ValueError(
            f"{context} contains disconnected nodes not reachable from root_node_id."
        )
    if value.get("occurrence_paths") != paths:
        raise ValueError(f"{context}.occurrence_paths disagrees with its node graph.")

    def paths_from(node_id: str) -> set[str]:
        result: set[str] = set()
        local_active: set[str] = set()

        def visit(current_id: str, prefix: tuple[str, ...], depth: int) -> None:
            if depth > _MAX_HIERARCHY_DEPTH or current_id in local_active:
                raise ValueError(f"{context} contains a cyclic or over-deep hierarchy.")
            local_active.add(current_id)
            current = node_by_id[current_id]
            for occurrence in list(current["occurrences"]):
                path = (*prefix, str(occurrence["name"]))
                result.add("/".join(path))
                source = node_by_id[str(occurrence["source_node_id"])]
                if source["kind"] in {"assembly", "part"}:
                    visit(str(source["node_id"]), path, depth + 1)
            local_active.remove(current_id)

        visit(node_id, (), 0)
        return result

    for node_index, node in enumerate(nodes):
        if str(node["kind"]) != "assembly":
            continue
        node_context = f"{context}.nodes[{node_index}]"
        available_paths = paths_from(str(node["node_id"]))
        grounded = list(node.get("grounded_occurrence_paths") or [])
        if len(grounded) != len(set(grounded)) or any(
            path not in available_paths for path in grounded
        ):
            raise ValueError(
                f"{node_context}.grounded_occurrence_paths contains a duplicate or "
                "unknown stable path."
            )
        clean_joints = [
            _validate_hierarchy_joint(
                raw_joint,
                context=f"{node_context}.joints[{joint_index}]",
                available_paths=available_paths,
            )
            for joint_index, raw_joint in enumerate(list(node.get("joints") or []))
        ]
        if len({joint["name"] for joint in clean_joints}) != len(clean_joints):
            raise ValueError(f"{node_context}.joints contains duplicate object names.")
        node["grounded_occurrence_paths"] = grounded
        node["joints"] = clean_joints
    counts = value.get("counts")
    limits = value.get("limits")
    expected_counts = {
        "nodes": len(nodes),
        "occurrences": occurrence_count,
        "joints": joint_count,
        "shape_artifacts": shape_count,
        "maximum_depth": max((int(item["depth"]) for item in paths), default=0),
    }
    if counts != expected_counts or limits != _EXPECTED_HIERARCHY_LIMITS:
        raise ValueError(f"{context}.counts or limits is inconsistent.")
    return {
        "descriptor": {
            **value,
            "nodes": nodes,
            "occurrence_paths": paths,
        },
        "nodes": MappingProxyType(node_by_id),
        "shapes": MappingProxyType(loaded_shapes),
    }


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 10:
        return "<maximum diagnostic depth reached>"
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item, depth=depth + 1) for item in value]
    quantity = getattr(value, "Value", None)
    if isinstance(quantity, (int, float)):
        return float(quantity)
    name = getattr(value, "Name", None)
    if isinstance(name, str):
        return name
    return str(value)


def configure_assembly_references(root: Path, entries: list[dict[str, Any]]) -> None:
    """Authenticate component BREPs and bind their bounded semantic metadata."""

    configure_part_references(root, entries)
    metadata: dict[tuple[str, str], Mapping[str, Any]] = {}
    hierarchies: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise ValueError(f"document_references[{index}] must be an object.")
        key = (
            str(raw.get("document_uid") or ""),
            str(raw.get("object_name") or ""),
        )
        shape = detached_reference_shape(
            {"document_uid": key[0], "object_name": key[1]}
        )
        facts = part_shape_facts(shape, max_subelements=32)
        if facts["null"] or not facts["valid"] or int(facts["solids"]) < 1:
            raise ValueError(
                f"Assembly component reference {key[1]!r} must contain at least "
                "one valid solid."
            )
        reported = raw.get("facts")
        if isinstance(reported, dict):
            for field in ("solids", "faces", "edges", "vertices"):
                if int(reported.get(field, -1)) != int(facts[field]):
                    raise ValueError(
                        f"Assembly component reference {key[1]!r} changed topology "
                        f"during transfer ({field})."
                    )
        interfaces = raw.get("published_interfaces", {})
        if not isinstance(interfaces, dict) or len(interfaces) > 64:
            raise ValueError(
                f"Assembly component reference {key[1]!r} has invalid semantic interfaces."
            )
        file_name = raw.get("document_file_name", "")
        properties_raw = raw.get("bom_properties", [])
        if (
            not isinstance(file_name, str)
            or len(file_name) > 4096
            or (file_name and Path(file_name).name != file_name)
            or not isinstance(properties_raw, list)
            or len(properties_raw) > 64
        ):
            raise ValueError(
                f"Assembly component reference {key[1]!r} has invalid BOM identity."
            )
        bom_properties = [
            _validate_hierarchy_property(
                value,
                context=(
                    f"Assembly component reference {key[1]!r} "
                    f"bom_properties[{property_index}]"
                ),
            )
            for property_index, value in enumerate(properties_raw)
        ]
        if len({str(item["name"]) for item in bom_properties}) != len(bom_properties):
            raise ValueError(
                f"Assembly component reference {key[1]!r} has duplicate BOM properties."
            )
        metadata[key] = MappingProxyType(
            {
                "label": str(raw.get("label") or ""),
                "type_id": str(raw.get("type_id") or ""),
                "source_kind": str(raw.get("source_kind") or "shape"),
                "transient_topology": bool(raw.get("transient_topology")),
                "requires_semantic_interfaces": bool(
                    raw.get("requires_semantic_interfaces")
                ),
                "published_interfaces": _json_safe(interfaces),
                "facts": facts,
                "document_file_name": file_name,
                "bom_properties": bom_properties,
            }
        )
        hierarchy = raw.get("assembly_hierarchy")
        if hierarchy is not None:
            source_kind = str(raw.get("source_kind") or "")
            if source_kind not in {"assembly", "part"}:
                raise ValueError(
                    f"Assembly component reference {key[1]!r} has hierarchy metadata "
                    "but is not an Assembly or App::Part source."
                )
            loaded_hierarchy = _load_assembly_hierarchy(
                root,
                hierarchy,
                context=f"document reference {key[1]!r} assembly_hierarchy",
            )
            descriptor = loaded_hierarchy["descriptor"]
            root_node = loaded_hierarchy["nodes"][str(descriptor["root_node_id"])]
            if str(root_node["kind"]) != source_kind:
                raise ValueError(
                    f"Assembly component reference {key[1]!r} hierarchy root kind "
                    "does not match its authenticated source kind."
                )
            hierarchies[key] = MappingProxyType(loaded_hierarchy)
        # A rigid component may intentionally provide only one authenticated
        # aggregate BREP. Operations that require internals (flexibility,
        # stable occurrence paths, or a detailed BOM) reject the missing
        # hierarchy at their own call site with a copy-ready model correction.
    global _REFERENCE_METADATA
    _REFERENCE_METADATA = MappingProxyType(metadata)
    global _REFERENCE_HIERARCHIES
    _REFERENCE_HIERARCHIES = MappingProxyType(hierarchies)


def _properties(value: DomainValue, operation: str) -> dict[str, Any]:
    if not isinstance(value, DomainValue) or value.domain != "assembly":
        raise AssemblyCandidateError(
            f"api.{operation}: expected a value from the active Assembly api."
        )
    return dict(value.properties)


def _reference(value: Any, *, context: str) -> dict[str, str]:
    try:
        result = normalize_document_reference(value)
    except DocumentReferenceError as exc:
        raise AssemblyCandidateError(
            f"{context} must contain document_uid and object_name, with an "
            f"optional portable document_path: {exc}"
        ) from exc
    reference_key = (result["document_uid"], result["object_name"])
    if reference_key not in _REFERENCE_METADATA:
        raise AssemblyCandidateError(
            f"{context} {result['object_name']!r} was not staged from this "
            "program's validated inputs.",
            details={
                "stage": "component_reference",
                "reference": result,
                "correction": (
                    "Put the exact stable reference in inputs and mark that input-schema "
                    "property with x-vibecad-reference=true."
                ),
            },
        )
    return result


def _catalog_fastener_identity(
    value: DomainValue,
    *,
    context: str,
) -> dict[str, Any]:
    if (
        not isinstance(value, DomainValue)
        or value.domain != "assembly"
        or value.operation != "fastener"
        or value.output_type != "component_link"
        or len(value.arguments) != 2
    ):
        raise AssemblyCandidateError(
            f"{context} must come from api.fastener.",
            details={"stage": "fastener_graph"},
        )
    properties = _properties(value, "fastener")
    try:
        from VibeCADFasteners import resolve_fastener

        return resolve_fastener(
            standard=value.arguments[0],
            nominal_thread=value.arguments[1],
            length_mm=properties.get("length_mm"),
            model_thread=properties["model_thread"],
            left_handed=bool(properties.get("left_handed")),
            options=dict(properties.get("options") or {}),
        )
    except Exception as exc:
        raise AssemblyCandidateError(
            f"{context} rejected the exact catalog request: {exc}",
            details={
                "stage": "fastener_catalog",
                "standard": str(value.arguments[0]),
                "nominal_thread": str(value.arguments[1]),
            },
        ) from exc


def _catalog_fastener_source(
    identity: Mapping[str, Any],
) -> dict[str, str]:
    from VibeCADFasteners import catalog_component_reference

    return catalog_component_reference(identity)


def _catalog_fastener_brep(
    shape: Any,
    *,
    artifact_root: Path,
    component_index: int,
) -> dict[str, Any]:
    relative = Path("outputs") / f"assembly-fastener-{component_index}.brep"
    target = artifact_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shape.exportBrep(str(target))
    size = target.stat().st_size
    if not 1 <= size <= 256 * 1024 * 1024:
        raise AssemblyCandidateError(
            "Generated fastener BREP exceeds the bounded Assembly artifact size.",
            details={
                "stage": "fastener_brep",
                "artifact_bytes": size,
            },
        )
    return {
        "artifact_kind": "brep",
        "artifact_path": str(relative),
        "artifact_sha256": _sha256_path(target),
        "artifact_bytes": size,
    }


def _native_placement(value: Any, *, context: str) -> Any:
    import FreeCAD as App

    if not isinstance(value, Mapping) or set(value) != {"position", "rotation"}:
        raise AssemblyCandidateError(
            f"{context} must contain exactly position and rotation."
        )
    position = value.get("position")
    rotation = value.get("rotation")
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        raise AssemblyCandidateError(f"{context}.position must be [x,y,z].")
    if not isinstance(rotation, (list, tuple)) or len(rotation) != 4:
        raise AssemblyCandidateError(
            f"{context}.rotation must be quaternion [x,y,z,w]."
        )
    numbers = [float(item) for item in (*position, *rotation)]
    if not all(math.isfinite(item) for item in numbers):
        raise AssemblyCandidateError(f"{context} must contain only finite values.")
    if math.sqrt(sum(item * item for item in numbers[3:])) <= 1.0e-12:
        raise AssemblyCandidateError(f"{context}.rotation quaternion must be non-zero.")
    return App.Placement(
        App.Vector(*numbers[:3]),
        App.Rotation(*numbers[3:]),
    )


def _native_placement_matrix(value: Any, *, context: str) -> Any:
    import FreeCAD as App

    values = _finite_matrix_payload(value, context=context)
    matrix = App.Matrix()
    for name, number in zip(
        (
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
        ),
        values,
        strict=True,
    ):
        setattr(matrix, name, number)
    return App.Placement(matrix)


def _apply_hierarchy_properties(obj: Any, node: Mapping[str, Any]) -> None:
    """Recreate only native BOM-supported scalar source properties."""

    identity = node.get("identity")
    node_name = (
        str(identity.get("object_name") or "")
        if isinstance(identity, Mapping)
        else str(getattr(obj, "Name", "") or "")
    )
    node_id = str(node.get("node_id") or node_name)
    existing = set(getattr(obj, "PropertiesList", []) or [])
    for item in list(node.get("bom_properties") or []):
        name = str(item["name"])
        if name not in existing:
            try:
                obj.addProperty(
                    str(item["property_type"]),
                    name,
                    "BOM",
                    "Authenticated source property for isolated BOM generation.",
                )
            except Exception as exc:
                raise AssemblyCandidateError(
                    f"Could not recreate BOM property {name!r} on source node "
                    f"{node_name!r}: {exc}",
                    details={
                        "stage": "assembly_hierarchy_property",
                        "source_node_id": node_id,
                        "property_name": name,
                        "property_type": str(item["property_type"]),
                        "correction": (
                            "Use a native BOM-supported String, Quantity, Enumeration, "
                            "Float, Integer, or Bool property."
                        ),
                    },
                ) from exc
            existing.add(name)
        kind = str(item["kind"])
        try:
            if kind == "enumeration":
                setattr(obj, name, list(item["choices"]))
                setattr(obj, name, str(item["value"]))
            elif kind == "quantity":
                setattr(obj, name, str(item["assignment"]))
            else:
                setattr(obj, name, item["value"])
        except Exception as exc:
            raise AssemblyCandidateError(
                f"Could not apply BOM property {name!r} on source node "
                f"{node_name!r}: {exc}",
                details={
                    "stage": "assembly_hierarchy_property",
                    "source_node_id": node_id,
                    "property_name": name,
                },
            ) from exc


def _safe_native_name(prefix: str, stable_name: str, suffix: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", stable_name).strip("_") or prefix
    if not clean[0].isalpha() and clean[0] != "_":
        clean = f"{prefix}_{clean}"
    return f"{prefix}_{clean}_{suffix}"


def _hierarchy_path_chain(
    reconstruction: Mapping[str, Any],
    *,
    node_id: str,
    stable_path: str,
    context: str,
) -> list[tuple[str, dict[str, Any], Any, dict[str, Any]]]:
    """Resolve and explain one stable path without trusting generated names."""

    nodes = reconstruction["nodes"]
    occurrences = reconstruction["occurrences"]
    segments = stable_path.split("/") if stable_path else []
    if not segments:
        raise AssemblyCandidateError(f"{context} has an empty occurrence path.")
    current_node_id = node_id
    chain: list[tuple[str, dict[str, Any], Any, dict[str, Any]]] = []
    for segment_index, segment in enumerate(segments):
        node = nodes.get(current_node_id)
        if node is None:
            raise AssemblyCandidateError(
                f"{context} traversed missing source node {current_node_id!r}."
            )
        occurrence = next(
            (
                item
                for item in list(node.get("occurrences") or [])
                if str(item.get("name") or "") == segment
            ),
            None,
        )
        if occurrence is None:
            available = [str(item["name"]) for item in list(node.get("occurrences") or [])]
            raise AssemblyCandidateError(
                f"{context} path segment {segment!r} does not exist after "
                f"{'/'.join(segments[:segment_index]) or '<root>'}; available segments "
                f"are {available or ['<none>']}.",
                details={
                    "stage": "assembly_occurrence_path",
                    "requested_path": stable_path,
                    "failed_segment_index": segment_index,
                    "available_segments": available,
                    "correction": "Copy one exact occurrence path from Assembly domain context.",
                },
            )
        source_occurrence = occurrences.get(
            (current_node_id, str(occurrence["occurrence_id"]))
        )
        source_node = nodes.get(str(occurrence["source_node_id"]))
        if source_occurrence is None or source_node is None:
            raise AssemblyCandidateError(
                f"{context} path {stable_path!r} has no reconstructed native occurrence."
            )
        chain.append((current_node_id, occurrence, source_occurrence, source_node))
        current_node_id = str(occurrence["source_node_id"])
    return chain


def _mirrored_hierarchy_occurrence(
    container: Any,
    source_occurrence: Any,
    *,
    stable_path: str,
    context: str,
) -> Any:
    matches = [
        child
        for child in list(getattr(container, "Group", []) or [])
        if getattr(child, "LinkedObject", None) is source_occurrence
    ]
    if len(matches) != 1:
        available = [
            {
                "name": str(getattr(child, "Name", "") or ""),
                "linked_object": str(
                    getattr(getattr(child, "LinkedObject", None), "Name", "") or ""
                ),
                "type_id": str(getattr(child, "TypeId", "") or ""),
            }
            for child in list(getattr(container, "Group", []) or [])
            if hasattr(child, "LinkedObject")
        ]
        raise AssemblyCandidateError(
            f"{context} could not map stable path {stable_path!r} into the native "
            f"AssemblyLink; expected one synchronized occurrence and found {len(matches)}.",
            details={
                "stage": "assembly_hierarchy_synchronization",
                "occurrence_path": stable_path,
                "source_occurrence": str(
                    getattr(source_occurrence, "Name", "") or ""
                ),
                "available_native_links": available,
                "correction": (
                    "Recompute the source Assembly, then retry with the same stable "
                    "occurrence_path. Generated AssemblyLink child names are never inputs."
                ),
            },
        )
    return matches[0]


def _resolve_hierarchy_reference(
    reconstruction: Mapping[str, Any],
    *,
    root: Any,
    node_id: str,
    stable_path: str,
    subelements: list[str],
    context: str,
) -> dict[str, Any]:
    """Map one stable path onto exact native rigid/flexible reference syntax."""

    chain = _hierarchy_path_chain(
        reconstruction,
        node_id=node_id,
        stable_path=stable_path,
        context=context,
    )
    container = root
    target = root if str(getattr(root, "TypeId", "") or "") != (
        "Assembly::AssemblyObject"
    ) else None
    prefix_names: list[str] = []
    locked_behind_rigid_boundary = False
    native_chain: list[dict[str, Any]] = []
    for chain_index, (
        _parent_node_id,
        occurrence,
        source_occurrence,
        source_node,
    ) in enumerate(chain):
        container_type = str(getattr(container, "TypeId", "") or "")
        if container_type == "Assembly::AssemblyObject":
            actual = source_occurrence
            actual_is_live = True
        elif container_type == "Assembly::AssemblyLink":
            actual = _mirrored_hierarchy_occurrence(
                container,
                source_occurrence,
                stable_path="/".join(stable_path.split("/")[: chain_index + 1]),
                context=context,
            )
            actual_is_live = True
        elif container_type == "App::Part" and source_occurrence in list(
            getattr(container, "Group", []) or []
        ):
            actual = source_occurrence
            actual_is_live = True
        else:
            # App::Link-to-Part does not mirror each inner object.  Native
            # XLinkSub addresses those objects by source object-name prefixes.
            actual = source_occurrence
            actual_is_live = False

        if locked_behind_rigid_boundary:
            prefix_names.append(str(getattr(actual, "Name", "") or ""))
        elif container_type == "Assembly::AssemblyObject":
            target = actual
            prefix_names = []
        elif container_type == "Assembly::AssemblyLink":
            if bool(getattr(container, "Rigid", True)):
                locked_behind_rigid_boundary = True
                prefix_names.append(str(getattr(actual, "Name", "") or ""))
            else:
                target = actual
                prefix_names = []
        else:
            locked_behind_rigid_boundary = True
            prefix_names.append(str(getattr(actual, "Name", "") or ""))

        native_chain.append(
            {
                "stable_name": str(occurrence["name"]),
                "source_node_id": str(source_node["node_id"]),
                "source_kind": str(source_node["kind"]),
                "native_name": str(getattr(actual, "Name", "") or ""),
                "native_type_id": str(getattr(actual, "TypeId", "") or ""),
                "container_type_id": container_type,
                "container_rigid": (
                    bool(getattr(container, "Rigid", True))
                    if container_type == "Assembly::AssemblyLink"
                    else None
                ),
                "live_occurrence": actual_is_live,
            }
        )
        container = actual

    if target is None:
        raise AssemblyCandidateError(
            f"{context} stable path {stable_path!r} did not resolve to a native target."
        )
    prefix = ".".join(name for name in prefix_names if name)
    native_subelements = [
        (
            f"{prefix}.{str(subelement)}"
            if prefix and subelement
            else f"{prefix}."
            if prefix
            else str(subelement or "")
        )
        for subelement in subelements
    ]
    leaf_node = chain[-1][3]
    return {
        "target": target,
        "subelements": native_subelements,
        "occurrence_path": stable_path,
        "leaf_node_id": str(leaf_node["node_id"]),
        "leaf_node": leaf_node,
        "leaf_native": reconstruction["native_nodes"][str(leaf_node["node_id"])],
        "leaf_occurrence": actual,
        "leaf_is_live_occurrence": bool(native_chain[-1]["live_occurrence"]),
        "native_prefix": prefix,
        "native_chain": native_chain,
        "target_mode": (
            "prefixed_rigid_boundary" if prefix else "direct_exposed_occurrence"
        ),
    }


def _reconstruct_assembly_hierarchy(
    document: Any,
    source_key: tuple[str, str],
    *,
    source_index: int,
) -> dict[str, Any]:
    """Rebuild one authenticated native source graph in the isolated document."""

    import JointObject

    hierarchy = _REFERENCE_HIERARCHIES.get(source_key)
    if hierarchy is None:
        raise AssemblyCandidateError(
            f"Container source {source_key[1]!r} has no authenticated hierarchy.",
            details={
                "stage": "assembly_hierarchy",
                "source": {
                    "document_uid": source_key[0],
                    "object_name": source_key[1],
                },
                "correction": (
                    "Regenerate after the live source AssemblyObject or App::Part is "
                    "available."
                ),
            },
        )
    descriptor = dict(hierarchy["descriptor"])
    nodes: Mapping[str, Mapping[str, Any]] = hierarchy["nodes"]
    shapes: Mapping[str, Any] = hierarchy["shapes"]
    native_nodes: dict[str, Any] = {}
    joint_groups: dict[str, Any] = {}
    for node_order, node in enumerate(list(descriptor["nodes"])):
        node_id = str(node["node_id"])
        kind = str(node["kind"])
        internal_name = _safe_native_name(
            "Hierarchy",
            str(node["identity"]["object_name"]),
            f"{source_index}_{node_order}",
        )
        if kind == "assembly":
            native = document.addObject("Assembly::AssemblyObject", internal_name)
            native.Type = "Assembly"
            joint_groups[node_id] = native.newObject(
                "Assembly::JointGroup", f"HierarchyJoints_{source_index}_{node_order}"
            )
        elif kind == "part":
            native = document.addObject("App::Part", internal_name)
        else:
            native = document.addObject("Part::Feature", internal_name)
        if native is None:
            raise AssemblyCandidateError(
                f"FreeCAD could not reconstruct Assembly hierarchy node "
                f"{node['identity']['object_name']!r}.",
                details={"stage": "assembly_hierarchy_reconstruction", "node_id": node_id},
            )
        native.Label = str(node.get("label") or node["identity"]["object_name"])
        _apply_hierarchy_properties(native, node)
        shape = shapes.get(node_id)
        if kind == "shape":
            if shape is None:
                raise AssemblyCandidateError(
                    f"Assembly hierarchy leaf {node['identity']['object_name']!r} has no BREP.",
                    details={"stage": "assembly_hierarchy_reconstruction", "node_id": node_id},
                )
            native.Shape = shape.copy()
        native_nodes[node_id] = native

    occurrences: dict[tuple[str, str], Any] = {}
    for node in list(descriptor["nodes"]):
        node_id = str(node["node_id"])
        if str(node["kind"]) not in {"assembly", "part"}:
            continue
        container = native_nodes[node_id]
        for occurrence_index, occurrence in enumerate(list(node["occurrences"])):
            source_node_id = str(occurrence["source_node_id"])
            source = native_nodes[source_node_id]
            occurrence_name = _safe_native_name(
                "Occurrence",
                str(occurrence["name"]),
                f"{source_index}_{node_id}_{occurrence_index}",
            )
            native_type = (
                "Assembly::AssemblyLink"
                if str(occurrence["link_mode"]) == "assembly_link"
                else "App::Link"
            )
            native = container.newObject(native_type, occurrence_name)
            if native is None:
                raise AssemblyCandidateError(
                    f"FreeCAD could not reconstruct Assembly occurrence "
                    f"{occurrence['name']!r}.",
                    details={
                        "stage": "assembly_hierarchy_reconstruction",
                        "node_id": node_id,
                        "occurrence_id": str(occurrence["occurrence_id"]),
                    },
                )
            if native_type == "Assembly::AssemblyLink":
                # Setting the source first and then changing Rigid mirrors the
                # exact native transform propagation used by the workbench.
                native.LinkedObject = source
                native.Placement = _native_placement_matrix(
                    occurrence["placement_matrix"],
                    context=f"hierarchy occurrence {occurrence['name']!r} placement",
                )
                native.Rigid = bool(occurrence["rigid"])
            else:
                native.LinkedObject = source
                native.Placement = _native_placement_matrix(
                    occurrence["placement_matrix"],
                    context=f"hierarchy occurrence {occurrence['name']!r} placement",
                )
                if hasattr(native, "Scale"):
                    native.Scale = float(occurrence["scale"])
            native.Label = str(occurrence.get("label") or occurrence["name"])
            occurrences[(node_id, str(occurrence["occurrence_id"]))] = native

    document.recompute()
    reconstruction = {
        "descriptor": descriptor,
        "nodes": nodes,
        "native_nodes": native_nodes,
        "occurrences": occurrences,
        "root": native_nodes[str(descriptor["root_node_id"])],
        "hierarchy_sha256": hashlib.sha256(
            json.dumps(
                descriptor,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
    }

    # Recreate source grounding and joints from authenticated precomputed
    # connector frames. Geometry derivation and solving remain in this worker.
    for node in list(descriptor["nodes"]):
        if str(node["kind"]) != "assembly":
            continue
        node_id = str(node["node_id"])
        joint_group = joint_groups[node_id]
        for ground_index, stable_path in enumerate(
            list(node.get("grounded_occurrence_paths") or [])
        ):
            resolved_ground = _resolve_hierarchy_reference(
                reconstruction,
                root=reconstruction["native_nodes"][node_id],
                node_id=node_id,
                stable_path=str(stable_path),
                subelements=["", ""],
                context=f"source ground {ground_index}",
            )
            ground = joint_group.newObject(
                "App::FeaturePython", f"HierarchyGround_{source_index}_{node_id}_{ground_index}"
            )
            JointObject.GroundedJoint(ground, resolved_ground["target"])
        for joint_index, record in enumerate(list(node.get("joints") or [])):
            native_type = str(record["native_type"])
            joint = joint_group.newObject(
                "App::FeaturePython", f"HierarchyJoint_{source_index}_{node_id}_{joint_index}"
            )
            JointObject.Joint(joint, list(JointObject.JointTypes).index(native_type))
            joint.Label = str(record.get("label") or record["name"])
            joint.Distance = float(record["distance"])
            joint.Distance2 = float(record["distance2"])
            joint.Angle = float(record["angle"])
            length_limits = list(record["length_limits"])
            angle_limits = list(record["angle_limits"])
            joint.EnableLengthMin = length_limits[0] is not None
            joint.EnableLengthMax = length_limits[1] is not None
            joint.EnableAngleMin = angle_limits[0] is not None
            joint.EnableAngleMax = angle_limits[1] is not None
            if length_limits[0] is not None:
                joint.LengthMin = float(length_limits[0])
            if length_limits[1] is not None:
                joint.LengthMax = float(length_limits[1])
            if angle_limits[0] is not None:
                joint.AngleMin = float(angle_limits[0])
            if angle_limits[1] is not None:
                joint.AngleMax = float(angle_limits[1])
            joint.Offset1 = _native_placement_matrix(
                record["offset1_matrix"], context=f"source joint {record['name']} Offset1"
            )
            joint.Offset2 = _native_placement_matrix(
                record["offset2_matrix"], context=f"source joint {record['name']} Offset2"
            )
            joint.Placement1 = _native_placement_matrix(
                record["placement1_matrix"],
                context=f"source joint {record['name']} Placement1",
            )
            joint.Placement2 = _native_placement_matrix(
                record["placement2_matrix"],
                context=f"source joint {record['name']} Placement2",
            )
            for reference_index in (1, 2):
                reference = dict(record[f"reference{reference_index}"])
                resolved_reference = _resolve_hierarchy_reference(
                    reconstruction,
                    root=reconstruction["native_nodes"][node_id],
                    node_id=node_id,
                    stable_path=str(reference["occurrence_path"]),
                    subelements=list(reference["subelements"]),
                    context=f"source joint {record['name']!r} Reference{reference_index}",
                )
                setattr(
                    joint,
                    f"Reference{reference_index}",
                    [
                        resolved_reference["target"],
                        resolved_reference["subelements"],
                    ],
                )
            if hasattr(joint, "Suppressed"):
                joint.Suppressed = bool(record["suppressed"])
    document.recompute()
    return reconstruction


def _placement_matrix(placement: Any) -> list[float]:
    matrix = placement.toMatrix()
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


def _placement_fact(placement: Any) -> dict[str, Any]:
    return {
        "position_mm": [
            float(placement.Base.x),
            float(placement.Base.y),
            float(placement.Base.z),
        ],
        "rotation_axis": [
            float(placement.Rotation.Axis.x),
            float(placement.Rotation.Axis.y),
            float(placement.Rotation.Axis.z),
        ],
        "rotation_angle_degrees": math.degrees(float(placement.Rotation.Angle)),
        "matrix": _placement_matrix(placement),
    }


def _global_placement_fact(
    obj: Any,
    *,
    context: str,
    hierarchy_root: Any | None = None,
    native_chain: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read an authenticated native global placement without a local fallback."""

    native_object = str(getattr(obj, "Name", "") or "")
    details = {
        "stage": "assembly_occurrence_placement",
        "native_object": native_object,
        "correction": (
            "Recompute and solve the source Assembly, then retry with the same "
            "stable occurrence_path."
        ),
    }
    if hierarchy_root is not None or native_chain is not None:
        if hierarchy_root is None or not native_chain:
            raise AssemblyCandidateError(
                f"{context} has an incomplete native hierarchy placement path.",
                details=details,
            )
        native_names = [str(item.get("native_name") or "") for item in native_chain]
        if any(not name or "." in name or len(name) > 128 for name in native_names):
            raise AssemblyCandidateError(
                f"{context} has a malformed native hierarchy placement path.",
                details={**details, "native_chain": native_names},
            )
        native_subname = ".".join(native_names) + "."
        root_name = str(getattr(hierarchy_root, "Name", "") or "")
        try:
            resolved = hierarchy_root.getSubObject(native_subname, 1)
        except Exception as exc:
            raise AssemblyCandidateError(
                f"{context} native hierarchy path could not be resolved: {exc}",
                details={
                    **details,
                    "native_root": root_name,
                    "native_subname": native_subname,
                },
            ) from exc
        expected_document = getattr(obj, "Document", None)
        resolved_document = getattr(resolved, "Document", None)
        expected_identity = (
            str(getattr(expected_document, "Uid", "") or ""),
            native_object,
        )
        resolved_identity = (
            str(getattr(resolved_document, "Uid", "") or ""),
            str(getattr(resolved, "Name", "") or ""),
        )
        if resolved is None or not all(expected_identity) or (
            resolved_identity != expected_identity
        ):
            raise AssemblyCandidateError(
                f"{context} native hierarchy path resolved to a different object.",
                details={
                    **details,
                    "native_root": root_name,
                    "native_subname": native_subname,
                    "expected_identity": expected_identity,
                    "resolved_identity": resolved_identity,
                },
            )
        try:
            placement = hierarchy_root.getPlacementOf(native_subname, obj)
        except Exception as exc:
            raise AssemblyCandidateError(
                f"{context} authenticated native global placement could not be read: "
                f"{exc}",
                details={
                    **details,
                    "native_root": root_name,
                    "native_subname": native_subname,
                },
            ) from exc
        return _placement_fact(placement)

    getter = getattr(obj, "getGlobalPlacement", None)
    if not callable(getter):
        raise AssemblyCandidateError(
            f"{context} does not expose a native global placement.", details=details
        )
    try:
        return _placement_fact(getter())
    except Exception as exc:
        raise AssemblyCandidateError(
            f"{context} native global placement could not be read: {exc}",
            details=details,
        ) from exc


def _component_hierarchy_state(
    component: Any,
    reconstruction: Mapping[str, Any],
    *,
    component_output: str,
) -> list[dict[str, Any]]:
    """Return stable-path placement evidence after the native parent solve."""

    result = []
    root_node_id = str(reconstruction["descriptor"]["root_node_id"])
    for item in list(reconstruction["descriptor"]["occurrence_paths"]):
        stable_path = str(item["path"])
        resolved = _resolve_hierarchy_reference(
            reconstruction,
            root=component,
            node_id=root_node_id,
            stable_path=stable_path,
            subelements=["", ""],
            context=(
                f"component output {component_output!r} occurrence-state inspection"
            ),
        )
        occurrence = resolved["leaf_occurrence"]
        live = bool(resolved["leaf_is_live_occurrence"])
        result.append(
            {
                "occurrence_path": stable_path,
                "source_node_id": str(resolved["leaf_node_id"]),
                "source_kind": str(resolved["leaf_node"]["kind"]),
                "source_label": str(resolved["leaf_node"]["label"]),
                "native_name": str(getattr(occurrence, "Name", "") or ""),
                "native_type_id": str(getattr(occurrence, "TypeId", "") or ""),
                "native_target_mode": str(resolved["target_mode"]),
                "live_occurrence": live,
                "local_placement": (
                    _placement_fact(occurrence.Placement) if live else None
                ),
                "global_placement": (
                    _global_placement_fact(
                        occurrence,
                        context=(
                            f"component output {component_output!r} occurrence "
                            f"{stable_path!r}"
                        ),
                        hierarchy_root=component,
                        native_chain=list(resolved["native_chain"]),
                    )
                    if live
                    else None
                ),
            }
        )
    return result


def _native_reference(reference: Any) -> dict[str, Any]:
    if not isinstance(reference, tuple) or len(reference) < 2:
        return {"component": None, "subelements": []}
    raw = reference[1]
    subelements = [raw] if isinstance(raw, str) else list(raw or [])
    return {
        "component": str(getattr(reference[0], "Name", "") or ""),
        "subelements": [str(item) for item in subelements],
    }


def _graph_contract(
    raw_result: Mapping[str, Any],
) -> tuple[
    str,
    DomainValue,
    str,
    DomainValue,
    dict[int, str],
    dict[int, str],
]:
    if len({id(value) for value in raw_result.values()}) != len(raw_result):
        raise AssemblyCandidateError(
            "Each Assembly graph value must be returned exactly once under one output name."
        )
    assemblies = [
        (name, value)
        for name, value in raw_result.items()
        if isinstance(value, DomainValue) and value.output_type == "assembly"
    ]
    diagnostics = [
        (name, value)
        for name, value in raw_result.items()
        if isinstance(value, DomainValue) and value.output_type == "solver_diagnostics"
    ]
    if len(assemblies) != 1 or len(diagnostics) != 1:
        raise AssemblyCandidateError(
            "An Assembly program must return exactly one assembly and one "
            "solver_diagnostics output.",
            details={
                "assembly_outputs": [name for name, _value in assemblies],
                "diagnostic_outputs": [name for name, _value in diagnostics],
            },
        )
    assembly_name, assembly_value = assemblies[0]
    diagnostics_name, diagnostics_value = diagnostics[0]
    if assembly_value.operation != "assembly" or diagnostics_value.operation != "solve":
        raise AssemblyCandidateError(
            "Assembly and diagnostics outputs must come from api.assembly and api.solve."
        )
    assembly_properties = _properties(assembly_value, "assembly")
    components = list(assembly_properties.get("components") or [])
    joints = list(assembly_properties.get("joints") or [])
    if not components:
        raise AssemblyCandidateError("api.assembly must contain at least one component.")
    component_outputs = {
        id(value): name
        for name, value in raw_result.items()
        if isinstance(value, DomainValue) and value.output_type == "component_link"
    }
    joint_outputs = {
        id(value): name
        for name, value in raw_result.items()
        if isinstance(value, DomainValue) and value.output_type == "joint"
    }
    if {id(value) for value in components} != set(component_outputs):
        raise AssemblyCandidateError(
            "Every component listed in api.assembly must be returned exactly once, "
            "and no unlisted component_link output is allowed.",
            details={
                "returned_components": list(component_outputs.values()),
                "assembly_component_count": len(components),
            },
        )
    if {id(value) for value in joints} != set(joint_outputs):
        raise AssemblyCandidateError(
            "Every joint listed in api.assembly must be returned exactly once, "
            "and no unlisted joint output is allowed.",
            details={
                "returned_joints": list(joint_outputs.values()),
                "assembly_joint_count": len(joints),
            },
        )
    if not diagnostics_value.arguments or diagnostics_value.arguments[0] is not assembly_value:
        raise AssemblyCandidateError(
            "api.solve must receive the exact api.assembly variable returned in result."
        )
    return (
        assembly_name,
        assembly_value,
        diagnostics_name,
        diagnostics_value,
        component_outputs,
        joint_outputs,
    )


def _simulation_contract(
    raw_result: Mapping[str, Any],
    *,
    assembly_value: DomainValue,
    joint_outputs: Mapping[int, str],
) -> tuple[str, DomainValue, dict[int, str]] | None:
    simulations = [
        (name, value)
        for name, value in raw_result.items()
        if isinstance(value, DomainValue) and value.output_type == "simulation"
    ]
    motion_outputs = {
        id(value): name
        for name, value in raw_result.items()
        if isinstance(value, DomainValue) and value.output_type == "motion"
    }
    if not simulations and not motion_outputs:
        return None
    if len(simulations) != 1:
        raise AssemblyCandidateError(
            "Assembly motion outputs require exactly one api.simulation output.",
            details={
                "stage": "simulation_graph",
                "simulation_outputs": [name for name, _value in simulations],
                "motion_outputs": list(motion_outputs.values()),
            },
        )
    simulation_output, simulation_value = simulations[0]
    if simulation_value.operation != "simulation" or len(simulation_value.arguments) != 1:
        raise AssemblyCandidateError(
            f"Simulation output {simulation_output!r} must come from api.simulation."
        )
    if simulation_value.arguments[0] is not assembly_value:
        raise AssemblyCandidateError(
            f"Simulation output {simulation_output!r} must consume the exact returned "
            "api.assembly value."
        )
    properties = _properties(simulation_value, "simulation")
    motions = list(properties.get("motions") or [])
    if not motions:
        raise AssemblyCandidateError(
            f"Simulation output {simulation_output!r} must contain at least one "
            "api.motion value."
        )
    if {id(value) for value in motions} != set(motion_outputs):
        raise AssemblyCandidateError(
            "Every api.motion value used by api.simulation must be returned exactly "
            "once, and no unlisted motion output is allowed.",
            details={
                "stage": "simulation_graph",
                "returned_motions": list(motion_outputs.values()),
                "simulation_motion_count": len(motions),
            },
        )
    drives: set[tuple[int, str]] = set()
    for index, motion in enumerate(motions):
        if (
            not isinstance(motion, DomainValue)
            or motion.domain != "assembly"
            or motion.operation != "motion"
            or motion.output_type != "motion"
            or len(motion.arguments) != 1
        ):
            raise AssemblyCandidateError(
                f"Simulation motion {index} must come from api.motion."
            )
        joint = motion.arguments[0]
        joint_output = joint_outputs.get(id(joint))
        if joint_output is None:
            raise AssemblyCandidateError(
                f"Motion output {motion_outputs[id(motion)]!r} drives a joint not "
                "returned by this assembly graph."
            )
        motion_properties = _properties(motion, "motion")
        drive = (id(joint), str(motion_properties.get("motion_type") or ""))
        if drive in drives:
            raise AssemblyCandidateError(
                f"Joint output {joint_output!r} has duplicate {drive[1]!r} motions."
            )
        drives.add(drive)
    return simulation_output, simulation_value, motion_outputs


def _exploded_view_contract(
    raw_result: Mapping[str, Any],
    *,
    assembly_value: DomainValue,
    component_outputs: Mapping[int, str],
) -> list[tuple[str, DomainValue]]:
    views = [
        (name, value)
        for name, value in raw_result.items()
        if isinstance(value, DomainValue) and value.output_type == "exploded_view"
    ]
    for output_name, value in views:
        if (
            value.operation != "exploded_view"
            or len(value.arguments) != 1
            or value.arguments[0] is not assembly_value
        ):
            raise AssemblyCandidateError(
                f"Exploded-view output {output_name!r} must come from "
                "api.exploded_view using the exact returned assembly variable.",
                details={
                    "stage": "exploded_view_graph",
                    "exploded_view_output": output_name,
                },
            )
        properties = _properties(value, "exploded_view")
        moves = list(properties.get("moves") or [])
        if not 1 <= len(moves) <= 64:
            raise AssemblyCandidateError(
                f"Exploded-view output {output_name!r} must contain 1-64 moves.",
                details={"stage": "exploded_view_graph"},
            )
        reference_count = 0
        for move_index, move in enumerate(moves):
            if not isinstance(move, Mapping):
                raise AssemblyCandidateError(
                    f"Exploded-view output {output_name!r} move {move_index} is malformed."
                )
            components = list(move.get("components") or [])
            reference_count += len(components)
            for component_index, component in enumerate(components):
                if id(component) not in component_outputs:
                    raise AssemblyCandidateError(
                        f"Exploded-view output {output_name!r} move {move_index} "
                        f"component {component_index} is not returned by this assembly.",
                        details={
                            "stage": "exploded_view_graph",
                            "exploded_view_output": output_name,
                            "move_index": move_index,
                            "component_index": component_index,
                        },
                    )
        if reference_count > 256:
            raise AssemblyCandidateError(
                f"Exploded-view output {output_name!r} exceeds 256 component references."
            )
    return views


def _bom_contract(
    raw_result: Mapping[str, Any],
    *,
    assembly_value: DomainValue,
) -> list[tuple[str, DomainValue]]:
    boms = [
        (name, value)
        for name, value in raw_result.items()
        if isinstance(value, DomainValue) and value.output_type == "bom"
    ]
    if len(boms) > 8:
        raise AssemblyCandidateError(
            "An Assembly program may publish at most eight named BOM views.",
            details={
                "stage": "bom_graph",
                "bom_outputs": [name for name, _value in boms],
                "maximum_bom_outputs": 8,
                "correction": (
                    "Keep only the BOM views needed by downstream consumers; vary "
                    "columns and detail settings in those named outputs."
                ),
            },
        )
    for output_name, value in boms:
        if (
            value.operation != "bill_of_materials"
            or len(value.arguments) != 1
            or value.arguments[0] is not assembly_value
        ):
            raise AssemblyCandidateError(
                f"BOM output {output_name!r} must come from api.bill_of_materials "
                "using the exact returned assembly variable.",
                details={
                    "stage": "bom_graph",
                    "bom_output": output_name,
                    "correction": (
                        f"Use {output_name.lower()} = api.bill_of_materials(model, ...) "
                        f"and return {output_name!r}: {output_name.lower()}."
                    ),
                },
            )
    return boms


def _mechanism_check_contract(
    raw_result: Mapping[str, Any],
    *,
    assembly_value: DomainValue,
    component_outputs: Mapping[int, str],
    mechanism_scenario: Mapping[str, Any],
) -> list[tuple[str, DomainValue, dict[str, Any]]]:
    """Bind static verification declarations to exact returned graph values."""

    verifications = [
        (name, value)
        for name, value in raw_result.items()
        if isinstance(value, DomainValue)
        and value.output_type == "mechanism_verification"
    ]
    if len(verifications) > 8:
        raise AssemblyCandidateError(
            "An Assembly program may publish at most eight mechanism verifications.",
            details={
                "stage": "mechanism_verification_graph",
                "verification_outputs": [name for name, _value in verifications],
            },
        )
    result: list[tuple[str, DomainValue, dict[str, Any]]] = []
    for output_name, value in verifications:
        if (
            value.operation != "mechanism_check"
            or len(value.arguments) != 1
            or value.arguments[0] is not assembly_value
        ):
            raise AssemblyCandidateError(
                f"Mechanism verification output {output_name!r} must come from "
                "api.mechanism_check using the exact returned assembly variable.",
                details={
                    "stage": "mechanism_verification_graph",
                    "verification_output": output_name,
                },
            )
        properties = _properties(value, "mechanism_check")
        requirements = []
        for index, item in enumerate(list(properties.get("requirements") or [])):
            if not isinstance(item, Mapping):
                raise AssemblyCandidateError(
                    f"Mechanism verification output {output_name!r} requirement "
                    f"{index} is malformed."
                )
            first = component_outputs.get(id(item.get("first")))
            second = component_outputs.get(id(item.get("second")))
            if first is None or second is None:
                raise AssemblyCandidateError(
                    f"Mechanism verification output {output_name!r} requirement "
                    f"{index} references an unreturned component.",
                    details={"stage": "mechanism_verification_graph"},
                )
            normalized = {
                "id": f"Requirement{index + 1:03d}",
                "type": str(item.get("type") or ""),
                "first_component": first,
                "second_component": second,
                "tolerance_mm": item.get("tolerance_mm"),
            }
            if normalized["type"] == "minimum_clearance":
                normalized["minimum_mm"] = item.get("minimum_mm")
            requirements.append(normalized)
        contacts = []
        for index, item in enumerate(list(properties.get("contacts") or [])):
            if not isinstance(item, Mapping):
                raise AssemblyCandidateError(
                    f"Mechanism verification output {output_name!r} contact "
                    f"{index} is malformed."
                )
            first = component_outputs.get(id(item.get("first")))
            second = component_outputs.get(id(item.get("second")))
            if first is None or second is None:
                raise AssemblyCandidateError(
                    f"Mechanism verification output {output_name!r} contact "
                    f"{index} references an unreturned component.",
                    details={"stage": "mechanism_verification_graph"},
                )
            policy = str(item.get("policy") or "")
            normalized = {
                "id": f"Contact{index + 1:03d}",
                "policy": policy,
                "first_component": first,
                "second_component": second,
            }
            if policy == "ignored":
                normalized["reason"] = item.get("reason")
            else:
                normalized["tolerance_mm"] = item.get("tolerance_mm")
            if policy == "clearance":
                normalized["minimum_clearance_mm"] = item.get(
                    "minimum_clearance_mm"
                )
            elif policy in {"allowed", "required"}:
                normalized["first_interface"] = item.get("first_interface")
                normalized["second_interface"] = item.get("second_interface")
            contacts.append(normalized)
        contract = normalize_mechanism_static_check(
            mechanism_scenario,
            {
                "schema": MECHANISM_STATIC_CHECK_SCHEMA,
                "id": output_name,
                "label": str(properties.get("label") or ""),
                "scenario_sha256": mechanism_scenario_sha256(
                    mechanism_scenario
                ),
                "requirements": requirements,
                "contacts": contacts,
            },
        )
        result.append((output_name, value, contract))
    return result


def _static_mechanism_geometry(
    check: Mapping[str, Any],
    *,
    components: Mapping[str, Any],
    component_values: Mapping[str, DomainValue],
    component_source_metadata: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate one explicit rigid-component static geometry request."""

    declarations = [
        dict(item) for item in list(check.get("requirements") or [])
    ] + [
        dict(item)
        for item in list(check.get("contacts") or [])
        if str(item.get("policy") or "") != "ignored"
    ]
    requested_components = {
        str(item[field])
        for item in declarations
        for field in ("first_component", "second_component")
    }
    for component_name in sorted(requested_components):
        component_value = component_values.get(component_name)
        if component_value is None:
            raise AssemblyCandidateError(
                f"Mechanism verification {check['id']!r} references unknown "
                f"component {component_name!r}.",
                details={"stage": "mechanism_verification_geometry"},
            )
        properties = _properties(component_value, component_value.operation)
        if bool(properties.get("flexible", False)):
            raise AssemblyCandidateError(
                f"Mechanism verification {check['id']!r} cannot certify flexible "
                f"component {component_name!r} as one rigid static shape.",
                details={
                    "stage": "mechanism_verification_geometry",
                    "verification_output": str(check["id"]),
                    "component_output": component_name,
                    "correction": (
                        "Use a rigid occurrence for this static check. Flexible "
                        "occurrence-path certification is not part of the static "
                        "solved-state contract."
                    ),
                },
            )

    geometry_components: dict[str, dict[str, Any]] = {}
    geometry_interfaces: dict[str, dict[str, list[str]]] = {}
    for component_name, component in components.items():
        source = getattr(component, "LinkedObject", None)
        shape = getattr(source, "Shape", None)
        if (
            shape is None
            or bool(shape.isNull())
            or not bool(shape.isValid())
            or len(list(shape.Solids)) < 1
        ):
            raise AssemblyCandidateError(
                f"Component output {component_name!r} has no valid source BREP for "
                "static mechanism verification.",
                details={
                    "stage": "mechanism_verification_geometry",
                    "component_output": component_name,
                },
            )
        placement = component.Placement
        geometry_components[component_name] = {
            "shape": shape,
            "placement": {
                "position": [
                    float(placement.Base.x),
                    float(placement.Base.y),
                    float(placement.Base.z),
                ],
                "rotation": [
                    float(value) for value in placement.Rotation.Q
                ],
            },
        }
        metadata = component_source_metadata.get(component_name)
        published = (
            dict(metadata.get("published_interfaces") or {})
            if isinstance(metadata, Mapping)
            else {}
        )
        geometry_interfaces[component_name] = {
            str(name): [str(item) for item in list(raw.get("subelements") or [])]
            for name, raw in published.items()
            if isinstance(raw, Mapping)
            and list(raw.get("subelements") or [])
        }

    geometry_declarations = []
    for item in declarations:
        policy = str(item.get("policy") or "")
        first_interface = (
            str(item["first_interface"])
            if policy in {"allowed", "required"}
            else None
        )
        second_interface = (
            str(item["second_interface"])
            if policy in {"allowed", "required"}
            else None
        )
        if first_interface is not None:
            first_name = str(item["first_component"])
            second_name = str(item["second_component"])
            for component_name, interface_name, field in (
                (first_name, first_interface, "first_interface"),
                (second_name, second_interface, "second_interface"),
            ):
                if interface_name not in geometry_interfaces.get(
                    component_name,
                    {},
                ):
                    raise AssemblyCandidateError(
                        f"Mechanism verification {check['id']!r} {field} "
                        f"{interface_name!r} on component {component_name!r} has "
                        "no published contact subelements.",
                        details={
                            "stage": "mechanism_verification_interface",
                            "verification_output": str(check["id"]),
                            "component_output": component_name,
                            "requested_interface": interface_name,
                            "available_contact_interfaces": sorted(
                                geometry_interfaces.get(component_name, {})
                            ),
                            "correction": (
                                "Publish a semantic interface backed by one or more "
                                "FaceN, EdgeN, or VertexN subelements, then use its "
                                "exact name."
                            ),
                        },
                    )
        geometry_declarations.append(
            {
                "declaration_id": str(item["id"]),
                "first_component": str(item["first_component"]),
                "second_component": str(item["second_component"]),
                "tolerance_mm": float(item["tolerance_mm"]),
                "first_interface": first_interface,
                "second_interface": second_interface,
            }
        )
    try:
        return measure_static_mechanism_pairs(
            geometry_components,
            geometry_declarations,
            interfaces=geometry_interfaces,
        )
    except Exception as exc:
        raise AssemblyCandidateError(
            f"Mechanism verification {check['id']!r} could not prepare exact "
            f"static geometry: {exc}",
            details={
                "stage": "mechanism_verification_geometry",
                "verification_output": str(check["id"]),
            },
        ) from exc


def _mechanism_scenario_contract(
    *,
    assembly_output: str,
    assembly_value: DomainValue,
    diagnostics_output: str,
    diagnostics_value: DomainValue,
    component_outputs: Mapping[int, str],
    joint_outputs: Mapping[int, str],
    simulation_contract: tuple[str, DomainValue, dict[int, str]] | None,
) -> dict[str, Any]:
    """Adapt the Assembly graph into the shared, FreeCAD-independent contract."""

    assembly_properties = _properties(assembly_value, "assembly")
    components = []
    for value in list(assembly_properties.get("components") or []):
        output_name = component_outputs[id(value)]
        properties = _properties(value, value.operation)
        if value.operation == "component":
            source = {
                "kind": "document_object",
                **_reference(
                    value.arguments[0],
                    context=f"component output {output_name!r} source",
                ),
            }
            flexible = bool(properties.get("flexible"))
        elif value.operation == "fastener":
            source = {
                "kind": "standard_fastener",
                "standard": str(value.arguments[0]),
                "nominal_thread": str(value.arguments[1]),
                "length_mm": properties.get("length_mm"),
                "model_thread": bool(properties.get("model_thread")),
                "left_handed": bool(properties.get("left_handed")),
                "options": dict(properties.get("options") or {}),
            }
            flexible = False
        else:
            raise AssemblyCandidateError(
                f"Component output {output_name!r} must come from "
                "api.component or api.fastener."
            )
        components.append(
            {
                "id": output_name,
                "label": str(properties.get("label") or ""),
                "source": source,
                "initial_placement": dict(properties.get("placement") or {}),
                "grounded": bool(properties.get("grounded")),
                "flexible": flexible,
            }
        )

    joints = []
    for value in list(assembly_properties.get("joints") or []):
        output_name = joint_outputs[id(value)]
        properties = _properties(value, "joint")
        connectors = []
        for connector in value.arguments:
            connector_properties = _properties(connector, "connector")
            component = connector.arguments[0]
            component_output = component_outputs.get(id(component))
            if component_output is None:
                raise AssemblyCandidateError(
                    f"Joint output {output_name!r} references an unreturned component."
                )
            connectors.append(
                {
                    "component_id": component_output,
                    "selection": dict(
                        connector_properties.get("selection") or {}
                    ),
                    "occurrence_path": connector_properties.get(
                        "occurrence_path"
                    ),
                    "anchor": connector_properties.get("anchor"),
                    "offset": dict(connector_properties.get("offset") or {}),
                }
            )
        joints.append(
            {
                "id": output_name,
                "label": str(properties.get("label") or ""),
                "kind": str(properties.get("kind") or ""),
                "connectors": connectors,
                "parameters": dict(properties.get("parameters") or {}),
                "length_limits_mm": properties.get("length_limits_mm"),
                "angle_limits_degrees": properties.get(
                    "angle_limits_degrees"
                ),
                "suppressed": bool(properties.get("suppressed")),
            }
        )

    diagnostics_properties = _properties(diagnostics_value, "solve")
    motions: list[dict[str, Any]] = []
    simulation = None
    if simulation_contract is not None:
        simulation_output, simulation_value, motion_outputs = simulation_contract
        simulation_properties = _properties(simulation_value, "simulation")
        for motion in list(simulation_properties.get("motions") or []):
            motion_output = motion_outputs[id(motion)]
            motion_properties = _properties(motion, "motion")
            joint_output = joint_outputs.get(id(motion.arguments[0]))
            if joint_output is None:
                raise AssemblyCandidateError(
                    f"Motion output {motion_output!r} drives an unreturned joint."
                )
            motions.append(
                {
                    "id": motion_output,
                    "label": str(motion_properties.get("label") or ""),
                    "joint_id": joint_output,
                    "motion_type": str(
                        motion_properties.get("motion_type") or ""
                    ),
                    "formula": str(motion_properties.get("formula") or ""),
                }
            )
        simulation = {
            "id": simulation_output,
            "label": str(simulation_properties.get("label") or ""),
            "motion_ids": [item["id"] for item in motions],
            "start_time_s": simulation_properties.get("start_time_s"),
            "end_time_s": simulation_properties.get("end_time_s"),
            "time_step_s": simulation_properties.get("time_step_s"),
            "error_tolerance": simulation_properties.get("error_tolerance"),
            "frames_per_second": simulation_properties.get(
                "frames_per_second"
            ),
        }

    return normalize_mechanism_scenario(
        {
            "schema": MECHANISM_SCENARIO_SCHEMA,
            "assembly": {
                "id": assembly_output,
                "label": str(assembly_properties.get("label") or ""),
            },
            "components": components,
            "joints": joints,
            "solve": {
                "id": diagnostics_output,
                "label": str(diagnostics_properties.get("label") or ""),
                "require_solved": bool(
                    diagnostics_properties.get("require_solved", True)
                ),
            },
            "motions": motions,
            "simulation": simulation,
        }
    )


def _compact_placement(placement: Any) -> dict[str, list[float]]:
    quaternion = [float(value) for value in placement.Rotation.Q]
    magnitude = math.sqrt(sum(value * value for value in quaternion))
    if magnitude <= 1.0e-15:
        raise AssemblyCandidateError(
            "The native simulation produced a zero-length placement quaternion.",
            details={"stage": "simulation_trace"},
        )
    return {
        "position_mm": [
            float(placement.Base.x),
            float(placement.Base.y),
            float(placement.Base.z),
        ],
        "rotation_xyzw": [value / magnitude for value in quaternion],
    }


def _quaternion_multiply(first: list[float], second: list[float]) -> list[float]:
    x1, y1, z1, w1 = first
    x2, y2, z2, w2 = second
    return [
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    ]


def _quaternion_rotate(quaternion: list[float], vector: list[float]) -> list[float]:
    rotated = _quaternion_multiply(
        _quaternion_multiply(quaternion, [*vector, 0.0]),
        [-quaternion[0], -quaternion[1], -quaternion[2], quaternion[3]],
    )
    return rotated[:3]


def _relative_compact_placement(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> tuple[list[float], list[float]]:
    first_position = [float(value) for value in first["position_mm"]]
    second_position = [float(value) for value in second["position_mm"]]
    first_rotation = [float(value) for value in first["rotation_xyzw"]]
    second_rotation = [float(value) for value in second["rotation_xyzw"]]
    inverse = [
        -first_rotation[0],
        -first_rotation[1],
        -first_rotation[2],
        first_rotation[3],
    ]
    relative_position = _quaternion_rotate(
        inverse,
        [
            second_position[index] - first_position[index]
            for index in range(3)
        ],
    )
    relative_rotation = _quaternion_multiply(inverse, second_rotation)
    magnitude = math.sqrt(sum(value * value for value in relative_rotation))
    return (
        relative_position,
        [value / magnitude for value in relative_rotation],
    )


def _motion_observations(
    frames: list[dict[str, Any]],
    motion_records: list[dict[str, Any]],
    joint_data: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for record in motion_records:
        joint_output = str(record["joint_output"])
        connectors = list(joint_data[joint_output]["connectors"])
        first_component = str(connectors[0]["component_output"])
        second_component = str(connectors[1]["component_output"])
        initial_position, initial_rotation = _relative_compact_placement(
            frames[0]["component_placements"][first_component],
            frames[0]["component_placements"][second_component],
        )
        maximum_translation = 0.0
        maximum_rotation = 0.0
        for frame in frames[1:]:
            position, rotation = _relative_compact_placement(
                frame["component_placements"][first_component],
                frame["component_placements"][second_component],
            )
            maximum_translation = max(
                maximum_translation,
                math.sqrt(
                    sum(
                        (position[index] - initial_position[index]) ** 2
                        for index in range(3)
                    )
                ),
            )
            dot = abs(
                sum(
                    initial_rotation[index] * rotation[index]
                    for index in range(4)
                )
            )
            maximum_rotation = max(
                maximum_rotation,
                math.degrees(2.0 * math.acos(max(-1.0, min(1.0, dot)))),
            )
        observations.append(
            {
                **record,
                "component_outputs": [first_component, second_component],
                "time_dependent": bool(
                    re.search(r"\btime\b", str(record["formula"]))
                ),
                "maximum_relative_translation_mm": maximum_translation,
                "maximum_relative_rotation_degrees": maximum_rotation,
            }
        )
    return observations


def _native_simulation_properties(obj: Any) -> None:
    properties = set(getattr(obj, "PropertiesList", []) or [])
    for property_type, name, description in (
        ("App::PropertyTime", "aTimeStart", "Simulation start time."),
        ("App::PropertyTime", "bTimeEnd", "Simulation end time."),
        ("App::PropertyTime", "cTimeStepOutput", "Simulation output time step."),
        (
            "App::PropertyFloat",
            "fGlobalErrorTolerance",
            "Integration global error tolerance.",
        ),
        ("App::PropertyInteger", "jFramesPerSecond", "Playback frames per second."),
    ):
        if name not in properties:
            obj.addProperty(property_type, name, "Simulation", description, locked=True)


def _native_motion_properties(obj: Any) -> None:
    properties = set(getattr(obj, "PropertiesList", []) or [])
    for property_type, name, description in (
        (
            "App::PropertyXLinkSubHidden",
            "Joint",
            "The native joint driven by this motion.",
        ),
        ("App::PropertyString", "Formula", "The native symbolic motion formula."),
        ("App::PropertyEnumeration", "MotionType", "Angular or linear motion."),
    ):
        if name not in properties:
            obj.addProperty(property_type, name, "Motion", description, locked=True)


def _vector_fact(value: Any) -> list[float]:
    return [float(value.x), float(value.y), float(value.z)]


def _execute_native_exploded_view(
    *,
    document: Any,
    assembly: Any,
    assembly_output: str,
    output_name: str,
    value: DomainValue,
    component_outputs: Mapping[int, str],
    components: Mapping[str, Any],
) -> dict[str, Any]:
    """Create and independently read FreeCAD's native exploded-view graph."""

    import FreeCAD as App
    import CommandCreateView
    import UtilsAssembly

    properties = _properties(value, "exploded_view")
    moves = list(properties.get("moves") or [])
    view_group = UtilsAssembly.getViewGroup(assembly)
    view = view_group.newObject("App::FeaturePython", f"CandidateView{output_name}")
    CommandCreateView.ExplodedView(view)
    native_steps: list[Any] = []
    move_records: list[dict[str, Any]] = []
    for move_index, move in enumerate(moves):
        kind = str(move.get("kind") or "")
        component_values = list(move.get("components") or [])
        component_names = [component_outputs[id(item)] for item in component_values]
        step = assembly.newObject("App::FeaturePython", f"CandidateMove{move_index}")
        CommandCreateView.ExplodedViewStep(step, 1 if kind == "radial" else 0)
        if kind == "normal":
            transform = _native_placement(
                move.get("transform"),
                context=(
                    f"exploded-view output {output_name!r} move {move_index} transform"
                ),
            )
            step.MovementTransform = transform
            record: dict[str, Any] = {
                "move_index": move_index,
                "kind": "normal",
                "component_outputs": component_names,
                "transform": dict(move["transform"]),
                "movement_transform": _placement_fact(transform),
            }
        elif kind == "radial":
            distance = float(move.get("radial_distance_mm"))
            step.MovementTransform = App.Placement(
                App.Vector(distance, 0.0, 0.0), App.Rotation()
            )
            record = {
                "move_index": move_index,
                "kind": "radial",
                "component_outputs": component_names,
                "radial_distance_mm": distance,
                "movement_transform": _placement_fact(step.MovementTransform),
            }
        else:
            raise AssemblyCandidateError(
                f"Exploded-view output {output_name!r} move {move_index} has "
                f"unsupported kind {kind!r}.",
                details={"stage": "exploded_view_graph"},
            )
        step.References = [
            assembly,
            [f"{components[name].Name}." for name in component_names],
        ]
        native_steps.append(step)
        move_records.append(record)

    solved_placements = {
        name: component.Placement.copy() for name, component in components.items()
    }
    previous_placements = dict(solved_placements)
    all_lines: list[tuple[Any, Any]] = []
    for move_index, (step, record) in enumerate(zip(native_steps, move_records)):
        view.Group = native_steps[: move_index + 1]
        final_placements, line_positions = view.Proxy._calculateExplodedPlacements(view)
        component_names = list(record["component_outputs"])
        current_placements: dict[str, Any] = {}
        changed_components: list[str] = []
        for name in component_names:
            component = components[name]
            placement = final_placements.get(component, previous_placements[name])
            current_placements[name] = placement
            if _placement_matrix(placement) != _placement_matrix(previous_placements[name]):
                changed_components.append(name)
        unchanged_components = [
            name for name in component_names if name not in changed_components
        ]
        if unchanged_components:
            raise AssemblyCandidateError(
                f"Exploded-view output {output_name!r} move {move_index} did not "
                "move every selected component.",
                details={
                    "stage": "exploded_view_effect",
                    "exploded_view_output": output_name,
                    "move_index": move_index,
                    "kind": record["kind"],
                    "component_outputs": component_names,
                    "changed_component_outputs": changed_components,
                    "unchanged_component_outputs": unchanged_components,
                    "correction": (
                        "Increase the transform translation/rotation, or choose components "
                        "whose bounding-box centres are away from the assembly centre for "
                        "a radial move. Split unlike components into separate moves when "
                        "necessary."
                    ),
                },
            )
        expected_line_count = sum(
            len(item["component_outputs"]) for item in move_records[: move_index + 1]
        )
        if len(line_positions) != expected_line_count:
            raise AssemblyCandidateError(
                f"Native exploded-view output {output_name!r} returned "
                f"{len(line_positions)} line positions after move {move_index}; "
                f"expected {expected_line_count}.",
                details={"stage": "exploded_view_native_readback"},
            )
        new_lines = line_positions[len(all_lines) :]
        if len(new_lines) != len(component_names):
            raise AssemblyCandidateError(
                f"Native exploded-view output {output_name!r} move {move_index} "
                "returned misaligned line positions.",
                details={"stage": "exploded_view_native_readback"},
            )
        record["changed_component_outputs"] = changed_components
        record["final_placements"] = {
            name: _placement_fact(current_placements[name]) for name in component_names
        }
        for name, (start, end) in zip(component_names, new_lines):
            start_values = _vector_fact(start)
            end_values = _vector_fact(end)
            record.setdefault("line_segments", []).append(
                {
                    "component_output": name,
                    "start_mm": start_values,
                    "end_mm": end_values,
                    "length_mm": math.dist(start_values, end_values),
                }
            )
        all_lines.extend(new_lines)
        previous_placements.update(current_placements)

    view.Group = native_steps
    final_placements, final_lines = view.Proxy._calculateExplodedPlacements(view)
    if len(final_lines) != len(all_lines):
        raise AssemblyCandidateError(
            f"Native exploded-view output {output_name!r} final line count changed.",
            details={"stage": "exploded_view_native_readback"},
        )
    complete_final = {
        name: _placement_fact(final_placements.get(component, solved_placements[name]))
        for name, component in components.items()
    }
    centre, size = UtilsAssembly.getComAndSize(assembly)
    if not math.isfinite(float(size)) or float(size) <= 1.0e-12:
        raise AssemblyCandidateError(
            f"Exploded-view output {output_name!r} has no finite assembly bounds.",
            details={"stage": "exploded_view_native_readback"},
        )
    data = {
        "schema": _EXPLODED_VIEW_SCHEMA,
        "assembly_output": assembly_output,
        "moves": move_records,
        "assembly_bounds": {
            "center_mm": _vector_fact(centre),
            "diagonal_mm": float(size),
        },
        "final_component_placements": complete_final,
        "line_count": len(final_lines),
        "native_readback": {
            "view_group_type": str(view_group.TypeId),
            "view_proxy_class": type(view.Proxy).__name__,
            "step_proxy_classes": [type(step.Proxy).__name__ for step in native_steps],
            "move_types": [str(step.MoveType) for step in native_steps],
            "reference_paths": [list(step.References[1]) for step in native_steps],
        },
    }
    return data


def _bom_component_sources(
    component_values: list[DomainValue],
    component_outputs: Mapping[int, str],
) -> list[dict[str, Any]]:
    """Bind planner inputs to the exact returned component graph."""

    result: list[dict[str, Any]] = []
    for value in component_values:
        output_name = component_outputs[id(value)]
        if value.operation == "fastener":
            identity = _catalog_fastener_identity(
                value,
                context=f"BOM component output {output_name!r}",
            )
            from VibeCADFasteners import fastener_bom_properties

            source = _catalog_fastener_source(identity)
            result.append(
                {
                    "output_name": output_name,
                    "reference": {
                        **source,
                        "label": str(identity["part_number"]),
                        "source_kind": "standard_fastener",
                        "document_file_name": "",
                        "bom_properties": fastener_bom_properties(identity),
                    },
                }
            )
            continue
        if value.operation != "component" or len(value.arguments) != 1:
            raise AssemblyCandidateError(
                f"BOM component output {output_name!r} must come from "
                "api.component or api.fastener.",
                details={"stage": "bom_graph", "component_output": output_name},
            )
        source = _reference(
            value.arguments[0],
            context=f"BOM component output {output_name!r} source",
        )
        key = (source["document_uid"], source["object_name"])
        metadata = _REFERENCE_METADATA[key]
        reference: dict[str, Any] = {
            **source,
            "label": str(metadata.get("label") or source["object_name"]),
            "source_kind": str(metadata.get("source_kind") or "shape"),
            "document_file_name": str(metadata.get("document_file_name") or ""),
            "bom_properties": list(metadata.get("bom_properties") or []),
        }
        hierarchy = _REFERENCE_HIERARCHIES.get(key)
        if hierarchy is not None:
            reference["assembly_hierarchy"] = dict(hierarchy["descriptor"])
        result.append({"output_name": output_name, "reference": reference})
    return result


def _spreadsheet_column(number: int) -> str:
    result = ""
    value = number
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _spreadsheet_content(sheet: Any, address: str) -> str:
    try:
        value = str(sheet.getContents(address) or "")
    except ValueError:
        return ""
    # Spreadsheet string cells preserve a leading quote used to force literal
    # text.  Native BomObject::getText applies the same one-character removal.
    return value[1:] if value.startswith("'") else value


def _execute_native_bom(
    *,
    document: Any,
    assembly: Any,
    assembly_output: str,
    output_name: str,
    value: DomainValue,
    component_sources: list[dict[str, Any]],
    bom_index: int,
) -> dict[str, Any]:
    """Generate and read one native BOM entirely inside the isolated worker."""

    import FreeCAD as App

    from VibeCADAssemblyBOM import (
        ASSEMBLY_BOM_SCHEMA,
        AssemblyBOMError,
        plan_assembly_bom,
    )

    properties = _properties(value, "bill_of_materials")
    try:
        contract = plan_assembly_bom(
            component_sources,
            columns=properties.get("columns"),
            detail_subassemblies=properties.get("detail_subassemblies"),
            detail_parts=properties.get("detail_parts"),
            only_parts=properties.get("only_parts"),
            row_overrides=properties.get("row_overrides"),
        )
    except AssemblyBOMError as exc:
        raise AssemblyCandidateError(str(exc), details=exc.details) from exc
    if str(contract.get("schema") or "") != _ASSEMBLY_BOM_SCHEMA or (
        ASSEMBLY_BOM_SCHEMA != _ASSEMBLY_BOM_SCHEMA
    ):
        raise AssemblyCandidateError(
            f"BOM output {output_name!r} planner returned an unsupported schema.",
            details={"stage": "bom_planner", "bom_output": output_name},
        )

    group = assembly.newObject(
        "Assembly::BomGroup", f"CandidateBOMGroup{bom_index}"
    )
    if group is None:
        raise AssemblyCandidateError(
            f"FreeCAD did not create a native BOM group for output {output_name!r}.",
            details={"stage": "bom_native_creation", "bom_output": output_name},
        )
    bom = group.newObject("Assembly::BomObject", f"CandidateBOM{bom_index}")
    if bom is None:
        raise AssemblyCandidateError(
            f"FreeCAD did not create Assembly::BomObject for output {output_name!r}.",
            details={"stage": "bom_native_creation", "bom_output": output_name},
        )
    columns = list(contract["columns"])
    native_names = [str(column["native_name"]) for column in columns]
    settings = dict(contract["settings"])
    bom.columnsNames = native_names
    bom.detailSubAssemblies = bool(settings["detail_subassemblies"])
    bom.detailParts = bool(settings["detail_parts"])
    bom.onlyParts = bool(settings["only_parts"])
    document.recompute()

    if (
        str(getattr(group, "TypeId", "") or "") != "Assembly::BomGroup"
        or str(getattr(bom, "TypeId", "") or "") != "Assembly::BomObject"
        or "autoGenerate" not in set(getattr(bom, "PropertiesList", []) or [])
        or bool(getattr(bom, "autoGenerate", False)) is not True
        or list(getattr(bom, "columnsNames", []) or []) != native_names
        or bool(getattr(bom, "detailSubAssemblies", False))
        is not settings["detail_subassemblies"]
        or bool(getattr(bom, "detailParts", False)) is not settings["detail_parts"]
        or bool(getattr(bom, "onlyParts", False)) is not settings["only_parts"]
    ):
        raise AssemblyCandidateError(
            f"Native BOM output {output_name!r} changed its declared type or settings.",
            details={
                "stage": "bom_native_readback",
                "bom_output": output_name,
            },
        )
    raw_used_range = bom.getUsedRange()
    used_range = (
        [str(item) for item in raw_used_range]
        if isinstance(raw_used_range, tuple) and len(raw_used_range) == 2
        else []
    )
    if used_range != list(contract["used_range"]):
        raise AssemblyCandidateError(
            f"Native BOM output {output_name!r} returned used range {used_range}; "
            f"expected {contract['used_range']}.",
            details={
                "stage": "bom_native_readback",
                "bom_output": output_name,
                "observed_used_range": used_range,
                "expected_used_range": list(contract["used_range"]),
            },
        )
    headers = [
        _spreadsheet_content(bom, f"{_spreadsheet_column(index + 1)}1")
        for index in range(len(columns))
    ]
    if headers != native_names:
        raise AssemblyCandidateError(
            f"Native BOM output {output_name!r} returned altered column headers.",
            details={
                "stage": "bom_native_readback",
                "bom_output": output_name,
                "observed_headers": headers,
                "expected_headers": native_names,
            },
        )
    mirrored_suffix = str(
        App.ParamGet("User parameter:BaseApp/Preferences/Mod/Assembly").GetString(
            "BomMirroredSuffix", " (mirrored)"
        )
        or ""
    )
    if len(mirrored_suffix) > 256:
        raise AssemblyCandidateError(
            "The native Assembly mirrored BOM suffix exceeds 256 characters.",
            details={
                "stage": "bom_native_readback",
                "correction": (
                    "Shorten Preferences > Assembly > BOM mirrored suffix and retry."
                ),
            },
        )
    normalized_native_rows: list[dict[str, str]] = []
    substituted_headings = [
        str(column["heading"])
        for column in columns
        if column["kind"] == "custom"
        or (column["kind"] == "builtin" and column.get("key") == "file_name")
    ]
    for row_index, expected_row in enumerate(list(contract["rows"]), start=2):
        normalized: dict[str, str] = {}
        for column_index, column in enumerate(columns, start=1):
            heading = str(column["heading"])
            observed = _spreadsheet_content(
                bom, f"{_spreadsheet_column(column_index)}{row_index}"
            )
            expected = str(expected_row["cells"][heading])
            if column["kind"] == "custom":
                if observed:
                    raise AssemblyCandidateError(
                        f"Native BOM output {output_name!r} unexpectedly retained a "
                        f"custom value in row {row_index - 1}, column {heading!r}.",
                        details={"stage": "bom_native_readback", "bom_output": output_name},
                    )
                normalized[heading] = ""
                continue
            if column["kind"] == "builtin" and column.get("key") == "file_name":
                # Reconstructed sources intentionally live in one unsaved worker
                # document.  The authenticated original basename is substituted by
                # the planner and the raw worker path is never returned.
                normalized[heading] = ""
                continue
            native_expected = expected
            if (
                column["kind"] == "builtin"
                and column.get("key") == "name"
                and bool(expected_row["mirrored"])
            ):
                label = expected.removesuffix(" (mirrored)")
                native_expected = f"{label}{mirrored_suffix}"
            numeric_equivalent = False
            if observed != native_expected and column["kind"] == "property":
                try:
                    observed_number = float(observed)
                    expected_number = float(native_expected)
                    numeric_equivalent = math.isclose(
                        observed_number,
                        expected_number,
                        rel_tol=1.0e-12,
                        abs_tol=1.0e-12,
                    )
                except (TypeError, ValueError):
                    numeric_equivalent = False
            if observed != native_expected and not numeric_equivalent:
                raise AssemblyCandidateError(
                    f"Native BOM output {output_name!r} row {row_index - 1} column "
                    f"{heading!r} disagrees with authenticated source data.",
                    details={
                        "stage": "bom_native_readback",
                        "bom_output": output_name,
                        "row_index": row_index - 1,
                        "heading": heading,
                        "observed": observed,
                        "expected": native_expected,
                    },
                )
            normalized[heading] = expected
        normalized_native_rows.append(normalized)
    native_readback: dict[str, Any] = {
        "group_type_id": str(group.TypeId),
        "object_type_id": str(bom.TypeId),
        "auto_generate": bool(getattr(bom, "autoGenerate", True)),
        "columns_names": native_names,
        "settings": settings,
        "used_range": used_range,
        "row_count": len(normalized_native_rows),
        "normalized_cells": normalized_native_rows,
        "substituted_headings": substituted_headings,
        "mirrored_names_normalized": any(
            bool(row["mirrored"]) for row in list(contract["rows"])
        ),
    }
    native_readback["sha256"] = hashlib.sha256(
        json.dumps(
            native_readback,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        **contract,
        "assembly_output": assembly_output,
        "native_readback": native_readback,
    }


def _execute_native_simulation(
    *,
    document: Any,
    assembly: Any,
    assembly_output: str,
    simulation_output: str,
    simulation_value: DomainValue,
    motion_outputs: Mapping[int, str],
    joint_outputs: Mapping[int, str],
    joint_objects: Mapping[str, Any],
    joint_data: Mapping[str, Mapping[str, Any]],
    components: Mapping[str, Any],
    artifact_root: Path,
    outputs_by_name: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    properties = _properties(simulation_value, "simulation")
    simulation_group = assembly.newObject(
        "Assembly::SimulationGroup", "CandidateSimulations"
    )
    simulation = simulation_group.newObject(
        "App::FeaturePython", "CandidateSimulation"
    )
    simulation.addExtension("App::GroupExtensionPython")
    _native_simulation_properties(simulation)
    simulation.aTimeStart = float(properties["start_time_s"])
    simulation.bTimeEnd = float(properties["end_time_s"])
    simulation.cTimeStepOutput = float(properties["time_step_s"])
    simulation.fGlobalErrorTolerance = float(properties["error_tolerance"])
    simulation.jFramesPerSecond = int(properties["frames_per_second"])

    native_motions = []
    motion_records: list[dict[str, Any]] = []
    for index, value in enumerate(list(properties.get("motions") or [])):
        output_name = motion_outputs[id(value)]
        motion_properties = _properties(value, "motion")
        joint_value = value.arguments[0]
        joint_output = joint_outputs[id(joint_value)]
        joint = joint_objects[joint_output]
        motion = assembly.newObject("App::FeaturePython", f"CandidateMotion{index}")
        _native_motion_properties(motion)
        native_type = (
            "Angular"
            if str(motion_properties["motion_type"]) == "angular"
            else "Linear"
        )
        motion.MotionType = ["Angular", "Linear"]
        motion.MotionType = native_type
        motion.Joint = joint
        motion.Formula = str(motion_properties["formula"])
        native_motions.append(motion)
        record = {
            "motion_output": output_name,
            "joint_output": joint_output,
            "joint_type": str(joint_data[joint_output]["kind"]),
            "motion_type": str(motion_properties["motion_type"]),
            "native_motion_type": native_type,
            "formula": str(motion_properties["formula"]),
        }
        motion_records.append(record)
        outputs_by_name[output_name]["assembly_data"] = {
            "assembly_output": assembly_output,
            "simulation_output": simulation_output,
            **record,
        }
    simulation.Group = native_motions
    document.recompute()

    saved_placements = {
        name: component.Placement.copy() for name, component in components.items()
    }
    try:
        try:
            native_code = int(assembly.generateSimulation(simulation))
        except Exception as exc:
            raise AssemblyCandidateError(
                f"Native Assembly simulation {simulation_output!r} raised "
                f"{type(exc).__name__}: {exc}",
                details={
                    "stage": "native_simulation",
                    "simulation_output": simulation_output,
                    "motions": motion_records,
                    "correction": (
                        "Use only the documented formula names/functions and verify "
                        "that every driven joint belongs to a clean solved mechanism."
                    ),
                },
            ) from exc
        if native_code != 0:
            raise AssemblyCandidateError(
                f"Native Assembly simulation {simulation_output!r} failed with "
                f"{_SOLVER_VERDICTS.get(native_code, 'native_error')} "
                f"(code {native_code}).",
                details={
                    "stage": "native_simulation",
                    "simulation_output": simulation_output,
                    "native_code": native_code,
                    "motions": motion_records,
                    "correction": (
                        "Keep the solved joint graph, then simplify the reported motion "
                        "formula to a documented expression such as initialValue + "
                        "0.5*time and retry."
                    ),
                },
            )
        frame_count = int(assembly.numberOfFrames())
        estimated_limit = int(properties["estimated_frame_limit"])
        pose_count = frame_count * len(components)
        if (
            frame_count < 2
            or frame_count > estimated_limit
            or frame_count > 10_000
            or pose_count > 100_000
        ):
            raise AssemblyCandidateError(
                f"Native Assembly simulation {simulation_output!r} returned "
                f"{frame_count} frames ({pose_count} component poses), outside the "
                "declared bounded schedule.",
                details={
                    "stage": "simulation_trace",
                    "simulation_output": simulation_output,
                    "frame_count": frame_count,
                    "estimated_frame_limit": estimated_limit,
                    "pose_count": pose_count,
                    "correction": "Increase time_step_s or shorten the time range.",
                },
            )
        frames: list[dict[str, Any]] = []
        start_time = float(properties["start_time_s"])
        end_time = float(properties["end_time_s"])
        time_step = float(properties["time_step_s"])
        for frame_index in range(frame_count):
            update_result = assembly.updateForFrame(frame_index)
            # The generated Python binding currently reports successful native
            # frame application as None even though the C++ method uses a status
            # code internally. Preserve a future numeric failure code if exposed.
            update_code = 0 if update_result is None else int(update_result)
            if update_code != 0:
                raise AssemblyCandidateError(
                    f"Native Assembly simulation {simulation_output!r} could not "
                    f"read frame {frame_index} (code {update_code}).",
                    details={
                        "stage": "simulation_trace",
                        "simulation_output": simulation_output,
                        "frame_index": frame_index,
                        "native_code": update_code,
                    },
                )
            frames.append(
                {
                    "frame_index": frame_index,
                    "frame_kind": "input" if frame_index == 0 else "solver_output",
                    "nominal_time_s": None
                    if frame_index == 0
                    else min(end_time, start_time + (frame_index - 1) * time_step),
                    "component_placements": {
                        name: _compact_placement(component.Placement)
                        for name, component in components.items()
                    },
                }
            )
    finally:
        for name, placement in saved_placements.items():
            components[name].Placement = placement

    observations = _motion_observations(frames, motion_records, joint_data)
    for observation in observations:
        change = (
            float(observation["maximum_relative_rotation_degrees"])
            if observation["motion_type"] == "angular"
            else float(observation["maximum_relative_translation_mm"])
        )
        tolerance = 1.0e-7 if observation["motion_type"] == "angular" else 1.0e-8
        if observation["time_dependent"] and change <= tolerance:
            unit = "degrees" if observation["motion_type"] == "angular" else "mm"
            raise AssemblyCandidateError(
                f"Motion output {observation['motion_output']!r} uses time but "
                f"produced no measurable {observation['motion_type']} movement "
                f"(maximum {change:.9g} {unit}).",
                details={
                    "stage": "simulation_motion_effect",
                    **observation,
                    "correction": (
                        "Check the formula magnitude and driven joint type, remove an "
                        "accidental zero multiplier, or lengthen the simulation range."
                    ),
                },
            )

    trace = {
        "schema": _SIMULATION_TRACE_SCHEMA,
        "assembly_output": assembly_output,
        "simulation_output": simulation_output,
        "component_outputs": list(components),
        "motion_outputs": [record["motion_output"] for record in motion_records],
        "parameters": {
            "start_time_s": float(properties["start_time_s"]),
            "end_time_s": float(properties["end_time_s"]),
            "time_step_s": float(properties["time_step_s"]),
            "error_tolerance": float(properties["error_tolerance"]),
            "frames_per_second": int(properties["frames_per_second"]),
        },
        "motion_observations": observations,
        "frames": frames,
    }
    encoded = json.dumps(
        trace,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > _MAX_SIMULATION_TRACE_BYTES:
        raise AssemblyCandidateError(
            f"Simulation trace requires {len(encoded)} bytes; the accepted maximum is "
            f"{_MAX_SIMULATION_TRACE_BYTES} bytes.",
            details={
                "stage": "simulation_trace",
                "simulation_output": simulation_output,
                "correction": "Increase time_step_s or shorten the time range.",
            },
        )
    relative = Path("outputs") / "assembly-simulation-trace.json"
    target = artifact_root / relative
    target.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    summary = {
        "simulation_output": simulation_output,
        "motion_outputs": [record["motion_output"] for record in motion_records],
        "parameters": dict(trace["parameters"]),
        "native_code": 0,
        "frame_count": len(frames),
        "pose_count": len(frames) * len(components),
        "motion_observations": observations,
        "artifact_schema": _SIMULATION_TRACE_SCHEMA,
        "artifact_sha256": digest,
        "artifact_bytes": len(encoded),
    }
    simulation_item = outputs_by_name[simulation_output]
    simulation_item.update(
        {
            "artifact_kind": "assembly_simulation_json",
            "artifact_path": str(relative),
            "artifact_schema": _SIMULATION_TRACE_SCHEMA,
            "artifact_sha256": digest,
            "artifact_bytes": len(encoded),
            "frame_count": len(frames),
            "pose_count": len(frames) * len(components),
            "assembly_data": {"assembly_output": assembly_output, **summary},
        }
    )
    return summary


def _subelement(component: Any, name: str, *, context: str) -> tuple[Any, str]:
    match = _SUBELEMENT.fullmatch(name)
    if match is None:
        raise AssemblyCandidateError(f"{context} has invalid exact subelement {name!r}.")
    collection_name = {
        "Face": "Faces",
        "Edge": "Edges",
        "Vertex": "Vertexes",
    }[match.group(1)]
    values = list(getattr(component.Shape, collection_name, []) or [])
    index = int(match.group(2))
    if index > len(values):
        raise AssemblyCandidateError(
            f"{context} selects {name}, but the snapshotted component has only "
            f"{len(values)} {collection_name.lower()}.",
            details={
                "stage": "connector_selection",
                "requested": name,
                "available_range": f"1..{len(values)}",
                "component": component.Name,
            },
        )
    return values[index - 1], match.group(1).lower()


def _connector_anchor(
    component: Any,
    selected_shape: Any,
    selected_category: str,
    selected_name: str,
    requested: Any,
    *,
    context: str,
) -> str:
    """Resolve an optional exact vertex anchor against the selected subshape."""

    if requested is None or str(requested or "") == selected_name:
        return selected_name
    anchor_name = str(requested or "")
    anchor_shape, anchor_category = _subelement(
        component,
        anchor_name,
        context=f"{context}.anchor",
    )
    if anchor_category != "vertex":
        raise AssemblyCandidateError(
            f"{context}.anchor must be the selected subelement itself or an exact "
            "VertexN on that edge/face.",
            details={
                "stage": "connector_anchor",
                "selection": selected_name,
                "requested_anchor": anchor_name,
                "resolved_anchor_type": anchor_category,
            },
        )
    if selected_category == "vertex":
        raise AssemblyCandidateError(
            f"{context} selects vertex {selected_name}; its anchor cannot be changed.",
            details={
                "stage": "connector_anchor",
                "selection": selected_name,
                "requested_anchor": anchor_name,
            },
        )
    selected_vertices = list(getattr(selected_shape, "Vertexes", []) or [])
    available_anchors = []
    for index, candidate in enumerate(
        list(getattr(component.Shape, "Vertexes", []) or []),
        start=1,
    ):
        for vertex in selected_vertices:
            try:
                same = bool(vertex.isSame(candidate)) or (
                    vertex.Point - candidate.Point
                ).Length <= 1.0e-7
            except Exception:
                same = False
            if same:
                available_anchors.append(f"Vertex{index}")
                break
    belongs = anchor_name in available_anchors
    if not belongs:
        raise AssemblyCandidateError(
            f"{context}.anchor {anchor_name} is not a vertex of selected "
            f"{selected_name}; available anchors are "
            f"{available_anchors or ['<none>']}.",
            details={
                "stage": "connector_anchor",
                "selection": selected_name,
                "requested_anchor": anchor_name,
                "available_anchors": available_anchors,
                "available_vertex_count": len(selected_vertices),
                "suggestion": (
                    "Choose a VertexN belonging to the selected edge/face, or omit "
                    "anchor to use the natural center."
                ),
            },
        )
    return anchor_name


def _geometry_type(value: Any, category: str) -> str:
    if category == "origin":
        return "component_origin"
    if category == "vertex":
        return "point"
    geometry = value.Surface if category == "face" else value.Curve
    name = type(geometry).__name__.lower()
    aliases = (
        ("line", "line"),
        ("circle", "circle"),
        ("plane", "plane"),
        ("cylinder", "cylinder"),
        ("cone", "cone"),
        ("sphere", "sphere"),
        ("torus", "torus"),
        ("ellipse", "ellipse"),
        ("bspline", "bspline"),
        ("bezier", "bezier"),
    )
    return next((result for marker, result in aliases if marker in name), name or category)


def _resolve_connector(
    connector: DomainValue,
    *,
    component_outputs: Mapping[int, str],
    components: Mapping[str, Any],
    component_sources: Mapping[str, dict[str, str]],
    component_source_metadata: Mapping[str, Mapping[str, Any]],
    component_reconstructions: Mapping[str, dict[str, Any] | None],
    context: str,
) -> dict[str, Any]:
    if connector.operation != "connector" or connector.output_type != "connector":
        raise AssemblyCandidateError(f"{context} must come from api.connector.")
    if len(connector.arguments) != 1:
        raise AssemblyCandidateError(f"{context} has a malformed component reference.")
    component_value = connector.arguments[0]
    output_name = component_outputs.get(id(component_value))
    component = components.get(str(output_name or ""))
    source_ref = component_sources.get(str(output_name or ""))
    if component is None or source_ref is None:
        raise AssemblyCandidateError(
            f"{context} references a component not returned by this assembly graph."
        )
    properties = _properties(connector, "connector")
    selection = properties.get("selection")
    if not isinstance(selection, Mapping):
        raise AssemblyCandidateError(f"{context}.selection is malformed.")
    metadata = component_source_metadata.get(str(output_name or ""))
    if metadata is None:
        raise AssemblyCandidateError(
            f"{context} lost its authenticated component-source metadata."
        )
    reconstruction = component_reconstructions.get(str(output_name or ""))
    occurrence_path = str(properties.get("occurrence_path") or "")
    hierarchy_chain = None
    selection_component = component
    if occurrence_path:
        if reconstruction is None:
            raise AssemblyCandidateError(
                f"{context} requests occurrence_path={occurrence_path!r}, but component "
                f"{output_name!r} has no authenticated Assembly/App::Part hierarchy.",
                details={
                    "stage": "assembly_occurrence_path",
                    "component_output": output_name,
                    "requested_path": occurrence_path,
                    "correction": (
                        "Use a native AssemblyObject or App::Part reference regenerated "
                        "from the live document, or remove occurrence_path for an aggregate "
                        "component."
                    ),
                },
            )
        hierarchy_chain = _hierarchy_path_chain(
            reconstruction,
            node_id=str(reconstruction["descriptor"]["root_node_id"]),
            stable_path=occurrence_path,
            context=context,
        )
        leaf_node = hierarchy_chain[-1][3]
        selection_component = reconstruction["native_nodes"][str(leaf_node["node_id"])]
        leaf_contract = leaf_node.get("reference_contract")
        metadata = leaf_contract if isinstance(leaf_contract, Mapping) else {}
    elif (
        reconstruction is not None
        and str(getattr(component, "TypeId", "") or "") == "Assembly::AssemblyLink"
        and not bool(getattr(component, "Rigid", True))
    ):
        available_paths = [
            str(item["path"])
            for item in list(reconstruction["descriptor"]["occurrence_paths"])
        ]
        raise AssemblyCandidateError(
            f"{context} targets flexible component {output_name!r} without an internal "
            "occurrence_path. A flexible AssemblyLink root is not one rigid solver body.",
            details={
                "stage": "assembly_occurrence_path",
                "component_output": output_name,
                "available_occurrence_paths": available_paths,
                "correction": (
                    "Set occurrence_path to one exact value copied from "
                    "available_occurrence_paths, for example "
                    f"{available_paths[0]!r}." if available_paths else
                    "The source Assembly contains no addressable component occurrences."
                ),
            },
        )
    mode = str(selection.get("type") or "")
    semantic: dict[str, Any] | None = None
    standard_frame: Mapping[str, Any] | None = None
    semantic_frame: Mapping[str, Any] | None = None
    if mode == "component_origin":
        if properties.get("anchor") is not None:
            raise AssemblyCandidateError(
                f"{context}.anchor is unavailable for a component-origin connector.",
                details={
                    "stage": "connector_anchor",
                    "selection": "component_origin",
                    "suggestion": "Remove anchor or select an exact edge/face/vertex.",
                },
            )
        if metadata.get("requires_semantic_interfaces"):
            raise AssemblyCandidateError(
                f"{context} targets regenerating scripted component {output_name!r}; "
                "select a published_interface that explicitly declares the component "
                "origin or connector frame.",
                details={
                    "stage": "connector_selection",
                    "component_output": output_name,
                    "available_interfaces": sorted(
                        dict(metadata.get("published_interfaces") or {})
                    ),
                },
            )
        element = ""
        anchor = ""
        geometry_type = "component_origin"
    elif mode == "exact_subelement":
        if metadata.get("transient_topology"):
            raise AssemblyCandidateError(
                f"{context} targets regenerating scripted component {output_name!r}; "
                "use a published_interface connector instead of a transient "
                "FaceN/EdgeN/VertexN name.",
                details={
                    "stage": "connector_selection",
                    "component_output": output_name,
                    "available_interfaces": sorted(
                        dict(metadata.get("published_interfaces") or {})
                    ),
                },
            )
        element = str(selection.get("subelement") or "")
        subshape, category = _subelement(selection_component, element, context=context)
        anchor = _connector_anchor(
            selection_component,
            subshape,
            category,
            element,
            properties.get("anchor"),
            context=context,
        )
        geometry_type = _geometry_type(subshape, category)
    elif mode == "published_interface":
        if properties.get("anchor") is not None:
            raise AssemblyCandidateError(
                f"{context}.anchor cannot refine a semantic published interface.",
                details={
                    "stage": "connector_anchor",
                    "suggestion": (
                        "Publish a dedicated connector interface at the required point, "
                        "or use connector offset."
                    ),
                },
            )
        interface_name = str(selection.get("interface_name") or "")
        interfaces = dict(metadata.get("published_interfaces") or {})
        raw = interfaces.get(interface_name)
        if not isinstance(raw, dict):
            raise AssemblyCandidateError(
                f"{context} published interface {interface_name!r} does not exist "
                f"on component {output_name!r}.",
                details={
                    "stage": "connector_selection",
                    "component_output": output_name,
                    "available_interfaces": sorted(interfaces),
                },
            )
        subelements = list(raw.get("subelements") or [])
        geometry = list(raw.get("geometry") or [])
        if len(subelements) > 1 or len(geometry) > 1:
            raise AssemblyCandidateError(
                f"{context} published interface must resolve to one connector, "
                "not a multi-element selection.",
                details={"interface": interface_name, "resolved": raw},
            )
        element = str(subelements[0]) if subelements else ""
        anchor = element
        interface_selection = dict(raw.get("selection") or {})
        if element:
            subshape, category = _subelement(
                selection_component, element, context=context
            )
            inferred = _geometry_type(subshape, category)
        else:
            inferred = (
                "component_frame"
                if interface_selection.get("type") == "frame"
                else "component_origin"
            )
        geometry_type = str(
            (geometry[0] if geometry else {}).get("geometry_type") or inferred
        )
        raw_standard_frame = raw.get("standard_frame")
        if raw_standard_frame is not None:
            if not isinstance(raw_standard_frame, Mapping):
                raise AssemblyCandidateError(
                    f"{context} published interface has a malformed standard frame."
                )
            standard_frame = raw_standard_frame
        raw_semantic_frame = raw.get("connector_frame")
        if interface_selection.get("type") == "frame":
            if not isinstance(raw_semantic_frame, Mapping):
                raise AssemblyCandidateError(
                    f"{context} published interface has no explicit connector frame."
                )
            semantic_frame = raw_semantic_frame
        semantic = {
            "type": "published_interface",
            "interface_name": interface_name,
            "model_id": str(raw.get("model_id") or ""),
            "publication_name": str(raw.get("publication_name") or ""),
            "output_key": str(raw.get("output_key") or ""),
        }
    else:
        raise AssemblyCandidateError(f"{context} uses unsupported selection type {mode!r}.")
    hierarchy_reference = None
    native_component = component
    native_element = element
    native_anchor = anchor
    if occurrence_path:
        if reconstruction is None:  # Kept explicit for static narrowing and diagnostics.
            raise AssemblyCandidateError(f"{context} lost its authenticated hierarchy.")
        hierarchy_reference = _resolve_hierarchy_reference(
            reconstruction,
            root=component,
            node_id=str(reconstruction["descriptor"]["root_node_id"]),
            stable_path=occurrence_path,
            subelements=[element, anchor],
            context=context,
        )
        native_component = hierarchy_reference["target"]
        native_element, native_anchor = hierarchy_reference["subelements"]
    user_offset = _native_placement(
        properties.get("offset"),
        context=f"{context}.offset",
    )
    if standard_frame is not None:
        interface_frame = _native_placement(
            standard_frame,
            context=f"{context} published-interface frame",
        )
    elif semantic_frame is not None:
        interface_frame = _native_placement_matrix(
            semantic_frame.get("matrix"),
            context=f"{context} semantic connector frame",
        )
    else:
        interface_frame = None
    offset = (
        interface_frame.multiply(user_offset)
        if interface_frame is not None
        else user_offset
    )
    return {
        "component_output": output_name,
        "component": native_component,
        "selection": dict(selection),
        "semantic_selection": semantic,
        "element": element,
        "anchor": anchor,
        "native_element": native_element,
        "native_anchor": native_anchor,
        "geometry_type": geometry_type,
        "occurrence_path": occurrence_path or None,
        "native_target_mode": (
            str(hierarchy_reference["target_mode"])
            if hierarchy_reference is not None
            else "component_root"
        ),
        "native_hierarchy_chain": (
            list(hierarchy_reference["native_chain"])
            if hierarchy_reference is not None
            else []
        ),
        "interface_frame": (
            _placement_fact(interface_frame)
            if interface_frame is not None
            else None
        ),
        "offset": offset,
    }


def _compatibility(kind: str, connectors: list[dict[str, Any]]) -> dict[str, Any]:
    geometry = [str(item.get("geometry_type") or "") for item in connectors]
    axis_capable = {
        "line",
        "circle",
        "plane",
        "cylinder",
        "cone",
        "component_origin",
        "component_frame",
    }
    rotary = {"circle", "cylinder", "cone"}
    linear = {"line", "plane"}
    criteria = "any valid connector geometry"
    compatible = all(geometry)
    if kind in {"revolute", "cylindrical", "screw", "gears", "belt"}:
        criteria = "both connectors must define axes"
        compatible = all(item in axis_capable for item in geometry)
    elif kind == "slider":
        criteria = "both connectors must define linear axes or plane normals"
        compatible = all(
            item in linear | {"component_origin", "component_frame"}
            for item in geometry
        )
    elif kind == "rack_pinion":
        criteria = "one linear connector and one circular/cylindrical connector"
        compatible = any(item in linear for item in geometry) and any(
            item in rotary for item in geometry
        )
    elif kind in {"parallel", "perpendicular", "angle"}:
        criteria = "both connectors must define orientations"
        compatible = all(item in axis_capable for item in geometry)
    return {
        "ok": compatible,
        "joint_type": kind,
        "criteria": criteria,
        "resolved_geometry_types": geometry,
    }


def _apply_joint_properties(joint: Any, properties: Mapping[str, Any]) -> None:
    kind = str(properties.get("kind") or "")
    parameters = dict(properties.get("parameters") or {})
    if kind == "distance":
        joint.Distance = float(parameters["distance_mm"])
    elif kind == "angle":
        joint.Angle = float(parameters["angle_degrees"])
    elif kind == "rack_pinion":
        joint.Distance = float(parameters["pitch_radius_mm"])
    elif kind == "screw":
        joint.Distance = float(parameters["thread_pitch_mm"])
    elif kind in {"gears", "belt"}:
        joint.Distance = float(parameters["radius1_mm"])
        joint.Distance2 = float(parameters["radius2_mm"])
    length_limits = properties.get("length_limits_mm")
    length_min = length_limits[0] if length_limits is not None else None
    length_max = length_limits[1] if length_limits is not None else None
    joint.EnableLengthMin = length_min is not None
    joint.EnableLengthMax = length_max is not None
    if length_limits is not None:
        if length_min is not None:
            joint.LengthMin = float(length_min)
        if length_max is not None:
            joint.LengthMax = float(length_max)
    angle_limits = properties.get("angle_limits_degrees")
    angle_min = angle_limits[0] if angle_limits is not None else None
    angle_max = angle_limits[1] if angle_limits is not None else None
    joint.EnableAngleMin = angle_min is not None
    joint.EnableAngleMax = angle_max is not None
    if angle_limits is not None:
        if angle_min is not None:
            joint.AngleMin = float(angle_min)
        if angle_max is not None:
            joint.AngleMax = float(angle_max)


def _joint_readback(joint: Any, kind: str) -> dict[str, Any]:
    def quantity(name: str) -> float:
        value = getattr(joint, name)
        return float(getattr(value, "Value", value))

    result: dict[str, Any] = {
        "native_type": str(joint.JointType),
        "suppressed": bool(getattr(joint, "Suppressed", False)),
        "length_limits_mm": (
            [
                quantity("LengthMin") if bool(joint.EnableLengthMin) else None,
                quantity("LengthMax") if bool(joint.EnableLengthMax) else None,
            ]
            if bool(joint.EnableLengthMin) or bool(joint.EnableLengthMax)
            else None
        ),
        "angle_limits_degrees": (
            [
                quantity("AngleMin") if bool(joint.EnableAngleMin) else None,
                quantity("AngleMax") if bool(joint.EnableAngleMax) else None,
            ]
            if bool(joint.EnableAngleMin) or bool(joint.EnableAngleMax)
            else None
        ),
    }
    if kind == "distance":
        result["distance_mm"] = quantity("Distance")
    elif kind == "angle":
        result["angle_degrees"] = quantity("Angle")
    elif kind == "rack_pinion":
        result["pitch_radius_mm"] = quantity("Distance")
    elif kind == "screw":
        result["thread_pitch_mm"] = quantity("Distance")
    elif kind in {"gears", "belt"}:
        result["radius1_mm"] = quantity("Distance")
        result["radius2_mm"] = quantity("Distance2")
    return result


def _native_diagnostics(assembly: Any) -> dict[str, Any]:
    getter = getattr(assembly, "getSolverDiagnostics", None)
    if not callable(getter):
        return {
            "available": False,
            "reason": "AssemblyObject.getSolverDiagnostics is unavailable in this build.",
        }
    try:
        value = getter()
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    if not isinstance(value, dict):
        return {
            "available": False,
            "error": "AssemblyObject.getSolverDiagnostics returned a non-object value.",
        }
    return {"available": True, **_json_safe(value)}


def _diagnostics_conflict(diagnostics: Mapping[str, Any]) -> bool:
    return any(
        bool(diagnostics.get(name))
        for name in (
            "has_conflicts",
            "has_redundancies",
            "has_partial_redundancies",
            "has_malformed_constraints",
        )
    )


def _frame_z_axis(frame: Mapping[str, Any]) -> tuple[float, float, float]:
    matrix = list(frame.get("matrix") or [])
    if len(matrix) != 16:
        return (0.0, 0.0, 0.0)
    return (float(matrix[2]), float(matrix[6]), float(matrix[10]))


def _parallel_axes(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    axis1 = _frame_z_axis(first)
    axis2 = _frame_z_axis(second)
    norm1 = math.sqrt(sum(item * item for item in axis1))
    norm2 = math.sqrt(sum(item * item for item in axis2))
    if norm1 <= 1.0e-12 or norm2 <= 1.0e-12:
        return False
    dot = sum(a * b for a, b in zip(axis1, axis2, strict=True)) / (norm1 * norm2)
    return abs(dot) >= 1.0 - 1.0e-7


def _coupled_joint_issues(
    joint_data: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Mirror native RackPinion/Screw slider prerequisites with useful feedback."""

    sliders = [
        (name, data)
        for name, data in joint_data.items()
        if data.get("kind") == "slider" and not bool(data.get("suppressed"))
    ]
    issues: list[dict[str, Any]] = []
    for name, data in joint_data.items():
        kind = str(data.get("kind") or "")
        if kind not in {"rack_pinion", "screw"} or bool(data.get("suppressed")):
            continue
        connectors = list(data.get("connectors") or [])
        compatible_slider = None
        for slider_name, slider_data in sliders:
            slider_connectors = list(slider_data.get("connectors") or [])
            for connector in connectors:
                for slider_connector in slider_connectors:
                    if (
                        connector.get("component_output")
                        == slider_connector.get("component_output")
                        and _parallel_axes(
                            dict(connector.get("local_frame") or {}),
                            dict(slider_connector.get("local_frame") or {}),
                        )
                    ):
                        compatible_slider = slider_name
                        break
                if compatible_slider is not None:
                    break
            if compatible_slider is not None:
                break
        if compatible_slider is None:
            issues.append(
                {
                    "code": "missing_collinear_slider",
                    "joint_output": name,
                    "joint_type": kind,
                    "component_outputs": [
                        str(item.get("component_output") or "")
                        for item in connectors
                    ],
                    "available_slider_outputs": [item[0] for item in sliders],
                    "requirement": (
                        "FreeCAD's native RackPinion and Screw joints require a "
                        "non-suppressed Slider joint sharing one component with a "
                        "collinear local connector +Z axis."
                    ),
                    "suggestion": (
                        "Create and return an api.joint('slider', ...) for the rack "
                        "or screw component, align both connector +Z axes, and include "
                        "both joints in api.assembly."
                    ),
                }
            )
    return issues


def validate_and_solve_assembly(
    document: Any,
    raw_result: Mapping[str, Any],
    outputs: list[dict[str, Any]],
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    """Build, solve, and annotate one exact native assembly candidate."""

    import JointObject
    import UtilsAssembly

    (
        assembly_output,
        assembly_value,
        diagnostics_output,
        diagnostics_value,
        component_outputs,
        joint_outputs,
    ) = _graph_contract(raw_result)
    simulation_contract = _simulation_contract(
        raw_result,
        assembly_value=assembly_value,
        joint_outputs=joint_outputs,
    )
    exploded_view_contract = _exploded_view_contract(
        raw_result,
        assembly_value=assembly_value,
        component_outputs=component_outputs,
    )
    bom_contract = _bom_contract(raw_result, assembly_value=assembly_value)
    mechanism_scenario = _mechanism_scenario_contract(
        assembly_output=assembly_output,
        assembly_value=assembly_value,
        diagnostics_output=diagnostics_output,
        diagnostics_value=diagnostics_value,
        component_outputs=component_outputs,
        joint_outputs=joint_outputs,
        simulation_contract=simulation_contract,
    )
    mechanism_check_contracts = _mechanism_check_contract(
        raw_result,
        assembly_value=assembly_value,
        component_outputs=component_outputs,
        mechanism_scenario=mechanism_scenario,
    )
    assembly_properties = _properties(assembly_value, "assembly")
    component_values = list(assembly_properties.get("components") or [])
    component_values_by_output = {
        component_outputs[id(value)]: value for value in component_values
    }
    joint_values = list(assembly_properties.get("joints") or [])
    bom_sources = _bom_component_sources(component_values, component_outputs)

    assembly = document.addObject("Assembly::AssemblyObject", "CandidateAssembly")
    if assembly is None:
        raise AssemblyCandidateError(
            "This FreeCAD build did not create Assembly::AssemblyObject."
        )
    assembly.Type = "Assembly"
    assembly.Label = str(assembly_properties.get("label") or assembly_output)
    joint_group = assembly.newObject("Assembly::JointGroup", "Joints")

    source_objects: dict[tuple[str, str], Any] = {}
    source_reconstructions: dict[tuple[str, str], dict[str, Any]] = {}
    components: dict[str, Any] = {}
    component_sources: dict[str, dict[str, str]] = {}
    component_source_metadata: dict[str, Mapping[str, Any]] = {}
    component_reconstructions: dict[str, dict[str, Any] | None] = {}
    grounded_outputs: list[str] = []
    pending_grounding: list[tuple[int, str, Any]] = []
    component_data: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(component_values):
        output_name = component_outputs[id(value)]
        if value.operation not in {"component", "fastener"}:
            raise AssemblyCandidateError(
                f"Component output {output_name!r} must come from "
                "api.component or api.fastener."
            )
        properties = _properties(value, value.operation)
        catalog_identity: dict[str, Any] | None = None
        catalog_brep: dict[str, Any] | None = None
        if value.operation == "fastener":
            if artifact_root is None:
                raise AssemblyCandidateError(
                    f"Component output {output_name!r} requires a staged fastener BREP.",
                    details={"stage": "fastener_brep"},
                )
            catalog_identity = _catalog_fastener_identity(
                value,
                context=f"Component output {output_name!r}",
            )
            source_ref = _catalog_fastener_source(catalog_identity)
            source_key = (
                source_ref["document_uid"],
                source_ref["object_name"],
            )
            try:
                from VibeCADFasteners import (
                    assembly_component_contract,
                    create_fastener_feature,
                )

                source = source_objects.get(source_key)
                if source is None:
                    source, observed_identity = create_fastener_feature(
                        document,
                        standard=catalog_identity["standard"],
                        nominal_thread=catalog_identity["nominal_size"],
                        length_mm=catalog_identity["length_mm"],
                        model_thread=bool(catalog_identity["model_thread"]),
                        left_handed=bool(catalog_identity["left_handed"]),
                        options=dict(catalog_identity["options"]),
                        object_name=f"CandidateFastenerSource{index}",
                        label=str(catalog_identity["part_number"]),
                    )
                    if (
                        observed_identity["canonical_key"]
                        != catalog_identity["canonical_key"]
                    ):
                        raise AssemblyCandidateError(
                            f"Component output {output_name!r} generated a different "
                            "catalog identity.",
                            details={"stage": "fastener_generation"},
                        )
                    source_objects[source_key] = source
                metadata = assembly_component_contract(
                    source.Shape,
                    catalog_identity,
                )
                catalog_brep = _catalog_fastener_brep(
                    source.Shape,
                    artifact_root=artifact_root,
                    component_index=index,
                )
            except AssemblyCandidateError:
                raise
            except Exception as exc:
                raise AssemblyCandidateError(
                    f"Component output {output_name!r} could not generate its "
                    f"catalog fastener: {exc}",
                    details={
                        "stage": "fastener_generation",
                        "standard": str(catalog_identity["standard"]),
                        "nominal_thread": str(catalog_identity["nominal_size"]),
                    },
                ) from exc
            source_kind = "standard_fastener"
            flexible = False
            reconstruction = None
            component = assembly.newObject(
                "App::Link",
                f"CandidateComponent{index}",
            )
        else:
            if len(value.arguments) != 1:
                raise AssemblyCandidateError(
                    f"Component output {output_name!r} has a malformed source."
                )
            source_ref = _reference(
                value.arguments[0],
                context=f"component output {output_name!r} source",
            )
            source_key = (
                source_ref["document_uid"],
                source_ref["object_name"],
            )
            metadata = _REFERENCE_METADATA[source_key]
            source_kind = str(metadata.get("source_kind") or "shape")
            flexible = bool(properties.get("flexible", False))
            hierarchy = _REFERENCE_HIERARCHIES.get(source_key)
            reconstruction = None
        grounded = bool(properties.get("grounded"))
        if flexible and source_kind != "assembly":
            raise AssemblyCandidateError(
                f"Component output {output_name!r} requests flexible=True, but source "
                f"{source_ref['object_name']!r} is {source_kind!r}, not a native Assembly.",
                details={
                    "stage": "assembly_hierarchy",
                    "component_output": output_name,
                    "source_kind": source_kind,
                    "correction": (
                        "Remove flexible=True, or reference an Assembly::AssemblyObject "
                        "listed as eligible_flexible_subassembly in Assembly domain context."
                    ),
                },
            )
        if flexible and grounded:
            raise AssemblyCandidateError(
                f"Component output {output_name!r} cannot be both flexible and grounded.",
                details={
                    "stage": "assembly_grounding",
                    "component_output": output_name,
                    "correction": (
                        "Set grounded=False on the flexible subassembly and ground one "
                        "rigid base component in the parent assembly."
                    ),
                },
            )

        if (
            value.operation == "component"
            and source_kind in {"assembly", "part"}
            and hierarchy is not None
        ):
            reconstruction = source_reconstructions.get(source_key)
            if reconstruction is None:
                reconstruction = _reconstruct_assembly_hierarchy(
                    document,
                    source_key,
                    source_index=len(source_reconstructions),
                )
                source_reconstructions[source_key] = reconstruction
                source_objects[source_key] = reconstruction["root"]
            component = assembly.newObject(
                (
                    "Assembly::AssemblyLink"
                    if source_kind == "assembly"
                    else "App::Link"
                ),
                f"CandidateComponent{index}",
            )
            source = reconstruction["root"]
        elif value.operation == "component":
            if flexible:
                raise AssemblyCandidateError(
                    f"Component output {output_name!r} cannot expose source internals "
                    "because its Assembly hierarchy was not authenticated.",
                    details={
                        "stage": "assembly_hierarchy",
                        "component_output": output_name,
                        "source": source_ref,
                        "correction": (
                            "Regenerate the candidate while the referenced live native "
                            "AssemblyObject is available, then keep flexible=True."
                        ),
                    },
                )
            source = source_objects.get(source_key)
            if source is None:
                source = document.addObject("Part::Feature", f"CandidateSource{index}")
                source_identity = {
                    "document_uid": source_ref["document_uid"],
                    "object_name": source_ref["object_name"],
                }
                source.Shape = detached_reference_shape(source_identity)
                source.Label = str(metadata.get("label") or output_name)
                _apply_hierarchy_properties(
                    source,
                    {
                        "node_id": source_ref["object_name"],
                        "identity": source_identity,
                        "bom_properties": list(metadata.get("bom_properties") or []),
                    },
                )
                source_objects[source_key] = source
            component = assembly.newObject(
                "App::Link",
                f"CandidateComponent{index}",
            )
        if component is None:
            raise AssemblyCandidateError(
                f"FreeCAD did not create component output {output_name!r}."
            )
        component.LinkedObject = source
        component.Label = str(properties.get("label") or output_name)
        initial = _native_placement(
            properties.get("placement"),
            context=f"component output {output_name!r} placement",
        )
        component.Placement = initial
        if str(getattr(component, "TypeId", "") or "") == "Assembly::AssemblyLink":
            component.Rigid = not flexible
        components[output_name] = component
        component_sources[output_name] = source_ref
        component_source_metadata[output_name] = metadata
        component_reconstructions[output_name] = reconstruction
        if grounded:
            grounded_outputs.append(output_name)
            pending_grounding.append((index, output_name, component))
        occurrence_paths = (
            [
                str(item["path"])
                for item in list(reconstruction["descriptor"]["occurrence_paths"])
            ]
            if reconstruction is not None
            else []
        )
        component_data[output_name] = {
            "assembly_output": assembly_output,
            "source": source_ref,
            "source_type_id": str(metadata.get("type_id") or ""),
            "source_kind": source_kind,
            "source_facts": dict(metadata.get("facts") or {}),
            "grounded": grounded,
            "flexible": flexible,
            "native_name": str(getattr(component, "Name", "") or ""),
            "native_type_id": str(getattr(component, "TypeId", "") or ""),
            "initial_placement": _placement_fact(initial),
            "hierarchy_sha256": (
                str(reconstruction["hierarchy_sha256"])
                if reconstruction is not None
                else ""
            ),
            "occurrence_path_count": len(occurrence_paths),
            "occurrence_paths": occurrence_paths,
        }
        if catalog_identity is not None and catalog_brep is not None:
            component_data[output_name].update(
                {
                    "catalog_fastener": catalog_identity,
                    "source_contract": dict(metadata),
                    "source_brep": catalog_brep,
                }
            )

    # Native AssemblyLinks synchronize their generated children and internal
    # joints in the worker before any model-authored connector is resolved.
    document.recompute()
    for ground_index, _output_name, component in pending_grounding:
        ground = joint_group.newObject("App::FeaturePython", f"Ground{ground_index}")
        JointObject.GroundedJoint(ground, component)

    diagnostics_properties = _properties(diagnostics_value, "solve")
    require_solved = bool(diagnostics_properties.get("require_solved", True))
    if require_solved and not grounded_outputs:
        raise AssemblyCandidateError(
            "api.solve requires at least one grounded component; create the fixed "
            "base with api.component(..., grounded=True) and reuse that variable "
            "throughout the graph (no_grounded_component, code -6).",
            details={
                "stage": "assembly_grounding",
                "status": "failed",
                "solver_code": -6,
                "solver_verdict": "no_grounded_component",
                "component_count": len(components),
                "grounded_components": [],
            },
        )

    joint_data: dict[str, dict[str, Any]] = {}
    joint_objects: dict[str, Any] = {}
    for index, value in enumerate(joint_values):
        output_name = joint_outputs[id(value)]
        if value.operation != "joint" or len(value.arguments) != 2:
            raise AssemblyCandidateError(
                f"Joint output {output_name!r} must come from api.joint."
            )
        properties = _properties(value, "joint")
        kind = str(properties.get("kind") or "")
        native_type = _JOINT_NATIVE.get(kind)
        if native_type is None or native_type not in list(JointObject.JointTypes):
            raise AssemblyCandidateError(
                f"Joint output {output_name!r} requests unsupported type {kind!r}.",
                details={
                    "stage": "joint_type",
                    "requested_type": kind,
                    "native_supported_types": list(JointObject.JointTypes),
                },
            )
        connectors = [
            _resolve_connector(
                connector,
                component_outputs=component_outputs,
                components=components,
                component_sources=component_sources,
                component_source_metadata=component_source_metadata,
                component_reconstructions=component_reconstructions,
                context=f"joint output {output_name!r} connector {connector_index}",
            )
            for connector_index, connector in enumerate(value.arguments, start=1)
        ]
        if connectors[0]["component_output"] == connectors[1]["component_output"]:
            raise AssemblyCandidateError(
                f"Joint output {output_name!r} connects a component to itself."
            )
        compatibility = _compatibility(kind, connectors)
        if not compatibility["ok"]:
            raise AssemblyCandidateError(
                f"Joint output {output_name!r} has connector geometry incompatible "
                f"with a {kind} joint: {compatibility['criteria']}.",
                details={
                    "stage": "joint_compatibility",
                    "joint_output": output_name,
                    "compatibility": compatibility,
                },
            )
        joint = joint_group.newObject("App::FeaturePython", f"CandidateJoint{index}")
        JointObject.Joint(joint, list(JointObject.JointTypes).index(native_type))
        joint_objects[output_name] = joint
        joint.Label = str(properties.get("label") or output_name)
        _apply_joint_properties(joint, properties)
        joint.Offset1 = connectors[0]["offset"]
        joint.Offset2 = connectors[1]["offset"]
        references = [
            [
                item["component"],
                [item["native_element"], item["native_anchor"]],
            ]
            for item in connectors
        ]
        try:
            joint.Proxy.setJointConnectors(joint, references)
        except Exception as exc:
            raise AssemblyCandidateError(
                f"FreeCAD could not derive connector frames for joint output "
                f"{output_name!r}: {exc}",
                details={
                    "stage": "native_connector_frames",
                    "joint_output": output_name,
                    "joint_type": kind,
                    "references": [
                        {
                            "component_output": item["component_output"],
                            "selection": item["selection"],
                            "element": item["element"],
                            "anchor": item["anchor"],
                            "occurrence_path": item["occurrence_path"],
                            "native_element": item["native_element"],
                            "native_anchor": item["native_anchor"],
                            "geometry_type": item["geometry_type"],
                        }
                        for item in connectors
                    ],
                },
            ) from exc
        if hasattr(joint, "Suppressed"):
            joint.Suppressed = bool(properties.get("suppressed"))
        frames = []
        for connector_index, connector in enumerate(connectors, start=1):
            native_reference = getattr(joint, f"Reference{connector_index}")
            local_frame = getattr(joint, f"Placement{connector_index}")
            try:
                global_frame = UtilsAssembly.getJcsGlobalPlc(
                    local_frame,
                    native_reference,
                )
            except Exception as exc:
                raise AssemblyCandidateError(
                    f"FreeCAD could not resolve the global connector frame for joint "
                    f"output {output_name!r} connector {connector_index}: {exc}",
                    details={
                        "stage": "native_connector_frames",
                        "joint_output": output_name,
                        "connector_index": connector_index,
                    },
                ) from exc
            frames.append(
                {
                    "index": connector_index,
                    "component_output": connector["component_output"],
                    "selection": connector["selection"],
                    "semantic_selection": connector["semantic_selection"],
                    "element": connector["element"],
                    "anchor": connector["anchor"],
                    "occurrence_path": connector["occurrence_path"],
                    "geometry_type": connector["geometry_type"],
                    "native_target_mode": connector["native_target_mode"],
                    "native_hierarchy_chain": connector[
                        "native_hierarchy_chain"
                    ],
                    "native_reference": _native_reference(native_reference),
                    "interface_frame": connector["interface_frame"],
                    "offset": _placement_fact(connector["offset"]),
                    "local_frame": _placement_fact(local_frame),
                    "global_frame": _placement_fact(global_frame),
                }
            )
        joint_data[output_name] = {
            "assembly_output": assembly_output,
            "kind": kind,
            "native_type": native_type,
            "parameters": dict(properties.get("parameters") or {}),
            "length_limits_mm": properties.get("length_limits_mm"),
            "angle_limits_degrees": properties.get("angle_limits_degrees"),
            "suppressed": bool(properties.get("suppressed")),
            "compatibility": compatibility,
            "connectors": frames,
            "native_readback": _joint_readback(joint, kind),
        }

    joint_dependency_issues = _coupled_joint_issues(joint_data)
    if require_solved and joint_dependency_issues:
        first_issue = joint_dependency_issues[0]
        raise AssemblyCandidateError(
            f"Joint output {first_issue['joint_output']!r} is not a functional "
            f"{first_issue['joint_type']} graph: {first_issue['requirement']} "
            f"{first_issue['suggestion']}",
            details={
                "stage": "joint_dependency",
                "status": "failed",
                "issues": joint_dependency_issues,
            },
        )

    def evaluate_native_solve(
        _scenario: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        document.recompute()
        native_solver_code = int(assembly.solve(False))
        document.recompute()
        native = _native_diagnostics(assembly)
        verdict = _SOLVER_VERDICTS.get(
            native_solver_code,
            f"unknown_status_{native_solver_code}",
        )
        placements = {
            name: _placement_fact(component.Placement)
            for name, component in components.items()
        }
        occurrences = {
            name: (
                _component_hierarchy_state(
                    components[name],
                    reconstruction,
                    component_output=name,
                )
                if reconstruction is not None
                else []
            )
            for name, reconstruction in component_reconstructions.items()
        }
        status = (
            "solved"
            if native_solver_code == 0
            and not _diagnostics_conflict(native)
            and not joint_dependency_issues
            else "failed"
        )
        return {
            "schema": MECHANISM_SOLVE_REPORT_SCHEMA,
            "scenario_sha256": mechanism_scenario_sha256(
                _scenario
            ),
            "status": status,
            "solver_code": native_solver_code,
            "solver_verdict": verdict,
            "require_solved": require_solved,
            "component_count": len(components),
            "joint_count": len(joint_values),
            "grounded_components": grounded_outputs,
            "native_diagnostics": native,
            "component_placements": placements,
            "component_occurrences": occurrences,
            "joint_dependency_issues": joint_dependency_issues,
        }

    solve_report = evaluate_mechanism_scenario(
        mechanism_scenario,
        evaluate_native_solve,
    )
    solver_code = int(solve_report["solver_code"])
    solver_verdict = str(solve_report["solver_verdict"])
    native_diagnostics = dict(solve_report["native_diagnostics"])
    component_placements = dict(solve_report["component_placements"])
    component_occurrence_states = {
        name: list(solve_report["component_occurrences"][name])
        for name, reconstruction in component_reconstructions.items()
        if reconstruction is not None
    }
    diagnostics = {
        "status": solve_report["status"],
        "solver_code": solver_code,
        "solver_verdict": solver_verdict,
        "native": native_diagnostics,
        "component_count": len(components),
        "joint_count": len(joint_values),
        "grounded_components": grounded_outputs,
        "component_placements": component_placements,
        "component_occurrence_counts": {
            name: len(items) for name, items in component_occurrence_states.items()
        },
        "joint_outputs": list(joint_data),
        "joint_dependency_issues": joint_dependency_issues,
        "require_solved": require_solved,
    }
    if require_solved and (
        solver_code != 0 or _diagnostics_conflict(native_diagnostics)
    ):
        raise AssemblyCandidateError(
            f"The isolated native Assembly solver rejected the graph with "
            f"{solver_verdict} (code {solver_code}). Inspect details for conflicting, "
            "redundant, malformed, or ungrounded constraints.",
            details={"stage": "native_solver", **diagnostics},
        )

    by_name = {str(item.get("name") or ""): item for item in outputs}
    verification_summaries: list[dict[str, Any]] = []
    for verification_output, _verification_value, check in (
        mechanism_check_contracts
    ):
        geometry_evidence = _static_mechanism_geometry(
            check,
            components=components,
            component_values=component_values_by_output,
            component_source_metadata=component_source_metadata,
        )
        verification_report = evaluate_static_mechanism_check(
            mechanism_scenario,
            solve_report,
            check,
            geometry_evidence,
        )
        by_name[verification_output]["assembly_data"] = {
            "assembly_output": assembly_output,
            "static_check": check,
            "report": verification_report,
        }
        summary = {
            "verification_output": verification_output,
            "verdict": str(verification_report["verdict"]),
            "declaration_count": int(
                verification_report["summary"]["declaration_count"]
            ),
            "pass_count": int(verification_report["summary"]["pass_count"]),
            "fail_count": int(verification_report["summary"]["fail_count"]),
            "indeterminate_count": int(
                verification_report["summary"]["indeterminate_count"]
            ),
            "ignored_count": int(
                verification_report["summary"]["ignored_count"]
            ),
            "static_check_sha256": str(
                verification_report["static_check_sha256"]
            ),
        }
        verification_summaries.append(summary)
    if verification_summaries:
        diagnostics["verifications"] = verification_summaries
    simulation_summary = None
    if simulation_contract is not None:
        simulation_output, simulation_value, motion_outputs = simulation_contract
        if diagnostics["status"] != "solved":
            raise AssemblyCandidateError(
                f"Simulation output {simulation_output!r} requires a clean solved "
                "assembly graph even when api.solve(..., require_solved=False).",
                details={
                    "stage": "simulation_precondition",
                    "simulation_output": simulation_output,
                    "solver_code": solver_code,
                    "solver_verdict": solver_verdict,
                    "correction": (
                        "Repair the reported joint or grounding failure, obtain "
                        "Diagnostics status='solved', then run api.simulation."
                    ),
                },
            )
        if artifact_root is None:
            raise AssemblyCandidateError(
                "Assembly simulation requires the isolated candidate artifact root.",
                details={
                    "stage": "simulation_trace",
                    "simulation_output": simulation_output,
                },
            )
        simulation_summary = _execute_native_simulation(
            document=document,
            assembly=assembly,
            assembly_output=assembly_output,
            simulation_output=simulation_output,
            simulation_value=simulation_value,
            motion_outputs=motion_outputs,
            joint_outputs=joint_outputs,
            joint_objects=joint_objects,
            joint_data=joint_data,
            components=components,
            artifact_root=artifact_root,
            outputs_by_name=by_name,
        )
        diagnostics["simulation"] = simulation_summary
    exploded_view_summaries: list[dict[str, Any]] = []
    for exploded_view_output, exploded_view_value in exploded_view_contract:
        if diagnostics["status"] != "solved":
            raise AssemblyCandidateError(
                f"Exploded-view output {exploded_view_output!r} requires a clean "
                "solved assembly graph even when "
                "api.solve(..., require_solved=False).",
                details={
                    "stage": "exploded_view_precondition",
                    "exploded_view_output": exploded_view_output,
                    "solver_code": solver_code,
                    "solver_verdict": solver_verdict,
                    "correction": (
                        "Repair the reported joint or grounding failure, obtain "
                        "Diagnostics status='solved', then create the exploded view."
                    ),
                },
            )
        exploded_view_data = _execute_native_exploded_view(
            document=document,
            assembly=assembly,
            assembly_output=assembly_output,
            output_name=exploded_view_output,
            value=exploded_view_value,
            component_outputs=component_outputs,
            components=components,
        )
        by_name[exploded_view_output]["assembly_data"] = exploded_view_data
        moved_components = list(
            dict.fromkeys(
                component_name
                for move in exploded_view_data["moves"]
                for component_name in move["component_outputs"]
            )
        )
        exploded_view_summaries.append(
            {
                "exploded_view_output": exploded_view_output,
                "move_count": len(exploded_view_data["moves"]),
                "component_reference_count": sum(
                    len(move["component_outputs"])
                    for move in exploded_view_data["moves"]
                ),
                "moved_component_outputs": moved_components,
                "line_count": int(exploded_view_data["line_count"]),
                "assembly_bounds": dict(exploded_view_data["assembly_bounds"]),
            }
        )
    if exploded_view_summaries:
        diagnostics["exploded_views"] = exploded_view_summaries
    bom_summaries: list[dict[str, Any]] = []
    if bom_contract:
        from VibeCADAssemblyBOM import bom_summary

        for bom_index, (bom_output, bom_value) in enumerate(bom_contract):
            bom_data = _execute_native_bom(
                document=document,
                assembly=assembly,
                assembly_output=assembly_output,
                output_name=bom_output,
                value=bom_value,
                component_sources=bom_sources,
                bom_index=bom_index,
            )
            by_name[bom_output]["assembly_data"] = bom_data
            bom_summaries.append(bom_summary(bom_data, output_name=bom_output))
        diagnostics["boms"] = bom_summaries
    by_name[assembly_output]["assembly_data"] = {
        "component_outputs": [component_outputs[id(value)] for value in component_values],
        "joint_outputs": [joint_outputs[id(value)] for value in joint_values],
        "diagnostics_output": diagnostics_output,
    }
    if simulation_summary is not None:
        by_name[assembly_output]["assembly_data"].update(
            {
                "motion_outputs": list(simulation_summary["motion_outputs"]),
                "simulation_output": str(simulation_summary["simulation_output"]),
            }
        )
    if exploded_view_summaries:
        by_name[assembly_output]["assembly_data"]["exploded_view_outputs"] = [
            item["exploded_view_output"] for item in exploded_view_summaries
        ]
    if bom_summaries:
        by_name[assembly_output]["assembly_data"]["bom_outputs"] = [
            item["bom_output"] for item in bom_summaries
        ]
    if verification_summaries:
        by_name[assembly_output]["assembly_data"][
            "mechanism_verification_outputs"
        ] = [
            item["verification_output"] for item in verification_summaries
        ]
    for output_name, data in component_data.items():
        data["solved_placement"] = component_placements[output_name]
        if output_name in component_occurrence_states:
            data["solved_occurrences"] = component_occurrence_states[output_name]
        by_name[output_name]["assembly_data"] = data
        by_name[output_name]["solved_placement_matrix"] = component_placements[
            output_name
        ]["matrix"]
    for output_name, data in joint_data.items():
        by_name[output_name]["assembly_data"] = data
        by_name[output_name]["connector_frames"] = list(data["connectors"])
    by_name[diagnostics_output]["diagnostics"] = diagnostics
    by_name[diagnostics_output]["assembly_data"] = {
        "assembly_output": assembly_output
    }
    result = {
        "status": solve_report["status"],
        "solver_code": solve_report["solver_code"],
        "solver_verdict": solve_report["solver_verdict"],
        "component_count": solve_report["component_count"],
        "joint_count": solve_report["joint_count"],
        "grounded_components": solve_report["grounded_components"],
        "native_diagnostics": solve_report["native_diagnostics"],
        "component_placements": solve_report["component_placements"],
        "component_occurrence_counts": {
            name: len(items) for name, items in component_occurrence_states.items()
        },
        "joint_dependency_issues": solve_report["joint_dependency_issues"],
    }
    if simulation_summary is not None:
        result["simulation"] = simulation_summary
    if exploded_view_summaries:
        result["exploded_views"] = exploded_view_summaries
    if bom_summaries:
        result["boms"] = bom_summaries
    if verification_summaries:
        result["verifications"] = verification_summaries
    return result


def encoded_diagnostics(value: Mapping[str, Any]) -> str:
    """Return a bounded stable representation for worker error messages/tests."""

    return json.dumps(
        _json_safe(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
