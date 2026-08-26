# SPDX-License-Identifier: LGPL-2.1-or-later

"""Drawing provider calls receive exact state guards inside VibeCAD."""

from __future__ import annotations

import pytest

import VibeCADNativeDrawingInternalTargets as targets
from VibeCADNativeDrawingDimensionInferenceSchema import (
    drawing_dimension_inference_capability_definition,
)
from VibeCADNativeTargets import NativeTargetError


_HASH = {
    "expected_state_sha256": "1" * 64,
    "expected_projection_state_sha256": "2" * 64,
}


def _reference(*, projection: bool = False) -> dict:
    properties = {
        "object_name": {"type": "string"},
        "expected_state_sha256": {"type": "string"},
    }
    required = ["object_name", "expected_state_sha256"]
    if projection:
        properties["expected_projection_state_sha256"] = {"type": "string"}
        required.append("expected_projection_state_sha256")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def test_materializer_adds_internal_guards_without_changing_provider_targets(
    monkeypatch,
) -> None:
    schema = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "const": "create"},
            "page": _reference(),
            "view": _reference(projection=True),
        },
        "required": ["operation", "page", "view"],
        "additionalProperties": False,
    }
    observed = []

    def resolve(_document, tool_name, path, _container, _arguments):
        observed.append((tool_name, path))
        return _HASH[path[-1]]

    monkeypatch.setattr(targets, "_resolve_internal_state_field", resolve)
    arguments = {
        "operation": "create",
        "page": {"object_name": "Page"},
        "view": {"object_name": "Front"},
    }

    materialized = targets.materialize_drawing_internal_targets(
        object(),
        "drawing.test",
        schema,
        arguments,
    )

    assert arguments == {
        "operation": "create",
        "page": {"object_name": "Page"},
        "view": {"object_name": "Front"},
    }
    assert materialized == {
        "operation": "create",
        "page": {
            "object_name": "Page",
            "expected_state_sha256": "1" * 64,
        },
        "view": {
            "object_name": "Front",
            "expected_state_sha256": "1" * 64,
            "expected_projection_state_sha256": "2" * 64,
        },
    }
    assert observed == [
        ("drawing.test", ("page", "expected_state_sha256")),
        (
            "drawing.test",
            ("view", "expected_projection_state_sha256"),
        ),
        ("drawing.test", ("view", "expected_state_sha256")),
    ]


def test_materializer_preserves_explicit_compatible_internal_guard(
    monkeypatch,
) -> None:
    schema = _reference()
    monkeypatch.setattr(
        targets,
        "_resolve_internal_state_field",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not resolve")),
    )

    materialized = targets.materialize_drawing_internal_targets(
        object(),
        "drawing.test",
        schema,
        {
            "object_name": "Page",
            "expected_state_sha256": "f" * 64,
        },
    )

    assert materialized["expected_state_sha256"] == "f" * 64


def test_dimension_inference_materializer_keeps_exact_elements_minimal(
    monkeypatch,
) -> None:
    definition = drawing_dimension_inference_capability_definition()
    schema = definition.variants[0].parameters
    monkeypatch.setattr(
        targets,
        "_resolve_internal_state_field",
        lambda _document, _tool, path, _container, _arguments: {
            "expected_state_sha256": "1" * 64,
            "expected_projection_state_sha256": "2" * 64,
        }[path[-1]],
    )

    materialized = targets.materialize_drawing_internal_targets(
        object(),
        "drawing.dimension_infer",
        schema,
        {
            "label": "Overall width",
            "page": {"object_name": "Page"},
            "view": {"object_name": "Front"},
            "label_position_on_page_mm": {"x_mm": 80.0, "y_mm": 30.0},
            "elements": [{"subelement": "Edge1"}],
        },
    )

    assert materialized["elements"] == [{"subelement": "Edge1"}]


def test_projection_guard_rejects_non_projected_view_with_exact_candidates() -> None:
    class DrawingObject:
        def __init__(self, document, name: str, type_id: str) -> None:
            self.Document = document
            self.Name = name
            self.TypeId = type_id

        def isDerivedFrom(self, expected: str) -> bool:
            return self.TypeId == expected

    class Document:
        Uid = "drawing-document"

        def __init__(self) -> None:
            self.Objects = [
                DrawingObject(self, "ProjectionGroup", "TechDraw::DrawProjGroup"),
                DrawingObject(self, "Front", "TechDraw::DrawViewPart"),
            ]

        def getObject(self, name: str):
            return next((obj for obj in self.Objects if obj.Name == name), None)

    document = Document()

    with pytest.raises(NativeTargetError) as caught:
        targets._resolve_internal_state_field(
            document,
            "drawing.projected_geometry",
            ("view", "expected_projection_state_sha256"),
            {"object_name": "ProjectionGroup"},
            {"view": {"object_name": "ProjectionGroup"}},
        )

    assert caught.value.failure() == {
        "error_code": "NATIVE_TARGET_INVALID",
        "message": (
            "The exact object target has type 'TechDraw::DrawProjGroup'; this "
            "operation accepts TechDraw::DrawViewPart."
        ),
        "exact_target": {
            "document_uid": "drawing-document",
            "object_name": "ProjectionGroup",
        },
        "actual_type": "TechDraw::DrawProjGroup",
        "accepted_types": ["TechDraw::DrawViewPart"],
        "candidates": [
            {
                "document_uid": "drawing-document",
                "object_name": "Front",
                "type_id": "TechDraw::DrawViewPart",
            }
        ],
    }


def test_materializer_rejects_page_before_reading_projected_view_state() -> None:
    class DrawingObject:
        def __init__(self, document, name: str, type_id: str) -> None:
            self.Document = document
            self.Name = name
            self.TypeId = type_id

        def isDerivedFrom(self, expected: str) -> bool:
            return self.TypeId == expected

    class Document:
        Uid = "drawing-document"

        def __init__(self) -> None:
            self.Objects = [
                DrawingObject(self, "Page", "TechDraw::DrawPage"),
                DrawingObject(self, "Front", "TechDraw::DrawViewPart"),
            ]

        def getObject(self, name: str):
            return next((obj for obj in self.Objects if obj.Name == name), None)

    with pytest.raises(NativeTargetError) as caught:
        targets.materialize_drawing_internal_targets(
            Document(),
            "drawing.projected_geometry",
            _reference(projection=True),
            {"object_name": "Page"},
        )

    failure = caught.value.failure()
    assert failure["actual_type"] == "TechDraw::DrawPage"
    assert failure["accepted_types"] == ["TechDraw::DrawViewPart"]
    assert [item["object_name"] for item in failure["candidates"]] == ["Front"]
