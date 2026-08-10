# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeModelCatalogRuntime as runtime_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeModelCatalogRuntime import NativeModelCatalogRuntime
from VibeCADNativeModelCatalogSchema import model_catalog_capability_definition
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Document:
    Uid = "document-catalog"
    Name = "DocumentCatalog"


def _runtime() -> NativeModelCatalogRuntime:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("model-catalog-unit")
    return NativeModelCatalogRuntime(
        NativeRuntimeContext(
            service=SimpleNamespace(),
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=lambda: None,
            active_document=lambda: document,
            active_surface_id=lambda: "model",
            edit_or_task_active=lambda: False,
        )
    )


def _fastener_arguments() -> dict[str, object]:
    return {
        "operation": "fasteners",
        "query": "socket cap",
        "family": "Screw",
        "standard": "ISO4762",
        "nominal_thread": "M3",
        "length_mm": 10.0,
        "limit": 5,
    }


def test_catalog_contract_combines_hole_and_bounded_fastener_discovery() -> None:
    definition = model_catalog_capability_definition()
    schema = definition.provider_schema(("hole_threads", "fasteners"))
    parameters = schema["parameters"]

    assert definition.name == "model.catalog"
    assert definition.primary_classification == "read"
    assert parameters["additionalProperties"] is False
    assert parameters["properties"]["operation"]["enum"] == [
        "hole_threads",
        "fasteners",
    ]
    assert parameters["properties"]["limit"]["maximum"] == 25
    assert parameters["properties"]["query"]["maxLength"] == 256


def test_fastener_catalog_is_shared_with_assemble_without_leaking_hole_catalog() -> None:
    definition = model_catalog_capability_definition()
    variants = {variant.operation: variant for variant in definition.variants}

    assert variants["fasteners"].surface_ids == frozenset({"model", "assemble"})
    assert variants["hole_threads"].surface_ids == frozenset({"model"})


def test_fastener_catalog_returns_only_constructor_relevant_bounded_fields(
    monkeypatch,
) -> None:
    observed = {}

    def search(query, **filters):
        observed.update({"query": query, **filters})
        return {
            "catalog": "freecad-fasteners",
            "catalog_version": "0.5.64",
            "generator_revision": "abc",
            "model_thread_limits": {"maximum_objects_per_document": 32},
            "excluded_upstream_standards": [["NOISY", "DATA"]],
            "requested": {
                "family": "Screw",
                "standard": "ISO4762",
                "nominal_thread": "M3",
                "length_mm": 10.0,
            },
            "total_matches": 1,
            "returned": 1,
            "truncated": False,
            "results": [
                {
                    "standard": "ISO4762",
                    "family": "Screw",
                    "description": "Socket head cap screw",
                    "nominal_threads": ["M2", "M3", "M4"],
                    "nominal_thread": "M3",
                    "requires_length": True,
                    "supports_model_thread": True,
                    "supports_left_handed": True,
                    "option_names": [],
                    "default_options": {},
                    "valid_lengths_mm": [8.0, 10.0, 12.0],
                    "requested_match": True,
                    "constructor": {
                        "standard": "ISO4762",
                        "nominal_thread": "M3",
                        "length_mm": 10.0,
                        "model_thread": False,
                        "left_handed": False,
                        "options": {},
                    },
                    "canonical_key": "freecad-fasteners:123",
                }
            ],
        }

    monkeypatch.setattr(runtime_module, "search_catalog", search)

    result = _runtime().read_catalog(_fastener_arguments())

    assert observed == {
        "query": "socket cap",
        "family": "Screw",
        "standard": "ISO4762",
        "nominal_thread": "M3",
        "length_mm": 10.0,
        "limit": 5,
    }
    assert "excluded_upstream_standards" not in result
    assert "nominal_threads" not in result["results"][0]
    assert result["results"][0]["constructor"]["standard"] == "ISO4762"
    assert result["results"][0]["valid_lengths_mm"] == [8.0, 10.0, 12.0]


def test_fastener_catalog_includes_sizes_only_for_exact_standard_discovery(
    monkeypatch,
) -> None:
    arguments = _fastener_arguments()
    arguments["nominal_thread"] = None
    arguments["length_mm"] = None
    monkeypatch.setattr(
        runtime_module,
        "search_catalog",
        lambda *_args, **_kwargs: {
            "catalog": "freecad-fasteners",
            "requested": {
                "family": "Screw",
                "standard": "ISO4762",
                "nominal_thread": "",
                "length_mm": None,
            },
            "total_matches": 1,
            "returned": 1,
            "truncated": False,
            "results": [
                {
                    "standard": "ISO4762",
                    "family": "Screw",
                    "description": "Socket head cap screw",
                    "nominal_threads": ["M2", "M3", "M4"],
                    "requires_length": True,
                    "supports_model_thread": True,
                    "supports_left_handed": True,
                    "option_names": [],
                }
            ],
        },
    )

    result = _runtime().read_catalog(arguments)

    assert result["results"][0]["nominal_threads"] == ["M2", "M3", "M4"]


def test_catalog_rejects_extra_fields_and_invalid_native_bounds(monkeypatch) -> None:
    runtime = _runtime()
    arguments = _fastener_arguments()
    arguments["selection"] = "current"
    with pytest.raises(NativeArgumentError, match="do not match"):
        runtime.read_catalog(arguments)

    arguments = _fastener_arguments()
    arguments["limit"] = True
    with pytest.raises(NativeModelError, match="limit"):
        runtime.read_catalog(arguments)

    monkeypatch.setattr(
        runtime_module,
        "search_catalog",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            runtime_module.FastenerCatalogError("unavailable exact length")
        ),
    )
    with pytest.raises(NativeModelError, match="unavailable exact length"):
        runtime.read_catalog(_fastener_arguments())
