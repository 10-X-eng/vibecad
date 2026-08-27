# SPDX-License-Identifier: LGPL-2.1-or-later

"""Managed semantic references to regenerating scripted CAD outputs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping

import VibeCADScriptedPublication as publication


CONTRACT_SCHEMA = "vibecad-reference-contract-v1"
INTERFACE_TABLE_SCHEMA = "vibecad-published-interfaces-v2"
INTERFACE_TABLE_SCHEMA_KEY = "_schema"
INTERFACE_TABLE_OUTPUTS_KEY = "_outputs"
PROP_CONTRACT = "VibeCADReferenceContract"
PROP_DERIVED_STATE = "VibeCADDerivedState"
PROP_STALE_REASON = "VibeCADStaleReason"
PROP_SOURCE_REVISION = "VibeCADSourceRevision"
PROP_NATIVE_INTERFACE = "VibeCADComponentInterface"
PROP_NATIVE_INTERFACE_NAME = "VibeCADInterfaceName"
PROP_NATIVE_INTERFACE_KIND = "VibeCADInterfaceKind"
PROP_NATIVE_INTERFACE_ALLOWED_JOINTS = "VibeCADInterfaceAllowedJoints"
PROP_NATIVE_INTERFACE_COMPATIBILITY = "VibeCADInterfaceCompatibility"
PROP_NATIVE_INTERFACE_FIT = "VibeCADInterfaceFit"
PROP_NATIVE_INTERFACE_JOINT_PARAMETERS = "VibeCADInterfaceJointParameters"
PROP_NATIVE_INTERFACE_GEOMETRY = "VibeCADInterfaceGeometryBinding"
INTERFACE_GEOMETRY_SCHEMA = "vibecad-interface-geometry-binding-v1"
INTERFACE_FIT_SCHEMA = "vibecad-interface-fit-v1"
INTERFACE_JOINT_PARAMETERS_SCHEMA = "vibecad-interface-joint-parameters-v1"
INTERFACE_FIT_CLASSES = frozenset({
    "bearing", "clearance", "custom", "interference", "threaded", "transition",
})
NATIVE_INTERFACE_KINDS = frozenset({
    "axis",
    "bearing_face",
    "bearing_seat",
    "bolt_pattern",
    "bore",
    "electrical_connector",
    "fixture",
    "fluid_port",
    "frame",
    "mounting_pattern",
    "plane",
    "planar_mate",
    "point",
    "shaft",
    "shaft_seat",
    "thread",
    "thread_axis",
    "tool",
})


class ReferenceContractError(RuntimeError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        self.details = dict(details or {})
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class NativeInterfaceSpec:
    name: str
    kind: str
    allowed_joints: tuple[str, ...]
    compatibility: str
    fit: dict[str, Any] | None = None
    joint_parameters: dict[str, Any] | None = None


def normalize_interface_joint_parameters(value: Any) -> dict[str, Any] | None:
    """Normalize only explicit relation values that planning may carry forward."""

    if value in (None, {}):
        return None
    if (
        not isinstance(value, Mapping)
        or set(value) != {"schema", "values"}
        or value.get("schema") != INTERFACE_JOINT_PARAMETERS_SCHEMA
        or not isinstance(value.get("values"), Mapping)
    ):
        raise ReferenceContractError("Interface joint parameters use an unsupported contract.")
    normalized: dict[str, dict[str, float]] = {}
    contracts = {
        "distance": ("distance_mm", -1_000_000.0, 1_000_000.0),
        "angle": ("angle_degrees", -360.0, 360.0),
    }
    for kind, raw in value["values"].items():
        contract = contracts.get(str(kind))
        if contract is None or not isinstance(raw, Mapping):
            raise ReferenceContractError("Interface joint parameters contain an unsupported relation.")
        field, minimum, maximum = contract
        if set(raw) != {field}:
            raise ReferenceContractError(f"Interface {kind} parameters must contain only {field}.")
        number = raw[field]
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ReferenceContractError(f"Interface {field} must be a finite number.")
        number = float(number)
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ReferenceContractError(f"Interface {field} is outside the supported range.")
        normalized[str(kind)] = {field: number}
    if not normalized:
        raise ReferenceContractError("Interface joint parameters cannot be empty.")
    return {
        "schema": INTERFACE_JOINT_PARAMETERS_SCHEMA,
        "values": {kind: normalized[kind] for kind in sorted(normalized)},
    }


def normalize_interface_fit(value: Any) -> dict[str, Any] | None:
    """Normalize explicit fit semantics; never infer fit from geometry."""

    if value in (None, {}):
        return None
    if not isinstance(value, Mapping):
        raise ReferenceContractError("Interface fit must be an object.")
    allowed = {
        "schema", "fit_class", "designation",
        "minimum_clearance_mm", "maximum_clearance_mm",
    }
    if set(value) - allowed or value.get("schema") != INTERFACE_FIT_SCHEMA:
        raise ReferenceContractError("Interface fit uses an unsupported contract.")
    fit_class = str(value.get("fit_class") or "").strip().lower()
    if fit_class not in INTERFACE_FIT_CLASSES:
        raise ReferenceContractError(
            f"Interface fit_class must be one of {sorted(INTERFACE_FIT_CLASSES)}."
        )
    result: dict[str, Any] = {
        "schema": INTERFACE_FIT_SCHEMA,
        "fit_class": fit_class,
    }
    designation = str(value.get("designation") or "").strip()
    if designation:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ./_:+-]{0,95}", designation):
            raise ReferenceContractError("Interface fit designation is invalid.")
        result["designation"] = designation
    bounds = []
    for field in ("minimum_clearance_mm", "maximum_clearance_mm"):
        if field not in value:
            continue
        number = value[field]
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ReferenceContractError(f"Interface {field} must be a finite number.")
        number = float(number)
        if not math.isfinite(number):
            raise ReferenceContractError(f"Interface {field} must be a finite number.")
        result[field] = number
        bounds.append(field)
    if len(bounds) == 1:
        raise ReferenceContractError("Interface fit clearance bounds must be supplied together.")
    if len(bounds) == 2 and result["minimum_clearance_mm"] > result["maximum_clearance_mm"]:
        raise ReferenceContractError("Interface fit clearance bounds are reversed.")
    return result


def _native_support_entries(lcs: Any) -> list[tuple[Any, list[str]]]:
    raw = getattr(lcs, "AttachmentSupport", None)
    if not raw:
        raw = getattr(lcs, "Support", None)
    if not raw:
        return []
    if isinstance(raw, tuple) and len(raw) == 2 and hasattr(raw[0], "Name"):
        raw = [raw]
    entries: list[tuple[Any, list[str]]] = []
    for item in list(raw)[:8]:
        if not isinstance(item, (tuple, list)) or len(item) != 2 or not hasattr(item[0], "Name"):
            continue
        subelements = item[1]
        if isinstance(subelements, str):
            subelements = [subelements]
        entries.append((item[0], sorted(str(value) for value in list(subelements)[:32] if str(value))))
    return entries


def capture_native_interface_geometry(lcs: Any) -> dict[str, Any]:
    """Capture conservative LCS support evidence for later invalidation checks."""

    supports = []
    status = "current"
    for source, subelements in _native_support_entries(lcs):
        shape = getattr(source, "Shape", None)
        export = getattr(shape, "exportBrepToString", None)
        shape_hash = ""
        if callable(export):
            try:
                shape_hash = hashlib.sha256(str(export()).encode("utf-8")).hexdigest()
            except Exception:
                status = "indeterminate"
        else:
            status = "indeterminate"
        supports.append({
            "object_name": str(source.Name),
            "subelements": subelements,
            **({"shape_sha256": shape_hash} if shape_hash else {}),
        })
    if not supports:
        status = "unbound"
    snapshot = {
        "schema": INTERFACE_GEOMETRY_SCHEMA,
        "map_mode": str(getattr(lcs, "MapMode", "") or ""),
        "supports": supports,
    }
    return {**snapshot, "binding_sha256": _interface_geometry_hash(snapshot), "status": status}


def _interface_geometry_hash(value: Mapping[str, Any]) -> str:
    payload = {key: value[key] for key in ("schema", "map_mode", "supports") if key in value}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def native_interface_geometry_currentness(lcs: Any) -> dict[str, Any]:
    """Compare persisted support evidence with the current LCS support graph."""

    raw = str(getattr(lcs, PROP_NATIVE_INTERFACE_GEOMETRY, "") or "").strip()
    if not raw:
        return {"schema": INTERFACE_GEOMETRY_SCHEMA, "status": "unrecorded"}
    try:
        recorded = json.loads(raw)
    except ValueError:
        return {"schema": INTERFACE_GEOMETRY_SCHEMA, "status": "invalid"}
    if not isinstance(recorded, Mapping) or recorded.get("schema") != INTERFACE_GEOMETRY_SCHEMA:
        return {"schema": INTERFACE_GEOMETRY_SCHEMA, "status": "invalid"}
    live = capture_native_interface_geometry(lcs)
    recorded_hash = str(recorded.get("binding_sha256") or "")
    if recorded.get("status") == "unbound" and live["status"] == "unbound":
        status = "unbound"
    elif live["status"] == "indeterminate":
        status = "indeterminate"
    else:
        status = "current" if recorded_hash == live["binding_sha256"] else "stale"
    return {
        "schema": INTERFACE_GEOMETRY_SCHEMA,
        "status": status,
        "recorded_binding_sha256": recorded_hash,
        "live_binding_sha256": live["binding_sha256"],
        "map_mode": live["map_mode"],
        "supports": live["supports"],
    }


def is_native_coordinate_system(obj: Any) -> bool:
    """Return whether *obj* is one shipped native coordinate-system type."""

    try:
        return bool(obj.isDerivedFrom("App::LocalCoordinateSystem")) or str(
            getattr(obj, "TypeId", "") or ""
        ) in {
            "PartDesign::CoordinateSystem",
            "Part::LocalCoordinateSystem",
        }
    except Exception:
        return False


def _direct_component_resources(component: Any) -> list[Any]:
    resources: list[Any] = []
    for candidate in list(getattr(component, "Group", []) or []):
        if candidate is not None and candidate not in resources:
            resources.append(candidate)
    return resources


def native_interface_definitions(component: Any) -> dict[str, dict[str, Any]]:
    """Read explicitly tagged native LCS children; never inspect shape geometry."""

    definitions: dict[str, dict[str, Any]] = {}
    for lcs in _direct_component_resources(component):
        properties = set(getattr(lcs, "PropertiesList", []) or [])
        if (
            not is_native_coordinate_system(lcs)
            or PROP_NATIVE_INTERFACE not in properties
            or not bool(getattr(lcs, PROP_NATIVE_INTERFACE, False))
        ):
            continue
        name = str(getattr(lcs, PROP_NATIVE_INTERFACE_NAME, "") or "").strip()
        kind = str(getattr(lcs, PROP_NATIVE_INTERFACE_KIND, "") or "").strip()
        if not name or kind not in NATIVE_INTERFACE_KINDS or name in definitions:
            continue
        try:
            allowed = json.loads(
                str(getattr(lcs, PROP_NATIVE_INTERFACE_ALLOWED_JOINTS, "[]") or "[]")
            )
        except ValueError:
            continue
        if not isinstance(allowed, list) or any(
            not isinstance(value, str) for value in allowed
        ):
            continue
        compatibility = str(
            getattr(lcs, PROP_NATIVE_INTERFACE_COMPATIBILITY, "") or ""
        ).strip()
        fit = None
        raw_fit = str(getattr(lcs, PROP_NATIVE_INTERFACE_FIT, "") or "").strip()
        if raw_fit:
            try:
                fit = normalize_interface_fit(json.loads(raw_fit))
            except (ValueError, ReferenceContractError):
                continue
        joint_parameters = None
        raw_parameters = str(
            getattr(lcs, PROP_NATIVE_INTERFACE_JOINT_PARAMETERS, "") or ""
        ).strip()
        if raw_parameters:
            try:
                joint_parameters = normalize_interface_joint_parameters(
                    json.loads(raw_parameters)
                )
            except (ValueError, ReferenceContractError):
                continue
        connector = {
            "kind": kind,
            **({"allowed_joints": list(allowed)} if allowed else {}),
            **({"compatibility": compatibility} if compatibility else {}),
            **({"fit": fit} if fit is not None else {}),
            **(
                {"joint_parameters": joint_parameters}
                if joint_parameters is not None else {}
            ),
        }
        definitions[name] = {
            "selection": {
                "type": "frame",
                "native_lcs": str(getattr(lcs, "Name", "") or ""),
            },
            "connector": connector,
            "resolved": {
                "object": str(getattr(component, "Name", "") or ""),
                "subelements": [],
                "geometry": [],
                "connector_frame": _placement_frame(lcs.Placement),
                "geometry_binding": native_interface_geometry_currentness(lcs),
            },
        }
    return definitions


def prepare_native_interface(
    component: Any,
    lcs: Any,
    *,
    name: str,
    kind: str,
    allowed_joints: list[str] | tuple[str, ...] = (),
    compatibility: str = "",
    fit: Mapping[str, Any] | None = None,
    joint_parameters: Mapping[str, Any] | None = None,
) -> NativeInterfaceSpec:
    """Validate and normalize one exact native component-interface request."""

    from vibescript_assembly_api import JOINT_TYPES

    clean_name = str(name or "").strip()
    clean_kind = str(kind or "").strip().lower()
    clean_joints = [str(value or "").strip().lower() for value in allowed_joints]
    clean_compatibility = str(compatibility or "").strip()
    clean_fit = normalize_interface_fit(fit)
    clean_joint_parameters = normalize_interface_joint_parameters(joint_parameters)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", clean_name):
        raise ReferenceContractError("Interface name is not a stable identifier.")
    if clean_kind not in NATIVE_INTERFACE_KINDS:
        raise ReferenceContractError(
            f"Interface kind must be one of {sorted(NATIVE_INTERFACE_KINDS)}."
        )
    if len(clean_joints) != len(set(clean_joints)) or any(
        value not in JOINT_TYPES for value in clean_joints
    ):
        raise ReferenceContractError(
            f"Allowed joints must be unique values from {list(JOINT_TYPES)}."
        )
    if clean_joint_parameters is not None and any(
        kind not in clean_joints for kind in clean_joint_parameters["values"]
    ):
        raise ReferenceContractError(
            "Interface joint parameters must target an explicitly allowed joint."
        )
    if clean_compatibility and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", clean_compatibility
    ):
        raise ReferenceContractError("Interface compatibility is not a stable token.")
    if getattr(component, "Document", None) is not getattr(lcs, "Document", None):
        raise ReferenceContractError("The component and LCS belong to different documents.")
    if not is_native_coordinate_system(lcs):
        raise ReferenceContractError("The selected interface is not a native LCS.")
    if lcs not in _direct_component_resources(component):
        raise ReferenceContractError(
            "The selected LCS is not a direct resource of the selected component."
        )
    for existing_name, definition in native_interface_definitions(component).items():
        native_name = str(
            dict(definition.get("selection") or {}).get("native_lcs") or ""
        )
        if existing_name == clean_name and native_name != str(lcs.Name):
            raise ReferenceContractError(
                f"Component {component.Name!r} already publishes interface {clean_name!r}."
            )
    return NativeInterfaceSpec(
        clean_name,
        clean_kind,
        tuple(clean_joints),
        clean_compatibility,
        clean_fit,
        clean_joint_parameters,
    )


def publish_native_interface(
    component: Any,
    lcs: Any,
    *,
    name: str,
    kind: str,
    allowed_joints: list[str] | tuple[str, ...] = (),
    compatibility: str = "",
    fit: Mapping[str, Any] | None = None,
    joint_parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish one exact existing LCS as a reusable component connector."""

    spec = prepare_native_interface(
        component,
        lcs,
        name=name,
        kind=kind,
        allowed_joints=allowed_joints,
        compatibility=compatibility,
        fit=fit,
        joint_parameters=joint_parameters,
    )
    for property_type, property_name, description in (
        ("App::PropertyBool", PROP_NATIVE_INTERFACE, "Marks this LCS as a component interface."),
        ("App::PropertyString", PROP_NATIVE_INTERFACE_NAME, "Stable component-local interface name."),
        ("App::PropertyString", PROP_NATIVE_INTERFACE_KIND, "Explicit connector kind."),
        ("App::PropertyString", PROP_NATIVE_INTERFACE_ALLOWED_JOINTS, "JSON list of allowed Assembly joint kinds."),
        ("App::PropertyString", PROP_NATIVE_INTERFACE_COMPATIBILITY, "Exact connector compatibility token."),
        ("App::PropertyString", PROP_NATIVE_INTERFACE_FIT, "Versioned explicit engineering fit JSON."),
        ("App::PropertyString", PROP_NATIVE_INTERFACE_JOINT_PARAMETERS, "Versioned explicit relation-inference parameters JSON."),
        ("App::PropertyString", PROP_NATIVE_INTERFACE_GEOMETRY, "Canonical LCS support binding and shape evidence."),
    ):
        if property_name not in set(getattr(lcs, "PropertiesList", []) or []):
            lcs.addProperty(
                property_type,
                property_name,
                "VibeCAD Interface",
                description,
            )
    setattr(lcs, PROP_NATIVE_INTERFACE, True)
    setattr(lcs, PROP_NATIVE_INTERFACE_NAME, spec.name)
    setattr(lcs, PROP_NATIVE_INTERFACE_KIND, spec.kind)
    setattr(
        lcs,
        PROP_NATIVE_INTERFACE_ALLOWED_JOINTS,
        json.dumps(spec.allowed_joints, ensure_ascii=True, separators=(",", ":")),
    )
    setattr(lcs, PROP_NATIVE_INTERFACE_COMPATIBILITY, spec.compatibility)
    setattr(
        lcs,
        PROP_NATIVE_INTERFACE_FIT,
        "" if spec.fit is None else json.dumps(spec.fit, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
    )
    setattr(
        lcs,
        PROP_NATIVE_INTERFACE_JOINT_PARAMETERS,
        "" if spec.joint_parameters is None else json.dumps(
            spec.joint_parameters,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    geometry_binding = capture_native_interface_geometry(lcs)
    setattr(
        lcs,
        PROP_NATIVE_INTERFACE_GEOMETRY,
        json.dumps(geometry_binding, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
    )
    return native_interface_definitions(component)[spec.name]


def interface_definitions_for_output(
    interfaces: Any,
    output_key: str,
) -> dict[str, dict[str, Any]]:
    """Return one output's local interface namespace from either table format."""

    if not isinstance(interfaces, dict):
        return {}
    if interfaces.get(INTERFACE_TABLE_SCHEMA_KEY) == INTERFACE_TABLE_SCHEMA:
        outputs = interfaces.get(INTERFACE_TABLE_OUTPUTS_KEY)
        if not isinstance(outputs, dict):
            return {}
        definitions = outputs.get(str(output_key or ""))
        if not isinstance(definitions, dict):
            return {}
        return {
            str(name): dict(definition)
            for name, definition in definitions.items()
            if isinstance(definition, dict)
        }
    return {
        str(name): dict(definition)
        for name, definition in interfaces.items()
        if isinstance(definition, dict)
        and str(definition.get("output") or "") == str(output_key or "")
    }


def interface_identities(interfaces: Any) -> set[tuple[str, str]]:
    """Return stable ``(output key, local interface name)`` identities."""

    if not isinstance(interfaces, dict):
        return set()
    if interfaces.get(INTERFACE_TABLE_SCHEMA_KEY) == INTERFACE_TABLE_SCHEMA:
        outputs = interfaces.get(INTERFACE_TABLE_OUTPUTS_KEY)
        if not isinstance(outputs, dict):
            return set()
        return {
            (str(output_key), str(name))
            for output_key, definitions in outputs.items()
            if isinstance(definitions, dict)
            for name, definition in definitions.items()
            if isinstance(definition, dict)
        }
    return {
        (str(definition.get("output") or ""), str(name))
        for name, definition in interfaces.items()
        if isinstance(definition, dict)
        and str(definition.get("output") or "")
    }


def _placement_frame(placement: Any) -> dict[str, Any]:
    matrix = placement.toMatrix()
    values = [
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
    return {
        "schema": "vibecad-connector-frame-v1",
        "origin_mm": [values[3], values[7], values[11]],
        "x_direction": [values[0], values[4], values[8]],
        "axis_direction": [values[2], values[6], values[10]],
        "matrix": values,
    }


def _native_connector_frame(published: Any, subelements: list[str]) -> dict[str, Any] | None:
    """Derive a missing legacy frame with native Assembly JCS semantics."""

    if len(subelements) > 1:
        return None
    if not subelements:
        import FreeCAD as App

        return _placement_frame(App.Placement())
    import UtilsAssembly

    element = str(subelements[0])
    return _placement_frame(
        UtilsAssembly.findPlacement([published, [element, element]])
    )


def connector_frame_placement(value: Any) -> Any:
    """Reconstruct one validated local connector placement from its matrix."""

    if not isinstance(value, dict) or value.get("schema") != "vibecad-connector-frame-v1":
        raise ReferenceContractError(
            "A semantic connector frame has an unsupported contract.",
            details={"connector_frame": value},
        )
    values = value.get("matrix")
    if (
        not isinstance(values, list)
        or len(values) != 16
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            for item in values
        )
    ):
        raise ReferenceContractError(
            "A semantic connector frame has an invalid matrix.",
            details={"connector_frame": value},
        )
    import FreeCAD as App

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
        setattr(matrix, name, float(number))
    return App.Placement(matrix)


def published_interface_descriptors(
    interfaces: Any,
    output_key: str,
) -> list[dict[str, Any]]:
    """Return compact, assembly-ready interface metadata for one output."""

    descriptors: list[dict[str, Any]] = []
    definitions = interface_definitions_for_output(interfaces, output_key)
    for raw_name, raw_definition in sorted(definitions.items()):
        selection = raw_definition.get("selection")
        resolved = raw_definition.get("resolved")
        if not isinstance(selection, dict) or not isinstance(resolved, dict):
            continue
        subelements = [str(value) for value in list(resolved.get("subelements") or [])]
        geometry = [
            dict(value)
            for value in list(resolved.get("geometry") or [])
            if isinstance(value, dict)
        ]
        frame = resolved.get("connector_frame")
        selection_type = str(selection.get("type") or "")
        descriptor = {
            "name": str(raw_name),
            "selection_type": selection_type,
            "subelements": subelements,
            "connector_eligible": (
                (selection_type in {"origin", "frame"} and not subelements)
                or len(subelements) == 1
            ),
        }
        if raw_definition.get("description"):
            descriptor["description"] = str(raw_definition["description"])
        connector = raw_definition.get("connector")
        if isinstance(connector, dict):
            descriptor["connector"] = dict(connector)
        if len(geometry) == 1 and geometry[0].get("geometry_type"):
            descriptor["geometry_type"] = str(geometry[0]["geometry_type"])
        elif not subelements:
            descriptor["geometry_type"] = (
                "component_frame"
                if selection_type == "frame"
                else "component_origin"
            )
        if isinstance(frame, dict):
            descriptor["frame"] = dict(frame)
        descriptors.append(descriptor)
    return descriptors


def component_interface_descriptors(
    source: Any,
) -> tuple[bool, list[dict[str, Any]]]:
    """Read the explicit connector interfaces carried by one component."""

    published = published_object(source)
    if published is not None:
        try:
            root = publication.model_root_for(published)
            table = json.loads(
                str(getattr(root, publication.PROP_INTERFACES, "{}") or "{}")
            )
        except (publication.PublicationError, ValueError) as exc:
            raise ReferenceContractError(
                f"Component {getattr(source, 'Name', '')!r} has invalid interfaces.",
                details={"native_error": str(exc)},
            ) from exc
        output_key = str(
            getattr(published, publication.PROP_OUTPUT_KEY, "") or ""
        )
        return True, published_interface_descriptors(table, output_key)

    native = native_interface_definitions(source)
    descriptors: list[dict[str, Any]] = []
    for name, definition in sorted(native.items()):
        resolved = dict(definition.get("resolved") or {})
        selection = dict(definition.get("selection") or {})
        geometry = list(resolved.get("geometry") or [])
        descriptor: dict[str, Any] = {
            "name": str(name),
            "selection_type": str(selection.get("type") or "frame"),
            "connector_eligible": True,
            "connector": dict(definition.get("connector") or {}),
        }
        if geometry and isinstance(geometry[0], Mapping):
            descriptor["geometry_type"] = str(
                geometry[0].get("geometry_type") or ""
            )
        frame = resolved.get("connector_frame")
        if isinstance(frame, Mapping):
            descriptor["frame"] = dict(frame)
        geometry_binding = resolved.get("geometry_binding")
        if isinstance(geometry_binding, Mapping):
            descriptor["geometry_binding"] = dict(geometry_binding)
        descriptors.append(descriptor)
    return bool(native), descriptors


def connector_interface_record(
    descriptor: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Normalize one published interface for Assembly connector ranking."""

    if not descriptor.get("connector_eligible"):
        return None
    name = str(descriptor.get("name") or "")
    if not name:
        return None
    connector = dict(descriptor.get("connector") or {})
    kind = str(connector.get("kind") or "")
    geometry = str(descriptor.get("geometry_type") or "").lower()
    if not geometry or geometry in {"component_frame", "component_origin"}:
        geometry = {
            "axis": "cylinder",
            "bearing_face": "plane",
            "bearing_seat": "cylinder",
            "bolt_pattern": "component_origin",
            "bore": "cylinder",
            "electrical_connector": "component_origin",
            "fixture": "component_origin",
            "fluid_port": "component_origin",
            "plane": "plane",
            "planar_mate": "plane",
            "point": "vertex",
            "frame": "component_origin",
            "mounting_pattern": "component_origin",
            "shaft": "cylinder",
            "shaft_seat": "cylinder",
            "thread": "cylinder",
            "thread_axis": "cylinder",
            "tool": "component_origin",
        }.get(kind, "component_origin")
    frame = descriptor.get("frame")
    origin = [0.0, 0.0, 0.0]
    axis = [0.0, 0.0, 1.0]
    if isinstance(frame, Mapping):
        raw_origin = frame.get("origin_mm")
        raw_axis = frame.get("axis_direction")
        if isinstance(raw_origin, list) and len(raw_origin) == 3:
            origin = [float(value) for value in raw_origin]
        if isinstance(raw_axis, list) and len(raw_axis) == 3:
            axis = [float(value) for value in raw_axis]
    record = {
        "selection": {
            "type": "published_interface",
            "interface_name": name,
        },
        "contract": connector or None,
        "element": name,
        "geometry": geometry,
        "origin_mm": origin,
        "axis": axis,
    }
    geometry_binding = descriptor.get("geometry_binding")
    if isinstance(geometry_binding, Mapping):
        record["geometry_binding"] = dict(geometry_binding)
    return record


def resolve_component_interface(source: Any, interface_name: str) -> dict[str, Any]:
    """Resolve a published or native semantic connector on one component."""

    if published_object(source) is not None:
        return resolve_interface(None, source, interface_name)
    definitions = native_interface_definitions(source)
    definition = definitions.get(str(interface_name or ""))
    if not isinstance(definition, dict):
        raise ReferenceContractError(
            f"Component interface {interface_name!r} does not exist.",
            details={"available_interfaces": sorted(definitions)},
        )
    selection = dict(definition.get("selection") or {})
    resolved = dict(definition.get("resolved") or {})
    frame = resolved.get("connector_frame")
    if selection.get("type") == "frame":
        connector_frame_placement(frame)
    return {
        "ok": True,
        "model_id": "",
        "publication_name": "",
        "output_key": "",
        "interface_name": str(interface_name),
        "selection": selection,
        "subelements": list(resolved.get("subelements") or []),
        "geometry": list(resolved.get("geometry") or []),
        "connector_frame": frame,
        "connector": dict(definition.get("connector") or {}),
    }


def interface_selection_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {
                "const": "published_interface",
                "description": (
                    "Bind to a stable semantic interface declared by a "
                    "scripted model, not to a transient FaceN/EdgeN name."
                ),
            },
            "interface_name": {
                "type": "string",
                "pattern": "^[A-Za-z][A-Za-z0-9_]*$",
                "description": "Exact published interface name from model context.",
            },
        },
        "required": ["type", "interface_name"],
        "additionalProperties": False,
    }


def set_contract(obj: Any, domain: str, payload: dict[str, Any]) -> None:
    if PROP_CONTRACT not in list(getattr(obj, "PropertiesList", []) or []):
        obj.addProperty("App::PropertyString", PROP_CONTRACT, "VibeCAD References")
    contract = {
        "schema": CONTRACT_SCHEMA,
        "domain": str(domain),
        **dict(payload),
    }
    setattr(obj, PROP_CONTRACT, json.dumps(contract, sort_keys=True, separators=(",", ":")))


def read_contract(obj: Any) -> dict[str, Any] | None:
    if PROP_CONTRACT not in list(getattr(obj, "PropertiesList", []) or []):
        return None
    raw = str(getattr(obj, PROP_CONTRACT, "") or "")
    if not raw:
        return None
    try:
        contract = json.loads(raw)
    except ValueError as exc:
        raise ReferenceContractError(
            f"Object {getattr(obj, 'Name', '<object>')} has invalid managed "
            "reference metadata.",
            details={"native_error": str(exc), "raw_contract": raw},
        ) from exc
    if not isinstance(contract, dict) or contract.get("schema") != CONTRACT_SCHEMA:
        raise ReferenceContractError(
            f"Object {getattr(obj, 'Name', '<object>')} has an unsupported "
            "managed reference contract.",
            details={"contract": contract},
        )
    return contract


def _publication_for_target(target: Any) -> Any | None:
    """Resolve a managed output target to its canonical publication."""

    if publication.role_of(target) != publication.ROLE_PUBLICATION_TARGET:
        return None
    model_id = str(getattr(target, publication.PROP_MODEL_ID, "") or "")
    output_key = str(getattr(target, publication.PROP_OUTPUT_KEY, "") or "")
    if not model_id or not output_key:
        return None
    try:
        root = publication.model_root_for(target)
        return publication.model_publications(root).get(output_key)
    except publication.PublicationError:
        return None


def published_object(value: Any) -> Any | None:
    # App::Link forwards properties from LinkedObject, including the scripted
    # role tag. Resolve the native link target before inspecting the occurrence
    # itself or an assembly component can be mistaken for the publication.
    linked = getattr(value, "LinkedObject", None)
    if publication.is_publication(linked):
        return linked
    linked_publication = _publication_for_target(linked)
    if linked_publication is not None:
        return linked_publication
    # A reusable Part Design/Robot component occurrence is itself the stable
    # output carrier.  It intentionally links the vendor/native definition
    # rather than a scripted publication, while its owning program root stores
    # exact output-local interface frames.  Treat only that explicit managed
    # contract as interface-bearing; ordinary App::Link objects remain links to
    # their publication target as above.
    if (
        str(getattr(value, "TypeId", "") or "") == "App::Link"
        and str(getattr(value, "VibeCADVibeScriptOutputType", "") or "")
        == "component_link"
        and str(getattr(value, "VibeCADVibeScriptDomain", "") or "")
        in {"partdesign", "robot"}
        and publication.role_of(value) == publication.ROLE_IMPLEMENTATION
    ):
        return value
    if publication.is_publication(value):
        return value
    target_publication = _publication_for_target(value)
    if target_publication is not None:
        return target_publication
    objects = list(getattr(value, "Objects", []) or [])
    if len(objects) == 1 and publication.is_publication(objects[0]):
        return objects[0]
    return None


def resolve_interface(
    service: Any,
    source: Any,
    interface_name: str,
) -> dict[str, Any]:
    published = published_object(source)
    if published is None:
        raise ReferenceContractError(
            "Published interfaces can only be selected on a VibeCAD published "
            "output or an App::Link to one.",
            details={"source": getattr(source, "Name", None)},
        )
    model_id = str(getattr(published, publication.PROP_MODEL_ID, "") or "")
    output_key = str(getattr(published, publication.PROP_OUTPUT_KEY, "") or "")
    try:
        root = publication.model_root_for(published)
    except publication.PublicationError as exc:
        raise ReferenceContractError(
            "The published output does not resolve to exactly one scripted model root.",
            details={"model_id": model_id, **exc.details},
        ) from exc
    try:
        interfaces = json.loads(
            str(getattr(root, publication.PROP_INTERFACES, "{}") or "{}")
        )
    except ValueError as exc:
        raise ReferenceContractError(
            "The scripted model's published interface table is invalid.",
            details={"model_root": root.Name, "native_error": str(exc)},
        ) from exc
    definitions = interface_definitions_for_output(interfaces, output_key)
    definition = definitions.get(str(interface_name or ""))
    if not isinstance(definition, dict):
        raise ReferenceContractError(
            f"Published interface {interface_name!r} does not exist on output "
            f"{output_key!r}.",
            details={
                "model_root": root.Name,
                "output_key": output_key,
                "available_interfaces": sorted(definitions),
            },
        )
    selection = dict(definition.get("selection") or {})
    resolved = definition.get("resolved")
    if not isinstance(resolved, dict):
        raise ReferenceContractError(
            f"Published interface {interface_name!r} has no validated resolution "
            "for the accepted model revision.",
            details={
                "model_id": model_id,
                "output_key": output_key,
                "selection": selection,
                "resolved": resolved,
            },
        )
    subelements = list(resolved.get("subelements") or [])
    geometry = list(resolved.get("geometry") or [])
    mode = str(selection.get("type") or "")
    expected = (
        0
        if mode in {"origin", "frame"}
        else int(selection.get("expected_count") or 0)
    )
    if (
        mode not in {"origin", "frame", "query"}
        or str(resolved.get("object") or "") != published.Name
        or len(subelements) != expected
        or len(geometry) != expected
    ):
        raise ReferenceContractError(
            f"Published interface {interface_name!r} has inconsistent validated "
            "resolution metadata.",
            details={
                "model_id": model_id,
                "output_key": output_key,
                "selection": selection,
                "resolved": resolved,
                "expected_count": expected,
            },
        )
    connector_frame = resolved.get("connector_frame")
    if connector_frame is not None and not isinstance(connector_frame, dict):
        raise ReferenceContractError(
            f"Published interface {interface_name!r} has a malformed connector frame.",
            details={"connector_frame": connector_frame},
        )
    if mode == "frame" and connector_frame is None:
        raise ReferenceContractError(
            f"Published interface {interface_name!r} has no explicit connector frame.",
            details={"selection": selection},
        )
    if connector_frame is None:
        try:
            connector_frame = _native_connector_frame(published, subelements)
        except Exception as exc:
            raise ReferenceContractError(
                f"Published interface {interface_name!r} has no resolvable native "
                "connector frame.",
                details={"subelements": subelements, "native_error": str(exc)},
            ) from exc
    connector_frame_placement(connector_frame)
    connector = definition.get("connector")
    if connector is not None and not isinstance(connector, dict):
        raise ReferenceContractError(
            f"Published interface {interface_name!r} has a malformed connector contract.",
            details={"connector": connector},
        )
    return {
        "ok": True,
        "model_id": model_id,
        "model_root": root.Name,
        "publication": published,
        "publication_name": published.Name,
        "output_key": output_key,
        "interface_name": interface_name,
        "selection": selection,
        "subelements": subelements,
        "geometry": geometry,
        "connector_frame": connector_frame,
        **({"connector": dict(connector)} if isinstance(connector, dict) else {}),
    }


def referenced_interface_names(contract: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "published_interface":
                model_id = str(value.get("model_id") or "")
                name = str(value.get("interface_name") or "")
                if model_id and name:
                    found.append((model_id, name))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(contract)
    return found


def referenced_interfaces(
    contract: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """Return managed ``(model, output, interface)`` references.

    Older contracts did not persist the output key.  They remain readable with
    an empty output identity and are handled conservatively during removal.
    """

    found: list[tuple[str, str, str]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "published_interface":
                model_id = str(value.get("model_id") or "")
                output_key = str(value.get("output_key") or "")
                name = str(value.get("interface_name") or "")
                if model_id and name:
                    found.append((model_id, output_key, name))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(contract)
    return found


def referenced_model_ids(contract: dict[str, Any]) -> set[str]:
    """Return scripted-model dependencies explicitly recorded by a contract."""

    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") in {"published_interface", "scripted_model"}:
                model_id = str(value.get("model_id") or "")
                if model_id:
                    found.add(model_id)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(contract)
    return found


def scripted_model_dependencies(obj: Any) -> list[str]:
    """Find scripted models in the native dependency ancestry of ``obj``."""

    candidates = [obj, *list(getattr(obj, "OutListRecursive", []) or [])]
    seen: set[int] = set()
    model_ids: set[str] = set()
    for candidate in candidates:
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        published = published_object(candidate)
        if published is not None:
            model_id = str(
                getattr(published, publication.PROP_MODEL_ID, "") or ""
            )
            if model_id:
                model_ids.add(model_id)
        contract = read_contract(candidate)
        if contract is not None:
            model_ids.update(referenced_model_ids(contract))
    return sorted(model_ids)


def dependency_records(model_ids: list[str]) -> list[dict[str, str]]:
    return [
        {"type": "scripted_model", "model_id": model_id}
        for model_id in sorted(set(model_ids))
        if model_id
    ]


def validate_removed_interfaces(
    doc: Any,
    publications: list[Any],
    model_id: str,
    previous_names: set[Any],
    current_names: set[Any],
    *,
    preflight: dict[str, Any] | None = None,
) -> None:
    def normalize(values: set[Any]) -> set[tuple[str, str]]:
        result: set[tuple[str, str]] = set()
        for value in values:
            if isinstance(value, tuple) and len(value) == 2:
                result.add((str(value[0]), str(value[1])))
            else:
                result.add(("", str(value)))
        return result

    removed = normalize(previous_names) - normalize(current_names)
    if not removed:
        return
    consumers: list[dict[str, Any]] = []
    carriers = list((preflight or {}).get("_carriers") or [])
    if not carriers:
        carriers, _uses = _reference_graph(doc, publications)
    for obj in carriers:
        if publication.is_publication(obj):
            continue
        contract = read_contract(obj)
        if contract is None:
            continue
        used_identities = sorted(
            (removed_output, name)
            for referenced_model, output_key, name in referenced_interfaces(contract)
            for removed_output, removed_name in removed
            if referenced_model == model_id
            and name == removed_name
            and (
                not output_key
                or not removed_output
                or output_key == removed_output
            )
        )
        used = [
            {"output": output, "name": name}
            for output, name in dict.fromkeys(used_identities)
        ]
        if used:
            consumers.append({"object": obj.Name, "interfaces": used})
    if consumers:
        raise ReferenceContractError(
            "The VibeScript update removes published interfaces that are still "
            "used by downstream CAD objects.",
            details={
                "removed_interfaces": [
                    {"output": output, "name": name}
                    for output, name in sorted(removed)
                ],
                "consumers": consumers,
            },
        )


def refresh_after_publication(
    service: Any,
    model_id: str,
    publications: list[Any],
    *,
    revision: str,
    preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc = service._active_document()
    carriers = list((preflight or {}).get("_carriers") or [])
    uses = list((preflight or {}).get("_uses") or [])
    if not carriers:
        carriers, uses = _reference_graph(doc, publications)
    managed: list[dict[str, Any]] = []
    unsafe: list[dict[str, Any]] = []
    for use in uses:
        if not use.get("subelements"):
            continue
        owner = use["owner"]
        contract = read_contract(owner)
        if contract is None:
            unsafe.append(publication.json_reference_uses([use])[0])
            continue
        managed.append({"object": owner, "contract": contract})
    if unsafe:
        raise ReferenceContractError(
            "Regeneration would leave unmanaged Face/Edge/Vertex references "
            "pointing at potentially different geometry.",
            details={
                "unsafe_references": unsafe,
                "required_action": (
                    "Recreate these references with a semantic published interface "
                    "or remove them before regenerating the model."
                ),
            },
        )

    carrier_order = {id(carrier): index for index, carrier in enumerate(carriers)}
    managed.sort(
        key=lambda item: carrier_order.get(id(item["object"]), len(carriers))
    )
    rebound: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    deferred_part_rebinds: list[str] = []
    seen: set[int] = set()
    for item in managed:
        obj = item["object"]
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        contract = item["contract"]
        if model_id not in referenced_model_ids(contract):
            continue
        if str(contract.get("domain") or "") == "part_edge_finish":
            deferred_part_rebinds.append(str(getattr(obj, "Name", "") or ""))
            continue
        contract = dict(contract)
        contract["source_revision"] = revision
        outcome = _rebind_one(service, obj, contract)
        if outcome.get("rebind_deferred"):
            deferred.append(outcome)
        else:
            rebound.append(outcome)

    invalidated: list[dict[str, Any]] = []
    for carrier in carriers:
        if publication.is_publication(carrier):
            continue
        if id(carrier) in seen:
            continue
        touch = getattr(carrier, "touch", None)
        if callable(touch):
            touch()
        if _is_derived_analysis_or_manufacturing_object(carrier):
            mark_stale(
                carrier,
                revision,
                "A referenced scripted model changed; regenerate this derived result.",
            )
            invalidated.append(
                {"object": carrier.Name, "type": getattr(carrier, "TypeId", "")}
            )
    # _reference_graph expands publication consumers breadth-first, which is
    # already dependency order from the regenerated source toward derived Part
    # features. Avoid materializing the whole document's topological ordering.
    ordered = list(carriers)
    part_recompute_objects = [
        str(getattr(item, "Name", "") or "")
        for item in ordered
        if not publication.is_publication(item)
        and "Shape" in list(getattr(item, "PropertiesList", []) or [])
        and str(getattr(item, "TypeId", "") or "").startswith(("Part::", "PartDesign::"))
        and "Python" not in str(getattr(item, "TypeId", "") or "")
    ]
    return {
        "rebound": rebound,
        "deferred": deferred,
        "invalidated": invalidated,
        "carrier_count": len(carriers),
        "part_recompute_objects": part_recompute_objects,
        "deferred_part_rebinds": deferred_part_rebinds,
        "native_part_expectations": list(
            (preflight or {}).get("native_part_expectations") or []
        ),
    }


def preflight_regeneration(
    service: Any,
    publications: list[Any],
    *,
    model_root: Any | None = None,
) -> dict[str, Any]:
    doc = service._active_document()
    carriers, uses = _reference_graph(
        doc,
        publications,
        model_root=model_root,
    )
    unsafe: list[dict[str, Any]] = []
    managed_objects: set[str] = set()
    for use in uses:
        if not use.get("subelements"):
            continue
        owner = use["owner"]
        contract = read_contract(owner)
        if contract is None:
            unsafe.append(publication.json_reference_uses([use])[0])
        else:
            managed_objects.add(str(getattr(owner, "Name", "") or ""))
    if unsafe:
        raise ReferenceContractError(
            "Regeneration would leave unmanaged Face/Edge/Vertex references "
            "pointing at potentially different geometry.",
            details={
                "unsafe_references": unsafe,
                "required_action": (
                    "Recreate these references with a semantic published interface "
                    "or remove them before regenerating the model."
                ),
            },
        )
    return {
        "carrier_count": len(carriers),
        "carrier_objects": [
            str(getattr(item, "Name", "") or "") for item in carriers
        ],
        "managed_reference_objects": sorted(managed_objects),
        "native_part_expectations": native_part_carrier_expectations(carriers),
        "_carriers": carriers,
        "_uses": uses,
    }


def _reference_graph(
    doc: Any,
    publications: list[Any],
    *,
    model_root: Any | None = None,
) -> tuple[list[Any], list[dict[str, Any]]]:
    roots: dict[str, Any] = {}
    if model_root is not None:
        root_name = str(getattr(model_root, "Name", "") or "")
        unowned = []
        for item in publications:
            try:
                owner = publication.model_root_for(item)
                publication.publication_target(item, model_root)
            except publication.PublicationError:
                owner = None
            if owner is not model_root:
                unowned.append(str(getattr(item, "Name", "") or ""))
        if not root_name or unowned:
            raise ReferenceContractError(
                "Scripted outputs do not belong to their declared model root.",
                details={"model_root": root_name, "unowned_outputs": unowned},
            )
        roots[root_name] = model_root
    else:
        for item in publications:
            try:
                root = publication.model_root_for(item)
            except publication.PublicationError as exc:
                raise ReferenceContractError(
                    "A scripted publication has no unambiguous model owner.",
                    details=exc.details,
                ) from exc
            roots[str(getattr(root, "Name", "") or "")] = root
    internal = list(roots.values())
    for root in roots.values():
        internal.extend(publication.implementation_closure(root))
    carriers = list(publications)
    carrier_ids = {id(item) for item in carriers}
    all_uses: list[dict[str, Any]] = []
    use_keys: set[tuple[Any, ...]] = set()

    def retain_uses(uses: list[dict[str, Any]]) -> None:
        for use in uses:
            key = (
                id(use.get("owner")),
                str(use.get("property") or ""),
                use.get("_target_id"),
                str(use.get("target_name") or ""),
                tuple(str(item) for item in list(use.get("subelements") or [])),
            )
            if key in use_keys:
                continue
            use_keys.add(key)
            all_uses.append(use)

    changed = True
    while changed:
        changed = False
        uses = publication.external_reference_uses(
            doc,
            carriers,
            internal_objects=[*publications, *internal],
        )
        retain_uses(uses)
        for use in uses:
            property_type = str(use.get("property_type") or "")
            if "LinkSub" in property_type:
                continue
            owner = use["owner"]
            if id(owner) not in carrier_ids:
                carrier_ids.add(id(owner))
                carriers.append(owner)
                changed = True
    final_uses = publication.external_reference_uses(
        doc,
        carriers,
        internal_objects=[*publications, *internal],
    )
    retain_uses(final_uses)
    return carriers, all_uses


def _rebind_one(service: Any, obj: Any, contract: dict[str, Any]) -> dict[str, Any]:
    domain = str(contract.get("domain") or "")
    if domain == "assembly_joint":
        import VibeCADReferenceRebindAssembly as handler
    elif domain == "fem_constraint":
        import VibeCADReferenceRebindFEM as handler
    elif domain == "techdraw_dimension":
        import VibeCADReferenceRebindDrawing as handler
    elif domain == "cam_reference":
        import VibeCADReferenceRebindManufacture as handler
    elif domain == "part_edge_finish":
        import VibeCADReferenceRebindPart as handler
    else:
        raise ReferenceContractError(
            f"No rebinding implementation exists for managed reference domain {domain!r}.",
            details={"object": obj.Name, "contract": contract},
        )
    rebind = getattr(handler, "rebind_scripted_reference", None)
    if not callable(rebind):
        raise ReferenceContractError(
            f"Reference domain {domain!r} does not implement regeneration rebinding.",
            details={"object": obj.Name},
        )
    result = rebind(service, obj, contract)
    if not isinstance(result, dict) or not result.get("ok"):
        raise ReferenceContractError(
            f"Managed references on {obj.Name} could not be rebound.",
            details={"object": obj.Name, "domain": domain, "result": result},
        )
    return result


def _native_part_carriers(carriers: list[Any]) -> list[Any]:
    return [
        obj
        for obj in carriers
        if not publication.is_publication(obj)
        and str(getattr(obj, "TypeId", "") or "").startswith(
            ("Part::", "PartDesign::")
        )
        and "Python" not in str(getattr(obj, "TypeId", "") or "")
        and "Shape" in list(getattr(obj, "PropertiesList", []) or [])
    ]


def _expected_content_kind(shape_type: str) -> str:
    return {
        "Solid": "solid",
        "CompSolid": "solid",
        "Shell": "face",
        "Face": "face",
        "Wire": "edge",
        "Edge": "edge",
        "Vertex": "vertex",
    }.get(shape_type, "topology")


def native_part_carrier_expectations(carriers: list[Any]) -> list[dict[str, Any]]:
    """Capture cheap owner-thread expectations without validating geometry."""

    expectations: list[dict[str, Any]] = []
    for obj in _native_part_carriers(carriers):
        shape = getattr(obj, "Shape", None)
        try:
            is_null = shape is None or bool(shape.isNull())
            shape_type = None if is_null else str(shape.ShapeType)
        except Exception as exc:
            is_null = True
            shape_type = None
            inspection_error = str(exc)
        else:
            inspection_error = None
        expectations.append(
            {
                "object": str(getattr(obj, "Name", "") or ""),
                "type": str(getattr(obj, "TypeId", "") or ""),
                "state": [
                    str(value) for value in list(getattr(obj, "State", []) or [])
                ],
                "shape_null": is_null,
                "shape_type": shape_type,
                "expected_content_kind": _expected_content_kind(shape_type or ""),
                **(
                    {"inspection_error": inspection_error}
                    if inspection_error
                    else {}
                ),
            }
        )
    return expectations


def capture_native_part_carriers(carriers: list[Any]) -> list[dict[str, Any]]:
    """Capture immutable shape handles for provider-thread validation."""

    return [
        {
            "object": str(getattr(obj, "Name", "") or ""),
            "type": str(getattr(obj, "TypeId", "") or ""),
            "state": [str(value) for value in list(getattr(obj, "State", []) or [])],
            "_shape": getattr(obj, "Shape", None),
        }
        for obj in _native_part_carriers(carriers)
    ]


def native_part_carrier_facts(
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate detached native Part shape handles on the provider worker."""

    facts: list[dict[str, Any]] = []
    for snapshot in snapshots:
        item: dict[str, Any] = {
            "object": str(snapshot.get("object") or ""),
            "type": str(snapshot.get("type") or ""),
            "state": list(snapshot.get("state") or []),
        }
        try:
            shape = snapshot.get("_shape")
            is_null = shape is None or bool(shape.isNull())
            item.update(
                {
                    "shape_null": is_null,
                    "shape_valid": False if is_null else bool(shape.isValid()),
                    "shape_type": None if is_null else str(shape.ShapeType),
                    "solids": 0 if is_null else len(list(shape.Solids or [])),
                    "faces": 0 if is_null else len(list(shape.Faces or [])),
                    "edges": 0 if is_null else len(list(shape.Edges or [])),
                    "vertices": 0 if is_null else len(list(shape.Vertexes or [])),
                    "volume_mm3": 0.0 if is_null else float(shape.Volume),
                }
            )
        except Exception as exc:
            item["inspection_error"] = str(exc)
        facts.append(item)
    return facts


def _validate_native_part_carriers(
    snapshots: list[dict[str, Any]],
    expectations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reject invalid downstream Part features before regeneration commits."""

    expected_by_name = {
        str(item.get("object") or ""): item
        for item in expectations
        if isinstance(item, dict) and item.get("object")
    }
    checked = native_part_carrier_facts(snapshots)
    failures: list[dict[str, Any]] = []
    for item in checked:
        state_values = list(item.get("state") or [])
        before = expected_by_name.get(str(item.get("object") or ""))
        retained_content = True
        if before is not None:
            expected_kind = str(before.get("expected_content_kind") or "topology")
            if expected_kind == "solid":
                retained_content = int(item.get("solids", 0) or 0) > 0
            elif expected_kind == "face":
                retained_content = int(item.get("faces", 0) or 0) > 0
            elif expected_kind == "edge":
                retained_content = int(item.get("edges", 0) or 0) > 0
            elif expected_kind == "vertex":
                retained_content = int(item.get("vertices", 0) or 0) > 0
            else:
                retained_content = any(
                    int(item.get(field, 0) or 0) > 0
                    for field in ("solids", "faces", "edges", "vertices")
                )
        item["pre_regeneration_shape"] = before
        item["retained_shape_content"] = retained_content
        item["ok"] = bool(
            item.get("shape_null") is False
            and item.get("shape_valid") is True
            and not any("Invalid" in value for value in state_values)
            and not item.get("inspection_error")
            and retained_content
        )
        if not item["ok"]:
            failures.append(item)
    if failures:
        raise ReferenceContractError(
            "A downstream native Part feature became invalid after scripted-model "
            "regeneration; the update was not accepted.",
            details={"invalid_part_features": failures},
        )
    return {"ok": True, "checked": checked}


def _is_derived_analysis_or_manufacturing_object(obj: Any) -> bool:
    type_id = str(getattr(obj, "TypeId", "") or "").lower()
    if any(
        marker in type_id
        for marker in ("femmesh", "femresult", "resultmechanical")
    ):
        return True
    properties = set(getattr(obj, "PropertiesList", []) or [])
    return type_id.startswith("path::") and {"Base", "Path"}.issubset(properties)


def mark_stale(obj: Any, revision: str, reason: str) -> None:
    for name in (PROP_DERIVED_STATE, PROP_STALE_REASON, PROP_SOURCE_REVISION):
        if name not in list(getattr(obj, "PropertiesList", []) or []):
            obj.addProperty("App::PropertyString", name, "VibeCAD References")
    setattr(obj, PROP_DERIVED_STATE, "stale")
    setattr(obj, PROP_STALE_REASON, reason)
    setattr(obj, PROP_SOURCE_REVISION, revision)


def rebind_managed_reference(
    service: Any, obj: Any, *, revision: str
) -> dict[str, Any]:
    """Rebind one persisted semantic contract without recomputing the document."""

    contract = read_contract(obj)
    if contract is None:
        raise ReferenceContractError(
            "The requested downstream object has no managed reference contract.",
            details={"object": str(getattr(obj, "Name", "") or "")},
        )
    effective = dict(contract)
    effective["source_revision"] = revision
    return _rebind_one(service, obj, effective)


def validate_native_part_refresh(
    snapshots: list[dict[str, Any]], expectations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Validate recomputed detached Part carriers against preflight facts."""

    return _validate_native_part_carriers(snapshots, expectations)
