# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact Drawing dimension prefix and precision tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from VibeCADNativeDrawingDimensionTextSchema import (
    DRAWING_DIMENSION_TEXT_CAPABILITY_NAME,
    DRAWING_DIMENSION_TEXT_OPERATIONS,
    drawing_dimension_text_capability_definition,
)
from VibeCADNativeDrawingDimensionTextState import (
    NativeDrawingDimensionTextStateError,
    normalize_dimension_text_host_plans,
)
from vibecad_tests.schema_test_helpers import exact_provider_branches


MOD_ROOT = Path(__file__).resolve().parents[2]


def _plan(
    before: str,
    after: str,
    *,
    prefix: str = "",
    decimal_before: int | None = None,
    decimal_after: int | None = None,
    changed: bool = True,
    reason: str = "",
) -> dict:
    return {
        "object_name": "Dimension",
        "format_spec_before": before,
        "format_spec_after": after,
        "inserted_prefix": prefix,
        "decimal_places_before": decimal_before,
        "decimal_places_after": decimal_after,
        "changed": changed,
        "inapplicable_reason": reason,
    }


def test_dimension_text_schema_has_six_closed_exact_branches() -> None:
    definition = drawing_dimension_text_capability_definition()
    schema = definition.provider_schema(DRAWING_DIMENSION_TEXT_OPERATIONS)
    by_operation = exact_provider_branches(
        definition, DRAWING_DIMENSION_TEXT_OPERATIONS
    )

    assert definition.name == DRAWING_DIMENSION_TEXT_CAPABILITY_NAME
    assert "oneOf" not in schema["parameters"]
    assert schema["parameters"]["properties"]["operation"]["enum"] == list(
        DRAWING_DIMENSION_TEXT_OPERATIONS
    )
    assert tuple(by_operation) == DRAWING_DIMENSION_TEXT_OPERATIONS
    for operation, branch in by_operation.items():
        expected = ["operation", "page", "dimensions"]
        if operation == "insert_repetition_prefix":
            expected.append("repeat_count")
            count = branch["properties"]["repeat_count"]
            assert (count["minimum"], count["maximum"]) == (1, 9999)
        assert branch["required"] == expected
        assert branch["additionalProperties"] is False
        dimensions = branch["properties"]["dimensions"]
        assert (dimensions["minItems"], dimensions["maxItems"]) == (1, 64)
        assert dimensions["uniqueItems"] is True
        assert dimensions["items"]["additionalProperties"] is False
        assert set(dimensions["items"]["properties"]) == {
            "object_name",
            "expected_format_state_sha256",
        }
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "selection" not in encoded.casefold()
    assert "format_spec" not in encoded
    assert len(encoded.encode("utf-8")) < 20 * 1024


@pytest.mark.parametrize(
    ("operation", "repetition", "raw", "expected"),
    (
        (
            "insert_diameter_prefix",
            "",
            _plan("%.2f", "⌀%.2f", prefix="⌀"),
            "⌀%.2f",
        ),
        (
            "insert_square_prefix",
            "",
            _plan("%.2f", "□%.2f", prefix="□"),
            "□%.2f",
        ),
        (
            "insert_repetition_prefix",
            "12",
            _plan("%.2f", "12× %.2f", prefix="12× "),
            "12× %.2f",
        ),
        (
            "remove_prefix",
            "",
            _plan("⌀REF %.2f mm", "%.2f mm"),
            "%.2f mm",
        ),
        (
            "increase_decimals",
            "",
            _plan("REF %.2f", "REF %.3f", decimal_before=2, decimal_after=3),
            "REF %.3f",
        ),
        (
            "decrease_decimals",
            "",
            _plan("REF %.2f", "REF %.1f", decimal_before=2, decimal_after=1),
            "REF %.1f",
        ),
    ),
)
def test_dimension_text_state_validates_each_exact_transformation(
    operation: str,
    repetition: str,
    raw: dict,
    expected: str,
) -> None:
    normalized = normalize_dimension_text_host_plans(
        [raw],
        operation=operation,
        repetition_text=repetition,
    )

    assert normalized[0]["format_spec_after"] == expected
    assert normalized[0]["changed"] is True


@pytest.mark.parametrize(
    ("operation", "raw", "reason"),
    (
        (
            "remove_prefix",
            _plan(
                "%.2f",
                "%.2f",
                changed=False,
                reason="the dimension format has no prefix before its precision marker",
            ),
            "no prefix",
        ),
        (
            "increase_decimals",
            _plan(
                "%.9f",
                "%.9f",
                decimal_before=9,
                decimal_after=9,
                changed=False,
                reason="the dimension precision is already at the maximum of 9",
            ),
            "maximum",
        ),
        (
            "decrease_decimals",
            _plan(
                "%.0f",
                "%.0f",
                decimal_before=0,
                decimal_after=0,
                changed=False,
                reason="the dimension precision is already at the minimum of 0",
            ),
            "minimum",
        ),
    ),
)
def test_dimension_text_state_preserves_precise_inapplicable_reasons(
    operation: str,
    raw: dict,
    reason: str,
) -> None:
    normalized = normalize_dimension_text_host_plans([raw], operation=operation)

    assert normalized[0]["changed"] is False
    assert reason in normalized[0]["inapplicable_reason"]


def test_dimension_text_state_rejects_inconsistent_or_duplicate_host_plans() -> None:
    inconsistent = _plan("%.2f", "%.4f", decimal_before=2, decimal_after=4)
    with pytest.raises(
        NativeDrawingDimensionTextStateError,
        match="inconsistent",
    ):
        normalize_dimension_text_host_plans(
            [inconsistent],
            operation="increase_decimals",
        )

    duplicate = _plan("%.2f", "⌀%.2f", prefix="⌀")
    with pytest.raises(
        NativeDrawingDimensionTextStateError,
        match="duplicate",
    ):
        normalize_dimension_text_host_plans(
            [duplicate, duplicate],
            operation="insert_diameter_prefix",
        )


def test_human_and_native_dimension_text_paths_share_one_compiled_builder() -> None:
    command = (MOD_ROOT / "TechDraw" / "Gui" / "CommandExtensionDims.cpp").read_text(
        encoding="utf-8"
    )
    binding = (MOD_ROOT / "TechDraw" / "Gui" / "AppTechDrawGuiPy.cpp").read_text(
        encoding="utf-8"
    )
    builder = (MOD_ROOT / "TechDraw" / "Gui" / "DimensionTextBuilder.cpp").read_text(
        encoding="utf-8"
    )

    relevant = command[
        command.index("void execInsertPrefixChar") : command.index(
            "// TechDraw_ExtensionPosHorizChainDimension"
        )
    ]
    assert relevant.count("changeDrawingDimensionText(") == 3
    assert "FormatSpec.setValue" not in relevant
    assert "validateDrawingDimensionText" in binding
    assert "changeDrawingDimensionText" in binding
    assert "plan.dimension->FormatSpec.setValue" in builder
    assert 'return repetitionText + "× ";' in builder
