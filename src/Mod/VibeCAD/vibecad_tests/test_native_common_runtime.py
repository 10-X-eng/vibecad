# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import VibeCADNativeCommonRuntime as runtime_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeAssemblyProviderState import provider_assembly_state
from VibeCADNativeCommonRuntime import NativeCommonRuntime
from VibeCADNativeCommonSchema import common_capability_definitions
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
        scoped_capability_prefix=overrides.get("scoped_capability_prefix"),
        document_thread_dispatch=overrides.get("document_thread_dispatch"),
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


def test_active_assembly_state_read_returns_the_model_visible_index(
    monkeypatch,
) -> None:
    runtime, _state, _document = _runtime(
        active_surface_id=lambda: "assemble"
    )
    full_domain = {
        "kind": "assembly",
        "assembly_count": 1,
        "active_assembly": {"object_name": "Assembly"},
        "assemblies": [
            {
                "object_name": "Assembly",
                "counts": {"components": 1, "joints": 0, "grounded": 1},
                "components": [
                    {
                        "object_name": "Base",
                        "grounded": True,
                        "placement": {"origin_mm": {"x": 1, "y": 2, "z": 3}},
                        "shape": {"faces": 1000},
                    }
                ],
                "joints": [],
            }
        ],
        "available_component_sources": [{"object_name": "Unused"}],
        "robot_tool_shapes": {
            "candidate_count": 38,
            "candidates": [{"shape": {"faces": 1000}}],
        },
    }
    monkeypatch.setattr(
        runtime_module,
        "build_active_snapshot",
        lambda _document, _surface, _state: {"domain": full_domain},
    )

    assert runtime.read_state({"operation": "active"}) == {
        "domain": provider_assembly_state(full_domain)
    }


def test_active_state_read_uses_the_same_provider_view_as_turn_start(
    monkeypatch,
) -> None:
    runtime, _state, _document = _runtime(
        active_surface_id=lambda: "drawing"
    )
    full_snapshot = {
        "surface_id": "drawing",
        "domain": {"kind": "drawing", "pages": [{"object_name": "Page"}]},
    }
    provider_snapshot = {
        "surface_id": "drawing",
        "domain": {
            "kind": "drawing",
            "pages": [
                {
                    "page_name": "Page",
                    "page_target": {
                        "object_name": "Page",
                        "expected_state_sha256": "a" * 64,
                    },
                }
            ],
        },
    }
    monkeypatch.setattr(
        runtime_module,
        "build_active_snapshot",
        lambda _document, _surface, _state: full_snapshot,
    )
    calls = []

    def compact(snapshot):
        calls.append(snapshot)
        return provider_snapshot

    monkeypatch.setattr(
        runtime_module,
        "provider_visible_native_state",
        compact,
        raising=False,
    )

    assert runtime.read_state({"operation": "active"}) == provider_snapshot
    assert calls == [full_snapshot]


def test_successful_undo_is_not_rejected_when_state_refresh_fails(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime(
        active_surface_id=lambda: "drawing",
        scoped_capability_prefix="drawing",
    )
    result = {"undone": {"capability": "drawing.note"}, "undo_available": False}
    undo_calls = []
    monkeypatch.setattr(
        runtime._undo,
        "undo_latest",
        lambda **kwargs: (
            undo_calls.append(kwargs) or SimpleNamespace(result=result)
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("drawing refresh failed")),
    )

    response = runtime.undo_document(
        {"operation": "assistant_local"},
        ticket=state.begin_call(document.Uid, "document.undo"),
    )

    assert response == {"result": result}
    assert undo_calls[0]["capability_prefix"] == "drawing"


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
    monkeypatch.setattr(
        runtime_module,
        "set_isometric",
        lambda target: {"document": target.Name, "orientation": "isometric"},
    )
    monkeypatch.setattr(
        runtime_module,
        "set_standard_view",
        lambda target, orientation: {
            "document": target.Name,
            "orientation": orientation,
        },
    )
    monkeypatch.setattr(
        runtime_module,
        "set_grid_visible",
        lambda target, visible: {
            "document": target.Name,
            "grid_visible": visible,
        },
    )

    assert runtime.control_view({"operation": "fit_all"})["fit"] is True
    assert runtime.control_view({"operation": "set_isometric"}) == {
        "document": document.Name,
        "orientation": "isometric",
    }
    assert runtime.control_view({"operation": "set_top"}) == {
        "document": document.Name,
        "orientation": "top",
    }
    assert runtime.control_view({"operation": "set_grid"}) == {
        "document": document.Name,
        "grid_visible": True,
    }
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


def test_element_inspection_accepts_a_bounded_batch(monkeypatch) -> None:
    runtime, _state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "inspect_element",
        lambda target_document, target: {
            "target": target.summary(),
            "same_document": target_document is document,
        },
    )

    result = runtime.inspect(
        {
            "operation": "element",
            "targets": [
                {"object_name": "Plate", "subelement": "Edge1"},
                {"object_name": "Plate", "subelement": "Edge2"},
                {"object_name": "Plate", "subelement": "Edge3"},
            ],
        }
    )

    assert [item["target"]["subelement"] for item in result["elements"]] == [
        "Edge1",
        "Edge2",
        "Edge3",
    ]
    assert all(item["same_document"] for item in result["elements"])


def test_radius_inspection_accepts_a_bounded_comparison_batch(monkeypatch) -> None:
    definition = next(
        item for item in common_capability_definitions() if item.name == "inspect.query"
    )
    radius = next(item for item in definition.variants if item.operation == "radius")
    assert radius.parameters["properties"]["targets"]["maxItems"] == 16

    runtime, _state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "measure_radius",
        lambda target_document, target: {
            "target": target.summary(),
            "radius_mm": 10.0 if target.subelement == "Face1" else 20.0,
            "same_document": target_document is document,
        },
    )

    result = runtime.inspect(
        {
            "operation": "radius",
            "targets": [
                {"object_name": "GearOne", "subelement": "Face1"},
                {"object_name": "GearTwo", "subelement": "Face2"},
            ],
        }
    )

    assert [item["radius_mm"] for item in result["measurements"]] == [10.0, 20.0]
    assert all(item["same_document"] for item in result["measurements"])


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


def test_first_projected_geometry_read_defaults_the_prior_hash(
    monkeypatch,
) -> None:
    runtime, _state, _document = _runtime(
        active_surface_id=lambda: "drawing"
    )
    view = SimpleNamespace(Name="Front")
    monkeypatch.setattr(
        runtime_module,
        "resolve_object",
        lambda *_args, **_kwargs: view,
    )
    observed = []
    monkeypatch.setattr(
        runtime_module,
        "drawing_projected_geometry_page",
        lambda target, **values: observed.append((target, values))
        or {"elements": []},
    )
    monkeypatch.setattr(
        runtime_module,
        "provider_projected_geometry_page",
        lambda page, **_kwargs: page,
    )

    assert runtime.read_projected_geometry(
        {
            "operation": "read",
            "view": {
                "object_name": "Front",
            },
        }
    ) == {"elements": []}
    assert observed == [
        (
            view,
            {
                "offset": 0,
                "page_size": 48,
            },
        )
    ]


def test_projected_geometry_provider_result_keeps_only_dimensioning_signal(
    monkeypatch,
) -> None:
    runtime, _state, _document = _runtime(active_surface_id=lambda: "drawing")
    view = SimpleNamespace(Name="Front")
    monkeypatch.setattr(runtime_module, "resolve_object", lambda *_args, **_kwargs: view)
    monkeypatch.setattr(
        runtime_module,
        "drawing_view_state",
        lambda _view: {"state_sha256": "c" * 64},
    )
    monkeypatch.setattr(
        runtime_module,
        "drawing_projected_geometry_page",
        lambda *_args, **_kwargs: {
            "view": {
                "object_name": "Front",
                "type_id": "TechDraw::DrawViewPart",
                "projection_state_sha256": "a" * 64,
            },
            "coordinate_space": "view_projection_scaled_centered",
            "axis_convention": "x_right_y_up",
            "view_scale": 2.0,
            "counts": {"edges": 1, "vertices": 0, "faces": 0, "total": 1},
            "offset": 0,
            "returned_count": 1,
            "next_offset": None,
            "elements": [
                {
                    "name": "Edge0",
                    "element_type": "edge",
                    "geometry_type": "Line",
                    "edge_class": "outline",
                    "visible": True,
                    "closed": False,
                    "length_view_mm": 80.0,
                    "bounds_in_view_mm": {
                        "min_x_mm": -40.0,
                        "min_y_mm": 0.0,
                        "max_x_mm": 40.0,
                        "max_y_mm": 0.0,
                        "width_mm": 80.0,
                        "height_mm": 0.0,
                    },
                    "start_in_view_mm": {"x_mm": -40.0, "y_mm": 0.0},
                    "end_in_view_mm": {"x_mm": 40.0, "y_mm": 0.0},
                    "midpoint_in_view_mm": {"x_mm": 0.0, "y_mm": 0.0},
                    "hlr_source_index": 4,
                    "source_mapping": {
                        "status": "exact",
                        "candidates": [
                            {"object_name": "Body", "subelement": "Edge5"}
                        ],
                    },
                    "element_state_sha256": "b" * 64,
                    "axonometric_value_mode": "x_axis_true_length",
                }
            ],
        },
    )
    monkeypatch.setitem(
        sys.modules,
        "TechDrawGui",
        SimpleNamespace(
            inspectProjectedDimensionApplicability=lambda _view, names: {
                name: {
                    "valid_dimensions": ["aligned", "horizontal"],
                    "approximate_dimensions": [],
                }
                for name in names
            }
        ),
    )

    result = runtime.read_projected_geometry(
        {
            "operation": "read",
            "view": {
                "object_name": "Front",
                "expected_state_sha256": "c" * 64,
            },
        }
    )

    assert result["elements"] == [
        {
            "name": "Edge0",
            "kind": "line",
            "visible": True,
            "edge_class": "outline",
            "start_in_view_mm": {"x_mm": -40.0, "y_mm": 0.0},
            "end_in_view_mm": {"x_mm": 40.0, "y_mm": 0.0},
            "length_mm": 40.0,
            "orientation": "horizontal",
            "valid_dimensions": ["aligned", "horizontal"],
            "true_length_axis": "x",
            "source": {"object_name": "Body", "subelement": "Edge5"},
        }
    ]
    assert "bounds_in_view_mm" not in result["elements"][0]
    assert "midpoint_in_view_mm" not in result["elements"][0]
    assert "hlr_source_index" not in result["elements"][0]


def test_drawing_source_catalog_defaults_to_the_first_bounded_page(
    monkeypatch,
) -> None:
    runtime, _state, document = _runtime(
        active_surface_id=lambda: "drawing"
    )
    observed = []
    monkeypatch.setattr(
        runtime_module,
        "drawing_source_catalog_page",
        lambda target, **values: observed.append((target, values))
        or {"source_count": 100, "next_offset": 48, "sources": []},
    )

    result = runtime.read_drawing_sources({"operation": "list"})

    assert result["source_count"] == 100
    assert result["next_offset"] == 48
    assert observed == [
        (
            document,
            {
                "offset": 0,
                "page_size": 48,
                "structural_revision": 0,
                "require_cached": False,
            },
        )
    ]


def test_gui_drawing_source_read_requires_the_responsive_cache(monkeypatch) -> None:
    runtime, _state, _document = _runtime(
        active_surface_id=lambda: "drawing",
        document_thread_dispatch=lambda operation: operation(),
    )
    options = []
    monkeypatch.setattr(
        runtime_module,
        "drawing_source_catalog_page",
        lambda _document, **values: options.append(values) or {"sources": []},
    )

    runtime.read_drawing_sources({"operation": "list"})

    assert options == [
        {
            "offset": 0,
            "page_size": 48,
            "structural_revision": 0,
            "require_cached": True,
        }
    ]


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
