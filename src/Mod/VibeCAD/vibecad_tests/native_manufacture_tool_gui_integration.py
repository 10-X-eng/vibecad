# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM ToolBits and controllers."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Main.Gui.Job as PathJobGui
import Path.Main.Job as PathJob
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureSnapshot import build_manufacture_snapshot
from VibeCADNativeManufactureState import job_state, tool_controller_state
from VibeCADNativeManufactureToolSchema import (
    MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME,
)
from VibeCADNativeManufactureFocusedToolSchema import (
    MANUFACTURE_FOCUSED_TOOL_CAPABILITIES,
)
from VibeCADNativeManufactureToolOutputSchema import (
    MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME,
)
from VibeCADNativeOutput import authorize_native_output_path
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


ADD_TOOL = MANUFACTURE_FOCUSED_TOOL_CAPABILITIES["create_controller"]
SET_CONTROLLER = MANUFACTURE_FOCUSED_TOOL_CAPABILITIES["update_controller"]
UPDATE_TOOL = MANUFACTURE_FOCUSED_TOOL_CAPABILITIES["update_tool_bit"]


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


def _commit(document, label: str, action):
    document.openTransaction(label)
    transaction = int(document.getBookedTransactionID())
    assert transaction
    try:
        value = action()
        assert document.recompute(None, True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return value


def _create_model_and_job(document):
    def create_model():
        model = document.addObject("Part::Feature", "ToolGateModel")
        model.Label = "Tool gate model"
        model.Shape = Part.makeBox(40.0, 30.0, 10.0)
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create tool gate model", create_model)

    def create_job():
        job = PathJob.Create("Job", [model], templateFile=None)
        provider = PathJobGui.ViewProvider(job.ViewObject)
        job.ViewObject.Proxy = provider
        job.ViewObject.addExtension("Gui::ViewProviderGroupExtensionPython")
        provider.setupEditVisibility(job)
        try:
            provider.syncTimelineReplacedInputs(job)
        finally:
            provider.resetEditVisibility(job)
        provider.applyAcceptedReplacementVisibilityTransition(job)
        provider.deleteOnReject = False
        return job

    return model, _commit(document, "Create tool gate Job", create_job)


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _turn(surface, registry) -> NativeTurnSnapshot:
    catalog = registry.definition(MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME)
    mutations = tuple(
        registry.definition(name) for name in (ADD_TOOL, SET_CONTROLLER, UPDATE_TOOL)
    )
    output = registry.definition(MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME)
    assert catalog is not None and all(mutations) and output is not None
    schemas = (
        catalog.provider_schema(("list_tools", "read_tool")),
        *(
            definition.provider_schema((definition.variants[0].operation,))
            for definition in mutations
        ),
        output.provider_schema(("save", "save_as")),
    )
    encoded = json.dumps(schemas, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "catalog_id",
        "expected_content_sha256",
        "horizontal_feed_mm_per_minute",
        "property_changes",
        '"choice"',
    ):
        assert field in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME,
                ADD_TOOL,
                SET_CONTROLLER,
                UPDATE_TOOL,
                MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME,
            ),
            schemas=schemas,
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _controller_settings(label: str, tool_number: int | dict, feed: float) -> dict:
    return {
        "label": label,
        "tool_number": tool_number,
        "tool_length_offset": 4,
        "spindle_speed_rpm": 12_500.0,
        "spindle_direction": "Forward",
        "horizontal_feed_mm_per_minute": feed,
        "vertical_feed_mm_per_minute": feed / 3.0,
        "ramp_feed_mm_per_minute": feed / 2.0,
        "lead_in_feed_mm_per_minute": feed * 0.8,
        "lead_out_feed_mm_per_minute": feed * 0.7,
        "horizontal_rapid_mm_per_minute": 3_000.0,
        "vertical_rapid_mm_per_minute": 1_500.0,
    }


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _job_resource_names(document, job) -> set[str]:
    return {
        obj.Name
        for obj in document.Objects
        if str(getattr(obj, "VibeCADTimelineRole", "") or "") == "resource"
        and getattr(obj, "VibeCADTimelineOwner", None) is job
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-tool-")
        temporary_root = Path(temporary.name)
        save_path = temporary_root / "native-manufacture-tool.FCStd"
        fctb_path = temporary_root / "native-tool.fctb"
        yaml_path = temporary_root / "native-tool.yaml"
        output_authorizations = []
        cancel_output = {"value": False}

        def authorize_output(request):
            output_authorizations.append(request)
            if cancel_output["value"]:
                cancel_output["value"] = False
                return None
            destination = {
                "cam_toolbit_save": fctb_path,
                "cam_toolbit_save_as": yaml_path,
            }[request.purpose]
            return authorize_native_output_path(request, destination)

        document = App.newDocument("NativeManufactureToolGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        assert (
            plans["CAM_ToolBitDock"].capability_family,
            plans["CAM_ToolBitDock"].operation_variant,
            plans["CAM_ToolBitDock"].exact_target_type,
            plans["CAM_ToolBitDock"].classification.mutation,
            plans["CAM_ToolBitDock"].classification.human_only,
        ) == (
            ADD_TOOL,
            "create_controller",
            "ExactCamJobAndCatalogTool",
            True,
            False,
        )

        model, job = _create_model_and_job(document)
        job_name = str(job.Name)
        initial_tool_count = len(job.Tools.Group)
        snapshot = build_manufacture_snapshot(document)
        assert snapshot["tool_catalog"]["count"] >= 1
        assert snapshot["tool_catalog"]["items"]

        registry = build_native_capability_registry()
        output_definition = registry.definition(
            MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME
        )
        assert output_definition is not None
        assert {
            variant.operation: variant.action_ids
            for variant in output_definition.variants
        } == {
            "save": frozenset({"CAM_ToolBitSave"}),
            "save_as": frozenset({"CAM_ToolBitSaveAs"}),
        }
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-tool-gui")

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
            authorize_output=authorize_output,
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

        def refresh_dispatcher() -> None:
            nonlocal dispatcher
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

        def call(capability: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                capability,
                json.dumps(arguments, separators=(",", ":")),
                f"native-manufacture-tool-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        catalog_snapshot = snapshot["tool_catalog"]
        revision_before_reads = state_store.current_revision(context.document_uid)
        undo_before_reads = int(document.UndoCount)
        listing = call(
            MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME,
            {
                "operation": "list_tools",
                "offset": 0,
                "page_size": 128,
            },
        )
        assert listing["state_sha256"] == catalog_snapshot["state_sha256"]
        searched = call(
            MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME,
            {
                "operation": "list_tools",
                "query": "drill",
                "offset": 0,
                "page_size": 8,
            },
        )
        assert searched["count"] >= 1
        assert searched["query"] == "drill"
        assert all(
            "drill" in f"{item['label']} {item['shape_type']}".casefold()
            for item in searched["items"]
        )
        selected = next(
            (item for item in listing["items"] if item["shape_type"].lower() == "bullnose"),
            listing["items"][0],
        )
        exact_catalog = {
            "catalog_id": selected["catalog_id"],
            "expected_content_sha256": selected["content_sha256"],
        }
        detail = call(
            MANUFACTURE_TOOL_CATALOG_CAPABILITY_NAME,
            {"operation": "read_tool", "catalog_tool": exact_catalog},
        )
        properties = {
            item["property_name"]: item
            for item in detail["tool"]["editable_properties"]
        }
        assert properties["Diameter"]["kind"] == "length_mm"
        assert state_store.current_revision(context.document_uid) == revision_before_reads
        assert int(document.UndoCount) == undo_before_reads

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()

        job_before_create = job_state(job)
        names_before_create = tuple(obj.Name for obj in document.Objects)
        resources_before_create = _job_resource_names(document, job)
        create_arguments = {
            "job_target": _target(job_before_create),
            "catalog_tool": exact_catalog,
            "tool_label": "Native bullnose",
            "tool_property_changes": [
                {
                    "property_name": "Diameter",
                    "value": {"kind": "length_mm", "value": 8.0},
                }
            ],
            "controller": _controller_settings(
                "Native roughing controller",
                {"kind": "next_available"},
                900.0,
            ),
        }

        conflict = json.loads(json.dumps(create_arguments))
        conflict["controller"]["tool_number"] = {
            "kind": "explicit",
            "value": int(job.Tools.Group[0].ToolNumber),
        }
        failed = call(ADD_TOOL, conflict, succeeds=False)
        assert failed["error_code"] == "NATIVE_MANUFACTURE_TOOL_NUMBER_CONFLICT", failed
        assert tuple(obj.Name for obj in document.Objects) == names_before_create
        assert int(document.UndoCount) == undo_before_reads

        created = call(ADD_TOOL, create_arguments)
        revision_after_create = state_store.current_revision(context.document_uid)
        created_tool = document.getObject(created["controller"]["tool"]["object_name"])
        assert created_tool is not None
        assert not bool(getattr(created_tool.Proxy, "_visual_update_queued", False))
        assert not hasattr(created_tool.Proxy, "_recompute_observer")
        assert not bool(document.RecomputePending)
        touched_after_create = [
            obj.Name for obj in document.Objects if "Touched" in obj.State
        ]
        assert not touched_after_create, touched_after_create
        _events(12)
        assert state_store.current_revision(context.document_uid) == revision_after_create
        controller_name = created["controller"]["object_name"]
        tool_name = created["controller"]["tool"]["object_name"]
        tool_controller = document.getObject(controller_name)
        assert tool_controller is not None
        assert tool_controller in tuple(job.Tools.Group)
        created_state = tool_controller_state(tool_controller)
        shape_volume_created = float(tool_controller.Tool.Shape.Volume)
        shape_bounds_created = tuple(
            float(value)
            for value in (
                tool_controller.Tool.Shape.BoundBox.XLength,
                tool_controller.Tool.Shape.BoundBox.YLength,
                tool_controller.Tool.Shape.BoundBox.ZLength,
            )
        )
        assert created_state["label"] == "Native roughing controller"
        assert created_state["tool"]["label"] == "Native bullnose"
        created_properties = {
            item["property_name"]: item
            for item in created_state["tool"]["editable_properties"]
        }
        assert created_properties["Diameter"] == {
            "property_name": "Diameter",
            "group": "Shape",
            "kind": "length_mm",
            "value": 8.0,
        }
        new_resources = _job_resource_names(document, job) - resources_before_create
        assert controller_name in new_resources and tool_name in new_resources
        assert created["resource_count"] == len(new_resources)
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()

        names_after_create = tuple(obj.Name for obj in document.Objects)
        document.undo()
        _events(12)
        assert document.getObject(controller_name) is None
        assert tuple(obj.Name for obj in document.Objects) == names_before_create
        assert len(job.Tools.Group) == initial_tool_count
        document.redo()
        _events(12)
        job = document.getObject(job_name)
        tool_controller = document.getObject(controller_name)
        assert job is not None and tool_controller is not None
        assert tuple(obj.Name for obj in document.Objects) == names_after_create
        assert tool_controller_state(tool_controller)["state_sha256"] == created_state["state_sha256"]
        refresh_dispatcher()

        update_controller_arguments = {
            "target": _target(tool_controller_state(tool_controller)),
            "controller": _controller_settings(
                "Native finishing controller",
                9,
                600.0,
            ),
        }
        updated_controller = call(
            SET_CONTROLLER,
            update_controller_arguments,
        )
        _events(8)
        controller_after = tool_controller_state(tool_controller)
        assert controller_after == updated_controller["controller"]
        job_after_controller = job_state(job)
        assert updated_controller["job"] == {
            "object_name": job_after_controller["object_name"],
            "state_sha256": job_after_controller["state_sha256"],
            "tool_count": job_after_controller["counts"]["tools"],
        }
        assert controller_after["tool_number"] == 9
        assert controller_after["horizontal_feed_mm_per_minute"] == 600.0
        document.undo()
        _events(8)
        tool_controller = document.getObject(controller_name)
        assert tool_controller_state(tool_controller)["state_sha256"] == created_state["state_sha256"]
        document.redo()
        _events(8)
        tool_controller = document.getObject(controller_name)
        assert tool_controller_state(tool_controller)["state_sha256"] == controller_after["state_sha256"]
        refresh_dispatcher()

        tool_before_update = tool_controller_state(tool_controller)["tool"]
        shape_volume_before = float(tool_controller.Tool.Shape.Volume)
        visual_before = _job_resource_names(document, job)
        controller_as_tool = call(
            UPDATE_TOOL,
            {
                "target": _target(tool_controller_state(tool_controller)),
                "label": "Wrong target",
                "property_changes": [
                    {
                        "property_name": "Diameter",
                        "value": 7.5,
                    }
                ],
            },
            succeeds=False,
        )
        assert controller_as_tool["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        assert controller_as_tool["repair"]["target"] == _target(tool_before_update)
        update_tool_arguments = {
            "target": _target(tool_before_update),
            "label": "Native 7.5 mm bullnose",
            "property_changes": [
                {
                    "property_name": "Diameter",
                    "value": 7.5,
                },
                {
                    "property_name": "Material",
                    "value": "Carbide",
                },
            ],
        }
        updated_tool = call(UPDATE_TOOL, update_tool_arguments)
        _events(12)
        job = document.getObject(job_name)
        tool_controller = document.getObject(controller_name)
        tool_after = tool_controller_state(tool_controller)["tool"]
        assert tool_after == updated_tool["tool"]
        job_after_tool = job_state(job)
        assert updated_tool["jobs"] == [
            {
                "object_name": job_after_tool["object_name"],
                "state_sha256": job_after_tool["state_sha256"],
                "tool_count": job_after_tool["counts"]["tools"],
            }
        ]
        assert tool_after["label"] == "Native 7.5 mm bullnose"
        after_properties = {
            item["property_name"]: item for item in tool_after["editable_properties"]
        }
        assert after_properties["Diameter"]["value"] == 7.5
        assert after_properties["Material"]["value"] == "Carbide"
        final_controller_state = tool_controller_state(tool_controller)
        shape_volume_after = float(tool_controller.Tool.Shape.Volume)
        assert shape_volume_after < shape_volume_before, (
            shape_volume_created,
            shape_volume_before,
            shape_volume_after,
            shape_bounds_created,
            tuple(
                float(value)
                for value in (
                    tool_controller.Tool.Shape.BoundBox.XLength,
                    tool_controller.Tool.Shape.BoundBox.YLength,
                    tool_controller.Tool.Shape.BoundBox.ZLength,
                )
            ),
        )
        visual_after = _job_resource_names(document, job)
        assert controller_name in visual_after and tool_name in visual_after
        assert visual_after == visual_before
        document.undo()
        _events(12)
        job = document.getObject(job_name)
        tool_controller = document.getObject(controller_name)
        assert tool_controller_state(tool_controller)["tool"]["state_sha256"] == tool_before_update["state_sha256"]
        assert _job_resource_names(document, job) == visual_before
        assert math.isclose(
            float(tool_controller.Tool.Shape.Volume),
            shape_volume_before,
            rel_tol=1.0e-9,
        )
        document.redo()
        _events(12)
        job = document.getObject(job_name)
        tool_controller = document.getObject(controller_name)
        assert tool_controller_state(tool_controller)["tool"]["state_sha256"] == tool_after["state_sha256"]
        assert _job_resource_names(document, job) == visual_after
        assert math.isclose(
            float(tool_controller.Tool.Shape.Volume),
            shape_volume_after,
            rel_tol=1.0e-9,
        )
        refresh_dispatcher()

        from Path.Tool.toolbit.serializers.fctb import FCTBSerializer
        from Path.Tool.toolbit.serializers.yaml import YamlToolBitSerializer

        exact_tool = document.getObject(tool_after["object_name"])
        assert exact_tool is not None
        raw_tool_state_before = tuple(str(value) for value in exact_tool.State)
        FCTBSerializer.serialize(exact_tool.Proxy)
        assert tuple(str(value) for value in exact_tool.State) == raw_tool_state_before
        serialized_read_state = tool_controller_state(tool_controller)["tool"]
        assert tuple(str(value) for value in exact_tool.State) == raw_tool_state_before
        assert serialized_read_state["state_sha256"] == tool_after["state_sha256"]
        tool_before_output = serialized_read_state
        raw_tool_state_before_output = tuple(str(value) for value in exact_tool.State)
        revision_before_output = state_store.current_revision(context.document_uid)
        undo_before_output = int(document.UndoCount)
        objects_before_output = tuple(document.Objects)
        selection_before_output = _selection()
        authorization_count = len(output_authorizations)
        stale_output = call(
            MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME,
            {
                "operation": "save",
                "target": {
                    "object_name": tool_after["object_name"],
                    "expected_state_sha256": "0" * 64,
                },
                "format": "fctb",
            },
            succeeds=False,
        )
        assert stale_output["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert len(output_authorizations) == authorization_count

        cancel_output["value"] = True
        cancelled_output = call(
            MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME,
            {
                "operation": "save",
                "target": _target(tool_before_output),
                "format": "fctb",
            },
            succeeds=False,
        )
        assert (
            cancelled_output["error_code"]
            == "NATIVE_MANUFACTURE_TOOL_OUTPUT_CANCELLED"
        )
        assert not fctb_path.exists() and not yaml_path.exists()

        saved_tool = call(
            MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME,
            {
                "operation": "save",
                "target": _target(tool_before_output),
                "format": "fctb",
            },
        )
        saved_as_tool = call(
            MANUFACTURE_TOOL_OUTPUT_CAPABILITY_NAME,
            {
                "operation": "save_as",
                "target": _target(tool_before_output),
                "format": "yaml",
            },
        )
        assert [request.purpose for request in output_authorizations[-3:]] == [
            "cam_toolbit_save",
            "cam_toolbit_save",
            "cam_toolbit_save_as",
        ]
        assert saved_tool["operation"] == "save"
        assert saved_tool["format"] == "fctb"
        assert saved_tool["output"]["file_name"] == fctb_path.name
        assert saved_as_tool["operation"] == "save_as"
        assert saved_as_tool["format"] == "yaml"
        assert saved_as_tool["output"]["file_name"] == yaml_path.name
        assert fctb_path.is_file() and yaml_path.is_file()
        restored_fctb = FCTBSerializer.deep_deserialize(fctb_path.read_bytes())
        restored_yaml = YamlToolBitSerializer.deep_deserialize(yaml_path.read_bytes())
        assert restored_fctb.label == tool_after["label"]
        assert restored_yaml.label == tool_after["label"]
        assert temporary.name not in json.dumps(saved_tool, sort_keys=True)
        assert temporary.name not in json.dumps(saved_as_tool, sort_keys=True)
        assert state_store.current_revision(context.document_uid) == revision_before_output
        assert int(document.UndoCount) == undo_before_output
        assert tuple(document.Objects) == objects_before_output
        assert _selection() == selection_before_output
        tool_after_output = tool_controller_state(tool_controller)["tool"]
        assert tool_after_output == tool_before_output, (
            tool_before_output,
            tool_after_output,
        )
        assert (
            tuple(str(value) for value in exact_tool.State)
            == raw_tool_state_before_output
        )

        minimal_created = call(
            ADD_TOOL,
            {
                "job_target": _target(job_state(job)),
                "catalog_tool": exact_catalog,
            },
        )
        _events(8)
        minimal_controller = document.getObject(
            minimal_created["controller"]["object_name"]
        )
        assert minimal_controller is not None
        assert minimal_controller.Label == f"TC: {selected['label']}"
        assert minimal_controller.Tool.Label == selected["label"]
        assert int(minimal_controller.ToolNumber) >= 1
        assert float(minimal_controller.SpindleSpeed) == 0.0
        assert float(minimal_controller.HorizFeed) == 0.0
        default_expressions = dict(minimal_controller.ExpressionEngine)
        assert default_expressions["RampFeed"] == "HorizFeed"
        assert default_expressions["LeadInFeed"] == "HorizFeed"
        assert default_expressions["LeadOutFeed"] == "HorizFeed"
        assert _selection() == selection_before
        minimal_controller_name = str(minimal_controller.Name)
        final_resources = _job_resource_names(document, job)

        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        job = document.getObject(job_name)
        tool_controller = document.getObject(controller_name)
        minimal_controller = document.getObject(minimal_controller_name)
        assert job is not None and tool_controller is not None
        assert minimal_controller is not None
        reopened = tool_controller_state(tool_controller)
        assert reopened["state_sha256"] == final_controller_state["state_sha256"]
        assert reopened["tool"]["state_sha256"] == tool_after["state_sha256"]
        assert reopened["tool_number"] == 9
        assert _job_resource_names(document, job) == final_resources
        assert not Gui.Control.activeDialog()

        print(
            "VIBECAD_NATIVE_MANUFACTURE_TOOL_GUI_OK "
            "catalog=true catalog_search=true paging_128=true exact_targets=true "
            "human_default_create=true controller_create=true "
            "controller_update=true tool_properties=true stable_resource_graph=true "
            "save=true save_as=true path_private=true output_read_only=true "
            "rollback=true undo=true redo=true reopen=true",
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
