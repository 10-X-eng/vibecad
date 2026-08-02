# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact timeline publication contracts for direct domain tools."""

from __future__ import annotations

from typing import Any

import pytest

from tool_impl.service import domain_runtime


class _Document:
    def __init__(self) -> None:
        self.objects: dict[str, _Object] = {}
        self.created_by_transaction: set[_Object] = set()
        self.finalized: list[tuple[_Object, list[_Object]]] = []

    def getObject(self, name: str) -> _Object | None:
        return self.objects.get(name)

    def publishProvisionalTimelineOperationBlock(
        self,
        operation: _Object,
        ordered_resources: list[_Object],
    ) -> None:
        block = [*ordered_resources, operation]
        if not all(obj in self.created_by_transaction for obj in block):
            raise RuntimeError(
                "The operation graph was not created by this exact active "
                "transaction."
            )
        domain_runtime._mark_timeline_operation(operation)
        for resource in ordered_resources:
            domain_runtime._mark_timeline_resource(resource, operation)
        self.finalized.append((operation, block))


class _Object:
    def __init__(self, document: _Document, name: str) -> None:
        self.Document = document
        self.Name = name
        self.PropertiesList: list[str] = []
        self._property_types: dict[str, str] = {}
        self._editor_modes: dict[str, int] = {}
        self._property_statuses: dict[str, set[str]] = {}
        document.objects[name] = self

    def addProperty(
        self,
        type_id: str,
        property_name: str,
        *_args: Any,
        **_kwargs: Any,
    ) -> None:
        self.PropertiesList.append(property_name)
        self._property_types[property_name] = type_id

    def getTypeIdOfProperty(self, property_name: str) -> str:
        return self._property_types[property_name]

    def setEditorMode(self, property_name: str, mode: int) -> None:
        self._editor_modes[property_name] = mode

    def setPropertyStatus(
        self,
        property_name: str,
        statuses: str | tuple[str, ...],
    ) -> None:
        if isinstance(statuses, str):
            statuses = (statuses,)
        self._property_statuses.setdefault(property_name, set()).update(statuses)


def test_direct_domain_block_uses_only_explicit_exact_identities() -> None:
    document = _Document()
    operation = _Object(document, "Analysis")
    first_resource = _Object(document, "Solver")
    second_resource = _Object(document, "Report")
    document.created_by_transaction.update(
        {operation, first_resource, second_resource}
    )

    domain_runtime.finalize_new_timeline_operation(
        operation,
        [first_resource, second_resource],
    )

    assert operation.VibeCADTimelineRole == "operation"
    assert "VibeCADTimelineOwner" not in operation.PropertiesList
    assert operation._property_statuses["VibeCADTimelineRole"] == {
        "Hidden",
        "LockDynamic",
        "NoRecompute",
    }
    for resource in (first_resource, second_resource):
        assert resource.VibeCADTimelineRole == "resource"
        assert resource.VibeCADTimelineOwner is operation
        assert (
            resource.getTypeIdOfProperty("VibeCADTimelineOwner")
            == "App::PropertyLinkHidden"
        )
        assert resource._property_statuses["VibeCADTimelineRole"] == {
            "Hidden",
            "LockDynamic",
            "NoRecompute",
        }
        assert resource._property_statuses["VibeCADTimelineOwner"] == {
            "Hidden",
            "LockDynamic",
            "NoRecompute",
        }
    assert document.finalized == [
        (
            operation,
            [first_resource, second_resource, operation],
        )
    ]


def test_direct_domain_block_rejects_duplicate_identity_before_metadata() -> None:
    document = _Document()
    operation = _Object(document, "Page")
    resource = _Object(document, "Template")
    document.created_by_transaction.update({operation, resource})

    with pytest.raises(ValueError, match="distinct exact live"):
        domain_runtime.finalize_new_timeline_operation(
            operation,
            [resource, resource],
        )

    assert operation.PropertiesList == []
    assert resource.PropertiesList == []
    assert document.finalized == []


def test_direct_domain_block_requires_native_current_transaction_proof() -> None:
    document = _Document()
    operation = _Object(document, "Analysis")
    resource = _Object(document, "Solver")
    document.created_by_transaction.add(operation)

    with pytest.raises(RuntimeError, match="exact active transaction"):
        domain_runtime.finalize_new_timeline_operation(
            operation,
            [resource],
        )

    assert operation.PropertiesList == []
    assert resource.PropertiesList == []
    assert document.finalized == []


def test_new_operation_scope_publishes_exact_partial_graph_on_failure() -> None:
    document = _Document()
    operation = _Object(document, "DrawingPage")
    resource = _Object(document, "DrawingTemplate")
    document.created_by_transaction.update({operation, resource})

    with pytest.raises(RuntimeError, match="native setup failed"):
        with domain_runtime.NewTimelineOperation() as timeline:
            timeline.set_operation(operation)
            timeline.add_resource(resource)
            raise RuntimeError("native setup failed")

    assert document.finalized == [
        (operation, [resource, operation]),
    ]
    assert operation.VibeCADTimelineRole == "operation"
    assert resource.VibeCADTimelineRole == "resource"
    assert resource.VibeCADTimelineOwner is operation


def test_new_operation_scope_without_created_operation_is_a_noop() -> None:
    document = _Document()

    with pytest.raises(RuntimeError, match="factory failed"):
        with domain_runtime.NewTimelineOperation():
            raise RuntimeError("factory failed")

    assert document.finalized == []
