# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exhaustive semantic-timeline inventory for retained direct write tools."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest

from tool_impl import sketcher as sketcher_tools
from tool_impl.service import TOOL_MODULE_NAMES
from tool_impl.service import partdesign_transform_feature


ALLOWED_CONTRACTS = {
    "operation",
    "resource_graph",
    "replacement",
    "in_place_edit",
    "no_persistent_mutation",
    "structural_container",
}

# Each retained SAFE_WRITE service has an explicit durable-history contract.
# Multiple contracts mean the schema exposes materially different execution
# modes; they do not permit the implementation to infer a mode from document
# deltas.
EXPECTATIONS = {
    "assembly.create_assembly": (
        {"operation"},
        "Assembly is the step; its JointGroup is internal structure.",
    ),
    "assembly.create_joint": (
        {"operation"},
        "The new native Joint is the step; solved placements are its effect.",
    ),
    "assembly.ground_component": (
        {"operation"},
        "The new GroundedJoint is the step.",
    ),
    "assembly.insert_component": (
        {"resource_graph"},
        "The occurrence is the step and exact synchronized children are resources.",
    ),
    "assembly.solve": (
        {"in_place_edit"},
        "The solver updates existing occurrence placements without a new step.",
    ),
    "cam.add_operation": (
        {"operation"},
        "The retained native CAM operation is one step, including failed output.",
    ),
    "cam.add_tool": (
        {"resource_graph", "operation"},
        "Success extends the Job resource graph; a retained partial tool is its own step.",
    ),
    "cam.create_job": (
        {"resource_graph", "replacement"},
        "Job owns its generated graph and replaces only exact initially visible models.",
    ),
    "component.publish_interface": (
        {"in_place_edit"},
        "Interface publication annotates one existing component-owned coordinate system.",
    ),
    "draft.create_array": (
        {"operation"},
        "The linked array is a source-preserving step.",
    ),
    "draft.create_bspline": (
        {"operation"},
        "The B-spline is one step.",
    ),
    "draft.create_circle": (
        {"operation"},
        "The circle or arc is one step.",
    ),
    "draft.create_rectangle": (
        {"operation"},
        "The rectangle is one step.",
    ),
    "draft.create_text": (
        {"operation"},
        "The text annotation is one step.",
    ),
    "draft.create_wire": (
        {"operation"},
        "The wire is one step.",
    ),
    "fem.add_constraint": (
        {"operation"},
        "The native FEM constraint is one step.",
    ),
    "fem.add_material": (
        {"operation"},
        "The native FEM material object is one step.",
    ),
    "fem.create_analysis": (
        {"resource_graph"},
        "Analysis is the step and its default solver is a resource.",
    ),
    "fem.mesh_analysis": (
        {"operation", "in_place_edit"},
        "Start creates the mesh step; status, cancel, and finalize edit that mesh.",
    ),
    "fem.solve": (
        {"resource_graph", "in_place_edit"},
        "Lifecycle edits the solver; one result root owns exact imported auxiliaries.",
    ),
    "material.apply_material": (
        {"in_place_edit"},
        "Material assignment edits the exact existing shaped object.",
    ),
    "material.set_appearance": (
        {"in_place_edit"},
        "Appearance assignment edits the exact existing view provider.",
    ),
    "mesh.repair": (
        {"replacement"},
        "The parametric repair step replaces only the exact visible source mesh.",
    ),
    "meshpart.mesh_from_shape": (
        {"operation"},
        "The converted mesh is a source-preserving step.",
    ),
    "meshpart.shape_from_mesh": (
        {"operation"},
        "The converted Part shape is a source-preserving step.",
    ),
    "model.extrude": (
        {"operation", "replacement"},
        "Body mode creates a feature; standalone mode replaces its visible profile.",
    ),
    "model.loft": (
        {"operation", "replacement"},
        "Body mode creates a feature; standalone mode replaces its visible sections.",
    ),
    "model.sweep": (
        {"operation", "replacement"},
        "Body mode creates a feature; standalone mode replaces its visible inputs.",
    ),
    "model.helix": (
        {"operation"},
        "The additive or subtractive helix is one Body-history step.",
    ),
    "model.mirror": (
        {"operation"},
        "Body and standalone mirror modes both preserve their sources.",
    ),
    "model.fillet": (
        {"operation"},
        "The Body-native fillet is one Body-history step.",
    ),
    "model.chamfer": (
        {"operation"},
        "The Body-native chamfer is one Body-history step.",
    ),
    "model.thickness": (
        {"operation"},
        "The Body-native thickness feature is one Body-history step.",
    ),
    "model.boolean": (
        {"replacement"},
        "The standalone boolean replaces only operands visible before the call.",
    ),
    "model.revolve": (
        {"operation", "replacement"},
        "Body mode creates a feature; standalone mode replaces its visible profile.",
    ),
    "part.boolean": (
        {"replacement"},
        "The boolean step replaces only operands visible before the call.",
    ),
    "part.chamfer": (
        {"replacement"},
        "The chamfer step replaces only its visible source.",
    ),
    "part.extrude": (
        {"replacement"},
        "The extrusion step replaces only its visible profile.",
    ),
    "part.fillet": (
        {"replacement"},
        "The fillet step replaces only its visible source.",
    ),
    "part.mirror": (
        {"operation"},
        "The mirrored result preserves its source.",
    ),
    "part.revolve": (
        {"replacement"},
        "The revolution step replaces only its visible profile.",
    ),
    "partdesign.additive_helix": (
        {"operation"},
        "The additive helix feature is one Body-history step.",
    ),
    "partdesign.additive_loft": (
        {"operation"},
        "The additive loft feature is one Body-history step.",
    ),
    "partdesign.additive_pipe": (
        {"operation"},
        "The additive pipe feature is one Body-history step.",
    ),
    "partdesign.boolean": (
        {"operation"},
        "The Part Design boolean feature is one Body-history step.",
    ),
    "partdesign.chamfer": (
        {"operation"},
        "The Part Design chamfer feature is one Body-history step.",
    ),
    "partdesign.create_body": (
        {"structural_container"},
        "Body and Origin are Browser/render structure; owned features are the steps.",
    ),
    "partdesign.create_datum_axis": (
        {"operation"},
        "The datum axis is one Body-history step.",
    ),
    "partdesign.create_datum_plane": (
        {"operation"},
        "The datum plane is one Body-history step.",
    ),
    "partdesign.create_datum_point": (
        {"operation"},
        "The datum point is one Body-history step.",
    ),
    "partdesign.create_shape_binder": (
        {"operation"},
        "The shape binder is one Body-history step.",
    ),
    "partdesign.create_sketch": (
        {"operation"},
        "The sketch is one Body-history step.",
    ),
    "partdesign.create_subshape_binder": (
        {"operation"},
        "The sub-shape binder is one Body-history step.",
    ),
    "partdesign.draft": (
        {"operation"},
        "The draft feature is one Body-history step.",
    ),
    "partdesign.edit_sketch": (
        {"no_persistent_mutation"},
        "Entering native sketch edit mode changes GUI state, not document history.",
    ),
    "partdesign.fillet": (
        {"operation"},
        "The Part Design fillet feature is one Body-history step.",
    ),
    "partdesign.groove": (
        {"operation"},
        "The groove feature is one Body-history step.",
    ),
    "partdesign.hole": (
        {"operation"},
        "The hole feature is one Body-history step.",
    ),
    "partdesign.linear_pattern": (
        {"operation"},
        "The linear pattern feature is one Body-history step.",
    ),
    "partdesign.mirror": (
        {"operation"},
        "The mirrored feature is one Body-history step.",
    ),
    "partdesign.multi_transform": (
        {"resource_graph"},
        "MultiTransform is the step and its exact transform children are resources.",
    ),
    "partdesign.pad": (
        {"operation"},
        "The pad feature is one Body-history step.",
    ),
    "partdesign.pocket": (
        {"operation"},
        "The pocket feature is one Body-history step.",
    ),
    "partdesign.polar_pattern": (
        {"operation"},
        "The polar pattern feature is one Body-history step.",
    ),
    "partdesign.revolution": (
        {"operation"},
        "The revolution feature is one Body-history step.",
    ),
    "partdesign.set_tip": (
        {"in_place_edit"},
        "The command moves the existing timeline marker and Body Tip.",
    ),
    "partdesign.subtractive_helix": (
        {"operation"},
        "The subtractive helix feature is one Body-history step.",
    ),
    "partdesign.subtractive_loft": (
        {"operation"},
        "The subtractive loft feature is one Body-history step.",
    ),
    "partdesign.subtractive_pipe": (
        {"operation"},
        "The subtractive pipe feature is one Body-history step.",
    ),
    "partdesign.thickness": (
        {"operation"},
        "The thickness feature is one Body-history step.",
    ),
    "partdesign.thin_loft": (
        {"operation"},
        "The scripted thin-loft feature is one Body-history step.",
    ),
    "spreadsheet.create_sheet": (
        {"operation"},
        "The spreadsheet is one step.",
    ),
    "spreadsheet.set_cells": (
        {"in_place_edit"},
        "The ordered cell batch edits one existing spreadsheet.",
    ),
    "surface.blend": (
        {"operation"},
        "The blend surface is a source-preserving step.",
    ),
    "surface.extend": (
        {"operation"},
        "The extended surface is a source-preserving step.",
    ),
    "surface.fill": (
        {"operation"},
        "The filled surface is a source-preserving step.",
    ),
    "surface.loft": (
        {"operation"},
        "The lofted surface is a source-preserving step.",
    ),
    "surface.thicken": (
        {"replacement"},
        "The thickened solid replaces only its exact visible source surface.",
    ),
    "techdraw.add_annotation": (
        {"operation"},
        "The annotation is one drawing step.",
    ),
    "techdraw.add_dimension": (
        {"operation"},
        "The dimension is one drawing step.",
    ),
    "techdraw.add_view": (
        {"operation"},
        "The drawing view is one step.",
    ),
    "techdraw.create_page": (
        {"resource_graph"},
        "The page is the step and its template is a resource.",
    ),
}


# Sketch editing changes the already-published Sketch operation.  Geometry,
# constraints, construction state, and external references are not separate
# document-history operations; native document undo still records each edit.
SKETCHER_EXPECTATIONS = {
    "sketcher.close_sketch": "no_persistent_mutation",
    "sketcher.draw_rectangle": "in_place_edit",
    "sketcher.add_polyline": "in_place_edit",
    "sketcher.add_arc": "in_place_edit",
    "sketcher.add_circle": "in_place_edit",
    "sketcher.add_ellipse": "in_place_edit",
    "sketcher.add_spline": "in_place_edit",
    "sketcher.add_hole_pattern": "in_place_edit",
    "sketcher.add_slot": "in_place_edit",
    "sketcher.constrain": "in_place_edit",
    "sketcher.edit_constraint": "in_place_edit",
    "sketcher.move_point": "in_place_edit",
    "sketcher.translate_geometry": "in_place_edit",
    "sketcher.modify_geometry": "in_place_edit",
    "sketcher.add_external_geometry": "in_place_edit",
    "sketcher.remove_external_geometry": "in_place_edit",
    "sketcher.delete_items": "in_place_edit",
    "sketcher.set_construction": "in_place_edit",
}


def _safe_write_inventory() -> dict[str, Path]:
    result = {}
    for module_name in TOOL_MODULE_NAMES:
        module = import_module(f"tool_impl.service.{module_name}")
        if module.TOOL_SPEC.get("safety") != "SAFE_WRITE":
            continue
        result[str(module.TOOL_SPEC["name"])] = Path(module.__file__).resolve()
    return result


def _safe_write_sketcher_inventory() -> dict[str, Path]:
    result = {}
    for module_name in sketcher_tools.TOOL_MODULE_NAMES:
        module = import_module(f"tool_impl.sketcher.{module_name}")
        if module.TOOL_SPEC.get("safety") != "SAFE_WRITE":
            continue
        result[str(module.TOOL_SPEC["name"])] = Path(module.__file__).resolve()
    return result


def test_every_retained_safe_write_service_has_one_exact_history_contract() -> None:
    inventory = _safe_write_inventory()

    assert len(inventory) == 82
    assert set(EXPECTATIONS) == set(inventory)
    for contracts, semantics in EXPECTATIONS.values():
        assert contracts
        assert contracts <= ALLOWED_CONTRACTS
        assert semantics.strip()


def test_every_sketcher_write_tool_edits_the_one_existing_sketch_operation() -> None:
    inventory = _safe_write_sketcher_inventory()

    assert len(inventory) == 18
    assert set(SKETCHER_EXPECTATIONS) == set(inventory)
    assert set(SKETCHER_EXPECTATIONS.values()) == {
        "in_place_edit",
        "no_persistent_mutation",
    }

    # A Sketcher edit tool may mutate the active SketchObject, but it must not
    # manufacture or retire document objects behind the timeline's back.
    implementation_paths = set(inventory.values())
    implementation_paths.update(
        Path(sketcher_tools.__file__).resolve().parent.glob("*.py")
    )
    for path in implementation_paths:
        source = path.read_text(encoding="utf-8")
        assert ".addObject(" not in source
        assert ".newObject(" not in source
        assert ".removeObject(" not in source


def test_group_roots_and_multi_object_factories_publish_explicit_identities() -> None:
    sources = {
        path.stem: path.read_text(encoding="utf-8")
        for path in _safe_write_inventory().values()
    }

    assert "finalize_new_timeline_operation(body)" not in sources[
        "partdesign_create_body"
    ]
    assert "NewTimelineOperation()" in sources[
        "assembly_create_assembly"
    ]
    assert "timeline.set_operation(assembly)" in sources[
        "assembly_create_assembly"
    ]
    assert "NewTimelineOperation()" in sources["fem_create_analysis"]
    assert "timeline.add_resource(solver)" in sources["fem_create_analysis"]
    assert "NewTimelineOperation()" in sources["techdraw_create_page"]
    assert "timeline.add_resource(template)" in sources[
        "techdraw_create_page"
    ]
    assert "finalizeInsertedComponentTimeline(component)" in sources[
        "assembly_insert_component"
    ]
    assert "createDefaultStock=True" in sources["cam_create_job"]
    assert "stageTimelineDirectResourceReplacement(" in sources["cam_create_job"]
    assert "finalizeTimelineDirectResourceReplacement(" in sources[
        "cam_create_job"
    ]
    assert "captureTimelineObjects(" not in sources["cam_create_job"]
    assert "finalizeProvisionalTimelineOperation(" not in sources[
        "cam_create_job"
    ]
    assert "initially_visible_models" in sources["cam_create_job"]
    assert "markTimelineReplacedInputs(" in sources["cam_create_job"]
    assert "stageTimelineResourceGraphExtension(" in sources["cam_add_tool"]
    assert "finalizeTimelineResourceGraphExtension(" in sources[
        "cam_add_tool"
    ]
    assert "publishProvisionalToolBit(" in sources["cam_add_tool"]
    assert "captureTimelineObjects(" not in sources["cam_add_tool"]
    assert "finalizeProvisionalTimelineOperation(" not in sources[
        "cam_add_tool"
    ]


def test_source_preserving_array_and_exact_replacements_are_declared() -> None:
    sources = {
        path.stem: path.read_text(encoding="utf-8")
        for path in _safe_write_inventory().values()
    }

    assert "timeline.accept_derived_output(obj, [base])" in sources[
        "draft_create_array"
    ]
    for module_name in (
        "part_boolean",
        "part_extrude",
        "part_fillet",
        "part_revolve",
    ):
        assert "replaced_inputs=" in sources[module_name]
    assert "mark_modeling_replaced_inputs(" in sources["surface_thicken"]
    assert "NewTimelineOperation()" in sources["mesh_repair"]
    assert "timeline.set_operation(operation)" in sources["mesh_repair"]
    assert "mark_modeling_replaced_inputs(" in sources["mesh_repair"]
    assert "VibeCADTimelineRole" not in sources["mesh_repair"]
    assert "VibeCADTimelineReplacedInputs" not in sources["mesh_repair"]


def test_direct_write_tools_never_infer_history_from_document_snapshots() -> None:
    for path in _safe_write_inventory().values():
        source = path.read_text(encoding="utf-8")
        assert "captureTimelineObjects(" not in source
        assert "finalizeProvisionalTimelineOperation(" not in source


def test_noncreating_services_do_not_add_document_objects() -> None:
    inventory = _safe_write_inventory()
    noncreating = {
        name
        for name, (contracts, _semantics) in EXPECTATIONS.items()
        if contracts <= {"in_place_edit", "no_persistent_mutation"}
    }

    for name in noncreating:
        source = inventory[name].read_text(encoding="utf-8")
        assert ".addObject(" not in source
        assert ".newObject(" not in source


class _ExactDocument:
    def __init__(self) -> None:
        self.objects = {}

    def getObject(self, name):
        return self.objects.get(name)


class _ExactObject:
    def __init__(self, document, name, *, owner=False) -> None:
        self.Document = document
        self.Name = name
        if owner:
            self.Transformations = []
        document.objects[name] = self


class _ExactBody:
    def __init__(self, document) -> None:
        self.Document = document

    def newObject(self, _type_id, name):
        return _ExactObject(self.Document, name)


def test_multi_transform_child_is_owned_immediately_and_exactly() -> None:
    document = _ExactDocument()
    owner = _ExactObject(document, "MultiTransform", owner=True)
    first = _ExactObject(document, "LinearPatternTransform")
    second = _ExactObject(document, "MirrorTransform")

    partdesign_transform_feature._adopt_multi_transform_child(owner, first)
    partdesign_transform_feature._adopt_multi_transform_child(owner, second)

    assert owner.Transformations == [first, second]
    with pytest.raises(ValueError, match="adopted twice"):
        partdesign_transform_feature._adopt_multi_transform_child(owner, second)

    other_document = _ExactDocument()
    foreign = _ExactObject(other_document, "ForeignTransform")
    with pytest.raises(ValueError, match="exact live"):
        partdesign_transform_feature._adopt_multi_transform_child(owner, foreign)


def test_failed_multi_transform_configuration_keeps_child_in_owner_block() -> None:
    document = _ExactDocument()
    body = _ExactBody(document)
    owner = _ExactObject(document, "MultiTransform", owner=True)

    with pytest.raises(RuntimeError, match="reference no longer exists"):
        partdesign_transform_feature._create_transform_child(
            body,
            document,
            {
                "type": "linear",
                "reference": {
                    "object_name": "MissingAxis",
                    "subelement": "",
                },
                "distribution": {},
                "reversed": False,
            },
            timeline_owner=owner,
        )

    assert len(owner.Transformations) == 1
    assert owner.Transformations[0].Name == "LinearPatternTransform"
