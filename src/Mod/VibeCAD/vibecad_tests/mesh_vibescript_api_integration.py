# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native production gate for the Mesh VibeScript domain."""

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

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
import VibeCADVibeScriptDomainPublication as publication_module  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    delete_live_program,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    MeshDomainAdapter,
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
    _mesh_document_snapshot,
    complete_domain_context,
    domain_context_snapshot,
    get_vibescript_pack,
    validate_program_source,
)
from vibescript_mesh_api import MeshDomainAPI  # noqa: E402
from vibescript_mesh_worker import (  # noqa: E402
    MeshCandidateError,
    VALIDATION_SCHEMA,
    mesh_diagnostics,
    validate_mesh_definition,
)


EXPORTS = ("mesh", "from_object", "transform", "repair", "diagnostics")
EXPECTED_OUTPUTS = [{"name": "Mesh", "type": "mesh"}]


class _Service:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _active_document():
        return App.ActiveDocument

    @staticmethod
    def active_workbench_name() -> str:
        return "MeshWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "mesh-native-fixture-revision"

    def project_scope_snapshot(self) -> dict[str, str]:
        return {"root": str(self.root), "project_id": "mesh-native-fixture"}


def _captured(
    root: Path,
    document,
    *,
    operation: str,
    arguments: dict,
) -> dict:
    pack = get_vibescript_pack("MeshWorkbench")
    assert pack is not None
    return {
        "tool_name": f"vibescript.mesh.{operation}",
        "operation": operation,
        "arguments": arguments,
        "pack": pack,
        "project_root": str(root),
        "project_id": "mesh-native-fixture",
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "mesh-native-fixture-revision",
        "document_objects": [
            {
                "name": str(obj.Name),
                "label": str(obj.Label),
                "type_id": str(obj.TypeId),
            }
            for obj in document.Objects
        ],
        "live_programs": [],
        "surface": resolve_modeling_surface("MeshWorkbench", "vibescript").summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 90.0,
        "memory_limit_bytes": 3 * 1024 * 1024 * 1024,
    }


def _prepare_execute_validate(captured: dict, service: _Service | None = None):
    prepared = prepare_candidate(captured)
    if prepared["reference_requirements"]:
        assert service is not None
        prepared = finalize_candidate(
            prepared,
            capture_reference_inputs(service, prepared),
        )
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    return prepared, execution, validate_candidate(prepared, execution)


def _run_candidate(captured: dict, service: _Service):
    prepared, execution, validated = _prepare_execute_validate(captured, service)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    return prepared, execution, validated, publication, accepted


def _api() -> MeshDomainAPI:
    return MeshDomainAPI(EXPORTS, ("mesh",))


def _expect_error(fragment: str, call) -> None:
    try:
        call()
    except (TypeError, ValueError, RuntimeError, MeshCandidateError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f"Expected Mesh failure containing {fragment!r}.")


def _tetrahedron(height: float = 1.0) -> list[list[list[float]]]:
    return [
        [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        [[0, 0, 0], [0, 0, height], [1, 0, 0]],
        [[0, 0, 0], [0, 1, 0], [0, 0, height]],
        [[1, 0, 0], [0, 0, height], [0, 1, 0]],
    ]


def _exercise_source_api() -> None:
    import Mesh

    api = _api()
    assert api.exported_names == EXPORTS
    assert not hasattr(api, "output")
    for export in EXPORTS:
        member = getattr(api, export)
        signature = str(inspect.signature(member))
        assert "*args" not in signature and "**" not in signature
        assert inspect.getdoc(member)

    raw = api.mesh(_tetrahedron(), label="Raw")
    imported = api.from_object(
        {"document_uid": "document", "object_name": "ExistingMesh"},
        label="Imported",
    )
    assert validate_mesh_definition(imported, require_domain_value=True) == (
        imported.to_payload()
    )
    moved = api.transform(
        raw,
        translation=[1, 2, 3],
        rotation=[0, 0, 0, 2],
        scale=[2, 3, 4],
    )
    repaired = api.repair(
        moved,
        remove_non_manifolds=True,
        fix_self_intersections=True,
        fill_holes_max_edges=3,
    )
    _expect_error("at least one explicit repair", lambda: api.repair(raw))
    checked = api.diagnostics(
        repaired,
        require_solid=True,
        require_closed=True,
        require_manifold=True,
        require_consistent_orientation=True,
        require_no_self_intersections=True,
        max_components=1,
        max_open_edges=0,
    )
    assert (
        validate_mesh_definition(
            checked,
            require_domain_value=True,
        )
        == checked.to_payload()
    )
    try:
        raw.arguments[0][0][0] = (9.0, 9.0, 9.0)
    except TypeError:
        pass
    else:
        raise AssertionError("Mesh graph values must be deeply immutable.")

    _expect_error("1-200000 triangles", lambda: api.mesh([]))
    try:
        api.from_object({"document_uid": "document", "object_name": "bad name"})
    except ValueError as exc:
        assert exc.details["stage"] == "source_validation"
        assert exc.details["parameter"] == "reference.object_name"
        assert "Change only the failing source expression" in exc.details["correction"]
    else:
        raise AssertionError("Expected one structured api.from_object source failure.")
    _expect_error(
        "must be finite",
        lambda: api.mesh([[[0, 0, 0], [float("nan"), 0, 0], [0, 1, 0]]]),
    )
    _expect_error("greater than 0", lambda: api.transform(raw, scale=[1, 0, 1]))
    _expect_error("non-zero", lambda: api.transform(raw, rotation=[0, 0, 0, 0]))
    _expect_error(
        "must both be zero",
        lambda: api.repair(raw, decimate_reduction=0.5),
    )
    _expect_error(
        "must be an integer",
        lambda: api.repair(raw, fill_holes_max_edges=True),
    )
    _expect_error(
        "must be true or false",
        lambda: api.diagnostics(raw, require_closed=1),
    )

    crossing = [
        [[0, 0, 0], [2, 0, 0], [1, 2, 0]],
        [[1, -1, -1], [1, 1, 1], [1, 1, -1]],
    ]
    small_intersection = mesh_diagnostics(Mesh.Mesh(crossing))
    assert small_intersection["has_self_intersections"] is True
    assert small_intersection["self_intersection_details_available"] is True
    assert small_intersection["self_intersection_count"] >= 1
    assert small_intersection["self_intersection_sample"]
    separated = [
        [[10 + index * 3, 0, 0], [11 + index * 3, 0, 0], [10 + index * 3, 1, 0]]
        for index in range(127)
    ]
    bounded_intersection = mesh_diagnostics(Mesh.Mesh([*crossing, *separated]))
    assert bounded_intersection["has_self_intersections"] is True
    assert bounded_intersection["self_intersection_details_available"] is False
    assert bounded_intersection["self_intersection_count"] is None
    assert bounded_intersection["self_intersection_sample"] == []
    assert bounded_intersection["self_intersection_sample_truncated"] is True

    pack = get_vibescript_pack("MeshWorkbench")
    assert pack is not None
    description = MeshDomainAdapter(pack).describe_api()
    assert description["api_contract"] == "vibecad-vibescript-mesh-api-v1"
    assert [item["name"] for item in description["runtime_exports"]] == list(EXPORTS)
    assert "BMS" in description["evaluation_model"]
    assert "self-intersection" in description["operation_contracts"]["diagnostics"]
    assert "default-only diagnostics" in description["redundancy_contract"]
    assert set(description["operation_selection"]) == set(EXPORTS)
    assert (
        len(
            json.dumps(description, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        < 32_000
    )
    for pattern in description["recommended_patterns"]:
        validate_program_source(pattern["source"])


def _source() -> str:
    return (
        "raw = api.mesh([[[0,0,0],[1,0,0],[0,1,0]],"
        "[[0,0,0],[0,0,inputs['height']],[1,0,0]],"
        "[[0,0,0],[0,1,0],[0,0,inputs['height']]]], label='Open Mesh')\n"
        "fixed = api.repair(raw, fill_holes_max_edges=3, label='Repaired Mesh')\n"
        "moved = api.transform(fixed, translation=[inputs['offset'],2,3], "
        "rotation=[0,0,0.7071067811865476,0.7071067811865476], "
        "scale=[2,3,1], label='Transformed Mesh')\n"
        "checked = api.diagnostics(moved, require_solid=True, require_closed=True, "
        "require_manifold=True, require_consistent_orientation=True, "
        "require_no_self_intersections=True, max_components=1, max_open_edges=0, "
        "label='Checked Mesh')\n"
        "result = {'Mesh': checked}\n"
    )


def _input_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "height": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 1000,
            },
            "offset": {
                "type": "number",
                "minimum": -1000,
                "maximum": 1000,
            },
        },
        "required": ["height", "offset"],
        "additionalProperties": False,
    }


def _create_arguments() -> dict:
    return {
        "program_name": "Native Mesh Lifecycle",
        "source": _source(),
        "input_schema": _input_schema(),
        "inputs": {"height": 1.0, "offset": 0.0},
        "expected_outputs": EXPECTED_OUTPUTS,
    }


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


def _reference_source() -> str:
    return (
        "source = api.from_object(inputs['source'], label='Human Source Snapshot')\n"
        "fixed = api.repair(source, remove_duplicate_points=False, "
        "remove_duplicate_facets=False, fix_degenerations=False, "
        "fill_holes_max_edges=3, harmonize_normals=True, label='Filled Mesh')\n"
        "checked = api.diagnostics(fixed, require_solid=True, require_closed=True, "
        "require_manifold=True, require_consistent_orientation=True, "
        "max_components=1, max_open_edges=0, label='Imported and Repaired')\n"
        "result = {'Mesh': checked}\n"
    )


def _reference_arguments(document, source) -> dict[str, object]:
    return {
        "program_name": "Existing Mesh Repair Lifecycle",
        "source": _reference_source(),
        "input_schema": {
            "type": "object",
            "properties": {"source": _reference_schema()},
            "required": ["source"],
            "additionalProperties": False,
        },
        "inputs": {
            "source": {
                "document_uid": str(document.Uid),
                "object_name": str(source.Name),
            }
        },
        "expected_outputs": EXPECTED_OUTPUTS,
    }


def _managed_names(document, program_id: str) -> set[str]:
    return {
        str(obj.Name)
        for obj in document.Objects
        if str(getattr(obj, PROP_PROGRAM_ID, "") or "") == program_id
    }


def _output(document, accepted: dict):
    name = accepted["live_outputs"]["Mesh"]["object_name"]
    obj = document.getObject(name)
    assert obj is not None
    return obj


def _snapshot(obj) -> dict:
    local_mesh = obj.Mesh.copy()
    local_mesh.Placement = App.Placement()
    diagnostics = mesh_diagnostics(local_mesh)
    return {
        "name": str(obj.Name),
        "label": str(obj.Label),
        "revision": str(obj.VibeCADVibeScriptRevision),
        "definition": str(obj.VibeCADVibeScriptDefinition),
        "validation": str(obj.VibeCADMeshValidation),
        "placement": [
            float(value)
            for value in (
                obj.Placement.Base.x,
                obj.Placement.Base.y,
                obj.Placement.Base.z,
                *obj.Placement.Rotation.Q,
            )
        ],
        "human_note": str(getattr(obj, "HumanMeshNote", "") or ""),
        "diagnostics": diagnostics,
    }


def _assert_snapshot(obj, expected: dict) -> None:
    observed = _snapshot(obj)
    expected_placement = list(expected["placement"])
    observed_placement = list(observed["placement"])
    assert len(expected_placement) == len(observed_placement) == 7
    assert all(
        math.isclose(left, right, rel_tol=1.0e-12, abs_tol=1.0e-12)
        for left, right in zip(observed_placement, expected_placement)
    )
    expected_without_placement = {
        key: value for key, value in expected.items() if key != "placement"
    }
    observed_without_placement = {
        key: value for key, value in observed.items() if key != "placement"
    }
    assert observed_without_placement == expected_without_placement, json.dumps(
        {"expected": expected, "observed": observed},
        ensure_ascii=True,
        sort_keys=True,
    )


def _assert_native_output(obj, expected_facts: dict) -> None:
    assert str(obj.TypeId) == "Mesh::Feature"
    assert "VibeCADMeshValidation" in obj.PropertiesList
    local_mesh = obj.Mesh.copy()
    local_mesh.Placement = App.Placement()
    observed = mesh_diagnostics(local_mesh)
    assert observed == expected_facts
    validation = json.loads(str(obj.VibeCADMeshValidation))
    assert validation["schema"] == VALIDATION_SCHEMA
    assert validation["diagnostics"] == expected_facts


def main() -> int:
    _exercise_source_api()
    root = Path(tempfile.mkdtemp(prefix="vibecad-mesh-native-"))
    document = App.newDocument("VibeScriptMeshNative")
    service = _Service(root)
    try:
        App.setActiveDocument(document.Name)
        surface = resolve_modeling_surface("MeshWorkbench", "vibescript")
        assert surface.available is True, surface.unavailable_reason
        assert surface.cad_tool_names == tuple(
            f"vibescript.mesh.{name}"
            for name in (
                "create_program",
                "edit_source",
                "set_inputs",
                "reconfigure_program",
                "delete_program",
            )
        )

        create_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments=_create_arguments(),
        )
        prepared, execution, validated = _prepare_execute_validate(create_capture)
        assert execution["mesh_validation"]["output_count"] == 1
        assert execution["mesh_validation"]["total_facets"] == 4
        assert validated["outputs"][0]["facts"]["is_solid"] is True
        assert validated["outputs"][0]["facts"]["open_edges"] == 0
        assert [
            item["operation"]
            for item in validated["outputs"][0]["mesh_data"]["operation_trace"]
        ] == ["mesh", "repair", "transform", "diagnostics"]

        malformed = copy.deepcopy(execution)
        malformed["outputs"][0]["mesh_data"]["diagnostics"]["facets"] += 1
        _expect_error(
            "differs from the imported native Mesh",
            lambda: validate_candidate(prepared, malformed),
        )
        malformed = copy.deepcopy(execution)
        malformed["outputs"][0]["mesh_data"]["artifact_sha256"] = "0" * 64
        _expect_error(
            "artifact digest changed",
            lambda: validate_candidate(prepared, malformed),
        )
        malformed = copy.deepcopy(execution)
        malformed["outputs"][0]["mesh_data"]["operation_trace"][1]["applied"] = []
        _expect_error(
            "changed repair passes",
            lambda: validate_candidate(prepared, malformed),
        )

        retain_candidate(prepared, status="validated")
        publication = publish_candidate(service, prepared, validated)
        accepted = accept_candidate(prepared, publication)
        obj = _output(document, accepted)
        stable_name = str(obj.Name)
        _assert_native_output(obj, validated["outputs"][0]["facts"])
        assert _managed_names(document, prepared["program_id"]) == {stable_name}
        obj.addProperty(
            "App::PropertyString",
            "HumanMeshNote",
            "Human",
            "Human-authored metadata that regeneration and rollback must preserve.",
        )
        obj.HumanMeshNote = "preserve this human value"
        obj.Placement = App.Placement(
            App.Vector(11, 12, 13),
            App.Rotation(0, 0, 1, 15),
        )

        inspection = complete_inspection(
            {
                **create_capture,
                "program_id": prepared["program_id"],
                "live_programs": [],
            }
        )
        assert inspection["ok"] is True
        assert inspection["program"]["accepted_revision"] == prepared["revision"]
        assert inspection["program"]["live_outputs"]["Mesh"]["mesh_data"]

        consumer = document.addObject("App::FeaturePython", "HumanMeshConsumer")
        consumer.addProperty("App::PropertyLink", "Source")
        consumer.Source = obj

        failed_capture = _captured(
            root,
            document,
            operation="edit_source",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "replacements": [
                    {"old": "fill_holes_max_edges=3", "new": "fill_holes_max_edges=2"}
                ],
            },
        )
        failed_prepared = prepare_candidate(failed_capture)
        failed_execution = execute_candidate(failed_prepared, cancellation_check=None)
        assert failed_execution["ok"] is False
        assert failed_execution["observed"]["details"]["stage"] == (
            "diagnostic_requirements"
        )
        assert failed_execution["domain_failure_stage"] == "diagnostic_requirements"
        assert failed_execution["retry"]["required_changes"] == [
            failed_execution["observed"]["details"]["correction"]
        ]
        assert (
            "preserve the requirement"
            in failed_execution["retry"]["required_changes"][0]
        )
        retain_candidate(failed_prepared, status="failed", failure=failed_execution)
        assert document.getObject(stable_name) is obj
        assert str(obj.VibeCADVibeScriptRevision) == accepted["accepted_revision"]

        edit_capture = _captured(
            root,
            document,
            operation="edit_source",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": failed_prepared["revision"],
                "replacements": [
                    {"old": "fill_holes_max_edges=2", "new": "fill_holes_max_edges=3"},
                    {"old": "label='Checked Mesh'", "new": "label='Edited Mesh'"},
                ],
            },
        )
        _, _, _, edit_publication, accepted = _run_candidate(edit_capture, service)
        assert edit_publication["created_objects"] == []
        assert _output(document, accepted) is obj
        assert consumer.Source is obj
        assert str(obj.Label) == "Edited Mesh"
        assert obj.HumanMeshNote == "preserve this human value"
        assert [obj.Placement.Base.x, obj.Placement.Base.y, obj.Placement.Base.z] == [
            11.0,
            12.0,
            13.0,
        ]

        input_capture = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "patch": {"height": 2.0, "offset": 5.0},
            },
        )
        _, _, input_validated, input_publication, accepted = _run_candidate(
            input_capture,
            service,
        )
        assert input_publication["created_objects"] == []
        assert _output(document, accepted) is obj
        assert consumer.Source is obj
        _assert_native_output(obj, input_validated["outputs"][0]["facts"])

        reconfigured_source = (
            _source()
            .replace(
                "[inputs['offset'],2,3]",
                "inputs['translation']",
            )
            .replace("label='Checked Mesh'", "label='Reconfigured Mesh'")
        )
        reconfigure_capture = _captured(
            root,
            document,
            operation="reconfigure_program",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "source": reconfigured_source,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "height": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "maximum": 1000,
                        },
                        "translation": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                        },
                    },
                    "required": ["height", "translation"],
                    "additionalProperties": False,
                },
                "inputs": {"height": 3.0, "translation": [8.0, 4.0, 2.0]},
                "expected_outputs": EXPECTED_OUTPUTS,
            },
        )
        (
            reconfigured_prepared,
            _,
            reconfigured_validated,
        ) = _prepare_execute_validate(reconfigure_capture)
        retain_candidate(reconfigured_prepared, status="validated")
        before_publication_fault = _snapshot(obj)
        original_configure = publication_module._configure_mesh

        def fail_after_assignment(*args, **kwargs):
            original_configure(*args, **kwargs)
            raise RuntimeError("injected Mesh publication failure")

        publication_module._configure_mesh = fail_after_assignment
        try:
            _expect_error(
                "injected Mesh publication failure",
                lambda: publish_candidate(
                    service,
                    reconfigured_prepared,
                    reconfigured_validated,
                ),
            )
        finally:
            publication_module._configure_mesh = original_configure
        obj = document.getObject(stable_name)
        assert obj is not None
        _assert_snapshot(obj, before_publication_fault)
        assert consumer.Source is obj

        reconfigured_publication = publish_candidate(
            service,
            reconfigured_prepared,
            reconfigured_validated,
        )
        accepted = accept_candidate(reconfigured_prepared, reconfigured_publication)
        obj = _output(document, accepted)
        assert str(obj.Name) == stable_name
        assert consumer.Source is obj
        assert str(obj.Label) == "Reconfigured Mesh"
        assert obj.HumanMeshNote == "preserve this human value"
        _assert_native_output(obj, reconfigured_validated["outputs"][0]["facts"])

        import Mesh

        human_source = document.addObject("Mesh::Feature", "HumanMeshSource")
        human_source.Label = "Human Open Mesh"
        human_source.Mesh = Mesh.Mesh(_tetrahedron()[:3])
        human_source.Placement = App.Placement(
            App.Vector(25, 30, 40),
            App.Rotation(App.Vector(0, 0, 1), 90),
        )
        human_source_name = str(human_source.Name)
        human_source_before = {
            "facets": int(human_source.Mesh.CountFacets),
            "placement": [
                float(human_source.Placement.Base.x),
                float(human_source.Placement.Base.y),
                float(human_source.Placement.Base.z),
                *[float(value) for value in human_source.Placement.Rotation.Q],
            ],
        }
        reference_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments=_reference_arguments(document, human_source),
        )
        (
            reference_prepared,
            reference_execution,
            reference_validated,
            _,
            reference_accepted,
        ) = _run_candidate(reference_capture, service)
        reference_obj = _output(document, reference_accepted)
        reference_stable_name = str(reference_obj.Name)
        reference_trace = reference_execution["outputs"][0]["mesh_data"][
            "operation_trace"
        ]
        assert [item["operation"] for item in reference_trace] == [
            "from_object",
            "repair",
            "diagnostics",
        ]
        assert reference_trace[0]["source"]["object_name"] == human_source_name
        assert reference_trace[0]["source"]["artifact_kind"] == "mesh_bms"
        source_matrix = reference_trace[0]["source"]["source_placement_matrix"]
        assert [source_matrix[3], source_matrix[7], source_matrix[11]] == [
            25.0,
            30.0,
            40.0,
        ]
        assert reference_trace[0]["source_diagnostics"]["open_edges"] == 3
        assert reference_validated["outputs"][0]["facts"]["is_solid"] is True
        assert reference_validated["outputs"][0]["facts"]["open_edges"] == 0
        reference_bounds = reference_validated["outputs"][0]["facts"]["bounds"]
        assert all(
            math.isclose(value, expected, rel_tol=1.0e-9, abs_tol=1.0e-9)
            for value, expected in zip(
                reference_bounds["minimum"],
                [24.0, 30.0, 40.0],
            )
        ), reference_bounds
        assert all(
            math.isclose(value, expected, rel_tol=1.0e-9, abs_tol=1.0e-9)
            for value, expected in zip(
                reference_bounds["maximum"],
                [25.0, 31.0, 41.0],
            )
        ), reference_bounds
        assert int(human_source.Mesh.CountFacets) == human_source_before["facets"]
        assert [
            float(human_source.Placement.Base.x),
            float(human_source.Placement.Base.y),
            float(human_source.Placement.Base.z),
            *[float(value) for value in human_source.Placement.Rotation.Q],
        ] == human_source_before["placement"]

        raw_context = _mesh_document_snapshot(document)
        assert raw_context["object_count"] == 3
        raw_by_name = {item["name"]: item for item in raw_context["objects"]}
        assert raw_by_name[stable_name]["native_summary"]["facets"] == 4
        assert raw_by_name[human_source_name]["native_summary"]["facets"] == 3
        context = complete_domain_context(domain_context_snapshot(service, "mesh"))
        context_by_name = {
            item["name"]: item for item in context["document_meshes"]["objects"]
        }
        document_mesh = context_by_name[stable_name]
        assert document_mesh["name"] == stable_name
        assert document_mesh["eligible_for_from_object"] is True
        assert document_mesh["accepted_validation"]["schema"] == VALIDATION_SCHEMA
        assert document_mesh["accepted_validation"]["operation_trace"] == [
            "mesh",
            "repair",
            "transform",
            "diagnostics",
        ]
        assert context_by_name[human_source_name]["reference"] == {
            "document_uid": str(document.Uid),
            "object_name": human_source_name,
        }
        assert context_by_name[human_source_name]["eligible_for_from_object"] is True
        assert context_by_name[reference_stable_name]["accepted_validation"][
            "operation_trace"
        ] == ["from_object", "repair", "diagnostics"]

        save_path = root / "mesh-production.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        reopened = App.openDocument(str(save_path))
        assert reopened is not None
        App.setActiveDocument(reopened.Name)
        reopened.recompute()
        obj = reopened.getObject(stable_name)
        consumer = reopened.getObject("HumanMeshConsumer")
        human_source = reopened.getObject(human_source_name)
        reference_obj = reopened.getObject(reference_stable_name)
        assert obj is not None and consumer is not None
        assert human_source is not None and reference_obj is not None
        assert consumer.Source is obj
        assert obj.HumanMeshNote == "preserve this human value"
        _assert_native_output(obj, reconfigured_validated["outputs"][0]["facts"])
        assert int(human_source.Mesh.CountFacets) == human_source_before["facets"]
        _assert_native_output(
            reference_obj,
            reference_validated["outputs"][0]["facts"],
        )

        reference_delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": reference_prepared["program_id"],
                "expected_revision": reference_prepared["revision"],
                "reason": "existing Mesh::Feature acquisition lifecycle complete",
            },
        )
        reference_delete = prepare_delete(reference_delete_capture)
        reference_finished = finish_delete(
            reference_delete,
            delete_live_program(service, reference_delete),
        )
        assert reference_finished["ok"] is True
        assert reopened.getObject(reference_stable_name) is None
        assert reopened.getObject(human_source_name) is human_source

        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": reconfigured_prepared["program_id"],
                "expected_revision": reconfigured_prepared["revision"],
                "reason": "verify Mesh external-reference guard",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        _expect_error(
            "reference",
            lambda: delete_live_program(service, prepared_delete),
        )
        restore_prepared_delete(prepared_delete)
        consumer.Source = None
        reopened.removeObject(consumer.Name)

        before_delete_fault = _snapshot(obj)
        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": reconfigured_prepared["program_id"],
                "expected_revision": reconfigured_prepared["revision"],
                "reason": "exercise explicit Mesh deletion rollback",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        original_remove = publication_module._remove_owned_objects

        def fail_after_committed_removal(active_document, managed_objects):
            first = next(iter(managed_objects))
            active_document.removeObject(first.Name)
            active_document.commitTransaction()
            raise RuntimeError("injected Mesh deletion failure")

        publication_module._remove_owned_objects = fail_after_committed_removal
        try:
            _expect_error(
                "injected Mesh deletion failure",
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
                "program_id": reconfigured_prepared["program_id"],
                "expected_revision": reconfigured_prepared["revision"],
                "reason": "Mesh production integration complete",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        finished = finish_delete(
            prepared_delete,
            delete_live_program(service, prepared_delete),
        )
        assert finished["ok"] is True
        assert not _managed_names(reopened, reconfigured_prepared["program_id"])
        App.closeDocument(reopened.Name)
        print(
            json.dumps(
                {
                    "ok": True,
                    "integration": "mesh_vibescript_api",
                    "stable_output": stable_name,
                    "native_facets": 4,
                    "existing_object_acquisition": True,
                    "placement_baked_snapshot": True,
                    "source_object_unchanged": True,
                    "exact_worker_host_diagnostics": True,
                    "explicit_publication_rollback": True,
                    "explicit_deletion_rollback": True,
                    "failed_candidate_retention": True,
                    "save_reopen": True,
                    "external_reference_guard": True,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        if App.ActiveDocument is not None:
            App.closeDocument(App.ActiveDocument.Name)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
