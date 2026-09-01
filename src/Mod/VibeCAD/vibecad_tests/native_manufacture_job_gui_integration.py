# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for atomic Native CAM Job creation."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback
from types import SimpleNamespace

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Path.Preferences as CamPreferences
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureJobSchema import MANUFACTURE_JOB_CAPABILITY_NAME
from VibeCADNativeManufactureSnapshot import build_manufacture_snapshot
from VibeCADNativeManufactureState import job_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
import VibeCADScriptedPublication as ScriptedPublication
import VibeCADNativeManufactureFollowUpRuntime as ManufactureFollowUpRuntimeModule
import VibeCADNativeManufactureJobRuntime as ManufactureJobRuntimeModule


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


def _model(document, name: str, *, visible: bool, x: float):
    def create():
        model = document.addObject("Part::Feature", name)
        model.Label = name.replace("Model", " model")
        model.Shape = Part.makeBox(30.0, 20.0, 8.0, App.Vector(x, 0.0, 0.0))
        model.ViewObject.Visibility = visible
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    return _commit(document, f"Create {name}", create)


def _body_model(document, name: str, *, visible: bool, x: float):
    def create():
        body = document.addObject("PartDesign::Body", name)
        body.Label = name.replace("Model", " model")
        body.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
            "Document timeline classification",
            attr=16,
            hidden=True,
            locked=True,
        )
        body.VibeCADTimelineRole = "internal"
        ScriptedPublication.tag_object(
            body,
            role=ScriptedPublication.ROLE_IMPLEMENTATION,
            engine="vibescript:partdesign",
            model_id="0123456789abcdef0123456789abcdef",
            output_key="FixtureBlock",
            revision="sketch-v1:test",
        )
        feature = body.newObject("PartDesign::Feature", f"{name}Feature")
        feature.Shape = Part.makeBox(30.0, 20.0, 8.0, App.Vector(x, 0.0, 0.0))
        document.publishProvisionalTimelineOperationBlock(feature, (), ())
        publication = document.addObject("App::Link", f"{name}Publication")
        publication.Label = body.Label
        publication.LinkedObject = body
        ScriptedPublication.tag_object(
            publication,
            role=ScriptedPublication.ROLE_PUBLICATION,
            engine="vibescript:partdesign",
            model_id="0123456789abcdef0123456789abcdef",
            output_key="FixtureBlock",
            revision="sketch-v1:test",
        )
        publication.ViewObject.Visibility = False
        document.publishProvisionalTimelineOperationBlock(publication, (), ())
        body.ViewObject.Visibility = visible
        return body

    return _commit(document, f"Create {name}", create)


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _visible_children(item) -> tuple:
    return tuple(
        item.child(index)
        for index in range(item.childCount())
        if not item.child(index).isHidden()
    )


def _tree_snapshot(item) -> tuple:
    return (
        item.text(0),
        tuple(_tree_snapshot(child) for child in _visible_children(item)),
    )


def _tree_child(item, label: str):
    return next(
        (child for child in _visible_children(item) if child.text(0) == label),
        None,
    )


def _tree_label_count(item, label: str) -> int:
    return int(item.text(0) == label) + sum(
        _tree_label_count(child, label) for child in _visible_children(item)
    )


def _document_tree_item(document):
    for _attempt in range(80):
        _events(2)
        for tree in Gui.getMainWindow().findChildren(QtWidgets.QTreeWidget):
            if not tree.isVisible() or not tree.viewport().isVisible():
                continue
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                if not item.isHidden() and item.text(0) == document.Label:
                    return item
    return None


def _assert_manufacture_tree(document, jobs) -> None:
    document_item = _document_tree_item(document)
    assert document_item is not None
    snapshot = _tree_snapshot(document_item)
    for job in jobs:
        setup = _tree_child(document_item, job.Label)
        assert setup is not None, snapshot
        assert _tree_label_count(document_item, job.Label) == 1, snapshot
        setup_children = _visible_children(setup)
        expected_children = [job.Stock.Label, "Tools"]
        if job.Operations.Group:
            expected_children.append("Operations")
        expected_children.append(job.SetupSheet.Label)
        assert [child.text(0) for child in setup_children] == expected_children, snapshot
        tools = setup_children[1]
        assert len(_visible_children(tools)) == len(job.Tools.Group), snapshot
        assert all(
            not _visible_children(controller_item)
            for controller_item in _visible_children(tools)
        ), snapshot
        if job.Operations.Group:
            operations = _tree_child(setup, "Operations")
            assert operations is not None, snapshot
            assert [item.text(0) for item in _visible_children(operations)] == [
                operation.Label for operation in job.Operations.Group
            ], snapshot
        assert _tree_label_count(setup, job.Model.Label) == 0, snapshot

        for controller in job.Tools.Group:
            tool = controller.Tool
            assert _tree_label_count(document_item, tool.Label) == 0, snapshot
            bit_body = getattr(tool, "BitBody", None)
            if bit_body is not None:
                assert _tree_label_count(document_item, bit_body.Label) == 0, snapshot


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_JOB_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("create_job_from_template",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    assert "replace_in_history" in encoded
    assert "expected_creation_state_sha256" not in encoded
    assert "expected_job_count" not in encoded
    assert "expected_content_sha256" in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(MANUFACTURE_JOB_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _arguments(
    snapshot: dict,
    *,
    label: str = "Native production Job",
    model_names: tuple[str, ...] = ("VisibleModel", "HiddenModel"),
) -> dict:
    candidates = {item["object_name"]: item for item in snapshot["model_candidates"]}
    template = next(
        item for item in snapshot["job_creation"]["templates"] if item["is_default"]
    )
    return {
        "operation": "create_job_from_template",
        "label": label,
        "models": [
            {
                "object_name": name,
                "expected_state_sha256": candidates[name]["state_sha256"],
                "replace_in_history": candidates[name][
                    "job_create_replaces_in_history"
                ],
            }
            for name in model_names
        ],
        "template": {
            "kind": "catalog",
            "template_id": template["template_id"],
            "expected_content_sha256": template["content_sha256"],
        },
    }


def _job_resources(document, job) -> tuple:
    return tuple(
        obj
        for obj in document.Objects
        if str(getattr(obj, "VibeCADTimelineRole", "") or "") == "resource"
        and getattr(obj, "VibeCADTimelineOwner", None) is job
    )


def _assert_job_graph(document, job, sources, replacements) -> tuple:
    assert job.VibeCADTreeRole == "manufacture_setup"
    resources = _job_resources(document, job)
    assert resources
    assert tuple(job.VibeCADTimelineReplacedInputs) == tuple(replacements)
    assert all(not source.ViewObject.Visibility for source in sources)
    assert len(job.Model.Group) == len(sources)
    assert tuple(job.Proxy.baseObject(job, clone) for clone in job.Model.Group) == (
        *sources,
    )
    assert len(job.Tools.Group) >= 1
    assert job.Stock is not None
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None
    operations = tuple(timeline.Operations)
    job_index = operations.index(job)
    assert set(operations[job_index - len(resources) : job_index]) == set(resources)
    return resources


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    template_preferences = None
    prior_default_template = ""
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-job-")
        save_path = Path(temporary.name) / "native-manufacture-job.FCStd"
        template_path = Path(temporary.name) / "job_native_gate.json"
        template_path.write_text(
            json.dumps(
                {
                    "Version": 1,
                    "Desc": "Catalog-authenticated Native Job",
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        template_preferences = CamPreferences.preferences()
        prior_default_template = template_preferences.GetString(
            CamPreferences.DefaultJobTemplate,
            "",
        )
        template_preferences.SetString(
            CamPreferences.DefaultJobTemplate,
            str(template_path),
        )
        document = App.newDocument("NativeManufactureJobGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        assert (
            plans["CAM_Job"].capability_family,
            plans["CAM_Job"].operation_variant,
            plans["CAM_Job"].exact_target_type,
        ) == (
            MANUFACTURE_JOB_CAPABILITY_NAME,
            "create_job",
            "ExactCurrentCamModelsAndCreationEnvironment",
        )

        visible = _model(document, "VisibleModel", visible=True, x=0.0)
        hidden = _model(document, "HiddenModel", visible=False, x=40.0)
        second_model = _body_model(document, "SecondModel", visible=True, x=80.0)
        initial_names = tuple(obj.Name for obj in document.Objects)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)
        snapshot = build_manufacture_snapshot(document)
        assert snapshot["job_count"] == 0
        assert snapshot["job_creation"]["default_template_id"] is not None
        assert sum(
            1 for item in snapshot["job_creation"]["templates"] if item["is_default"]
        ) == 1
        candidate_names = {item["object_name"] for item in snapshot["model_candidates"]}
        assert candidate_names == {
            visible.Name,
            hidden.Name,
            second_model.Name,
        }, snapshot["model_candidates"]
        assert next(
            item for item in snapshot["model_candidates"] if item["object_name"] == visible.Name
        )["job_create_replaces_in_history"] is True
        assert next(
            item for item in snapshot["model_candidates"] if item["object_name"] == hidden.Name
        )["job_create_replaces_in_history"] is False

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-job-gui")

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
        capture_calls = []
        original_job_capture = ManufactureJobRuntimeModule.capture_job_creation_environment
        original_follow_up_capture = (
            ManufactureFollowUpRuntimeModule.capture_job_creation_environment
        )

        def record_capture():
            capture_calls.append("capture")
            return SimpleNamespace(state_sha256="unused")

        ManufactureJobRuntimeModule.capture_job_creation_environment = record_capture
        ManufactureFollowUpRuntimeModule.capture_job_creation_environment = record_capture
        try:
            shared_bindings = build_native_runtime_bindings(context, ("state.read",))
        finally:
            ManufactureJobRuntimeModule.capture_job_creation_environment = (
                original_job_capture
            )
            ManufactureFollowUpRuntimeModule.capture_job_creation_environment = (
                original_follow_up_capture
            )
        assert tuple(shared_bindings) == ("state.read",)
        assert capture_calls == []
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

        def call(arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                MANUFACTURE_JOB_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"native-manufacture-job-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(visible, "Edge1")
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)
        arguments = _arguments(snapshot)
        second_arguments = _arguments(
            snapshot,
            label="Independent second setup",
            model_names=("SecondModel",),
        )

        template_preferences.SetString(CamPreferences.DefaultJobTemplate, "")
        stale = call(arguments, succeeds=False)
        assert stale["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE", stale
        template_preferences.SetString(
            CamPreferences.DefaultJobTemplate,
            str(template_path),
        )
        assert int(document.UndoCount) == undo_before
        assert tuple(obj.Name for obj in document.Objects) == initial_names

        visible.ViewObject.Visibility = False
        stale_visibility = call(arguments, succeeds=False)
        assert stale_visibility["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        visible.ViewObject.Visibility = True
        assert int(document.UndoCount) == undo_before
        assert tuple(obj.Name for obj in document.Objects) == initial_names

        duplicate = json.loads(json.dumps(arguments))
        duplicate["models"][1] = duplicate["models"][0]
        duplicate_failure = call(duplicate, succeeds=False)
        assert duplicate_failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert int(document.UndoCount) == undo_before

        result = call(arguments)
        _events(12)
        job_name = result["job"]["object_name"]
        job = document.getObject(job_name)
        assert job is not None
        resources = _assert_job_graph(
            document,
            job,
            (visible, hidden),
            (visible,),
        )
        resource_names = tuple(obj.Name for obj in resources)
        created_names = tuple(
            obj.Name for obj in document.Objects if obj.Name not in initial_names
        )
        assert {job_name, *resource_names}.issubset(set(created_names))
        implicit_names = set(created_names) - {job_name, *resource_names}
        assert implicit_names
        expected_implicit = set()
        for resource in resources:
            origin = getattr(resource, "Origin", None)
            if origin is not None:
                expected_implicit.add(origin.Name)
                expected_implicit.update(
                    item.Name for item in tuple(origin.OriginFeatures)
                )
        assert implicit_names == expected_implicit
        assert result["resource_count"] == len(resources)
        assert result["template"] == {
            "kind": "catalog",
            "template_id": arguments["template"]["template_id"],
            "content_sha256": arguments["template"]["expected_content_sha256"],
        }
        assert job.Description == "Catalog-authenticated Native Job"
        assert result["history_replacements"] == [
            {
                "document_uid": str(document.Uid),
                "object_name": visible.Name,
                "type_id": visible.TypeId,
            }
        ]
        assert [item["object_name"] for item in result["receipt"]["created"]] == [
            job_name
        ]
        assert [item["object_name"] for item in result["receipt"]["replaced"]] == [
            visible.Name
        ]
        assert result["assistant_undo_available"] is True
        assert int(document.UndoCount) == undo_before + 1
        assert state_store.current_revision(context.document_uid) == revision_before + 1
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        created_state = job_state(job)

        second_result = call(second_arguments)
        _events(12)
        second_job_name = second_result["job"]["object_name"]
        second_job = document.getObject(second_job_name)
        assert second_job is not None
        second_resources = _assert_job_graph(
            document,
            second_job,
            (second_model,),
            (second_model,),
        )
        second_resource_names = tuple(obj.Name for obj in second_resources)
        second_created_state = job_state(second_job)
        assert job_state(job) == created_state
        assert int(document.UndoCount) == undo_before + 2

        third_arguments = _arguments(
            build_manufacture_snapshot(document),
            label="Independent third setup",
            model_names=("SecondModel",),
        )
        third_result = call(third_arguments)
        _events(12)
        third_job_name = third_result["job"]["object_name"]
        third_job = document.getObject(third_job_name)
        assert third_job is not None
        third_resources = _assert_job_graph(
            document,
            third_job,
            (second_model,),
            (),
        )
        assert third_resources
        assert job_state(job) == created_state
        assert job_state(second_job) == second_created_state
        assert int(document.UndoCount) == undo_before + 3

        document.undo()
        _events(12)
        assert document.getObject(third_job_name) is None
        assert job_state(job) == created_state
        assert job_state(second_job) == second_created_state

        document.undo()
        _events(12)
        assert document.getObject(second_job_name) is None
        assert all(document.getObject(name) is None for name in second_resource_names)
        assert job_state(job) == created_state
        assert second_model.ViewObject.Visibility

        document.undo()
        _events(12)
        assert not any(document.getObject(name) for name in created_names)
        assert visible.ViewObject.Visibility
        assert not hidden.ViewObject.Visibility
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline

        document.redo()
        _events(12)
        document.redo()
        _events(12)
        job = document.getObject(job_name)
        second_job = document.getObject(second_job_name)
        visible = document.getObject("VisibleModel")
        hidden = document.getObject("HiddenModel")
        second_model = document.getObject("SecondModel")
        assert (
            job is not None
            and second_job is not None
            and visible is not None
            and hidden is not None
            and second_model is not None
        )
        _assert_job_graph(document, job, (visible, hidden), (visible,))
        _assert_job_graph(document, second_job, (second_model,), (second_model,))
        assert job_state(job)["state_sha256"] == created_state["state_sha256"]
        assert job_state(second_job)["state_sha256"] == (
            second_created_state["state_sha256"]
        )

        # Exercise additive migration of CAM Jobs saved before the explicit
        # Manufacture tree role existed. The restored proxy must add the role,
        # and the final browser rebuild must not retain its legacy claim tree.
        job.removeProperty("VibeCADTreeRole")
        second_job.removeProperty("VibeCADTreeRole")
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        job = document.getObject(job_name)
        second_job = document.getObject(second_job_name)
        visible = document.getObject("VisibleModel")
        hidden = document.getObject("HiddenModel")
        second_model = document.getObject("SecondModel")
        assert (
            job is not None
            and second_job is not None
            and visible is not None
            and hidden is not None
            and second_model is not None
        )
        reopened_resources = _assert_job_graph(
            document,
            job,
            (visible, hidden),
            (visible,),
        )
        reopened_second_resources = _assert_job_graph(
            document,
            second_job,
            (second_model,),
            (second_model,),
        )
        assert {obj.Name for obj in reopened_resources} == set(resource_names)
        assert {obj.Name for obj in reopened_second_resources} == set(
            second_resource_names
        )
        assert all(document.getObject(name) is not None for name in created_names)
        assert job_state(job)["state_sha256"] == created_state["state_sha256"]
        assert job_state(second_job)["state_sha256"] == (
            second_created_state["state_sha256"]
        )
        assert job.ViewObject.Proxy.__class__.__name__ == "ViewProvider"
        assert job.Description == "Catalog-authenticated Native Job"
        _assert_manufacture_tree(document, (job, second_job))

        print(
            "VIBECAD_NATIVE_MANUFACTURE_JOB_GUI_OK "
            "exact_targets=true replacement=true resource_graph=true multi_setup=true "
            "rollback=true undo=true redo=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if template_preferences is not None:
            template_preferences.SetString(
                CamPreferences.DefaultJobTemplate,
                prior_default_template,
            )
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
