# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sheet ribbon actions retain explicit mutation and presentation contracts."""

from VibeCADNativeActionManifest import classify_native_surface
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADRibbonSurface import RibbonSurface


GROUPS = {
    "View": ("Std_ViewFitAll", "Std_ViewIsometric", "VibeCAD_ToggleGrid", "VibeCAD_SectionView"),
    "Inspect": ("Std_Measure", "Std_MassProperties", "Inspection_InspectElement",
                "Part_CheckGeometry", "Inspection_VisualInspection"),
    "Create": ("SheetMetal_CreateBaseShape", "SheetMetal_CreateFromSketch", "SheetMetal_CreateFromSolid", "SheetMetal_CreateEditable"),
    "Bend/Form": ("SheetMetal_CreateFlange", "SheetMetal_CreateFold", "SheetMetal_EditParameters"),
    "Cut/Relief": ("SheetMetal_EditCuts",),
    "Materials": ("SheetMetal_EditMaterial",),
    "Folded/Flat": ("SheetMetal_ViewFolded", "SheetMetal_ViewFlat"),
    "RMFG": ("SheetMetal_RMFGConnection", "SheetMetal_RMFGManufacture"),
}


def surface():
    return RibbonSurface.from_manifest({"schema_version": 1, "surface_id": "sheet_metal", "groups": [
        {"label": label, "actions": [
            {"command_id": name, "label": name, "kind": "command", "available": True}
            for name in names]} for label, names in GROUPS.items()]}, revision=1)


def test_sheet_actions_are_explicit_and_create_is_not_hidden_as_human_only():
    plans = {plan.command_id: plan for plan in classify_native_surface(surface())}
    for name in GROUPS["Create"]:
        assert plans[name].capability_family == "sheet_metal.create"
        assert plans[name].classification.mutation
        assert not plans[name].classification.human_only
        assert plans[name].background_required
    for name, operation in (("SheetMetal_EditParameters", "set_parameters"),
                            ("SheetMetal_EditCuts", "add_circle"),
                            ("SheetMetal_EditMaterial", "set_material")):
        plan = plans[name]
        assert plan.capability_family == "sheet_metal.edit"
        assert plan.operation_variant == operation
        assert plan.exact_target_type == "ExactSharedSheetState"
        assert plan.transaction_behavior == "background"
    for name in GROUPS["Folded/Flat"]:
        plan = plans[name]
        assert plan.capability_family == "sheet_metal.view"
        assert plan.operation_variant == "set_representation"
        assert plan.classification.view and not plan.classification.mutation
        assert plan.transaction_behavior == "presentation"


def test_sheet_inventory_resolves_all_create_edit_and_presentation_operations():
    resolved = resolve_native_provider_surface(surface(), build_native_capability_registry())
    assert resolved.available
    assert resolved.missing_definition_names == ()
    assert resolved.missing_implementation_names == ()
    assert resolved.incomplete_definition_names == ()
    assert not resolved.human_only_action_ids


def test_new_flange_is_a_native_background_creation_with_exact_source_edges():
    from jsonschema import Draft202012Validator

    plans = {plan.command_id: plan for plan in classify_native_surface(surface())}
    plan = plans["SheetMetal_CreateFlange"]
    assert plan.capability_family == "sheet_metal.create"
    assert plan.operation_variant == "add_flange"
    assert plan.background_required
    assert not plan.classification.human_only
    definition = build_native_capability_registry().definition("sheet_metal.create")
    validator = Draft202012Validator(definition.provider_schema(("add_flange",))["parameters"])
    arguments = {"object_name": "SheetCut", "subelements": ["Edge12"], "length": 20,
                 "bend_radius": 2, "bend_angle": 90, "invert": False,
                 "bend_type": "Material Outside", "length_spec": "Leg"}
    assert not list(validator.iter_errors(arguments))
    for changes in ({"subelements": []}, {"subelements": ["Vertex1"]},
                    {"bend_angle": 0}, {"bend_angle": 181}, {"length": 0}):
        assert list(validator.iter_errors({**arguments, **changes}))


def test_creation_variants_keep_legacy_action_aliases():
    definition = build_native_capability_registry().definition("sheet_metal.create")
    for operation, legacy, current in (
        ("base_shape", "SheetMetal_BaseShape", "SheetMetal_CreateBaseShape"),
        ("base_from_sketch", "SheetMetal_AddBase", "SheetMetal_CreateFromSketch"),
        ("from_solid", "SheetMetal_FromSolid", "SheetMetal_CreateFromSolid"),
    ):
        variant = next(item for item in definition.variants if item.operation == operation)
        assert {legacy, current}.issubset(variant.action_ids)


def test_internal_fold_is_native_with_one_skin_and_an_editable_bend_sketch():
    from jsonschema import Draft202012Validator

    plans = {plan.command_id: plan for plan in classify_native_surface(surface())}
    plan = plans["SheetMetal_CreateFold"]
    assert plan.capability_family == "sheet_metal.create"
    assert plan.operation_variant == "fold_from_sketch"
    assert plan.background_required
    assert not plan.classification.human_only
    definition = build_native_capability_registry().definition("sheet_metal.create")
    validator = Draft202012Validator(definition.provider_schema(("fold_from_sketch",))["parameters"])
    arguments = {"object_name": "SheetCut", "subelements": ["Face1"], "sketch_name": "BendLine",
                 "bend_radius": 2, "bend_angle": 90, "invert": False, "invert_bend": False,
                 "k_factor": .42, "position": "middle"}
    assert not list(validator.iter_errors(arguments))
    for changes in ({"subelements": []}, {"subelements": ["Edge1"]},
                    {"subelements": ["Face1", "Face2"]}, {"sketch_name": ""},
                    {"k_factor": -1}, {"k_factor": 1.1}, {"bend_angle": 0}):
        assert list(validator.iter_errors({**arguments, **changes}))


def test_rmfg_connection_is_native_and_does_not_authorize_payment():
    plans = {plan.command_id: plan for plan in classify_native_surface(surface())}
    plan = plans["SheetMetal_RMFGConnection"]
    assert plan.capability_family == "sheet_metal.connection"
    assert plan.classification.read and not plan.classification.mutation
    assert not plan.classification.human_only
    assert plan.operation_variant == "show_connection"
    definition = build_native_capability_registry().definition("sheet_metal.connection")
    assert {variant.operation for variant in definition.variants} == {"status", "show_connection"}
    assert all(variant.transaction_behavior == "none" for variant in definition.variants)


def test_manufacturing_action_is_native_output_for_an_exact_sheet():
    plans = {plan.command_id: plan for plan in classify_native_surface(surface())}
    plan = plans["SheetMetal_RMFGManufacture"]
    assert plan.capability_family == "sheet_metal.manufacturing"
    assert plan.classification.export and not plan.classification.mutation
    assert not plan.classification.human_only
    assert plan.operation_variant == "show_panel"
    assert plan.exact_target_type == "ExactSharedSheetState"
    assert plan.transaction_behavior == "output"
