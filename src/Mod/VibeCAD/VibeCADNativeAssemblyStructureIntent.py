# SPDX-License-Identifier: LGPL-2.1-or-later

"""Capture exact runtime state for small Assembly structure requests."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAssemblyComponents import (
    assembly_components,
    available_component_sources,
)
from VibeCADNativeAssemblyDiagnosisState import capture_assembly_diagnosis_state
from VibeCADNativeAssemblySimulationState import capture_assembly_simulation_state
from VibeCADNativeAssemblyState import assembly_objects, read_active_assembly, same_assembly
from VibeCADNativeAssemblyViewState import capture_assembly_view_state
from VibeCADNativeTargets import NativeObjectRef, NativeTargetError, resolve_object


class NativeAssemblyStructureIntentError(RuntimeError):
    """Assembly structure intent cannot be resolved against the live document."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_STRUCTURE_FAILED",
            "message": str(self),
        }


_IDENTITY_PLACEMENT = {
    "origin_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
    "rotation": {
        "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
        "angle_degrees": 0.0,
    },
}
_SIMULATION_DEFAULTS = {
    "time_start_seconds": 0.0,
    "time_end_seconds": 10.0,
    "output_time_step_seconds": 0.05,
    "global_error_tolerance": 1.0e-6,
    "frames_per_second": 30,
}


def _initial_placement(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise NativeAssemblyStructureIntentError("placement must be an object.")
    return {
        "origin_mm": value.get("origin_mm", _IDENTITY_PLACEMENT["origin_mm"]),
        "rotation": value.get("rotation", _IDENTITY_PLACEMENT["rotation"]),
    }


def _object_ref(document_uid: str, value: Any, field: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeAssemblyStructureIntentError(
            f"{field} must identify one object."
        )
    try:
        return NativeObjectRef(document_uid, str(value["object_name"]))
    except NativeTargetError as exc:
        raise NativeAssemblyStructureIntentError(str(exc)) from exc


def _active_assembly(document: Any, reference: NativeObjectRef) -> Any:
    try:
        assembly = resolve_object(
            document,
            reference,
            expected_types=("Assembly::AssemblyObject",),
        )
        if not same_assembly(assembly, read_active_assembly(document)):
            raise NativeAssemblyStructureIntentError(
                "assembly must identify the active assembly."
            )
        return assembly
    except NativeAssemblyStructureIntentError:
        raise
    except Exception as exc:
        raise NativeAssemblyStructureIntentError(str(exc)) from exc


def _source(
    document: Any,
    assembly: Any,
    value: Any,
) -> tuple[dict[str, Any], str, bool]:
    if not isinstance(value, Mapping) or set(value) != {
        "document_name",
        "object_name",
    }:
        raise NativeAssemblyStructureIntentError(
            "source must identify one available component source."
        )
    matches = [
        item
        for item in available_component_sources(document, assembly)[0]
        if item.get("document_name") == value["document_name"]
        and item.get("object_name") == value["object_name"]
    ]
    if len(matches) != 1:
        raise NativeAssemblyStructureIntentError(
            "source is not an available component source."
        )
    source = matches[0]
    return (
        {
            name: source[name]
            for name in (
                "document_uid",
                "document_name",
                "object_name",
                "object_id",
            )
        },
        str(source.get("label") or source["object_name"]),
        bool(source.get("subassembly")),
    )


def _assembly_state(document: Any, document_uid: str, values: Mapping[str, Any]):
    reference = _object_ref(document_uid, values.get("assembly"), "assembly")
    return reference, _active_assembly(document, reference)


def expand_structure_intent(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Add exact live preconditions to one schema-validated request."""

    if operation == "create_assembly":
        return {
            "label": values["label"],
            "parent_assembly": values.get("parent_assembly"),
            "expected_assembly_count": len(assembly_objects(document)),
        }

    if operation == "create_simulation":
        assembly = read_active_assembly(document)
        if assembly is None:
            raise NativeAssemblyStructureIntentError("No Assembly is active.")
        state = capture_assembly_simulation_state(assembly)
        return {
            "assembly": {"object_name": str(assembly.Name)},
            "label": values.get("label", "Simulation"),
            **{
                name: values.get(name, default)
                for name, default in _SIMULATION_DEFAULTS.items()
            },
            "motions": values["motions"],
            "expected_simulation_state_sha256": state.state_sha256,
            "expected_component_count": len(state.components),
            "expected_grounded_count": len(state.grounded_joints),
            "expected_eligible_joint_count": len(state.eligible_joints),
            "expected_simulation_count": len(state.simulations),
        }

    assembly_ref, assembly = _assembly_state(document, document_uid, values)
    assembly_value = {"object_name": assembly_ref.object_name}
    component_count = len(assembly_components(assembly))

    if operation == "insert_component":
        source, default_label, subassembly = _source(
            document,
            assembly,
            values["source"],
        )
        return {
            "assembly": assembly_value,
            "source": source,
            "label": values.get("label", default_label),
            "placement": _initial_placement(values.get("placement")),
            "rigid": True if subassembly else None,
            "expected_component_count": component_count,
        }
    if operation == "create_part":
        return {
            "assembly": assembly_value,
            "label": values["label"],
            "placement": _initial_placement(values.get("placement")),
            "expected_component_count": component_count,
        }
    if operation in {"make_flexible", "make_rigid"}:
        state = capture_assembly_diagnosis_state(assembly)
        return {
            "assembly": assembly_value,
            "link": values["link"],
            "expected_state_sha256": state.state_sha256,
            "expected_component_count": len(state.components),
            "expected_grounded_count": len(state.grounded_joints),
            "expected_joint_count": len(state.regular_joints),
        }
    if operation == "solve_assembly":
        state = capture_assembly_diagnosis_state(assembly)
        return {
            "assembly": assembly_value,
            "expected_solver_state_sha256": state.solver_state.state_sha256,
            "expected_component_count": len(state.components),
            "expected_grounded_count": len(state.grounded_joints),
            "expected_joint_count": len(state.regular_joints),
        }
    if operation == "create_view":
        state = capture_assembly_view_state(assembly)
        single = bool(values.get("parts_as_single_solid", False))
        return {
            "assembly": assembly_value,
            "label": values["label"],
            "parts_as_single_solid": single,
            "moves": values["moves"],
            "expected_view_state_sha256": state.state_sha256,
            "expected_component_count": state.component_count,
            "expected_target_count": len(state.targets(single)),
            "expected_view_count": len(state.views),
        }
    raise NativeAssemblyStructureIntentError(
        "The Assembly structure operation is unavailable."
    )
