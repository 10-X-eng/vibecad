# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact CAM semantic operation copies."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Base.Util as PathUtil
import Path.Dressup.Gui.Array as PathDressupArrayGui
import Path.Main.Gui.Job as PathJobGui
import Path.Op.Custom as PathCustom
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureFocusedModifySchema import (
    MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES,
)

from VibeCADNativeManufactureState import job_state, persistent_resource_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


CAPABILITY_NAME = MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES["copy_operations"]


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface():
    Gui.activateWorkbench("CAMWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject,
        "VibeCADRibbonController",
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture", surface.surface_id
    return controller, surface


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _visibility(document) -> tuple:
    return tuple(
        (obj.Name, bool(obj.ViewObject.Visibility))
        for obj in document.Objects
        if getattr(obj, "ViewObject", None) is not None
    )


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _arguments(job, operation_names: tuple[str, ...]) -> dict:
    return {
        "operation": "copy_operations",
        "jobs": [
            {
                "job": _target(job_state(job)),
                "operation_names": list(operation_names),
            }
        ],
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("copy_operations",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "jobs",
        "job",
        "operation_names",
        "object_name",
        "expected_state_sha256",
    ):
        assert field in encoded
    assert '"maxItems":8' in encoded
    assert '"maxItems":64' in encoded
    assert '"uniqueItems":true' in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _create_fixture(document):
    model = document.addObject("Part::Feature", "CopyGateModel")
    model.Label = "Operation copy gate model"
    model.Shape = Part.makeBox(24.0, 18.0, 8.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group

    roughing = PathCustom.Create("CopyRoughing", parentJob=job)
    job.Proxy.addOperation(roughing)
    roughing.Label = "Copy roughing pass"
    roughing.Gcode = [
        "G0 X0 Y0 Z5",
        "G0 X0 Y0 Z0",
        "G1 X16 Y0 Z0",
        "G1 X16 Y10 Z0",
    ]

    finishing_base = PathCustom.Create("CopyFinishingBase", parentJob=job)
    job.Proxy.addOperation(finishing_base)
    finishing_base.Label = "Copy finishing base"
    finishing_base.Gcode = [
        "G0 X0 Y0 Z4",
        "G0 X0 Y0 Z0",
        "G1 X20 Y0 Z0",
        "G1 X20 Y12 Z0",
    ]
    finishing = PathDressupArrayGui.Create(finishing_base, "CopyFinishingArray")
    assert finishing is not None
    finishing.Label = "Copy finishing array"
    finishing.Copies = 1
    finishing.Offset = App.Vector(28.0, 0.0, 0.0)
    assert document.recompute(None, True, True) is not False
    assert tuple(job.Operations.Group) == (roughing, finishing)
    assert all(value.isValid() for value in (roughing, finishing_base, finishing))
    return model, job, roughing, finishing_base, finishing


def _assert_copy_resource(resource, owner) -> None:
    assert str(resource.VibeCADTimelineRole) == "resource"
    assert resource.VibeCADTimelineOwner is owner
    assert "VibeCADTimelineReplacedInputs" not in resource.PropertiesList


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-copy-")
        save_path = Path(temporary.name) / "native-manufacture-copy.FCStd"
        document = App.newDocument("NativeManufactureCopyGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_OperationCopy"]
        actual_plan = (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        )
        assert actual_plan == (
            CAPABILITY_NAME,
            "copy_operations",
            "ExactCamOperationCopySet",
            True,
            False,
        ), actual_plan

        model, job, roughing, finishing_base, finishing = _create_fixture(document)
        document.clearUndos()
        source_objects = (roughing, finishing_base, finishing)
        source_states = {
            value.Name: persistent_resource_state(value) for value in source_objects
        }
        initial_objects = tuple(document.Objects)
        initial_group = tuple(job.Operations.Group)
        initial_timeline = (
            tuple(document.VibeCADTimeline.Operations),
            tuple(bool(value) for value in document.VibeCADTimeline.VisibilityAtEnd),
            tuple(bool(value) for value in document.VibeCADTimeline.SuppressionAtEnd),
            int(document.VibeCADTimeline.Position),
        )

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-operation-copy-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        call_index = 0

        def call(payload: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-copy-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()
        visibility_before = _visibility(document)

        stale = _arguments(job, (roughing.Name,))
        stale["jobs"][0]["job"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert tuple(document.Objects) == initial_objects
        assert int(document.UndoCount) == 0

        missing = _arguments(job, (roughing.Name,))
        missing["jobs"][0]["operation_names"] = ["MissingCopyOperation"]
        missing_result = call(missing, succeeds=False)
        assert missing_result["error_code"] == "NATIVE_MANUFACTURE_TARGET_STALE"
        assert missing_result["repair"]["job_object_name"] == job.Name
        assert int(document.UndoCount) == 0

        single_payload = _arguments(job, (finishing.Name,))
        with patch(
            "VibeCADNativeManufactureModifyRuntime.verify_operation_copy",
            side_effect=RuntimeError("forced copy postcondition failure"),
        ):
            failed = call(single_payload, succeeds=False)
        assert failed["error_code"] == "NATIVE_POSTCONDITION_FAILED", failed
        assert tuple(document.Objects) == initial_objects
        assert tuple(job.Operations.Group) == initial_group
        assert initial_timeline == (
            tuple(document.VibeCADTimeline.Operations),
            tuple(bool(value) for value in document.VibeCADTimeline.VisibilityAtEnd),
            tuple(bool(value) for value in document.VibeCADTimeline.SuppressionAtEnd),
            int(document.VibeCADTimeline.Position),
        )
        assert int(document.UndoCount) == 0

        single = call(single_payload)
        revision_after_single = state_store.current_revision(context.document_uid)
        _events(16)
        assert (
            state_store.current_revision(context.document_uid)
            == revision_after_single
        )
        assert single["history"]["grouped"] is False
        assert single["history"]["closure_object_count"] == 2, single
        assert single["assistant_undo_available"] is True
        assert len(single["receipt"]["created"]) == 1
        copied_finishing_name = single["copies"][0]["object_name"]
        copied_finishing = document.getObject(copied_finishing_name)
        copied_base = copied_finishing.Base
        copied_base_name = str(copied_base.Name)
        assert copied_finishing is not finishing
        assert copied_base is not finishing_base
        assert tuple(job.Operations.Group) == (*initial_group, copied_finishing)
        assert str(copied_finishing.VibeCADTimelineRole) == "operation"
        assert PathUtil.timelineParentJob(copied_finishing) is job
        _assert_copy_resource(copied_base, copied_finishing)
        assert copied_base.ViewObject.Visibility is False
        assert _selection() == selection_before
        assert _visibility(document)[: len(visibility_before)] == visibility_before
        assert all(
            persistent_resource_state(value) == source_states[value.Name]
            for value in source_objects
        )
        assert int(document.UndoCount) == 1

        document.undo()
        _events(16)
        assert document.getObject(copied_finishing_name) is None
        assert document.getObject(copied_base_name) is None
        assert tuple(job.Operations.Group) == initial_group
        document.redo()
        _events(16)
        job = document.getObject(job.Name)
        roughing = document.getObject(roughing.Name)
        finishing_base = document.getObject(finishing_base.Name)
        finishing = document.getObject(finishing.Name)
        copied_finishing = document.getObject(copied_finishing_name)
        copied_base = document.getObject(copied_base_name)
        assert copied_finishing.Base is copied_base
        _assert_copy_resource(copied_base, copied_finishing)

        # Undo/redo is a human history action, so subsequent assistant work must
        # begin from a fresh turn rather than bypassing the revision guard.
        turn = _turn(surface, registry)
        frozen = turn.surface
        ledger.begin_run("native-manufacture-operation-copy-gui-after-redo")
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        multi_payload = _arguments(job, (finishing.Name, roughing.Name))
        before_multi = tuple(document.Objects)
        multi = call(multi_payload)
        revision_after_multi = state_store.current_revision(context.document_uid)
        _events(16)
        assert state_store.current_revision(context.document_uid) == revision_after_multi
        assert multi["history"]["grouped"] is True
        assert multi["history"]["copied_operation_count"] == 2
        assert multi["history"]["closure_object_count"] == 3
        assert [value["source_object_name"] for value in multi["copies"]] == [
            roughing.Name,
            finishing.Name,
        ]
        multi_output_names = [value["object_name"] for value in multi["copies"]]
        controller_name = multi["history"]["object_name"]
        history_controller = document.getObject(controller_name)
        multi_outputs = [document.getObject(name) for name in multi_output_names]
        created_multi = tuple(obj for obj in document.Objects if obj not in before_multi)
        assert len(created_multi) == 4
        assert tuple(history_controller.CAMOutputs) == tuple(multi_outputs)
        assert history_controller.ViewObject.ShowInTree is False
        assert str(history_controller.VibeCADTimelineRole) == "operation"
        for copied in created_multi:
            if copied is history_controller:
                continue
            _assert_copy_resource(copied, history_controller)
        assert all(value in job.Operations.Group for value in multi_outputs)
        assert len(multi["receipt"]["created"]) == 2
        assert int(document.UndoCount) == 2
        assert _selection() == selection_before
        assert _visibility(document)[: len(visibility_before)] == visibility_before

        document.undo()
        _events(16)
        assert document.getObject(controller_name) is None
        assert all(document.getObject(name) is None for name in multi_output_names)
        document.redo()
        _events(16)
        job = document.getObject(job.Name)
        history_controller = document.getObject(controller_name)
        multi_outputs = [document.getObject(name) for name in multi_output_names]
        assert tuple(history_controller.CAMOutputs) == tuple(multi_outputs)

        document_name = str(document.Name)
        job_name = str(job.Name)
        source_names = tuple(value.Name for value in source_objects)
        document.saveAs(str(save_path))
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _events(24)
        job = document.getObject(job_name)
        history_controller = document.getObject(controller_name)
        copied_finishing = document.getObject(copied_finishing_name)
        copied_base = document.getObject(copied_base_name)
        multi_outputs = [document.getObject(name) for name in multi_output_names]
        assert job is not None and all(document.getObject(name) for name in source_names)
        assert copied_finishing.Base is copied_base
        _assert_copy_resource(copied_base, copied_finishing)
        assert tuple(history_controller.CAMOutputs) == tuple(multi_outputs)
        assert all(value in job.Operations.Group for value in (*multi_outputs, copied_finishing))
        for value in multi_outputs:
            _assert_copy_resource(value, history_controller)
        assert all(value.isValid() for value in (*multi_outputs, copied_finishing, copied_base))

        print(
            "VIBECAD_NATIVE_MANUFACTURE_OPERATION_COPY_GUI_OK "
            "exact_jobs=true stale=true rollback=true single=true dressup=true "
            "batch=true source_preserved=true history=true receipt=true "
            "selection=true visibility=true undo=true redo=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
