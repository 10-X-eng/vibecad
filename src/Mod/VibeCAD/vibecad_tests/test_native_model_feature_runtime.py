# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeModelFeatureRuntime as runtime_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeDesignProfiles import (
    PreparedDesignProfile,
    preflight_design_profile,
)
from VibeCADNativeDesignResults import DesignResultSpec
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelFeatureBindings import _aligned_sketch_axis
from VibeCADNativeModelFeatureRuntime import NativeModelFeatureRuntime
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeTargets import NativeObjectRef
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Object:
    def __init__(self, document, name: str, type_id: str):
        self.Document = document
        self.Name = name
        self.TypeId = type_id

    def isDerivedFrom(self, expected: str) -> bool:
        return self.TypeId == expected


class _Document:
    Uid = "document-feature"
    Name = "DocumentFeature"

    def __init__(self):
        self.objects = {
            "Component": _Object(self, "Component", "PartDesign::Component"),
            "TargetBody": _Object(self, "TargetBody", "PartDesign::Body"),
        }

    def getObject(self, name: str):
        return self.objects.get(name)


def _runtime():
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("model-feature-unit")
    context = NativeRuntimeContext(
        service=SimpleNamespace(),
        document=document,
        state=state,
        undo_ledger=ledger,
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: "model",
        edit_or_task_active=lambda: False,
    )
    return NativeModelFeatureRuntime(context), state, document


def _placement():
    return {
        "origin_mm": {"x": 1.0, "y": 2.0, "z": 3.0},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": 15.0,
        },
    }


def _box_result(mode="new_body", targets=None, component=None):
    return {
        "operation": "primitive",
        "label": "Exact Box",
        "placement": _placement(),
        "result": {
            "mode": mode,
            "targets": list(targets or []),
            "destination_component": component,
        },
        "definition": {
            "kind": "box",
            "length_mm": 12.0,
            "width_mm": 8.0,
            "height_mm": 5.0,
        },
    }


@pytest.mark.parametrize(
    ("vertical", "axis", "expected"),
    (
        ((0.0, 0.0, 1.0), "Z", ("V_Axis", False)),
        ((0.0, 0.0, -1.0), "Z", ("V_Axis", True)),
        ((0.0, 1.0, 0.0), "X", ("H_Axis", False)),
    ),
)
def test_global_axis_resolution_preserves_requested_direction(vertical, axis, expected) -> None:
    assert _aligned_sketch_axis(
        horizontal=(1.0, 0.0, 0.0),
        vertical=vertical,
        requested=axis,
    ) == expected


def test_global_axis_resolution_rejects_an_axis_normal_to_the_sketch() -> None:
    with pytest.raises(NativeModelError, match="does not lie in Sketch"):
        _aligned_sketch_axis(
            horizontal=(1.0, 0.0, 0.0),
            vertical=(0.0, 1.0, 0.0),
            requested="Z",
        )


def test_feature_runtime_preflights_and_routes_native_parameters(monkeypatch) -> None:
    runtime, state, document = _runtime()
    captured = {}
    placement = object()

    monkeypatch.setattr(runtime_module, "placement_from_mapping", lambda value: placement)
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )
    monkeypatch.setattr(
        runtime_module,
        "create_design_primitive",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )

    result = runtime.mutate_feature(
        _box_result(
            mode="join",
            targets=[{"object_name": "TargetBody"}],
        ),
        ticket=state.begin_call(document.Uid, "model.feature"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Design Primitive"
    draft = captured["mutate"](document)
    assert draft.operation == "design_box"
    assert draft.native_parameters == {"Length": 12.0, "Width": 8.0, "Height": 5.0}
    assert draft.placement is placement
    assert draft.result_spec.mode == "join"
    assert draft.result_spec.target_refs[0].object_name == "TargetBody"


@pytest.mark.parametrize(
    ("arguments", "message"),
    (
        (
            _box_result(
                mode="new_body",
                targets=[{"object_name": "TargetBody"}],
            ),
            "cannot also target",
        ),
        (
            _box_result(
                mode="join",
                component={"object_name": "Component"},
            ),
            "require exact Bodies",
        ),
    ),
)
def test_invalid_result_semantics_do_not_start_mutation(
    monkeypatch,
    arguments,
    message,
) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(runtime_module, "placement_from_mapping", lambda value: object())
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_feature(
            arguments,
            ticket=state.begin_call(document.Uid, "model.feature"),
        )


def test_cross_parameter_validation_happens_before_transaction(monkeypatch) -> None:
    runtime, state, document = _runtime()
    arguments = {
        "operation": "primitive",
        "label": "Invalid Tube",
        "placement": _placement(),
        "result": {
            "mode": "new_body",
            "targets": [],
            "destination_component": None,
        },
        "definition": {
            "kind": "tube",
            "outer_radius_mm": 4.0,
            "inner_radius_mm": 4.0,
            "height_mm": 10.0,
        },
    }
    monkeypatch.setattr(runtime_module, "placement_from_mapping", lambda value: object())
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match="inner radius"):
        runtime.mutate_feature(
            arguments,
            ticket=state.begin_call(document.Uid, "model.feature"),
        )


@pytest.mark.parametrize(
    "definition",
    (
        {
            "kind": "box",
            "length_mm": 12.0,
            "height_mm": 5.0,
        },
        {
            "kind": "box",
            "length_mm": 12.0,
            "width_mm": 8.0,
            "height_mm": 5.0,
            "radius_mm": 3.0,
        },
    ),
)
def test_compact_primitive_schema_still_has_exact_runtime_fields(definition) -> None:
    runtime, state, document = _runtime()
    arguments = _box_result()
    arguments["definition"] = definition

    with pytest.raises(NativeArgumentError, match="do not match"):
        runtime.mutate_feature(
            arguments,
            ticket=state.begin_call(document.Uid, "model.feature"),
        )


def _profile_result(kind, definition):
    return {
        "operation": "profile",
        "label": f"Invalid {kind.title()}",
        "profile": {"object_name": "Profile"},
        "result": {
            "mode": "new_body",
            "targets": [],
            "destination_component": None,
        },
        "definition": {"kind": kind, **definition},
    }


@pytest.mark.parametrize(
    "arguments",
    (
        _profile_result(
            "extrude",
            {
                "direction": {
                    "kind": "sketch_normal",
                    "vector": {"x": 0.0, "y": 0.0, "z": 1.0},
                },
                "extent": {
                    "kind": "one_side",
                    "sides": [
                        {
                            "kind": "length",
                            "length_mm": 10.0,
                            "taper_degrees": 0.0,
                        }
                    ],
                    "reversed": False,
                },
            },
        ),
        _profile_result(
            "revolve",
            {
                "axis": {"object_name": "Profile", "subelements": ["V_Axis"]},
                "extent": {"kind": "up_to_last", "reversed": False},
            },
        ),
        _profile_result(
            "sweep",
            {
                "path": {"object_name": "Path", "subelements": ["Edge1"]},
                "options": {
                    "spine_tangent": False,
                    "orientation": {
                        "kind": "standard",
                        "vector": {"x": 0.0, "y": 0.0, "z": 1.0},
                    },
                    "transition": "transformed",
                    "transformation": "constant",
                    "sections": [],
                },
            },
        ),
        _profile_result(
            "helix",
            {
                "axis": {"object_name": "Profile", "subelements": ["V_Axis"]},
                "parameters": {
                    "kind": "pitch_height_angle",
                    "pitch_mm": 2.0,
                    "height_mm": 10.0,
                    "turns": 5.0,
                    "angle_degrees": 0.0,
                },
                "left_handed": False,
                "reversed": False,
                "outside": False,
                "tolerance": 1.0,
            },
        ),
    ),
)
def test_compact_profile_schema_still_has_exact_nested_runtime_fields(arguments) -> None:
    runtime, state, document = _runtime()

    with pytest.raises(NativeModelError, match="fields do not match|inconsistent inputs"):
        runtime.mutate_feature(
            arguments,
            ticket=state.begin_call(document.Uid, "model.feature"),
        )


@pytest.mark.parametrize(
    ("kind", "side_count"),
    (("one_side", 2), ("symmetric", 2), ("two_sides", 1)),
)
def test_compact_extrude_extent_rejects_the_wrong_side_count_before_preflight(
    monkeypatch,
    kind,
    side_count,
) -> None:
    runtime, state, document = _runtime()
    side = {"kind": "length", "length_mm": 10.0, "taper_degrees": 0.0}
    arguments = _profile_result(
        "extrude",
        {
            "direction": {"kind": "sketch_normal"},
            "extent": {
                "kind": kind,
                "sides": [dict(side) for _index in range(side_count)],
                "reversed": False,
            },
        },
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_design_profile",
        lambda *_args: pytest.fail("document preflight started"),
    )

    with pytest.raises(NativeModelError, match="requires [12] side definition"):
        runtime.mutate_feature(
            arguments,
            ticket=state.begin_call(document.Uid, "model.feature"),
        )


def test_typed_definition_cannot_cross_primitive_and_profile_families() -> None:
    runtime, state, document = _runtime()
    arguments = {
        "operation": "profile",
        "label": "Wrong Family",
        "profile": {"object_name": "Profile", "regions": []},
        "result": {
            "mode": "new_body",
            "targets": [],
            "destination_component": None,
        },
        "definition": {
            "kind": "box",
            "length_mm": 12.0,
            "width_mm": 8.0,
            "height_mm": 5.0,
        },
    }

    with pytest.raises(NativeArgumentError, match="operation is unavailable"):
        runtime.mutate_feature(
            arguments,
            ticket=state.begin_call(document.Uid, "model.feature"),
        )


def test_profile_feature_routes_prepared_exact_inputs_without_primitive_placement(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    prepared = SimpleNamespace(transaction_name="Create Native Design Loft")
    observed = {}
    arguments = {
        "operation": "profile",
        "label": "Exact Loft",
        "profile": {"object_name": "Profile", "regions": []},
        "result": {
            "mode": "new_body",
            "targets": [],
            "destination_component": None,
        },
        "definition": {
            "kind": "loft",
            "sections": [{"object_name": "Section", "regions": []}],
            "ruled": False,
            "closed": False,
        },
    }

    monkeypatch.setattr(
        runtime_module,
        "prepare_design_profile",
        lambda uid, operation, values: observed.update(
            uid=uid,
            operation=operation,
            values=values,
        )
        or prepared,
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_design_profile",
        lambda target_document, value, result_spec: observed.update(
            preflight=(target_document, value, result_spec)
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_prepared_design_profile",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "placement_from_mapping",
        lambda _value: pytest.fail("primitive placement parser called"),
    )
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    assert runtime.mutate_feature(
        arguments,
        ticket=state.begin_call(document.Uid, "model.feature"),
    ) == {"routed": True}
    assert observed["uid"] == document.Uid
    assert observed["operation"] == "design_loft"
    assert observed["preflight"][:2] == (document, prepared)
    assert observed["preflight"][2].mode == "new_body"
    assert captured["transaction_name"] == "Create Native Design Loft"
    draft = captured["mutate"](document)
    assert draft.prepared is prepared
    assert draft.label == "Exact Loft"


@pytest.mark.parametrize(
    ("operation", "spec"),
    (
        (
            "design_extrude",
            SimpleNamespace(
                side1=SimpleNamespace(kind="up_to_first"),
                side2=None,
            ),
        ),
        (
            "design_revolve",
            SimpleNamespace(extent_kind="up_to_last"),
        ),
        (
            "design_revolve",
            SimpleNamespace(extent_kind="up_to_face"),
        ),
    ),
)
def test_target_dependent_profile_extent_is_rejected_before_document_preflight(
    operation,
    spec,
) -> None:
    prepared = PreparedDesignProfile(operation, spec)
    result = DesignResultSpec("new_body", (), None)

    with pytest.raises(NativeModelError, match="exactly one explicit target Body"):
        preflight_design_profile(None, prepared, result)

    two_targets = DesignResultSpec(
        "join",
        (
            NativeObjectRef("document-feature", "FirstBody"),
            NativeObjectRef("document-feature", "SecondBody"),
        ),
        None,
    )
    with pytest.raises(NativeModelError, match="exactly one explicit target Body"):
        preflight_design_profile(None, prepared, two_targets)
