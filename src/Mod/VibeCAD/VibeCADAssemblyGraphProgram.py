# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compile a typed Assembly graph into portable VibeScript source."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import keyword
import math
import re
from typing import Any

from vibescript_assembly_api import explicit_connector_compatibility
from vibescript_component_api import component_placement


_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_CATALOG_KEY = re.compile(r"^component-[1-9][0-9]*$")
_PROGRAM_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 ._-]{0,119}$")
JOINT_TYPES = (
    "fixed",
    "revolute",
    "cylindrical",
    "slider",
    "ball",
    "distance",
    "parallel",
    "perpendicular",
    "angle",
    "rack_pinion",
    "screw",
    "gear",
    "belt",
)
_JOINT_PARAMETERS = (
    "distance_mm",
    "angle_degrees",
    "pitch_radius_mm",
    "thread_pitch_mm",
    "radius1_mm",
    "radius2_mm",
    "length_limits_mm",
    "angle_limits_degrees",
    "suppressed",
    "label",
)
_REQUIRED_JOINT_PARAMETERS = {
    "distance": ("distance_mm",),
    "angle": ("angle_degrees",),
    "rack_pinion": ("pitch_radius_mm",),
    "screw": ("thread_pitch_mm",),
    "gear": ("radius1_mm", "radius2_mm"),
    "belt": ("radius1_mm", "radius2_mm"),
}
_ALLOWED_JOINT_PARAMETERS = {
    "fixed": (),
    "revolute": ("angle_limits_degrees",),
    "cylindrical": ("length_limits_mm", "angle_limits_degrees"),
    "slider": ("length_limits_mm",),
    "ball": (),
    "distance": ("distance_mm",),
    "parallel": (),
    "perpendicular": (),
    "angle": ("angle_degrees",),
    "rack_pinion": ("pitch_radius_mm",),
    "screw": ("thread_pitch_mm",),
    "gear": ("radius1_mm", "radius2_mm"),
    "belt": ("radius1_mm", "radius2_mm"),
}


class AssemblyGraphProgramError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        path: Sequence[Any] = (),
        observed: Mapping[str, Any] | None = None,
        allowed_values: Sequence[Any] = (),
        required_changes: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self.path = list(path)
        self.observed = dict(observed or {})
        self.allowed_values = list(allowed_values)
        self.required_changes = [dict(value) for value in required_changes]
        super().__init__(message)


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AssemblyGraphProgramError(f"{path} must be an object")
    return dict(value)


def _records(value: Any, path: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise AssemblyGraphProgramError(f"{path} must be an array")
    return [_mapping(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _key(value: Any, path: str) -> str:
    clean = str(value or "").strip()
    if _KEY.fullmatch(clean) is None:
        raise AssemblyGraphProgramError(f"{path} must be a stable identifier")
    return clean


def _catalog_key(value: Any, path: str) -> str:
    clean = str(value or "").strip()
    if _CATALOG_KEY.fullmatch(clean) is None:
        raise AssemblyGraphProgramError(f"{path} must be a listed catalog_key")
    return clean


def _program_name(data: Mapping[str, Any]) -> str:
    explicit = str(data.get("program_name") or "").strip()
    if explicit:
        if _PROGRAM_NAME.fullmatch(explicit) is None:
            raise AssemblyGraphProgramError("program_name is invalid")
        return explicit
    derived = re.sub(
        r"[^A-Za-z0-9 ._-]+",
        "_",
        str(data.get("label") or "Assembly"),
    ).strip(" ._-")
    if not derived:
        return "Assembly"
    if not derived[0].isalpha():
        derived = f"Assembly {derived}"
    return derived[:120].rstrip(" ._-")


def _unique(
    records: list[dict[str, Any]],
    noun: str,
    *,
    derive_from_label: bool = False,
) -> list[str]:
    keys = []
    for index, record in enumerate(records):
        raw_key = record.get("key")
        if derive_from_label and not str(raw_key or "").strip():
            raw_key = re.sub(
                r"[^A-Za-z0-9_]+",
                "_",
                str(record.get("label") or f"{noun}_{index + 1}"),
            ).strip("_")
            if not raw_key or not raw_key[0].isalpha():
                raw_key = f"{noun}_{raw_key}"
            record["key"] = str(raw_key)[:64].rstrip("_")
        keys.append(_key(raw_key, f"{noun}s[{index}].key"))
    if len(keys) != len(set(keys)):
        raise AssemblyGraphProgramError(f"{noun} keys must be unique")
    return keys


def _variables(keys: Sequence[str], noun: str) -> dict[str, str]:
    result: dict[str, str] = {}
    used: set[str] = set()
    for index, key in enumerate(keys, start=1):
        base = re.sub(r"[^A-Za-z0-9_]", "_", key).strip("_")
        if not base or not base[0].isalpha() or keyword.iskeyword(base):
            base = f"{noun}_{base or index}"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        result[key] = candidate
    return result


def _call_kwargs(record: Mapping[str, Any], names: Sequence[str]) -> str:
    values = [f"{name}={record[name]!r}" for name in names if name in record]
    return (", " + ", ".join(values)) if values else ""


def _published_interfaces(
    component_catalog: Mapping[str, Any] | None,
    catalog_key: str,
) -> list[dict[str, Any]] | None:
    catalog = dict(component_catalog or {})
    records = catalog.get("components") or catalog.get("candidates") or []
    for raw_component in records:
        if not isinstance(raw_component, Mapping):
            continue
        if str(raw_component.get("catalog_key") or "") != catalog_key:
            continue
        raw_interfaces = raw_component.get("interfaces")
        if not isinstance(raw_interfaces, Sequence) or isinstance(
            raw_interfaces, (str, bytes)
        ):
            return None
        return [
            dict(raw_interface)
            for raw_interface in raw_interfaces
            if isinstance(raw_interface, Mapping)
        ]
    return None


def _published_connector(
    interfaces: Sequence[Mapping[str, Any]] | None,
    interface_name: str,
) -> dict[str, Any]:
    for raw_interface in interfaces or ():
        if str(raw_interface.get("name") or "") != interface_name:
            continue
        connector = raw_interface.get("connector")
        return dict(connector) if isinstance(connector, Mapping) else {}
    return {}


def _positive_connector_value(connector: Mapping[str, Any], name: str) -> float | None:
    value = connector.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0.0 else None


def _connector_joint_types(connector: Mapping[str, Any]) -> set[str]:
    allowed = connector.get("allowed_joints")
    if not isinstance(allowed, (list, tuple)):
        return set(JOINT_TYPES)
    return {
        "gear" if str(value) == "gears" else str(value)
        for value in allowed
        if str(value) in {*JOINT_TYPES, "gears"}
    }


def _connector_compatibility_token(
    connector: Mapping[str, Any],
    kind: str,
) -> str:
    compatibility = connector.get("compatibility")
    if isinstance(compatibility, Mapping):
        return str(
            compatibility.get("gears" if kind == "gear" else kind)
            or compatibility.get(kind)
            or ""
        )
    return str(compatibility or "")


def _compatible_connector_joint_types(
    connectors: Sequence[Mapping[str, Any]],
) -> list[str]:
    return [
        kind
        for kind in JOINT_TYPES
        if all(kind in _connector_joint_types(connector) for connector in connectors)
        and len(
            {
                token
                for token in (
                    _connector_compatibility_token(connector, kind)
                    for connector in connectors
                )
                if token
            }
        )
        <= 1
    ]


def _connector_source(
    raw_key: Any,
    raw_interface: Any,
    *,
    path: str,
    component_keys: set[str],
    component_variables: Mapping[str, str],
) -> tuple[str, str]:
    component = _key(raw_key, f"{path}_key")
    if component not in component_keys:
        raise AssemblyGraphProgramError(
            f"{path} references unknown component {component!r}"
        )
    interface = str(raw_interface or "").strip()
    if re.fullmatch(r"^[A-Za-z][A-Za-z0-9_]*$", interface) is None:
        raise AssemblyGraphProgramError(f"{path}_interface is invalid")
    selection = {
        "type": "published_interface",
        "interface_name": interface,
    }
    return component, (
        f"api.connector({component_variables[component]}, {dict(selection)!r})"
    )


def _validate_connectivity(
    component_keys: list[str],
    grounded: set[str],
    edges: list[tuple[str, str]],
) -> None:
    if not grounded:
        raise AssemblyGraphProgramError("components require at least one grounded component")
    adjacency = {key: set() for key in component_keys}
    for first, second in edges:
        adjacency[first].add(second)
        adjacency[second].add(first)
    reached = set(grounded)
    pending = list(grounded)
    while pending:
        current = pending.pop()
        for neighbor in adjacency[current] - reached:
            reached.add(neighbor)
            pending.append(neighbor)
    missing = [key for key in component_keys if key not in reached]
    if missing:
        raise AssemblyGraphProgramError(
            f"components are disconnected from ground: {missing}",
            path=("joints",),
            observed={
                "grounded_component_key": next(iter(grounded)),
                "disconnected_component_keys": missing,
            },
            required_changes=(
                {
                    "path": ["joints"],
                    "connect_components": missing,
                    "to_grounded_graph": True,
                },
            ),
        )


def _validate_rotational_couplings(
    joint_records: Sequence[Mapping[str, Any]],
    grounded: set[str],
) -> None:
    revolutes = [
        record
        for record in joint_records
        if record["kind"] == "revolute" and not record["suppressed"]
    ]
    for record in joint_records:
        if record["kind"] not in {"gear", "belt"} or record["suppressed"]:
            continue
        endpoints = list(record["endpoints"])
        matches = [
            [
                str(revolute["key"])
                for revolute in revolutes
                if endpoint in revolute["endpoints"]
            ]
            for endpoint in endpoints
        ]
        grounded_components = list(
            dict.fromkeys(
                component
                for component, _interface in endpoints
                if component in grounded
            )
        )
        if (
            not grounded_components
            and all(len(items) == 1 for items in matches)
            and matches[0][0] != matches[1][0]
        ):
            continue
        index = int(record["index"])
        raise AssemblyGraphProgramError(
            f"joints[{index}] {record['kind']} requires one distinct "
            "revolute joint at each interface",
            path=("joints", index),
            observed={
                "joint_key": record["key"],
                "grounded_component_keys": grounded_components,
                "endpoints": [
                    {
                        "component_key": component,
                        "interface": interface,
                        "revolute_joint_keys": endpoint_matches,
                    }
                    for (component, interface), endpoint_matches in zip(
                        endpoints,
                        matches,
                        strict=True,
                    )
                ],
            },
            required_changes=(
                {
                    "path": ["joints", index],
                    "require_distinct_revolute_at_each_interface": True,
                    "unground_component_keys": grounded_components,
                },
            ),
        )


def compile_assembly_program(
    request: Mapping[str, Any],
    *,
    component_catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return generic create_program arguments for one structured graph."""

    data = _mapping(request, "request")
    program_name = _program_name(data)

    components = _records(data.get("components"), "components")
    if not components:
        raise AssemblyGraphProgramError("components must contain at least one component")
    component_keys = _unique(components, "component")
    component_key_set = set(component_keys)
    grounded_key = _key(
        data.get("grounded_component_key"),
        "grounded_component_key",
    )
    if grounded_key not in component_key_set:
        raise AssemblyGraphProgramError(
            f"grounded_component_key references unknown component {grounded_key!r}",
            path=("grounded_component_key",),
            observed={"received": grounded_key},
            allowed_values=component_keys,
        )
    component_variables = _variables(component_keys, "component")
    component_records = dict(zip(component_keys, components))
    grounded = {grounded_key}

    joints = _records(data.get("joints"), "joints")
    joint_keys = _unique(joints, "joint", derive_from_label=True)
    joint_variables = _variables(joint_keys, "joint")
    joint_kinds: dict[str, str] = {}
    joint_sources: list[str] = []
    joint_records: list[dict[str, Any]] = []
    edges: list[tuple[str, str]] = []
    for index, (key, joint) in enumerate(zip(joint_keys, joints)):
        joint_spec = _mapping(joint.get("joint"), f"joints[{index}].joint")
        kind = str(joint_spec.get("kind") or "").strip()
        if kind not in JOINT_TYPES:
            raise AssemblyGraphProgramError(
                f"joints[{index}].kind must be one of {list(JOINT_TYPES)}"
            )
        first_component, first = _connector_source(
            joint.get("first_key"),
            joint.get("first_interface"),
            path=f"joints[{index}].first",
            component_keys=component_key_set,
            component_variables=component_variables,
        )
        second_component, second = _connector_source(
            joint.get("second_key"),
            joint.get("second_interface"),
            path=f"joints[{index}].second",
            component_keys=component_key_set,
            component_variables=component_variables,
        )
        if first_component == second_component:
            alternatives = [
                component_key
                for component_key in component_keys
                if component_key != first_component
            ]
            raise AssemblyGraphProgramError(
                f"joints[{index}] must connect two components",
                path=("joints", index, "second_key"),
                observed={"received": second_component},
                allowed_values=alternatives,
                required_changes=(
                    {
                        "path": ["joints", index, "second_key"],
                        "allowed_values": alternatives,
                    },
                ),
            )
        edges.append((first_component, second_component))
        joint_kinds[key] = kind
        api_kind = "gears" if kind == "gear" else kind
        endpoint_components = (first_component, second_component)
        endpoint_interfaces = (
            str(joint.get("first_interface") or ""),
            str(joint.get("second_interface") or ""),
        )
        published_interfaces = tuple(
            _published_interfaces(
                component_catalog,
                str(component_records[component_key].get("catalog_key") or ""),
            )
            for component_key in endpoint_components
        )
        for endpoint, component_key, interface_name, interfaces in zip(
            ("first", "second"),
            endpoint_components,
            endpoint_interfaces,
            published_interfaces,
            strict=True,
        ):
            interface_names = [
                str(interface.get("name") or "")
                for interface in interfaces or ()
                if str(interface.get("name") or "")
            ]
            if interface_names and interface_name not in interface_names:
                path = ("joints", index, f"{endpoint}_interface")
                raise AssemblyGraphProgramError(
                    f"joints[{index}].{endpoint}_interface is not published by "
                    f"component {component_key!r}",
                    path=path,
                    observed={
                        "component_key": component_key,
                        "received": interface_name,
                    },
                    allowed_values=interface_names,
                    required_changes=(
                        {
                            "path": list(path),
                            "allowed_values": interface_names,
                        },
                    ),
                )
        endpoint_connectors = tuple(
            _published_connector(
                interfaces,
                interface_name,
            )
            for interfaces, interface_name in zip(
                published_interfaces,
                endpoint_interfaces,
                strict=True,
            )
        )
        compatibility = explicit_connector_compatibility(kind, endpoint_connectors)
        if compatibility.get("ok") is not True:
            compatible_kinds = _compatible_connector_joint_types(
                endpoint_connectors
            )
            raise AssemblyGraphProgramError(
                f"joints[{index}] {compatibility.get('reason') or 'has incompatible interfaces'}",
                path=("joints", index, "joint", "kind"),
                observed={
                    "joint_kind": kind,
                    "first": {
                        "component_key": first_component,
                        "interface": endpoint_interfaces[0],
                        "connector": endpoint_connectors[0],
                    },
                    "second": {
                        "component_key": second_component,
                        "interface": endpoint_interfaces[1],
                        "connector": endpoint_connectors[1],
                    },
                },
                allowed_values=compatible_kinds,
                required_changes=(
                    (
                        {
                            "path": ["joints", index, "joint", "kind"],
                            "allowed_values": compatible_kinds,
                        },
                    )
                    if compatible_kinds
                    else (
                        {
                            "path": ["joints", index],
                            "change_endpoints": True,
                        },
                    )
                ),
            )
        joint_arguments = {
            **joint_spec,
            **({"label": joint["label"]} if "label" in joint else {}),
        }
        if kind in {"gear", "belt"}:
            for parameter, connector in zip(
                ("radius1_mm", "radius2_mm"),
                endpoint_connectors,
                strict=True,
            ):
                if joint_arguments.get(parameter) is not None:
                    continue
                value = _positive_connector_value(
                    connector,
                    "pitch_radius_mm",
                )
                if value is not None:
                    joint_arguments[parameter] = value
        invalid_parameters = [
            name
            for name in _JOINT_PARAMETERS
            if name not in {"suppressed", "label"}
            and name in joint_arguments
            and name not in _ALLOWED_JOINT_PARAMETERS[kind]
        ]
        if invalid_parameters:
            raise AssemblyGraphProgramError(
                f"joints[{index}] {kind} does not accept "
                + " or ".join(invalid_parameters)
            )
        missing_parameters = [
            name
            for name in _REQUIRED_JOINT_PARAMETERS.get(kind, ())
            if joint_arguments.get(name) is None
        ]
        if missing_parameters:
            raise AssemblyGraphProgramError(
                f"joints[{index}] {kind} requires "
                + " and ".join(missing_parameters)
            )
        kwargs = _call_kwargs(joint_arguments, _JOINT_PARAMETERS)
        joint_sources.append(
            f"    {joint_variables[key]} = api.joint("
            f"{api_kind!r}, {first}, {second}{kwargs})"
        )
        joint_records.append(
            {
                "index": index,
                "key": key,
                "kind": kind,
                "suppressed": bool(joint_arguments.get("suppressed")),
                "endpoints": (
                    (first_component, endpoint_interfaces[0]),
                    (second_component, endpoint_interfaces[1]),
                ),
            }
        )
    _validate_rotational_couplings(joint_records, grounded)
    _validate_connectivity(component_keys, grounded, edges)

    lines = ["def main():"]
    for index, (key, component) in enumerate(zip(component_keys, components)):
        api_component = dict(component)
        if "placement" in api_component:
            try:
                component_placement(
                    "component",
                    "placement",
                    api_component["placement"],
                )
            except ValueError as exc:
                raise AssemblyGraphProgramError(
                    str(exc),
                    path=("components", index, "placement"),
                    observed={"placement": api_component["placement"]},
                ) from exc
        if key == grounded_key:
            api_component["grounded"] = True
        if "flexible_subassembly" in api_component:
            api_component["flexible"] = api_component.pop("flexible_subassembly")
        kwargs = _call_kwargs(
            api_component,
            ("placement", "grounded", "flexible", "label"),
        )
        lines.append(
            f"    {component_variables[key]} = api.component(inputs[{key!r}]{kwargs})"
        )
    lines.extend(joint_sources)
    lines.append("    components = {")
    lines.extend(
        f"        {key!r}: {component_variables[key]}," for key in component_keys
    )
    lines.append("    }")
    lines.append("    joints = {")
    lines.extend(f"        {key!r}: {joint_variables[key]}," for key in joint_keys)
    lines.append("    }")
    assembly_label = str(data.get("label") or "").strip()
    label_arg = f", label={assembly_label!r}" if assembly_label else ""
    lines.append(f"    model = api.assembly(components, joints{label_arg})")
    lines.append("    diagnostics = api.solve(model)")

    expected_outputs = [
        {"name": "assembly", "type": "assembly"},
        {"name": "solver_diagnostics", "type": "solver_diagnostics"},
    ]
    output_values = [
        ("assembly", "model"),
        ("solver_diagnostics", "diagnostics"),
    ]

    simulation = data.get("simulation")
    if simulation is not None:
        simulation_data = _mapping(simulation, "simulation")
        motions = _records(simulation_data.get("motions"), "simulation.motions")
        if not motions:
            raise AssemblyGraphProgramError(
                "simulation.motions must contain at least one motion"
            )
        motion_keys = _unique(motions, "motion")
        motion_variables = _variables(motion_keys, "motion")
        for index, (key, motion) in enumerate(zip(motion_keys, motions)):
            joint = _key(motion.get("joint"), f"simulation.motions[{index}].joint")
            if joint not in joint_kinds:
                raise AssemblyGraphProgramError(
                    f"simulation.motions[{index}] references unknown joint {joint!r}"
                )
            if joint_kinds[joint] not in {"revolute", "slider", "cylindrical"}:
                raise AssemblyGraphProgramError(
                    f"simulation.motions[{index}] cannot drive a {joint_kinds[joint]} joint"
                )
            formula = str(motion.get("formula") or "").strip()
            if not formula:
                raise AssemblyGraphProgramError(
                    f"simulation.motions[{index}].formula is required"
                )
            kwargs = _call_kwargs(motion, ("motion_type", "label"))
            lines.append(
                f"    {motion_variables[key]} = api.motion("
                f"{joint_variables[joint]}, {formula!r}{kwargs})"
            )
        lines.append("    motions = {")
        lines.extend(
            f"        {key!r}: {motion_variables[key]}," for key in motion_keys
        )
        lines.append("    }")
        simulation_kwargs = _call_kwargs(
            simulation_data,
            (
                "start_time_s",
                "end_time_s",
                "time_step_s",
                "error_tolerance",
                "frames_per_second",
                "label",
            ),
        )
        lines.append(f"    simulation_result = api.simulation(model, motions{simulation_kwargs})")
        expected_outputs.append({"name": "simulation", "type": "simulation"})
        output_values.append(("simulation", "simulation_result"))

    bom = data.get("bom")
    if bom is not None:
        bom_data = _mapping(bom, "bom")
        bom_kwargs = _call_kwargs(
            bom_data,
            (
                "columns",
                "detail_subassemblies",
                "detail_parts",
                "only_parts",
                "row_overrides",
                "label",
            ),
        )
        lines.append(f"    bom_result = api.bill_of_materials(model{bom_kwargs})")
        expected_outputs.append({"name": "bom", "type": "bom"})
        output_values.append(("bom", "bom_result"))

    lines.append("    return {")
    lines.extend(f"        {name!r}: {value}," for name, value in output_values)
    lines.append("    }")

    return {
        "program_name": program_name,
        "source": "\n".join(lines) + "\n",
        "input_schema": {
            "properties": {
                key: {"type": "object", "x-vibecad-reference": True}
                for key in component_keys
            },
            "required": component_keys,
            "additionalProperties": False,
        },
        "inputs": {
            key: {"catalog_key": _catalog_key(component.get("catalog_key"), f"components[{index}].catalog_key")}
            for index, (key, component) in enumerate(zip(component_keys, components))
        },
        "expected_outputs": expected_outputs,
    }


def assembly_program_tool_spec() -> dict[str, Any]:
    key = {"type": "string", "pattern": _KEY.pattern}
    joint_key = {
        "description": "Stable identifier; defaults from label.",
        **key,
    }
    vector3 = {
        "type": "array",
        "minItems": 3,
        "maxItems": 3,
        "items": {"type": "number"},
    }
    quaternion = {
        "type": "array",
        "minItems": 4,
        "maxItems": 4,
        "items": {"type": "number"},
    }
    placement = {
        "description": "Initial occurrence placement in millimetres.",
        "oneOf": [
            vector3,
            {
                "type": "object",
                "properties": {
                    "position": vector3,
                },
                "required": ["position"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "position": vector3,
                    "rotation": quaternion,
                },
                "required": ["rotation"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "position": vector3,
                    "axis": vector3,
                    "angle_degrees": {"type": "number"},
                },
                "required": ["axis", "angle_degrees"],
                "additionalProperties": False,
            },
        ],
    }
    component = {
        "type": "object",
        "properties": {
            "key": key,
            "catalog_key": {"type": "string", "pattern": _CATALOG_KEY.pattern},
            "placement": placement,
            "flexible_subassembly": {
                "description": "Solve this subassembly's internal joints.",
                "type": "boolean",
            },
            "label": {"type": "string"},
        },
        "required": ["key", "catalog_key"],
        "additionalProperties": False,
    }
    joint_parameters: dict[str, Any] = {}
    for name in (
        "distance_mm",
        "angle_degrees",
        "pitch_radius_mm",
        "thread_pitch_mm",
        "radius1_mm",
        "radius2_mm",
    ):
        joint_parameters[name] = {"type": "number"}
    for name in ("length_limits_mm", "angle_limits_degrees"):
        joint_parameters[name] = {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "items": {"type": ["number", "null"]},
        }
    interface_name = {
        "type": "string",
        "pattern": "^[A-Za-z][A-Za-z0-9_]*$",
    }
    joint_variants = []
    for kind in JOINT_TYPES:
        parameter_names = _ALLOWED_JOINT_PARAMETERS[kind]
        properties = {
            "kind": {"type": "string", "const": kind},
            "suppressed": {"type": "boolean"},
            **{
                name: joint_parameters[name]
                for name in parameter_names
            },
        }
        required_parameters = (
            ()
            if kind in {"gear", "belt"}
            else _REQUIRED_JOINT_PARAMETERS.get(kind, ())
        )
        joint_variants.append(
            {
                "type": "object",
                "properties": properties,
                "required": ["kind", *required_parameters],
                "additionalProperties": False,
            }
        )
    joint_properties: dict[str, Any] = {
        "key": joint_key,
        "first_key": key,
        "first_interface": {
            "description": "Published interface on first_key.",
            **interface_name,
        },
        "second_key": key,
        "second_interface": {
            "description": "Published interface on second_key.",
            **interface_name,
        },
        "joint": {"oneOf": joint_variants},
        "label": {"type": "string"},
    }
    motion = {
        "type": "object",
        "properties": {
            "key": key,
            "joint": key,
            "formula": {
                "description": (
                    "Expression using time, initialValue, pi, abs, asin, arcsin, "
                    "arctan, cos, or sin."
                ),
                "type": "string",
                "minLength": 1,
            },
            "motion_type": {
                "type": "string",
                "enum": ["auto", "angular", "linear"],
            },
            "label": {"type": "string"},
        },
        "required": ["key", "joint", "formula"],
        "additionalProperties": False,
    }
    simulation_properties: dict[str, Any] = {
        "motions": {"type": "array", "minItems": 1, "maxItems": 128, "items": motion},
        "label": {"type": "string"},
        "frames_per_second": {"type": "integer", "minimum": 1},
    }
    for name in (
        "start_time_s",
        "end_time_s",
        "time_step_s",
        "error_tolerance",
    ):
        simulation_properties[name] = {"type": "number"}
    bom = {
        "type": "object",
        "properties": {
            "columns": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "string",
                    "enum": ["index", "name", "quantity", "file_name"],
                },
            },
            "detail_subassemblies": {"type": "boolean"},
            "detail_parts": {"type": "boolean"},
            "only_parts": {
                "description": "Part containers and subassemblies only.",
                "type": "boolean",
            },
            "label": {"type": "string"},
        },
        "additionalProperties": False,
    }
    return {
        "name": "vibescript.create_assembly",
        "description": "Create or replace one complete assembly definition.",
        "parameters": {
            "type": "object",
            "properties": {
                "replace": {
                    "description": "Existing assembly source to replace.",
                    "type": "object",
                    "properties": {
                        "program": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 300,
                            "pattern": "^[^/]+(?:/[^/]+/[^/]+)?$",
                        },
                        "expected_revision": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{64}$",
                        },
                    },
                    "required": ["program", "expected_revision"],
                    "additionalProperties": False,
                },
                "program_name": {
                    "description": "Program label.",
                    "type": "string",
                    "maxLength": 120,
                    "pattern": "^(?:[A-Za-z][A-Za-z0-9 ._-]{0,119})?$",
                },
                "label": {"description": "Assembly label.", "type": "string"},
                "grounded_component_key": {
                    "description": "Stationary root occurrence key.",
                    **key,
                },
                "components": {
                    "description": (
                        "All occurrences; repeat catalog_key for repeated parts."
                    ),
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 256,
                    "items": component,
                },
                "joints": {
                    "description": "All joints between occurrences.",
                    "type": "array",
                    "maxItems": 512,
                    "items": {
                        "type": "object",
                        "properties": joint_properties,
                        "required": [
                            "first_key",
                            "first_interface",
                            "second_key",
                            "second_interface",
                            "joint",
                        ],
                        "additionalProperties": False,
                    },
                },
                "simulation": {
                    "description": (
                        "Optional motion for revolute, slider, or cylindrical joints; "
                        "formulas use time, initialValue, pi, abs, asin, arcsin, "
                        "arctan, cos, or sin."
                    ),
                    "type": "object",
                    "properties": simulation_properties,
                    "required": ["motions"],
                    "additionalProperties": False,
                },
                "bom": {
                    "description": "Optional bill of materials.",
                    **bom,
                },
            },
            "required": ["grounded_component_key", "components", "joints"],
            "additionalProperties": False,
        },
        "safety": "SAFE_WRITE",
        "contextual": True,
        "requires_document": True,
        "edit_modes": ["none"],
    }
