# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contracts for the Parameters spreadsheet ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


PARAMETERS_SHEET_CAPABILITY_NAME = "parameters.sheet"
PARAMETERS_READ_CAPABILITY_NAME = "parameters.read"
PARAMETERS_CELL_CAPABILITY_NAME = "parameters.cell"
PARAMETERS_FORMAT_CAPABILITY_NAME = "parameters.format"
PARAMETERS_EXPORT_CAPABILITY_NAME = "parameters.export"

_SURFACE = frozenset({"parameters"})
_SHA256 = {
    "type": "string",
    "pattern": r"^[0-9a-f]{64}$",
    "minLength": 64,
    "maxLength": 64,
}
_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "maxLength": 128,
}
_ADDRESS = {
    "type": "string",
    "pattern": r"^[A-Za-z]{1,3}[1-9][0-9]{0,6}$",
    "maxLength": 10,
}
_RANGE_TEXT = {
    "type": "string",
    "pattern": r"^[A-Za-z]{1,3}[1-9][0-9]{0,6}(?::[A-Za-z]{1,3}[1-9][0-9]{0,6})?$",
    "maxLength": 21,
    "description": "A1 or A1:B2; each call is limited to 512 cells.",
}


def _closed(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_SHEET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_EXACT_RANGE = _closed(
    {
        "range": _RANGE_TEXT,
        "expected_range_state_sha256": _SHA256,
    },
    ("range", "expected_range_state_sha256"),
)
_EXACT_CELL = _closed(
    {
        "address": _ADDRESS,
        "expected_cell_state_sha256": _SHA256,
    },
    ("address", "expected_cell_state_sha256"),
)
_RGB = {
    "oneOf": [
        {
            "type": "array",
            "items": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "minItems": 3,
            "maxItems": 3,
        },
        {"type": "null"},
    ]
}


def _variant(
    operation: str,
    description: str,
    action_id: str,
    target: str | None,
    parameters: dict[str, Any],
    *,
    behavior: str = "document",
    background: bool = False,
    supplemental: bool = False,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=_SURFACE,
        exact_target_type=target,
        transaction_behavior=behavior,
        background_required=background,
        parameters=parameters,
        provider_supplemental=supplemental,
    )


def parameters_sheet_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=PARAMETERS_SHEET_CAPABILITY_NAME,
        description="Create a Parameters sheet or import one human-authorized CSV.",
        primary_classification="mutation",
        variants=(
            _variant(
                "create",
                "Create one empty sheet as one exact History operation.",
                "Spreadsheet_CreateSheet",
                "NewParametersSheet",
                _closed(
                    {"label": {"type": "string", "minLength": 1, "maxLength": 160}},
                    ("label",),
                ),
            ),
            _variant(
                "import_csv",
                "Ask the human for bounded UTF-8 CSV and import it responsively.",
                "Spreadsheet_Import",
                "HumanAuthorizedParametersCsv",
                _closed({}, ()),
                behavior="background",
                background=True,
            ),
        ),
    )


def parameters_read_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=PARAMETERS_READ_CAPABILITY_NAME,
        description="Read exact raw, evaluated, formula, alias, merge, and format cell state.",
        primary_classification="read",
        variants=(
            _variant(
                "read_range",
                "Read one bounded range and receive hashes usable by later edits.",
                "VibeCAD_NativeParametersReadRange",
                "ExactParametersSheetAndRange",
                _closed({"sheet": _SHEET, "range": _RANGE_TEXT}, ("sheet", "range")),
                behavior="none",
            ),
        ),
    )


def parameters_cell_capability_definition() -> NativeCapabilityDefinition:
    value_update = _closed(
        {
            "address": _ADDRESS,
            "value": {"type": "string", "maxLength": 4096},
            "expected_cell_state_sha256": _SHA256,
        },
        ("address", "value", "expected_cell_state_sha256"),
    )
    formula_update = _closed(
        {
            "address": _ADDRESS,
            "formula": {
                "type": "string",
                "pattern": r"^=.+$",
                "maxLength": 4096,
            },
            "expected_cell_state_sha256": _SHA256,
        },
        ("address", "formula", "expected_cell_state_sha256"),
    )
    common_sheet = {"sheet": _SHEET}
    return NativeCapabilityDefinition(
        name=PARAMETERS_CELL_CAPABILITY_NAME,
        description="Write exact cells, formulas, aliases, merges, and cell properties.",
        primary_classification="mutation",
        variants=(
            _variant(
                "write_values",
                "Write 1 to 256 hash-pinned raw cell values atomically.",
                "VibeCAD_NativeParametersWriteValues",
                "ExactParametersCells",
                _closed(
                    {
                        **common_sheet,
                        "updates": {"type": "array", "items": value_update, "minItems": 1, "maxItems": 256},
                    },
                    ("sheet", "updates"),
                ),
                supplemental=True,
            ),
            _variant(
                "write_formulas",
                "Write 1 to 256 hash-pinned formulas and report evaluation errors.",
                "VibeCAD_NativeParametersWriteFormulas",
                "ExactParametersFormulaCells",
                _closed(
                    {
                        **common_sheet,
                        "updates": {"type": "array", "items": formula_update, "minItems": 1, "maxItems": 256},
                    },
                    ("sheet", "updates"),
                ),
                supplemental=True,
            ),
            _variant(
                "set_alias",
                "Set or clear one stable expression alias on an exact cell.",
                "Spreadsheet_SetAlias",
                "ExactParametersCell",
                _closed(
                    {
                        **common_sheet,
                        "cell": _EXACT_CELL,
                        "alias": {"type": "string", "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$|^$", "maxLength": 64},
                    },
                    ("sheet", "cell", "alias"),
                ),
            ),
            _variant(
                "merge",
                "Merge one exact multi-cell rectangular range.",
                "Spreadsheet_MergeCells",
                "ExactParametersRange",
                _closed({**common_sheet, "target": _EXACT_RANGE}, ("sheet", "target")),
            ),
            _variant(
                "split",
                "Split the merged range containing one exact cell.",
                "Spreadsheet_SplitCell",
                "ExactParametersMergedCell",
                _closed({**common_sheet, "cell": _EXACT_CELL}, ("sheet", "cell")),
            ),
            _variant(
                "set_properties",
                "Set display unit and/or foreground/background color on one exact range.",
                "Spreadsheet_CellProperties",
                "ExactParametersRange",
                _closed(
                    {
                        **common_sheet,
                        "target": _EXACT_RANGE,
                        "properties": {
                            **_closed(
                                {
                                    "display_unit": {
                                        "oneOf": [
                                            {"type": "string", "maxLength": 128},
                                            {"type": "null"},
                                        ]
                                    },
                                    "foreground_rgb": _RGB,
                                    "background_rgb": _RGB,
                                },
                                (),
                            ),
                            "minProperties": 1,
                        },
                    },
                    ("sheet", "target", "properties"),
                ),
            ),
        ),
    )


def parameters_format_capability_definition() -> NativeCapabilityDefinition:
    actions = {
        "align_left": "Spreadsheet_AlignLeft",
        "align_center": "Spreadsheet_AlignCenter",
        "align_right": "Spreadsheet_AlignRight",
        "align_top": "Spreadsheet_AlignTop",
        "align_vertical_center": "Spreadsheet_AlignVCenter",
        "align_bottom": "Spreadsheet_AlignBottom",
        "set_bold": "Spreadsheet_StyleBold",
        "set_italic": "Spreadsheet_StyleItalic",
        "set_underline": "Spreadsheet_StyleUnderline",
    }
    variants = []
    for operation, action_id in actions.items():
        properties = {"sheet": _SHEET, "target": _EXACT_RANGE}
        required = ["sheet", "target"]
        if operation.startswith("set_"):
            properties["enabled"] = {"type": "boolean"}
            required.append("enabled")
        variants.append(
            _variant(
                operation,
                (
                    "Enable or disable one exact text style on a range."
                    if operation.startswith("set_")
                    else "Apply one exact horizontal or vertical alignment to a range."
                ),
                action_id,
                "ExactParametersRange",
                _closed(properties, tuple(required)),
            )
        )
    return NativeCapabilityDefinition(
        name=PARAMETERS_FORMAT_CAPABILITY_NAME,
        description="Apply explicit non-toggle alignment and text style to exact ranges.",
        primary_classification="mutation",
        variants=tuple(variants),
    )


def parameters_export_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=PARAMETERS_EXPORT_CAPABILITY_NAME,
        description="Export one hash-pinned Parameters sheet to human-authorized CSV.",
        primary_classification="export",
        variants=(
            _variant(
                "export_csv",
                "Ask the human for a destination and atomically publish exact CSV.",
                "Spreadsheet_Export",
                "ExactParametersSheetAndHumanAuthorizedOutput",
                _closed({"sheet": _SHEET}, ("sheet",)),
                behavior="background_output",
                background=True,
            ),
        ),
    )


def register_parameters_capability_definitions(registry: NativeCapabilityRegistry) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    definitions = (
        parameters_sheet_capability_definition(),
        parameters_read_capability_definition(),
        parameters_cell_capability_definition(),
        parameters_format_capability_definition(),
        parameters_export_capability_definition(),
    )
    for definition in definitions:
        if definition.name == PARAMETERS_READ_CAPABILITY_NAME:
            registry.register_shared_definition(definition)
        else:
            registry.register_definition(definition)
