# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for exact Native Drawing page operations."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

from VibeCADNativeDrawingPage import (
    BUILT_IN_DRAWING_TEMPLATES,
    built_in_template_relative_path,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingPageSchema import drawing_page_capability_definitions
from VibeCADNativeDrawingReadiness import (
    _inside_bounds,
    require_drawing_export_readiness,
)
from VibeCADNativeDrawingState import _template_geometry


def test_template_drawable_bounds_use_page_coordinates() -> None:
    template = type(
        "_Template",
        (),
        {
            "Width": 297.0,
            "Height": 210.0,
            "Orientation": "Landscape",
            "DrawableX": 20.0,
            "DrawableY": 10.0,
            "DrawableWidth": 267.0,
            "DrawableHeight": 142.0,
            "DrawableClearance": 7.0,
        },
    )()
    geometry = _template_geometry(template)
    assert geometry["drawing_clearance_mm"] == 7.0
    assert geometry["drawing_bounds_mm"] == {
        "min_x_mm": 27.0,
        "min_y_mm": 65.0,
        "max_x_mm": 280.0,
        "max_y_mm": 193.0,
    }


def _branch(schema: dict, operation: str) -> dict:
    return next(
        branch
        for branch in schema["parameters"]["oneOf"]
        if branch["properties"]["operation"]["const"] == operation
    )


def test_page_operations_are_focused_closed_tools() -> None:
    definitions = drawing_page_capability_definitions()
    assert tuple(definition.name for definition in definitions) == (
        "drawing.create_page",
        "drawing.choose_page_template",
        "drawing.template_fields",
        "drawing.redraw_page",
        "drawing.page_updates",
        "drawing.page_readiness",
    )
    assert all(len(definition.variants) == 1 for definition in definitions)
    variants = tuple(definition.variants[0] for definition in definitions)
    operations = tuple(variant.operation for variant in variants)

    assert operations == (
        "page_default",
        "page_template",
        "fill_template_fields",
        "redraw_page",
        "set_keep_updated",
        "inspect_page_readiness",
    )
    assert tuple(
        next(iter(variant.action_ids)) for variant in variants
    ) == (
        "TechDraw_PageDefault",
        "TechDraw_PageTemplate",
        "TechDraw_FillTemplateFields",
        "TechDraw_RedrawPage",
        "TechDrawContextToggleKeepUpdated",
        "VibeCAD_DrawingInspectPageReadiness",
    )
    assert tuple(variant.exact_target_type for variant in variants) == (
        "NewDrawingPageWithConfiguredTemplate",
        "HumanAuthorizedSvgTemplateForNewDrawingPage",
        "ExactDrawingPageAndEditableTemplateFields",
        "ExactDrawingPageAndActiveViewGraph",
        "ExactDrawingPageAndUpdatePolicyState",
        "ExactRenderedDrawingPageReadiness",
    )
    assert all(variant.surface_ids == frozenset({"drawing"}) for variant in variants)
    assert tuple(variant.transaction_behavior for variant in variants) == (
        "document",
        "document",
        "document",
        "background",
        "document",
        "none",
    )
    assert tuple(variant.background_required for variant in variants) == (
        False,
        False,
        False,
        True,
        False,
        False,
    )
    assert tuple(definition.primary_classification for definition in definitions) == (
        "mutation",
        "mutation",
        "mutation",
        "mutation",
        "mutation",
        "read",
    )
    encoded = json.dumps(
        [definition.provider_schema((definition.variants[0].operation,)) for definition in definitions],
        sort_keys=True,
        separators=(",", ":"),
    ).casefold()
    assert "unknown" not in encoded
    assert "path" not in encoded


def test_page_creation_can_select_a_named_built_in_standard_template() -> None:
    definition = drawing_page_capability_definitions()[0]
    branch = _branch(definition.provider_schema(("page_default",)), "page_default")

    assert branch["required"] == []
    template = branch["properties"]["template"]
    assert "iso_a4_landscape" in template["enum"]
    assert "asme_ansi_a_landscape" in template["enum"]
    assert built_in_template_relative_path("iso_a4_landscape") == (
        "Mod/TechDraw/Templates/ISO/A4_Landscape_ISO5457_advanced.svg"
    )


def test_named_built_in_templates_publish_exact_drawable_bounds() -> None:
    template_root = Path(__file__).resolve().parents[2] / "TechDraw" / "Templates"
    namespace = "https://www.freecad.org/wiki/index.php?title=Svg_Namespace"
    names = tuple(
        f"{{{namespace}}}{name}"
        for name in (
            "drawable-x-mm",
            "drawable-y-mm",
            "drawable-width-mm",
            "drawable-height-mm",
            "drawable-clearance-mm",
        )
    )

    for relative in BUILT_IN_DRAWING_TEMPLATES.values():
        root = ElementTree.parse(template_root / relative).getroot()
        values = tuple(float(root.attrib[name]) for name in names)
        width = float(str(root.attrib["width"]).removesuffix("mm"))
        height = float(str(root.attrib["height"]).removesuffix("mm"))
        x, y, drawable_width, drawable_height, clearance = values
        assert 0.0 <= x < x + drawable_width <= width
        assert 0.0 <= y < y + drawable_height <= height
        assert clearance == 7.0
        assert 2.0 * clearance < drawable_width
        assert 2.0 * clearance < drawable_height


def test_iso_unit_system_is_normal_title_block_data() -> None:
    template_root = Path(__file__).resolve().parents[2] / "TechDraw" / "Templates"
    editable = (
        "{https://www.freecad.org/wiki/index.php?title=Svg_Namespace}editable"
    )

    for relative in BUILT_IN_DRAWING_TEMPLATES.values():
        if not relative.startswith("ISO/"):
            continue
        root = ElementTree.parse(template_root / relative).getroot()
        by_id = {
            element.attrib["id"]: element
            for element in root.iter()
            if "id" in element.attrib
        }
        parents = {child: parent for parent in root.iter() for child in parent}
        units = next(
            element
            for element in root.iter()
            if element.attrib.get(editable) == "unit_system"
        )
        label = by_id["unit_system_label"]
        tolerances = by_id["general_tolerances_data_field"]
        scale = by_id["sheet_scale_data_field"]

        assert parents[label].attrib["id"] == "title_block_labels"
        assert parents[units].attrib["id"] == "title_block_data_fields"
        assert float(tolerances.attrib["x"]) < float(units.attrib["x"]) < float(
            scale.attrib["x"]
        )
        assert float(units.attrib["y"]) == float(tolerances.attrib["y"])


def test_asme_unit_system_is_in_the_scale_cell() -> None:
    template_root = Path(__file__).resolve().parents[2] / "TechDraw" / "Templates"

    for relative in BUILT_IN_DRAWING_TEMPLATES.values():
        if not relative.startswith("ASME/"):
            continue
        root = ElementTree.parse(template_root / relative).getroot()
        editable = {
            value.casefold(): element
            for element in root.iter()
            for name, value in element.attrib.items()
            if name.endswith("}editable")
        }
        units = editable["unit_system"]
        scale = editable["scale"]
        weight = editable["weight"]

        assert float(scale.attrib["x"]) < float(units.attrib["x"]) < float(
            weight.attrib["x"]
        )
        assert float(units.attrib["x"]) <= (
            float(scale.attrib["x"]) + float(weight.attrib["x"])
        ) / 2.0
        assert float(units.attrib["x"]) - float(scale.attrib["x"]) >= 9.0
        assert abs(float(units.attrib["y"]) - float(scale.attrib["y"])) < 0.1


def test_template_field_edit_requires_stale_state_and_value_guards() -> None:
    definition = drawing_page_capability_definitions()[2]
    schema = definition.provider_schema(("fill_template_fields",))
    branch = _branch(schema, "fill_template_fields")
    page = branch["properties"]["page"]
    updates = branch["properties"]["updates"]
    update = updates["items"]

    assert branch["required"] == ["page", "updates"]
    assert page["required"] == ["object_name", "expected_state_sha256"]
    assert page["additionalProperties"] is False
    assert updates["minItems"] == 1
    assert updates["maxItems"] == 64
    assert update["required"] == ["field_name", "value"]
    assert update["additionalProperties"] is False
    assert update["properties"]["field_name"]["description"] == (
        "Exact field_name from page.editable_fields."
    )
    assert "expected_value" in update["properties"]
    assert update["properties"]["expected_value"]["description"] == (
        "Optional compare-and-set value."
    )


def test_page_redraw_requires_one_exact_page_and_no_worker_paths() -> None:
    definition = drawing_page_capability_definitions()[3]
    schema = definition.provider_schema(("redraw_page",))
    branch = _branch(schema, "redraw_page")

    assert branch["required"] == ["page"]
    assert branch["additionalProperties"] is False
    assert branch["properties"]["page"]["required"] == [
        "object_name",
        "expected_state_sha256",
    ]
    assert branch["properties"]["page"]["additionalProperties"] is False
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).casefold()
    assert "path" not in encoded
    assert "snapshot" not in encoded


def test_page_readiness_is_one_exact_read_only_page() -> None:
    definition = drawing_page_capability_definitions()[5]
    schema = definition.provider_schema(("inspect_page_readiness",))
    branch = _branch(schema, "inspect_page_readiness")

    assert branch["required"] == ["page"]
    assert branch["additionalProperties"] is False
    assert branch["properties"]["page"]["required"] == [
        "object_name",
        "expected_state_sha256",
    ]
    assert branch["properties"]["offset"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 1_000_000,
        "default": 0,
    }


def test_export_blocks_objective_rendered_collisions() -> None:
    readiness = {
        "page": {"object_name": "Page", "state_sha256": "1" * 64},
        "issues": ["item_collisions"],
        "clipping": {"count": 0},
        "collisions": {"count": 4},
        "references": {"count": 0},
        "duplicate_dimensions": {"count": 0},
    }
    with pytest.raises(NativeDrawingError) as collision_failure:
        require_drawing_export_readiness(readiness)

    assert collision_failure.value.error_code == "NATIVE_DRAWING_OUTPUT_NOT_READY"
    assert collision_failure.value.repair["blocking_issues"] == ["item_collisions"]

    readiness["issues"].append("clipped_items")
    readiness["clipping"] = {"count": 2}
    with pytest.raises(NativeDrawingError) as failure:
        require_drawing_export_readiness(readiness)

    assert failure.value.error_code == "NATIVE_DRAWING_OUTPUT_NOT_READY"
    assert failure.value.repair["blocking_issues"] == [
        "item_collisions",
        "clipped_items",
    ]
    assert failure.value.repair["tool"] == "drawing.page_readiness"

    readiness["issues"] = ["unit_system_missing"]
    readiness["units"] = {
        "supported": True,
        "declared": False,
        "field_name": "unit_system",
        "value": "",
    }
    with pytest.raises(NativeDrawingError) as units_failure:
        require_drawing_export_readiness(readiness)

    assert units_failure.value.repair["blocking_issues"] == [
        "unit_system_missing"
    ]


def test_rendered_bounds_are_checked_against_the_exact_drawing_area() -> None:
    drawing_area = {
        "min_x_mm": 20.0,
        "min_y_mm": 10.0,
        "max_x_mm": 287.0,
        "max_y_mm": 152.0,
    }

    assert _inside_bounds(
        {
            "min_x_mm": 20.0,
            "min_y_mm": 10.0,
            "max_x_mm": 287.0,
            "max_y_mm": 152.0,
        },
        drawing_area,
    )
    assert not _inside_bounds(
        {
            "min_x_mm": 19.0,
            "min_y_mm": 10.0,
            "max_x_mm": 287.0,
            "max_y_mm": 152.0,
        },
        drawing_area,
    )
