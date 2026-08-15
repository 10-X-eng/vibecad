# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for Drawing file export and Print All."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_EXPORT_CAPABILITY_NAME = "drawing.export"
DRAWING_EXPORT_OPERATIONS = ("svg", "dxf", "pdf", "print_all")
_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "minLength": 1,
    "maxLength": 128,
}
_SHA256 = {
    "type": "string",
    "pattern": r"^[0-9a-f]{64}$",
    "minLength": 64,
    "maxLength": 64,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_PAGE_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)


def _page_variant(
    operation: str,
    action_ids: frozenset[str],
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=(
            f"Ask the human for a {operation.upper()} destination, render one exact "
            "current-History Drawing page through TechDraw, validate the generated "
            "artifact, and publish it atomically. The AI never receives a path."
        ),
        action_ids=action_ids,
        surface_ids=frozenset({"drawing"}),
        exact_target_type="TechDraw::DrawPage",
        transaction_behavior="background_output",
        background_required=True,
        parameters=_closed({"page": _PAGE_TARGET}, ("page",)),
    )


def drawing_export_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_EXPORT_CAPABILITY_NAME,
        description=(
            "Export one exact Drawing page as SVG, DXF, or PDF, or ask the human "
            "to authorize Print All. Every operation is output-only, responsive, "
            "current-History scoped, and path-free to the AI."
        ),
        primary_classification="export",
        variants=(
            _page_variant(
                "svg",
                frozenset(
                    {"TechDraw_ExportPageSVG", "TechDrawContextExportSVG"}
                ),
            ),
            _page_variant(
                "dxf",
                frozenset(
                    {"TechDraw_ExportPageDXF", "TechDrawContextExportDXF"}
                ),
            ),
            _page_variant("pdf", frozenset({"TechDrawContextExportPDF"})),
            NativeCapabilityVariant(
                operation="print_all",
                description=(
                    "Open the platform print dialog for every current-History Drawing "
                    "page. The human chooses and authorizes the printer or print-to-file "
                    "destination; the AI receives no device name or filesystem path."
                ),
                action_ids=frozenset(
                    {"TechDraw_PrintAll", "TechDrawContextPrintAll"}
                ),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="App::Document",
                transaction_behavior="background_output",
                background_required=True,
                parameters=_closed({}, ()),
            ),
        ),
    )


def register_drawing_export_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_export_capability_definition())
