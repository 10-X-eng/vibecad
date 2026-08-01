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


EXPORTS = (
    "mesh",
    "from_object",
    "transform",
    "union",
    "difference",
    "intersection",
    "repair",
    "diagnostics",
    "mesh_from_shape",
    "shape_from_mesh",
)
OUTPUT_TYPES = ("mesh", "solid", "shell", "face", "wire", "compound")
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
    return MeshDomainAPI(EXPORTS, OUTPUT_TYPES)


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


def _box_triangles(
    origin: tuple[float, float, float],
    size: tuple[float, float, float],
    *,
    inward: bool = False,
) -> list[list[list[float]]]:
    x0, y0, z0 = origin
    x1, y1, z1 = (
        x0 + size[0],
        y0 + size[1],
        z0 + size[2],
    )
    points = [
        [x0, y0, z0],
        [x1, y0, z0],
        [x1, y1, z0],
        [x0, y1, z0],
        [x0, y0, z1],
        [x1, y0, z1],
        [x1, y1, z1],
        [x0, y1, z1],
    ]
    indices = [
        (0, 2, 1),
        (0, 3, 2),
        (4, 5, 6),
        (4, 6, 7),
        (0, 1, 5),
        (0, 5, 4),
        (3, 7, 6),
        (3, 6, 2),
        (0, 4, 7),
        (0, 7, 3),
        (1, 2, 6),
        (1, 6, 5),
    ]
    if inward:
        indices = [(first, third, second) for first, second, third in indices]
    return [[points[index] for index in triangle] for triangle in indices]


def _boolean_source(operation: str, *, label: str | None = None) -> str:
    first = json.dumps(
        _box_triangles((0.0, 0.0, 0.0), (10.0, 10.0, 10.0)),
        separators=(",", ":"),
    )
    second = json.dumps(
        _box_triangles((0.0, 2.0, 2.0), (6.0, 6.0, 6.0)),
        separators=(",", ":"),
    )
    output_label = label or f"Native {operation.title()}"
    return (
        f"first = api.mesh({first}, label='First Solid')\n"
        f"second_local = api.mesh({second}, label='Second Local Solid')\n"
        "second = api.transform(second_local, "
        "translation=[inputs['offset'],0,0], label='Positioned Second Solid')\n"
        f"combined = api.{operation}(first, second, linear_deflection=0.05, "
        "angular_deflection_degrees=20, relative=False, "
        f"label={output_label!r})\n"
        "result = {'Mesh': combined}\n"
    )


def _hollow_union_source(operation: str) -> str:
    hollow = [
        *_box_triangles((0.0, 0.0, 0.0), (10.0, 10.0, 10.0)),
        *_box_triangles(
            (3.0, 3.0, 3.0),
            (4.0, 4.0, 4.0),
            inward=True,
        ),
    ]
    void_solid = _box_triangles((4.0, 4.0, 4.0), (2.0, 2.0, 2.0))
    return (
        f"hollow = api.mesh({json.dumps(hollow, separators=(',', ':'))}, "
        "label='Hollow Solid')\n"
        f"inside_void = api.mesh({json.dumps(void_solid, separators=(',', ':'))}, "
        "label='Solid Inside Void')\n"
        f"combined = api.{operation}(hollow, inside_void, label='Cavity Check')\n"
        "result = {'Mesh': combined}\n"
    )


def _identical_operand_source(operation: str) -> str:
    solid = json.dumps(
        _box_triangles((0.0, 0.0, 0.0), (10.0, 10.0, 10.0)),
        separators=(",", ":"),
    )
    return (
        f"solid = api.mesh({solid}, label='Shared Solid')\n"
        f"combined = api.{operation}(solid, solid, label='Identical "
        f"{operation.title()}')\n"
        "result = {'Mesh': combined}\n"
    )


def _inverted_orientation_source() -> str:
    inverted = json.dumps(
        _box_triangles(
            (0.0, 0.0, 0.0),
            (10.0, 10.0, 10.0),
            inward=True,
        ),
        separators=(",", ":"),
    )
    normal = json.dumps(
        _box_triangles((5.0, 0.0, 0.0), (10.0, 10.0, 10.0)),
        separators=(",", ":"),
    )
    return (
        f"inverted = api.mesh({inverted}, label='Uniformly Inverted Solid')\n"
        f"normal = api.mesh({normal}, label='Normal Solid')\n"
        "combined = api.union(inverted, normal, label='Orientation Corrected Union')\n"
        "result = {'Mesh': combined}\n"
    )


def _boolean_arguments(operation: str, *, source: str | None = None) -> dict:
    return {
        "program_name": f"Native Mesh {operation.title()} Lifecycle",
        "source": source or _boolean_source(operation),
        "input_schema": {
            "type": "object",
            "properties": {
                "offset": {
                    "type": "number",
                    "minimum": -1000,
                    "maximum": 1000,
                }
            },
            "required": ["offset"],
            "additionalProperties": False,
        },
        "inputs": {"offset": 5.0},
        "expected_outputs": EXPECTED_OUTPUTS,
    }


def _hollow_arguments(operation: str) -> dict:
    return {
        "program_name": f"Hollow Mesh {operation.title()} Cavity",
        "source": _hollow_union_source(operation),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "inputs": {},
        "expected_outputs": EXPECTED_OUTPUTS,
    }


def _literal_boolean_arguments(program_name: str, source: str) -> dict:
    return {
        "program_name": program_name,
        "source": source,
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "inputs": {},
        "expected_outputs": EXPECTED_OUTPUTS,
    }


def _exercise_source_api() -> None:
    import Mesh
    from vibescript_meshpart_worker import validate_meshpart_definition

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
    reference = {"document_uid": "document", "object_name": "ExistingShape"}
    converted_mesh = api.mesh_from_shape(reference, label="Converted Mesh")
    converted_shape = api.shape_from_mesh(
        {"document_uid": "document", "object_name": "ExistingMesh"},
        output_type="solid",
        label="Converted Solid",
    )
    for converted in (converted_mesh, converted_shape):
        assert converted.domain == "mesh"
        assert validate_meshpart_definition(
            converted,
            definition_domain="mesh",
        ) == converted.to_payload()
    _expect_error(
        "publish that conversion and reference its stable Mesh::Feature",
        lambda: api.transform(converted_mesh),
    )
    moved = api.transform(
        raw,
        translation=[1, 2, 3],
        rotation=[0, 0, 0, 2],
        scale=[2, 3, 4],
    )
    for operation in ("union", "difference", "intersection"):
        boolean = getattr(api, operation)(
            raw,
            moved,
            linear_deflection=0.05,
            angular_deflection_degrees=20,
            relative=True,
            label=operation.title(),
        )
        assert validate_mesh_definition(
            boolean,
            require_domain_value=True,
        ) == boolean.to_payload()
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
    for operation in ("union", "difference", "intersection"):
        identical = getattr(api, operation)(raw, raw)
        assert validate_mesh_definition(
            identical,
            require_domain_value=True,
        ) == identical.to_payload()
    _expect_error(
        "linear_deflection must be greater than 0",
        lambda: api.difference(raw, moved, linear_deflection=0),
    )
    _expect_error(
        "angular_deflection_degrees must be at most 180",
        lambda: api.intersection(
            raw,
            moved,
            angular_deflection_degrees=181,
        ),
    )
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


def _assert_volume(facts: dict, expected: float) -> None:
    assert math.isclose(
        abs(float(facts["volume_mm3"])),
        expected,
        rel_tol=1.0e-9,
        abs_tol=1.0e-7,
    ), facts


def _exercise_boolean_lifecycle(root: Path) -> None:
    document = App.newDocument("VibeScriptMeshBooleanNative")
    service = _Service(root)
    tracked: list[dict[str, object]] = []
    try:
        App.setActiveDocument(document.Name)
        expected_initial = {
            "union": 1036.0,
            "difference": 820.0,
            "intersection": 180.0,
        }
        expected_updated = {
            "union": 1216.0,
            "difference": 1000.0,
            "intersection": 216.0,
        }
        updated_offsets = {
            "union": 20.0,
            "difference": 20.0,
            "intersection": 2.0,
        }
        for operation in ("union", "difference", "intersection"):
            create_capture = _captured(
                root,
                document,
                operation="create_program",
                arguments=_boolean_arguments(operation),
            )
            prepared, execution, validated = _prepare_execute_validate(
                create_capture,
                service,
            )
            facts = validated["outputs"][0]["facts"]
            _assert_volume(facts, expected_initial[operation])
            trace = validated["outputs"][0]["mesh_data"]["operation_trace"]
            assert [item["operation"] for item in trace] == [
                "mesh",
                "mesh",
                "transform",
                operation,
            ]
            boolean_trace = trace[-1]
            assert boolean_trace["backend"] == "MeshPart::Boolean/OpenCASCADE"
            assert boolean_trace["tessellation"] == {
                "linear_deflection": 0.05,
                "angular_deflection_degrees": 20.0,
                "relative": False,
            }
            assert boolean_trace["first"]["is_solid"] is True
            assert boolean_trace["second"]["is_solid"] is True
            assert boolean_trace["result"]["is_solid"] is True

            forged = copy.deepcopy(execution)
            forged["outputs"][0]["mesh_data"]["operation_trace"][-1][
                "backend"
            ] = "OpenSCAD"
            _expect_error(
                "changed its OCC backend",
                lambda: validate_candidate(prepared, forged),
            )

            retain_candidate(prepared, status="validated")
            publication = publish_candidate(service, prepared, validated)
            accepted = accept_candidate(prepared, publication)
            obj = _output(document, accepted)
            stable_name = str(obj.Name)
            if operation == "union":
                edit_capture = _captured(
                    root,
                    document,
                    operation="edit_source",
                    arguments={
                        "program_id": prepared["program_id"],
                        "expected_revision": accepted["working_revision"],
                        "source": _boolean_source(
                            operation,
                            label="Edited Native Union",
                        ),
                    },
                )
                (
                    _,
                    _,
                    _,
                    edit_publication,
                    accepted,
                ) = _run_candidate(edit_capture, service)
                assert edit_publication["created_objects"] == []
                assert _output(document, accepted) is obj
                assert str(obj.Label) == "Edited Native Union"

            input_capture = _captured(
                root,
                document,
                operation="set_inputs",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": accepted["working_revision"],
                    "patch": {"offset": updated_offsets[operation]},
                },
            )
            (
                input_prepared,
                _,
                input_validated,
                input_publication,
                accepted,
            ) = _run_candidate(input_capture, service)
            assert input_publication["created_objects"] == []
            assert _output(document, accepted) is obj
            _assert_volume(
                input_validated["outputs"][0]["facts"],
                expected_updated[operation],
            )
            if operation == "union":
                assert input_validated["outputs"][0]["facts"]["components"] >= 2
            if operation == "difference":
                # A disjoint subtraction is a valid identity result, not an error.
                assert (
                    input_validated["outputs"][0]["mesh_data"]["operation_trace"][-1][
                        "operation"
                    ]
                    == "difference"
                )
            tracked.append(
                {
                    "program_id": prepared["program_id"],
                    "expected_revision": input_prepared["revision"],
                    "object_name": stable_name,
                    "facts": input_validated["outputs"][0]["facts"],
                }
            )

        intersection = tracked[2]
        non_overlap_capture = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": intersection["program_id"],
                "expected_revision": intersection["expected_revision"],
                "patch": {"offset": 20.0},
            },
        )
        non_overlap_prepared = prepare_candidate(non_overlap_capture)
        non_overlap_execution = execute_candidate(
            non_overlap_prepared,
            cancellation_check=None,
        )
        assert non_overlap_execution["ok"] is False
        assert non_overlap_execution["observed"]["details"]["stage"] == (
            "boolean_non_overlap"
        )
        assert "positive solid overlap" in non_overlap_execution["observed"][
            "details"
        ]["correction"]
        retain_candidate(
            non_overlap_prepared,
            status="failed",
            failure=non_overlap_execution,
        )
        intersection["expected_revision"] = non_overlap_prepared["revision"]

        hollow_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments=_hollow_arguments("union"),
        )
        (
            hollow_prepared,
            _,
            hollow_validated,
            _,
            hollow_accepted,
        ) = _run_candidate(hollow_capture, service)
        hollow_obj = _output(document, hollow_accepted)
        _assert_volume(hollow_validated["outputs"][0]["facts"], 944.0)
        assert hollow_validated["outputs"][0]["facts"]["components"] >= 2
        hollow_intersection_capture = _captured(
            root,
            document,
            operation="edit_source",
            arguments={
                "program_id": hollow_prepared["program_id"],
                "expected_revision": hollow_accepted["working_revision"],
                "source": _hollow_union_source("intersection"),
            },
        )
        hollow_failed = prepare_candidate(hollow_intersection_capture)
        hollow_execution = execute_candidate(hollow_failed, cancellation_check=None)
        assert hollow_execution["ok"] is False
        assert hollow_execution["observed"]["details"]["stage"] == (
            "boolean_non_overlap"
        )
        retain_candidate(hollow_failed, status="failed", failure=hollow_execution)
        tracked.append(
            {
                "program_id": hollow_prepared["program_id"],
                "expected_revision": hollow_failed["revision"],
                "object_name": str(hollow_obj.Name),
                "facts": hollow_validated["outputs"][0]["facts"],
            }
        )

        for operation in ("union", "intersection"):
            identical_capture = _captured(
                root,
                document,
                operation="create_program",
                arguments=_literal_boolean_arguments(
                    f"Identical Operand {operation.title()}",
                    _identical_operand_source(operation),
                ),
            )
            (
                identical_prepared,
                _,
                identical_validated,
                _,
                identical_accepted,
            ) = _run_candidate(identical_capture, service)
            identical_obj = _output(document, identical_accepted)
            _assert_volume(identical_validated["outputs"][0]["facts"], 1000.0)
            tracked.append(
                {
                    "program_id": identical_prepared["program_id"],
                    "expected_revision": identical_prepared["revision"],
                    "object_name": str(identical_obj.Name),
                    "facts": identical_validated["outputs"][0]["facts"],
                }
            )

        identical_difference_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments=_literal_boolean_arguments(
                "Identical Operand Difference",
                _identical_operand_source("difference"),
            ),
        )
        identical_difference = prepare_candidate(identical_difference_capture)
        identical_difference_execution = execute_candidate(
            identical_difference,
            cancellation_check=None,
        )
        assert identical_difference_execution["ok"] is False
        assert identical_difference_execution["observed"]["details"]["stage"] == (
            "boolean_empty_result"
        )
        retain_candidate(
            identical_difference,
            status="failed",
            failure=identical_difference_execution,
        )
        tracked.append(
            {
                "program_id": identical_difference["program_id"],
                "expected_revision": identical_difference["revision"],
                "object_name": "",
                "facts": {},
            }
        )

        inverted_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments=_literal_boolean_arguments(
                "Uniformly Inverted Mesh Boolean",
                _inverted_orientation_source(),
            ),
        )
        (
            inverted_prepared,
            _,
            inverted_validated,
            _,
            inverted_accepted,
        ) = _run_candidate(inverted_capture, service)
        inverted_obj = _output(document, inverted_accepted)
        _assert_volume(inverted_validated["outputs"][0]["facts"], 1500.0)
        tracked.append(
            {
                "program_id": inverted_prepared["program_id"],
                "expected_revision": inverted_prepared["revision"],
                "object_name": str(inverted_obj.Name),
                "facts": inverted_validated["outputs"][0]["facts"],
            }
        )

        open_first = json.dumps(_tetrahedron()[:3], separators=(",", ":"))
        closed_second = json.dumps(
            _box_triangles((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
            separators=(",", ":"),
        )
        invalid_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments={
                "program_name": "Invalid Mesh Boolean Operand",
                "source": (
                    f"first = api.mesh({open_first}, label='Open Operand')\n"
                    f"second = api.mesh({closed_second}, label='Solid Operand')\n"
                    "result = {'Mesh': api.union(first, second, label='Invalid')}\n"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "inputs": {},
                "expected_outputs": EXPECTED_OUTPUTS,
            },
        )
        invalid_prepared = prepare_candidate(invalid_capture)
        invalid_execution = execute_candidate(
            invalid_prepared,
            cancellation_check=None,
        )
        assert invalid_execution["ok"] is False
        assert invalid_execution["observed"]["details"]["stage"] == "boolean_input"
        assert invalid_execution["observed"]["details"]["operand"] == "first"
        assert "closed manifold solid" in invalid_execution["observed"]["details"][
            "correction"
        ]
        retain_candidate(
            invalid_prepared,
            status="failed",
            failure=invalid_execution,
        )
        tracked.append(
            {
                "program_id": invalid_prepared["program_id"],
                "expected_revision": invalid_prepared["revision"],
                "object_name": "",
                "facts": {},
            }
        )

        save_path = root / "mesh-booleans.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        reopened = App.openDocument(str(save_path))
        assert reopened is not None
        App.setActiveDocument(reopened.Name)
        reopened.recompute()
        for item in tracked:
            object_name = str(item["object_name"])
            if not object_name:
                continue
            obj = reopened.getObject(object_name)
            assert obj is not None
            _assert_native_output(obj, item["facts"])

        for item in reversed(tracked):
            delete_capture = _captured(
                root,
                reopened,
                operation="delete_program",
                arguments={
                    "program_id": item["program_id"],
                    "expected_revision": item["expected_revision"],
                    "reason": "Mesh boolean integration complete",
                },
            )
            prepared_delete = prepare_delete(delete_capture)
            finished = finish_delete(
                prepared_delete,
                delete_live_program(service, prepared_delete),
            )
            assert finished["ok"] is True
        App.closeDocument(reopened.Name)
    finally:
        if App.ActiveDocument is not None:
            App.closeDocument(App.ActiveDocument.Name)


def _exercise_meshworkbench_conversions(root: Path) -> None:
    """Prove both existing MeshPart calls execute from the shipped Mesh domain."""

    import Mesh
    import Part

    document = App.newDocument("VibeScriptMeshConversions")
    service = _Service(root)
    try:
        App.setActiveDocument(document.Name)
        shape_source = document.addObject("Part::Feature", "ShapeSource")
        shape_source.Shape = Part.makeBox(10, 8, 6)
        mesh_source = document.addObject("Mesh::Feature", "MeshSource")
        mesh_source.Mesh = Mesh.Mesh(
            _box_triangles((0.0, 0.0, 0.0), (10.0, 8.0, 6.0))
        )
        document.recompute()
        context = complete_domain_context(domain_context_snapshot(service, "mesh"))
        shape_context = {
            item["name"]: item
            for item in context["document_shape_sources"]["objects"]
        }
        mesh_context = {
            item["name"]: item for item in context["document_meshes"]["objects"]
        }
        assert shape_context[shape_source.Name]["eligible_for_mesh_from_shape"] is True
        assert mesh_context[mesh_source.Name]["eligible_for_shape_from_mesh"] is True
        reference_schema = {
            "type": "object",
            "x-vibecad-reference": True,
            "properties": {
                "document_uid": {"type": "string", "minLength": 1},
                "object_name": {"type": "string", "minLength": 1},
            },
            "required": ["document_uid", "object_name"],
            "additionalProperties": False,
        }
        arguments = {
            "program_name": "Mesh Workbench Conversions",
            "source": (
                "native = api.mesh([[[0,0,0],[1,0,0],[0,1,0]],"
                "[[0,0,0],[0,0,1],[1,0,0]],"
                "[[0,0,0],[0,1,0],[0,0,1]],"
                "[[1,0,0],[0,0,1],[0,1,0]]], label='Native Mesh')\n"
                "meshed = api.mesh_from_shape(inputs['shape'], method='standard', "
                "linear_deflection=0.25, angular_deflection_degrees=15, "
                "preserve_face_groups=True, label='Converted Mesh')\n"
                "solid = api.shape_from_mesh(inputs['mesh'], output_type='solid', "
                "tolerance=0.01, harmonize_normals=True, refine=True, "
                "label='Recovered Solid')\n"
                "result = {'Native': native, 'Meshed': meshed, 'Solid': solid}\n"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "shape": reference_schema,
                    "mesh": reference_schema,
                },
                "required": ["shape", "mesh"],
                "additionalProperties": False,
            },
            "inputs": {
                "shape": {
                    "document_uid": str(document.Uid),
                    "object_name": str(shape_source.Name),
                },
                "mesh": {
                    "document_uid": str(document.Uid),
                    "object_name": str(mesh_source.Name),
                },
            },
            "expected_outputs": [
                {"name": "Native", "type": "mesh"},
                {"name": "Meshed", "type": "mesh"},
                {"name": "Solid", "type": "solid"},
            ],
        }
        prepared, execution, validated, publication, accepted = _run_candidate(
            _captured(
                root,
                document,
                operation="create_program",
                arguments=arguments,
            ),
            service,
        )
        assert execution["mesh_validation"]["output_count"] == 1
        assert execution["meshpart_validation"]["output_count"] == 2
        assert validated["meshpart_validation"]["mesh_output_count"] == 1
        assert validated["meshpart_validation"]["shape_output_count"] == 1
        assert [item["type"] for item in validated["outputs"]] == [
            "mesh",
            "mesh",
            "solid",
        ]
        assert all(
            item["definition"]["domain"] == "mesh"
            for item in validated["outputs"]
        )
        assert publication["ok"] is True
        live = accepted["live_outputs"]
        mesh_output = document.getObject(live["Meshed"]["object_name"])
        solid_output = document.getObject(live["Solid"]["object_name"])
        assert mesh_output is not None and mesh_output.TypeId == "Mesh::Feature"
        assert int(mesh_output.Mesh.CountFacets) > 0
        assert solid_output is not None and solid_output.TypeId == "Part::Feature"
        assert not solid_output.Shape.isNull() and solid_output.Shape.isValid()
        assert solid_output.Shape.ShapeType == "Solid"

        prepared_delete = prepare_delete(
            _captured(
                root,
                document,
                operation="delete_program",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": prepared["revision"],
                    "reason": "Mesh workbench conversion integration complete",
                },
            )
        )
        finish_delete(
            prepared_delete,
            delete_live_program(service, prepared_delete),
        )
    finally:
        if App.ActiveDocument is not None:
            App.closeDocument(App.ActiveDocument.Name)


def main() -> int:
    _exercise_source_api()
    root = Path(tempfile.mkdtemp(prefix="vibecad-mesh-native-"))
    _exercise_boolean_lifecycle(root)
    _exercise_meshworkbench_conversions(root)
    document = App.newDocument("VibeScriptMeshNative")
    service = _Service(root)
    try:
        App.setActiveDocument(document.Name)
        surface = resolve_modeling_surface("MeshWorkbench", "vibescript")
        assert surface.available is True, surface.unavailable_reason
        assert surface.cad_tool_names == (
            "vibescript.read_source",
            "vibescript.read_api",
            "vibescript.build_program",
            "vibescript.edit_source",
            "vibescript.mesh.create_program",
            "vibescript.mesh.set_inputs",
            "vibescript.mesh.reconfigure_program",
            "vibescript.mesh.delete_program",
            "mesh.list_meshes",
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
                "source": _source().replace(
                    "fill_holes_max_edges=3",
                    "fill_holes_max_edges=2",
                ),
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
                "source": _source().replace(
                    "label='Checked Mesh'",
                    "label='Edited Mesh'",
                ),
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
        assert document_mesh["eligible_for_shape_from_mesh"] is True
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
        assert context_by_name[human_source_name]["eligible_for_shape_from_mesh"] is True
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
        original_remove = publication_module._remove_timeline_deletion

        def fail_after_committed_removal(active_document, deletion):
            original_remove(active_document, deletion)
            active_document.commitTransaction()
            raise RuntimeError("injected Mesh deletion failure")

        publication_module._remove_timeline_deletion = fail_after_committed_removal
        try:
            _expect_error(
                "injected Mesh deletion failure",
                lambda: delete_live_program(service, prepared_delete),
            )
            restore_prepared_delete(prepared_delete)
        finally:
            publication_module._remove_timeline_deletion = original_remove
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
