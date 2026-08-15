# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact legacy FEM result adoption before native reconciliation."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


class _Object:
    def __init__(
        self,
        name: str,
        object_id: int,
        type_id: str,
        **properties: Any,
    ) -> None:
        self.Name = name
        self.ID = object_id
        self.TypeId = type_id
        self.Document = None
        self.PropertiesList = list(properties)
        self._property_types = {}
        for property_name, value in properties.items():
            setattr(self, property_name, value)
            self._property_types[property_name] = (
                "App::PropertyString"
                if property_name in {"VibeCADTimelineRole", "VibeCADResultSolver"}
                else "App::PropertyLinkHidden"
            )

    def getTypeIdOfProperty(self, name: str) -> str:
        return self._property_types[name]

    def isDerivedFrom(self, type_id: str) -> bool:
        return self.TypeId == type_id

    def removeProperty(self, name: str) -> None:
        self.PropertiesList.remove(name)
        self._property_types.pop(name)
        delattr(self, name)


class _Timeline:
    TypeId = "App::DocumentTimeline"

    def __init__(self, operations: list[_Object]) -> None:
        self.Operations = operations


class _Document:
    def __init__(self, objects: list[_Object], operations: list[_Object]) -> None:
        self._objects_by_id = {obj.ID: obj for obj in objects}
        self._timeline = _Timeline(operations)
        self.adoptions = []
        self.lookups = []
        for obj in objects:
            obj.Document = self

    def getObject(self, identity: Any) -> Any:
        self.lookups.append(identity)
        if identity == "VibeCADTimeline":
            return self._timeline
        return self._objects_by_id.get(identity)

    def adoptExistingTimelineOperationBlock(
        self,
        root: _Object,
        resources: tuple[_Object, ...],
        owners: tuple[_Object, ...],
    ) -> None:
        self.adoptions.append((root, resources, owners))


@pytest.fixture
def objecttools(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    qtcore = ModuleType("PySide.QtCore")
    qtcore.QProcess = object
    pyside = ModuleType("PySide")
    pyside.QtCore = qtcore
    monkeypatch.setitem(sys.modules, "PySide", pyside)
    monkeypatch.setitem(sys.modules, "PySide.QtCore", qtcore)

    femtools = ModuleType("femtools")
    femtools.membertools = SimpleNamespace(
        _is_suppressed=lambda _obj: False,
    )
    monkeypatch.setitem(sys.modules, "femtools", femtools)

    def canonical_resource_order(owner, resources):
        children = {owner: []}
        for resource in resources:
            children[resource] = []
        for resource in resources:
            children[resource.VibeCADTimelineOwner].append(resource)
        ordered = []

        def visit(resource):
            for child in children[resource]:
                visit(child)
            ordered.append(resource)

        for resource in children[owner]:
            visit(resource)
        return ordered

    femcommands = ModuleType("femcommands")
    femcommands_manager = ModuleType("femcommands.manager")
    femcommands_manager._canonical_timeline_resource_order = (
        canonical_resource_order
    )
    femcommands.manager = femcommands_manager
    monkeypatch.setitem(sys.modules, "femcommands", femcommands)
    monkeypatch.setitem(
        sys.modules,
        "femcommands.manager",
        femcommands_manager,
    )

    module_path = (
        Path(__file__).resolve().parents[2] / "Fem" / "femtools" / "objecttools.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_vibecad_test_fem_objecttools",
        module_path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _solver(name: str = "Solver", object_id: int = 1) -> _Object:
    solver = _Object(name, object_id, "Fem::FemSolverObject")
    solver.Results = []
    return solver


def _pipeline(
    name: str = "Pipeline",
    object_id: int = 2,
    **properties: Any,
) -> _Object:
    return _Object(
        name,
        object_id,
        "Fem::FemPostPipeline",
        **properties,
    )


def _output(
    name: str = "Output",
    object_id: int = 3,
    **properties: Any,
) -> _Object:
    return _Object(
        name,
        object_id,
        "App::TextDocument",
        **properties,
    )


def test_wholly_legacy_solver_results_adopt_once_in_history_order(
    objecttools: ModuleType,
) -> None:
    solver = _solver()
    root = _pipeline()
    output = _output()
    solver.Results = [root, output]
    document = _Document(
        [solver, root, output],
        [solver, root, output],
    )

    result = objecttools._ensure_exact_retained_result_graph(solver)

    assert result == "adopted"
    assert document.adoptions == [
        (solver, (output, root), (root, solver)),
    ]
    assert all(
        isinstance(identity, int) or identity == "VibeCADTimeline"
        for identity in document.lookups
    )


def test_non_result_objecttools_users_do_not_require_a_history(
    objecttools: ModuleType,
) -> None:
    mesh = _Object("Mesh", 1, "Fem::FemMeshObject")
    document = _Document([mesh], [])
    document._timeline = None

    assert objecttools._ensure_exact_retained_result_graph(mesh) == "none"
    assert document.lookups == [mesh.ID]


def test_adoption_precedes_the_normal_native_update_and_staging_path(
    objecttools: ModuleType,
) -> None:
    source = inspect.getsource(
        objecttools.ObjectTools._process_finished,
    )

    assert source.index("_ensure_exact_retained_result_graph(self.obj)") < source.index(
        "result_graph = self.update_properties()"
    )


def test_canonical_owned_solver_results_skip_adoption(
    objecttools: ModuleType,
) -> None:
    solver = _solver()
    solver.PropertiesList.append("VibeCADTimelineRole")
    solver._property_types["VibeCADTimelineRole"] = "App::PropertyString"
    solver.VibeCADTimelineRole = "operation"
    root = _pipeline(
        VibeCADTimelineRole="resource",
        VibeCADTimelineOwner=solver,
    )
    output = _output(
        VibeCADTimelineRole="resource",
        VibeCADTimelineOwner=root,
    )
    solver.Results = [root, output]
    document = _Document(
        [solver, root, output],
        [output, root, solver],
    )

    result = objecttools._ensure_exact_retained_result_graph(solver)

    assert result == "canonical"
    assert document.adoptions == []


def test_canonical_graph_allows_internal_resource_omitted_from_solver_results(
    objecttools: ModuleType,
) -> None:
    solver = _solver()
    solver.PropertiesList.append("VibeCADTimelineRole")
    solver._property_types["VibeCADTimelineRole"] = "App::PropertyString"
    solver.VibeCADTimelineRole = "operation"
    root = _pipeline(
        VibeCADTimelineRole="resource",
        VibeCADTimelineOwner=solver,
    )
    omitted = _output(
        "OmittedOutput",
        3,
        VibeCADTimelineRole="resource",
        VibeCADTimelineOwner=root,
    )
    returned = _output(
        "ReturnedOutput",
        4,
        VibeCADTimelineRole="resource",
        VibeCADTimelineOwner=root,
    )
    solver.Results = [root, returned]
    document = _Document(
        [solver, root, omitted, returned],
        [omitted, returned, root, solver],
    )

    assert objecttools._ensure_exact_retained_result_graph(solver) == "canonical"
    assert document.adoptions == []


def test_previous_result_metadata_is_normalized_during_exact_adoption(
    objecttools: ModuleType,
) -> None:
    solver = _solver()
    root = _pipeline(
        VibeCADTimelineRole="operation",
        VibeCADResultSolver=f"{solver.Name}:{solver.ID}",
    )
    output = _output()
    solver.Results = [root, output]
    document = _Document(
        [solver, root, output],
        [solver, output, root],
    )

    assert objecttools._ensure_exact_retained_result_graph(solver) == "adopted"
    assert "VibeCADResultSolver" not in root.PropertiesList
    assert document.adoptions == [
        (solver, (output, root), (root, solver)),
    ]


def test_partial_previous_source_metadata_is_removed_during_adoption(
    objecttools: ModuleType,
) -> None:
    solver = _solver()
    root = _pipeline(VibeCADResultSolver=None)
    output = _output()
    solver.Results = [root, output]
    document = _Document(
        [solver, root, output],
        [solver, root, output],
    )

    assert objecttools._ensure_exact_retained_result_graph(solver) == "adopted"
    assert "VibeCADResultSolver" not in root.PropertiesList
    assert document.adoptions == [
        (solver, (output, root), (root, solver)),
    ]


def test_noncontiguous_legacy_results_reject_without_adoption(
    objecttools: ModuleType,
) -> None:
    solver = _solver()
    root = _pipeline()
    output = _output()
    unrelated = _Object("Unrelated", 4, "Part::Feature")
    solver.Results = [root, output]
    document = _Document(
        [solver, root, output, unrelated],
        [solver, root, unrelated, output],
    )

    with pytest.raises(
        RuntimeError,
        match="contiguous History segment",
    ):
        objecttools._ensure_exact_retained_result_graph(solver)

    assert document.adoptions == []


def test_multiple_legacy_pipelines_reject_as_ambiguous(
    objecttools: ModuleType,
) -> None:
    solver = _solver()
    first = _pipeline("FirstPipeline", 2)
    second = _pipeline("SecondPipeline", 3)
    solver.Results = [first, second]
    document = _Document(
        [solver, first, second],
        [solver, first, second],
    )

    with pytest.raises(
        RuntimeError,
        match="cannot be assigned to exact result generations",
    ):
        objecttools._ensure_exact_retained_result_graph(solver)

    assert document.adoptions == []


def test_solver_result_identity_is_verified_by_id_not_name(
    objecttools: ModuleType,
) -> None:
    solver = _solver()
    root = _pipeline()
    output = _output()
    replacement = _output("Replacement", output.ID)
    solver.Results = [root, output]
    document = _Document(
        [solver, root, replacement],
        [solver, root, output],
    )
    output.Document = document

    with pytest.raises(
        RuntimeError,
        match="missing, duplicate, or cross-document",
    ):
        objecttools._ensure_exact_retained_result_graph(solver)

    assert output.Name not in document.lookups
