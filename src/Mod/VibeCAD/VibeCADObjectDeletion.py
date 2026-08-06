# SPDX-License-Identifier: LGPL-2.1-or-later

"""Guarded deletion of one exact unowned CAD object and its contained children."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from VibeCADDocumentReferences import (
    DocumentReferenceError,
    normalize_document_reference,
    resolve_reference_target,
)


class ObjectDeletionError(RuntimeError):
    """One exact object cannot be deleted without violating document ownership."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        observed: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = str(code)
        self.observed = dict(observed or {})
        super().__init__(str(message))


def _object_identity(obj: Any) -> dict[str, str]:
    return {
        "object_name": str(getattr(obj, "Name", "") or ""),
        "label": str(getattr(obj, "Label", "") or ""),
        "type_id": str(getattr(obj, "TypeId", "") or ""),
    }


def _contained_objects(root: Any) -> list[Any]:
    """Return exact group containment, never arbitrary dependency traversal."""

    ordered: list[Any] = []
    seen: set[int] = set()

    def visit(obj: Any) -> None:
        if id(obj) in seen:
            return
        seen.add(id(obj))
        ordered.append(obj)
        group = getattr(obj, "Group", None)
        if not isinstance(group, (list, tuple)):
            return
        for child in group:
            if child is not None:
                visit(child)

    visit(root)
    return ordered


def _managed_source_identity(targets: list[Any]) -> tuple[str, str] | None:
    for obj in targets:
        program_id = str(
            getattr(obj, "VibeCADVibeScriptProgramId", "")
            or getattr(obj, "VibeCADScriptedModelId", "")
            or ""
        ).strip()
        if not program_id:
            continue
        domain = str(getattr(obj, "VibeCADVibeScriptDomain", "") or "").strip()
        return program_id, domain
    return None


def delete_exact_object(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    """Delete one authenticated top-level object with dependency-safe rollback."""

    owner_document = service._active_document()
    if owner_document is None:
        raise ObjectDeletionError(
            "NO_ACTIVE_DOCUMENT",
            "Open a document before deleting an object.",
        )
    try:
        reference = normalize_document_reference(args.get("reference"))
        target = resolve_reference_target(
            owner_document,
            reference,
            "reference",
            open_missing=False,
        )
    except DocumentReferenceError as exc:
        raise ObjectDeletionError(
            "OBJECT_REFERENCE_INVALID",
            str(exc),
        ) from exc
    if getattr(target, "Document", None) is not owner_document:
        raise ObjectDeletionError(
            "OBJECT_DOCUMENT_NOT_ACTIVE",
            "Delete an object from its own active document.",
            observed={"reference": reference},
        )

    targets = _contained_objects(target)
    managed = _managed_source_identity(targets)
    if managed is not None:
        program_id, domain = managed
        raise ObjectDeletionError(
            "MANAGED_OBJECT_REQUIRES_PROGRAM_DELETE",
            "This object belongs to a VibeScript source. Delete its output or source "
            "so CAD geometry and editable code remain coherent.",
            observed={
                "reference": reference,
                "source_id": program_id,
                "domain": domain,
                "required_tool": "vibescript.delete_program",
            },
        )

    target_ids = {id(obj) for obj in targets}
    external = []
    for obj in targets:
        for consumer in list(getattr(obj, "InList", []) or []):
            if id(consumer) in target_ids:
                continue
            external.append(
                {
                    "deleted_object": str(getattr(obj, "Name", "") or ""),
                    "referencing_object": str(
                        getattr(consumer, "Name", "") or ""
                    ),
                    "referencing_type": str(
                        getattr(consumer, "TypeId", "") or ""
                    ),
                }
            )
    if external:
        raise ObjectDeletionError(
            "OBJECT_HAS_EXTERNAL_REFERENCES",
            "The object cannot be deleted while objects outside its containment "
            "tree reference it.",
            observed={"reference": reference, "references": external},
        )

    deleted = [_object_identity(obj) for obj in targets]
    names = [item["object_name"] for item in deleted if item["object_name"]]
    transaction_open = False
    try:
        if hasattr(owner_document, "openTransaction"):
            owner_document.openTransaction("Delete exact VibeCAD object")
            transaction_open = True
        for obj in reversed(targets):
            name = str(getattr(obj, "Name", "") or "")
            if name and owner_document.getObject(name) is not None:
                owner_document.removeObject(name)
        survivors = [name for name in names if owner_document.getObject(name) is not None]
        if survivors:
            raise RuntimeError(
                "FreeCAD retained deletion targets: " + ", ".join(survivors)
            )
        if transaction_open and hasattr(owner_document, "commitTransaction"):
            owner_document.commitTransaction()
            transaction_open = False
    except Exception as exc:
        if transaction_open and hasattr(owner_document, "abortTransaction"):
            try:
                owner_document.abortTransaction()
            except Exception:
                pass
        raise ObjectDeletionError(
            "OBJECT_DELETE_FAILED",
            f"FreeCAD could not delete the exact object safely: {exc}",
            observed={"reference": reference, "targets": names},
        ) from exc

    return {
        "ok": True,
        "tool": "vibescript.delete_object",
        "reference": reference,
        "reason": str(args.get("reason") or ""),
        "deleted_objects": deleted,
        "cad_objects_removed": len(deleted),
        "recompute_deferred": True,
    }
