# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for VibeCAD's bundled Codex transport."""

from __future__ import annotations

import base64
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import VibeCADCodex as codex
import VibeCADPreferences as preferences
import VibeCADProvider as provider
import VibeCADSession as session
from VibeCADTools import SafetyLevel


def _tool_schema(name: str) -> dict:
    return {
        "name": name,
        "description": f"Call {name}.",
        "parameters": {
            "type": "object",
            "properties": {
                "model_id": {
                    "type": "string",
                    "description": "Exact model identifier.",
                }
            },
            "required": ["model_id"],
            "additionalProperties": False,
        },
    }


def _surface_context(*names: str, workbench: str = "PartDesignWorkbench") -> dict:
    schemas = [_tool_schema(name) for name in names]
    return {
        "provider_tool_schemas": schemas,
        "provider_tool_surface": session._turn_start_tool_surface(workbench, schemas),
    }


def _scripted_context() -> dict:
    return _surface_context(
        "vibescript.read_source",
        "vibescript.create_program",
    )


def _part_vibescript_context() -> dict:
    return _surface_context(
        "vibescript.read_source",
        "vibescript.create_program",
        workbench="PartWorkbench",
    )


def test_codex_dynamic_tools_require_a_frozen_turn_start_surface() -> None:
    context = _part_vibescript_context()
    context.pop("provider_tool_surface")
    with pytest.raises(provider.ProviderUnavailable, match="frozen turn-start"):
        provider._codex_dynamic_tool_surface(context)


def test_turn_start_surface_accepts_one_workbench_vibescript_domain() -> None:
    schemas = _part_vibescript_context()["provider_tool_schemas"]
    surface = session._turn_start_tool_surface("PartWorkbench", schemas)
    assert surface["kind"] == "turn_start_snapshot"
    assert surface["frozen"] is True
    assert surface["engine"] == "vibescript"
    assert surface["domain"] == "part"
    assert surface["workbench"] == "PartWorkbench"
    assert surface["tool_names"] == [
        "vibescript.read_source",
        "vibescript.create_program",
    ]
    assert surface["schema_count"] == 2
    assert surface["schema_sha256"] == provider.provider_tool_schema_digest(schemas)
    assert surface["available"] is True
    assert surface["unavailable_reason"] == ""


def test_turn_start_surface_preserves_pure_vibescript_behavior() -> None:
    from VibeCADModelingSurface import resolve_modeling_surface

    resolution = resolve_modeling_surface("PartDesignWorkbench", "vibescript")
    schemas = [_tool_schema(name) for name in resolution.tool_names]
    surface = session._turn_start_tool_surface("PartDesignWorkbench", schemas)
    assert surface["engine"] == "vibescript"
    assert surface["domain"] == "partdesign"
    assert surface["tool_names"] == [schema["name"] for schema in schemas]


def test_turn_start_surface_rejects_multiple_vibescript_domains() -> None:
    schemas = [
        _tool_schema("vibescript.partdesign.create_program"),
        _tool_schema("vibescript.assembly.create_program"),
    ]
    with pytest.raises(ValueError, match="active domain namespace"):
        session._turn_start_tool_surface("AssemblyWorkbench", schemas)


@pytest.mark.parametrize(
    "schemas",
    (
        [],
        [{"description": "missing name"}],
        [_tool_schema("assembly.solve"), _tool_schema("assembly.solve")],
    ),
)
def test_turn_start_surface_rejects_malformed_declarations(schemas: list[dict]) -> None:
    with pytest.raises(ValueError):
        session._turn_start_tool_surface("AssemblyWorkbench", schemas)


def test_codex_dynamic_tools_preserve_vibecad_namespaces_and_schema() -> None:
    tools, names = provider._codex_dynamic_tool_surface(_scripted_context())
    assert names == {
        ("vibescript", "read_source"): "vibescript.read_source",
        ("vibescript", "create_program"): "vibescript.create_program",
    }
    assert [namespace["name"] for namespace in tools] == ["vibescript"]
    read_tool = tools[0]["tools"][0]
    assert read_tool["name"] == "read_source"
    assert (
        read_tool["inputSchema"]
        == _scripted_context()["provider_tool_schemas"][0]["parameters"]
    )


def test_codex_dynamic_tools_use_one_workbench_neutral_namespace() -> None:
    tools, names = provider._codex_dynamic_tool_surface(_part_vibescript_context())
    assert names == {
        ("vibescript", "read_source"): "vibescript.read_source",
        ("vibescript", "create_program"): "vibescript.create_program",
    }
    assert [namespace["name"] for namespace in tools] == ["vibescript"]


def test_turn_start_surface_rejects_human_mutation_commands() -> None:
    schemas = [
        _tool_schema("part.measure"),
        _tool_schema("vibescript.part.create_program"),
    ]
    with pytest.raises(ValueError, match="mutation or foreign read"):
        session._turn_start_tool_surface("PartWorkbench", schemas)


def test_codex_dynamic_tools_reject_surface_name_or_schema_drift() -> None:
    name_drift = _part_vibescript_context()
    name_drift["provider_tool_surface"]["tool_names"] = []
    with pytest.raises(provider.ProviderUnavailable, match="do not match"):
        provider._codex_dynamic_tool_surface(name_drift)

    schema_drift = _part_vibescript_context()
    schema_drift["provider_tool_schemas"][0]["description"] = "Changed after freeze."
    with pytest.raises(provider.ProviderUnavailable, match="changed after"):
        provider._codex_dynamic_tool_surface(schema_drift)


def test_codex_dynamic_tools_reject_a_false_scripted_engine_declaration() -> None:
    context = _part_vibescript_context()
    context["provider_tool_surface"]["engine"] = "invalid"
    with pytest.raises(provider.ProviderUnavailable, match="does not match"):
        provider._codex_dynamic_tool_surface(context)


def test_provider_update_keeps_the_turn_surface_frozen_after_workbench_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _part_vibescript_context()
    next_context = _scripted_context()
    next_context["workbench"] = "PartDesignWorkbench"
    next_context["modeling_surface"] = {
        "workbench": "PartDesignWorkbench",
        "engine": "vibescript",
        "domain": "partdesign",
        "surface_id": next_context["provider_tool_surface"]["surface_id"],
    }
    monkeypatch.setattr(
        session,
        "_build_context_for_provider",
        lambda *_args: next_context,
    )

    initial_surface = dict(initial["provider_tool_surface"])
    initial_schemas = list(initial["provider_tool_schemas"])
    runner = session.make_provider_tool_runner(
        object(),
        tool_trace=[],
        progress_callback=None,
        cancellation_check=None,
        steering_check=None,
        question_callback=None,
        turn_surface=initial_surface,
        turn_schemas=initial_schemas,
        turn_modeling_surface={
            "workbench": "PartWorkbench",
            "engine": "vibescript",
            "domain": "part",
            "surface_id": initial_surface["surface_id"],
        },
    )

    updated = runner.provider_update()

    assert updated["provider_tool_surface"] == initial_surface
    assert updated["provider_tool_schemas"] == initial_schemas
    assert updated["workbench"] == "PartWorkbench"
    assert updated["modeling_surface"]["invalidated"] is True
    assert updated["modeling_surface"]["next_turn_required"] is True
    assert "vibescript_domain" not in updated


def test_codex_dynamic_tools_reject_malformed_or_extended_snapshots() -> None:
    malformed = _part_vibescript_context()
    malformed["provider_tool_schemas"][0]["parameters"] = {"type": "string"}
    malformed["provider_tool_surface"] = session._turn_start_tool_surface(
        "PartWorkbench", malformed["provider_tool_schemas"]
    )
    with pytest.raises(provider.ProviderUnavailable, match="Invalid frozen schema"):
        provider._codex_dynamic_tool_surface(malformed)

    extended = _part_vibescript_context()
    extended["provider_tool_surface"]["unexpected"] = True
    with pytest.raises(provider.ProviderUnavailable, match="unexpected fields"):
        provider._codex_dynamic_tool_surface(extended)


def test_tool_runner_revalidates_each_call_against_the_live_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Spec:
        def validate_arguments(self, _args: dict) -> None:
            raise AssertionError("A removed tool must be blocked before validation.")

    class _Registry:
        def __init__(self) -> None:
            self.called = False

        def get(self, _name: str):
            return SimpleNamespace(
                safety=SafetyLevel.READ,
                workbench="PartDesignWorkbench",
                spec=_Spec(),
            )

        def call(self, _name: str, **_args):
            self.called = True
            raise AssertionError("A removed tool must never execute.")

    class _Service:
        def __init__(self) -> None:
            self.registry = _Registry()

        def active_workbench_name(self) -> str:
            return "AssemblyWorkbench"

    service = _Service()
    monkeypatch.setattr(
        session,
        "_live_provider_surface_state",
        lambda _service, _interaction_mode="build": {
            "workbench": "AssemblyWorkbench",
            "runtime_state": {"edit_mode": "none"},
            "tool_names": ["assembly.solve"],
        },
    )
    runner = session.make_provider_tool_runner(
        service,
        tool_trace=[],
        progress_callback=None,
        cancellation_check=None,
        steering_check=None,
        question_callback=None,
    )

    result = runner("vibescript.part.create_program", "{}")

    assert result["ok"] is False
    assert result["failure_code"] == "TOOL_NOT_ON_ACTIVE_SURFACE"
    assert result["candidates"] == ["assembly.solve"]
    assert service.registry.called is False


def test_codex_images_use_the_bounded_inline_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screenshot = tmp_path / "viewport.png"
    screenshot.write_bytes(b"x" * (provider.CODEX_INLINE_IMAGE_MAX_BYTES + 1))
    encoded = b"\xff\xd8" + (b"v" * 1024) + b"\xff\xd9"
    calls: list[tuple[Path, int, bool]] = []

    def encode(
        path: Path,
        *,
        max_bytes: int,
        prefer_jpeg: bool,
    ) -> tuple[str, bytes, dict]:
        calls.append((path, max_bytes, prefer_jpeg))
        return (
            "image/jpeg",
            encoded,
            {
                "resized": True,
                "encoded_format": "jpg",
                "image_size": [1280, 812],
                "size_bytes": len(encoded),
            },
        )

    monkeypatch.setattr(provider, "_provider_encoded_image_payload", encode)
    context = {
        "view_screenshot": {
            "captured": True,
            "new_observation": True,
            "pending_attachment": True,
            "path": str(screenshot),
        }
    }

    turn_input = provider._codex_turn_input("Inspect it.", context)
    tool_output = provider._codex_tool_image_content_items(context)

    assert calls == [
        (screenshot, provider.CODEX_INLINE_IMAGE_MAX_BYTES, True),
        (screenshot, provider.CODEX_INLINE_IMAGE_MAX_BYTES, True),
    ]
    turn_image = turn_input[-1]
    tool_image = tool_output[-1]
    assert turn_image["type"] == "image"
    assert tool_image["type"] == "inputImage"
    assert turn_image["url"] == tool_image["imageUrl"]
    assert turn_image["url"].startswith("data:image/jpeg;base64,")
    assert base64.b64decode(turn_image["url"].partition(",")[2]) == encoded
    assert len(encoded) <= provider.CODEX_INLINE_IMAGE_MAX_BYTES


def test_consumed_view_is_not_attached_to_a_later_provider_turn(
    tmp_path: Path,
) -> None:
    screenshot = tmp_path / "viewport.png"
    screenshot.write_bytes(b"png")
    context = {
        "view_screenshot": {
            "captured": True,
            "pending_attachment": False,
            "path": str(screenshot),
        }
    }

    assert provider._screenshot_image_payload(context) is None
    assert provider._context_image_blocks(context) == []


def test_session_consumes_the_exact_view_after_copying_provider_context() -> None:
    consumed: list[dict] = []
    service = SimpleNamespace(
        consume_view_screenshot_attachment=lambda value: consumed.append(dict(value))
    )
    screenshot = {
        "captured": True,
        "pending_attachment": True,
        "path": "/project/screenshots/view.png",
    }
    context = {"view_screenshot": dict(screenshot)}

    session._consume_context_view_attachment(
        service, context, lambda operation: operation()
    )

    assert consumed == [screenshot]
    assert context["view_screenshot"] == screenshot


def test_codex_thread_config_disables_non_vibecad_tool_surfaces() -> None:
    config = codex.vibecad_thread_config()
    assert config["orchestrator.mcp.enabled"] is False
    assert config["orchestrator.skills.enabled"] is False
    assert config["project_doc_max_bytes"] == 0
    assert config["tools.experimental_request_user_input.enabled"] is False
    assert config["skills.include_instructions"] is False
    assert config["features.shell_tool"] is False
    assert config["features.plugins"] is False
    assert config["web_search"] == "disabled"
    assert config["include_collaboration_mode_instructions"] is False
    assert config["features.code_mode"] == {
        "enabled": False,
        "direct_only_tool_namespaces": ["core"],
    }


def test_codex_thread_config_enables_only_web_and_skill_capabilities() -> None:
    config = codex.vibecad_thread_config(
        web_search_enabled=True,
        skills_enabled=True,
    )
    assert config["web_search"] == "live"
    assert config["skills.bundled.enabled"] is True
    assert config["skills.include_instructions"] is True
    assert config["orchestrator.skills.enabled"] is False
    assert config["features.shell_tool"] is False
    assert config["features.browser_use"] is False
    assert config["features.computer_use"] is False
    assert config["features.plugins"] is False


def test_codex_thread_config_enables_plan_and_api_key_provider() -> None:
    config = codex.vibecad_thread_config(
        collaboration_mode_enabled=True,
        openai_base_url="https://api.example.test/v1/",
    )

    assert config["include_collaboration_mode_instructions"] is True
    assert config["model_provider"] == codex.CODEX_OPENAI_PROVIDER_ID
    prefix = f"model_providers.{codex.CODEX_OPENAI_PROVIDER_ID}"
    assert config[f"{prefix}.base_url"] == "https://api.example.test/v1"
    assert config[f"{prefix}.env_key"] == codex.CODEX_OPENAI_API_KEY_ENV
    assert config[f"{prefix}.wire_api"] == "responses"
    assert config[f"{prefix}.requires_openai_auth"] is False


def test_codex_environment_uses_only_the_selected_vibecad_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(codex.CODEX_HOME_ENV, str(tmp_path / "codex-home"))
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai")
    monkeypatch.setenv("CODEX_API_KEY", "ambient-codex")

    environment = codex._subprocess_environment(
        {codex.CODEX_OPENAI_API_KEY_ENV: "selected-key"}
    )

    assert "OPENAI_API_KEY" not in environment
    assert "CODEX_API_KEY" not in environment
    assert environment[codex.CODEX_OPENAI_API_KEY_ENV] == "selected-key"


def test_provider_capability_preferences_have_explicit_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnsetPreferences:
        def GetBool(self, _name: str, default: bool) -> bool:
            return default

        def GetString(self, _name: str, default: str) -> str:
            return default

        def GetFloat(self, _name: str, default: float) -> float:
            return default

        def GetInt(self, _name: str, default: int) -> int:
            return default

    settings = preferences.VibeCADSettings()
    assert settings.web_search_enabled is False
    assert settings.design_review_enabled is False
    assert settings.codex_skills_enabled is False

    monkeypatch.setattr(preferences, "preferences", lambda: _UnsetPreferences())
    loaded = preferences.load_settings()
    assert loaded.design_review_enabled is False

    class _OptedInPreferences(_UnsetPreferences):
        def GetBool(self, name: str, default: bool) -> bool:
            if name == "DesignReviewEnabled":
                return True
            return default

    monkeypatch.setattr(preferences, "preferences", lambda: _OptedInPreferences())
    assert preferences.load_settings().design_review_enabled is True


def test_codex_skill_reader_is_scoped_to_enabled_skill_directory(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "design-review"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# Design review\n", encoding="utf-8")
    reference = skill_dir / "references" / "checks.md"
    reference.parent.mkdir()
    reference.write_text("Check interfaces.\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("private\n", encoding="utf-8")
    catalog = {
        "design-review": codex.CodexSkill(
            name="design-review",
            description="Review a design.",
            path=skill_file,
        )
    }

    main = codex.read_codex_skill_resource(catalog, name="design-review")
    assert main == {
        "ok": True,
        "skill": "design-review",
        "resource": "SKILL.md",
        "content": "# Design review\n",
    }
    nested = codex.read_codex_skill_resource(
        catalog,
        name="design-review",
        resource="references/checks.md",
    )
    assert nested["ok"] is True
    assert nested["content"] == "Check interfaces.\n"
    escaped = codex.read_codex_skill_resource(
        catalog,
        name="design-review",
        resource="../../outside.md",
    )
    assert escaped["ok"] is False
    assert "inside the skill directory" in escaped["error"]


def test_codex_skill_catalog_uses_personal_root_and_enabled_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vibecad_home = tmp_path / "vibecad-codex"
    personal_home = tmp_path / "personal-codex"
    personal_root = personal_home / "skills"
    personal_root.mkdir(parents=True)
    skill_file = personal_root / "cad-review" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text("# CAD review\n", encoding="utf-8")
    monkeypatch.setenv(codex.CODEX_HOME_ENV, str(vibecad_home))
    monkeypatch.setenv("CODEX_HOME", str(personal_home))

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def request(self, method: str, params: dict, timeout: float) -> dict:
            self.calls.append((method, params))
            if method == "skills/extraRoots/set":
                return {}
            if method == "skills/list":
                return {
                    "data": [
                        {
                            "skills": [
                                {
                                    "name": "cad-review",
                                    "description": "Review CAD intent.",
                                    "path": str(skill_file),
                                    "enabled": True,
                                },
                                {
                                    "name": "disabled",
                                    "description": "Disabled.",
                                    "path": str(skill_file),
                                    "enabled": False,
                                },
                            ]
                        }
                    ]
                }
            raise AssertionError(method)

    client = _Client()
    catalog = codex.load_codex_skill_catalog(client, cwd=tmp_path)
    assert list(catalog) == ["cad-review"]
    assert client.calls[0] == (
        "skills/extraRoots/set",
        {"extraRoots": [str(personal_root.resolve())]},
    )
    assert client.calls[1] == (
        "skills/list",
        {"cwds": [str(tmp_path)], "forceReload": True},
    )


def test_current_subscription_reasoning_efforts_are_preserved() -> None:
    assert preferences.normalize_reasoning_effort("max") == "max"
    assert preferences.normalize_reasoning_effort("ultra") == "ultra"


def test_choose_provider_carries_codex_capability_preferences() -> None:
    class _Service:
        def provider_name(self) -> str:
            return "chatgpt"

        def auth_state(self):
            return object()

        def provider_model(self) -> str:
            return "gpt-test"

        def provider_reasoning_effort(self) -> str:
            return "high"

        def web_search_enabled(self) -> bool:
            return True

        def codex_skills_enabled(self) -> bool:
            return True

    selected = session.choose_provider(_Service())
    assert isinstance(selected, provider.CodexProvider)
    assert selected.auth_mode == "chatgpt"
    assert selected.web_search_enabled is True
    assert selected.skills_enabled is True


def test_subscription_provider_identity_is_explicit_and_disables_fallback() -> None:
    selected = provider.CodexProvider(
        model="gpt-5.6-sol",
        auth_mode="chatgpt",
        reasoning_effort="max",
    )

    assert session.provider_execution_identity(selected) == {
        "provider_id": "chatgpt",
        "provider_label": "ChatGPT subscription via Codex",
        "adapter": "CodexProvider",
        "requested_model": "gpt-5.6-sol",
        "model_selection": "explicit",
        "reasoning_effort": "max",
        "model_fallback_allowed": False,
    }


@pytest.mark.parametrize(
    ("provider_name", "provider_type"),
    [
        ("openai", provider.CodexProvider),
        ("anthropic", provider.AnthropicProvider),
    ],
)
def test_choose_provider_enables_web_search_for_api_providers(
    provider_name: str,
    provider_type: type,
) -> None:
    class _Auth:
        can_call_provider = True

    class _Service:
        def provider_name(self) -> str:
            return provider_name

        def auth_state(self):
            return _Auth()

        def provider_model(self) -> str:
            return "test-model"

        def provider_api_key(self) -> str:
            return "test-key"

        def provider_reasoning_effort(self) -> str:
            return "high"

        def provider_base_url(self):
            return None

        def web_search_enabled(self) -> bool:
            return True

        def codex_skills_enabled(self) -> bool:
            return False

        def intent_memory_model(self) -> str:
            return "memory-model"

    selected = session.choose_provider(_Service())
    assert isinstance(selected, provider_type)
    assert selected.web_search_enabled is True
    if provider_name == "openai":
        assert selected.auth_mode == "api_key"
        assert selected.api_key == "test-key"
    else:
        assert selected.compaction_model == "memory-model"


def test_plan_surface_excludes_document_mutation_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Spec:
        def supports_edit_mode(self, _edit_mode: str) -> bool:
            return True

    tools = {
        "core.read": SimpleNamespace(
            safety=SafetyLevel.READ,
            spec=_Spec(),
            to_schema=lambda **_kwargs: _tool_schema("core.read"),
        ),
        "core.view": SimpleNamespace(
            safety=SafetyLevel.VIEW,
            spec=_Spec(),
            to_schema=lambda **_kwargs: _tool_schema("core.view"),
        ),
        "partdesign.write": SimpleNamespace(
            safety=SafetyLevel.SAFE_WRITE,
            spec=_Spec(),
            to_schema=lambda **_kwargs: _tool_schema("partdesign.write"),
        ),
    }
    service = SimpleNamespace(registry=SimpleNamespace(get=lambda name: tools[name]))
    monkeypatch.setattr(session, "_surface_tool_names", lambda *_args: set(tools))

    schemas = session.provider_tool_schemas(
        service,
        "PartDesignWorkbench",
        runtime_state={"edit_mode": False, "active_sketch": None},
        interaction_mode="plan",
    )

    assert [schema["name"] for schema in schemas] == ["core.read", "core.view"]


def test_openai_api_key_and_plan_mode_run_through_codex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Client:
        instance = None

        def __init__(
            self,
            *,
            notification_handler,
            server_request_handler,
            environment=None,
        ) -> None:
            self.notification_handler = notification_handler
            self.server_request_handler = server_request_handler
            self.environment = dict(environment or {})
            self.requests: list[tuple[str, dict]] = []
            self.alive = True
            _Client.instance = self

        @property
        def stderr_tail(self) -> list[str]:
            return []

        def start(self) -> None:
            return None

        def request(self, method: str, params: dict, timeout: float) -> dict:
            self.requests.append((method, dict(params)))
            if method == "thread/start":
                return {"thread": {"id": "thread-1"}, "model": "gpt-test"}
            if method == "turn/start":
                self.notification_handler(
                    "item/completed",
                    {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {"type": "plan", "text": "Inspect, then revise."},
                    },
                )
                self.notification_handler(
                    "turn/completed",
                    {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1", "status": "completed"},
                    },
                )
                return {"turn": {"id": "turn-1"}}
            if method == "thread/delete":
                return {}
            raise AssertionError(method)

        def close(self) -> None:
            self.alive = False

    monkeypatch.setattr(codex, "CodexAppServerClient", _Client)
    active_provider = provider.CodexProvider(
        model="gpt-test",
        api_key="secret-test-key",
        auth_mode="api_key",
        base_url="https://api.example.test/v1",
        reasoning_effort="high",
    )
    context = _surface_context("core.set_view")
    context["_vibecad_interaction_mode"] = "plan"

    result = active_provider.run("Plan the change.", context)

    client = _Client.instance
    assert client is not None
    assert client.environment == {codex.CODEX_OPENAI_API_KEY_ENV: "secret-test-key"}
    assert [method for method, _params in client.requests].count("account/read") == 0
    thread_request = next(
        params for method, params in client.requests if method == "thread/start"
    )
    assert thread_request["modelProvider"] == codex.CODEX_OPENAI_PROVIDER_ID
    assert thread_request["config"]["include_collaboration_mode_instructions"] is True
    turn_request = next(
        params for method, params in client.requests if method == "turn/start"
    )
    assert turn_request["collaborationMode"] == {
        "mode": "plan",
        "settings": {
            "model": "gpt-test",
            "reasoning_effort": "high",
            "developer_instructions": None,
        },
    }
    assert result.final_output == "Inspect, then revise."
    assert result.raw["interaction_mode"] == "plan"


def test_codex_client_initializes_and_reads_account_from_json_rpc(
    tmp_path: Path,
) -> None:
    fake_server = tmp_path / "fake_app_server.py"
    fake_server.write_text(
        """
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        continue
    if method == "initialize":
        result = {"userAgent": "fake"}
    elif method == "account/read":
        result = {"account": None, "requiresOpenaiAuth": True}
    else:
        print(json.dumps({"id": request_id, "error": {"code": -1, "message": method}}), flush=True)
        continue
    print(json.dumps({"id": request_id, "result": result}), flush=True)
""".lstrip(),
        encoding="utf-8",
    )
    command = codex.CodexRuntimeCommand(
        argv=(sys.executable, str(fake_server)),
        executable=Path(sys.executable),
        source="test",
        version="test",
    )
    with codex.CodexAppServerClient(command=command) as client:
        result = client.request("account/read", {"refreshToken": False})
    assert result == {"account": None, "requiresOpenaiAuth": True}
