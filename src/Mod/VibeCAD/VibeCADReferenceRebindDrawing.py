# SPDX-License-Identifier: LGPL-2.1-or-later

"""Durable drawing-reference invalidation after VibeScript regeneration."""

from __future__ import annotations

from typing import Any

import VibeCADReferenceContracts as reference_contracts


def rebind_scripted_reference(
    service: Any,
    dimension_obj: Any,
    contract: dict[str, Any],
) -> dict[str, Any]:
    doc = service._active_document()
    view_name = str(contract.get("view_name") or "")
    view = doc.getObject(view_name) if doc is not None else None
    if view is None:
        return _invalid("The TechDraw view recorded by the dimension no longer exists.")
    references = contract.get("references")
    if not isinstance(references, list) or not references:
        return _invalid("The TechDraw reference contract contains no references.")
    try:
        view.touch()
        dimension_obj.touch()
        revision = str(contract.get("source_revision") or "")
        reference_contracts.mark_stale(
            view,
            revision,
            "A referenced scripted model changed; regenerate this drawing view.",
        )
        reference_contracts.mark_stale(
            dimension_obj,
            revision,
            "The source view changed; regenerate and verify this dimension.",
        )
    except Exception as exc:
        return _invalid(
            "FreeCAD could not invalidate the affected TechDraw dimension.",
            native_error=str(exc),
        )
    return {
        "ok": True,
        "domain": "techdraw_dimension",
        "object": dimension_obj.Name,
        "view": view.Name,
        "rebind_deferred": True,
        "derived_state": "stale",
        "projection_recompute_deferred": True,
    }


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
