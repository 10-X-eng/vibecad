# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for first-class Google Gemini provider support."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import VibeCADAuth as auth
import VibeCADDesignReview as design_review
import VibeCADIntentMemoryCompiler as intent_compiler
import VibeCADPreferences as preferences
import VibeCADProvider as provider
import VibeCADSession as session


ROOT = Path(__file__).resolve().parents[4]


def test_readme_documents_gemini_setup() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "**Google Gemini**" in readme
    assert "GEMINI_API_KEY=your-key-here" in readme
    assert "gemini-flash-latest" in readme
    assert auth.DEFAULT_GEMINI_API_BASE in readme


def test_release_paths_package_and_smoke_the_gemini_sdk() -> None:
    requirements = (ROOT / "src/Mod/VibeCAD/requirements.txt").read_text(
        encoding="utf-8"
    )
    installer = (
        ROOT
        / "package/rattler-build/scripts/install_vibecad_provider_deps.sh"
    ).read_text(encoding="utf-8")
    windows_bundle = (
        ROOT / "package/rattler-build/windows/create_bundle.sh"
    ).read_text(encoding="utf-8")
    linux_bundle = (
        ROOT / "package/rattler-build/linux/create_bundle.sh"
    ).read_text(encoding="utf-8")
    macos_bundle = (
        ROOT / "package/rattler-build/osx/create_bundle.sh"
    ).read_text(encoding="utf-8")
    macos_validator = (
        ROOT
        / "package/rattler-build/scripts/validate_vibecad_macos_runtime.py"
    ).read_text(encoding="utf-8")
    local_release = (
        ROOT
        / "package/rattler-build/scripts/build_vibecad_local_release.sh"
    ).read_text(encoding="utf-8")

    assert "openai==3.5.0" in requirements
    assert "pip uninstall --yes openai openai-agents" not in installer
    assert '    "openai",' in installer
    assert "importlib.import_module(module_name)" in installer
    assert ", openai, tuf" in windows_bundle
    assert "openai \\" in linux_bundle
    assert "openai" in macos_bundle
    assert '"openai": _check_module("openai")' in macos_validator
    assert ", openai, numpy" in local_release


def test_gemini_auth_and_preferences_are_first_class() -> None:
    spec = auth.provider_spec("gemini")
    assert spec.display_name == "Google Gemini"
    assert spec.env_var == "GEMINI_API_KEY"
    assert spec.uses_api_key is True
    assert spec.models_url == (
        "https://generativelanguage.googleapis.com/v1beta/openai/models"
    )
    assert preferences.normalize_provider("gemini") == "gemini"
    assert preferences.reasoning_efforts_for_provider("gemini") == (
        "none",
        "minimal",
        "low",
        "medium",
        "high",
    )
    assert "xhigh" in preferences.reasoning_efforts_for_provider("anthropic")

    settings = preferences.VibeCADSettings(
        provider="gemini",
        gemini_model="gemini-flash-latest",
        gemini_intent_memory_model="gemini-3.6-flash",
    )
    assert settings.active_model == "gemini-flash-latest"
    assert settings.active_base_url == provider.DEFAULT_GEMINI_API_BASE
    assert settings.model_for("gemini") == "gemini-flash-latest"
    assert settings.intent_memory_model_for("gemini") == "gemini-3.6-flash"

    credential = auth.resolve_auth_credential(
        env={"GEMINI_API_KEY": "gemini-test-key"},
        provider="gemini",
    )
    assert credential is not None
    assert credential.value == "gemini-test-key"


def test_gemini_model_preferences_round_trip(monkeypatch) -> None:
    class _Preferences:
        def __init__(self) -> None:
            self.values: dict[str, object] = {}

        def GetString(self, name: str, default: str) -> str:
            return str(self.values.get(name, default))

        def GetBool(self, name: str, default: bool) -> bool:
            return bool(self.values.get(name, default))

        def GetFloat(self, name: str, default: float) -> float:
            return float(self.values.get(name, default))

        def GetInt(self, name: str, default: int) -> int:
            return int(self.values.get(name, default))

        def SetString(self, name: str, value: str) -> None:
            self.values[name] = value

        def SetBool(self, name: str, value: bool) -> None:
            self.values[name] = value

        def SetFloat(self, name: str, value: float) -> None:
            self.values[name] = value

        def SetInt(self, name: str, value: int) -> None:
            self.values[name] = value

    storage = _Preferences()
    monkeypatch.setattr(preferences, "preferences", lambda: storage)
    preferences.save_settings(
        preferences.VibeCADSettings(
            provider="gemini",
            gemini_model="gemini-3.6-flash",
            gemini_intent_memory_model="gemini-flash-latest",
        )
    )

    loaded = preferences.load_settings()
    assert loaded.provider == "gemini"
    assert loaded.gemini_model == "gemini-3.6-flash"
    assert loaded.gemini_intent_memory_model == "gemini-flash-latest"
    assert loaded.active_base_url == provider.DEFAULT_GEMINI_API_BASE


def test_choose_provider_uses_the_gemini_chat_completions_adapter() -> None:
    class _Auth:
        can_call_provider = True

    class _Service:
        def provider_name(self) -> str:
            return "gemini"

        def auth_state(self):
            return _Auth()

        def provider_model(self) -> str:
            return "gemini-flash-latest"

        def provider_api_key(self) -> str:
            return "gemini-test-key"

        def provider_reasoning_effort(self) -> str:
            return "high"

        def provider_base_url(self) -> str:
            return provider.DEFAULT_GEMINI_API_BASE

        def web_search_enabled(self) -> bool:
            return False

        def codex_skills_enabled(self) -> bool:
            return False

    selected = session.choose_provider(_Service())

    assert isinstance(selected, provider.GeminiProvider)
    assert selected.api_key == "gemini-test-key"
    assert selected.base_url == provider.DEFAULT_GEMINI_API_BASE
    assert session.provider_execution_identity(selected) == {
        "provider_id": "gemini",
        "provider_label": "Google Gemini",
        "adapter": "GeminiProvider",
        "requested_model": "gemini-flash-latest",
        "model_selection": "explicit",
        "reasoning_effort": "high",
        "model_fallback_allowed": False,
    }


def test_gemini_maps_images_and_function_schemas(monkeypatch) -> None:
    monkeypatch.setattr(
        provider,
        "_context_image_blocks",
        lambda _context: [("R1/1:bracket.png", "image/png", "aW1hZ2U=")],
    )
    monkeypatch.setattr(
        provider,
        "_context_image_delivery_notes",
        lambda _context: ["R_MISS:drawing.png|file unavailable"],
    )

    content = provider._gemini_user_content("Inspect both references.", {})
    assert content == [
        {"type": "text", "text": "Inspect both references."},
        {"type": "text", "text": "R_MISS:drawing.png|file unavailable"},
        {"type": "text", "text": "R1/1:bracket.png"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,aW1hZ2U="},
        },
    ]
    assert provider._gemini_tool_definition(
        {
            "name": "state.read",
            "description": "Read the live CAD state.",
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
        }
    ) == {
        "type": "function",
        "function": {
            "name": "state_read",
            "description": "Read the live CAD state.",
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
        },
    }


def test_gemini_forced_tool_completion_returns_structured_arguments(monkeypatch) -> None:
    requests: list[dict[str, object]] = []
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="submit_result",
                                arguments='{"status":"ready"}',
                            )
                        )
                    ]
                )
            )
        ]
    )

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create)
            )

        @staticmethod
        def _create(**kwargs):
            requests.append(kwargs)
            return response

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_OpenAI))
    result = provider._gemini_forced_tool_completion(
        prompt="Review this.",
        context={},
        model="gemini-flash-latest",
        api_key="gemini-test-key",
        reasoning_effort="high",
        timeout_seconds=10.0,
        base_url=provider.DEFAULT_GEMINI_API_BASE,
        instructions="Return one structured result.",
        tool_schema={
            "name": "submit_result",
            "description": "Submit the result.",
            "parameters": {
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "required": ["status"],
            },
        },
        operation_label="test operation",
    )

    assert result == {"status": "ready"}
    assert requests[0]["tool_choice"] == "required"
    assert requests[0]["reasoning_effort"] == "high"


def test_gemini_routes_structured_review_and_intent_memory(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    review = {
        "verdict": "ready",
        "summary": "Ready to build.",
        "strengths": [],
        "findings": [],
        "required_revisions": [],
        "questions_for_user": [],
    }

    def run_subprocess(**kwargs):
        calls.append(kwargs)
        raw = review if len(calls) == 1 else {"base_revision": 1, "changes": []}
        return provider.ProviderResult(final_output="", raw=raw)

    monkeypatch.setattr(design_review, "_run_provider_subprocess", run_subprocess)
    monkeypatch.setattr(intent_compiler, "_run_provider_subprocess", run_subprocess)

    assert design_review.run_design_review(
        provider="gemini",
        model="gemini-flash-latest",
        api_key="gemini-test-key",
        base_url=provider.DEFAULT_GEMINI_API_BASE,
        reasoning_effort="high",
        customer_intent="Make a bracket.",
        design_draft="Use a ribbed L bracket.",
        context={},
    ) == review
    update = intent_compiler.compile_intent_memory_update(
        provider="gemini",
        model="gemini-flash-latest",
        api_key="gemini-test-key",
        base_url=provider.DEFAULT_GEMINI_API_BASE,
        memory={"revision": 1},
        uncovered_turns=[{"turn_id": "turn-1", "role": "user", "text": "Make it."}],
    )

    assert update == {"base_revision": 1, "changes": []}
    assert calls[0]["child_main"] is design_review._gemini_review_child_main
    assert calls[1]["child_main"] is intent_compiler._gemini_compiler_child_main


class _GeminiConnection:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.closed = False

    def send(self, message: dict[str, object]) -> None:
        self.messages.append(message)

    def recv(self) -> dict[str, object]:
        return {
            "type": "tool_result",
            "result": {"ok": True, "objects": ["Body"]},
            "context": {
                "provider_tool_schemas": [_state_read_schema()],
            },
        }

    def close(self) -> None:
        self.closed = True


def _state_read_schema() -> dict[str, object]:
    return {
        "name": "state.read",
        "description": "Read the live CAD state.",
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    }


def _chunk(*, content: str | None = None, tool_calls=None, finish_reason=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=tool_calls or []),
                finish_reason=finish_reason,
            )
        ]
    )


def test_gemini_stream_preserves_thought_signatures_and_repairs_tool_arguments(
    monkeypatch,
) -> None:
    thought_signature = {"google": {"thought_signature": "signature-17"}}
    first_tool_delta = SimpleNamespace(
        index=0,
        id="gemini-call-17",
        type="function",
        function=SimpleNamespace(name="state_read", arguments="{}"),
        extra_content=thought_signature,
    )
    real_arguments_delta = SimpleNamespace(
        index=0,
        id=None,
        type=None,
        function=SimpleNamespace(name=None, arguments='{"target":"Body"}'),
        extra_content=None,
    )
    streams = [
        iter(
            [
                _chunk(tool_calls=[first_tool_delta]),
                _chunk(tool_calls=[real_arguments_delta]),
                _chunk(finish_reason="tool_calls"),
            ]
        ),
        iter([_chunk(content="Inspection complete."), _chunk(finish_reason="stop")]),
    ]
    client_kwargs: list[dict[str, object]] = []
    client_closed: list[bool] = []
    requests: list[dict[str, object]] = []

    class _OpenAI:
        def __init__(self, **kwargs) -> None:
            client_kwargs.append(kwargs)
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create)
            )

        @staticmethod
        def close() -> None:
            client_closed.append(True)

        @staticmethod
        def _create(**kwargs):
            requests.append(kwargs)
            return streams.pop(0)

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_OpenAI))
    monkeypatch.setattr(
        provider, "_validate_provider_wire_surface", lambda _context: None
    )
    connection = _GeminiConnection()

    provider._gemini_child_main(
        connection,
        "Inspect Body.",
        {"provider_tool_schemas": [_state_read_schema()]},
        "gemini-flash-latest",
        "gemini-test-key",
        "high",
        10.0,
        3,
        False,
        provider.DEFAULT_GEMINI_API_BASE,
    )

    assert client_kwargs == [
        {
            "api_key": "gemini-test-key",
            "base_url": provider.DEFAULT_GEMINI_API_BASE,
            "timeout": 10.0,
            "max_retries": 2,
        }
    ]
    assert client_closed == [True]
    tool_messages = [
        message for message in connection.messages if message.get("type") == "tool"
    ]
    assert tool_messages == [
        {
            "type": "tool",
            "tool_name": "state.read",
            "arguments_json": '{"target":"Body"}',
            "provider_call_id": "gemini-call-17",
        }
    ]
    assert len(requests) == 2
    assert requests[0]["stream"] is True
    assert requests[0]["reasoning_effort"] == "high"
    assert requests[0]["tools"] == [
        provider._gemini_tool_definition(_state_read_schema())
    ]
    assistant_message = requests[1]["messages"][2]
    assert assistant_message["role"] == "assistant"
    assert assistant_message["tool_calls"][0]["function"]["arguments"] == (
        '{"target":"Body"}'
    )
    assert assistant_message["tool_calls"][0]["extra_content"] == thought_signature
    tool_result_message = requests[1]["messages"][3]
    assert tool_result_message["role"] == "tool"
    assert tool_result_message["name"] == "state_read"
    assert tool_result_message["tool_call_id"] == "gemini-call-17"
    assert json.loads(tool_result_message["content"]) == {
        "ok": True,
        "objects": ["Body"],
        "vibecad_state_after": {"workbench": ""},
    }
    assert connection.messages[-1] == {
        "type": "done",
        "final_output": "Inspection complete.",
        "raw": None,
    }
    assert connection.closed
