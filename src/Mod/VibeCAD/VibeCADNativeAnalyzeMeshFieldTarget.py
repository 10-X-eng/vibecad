# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, acyclic dependency targets for Gmsh refinement fields."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeMeshRefinementState import (
    mesh_refinement_mode,
    mesh_refinement_state,
)
from VibeCADNativeAnalyzeMeshRefinementTarget import (
    PreparedMeshRefinementTarget,
    mesh_refinement_target_still_exact,
    prepare_mesh_refinement_target,
)


_FIELD_MODES = frozenset({"region", "distance", "shape", "manipulate", "advanced"})


@dataclass(frozen=True, slots=True)
class PreparedMeshFieldDependencies:
    direct: tuple[PreparedMeshRefinementTarget, ...]
    closure: tuple[PreparedMeshRefinementTarget, ...]

    @property
    def objects(self) -> tuple[Any, ...]:
        return tuple(target.refinement for target in self.direct)


def mesh_resource_owner(document: Any, resource: Any) -> Any:
    owner = getattr(resource, "VibeCADTimelineOwner", None)
    if owner is None or getattr(owner, "Document", None) is not document:
        raise NativeAnalyzeError("The refinement field has no durable mesh History owner.")
    linked = resource in tuple(getattr(owner, "MeshRefinementList", ()) or ())
    if not linked:
        raise NativeAnalyzeError(
            "The refinement field is not linked to its mesh History owner."
        )
    return owner


def _children(obj: Any, mode: str) -> tuple[Any, ...]:
    if mode == "manipulate":
        child = getattr(obj, "Refinement", None)
        return (child,) if child is not None else ()
    if mode == "advanced":
        return tuple(getattr(obj, "Refinements", ()) or ())
    return ()


def _validate_field_node(document: Any, mesh: Any, obj: Any) -> tuple[str, dict[str, Any]]:
    mode = mesh_refinement_mode(obj)
    if mode not in _FIELD_MODES:
        raise NativeAnalyzeError(
            "Refinement-field inputs must be region, distance, shape, manipulation, "
            "or advanced size fields.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    if mesh_resource_owner(document, obj) is not mesh:
        raise NativeAnalyzeError(
            "Every refinement-field input must belong to the same mesh definition."
        )
    state = mesh_refinement_state(obj)
    if state["suppressed"]:
        raise NativeAnalyzeError(
            f"Input refinement {obj.Name!r} is suppressed and cannot supply a field."
        )
    if mode == "advanced" and state["definition"].get("kind") == "result":
        raise NativeAnalyzeError(
            "Result-backed mesh fields require exact FEM result targeting, which is not "
            "available until the result-data tool family is active."
        )
    return mode, state


def prepare_mesh_field_dependencies(
    document: Any,
    document_uid: str,
    mesh: Any,
    value: Any,
    *,
    minimum: int,
    maximum: int,
    forbidden: Any | None = None,
) -> PreparedMeshFieldDependencies:
    if not isinstance(value, list):
        raise NativeAnalyzeError("input_refinements must be an ordered array of exact targets.")
    if not minimum <= len(value) <= maximum:
        raise NativeAnalyzeError(
            f"input_refinements must contain from {minimum} to {maximum} exact fields."
        )
    direct = tuple(
        prepare_mesh_refinement_target(document, document_uid, item)
        for item in value
    )
    identities = tuple(id(target.refinement) for target in direct)
    if len(set(identities)) != len(identities):
        raise NativeAnalyzeError("input_refinements cannot contain duplicate fields.")

    closure: dict[int, PreparedMeshRefinementTarget] = {}
    active: set[int] = set()

    def visit(obj: Any) -> None:
        identity = id(obj)
        if obj is forbidden:
            raise NativeAnalyzeError(
                "The requested refinement dependencies would create a cycle.",
                error_code="NATIVE_ANALYZE_DEPENDENCY_CYCLE",
            )
        if identity in active:
            raise NativeAnalyzeError(
                "The existing refinement dependency graph contains a cycle.",
                error_code="NATIVE_ANALYZE_DEPENDENCY_CYCLE",
            )
        if identity in closure:
            return
        mode, state = _validate_field_node(document, mesh, obj)
        active.add(identity)
        for child in _children(obj, mode):
            visit(child)
        active.remove(identity)
        closure[identity] = PreparedMeshRefinementTarget(
            obj,
            mode,
            state["state_sha256"],
        )

    for target in direct:
        visit(target.refinement)
    return PreparedMeshFieldDependencies(direct, tuple(closure.values()))


def mesh_field_dependencies_still_exact(
    prepared: PreparedMeshFieldDependencies,
) -> bool:
    return all(mesh_refinement_target_still_exact(target) for target in prepared.closure)


def dependency_payload(obj: Any) -> dict[str, str]:
    state = mesh_refinement_state(obj)
    return {
        "object_name": str(obj.Name),
        "expected_state_sha256": state["state_sha256"],
    }
