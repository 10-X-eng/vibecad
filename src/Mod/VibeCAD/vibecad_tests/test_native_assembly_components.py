# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyComponents as components_module
import VibeCADNativeAssemblySnapshot as snapshot_module
from VibeCADNativeAssemblyComponents import (
    AssemblySourceRef,
    CreatePartSpec,
    InsertComponentSpec,
    NativeAssemblyComponentError,
    create_part,
    insert_component,
    preflight_create_part,
    preflight_insert_component,
    verify_created_part,
    verify_inserted_component,
)
from VibeCADNativeAssemblySnapshot import build_assembly_snapshot
from VibeCADNativeAssemblyStructureSchema import (
    assembly_structure_capability_definition,
)
from VibeCADNativeTargets import NativeObjectRef


_DERIVATIONS = {
    "Assembly::AssemblyObject": {"App::Part"},
    "PartDesign::Body": {"Part::Feature"},
    "Part::Box": {"Part::Feature"},
}


class _Placement:
    def __init__(self, name: str) -> None:
        self.name = name

    def isSame(self, other, _tolerance: float) -> bool:
        return isinstance(other, _Placement) and other.name == self.name

    def isIdentity(self) -> bool:
        return self.name == "origin"


class _Object:
    def __init__(self, document, name: str, type_id: str, parent=None) -> None:
        self.Document = document
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.ID = document.next_id
        document.next_id += 1
        self.Group = []
        self.InListRecursive = []
        self.State = []
        self.Placement = _Placement("origin")
        self.LinkedObject = None
        self.Rigid = True
        self._parent = parent
        self.ViewObject = SimpleNamespace(isInEditMode=lambda: True)

    def isDerivedFrom(self, expected: str) -> bool:
        return self.TypeId == expected or expected in _DERIVATIONS.get(self.TypeId, set())

    def newObject(self, type_id: str, base_name: str):
        return self.Document._create(type_id, base_name, parent=self)

    def hasObject(self, candidate, recursive: bool = False) -> bool:
        if candidate in self.Group:
            return True
        return recursive and any(
            child.hasObject(candidate, True)
            for child in self.Group
            if hasattr(child, "hasObject")
        )

    def recompute(self) -> None:
        return None


class _Document:
    def __init__(self, name: str = "AssemblyDocument", uid: str = "assembly-document"):
        self.Name = name
        self.Uid = uid
        self.FileName = ""
        self.Objects = []
        self.next_id = 1

    def _unique_name(self, base_name: str) -> str:
        if self.getObject(base_name) is None:
            return base_name
        index = 1
        while self.getObject(f"{base_name}{index:03d}") is not None:
            index += 1
        return f"{base_name}{index:03d}"

    def _create(self, type_id: str, base_name: str, parent=None):
        result = _Object(self, self._unique_name(base_name), type_id, parent)
        self.Objects.append(result)
        if parent is not None:
            parent.Group.append(result)
        return result

    def addObject(self, type_id: str, base_name: str):
        return self._create(type_id, base_name)

    def getObject(self, name: str):
        return next((obj for obj in self.Objects if obj.Name == name), None)


def _assembly(document: _Document):
    assembly = document.addObject("Assembly::AssemblyObject", "Assembly")
    assembly.newObject("Assembly::JointGroup", "Joints")
    return assembly


def _source_ref(source: _Object) -> AssemblySourceRef:
    return AssemblySourceRef(
        source.Document.Uid,
        source.Document.Name,
        source.Name,
        source.ID,
    )


def _mark_inserted(occurrence: _Object) -> None:
    occurrence.VibeCADTimelineRole = "operation"
    if occurrence.TypeId == "Assembly::AssemblyLink":
        resource = occurrence.newObject("App::FeaturePython", "ManagedClone")
        resource.VibeCADTimelineRole = "resource"
        resource.VibeCADTimelineOwner = occurrence


def _part_factory(label: str, document: _Document):
    part = document.addObject("App::Part", "Part")
    part.Label = label
    body = part.newObject("PartDesign::Body", "Body")
    return part, body


def _mark_new_part(part: _Object, body: _Object, occurrence: _Object) -> None:
    part.VibeCADTimelineRole = "operation"
    body.VibeCADTimelineRole = "resource"
    body.VibeCADTimelineOwner = part
    occurrence.VibeCADTimelineRole = "resource"
    occurrence.VibeCADTimelineOwner = part


@pytest.fixture(autouse=True)
def _active_timeline(monkeypatch):
    monkeypatch.setattr(components_module, "_timeline_active", lambda _obj: True)


def test_schema_covers_both_live_insert_actions_with_exact_targets() -> None:
    definition = assembly_structure_capability_definition()
    variants = {variant.operation: variant for variant in definition.variants}

    assert tuple(variants) == (
        "create_assembly",
        "insert_component",
        "create_part",
        "solve_assembly",
        "create_view",
        "create_simulation",
        "create_bom",
    )
    assert variants["insert_component"].action_ids == frozenset(
        {"Assembly_InsertLink"}
    )
    assert variants["create_part"].action_ids == frozenset(
        {"Assembly_InsertNewPart"}
    )
    insert_schema = definition.provider_schema(("insert_component",))["parameters"][
        "oneOf"
    ][0]
    assert set(insert_schema["required"]) == {
        "operation",
        "assembly",
        "source",
        "label",
        "placement",
        "rigid",
        "expected_component_count",
    }
    assert insert_schema["additionalProperties"] is False
    assert set(insert_schema["properties"]["source"]["required"]) == {
        "document_uid",
        "document_name",
        "object_name",
        "object_id",
    }


def test_regular_component_insert_is_one_exact_operation_without_activation() -> None:
    document = _Document()
    source = document.addObject("Part::Box", "SourceBox")
    assembly = _assembly(document)
    placement = _Placement("placed")
    def active(_document):
        return assembly
    spec = InsertComponentSpec(
        NativeObjectRef(document.Uid, assembly.Name),
        _source_ref(source),
        "Housing occurrence",
        placement,
        None,
        0,
    )

    draft = insert_component(
        document,
        spec,
        active_reader=active,
        finalizer=_mark_inserted,
    )
    result = verify_inserted_component(document, draft, active_reader=active)

    occurrence = document.getObject(result["occurrence"]["object_name"])
    assert result["component_count"] == 1
    assert result["subassembly"] is False
    assert result["grounded"] is False
    assert occurrence.TypeId == "App::Link"
    assert occurrence.LinkedObject is source
    assert occurrence.Placement.isSame(placement, 1.0e-12)
    assert tuple(item.object_name for item in draft.created) == (occurrence.Name,)


def test_subassembly_insert_owns_its_materialized_resource_graph() -> None:
    document = _Document()
    source = _assembly(document)
    source.Name = "SourceAssembly"
    source.Label = "Source Assembly"
    target = _assembly(document)
    placement = _Placement("origin")
    def active(_document):
        return target
    spec = InsertComponentSpec(
        NativeObjectRef(document.Uid, target.Name),
        _source_ref(source),
        "Flexible module",
        placement,
        False,
        0,
    )

    draft = insert_component(
        document,
        spec,
        active_reader=active,
        finalizer=_mark_inserted,
    )
    result = verify_inserted_component(document, draft, active_reader=active)

    occurrence = document.getObject(result["occurrence"]["object_name"])
    resources = [obj for obj in draft.value["created_objects"] if obj is not occurrence]
    assert occurrence.TypeId == "Assembly::AssemblyLink"
    assert occurrence.Rigid is False
    assert result["rigid"] is False
    assert len(resources) == 1
    assert resources[0].VibeCADTimelineOwner is occurrence
    assert len(draft.created) == 2


def test_insert_preflight_rejects_stale_counts_modes_and_cycles() -> None:
    document = _Document()
    source = document.addObject("Part::Box", "Source")
    assembly = _assembly(document)

    def active(_document):
        return assembly

    base = dict(
        assembly_ref=NativeObjectRef(document.Uid, assembly.Name),
        source_ref=_source_ref(source),
        label="Occurrence",
        placement=_Placement("origin"),
        rigid=None,
    )

    with pytest.raises(NativeAssemblyComponentError, match="count changed"):
        preflight_insert_component(
            document,
            InsertComponentSpec(**base, expected_component_count=1),
            active_reader=active,
        )
    with pytest.raises(NativeAssemblyComponentError, match="rigid must"):
        preflight_insert_component(
            document,
            InsertComponentSpec(**{**base, "rigid": True}, expected_component_count=0),
            active_reader=active,
        )
    assembly.Group.append(source)
    with pytest.raises(NativeAssemblyComponentError, match="dependency cycle"):
        preflight_insert_component(
            document,
            InsertComponentSpec(**base, expected_component_count=1),
            active_reader=active,
        )


def test_nonidentity_flexible_insert_requires_a_placeable_source_component() -> None:
    document = _Document()
    source = _assembly(document)
    source.Name = "EmptySourceAssembly"
    target = _assembly(document)

    def active(_document):
        return target

    with pytest.raises(NativeAssemblyComponentError, match="placeable source"):
        preflight_insert_component(
            document,
            InsertComponentSpec(
                NativeObjectRef(document.Uid, target.Name),
                _source_ref(source),
                "Empty flexible source",
                _Placement("placed"),
                False,
                0,
            ),
            active_reader=active,
        )


def test_create_part_publishes_one_part_body_occurrence_operation() -> None:
    document = _Document()
    assembly = _assembly(document)

    def active(_document):
        return assembly

    placement = _Placement("new-part")
    spec = CreatePartSpec(
        NativeObjectRef(document.Uid, assembly.Name),
        "Drive bracket",
        placement,
        0,
    )

    draft = create_part(
        document,
        spec,
        active_reader=active,
        part_factory=_part_factory,
        finalizer=_mark_new_part,
    )
    result = verify_created_part(document, draft, active_reader=active)

    part = document.getObject(result["part"]["object_name"])
    body = document.getObject(result["body"]["object_name"])
    occurrence = document.getObject(result["occurrence"]["object_name"])
    assert part.Label == "Drive bracket"
    assert body in part.Group
    assert occurrence.LinkedObject is part
    assert occurrence.Placement.isSame(placement, 1.0e-12)
    assert result["component_count"] == 1
    assert result["body_activation_changed"] is False
    assert len(draft.created) == 3


def test_new_part_rejects_a_duplicate_semantic_label_before_mutation() -> None:
    document = _Document()
    existing = document.addObject("Part::Box", "Existing")
    existing.Label = "Drive bracket"
    assembly = _assembly(document)

    def active(_document):
        return assembly

    before = tuple(document.Objects)
    with pytest.raises(NativeAssemblyComponentError, match="already in use"):
        preflight_create_part(
            document,
            CreatePartSpec(
                NativeObjectRef(document.Uid, assembly.Name),
                "Drive bracket",
                _Placement("origin"),
                0,
            ),
            active_reader=active,
        )
    assert tuple(document.Objects) == before


def test_assemble_snapshot_returns_bounded_exact_link_sources(monkeypatch) -> None:
    document = _Document()
    source = document.addObject("Part::Box", "SourceBox")
    source.Label = "Source Box"
    assembly = _assembly(document)
    monkeypatch.setattr(snapshot_module, "read_active_assembly", lambda _doc: assembly)

    snapshot = build_assembly_snapshot(document)

    assert snapshot["active_assembly"]["object_name"] == assembly.Name
    assert snapshot["available_component_sources"] == [
        {
            "document_uid": document.Uid,
            "document_name": document.Name,
            "object_name": source.Name,
            "object_id": source.ID,
            "type_id": source.TypeId,
            "label": source.Label,
            "subassembly": False,
        }
    ]
