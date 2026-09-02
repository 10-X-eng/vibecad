# SPDX-License-Identifier: LGPL-2.1-or-later

"""Frozen exact state for one background FEM mesh generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
from VibeCADNativeAnalyzeTargets import (
    PreparedFemMeshDefinitionTarget,
    fem_mesh_definition_target_still_exact,
    prepare_fem_mesh_definition_target,
)


_GMSH_MODES = frozenset(
    {
        "region",
        "group",
        "distance",
        "boundary_layer",
        "shape",
        "transfinite_curve",
        "transfinite_surface",
        "transfinite_volume",
        "manipulate",
        "advanced",
    }
)


@dataclass(frozen=True, slots=True)
class FrozenMeshResource:
    resource: Any
    mode: str
    state_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedMeshGenerationTarget:
    target: PreparedFemMeshDefinitionTarget
    resources: tuple[FrozenMeshResource, ...]
    history_operations: tuple[Any, ...]

    @property
    def mesh(self) -> Any:
        return self.target.mesh

    @property
    def backend(self) -> str:
        return self.target.kind


def _resource_inventory(mesh: Any) -> tuple[Any, ...]:
    refinements = tuple(getattr(mesh, "MeshRefinementList", ()) or ())
    groups = tuple(getattr(mesh, "MeshGroupList", ()) or ())
    resources = (*refinements, *groups)
    if len({id(value) for value in resources}) != len(resources):
        raise NativeAnalyzeError("The mesh definition contains duplicate refinement identities.")
    return resources


def _validate_dependency_graph(mesh: Any, resources: tuple[Any, ...]) -> None:
    resource_ids = {id(resource) for resource in resources}
    complete: set[int] = set()
    active: set[int] = set()

    def visit(resource: Any) -> None:
        identity = id(resource)
        if identity in active:
            raise NativeAnalyzeError(
                "The mesh refinement-field graph contains a cycle.",
                error_code="NATIVE_ANALYZE_DEPENDENCY_CYCLE",
            )
        if identity in complete:
            return
        if identity not in resource_ids:
            raise NativeAnalyzeError(
                "A refinement-field input is not owned by the exact mesh definition."
            )
        state = mesh_refinement_state(resource)
        if state["refinement_mode"] == "advanced" and state["definition"].get("kind") == "result":
            raise NativeAnalyzeError(
                "Result-backed Gmsh fields cannot be generated until their FEM result data "
                "has an exact field-data target."
            )
        children = ()
        if state["refinement_mode"] == "manipulate":
            child = getattr(resource, "Refinement", None)
            if child is None and not state["suppressed"]:
                raise NativeAnalyzeError(
                    f"Active manipulation {resource.Name!r} has no input refinement."
                )
            children = (child,) if child is not None else ()
        elif state["refinement_mode"] == "advanced":
            children = tuple(getattr(resource, "Refinements", ()) or ())
        active.add(identity)
        for child in children:
            child_state = mesh_refinement_state(child)
            if child_state["suppressed"] and not state["suppressed"]:
                raise NativeAnalyzeError(
                    f"Active field {resource.Name!r} depends on suppressed field {child.Name!r}."
                )
            visit(child)
        active.remove(identity)
        complete.add(identity)

    for resource in resources:
        visit(resource)


def prepare_mesh_generation_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    backend: str,
) -> PreparedMeshGenerationTarget:
    target = prepare_fem_mesh_definition_target(
        document,
        document_uid,
        value,
        expected_kind=backend,
    )
    mesh = target.mesh
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        mesh not in operations
        or str(getattr(mesh, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(mesh, "VibeCADTimelineOwner", None) is not None
    ):
        raise NativeAnalyzeError(
            "The mesh definition is not a durable root operation in current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INVALID",
        )
    resources = _resource_inventory(mesh)
    frozen = []
    for resource in resources:
        state = mesh_refinement_state(resource)
        mode = state["refinement_mode"]
        if backend == "netgen" and mode != "region":
            raise NativeAnalyzeError(
                f"Netgen generation cannot consume {mode} refinement {resource.Name!r}."
            )
        if backend == "gmsh" and mode not in _GMSH_MODES:
            raise NativeAnalyzeError(
                f"Gmsh generation cannot consume {mode} refinement {resource.Name!r}."
            )
        if getattr(resource, "VibeCADTimelineOwner", None) is not mesh:
            raise NativeAnalyzeError(
                f"Mesh resource {resource.Name!r} has invalid History ownership."
            )
        frozen.append(FrozenMeshResource(resource, mode, state["state_sha256"]))
    if backend == "gmsh":
        _validate_dependency_graph(mesh, resources)
    return PreparedMeshGenerationTarget(target, tuple(frozen), operations)


def mesh_generation_resource_scope(prepared: PreparedMeshGenerationTarget) -> str:
    """Return the exact analysis scope that owns a prepared mesh definition."""

    if not isinstance(prepared, PreparedMeshGenerationTarget):
        raise TypeError("prepared must be a PreparedMeshGenerationTarget")
    mesh = prepared.mesh
    document = getattr(mesh, "Document", None)
    owners = []
    for obj in tuple(getattr(document, "Objects", ()) or ()):
        try:
            if obj.isDerivedFrom("Fem::FemAnalysis") and mesh in tuple(obj.Group or ()):
                owners.append(obj)
        except Exception:
            continue
    if len(owners) != 1:
        raise NativeAnalyzeError(
            "The prepared FEM mesh definition must belong to exactly one analysis."
        )
    return f"analyze:{owners[0].Name}"


def mesh_generation_target_still_exact(prepared: PreparedMeshGenerationTarget) -> bool:
    if not fem_mesh_definition_target_still_exact(prepared.target):
        return False
    try:
        if _resource_inventory(prepared.mesh) != tuple(
            item.resource for item in prepared.resources
        ):
            return False
        return all(
            mesh_refinement_state(item.resource)["state_sha256"] == item.state_sha256
            for item in prepared.resources
        )
    except NativeAnalyzeError:
        return False


def mesh_generation_resources_still_exact(
    prepared: PreparedMeshGenerationTarget,
) -> bool:
    try:
        timeline = getattr(prepared.mesh.Document, "VibeCADTimeline", None)
        if tuple(getattr(timeline, "Operations", ()) or ()) != prepared.history_operations:
            return False
        if _resource_inventory(prepared.mesh) != tuple(
            item.resource for item in prepared.resources
        ):
            return False
        return all(
            mesh_refinement_state(item.resource)["state_sha256"] == item.state_sha256
            for item in prepared.resources
        )
    except NativeAnalyzeError:
        return False


def generation_target_summary(prepared: PreparedMeshGenerationTarget) -> dict[str, Any]:
    state = fem_mesh_definition_state(prepared.mesh)
    return {
        "object_name": state["object_name"],
        "backend": prepared.backend,
        "expected_state_sha256": prepared.target.expected_state_sha256,
        "resource_count": len(prepared.resources),
    }
