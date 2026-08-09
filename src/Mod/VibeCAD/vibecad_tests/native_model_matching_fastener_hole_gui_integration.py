# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human/Native parity and lifecycle gate for matching fastener holes."""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import traceback
from unittest import mock

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign
from PySide import QtCore, QtGui, QtWidgets

import VibeCADFastenersGui
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADFastenerModel import create_model_fastener_graph
from VibeCADFasteners import (
    HOLE_SCHEMA,
    PROP_HOLE_FASTENER_KEY,
    PROP_HOLE_FIT,
    PROP_HOLE_PURPOSE,
    PROP_HOLE_RESOLUTION,
    PROP_HOLE_SCHEMA,
)
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelErrors import NativeModelError
import VibeCADNativeModelFastenerRuntime as runtime_module
from VibeCADNativeModelFastenerSchema import model_fastener_capability_definition
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger


def _process_events(rounds: int = 20) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _close(left: float, right: float, tolerance: float = 1.0e-7) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1.0e-9, abs_tol=tolerance)


def _shape_signature(shape) -> tuple[object, ...]:
    bounds = shape.BoundBox
    return (
        str(shape.ShapeType),
        len(shape.Solids),
        len(shape.Faces),
        len(shape.Edges),
        len(shape.Vertexes),
        float(shape.Volume),
        float(shape.Area),
        float(bounds.XMin),
        float(bounds.XMax),
        float(bounds.YMin),
        float(bounds.YMax),
        float(bounds.ZMin),
        float(bounds.ZMax),
    )


def _assert_signature(actual, expected, tolerance: float = 1.0e-7) -> None:
    assert actual[:5] == expected[:5], (actual, expected)
    for left, right in zip(actual[5:], expected[5:], strict=True):
        assert _close(left, right, tolerance), (actual, expected)


def _new_fastener(document, name: str, standard: str):
    return create_model_fastener_graph(
        document,
        label=name,
        standard=standard,
        nominal_thread="M3",
        length_mm=10.0,
        model_thread=False,
        left_handed=False,
        options={},
    )


def _new_host(document, name: str, x: float):
    operation = document.addObject("PartDesign::DesignBox", f"{name}Blank")
    edit = PartDesign.beginDesignOperationEdit(operation)
    operation.Length = 24.0
    operation.Width = 24.0
    operation.Height = 8.0
    operation.Placement.Base.x = x
    PartDesign.setDesignOperationTargets(edit, "New Body", [])
    document.recompute([operation], True, True)
    body = PartDesign.finalizeDesignOperationEdit(edit)[0]
    body.Label = f"{name} Body"
    return body, body.Tip.CurrentState


def _new_profile(document, name: str, centers: tuple[tuple[float, float], ...]):
    sketch = document.addObject("Sketcher::SketchObject", name)
    PartDesign.initializeDesignDefinition(sketch)
    sketch.Placement.Base.z = 8.0
    for x, y in centers:
        sketch.addGeometry(
            Part.Circle(
                App.Vector(x, y, 0),
                App.Vector(0, 0, 1),
                1.5,
            ),
            False,
        )
    document.recompute([sketch], True, True)
    PartDesign.finalizeDesignDefinition(sketch)
    return sketch


def _setup(document):
    document.openTransaction("Create matching-fastener-hole inputs")
    socket = _new_fastener(document, "M3 socket bolt", "ISO4762")
    countersunk = _new_fastener(document, "M3 countersunk bolt", "ISO10642")
    cases = {}
    for name, x in (
        ("Parity", 0.0),
        ("TappedFirst", 40.0),
        ("TappedSecond", 70.0),
        ("Counterbore", 100.0),
        ("Countersink", 130.0),
        ("Rollback", 160.0),
    ):
        cases[name] = _new_host(document, name, x)
    profiles = {
        "Parity": _new_profile(document, "ParityLocations", ((12.0, 12.0),)),
        "Tapped": _new_profile(
            document,
            "TappedLocations",
            ((52.0, 12.0), (82.0, 12.0)),
        ),
        "Counterbore": _new_profile(
            document,
            "CounterboreLocations",
            ((112.0, 12.0),),
        ),
        "Countersink": _new_profile(
            document,
            "CountersinkLocations",
            ((142.0, 12.0),),
        ),
        "Rollback": _new_profile(
            document,
            "RollbackLocations",
            ((172.0, 12.0),),
        ),
    }
    owned_body = document.addObject("PartDesign::Body", "OwnedSketchBody")
    owned_profile = owned_body.newObject(
        "Sketcher::SketchObject",
        "OwnedHoleLocations",
    )
    owned_profile.addGeometry(
        Part.Circle(App.Vector(4, 4, 0), App.Vector(0, 0, 1), 1.0),
        False,
    )
    document.recompute()
    document.commitTransaction()
    return {
        "socket": socket,
        "countersunk": countersunk,
        "cases": cases,
        "profiles": profiles,
        "owned_profile": owned_profile,
    }


def _finish_task() -> None:
    assert Gui.Control.activeDialog()
    for box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        if not box.isVisible():
            continue
        button = box.button(QtWidgets.QDialogButtonBox.Ok)
        if button is not None and button.isEnabled():
            button.click()
            _process_events()
            assert not Gui.Control.activeDialog()
            return
    raise AssertionError("The matching-hole task has no enabled OK button.")


def _human_parity(document, setup):
    fastener = setup["socket"]
    body, _base = setup["cases"]["Parity"]
    profile = setup["profiles"]["Parity"]
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(fastener.body)
    Gui.Selection.addSelection(profile)
    Gui.Selection.addSelection(body)
    _process_events()
    VibeCADFastenersGui.ensure_commands_registered()
    command = Gui.Command.get("VibeCAD_CreateMatchingFastenerHole")
    assert command is not None and command.isActive()

    answers = iter((("clearance", True), ("normal", True)))

    def choose_item(_parent, _title, _label, items, _current, _editable):
        answer = next(answers)
        assert answer[0] in [str(item) for item in items]
        return answer

    with mock.patch.object(QtGui, "QInputDialog") as dialog:
        dialog.getItem.side_effect = choose_item
        Gui.runCommand("VibeCAD_CreateMatchingFastenerHole")
    _process_events()
    assert dialog.getItem.call_count == 2
    hole = document.ActiveObject
    assert hole is not None and hole.TypeId == "PartDesign::DesignHole"
    _finish_task()
    PartDesign.validateDesign(hole)
    controls = {
        "base_profile": int(hole.BaseProfileType),
        "depth": str(hole.DepthType),
        "thread_type": str(hole.ThreadType),
        "thread_size": str(hole.ThreadSize),
        "thread_fit": str(hole.ThreadFit),
        "threaded": bool(hole.Threaded),
        "modeled": bool(hole.ModelThread),
        "cosmetic": bool(hole.CosmeticThread),
        "cut": str(hole.HoleCutType),
        "refine": bool(hole.Refine),
        "resolution": str(getattr(hole, PROP_HOLE_RESOLUTION)),
    }
    result_signature = _shape_signature(body.Shape)
    cutter_signature = _shape_signature(hole.AddSubShape)
    hole_name = hole.Name
    document.undo()
    _process_events()
    assert document.getObject(hole_name) is None
    assert document.getObject(body.Name) is body
    return controls, result_signature, cutter_signature


def _turn():
    definition = model_fastener_capability_definition()
    operations = tuple(variant.operation for variant in definition.variants)
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "9" * 64,
            tuple(
                next(iter(variant.action_ids))
                for variant in definition.variants
            ),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(operations),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _arguments(fastener, profile, bodies, purpose, fit):
    return {
        "operation": "create_matching_fastener_hole",
        "label": "Matching standard fastener hole",
        "fastener": {"object_name": fastener.body.Name},
        "profile": {"object_name": profile.Name},
        "purpose": purpose,
        "fit": fit,
        "targets": [{"object_name": body.Name} for body in bodies],
    }


def _assert_response(document, response, arguments, fastener, profile, bodies):
    assert set(response) == {
        "ok",
        "operation",
        "result_mode",
        "bodies",
        "feature",
        "receipt",
        "assistant_undo_available",
    }
    assert response["result_mode"] == "cut"
    operation = document.getObject(response["operation"]["object_name"])
    assert operation is not None and operation.TypeId == "PartDesign::DesignHole"
    assert operation.Profile[0] is profile
    assert operation.getParentGeoFeatureGroup() is None
    assert operation.ResultOperation == "Cut"
    assert list(operation.InputBodyIds) == [str(body.VibeCADBodyId) for body in bodies]
    feature = response["feature"]
    assert feature["fastener"]["object_name"] == fastener.body.Name
    assert feature["canonical_key"] == fastener.identity["canonical_key"]
    assert feature["purpose"] == arguments["purpose"]
    assert feature["fit"] == arguments["fit"]
    assert feature["cutter_solid_count"] == len(operation.AddSubShape.Solids)
    assert _close(feature["diameter_mm"], operation.Diameter.Value)
    assert response["assistant_undo_available"] is True
    assert {item["object_name"] for item in response["receipt"]["created"]} == {
        operation.Name
    }
    assert {item["object_name"] for item in response["receipt"]["changed"]} == {
        body.Name for body in bodies
    }
    assert response["receipt"]["deleted"] == []
    assert response["receipt"]["replaced"] == []
    assert str(getattr(operation, PROP_HOLE_SCHEMA)) == HOLE_SCHEMA
    assert str(getattr(operation, PROP_HOLE_FASTENER_KEY)) == fastener.identity[
        "canonical_key"
    ]
    assert str(getattr(operation, PROP_HOLE_PURPOSE)) == arguments["purpose"]
    assert str(getattr(operation, PROP_HOLE_FIT)) == arguments["fit"]
    PartDesign.validateDesign(operation)
    for body in bodies:
        state = body.Tip.CurrentState
        assert state.Operation is operation
        assert not body.Shape.isNull() and body.Shape.isValid()
        assert len(body.Shape.Solids) == 1
    return operation


def _record(operation, profile, bodies):
    return {
        "operation_name": operation.Name,
        "operation_id": str(operation.OperationId),
        "profile_name": profile.Name,
        "fastener_key": str(getattr(operation, PROP_HOLE_FASTENER_KEY)),
        "purpose": str(getattr(operation, PROP_HOLE_PURPOSE)),
        "fit": str(getattr(operation, PROP_HOLE_FIT)),
        "resolution": str(getattr(operation, PROP_HOLE_RESOLUTION)),
        "bodies": tuple(
            {
                "name": body.Name,
                "body_id": str(body.VibeCADBodyId),
                "state_name": body.Tip.CurrentState.Name,
                "state_id": str(body.Tip.CurrentState.BodyStateId),
                "signature": _shape_signature(body.Shape),
            }
            for body in bodies
        ),
    }


def _assert_record(document, record, *, restored: bool = False):
    operation = document.getObject(record["operation_name"])
    profile = document.getObject(record["profile_name"])
    assert operation is not None and profile is not None
    assert operation.Profile[0] is profile
    assert str(operation.OperationId) == record["operation_id"]
    assert str(getattr(operation, PROP_HOLE_FASTENER_KEY)) == record["fastener_key"]
    assert str(getattr(operation, PROP_HOLE_PURPOSE)) == record["purpose"]
    assert str(getattr(operation, PROP_HOLE_FIT)) == record["fit"]
    assert str(getattr(operation, PROP_HOLE_RESOLUTION)) == record["resolution"]
    PartDesign.validateDesign(operation)
    for body_record in record["bodies"]:
        body = document.getObject(body_record["name"])
        state = document.getObject(body_record["state_name"])
        assert body is not None and state is not None
        assert str(body.VibeCADBodyId) == body_record["body_id"]
        assert body.Tip.CurrentState is state and state.Operation is operation
        assert str(state.BodyStateId) == body_record["state_id"]
        _assert_signature(
            _shape_signature(body.Shape),
            body_record["signature"],
            1.0e-2 if restored else 1.0e-7,
        )
    return operation


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeMatchingFastenerHoleGate")
        document.UndoMode = True
        VibeGui._connect_document_observer()
        setup = _setup(document)
        _process_events()
        workbench = Gui.activeWorkbench().name()

        human_controls, human_result, human_cutter = _human_parity(document, setup)
        assert Gui.activeWorkbench().name() == workbench

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-matching-fastener-hole-gui")
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
        turn = _turn()
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                "model.fastener",
                json.dumps(arguments, separators=(",", ":")),
                f"matching-fastener-hole-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            assert not Gui.Control.activeDialog()
            assert Gui.activeWorkbench().name() == workbench
            return response

        parity_body, parity_base = setup["cases"]["Parity"]
        parity_profile = setup["profiles"]["Parity"]
        parity_arguments = _arguments(
            setup["socket"],
            parity_profile,
            (parity_body,),
            "clearance",
            "normal",
        )
        parity_response = native_call(parity_arguments)
        parity_hole = _assert_response(
            document,
            parity_response,
            parity_arguments,
            setup["socket"],
            parity_profile,
            (parity_body,),
        )
        native_controls = {
            "base_profile": int(parity_hole.BaseProfileType),
            "depth": str(parity_hole.DepthType),
            "thread_type": str(parity_hole.ThreadType),
            "thread_size": str(parity_hole.ThreadSize),
            "thread_fit": str(parity_hole.ThreadFit),
            "threaded": bool(parity_hole.Threaded),
            "modeled": bool(parity_hole.ModelThread),
            "cosmetic": bool(parity_hole.CosmeticThread),
            "cut": str(parity_hole.HoleCutType),
            "refine": bool(parity_hole.Refine),
            "resolution": str(getattr(parity_hole, PROP_HOLE_RESOLUTION)),
        }
        assert native_controls == human_controls, (
            human_controls,
            native_controls,
        )
        _assert_signature(_shape_signature(parity_body.Shape), human_result)
        _assert_signature(_shape_signature(parity_hole.AddSubShape), human_cutter)
        assert parity_body.Shape.Volume < parity_base.Shape.Volume
        parity_record = _record(parity_hole, parity_profile, (parity_body,))

        document.undo()
        _process_events()
        assert document.getObject(parity_record["operation_name"]) is None
        assert parity_body.Tip.CurrentState is parity_base
        document.redo()
        _process_events()
        _assert_record(document, parity_record)

        before = tuple(obj.Name for obj in document.Objects)
        rollback_body, rollback_base = setup["cases"]["Rollback"]
        rollback_profile = setup["profiles"]["Rollback"]
        before_rollback = _shape_signature(rollback_body.Shape)

        invalid_schema = {**parity_arguments, "selection": []}
        response = native_call(invalid_schema, succeeds=False)
        assert response["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        stale = {**parity_arguments, "fastener": {"object_name": "DeletedFastener"}}
        response = native_call(stale, succeeds=False)
        assert response["error_code"] == "NATIVE_TARGET_INVALID"

        wrong_profile = {
            **parity_arguments,
            "profile": {"object_name": parity_body.Name},
        }
        response = native_call(wrong_profile, succeeds=False)
        assert response["error_code"] == "NATIVE_TARGET_INVALID"

        owned_profile = {
            **parity_arguments,
            "profile": {"object_name": setup["owned_profile"].Name},
        }
        response = native_call(owned_profile, succeeds=False)
        assert response["error_code"] == "NATIVE_MODEL_INVALID"

        self_target = {
            **parity_arguments,
            "targets": [{"object_name": setup["socket"].body.Name}],
        }
        response = native_call(self_target, succeeds=False)
        assert response["error_code"] == "NATIVE_MODEL_INVALID"

        tapped_fit = {**parity_arguments, "purpose": "tapped", "fit": "loose"}
        response = native_call(tapped_fit, succeeds=False)
        assert response["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert not document.HasPendingTransaction

        original_verifier = runtime_module.verify_design_operation

        def reject_verification(_document, _draft):
            raise NativeModelError("Forced matching-hole verifier failure.")

        runtime_module.verify_design_operation = reject_verification
        try:
            rollback_arguments = _arguments(
                setup["socket"],
                rollback_profile,
                (rollback_body,),
                "clearance",
                "close",
            )
            response = native_call(rollback_arguments, succeeds=False)
        finally:
            runtime_module.verify_design_operation = original_verifier
        assert response["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        _assert_signature(_shape_signature(rollback_body.Shape), before_rollback)
        assert rollback_body.Tip.CurrentState is rollback_base
        assert not document.HasPendingTransaction

        records = [parity_record]
        cases = (
            (
                setup["socket"],
                setup["profiles"]["Tapped"],
                (
                    setup["cases"]["TappedFirst"][0],
                    setup["cases"]["TappedSecond"][0],
                ),
                "tapped",
                "normal",
            ),
            (
                setup["socket"],
                setup["profiles"]["Counterbore"],
                (setup["cases"]["Counterbore"][0],),
                "counterbore",
                "close",
            ),
            (
                setup["countersunk"],
                setup["profiles"]["Countersink"],
                (setup["cases"]["Countersink"][0],),
                "countersink",
                "loose",
            ),
        )
        for fastener, profile, bodies, purpose, fit in cases:
            arguments = _arguments(fastener, profile, bodies, purpose, fit)
            response = native_call(arguments)
            operation = _assert_response(
                document,
                response,
                arguments,
                fastener,
                profile,
                bodies,
            )
            records.append(_record(operation, profile, bodies))

        for record in records:
            operation = _assert_record(document, record)
            bodies = [document.getObject(item["name"]) for item in record["bodies"]]
            for _index in range(4):
                assert document.recompute([operation, *bodies], True, True) is not False
                _assert_record(document, record)

        save_directory = tempfile.mkdtemp(prefix="vibecad-native-matching-hole-")
        save_path = Path(save_directory) / "matching-fastener-holes.FCStd"
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        document.UndoMode = True
        assert document.recompute(None, True, True) is not False
        _process_events()
        for record in records:
            _assert_record(document, record, restored=True)

        print("VIBECAD_NATIVE_MODEL_MATCHING_FASTENER_HOLE_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if save_directory is not None:
            shutil.rmtree(save_directory, ignore_errors=True)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
