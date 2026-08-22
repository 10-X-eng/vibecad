# SPDX-License-Identifier: LGPL-2.1-or-later

"""Small model-visible index of the exact internal Assembly state."""

from __future__ import annotations

from typing import Any, Mapping


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _identity(value: Any, *, include_type: bool = True) -> dict[str, Any]:
    record = _mapping(value)
    result = {
        name: _text(record.get(name))
        for name in ("object_name", "label")
        if _text(record.get(name))
    }
    if include_type and _text(record.get("type_id")):
        result["type_id"] = _text(record.get("type_id"))
    return result


def _endpoint(value: Any) -> dict[str, str]:
    record = _mapping(value)
    component = _text(_mapping(record.get("component")).get("object_name"))
    element = _text(record.get("element_path"))
    return {
        name: item
        for name, item in (("component", component), ("element", element))
        if item
    }


def _joint(value: Any) -> dict[str, Any]:
    record = _mapping(value)
    result = _identity(record, include_type=False)
    for name in ("joint_type", "suppressed"):
        if name in record:
            result[name] = record[name]
    for name in ("first", "second"):
        endpoint = _endpoint(record.get(name))
        if endpoint:
            result[name] = endpoint
    for source in ("angular_limits", "linear_limits", "cylindrical_limits"):
        limits = record.get(source)
        if isinstance(limits, Mapping):
            result["limits"] = dict(limits)
            break
    return result


def _component(value: Any) -> dict[str, Any]:
    record = _mapping(value)
    result = _identity(record)
    if "grounded" in record:
        result["grounded"] = bool(record["grounded"])
    if "rigid" in record:
        result["rigid"] = bool(record["rigid"])
    fastener = record.get("standard_fastener")
    if isinstance(fastener, Mapping):
        result["standard_fastener"] = {
            name: fastener[name]
            for name in (
                "part_number",
                "standard",
                "nominal_thread",
                "length_mm",
                "model_thread",
                "left_handed",
            )
            if name in fastener
            and not (name == "length_mm" and fastener[name] is None)
        }
        result["standard_fastener"]["catalog_option_overrides"] = dict(
            fastener.get("options")
            or fastener.get("catalog_option_overrides")
            or {}
        )
    return result


def _count(value: Any) -> int:
    return int(value) if type(value) is int and value >= 0 else 0


def _robot(value: Any) -> dict[str, Any]:
    record = _mapping(value)
    result = _identity(record.get("object"))
    if _text(record.get("label")):
        result["label"] = _text(record.get("label"))
    for name in ("axes_degrees", "home_degrees"):
        values = record.get(name)
        if isinstance(values, list):
            result[name] = list(values[:6])
    tool_shape = _identity(record.get("tool_shape"))
    if tool_shape:
        result["tool_shape"] = tool_shape
    for name in ("suppressed", "valid"):
        if name in record:
            result[name] = bool(record[name])
    return result


def _tool_shape(value: Any) -> dict[str, Any]:
    record = _mapping(value)
    result = _identity(record.get("object"))
    if _text(record.get("label")):
        result["label"] = _text(record.get("label"))
    geometry = _mapping(record.get("geometry"))
    if _text(geometry.get("kind")):
        result["kind"] = _text(geometry.get("kind"))
    return result


def _trajectory(value: Any) -> dict[str, Any]:
    record = _mapping(value)
    result = _identity(record.get("object"))
    if _text(record.get("label")):
        result["label"] = _text(record.get("label"))
    feature = _mapping(record.get("feature"))
    if _text(feature.get("kind")):
        result["kind"] = _text(feature.get("kind"))
    for name in ("waypoint_count", "length_mm", "duration_seconds"):
        if name in record:
            result[name] = record[name]
    for name in ("suppressed", "valid", "usable_at_history"):
        if name in record:
            result[name] = bool(record[name])
    return result


def _motion_study(value: Any) -> dict[str, Any]:
    record = _mapping(value)
    result = _identity(record, include_type=False)
    for name in (
        "motion_count",
        "time_start_seconds",
        "time_end_seconds",
        "output_time_step_seconds",
    ):
        if name in record:
            result[name] = record[name]
    return result


def _robot_context(
    setup: Mapping[str, Any],
    tools: Mapping[str, Any],
    defaults: Mapping[str, Any],
    trajectories: Mapping[str, Any],
) -> dict[str, Any]:
    motion = _mapping(defaults.get("motion"))
    orientation = _mapping(defaults.get("orientation"))
    waypoint_defaults = {
        name: motion[name]
        for name in (
            "speed_mm_per_s",
            "continuous",
            "acceleration_mm_per_s2",
        )
        if name in motion
    }
    if orientation:
        waypoint_defaults["orientation"] = {
            name: list(value)
            for name, value in orientation.items()
            if name in {"displacement_mm", "quaternion_xyzw"}
            and isinstance(value, list)
        }
    return {
        "robot_count": _count(setup.get("robot_count")),
        "robots": [_robot(item) for item in list(setup.get("robots") or ())[:32]],
        "tool_shape_candidate_count": _count(tools.get("candidate_count")),
        "tool_shape_candidates": [
            _tool_shape(item) for item in list(tools.get("candidates") or ())[:64]
        ],
        "trajectory_count": _count(trajectories.get("trajectory_count")),
        "waypoint_count": _count(trajectories.get("waypoint_count")),
        "trajectories": [
            _trajectory(item)
            for item in list(trajectories.get("trajectories") or ())[:16]
        ],
        "waypoint_defaults": waypoint_defaults,
    }


def _assembly(value: Any) -> dict[str, Any]:
    record = _mapping(value)
    result = _identity(record, include_type=False)
    if "active" in record:
        result["active"] = bool(record["active"])
    state = record.get("state")
    if isinstance(state, list) and state:
        result["state"] = [_text(item) for item in state if _text(item)][:8]
    counts = _mapping(record.get("counts"))
    result["counts"] = {
        name: _count(counts.get(name))
        for name in ("components", "joints", "grounded")
    }
    result["components"] = [
        _component(item) for item in list(record.get("components") or ())[:32]
    ]
    result["joints"] = [
        _joint(item) for item in list(record.get("joints") or ())[:32]
    ]

    solver = _mapping(record.get("solver_health"))
    conflicts = _mapping(solver.get("conflict_counts"))
    result["solver"] = {
        "remaining_degrees_of_freedom": _count(
            solver.get("remaining_degrees_of_freedom")
        ),
        "conflicts": {
            name: _count(conflicts.get(name))
            for name in (
                "conflicting",
                "redundant",
                "partially_redundant",
                "malformed",
            )
        },
    }
    simulation_state = _mapping(record.get("simulation_state"))
    result["artifacts"] = {
        "boms": _count(_mapping(record.get("bom_state")).get("bom_count")),
        "views": _count(_mapping(record.get("view_state")).get("view_count")),
        "simulations": _count(simulation_state.get("simulation_count")),
    }
    result["motion_studies"] = [
        _motion_study(item)
        for item in list(simulation_state.get("simulations") or ())[:16]
    ]
    playback = _mapping(record.get("simulation_playback"))
    result["playback"] = {"active": playback.get("active") is True}
    if playback.get("active") is True:
        result["playback"].update({
            "playback_id": _text(playback.get("playback_id")),
            "simulation": _identity(
                playback.get("simulation"),
                include_type=False,
            ),
            "time_seconds": playback.get("time_seconds"),
            "playing": bool(playback.get("playing")),
            "direction": _text(playback.get("direction")),
        })
    return result


def provider_assembly_state(value: Any) -> dict[str, Any]:
    """Return only identities and facts useful for choosing the next tool."""

    record = _mapping(value)
    active = _text(_mapping(record.get("active_assembly")).get("object_name"))
    sources = record.get("available_component_sources")
    source_count = len(sources) if isinstance(sources, list) else 0
    robot_setup = _mapping(record.get("robot_setup"))
    robot_tools = _mapping(record.get("robot_tool_shapes"))
    robot_defaults = _mapping(record.get("robot_waypoint_defaults"))
    trajectories = _mapping(record.get("robot_trajectories"))
    result: dict[str, Any] = {
        "kind": "assembly",
        "assembly_count": _count(record.get("assembly_count")),
    }
    if active:
        result["active_assembly"] = {"object_name": active}
    result["assemblies"] = [
        _assembly(item) for item in list(record.get("assemblies") or ())[:16]
    ]
    result["available_component_source_count"] = source_count
    result["available_component_sources"] = [
        {
            name: item[name]
            for name in (
                "document_name",
                "object_name",
                "label",
                "subassembly",
            )
            if name in item and item[name] not in (None, "")
        }
        for item in list(sources or ())[:48]
        if isinstance(item, Mapping)
    ]
    result["robot"] = _robot_context(
        robot_setup,
        robot_tools,
        robot_defaults,
        trajectories,
    )
    return result
