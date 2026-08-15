# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for FEM filtering and Mesh conversion."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import Fem
import FreeCAD as App
import FreeCADGui as Gui
import ObjectsFem
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshOutputSchema import ANALYZE_MESH_OUTPUT_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshOutputState import mesh_filter_state
from VibeCADNativeAnalyzeMeshState import fem_mesh_object_state
from VibeCADNativeAnalyzeResultState import result_state
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 8) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface(main_window):
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(i for i in range(tabs.count()) if str(tabs.tabData(i)) == "FemWorkbench")
    tabs.setCurrentIndex(index)
    _events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "analyze"
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    output = registry.definition(ANALYZE_MESH_OUTPUT_CAPABILITY_NAME)
    assert inspect is not None and output is not None
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(ANALYZE_INSPECT_CAPABILITY_NAME, ANALYZE_MESH_OUTPUT_CAPABILITY_NAME),
            schemas=(
                inspect.provider_schema(("fem_mesh_elements",)),
                output.provider_schema(
                    (
                        "erase_elements",
                        "erase_element_ranges",
                        "convert_surface",
                        "convert_deformed_surface",
                    )
                ),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _two_tetra_mesh():
    mesh = Fem.FemMesh()
    for node_id, point in (
        (1, (0.0, 0.0, 0.0)),
        (2, (1.0, 0.0, 0.0)),
        (3, (0.0, 1.0, 0.0)),
        (4, (0.0, 0.0, 1.0)),
        (5, (0.0, 0.0, -1.0)),
    ):
        mesh.addNode(*point, node_id)
    mesh.addVolume([1, 2, 3, 4], 101)
    mesh.addVolume([1, 3, 2, 5], 102)
    return mesh


def _source(document, name: str, label: str):
    document.openTransaction(f"Create {label}")
    try:
        source = document.addObject("Fem::FemMeshObject", name)
        source.Label = label
        source.FemMesh = _two_tetra_mesh()
        assert document.recompute([source], True, True) is not False
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return source


def _result(document, name: str, label: str, mesh):
    document.openTransaction(f"Create {label}")
    try:
        result = ObjectsFem.makeResultMechanical(document, name)
        result.Label = label
        result.Mesh = mesh
        result.NodeNumbers = [1, 2, 3, 4, 5]
        result.DisplacementVectors = [
            App.Vector(0.0, 0.0, 0.0),
            App.Vector(0.0, 0.0, 0.0),
            App.Vector(0.0, 0.0, 0.0),
            App.Vector(0.0, 0.0, 0.5),
            App.Vector(0.0, 0.0, -0.25),
        ]
        assert document.recompute([result], True, True) is not False
        document.publishProvisionalTimelineOperationBlock(result, (), ())
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return result


def _target(obj) -> dict:
    state = fem_mesh_object_state(obj)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-fem-output-")
        path = Path(temporary.name) / "native-fem-output.FCStd"
        document = App.newDocument("NativeAnalyzeMeshOutputGate")
        document.UndoMode = 1
        document.saveAs(str(path))
        VibeGui._connect_document_observer()
        controller, surface = _surface(Gui.getMainWindow())
        filter_source = _source(document, "FilterSource", "Filter Source")
        convert_source = _source(document, "ConvertSource", "Convert Source")
        deformed_source = _source(document, "DeformedSource", "Deformed Source")
        displacement_result = _result(
            document,
            "DeformationResult",
            "Deformation Result",
            deformed_source,
        )
        mismatched_result = _result(
            document,
            "MismatchedResult",
            "Mismatched Result",
            convert_source,
        )
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-mesh-output-gui")

        def authorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=authorize,
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
            reauthorize_turn=authorize,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def call(tool: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                tool,
                json.dumps(arguments, separators=(",", ":")),
                f"native-fem-output-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            return result

        revision_before_read = state_store.current_revision(str(document.Uid))
        page = call(
            ANALYZE_INSPECT_CAPABILITY_NAME,
            {
                "operation": "fem_mesh_elements",
                "target": _target(filter_source),
                "element_kind": "primary",
                "offset": 0,
                "page_size": 1,
            },
        )
        assert page["element_kind"] == "volume"
        assert page["total"] == 2 and page["next_offset"] == 1
        assert page["elements"][0]["element_id"] == 101
        assert len(page["elements"][0]["node_ids"]) == 4
        assert state_store.current_revision(str(document.Uid)) == revision_before_read

        before_invalid_objects = tuple(document.Objects)
        before_invalid_state = fem_mesh_object_state(filter_source)
        call(
            ANALYZE_MESH_OUTPUT_CAPABILITY_NAME,
            {
                "operation": "erase_elements",
                "target": _target(filter_source),
                "label": "Invalid Filter",
                "element_ids": [999],
            },
            succeeds=False,
        )
        assert tuple(document.Objects) == before_invalid_objects
        assert fem_mesh_object_state(filter_source) == before_invalid_state

        filtered_result = call(
            ANALYZE_MESH_OUTPUT_CAPABILITY_NAME,
            {
                "operation": "erase_element_ranges",
                "target": _target(filter_source),
                "label": "Keep Lower Tetra",
                "element_id_ranges": [{"first_id": 101, "last_id": 101}],
            },
        )
        filter_obj = document.getObject(filtered_result["created_filter"]["object_name"])
        filtered_mesh = document.getObject(filtered_result["result_mesh"]["object_name"])
        assert filter_obj is not None and filtered_mesh is not None
        assert filtered_mesh.FemMesh.Volumes == (102,)
        assert filtered_mesh.FemMesh.NodeCount == 4
        assert tuple(filter_obj.Elements) == (-6, 102)
        assert filter_obj.FemMesh is filtered_mesh
        assert filtered_mesh.VibeCADTimelineOwner is filter_obj
        assert tuple(filter_obj.VibeCADTimelineReplacedInputs) == (filter_source,)

        filter_state_before_undo = mesh_filter_state(filter_obj)
        filter_name = str(filter_obj.Name)
        document.undo()
        assert document.getObject(filter_name) is None
        assert bool(filter_source.ViewObject.Visibility)
        document.redo()
        filter_obj = document.getObject(filter_state_before_undo["object_name"])
        assert mesh_filter_state(filter_obj)["state_sha256"] == filter_state_before_undo[
            "state_sha256"
        ]

        converted_result = call(
            ANALYZE_MESH_OUTPUT_CAPABILITY_NAME,
            {
                "operation": "convert_surface",
                "target": _target(convert_source),
                "label": "Converted Exterior",
            },
        )
        converted = document.getObject(converted_result["created_mesh"]["object_name"])
        assert converted is not None
        converted_state = mesh_object_state(converted)
        assert converted_state["topology"]["facets"] == 6
        assert tuple(converted.VibeCADTimelineReplacedInputs) == (convert_source,)
        assert converted.FemSource is convert_source
        assert converted.FemResultSource is None
        assert str(converted.ConversionMode) == "undeformed"

        before_mismatch_objects = tuple(document.Objects)
        mismatch = call(
            ANALYZE_MESH_OUTPUT_CAPABILITY_NAME,
            {
                "operation": "convert_deformed_surface",
                "target": _target(deformed_source),
                "result": {
                    "object_name": mismatched_result.Name,
                    "expected_state_sha256": result_state(
                        mismatched_result, include_ranges=False
                    )["state_sha256"],
                },
                "label": "Invalid Deformed Exterior",
            },
            succeeds=False,
        )
        assert mismatch["error_code"] == "NATIVE_ANALYZE_RESULT_MESH_MISMATCH"
        assert tuple(document.Objects) == before_mismatch_objects

        deformed_result = call(
            ANALYZE_MESH_OUTPUT_CAPABILITY_NAME,
            {
                "operation": "convert_deformed_surface",
                "target": _target(deformed_source),
                "result": {
                    "object_name": displacement_result.Name,
                    "expected_state_sha256": result_state(
                        displacement_result, include_ranges=False
                    )["state_sha256"],
                },
                "label": "Result-Deformed Exterior",
            },
        )
        deformed = document.getObject(deformed_result["created_mesh"]["object_name"])
        assert deformed_result["conversion"] == {
            "mode": "result_deformed",
            "exterior_face_count": 6,
            "mesh_facet_count": 6,
        }
        assert deformed_result["source_fem_result"]["surface_node_count"] == 5
        deformed_state = mesh_object_state(deformed)
        assert deformed_state["bounds"]["minimum_mm"][2] == -1.25
        assert deformed_state["bounds"]["maximum_mm"][2] == 1.5
        assert deformed.FemSource is deformed_source
        assert deformed.FemResultSource is displacement_result
        assert str(deformed.ConversionMode) == "result_deformed"
        assert float(deformed.DisplacementScale) == 1.0
        assert tuple(deformed.VibeCADTimelineReplacedInputs) == (deformed_source,)

        deformed_name = str(deformed.Name)
        document.undo()
        assert document.getObject(deformed_name) is None
        assert bool(deformed_source.ViewObject.Visibility)
        document.redo()
        deformed = document.getObject(deformed_name)
        assert mesh_object_state(deformed)["state_sha256"] == deformed_state["state_sha256"]

        expected = {
            "filter": mesh_filter_state(filter_obj),
            "filtered_mesh": fem_mesh_object_state(filter_obj.FemMesh),
            "converted": converted_state,
            "deformed": deformed_state,
        }
        deformed_source_name = str(deformed_source.Name)
        displacement_result_name = str(displacement_result.Name)
        document.recompute()
        document.save()
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(path))
        Gui.activeDocument().activeView().viewAxonometric()
        _events(20)
        reopened_filter = document.getObject(expected["filter"]["object_name"])
        reopened_filtered = document.getObject(expected["filtered_mesh"]["object_name"])
        reopened_converted = document.getObject(expected["converted"]["object_name"])
        reopened_deformed = document.getObject(expected["deformed"]["object_name"])
        assert mesh_filter_state(reopened_filter)["state_sha256"] == expected["filter"][
            "state_sha256"
        ]
        assert fem_mesh_object_state(reopened_filtered)["state_sha256"] == expected[
            "filtered_mesh"
        ]["state_sha256"]
        assert mesh_object_state(reopened_converted)["state_sha256"] == expected["converted"][
            "state_sha256"
        ]
        assert mesh_object_state(reopened_deformed)["state_sha256"] == expected["deformed"][
            "state_sha256"
        ]
        assert reopened_deformed.FemSource.Name == deformed_source_name
        assert reopened_deformed.FemResultSource.Name == displacement_result_name
        assert str(reopened_deformed.ConversionMode) == "result_deformed"
        print(
            "VIBECAD_NATIVE_ANALYZE_MESH_OUTPUT_GUI_OK "
            "actions=2 variants=4 inspect=true exact_ids=true ranges=true atomic_rejection=true "
            "filtered_resource=true conversion_facets=6 result_deformed=true "
            "result_mesh_match=true displacement_range=true provenance=true history=true "
            "undo_redo=true reopen=true read_revision_stable=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None:
            try:
                App.closeDocument(document.Name)
            except Exception:
                pass
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
