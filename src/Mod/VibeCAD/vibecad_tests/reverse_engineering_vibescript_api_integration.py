# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native production gate for the canonical Reverse Engineering worker API."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile

MODULE_ROOT = Path(__file__).resolve().parent.parent
while str(MODULE_ROOT) in sys.path:
    sys.path.remove(str(MODULE_ROOT))
sys.path.insert(0, str(MODULE_ROOT))

import FreeCAD as App  # noqa: E402
import Mesh  # noqa: E402
import Part  # noqa: E402
import Points  # noqa: E402

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
import VibeCADVibeScriptDomainPublication as publication_module  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    PROP_REVERSE_VALIDATION,
    delete_live_program,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    ReverseEngineeringDomainAdapter,
    accept_candidate,
    capture_reference_shapes,
    complete_inspection,
    execute_candidate,
    finalize_candidate,
    finish_delete,
    prepare_candidate,
    prepare_delete,
    restore_prepared_delete,
    retain_candidate,
    validate_candidate,
)
from VibeCADVibeScriptDomains import (  # noqa: E402
    PROP_PROGRAM_ID,
    complete_domain_context,
    domain_context_snapshot,
    get_vibescript_pack,
)
from vibescript_mesh_worker import mesh_diagnostics  # noqa: E402
from vibescript_part_worker import part_shape_facts  # noqa: E402
from vibescript_reverse_engineering_api import (  # noqa: E402
    ReverseEngineeringAPIError,
    ReverseEngineeringDomainAPI,
)
from vibescript_reverse_engineering_worker import (  # noqa: E402
    ReverseEngineeringCandidateError,
    mesh_fingerprint,
    native_capabilities,
    validate_and_build_reverse_engineering,
    validate_reverse_definition,
)


EXPORTS = (
    "fit_curve",
    "fit_surface",
    "reconstruct",
    "segment",
    "fit_metrics",
)
OUTPUT_TYPES = ("curve", "surface", "brep", "mesh", "fit_metrics")
EXPECTED_OUTPUTS = [
    {"name": "Curve", "type": "curve"},
    {"name": "Surface", "type": "surface"},
    {"name": "Mesh", "type": "mesh"},
    {"name": "Regions", "type": "mesh"},
    {"name": "BREP", "type": "brep"},
    {"name": "Report", "type": "fit_metrics"},
]


class _Service:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _active_document():
        return App.ActiveDocument

    @staticmethod
    def active_workbench_name() -> str:
        return "ReverseEngineeringWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "reverse-engineering-native-fixture-revision"

    def project_scope_snapshot(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "project_id": "reverse-engineering-native-fixture",
        }

    @staticmethod
    def provider_working_set() -> dict[str, object]:
        return {"target_count": 0, "targets": []}

    @staticmethod
    def selection_summary() -> dict[str, object]:
        return {"selection": []}


def _api() -> ReverseEngineeringDomainAPI:
    return ReverseEngineeringDomainAPI(EXPORTS, OUTPUT_TYPES)


def _build(document, result: dict, expected: list[dict[str, str]]):
    with tempfile.TemporaryDirectory(prefix="vibecad-reverse-worker-") as directory:
        root = Path(directory)
        (root / "outputs").mkdir()
        outputs, validation = validate_and_build_reverse_engineering(
            result,
            expected,
            root,
            document,
            max_shape_subelements=64,
        )
        detached = []
        for item in outputs:
            copy = dict(item)
            path = root / str(item.get("artifact_path") or "")
            if item.get("artifact_kind") == "mesh_bms":
                mesh = Mesh.Mesh(str(path))
                copy["loaded_fingerprint"] = mesh_fingerprint(mesh)
                copy["loaded_facets"] = int(mesh.CountFacets)
            elif item.get("artifact_kind") == "brep":
                shape = Part.Shape()
                shape.importBrep(str(path))
                copy["loaded_shape"] = shape
            detached.append(copy)
        return detached, validation


def _grid(width: int, height: int) -> list[list[float]]:
    return [
        [float(column), float(row), 0.08 * column * row]
        for row in range(height)
        for column in range(width)
    ]


def _expect_candidate_error(stage: str, call) -> None:
    try:
        call()
    except ReverseEngineeringCandidateError as exc:
        assert exc.details.get("stage") == stage, exc.details
        assert str(exc.details.get("correction") or "").strip(), exc.details
    else:
        raise AssertionError(f"Expected Reverse Engineering failure at {stage!r}.")


def _expect_error(fragment: str, call) -> None:
    try:
        call()
    except (TypeError, ValueError, RuntimeError, ReverseEngineeringCandidateError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(
            f"Expected Reverse Engineering failure containing {fragment!r}."
        )


def _reference_schema() -> dict[str, object]:
    return {
        "type": "object",
        "x-vibecad-reference": True,
        "properties": {
            "document_uid": {"type": "string", "minLength": 1},
            "object_name": {"type": "string", "minLength": 1},
        },
        "required": ["document_uid", "object_name"],
        "additionalProperties": False,
    }


def _input_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "source": _reference_schema(),
            "method": {"type": "string", "enum": ["structured_grid", "greedy"]},
            "diagonal": {
                "type": "string",
                "enum": ["shortest", "forward", "backward"],
            },
            "curve_tolerance": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 100,
            },
            "surface_weight": {
                "type": "number",
                "minimum": 0,
                "maximum": 100,
            },
            "fit_tolerance": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 100,
            },
        },
        "required": [
            "source",
            "method",
            "diagonal",
            "curve_tolerance",
            "surface_weight",
            "fit_tolerance",
        ],
        "additionalProperties": False,
    }


def _program_source(*, label_prefix: str = "Native") -> str:
    return (
        "curve = api.fit_curve(inputs['source'], min_degree=2, max_degree=4, "
        "continuity='c1', tolerance=inputs['curve_tolerance'], "
        f"label='{label_prefix} curve')\n"
        "surface = api.fit_surface(inputs['source'], u_degree=2, v_degree=2, "
        "u_poles=4, v_poles=4, iterations=3, "
        "smoothing_weight=inputs['surface_weight'], "
        f"label='{label_prefix} surface')\n"
        "reconstruction_parameters = ({'diagonal':inputs['diagonal']} if "
        "inputs['method'] == 'structured_grid' else {'search_radius':2.0})\n"
        "mesh = api.reconstruct(inputs['source'], method=inputs['method'], "
        "parameters=reconstruction_parameters, "
        f"label='{label_prefix} mesh')\n"
        "regions = api.segment(mesh, method='normal_regions', "
        "parameters={'segment':'all','minimum_facets':1,'angle_degrees':12.0}, "
        f"label='{label_prefix} regions')\n"
        "brep = api.reconstruct(inputs['source'], method='structured_grid', "
        "output_type='brep', parameters={'diagonal':inputs['diagonal']}, "
        f"label='{label_prefix} BREP')\n"
        "report = api.fit_metrics(regions, tolerance=inputs['fit_tolerance'], "
        f"label='{label_prefix} report')\n"
        "result = {'Curve':curve, 'Surface':surface, 'Mesh':mesh, "
        "'Regions':regions, 'BREP':brep, 'Report':report}\n"
    )


def _captured(
    root: Path,
    document,
    *,
    operation: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    pack = get_vibescript_pack("ReverseEngineeringWorkbench")
    assert pack is not None and pack.production_ready
    return {
        "tool_name": f"vibescript.reverse_engineering.{operation}",
        "operation": operation,
        "arguments": arguments,
        "pack": pack,
        "project_root": str(root),
        "project_id": "reverse-engineering-native-fixture",
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "reverse-engineering-native-fixture-revision",
        "document_objects": [
            {
                "name": str(obj.Name),
                "label": str(obj.Label),
                "type_id": str(obj.TypeId),
            }
            for obj in document.Objects
        ],
        "live_programs": [],
        "surface": resolve_modeling_surface(
            "ReverseEngineeringWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 90.0,
        "memory_limit_bytes": 3 * 1024 * 1024 * 1024,
    }


def _prepare_execute_validate(captured: dict[str, object], service: _Service):
    prepared = prepare_candidate(captured)
    staged_names = {path.name for path in Path(prepared["staging"]).iterdir()}
    expected_staging = {
        "worker.py",
        "vibescript_domain_api.py",
        "vibescript_reverse_engineering_api.py",
        "vibescript_reverse_engineering_worker.py",
        "vibescript_points_api.py",
        "vibescript_points_worker.py",
        "vibescript_meshpart_api.py",
        "vibescript_meshpart_worker.py",
        "vibescript_mesh_worker.py",
        "vibescript_part_worker.py",
    }
    assert expected_staging <= staged_names, {
        "missing": sorted(expected_staging - staged_names),
        "unexpected": sorted(staged_names - expected_staging),
    }
    assert not any(
        name.startswith("vibescript_")
        and name.endswith(("_api.py", "_worker.py"))
        and name not in expected_staging
        for name in staged_names
    )
    if prepared["reference_requirements"]:
        prepared = finalize_candidate(
            prepared,
            capture_reference_shapes(service, prepared),
        )
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    return prepared, execution, validate_candidate(prepared, execution)


def _run_candidate(captured: dict[str, object], service: _Service):
    prepared, execution, validated = _prepare_execute_validate(captured, service)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    return prepared, execution, validated, publication, accepted


def _outputs(document, accepted: dict[str, object]) -> dict[str, object]:
    result = {}
    for name, details in accepted["live_outputs"].items():
        obj = document.getObject(details["object_name"])
        assert obj is not None
        result[name] = obj
    return result


def _managed_names(document, program_id: str) -> set[str]:
    return {
        str(obj.Name)
        for obj in document.Objects
        if str(getattr(obj, PROP_PROGRAM_ID, "") or "") == program_id
    }


def _placement(obj) -> list[float]:
    placement = getattr(obj, "Placement", None)
    if placement is None:
        placement = getattr(obj, "HumanReversePlacement")
    return [
        float(value)
        for value in (
            placement.Base.x,
            placement.Base.y,
            placement.Base.z,
            *placement.Rotation.Q,
        )
    ]


def _geometry_signature(obj) -> dict[str, object]:
    if str(obj.TypeId) == "Mesh::Feature":
        mesh = obj.Mesh.copy()
        mesh.Placement = App.Placement()
        return {
            "kind": "mesh",
            "fingerprint": mesh_fingerprint(mesh),
            "facts": mesh_diagnostics(mesh),
            "segments": [
                [int(item) for item in list(mesh.getSegment(index) or [])]
                for index in range(int(mesh.countSegments()))
            ],
        }
    if str(obj.TypeId) == "Part::Feature":
        shape = obj.Shape.copy()
        shape.Placement = App.Placement()
        return {
            "kind": "brep",
            "facts": part_shape_facts(shape, max_subelements=64),
        }
    return {
        "kind": "metrics",
        "target": str(obj.VibeCADTargetOutput),
        "target_operation": str(obj.VibeCADTargetOperation),
        "source_points": int(obj.VibeCADSourcePointCount),
        "evaluated_points": int(obj.VibeCADEvaluatedPointCount),
        "mean": float(obj.VibeCADMeanFitDistance),
        "rms": float(obj.VibeCADRMSFitDistance),
        "maximum": float(obj.VibeCADMaximumFitDistance),
        "tolerance": float(obj.VibeCADFitTolerance),
        "within": float(obj.VibeCADWithinTolerance),
        "within_fraction": float(obj.VibeCADWithinToleranceFraction),
        "segments": int(obj.VibeCADSegmentCount),
    }


def _snapshot(obj) -> dict[str, object]:
    return {
        "name": str(obj.Name),
        "type_id": str(obj.TypeId),
        "label": str(obj.Label),
        "revision": str(obj.VibeCADVibeScriptRevision),
        "definition": str(obj.VibeCADVibeScriptDefinition),
        "validation": str(getattr(obj, PROP_REVERSE_VALIDATION)),
        "placement": _placement(obj),
        "human_note": str(getattr(obj, "HumanReverseNote", "") or ""),
        "human_length": float(getattr(obj, "HumanReverseLength", 0.0) or 0.0),
        "expressions": [
            [str(path), str(expression)]
            for path, expression in list(obj.ExpressionEngine or [])
        ],
        "geometry": _geometry_signature(obj),
    }


def _assert_snapshot(obj, expected: dict[str, object]) -> None:
    observed = _snapshot(obj)
    for left, right in zip(observed["placement"], expected["placement"]):
        assert math.isclose(left, right, rel_tol=1.0e-12, abs_tol=1.0e-12)
    observed["placement"] = []
    expected = copy.deepcopy(expected)
    expected["placement"] = []
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
            "HumanReverseNote",
            "Human",
            "Human-authored state that regeneration and rollback must preserve.",
        )
        obj.HumanReverseNote = f"preserve {name}"
        obj.addProperty(
            "App::PropertyLength",
            "HumanReverseLength",
            "Human",
            "Human-authored expression-backed property.",
        )
        obj.HumanReverseLength = float(index)
        obj.setExpression("HumanReverseLength", f"{index} mm + 2 mm")
        if not hasattr(obj, "Placement"):
            obj.addProperty(
                "App::PropertyPlacement",
                "HumanReversePlacement",
                "Human",
                "Human-authored placement on a structured report carrier.",
            )
        placement_property = (
            "Placement" if hasattr(obj, "Placement") else "HumanReversePlacement"
        )
        setattr(obj, placement_property, App.Placement(
            App.Vector(index * 2, index * 3, index * 4),
            App.Rotation(App.Vector(0, 0, 1), index * 3),
        ))


def _exercise_stable_mesh_reference(
    root: Path,
    document,
    service: _Service,
) -> object:
    source = document.addObject("Mesh::Feature", "HumanExternalMesh")
    source.Label = "Human external mesh source"
    source.Mesh = Mesh.Mesh(
        [
            [(0, 0, 0), (2, 0, 0), (0, 2, 0)],
            [(10, 0, 0), (12, 0, 0), (10, 2, 0)],
        ]
    )
    source.Placement = App.Placement(
        App.Vector(3, 4, 5),
        App.Rotation(App.Vector(0, 0, 1), 20),
    )
    source.addProperty("App::PropertyString", "HumanSourceNote", "Human")
    source.HumanSourceNote = "external mesh must remain untouched"
    reference = {
        "document_uid": str(document.Uid),
        "object_name": str(source.Name),
    }
    captured = _captured(
        root,
        document,
        operation="create_program",
        arguments={
            "program_name": "Stable Mesh Segmentation",
            "source": (
                "regions = api.segment(inputs['source'], "
                "method='connected_components', "
                "parameters={'segment':'all','minimum_facets':1}, "
                "label='Referenced mesh regions')\n"
                "result = {'Regions':regions}\n"
            ),
            "input_schema": {
                "type": "object",
                "properties": {"source": _reference_schema()},
                "required": ["source"],
                "additionalProperties": False,
            },
            "inputs": {"source": reference},
            "expected_outputs": [{"name": "Regions", "type": "mesh"}],
        },
    )
    prepared, execution, validated = _prepare_execute_validate(captured, service)
    assert prepared["resolved_references"][0]["artifact_kind"] == "mesh_bms"
    trace = execution["outputs"][0]["reverse_data"]["operation_trace"]
    assert trace["segment_count"] == 2
    assert trace["retained_facets"] == 2
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    regions = _outputs(document, accepted)["Regions"]
    regions_name = str(regions.Name)
    assert str(regions.TypeId) == "Mesh::Feature"
    assert int(regions.Mesh.CountFacets) == 2
    assert int(regions.Mesh.countSegments()) == 2
    assert source.HumanSourceNote == "external mesh must remain untouched"
    delete_capture = _captured(
        root,
        document,
        operation="delete_program",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": prepared["revision"],
            "reason": "stable Mesh reference gate complete",
        },
    )
    prepared_delete = prepare_delete(delete_capture)
    finished = finish_delete(
        prepared_delete,
        delete_live_program(service, prepared_delete),
    )
    assert finished["ok"] is True
    assert document.getObject(regions_name) is None
    return source


def _exercise_lifecycle() -> dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix="vibecad-reverse-native-"))
    document = App.newDocument("VibeScriptReverseEngineeringNative")
    service = _Service(root)
    try:
        App.setActiveDocument(document.Name)
        surface = resolve_modeling_surface(
            "ReverseEngineeringWorkbench", "vibescript"
        )
        assert surface.available is True, surface.unavailable_reason
        assert surface.cad_tool_names == tuple(
            f"vibescript.reverse_engineering.{name}"
            for name in (
                "describe_api",
                "inspect_program",
                "create_program",
                "edit_source",
                "set_inputs",
                "reconfigure_program",
                "delete_program",
            )
        )

        source = document.addObject("Points::Feature", "HumanStructuredCloud")
        source.Label = "Human structured point source"
        source.Points = Points.Points([App.Vector(*point) for point in _grid(4, 4)])
        source.addProperty("App::PropertyInteger", "Width", "Points")
        source.addProperty("App::PropertyInteger", "Height", "Points")
        source.Width = 4
        source.Height = 4
        source.Placement = App.Placement(
            App.Vector(5, 7, 11),
            App.Rotation(App.Vector(0, 0, 1), 15),
        )
        source.addProperty("App::PropertyString", "HumanSourceNote", "Human")
        source.HumanSourceNote = "source must remain untouched"
        mesh_source = _exercise_stable_mesh_reference(root, document, service)
        reference = {
            "document_uid": str(document.Uid),
            "object_name": str(source.Name),
        }
        initial_inputs = {
            "source": reference,
            "method": "structured_grid",
            "diagonal": "shortest",
            "curve_tolerance": 0.02,
            "surface_weight": 0.1,
            "fit_tolerance": 0.05,
        }
        create_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments={
                "program_name": "Native Reverse Engineering Lifecycle",
                "source": _program_source(),
                "input_schema": _input_schema(),
                "inputs": initial_inputs,
                "expected_outputs": EXPECTED_OUTPUTS,
            },
        )
        prepared, execution, validated = _prepare_execute_validate(
            create_capture, service
        )
        assert prepared["resolved_references"][0]["artifact_kind"] == "points_asc"
        assert prepared["resolved_references"][0]["structured"] == {
            "width": 4,
            "height": 4,
        }
        worker_validation = execution["reverse_engineering_validation"]
        assert worker_validation["native_capabilities"] == native_capabilities()
        assert worker_validation["output_count"] == len(EXPECTED_OUTPUTS)
        assert [item["operation"] for item in worker_validation["outputs"]] == [
            "fit_curve",
            "fit_surface",
            "reconstruct",
            "segment",
            "reconstruct",
            "fit_metrics",
        ]

        malformed = copy.deepcopy(execution)
        malformed["reverse_engineering_validation"]["native_capabilities"][
            "approxCurve"
        ] = False
        _expect_error(
            "capabilities changed",
            lambda: validate_candidate(prepared, malformed),
        )
        malformed = copy.deepcopy(execution)
        malformed["outputs"][2]["reverse_data"]["geometry_fingerprint"] = "0" * 64
        _expect_error(
            "artifact identity changed",
            lambda: validate_candidate(prepared, malformed),
        )
        malformed = copy.deepcopy(execution)
        malformed["outputs"][5]["reverse_data"]["fit_metrics"][
            "mean_distance"
        ] += 1.0
        _expect_error(
            "distance metrics",
            lambda: validate_candidate(prepared, malformed),
        )
        malformed = copy.deepcopy(execution)
        malformed["outputs"][3]["reverse_data"]["operation_trace"][
            "operation"
        ] = "reconstruct"
        _expect_error(
            "inconsistent trace",
            lambda: validate_candidate(prepared, malformed),
        )

        retain_candidate(prepared, status="validated")
        publication = publish_candidate(service, prepared, validated)
        accepted = accept_candidate(prepared, publication)
        outputs = _outputs(document, accepted)
        stable_names = {name: str(obj.Name) for name, obj in outputs.items()}
        assert {name: str(obj.TypeId) for name, obj in outputs.items()} == {
            "Curve": "Part::Feature",
            "Surface": "Part::Feature",
            "Mesh": "Mesh::Feature",
            "Regions": "Mesh::Feature",
            "BREP": "Part::Feature",
            "Report": "App::FeaturePython",
        }
        assert _managed_names(document, prepared["program_id"]) == set(
            stable_names.values()
        )
        assert len(outputs["Curve"].Shape.Edges) >= 1
        assert len(outputs["Surface"].Shape.Faces) >= 1
        assert int(outputs["Mesh"].Mesh.CountFacets) == 18
        assert int(outputs["Regions"].Mesh.CountFacets) == 18
        assert len(outputs["BREP"].Shape.Faces) == 18
        assert int(outputs["Report"].VibeCADSourcePointCount) == 16
        assert 0.0 <= float(outputs["Report"].VibeCADWithinTolerance) <= 100.0
        _add_human_state(outputs)

        consumer = document.addObject("App::FeaturePython", "HumanReverseConsumer")
        consumer.addProperty("App::PropertyLinkList", "Sources")
        consumer.Sources = list(outputs.values())
        inspection = complete_inspection(
            {
                **create_capture,
                "program_id": prepared["program_id"],
                "live_programs": [],
            }
        )
        assert inspection["program"]["live_outputs"]["Mesh"]["reverse_data"]

        if not native_capabilities()["triangulate"]:
            failed_capture = _captured(
                root,
                document,
                operation="set_inputs",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": accepted["working_revision"],
                    "patch": {"method": "greedy"},
                },
            )
            failed_prepared = prepare_candidate(failed_capture)
            failed_prepared = finalize_candidate(
                failed_prepared,
                capture_reference_shapes(service, failed_prepared),
            )
            failed_execution = execute_candidate(
                failed_prepared, cancellation_check=None
            )
            assert failed_execution["ok"] is False
            assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
            assert failed_execution["observed"]["details"]["stage"] == (
                "native_capability"
            )
            failure_details = failed_execution["observed"]["details"]
            assert failed_execution["domain_failure_stage"] == "native_capability"
            assert failed_execution["retry"]["required_changes"] == [
                failure_details["correction"]
            ]
            assert "structured_grid" in failure_details["correction"]
            retain_candidate(
                failed_prepared,
                status="failed",
                failure=failed_execution,
            )
            assert all(
                str(obj.VibeCADVibeScriptRevision) == accepted["accepted_revision"]
                for obj in outputs.values()
            )
            expected_revision = failed_prepared["revision"]
        else:
            expected_revision = accepted["working_revision"]

        recovery_capture = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": expected_revision,
                "patch": {
                    "method": "structured_grid",
                    "diagonal": "forward",
                    "curve_tolerance": 0.03,
                    "surface_weight": 0.2,
                    "fit_tolerance": 0.08,
                },
            },
        )
        recovery_prepared, _, recovery_validated, recovery_publication, accepted = (
            _run_candidate(recovery_capture, service)
        )
        assert recovery_publication["created_objects"] == []
        outputs = _outputs(document, accepted)
        assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
        assert consumer.Sources == list(outputs.values())
        for name, obj in outputs.items():
            assert obj.HumanReverseNote == f"preserve {name}"

        reconfigured_source = _program_source(label_prefix="Reconfigured")
        reconfigure_capture = _captured(
            root,
            document,
            operation="reconfigure_program",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "source": reconfigured_source,
                "input_schema": _input_schema(),
                "inputs": dict(recovery_prepared["inputs"]),
                "expected_outputs": EXPECTED_OUTPUTS,
            },
        )
        reconfigured, _, reconfigured_validated = _prepare_execute_validate(
            reconfigure_capture, service
        )
        retain_candidate(reconfigured, status="validated")
        before_fault = {name: _snapshot(obj) for name, obj in outputs.items()}
        original_configure = publication_module._configure_reverse_engineering

        def fail_after_report_assignment(obj, item):
            original_configure(obj, item)
            if item["name"] == "Report":
                raise RuntimeError("injected Reverse Engineering publication failure")

        publication_module._configure_reverse_engineering = fail_after_report_assignment
        try:
            _expect_error(
                "injected Reverse Engineering publication failure",
                lambda: publish_candidate(service, reconfigured, reconfigured_validated),
            )
        finally:
            publication_module._configure_reverse_engineering = original_configure
        outputs = {
            name: document.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(obj is not None for obj in outputs.values()), [
            name for name, obj in outputs.items() if obj is None
        ]
        for name, obj in outputs.items():
            _assert_snapshot(obj, before_fault[name])
        assert consumer.Sources == list(outputs.values())

        reconfigured_publication = publish_candidate(
            service, reconfigured, reconfigured_validated
        )
        accepted = accept_candidate(reconfigured, reconfigured_publication)
        outputs = _outputs(document, accepted)
        assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
        assert consumer.Sources == list(outputs.values())
        assert all(str(obj.Label).startswith("Reconfigured") for obj in outputs.values())
        assert source.HumanSourceNote == "source must remain untouched"
        assert mesh_source.HumanSourceNote == "external mesh must remain untouched"

        context = complete_domain_context(
            domain_context_snapshot(service, "reverse_engineering")
        )
        assert context["domain"] == "reverse_engineering"
        assert context["native_reverse_engineering_capabilities"] == (
            native_capabilities()
        )
        source_context = next(
            item
            for item in context["document_point_clouds"]["objects"]
            if item["name"] == source.Name
        )
        assert source_context["native_summary"]["points"] == 16
        mesh_context = next(
            item
            for item in context["document_mesh_sources"]["objects"]
            if item["name"] == stable_names["Mesh"]
        )
        assert mesh_context["accepted_validation"]["schema"]

        save_path = root / "reverse-engineering-production.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        reopened = App.openDocument(str(save_path))
        assert reopened is not None
        App.setActiveDocument(reopened.Name)
        reopened.recompute()
        outputs = {
            name: reopened.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(obj is not None for obj in outputs.values())
        consumer = reopened.getObject("HumanReverseConsumer")
        source = reopened.getObject("HumanStructuredCloud")
        assert consumer is not None and source is not None
        assert consumer.Sources == list(outputs.values())
        assert source.HumanSourceNote == "source must remain untouched"
        for name, obj in outputs.items():
            assert obj.HumanReverseNote == f"preserve {name}"
            assert json.loads(str(getattr(obj, PROP_REVERSE_VALIDATION)))["schema"]

        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": reconfigured["program_id"],
                "expected_revision": reconfigured["revision"],
                "reason": "verify Reverse Engineering external-reference guard",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        _expect_error(
            "reference",
            lambda: delete_live_program(service, prepared_delete),
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
                "reason": "exercise explicit Reverse Engineering deletion rollback",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        original_remove = publication_module._remove_owned_objects

        def fail_after_committed_removal(active_document, managed_objects):
            for managed in list(managed_objects):
                active_document.removeObject(managed.Name)
            active_document.commitTransaction()
            raise RuntimeError("injected Reverse Engineering deletion failure")

        publication_module._remove_owned_objects = fail_after_committed_removal
        try:
            try:
                delete_live_program(service, prepared_delete)
            except RuntimeError as exc:
                assert "injected Reverse Engineering deletion failure" in str(exc)
                assert "rollback failure" not in str(exc), str(exc)
            else:
                raise AssertionError("Expected injected deletion failure.")
            restore_prepared_delete(prepared_delete)
        finally:
            publication_module._remove_owned_objects = original_remove
        outputs = {
            name: reopened.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(obj is not None for obj in outputs.values()), [
            name for name, obj in outputs.items() if obj is None
        ]
        for name, obj in outputs.items():
            _assert_snapshot(obj, before_delete_fault[name])

        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": reconfigured["program_id"],
                "expected_revision": reconfigured["revision"],
                "reason": "Reverse Engineering production integration complete",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        finished = finish_delete(
            prepared_delete,
            delete_live_program(service, prepared_delete),
        )
        assert finished["ok"] is True
        assert not _managed_names(reopened, reconfigured["program_id"])
        App.closeDocument(reopened.Name)
        return {
            "stable_native_outputs": True,
            "failed_candidate_retention": True,
            "exact_worker_host_validation": True,
            "explicit_publication_rollback": True,
            "explicit_deletion_rollback": True,
            "save_reopen": True,
            "external_reference_guard": True,
            "stable_mesh_reference": True,
        }
    finally:
        if App.ActiveDocument is not None:
            App.closeDocument(App.ActiveDocument.Name)
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    api = _api()
    assert api.exported_names == EXPORTS
    for redundant in (
        "approximate_curve",
        "approximate_surface",
        "triangulate",
        "output",
    ):
        assert not hasattr(api, redundant)
    try:
        api.fit_curve({"document_uid": 1, "object_name": "Cloud"})
    except ReverseEngineeringAPIError as exc:
        assert exc.details["stage"] == "source_validation"
        assert exc.details["operation"] == "fit_curve"
        assert exc.details["parameter"] == "source.document_uid"
        assert "Change only the failing source expression" in exc.details["correction"]
    else:
        raise AssertionError("Expected one structured Reverse Engineering source failure.")

    pack = get_vibescript_pack("ReverseEngineeringWorkbench")
    assert pack is not None
    description = ReverseEngineeringDomainAdapter(pack).describe_api()
    assert description["api_contract"] == (
        "vibecad-vibescript-reverse-engineering-api-v1"
    )
    assert description["canonical_operations"]["reconstruct"][
        "brep_semantics"
    ].startswith("output_type='brep'")
    assert "row-major" in description["composition_contract"]["structured_order"]
    assert "One-way" in description["canonical_operations"]["fit_metrics"][
        "meaning"
    ]
    assert "cannot switch workbench" in description["workbench_handoffs"]["rule"]
    assert len(json.dumps(description, allow_nan=False).encode("utf-8")) < 32 * 1024

    document = App.newDocument(
        "VibeCADReverseWorkerGate", "VibeCAD Reverse Worker Gate", True, True
    )
    try:
        curve_points = [
            [0.0, 0.0, 0.0],
            [1.0, 0.5, 0.1],
            [2.0, -0.25, 0.2],
            [3.0, 0.75, 0.1],
            [4.0, 0.0, 0.0],
        ]
        curve = api.fit_curve(
            curve_points,
            parametrization="chord_length",
            min_degree=2,
            max_degree=4,
            continuity="c1",
            tolerance=0.01,
            label="Native fitted curve",
        )
        assert validate_reverse_definition(curve) == curve.to_payload()
        curve_outputs, _curve_validation = _build(
            document,
            {"Curve": curve},
            [{"name": "Curve", "type": "curve"}],
        )
        curve_shape = curve_outputs[0]["loaded_shape"]
        assert not curve_shape.isNull() and curve_shape.isValid()
        assert len(curve_shape.Edges) >= 1 and len(curve_shape.Faces) == 0
        _expect_candidate_error(
            "result_contract",
            lambda: _build(
                document,
                {"Curve": curve, "Unexpected": curve},
                [{"name": "Curve", "type": "curve"}],
            ),
        )

        surface_points = _grid(4, 4)
        surface = api.fit_surface(
            surface_points,
            u_degree=2,
            v_degree=2,
            u_poles=4,
            v_poles=4,
            iterations=3,
            label="Native fitted surface",
        )
        surface_outputs, _surface_validation = _build(
            document,
            {"Surface": surface},
            [{"name": "Surface", "type": "surface"}],
        )
        surface_shape = surface_outputs[0]["loaded_shape"]
        assert not surface_shape.isNull() and surface_shape.isValid()
        assert len(surface_shape.Faces) >= 1

        grid = _grid(5, 4)
        reconstructed = api.reconstruct(
            grid,
            method="structured_grid",
            parameters={"grid_size": [5, 4], "diagonal": "shortest"},
            label="Structured mesh",
        )
        segmented = api.segment(
            reconstructed,
            method="normal_regions",
            parameters={
                "segment": "all",
                "minimum_facets": 1,
                "angle_degrees": 12.0,
            },
            label="Normal regions",
        )
        report = api.fit_metrics(segmented, tolerance=0.05, label="Fit report")
        outputs, validation = _build(
            document,
            {"Mesh": reconstructed, "Segments": segmented, "Report": report},
            [
                {"name": "Mesh", "type": "mesh"},
                {"name": "Segments", "type": "mesh"},
                {"name": "Report", "type": "fit_metrics"},
            ],
        )
        assert outputs[0]["loaded_facets"] == 24
        assert outputs[0]["loaded_fingerprint"] == outputs[0]["reverse_data"][
            "geometry_fingerprint"
        ]
        assert outputs[1]["loaded_fingerprint"] == outputs[1]["reverse_data"][
            "geometry_fingerprint"
        ]
        metrics = outputs[2]["reverse_data"]["fit_metrics"]
        assert metrics["source_point_count"] == len(grid)
        assert metrics["evaluated_point_count"] == len(grid)
        assert 0.0 <= metrics["within_tolerance_fraction"] <= 1.0
        assert validation["output_count"] == 3

        brep = api.reconstruct(
            _grid(3, 3),
            output_type="brep",
            parameters={"grid_size": [3, 3], "diagonal": "forward"},
            label="Triangulated BREP",
        )
        brep_outputs, _brep_validation = _build(
            document,
            {"BREP": brep},
            [{"name": "BREP", "type": "brep"}],
        )
        brep_shape = brep_outputs[0]["loaded_shape"]
        assert not brep_shape.isNull() and brep_shape.isValid()
        assert len(brep_shape.Faces) == 8

        capabilities = native_capabilities()
        assert capabilities["approxCurve"] is True
        assert capabilities["approxSurface"] is True
        if not capabilities["triangulate"]:
            unavailable = api.reconstruct(
                grid,
                method="greedy",
                parameters={"search_radius": 2.0},
            )
            _expect_candidate_error(
                "native_capability",
                lambda: _build(
                    document,
                    {"Mesh": unavailable},
                    [{"name": "Mesh", "type": "mesh"}],
                ),
            )
    finally:
        App.closeDocument(document.Name)

    lifecycle = _exercise_lifecycle()
    print(
        json.dumps(
            {
                "integration": "reverse_engineering_vibescript_worker",
                "ok": True,
                "canonical_provider_operations": len(EXPORTS),
                "curve_and_surface_fitting": True,
                "structured_mesh_and_brep": True,
                "segmentation_and_metrics": True,
                "model_correctable_errors": True,
                "exact_result_contract": True,
                "artifact_roundtrip": True,
                "native_capabilities": native_capabilities(),
                **lifecycle,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
