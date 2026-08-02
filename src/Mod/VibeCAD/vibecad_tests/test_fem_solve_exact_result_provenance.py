# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact native-result provenance for the asynchronous FEM service."""

from __future__ import annotations

from typing import Any

import pytest

from tool_impl.service import fem_solve


class _Object:
    def __init__(self, name: str, object_id: int, **properties: Any) -> None:
        self.Name = name
        self.ID = object_id
        self.Document = None
        self.PropertiesList = list(properties)
        self._property_types = {}
        for name, value in properties.items():
            setattr(self, name, value)
            self._property_types[name] = (
                "App::PropertyString"
                if name == "VibeCADTimelineRole"
                else "App::PropertyLinkHidden"
            )

    def getTypeIdOfProperty(self, name: str) -> str:
        return self._property_types[name]


class _Timeline:
    TypeId = "App::DocumentTimeline"

    def __init__(self, operations: list[_Object]) -> None:
        self.Operations = operations


class _Document:
    def __init__(self, objects: list[_Object]) -> None:
        self._objects = {obj.Name: obj for obj in objects}
        self._objects_by_id = {obj.ID: obj for obj in objects}
        for obj in objects:
            obj.Document = self

    def add_timeline(self, operations: list[_Object]) -> None:
        self._objects["VibeCADTimeline"] = _Timeline(operations)

    def getObject(self, identity: str | int) -> Any:
        if isinstance(identity, int):
            return self._objects_by_id.get(identity)
        return self._objects.get(identity)


def _fixture(
    root_lifecycle: str,
    resource_lifecycle: str = "created",
) -> tuple[_Object, _Object, _Object, dict[str, Any]]:
    solver = _Object("Solver", 1)
    root = _Object(
        "Pipeline",
        2,
        VibeCADTimelineRole="operation",
        VibeCADResultSolver=solver,
    )
    resource = _Object(
        "DatOutput",
        3,
        VibeCADTimelineRole="resource",
        VibeCADTimelineOwner=root,
    )
    document = _Document([solver, root, resource])
    document.add_timeline([resource, root])
    diagnostics = {
        "property_update": {
            "status": "completed",
            "result_graph": {
                "root": {
                    "name": root.Name,
                    "id": root.ID,
                    "lifecycle": root_lifecycle,
                },
                "resources": [
                    {
                        "name": resource.Name,
                        "id": resource.ID,
                        "lifecycle": resource_lifecycle,
                    }
                ],
            },
        }
    }
    return solver, root, resource, diagnostics


def test_new_result_root_and_resources_are_exact_created_results() -> None:
    solver, root, resource, diagnostics = _fixture("created")

    generation = fem_solve._exact_imported_result_generation(
        solver,
        diagnostics,
    )

    assert generation == {
        "created_results": ["Pipeline", "DatOutput"],
        "changed_results": [],
        "result_objects": [root, resource],
    }


def test_retained_result_root_is_changed_and_new_resources_are_created() -> None:
    solver, root, resource, diagnostics = _fixture("updated")

    generation = fem_solve._exact_imported_result_generation(
        solver,
        diagnostics,
    )

    assert generation == {
        "created_results": ["DatOutput"],
        "changed_results": ["Pipeline"],
        "result_objects": [resource, root],
    }


def test_retained_result_root_and_resource_are_both_exact_changes() -> None:
    solver, root, resource, diagnostics = _fixture(
        "updated",
        "updated",
    )

    generation = fem_solve._exact_imported_result_generation(
        solver,
        diagnostics,
    )

    assert generation == {
        "created_results": [],
        "changed_results": ["Pipeline", "DatOutput"],
        "result_objects": [root, resource],
    }


def test_missing_native_graph_keeps_legacy_runtime_compatibility() -> None:
    solver, _root, _resource, _diagnostics = _fixture("created")

    assert (
        fem_solve._exact_imported_result_generation(
            solver,
            {"property_update": {"status": "completed"}},
        )
        is None
    )


def test_native_importer_can_exactly_report_no_results() -> None:
    solver, _root, _resource, _diagnostics = _fixture("created")

    assert fem_solve._exact_imported_result_generation(
        solver,
        {
            "property_update": {
                "status": "completed",
                "result_graph": None,
            }
        },
    ) == {
        "created_results": [],
        "changed_results": [],
        "result_objects": [],
    }


def test_native_graph_requires_its_completed_property_update() -> None:
    solver, _root, _resource, diagnostics = _fixture("created")
    diagnostics["property_update"]["status"] = "failed"

    with pytest.raises(
        RuntimeError,
        match="completed property update",
    ):
        fem_solve._exact_imported_result_generation(
            solver,
            diagnostics,
        )


def test_new_root_rejects_an_updated_pre_existing_resource() -> None:
    solver, _root, _resource, diagnostics = _fixture(
        "created",
        "updated",
    )

    with pytest.raises(
        ValueError,
        match="newly created FEM result root",
    ):
        fem_solve._exact_imported_result_generation(
            solver,
            diagnostics,
        )


def test_native_graph_rejects_name_reuse_with_a_different_object_id() -> None:
    solver, _root, _resource, diagnostics = _fixture("created")
    diagnostics["property_update"]["result_graph"]["root"]["id"] = 200

    with pytest.raises(
        RuntimeError,
        match="changed exact document identity",
    ):
        fem_solve._exact_imported_result_generation(
            solver,
            diagnostics,
        )


def test_native_graph_rejects_a_name_mismatch_for_the_exact_object_id() -> None:
    solver, _root, _resource, diagnostics = _fixture("created")
    diagnostics["property_update"]["result_graph"]["root"]["name"] = (
        "DifferentName"
    )

    with pytest.raises(
        RuntimeError,
        match="changed exact document identity",
    ):
        fem_solve._exact_imported_result_generation(
            solver,
            diagnostics,
        )


def test_native_graph_accepts_canonical_nested_resource_ownership() -> None:
    solver = _Object("Solver", 1)
    root = _Object(
        "Pipeline",
        2,
        VibeCADTimelineRole="operation",
        VibeCADResultSolver=solver,
    )
    parent = _Object(
        "ResultGroup",
        3,
        VibeCADTimelineRole="resource",
        VibeCADTimelineOwner=root,
    )
    leaf = _Object(
        "DatOutput",
        4,
        VibeCADTimelineRole="resource",
        VibeCADTimelineOwner=parent,
    )
    document = _Document([solver, root, parent, leaf])
    document.add_timeline([leaf, parent, root])

    generation = fem_solve._exact_imported_result_generation(
        solver,
        {
            "property_update": {
                "status": "completed",
                "result_graph": {
                    "root": {
                        "name": root.Name,
                        "id": root.ID,
                        "lifecycle": "updated",
                    },
                    "resources": [
                        {
                            "name": leaf.Name,
                            "id": leaf.ID,
                            "lifecycle": "updated",
                        },
                        {
                            "name": parent.Name,
                            "id": parent.ID,
                            "lifecycle": "updated",
                        },
                    ],
                },
            }
        },
    )

    assert generation == {
        "created_results": [],
        "changed_results": [
            "Pipeline",
            "DatOutput",
            "ResultGroup",
        ],
        "result_objects": [root, leaf, parent],
    }


def test_native_graph_rejects_an_omitted_owned_resource() -> None:
    solver, root, _resource, diagnostics = _fixture("updated")
    extra = _Object(
        "ExtraOutput",
        4,
        VibeCADTimelineRole="resource",
        VibeCADTimelineOwner=root,
    )
    document = solver.Document
    extra.Document = document
    document._objects[extra.Name] = extra
    document._objects_by_id[extra.ID] = extra
    document.getObject("VibeCADTimeline").Operations.insert(1, extra)

    with pytest.raises(
        RuntimeError,
        match="does not exactly match",
    ):
        fem_solve._exact_imported_result_generation(
            solver,
            diagnostics,
        )


def test_native_graph_rejects_an_unowned_resource() -> None:
    solver, _root, resource, diagnostics = _fixture("created")
    resource.VibeCADTimelineOwner = None

    with pytest.raises(
        RuntimeError,
        match="owner changed exact identity",
    ):
        fem_solve._exact_imported_result_generation(
            solver,
            diagnostics,
        )
