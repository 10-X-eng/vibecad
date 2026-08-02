# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native production gate for the canonical MeshPart VibeScript domain."""

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
import MeshPart  # noqa: E402
import Part  # noqa: E402

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
import VibeCADVibeScriptDomainPublication as publication_module  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    PROP_INPUT_OBJECTS,
    delete_live_program,
    mark_programs_stale_from_source,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    MeshPartDomainAdapter,
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
    complete_domain_context,
    domain_context_snapshot,
    get_vibescript_pack,
    validate_program_source,
)
from vibescript_mesh_worker import mesh_diagnostics  # noqa: E402
from vibescript_meshpart_api import MeshPartDomainAPI  # noqa: E402
from vibescript_meshpart_worker import (  # noqa: E402
    MeshPartCandidateError,
    VALIDATION_SCHEMA,
    validate_meshpart_definition,
)
from vibescript_part_worker import part_shape_facts  # noqa: E402


EXPORTS = ("mesh_from_shape", "shape_from_mesh")
OUTPUT_TYPES = ("mesh", "solid", "shell", "face", "wire", "compound")
EXPECTED_OUTPUTS = [
    {"name": "Mesh", "type": "mesh"},
    {"name": "Solid", "type": "solid"},
    {"name": "Boundary", "type": "wire"},
]


class _Service:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _active_document():
        return App.ActiveDocument

    @staticmethod
    def active_workbench_name() -> str:
        return "MeshPartWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "meshpart-native-fixture-revision"

    def project_scope_snapshot(self) -> dict[str, str]:
        return {"root": str(self.root), "project_id": "meshpart-native-fixture"}

    @staticmethod
    def provider_working_set() -> dict[str, object]:
        return {"target_count": 0, "targets": []}

    @staticmethod
    def selection_summary() -> dict[str, object]:
        return {"selection": []}


def _api() -> MeshPartDomainAPI:
    return MeshPartDomainAPI(EXPORTS, OUTPUT_TYPES)


def _expect_error(fragment: str, call) -> None:
    try:
        call()
    except (TypeError, ValueError, RuntimeError, MeshPartCandidateError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f"Expected MeshPart failure containing {fragment!r}.")


def _exercise_source_api() -> None:
    api = _api()
    reference = {"document_uid": "document", "object_name": "Source"}
    assert api.exported_names == EXPORTS
    assert not hasattr(api, "output")
    for name in EXPORTS:
        member = getattr(api, name)
        signature = str(inspect.signature(member))
        assert "*args" not in signature and "**" not in signature
        assert inspect.getdoc(member)

    standard = api.mesh_from_shape(
        reference,
        subelements=["Face3", "Face1"],
        preserve_face_groups=True,
        label="Grouped mesh",
    )
    assert standard.properties["subelements"] == ("Face1", "Face3")
    assert standard.properties["linear_deflection"] == 0.1
    assert standard.properties["fineness"] is None
    assert validate_meshpart_definition(standard) == standard.to_payload()

    method_cases = (
        api.mesh_from_shape(reference, method="max_length", max_length=2),
        api.mesh_from_shape(reference, method="max_area", max_area=2),
        api.mesh_from_shape(reference, method="local_length", local_length=2),
        api.mesh_from_shape(reference, method="deflection", deflection=0.2),
        api.mesh_from_shape(
            reference,
            method="min_max_length",
            min_length=0.1,
            max_length=2,
        ),
        api.mesh_from_shape(reference, method="netgen_fineness", fineness="fine"),
        api.mesh_from_shape(
            reference,
            method="netgen_custom",
            growth_rate=0.4,
            segments_per_edge=1.5,
            segments_per_radius=2.5,
        ),
    )
    assert all(validate_meshpart_definition(value) for value in method_cases)
    assert method_cases[-1].properties["linear_deflection"] is None
    assert method_cases[-1].properties["growth_rate"] == 0.4

    boundary = api.shape_from_mesh(
        reference,
        output_type="wire",
        facet_indices=[3, 1, 2],
        label="Boundary",
    )
    assert boundary.properties["representation"] == "boundary"
    assert boundary.properties["facet_indices"] == (1, 2, 3)
    assert boundary.properties["tolerance"] is None
    assert validate_meshpart_definition(boundary) == boundary.to_payload()
    solid = api.shape_from_mesh(reference, output_type="solid")
    assert solid.properties["representation"] == "surface"
    assert solid.properties["require_closed"] is True
    assert solid.properties["tolerance"] == 0.01

    try:
        standard.properties["method"] = "max_area"
    except TypeError:
        pass
    else:
        raise AssertionError("MeshPart graph values must be deeply immutable.")

    try:
        api.mesh_from_shape({"document_uid": 1, "object_name": "Source"})
    except ValueError as exc:
        assert exc.details["stage"] == "source_validation"
        assert exc.details["operation"] == "mesh_from_shape"
        assert exc.details["parameter"] == "source.document_uid"
        assert "Change only the failing source expression" in exc.details["correction"]
    else:
        raise AssertionError("Expected one structured MeshPart source failure.")

    cases = (
        (
            "source.document_uid",
            lambda: api.mesh_from_shape({"document_uid": 1, "object_name": "Source"}),
        ),
        (
            "one topology class",
            lambda: api.mesh_from_shape(reference, subelements=["Face1", "Shell1"]),
        ),
        (
            "max_area is required",
            lambda: api.mesh_from_shape(reference, method="max_area"),
        ),
        (
            "relative is not used",
            lambda: api.mesh_from_shape(
                reference, method="max_area", max_area=1, relative=True
            ),
        ),
        (
            "fineness is not used",
            lambda: api.mesh_from_shape(reference, fineness="fine"),
        ),
        (
            "less than or equal",
            lambda: api.mesh_from_shape(
                reference,
                method="netgen_custom",
                min_length=2,
                max_length=1,
            ),
        ),
        (
            "cannot both be true",
            lambda: api.mesh_from_shape(
                reference,
                method="netgen_fineness",
                second_order=True,
                allow_quad=True,
            ),
        ),
        (
            "mutually exclusive",
            lambda: api.shape_from_mesh(
                reference,
                facet_indices=[1],
                segment_index=1,
            ),
        ),
        (
            "duplicate index",
            lambda: api.shape_from_mesh(reference, facet_indices=[1, 1]),
        ),
        (
            "must be omitted",
            lambda: api.shape_from_mesh(
                reference,
                output_type="wire",
                tolerance=0.1,
            ),
        ),
        (
            "must be one of",
            lambda: api.shape_from_mesh(
                reference,
                output_type="solid",
                representation="boundary",
            ),
        ),
    )
    for fragment, call in cases:
        _expect_error(fragment, call)

    pack = get_vibescript_pack("MeshPartWorkbench")
    assert pack is not None
    description = MeshPartDomainAdapter(pack).describe_api()
    assert description["api_contract"] == "vibecad-vibescript-meshpart-api-v1"
    assert [item["name"] for item in description["runtime_exports"]] == list(EXPORTS)
    assert "consolidated" in description["redundancy_contract"]
    assert set(description["operation_selection"]) == set(EXPORTS)
    assert (
        "Do not generate several mesher variants"
        in description["canonical_operations"]["mesh_from_shape"]["method_rule"]
    )
    assert (
        "Do not pass mesh_from_shape directly"
        in description["composition_contract"]["independent_sources"]
    )
    assert (
        "next_write_expected_revision"
        in description["model_verification_contract"]["failure_repair"]
    )
    assert (
        len(
            json.dumps(description, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        < 32_000
    )
    assert description["native_safety_contract"]["no_synchronous_fallback"] is True
    for pattern in description["recommended_patterns"]:
        validate_program_source(pattern["source"])


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


def _source() -> str:
    return (
        "converted = api.mesh_from_shape(inputs['shape'], method='standard', "
        "linear_deflection=inputs['deflection'], angular_deflection_degrees=15, "
        "preserve_face_groups=True, label='Converted Mesh')\n"
        "solid = api.shape_from_mesh(inputs['mesh'], output_type='solid', "
        "tolerance=inputs['tolerance'], harmonize_normals=True, refine=True, "
        "label='Recovered Solid')\n"
        "boundary = api.shape_from_mesh(inputs['mesh'], output_type='wire', "
        "segment_index=inputs['segment'], label='Face Boundary')\n"
        "result = {'Mesh': converted, 'Solid': solid, 'Boundary': boundary}\n"
    )


def _input_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "shape": _reference_schema(),
            "mesh": _reference_schema(),
            "deflection": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 1000,
            },
            "tolerance": {
                "type": "number",
                "minimum": 1.0e-9,
                "maximum": 1000,
            },
            "segment": {"type": "integer", "minimum": 1, "maximum": 1000000},
        },
        "required": ["shape", "mesh", "deflection", "tolerance", "segment"],
        "additionalProperties": False,
    }


def _create_arguments(document, shape_source, mesh_source) -> dict[str, object]:
    return {
        "program_name": "Native MeshPart Lifecycle",
        "source": _source(),
        "input_schema": _input_schema(),
        "inputs": {
            "shape": {
                "document_uid": str(document.Uid),
                "object_name": str(shape_source.Name),
            },
            "mesh": {
                "document_uid": str(document.Uid),
                "object_name": str(mesh_source.Name),
            },
            "deflection": 0.25,
            "tolerance": 0.01,
            "segment": 1,
        },
        "expected_outputs": EXPECTED_OUTPUTS,
    }


def _captured(
    root: Path,
    document,
    *,
    operation: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    pack = get_vibescript_pack("MeshPartWorkbench")
    assert pack is not None
    return {
        "tool_name": f"vibescript.meshpart.{operation}",
        "operation": operation,
        "arguments": arguments,
        "pack": pack,
        "project_root": str(root),
        "project_id": "meshpart-native-fixture",
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "meshpart-native-fixture-revision",
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
            "MeshPartWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 90.0,
        "memory_limit_bytes": 3 * 1024 * 1024 * 1024,
    }


def _prepare_execute_validate(captured: dict[str, object], service: _Service):
    prepared = prepare_candidate(captured)
    assert prepared["reference_requirements"]
    prepared = finalize_candidate(
        prepared,
        capture_reference_inputs(service, prepared),
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


def _managed_names(document, program_id: str) -> set[str]:
    return {
        str(obj.Name)
        for obj in document.Objects
        if str(getattr(obj, PROP_PROGRAM_ID, "") or "") == program_id
    }


def _outputs(document, accepted: dict[str, object]) -> dict[str, object]:
    result = {}
    for name, data in dict(accepted["live_outputs"]).items():
        obj = document.getObject(str(data["object_name"]))
        assert obj is not None
        result[str(name)] = obj
    return result


def _local_mesh_facts(obj) -> dict[str, object]:
    mesh = obj.Mesh.copy()
    mesh.Placement = App.Placement()
    return mesh_diagnostics(mesh)


def _local_shape_facts(obj) -> dict[str, object]:
    shape = obj.Shape.copy()
    shape.Placement = App.Placement()
    return part_shape_facts(shape, max_subelements=256)


def _placement(obj) -> list[float]:
    return [
        float(value)
        for value in (
            obj.Placement.Base.x,
            obj.Placement.Base.y,
            obj.Placement.Base.z,
            *obj.Placement.Rotation.Q,
        )
    ]


def _assert_values(observed, expected, path: str = "value") -> None:
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        assert observed == expected, (path, observed, expected)
    elif isinstance(expected, int):
        assert type(observed) is int and observed == expected, (
            path,
            observed,
            expected,
        )
    elif isinstance(expected, float):
        assert math.isclose(
            float(observed),
            expected,
            rel_tol=1.0e-9,
            abs_tol=1.0e-9,
        ), (path, observed, expected)
    elif isinstance(expected, list):
        assert isinstance(observed, list) and len(observed) == len(expected), (
            path,
            observed,
            expected,
        )
        for index, (left, right) in enumerate(zip(observed, expected)):
            _assert_values(left, right, f"{path}[{index}]")
    elif isinstance(expected, dict):
        assert isinstance(observed, dict) and set(observed) == set(expected), (
            path,
            set(observed),
            set(expected),
        )
        for key, value in expected.items():
            _assert_values(observed[key], value, f"{path}.{key}")
    else:
        assert observed == expected, (path, observed, expected)


def _snapshot(obj) -> dict[str, object]:
    common = {
        "name": str(obj.Name),
        "type_id": str(obj.TypeId),
        "label": str(obj.Label),
        "revision": str(obj.VibeCADVibeScriptRevision),
        "definition": str(obj.VibeCADVibeScriptDefinition),
        "validation": str(obj.VibeCADMeshPartValidation),
        "input_snapshots": str(obj.VibeCADVibeScriptInputSnapshots),
        "placement": _placement(obj),
        "human_note": str(getattr(obj, "HumanMeshPartNote", "") or ""),
        "human_length": float(getattr(obj, "HumanLength", 0.0) or 0.0),
        "expressions": [
            [str(path), str(expression)]
            for path, expression in list(obj.ExpressionEngine or [])
        ],
    }
    if str(obj.TypeId) == "Mesh::Feature":
        mesh = obj.Mesh.copy()
        common["facts"] = _local_mesh_facts(obj)
        common["segments"] = [
            [int(item) for item in list(mesh.getSegment(index) or [])]
            for index in range(int(mesh.countSegments()))
        ]
    else:
        common["facts"] = _local_shape_facts(obj)
    return common


def _assert_snapshot(obj, expected: dict[str, object]) -> None:
    observed = _snapshot(obj)
    expected_placement = list(expected["placement"])
    observed_placement = list(observed["placement"])
    assert all(
        math.isclose(left, right, rel_tol=1.0e-12, abs_tol=1.0e-12)
        for left, right in zip(observed_placement, expected_placement)
    )
    _assert_values(
        {k: v for k, v in observed.items() if k != "placement"},
        {k: v for k, v in expected.items() if k != "placement"},
        "snapshot",
    )


def _assert_native_outputs(
    outputs: dict[str, object],
    validated: dict[str, object],
) -> None:
    by_name = {item["name"]: item for item in validated["outputs"]}
    mesh = outputs["Mesh"]
    assert str(mesh.TypeId) == "Mesh::Feature"
    _assert_values(_local_mesh_facts(mesh), by_name["Mesh"]["facts"], "Mesh.facts")
    mesh_validation = json.loads(str(mesh.VibeCADMeshPartValidation))
    assert mesh_validation["schema"] == VALIDATION_SCHEMA
    assert (
        mesh_validation["mesher_backend"]
        == by_name["Mesh"]["meshpart_data"]["mesher_backend"]
    )
    assert int(mesh.Mesh.countSegments()) == len(
        by_name["Mesh"]["meshpart_data"]["segments"]
    )
    for name, shape_type in (("Solid", "Solid"), ("Boundary", "Wire")):
        obj = outputs[name]
        assert str(obj.TypeId) == "Part::Feature"
        facts = _local_shape_facts(obj)
        _assert_values(facts, by_name[name]["facts"], f"{name}.facts")
        assert facts["shape_type"] == shape_type
        validation = json.loads(str(obj.VibeCADMeshPartValidation))
        assert validation["schema"] == VALIDATION_SCHEMA
    assert _local_shape_facts(outputs["Solid"])["faces"] == 6
    assert len(outputs["Boundary"].Shape.Edges) == 4


def _add_human_state(outputs: dict[str, object]) -> None:
    for index, (name, obj) in enumerate(outputs.items(), start=1):
        obj.addProperty(
            "App::PropertyString",
            "HumanMeshPartNote",
            "Human",
            "Human-authored state that MeshPart must preserve.",
        )
        obj.HumanMeshPartNote = f"preserve {name}"
        obj.addProperty(
            "App::PropertyLength",
            "HumanLength",
            "Human",
            "Human-authored expression-backed property.",
        )
        obj.HumanLength = float(index)
        obj.setExpression("HumanLength", f"{index} mm + 2 mm")
        obj.Placement = App.Placement(
            App.Vector(index * 10, index * 20, index * 30),
            App.Rotation(App.Vector(0, 0, 1), index * 5),
        )


def _create_sources(document):
    shape_source = document.addObject("Part::Feature", "NativeShapeSource")
    shape_source.Label = "Human BREP source"
    shape_source.Shape = Part.makeBox(10, 8, 6)
    shape_source.addProperty("App::PropertyString", "HumanSourceNote", "Human")
    shape_source.HumanSourceNote = "shape source must remain untouched"

    source_mesh = MeshPart.meshFromShape(
        Shape=Part.makeBox(4, 3, 2),
        LinearDeflection=0.25,
        AngularDeflection=math.radians(15),
        Relative=False,
        Segments=True,
    )
    assert int(source_mesh.CountFacets) == 12
    assert int(source_mesh.countSegments()) == 6
    mesh_source = document.addObject("Mesh::Feature", "NativeMeshSource")
    mesh_source.Label = "Human mesh source"
    mesh_source.Mesh = source_mesh
    mesh_source.Placement = App.Placement(
        App.Vector(25, 10, 5),
        App.Rotation(App.Vector(0, 0, 1), 30),
    )
    mesh_source.addProperty("App::PropertyString", "HumanSourceNote", "Human")
    mesh_source.HumanSourceNote = "mesh source must remain untouched"
    return shape_source, mesh_source


def _exercise_native_operation_matrix(
    root: Path,
    document,
    service: _Service,
    shape_source,
    mesh_source,
) -> None:
    """Execute every native backend overload and every declared output class."""

    reference_inputs = _create_arguments(document, shape_source, mesh_source)["inputs"]
    matrix_root = root / "native-operation-matrix"
    matrix_outputs = [
        {"name": "Standard", "type": "mesh"},
        {"name": "MaxLength", "type": "mesh"},
        {"name": "MaxArea", "type": "mesh"},
        {"name": "LocalLength", "type": "mesh"},
        {"name": "Deflection", "type": "mesh"},
        {"name": "MinMax", "type": "mesh"},
        {"name": "Face", "type": "face"},
        {"name": "Shell", "type": "shell"},
        {"name": "SurfaceCompound", "type": "compound"},
        {"name": "BoundaryCompound", "type": "compound"},
    ]
    matrix_source = (
        "result = {\n"
        "'Standard': api.mesh_from_shape(inputs['shape']),\n"
        "'MaxLength': api.mesh_from_shape(inputs['shape'], method='max_length', max_length=4),\n"
        "'MaxArea': api.mesh_from_shape(inputs['shape'], method='max_area', max_area=8),\n"
        "'LocalLength': api.mesh_from_shape(inputs['shape'], method='local_length', local_length=4),\n"
        "'Deflection': api.mesh_from_shape(inputs['shape'], method='deflection', deflection=0.5),\n"
        "'MinMax': api.mesh_from_shape(inputs['shape'], method='min_max_length', min_length=1, max_length=4),\n"
        "'Face': api.shape_from_mesh(inputs['mesh'], output_type='face', facet_indices=[1]),\n"
        "'Shell': api.shape_from_mesh(inputs['mesh'], output_type='shell', require_closed=True),\n"
        "'SurfaceCompound': api.shape_from_mesh(inputs['mesh'], output_type='compound'),\n"
        "'BoundaryCompound': api.shape_from_mesh(inputs['mesh'], output_type='compound', representation='boundary', segment_index=1),\n"
        "}\n"
    )
    try:
        capture = _captured(
            matrix_root,
            document,
            operation="create_program",
            arguments={
                "program_name": "Native MeshPart Operation Matrix",
                "source": matrix_source,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "shape": _reference_schema(),
                        "mesh": _reference_schema(),
                    },
                    "required": ["shape", "mesh"],
                    "additionalProperties": False,
                },
                "inputs": {
                    "shape": reference_inputs["shape"],
                    "mesh": reference_inputs["mesh"],
                },
                "expected_outputs": matrix_outputs,
            },
        )
        _, execution, validated = _prepare_execute_validate(capture, service)
        assert execution["meshpart_validation"]["output_count"] == len(matrix_outputs)
        by_name = {item["name"]: item for item in validated["outputs"]}
        assert by_name["Standard"]["meshpart_data"]["mesher_backend"] == ("opencascade")
        for name in ("MaxLength", "MaxArea", "LocalLength", "Deflection", "MinMax"):
            assert by_name[name]["meshpart_data"]["mesher_backend"] == "mefisto"
            assert by_name[name]["facts"]["facets"] > 0
        assert by_name["Face"]["facts"]["shape_type"] == "Face"
        assert by_name["Face"]["facts"]["faces"] == 1
        assert by_name["Shell"]["facts"]["shape_type"] == "Shell"
        assert by_name["Shell"]["detached_shape"].isClosed()
        assert by_name["SurfaceCompound"]["facts"]["shape_type"] == "Compound"
        assert by_name["SurfaceCompound"]["facts"]["faces"] == 12
        boundary = by_name["BoundaryCompound"]
        assert boundary["facts"]["shape_type"] == "Compound"
        assert boundary["meshpart_data"]["conversion"]["boundary_count"] == 1
        assert boundary["meshpart_data"]["conversion"]["boundary_edge_counts"] == [4]
    finally:
        shutil.rmtree(matrix_root, ignore_errors=True)

    for method, settings in (
        ("netgen_fineness", "fineness='moderate'"),
        (
            "netgen_custom",
            "growth_rate=0.3, segments_per_edge=1, segments_per_radius=2",
        ),
    ):
        capability_root = root / f"native-capability-{method}"
        try:
            capture = _captured(
                capability_root,
                document,
                operation="create_program",
                arguments={
                    "program_name": f"Native MeshPart {method}",
                    "source": (
                        "result = {'Result': api.mesh_from_shape(inputs['shape'], "
                        f"method='{method}', {settings})}}\n"
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {"shape": _reference_schema()},
                        "required": ["shape"],
                        "additionalProperties": False,
                    },
                    "inputs": {"shape": reference_inputs["shape"]},
                    "expected_outputs": [{"name": "Result", "type": "mesh"}],
                },
            )
            prepared = prepare_candidate(capture)
            prepared = finalize_candidate(
                prepared,
                capture_reference_inputs(service, prepared),
            )
            execution = execute_candidate(prepared, cancellation_check=None)
            assert execution["ok"] is False
            details = execution["observed"]["details"]
            assert details["stage"] == "native_mesher_capability"
            assert details["method"] == method
            assert details["netgen_available"] is False
            assert any(
                "standard" in str(change) for change in details["required_changes"]
            )
        finally:
            shutil.rmtree(capability_root, ignore_errors=True)


def main() -> int:
    _exercise_source_api()
    root = Path(tempfile.mkdtemp(prefix="vibecad-meshpart-native-"))
    document = App.newDocument("VibeScriptMeshPartNative")
    service = _Service(root)
    try:
        App.setActiveDocument(document.Name)
        shape_source, mesh_source = _create_sources(document)
        _exercise_native_operation_matrix(
            root,
            document,
            service,
            shape_source,
            mesh_source,
        )
        create_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments=_create_arguments(document, shape_source, mesh_source),
        )
        prepared, execution, validated = _prepare_execute_validate(
            create_capture,
            service,
        )
        assert len(prepared["resolved_references"]) == 2
        references = {
            item["object_name"]: item for item in prepared["resolved_references"]
        }
        assert references[shape_source.Name]["artifact_kind"] == "brep"
        assert references[shape_source.Name]["shape_type"] == "Solid"
        mesh_reference = references[mesh_source.Name]
        assert mesh_reference["artifact_kind"] == "mesh_bms"
        assert len(mesh_reference["mesh_segments"]) == 6
        assert mesh_reference["facts"]["facets"] == 12
        assert mesh_reference["mesh_source_placement_matrix"][3] == 25.0
        assert mesh_reference["mesh_source_placement_matrix"][7] == 10.0
        assert mesh_reference["mesh_source_placement_matrix"][11] == 5.0
        assert execution["meshpart_validation"] == validated["meshpart_validation"]
        assert execution["meshpart_validation"]["output_count"] == 3
        assert execution["meshpart_validation"]["mesh_output_count"] == 1
        assert execution["meshpart_validation"]["shape_output_count"] == 2
        by_name = {item["name"]: item for item in validated["outputs"]}
        assert by_name["Mesh"]["meshpart_data"]["mesher_backend"] == "opencascade"
        assert len(by_name["Mesh"]["meshpart_data"]["segments"]) == 6
        assert by_name["Solid"]["facts"]["shape_type"] == "Solid"
        assert by_name["Solid"]["facts"]["faces"] == 6
        assert by_name["Boundary"]["facts"]["shape_type"] == "Wire"
        solid_stages = by_name["Solid"]["meshpart_data"]["conversion"][
            "safe_native_stages"
        ]
        assert solid_stages == [
            "Mesh.harmonizeNormals",
            "Part.Shape.makeShapeFromMesh(sew=False)",
            "TopoShape.sewShape",
            "BREP topology normalization",
            "Part.makeSolid",
            "BREP solid normalization",
            "TopoShape.removeSplitter",
        ]
        assert by_name["Boundary"]["meshpart_data"]["conversion"][
            "safe_native_stages"
        ] == ["MeshPart.wireFromMesh"]

        malformed = copy.deepcopy(execution)
        malformed["outputs"][0]["meshpart_data"]["diagnostics"]["facets"] += 1
        _expect_error(
            "outputs.Mesh.meshpart_data.diagnostics",
            lambda: validate_candidate(prepared, malformed),
        )
        malformed = copy.deepcopy(execution)
        malformed["outputs"][0]["meshpart_data"]["artifact_sha256"] = "0" * 64
        _expect_error(
            "artifact digest changed",
            lambda: validate_candidate(prepared, malformed),
        )
        malformed = copy.deepcopy(execution)
        malformed["outputs"][0]["meshpart_data"]["mesher_backend"] = "netgen"
        _expect_error(
            "inconsistent mesher backend",
            lambda: validate_candidate(prepared, malformed),
        )
        malformed = copy.deepcopy(execution)
        malformed["outputs"][1]["meshpart_data"]["conversion"]["safe_native_stages"][
            1
        ] = "Part.Shape.makeShapeFromMesh(sew=True)"
        _expect_error(
            "conversion trace is unsupported",
            lambda: validate_candidate(prepared, malformed),
        )

        retain_candidate(prepared, status="validated")
        publication = publish_candidate(service, prepared, validated)
        accepted = accept_candidate(prepared, publication)
        outputs = _outputs(document, accepted)
        stable_names = {name: str(obj.Name) for name, obj in outputs.items()}
        assert _managed_names(document, prepared["program_id"]) == set(
            stable_names.values()
        )
        _assert_native_outputs(outputs, validated)
        for obj in outputs.values():
            assert list(getattr(obj, PROP_INPUT_OBJECTS)) == [
                shape_source,
                mesh_source,
            ]
        assert shape_source.HumanSourceNote == "shape source must remain untouched"
        assert mesh_source.HumanSourceNote == "mesh source must remain untouched"
        _add_human_state(outputs)

        inspection = complete_inspection(
            {
                **create_capture,
                "program_id": prepared["program_id"],
                "live_programs": [],
            }
        )
        assert inspection["ok"] is True
        assert inspection["program"]["accepted_revision"] == prepared["revision"]
        assert inspection["program"]["live_outputs"]["Solid"]["meshpart_data"]

        consumer = document.addObject("App::FeaturePython", "HumanMeshPartConsumer")
        consumer.addProperty("App::PropertyLinkList", "Sources")
        consumer.Sources = list(outputs.values())

        failed_capture = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "patch": {"segment": 999},
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
        assert failed_execution["observed"]["details"]["stage"] == "mesh_selection"
        assert failed_execution["observed"]["details"]["available_segment_count"] == 6
        failure_details = failed_execution["observed"]["details"]
        assert failed_execution["domain_failure_stage"] == "mesh_selection"
        assert failed_execution["retry"]["required_changes"] == [
            failure_details["correction"]
        ]
        assert "reported source segment" in failure_details["correction"]
        retain_candidate(
            failed_prepared,
            status="failed",
            failure=failed_execution,
        )
        for name, obj in outputs.items():
            assert document.getObject(stable_names[name]) is obj
            assert str(obj.VibeCADVibeScriptRevision) == accepted["accepted_revision"]

        recovery_capture = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": failed_prepared["revision"],
                "patch": {"segment": 1, "deflection": 0.2},
            },
        )
        _, _, recovery_validated, recovery_publication, accepted = _run_candidate(
            recovery_capture,
            service,
        )
        assert recovery_publication["created_objects"] == []
        outputs = _outputs(document, accepted)
        assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
        assert consumer.Sources == list(outputs.values())
        _assert_native_outputs(outputs, recovery_validated)
        for name, obj in outputs.items():
            assert obj.HumanMeshPartNote == f"preserve {name}"

        edit_capture = _captured(
            root,
            document,
            operation="edit_source",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "source": (
                    _source()
                    .replace("label='Converted Mesh'", "label='Edited Mesh'")
                    .replace("label='Recovered Solid'", "label='Edited Solid'")
                    .replace("label='Face Boundary'", "label='Edited Boundary'")
                ),
            },
        )
        _, _, edit_validated, edit_publication, accepted = _run_candidate(
            edit_capture,
            service,
        )
        assert edit_publication["created_objects"] == []
        outputs = _outputs(document, accepted)
        assert [str(outputs[name].Label) for name in outputs] == [
            "Edited Mesh",
            "Edited Solid",
            "Edited Boundary",
        ]
        assert consumer.Sources == list(outputs.values())
        _assert_native_outputs(outputs, edit_validated)

        prior_mesh_digest = next(
            item["mesh_sha256"]
            for item in prepared["resolved_references"]
            if item["object_name"] == mesh_source.Name
        )
        mesh_source.Placement = App.Placement(
            App.Vector(35, 15, 7),
            App.Rotation(App.Vector(0, 0, 1), 45),
        )
        observer_marked = {
            str(obj.Name)
            for obj in outputs.values()
            if str(obj.VibeCADDerivedState) == "stale"
        }
        explicitly_marked = set(
            mark_programs_stale_from_source(mesh_source, "Placement")
        )
        assert observer_marked | explicitly_marked == set(stable_names.values())
        assert all(obj.VibeCADDerivedState == "stale" for obj in outputs.values())
        dependency_capture = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "patch": {"tolerance": 0.01},
            },
        )
        dependency_prepared, _, dependency_validated, _, accepted = _run_candidate(
            dependency_capture,
            service,
        )
        new_mesh_reference = next(
            item
            for item in dependency_prepared["resolved_references"]
            if item["object_name"] == mesh_source.Name
        )
        assert new_mesh_reference["mesh_sha256"] != prior_mesh_digest
        outputs = _outputs(document, accepted)
        assert all(obj.VibeCADDerivedState == "accepted" for obj in outputs.values())
        assert consumer.Sources == list(outputs.values())
        _assert_native_outputs(outputs, dependency_validated)

        reconfigured_source = (
            _source()
            .replace(
                "method='standard', linear_deflection=inputs['deflection'], "
                "angular_deflection_degrees=15, preserve_face_groups=True",
                "method='max_length', max_length=inputs['max_length']",
            )
            .replace("label='Converted Mesh'", "label='Reconfigured Mesh'")
            .replace("label='Recovered Solid'", "label='Reconfigured Solid'")
            .replace("label='Face Boundary'", "label='Reconfigured Boundary'")
        )
        reconfigured_schema = _input_schema()
        properties = dict(reconfigured_schema["properties"])
        properties.pop("deflection")
        properties["max_length"] = {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": 1000,
        }
        reconfigured_schema["properties"] = properties
        reconfigured_schema["required"] = [
            "shape",
            "mesh",
            "max_length",
            "tolerance",
            "segment",
        ]
        reconfigured_inputs = {
            key: value
            for key, value in dependency_prepared["inputs"].items()
            if key != "deflection"
        }
        reconfigured_inputs["max_length"] = 1.5
        reconfigure_capture = _captured(
            root,
            document,
            operation="reconfigure_program",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "source": reconfigured_source,
                "input_schema": reconfigured_schema,
                "inputs": reconfigured_inputs,
                "expected_outputs": EXPECTED_OUTPUTS,
            },
        )
        reconfigured_prepared, _, reconfigured_validated = _prepare_execute_validate(
            reconfigure_capture, service
        )
        assert (
            reconfigured_validated["outputs"][0]["meshpart_data"]["mesher_backend"]
            == "mefisto"
        )
        retain_candidate(reconfigured_prepared, status="validated")
        before_publication_fault = {
            name: _snapshot(obj) for name, obj in outputs.items()
        }
        original_configure_shape = publication_module._configure_meshpart_shape

        def fail_after_shape_assignment(obj, item):
            original_configure_shape(obj, item)
            if item["name"] == "Solid":
                raise RuntimeError("injected MeshPart publication failure")

        publication_module._configure_meshpart_shape = fail_after_shape_assignment
        try:
            _expect_error(
                "injected MeshPart publication failure",
                lambda: publish_candidate(
                    service,
                    reconfigured_prepared,
                    reconfigured_validated,
                ),
            )
        finally:
            publication_module._configure_meshpart_shape = original_configure_shape
        outputs = {
            name: document.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(obj is not None for obj in outputs.values())
        for name, obj in outputs.items():
            _assert_snapshot(obj, before_publication_fault[name])
        assert consumer.Sources == list(outputs.values())

        reconfigured_publication = publish_candidate(
            service,
            reconfigured_prepared,
            reconfigured_validated,
        )
        accepted = accept_candidate(
            reconfigured_prepared,
            reconfigured_publication,
        )
        outputs = _outputs(document, accepted)
        assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
        assert consumer.Sources == list(outputs.values())
        assert [str(outputs[name].Label) for name in outputs] == [
            "Reconfigured Mesh",
            "Reconfigured Solid",
            "Reconfigured Boundary",
        ]
        _assert_native_outputs(outputs, reconfigured_validated)
        for name, obj in outputs.items():
            assert obj.HumanMeshPartNote == f"preserve {name}"
            assert obj.ExpressionEngine

        context = complete_domain_context(domain_context_snapshot(service, "meshpart"))
        shape_context = next(
            item
            for item in context["document_shape_sources"]["objects"]
            if item["name"] == shape_source.Name
        )
        mesh_context = next(
            item
            for item in context["document_mesh_sources"]["objects"]
            if item["name"] == mesh_source.Name
        )
        assert shape_context["eligible_for_mesh_from_shape"] is True
        assert shape_context["facts"]["shape_type"] == "Solid"
        assert mesh_context["eligible_for_shape_from_mesh"] is True
        assert mesh_context["native_summary"]["segments"] == 6
        converted_context = next(
            item
            for item in context["document_mesh_sources"]["objects"]
            if item["name"] == stable_names["Mesh"]
        )
        assert converted_context["accepted_validation"]["schema"] == VALIDATION_SCHEMA
        assert converted_context["accepted_validation"]["method"] == "max_length"
        assert converted_context["accepted_validation"]["mesher_backend"] == "mefisto"

        save_path = root / "meshpart-production.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        reopened = App.openDocument(str(save_path))
        assert reopened is not None
        App.setActiveDocument(reopened.Name)
        reopened.recompute()
        shape_source = reopened.getObject("NativeShapeSource")
        mesh_source = reopened.getObject("NativeMeshSource")
        consumer = reopened.getObject("HumanMeshPartConsumer")
        assert (
            shape_source is not None
            and mesh_source is not None
            and consumer is not None
        )
        outputs = {
            name: reopened.getObject(object_name)
            for name, object_name in stable_names.items()
        }
        assert all(obj is not None for obj in outputs.values())
        assert consumer.Sources == list(outputs.values())
        assert shape_source.HumanSourceNote == "shape source must remain untouched"
        assert mesh_source.HumanSourceNote == "mesh source must remain untouched"
        _assert_native_outputs(outputs, reconfigured_validated)
        for name, obj in outputs.items():
            assert obj.HumanMeshPartNote == f"preserve {name}"
            assert obj.ExpressionEngine

        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": reconfigured_prepared["program_id"],
                "expected_revision": reconfigured_prepared["revision"],
                "reason": "verify MeshPart external-reference guard",
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

        before_delete_fault = {name: _snapshot(obj) for name, obj in outputs.items()}
        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": reconfigured_prepared["program_id"],
                "expected_revision": reconfigured_prepared["revision"],
                "reason": "exercise explicit MeshPart deletion rollback",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        original_remove = publication_module._remove_timeline_deletion

        def fail_after_committed_removal(active_document, deletion):
            original_remove(active_document, deletion)
            active_document.commitTransaction()
            raise RuntimeError("injected MeshPart deletion failure")

        publication_module._remove_timeline_deletion = fail_after_committed_removal
        try:
            _expect_error(
                "injected MeshPart deletion failure",
                lambda: delete_live_program(service, prepared_delete),
            )
            restore_prepared_delete(prepared_delete)
        finally:
            publication_module._remove_timeline_deletion = original_remove
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
                "program_id": reconfigured_prepared["program_id"],
                "expected_revision": reconfigured_prepared["revision"],
                "reason": "MeshPart production integration complete",
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
                    "integration": "meshpart_vibescript_api",
                    "canonical_operations": list(EXPORTS),
                    "stable_outputs": stable_names,
                    "native_brep_and_mesh": True,
                    "placement_baked_references": True,
                    "face_group_roundtrip": True,
                    "exact_worker_host_validation": True,
                    "explicit_publication_rollback": True,
                    "explicit_deletion_rollback": True,
                    "failed_candidate_retention": True,
                    "save_reopen": True,
                    "external_reference_guard": True,
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
