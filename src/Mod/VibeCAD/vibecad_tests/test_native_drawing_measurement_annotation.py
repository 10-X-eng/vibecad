# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact Drawing measurement annotations."""

from __future__ import annotations

import json
from pathlib import Path

from VibeCADNativeActionManifest import _plan
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    provider_visible_native_schema,
)
from VibeCADNativeDrawingDimensionBindings import (
    register_drawing_dimension_capability_implementation,
)
from VibeCADNativeDrawingDimensionSchema import (
    DRAWING_DIMENSION_CAPABILITY_BY_OPERATION,
    drawing_dimension_capability_definitions,
    register_drawing_dimension_capability_definition,
)
from VibeCADNativeDrawingMeasurementAnnotationSchema import (
    DRAWING_MEASUREMENT_ANNOTATION_OPERATIONS,
)
from VibeCADRibbonSurface import RibbonAction


MOD_ROOT = Path(__file__).resolve().parents[2]


def _definition(operation: str):
    return next(
        definition
        for definition in drawing_dimension_capability_definitions()
        if definition.variants[0].operation == operation
    )


def _branch(operation: str) -> dict:
    definition = _definition(operation)
    return provider_visible_native_schema(
        definition.provider_schema((operation,))
    )["parameters"]["oneOf"][0]


def test_measurement_annotation_schema_is_closed_exact_and_host_measured() -> None:
    assert DRAWING_MEASUREMENT_ANNOTATION_OPERATIONS == (
        "create_area_annotation",
        "create_arc_length_annotation",
    )
    area = _branch("create_area_annotation")
    arc = _branch("create_arc_length_annotation")
    for branch in (area, arc):
        assert branch["additionalProperties"] is False
        assert branch["required"] == ["page", "view", "elements", "label"]
        elements = branch["properties"]["elements"]
        assert (elements["minItems"], elements["maxItems"]) == (1, 64)
        assert elements["items"]["additionalProperties"] is False
        assert set(elements["items"]["required"]) == {"subelement"}
        assert "text" not in branch["properties"]
        assert "value" not in branch["properties"]
        assert "placement" not in branch["properties"]
        assert "style" not in branch["properties"]
    assert area["properties"]["elements"]["items"]["properties"][
        "subelement"
    ]["pattern"].startswith("^Face")
    assert arc["properties"]["elements"]["items"]["properties"][
        "subelement"
    ]["pattern"].startswith("^Edge")

    area_variant = _definition("create_area_annotation").variants[0]
    arc_variant = _definition("create_arc_length_annotation").variants[0]
    assert area_variant.action_ids == frozenset(
        {"TechDraw_ExtensionAreaAnnotation"}
    )
    assert arc_variant.action_ids == frozenset(
        {"TechDraw_ExtensionArcLengthAnnotation"}
    )
    assert area_variant.exact_target_type == (
        "ExactDrawingProjectedFacesAndAreaAnnotation"
    )
    assert arc_variant.exact_target_type == (
        "ExactDrawingOrderedProjectedEdgesAndArcLengthAnnotation"
    )
    assert all(
        variant.transaction_behavior == "document"
        and not variant.background_required
        for variant in (area_variant, arc_variant)
    )

    encoded = json.dumps([area, arc], sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 8 * 1024


def test_measurement_actions_resolve_to_their_exact_branches() -> None:
    area = _plan(
        "drawing",
        "Attributes",
        RibbonAction(
            command_id="TechDraw_ExtensionAreaAnnotation",
            label="Area Annotation",
            available=True,
            kind="command",
        ),
    )
    arc = _plan(
        "drawing",
        "Attributes",
        RibbonAction(
            command_id="TechDraw_ExtensionArcLengthAnnotation",
            label="Arc Length Annotation",
            available=True,
            kind="command",
        ),
    )
    assert (
        area.capability_family,
        area.operation_variant,
        area.exact_target_type,
    ) == (
        DRAWING_DIMENSION_CAPABILITY_BY_OPERATION["create_area_annotation"],
        "create_area_annotation",
        "ExactDrawingProjectedFacesAndAreaAnnotation",
    )
    assert (
        arc.capability_family,
        arc.operation_variant,
        arc.exact_target_type,
    ) == (
        DRAWING_DIMENSION_CAPABILITY_BY_OPERATION["create_arc_length_annotation"],
        "create_arc_length_annotation",
        "ExactDrawingOrderedProjectedEdgesAndArcLengthAnnotation",
    )
    assert area.transaction_behavior == arc.transaction_behavior == "document"
    assert not area.background_required and not arc.background_required


def test_measurement_registry_has_definition_and_implementation() -> None:
    registry = NativeCapabilityRegistry()
    register_drawing_dimension_capability_definition(registry)
    register_drawing_dimension_capability_implementation(registry)
    for operation in DRAWING_MEASUREMENT_ANNOTATION_OPERATIONS:
        name = DRAWING_DIMENSION_CAPABILITY_BY_OPERATION[operation]
        assert registry.definition(name) is not None
        assert registry.implementation(name) is not None


def test_human_and_native_share_typed_compiled_measurement_builder() -> None:
    command = (
        MOD_ROOT / "TechDraw" / "Gui" / "CommandExtensionPack.cpp"
    ).read_text(encoding="utf-8")
    builder = (MOD_ROOT / "TechDraw" / "Gui" / "BalloonBuilder.cpp").read_text(
        encoding="utf-8"
    )
    header = (MOD_ROOT / "TechDraw" / "App" / "DrawViewBalloon.h").read_text(
        encoding="utf-8"
    )
    implementation = (
        MOD_ROOT / "TechDraw" / "App" / "DrawViewBalloon.cpp"
    ).read_text(encoding="utf-8")

    assert command.count("createProjectedMeasurementAnnotationFeature(") == 2
    assert "_createBalloon(" not in command
    assert "validateProjectedMeasurementAnnotation(" in builder
    assert "BRepGProp::SurfaceProperties" in builder
    assert "BRepGProp::LinearProperties" in builder
    assert "MeasurementSource.setValue" in builder
    assert "MeasurementValue.setValue" in builder
    assert "MeasurementKind.setValue" in builder
    assert "PropertyEnumeration MeasurementKind" in header
    assert "PropertyLinkSub MeasurementSource" in header
    assert "PropertyFloat MeasurementValue" in header
    assert '"None", "Area", "ArcLength"' in implementation
