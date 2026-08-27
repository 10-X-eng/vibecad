# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeModelDressupRuntime as runtime_module
from VibeCADNativeDesignChamfer import (
    preflight_design_chamfer,
    prepare_design_chamfer,
)
from VibeCADNativeDesignDraft import preflight_design_draft, prepare_design_draft
from VibeCADNativeDesignFillet import preflight_design_fillet, prepare_design_fillet
from VibeCADNativeDesignThickness import (
    preflight_design_thickness,
    prepare_design_thickness,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelDressupRuntime import NativeModelDressupRuntime
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Shape:
    Solids = (object(),)

    def isNull(self) -> bool:
        return False

    def isValid(self) -> bool:
        return True

    @staticmethod
    def getElement(name: str):
        if name.startswith("Edge"):
            return SimpleNamespace(
                ShapeType="Edge",
                Curve=SimpleNamespace(TypeId="Part::GeomLine"),
            )
        return SimpleNamespace(
            ShapeType="Face",
            Surface=SimpleNamespace(TypeId="Part::GeomPlane"),
        )


class _Body:
    TypeId = "PartDesign::Body"

    def __init__(self, document, name: str):
        self.Document = document
        self.Name = name
        self.Shape = _Shape()

    def isDerivedFrom(self, expected: str) -> bool:
        return expected == self.TypeId


class _Feature(_Body):
    TypeId = "PartDesign::DesignBox"

    def isDerivedFrom(self, expected: str) -> bool:
        return expected in {self.TypeId, "Part::Feature"}


class _Document:
    Uid = "document-dressup"
    Name = "DocumentDressup"

    def __init__(self):
        self.objects = {
            "TargetBody": _Body(self, "TargetBody"),
            "ReferenceState": _Feature(self, "ReferenceState"),
        }

    def getObject(self, name: str):
        return self.objects.get(name)


def _runtime():
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("model-dressup-unit")
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
    return NativeModelDressupRuntime(context), state, document


def _arguments():
    return {
        "operation": "fillet",
        "label": "Exact Fillet",
        "selection": {
            "kind": "explicit",
            "targets": [
                {"object_name": "TargetBody", "subelements": ["Edge1"]}
            ],
        },
        "radius_mm": 1.25,
    }


def _preview_arguments(arguments):
    return {**arguments, "stage": "propose", "preview_id": ""}


def _chamfer_arguments():
    arguments = _arguments()
    arguments["operation"] = "chamfer"
    arguments["label"] = "Exact Chamfer"
    arguments.pop("radius_mm")
    arguments["definition"] = {
        "kind": "two_distances",
        "size_mm": 1.0,
        "second_size_mm": 1.5,
        "flip_direction": True,
    }
    return arguments


def _draft_arguments():
    return {
        "operation": "draft",
        "label": "Exact Draft",
        "selection": {
            "kind": "explicit",
            "targets": [
                {"object_name": "TargetBody", "subelements": ["Face1"]}
            ],
        },
        "angle_degrees": 5.0,
        "neutral_plane": {
            "kind": "subelement",
            "object_name": "ReferenceState",
            "subelement": "Face5",
        },
        "pull_direction": {
            "kind": "subelement",
            "object_name": "ReferenceState",
            "subelement": "Edge1",
        },
        "reversed": True,
    }


def _thickness_arguments():
    return {
        "operation": "thickness",
        "label": "Exact Thickness",
        "selection": {
            "kind": "explicit",
            "targets": [
                {"object_name": "TargetBody", "subelements": ["Face6"]}
            ],
        },
        "thickness_mm": 1.0,
        "direction": "inward",
        "mode": "skin",
        "join": "arc",
        "intersection_handling": False,
    }


def test_dressup_runtime_preflights_then_routes_one_immediate_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    captured = {}
    observed = []

    monkeypatch.setattr(
        runtime_module,
        "preflight_design_fillet",
        lambda target_document, prepared: observed.append(
            (target_document, prepared)
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_design_fillet",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    preview_arguments = _preview_arguments(_arguments())
    preview = runtime.mutate_dressup(
        preview_arguments,
        ticket=state.begin_call(document.Uid, "model.dressup"),
    )
    assert preview["applied"] is False
    assert observed == []
    assert captured == {}
    result = runtime.mutate_dressup(
        {
            **preview_arguments,
            "stage": "apply",
            "preview_id": preview["preview_id"],
        },
        ticket=state.begin_call(document.Uid, "model.dressup"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Design Fillet"
    assert observed[0][0] is document
    assert observed[0][1].targets[0].body.object_name == "TargetBody"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Fillet"
    assert draft.spec.radius_mm == 1.25


def test_fillet_preflight_resolves_exact_body_and_subelement_type() -> None:
    _runtime_value, _state, document = _runtime()
    prepared = prepare_design_fillet(document.Uid, _arguments())

    assert preflight_design_fillet(document, prepared) == (
        document.getObject("TargetBody"),
    )

    document.getObject("TargetBody").Shape.getElement = lambda _name: SimpleNamespace(
        ShapeType="Vertex"
    )
    with pytest.raises(NativeModelError, match="changed geometric type"):
        preflight_design_fillet(document, prepared)


def test_chamfer_runtime_preflights_then_routes_one_immediate_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    captured = {}
    observed = []

    monkeypatch.setattr(
        runtime_module,
        "preflight_design_chamfer",
        lambda target_document, prepared: observed.append(
            (target_document, prepared)
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_design_chamfer",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    preview_arguments = _preview_arguments(_chamfer_arguments())
    preview = runtime.mutate_dressup(
        preview_arguments,
        ticket=state.begin_call(document.Uid, "model.dressup"),
    )
    assert preview["applied"] is False
    assert observed == []
    assert captured == {}
    result = runtime.mutate_dressup(
        {
            **preview_arguments,
            "stage": "apply",
            "preview_id": preview["preview_id"],
        },
        ticket=state.begin_call(document.Uid, "model.dressup"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Design Chamfer"
    assert observed[0][0] is document
    assert observed[0][1].definition.kind == "two_distances"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Chamfer"
    assert draft.spec.definition.second_size_mm == 1.5


def test_chamfer_preflight_resolves_exact_body_and_subelement_type() -> None:
    _runtime_value, _state, document = _runtime()
    prepared = prepare_design_chamfer(document.Uid, _chamfer_arguments())

    assert preflight_design_chamfer(document, prepared) == (
        document.getObject("TargetBody"),
    )


def test_draft_runtime_preflights_then_routes_one_immediate_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    captured = {}
    observed = []

    monkeypatch.setattr(
        runtime_module,
        "preflight_design_draft",
        lambda target_document, prepared: observed.append(
            (target_document, prepared)
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_design_draft",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    preview = runtime.mutate_dressup(
        _draft_arguments(),
        ticket=state.begin_call(document.Uid, "model.dressup"),
    )
    assert preview["applied"] is False
    apply_arguments = dict(_draft_arguments())
    apply_arguments["stage"] = "apply"
    apply_arguments["preview_id"] = preview["preview_id"]
    result = runtime.mutate_dressup(
        apply_arguments,
        ticket=state.begin_call(document.Uid, "model.dressup"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Design Draft"
    assert observed[0][0] is document
    assert observed[0][1].targets[0].subelements == ("Face1",)
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Draft"
    assert draft.spec.pull_direction.subelement == "Edge1"


def test_draft_preflight_resolves_body_and_reference_geometry() -> None:
    _runtime_value, _state, document = _runtime()
    prepared = prepare_design_draft(document.Uid, _draft_arguments())

    assert preflight_design_draft(document, prepared) == (
        document.getObject("TargetBody"),
    )

    reference = document.getObject("ReferenceState")
    reference.Shape.getElement = lambda _name: SimpleNamespace(
        ShapeType="Face",
        Surface=SimpleNamespace(TypeId="Part::GeomCylinder"),
    )
    with pytest.raises(NativeModelError, match="face must be planar"):
        preflight_design_draft(document, prepared)


def test_thickness_runtime_preflights_then_routes_one_immediate_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    captured = {}
    observed = []

    monkeypatch.setattr(
        runtime_module,
        "preflight_design_thickness",
        lambda target_document, prepared: observed.append(
            (target_document, prepared)
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_design_thickness",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    preview = runtime.mutate_dressup(
        _thickness_arguments(),
        ticket=state.begin_call(document.Uid, "model.dressup"),
    )
    assert preview["applied"] is False
    apply_arguments = dict(_thickness_arguments())
    apply_arguments["stage"] = "apply"
    apply_arguments["preview_id"] = preview["preview_id"]
    result = runtime.mutate_dressup(
        apply_arguments,
        ticket=state.begin_call(document.Uid, "model.dressup"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Design Thickness"
    assert observed[0][0] is document
    assert observed[0][1].targets[0].subelements == ("Face6",)
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Thickness"
    assert draft.spec.thickness_mm == 1.0


def test_thickness_preflight_resolves_exact_body_and_face() -> None:
    _runtime_value, _state, document = _runtime()
    prepared = prepare_design_thickness(document.Uid, _thickness_arguments())

    assert preflight_design_thickness(document, prepared) == (
        document.getObject("TargetBody"),
    )


def test_duplicate_body_preflight_never_starts_transaction(monkeypatch) -> None:
    runtime, state, document = _runtime()
    arguments = _arguments()
    arguments["selection"]["targets"].append(
        {"object_name": "TargetBody", "subelements": ["Edge2"]}
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    preview_arguments = _preview_arguments(arguments)
    preview = runtime.mutate_dressup(
        preview_arguments,
        ticket=state.begin_call(document.Uid, "model.dressup"),
    )
    assert preview["applied"] is False
    with pytest.raises(NativeModelError, match="repeat the same target Body"):
        runtime.mutate_dressup(
            {
                **preview_arguments,
                "stage": "apply",
                "preview_id": preview["preview_id"],
            },
            ticket=state.begin_call(document.Uid, "model.dressup"),
        )
