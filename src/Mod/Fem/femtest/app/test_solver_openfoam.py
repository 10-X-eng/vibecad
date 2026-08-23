# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import unittest
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
        self.assertEqual(solver.MaxIterations, 1000)
        self.assertEqual(solver.WriteEveryIterations, 100)
        self.assertAlmostEqual(solver.PressureTolerance, 1.0e-6)
        self.assertAlmostEqual(solver.VelocityTolerance, 1.0e-5)

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

    def test_standard_run_dispatches_openfoam_to_shared_gui_runner(self):
        from femsolver.run import run_fem_solver

        analysis = ObjectsFem.makeAnalysis(self.document)
        solver = ObjectsFem.makeSolverOpenFOAM(self.document)
        analysis.addObject(solver)
        calls = []
        gui_runner = mock.Mock(
            run_openfoam_solver=lambda exact_solver: calls.append(exact_solver)
            or "started"
        )

        with mock.patch.dict(
            "sys.modules",
            {"VibeCADAnalyzeSolverGui": gui_runner},
        ):
            result = run_fem_solver(solver)

        self.assertEqual(result, "started")
        self.assertEqual(calls, [solver])

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


if __name__ == "__main__":
    unittest.main()
