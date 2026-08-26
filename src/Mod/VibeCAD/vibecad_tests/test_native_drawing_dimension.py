# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact explicit Native Drawing dimensions."""

from __future__ import annotations

import json

import pytest

import VibeCADNativeDrawingDimension as dimension_module
from VibeCADNativeCapabilityRegistry import provider_visible_native_schema
from VibeCADNativeDrawingDimension import (
    DimensionSpec,
    _dimension_state_mismatches,
    _matches_document_label,
)
from VibeCADNativeDrawingDimensionSchema import (
    DRAWING_DIMENSION_CAPABILITY_BY_OPERATION,
    DRAWING_DIMENSION_CAPABILITY_NAMES,
    drawing_dimension_capability_definitions,
)
from VibeCADNativeDrawingDimensionInferenceSchema import (
    drawing_dimension_inference_capability_definition,
)
from VibeCADNativeDrawingDimensionSupport import (
    drawing_label_position_in_view_mm,
    drawing_position_within_page_bounds,
    provider_drawing_dimension_state,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingDimensionRuntime import (
    _normalized_arguments,
    _specific_operation,
)
from VibeCADNativeDrawingDimensionState import drawing_dimension_state


def _branches() -> dict[str, dict]:
    return {
        definition.variants[0].operation: provider_visible_native_schema(
            definition.provider_schema((definition.variants[0].operation,))
        )["parameters"]["oneOf"][0]
        for definition in drawing_dimension_capability_definitions()
    }


def test_dimensions_are_focused_single_operation_tools() -> None:
    definitions = drawing_dimension_capability_definitions()
    branches = _branches()

    assert tuple(definition.name for definition in definitions) == (
        DRAWING_DIMENSION_CAPABILITY_NAMES
    )
    assert tuple(branches) == tuple(DRAWING_DIMENSION_CAPABILITY_BY_OPERATION)
    assert all(len(definition.variants) == 1 for definition in definitions)
    assert len(set(DRAWING_DIMENSION_CAPABILITY_NAMES)) == len(
        DRAWING_DIMENSION_CAPABILITY_NAMES
    )
    assert "drawing.dimension" not in DRAWING_DIMENSION_CAPABILITY_NAMES
    assert "drawing.dimension_infer" not in DRAWING_DIMENSION_CAPABILITY_NAMES
    assert all(branch["additionalProperties"] is False for branch in branches.values())
    assert all("operation" not in branch["properties"] for branch in branches.values())
    for operation, branch in branches.items():
        if operation in {"create_area_annotation", "create_arc_length_annotation", "edit"}:
            continue
        assert "label_position_on_page_mm" in branch["required"]
        assert "label_position_in_view_mm" not in branch["properties"]
    assert set(branches["create_radial"]["required"]) >= {"edge", "kind"}
    assert "allow_approximate" not in branches["create_radial"]["required"]
    assert branches["create_radial"]["properties"]["allow_approximate"][
        "default"
    ] is False
    assert branches["create_radial"]["properties"]["kind"]["enum"] == [
        "radius",
        "diameter",
    ]
    assert branches["create_linear"]["properties"]["direction"]["enum"] == [
        "aligned",
        "horizontal",
        "vertical",
    ]
    linear_references = branches["create_linear"]["properties"]["references"]
    assert "parallel" in linear_references["description"]
    assert "differ on the chosen axis" in linear_references["description"]
    assert set(branches["create_angle"]["required"]) >= {
        "first_edge",
        "second_edge",
    }
    assert set(branches["create_three_point_angle"]["required"]) >= {
        "first_arm_point",
        "apex_point",
        "second_arm_point",
    }
    assert set(branches["create_area"]["required"]) >= {"face"}
    view_extent = branches["create_view_extent"]
    edge_extent = branches["create_edge_extent"]
    assert set(view_extent["required"]) >= {"direction"}
    assert "extent" not in view_extent["properties"]
    assert "edges" not in view_extent["properties"]
    assert set(edge_extent["required"]) >= {"edges", "direction"}
    assert edge_extent["properties"]["edges"]["maxItems"] == 64
    assert "combined" in edge_extent["properties"]["edges"]["description"]
    assert "extent" not in edge_extent["properties"]
    for branch in (view_extent, edge_extent):
        assert branch["properties"]["direction"]["enum"] == [
            "horizontal",
            "vertical",
        ]
    axonometric = branches["create_axonometric_length"]
    assert set(axonometric["required"]) >= {
        "measurement",
        "extension_direction_edge",
        "expected_value_mode",
    }
    measurement = axonometric["properties"]["measurement"]
    assert measurement["properties"]["kind"]["enum"] == ["edge", "vertex_pair"]
    assert "Fields: edge=dimension_edge" in measurement["description"]
    assert axonometric["properties"]["expected_value_mode"]["enum"] == [
        "projected",
        "x_axis_true_length",
        "y_axis_true_length",
        "z_axis_true_length",
    ]
    encoded = json.dumps(
        [
            provider_visible_native_schema(
                definition.provider_schema((definition.variants[0].operation,))
            )
            for definition in definitions
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 28 * 1024
    assert "overall width or height" in next(
        definition.description
        for definition in definitions
        if definition.name == "drawing.view_extent_dimension"
    )
    assert "selected subset" in next(
        definition.description
        for definition in definitions
        if definition.name == "drawing.edge_extent_dimension"
    )


def test_dimension_inference_uses_page_coordinates() -> None:
    definition = drawing_dimension_inference_capability_definition()
    branch = provider_visible_native_schema(
        definition.provider_schema(("infer",))
    )["parameters"]["oneOf"][0]

    assert "label_position_on_page_mm" in branch["required"]
    assert "label_position_in_view_mm" not in branch["properties"]


def _line(name: str, start: tuple[float, float], end: tuple[float, float]) -> dict:
    return {
        "name": name,
        "element_type": "edge",
        "geometry_type": "Line",
        "closed": False,
        "start_in_view_mm": {"x_mm": start[0], "y_mm": start[1]},
        "end_in_view_mm": {"x_mm": end[0], "y_mm": end[1]},
    }


def test_linear_dimension_preflight_reports_exact_valid_pair_directions() -> None:
    horizontal_pair = (
        _line("Edge1", (-20.0, -5.0), (20.0, -5.0)),
        _line("Edge3", (-20.0, 1.0), (20.0, 1.0)),
    )

    with pytest.raises(NativeDrawingError) as caught:
        dimension_module._validate_linear_reference_geometry(
            "create_horizontal",
            horizontal_pair,
        )

    assert caught.value.failure() == {
        "error_code": "NATIVE_DRAWING_DIMENSION_REFERENCES_INVALID",
        "message": (
            "Projected line references Edge1 and Edge3 cannot measure "
            "horizontal separation."
        ),
        "repair": {
            "requested_subelements": ["Edge1", "Edge3"],
            "valid_directions": ["aligned", "vertical"],
        },
    }
    dimension_module._validate_linear_reference_geometry(
        "create_vertical",
        horizontal_pair,
    )


def test_linear_dimension_preflight_rejects_curves_and_accepts_distinct_points() -> None:
    circle = {
        "name": "Edge4",
        "element_type": "edge",
        "geometry_type": "Circle",
        "closed": True,
        "start_in_view_mm": {"x_mm": 3.0, "y_mm": 0.0},
        "end_in_view_mm": {"x_mm": 3.0, "y_mm": 0.0},
    }
    with pytest.raises(NativeDrawingError, match="not a projected line"):
        dimension_module._validate_linear_reference_geometry(
            "create_length",
            (circle,),
        )

    vertices = (
        {
            "name": "Vertex0",
            "element_type": "vertex",
            "point_in_view_mm": {"x_mm": 0.0, "y_mm": 0.0},
        },
        {
            "name": "Vertex1",
            "element_type": "vertex",
            "point_in_view_mm": {"x_mm": 10.0, "y_mm": 0.0},
        },
    )
    dimension_module._validate_linear_reference_geometry(
        "create_horizontal",
        vertices,
    )


def test_dimension_page_position_is_converted_only_at_the_host_boundary(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "VibeCADNativeDrawingDimensionSupport.drawing_view_position_on_page",
        lambda _view: {"x_mm": 120.0, "y_mm": 85.0},
    )

    assert drawing_label_position_in_view_mm(
        object(),
        {"x_mm": 142.5, "y_mm": 72.0},
    ) == {"x_mm": 22.5, "y_mm": -13.0}
    assert provider_drawing_dimension_state(
        {
            "object_name": "Dimension",
            "label_position_in_view_mm": {"x_mm": 22.5, "y_mm": -13.0},
        },
        object(),
    ) == {
        "object_name": "Dimension",
        "label_position_on_page_mm": {"x_mm": 142.5, "y_mm": 72.0},
    }


def test_drawing_page_positions_fail_before_mutation_outside_template_bounds(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "VibeCADNativeDrawingDimensionSupport.drawing_page_state",
        lambda _page: {
            "template_geometry": {
                "drawing_bounds_mm": {
                    "min_x_mm": 20.0,
                    "min_y_mm": 10.0,
                    "max_x_mm": 287.0,
                    "max_y_mm": 200.0,
                }
            }
        },
    )

    assert drawing_position_within_page_bounds(
        object(),
        {"x_mm": 80.0, "y_mm": 30.0},
        noun="dimension label",
        error_code="NATIVE_DRAWING_DIMENSION_PLACEMENT_INVALID",
    ) == {"x_mm": 80.0, "y_mm": 30.0}

    with pytest.raises(NativeDrawingError) as caught:
        drawing_position_within_page_bounds(
            object(),
            {"x_mm": 10.0, "y_mm": 30.0},
            noun="dimension label",
            error_code="NATIVE_DRAWING_DIMENSION_PLACEMENT_INVALID",
        )

    assert caught.value.failure() == {
        "error_code": "NATIVE_DRAWING_DIMENSION_PLACEMENT_INVALID",
        "message": "The Drawing dimension label position is outside the drawing area.",
        "repair": {
            "drawing_bounds_mm": {
                "min_x_mm": 20.0,
                "min_y_mm": 10.0,
                "max_x_mm": 287.0,
                "max_y_mm": 200.0,
            },
            "requested_position_on_page_mm": {"x_mm": 10.0, "y_mm": 30.0},
        },
    }


def test_drawing_page_positions_use_exact_page_bounds_without_declared_area(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "VibeCADNativeDrawingDimensionSupport.drawing_page_state",
        lambda _page: {
            "template_geometry": {
                "width_mm": 297.0,
                "height_mm": 210.0,
                "drawing_bounds_mm": None,
            }
        },
    )

    assert drawing_position_within_page_bounds(
        object(),
        {"x_mm": 80.0, "y_mm": 30.0},
        noun="dimension label",
        error_code="NATIVE_DRAWING_DIMENSION_PLACEMENT_INVALID",
    ) == {"x_mm": 80.0, "y_mm": 30.0}

    with pytest.raises(NativeDrawingError, match="outside the drawing area"):
        drawing_position_within_page_bounds(
            object(),
            {"x_mm": 298.0, "y_mm": 30.0},
            noun="dimension label",
            error_code="NATIVE_DRAWING_DIMENSION_PLACEMENT_INVALID",
        )


def test_focused_extent_tools_lower_to_the_exact_host_operation() -> None:
    common = {"label": "Height"}
    operation, values = _specific_operation(
        "create_view_extent",
        {**common, "direction": "vertical"},
    )
    assert operation == "create_vertical_extent"
    assert values == {**common, "extent": {"scope": "whole_view"}}

    edges = [{"subelement": "Edge0"}, {"subelement": "Edge2"}]
    operation, values = _specific_operation(
        "create_edge_extent",
        {**common, "direction": "horizontal", "edges": edges},
    )
    assert operation == "create_horizontal_extent"
    assert values == {**common, "extent": {"scope": "edges", "edges": edges}}


def test_radial_tool_defaults_to_exact_geometry() -> None:
    normalized = _normalized_arguments(
        {"operation": "create_radial", "kind": "diameter"}
    )
    assert normalized["allow_approximate"] is False

    operation, values = _specific_operation(
        "create_radial",
        {"label": "Bore", "kind": "diameter", "edge": {"subelement": "Edge0"}},
    )

    assert operation == "create_diameter"
    assert values["allow_approximate"] is False


def test_projected_dimension_guards_stay_internal() -> None:
    definitions = drawing_dimension_capability_definitions()
    for definition, (operation, branch) in zip(
        definitions,
        _branches().items(),
        strict=True,
    ):
        view = branch.get("properties", {}).get("view")
        if view is None:
            continue
        assert view["required"] == ["object_name"]
        assert set(view["properties"]) == {"object_name"}
        internal = definition.provider_schema((operation,))["parameters"]["oneOf"][0]
        assert internal["properties"]["view"]["required"] == [
            "object_name",
            "expected_state_sha256",
            "expected_projection_state_sha256",
        ]
        encoded = json.dumps(branch, sort_keys=True, separators=(",", ":"))
        assert "expected_element_state_sha256" not in encoded


def test_dimension_editor_declares_complete_state_replacement_concisely() -> None:
    definition = next(
        item
        for item in drawing_dimension_capability_definitions()
        if item.name == "drawing.edit_dimension"
    )

    assert definition.description == (
        "Replace a dimension's complete display, tolerance, layout, and appearance."
    )


def test_document_label_matching_accepts_only_freecad_unique_forms() -> None:
    assert _matches_document_label("Width", "Width")
    assert _matches_document_label("Width001", "Width")
    assert _matches_document_label("Width125", "Width009")
    assert _matches_document_label("124", "123")
    assert not _matches_document_label("Width01", "Width")
    assert not _matches_document_label("Other001", "Width")
    assert not _matches_document_label("Width001extra", "Width")


def test_dimension_postcondition_identifies_a_zero_directional_measurement() -> None:
    spec = DimensionSpec(
        operation="create_vertical",
        dimension_type="DistanceY",
        label="Height",
        x_mm=-6.0,
        y_mm=8.0,
        subelements=("Edge0", "Edge2"),
        allow_approximate=False,
    )
    state = {
        "label": "Height",
        "page_name": "Page",
        "view_name": "Front",
        "dimension_type": "DistanceY",
        "measure_type": "Projected",
        "references": [
            {"view_name": "Front", "subelement": "Edge0"},
            {"view_name": "Front", "subelement": "Edge2"},
        ],
        "label_position_in_view_mm": {"x_mm": -6.0, "y_mm": 8.0},
        "measured_value": {"value": 0.0, "unit": "mm"},
        "timeline_role": "operation",
        "timeline_owner_name": "",
        "timeline_usable": True,
        "valid": True,
    }

    assert _dimension_state_mismatches(
        state,
        spec=spec,
        page_name="Page",
        view_name="Front",
        is_extent=False,
    ) == ("measured_value",)


class _Document:
    @staticmethod
    def isObjectUsableAtCurrentTimelinePosition(_obj) -> bool:
        return True


class _Object:
    def __init__(self, name: str, document: _Document) -> None:
        self.Name = name
        self.Document = document


class _Dimension(_Object):
    TypeId = "TechDraw::DrawViewDimension"
    Type = "Distance"
    MeasureType = "Projected"
    Label = "Width"
    X = 12.0
    Y = 8.0
    VibeCADTimelineRole = "operation"
    VibeCADTimelineOwner = None

    def __init__(self) -> None:
        document = _Document()
        super().__init__("Dimension", document)
        self.view = _Object("View", document)
        self.page = _Object("Page", document)
        self.References2D = ((self.view, ("Edge0",)),)
        self.State = ("Up-to-date",)

    @staticmethod
    def isDerivedFrom(type_id: str) -> bool:
        return type_id == "TechDraw::DrawViewDimension"

    def findParentPage(self):
        return self.page

    @staticmethod
    def getRawValue() -> float:
        return 40.0

    @staticmethod
    def getText() -> str:
        return "40.00 mm"

    @staticmethod
    def isValid() -> bool:
        return True


def test_transient_document_status_is_reported_but_not_hashed() -> None:
    dimension = _Dimension()
    current = drawing_dimension_state(dimension)
    dimension.State = ("Touched",)
    touched = drawing_dimension_state(dimension)

    assert current["state_messages"] == ["Up-to-date"]
    assert touched["state_messages"] == ["Touched"]
    assert touched["state_sha256"] == current["state_sha256"]


def test_dimension_display_changes_never_expose_a_literal_as_numeric_format() -> None:
    from VibeCADNativeDrawingDimensionEdit import _apply_display

    class RecordedDimension:
        def __init__(self, arbitrary: bool) -> None:
            self._arbitrary = arbitrary
            self.events: list[tuple] = []

        @property
        def Arbitrary(self) -> bool:
            return self._arbitrary

        @Arbitrary.setter
        def Arbitrary(self, value: bool) -> None:
            self._arbitrary = value
            self.events.append(("arbitrary", value))

        @property
        def FormatSpec(self) -> str:
            return ""

        @FormatSpec.setter
        def FormatSpec(self, value: str) -> None:
            self.events.append(("format", value, self._arbitrary))

    literal = RecordedDimension(False)
    _apply_display(literal, {"format_spec": "20.0 mm", "arbitrary": True})
    assert literal.events == [
        ("arbitrary", True),
        ("format", "20.0 mm", True),
    ]

    measured = RecordedDimension(True)
    _apply_display(measured, {"format_spec": "Edited %.3f", "arbitrary": False})
    assert measured.events == [
        ("format", "Edited %.3f", True),
        ("arbitrary", False),
    ]
