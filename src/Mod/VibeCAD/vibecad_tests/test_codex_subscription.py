# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for the ChatGPT subscription transport."""

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
    return _surface_context("vibescript.inspect_model")


def _part_vibescript_context() -> dict:
    return _surface_context(
        "vibescript.part.inspect_program",
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
    assert surface["tool_names"] == ["vibescript.part.inspect_program"]
    assert surface["schema_count"] == 1
    assert surface["schema_sha256"] == provider.provider_tool_schema_digest(schemas)
    assert surface["available"] is True
    assert surface["unavailable_reason"] == ""


def test_turn_start_surface_preserves_pure_vibescript_behavior() -> None:
    schemas = [{"name": name} for name in sorted(session.VIBESCRIPT_PROVIDER_TOOLS)]
    surface = session._turn_start_tool_surface("PartDesignWorkbench", schemas)
    assert surface["engine"] == "vibescript"
    assert surface["domain"] == "partdesign"
    assert surface["tool_names"] == [schema["name"] for schema in schemas]


def test_turn_start_surface_accepts_native_tools_without_a_scripted_engine() -> None:
    schemas = [_tool_schema("bim.list_structure")]
    surface = session._turn_start_tool_surface("BIMWorkbench", schemas)
    assert surface["engine"] == "native"
    assert surface["tool_names"] == ["bim.list_structure"]


def test_turn_start_surface_rejects_multiple_scripted_engines() -> None:
    schemas = [
        _tool_schema("vibescript.inspect_model"),
        _tool_schema("build123d.inspect_model"),
    ]
    with pytest.raises(ValueError, match="multiple modeling engines"):
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
    assert names == {("vibescript", "inspect_model"): "vibescript.inspect_model"}
    assert tools == [
        {
            "type": "namespace",
            "name": "vibescript",
            "description": "VibeCAD vibescript operations available now.",
            "tools": [
                {
                    "type": "function",
                    "name": "inspect_model",
                    "description": "Call vibescript.inspect_model.",
                    "deferLoading": False,
                    "inputSchema": _scripted_context()["provider_tool_schemas"][0]["parameters"],
                }
            ],
        }
    ]


def test_codex_dynamic_tools_accept_one_domain_qualified_namespace() -> None:
    tools, names = provider._codex_dynamic_tool_surface(_part_vibescript_context())
    assert names == {("vibescript", "part_inspect_program"): "vibescript.part.inspect_program"}
    assert [namespace["name"] for namespace in tools] == ["vibescript"]


def test_turn_start_surface_rejects_mixed_native_and_vibescript_namespaces() -> None:
    schemas = [
        _tool_schema("part.measure"),
        _tool_schema("vibescript.part.inspect_program"),
    ]
    with pytest.raises(ValueError, match="cannot contain native"):
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
    context["provider_tool_surface"]["engine"] = "openscad"
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
    next_context["vibescript"] = {"models": []}
    monkeypatch.setattr(session, "_context_for_provider", lambda *_args: next_context)
    monkeypatch.setattr(
        session,
        "_complete_vibescript_provider_context",
        lambda _service, context: dict(context),
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
        lambda _service: {
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

    result = runner("vibescript.inspect_model", "{}")

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


def test_provider_capability_preferences_have_explicit_defaults() -> None:
    settings = preferences.VibeCADSettings()
    assert settings.web_search_enabled is False
    assert settings.design_review_enabled is True
    assert settings.codex_skills_enabled is False


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
    assert isinstance(selected, provider.ChatGPTSubscriptionProvider)
    assert selected.web_search_enabled is True
    assert selected.skills_enabled is True


@pytest.mark.parametrize(
    ("provider_name", "provider_type"),
    [
        ("openai", provider.OpenAIProvider),
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

    selected = session.choose_provider(_Service())
    assert isinstance(selected, provider_type)
    assert selected.web_search_enabled is True


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
