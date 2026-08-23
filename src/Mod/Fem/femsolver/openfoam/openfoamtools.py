# SPDX-License-Identifier: LGPL-2.1-or-later

"""Import detached OpenFOAM results into durable FEM result objects."""

from __future__ import annotations

from pathlib import Path

import FreeCAD


class OpenFOAMTools:
    def __init__(self, solver, working_directory, *, result_glob, solver_log):
        self.obj = solver
        self.working_directory = Path(working_directory)
        self.result_glob = str(result_glob)
        self.solver_log = str(solver_log)
        self.fem_param = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem")

    def update_properties(self):
        keep_results = self.fem_param.GetGroup("General").GetBool(
            "KeepResultsOnReRun", False
        )
        pipeline = None
        if not keep_results:
            pipeline = next(
                (
                    result
                    for result in tuple(self.obj.Results or ())
                    if result.isDerivedFrom("Fem::FemPostPipeline")
                ),
                None,
            )
        from femcommands.manager import _stage_timeline_result_graph

        reconciliation = _stage_timeline_result_graph(self.obj, pipeline)
        pipeline_created = pipeline is None
        document = self.obj.Document
        analysis = self.obj.getParentGroup()
        if pipeline_created:
            pipeline = document.addObject(
                "Fem::FemPostPipeline", self.obj.Name + "Result"
            )
            pipeline.Label = "OpenFOAM Flow Result"
            analysis.addObject(pipeline)
            results = list(self.obj.Results or ())
            results.append(pipeline)
            self.obj.Results = results

        result_files = tuple(sorted(self.working_directory.glob(self.result_glob)))
        if len(result_files) != 1:
            raise RuntimeError(
                "OpenFOAM result export must produce exactly one internal VTK file"
            )
        pipeline.read(str(result_files[0]))

        output = next(
            (
                candidate
                for candidate in tuple(analysis.Group or ())
                if candidate.isDerivedFrom("App::TextDocument")
                and getattr(candidate, "VibeCADTimelineOwner", None) is pipeline
            ),
            None,
        )
        output_created = output is None
        if output_created:
            output = document.addObject("App::TextDocument", self.obj.Name + "Output")
            output.Label = "OpenFOAM Solver Output"
            analysis.addObject(output)
        log_path = self.working_directory / self.solver_log
        output.Text = log_path.read_text(encoding="utf-8", errors="replace")

        if FreeCAD.GuiUp and pipeline_created:
            pipeline.ViewObject.DisplayMode = "Surface"
            pipeline.ViewObject.SelectionStyle = "BoundBox"
            fields = pipeline.ViewObject.getEnumerationsOfProperty("Field")
            if "U" in fields:
                pipeline.ViewObject.Field = "U"
        resources = (output,) if output_created else ()
        return pipeline, resources, pipeline_created, reconciliation
