# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read-only helpers for the exact active Assembly joint graph."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from VibeCADNativeTargets import object_reference


MAX_ASSEMBLY_JOINTS = 256
MAX_SOLVER_JOINT_DIAGNOSTICS = 32
MAX_SOLVER_CONSTRAINT_DIAGNOSTICS = 16


class NativeAssemblyJointGraphError(RuntimeError):
    """The active Assembly joint graph is malformed or unavailable."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_JOINT_GRAPH_FAILED",
            "message": str(self),
        }


def timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def object_is_valid(obj: Any) -> bool:
    reader = getattr(obj, "isValid", None)
    if not callable(reader):
        return True
    try:
        return bool(reader())
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def require_joint_group(
    assembly: Any,
    *,
    active_reader: Callable[[Any], bool] = timeline_active,
) -> Any:
    """Return one exact active native JointGroup without creating resources."""

    document = getattr(assembly, "Document", None)
    groups = [
        child
        for child in list(getattr(assembly, "Group", ()) or ())
        if str(getattr(child, "TypeId", "") or "") == "Assembly::JointGroup"
        and getattr(child, "Document", None) is document
        and getattr(document, "getObject", lambda _name: None)(
            str(getattr(child, "Name", "") or "")
        )
        is child
    ]
    if len(groups) != 1:
        raise NativeAssemblyJointGraphError(
            "The human-active Assembly must contain one exact native joint group."
        )
    joint_group = groups[0]
    if not active_reader(joint_group) or not object_is_valid(joint_group):
        raise NativeAssemblyJointGraphError(
            "The human-active Assembly joint group is not active and valid."
        )
    return joint_group


def is_regular_joint(joint: Any) -> bool:
    properties = set(getattr(joint, "PropertiesList", ()) or ())
    return (
        "JointType" in properties
        and "Reference1" in properties
        and "Reference2" in properties
        and "ObjectToGround" not in properties
    )


def active_regular_joints(
    joint_group: Any,
    *,
    active_reader: Callable[[Any], bool] = timeline_active,
) -> tuple[Any, ...]:
    joints = tuple(
        joint
        for joint in list(getattr(joint_group, "Group", ()) or ())
        if active_reader(joint) and is_regular_joint(joint)
    )
    if len(joints) > MAX_ASSEMBLY_JOINTS:
        raise NativeAssemblyJointGraphError(
            f"The active Assembly exceeds the {MAX_ASSEMBLY_JOINTS}-joint Native bound."
        )
    return joints


def _bounded_items(value: Any, maximum: int, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise NativeAssemblyJointGraphError(
            f"The active Assembly returned malformed {field} diagnostics."
        )
    return list(value[:maximum])


def _bounded_names(value: Any, field: str) -> list[str]:
    return [
        str(item)
        for item in _bounded_items(
            value,
            MAX_SOLVER_JOINT_DIAGNOSTICS,
            field,
        )
    ]


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise NativeAssemblyJointGraphError(
            f"The active Assembly returned malformed {field} diagnostics."
        )
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblyJointGraphError(
            f"The active Assembly returned malformed {field} diagnostics."
        ) from exc


def _finite_number(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number or number in (float("inf"), float("-inf")):
        return fallback
    return number


def solver_diagnostics(assembly: Any) -> dict[str, Any]:
    """Normalize the bounded public diagnostics from the most recent solve."""

    reader = getattr(assembly, "getSolverDiagnostics", None)
    if not callable(reader):
        return {
            "solver_status": None,
            "solver_message": "Native solver diagnostics are unavailable.",
        }
    try:
        raw = reader()
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeAssemblyJointGraphError(
            "The active Assembly solver diagnostics could not be read."
        ) from exc
    if not isinstance(raw, Mapping):
        raise NativeAssemblyJointGraphError(
            "The active Assembly returned malformed solver diagnostics."
        )
    result: dict[str, Any] = {
        "solver_status": _integer(raw.get("solver_status", 0), "solver status"),
        "remaining_degrees_of_freedom": _integer(
            raw.get("remaining_degrees_of_freedom", 0),
            "remaining degrees of freedom",
        ),
        "has_conflicts": bool(raw.get("has_conflicts", False)),
        "has_redundancies": bool(raw.get("has_redundancies", False)),
        "has_partial_redundancies": bool(
            raw.get("has_partial_redundancies", False)
        ),
        "has_malformed_constraints": bool(
            raw.get("has_malformed_constraints", False)
        ),
        "conflicting_joints": _bounded_names(
            raw.get("conflicting_joints"), "conflicting joint"
        ),
        "redundant_joints": _bounded_names(
            raw.get("redundant_joints"), "redundant joint"
        ),
        "partially_redundant_joints": _bounded_names(
            raw.get("partially_redundant_joints"),
            "partially redundant joint",
        ),
        "malformed_joints": _bounded_names(
            raw.get("malformed_joints"), "malformed joint"
        ),
    }
    message = str(raw.get("solver_message") or "").strip()
    if message:
        result["solver_message"] = message[:512]
    normalized_joints = []
    for item in _bounded_items(
        raw.get("joints"),
        MAX_SOLVER_JOINT_DIAGNOSTICS,
        "joint",
    ):
        if not isinstance(item, Mapping):
            continue
        constraints = []
        for constraint in _bounded_items(
            item.get("constraints"),
            MAX_SOLVER_CONSTRAINT_DIAGNOSTICS,
            "joint constraint",
        ):
            if not isinstance(constraint, Mapping):
                continue
            constraints.append(
                {
                    "specification": str(
                        constraint.get("specification") or ""
                    )[:160],
                    "residual": _finite_number(constraint.get("residual")),
                    "redundant": bool(constraint.get("redundant", False)),
                }
            )
        normalized_joints.append(
            {
                "joint": str(item.get("joint") or "")[:128],
                "status": str(item.get("status") or "")[:32],
                "constraint_count": _integer(
                    item.get("constraint_count", 0), "constraint count"
                ),
                "redundant_constraint_count": _integer(
                    item.get("redundant_constraint_count", 0),
                    "redundant constraint count",
                ),
                "removed_degrees_of_freedom": _integer(
                    item.get("removed_degrees_of_freedom", 0),
                    "removed degrees of freedom",
                ),
                "maximum_absolute_residual": _finite_number(
                    item.get("maximum_absolute_residual")
                ),
                "constraints": constraints,
            }
        )
    if normalized_joints:
        result["joints"] = normalized_joints
    grounded = []
    for item in _bounded_items(
        raw.get("grounded_components"),
        MAX_SOLVER_JOINT_DIAGNOSTICS,
        "grounded component",
    ):
        if isinstance(item, Mapping):
            grounded.append(
                {
                    "joint": str(item.get("joint") or "")[:128],
                    "component": str(item.get("component") or "")[:128],
                }
            )
    result["grounded_components"] = grounded
    result["residual_tolerance"] = _finite_number(
        raw.get("residual_tolerance"),
        1.0e-6,
    )
    return result


def reference_summary(reference: Any) -> dict[str, Any] | None:
    """Return one component-rooted joint connector without resolving selection."""

    try:
        if reference is None or len(reference) != 2:
            return None
        component = reference[0]
        paths = list(reference[1] or [])
        if component is None or len(paths) < 2:
            return None
        return {
            "component": object_reference(component),
            "element_path": str(paths[0]),
            "anchor_path": str(paths[1]),
        }
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return None
