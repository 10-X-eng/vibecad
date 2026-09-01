# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native production gate for the canonical FEM VibeScript domain."""

from __future__ import annotations

import copy
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

MODULE_ROOT = Path(__file__).resolve().parent.parent
while str(MODULE_ROOT) in sys.path:
    sys.path.remove(str(MODULE_ROOT))
sys.path.insert(0, str(MODULE_ROOT))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    FEMDomainAdapter,
    accept_candidate,
    capture_reference_inputs,
    execute_candidate,
    finish_delete,
    finalize_candidate,
    prepare_candidate,
    prepare_delete,
    retain_candidate,
    restore_prepared_delete,
    validate_candidate,
)
import VibeCADVibeScriptDomainPublication as publication_module  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    PROP_FEM_VALIDATION,
    delete_live_program,
    publish_candidate,
)
from VibeCADVibeScriptDomains import (  # noqa: E402
    PROP_PROGRAM_ID,
    complete_domain_context,
    domain_context_snapshot,
    get_vibescript_pack,
)
from vibescript_fem_api import FEMAPIError, FEMDomainAPI  # noqa: E402
from vibescript_fem_worker import (  # noqa: E402
    FEMCandidateError,
    _mesh_topology,
    validate_and_build_fem,
    validate_fem_definition,
)


EXPORTS = (
    "analysis",
    "solver",
    "material",
    "constraint",
    "load_case",
    "mesh",
    "solve",
)
OUTPUT_TYPES = (
    "analysis",
    "solver",
    "material",
    "constraint",
    "load_case",
    "mesh",
    "result",
)
EXPECTED_OUTPUTS = [
    {"name": "Analysis", "type": "analysis"},
    {"name": "Solver", "type": "solver"},
    {"name": "Material", "type": "material"},
    {"name": "MaterialRemainder", "type": "material"},
    {"name": "Fixed", "type": "constraint"},
    {"name": "Force", "type": "constraint"},
    {"name": "LoadCase", "type": "load_case"},
    {"name": "Mesh", "type": "mesh"},
    {"name": "Result", "type": "result"},
]
MULTI_STUDY_EXPECTED_OUTPUTS = [
    {"name": "StudyASolver", "type": "solver"},
    {"name": "StudyAMaterial", "type": "material"},
    {"name": "StudyAFixed", "type": "constraint"},
    {"name": "StudyAForce", "type": "constraint"},
    {"name": "StudyALoadCase", "type": "load_case"},
    {"name": "StudyAMesh", "type": "mesh"},
    {"name": "StudyAAnalysis", "type": "analysis"},
    {"name": "StudyAResult", "type": "result"},
    {"name": "StudyBSolver", "type": "solver"},
    {"name": "StudyBMaterial", "type": "material"},
    {"name": "StudyBFixed", "type": "constraint"},
    {"name": "StudyBForce", "type": "constraint"},
    {"name": "StudyBLoadCase", "type": "load_case"},
    {"name": "StudyBMesh", "type": "mesh"},
    {"name": "StudyBAnalysis", "type": "analysis"},
    {"name": "StudyBResult", "type": "result"},
    {"name": "StudyCSolver", "type": "solver"},
    {"name": "StudyCMaterial", "type": "material"},
    {"name": "StudyCFixed", "type": "constraint"},
    {"name": "StudyCForce", "type": "constraint"},
    {"name": "StudyCLoadCase", "type": "load_case"},
    {"name": "StudyCMesh", "type": "mesh"},
    {"name": "StudyCAnalysis", "type": "analysis"},
    {"name": "StudyCResult", "type": "result"},
]


class _Service:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _active_document():
        return App.ActiveDocument

    @staticmethod
    def active_workbench_name() -> str:
        return "FemWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "fem-native-fixture-revision"

    def project_scope_snapshot(self) -> dict[str, str]:
        return {"root": str(self.root), "project_id": "fem-native-fixture"}

    @staticmethod
    def provider_working_set() -> dict[str, object]:
        return {"target_count": 0, "targets": []}

    @staticmethod
    def selection_summary() -> dict[str, object]:
        return {"selection": []}


def _api() -> FEMDomainAPI:
    return FEMDomainAPI(EXPORTS, OUTPUT_TYPES)


def _expect_error(fragment: str, call) -> None:
    try:
        call()
    except (TypeError, ValueError, RuntimeError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f"Expected FEM failure containing {fragment!r}.")


def _expect_candidate_error(stage: str, call) -> FEMCandidateError:
    try:
        call()
    except FEMCandidateError as exc:
        assert exc.details.get("stage") == stage, exc.details
        assert str(exc.details.get("correction") or "").strip(), exc.details
        return exc
    raise AssertionError(f"Expected FEM candidate failure at {stage!r}.")


def _exercise_source_api(document_uid: str) -> None:
    api = _api()
    assert api.exported_names == EXPORTS
    for redundant in (
        "output",
        "fixed",
        "force",
        "pressure",
        "gmsh",
        "calculix",
        "validate",
        "run_solver",
    ):
        assert not hasattr(api, redundant)
    for name in EXPORTS:
        signature = str(inspect.signature(getattr(api, name)))
        assert "*args" not in signature and "**" not in signature
        assert inspect.getdoc(getattr(api, name))
    assert "analysis_type" not in str(inspect.signature(api.solver))
    reference = {"document_uid": document_uid, "object_name": "SourceSolid"}
    solver = api.solver()
    material = api.material(
        name="Steel",
        youngs_modulus_mpa=210000,
        poisson_ratio=0.3,
        density_kg_m3=7850,
        assignments=[
            {
                "target": reference,
                "selection": {"type": "subelement", "name": "Solid1"},
            }
        ],
    )
    remainder = api.material(
        name="Aluminium",
        youngs_modulus_mpa=69000,
        poisson_ratio=0.33,
        density_kg_m3=2700,
    )
    fixed = api.constraint(
        "fixed", reference, {"type": "subelement", "name": "Face1"}
    )
    force = api.constraint(
        "force",
        reference,
        {"type": "subelement", "name": "Face2"},
        magnitude=1000,
        direction=[1, 0, 0],
    )
    load_case = api.load_case([fixed, force])
    mesh = api.mesh(
        reference,
        method="inline",
        nodes=[
            [0, 0, 0],
            [10, 0, 0],
            [10, 8, 0],
            [0, 8, 0],
            [0, 0, 6],
            [10, 0, 6],
            [10, 8, 6],
            [0, 8, 6],
        ],
        elements=[
            [0, 1, 3, 4],
            [1, 2, 3, 6],
            [1, 3, 4, 6],
            [1, 4, 5, 6],
            [3, 4, 6, 7],
        ],
        element_type="tetra4",
        order=1,
    )
    analysis = api.analysis(solver, [material, remainder], [load_case], mesh)
    result = api.solve(analysis, execution="validate_only")
    assert [
        validate_fem_definition(value)["operation"]
        for value in (
            solver,
            material,
            remainder,
            fixed,
            load_case,
            mesh,
            analysis,
            result,
        )
    ] == [
        "solver",
        "material",
        "material",
        "constraint",
        "load_case",
        "mesh",
        "analysis",
        "solve",
    ]
    _expect_error(
        "cannot set magnitude",
        lambda: api.constraint(
            "fixed",
            reference,
            {"type": "subelement", "name": "Face1"},
            magnitude=1,
        ),
    )
    _expect_error(
        "cannot receive inline",
        lambda: api.mesh(
            reference,
            method="gmsh",
            nodes=[[0, 0, 0]],
            maximum_size=1,
        ),
    )
    _expect_error(
        "cannot contain duplicate",
        lambda: api.load_case([fixed, fixed]),
    )
    try:
        api.material(
            name="Invalid",
            youngs_modulus_mpa=0,
            poisson_ratio=0.3,
            density_kg_m3=1,
        )
    except FEMAPIError as exc:
        assert exc.details["stage"] == "source_validation"
        assert exc.details["operation"] == "material"
        assert exc.details["parameter"] == "youngs_modulus_mpa"
        assert "Change only the failing source expression" in exc.details["correction"]
    else:
        raise AssertionError("Expected one structured FEM source failure.")
    pack = get_vibescript_pack("FemWorkbench")
    assert pack is not None
    description = FEMDomainAdapter(pack).describe_api()
    encoded_description = json.dumps(
        description,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded_description) < 32 * 1024
    assert description["operation_selection"]["load_case"].endswith(
        "alternative scenario."
    )
    assert "solver_executed=false" in description["solve_and_evidence_contract"][
        "validate_only"
    ]
    assert "one material unassigned as the remainder" in description[
        "material_contract"
    ]["multiple_materials"]
    assert "active workbench determines" in description[
        "workbench_handoffs"
    ]["rule"]
    assert "fixed to a static" in description["solver_contract"]["analysis_type"]
    document = App.newDocument("VibeCADFEMResultContract", "FEM result gate", True, True)
    try:
        _expect_candidate_error(
            "result_contract",
            lambda: validate_and_build_fem(
                document,
                {"Solver": solver, "Unexpected": solver},
                [{"name": "Solver", "type": "solver"}],
                Path(tempfile.gettempdir()),
            ),
        )
    finally:
        App.closeDocument(document.Name)


def _input_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "solid": {
                "type": "object",
                "x-vibecad-reference": True,
                "properties": {
                    "document_uid": {"type": "string", "minLength": 1},
                    "object_name": {"type": "string", "minLength": 1},
                },
                "required": ["document_uid", "object_name"],
                "additionalProperties": False,
            },
            "force": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 1000000,
            },
        },
        "required": ["solid", "force"],
        "additionalProperties": False,
    }


def _creation_arguments(
    document,
    source_object,
    *,
    program_name: str,
    source: str,
    expected_outputs: list[dict[str, str]] = EXPECTED_OUTPUTS,
) -> dict[str, object]:
    return {
        "program_name": program_name,
        "source": source,
        "input_schema": _input_schema(),
        "inputs": {
            "solid": {
                "document_uid": str(document.Uid),
                "object_name": str(source_object.Name),
            },
            "force": 1000.0,
        },
        "expected_outputs": expected_outputs,
    }


def _multi_study_program_source() -> str:
    mesh = (
        "nodes=[[0,0,0],[10,0,0],[10,8,0],[0,8,0],"
        "[0,0,6],[10,0,6],[10,8,6],[0,8,6],"
        "[20,0,0],[30,0,0],[30,8,0],[20,8,0],"
        "[20,0,6],[30,0,6],[30,8,6],[20,8,6]], "
        "elements=[[0,1,2,3,4,5,6,7],[8,9,10,11,12,13,14,15]], "
        "element_type='hexa8', order=1"
    )
    return (
        "solver_a = api.solver(label='Study A solver')\n"
        "material_a = api.material(name='Study A steel', youngs_modulus_mpa=210000, "
        "poisson_ratio=0.3, density_kg_m3=7850, label='Study A material')\n"
        "fixed_a = api.constraint('fixed', inputs['solid'], "
        "{'type':'subelement','name':'Face1'}, label='Study A fixed')\n"
        "force_a = api.constraint('force', inputs['solid'], "
        "{'type':'subelement','name':'Face2'}, magnitude=inputs['force'], "
        "direction=[1,0,0], label='Study A force')\n"
        "case_a = api.load_case([fixed_a, force_a], label='Study A load case')\n"
        f"mesh_a = api.mesh(inputs['solid'], method='inline', {mesh}, "
        "label='Study A mesh')\n"
        "analysis_a = api.analysis(solver_a, [material_a], [case_a], mesh_a, "
        "label='Study A')\n"
        "result_a = api.solve(analysis_a, execution='validate_only', "
        "label='Study A result')\n"
        "solver_b = api.solver(reduced_integration=False, label='Study B solver')\n"
        "material_b = api.material(name='Study B aluminium', youngs_modulus_mpa=69000, "
        "poisson_ratio=0.33, density_kg_m3=2700, label='Study B material')\n"
        "fixed_b = api.constraint('fixed', inputs['solid'], "
        "{'type':'subelement','name':'Face1'}, label='Study B fixed')\n"
        "force_b = api.constraint('force', inputs['solid'], "
        "{'type':'subelement','name':'Face2'}, magnitude=inputs['force'] * 0.5, "
        "direction=[0,1,0], label='Study B force')\n"
        "case_b = api.load_case([fixed_b, force_b], label='Study B load case')\n"
        f"mesh_b = api.mesh(inputs['solid'], method='inline', {mesh}, "
        "label='Study B mesh')\n"
        "analysis_b = api.analysis(solver_b, [material_b], [case_b], mesh_b, "
        "label='Study B')\n"
        "result_b = api.solve(analysis_b, execution='validate_only', "
        "label='Study B result')\n"
        "solver_c = api.solver(reduced_integration=True, label='Study C solver')\n"
        "material_c = api.material(name='Study C titanium', youngs_modulus_mpa=116000, "
        "poisson_ratio=0.34, density_kg_m3=4500, label='Study C material')\n"
        "fixed_c = api.constraint('fixed', inputs['solid'], "
        "{'type':'subelement','name':'Face1'}, label='Study C fixed')\n"
        "force_c = api.constraint('force', inputs['solid'], "
        "{'type':'subelement','name':'Face2'}, magnitude=inputs['force'] * 0.25, "
        "direction=[0,0,1], label='Study C force')\n"
        "case_c = api.load_case([fixed_c, force_c], label='Study C load case')\n"
        f"mesh_c = api.mesh(inputs['solid'], method='inline', {mesh}, "
        "label='Study C mesh')\n"
        "analysis_c = api.analysis(solver_c, [material_c], [case_c], mesh_c, "
        "label='Study C')\n"
        "result_c = api.solve(analysis_c, execution='validate_only', "
        "label='Study C result')\n"
        "result = {'StudyASolver': solver_a, 'StudyAMaterial': material_a, "
        "'StudyAFixed': fixed_a, 'StudyAForce': force_a, "
        "'StudyALoadCase': case_a, 'StudyAMesh': mesh_a, "
        "'StudyAAnalysis': analysis_a, 'StudyAResult': result_a, "
        "'StudyBSolver': solver_b, 'StudyBMaterial': material_b, "
        "'StudyBFixed': fixed_b, 'StudyBForce': force_b, "
        "'StudyBLoadCase': case_b, 'StudyBMesh': mesh_b, "
        "'StudyBAnalysis': analysis_b, 'StudyBResult': result_b, "
        "'StudyCSolver': solver_c, 'StudyCMaterial': material_c, "
        "'StudyCFixed': fixed_c, 'StudyCForce': force_c, "
        "'StudyCLoadCase': case_c, 'StudyCMesh': mesh_c, "
        "'StudyCAnalysis': analysis_c, 'StudyCResult': result_c}\n"
    )


def _assert_multi_study_outputs(document, accepted: dict[str, object]) -> None:
    outputs = _outputs(document, accepted)
    assert set(outputs) == {
        item["name"] for item in MULTI_STUDY_EXPECTED_OUTPUTS
    }
    analysis_a = outputs["StudyAAnalysis"]
    analysis_b = outputs["StudyBAnalysis"]
    analysis_c = outputs["StudyCAnalysis"]
    assert len({analysis_a, analysis_b, analysis_c}) == 3
    for prefix, analysis in (("A", analysis_a), ("B", analysis_b), ("C", analysis_c)):
        assert set(analysis.Group) == {
            outputs[name]
            for name in (
                f"Study{prefix}Solver",
                f"Study{prefix}Material",
                f"Study{prefix}Fixed",
                f"Study{prefix}Force",
                f"Study{prefix}LoadCase",
                f"Study{prefix}Mesh",
                f"Study{prefix}Result",
            )
        }
    assert set(analysis_a.Group).isdisjoint(set(analysis_b.Group))
    assert set(analysis_a.Group).isdisjoint(set(analysis_c.Group))
    assert set(analysis_b.Group).isdisjoint(set(analysis_c.Group))
    assert outputs["StudyAResult"].VibeCADAnalysisObjectName == analysis_a.Name
    assert outputs["StudyBResult"].VibeCADAnalysisObjectName == analysis_b.Name
    assert outputs["StudyCResult"].VibeCADAnalysisObjectName == analysis_c.Name


def _exercise_multiple_independent_studies(
    root: Path,
    source_document,
    source_object,
    service: _Service,
) -> None:
    App.setActiveDocument(source_document.Name)
    captured = _captured(
        root,
        source_document,
        operation="create_program",
        arguments=_creation_arguments(
            source_document,
            source_object,
            program_name="Independent FEM studies fixture",
            source=_multi_study_program_source(),
            expected_outputs=MULTI_STUDY_EXPECTED_OUTPUTS,
        ),
    )
    prepared, execution, validated = _prepare_execute_validate(captured, service)
    assert execution.get("ok") is True, execution
    assert validated is not None
    assert execution["fem_validation"]["analysis_outputs"] == [
        "StudyAAnalysis",
        "StudyBAnalysis",
        "StudyCAnalysis",
    ]
    assert execution["fem_validation"]["result_outputs"] == [
        "StudyAResult",
        "StudyBResult",
        "StudyCResult",
    ]
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    _assert_multi_study_outputs(source_document, accepted)
    stable_names = {
        name: str(obj.Name) for name, obj in _outputs(source_document, accepted).items()
    }
    source_name = str(source_object.Name)
    source_document.undo()
    assert all(
        source_document.getObject(name) is None for name in stable_names.values()
    )
    assert source_document.getObject(source_name) is source_object
    source_document.redo()
    assert {
        name: str(obj.Name) for name, obj in _outputs(source_document, accepted).items()
    } == stable_names
    _assert_multi_study_outputs(source_document, accepted)
    save_path = root / "fem-multiple-independent-studies.FCStd"
    source_document.saveAs(str(save_path))
    App.closeDocument(source_document.Name)
    reopened = App.openDocument(str(save_path))
    assert reopened is not None
    try:
        _assert_multi_study_outputs(reopened, accepted)
        reopened.recompute()
        _assert_multi_study_outputs(reopened, accepted)
    finally:
        App.closeDocument(reopened.Name)


def _program_source(
    *,
    force_scale: float = 1.0,
    mesh_method: str = "inline",
    execution: str = "validate_only",
) -> str:
    if mesh_method == "inline":
        mesh_source = (
            "mesh = api.mesh(inputs['solid'], method='inline', "
            "nodes=[[0,0,0],[10,0,0],[10,8,0],[0,8,0],"
            "[0,0,6],[10,0,6],[10,8,6],[0,8,6],"
            "[20,0,0],[30,0,0],[30,8,0],[20,8,0],"
            "[20,0,6],[30,0,6],[30,8,6],[20,8,6]], "
            "elements=[[0,1,2,3,4,5,6,7],[8,9,10,11,12,13,14,15]], "
            "element_type='hexa8', order=1, "
            "label='Mesh')\n"
        )
    elif mesh_method == "gmsh":
        mesh_source = (
            "mesh = api.mesh(inputs['solid'], method='gmsh', "
            "maximum_size=2.0, minimum_size=0.2, order=1, label='Mesh')\n"
        )
    else:
        raise AssertionError(f"Unsupported FEM fixture mesh method {mesh_method!r}.")
    if execution not in {"validate_only", "calculix"}:
        raise AssertionError(f"Unsupported FEM fixture execution {execution!r}.")
    return (
        "solver = api.solver(label='CalculiX')\n"
        "material = api.material(name='Steel', youngs_modulus_mpa=210000, "
        "poisson_ratio=0.3, density_kg_m3=7850, "
        "assignments=[{'target':inputs['solid'], "
        "'selection':{'type':'subelement','name':'Solid1'}}], label='Steel')\n"
        "material_remainder = api.material(name='Aluminium', "
        "youngs_modulus_mpa=69000, poisson_ratio=0.33, density_kg_m3=2700, "
        "label='Aluminium remainder')\n"
        "fixed = api.constraint('fixed', inputs['solid'], "
        "{'type':'subelement','name':'Face1'}, label='Fixed')\n"
        "force = api.constraint('force', inputs['solid'], "
        "{'type':'subelement','name':'Face2'}, "
        f"magnitude=inputs['force'] * {force_scale!r}, direction=[1,0,0], "
        "label='Force')\n"
        "case = api.load_case([fixed, force], label='Load Case')\n"
        + mesh_source
        + "analysis = api.analysis(solver, [material, material_remainder], [case], "
        "mesh, label='Analysis')\n"
        f"solved = api.solve(analysis, execution={execution!r}, "
        "label='Validated Input')\n"
        "result = {'Analysis':analysis, 'Solver':solver, 'Material':material, "
        "'MaterialRemainder':material_remainder, "
        "'Fixed':fixed, 'Force':force, 'LoadCase':case, 'Mesh':mesh, "
        "'Result':solved}\n"
    )


def _captured(
    root: Path,
    document,
    *,
    operation: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    pack = get_vibescript_pack("FemWorkbench")
    assert pack is not None
    return {
        "tool_name": f"vibescript.fem.{operation}",
        "operation": operation,
        "arguments": arguments,
        "pack": pack,
        "project_root": str(root),
        "project_id": "fem-native-fixture",
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "fem-native-fixture-revision",
        "document_objects": [
            {"name": obj.Name, "label": obj.Label, "type_id": obj.TypeId}
            for obj in document.Objects
        ],
        "live_programs": [],
        "surface": resolve_modeling_surface("FemWorkbench", "vibescript").summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 90.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }


def _prepare_execute_validate(
    captured: dict[str, object], service: _Service
) -> tuple[dict[str, object], dict[str, object], dict[str, object] | None]:
    prepared = prepare_candidate(captured)
    assert prepared["finalized"] is False
    snapshots = capture_reference_inputs(service, prepared)
    prepared = finalize_candidate(prepared, snapshots)
    staged_names = {path.name for path in Path(prepared["staging"]).iterdir()}
    assert staged_names == {
        "request.json",
        "references",
        "worker.py",
        "vibescript_domain_api.py",
        "vibescript_fem_api.py",
        "vibescript_fem_worker.py",
        "vibescript_worker_progress.py",
    }, sorted(staged_names)
    execution = execute_candidate(prepared, cancellation_check=None)
    validated = validate_candidate(prepared, execution) if execution.get("ok") else None
    return prepared, execution, validated


def _outputs(document, accepted: dict[str, object]) -> dict[str, object]:
    result = {}
    for name, details in accepted["live_outputs"].items():
        obj = document.getObject(details["object_name"])
        assert obj is not None, (name, details)
        result[name] = obj
    return result


def _managed_names(document, program_id: str) -> set[str]:
    return {
        str(obj.Name)
        for obj in document.Objects
        if str(getattr(obj, PROP_PROGRAM_ID, "") or "") == program_id
    }


def _reference_state(value) -> list[list[object]]:
    return [
        [str(target.Name), [str(item) for item in subelements]]
        for target, subelements in list(value or [])
    ]


def _assert_fem_timeline_graph(
    document,
    outputs: dict[str, object],
    source,
) -> dict[str, object]:
    assert document.getObject(source.Name) is source
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None
    operations = list(timeline.Operations)
    roles = {}
    for name, obj in outputs.items():
        assert obj in operations
        assert obj.VibeCADTimelineRole == "operation"
        assert obj.getTypeIdOfProperty(
            "VibeCADTimelineRole"
        ) == "App::PropertyString"
        assert getattr(obj, "VibeCADTimelineOwner", None) is None
        roles[name] = [str(obj.Name), str(obj.VibeCADTimelineRole)]

    assert _reference_state(outputs["Material"].References) == [
        [str(source.Name), ["Solid1"]]
    ]
    assert _reference_state(outputs["MaterialRemainder"].References) == []
    assert _reference_state(outputs["Fixed"].References) == [
        [str(source.Name), ["Face1"]]
    ]
    assert _reference_state(outputs["Force"].References) == [
        [str(source.Name), ["Face2"]]
    ]
    assert outputs["Mesh"].Shape is source
    assert list(outputs["LoadCase"].VibeCADConstraints) == [
        outputs["Fixed"],
        outputs["Force"],
    ]
    assert set(outputs["Analysis"].Group) == {
        outputs[name]
        for name in (
            "Solver",
            "Material",
            "MaterialRemainder",
            "Fixed",
            "Force",
            "LoadCase",
            "Mesh",
            "Result",
        )
    }
    assert outputs["Result"].Mesh is outputs["Mesh"]
    assert (
        str(outputs["Result"].VibeCADAnalysisObjectName)
        == str(outputs["Analysis"].Name)
    )
    return {
        "roles": roles,
        "material_source": str(source.Name),
        "fixed_source": str(source.Name),
        "force_source": str(source.Name),
        "mesh_source": str(source.Name),
    }


def _snapshot(obj) -> dict[str, object]:
    output_type = str(getattr(obj, "VibeCADVibeScriptOutputType", "") or "")
    result: dict[str, object] = {
        "name": str(obj.Name),
        "type_id": str(obj.TypeId),
        "output_type": output_type,
        "label": str(obj.Label),
        "revision": str(obj.VibeCADVibeScriptRevision),
        "definition": str(obj.VibeCADVibeScriptDefinition),
        "validation": str(getattr(obj, PROP_FEM_VALIDATION)),
        "human_note": str(getattr(obj, "HumanFEMNote", "") or ""),
        "human_length": float(getattr(obj, "HumanFEMLength", 0.0) or 0.0),
        "expressions": [
            [str(path), str(expression)]
            for path, expression in list(obj.ExpressionEngine or [])
        ],
    }
    if output_type == "analysis":
        result["native"] = {"group": [str(item.Name) for item in obj.Group]}
    elif output_type == "solver":
        result["native"] = {
            "analysis_type": str(obj.AnalysisType),
            "matrix_solver": str(obj.MatrixSolverType),
            "geometrical_nonlinearity": bool(obj.GeometricalNonlinearity),
            "material_nonlinearity": bool(obj.MaterialNonlinearity),
            "reduced_integration": bool(obj.ReducedIntegration),
        }
    elif output_type == "material":
        result["native"] = {
            "category": str(obj.Category),
            "material": dict(obj.Material),
            "references": _reference_state(obj.References),
        }
    elif output_type == "constraint":
        native = {"references": _reference_state(obj.References)}
        if obj.TypeId == "Fem::ConstraintForce":
            native.update(
                {
                    "force": str(obj.Force),
                    "direction": [float(value) for value in obj.DirectionVector],
                    "reversed": bool(obj.Reversed),
                }
            )
        elif obj.TypeId == "Fem::ConstraintPressure":
            native.update(
                {"pressure": str(obj.Pressure), "reversed": bool(obj.Reversed)}
            )
        result["native"] = native
    elif output_type == "load_case":
        result["native"] = {
            "group": [str(item.Name) for item in obj.Group],
            "constraints": [str(item.Name) for item in obj.VibeCADConstraints],
        }
    elif output_type == "mesh":
        result["native"] = {
            "shape": str(obj.Shape.Name),
            "topology": _mesh_topology(obj.FemMesh),
            "order": str(obj.ElementOrder),
            "dimension": str(obj.ElementDimension),
        }
    elif output_type == "result":
        result["native"] = {
            "mesh": str(obj.Mesh.Name),
            "analysis": str(obj.VibeCADAnalysisObjectName),
            "status": str(obj.VibeCADFEMStatus),
            "solver_executed": bool(obj.VibeCADSolverExecuted),
            "input_sha256": str(obj.VibeCADInputDeckSHA256),
            "node_numbers": [int(value) for value in obj.NodeNumbers],
            "time": float(obj.Time),
        }
    else:
        raise AssertionError(f"Unexpected FEM output type {output_type!r}.")
    return result


def _assert_snapshot(obj, expected: dict[str, object]) -> None:
    observed = _snapshot(obj)
    if observed != expected:
        differences = {
            key: {"observed": observed.get(key), "expected": expected.get(key)}
            for key in sorted(set(observed) | set(expected))
            if observed.get(key) != expected.get(key)
        }
        raise AssertionError(json.dumps(differences, sort_keys=True, default=str))


def _add_human_state(outputs: dict[str, object]) -> None:
    for index, (name, obj) in enumerate(outputs.items(), start=1):
        obj.addProperty(
            "App::PropertyString",
            "HumanFEMNote",
            "Human",
            "Human-authored state that FEM regeneration and rollback must preserve.",
        )
        obj.HumanFEMNote = f"preserve {name}"
        obj.addProperty(
            "App::PropertyLength",
            "HumanFEMLength",
            "Human",
            "Human-authored expression-backed property.",
        )
        obj.HumanFEMLength = float(index)
        obj.setExpression("HumanFEMLength", f"{index} mm + 2 mm")


def _run_candidate(captured: dict[str, object], service: _Service):
    prepared, execution, validated = _prepare_execute_validate(captured, service)
    assert execution.get("ok") is True, execution
    assert validated is not None
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    return prepared, execution, validated, publication, accepted


def _exercise_missing_external_capabilities(
    root: Path,
    document,
    source_object,
    service: _Service,
) -> None:
    App.setActiveDocument(document.Name)
    empty_path = root / "no-external-fem-tools"
    empty_path.mkdir()
    cases = (
        ("gmsh", _program_source(mesh_method="gmsh")),
        ("calculix", _program_source(execution="calculix")),
    )
    for capability, program_source in cases:
        captured = _captured(
            root,
            document,
            operation="create_program",
            arguments=_creation_arguments(
                document,
                source_object,
                program_name=f"Missing {capability} capability fixture",
                source=program_source,
            ),
        )
        # Keep the Pixi runtime directories available so the isolated worker can
        # load Python and FreeCAD DLLs on Windows, while preventing executable
        # discovery for the capability under test.
        with patch.dict(
            os.environ,
            {
                "PATH": os.pathsep.join(
                    (str(empty_path), os.environ.get("PATH", ""))
                ),
                "PATHEXT": ".VIBE",
            },
            clear=False,
        ):
            prepared, execution, validated = _prepare_execute_validate(
                captured, service
            )
        assert validated is None
        assert execution["ok"] is False
        assert execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
        details = execution["observed"]["details"]
        assert details["stage"] == "external_capability"
        assert details["capability"] == capability
        assert execution["retry"]["required_changes"] == [details["correction"]]
        assert capability in details["correction"]
        retain_candidate(prepared, status="failed", failure=execution)


def _run_integration() -> int:
    with tempfile.TemporaryDirectory(prefix="vibecad-fem-integration-") as directory:
        root = Path(directory)
        document = App.newDocument("VibeCADFEMIntegration")
        document.UndoMode = 1
        source = document.addObject("Part::Feature", "SourceSolid")
        source.Label = "Human two-solid source"
        source.Shape = Part.makeCompound(
            [
                Part.makeBox(10, 8, 6),
                Part.makeBox(10, 8, 6, App.Vector(20, 0, 0)),
            ]
        )
        source_name = str(source.Name)
        service = _Service(root)
        document.commitTransaction()
        _exercise_source_api(str(document.Uid))
        multi_document = App.newDocument(
            "VibeCADFEMMultipleStudies",
            "VibeCAD FEM multiple independent studies",
            True,
            True,
        )
        multi_document.UndoMode = 1
        multi_source = multi_document.addObject("Part::Feature", "SourceSolid")
        multi_source.Label = "Shared geometry for independent studies"
        multi_source.Shape = Part.makeCompound(
            [
                Part.makeBox(10, 8, 6),
                Part.makeBox(10, 8, 6, App.Vector(20, 0, 0)),
            ]
        )
        multi_document.commitTransaction()
        _exercise_multiple_independent_studies(
            root,
            multi_document,
            multi_source,
            _Service(root),
        )
        _exercise_missing_external_capabilities(
            root,
            document,
            source,
            service,
        )
        prepared, execution, validated = _prepare_execute_validate(
            _captured(
                root,
                document,
                operation="create_program",
                arguments=_creation_arguments(
                    document,
                    source,
                    program_name="Native FEM fixture",
                    source=_program_source(),
                ),
            ),
            service,
        )
        assert execution.get("ok") is True, execution
        assert validated is not None
        assert execution["fem_validation"]["solver_executed"] is False
        malformed = copy.deepcopy(execution)
        malformed["fem_validation"]["output_count"] -= 1
        _expect_error("output count", lambda: validate_candidate(prepared, malformed))
        malformed = copy.deepcopy(execution)
        next(item for item in malformed["outputs"] if item["type"] == "mesh")[
            "fem_data"
        ]["nodes"][0][1] = 1.0
        _expect_error("reconstruction", lambda: validate_candidate(prepared, malformed))
        malformed = copy.deepcopy(execution)
        next(item for item in malformed["outputs"] if item["type"] == "result")[
            "fem_data"
        ]["input_deck"]["artifact_sha256"] = "0" * 64
        _expect_error("unauthenticated", lambda: validate_candidate(prepared, malformed))
        mesh = next(item for item in validated["outputs"] if item["type"] == "mesh")
        assert mesh["detached_fem_mesh"] is not None
        result = next(item for item in validated["outputs"] if item["type"] == "result")
        assert result["fem_data"]["status"] == "input_validated"
        assert result["fem_data"]["solver_executed"] is False
        retain_candidate(prepared, status="validated")
        publication = publish_candidate(service, prepared, validated)
        accepted = accept_candidate(prepared, publication)
        outputs = _outputs(document, accepted)
        assert all(outputs.values())
        assert {name: obj.TypeId for name, obj in outputs.items()} == {
            "Analysis": "Fem::FemAnalysis",
            "Solver": "Fem::FemSolverObjectPython",
            "Material": "App::MaterialObjectPython",
            "MaterialRemainder": "App::MaterialObjectPython",
            "Fixed": "Fem::ConstraintFixed",
            "Force": "Fem::ConstraintForce",
            "LoadCase": "App::DocumentObjectGroup",
            "Mesh": "Fem::FemMeshShapeBaseObjectPython",
            "Result": "Fem::FemResultObjectPython",
        }
        assert outputs["Mesh"].FemMesh.NodeCount == 16
        assert outputs["Mesh"].FemMesh.VolumeCount == 2
        assert _reference_state(outputs["Material"].References) == [
            ["SourceSolid", ["Solid1"]]
        ]
        assert _reference_state(outputs["MaterialRemainder"].References) == []
        result_mapping = json.loads(str(getattr(outputs["Result"], PROP_FEM_VALIDATION)))[
            "material_mesh_mapping"
        ]
        assert result_mapping["active_element_count"] == 2
        assert result_mapping["default_material_output"] == "MaterialRemainder"
        assert [item["element_count"] for item in result_mapping["materials"]] == [
            1,
            1,
        ]
        assert outputs["Result"].VibeCADFEMStatus == "input_validated"
        assert outputs["Result"].VibeCADSolverExecuted is False
        assert outputs["Result"] in list(outputs["Analysis"].Group)
        stable_names = {name: str(obj.Name) for name, obj in outputs.items()}
        assert _managed_names(document, prepared["program_id"]) == set(
            stable_names.values()
        )
        created_state = _assert_fem_timeline_graph(document, outputs, source)
        document.undo()
        assert not _managed_names(document, prepared["program_id"])
        assert document.getObject(source.Name) is source
        document.redo()
        outputs = {
            name: document.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(outputs.values())
        assert _assert_fem_timeline_graph(
            document,
            outputs,
            document.getObject(source.Name),
        ) == created_state
        _add_human_state(outputs)
        consumer = document.addObject("App::FeaturePython", "HumanFEMConsumer")
        consumer.addProperty("App::PropertyLinkList", "Sources")
        consumer.Sources = list(outputs.values())
        document.commitTransaction()

        failed_capture = _captured(
            root,
            document,
            operation="edit_source",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "source": _program_source().replace("'Face2'", "'Face99'"),
            },
        )
        failed_prepared, failed_execution, failed_validated = _prepare_execute_validate(
            failed_capture, service
        )
        assert failed_validated is None
        assert failed_execution["ok"] is False
        assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
        assert failed_execution["domain_failure_stage"] == "semantic_selection"
        failure_details = failed_execution["observed"]["details"]
        assert failure_details["stage"] == "semantic_selection"
        assert failure_details["requested"] == "Face99"
        assert "Face1" in failure_details["available_subelements"]
        assert failed_execution["retry"]["required_changes"] == [
            failure_details["correction"]
        ]
        assert "available" in failure_details["correction"]
        retain_candidate(failed_prepared, status="failed", failure=failed_execution)
        for obj in outputs.values():
            assert str(obj.VibeCADVibeScriptRevision) == accepted["accepted_revision"]

        recovery_capture = _captured(
            root,
            document,
            operation="edit_source",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": failed_prepared["revision"],
                "source": _program_source(),
            },
        )
        _recovered, _, _, recovery_publication, accepted = _run_candidate(
            recovery_capture, service
        )
        assert recovery_publication["created_objects"] == []
        outputs = _outputs(document, accepted)
        assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
        assert consumer.Sources == list(outputs.values())

        update_capture = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "patch": {"force": 1500.0},
            },
        )
        updated, _, _, update_publication, accepted = _run_candidate(
            update_capture, service
        )
        assert update_publication["created_objects"] == []
        outputs = _outputs(document, accepted)
        assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
        assert (
            abs(float(outputs["Force"].Force.getValueAs("N").Value) - 1500.0)
            <= 1.0e-9
        )
        for name, obj in outputs.items():
            assert obj.HumanFEMNote == f"preserve {name}"
        document.undo()
        outputs = {
            name: document.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(outputs.values())
        assert (
            abs(float(outputs["Force"].Force.getValueAs("N").Value) - 1000.0)
            <= 1.0e-9
        )
        _assert_fem_timeline_graph(
            document,
            outputs,
            document.getObject(source.Name),
        )
        assert consumer.Sources == list(outputs.values())
        document.redo()
        outputs = {
            name: document.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(outputs.values())
        assert (
            abs(float(outputs["Force"].Force.getValueAs("N").Value) - 1500.0)
            <= 1.0e-9
        )
        _assert_fem_timeline_graph(
            document,
            outputs,
            document.getObject(source.Name),
        )
        assert consumer.Sources == list(outputs.values())

        reconfigure_capture = _captured(
            root,
            document,
            operation="reconfigure_program",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "source": _program_source(force_scale=2.0),
                "input_schema": _input_schema(),
                "inputs": dict(updated["inputs"]),
                "expected_outputs": EXPECTED_OUTPUTS,
            },
        )
        reconfigured, reconfigured_execution, reconfigured_validated = (
            _prepare_execute_validate(reconfigure_capture, service)
        )
        assert reconfigured_execution.get("ok") is True, reconfigured_execution
        assert reconfigured_validated is not None
        retain_candidate(reconfigured, status="validated")
        before_fault = {name: _snapshot(obj) for name, obj in outputs.items()}
        original_configure = publication_module._configure_fem

        def fail_after_result(active_document, obj, item, live_outputs):
            original_configure(active_document, obj, item, live_outputs)
            if item["name"] == "Result":
                raise RuntimeError("injected FEM publication failure")

        publication_module._configure_fem = fail_after_result
        try:
            _expect_error(
                "injected FEM publication failure",
                lambda: publish_candidate(
                    service, reconfigured, reconfigured_validated
                ),
            )
        finally:
            publication_module._configure_fem = original_configure
        outputs = {
            name: document.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(outputs.values())
        for name, obj in outputs.items():
            _assert_snapshot(obj, before_fault[name])
        assert consumer.Sources == list(outputs.values())

        reconfigured_publication = publish_candidate(
            service, reconfigured, reconfigured_validated
        )
        accepted = accept_candidate(reconfigured, reconfigured_publication)
        outputs = _outputs(document, accepted)
        assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
        assert (
            abs(float(outputs["Force"].Force.getValueAs("N").Value) - 3000.0)
            <= 1.0e-9
        )
        assert consumer.Sources == list(outputs.values())

        context = complete_domain_context(domain_context_snapshot(service, "fem"))
        assert context["domain"] == "fem"
        assert context["document_fem"]["object_count"] == len(EXPECTED_OUTPUTS)
        context_by_output = {
            item["program_output"]: item
            for item in context["document_fem"]["objects"]
        }
        assert set(context_by_output) == {item["name"] for item in EXPECTED_OUTPUTS}
        assert context_by_output["Mesh"]["native_summary"]["node_count"] == 16
        assert context_by_output["Mesh"]["native_summary"]["volume_count"] == 2
        assert context_by_output["Result"]["native_summary"]["solver_executed"] is False
        assert context_by_output["Result"]["accepted_validation"]["status"] == (
            "input_validated"
        )
        reference_candidate = next(
            item
            for item in context["fem_reference_candidates"]["objects"]
            if item["name"] == source.Name
        )
        assert reference_candidate["eligible_for_fem_reference"] is True
        assert "document_robots" not in context
        assert "document_inspections" not in context
        assert "document_meshes" not in context

        save_path = root / "fem-production.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        reopened = App.openDocument(str(save_path))
        assert reopened is not None
        App.setActiveDocument(reopened.Name)
        reopened.UndoMode = 1
        outputs = {
            name: reopened.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(outputs.values())
        reopened.recompute()
        assert outputs["Mesh"].FemMesh.NodeCount == 16
        assert outputs["Mesh"].FemMesh.VolumeCount == 2
        assert _reference_state(outputs["Material"].References) == [
            ["SourceSolid", ["Solid1"]]
        ]
        assert outputs["Result"].VibeCADSolverExecuted is False
        assert outputs["Result"] in list(outputs["Analysis"].Group)
        for name, obj in outputs.items():
            assert obj.HumanFEMNote == f"preserve {name}"
            assert json.loads(str(getattr(obj, PROP_FEM_VALIDATION)))[
                "native_type"
            ] == obj.TypeId

        consumer = reopened.getObject("HumanFEMConsumer")
        assert consumer is not None and consumer.Sources == list(outputs.values())
        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": reconfigured["program_id"],
                "expected_revision": reconfigured["revision"],
                "reason": "verify FEM external-reference guard",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        _expect_error(
            "reference", lambda: delete_live_program(service, prepared_delete)
        )
        restore_prepared_delete(prepared_delete)
        consumer.Sources = []
        reopened.removeObject(consumer.Name)

        before_delete_fault = {
            name: _snapshot(obj) for name, obj in outputs.items()
        }
        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": reconfigured["program_id"],
                "expected_revision": reconfigured["revision"],
                "reason": "exercise explicit FEM deletion rollback",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        original_remove = publication_module._remove_timeline_deletion

        def fail_after_committed_removal(active_document, deletion):
            original_remove(active_document, deletion)
            active_document.commitTransaction()
            raise RuntimeError("injected FEM deletion failure")

        publication_module._remove_timeline_deletion = fail_after_committed_removal
        try:
            try:
                delete_live_program(service, prepared_delete)
            except RuntimeError as exc:
                assert "injected FEM deletion failure" in str(exc)
                assert "rollback failure" not in str(exc).lower(), str(exc)
            else:
                raise AssertionError("Expected injected FEM deletion failure.")
            restore_prepared_delete(prepared_delete)
        finally:
            publication_module._remove_timeline_deletion = original_remove
        outputs = {
            name: reopened.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(outputs.values())
        for name, obj in outputs.items():
            _assert_snapshot(obj, before_delete_fault[name])

        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": reconfigured["program_id"],
                "expected_revision": reconfigured["revision"],
                "reason": "FEM production integration complete",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        finished = finish_delete(
            prepared_delete,
            delete_live_program(service, prepared_delete),
        )
        assert finished["ok"] is True
        assert not _managed_names(reopened, reconfigured["program_id"])
        assert reopened.getObject(source_name) is not None
        reopened.undo()
        outputs = {
            name: reopened.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(outputs.values())
        _assert_fem_timeline_graph(
            reopened,
            outputs,
            reopened.getObject(source_name),
        )
        reopened.redo()
        assert not _managed_names(reopened, reconfigured["program_id"])
        assert reopened.getObject(source_name) is not None
        App.closeDocument(reopened.Name)
    print(
        json.dumps(
            {
                "integration": "fem_vibescript_api",
                "ok": True,
                "canonical_nonredundant_api": True,
                "exact_worker_host_validation": True,
                "mesh_constraint_mapping": True,
                "material_assignment": True,
                "model_correctable_errors": True,
                "model_first_description": True,
                "exact_result_contract": True,
                "solver_execution_not_faked": True,
                "stable_native_outputs": True,
                "failed_candidate_retention": True,
                "in_place_regeneration": True,
                "explicit_publication_rollback": True,
                "explicit_deletion_rollback": True,
                "save_reopen": True,
                "external_reference_guard": True,
                "multiple_independent_studies": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def main() -> int:
    pack = get_vibescript_pack("FemWorkbench")
    assert pack is not None and pack.production_ready
    return _run_integration()


if __name__ == "__main__":
    raise SystemExit(main())
