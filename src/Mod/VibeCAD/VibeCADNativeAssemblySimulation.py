# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact atomic creation of one native Assembly simulation graph."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable

from VibeCADNativeAssemblyJointConnectors import placement_is_same
from VibeCADNativeAssemblySimulationState import (
    MAX_SIMULATION_FORMULA_CHARACTERS,
    AssemblySimulationJoint,
    AssemblySimulationState,
    capture_assembly_simulation_state,
)
from VibeCADNativeAssemblySolveState import (
    AssemblySolverState,
    capture_assembly_solver_state,
)
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    read_current_selection,
    resolve_object,
)


NATIVE_ASSEMBLY_SIMULATION_FAILED = "NATIVE_ASSEMBLY_SIMULATION_FAILED"
MAX_SIMULATION_REQUEST_MOTIONS = 256
MAX_SIMULATION_OUTPUT_INTERVALS = 10_000


class NativeAssemblySimulationError(NativeMutationError):
    """The requested Assembly simulation could not be created exactly."""

    def __init__(self, message: str) -> None:
        super().__init__(NATIVE_ASSEMBLY_SIMULATION_FAILED, message)


@dataclass(frozen=True, slots=True)
class AssemblySimulationMotionSpec:
    joint_ref: NativeObjectRef
    motion_type: str
    formula: str


@dataclass(frozen=True, slots=True)
class AssemblySimulationCreateSpec:
    assembly_ref: NativeObjectRef
    label: str
    time_start_seconds: float
    time_end_seconds: float
    output_time_step_seconds: float
    global_error_tolerance: float
    frames_per_second: int
    motions: tuple[AssemblySimulationMotionSpec, ...]
    expected_simulation_state_sha256: str
    expected_component_count: int
    expected_grounded_count: int
    expected_eligible_joint_count: int
    expected_simulation_count: int


@dataclass(frozen=True, slots=True)
class PreparedAssemblySimulationMotion:
    spec: AssemblySimulationMotionSpec
    joint: AssemblySimulationJoint


@dataclass(frozen=True, slots=True)
class PreparedAssemblySimulation:
    spec: AssemblySimulationCreateSpec
    state: AssemblySimulationState
    motions: tuple[PreparedAssemblySimulationMotion, ...]
    planned_output_interval_count: int
    active_before: Any
    selection_before: dict[str, Any]


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _exact_digest(value: Any) -> str:
    digest = str(value or "")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise NativeAssemblySimulationError(
            "expected_simulation_state_sha256 must be one lowercase SHA-256 digest."
        )
    return digest


def _exact_count(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeAssemblySimulationError(
            f"{field} must be an integer from 0 through {maximum}."
        )
    return value


def _finite(value: Any, field: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise NativeAssemblySimulationError(
            f"{field} must be a finite number from {minimum:g} through {maximum:g}."
        )
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblySimulationError(
            f"{field} must be a finite number from {minimum:g} through {maximum:g}."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeAssemblySimulationError(
            f"{field} must be a finite number from {minimum:g} through {maximum:g}."
        )
    return result


def _validate_formula(value: Any) -> str:
    if not isinstance(value, str):
        raise NativeAssemblySimulationError("Every motion formula must be text.")
    formula = value.strip()
    if not formula or len(formula) > MAX_SIMULATION_FORMULA_CHARACTERS:
        raise NativeAssemblySimulationError(
            f"Every motion formula must contain 1 through {MAX_SIMULATION_FORMULA_CHARACTERS} characters."
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in formula):
        raise NativeAssemblySimulationError(
            "Every motion formula must be one printable line."
        )
    return formula


def _validate_parameters(spec: AssemblySimulationCreateSpec) -> int:
    start = _finite(
        spec.time_start_seconds,
        "time_start_seconds",
        minimum=-1_000_000.0,
        maximum=1_000_000.0,
    )
    end = _finite(
        spec.time_end_seconds,
        "time_end_seconds",
        minimum=-1_000_000.0,
        maximum=1_000_000.0,
    )
    step = _finite(
        spec.output_time_step_seconds,
        "output_time_step_seconds",
        minimum=1.0e-9,
        maximum=1_000_000.0,
    )
    _finite(
        spec.global_error_tolerance,
        "global_error_tolerance",
        minimum=1.0e-12,
        maximum=1.0,
    )
    if end <= start:
        raise NativeAssemblySimulationError(
            "time_end_seconds must be greater than time_start_seconds."
        )
    if (
        type(spec.frames_per_second) is not int
        or not 1 <= spec.frames_per_second <= 240
    ):
        raise NativeAssemblySimulationError(
            "frames_per_second must be an integer from 1 through 240."
        )
    intervals = int(math.ceil((end - start) / step))
    if not 1 <= intervals <= MAX_SIMULATION_OUTPUT_INTERVALS:
        raise NativeAssemblySimulationError(
            f"The simulation must request 1 through {MAX_SIMULATION_OUTPUT_INTERVALS} output intervals."
        )
    return intervals


def _exact_active_assembly(
    document: Any,
    spec: AssemblySimulationCreateSpec,
    active_reader: Callable[[Any], Any | None],
) -> Any:
    try:
        assembly = resolve_object(
            document,
            spec.assembly_ref,
            expected_types=("Assembly::AssemblyObject",),
        )
    except Exception as exc:
        raise NativeAssemblySimulationError(str(exc)) from exc
    if not same_assembly(assembly, active_reader(document)) or not _timeline_active(
        assembly
    ):
        raise NativeAssemblySimulationError(
            "The human-active Assembly changed; read current Assemble state and retry."
        )
    return assembly


def _resolve_motions(
    document: Any,
    state: AssemblySimulationState,
    specs: tuple[AssemblySimulationMotionSpec, ...],
) -> tuple[PreparedAssemblySimulationMotion, ...]:
    if (
        not isinstance(specs, tuple)
        or not 1 <= len(specs) <= MAX_SIMULATION_REQUEST_MOTIONS
    ):
        raise NativeAssemblySimulationError(
            f"A simulation requires 1 through {MAX_SIMULATION_REQUEST_MOTIONS} ordered motions."
        )
    eligible = {str(item.obj.Name): item for item in state.eligible_joints}
    result = []
    used: set[tuple[str, str]] = set()
    for item in specs:
        if not isinstance(item, AssemblySimulationMotionSpec):
            raise TypeError("Every motion must be an AssemblySimulationMotionSpec")
        if item.motion_type not in {"angular", "linear"}:
            raise NativeAssemblySimulationError(
                "Every motion_type must be angular or linear."
            )
        try:
            joint = resolve_object(document, item.joint_ref)
        except Exception as exc:
            raise NativeAssemblySimulationError(str(exc)) from exc
        candidate = eligible.get(str(getattr(joint, "Name", "") or ""))
        if candidate is None or candidate.obj is not joint:
            raise NativeAssemblySimulationError(
                "A requested motion joint is not currently driveable in the human-active Assembly."
            )
        if item.motion_type not in candidate.supported_motion_types:
            raise NativeAssemblySimulationError(
                f"A {candidate.joint_type} joint does not support a {item.motion_type} motion."
            )
        key = (str(joint.Name), item.motion_type)
        if key in used:
            raise NativeAssemblySimulationError(
                "A simulation cannot repeat the same motion type on one joint."
            )
        used.add(key)
        normalized = AssemblySimulationMotionSpec(
            item.joint_ref,
            item.motion_type,
            _validate_formula(item.formula),
        )
        result.append(PreparedAssemblySimulationMotion(normalized, candidate))
    return tuple(result)


def preflight_create_assembly_simulation(
    document: Any,
    spec: AssemblySimulationCreateSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> PreparedAssemblySimulation:
    """Freeze one exact active Assembly and complete ordered motion request."""

    if not isinstance(spec, AssemblySimulationCreateSpec):
        raise TypeError("spec must be an AssemblySimulationCreateSpec")
    if not isinstance(spec.assembly_ref, NativeObjectRef):
        raise TypeError("spec.assembly_ref must be a NativeObjectRef")
    if not isinstance(spec.label, str) or not 1 <= len(spec.label.strip()) <= 160:
        raise NativeAssemblySimulationError(
            "A simulation label must contain 1 to 160 characters."
        )
    expected_digest = _exact_digest(spec.expected_simulation_state_sha256)
    expected_components = _exact_count(
        spec.expected_component_count,
        "expected_component_count",
        100_000,
    )
    expected_grounded = _exact_count(
        spec.expected_grounded_count,
        "expected_grounded_count",
        256,
    )
    expected_eligible = _exact_count(
        spec.expected_eligible_joint_count,
        "expected_eligible_joint_count",
        256,
    )
    expected_simulations = _exact_count(
        spec.expected_simulation_count,
        "expected_simulation_count",
        1_024,
    )
    intervals = _validate_parameters(spec)
    assembly = _exact_active_assembly(document, spec, active_reader)
    try:
        state = capture_assembly_simulation_state(assembly)
    except Exception as exc:
        raise NativeAssemblySimulationError(str(exc)) from exc
    if (
        len(state.components) != expected_components
        or len(state.grounded_joints) != expected_grounded
        or len(state.eligible_joints) != expected_eligible
        or len(state.simulations) != expected_simulations
    ):
        raise NativeAssemblySimulationError(
            "The active Assembly simulation counts changed; read current Assemble state and retry."
        )
    if state.state_sha256 != expected_digest:
        raise NativeAssemblySimulationError(
            "The active Assembly simulation state changed; read current Assemble state and retry."
        )
    if len(state.components) < 2:
        raise NativeAssemblySimulationError(
            "An Assembly simulation requires at least two active components."
        )
    if not state.grounded_joints:
        raise NativeAssemblySimulationError(
            "Ground at least one Assembly component before creating a simulation."
        )
    if not state.eligible_joints:
        raise NativeAssemblySimulationError(
            "Create an active Revolute, Slider, or Cylindrical joint before creating a simulation."
        )
    motions = _resolve_motions(document, state, spec.motions)
    selection = selection_reader(document)
    if not same_assembly(assembly, active_reader(document)):
        raise NativeAssemblySimulationError(
            "The human-active Assembly changed during simulation preflight."
        )
    return PreparedAssemblySimulation(
        spec=spec,
        state=state,
        motions=motions,
        planned_output_interval_count=intervals,
        active_before=assembly,
        selection_before=selection,
    )


def _create_simulation_feature(assembly: Any) -> tuple[Any, Any]:
    import CommandCreateSimulation
    import UtilsAssembly

    group = UtilsAssembly.getSimulationGroup(assembly)
    simulation = group.newObject("App::FeaturePython", "Simulation")
    CommandCreateSimulation.Simulation(simulation)
    CommandCreateSimulation.ViewProviderSimulation(simulation.ViewObject)
    return group, simulation


def _create_motion_feature(
    assembly: Any,
    prepared: PreparedAssemblySimulationMotion,
) -> Any:
    import CommandCreateSimulation

    native_type = "Angular" if prepared.spec.motion_type == "angular" else "Linear"
    motion = assembly.newObject("App::FeaturePython", "Motion")
    CommandCreateSimulation.Motion(
        motion,
        native_type,
        prepared.joint.obj,
        prepared.spec.formula,
    )
    CommandCreateSimulation.ViewProviderMotion(motion.ViewObject)
    return motion


def _finalize_simulation(
    document: Any, simulation: Any, motions: tuple[Any, ...]
) -> None:
    document.finalizeProvisionalTimelineOperationBlock(
        simulation,
        [*motions, simulation],
    )


def _new_document_objects(document: Any, before: tuple[Any, ...]) -> tuple[Any, ...]:
    identities = {id(obj) for obj in before}
    return tuple(
        obj
        for obj in list(getattr(document, "Objects", ()) or ())
        if id(obj) not in identities
    )


def create_assembly_simulation(
    document: Any,
    spec: AssemblySimulationCreateSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
    simulation_factory: Callable[[Any], tuple[Any, Any]] = _create_simulation_feature,
    motion_factory: Callable[[Any, PreparedAssemblySimulationMotion], Any] = (
        _create_motion_feature
    ),
    finalizer: Callable[[Any, Any, tuple[Any, ...]], None] = _finalize_simulation,
) -> NativeMutationDraft:
    """Create one accepted human-equivalent simulation graph in one transaction."""

    prepared = preflight_create_assembly_simulation(
        document,
        spec,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    before_objects = tuple(document.Objects)
    group, simulation = simulation_factory(prepared.state.assembly)
    if (
        group is None
        or simulation is None
        or getattr(group, "Document", None) is not document
        or getattr(simulation, "Document", None) is not document
        or str(getattr(group, "TypeId", "") or "") != "Assembly::SimulationGroup"
        or str(getattr(simulation, "TypeId", "") or "") != "App::FeaturePython"
        or type(getattr(simulation, "Proxy", None)).__name__ != "Simulation"
    ):
        raise NativeAssemblySimulationError(
            "The native Assembly simulation factory returned the wrong operation graph."
        )
    simulation.Label = spec.label.strip()
    simulation.aTimeStart = float(spec.time_start_seconds)
    simulation.bTimeEnd = float(spec.time_end_seconds)
    simulation.cTimeStepOutput = float(spec.output_time_step_seconds)
    simulation.fGlobalErrorTolerance = float(spec.global_error_tolerance)
    simulation.jFramesPerSecond = int(spec.frames_per_second)
    motions = []
    for requested in prepared.motions:
        motion = motion_factory(prepared.state.assembly, requested)
        if (
            motion is None
            or getattr(motion, "Document", None) is not document
            or str(getattr(motion, "TypeId", "") or "") != "App::FeaturePython"
            or type(getattr(motion, "Proxy", None)).__name__ != "Motion"
        ):
            raise NativeAssemblySimulationError(
                "The native Assembly motion factory returned the wrong resource object."
            )
        motions.append(motion)
    motion_tuple = tuple(motions)
    simulation.Group = list(motion_tuple)
    try:
        import UtilsAssembly

        for motion in motion_tuple:
            UtilsAssembly.markTimelineResource(motion, simulation)
    except (ImportError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeAssemblySimulationError(
            "The Assembly motions could not be assigned to their simulation operation."
        ) from exc
    finalizer(document, simulation, motion_tuple)
    created_objects = _new_document_objects(document, before_objects)
    semantic_objects: tuple[Any, ...] = (*motion_tuple, simulation)
    if prepared.state.simulation_group is None:
        semantic_objects = (group, *semantic_objects)
    semantic = set(semantic_objects)
    if not semantic.issubset(set(created_objects)) or any(
        obj not in semantic
        and str(getattr(obj, "TypeId", "") or "") != "App::DocumentTimeline"
        for obj in created_objects
    ):
        raise NativeAssemblySimulationError(
            "Simulation creation changed objects outside its exact native graph."
        )
    changed = [prepared.state.assembly]
    if prepared.state.simulation_group is not None:
        changed.append(group)
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "before_objects": before_objects,
            "created_objects": created_objects,
            "simulation_group": group,
            "simulation": simulation,
            "motions": motion_tuple,
        },
        recompute_targets=(*motion_tuple, simulation, group, prepared.state.assembly),
        created=tuple(object_identity(obj) for obj in semantic_objects),
        changed=tuple(object_identity(obj) for obj in changed),
    )


def _same_solver_state(
    expected: AssemblySolverState, current: AssemblySolverState
) -> bool:
    if len(expected.records) != len(current.records):
        return False
    for before, after in zip(expected.records, current.records, strict=True):
        if (
            before.obj is not after.obj
            or int(before.obj.ID) != int(after.obj.ID)
            or str(before.obj.TypeId) != str(after.obj.TypeId)
            or not placement_is_same(before.placement, after.placement)
            or before.placement_locks != after.placement_locks
        ):
            return False
    return True


def _linked_joint(motion: Any) -> Any | None:
    try:
        return motion.Joint[0] if motion.Joint is not None else None
    except (AttributeError, IndexError, ReferenceError, RuntimeError, TypeError):
        return None


def _timeline_block(document: Any, motions: tuple[Any, ...], simulation: Any) -> bool:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "") or "") != (
        "App::DocumentTimeline"
    ):
        return False
    operations = list(getattr(timeline, "Operations", ()) or ())
    accepted_visibility = list(getattr(timeline, "VisibilityAtEnd", ()) or ())
    if len(accepted_visibility) != len(operations):
        return False
    block = [*motions, simulation]
    try:
        start = operations.index(motions[0])
    except (IndexError, ValueError):
        return False
    if operations[start : start + len(block)] != block:
        return False
    return all(
        bool(accepted_visibility[start + offset])
        == bool(getattr(obj, "Visibility", True))
        for offset, obj in enumerate(block)
    )


def _verify_motion(
    requested: PreparedAssemblySimulationMotion,
    motion: Any,
    simulation: Any,
) -> bool:
    native_type = "Angular" if requested.spec.motion_type == "angular" else "Linear"
    try:
        return (
            str(motion.TypeId) == "App::FeaturePython"
            and type(motion.Proxy).__name__ == "Motion"
            and type(motion.ViewObject.Proxy).__name__ == "ViewProviderMotion"
            and _timeline_active(motion)
            and str(motion.VibeCADTimelineRole) == "resource"
            and motion.VibeCADTimelineOwner is simulation
            and _linked_joint(motion) is requested.joint.obj
            and str(motion.MotionType) == native_type
            and str(motion.Formula) == requested.spec.formula
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _verify_parameter_properties(simulation: Any) -> bool:
    expected = {
        "aTimeStart": "App::PropertyTime",
        "bTimeEnd": "App::PropertyTime",
        "cTimeStepOutput": "App::PropertyTime",
        "fGlobalErrorTolerance": "App::PropertyFloat",
        "jFramesPerSecond": "App::PropertyInteger",
    }
    try:
        return all(
            str(simulation.getTypeIdOfProperty(name)) == type_id
            for name, type_id in expected.items()
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def verify_created_assembly_simulation(
    document: Any,
    draft: NativeMutationDraft,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> dict[str, Any]:
    """Prove the exact graph, parameters, History block, and unchanged mechanism."""

    value = draft.value
    prepared: PreparedAssemblySimulation = value["prepared"]
    before = prepared.state
    assembly = before.assembly
    group = value["simulation_group"]
    simulation = value["simulation"]
    motions = tuple(value["motions"])
    if (
        document.getObject(str(assembly.Name)) is not assembly
        or document.getObject(str(group.Name)) is not group
        or document.getObject(str(simulation.Name)) is not simulation
        or not same_assembly(prepared.active_before, active_reader(document))
        or selection_reader(document) != prepared.selection_before
        or str(getattr(group, "TypeId", "") or "") != "Assembly::SimulationGroup"
        or group not in list(getattr(assembly, "Group", ()) or ())
        or list(getattr(group, "Group", ()) or ())[-1:] != [simulation]
        or str(getattr(simulation, "TypeId", "") or "") != "App::FeaturePython"
        or type(getattr(simulation, "Proxy", None)).__name__ != "Simulation"
        or type(
            getattr(getattr(simulation, "ViewObject", None), "Proxy", None)
        ).__name__
        != "ViewProviderSimulation"
        or str(getattr(simulation, "Label", "") or "") != prepared.spec.label.strip()
        or str(getattr(simulation, "VibeCADTimelineRole", "") or "") != "operation"
        or str(getattr(simulation, "VibeCADTimelineEditCommand", "") or "")
        != "Assembly_EditHistoryOperation"
        or not _timeline_active(simulation)
        or list(getattr(simulation, "Group", ()) or ()) != list(motions)
        or len(motions) != len(prepared.motions)
        or not _verify_parameter_properties(simulation)
        or not all(
            _verify_motion(requested, motion, simulation)
            for requested, motion in zip(prepared.motions, motions, strict=True)
        )
        or not _timeline_block(document, motions, simulation)
    ):
        raise NativeAssemblySimulationError(
            "The simulation failed its exact graph, parameter, History, or human-state postcondition."
        )
    expected_parameters = (
        prepared.spec.time_start_seconds,
        prepared.spec.time_end_seconds,
        prepared.spec.output_time_step_seconds,
        prepared.spec.global_error_tolerance,
    )
    actual_parameters = (
        float(simulation.aTimeStart.Value),
        float(simulation.bTimeEnd.Value),
        float(simulation.cTimeStepOutput.Value),
        float(simulation.fGlobalErrorTolerance),
    )
    if (
        any(
            not math.isclose(actual, expected, rel_tol=1.0e-12, abs_tol=1.0e-12)
            for actual, expected in zip(
                actual_parameters, expected_parameters, strict=True
            )
        )
        or int(simulation.jFramesPerSecond) != prepared.spec.frames_per_second
    ):
        raise NativeAssemblySimulationError(
            "The simulation parameters changed before commit."
        )
    created = tuple(value["created_objects"])
    if _new_document_objects(document, tuple(value["before_objects"])) != created:
        raise NativeAssemblySimulationError(
            "The simulation document graph changed after native creation."
        )
    try:
        current = capture_assembly_simulation_state(assembly)
        current_solver = capture_assembly_solver_state(assembly)
    except Exception as exc:
        raise NativeAssemblySimulationError(
            "The Assembly simulation state could not be read before commit."
        ) from exc
    if (
        current.components != before.components
        or current.grounded_joints != before.grounded_joints
        or current.regular_joints != before.regular_joints
        or tuple(item.obj for item in current.eligible_joints)
        != tuple(item.obj for item in before.eligible_joints)
        or not _same_solver_state(before.solver_state, current_solver)
        or current.simulation_group is not group
        or current.simulations != (*before.simulations, simulation)
        or current.simulation_records[:-1] != before.simulation_records
    ):
        raise NativeAssemblySimulationError(
            "Simulation creation changed the Assembly mechanism or prior simulation graph."
        )
    record = current.simulation_records[-1]
    if record["simulation"]["object_name"] != str(simulation.Name) or len(
        record["motions"]
    ) != len(motions):
        raise NativeAssemblySimulationError(
            "The created simulation graph changed before commit."
        )
    angular_count = sum(
        motion.spec.motion_type == "angular" for motion in prepared.motions
    )
    linear_count = len(prepared.motions) - angular_count
    return {
        "operation": "create_simulation",
        "assembly": object_reference(assembly),
        "simulation_group": object_reference(group),
        "simulation": object_reference(simulation),
        "label": str(simulation.Label),
        "component_count": len(current.components),
        "grounded_count": len(current.grounded_joints),
        "eligible_joint_count": len(current.eligible_joints),
        "simulation_count": len(current.simulations),
        "motion_count": len(motions),
        "angular_motion_count": angular_count,
        "linear_motion_count": linear_count,
        "planned_output_interval_count": prepared.planned_output_interval_count,
        "time_start_seconds": float(simulation.aTimeStart.Value),
        "time_end_seconds": float(simulation.bTimeEnd.Value),
        "output_time_step_seconds": float(simulation.cTimeStepOutput.Value),
        "global_error_tolerance": float(simulation.fGlobalErrorTolerance),
        "frames_per_second": int(simulation.jFramesPerSecond),
        "motions": [
            {
                "motion": object_reference(motion),
                "joint": object_reference(requested.joint.obj),
                "motion_type": requested.spec.motion_type,
            }
            for requested, motion in zip(prepared.motions, motions, strict=True)
        ],
        "simulation_state_sha256": current.state_sha256,
        "kinematics_generated": False,
        "active_assembly_unchanged": True,
        "selection_unchanged": True,
        "assembly_placements_unchanged": True,
    }
