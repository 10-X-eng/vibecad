# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from itertools import product

import pytest

from VibeCADNativeActionManifest import (
    ALLOWED_ACTION_IDS_BY_SURFACE,
    KNOWN_ACTIONS_BY_SURFACE,
    NativeActionManifestError,
    classify_native_surface,
    resolve_native_action_inventory,
)
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeSurfaceVariants import (
    NativeSurfaceVariant,
    NativeSurfaceVariantError,
    analyze_surface_variant,
    drawing_surface_variant,
    manufacture_surface_variants,
    validate_surface_variant,
)
from VibeCADRibbonSurface import (
    BUILD_FEATURE_KEYS,
    PREFERENCE_DEFAULTS_BY_SURFACE,
    RibbonSurface,
    RibbonSurfaceEnvironment,
)


def _environment(
    surface_id: str,
    *,
    features: dict[str, bool],
    preferences: dict[str, bool] | None = None,
) -> RibbonSurfaceEnvironment:
    feature_values = {name: False for name in BUILD_FEATURE_KEYS}
    feature_values.update(features)
    preference_values = dict(PREFERENCE_DEFAULTS_BY_SURFACE.get(surface_id, ()))
    preference_values.update(preferences or {})
    return RibbonSurfaceEnvironment.from_mapping(
        {
            "schema_version": 1,
            "build_features": feature_values,
            "preferences": preference_values,
        },
        surface_id=surface_id,
    )


def _manifest_for_variant(
    surface_id: str,
    variant: NativeSurfaceVariant,
) -> dict[str, object]:
    composites = variant.composite_map
    groups = []
    for label, command_ids in variant.groups:
        actions = []
        index = 0
        while index < len(command_ids):
            command_id = command_ids[index]
            children = composites.get(command_id, ())
            if children:
                observed = command_ids[index + 1 : index + 1 + len(children)]
                assert observed == children
                actions.append(
                    {
                        "command_id": command_id,
                        "kind": "composite",
                        "label": command_id,
                        "available": True,
                        "children": [
                            {
                                "command_id": child,
                                "kind": "command",
                                "label": child,
                                "available": True,
                                "parent_command_id": command_id,
                            }
                            for child in children
                        ],
                    }
                )
                index += len(children) + 1
                continue
            actions.append(
                {
                    "command_id": command_id,
                    "kind": "command",
                    "label": command_id,
                    "available": True,
                }
            )
            index += 1
        groups.append({"label": label, "actions": actions})
    return {
        "schema_version": 1,
        "surface_id": surface_id,
        "groups": groups,
    }


def _surface_for_variant(
    surface_id: str,
    variant: NativeSurfaceVariant,
    environment: RibbonSurfaceEnvironment,
) -> RibbonSurface:
    return RibbonSurface.from_manifest(
        _manifest_for_variant(surface_id, variant),
        revision=7,
        environment=environment.to_mapping(),
    )


def test_analyze_matrix_covers_every_valid_vtk_and_netgen_combination() -> None:
    baseline = KNOWN_ACTIONS_BY_SURFACE["analyze"]
    observed_names = set()
    for netgen, (vtk, vtk_python) in product(
        (False, True),
        ((False, False), (True, False), (True, True)),
    ):
        environment = _environment(
            "analyze",
            features={
                "fem": True,
                "fem_netgen": netgen,
                "fem_vtk": vtk,
                "fem_vtk_python": vtk_python,
            },
        )
        variant = analyze_surface_variant(baseline, environment)
        surface = _surface_for_variant("analyze", variant, environment)
        matched = validate_surface_variant(surface, baseline)
        plans = classify_native_surface(surface)

        observed_names.add(variant.name)
        assert matched == variant
        assert tuple(plan.command_id for plan in plans) == variant.command_ids
        assert resolve_native_provider_surface(surface).missing_action_ids == ()
        assert ("FEM_PostCreateFunctions" in variant.composite_map) is vtk
        assert ("FEM_PostVisualization" in variant.composite_map) is vtk_python
        assert "FEM_MeshNetgenFromShape" in variant.command_ids
    assert len(observed_names) == 6


def test_analyze_rejects_impossible_compiled_environments() -> None:
    baseline = KNOWN_ACTIONS_BY_SURFACE["analyze"]
    without_fem = _environment("analyze", features={"fem": False})
    python_without_vtk = _environment(
        "analyze",
        features={"fem": True, "fem_vtk_python": True},
    )

    with pytest.raises(NativeSurfaceVariantError, match="FEM is not compiled"):
        analyze_surface_variant(baseline, without_fem)
    with pytest.raises(NativeSurfaceVariantError, match="requires compiled FEM VTK"):
        analyze_surface_variant(baseline, python_without_vtk)


def test_analyze_classifier_rejects_missing_conditional_action() -> None:
    baseline = KNOWN_ACTIONS_BY_SURFACE["analyze"]
    environment = _environment(
        "analyze",
        features={"fem": True, "fem_vtk": True, "fem_vtk_python": True},
    )
    variant = analyze_surface_variant(baseline, environment)
    groups = tuple(
        (
            label,
            tuple(
                command_id
                for command_id in command_ids
                if command_id != "FEM_PostFilterGlyph"
            ),
        )
        for label, command_ids in variant.groups
    )
    malformed = replace(variant, groups=groups)
    surface = _surface_for_variant("analyze", malformed, environment)

    with pytest.raises(NativeActionManifestError, match="exact compiled/preference"):
        classify_native_surface(surface)


def test_manufacture_matrix_covers_preferences_runtime_options_and_robot() -> None:
    baseline = KNOWN_ACTIONS_BY_SURFACE["manufacture"]
    observed_names = set()
    observed_graphs = set()
    for legacy, advanced, experimental, robot in product((False, True), repeat=4):
        environment = _environment(
            "manufacture",
            features={"cam": True, "robot": robot},
            preferences={
                "cam.default_simulator_legacy": legacy,
                "cam.enable_advanced_ocl_features": advanced,
                "cam.enable_experimental_features": experimental,
            },
        )
        variants = manufacture_surface_variants(baseline, environment)
        assert len(variants) == (4 if advanced else 1)
        for variant in variants:
            surface = _surface_for_variant("manufacture", variant, environment)
            matched = validate_surface_variant(surface, baseline)
            plans = classify_native_surface(surface)
            ocl = "CAM_3dTools" in variant.composite_map
            camotics = "CAM_Camotics" in variant.command_ids
            observed_names.add(variant.name)
            observed_graphs.add((variant.groups, variant.composites))
            assert matched == variant
            assert tuple(plan.command_id for plan in plans) == variant.command_ids
            assert resolve_native_provider_surface(surface).missing_action_ids == ()
            assert variant.composite_map["CAM_SimTools"] == (
                ("CAM_Simulator", "CAM_SimulatorGL")
                if legacy
                else ("CAM_SimulatorGL", "CAM_Simulator")
            ) + ("CAM_RetainSimulationResult",)
            assert ("Area" in dict(variant.groups)) is experimental
            assert ("Robot" in dict(variant.groups)) is robot
            assert ("Export" in dict(variant.groups)) is robot
            assert not ocl or advanced
            assert not camotics or advanced
            if not advanced:
                assert ocl is False
                assert camotics is False
    assert len(observed_names) == 40
    # Eight advanced-enabled environments have neither optional runtime and
    # therefore intentionally render the same graph as advanced-disabled.
    assert len(observed_graphs) == 32


def test_drawing_matrix_covers_every_dimension_preference_combination() -> None:
    baseline = KNOWN_ACTIONS_BY_SURFACE["drawing"]
    observed_commands = set()
    for separated, single in product((False, True), repeat=2):
        environment = _environment(
            "drawing",
            features={"techdraw": True},
            preferences={
                "techdraw.separated_dimensioning_tools": separated,
                "techdraw.single_dimensioning_tool": single,
            },
        )
        variant = drawing_surface_variant(baseline, environment)
        surface = _surface_for_variant("drawing", variant, environment)
        matched = validate_surface_variant(surface, baseline)
        inventory = resolve_native_action_inventory(surface)
        provider = resolve_native_provider_surface(surface)

        observed_commands.update(variant.command_ids)
        assert matched == variant
        assert inventory.required_action_ids == variant.command_ids
        assert tuple(plan.command_id for plan in inventory.plans) == variant.command_ids
        assert provider.missing_action_ids == ()
        assert ("TechDraw_Dimension" in variant.command_ids) is single
        assert ("TechDraw_CompDimensionTools" in variant.composite_map) is (
            single and not separated
        )
        assert ("TechDraw_ExtentGroup" in variant.composite_map) is separated
        assert (
            "TechDraw_ExtensionCreateChainDimensionGroup" in variant.composite_map
        ) is separated
        assert ("TechDraw_ExtensionAreaAnnotation" in variant.command_ids) is separated
    assert observed_commands == ALLOWED_ACTION_IDS_BY_SURFACE["drawing"]


def test_drawing_rejects_surface_without_compiled_techdraw() -> None:
    environment = _environment(
        "drawing",
        features={"techdraw": False},
    )
    with pytest.raises(NativeSurfaceVariantError, match="TechDraw is not compiled"):
        drawing_surface_variant(
            KNOWN_ACTIONS_BY_SURFACE["drawing"],
            environment,
        )


def test_drawing_classifier_rejects_preference_graph_drift() -> None:
    baseline = KNOWN_ACTIONS_BY_SURFACE["drawing"]
    environment = _environment(
        "drawing",
        features={"techdraw": True},
        preferences={
            "techdraw.separated_dimensioning_tools": True,
            "techdraw.single_dimensioning_tool": False,
        },
    )
    variant = drawing_surface_variant(baseline, environment)
    malformed = replace(
        variant,
        groups=tuple(
            (
                label,
                (
                    ("TechDraw_Dimension", *command_ids)
                    if label == "Dimensions"
                    else command_ids
                ),
            )
            for label, command_ids in variant.groups
        ),
    )
    surface = _surface_for_variant("drawing", malformed, environment)

    with pytest.raises(NativeActionManifestError, match="exact compiled/preference"):
        classify_native_surface(surface)


def test_conditional_variant_union_has_no_stale_manifest_actions() -> None:
    analyze_commands = set()
    for netgen, (vtk, vtk_python) in product(
        (False, True),
        ((False, False), (True, False), (True, True)),
    ):
        environment = _environment(
            "analyze",
            features={
                "fem": True,
                "fem_netgen": netgen,
                "fem_vtk": vtk,
                "fem_vtk_python": vtk_python,
            },
        )
        analyze_commands.update(
            analyze_surface_variant(
                KNOWN_ACTIONS_BY_SURFACE["analyze"],
                environment,
            ).command_ids
        )
    assert analyze_commands == ALLOWED_ACTION_IDS_BY_SURFACE["analyze"]

    manufacture_commands = set()
    for legacy, advanced, experimental, robot in product((False, True), repeat=4):
        environment = _environment(
            "manufacture",
            features={"cam": True, "robot": robot},
            preferences={
                "cam.default_simulator_legacy": legacy,
                "cam.enable_advanced_ocl_features": advanced,
                "cam.enable_experimental_features": experimental,
            },
        )
        for variant in manufacture_surface_variants(
            KNOWN_ACTIONS_BY_SURFACE["manufacture"],
            environment,
        ):
            manufacture_commands.update(variant.command_ids)
    assert manufacture_commands == ALLOWED_ACTION_IDS_BY_SURFACE["manufacture"]

    drawing_commands = set()
    for separated, single in product((False, True), repeat=2):
        environment = _environment(
            "drawing",
            features={"techdraw": True},
            preferences={
                "techdraw.separated_dimensioning_tools": separated,
                "techdraw.single_dimensioning_tool": single,
            },
        )
        drawing_commands.update(
            drawing_surface_variant(
                KNOWN_ACTIONS_BY_SURFACE["drawing"],
                environment,
            ).command_ids
        )
    assert drawing_commands == ALLOWED_ACTION_IDS_BY_SURFACE["drawing"]


def test_manufacture_rejects_surface_without_compiled_cam() -> None:
    environment = _environment(
        "manufacture",
        features={"cam": False, "robot": True},
    )
    with pytest.raises(NativeSurfaceVariantError, match="CAM is not compiled"):
        manufacture_surface_variants(
            KNOWN_ACTIONS_BY_SURFACE["manufacture"],
            environment,
        )


def test_manufacture_classifier_rejects_optional_action_without_preference() -> None:
    baseline = KNOWN_ACTIONS_BY_SURFACE["manufacture"]
    environment = _environment(
        "manufacture",
        features={"cam": True, "robot": True},
    )
    variant = manufacture_surface_variants(baseline, environment)[0]
    groups = tuple(
        (
            label,
            ((*command_ids, "CAM_Camotics") if label == "Tools" else command_ids),
        )
        for label, command_ids in variant.groups
    )
    malformed = replace(variant, groups=groups)
    surface = _surface_for_variant("manufacture", malformed, environment)

    with pytest.raises(NativeActionManifestError, match="exact compiled/preference"):
        classify_native_surface(surface)


def test_manufacture_classifier_rejects_same_order_with_wrong_composite() -> None:
    baseline = KNOWN_ACTIONS_BY_SURFACE["manufacture"]
    environment = _environment(
        "manufacture",
        features={"cam": True, "robot": True},
        preferences={"cam.enable_advanced_ocl_features": True},
    )
    variant = next(
        value
        for value in manufacture_surface_variants(baseline, environment)
        if "CAM_3dTools" in value.composite_map
        and "CAM_Camotics" not in value.command_ids
    )
    manifest = _manifest_for_variant("manufacture", variant)
    operations = next(
        group for group in manifest["groups"] if group["label"] == "Operations"
    )
    actions = operations["actions"]
    parent_index = next(
        index
        for index, action in enumerate(actions)
        if action["command_id"] == "CAM_3dTools"
    )
    parent = actions[parent_index]
    children = parent["children"]
    parent["children"] = children[:1]
    actions[parent_index + 1 : parent_index + 1] = [
        {
            "command_id": child["command_id"],
            "kind": "command",
            "label": child["label"],
            "available": True,
        }
        for child in children[1:]
    ]
    surface = RibbonSurface.from_manifest(
        manifest,
        revision=7,
        environment=environment.to_mapping(),
    )
    assert surface.command_ids == variant.command_ids

    with pytest.raises(NativeActionManifestError, match="exact compiled/preference"):
        classify_native_surface(surface)


def test_manufacture_classifier_rejects_same_order_in_wrong_group() -> None:
    baseline = KNOWN_ACTIONS_BY_SURFACE["manufacture"]
    environment = _environment(
        "manufacture",
        features={"cam": True, "robot": True},
        preferences={"cam.enable_experimental_features": True},
    )
    variant = manufacture_surface_variants(baseline, environment)[0]
    manifest = deepcopy(_manifest_for_variant("manufacture", variant))
    modify = next(group for group in manifest["groups"] if group["label"] == "Modify")
    area_index = next(
        index
        for index, group in enumerate(manifest["groups"])
        if group["label"] == "Area"
    )
    area = manifest["groups"].pop(area_index)
    modify["actions"].extend(area["actions"])
    surface = RibbonSurface.from_manifest(
        manifest,
        revision=7,
        environment=environment.to_mapping(),
    )
    assert surface.command_ids == variant.command_ids

    with pytest.raises(NativeActionManifestError, match="exact compiled/preference"):
        classify_native_surface(surface)
