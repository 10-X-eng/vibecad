# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native TechDraw text annotation at an exact page position."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "techdraw.add_annotation",
    "description": (
        "Create one native TechDraw text annotation (a note) at an exact "
        "position on an exact drawing page. Each array item becomes one line "
        "of text. Use this for titles, notes, and callout text; use "
        "techdraw.add_dimension for measurements."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "TechDrawWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "page_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the drawing page from techdraw.list_pages."
                ),
            },
            "text_lines": {
                "type": "array",
                "items": {
                    "type": "string",
                    "description": "One line of annotation text.",
                },
                "minItems": 1,
                "maxItems": 20,
                "description": "Lines of text to display, in order.",
            },
            "x_mm": {
                "type": "number",
                "description": (
                    "Horizontal position of the annotation on the page in mm, "
                    "measured from the page's bottom-left corner."
                ),
            },
            "y_mm": {
                "type": "number",
                "description": (
                    "Vertical position of the annotation on the page in mm, "
                    "measured from the page's bottom-left corner."
                ),
            },
            "text_size_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 50,
                "description": "Text height in mm; 5 is a typical note size.",
            },
        },
        "required": ["page_name", "text_lines", "x_mm", "y_mm", "text_size_mm"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    page_name: str,
    text_lines: list[str],
    x_mm: float,
    y_mm: float,
    text_size_mm: float,
) -> dict[str, Any]:
    if not isinstance(text_lines, list) or not text_lines:
        return _invalid("text_lines must be a non-empty array.")
    lines = [str(line) for line in text_lines]
    if not any(line.strip() for line in lines):
        return _invalid("text_lines must contain at least one non-empty line.")
    if float(text_size_mm) <= 0:
        return _invalid("text_size_mm must be positive.")
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    page = doc.getObject(str(page_name or "").strip())
    if page is None or getattr(page, "TypeId", "") != "TechDraw::DrawPage":
        return _invalid(
            f"Drawing page not found by exact internal name: {page_name}. "
            "Call techdraw.list_pages for exact names."
        )

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target_page = active.getObject(page.Name)
        if target_page is None:
            raise RuntimeError("The drawing page no longer exists.")
        annotation = active.addObject("TechDraw::DrawViewAnnotation", "Annotation")
        annotation.Text = lines
        annotation.TextSize = float(text_size_mm)
        target_page.addView(annotation)
        annotation.X = float(x_mm)
        annotation.Y = float(y_mm)
        active.recompute()
        return {
            "document": active.Name,
            "page": target_page.Name,
            "annotation": annotation.Name,
            "line_count": len(lines),
            "position_mm": {"x": float(x_mm), "y": float(y_mm)},
        }

    transaction = run_freecad_transaction(
        "Add TechDraw annotation",
        create,
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "add_annotation"},
        next_action=(
            "Capture a screenshot to check the page layout, or add more "
            "views and dimensions."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
