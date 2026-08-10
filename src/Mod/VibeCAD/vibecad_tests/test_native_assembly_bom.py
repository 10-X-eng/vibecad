# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import replace

import pytest

import VibeCADNativeAssemblyBom as bom_module
import VibeCADNativeAssemblyBomState as state_module
import VibeCADNativeAssemblyStructureRuntime as runtime_module
from VibeCADNativeAssemblyBom import (
    AssemblyBomCreateSpec,
    NativeAssemblyBomError,
    create_assembly_bom,
    preflight_create_assembly_bom,
)
from VibeCADNativeAssemblyBomState import (
    AssemblyBomState,
    NativeAssemblyBomStateError,
    _source_graph,
    read_bom_table,
)
from VibeCADNativeAssemblySolveState import AssemblySolverState
from VibeCADNativeAssemblyStructureSchema import (
    assembly_structure_capability_definition,
)
from VibeCADNativeTargets import NativeObjectRef


class _Document:
    Uid = "native-assembly-bom-document"
    Name = "NativeAssemblyBomDocument"
    FileName = "/tmp/native-assembly-bom.FCStd"

    def __init__(self) -> None:
        self.objects = {}

    def add(self, name: str, type_id: str):
        obj = _Object(self, name, type_id, len(self.objects) + 1)
        self.objects[name] = obj
        return obj

    def getObject(self, name: str):
        return self.objects.get(name)


class _Object:
    def __init__(self, document, name: str, type_id: str, object_id: int) -> None:
        self.Document = document
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.ID = object_id
        self.Group = []
        self.OutList = []
        self.PropertiesList = []
        self._property_types = {}
        self.Scale = 1.0

    def isDerivedFrom(self, type_id: str) -> bool:
        if self.TypeId == type_id:
            return True
        return (
            self.TypeId == "Assembly::AssemblyObject" and type_id == "App::Part"
        ) or (self.TypeId == "App::LinkElement" and type_id == "App::Link")

    def getTypeIdOfProperty(self, name: str) -> str:
        return self._property_types[name]

    def add_scalar(self, name: str, type_id: str, value) -> None:
        self.PropertiesList.append(name)
        self._property_types[name] = type_id
        setattr(self, name, value)


class _Sheet:
    def __init__(self, cells: dict[str, str]) -> None:
        self.cells = dict(cells)

    def getUsedRange(self):
        return ("A1", "C3")

    def getContents(self, address: str) -> str:
        return self.cells.get(address, "")


def _fixture() -> tuple[_Document, _Object, _Object, AssemblyBomState]:
    document = _Document()
    assembly = document.add("Assembly", "Assembly::AssemblyObject")
    component = document.add("Occurrence", "App::Link")
    state = AssemblyBomState(
        assembly=assembly,
        components=(component,),
        source_records=({"node_id": "n0000"},),
        bom_group=None,
        boms=(),
        bom_records=(),
        state_sha256="a" * 64,
    )
    return document, assembly, component, state


def _spec(document: _Document, assembly: _Object) -> AssemblyBomCreateSpec:
    return AssemblyBomCreateSpec(
        assembly_ref=NativeObjectRef(document.Uid, assembly.Name),
        label="  Service BOM  ",
        columns=("Index", ".PartNumber", "Quantity"),
        detail_subassemblies=True,
        detail_parts=False,
        only_parts=True,
        expected_bom_state_sha256="a" * 64,
        expected_component_count=1,
        expected_bom_count=0,
    )


def _patch_preflight_state(monkeypatch, state: AssemblyBomState) -> None:
    monkeypatch.setattr(bom_module, "_timeline_active", lambda _obj: True)
    monkeypatch.setattr(
        bom_module,
        "capture_assembly_bom_state",
        lambda _assembly: state,
    )
    monkeypatch.setattr(
        bom_module,
        "capture_assembly_solver_state",
        lambda _assembly: AssemblySolverState((), "b" * 64),
    )


def test_schema_maps_only_bom_action_to_one_closed_bounded_contract() -> None:
    definition = assembly_structure_capability_definition()
    variant = next(
        value for value in definition.variants if value.operation == "create_bom"
    )
    schema = definition.provider_schema(("create_bom",))["parameters"]["oneOf"][0]

    assert variant.action_ids == frozenset({"Assembly_CreateBom"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    assert set(schema["required"]) == {
        "operation",
        "assembly",
        "label",
        "columns",
        "detail_subassemblies",
        "detail_parts",
        "only_parts",
        "expected_bom_state_sha256",
        "expected_component_count",
        "expected_bom_count",
    }
    assert schema["additionalProperties"] is False
    columns = schema["properties"]["columns"]
    assert columns["minItems"] == 1
    assert columns["maxItems"] == 32
    assert columns["uniqueItems"] is True
    assert columns["items"]["maxLength"] == 129
    assert "A-Za-z_" in columns["items"]["pattern"]


def test_runtime_decodes_exact_bom_references_and_ordered_columns() -> None:
    reference = runtime_module._bom_object_ref(
        _Document.Uid,
        {"object_name": "Assembly"},
    )
    columns = runtime_module._bom_columns(["Index", ".PartNumber", "Quantity"])

    assert reference == NativeObjectRef(_Document.Uid, "Assembly")
    assert columns == ("Index", ".PartNumber", "Quantity")
    with pytest.raises(NativeAssemblyBomError, match="exact object"):
        runtime_module._bom_object_ref(
            _Document.Uid,
            {"object_name": "Assembly", "label": "ambiguous"},
        )
    with pytest.raises(NativeAssemblyBomError, match="ordered list"):
        runtime_module._bom_columns("Index")


def test_preflight_freezes_exact_state_and_allows_human_column_sets(
    monkeypatch,
) -> None:
    document, assembly, _component, state = _fixture()
    _patch_preflight_state(monkeypatch, state)
    selection = {"items": [{"object_name": "Occurrence"}]}

    prepared = preflight_create_assembly_bom(
        document,
        _spec(document, assembly),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selection,
    )

    assert prepared.state is state
    assert prepared.spec.label == "Service BOM"
    assert prepared.columns == ("Index", ".PartNumber", "Quantity")
    assert prepared.selection_before == selection

    without_name = replace(_spec(document, assembly), columns=("Quantity",))
    assert preflight_create_assembly_bom(
        document,
        without_name,
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selection,
    ).columns == ("Quantity",)


def test_preflight_rejects_stale_or_ambiguous_requests_before_factory(
    monkeypatch,
) -> None:
    document, assembly, _component, state = _fixture()
    _patch_preflight_state(monkeypatch, state)
    calls = []

    with pytest.raises(NativeAssemblyBomError, match="state changed"):
        create_assembly_bom(
            document,
            replace(
                _spec(document, assembly),
                expected_bom_state_sha256="c" * 64,
            ),
            active_reader=lambda _document: assembly,
            factory=lambda *_args: calls.append(True),
        )
    assert calls == []

    for columns, message in (
        (("Name", "Name"), "unique"),
        ((".1Invalid",), "valid native"),
        (("Name\nInjected",), "valid native"),
    ):
        with pytest.raises(NativeAssemblyBomError, match=message):
            preflight_create_assembly_bom(
                document,
                replace(_spec(document, assembly), columns=columns),
                active_reader=lambda _document: assembly,
            )
    with pytest.raises(NativeAssemblyBomError, match="must be booleans"):
        preflight_create_assembly_bom(
            document,
            replace(_spec(document, assembly), only_parts=1),
            active_reader=lambda _document: assembly,
        )


def test_source_graph_matches_outlist_link_mirroring_and_property_semantics(
    monkeypatch,
) -> None:
    document = _Document()
    assembly = document.add("Assembly", "Assembly::AssemblyObject")
    occurrence = document.add("Occurrence", "App::Link")
    source = document.add("SourcePart", "App::Part")
    solid = document.add("NestedSolid", "Part::Feature")
    ignored = document.add("Joints", "Assembly::JointGroup")
    occurrence.Scale = -1.0
    occurrence.getLinkedObject = lambda: source
    source.add_scalar("PartNumber", "App::PropertyString", "PN-42")
    source.add_scalar("Revision", "App::PropertyInteger", 7)
    source.add_scalar("Tags", "App::PropertyStringList", ["ignored"])
    assembly.OutList = [ignored, occurrence]
    assembly.Group = [ignored]
    source.OutList = [solid]
    monkeypatch.setattr(state_module, "_timeline_active", lambda _obj: True)

    records = _source_graph(assembly)

    assert [record["object"]["object_name"] for record in records] == [
        "Assembly",
        "SourcePart",
        "NestedSolid",
    ]
    edge = records[0]["occurrences"][0]
    assert edge["object"]["object_name"] == "Occurrence"
    assert edge["scale"] == -1.0
    assert edge["mirrored"] is True
    assert [item["name"] for item in records[1]["properties"]] == [
        "PartNumber",
        "Revision",
    ]
    assert records[1]["occurrences"][0]["target_node_id"] == "n0002"

    state = AssemblyBomState(
        assembly=assembly,
        components=(occurrence,),
        source_records=records,
        bom_group=None,
        boms=(),
        bom_records=(),
        state_sha256="d" * 64,
    )
    assert state.summary()["available_property_columns"] == [
        {
            "column": ".PartNumber",
            "property_types": ["App::PropertyString"],
        },
        {
            "column": ".Revision",
            "property_types": ["App::PropertyInteger"],
        },
    ]


def test_bom_table_reader_returns_bounded_literal_preview_and_stable_digest() -> None:
    sheet = _Sheet(
        {
            "A1": "'Index",
            "B1": "'Name",
            "C1": "'Quantity",
            "A2": "'1",
            "B2": "'Bracket",
            "C2": "'2",
            "A3": "'2",
            "B3": "'Bolt",
            "C3": "'4",
        }
    )

    first = read_bom_table(sheet)
    second = read_bom_table(sheet)

    assert first == second
    assert first["used_range"] == ["A1", "C3"]
    assert first["cell_count"] == 9
    assert first["headers"] == ["Index", "Name", "Quantity"]
    assert first["row_count"] == 2
    assert first["row_preview"] == [
        {"Index": "1", "Name": "Bracket", "Quantity": "2"},
        {"Index": "2", "Name": "Bolt", "Quantity": "4"},
    ]
    sheet.cells["C3"] = "'5"
    assert read_bom_table(sheet)["table_sha256"] != first["table_sha256"]

    sheet.cells["B2"] = "'" + "x" * 200
    truncated = read_bom_table(sheet)
    assert truncated["preview_values_truncated"] is True
    assert truncated["row_preview"][0]["Name"].endswith("...")
    with pytest.raises(NativeAssemblyBomStateError, match="cell budget"):
        read_bom_table(sheet, maximum_cells=8)
