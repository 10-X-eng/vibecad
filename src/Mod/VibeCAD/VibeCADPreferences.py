# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD preferences for VibeCAD.

Preferences intentionally store only non-secret settings. API keys are read
from the process environment, OS keyring, or a user-selected .env file by
VibeCADAuth.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

import FreeCAD as App

from VibeCADAuth import (
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
DEFAULT_MODELS = {
    "openai": DEFAULT_MODEL,
    "anthropic": DEFAULT_ANTHROPIC_MODEL,
    "chatgpt": DEFAULT_CHATGPT_MODEL,
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
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_SCRIPTED_TIMEOUT_SECONDS = 300.0
DEFAULT_SCRIPTED_MEMORY_LIMIT_MB = 6144


def normalize_provider(value: str | None) -> str:
    clean = (value or "").strip().lower()
    return clean if clean in PROVIDERS else DEFAULT_PROVIDER


@dataclass(frozen=True)
class VibeCADSettings:
    use_online_provider: bool = True
    model: str = DEFAULT_MODEL
    dotenv_path: str = ""
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    provider: str = DEFAULT_PROVIDER
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    chatgpt_model: str = DEFAULT_CHATGPT_MODEL
    web_search_enabled: bool = False
    design_review_enabled: bool = True
    codex_skills_enabled: bool = False
    openai_base_url: str = ""
    anthropic_base_url: str = ""
    intent_memory_enabled: bool = True
    openai_intent_memory_model: str = ""
    anthropic_intent_memory_model: str = ""
    chatgpt_intent_memory_model: str = ""
    build123d_enabled: bool = False
    openscad_enabled: bool = False
    vibescript_enabled: bool = True
    vibescript_on_bim_enabled: bool = False
    openscad_executable: str = ""
    openscad_library_paths: str = ""
    scripted_timeout_seconds: float = DEFAULT_SCRIPTED_TIMEOUT_SECONDS
    scripted_memory_limit_mb: int = DEFAULT_SCRIPTED_MEMORY_LIMIT_MB

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
        return self.model.strip() or DEFAULT_MODEL

    @property
    def active_base_url(self) -> str | None:
        """Base URL override for the selected provider; None means official endpoint."""
        provider = normalize_provider(self.provider)
        if provider == "chatgpt":
            return None
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
        return self.model.strip() or DEFAULT_MODEL

    def intent_memory_model_for(self, provider: str) -> str:
        """Intent compiler model, defaulting explicitly to the interactive model."""
        clean_provider = normalize_provider(provider)
        if clean_provider == "anthropic":
            override = self.anthropic_intent_memory_model.strip()
        elif clean_provider == "chatgpt":
            override = self.chatgpt_intent_memory_model.strip()
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
        web_search_enabled=pref.GetBool("WebSearchEnabled", False),
        design_review_enabled=pref.GetBool("DesignReviewEnabled", True),
        codex_skills_enabled=pref.GetBool("CodexSkillsEnabled", False),
        openai_base_url=pref.GetString("OpenAIBaseUrl", ""),
        anthropic_base_url=pref.GetString("AnthropicBaseUrl", ""),
        intent_memory_enabled=pref.GetBool("IntentMemoryEnabled", True),
        openai_intent_memory_model=pref.GetString("OpenAIIntentMemoryModel", ""),
        anthropic_intent_memory_model=pref.GetString("AnthropicIntentMemoryModel", ""),
        chatgpt_intent_memory_model=pref.GetString("ChatGPTIntentMemoryModel", ""),
        build123d_enabled=pref.GetBool("Build123dEnabled", False),
        openscad_enabled=pref.GetBool("OpenSCADEnabled", False),
        vibescript_enabled=pref.GetBool("VibeScriptEnabled", True),
        vibescript_on_bim_enabled=pref.GetBool("VibeScriptOnBIMEnabled", False),
        openscad_executable=pref.GetString("OpenSCADExecutable", ""),
        openscad_library_paths=pref.GetString("OpenSCADLibraryPaths", ""),
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
    pref.SetBool("Build123dEnabled", bool(settings.build123d_enabled))
    pref.SetBool("OpenSCADEnabled", bool(settings.openscad_enabled))
    pref.SetBool("VibeScriptEnabled", bool(settings.vibescript_enabled))
    pref.SetBool("VibeScriptOnBIMEnabled", bool(settings.vibescript_on_bim_enabled))
    pref.SetString("OpenSCADExecutable", settings.openscad_executable.strip())
    pref.SetString("OpenSCADLibraryPaths", settings.openscad_library_paths.strip())
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
    pref.RemBool("UseOnlineProvider")
    pref.RemString("Model")
    pref.RemString("DotenvPath")
    pref.RemString("ReasoningEffort")
    pref.RemString("Provider")
    pref.RemString("AnthropicModel")
    pref.RemString("ChatGPTModel")
    pref.RemBool("WebSearchEnabled")
    pref.RemBool("DesignReviewEnabled")
    pref.RemBool("CodexSkillsEnabled")
    pref.RemString("OpenAIBaseUrl")
    pref.RemString("AnthropicBaseUrl")
    pref.RemBool("IntentMemoryEnabled")
    pref.RemString("OpenAIIntentMemoryModel")
    pref.RemString("AnthropicIntentMemoryModel")
    pref.RemString("ChatGPTIntentMemoryModel")
    pref.RemBool("Build123dEnabled")
    pref.RemBool("OpenSCADEnabled")
    pref.RemBool("VibeScriptEnabled")
    pref.RemBool("VibeScriptOnBIMEnabled")
    pref.RemString("OpenSCADExecutable")
    pref.RemString("OpenSCADLibraryPaths")
    pref.RemFloat("ScriptedTimeoutSeconds")
    pref.RemInt("ScriptedMemoryLimitMB")
    pref.RemBool("ContextDebugEnabled")
    pref.RemString("ContextDebugDirectory")


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
    if clean_provider == "chatgpt":
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
    return list_provider_models(
        credential.value, provider=clean_provider, base_url=base_url
    )


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

        class _AsyncBridge(QtCore.QObject):
            event = QtCore.Signal(str, object)
            finished = QtCore.Signal(str, object)

        self._async_bridge = _AsyncBridge(self.form)
        self._async_bridge.event.connect(self._chatgpt_task_event)
        self._async_bridge.finished.connect(self._chatgpt_task_finished)

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

        self.build123d_enabled = QtWidgets.QCheckBox(self.form)
        self.build123d_enabled.setObjectName("VibeCADPrefBuild123dEnabled")
        self.build123d_enabled.setToolTip(
            "Make the isolated build123d modeling engine available in the "
            "PartDesign VibeCAD panel. Model-generated code never runs in "
            "FreeCAD's Python process."
        )
        self.build123d_enabled.toggled.connect(self._refresh_build123d_status)
        layout.addRow("Enable build123d", self.build123d_enabled)

        self.build123d_status = QtWidgets.QLabel(self.form)
        self.build123d_status.setObjectName("VibeCADPrefBuild123dStatus")
        self.build123d_status.setWordWrap(True)
        self.build123d_status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addRow("build123d status", self.build123d_status)

        self.openscad_enabled = QtWidgets.QCheckBox(self.form)
        self.openscad_enabled.setObjectName("VibeCADPrefOpenSCADEnabled")
        self.openscad_enabled.setToolTip(
            "Make the isolated OpenSCAD source engine available in PartDesign. "
            "Compilation and CSG conversion run outside the FreeCAD GUI process."
        )
        self.openscad_enabled.toggled.connect(self._refresh_openscad_status)
        layout.addRow("Enable OpenSCAD", self.openscad_enabled)

        openscad_executable_row = QtWidgets.QHBoxLayout()
        self.openscad_executable = QtWidgets.QLineEdit(self.form)
        self.openscad_executable.setObjectName("VibeCADPrefOpenSCADExecutable")
        self.openscad_executable.setPlaceholderText("Use bundled OpenSCAD")
        self.openscad_executable.setToolTip(
            "Optional explicit OpenSCAD CLI override. Leave blank to use the "
            "runtime bundled with VibeCAD. VibeCAD does not search PATH."
        )
        self.openscad_executable.textChanged.connect(self._refresh_openscad_status)
        browse_openscad = QtWidgets.QPushButton("Browse", self.form)
        browse_openscad.setObjectName("VibeCADPrefBrowseOpenSCADExecutable")
        browse_openscad.clicked.connect(self._browse_openscad_executable)
        openscad_executable_row.addWidget(self.openscad_executable, 1)
        openscad_executable_row.addWidget(browse_openscad)
        layout.addRow("OpenSCAD executable", openscad_executable_row)

        self.openscad_library_paths = QtWidgets.QPlainTextEdit(self.form)
        self.openscad_library_paths.setObjectName("VibeCADPrefOpenSCADLibraryPaths")
        self.openscad_library_paths.setPlaceholderText(
            "One additional OpenSCAD library directory per line"
        )
        self.openscad_library_paths.setMaximumHeight(72)
        self.openscad_library_paths.setToolTip(
            "Explicit user library directories. Project libraries and bundled "
            "BOSL2/MCAD are always available."
        )
        layout.addRow("OpenSCAD libraries", self.openscad_library_paths)

        self.openscad_status = QtWidgets.QLabel(self.form)
        self.openscad_status.setObjectName("VibeCADPrefOpenSCADStatus")
        self.openscad_status.setWordWrap(True)
        self.openscad_status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addRow("OpenSCAD status", self.openscad_status)

        self.vibescript_enabled = QtWidgets.QCheckBox(self.form)
        self.vibescript_enabled.setObjectName("VibeCADPrefVibeScriptEnabled")
        self.vibescript_enabled.setToolTip(
            "Make the VibeScript native-modeling engine available in PartDesign "
            "(enabled by default). Scripts run in-process against the live "
            "document inside a single transaction; no external runtime is "
            "required."
        )
        layout.addRow("Enable VibeScript", self.vibescript_enabled)

        self.vibescript_on_bim_enabled = QtWidgets.QCheckBox(self.form)
        self.vibescript_on_bim_enabled.setObjectName(
            "VibeCADPrefVibeScriptOnBIMEnabled"
        )
        self.vibescript_on_bim_enabled.setToolTip(
            "Also surface VibeScript tools in the BIM workbench when VibeScript "
            "is the selected Part Design engine. Disabled by default because BIM "
            "models buildings top-down through its own native tool pack."
        )
        layout.addRow("Enable VibeScript in BIM", self.vibescript_on_bim_enabled)

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

        self.status = QtWidgets.QLabel(self.form)
        self.status.setObjectName("VibeCADPrefAuthStatus")
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addRow("Auth status", self.status)

        refresh = QtWidgets.QPushButton("Refresh", self.form)
        refresh.setObjectName("VibeCADPrefRefreshAuth")
        refresh.clicked.connect(self._refresh_status)
        layout.addRow("", refresh)

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
        self._set_form_row_visible(self.web_search_enabled, True)
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
        api_key_provider = provider in {"openai", "anthropic"}
        self._set_form_row_visible(self.dotenv_row, api_key_provider)
        self._set_form_row_visible(self.api_key_row, api_key_provider)
        self._set_form_row_visible(self.chatgpt_auth_row, provider == "chatgpt")
        self._refresh_reasoning_efforts()

    def _chatgpt_model_changed(self, _index: int = 0) -> None:
        if self._selected_provider() == "chatgpt":
            self._refresh_reasoning_efforts()

    def _refresh_reasoning_efforts(self) -> None:
        provider = self._selected_provider()
        current = self.reasoning_effort.currentText().strip()
        allowed = list(REASONING_EFFORTS)
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
        self.chatgpt_cancel_sign_in.setEnabled(login_busy)
        self.fetch_models.setEnabled(not busy)

    def _run_chatgpt_task(self, task: str, operation) -> bool:
        if self._chatgpt_task_active:
            self.status.setText(
                "busy | A ChatGPT account operation is already running."
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

        if payload.get("type") == "chatgpt":
            url = str(payload.get("authUrl") or "")
            if url:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))
            self.status.setText(
                "sign_in_pending | Complete ChatGPT sign-in in your browser."
            )
            return
        url = str(payload.get("verificationUrl") or "")
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
            return
        result = clean.get("result")
        if task == "models":
            model_result = result if isinstance(result, dict) else {}
            if not model_result.get("ok"):
                self.status.setText(
                    f"models_error | {model_result.get('error') or 'Unknown error'}"
                )
                return
            self._chatgpt_model_details = {
                str(item.get("id")): dict(item)
                for item in model_result.get("model_details") or []
                if isinstance(item, dict) and item.get("id")
            }
            self._chatgpt_default_model = str(model_result.get("default_model") or "")
            self._apply_provider_models(
                "chatgpt", list(model_result.get("models") or [])
            )
            self._refresh_reasoning_efforts()
            return
        if task == "logout":
            self.status.setText("not_configured | ChatGPT account signed out.")
            return
        self.status.setText(self._chatgpt_account_status(result))
        self._chatgpt_login_session = None

    def _start_chatgpt_login(self, mode: str) -> None:
        if self._selected_provider() != "chatgpt":
            return
        from VibeCADCodex import ChatGPTLoginSession

        session = ChatGPTLoginSession()
        self._chatgpt_login_session = session

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

        self.status.setText("sign_out_pending | Signing out of ChatGPT...")
        self._run_chatgpt_task("logout", logout_account)

    def _refresh_build123d_status(self, _enabled: bool | None = None) -> None:
        if not self.build123d_enabled.isChecked():
            self.build123d_status.setText("disabled")
            return
        try:
            from VibeCADBuild123d import runtime_health

            health = runtime_health(refresh=True)
        except Exception as exc:
            self.build123d_status.setText(f"unavailable | {exc}")
            return
        if health.get("ready"):
            self.build123d_status.setText(
                f"ready | build123d {health.get('version')} | isolated process"
            )
        else:
            self.build123d_status.setText(
                f"unavailable | {health.get('error') or 'runtime check failed'}"
            )

    def _browse_openscad_executable(self) -> None:
        from PySide import QtWidgets

        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self.form,
            "Select OpenSCAD executable",
            self.openscad_executable.text() or str(Path.home()),
            "Executables (*.exe);;All files (*)",
        )
        if selected:
            self.openscad_executable.setText(selected)
            self._refresh_openscad_status()

    def _refresh_openscad_status(self, _enabled: bool | None = None) -> None:
        if not self.openscad_enabled.isChecked():
            self.openscad_status.setText("disabled")
            return
        try:
            from VibeCADOpenSCAD import runtime_health

            health = runtime_health(
                executable_override=self.openscad_executable.text().strip(),
                refresh=True,
            )
        except Exception as exc:
            self.openscad_status.setText(f"unavailable | {exc}")
            return
        if health.get("ready"):
            source = "override" if health.get("source") == "preference" else "bundled"
            self.openscad_status.setText(
                f"ready | {health.get('version')} | {source} | isolated process"
            )
        else:
            self.openscad_status.setText(
                f"unavailable | {health.get('error') or 'runtime check failed'}"
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
        return self.model

    def _provider_memory_combo(self, provider: str):
        if provider == "anthropic":
            return self.anthropic_intent_memory_model
        if provider == "chatgpt":
            return self.chatgpt_intent_memory_model
        return self.openai_intent_memory_model

    def _provider_active_memory_label(self, provider: str) -> str:
        if provider == "anthropic":
            return "Use active Anthropic model"
        if provider == "chatgpt":
            return "Use active ChatGPT model"
        return "Use active OpenAI model"

    def _apply_provider_models(self, provider: str, models: list[str]) -> None:
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
        self.status.setText(f"models_ok | {display} | {len(models)} models")

    def _fetch_models(self) -> None:
        provider = self._selected_provider()
        if provider == "chatgpt":
            from VibeCADCodex import list_models

            self.status.setText(
                "models_pending | Loading ChatGPT subscription models..."
            )
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
        self._apply_provider_models(provider, list(result["models"]))

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
        delete_keyring_key(provider=self._selected_provider())
        self.api_key.clear()
        self.intent_memory_status.clear()
        self._refresh_status()

    def _validate_auth(self) -> None:
        provider = self._selected_provider()
        if provider == "chatgpt":
            self._refresh_chatgpt_status()
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
            build123d_enabled=self.build123d_enabled.isChecked(),
            openscad_enabled=self.openscad_enabled.isChecked(),
            vibescript_enabled=self.vibescript_enabled.isChecked(),
            vibescript_on_bim_enabled=self.vibescript_on_bim_enabled.isChecked(),
            openscad_executable=self.openscad_executable.text().strip(),
            openscad_library_paths=self.openscad_library_paths.toPlainText().strip(),
            scripted_timeout_seconds=persisted.scripted_timeout_seconds,
            scripted_memory_limit_mb=persisted.scripted_memory_limit_mb,
        )

    def _refresh_status(self) -> None:
        if self._selected_provider() == "chatgpt":
            self._refresh_chatgpt_status()
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

        self.status.setText("checking | Checking ChatGPT subscription sign-in...")
        self._run_chatgpt_task("status", lambda: read_account(refresh_token=False))

    def saveSettings(self) -> None:
        save_settings(self._current_settings())
        try:
            import VibeCADGui

            VibeCADGui.apply_modeling_preferences()
        except Exception as exc:
            App.Console.PrintWarning(
                f"VibeCAD modeling preference update failed: {exc}\n"
            )

    def loadSettings(self) -> None:
        settings = load_settings()
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
        self.build123d_enabled.setChecked(settings.build123d_enabled)
        self._refresh_build123d_status()
        self.openscad_enabled.setChecked(settings.openscad_enabled)
        self.vibescript_enabled.setChecked(settings.vibescript_enabled)
        self.vibescript_on_bim_enabled.setChecked(settings.vibescript_on_bim_enabled)
        self.openscad_executable.setText(settings.openscad_executable)
        self.openscad_library_paths.setPlainText(settings.openscad_library_paths)
        self._refresh_openscad_status()
        self.api_key.clear()
        self._update_provider_visibility()
        self._refresh_status()


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
