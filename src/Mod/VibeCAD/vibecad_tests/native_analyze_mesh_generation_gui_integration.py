# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI background lifecycle gate for Native FEM mesh generation."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAnalyzeMeshRefinementSchema import ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
from VibeCADNativeAnalyzeMeshSchema import ANALYZE_MESH_CAPABILITY_NAME
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
from VibeCADNativeAnalyzeModelSchema import ANALYZE_MODEL_CAPABILITY_NAME
from VibeCADNativeAnalyzeState import analysis_state
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
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
    model = registry.definition(ANALYZE_MODEL_CAPABILITY_NAME)
    mesh = registry.definition(ANALYZE_MESH_CAPABILITY_NAME)
    refinement = registry.definition(ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME)
    jobs = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    assert all(item is not None for item in (model, mesh, refinement, jobs))
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                ANALYZE_MODEL_CAPABILITY_NAME,
                ANALYZE_MESH_CAPABILITY_NAME,
                ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME,
                NATIVE_BACKGROUND_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(("create_analysis",)),
                mesh.provider_schema(
                    ("create_gmsh", "create_netgen", "generate_gmsh", "generate_netgen")
                ),
                refinement.provider_schema(("create_region",)),
                jobs.provider_schema(("status", "cancel")),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _analysis_target(state: dict) -> dict:
    return {**_target(state), "expected_member_count": state["member_count"]}


def _refs(source, *subelements: str) -> list[dict]:
    return [
        {
            "object_name": source.Name,
            "expected_state_sha256": mesh_object_state(source)["state_sha256"],
            "subelements": list(subelements),
        }
    ]


def _source(document, name: str, x: float):
    document.openTransaction(f"Create {name}")
    try:
        source = document.addObject("Part::Box", name)
        source.Length = 10.0
        source.Width = 8.0
        source.Height = 6.0
        source.Placement.Base.x = x
        assert document.recompute([source], True, True) is not False
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return source


def _write_fake_gmsh(path: Path, fixture: Path) -> None:
    path.write_text(
        "#!/usr/bin/python3\n"
        "import pathlib, re, shutil, sys, time\n"
        "time.sleep(0.35)\n"
        "model = pathlib.Path(sys.argv[-1])\n"
        "text = model.read_text(encoding='utf-8')\n"
        "match = re.search(r'Save \\\"([^\\\"]+)\\\"', text)\n"
        "assert match is not None\n"
        "output = (model.parent / match.group(1)).resolve()\n"
        f"shutil.copyfile({str(fixture)!r}, output)\n",
        encoding="utf-8",
    )
    path.chmod(0o700)


def _write_fake_netgen(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/python3\n"
        "import pathlib, re, sys, time\n"
        "import numpy as np\n"
        "time.sleep(0.35)\n"
        "model = pathlib.Path(sys.argv[-1])\n"
        "text = model.read_text(encoding='utf-8')\n"
        "match = re.search(r\"'result_file': '([^']+)'\", text)\n"
        "assert match is not None\n"
        "result = {'coords': np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]]), "
        "'Edges': [[], []], 'Faces': [[], []], 'Volumes': [[1,2,3,4], [4]]}\n"
        "groups = {'Edges': [], 'Faces': [], 'Solids': []}\n"
        "np.save(match.group(1), np.array([result, groups], dtype=object))\n",
        encoding="utf-8",
    )
    path.chmod(0o700)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    gmsh_preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh")
    netgen_preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Netgen")
    old_gmsh = gmsh_preferences.GetString("gmshBinaryPath", "")
    old_netgen = netgen_preferences.GetString("NetgenPythonPath", "")
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-fem-generation-")
        root = Path(temporary.name)
        path = root / "native-fem-generation.FCStd"
        fixture = Path(App.getHomePath()) / "Mod/Fem/femtest/data/gmsh/Cube_Volume.vtk"
        assert fixture.is_file(), fixture
        fake_gmsh = root / "fake-gmsh"
        fake_netgen = root / "fake-netgen"
        _write_fake_gmsh(fake_gmsh, fixture)
        _write_fake_netgen(fake_netgen)
        gmsh_preferences.SetString("gmshBinaryPath", str(fake_gmsh))
        netgen_preferences.SetString("NetgenPythonPath", str(fake_netgen))

        document = App.newDocument("NativeAnalyzeMeshGenerationGate")
        document.UndoMode = 1
        document.saveAs(str(path))
        VibeGui._connect_document_observer()
        controller, surface = _surface(Gui.getMainWindow())
        gmsh_source = _source(document, "GmshSource", 0.0)
        netgen_source = _source(document, "NetgenSource", 20.0)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-mesh-generation-gui")

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
            background_manager=service.native_background_manager(),
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
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
                f"native-fem-generation-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            return result

        def create_mesh(source, backend: str):
            analysis_result = call(
                ANALYZE_MODEL_CAPABILITY_NAME,
                {
                    "operation": "create_analysis",
                    "label": f"{backend.title()} Analysis",
                    "default_solver_policy": "none",
                },
            )
            analysis = document.getObject(analysis_result["created_analysis"]["object_name"])
            settings = (
                {
                    "maximum_size_mm": 3.0,
                    "minimum_size_mm": 0.5,
                    "element_dimension": "3d",
                    "element_order": "first",
                }
                if backend == "gmsh"
                else {
                    "maximum_size_mm": 3.0,
                    "minimum_size_mm": 0.5,
                    "fineness": "moderate",
                    "second_order": False,
                }
            )
            mesh_result = call(
                ANALYZE_MESH_CAPABILITY_NAME,
                {
                    "operation": f"create_{backend}",
                    "analysis": _analysis_target(analysis_state(analysis)),
                    "source": _target(mesh_object_state(source)),
                    "label": f"{backend.title()} Mesh",
                    "settings": settings,
                },
            )
            mesh = document.getObject(mesh_result["created_mesh_definition"]["object_name"])
            region_result = call(
                ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME,
                {
                    "operation": "create_region",
                    "mesh": _target(fem_mesh_definition_state(mesh)),
                    "label": f"{backend.title()} Region",
                    "references": _refs(source, "Solid1"),
                    "definition": {"element_size_mm": 1.0},
                },
            )
            region = document.getObject(region_result["created_mesh_refinement"]["object_name"])
            return mesh, region

        gmsh_mesh, gmsh_region = create_mesh(gmsh_source, "gmsh")
        netgen_mesh, netgen_region = create_mesh(netgen_source, "netgen")

        ui_ticks = 0
        timer = QtCore.QTimer()
        timer.setInterval(10)

        def tick() -> None:
            nonlocal ui_ticks
            ui_ticks += 1

        timer.timeout.connect(tick)
        timer.start()

        def wait_for_job(job_id: str, *, timeout: float = 15.0) -> dict:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                _events(2)
                snapshot = context.background_manager.snapshot(job_id)
                if snapshot.terminal:
                    return call(
                        NATIVE_BACKGROUND_CAPABILITY_NAME,
                        {"operation": "status", "job_id": job_id},
                    )["job"]
                time.sleep(0.01)
            raise AssertionError(f"Background FEM job {job_id} did not finish")

        generated = {}
        for backend, mesh in (("gmsh", gmsh_mesh), ("netgen", netgen_mesh)):
            started = call(
                ANALYZE_MESH_CAPABILITY_NAME,
                {
                    "operation": f"generate_{backend}",
                    "target": _target(fem_mesh_definition_state(mesh)),
                    "timeout_seconds": 10,
                },
            )
            job = wait_for_job(started["job"]["job_id"])
            assert job["phase"] == "completed", job
            generated[backend] = job["result"]["generated_mesh_definition"]
            assert generated[backend]["generated"]
            assert generated[backend]["topology"]["nodes"] > 0

        assert ui_ticks >= 10, ui_ticks
        netgen_before_undo = fem_mesh_definition_state(netgen_mesh)
        document.undo()
        assert not fem_mesh_definition_state(netgen_mesh)["generated"]
        document.redo()
        assert fem_mesh_definition_state(netgen_mesh)["state_sha256"] == netgen_before_undo[
            "state_sha256"
        ]

        gmsh_before_cancel = fem_mesh_definition_state(gmsh_mesh)
        revision_before_cancel = state_store.current_revision(str(document.Uid))
        started = call(
            ANALYZE_MESH_CAPABILITY_NAME,
            {
                "operation": "generate_gmsh",
                "target": _target(gmsh_before_cancel),
                "timeout_seconds": 10,
            },
        )
        cancel = call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            {"operation": "cancel", "job_id": started["job"]["job_id"]},
        )
        assert cancel["cancel_accepted"]
        cancelled = wait_for_job(started["job"]["job_id"])
        assert cancelled["phase"] == "cancelled", cancelled
        assert fem_mesh_definition_state(gmsh_mesh)["state_sha256"] == gmsh_before_cancel[
            "state_sha256"
        ]
        assert state_store.current_revision(str(document.Uid)) == revision_before_cancel

        expected = {
            gmsh_mesh.Name: fem_mesh_definition_state(gmsh_mesh),
            netgen_mesh.Name: fem_mesh_definition_state(netgen_mesh),
            gmsh_region.Name: mesh_refinement_state(gmsh_region),
            netgen_region.Name: mesh_refinement_state(netgen_region),
        }
        mesh_names = {gmsh_mesh.Name, netgen_mesh.Name}
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _events(12)
        for name, old in expected.items():
            obj = document.getObject(name)
            current = (
                fem_mesh_definition_state(obj)
                if name in mesh_names
                else mesh_refinement_state(obj)
            )
            assert current["state_sha256"] == old["state_sha256"]

        timer.stop()
        print(
            "VIBECAD_NATIVE_ANALYZE_MESH_GENERATION_GUI_OK backends=2 "
            "background=true responsive=true cancellation=true exact_commit=true "
            "refinements=true undo_redo=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        gmsh_preferences.SetString("gmshBinaryPath", old_gmsh)
        netgen_preferences.SetString("NetgenPythonPath", old_netgen)
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
