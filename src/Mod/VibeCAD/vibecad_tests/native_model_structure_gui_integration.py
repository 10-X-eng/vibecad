# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD gate for Native Model structure and reusable Sketch setup."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import PartDesign
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelStructureSchema import (
    model_structure_capability_definitions,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _make_source_body(document):
    document.openTransaction("Create Model structure gate source")
    operation = document.addObject("PartDesign::DesignBox", "GateSourceBox")
    edit = PartDesign.beginDesignOperationEdit(operation)
    operation.Length = 24.0
    operation.Width = 18.0
    operation.Height = 8.0
    PartDesign.setDesignOperationTargets(edit, "New Body", [])
    document.recompute()
    outputs = list(PartDesign.finalizeDesignOperationEdit(edit) or [])
    if len(outputs) != 1:
        raise AssertionError("Gate source did not publish one Body")
    outputs[0].Label = "Gate Source Body"
    document.commitTransaction()
    PartDesign.validateDesign(operation)
    return operation, outputs[0]


def _turn(definitions):
    schemas = tuple(
        definition.provider_schema(
            tuple(variant.operation for variant in definition.variants)
        )
        for definition in definitions
    )
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "b" * 64,
            tuple(
                sorted(
                    {
                        action
                        for definition in definitions
                        for variant in definition.variants
                        for action in variant.action_ids
                    }
                )
            ),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=tuple(definition.name for definition in definitions),
        schemas=schemas,
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelStructureGate")
        VibeGui._connect_document_observer()
        _process_events()

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-structure-gui")
        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: "model",
            edit_or_task_active=lambda: False,
        )
        definitions = model_structure_capability_definitions()
        turn = _turn(definitions)
        registry = build_native_capability_registry()
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def native_call(name, arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                name,
                json.dumps(arguments, separators=(",", ":")),
                f"model-structure-call-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            return result

        def undo_redo(*names):
            document.undo()
            _process_events()
            assert all(document.getObject(name) is None for name in names)
            document.redo()
            _process_events()
            assert all(document.getObject(name) is not None for name in names)

        before_invalid = tuple(obj.Name for obj in document.Objects)
        invalid = native_call(
            "model.structure",
            {"operation": "new_body", "label": "Missing explicit parent"},
            succeeds=False,
        )
        assert invalid["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before_invalid

        component_result = native_call(
            "model.structure",
            {
                "operation": "new_component",
                "label": "Bracket Component",
                "parent_component": None,
            },
        )
        component_name = component_result["component"]["object_name"]
        assert component_result["assistant_undo_available"] is True
        undo_redo(component_name)
        component = document.getObject(component_name)
        assert component.TypeId == "PartDesign::Component"

        body_result = native_call(
            "model.structure",
            {
                "operation": "new_body",
                "label": "Empty Physical Body",
                "component": {"object_name": component_name},
            },
        )
        body_name = body_result["body"]["object_name"]
        undo_redo(body_name)
        body = document.getObject(body_name)
        component = document.getObject(component_name)
        assert body in list(component.Group)
        assert list(body.Group) == [] and body.Tip is None

        sketch_result = native_call(
            "model.sketch",
            {
                "operation": "new_sketch",
                "label": "Bracket Profile",
                "support": {
                    "kind": "base_plane",
                    "plane": "XY",
                    "offset_mm": 0.0,
                },
            },
        )
        sketch_name = sketch_result["sketch"]["object_name"]
        undo_redo(sketch_name)
        sketch = document.getObject(sketch_name)
        assert sketch.getParentGeoFeatureGroup() is None
        assert sketch_result["entered_edit_mode"] is False
        assert sketch_result["next_step"] == {
            "human_action": "open_created_sketch"
        }
        assert Gui.activeDocument().getInEdit() is None
        PartDesign.validateDesign(sketch)

        readiness = native_call(
            "sketch.validate",
            {
                "operation": "validate_sketch",
                "target": {"object_name": sketch_name},
            },
        )
        assert readiness["valid"] is True
        assert readiness["geometry_count"] == 0
        assert readiness["solid_feature_ready"] is False

        _source_operation, source_body = _make_source_body(document)
        _process_events()

        face_sketch_result = native_call(
            "model.sketch",
            {
                "operation": "new_sketch",
                "label": "Face Supported Profile",
                "support": {
                    "kind": "planar_face",
                    "target": {
                        "object_name": source_body.Name,
                        "subelement": "Face6",
                    },
                },
            },
        )
        face_sketch_name = face_sketch_result["sketch"]["object_name"]
        face_sketch = document.getObject(face_sketch_name)
        assert face_sketch.getParentGeoFeatureGroup() is None
        assert len(face_sketch.AttachmentSupport) == 1
        PartDesign.validateDesign(face_sketch)

        reference_result = native_call(
            "model.structure",
            {
                "operation": "sub_shape_binder",
                "label": "Source Body Reference",
                "references": [
                    {"object_name": source_body.Name, "subelements": []}
                ],
            },
        )
        reference_name = reference_result["reference"]["object_name"]
        undo_redo(reference_name)
        reference = document.getObject(reference_name)
        assert reference.TypeId == "PartDesign::SubShapeBinder"
        assert reference.getParentGeoFeatureGroup() is None
        PartDesign.validateDesign(reference)

        empty_clone_before = tuple(obj.Name for obj in document.Objects)
        invalid_clone = native_call(
            "model.structure",
            {
                "operation": "clone",
                "source_body": {"object_name": body_name},
                "label": "Invalid Empty Clone",
                "output_body_label": "Invalid Empty Body Copy",
            },
            succeeds=False,
        )
        assert invalid_clone["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == empty_clone_before

        clone_result = native_call(
            "model.structure",
            {
                "operation": "clone",
                "source_body": {"object_name": source_body.Name},
                "label": "Source Body Clone",
                "output_body_label": "Source Body Copy",
            },
        )
        clone_name = clone_result["operation"]["object_name"]
        clone_body_name = clone_result["output_body"]["object_name"]
        clone_body_id = str(document.getObject(clone_body_name).VibeCADBodyId)
        clone_operation_id = str(document.getObject(clone_name).OperationId)
        undo_redo(clone_name, clone_body_name)
        PartDesign.validateDesign(document.getObject(clone_name))

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-model-"))
        save_path = save_directory / "ModelStructure.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        _process_events()
        for name in (
            component_name,
            body_name,
            sketch_name,
            face_sketch_name,
            reference_name,
            clone_name,
            clone_body_name,
        ):
            assert document.getObject(name) is not None, name
        assert str(document.getObject(clone_body_name).VibeCADBodyId) == clone_body_id
        assert str(document.getObject(clone_name).OperationId) == clone_operation_id
        PartDesign.validateDesign(document.getObject(sketch_name))
        PartDesign.validateDesign(document.getObject(reference_name))
        PartDesign.validateDesign(document.getObject(clone_name))

        print("VIBECAD_NATIVE_MODEL_STRUCTURE_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
