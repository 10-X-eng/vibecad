# SPDX-License-Identifier: LGPL-2.1-or-later

"""Internal prototype smoke test for gated schema-v2 domain adapters.

Passing this test does not make a workbench production-ready. It exercises
shared lifecycle plumbing for in-progress adapters that remain unavailable to
providers until their real native APIs and dedicated integration suites pass
the production-readiness gate.
"""

from __future__ import annotations

from pathlib import Path
import sys

MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    accept_candidate,
    capture_reference_inputs,
    complete_inspection,
    execute_candidate,
    finalize_candidate,
    finish_delete,
    prepare_candidate,
    prepare_delete,
    retain_candidate,
    validate_candidate,
)
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    delete_live_program,
    publish_candidate,
)
from VibeCADVibeScriptDomains import (  # noqa: E402
    PROP_PROGRAM_ID,
    get_vibescript_pack,
)
from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402


class _WorkbenchService:
    def __init__(self, workbench: str) -> None:
        self.workbench = workbench

    def _active_document(self):
        import FreeCAD as App

        return App.ActiveDocument

    def active_workbench_name(self) -> str:
        return self.workbench

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "fixture-revision"

    @staticmethod
    def _partdesign_body_for_feature(_obj):
        return None


def _assert_program_inspection(captured: dict, prepared: dict) -> None:
    inspection = complete_inspection(
        {
            **captured,
            "program_id": prepared["program_id"],
            "live_programs": [],
        }
    )
    assert inspection.get("ok") is True, inspection
    assert inspection["program"]["program_id"] == prepared["program_id"]
    assert inspection["program"]["accepted_revision"] == prepared["revision"]


def _exercise_draft_lifecycle(root: Path, captured: dict) -> None:
    import FreeCAD as App

    document = App.newDocument("VibeScriptDraftFixture")
    pack = get_vibescript_pack("DraftWorkbench")
    assert pack is not None
    source = (
        "wire = api.wire([[0,0,0],[4,0,0],[4,3,0]], closed=True, "
        "make_face=True, label='Draft Wire')\n"
        "circle = api.circle(2, placement={'position':[8,2,0],"
        "'rotation':[0,0,0,1]}, make_face=True, label='Draft Circle')\n"
        "rectangle = api.rectangle(3, 2, placement={'position':[12,0,0],"
        "'rotation':[0,0,0,1]}, make_face=True, label='Draft Rectangle')\n"
        "spline = api.bspline([[0,6,0],[2,8,0],[4,6,0]], "
        "parameterization=0.5, label='Draft Spline')\n"
        "ortho = api.array(rectangle, kind='orthogonal', interval_x=[5,0,0], "
        "interval_y=[0,4,0], count_x=2, count_y=2, use_link=True, "
        "label='Draft Link Array')\n"
        "polar = api.array(circle, kind='polar', count=3, "
        "total_angle_degrees=180, center=[8,2,0], use_link=False, "
        "label='Draft Shape Array')\n"
        "note = api.text(['Worker validated'], placement={'position':[0,10,0],"
        "'rotation':[0,0,0,1]}, height=2, line_spacing=1.25, label='Draft Note')\n"
        "result = {'Wire':wire, 'Circle':circle, 'Rectangle':rectangle, "
        "'Spline':spline, 'Ortho':ortho, 'Polar':polar, 'Note':note}\n"
    )
    draft_captured = {
        **captured,
        "pack": pack,
        "operation": "create_program",
        "tool_name": "vibescript.draft.create_program",
        "arguments": {
            "program_name": "Worker Draft",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "inputs": {},
            "expected_outputs": [
                {"name": "Wire", "type": "wire"},
                {"name": "Circle", "type": "circle"},
                {"name": "Rectangle", "type": "rectangle"},
                {"name": "Spline", "type": "bspline"},
                {"name": "Ortho", "type": "array"},
                {"name": "Polar", "type": "array"},
                {"name": "Note", "type": "text"},
            ],
        },
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_objects": [],
        "surface": resolve_modeling_surface("DraftWorkbench", "vibescript").summary(),
    }
    prepared = prepare_candidate(draft_captured)
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    service = _WorkbenchService("DraftWorkbench")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    _assert_program_inspection(draft_captured, prepared)
    live_names = {
        name: details["object_name"]
        for name, details in accepted["live_outputs"].items()
    }
    expected_types = {
        "Wire": "Part::FeaturePython",
        "Circle": "Part::Part2DObjectPython",
        "Rectangle": "Part::Part2DObjectPython",
        "Spline": "Part::FeaturePython",
        "Ortho": "Part::FeaturePython",
        "Polar": "Part::FeaturePython",
        "Note": "App::FeaturePython",
    }
    for output_name, expected_type in expected_types.items():
        obj = document.getObject(live_names[output_name])
        assert obj.TypeId == expected_type
        assert obj.Proxy is not None
    for output_name in ("Wire", "Circle", "Rectangle", "Spline", "Ortho", "Polar"):
        assert not document.getObject(live_names[output_name]).Shape.isNull()
    assert document.getObject(live_names["Ortho"]).Base is document.getObject(
        live_names["Rectangle"]
    )
    assert document.getObject(live_names["Ortho"]).Proxy.use_link is True
    assert document.getObject(live_names["Polar"]).Base is document.getObject(
        live_names["Circle"]
    )
    assert document.getObject(live_names["Polar"]).Proxy.use_link is False

    update_captured = {
        **draft_captured,
        "operation": "edit_source",
        "tool_name": "vibescript.draft.edit_source",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": prepared["revision"],
            "source": source.replace("Draft Wire", "Updated Draft Wire"),
        },
    }
    update_prepared = prepare_candidate(update_captured)
    update_execution = execute_candidate(update_prepared, cancellation_check=None)
    assert update_execution.get("ok") is True, update_execution
    update_validated = validate_candidate(update_prepared, update_execution)
    retain_candidate(update_prepared, status="validated")
    updated = accept_candidate(
        update_prepared,
        publish_candidate(service, update_prepared, update_validated),
    )
    assert {
        name: details["object_name"]
        for name, details in updated["live_outputs"].items()
    } == live_names

    path = root / "draft-vibescript.FCStd"
    document.saveAs(str(path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(path))
    assert reopened is not None
    for output_name, object_name in live_names.items():
        obj = reopened.getObject(object_name)
        assert obj is not None, output_name
        assert obj.TypeId == expected_types[output_name]
        assert obj.Proxy is not None

    delete_captured = {
        **update_captured,
        "operation": "delete_program",
        "tool_name": "vibescript.draft.delete_program",
        "arguments": {
            "program_id": update_prepared["program_id"],
            "expected_revision": update_prepared["revision"],
            "reason": "Draft integration lifecycle complete",
        },
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
    }
    prepared_delete = prepare_delete(delete_captured)
    deletion = delete_live_program(service, prepared_delete)
    assert finish_delete(prepared_delete, deletion)["ok"] is True
    assert not any(
        str(getattr(obj, PROP_PROGRAM_ID, "")) == update_prepared["program_id"]
        for obj in reopened.Objects
    )
    App.closeDocument(reopened.Name)


def _exercise_sketcher_lifecycle(root: Path, captured: dict) -> None:
    import FreeCAD as App

    document = App.newDocument("VibeScriptSketcherFixture")
    pack = get_vibescript_pack("SketcherWorkbench")
    assert pack is not None
    source = (
        "bottom = api.line([0,0], [10,0], name='Bottom')\n"
        "right = api.line([10,0], [10,6], name='Right')\n"
        "top = api.line([10,6], [0,6], name='Top')\n"
        "left = api.line([0,6], [0,0], name='Left')\n"
        "geometry = [bottom, right, top, left]\n"
        "constraints = [\n"
        "  api.constraint('horizontal', [bottom]),\n"
        "  api.constraint('vertical', [right]),\n"
        "  api.constraint('horizontal', [top]),\n"
        "  api.constraint('vertical', [left]),\n"
        "]\n"
        "result = {'Profile': api.sketch(geometry, constraints, "
        "label='Worker Sketch')}\n"
    )
    sketch_captured = {
        **captured,
        "pack": pack,
        "operation": "create_program",
        "tool_name": "vibescript.sketcher.create_program",
        "arguments": {
            "program_name": "Worker Sketch",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "inputs": {},
            "expected_outputs": [{"name": "Profile", "type": "sketch"}],
        },
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_objects": [],
        "surface": resolve_modeling_surface(
            "SketcherWorkbench", "vibescript"
        ).summary(),
    }
    prepared = prepare_candidate(sketch_captured)
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    assert execution["sketch_validation"]["geometry_count"] == 4
    assert execution["sketch_validation"]["conflicting_constraints"] == []
    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    service = _WorkbenchService("SketcherWorkbench")
    accepted = accept_candidate(
        prepared,
        publish_candidate(service, prepared, validated),
    )
    _assert_program_inspection(sketch_captured, prepared)
    object_name = accepted["live_outputs"]["Profile"]["object_name"]
    sketch = document.getObject(object_name)
    assert sketch.TypeId == "Sketcher::SketchObject"
    assert sketch.GeometryCount == 4
    assert sketch.ConstraintCount == 4
    assert "VibeCADSketchValidation" in sketch.PropertiesList

    path = root / "sketcher-vibescript.FCStd"
    document.saveAs(str(path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(path))
    assert reopened is not None
    reopened_sketch = reopened.getObject(object_name)
    assert reopened_sketch is not None
    assert reopened_sketch.GeometryCount == 4
    delete_captured = {
        **sketch_captured,
        "operation": "delete_program",
        "tool_name": "vibescript.sketcher.delete_program",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": prepared["revision"],
            "reason": "Sketcher integration lifecycle complete",
        },
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
    }
    prepared_delete = prepare_delete(delete_captured)
    deletion = delete_live_program(service, prepared_delete)
    assert finish_delete(prepared_delete, deletion)["ok"] is True
    assert reopened.getObject(object_name) is None
    App.closeDocument(reopened.Name)


def _exercise_spreadsheet_lifecycle(root: Path, captured: dict) -> None:
    import FreeCAD as App

    document = App.newDocument("VibeScriptSpreadsheetFixture")
    pack = get_vibescript_pack("SpreadsheetWorkbench")
    assert pack is not None
    source = (
        "cells = [\n"
        "  api.cell('A1', 10, unit='mm', alias='length', style='bold'),\n"
        "  api.cell('B1', expression='length * 2', alias='double_length'),\n"
        "]\n"
        "result = {'Parameters': api.sheet(cells=cells, label='Worker Sheet')}\n"
    )
    sheet_captured = {
        **captured,
        "pack": pack,
        "operation": "create_program",
        "tool_name": "vibescript.spreadsheet.create_program",
        "arguments": {
            "program_name": "Worker Spreadsheet",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "inputs": {},
            "expected_outputs": [{"name": "Parameters", "type": "sheet"}],
        },
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_objects": [],
        "surface": resolve_modeling_surface(
            "SpreadsheetWorkbench", "vibescript"
        ).summary(),
    }
    prepared = prepare_candidate(sheet_captured)
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    validated = validate_candidate(prepared, execution)
    assert validated["outputs"][0]["sheet_validation"]["cell_count"] == 2
    retain_candidate(prepared, status="validated")
    service = _WorkbenchService("SpreadsheetWorkbench")
    accepted = accept_candidate(
        prepared,
        publish_candidate(service, prepared, validated),
    )
    _assert_program_inspection(sheet_captured, prepared)
    object_name = accepted["live_outputs"]["Parameters"]["object_name"]
    sheet = document.getObject(object_name)
    assert sheet.TypeId == "Spreadsheet::Sheet"
    assert sheet.getAlias("A1") == "length"
    assert sheet.getAlias("B1") == "double_length"

    replacement = (
        "cells = [\n"
        "  api.cell('A1', 12, unit='mm', alias='length'),\n"
        "]\n"
        "result = {'Parameters': api.sheet(cells=cells, label='Worker Sheet')}\n"
    )
    update_captured = {
        **sheet_captured,
        "operation": "reconfigure_program",
        "tool_name": "vibescript.spreadsheet.reconfigure_program",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": prepared["revision"],
            "source": replacement,
            "input_schema": sheet_captured["arguments"]["input_schema"],
            "inputs": {},
            "expected_outputs": [{"name": "Parameters", "type": "sheet"}],
        },
    }
    update_prepared = prepare_candidate(update_captured)
    update_execution = execute_candidate(update_prepared, cancellation_check=None)
    assert update_execution.get("ok") is True, update_execution
    update_validated = validate_candidate(update_prepared, update_execution)
    retain_candidate(update_prepared, status="validated")
    updated = accept_candidate(
        update_prepared,
        publish_candidate(service, update_prepared, update_validated),
    )
    assert updated["live_outputs"]["Parameters"]["object_name"] == object_name
    assert sheet.getAlias("A1") == "length"
    assert sheet.getContents("B1") == ""

    path = root / "spreadsheet-vibescript.FCStd"
    document.saveAs(str(path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(path))
    assert reopened is not None
    reopened_sheet = reopened.getObject(object_name)
    assert reopened_sheet is not None
    assert reopened_sheet.getAlias("A1") == "length"
    delete_captured = {
        **update_captured,
        "operation": "delete_program",
        "tool_name": "vibescript.spreadsheet.delete_program",
        "arguments": {
            "program_id": update_prepared["program_id"],
            "expected_revision": update_prepared["revision"],
            "reason": "Spreadsheet integration lifecycle complete",
        },
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
    }
    prepared_delete = prepare_delete(delete_captured)
    deletion = delete_live_program(service, prepared_delete)
    assert finish_delete(prepared_delete, deletion)["ok"] is True
    assert reopened.getObject(object_name) is None
    App.closeDocument(reopened.Name)


def _exercise_remaining_domain_matrix(root: Path, captured: dict) -> None:
    import FreeCAD as App
    import Part
    import Points

    cases = (
        (
            "PartWorkbench",
            "solid",
            "result = {'Result': api.box(3, 4, 5, label='Initial Label')}\n",
        ),
        (
            "SurfaceWorkbench",
            "fill",
            "a = api.line([0,0,0], [4,0,0])\n"
            "b = api.line([4,0,0], [4,3,0])\n"
            "c = api.line([4,3,0], [0,3,0])\n"
            "d = api.line([0,3,0], [0,0,0])\n"
            "result = {'Result': api.fill([api.boundary(x) for x in (a,b,c,d)], "
            "label='Initial Label')}\n",
        ),
        (
            "MeshWorkbench",
            "mesh",
            "result = {'Result': api.mesh(triangles=[[[0,0,0],[1,0,0],[0,1,0]]], "
            "label='Initial Label')}\n",
        ),
        (
            "MeshPartWorkbench",
            "mesh",
            "result = {'Result': api.mesh_from_shape("
            "inputs['source'], label='Initial Label')}\n",
        ),
        (
            "PointsWorkbench",
            "points",
            "result = {'Result': api.point_cloud([[0,0,0],[1,2,3]], "
            "label='Initial Label')}\n",
        ),
        (
            "ReverseEngineeringWorkbench",
            "mesh",
            "result = {'Result': api.reconstruct("
            "[[0,0,0],[1,0,0],[0,1,0],[1,1,0]], "
            "parameters={'grid_size':[2,2]}, label='Initial Label')}\n",
        ),
        (
            "InspectionWorkbench",
            "inspection_feature",
            "result = {'Result': api.comparison(inputs['actual'], "
            "[inputs['nominal']], search_radius=1.0, tolerance=0.1, "
            "label='Initial Label')}\n",
        ),
        (
            "RobotWorkbench",
            "robot",
            "result = {'Result': api.robot(label='Initial Label')}\n",
        ),
    )
    # These domains require complete multi-output native graphs. Their dedicated
    # production gates exercise those exact graphs, including rollback and
    # save/reopen; a fabricated zero-argument single-output smoke is invalid.
    for dedicated_workbench in (
        "FemWorkbench",
        "CAMWorkbench",
        "TechDrawWorkbench",
    ):
        dedicated_pack = get_vibescript_pack(dedicated_workbench)
        assert dedicated_pack is not None and dedicated_pack.production_ready
    for index, (workbench, output_type, template_source) in enumerate(cases):
        pack = get_vibescript_pack(workbench)
        assert pack is not None
        if not pack.production_ready:
            unavailable = resolve_modeling_surface(workbench, "vibescript")
            assert unavailable.available is False
            assert unavailable.cad_tool_names == ()
            assert "production-readiness gate" in unavailable.unavailable_reason
            continue
        document = App.newDocument(f"VibeScriptDomainMatrix{index}")
        document.setUndoMode(1)
        source = template_source
        input_schema = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
        inputs = {}
        document_objects = []
        if workbench == "MeshPartWorkbench":
            seed = document.addObject("Part::Feature", "MeshPartMatrixSource")
            seed.Shape = Part.makeBox(3, 4, 5)
            reference = {
                "document_uid": str(document.Uid),
                "object_name": str(seed.Name),
            }
            input_schema = {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "object",
                        "x-vibecad-reference": True,
                        "properties": {
                            "document_uid": {"type": "string", "minLength": 1},
                            "object_name": {"type": "string", "minLength": 1},
                        },
                        "required": ["document_uid", "object_name"],
                        "additionalProperties": False,
                    }
                },
                "required": ["source"],
                "additionalProperties": False,
            }
            inputs = {"source": reference}
            document_objects = [
                {"name": seed.Name, "label": seed.Label, "type_id": seed.TypeId}
            ]
        elif workbench == "InspectionWorkbench":
            nominal = document.addObject("Part::Feature", "InspectionMatrixNominal")
            nominal.Shape = Part.makePlane(2, 2)
            actual = document.addObject("Points::Feature", "InspectionMatrixActual")
            actual.Points = Points.Points(
                [App.Vector(0.5, 0.5, 0.05), App.Vector(1.5, 1.5, 0.05)]
            )
            reference_schema = {
                "type": "object",
                "x-vibecad-reference": True,
                "properties": {
                    "document_uid": {"type": "string", "minLength": 1},
                    "object_name": {"type": "string", "minLength": 1},
                },
                "required": ["document_uid", "object_name"],
                "additionalProperties": False,
            }
            input_schema = {
                "type": "object",
                "properties": {
                    "actual": reference_schema,
                    "nominal": reference_schema,
                },
                "required": ["actual", "nominal"],
                "additionalProperties": False,
            }
            inputs = {
                "actual": {
                    "document_uid": str(document.Uid),
                    "object_name": str(actual.Name),
                },
                "nominal": {
                    "document_uid": str(document.Uid),
                    "object_name": str(nominal.Name),
                },
            }
            document_objects = [
                {"name": obj.Name, "label": obj.Label, "type_id": obj.TypeId}
                for obj in (actual, nominal)
            ]
        # Input fixtures are the accepted baseline. VibeScript publication must
        # be one later native transaction so undo/redo cannot consume a source.
        document.commitTransaction()
        operation_captured = {
            **captured,
            "pack": pack,
            "operation": "create_program",
            "tool_name": f"vibescript.{pack.domain}.create_program",
            "arguments": {
                "program_name": f"Matrix {pack.title}",
                "source": source,
                "input_schema": input_schema,
                "inputs": inputs,
                "expected_outputs": [{"name": "Result", "type": output_type}],
            },
            "document_name": str(document.Name),
            "document_uid": str(document.Uid),
            "document_objects": document_objects,
            "surface": resolve_modeling_surface(workbench, "vibescript").summary(),
        }
        service = _WorkbenchService(workbench)
        prepared = prepare_candidate(operation_captured)
        if prepared.get("reference_requirements"):
            prepared = finalize_candidate(
                prepared,
                capture_reference_inputs(service, prepared),
            )
        execution = execute_candidate(prepared, cancellation_check=None)
        assert execution.get("ok") is True, (workbench, execution)
        validated = validate_candidate(prepared, execution)
        retain_candidate(prepared, status="validated")
        accepted = accept_candidate(
            prepared,
            publish_candidate(service, prepared, validated),
        )
        _assert_program_inspection(operation_captured, prepared)
        object_name = accepted["live_outputs"]["Result"]["object_name"]
        obj = document.getObject(object_name)
        assert obj is not None
        assert str(getattr(obj, "VibeCADVibeScriptOutputType", "")) == output_type
        assert document.undo()
        assert document.getObject(object_name) is None
        assert document.redo()
        obj = document.getObject(object_name)
        assert obj is not None
        assert str(getattr(obj, "VibeCADVibeScriptOutputType", "")) == output_type

        update_captured = {
            **operation_captured,
            "operation": "edit_source",
            "tool_name": f"vibescript.{pack.domain}.edit_source",
            "arguments": {
                "program_id": prepared["program_id"],
                "expected_revision": prepared["revision"],
                "source": source.replace("Initial Label", "Updated Label"),
            },
        }
        update_prepared = prepare_candidate(update_captured)
        if update_prepared.get("reference_requirements"):
            update_prepared = finalize_candidate(
                update_prepared,
                capture_reference_inputs(service, update_prepared),
            )
        update_execution = execute_candidate(update_prepared, cancellation_check=None)
        assert update_execution.get("ok") is True, (workbench, update_execution)
        update_validated = validate_candidate(update_prepared, update_execution)
        retain_candidate(update_prepared, status="validated")
        updated = accept_candidate(
            update_prepared,
            publish_candidate(service, update_prepared, update_validated),
        )
        assert updated["live_outputs"]["Result"]["object_name"] == object_name
        assert document.getObject(object_name).Label == "Updated Label"
        assert document.undo()
        assert document.getObject(object_name).Label == "Initial Label"
        assert document.redo()
        assert document.getObject(object_name).Label == "Updated Label"

        path = root / f"matrix-{pack.domain}.FCStd"
        document.saveAs(str(path))
        App.closeDocument(document.Name)
        reopened = App.openDocument(str(path))
        assert reopened is not None
        reopened_output = reopened.getObject(object_name)
        assert reopened_output is not None
        assert (
            str(getattr(reopened_output, PROP_PROGRAM_ID, ""))
            == update_prepared["program_id"]
        )
        reopened.setUndoMode(1)
        delete_captured = {
            **update_captured,
            "operation": "delete_program",
            "tool_name": f"vibescript.{pack.domain}.delete_program",
            "arguments": {
                "program_id": update_prepared["program_id"],
                "expected_revision": update_prepared["revision"],
                "reason": "Domain matrix lifecycle complete",
            },
            "document_name": str(reopened.Name),
            "document_uid": str(reopened.Uid),
        }
        prepared_delete = prepare_delete(delete_captured)
        deletion = delete_live_program(service, prepared_delete)
        assert finish_delete(prepared_delete, deletion)["ok"] is True
        assert reopened.getObject(object_name) is None
        assert reopened.undo()
        assert reopened.getObject(object_name) is not None
        assert reopened.redo()
        assert reopened.getObject(object_name) is None
        App.closeDocument(reopened.Name)

    material_pack = get_vibescript_pack("MaterialWorkbench")
    assert material_pack is not None
    import Materials

    document = App.newDocument("VibeScriptMaterialMatrix")
    document.setUndoMode(1)
    target = document.addObject("Part::Feature", "MaterialTarget")
    target.Shape = Part.makeBox(1, 1, 1)
    original_material_uuid = str(target.ShapeMaterial.UUID)
    material_card = next(
        card
        for card in Materials.MaterialManager().Materials.values()
        if str(card.UUID) != original_material_uuid
        and card.hasPhysicalProperty("Density")
    )
    reference = {"document_uid": str(document.Uid), "object_name": target.Name}
    document.commitTransaction()
    source = (
        "card = api.material(inputs['material_uuid'], "
        "require_physical_properties=['Density'])\n"
        "result = {'Result': api.assign(inputs['target'], card, label='Initial Label')}\n"
    )
    operation_captured = {
        **captured,
        "pack": material_pack,
        "operation": "create_program",
        "tool_name": "vibescript.material.create_program",
        "arguments": {
            "program_name": "Matrix Material",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {
                    "material_uuid": {"type": "string"},
                    "target": {
                        "type": "object",
                        "x-vibecad-reference": True,
                        "properties": {
                            "document_uid": {"type": "string", "minLength": 1},
                            "object_name": {"type": "string", "minLength": 1},
                        },
                        "required": ["document_uid", "object_name"],
                        "additionalProperties": False,
                    },
                },
                "required": ["material_uuid", "target"],
                "additionalProperties": False,
            },
            "inputs": {
                "material_uuid": str(material_card.UUID),
                "target": reference,
            },
            "expected_outputs": [{"name": "Result", "type": "material_assignment"}],
        },
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_objects": [
            {"name": target.Name, "label": target.Label, "type_id": target.TypeId}
        ],
        "material_targets": [
            {
                "reference": reference,
                "label": target.Label,
                "type_id": target.TypeId,
                "physical_assignment_supported": True,
                "current_material": {
                    "uuid": original_material_uuid,
                    "name": str(target.ShapeMaterial.Name),
                },
                "appearance_supported_properties": [],
                "display_modes": [],
                "display_modes_truncated": False,
                "managed_material_output": False,
            }
        ],
        "surface": resolve_modeling_surface(
            "MaterialWorkbench", "vibescript"
        ).summary(),
    }
    prepared = prepare_candidate(operation_captured)
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    service = _WorkbenchService("MaterialWorkbench")
    accepted = accept_candidate(
        prepared, publish_candidate(service, prepared, validated)
    )
    _assert_program_inspection(operation_captured, prepared)
    object_name = accepted["live_outputs"]["Result"]["object_name"]
    assert document.getObject(object_name).VibeCADTargetObject == target.Name
    assert str(target.ShapeMaterial.UUID) == str(material_card.UUID)
    assert document.undo()
    assert document.getObject(object_name) is None
    assert str(document.getObject(target.Name).ShapeMaterial.UUID) == (
        original_material_uuid
    )
    assert document.redo()
    target = document.getObject(target.Name)
    assert document.getObject(object_name) is not None
    assert str(target.ShapeMaterial.UUID) == str(material_card.UUID)
    update_captured = {
        **operation_captured,
        "operation": "edit_source",
        "tool_name": "vibescript.material.edit_source",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": prepared["revision"],
            "source": source.replace("Initial Label", "Updated Label"),
        },
    }
    update_prepared = prepare_candidate(update_captured)
    update_execution = execute_candidate(update_prepared, cancellation_check=None)
    assert update_execution.get("ok") is True, update_execution
    update_validated = validate_candidate(update_prepared, update_execution)
    retain_candidate(update_prepared, status="validated")
    updated = accept_candidate(
        update_prepared,
        publish_candidate(service, update_prepared, update_validated),
    )
    assert updated["live_outputs"]["Result"]["object_name"] == object_name
    assert document.getObject(object_name).Label == "Updated Label"
    assert document.undo()
    assert document.getObject(object_name).Label == "Initial Label"
    assert document.redo()
    assert document.getObject(object_name).Label == "Updated Label"
    path = root / "matrix-material.FCStd"
    document.saveAs(str(path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(path))
    assert reopened is not None
    assert reopened.getObject(object_name) is not None
    reopened.setUndoMode(1)
    delete_captured = {
        **update_captured,
        "operation": "delete_program",
        "tool_name": "vibescript.material.delete_program",
        "arguments": {
            "program_id": update_prepared["program_id"],
            "expected_revision": update_prepared["revision"],
            "reason": "Material matrix lifecycle complete",
        },
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
    }
    prepared_delete = prepare_delete(delete_captured)
    deletion = delete_live_program(service, prepared_delete)
    assert finish_delete(prepared_delete, deletion)["ok"] is True
    assert reopened.getObject("MaterialTarget") is not None
    assert (
        str(reopened.getObject("MaterialTarget").ShapeMaterial.UUID)
        == original_material_uuid
    )
    assert reopened.undo()
    assert reopened.getObject(object_name) is not None
    assert str(
        reopened.getObject("MaterialTarget").ShapeMaterial.UUID
    ) == str(material_card.UUID)
    assert reopened.redo()
    assert reopened.getObject(object_name) is None
    assert (
        str(reopened.getObject("MaterialTarget").ShapeMaterial.UUID)
        == original_material_uuid
    )
    App.closeDocument(reopened.Name)


def main() -> int:
    import FreeCAD as App
    from pathlib import Path
    import shutil
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="vibecad-domain-worker-"))
    try:
        pack = get_vibescript_pack("PartWorkbench")
        assert pack is not None
        captured = {
            "pack": pack,
            "operation": "create_program",
            "tool_name": "vibescript.part.create_program",
            "arguments": {
                "program_name": "Worker Box",
                "source": 'result = {"Box": api.box(inputs["length"], 2, 3)}',
                "input_schema": {
                    "type": "object",
                    "properties": {"length": {"type": "number"}},
                    "required": ["length"],
                    "additionalProperties": False,
                },
                "inputs": {"length": 4.0},
                "expected_outputs": [{"name": "Box", "type": "solid"}],
            },
            "project_root": str(root),
            "document_name": "Fixture",
            "document_uid": "fixture-document",
            "document_revision": "fixture-revision",
            "document_objects": [],
            "surface": {
                "workbench": "PartWorkbench",
                "engine": "vibescript",
                "surface_id": pack.surface_id,
            },
            "freecad_home": str(Path(App.getHomePath()).resolve()),
            "timeout_seconds": 30.0,
            "memory_limit_bytes": 1024 * 1024 * 1024,
        }
        prepared = prepare_candidate(captured)
        execution = execute_candidate(prepared, cancellation_check=None)
        assert execution.get("ok") is True, execution
        validated = validate_candidate(prepared, execution)
        assert validated.get("ok") is True
        output = validated["outputs"][0]
        assert output["name"] == "Box"
        assert output["type"] == "solid"
        assert output["facts"]["solids"] == 1
        retained = retain_candidate(prepared, status="validated")
        assert Path(retained["attempt_directory"]).is_dir()

        _exercise_draft_lifecycle(root, captured)
        _exercise_sketcher_lifecycle(root, captured)
        _exercise_spreadsheet_lifecycle(root, captured)
        _exercise_remaining_domain_matrix(root, captured)

        import Part

        live_document = App.newDocument("VibeScriptAssemblyFixture")
        live_document.setUndoMode(1)
        source_a = live_document.addObject("Part::Feature", "SourceA")
        source_a.Label = "Source A"
        source_a.Shape = Part.makeBox(10, 10, 10)
        source_b = live_document.addObject("Part::Feature", "SourceB")
        source_b.Label = "Source B"
        source_b.Shape = Part.makeBox(4, 4, 20)
        live_document.recompute()
        live_document.commitTransaction()

        assembly_pack = get_vibescript_pack("AssemblyWorkbench")
        assert assembly_pack is not None
        reference_a = {
            "document_uid": str(live_document.Uid),
            "object_name": "SourceA",
        }
        reference_b = {
            "document_uid": str(live_document.Uid),
            "object_name": "SourceB",
        }
        assembly_source = (
            "base = api.component(inputs['base'], grounded=True, label='Base')\n"
            "arm = api.component(inputs['arm'], placement=[0, 0, 0], label='Arm')\n"
            "hinge = api.joint('revolute', api.connector(base), api.connector(arm), "
            "angle_limits_degrees=[-90, 90], label='Hinge')\n"
            "model = api.assembly([base, arm], [hinge], label='Fixture Assembly')\n"
            "diagnostics = api.solve(model)\n"
            "result = {'Main': model, 'Base': base, 'Arm': arm, "
            "'Hinge': hinge, 'Diagnostics': diagnostics}"
        )
        assembly_captured = {
            **captured,
            "pack": assembly_pack,
            "operation": "create_program",
            "tool_name": "vibescript.assembly.create_program",
            "arguments": {
                "program_name": "Worker Assembly",
                "source": assembly_source,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "base": {
                            "type": "object",
                            "x-vibecad-reference": True,
                            "properties": {
                                "document_uid": {"type": "string"},
                                "object_name": {"type": "string"},
                            },
                            "required": ["document_uid", "object_name"],
                            "additionalProperties": False,
                        },
                        "arm": {
                            "type": "object",
                            "x-vibecad-reference": True,
                            "properties": {
                                "document_uid": {"type": "string"},
                                "object_name": {"type": "string"},
                            },
                            "required": ["document_uid", "object_name"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["base", "arm"],
                    "additionalProperties": False,
                },
                "inputs": {"base": reference_a, "arm": reference_b},
                "expected_outputs": [
                    {"name": "Main", "type": "assembly"},
                    {"name": "Base", "type": "component_link"},
                    {"name": "Arm", "type": "component_link"},
                    {"name": "Hinge", "type": "joint"},
                    {"name": "Diagnostics", "type": "solver_diagnostics"},
                ],
            },
            "document_objects": [
                {"name": "SourceA", "label": "Source A", "type_id": "Part::Feature"},
                {"name": "SourceB", "label": "Source B", "type_id": "Part::Feature"},
            ],
            "document_name": str(live_document.Name),
            "document_uid": str(live_document.Uid),
            "surface": resolve_modeling_surface(
                "AssemblyWorkbench", "vibescript"
            ).summary(),
        }
        assembly_prepared = prepare_candidate(assembly_captured)
        assembly_service = _WorkbenchService("AssemblyWorkbench")
        assembly_prepared = finalize_candidate(
            assembly_prepared,
            capture_reference_inputs(assembly_service, assembly_prepared),
        )
        assembly_execution = execute_candidate(
            assembly_prepared, cancellation_check=None
        )
        assert assembly_execution.get("ok") is True, assembly_execution
        assert assembly_execution["assembly_validation"]["solver_code"] == 0
        assert assembly_execution["assembly_validation"]["joint_count"] == 1
        assembly_validated = validate_candidate(assembly_prepared, assembly_execution)
        assert assembly_validated.get("ok") is True
        diagnostics = next(
            item
            for item in assembly_validated["outputs"]
            if item["name"] == "Diagnostics"
        )
        assert diagnostics["diagnostics"]["status"] == "solved"
        retain_candidate(assembly_prepared, status="validated")
        service = assembly_service
        publication = publish_candidate(service, assembly_prepared, assembly_validated)
        accepted = accept_candidate(assembly_prepared, publication)
        _assert_program_inspection(assembly_captured, assembly_prepared)
        assert accepted["accepted_revision"] == assembly_prepared["revision"]
        live_names = {
            name: details["object_name"]
            for name, details in accepted["live_outputs"].items()
        }
        assert live_document.getObject(live_names["Main"]).TypeId == (
            "Assembly::AssemblyObject"
        )
        assert live_document.getObject(live_names["Base"]).LinkedObject is source_a
        assert live_document.getObject(live_names["Arm"]).LinkedObject is source_b
        assert live_document.getObject(live_names["Hinge"]).Proxy is not None
        diagnostics_object = live_document.getObject(live_names["Diagnostics"])
        assert diagnostics_object.VibeCADSolverStatus == "solved"
        assert diagnostics_object.VibeCADSolverCode == 0
        grounded = [
            obj
            for obj in live_document.Objects
            if str(getattr(obj, "VibeCADVibeScriptOutputName", "")) == "Base.ground"
        ]
        assert len(grounded) == 1
        assert grounded[0].Proxy is not None
        accepted_diagnostics = next(
            item["diagnostics"]
            for item in accepted["outputs"]
            if item["name"] == "Diagnostics"
        )
        assert accepted_diagnostics["solver_code"] == 0
        assert accepted_diagnostics["grounded_components"] == ["Base"]
        assert live_document.undo()
        for object_name in live_names.values():
            assert live_document.getObject(object_name) is None
        assert live_document.getObject(source_a.Name) is source_a
        assert live_document.getObject(source_b.Name) is source_b
        assert live_document.redo()
        for object_name in live_names.values():
            assert live_document.getObject(object_name) is not None

        update_captured = {
            **assembly_captured,
            "operation": "edit_source",
            "tool_name": "vibescript.assembly.edit_source",
            "arguments": {
                "program_id": assembly_prepared["program_id"],
                "expected_revision": assembly_prepared["revision"],
                "source": assembly_source.replace(
                    "Fixture Assembly",
                    "Updated Assembly",
                ),
            },
        }
        update_prepared = prepare_candidate(update_captured)
        update_prepared = finalize_candidate(
            update_prepared,
            capture_reference_inputs(service, update_prepared),
        )
        update_execution = execute_candidate(update_prepared, cancellation_check=None)
        assert update_execution.get("ok") is True, update_execution
        update_validated = validate_candidate(update_prepared, update_execution)
        retain_candidate(update_prepared, status="validated")
        update_publication = publish_candidate(
            service, update_prepared, update_validated
        )
        updated = accept_candidate(update_prepared, update_publication)
        assert {
            name: details["object_name"]
            for name, details in updated["live_outputs"].items()
        } == live_names
        assert live_document.getObject(live_names["Main"]).Label == "Updated Assembly"
        assert live_document.undo()
        assert live_document.getObject(live_names["Main"]).Label == (
            "Fixture Assembly"
        )
        assert live_document.redo()
        assert live_document.getObject(live_names["Main"]).Label == (
            "Updated Assembly"
        )

        failed_captured = {
            **assembly_captured,
            "operation": "edit_source",
            "tool_name": "vibescript.assembly.edit_source",
            "arguments": {
                "program_id": update_prepared["program_id"],
                "expected_revision": update_prepared["revision"],
                "source": assembly_source.replace(
                    "Fixture Assembly",
                    "Updated Assembly",
                ).replace("api.solve", "api.missing_export"),
            },
        }
        failed_prepared = prepare_candidate(failed_captured)
        failed_prepared = finalize_candidate(
            failed_prepared,
            capture_reference_inputs(service, failed_prepared),
        )
        failed_execution = execute_candidate(failed_prepared, cancellation_check=None)
        assert failed_execution.get("ok") is False
        failed_retained = retain_candidate(
            failed_prepared,
            status="failed",
            failure=failed_execution,
        )
        assert (
            failed_retained["manifest"]["accepted_revision"]
            == update_prepared["revision"]
        )
        assert (
            failed_retained["manifest"]["working_revision"]
            == failed_prepared["revision"]
        )
        assert live_document.getObject(live_names["Main"]).Label == "Updated Assembly"

        document_path = root / "assembly-vibescript.FCStd"
        live_document.recompute()
        live_document.saveAs(str(document_path))
        App.closeDocument(live_document.Name)
        reopened = App.openDocument(str(document_path))
        assert reopened is not None
        for output_name, object_name in live_names.items():
            obj = reopened.getObject(object_name)
            assert obj is not None, output_name
            assert (
                str(getattr(obj, PROP_PROGRAM_ID, "")) == update_prepared["program_id"]
            )
        reopened.setUndoMode(1)

        delete_captured = {
            **update_captured,
            "operation": "delete_program",
            "tool_name": "vibescript.assembly.delete_program",
            "arguments": {
                "program_id": update_prepared["program_id"],
                "expected_revision": failed_prepared["revision"],
                "reason": "Integration lifecycle complete",
            },
            "document_name": str(reopened.Name),
            "document_uid": str(reopened.Uid),
        }
        prepared_delete = prepare_delete(delete_captured)
        deletion = delete_live_program(service, prepared_delete)
        deleted = finish_delete(prepared_delete, deletion)
        assert deleted["ok"] is True
        assert reopened.getObject("SourceA") is not None
        assert reopened.getObject("SourceB") is not None
        assert not any(
            str(getattr(obj, PROP_PROGRAM_ID, "")) == update_prepared["program_id"]
            for obj in reopened.Objects
        )
        assert reopened.undo()
        for object_name in live_names.values():
            assert reopened.getObject(object_name) is not None
        assert reopened.getObject("SourceA") is not None
        assert reopened.getObject("SourceB") is not None
        assert reopened.redo()
        assert not any(
            str(getattr(obj, PROP_PROGRAM_ID, ""))
            == update_prepared["program_id"]
            for obj in reopened.Objects
        )
        App.closeDocument(reopened.Name)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("VibeScript domain worker integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
