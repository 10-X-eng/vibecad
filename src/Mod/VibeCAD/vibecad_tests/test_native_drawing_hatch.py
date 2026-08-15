# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact Drawing image and geometric hatches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from VibeCADNativeDrawingHatchSchema import (
    DRAWING_HATCH_CAPABILITY_NAME,
    DRAWING_HATCH_OPERATIONS,
    drawing_hatch_capability_definition,
)
from VibeCADNativeDrawingHatchState import (
    NativeDrawingHatchStateError,
    normalize_drawing_hatch_plan,
)
from VibeCADNativeLabel import matches_preferred_document_label


MOD_ROOT = Path(__file__).resolve().parents[2]


def _plan(kind: str) -> dict:
    style = {
        "scale": 1.25,
        "rotation_degrees": 30.0,
        "offset_mm": {"x_mm": 2.0, "y_mm": -3.0},
        "color_rgb": {"red": 0.1, "green": 0.2, "blue": 0.3},
    }
    if kind == "geometric":
        style["line_width_mm"] = 0.35
    return {
        "view_name": "View",
        "page_name": "Page",
        "faces": ["Face0", "Face2"],
        "pattern_file_name": "pattern.pat" if kind == "geometric" else "pattern.svg",
        "pattern_name" if kind == "geometric" else "pattern_kind": (
            "ANSI31" if kind == "geometric" else "svg"
        ),
        "style": style,
    }


def test_hatch_schema_has_five_closed_path_free_branches() -> None:
    definition = drawing_hatch_capability_definition()
    schema = definition.provider_schema(DRAWING_HATCH_OPERATIONS)
    branches = schema["parameters"]["oneOf"]
    by_operation = {
        branch["properties"]["operation"]["const"]: branch
        for branch in branches
    }

    assert definition.name == DRAWING_HATCH_CAPABILITY_NAME
    assert tuple(by_operation) == DRAWING_HATCH_OPERATIONS
    assert by_operation["read_defaults"]["required"] == ["operation"]
    for operation in DRAWING_HATCH_OPERATIONS[:-1]:
        branch = by_operation[operation]
        expected = ["operation", "page", "view", "faces", "label", "style"]
        if operation.startswith("create_geometric"):
            expected.append("pattern_name")
        assert branch["required"] == expected
        assert branch["additionalProperties"] is False
        faces = branch["properties"]["faces"]
        assert (faces["minItems"], faces["maxItems"]) == (1, 64)
        assert faces["uniqueItems"] is True
        assert faces["items"]["additionalProperties"] is False
        assert "exact label" in branch["properties"]["label"]["description"]
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert '"path":' not in encoded.casefold()
    assert '"file_path":' not in encoded.casefold()
    assert "pattern_file" not in encoded
    assert len(encoded.encode("utf-8")) < 12 * 1024


@pytest.mark.parametrize("kind", ("image", "geometric"))
def test_hatch_plan_normalizes_complete_host_state(kind: str) -> None:
    normalized = normalize_drawing_hatch_plan(_plan(kind), kind=kind)

    assert normalized["faces"] == ["Face0", "Face2"]
    assert normalized["style"]["rotation_degrees"] == 30.0
    discriminator = "pattern_name" if kind == "geometric" else "pattern_kind"
    assert normalized[discriminator]


@pytest.mark.parametrize(
    ("kind", "mutation"),
    (
        ("image", {"faces": ["Face0", "Face0"]}),
        ("image", {"pattern_kind": ""}),
        ("geometric", {"pattern_name": ""}),
        ("geometric", {"pattern_file_name": ""}),
    ),
)
def test_hatch_plan_rejects_inconsistent_host_state(
    kind: str,
    mutation: dict,
) -> None:
    raw = _plan(kind)
    raw.update(mutation)
    with pytest.raises(NativeDrawingHatchStateError):
        normalize_drawing_hatch_plan(raw, kind=kind)


def test_hatch_labels_follow_the_shared_freecad_unique_label_contract() -> None:
    assert matches_preferred_document_label("Hatch", "Hatch")
    assert matches_preferred_document_label("Hatch001", "Hatch")
    assert matches_preferred_document_label("Hatch125", "Hatch009")
    assert not matches_preferred_document_label("Other001", "Hatch")


def test_human_and_native_hatch_paths_share_one_compiled_builder() -> None:
    task = (MOD_ROOT / "TechDraw" / "Gui" / "TaskHatch.cpp").read_text(
        encoding="utf-8"
    )
    command = (MOD_ROOT / "TechDraw" / "Gui" / "CommandDecorate.cpp").read_text(
        encoding="utf-8"
    )
    binding = (MOD_ROOT / "TechDraw" / "Gui" / "AppTechDrawGuiPy.cpp").read_text(
        encoding="utf-8"
    )
    builder = (MOD_ROOT / "TechDraw" / "Gui" / "HatchBuilder.cpp").read_text(
        encoding="utf-8"
    )

    assert "createDrawingImageHatch(m_dvp" in task
    geometric_command = command[
        command.index("void CmdTechDrawGeometricHatch::activated") : command.index(
            "bool CmdTechDrawGeometricHatch::isActive"
        )
    ]
    assert geometric_command.count("createDrawingGeometricHatch(") == 1
    for function in (
        "drawingHatchDefaults",
        "validateDrawingImageHatch",
        "createDrawingImageHatch",
        "validateDrawingGeometricHatch",
        "createDrawingGeometricHatch",
    ):
        assert function in binding
        assert function in builder
    assert "hatch->recomputeFeature();" in builder
    assert "view->requestPaint();" in builder
