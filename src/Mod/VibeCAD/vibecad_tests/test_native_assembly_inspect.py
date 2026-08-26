# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyInspect as inspect_module
import VibeCADNativeAssemblyInspectRuntime as runtime_module
import VibeCADReferenceContracts as reference_contracts
import VibeCADScriptedPublication as publication
from VibeCADNativeAssemblyInspect import (
    NativeAssemblyInspectError,
    read_selected_linked_assembly,
)
from VibeCADNativeAssemblyInspectRuntime import NativeAssemblyInspectRuntime
from VibeCADNativeAssemblyInspectSchema import (
    ASSEMBLY_CONNECTORS_CAPABILITY_NAME,
    ASSEMBLY_INSPECT_CAPABILITY_NAME,
    ASSEMBLY_LINKED_ASSEMBLY_CAPABILITY_NAME,
    assembly_connectors_capability_definition,
    assembly_inspect_capability_definition,
)
from VibeCADNativeCapabilityRegistry import provider_visible_native_schema
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


def test_schema_keeps_link_navigation_without_duplicate_connector_discovery() -> None:
    definition = assembly_inspect_capability_definition()

    assert definition.name == ASSEMBLY_LINKED_ASSEMBLY_CAPABILITY_NAME
    assert definition.description == "Read a nested AssemblyLink's source Assembly."
    assert definition.primary_classification == "read"
    assert tuple(variant.operation for variant in definition.variants) == ("linked_source",)
    variant = definition.variants[0]
    assert variant.operation == "linked_source"
    assert variant.action_ids == frozenset({"Assembly_LinkSelectLinked"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert variant.transaction_behavior == "none"
    assert variant.parameters["required"] == ["link"]


def test_connector_discovery_is_one_focused_operation_without_a_discriminator() -> None:
    definition = assembly_connectors_capability_definition()
    variant = definition.variants[0]
    schema = provider_visible_native_schema(
        definition.provider_schema((variant.operation,))
    )
    parameters = schema["parameters"]["oneOf"][0]

    assert definition.name == ASSEMBLY_CONNECTORS_CAPABILITY_NAME
    assert definition.description == "Find compatible endpoint pairs for a joint."
    assert variant.operation == "find"
    assert variant.surface_ids == frozenset({"assemble"})
    assert parameters["properties"]["joint_type"]["description"] == (
        "Joint being created."
    )
    assert parameters["required"] == [
        "first_component",
        "second_component",
        "joint_type",
    ]
    assert parameters["properties"]["limit"]["maximum"] == 100
    assert "operation" not in parameters["properties"]


def test_connector_runtime_finds_pairs_between_two_components(monkeypatch) -> None:
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
        "read_joint_connector_pairs",
        lambda document, first, second, *, joint_type, limit, guard: (
            calls.append(
                (document, first, second, joint_type, limit, guard)
            )
            or {"operation": "joint_connector_pairs"}
        ),
        raising=False,
    )

    result = runtime.connectors(
        {
            "first_component": {"object_name": "PlanetGear"},
            "second_component": {"object_name": "Carrier"},
            "joint_type": "revolute",
            "limit": 50,
        }
    )

    assert result == {"operation": "joint_connector_pairs"}
    assert calls[0][0] is context.document
    assert calls[0][1:3] == (
        NativeObjectRef("target-uid", "PlanetGear"),
        NativeObjectRef("target-uid", "Carrier"),
    )
    assert calls[0][3:5] == ("revolute", 50)
    assert calls[0][5] is context.guard


def test_connector_pair_ranking_accepts_aircraft_scale_result_pages() -> None:
    assert inspect_module.rank_connector_pairs(
        [],
        [],
        joint_type="revolute",
        limit=50,
    ) == []


def test_connector_inventory_bounds_large_topology_without_losing_origins() -> None:
    shape = SimpleNamespace(
        Faces=[object()] * 5_000,
        Edges=[object()] * 10_000,
        Vertexes=[object()] * 7_500,
    )

    names = inspect_module._bounded_connector_names(shape, "fixed")

    assert len(names) == inspect_module.MAX_JOINT_CONNECTORS
    assert names[-1] == ""
    assert names[0] == "Face1"
    assert any(name == "Face5000" for name in names)
    assert any(name == "Edge10000" for name in names)
    assert any(name == "Vertex7500" for name in names)


def test_connector_pairs_publish_component_origins_first(monkeypatch) -> None:
    first_component = SimpleNamespace(Name="Base")
    second_component = SimpleNamespace(Name="Turret")
    origin = lambda component: {
        "endpoint": {
            "component": component.Name,
            "connector_type": "element",
            "connector": "Origin",
        },
        "element": "",
        "geometry": "component_origin",
        "origin_mm": [0.0, 0.0, 0.0],
        "axis": [0.0, 0.0, 1.0],
    }
    inventories = {
        "Base": (first_component, [origin(first_component)]),
        "Turret": (second_component, [origin(second_component)]),
    }
    monkeypatch.setattr(inspect_module, "read_active_assembly", lambda _doc: object())
    monkeypatch.setattr(
        inspect_module,
        "_joint_connector_inventory",
        lambda _doc, _assembly, reference, _joint_type: inventories[
            reference.object_name
        ],
    )
    monkeypatch.setattr(inspect_module, "_live_object", lambda _obj: True)
    monkeypatch.setattr(
        inspect_module,
        "_exact_object_summary",
        lambda component: {"object_name": component.Name},
    )

    result = inspect_module.read_joint_connector_pairs(
        object(),
        NativeObjectRef("target-uid", "Base"),
        NativeObjectRef("target-uid", "Turret"),
        joint_type="fixed",
        limit=12,
        guard=lambda: None,
    )

    assert result["pairs"] == [
        {
            "first": {
                "component": "Base",
                "connector_type": "element",
                "connector": "Origin",
            },
            "second": {
                "component": "Turret",
                "connector_type": "element",
                "connector": "Origin",
            },
            "first_geometry": {
                "type": "component_origin",
                "origin_mm": [0.0, 0.0, 0.0],
                "axis": [0.0, 0.0, 1.0],
            },
            "second_geometry": {
                "type": "component_origin",
                "origin_mm": [0.0, 0.0, 0.0],
                "axis": [0.0, 0.0, 1.0],
            },
        }
    ]


def test_connector_pairs_exclude_generic_origins_for_revolute_geometry(monkeypatch) -> None:
    first_component = SimpleNamespace(Name="Base")
    second_component = SimpleNamespace(Name="Turret")

    def origin(component):
        return {
            "endpoint": {
                "component": component.Name,
                "connector_type": "element",
                "connector": "Origin",
            },
            "element": "",
            "geometry": "component_origin",
            "origin_mm": [0.0, 0.0, 0.0],
            "axis": [0.0, 0.0, 1.0],
        }

    def cylinder(component, element, radius):
        return {
            "endpoint": {
                "component": component.Name,
                "connector_type": "element",
                "connector": element,
            },
            "element": element,
            "geometry": "cylinder",
            "origin_mm": [0.0, 0.0, 12.0],
            "axis": [0.0, 0.0, 1.0],
            "radius_mm": radius,
            "area_mm2": 100.0,
        }

    inventories = {
        "Base": (
            first_component,
            [origin(first_component), cylinder(first_component, "Face8", 45.0)],
        ),
        "Turret": (
            second_component,
            [origin(second_component), cylinder(second_component, "Face7", 40.0)],
        ),
    }
    monkeypatch.setattr(inspect_module, "read_active_assembly", lambda _doc: object())
    monkeypatch.setattr(
        inspect_module,
        "_joint_connector_inventory",
        lambda _doc, _assembly, reference, _joint_type: inventories[
            reference.object_name
        ],
    )
    monkeypatch.setattr(inspect_module, "_live_object", lambda _obj: True)
    monkeypatch.setattr(
        inspect_module,
        "_exact_object_summary",
        lambda component: {"object_name": component.Name},
    )

    result = inspect_module.read_joint_connector_pairs(
        object(),
        NativeObjectRef("target-uid", "Base"),
        NativeObjectRef("target-uid", "Turret"),
        joint_type="revolute",
        limit=12,
        guard=lambda: None,
    )

    assert [
        (pair["first"]["connector"], pair["second"]["connector"])
        for pair in result["pairs"]
    ] == [("Face8", "Face7")]


def test_connector_inventory_prefers_published_interfaces_without_scanning_faces(
    monkeypatch,
) -> None:
    component = SimpleNamespace(Name="Carrier")
    assembly = object()
    reference = NativeObjectRef("target-uid", "Carrier")
    monkeypatch.setattr(inspect_module, "resolve_object", lambda _doc, _ref: component)
    monkeypatch.setattr(inspect_module, "assembly_components", lambda _assembly: (component,))
    monkeypatch.setattr(inspect_module, "_timeline_active", lambda _obj: True)
    monkeypatch.setattr(
        inspect_module,
        "_component_is_movable",
        lambda _assembly, _component: True,
        raising=False,
    )
    monkeypatch.setattr(
        inspect_module,
        "_published_connector_inventory",
        lambda _component, _joint_type: (
            True,
            [
                {
                    "endpoint": {
                        "component": "Carrier",
                        "connector_type": "interface",
                        "connector": "Planet1Axis",
                    },
                    "element": "Planet1Axis",
                    "geometry": "cylinder",
                    "origin_mm": [22.0, 0.0, 0.0],
                    "axis": [0.0, 0.0, 1.0],
                }
            ],
        ),
        raising=False,
    )
    monkeypatch.setattr(
        inspect_module,
        "source_connector_inventory",
        lambda *_args, **_kwargs: pytest.fail("semantic components must not scan topology"),
    )

    resolved_component, connectors = inspect_module._joint_connector_inventory(
        object(), assembly, reference, "revolute"
    )

    assert resolved_component is component
    assert connectors[0]["endpoint"] == {
        "component": "Carrier",
        "connector_type": "interface",
        "connector": "Planet1Axis",
    }


def test_connector_pair_preserves_authored_endpoint_contracts() -> None:
    def connector(component: str, contract: dict):
        return {
            "endpoint": {
                "component": component,
                "connector_type": "interface",
                "connector": "Axis",
            },
            "element": "Axis",
            "geometry": "cylinder",
            "origin_mm": [0.0, 0.0, 0.0],
            "axis": [0.0, 0.0, 1.0],
            "contract": contract,
        }

    result = inspect_module._pair_record(
        connector(
            "Input",
            {
                "kind": "axis",
                "allowed_joints": ["revolute", "gears"],
                "compatibility": "DRIVE_INPUT",
                "pitch_radius_mm": 30.0,
            },
        ),
        connector(
            "Output",
            {
                "kind": "axis",
                "allowed_joints": ["slider", "screw"],
                "compatibility": "DRIVE_OUTPUT",
                "thread_pitch_mm": 4.0,
            },
        ),
    )

    assert result["first_contract"] == {
        "kind": "axis",
        "allowed_joints": ["revolute", "gears"],
        "compatibility": "DRIVE_INPUT",
        "pitch_radius_mm": 30.0,
    }
    assert result["second_contract"] == {
        "kind": "axis",
        "allowed_joints": ["slider", "screw"],
        "compatibility": "DRIVE_OUTPUT",
        "thread_pitch_mm": 4.0,
    }
    assert "radius1_mm" not in result
    assert "radius2_mm" not in result


def test_occurrence_resolves_authored_interfaces_through_exact_publication_identity() -> None:
    model_id = "model-123"
    output_key = "Drive"
    root = SimpleNamespace(
        Name="Program",
        VibeCADScriptedRole=publication.ROLE_MODEL,
        VibeCADScriptedModelId=model_id,
    )
    target = SimpleNamespace(
        Name="DriveSource",
        VibeCADScriptedRole=publication.ROLE_PUBLICATION_TARGET,
        VibeCADScriptedModelId=model_id,
        VibeCADScriptedOutputKey=output_key,
        InList=[root],
    )
    published = SimpleNamespace(
        Name="PublishedDrive",
        TypeId="App::Link",
        VibeCADScriptedRole=publication.ROLE_PUBLICATION,
        VibeCADScriptedModelId=model_id,
        VibeCADScriptedOutputKey=output_key,
        LinkedObject=(root, "DriveSource."),
    )

    class _PublicationDocument:
        Objects = [root, target, published]

        @staticmethod
        def findObjects(*, Type):
            return [published] if Type == "App::Link" else []

    document = _PublicationDocument()
    root.Document = document
    target.Document = document
    published.Document = document
    occurrence = SimpleNamespace(
        Name="DriveOccurrence",
        TypeId="App::Link",
        LinkedObject=target,
        Document=document,
    )

    assert reference_contracts.published_object(occurrence) is published


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


def test_read_uses_the_explicit_link_when_selection_is_empty(monkeypatch) -> None:
    target, _source_document, link, source = _graph(monkeypatch)

    result = read_selected_linked_assembly(
        target,
        NativeObjectRef(target.Uid, link.Name),
        guard=lambda: None,
        selection_api=_Selection(),
    )

    assert result["assembly_link"]["object_name"] == link.Name
    assert result["linked_assembly"]["object_name"] == source.Name
    assert result["selected_subelements"] == []
    assert result["selection_unchanged"] is True


@pytest.mark.parametrize("selection_kind", ("empty", "multiple", "wrong"))
def test_read_preserves_any_human_selection(monkeypatch, selection_kind) -> None:
    target, _source_document, link, _source = _graph(monkeypatch)
    other = _Object("Other", 33, "Part::Feature", "Other")
    target.add(other)
    selected = {
        "empty": (),
        "multiple": (link, other),
        "wrong": (other,),
    }[selection_kind]

    result = read_selected_linked_assembly(
        target,
        NativeObjectRef(target.Uid, link.Name),
        guard=lambda: None,
        selection_api=_Selection(*selected),
    )

    assert result["assembly_link"]["object_name"] == link.Name
    assert result["selected_subelements"] == []
    assert result["selection_unchanged"] is True


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


def test_runtime_lists_joint_connectors_with_internal_page_defaults(monkeypatch) -> None:
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
        "read_joint_connectors",
        lambda document, reference, *, joint_type, offset, page_size, guard: (
            calls.append(
                (document, reference, joint_type, offset, page_size, guard)
            )
            or {"operation": "joint_connectors"}
        ),
        raising=False,
    )

    result = runtime.inspect(
        {
            "operation": "joint_connectors",
            "component": {"object_name": "PlanetGear"},
            "joint_type": "revolute",
        }
    )

    assert result == {"operation": "joint_connectors"}
    assert calls[0][0] is context.document
    assert calls[0][1] == NativeObjectRef("target-uid", "PlanetGear")
    assert calls[0][2:5] == ("revolute", 0, 48)
    assert calls[0][5] is context.guard


def test_production_registry_contains_the_complete_inspection_family() -> None:
    registry = build_native_capability_registry()

    assert registry.definition(ASSEMBLY_INSPECT_CAPABILITY_NAME) is not None
    assert registry.implementation(ASSEMBLY_INSPECT_CAPABILITY_NAME) is not None
