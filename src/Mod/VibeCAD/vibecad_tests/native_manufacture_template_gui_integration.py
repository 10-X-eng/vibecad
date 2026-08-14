# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact CAM Job template output."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Main.Gui.Job as PathJobGui
from Path.Main.Gui.JobCmd import CommandJobTemplateExport
import Path.Preferences as PathPreferences
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    resolve_native_provider_surface,
)
from VibeCADNativeContextManifest import context_actions_for_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureState import job_state, tool_controller_state
from VibeCADNativeManufactureTemplateSchema import (
    MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
)
from VibeCADNativeOutput import authorize_native_output_path
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTargets import document_uid
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 12) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def _create_fixture(document, name: str):
    model = document.addObject("Part::Feature", f"{name}Model")
    model.Shape = Part.makeBox(36.0, 24.0, 8.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    job.Description = "Source Job Description"
    processors = tuple(job.getEnumerationsOfProperty("PostProcessor") or ())
    assert processors
    job.PostProcessor = next(
        (value for value in processors if "linuxcnc" in value.casefold()),
        processors[0],
    )
    job.PostProcessorArgs = "--no-show-editor"
    job.PostProcessorOutputFile = "Reusable-%D.ngc"
    assert document.recompute(None, True, True) is not False
    return model, job


def _target(obj, reader) -> dict[str, str]:
    state = reader(obj)
    return {
        "object_name": str(state["object_name"]),
        "expected_state_sha256": str(state["state_sha256"]),
    }


def _surface_and_turn():
    Gui.activateWorkbench("CAMWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject,
        "VibeCADRibbonController",
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture"
    action = next(
        value
        for value in context_actions_for_surface("manufacture")
        if value.action_id == "CAM_ExportTemplate"
    )
    assert (
        action.capability_family,
        action.operation_variant,
        action.exact_target_type,
        action.classification.export,
        action.transaction_behavior,
    ) == (
        MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
        "export_template",
        "ExactCamJobTemplateContentAndHumanAuthorizedOutput",
        True,
        "output",
    )
    registry = build_native_capability_registry()
    full_provider = resolve_native_provider_surface(surface, registry)
    assert MANUFACTURE_TEMPLATE_CAPABILITY_NAME not in {
        *full_provider.missing_definition_names,
        *full_provider.missing_implementation_names,
        *full_provider.incomplete_definition_names,
    }
    definition = registry.definition(MANUFACTURE_TEMPLATE_CAPABILITY_NAME)
    schema = definition.provider_schema(("export_template",))
    branch = schema["parameters"]["oneOf"][0]
    assert branch["additionalProperties"] is False
    assert branch["required"] == [
        "operation",
        "job",
        "description",
        "include_postprocessing",
        "tool_controllers",
        "stock",
        "setup_sheet",
    ]
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert not any(
        value in encoded for value in ('"path"', '"destination"', '"file_name"')
    )
    background = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    turn = NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
                NATIVE_BACKGROUND_CAPABILITY_NAME,
            ),
            schemas=(
                schema,
                background.provider_schema(("status", "cancel")),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )
    return controller, registry, turn


def _document_state(document, state_store) -> dict:
    timeline = document.getObject("VibeCADTimeline")
    return {
        "objects": tuple(obj.Name for obj in document.Objects),
        "states": tuple(
            (obj.Name, tuple(str(value) for value in obj.State))
            for obj in document.Objects
        ),
        "timeline": (
            tuple(obj.Name for obj in timeline.Operations),
            tuple(bool(value) for value in timeline.VisibilityAtEnd),
            tuple(bool(value) for value in timeline.SuppressionAtEnd),
            int(timeline.Position),
        ),
        "selection": tuple(
            (item.Object.Name, tuple(item.SubElementNames))
            for item in Gui.Selection.getSelectionEx()
        ),
        "visibility": tuple(
            (obj.Name, bool(obj.ViewObject.Visibility))
            for obj in document.Objects
            if getattr(obj, "ViewObject", None) is not None
        ),
        "undo": int(document.UndoCount),
        "redo": int(document.RedoCount),
        "transaction": int(document.getBookedTransactionID() or 0),
        "gui_modified": bool(Gui.getDocument(document.Name).Modified),
        "revision": state_store.current_revision(document_uid(document)),
    }


def _payload(job, controller) -> dict:
    return {
        "operation": "export_template",
        "job": _target(job, job_state),
        "description": str(job.Description),
        "include_postprocessing": True,
        "tool_controllers": [_target(controller, tool_controller_state)],
        "stock": {"kind": "include", "extent": True, "placement": True},
        "setup_sheet": {
            "tool_rapids": True,
            "coolant": True,
            "operation_heights": True,
            "operation_depths": True,
            "operation_settings": [],
        },
    }


def _run() -> None:
    document = None
    restored_document = None
    temporary = None
    exit_code = 1
    preferences = PathPreferences.preferences()
    advanced_present = (
        PathPreferences.EnableAdvancedOCLFeatures in tuple(preferences.GetBools())
    )
    advanced_before = bool(
        preferences.GetBool(PathPreferences.EnableAdvancedOCLFeatures, False)
    )
    try:
        preferences.SetBool(PathPreferences.EnableAdvancedOCLFeatures, True)
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-template-gui-")
        root = Path(temporary.name)
        output = root / "job_authorized.json"
        document = App.newDocument("NativeManufactureTemplateGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, registry, turn = _surface_and_turn()
        model, job = _create_fixture(document, "Template")
        tool_controller = job.Tools.Group[0]
        document.clearUndos()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model)

        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-template-gui")
        authorization_mode = {"value": "allow"}
        requests = []
        authorizer_threads = []
        main_thread_id = threading.get_ident()

        def authorize(request):
            requests.append(request)
            authorizer_threads.append(threading.get_ident())
            if authorization_mode["value"] == "cancel":
                return None
            if authorization_mode["value"] == "selection_stale":
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(job)
            return authorize_native_output_path(request, output)

        def reauthorize() -> None:
            require_frozen_native_surface(turn.surface, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
            authorize_output=authorize,
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
        payload = _payload(job, tool_controller)
        invalid = dict(payload)
        invalid["path"] = str(root / "provider.json")
        rejected = dispatcher.call(
            MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
            json.dumps(invalid, separators=(",", ":")),
            "native-template-provider-path",
        )
        assert rejected["ok"] is False
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert not (root / "provider.json").exists()

        invalid_settings = json.loads(json.dumps(payload))
        invalid_settings["setup_sheet"]["operation_settings"] = ["NotRegistered"]
        settings_rejected = dispatcher.call(
            MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
            json.dumps(invalid_settings, separators=(",", ":")),
            "native-template-invalid-setting",
        )
        assert settings_rejected["ok"] is False
        assert settings_rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "available_operation_settings" in settings_rejected["repair"]

        before = _document_state(document, state_store)
        result = dispatcher.call(
            MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
            json.dumps(payload, separators=(",", ":")),
            "native-template-success",
        )
        assert result["ok"] is True, result
        duplicate = dispatcher.call(
            MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
            json.dumps(payload, separators=(",", ":")),
            "native-template-success",
        )
        assert duplicate == result
        assert len(requests) == 1
        assert authorizer_threads == [main_thread_id]
        assert requests[0].suggested_file_name.startswith("job_")
        assert requests[0].allowed_suffixes == (".json",)
        assert output.is_file()
        expected = job.Proxy.exportTemplateAttributes(
            job,
            description=payload["description"],
            includePostProcessing=True,
            toolControllers=(tool_controller,),
            includeStock=True,
            includeStockExtent=True,
            includeStockPlacement=True,
            includeSettingToolRapid=True,
            includeSettingCoolant=True,
            includeSettingOperationHeights=True,
            includeSettingOperationDepths=True,
            includeSettingOperations=(),
        )
        expected_bytes = json.dumps(
            expected,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        assert output.read_bytes() == expected_bytes
        human_output = root / "job_human_command.json"
        CommandJobTemplateExport.Execute(job, human_output)
        assert human_output.read_bytes() == expected_bytes
        exported = json.loads(expected_bytes.decode("utf-8"))
        assert exported["Version"] == 1
        assert exported["Desc"] == payload["description"]
        assert len(exported["ToolController"]) == 1
        assert "Stock" in exported and "SetupSheet" in exported
        receipt = result
        assert receipt["operation"] == "export_template"
        assert receipt["template"]["version"] == 1
        assert receipt["template"]["tool_controller_count"] == 1
        assert receipt["template"]["stock"] == "include"
        assert receipt["output"]["file_name"] == output.name
        assert str(root) not in json.dumps(receipt, separators=(",", ":"))
        assert _document_state(document, state_store) == before

        prior = output.read_bytes()
        authorization_mode["value"] = "cancel"
        cancelled = dispatcher.call(
            MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
            json.dumps(payload, separators=(",", ":")),
            "native-template-cancel",
        )
        assert cancelled["ok"] is False
        assert cancelled["error_code"] == (
            "NATIVE_MANUFACTURE_TEMPLATE_OUTPUT_CANCELLED"
        )
        assert output.read_bytes() == prior
        assert _document_state(document, state_store) == before

        authorization_mode["value"] = "selection_stale"
        stale = dispatcher.call(
            MANUFACTURE_TEMPLATE_CAPABILITY_NAME,
            json.dumps(payload, separators=(",", ":")),
            "native-template-selection-stale",
        )
        assert stale["ok"] is False
        assert stale["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert output.read_bytes() == prior
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model)
        assert _document_state(document, state_store) == before
        authorization_mode["value"] = "allow"

        restored_document = App.newDocument("NativeManufactureTemplateRestore")
        restored_document.UndoMode = 1
        restored_model = restored_document.addObject(
            "Part::Feature",
            "RestoredTemplateModel",
        )
        restored_model.Shape = Part.makeBox(20.0, 16.0, 6.0)
        restored_document.recompute()
        restored_job = PathJobGui.Create(
            [restored_model],
            str(output),
            openTaskPanel=False,
        )
        assert restored_document.recompute(None, True, True) is not False
        assert restored_job.Description == payload["description"]
        assert restored_job.PostProcessor == job.PostProcessor
        assert len(restored_job.Tools.Group) == 1
        assert restored_job.Stock is not None

        print(
            "VIBECAD_NATIVE_MANUFACTURE_TEMPLATE_GUI_OK "
            "context=true complete_family=true closed_schema=true "
            "no_provider_path=true exact_job=true exact_controllers=true "
            "human_authorized=true canonical_human_format=true "
            "round_trip=true cancel=true stale=true duplicate_guard=true "
            "document_unchanged=true history=true undo=true redo=true "
            "selection=true visibility=true low_noise=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if restored_document is not None and App.getDocument(
            restored_document.Name
        ) is not None:
            App.closeDocument(restored_document.Name)
        if document is not None and App.getDocument(document.Name) is not None:
            App.closeDocument(document.Name)
        if advanced_present:
            preferences.SetBool(
                PathPreferences.EnableAdvancedOCLFeatures,
                advanced_before,
            )
        else:
            preferences.RemBool(PathPreferences.EnableAdvancedOCLFeatures)
        if temporary is not None:
            temporary.cleanup()
        QtWidgets.QApplication.instance().exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
