# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADNativeCommonBindings import COMMON_NATIVE_CAPABILITY_NAMES
from VibeCADNativeAssemblyStructureBindings import (
    ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import MAX_NATIVE_SCHEMAS_JSON_BYTES
from VibeCADNativeComponentInterfaceBindings import (
    COMPONENT_INTERFACE_CAPABILITY_NAME,
)
from VibeCADNativeModelDressupBindings import MODEL_DRESSUP_CAPABILITY_NAME
from VibeCADNativeModelCatalogBindings import MODEL_CATALOG_CAPABILITY_NAME
from VibeCADNativeModelBooleanBindings import MODEL_BOOLEAN_CAPABILITY_NAME
from VibeCADNativeModelFeatureBindings import MODEL_FEATURE_CAPABILITY_NAMES
from VibeCADNativeModelFastenerBindings import MODEL_FASTENER_CAPABILITY_NAME
from VibeCADNativeModelHoleBindings import MODEL_HOLE_CAPABILITY_NAME
from VibeCADNativeModelJoinBindings import MODEL_JOIN_CAPABILITY_NAME
from VibeCADNativeModelPartBindings import MODEL_PART_CAPABILITY_NAME
from VibeCADNativeModelStructureBindings import MODEL_STRUCTURE_CAPABILITY_NAMES
from VibeCADNativeModelSurfaceBindings import MODEL_SURFACE_CAPABILITY_NAME
from VibeCADNativeModelTransformBindings import MODEL_TRANSFORM_CAPABILITY_NAME
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeSketchBatchBindings import SKETCH_BATCH_CAPABILITY_NAME
from VibeCADNativeSketchConstraintBindings import SKETCH_CONSTRAINT_CAPABILITY_NAME
from VibeCADNativeSketchControlBindings import SKETCH_CONTROL_CAPABILITY_NAME
from VibeCADNativeSketchGeometryBindings import SKETCH_GEOMETRY_CAPABILITY_NAME
from VibeCADNativeSketchInspectBindings import SKETCH_INSPECT_CAPABILITY_NAME
from VibeCADNativeSketchPresentationBindings import (
    SKETCH_PRESENTATION_CAPABILITY_NAME,
)


def test_production_registry_assembles_finished_contracts_and_bindings_exactly() -> None:
    registry = build_native_capability_registry()

    assert registry.shared_definition_names == (
        *COMMON_NATIVE_CAPABILITY_NAMES,
        "model.catalog",
        SKETCH_BATCH_CAPABILITY_NAME,
    )
    expected = (
        *COMMON_NATIVE_CAPABILITY_NAMES,
        ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
        COMPONENT_INTERFACE_CAPABILITY_NAME,
        MODEL_BOOLEAN_CAPABILITY_NAME,
        *MODEL_STRUCTURE_CAPABILITY_NAMES,
        *MODEL_FEATURE_CAPABILITY_NAMES,
        MODEL_FASTENER_CAPABILITY_NAME,
        MODEL_DRESSUP_CAPABILITY_NAME,
        MODEL_CATALOG_CAPABILITY_NAME,
        MODEL_HOLE_CAPABILITY_NAME,
        MODEL_JOIN_CAPABILITY_NAME,
        MODEL_PART_CAPABILITY_NAME,
        MODEL_SURFACE_CAPABILITY_NAME,
        MODEL_TRANSFORM_CAPABILITY_NAME,
        SKETCH_BATCH_CAPABILITY_NAME,
        SKETCH_CONSTRAINT_CAPABILITY_NAME,
        SKETCH_CONTROL_CAPABILITY_NAME,
        SKETCH_GEOMETRY_CAPABILITY_NAME,
        SKETCH_INSPECT_CAPABILITY_NAME,
        SKETCH_PRESENTATION_CAPABILITY_NAME,
    )
    assert registry.definition_names == tuple(sorted(expected))
    assert registry.implementation_names == registry.definition_names


def test_production_registry_is_fresh_and_has_no_document_or_gui_state() -> None:
    first = build_native_capability_registry()
    second = build_native_capability_registry()

    assert first is not second
    assert first.definition_names == second.definition_names
    assert all(
        first.implementation(name) is not second.implementation(name)
        for name in first.implementation_names
    )


def test_current_registered_model_contracts_fit_the_hard_schema_budget() -> None:
    registry = build_native_capability_registry()
    schemas = []
    for name in registry.definition_names:
        definition = registry.definition(name)
        operations = tuple(
            variant.operation
            for variant in definition.variants
            if "model" in variant.surface_ids
        )
        if operations:
            schemas.append(definition.provider_schema(operations))

    encoded = json.dumps(
        schemas,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES
