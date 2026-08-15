# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded exact placement state for the active native Assembly solver."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Iterable

from VibeCADNativeAssemblyJointConnectors import placement_summary


MAX_SOLVER_PLACEMENT_OBJECTS = 128


class NativeAssemblySolveStateError(RuntimeError):
    """The solver-relevant placement graph cannot be represented exactly."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_SOLVE_STATE_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class AssemblyPlacementRecord:
    obj: Any
    placement: Any
    placement_locks: tuple[tuple[str, bool], ...]


@dataclass(frozen=True, slots=True)
class AssemblySolverState:
    records: tuple[AssemblyPlacementRecord, ...]
    state_sha256: str

    def summary(self) -> dict[str, Any]:
        return {
            "available": True,
            "placement_object_count": len(self.records),
            "state_sha256": self.state_sha256,
        }


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _is_derived(obj: Any, type_id: str) -> bool:
    if str(getattr(obj, "TypeId", "") or "") == type_id:
        return True
    reader = getattr(obj, "isDerivedFrom", None)
    if not callable(reader):
        return False
    try:
        return bool(reader(type_id))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _is_link_group(obj: Any) -> bool:
    reader = getattr(obj, "isLinkGroup", None)
    if callable(reader):
        try:
            return bool(reader())
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            return False
    return bool(
        str(getattr(obj, "TypeId", "") or "") == "App::Link"
        and int(getattr(obj, "ElementCount", 0) or 0) > 0
    )


def _placement_locks(obj: Any) -> tuple[tuple[str, bool], ...]:
    properties = set(getattr(obj, "PropertiesList", ()) or ())
    reader = getattr(obj, "getPropertyStatus", None)
    result = []
    for name in ("Placement", "LinkPlacement"):
        if name not in properties:
            continue
        try:
            read_only = bool(
                callable(reader) and "ReadOnly" in tuple(reader(name) or ())
            )
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            read_only = False
        result.append((name, read_only))
    return tuple(result)


def _copy_placement(obj: Any) -> Any:
    try:
        return obj.Placement
    except (AttributeError, ReferenceError, RuntimeError) as exc:
        raise NativeAssemblySolveStateError(
            "A solver placement object no longer exposes its exact placement."
        ) from exc


def solver_placement_objects(
    assembly: Any,
    *,
    active_reader: Callable[[Any], bool] = _timeline_active,
) -> tuple[Any, ...]:
    """Mirror the native solver's movable graph plus normalized link containers."""

    document = getattr(assembly, "Document", None)
    if document is None:
        raise NativeAssemblySolveStateError(
            "The human-active Assembly has no exact document."
        )
    result: list[Any] = []
    result_names: set[str] = set()
    visited_containers: set[int] = set()

    def append_placement(obj: Any) -> None:
        name = str(getattr(obj, "Name", "") or "")
        get_object = getattr(document, "getObject", None)
        if (
            not name
            or getattr(obj, "Document", None) is not document
            or (callable(get_object) and get_object(name) is not obj)
            or not hasattr(obj, "Placement")
        ):
            return
        if name in result_names:
            return
        result_names.add(name)
        result.append(obj)
        if len(result) > MAX_SOLVER_PLACEMENT_OBJECTS:
            raise NativeAssemblySolveStateError(
                "The active Assembly exceeds the 128-object Native solve bound."
            )

    def visit(objects: Iterable[Any]) -> None:
        for obj in tuple(objects):
            if (
                obj is None
                or getattr(obj, "Document", None) is not document
                or not active_reader(obj)
            ):
                continue
            if _is_derived(obj, "Assembly::AssemblyLink"):
                append_placement(obj)
                if not bool(getattr(obj, "Rigid", True)):
                    identity = id(obj)
                    if identity not in visited_containers:
                        visited_containers.add(identity)
                        visit(list(getattr(obj, "Group", ()) or ()))
                continue
            if _is_link_group(obj):
                append_placement(obj)
                identity = id(obj)
                if identity not in visited_containers:
                    visited_containers.add(identity)
                    for element in list(getattr(obj, "ElementList", ()) or ()):
                        if element is not None and active_reader(element):
                            append_placement(element)
                continue
            if _is_derived(obj, "App::DocumentObjectGroup"):
                identity = id(obj)
                if identity not in visited_containers:
                    visited_containers.add(identity)
                    visit(list(getattr(obj, "Group", ()) or ()))
                continue
            if _is_derived(obj, "App::Link"):
                linked_reader = getattr(obj, "getLinkedObject", None)
                linked = linked_reader() if callable(linked_reader) else None
                if (
                    linked is not None
                    and active_reader(linked)
                    and _is_derived(linked, "App::GeoFeature")
                    and not _is_derived(linked, "App::LocalCoordinateSystem")
                ):
                    append_placement(obj)
                continue
            if _is_derived(obj, "App::GeoFeature") and not _is_derived(
                obj,
                "App::LocalCoordinateSystem",
            ):
                append_placement(obj)

    visit(list(getattr(assembly, "Group", ()) or ()))
    return tuple(result)


def capture_assembly_solver_state(
    assembly: Any,
    *,
    active_reader: Callable[[Any], bool] = _timeline_active,
) -> AssemblySolverState:
    objects = solver_placement_objects(assembly, active_reader=active_reader)
    records = tuple(
        AssemblyPlacementRecord(
            obj=obj,
            placement=_copy_placement(obj),
            placement_locks=_placement_locks(obj),
        )
        for obj in objects
    )
    canonical = [
        {
            "object_name": str(record.obj.Name),
            "object_id": int(record.obj.ID),
            "type_id": str(record.obj.TypeId),
            "placement": placement_summary(record.placement),
            "placement_locks": dict(record.placement_locks),
        }
        for record in records
    ]
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return AssemblySolverState(records, hashlib.sha256(encoded).hexdigest())


def assembly_solver_state_summary(assembly: Any) -> dict[str, Any]:
    return capture_assembly_solver_state(assembly).summary()
