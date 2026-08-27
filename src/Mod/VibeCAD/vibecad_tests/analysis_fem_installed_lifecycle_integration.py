# SPDX-License-Identifier: LGPL-2.1-or-later

"""Installed-host exact-source lifecycle gate for detached FEM publication.

The gate uses deterministic synthetic result fields. It proves document
identity, stale-state refusal, object rebinding, and publication behavior; it
does not claim physical solver correctness.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback


VIBECAD_MODULE = Path(__file__).resolve().parents[1]
BUNDLED_SITE_PACKAGES = Path(sys.executable).resolve().parent / "Lib" / "site-packages"
for path in (BUNDLED_SITE_PACKAGES, VIBECAD_MODULE):
    text = str(path)
    if text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

import FreeCAD as App
import ObjectsFem

import tool_impl.analysis_fem_adapter as host_adapter
import VibeCADNativeAnalyzeSolverExecution as legacy
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSolverExecution import (
    CapturedSolverExecutionRequest,
    PreparedSolverExecution,
    SolverExecutionRequest,
)
from VibeCADNativeAnalyzeSolverState import PreparedSolverTarget, solver_state


def _result_grid():
    from vtkmodules.vtkCommonCore import vtkDoubleArray, vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkTetra, vtkUnstructuredGrid

    points = vtkPoints()
    for point in ((0, 0, 0), (10, 0, 0), (0, 10, 0), (0, 0, 10)):
        points.InsertNextPoint(*point)
    tetrahedron = vtkTetra()
    for index in range(4):
        tetrahedron.GetPointIds().SetId(index, index)
    grid = vtkUnstructuredGrid()
    grid.SetPoints(points)
    grid.InsertNextCell(tetrahedron.GetCellType(), tetrahedron.GetPointIds())
    displacement = vtkDoubleArray()
    displacement.SetName("Displacement")
    displacement.SetNumberOfComponents(3)
    for value in ((0, 0, 0), (0.1, 0, 0), (0, 0.2, 0), (0, 0, 0.3)):
        displacement.InsertNextTuple3(*value)
    grid.GetPointData().AddArray(displacement)
    return grid


class _DeterministicResultImporter:
    def __init__(self, solver) -> None:
        self.solver = solver

    def update_properties(self):
        from femcommands.manager import _stage_timeline_result_graph

        reconciliation = _stage_timeline_result_graph(self.solver)
        document = self.solver.Document
        root = document.addObject("Fem::FemPostPipeline", self.solver.Name + "Result")
        root.Label = "Installed FEM lifecycle result"
        root.Data = _result_grid()
        output = document.addObject("App::TextDocument", self.solver.Name + "Output")
        output.Label = "Installed FEM lifecycle output"
        output.Text = "deterministic installed-host lifecycle evidence"
        analysis = self.solver.getParentGroup()
        analysis.addObject(root)
        analysis.addObject(output)
        self.solver.Results = list(self.solver.Results) + [root, output]
        return root, (output,), True, reconciliation


def _create_fixture(path: Path):
    document = App.newDocument("FemInstalledLifecycle")
    document.UndoMode = 1
    document.openTransaction("Create installed FEM lifecycle fixture")
    try:
        analysis = ObjectsFem.makeAnalysis(document, "Analysis")
        solver = ObjectsFem.makeSolverCalculiX(document, "LifecycleSolver")
        solver.Label = "Installed lifecycle CalculiX solver"
        analysis.addObject(solver)
        from femcommands import manager

        manager._mark_timeline_operation(solver)
        document.publishProvisionalTimelineOperationBlock(solver, (), ())
        document.recompute([solver, analysis], True, True)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    document.saveAs(str(path))
    state = solver_state(solver)
    target = PreparedSolverTarget(
        solver,
        "calculix",
        state["state_sha256"],
    )
    history = tuple(document.VibeCADTimeline.Operations)
    preferences = legacy._current_solver_runtime_preferences("calculix")
    request = SolverExecutionRequest(
        target=target,
        implementation=str(state["implementation"]),
        history_operations=history,
        working_directory=tempfile.gettempdir(),
        commands=(),
        environment={},
        timeout_seconds=60,
        input_sha256="a" * 64,
        input_file_count=1,
        keep_results=legacy._keep_results_from_runtime_preferences(preferences),
        importer_state={"result_format": "deterministic-installed-lifecycle"},
        runtime_preferences=preferences,
    )
    prepared = PreparedSolverExecution(
        request,
        ({"stage": 1, "program": "installed-lifecycle-fixture", "exit_code": 0},),
    )
    completed = host_adapter.CompletedFEMSolverExecution(
        analysis=host_adapter._completed_contract(
            request,
            document_uid=str(document.Uid),
        ),
        legacy_prepared=prepared,
    )
    captured = CapturedSolverExecutionRequest(
        target,
        history,
        60,
        request.keep_results,
        preferences,
    )
    identity = {
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "solver_name": str(solver.Name),
        "solver_id": int(solver.ID),
        "state_sha256": str(state["state_sha256"]),
        "history_identity": [list(item) for item in request.history_identity],
    }
    document.save()
    App.closeDocument(document.Name)
    return captured, completed, identity


def _expect_stale(name: str, operation) -> dict:
    try:
        operation()
    except NativeAnalyzeError as exc:
        assert exc.error_code == "NATIVE_ANALYZE_STATE_STALE", (name, exc.failure())
        return {"case": name, "error_code": exc.error_code, "refused": True}
    raise AssertionError(f"{name} did not refuse stale FEM publication")


def _close(document) -> None:
    if document is not None and str(document.Name) in App.listDocuments():
        App.closeDocument(document.Name)


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="vibecad-installed-fem-lifecycle-") as temp:
        save_path = Path(temp) / "fem-installed-lifecycle.FCStd"
        captured, completed, identity = _create_fixture(save_path)
        refusals = []

        refusals.append(
            _expect_stale(
                "closed_source",
                lambda: host_adapter.rebind_completed_solver_execution(None, completed),
            )
        )

        switched = App.newDocument("FemInstalledLifecycleSwitched")
        try:
            refusals.append(
                _expect_stale(
                    "switched_document",
                    lambda: host_adapter.rebind_completed_solver_execution(
                        switched, completed
                    ),
                )
            )
        finally:
            _close(switched)

        replacement = App.newDocument(identity["document_name"])
        try:
            assert str(replacement.Uid) != identity["document_uid"]
            refusals.append(
                _expect_stale(
                    "same_name_replacement_uid",
                    lambda: host_adapter.rebind_completed_solver_execution(
                        replacement, completed
                    ),
                )
            )
        finally:
            _close(replacement)

        stale_solver = App.openDocument(str(save_path))
        try:
            stale_solver.getObject(identity["solver_name"]).Label += " changed"
            stale_solver.recompute()
            refusals.append(
                _expect_stale(
                    "solver_state_changed",
                    lambda: legacy.rebind_captured_solver_execution(
                        stale_solver,
                        identity["document_uid"],
                        captured,
                    ),
                )
            )
        finally:
            _close(stale_solver)

        stale_history = App.openDocument(str(save_path))
        try:
            extra = stale_history.addObject("App::FeaturePython", "UnexpectedHistory")
            stale_history.VibeCADTimeline.Operations = list(
                stale_history.VibeCADTimeline.Operations
            ) + [extra]
            stale_history.recompute()
            refusals.append(
                _expect_stale(
                    "history_changed",
                    lambda: legacy.rebind_captured_solver_execution(
                        stale_history,
                        identity["document_uid"],
                        captured,
                    ),
                )
            )
        finally:
            _close(stale_history)

        preference_group = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Fem/General"
        )
        original_keep = preference_group.GetBool("KeepResultsOnReRun", False)
        preference_group.SetBool("KeepResultsOnReRun", not original_keep)
        stale_preference = App.openDocument(str(save_path))
        try:
            refusals.append(
                _expect_stale(
                    "runtime_preference_changed",
                    lambda: legacy.rebind_captured_solver_execution(
                        stale_preference,
                        identity["document_uid"],
                        captured,
                    ),
                )
            )
        finally:
            _close(stale_preference)
            preference_group.SetBool("KeepResultsOnReRun", original_keep)

        reopened = App.openDocument(str(save_path))
        original_importer = legacy._import_tool
        try:
            assert str(reopened.Uid) == identity["document_uid"]
            rebound_capture = legacy.rebind_captured_solver_execution(
                reopened,
                identity["document_uid"],
                captured,
            )
            legacy.validate_captured_solver_execution(reopened, rebound_capture)
            rebound_completed = host_adapter.rebind_completed_solver_execution(
                reopened,
                completed,
            )
            legacy._import_tool = lambda request: _DeterministicResultImporter(
                request.target.solver
            )
            reopened.openTransaction("Publish exact reopened FEM source")
            try:
                draft = host_adapter.commit_solver_execution(
                    reopened,
                    rebound_completed,
                )
                reopened.recompute(list(draft.recompute_targets), True, True)
                public = dict(host_adapter.verify_solver_execution(reopened, draft))
                reopened.commitTransaction()
            except Exception:
                reopened.abortTransaction()
                raise
            rebound_solver = rebound_completed.legacy_prepared.request.target.solver
            publication = {
                "rebound": rebound_solver.Document is reopened,
                "document_uid": str(reopened.Uid),
                "solver_name": str(rebound_solver.Name),
                "solver_id": int(rebound_solver.ID),
                "state_sha256": identity["state_sha256"],
                "result_name": str(draft.value["root"].Name),
                "resource_count": len(tuple(draft.value["resources"])),
                "claim_ceiling": str(public["claim_ceiling"]),
                "qualified": bool(public["qualified"]),
            }
            assert publication["rebound"] is True
            assert publication["solver_id"] == identity["solver_id"]
            assert publication["resource_count"] == 1
            assert publication["claim_ceiling"] == "model_unqualified"
            assert publication["qualified"] is False
        finally:
            legacy._import_tool = original_importer
            _close(reopened)

        assert [item["case"] for item in refusals] == [
            "closed_source",
            "switched_document",
            "same_name_replacement_uid",
            "solver_state_changed",
            "history_changed",
            "runtime_preference_changed",
        ]
        return {
            "runtime": "installed-freecadcmd",
            "synthetic_result_fields": True,
            "physical_solver_validation": False,
            "source": identity,
            "refusals": refusals,
            "publication": publication,
        }


if __name__ == "__main__":
    try:
        report = run()
        print(
            "VIBECAD_ANALYSIS_FEM_INSTALLED_LIFECYCLE_OK "
            + json.dumps(report, sort_keys=True, separators=(",", ":")),
            flush=True,
        )
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
        raise
