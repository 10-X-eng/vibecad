# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path

import pytest

import VibeCADNativeSketchText as text_module
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchText import (
    create_sketch_text,
    preflight_sketch_text,
    prepare_sketch_text,
    verify_sketch_text,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "text": "AI",
            "font_name": "default",
            "handle_start_mm": {"x": 2.0, "y": 3.0},
            "handle_end_mm": {"x": 42.0, "y": 3.0},
            "sizing_mode": "width",
            **updates,
        }
    )


@pytest.fixture
def text_host(monkeypatch, tmp_path):
    font_path = tmp_path / "osifont-lgpl3fe.ttf"
    font_path.write_bytes(b"fake-font-data")
    monkeypatch.setattr(
        text_module,
        "_available_font_files",
        lambda: {
            "osifont-lgpl3fe": font_path,
            "ExampleFont": tmp_path / "ExampleFont.otf",
        },
    )
    example_path = tmp_path / "ExampleFont.otf"
    example_path.write_bytes(b"another-fake-font")
    return (*install_fake_sketch_host(monkeypatch), font_path, example_path)


def _prepared(document, context, values):
    return preflight_sketch_text(
        context,
        prepare_sketch_text(document.Uid, values),
    )


def test_text_matches_human_text_constraint_topology(text_host) -> None:
    document, sketch, context, _font_path, _example_path = text_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_text(document, prepared)
    result = verify_sketch_text(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert (result["geometry_count"], result["constraint_count"]) == (4, 1)
    assert result["text"] == "AI"
    assert result["font_name"] == "osifont-lgpl3fe"
    assert result["sizing_mode"] == "width"
    assert result["handle"] == {
        "index": 1,
        "type_id": "Part::GeomLineSegment",
        "kind": "line",
        "construction": True,
        "blocked": False,
        "geometry_id": 101,
        "start_mm": [2.0, 3.0, 0.0],
        "end_mm": [42.0, 3.0, 0.0],
        "first_parameter": 0.0,
        "last_parameter": 1.0,
    }
    assert result["text_constraint"] == {
        "index": 0,
        "type": "Text",
        "handle_index": 1,
        "element_count": 3,
    }
    assert result["generated_geometry"] == {
        "count": 2,
        "first_index": 2,
        "last_index": 3,
        "kind_counts": {"line": 2},
        "sha256": result["generated_geometry"]["sha256"],
        "construction": False,
    }
    assert len(result["generated_geometry"]["sha256"]) == 64
    constraint = sketch.Constraints[0]
    assert constraint.Elements == ((1, 0), (2, 0), (3, 0))
    assert constraint.Text == "AI"
    assert constraint.Font == "osifont-lgpl3fe"
    assert constraint.IsTextHeight is False


def test_text_resolves_explicit_font_case_insensitively(text_host) -> None:
    document, _sketch, context, _font_path, _example_path = text_host
    prepared = _prepared(
        document,
        context,
        _values(font_name="examplefont", sizing_mode="height"),
    )

    result = verify_sketch_text(
        document,
        create_sketch_text(document, prepared),
    )

    assert result["font_name"] == "ExampleFont"
    assert result["sizing_mode"] == "height"


@pytest.mark.parametrize(
    "updates",
    (
        {"text": ""},
        {"text": "   "},
        {"text": "two\nlines"},
        {"text": "x" * 65},
        {"font_name": ""},
        {"font_name": " default "},
        {"handle_end_mm": {"x": 2.0, "y": 3.0}},
        {"sizing_mode": "length"},
        {"unexpected": True},
    ),
)
def test_text_rejects_invalid_definition(text_host, updates) -> None:
    document, _sketch, _context, _font_path, _example_path = text_host

    with pytest.raises(NativeSketchError):
        prepare_sketch_text(document.Uid, _values(**updates))


def test_text_rejects_unknown_font_before_mutation(text_host) -> None:
    document, sketch, context, _font_path, _example_path = text_host

    with pytest.raises(NativeSketchError, match="not installed"):
        _prepared(document, context, _values(font_name="MissingFont"))

    assert (sketch.GeometryCount, sketch.ConstraintCount) == (1, 0)


def test_text_rejects_font_file_change_after_preflight(text_host) -> None:
    document, sketch, context, font_path, _example_path = text_host
    prepared = _prepared(document, context, _values())
    Path(font_path).write_bytes(b"changed-font-data-with-new-size")

    with pytest.raises(NativeSketchError, match="changed after preflight"):
        create_sketch_text(document, prepared)

    assert (sketch.GeometryCount, sketch.ConstraintCount) == (1, 0)


def test_text_verifier_rejects_durable_metadata_drift(text_host) -> None:
    document, sketch, context, _font_path, _example_path = text_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_text(document, prepared)
    sketch.Constraints[0].Text = "drifted"

    with pytest.raises(NativeSketchError, match="metadata changed"):
        verify_sketch_text(document, draft)
