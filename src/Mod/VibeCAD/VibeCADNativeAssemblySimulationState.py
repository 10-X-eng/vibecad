# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded exact state for native Assembly simulation authoring."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from VibeCADNativeAssemblyComponents import assembly_components
from VibeCADNativeAssemblyGrounding import active_grounded_joints
from VibeCADNativeAssemblyJointConnectors import (
    NativeAssemblyJointConnectorError,
    connector_summary,
    placement_summary,
)
from VibeCADNativeAssemblyJointGraph import (
    active_regular_joints,
    require_joint_group,
)
from VibeCADNativeAssemblySolveState import (
    AssemblySolverState,
    capture_assembly_solver_state,
)
from VibeCADNativeTargets import object_reference


MAX_SIMULATION_OPERATIONS = 1_024
MAX_SIMULATION_MOTIONS = 4_096
MAX_SIMULATION_FORMULA_CHARACTERS = 512
MAX_SIMULATION_JOINT_PREVIEW = 32
MAX_SIMULATION_OPERATION_PREVIEW = 16
_MOTION_JOINT_TYPES = frozenset({"Revolute", "Slider", "Cylindrical"})
_BOOL_JOINT_PROPERTIES = (
    "Suppressed",
    "Detach1",
    "Detach2",
    "EnableLengthMin",
    "EnableLengthMax",
    "EnableAngleMin",
    "EnableAngleMax",
)
_NUMBER_JOINT_PROPERTIES = (
    "Distance",
    "Distance2",
    "Angle",
    "LengthMin",
    "LengthMax",
    "AngleMin",
    "AngleMax",
)
_SIMULATION_PARAMETERS = (
    "aTimeStart",
    "bTimeEnd",
    "cTimeStepOutput",
    "fGlobalErrorTolerance",
    "jFramesPerSecond",
)


class NativeAssemblySimulationStateError(RuntimeError):
    """The live simulation-authoring graph cannot be represented exactly."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_SIMULATION_STATE_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class AssemblySimulationJoint:
    obj: Any
    joint_type: str
    supported_motion_types: tuple[str, ...]
    record: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AssemblySimulationState:
    assembly: Any
    joint_group: Any
    components: tuple[Any, ...]
    grounded_joints: tuple[Any, ...]
    regular_joints: tuple[Any, ...]
    eligible_joints: tuple[AssemblySimulationJoint, ...]
    solver_state: AssemblySolverState
    simulation_group: Any | None
    simulations: tuple[Any, ...]
    simulation_records: tuple[dict[str, Any], ...]
    state_sha256: str

    def summary(self) -> dict[str, Any]:
        motion_count = sum(len(record["motions"]) for record in self.simulation_records)
        result: dict[str, Any] = {
            "available": True,
            "state_sha256": self.state_sha256,
            "component_count": len(self.components),
            "grounded_count": len(self.grounded_joints),
            "joint_count": len(self.regular_joints),
            "eligible_joint_count": len(self.eligible_joints),
            "simulation_count": len(self.simulations),
            "motion_count": motion_count,
            "eligible_joints": [
                {
                    **object_reference(item.obj),
                    "label": str(getattr(item.obj, "Label", "") or "")[:160],
                    "joint_type": item.joint_type,
                    "supported_motion_types": list(item.supported_motion_types),
                }
                for item in self.eligible_joints[:MAX_SIMULATION_JOINT_PREVIEW]
            ],
            "simulations": [
                {
                    **dict(record["simulation"]),
                    "label": str(record["label"]),
                    "motion_count": len(record["motions"]),
                    "time_start_seconds": record["parameters"]["time_start_seconds"],
                    "time_end_seconds": record["parameters"]["time_end_seconds"],
                    "output_time_step_seconds": record["parameters"][
                        "output_time_step_seconds"
                    ],
                }
                for record in self.simulation_records[:MAX_SIMULATION_OPERATION_PREVIEW]
            ],
        }
        if len(self.eligible_joints) > MAX_SIMULATION_JOINT_PREVIEW:
            result["eligible_joints_truncated"] = True
        if len(self.simulations) > MAX_SIMULATION_OPERATION_PREVIEW:
            result["simulations_truncated"] = True
        return result


def _live_object(obj: Any, document: Any) -> bool:
    name = str(getattr(obj, "Name", "") or "")
    reader = getattr(document, "getObject", None)
    return bool(
        name
        and getattr(obj, "Document", None) is document
        and callable(reader)
        and reader(name) is obj
    )


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _object_valid(obj: Any) -> bool:
    reader = getattr(obj, "isValid", None)
    if not callable(reader):
        return True
    try:
        return bool(reader())
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _identity_record(obj: Any) -> dict[str, Any]:
    if obj is None:
        raise NativeAssemblySimulationStateError(
            "An Assembly simulation object identity is missing."
        )
    try:
        result = object_reference(obj)
        object_id = int(obj.ID)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeAssemblySimulationStateError(
            "An Assembly simulation object has an invalid identity."
        ) from exc
    if object_id < 0:
        raise NativeAssemblySimulationStateError(
            "An Assembly simulation object has an invalid identity."
        )
    return {**result, "object_id": object_id}


def _reference_record(reference: Any) -> dict[str, Any] | None:
    if reference is None:
        return None
    try:
        component = reference[0]
        paths = tuple(str(path) for path in tuple(reference[1]))
        if (
            component is None
            or len(paths) > 8
            or any(len(path) > 512 for path in paths)
        ):
            raise ValueError
        return {"component": _identity_record(component), "paths": list(paths)}
    except (
        AttributeError,
        IndexError,
        ReferenceError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return {"malformed": True, "value_type": type(reference).__name__[:64]}


def _placement_record(value: Any) -> dict[str, Any]:
    try:
        return placement_summary(value)
    except (
        AttributeError,
        NativeAssemblyJointConnectorError,
        OverflowError,
        ReferenceError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return {"malformed": True, "value_type": type(value).__name__[:64]}


def _joint_property_record(joint: Any) -> dict[str, Any]:
    properties = set(getattr(joint, "PropertiesList", ()) or ())
    result: dict[str, Any] = {}
    for name in _BOOL_JOINT_PROPERTIES:
        if name in properties:
            value = getattr(joint, name, None)
            result[name] = value if type(value) is bool else {"malformed": True}
    for name in _NUMBER_JOINT_PROPERTIES:
        if name not in properties:
            continue
        value = getattr(getattr(joint, name, None), "Value", getattr(joint, name, None))
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            result[name] = {"malformed": True}
        else:
            result[name] = number if math.isfinite(number) else {"malformed": True}
    return result


def _joint_record(joint: Any) -> dict[str, Any]:
    return {
        **_identity_record(joint),
        "label": str(getattr(joint, "Label", "") or "")[:256],
        "joint_type": str(getattr(joint, "JointType", "") or "")[:64],
        "reference1": _reference_record(getattr(joint, "Reference1", None)),
        "reference2": _reference_record(getattr(joint, "Reference2", None)),
        "offset1": _placement_record(getattr(joint, "Offset1", None)),
        "offset2": _placement_record(getattr(joint, "Offset2", None)),
        "properties": _joint_property_record(joint),
    }


def _supported_motion_types(joint_type: str) -> tuple[str, ...]:
    if joint_type == "Revolute":
        return ("angular",)
    if joint_type == "Slider":
        return ("linear",)
    if joint_type == "Cylindrical":
        return ("angular", "linear")
    return ()


def _eligible_joint(
    joint: Any, record: dict[str, Any]
) -> AssemblySimulationJoint | None:
    joint_type = str(getattr(joint, "JointType", "") or "")
    supported = _supported_motion_types(joint_type)
    if (
        not supported
        or bool(getattr(joint, "Suppressed", False))
        or not _object_valid(joint)
    ):
        return None
    try:
        connector_summary(joint.Reference1, joint.Offset1)
        connector_summary(joint.Reference2, joint.Offset2)
    except (
        AttributeError,
        NativeAssemblyJointConnectorError,
        ReferenceError,
        RuntimeError,
        TypeError,
    ):
        return None
    return AssemblySimulationJoint(joint, joint_type, supported, record)


def _grounded_graph(joint_group: Any, assembly: Any) -> tuple[Any, ...]:
    result = []
    for joint in active_grounded_joints(joint_group):
        component = getattr(joint, "ObjectToGround", None)
        try:
            import UtilsAssembly

            owned = bool(
                component is not None
                and _live_object(component, assembly.Document)
                and _timeline_active(component)
                and UtilsAssembly.assemblyOwnsGroundingComponent(
                    assembly,
                    component,
                )
            )
        except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
            owned = False
        if owned:
            result.append(joint)
    return tuple(result)


def _quantity(value: Any, field: str) -> float:
    raw = getattr(value, "Value", value)
    try:
        result = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblySimulationStateError(
            f"An existing Assembly simulation has an invalid {field}."
        ) from exc
    if not math.isfinite(result):
        raise NativeAssemblySimulationStateError(
            f"An existing Assembly simulation has an invalid {field}."
        )
    return result


def _motion_joint(motion: Any) -> Any | None:
    try:
        value = motion.Joint
        return value[0] if value is not None else None
    except (AttributeError, IndexError, ReferenceError, RuntimeError, TypeError):
        return None


def _motion_record(motion: Any, owner: Any, document: Any) -> dict[str, Any]:
    if not _live_object(motion, document) or not _timeline_active(motion):
        raise NativeAssemblySimulationStateError(
            "An existing Assembly simulation contains an inactive or stale motion."
        )
    formula = str(getattr(motion, "Formula", "") or "")
    if len(formula) > MAX_SIMULATION_FORMULA_CHARACTERS:
        raise NativeAssemblySimulationStateError(
            "An existing Assembly motion formula exceeds the Native state bound."
        )
    joint = _motion_joint(motion)
    return {
        "motion": _identity_record(motion),
        "label": str(getattr(motion, "Label", "") or "")[:256],
        "proxy_class": type(getattr(motion, "Proxy", None)).__name__,
        "timeline_role": str(getattr(motion, "VibeCADTimelineRole", "") or ""),
        "timeline_owner": (
            None
            if getattr(motion, "VibeCADTimelineOwner", None) is None
            else _identity_record(motion.VibeCADTimelineOwner)
        ),
        "owner_matches": getattr(motion, "VibeCADTimelineOwner", None) is owner,
        "joint": None if joint is None else _identity_record(joint),
        "motion_type": str(getattr(motion, "MotionType", "") or ""),
        "formula": formula,
    }


def _simulation_record(simulation: Any, document: Any) -> dict[str, Any]:
    if not _live_object(simulation, document) or not _timeline_active(simulation):
        raise NativeAssemblySimulationStateError(
            "Move Assembly History to the end before creating another simulation."
        )
    properties = set(getattr(simulation, "PropertiesList", ()) or ())
    if (
        not set(_SIMULATION_PARAMETERS).issubset(properties)
        or "Group" not in properties
    ):
        raise NativeAssemblySimulationStateError(
            "The Assembly simulation group contains a malformed operation."
        )
    motions = tuple(getattr(simulation, "Group", ()) or ())
    if len(motions) > MAX_SIMULATION_MOTIONS:
        raise NativeAssemblySimulationStateError(
            f"One Assembly simulation exceeds the {MAX_SIMULATION_MOTIONS}-motion Native bound."
        )
    parameters = {
        "time_start_seconds": _quantity(simulation.aTimeStart, "start time"),
        "time_end_seconds": _quantity(simulation.bTimeEnd, "end time"),
        "output_time_step_seconds": _quantity(
            simulation.cTimeStepOutput,
            "output time step",
        ),
        "global_error_tolerance": _quantity(
            simulation.fGlobalErrorTolerance,
            "global error tolerance",
        ),
        "frames_per_second": int(simulation.jFramesPerSecond),
    }
    return {
        "simulation": _identity_record(simulation),
        "label": str(getattr(simulation, "Label", "") or "")[:256],
        "proxy_class": type(getattr(simulation, "Proxy", None)).__name__,
        "timeline_role": str(getattr(simulation, "VibeCADTimelineRole", "") or ""),
        "timeline_edit_command": str(
            getattr(simulation, "VibeCADTimelineEditCommand", "") or ""
        ),
        "parameters": parameters,
        "motions": [_motion_record(motion, simulation, document) for motion in motions],
    }


def _simulation_graph(
    assembly: Any,
) -> tuple[Any | None, tuple[Any, ...], tuple[dict[str, Any], ...]]:
    groups = [
        child
        for child in list(getattr(assembly, "Group", ()) or ())
        if str(getattr(child, "TypeId", "") or "") == "Assembly::SimulationGroup"
    ]
    if len(groups) > 1:
        raise NativeAssemblySimulationStateError(
            "The active Assembly contains multiple simulation groups."
        )
    if not groups:
        return None, (), ()
    group = groups[0]
    document = assembly.Document
    if not _live_object(group, document):
        raise NativeAssemblySimulationStateError(
            "The Assembly simulation group is not one exact live object."
        )
    simulations = tuple(getattr(group, "Group", ()) or ())
    if len(simulations) > MAX_SIMULATION_OPERATIONS:
        raise NativeAssemblySimulationStateError(
            f"The active Assembly exceeds the {MAX_SIMULATION_OPERATIONS}-simulation Native bound."
        )
    records = []
    total_motions = 0
    for simulation in simulations:
        record = _simulation_record(simulation, document)
        total_motions += len(record["motions"])
        if total_motions > MAX_SIMULATION_MOTIONS:
            raise NativeAssemblySimulationStateError(
                f"The active Assembly exceeds the {MAX_SIMULATION_MOTIONS}-motion Native bound."
            )
        records.append(record)
    return group, simulations, tuple(records)


def capture_assembly_simulation_state(assembly: Any) -> AssemblySimulationState:
    """Capture exact solver inputs, driveable joints, and durable simulations."""

    document = getattr(assembly, "Document", None)
    if (
        document is None
        or not _live_object(assembly, document)
        or not _timeline_active(assembly)
    ):
        raise NativeAssemblySimulationStateError(
            "The human-active Assembly is not one exact live History object."
        )
    try:
        joint_group = require_joint_group(assembly)
        components = assembly_components(assembly)
        regular_joints = active_regular_joints(joint_group)
        grounded = _grounded_graph(joint_group, assembly)
        solver_state = capture_assembly_solver_state(assembly)
    except Exception as exc:
        raise NativeAssemblySimulationStateError(
            "The active Assembly solver graph is unavailable for simulation authoring."
        ) from exc
    joint_records = tuple(_joint_record(joint) for joint in regular_joints)
    eligible = tuple(
        candidate
        for joint, record in zip(regular_joints, joint_records, strict=True)
        if (candidate := _eligible_joint(joint, record)) is not None
    )
    simulation_group, simulations, simulation_records = _simulation_graph(assembly)
    canonical = {
        "assembly": _identity_record(assembly),
        "joint_group": _identity_record(joint_group),
        "components": [_identity_record(component) for component in components],
        "grounded": [
            {
                "joint": _identity_record(joint),
                "component": _identity_record(joint.ObjectToGround),
            }
            for joint in grounded
        ],
        "regular_joints": list(joint_records),
        "eligible_joint_names": [str(item.obj.Name) for item in eligible],
        "solver_placement_state_sha256": solver_state.state_sha256,
        "simulation_group": (
            None if simulation_group is None else _identity_record(simulation_group)
        ),
        "simulations": list(simulation_records),
    }
    try:
        encoded = json.dumps(
            canonical,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (OverflowError, TypeError, ValueError) as exc:
        raise NativeAssemblySimulationStateError(
            "The Assembly simulation state cannot be represented exactly."
        ) from exc
    return AssemblySimulationState(
        assembly=assembly,
        joint_group=joint_group,
        components=components,
        grounded_joints=grounded,
        regular_joints=regular_joints,
        eligible_joints=eligible,
        solver_state=solver_state,
        simulation_group=simulation_group,
        simulations=simulations,
        simulation_records=simulation_records,
        state_sha256=hashlib.sha256(encoded).hexdigest(),
    )


def assembly_simulation_state_summary(assembly: Any) -> dict[str, Any]:
    return capture_assembly_simulation_state(assembly).summary()
