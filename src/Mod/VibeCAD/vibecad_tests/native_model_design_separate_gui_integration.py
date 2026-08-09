# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for Native Model Design Separate."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Materials
import Part
import PartDesign
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADNativeModelStructureRuntime as structure_runtime_module
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDesignSeparate import (
    create_design_separate,
    preflight_design_separate,
    prepare_design_separate,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelStructureSchema import (
    model_structure_capability_definitions,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger


_MATERIAL_UUID = "94370b96-c97e-4a3f-83b2-11d7461f7da7"


def _process_events(rounds: int = 18) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _shape_signature(shape):
    bounds = shape.BoundBox
    return tuple(
        round(float(value), 7)
        for value in (
            shape.Volume,
            bounds.XMin,
            bounds.XMax,
            bounds.YMin,
            bounds.YMax,
            bounds.ZMin,
            bounds.ZMax,
        )
    )


def _same_solid_geometry(expected, actual) -> bool:
    expected_center = expected.CenterOfMass
    actual_center = actual.CenterOfMass
    scalar_pairs = (
        (expected.Volume, actual.Volume),
        (expected.Area, actual.Area),
        (expected_center.x, actual_center.x),
        (expected_center.y, actual_center.y),
        (expected_center.z, actual_center.z),
    )
    if not all(
        math.isclose(float(left), float(right), rel_tol=1.0e-9, abs_tol=1.0e-7)
        for left, right in scalar_pairs
    ):
        return False
    overlap = expected.common(actual)
    tolerance = max(1.0e-7, abs(float(expected.Volume)) * 1.0e-8)
    return math.isclose(
        float(overlap.Volume),
        float(expected.Volume),
        rel_tol=0.0,
        abs_tol=tolerance,
    )


def _same_solid_set(expected, actual) -> bool:
    remaining = list(actual)
    if len(expected) != len(remaining):
        return False
    for wanted in expected:
        index = next(
            (
                candidate_index
                for candidate_index, candidate in enumerate(remaining)
                if _same_solid_geometry(wanted, candidate)
            ),
            None,
        )
        if index is None:
            return False
        remaining.pop(index)
    return not remaining


def _root_source(
    document,
    name,
    solids,
    *,
    label=None,
    placement=None,
    finalize=False,
):
    source = document.addObject("Part::Feature", name)
    source.Label = label or name
    source.Shape = Part.makeCompound(list(solids))
    if placement is not None:
        source.Placement = placement
    source.ShapeMaterial = Materials.MaterialManager().getMaterial(_MATERIAL_UUID)
    source.ViewObject.ShapeColor = (0.18, 0.42, 0.76)
    source.ViewObject.LineColor = (0.04, 0.05, 0.06)
    source.ViewObject.PointColor = (0.80, 0.20, 0.10)
    source.ViewObject.Transparency = 17
    document.recompute()
    if finalize:
        PartDesign.finalizeDesignDefinition(source)
        document.recompute()
    return source


def _component(document, name, label, placement=None):
    document.openTransaction(f"Create {label}")
    try:
        component = document.addObject("PartDesign::Component", name)
        document.classifyProvisionalTimelineInternalObject(component)
        component.Label = label
        if placement is not None:
            component.Placement = placement
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert str(component.ComponentId)
    return component


def _select(*objects, subelement=None) -> None:
    Gui.Selection.clearSelection()
    for obj in objects:
        if subelement:
            Gui.Selection.addSelection(obj, subelement)
        else:
            Gui.Selection.addSelection(obj)
    _process_events(4)


def _new_objects(document, names_before, type_id):
    return [
        obj
        for obj in document.Objects
        if obj.Name not in names_before and obj.TypeId == type_id
    ]


def _assert_separate_graph(operation, source, outputs, destination=None):
    assert operation.TypeId == "PartDesign::DesignSeparate"
    assert operation.Source is source
    assert operation.ResultOperation == "New Bodies"
    assert operation.Shape.isNull()
    assert operation.getParentGeoFeatureGroup() is None
    assert operation.isValid(), operation.getStatusString()
    assert len(outputs) == len(source.Shape.Solids)
    assert list(operation.InputStates) == []
    assert list(operation.InputBodyIds) == []
    assert list(operation.InputFrames) == []
    assert list(operation.OutputPreviousInputIndices) == [-1] * len(outputs)
    assert len(operation.RegionWitnesses) == len(outputs)
    assert len(set(operation.OutputBodyIds)) == len(outputs)
    assert [str(body.VibeCADBodyId) for body in outputs] == list(
        operation.OutputBodyIds
    )
    assert all(body.getParentGeoFeatureGroup() is destination for body in outputs)
    component_id = str(destination.ComponentId) if destination else ""
    assert list(operation.OutputComponentIds) == [component_id] * len(outputs)
    assert list(operation.VibeCADTimelineReplacedInputs) == [source]
    assert not source.Visibility
    assert all(len(body.Shape.Solids) == 1 and body.Shape.isValid() for body in outputs)
    assert all(body.Tip.CurrentState.Operation is operation for body in outputs)
    assert all(
        str(body.ShapeMaterial.UUID) == str(source.ShapeMaterial.UUID)
        for body in outputs
    )
    assert _same_solid_set(
        tuple(source.Shape.Solids),
        tuple(operation.PreviewShape.Solids),
    )
    assert sorted(_shape_signature(body.Shape) for body in outputs) == sorted(
        _shape_signature(shape) for shape in operation.OutputShapes
    )
    PartDesign.validateDesign(operation)


def _separate_turn():
    definition = next(
        item
        for item in model_structure_capability_definitions()
        if item.name == "model.structure"
    )
    schema = definition.provider_schema(("separate",))
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "d" * 64,
            ("PartDesign_Separate",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=("model.structure",),
        schemas=(schema,),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _dispatcher(document):
    service = get_service()
    service.select_modeling_engine("native")
    state = service.native_document_state_store()
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("native-model-design-separate-gui")
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
    turn = _separate_turn()
    dispatcher = NativeTurnDispatcher(
        document=document,
        state=state,
        registry=build_native_capability_registry(),
        turn=turn,
        runtimes=build_native_runtime_bindings(context, turn.tool_names),
        reauthorize_turn=lambda: None,
        active_document=lambda: App.ActiveDocument,
    )
    return dispatcher


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelDesignSeparateGate")
        document.UndoMode = True
        VibeGui._connect_document_observer()
        _process_events()

        # The human command is immediate and accepts only a whole active root
        # multi-solid definition plus an optional Component.
        one_solid = _root_source(
            document,
            "HumanOneSolid",
            [Part.makeBox(3, 4, 5)],
        )
        _select(one_solid)
        assert not Gui.isCommandActive("PartDesign_Separate")

        human_source = _root_source(
            document,
            "HumanSeparateSource",
            [
                Part.makeBox(10, 8, 6),
                Part.makeCylinder(3, 9, App.Vector(20, 0, 0)),
            ],
            label="Human Housing",
        )
        _select(human_source, subelement="Face1")
        assert not Gui.isCommandActive("PartDesign_Separate")
        _select(human_source)
        assert Gui.isCommandActive("PartDesign_Separate")
        human_names_before = {obj.Name for obj in document.Objects}
        Gui.runCommand("PartDesign_Separate", 0)
        _process_events()
        assert not Gui.Control.activeDialog()
        assert not document.HasPendingTransaction
        human_operation = _new_objects(
            document,
            human_names_before,
            "PartDesign::DesignSeparate",
        )[0]
        human_outputs = _new_objects(
            document,
            human_names_before,
            "PartDesign::Body",
        )
        _assert_separate_graph(
            human_operation,
            human_source,
            human_outputs,
        )
        assert human_operation.Label == "Separate Human Housing"
        assert [body.Label for body in human_outputs] == [
            "Human Housing 1",
            "Human Housing 2",
        ]

        # Its edit surface exposes destination/output reconciliation and no
        # geometry-creation dialog during the ribbon action itself.
        assert human_operation.ViewObject.doubleClicked()
        _process_events()
        assert Gui.Control.activeDialog()
        summaries = [
            widget
            for widget in Gui.getMainWindow().findChildren(QtWidgets.QLabel)
            if widget.objectName() == "DesignSeparateSummary"
        ]
        output_lists = [
            widget
            for widget in Gui.getMainWindow().findChildren(QtWidgets.QListWidget)
            if widget.objectName() == "DesignBodyList"
        ]
        assert len(summaries) == 1
        assert len(output_lists) == 1 and output_lists[0].count() == 2
        Gui.Control.activeTaskDialog().reject()
        _process_events()
        assert not Gui.Control.activeDialog()
        assert not document.HasPendingTransaction

        dispatcher = _dispatcher(document)
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                "model.structure",
                json.dumps(arguments, separators=(",", ":")),
                f"model-separate-call-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            return result

        def arguments(source, destination=None, label="Native Separate"):
            return {
                "operation": "separate",
                "label": label,
                "source": {"object_name": source.Name},
                "destination_component": (
                    {"object_name": destination.Name} if destination else None
                ),
            }

        # Closed schema: omission is rejected before preflight or mutation.
        before_schema_failure = tuple(obj.Name for obj in document.Objects)
        malformed = arguments(human_source)
        malformed.pop("destination_component")
        failed = native_call(malformed, succeeds=False)
        assert failed["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before_schema_failure

        # Domain failures are likewise mutation-free.
        for invalid_source in (one_solid, human_outputs[0], human_operation):
            before = tuple(obj.Name for obj in document.Objects)
            result = native_call(arguments(invalid_source), succeeds=False)
            assert result["error_code"] in {
                "NATIVE_MODEL_INVALID",
                "NATIVE_TARGET_INVALID",
            }
            assert tuple(obj.Name for obj in document.Objects) == before
            assert not document.HasPendingTransaction

        regular_part = document.addObject("App::Part", "NotAComponent")
        invalid_destination_source = _root_source(
            document,
            "InvalidDestinationSource",
            [Part.makeBox(5, 5, 5), Part.makeBox(4, 4, 4, App.Vector(12, 0, 0))],
        )
        before = tuple(obj.Name for obj in document.Objects)
        result = native_call(
            arguments(invalid_destination_source, regular_part),
            succeeds=False,
        )
        assert result["error_code"] in {
            "NATIVE_MODEL_INVALID",
            "NATIVE_TARGET_INVALID",
        }
        assert tuple(obj.Name for obj in document.Objects) == before

        missing = native_call(
            {
                "operation": "separate",
                "label": "Missing Separate",
                "source": {"object_name": "NoSuchSeparateSource"},
                "destination_component": None,
            },
            succeeds=False,
        )
        assert missing["error_code"] == "NATIVE_TARGET_INVALID"

        inactive_source = _root_source(
            document,
            "InactiveSeparateSource",
            [Part.makeBox(5, 6, 7), Part.makeBox(3, 4, 5, App.Vector(12, 0, 0))],
            finalize=True,
        )
        timeline = document.VibeCADTimeline
        timeline_end = timeline.Position
        source_index = list(timeline.Operations).index(inactive_source)
        timeline.Position = source_index
        _process_events()
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(arguments(inactive_source), succeeds=False)
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()

        grouped_source = _root_source(
            document,
            "GroupedSeparateSource",
            [Part.makeBox(4, 5, 6), Part.makeBox(3, 4, 5, App.Vector(10, 0, 0))],
        )
        group = document.addObject("App::DocumentObjectGroup", "SourceGroup")
        group.addObject(grouped_source)
        document.recompute()
        before = tuple(obj.Name for obj in document.Objects)
        result = native_call(arguments(grouped_source), succeeds=False)
        assert result["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        linked_target = _root_source(
            document,
            "LinkedSeparateTarget",
            [Part.makeBox(4, 4, 4), Part.makeBox(3, 3, 3, App.Vector(10, 0, 0))],
            finalize=True,
        )
        link = document.addObject("App::Link", "SeparateLink")
        link.LinkedObject = linked_target
        document.recompute()
        before = tuple(obj.Name for obj in document.Objects)
        result = native_call(arguments(link), succeeds=False)
        assert result["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        # Preflight captures the exact shape and label; a changed source cannot
        # cross the transaction boundary.
        changed_source = _root_source(
            document,
            "ChangedAfterPreflight",
            [Part.makeBox(6, 7, 8), Part.makeBox(2, 3, 4, App.Vector(14, 0, 0))],
        )
        spec = prepare_design_separate(
            document.Uid,
            {"source": {"object_name": changed_source.Name}, "destination_component": None},
        )
        prepared = preflight_design_separate(document, spec)
        changed_source.Label = "Changed after preflight"
        names_before = tuple(obj.Name for obj in document.Objects)
        document.openTransaction("Reject changed Separate preflight")
        try:
            try:
                create_design_separate(
                    document,
                    label="Must Not Exist",
                    prepared=prepared,
                )
            except NativeModelError:
                pass
            else:
                raise AssertionError("Changed Separate preflight was accepted")
        finally:
            document.abortTransaction()
        assert tuple(obj.Name for obj in document.Objects) == names_before
        assert "VibeCADDefinitionId" not in changed_source.PropertiesList

        # A verifier failure rolls back the operation, outputs, source
        # publication, visibility, and destination membership atomically.
        rollback_source = _root_source(
            document,
            "RollbackSeparateSource",
            [Part.makeBox(7, 8, 9), Part.makeCylinder(2, 5, App.Vector(16, 0, 0))],
        )
        rollback_names = tuple(obj.Name for obj in document.Objects)
        original_verifier = structure_runtime_module.verify_design_separate

        def fail_verifier(_document, _draft):
            raise NativeModelError("Forced Separate verifier failure")

        structure_runtime_module.verify_design_separate = fail_verifier
        try:
            result = native_call(arguments(rollback_source), succeeds=False)
        finally:
            structure_runtime_module.verify_design_separate = original_verifier
        assert result["error_code"] == "NATIVE_MODEL_INVALID", result
        assert tuple(obj.Name for obj in document.Objects) == rollback_names
        assert rollback_source.Visibility
        assert "VibeCADDefinitionId" not in rollback_source.PropertiesList
        assert not document.HasPendingTransaction

        # Root output with transformed, mixed source solids.
        native_source = _root_source(
            document,
            "NativeSeparateSource",
            [
                Part.makeBox(11, 7, 5),
                Part.makeCylinder(2.5, 8, App.Vector(18, 2, 0)),
                Part.makeSphere(3, App.Vector(32, 1, 4)),
            ],
            label="Native Gearcase",
            placement=App.Placement(
                App.Vector(9, -4, 3),
                App.Rotation(App.Vector(0, 0, 1), 17),
            ),
        )
        source_brep = native_source.Shape.exportBrepToString()
        root_result = native_call(
            arguments(native_source, label="Separate Native Gearcase")
        )
        assert root_result["body_count"] == 3
        assert root_result["assistant_undo_available"] is True
        root_operation = document.getObject(root_result["operation"]["object_name"])
        root_outputs = [
            document.getObject(item["body"]["object_name"])
            for item in root_result["bodies"]
        ]
        _assert_separate_graph(root_operation, native_source, root_outputs)
        assert native_source.Shape.exportBrepToString() == source_brep
        assert [body.Label for body in root_outputs] == [
            "Native Gearcase 1",
            "Native Gearcase 2",
            "Native Gearcase 3",
        ]
        assert all(body.ViewObject.ShapeColor == native_source.ViewObject.ShapeColor for body in root_outputs)
        assert all(body.ViewObject.LineColor == native_source.ViewObject.LineColor for body in root_outputs)
        assert all(body.ViewObject.Transparency == 17 for body in root_outputs)

        # Explicit destination Component preserves global geometry while the
        # output Bodies use the Component's local frame.
        component_source = _root_source(
            document,
            "ComponentSeparateSource",
            [
                Part.makeBox(8, 9, 10),
                Part.makeBox(4, 5, 6, App.Vector(15, 2, 1)),
            ],
            label="Component Housing",
            placement=App.Placement(
                App.Vector(-20, 30, 8),
                App.Rotation(App.Vector(1, 0, 0), 11),
            ),
        )
        destination = _component(
            document,
            "SeparateDestination",
            "Separated Components",
            App.Placement(
                App.Vector(100, 25, -10),
                App.Rotation(App.Vector(0, 1, 0), 23),
            ),
        )
        component_result = native_call(
            arguments(
                component_source,
                destination,
                label="Separate Component Housing",
            )
        )
        operation_name = component_result["operation"]["object_name"]
        output_names = [
            item["body"]["object_name"] for item in component_result["bodies"]
        ]
        operation = document.getObject(operation_name)
        outputs = [document.getObject(name) for name in output_names]
        _assert_separate_graph(operation, component_source, outputs, destination)
        assert component_result["destination_component"]["object_name"] == destination.Name
        accepted_body_ids = list(operation.OutputBodyIds)
        accepted_witnesses = [tuple(value) for value in operation.RegionWitnesses]
        accepted_operation_id = str(operation.OperationId)

        for _index in range(3):
            document.recompute()
            _process_events(4)
            _assert_separate_graph(operation, component_source, outputs, destination)
            assert list(operation.OutputBodyIds) == accepted_body_ids
            assert [tuple(value) for value in operation.RegionWitnesses] == accepted_witnesses

        # One assistant undo removes the entire semantic operation and restores
        # its source; redo restores the same persistent identities.
        document.undo()
        _process_events()
        assert document.getObject(operation_name) is None
        assert all(document.getObject(name) is None for name in output_names)
        assert component_source.Visibility
        document.redo()
        _process_events()
        operation = document.getObject(operation_name)
        outputs = [document.getObject(name) for name in output_names]
        component_source = document.getObject(component_source.Name)
        destination = document.getObject(destination.Name)
        _assert_separate_graph(operation, component_source, outputs, destination)
        assert str(operation.OperationId) == accepted_operation_id
        assert list(operation.OutputBodyIds) == accepted_body_ids
        assert [tuple(value) for value in operation.RegionWitnesses] == accepted_witnesses

        # Save/reopen preserves exact operation, Body, witness, Component, and
        # replaced-source identities.
        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-separate-"))
        save_path = save_directory / "NativeDesignSeparate.FCStd"
        source_name = component_source.Name
        component_name = destination.Name
        document.saveAs(str(save_path))
        saved_document_name = document.Name
        App.closeDocument(saved_document_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        _process_events()
        operation = document.getObject(operation_name)
        component_source = document.getObject(source_name)
        destination = document.getObject(component_name)
        outputs = [document.getObject(name) for name in output_names]
        _assert_separate_graph(operation, component_source, outputs, destination)
        assert str(operation.OperationId) == accepted_operation_id
        assert list(operation.OutputBodyIds) == accepted_body_ids
        assert [tuple(value) for value in operation.RegionWitnesses] == accepted_witnesses
        assert not document.HasPendingTransaction
        assert not Gui.Control.activeDialog()

        print("VIBECAD_NATIVE_MODEL_DESIGN_SEPARATE_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            Gui.Control.activeTaskDialog().reject()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
