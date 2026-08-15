# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded exact state for native Assembly exploded-view authoring."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from VibeCADNativeAssemblyJointConnectors import (
    NativeAssemblyJointConnectorError,
    placement_summary,
)
from VibeCADNativeTargets import object_reference


MAX_VIEW_TARGETS = 4_096
MAX_VIEW_OPERATIONS = 1_024
MAX_VIEW_STEPS = 4_096
MAX_VIEW_REFERENCES = 16_384
MAX_VIEW_SELECTION_PATH = 512
MAX_VIEW_TARGET_PREVIEW = 32
MAX_VIEW_OPERATION_PREVIEW = 16


class NativeAssemblyViewStateError(RuntimeError):
    """The live exploded-view graph cannot be represented exactly."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_VIEW_STATE_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class AssemblyViewTarget:
    obj: Any
    root: Any
    selection_path: str
    placement: Any
    record: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AssemblyViewState:
    assembly: Any
    component_count: int
    assembly_center: Any
    assembly_diagonal_mm: float
    individual_targets: tuple[AssemblyViewTarget, ...]
    solid_targets: tuple[AssemblyViewTarget, ...]
    view_group: Any | None
    views: tuple[Any, ...]
    view_records: tuple[dict[str, Any], ...]
    state_sha256: str

    def targets(self, parts_as_single_solid: bool) -> tuple[AssemblyViewTarget, ...]:
        return self.solid_targets if parts_as_single_solid else self.individual_targets

    def summary(self) -> dict[str, Any]:
        individual = {id(target.obj): target for target in self.individual_targets}
        solid = {id(target.obj): target for target in self.solid_targets}
        ordered: list[AssemblyViewTarget] = list(self.individual_targets)
        ordered.extend(
            target for target in self.solid_targets if id(target.obj) not in individual
        )
        targets = []
        for target in ordered[:MAX_VIEW_TARGET_PREVIEW]:
            value = dict(target.record["object"])
            value["label"] = str(target.record["label"])
            modes = []
            if id(target.obj) in individual:
                modes.append("individual_objects")
            if id(target.obj) in solid:
                modes.append("parts_as_single_solid")
            value["target_modes"] = modes
            targets.append(value)
        result: dict[str, Any] = {
            "available": True,
            "state_sha256": self.state_sha256,
            "component_count": self.component_count,
            "view_count": len(self.views),
            "move_count": sum(len(record["moves"]) for record in self.view_records),
            "individual_target_count": len(self.individual_targets),
            "solid_target_count": len(self.solid_targets),
            "assembly_bounds": {
                "center_mm": _vector_summary(self.assembly_center),
                "diagonal_mm": self.assembly_diagonal_mm,
            },
            "movable_targets": targets,
            "views": [
                {
                    **dict(record["view"]),
                    "label": str(record["label"]),
                    "move_count": len(record["moves"]),
                }
                for record in self.view_records[:MAX_VIEW_OPERATION_PREVIEW]
            ],
        }
        if len(ordered) > MAX_VIEW_TARGET_PREVIEW:
            result["movable_targets_truncated"] = True
        if len(self.view_records) > MAX_VIEW_OPERATION_PREVIEW:
            result["views_truncated"] = True
        return result


def _identity_record(obj: Any) -> dict[str, Any]:
    result = object_reference(obj)
    object_id = getattr(obj, "ID", None)
    if type(object_id) is not int or object_id < 0:
        raise NativeAssemblyViewStateError(
            "An Assembly exploded-view object has an invalid identity."
        )
    return {**result, "object_id": object_id}


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


def _vector_summary(vector: Any) -> dict[str, float]:
    values = {
        "x": float(getattr(vector, "x", 0.0)),
        "y": float(getattr(vector, "y", 0.0)),
        "z": float(getattr(vector, "z", 0.0)),
    }
    if not all(math.isfinite(value) for value in values.values()):
        raise NativeAssemblyViewStateError(
            "Assembly exploded-view bounds contain non-finite coordinates."
        )
    return values


def _copy_placement(obj: Any) -> Any:
    placement = getattr(obj, "Placement", None)
    if placement is None:
        raise NativeAssemblyViewStateError(
            "An Assembly exploded-view target has no placement."
        )
    try:
        placement_summary(placement)
    except NativeAssemblyJointConnectorError as exc:
        raise NativeAssemblyViewStateError(str(exc)) from exc
    try:
        import FreeCAD as App

        return App.Placement(placement)
    except (ImportError, AttributeError, RuntimeError, TypeError):
        return placement


def _canonical_target_path(assembly: Any, target: Any) -> tuple[Any, str]:
    try:
        import UtilsAssembly
    except ImportError as exc:
        raise NativeAssemblyViewStateError(
            "The native Assembly selection-path API is unavailable."
        ) from exc
    document = assembly.Document
    candidates: list[tuple[Any, str]] = []
    try:
        parents = list(target.Parents or ())
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeAssemblyViewStateError(
            "An exploded-view target has no readable Assembly parent path."
        ) from exc
    for value in parents:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            continue
        root, raw_path = value
        path = str(raw_path or "")
        if (
            not _live_object(root, document)
            or not path
            or not path.endswith(".")
            or len(path) > MAX_VIEW_SELECTION_PATH
            or "?" in path
        ):
            continue
        try:
            resolved = UtilsAssembly.getObject([root, [path]])
            moving_component, _relative = UtilsAssembly.getComponentReference(
                assembly,
                root,
                path,
            )
            movable = UtilsAssembly.isMovableAssemblyComponent(
                assembly,
                moving_component,
            )
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            continue
        if resolved is target and movable and _timeline_active(moving_component):
            candidates.append((root, path))
    if not candidates:
        raise NativeAssemblyViewStateError(
            "An exploded-view target has no exact Assembly-rooted selection path."
        )
    candidates.sort(
        key=lambda item: (
            item[0] is not assembly,
            len(item[1]),
            str(item[0].Name),
            item[1],
            int(item[0].ID),
        )
    )
    return candidates[0]


def _target_center(target: Any, root: Any, path: str) -> Any:
    try:
        import UtilsAssembly

        center = UtilsAssembly.getCenterOfBoundingBox(
            [target],
            [[root, [path]]],
        )
        _vector_summary(center)
        return center
    except Exception as exc:
        raise NativeAssemblyViewStateError(
            "An exploded-view target has no finite selection bounds."
        ) from exc


def _target_records(
    assembly: Any,
    *,
    parts_as_single_solid: bool,
) -> tuple[AssemblyViewTarget, ...]:
    try:
        import UtilsAssembly

        raw = UtilsAssembly.getMovablePartsWithin(
            assembly,
            parts_as_single_solid,
        )
    except (
        ImportError,
        AttributeError,
        ReferenceError,
        RuntimeError,
        TypeError,
    ) as exc:
        raise NativeAssemblyViewStateError(
            "The active Assembly movable-target graph is unavailable."
        ) from exc
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_VIEW_TARGETS:
        raise NativeAssemblyViewStateError(
            f"The active Assembly exceeds the {MAX_VIEW_TARGETS}-target Native view bound."
        )
    targets = tuple(raw)
    if len({id(target) for target in targets}) != len(targets):
        raise NativeAssemblyViewStateError(
            "The active Assembly movable-target graph contains duplicates."
        )
    document = assembly.Document
    result = []
    for target in targets:
        if not _live_object(target, document) or not _timeline_active(target):
            raise NativeAssemblyViewStateError(
                "The active Assembly contains a stale exploded-view target."
            )
        root, path = _canonical_target_path(assembly, target)
        placement = _copy_placement(target)
        center = _target_center(target, root, path)
        result.append(
            AssemblyViewTarget(
                obj=target,
                root=root,
                selection_path=path,
                placement=placement,
                record={
                    "object": _identity_record(target),
                    "label": str(getattr(target, "Label", "") or "")[:256],
                    "root": _identity_record(root),
                    "selection_path": path,
                    "placement": placement_summary(placement),
                    "selection_center_mm": _vector_summary(center),
                },
            )
        )
    return tuple(result)


def _reference_record(reference: Any) -> dict[str, Any]:
    try:
        root = reference[0]
        paths = list(reference[1])
    except (AttributeError, IndexError, ReferenceError, TypeError) as exc:
        raise NativeAssemblyViewStateError(
            "An existing exploded-view move contains malformed references."
        ) from exc
    if root is None or len(paths) > MAX_VIEW_REFERENCES:
        raise NativeAssemblyViewStateError(
            "An existing exploded-view move contains unbounded references."
        )
    normalized = [str(path or "") for path in paths]
    if any(len(path) > MAX_VIEW_SELECTION_PATH for path in normalized):
        raise NativeAssemblyViewStateError(
            "An existing exploded-view move contains an unbounded selection path."
        )
    return {"root": _identity_record(root), "paths": normalized}


def _move_record(move: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "move": _identity_record(move),
        "label": str(getattr(move, "Label", "") or "")[:256],
        "active": _timeline_active(move),
        "proxy_class": type(getattr(move, "Proxy", None)).__name__,
    }
    if hasattr(move, "MoveType"):
        result["move_type"] = str(move.MoveType)
    if hasattr(move, "MovementTransform"):
        try:
            result["movement_transform"] = placement_summary(move.MovementTransform)
        except NativeAssemblyJointConnectorError as exc:
            raise NativeAssemblyViewStateError(str(exc)) from exc
    if hasattr(move, "References"):
        result["references"] = _reference_record(move.References)
    return result


def _view_graph(
    assembly: Any,
) -> tuple[Any | None, tuple[Any, ...], tuple[dict[str, Any], ...]]:
    groups = [
        child
        for child in list(getattr(assembly, "Group", ()) or ())
        if str(getattr(child, "TypeId", "") or "") == "Assembly::ViewGroup"
    ]
    if len(groups) > 1:
        raise NativeAssemblyViewStateError(
            "The active Assembly contains multiple exploded-view groups."
        )
    if not groups:
        return None, (), ()
    group = groups[0]
    document = assembly.Document
    if not _live_object(group, document):
        raise NativeAssemblyViewStateError(
            "The Assembly exploded-view group is not live."
        )
    members = list(getattr(group, "Group", ()) or ())
    if len(members) > MAX_VIEW_OPERATIONS:
        raise NativeAssemblyViewStateError(
            f"The active Assembly exceeds the {MAX_VIEW_OPERATIONS}-view Native bound."
        )
    views = []
    records = []
    total_steps = 0
    for member in members:
        if not _live_object(member, document):
            raise NativeAssemblyViewStateError(
                "The exploded-view group contains a stale object."
            )
        proxy_class = type(getattr(member, "Proxy", None)).__name__
        if proxy_class != "ExplodedView" or not _timeline_active(member):
            continue
        moves = list(getattr(member, "Group", ()) or ())
        total_steps += len(moves)
        if total_steps > MAX_VIEW_STEPS:
            raise NativeAssemblyViewStateError(
                f"The active Assembly exceeds the {MAX_VIEW_STEPS}-move Native view bound."
            )
        views.append(member)
        records.append(
            {
                "view": _identity_record(member),
                "label": str(getattr(member, "Label", "") or "")[:256],
                "moves": [_move_record(move) for move in moves],
            }
        )
    return group, tuple(views), tuple(records)


def capture_assembly_view_state(assembly: Any) -> AssemblyViewState:
    """Capture the exact human-command inputs and current durable view graph."""

    document = getattr(assembly, "Document", None)
    if (
        document is None
        or not _live_object(assembly, document)
        or not _timeline_active(assembly)
    ):
        raise NativeAssemblyViewStateError(
            "The human-active Assembly is not one exact live History object."
        )
    try:
        import UtilsAssembly

        component_count = int(UtilsAssembly.number_of_components_in(assembly))
        center, raw_diagonal = UtilsAssembly.getComAndSize(assembly)
    except (
        ImportError,
        AttributeError,
        ReferenceError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        raise NativeAssemblyViewStateError(
            "The active Assembly exploded-view bounds are unavailable."
        ) from exc
    diagonal = float(raw_diagonal)
    _vector_summary(center)
    if not math.isfinite(diagonal) or diagonal < 0.0:
        raise NativeAssemblyViewStateError(
            "The active Assembly has invalid exploded-view bounds."
        )
    individual = _target_records(assembly, parts_as_single_solid=False)
    solid = _target_records(assembly, parts_as_single_solid=True)
    view_group, views, view_records = _view_graph(assembly)
    canonical = {
        "assembly": _identity_record(assembly),
        "component_count": component_count,
        "assembly_bounds": {
            "center_mm": _vector_summary(center),
            "diagonal_mm": diagonal,
        },
        "individual_targets": [target.record for target in individual],
        "solid_targets": [target.record for target in solid],
        "view_group": None if view_group is None else _identity_record(view_group),
        "views": view_records,
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
        raise NativeAssemblyViewStateError(
            "The Assembly exploded-view state cannot be represented exactly."
        ) from exc
    return AssemblyViewState(
        assembly=assembly,
        component_count=component_count,
        assembly_center=center,
        assembly_diagonal_mm=diagonal,
        individual_targets=individual,
        solid_targets=solid,
        view_group=view_group,
        views=views,
        view_records=view_records,
        state_sha256=hashlib.sha256(encoded).hexdigest(),
    )


def assembly_view_state_summary(assembly: Any) -> dict[str, Any]:
    return capture_assembly_view_state(assembly).summary()
