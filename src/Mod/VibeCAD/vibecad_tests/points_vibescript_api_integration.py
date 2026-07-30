# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native production gate for the canonical Points VibeScript domain."""

from __future__ import annotations

import copy
import inspect
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
import Points  # noqa: E402

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
from VibeCADPointArtifacts import (  # noqa: E402
    approve_point_artifact,
    point_artifact_program_references,
    point_artifacts_summary,
    remove_point_artifact,
    resolve_point_artifacts,
)
import VibeCADVibeScriptDomainPublication as publication_module  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    delete_live_program,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    PointsDomainAdapter,
    accept_candidate,
    capture_reference_inputs,
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
    _points_document_snapshot,
    complete_domain_context,
    domain_context_snapshot,
    get_vibescript_pack,
    validate_program_source,
)
from vibescript_points_api import PointsAPIError, PointsDomainAPI  # noqa: E402
from vibescript_points_worker import (  # noqa: E402
    PointsCandidateError,
    VALIDATION_SCHEMA,
    point_facts,
    validate_points_definition,
)


EXPORTS = ("point_cloud",)
EXPECTED_OUTPUTS = [{"name": "Cloud", "type": "points"}]


class _Service:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _active_document():
        return App.ActiveDocument

    @staticmethod
    def active_workbench_name() -> str:
        return "PointsWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "points-native-fixture-revision"

    def project_scope_snapshot(self) -> dict[str, str]:
        return {"root": str(self.root), "project_id": "points-native-fixture"}

    @staticmethod
    def provider_working_set() -> dict[str, object]:
        return {"target_count": 0, "targets": []}

    @staticmethod
    def selection_summary() -> dict[str, object]:
        return {"selection": []}


def _api() -> PointsDomainAPI:
    return PointsDomainAPI(EXPORTS, ("points",))


def _expect_error(fragment: str, call) -> None:
    try:
        call()
    except (TypeError, ValueError, RuntimeError, PointsCandidateError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f"Expected Points failure containing {fragment!r}.")


def _exercise_source_api() -> None:
    api = _api()
    assert api.exported_names == EXPORTS
    assert not hasattr(api, "output")
    for forbidden in ("load_artifact", "transform", "filter", "downsample", "points"):
        assert not hasattr(api, forbidden)
    signature = str(inspect.signature(api.point_cloud))
    assert "*args" not in signature and "**" not in signature
    assert inspect.getdoc(api.point_cloud)

    value = api.point_cloud(
        [[0, 0, 0], [1, 2, 3], [1.01, 2, 3]],
        pipeline=[
            {
                "op": "transform",
                "translation": [1, 2, 3],
                "rotation": [0, 0, 0, 2],
                "scale": [2, 3, 4],
            },
            {"op": "filter", "method": "deduplicate", "tolerance": 0.1},
            {
                "op": "sample",
                "method": "voxel",
                "voxel_size": 1.0,
                "reduction": "centroid",
            },
        ],
        invalid_points="drop",
        label="Canonical",
    )
    assert validate_points_definition(value) == value.to_payload()
    try:
        value.properties["pipeline"][0]["translation"] = (9, 9, 9)
    except TypeError:
        pass
    else:
        raise AssertionError("Points graph values must be deeply immutable.")

    reference = {"document_uid": "document", "object_name": "Cloud"}
    artifact = {"artifact_id": "a" * 32}
    assert api.point_cloud(reference).arguments[0]["kind"] == "document"
    assert api.point_cloud(artifact).arguments[0]["kind"] == "artifact"
    try:
        api.point_cloud({"document_uid": 1, "object_name": "Cloud"})
    except PointsAPIError as exc:
        assert exc.details["stage"] == "source_validation"
        assert exc.details["operation"] == "point_cloud"
        assert exc.details["parameter"] == "source.document_uid"
        assert "Change only the failing source expression" in exc.details["correction"]
    else:
        raise AssertionError("Expected one structured Points source failure.")
    _expect_error("source", lambda: api.point_cloud("/tmp/raw.xyz"))
    _expect_error(
        "32-character lowercase hexadecimal",
        lambda: api.point_cloud({"artifact_id": "bad"}),
    )
    _expect_error(
        "identity transform",
        lambda: api.point_cloud([[0, 0, 0]], pipeline=[{"op": "transform"}]),
    )
    _expect_error(
        "unused by filter method",
        lambda: api.point_cloud(
            [[0, 0, 0]],
            pipeline=[
                {
                    "op": "filter",
                    "method": "crop_box",
                    "minimum": [0, 0, 0],
                    "maximum": [1, 1, 1],
                    "tolerance": 0.1,
                }
            ],
        ),
    )
    _expect_error(
        "step",
        lambda: api.point_cloud(
            [[0, 0, 0]],
            pipeline=[{"op": "sample", "method": "stride", "step": 1}],
        ),
    )

    pack = get_vibescript_pack("PointsWorkbench")
    assert pack is not None
    description = PointsDomainAdapter(pack).describe_api()
    assert description["api_contract"] == "vibecad-vibescript-points-api-v1"
    assert [item["name"] for item in description["runtime_exports"]] == [
        "point_cloud"
    ]
    assert "one point_cloud operation" in description["redundancy_contract"]
    assert "strictly left to right" in description["composition_contract"][
        "ordered_execution"
    ]
    assert "world coordinates" in description["canonical_operation"]["point_cloud"][
        "coordinate_frame"
    ]
    assert "operation_trace" in description["model_verification_contract"][
        "pipeline_evidence"
    ]
    assert "active workbench determines" in description["workbench_handoffs"]["rule"]
    assert len(json.dumps(description, allow_nan=False).encode("utf-8")) < 32 * 1024
    assert description["execution_contract"]["no_synchronous_fallback"] is True
    validate_program_source(description["recommended_patterns"][0]["source"])


def _write_asc(path: Path) -> None:
    path.write_text(
        "0 0 0\n1 0 0\n2 0 0\n3 0 0\n4 0 0\n",
        encoding="utf-8",
    )


def _write_ply(path: Path) -> None:
    path.write_text(
        "ply\n"
        "format ascii 1.0\n"
        "element vertex 4\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "end_header\n"
        "0 0 0\n0 1 0\n0 2 0\n0 3 0\n",
        encoding="utf-8",
    )


def _exercise_artifact_registry(root: Path) -> tuple[dict, dict]:
    source_root = root / "human-sources"
    source_root.mkdir()
    asc_path = source_root / "scan.asc"
    ply_path = source_root / "scan.ply"
    tamper_path = source_root / "tamper.xyz"
    _write_asc(asc_path)
    _write_ply(ply_path)
    _write_asc(tamper_path)
    asc = approve_point_artifact(root, asc_path, label="Approved ASC")
    ply = approve_point_artifact(root, ply_path, label="Approved PLY")
    tamper = approve_point_artifact(root, tamper_path, label="Tamper probe")
    summary = point_artifacts_summary(root)
    assert summary["artifact_count"] == 3
    assert all("path" not in item for item in summary["artifacts"])
    assert all(item["available"] is True for item in summary["artifacts"])
    resolved = resolve_point_artifacts(root, [tamper["artifact_id"]])[0]
    approved_copy = Path(resolved["path"])
    approved_copy.write_text("changed\n", encoding="utf-8")
    _expect_error(
        "changed size",
        lambda: resolve_point_artifacts(root, [tamper["artifact_id"]]),
    )
    shutil.copyfile(tamper_path, approved_copy)
    assert resolve_point_artifacts(root, [tamper["artifact_id"]])
    removed = remove_point_artifact(root, tamper["artifact_id"])
    assert removed["artifact_copy_deleted"] is True
    return asc, ply


def _captured(
    root: Path,
    document,
    *,
    operation: str,
    arguments: dict,
) -> dict:
    pack = get_vibescript_pack("PointsWorkbench")
    assert pack is not None
    return {
        "tool_name": f"vibescript.points.{operation}",
        "operation": operation,
        "arguments": arguments,
        "pack": pack,
        "project_root": str(root),
        "project_id": "points-native-fixture",
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "points-native-fixture-revision",
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
            "PointsWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 90.0,
        "memory_limit_bytes": 3 * 1024 * 1024 * 1024,
    }


def _prepare_execute_validate(captured: dict, service: _Service):
    prepared = prepare_candidate(captured)
    staged_names = {path.name for path in Path(prepared["staging"]).iterdir()}
    assert {
        "worker.py",
        "vibescript_domain_api.py",
        "vibescript_points_api.py",
        "vibescript_points_worker.py",
    } <= staged_names
    assert not any(
        name.startswith("vibescript_")
        and name.endswith(("_api.py", "_worker.py"))
        and name
        not in {
            "vibescript_domain_api.py",
            "vibescript_points_api.py",
            "vibescript_points_worker.py",
        }
        for name in staged_names
    )
    if prepared["reference_requirements"]:
        prepared = finalize_candidate(
            prepared,
            capture_reference_inputs(service, prepared),
        )
    assert (Path(prepared["staging"]) / "request.json").is_file()
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    return prepared, execution, validate_candidate(prepared, execution)


def _run_candidate(captured: dict, service: _Service):
    prepared, execution, validated = _prepare_execute_validate(captured, service)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    return prepared, execution, validated, publication, accepted


def _input_schema() -> dict:
    reference = {
        "type": "object",
        "x-vibecad-reference": True,
        "properties": {
            "document_uid": {"type": "string", "minLength": 1},
            "object_name": {"type": "string", "minLength": 1},
        },
        "required": ["document_uid", "object_name"],
        "additionalProperties": False,
    }
    artifact = {
        "type": "object",
        "x-vibecad-point-artifact": True,
        "properties": {
            "artifact_id": {
                "type": "string",
                "pattern": "^[0-9a-f]{32}$",
            }
        },
        "required": ["artifact_id"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "source": {"oneOf": [reference, artifact]},
            "offset": {"type": "number", "minimum": -1000, "maximum": 1000},
            "max_points": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        "required": ["source", "offset", "max_points"],
        "additionalProperties": False,
    }


def _source() -> str:
    return (
        "cloud = api.point_cloud(inputs['source'], pipeline=["
        "{'op':'transform','translation':[inputs['offset'],1,2],"
        "'rotation':[0,0,0.7071067811865476,0.7071067811865476],"
        "'scale':[2,1,1]},"
        "{'op':'filter','method':'crop_box','minimum':[-1000,-1000,-1000],"
        "'maximum':[1000,1000,1000]},"
        "{'op':'sample','method':'limit','max_points':inputs['max_points']}],"
        "invalid_points='reject', preserve_attributes=True, label='Processed Cloud')\n"
        "result = {'Cloud': cloud}\n"
    )


def _managed_names(document, program_id: str) -> set[str]:
    return {
        str(obj.Name)
        for obj in document.Objects
        if str(getattr(obj, PROP_PROGRAM_ID, "") or "") == program_id
    }


def _output(document, accepted: dict):
    name = accepted["live_outputs"]["Cloud"]["object_name"]
    obj = document.getObject(name)
    assert obj is not None
    return obj


def _attributes(obj) -> dict[str, list]:
    return {
        "colors": [tuple(float(component) for component in value) for value in obj.Color]
        if hasattr(obj, "Color")
        else [],
        "intensities": [float(value) for value in obj.Intensity]
        if hasattr(obj, "Intensity")
        else [],
        "normals": [(float(value.x), float(value.y), float(value.z)) for value in obj.Normal]
        if hasattr(obj, "Normal")
        else [],
    }


def _snapshot(obj) -> dict:
    kernel = obj.Points.copy()
    kernel.Placement = App.Placement()
    structured = None
    if hasattr(obj, "Width") and hasattr(obj, "Height"):
        width = int(obj.Width)
        height = int(obj.Height)
        if width > 0 and height > 0 and width * height == int(kernel.CountPoints):
            structured = {"width": width, "height": height}
    attributes = _attributes(obj)
    return {
        "name": str(obj.Name),
        "label": str(obj.Label),
        "revision": str(obj.VibeCADVibeScriptRevision),
        "definition": str(obj.VibeCADVibeScriptDefinition),
        "validation": str(obj.VibeCADPointsValidation),
        "placement": [
            float(value)
            for value in (
                obj.Placement.Base.x,
                obj.Placement.Base.y,
                obj.Placement.Base.z,
                *obj.Placement.Rotation.Q,
            )
        ],
        "human_note": str(getattr(obj, "HumanPointNote", "") or ""),
        "human_expression": [
            [str(path), str(expression)]
            for path, expression in list(obj.ExpressionEngine or [])
        ],
        "facts": point_facts(
            kernel,
            attributes=attributes,
            structured=structured,
        ),
        "attributes": attributes,
    }


def _expression(obj, property_name: str) -> str:
    return next(
        (
            str(expression)
            for path, expression in list(obj.ExpressionEngine or [])
            if str(path).lstrip(".") == property_name
        ),
        "",
    )


def _assert_snapshot(obj, expected: dict) -> None:
    observed = _snapshot(obj)
    for left, right in zip(observed["placement"], expected["placement"]):
        assert math.isclose(left, right, rel_tol=1.0e-12, abs_tol=1.0e-12)
    observed_values = {
        key: value for key, value in observed.items() if key != "placement"
    }
    expected_values = {
        key: value for key, value in expected.items() if key != "placement"
    }
    if observed_values != expected_values:
        differences = {}
        for key in sorted(set(observed_values) | set(expected_values)):
            left = observed_values.get(key)
            right = expected_values.get(key)
            if left == right:
                continue
            differences[key] = {
                "observed": (
                    {"length": len(left), "prefix": left[:160]}
                    if isinstance(left, str) and len(left) > 200
                    else left
                ),
                "expected": (
                    {"length": len(right), "prefix": right[:160]}
                    if isinstance(right, str) and len(right) > 200
                    else right
                ),
            }
        raise AssertionError(json.dumps(differences, sort_keys=True))


def _assert_native_output(obj, expected_facts: dict) -> None:
    assert str(obj.TypeId) == "Points::Feature"
    assert "VibeCADPointsValidation" in obj.PropertiesList
    kernel = obj.Points.copy()
    kernel.Placement = App.Placement()
    structured = expected_facts.get("structured")
    observed = point_facts(
        kernel,
        attributes=_attributes(obj),
        structured=structured,
    )
    assert observed == expected_facts
    validation = json.loads(str(obj.VibeCADPointsValidation))
    assert validation["schema"] == VALIDATION_SCHEMA
    assert validation["facts"] == expected_facts


def main() -> int:
    _exercise_source_api()
    root = Path(tempfile.mkdtemp(prefix="vibecad-points-native-"))
    document = App.newDocument("VibeScriptPointsNative")
    service = _Service(root)
    try:
        App.setActiveDocument(document.Name)
        asc_artifact, ply_artifact = _exercise_artifact_registry(root)
        surface = resolve_modeling_surface("PointsWorkbench", "vibescript")
        assert surface.available is True, surface.unavailable_reason
        assert surface.cad_tool_names == tuple(
            f"vibescript.points.{name}"
            for name in (
                "create_program",
                "edit_source",
                "set_inputs",
                "reconfigure_program",
                "delete_program",
            )
        )

        source_obj = document.addObject("Points::Feature", "HumanPointSource")
        source_obj.Points = Points.Points(
            [
                App.Vector(0, 0, 0),
                App.Vector(1, 0, 0),
                App.Vector(2, 0, 0),
                App.Vector(0, 1, 0),
                App.Vector(1, 1, 0),
                App.Vector(2, 1, 0),
            ]
        )
        source_obj.addProperty("App::PropertyColorList", "Color", "Points")
        source_obj.addProperty(
            "Points::PropertyGreyValueList", "Intensity", "Points"
        )
        source_obj.addProperty("Points::PropertyNormalList", "Normal", "Points")
        source_obj.addProperty("App::PropertyInteger", "Width", "Points")
        source_obj.addProperty("App::PropertyInteger", "Height", "Points")
        source_obj.Color = [
            (1.0, 0.0, 0.0, 1.0),
            (0.0, 1.0, 0.0, 1.0),
            (0.0, 0.0, 1.0, 1.0),
            (1.0, 1.0, 0.0, 1.0),
            (0.0, 1.0, 1.0, 1.0),
            (1.0, 0.0, 1.0, 1.0),
        ]
        source_obj.Intensity = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        source_obj.Normal = [App.Vector(0, 0, 1)] * 6
        source_obj.Width = 3
        source_obj.Height = 2
        source_obj.Placement = App.Placement(
            App.Vector(10, 20, 30),
            App.Rotation(App.Vector(0, 0, 1), 90),
        )
        document_reference = {
            "document_uid": str(document.Uid),
            "object_name": str(source_obj.Name),
        }
        create_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments={
                "program_name": "Native Points Lifecycle",
                "source": _source(),
                "input_schema": _input_schema(),
                "inputs": {
                    "source": document_reference,
                    "offset": 0.0,
                    "max_points": 6,
                },
                "expected_outputs": EXPECTED_OUTPUTS,
            },
        )
        prepared, execution, validated = _prepare_execute_validate(
            create_capture, service
        )
        assert execution["points_validation"]["output_count"] == 1
        facts = validated["outputs"][0]["facts"]
        assert facts["points"] == 6
        assert facts["structured"] == {"width": 3, "height": 2}
        assert sorted(facts["attributes"]) == ["colors", "intensities", "normals"]
        assert facts["bounds"]["minimum"] == [-22.0, 19.0, 32.0]
        assert facts["bounds"]["maximum"] == [-20.0, 21.0, 32.0]
        assert facts["centroid"] == [-21.0, 20.0, 32.0]
        assert [item["op"] for item in validated["outputs"][0]["points_data"]["operation_trace"]] == [
            "transform",
            "filter",
            "sample",
        ]

        malformed = copy.deepcopy(execution)
        malformed["outputs"][0]["artifact_sha256"] = "0" * 64
        _expect_error("SHA-256 changed", lambda: validate_candidate(prepared, malformed))
        malformed = copy.deepcopy(execution)
        malformed["outputs"][0]["points_data"]["facts"]["points"] += 1
        _expect_error("facts changed", lambda: validate_candidate(prepared, malformed))
        malformed = copy.deepcopy(execution)
        malformed["outputs"][0]["points_data"]["operation_trace"][0][
            "output_count"
        ] -= 1
        _expect_error("trace stage", lambda: validate_candidate(prepared, malformed))
        malformed = copy.deepcopy(execution)
        malformed["outputs"][0]["attribute_artifacts"][0]["artifact_sha256"] = (
            "0" * 64
        )
        _expect_error("SHA-256", lambda: validate_candidate(prepared, malformed))

        retain_candidate(prepared, status="validated")
        publication = publish_candidate(service, prepared, validated)
        accepted = accept_candidate(prepared, publication)
        obj = _output(document, accepted)
        stable_name = str(obj.Name)
        _assert_native_output(obj, facts)
        assert _managed_names(document, prepared["program_id"]) == {stable_name}
        obj.addProperty(
            "App::PropertyString",
            "HumanPointNote",
            "Human",
            "Human-authored metadata regeneration and rollback must preserve.",
        )
        obj.HumanPointNote = "preserve this human value"
        obj.addProperty("App::PropertyLength", "HumanPointScale", "Human")
        obj.setExpression("HumanPointScale", "2 mm + 3 mm")
        obj.Placement = App.Placement(
            App.Vector(11, 12, 13),
            App.Rotation(App.Vector(0, 1, 0), 15),
        )
        consumer = document.addObject("App::FeaturePython", "HumanPointConsumer")
        consumer.addProperty("App::PropertyLink", "Source")
        consumer.Source = obj

        crop_bounds = (
            "'minimum':[-1000,-1000,-1000],'maximum':[1000,1000,1000]"
        )
        empty_bounds = (
            "'minimum':[5000,5000,5000],'maximum':[6000,6000,6000]"
        )
        failed_capture = _captured(
            root,
            document,
            operation="edit_source",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "source": _source().replace(crop_bounds, empty_bounds),
            },
        )
        failed_prepared = prepare_candidate(failed_capture)
        failed_prepared = finalize_candidate(
            failed_prepared,
            capture_reference_inputs(service, failed_prepared),
        )
        failed_execution = execute_candidate(
            failed_prepared,
            cancellation_check=None,
        )
        assert failed_execution["ok"] is False
        assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
        assert failed_execution["domain_failure_stage"] == "pipeline_empty"
        failure_details = failed_execution["observed"]["details"]
        assert failure_details["pipeline_index"] == 1
        assert failure_details["input_count"] == 6
        assert failed_execution["retry"]["required_changes"] == [
            failure_details["correction"]
        ]
        assert "pipeline[1]" in failure_details["correction"]
        retain_candidate(
            failed_prepared,
            status="failed",
            failure=failed_execution,
        )
        assert document.getObject(stable_name) is obj
        assert consumer.Source is obj
        assert str(obj.VibeCADVibeScriptRevision) == accepted["accepted_revision"]

        recovery_capture = _captured(
            root,
            document,
            operation="edit_source",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": failed_prepared["revision"],
                "source": _source(),
            },
        )
        _, _, _, recovery_publication, accepted = _run_candidate(
            recovery_capture,
            service,
        )
        assert recovery_publication["created_objects"] == []
        assert _output(document, accepted) is obj
        assert consumer.Source is obj
        assert obj.HumanPointNote == "preserve this human value"
        assert _expression(obj, "HumanPointScale")

        inspection = complete_inspection(
            {
                **create_capture,
                "program_id": prepared["program_id"],
                "live_programs": [],
            }
        )
        assert inspection["program"]["live_outputs"]["Cloud"]["points_data"]

        input_capture = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "patch": {"max_points": 3, "offset": 5.0},
            },
        )
        _, _, input_validated, input_publication, accepted = _run_candidate(
            input_capture, service
        )
        assert input_publication["created_objects"] == []
        assert _output(document, accepted) is obj
        assert consumer.Source is obj
        assert input_validated["outputs"][0]["facts"]["points"] == 3
        assert input_validated["outputs"][0]["facts"]["structured"] is None
        assert obj.HumanPointNote == "preserve this human value"
        assert _expression(obj, "HumanPointScale")
        assert [obj.Placement.Base.x, obj.Placement.Base.y, obj.Placement.Base.z] == [
            11.0,
            12.0,
            13.0,
        ]

        for artifact, expected_points in ((asc_artifact, 4), (ply_artifact, 4)):
            artifact_capture = _captured(
                root,
                document,
                operation="set_inputs",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": accepted["working_revision"],
                    "patch": {
                        "source": {"artifact_id": artifact["artifact_id"]},
                        "max_points": expected_points,
                    },
                },
            )
            _, _, artifact_validated, _, accepted = _run_candidate(
                artifact_capture, service
            )
            assert artifact_validated["outputs"][0]["facts"]["points"] == expected_points
            references = point_artifact_program_references(
                root, artifact["artifact_id"]
            )
            assert references and references[0]["accepted_reference"] is True
            _expect_error(
                "programs reference it",
                lambda artifact_id=artifact["artifact_id"]: remove_point_artifact(
                    root, artifact_id
                ),
            )

        restore_capture = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "patch": {"source": document_reference, "max_points": 4},
            },
        )
        restore_prepared, _, restore_validated = _prepare_execute_validate(
            restore_capture, service
        )
        retain_candidate(restore_prepared, status="validated")
        before_publication_fault = _snapshot(obj)
        original_configure = publication_module._configure_points

        def fail_after_assignment(*args, **kwargs):
            original_configure(*args, **kwargs)
            raise RuntimeError("injected Points publication failure")

        publication_module._configure_points = fail_after_assignment
        try:
            _expect_error(
                "injected Points publication failure",
                lambda: publish_candidate(service, restore_prepared, restore_validated),
            )
        finally:
            publication_module._configure_points = original_configure
        obj = document.getObject(stable_name)
        assert obj is not None
        _assert_snapshot(obj, before_publication_fault)
        assert consumer.Source is obj

        restored_publication = publish_candidate(
            service, restore_prepared, restore_validated
        )
        accepted = accept_candidate(restore_prepared, restored_publication)
        obj = _output(document, accepted)
        assert str(obj.Name) == stable_name
        assert consumer.Source is obj
        assert obj.HumanPointNote == "preserve this human value"
        _assert_native_output(obj, restore_validated["outputs"][0]["facts"])
        for artifact in (asc_artifact, ply_artifact):
            assert not point_artifact_program_references(root, artifact["artifact_id"])
            assert remove_point_artifact(root, artifact["artifact_id"])[
                "artifact_copy_deleted"
            ] is True

        raw_context = _points_document_snapshot(document)
        managed_context = next(
            item for item in raw_context["objects"] if item["name"] == stable_name
        )
        assert managed_context["native_summary"]["points"] == 4
        assert len(managed_context["native_summary"]["sample"]) == 4
        context = complete_domain_context(domain_context_snapshot(service, "points"))
        point_context = next(
            item
            for item in context["document_point_clouds"]["objects"]
            if item["name"] == stable_name
        )
        assert point_context["accepted_validation"]["schema"] == VALIDATION_SCHEMA
        assert context["approved_point_artifacts"]["artifact_count"] == 0

        save_path = root / "points-production.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        reopened = App.openDocument(str(save_path))
        assert reopened is not None
        App.setActiveDocument(reopened.Name)
        reopened.recompute()
        obj = reopened.getObject(stable_name)
        consumer = reopened.getObject("HumanPointConsumer")
        assert obj is not None and consumer is not None
        assert consumer.Source is obj
        assert obj.HumanPointNote == "preserve this human value"
        assert _expression(obj, "HumanPointScale")
        _assert_native_output(obj, restore_validated["outputs"][0]["facts"])

        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": restore_prepared["program_id"],
                "expected_revision": restore_prepared["revision"],
                "reason": "verify Points external-reference guard",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        _expect_error("reference", lambda: delete_live_program(service, prepared_delete))
        restore_prepared_delete(prepared_delete)
        consumer.Source = None
        reopened.removeObject(consumer.Name)

        before_delete_fault = _snapshot(obj)
        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": restore_prepared["program_id"],
                "expected_revision": restore_prepared["revision"],
                "reason": "exercise explicit Points deletion rollback",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        original_remove = publication_module._remove_owned_objects

        def fail_after_committed_removal(active_document, managed_objects):
            first = next(iter(managed_objects))
            active_document.removeObject(first.Name)
            active_document.commitTransaction()
            raise RuntimeError("injected Points deletion failure")

        publication_module._remove_owned_objects = fail_after_committed_removal
        try:
            _expect_error(
                "injected Points deletion failure",
                lambda: delete_live_program(service, prepared_delete),
            )
            restore_prepared_delete(prepared_delete)
        finally:
            publication_module._remove_owned_objects = original_remove
        obj = reopened.getObject(stable_name)
        assert obj is not None
        _assert_snapshot(obj, before_delete_fault)

        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": restore_prepared["program_id"],
                "expected_revision": restore_prepared["revision"],
                "reason": "Points production integration complete",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        finished = finish_delete(
            prepared_delete,
            delete_live_program(service, prepared_delete),
        )
        assert finished["ok"] is True
        assert not _managed_names(reopened, restore_prepared["program_id"])
        App.closeDocument(reopened.Name)
        print(
            json.dumps(
                {
                    "ok": True,
                    "integration": "points_vibescript_api",
                    "stable_output": stable_name,
                    "canonical_provider_operations": 1,
                    "document_and_asc_and_ply_sources": True,
                    "attribute_preservation": True,
                    "model_correctable_failure_recovery": True,
                    "single_source_placement_bake": True,
                    "explicit_publication_rollback": True,
                    "explicit_deletion_rollback": True,
                    "artifact_reference_guard": True,
                    "save_reopen": True,
                    "external_reference_guard": True,
                    "exact_worker_host_validation": True,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    finally:
        if App.ActiveDocument is not None:
            App.closeDocument(App.ActiveDocument.Name)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
