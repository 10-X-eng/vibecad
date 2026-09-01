# SPDX-License-Identifier: LGPL-2.1-or-later

"""Project the Manufacture provider from exact per-setup document state."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    NativeProviderSurface,
    _provider_schema_operations,
    project_native_provider_operations,
    project_native_provider_surface,
)


_SHARED = frozenset(
    {
        "state.read",
        "view.control",
        "inspect.query",
        "inspect.compare",
        "document.save",
        "document.undo",
    }
)
_DISCOVERY = frozenset(
    {
        "manufacture.job",
        "manufacture.setups",
        "manufacture.tool_catalog",
    }
)
_SETUP_LIFECYCLE = frozenset(
    {
        "manufacture.tool",
        "manufacture.add_tool",
        "manufacture.program",
        "manufacture.template",
        "manufacture.read_setup",
        "manufacture.setup_options",
        "manufacture.validate",
    }
)
_COMMON_OPERATIONS = frozenset(
    {
        "manufacture.face",
        "manufacture.pocket",
        "manufacture.pocket_3d",
        "manufacture.profile",
        "manufacture.drill",
        "manufacture.surface",
        "manufacture.waterline",
        "manufacture.rotary_surface",
        "manufacture.helix",
        "manufacture.adaptive",
        "manufacture.slot",
        "manufacture.thread_mill",
        "manufacture.engrave",
        "manufacture.deburr",
        "manufacture.v_carve",
    }
)
_OPERATION_EDITS = frozenset(
    {
        "manufacture.array",
        "manufacture.copy_path",
        "manufacture.start_point",
    }
)
_BUSY_SETUP_ALLOWED = frozenset(
    {
        "state.read",
        "native.job",
    }
)
_ACTIVE_SIMULATION_ALLOWED = frozenset(
    {
        "state.read",
        "inspect.query",
        "inspect.compare",
        "manufacture.setups",
        "manufacture.read_setup",
        "manufacture.setup_options",
        "manufacture.validate",
        "manufacture.tool_catalog",
    }
)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _readiness_ready(setup: Mapping[str, Any], name: str) -> bool:
    readiness = setup.get("readiness")
    value = readiness.get(name) if isinstance(readiness, Mapping) else None
    return bool(isinstance(value, Mapping) and value.get("ready") is True)


def _valid_setup(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if not str(value.get("object_name") or ""):
        return None
    state_sha256 = value.get("state_sha256")
    if not isinstance(state_sha256, str) or len(state_sha256) != 64:
        return None
    counts = value.get("counts")
    if not isinstance(counts, Mapping):
        return None
    for name in ("models", "tools", "operations", "active_operations"):
        if _nonnegative_int(counts.get(name)) is None:
            return None
    return value


def _scoped_setups(domain: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...] | None:
    """Return selected/sole setup state, or every complete catalog setup."""

    active = domain.get("active_job")
    if active is not None:
        valid = _valid_setup(active)
        return (valid,) if valid is not None else None

    count = _nonnegative_int(domain.get("job_count"))
    jobs = domain.get("jobs")
    if count is None or not isinstance(jobs, list):
        return None
    if count == 0:
        return () if not jobs and domain.get("jobs_truncated") is not True else None
    if domain.get("jobs_truncated") is True or len(jobs) != count:
        return None
    result = tuple(_valid_setup(value) for value in jobs)
    if any(value is None for value in result):
        return None
    return tuple(value for value in result if value is not None)


def _focused_setup_is_busy(domain: Mapping[str, Any]) -> bool:
    active = _valid_setup(domain.get("active_job"))
    jobs = domain.get("background_jobs")
    if active is None or not isinstance(jobs, list):
        return False
    expected_scope = f"manufacture:{active['object_name']}"
    return any(
        isinstance(job, Mapping)
        and job.get("resource_scope") == expected_scope
        and job.get("terminal") is False
        for job in jobs
    )


def _has_running_background_job(domain: Mapping[str, Any]) -> bool:
    jobs = domain.get("background_jobs")
    return isinstance(jobs, list) and any(
        isinstance(job, Mapping) and job.get("terminal") is False for job in jobs
    )


def _active_simulation(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping) or value.get("mode") != "gl":
        return None
    simulation_id = str(value.get("simulation_id") or "")
    if len(simulation_id) != 32 or any(
        character not in "0123456789abcdef" for character in simulation_id
    ):
        return None
    if not str(value.get("job") or ""):
        return None
    return value


def manufacture_provider_tool_names(
    domain: Mapping[str, Any],
    available_tool_names: Sequence[str],
) -> tuple[str, ...]:
    """Return the useful provider families for current Manufacture state."""

    allowed = set(_SHARED | _DISCOVERY)
    if not isinstance(domain, Mapping) or domain.get("kind") != "manufacture":
        return tuple(name for name in available_tool_names if name in allowed)
    if "active_simulation" in domain:
        allowed = set(_ACTIVE_SIMULATION_ALLOWED)
        if _has_running_background_job(domain):
            allowed.add("native.job")
        if _active_simulation(domain.get("active_simulation")) is not None:
            allowed.add("manufacture.close_simulation")
        return tuple(name for name in available_tool_names if name in allowed)
    if _focused_setup_is_busy(domain):
        allowed = set(_BUSY_SETUP_ALLOWED)
        if _nonnegative_int(domain.get("remaining_stock_result_count")):
            allowed.add("manufacture.remaining_stock")
        return tuple(name for name in available_tool_names if name in allowed)
    result_count = _nonnegative_int(domain.get("remaining_stock_result_count"))
    if _has_running_background_job(domain):
        allowed.add("native.job")
    if result_count:
        allowed.add("manufacture.follow_up_setup")
        allowed.add("manufacture.remaining_stock")
    setups = _scoped_setups(domain)
    if not setups:
        return tuple(name for name in available_tool_names if name in allowed)

    allowed.update(_SETUP_LIFECYCLE)
    if any(
        int(setup["counts"]["models"]) > 0
        and int(setup["counts"]["tools"]) > 0
        for setup in setups
    ):
        allowed.update(
            _COMMON_OPERATIONS
            | {
                "manufacture.probe",
                "manufacture.geometry",
                "manufacture.loop",
                "manufacture.set_controller",
                "manufacture.update_tool",
            }
        )
    if any(int(setup["counts"]["tools"]) > 0 for setup in setups):
        allowed.add("manufacture.threads")
    if any(int(setup["counts"]["operations"]) > 0 for setup in setups):
        allowed.update(
            _OPERATION_EDITS
            | {
                "manufacture.operations",
                "manufacture.dressup",
                "manufacture.toolpath",
            }
        )
    if any(_readiness_ready(setup, "simulation") for setup in setups):
        allowed.update(
            {
                "manufacture.simulation",
                "manufacture.simulation_result",
                "manufacture.camotics",
            }
        )
    if any(_readiness_ready(setup, "post") for setup in setups):
        allowed.update({"manufacture.post_job", "manufacture.post_selected"})
    return tuple(name for name in available_tool_names if name in allowed)


def _operation_scope(
    domain: Mapping[str, Any],
    available_by_tool: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    setups = _scoped_setups(domain)
    has_setup = bool(setups)
    model_count = max(
        (int(setup["counts"]["models"]) for setup in setups or ()),
        default=0,
    )
    operation_count = max(
        (int(setup["counts"]["operations"]) for setup in setups or ()),
        default=0,
    )
    tool_count = max(
        (int(setup["counts"]["tools"]) for setup in setups or ()),
        default=0,
    )
    result: dict[str, tuple[str, ...]] = {}
    catalog = available_by_tool.get("manufacture.tool_catalog")
    if catalog is not None:
        result["manufacture.tool_catalog"] = tuple(
            operation for operation in catalog if operation == "list_tools"
        )
    job = available_by_tool.get("manufacture.job")
    if job is not None:
        allowed = {"create_job", "create_job_from_template"}
        if has_setup:
            allowed.update(
                {"configure_stock", "orient_workpiece", "update_setup"}
            )
        result["manufacture.job"] = tuple(
            operation for operation in job if operation in allowed
        )

    inspect = available_by_tool.get("manufacture.inspect")
    if inspect is not None:
        allowed = {"list_setups"}
        if _nonnegative_int(domain.get("remaining_stock_result_count")):
            allowed.add("list_remaining_stock")
        if has_setup:
            allowed.update({"read_job", "search_setup_options", "validate_job"})
        if tool_count:
            allowed.add("read_thread_catalog")
        if model_count:
            allowed.update({"detect_loop", "read_model_geometry"})
        if operation_count:
            allowed.add("inspect_toolpath")
        result["manufacture.inspect"] = tuple(
            operation for operation in inspect if operation in allowed
        )

    tool = available_by_tool.get("manufacture.tool")
    if tool is not None:
        allowed = {"create_controller"} if has_setup else set()
        if tool_count:
            allowed.update({"update_controller", "update_tool_bit"})
        result["manufacture.tool"] = tuple(
            operation for operation in tool if operation in allowed
        )

    program = available_by_tool.get("manufacture.program")
    if program is not None:
        allowed = {"comment", "stop"} if has_setup else set()
        if tool_count:
            allowed.add("custom")
        result["manufacture.program"] = tuple(
            operation for operation in program if operation in allowed
        )

    operations = available_by_tool.get("manufacture.operation")
    if operations is not None:
        correction = {"set_start_point", "array", "simple_copy"}
        allowed = set(operations) - correction if model_count and tool_count else set()
        if operation_count:
            allowed.update(correction)
        result["manufacture.operation"] = tuple(
            operation for operation in operations if operation in allowed
        )
    return result


def scope_manufacture_provider_surface(
    surface: NativeProviderSurface,
    active_state: Mapping[str, Any],
    *,
    registry: NativeCapabilityRegistry | None = None,
) -> NativeProviderSurface:
    """Project a validated Manufacture surface without changing human ribbon state."""

    if not isinstance(surface, NativeProviderSurface):
        raise TypeError("surface must be a NativeProviderSurface")
    if not surface.available or surface.snapshot.surface_id != "manufacture":
        return surface
    domain = active_state.get("domain") if isinstance(active_state, Mapping) else None
    names = manufacture_provider_tool_names(
        domain if isinstance(domain, Mapping) else {},
        surface.tool_names,
    )
    projected = project_native_provider_surface(surface, names)
    if registry is None:
        return projected
    return project_native_provider_operations(
        projected,
        registry,
        _operation_scope(
            domain if isinstance(domain, Mapping) else {},
            {
                name: _provider_schema_operations(schema)
                for name, schema in zip(
                    projected.tool_names,
                    projected.schemas,
                    strict=True,
                )
            },
        ),
    )
