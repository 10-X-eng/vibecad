# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded state behind Native Assembly solver-diagnosis reads."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeAssemblyComponents import (
    NativeAssemblyComponentError,
    assembly_components,
)
from VibeCADNativeAssemblyGrounding import (
    NativeAssemblyGroundingError,
    active_grounded_joints,
)
from VibeCADNativeAssemblyJointConnectors import (
    NativeAssemblyJointConnectorError,
    placement_summary,
)
from VibeCADNativeAssemblyJointGraph import (
    MAX_ASSEMBLY_JOINTS,
    NativeAssemblyJointGraphError,
    active_regular_joints,
    require_joint_group,
)
from VibeCADNativeAssemblySolveState import (
    AssemblySolverState,
    NativeAssemblySolveStateError,
    capture_assembly_solver_state,
)


MAX_DIAGNOSTIC_CONSTRAINTS_PER_JOINT = 32
MAX_SOLVER_MESSAGE_LENGTH = 512
_DIAGNOSTIC_CATEGORIES = (
    "conflicting",
    "redundant",
    "partially_redundant",
    "malformed",
)
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


class NativeAssemblyDiagnosisError(RuntimeError):
    """The most recent exact Assembly solver diagnosis is unavailable."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_DIAGNOSIS_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class SolverConstraintDiagnosis:
    specification: str
    residual: float
    redundant: bool


@dataclass(frozen=True, slots=True)
class SolverJointDiagnosis:
    joint: Any
    status: str
    constraint_count: int
    redundant_constraint_count: int
    removed_degrees_of_freedom: int
    maximum_absolute_residual: float
    constraints: tuple[SolverConstraintDiagnosis, ...]


@dataclass(frozen=True, slots=True)
class AssemblyDiagnosisState:
    assembly: Any
    joint_group: Any
    components: tuple[Any, ...]
    grounded_joints: tuple[Any, ...]
    regular_joints: tuple[Any, ...]
    solver_state: AssemblySolverState
    solver_status: int
    solver_message: str
    remaining_degrees_of_freedom: int
    residual_tolerance: float
    conflicting_names: tuple[str, ...]
    redundant_names: tuple[str, ...]
    partially_redundant_names: tuple[str, ...]
    malformed_names: tuple[str, ...]
    joint_diagnostics: tuple[SolverJointDiagnosis, ...]
    state_sha256: str

    def summary(self) -> dict[str, Any]:
        return {
            "available": True,
            "state_sha256": self.state_sha256,
            "component_count": len(self.components),
            "grounded_count": len(self.grounded_joints),
            "joint_count": len(self.regular_joints),
            "solver_status": self.solver_status,
            "remaining_degrees_of_freedom": self.remaining_degrees_of_freedom,
            "conflicting_count": len(self.conflicting_names),
            "redundant_count": len(self.redundant_names),
            "partially_redundant_count": len(self.partially_redundant_names),
            "malformed_count": len(self.malformed_names),
            "residual_tolerance": self.residual_tolerance,
        }


def _malformed(field: str) -> NativeAssemblyDiagnosisError:
    return NativeAssemblyDiagnosisError(
        f"The active Assembly returned malformed {field} diagnostics."
    )


def _exact_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise _malformed(field)
    return value


def _exact_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _malformed(field)
    return value


def _finite(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise _malformed(field)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _malformed(field) from exc
    if not math.isfinite(result) or (positive and result <= 0.0):
        raise _malformed(field)
    return result


def _bounded_sequence(value: Any, maximum: int, field: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise _malformed(field)
    return tuple(value)


def _name_sequence(value: Any, field: str) -> tuple[str, ...]:
    values = _bounded_sequence(value, MAX_ASSEMBLY_JOINTS, field)
    names = tuple(str(item) if isinstance(item, str) else "" for item in values)
    if any(not name or len(name) > 128 for name in names) or len(set(names)) != len(
        names
    ):
        raise _malformed(field)
    return names


def _object_identity_record(obj: Any) -> dict[str, Any]:
    result = {
        "object_name": str(getattr(obj, "Name", "") or ""),
        "object_id": int(getattr(obj, "ID", -1)),
        "type_id": str(getattr(obj, "TypeId", "") or ""),
    }
    if "Rigid" in set(getattr(obj, "PropertiesList", ()) or ()):
        rigid = getattr(obj, "Rigid", None)
        result["rigid"] = rigid if type(rigid) is bool else {"malformed": True}
    return result


def _reference_record(reference: Any) -> dict[str, Any] | None:
    if reference is None:
        return None
    try:
        if len(reference) != 2:
            raise ValueError
        component = reference[0]
        paths = tuple(reference[1])
        if component is None or len(paths) > 8:
            raise ValueError
        normalized_paths = tuple(str(path) for path in paths)
        if any(len(path) > 512 for path in normalized_paths):
            raise ValueError
        return {
            "component": _object_identity_record(component),
            "paths": normalized_paths,
        }
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
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
        value = getattr(joint, name, None)
        value = getattr(value, "Value", value)
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            result[name] = {"malformed": True}
        else:
            result[name] = number if math.isfinite(number) else {"malformed": True}
    return result


def _joint_definition_record(joint: Any) -> dict[str, Any]:
    return {
        **_object_identity_record(joint),
        "label": str(getattr(joint, "Label", "") or "")[:256],
        "joint_type": str(getattr(joint, "JointType", "") or "")[:64],
        "reference1": _reference_record(getattr(joint, "Reference1", None)),
        "reference2": _reference_record(getattr(joint, "Reference2", None)),
        "offset1": _placement_record(getattr(joint, "Offset1", None)),
        "offset2": _placement_record(getattr(joint, "Offset2", None)),
        "properties": _joint_property_record(joint),
    }


def _constraint_diagnosis(value: Any) -> SolverConstraintDiagnosis:
    if not isinstance(value, Mapping):
        raise _malformed("joint constraint")
    specification = value.get("specification")
    if (
        not isinstance(specification, str)
        or not specification
        or len(specification) > 160
    ):
        raise _malformed("joint constraint specification")
    residual = _finite(value.get("residual"), "joint constraint residual")
    absolute = _finite(
        value.get("absolute_residual"),
        "joint constraint absolute residual",
    )
    if not math.isclose(absolute, abs(residual), rel_tol=1.0e-12, abs_tol=1.0e-12):
        raise _malformed("joint constraint absolute residual")
    redundant = _exact_bool(value.get("redundant"), "joint constraint redundancy")
    return SolverConstraintDiagnosis(specification, residual, redundant)


def _expected_status(name: str, categories: Mapping[str, tuple[str, ...]]) -> str:
    if name in categories["malformed"]:
        return "malformed"
    if name in categories["conflicting"]:
        return "conflicting"
    if name in categories["redundant"]:
        return "redundant"
    if name in categories["partially_redundant"]:
        return "partially_redundant"
    return "satisfied"


def _joint_diagnosis(
    value: Any,
    joints: Mapping[str, Any],
    categories: Mapping[str, tuple[str, ...]],
) -> SolverJointDiagnosis:
    if not isinstance(value, Mapping):
        raise _malformed("joint")
    name = value.get("joint")
    if not isinstance(name, str) or name not in joints:
        raise _malformed("joint identity")
    constraints = tuple(
        _constraint_diagnosis(item)
        for item in _bounded_sequence(
            value.get("constraints"),
            MAX_DIAGNOSTIC_CONSTRAINTS_PER_JOINT,
            "joint constraint",
        )
    )
    count = _exact_int(
        value.get("constraint_count"),
        "joint constraint count",
        minimum=0,
        maximum=MAX_DIAGNOSTIC_CONSTRAINTS_PER_JOINT,
    )
    redundant_count = _exact_int(
        value.get("redundant_constraint_count"),
        "joint redundant constraint count",
        minimum=0,
        maximum=count,
    )
    removed = _exact_int(
        value.get("removed_degrees_of_freedom"),
        "joint removed degrees of freedom",
        minimum=0,
        maximum=count,
    )
    maximum = _finite(
        value.get("maximum_absolute_residual"),
        "joint maximum residual",
    )
    actual_maximum = max((abs(item.residual) for item in constraints), default=0.0)
    if (
        count != len(constraints)
        or redundant_count != sum(item.redundant for item in constraints)
        or removed != count - redundant_count
        or not math.isclose(maximum, actual_maximum, rel_tol=1.0e-9, abs_tol=1.0e-12)
    ):
        raise _malformed("joint aggregate")
    status = value.get("status")
    if status != _expected_status(name, categories):
        raise _malformed("joint status")
    return SolverJointDiagnosis(
        joints[name],
        status,
        count,
        redundant_count,
        removed,
        maximum,
        constraints,
    )


def _grounded_records(
    raw: Mapping[str, Any],
    grounded_joints: tuple[Any, ...],
) -> tuple[dict[str, str], ...]:
    records = []
    for value in _bounded_sequence(
        raw.get("grounded_components"),
        MAX_ASSEMBLY_JOINTS,
        "grounded component",
    ):
        if not isinstance(value, Mapping):
            raise _malformed("grounded component")
        joint = value.get("joint")
        component = value.get("component")
        if not isinstance(joint, str) or not isinstance(component, str):
            raise _malformed("grounded component")
        records.append({"joint": joint, "component": component})
    expected = [
        {
            "joint": str(getattr(joint, "Name", "") or ""),
            "component": str(
                getattr(getattr(joint, "ObjectToGround", None), "Name", "") or ""
            ),
        }
        for joint in grounded_joints
    ]
    if records != expected:
        raise _malformed("grounded component graph")
    return tuple(records)


def _diagnostic_canonical(
    state: dict[str, Any],
    joint_diagnostics: tuple[SolverJointDiagnosis, ...],
) -> dict[str, Any]:
    return {
        **state,
        "joint_diagnostics": [
            {
                "joint": _object_identity_record(item.joint),
                "status": item.status,
                "constraint_count": item.constraint_count,
                "redundant_constraint_count": item.redundant_constraint_count,
                "removed_degrees_of_freedom": item.removed_degrees_of_freedom,
                "maximum_absolute_residual": item.maximum_absolute_residual,
                "constraints": [
                    {
                        "specification": constraint.specification,
                        "residual": constraint.residual,
                        "redundant": constraint.redundant,
                    }
                    for constraint in item.constraints
                ],
            }
            for item in joint_diagnostics
        ],
    }


def capture_assembly_diagnosis_state(assembly: Any) -> AssemblyDiagnosisState:
    """Capture and cross-check one complete bounded public solver diagnosis."""

    document = getattr(assembly, "Document", None)
    if document is None:
        raise NativeAssemblyDiagnosisError(
            "The human-active Assembly has no exact document."
        )
    try:
        joint_group = require_joint_group(assembly)
        components = assembly_components(assembly)
        grounded_joints = active_grounded_joints(joint_group)
        regular_joints = active_regular_joints(joint_group)
    except (
        NativeAssemblyComponentError,
        NativeAssemblyGroundingError,
        NativeAssemblyJointGraphError,
    ) as exc:
        raise NativeAssemblyDiagnosisError(
            "The active Assembly joint graph is unavailable for solver diagnosis."
        ) from exc
    try:
        solver_state = capture_assembly_solver_state(assembly)
    except (
        AttributeError,
        NativeAssemblyJointConnectorError,
        NativeAssemblySolveStateError,
        OverflowError,
        ReferenceError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        raise NativeAssemblyDiagnosisError(
            "The active Assembly solver placement state is unavailable."
        ) from exc
    reader = getattr(assembly, "getSolverDiagnostics", None)
    if not callable(reader):
        raise NativeAssemblyDiagnosisError(
            "The active Assembly does not expose native solver diagnostics."
        )
    try:
        raw = reader()
    except (
        AttributeError,
        OverflowError,
        ReferenceError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        raise NativeAssemblyDiagnosisError(
            "The active Assembly solver diagnostics could not be read."
        ) from exc
    if not isinstance(raw, Mapping):
        raise _malformed("solver")

    categories = {
        category: _name_sequence(
            raw.get(f"{category}_joints"),
            f"{category.replace('_', ' ')} joint",
        )
        for category in _DIAGNOSTIC_CATEGORIES
    }
    flags = {
        "conflicting": _exact_bool(raw.get("has_conflicts"), "conflict flag"),
        "redundant": _exact_bool(raw.get("has_redundancies"), "redundancy flag"),
        "partially_redundant": _exact_bool(
            raw.get("has_partial_redundancies"),
            "partial redundancy flag",
        ),
        "malformed": _exact_bool(
            raw.get("has_malformed_constraints"),
            "malformed flag",
        ),
    }
    if any(flags[name] is not bool(categories[name]) for name in flags):
        raise _malformed("solver category flag")

    joints_by_name = {
        str(getattr(joint, "Name", "") or ""): joint for joint in regular_joints
    }
    categorized_names = set().union(*categories.values())
    if not categorized_names.issubset(joints_by_name):
        raise _malformed("solver joint graph")
    joint_diagnostics = tuple(
        _joint_diagnosis(value, joints_by_name, categories)
        for value in _bounded_sequence(
            raw.get("joints"),
            MAX_ASSEMBLY_JOINTS,
            "joint",
        )
    )
    diagnostic_names = [str(item.joint.Name) for item in joint_diagnostics]
    if len(set(diagnostic_names)) != len(diagnostic_names):
        raise _malformed("duplicate joint")
    if set(categories["malformed"]).intersection(diagnostic_names):
        raise _malformed("malformed joint")
    diagnostics_by_name = {str(item.joint.Name): item for item in joint_diagnostics}
    tolerance = _finite(
        raw.get("residual_tolerance"),
        "residual tolerance",
        positive=True,
    )
    expected_conflicting = tuple(
        name
        for name in diagnostic_names
        if diagnostics_by_name[name].maximum_absolute_residual > tolerance
    )
    expected_redundant = tuple(
        name
        for name in diagnostic_names
        if any(
            value.specification.startswith("Redundant")
            for value in diagnostics_by_name[name].constraints
        )
    )
    expected_partially_redundant = tuple(
        name
        for name in diagnostic_names
        if 0
        < diagnostics_by_name[name].redundant_constraint_count
        < diagnostics_by_name[name].constraint_count
    )
    expected_categories = {
        "conflicting": expected_conflicting,
        "redundant": expected_redundant,
        "partially_redundant": expected_partially_redundant,
    }
    for category, expected in expected_categories.items():
        if categories[category] != expected:
            raise _malformed(f"{category.replace('_', ' ')} joint")

    solver_status = _exact_int(
        raw.get("solver_status"),
        "solver status",
        minimum=-1_000_000,
        maximum=1_000_000,
    )
    remaining = _exact_int(
        raw.get("remaining_degrees_of_freedom"),
        "remaining degrees of freedom",
        minimum=-1_000_000,
        maximum=1_000_000,
    )
    message = raw.get("solver_message")
    if not isinstance(message, str) or len(message) > MAX_SOLVER_MESSAGE_LENGTH:
        raise _malformed("solver message")
    grounded_records = _grounded_records(raw, grounded_joints)

    canonical_base = {
        "assembly": _object_identity_record(assembly),
        "joint_group": _object_identity_record(joint_group),
        "components": [_object_identity_record(item) for item in components],
        "grounded_joints": [_object_identity_record(item) for item in grounded_joints],
        "regular_joints": [_joint_definition_record(item) for item in regular_joints],
        "solver_placement_state_sha256": solver_state.state_sha256,
        "solver_status": solver_status,
        "solver_message": message,
        "remaining_degrees_of_freedom": remaining,
        "residual_tolerance": tolerance,
        "categories": categories,
        "grounded_components": grounded_records,
    }
    canonical = _diagnostic_canonical(canonical_base, joint_diagnostics)
    try:
        encoded = json.dumps(
            canonical,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblyDiagnosisError(
            "The active Assembly solver diagnosis cannot be represented exactly."
        ) from exc
    return AssemblyDiagnosisState(
        assembly=assembly,
        joint_group=joint_group,
        components=components,
        grounded_joints=grounded_joints,
        regular_joints=regular_joints,
        solver_state=solver_state,
        solver_status=solver_status,
        solver_message=message,
        remaining_degrees_of_freedom=remaining,
        residual_tolerance=tolerance,
        conflicting_names=categories["conflicting"],
        redundant_names=categories["redundant"],
        partially_redundant_names=categories["partially_redundant"],
        malformed_names=categories["malformed"],
        joint_diagnostics=joint_diagnostics,
        state_sha256=hashlib.sha256(encoded).hexdigest(),
    )


def assembly_diagnosis_state_summary(assembly: Any) -> dict[str, Any]:
    return capture_assembly_diagnosis_state(assembly).summary()
