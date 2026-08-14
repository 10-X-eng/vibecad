# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Drawing image and PAT hatches."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets
import TechDrawGui

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDimensionSupport import drawing_selection_state
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingHatch import drawing_hatch_defaults_state
from VibeCADNativeDrawingHatchSchema import (
    DRAWING_HATCH_CAPABILITY_NAME,
    DRAWING_HATCH_OPERATIONS,
)
from VibeCADNativeDrawingHatchState import (
    drawing_hatch_inventory_state,
    drawing_hatch_state,
)
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeInput import authorize_native_input_path
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface():
    Gui.activateWorkbench("TechDrawWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject, "VibeCADRibbonController"
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "drawing"
    return controller, surface


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _page_image_sha256() -> str:
    mdi = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    assert mdi is not None and mdi.activeSubWindow() is not None
    image = mdi.activeSubWindow().grab().toImage()
    data = QtCore.QByteArray()
    buffer = QtCore.QBuffer(data)
    assert buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    return hashlib.sha256(bytes(data)).hexdigest()


def _create_fixture(document):
    document.openTransaction("Create Drawing hatch fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        shapes = tuple(
            Part.makeBox(
                20.0,
                14.0,
                6.0,
                App.Vector(-75.0 + 30.0 * index, -7.0, 0.0),
            )
            for index in range(6)
        )
        source = document.addObject("Part::Feature", "HatchSource")
        source.Label = "Hatch Source"
        source.Shape = Part.makeCompound(shapes)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        page = document.addObject("TechDraw::DrawPage", "HatchPage")
        page.Label = "Hatch Page"
        template = document.addObject("TechDraw::DrawSVGTemplate", "HatchTemplate")
        template.Template = str(
            Path(App.getResourceDir())
            / "Mod"
            / "TechDraw"
            / "Templates"
            / "ISO"
            / "A4_Landscape_TD.svg"
        )
        page.Template = template
        document.publishProvisionalTimelineOperationBlock(page, (template,), ())
        view = document.addObject("TechDraw::DrawViewPart", "HatchView")
        view.Label = "Hatch View"
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.0
        view.X = 105.0
        view.Y = 80.0
        view.CoarseView = False
        document.publishProvisionalTimelineOperationBlock(view, (), ())
        assert int(page.addView(view)) >= 1
        assert document.recompute([source, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(28)
    geometry = drawing_projected_geometry_state(view)
    faces = [
        item["name"] for item in geometry["elements"] if item["element_type"] == "face"
    ]
    assert len(faces) >= 6, faces
    return source, page, view, tuple(faces[:6])


def _human_hatch(command: str, view, face: str):
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(view, face)
    Gui.runCommand(command)
    _events(16)
    assert Gui.Control.activeDialog()
    task = Gui.Control.activeTaskDialog()
    assert task is not None
    task.accept()
    _events(20)
    assert not Gui.Control.activeDialog()
    inventory = drawing_hatch_inventory_state(view)
    assert inventory["hatch_count"] >= 1
    return view.Document.getObject(inventory["hatches"][-1]["object_name"])


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_HATCH_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(DRAWING_HATCH_OPERATIONS)
    branches = schema["parameters"]["oneOf"]
    assert [branch["properties"]["operation"]["const"] for branch in branches] == list(
        DRAWING_HATCH_OPERATIONS
    )
    by_operation = {
        branch["properties"]["operation"]["const"]: branch for branch in branches
    }
    assert by_operation["create_image_default"]["required"] == [
        "operation",
        "page",
        "view",
        "faces",
        "label",
        "style",
    ]
    assert "pattern_name" in by_operation["create_geometric_default"]["required"]
    assert by_operation["read_defaults"]["required"] == ["operation"]
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "pattern_file" not in encoded
    assert '"path":' not in encoded.casefold()
    assert '"file_path":' not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 12 * 1024
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_HATCH_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _arguments(page, view, faces: tuple[str, ...], operation: str, defaults) -> dict:
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    geometry = drawing_projected_geometry_state(view)
    by_name = {item["name"]: item for item in geometry["elements"]}
    kind = "image" if operation.startswith("create_image") else "geometric"
    style = json.loads(json.dumps(defaults[kind]["default_style"]))
    result = {
        "operation": operation,
        "page": {
            "object_name": page.Name,
            "expected_state_sha256": page_state["state_sha256"],
        },
        "view": {
            "object_name": view.Name,
            "expected_state_sha256": view_state["state_sha256"],
            "expected_projection_state_sha256": geometry[
                "projection_state_sha256"
            ],
        },
        "faces": [
            {
                "subelement": face,
                "expected_element_state_sha256": by_name[face][
                    "element_state_sha256"
                ],
            }
            for face in faces
        ],
        "label": f"Native {kind.title()} Hatch",
        "style": style,
    }
    if kind == "geometric":
        result["pattern_name"] = defaults["geometric"][
            "default_pattern_name"
        ]
    return result


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-drawing-hatch-")
        temporary_path = Path(temporary.name)
        save_path = temporary_path / "drawing-hatch.FCStd"
        custom_image_path = temporary_path / "custom-pattern.svg"
        custom_pat_path = temporary_path / "custom-pattern.pat"
        shutil.copyfile(
            Path(App.getResourceDir()) / "Mod" / "TechDraw" / "Patterns" / "simple.svg",
            custom_image_path,
        )
        shutil.copyfile(
            Path(App.getResourceDir()) / "Mod" / "TechDraw" / "PAT" / "FCPAT.pat",
            custom_pat_path,
        )
        controller, surface = _surface()
        plans = {
            item.command_id: item
            for item in resolve_native_action_inventory(surface).plans
            if item.command_id in {"TechDraw_Hatch", "TechDraw_GeometricHatch"}
        }
        assert (
            plans["TechDraw_Hatch"].capability_family,
            plans["TechDraw_Hatch"].operation_variant,
            plans["TechDraw_Hatch"].exact_target_type,
        ) == (
            DRAWING_HATCH_CAPABILITY_NAME,
            "create_image_default",
            "ExactDrawingProjectedFacesAndImageHatchStyle",
        )
        assert (
            plans["TechDraw_GeometricHatch"].capability_family,
            plans["TechDraw_GeometricHatch"].operation_variant,
            plans["TechDraw_GeometricHatch"].exact_target_type,
        ) == (
            DRAWING_HATCH_CAPABILITY_NAME,
            "create_geometric_default",
            "ExactDrawingProjectedFacesAndGeometricHatchStyle",
        )

        document = App.newDocument("NativeDrawingHatchGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view, faces = _create_fixture(document)
        initial_image = _page_image_sha256()

        human_image = _human_hatch("TechDraw_Hatch", view, faces[0])
        human_image_state = drawing_hatch_state(human_image)
        assert human_image_state["kind"] == "image"
        assert human_image_state["faces"] == [faces[0]]
        image_after_human = _page_image_sha256()
        assert image_after_human != initial_image

        human_geometric = _human_hatch("TechDraw_GeometricHatch", view, faces[1])
        human_geometric_state = drawing_hatch_state(human_geometric)
        assert human_geometric_state["kind"] == "geometric"
        assert human_geometric_state["faces"] == [faces[1]]
        assert _page_image_sha256() != image_after_human
        history = tuple(document.VibeCADTimeline.Operations)
        assert human_image in history and human_geometric in history

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-hatch-gui")
        authorized_paths: list[Path | None] = []

        def authorize_input(request):
            assert authorized_paths
            selected = authorized_paths.pop(0)
            return None if selected is None else authorize_native_input_path(request, selected)

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
            authorize_input=authorize_input,
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

        def call(arguments: dict, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                DRAWING_HATCH_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-hatch-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(view, faces[5])
        selection_before = _selection()
        visibility_before = tuple(
            bool(item.ViewObject.Visibility) for item in (source, page, view)
        )

        revision_before_defaults = state_store.current_revision(str(document.Uid))
        defaults_response = call({"operation": "read_defaults"})
        defaults = {
            "image": defaults_response["image"],
            "geometric": defaults_response["geometric"],
        }
        assert defaults_response["defaults_state_sha256"] == drawing_hatch_defaults_state()[
            "defaults_state_sha256"
        ]
        defaults_json = json.dumps(defaults_response, separators=(",", ":"))
        assert str(Path(App.getResourceDir())) not in defaults_json
        assert state_store.current_revision(str(document.Uid)) == revision_before_defaults

        native_image_arguments = _arguments(
            page, view, (faces[2],), "create_image_default", defaults
        )
        image_before_native = _page_image_sha256()
        native_image_response = call(native_image_arguments)
        _events(20)
        image_after_native = _page_image_sha256()
        assert image_after_native != image_before_native
        native_image_name = native_image_response["hatch"]["object_name"]
        assert native_image_response["hatch"]["kind"] == "image"
        assert native_image_response["source_kind"] == "configured_default"
        assert not Gui.Control.activeDialog()

        duplicate = call(
            _arguments(page, view, (faces[2],), "create_image_default", defaults),
            False,
        )
        assert duplicate["error_code"] == "NATIVE_DRAWING_HATCH_FACE_CONFLICT"
        assert duplicate["repair"]["conflicting_hatches"][0]["faces"] == [faces[2]]

        stale_arguments = _arguments(
            page, view, (faces[3],), "create_geometric_default", defaults
        )
        stale_arguments["faces"][0]["expected_element_state_sha256"] = "0" * 64
        stale = call(stale_arguments, False)
        assert stale["error_code"] == "NATIVE_DRAWING_HATCH_REFERENCE_STALE"

        wrong_type = _arguments(
            page, view, (faces[3],), "create_geometric_default", defaults
        )
        wrong_type["view"]["object_name"] = source.Name
        rejected = call(wrong_type, False)
        assert rejected["error_code"] == "NATIVE_TARGET_INVALID"
        assert rejected["accepted_types"] == ["TechDraw::DrawViewPart"]

        invalid_pattern = _arguments(
            page, view, (faces[3],), "create_geometric_default", defaults
        )
        invalid_pattern["pattern_name"] = "NotARealPattern"
        rejected = call(invalid_pattern, False)
        assert rejected["error_code"] == "NATIVE_DRAWING_HATCH_PATTERN_INVALID"
        assert defaults["geometric"]["default_pattern_name"] in rejected["repair"][
            "available_pattern_names"
        ]

        geometric_before_native = _page_image_sha256()
        native_geometric_response = call(
            _arguments(
                page, view, (faces[3],), "create_geometric_default", defaults
            )
        )
        _events(20)
        assert _page_image_sha256() != geometric_before_native
        native_geometric_name = native_geometric_response["hatch"]["object_name"]
        assert native_geometric_response["hatch"]["kind"] == "geometric"

        objects_before_cancel = tuple(document.Objects)
        history_before_cancel = tuple(document.VibeCADTimeline.Operations)
        authorized_paths.append(None)
        cancelled = call(
            _arguments(page, view, (faces[4],), "create_image_file", defaults),
            False,
        )
        assert cancelled["error_code"] == "NATIVE_DRAWING_HATCH_INPUT_CANCELLED"
        assert tuple(document.Objects) == objects_before_cancel
        assert tuple(document.VibeCADTimeline.Operations) == history_before_cancel

        authorized_paths.append(custom_image_path)
        custom_image_response = call(
            _arguments(page, view, (faces[4],), "create_image_file", defaults)
        )
        custom_image_name = custom_image_response["hatch"]["object_name"]
        assert custom_image_response["source_kind"] == "human_authorized"
        assert custom_image_response["hatch"]["pattern"]["file_name"] == custom_image_path.name

        objects_before_rollback = tuple(document.Objects)
        history_before_rollback = tuple(document.VibeCADTimeline.Operations)
        original_create = TechDrawGui.createDrawingGeometricHatch

        def fail_after_create(*args):
            original_create(*args)
            raise RuntimeError("Injected hatch creation failure")

        authorized_paths.append(custom_pat_path)
        TechDrawGui.createDrawingGeometricHatch = fail_after_create
        try:
            rolled_back = call(
                _arguments(
                    page, view, (faces[5],), "create_geometric_file", defaults
                ),
                False,
            )
        finally:
            TechDrawGui.createDrawingGeometricHatch = original_create
        assert (
            rolled_back["error_code"] == "NATIVE_DRAWING_HATCH_CREATE_FAILED"
        ), rolled_back
        assert tuple(document.Objects) == objects_before_rollback
        assert tuple(document.VibeCADTimeline.Operations) == history_before_rollback

        authorized_paths.append(custom_pat_path)
        custom_geometric_response = call(
            _arguments(
                page, view, (faces[5],), "create_geometric_file", defaults
            )
        )
        custom_geometric_name = custom_geometric_response["hatch"]["object_name"]
        assert custom_geometric_response["source_kind"] == "human_authorized"
        assert custom_geometric_response["hatch"]["pattern"]["file_name"] == custom_pat_path.name

        expected_names = {
            human_image.Name,
            human_geometric.Name,
            native_image_name,
            native_geometric_name,
            custom_image_name,
            custom_geometric_name,
        }
        inventory = drawing_hatch_inventory_state(view)
        assert {item["object_name"] for item in inventory["hatches"]} == expected_names
        claimed_names = {item.Name for item in view.ViewObject.claimChildren()}
        assert expected_names <= claimed_names
        assert expected_names <= {
            item.Name for item in document.VibeCADTimeline.Operations
        }
        snapshot = build_drawing_snapshot(
            document, selection=drawing_selection_state(document)
        )
        view_snapshot = snapshot["pages"][0]["views"][0]
        assert view_snapshot["hatches"]["hatch_count"] == 6
        assert snapshot["hatch_defaults"]["defaults_state_sha256"] == defaults_response[
            "defaults_state_sha256"
        ]
        assert _selection() == selection_before
        assert tuple(
            bool(item.ViewObject.Visibility) for item in (source, page, view)
        ) == visibility_before
        assert not authorized_paths
        assert not Gui.Control.activeDialog()
        assert len(json.dumps(custom_geometric_response, separators=(",", ":")).encode()) < 4096

        document.undo()
        _events(16)
        assert document.getObject(custom_geometric_name) is None
        document.redo()
        _events(20)
        assert drawing_hatch_state(document.getObject(custom_geometric_name))["valid"]

        names = {
            "page": page.Name,
            "view": view.Name,
            "hatches": tuple(sorted(expected_names)),
        }
        document.saveAs(str(save_path))
        custom_image_path.unlink()
        custom_pat_path.unlink()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        page = document.getObject(names["page"])
        view = document.getObject(names["view"])
        assert page is not None and view is not None
        page.ViewObject.show()
        assert document.recompute([view, page], True, True) is not False
        _events(28)
        reopened = drawing_hatch_inventory_state(view)
        assert tuple(sorted(item["object_name"] for item in reopened["hatches"])) == names[
            "hatches"
        ]
        assert all(item["valid"] for item in reopened["hatches"])
        assert {
            item["pattern"]["file_name"] for item in reopened["hatches"]
        } >= {"custom-pattern.svg", "custom-pattern.pat"}
        assert set(names["hatches"]) <= {
            item.Name for item in view.ViewObject.claimChildren()
        }

        print(
            "VIBECAD_NATIVE_DRAWING_HATCH_GUI_OK operations=5 image=true "
            "geometric=true human_oracle=true shared_host_builder=true "
            "exact_faces=true explicit_style=true defaults=true catalog=true "
            "human_authorized_files=true path_free=true artifact_hash=true "
            "embedded_reopen=true visual_hash=true tree=true history=true "
            "snapshot=true stale=true wrong_type=true duplicate_refusal=true "
            "invalid_pattern=true cancellation=true rollback=true undo=true "
            "redo=true selection=true visibility=true closed_schema=true "
            "low_noise=true native_no_task=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
