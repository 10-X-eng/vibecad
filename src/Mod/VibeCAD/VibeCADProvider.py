# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider abstraction for VibeCAD AI runtimes."""

from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from VibeCADDebug import capture_provider_request
from VibeCADModelingSurface import (
    is_model_assembly_workbench,
    validate_surface_names,
)
from VibeCADVibeScriptDomains import get_vibescript_pack


MAX_PROVIDER_IMAGE_BYTES = 2_000_000
CODEX_INLINE_IMAGE_MAX_BYTES = 60_000
CODEX_LOCAL_IMAGE_MAX_BYTES = 20 * 1024 * 1024
PROVIDER_IMAGE_MAX_EDGE = 1568
PROVIDER_IMAGE_MIN_EDGE = 512
MAX_PROVIDER_TOOL_RESULT_BYTES = 40 * 1024
MAX_PROVIDER_COMPLETE_READ_BYTES = 2 * 1024 * 1024
MAX_PROVIDER_RESULT_TOP_LEVEL_FIELDS = 256
MAX_PROVIDER_INSTRUCTIONS_BYTES = 8 * 1024
DEFAULT_ANTHROPIC_MAX_TOKENS = 8192
ANTHROPIC_TURN_COMPACTION_MAX_TOKENS = 4096
ANTHROPIC_TURN_COMPACTION_MAX_INPUT_BYTES = 32 * 1024
ANTHROPIC_TURN_COMPACTION_MAX_ATTEMPTS = 2
PROVIDER_STREAM_DELTA_FLUSH_SECONDS = 0.075
ANTHROPIC_THINKING_BUDGETS = {
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
    "xhigh": 32768,
}
ANTHROPIC_ADAPTIVE_EFFORT = {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
}
ANTHROPIC_STREAM_MAX_ATTEMPTS = 3


VIBECAD_SYSTEM_INSTRUCTIONS = """You are VibeCAD, the mechanical design engineer for the user's live FreeCAD model.

CURRENT_USER_MESSAGE controls; RECENT_CONVERSATION_JSON resolves follow-ups. Treat explicit user constraints as requirements. A correction changes only the named geometry; preserve the existing architecture, identity, and history unless replacement or redesign was requested. Build editable, parametric geometry meeting function, dimensions, fit, manufacturability, and appearance. Default to catalog fasteners. Decide unspecified details; ask only if a choice changes function or geometry.

Use only the tools exposed for this turn and exact state returned in the current context or by a tool; never guess names, references, revisions, or API members. Fix failures before dependent features; never repeat an unchanged failure. Hide source bodies when reviewing assembly occurrences. Before claiming completion, verify requested dimensions, topology, interfaces, clearances, assembly retention, service motion, manufacturability, and appearance; capture the viewport for visual judgment. Never claim work or verification not performed."""


ANTHROPIC_TURN_COMPACTION_INSTRUCTIONS = """You compact one unfinished VibeCAD agent turn.

Call commit_turn_compaction exactly once. Preserve the current user request,
explicit requirements and rejected directions, completed CAD actions and their
observed results, live artifact identities and revisions, the blocking failure,
and the next concrete action. Be concise and factual. Do not solve the CAD task.
Do not invent state. Omit hidden reasoning, narration, apologies, raw source,
schemas, geometry arrays, screenshots, catalog inventories, and repeated logs.
The supplied packet has already removed those deterministic or noisy values."""


def _vibescript_surface_active(context: dict[str, Any]) -> bool:
    """Return whether this turn exposes the VibeScript authoring surface."""
    for schema in context.get("provider_tool_schemas") or []:
        if isinstance(schema, dict) and str(schema.get("name", "")).startswith(
            "vibescript."
        ):
            return True
    return False


def _vibescript_domain(context: dict[str, Any]) -> str | None:
    surface = context.get("modeling_surface")
    if isinstance(surface, dict) and surface.get("engine") == "vibescript":
        domain = str(surface.get("domain") or "").strip()
        if domain:
            return domain
    domains: set[str] = set()
    for schema in context.get("provider_tool_schemas") or []:
        if not isinstance(schema, dict):
            continue
        parts = str(schema.get("name") or "").split(".")
        if not parts or parts[0] != "vibescript":
            continue
        if len(parts) == 3:
            domains.add(parts[1])
    return next(iter(domains)) if len(domains) == 1 else None


def _vibescript_authoring_instruction(context: dict[str, Any]) -> str:
    domain = _vibescript_domain(context)
    workbench = str(context.get("workbench") or "")
    pack = get_vibescript_pack(workbench)
    if pack is None or pack.domain != domain:
        return ""
    if is_model_assembly_workbench(workbench):
        part_pack = get_vibescript_pack("PartDesignWorkbench")
        assembly_pack = get_vibescript_pack("AssemblyWorkbench")
        if part_pack is None or assembly_pack is None:
            return ""
        return (
            "VIBESCRIPT MODEL + ASSEMBLY AUTHORING\n"
            "Model and Assembly are one authoring surface; the visible ribbon is "
            "presentation only. For a new source, pass domain='partdesign' to "
            "vibescript.create_program for part geometry or domain='assembly' for "
            "occurrences, joints, mechanisms, and simulations. Read the matching API "
            "with vibescript.read_api(domain=...). Existing sources route by their "
            "human-readable program reference without a workbench switch.\n"
            f"PARTS: {part_pack.instructions}\n"
            f"ASSEMBLIES: {assembly_pack.instructions}\n"
            "Use definitions in available_components with api.component or "
            "api.instances; search only when the needed item is absent or needs more "
            "metadata. editable_sources.all_sources lists both domains; each item is "
            "one editable part or program, including failed and unbuilt code. Copy its "
            "program reference into vibescript.read_source before editing, then send "
            "the complete updated source "
            "and revision to vibescript.edit_source, and use "
            "vibescript.build_program only to rebuild unchanged code. Source receives "
            "immutable doc and api values plus validated inputs. Reuse existing source. "
            "Use vibescript.set_inputs for a value-only change; otherwise include "
            "changed inputs, input_schema, or expected_outputs with "
            "vibescript.edit_source. Use "
            "vibescript.read_geometry for imported or unfamiliar geometry and "
            "vibescript.read_placement before relying on an unfamiliar coordinate "
            "convention."
        )
    component_instruction = (
        " Use a definition in available_components with api.component or api.instances. "
        "In Assembly, an occurrence reference adopts that exact placed object instead "
        "of making a duplicate. Search the component catalog only when the needed item "
        "is not listed or more metadata is required. "
        "If its inventory is truncated, enumerate compact references with limit=200 "
        "and always follow next_offset until it is null; byte-safe pages may be smaller."
        if domain in {"partdesign", "assembly", "robot"}
        else ""
    )
    return (
        f"VIBESCRIPT {pack.title.upper()} AUTHORING\n"
        f"Write CAD only through the active {pack.title} VibeScript API. "
        f"{pack.instructions}{component_instruction}\n\n"
        "Each editable_sources item is one editable part or program, including failed "
        "and unbuilt code. Copy its program reference into vibescript.read_source before "
        "editing; send the complete updated source and returned revision to "
        "vibescript.edit_source. Use vibescript.build_program to rebuild unchanged code. "
        "Use vibescript.read_geometry on an exact object reference before depending on "
        "imported or unfamiliar geometry. "
        "Use vibescript.read_placement before relying on an unfamiliar sketch plane or "
        "oriented primitive. "
        "Read only needed API calls with vibescript.read_api(names=[...]) or groups=[...]. "
        "Source receives immutable doc and api values plus validated inputs. Outputs "
        "keep stable names and must use these types: "
        + ", ".join(pack.output_types)
        + ". Reuse existing source. Use vibescript.set_inputs for a value-only change; "
        "otherwise use vibescript.edit_source and include any changed inputs, input_schema, "
        "or expected_outputs in the same call."
    )


def _system_instruction_sections(context: dict[str, Any]) -> list[str]:
    """Ordered system-instruction sections shared by every wire format."""
    sections = [VIBECAD_SYSTEM_INSTRUCTIONS]
    if _vibescript_surface_active(context):
        instruction = _vibescript_authoring_instruction(context)
        if instruction:
            sections.append(instruction)
    return sections


def _provider_instructions(context: dict[str, Any]) -> str:
    instructions = "\n\n".join(_system_instruction_sections(context))
    encoded_bytes = len(instructions.encode("utf-8"))
    if encoded_bytes > MAX_PROVIDER_INSTRUCTIONS_BYTES:
        raise ValueError(
            "VibeCAD provider instructions exceed the deterministic "
            f"{MAX_PROVIDER_INSTRUCTIONS_BYTES}-byte limit ({encoded_bytes} bytes)."
        )
    return instructions


def _provider_option(context: dict[str, Any], name: str) -> bool:
    options = context.get("_vibecad_provider_options")
    return bool(options.get(name)) if isinstance(options, dict) else False


def _provider_option_value(context: dict[str, Any], name: str) -> Any:
    options = context.get("_vibecad_provider_options")
    return options.get(name) if isinstance(options, dict) else None


def _anthropic_system_blocks(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": section,
            "cache_control": {"type": "ephemeral"},
        }
        for section in _system_instruction_sections(context)
    ]


class ProviderUnavailable(RuntimeError):
    pass


@dataclass
class ProviderResult:
    final_output: str
    raw: Any = None


ToolRunner = Callable[[str, str, str], dict[str, Any]]
CancellationCheck = Callable[[], bool]
ProgressCallback = Callable[[dict[str, Any]], None]


class BaseProvider:
    def run(
        self,
        prompt: str,
        context: dict[str, Any],
        tool_runner: ToolRunner | None = None,
        cancellation_check: CancellationCheck | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ProviderResult:
        raise NotImplementedError


class OfflineProvider(BaseProvider):
    """Report that AI is unavailable without pretending to perform CAD work."""

    def run(
        self,
        prompt: str,
        context: dict[str, Any],
        tool_runner: ToolRunner | None = None,
        cancellation_check: CancellationCheck | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ProviderResult:
        if cancellation_check is not None and cancellation_check():
            raise ProviderUnavailable("VibeCAD run stopped by user.")
        workbench = context.get("workbench") or "unknown"
        return ProviderResult(
            "VibeCAD is offline. "
            f"Active workbench: {workbench}. "
            "Configure authentication before asking the AI provider."
        )


def provider_tool_schema_digest(schemas: list[dict[str, Any]]) -> str:
    """Return a deterministic digest for one ordered provider schema list."""
    try:
        encoded = json.dumps(
            schemas,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Provider tool schemas are not JSON serializable: {exc}"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _codex_uses_namespaced_tools(
    *,
    auth_mode: str,
    base_url: str | None,
) -> bool:
    """Return whether the active Responses endpoint accepts namespace tools."""

    if str(auth_mode or "").strip().lower() == "chatgpt":
        return True
    clean_base_url = str(base_url or "").strip()
    if not clean_base_url:
        return True
    hostname = str(urlsplit(clean_base_url).hostname or "").lower()
    return hostname == "api.openai.com" or hostname.endswith(".api.openai.com")


def _codex_flat_function_name(namespace: str, function_name: str) -> str:
    name = (
        f"{_provider_function_name(namespace)}"
        f"__{_provider_function_name(function_name)}"
    )
    if len(name) > 128:
        raise ValueError(
            f"Flattened Codex dynamic tool name exceeds 128 characters: {name!r}"
        )
    return name


def _codex_dynamic_tool_surface(
    context: dict[str, Any],
    *,
    namespaced: bool = True,
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], str]]:
    """Build app-server tools from the frozen turn-start VibeCAD surface."""

    surface = context.get("provider_tool_surface")
    if not (
        isinstance(surface, dict)
        and surface.get("kind") == "turn_start_snapshot"
        and surface.get("frozen") is True
    ):
        reason = str(surface.get("reason") or "") if isinstance(surface, dict) else ""
        raise ProviderUnavailable(
            "Codex mode requires a valid frozen turn-start VibeCAD "
            "tool surface." + (f" {reason}" if reason else "")
        )
    expected_surface_fields = {
        "kind",
        "frozen",
        "workbench",
        "engine",
        "domain",
        "surface_id",
        "available",
        "unavailable_reason",
        "tool_names",
        "schema_count",
        "schema_sha256",
    }
    if set(surface) != expected_surface_fields:
        raise ProviderUnavailable(
            "The frozen VibeCAD tool surface has missing or unexpected fields."
        )
    schemas = context.get("provider_tool_schemas")
    if not isinstance(schemas, list) or not schemas:
        raise ProviderUnavailable(
            "The frozen VibeCAD tool surface has no provider tool schemas."
        )
    if any(not isinstance(schema, dict) for schema in schemas):
        raise ProviderUnavailable(
            "The frozen VibeCAD tool surface contains a non-object schema."
        )
    schema_names = [str(schema.get("name") or "").strip() for schema in schemas]
    declared = surface.get("tool_names")
    if not isinstance(declared, list):
        raise ProviderUnavailable(
            "The frozen VibeCAD tool surface has no declared tool-name list."
        )
    declared_names = [str(name).strip() for name in declared]
    if any(not name for name in schema_names + declared_names):
        raise ProviderUnavailable(
            "The frozen VibeCAD tool surface contains an empty tool name."
        )
    if any(
        not separator or not domain or not operation
        for name in schema_names
        for domain, separator, operation in (name.partition("."),)
    ):
        raise ProviderUnavailable(
            "Every frozen VibeCAD tool name must use the domain.operation form."
        )
    if len(schema_names) != len(set(schema_names)):
        raise ProviderUnavailable(
            "The frozen VibeCAD tool surface contains duplicate tool schemas."
        )
    if schema_names != declared_names:
        raise ProviderUnavailable(
            "The VibeCAD tool declarations do not match the frozen turn-start "
            "surface. Start a new turn from the current surface."
        )
    if surface.get("schema_count") != len(schemas):
        raise ProviderUnavailable(
            "The VibeCAD schema count does not match the frozen turn-start surface."
        )
    try:
        schema_digest = provider_tool_schema_digest(schemas)
    except ValueError as exc:
        raise ProviderUnavailable(str(exc)) from exc
    if surface.get("schema_sha256") != schema_digest:
        raise ProviderUnavailable(
            "The VibeCAD tool schemas changed after the turn-start surface was frozen."
        )
    workbench = str(surface.get("workbench") or "") or None
    engine = str(surface.get("engine") or "")
    modeling_surface = context.get("modeling_surface")
    if not isinstance(modeling_surface, dict):
        raise ProviderUnavailable(
            "The frozen VibeCAD turn has no modeling-surface declaration."
        )
    if (
        str(modeling_surface.get("workbench") or "") != str(workbench or "")
        or surface.get("engine") != modeling_surface.get("engine")
        or surface.get("domain") != modeling_surface.get("domain")
        or surface.get("surface_id") != modeling_surface.get("surface_id")
        or surface.get("available") is not modeling_surface.get("available")
        or str(surface.get("unavailable_reason") or "")
        != str(modeling_surface.get("unavailable_reason") or "")
    ):
        raise ProviderUnavailable(
            "The modeling-engine/domain declaration does not match the frozen "
            "VibeCAD surface."
        )
    try:
        validate_surface_names(
            workbench=workbench,
            engine=engine,
            names=schema_names,
            allowed_names=declared_names,
        )
    except ValueError as exc:
        raise ProviderUnavailable(str(exc)) from exc
    dynamic_tools: list[dict[str, Any]] = []
    namespaces: dict[str, dict[str, Any]] = {}
    names: dict[tuple[str, str], str] = {}
    for schema in schemas:
        tool_name = str(schema.get("name") or "").strip()
        domain, _, operation = tool_name.partition(".")
        try:
            namespace_name = _provider_function_name(domain)
            function_name = _provider_function_name(operation)
            input_schema = _provider_tool_parameters(schema)
        except ValueError as exc:
            raise ProviderUnavailable(
                f"Invalid frozen schema for VibeCAD tool {tool_name!r}: {exc}"
            ) from exc
        flat_name = (
            ""
            if namespaced
            else _codex_flat_function_name(namespace_name, function_name)
        )
        key = (
            (namespace_name, function_name)
            if namespaced
            else ("", flat_name)
        )
        if key in names:
            raise ProviderUnavailable(
                "Duplicate Codex dynamic tool name: "
                + (
                    f"{namespace_name}.{function_name}"
                    if namespaced
                    else flat_name
                )
            )
        names[key] = tool_name
        function = {
            "type": "function",
            "name": function_name if namespaced else flat_name,
            "description": str(schema.get("description") or ""),
            "deferLoading": False,
            "inputSchema": input_schema,
        }
        if not namespaced:
            dynamic_tools.append(function)
            continue
        namespace = namespaces.setdefault(
            namespace_name,
            {
                "type": "namespace",
                "name": namespace_name,
                "description": f"VibeCAD {domain or 'CAD'} operations available now.",
                "tools": [],
            },
        )
        namespace["tools"].append(function)
    if namespaced:
        dynamic_tools = [namespaces[name] for name in sorted(namespaces)]
    return dynamic_tools, names


def _codex_skill_read_tool(*, namespaced: bool = True) -> dict[str, Any]:
    function = {
        "type": "function",
        "name": (
            "read" if namespaced else _codex_flat_function_name("skills", "read")
        ),
        "description": (
            "Read one enabled skill's SKILL.md or a referenced UTF-8 "
            "resource contained in that skill directory."
        ),
        "deferLoading": False,
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact skill name from the available skills list.",
                },
                "resource": {
                    "type": "string",
                    "description": (
                        "Relative resource path inside the skill directory; "
                        "defaults to SKILL.md."
                    ),
                    "default": "SKILL.md",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    }
    if not namespaced:
        return function
    return {
        "type": "namespace",
        "name": "skills",
        "description": "Read enabled Codex skill instructions and resources.",
        "tools": [function],
    }


def _codex_turn_input(prompt: str, context: dict[str, Any]) -> list[dict[str, Any]]:
    visible = _model_visible_context(context)
    image_blocks = _codex_context_image_blocks(visible)
    items: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for note in _context_image_delivery_notes(visible):
        items.append({"type": "text", "text": note})
    for label, mime_type, data in image_blocks:
        items.append({"type": "text", "text": label})
        items.append(
            {
                "type": "image",
                "url": f"data:{mime_type};base64,{data}",
            }
        )
    return items


def _codex_prompt_without_replayed_conversation(prompt: str) -> str:
    """Remove text-history replay once the Codex thread already owns it."""

    start = "RECENT_CONVERSATION_JSON\n"
    end = "\nEND_RECENT_CONVERSATION_JSON"
    if start not in prompt or end not in prompt:
        return prompt
    prefix, remainder = prompt.split(start, 1)
    _prior, suffix = remainder.split(end, 1)
    empty = json.dumps(
        {
            "turns": [],
            "omitted_turn_count": 0,
            "truncated_turn_count": 0,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return prefix + start + empty + end + suffix


def _codex_tool_image_content_items(
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the legacy dynamic-tool image response representation.

    Codex accepts this shape for the immediate tool response, but Responses
    history replays can reject the nested ``function_call_output`` image URL.
    New tool captures are therefore delivered with ``turn/steer`` instead.
    This helper remains for callers that only need protocol serialization.
    """
    visible = _model_visible_context(context)
    image_blocks = _codex_context_image_blocks(visible)
    items = [
        {"type": "inputText", "text": note}
        for note in _context_image_delivery_notes(visible)
    ]
    for label, mime_type, data in image_blocks:
        items.append({"type": "inputText", "text": label})
        items.append(
            {
                "type": "inputImage",
                "imageUrl": f"data:{mime_type};base64,{data}",
            }
        )
    return items


def _codex_local_image_input(
    path_text: Any,
    *,
    label: str,
) -> list[dict[str, Any]]:
    """Return same-turn Codex user input for one verified local image."""

    path = Path(str(path_text or "")).expanduser()
    if not path.is_file():
        raise ValueError(f"captured image file not found: {path}")
    try:
        size = int(path.stat().st_size)
    except OSError as exc:
        raise ValueError(f"captured image file could not be inspected: {path}") from exc
    if size <= 0:
        raise ValueError("captured image file is empty")
    if size > CODEX_LOCAL_IMAGE_MAX_BYTES:
        raise ValueError(
            f"captured image is {size} bytes; maximum is "
            f"{CODEX_LOCAL_IMAGE_MAX_BYTES} bytes"
        )
    if _provider_image_mime_for_suffix(path.suffix) is None:
        raise ValueError(
            f"captured image type is unsupported: {path.suffix or path.name}"
        )
    return [
        {"type": "text", "text": str(label or "V:current")},
        {
            "type": "localImage",
            "path": str(path.resolve()),
            "detail": "original",
        },
    ]


def _codex_tool_image_steer_input(
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return non-replayed visual input for an image-producing CAD tool."""

    attachment = result.get("_vibecad_image_attachment")
    if not isinstance(attachment, dict) or not str(attachment.get("path") or ""):
        return []
    name = str(attachment.get("name") or "current viewport").strip()
    return _codex_local_image_input(
        attachment["path"],
        label=f"V:current|{name}",
    )


def _codex_shutdown_summary(client: Any) -> str:
    """Return concise app-server lifecycle state for provider failures."""

    details = getattr(client, "shutdown_details", None)
    if callable(details):
        details = details()
    if not isinstance(details, dict):
        return ""
    values: list[str] = []
    reason = str(details.get("reason") or "").strip()
    if reason:
        values.append(f"reason={reason}")
    exit_code = details.get("process_exit_code")
    if exit_code is not None:
        values.append(f"exit_code={exit_code}")
    active = details.get("active_server_request_count")
    if isinstance(active, int) and active > 0:
        values.append(f"active_tool_calls={active}")
    late = details.get("late_server_response_count")
    if isinstance(late, int) and late > 0:
        values.append(f"discarded_late_replies={late}")
    return ", ".join(values)


def _codex_context_screenshot_steer_input(
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Support older screenshot tools that expose only refreshed context."""

    screenshot = context.get("view_screenshot")
    if (
        not isinstance(screenshot, dict)
        or not screenshot.get("captured")
        or screenshot.get("pending_attachment") is not True
        or not str(screenshot.get("path") or "")
    ):
        return []
    return _codex_local_image_input(
        screenshot["path"],
        label="V:current|current viewport",
    )


class CodexProvider(BaseProvider):
    """OpenAI adapter backed exclusively by the official Codex app-server."""

    def __init__(
        self,
        model: str = "",
        api_key: str | None = None,
        auth_mode: str = "chatgpt",
        reasoning_effort: str = "high",
        timeout_seconds: float | None = None,
        base_url: str | None = None,
        web_search_enabled: bool = False,
        skills_enabled: bool = False,
    ) -> None:
        clean_auth_mode = str(auth_mode or "").strip().lower()
        if clean_auth_mode not in {"api_key", "chatgpt"}:
            raise ValueError("Codex auth_mode must be api_key or chatgpt.")
        self.model = str(model or "").strip()
        self.api_key = str(api_key or "").strip() or None
        self.auth_mode = clean_auth_mode
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.base_url = str(base_url or "").strip() or None
        self.web_search_enabled = bool(web_search_enabled)
        self.skills_enabled = bool(skills_enabled)

    @property
    def provider_id(self) -> str:
        return "openai" if self.auth_mode == "api_key" else "chatgpt"

    @property
    def provider_label(self) -> str:
        return (
            "OpenAI API key via Codex"
            if self.auth_mode == "api_key"
            else "ChatGPT subscription via Codex"
        )

    def run(
        self,
        prompt: str,
        context: dict[str, Any],
        tool_runner: ToolRunner | None = None,
        cancellation_check: CancellationCheck | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ProviderResult:
        from VibeCADCodex import (
            CODEX_OPENAI_API_KEY_ENV,
            CODEX_OPENAI_PROVIDER_ID,
            CodexAppServerClient,
            CodexAppServerError,
            codex_workspace,
            load_codex_skill_catalog,
            managed_codex_session,
            read_codex_skill_resource,
            update_cached_account,
            vibecad_thread_config,
        )
        from VibeCADCodexResponses import codex_responses_base_url

        live_context = dict(context)
        interaction_mode = (
            str(live_context.get("_vibecad_interaction_mode") or "build")
            .strip()
            .lower()
        )
        if interaction_mode not in {"build", "plan"}:
            raise ProviderUnavailable(
                f"Unknown VibeCAD interaction mode {interaction_mode!r}."
            )
        plan_mode = interaction_mode == "plan"
        namespaced_tools = _codex_uses_namespaced_tools(
            auth_mode=self.auth_mode,
            base_url=self.base_url,
        )
        current_dynamic_tools, dynamic_name_map = _codex_dynamic_tool_surface(
            live_context,
            namespaced=namespaced_tools,
        )
        if not current_dynamic_tools:
            raise ProviderUnavailable(
                "Codex mode has no declared VibeCAD tools for the current workbench."
            )
        thread_surface = live_context.get("_vibecad_codex_thread_surface")
        if isinstance(thread_surface, dict):
            thread_context = dict(live_context)
            thread_context.update(thread_surface)
        else:
            thread_context = live_context
        thread_dynamic_tools, _thread_name_map = _codex_dynamic_tool_surface(
            thread_context,
            namespaced=namespaced_tools,
        )
        skill_call_key = (
            ("skills", "read")
            if namespaced_tools
            else ("", _codex_flat_function_name("skills", "read"))
        )

        state_lock = threading.RLock()
        turn_completed = threading.Event()
        thread_id = ""
        turn_id = ""
        turn_status = ""
        turn_error = ""
        latest_message = ""
        skill_catalog: dict[str, Any] = {}

        def notification(method: str, params: dict[str, Any]) -> None:
            nonlocal turn_status, turn_error, latest_message
            event_thread_id = str(params.get("threadId") or "")
            event_turn_id = str(params.get("turnId") or "")
            if thread_id and event_thread_id and event_thread_id != thread_id:
                return
            if turn_id and event_turn_id and event_turn_id != turn_id:
                return
            if method in {"item/agentMessage/delta", "item/plan/delta"}:
                delta = str(params.get("delta") or "")
                if delta:
                    _emit_provider_progress(
                        progress_callback,
                        {
                            "event": "provider_text_delta",
                            "provider": self.provider_label,
                            "turn": 1,
                            "text": delta,
                        },
                    )
                return
            if method in {
                "item/reasoning/summaryTextDelta",
                "item/reasoning/textDelta",
            }:
                delta = str(params.get("delta") or "")
                if delta:
                    _emit_provider_progress(
                        progress_callback,
                        {
                            "event": "provider_reasoning_delta",
                            "provider": self.provider_label,
                            "turn": 1,
                            "text": delta,
                        },
                    )
                return
            if method == "item/started":
                item = params.get("item")
                if isinstance(item, dict) and item.get("type") in {
                    "webSearch",
                    "web_search",
                }:
                    _emit_provider_progress(
                        progress_callback,
                        {
                            "event": "provider_web_search_started",
                            "provider": self.provider_label,
                        },
                    )
                return
            if method == "item/completed":
                item = params.get("item")
                if isinstance(item, dict) and item.get("type") in {
                    "webSearch",
                    "web_search",
                }:
                    query = str(item.get("query") or "").strip()
                    action = item.get("action")
                    if not query and isinstance(action, dict):
                        query = str(action.get("query") or "").strip()
                    _emit_provider_progress(
                        progress_callback,
                        {
                            "event": "provider_web_search_completed",
                            "provider": self.provider_label,
                            "query": query,
                        },
                    )
                    return
                if isinstance(item, dict) and item.get("type") in {
                    "agentMessage",
                    "plan",
                }:
                    text = str(item.get("text") or "").strip()
                    if text:
                        with state_lock:
                            latest_message = text
                return
            if method == "account/updated":
                if params.get("authMode") == "chatgpt":
                    cached = {
                        "type": "chatgpt",
                        "planType": params.get("planType"),
                    }
                    update_cached_account(cached)
                elif params.get("authMode") is None:
                    update_cached_account(None)
                return
            if method == "turn/completed":
                turn = params.get("turn")
                if isinstance(turn, dict):
                    with state_lock:
                        turn_status = str(turn.get("status") or "")
                        error = turn.get("error")
                        if isinstance(error, dict):
                            turn_error = str(error.get("message") or error)
                        elif error:
                            turn_error = str(error)
                turn_completed.set()

        def server_request(method: str, params: dict[str, Any]) -> dict[str, Any]:
            nonlocal live_context
            if method != "item/tool/call":
                raise CodexAppServerError(
                    f"VibeCAD does not permit Codex server request {method}."
                )
            namespace = str(params.get("namespace") or "")
            function_name = str(params.get("tool") or "")
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            if (namespace, function_name) == skill_call_key:
                _emit_provider_progress(
                    progress_callback,
                    {
                        "event": "provider_tool_requested",
                        "provider": self.provider_label,
                        "tool_name": "skills.read",
                        "tool_kind": "skill",
                        "arguments": _tool_arguments_summary(
                            json.dumps(
                                _json_safe(arguments),
                                ensure_ascii=True,
                                separators=(",", ":"),
                            )
                        ),
                    },
                )
                model_result = read_codex_skill_resource(
                    skill_catalog,
                    name=str(arguments.get("name") or ""),
                    resource=str(arguments.get("resource") or "SKILL.md"),
                )
                _emit_provider_progress(
                    progress_callback,
                    {
                        "event": "provider_tool_result_sent",
                        "provider": self.provider_label,
                        "tool_name": "skills.read",
                        "tool_kind": "skill",
                        "ok": bool(model_result.get("ok")),
                        "error": model_result.get("error"),
                    },
                )
                return {
                    "contentItems": [
                        {
                            "type": "inputText",
                            "text": json.dumps(
                                _json_safe(model_result),
                                ensure_ascii=True,
                                separators=(",", ":"),
                            ),
                        }
                    ],
                    "success": True,
                }

            tool_name = dynamic_name_map.get((namespace, function_name))
            if tool_name is None:
                declared_name = _thread_name_map.get((namespace, function_name))
                if declared_name is not None:
                    raise CodexAppServerError(
                        f"VibeCAD tool {declared_name} is not available in this "
                        f"{interaction_mode} turn."
                    )
                raise CodexAppServerError(
                    f"Unknown VibeCAD dynamic tool {namespace}.{function_name}."
                )
            arguments_json = json.dumps(
                _json_safe(arguments), ensure_ascii=True, separators=(",", ":")
            )
            provider_call_id = str(
                params.get("callId") or params.get("call_id") or ""
            )
            _emit_provider_progress(
                progress_callback,
                {
                    "event": "provider_tool_requested",
                    "provider": self.provider_label,
                    "tool_name": tool_name,
                    "arguments": _tool_arguments_summary(arguments_json),
                },
            )
            result = _call_parent_tool(
                tool_runner,
                tool_name,
                arguments_json,
                provider_call_id,
            )
            updated_context = _tool_runner_provider_update(tool_runner)
            with state_lock:
                live_context = updated_context
            model_result = _provider_visible_tool_result(result)
            model_result["vibecad_state_after"] = _provider_state_after_tool(
                updated_context, result
            )
            content_items: list[dict[str, Any]] = [
                {
                    "type": "inputText",
                    "text": json.dumps(
                        _json_safe(model_result),
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ),
                }
            ]
            image_input = _codex_tool_image_steer_input(result)
            if not image_input and (
                tool_name == "core.capture_view_screenshot"
                and result.get("captured")
                and result.get("new_observation", True)
            ):
                # Older capture implementations may not return the private exact
                # attachment. Fall back to the newly refreshed screenshot only.
                image_input = _codex_context_screenshot_steer_input(updated_context)
            if image_input:
                if not thread_id or not turn_id:
                    raise CodexAppServerError(
                        "VibeCAD captured an image before Codex established the "
                        "active turn required for visual delivery."
                    )
                steer_request = {
                    "threadId": thread_id,
                    "expectedTurnId": turn_id,
                    "input": image_input,
                }
                _capture_outbound_request(
                    live_context,
                    provider=self.provider_id,
                    sdk_call="codex-app-server.turn/steer",
                    turn=1,
                    request=steer_request,
                    base_url=(
                        self.base_url if self.auth_mode == "api_key" else None
                    ),
                )
                try:
                    client.request("turn/steer", steer_request, timeout=30.0)
                except Exception as exc:
                    raise CodexAppServerError(
                        "VibeCAD captured the image but could not deliver it to "
                        f"the active Codex turn: {exc}"
                    ) from exc
            if any(
                item.get("type") == "inputText"
                and "data:image/" in str(item.get("text") or "")
                for item in content_items
            ):
                raise CodexAppServerError(
                    "VibeCAD refused to place image bytes in dynamic-tool text."
                )
            _emit_provider_progress(
                progress_callback,
                {
                    "event": "provider_tool_result_sent",
                    "provider": self.provider_label,
                    "tool_name": tool_name,
                    "ok": bool(result.get("ok")),
                    "error": result.get("error"),
                    "failure_stage": result.get("failure_stage"),
                },
            )
            # Dynamic-tool success describes the client bridge, not the CAD
            # operation. Domain failures stay structured in the tool result so
            # the model can diagnose and repair them in the same turn.
            return {"contentItems": content_items, "success": True}

        if self.auth_mode == "api_key" and not self.api_key:
            raise ProviderUnavailable("No OpenAI API key is configured.")
        codex_base_url = (
            codex_responses_base_url(self.base_url)
            if self.auth_mode == "api_key"
            else None
        )
        environment = (
            {CODEX_OPENAI_API_KEY_ENV: self.api_key}
            if self.auth_mode == "api_key" and self.api_key
            else None
        )
        session_identity = live_context.get("_vibecad_codex_session")
        thread_declaration = thread_context.get("provider_tool_surface")
        managed = bool(
            isinstance(session_identity, dict)
            and str(session_identity.get("conversation_id") or "").strip()
            and isinstance(thread_declaration, dict)
            and thread_declaration.get("kind") == "turn_start_snapshot"
        )
        managed_stack = ExitStack()
        managed_lease = None
        if managed:
            runtime_payload = {
                "auth_mode": self.auth_mode,
                "model": self.model,
                "base_url": self.base_url or "",
                "web_search_enabled": self.web_search_enabled,
                "skills_enabled": self.skills_enabled,
                "api_key_sha256": (
                    hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()
                    if self.api_key
                    else ""
                ),
            }
            runtime_key = hashlib.sha256(
                json.dumps(
                    runtime_payload,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            thread_payload = {
                "conversation_id": str(session_identity["conversation_id"]),
                "conversation_path": str(
                    session_identity.get("conversation_path") or ""
                ),
                "workbench": str(thread_declaration.get("workbench") or ""),
                "engine": str(thread_declaration.get("engine") or ""),
                "surface_id": str(thread_declaration.get("surface_id") or ""),
                "schema_sha256": str(
                    thread_declaration.get("schema_sha256") or ""
                ),
            }
            thread_key = hashlib.sha256(
                json.dumps(
                    thread_payload,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            managed_lease = managed_stack.enter_context(
                managed_codex_session(
                    runtime_key=runtime_key,
                    thread_key=thread_key,
                    client_factory=CodexAppServerClient,
                    notification_handler=notification,
                    server_request_handler=server_request,
                    environment=environment,
                )
            )
            client = managed_lease.client
        else:
            client = CodexAppServerClient(
                notification_handler=notification,
                server_request_handler=server_request,
                environment=environment,
            )
        deadline = (
            time.monotonic() + self.timeout_seconds
            if self.timeout_seconds is not None and self.timeout_seconds > 0
            else None
        )
        try:
            if managed_lease is None:
                client.start()
            if self.auth_mode == "chatgpt":
                account_result = client.request(
                    "account/read", {"refreshToken": False}, timeout=30.0
                )
                account = (
                    account_result.get("account")
                    if isinstance(account_result, dict)
                    else None
                )
                if not isinstance(account, dict) or account.get("type") != "chatgpt":
                    update_cached_account(None)
                    raise ProviderUnavailable(
                        "No ChatGPT subscription is signed in. Open VibeCAD "
                        "Preferences and choose Sign in with ChatGPT."
                    )
                update_cached_account(account)

            if self.skills_enabled:
                skill_catalog = load_codex_skill_catalog(
                    client,
                    cwd=codex_workspace(),
                )
                if skill_catalog:
                    thread_dynamic_tools.append(
                        _codex_skill_read_tool(namespaced=namespaced_tools)
                    )

            forbidden_capabilities = [
                "shell",
                "general filesystem",
                "coding",
                "plugin",
                "app",
                "browser automation",
                "computer-control",
            ]
            if not self.web_search_enabled:
                forbidden_capabilities.append("web")
            developer_instructions = (
                "Operate only through the supplied VibeCAD tools. Do not "
                f"use {', '.join(forbidden_capabilities)} tools."
            )
            if self.skills_enabled and skill_catalog:
                developer_instructions += (
                    " Read selected skill instructions and referenced resources "
                    "only through skills.read."
                )
            thread_request: dict[str, Any] = {
                "cwd": str(codex_workspace()),
                "approvalPolicy": "never",
                "allowProviderModelFallback": False,
                "sandbox": "read-only",
                "baseInstructions": _provider_instructions(live_context),
                "developerInstructions": developer_instructions,
                "ephemeral": not managed,
                "environments": [],
                "dynamicTools": thread_dynamic_tools,
                "config": vibecad_thread_config(
                    web_search_enabled=self.web_search_enabled,
                    skills_enabled=self.skills_enabled,
                    collaboration_mode_enabled=managed or plan_mode,
                    openai_base_url=(
                        (codex_base_url or "")
                        if self.auth_mode == "api_key"
                        else None
                    ),
                ),
                "serviceName": "vibecad",
            }
            if self.auth_mode == "api_key":
                thread_request["modelProvider"] = CODEX_OPENAI_PROVIDER_ID
            if self.model:
                thread_request["model"] = self.model
            if managed_lease is not None and managed_lease.thread_id:
                resume_request = {"threadId": managed_lease.thread_id}
                _capture_outbound_request(
                    live_context,
                    provider=self.provider_id,
                    sdk_call="codex-app-server.thread/resume",
                    turn=1,
                    request=resume_request,
                    base_url=(
                        self.base_url if self.auth_mode == "api_key" else None
                    ),
                )
                thread_result = client.request(
                    "thread/resume",
                    resume_request,
                    timeout=30.0,
                )
            else:
                _capture_outbound_request(
                    live_context,
                    provider=self.provider_id,
                    sdk_call="codex-app-server.thread/start",
                    turn=1,
                    request=thread_request,
                    base_url=(
                        self.base_url if self.auth_mode == "api_key" else None
                    ),
                )
                thread_result = client.request(
                    "thread/start", thread_request, timeout=30.0
                )
            thread = (
                thread_result.get("thread") if isinstance(thread_result, dict) else None
            )
            if not isinstance(thread, dict) or not thread.get("id"):
                raise ProviderUnavailable("Codex app-server created no VibeCAD thread.")
            thread_id = str(thread["id"])
            resumed_thread = bool(
                managed_lease is not None and managed_lease.thread_id
            )
            if managed_lease is not None:
                managed_lease.remember_thread(thread_id)

            turn_request: dict[str, Any] = {
                "threadId": thread_id,
                "input": _codex_turn_input(
                    (
                        _codex_prompt_without_replayed_conversation(prompt)
                        if resumed_thread
                        else prompt
                    ),
                    live_context,
                ),
                "environments": [],
            }
            effort = _provider_reasoning_effort(self.reasoning_effort)
            if plan_mode:
                effective_model = str(
                    thread_result.get("model")
                    if isinstance(thread_result, dict)
                    else ""
                ).strip()
                if not effective_model:
                    effective_model = self.model
                if not effective_model:
                    raise ProviderUnavailable(
                        "Codex did not report the model required for Plan mode."
                    )
                turn_request["collaborationMode"] = {
                    "mode": "plan",
                    "settings": {
                        "model": effective_model,
                        "reasoning_effort": effort or "medium",
                        "developer_instructions": None,
                    },
                }
                turn_request["summary"] = "auto"
            elif effort:
                turn_request["effort"] = effort
                turn_request["summary"] = "auto"
            else:
                turn_request["effort"] = "none"
                turn_request["summary"] = "none"
            _capture_outbound_request(
                live_context,
                provider=self.provider_id,
                sdk_call="codex-app-server.turn/start",
                turn=1,
                request=turn_request,
                base_url=(self.base_url if self.auth_mode == "api_key" else None),
            )
            turn_result = client.request("turn/start", turn_request, timeout=30.0)
            turn = turn_result.get("turn") if isinstance(turn_result, dict) else None
            if not isinstance(turn, dict) or not turn.get("id"):
                raise ProviderUnavailable("Codex app-server created no VibeCAD turn.")
            turn_id = str(turn["id"])

            while not turn_completed.wait(0.05):
                if cancellation_check is not None and cancellation_check():
                    try:
                        client.request(
                            "turn/interrupt",
                            {"threadId": thread_id, "turnId": turn_id},
                            timeout=5.0,
                        )
                    finally:
                        raise ProviderUnavailable("VibeCAD run stopped by user.")
                if deadline is not None and time.monotonic() >= deadline:
                    try:
                        client.request(
                            "turn/interrupt",
                            {"threadId": thread_id, "turnId": turn_id},
                            timeout=5.0,
                        )
                    finally:
                        raise TimeoutError
                if not client.alive:
                    shutdown = _codex_shutdown_summary(client)
                    tail = " | ".join(client.stderr_tail[-3:])
                    raise ProviderUnavailable(
                        "Codex app-server stopped during the VibeCAD turn"
                        + (f" ({shutdown})" if shutdown else "")
                        + (f": {tail}" if tail else ".")
                    )

            with state_lock:
                completed_status = turn_status
                completed_error = turn_error
                final_output = latest_message
            if completed_status == "interrupted":
                raise ProviderUnavailable("VibeCAD run stopped by user.")
            if completed_status != "completed":
                raise ProviderUnavailable(
                    completed_error
                    or f"Codex turn ended with {completed_status or 'unknown status'}."
                )
            return ProviderResult(
                final_output=final_output,
                raw={
                    "thread_id": thread_id,
                    "interaction_mode": interaction_mode,
                    "auth_mode": self.auth_mode,
                },
            )
        except CodexAppServerError as exc:
            raise ProviderUnavailable(str(exc)) from exc
        finally:
            if managed_lease is not None:
                managed_stack.close()
            else:
                if client.alive and thread_id:
                    try:
                        client.request(
                            "thread/delete", {"threadId": thread_id}, timeout=5.0
                        )
                    except Exception:
                        pass
                client.close()


class AnthropicProvider(BaseProvider):
    """Native Anthropic Messages API adapter.

    Drives a tool-use loop over the same parent/child pipe bridge as the
    parent process: the child sends ``tool`` requests, the parent executes the
    real FreeCAD tool and replies with ``tool_result``. The dependency on the
    ``anthropic`` SDK stays optional so FreeCAD can start without it.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        api_key: str | None = None,
        reasoning_effort: str = "high",
        timeout_seconds: float | None = None,
        max_turns: int | None = None,
        base_url: str | None = None,
        web_search_enabled: bool = False,
        compaction_model: str | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.max_turns = max_turns
        self.base_url = base_url
        self.web_search_enabled = bool(web_search_enabled)
        self.compaction_model = str(compaction_model or model).strip() or model

    def run(
        self,
        prompt: str,
        context: dict[str, Any],
        tool_runner: ToolRunner | None = None,
        cancellation_check: CancellationCheck | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ProviderResult:
        try:
            provider_context = dict(context)
            provider_context["_vibecad_provider_options"] = {
                "web_search_enabled": self.web_search_enabled,
                "compaction_model": self.compaction_model,
            }
            return _run_provider_subprocess(
                prompt=prompt,
                context=provider_context,
                tool_runner=tool_runner,
                model=self.model,
                api_key=self.api_key,
                reasoning_effort=self.reasoning_effort,
                timeout_seconds=self.timeout_seconds,
                max_turns=self.max_turns,
                base_url=self.base_url,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
                child_main=_anthropic_child_main,
                provider_label="Anthropic provider",
            )
        except TimeoutError as exc:
            if self.timeout_seconds and self.timeout_seconds > 0:
                raise ProviderUnavailable(
                    f"Anthropic provider timed out after {self.timeout_seconds:g} seconds."
                ) from exc
            raise


def _run_with_deadline(call: Callable[[], Any], timeout_seconds: float) -> Any:
    if (
        timeout_seconds <= 0
        or threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
    ):
        return call()

    previous_handler = signal.getsignal(signal.SIGALRM)

    def _handle_timeout(signum, frame):
        raise TimeoutError

    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        return call()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _provider_reasoning_effort(value: str | None) -> str | None:
    clean = str(value or "").strip().lower()
    if clean in {"", "none", "off", "disabled", "false", "0"}:
        return None
    return clean


def _provider_windows_gui_session() -> bool:
    if sys.platform != "win32":
        return False
    try:
        from PySide import QtWidgets
    except Exception:
        return False
    try:
        return QtWidgets.QApplication.instance() is not None
    except Exception:
        return False


def _provider_spawn_python_executable(
    prefer_windowless: bool | None = None,
) -> str | None:
    if sys.platform not in {"darwin", "linux", "win32"}:
        return None

    if sys.platform in {"darwin", "linux"}:
        versioned_name = f"python{sys.version_info.major}.{sys.version_info.minor}"
        executable_names = (versioned_name, "python3", "python")
        candidates: list[Path] = []
        current_executable = Path(sys.executable or "")
        if current_executable.name.startswith("python"):
            candidates.append(current_executable)
        for prefix in (
            os.environ.get("CONDA_PREFIX"),
            os.environ.get("VIRTUAL_ENV"),
            sys.prefix,
            getattr(sys, "base_prefix", ""),
            str(Path(__file__).resolve().parents[2]),
        ):
            if prefix:
                candidates.extend(
                    Path(prefix) / "bin" / name for name in executable_names
                )
        candidates.extend(
            Path(resolved)
            for name in executable_names
            for resolved in (shutil.which(name),)
            if resolved
        )
        seen: set[str] = set()
        for candidate in candidates:
            candidate_text = str(candidate)
            if not candidate_text or candidate_text in seen:
                continue
            seen.add(candidate_text)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate_text
        return None

    use_windowless = (
        _provider_windows_gui_session()
        if prefer_windowless is None
        else bool(prefer_windowless)
    )
    executable_names = (
        ("pythonw.exe", "python.exe")
        if use_windowless
        else ("python.exe", "pythonw.exe")
    )
    candidates: list[Path] = []
    current_executable = Path(sys.executable or "")
    if current_executable.name.lower() in {"python.exe", "pythonw.exe"}:
        candidates.extend(
            current_executable.with_name(name) for name in executable_names
        )
    elif current_executable.name:
        candidates.extend(
            current_executable.with_name(name) for name in executable_names
        )

    for prefix in {sys.prefix, getattr(sys, "base_prefix", "")}:
        if prefix:
            candidates.extend(Path(prefix) / name for name in executable_names)

    seen: set[str] = set()
    for candidate in candidates:
        candidate_text = str(candidate)
        if not candidate_text or candidate_text in seen:
            continue
        seen.add(candidate_text)
        if candidate.exists():
            return candidate_text
    return None


def _provider_multiprocessing_context(
    prefer_windowless_python: bool | None = None,
) -> multiprocessing.context.BaseContext:
    start_methods = multiprocessing.get_all_start_methods()
    if sys.platform in {"darwin", "linux"}:
        python_executable = _provider_spawn_python_executable()
        if not python_executable:
            raise ProviderUnavailable(
                "VibeCAD cannot start the AI provider process because the packaged "
                f"{sys.platform} Python executable was not found."
            )
        if "spawn" not in start_methods:
            raise ProviderUnavailable(
                "VibeCAD cannot start the AI provider process because clean Python "
                f"spawn support is unavailable on {sys.platform}."
            )
        multiprocessing.set_executable(python_executable)
        return multiprocessing.get_context("spawn")

    if sys.platform == "win32":
        python_executable = _provider_spawn_python_executable(
            prefer_windowless=prefer_windowless_python
        )
        if not python_executable:
            raise ProviderUnavailable(
                "VibeCAD cannot start the AI provider process because python.exe "
                "or pythonw.exe was not found in the packaged runtime."
            )
        multiprocessing.set_executable(python_executable)

    if "spawn" in start_methods:
        return multiprocessing.get_context("spawn")
    return multiprocessing.get_context()


@contextmanager
def _provider_spawn_bootstrap_environment():
    """Force multiprocessing spawn to use packaged Python in embedded hosts.

    Python's spawn command ignores ``multiprocessing.set_executable()`` when
    ``sys.frozen`` is true and launches ``sys.executable`` with
    ``--multiprocessing-fork`` instead.  FreeCAD is an embedded application, not
    a Python-frozen app with a multiprocessing-aware executable, so the child can
    exit cleanly without ever running the target. Temporarily clearing the flag
    lets multiprocessing generate the normal packaged-Python ``spawn_main``
    command line.
    """

    if sys.platform not in {"darwin", "linux", "win32"} or not getattr(
        sys, "frozen", False
    ):
        yield
        return

    sentinel = object()
    original = getattr(sys, "frozen", sentinel)
    try:
        try:
            delattr(sys, "frozen")
        except Exception:
            setattr(sys, "frozen", False)
        yield
    finally:
        if original is sentinel:
            try:
                delattr(sys, "frozen")
            except Exception:
                pass
        else:
            setattr(sys, "frozen", original)


def _provider_subprocess_smoke_child_main(
    conn,
    prompt: str,
    context: dict[str, Any],
    model: str,
    api_key: str | None,
    reasoning_effort: str | None,
    timeout_seconds: float | None,
    max_turns: int | None,
    clear_inherited_modules: bool,
    base_url: str | None = None,
) -> None:
    try:
        conn.send(
            {
                "type": "done",
                "final_output": "ok",
                "raw": {"pid": os.getpid(), "executable": sys.executable},
            }
        )
    finally:
        conn.close()


def _provider_subprocess_smoke(
    *,
    prefer_windowless_python: bool | None = None,
    require_windowless_python: bool = False,
) -> None:
    result = _run_provider_subprocess(
        prompt="smoke",
        context={},
        tool_runner=None,
        model="smoke",
        api_key=None,
        reasoning_effort=None,
        timeout_seconds=10.0,
        max_turns=1,
        clear_inherited_modules=False,
        child_main=_provider_subprocess_smoke_child_main,
        provider_label="VibeCAD provider subprocess smoke",
        prefer_windowless_python=prefer_windowless_python,
    )
    if result.final_output != "ok":
        raise RuntimeError(f"Unexpected provider subprocess smoke result: {result!r}")
    executable = ""
    if isinstance(result.raw, dict):
        executable = str(result.raw.get("executable") or "")
    if (
        require_windowless_python
        and sys.platform == "win32"
        and not executable.lower().endswith("pythonw.exe")
    ):
        raise RuntimeError(
            f"Expected provider subprocess smoke to use pythonw.exe, got {executable!r}"
        )


def _run_provider_subprocess(
    *,
    prompt: str,
    context: dict[str, Any],
    tool_runner: ToolRunner | None,
    model: str,
    api_key: str | None,
    reasoning_effort: str | None,
    timeout_seconds: float | None,
    max_turns: int | None = None,
    base_url: str | None = None,
    clear_inherited_modules: bool = True,
    event_pump: Callable[[], None] | None = None,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
    child_main: Callable[..., None] | None = None,
    provider_label: str = "VibeCAD provider",
    prefer_windowless_python: bool | None = None,
) -> ProviderResult:
    if child_main is None:
        raise ValueError("Provider subprocess execution requires an explicit child.")
    multiprocessing_context = _provider_multiprocessing_context(
        prefer_windowless_python=prefer_windowless_python
    )
    reasoning_effort = _provider_reasoning_effort(reasoning_effort)
    parent_conn, child_conn = multiprocessing_context.Pipe()
    process = multiprocessing_context.Process(
        target=child_main,
        args=(
            child_conn,
            prompt,
            context,
            model,
            api_key,
            reasoning_effort,
            timeout_seconds,
            max_turns,
            clear_inherited_modules,
            base_url,
        ),
    )
    process.daemon = True
    original_stdin = sys.stdin
    replacement_stdin = None
    try:
        if not hasattr(sys.stdin, "close"):
            replacement_stdin = open(os.devnull, "r", encoding="utf-8")
            sys.stdin = replacement_stdin
        with _provider_spawn_bootstrap_environment():
            process.start()
    finally:
        sys.stdin = original_stdin
        if replacement_stdin is not None:
            replacement_stdin.close()
    child_conn.close()
    provider_started_at = time.monotonic()
    last_provider_activity_at = provider_started_at
    last_wait_notice_at = 0.0
    _emit_provider_progress(
        progress_callback,
        {
            "event": "provider_subprocess_started",
            "provider": provider_label,
            "pid": process.pid,
        },
    )

    deadline = (
        time.monotonic() + timeout_seconds
        if timeout_seconds is not None and timeout_seconds > 0
        else None
    )
    pump_events = event_pump or _process_provider_wait_events
    try:
        while True:
            if cancellation_check is not None and cancellation_check():
                raise ProviderUnavailable("VibeCAD run stopped by user.")
            remaining = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            if deadline is not None and remaining <= 0:
                raise TimeoutError
            wait_seconds = 0.05 if remaining is None else min(0.05, remaining)
            if parent_conn.poll(wait_seconds):
                try:
                    message = parent_conn.recv()
                except EOFError as exc:
                    raise ProviderUnavailable(
                        f"{provider_label} process ended before sending a result."
                    ) from exc
                if not isinstance(message, dict):
                    raise ProviderUnavailable(
                        f"{provider_label} process sent an invalid terminal message."
                    )
                last_provider_activity_at = time.monotonic()
                message_type = message.get("type")
                last_wait_notice_at = 0.0
                if message_type == "tool":
                    if cancellation_check is not None and cancellation_check():
                        raise ProviderUnavailable("VibeCAD run stopped by user.")
                    tool_name = str(message.get("tool_name", ""))
                    arguments_json = str(message.get("arguments_json") or "{}")
                    provider_call_id = str(message.get("provider_call_id") or "")
                    _emit_provider_progress(
                        progress_callback,
                        {
                            "event": "provider_tool_requested",
                            "provider": provider_label,
                            "tool_name": tool_name,
                            "arguments": _tool_arguments_summary(arguments_json),
                        },
                    )
                    result = _call_parent_tool(
                        tool_runner,
                        tool_name,
                        arguments_json,
                        provider_call_id,
                    )
                    parent_conn.send(
                        {
                            "type": "tool_result",
                            "result": result,
                            "context": _tool_runner_provider_update(tool_runner),
                        }
                    )
                    _emit_provider_progress(
                        progress_callback,
                        {
                            "event": "provider_tool_result_sent",
                            "provider": provider_label,
                            "tool_name": tool_name,
                            "ok": bool(result.get("ok")),
                            "error": result.get("error"),
                            "failure_stage": result.get("failure_stage"),
                        },
                    )
                    continue
                elif message_type == "done":
                    process.join(timeout=0.2)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=1)
                    return ProviderResult(
                        final_output=str(message.get("final_output", "")),
                        raw=message.get("raw"),
                    )
                elif message_type == "progress":
                    event = message.get("event")
                    if isinstance(event, dict):
                        _emit_provider_progress(progress_callback, event)
                    continue
                elif message_type == "error":
                    error = str(message.get("error") or "unknown provider error")
                    raise ProviderUnavailable(error)
                else:
                    continue
            else:
                pump_events()
                now = time.monotonic()
                if (
                    progress_callback is not None
                    and now - last_provider_activity_at >= 8.0
                    and now - last_wait_notice_at >= 15.0
                ):
                    last_wait_notice_at = now
                    _emit_provider_progress(
                        progress_callback,
                        {
                            "event": "provider_waiting",
                            "provider": provider_label,
                            "elapsed_seconds": now - provider_started_at,
                            "idle_seconds": now - last_provider_activity_at,
                            "pid": process.pid,
                        },
                    )

            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError

            if not process.is_alive():
                process.join(timeout=1)
                # A short-lived Windows pythonw child can finish immediately
                # after writing its final pipe message.  Give that message one
                # last bounded drain before treating a clean exit as empty.
                if parent_conn.poll(0.2):
                    continue
                if process.exitcode == 0:
                    raise ProviderUnavailable(
                        f"{provider_label} exited without a result."
                    )
                exit_detail = f"code {process.exitcode}"
                if (
                    os.name != "nt"
                    and process.exitcode is not None
                    and process.exitcode < 0
                ):
                    try:
                        exit_detail = signal.Signals(-process.exitcode).name
                    except ValueError:
                        exit_detail = f"signal {-process.exitcode}"
                raise ProviderUnavailable(
                    f"{provider_label} process exited with {exit_detail}."
                )
    finally:
        parent_conn.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=2)


def _process_provider_wait_events() -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    from PySide import QtCore, QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    app.processEvents(QtCore.QEventLoop.AllEvents, 10)


def _emit_provider_progress(
    progress_callback: ProgressCallback | None,
    event: dict[str, Any],
) -> None:
    if progress_callback is None:
        return
    progress_callback(dict(event))


def _send_child_progress(conn: Any, event: dict[str, Any]) -> None:
    conn.send({"type": "progress", "event": _json_safe(event)})


class _ProviderStreamDeltaBatcher:
    """Coalesce high-frequency provider deltas before crossing into the GUI.

    Provider SDKs commonly yield a separate event for a few characters or one
    token. Forwarding every event through the parent pipe causes one synchronous
    Qt transcript edit per fragment. Keep the stream live while bounding those
    cross-process GUI updates to a human-scale cadence.
    """

    def __init__(
        self,
        emit: Callable[[dict[str, Any]], None],
        *,
        provider: str,
        turn: int,
        flush_seconds: float = PROVIDER_STREAM_DELTA_FLUSH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._emit = emit
        self._provider = provider
        self._turn = turn
        self._flush_seconds = max(0.001, float(flush_seconds))
        self._clock = clock
        self._event_name = ""
        self._parts: list[str] = []
        self._last_flush_at = clock()

    def append(self, event_name: str, text: Any) -> None:
        delta = str(text or "")
        if not delta:
            return
        clean_event = str(event_name or "").strip()
        if clean_event not in {"provider_text_delta", "provider_reasoning_delta"}:
            raise ValueError(f"Unsupported provider delta event {clean_event!r}.")
        if self._event_name and clean_event != self._event_name:
            self.flush()
        self._event_name = clean_event
        self._parts.append(delta)
        if self._clock() - self._last_flush_at >= self._flush_seconds:
            self.flush()

    def flush(self) -> None:
        if not self._parts:
            return
        event = {
            "event": self._event_name,
            "provider": self._provider,
            "turn": self._turn,
            "text": "".join(self._parts),
        }
        self._event_name = ""
        self._parts = []
        self._last_flush_at = self._clock()
        self._emit(event)


def _send_child_error(conn: Any, provider_label: str, exc: BaseException) -> None:
    """Best-effort terminal error delivery from an isolated provider child."""

    detail = " ".join(str(exc or "").split())
    exception_name = type(exc).__name__
    message = f"{provider_label} failed with {exception_name}"
    if detail:
        message += f": {detail}"
    try:
        conn.send({"type": "error", "error": message})
    except (BrokenPipeError, EOFError, OSError):
        # The parent already owns cancellation/timeout reporting after closing
        # its pipe. There is no remaining receiver for a terminal child event.
        pass


def _tool_arguments_summary(arguments_json: str) -> dict[str, Any]:
    summary: dict[str, Any] = {"bytes": len(arguments_json.encode("utf-8"))}
    try:
        arguments = json.loads(arguments_json or "{}")
    except Exception:
        summary["valid_json"] = False
        return summary
    summary["valid_json"] = True
    if not isinstance(arguments, dict):
        summary["shape"] = type(arguments).__name__
        return summary
    keys = [str(key) for key in arguments]
    summary["key_count"] = len(keys)
    summary["keys"] = keys[:8]
    if len(keys) > 8:
        summary["truncated"] = True
    return summary


def _call_parent_tool(
    tool_runner: ToolRunner | None,
    tool_name: str,
    arguments_json: str,
    provider_call_id: str,
) -> dict[str, Any]:
    if tool_runner is None:
        return {"ok": False, "error": "No VibeCAD tool runner is available."}
    try:
        return tool_runner(tool_name, arguments_json, provider_call_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _tool_runner_provider_update(
    tool_runner: ToolRunner | None,
) -> dict[str, Any]:
    if tool_runner is None:
        raise RuntimeError("No VibeCAD tool runner is available for state refresh.")
    refresh = getattr(tool_runner, "provider_update", None)
    if not callable(refresh):
        raise RuntimeError("The VibeCAD tool runner has no provider_update contract.")
    value = refresh()
    if not isinstance(value, dict):
        raise RuntimeError("VibeCAD provider_update returned no structured context.")
    return value


def _model_visible_context(
    context: dict[str, Any],
) -> dict[str, Any]:
    sections = (
        "workbench",
        "modeling_surface",
        "native_state",
        "document",
        "selection",
        "editable_sources",
        "available_components",
        "view_screenshot",
        "reference_images",
    )
    result = {
        key: _json_safe(context[key])
        for key in sections
        if key in context and context[key] not in (None, "", [], {})
    }
    editable = result.get("editable_sources")
    if isinstance(editable, dict):
        result["editable_sources"] = _provider_visible_editable_sources(editable)
    components = result.get("available_components")
    if isinstance(components, dict):
        cleaned_components = dict(components)
        cleaned_components["components"] = [
            _provider_visible_component(item)
            for item in list(components.get("components") or [])
            if isinstance(item, dict)
        ]
        result["available_components"] = cleaned_components
    return result


def _provider_visible_program_source(source: dict[str, Any]) -> dict[str, Any]:
    """Expose one editable source by readable identity, never persistence UUID."""

    allowed = (
        "program",
        "source_kind",
        "domain",
        "workbench",
        "label",
        "status",
        "affected_outputs",
        "latest_candidate",
        "error",
        "read_tool",
        "read_arguments",
        "build_tool",
        "build_arguments",
        "edit_tool",
        "edit_target_arguments",
        "delete_output_tool",
        "delete_program_tool",
        "delete_target_arguments",
    )
    return {
        key: source[key]
        for key in allowed
        if key in source and source[key] not in (None, "", [], {})
    }


def _provider_visible_editable_sources(editable: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: value
        for key, value in editable.items()
        if key not in {"sources", "all_sources", "component_sources"}
    }
    for key in ("sources", "all_sources"):
        if key in editable:
            result[key] = [
                _provider_visible_program_source(source)
                for source in list(editable.get(key) or [])
                if isinstance(source, dict)
            ]
    if "component_sources" in editable:
        result["component_sources"] = [
            {
                key: value
                for key, value in source.items()
                if key not in {"source_id", "program_id", "document_uid"}
            }
            for source in list(editable.get("component_sources") or [])
            if isinstance(source, dict)
        ]
    return result


def _provider_visible_component(component: dict[str, Any]) -> dict[str, Any]:
    result = dict(component)
    authoring = result.get("authoring_source")
    if isinstance(authoring, dict):
        result["authoring_source"] = {
            key: value
            for key, value in authoring.items()
            if key not in {"source_id", "program_id", "document_uid"}
        }
    return result


def _provider_function_name(tool_name: str) -> str:
    clean = "_".join(
        part
        for part in "".join(
            character if character.isalnum() else "_"
            for character in str(tool_name or "").strip()
        ).split("_")
        if part
    )
    if not clean:
        raise ValueError("Provider tool name cannot be empty.")
    return clean


def _provider_tool_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    parameters = schema.get("parameters")
    if isinstance(parameters, dict) and set(parameters) == {"oneOf"}:
        branches = parameters.get("oneOf")
        if (
            isinstance(branches, list)
            and len(branches) == 1
            and isinstance(branches[0], dict)
        ):
            parameters = branches[0]
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        raise ValueError(f"Provider tool {schema.get('name')!r} has no object schema.")
    if not isinstance(parameters.get("properties"), dict):
        raise ValueError(f"Provider tool {schema.get('name')!r} has no properties.")
    return _json_safe(parameters)


def _anthropic_tool_definition(schema: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(schema.get("name") or "").strip()
    if not tool_name:
        raise ValueError("Provider tool schema is missing name.")
    return {
        "name": _provider_function_name(tool_name),
        "description": str(schema.get("description") or ""),
        "input_schema": _provider_tool_parameters(schema),
    }


def _selected_fields(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value[key]
        for key in keys
        if key in value and value[key] not in (None, "", [], {})
    }


def _compact_profile_status(value: Any) -> dict[str, Any]:
    return _selected_fields(
        value,
        (
            "found",
            "geometry_count",
            "constraint_count",
            "degrees_of_freedom",
            "constraint_state",
            "fully_constrained",
            "under_constrained",
            "construction_geometry_count",
            "edge_count",
            "wire_count",
            "closed_wire_count",
            "open_wire_count",
            "closed_profile",
            "ready_for_closed_profile_feature",
            "ready_for_pad",
            "ready_for_pocket",
            "ready_for_revolve",
            "ready_for_loft_section",
            "ready_for_hole_centers",
            "ready_for_path",
            "ready_for_layout",
            "geometry_types",
            "face_build_errors",
            "conflicting_constraint_indices",
            "redundant_constraint_indices",
            "constraint_type_counts",
            "block_constraint_count",
            "reason",
        ),
    )


def _compact_active_sketch_state(
    value: Any,
    *,
    include_profile: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = _selected_fields(
        value,
        (
            "found",
            "name",
            "label",
            "is_open",
            "owner_body",
            "map_mode",
            "support",
            "geometry_bounds",
        ),
    )
    if include_profile:
        profile = _compact_profile_status(value.get("profile_status"))
        if profile:
            result["profile_status"] = profile

    debt = _selected_fields(
        value.get("constraint_debt"),
        (
            "open_endpoint_count",
            "open_endpoints",
            "unconstrained_geometry_count",
            "unconstrained_geometry",
            "conflicting_constraint_indices",
            "redundant_constraint_indices",
            "native_degenerate_geometry_count",
            "visible_degenerate_geometry",
        ),
    )
    if debt:
        result["constraint_debt"] = debt

    junctions = value.get("junction_diagnostics")
    if isinstance(junctions, dict):
        compact_junctions = _selected_fields(
            junctions,
            (
                "junction_count",
                "non_tangent_junction_count",
                "tangent_tolerance_degrees",
                "near_tangent_tolerance_degrees",
            ),
        )
        if compact_junctions:
            result["junction_diagnostics"] = compact_junctions
    return result


def _provider_state_after_tool(
    context: dict[str, Any],
    tool_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del tool_result
    surface = context.get("modeling_surface")
    if not isinstance(surface, dict):
        return {"workbench": str(context.get("workbench") or "")}
    keys = (
        "workbench",
        "engine",
        "domain",
        "surface_id",
        "available",
        "invalidated",
        "next_turn_required",
    )
    result = {
        "surface": {
            key: _json_safe(surface[key])
            for key in keys
            if key in surface and surface[key] not in (None, "", [], {})
        }
    }
    native_state = context.get("native_state")
    if isinstance(native_state, dict):
        result["active_domain"] = _json_safe(native_state)
    return result


def _provider_visible_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return an exact normal result or an honest, bounded omission envelope.

    Authoring results are normally small and pass through unchanged. If a tool
    unexpectedly returns a huge diagnostic or artifact structure, replace the
    largest complete top-level values with deterministic size descriptors. No
    CAD value is truncated or sampled, and the model is directed to inspect
    the now-live state explicitly.
    """

    visible = dict(result)
    visible.pop("_vibecad_image_attachment", None)
    source_lifecycle = bool(
        visible.pop("_vibecad_source_lifecycle_result", False)
    )
    source_read = bool(visible.pop("_vibecad_source_read_result", False))
    geometry_request = visible.pop("_vibecad_geometry_read_request", None)
    complete_read = bool(
        visible.pop("_vibecad_complete_source_result", False)
        or visible.pop("_vibecad_complete_api_result", False)
    )
    visible = _provider_hide_internal_program_ids(visible)
    if source_lifecycle:
        visible = _provider_visible_source_lifecycle_result(visible)
    elif isinstance(visible.get("result"), dict) and bool(
        visible["result"].pop("_vibecad_source_lifecycle_result", False)
    ):
        # Background writes retain the exact raw result in the process-local
        # operation manager, but their terminal read must pass through the same
        # concise provider projection as a synchronous source write. Without
        # this, one collision summary is repeated through candidate outputs,
        # publication metadata, and live outputs until useful data crosses the
        # provider byte boundary.
        visible["result"] = _provider_visible_source_lifecycle_result(
            visible["result"]
        )
    elif source_read:
        visible = _provider_visible_source_read_result(visible)
    elif isinstance(geometry_request, dict):
        visible = _provider_visible_geometry_read_result(visible, geometry_request)
    visible = _provider_compact_terminal_operation(visible)
    safe = _json_safe(visible)
    encoded = json.dumps(
        safe,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    result_limit = (
        MAX_PROVIDER_COMPLETE_READ_BYTES
        if complete_read
        else MAX_PROVIDER_TOOL_RESULT_BYTES
    )
    if len(encoded) <= result_limit:
        return safe

    priority_fields = (
        "ok",
        "failure_code",
        "failure_stage",
        "error",
        "cancelled",
        "retry_same_call",
        "created",
        "updated",
        "changed",
        "deleted",
        "operation_succeeded",
        "operation",
        "result",
        "next_action",
        "next_actions",
        "document",
        "object",
        "object_name",
        "assembly",
        "program_id",
        "model_id",
        "working_revision",
        "accepted_revision",
        "revision",
        "model_state",
        "verification",
        "native_diagnostics",
        "transaction",
        "human_steering",
    )
    if len(safe) <= MAX_PROVIDER_RESULT_TOP_LEVEL_FIELDS:
        projected = dict(safe)
        omitted_count = 0
    else:
        projected = {key: safe[key] for key in priority_fields if key in safe}
        omitted_count = len(safe) - len(projected)
    boundary = {
        "bounded": True,
        "reason": "provider_tool_result_byte_limit",
        "original_json_bytes": len(encoded),
        "limit_json_bytes": result_limit,
        "original_sha256": hashlib.sha256(encoded).hexdigest(),
        "original_top_level_field_count": len(safe),
        "omitted_top_level_field_count": omitted_count,
        "recovery": (
            "Use the active surface's declared read tool for only the exact fact "
            "needed next."
        ),
    }
    projected["vibecad_result_boundary"] = boundary

    while _provider_json_bytes(projected) > result_limit:
        candidates = []
        protected_fields = {
            "ok",
            "operation",
            "operation_succeeded",
            "result",
            "next_action",
            "next_actions",
            "vibecad_result_boundary",
        }
        for key, value in projected.items():
            if key in protected_fields:
                continue
            if isinstance(value, dict) and value.get("_vibecad_value_omitted") is True:
                continue
            candidates.append((_provider_json_bytes(value), str(key), key, value))
        if not candidates:
            break
        _, _, key, value = sorted(
            candidates,
            key=lambda item: (-item[0], item[1]),
        )[0]
        projected[key] = _provider_omitted_value(value)
        omitted_count += 1
        boundary["omitted_top_level_field_count"] = omitted_count

    if _provider_json_bytes(projected) > result_limit:
        # A pathological protected value (normally an unbounded native error)
        # must not erase the terminal verdict. Return a fixed-shape operation
        # summary with the exact failure code, revision, and recovery calls.
        # This is more useful than replacing ``result`` wholesale with a byte
        # boundary marker: the model can still make the correct next call.
        projected = _provider_minimal_terminal_result(
            safe,
            boundary={
                **boundary,
                "omitted_top_level_field_count": len(safe),
            },
        )
    return projected


def _provider_minimal_terminal_result(
    result: dict[str, Any],
    *,
    boundary: dict[str, Any],
) -> dict[str, Any]:
    """Preserve an actionable terminal verdict under pathological payloads."""

    operation = result.get("operation")
    nested = result.get("result")
    if not isinstance(operation, dict) or not isinstance(nested, dict):
        return {
            **({"ok": result["ok"]} if "ok" in result else {}),
            "vibecad_result_boundary": boundary,
        }

    compact_operation = {
        key: operation[key]
        for key in ("status", "tool")
        if operation.get(key) not in (None, "")
    }
    compact_nested = {
        key: nested[key]
        for key in (
            "ok",
            "failure_code",
            "failure_stage",
            "cancelled",
            "retry_same_call",
            "program",
            "revision",
            "working_revision",
            "accepted_revision",
            "state",
            "validation_scope",
            "next_action",
            "next_actions",
        )
        if nested.get(key) not in (None, "", [], {})
    }
    if nested.get("error") not in (None, ""):
        encoded_error = str(nested["error"]).encode("utf-8", errors="replace")
        compact_nested["error"] = (
            str(nested["error"])
            if len(encoded_error) <= 2048
            else "The exact failure diagnostic exceeded the provider response limit."
        )
        if len(encoded_error) > 2048:
            compact_nested["error_boundary"] = {
                "utf8_bytes": len(encoded_error),
                "sha256": hashlib.sha256(encoded_error).hexdigest(),
            }
    return {
        **({"ok": result["ok"]} if "ok" in result else {}),
        **(
            {"operation_succeeded": result["operation_succeeded"]}
            if "operation_succeeded" in result
            else {}
        ),
        "operation": compact_operation,
        "result": compact_nested,
        "vibecad_result_boundary": boundary,
    }


def _provider_hide_internal_program_ids(value: Any) -> Any:
    """Remove persistence UUIDs from provider results while preserving readable targets."""

    if isinstance(value, dict):
        return {
            key: _provider_hide_internal_program_ids(item)
            for key, item in value.items()
            if key not in {"source_id", "program_id"}
        }
    if isinstance(value, list):
        return [_provider_hide_internal_program_ids(item) for item in value]
    return value


def _provider_compact_output(name: str, value: Any) -> dict[str, Any]:
    output = dict(value) if isinstance(value, dict) else {}
    compact = {
        "name": str(name),
        **{
            key: output[key]
            for key in (
                "label",
                "output_type",
                "derived_state",
                "visible",
                "reference",
            )
            if output.get(key) not in (None, "", [], {})
        },
    }
    facts = output.get("facts")
    if isinstance(facts, dict):
        compact_facts = {
            key: facts[key]
            for key in (
                "shape_type",
                "valid",
                "solid_count",
                "shell_count",
                "face_count",
                "edge_count",
                "vertex_count",
                "volume_mm3",
                "area_mm2",
                "bounds_mm",
            )
            if facts.get(key) not in (None, "", [], {})
        }
        if compact_facts:
            compact["geometry"] = compact_facts
    assembly_data = output.get("assembly_data")
    validation_scope = output.get("validation_scope")
    if validation_scope is None and isinstance(assembly_data, dict):
        validation_scope = assembly_data.get("validation_scope")
    if isinstance(validation_scope, dict):
        compact["validation_scope"] = dict(validation_scope)
    collision = (
        assembly_data.get("collision_summary")
        if isinstance(assembly_data, dict)
        else None
    )
    if isinstance(collision, dict):
        compact["collision_summary"] = {
            key: collision[key]
            for key in (
                "status",
                "analysis_complete",
                "collision_free",
                "evaluated_frame_count",
                "colliding_frame_count",
                "colliding_pair_count",
                "first_collision",
                "warning_count",
                "warnings",
            )
            if collision.get(key) not in (None, "", [], {})
            or key in {"collision_free", "analysis_complete"}
        }
    return compact


def _provider_compact_failure_details(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    fields = (
        "stage",
        "status",
        "solver_code",
        "solver_verdict",
        "joint_output",
        "joint_type",
        "component_output",
        "simulation_output",
        "frame_index",
        "iteration",
        "latest_residual",
        "correction",
    )
    result = {
        key: value[key]
        for key in fields
        if value.get(key) not in (None, "", [], {})
    }
    issues = value.get("issues")
    if isinstance(issues, list) and issues:
        result["issues"] = [
            _provider_compact_failure_details(item)
            for item in issues[:8]
            if isinstance(item, dict)
        ]
        if len(issues) > 8:
            result["issues_omitted"] = len(issues) - 8
    return result


def _provider_compact_observed(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {
        key: value[key]
        for key in (
            "exception_type",
            "domain_failure_stage",
            "termination_reason",
            "limit_reached",
            "returncode",
            "elapsed_seconds",
            "cancelled_by",
            "accepted_live_outputs_preserved",
            "partial_candidate_outputs_published",
        )
        if value.get(key) not in (None, "", [], {})
    }
    progress = value.get("worker_progress")
    if isinstance(progress, dict):
        result["worker_progress"] = {
            key: progress[key]
            for key in (
                "domain",
                "phase",
                "current_output",
                "phase_elapsed_seconds",
                "elapsed_seconds",
                "item_progress",
                "current_graph_node",
                "last_completed_graph_node",
                "completed",
                "failure",
            )
            if key in progress
        }
    details = _provider_compact_failure_details(value.get("details"))
    if details:
        result["details"] = details
    return result


def _provider_visible_source_lifecycle_result(
    result: dict[str, Any],
) -> dict[str, Any]:
    """Return one concise source-write verdict with exact next actions."""

    program = str(result.get("program") or "")
    revision = str(
        result.get("working_revision")
        or result.get("current_revision")
        or result.get("next_write_expected_revision")
        or ""
    )
    compact: dict[str, Any] = {
        key: result[key]
        for key in (
            "ok",
            "tool",
            "requested_action",
            "failure_code",
            "failure_stage",
            "error",
            "cancelled",
            "retry_same_call",
            "created",
            "updated",
            "changed",
            "deleted",
            "source_deleted",
            "deleted_output",
            "cad_objects_removed",
            "artifacts_deleted",
            "warnings",
            "phase_timings_seconds",
            "lifecycle_elapsed_seconds",
        )
        if result.get(key) not in (None, "", [], {})
    }
    if program:
        compact["program"] = program
    if revision:
        compact["revision"] = revision

    if isinstance(result.get("outputs"), list):
        live_outputs = (
            result.get("live_outputs")
            if isinstance(result.get("live_outputs"), dict)
            else {}
        )
        public_outputs = []
        for index, value in enumerate(result["outputs"]):
            if not isinstance(value, dict):
                continue
            name = str(value.get("name") or value.get("output_name") or index)
            live = live_outputs.get(name)
            combined = dict(live) if isinstance(live, dict) else {}
            combined.update(value)
            public_outputs.append(_provider_compact_output(name, combined))
        compact["outputs"] = public_outputs
    else:
        raw_outputs = result.get("live_outputs")
        if isinstance(raw_outputs, dict):
            compact["outputs"] = [
                _provider_compact_output(str(name), value)
                for name, value in sorted(
                    raw_outputs.items(), key=lambda item: str(item[0])
                )
            ]

    for output in list(compact.get("outputs") or []):
        if not isinstance(output, dict):
            continue
        validation_scope = output.get("validation_scope")
        if isinstance(validation_scope, dict):
            compact["validation_scope"] = dict(validation_scope)
            break

    model_state = result.get("model_state")
    if isinstance(model_state, dict):
        state = {
            key: model_state[key]
            for key in (
                "status",
                "accepted_is_current",
                "accepted_live_state_preserved",
            )
            if model_state.get(key) not in (None, "", [], {})
        }
        if state:
            compact["state"] = state

    for key in ("verification", "recovery"):
        if result.get(key) not in (None, "", [], {}):
            compact[key] = result[key]
    required_changes = result.get("required_changes")
    if isinstance(required_changes, list) and required_changes:
        compact["required_changes"] = required_changes[:8]
        if len(required_changes) > 8:
            compact["required_changes_omitted"] = len(required_changes) - 8
    if result.get("ok") is not True and isinstance(result.get("observed"), dict):
        observed = _provider_compact_observed(result["observed"])
        if observed:
            compact["observed"] = observed

    if program:
        actions: list[dict[str, Any]] = [
            {
                "tool": "vibescript.read_source",
                "arguments": {"program": program, "include_logs": False},
            }
        ]
        if revision:
            actions.append(
                {
                    "tool": "vibescript.build_program",
                    "arguments": {
                        "program": program,
                        "expected_revision": revision,
                    },
                }
            )
        compact["next_actions"] = actions
    return compact


def _provider_compact_terminal_operation(
    result: dict[str, Any],
) -> dict[str, Any]:
    """Keep one invariant terminal verdict below the provider byte boundary."""

    operation = result.get("operation")
    nested = result.get("result")
    if (
        not isinstance(operation, dict)
        or str(operation.get("status") or "") == "running"
        or not isinstance(nested, dict)
    ):
        return result
    compact_nested = {
        key: nested[key]
        for key in (
            "ok",
            "tool",
            "requested_action",
            "failure_code",
            "failure_stage",
            "error",
            "cancelled",
            "retry_same_call",
            "created",
            "updated",
            "changed",
            "deleted",
            "program",
            "revision",
            "working_revision",
            "accepted_revision",
            "state",
            "model_state",
            "outputs",
            "warnings",
            "phase_timings_seconds",
            "lifecycle_elapsed_seconds",
            "validation_scope",
            "next_action",
            "next_actions",
        )
        if nested.get(key) not in (None, "", [], {})
    }
    if nested.get("ok") is not True:
        observed = _provider_compact_observed(nested.get("observed"))
        if observed:
            compact_nested["observed"] = observed
        required_changes = nested.get("required_changes")
        if isinstance(required_changes, list) and required_changes:
            compact_nested["required_changes"] = required_changes[:8]
    compact = dict(result)
    compact["result"] = compact_nested
    return compact


def _provider_visible_source_read_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return source code, its concise state, and the exact legal next actions."""

    revision = str(result.get("current_revision") or "")
    compact = {
        key: result[key]
        for key in (
            "ok",
            "program",
            "source",
            "source_range",
            "expected_outputs",
        )
        if result.get(key) not in (None, "", [], {})
    }
    if revision:
        compact["revision"] = revision
    input_schema = result.get("input_schema")
    inputs = result.get("inputs")
    schema_properties = (
        dict(input_schema.get("properties") or {})
        if isinstance(input_schema, dict)
        else {}
    )
    if inputs or schema_properties:
        compact["input_schema"] = input_schema
        compact["inputs"] = inputs
    affected = [
        dict(value)
        for value in list(result.get("affected_outputs") or [])
        if isinstance(value, dict)
    ]
    if affected:
        compact["outputs"] = affected
    model_state = result.get("model_state")
    if isinstance(model_state, dict):
        state = {
            key: model_state[key]
            for key in ("status",)
            if model_state.get(key) not in (None, "", [], {})
        }
        if model_state.get("accepted_is_current") is False:
            state["accepted_is_current"] = False
        if (
            model_state.get("accepted_is_current") is False
            and "accepted_live_state_preserved" in model_state
        ):
            state["accepted_live_state_preserved"] = bool(
                model_state["accepted_live_state_preserved"]
            )
        accepted_revision = str(result.get("accepted_revision") or "")
        if accepted_revision and accepted_revision != revision:
            state["accepted_revision"] = accepted_revision
        if state:
            compact["state"] = state
    latest = result.get("latest_candidate")
    if isinstance(latest, dict) and latest.get("failure"):
        compact["latest_failure"] = latest["failure"]
    actions = []
    for key in ("edit_source", "build_program"):
        action = result.get(key)
        if isinstance(action, dict):
            actions.append(action)
    if actions:
        compact["next_actions"] = actions
    return compact


def _provider_visible_geometry_read_result(
    result: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    geometry = dict(result.get("geometry") or {})
    for key in ("face_details", "edge_details"):
        if not request.get("include_subelements"):
            geometry.pop(key, None)
    if not request.get("queries"):
        geometry.pop("query_results", None)
    geometry = {
        key: value
        for key, value in geometry.items()
        if value not in (None, "", [], {})
        and key not in {"subelement_detail_limit", "subelement_details_truncated"}
    }
    reference = dict(result.get("reference") or {})
    raw_object = dict(result.get("object") or {})
    obj = {
        "reference": reference,
        **{
            key: raw_object[key]
            for key in ("label", "type", "visible")
            if raw_object.get(key) not in (None, "", [], {})
        },
    }
    compact: dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "object": obj,
        "geometry": geometry,
    }
    placement = dict(result.get("placement") or {})
    matrix = placement.get("matrix_4x4_row_major")
    identity = [
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    ]
    if matrix and list(matrix) != identity:
        compact["placement"] = placement
    shape_revision = result.get("shape_revision")
    if isinstance(shape_revision, dict) and shape_revision.get("shape_hash") is not None:
        compact["selection_revision"] = {
            "shape_hash": shape_revision["shape_hash"],
            "rule": "Read geometry again after this object's topology changes.",
        }
    execution = result.get("execution")
    if isinstance(execution, dict) and execution.get("elapsed_seconds") is not None:
        compact["elapsed_seconds"] = execution["elapsed_seconds"]
    return compact


def _provider_json_bytes(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _provider_omitted_value(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "_vibecad_value_omitted": True,
        "reason": "provider_tool_result_byte_limit",
        "json_bytes": _provider_json_bytes(value),
    }
    if isinstance(value, dict):
        result.update({"value_type": "object", "entry_count": len(value)})
    elif isinstance(value, list):
        result.update({"value_type": "array", "item_count": len(value)})
    elif isinstance(value, str):
        result.update(
            {
                "value_type": "string",
                "characters": len(value),
                "utf8_bytes": len(value.encode("utf-8", errors="replace")),
            }
        )
    else:
        result["value_type"] = type(value).__name__
    return result


def _tool_result_image_context(result: dict[str, Any]) -> dict[str, Any] | None:
    attachment = result.get("_vibecad_image_attachment")
    if not isinstance(attachment, dict) or not str(attachment.get("path") or ""):
        return None
    return {
        "reference_images": {
            "count": 1,
            "images": [
                {
                    "id": "explicit-inspection",
                    "name": str(attachment.get("name") or "reference"),
                    "path": str(attachment["path"]),
                }
            ],
        }
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("Provider payload dictionaries must use string keys.")
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    raise TypeError(f"Provider payload contains non-JSON value {type(value).__name__}.")


def _capture_outbound_request(
    context: dict[str, Any],
    *,
    provider: str,
    sdk_call: str,
    turn: int,
    request: dict[str, Any],
    base_url: str | None,
    attempt: int = 1,
) -> dict[str, Any] | None:
    config = context.get("_vibecad_debug")
    if not isinstance(config, dict) or not config.get("enabled"):
        return None
    directory = str(config.get("capture_directory") or "").strip()
    if not directory:
        raise RuntimeError(
            "Context debugging is enabled without a provider request capture directory."
        )
    return capture_provider_request(
        directory=directory,
        provider=provider,
        sdk_call=sdk_call,
        turn=turn,
        attempt=attempt,
        request=_json_safe(request),
        base_url=base_url,
    )


def _object_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json", exclude_none=True)
        return payload if isinstance(payload, dict) else {}
    return {}


def _markdown_with_sources(text: str, sources: list[tuple[str, str]]) -> str:
    clean_text = str(text or "").strip()
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url, title in sources:
        clean_url = str(url or "").strip()
        if not clean_url or clean_url in seen or clean_url in clean_text:
            continue
        seen.add(clean_url)
        clean_title = str(title or "").strip() or clean_url
        clean_title = clean_title.replace("[", "").replace("]", "")
        unique.append((clean_url, clean_title))
    if not unique:
        return clean_text
    source_lines = [f"- [{title}]({url})" for url, title in unique]
    return clean_text + "\n\nSources:\n" + "\n".join(source_lines)


def _validate_provider_wire_surface(context: dict[str, Any]) -> None:
    """Apply the frozen resolver contract to every online provider transport."""

    # A few isolated transport tests and extension callers still supply schemas
    # without a session snapshot. Production sessions always include one. When
    # it is present, use the same strict validation as Codex before serializing
    # schemas for the Anthropic API.
    if "provider_tool_surface" in context:
        _codex_dynamic_tool_surface(context)


def _provider_qt_modules() -> tuple[Any, Any] | None:
    try:
        from PySide import QtCore, QtGui
    except ImportError:
        return None
    return QtCore, QtGui


def _provider_image_mime_for_suffix(suffix: str) -> str | None:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(str(suffix or "").lower())


def _provider_encoded_image_payload(
    path: Path,
    *,
    max_bytes: int = MAX_PROVIDER_IMAGE_BYTES,
    prefer_jpeg: bool = False,
) -> tuple[str, bytes, dict[str, Any]] | None:
    """Encode an oversized image into a provider-safe payload.

    This is intentionally provider-local instead of importing Core's attachment
    helper: provider payload limits are runtime concerns and this module must
    stay importable in the child process without creating Core/Session cycles.
    """
    qt_modules = _provider_qt_modules()
    if qt_modules is None:
        return None
    qt_core, qt_gui = qt_modules
    image = qt_gui.QImage(str(path))
    if image.isNull():
        return None
    width = int(image.width())
    height = int(image.height())
    if width <= 0 or height <= 0:
        return None

    original_format = {
        ".png": "PNG",
        ".jpg": "JPG",
        ".jpeg": "JPG",
        ".webp": "WEBP",
    }.get(path.suffix.lower(), "PNG")
    original_attempt = (
        original_format,
        _provider_image_mime_for_suffix(path.suffix) or "image/png",
        90,
    )
    jpeg_attempt = ("JPG", "image/jpeg", 90 if prefer_jpeg else 85)
    attempts: list[tuple[str, str, int]] = []
    if prefer_jpeg:
        attempts.append(jpeg_attempt)
    if original_attempt != jpeg_attempt:
        attempts.append(original_attempt)
    if original_format != "JPG" and not prefer_jpeg:
        attempts.append(jpeg_attempt)

    best: tuple[str, bytes, dict[str, Any]] | None = None
    long_edge = max(width, height)
    for encode_format, mime_type, starting_quality in attempts:
        edge = min(long_edge, PROVIDER_IMAGE_MAX_EDGE)
        quality = starting_quality
        for _attempt in range(10):
            scaled = image
            if max(width, height) > edge:
                scaled = image.scaled(
                    edge,
                    edge,
                    qt_core.Qt.KeepAspectRatio,
                    qt_core.Qt.SmoothTransformation,
                )
            buffer = qt_core.QBuffer()
            buffer.open(qt_core.QIODevice.WriteOnly)
            saved = scaled.save(buffer, encode_format, quality)
            payload = bytes(buffer.data())
            buffer.close()
            if saved and payload:
                metadata = {
                    "resized": (
                        int(scaled.width()) != width or int(scaled.height()) != height
                    ),
                    "transcoded": encode_format != original_format,
                    "encoded_format": encode_format.lower(),
                    "image_size": [int(scaled.width()), int(scaled.height())],
                    "size_bytes": len(payload),
                }
                candidate = (mime_type, payload, metadata)
                if best is None or len(payload) < len(best[1]):
                    best = candidate
                if len(payload) <= max_bytes:
                    return candidate
            if encode_format in {"JPG", "WEBP"} and quality > 40:
                quality -= 15
            elif edge > PROVIDER_IMAGE_MIN_EDGE:
                edge = max(PROVIDER_IMAGE_MIN_EDGE, int(edge * 0.75))
            else:
                break
    if best is not None and len(best[1]) <= max_bytes:
        return best
    return None


def _image_file_payload(
    path_text: Any,
    *,
    max_bytes: int = MAX_PROVIDER_IMAGE_BYTES,
    prefer_jpeg: bool = False,
) -> tuple[str, str] | None:
    """Return (mime_type, base64_data) for an image file, or None if unusable."""
    payload = _image_file_payload_with_status(
        path_text,
        max_bytes=max_bytes,
        prefer_jpeg=prefer_jpeg,
    )
    if not payload.get("available"):
        return None
    return str(payload["mime_type"]), str(payload["data"])


def _image_file_payload_with_status(
    path_text: Any,
    *,
    max_bytes: int = MAX_PROVIDER_IMAGE_BYTES,
    prefer_jpeg: bool = False,
) -> dict[str, Any]:
    """Return provider payload data plus explicit delivery status."""
    if not path_text:
        return {"available": False, "reason": "empty image path"}
    try:
        path = Path(str(path_text))
        if not path.is_file():
            return {"available": False, "reason": f"image file not found: {path}"}
        size = path.stat().st_size
        if size <= 0:
            return {"available": False, "reason": "image file is empty"}
        suffix = path.suffix.lower()
        mime_type = _provider_image_mime_for_suffix(suffix)
        if mime_type is None:
            return {
                "available": False,
                "reason": f"unsupported image type: {suffix or path.name}",
            }
        if size <= max_bytes:
            return {
                "available": True,
                "mime_type": mime_type,
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                "resized": False,
                "size_bytes": size,
            }
        encoded = _provider_encoded_image_payload(
            path,
            max_bytes=max_bytes,
            prefer_jpeg=prefer_jpeg,
        )
        if encoded is None:
            return {
                "available": False,
                "reason": (
                    f"image is {size} bytes and could not be resized below "
                    f"{max_bytes} bytes"
                ),
                "size_bytes": size,
            }
        encoded_mime, raw, metadata = encoded
        return {
            "available": True,
            "mime_type": encoded_mime,
            "data": base64.b64encode(raw).decode("ascii"),
            "resized": True,
            "source_size_bytes": size,
            **metadata,
        }
    except Exception as exc:
        return {"available": False, "reason": f"image payload failed: {exc}"}


def _screenshot_image_payload(
    context: dict[str, Any],
    *,
    max_bytes: int = MAX_PROVIDER_IMAGE_BYTES,
    prefer_jpeg: bool = False,
) -> tuple[str, str] | None:
    """Return (mime_type, base64_data) for the captured viewport screenshot."""
    screenshot = context.get("view_screenshot")
    if (
        not isinstance(screenshot, dict)
        or not screenshot.get("captured")
        or screenshot.get("pending_attachment") is not True
    ):
        return None
    return _image_file_payload(
        screenshot.get("path"),
        max_bytes=max_bytes,
        prefer_jpeg=prefer_jpeg,
    )


def _context_image_blocks(
    context: dict[str, Any],
    *,
    max_bytes: int = MAX_PROVIDER_IMAGE_BYTES,
    prefer_jpeg: bool = False,
) -> list[tuple[str, str, str]]:
    """Return labeled image payloads as (label_text, mime_type, base64_data)."""
    blocks: list[tuple[str, str, str]] = []
    references = context.get("reference_images")
    entries: list[dict[str, Any]] = []
    if isinstance(references, dict):
        raw_entries = references.get("images")
        if isinstance(raw_entries, list):
            entries = [entry for entry in raw_entries if isinstance(entry, dict)]
    usable: list[tuple[dict[str, Any], tuple[str, str]]] = []
    unavailable: list[dict[str, str]] = []
    for entry in entries:
        payload = _image_file_payload_with_status(
            entry.get("path"),
            max_bytes=max_bytes,
            prefer_jpeg=prefer_jpeg,
        )
        entry["provider_delivery"] = {
            key: value
            for key, value in payload.items()
            if key not in {"data", "mime_type"}
        }
        if payload.get("available"):
            usable.append((entry, (str(payload["mime_type"]), str(payload["data"]))))
        else:
            unavailable.append(
                {
                    "name": str(entry.get("name") or entry.get("id") or "reference"),
                    "reason": str(payload.get("reason") or "image unavailable"),
                }
            )
    if isinstance(references, dict):
        if unavailable:
            references["provider_delivery_notes"] = unavailable
        else:
            references.pop("provider_delivery_notes", None)
    total = len(usable)
    for index, (entry, (mime_type, image_data)) in enumerate(usable, start=1):
        name = str(entry.get("name") or f"reference-{index}")
        user_label = str(entry.get("label") or "").strip()
        suffix = f"|{user_label}" if user_label else ""
        label_text = f"R{index}/{total}:{name}{suffix}"
        blocks.append((label_text, mime_type, image_data))
    screenshot_payload = _screenshot_image_payload(
        context,
        max_bytes=max_bytes,
        prefer_jpeg=prefer_jpeg,
    )
    if screenshot_payload is not None:
        mime_type, image_data = screenshot_payload
        blocks.append(
            (
                "V:current",
                mime_type,
                image_data,
            )
        )
    return blocks


def _codex_context_image_blocks(
    context: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """Return inline images that fit the Codex app-server URL boundary."""
    return _context_image_blocks(
        context,
        max_bytes=CODEX_INLINE_IMAGE_MAX_BYTES,
        prefer_jpeg=True,
    )


def _context_image_delivery_notes(context: dict[str, Any]) -> list[str]:
    references = context.get("reference_images")
    if not isinstance(references, dict):
        return []
    notes = references.get("provider_delivery_notes")
    if not isinstance(notes, list):
        return []
    lines: list[str] = []
    for item in notes:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "reference")
        reason = str(item.get("reason") or "not delivered")
        lines.append(f"R_MISS:{name}|{reason}")
    return lines


def _anthropic_user_content(
    prompt: str, context: dict[str, Any]
) -> str | list[dict[str, Any]]:
    blocks = _context_image_blocks(context)
    delivery_notes = _context_image_delivery_notes(context)
    if not blocks and not delivery_notes:
        return prompt
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for note in delivery_notes:
        content.append({"type": "text", "text": note})
    for label_text, mime_type, image_data in blocks:
        content.append({"type": "text", "text": label_text})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": image_data,
                },
            }
        )
    return content


def _anthropic_visual_repin_content(
    context: dict[str, Any], screenshot_summary: dict[str, Any]
) -> list[dict[str, Any]]:
    if (
        not isinstance(screenshot_summary, dict)
        or not screenshot_summary.get("captured")
        or not screenshot_summary.get("new_observation", True)
    ):
        return []
    references = context.get("reference_images")
    has_references = bool(isinstance(references, dict) and references.get("images"))
    visual_context = {
        "view_screenshot": screenshot_summary,
    }
    if has_references:
        visual_context["reference_images"] = references
    blocks = _context_image_blocks(visual_context)
    if not blocks:
        return []
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "Current viewport observation captured after the preceding CAD operation.",
        }
    ]
    for label_text, mime_type, image_data in blocks:
        content.append({"type": "text", "text": label_text})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": image_data,
                },
            }
        )
    return content


def _anthropic_inspected_image_content(
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    context = _tool_result_image_context(result)
    if context is None:
        return []
    blocks = _context_image_blocks(context)
    content: list[dict[str, Any]] = [
        {"type": "text", "text": "Explicitly requested project reference image."}
    ]
    for label_text, mime_type, image_data in blocks:
        content.append({"type": "text", "text": label_text})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": image_data,
                },
            }
        )
    return content if len(content) > 1 else []


def _anthropic_thinking_config(reasoning_effort: str | None) -> dict[str, Any] | None:
    if _anthropic_adaptive_effort(reasoning_effort) is None:
        return None
    return {"type": "adaptive"}


def _anthropic_adaptive_effort(reasoning_effort: str | None) -> str | None:
    """Map the user setting to Anthropic's adaptive-thinking effort literal."""
    if not reasoning_effort:
        return None
    return ANTHROPIC_ADAPTIVE_EFFORT.get(str(reasoning_effort).strip().lower())


def _anthropic_request_tools(
    cad_tools: list[dict[str, Any]], web_search_enabled: bool
) -> list[dict[str, Any]]:
    tools = list(cad_tools)
    if web_search_enabled:
        tools.append(
            {
                "type": "web_search_20260318",
                "name": "web_search",
                "max_uses": 5,
                "allowed_callers": ["direct"],
            }
        )
    return tools


def _anthropic_final_text(content_blocks: list[Any]) -> str:
    parts: list[str] = []
    sources: list[tuple[str, str]] = []
    for block in content_blocks:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type != "text":
            continue
        text = getattr(block, "text", None) or (
            block.get("text") if isinstance(block, dict) else None
        )
        if text:
            parts.append(str(text))
        payload = _object_payload(block)
        for citation in payload.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            url = str(citation.get("url") or "").strip()
            if url:
                sources.append((url, str(citation.get("title") or "")))
    return _markdown_with_sources("\n\n".join(parts), sources)


def _anthropic_assistant_request_content(
    content_blocks: list[Any],
) -> list[dict[str, Any]]:
    request_blocks: list[dict[str, Any]] = []
    for block in content_blocks:
        block_type = _anthropic_block_type(block)
        if block_type == "text":
            text = getattr(block, "text", None) or (
                block.get("text") if isinstance(block, dict) else None
            )
            request_blocks.append({"type": "text", "text": str(text or "")})
            continue
        if block_type == "thinking":
            thinking = getattr(block, "thinking", None) or (
                block.get("thinking") if isinstance(block, dict) else None
            )
            signature = getattr(block, "signature", None) or (
                block.get("signature") if isinstance(block, dict) else None
            )
            item = {"type": "thinking", "thinking": str(thinking or "")}
            if signature:
                item["signature"] = str(signature)
            request_blocks.append(item)
            continue
        if block_type == "redacted_thinking":
            data = getattr(block, "data", None) or (
                block.get("data") if isinstance(block, dict) else None
            )
            item = {"type": "redacted_thinking"}
            if data:
                item["data"] = str(data)
            request_blocks.append(item)
            continue
        if block_type == "tool_use":
            block_id = getattr(block, "id", None) or (
                block.get("id") if isinstance(block, dict) else None
            )
            name = getattr(block, "name", None) or (
                block.get("name") if isinstance(block, dict) else None
            )
            tool_input = getattr(block, "input", None)
            if tool_input is None and isinstance(block, dict):
                tool_input = block.get("input")
            request_blocks.append(
                {
                    "type": "tool_use",
                    "id": str(block_id or ""),
                    "name": str(name or ""),
                    "input": _json_safe(tool_input or {}),
                }
            )
            continue
        payload = _object_payload(block)
        if payload:
            request_blocks.append(_json_safe(payload))
    return request_blocks


def _anthropic_block_type(block: Any) -> str:
    block_type = getattr(block, "type", None) or (
        block.get("type") if isinstance(block, dict) else None
    )
    return str(block_type or "unknown")


def _anthropic_response_summary(response: Any) -> dict[str, Any]:
    blocks = list(getattr(response, "content", []) or [])
    counts: dict[str, int] = {}
    text_chars = 0
    thinking_chars = 0
    tool_names: list[str] = []
    for block in blocks:
        block_type = _anthropic_block_type(block)
        counts[block_type] = counts.get(block_type, 0) + 1
        if block_type == "text":
            text = getattr(block, "text", None) or (
                block.get("text") if isinstance(block, dict) else None
            )
            if text:
                text_chars += len(str(text))
        elif block_type == "thinking":
            thinking = getattr(block, "thinking", None) or (
                block.get("thinking") if isinstance(block, dict) else None
            )
            if thinking:
                thinking_chars += len(str(thinking))
        elif block_type == "tool_use":
            name = getattr(block, "name", None) or (
                block.get("name") if isinstance(block, dict) else None
            )
            if name:
                tool_names.append(str(name))
    return {
        "stop_reason": str(getattr(response, "stop_reason", "") or ""),
        "block_counts": counts,
        "text_chars": text_chars,
        "thinking_chars": thinking_chars,
        "tool_names": tool_names[:8],
        "tool_name_count": len(tool_names),
    }


def _anthropic_stream_event_summary(event: Any) -> dict[str, Any]:
    event_type = getattr(event, "type", None) or (
        event.get("type") if isinstance(event, dict) else None
    )
    summary: dict[str, Any] = {"stream_event_type": str(event_type or "unknown")}
    block = getattr(event, "content_block", None) or (
        event.get("content_block") if isinstance(event, dict) else None
    )
    if block is not None:
        summary["block_type"] = _anthropic_block_type(block)
        name = getattr(block, "name", None) or (
            block.get("name") if isinstance(block, dict) else None
        )
        if name:
            summary["tool_name"] = str(name)
    delta = getattr(event, "delta", None) or (
        event.get("delta") if isinstance(event, dict) else None
    )
    if delta is not None:
        delta_type = getattr(delta, "type", None) or (
            delta.get("type") if isinstance(delta, dict) else None
        )
        if delta_type:
            summary["delta_type"] = str(delta_type)
        stop_reason = getattr(delta, "stop_reason", None) or (
            delta.get("stop_reason") if isinstance(delta, dict) else None
        )
        if stop_reason:
            summary["stop_reason"] = str(stop_reason)
        text = getattr(delta, "text", None) or (
            delta.get("text") if isinstance(delta, dict) else None
        )
        if text and str(delta_type or "") == "text_delta":
            summary["text_delta"] = str(text)
        thinking = getattr(delta, "thinking", None) or (
            delta.get("thinking") if isinstance(delta, dict) else None
        )
        if thinking and str(delta_type or "") == "thinking_delta":
            summary["reasoning_delta"] = str(thinking)
    return summary


def _short_provider_error(exc: BaseException, limit: int = 180) -> str:
    text = " ".join(str(exc or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _is_retryable_anthropic_stream_error(
    exc: BaseException,
    anthropic_module: Any | None = None,
) -> bool:
    if anthropic_module is not None:
        for name in ("APIConnectionError", "APITimeoutError"):
            error_type = getattr(anthropic_module, name, None)
            if error_type is not None and isinstance(exc, error_type):
                return True
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(chain) < 6:
        chain.append(current)
        current = current.__cause__ or current.__context__
    text = " | ".join(f"{item.__class__.__name__}: {item}" for item in chain).lower()
    retry_tokens = (
        "api connection",
        "api timeout",
        "broken pipe",
        "connection aborted",
        "connection reset",
        "connection timed out",
        "incomplete chunked read",
        "peer closed connection",
        "readerror",
        "read error",
        "readtimeout",
        "remoteprotocolerror",
        "server disconnected",
    )
    return any(token in text for token in retry_tokens)


def _bounded_compaction_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    marker = "\n...[omitted from compaction input]...\n"
    remaining = max(0, limit - len(marker))
    head = remaining // 2
    return text[:head] + marker + text[-(remaining - head) :]


def _bounded_compaction_value(value: Any, *, depth: int = 0) -> Any:
    """Bound small semantic values without forwarding provider-sized payloads."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _bounded_compaction_text(value, 1200)
    if depth >= 3:
        if isinstance(value, dict):
            return {"omitted_mapping_entries": len(value)}
        if isinstance(value, (list, tuple)):
            return {"omitted_list_items": len(value)}
        return str(type(value).__name__)
    if isinstance(value, dict):
        items = list(value.items())
        bounded = {
            str(key): _bounded_compaction_value(item, depth=depth + 1)
            for key, item in items[:16]
        }
        if len(items) > 16:
            bounded["omitted_mapping_entries"] = len(items) - 16
        return bounded
    if isinstance(value, (list, tuple)):
        bounded = [
            _bounded_compaction_value(item, depth=depth + 1)
            for item in list(value)[:12]
        ]
        if len(value) > 12:
            bounded.append({"omitted_list_items": len(value) - 12})
        return bounded
    return _bounded_compaction_text(value, 240)


def _anthropic_prompt_compaction_context(prompt: str) -> dict[str, Any]:
    """Extract conversation intent while excluding deterministic CAD state."""

    text = str(prompt or "")
    recent_marker = "RECENT_CONVERSATION_JSON\n"
    recent_end = "\nEND_RECENT_CONVERSATION_JSON\n\n"
    conversation: list[dict[str, str]] = []
    current_request = text
    if recent_marker in text and recent_end in text:
        recent_text = text.split(recent_marker, 1)[1].split(recent_end, 1)[0]
        try:
            recent_payload = json.loads(recent_text)
        except Exception:
            recent_payload = {}
        if isinstance(recent_payload, dict):
            for item in list(recent_payload.get("turns") or [])[-6:]:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").strip().lower()
                content = str(item.get("content") or "").strip()
                if role in {"user", "assistant"} and content:
                    conversation.append(
                        {
                            "role": role,
                            "content": _bounded_compaction_text(content, 1600),
                        }
                    )
        request_section = text.split(recent_end, 1)[1]
        if "\n" in request_section:
            _section_name, current_request = request_section.split("\n", 1)
        else:
            current_request = request_section
    return {
        "current_request": _bounded_compaction_text(current_request, 8000),
        "recent_conversation": conversation,
    }


_COMPACTION_ARGUMENT_KEYS = {
    "program",
    "reference",
    "references",
    "object",
    "object_name",
    "object_names",
    "expected_revision",
    "operation",
    "query",
    "names",
    "groups",
    "inputs",
    "expected_outputs",
    "frame",
    "camera",
}

_COMPACTION_RESULT_KEYS = {
    "ok",
    "error",
    "failure_code",
    "failure_stage",
    "cancelled",
    "retry_same_call",
    "created",
    "updated",
    "changed",
    "deleted",
    "operation",
    "document",
    "object",
    "object_name",
    "assembly",
    "program",
    "working_revision",
    "accepted_revision",
    "current_revision",
    "revision",
    "model_state",
    "affected_outputs",
    "expected_outputs",
    "transaction",
    "verification",
    "requested",
    "observed",
}


def _anthropic_compaction_tool_event(
    tool_name: str,
    arguments: Any,
    result: Any,
) -> dict[str, Any]:
    safe_arguments = arguments if isinstance(arguments, dict) else {}
    argument_summary = {
        key: _bounded_compaction_value(value)
        for key, value in safe_arguments.items()
        if key in _COMPACTION_ARGUMENT_KEYS
    }
    if "source" in safe_arguments:
        source = str(safe_arguments.get("source") or "")
        argument_summary["source"] = {
            "omitted": "readable_source_text",
            "characters": len(source),
        }
    if "input_schema" in safe_arguments:
        argument_summary["input_schema"] = {
            "omitted": "readable_input_schema"
        }

    def project_result(value: Any, depth: int = 0) -> dict[str, Any]:
        if not isinstance(value, dict) or depth >= 3:
            return {}
        projected = {
            str(key): _bounded_compaction_value(item)
            for key, item in value.items()
            if key in _COMPACTION_RESULT_KEYS
        }
        for container_key in ("result", "diagnostic"):
            nested = project_result(value.get(container_key), depth + 1)
            if nested:
                projected[container_key] = nested
        return projected

    return {
        "tool": str(tool_name or "unknown"),
        "arguments": argument_summary,
        "result": project_result(result),
    }


def _anthropic_turn_compaction_packet(
    *,
    prompt: str,
    tool_events: list[dict[str, Any]],
    assistant_progress: list[str],
    previous_compaction: dict[str, Any] | None,
    generation: int,
) -> dict[str, Any]:
    packet = {
        "trigger": "assistant_output_budget_exhausted",
        "generation": generation,
        **_anthropic_prompt_compaction_context(prompt),
        "assistant_visible_progress": [
            _bounded_compaction_text(item, 1600)
            for item in assistant_progress[-4:]
            if str(item or "").strip()
        ],
        "cad_tool_events": list(tool_events[-24:]),
    }
    if previous_compaction:
        packet["previous_compaction"] = _bounded_compaction_value(
            previous_compaction
        )

    def packet_bytes() -> int:
        return len(
            json.dumps(
                packet, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        )

    while (
        packet_bytes() > ANTHROPIC_TURN_COMPACTION_MAX_INPUT_BYTES
        and len(packet["recent_conversation"]) > 2
    ):
        packet["recent_conversation"].pop(0)
    while (
        packet_bytes() > ANTHROPIC_TURN_COMPACTION_MAX_INPUT_BYTES
        and len(packet["cad_tool_events"]) > 8
    ):
        packet["cad_tool_events"].pop(0)
    while (
        packet_bytes() > ANTHROPIC_TURN_COMPACTION_MAX_INPUT_BYTES
        and packet["assistant_visible_progress"]
    ):
        packet["assistant_visible_progress"].pop(0)
    if packet_bytes() > ANTHROPIC_TURN_COMPACTION_MAX_INPUT_BYTES:
        packet["current_request"] = _bounded_compaction_text(
            packet["current_request"], 3000
        )
        packet.pop("previous_compaction", None)
    if packet_bytes() > ANTHROPIC_TURN_COMPACTION_MAX_INPUT_BYTES:
        raise RuntimeError(
            "Bounded Anthropic turn-compaction packet exceeded its fixed byte limit."
        )
    return packet


def _anthropic_turn_compaction_tool() -> dict[str, Any]:
    string_list = {
        "type": "array",
        "items": {"type": "string", "maxLength": 1200},
        "maxItems": 32,
    }
    return {
        "name": "commit_turn_compaction",
        "description": "Commit the concise state required to continue this turn.",
        "input_schema": {
            "type": "object",
            "properties": {
                "current_request": {"type": "string", "maxLength": 6000},
                "requirements": string_list,
                "completed_actions": string_list,
                "live_artifacts": string_list,
                "open_issues": string_list,
                "next_action": {"type": "string", "maxLength": 1600},
            },
            "required": [
                "current_request",
                "requirements",
                "completed_actions",
                "live_artifacts",
                "open_issues",
                "next_action",
            ],
            "additionalProperties": False,
        },
    }


def _anthropic_compact_turn_in_thread(
    *,
    anthropic_module: Any,
    client_kwargs: dict[str, Any],
    model: str,
    packet: dict[str, Any],
    debug_context: dict[str, Any],
    base_url: str | None,
    generation: int,
) -> dict[str, Any]:
    """Run a tool-less, bounded compaction request outside the provider loop thread."""

    tool = _anthropic_turn_compaction_tool()
    request = {
        "model": model,
        "max_tokens": ANTHROPIC_TURN_COMPACTION_MAX_TOKENS,
        "system": ANTHROPIC_TURN_COMPACTION_INSTRUCTIONS,
        "messages": [
            {
                "role": "user",
                "content": json.dumps(
                    packet,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        ],
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": tool["name"]},
    }
    _capture_outbound_request(
        debug_context,
        provider="anthropic",
        sdk_call="Anthropic.messages.create.turn_compaction",
        turn=generation,
        request=request,
        base_url=base_url,
    )
    result: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            response = anthropic_module.Anthropic(
                **dict(client_kwargs)
            ).messages.create(**request)
            calls = [
                block
                for block in list(getattr(response, "content", []) or [])
                if _anthropic_block_type(block) == "tool_use"
            ]
            if len(calls) != 1:
                raise RuntimeError(
                    "Anthropic turn compaction did not return exactly one "
                    "structured state call."
                )
            call = calls[0]
            call_name = getattr(call, "name", None) or _object_payload(call).get(
                "name"
            )
            if str(call_name or "") != tool["name"]:
                raise RuntimeError("Anthropic turn compaction called the wrong tool.")
            value = getattr(call, "input", None)
            if value is None:
                value = _object_payload(call).get("input")
            if not isinstance(value, dict):
                raise RuntimeError("Anthropic turn compaction returned invalid state.")
            result.append(_json_safe(value))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(
        target=worker,
        name="VibeCAD-Anthropic-Turn-Compaction",
        daemon=True,
    )
    thread.start()
    while thread.is_alive():
        thread.join(0.05)
    if errors:
        raise RuntimeError(
            "Anthropic turn compaction failed: " + _short_provider_error(errors[0])
        ) from errors[0]
    if len(result) != 1:
        raise RuntimeError("Anthropic turn compaction returned no state.")
    return result[0]


def _anthropic_compaction_resume_state(context: dict[str, Any]) -> dict[str, Any]:
    """Keep only live pointers that the continuing model cannot safely guess."""

    state: dict[str, Any] = {}
    for key in ("workbench", "modeling_surface", "document", "selection"):
        value = context.get(key)
        if value not in (None, "", [], {}):
            state[key] = _bounded_compaction_value(value)
    editable = context.get("editable_sources")
    if isinstance(editable, dict):
        source_keys = {
            "program",
            "name",
            "program_name",
            "label",
            "current_revision",
            "working_revision",
            "accepted_revision",
            "status",
            "model_state",
            "affected_outputs",
            "expected_outputs",
        }
        source_pointers = []
        for source in list(editable.get("sources") or [])[:64]:
            if not isinstance(source, dict):
                continue
            source_pointers.append(
                {
                    key: _bounded_compaction_value(value)
                    for key, value in source.items()
                    if key in source_keys
                }
            )
        state["editable_source_pointers"] = source_pointers
    return state


def _anthropic_compaction_resume_message(
    compaction: dict[str, Any], context: dict[str, Any]
) -> str:
    return json.dumps(
        {
            "instruction": (
                "Continue the same unfinished request from this compacted state. "
                "Use read tools for omitted source, geometry, API, or catalog data."
            ),
            "compacted_turn": compaction,
            "live_state": _anthropic_compaction_resume_state(context),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _anthropic_child_main(
    conn,
    prompt: str,
    context: dict[str, Any],
    model: str,
    api_key: str | None,
    reasoning_effort: str | None,
    timeout_seconds: float | None,
    max_turns: int | None,
    clear_inherited_modules: bool,
    base_url: str | None = None,
) -> None:
    try:
        if clear_inherited_modules:
            _clear_inherited_sdk_modules()
        import anthropic
    except Exception as exc:
        _send_child_error(
            conn,
            "Anthropic SDK initialization",
            ProviderUnavailable(
                "Install the bundled 'anthropic' package and configure authentication. "
                f"({exc})"
            ),
        )
        conn.close()
        return

    try:
        live_context = dict(context)
        web_search_enabled = _provider_option(live_context, "web_search_enabled")
        compaction_model = str(
            _provider_option_value(live_context, "compaction_model") or model
        ).strip() or model

        def build_tool_surface(
            surface_context: dict[str, Any],
        ) -> tuple[dict[str, str], list[dict[str, Any]]]:
            _validate_provider_wire_surface(surface_context)
            by_name: dict[str, str] = {}
            definitions: list[dict[str, Any]] = []
            for index, schema in enumerate(
                surface_context.get("provider_tool_schemas") or []
            ):
                if not isinstance(schema, dict):
                    raise ValueError(f"Provider tool schema {index} must be an object.")
                tool_name = str(schema.get("name") or "").strip()
                if not tool_name:
                    raise ValueError(f"Provider tool schema {index} is missing name.")
                definition = _anthropic_tool_definition(schema)
                function_name = str(definition["name"])
                if function_name in by_name:
                    raise ValueError(
                        f"Duplicate provider function name: {function_name}"
                    )
                by_name[function_name] = tool_name
                definitions.append(definition)
            return by_name, definitions

        tools_by_name, tool_definitions = build_tool_surface(live_context)
        thinking = _anthropic_thinking_config(reasoning_effort)
        max_tokens = DEFAULT_ANTHROPIC_MAX_TOKENS
        if thinking is not None:
            max_tokens += int(
                ANTHROPIC_THINKING_BUDGETS[str(reasoning_effort).strip().lower()]
            )

        system_blocks = _anthropic_system_blocks(live_context)
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": _anthropic_user_content(
                    prompt, _model_visible_context(live_context)
                ),
            }
        ]
        tool_events: list[dict[str, Any]] = []
        assistant_progress: list[str] = []
        previous_compaction: dict[str, Any] | None = None
        compaction_count = 0

        client_kwargs: dict[str, Any] = {"max_retries": 2}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url
        if timeout_seconds is not None and timeout_seconds > 0:
            client_kwargs["timeout"] = timeout_seconds
        client = anthropic.Anthropic(**client_kwargs)

        request_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "tools": _anthropic_request_tools(tool_definitions, web_search_enabled),
        }
        if thinking is not None:
            request_kwargs["thinking"] = thinking
            request_kwargs["output_config"] = {
                "effort": _anthropic_adaptive_effort(reasoning_effort)
            }

        def _stream_response(turn: int, attempt: int) -> Any:
            # The SDK rejects non-streaming requests that could exceed ten
            # minutes (large max_tokens plus thinking budgets), so always
            # stream and accumulate the final message.
            system_blocks = _anthropic_system_blocks(live_context)
            sdk_request = {
                "messages": messages,
                **request_kwargs,
                "system": system_blocks,
            }
            _capture_outbound_request(
                live_context,
                provider="anthropic",
                sdk_call="Anthropic.messages.stream",
                turn=turn,
                attempt=attempt,
                request=sdk_request,
                base_url=base_url,
            )
            _send_child_progress(
                conn,
                {
                    "event": "anthropic_request_started",
                    "turn": turn,
                    "attempt": attempt,
                    "model": model,
                    "message_count": len(messages),
                    "tool_count": len(request_kwargs["tools"]),
                    "max_tokens": max_tokens,
                    "thinking": request_kwargs.get("thinking"),
                    "output_config": request_kwargs.get("output_config"),
                },
            )
            delta_batcher = _ProviderStreamDeltaBatcher(
                lambda event: _send_child_progress(conn, event),
                provider="Anthropic",
                turn=turn,
            )
            with client.messages.stream(**sdk_request) as stream:
                event_count = 0
                last_delta_notice_at = 0.0
                try:
                    iterator = iter(stream)
                except TypeError:
                    _send_child_progress(
                        conn,
                        {
                            "event": "anthropic_stream_waiting",
                            "turn": turn,
                        },
                    )
                    return stream.get_final_message()
                for stream_event in iterator:
                    event_count += 1
                    summary = _anthropic_stream_event_summary(stream_event)
                    stream_event_type = summary.get("stream_event_type")
                    delta_type = summary.get("delta_type")
                    text_delta = summary.get("text_delta")
                    if text_delta:
                        delta_batcher.append(
                            "provider_text_delta",
                            text_delta,
                        )
                    reasoning_delta = summary.get("reasoning_delta")
                    if reasoning_delta:
                        delta_batcher.append(
                            "provider_reasoning_delta",
                            reasoning_delta,
                        )
                    if (
                        stream_event_type == "content_block_start"
                        and summary.get("block_type") == "server_tool_use"
                        and summary.get("tool_name") == "web_search"
                    ):
                        _send_child_progress(
                            conn,
                            {
                                "event": "provider_web_search_started",
                                "provider": "Anthropic",
                                "turn": turn,
                            },
                        )
                    elif (
                        stream_event_type == "content_block_start"
                        and summary.get("block_type") == "web_search_tool_result"
                    ):
                        _send_child_progress(
                            conn,
                            {
                                "event": "provider_web_search_completed",
                                "provider": "Anthropic",
                                "turn": turn,
                                "query": "",
                            },
                        )
                    now = time.monotonic()
                    should_report = stream_event_type in {
                        "message_start",
                        "content_block_start",
                        "content_block_stop",
                        "message_delta",
                        "message_stop",
                    }
                    if (
                        not should_report
                        and delta_type
                        and now - last_delta_notice_at >= 5.0
                    ):
                        should_report = True
                        last_delta_notice_at = now
                    if should_report:
                        delta_batcher.flush()
                        event = {
                            "event": "anthropic_stream_event",
                            "turn": turn,
                            "event_count": event_count,
                        }
                        event.update(summary)
                        _send_child_progress(conn, event)
                delta_batcher.flush()
                _send_child_progress(
                    conn,
                    {
                        "event": "anthropic_stream_completed",
                        "turn": turn,
                        "event_count": event_count,
                    },
                )
                return stream.get_final_message()

        def _stream_response_with_retries(turn: int) -> Any:
            for attempt in range(1, ANTHROPIC_STREAM_MAX_ATTEMPTS + 1):
                try:
                    return _stream_response(turn, attempt)
                except anthropic.BadRequestError:
                    raise
                except Exception as exc:
                    if (
                        attempt >= ANTHROPIC_STREAM_MAX_ATTEMPTS
                        or not _is_retryable_anthropic_stream_error(exc, anthropic)
                    ):
                        raise
                    _send_child_progress(
                        conn,
                        {
                            "event": "anthropic_stream_retrying",
                            "turn": turn,
                            "attempt": attempt,
                            "next_attempt": attempt + 1,
                            "max_attempts": ANTHROPIC_STREAM_MAX_ATTEMPTS,
                            "error": _short_provider_error(exc),
                        },
                    )
                    time.sleep(min(2.0, 0.25 * attempt))
            raise RuntimeError("Anthropic stream retry loop exited unexpectedly.")

        turn = 1
        while max_turns is None or max_turns <= 0 or turn <= max_turns:
            response = _stream_response_with_retries(turn)
            content_blocks = list(response.content)
            response_text = _anthropic_final_text(content_blocks)
            _send_child_progress(
                conn,
                {
                    "event": "anthropic_response_received",
                    "turn": turn,
                    **_anthropic_response_summary(response),
                },
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": _anthropic_assistant_request_content(content_blocks),
                }
            )
            if response_text.strip():
                assistant_progress.append(response_text.strip())
            if response.stop_reason == "max_tokens":
                if compaction_count >= ANTHROPIC_TURN_COMPACTION_MAX_ATTEMPTS:
                    raise RuntimeError(
                        "Anthropic exhausted its output budget after "
                        f"{compaction_count} compacted continuations."
                    )
                compaction_count += 1
                packet = _anthropic_turn_compaction_packet(
                    prompt=prompt,
                    tool_events=tool_events,
                    assistant_progress=assistant_progress,
                    previous_compaction=previous_compaction,
                    generation=compaction_count,
                )
                packet_bytes = len(
                    json.dumps(
                        packet,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                _send_child_progress(
                    conn,
                    {
                        "event": "anthropic_turn_compaction_started",
                        "turn": turn,
                        "generation": compaction_count,
                        "model": compaction_model,
                        "input_bytes": packet_bytes,
                    },
                )
                previous_compaction = _anthropic_compact_turn_in_thread(
                    anthropic_module=anthropic,
                    client_kwargs=client_kwargs,
                    model=compaction_model,
                    packet=packet,
                    debug_context=live_context,
                    base_url=base_url,
                    generation=compaction_count,
                )
                messages = [
                    {
                        "role": "user",
                        "content": _anthropic_compaction_resume_message(
                            previous_compaction, live_context
                        ),
                    }
                ]
                _send_child_progress(
                    conn,
                    {
                        "event": "anthropic_turn_compaction_completed",
                        "turn": turn,
                        "generation": compaction_count,
                        "model": compaction_model,
                        "resumed_message_count": len(messages),
                    },
                )
                turn += 1
                continue
            if response.stop_reason == "pause_turn":
                turn += 1
                continue
            tool_use_blocks = [
                block
                for block in content_blocks
                if getattr(block, "type", None) == "tool_use"
            ]
            if response.stop_reason != "tool_use" or not tool_use_blocks:
                final_output = response_text.strip()
                if not final_output:
                    summary = _anthropic_response_summary(response)
                    raise RuntimeError(
                        "Anthropic completed the turn without any user-visible text "
                        f"(stop_reason={summary['stop_reason'] or 'unknown'}, "
                        f"blocks={summary['block_counts']})."
                    )
                conn.send(
                    {
                        "type": "done",
                        "final_output": final_output,
                        "raw": None,
                    }
                )
                return
            server_use_ids = {
                str(getattr(block, "id", "") or _object_payload(block).get("id") or "")
                for block in content_blocks
                if _anthropic_block_type(block) == "server_tool_use"
            }
            server_result_ids = {
                str(
                    getattr(block, "tool_use_id", "")
                    or _object_payload(block).get("tool_use_id")
                    or ""
                )
                for block in content_blocks
                if _anthropic_block_type(block).endswith("_tool_result")
            }
            pending_server_tool = bool(server_use_ids - server_result_ids)
            tool_results: list[dict[str, Any]] = []
            visual_repin_blocks: list[dict[str, Any]] = []
            for block in tool_use_blocks:
                tool_name = tools_by_name.get(block.name)
                updated_context = None
                if tool_name is None:
                    result: Any = {
                        "ok": False,
                        "error": f"Unknown VibeCAD tool: {block.name}",
                    }
                else:
                    arguments_json = json.dumps(_json_safe(block.input or {}))
                    conn.send(
                        {
                            "type": "tool",
                            "tool_name": tool_name,
                            "arguments_json": arguments_json,
                            "provider_call_id": str(block.id),
                        }
                    )
                    bridge = conn.recv()
                    if bridge.get("type") != "tool_result":
                        raise RuntimeError("Invalid VibeCAD tool bridge response.")
                    result = bridge.get("result")
                    if not isinstance(result, dict):
                        result = {
                            "ok": False,
                            "error": "VibeCAD tool returned no structured result.",
                        }
                    updated_context = bridge.get("context")
                if isinstance(updated_context, dict):
                    live_context = updated_context
                    tools_by_name, tool_definitions = build_tool_surface(live_context)
                    request_kwargs["tools"] = _anthropic_request_tools(
                        tool_definitions, web_search_enabled
                    )
                if isinstance(result, dict):
                    result["vibecad_state_after"] = _provider_state_after_tool(
                        live_context,
                        result,
                    )
                if (
                    tool_name == "core.capture_view_screenshot"
                    and not pending_server_tool
                ):
                    screenshot_summary = (
                        result.get("result")
                        if isinstance(result, dict)
                        and isinstance(result.get("result"), dict)
                        else result
                    )
                    if isinstance(screenshot_summary, dict):
                        visual_repin_blocks.extend(
                            _anthropic_visual_repin_content(
                                live_context, screenshot_summary
                            )
                        )
                if isinstance(result, dict) and not pending_server_tool:
                    visual_repin_blocks.extend(
                        _anthropic_inspected_image_content(result)
                    )
                tool_events.append(
                    _anthropic_compaction_tool_event(
                        tool_name or str(getattr(block, "name", "") or "unknown"),
                        getattr(block, "input", None) or {},
                        result,
                    )
                )
                visible_result = (
                    _provider_visible_tool_result(result)
                    if isinstance(result, dict)
                    else result
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(_json_safe(visible_result)),
                    }
                )
            messages.append(
                {"role": "user", "content": [*tool_results, *visual_repin_blocks]}
            )
            turn += 1
        conn.send(
            {
                "type": "error",
                "error": "Anthropic provider turn limit reached.",
            }
        )
    except BaseException as exc:
        _send_child_error(conn, "Anthropic provider", exc)
    finally:
        conn.close()


def _clear_inherited_sdk_modules() -> None:
    for name in list(sys.modules):
        if (
            name == "pydantic"
            or name.startswith("pydantic.")
            or name == "anthropic"
            or name.startswith("anthropic.")
            or name == "httpx"
            or name.startswith("httpx.")
        ):
            sys.modules.pop(name, None)
