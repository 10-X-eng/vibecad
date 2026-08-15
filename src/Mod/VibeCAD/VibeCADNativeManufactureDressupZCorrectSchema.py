# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider schema for probe-map Z Correction dress-up."""

from __future__ import annotations

from VibeCADNativeManufactureContract import (
    PATH_OPERATION_LABEL_SCHEMA as LABEL_SCHEMA,
)


_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
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


_EXACT_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)


Z_CORRECT_DRESSUP_PARAMETERS_SCHEMA = _closed(
    {
        "label": LABEL_SCHEMA,
        "job": _EXACT_TARGET,
        "base_operation": _EXACT_TARGET,
        "arc_maximum_deflection_mm": {
            "type": "number",
            "minimum": 0.001,
            "maximum": 1_000_000.0,
            "description": (
                "Maximum chord deflection used when linearizing circular cutting "
                "moves before applying the probe surface."
            ),
        },
        "line_maximum_segment_length_mm": {
            "type": "number",
            "minimum": 0.001,
            "maximum": 1_000_000.0,
            "description": (
                "Maximum length of each generated linear cutting segment. The host "
                "asks the human to choose the probe-map file; no filesystem path is "
                "accepted from the model."
            ),
        },
    },
    (
        "label",
        "job",
        "base_operation",
        "arc_maximum_deflection_mm",
        "line_maximum_segment_length_mm",
    ),
)
