# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real GUI gate for shared human/AI OpenFOAM execution."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import ObjectsFem
import Part
from PySide import QtCore, QtWidgets

from femmesh.gmshtools import GmshTools
from femsolver.run import run_fem_solver
from VibeCADCore import get_service
import VibeCADGui
from VibeCADNativeAnalyzeFluidValues import apply_fluid_values, prepare_fluid_values
from VibeCADNativeAnalyzeStudy import configure_study_intent


def _boundary(document, domain, name, faces, condition):
    obj = ObjectsFem.makeConstraintFluidBoundary(document, name)
    apply_fluid_values(
        obj,
        prepare_fluid_values(
            "fluid_boundary",
            {
                "condition": condition,
                "turbulence": {"kind": "none"},
                "thermal": {"kind": "adiabatic"},
            },
        ),
    )
    obj.References = [(domain, tuple(faces))]
    return obj


def _create_case(document, root):
    domain = document.addObject("Part::Feature", "FluidDomain")
    domain.Shape = Part.makeBox(20, 10, 10)
    analysis = ObjectsFem.makeAnalysis(document, "Analysis")
    configure_study_intent(analysis, {"physics": ["fluid"], "regime": "steady"})

    material = ObjectsFem.makeMaterialFluid(document, "Air")
    material.Material = {
        "Name": "Air",
        "Density": "1.204 kg/m^3",
        "KinematicViscosity": "1.506e-5 m^2/s",
    }
    mesh = ObjectsFem.makeMeshGmsh(document, "FluidMesh")
    mesh.Shape = domain
    mesh.WorkingDirectory = str(root / "gmsh")
    mesh.CharacteristicLengthMax = 3
    mesh.ElementOrder = "1st"
    inlet = _boundary(
        document,
        domain,
        "Inlet",
        ("Face1",),
        {"kind": "inlet_velocity", "velocity_m_s": 1.0},
    )
    outlet = _boundary(
        document,
        domain,
        "Outlet",
        ("Face2",),
        {"kind": "outlet_static_pressure", "pressure_pa": 0.0},
    )
    walls = _boundary(
        document,
        domain,
        "Walls",
        ("Face3", "Face4", "Face5", "Face6"),
        {"kind": "wall_no_slip"},
    )
    for obj in (material, mesh, inlet, outlet, walls):
        analysis.addObject(obj)
    document.recompute()
    GmshTools(mesh).create_mesh()
    assert mesh.FemMesh.VolumeCount > 0

    document.openTransaction("Create OpenFOAM solver")
    try:
        solver = ObjectsFem.makeSolverOpenFOAM(document)
        solver.MaxIterations = 300
        solver.WriteEveryIterations = 100
        analysis.addObject(solver)
        from femcommands import manager

        manager._mark_timeline_operation(solver)
        document.publishProvisionalTimelineOperationBlock(solver, (), ())
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return solver


def _run():
    application = QtWidgets.QApplication.instance()
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-openfoam-gui-")
    root = Path(temporary.name)
    output = root / "openfoam-gui.FCStd"
    document = None
    heartbeat = QtCore.QTimer()
    poll = QtCore.QTimer()
    ticks = 0
    exit_code = 1

    def finish(code):
        nonlocal exit_code
        exit_code = code
        heartbeat.stop()
        poll.stop()
        if document is not None and document.Name in App.listDocuments():
            document.save()
            App.closeDocument(document.Name)
        temporary.cleanup()
        application.exit(code)

    try:
        Gui.activateWorkbench("FemWorkbench")
        document = App.newDocument("NativeAnalyzeOpenFOAMGuiGate")
        document.UndoMode = 1
        document.saveAs(str(output))
        VibeCADGui._ensure_document_thread_invoker()
        solver = _create_case(document, root)
        job_id = run_fem_solver(solver)
        assert isinstance(job_id, str) and job_id

        def beat():
            nonlocal ticks
            ticks += 1

        def check():
            nonlocal document
            try:
                snapshot = get_service().native_background_manager().snapshot(job_id)
                if not snapshot.terminal:
                    return
                assert snapshot.phase == "completed", snapshot.error
                assert ticks >= 2
                result = snapshot.result
                assert result["execution"]["backend"] == "openfoam"
                assert len(result["execution"]["stages"]) == 5
                assert all(
                    stage["exit_code"] == 0 for stage in result["execution"]["stages"]
                )
                result_name = result["result"]["object_name"]
                result_object = document.getObject(result_name)
                assert result_object is not None
                assert result_object.isDerivedFrom("Fem::FemPostPipeline")
                assert result_object.VibeCADTimelineOwner is solver
                resources = result["result"]["resources"]
                assert len(resources) == 1
                log = document.getObject(resources[0]["object_name"])
                assert log is not None and "SIMPLE solution converged" in log.Text
                document.save()
                App.closeDocument(document.Name)
                document = App.openDocument(str(output))
                assert document.getObject(result_name) is not None
                print(
                    "VIBECAD_NATIVE_ANALYZE_OPENFOAM_GUI_OK "
                    "human_command=true shared_pipeline=true real_solver=true "
                    "background=true ui_responsive=true result_import=true reopen=true",
                    flush=True,
                )
                finish(0)
            except Exception:
                traceback.print_exc(file=sys.__stderr__)
                finish(1)

        heartbeat.timeout.connect(beat)
        heartbeat.start(5)
        poll.timeout.connect(check)
        poll.start(50)
        QtCore.QTimer.singleShot(180000, lambda: finish(1))
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
        finish(1)


QtCore.QTimer.singleShot(1000, _run)
