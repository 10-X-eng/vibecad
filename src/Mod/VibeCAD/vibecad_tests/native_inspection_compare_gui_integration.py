# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI gate for background Visual Inspection comparison."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Mesh
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeBackground import NativeBackgroundManager
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeInspectionCompareSchema import INSPECTION_COMPARE_CAPABILITY_NAME
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from VibeCADInspectionComparisonGui import start_visual_inspection


def _events(rounds: int = 8) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _mesh_surface():
    window = Gui.getMainWindow()
    controller = window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        value
        for value in range(tabs.count())
        if str(tabs.tabData(value)) == "MeshWorkbench"
    )
    tabs.setCurrentIndex(index)
    _events(16)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "mesh"
    return controller, surface


def _sources(document):
    document.openTransaction("Create comparison sources")
    try:
        nominal = document.addObject("Part::Feature", "NominalSurface")
        nominal.Label = "Nominal Surface"
        nominal.Shape = Part.makePlane(10.0, 10.0)
        actual = document.addObject("Mesh::Feature", "ActualScan")
        actual.Label = "Actual Scan"
        actual.Mesh = Mesh.Mesh(
            [
                ((0.0, 0.0, 0.1), (10.0, 0.0, 0.1), (10.0, 10.0, 0.1)),
                ((0.0, 0.0, 0.1), (10.0, 10.0, 0.1), (0.0, 10.0, 0.1)),
            ]
        )
        assert document.recompute([nominal, actual], True, True) is not False
        document.publishProvisionalTimelineOperationBlock(nominal, (), ())
        document.publishProvisionalTimelineOperationBlock(actual, (), ())
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return actual, nominal


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("MeshWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-inspection-compare-")
        path = Path(temporary.name) / "inspection-compare.FCStd"
        document = App.newDocument("InspectionCompareGate")
        document.UndoMode = 1
        document.saveAs(str(path))
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        controller, surface = _mesh_surface()
        actual, nominal = _sources(document)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        provider = resolve_native_provider_surface(surface, registry)
        assert provider.available, provider.unavailable_reason
        assert INSPECTION_COMPARE_CAPABILITY_NAME in provider.tool_names
        schema = next(
            value for value in provider.schemas
            if value["name"] == INSPECTION_COMPARE_CAPABILITY_NAME
        )
        branch = schema["parameters"]["oneOf"][0]
        assert branch["required"] == [
            "actual",
            "nominals",
            "search_radius_mm",
            "tolerance_mm",
        ]
        assert "operation" not in branch["properties"]
        turn = NativeTurnSnapshot.from_provider_surface(provider)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-inspection-compare-gui")
        diagnostics = []

        def diagnose(job_id, exc):
            diagnostics.append("".join(traceback.format_exception(exc)))
            return f"inspection-compare-{job_id}"

        background = NativeBackgroundManager(diagnostic_sink=diagnose)

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
            background_manager=background,
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
        )
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        result = dispatcher.call(
            INSPECTION_COMPARE_CAPABILITY_NAME,
            json.dumps(
                {
                    "actual": {"object_name": actual.Name},
                    "nominals": [{"object_name": nominal.Name}],
                    "search_radius_mm": 1.0,
                    "tolerance_mm": 0.2,
                    "result_label": "Box Deviation",
                },
                separators=(",", ":"),
            ),
            "native-inspection-compare-1",
        )
        assert result.get("ok") is True, result
        job = result.get("job")
        assert isinstance(job, dict) and job.get("job_id"), result
        deadline = time.monotonic() + 180.0
        while time.monotonic() < deadline:
            _events(2)
            snapshot = background.snapshot(job["job_id"])
            if snapshot.terminal:
                break
        else:
            raise AssertionError(f"Inspection comparison did not finish: {job}")
        assert snapshot.phase == "completed", (snapshot, diagnostics)
        verified = dict(snapshot.result or {})
        assert verified["representation"] == "inspection_result"
        assert verified["summary"]["measured_count"] > 0, verified
        feature = document.getObject(verified["result"]["object_name"])
        group = document.getObject(verified["group"]["object_name"])
        assert feature is not None and feature.TypeId == "Inspection::Feature"
        assert group is not None and group.TypeId == "Inspection::Group"
        assert feature.isFrozen() and len(feature.Distances) == actual.Mesh.CountPoints
        assert feature.Actual is actual and tuple(feature.Nominals) == (nominal,)
        assert not actual.Visibility and not nominal.Visibility
        assert document.recompute() is not False
        assert len(feature.Distances) == actual.Mesh.CountPoints
        document.save()

        feature_name = feature.Name
        group_name = group.Name
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        restored = document.getObject(feature_name)
        restored_group = document.getObject(group_name)
        assert restored is not None and restored.isFrozen()
        assert restored_group is not None and tuple(restored_group.Group) == (restored,)
        assert len(restored.Distances) > 0

        actual = document.getObject("ActualScan")
        nominal = document.getObject("NominalSurface")
        document.openTransaction("Create second comparison source")
        try:
            second = document.addObject("Mesh::Feature", "SecondActualScan")
            second.Label = "Second Actual Scan"
            second.Mesh = Mesh.Mesh(
                [
                    ((0.0, 0.0, 0.15), (10.0, 0.0, 0.15), (10.0, 10.0, 0.15)),
                    ((0.0, 0.0, 0.15), (10.0, 10.0, 0.15), (0.0, 10.0, 0.15)),
                ]
            )
            assert document.recompute([second], True, True) is not False
            document.publishProvisionalTimelineOperationBlock(second, (), ())
            document.commitTransaction()
        except Exception:
            document.abortTransaction()
            raise
        human_job = start_visual_inspection(
            [actual, second],
            [nominal],
            1.0,
            0.2,
        )
        deadline = time.monotonic() + 180.0
        while time.monotonic() < deadline:
            _events(2)
            human_snapshot = service.native_background_manager().snapshot(human_job)
            if human_snapshot.terminal:
                break
        else:
            raise AssertionError(f"Human Visual Inspection did not finish: {human_job}")
        assert human_snapshot.phase == "completed", human_snapshot
        human_result = dict(human_snapshot.result or {})
        assert len(human_result["comparisons"]) == 2
        human_group = document.getObject(human_result["group"]["object_name"])
        assert human_group is not None and len(human_group.Group) == 2
        assert all(feature.isFrozen() for feature in human_group.Group)
        document.save()
        human_group_name = human_group.Name
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        restored = document.getObject(feature_name)
        restored_human_group = document.getObject(human_group_name)
        assert restored is not None
        assert restored_human_group is not None and len(restored_human_group.Group) == 2
        print(
            json.dumps(
                {
                    "ok": True,
                    "samples": len(restored.Distances),
                    "measured": int(restored.VibeCADMeasuredCount),
                    "passed": bool(restored.VibeCADPassed),
                    "human_comparisons": len(restored_human_group.Group),
                },
                sort_keys=True,
            )
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if document is not None:
            try:
                App.closeDocument(document.Name)
            except Exception:
                pass
        if temporary is not None:
            temporary.cleanup()
        QtCore.QTimer.singleShot(0, lambda: application.exit(exit_code))


QtCore.QTimer.singleShot(0, _run)
