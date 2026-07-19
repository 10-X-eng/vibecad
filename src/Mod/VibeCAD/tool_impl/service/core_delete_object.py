# SPDX-License-Identifier: LGPL-2.1-or-later

"""Delete one exact document object and report the complete retained cascade."""

from __future__ import annotations

from typing import Any

from VibeCADTools import tool_failure
from VibeCADTransactions import run_freecad_transaction


TOOL_SPEC = {
    "name": "core.delete_object",
    "description": (
        "Delete exactly one object from the active document by internal name. "
        "For a Body member, FreeCAD's native Body removal reroutes history before "
        "document deletion. The result reports every retained cascade and any "
        "object left invalid after recompute."
    ),
    "contextual": True,
    # Compatibility entry point for existing internal callers. Exact modeling
    # surfaces intentionally expose deletion only through their owning pack or
    # VibeScript lifecycle, never as a cross-workbench provider tool.
    "provider_visible": False,
    "safety": "SAFE_WRITE",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "object_name": {
                "type": "string",
                "minLength": 1,
                "description": "Exact internal Name of the object to delete.",
            }
        },
        "required": ["object_name"],
        "additionalProperties": False,
    },
}


def run(service: Any, object_name: str) -> dict[str, Any]:
    doc = service._active_document()
    clean_name = str(object_name).strip()
    if doc is None:
        return tool_failure(
            TOOL_SPEC["name"],
            "NO_ACTIVE_DOCUMENT",
            "precondition",
            "No active document.",
            requested={"object_name": object_name},
        )
    target = doc.getObject(clean_name)
    if target is None:
        candidates = [
            service._document_object_summary(item)
            for item in list(getattr(doc, "Objects", []) or [])[:80]
        ]
        return tool_failure(
            TOOL_SPEC["name"],
            "OBJECT_NOT_FOUND",
            "precondition",
            f"Object not found by exact internal name: {clean_name}",
            requested={"object_name": object_name},
            normalized={"object_name": clean_name},
            candidates=candidates,
            required_changes=[{"object_name": "choose one candidate internal name"}],
        )

    owner = service._partdesign_body_for_feature(target)
    target_summary = service._document_object_summary(target)
    body_before = service._partdesign_body_summary(owner) if owner is not None else None
    incoming_before = [_object_ref(item) for item in list(getattr(target, "InList", []) or [])]
    outgoing_before = [_object_ref(item) for item in list(getattr(target, "OutList", []) or [])]
    invalid_before = _invalid_object_states(service, doc)
    invalid_before_names = {str(item.get("name")) for item in invalid_before}

    def remove() -> dict[str, Any]:
        current = doc.getObject(clean_name)
        if current is None:
            raise RuntimeError(f"Object disappeared before deletion: {clean_name}")
        current_owner = service._partdesign_body_for_feature(current)
        membership_removed = False
        if current_owner is not None and current in list(current_owner.Group):
            removed = list(current_owner.removeObject(current))
            membership_removed = current in removed
            if not membership_removed:
                raise RuntimeError(
                    f"Body {current_owner.Name} did not remove member {clean_name}."
                )
        doc.removeObject(clean_name)
        return {
            "operation": "delete_object",
            "deleted_object": target_summary,
            "exact_internal_name": clean_name,
            "partdesign_owner": getattr(current_owner, "Name", None),
            "body_membership_removed": membership_removed,
        }

    def verify(_: dict[str, Any]) -> dict[str, Any]:
        still_present = doc.getObject(clean_name) is not None
        invalid_objects = _new_invalid_objects(service, doc, invalid_before_names)
        return {
            "ok": not still_present and not invalid_objects,
            "checks": [
                {
                    "name": "object_absent",
                    "ok": not still_present,
                    "object_name": clean_name,
                },
                {
                    "name": "remaining_document_objects_valid",
                    "ok": not invalid_objects,
                    "invalid_objects": invalid_objects,
                },
            ],
            "error": (
                f"Deletion left {len(invalid_objects)} object(s) invalid."
                if invalid_objects
                else f"FreeCAD still contains object {clean_name} after deletion."
                if still_present
                else None
            ),
        }

    transaction = run_freecad_transaction(f"Delete object: {clean_name}", remove, verify)
    mutation = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    still_present = doc.getObject(clean_name) is not None
    owner_after = doc.getObject(owner.Name) if owner is not None else None
    invalid_objects = _new_invalid_objects(service, doc, invalid_before_names)
    details = {
        "operation": "delete_object",
        "deleted_object": target_summary,
        "exact_internal_name": clean_name,
        "partdesign_owner": getattr(owner, "Name", None),
        "body_membership_removed": mutation.get("body_membership_removed"),
        "incoming_references_before": incoming_before,
        "outgoing_references_before": outgoing_before,
        "body_state_before": body_before,
        "body_state_after": (
            service._partdesign_body_summary(owner_after) if owner_after is not None else None
        ),
        "document_delta": transaction.get("document_delta") or {},
        "cascaded_changes": transaction.get("state_change") or {},
        "invalid_objects_after": invalid_objects,
        "preexisting_invalid_objects": invalid_before,
        "still_present": still_present,
        "native_diagnostics": transaction.get("native_diagnostics") or {},
        "state_change": transaction.get("state_change") or {},
        "transaction": {
            key: transaction.get(key)
            for key in (
                "transaction_name",
                "transaction_opened",
                "mutation_started",
                "commit_attempted",
                "commit_succeeded",
            )
        },
    }
    if bool(transaction.get("ok")) and not still_present and not invalid_objects:
        return {"ok": True, **details}
    failure = tool_failure(
        TOOL_SPEC["name"],
        str(transaction.get("failure_code") or "DELETE_POSTCONDITION_FAILED"),
        str(transaction.get("failure_stage") or "postcondition"),
        str(transaction.get("error") or "Object deletion failed verification."),
        requested={"object_name": object_name},
        normalized={"object_name": clean_name},
        observed={
            "still_present": still_present,
            "invalid_objects_after": invalid_objects,
        },
        state_change=transaction.get("state_change") or {},
        native_diagnostics=transaction.get("native_diagnostics") or {},
        required_changes=[
            {"repair_or_delete": name}
            for name in (transaction.get("state_change") or {}).get("repair_targets", [])
        ],
    )
    return {**details, **failure}


def _object_ref(obj: Any) -> dict[str, Any]:
    return {
        "name": getattr(obj, "Name", None),
        "label": getattr(obj, "Label", getattr(obj, "Name", None)),
        "type": getattr(obj, "TypeId", None),
    }


def _invalid_object_states(service: Any, doc: Any) -> list[dict[str, Any]]:
    invalid = []
    for obj in list(getattr(doc, "Objects", []) or []):
        state = [str(item) for item in list(getattr(obj, "State", []) or [])]
        valid = True
        checker = getattr(obj, "isValid", None)
        if callable(checker):
            try:
                valid = bool(checker())
            except Exception:
                valid = False
        if valid and not any(item.lower() in {"invalid", "error"} for item in state):
            continue
        summary = service._document_object_summary(obj)
        summary["state"] = state
        summary["is_valid"] = valid
        invalid.append(summary)
    return invalid


def _new_invalid_objects(
    service: Any,
    doc: Any,
    preexisting_names: set[str],
) -> list[dict[str, Any]]:
    return [
        item
        for item in _invalid_object_states(service, doc)
        if str(item.get("name")) not in preexisting_names
    ]
