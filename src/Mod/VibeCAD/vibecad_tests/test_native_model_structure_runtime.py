# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeModelStructureRuntime as runtime_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelStructureRuntime import NativeModelStructureRuntime
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Object:
    def __init__(self, document, name: str, type_id: str):
        self.Document = document
        self.Name = name
        self.TypeId = type_id
        self.Shape = SimpleNamespace(isNull=lambda: False, isValid=lambda: True)

    def isDerivedFrom(self, expected: str) -> bool:
        return self.TypeId == expected


class _Document:
    Uid = "document-a"
    Name = "DocumentA"

    def __init__(self):
        self.objects = {
            "Component": _Object(self, "Component", "PartDesign::Component"),
            "SourceBody": _Object(self, "SourceBody", "PartDesign::Body"),
        }

    def getObject(self, name: str):
        return self.objects.get(name)


def _runtime():
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("model-structure-unit")
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
    return NativeModelStructureRuntime(context), state, document


def test_structure_runtime_routes_exact_explicit_parent_without_selection(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    captured = {}

    def run_immediate(context, **kwargs):
        captured.update(kwargs)
        return {"routed": True}

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    monkeypatch.setattr(
        runtime_module,
        "create_body",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    result = runtime.mutate_structure(
        {
            "operation": "new_body",
            "label": "Bracket",
            "component": {"object_name": "Component"},
        },
        ticket=state.begin_call(document.Uid, "model.structure"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Body"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.component_ref.object_name == "Component"


def test_clone_preflight_rejects_empty_or_wrong_exact_source(monkeypatch) -> None:
    runtime, state, document = _runtime()
    document.objects["SourceBody"].Shape = SimpleNamespace(
        isNull=lambda: True,
        isValid=lambda: True,
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match="no valid current History shape"):
        runtime.mutate_structure(
            {
                "operation": "clone",
                "source_body": {"object_name": "SourceBody"},
                "label": "Clone",
                "output_body_label": "Body Copy",
            },
            ticket=state.begin_call(document.Uid, "model.structure"),
        )


def test_binder_rejects_duplicate_exact_sources_before_transaction(monkeypatch) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )
    duplicate = {"object_name": "SourceBody", "subelements": ["Face1"]}

    with pytest.raises(NativeModelError, match="repeats"):
        runtime.mutate_structure(
            {
                "operation": "sub_shape_binder",
                "label": "Reference",
                "references": [duplicate, dict(duplicate)],
            },
            ticket=state.begin_call(document.Uid, "model.structure"),
        )


def test_separate_preflights_exact_targets_before_routing_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    source = _Object(document, "MultiSolid", "Part::Feature")
    destination = document.objects["Component"]
    document.objects[source.Name] = source
    observed = {}
    prepared = object()

    monkeypatch.setattr(
        runtime_module,
        "prepare_design_separate",
        lambda document_uid, value: observed.update(
            document_uid=document_uid,
            value=value,
        )
        or "spec",
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_design_separate",
        lambda target_document, spec: observed.update(
            document=target_document,
            spec=spec,
        )
        or prepared,
    )

    def run_immediate(context, **kwargs):
        observed.update(context=context, **kwargs)
        return {"routed": True}

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    monkeypatch.setattr(
        runtime_module,
        "create_design_separate",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    result = runtime.mutate_structure(
        {
            "operation": "separate",
            "label": "Separate Housing",
            "source": {"object_name": source.Name},
            "destination_component": {"object_name": destination.Name},
        },
        ticket=state.begin_call(document.Uid, "model.structure"),
    )

    assert result == {"routed": True}
    assert observed["document_uid"] == document.Uid
    assert observed["value"] == {
        "source": {"object_name": source.Name},
        "destination_component": {"object_name": destination.Name},
    }
    assert observed["document"] is document
    assert observed["spec"] == "spec"
    assert observed["transaction_name"] == "Create Native Design Separate"
    draft = observed["mutate"](document)
    assert draft.document is document
    assert draft.label == "Separate Housing"
    assert draft.prepared is prepared


def test_separate_defaults_omitted_destination_to_design_root(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    observed = {}
    monkeypatch.setattr(
        runtime_module,
        "preflight_design_separate",
        lambda _document, spec: observed.setdefault("spec", spec) or object(),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: {"routed": True},
    )

    result = runtime.mutate_structure(
        {
            "operation": "separate",
            "label": "Root Separate",
            "source": {"object_name": "SourceBody"},
        },
        ticket=state.begin_call(document.Uid, "model.structure"),
    )

    assert result == {"routed": True}
    assert observed["spec"].destination_component_ref is None


def test_sketch_readiness_is_read_only_and_exact(monkeypatch) -> None:
    runtime, _state, document = _runtime()
    sketch = _Object(document, "Sketch", "Sketcher::SketchObject")
    document.objects[sketch.Name] = sketch
    observed = []
    monkeypatch.setattr(
        runtime_module,
        "sketch_readiness",
        lambda target_document, target: observed.append(
            (target_document, target.object_name)
        )
        or {"valid": True},
    )

    assert runtime.validate_sketch(
        {
            "operation": "validate_sketch",
            "target": {"object_name": "Sketch"},
        }
    ) == {"valid": True}
    assert observed == [(document, "Sketch")]


def test_revolution_sketch_returns_axial_and_radial_profile_coordinates(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    captured = {}

    def run_immediate(_context, **kwargs):
        captured.update(kwargs)
        return {
            "sketch": {"object_name": "TurnedProfile"},
            "support": {
                "kind": "base_plane",
                "plane": "XZ",
                "offset_mm": 0.0,
                "reverse_normal": True,
            },
        }

    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        run_immediate,
    )
    monkeypatch.setattr(
        runtime_module,
        "create_reusable_sketch",
        lambda _document, **kwargs: captured.update(create_kwargs=kwargs),
    )

    result = runtime.create_sketch(
        {
            "operation": "create_revolution",
            "label": "Turned Profile",
            "axis": "Z",
        },
        ticket=state.begin_call(document.Uid, "model.revolution_sketch"),
    )

    assert result["profile_coordinates"] == {
        "axial": "y_mm",
        "radius": "x_mm >= 0",
        "axis": "x_mm = 0",
    }
    captured["mutate"](document)
    assert captured["create_kwargs"]["profile_intent"] == {
        "kind": "axisymmetric",
        "global_axis": "Z",
        "sketch_axis": "V_Axis",
        "axial": "y_mm",
        "radius": "x_mm >= 0",
        "axis": "x_mm = 0",
    }
