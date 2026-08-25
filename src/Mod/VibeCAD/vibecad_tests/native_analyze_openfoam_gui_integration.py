# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real GUI gate for shared human/AI OpenFOAM execution."""

from __future__ import annotations

import json
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
from VibeCADNativeAnalyzeFlowPresentation import present_flow_result
from VibeCADNativeAnalyzePostFunctions import (
    create_post_function,
    prepare_post_function,
    verify_post_function,
)
from VibeCADNativeAnalyzeResultState import result_reference_state, result_state
from VibeCADNativeAnalyzeStudy import configure_study_intent
from VibeCADNativeMutation import run_human_mutation


def _boundary(document, domain, name, faces, condition, turbulence=None):
    obj = ObjectsFem.makeConstraintFluidBoundary(document, name)
    apply_fluid_values(
        obj,
        prepare_fluid_values(
            "fluid_boundary",
            {
                "condition": condition,
                "turbulence": turbulence or {"kind": "none"},
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
    material.References = [(domain, ("Solid1",))]
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
        {
            "kind": "intensity_length_scale",
            "intensity_ratio": 0.05,
            "length_scale_m": 0.001,
        },
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
    initial = ObjectsFem.makeConstraintInitialFlowVelocity(document, "InitialVelocity")
    apply_fluid_values(
        initial,
        prepare_fluid_values(
            "initial_flow_velocity",
            {"components": {"x": {"kind": "value", "value_m_s": 1.0}}},
        ),
    )
    for obj in (material, mesh, inlet, outlet, walls, initial):
        analysis.addObject(obj)
    document.recompute()
    GmshTools(mesh).create_mesh()
    assert mesh.FemMesh.VolumeCount > 0

    document.openTransaction("Create OpenFOAM solver")
    try:
        solver = ObjectsFem.makeSolverOpenFOAM(document)
        solver.MaxIterations = 300
        solver.WriteEveryIterations = 100
        solver.TurbulenceModel = "kOmegaSST"
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
                assert len(result["execution"]["stages"]) == 6
                assert all(
                    stage["exit_code"] == 0 for stage in result["execution"]["stages"]
                )
                result_name = result["result"]["object_name"]
                result_object = document.getObject(result_name)
                assert result_object is not None
                assert result_object.isDerivedFrom("Fem::FemPostPipeline")
                assert result_object.VibeCADTimelineOwner is solver
                assert "VibeCADOpenFOAMSummary" in result_object.PropertiesList
                flow_summary = json.loads(result_object.VibeCADOpenFOAMSummary)
                assert flow_summary["pressure_unit"] == "Pa"
                assert flow_summary["velocity_unit"] == "m/s"
                assert flow_summary["turbulence_model"] == "kOmegaSST"
                assert flow_summary["converged"] is True
                assert flow_summary["kinematic_viscosity_m2_s"] == 1.506e-5
                assert flow_summary["maximum_velocity_m_s"] >= 1.0
                assert flow_summary["static_pressure_drop_pa"] > 0.0
                assert [
                    boundary["name"] for boundary in flow_summary["boundaries"]
                ] == ["Face1", "Face2", "Face3", "Face4", "Face5", "Face6"]
                assert flow_summary["boundaries"][0]["kind"] == "inlet_velocity"
                assert flow_summary["boundaries"][1]["kind"] == "outlet_static_pressure"
                assert flow_summary["boundaries"][0]["condition"] == {
                    "kind": "inlet_velocity",
                    "turbulence": {
                        "intensity_ratio": 0.05,
                        "kind": "intensity_length_scale",
                        "length_scale_m": 0.001,
                    },
                    "velocity_m_s": 1.0,
                }
                from femsolver.openfoam.results import openfoam_flow_performance

                performance = openfoam_flow_performance(
                    flow_summary,
                    upstream_boundary="Face1",
                    downstream_boundary="Face2",
                    flow_boundary="Face2",
                )
                assert abs(performance["geometric_flow_area_m2"] - 1.0e-4) < 1.0e-10
                assert performance["volumetric_flow_rate_m3_s"] > 0.0
                assert performance["mass_flow_rate_kg_s"] > 0.0
                assert performance["effective_flow_area_m2"] > 0.0
                assert performance["discharge_coefficient"] > 0.0
                assert performance["continuity_error_percent"] < 1.0
                fields = {
                    (field["name"], field.get("unit"))
                    for field in result_state(result_object)["fields"]
                }
                assert ("Pressure", "Pa") in fields
                assert ("Velocity", "m/s") in fields
                assert ("Turbulent Kinetic Energy", "m^2/s^2") in fields
                assert ("Specific Dissipation Rate", "1/s") in fields
                assert ("Turbulent Kinematic Viscosity", "m^2/s") in fields
                exact_flow = result_state(result_object)["flow"]
                assert exact_flow["kinematic_viscosity_m2_s"] == 1.506e-5
                assert exact_flow["boundaries"][0]["condition"]["kind"] == (
                    "inlet_velocity"
                )
                pressure_view = present_flow_result(result_object, "pressure")
                assert pressure_view["presentation"]["field"] == "Pressure"
                velocity_view = present_flow_result(result_object, "velocity")
                assert velocity_view["presentation"]["field"] == "Velocity"
                assert velocity_view["presentation"]["component"] == "Magnitude"
                turbulence_view = present_flow_result(
                    result_object, "turbulent_kinetic_energy"
                )
                assert turbulence_view["presentation"]["field"] == (
                    "Turbulent Kinetic Energy"
                )
                assert str(result_object.VibeCADDataLengthUnit) == "m"
                result_target = result_reference_state(result_object)
                prepared_plane = prepare_post_function(
                    document,
                    str(document.Uid),
                    kind="plane",
                    pipeline={
                        "object_name": result_target["object_name"],
                        "expected_state_sha256": result_target["state_sha256"],
                    },
                    label="Mid-plane",
                    origin_mm={"x": 10.0, "y": 5.0, "z": 5.0},
                    normal={"x": 0.0, "y": 0.0, "z": 1.0},
                )
                plane_result = run_human_mutation(
                    document=document,
                    transaction_name="Create OpenFOAM mid-plane",
                    mutate=lambda current: create_post_function(
                        current, prepared_plane
                    ),
                    verify=verify_post_function,
                )
                plane = document.getObject(
                    plane_result["created_function"]["object_name"]
                )
                assert plane is not None
                assert getattr(plane, "VibeCADTimelineOwner", None) is None
                assert plane_result["function_provider"][
                    "timeline_owner_chain"
                ] == [result_object.Name, solver.Name]
                assert abs(float(plane.PlaneOrigin.x) - 0.01) < 1.0e-12
                assert abs(float(plane.PlaneOrigin.y) - 0.005) < 1.0e-12
                assert abs(float(plane.PlaneOrigin.z) - 0.005) < 1.0e-12
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
                    "background=true ui_responsive=true result_import=true reopen=true "
                    "gfa_efa=true surface_flux=true turbulence=kOmegaSST "
                    "post_function=true millimeter_coordinates=true nested_history=true",
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
