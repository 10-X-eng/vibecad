# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact compiled plans for Drawing dimension prefix and precision changes."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


MAX_DRAWING_DIMENSION_TEXT_TARGETS = 64
MAX_DRAWING_REPETITION_COUNT = 9999
MAX_DRAWING_DIMENSION_FORMAT_CHARACTERS = 512
DRAWING_DIMENSION_TEXT_OPERATIONS = (
    "insert_diameter_prefix",
    "insert_square_prefix",
    "insert_repetition_prefix",
    "remove_prefix",
    "increase_decimals",
    "decrease_decimals",
)
_OBJECT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class NativeDrawingDimensionTextStateError(RuntimeError):
    """A compiled dimension-text plan is malformed or inconsistent."""


def _text(value: Any, noun: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_DRAWING_DIMENSION_FORMAT_CHARACTERS:
        raise NativeDrawingDimensionTextStateError(
            f"Drawing dimension {noun} exceeds 512 characters."
        )
    if "\x00" in value:
        raise NativeDrawingDimensionTextStateError(
            f"Drawing dimension {noun} contains an unsupported null character."
        )
    return value


def _optional_decimal(value: Any, noun: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 9:
        raise NativeDrawingDimensionTextStateError(
            f"Drawing dimension {noun} is not a decimal-place count from 0 to 9."
        )
    return value


def _expected(
    operation: str,
    before: str,
    repetition_text: str,
) -> tuple[str, str, int | None, int | None, str]:
    marker = before.find("%.")
    if operation == "insert_diameter_prefix":
        return "⌀" + before, "⌀", None, None, ""
    if operation == "insert_square_prefix":
        return "□" + before, "□", None, None, ""
    if operation == "insert_repetition_prefix":
        prefix = repetition_text + "× "
        return prefix + before, prefix, None, None, ""
    if operation == "remove_prefix":
        if marker < 0:
            return before, "", None, None, "the dimension format has no precision marker"
        if marker == 0:
            return (
                before,
                "",
                None,
                None,
                "the dimension format has no prefix before its precision marker",
            )
        return before[marker:], "", None, None, ""
    if marker < 0 or marker + 2 >= len(before) or not before[marker + 2].isdigit():
        return (
            before,
            "",
            None,
            None,
            "the dimension format has no single-digit precision marker",
        )
    decimal_before = int(before[marker + 2])
    delta = 1 if operation == "increase_decimals" else -1
    decimal_after = decimal_before + delta
    if not 0 <= decimal_after <= 9:
        reason = (
            "the dimension precision is already at the maximum of 9"
            if delta > 0
            else "the dimension precision is already at the minimum of 0"
        )
        return before, "", decimal_before, decimal_before, reason
    after = before[: marker + 2] + str(decimal_after) + before[marker + 3 :]
    return after, "", decimal_before, decimal_after, ""


def normalize_dimension_text_host_plans(
    raw: Any,
    *,
    operation: str,
    repetition_text: str = "",
) -> list[dict[str, Any]]:
    """Validate a complete ordered compiled plan for one operation."""

    if operation not in DRAWING_DIMENSION_TEXT_OPERATIONS:
        raise NativeDrawingDimensionTextStateError(
            "Drawing dimension-text operation is unsupported."
        )
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not (
        1 <= len(raw) <= MAX_DRAWING_DIMENSION_TEXT_TARGETS
    ):
        raise NativeDrawingDimensionTextStateError(
            "TechDraw returned an unsupported dimension-text target count."
        )
    fields = frozenset(
        {
            "object_name",
            "format_spec_before",
            "format_spec_after",
            "inserted_prefix",
            "decimal_places_before",
            "decimal_places_after",
            "changed",
            "inapplicable_reason",
        }
    )
    result = []
    for item in raw:
        if not isinstance(item, Mapping) or frozenset(item) != fields:
            raise NativeDrawingDimensionTextStateError(
                "TechDraw returned malformed dimension-text plan data."
            )
        object_name = str(item["object_name"] or "")
        if _OBJECT_NAME.fullmatch(object_name) is None or len(object_name) > 128:
            raise NativeDrawingDimensionTextStateError(
                "TechDraw returned an invalid dimension identity."
            )
        before = _text(item["format_spec_before"], "format before")
        after = _text(item["format_spec_after"], "format after")
        prefix = _text(item["inserted_prefix"], "inserted prefix")
        reason = _text(item["inapplicable_reason"], "inapplicable reason")
        changed = item["changed"]
        if type(changed) is not bool:
            raise NativeDrawingDimensionTextStateError(
                "TechDraw returned a non-boolean dimension-text change flag."
            )
        decimal_before = _optional_decimal(
            item["decimal_places_before"], "precision before"
        )
        decimal_after = _optional_decimal(
            item["decimal_places_after"], "precision after"
        )
        expected = _expected(operation, before, repetition_text)
        if (after, prefix, decimal_before, decimal_after, reason) != expected:
            raise NativeDrawingDimensionTextStateError(
                f"TechDraw returned an inconsistent {operation} plan for "
                f"{object_name!r}."
            )
        if changed != (after != before) or changed == bool(reason):
            raise NativeDrawingDimensionTextStateError(
                "TechDraw returned inconsistent dimension-text applicability."
            )
        result.append(
            {
                "object_name": object_name,
                "format_spec_before": before,
                "format_spec_after": after,
                "inserted_prefix": prefix,
                "decimal_places_before": decimal_before,
                "decimal_places_after": decimal_after,
                "changed": changed,
                "inapplicable_reason": reason,
            }
        )
    names = [item["object_name"] for item in result]
    if len(names) != len(set(names)):
        raise NativeDrawingDimensionTextStateError(
            "TechDraw returned duplicate dimension-text targets."
        )
    return result
