# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared provider/runtime contracts for Native CAM path operations."""

from __future__ import annotations

from typing import Any

from VibeCADNativeManufactureErrors import NativeManufactureError


PATH_OPERATION_LABEL_SCHEMA = {
    "type": "string",
    "minLength": 1,
    "maxLength": 160,
    "pattern": r"^(?=[\x20-\x27\x2A-\x7E]*[\x21-\x27\x2A-\x7E])[\x20-\x27\x2A-\x7E]+$",
    "description": "Trimmed printable ASCII path-operation label without parentheses.",
}


def clean_path_operation_label(value: Any, noun: str) -> str:
    """Return a bounded label that is safe when CAM emits it as G-code text."""

    if not isinstance(value, str):
        raise NativeManufactureError(
            f"A {noun} label must be one string.",
            error_code="NATIVE_ARGUMENTS_INVALID",
            repair={"field": "label", "expected_type": "string"},
        )
    result = value.strip()
    if not result or len(result) > 160:
        raise NativeManufactureError(
            f"A {noun} label must contain 1 through 160 characters after trimming.",
            error_code="NATIVE_ARGUMENTS_INVALID",
            repair={
                "field": "label",
                "minimum_length": 1,
                "maximum_length": 160,
                "surrounding_spaces": "trimmed",
            },
        )
    rejected = next(
        (
            character
            for character in result
            if ord(character) < 0x20
            or ord(character) > 0x7E
            or character in "()"
        ),
        None,
    )
    if rejected is not None:
        raise NativeManufactureError(
            f"A {noun} label must use printable ASCII without parentheses or line breaks.",
            error_code="NATIVE_ARGUMENTS_INVALID",
            repair={
                "field": "label",
                "accepted": "printable ASCII 0x20 through 0x7e except ( and )",
                "rejected_codepoint": f"U+{ord(rejected):04X}",
            },
        )
    return result
