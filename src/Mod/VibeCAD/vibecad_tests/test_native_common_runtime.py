# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeCommonRuntime as runtime_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeCommonRuntime import NativeCommonRuntime
from VibeCADNativeDispatch import NativeCapabilityCall
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext, NativeRuntimeContextError
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Document:
    Uid = "document-a"
    Name = "DocumentA"


def _runtime(**overrides):
    document = overrides.get("document", _Document())
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("run-a")
    active = overrides.get("active_document", lambda: document)
    context = NativeRuntimeContext(
        service=overrides.get("service", SimpleNamespace()),
        document=document,
        state=state,
        undo_ledger=ledger,
        reauthorize_turn=overrides.get("reauthorize_turn", lambda: None),
        active_document=active,
        active_surface_id=overrides.get("active_surface_id", lambda: "model"),
        edit_or_task_active=overrides.get("edit_or_task_active", lambda: False),
    )
    runtime = NativeCommonRuntime(context=context)
    return runtime, state, document


def test_state_reads_are_live_and_reauthorized(monkeypatch) -> None:
    calls = []
    runtime, _state, document = _runtime(
        reauthorize_turn=lambda: calls.append("authorize")
    )
    monkeypatch.setattr(
        runtime_module,
        "build_active_snapshot",
        lambda target, surface, state: {
            "document": target.Name,
            "surface": surface,
            "revision": state["structural_revision"],
        },
    )
    monkeypatch.setattr(
        runtime_module,
        "read_current_selection",
        lambda target: {"document": target.Name, "items": []},
    )

    assert runtime.read_state({"operation": "active"}) == {
        "document": document.Name,
        "surface": "model",
        "revision": 0,
    }
    assert runtime.read_state({"operation": "selection"}) == {
        "document": document.Name,
        "items": [],
    }
    assert calls == ["authorize", "authorize"]


def test_view_runtime_uses_fixed_operations_and_exact_injected_document(
    monkeypatch,
) -> None:
    runtime, _state, document = _runtime(service=SimpleNamespace(name="service"))
    monkeypatch.setattr(
        runtime_module,
        "fit_all",
        lambda target: {"document": target.Name, "fit": True},
    )
    captured = []

    def capture(service, target, *, frame, targets):
        captured.append((service.name, target.Name, frame, targets))
        return {"captured": True}

    monkeypatch.setattr(runtime_module, "capture_screenshot", capture)

    assert runtime.control_view({"operation": "fit_all"})["fit"] is True
    assert runtime.control_view(
        {
            "operation": "capture_objects",
            "targets": [{"object_name": "Box"}],
        }
    ) == {"captured": True}
    target = captured[0][3][0]
    assert (target.document_uid, target.object_name) == (document.Uid, "Box")


def test_inspect_runtime_maps_object_and_subelement_targets_without_labels(
    monkeypatch,
) -> None:
    runtime, _state, document = _runtime()
    observed = []
    monkeypatch.setattr(
        runtime_module,
        "inspect_element",
        lambda target_document, target: observed.append((target_document, target))
        or {"shape_type": "Edge"},
    )
    monkeypatch.setattr(
        runtime_module,
        "geometry_validity",
        lambda target_document, target: observed.append((target_document, target))
        or {"valid": True},
    )

    assert runtime.inspect(
        {
            "operation": "element",
            "targets": [{"object_name": "Box", "subelement": "Edge1"}],
        }
    )["shape_type"] == "Edge"
    assert runtime.inspect(
        {"operation": "validity", "targets": [{"object_name": "Box"}]}
    )["valid"] is True
    assert all(value[0] is document for value in observed)
    assert observed[0][1].subelement == "Edge1"
    assert observed[1][1].object_name == "Box"


def test_mass_properties_uses_one_canonical_targets_list(
    monkeypatch,
) -> None:
    runtime, _state, document = _runtime()
    observed = []
    monkeypatch.setattr(
        runtime_module,
        "mass_properties",
        lambda target_document, targets: observed.append((target_document, targets))
        or {"volume_mm3": 1.0},
    )

    assert runtime.inspect(
        {
            "operation": "mass_properties",
            "targets": [
                {"object_name": "Body"},
                {"object_name": "ToolBody"},
            ],
        }
    ) == {"volume_mm3": 1.0}
    assert [target.object_name for target in observed[0][1]] == [
        "Body",
        "ToolBody",
    ]
    assert all(item[0] is document for item in observed)

    with pytest.raises(NativeArgumentError, match="do not match"):
        runtime.inspect(
            {
                "operation": "mass_properties",
                "target": {"object_name": "Body"},
            }
        )


def test_save_runtime_uses_guarded_existing_path_only(monkeypatch) -> None:
    runtime, _state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "guarded_save",
        lambda target, **guards: {
            "exact": target is document,
            "active": guards["active_document"]() is document,
        },
    )

    assert runtime.save_document({"operation": "existing_path"}) == {
        "exact": True,
        "active": True,
    }
    with pytest.raises(NativeArgumentError):
        runtime.save_document({"operation": "save_as", "path": "/tmp/a.FCStd"})


def test_runtime_refuses_extra_fields_invalid_targets_and_inactive_document() -> None:
    runtime, _state, _document = _runtime()
    with pytest.raises(NativeArgumentError, match="do not match"):
        runtime.control_view({"operation": "fit_all", "command": "Std_ViewFitAll"})
    with pytest.raises(Exception, match="internal object name"):
        runtime.control_view(
            {
                "operation": "capture_objects",
                "targets": [{"object_name": "Human label with spaces"}],
            }
        )

    inactive, _state, _document = _runtime(active_document=lambda: None)
    with pytest.raises(NativeRuntimeContextError, match="no longer active"):
        inactive.read_state({"operation": "selection"})


def test_production_common_bindings_inject_runtime_arguments_and_ticket(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    registry = build_native_capability_registry()
    ticket = state.begin_call(document.Uid, "state.read")
    observed = []
    monkeypatch.setattr(
        runtime,
        "read_state",
        lambda arguments: observed.append(arguments) or {"surface": "model"},
    )

    implementation = registry.implementation("state.read")
    assert implementation is not None
    result = implementation.handler(
        NativeCapabilityCall({"operation": "active"}, ticket, runtime)
    )

    assert result == {"surface": "model"}
    assert observed == [{"operation": "active"}]
