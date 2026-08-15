# SPDX-License-Identifier: LGPL-2.1-or-later

"""Detached, shell-free Mystran preparation and exact result import."""

from __future__ import annotations

import os

import FreeCAD

from femmesh import meshsetsgetter
from femtools import membertools

from . import tasks
from . import writer


class MystranTools:
    """Prepare and import Mystran artifacts without running its task machine."""

    def __init__(self, solver, *, working_directory):
        self.obj = solver
        self.analysis = solver.getParentGroup()
        self.working_directory = str(working_directory)
        self.input_deck = ""
        self.fem_param = FreeCAD.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Fem"
        )

    def prepare(self):
        mesh = membertools.get_mesh_to_solve(self.analysis)
        mesh_data = meshsetsgetter.MeshSetsGetter(
            self.analysis,
            self.obj,
            mesh,
            membertools.AnalysisMember(self.analysis),
        )
        mesh_data.get_mesh_sets()
        input_writer = writer.FemInputWriterMystran(
            self.analysis,
            self.obj,
            mesh,
            mesh_data.member,
            self.working_directory,
        )
        path = input_writer.write_solver_input()
        if not path or not os.path.isfile(path):
            raise RuntimeError("The Mystran input writer produced no BDF file")
        self.input_deck = os.path.splitext(os.path.basename(path))[0]
        return path

    def update_properties(self):
        keep_results = self.fem_param.GetGroup("General").GetBool(
            "KeepResultsOnReRun",
            False,
        )
        from femcommands.manager import _stage_timeline_result_graph

        replacement_roots = (
            tuple(
                result
                for result in tuple(self.obj.Results or ())
                if getattr(result, "VibeCADTimelineOwner", None) is self.obj
            )
            if not keep_results
            else ()
        )
        reconciliation = _stage_timeline_result_graph(
            self.obj,
            replacement_roots=replacement_roots,
        )
        root, resources = tasks.import_result_graph(
            self.working_directory,
            self.input_deck,
            self.analysis,
        )
        solver_results = list(self.obj.Results or ())
        if root not in solver_results:
            solver_results.append(root)
            self.obj.Results = solver_results
        return root, resources, True, reconciliation
