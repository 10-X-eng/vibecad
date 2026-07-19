# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native production gate for the canonical Inspection VibeScript domain."""

from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
import shutil
import sys
import tempfile

MODULE_ROOT = Path(__file__).resolve().parent.parent
while str(MODULE_ROOT) in sys.path:
    sys.path.remove(str(MODULE_ROOT))
sys.path.insert(0, str(MODULE_ROOT))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402
import Points  # noqa: E402

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
import VibeCADVibeScriptDomainPublication as publication_module  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    PROP_INSPECTION_VALIDATION,
    delete_live_program,
    mark_programs_stale_from_source,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    InspectionDomainAdapter,
    abandon_prepared_candidate,
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
from vibescript_inspection_api import (  # noqa: E402
    InspectionAPIError,
    InspectionDomainAPI,
)
from vibescript_inspection_worker import (  # noqa: E402
    DISTANCE_SCHEMA,
    InspectionCandidateError,
    VALIDATION_SCHEMA,
    validate_and_build_inspection,
    validate_inspection_definition,
)


EXPORTS = ("comparison", "group", "measurement", "report")
OUTPUT_TYPES = (
    "inspection_group",
    "inspection_feature",
    "measurement",
    "report",
)
EXPECTED_OUTPUTS = [
    {"name": "Comparison", "type": "inspection_feature"},
    {"name": "Inspection", "type": "inspection_group"},
    {"name": "RMS", "type": "measurement"},
    {"name": "Report", "type": "report"},
]


class _Service:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _active_document():
        return App.ActiveDocument

    @staticmethod
    def active_workbench_name() -> str:
        return "InspectionWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "inspection-native-fixture-revision"

    def project_scope_snapshot(self) -> dict[str, str]:
        return {"root": str(self.root), "project_id": "inspection-native-fixture"}

    @staticmethod
    def provider_working_set() -> dict[str, object]:
        return {"target_count": 0, "targets": []}

    @staticmethod
    def selection_summary() -> dict[str, object]:
        return {"selection": []}


def _api() -> InspectionDomainAPI:
    return InspectionDomainAPI(EXPORTS, OUTPUT_TYPES)


def _expect_error(fragment: str, call) -> None:
    try:
        call()
    except (TypeError, ValueError, RuntimeError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f"Expected Inspection failure containing {fragment!r}.")


def _expect_candidate_error(stage: str, call) -> None:
    try:
        call()
    except InspectionCandidateError as exc:
        assert exc.details.get("stage") == stage, exc.details
        assert str(exc.details.get("correction") or "").strip(), exc.details
    else:
        raise AssertionError(f"Expected Inspection failure at {stage!r}.")


def _exercise_source_api() -> None:
    api = _api()
    assert api.exported_names == EXPORTS
    for redundant in ("inspection", "tolerance", "compare", "output"):
        assert not hasattr(api, redundant)
    for name in EXPORTS:
        signature = str(inspect.signature(getattr(api, name)))
        assert "*args" not in signature and "**" not in signature
        assert inspect.getdoc(getattr(api, name))
    actual = {"document_uid": "document", "object_name": "Actual"}
    nominal = {"document_uid": "document", "object_name": "Nominal"}
    comparison = api.comparison(
        actual,
        [nominal],
        search_radius=1.0,
        tolerance=[-0.1, 0.2],
        label="Comparison",
    )
    group = api.group([comparison], label="Inspection")
    measurement = api.measurement(comparison, metric="rms", label="RMS")
    report = api.report(group, label="Report")
    assert [
        validate_inspection_definition(value)["operation"]
        for value in (comparison, group, measurement, report)
    ] == list(EXPORTS)
    try:
        api.comparison(
            {"document_uid": 1, "object_name": "Actual"},
            [nominal],
            search_radius=1.0,
            tolerance=0.1,
        )
    except InspectionAPIError as exc:
        assert exc.details["stage"] == "source_validation"
        assert exc.details["operation"] == "comparison"
        assert exc.details["parameter"] == "actual.document_uid"
        assert "Change only the failing source expression" in exc.details["correction"]
    else:
        raise AssertionError("Expected one structured Inspection source failure.")
    _expect_error(
        "cannot also appear",
        lambda: api.comparison(
            actual,
            [actual],
            search_radius=1.0,
            tolerance=0.1,
        ),
    )
    _expect_error(
        "inside search_radius",
        lambda: api.comparison(
            actual,
            [nominal],
            search_radius=0.1,
            tolerance=0.2,
        ),
    )
    _expect_error(
        "duplicate definitions",
        lambda: api.group([comparison, comparison]),
    )
    pack = get_vibescript_pack("InspectionWorkbench")
    assert pack is not None
    description = InspectionDomainAdapter(pack).describe_api()
    assert description["api_contract"] == "vibecad-vibescript-inspection-api-v1"
    assert "smallest absolute" in description["input_reference_contract"][
        "nominal_distance"
    ]["multiple_nominals"]
    assert "measured_count only" in description["distance_contract"][
        "completeness"
    ]["within_tolerance_fraction"]
    assert "does not apply" in description["distance_contract"][
        "thickness_compatibility"
    ]
    assert "cannot switch workbench" in description["workbench_handoffs"]["rule"]
    assert len(json.dumps(description, allow_nan=False).encode("utf-8")) < 32 * 1024


def _reference_schema() -> dict:
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


def _captured(
    root: Path,
    document,
    source: str | None = None,
    *,
    operation: str = "create_program",
    arguments: dict[str, object] | None = None,
) -> dict:
    pack = get_vibescript_pack("InspectionWorkbench")
    assert pack is not None
    if arguments is None:
        if source is None:
            raise AssertionError("A create-program capture requires source.")
        reference_schema = _reference_schema()
        arguments = {
            "program_name": "Native Inspection",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {
                    "actual": reference_schema,
                    "nominal": reference_schema,
                },
                "required": ["actual", "nominal"],
                "additionalProperties": False,
            },
            "inputs": {
                "actual": {
                    "document_uid": str(document.Uid),
                    "object_name": "Actual",
                },
                "nominal": {
                    "document_uid": str(document.Uid),
                    "object_name": "Nominal",
                },
            },
            "expected_outputs": EXPECTED_OUTPUTS,
        }
    return {
        "tool_name": f"vibescript.inspection.{operation}",
        "operation": operation,
        "arguments": arguments,
        "pack": pack,
        "project_root": str(root),
        "project_id": "inspection-native-fixture",
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "inspection-native-fixture-revision",
        "document_objects": [
            {"name": obj.Name, "label": obj.Label, "type_id": obj.TypeId}
            for obj in document.Objects
        ],
        "live_programs": [],
        "surface": resolve_modeling_surface(
            "InspectionWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 90.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }


def _exercise_worker(root: Path) -> dict:
    document = App.newDocument("InspectionWorkerFixture")
    nominal = document.addObject("Part::Feature", "Nominal")
    nominal.Shape = Part.makePlane(10, 10)
    actual = document.addObject("Points::Feature", "Actual")
    actual.Points = Points.Points(
        [
            App.Vector(1, 1, 0.05),
            App.Vector(4, 1, 0.05),
            App.Vector(1, 4, 0.05),
            App.Vector(4, 4, 0.05),
        ]
    )
    document.recompute()
    source = (
        "comparison = api.comparison(inputs['actual'], [inputs['nominal']], "
        "search_radius=1.0, tolerance=0.1, label='Measured Plane')\n"
        "inspection = api.group([comparison], label='Plane Inspection')\n"
        "rms = api.measurement(comparison, metric='rms', label='RMS Deviation')\n"
        "report = api.report(inspection, label='Inspection Report')\n"
        "result = {'Comparison':comparison, 'Inspection':inspection, "
        "'RMS':rms, 'Report':report}\n"
    )
    direct_comparison = _api().comparison(
        {"document_uid": str(document.Uid), "object_name": "Actual"},
        [{"document_uid": str(document.Uid), "object_name": "Nominal"}],
        search_radius=1.0,
        tolerance=0.1,
    )
    _expect_candidate_error(
        "result_contract",
        lambda: validate_and_build_inspection(
            document,
            {"Comparison": direct_comparison, "Unexpected": direct_comparison},
            [{"name": "Comparison", "type": "inspection_feature"}],
            root,
        ),
    )
    captured = _captured(root, document, source)
    service = _Service(root)
    prepared = prepare_candidate(captured)
    try:
        snapshots = capture_reference_shapes(service, prepared)
        snapshot_kinds = {
            item["reference_artifact_kind"] for item in snapshots
        }
        assert snapshot_kinds == {
            "brep",
            "points_asc",
        }, snapshots
        prepared = finalize_candidate(prepared, snapshots)
        execution = execute_candidate(prepared, cancellation_check=None)
        assert execution.get("ok") is True, execution
        assert execution["inspection_validation"]["schema"] == VALIDATION_SCHEMA
        validated = validate_candidate(prepared, execution)
        comparison = validated["outputs"][0]
        assert comparison["artifact_schema"] == DISTANCE_SCHEMA
        assert comparison["detached_distances"] == [
            0.05000000074505806,
            0.05000000074505806,
            0.05000000074505806,
            0.05000000074505806,
        ]
        summary = comparison["inspection_data"]["distance_summary"]
        assert summary["sample_count"] == 4
        assert summary["measured_count"] == 4
        assert summary["unmeasured_count"] == 0
        assert summary["passed"] is True
        assert abs(float(summary["rms"]) - 0.05) < 1.0e-7
        assert validated["outputs"][1]["inspection_data"]["passed"] is True
        assert validated["outputs"][2]["inspection_data"]["unit"] == "mm"
        assert validated["outputs"][3]["inspection_data"]["entries"][0][
            "output"
        ] == "Comparison"
        return {
            "ok": True,
            "native_distances": True,
            "authenticated_brep_and_points": True,
            "typed_group_measurement_report": True,
        }
    finally:
        abandon_prepared_candidate(prepared)
        App.closeDocument(document.Name)


def _input_schema() -> dict[str, object]:
    reference = _reference_schema()
    return {
        "type": "object",
        "properties": {
            "actual": reference,
            "nominal": reference,
            "search_radius": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 100,
            },
            "tolerance": {
                "type": "number",
                "minimum": 0,
                "maximum": 100,
            },
        },
        "required": ["actual", "nominal", "search_radius", "tolerance"],
        "additionalProperties": False,
    }


def _program_source(*, label_prefix: str = "Native") -> str:
    return (
        "comparison = api.comparison(inputs['actual'], [inputs['nominal']], "
        "search_radius=inputs['search_radius'], tolerance=inputs['tolerance'], "
        "require_complete=True, "
        f"label='{label_prefix} comparison')\n"
        "inspection = api.group([comparison], "
        f"label='{label_prefix} inspection')\n"
        "rms = api.measurement(comparison, metric='rms', "
        f"label='{label_prefix} RMS')\n"
        "report = api.report(inspection, "
        f"label='{label_prefix} report')\n"
        "result = {'Comparison':comparison, 'Inspection':inspection, "
        "'RMS':rms, 'Report':report}\n"
    )


def _prepare_execute_validate(captured: dict[str, object], service: _Service):
    prepared = prepare_candidate(captured)
    staged_names = {path.name for path in Path(prepared["staging"]).iterdir()}
    expected_staging = {
        "worker.py",
        "vibescript_domain_api.py",
        "vibescript_inspection_api.py",
        "vibescript_inspection_worker.py",
        "vibescript_points_worker.py",
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
    ), staged_names
    if prepared["reference_requirements"]:
        prepared = finalize_candidate(
            prepared,
            capture_reference_shapes(service, prepared),
        )
    execution = execute_candidate(prepared, cancellation_check=None)
    return prepared, execution, (
        validate_candidate(prepared, execution) if execution.get("ok") else None
    )


def _run_candidate(captured: dict[str, object], service: _Service):
    prepared, execution, validated = _prepare_execute_validate(captured, service)
    assert execution.get("ok") is True, execution
    assert validated is not None
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    return prepared, execution, validated, publication, accepted


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


def _snapshot(obj) -> dict[str, object]:
    output_type = str(getattr(obj, "VibeCADVibeScriptOutputType", "") or "")
    result: dict[str, object] = {
        "name": str(obj.Name),
        "type_id": str(obj.TypeId),
        "output_type": output_type,
        "label": str(obj.Label),
        "revision": str(obj.VibeCADVibeScriptRevision),
        "definition": str(obj.VibeCADVibeScriptDefinition),
        "validation": str(getattr(obj, PROP_INSPECTION_VALIDATION)),
        "derived_state": str(obj.VibeCADDerivedState),
        "input_objects": [
            str(item.Name)
            for item in list(getattr(obj, "VibeCADVibeScriptInputObjects", []) or [])
        ],
        "human_note": str(getattr(obj, "HumanInspectionNote", "") or ""),
        "human_length": float(
            getattr(obj, "HumanInspectionLength", 0.0) or 0.0
        ),
        "expressions": [
            [str(path), str(expression)]
            for path, expression in list(obj.ExpressionEngine or [])
        ],
        "managed_properties": sorted(
            name
            for name in list(obj.PropertiesList or [])
            if str(obj.getGroupOfProperty(name) or "") == "VibeCAD"
        ),
    }
    if output_type == "inspection_feature":
        result["native"] = {
            "actual": str(obj.Actual.Name),
            "nominals": [str(item.Name) for item in list(obj.Nominals or [])],
            "search_radius": float(obj.SearchRadius),
            "thickness": float(obj.Thickness),
            "distances": [float(value) for value in list(obj.Distances or [])],
            "passed": bool(obj.VibeCADPassed),
            "sample_count": int(obj.VibeCADSampleCount),
            "measured_count": int(obj.VibeCADMeasuredCount),
            "rms": float(obj.VibeCADRMSDistance),
        }
    elif output_type == "inspection_group":
        result["native"] = {
            "members": [str(item.Name) for item in list(obj.Group or [])],
            "passed": bool(obj.VibeCADPassed),
            "comparison_count": int(obj.VibeCADComparisonCount),
        }
    elif output_type == "measurement":
        result["native"] = {
            "comparison": str(obj.VibeCADComparison.Name),
            "metric": str(obj.VibeCADMetric),
            "value": float(obj.VibeCADValue),
            "unit": str(obj.VibeCADUnit),
            "passed": bool(obj.VibeCADPassed),
        }
    elif output_type == "report":
        result["native"] = {
            "group": str(obj.VibeCADInspectionGroup.Name),
            "entries": str(obj.VibeCADInspectionEntries),
            "passed": bool(obj.VibeCADPassed),
            "comparison_count": int(obj.VibeCADComparisonCount),
        }
    else:
        raise AssertionError(f"Unexpected Inspection output type {output_type!r}.")
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
            "HumanInspectionNote",
            "Human",
            "Human-authored state that regeneration and rollback must preserve.",
        )
        obj.HumanInspectionNote = f"preserve {name}"
        obj.addProperty(
            "App::PropertyLength",
            "HumanInspectionLength",
            "Human",
            "Human-authored expression-backed property.",
        )
        obj.HumanInspectionLength = float(index)
        obj.setExpression("HumanInspectionLength", f"{index} mm + 2 mm")


def _exercise_lifecycle() -> dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix="vibecad-inspection-native-"))
    document = App.newDocument("VibeScriptInspectionNative")
    service = _Service(root)
    try:
        App.setActiveDocument(document.Name)
        pack = get_vibescript_pack("InspectionWorkbench")
        assert pack is not None and pack.production_ready
        surface = resolve_modeling_surface("InspectionWorkbench", "vibescript")
        assert surface.available is True, surface.unavailable_reason
        assert surface.cad_tool_names == tuple(
            f"vibescript.inspection.{name}"
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

        nominal = document.addObject("Part::Feature", "Nominal")
        nominal.Label = "Human nominal plane"
        nominal.Shape = Part.makePlane(10, 10)
        nominal.addProperty("App::PropertyString", "HumanSourceNote", "Human")
        nominal.HumanSourceNote = "nominal must remain untouched"
        actual = document.addObject("Points::Feature", "Actual")
        actual.Label = "Human measured points"
        actual.Points = Points.Points(
            [
                App.Vector(1, 1, 0.05),
                App.Vector(4, 1, 0.05),
                App.Vector(1, 4, 0.05),
                App.Vector(4, 4, 0.05),
            ]
        )
        actual.addProperty("App::PropertyString", "HumanSourceNote", "Human")
        actual.HumanSourceNote = "actual must remain untouched"
        document.recompute()
        references = {
            "actual": {
                "document_uid": str(document.Uid),
                "object_name": str(actual.Name),
            },
            "nominal": {
                "document_uid": str(document.Uid),
                "object_name": str(nominal.Name),
            },
        }
        initial_inputs = {
            **references,
            "search_radius": 1.0,
            "tolerance": 0.1,
        }
        create_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments={
                "program_name": "Native Inspection Lifecycle",
                "source": _program_source(),
                "input_schema": _input_schema(),
                "inputs": initial_inputs,
                "expected_outputs": EXPECTED_OUTPUTS,
            },
        )
        prepared, execution, validated = _prepare_execute_validate(
            create_capture, service
        )
        assert execution.get("ok") is True, execution
        assert validated is not None
        assert {
            item["artifact_kind"] for item in prepared["resolved_references"]
        } == {"brep", "points_asc"}
        assert execution["inspection_validation"]["schema"] == VALIDATION_SCHEMA
        assert execution["inspection_validation"]["output_count"] == 4

        malformed = copy.deepcopy(execution)
        malformed["inspection_validation"]["output_count"] = 3
        _expect_error(
            "global validation summary",
            lambda: validate_candidate(prepared, malformed),
        )
        malformed = copy.deepcopy(execution)
        malformed["outputs"][0]["inspection_data"]["distance_summary"][
            "rms"
        ] += 1.0
        _expect_error(
            "distance summary",
            lambda: validate_candidate(prepared, malformed),
        )
        malformed = copy.deepcopy(execution)
        malformed["outputs"][0]["inspection_data"]["native_trace"][
            "engine"
        ] = "Part"
        _expect_error(
            "native trace changed",
            lambda: validate_candidate(prepared, malformed),
        )

        retain_candidate(prepared, status="validated")
        publication = publish_candidate(service, prepared, validated)
        accepted = accept_candidate(prepared, publication)
        outputs = _outputs(document, accepted)
        stable_names = {name: str(obj.Name) for name, obj in outputs.items()}
        assert {name: str(obj.TypeId) for name, obj in outputs.items()} == {
            "Comparison": "Inspection::Feature",
            "Inspection": "Inspection::Group",
            "RMS": "App::FeaturePython",
            "Report": "App::FeaturePython",
        }
        assert _managed_names(document, prepared["program_id"]) == set(
            stable_names.values()
        )
        feature = outputs["Comparison"]
        accepted_distances = [float(value) for value in list(feature.Distances)]
        assert len(accepted_distances) == 4
        assert feature.isFrozen() is True
        assert "Touched" not in set(feature.State)
        document.recompute()
        assert [float(value) for value in list(feature.Distances)] == accepted_distances
        assert "Touched" not in set(feature.State)
        assert outputs["Inspection"].Group == [feature]
        assert outputs["RMS"].VibeCADComparison is feature
        assert outputs["Report"].VibeCADInspectionGroup is outputs["Inspection"]
        _add_human_state(outputs)

        consumer = document.addObject("App::FeaturePython", "HumanInspectionConsumer")
        consumer.addProperty("App::PropertyLinkList", "Sources")
        consumer.Sources = list(outputs.values())
        inspected = complete_inspection(
            {
                **create_capture,
                "program_id": prepared["program_id"],
                "live_programs": [],
            }
        )
        assert inspected["program"]["live_outputs"]["Comparison"][
            "inspection_data"
        ]

        failed_capture = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "patch": {"search_radius": 0.01},
            },
        )
        failed_prepared, failed_execution, failed_validated = (
            _prepare_execute_validate(failed_capture, service)
        )
        assert failed_validated is None
        assert failed_execution["ok"] is False
        assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
        assert failed_execution["domain_failure_stage"] == "source_validation"
        failure_details = failed_execution["observed"]["details"]
        assert failed_execution["retry"]["required_changes"] == [
            failure_details["correction"]
        ]
        assert failure_details["parameter"] == "tolerance"
        assert "search_radius" in failure_details["correction"]
        retain_candidate(
            failed_prepared,
            status="failed",
            failure=failed_execution,
        )
        assert all(
            str(obj.VibeCADVibeScriptRevision) == accepted["accepted_revision"]
            for obj in outputs.values()
        )

        recovery_capture = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": failed_prepared["revision"],
                "patch": {"search_radius": 1.0, "tolerance": 0.08},
            },
        )
        recovery_prepared, _, _, recovery_publication, accepted = _run_candidate(
            recovery_capture, service
        )
        assert recovery_publication["created_objects"] == []
        outputs = _outputs(document, accepted)
        assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
        assert consumer.Sources == list(outputs.values())
        for name, obj in outputs.items():
            assert obj.HumanInspectionNote == f"preserve {name}"

        reconfigure_capture = _captured(
            root,
            document,
            operation="reconfigure_program",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "source": _program_source(label_prefix="Reconfigured"),
                "input_schema": _input_schema(),
                "inputs": dict(recovery_prepared["inputs"]),
                "expected_outputs": EXPECTED_OUTPUTS,
            },
        )
        reconfigured, reconfigured_execution, reconfigured_validated = (
            _prepare_execute_validate(reconfigure_capture, service)
        )
        assert reconfigured_execution.get("ok") is True
        assert reconfigured_validated is not None
        retain_candidate(reconfigured, status="validated")
        before_fault = {name: _snapshot(obj) for name, obj in outputs.items()}
        original_configure = publication_module._configure_inspection

        def fail_after_report_assignment(active_document, obj, item, live_outputs):
            original_configure(active_document, obj, item, live_outputs)
            if item["name"] == "Report":
                raise RuntimeError("injected Inspection publication failure")

        publication_module._configure_inspection = fail_after_report_assignment
        try:
            _expect_error(
                "injected Inspection publication failure",
                lambda: publish_candidate(
                    service, reconfigured, reconfigured_validated
                ),
            )
        finally:
            publication_module._configure_inspection = original_configure
        outputs = {
            name: document.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(obj is not None for obj in outputs.values())
        for name, obj in outputs.items():
            _assert_snapshot(obj, before_fault[name])
        assert consumer.Sources == list(outputs.values())

        reconfigured_publication = publish_candidate(
            service, reconfigured, reconfigured_validated
        )
        accepted = accept_candidate(reconfigured, reconfigured_publication)
        outputs = _outputs(document, accepted)
        assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
        assert all(str(obj.Label).startswith("Reconfigured") for obj in outputs.values())
        assert consumer.Sources == list(outputs.values())
        assert nominal.HumanSourceNote == "nominal must remain untouched"
        assert actual.HumanSourceNote == "actual must remain untouched"

        context = complete_domain_context(
            domain_context_snapshot(service, "inspection")
        )
        assert context["domain"] == "inspection"
        assert context["document_inspections"]["object_count"] == 2
        assert any(
            item["name"] == nominal.Name
            for item in context["document_shape_sources"]["objects"]
        )
        assert any(
            item["name"] == actual.Name
            for item in context["document_point_clouds"]["objects"]
        )
        assert "approved_point_artifacts" not in context
        assert "native_reverse_engineering_capabilities" not in context

        save_path = root / "inspection-production.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        reopened = App.openDocument(str(save_path))
        assert reopened is not None
        App.setActiveDocument(reopened.Name)
        outputs = {
            name: reopened.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(obj is not None for obj in outputs.values())
        feature = outputs["Comparison"]
        assert feature.isFrozen() is True
        reopened_distances = [float(value) for value in list(feature.Distances)]
        reopened.recompute()
        assert [float(value) for value in list(feature.Distances)] == reopened_distances
        assert "Touched" not in set(feature.State)
        for name, obj in outputs.items():
            assert obj.HumanInspectionNote == f"preserve {name}"
            assert json.loads(str(getattr(obj, PROP_INSPECTION_VALIDATION)))[
                "schema"
            ] == VALIDATION_SCHEMA

        actual = reopened.getObject("Actual")
        nominal = reopened.getObject("Nominal")
        consumer = reopened.getObject("HumanInspectionConsumer")
        assert actual is not None and nominal is not None and consumer is not None
        actual.Points = Points.Points(
            [
                App.Vector(1, 1, 0.05),
                App.Vector(4, 1, 0.05),
                App.Vector(1, 4, 0.05),
                App.Vector(4, 4, 0.05),
                App.Vector(7, 7, 0.05),
            ]
        )
        marked = mark_programs_stale_from_source(actual, "Points")
        assert set(marked) == set(stable_names.values())
        assert all(str(obj.VibeCADDerivedState) == "stale" for obj in outputs.values())
        stale_distances = [float(value) for value in list(feature.Distances)]
        reopened.recompute()
        assert [float(value) for value in list(feature.Distances)] == stale_distances
        assert feature.isFrozen() is True
        assert "Touched" not in set(feature.State)

        regenerate_capture = _captured(
            root,
            reopened,
            operation="set_inputs",
            arguments={
                "program_id": reconfigured["program_id"],
                "expected_revision": accepted["working_revision"],
                "patch": {"tolerance": 0.12},
            },
        )
        regenerated, _, _, regenerated_publication, accepted = _run_candidate(
            regenerate_capture, service
        )
        assert regenerated_publication["created_objects"] == []
        outputs = _outputs(reopened, accepted)
        assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
        assert len(list(outputs["Comparison"].Distances)) == 5
        assert outputs["Comparison"].isFrozen() is True
        assert all(str(obj.VibeCADDerivedState) == "accepted" for obj in outputs.values())
        assert consumer.Sources == list(outputs.values())

        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": regenerated["program_id"],
                "expected_revision": regenerated["revision"],
                "reason": "verify Inspection external-reference guard",
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
                "program_id": regenerated["program_id"],
                "expected_revision": regenerated["revision"],
                "reason": "exercise explicit Inspection deletion rollback",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        original_remove = publication_module._remove_owned_objects

        def fail_after_committed_removal(active_document, managed_objects):
            for managed in list(managed_objects):
                active_document.removeObject(managed.Name)
            active_document.commitTransaction()
            raise RuntimeError("injected Inspection deletion failure")

        publication_module._remove_owned_objects = fail_after_committed_removal
        try:
            try:
                delete_live_program(service, prepared_delete)
            except RuntimeError as exc:
                assert "injected Inspection deletion failure" in str(exc)
                assert "rollback failure" not in str(exc), str(exc)
            else:
                raise AssertionError("Expected injected Inspection deletion failure.")
            restore_prepared_delete(prepared_delete)
        finally:
            publication_module._remove_owned_objects = original_remove
        outputs = {
            name: reopened.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(obj is not None for obj in outputs.values())
        for name, obj in outputs.items():
            _assert_snapshot(obj, before_delete_fault[name])

        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": regenerated["program_id"],
                "expected_revision": regenerated["revision"],
                "reason": "Inspection production integration complete",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        finished = finish_delete(
            prepared_delete,
            delete_live_program(service, prepared_delete),
        )
        assert finished["ok"] is True
        assert not _managed_names(reopened, regenerated["program_id"])
        App.closeDocument(reopened.Name)
        return {
            "stable_native_outputs": True,
            "failed_candidate_retention": True,
            "exact_worker_host_validation": True,
            "explicit_publication_rollback": True,
            "explicit_deletion_rollback": True,
            "save_reopen": True,
            "external_reference_guard": True,
            "stale_without_synchronous_recompute": True,
            "isolated_domain_context": True,
            "model_correctable_failure_recovery": True,
            "exact_result_contract": True,
        }
    finally:
        if App.ActiveDocument is not None:
            App.closeDocument(App.ActiveDocument.Name)
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    _exercise_source_api()
    root = Path(tempfile.mkdtemp(prefix="vibecad-inspection-api-"))
    try:
        result = _exercise_worker(root)
        lifecycle = _exercise_lifecycle()
        print(
            json.dumps(
                {
                    **result,
                    **lifecycle,
                    "integration": "inspection_vibescript_worker",
                    "canonical_provider_operations": len(EXPORTS),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
