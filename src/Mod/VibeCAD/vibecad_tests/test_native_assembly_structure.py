# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblySnapshot as snapshot_module
import VibeCADNativeAssemblyStructure as structure_module
from VibeCADNativeAssemblySnapshot import build_assembly_snapshot
from VibeCADNativeAssemblyState import (
    NativeAssemblyStateError,
    read_active_assembly,
)
from VibeCADNativeAssemblyStructure import (
    AssemblyCreateSpec,
    NativeAssemblyStructureError,
    create_assembly,
    preflight_create_assembly,
    verify_created_assembly,
)
from VibeCADNativeAssemblyStructureSchema import (
    assembly_structure_capability_definition,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeTargets import NativeObjectRef


class _Object:
    def __init__(
        self,
        document: "_Document",
        name: str,
        type_id: str,
        *,
        parent: "_Object | None" = None,
    ) -> None:
        self.Document = document
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.Type = ""
        self.ID = document.next_id
        document.next_id += 1
        self.Group: list[_Object] = []
        self.State = []
        self._parent = parent
        self.ViewObject = SimpleNamespace(isInEditMode=lambda: True)

    def isDerivedFrom(self, expected: str) -> bool:
        return self.TypeId == expected

    def getParentGeoFeatureGroup(self):
        return self._parent

    def newObject(self, type_id: str, base_name: str):
        return self.Document._create(type_id, base_name, parent=self)


class _Document:
    Uid = "assembly-document"
    Name = "AssemblyDocument"

    def __init__(self) -> None:
        self.Objects: list[_Object] = []
        self.next_id = 1

    def _unique_name(self, base_name: str) -> str:
        if self.getObject(base_name) is None:
            return base_name
        index = 1
        while self.getObject(f"{base_name}{index:03d}") is not None:
            index += 1
        return f"{base_name}{index:03d}"

    def _create(
        self,
        type_id: str,
        base_name: str,
        *,
        parent: _Object | None = None,
    ) -> _Object:
        value = _Object(
            self,
            self._unique_name(base_name),
            type_id,
            parent=parent,
        )
        self.Objects.append(value)
        if parent is not None:
            parent.Group.append(value)
        return value

    def addObject(self, type_id: str, base_name: str) -> _Object:
        return self._create(type_id, base_name)

    def getObject(self, name: str):
        return next((obj for obj in self.Objects if obj.Name == name), None)


def _assembly(document: _Document, *, parent: _Object | None = None) -> _Object:
    result = (
        document.addObject("Assembly::AssemblyObject", "Assembly")
        if parent is None
        else parent.newObject("Assembly::AssemblyObject", "Assembly")
    )
    result.Type = "Assembly"
    result.newObject("Assembly::JointGroup", "Joints")
    return result


def test_structure_schema_exactly_covers_the_live_create_action() -> None:
    definition = assembly_structure_capability_definition()
    schema = definition.provider_schema(("create_assembly",))["parameters"][
        "oneOf"
    ][0]

    assert definition.name == "assembly.structure"
    assert definition.primary_classification == "mutation"
    assert definition.variants[0].action_ids == frozenset(
        {"Assembly_CreateAssembly"}
    )
    assert definition.variants[0].surface_ids == frozenset({"assemble"})
    assert set(schema["required"]) == {
        "operation",
        "label",
        "parent_assembly",
        "expected_assembly_count",
    }
    assert schema["additionalProperties"] is False
    assert schema["properties"]["parent_assembly"]["oneOf"][1] == {
        "type": "null"
    }

    registry = build_native_capability_registry()
    registered = registry.definition("assembly.structure")
    assert registered is not None
    assert tuple(variant.operation for variant in registered.variants) == (
        "create_assembly",
        "insert_component",
        "create_part",
        "solve_assembly",
        "create_view",
        "create_simulation",
        "create_bom",
    )
    assert registry.implementation("assembly.structure") is not None


def test_active_assembly_read_is_exact_and_nonmutating() -> None:
    document = _Document()
    active = _assembly(document)
    before = tuple(document.Objects)
    view = SimpleNamespace(getActiveObject=lambda role: active if role == "assembly" else None)
    gui_document = SimpleNamespace(Document=document, ActiveView=view)

    assert read_active_assembly(
        document,
        gui_document=gui_document,
        timeline_active=lambda obj: obj is active,
    ) is active
    assert tuple(document.Objects) == before

    other = _Document()
    with pytest.raises(NativeAssemblyStateError, match="another document"):
        read_active_assembly(
            document,
            gui_document=SimpleNamespace(Document=other, ActiveView=view),
            timeline_active=lambda _obj: True,
        )
    with pytest.raises(NativeAssemblyStateError, match="no readable GUI"):
        read_active_assembly(
            document,
            gui_document_reader=lambda _document: None,
        )


def test_snapshot_identifies_only_the_human_active_assembly(monkeypatch) -> None:
    document = _Document()
    active = _assembly(document)
    inactive = _assembly(document)
    monkeypatch.setattr(snapshot_module, "read_active_assembly", lambda _doc: active)

    snapshot = build_assembly_snapshot(document)

    assert snapshot["assembly_count"] == 2
    assert snapshot["active_assembly"]["object_name"] == active.Name
    assert [item["active"] for item in snapshot["assemblies"]] == [True, False]
    assert snapshot["assemblies"][1]["object_name"] == inactive.Name


def test_root_assembly_creation_is_atomic_structure_without_activation(
    monkeypatch,
) -> None:
    document = _Document()
    monkeypatch.setattr(structure_module, "_timeline_active", lambda _obj: True)
    spec = AssemblyCreateSpec("Main mechanism", None, 0)

    def no_active(_document):
        return None

    draft = create_assembly(
        document,
        spec,
        active_reader=no_active,
        enforce_one_root=lambda: True,
    )
    result = verify_created_assembly(document, draft, active_reader=no_active)

    assert len(draft.created) == 2
    assert result["nested"] is False
    assert result["active_assembly_unchanged"] is True
    assert result["assembly_count"] == 1
    assembly = document.getObject(result["assembly"]["object_name"])
    assert assembly.Label == "Main mechanism"
    assert [child.TypeId for child in assembly.Group] == ["Assembly::JointGroup"]

    with pytest.raises(NativeAssemblyStructureError, match="one-root"):
        preflight_create_assembly(
            document,
            AssemblyCreateSpec("Second root", None, 1),
            active_reader=no_active,
            enforce_one_root=lambda: True,
        )


def test_nested_creation_requires_the_exact_human_active_parent(monkeypatch) -> None:
    document = _Document()
    parent = _assembly(document)
    monkeypatch.setattr(structure_module, "_timeline_active", lambda _obj: True)
    spec = AssemblyCreateSpec(
        "Nested mechanism",
        NativeObjectRef(document.Uid, parent.Name),
        1,
    )

    def parent_active(_document):
        return parent

    draft = create_assembly(
        document,
        spec,
        active_reader=parent_active,
        enforce_one_root=lambda: True,
    )
    result = verify_created_assembly(
        document,
        draft,
        active_reader=parent_active,
    )

    assert result["nested"] is True
    assert result["parent_assembly"]["object_name"] == parent.Name
    nested = document.getObject(result["assembly"]["object_name"])
    assert nested in parent.Group
    assert nested.getParentGeoFeatureGroup() is parent

    before = tuple(document.Objects)
    with pytest.raises(NativeAssemblyStructureError, match="human-active"):
        preflight_create_assembly(
            document,
            AssemblyCreateSpec("Stale", None, 2),
            active_reader=parent_active,
            enforce_one_root=lambda: True,
        )
    assert tuple(document.Objects) == before


def test_stale_count_is_rejected_before_any_factory_call() -> None:
    document = _Document()
    before = tuple(document.Objects)

    with pytest.raises(NativeAssemblyStructureError, match="count changed"):
        preflight_create_assembly(
            document,
            AssemblyCreateSpec("Stale", None, 1),
            active_reader=lambda _document: None,
            enforce_one_root=lambda: True,
        )

    assert tuple(document.Objects) == before
