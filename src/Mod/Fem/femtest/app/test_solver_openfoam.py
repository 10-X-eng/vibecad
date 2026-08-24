# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import FreeCAD as App
import ObjectsFem


class TestOpenFOAMSolver(unittest.TestCase):
    def setUp(self):
        self.document = App.newDocument("FemOpenFOAMTest")

    def tearDown(self):
        if self.document is not None:
            App.closeDocument(self.document.Name)

    def test_factory_creates_persistent_solver_settings(self):
        solver = ObjectsFem.makeSolverOpenFOAM(self.document)

        self.assertEqual(solver.Proxy.Type, "Fem::SolverOpenFOAM")
        self.assertTrue(solver.isDerivedFrom("Fem::FemSolverObjectPython"))
        self.assertEqual(solver.FlowRegime, "steady")
        self.assertEqual(solver.TurbulenceModel, "laminar")
        self.assertEqual(
            solver.getEnumerationsOfProperty("TurbulenceModel"),
            ["laminar", "kOmegaSST"],
        )
        self.assertEqual(solver.MaxIterations, 1000)
        self.assertEqual(solver.WriteEveryIterations, 100)
        self.assertAlmostEqual(solver.PressureTolerance, 1.0e-6)
        self.assertAlmostEqual(solver.VelocityTolerance, 1.0e-5)
        self.assertAlmostEqual(solver.TurbulenceTolerance, 1.0e-3)

    def test_steady_laminar_case_uses_exact_units_and_patch_roles(self):
        from femsolver.openfoam.case import SteadyIncompressibleCase, build_case_files

        case = SteadyIncompressibleCase(
            density_kg_m3=1.225,
            kinematic_viscosity_m2_s=1.5e-5,
            max_iterations=800,
            write_every_iterations=80,
            pressure_tolerance=1.0e-7,
            velocity_tolerance=1.0e-6,
            initial_velocity_m_s=(0.0, 0.0, 0.0),
            initial_pressure_pa=101325.0,
            patches={
                "Face1": {"kind": "inlet_velocity", "velocity_m_s": 2.5},
                "Face2": {
                    "kind": "outlet_static_pressure",
                    "pressure_pa": 101325.0,
                },
                "Face3": {"kind": "wall_no_slip"},
                "Face4": {"kind": "symmetry"},
            },
        )

        files = build_case_files(case)

        self.assertEqual(
            set(files),
            {
                "system/controlDict",
                "system/fvSchemes",
                "system/fvSolution",
                "system/changeDictionaryDict",
                "constant/physicalProperties",
                "constant/momentumTransport",
                "0/U",
                "0/p",
            },
        )
        self.assertIn("endTime         800;", files["system/controlDict"])
        self.assertIn("writeInterval   80;", files["system/controlDict"])
        self.assertIn("nu              1.5e-05;", files["constant/physicalProperties"])
        self.assertIn("simulationType  laminar;", files["constant/momentumTransport"])
        self.assertIn("type            surfaceNormalFixedValue;", files["0/U"])
        self.assertIn("refValue        uniform -2.5;", files["0/U"])
        self.assertIn("type            noSlip;", files["0/U"])
        self.assertIn("type            symmetry;", files["0/U"])
        self.assertIn("type            fixedValue;", files["0/p"])
        self.assertIn("value           uniform 82714.2857142857;", files["0/p"])
        self.assertIn("Face4 { type symmetry; }", files["system/changeDictionaryDict"])

    def test_steady_k_omega_sst_case_writes_turbulence_fields(self):
        from femsolver.openfoam.case import SteadyIncompressibleCase, build_case_files

        case = SteadyIncompressibleCase(
            density_kg_m3=1.225,
            kinematic_viscosity_m2_s=1.5e-5,
            max_iterations=800,
            write_every_iterations=80,
            pressure_tolerance=1.0e-7,
            velocity_tolerance=1.0e-6,
            initial_velocity_m_s=(2.5, 0.0, 0.0),
            initial_pressure_pa=0.0,
            patches={
                "Face1": {
                    "kind": "inlet_velocity",
                    "velocity_m_s": 2.5,
                    "turbulence": {
                        "kind": "intensity_length_scale",
                        "intensity_ratio": 0.05,
                        "length_scale_m": 0.01,
                    },
                },
                "Face2": {
                    "kind": "outlet_static_pressure",
                    "pressure_pa": 0.0,
                    "turbulence": {"kind": "none"},
                },
                "Face3": {
                    "kind": "wall_no_slip",
                    "turbulence": {"kind": "none"},
                },
                "Face4": {
                    "kind": "symmetry",
                    "turbulence": {"kind": "none"},
                },
            },
            turbulence_model="kOmegaSST",
            turbulence_tolerance=1.0e-3,
        )

        files = build_case_files(case)

        self.assertEqual(
            set(files),
            {
                "system/controlDict",
                "system/fvSchemes",
                "system/fvSolution",
                "system/changeDictionaryDict",
                "constant/physicalProperties",
                "constant/momentumTransport",
                "0/U",
                "0/p",
                "0/k",
                "0/omega",
                "0/nut",
            },
        )
        self.assertIn("simulationType  RAS;", files["constant/momentumTransport"])
        self.assertIn("model           kOmegaSST;", files["constant/momentumTransport"])
        self.assertIn("div(phi,k)", files["system/fvSchemes"])
        self.assertIn("div(phi,omega)", files["system/fvSchemes"])
        self.assertIn('"(k|omega)" 0.001;', files["system/fvSolution"])
        self.assertIn("type            fixedValue;", files["0/k"])
        self.assertIn("value           uniform 0.0234375;", files["0/k"])
        self.assertIn("type            kqRWallFunction;", files["0/k"])
        self.assertIn("type            omegaWallFunction;", files["0/omega"])
        self.assertIn("type            nutkWallFunction;", files["0/nut"])
        self.assertIn("type            symmetry;", files["0/omega"])
        self.assertIn("Face3 { type wall; }", files["system/changeDictionaryDict"])
        self.assertIn(
            "Face4 { type symmetry; }", files["system/changeDictionaryDict"]
        )

    def test_restored_solver_gains_turbulence_models_without_changing_selection(self):
        solver = ObjectsFem.makeSolverOpenFOAM(self.document)
        solver.TurbulenceModel = ["laminar"]
        solver_name = solver.Name
        with tempfile.TemporaryDirectory(prefix="vibecad-openfoam-solver-") as root:
            path = Path(root) / "solver.FCStd"
            self.document.saveAs(str(path))
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(str(path))
            restored = self.document.getObject(solver_name)

            self.assertEqual(restored.TurbulenceModel, "laminar")
            self.assertEqual(
                restored.getEnumerationsOfProperty("TurbulenceModel"),
                ["laminar", "kOmegaSST"],
            )

    def test_standard_run_dispatches_detached_solvers_to_shared_gui_runner(self):
        from femsolver.run import run_fem_solver

        analysis = ObjectsFem.makeAnalysis(self.document)
        solvers = (
            ObjectsFem.makeSolverElmer(self.document),
            ObjectsFem.makeSolverOpenFOAM(self.document),
        )
        for solver in solvers:
            analysis.addObject(solver)
        calls = []
        gui_runner = mock.Mock(
            run_solver_detached=lambda exact_solver: calls.append(exact_solver)
            or "started"
        )

        with mock.patch.dict(
            "sys.modules",
            {"VibeCADAnalyzeSolverGui": gui_runner},
        ), mock.patch.object(App, "GuiUp", True):
            results = [run_fem_solver(solver) for solver in solvers]

        self.assertEqual(results, ["started", "started"])
        self.assertEqual(calls, list(solvers))

    def test_case_requires_a_pressure_reference(self):
        from femsolver.openfoam.case import SteadyIncompressibleCase, build_case_files

        case = SteadyIncompressibleCase(
            density_kg_m3=1.225,
            kinematic_viscosity_m2_s=1.5e-5,
            max_iterations=100,
            write_every_iterations=10,
            pressure_tolerance=1.0e-6,
            velocity_tolerance=1.0e-5,
            initial_velocity_m_s=(0.0, 0.0, 0.0),
            initial_pressure_pa=0.0,
            patches={
                "Face1": {"kind": "inlet_velocity", "velocity_m_s": 2.5},
                "Face2": {"kind": "wall_no_slip"},
            },
        )

        with self.assertRaisesRegex(ValueError, "pressure-defining"):
            build_case_files(case)

    def test_flow_pipeline_dataset_survives_save_and_reopen(self):
        import vtk

        points = vtk.vtkPoints()
        for point in ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)):
            points.InsertNextPoint(*point)
        tetrahedron = vtk.vtkTetra()
        for index in range(4):
            tetrahedron.GetPointIds().SetId(index, index)
        dataset = vtk.vtkUnstructuredGrid()
        dataset.SetPoints(points)
        dataset.InsertNextCell(tetrahedron.GetCellType(), tetrahedron.GetPointIds())

        pressure = vtk.vtkDoubleArray()
        pressure.SetName("Pressure")
        velocity = vtk.vtkDoubleArray()
        velocity.SetName("Velocity")
        velocity.SetNumberOfComponents(3)
        for index in range(4):
            pressure.InsertNextValue(float(index))
            velocity.InsertNextTuple3(float(index), 0.0, 0.0)
        dataset.GetPointData().AddArray(pressure)
        dataset.GetPointData().AddArray(velocity)

        with tempfile.TemporaryDirectory(prefix="vibecad-openfoam-result-") as root:
            vtk_path = Path(root) / "flow.vtk"
            writer = vtk.vtkDataSetWriter()
            writer.SetFileName(str(vtk_path))
            writer.SetInputData(dataset)
            writer.SetFileTypeToBinary()
            self.assertEqual(writer.Write(), 1)

            pipeline = self.document.addObject("Fem::FemPostPipeline", "FlowResult")
            pipeline.read(str(vtk_path))
            self.assertEqual(pipeline.getDataSet().GetNumberOfPoints(), 4)

            document_path = Path(root) / "flow.FCStd"
            self.document.saveAs(str(document_path))
            cold_restore = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import FreeCAD as App; "
                        f"document=App.openDocument({str(document_path)!r}); "
                        "pipeline=document.getObject('FlowResult'); "
                        "dataset=pipeline.getDataSet(); "
                        "count=0 if dataset is None else dataset.GetNumberOfPoints(); "
                        "print('VTK_RESTORED', count); "
                        "App.closeDocument(document.Name); "
                        "raise SystemExit(0 if count == 4 else 2)"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(
                cold_restore.returncode,
                0,
                cold_restore.stdout + cold_restore.stderr,
            )
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(str(document_path))

            restored = self.document.getObject("FlowResult")
            self.assertIsNotNone(restored.Data)
            self.assertEqual(restored.getDataSet().GetNumberOfPoints(), 4)
            self.assertEqual(
                restored.getDataSet().GetPointData().GetArray("Pressure").GetRange(),
                (0.0, 3.0),
            )

    def test_flow_performance_uses_oriented_surface_integrals(self):
        import math
        import vtk

        def add_fields(dataset, pressure_value):
            pressure = vtk.vtkDoubleArray()
            pressure.SetName("p")
            velocity = vtk.vtkDoubleArray()
            velocity.SetName("U")
            velocity.SetNumberOfComponents(3)
            for _index in range(dataset.GetNumberOfCells()):
                pressure.InsertNextValue(pressure_value)
                velocity.InsertNextTuple3(2.0, 0.0, 0.0)
            dataset.GetCellData().AddArray(pressure)
            dataset.GetCellData().AddArray(velocity)

        def square(x, point_order, pressure_value):
            points = vtk.vtkPoints()
            for point in (
                (x, 0.0, 0.0),
                (x, 1.0, 0.0),
                (x, 1.0, 1.0),
                (x, 0.0, 1.0),
            ):
                points.InsertNextPoint(*point)
            cells = vtk.vtkCellArray()
            for indices in point_order:
                triangle = vtk.vtkTriangle()
                for local, point_index in enumerate(indices):
                    triangle.GetPointIds().SetId(local, point_index)
                cells.InsertNextCell(triangle)
            dataset = vtk.vtkPolyData()
            dataset.SetPoints(points)
            dataset.SetPolys(cells)
            add_fields(dataset, pressure_value)
            return dataset

        with tempfile.TemporaryDirectory(prefix="vibecad-openfoam-summary-") as root:
            vtk_root = Path(root) / "VTK"
            vtk_root.mkdir()
            internal = vtk.vtkUnstructuredGrid()
            points = vtk.vtkPoints()
            for point in ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)):
                points.InsertNextPoint(*point)
            tetrahedron = vtk.vtkTetra()
            for index in range(4):
                tetrahedron.GetPointIds().SetId(index, index)
            internal.SetPoints(points)
            internal.InsertNextCell(
                tetrahedron.GetCellType(), tetrahedron.GetPointIds()
            )
            add_fields(internal, 5.0)

            datasets = {
                vtk_root / "internal.vtk": internal,
                vtk_root / "inlet" / "inlet.vtk": square(
                    0.0, ((0, 3, 2), (0, 2, 1)), 10.0
                ),
                vtk_root / "outlet" / "outlet.vtk": square(
                    1.0, ((0, 1, 2), (0, 2, 3)), 0.0
                ),
            }
            for path, dataset in datasets.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                writer = vtk.vtkDataSetWriter()
                writer.SetFileName(str(path))
                writer.SetInputData(dataset)
                self.assertEqual(writer.Write(), 1)

            from femsolver.openfoam.results import (
                openfoam_flow_performance,
                openfoam_flow_summary,
            )

            summary = openfoam_flow_summary(
                root,
                result_glob="VTK/internal.vtk",
                density_kg_m3=1.2,
                patches={
                    "inlet": "inlet_velocity",
                    "outlet": "outlet_static_pressure",
                },
                patch_areas_m2={"inlet": 1.0, "outlet": 1.0},
                patch_conditions={
                    "inlet": {
                        "kind": "inlet_velocity",
                        "velocity_m_s": 2.0,
                        "turbulence": {
                            "kind": "intensity_length_scale",
                            "intensity_ratio": 0.05,
                            "length_scale_m": 0.01,
                        },
                    },
                    "outlet": {
                        "kind": "outlet_static_pressure",
                        "pressure_pa": 0.0,
                        "turbulence": {"kind": "none"},
                    },
                },
                kinematic_viscosity_m2_s=1.5e-5,
                turbulence_model="kOmegaSST",
                converged=True,
            )
            self.assertEqual(summary["turbulence_model"], "kOmegaSST")
            self.assertIs(summary["converged"], True)
            self.assertEqual(summary["kinematic_viscosity_m2_s"], 1.5e-5)
            boundaries = {item["name"]: item for item in summary["boundaries"]}
            self.assertEqual(
                boundaries["inlet"]["condition"]["velocity_m_s"], 2.0
            )
            self.assertEqual(boundaries["inlet"]["geometric_area_m2"], 1.0)
            self.assertAlmostEqual(
                boundaries["inlet"]["outward_volumetric_flow_rate_m3_s"], -2.0
            )
            self.assertAlmostEqual(
                boundaries["outlet"]["outward_volumetric_flow_rate_m3_s"], 2.0
            )
            self.assertAlmostEqual(
                boundaries["outlet"]["outward_mass_flow_rate_kg_s"], 2.4
            )

            performance = openfoam_flow_performance(
                summary,
                upstream_boundary="inlet",
                downstream_boundary="outlet",
                flow_boundary="outlet",
            )
            self.assertEqual(performance["geometric_flow_area_m2"], 1.0)
            self.assertEqual(performance["volumetric_flow_rate_m3_s"], 2.0)
            self.assertEqual(performance["mass_flow_rate_kg_s"], 2.4)
            self.assertEqual(performance["static_pressure_drop_pa"], 12.0)
            self.assertAlmostEqual(
                performance["effective_flow_area_m2"], 2.0 / math.sqrt(20.0)
            )
            self.assertAlmostEqual(
                performance["discharge_coefficient"], 2.0 / math.sqrt(20.0)
            )
            self.assertEqual(performance["continuity_error_percent"], 0.0)

            legacy = dict(summary)
            legacy.pop("density_kg_m3")
            with self.assertRaisesRegex(RuntimeError, "run OpenFOAM again"):
                openfoam_flow_performance(
                    legacy,
                    upstream_boundary="inlet",
                    downstream_boundary="outlet",
                    flow_boundary="outlet",
                )


if __name__ == "__main__":
    unittest.main()
