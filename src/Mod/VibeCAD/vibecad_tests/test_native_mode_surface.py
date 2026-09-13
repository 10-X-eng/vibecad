# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADModelingSurface import provider_engine_for_ribbon, resolve_modeling_surface
import VibeCADRibbonSurface as ribbon_module

from vibecad_tests.test_ribbon_surface import _manifest


@pytest.mark.parametrize(
    ("authoring_engine", "surface_id", "expected"),
    (
        ("vibescript", "model", "vibescript"),
        ("vibescript", "analyze", "native"),
        ("vibescript", "drawing", "native"),
        ("vibescript", "manufacture", "native"),
        ("native", "analyze", "native"),
        ("native", "drawing", "native"),
        ("native", "manufacture", "native"),
        ("native", "model", "native"),
    ),
)
def test_derived_artifact_ribbons_use_native_tools_without_changing_source_authority(
    authoring_engine: str,
    surface_id: str,
    expected: str,
) -> None:
    assert provider_engine_for_ribbon(authoring_engine, surface_id) == expected


def test_native_mode_uses_live_ribbon_identity_and_stays_fail_closed(
    monkeypatch,
) -> None:
    manifest = _manifest()
    manifest["groups"][0]["actions"].append(
        {
            "command_id": "PartDesign_DesignExtrude",
            "kind": "command",
            "label": "Extrude",
            "available": True,
        }
    )
    surface = ribbon_module.RibbonSurface.from_manifest(manifest, revision=12)
    monkeypatch.setattr(ribbon_module, "read_active_ribbon_surface", lambda: surface)

    resolved = resolve_modeling_surface("PartDesignWorkbench", "native")

    assert resolved.engine == "native"
    assert resolved.domain == "model"
    assert resolved.surface_id == (
        f"vibecad/surface/native/model/12/{surface.manifest_sha256[:12]}/"
        f"{surface.environment_sha256[:12]}"
    )
    assert resolved.available is False
    assert resolved.unavailable_reason == (
        "Native mode is not yet complete for this ribbon."
    )
    assert resolved.cad_tool_names == ()
    assert resolved.core_tool_names == ()
    assert resolved.tool_names == ()
    assert not any(name.startswith("vibescript.") for name in resolved.tool_names)


def test_native_mode_rejects_vibescript_schema_names() -> None:
    from VibeCADModelingSurface import validate_surface_names

    with pytest.raises(
        ValueError,
        match="A Native surface cannot contain VibeScript tools",
    ):
        validate_surface_names(
            workbench="PartDesignWorkbench",
            engine="native",
            names=("vibescript.read_source",),
        )


class _NativeService:
    def modeling_engine(self) -> str:
        return "native"

    def provider_debug_config(self) -> dict:
        return {}


def test_native_schema_assembly_never_reads_the_legacy_service_registry(
    monkeypatch,
) -> None:
    import VibeCADNativeProviderContext as provider_context
    import VibeCADSession as session

    schemas = [
        {
            "name": "model.feature",
            "description": "Exact manifest-owned capability.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
    ]
    monkeypatch.setattr(
        provider_context,
        "native_provider_tool_schemas",
        lambda: schemas,
    )
    monkeypatch.setattr(
        session,
        "_provider_safe_tool_names",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Native schemas used the retired service registry")
        ),
    )
    monkeypatch.setattr(
        session,
        "_minimal_runtime_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Native schemas used the VibeScript runtime surface")
        ),
    )

    assert session.provider_tool_schemas(
        _NativeService(),
        "PartDesignWorkbench",
    ) == schemas


def test_analyze_ribbon_selects_native_schemas_for_vibescript_geometry(
    monkeypatch,
) -> None:
    import VibeCADNativeProviderContext as provider_context
    import VibeCADSession as session

    schemas = [
        {
            "name": "analyze.model",
            "description": "Create or update a study.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
    ]
    monkeypatch.setattr(
        ribbon_module,
        "read_active_ribbon_surface",
        lambda: SimpleNamespace(surface_id="analyze"),
    )
    monkeypatch.setattr(
        provider_context,
        "native_provider_tool_schemas",
        lambda: schemas,
    )
    service = SimpleNamespace(modeling_engine=lambda: "vibescript")

    assert session.provider_tool_schemas(service, "FemWorkbench") == schemas
    assert service.modeling_engine() == "vibescript"


def test_drawing_ribbon_selects_native_schemas_for_vibescript_geometry(
    monkeypatch,
) -> None:
    import VibeCADNativeProviderContext as provider_context
    import VibeCADSession as session

    schemas = [
        {
            "name": "drawing.page",
            "description": "Create or update a drawing page.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
    ]
    monkeypatch.setattr(
        ribbon_module,
        "read_active_ribbon_surface",
        lambda: SimpleNamespace(surface_id="drawing"),
    )
    monkeypatch.setattr(
        provider_context,
        "native_provider_tool_schemas",
        lambda: schemas,
    )
    service = SimpleNamespace(modeling_engine=lambda: "vibescript")

    assert session.provider_tool_schemas(service, "TechDrawWorkbench") == schemas
    assert service.modeling_engine() == "vibescript"


def test_manufacture_ribbon_selects_native_schemas_for_vibescript_geometry(
    monkeypatch,
) -> None:
    import VibeCADNativeProviderContext as provider_context
    import VibeCADSession as session

    schemas = [
        {
            "name": "manufacture.setup",
            "description": "Create or update a machining setup.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
    ]
    monkeypatch.setattr(
        ribbon_module,
        "read_active_ribbon_surface",
        lambda: SimpleNamespace(surface_id="manufacture"),
    )
    monkeypatch.setattr(
        provider_context,
        "native_provider_tool_schemas",
        lambda: schemas,
    )
    service = SimpleNamespace(modeling_engine=lambda: "vibescript")

    assert session.provider_tool_schemas(service, "CAMWorkbench") == schemas
    assert service.modeling_engine() == "vibescript"


def test_legacy_vibescript_cam_remains_installed_but_is_not_available() -> None:
    from VibeCADVibeScriptDomains import (
        domain_availability,
        get_domain_adapter,
        get_vibescript_pack,
    )

    pack = get_vibescript_pack("CAMWorkbench")
    adapter = get_domain_adapter("cam")

    assert pack is not None
    assert adapter is not None
    assert pack.production_ready is False
    assert adapter.production_ready is True
    available, reason = domain_availability("CAMWorkbench")
    assert available is False
    assert "production-readiness gate" in reason


def test_native_availability_queries_use_the_manifest_surface(
    monkeypatch,
) -> None:
    import VibeCADNativeProviderContext as provider_context
    import VibeCADSession as session

    definition = SimpleNamespace(primary_classification="read")
    registry = SimpleNamespace(definition=lambda name: definition if name == "state.read" else None)
    provider = SimpleNamespace(
        available=True,
        tool_names=("model.feature", "state.read"),
    )
    monkeypatch.setattr(
        provider_context,
        "resolve_production_native_surface",
        lambda: (registry, provider),
    )
    service = _NativeService()

    assert session.is_provider_safe_tool(service, "model.feature") is True
    assert session.is_provider_safe_tool(service, "part.make_box") is False
    assert session.is_provider_safe_tool(service, "state.read") is True


def test_native_runner_assembly_returns_before_the_vibescript_runner_path(
    monkeypatch,
) -> None:
    import VibeCADNativeProviderRunner as runner_module
    import VibeCADNativeSessionFactory as factory_module
    import VibeCADSession as session

    execution = object()
    captured = {}

    class _Runner:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    def create_execution(**kwargs):
        captured["factory"] = kwargs
        return execution

    monkeypatch.setattr(runner_module, "NativeProviderToolRunner", _Runner)
    monkeypatch.setattr(
        factory_module,
        "create_native_session_execution",
        create_execution,
    )
    monkeypatch.setattr(
        session,
        "_vibescript_operation_manager",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Native runner entered the VibeScript runner path")
        ),
    )

    output_authorizer = object()
    input_authorizer = object()
    result = session.make_provider_tool_runner(
        _NativeService(),
        tool_trace=[],
        progress_callback=None,
        cancellation_check=None,
        steering_check=None,
        question_callback=None,
        output_authorization_callback=output_authorizer,
        input_authorization_callback=input_authorizer,
        document_thread_dispatch=lambda operation: operation(),
        turn_surface={"engine": "native", "domain": "model"},
        turn_schemas=[{"name": "model.feature"}],
        turn_modeling_surface={"engine": "native", "domain": "model"},
    )

    assert isinstance(result, _Runner)
    assert captured["execution"] is execution
    assert captured["factory"]["expected_surface"] == {
        "engine": "native",
        "domain": "model",
    }
    assert captured["factory"]["expected_schemas"] == [
        {"name": "model.feature"}
    ]
    assert captured["factory"]["output_authorizer"] is output_authorizer
    assert captured["factory"]["input_authorizer"] is input_authorizer
