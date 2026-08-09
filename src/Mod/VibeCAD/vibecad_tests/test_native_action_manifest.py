# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeActionManifest import (
    ALLOWED_ACTION_IDS_BY_SURFACE,
    DEFAULT_SURFACE_ACTION_COUNTS,
    DEFAULT_UNIQUE_ACTION_COUNT,
    KNOWN_ACTIONS_BY_SURFACE,
    KNOWN_COMPOSITE_COMMAND_IDS,
    NativeActionManifestError,
    OPTIONAL_ACTIONS_BY_SURFACE,
    classify_native_surface,
    planned_provider_capability_families,
)
from VibeCADRibbonSurface import RibbonSurface

from vibecad_tests.test_ribbon_surface import _manifest


EXPECTED_DEFAULT_COUNTS = {
    "analyze": 103,
    "assemble": 53,
    "drawing": 107,
    "manufacture": 54,
    "mesh": 60,
    "model": 75,
    "parameters": 24,
    "sketch.edit": 105,
    "sketch.setup": 15,
}

EXPECTED_MODEL_COMPOSITES = {
    "PartDesign_DesignPrimitive": (
        "PartDesign::DesignBox",
        "PartDesign::DesignCylinder",
        "PartDesign::DesignSphere",
        "PartDesign::DesignCone",
        "PartDesign::DesignEllipsoid",
        "PartDesign::DesignTorus",
        "PartDesign::DesignPrism",
        "PartDesign::DesignWedge",
        "PartDesign::DesignTube",
    ),
    "Part_CompOffset": (
        "Part_Offset",
        "Part_Offset2D",
    ),
    "Part_CompJoinFeatures": (
        "Part_JoinConnect",
        "Part_JoinEmbed",
        "Part_JoinCutout",
    ),
}


def _surface(manifest: dict[str, object] | None = None) -> RibbonSurface:
    return RibbonSurface.from_manifest(manifest or _manifest(), revision=3)


def test_default_inventory_has_exact_proven_counts_without_duplicates() -> None:
    assert DEFAULT_SURFACE_ACTION_COUNTS == EXPECTED_DEFAULT_COUNTS
    assert {
        surface_id: len(command_ids)
        for surface_id, command_ids in KNOWN_ACTIONS_BY_SURFACE.items()
        if surface_id != "unavailable"
    } == EXPECTED_DEFAULT_COUNTS
    assert all(
        len(command_ids) == len(set(command_ids))
        for command_ids in KNOWN_ACTIONS_BY_SURFACE.values()
    )
    unique_ids = set().union(
        *(set(command_ids) for command_ids in KNOWN_ACTIONS_BY_SURFACE.values())
    )
    assert len(unique_ids) == DEFAULT_UNIQUE_ACTION_COUNT == 527
    all_known_ids = unique_ids | set().union(
        *(set(command_ids) for command_ids in OPTIONAL_ACTIONS_BY_SURFACE.values())
    )
    assert KNOWN_COMPOSITE_COMMAND_IDS <= all_known_ids


def test_optional_inventory_is_explicit_and_does_not_change_default_counts() -> None:
    assert OPTIONAL_ACTIONS_BY_SURFACE["manufacture"] == (
        "CAM_Area",
        "CAM_Area_Workplane",
        "CAM_Camotics",
        "CAM_3dTools",
        "CAM_Surface",
        "CAM_Waterline",
        "CAM_RotarySurface",
    )
    assert OPTIONAL_ACTIONS_BY_SURFACE["drawing"] == (
        "TechDraw_ExtentGroup",
        "TechDraw_ExtensionAreaAnnotation",
        "TechDraw_ExtensionArcLengthAnnotation",
        "TechDraw_ExtensionCreateChainDimensionGroup",
        "TechDraw_ExtensionCreateCoordDimensionGroup",
        "TechDraw_ExtensionChamferDimensionGroup",
    )
    assert all(
        not (set(default_ids) & set(OPTIONAL_ACTIONS_BY_SURFACE[surface_id]))
        for surface_id, default_ids in KNOWN_ACTIONS_BY_SURFACE.items()
    )
    assert all(
        ALLOWED_ACTION_IDS_BY_SURFACE[surface_id]
        == set(default_ids) | set(OPTIONAL_ACTIONS_BY_SURFACE[surface_id])
        for surface_id, default_ids in KNOWN_ACTIONS_BY_SURFACE.items()
    )


def test_model_inventory_excludes_every_contextual_sketch_edit_action() -> None:
    shared_view = {
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
    }
    model = set(KNOWN_ACTIONS_BY_SURFACE["model"])
    sketch_edit = set(KNOWN_ACTIONS_BY_SURFACE["sketch.edit"])

    assert model & sketch_edit == shared_view
    assert model.isdisjoint(sketch_edit - shared_view)
    assert {
        "Sketcher_NewSketch",
        "Sketcher_EditSketch",
        "Sketcher_ValidateSketch",
    } <= model


def test_model_classifier_rejects_a_sketch_edit_geometry_action() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Geometry",
                "actions": [
                    {
                        "command_id": "Sketcher_CreateLine",
                        "kind": "command",
                        "label": "Line",
                        "available": True,
                    }
                ],
            }
        ],
    }

    with pytest.raises(NativeActionManifestError, match="unclassified actions"):
        classify_native_surface(_surface(manifest))


def _model_composite_manifest() -> dict[str, object]:
    manifest = _manifest()
    groups = manifest["groups"]
    assert isinstance(groups, list)
    groups.insert(
        1,
        {
            "label": "Geometry",
            "actions": [
                {
                    "command_id": "Part_CompOffset",
                    "kind": "composite",
                    "label": "Offset",
                    "available": True,
                    "children": [
                        {
                            "command_id": child_id,
                            "kind": "command",
                            "label": child_id,
                            "available": True,
                            "parent_command_id": "Part_CompOffset",
                        }
                        for child_id in EXPECTED_MODEL_COMPOSITES["Part_CompOffset"]
                    ],
                }
            ],
        },
    )
    groups.insert(
        2,
        {
            "label": "Modify",
            "actions": [
                {
                    "command_id": "Part_CompJoinFeatures",
                    "kind": "composite",
                    "label": "Join",
                    "available": True,
                    "children": [
                        {
                            "command_id": child_id,
                            "kind": "command",
                            "label": child_id,
                            "available": True,
                            "parent_command_id": "Part_CompJoinFeatures",
                        }
                        for child_id in EXPECTED_MODEL_COMPOSITES[
                            "Part_CompJoinFeatures"
                        ]
                    ],
                }
            ],
        },
    )
    return manifest


def test_model_composites_map_to_all_and_only_their_exact_leaf_variants() -> None:
    surface = _surface(_model_composite_manifest())
    plans = classify_native_surface(surface)
    actual = {
        action.command_id: tuple(child.command_id for child in action.children)
        for group in surface.groups
        for action in group.actions
        if action.kind == "composite"
    }

    assert actual == EXPECTED_MODEL_COMPOSITES
    by_id = {plan.command_id: plan for plan in plans}
    for parent_id, child_ids in EXPECTED_MODEL_COMPOSITES.items():
        assert by_id[parent_id].classification.parent_only is True
        assert by_id[parent_id].operation_variant is None
        assert tuple(by_id[child_id].parent_command_id for child_id in child_ids) == (
            parent_id,
        ) * len(child_ids)
    assert {
        child_id: (
            by_id[child_id].capability_family,
            by_id[child_id].operation_variant,
        )
        for child_ids in EXPECTED_MODEL_COMPOSITES.values()
        for child_id in child_ids
    } == {
        **{
            child_id: ("model.feature", "primitive")
            for child_id in EXPECTED_MODEL_COMPOSITES["PartDesign_DesignPrimitive"]
        },
        "Part_Offset": ("model.part", "offset_3d"),
        "Part_Offset2D": ("model.part", "offset_2d"),
        "Part_JoinConnect": ("model.join", "connect"),
        "Part_JoinEmbed": ("model.join", "embed"),
        "Part_JoinCutout": ("model.join", "cutout"),
    }


def test_model_composite_rejects_missing_or_reordered_leaf_variants() -> None:
    missing = _model_composite_manifest()
    missing["groups"][0]["actions"][0]["children"].pop()
    with pytest.raises(NativeActionManifestError, match="exposes children"):
        classify_native_surface(_surface(missing))

    reordered = _model_composite_manifest()
    offset_children = reordered["groups"][1]["actions"][0]["children"]
    offset_children.reverse()
    with pytest.raises(NativeActionManifestError, match="exposes children"):
        classify_native_surface(_surface(reordered))


def test_model_composite_leaf_cannot_be_advertised_without_its_exact_parent() -> None:
    manifest = _model_composite_manifest()
    offset = manifest["groups"][1]["actions"][0]
    leaf = offset["children"].pop(0)
    leaf.pop("parent_command_id")
    manifest["groups"][1]["actions"].append(leaf)

    with pytest.raises(NativeActionManifestError, match="exposes children"):
        classify_native_surface(_surface(manifest))


def test_classifier_preserves_live_order_and_records_every_contract_field() -> None:
    surface = _surface()

    plans = classify_native_surface(surface)

    assert tuple(plan.command_id for plan in plans) == surface.command_ids
    assert plans[0].command_id == "PartDesign_DesignPrimitive"
    assert plans[0].classification.parent_only is True
    assert plans[0].implementation_status == "parent_only"
    assert plans[0].operation_variant is None
    assert plans[0].transaction_behavior == "none"
    assert plans[1].parent_command_id == "PartDesign_DesignPrimitive"
    assert plans[1].classification.mutation is True
    assert plans[1].capability_family == "model.feature"
    assert plans[1].operation_variant == "primitive"
    assert plans[1].implementation_status == "planned"
    assert plans[-1].classification.read is True
    assert plans[-1].capability_family == "inspect.query"
    assert plans[-1].transaction_behavior == "none"

    expected_summary_fields = {
        "command_id",
        "surface_id",
        "group",
        "parent_command_id",
        "classification",
        "capability_family",
        "operation_variant",
        "prerequisites",
        "exact_target_type",
        "transaction_behavior",
        "postcondition_checker",
        "background_required",
        "implementation_status",
    }
    assert all(set(plan.summary()) == expected_summary_fields for plan in plans)
    assert all(
        sum(
            (
                plan.classification.read,
                plan.classification.mutation,
                plan.classification.view,
                plan.classification.export,
                plan.classification.parent_only,
                plan.classification.human_only,
            )
        )
        == 1
        for plan in plans
    )


def test_provider_families_exclude_parent_only_actions_and_preserve_order() -> None:
    plans = classify_native_surface(_surface())

    assert planned_provider_capability_families(plans) == (
        "model.feature",
        "inspect.query",
    )


def test_hole_is_a_focused_model_capability_instead_of_a_generic_feature() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Solids",
                "actions": [
                    {
                        "command_id": "PartDesign_Hole",
                        "kind": "command",
                        "label": "Hole",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.hole"
    assert plan.operation_variant == "hole"


def test_profile_actions_share_one_compact_typed_provider_variant() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Solids",
                "actions": [
                    {
                        "command_id": "PartDesign_DesignExtrude",
                        "kind": "command",
                        "label": "Extrude",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.feature"
    assert plan.operation_variant == "profile"


def test_standalone_part_primitives_use_a_focused_part_capability() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Solids",
                "actions": [
                    {
                        "command_id": "Part_Primitives",
                        "kind": "command",
                        "label": "Create primitives",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.part"
    assert plan.operation_variant == "primitive"


def test_part_builder_uses_the_same_focused_part_capability() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Solids",
                "actions": [
                    {
                        "command_id": "Part_Builder",
                        "kind": "command",
                        "label": "Shape Builder",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.part"
    assert plan.operation_variant == "builder"


def test_part_extrude_uses_the_focused_part_capability() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Solids",
                "actions": [
                    {
                        "command_id": "Part_Extrude",
                        "kind": "command",
                        "label": "Extrude",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.part"
    assert plan.operation_variant == "extrude"


def test_part_revolve_uses_the_focused_part_capability() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Solids",
                "actions": [
                    {
                        "command_id": "Part_Revolve",
                        "kind": "command",
                        "label": "Revolve",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.part"
    assert plan.operation_variant == "revolve"


def test_part_mirror_uses_the_focused_part_capability() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Transform",
                "actions": [
                    {
                        "command_id": "Part_Mirror",
                        "kind": "command",
                        "label": "Mirror",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.part"
    assert plan.operation_variant == "mirror"


def test_make_face_uses_the_focused_part_capability() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Surfaces",
                "actions": [
                    {
                        "command_id": "Part_MakeFace",
                        "kind": "command",
                        "label": "Face From Wires",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.part"
    assert plan.operation_variant == "make_face"


def test_ruled_surface_uses_the_focused_part_capability() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Surfaces",
                "actions": [
                    {
                        "command_id": "Part_RuledSurface",
                        "kind": "command",
                        "label": "Ruled Surface",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.part"
    assert plan.operation_variant == "ruled_surface"


@pytest.mark.parametrize(
    ("command_id", "label", "operation"),
    (
        ("Part_Offset", "3D Offset", "offset_3d"),
        ("Part_Offset2D", "2D Offset", "offset_2d"),
        ("Part_ProjectionOnSurface", "Projection on Surface", "project_surface"),
        ("Part_Compound", "Compound", "compound"),
        ("Part_CompoundFilter", "Compound Filter", "compound_filter"),
    ),
)
def test_retained_part_tools_use_the_focused_part_capability(
    command_id,
    label,
    operation,
) -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Part",
                "actions": [
                    {
                        "command_id": command_id,
                        "kind": "command",
                        "label": label,
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.part"
    assert plan.operation_variant == operation


def test_design_separate_uses_the_structure_capability() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Part",
                "actions": [
                    {
                        "command_id": "PartDesign_Separate",
                        "kind": "command",
                        "label": "Separate Solids",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.structure"
    assert plan.operation_variant == "separate"


def test_design_combine_uses_the_boolean_capability() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Part",
                "actions": [
                    {
                        "command_id": "PartDesign_Combine",
                        "kind": "command",
                        "label": "Combine",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.boolean"
    assert plan.operation_variant == "combine"


def test_design_split_uses_the_boolean_capability_without_legacy_slice_aliases() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Part",
                "actions": [
                    {
                        "command_id": "PartDesign_Split",
                        "kind": "command",
                        "label": "Split",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.boolean"
    assert plan.operation_variant == "split"


@pytest.mark.parametrize(
    ("command_id", "label", "operation"),
    (
        ("Part_JoinConnect", "Connect Shapes", "connect"),
        ("Part_JoinEmbed", "Embed Shapes", "embed"),
        ("Part_JoinCutout", "Cutout Shape", "cutout"),
    ),
)
def test_part_join_leaves_use_the_focused_join_capability(
    command_id,
    label,
    operation,
) -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Part",
                "actions": [
                    {
                        "command_id": command_id,
                        "kind": "command",
                        "label": label,
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.join"
    assert plan.operation_variant == operation


@pytest.mark.parametrize(
    ("command_id", "label"),
    (
        ("PartDesign_DesignMirror", "Mirror"),
        ("PartDesign_DesignLinearPattern", "Linear Pattern"),
        ("PartDesign_DesignCircularPattern", "Circular Pattern"),
    ),
)
def test_design_patterns_use_the_compact_typed_pattern_variant(command_id, label) -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Transform",
                "actions": [
                    {
                        "command_id": command_id,
                        "kind": "command",
                        "label": label,
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.transform"
    assert plan.operation_variant == "pattern"


def test_design_scale_uses_the_focused_transform_capability() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Transform",
                "actions": [
                    {
                        "command_id": "PartDesign_Scale",
                        "kind": "command",
                        "label": "Scale",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.transform"
    assert plan.operation_variant == "scale"


@pytest.mark.parametrize(
    ("command_id", "label", "operation"),
    (
        ("PartDesign_Fillet", "Fillet", "fillet"),
        ("PartDesign_Chamfer", "Chamfer", "chamfer"),
        ("PartDesign_Draft", "Draft", "draft"),
        ("PartDesign_Thickness", "Thickness", "thickness"),
    ),
)
def test_dressups_use_the_focused_model_dressup_capability(
    command_id,
    label,
    operation,
) -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Finish",
                "actions": [
                    {
                        "command_id": command_id,
                        "kind": "command",
                        "label": label,
                        "available": True,
                    }
                ],
            }
        ],
    }
    plan = classify_native_surface(
        RibbonSurface.from_manifest(manifest, revision=1)
    )[0]

    assert plan.capability_family == "model.dressup"
    assert plan.operation_variant == operation
    assert plan.exact_target_type is None
    assert plan.transaction_behavior == "document"


def test_leave_sketch_is_an_explicit_provider_edit_control() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "sketch.edit",
        "groups": [
            {
                "label": "Finish",
                "actions": [
                    {
                        "command_id": "Sketcher_LeaveSketch",
                        "kind": "command",
                        "label": "Close",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(_surface(manifest))[0]

    assert plan.classification.human_only is False
    assert plan.classification.interactive is False
    assert plan.classification.mutation is True
    assert plan.capability_family == "sketch.control"
    assert plan.operation_variant == "leave"
    assert plan.transaction_behavior == "edit_control"
    assert plan.implementation_status == "planned"
    assert planned_provider_capability_families((plan,)) == ("sketch.control",)


def test_cancel_sketch_remains_human_only() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "sketch.edit",
        "groups": [
            {
                "label": "Finish",
                "actions": [
                    {
                        "command_id": "Sketcher_CancelSketch",
                        "kind": "command",
                        "label": "Cancel",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(_surface(manifest))[0]

    assert plan.classification.human_only is True
    assert plan.classification.interactive is True
    assert plan.classification.mutation is False
    assert plan.operation_variant is None
    assert plan.transaction_behavior == "human"
    assert plan.implementation_status == "human_only"
    assert planned_provider_capability_families((plan,)) == ()


def test_unknown_live_action_fails_closed() -> None:
    manifest = _manifest()
    manifest["groups"][1]["actions"][0]["command_id"] = "Part_UnknownMutation"

    with pytest.raises(NativeActionManifestError, match="unclassified actions"):
        classify_native_surface(_surface(manifest))


def test_composite_leaf_role_drift_fails_closed() -> None:
    manifest = _manifest()
    primitive = manifest["groups"][0]["actions"][0]
    primitive["kind"] = "command"
    primitive.pop("children")

    with pytest.raises(NativeActionManifestError, match="composite/leaf role"):
        classify_native_surface(_surface(manifest))


def test_classifier_exposes_no_activation_or_command_dispatch_api() -> None:
    import VibeCADNativeActionManifest as module

    public_names = {name for name in vars(module) if not name.startswith("_")}
    forbidden_fragments = ("activate", "switch", "dispatch", "run_command")
    assert not any(
        fragment in name.lower()
        for name in public_names
        for fragment in forbidden_fragments
    )
