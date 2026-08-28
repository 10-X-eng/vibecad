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
    _plan,
)
from VibeCADRibbonSurface import RibbonAction, RibbonSurface

from vibecad_tests.test_ribbon_surface import _manifest


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


def test_default_inventory_counts_are_derived_and_actions_are_unique() -> None:
    derived_counts = {
        surface_id: len(command_ids)
        for surface_id, command_ids in KNOWN_ACTIONS_BY_SURFACE.items()
        if surface_id != "unavailable"
    }
    assert DEFAULT_SURFACE_ACTION_COUNTS == derived_counts
    assert all(
        len(command_ids) == len(set(command_ids))
        for command_ids in KNOWN_ACTIONS_BY_SURFACE.values()
    )
    unique_ids = set().union(
        *(set(command_ids) for command_ids in KNOWN_ACTIONS_BY_SURFACE.values())
    )
    assert len(unique_ids) == DEFAULT_UNIQUE_ACTION_COUNT
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


def test_expensive_manufacture_workflows_require_background_execution() -> None:
    expected = {
        "CAM_Profile": ("Operations", "background"),
        "CAM_Pocket_Shape": ("Operations", "background"),
        "CAM_Pocket3D": ("Operations", "background"),
        "CAM_Surface": ("Operations", "background"),
        "CAM_Waterline": ("Operations", "background"),
        "CAM_RotarySurface": ("Operations", "background"),
        "CAM_MillFacing": ("Operations", "background"),
        "CAM_Helix": ("Operations", "background"),
        "CAM_Adaptive": ("Operations", "background"),
        "CAM_Slot": ("Operations", "background"),
        "CAM_Drilling": ("Operations", "background"),
        "CAM_ThreadMilling": ("Operations", "background"),
        "CAM_Engrave": ("Operations", "background"),
        "CAM_Deburr": ("Operations", "background"),
        "CAM_Vcarve": ("Operations", "background"),
        "CAM_Camotics": ("Operations", "presentation"),
        "CAM_SimulatorGL": ("Program", "presentation"),
        "CAM_Simulator": ("Program", "background"),
        "CAM_DressupZCorrect": ("Modify", "background"),
        "CAM_Post": ("Program", "background_output"),
        "CAM_PostSelected": ("Program", "background_output"),
    }

    plans = {
        command_id: _plan(
            "manufacture",
            group_label,
            RibbonAction(
                command_id=command_id,
                label=command_id,
                available=True,
                kind="command",
            ),
        )
        for command_id, (group_label, _transaction_behavior) in expected.items()
    }

    assert set(plans) == set(expected)
    assert all(plan.background_required for plan in plans.values())
    assert {
        command_id: plan.transaction_behavior for command_id, plan in plans.items()
    } == {
        command_id: transaction_behavior
        for command_id, (_group_label, transaction_behavior) in expected.items()
    }


def test_common_cam_operations_resolve_to_focused_provider_tools() -> None:
    expected = {
        "CAM_MillFacing": ("manufacture.face", "mill_facing"),
        "CAM_Pocket_Shape": ("manufacture.pocket", "pocket_shape"),
        "CAM_Profile": ("manufacture.profile", "profile"),
        "CAM_Drilling": ("manufacture.drill", "drilling"),
        "CAM_Pocket3D": ("manufacture.pocket_3d", "pocket_3d"),
        "CAM_Surface": ("manufacture.surface", "surface"),
        "CAM_Waterline": ("manufacture.waterline", "waterline"),
        "CAM_RotarySurface": ("manufacture.rotary_surface", "rotary_surface"),
        "CAM_Helix": ("manufacture.helix", "helix"),
        "CAM_Adaptive": ("manufacture.adaptive", "adaptive"),
        "CAM_Slot": ("manufacture.slot", "slot"),
        "CAM_ThreadMilling": ("manufacture.thread_mill", "thread_milling"),
        "CAM_Engrave": ("manufacture.engrave", "engrave"),
        "CAM_Deburr": ("manufacture.deburr", "deburr"),
        "CAM_Vcarve": ("manufacture.v_carve", "v_carve"),
        "CAM_Array": ("manufacture.array", "array"),
        "CAM_SimpleCopy": ("manufacture.copy_path", "simple_copy"),
    }

    plans = {
        command_id: _plan(
            "manufacture",
            "Operations",
            RibbonAction(
                command_id=command_id,
                label=command_id,
                available=True,
                kind="command",
            ),
        )
        for command_id in expected
    }

    assert {
        command_id: (plan.capability_family, plan.operation_variant)
        for command_id, plan in plans.items()
    } == expected


def test_cam_tool_dock_resolves_to_focused_add_tool() -> None:
    plan = _plan(
        "manufacture",
        "Tools",
        RibbonAction(
            command_id="CAM_ToolBitDock",
            label="ToolBit Library",
            available=True,
            kind="command",
        ),
    )
    assert (plan.capability_family, plan.operation_variant) == (
        "manufacture.add_tool",
        "create_controller",
    )


def test_cam_inspection_commands_resolve_to_focused_tools() -> None:
    expected = {
        "CAM_Sanity": ("manufacture.validate", "validate_job"),
        "CAM_Inspect": ("manufacture.toolpath", "inspect_toolpath"),
        "CAM_SelectLoop": ("manufacture.loop", "detect_loop"),
    }
    plans = {
        command_id: _plan(
            "manufacture",
            "Operations",
            RibbonAction(
                command_id=command_id,
                label=command_id,
                available=True,
                kind="command",
            ),
        )
        for command_id in expected
    }
    assert {
        command_id: (plan.capability_family, plan.operation_variant)
        for command_id, plan in plans.items()
    } == expected


def test_cam_operation_edit_commands_resolve_by_intent() -> None:
    expected = {
        "CAM_OpActiveToggle": ("manufacture.operations", "set_active"),
        "CAM_OperationCopy": ("manufacture.operations", "copy_operations"),
        "CAM_DressupArray": ("manufacture.dressup", "array_dressup"),
        "CAM_DressupZCorrect": ("manufacture.dressup", "z_correct_dressup"),
    }
    plans = {
        command_id: _plan(
            "manufacture",
            "Modify",
            RibbonAction(
                command_id=command_id,
                label=command_id,
                available=True,
                kind="command",
            ),
        )
        for command_id in expected
    }
    assert {
        command_id: (plan.capability_family, plan.operation_variant)
        for command_id, plan in plans.items()
    } == expected


def test_cam_post_commands_resolve_by_output_scope() -> None:
    expected = {
        "CAM_Post": ("manufacture.post_job", "complete_job"),
        "CAM_PostSelected": (
            "manufacture.post_selected",
            "selected_operations",
        ),
    }
    plans = {
        command_id: _plan(
            "manufacture",
            "Program",
            RibbonAction(
                command_id=command_id,
                label=command_id,
                available=True,
                kind="command",
            ),
        )
        for command_id in expected
    }
    assert {
        command_id: (plan.capability_family, plan.operation_variant)
        for command_id, plan in plans.items()
    } == expected


def test_drawing_page_actions_resolve_to_four_exact_variants() -> None:
    expected = {
        "TechDraw_PageDefault": (
            "page_default",
            "NewDrawingPageWithConfiguredTemplate",
        ),
        "TechDraw_PageTemplate": (
            "page_template",
            "HumanAuthorizedSvgTemplateForNewDrawingPage",
        ),
        "TechDraw_FillTemplateFields": (
            "fill_template_fields",
            "ExactDrawingPageAndEditableTemplateFields",
        ),
        "TechDraw_RedrawPage": (
            "redraw_page",
            "ExactDrawingPageAndActiveViewGraph",
        ),
    }
    plans = {
        command_id: _plan(
            "drawing",
            "Page",
            RibbonAction(
                command_id=command_id,
                label=command_id,
                available=True,
                kind="command",
            ),
        )
        for command_id in expected
    }

    assert {
        command_id: plan.capability_family for command_id, plan in plans.items()
    } == {
        "TechDraw_PageDefault": "drawing.create_page",
        "TechDraw_PageTemplate": "drawing.choose_page_template",
        "TechDraw_FillTemplateFields": "drawing.template_fields",
        "TechDraw_RedrawPage": "drawing.redraw_page",
    }
    assert {
        command_id: (plan.transaction_behavior, plan.background_required)
        for command_id, plan in plans.items()
    } == {
        "TechDraw_PageDefault": ("document", False),
        "TechDraw_PageTemplate": ("document", False),
        "TechDraw_FillTemplateFields": ("document", False),
        "TechDraw_RedrawPage": ("background", True),
    }
    assert {
        command_id: (plan.operation_variant, plan.exact_target_type)
        for command_id, plan in plans.items()
    } == expected


def test_drawing_broken_view_resolves_to_exact_background_variant() -> None:
    plan = _plan(
        "drawing",
        "Views",
        RibbonAction(
            command_id="TechDraw_BrokenView",
            label="Broken View",
            available=True,
            kind="command",
        ),
    )
    assert (
        plan.capability_family,
        plan.operation_variant,
        plan.exact_target_type,
        plan.transaction_behavior,
        plan.background_required,
    ) == (
        "drawing.broken_view",
        "create_broken_view",
        "ExactDrawingPageSourcesBreakDefinitionsAndProjectionSettings",
        "background",
        True,
    )


def test_drawing_projection_group_resolves_to_exact_background_variant() -> None:
    plan = _plan(
        "drawing",
        "Views",
        RibbonAction(
            command_id="TechDraw_ProjectionGroup",
            label="Projection Group",
            available=True,
            kind="command",
        ),
    )
    assert (
        plan.capability_family,
        plan.operation_variant,
        plan.exact_target_type,
        plan.transaction_behavior,
        plan.background_required,
    ) == (
        "drawing.projection_group",
        "create_projection_group",
        "ExactDrawingPageSourcesProjectionSetAndConvention",
        "background",
        True,
    )


def test_drawing_active_view_resolves_to_exact_immediate_capture() -> None:
    plan = _plan(
        "drawing",
        "Views",
        RibbonAction(
            command_id="TechDraw_ActiveView",
            label="Active View",
            available=True,
            kind="command",
        ),
    )
    assert (
        plan.capability_family,
        plan.operation_variant,
        plan.exact_target_type,
        plan.transaction_behavior,
        plan.background_required,
    ) == (
        "drawing.active_view",
        "create_active_view",
        "ExactDrawingPageActive3DViewportAndCaptureSettings",
        "document",
        False,
    )


def test_model_inventory_excludes_every_contextual_sketch_edit_action() -> None:
    shared_view = {
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "VibeCAD_SectionView",
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


def test_analyze_examples_remains_human_only_instructional_ui() -> None:
    plan = _plan(
        "analyze",
        "Utilities",
        RibbonAction(
            command_id="FEM_Examples",
            label="Examples",
            available=True,
            kind="command",
        ),
    )

    assert plan.command_id == "FEM_Examples"
    assert plan.classification.human_only is True
    assert plan.classification.interactive is True
    assert plan.operation_variant is None
    assert plan.transaction_behavior == "human"
    assert plan.implementation_status == "human_only"
    assert planned_provider_capability_families((plan,)) == ()


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
            child_id: (
                "model.primitive",
                child_id.removeprefix("PartDesign::Design").lower(),
            )
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
    assert plans[1].capability_family == "model.primitive"
    assert plans[1].operation_variant == "box"
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
        "model.primitive",
        "inspect.query",
    )


def test_assemble_create_and_insert_actions_use_focused_contracts() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "assemble",
        "groups": [
            {
                "label": "Assembly",
                "actions": [
                    {
                        "command_id": "Assembly_CreateAssembly",
                        "kind": "command",
                        "label": "Create Assembly",
                        "available": True,
                    },
                    {
                        "command_id": "Assembly_InsertLink",
                        "kind": "command",
                        "label": "Insert Component",
                        "available": True,
                    },
                    {
                        "command_id": "Assembly_InsertNewPart",
                        "kind": "command",
                        "label": "Insert New Part",
                        "available": True,
                    },
                    {
                        "command_id": "Assembly_ToggleGrounded",
                        "kind": "command",
                        "label": "Toggle Grounded",
                        "available": True,
                    },
                    {
                        "command_id": "Assembly_SolveAssembly",
                        "kind": "command",
                        "label": "Solve Assembly",
                        "available": True,
                    },
                    {
                        "command_id": "Assembly_CreateView",
                        "kind": "command",
                        "label": "Exploded View",
                        "available": True,
                    },
                    {
                        "command_id": "Assembly_CreateSimulation",
                        "kind": "command",
                        "label": "Simulation",
                        "available": True,
                    },
                ],
            }
        ],
    }

    plans = classify_native_surface(_surface(manifest))

    assert tuple(plan.capability_family for plan in plans) == (
        "assembly.create",
        "assembly.insert",
        "assembly.new_part",
        "assembly.ground",
        "assembly.solve",
        "assembly.exploded_view",
        "assembly.motion_study",
    )
    assert tuple(plan.operation_variant for plan in plans) == (
        "create_assembly",
        "insert_component",
        "create_part",
        "set_grounded",
        "solve_assembly",
        "create_view",
        "create_simulation",
    )


def test_fastener_actions_use_the_active_ribbon_contract() -> None:
    actions = [
        {
            "command_id": "VibeCAD_InsertStandardFastener",
            "kind": "command",
            "label": "Insert Standard Fastener",
            "available": True,
        },
        {
            "command_id": "VibeCAD_EditStandardFastener",
            "kind": "command",
            "label": "Edit Standard Fastener",
            "available": True,
        },
    ]
    model = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [{"label": "Fasteners", "actions": actions}],
    }
    manifest = {
        "schema_version": 1,
        "surface_id": "assemble",
        "groups": [
            {
                "label": "Fasteners",
                "actions": actions,
            }
        ],
    }

    model_plans = classify_native_surface(_surface(model))
    plans = classify_native_surface(_surface(manifest))

    assert tuple(plan.capability_family for plan in model_plans) == (
        "model.fastener",
        "model.fastener",
    )
    assert tuple(plan.capability_family for plan in plans) == (
        "assembly.fastener",
        "assembly.fastener_edit",
    )
    assert tuple(plan.operation_variant for plan in plans) == (
        "insert_standard_fastener",
        "edit_standard_fastener",
    )


def test_aero_vlm_action_uses_the_exact_solver_operation() -> None:
    plan = _plan(
        "aero",
        "Aero",
        RibbonAction(
            command_id="VibeCADAero_VLM",
            label="VLM",
            available=True,
            kind="command",
        ),
    )

    assert plan.capability_family == "aero.solve"
    assert plan.operation_variant == "vlm"


def test_robot_configuration_preserves_document_and_session_boundaries() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "assemble",
        "groups": [
            {
                "label": "Robot",
                "actions": [
                    {
                        "command_id": command_id,
                        "kind": "command",
                        "label": command_id,
                        "available": True,
                    }
                    for command_id in (
                        "Robot_Create",
                        "Robot_AddToolShape",
                        "Robot_SetDefaultOrientation",
                        "Robot_SetDefaultValues",
                    )
                ],
            }
        ],
    }

    plans = classify_native_surface(_surface(manifest))

    assert {plan.capability_family for plan in plans} == {"robot.setup"}
    assert tuple(plan.operation_variant for plan in plans) == (
        "create",
        "add_tool_shape",
        "set_default_orientation",
        "set_default_values",
    )
    assert tuple(plan.transaction_behavior for plan in plans) == (
        "document",
        "document",
        "session",
        "session",
    )
    assert all(plan.classification.mutation for plan in plans)


def test_robot_motion_preserves_document_and_preview_session_boundaries() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "assemble",
        "groups": [
            {
                "label": "Motion",
                "actions": [
                    {
                        "command_id": command_id,
                        "kind": "command",
                        "label": command_id,
                        "available": True,
                    }
                    for command_id in (
                        "Robot_SetHomePos",
                        "Robot_RestoreHomePos",
                        "Robot_Simulate",
                    )
                ],
            }
        ],
    }

    plans = classify_native_surface(_surface(manifest))

    assert {plan.capability_family for plan in plans} == {"robot.motion"}
    assert tuple(plan.operation_variant for plan in plans) == (
        "set_home_pos",
        "restore_home_pos",
        "simulate",
    )
    assert tuple(plan.transaction_behavior for plan in plans) == (
        "document",
        "document",
        "session",
    )
    assert all(plan.background_required is False for plan in plans)
    assert all(plan.classification.mutation for plan in plans)


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


def test_extrude_has_one_exact_provider_capability() -> None:
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

    assert plan.capability_family == "model.extrude"
    assert plan.operation_variant == "create"


def test_revolve_has_one_exact_provider_capability() -> None:
    manifest = {
        "schema_version": 1,
        "surface_id": "model",
        "groups": [
            {
                "label": "Solids",
                "actions": [
                    {
                        "command_id": "PartDesign_DesignRevolve",
                        "kind": "command",
                        "label": "Revolve",
                        "available": True,
                    }
                ],
            }
        ],
    }

    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

    assert plan.capability_family == "model.revolve"
    assert plan.operation_variant == "create"


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


def test_design_split_uses_the_boolean_capability_without_legacy_slice_aliases() -> (
    None
):
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
def test_design_patterns_use_the_compact_typed_pattern_variant(
    command_id, label
) -> None:
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
    plan = classify_native_surface(RibbonSurface.from_manifest(manifest, revision=1))[0]

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
