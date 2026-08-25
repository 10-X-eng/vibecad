# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-solver gate for a multipart structural study through the provider surface."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADSession as Session
from VibeCADCore import get_service
from VibeCADNativeAnalyzeState import analysis_state
from VibeCADNativeProviderRunner import NativeProviderToolRunner
from VibeCADNativeSessionFactory import create_native_session_execution
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 8) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _activate_analyze():
    main_window = Gui.getMainWindow()
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        item
        for item in range(tabs.count())
        if str(tabs.tabData(item)) == "FemWorkbench"
    )
    tabs.setCurrentIndex(index)
    _events(24)
    assert read_active_ribbon_surface(controller).surface_id == "analyze"
    return controller


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    runner = None
    exit_code = 1
    gmsh_preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh")
    ccx_preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Ccx")
    old_gmsh = gmsh_preferences.GetString("gmshBinaryPath", "")
    old_ccx = ccx_preferences.GetString("ccxBinaryPath", "")
    try:
        gmsh_preferences.SetString("gmshBinaryPath", "/usr/bin/gmsh")
        ccx_preferences.SetString("ccxBinaryPath", "/usr/bin/ccx")
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-multipart-structural-"
        )
        output = Path(temporary.name) / "multipart-structural.FCStd"
        document = App.newDocument("NativeMultipartStructuralGate")
        document.UndoMode = 1
        document.saveAs(str(output))

        sources = (
            ("LeftPier", "Left pier", Part.makeBox(20.0, 20.0, 50.0)),
            (
                "RightPier",
                "Right pier",
                Part.makeBox(20.0, 20.0, 50.0, App.Vector(100.0, 0.0, 0.0)),
            ),
            (
                "Deck",
                "Bridge deck",
                Part.makeBox(120.0, 20.0, 10.0, App.Vector(0.0, 0.0, 50.0)),
            ),
        )
        for name, label, shape in sources:
            obj = document.addObject("Part::Feature", name)
            obj.Label = label
            obj.Shape = shape
        document.recompute()

        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        controller = _activate_analyze()
        service = get_service()
        service.select_modeling_engine("native")
        call_count = 0

        def call(tool: str, arguments: dict) -> dict:
            nonlocal call_count, runner
            call_count += 1
            context = Session._context_for_provider(service)
            schemas = list(context["provider_tool_schemas"])
            names = {str(schema.get("name") or "") for schema in schemas}
            assert tool in names, (tool, sorted(names), service.native_active_snapshot())
            runner = NativeProviderToolRunner(
                execution=create_native_session_execution(
                    service=service,
                    expected_surface=dict(context["provider_tool_surface"]),
                    expected_schemas=schemas,
                    controller=controller,
                    document_thread_dispatch=VibeGui._dispatch_to_document_thread,
                ),
                document_dispatch=lambda operation: operation(),
                refresh_context=lambda: Session._context_for_provider(service),
                frozen_surface=dict(context["provider_tool_surface"]),
                frozen_schemas=schemas,
                frozen_modeling_surface=dict(context["modeling_surface"]),
                tool_trace=[],
            )
            try:
                response = runner(
                    tool,
                    json.dumps(arguments, separators=(",", ":")),
                    f"native-multipart-structural-{call_count}",
                )
            finally:
                runner.close()
                runner = None
            assert response.get("ok") is True, response
            return response

        domain_response = call(
            "analyze.solid_domain",
            {
                "source_names": [name for name, _label, _shape in sources],
                "interface_mode": "shared",
                "label": "Bridge structural domain",
            },
        )
        domain_name = domain_response["domain"]["object_name"]
        domain = document.getObject(domain_name)
        assert domain is not None and len(domain.Shape.Solids) == 3
        assert len(domain.Shape.CompSolids) == 1

        face_response = call("analyze.faces", {"source_name": domain_name})
        faces = face_response["face_page"]["faces"]
        support_faces = [
            face["subelement"]
            for face in faces
            if face.get("normal") == [0.0, 0.0, -1.0]
            and abs(float(face["center_mm"][2])) < 1.0e-7
        ]
        load_faces = [
            face["subelement"]
            for face in faces
            if face.get("normal") == [0.0, 0.0, 1.0]
            and abs(float(face["center_mm"][2]) - 60.0) < 1.0e-7
        ]
        assert len(support_faces) == 2, faces
        assert len(load_faces) == 1, faces

        analysis_response = call(
            "analyze.model",
            {
                "operation": "create_analysis",
                "label": "Bridge static analysis",
                "default_solver_policy": "none",
                "study": {"physics": ["mechanical"], "regime": "steady"},
            },
        )
        analysis_name = analysis_response["created_analysis"]["object_name"]
        catalog = call(
            "analyze.material_catalog",
            {"query": "steel", "category": "solid", "limit": 10},
        )
        materials = catalog["materials"]
        assert materials
        material_name = str(materials[0]["properties"]["name"])
        call(
            "analyze.catalog_material",
            {
                "analysis_name": analysis_name,
                "source_name": domain_name,
                "material_name": material_name,
            },
        )
        call(
            "analyze.fixed_support",
            {
                "analysis_name": analysis_name,
                "source_name": domain_name,
                "subelement_names": support_faces,
            },
        )
        call(
            "analyze.force",
            {
                "analysis_name": analysis_name,
                "source_name": domain_name,
                "subelement_names": load_faces,
                "force_vector_n": {"x": 0.0, "y": 0.0, "z": -50000.0},
            },
        )
        mesh_response = call(
            "analyze.solid_mesh",
            {
                "analysis_name": analysis_name,
                "source_name": domain_name,
                "maximum_size_mm": 12.0,
                "minimum_size_mm": 2.0,
                "element_order": "second",
                "label": "Bridge volume mesh",
            },
        )
        mesh_name = mesh_response["mesh_name"]
        mesh_job = call("analyze.generate_gmsh", {"mesh_name": mesh_name})["job"]

        def await_job(job_id: str, timeout_seconds: float):
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                _events(2)
                snapshot = service.native_background_manager().snapshot(job_id)
                if snapshot.terminal:
                    return snapshot
                time.sleep(0.02)
            raise AssertionError(f"Background job did not finish: {job_id}")

        generated = await_job(mesh_job["job_id"], 180.0)
        assert generated.phase == "completed", generated
        mesh_result = generated.result["generated_mesh_definition"]
        assert mesh_result["generated"] is True
        assert mesh_result["topology"]["volumes"] > 0

        analysis = document.getObject(analysis_name)
        solver_response = call(
            "analyze.solver",
            {
                "operation": "create_calculix",
                "analysis": {
                    "object_name": analysis_name,
                    "expected_state_sha256": analysis_state(analysis)["state_sha256"],
                    "expected_member_count": analysis_state(analysis)["member_count"],
                },
                "label": "Bridge CalculiX solver",
            },
        )
        solver_name = solver_response["created_solver"]["object_name"]
        solve_job = call("analyze.run_solver", {"solver_name": solver_name})["job"]
        solved = await_job(solve_job["job_id"], 300.0)
        assert solved.phase == "completed", solved
        result_name = solved.result["result"]["object_name"]
        result = call("analyze.mechanical_results", {"result_name": result_name})
        assert result["maximum_displacement"]["value"] > 0.0, result
        assert result["maximum_von_mises_stress"]["value"] > 0.0, result

        expected = {
            "domain": domain_name,
            "analysis": analysis_name,
            "mesh": mesh_name,
            "solver": solver_name,
            "result": result_name,
        }
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(output))
        App.setActiveDocument(document.Name)
        _events(12)
        assert all(document.getObject(name) is not None for name in expected.values())
        reopened_domain = document.getObject(domain_name)
        assert len(reopened_domain.Shape.Solids) == 3
        assert len(reopened_domain.Shape.CompSolids) == 1
        print(
            "VIBECAD_NATIVE_ANALYZE_MULTIPART_STRUCTURAL_GUI_OK "
            f"solids=3 nodes={mesh_result['topology']['nodes']} "
            f"volumes={mesh_result['topology']['volumes']} "
            f"max_displacement_mm={result['maximum_displacement']['value']:.9g} "
            f"max_von_mises_mpa={result['maximum_von_mises_stress']['value']:.9g} "
            "provider_turns=true real_gmsh=true real_calculix=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if runner is not None:
            runner.close()
        gmsh_preferences.SetString("gmshBinaryPath", old_gmsh)
        ccx_preferences.SetString("ccxBinaryPath", old_ccx)
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
