# ***************************************************************************
# *   Copyright (c) 2021 Bernd Hahnebach <bernd@bimstatik.org>              *
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

__title__ = "FreeCAD FEM solver Mystran tasks"
__author__ = "Bernd Hahnebach"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import os
import os.path
import subprocess

import FreeCAD

try:
    import hfcMystranNeuIn

    result_reading = True
except Exception:
    FreeCAD.Console.PrintWarning("Module to read results not found.\n")
    result_reading = False


from . import writer
from .. import run
from .. import settings
from femmesh import meshsetsgetter
from femtools import femutils
from femtools import membertools

_inputFileName = None


class Check(run.Check):

    def run(self):
        self.pushStatus("Checking analysis member...\n")
        self.check_mesh_exists()
        self.check_material_exists()
        self.check_material_single()  # no multiple material
        self.check_geos_beamsection_single()  # no multiple beamsection
        self.check_geos_shellthickness_single()  # no multiple shellsection
        self.check_geos_beamsection_and_shellthickness()  # either beams or shells


class Prepare(run.Prepare):

    def run(self):
        global _inputFileName
        self.pushStatus("Preparing solver input...\n")

        # get mesh set data
        # TODO see calculix tasks get mesh set data
        mesh_obj = membertools.get_mesh_to_solve(self.analysis)[0]  # pre check done already
        meshdatagetter = meshsetsgetter.MeshSetsGetter(
            self.analysis,
            self.solver,
            mesh_obj,
            membertools.AnalysisMember(self.analysis),
        )
        meshdatagetter.get_mesh_sets()

        # write solver input
        w = writer.FemInputWriterMystran(
            self.analysis,
            self.solver,
            mesh_obj,
            meshdatagetter.member,
            self.directory,
        )
        path = w.write_solver_input()
        # report to user if task succeeded
        if path != "":
            self.pushStatus("Writing solver input completed.")
        else:
            self.pushStatus("Writing solver input failed.")
            self.fail()
        _inputFileName = os.path.splitext(os.path.basename(path))[0]


class Solve(run.Solve):

    def run(self):
        self.pushStatus("Executing solver...\n")

        infile = _inputFileName + ".bdf"

        # get solver binary
        self.pushStatus("Get solver binary...\n")
        try:
            binary = settings.require_binary("Mystran")
        except settings.SolverExecutableNotFoundError as exc:
            self.report.error(str(exc))
            self.pushStatus(str(exc) + "\n")
            self.fail()
            return

        # run solver
        self.pushStatus("Executing solver...\n")
        self._process = subprocess.Popen(
            args=[binary, infile],  # pass empty param fails! [binary, "", infile]
            cwd=self.directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.signalAbort.add(self._process.terminate)
        self._process.communicate()
        self.signalAbort.remove(self._process.terminate)

        # for chatching the output see CalculiX or Elmer solver tasks module


class Results(run.Results):

    def run(self):
        prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/General")
        keep_results = prefs.GetBool("KeepResultsOnReRun", False)
        if not FreeCAD.GuiUp:
            if not keep_results:
                self.purge_results()
            if result_reading is True:
                self.load_results()
            return

        document = self.analysis.Document
        if any(
            candidate.getBookedTransactionID() != 0
            or candidate.HasPendingTransaction
            for candidate in FreeCAD.listDocuments().values()
        ):
            raise RuntimeError(
                "Mystran results cannot be imported while another "
                "document transaction is active"
            )

        transaction_id = 0
        try:
            document.openTransaction("Import Mystran solver results")
            transaction_id = int(document.getBookedTransactionID())
            if transaction_id == 0:
                raise RuntimeError(
                    "Could not open the Mystran result import transaction"
                )
            if not keep_results:
                self.purge_results()
            if result_reading is not True:
                raise RuntimeError(
                    "The Mystran result importer is unavailable"
                )
            result_graph = self.load_results()
            if result_graph is None:
                raise RuntimeError(
                    "The Mystran result importer returned no result graph"
                )
            root, resources = result_graph
            from femcommands.manager import (
                _finalize_timeline_result_graph,
            )

            _finalize_timeline_result_graph(
                self.solver,
                root,
                resources,
            )
            solver_results = list(self.solver.Results)
            if root not in solver_results:
                solver_results.append(root)
                self.solver.Results = solver_results
            document.recompute()
            FreeCAD.closeActiveTransaction(False, transaction_id)
            transaction_id = 0
        except Exception:
            if (
                transaction_id
                and document.getBookedTransactionID()
                == transaction_id
            ):
                FreeCAD.closeActiveTransaction(True, transaction_id)
            raise

    def purge_results(self):
        self.pushStatus("Purge existing results...\n")
        # TODO see calculix result tasks
        for m in membertools.get_member(self.analysis, "Fem::FemResultObject"):
            if femutils.is_of_type(m.Mesh, "Fem::MeshResult"):
                self.analysis.Document.removeObject(m.Mesh.Name)
            self.analysis.Document.removeObject(m.Name)
        self.analysis.Document.recompute()

    def load_results(self):
        self.pushStatus("Import new results...\n")
        neu_result_file = os.path.join(self.directory, _inputFileName + ".NEU")
        if os.path.isfile(neu_result_file):
            document = self.analysis.Document
            imported = hfcMystranNeuIn.import_neu(neu_result_file)
            if (
                isinstance(imported, (tuple, list))
                and len(imported) == 2
            ):
                result, reported_resources = imported
            else:
                result = imported
                reported_resources = ()
            if (
                result is None
                or getattr(result, "Document", None) is not document
                or document.getObject(
                    getattr(result, "Name", "")
                )
                is not result
                or not document
                .isProvisionallyEnrolledInTimelineByCurrentTransaction(
                    result
                )
            ):
                raise RuntimeError(
                    "The Mystran importer must return its exact result "
                    "object"
                )

            resources = []
            for resource in reported_resources:
                if (
                    resource is result
                    or resource in resources
                    or getattr(resource, "Document", None) is not document
                    or document.getObject(
                        getattr(resource, "Name", "")
                    )
                    is not resource
                    or not document
                    .isProvisionallyEnrolledInTimelineByCurrentTransaction(
                        resource
                    )
                ):
                    raise RuntimeError(
                        "The Mystran importer returned an invalid exact "
                        "result resource"
                    )
                resources.append(resource)

            result_mesh = getattr(result, "Mesh", None)
            if (
                result_mesh is not None
                and getattr(result_mesh, "Document", None) is document
                and document.getObject(
                    getattr(result_mesh, "Name", "")
                )
                is result_mesh
                and document
                .isProvisionallyEnrolledInTimelineByCurrentTransaction(
                    result_mesh
                )
                and result_mesh not in resources
            ):
                resources.append(result_mesh)

            self.analysis.addObject(result)
            if result not in self.analysis.Group:
                raise RuntimeError(
                    "The Mystran result was not added to its analysis"
                )
            return result, tuple(resources)
        else:
            # TODO: use solver framework status message system
            FreeCAD.Console.PrintError(f"FEM: No results found at {neu_result_file}!\n")
            self.fail()
        return None


##  @}
