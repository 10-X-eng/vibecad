# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD gate for common Native read, view, save, and undo behavior."""

from __future__ import annotations

import math
import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGrid
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeCommonBindings import common_runtime_bindings
from VibeCADNativeCommonRuntime import NativeCommonRuntime
from VibeCADNativeCommonSchema import common_capability_definitions
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationRunner
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeObjectIdentity
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger, NativeUndoError
from VibeCADNativeView import set_grid_visible


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _edge_direction(edge) -> tuple[float, float, float]:
    parameter = 0.5 * (float(edge.FirstParameter) + float(edge.LastParameter))
    vector = edge.tangentAt(parameter)
    length = math.sqrt(vector.x**2 + vector.y**2 + vector.z**2)
    return vector.x / length, vector.y / length, vector.z / length


def _perpendicular_edge_names(shape) -> tuple[str, str]:
    edges = list(shape.Edges)
    for first_index, first in enumerate(edges):
        first_direction = _edge_direction(first)
        for second_index, second in enumerate(edges[first_index + 1 :], first_index + 1):
            second_direction = _edge_direction(second)
            dot = abs(sum(a * b for a, b in zip(first_direction, second_direction)))
            if dot < 1.0e-8:
                return f"Edge{first_index + 1}", f"Edge{second_index + 1}"
    raise AssertionError("Box did not expose perpendicular edges")


def _circular_edge_name(shape) -> str:
    for index, edge in enumerate(shape.Edges, 1):
        if abs(float(getattr(getattr(edge, "Curve", None), "Radius", 0.0)) - 5.0) < 1.0e-8:
            return f"Edge{index}"
    raise AssertionError("Cylinder did not expose a circular edge")


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    grid_was_visible = False
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeCommonGate")
        Gui.activateView("Gui::View3DInventor", True)
        VibeGui._connect_document_observer()
        VibeCADGrid.setup()
        _process_events()

        service = get_service()
        service.select_modeling_engine("native")
        state = service._native_document_states
        uid = str(document.Uid)

        box = document.addObject("Part::Feature", "ExactBox")
        box.Shape = Part.makeBox(10.0, 10.0, 10.0)
        cylinder = document.addObject("Part::Feature", "ExactCylinder")
        cylinder.Shape = Part.makeCylinder(5.0, 10.0, App.Vector(30.0, 0.0, 0.0))
        document.recompute()
        _process_events()

        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-common-gui-run")
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
        runtime = NativeCommonRuntime(context=context)
        definitions = common_capability_definitions()
        schemas = tuple(
            definition.provider_schema(
                tuple(variant.operation for variant in definition.variants)
            )
            for definition in definitions
        )
        turn = NativeTurnSnapshot.from_provider_surface(
            NativeProviderSurface(
                snapshot=NativeSurfaceSnapshot(
                    "model",
                    1,
                    "a" * 64,
                    ("VibeCAD_NativeCommonGate",),
                    ("VibeCAD_NativeCommonGate",),
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
        )
        registry = build_native_capability_registry()
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=common_runtime_bindings(runtime),
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def native_call(name, arguments):
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                name,
                json.dumps(arguments, separators=(",", ":")),
                f"gui-call-{call_number}",
            )
            assert result.pop("ok") is True, result
            return result

        active_state = native_call("state.read", {"operation": "active"})
        assert active_state["surface_id"] == "model"
        assert native_call("state.read", {"operation": "selection"})[
            "selected_count"
        ] == 0

        distance = native_call(
            "inspect.query",
            {
                "operation": "distance",
                "first": {"object_name": box.Name, "subelement": "Vertex1"},
                "second": {
                    "object_name": cylinder.Name,
                    "subelement": "Vertex1",
                },
            }
        )
        assert distance["distance_mm"] > 0.0
        first_edge, second_edge = _perpendicular_edge_names(box.Shape)
        angle = native_call(
            "inspect.query",
            {
                "operation": "angle",
                "first": {"object_name": box.Name, "subelement": first_edge},
                "second": {"object_name": box.Name, "subelement": second_edge},
            }
        )
        assert abs(angle["angle_degrees"] - 90.0) < 1.0e-7
        circle = _circular_edge_name(cylinder.Shape)
        circle_target = {"object_name": cylinder.Name, "subelement": circle}
        assert native_call("inspect.query", {"operation": "radius", "target": circle_target})[
            "radius_mm"
        ] == 5.0
        assert native_call("inspect.query", {"operation": "element", "target": circle_target})[
            "shape_type"
        ] == "Edge"
        assert native_call(
            "inspect.query",
            {"operation": "validity", "target": {"object_name": box.Name}},
        )["valid"] is True
        properties = native_call(
            "inspect.query",
            {
                "operation": "mass_properties",
                "targets": [
                    {"object_name": box.Name},
                    {"object_name": cylinder.Name},
                ],
            }
        )
        assert properties["volume_mm3"] > 1000.0
        assert properties["mass_kg"] > 0.001

        presentation_revision = state.current_revision(uid)
        assert native_call("view.control", {"operation": "fit_all"}) == {"fit_all": True}
        assert native_call("view.control", {"operation": "isometric"}) == {
            "orientation": "isometric"
        }
        grid_was_visible = VibeCADGrid.is_grid_visible()
        assert native_call(
            "view.control",
            {"operation": "set_grid", "visible": not grid_was_visible},
        )["grid_visible"] is not grid_was_visible
        assert native_call(
            "view.control",
            {"operation": "set_grid", "visible": grid_was_visible},
        )["grid_visible"] is grid_was_visible
        screenshot = native_call(
            "view.control",
            {
                "operation": "capture_objects",
                "targets": [
                    {"object_name": box.Name},
                    {"object_name": cylinder.Name},
                ],
            }
        )
        assert screenshot["artifact"]["size_bytes"] > 0
        assert Path(screenshot["artifact"]["path"]).is_file()
        assert screenshot["_vibecad_image_attachment"]["path"] == screenshot["artifact"]["path"]
        _process_events()
        assert state.current_revision(uid) == presentation_revision

        save_path = Path(tempfile.mkdtemp(prefix="vibecad-native-save-")) / "common.FCStd"
        document.saveAs(str(save_path))
        saved = native_call("document.save", {"operation": "existing_path"})
        assert saved["file_path"] == str(save_path)
        assert saved["size_bytes"] > 0

        runner = NativeMutationRunner(state)
        checkpoint = ledger.checkpoint(document)
        transaction_name = "Create assistant-owned box"

        def create_owned(target_document):
            feature = target_document.addObject("Part::Feature", "AssistantOwnedBox")
            feature.Shape = Part.makeBox(3.0, 4.0, 5.0)
            identity = NativeObjectIdentity(uid, feature.Name, feature.TypeId)
            return NativeMutationDraft(
                value=feature,
                recompute_targets=(feature,),
                created=(identity,),
            )

        execution = runner.run(
            ticket=state.begin_call(uid, "model.feature"),
            document=document,
            transaction_name=transaction_name,
            reauthorize_turn=lambda: None,
            mutate=create_owned,
            verify=lambda _document, draft: {"object": draft.created[0].summary()},
        )
        assert ledger.record_commit(
            document,
            transaction_name,
            checkpoint,
            execution.receipt,
        )
        undo_execution = native_call(
            "document.undo",
            {"operation": "assistant_local"},
        )
        _process_events()
        assert undo_execution["result"]["undone"]["capability"] == "model.feature"
        assert document.getObject("AssistantOwnedBox") is None

        checkpoint = ledger.checkpoint(document)
        execution = runner.run(
            ticket=state.begin_call(uid, "model.feature"),
            document=document,
            transaction_name=transaction_name,
            reauthorize_turn=lambda: None,
            mutate=create_owned,
            verify=lambda _document, draft: {"object": draft.created[0].summary()},
        )
        assert ledger.record_commit(document, transaction_name, checkpoint, execution.receipt)
        document.openTransaction("Human edit")
        human = document.addObject("Part::Feature", "HumanBox")
        human.Shape = Part.makeBox(2.0, 2.0, 2.0)
        document.commitTransaction()
        _process_events()
        try:
            ledger.undo_latest(
                ticket=state.begin_call(uid, "document.undo"),
                document=document,
                state=state,
                reauthorize_turn=lambda: None,
                active_document=lambda: App.ActiveDocument,
            )
        except NativeUndoError:
            pass
        else:
            raise AssertionError("Native undo touched unrelated human history")
        assert document.getObject("HumanBox") is not None
        assert document.UndoNames[0] == "Human edit"

        print("VIBECAD_NATIVE_COMMON_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        try:
            set_grid_visible(document, grid_was_visible) if document is not None else None
        except Exception:
            pass
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
