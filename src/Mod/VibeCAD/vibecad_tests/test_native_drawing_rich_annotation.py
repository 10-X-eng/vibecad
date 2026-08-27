# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact Drawing rich-text annotations."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from VibeCADNativeDrawingErrors import NativeDrawingError
import VibeCADNativeDrawingRichAnnotation as rich_annotation
from VibeCADNativeDrawingRichAnnotation import (
    _normalize_host_plan,
    _requested_width,
)
from VibeCADNativeDrawingRichAnnotationSchema import (
    DRAWING_NOTE_CAPABILITY_NAMES,
    drawing_rich_annotation_capability_definitions,
)
from VibeCADNativeDrawingRichAnnotationState import (
    MAX_DRAWING_RICH_ANNOTATION_PROVIDER_CONTENT_CHARACTERS,
)
from VibeCADNativeRegistry import build_native_capability_registry


MOD_ROOT = Path(__file__).resolve().parents[2]
_HOST_ERROR_CODE = "NATIVE_DRAWING_RICH_ANNOTATION_RUNTIME_UNAVAILABLE"


def _host_plan() -> dict:
    return {
        "page_name": "Page",
        "owner": {"kind": "view", "object_name": "View"},
        "object_name": "RichTextAnnotation",
        "label": "Inspection Note",
        "content": {
            "input_kind": "safe_html",
            "stored_html_sha256": "a" * 64,
            "plain_text_sha256": "b" * 64,
            "plain_text_preview": "Inspection note",
            "plain_text_characters": 15,
            "block_count": 1,
            "fragment_count": 2,
            "link_count": 1,
            "has_rich_formatting": True,
        },
        "placement_on_page_mm": {"x_mm": 120.0, "y_mm": 55.0},
        "width": {"mode": "fixed", "value_mm": 48.0},
        "frame": {
            "visible": True,
            "line_width_mm": 0.35,
            "line_style": "dash_dot",
            "color_rgb": {"red": 0.1, "green": 0.2, "blue": 0.3},
        },
    }


def test_notes_are_two_focused_tools_with_natural_optional_style() -> None:
    definitions = drawing_rich_annotation_capability_definitions()
    assert tuple(definition.name for definition in definitions) == (
        "drawing.note",
        "drawing.rich_note",
    )
    assert DRAWING_NOTE_CAPABILITY_NAMES == tuple(
        definition.name for definition in definitions
    )
    assert definitions[0].description == "Create a plain-text Drawing note."
    assert definitions[1].description == "Create a formatted Drawing note."
    branches = [
        definition.provider_schema(("create",))["parameters"]["oneOf"][0]
        for definition in definitions
    ]
    plain, rich = branches
    assert "text" in plain["required"] and "html" not in plain["properties"]
    assert "html" in rich["required"] and "text" not in rich["properties"]
    assert (
        plain["properties"]["text"]["maxLength"]
        == MAX_DRAWING_RICH_ANNOTATION_PROVIDER_CONTENT_CHARACTERS
    )
    assert (
        rich["properties"]["html"]["maxLength"]
        == MAX_DRAWING_RICH_ANNOTATION_PROVIDER_CONTENT_CHARACTERS
    )

    for branch in branches:
        assert branch["additionalProperties"] is False
        assert branch["properties"]["operation"]["const"] == "create"
        assert branch["properties"]["page"]["additionalProperties"] is False
        assert branch["properties"]["placement_on_page_mm"][
            "additionalProperties"
        ] is False
        assert branch["properties"]["placement_on_page_mm"]["description"] == (
            "Page coordinates in mm; use template_geometry width and height."
        )
        assert branch["properties"]["frame"]["additionalProperties"] is False
        assert set(branch["required"]) == {
            "page",
            "label",
            "placement_on_page_mm",
            "text" if branch is plain else "html",
        }
        assert branch["properties"]["owner"] == {
            "default": "page",
            "oneOf": [
                {"type": "string", "const": "page"},
                {
                    "type": "object",
                    "properties": {
                        "object_name": branch["properties"]["page"]["properties"][
                            "object_name"
                        ],
                        "expected_owner_state_sha256": branch["properties"]["page"][
                            "properties"
                        ]["expected_state_sha256"],
                    },
                    "required": ["object_name", "expected_owner_state_sha256"],
                    "additionalProperties": False,
                },
            ],
        }
        assert branch["properties"]["width"] == {
            "default": "automatic",
            "description": "Width in mm wraps text; automatic keeps one line.",
            "oneOf": [
                {"type": "string", "enum": ["auto", "automatic"]},
                {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 1_000_000.0,
                },
            ],
        }
        assert branch["properties"]["frame"]["required"] == []

    encoded = "".join(
        json.dumps(
            definition.provider_schema(("create",)),
            sort_keys=True,
            separators=(",", ":"),
        )
        for definition in definitions
    )
    for unwanted in (
        "unknown",
        "file_path",
        "data_url",
        "rejected",
        "warning",
        "read_defaults",
        "create_plain_text",
        "create_rich_text",
    ):
        assert unwanted not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 14 * 1024


@pytest.mark.parametrize("value", ("auto", "automatic"))
def test_note_width_accepts_natural_automatic_aliases(value: str) -> None:
    assert _requested_width(value) == {"mode": "automatic"}


def test_note_creation_validates_placement_against_the_drawing_area(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Page:
        Name = "Page"

    target = type("Target", (), {"page": Page(), "owner": None})()
    defaults = {
        "frame": {
            "visible": False,
            "line_width_mm": 0.35,
            "line_style": "continuous",
            "color_rgb": {"red": 0.0, "green": 0.0, "blue": 0.0},
        }
    }
    values = {
        "page": {"object_name": "Page", "expected_state_sha256": "a" * 64},
        "label": "Inspection Note",
        "text": "Inspect all edges.",
        "placement_on_page_mm": {"x_mm": 250.0, "y_mm": 10.0},
    }

    monkeypatch.setattr(
        rich_annotation,
        "drawing_rich_annotation_defaults_state",
        lambda: defaults,
    )
    monkeypatch.setattr(rich_annotation, "_target", lambda *args, **kwargs: target)
    monkeypatch.setattr(
        rich_annotation,
        "_host_plan",
        lambda *args, **kwargs: (_host_plan(), None),
    )

    def reject_outside_drawing_area(page, position, *, noun, error_code):
        assert page is target.page
        assert position == values["placement_on_page_mm"]
        assert noun == "note"
        assert error_code == "NATIVE_DRAWING_RICH_ANNOTATION_PLACEMENT_INVALID"
        raise NativeDrawingError(
            "The Drawing note position is outside the drawing area.",
            error_code=error_code,
            repair={
                "drawing_bounds_mm": {
                    "min_x_mm": 27.0,
                    "min_y_mm": 65.0,
                    "max_x_mm": 280.0,
                    "max_y_mm": 193.0,
                },
                "requested_position_on_page_mm": position,
            },
        )

    monkeypatch.setattr(
        rich_annotation,
        "drawing_position_within_page_bounds",
        reject_outside_drawing_area,
        raising=False,
    )

    with pytest.raises(NativeDrawingError) as caught:
        rich_annotation.prepare_drawing_rich_annotation(
            object(),
            operation="plain_text",
            values=values,
        )
    assert caught.value.error_code == "NATIVE_DRAWING_RICH_ANNOTATION_PLACEMENT_INVALID"


def test_rich_annotation_host_plan_preserves_complete_typed_state() -> None:
    raw = _host_plan()
    assert _normalize_host_plan(raw) == raw


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("placement_on_page_mm",), None),
        (("frame", "visible"), 1),
        (("content", "link_count"), "1"),
        (("owner", "kind"), "document"),
    ),
)
def test_rich_annotation_host_plan_rejects_malformed_nested_state(
    path: tuple[str, ...],
    value: object,
) -> None:
    raw = deepcopy(_host_plan())
    target = raw
    for name in path[:-1]:
        target = target[name]
    target[path[-1]] = value

    with pytest.raises(NativeDrawingError) as caught:
        _normalize_host_plan(raw)
    assert caught.value.error_code == _HOST_ERROR_CODE


def test_note_registry_has_two_definitions_and_implementations() -> None:
    registry = build_native_capability_registry()

    assert registry.definition("drawing.rich_annotation") is None
    for name in DRAWING_NOTE_CAPABILITY_NAMES:
        definition = registry.definition(name)
        implementation = registry.implementation(name)
        assert definition is not None
        assert implementation is not None
        assert tuple(item.operation for item in definition.variants) == ("create",)


def test_human_and_native_paths_share_one_compiled_builder_and_scene_policy() -> None:
    task = (MOD_ROOT / "TechDraw" / "Gui" / "TaskRichAnno.cpp").read_text(
        encoding="utf-8"
    )
    builder = (
        MOD_ROOT / "TechDraw" / "Gui" / "RichAnnotationBuilder.cpp"
    ).read_text(encoding="utf-8")
    binding = (MOD_ROOT / "TechDraw" / "Gui" / "AppTechDrawGuiPy.cpp").read_text(
        encoding="utf-8"
    )
    scene = (MOD_ROOT / "TechDraw" / "Gui" / "QGSPage.cpp").read_text(
        encoding="utf-8"
    )
    view = (MOD_ROOT / "TechDraw" / "Gui" / "QGIView.cpp").read_text(
        encoding="utf-8"
    )

    create_task = task[task.index("void TaskRichAnno::createAnnoFeature") :]
    assert create_task.count("createDrawingRichAnnotation(") == 1
    assert builder.count('QStringLiteral("TechDraw::DrawRichAnno")') == 1
    assert "document->publishProvisionalTimelineOperationBlock" in builder
    assert "annotation->requestPaint();" in builder
    for function in (
        "drawingRichAnnotationDefaults",
        "validateDrawingRichAnnotation",
        "createDrawingRichAnnotation",
        "inspectDrawingRichAnnotationContent",
    ):
        assert function in builder
        assert function in binding

    add_rich = scene[scene.index("QGIView* QGSPage::addRichAnno") :]
    add_rich = add_rich[: add_rich.index("void QGSPage::addRichAnnoToParent")]
    assert add_rich.count("addItemToScene(richView)") == 1
    assert "addRichAnnoToParent" not in add_rich
    assert "parentItem()->mapFromScene(position)" in view
    assert "UserType::QGIViewDimension" in view
    assert "UserType::QGIViewBalloon" in view


def test_native_result_state_never_returns_the_stored_html_blob() -> None:
    state = (
        MOD_ROOT
        / "VibeCAD"
        / "VibeCADNativeDrawingRichAnnotationState.py"
    ).read_text(encoding="utf-8")
    implementation = (
        MOD_ROOT / "VibeCAD" / "VibeCADNativeDrawingRichAnnotation.py"
    ).read_text(encoding="utf-8")

    content_return = state[state.index("def _content_state") : state.index("def _color")]
    assert '"stored_html_sha256"' in content_return
    assert '"canonical_html"' not in content_return
    assert '"stored_html"' not in content_return
    verify_return = implementation[implementation.index("def verify_drawing_rich_annotation") :]
    assert '"annotation": state' in verify_return
    assert '"page": {' in verify_return
