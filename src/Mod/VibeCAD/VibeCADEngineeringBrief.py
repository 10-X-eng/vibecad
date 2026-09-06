# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-mutating, durable Engineering Brief workflow for VibeCAD."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import threading
from typing import Any, Callable, Mapping

from VibeCADProject import now_iso
from VibeCADVibeScriptFileIO import atomic_write_text, open_shared_binary

ENGINEERING_BRIEF_SCHEMA = "vibecad-engineering-brief-v1"
ENGINEERING_BRIEF_VERSION = 1
ENGINEERING_BRIEFS_DIRECTORY = "engineering-briefs"

BRIEF_FIELD_ORDER = (
    "objective",
    "deliverables",
    "existing_geometry",
    "units",
    "dimensions",
    "materials",
    "interfaces",
    "loads",
    "manufacturing",
    "tolerances",
    "analyses",
    "acceptance_criteria",
    "requirements",
    "preferences",
)

BRIEF_FIELD_LABELS = {
    "objective": "Objective",
    "deliverables": "Deliverables",
    "existing_geometry": "Existing geometry and document context",
    "units": "Units",
    "dimensions": "Dimensions",
    "materials": "Materials",
    "interfaces": "Interfaces and constraints",
    "loads": "Loads and operating conditions",
    "manufacturing": "Manufacturing",
    "tolerances": "Tolerances",
    "analyses": "Required analyses and drawings",
    "acceptance_criteria": "Acceptance criteria",
    "requirements": "Hard requirements",
    "preferences": "Preferences",
}

ENGINEERING_BRIEF_TASK_INSTRUCTIONS = """You are VibeCAD's non-mutating Engineering Brief assistant.

Help an engineer convert an incomplete request into a precise, reviewable brief for a separate CAD agent. Use the supplied active-conversation, document, selection, workbench, and unit context instead of asking for facts already known. Treat requirements and rejected directions from the active conversation as established context, while letting the user's current request control. Distinguish hard requirements from preferences. Ask exactly one highest-value question per response when a missing answer could materially change function, geometry, analysis, manufacture, safety, or acceptance. Do not interrogate indefinitely: if the user says to use best judgment, record transparent assumptions and move forward. Never silently invent consequential dimensions, loads, materials, tolerances, standards, or safety factors.

Do not call or request CAD tools, mutate the document, claim CAD work was performed, or instruct the user to perform an unrelated workflow. Return only one JSON object matching the response contract in the current prompt. Do not wrap it in prose or Markdown."""

_CONVERSATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_STORE_LOCK = threading.RLock()


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _clean_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"Engineering Brief {field} must be an array.")
    return [clean for item in value if (clean := _clean_string(item))]


def _json_copy(value: Any, field: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Engineering Brief {field} must be JSON serializable."
        ) from exc


def _empty_brief(original_request: str) -> dict[str, Any]:
    return {
        "objective": _clean_string(original_request),
        "deliverables": [],
        "existing_geometry": [],
        "units": "",
        "dimensions": [],
        "materials": [],
        "interfaces": [],
        "loads": [],
        "manufacturing": [],
        "tolerances": [],
        "analyses": [],
        "acceptance_criteria": [],
        "requirements": [],
        "preferences": [],
    }


def _normalized_brief(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Engineering Brief brief must be an object.")
    normalized: dict[str, Any] = {}
    for field in BRIEF_FIELD_ORDER:
        raw = value.get(field, "" if field in {"objective", "units"} else [])
        if field in {"objective", "units"}:
            if not isinstance(raw, str):
                raise ValueError(f"Engineering Brief brief.{field} must be a string.")
            normalized[field] = _clean_string(raw)
        else:
            normalized[field] = _clean_string_list(raw, f"brief.{field}")
    return normalized


def _normalized_transcript(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("Engineering Brief transcript must be an array.")
    transcript: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("Engineering Brief transcript entries must be objects.")
        role = _clean_string(item.get("role")).lower()
        content = _clean_string(item.get("content"))
        if role not in {"user", "assistant"} or not content:
            raise ValueError(
                "Engineering Brief transcript entries require a user/assistant role "
                "and non-empty content."
            )
        transcript.append({"role": role, "content": content})
    return transcript


def _validated_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Engineering Brief state must be an object.")
    if value.get("schema") != ENGINEERING_BRIEF_SCHEMA:
        raise ValueError("Engineering Brief state has an unsupported schema.")
    if value.get("version") != ENGINEERING_BRIEF_VERSION:
        raise ValueError("Engineering Brief state has an unsupported version.")
    document_uid = _clean_string(value.get("document_uid"))
    if not document_uid:
        raise ValueError("Engineering Brief state requires a document_uid.")
    conversation_id = _clean_string(value.get("conversation_id")).lower()
    if _CONVERSATION_ID_PATTERN.fullmatch(conversation_id) is None:
        raise ValueError(
            "Engineering Brief state requires a 32-character conversation_id."
        )
    original_request = _clean_string(value.get("original_request"))
    ready = value.get("ready")
    if not isinstance(ready, bool):
        raise ValueError("Engineering Brief ready must be a boolean.")
    next_question = _clean_string(value.get("next_question"))
    if not ready and value.get("transcript") and not next_question:
        raise ValueError(
            "An unfinished Engineering Brief response requires next_question."
        )
    return {
        "schema": ENGINEERING_BRIEF_SCHEMA,
        "version": ENGINEERING_BRIEF_VERSION,
        "document_uid": document_uid,
        "conversation_id": conversation_id,
        "original_request": original_request,
        "context": _json_copy(value.get("context") or {}, "context"),
        "transcript": _normalized_transcript(value.get("transcript") or []),
        "brief": _normalized_brief(value.get("brief") or {}),
        "assumptions": _clean_string_list(
            value.get("assumptions") or [], "assumptions"
        ),
        "open_questions": _clean_string_list(
            value.get("open_questions") or [], "open_questions"
        ),
        "editable_text": str(value.get("editable_text") or "").strip(),
        "next_question": next_question,
        "ready": ready,
        "created_at": _clean_string(value.get("created_at")),
        "updated_at": _clean_string(value.get("updated_at")),
    }


def new_engineering_brief(
    original_request: str,
    identity: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Create one editable brief bound to the active document conversation."""

    clean_request = _clean_string(original_request)
    timestamp = now_iso()
    state = _validated_state(
        {
            "schema": ENGINEERING_BRIEF_SCHEMA,
            "version": ENGINEERING_BRIEF_VERSION,
            "document_uid": identity.get("document_uid"),
            "conversation_id": identity.get("conversation_id"),
            "original_request": clean_request,
            "context": dict(context),
            "transcript": [],
            "brief": _empty_brief(clean_request),
            "assumptions": [],
            "open_questions": [],
            "editable_text": "",
            "next_question": "",
            "ready": False,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )
    state["editable_text"] = _render_validated_engineering_brief(state)
    return state


def update_engineering_brief_draft(
    state: Mapping[str, Any],
    *,
    original_request: str | None = None,
    editable_text: str | None = None,
) -> dict[str, Any]:
    """Apply human edits without interpreting or discarding their wording."""

    validated = _validated_state(state)
    prior_render = _render_validated_engineering_brief(validated)
    supplied_text = str(editable_text).strip() if editable_text is not None else None
    preview_was_canonical = (
        supplied_text is not None
        and supplied_text
        in {
            str(validated.get("editable_text") or "").strip(),
            prior_render,
        }
        and str(validated.get("editable_text") or "").strip() == prior_render
    )
    updated = dict(validated)
    if original_request is not None:
        updated["original_request"] = _clean_string(original_request)
        if not validated["transcript"] and validated["brief"]["objective"] in {
            "",
            validated["original_request"],
        }:
            updated["brief"] = {
                **validated["brief"],
                "objective": updated["original_request"],
            }
    if editable_text is not None:
        updated["editable_text"] = supplied_text or ""
    if preview_was_canonical:
        updated["editable_text"] = _render_validated_engineering_brief(updated)
    updated["updated_at"] = now_iso()
    return _validated_state(updated)


def add_active_conversation_context(
    state: Mapping[str, Any],
    conversation_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Add one bounded active-conversation snapshot to an existing brief."""

    validated = _validated_state(state)
    updated = dict(validated)
    updated["context"] = {
        **validated["context"],
        "active_conversation": _json_copy(
            conversation_context,
            "context.active_conversation",
        ),
    }
    updated["updated_at"] = now_iso()
    return _validated_state(updated)


def build_engineering_brief_prompt(
    state: Mapping[str, Any],
    user_response: str,
    *,
    use_best_judgment: bool = False,
) -> str:
    """Create a self-contained request for one non-mutating interview turn."""

    validated = _validated_state(state)
    if not validated["original_request"]:
        raise ValueError(
            "Describe the engineering outcome before developing the brief."
        )
    clean_response = _clean_string(user_response)
    if use_best_judgment:
        clean_response = (
            clean_response
            or "Use your best engineering judgment for remaining details and list every "
            "assumption explicitly."
        )
    response_contract = {
        "assistant_message": "string; concise explanation or the one next question",
        "next_question": "string; empty only when ready is true",
        "ready": "boolean",
        "brief": {
            "objective": "string",
            "deliverables": ["string"],
            "existing_geometry": ["string"],
            "units": "string",
            "dimensions": ["string"],
            "materials": ["string"],
            "interfaces": ["string"],
            "loads": ["string"],
            "manufacturing": ["string"],
            "tolerances": ["string"],
            "analyses": ["string"],
            "acceptance_criteria": ["string"],
            "requirements": ["string"],
            "preferences": ["string"],
        },
        "assumptions": ["string"],
        "open_questions": ["string"],
    }
    return (
        "Develop the engineering brief below. Ask exactly one highest-value question "
        "if an unresolved answer materially changes the design. If the brief is ready, "
        "set ready=true and next_question to an empty string. Preserve known facts; "
        "never turn an assumption into a stated requirement. Do not call or request CAD "
        "tools. Return only JSON.\n\n"
        "ENGINEERING_BRIEF_RESPONSE_CONTRACT_JSON\n"
        + json.dumps(response_contract, ensure_ascii=False, separators=(",", ":"))
        + "\nEND_ENGINEERING_BRIEF_RESPONSE_CONTRACT_JSON\n\n"
        "ENGINEERING_BRIEF_STATE_JSON\n"
        + json.dumps(validated, ensure_ascii=False, separators=(",", ":"))
        + "\nEND_ENGINEERING_BRIEF_STATE_JSON\n\n"
        "LATEST_USER_RESPONSE\n"
        + (clean_response or "Begin by evaluating the current request and context.")
    )


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE
    )
    if fenced is not None:
        text = fenced.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("Engineering Brief provider response contains no JSON object.")
    try:
        decoded, _end = json.JSONDecoder().raw_decode(text[start:])
    except ValueError as exc:
        raise ValueError(
            f"Engineering Brief provider response is not valid JSON: {exc}"
        ) from exc
    if not isinstance(decoded, dict):
        raise ValueError("Engineering Brief provider response must be a JSON object.")
    return decoded


def parse_engineering_brief_result(
    raw: str,
    *,
    prior_state: Mapping[str, Any],
    user_response: str,
) -> dict[str, Any]:
    """Validate and merge one provider response without losing durable identity."""

    prior = _validated_state(prior_state)
    result = _extract_json_object(raw)
    required = {
        "assistant_message",
        "next_question",
        "ready",
        "brief",
        "assumptions",
        "open_questions",
    }
    missing = sorted(required - set(result))
    if missing:
        raise ValueError(
            "Engineering Brief provider response is missing: " + ", ".join(missing)
        )
    assistant_message = result.get("assistant_message")
    next_question = result.get("next_question")
    ready = result.get("ready")
    if not isinstance(assistant_message, str) or not assistant_message.strip():
        raise ValueError(
            "Engineering Brief provider response assistant_message must be a string."
        )
    if not isinstance(next_question, str):
        raise ValueError(
            "Engineering Brief provider response next_question must be a string."
        )
    if not isinstance(ready, bool):
        raise ValueError("Engineering Brief provider response ready must be a boolean.")
    clean_question = next_question.strip()
    if not ready and not clean_question:
        raise ValueError(
            "Engineering Brief provider response next_question is required until ready."
        )
    assumptions = _clean_string_list(result.get("assumptions"), "assumptions")
    open_questions = _clean_string_list(result.get("open_questions"), "open_questions")
    transcript = list(prior["transcript"])
    clean_response = _clean_string(user_response)
    if clean_response:
        transcript.append({"role": "user", "content": clean_response})
    transcript.append({"role": "assistant", "content": assistant_message.strip()})
    updated = _validated_state(
        {
            **prior,
            "transcript": transcript,
            "brief": _normalized_brief(result.get("brief")),
            "assumptions": assumptions,
            "open_questions": open_questions,
            "editable_text": "",
            "next_question": clean_question,
            "ready": ready,
            "updated_at": now_iso(),
        }
    )
    updated["editable_text"] = _render_validated_engineering_brief(updated)
    return updated


def run_engineering_brief_turn(
    state: Mapping[str, Any],
    *,
    user_response: str,
    provider: Any,
    use_best_judgment: bool = False,
    cancellation_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run one provider-only brief turn with no CAD tool runner or tool schemas."""

    validated = _validated_state(state)
    provider_context = {
        "workbench": validated["context"].get("workbench"),
        "document": deepcopy(validated["context"].get("document") or {}),
        "selection": deepcopy(validated["context"].get("selection") or {}),
        "units": deepcopy(validated["context"].get("units") or {}),
        "provider_tool_schemas": [],
        "_vibecad_toolless_task": True,
        "_vibecad_task_instructions": ENGINEERING_BRIEF_TASK_INSTRUCTIONS,
    }
    prompt = build_engineering_brief_prompt(
        validated,
        user_response,
        use_best_judgment=use_best_judgment,
    )
    result = provider.run(
        prompt,
        provider_context,
        tool_runner=None,
        cancellation_check=cancellation_check,
        progress_callback=progress_callback,
    )
    return parse_engineering_brief_result(
        str(getattr(result, "final_output", "") or ""),
        prior_state=validated,
        user_response=(
            _clean_string(user_response)
            or (
                "Use your best engineering judgment for remaining details and list "
                "every assumption explicitly."
                if use_best_judgment
                else ""
            )
        ),
    )


def _render_validated_engineering_brief(validated: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for field in BRIEF_FIELD_ORDER:
        value = validated["brief"][field]
        if not value:
            continue
        lines.append(BRIEF_FIELD_LABELS[field])
        if isinstance(value, list):
            lines.extend(f"- {item}" for item in value)
        else:
            lines.append(str(value))
        lines.append("")
    if validated["assumptions"]:
        lines.append("Explicit assumptions")
        lines.extend(f"- {item}" for item in validated["assumptions"])
        lines.append("")
    if validated["open_questions"]:
        lines.append("Open questions")
        lines.extend(f"- {item}" for item in validated["open_questions"])
        lines.append("")
    return "\n".join(lines).strip()


def render_engineering_brief(state: Mapping[str, Any]) -> str:
    """Render the canonical state as readable, editable plain text."""

    validated = _validated_state(state)
    return str(validated.get("editable_text") or "").strip() or (
        _render_validated_engineering_brief(validated)
    )


def engineering_brief_handoff(
    state: Mapping[str, Any],
    *,
    approved_text: str,
) -> str:
    """Build the single authoritative user turn sent to the normal CAD agent."""

    validated = _validated_state(state)
    readable = _clean_string(approved_text) or render_engineering_brief(validated)
    return (
        "Complete the work in the active VibeCAD document using this approved "
        "engineering brief. Inspect the current CAD state before acting and verify "
        "the acceptance criteria before claiming completion.\n\n" + readable
    )


class EngineeringBriefStore:
    """Durable per-conversation brief storage within one VibeCAD project."""

    def __init__(self, project_root: str | Path) -> None:
        clean_root = _clean_string(project_root)
        if not clean_root:
            raise ValueError("Engineering Brief storage requires a project root.")
        self.project_root = Path(clean_root).expanduser()
        self.directory = self.project_root / ENGINEERING_BRIEFS_DIRECTORY

    def path_for(self, conversation_id: str) -> Path:
        clean_id = _clean_string(conversation_id).lower()
        if _CONVERSATION_ID_PATTERN.fullmatch(clean_id) is None:
            raise ValueError(
                "Engineering Brief storage requires a 32-character conversation id."
            )
        return self.directory / f"{clean_id}.json"

    def write(self, state: Mapping[str, Any]) -> dict[str, Any]:
        validated = _validated_state(state)
        validated["updated_at"] = now_iso()
        if not validated["created_at"]:
            validated["created_at"] = validated["updated_at"]
        path = self.path_for(validated["conversation_id"])
        encoded = json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True)
        with _STORE_LOCK:
            atomic_write_text(path, encoded)
        return {"written": True, "path": str(path), "state": validated}

    def load(
        self,
        *,
        document_uid: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        path = self.path_for(conversation_id)
        with _STORE_LOCK:
            if not path.is_file():
                return {"available": False, "reason": "missing", "path": str(path)}
            try:
                with open_shared_binary(path) as stream:
                    raw = json.load(stream)
                state = _validated_state(raw)
            except (OSError, ValueError, RuntimeError) as exc:
                return {
                    "available": True,
                    "recoverable": False,
                    "error": str(exc),
                    "path": str(path),
                }
        if state["document_uid"] != _clean_string(document_uid):
            return {
                "available": False,
                "reason": "document_changed",
                "path": str(path),
            }
        if state["conversation_id"] != _clean_string(conversation_id).lower():
            return {
                "available": False,
                "reason": "conversation_changed",
                "path": str(path),
            }
        return {
            "available": True,
            "recoverable": True,
            "path": str(path),
            "state": state,
        }
