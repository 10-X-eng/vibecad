# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD preferences for VibeCAD.

Preferences intentionally store only non-secret settings. API keys are read
from the process environment, OS keyring, or a user-selected .env file by
VibeCADAuth.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import sys
import threading

import FreeCAD as App

from VibeCADAuth import (
    DEFAULT_GEMINI_API_BASE,
    DEFAULT_PROVIDER,
    PROVIDERS,
    delete_keyring_key,
    list_provider_models,
    resolve_auth_credential,
    resolve_auth_state,
    store_keyring_key,
    validate_api_key,
    validate_configured_auth,
)
from VibeCADGrokAuth import DEFAULT_XAI_API_BASE
from VibeCADDebug import default_capture_directory, resolve_capture_directory
from VibeCADPromptStarters import (
    BUILTIN_PROMPT_STARTERS,
    CATEGORY_ORDER,
    PromptStarter,
    create_custom_prompt_starter,
    load_custom_prompt_starters,
    prompt_starters_path,
    save_custom_prompt_starters,
)

PREFERENCE_GROUP = "User parameter:BaseApp/Preferences/Mod/VibeCAD"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_CHATGPT_MODEL = ""
DEFAULT_GROK_MODEL = "grok-4.6"
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
DEFAULT_MODELS = {
    "openai": DEFAULT_MODEL,
    "anthropic": DEFAULT_ANTHROPIC_MODEL,
    "chatgpt": DEFAULT_CHATGPT_MODEL,
    "grok": DEFAULT_GROK_MODEL,
    "gemini": DEFAULT_GEMINI_MODEL,
}
REASONING_EFFORTS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)
GEMINI_REASONING_EFFORTS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
)
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_SCRIPTED_TIMEOUT_SECONDS = 300.0
DEFAULT_SCRIPTED_MEMORY_LIMIT_MB = 6144
NEW_DOCUMENT_AUTHORING_MODES = ("ask", "native", "vibescript")
DEFAULT_NEW_DOCUMENT_AUTHORING_MODE = "ask"


def normalize_provider(value: str | None) -> str:
    clean = (value or "").strip().lower()
    return clean if clean in PROVIDERS else DEFAULT_PROVIDER


def reasoning_efforts_for_provider(provider: str | None) -> tuple[str, ...]:
    if normalize_provider(provider) == "gemini":
        return GEMINI_REASONING_EFFORTS
    return REASONING_EFFORTS


def normalize_new_document_authoring_mode(value: str | None) -> str:
    clean = str(value or "").strip().lower()
    return (
        clean
        if clean in NEW_DOCUMENT_AUTHORING_MODES
        else DEFAULT_NEW_DOCUMENT_AUTHORING_MODE
    )


@dataclass(frozen=True)
class VibeCADSettings:
    use_online_provider: bool = True
    model: str = DEFAULT_MODEL
    dotenv_path: str = ""
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    provider: str = DEFAULT_PROVIDER
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    chatgpt_model: str = DEFAULT_CHATGPT_MODEL
    grok_model: str = DEFAULT_GROK_MODEL
    gemini_model: str = DEFAULT_GEMINI_MODEL
    web_search_enabled: bool = False
    design_review_enabled: bool = False
    codex_skills_enabled: bool = False
    openai_base_url: str = ""
    anthropic_base_url: str = ""
    intent_memory_enabled: bool = True
    openai_intent_memory_model: str = ""
    anthropic_intent_memory_model: str = ""
    chatgpt_intent_memory_model: str = ""
    grok_intent_memory_model: str = ""
    gemini_intent_memory_model: str = ""
    scripted_timeout_seconds: float = DEFAULT_SCRIPTED_TIMEOUT_SECONDS
    scripted_memory_limit_mb: int = DEFAULT_SCRIPTED_MEMORY_LIMIT_MB
    mcp_enabled: bool = False
    new_document_authoring_mode: str = DEFAULT_NEW_DOCUMENT_AUTHORING_MODE

    @property
    def resolved_dotenv_path(self) -> Path | None:
        if not self.dotenv_path:
            return None
        return Path(self.dotenv_path).expanduser()

    @property
    def active_model(self) -> str:
        """Model for the selected provider."""
        provider = normalize_provider(self.provider)
        if provider == "anthropic":
            return self.anthropic_model.strip() or DEFAULT_ANTHROPIC_MODEL
        if provider == "chatgpt":
            return self.chatgpt_model.strip()
        if provider == "grok":
            return self.grok_model.strip() or DEFAULT_GROK_MODEL
        if provider == "gemini":
            return self.gemini_model.strip() or DEFAULT_GEMINI_MODEL
        return self.model.strip() or DEFAULT_MODEL

    @property
    def active_base_url(self) -> str | None:
        """Base URL override for the selected provider; None means official endpoint."""
        provider = normalize_provider(self.provider)
        if provider == "chatgpt":
            return None
        if provider == "grok":
            return DEFAULT_XAI_API_BASE
        if provider == "gemini":
            return DEFAULT_GEMINI_API_BASE
        if provider == "anthropic":
            override = self.anthropic_base_url.strip()
        else:
            override = self.openai_base_url.strip()
        return override or None

    def base_url_for(self, provider: str) -> str | None:
        """Base URL override for ``provider``; None means official endpoint."""
        clean_provider = normalize_provider(provider)
        if clean_provider == "chatgpt":
            return None
        if clean_provider == "grok":
            return DEFAULT_XAI_API_BASE
        if clean_provider == "gemini":
            return DEFAULT_GEMINI_API_BASE
        if clean_provider == "anthropic":
            override = self.anthropic_base_url.strip()
        else:
            override = self.openai_base_url.strip()
        return override or None

    def model_for(self, provider: str) -> str:
        """Configured interactive model for ``provider``."""
        clean_provider = normalize_provider(provider)
        if clean_provider == "anthropic":
            return self.anthropic_model.strip() or DEFAULT_ANTHROPIC_MODEL
        if clean_provider == "chatgpt":
            return self.chatgpt_model.strip()
        if clean_provider == "grok":
            return self.grok_model.strip() or DEFAULT_GROK_MODEL
        if clean_provider == "gemini":
            return self.gemini_model.strip() or DEFAULT_GEMINI_MODEL
        return self.model.strip() or DEFAULT_MODEL

    def intent_memory_model_for(self, provider: str) -> str:
        """Intent compiler model, defaulting explicitly to the interactive model."""
        clean_provider = normalize_provider(provider)
        if clean_provider == "anthropic":
            override = self.anthropic_intent_memory_model.strip()
        elif clean_provider == "chatgpt":
            override = self.chatgpt_intent_memory_model.strip()
        elif clean_provider == "grok":
            override = self.grok_intent_memory_model.strip()
        elif clean_provider == "gemini":
            override = self.gemini_intent_memory_model.strip()
        else:
            override = self.openai_intent_memory_model.strip()
        return override or self.model_for(provider)


@dataclass(frozen=True)
class VibeCADDebugSettings:
    context_debug_enabled: bool = False
    capture_directory: str = ""

    @property
    def resolved_capture_directory(self) -> Path:
        return resolve_capture_directory(self.capture_directory)


def preferences():
    return App.ParamGet(PREFERENCE_GROUP)


def normalize_reasoning_effort(value: str | None) -> str:
    clean = (value or "").strip().lower()
    return clean if clean in REASONING_EFFORTS else DEFAULT_REASONING_EFFORT


def _positive_float(value: object, default: float) -> float:
    try:
        clean = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return clean if clean > 0 else default


def _positive_int(value: object, default: int) -> int:
    try:
        clean = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default
    return clean if clean > 0 else default


def load_settings() -> VibeCADSettings:
    pref = preferences()
    return VibeCADSettings(
        new_document_authoring_mode=normalize_new_document_authoring_mode(
            pref.GetString(
                "NewDocumentAuthoringMode",
                DEFAULT_NEW_DOCUMENT_AUTHORING_MODE,
            )
        ),
        mcp_enabled=pref.GetBool("MCPEnabled", False),
        use_online_provider=pref.GetBool("UseOnlineProvider", True),
        model=pref.GetString("Model", DEFAULT_MODEL) or DEFAULT_MODEL,
        dotenv_path=pref.GetString("DotenvPath", ""),
        reasoning_effort=normalize_reasoning_effort(
            pref.GetString("ReasoningEffort", DEFAULT_REASONING_EFFORT)
        ),
        provider=normalize_provider(pref.GetString("Provider", DEFAULT_PROVIDER)),
        anthropic_model=pref.GetString("AnthropicModel", DEFAULT_ANTHROPIC_MODEL)
        or DEFAULT_ANTHROPIC_MODEL,
        chatgpt_model=pref.GetString("ChatGPTModel", DEFAULT_CHATGPT_MODEL),
        grok_model=pref.GetString("GrokModel", DEFAULT_GROK_MODEL) or DEFAULT_GROK_MODEL,
        gemini_model=pref.GetString("GeminiModel", DEFAULT_GEMINI_MODEL)
        or DEFAULT_GEMINI_MODEL,
        web_search_enabled=pref.GetBool("WebSearchEnabled", False),
        design_review_enabled=pref.GetBool("DesignReviewEnabled", False),
        codex_skills_enabled=pref.GetBool("CodexSkillsEnabled", False),
        openai_base_url=pref.GetString("OpenAIBaseUrl", ""),
        anthropic_base_url=pref.GetString("AnthropicBaseUrl", ""),
        intent_memory_enabled=pref.GetBool("IntentMemoryEnabled", True),
        openai_intent_memory_model=pref.GetString("OpenAIIntentMemoryModel", ""),
        anthropic_intent_memory_model=pref.GetString("AnthropicIntentMemoryModel", ""),
        chatgpt_intent_memory_model=pref.GetString("ChatGPTIntentMemoryModel", ""),
        grok_intent_memory_model=pref.GetString("GrokIntentMemoryModel", ""),
        gemini_intent_memory_model=pref.GetString("GeminiIntentMemoryModel", ""),
        scripted_timeout_seconds=_positive_float(
            pref.GetFloat("ScriptedTimeoutSeconds", DEFAULT_SCRIPTED_TIMEOUT_SECONDS),
            DEFAULT_SCRIPTED_TIMEOUT_SECONDS,
        ),
        scripted_memory_limit_mb=_positive_int(
            pref.GetInt("ScriptedMemoryLimitMB", DEFAULT_SCRIPTED_MEMORY_LIMIT_MB),
            DEFAULT_SCRIPTED_MEMORY_LIMIT_MB,
        ),
    )


def load_debug_settings() -> VibeCADDebugSettings:
    pref = preferences()
    return VibeCADDebugSettings(
        context_debug_enabled=pref.GetBool("ContextDebugEnabled", False),
        capture_directory=pref.GetString("ContextDebugDirectory", ""),
    )


def save_settings(settings: VibeCADSettings) -> None:
    pref = preferences()
    pref.SetString(
        "NewDocumentAuthoringMode",
        normalize_new_document_authoring_mode(
            settings.new_document_authoring_mode
        ),
    )
    pref.SetBool("MCPEnabled", bool(settings.mcp_enabled))
    pref.SetBool("UseOnlineProvider", bool(settings.use_online_provider))
    pref.SetString("Model", settings.model.strip() or DEFAULT_MODEL)
    pref.SetString("DotenvPath", settings.dotenv_path.strip())
    pref.SetString(
        "ReasoningEffort", normalize_reasoning_effort(settings.reasoning_effort)
    )
    pref.SetString("Provider", normalize_provider(settings.provider))
    pref.SetString(
        "AnthropicModel", settings.anthropic_model.strip() or DEFAULT_ANTHROPIC_MODEL
    )
    pref.SetString("ChatGPTModel", settings.chatgpt_model.strip())
    pref.SetString("GrokModel", settings.grok_model.strip() or DEFAULT_GROK_MODEL)
    pref.SetString(
        "GeminiModel", settings.gemini_model.strip() or DEFAULT_GEMINI_MODEL
    )
    pref.SetBool("WebSearchEnabled", bool(settings.web_search_enabled))
    pref.SetBool("DesignReviewEnabled", bool(settings.design_review_enabled))
    pref.SetBool("CodexSkillsEnabled", bool(settings.codex_skills_enabled))
    pref.SetString("OpenAIBaseUrl", settings.openai_base_url.strip())
    pref.SetString("AnthropicBaseUrl", settings.anthropic_base_url.strip())
    pref.SetBool("IntentMemoryEnabled", bool(settings.intent_memory_enabled))
    pref.SetString(
        "OpenAIIntentMemoryModel", settings.openai_intent_memory_model.strip()
    )
    pref.SetString(
        "AnthropicIntentMemoryModel", settings.anthropic_intent_memory_model.strip()
    )
    pref.SetString(
        "ChatGPTIntentMemoryModel", settings.chatgpt_intent_memory_model.strip()
    )
    pref.SetString(
        "GrokIntentMemoryModel", settings.grok_intent_memory_model.strip()
    )
    pref.SetString(
        "GeminiIntentMemoryModel", settings.gemini_intent_memory_model.strip()
    )
    pref.SetFloat(
        "ScriptedTimeoutSeconds",
        _positive_float(
            settings.scripted_timeout_seconds, DEFAULT_SCRIPTED_TIMEOUT_SECONDS
        ),
    )
    pref.SetInt(
        "ScriptedMemoryLimitMB",
        _positive_int(
            settings.scripted_memory_limit_mb, DEFAULT_SCRIPTED_MEMORY_LIMIT_MB
        ),
    )


def save_debug_settings(settings: VibeCADDebugSettings) -> None:
    pref = preferences()
    pref.SetBool("ContextDebugEnabled", bool(settings.context_debug_enabled))
    pref.SetString("ContextDebugDirectory", settings.capture_directory.strip())


def reset_settings() -> None:
    pref = preferences()
    pref.RemString("NewDocumentAuthoringMode")
    pref.RemBool("MCPEnabled")
    pref.RemBool("UseOnlineProvider")
    pref.RemString("Model")
    pref.RemString("DotenvPath")
    pref.RemString("ReasoningEffort")
    pref.RemString("Provider")
    pref.RemString("AnthropicModel")
    pref.RemString("ChatGPTModel")
    pref.RemString("GrokModel")
    pref.RemString("GeminiModel")
    pref.RemBool("WebSearchEnabled")
    pref.RemBool("DesignReviewEnabled")
    pref.RemBool("CodexSkillsEnabled")
    pref.RemString("OpenAIBaseUrl")
    pref.RemString("AnthropicBaseUrl")
    pref.RemBool("IntentMemoryEnabled")
    pref.RemString("OpenAIIntentMemoryModel")
    pref.RemString("AnthropicIntentMemoryModel")
    pref.RemString("ChatGPTIntentMemoryModel")
    pref.RemString("GrokIntentMemoryModel")
    pref.RemString("GeminiIntentMemoryModel")
    pref.RemFloat("ScriptedTimeoutSeconds")
    pref.RemInt("ScriptedMemoryLimitMB")
    pref.RemBool("ContextDebugEnabled")
    pref.RemString("ContextDebugDirectory")


def set_mcp_enabled(enabled: bool) -> None:
    """Persist a controller rollback without changing unrelated preferences."""

    preferences().SetBool("MCPEnabled", bool(enabled))


def configured_dotenv_path() -> Path | None:
    return load_settings().resolved_dotenv_path


def fetch_models_for_provider(
    provider: str,
    dotenv_path: Path | None = None,
    base_url: str | None = None,
) -> dict:
    """Resolve the configured key for ``provider`` and query its models endpoint.

    Returns the ``list_provider_models`` payload:
    {"ok": bool, "models": [str, ...], "error": str | None}.
    """
    clean_provider = normalize_provider(provider)
    if clean_provider in {"chatgpt", "grok"}:
        return list_provider_models(None, provider=clean_provider)
    credential = resolve_auth_credential(
        dotenv_path=dotenv_path, provider=clean_provider
    )
    if credential is None:
        display = PROVIDERS[clean_provider].display_name
        return {
            "ok": False,
            "models": [],
            "error": f"No {display} API key is configured.",
        }
    result = list_provider_models(
        credential.value, provider=clean_provider, base_url=base_url
    )
    if clean_provider == "openai" and result.get("ok") and base_url:
        from VibeCADOllama import inspect_models

        ollama = inspect_models(str(base_url), list(result.get("models") or []))
        if ollama.get("detected"):
            result["provider_runtime"] = "ollama"
            result["model_details"] = dict(ollama.get("models") or {})
    return result


class VibeCADPreferencesPage:
    def __init__(self, parent=None):
        from PySide import QtCore, QtWidgets

        self.form = QtWidgets.QWidget(parent)
        self.form.setObjectName("VibeCADPreferencesPage")
        self.form.setWindowTitle("VibeCAD")
        layout = QtWidgets.QFormLayout(self.form)
        self._layout = layout
        self._chatgpt_login_session = None
        self._chatgpt_task_active = False
        self._chatgpt_model_details: dict[str, dict] = {}
        self._chatgpt_default_model = ""
        self._grok_login_session = None
        self._oauth_task_provider = ""
        self._grok_bot_connection: dict[str, str] | None = None

        class _AsyncBridge(QtCore.QObject):
            event = QtCore.Signal(str, object)
            finished = QtCore.Signal(str, object)

        self._async_bridge = _AsyncBridge(self.form)
        self._async_bridge.event.connect(self._chatgpt_task_event)
        self._async_bridge.finished.connect(self._chatgpt_task_finished)

        self.new_document_authoring_mode = QtWidgets.QComboBox(self.form)
        self.new_document_authoring_mode.setObjectName(
            "VibeCADPrefNewDocumentAuthoringMode"
        )
        self.new_document_authoring_mode.addItem("Ask each time", "ask")
        self.new_document_authoring_mode.addItem("Native", "native")
        self.new_document_authoring_mode.addItem("VibeScript", "vibescript")
        self.new_document_authoring_mode.setToolTip(
            "Choose how genuinely new documents begin. Existing documents always "
            "use the authoring authority stored in the CAD file."
        )
        layout.addRow("New document authoring", self.new_document_authoring_mode)

        self.use_online = QtWidgets.QCheckBox(self.form)
        self.use_online.setObjectName("VibeCADPrefUseOnlineProvider")
        layout.addRow("Use online provider", self.use_online)

        self.provider = QtWidgets.QComboBox(self.form)
        self.provider.setObjectName("VibeCADPrefProvider")
        for provider_id in sorted(PROVIDERS):
            self.provider.addItem(PROVIDERS[provider_id].display_name, provider_id)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        layout.addRow("Provider", self.provider)

        self.model = QtWidgets.QComboBox(self.form)
        self.model.setObjectName("VibeCADPrefModel")
        self.model.setEditable(True)
        layout.addRow("OpenAI model", self.model)

        self.anthropic_model = QtWidgets.QComboBox(self.form)
        self.anthropic_model.setObjectName("VibeCADPrefAnthropicModel")
        self.anthropic_model.setEditable(True)
        layout.addRow("Anthropic model", self.anthropic_model)

        self.chatgpt_model = QtWidgets.QComboBox(self.form)
        self.chatgpt_model.setObjectName("VibeCADPrefChatGPTModel")
        self.chatgpt_model.addItem("Use account default", "")
        self.chatgpt_model.currentIndexChanged.connect(self._chatgpt_model_changed)
        layout.addRow("ChatGPT model", self.chatgpt_model)

        self.grok_model = QtWidgets.QComboBox(self.form)
        self.grok_model.setObjectName("VibeCADPrefGrokModel")
        self.grok_model.setEditable(True)
        layout.addRow("Grok model", self.grok_model)

        self.gemini_model = QtWidgets.QComboBox(self.form)
        self.gemini_model.setObjectName("VibeCADPrefGeminiModel")
        self.gemini_model.setEditable(True)
        layout.addRow("Gemini model", self.gemini_model)

        self.web_search_enabled = QtWidgets.QCheckBox(self.form)
        self.web_search_enabled.setObjectName("VibeCADPrefWebSearchEnabled")
        self.web_search_enabled.setToolTip(
            "Allow the selected provider to use its hosted web-search tool for "
            "current engineering facts and sources. Compatible custom endpoints "
            "must implement the same server-side tool."
        )
        layout.addRow("Web research", self.web_search_enabled)

        self.design_review_enabled = QtWidgets.QCheckBox(self.form)
        self.design_review_enabled.setObjectName(
            "VibeCADPrefDesignReviewEnabled"
        )
        self.design_review_enabled.setToolTip(
            "Give the CAD agent one read-only tool that sends a written design "
            "draft to an isolated reviewer before substantial new construction."
        )
        layout.addRow("Independent design review", self.design_review_enabled)

        self.codex_skills_enabled = QtWidgets.QCheckBox(self.form)
        self.codex_skills_enabled.setObjectName("VibeCADPrefCodexSkillsEnabled")
        self.codex_skills_enabled.setToolTip(
            "Expose enabled Codex skills through one scoped, read-only skill "
            "resource tool. Shell and general filesystem access remain disabled."
        )
        layout.addRow("Codex skills", self.codex_skills_enabled)

        self.openai_base_url = QtWidgets.QLineEdit(self.form)
        self.openai_base_url.setObjectName("VibeCADPrefOpenAIBaseUrl")
        self.openai_base_url.setPlaceholderText("https://api.openai.com/v1")
        self.openai_base_url.setToolTip(
            "Override the OpenAI API endpoint (include the /v1 segment). "
            "Leave blank to use the official endpoint. Use this to point at "
            "a local server that implements the OpenAI API."
        )
        layout.addRow("OpenAI base URL", self.openai_base_url)

        self.anthropic_base_url = QtWidgets.QLineEdit(self.form)
        self.anthropic_base_url.setObjectName("VibeCADPrefAnthropicBaseUrl")
        self.anthropic_base_url.setPlaceholderText("https://api.anthropic.com")
        self.anthropic_base_url.setToolTip(
            "Override the Anthropic API endpoint (without the /v1 segment). "
            "Leave blank to use the official endpoint."
        )
        layout.addRow("Anthropic base URL", self.anthropic_base_url)

        self.fetch_models = QtWidgets.QPushButton("Fetch models", self.form)
        self.fetch_models.setObjectName("VibeCADPrefFetchModels")
        self.fetch_models.clicked.connect(self._fetch_models)
        layout.addRow("", self.fetch_models)

        self.reasoning_effort = QtWidgets.QComboBox(self.form)
        self.reasoning_effort.setObjectName("VibeCADPrefReasoningEffort")
        self.reasoning_effort.addItems(REASONING_EFFORTS)
        layout.addRow("Reasoning effort", self.reasoning_effort)

        self.intent_memory_enabled = QtWidgets.QCheckBox(self.form)
        self.intent_memory_enabled.setObjectName("VibeCADPrefIntentMemoryEnabled")
        self.intent_memory_enabled.setToolTip(
            "Compile durable project intent after completed conversations so long "
            "sessions stay coherent without replaying the entire chat."
        )
        layout.addRow("Intent Memory", self.intent_memory_enabled)

        self.openai_intent_memory_model = QtWidgets.QComboBox(self.form)
        self.openai_intent_memory_model.setObjectName(
            "VibeCADPrefOpenAIIntentMemoryModel"
        )
        self.openai_intent_memory_model.addItem("Use active OpenAI model", "")
        layout.addRow("OpenAI memory model", self.openai_intent_memory_model)

        self.anthropic_intent_memory_model = QtWidgets.QComboBox(self.form)
        self.anthropic_intent_memory_model.setObjectName(
            "VibeCADPrefAnthropicIntentMemoryModel"
        )
        self.anthropic_intent_memory_model.addItem("Use active Anthropic model", "")
        layout.addRow("Anthropic memory model", self.anthropic_intent_memory_model)

        self.chatgpt_intent_memory_model = QtWidgets.QComboBox(self.form)
        self.chatgpt_intent_memory_model.setObjectName(
            "VibeCADPrefChatGPTIntentMemoryModel"
        )
        self.chatgpt_intent_memory_model.addItem("Use active ChatGPT model", "")
        layout.addRow("ChatGPT memory model", self.chatgpt_intent_memory_model)

        self.grok_intent_memory_model = QtWidgets.QComboBox(self.form)
        self.grok_intent_memory_model.setObjectName(
            "VibeCADPrefGrokIntentMemoryModel"
        )
        self.grok_intent_memory_model.addItem("Use active Grok model", "")
        layout.addRow("Grok memory model", self.grok_intent_memory_model)

        self.gemini_intent_memory_model = QtWidgets.QComboBox(self.form)
        self.gemini_intent_memory_model.setObjectName(
            "VibeCADPrefGeminiIntentMemoryModel"
        )
        self.gemini_intent_memory_model.addItem("Use active Gemini model", "")
        layout.addRow("Gemini memory model", self.gemini_intent_memory_model)

        self.rebuild_intent_memory = QtWidgets.QPushButton(
            "Rebuild Intent Memory", self.form
        )
        self.rebuild_intent_memory.setObjectName("VibeCADPrefRebuildIntentMemory")
        self.rebuild_intent_memory.clicked.connect(self._rebuild_intent_memory)
        layout.addRow("", self.rebuild_intent_memory)

        self.intent_memory_status = QtWidgets.QLabel(self.form)
        self.intent_memory_status.setObjectName("VibeCADPrefIntentMemoryStatus")
        self.intent_memory_status.setWordWrap(True)
        layout.addRow("Memory status", self.intent_memory_status)

        self.dotenv_row = QtWidgets.QWidget(self.form)
        dotenv_row = QtWidgets.QHBoxLayout(self.dotenv_row)
        dotenv_row.setContentsMargins(0, 0, 0, 0)
        self.dotenv_path = QtWidgets.QLineEdit(self.form)
        self.dotenv_path.setObjectName("VibeCADPrefDotenvPath")
        browse = QtWidgets.QPushButton("Browse", self.form)
        browse.setObjectName("VibeCADPrefBrowseDotenv")
        browse.clicked.connect(self._browse_dotenv)
        dotenv_row.addWidget(self.dotenv_path, 1)
        dotenv_row.addWidget(browse)
        layout.addRow(".env path", self.dotenv_row)

        self.api_key_row = QtWidgets.QWidget(self.form)
        api_key_row = QtWidgets.QHBoxLayout(self.api_key_row)
        api_key_row.setContentsMargins(0, 0, 0, 0)
        self.api_key = QtWidgets.QLineEdit(self.form)
        self.api_key.setObjectName("VibeCADPrefApiKey")
        self.api_key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key.setPlaceholderText("Paste an API key for the selected provider")
        save_key = QtWidgets.QPushButton("Save Key", self.form)
        save_key.setObjectName("VibeCADPrefSaveApiKey")
        save_key.clicked.connect(self._save_api_key)
        self.api_logout = QtWidgets.QPushButton("Logout", self.form)
        self.api_logout.setObjectName("VibeCADPrefLogout")
        self.api_logout.clicked.connect(self._logout)
        validate = QtWidgets.QPushButton("Validate", self.form)
        validate.setObjectName("VibeCADPrefValidateAuth")
        validate.clicked.connect(self._validate_auth)
        api_key_row.addWidget(self.api_key, 1)
        api_key_row.addWidget(save_key)
        api_key_row.addWidget(validate)
        api_key_row.addWidget(self.api_logout)
        layout.addRow("API key", self.api_key_row)

        self.chatgpt_auth_row = QtWidgets.QWidget(self.form)
        chatgpt_auth_layout = QtWidgets.QHBoxLayout(self.chatgpt_auth_row)
        chatgpt_auth_layout.setContentsMargins(0, 0, 0, 0)
        self.chatgpt_sign_in = QtWidgets.QPushButton("Sign in with ChatGPT", self.form)
        self.chatgpt_sign_in.setObjectName("VibeCADPrefChatGPTSignIn")
        self.chatgpt_sign_in.clicked.connect(
            lambda: self._start_chatgpt_login("browser")
        )
        self.chatgpt_device_sign_in = QtWidgets.QPushButton(
            "Use device code", self.form
        )
        self.chatgpt_device_sign_in.setObjectName("VibeCADPrefChatGPTDeviceSignIn")
        self.chatgpt_device_sign_in.clicked.connect(
            lambda: self._start_chatgpt_login("device")
        )
        self.chatgpt_cancel_sign_in = QtWidgets.QPushButton("Cancel", self.form)
        self.chatgpt_cancel_sign_in.setObjectName("VibeCADPrefChatGPTCancelSignIn")
        self.chatgpt_cancel_sign_in.setEnabled(False)
        self.chatgpt_cancel_sign_in.clicked.connect(self._cancel_chatgpt_login)
        self.chatgpt_logout = QtWidgets.QPushButton("Logout", self.form)
        self.chatgpt_logout.setObjectName("VibeCADPrefChatGPTLogout")
        self.chatgpt_logout.clicked.connect(self._chatgpt_logout)
        chatgpt_auth_layout.addWidget(self.chatgpt_sign_in)
        chatgpt_auth_layout.addWidget(self.chatgpt_device_sign_in)
        chatgpt_auth_layout.addWidget(self.chatgpt_cancel_sign_in)
        chatgpt_auth_layout.addWidget(self.chatgpt_logout)
        layout.addRow("ChatGPT account", self.chatgpt_auth_row)

        self.grok_auth_row = QtWidgets.QWidget(self.form)
        grok_auth_layout = QtWidgets.QHBoxLayout(self.grok_auth_row)
        grok_auth_layout.setContentsMargins(0, 0, 0, 0)
        self.grok_sign_in = QtWidgets.QPushButton("Sign in with X / Grok", self.form)
        self.grok_sign_in.setObjectName("VibeCADPrefGrokSignIn")
        self.grok_sign_in.clicked.connect(lambda: self._start_grok_login("browser"))
        self.grok_device_sign_in = QtWidgets.QPushButton("Use device code", self.form)
        self.grok_device_sign_in.setObjectName("VibeCADPrefGrokDeviceSignIn")
        self.grok_device_sign_in.clicked.connect(
            lambda: self._start_grok_login("device")
        )
        self.grok_cancel_sign_in = QtWidgets.QPushButton("Cancel", self.form)
        self.grok_cancel_sign_in.setObjectName("VibeCADPrefGrokCancelSignIn")
        self.grok_cancel_sign_in.setEnabled(False)
        self.grok_cancel_sign_in.clicked.connect(self._cancel_grok_login)
        self.grok_logout = QtWidgets.QPushButton("Logout", self.form)
        self.grok_logout.setObjectName("VibeCADPrefGrokLogout")
        self.grok_logout.clicked.connect(self._grok_logout)
        grok_auth_layout.addWidget(self.grok_sign_in)
        grok_auth_layout.addWidget(self.grok_device_sign_in)
        grok_auth_layout.addWidget(self.grok_cancel_sign_in)
        grok_auth_layout.addWidget(self.grok_logout)
        layout.addRow("Grok account", self.grok_auth_row)

        self.grok_bot_row = QtWidgets.QWidget(self.form)
        grok_bot_layout = QtWidgets.QHBoxLayout(self.grok_bot_row)
        grok_bot_layout.setContentsMargins(0, 0, 0, 0)
        self.grok_bot_connect = QtWidgets.QPushButton("Connect Grok Bot", self.form)
        self.grok_bot_connect.setObjectName("VibeCADPrefGrokBotConnect")
        self.grok_bot_connect.setToolTip(
            "Start the local control channel so Grok Bot can drive VibeCAD on "
            "this machine. Loopback only (127.0.0.1) and token protected."
        )
        self.grok_bot_connect.clicked.connect(self._connect_grok_bot)
        self.grok_bot_copy = QtWidgets.QPushButton("Copy connection", self.form)
        self.grok_bot_copy.setObjectName("VibeCADPrefGrokBotCopy")
        self.grok_bot_copy.setToolTip(
            "Copy the loopback endpoint and access token so Grok Bot can connect."
        )
        self.grok_bot_copy.setEnabled(False)
        self.grok_bot_copy.clicked.connect(self._copy_grok_bot_connection)
        grok_bot_layout.addWidget(self.grok_bot_connect)
        grok_bot_layout.addWidget(self.grok_bot_copy)
        layout.addRow("Grok Bot", self.grok_bot_row)

        self.grok_bot_app_row = QtWidgets.QWidget(self.form)
        grok_bot_app_layout = QtWidgets.QHBoxLayout(self.grok_bot_app_row)
        grok_bot_app_layout.setContentsMargins(0, 0, 0, 0)
        self.grok_bot_command = QtWidgets.QLineEdit(self.form)
        self.grok_bot_command.setObjectName("VibeCADPrefGrokBotCommand")
        self.grok_bot_command.setPlaceholderText(
            "Grok Bot app path (optional; auto-detected if left blank)"
        )
        self.grok_bot_command.setText(preferences().GetString("GrokBotCommand", ""))
        grok_bot_browse = QtWidgets.QPushButton("Browse", self.form)
        grok_bot_browse.setObjectName("VibeCADPrefGrokBotBrowse")
        grok_bot_browse.clicked.connect(self._browse_grok_bot_command)
        grok_bot_app_layout.addWidget(self.grok_bot_command)
        grok_bot_app_layout.addWidget(grok_bot_browse)
        layout.addRow("Grok Bot app", self.grok_bot_app_row)

        self.grok_bot_status = QtWidgets.QLabel(
            "not_connected | Click Connect Grok Bot to let Grok Bot control "
            "this machine from Grok.",
            self.form,
        )
        self.grok_bot_status.setObjectName("VibeCADPrefGrokBotStatus")
        self.grok_bot_status.setWordWrap(True)
        self.grok_bot_status.setTextInteractionFlags(
            QtCore.Qt.TextSelectableByMouse
        )
        layout.addRow("", self.grok_bot_status)

        self.status = QtWidgets.QLabel(self.form)
        self.status.setObjectName("VibeCADPrefAuthStatus")
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addRow("Auth status", self.status)

        refresh = QtWidgets.QPushButton("Refresh", self.form)
        refresh.setObjectName("VibeCADPrefRefreshAuth")
        refresh.clicked.connect(self._refresh_status)
        layout.addRow("", refresh)

        self._control_mode_status_timer = QtCore.QTimer(self.form)
        self._control_mode_status_timer.setInterval(500)
        self._control_mode_status_timer.timeout.connect(
            self._refresh_control_mode_availability
        )
        self._control_mode_status_timer.start()
        self._refresh_control_mode_availability()

    def _refresh_control_mode_availability(self) -> None:
        try:
            from VibeCADMCP import get_control_mode_controller

            snapshot = get_control_mode_controller().snapshot()
        except Exception:
            return
        self.rebuild_intent_memory.setEnabled(
            bool(snapshot.get("internal_agent_enabled"))
        )

    def _browse_dotenv(self) -> None:
        from PySide import QtWidgets

        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self.form,
            "Select .env file",
            self.dotenv_path.text() or str(Path.home()),
            "Environment files (*.env);;All files (*)",
        )
        if selected:
            self.dotenv_path.setText(selected)
            self._refresh_status()

    def _selected_provider(self) -> str:
        data = self.provider.currentData()
        return normalize_provider(data if isinstance(data, str) else None)

    def _set_form_row_visible(self, field, visible: bool) -> None:
        field.setVisible(bool(visible))
        label = self._layout.labelForField(field)
        if label is not None:
            label.setVisible(bool(visible))

    def _update_provider_visibility(self) -> None:
        provider = self._selected_provider()
        self._set_form_row_visible(self.model, provider == "openai")
        self._set_form_row_visible(self.anthropic_model, provider == "anthropic")
        self._set_form_row_visible(self.chatgpt_model, provider == "chatgpt")
        self._set_form_row_visible(self.grok_model, provider == "grok")
        self._set_form_row_visible(self.gemini_model, provider == "gemini")
        self._set_form_row_visible(self.web_search_enabled, provider != "gemini")
        self._set_form_row_visible(self.design_review_enabled, True)
        self._set_form_row_visible(self.codex_skills_enabled, provider == "chatgpt")
        self._set_form_row_visible(self.openai_base_url, provider == "openai")
        self._set_form_row_visible(self.anthropic_base_url, provider == "anthropic")
        self._set_form_row_visible(
            self.openai_intent_memory_model, provider == "openai"
        )
        self._set_form_row_visible(
            self.anthropic_intent_memory_model, provider == "anthropic"
        )
        self._set_form_row_visible(
            self.chatgpt_intent_memory_model, provider == "chatgpt"
        )
        self._set_form_row_visible(
            self.grok_intent_memory_model, provider == "grok"
        )
        self._set_form_row_visible(
            self.gemini_intent_memory_model, provider == "gemini"
        )
        api_key_provider = provider in {"openai", "anthropic", "gemini"}
        self._set_form_row_visible(self.dotenv_row, api_key_provider)
        self._set_form_row_visible(self.api_key_row, api_key_provider)
        self._set_form_row_visible(self.chatgpt_auth_row, provider == "chatgpt")
        self._set_form_row_visible(self.grok_auth_row, provider == "grok")
        self._refresh_reasoning_efforts()

    def _chatgpt_model_changed(self, _index: int = 0) -> None:
        if self._selected_provider() == "chatgpt":
            self._refresh_reasoning_efforts()

    def _refresh_reasoning_efforts(self) -> None:
        provider = self._selected_provider()
        current = self.reasoning_effort.currentText().strip()
        allowed = list(reasoning_efforts_for_provider(provider))
        preferred = current or DEFAULT_REASONING_EFFORT
        if provider == "chatgpt":
            model_id = str(self.chatgpt_model.currentData() or "").strip()
            effective_model = model_id or self._chatgpt_default_model
            detail = self._chatgpt_model_details.get(effective_model, {})
            advertised = [
                str(value)
                for value in detail.get("supported_reasoning_efforts") or []
                if str(value)
            ]
            if advertised:
                allowed = advertised
                preferred = str(
                    detail.get("default_reasoning_effort") or DEFAULT_REASONING_EFFORT
                )
        self.reasoning_effort.blockSignals(True)
        try:
            self.reasoning_effort.clear()
            self.reasoning_effort.addItems(allowed)
            selected = current if current in allowed else preferred
            index = self.reasoning_effort.findText(selected)
            self.reasoning_effort.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.reasoning_effort.blockSignals(False)
        if provider == "chatgpt" and current and current not in allowed:
            self.status.setText(
                f"reasoning_adjusted | {current} is unavailable for this model; "
                f"using {self.reasoning_effort.currentText()}."
            )

    def _provider_changed(self, _index: int = 0) -> None:
        self.api_key.clear()
        self._update_provider_visibility()
        self._refresh_status()

    def _set_chatgpt_task_controls(self, task: str = "") -> None:
        busy = bool(task)
        login_busy = task == "login"
        self.chatgpt_sign_in.setEnabled(not busy)
        self.chatgpt_device_sign_in.setEnabled(not busy)
        self.chatgpt_logout.setEnabled(not busy)
        self.chatgpt_cancel_sign_in.setEnabled(
            login_busy and self._oauth_task_provider == "chatgpt"
        )
        self.grok_sign_in.setEnabled(not busy)
        self.grok_device_sign_in.setEnabled(not busy)
        self.grok_logout.setEnabled(not busy)
        self.grok_cancel_sign_in.setEnabled(
            login_busy and self._oauth_task_provider == "grok"
        )
        self.fetch_models.setEnabled(not busy)

    def _run_chatgpt_task(self, task: str, operation) -> bool:
        if self._chatgpt_task_active:
            self.status.setText(
                "busy | An account operation is already running."
            )
            return False
        self._chatgpt_task_active = True
        self._chatgpt_task_name = task
        self._set_chatgpt_task_controls(task)

        def worker() -> None:
            try:
                result = operation()
                payload = {"ok": True, "result": result}
            except Exception as exc:
                payload = {"ok": False, "error": str(exc)}
            self._async_bridge.finished.emit(task, payload)

        threading.Thread(
            target=worker,
            name=f"VibeCAD-ChatGPT-{task}",
            daemon=True,
        ).start()
        return True

    def _chatgpt_account_status(self, result: object) -> str:
        payload = result if isinstance(result, dict) else {}
        account = payload.get("account") if isinstance(payload, dict) else None
        if not isinstance(account, dict) or account.get("type") != "chatgpt":
            return "not_configured | No ChatGPT subscription account is signed in."
        plan = str(account.get("planType") or "subscription")
        email = str(account.get("email") or "").strip()
        suffix = f" | {email}" if email else ""
        return f"verified | ChatGPT {plan}{suffix}"

    def _chatgpt_task_event(self, event: str, payload: object) -> None:
        if event != "login_started" or not isinstance(payload, dict):
            return
        from PySide import QtCore, QtGui

        login_type = str(payload.get("type") or "")
        if login_type in {"chatgpt", "grok"}:
            url = str(payload.get("authUrl") or "")
            if url:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))
            label = "ChatGPT" if login_type == "chatgpt" else "X / Grok"
            self.status.setText(
                f"sign_in_pending | Complete {label} sign-in in your browser."
            )
            return
        url = str(
            payload.get("verificationUrlComplete")
            or payload.get("verificationUrl")
            or ""
        )
        code = str(payload.get("userCode") or "")
        if url:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))
        self.status.setText(
            f"sign_in_pending | Open {url} and enter device code {code}."
        )

    def _chatgpt_task_finished(self, task: str, payload: object) -> None:
        self._chatgpt_task_active = False
        self._chatgpt_task_name = ""
        self._set_chatgpt_task_controls()
        clean = payload if isinstance(payload, dict) else {}
        if not clean.get("ok"):
            self.status.setText(f"auth_error | {clean.get('error') or 'Unknown error'}")
            self._chatgpt_login_session = None
            self._grok_login_session = None
            self._oauth_task_provider = ""
            return
        result = clean.get("result")
        if task == "models":
            model_result = result if isinstance(result, dict) else {}
            if not model_result.get("ok"):
                self.status.setText(
                    f"models_error | {model_result.get('error') or 'Unknown error'}"
                )
                self._oauth_task_provider = ""
                return
            provider = self._oauth_task_provider or self._selected_provider()
            if provider == "chatgpt":
                self._chatgpt_model_details = {
                    str(item.get("id")): dict(item)
                    for item in model_result.get("model_details") or []
                    if isinstance(item, dict) and item.get("id")
                }
                self._chatgpt_default_model = str(
                    model_result.get("default_model") or ""
                )
            self._apply_provider_models(
                provider, list(model_result.get("models") or [])
            )
            self._refresh_reasoning_efforts()
            self._oauth_task_provider = ""
            return
        if task == "logout":
            if self._oauth_task_provider == "grok":
                self.status.setText("not_configured | Grok / X account signed out.")
            else:
                self.status.setText("not_configured | ChatGPT account signed out.")
            self._oauth_task_provider = ""
            return
        if self._oauth_task_provider == "grok":
            self.status.setText(self._grok_account_status(result))
            self._grok_login_session = None
        else:
            self.status.setText(self._chatgpt_account_status(result))
            self._chatgpt_login_session = None
        self._oauth_task_provider = ""

    def _start_chatgpt_login(self, mode: str) -> None:
        if self._selected_provider() != "chatgpt":
            return
        from VibeCADCodex import ChatGPTLoginSession

        session = ChatGPTLoginSession()
        self._chatgpt_login_session = session
        self._oauth_task_provider = "chatgpt"

        def operation():
            try:
                started = session.start(mode)
                self._async_bridge.event.emit("login_started", started)
                return session.wait()
            finally:
                session.close()

        self.status.setText("sign_in_starting | Starting secure ChatGPT sign-in...")
        if not self._run_chatgpt_task("login", operation):
            session.close()
            self._chatgpt_login_session = None

    def _cancel_chatgpt_login(self) -> None:
        session = self._chatgpt_login_session
        if session is not None:
            session.request_cancel()
            self.status.setText("sign_in_cancelling | Cancelling ChatGPT sign-in...")

    def _chatgpt_logout(self) -> None:
        from VibeCADCodex import logout_account

        self._oauth_task_provider = "chatgpt"
        self.status.setText("sign_out_pending | Signing out of ChatGPT...")
        self._run_chatgpt_task("logout", logout_account)

    def _grok_account_status(self, result: object) -> str:
        payload = result if isinstance(result, dict) else {}
        account = payload.get("account") if isinstance(payload, dict) else None
        if not isinstance(account, dict) or account.get("type") != "grok":
            return "not_configured | No Grok / X account is signed in."
        email = str(account.get("email") or "").strip()
        suffix = f" | {email}" if email else ""
        return f"verified | Grok / X account is signed in{suffix}"

    def _start_grok_login(self, mode: str) -> None:
        if self._selected_provider() != "grok":
            return
        from VibeCADGrokAuth import GrokLoginSession

        session = GrokLoginSession()
        self._grok_login_session = session
        self._oauth_task_provider = "grok"

        def operation():
            try:
                started = session.start(mode)
                self._async_bridge.event.emit("login_started", started)
                return session.wait()
            finally:
                session.close()

        self.status.setText("sign_in_starting | Starting secure Grok / X sign-in...")
        if not self._run_chatgpt_task("login", operation):
            session.close()
            self._grok_login_session = None
            self._oauth_task_provider = ""

    def _cancel_grok_login(self) -> None:
        session = self._grok_login_session
        if session is not None:
            session.request_cancel()
            self.status.setText("sign_in_cancelling | Cancelling Grok / X sign-in...")

    def _grok_logout(self) -> None:
        from VibeCADGrokAuth import logout_account

        self._oauth_task_provider = "grok"
        self.status.setText("sign_out_pending | Signing out of Grok / X...")
        self._run_chatgpt_task("logout", logout_account)

    def _connect_grok_bot(self) -> None:
        """Start the local control channel so Grok Bot can drive this machine.

        VibeCAD already exposes a loopback control channel (see
        ``docs/vibecad-agent-control.md``). This one-click action ensures that
        server is running and surfaces the endpoint and access token a Grok Bot
        agent needs to connect. Sign-in and any model calls stay in the normal
        Grok provider flow; this only opens the local automation socket.
        """

        from PySide import QtWidgets

        try:
            import VibeCADAgentControl
        except Exception as exc:  # pragma: no cover - defensive import guard
            self._grok_bot_connection = None
            self.grok_bot_copy.setEnabled(False)
            self.grok_bot_status.setText(
                f"error | VibeCAD agent control is unavailable: {exc}"
            )
            return

        dispatch = None
        try:
            import VibeCADGui

            dispatch = getattr(VibeCADGui, "_dispatch_to_document_thread", None)
        except Exception:
            dispatch = None

        try:
            if dispatch is not None:
                VibeCADAgentControl.ensure_server_started(
                    document_thread_dispatch=dispatch
                )
            else:
                VibeCADAgentControl.ensure_server_started()
            token = VibeCADAgentControl.load_or_create_token()
            snapshot = VibeCADAgentControl.server_snapshot()
        except Exception as exc:
            self._grok_bot_connection = None
            self.grok_bot_copy.setEnabled(False)
            self.grok_bot_status.setText(
                f"error | Could not start the Grok Bot control channel: {exc}"
            )
            return

        base_url = snapshot.get("base_url") or (
            f"http://{snapshot.get('host')}:{snapshot.get('port')}"
        )
        token_path = snapshot.get("token_path", "")
        try:
            endpoint_path = str(VibeCADAgentControl.endpoint_path())
        except Exception:
            endpoint_path = ""

        if not (snapshot.get("running") and token):
            self._grok_bot_connection = None
            self.grok_bot_copy.setEnabled(False)
            self.grok_bot_status.setText(
                "error | The Grok Bot control channel did not start."
            )
            return

        # Write the AGENTS.md brief Grok Bot reads to learn how to drive VibeCAD.
        brief_path = ""
        try:
            brief_path = str(VibeCADAgentControl.write_agent_brief())
        except Exception as exc:
            self.grok_bot_status.setText(
                f"warning | Control channel is up but the brief could not be "
                f"written: {exc}"
            )

        self._grok_bot_connection = {
            "base_url": base_url,
            "token": token,
            "token_path": token_path,
            "endpoint_path": endpoint_path,
            "brief_path": brief_path,
        }
        self.grok_bot_copy.setEnabled(True)

        # Persist any explicit Grok Bot path, then try to launch it.
        explicit = self.grok_bot_command.text().strip()
        preferences().SetString("GrokBotCommand", explicit)
        launched = False
        launch_target = ""
        launch_error = ""
        try:
            command = VibeCADAgentControl.detect_grok_bot_command(explicit)
            if command:
                launch_target = command
                launched = self._launch_grok_bot(command, brief_path, base_url)
        except Exception as exc:  # pragma: no cover - defensive launch guard
            launch_error = str(exc)

        if launched:
            self.grok_bot_status.setText(
                f"connected | Launched Grok Bot ({launch_target}). It can control "
                f"this machine at {base_url}."
            )
            launch_line = f"Launched Grok Bot:\n{launch_target}\n\n"
        else:
            self.grok_bot_status.setText(
                f"connected | Grok Bot can control this machine at {base_url}. "
                "Grok Bot app was not found — set its path above or use Copy "
                "connection."
            )
            if launch_error:
                launch_line = f"Could not launch Grok Bot: {launch_error}\n\n"
            else:
                launch_line = (
                    "Grok Bot app was not found automatically. Set its path in "
                    "the 'Grok Bot app' field, or point Grok Bot at the brief "
                    "below.\n\n"
                )

        QtWidgets.QMessageBox.information(
            self.form,
            "Grok Bot",
            "This machine is ready for Grok Bot.\n\n"
            f"{launch_line}"
            f"Endpoint: {base_url}\n"
            f"Token file: {token_path}\n"
            f"Instructions (AGENTS.md): {brief_path or '(unavailable)'}\n\n"
            "The channel is loopback only (127.0.0.1) and Grok Bot must send "
            "the token. Use Copy connection to paste the details into Grok Bot.",
        )

    def _launch_grok_bot(
        self, command: str, brief_path: str, base_url: str
    ) -> bool:
        """Start the external Grok Bot app, pointed at the VibeCAD brief."""

        import subprocess

        environment = dict(os.environ)
        if brief_path:
            environment["VIBECAD_AGENTS_FILE"] = brief_path
        environment["VIBECAD_AGENT_ENDPOINT"] = base_url
        args = [command]
        if brief_path:
            args.append(brief_path)
        creationflags = 0
        if sys.platform == "win32":
            creationflags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        subprocess.Popen(  # noqa: S603 - user-configured/auto-detected launcher
            args,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(sys.platform != "win32"),
            creationflags=creationflags,
        )
        return True

    def _browse_grok_bot_command(self) -> None:
        from PySide import QtWidgets

        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self.form,
            "Select the Grok Bot application",
            self.grok_bot_command.text() or str(Path.home()),
            "Executables (*);;All files (*)",
        )
        if selected:
            self.grok_bot_command.setText(selected)
            preferences().SetString("GrokBotCommand", selected.strip())

    def _copy_grok_bot_connection(self) -> None:
        from PySide import QtWidgets

        connection = getattr(self, "_grok_bot_connection", None)
        if not connection:
            self.grok_bot_status.setText(
                "not_connected | Click Connect Grok Bot first."
            )
            return
        text = (
            "VibeCAD agent control for Grok Bot\n"
            f"base_url: {connection.get('base_url', '')}\n"
            f"token: {connection.get('token', '')}\n"
            f"token_path: {connection.get('token_path', '')}\n"
            f"endpoint_path: {connection.get('endpoint_path', '')}\n"
            f"brief_path: {connection.get('brief_path', '')}\n"
        )
        QtWidgets.QApplication.clipboard().setText(text)
        self.grok_bot_status.setText(
            "copied | Grok Bot connection details copied to the clipboard."
        )

    def _set_combo_text(self, combo, text: str) -> None:
        index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(text)

    def _memory_model_value(self, combo) -> str:
        data = combo.currentData()
        return str(data if data is not None else combo.currentText()).strip()

    def _set_memory_models(
        self,
        combo,
        models: list[str],
        current: str,
        active_label: str,
    ) -> None:
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(active_label, "")
            for model_name in models:
                combo.addItem(model_name, model_name)
            if current:
                index = combo.findData(current)
                if index < 0:
                    combo.addItem(current, current)
                    index = combo.count() - 1
                combo.setCurrentIndex(index)
            else:
                combo.setCurrentIndex(0)
        finally:
            combo.blockSignals(False)

    def _provider_model_combo(self, provider: str):
        if provider == "anthropic":
            return self.anthropic_model
        if provider == "chatgpt":
            return self.chatgpt_model
        if provider == "grok":
            return self.grok_model
        if provider == "gemini":
            return self.gemini_model
        return self.model

    def _provider_memory_combo(self, provider: str):
        if provider == "anthropic":
            return self.anthropic_intent_memory_model
        if provider == "chatgpt":
            return self.chatgpt_intent_memory_model
        if provider == "grok":
            return self.grok_intent_memory_model
        if provider == "gemini":
            return self.gemini_intent_memory_model
        return self.openai_intent_memory_model

    def _provider_active_memory_label(self, provider: str) -> str:
        if provider == "anthropic":
            return "Use active Anthropic model"
        if provider == "chatgpt":
            return "Use active ChatGPT model"
        if provider == "grok":
            return "Use active Grok model"
        if provider == "gemini":
            return "Use active Gemini model"
        return "Use active OpenAI model"

    def _apply_provider_models(
        self,
        provider: str,
        models: list[str],
        model_details: dict[str, dict] | None = None,
    ) -> None:
        combo = self._provider_model_combo(provider)
        current = (
            str(combo.currentData() or "").strip()
            if provider == "chatgpt"
            else combo.currentText().strip()
        )
        combo.blockSignals(True)
        try:
            combo.clear()
            if provider == "chatgpt":
                combo.addItem("Use account default", "")
                for model_name in models:
                    combo.addItem(model_name, model_name)
                index = combo.findData(current)
                combo.setCurrentIndex(index if index >= 0 else 0)
            else:
                combo.addItems(models)
                if current:
                    self._set_combo_text(combo, current)
        finally:
            combo.blockSignals(False)
        memory_combo = self._provider_memory_combo(provider)
        memory_current = self._memory_model_value(memory_combo)
        self._set_memory_models(
            memory_combo,
            models,
            memory_current,
            self._provider_active_memory_label(provider),
        )
        display = PROVIDERS[provider].display_name
        selected_model = (
            str(combo.currentData() or "").strip()
            if provider == "chatgpt"
            else combo.currentText().strip()
        )
        selected_details = dict((model_details or {}).get(selected_model) or {})
        runtime_context = int(
            selected_details.get("runtime_context_length") or 0
        )
        supported_context = int(
            selected_details.get("supported_context_length") or 0
        )
        context_status = ""
        if runtime_context > 0:
            context_status = f" | allocated context {runtime_context:,}"
        elif supported_context > 0:
            context_status = f" | supports context {supported_context:,}"
        self.status.setText(
            f"models_ok | {display} | {len(models)} models{context_status}"
        )

    def _fetch_models(self) -> None:
        provider = self._selected_provider()
        if provider == "chatgpt":
            from VibeCADCodex import list_models

            self._oauth_task_provider = "chatgpt"
            self.status.setText(
                "models_pending | Loading ChatGPT subscription models..."
            )
            self._run_chatgpt_task("models", list_models)
            return
        if provider == "grok":
            from VibeCADGrokAuth import list_models

            self._oauth_task_provider = "grok"
            self.status.setText("models_pending | Loading Grok models...")
            self._run_chatgpt_task("models", list_models)
            return
        settings = self._current_settings()
        result = fetch_models_for_provider(
            provider,
            dotenv_path=settings.resolved_dotenv_path,
            base_url=settings.base_url_for(provider),
        )
        if not result["ok"]:
            self.status.setText(f"models_error | {result['error']}")
            return
        self._apply_provider_models(
            provider,
            list(result["models"]),
            dict(result.get("model_details") or {}),
        )

    def _save_api_key(self) -> None:
        result = store_keyring_key(
            self.api_key.text(), provider=self._selected_provider()
        )
        self.api_key.clear()
        if not result["stored"]:
            self.status.setText(f"not_configured | {result['error']}")
            return
        self._refresh_status()

    def _rebuild_intent_memory(self) -> None:
        save_settings(self._current_settings())
        if not self.intent_memory_enabled.isChecked():
            self.intent_memory_status.setText(
                "Enable Intent Memory before rebuilding it."
            )
            return
        try:
            import VibeCADGui

            result = VibeCADGui.rebuild_intent_memory_async()
        except Exception as exc:
            self.intent_memory_status.setText(str(exc))
            return
        if result.get("started"):
            self.intent_memory_status.setText(
                "Rebuild started. Progress is shown in the VibeCAD panel."
            )
        else:
            self.intent_memory_status.setText(
                str(result.get("error") or "Not started.")
            )

    def _logout(self) -> None:
        if self._selected_provider() == "chatgpt":
            self._chatgpt_logout()
            return
        if self._selected_provider() == "grok":
            self._grok_logout()
            return
        delete_keyring_key(provider=self._selected_provider())
        self.api_key.clear()
        self.intent_memory_status.clear()
        self._refresh_status()

    def _validate_auth(self) -> None:
        provider = self._selected_provider()
        if provider == "chatgpt":
            self._refresh_chatgpt_status()
            return
        if provider == "grok":
            self._refresh_grok_status()
            return
        typed_key = self.api_key.text().strip()
        settings = self._current_settings()
        base_url = settings.base_url_for(provider)
        if typed_key:
            auth = validate_api_key(
                typed_key,
                provider=provider,
                source="unsaved API key",
                base_url=base_url,
            )
            self.api_key.clear()
        else:
            auth = validate_configured_auth(
                provider=provider,
                dotenv_path=settings.resolved_dotenv_path,
                base_url=base_url,
            )
        source = f" | {auth.source}" if auth.source else ""
        key = f" | {auth.redacted_key}" if auth.redacted_key else ""
        message = f" | {auth.message}" if auth.message else ""
        self.status.setText(f"{auth.status.value}{source}{key}{message}")

    def _current_settings(self) -> VibeCADSettings:
        persisted = load_settings()
        return VibeCADSettings(
            new_document_authoring_mode=normalize_new_document_authoring_mode(
                self.new_document_authoring_mode.currentData()
            ),
            mcp_enabled=persisted.mcp_enabled,
            use_online_provider=self.use_online.isChecked(),
            model=self.model.currentText().strip() or DEFAULT_MODEL,
            dotenv_path=self.dotenv_path.text().strip(),
            reasoning_effort=normalize_reasoning_effort(
                self.reasoning_effort.currentText()
            ),
            provider=self._selected_provider(),
            anthropic_model=self.anthropic_model.currentText().strip()
            or DEFAULT_ANTHROPIC_MODEL,
            chatgpt_model=str(self.chatgpt_model.currentData() or "").strip(),
            grok_model=self.grok_model.currentText().strip() or DEFAULT_GROK_MODEL,
            gemini_model=self.gemini_model.currentText().strip()
            or DEFAULT_GEMINI_MODEL,
            web_search_enabled=self.web_search_enabled.isChecked(),
            design_review_enabled=self.design_review_enabled.isChecked(),
            codex_skills_enabled=self.codex_skills_enabled.isChecked(),
            openai_base_url=self.openai_base_url.text().strip(),
            anthropic_base_url=self.anthropic_base_url.text().strip(),
            intent_memory_enabled=self.intent_memory_enabled.isChecked(),
            openai_intent_memory_model=self._memory_model_value(
                self.openai_intent_memory_model
            ),
            anthropic_intent_memory_model=(
                self._memory_model_value(self.anthropic_intent_memory_model)
            ),
            chatgpt_intent_memory_model=(
                self._memory_model_value(self.chatgpt_intent_memory_model)
            ),
            grok_intent_memory_model=(
                self._memory_model_value(self.grok_intent_memory_model)
            ),
            gemini_intent_memory_model=(
                self._memory_model_value(self.gemini_intent_memory_model)
            ),
            scripted_timeout_seconds=persisted.scripted_timeout_seconds,
            scripted_memory_limit_mb=persisted.scripted_memory_limit_mb,
        )

    def _refresh_status(self) -> None:
        if self._selected_provider() == "chatgpt":
            self._refresh_chatgpt_status()
            return
        if self._selected_provider() == "grok":
            self._refresh_grok_status()
            return
        settings = self._current_settings()
        auth = resolve_auth_state(
            dotenv_path=settings.resolved_dotenv_path,
            provider=self._selected_provider(),
        )
        source = f" | {auth.source}" if auth.source else ""
        key = f" | {auth.redacted_key}" if auth.redacted_key else ""
        self.status.setText(f"{auth.status.value}{source}{key}")

    def _refresh_chatgpt_status(self) -> None:
        if self._chatgpt_task_active:
            return
        from VibeCADCodex import read_account

        self._oauth_task_provider = "chatgpt"
        self.status.setText("checking | Checking ChatGPT subscription sign-in...")
        self._run_chatgpt_task("status", lambda: read_account(refresh_token=False))

    def _refresh_grok_status(self) -> None:
        if self._chatgpt_task_active:
            return
        from VibeCADGrokAuth import read_account

        self._oauth_task_provider = "grok"
        self.status.setText("checking | Checking Grok / X sign-in...")
        self._run_chatgpt_task("status", lambda: read_account(refresh_token=True))

    def saveSettings(self) -> None:
        save_settings(self._current_settings())
        preferences().SetString(
            "GrokBotCommand", self.grok_bot_command.text().strip()
        )
        try:
            import VibeCADGui

            VibeCADGui.apply_modeling_preferences()
        except Exception as exc:
            App.Console.PrintWarning(
                f"VibeCAD modeling preference update failed: {exc}\n"
            )

    def loadSettings(self) -> None:
        settings = load_settings()
        authoring_index = self.new_document_authoring_mode.findData(
            settings.new_document_authoring_mode
        )
        self.new_document_authoring_mode.setCurrentIndex(
            authoring_index if authoring_index >= 0 else 0
        )
        self.use_online.setChecked(settings.use_online_provider)
        provider_index = self.provider.findData(normalize_provider(settings.provider))
        self.provider.setCurrentIndex(provider_index if provider_index >= 0 else 0)
        self._set_combo_text(self.model, settings.model)
        self._set_combo_text(self.anthropic_model, settings.anthropic_model)
        if settings.chatgpt_model:
            index = self.chatgpt_model.findData(settings.chatgpt_model)
            if index < 0:
                self.chatgpt_model.addItem(
                    settings.chatgpt_model, settings.chatgpt_model
                )
                index = self.chatgpt_model.count() - 1
            self.chatgpt_model.setCurrentIndex(index)
        else:
            self.chatgpt_model.setCurrentIndex(0)
        self._set_combo_text(self.grok_model, settings.grok_model)
        self._set_combo_text(self.gemini_model, settings.gemini_model)
        self.web_search_enabled.setChecked(settings.web_search_enabled)
        self.design_review_enabled.setChecked(settings.design_review_enabled)
        self.codex_skills_enabled.setChecked(settings.codex_skills_enabled)
        index = self.reasoning_effort.findText(settings.reasoning_effort)
        self.reasoning_effort.setCurrentIndex(index if index >= 0 else 0)
        self.dotenv_path.setText(settings.dotenv_path)
        self.openai_base_url.setText(settings.openai_base_url)
        self.anthropic_base_url.setText(settings.anthropic_base_url)
        self.intent_memory_enabled.setChecked(settings.intent_memory_enabled)
        self._set_memory_models(
            self.openai_intent_memory_model,
            [],
            settings.openai_intent_memory_model,
            "Use active OpenAI model",
        )
        self._set_memory_models(
            self.anthropic_intent_memory_model,
            [],
            settings.anthropic_intent_memory_model,
            "Use active Anthropic model",
        )
        self._set_memory_models(
            self.chatgpt_intent_memory_model,
            [],
            settings.chatgpt_intent_memory_model,
            "Use active ChatGPT model",
        )
        self._set_memory_models(
            self.grok_intent_memory_model,
            [],
            settings.grok_intent_memory_model,
            "Use active Grok model",
        )
        self._set_memory_models(
            self.gemini_intent_memory_model,
            [],
            settings.gemini_intent_memory_model,
            "Use active Gemini model",
        )
        self.api_key.clear()
        self._update_provider_visibility()
        self._refresh_status()


class VibeCADMCPPreferencesPage:
    """Preferences and live connection details for external MCP control."""

    def __init__(self, parent=None):
        from PySide import QtCore, QtWidgets

        self.form = QtWidgets.QWidget(parent)
        self.form.setObjectName("VibeCADMCPPreferencesPage")
        self.form.setWindowTitle("MCP")
        layout = QtWidgets.QFormLayout(self.form)

        self.mcp_enabled = QtWidgets.QCheckBox(self.form)
        self.mcp_enabled.setObjectName("VibeCADPrefMCPEnabled")
        self.mcp_enabled.setToolTip(
            "Let an external MCP client control VibeCAD. The built-in agent is "
            "disabled until MCP control is turned off."
        )
        layout.addRow("External MCP control", self.mcp_enabled)

        self.mcp_state = QtWidgets.QLabel(self.form)
        self.mcp_state.setObjectName("VibeCADPrefMCPState")
        layout.addRow("MCP state", self.mcp_state)

        self.mcp_transport = QtWidgets.QLabel(self.form)
        self.mcp_transport.setObjectName("VibeCADPrefMCPTransport")
        self.mcp_transport.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addRow("MCP transport", self.mcp_transport)

        self.mcp_connection = QtWidgets.QLabel(self.form)
        self.mcp_connection.setObjectName("VibeCADPrefMCPConnection")
        layout.addRow("MCP connection", self.mcp_connection)

        self.mcp_error = QtWidgets.QLabel(self.form)
        self.mcp_error.setObjectName("VibeCADPrefMCPError")
        self.mcp_error.setWordWrap(True)
        self.mcp_error.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addRow("MCP last error", self.mcp_error)

        self.copy_mcp_configuration = QtWidgets.QPushButton(
            "Copy connection JSON", self.form
        )
        self.copy_mcp_configuration.setObjectName(
            "VibeCADPrefCopyMCPConfiguration"
        )
        self.copy_mcp_configuration.clicked.connect(
            self._copy_mcp_connection_configuration
        )
        layout.addRow("", self.copy_mcp_configuration)

        self._mcp_status_timer = QtCore.QTimer(self.form)
        self._mcp_status_timer.setInterval(500)
        self._mcp_status_timer.timeout.connect(self._refresh_mcp_status)
        self._mcp_status_timer.start()

        self._refresh_mcp_status()

    def _refresh_mcp_status(self) -> None:
        try:
            from VibeCADMCP import get_control_mode_controller

            snapshot = get_control_mode_controller().snapshot()
        except Exception as exc:
            snapshot = {
                "state": "unavailable",
                "transport": "stdio",
                "connection_state": "unavailable",
                "last_error": str(exc),
            }
        self.mcp_state.setText(str(snapshot.get("state") or "unknown"))
        self.mcp_transport.setText(str(snapshot.get("transport") or "stdio"))
        self.mcp_connection.setText(
            str(snapshot.get("connection_state") or "unknown")
        )
        self.mcp_error.setText(str(snapshot.get("last_error") or "None"))
        self.copy_mcp_configuration.setEnabled(
            snapshot.get("state") != "unavailable"
        )
        if (
            snapshot.get("connection_state") == "error"
            and not preferences().GetBool("MCPEnabled", False)
        ):
            self.mcp_enabled.setChecked(False)

    def _copy_mcp_connection_configuration(self) -> None:
        import json
        from PySide import QtWidgets
        from VibeCADMCP import get_control_mode_controller

        configuration = get_control_mode_controller().connection_configuration()
        QtWidgets.QApplication.clipboard().setText(
            json.dumps(configuration, indent=2, sort_keys=True)
        )

    def saveSettings(self) -> None:
        set_mcp_enabled(self.mcp_enabled.isChecked())
        try:
            import VibeCADGui

            VibeCADGui.apply_mcp_preferences()
        except Exception as exc:
            App.Console.PrintWarning(f"VibeCAD MCP preference update failed: {exc}\n")
        self._refresh_mcp_status()

    def loadSettings(self) -> None:
        self.mcp_enabled.setChecked(load_settings().mcp_enabled)
        self._refresh_mcp_status()


class VibeCADPromptStartersPreferencesPage:
    """Global prompt-starter library management."""

    _NEW_STARTER_CONTENT = """Outcome:
[describe what should be made or changed]

Driving requirements:
- Dimensions and units: [values]
- Interfaces and critical geometry: [details]
- Material and manufacturing process: [details]
- Loads, tolerances, and clearances: [details]
- Must preserve or avoid: [details]
- Completion criteria: [how the result should be verified]
"""

    def __init__(self, parent=None):
        from PySide import QtCore, QtWidgets

        self.form = QtWidgets.QWidget(parent)
        self.form.setObjectName("VibeCADPromptStartersPreferencesPage")
        self.form.setWindowTitle("Prompt Starters")
        layout = QtWidgets.QVBoxLayout(self.form)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self.form)
        splitter.setObjectName("VibeCADPrefPromptStarterSplitter")
        layout.addWidget(splitter, 1)

        self.tree = QtWidgets.QTreeWidget(splitter)
        self.tree.setObjectName("VibeCADPrefPromptStarterTree")
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(220)
        self.tree.setUniformRowHeights(True)
        self.tree.currentItemChanged.connect(self._selection_changed)
        splitter.addWidget(self.tree)

        editor = QtWidgets.QWidget(splitter)
        editor.setObjectName("VibeCADPrefPromptStarterEditor")
        editor_layout = QtWidgets.QFormLayout(editor)
        editor_layout.setContentsMargins(8, 0, 0, 0)
        editor_layout.setSpacing(8)

        self.source = QtWidgets.QLabel(editor)
        self.source.setObjectName("VibeCADPrefPromptStarterSource")
        editor_layout.addRow("Source", self.source)

        self.name = QtWidgets.QLineEdit(editor)
        self.name.setObjectName("VibeCADPrefPromptStarterName")
        self.name.setMaxLength(80)
        self.name.textChanged.connect(self._name_changed)
        editor_layout.addRow("Name", self.name)

        self.category = QtWidgets.QComboBox(editor)
        self.category.setObjectName("VibeCADPrefPromptStarterCategory")
        self.category.addItems(CATEGORY_ORDER)
        self.category.currentTextChanged.connect(self._category_changed)
        editor_layout.addRow("Category", self.category)

        self.content = QtWidgets.QPlainTextEdit(editor)
        self.content.setObjectName("VibeCADPrefPromptStarterContent")
        self.content.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.content.textChanged.connect(self._content_changed)
        editor_layout.addRow("Prompt", self.content)

        actions = QtWidgets.QWidget(editor)
        actions.setObjectName("VibeCADPrefPromptStarterActions")
        actions_layout = QtWidgets.QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(6)

        self.new_button = QtWidgets.QPushButton("New", actions)
        self.new_button.setObjectName("VibeCADPrefPromptStarterNew")
        self.new_button.clicked.connect(self._new_custom)
        actions_layout.addWidget(self.new_button)

        self.duplicate_button = QtWidgets.QPushButton("Duplicate", actions)
        self.duplicate_button.setObjectName("VibeCADPrefPromptStarterDuplicate")
        self.duplicate_button.clicked.connect(self._duplicate_selected)
        actions_layout.addWidget(self.duplicate_button)

        self.delete_button = QtWidgets.QPushButton("Delete", actions)
        self.delete_button.setObjectName("VibeCADPrefPromptStarterDelete")
        self.delete_button.clicked.connect(self._delete_selected)
        actions_layout.addWidget(self.delete_button)
        actions_layout.addStretch(1)
        editor_layout.addRow("", actions)

        splitter.addWidget(editor)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 520])

        self.status = QtWidgets.QLabel(self.form)
        self.status.setObjectName("VibeCADPrefPromptStarterStatus")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.status)

        item_data_role = getattr(QtCore.Qt, "ItemDataRole", QtCore.Qt)
        self._user_role = item_data_role.UserRole
        self._starters: dict[str, PromptStarter] = {}
        self._selected_id = ""
        self._loading_editor = False
        self._custom_load_error = ""

    def _selected_starter(self) -> PromptStarter | None:
        return self._starters.get(self._selected_id)

    def _tree_item_for(self, starter_id: str):
        from PySide import QtWidgets

        iterator = QtWidgets.QTreeWidgetItemIterator(self.tree)
        while iterator.value() is not None:
            item = iterator.value()
            if str(item.data(0, self._user_role) or "") == starter_id:
                return item
            iterator += 1
        return None

    def _reload_tree(self, selected_id: str = "") -> None:
        from PySide import QtWidgets

        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            selected_item = None
            first_item = None
            for category in CATEGORY_ORDER:
                starters = sorted(
                    (
                        starter
                        for starter in self._starters.values()
                        if starter.category == category
                    ),
                    key=lambda starter: (not starter.builtin, starter.name.casefold()),
                )
                if not starters:
                    continue
                group = QtWidgets.QTreeWidgetItem([category])
                group.setData(0, self._user_role, "")
                self.tree.addTopLevelItem(group)
                for starter in starters:
                    item = QtWidgets.QTreeWidgetItem([starter.name])
                    item.setData(0, self._user_role, starter.starter_id)
                    item.setToolTip(
                        0,
                        "Built in" if starter.builtin else "Custom prompt starter",
                    )
                    group.addChild(item)
                    if first_item is None:
                        first_item = item
                    if starter.starter_id == selected_id:
                        selected_item = item
                group.setExpanded(True)
            self.tree.setCurrentItem(selected_item or first_item)
        finally:
            self.tree.blockSignals(False)
        current = self.tree.currentItem()
        starter_id = (
            str(current.data(0, self._user_role) or "") if current is not None else ""
        )
        self._show_starter(starter_id)

    def _selection_changed(self, current, _previous) -> None:
        starter_id = (
            str(current.data(0, self._user_role) or "") if current is not None else ""
        )
        self._show_starter(starter_id)

    def _show_starter(self, starter_id: str) -> None:
        starter = self._starters.get(starter_id)
        self._selected_id = starter_id if starter is not None else ""
        self._loading_editor = True
        try:
            self.source.setText(
                "Built in (read only)"
                if starter is not None and starter.builtin
                else ("Custom" if starter is not None else "")
            )
            self.name.setText(starter.name if starter is not None else "")
            category_index = (
                self.category.findText(starter.category) if starter is not None else -1
            )
            self.category.setCurrentIndex(category_index if category_index >= 0 else 0)
            self.content.setPlainText(starter.content if starter is not None else "")
        finally:
            self._loading_editor = False

        editable = (
            starter is not None
            and not starter.builtin
            and not self._custom_load_error
        )
        self.name.setReadOnly(not editable)
        self.category.setEnabled(editable)
        self.content.setReadOnly(not editable)
        self.duplicate_button.setEnabled(
            starter is not None and not self._custom_load_error
        )
        self.delete_button.setEnabled(editable)

    def _replace_selected(self, *, name=None, category=None, content=None) -> None:
        starter = self._selected_starter()
        if starter is None or starter.builtin or self._loading_editor:
            return
        self._starters[starter.starter_id] = PromptStarter(
            starter_id=starter.starter_id,
            name=starter.name if name is None else name,
            category=starter.category if category is None else category,
            content=starter.content if content is None else content,
            builtin=False,
        )

    def _name_changed(self, text: str) -> None:
        self._replace_selected(name=text)
        item = self._tree_item_for(self._selected_id)
        if item is not None and not self._loading_editor:
            item.setText(0, text.strip() or "Untitled prompt starter")

    def _category_changed(self, category: str) -> None:
        if self._loading_editor or not self._selected_id:
            return
        starter = self._selected_starter()
        if starter is None or starter.builtin:
            return
        self._replace_selected(category=category)
        self._reload_tree(self._selected_id)

    def _content_changed(self) -> None:
        self._replace_selected(content=self.content.toPlainText())

    def _unique_name(self, base: str) -> str:
        existing = {starter.name.casefold() for starter in self._starters.values()}
        if base.casefold() not in existing:
            return base
        index = 2
        while f"{base} {index}".casefold() in existing:
            index += 1
        return f"{base} {index}"

    def _new_custom(self) -> None:
        if self._custom_load_error:
            return
        starter = create_custom_prompt_starter(
            name=self._unique_name("New prompt starter"),
            category="General",
            content=self._NEW_STARTER_CONTENT,
        )
        self._starters[starter.starter_id] = starter
        self._reload_tree(starter.starter_id)
        self.name.setFocus()
        self.name.selectAll()

    def _duplicate_selected(self) -> None:
        if self._custom_load_error:
            return
        source = self._selected_starter()
        if source is None:
            return
        starter = create_custom_prompt_starter(
            name=self._unique_name(f"Copy of {source.name}"),
            category=source.category,
            content=source.content,
        )
        self._starters[starter.starter_id] = starter
        self._reload_tree(starter.starter_id)
        self.name.setFocus()
        self.name.selectAll()

    def _delete_selected(self) -> None:
        starter = self._selected_starter()
        if starter is None or starter.builtin or self._custom_load_error:
            return
        del self._starters[starter.starter_id]
        self._reload_tree()

    def saveSettings(self) -> None:
        if self._custom_load_error:
            App.Console.PrintWarning(
                "VibeCAD prompt starters were not saved because the existing "
                f"library could not be loaded: {self._custom_load_error}\n"
            )
            return
        custom = [starter for starter in self._starters.values() if not starter.builtin]
        try:
            path = save_custom_prompt_starters(custom)
        except Exception as exc:
            self.status.setText(f"Not saved | {exc}")
            App.Console.PrintWarning(f"VibeCAD prompt starter save failed: {exc}\n")
            return
        self.status.setText(f"{len(custom)} custom | {path}")

    def loadSettings(self) -> None:
        self._custom_load_error = ""
        try:
            custom = load_custom_prompt_starters()
        except Exception as exc:
            custom = ()
            self._custom_load_error = str(exc)
        self._starters = {
            starter.starter_id: starter
            for starter in (*BUILTIN_PROMPT_STARTERS, *custom)
        }
        self.new_button.setEnabled(not self._custom_load_error)
        self._reload_tree(self._selected_id)
        if self._custom_load_error:
            self.status.setText(f"Custom library unavailable | {self._custom_load_error}")
        else:
            self.status.setText(f"{len(custom)} custom | {prompt_starters_path()}")


class VibeCADDebugPreferencesPage:
    """Preferences for the opt-in exact provider-request debugger."""

    def __init__(self, parent=None):
        from PySide import QtCore, QtWidgets

        self.form = QtWidgets.QWidget(parent)
        self.form.setObjectName("VibeCADDebugPreferencesPage")
        self.form.setWindowTitle("Debug")
        layout = QtWidgets.QFormLayout(self.form)

        self.enabled = QtWidgets.QCheckBox(self.form)
        self.enabled.setObjectName("VibeCADPrefContextDebugEnabled")
        self.enabled.setToolTip(
            "Capture every exact provider SDK request. Captures contain prompts, "
            "conversation history, tools, CAD context, and encoded images."
        )
        self.enabled.toggled.connect(self._enabled_changed)
        layout.addRow("Context debugger", self.enabled)

        directory_row = QtWidgets.QHBoxLayout()
        self.directory = QtWidgets.QLineEdit(self.form)
        self.directory.setObjectName("VibeCADPrefContextDebugDirectory")
        self.directory.setPlaceholderText(str(default_capture_directory()))
        self.directory.setToolTip(
            "Directory for timestamped JSON request captures. Leave blank to use "
            "the VibeCAD debug directory."
        )
        browse = QtWidgets.QPushButton("Browse", self.form)
        browse.setObjectName("VibeCADPrefBrowseContextDebugDirectory")
        browse.clicked.connect(self._browse_directory)
        directory_row.addWidget(self.directory, 1)
        directory_row.addWidget(browse)
        layout.addRow("Capture directory", directory_row)

        actions = QtWidgets.QHBoxLayout()
        self.open_viewer = QtWidgets.QPushButton("Open Viewer", self.form)
        self.open_viewer.setObjectName("VibeCADPrefOpenContextDebugViewer")
        self.open_viewer.clicked.connect(self._open_viewer)
        open_folder = QtWidgets.QPushButton("Open Folder", self.form)
        open_folder.setObjectName("VibeCADPrefOpenContextDebugFolder")
        open_folder.clicked.connect(self._open_folder)
        actions.addWidget(self.open_viewer)
        actions.addWidget(open_folder)
        actions.addStretch(1)
        layout.addRow("", actions)

        self.status = QtWidgets.QLabel(self.form)
        self.status.setObjectName("VibeCADPrefContextDebugStatus")
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.status.setWordWrap(True)
        layout.addRow("Capture status", self.status)

    def _settings(self) -> VibeCADDebugSettings:
        return VibeCADDebugSettings(
            context_debug_enabled=self.enabled.isChecked(),
            capture_directory=self.directory.text().strip(),
        )

    def _enabled_changed(self, enabled: bool) -> None:
        self.open_viewer.setEnabled(bool(enabled))
        self._refresh_status()

    def _refresh_status(self) -> None:
        settings = self._settings()
        state = "enabled" if settings.context_debug_enabled else "disabled"
        self.status.setText(f"{state} | {settings.resolved_capture_directory}")

    def _browse_directory(self) -> None:
        from PySide import QtWidgets

        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self.form,
            "Select provider request capture directory",
            str(self._settings().resolved_capture_directory),
        )
        if selected:
            self.directory.setText(selected)
            self._refresh_status()

    def _open_folder(self) -> None:
        from PySide import QtCore, QtGui

        directory = self._settings().resolved_capture_directory
        directory.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(directory)))

    def _open_viewer(self) -> None:
        if not self.enabled.isChecked():
            return
        save_debug_settings(self._settings())
        import VibeCADGui

        VibeCADGui.show_context_debugger()

    def saveSettings(self) -> None:
        save_debug_settings(self._settings())
        try:
            import VibeCADGui

            VibeCADGui.apply_context_debug_preferences()
        except Exception as exc:
            App.Console.PrintWarning(
                f"VibeCAD context debugger preference update failed: {exc}\n"
            )

    def loadSettings(self) -> None:
        settings = load_debug_settings()
        self.enabled.setChecked(settings.context_debug_enabled)
        self.directory.setText(settings.capture_directory)
        self._enabled_changed(settings.context_debug_enabled)
