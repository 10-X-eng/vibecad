# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contract for exact Native Drawing active-view capture."""

from __future__ import annotations

import json
from types import SimpleNamespace

from VibeCADNativeDrawingActiveView import (
    DEFAULT_CAPTURE_HEIGHT_PX,
    DEFAULT_CAPTURE_WIDTH_PX,
    MAX_CAPTURE_DIMENSION_PX,
    MAX_CAPTURE_PIXELS,
    drawing_active_viewport_state,
)
from VibeCADNativeDrawingActiveViewSchema import (
    drawing_active_view_capability_definition,
)


def _branch(schema: dict, operation: str) -> dict:
    return next(
        branch
        for branch in schema["parameters"]["oneOf"]
        if branch["properties"]["operation"]["const"] == operation
    )


def test_active_view_is_one_closed_path_private_operation() -> None:
    definition = drawing_active_view_capability_definition()
    assert definition.primary_classification == "mutation"
    assert len(definition.variants) == 1
    variant = definition.variants[0]
    assert variant.operation == "create_active_view"
    assert variant.action_ids == frozenset({"TechDraw_ActiveView"})
    assert variant.surface_ids == frozenset({"drawing"})
    assert variant.exact_target_type == (
        "ExactDrawingPageActive3DViewportAndCaptureSettings"
    )
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False

    schema = definition.provider_schema((variant.operation,))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).casefold()
    assert "unknown" not in encoded
    assert "path" not in encoded
    assert "data_url" not in encoded
    branch = _branch(schema, variant.operation)
    assert branch["additionalProperties"] is False
    assert branch["required"] == [
        "label",
        "page",
        "viewport",
        "position",
        "scale",
        "crop",
        "background",
    ]
    assert branch["properties"]["page"]["required"] == [
        "object_name",
        "expected_state_sha256",
    ]
    assert branch["properties"]["viewport"]["required"] == [
        "expected_state_sha256"
    ]


def test_active_view_publishes_all_capture_choices_and_runtime_bounds() -> None:
    definition = drawing_active_view_capability_definition()
    schema = definition.provider_schema(("create_active_view",))
    branch = _branch(schema, "create_active_view")
    crop = branch["properties"]["crop"]["oneOf"]
    background = branch["properties"]["background"]["oneOf"]

    assert tuple(item["properties"]["kind"]["const"] for item in crop) == (
        "full",
        "rectangle",
    )
    assert crop[1]["required"] == ["kind", "width_mm", "height_mm"]
    assert tuple(item["properties"]["kind"]["const"] for item in background) == (
        "transparent",
        "viewport",
        "solid",
    )
    assert background[2]["properties"]["rgb"]["additionalProperties"] is False
    assert DEFAULT_CAPTURE_WIDTH_PX == 1280
    assert DEFAULT_CAPTURE_HEIGHT_PX == 1024
    assert MAX_CAPTURE_DIMENSION_PX == 4096
    assert MAX_CAPTURE_PIXELS == 16 * 1024 * 1024


def test_active_view_state_ignores_hidden_objects_and_fem_artifacts(
    monkeypatch,
) -> None:
    import VibeCADNativeDrawingActiveView as active_view

    class _Shape:
        @staticmethod
        def isNull() -> bool:
            return False

        @staticmethod
        def hashCode() -> int:
            return 42

    class _Object:
        def __init__(self, name: str, type_id: str, *, visible: bool) -> None:
            self.ID = 1
            self.Name = name
            self.TypeId = type_id
            self.ViewObject = SimpleNamespace(Visibility=visible)
            self.Placement = None
            self.Points = None

        @staticmethod
        def getParentGroup():
            return None

        @staticmethod
        def getParentGeoFeatureGroup():
            return None

    class _ExcludedObject(_Object):
        @property
        def Shape(self):
            raise AssertionError("excluded objects must not read Shape")

    visible = _Object("VisibleBody", "PartDesign::Body", visible=True)
    visible.Shape = _Shape()
    hidden = _ExcludedObject("HiddenBody", "PartDesign::Body", visible=False)
    fem = _ExcludedObject(
        "FEMMeshGmsh",
        "Fem::FemMeshShapeBaseObjectPython",
        visible=True,
    )
    analysis = SimpleNamespace(
        Name="Analysis",
        TypeId="Fem::FemAnalysis",
        Group=(fem,),
        ViewObject=SimpleNamespace(Visibility=False),
    )

    document = SimpleNamespace(
        Uid="document-a",
        Objects=(visible,),
    )
    view = SimpleNamespace(
        getSize=lambda: (1280, 1024),
        getCamera=lambda: "camera",
        getCameraType=lambda: "Perspective",
        getViewDirection=lambda: SimpleNamespace(x=0.0, y=0.0, z=-1.0),
        getUpDirection=lambda: SimpleNamespace(x=0.0, y=1.0, z=0.0),
    )
    monkeypatch.setattr(
        active_view,
        "_active_gui_view",
        lambda _document, _gui=None: view,
    )
    monkeypatch.setattr(active_view, "_resolution_pixels_per_mm", lambda: 10.0)
    monkeypatch.setattr(
        active_view,
        "_current_selection",
        lambda current_document: (
            {
                "document_uid": "document-a",
                "selected_count": 0,
                "items": [],
            }
            if len(current_document.Objects) == 1
            else {
                "document_uid": "document-a",
                "selected_count": 2,
                "items": [
                    {"object": {"object_name": hidden.Name}},
                    {"object": {"object_name": fem.Name}},
                ],
            }
        ),
    )

    baseline = drawing_active_viewport_state(document)
    document.Objects = (visible, hidden, fem, analysis)
    filtered = drawing_active_viewport_state(document)

    assert baseline["visible_geometry_count"] == 1
    assert filtered == baseline
