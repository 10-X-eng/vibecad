# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from VibeCADNativeCommonBindings import COMMON_NATIVE_CAPABILITY_NAMES
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeDrawingDimensionSchema import DRAWING_DIMENSION_CAPABILITY_NAMES
from VibeCADNativeDrawingPlacementSchema import DRAWING_PLACEMENT_CAPABILITY_NAMES
from VibeCADNativeInspectionCompareSchema import INSPECTION_COMPARE_CAPABILITY_NAME
from VibeCADNativeMeshReconstructParametricSchema import (
    MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME,
)
from VibeCADNativeModelHistoryBindings import MODEL_HISTORY_CAPABILITY_NAMES
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeSketchBatchBindings import SKETCH_BATCH_CAPABILITY_NAME
from VibeCADNativeWorkspaceBindings import WORKSPACE_CAPABILITY_NAME


def test_production_registry_has_every_finished_contract_and_binding() -> None:
    registry = build_native_capability_registry()

    assert registry.shared_definition_names == (
        NATIVE_BACKGROUND_CAPABILITY_NAME,
        MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME,
        *COMMON_NATIVE_CAPABILITY_NAMES,
        INSPECTION_COMPARE_CAPABILITY_NAME,
        WORKSPACE_CAPABILITY_NAME,
        "parameters.read",
        "assembly.connectors",
        "component.interfaces",
        "model.catalog",
        "model.revolution_sketch",
        *MODEL_HISTORY_CAPABILITY_NAMES,
        "drawing.page_readiness",
        *DRAWING_DIMENSION_CAPABILITY_NAMES,
        *DRAWING_PLACEMENT_CAPABILITY_NAMES,
        SKETCH_BATCH_CAPABILITY_NAME,
        "sketch.finish",
    )
    assert set(registry.shared_definition_names) <= set(registry.definition_names)
    assert registry.definition_names == tuple(sorted(registry.definition_names))
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
