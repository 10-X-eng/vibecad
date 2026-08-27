# SPDX-License-Identifier: LGPL-2.1-or-later

"""Installed FreeCADCmd A/B gate for all migrated FEM publication paths.

This gate uses deterministic synthetic result fields. It proves host-document
publication behavior; it does not claim physical solver correctness.
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

from analysis_fem_publication_parity import (
    PUBLICATION_PARITY_DIMENSIONS,
    SUPPORTED_SOLVERS,
    assert_publication_parity,
)
import tool_impl.analysis_fem_adapter as host_adapter
import VibeCADNativeAnalyzeSolverExecution as legacy
from VibeCADNativeAnalyzeSolverExecution import (
    PreparedSolverExecution,
    SolverExecutionRequest,
)
from VibeCADNativeAnalyzeSolverState import PreparedSolverTarget, solver_state


_FACTORIES = {
    "calculix": ObjectsFem.makeSolverCalculiX,
    "elmer": ObjectsFem.makeSolverElmer,
    "z88": ObjectsFem.makeSolverZ88,
    "mystran": ObjectsFem.makeSolverMystran,
}


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
        root.Label = "Installed FEM parity result"
        root.Data = _result_grid()
        output = document.addObject("App::TextDocument", self.solver.Name + "Output")
        output.Label = "Installed FEM parity output"
        output.Text = "deterministic installed-host publication evidence"
        analysis = self.solver.getParentGroup()
        analysis.addObject(root)
        analysis.addObject(output)
        results = list(self.solver.Results)
        results.extend((root, output))
        self.solver.Results = results
        return root, (output,), True, reconciliation


def _create_solver(document, kind: str):
    document.openTransaction("Create installed FEM publication fixture")
    try:
        analysis = ObjectsFem.makeAnalysis(document, "Analysis")
        solver = _FACTORIES[kind](document, "PublicationParitySolver")
        solver.Label = f"Publication parity {kind} solver"
        analysis.addObject(solver)
        from femcommands import manager

        manager._mark_timeline_operation(solver)
        document.publishProvisionalTimelineOperationBlock(solver, (), ())
        document.recompute([solver, analysis], True, True)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return solver


def _request(document, solver, kind: str) -> SolverExecutionRequest:
    state = solver_state(solver)
    return SolverExecutionRequest(
        target=PreparedSolverTarget(solver, kind, state["state_sha256"]),
        implementation=str(state["implementation"]),
        history_operations=tuple(document.VibeCADTimeline.Operations),
        working_directory=tempfile.gettempdir(),
        commands=(),
        environment={},
        timeout_seconds=60,
        input_sha256="a" * 64,
        input_file_count=1,
        keep_results=legacy._current_keep_results(),
        importer_state={"result_format": "deterministic-installed-host"},
    )


def _contains_receipt(value) -> bool:
    if isinstance(value, dict):
        return any(
            "receipt" in str(key).lower() or _contains_receipt(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_receipt(item) for item in value)
    return False


def _publish(kind: str, *, host: bool, root: Path) -> dict:
    document = App.newDocument("FemPublicationParity")
    output = root / f"{kind}-{'host' if host else 'legacy'}.FCStd"
    original_importer = legacy._import_tool
    try:
        document.UndoMode = 1
        document.saveAs(str(output))
        solver = _create_solver(document, kind)
        request = _request(document, solver, kind)
        prepared = PreparedSolverExecution(
            request,
            ({"stage": 1, "program": f"{kind}-fixture", "exit_code": 0},),
        )
        legacy._import_tool = lambda actual_request: _DeterministicResultImporter(
            actual_request.target.solver
        )
        document.openTransaction("Installed FEM publication parity")
        try:
            if host:
                completed = host_adapter.CompletedFEMSolverExecution(
                    analysis=host_adapter._completed_contract(
                        request, document_uid="installed-publication-parity"
                    ),
                    legacy_prepared=prepared,
                )
                draft = host_adapter.commit_solver_execution(document, completed)
            else:
                draft = legacy.commit_solver_execution(document, prepared)
            document.recompute(list(draft.recompute_targets), True, True)
            public = dict(
                host_adapter.verify_solver_execution(document, draft)
                if host
                else legacy.verify_solver_execution(document, draft)
            )
            document.commitTransaction()
        except Exception:
            document.abortTransaction()
            raise

        result = draft.value["root"]
        resources = tuple(draft.value["resources"])
        solver_name = str(solver.Name)
        result_name = str(result.Name)
        resource_names = tuple(str(item.Name) for item in resources)
        timeline = tuple(document.VibeCADTimeline.Operations)
        solver_index = timeline.index(solver)
        history_block = timeline[solver_index - len(resources) - 1 : solver_index + 1]
        evidence = {
            "solver": solver_name,
            "solver_id": int(solver.ID),
            "root": result_name,
            "root_id": int(result.ID),
            "resources": [
                {
                    "object_name": str(item.Name),
                    "object_id": int(item.ID),
                    "type_id": str(item.TypeId),
                }
                for item in resources
            ],
            "result_membership": [str(item.Name) for item in tuple(solver.Results)],
            "history_block": [str(item.Name) for item in history_block],
            "ownership": [
                "solver" if result.VibeCADTimelineOwner is solver else "invalid",
                *(
                    "root" if item.VibeCADTimelineOwner is result else "invalid"
                    for item in resources
                ),
            ],
            "input_sha256": request.input_sha256,
            "state_sha256": request.target.expected_state_sha256,
            "receipt": "present" if _contains_receipt(public) else None,
            "public": public,
            "reopened": False,
        }
        document.recompute()
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(output))
        reopened_solver = document.getObject(solver_name)
        reopened_result = document.getObject(result_name)
        reopened_resources = tuple(
            document.getObject(name) for name in resource_names
        )
        evidence["reopened"] = bool(
            reopened_solver
            and reopened_result
            and all(reopened_resources)
            and reopened_result.VibeCADTimelineOwner is reopened_solver
            and all(
                item.VibeCADTimelineOwner is reopened_result
                for item in reopened_resources
            )
            and reopened_result in tuple(reopened_solver.Results)
        )
        return evidence
    finally:
        legacy._import_tool = original_importer
        if document.Name in App.listDocuments():
            App.closeDocument(document.Name)


def run() -> dict:
    with tempfile.TemporaryDirectory(
        prefix="vibecad-installed-fem-publication-parity-"
    ) as temporary:
        root = Path(temporary)
        evidence = {}
        for kind in SUPPORTED_SOLVERS:
            legacy_evidence = _publish(kind, host=False, root=root)
            host_evidence = _publish(kind, host=True, root=root)
            evidence[kind] = assert_publication_parity(
                legacy_evidence, host_evidence, solver_key=kind
            )
        return {
            "runtime": "installed-freecadcmd",
            "synthetic_result_fields": True,
            "physical_solver_validation": False,
            "dimensions": list(PUBLICATION_PARITY_DIMENSIONS),
            "solvers": evidence,
        }


if __name__ == "__main__":
    try:
        report = run()
        print(
            "VIBECAD_ANALYSIS_FEM_INSTALLED_PUBLICATION_OK "
            + json.dumps(report, sort_keys=True, separators=(",", ":")),
            flush=True,
        )
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
        raise
