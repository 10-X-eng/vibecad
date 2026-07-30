# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native TechDraw drawing page with a standard template."""

from __future__ import annotations

import os
from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


_TEMPLATE_FILES = {
    "a4_landscape": "ISO/A4_Landscape_blank.svg",
    "a3_landscape": "ISO/A3_Landscape_blank.svg",
    "a2_landscape": "ISO/A2_Landscape_blank.svg",
    "a1_landscape": "ISO/A1_Landscape_blank.svg",
    "a0_landscape": "ISO/A0_Landscape_blank.svg",
    "a4_portrait": "ISO/A4_Portrait_blank.svg",
}


TOOL_SPEC = {
    "name": "techdraw.create_page",
    "description": (
        "Create one native TechDraw drawing page with a blank ISO sheet "
        "template. The page starts empty; add projected views of 3D objects "
        "with techdraw.add_view, then dimensions and notes."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "TechDrawWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "sheet_size": {
                "type": "string",
                "enum": sorted(_TEMPLATE_FILES),
                "description": (
                    "Standard sheet size and orientation for the page "
                    "template: a0_landscape through a4_landscape, or "
                    "a4_portrait."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new page, e.g. 'BracketDrawing'.",
            },
        },
        "required": ["sheet_size", "label"],
        "additionalProperties": False,
    },
}


def run(service: Any, sheet_size: str, label: str) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    template_relative = _TEMPLATE_FILES.get(str(sheet_size or ""))
    if template_relative is None:
        return _invalid(
            "sheet_size must be one of: " + ", ".join(sorted(_TEMPLATE_FILES))
        )
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")

    import FreeCAD as App

    templates_dir = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates")
    requested_template_path = os.path.join(
        templates_dir, *template_relative.split("/")
    )
    installed_candidates = _installed_templates(templates_dir)
    if not os.path.isfile(requested_template_path):
        return _invalid(
            "The exact requested TechDraw template is not installed; no page "
            "was created and no substitute template was used.",
            requested_template_path=requested_template_path,
            requested_template_relative=template_relative,
            installed_template_candidates=installed_candidates,
            retained_objects=[],
        )

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        with domain_runtime.NewTimelineOperation() as timeline:
            page = active.addObject("TechDraw::DrawPage", "Page")
            timeline.set_operation(page)
            template = active.addObject(
                "TechDraw::DrawSVGTemplate",
                "Template",
            )
            timeline.add_resource(template)
            template.Template = requested_template_path
            page.Template = template
            page.Label = clean_label
        active.recompute()
        return {
            "document": active.Name,
            "page": page.Name,
            "page_label": page.Label,
            "template": template.Name,
            "requested_template_path": requested_template_path,
            "actual_template_path": str(template.Template),
            "template_link": getattr(getattr(page, "Template", None), "Name", None),
            "actual_page_dimensions_mm": {
                "width": float(page.PageWidth),
                "height": float(page.PageHeight),
            },
            "installed_template_candidates": installed_candidates,
            "retained_objects": [page.Name, template.Name],
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        dimensions = result.get("actual_page_dimensions_mm") or {}
        checks = [
            {
                "name": "exact_template_path",
                "ok": os.path.realpath(str(result.get("actual_template_path") or ""))
                == os.path.realpath(requested_template_path),
                "requested": requested_template_path,
                "actual": result.get("actual_template_path"),
            },
            {
                "name": "page_template_link",
                "ok": result.get("template_link") == result.get("template"),
                "actual": result.get("template_link"),
            },
            {
                "name": "positive_page_dimensions",
                "ok": float(dimensions.get("width", 0.0)) > 0.0
                and float(dimensions.get("height", 0.0)) > 0.0,
                "actual": dimensions,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create TechDraw page: {clean_label}",
        create,
        verifier=verify,
    )
    result = (
        transaction.get("result", {})
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={
            "operation": "create_page",
            "requested_template_path": requested_template_path,
            "installed_template_candidates": installed_candidates,
            **result,
        },
        next_action=("Add projected views of exact 3D objects with techdraw.add_view."),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _installed_templates(templates_dir: str) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    if not os.path.isdir(templates_dir):
        return candidates
    for relative_path in sorted(set(_TEMPLATE_FILES.values())):
        path = os.path.join(templates_dir, *relative_path.split("/"))
        if not os.path.isfile(path):
            continue
        candidates.append(
            {
                "relative_path": relative_path,
                "absolute_path": path,
            }
        )
    return sorted(candidates, key=lambda item: item["relative_path"].lower())
