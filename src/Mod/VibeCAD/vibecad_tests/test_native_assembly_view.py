# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyStructureRuntime as runtime_module
import VibeCADNativeAssemblyView as view_module
from VibeCADNativeAssemblyStructureSchema import (
    assembly_structure_capability_definition,
)
from VibeCADNativeAssemblyView import (
    AssemblyViewCreateSpec,
    AssemblyViewMoveSpec,
    NativeAssemblyViewError,
    preflight_create_assembly_view,
)
from VibeCADNativeAssemblyViewState import (
    AssemblyViewState,
    AssemblyViewTarget,
)
from VibeCADNativeTargets import NativeObjectRef


class _Placement:
    def __init__(self, x: float = 0.0, *, identity: bool = False) -> None:
        self.x = float(x)
        self.identity = bool(identity)

    def isIdentity(self) -> bool:
        return self.identity

    def isSame(self, other, tolerance: float) -> bool:
        return isinstance(other, _Placement) and abs(self.x - other.x) <= tolerance


class _Object:
    def __init__(self, document, name: str, type_id: str, object_id: int) -> None:
        self.Document = document
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.ID = object_id
        self.Placement = _Placement()


class _Document:
    Uid = "assembly-view-document"

    def __init__(self) -> None:
        self.objects = {}

    def add(self, name: str, type_id: str) -> _Object:
        obj = _Object(self, name, type_id, len(self.objects) + 1)
        self.objects[name] = obj
        return obj

    def getObject(self, name: str):
        return self.objects.get(name)


def _target(obj: _Object, root: _Object, path: str) -> AssemblyViewTarget:
    return AssemblyViewTarget(
        obj=obj,
        root=root,
        selection_path=path,
        placement=obj.Placement,
        record={
            "object": {
                "document_uid": obj.Document.Uid,
                "object_name": obj.Name,
                "type_id": obj.TypeId,
                "object_id": obj.ID,
            },
            "label": obj.Label,
            "root": {
                "document_uid": root.Document.Uid,
                "object_name": root.Name,
                "type_id": root.TypeId,
                "object_id": root.ID,
            },
            "selection_path": path,
            "placement": {},
            "selection_center_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
        },
    )


def _fixture() -> tuple[_Document, _Object, AssemblyViewState]:
    document = _Document()
    assembly = document.add("Assembly", "Assembly::AssemblyObject")
    assembly.ViewObject = SimpleNamespace(
        EnableMovement=False,
        DraggerVisibility=True,
    )
    first = document.add("First", "App::Link")
    second = document.add("Second", "App::Link")
    inner = document.add("Inner", "Part::Feature")
    individual = (
        _target(first, assembly, "First."),
        _target(second, assembly, "Second."),
        _target(inner, assembly, "Part.Inner."),
    )
    solid = individual[:2]
    state = AssemblyViewState(
        assembly=assembly,
        component_count=2,
        assembly_center=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        assembly_diagonal_mm=100.0,
        individual_targets=individual,
        solid_targets=solid,
        view_group=None,
        views=(),
        view_records=(),
        state_sha256="a" * 64,
    )
    return document, assembly, state


def test_schema_maps_only_the_live_view_action_to_a_closed_bounded_graph() -> None:
    definition = assembly_structure_capability_definition()
    variant = next(
        value for value in definition.variants if value.operation == "create_view"
    )
    schema = definition.provider_schema(("create_view",))["parameters"]["oneOf"][0]

    assert variant.action_ids == frozenset({"Assembly_CreateView"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert definition.primary_classification == "mutation"
    assert variant.transaction_behavior == "document"
    assert set(schema["required"]) == {
        "operation",
        "assembly",
        "label",
        "parts_as_single_solid",
        "moves",
        "expected_view_state_sha256",
        "expected_component_count",
        "expected_target_count",
        "expected_view_count",
    }
    assert schema["additionalProperties"] is False
    moves = schema["properties"]["moves"]
    assert moves["minItems"] == 1
    assert moves["maxItems"] == 256
    branches = moves["items"]["oneOf"]
    assert {branch["properties"]["kind"]["const"] for branch in branches} == {
        "normal",
        "radial",
    }
    assert all(branch["additionalProperties"] is False for branch in branches)
    assert all(
        branch["properties"]["targets"]["maxItems"] == 256 for branch in branches
    )


def test_runtime_parser_preserves_order_and_rejects_ambiguous_moves(
    monkeypatch,
) -> None:
    transforms = []

    def parse_transform(value):
        transforms.append(value)
        return _Placement(4.0)

    monkeypatch.setattr(runtime_module, "_view_placement", parse_transform)
    moves = runtime_module._view_moves(
        "assembly-view-document",
        [
            {
                "kind": "normal",
                "targets": [{"object_name": "First"}],
                "transform": {"exact": "transform"},
            },
            {
                "kind": "radial",
                "targets": [
                    {"object_name": "First"},
                    {"object_name": "Second"},
                ],
                "radial_distance_mm": 12.5,
            },
        ],
    )

    assert [move.kind for move in moves] == ["normal", "radial"]
    assert [target.object_name for target in moves[1].target_refs] == [
        "First",
        "Second",
    ]
    assert moves[0].movement_transform.x == 4.0
    assert moves[1].radial_distance_mm == 12.5
    assert transforms == [{"exact": "transform"}]

    with pytest.raises(NativeAssemblyViewError, match="duplicate"):
        runtime_module._view_moves(
            "assembly-view-document",
            [
                {
                    "kind": "radial",
                    "targets": [
                        {"object_name": "First"},
                        {"object_name": "First"},
                    ],
                    "radial_distance_mm": 1.0,
                }
            ],
        )
    with pytest.raises(NativeAssemblyViewError, match="exactly one"):
        runtime_module._view_moves(
            "assembly-view-document",
            [
                {
                    "kind": "normal",
                    "targets": [{"object_name": "First"}],
                    "transform": {},
                    "radial_distance_mm": 2.0,
                }
            ],
        )
    with pytest.raises(NativeAssemblyViewError, match="greater than zero"):
        runtime_module._view_moves(
            "assembly-view-document",
            [
                {
                    "kind": "radial",
                    "targets": [{"object_name": "First"}],
                    "radial_distance_mm": 0.0,
                }
            ],
        )


def test_preflight_freezes_scope_paths_state_and_human_presentation(
    monkeypatch,
) -> None:
    document, assembly, state = _fixture()
    monkeypatch.setattr(view_module, "_timeline_active", lambda _obj: True)
    monkeypatch.setattr(
        view_module,
        "capture_assembly_view_state",
        lambda _assembly: state,
    )
    monkeypatch.setattr(
        view_module,
        "resolve_object",
        lambda doc, reference, expected_types=(): doc.getObject(reference.object_name),
    )
    selection = {"items": [{"object_name": "Second"}]}
    spec = AssemblyViewCreateSpec(
        assembly_ref=NativeObjectRef(document.Uid, assembly.Name),
        label="Service sequence",
        parts_as_single_solid=False,
        moves=(
            AssemblyViewMoveSpec(
                kind="normal",
                target_refs=(NativeObjectRef(document.Uid, "Inner"),),
                movement_transform=_Placement(10.0),
            ),
            AssemblyViewMoveSpec(
                kind="radial",
                target_refs=(NativeObjectRef(document.Uid, "First"),),
                radial_distance_mm=15.0,
            ),
        ),
        expected_view_state_sha256=state.state_sha256,
        expected_component_count=2,
        expected_target_count=3,
        expected_view_count=0,
    )

    prepared = preflight_create_assembly_view(
        document,
        spec,
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selection,
    )

    assert prepared.state is state
    assert [move.targets[0].obj.Name for move in prepared.moves] == [
        "Inner",
        "First",
    ]
    assert prepared.moves[0].targets[0].selection_path == "Part.Inner."
    assert prepared.moves[0].root is assembly
    assert prepared.selection_before == selection
    assert prepared.presentation_before == (False, True)

    stale = AssemblyViewCreateSpec(
        assembly_ref=spec.assembly_ref,
        label=spec.label,
        parts_as_single_solid=spec.parts_as_single_solid,
        moves=spec.moves,
        expected_view_state_sha256="b" * 64,
        expected_component_count=spec.expected_component_count,
        expected_target_count=spec.expected_target_count,
        expected_view_count=spec.expected_view_count,
    )
    with pytest.raises(NativeAssemblyViewError, match="state changed"):
        preflight_create_assembly_view(
            document,
            stale,
            active_reader=lambda _document: assembly,
            selection_reader=lambda _document: selection,
        )


def test_preflight_rejects_wrong_scope_identity_transform_and_component_count(
    monkeypatch,
) -> None:
    document, assembly, state = _fixture()
    monkeypatch.setattr(view_module, "_timeline_active", lambda _obj: True)
    monkeypatch.setattr(view_module, "capture_assembly_view_state", lambda _asm: state)
    monkeypatch.setattr(
        view_module,
        "resolve_object",
        lambda doc, reference, expected_types=(): doc.getObject(reference.object_name),
    )

    def spec(target: str, transform: _Placement) -> AssemblyViewCreateSpec:
        return AssemblyViewCreateSpec(
            assembly_ref=NativeObjectRef(document.Uid, assembly.Name),
            label="View",
            parts_as_single_solid=True,
            moves=(
                AssemblyViewMoveSpec(
                    kind="normal",
                    target_refs=(NativeObjectRef(document.Uid, target),),
                    movement_transform=transform,
                ),
            ),
            expected_view_state_sha256=state.state_sha256,
            expected_component_count=2,
            expected_target_count=2,
            expected_view_count=0,
        )

    with pytest.raises(NativeAssemblyViewError, match="not available"):
        preflight_create_assembly_view(
            document,
            spec("Inner", _Placement(2.0)),
            active_reader=lambda _document: assembly,
        )
    with pytest.raises(NativeAssemblyViewError, match="translate or rotate"):
        preflight_create_assembly_view(
            document,
            spec("First", _Placement(identity=True)),
            active_reader=lambda _document: assembly,
        )

    state = replace(state, component_count=1)
    monkeypatch.setattr(view_module, "capture_assembly_view_state", lambda _asm: state)
    with pytest.raises(NativeAssemblyViewError, match="at least two"):
        preflight_create_assembly_view(
            document,
            spec("First", _Placement(2.0)),
            active_reader=lambda _document: assembly,
        )


def test_state_summary_is_bounded_and_does_not_echo_selection_paths() -> None:
    _document, _assembly, state = _fixture()
    summary = state.summary()

    assert summary["available"] is True
    assert summary["state_sha256"] == "a" * 64
    assert summary["component_count"] == 2
    assert summary["individual_target_count"] == 3
    assert summary["solid_target_count"] == 2
    assert [item["object_name"] for item in summary["movable_targets"]] == [
        "First",
        "Second",
        "Inner",
    ]
    assert summary["movable_targets"][0]["target_modes"] == [
        "individual_objects",
        "parts_as_single_solid",
    ]
    assert summary["movable_targets"][2]["target_modes"] == ["individual_objects"]
    assert all("selection_path" not in item for item in summary["movable_targets"])
