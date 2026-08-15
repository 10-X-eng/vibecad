# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyInspect as inspect_module
import VibeCADNativeAssemblyInspectRuntime as runtime_module
from VibeCADNativeAssemblyInspect import (
    NativeAssemblyInspectError,
    read_selected_linked_assembly,
)
from VibeCADNativeAssemblyInspectRuntime import NativeAssemblyInspectRuntime
from VibeCADNativeAssemblyInspectSchema import (
    ASSEMBLY_INSPECT_CAPABILITY_NAME,
    assembly_inspect_capability_definition,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeTargets import NativeObjectRef


class _Document:
    def __init__(self, name: str, uid: str) -> None:
        self.Name = name
        self.Uid = uid
        self.Objects = []

    def add(self, obj) -> None:
        obj.Document = self
        self.Objects.append(obj)

    def getObject(self, name: str):
        return next((obj for obj in self.Objects if obj.Name == name), None)


class _Object:
    def __init__(self, name: str, object_id: int, type_id: str, label: str) -> None:
        self.Name = name
        self.ID = object_id
        self.TypeId = type_id
        self.Label = label
        self.Document = None
        self.Rigid = False
        self.source = None

    def isDerivedFrom(self, type_id: str) -> bool:
        return self.TypeId == type_id

    def getLinkedAssembly(self):
        return self.source


class _Selection:
    def __init__(self, *objects, subelements=()) -> None:
        self.entries = [
            SimpleNamespace(Object=obj, SubElementNames=list(subelements))
            for obj in objects
        ]

    def getSelectionEx(self):
        return list(self.entries)


def _graph(monkeypatch):
    target = _Document("Target", "target-uid")
    source_document = _Document("Source", "source-uid")
    link = _Object("SubassemblyLink", 11, "Assembly::AssemblyLink", "Occurrence")
    source = _Object(
        "SourceAssembly", 22, "Assembly::AssemblyObject", "Source Assembly"
    )
    link.source = source
    target.add(link)
    source_document.add(source)
    monkeypatch.setattr(inspect_module, "_timeline_active", lambda _obj: True)
    return target, source_document, link, source


def test_schema_maps_only_the_shipped_link_navigation_action() -> None:
    definition = assembly_inspect_capability_definition()

    assert definition.name == ASSEMBLY_INSPECT_CAPABILITY_NAME
    assert definition.primary_classification == "read"
    assert len(definition.variants) == 1
    variant = definition.variants[0]
    assert variant.operation == "linked_source"
    assert variant.action_ids == frozenset({"Assembly_LinkSelectLinked"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert variant.transaction_behavior == "none"
    assert variant.parameters["required"] == ["link"]


def test_read_returns_exact_external_source_without_mutation(monkeypatch) -> None:
    target, _source_document, link, source = _graph(monkeypatch)
    selection = _Selection(link, subelements=("Face1",))
    guards = []

    result = read_selected_linked_assembly(
        target,
        NativeObjectRef(target.Uid, link.Name),
        guard=lambda: guards.append(True),
        selection_api=selection,
    )

    assert len(guards) == 2
    assert result == {
        "operation": "linked_source",
        "assembly_link": {
            "document_uid": target.Uid,
            "object_name": link.Name,
            "type_id": link.TypeId,
            "document_name": target.Name,
            "object_id": link.ID,
            "label": link.Label,
        },
        "linked_assembly": {
            "document_uid": source.Document.Uid,
            "object_name": source.Name,
            "type_id": source.TypeId,
            "document_name": source.Document.Name,
            "object_id": source.ID,
            "label": source.Label,
        },
        "source_is_external": True,
        "rigid": False,
        "selected_subelements": ["Face1"],
        "selection_unchanged": True,
        "active_document_unchanged": True,
        "document_graph_unchanged": True,
    }


@pytest.mark.parametrize("selection_kind", ("empty", "multiple", "wrong"))
def test_read_rejects_any_nonexact_human_selection(monkeypatch, selection_kind) -> None:
    target, _source_document, link, _source = _graph(monkeypatch)
    other = _Object("Other", 33, "Part::Feature", "Other")
    target.add(other)
    selected = {
        "empty": (),
        "multiple": (link, other),
        "wrong": (other,),
    }[selection_kind]

    with pytest.raises(NativeAssemblyInspectError):
        read_selected_linked_assembly(
            target,
            NativeObjectRef(target.Uid, link.Name),
            guard=lambda: None,
            selection_api=_Selection(*selected),
        )


def test_read_rejects_selection_or_source_change_during_call(monkeypatch) -> None:
    target, _source_document, link, _source = _graph(monkeypatch)
    selection = _Selection(link)
    guard_count = 0

    def guard() -> None:
        nonlocal guard_count
        guard_count += 1
        if guard_count == 2:
            selection.entries.clear()

    with pytest.raises(NativeAssemblyInspectError, match="selection changed"):
        read_selected_linked_assembly(
            target,
            NativeObjectRef(target.Uid, link.Name),
            guard=guard,
            selection_api=selection,
        )


def test_runtime_decodes_only_the_closed_linked_source_variant(monkeypatch) -> None:
    context = SimpleNamespace(
        document_uid="target-uid",
        document=object(),
        guard=lambda: None,
    )
    runtime = object.__new__(NativeAssemblyInspectRuntime)
    runtime._context = context
    calls = []
    monkeypatch.setattr(
        runtime_module,
        "read_selected_linked_assembly",
        lambda document, reference, *, guard: (
            calls.append((document, reference, guard)) or {"operation": "linked_source"}
        ),
    )

    result = runtime.inspect(
        {
            "operation": "linked_source",
            "link": {"object_name": "SubassemblyLink"},
        }
    )

    assert result == {"operation": "linked_source"}
    assert calls[0][0] is context.document
    assert calls[0][1] == NativeObjectRef("target-uid", "SubassemblyLink")
    assert calls[0][2] is context.guard
    with pytest.raises(Exception):
        runtime.inspect(
            {
                "operation": "linked_source",
                "link": {"object_name": "SubassemblyLink"},
                "unexpected": True,
            }
        )


def test_production_registry_contains_the_complete_inspection_family() -> None:
    registry = build_native_capability_registry()

    assert registry.definition(ASSEMBLY_INSPECT_CAPABILITY_NAME) is not None
    assert registry.implementation(ASSEMBLY_INSPECT_CAPABILITY_NAME) is not None
