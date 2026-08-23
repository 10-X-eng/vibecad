# ***************************************************************************
# *   Copyright (c) 2015 Bernd Hahnebach <bernd@bimstatik.org>              *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

__title__ = "Common FEM unit tests"
__author__ = "Bernd Hahnebach"
__url__ = "https://www.freecad.org"

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import FreeCAD

import ObjectsFem
from . import support_utils as testtools
from .support_utils import fcc_print


class TestFemCommon(unittest.TestCase):
    fcc_print("import TestFemCommon")

    # ********************************************************************************************
    def setUp(self):
        # setUp is executed before every test

        # new document
        self.document = FreeCAD.newDocument(self.__class__.__name__)

    # ********************************************************************************************
    def tearDown(self):
        # tearDown is executed after every test
        FreeCAD.closeDocument(self.document.Name)

    # ********************************************************************************************
    def test_00print(self):
        # since method name starts with 00 this will be run first
        # this test just prints a line with stars

        fcc_print(
            "\n{0}\n{1} run FEM TestFemCommon tests {2}\n{0}".format(100 * "*", 10 * "*", 61 * "*")
        )

    # ********************************************************************************************
    def test_adding_refshaps(self):
        doc = self.document
        slab = doc.addObject("Part::Plane", "Face")
        slab.Length = 500.00
        slab.Width = 500.00
        cf = ObjectsFem.makeConstraintFixed(doc)
        ref_eles = []
        # FreeCAD list property doesn't seem to support append,
        # thus we need a workaround
        # which on many elements is even much faster
        for i, face in enumerate(slab.Shape.Edges):
            ref_eles.append("Edge%d" % (i + 1))
        cf.References = [(slab, ref_eles)]
        doc.recompute()
        expected_reflist = [(slab, ("Edge1", "Edge2", "Edge3", "Edge4"))]
        assert_err_message = (
            "Adding reference shapes did not result in expected list {} != {}".format(
                cf.References, expected_reflist
            )
        )
        self.assertEqual(cf.References, expected_reflist, assert_err_message)

    def test_solver_runtime_status_reports_exact_engine_requirements(self):
        from femsolver import runtime

        resolved = {
            "ccx": "/opt/calculix/bin/ccx",
            "ElmerSolver": "/opt/elmer/bin/ElmerSolver",
            "ElmerGrid": "/opt/elmer/bin/ElmerGrid",
            "mystran": "/opt/mystran/bin/mystran",
            "z88r": "/opt/z88/bin/z88r",
            "blockMesh": "/opt/openfoam/bin/blockMesh",
            "snappyHexMesh": "/opt/openfoam/bin/snappyHexMesh",
            "foamToVTK": "/opt/openfoam/bin/foamToVTK",
            "foamRun": "/opt/openfoam/bin/foamRun",
        }
        with (
            mock.patch(
                "femsolver.settings.get_binary",
                side_effect=lambda name, _silent: {
                    "Calculix": resolved["ccx"],
                    "ElmerSolver": resolved["ElmerSolver"],
                    "ElmerGrid": resolved["ElmerGrid"],
                    "Mystran": resolved["mystran"],
                    "Z88": resolved["z88r"],
                }.get(name),
            ),
            mock.patch.object(runtime, "openfoam_environment", return_value={}),
            mock.patch.object(runtime, "resolve_executable", side_effect=resolved.get),
        ):
            statuses = {
                status["solver"]: status for status in runtime.solver_runtime_statuses()
            }

        self.assertEqual(
            set(statuses),
            {"calculix", "elmer", "mystran", "z88", "openfoam"},
        )
        self.assertEqual(statuses["calculix"]["programs"], {"solver": resolved["ccx"]})
        self.assertEqual(statuses["mystran"]["programs"], {"solver": resolved["mystran"]})
        self.assertEqual(statuses["z88"]["programs"], {"solver": resolved["z88r"]})
        self.assertEqual(
            statuses["elmer"],
            {
                "solver": "elmer",
                "transport": "native",
                "engine_ready": True,
                "programs": {
                    "grid": "/opt/elmer/bin/ElmerGrid",
                    "solver": "/opt/elmer/bin/ElmerSolver",
                },
                "missing": [],
            },
        )
        self.assertEqual(statuses["openfoam"]["engine_ready"], True)
        self.assertEqual(statuses["openfoam"]["programs"]["solver"], resolved["foamRun"])
        self.assertEqual(statuses["openfoam"]["missing"], [])

    def test_solver_runtime_status_names_missing_capabilities(self):
        from femsolver import runtime

        with (
            mock.patch("femsolver.settings.get_binary", return_value=None),
            mock.patch.object(runtime, "openfoam_environment", return_value={}),
            mock.patch.object(runtime, "resolve_executable", return_value=None),
        ):
            statuses = {
                status["solver"]: status for status in runtime.solver_runtime_statuses()
            }

        self.assertEqual(
            statuses["elmer"]["missing"],
            ["ElmerGrid", "ElmerSolver"],
        )
        self.assertEqual(statuses["calculix"]["missing"], ["ccx"])
        self.assertEqual(statuses["mystran"]["missing"], ["mystran"])
        self.assertEqual(statuses["z88"]["missing"], ["z88r"])
        self.assertEqual(
            statuses["openfoam"]["missing"],
            ["blockMesh", "snappyHexMesh", "foamToVTK", "foamRun|simpleFoam"],
        )
        self.assertEqual(statuses["elmer"]["programs"], {})
        self.assertEqual(statuses["openfoam"]["programs"], {})

    def test_openfoam_environment_file_exposes_its_exact_programs(self):
        from femsolver import runtime

        with tempfile.TemporaryDirectory(prefix="vibecad-openfoam-runtime-") as root:
            root_path = Path(root)
            binary_path = root_path / "platforms" / "bin"
            binary_path.mkdir(parents=True)
            for name in ("blockMesh", "snappyHexMesh", "foamToVTK", "foamRun"):
                program = binary_path / name
                program.write_text("#!/bin/sh\n", encoding="utf-8")
                program.chmod(0o700)
            environment_file = root_path / "bashrc"
            environment_file.write_text(
                f"export WM_PROJECT_DIR='{root_path}'\n"
                f"export PATH='{binary_path}':\"$PATH\"\n",
                encoding="utf-8",
            )

            environment = runtime.load_openfoam_environment(environment_file)

            self.assertEqual(environment["WM_PROJECT_DIR"], str(root_path))
            self.assertEqual(
                runtime.resolve_executable(
                    "blockMesh",
                    search_path=environment["PATH"],
                ),
                str(binary_path / "blockMesh"),
            )

    def test_solver_runtime_honors_configured_elmer_binaries(self):
        from femsolver import runtime

        configured = {
            "ElmerGrid": "/configured/elmer/ElmerGrid",
            "ElmerSolver": "/configured/elmer/ElmerSolver",
        }
        with (
            mock.patch(
                "femsolver.settings.get_binary",
                side_effect=lambda name, _silent: configured.get(name),
            ),
            mock.patch.object(runtime, "openfoam_environment", return_value={}),
            mock.patch.object(runtime, "resolve_executable", return_value=None),
        ):
            statuses = {
                status["solver"]: status
                for status in runtime.solver_runtime_statuses()
            }

        self.assertEqual(statuses["elmer"]["programs"], {
            "grid": configured["ElmerGrid"],
            "solver": configured["ElmerSolver"],
        })
        self.assertTrue(statuses["elmer"]["engine_ready"])

    def test_solver_runtime_filter_does_not_probe_unselected_engines(self):
        from femsolver import runtime

        configured = {
            "ElmerGrid": "/configured/elmer/ElmerGrid",
            "ElmerSolver": "/configured/elmer/ElmerSolver",
        }
        with (
            mock.patch(
                "femsolver.settings.get_binary",
                side_effect=lambda name, _silent: configured.get(name),
            ),
            mock.patch.object(
                runtime,
                "openfoam_environment",
                side_effect=AssertionError("OpenFOAM must not be probed"),
            ),
        ):
            statuses = runtime.solver_runtime_statuses({"elmer"})

        self.assertEqual(tuple(item["solver"] for item in statuses), ("elmer",))

    def test_solver_executable_resolution_falls_back_to_the_application_bundle(self):
        from femsolver import runtime

        with tempfile.TemporaryDirectory(prefix="vibecad-fem-runtime-") as root:
            program = Path(root) / "bin" / "ElmerGrid"
            program.parent.mkdir()
            program.write_text("#!/bin/sh\n", encoding="utf-8")
            program.chmod(0o700)
            with (
                mock.patch.dict(os.environ, {"PATH": ""}),
                mock.patch.object(FreeCAD, "getHomePath", return_value=root + os.sep),
            ):
                self.assertEqual(runtime.resolve_executable("ElmerGrid"), str(program))

    # ********************************************************************************************
    def test_pyimport_all_FEM_modules(self):
        # we're going to try to import all python modules from FreeCAD FEM
        pymodules = []

        # collect all Python modules in FEM
        pymodules += testtools.collect_python_modules("")  # FEM main dir
        pymodules += testtools.collect_python_modules("femexamples")
        pymodules += testtools.collect_python_modules("feminout")
        pymodules += testtools.collect_python_modules("femmesh")
        pymodules += testtools.collect_python_modules("femobjects")
        pymodules += testtools.collect_python_modules("femresult")
        pymodules += testtools.collect_python_modules("femtest")
        pymodules += testtools.collect_python_modules("femtools")
        pymodules += testtools.collect_python_modules("femsolver")
        # TODO test with join on Windows, the use of os.path.join
        # in the following code seems to create problems on Windows OS
        pymodules += testtools.collect_python_modules("femsolver/elmer")
        pymodules += testtools.collect_python_modules("femsolver/elmer/equations")
        pymodules += testtools.collect_python_modules("femsolver/z88")
        pymodules += testtools.collect_python_modules("femsolver/calculix")
        if FreeCAD.GuiUp:
            pymodules += testtools.collect_python_modules("femcommands")
            pymodules += testtools.collect_python_modules("femguiobjects")
            pymodules += testtools.collect_python_modules("femguiutils")
            pymodules += testtools.collect_python_modules("femtaskpanels")
            pymodules += testtools.collect_python_modules("femviewprovider")

        # import all collected modules
        # fcc_print(pymodules)
        for mod in pymodules:
            if (
                mod == "femsolver.solver_taskpanel"
                or mod == "femexamples.examplesgui"
                or mod == "TestFemGui"
            ) and not FreeCAD.GuiUp:
                continue

            fcc_print(f"Try importing {mod} ...")
            try:
                im = __import__(f"{mod}")
            except ImportError as e:
                # check if it is a VTK module that is missing, because maybe we should not need it
                if "vtkmodules" in e.name and not "BUILD_FEM_VTK_PYTHON" in FreeCAD.__cmake__:
                    im = True
                else:
                    im = False
            if not im:
                # to get an error message what was going wrong
                __import__(f"{mod}")
            self.assertTrue(im, f"Problem importing {mod}")
