# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact Drawing rich-text annotations."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingRichAnnotation import _normalize_host_plan
from VibeCADNativeDrawingRichAnnotationSchema import (
    DRAWING_RICH_ANNOTATION_CAPABILITY_NAME,
    DRAWING_RICH_ANNOTATION_OPERATIONS,
    drawing_rich_annotation_capability_definition,
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


def test_rich_annotation_schema_has_three_sharp_closed_branches() -> None:
    definition = drawing_rich_annotation_capability_definition()
    schema = definition.provider_schema(DRAWING_RICH_ANNOTATION_OPERATIONS)
    branches = schema["parameters"]["oneOf"]
    by_operation = {
        branch["properties"]["operation"]["const"]: branch
        for branch in branches
    }

    assert definition.name == DRAWING_RICH_ANNOTATION_CAPABILITY_NAME
    assert tuple(by_operation) == DRAWING_RICH_ANNOTATION_OPERATIONS
    assert by_operation["read_defaults"]["required"] == ["operation"]
    assert by_operation["read_defaults"]["additionalProperties"] is False

    plain = by_operation["create_plain_text"]
    rich = by_operation["create_rich_text"]
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

    for branch in (plain, rich):
        assert branch["additionalProperties"] is False
        assert branch["properties"]["page"]["additionalProperties"] is False
        assert branch["properties"]["placement_on_page_mm"][
            "additionalProperties"
        ] is False
        assert branch["properties"]["frame"]["additionalProperties"] is False
        owner = branch["properties"]["owner"]["oneOf"]
        assert [item["properties"]["kind"]["const"] for item in owner] == [
            "page",
            "view",
        ]
        width = branch["properties"]["width"]["oneOf"]
        assert [item["properties"]["mode"]["const"] for item in width] == [
            "automatic",
            "fixed",
        ]

    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "file_path" not in encoded.casefold()
    assert "data_url" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 14 * 1024


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


def test_rich_annotation_registry_has_one_definition_and_implementation() -> None:
    registry = build_native_capability_registry()

    definition = registry.definition(DRAWING_RICH_ANNOTATION_CAPABILITY_NAME)
    implementation = registry.implementation(DRAWING_RICH_ANNOTATION_CAPABILITY_NAME)
    assert definition is not None
    assert implementation is not None
    assert tuple(item.operation for item in definition.variants) == (
        DRAWING_RICH_ANNOTATION_OPERATIONS
    )


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
