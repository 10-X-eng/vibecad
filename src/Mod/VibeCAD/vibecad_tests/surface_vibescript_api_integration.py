# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD integration gate for the Surface VibeScript domain."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from vibescript_domain_api import create_domain_api  # noqa: E402
from vibescript_surface_worker import (  # noqa: E402
    SurfaceCandidateError,
    configure_surface_references,
    validate_and_build_surface,
)
from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    delete_live_program,
    mark_programs_stale_from_source,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    SurfaceDomainAdapter,
    _validate_surface_execution,
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
)


EXPORTS = (
    "line",
    "circle",
    "bezier",
    "bspline",
    "wire",
    "from_object",
    "face",
    "surface",
    "boundary",
    "curve_constraint",
    "face_constraint",
    "point_constraint",
    "fill",
    "blend",
    "extend",
    "loft",
    "thicken",
    "shell",
)
OUTPUT_TYPES = (
    "surface",
    "face",
    "shell",
    "fill",
    "blend",
    "extension",
    "loft",
    "solid",
)


def _api():
    return create_domain_api("surface", EXPORTS, OUTPUT_TYPES)


def _expect_error(fragment: str, call) -> None:
    try:
        call()
    except (TypeError, ValueError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f"Expected validation failure containing {fragment!r}.")


def _exercise_source_api() -> None:
    api = _api()
    assert tuple(api.exported_names) == EXPORTS
    assert not hasattr(api, "output")
    for name in EXPORTS:
        method = getattr(api, name)
        signature = str(inspect.signature(method))
        assert "*args" not in signature
        assert "**" not in signature
        assert inspect.getdoc(method)

    edge = api.line([0, 0, 0], [10, 0, 0], label="Edge")
    wire = api.wire(
        [[0, 0, 0], [10, 0, 0], [10, 8, 0], [0, 8, 0]], closed=True
    )
    face = api.face(wire)
    boundary = api.boundary(edge)
    assert edge.to_payload()["operation"] == "line"
    assert face.output_type == "face"
    assert api.fill([boundary]).output_type == "fill"

    _expect_error("must differ from start", lambda: api.line([0, 0, 0], [0, 0, 0]))
    _expect_error("greater than 0", lambda: api.circle([0, 0, 0], 0))
    _expect_error(
        "must be non-zero", lambda: api.circle([0, 0, 0], 2, normal=[0, 0, 0])
    )
    _expect_error("2-64 points", lambda: api.bezier([[0, 0, 0]]))
    _expect_error(
        "3-4096 points", lambda: api.bspline([[0, 0, 0], [1, 0, 0]])
    )
    _expect_error(
        "contain exactly", lambda: api.from_object({"object_name": "Box"}, "solid")
    )
    _expect_error(
        "mutually exclusive",
        lambda: api.from_object(
            {"document_uid": "doc", "object_name": "Box"},
            "face",
            subelement="Face1",
            interface="TopFace",
        ),
    )
    _expect_error("must have type", lambda: api.face(edge))
    _expect_error("support_face", lambda: api.boundary(edge, continuity="G1"))
    _expect_error(
        "must not exceed maximum_degree",
        lambda: api.fill([boundary], degree=9, maximum_degree=8),
    )
    _expect_error(
        "must be 'stretched'", lambda: api.blend([edge, edge], style="bad")
    )
    _expect_error("one boolean", lambda: api.blend([edge, edge], reversed=[True]))
    _expect_error("must have type", lambda: api.extend(edge))
    _expect_error("2-256", lambda: api.loft([edge]))
    _expect_error("non-zero", lambda: api.thicken(face, 0))
    _expect_error("duplicates", lambda: api.thicken(face, 1, remove_faces=[1, 1]))
    _expect_error("must have type", lambda: api.shell([edge]))

    pack = get_vibescript_pack("SurfaceWorkbench")
    assert pack is not None and pack.production_ready
    description = SurfaceDomainAdapter(pack).describe_api()
    assert description["api_contract"] == "vibecad-vibescript-surface-api-v1"
    assert [item["name"] for item in description["runtime_exports"]] == list(EXPORTS)
    assert set(description["typed_output_contracts"]) == set(OUTPUT_TYPES)
    assert "**properties" not in json.dumps(description["runtime_exports"])


def _closed_wire(api, points):
    return api.wire(points, closed=True)


def _face(api, points):
    return api.face(_closed_wire(api, points))


def _cube_faces(api, size: float = 4.0):
    s = float(size)
    return [
        _face(api, [[0, 0, 0], [s, 0, 0], [s, s, 0], [0, s, 0]]),
        _face(api, [[0, 0, s], [0, s, s], [s, s, s], [s, 0, s]]),
        _face(api, [[0, 0, 0], [0, 0, s], [s, 0, s], [s, 0, 0]]),
        _face(api, [[s, 0, 0], [s, 0, s], [s, s, s], [s, s, 0]]),
        _face(api, [[s, s, 0], [s, s, s], [0, s, s], [0, s, 0]]),
        _face(api, [[0, s, 0], [0, s, s], [0, 0, s], [0, 0, 0]]),
    ]


def _point_grid(rows: int, columns: int, *, scale: float = 1.0):
    return [
        [
            [
                float(column) * scale,
                float(row) * scale,
                0.15 * float(row * column) * scale,
            ]
            for column in range(columns)
        ]
        for row in range(rows)
    ]


def _reference_entry(root: Path, *, name: str, shape, **metadata: Any) -> dict[str, Any]:
    relative = Path("references") / f"{name}.brep"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shape.exportBrep(str(target))
    return {
        "document_uid": "surface-direct-document",
        "object_name": name,
        "label": name,
        "type_id": "Part::Feature",
        "shape_type": str(shape.ShapeType),
        "brep_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "artifact_path": str(relative),
        "facts": {
            "solids": len(shape.Solids),
            "shells": len(shape.Shells),
            "faces": len(shape.Faces),
            "wires": len(shape.Wires),
            "edges": len(shape.Edges),
            "vertices": len(shape.Vertexes),
        },
        "source_kind": "shape",
        "source_program_id": "",
        "source_program_domain": "",
        "source_revision": "",
        "transient_topology": False,
        "requires_semantic_interfaces": False,
        "published_interfaces": {},
        **metadata,
    }


def _direct_output(name: str, value) -> dict[str, Any]:
    return {"name": name, "type": value.output_type}


def _exercise_isolated_native_operations(root: Path) -> None:
    import FreeCAD as App
    import Part

    direct_root = root / "direct-worker"
    (direct_root / "outputs").mkdir(parents=True)
    box_entry = _reference_entry(
        direct_root,
        name="SourceBox",
        shape=Part.makeBox(10, 8, 6),
    )
    configure_surface_references(direct_root, [box_entry])
    api = _api()

    outer = _closed_wire(
        api, [[0, 0, 0], [12, 0, 0], [12, 10, 0], [0, 10, 0]]
    )
    hole = _closed_wire(api, [[3, 3, 0], [9, 3, 0], [9, 7, 0], [3, 7, 0]])
    holed_face = api.face(outer, holes=[hole], label="Holed Face")

    interpolated = api.surface(_point_grid(4, 4, scale=4), label="Interpolated")
    approximated = api.surface(
        _point_grid(6, 5, scale=2),
        mode="approximate",
        degree_min=2,
        degree_max=4,
        continuity="C1",
        tolerance=0.25,
        parametrization="centripetal",
        smoothing=[1.0, 0.5, 0.25],
        label="Approximated",
    )

    corners = ([0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0])
    fill_edges = [
        api.line(corners[index], corners[(index + 1) % 4]) for index in range(4)
    ]
    fill = api.fill(
        [api.boundary(edge) for edge in fill_edges],
        curve_constraints=[
            api.curve_constraint(
                api.bezier([[0, 5, 0], [5, 5, 1], [10, 5, 0]])
            )
        ],
        point_constraints=[api.point_constraint([5, 5, 1])],
        degree=3,
        points_on_curve=18,
        iterations=3,
        anisotropy=True,
        tolerance_2d=1.0e-5,
        tolerance_3d=1.0e-4,
        angular_tolerance=0.02,
        curvature_tolerance=0.2,
        maximum_degree=8,
        maximum_segments=12,
        label="Constrained Fill",
    )

    support = _face(api, corners)
    supported_fill = api.fill(
        [
            api.boundary(fill_edges[0], continuity="G1", support_face=support),
            *[api.boundary(edge) for edge in fill_edges[1:]],
        ],
        face_constraints=[api.face_constraint(support)],
        initial_face=support,
        label="Supported Fill",
    )

    blend_edges = [
        api.bezier([[0, 0, 0], [5, -1, 1], [10, 0, 0]]),
        api.line([10, 0, 0], [10, 8, 0]),
        api.bezier([[10, 8, 0], [5, 9, 2], [0, 8, 0]]),
        api.line([0, 8, 0], [0, 0, 0]),
    ]
    stretched = api.blend(blend_edges, style="stretched", label="Stretched")
    coons = api.blend(blend_edges, style="coons", label="Coons")
    curved = api.blend(
        blend_edges,
        style="curved",
        reversed=[False, False, False, False],
        label="Curved",
    )
    extended = api.extend(
        interpolated,
        u_negative=0.1,
        u_positive=0.15,
        v_negative=0.05,
        v_positive=0.2,
        tolerance=0.05,
        samples_u=24,
        samples_v=28,
        label="Extended",
    )

    open_lower = api.bezier([[0, 0, 0], [5, -2, 0], [10, 0, 0]])
    open_upper = api.bezier([[0, 4, 5], [5, 7, 7], [10, 4, 5]])
    loft = api.loft([open_lower, open_upper], max_degree=4, label="Open Loft")
    ruled = api.loft([open_lower, open_upper], ruled=True, label="Ruled Loft")
    lower = _closed_wire(api, [[0, 0, 0], [8, 0, 0], [8, 6, 0], [0, 6, 0]])
    upper = _closed_wire(api, [[1, 1, 5], [7, 1, 5], [7, 5, 5], [1, 5, 5]])
    solid_loft = api.loft([lower, upper], solid=True, max_degree=3, label="Solid Loft")
    thickened = api.thicken(
        api.face(lower),
        0.5,
        tolerance=1.0e-6,
        join="arc",
        label="Thickened",
    )
    hollowed = api.thicken(
        api.from_object(
            {"document_uid": "surface-direct-document", "object_name": "SourceBox"},
            "solid",
        ),
        -0.5,
        remove_faces=[6],
        tolerance=1.0e-6,
        join="arc",
        label="Hollowed",
    )
    cube_faces = _cube_faces(api)
    shell = api.shell(cube_faces, tolerance=1.0e-6, label="Sewn Shell")
    sewn_solid = api.shell(
        cube_faces,
        make_solid=True,
        tolerance=1.0e-6,
        cut_free_edges=False,
        nonmanifold=False,
        label="Sewn Solid",
    )
    exact_face = api.from_object(
        {"document_uid": "surface-direct-document", "object_name": "SourceBox"},
        "face",
        subelement="Face1",
        label="Exact Face",
    )

    values = {
        "HoledFace": holed_face,
        "Interpolated": interpolated,
        "Approximated": approximated,
        "Fill": fill,
        "SupportedFill": supported_fill,
        "Stretched": stretched,
        "Coons": coons,
        "Curved": curved,
        "Extended": extended,
        "Loft": loft,
        "Ruled": ruled,
        "SolidLoft": solid_loft,
        "Thickened": thickened,
        "Hollowed": hollowed,
        "Shell": shell,
        "SewnSolid": sewn_solid,
        "ExactFace": exact_face,
    }
    expected = [_direct_output(name, value) for name, value in values.items()]
    document = App.newDocument("SurfaceDirectWorker", "Surface Direct Worker", True, True)
    try:
        outputs, validation = validate_and_build_surface(
            document,
            values,
            expected,
            direct_root,
            max_shape_subelements=64,
        )
        assert validation["schema"] == "vibecad-vibescript-surface-validation-v1"
        assert validation["output_count"] == len(values)
        assert validation["operation_count"] > len(values)
        by_name = {item["name"]: item for item in outputs}
        exact_types = {
            "HoledFace": "Face",
            "Interpolated": "Face",
            "Approximated": "Face",
            "Fill": "Face",
            "SupportedFill": "Face",
            "Stretched": "Face",
            "Coons": "Face",
            "Curved": "Face",
            "Extended": "Face",
            "SolidLoft": "Solid",
            "Thickened": "Solid",
            "Hollowed": "Solid",
            "Shell": "Shell",
            "SewnSolid": "Solid",
            "ExactFace": "Face",
        }
        for name, shape_type in exact_types.items():
            assert by_name[name]["facts"]["shape_type"] == shape_type, name
        assert by_name["Loft"]["facts"]["shape_type"] in {"Face", "Shell"}
        assert by_name["Ruled"]["facts"]["shape_type"] in {"Face", "Shell"}
        assert by_name["HoledFace"]["facts"]["wires"] == 2
        assert by_name["Fill"]["surface_data"]["engine"] == "Surface::Filling"
        assert by_name["Fill"]["surface_data"]["curve_constraint_edge_count"] == 1
        assert by_name["Fill"]["surface_data"]["point_constraint_count"] == 1
        assert by_name["SupportedFill"]["surface_data"]["face_constraint_count"] == 1
        assert by_name["SupportedFill"]["surface_data"]["has_initial_face"] is True
        assert by_name["Stretched"]["surface_data"]["fill_type"] == "Stretched"
        assert by_name["Coons"]["surface_data"]["fill_type"] == "Coons"
        assert by_name["Curved"]["surface_data"]["fill_type"] == "Curved"
        assert by_name["Stretched"]["surface_data"][
            "preconditioned_boundary_count"
        ] == 2
        assert by_name["Extended"]["surface_data"]["native_properties"] == {
            "u_negative": 0.1,
            "u_positive": 0.15,
            "v_negative": 0.05,
            "v_positive": 0.2,
            "tolerance": 0.05,
            "samples_u": 24,
            "samples_v": 28,
        }
        assert (
            by_name["Hollowed"]["surface_data"]["engine"]
            == "TopoShape.makeThickness"
        )
        assert by_name["Shell"]["surface_data"]["engine"] == "Surface::Sewing"
        assert by_name["ExactFace"]["surface_data"]["resolved_subelement"] == "Face1"
        assert all((direct_root / item["artifact_path"]).is_file() for item in outputs)
    finally:
        App.closeDocument(document.Name)

    invalid_api = _api()
    bad = invalid_api.shell(
        [
            _face(
                invalid_api,
                [[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0]],
            ),
            _face(
                invalid_api,
                [[10, 0, 0], [12, 0, 0], [12, 2, 0], [10, 2, 0]],
            ),
        ]
    )
    bad_root = root / "disconnected-worker"
    (bad_root / "outputs").mkdir(parents=True)
    configure_surface_references(bad_root, [])
    document = App.newDocument(
        "SurfaceDisconnectedWorker", "Surface Disconnected", True, True
    )
    try:
        try:
            validate_and_build_surface(
                document,
                {"Bad": bad},
                [{"name": "Bad", "type": "shell"}],
                bad_root,
                max_shape_subelements=32,
            )
        except SurfaceCandidateError as exc:
            assert "exactly one connected shell" in str(exc)
            assert exc.details["stage"] == "shape_contract"
            assert exc.details["operation"] == "shell"
            assert "exactly one connected shell" in exc.details["correction"]
        else:
            raise AssertionError("Disconnected Surface faces were silently published.")
    finally:
        App.closeDocument(document.Name)


def _exercise_semantic_reference_contract(root: Path) -> None:
    import FreeCAD as App
    import Part

    semantic_root = root / "semantic-references"
    (semantic_root / "outputs").mkdir(parents=True)
    entry = _reference_entry(
        semantic_root,
        name="RegeneratingBody",
        shape=Part.makeBox(10, 8, 6),
        source_kind="scripted_publication",
        source_program_id="partdesign-model",
        source_program_domain="partdesign",
        source_revision="accepted-revision",
        transient_topology=True,
        requires_semantic_interfaces=True,
        published_interfaces={
            "TopFace": {
                "model_id": "partdesign-model",
                "publication_name": "Body",
                "output_key": "Body",
                "subelements": ["Face6"],
            },
            "WholeSolid": {
                "model_id": "partdesign-model",
                "publication_name": "Body",
                "output_key": "Body",
                "subelements": [],
            },
            "MultipleFaces": {
                "model_id": "partdesign-model",
                "publication_name": "Body",
                "output_key": "Body",
                "subelements": ["Face1", "Face2"],
            },
            "WrongTopology": {
                "model_id": "partdesign-model",
                "publication_name": "Body",
                "output_key": "Body",
                "subelements": ["Edge1"],
            },
        },
    )
    configure_surface_references(semantic_root, [entry])
    reference = {
        "document_uid": "surface-direct-document",
        "object_name": "RegeneratingBody",
    }
    api = _api()
    valid = {
        "SemanticFace": api.from_object(
            reference, "face", interface="TopFace", label="Semantic Face"
        ),
        "WholeSolid": api.from_object(
            reference, "solid", interface="WholeSolid", label="Whole Solid"
        ),
    }
    document = App.newDocument(
        "SurfaceSemanticReferences", "Surface Semantic References", True, True
    )
    try:
        outputs, _validation = validate_and_build_surface(
            document,
            valid,
            [_direct_output(name, value) for name, value in valid.items()],
            semantic_root,
            max_shape_subelements=32,
        )
        by_name = {item["name"]: item for item in outputs}
        semantic = by_name["SemanticFace"]["surface_data"]["semantic_interface"]
        assert semantic == {
            "interface_name": "TopFace",
            "model_id": "partdesign-model",
            "publication_name": "Body",
            "output_key": "Body",
        }
        assert by_name["SemanticFace"]["surface_data"]["resolved_subelement"] == "Face6"
        assert by_name["WholeSolid"]["facts"]["shape_type"] == "Solid"

        invalid = (
            (
                api.from_object(reference, "face", subelement="Face1"),
                "transient topology",
                "reference_selection",
            ),
            (
                api.from_object(reference, "face", interface="MissingFace"),
                "does not exist",
                "reference_selection",
            ),
            (
                api.from_object(reference, "face", interface="MultipleFaces"),
                "multiple subelements",
                "reference_selection",
            ),
            (
                api.from_object(reference, "face", interface="WrongTopology"),
                "ShapeType",
                "shape_contract",
            ),
        )
        for index, (value, message, stage) in enumerate(invalid):
            try:
                validate_and_build_surface(
                    document,
                    {"Invalid": value},
                    [{"name": "Invalid", "type": "face"}],
                    semantic_root,
                    max_shape_subelements=32,
                )
            except SurfaceCandidateError as exc:
                assert message in str(exc), (index, str(exc))
                assert exc.details["stage"] == stage, (index, exc.details)
                assert exc.details["operation"] == "from_object"
                assert "authenticated input reference" in exc.details["correction"]
            else:
                raise AssertionError(f"Invalid semantic reference case {index} succeeded.")
    finally:
        App.closeDocument(document.Name)


class _Service:
    def __init__(self, document, project_root: Path) -> None:
        self.document = document
        self.project_root = project_root

    def _active_document(self):
        return self.document

    @staticmethod
    def active_workbench_name() -> str:
        return "SurfaceWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "surface-production-revision"

    def project_scope_snapshot(self) -> dict[str, str]:
        return {"root": str(self.project_root)}

    def provider_working_set(self) -> dict[str, Any]:
        target = self.document.getObject("NativeSurfaceSource")
        if target is None:
            return {"target_count": 0, "targets": []}
        return {
            "target_count": 1,
            "targets": [
                {
                    "name": str(target.Name),
                    "label": str(target.Label),
                    "type_id": str(target.TypeId),
                }
            ],
        }

    @staticmethod
    def selection_summary() -> dict[str, list[Any]]:
        return {"selection": []}


def _reference_schema() -> dict[str, Any]:
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


def _program_source(*, publish_source_face: bool = True) -> str:
    result_prefix = "'SourceFace': source_face, " if publish_source_face else ""
    return (
        "source_face = api.from_object(inputs['source'], 'face', subelement='Face6', "
        "label='Source Face')\n"
        "grid = [[[0,0,0],[8,0,0],[16,0,0],[24,0,0]], "
        "[[0,8,0],[8,8,inputs['amplitude']],[16,8,inputs['amplitude']],[24,8,0]], "
        "[[0,16,0],[8,16,inputs['amplitude']],[16,16,inputs['amplitude']],[24,16,0]], "
        "[[0,24,0],[8,24,0],[16,24,0],[24,24,0]]]\n"
        "patch = api.surface(grid, label='Editable Surface')\n"
        "extended = api.extend(patch, u_negative=.05, u_positive=.1, "
        "v_negative=.05, v_positive=.1, tolerance=.05, samples_u=24, "
        "samples_v=24, label='Extended Surface')\n"
        "solid = api.thicken(source_face, inputs['thickness'], tolerance=1e-6, "
        "join='arc', label='Surface Solid')\n"
        f"result = {{{result_prefix}'Patch': patch, 'Extended': extended, "
        "'Solid': solid}\n"
    )


EXPECTED_LIFECYCLE_OUTPUTS = [
    {"name": "SourceFace", "type": "face"},
    {"name": "Patch", "type": "surface"},
    {"name": "Extended", "type": "extension"},
    {"name": "Solid", "type": "solid"},
]


def _base_capture(root: Path, document) -> dict[str, Any]:
    import FreeCAD as App

    pack = get_vibescript_pack("SurfaceWorkbench")
    assert pack is not None and pack.production_ready
    return {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "surface-production-revision",
        "document_objects": [
            {
                "name": str(obj.Name),
                "label": str(obj.Label),
                "type_id": str(obj.TypeId),
            }
            for obj in document.Objects
        ],
        "surface": resolve_modeling_surface(
            "SurfaceWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(Path(App.getHomePath()).resolve()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }


def _create_capture(base: dict[str, Any], reference: dict[str, str]) -> dict[str, Any]:
    return {
        **base,
        "operation": "create_program",
        "tool_name": "vibescript.surface.create_program",
        "arguments": {
            "program_name": "Production Surface",
            "source": _program_source(),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": _reference_schema(),
                    "amplitude": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 20,
                    },
                    "thickness": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "maximum": 5,
                    },
                },
                "required": ["source", "amplitude", "thickness"],
                "additionalProperties": False,
            },
            "inputs": {
                "source": reference,
                "amplitude": 4.0,
                "thickness": 0.5,
            },
            "expected_outputs": EXPECTED_LIFECYCLE_OUTPUTS,
        },
    }


def _finalize_prepared(prepared: dict[str, Any], service: _Service) -> dict[str, Any]:
    if prepared.get("reference_requirements") and not prepared.get("finalized"):
        return finalize_candidate(
            prepared,
            capture_reference_inputs(service, prepared),
        )
    return prepared


def _run_candidate(captured: dict[str, Any], service: _Service):
    prepared = _finalize_prepared(prepare_candidate(captured), service)
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    return prepared, execution, validated, publication, accepted


def _assert_live_contract(document, accepted: dict[str, Any]):
    identities = {
        name: details["object_name"]
        for name, details in accepted["live_outputs"].items()
    }
    objects = {
        name: document.getObject(object_name) for name, object_name in identities.items()
    }
    expected_types = {
        "SourceFace": "Face",
        "Patch": "Face",
        "Extended": "Face",
        "Solid": "Solid",
    }
    expected_operations = {
        "SourceFace": "from_object",
        "Patch": "surface",
        "Extended": "extend",
        "Solid": "thicken",
    }
    for name, shape_type in expected_types.items():
        obj = objects[name]
        assert obj is not None
        assert obj.TypeId == "Part::Feature"
        assert obj.Shape.ShapeType == shape_type
        assert not obj.Shape.isNull() and obj.Shape.isValid()
        assert obj.VibeCADSurfaceOperation == expected_operations[name]
        validation = json.loads(obj.VibeCADSurfaceValidation)
        assert validation["shape_type"] == shape_type
        assert validation["operation"] == expected_operations[name]
        assert str(getattr(obj, PROP_PROGRAM_ID)) == accepted["program_id"]
    assert objects["Patch"].VibeCADSurfaceEngine == "Part.BSplineSurface.interpolate"
    assert objects["Extended"].VibeCADSurfaceEngine == "Surface::Extend"
    assert objects["Solid"].VibeCADSurfaceEngine == "TopoShape.makeOffsetShape(fill=True)"
    return objects, identities


def _exercise_lifecycle(root: Path) -> None:
    import FreeCAD as App
    import Part

    document = App.newDocument("SurfaceProductionLifecycle")
    source_object = document.addObject("Part::Feature", "NativeSurfaceSource")
    source_object.Label = "Native Surface source"
    source_object.Shape = Part.makeBox(30, 24, 8)
    source_name = str(source_object.Name)
    reference = {
        "document_uid": str(document.Uid),
        "object_name": source_name,
    }
    service = _Service(document, root)
    base = _base_capture(root, document)
    create_capture = _create_capture(base, reference)
    prepared, execution, validated, publication, accepted = _run_candidate(
        create_capture, service
    )
    assert publication["recompute_deferred"] is True
    assert execution["surface_validation"]["output_count"] == 4
    assert len(prepared["resolved_references"]) == 1
    tampered_outputs = []
    for item in validated["outputs"]:
        tampered_item = dict(item)
        tampered_item["surface_data"] = copy.deepcopy(item["surface_data"])
        tampered_outputs.append(tampered_item)
    next(
        item for item in tampered_outputs if item["name"] == "Extended"
    )["surface_data"]["native_properties"]["samples_u"] = 99
    try:
        _validate_surface_execution(prepared, execution, tampered_outputs)
    except ValueError as exc:
        assert "samples_u changed" in str(exc), str(exc)
    else:
        raise AssertionError("Forged Surface native-property readback was accepted.")
    objects, identities = _assert_live_contract(document, accepted)
    original_objects = dict(objects)
    original_patch_hash = objects["Patch"].Shape.hashCode()

    inspection = complete_inspection(
        {**create_capture, "program_id": prepared["program_id"], "live_programs": []}
    )
    assert inspection["program"]["accepted_revision"] == prepared["revision"]
    assert inspection["program"]["working_revision"] == prepared["revision"]

    failed_capture = {
        **create_capture,
        "operation": "edit_source",
        "tool_name": "vibescript.surface.edit_source",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": prepared["revision"],
            "replacements": [
                {"old": "inputs['thickness']", "new": "0"},
            ],
        },
    }
    failed_prepared = _finalize_prepared(prepare_candidate(failed_capture), service)
    failed_execution = execute_candidate(failed_prepared, cancellation_check=None)
    assert failed_execution.get("ok") is False
    assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
    assert "api.thicken" in failed_execution["error"]
    assert "non-zero" in failed_execution["error"]
    failure_details = failed_execution["observed"]["details"]
    assert failure_details["stage"] == "source_validation"
    assert failure_details["operation"] == "thicken"
    assert failure_details["parameter"] == "thickness"
    assert failed_execution["domain_failure_stage"] == "source_validation"
    assert failed_execution["retry"]["required_changes"] == [
        failure_details["correction"]
    ]
    retain_candidate(failed_prepared, status="failed", failure=failed_execution)
    assert all(
        document.getObject(identities[name]) is original_objects[name]
        for name in identities
    )
    assert objects["Patch"].Shape.hashCode() == original_patch_hash
    failed_inspection = complete_inspection(
        {**failed_capture, "program_id": prepared["program_id"], "live_programs": []}
    )
    assert failed_inspection["program"]["working_revision"] == failed_prepared["revision"]
    assert failed_inspection["program"]["accepted_revision"] == prepared["revision"]
    assert failed_inspection["program"]["latest_candidate"]["status"] == "failed"

    recovery_capture = {
        **create_capture,
        "operation": "edit_source",
        "tool_name": "vibescript.surface.edit_source",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": failed_prepared["revision"],
            "replacements": [
                {
                    "old": "api.thicken(source_face, 0, tolerance=1e-6",
                    "new": (
                        "api.thicken(source_face, inputs['thickness'], "
                        "tolerance=1e-6"
                    ),
                }
            ],
        },
    }
    (
        recovery_prepared,
        _execution,
        _validated,
        recovery_publication,
        accepted,
    ) = _run_candidate(recovery_capture, service)
    assert recovery_publication["created_objects"] == []
    objects, recovered_identities = _assert_live_contract(document, accepted)
    assert recovered_identities == identities
    assert all(objects[name] is original_objects[name] for name in identities)

    consumer = document.addObject("App::FeaturePython", "NativeSurfaceConsumer")
    consumer.addProperty("App::PropertyLink", "Source", "Native")
    consumer.Source = objects["Patch"]
    update_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.surface.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": recovery_prepared["revision"],
            "patch": {"amplitude": 7.0},
        },
    }
    update_prepared, _execution, _validated, update_publication, accepted = (
        _run_candidate(update_capture, service)
    )
    assert update_publication["created_objects"] == []
    assert update_publication["downstream_references"]["safe_whole_object_uses"]
    assert consumer.Source is objects["Patch"]
    assert objects["Patch"].Shape.hashCode() != original_patch_hash

    unsafe = document.addObject("App::FeaturePython", "UnsafeSurfaceEdgeConsumer")
    unsafe.addProperty("App::PropertyLinkSub", "SourceEdge", "Native")
    unsafe.SourceEdge = (objects["Patch"], ["Edge1"])
    before_unsafe = objects["Patch"].Shape.hashCode()
    unsafe_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.surface.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": update_prepared["revision"],
            "patch": {"amplitude": 9.0},
        },
    }
    unsafe_prepared = _finalize_prepared(prepare_candidate(unsafe_capture), service)
    unsafe_execution = execute_candidate(unsafe_prepared, cancellation_check=None)
    assert unsafe_execution.get("ok") is True, unsafe_execution
    unsafe_validated = validate_candidate(unsafe_prepared, unsafe_execution)
    retain_candidate(unsafe_prepared, status="validated")
    try:
        publish_candidate(service, unsafe_prepared, unsafe_validated)
    except RuntimeError as exc:
        assert "Face/Edge/Vertex references" in str(exc)
        assert "UnsafeSurfaceEdgeConsumer" in str(exc)
    else:
        raise AssertionError("A transient Surface Edge1 consumer was silently accepted.")
    retain_candidate(
        unsafe_prepared,
        status="publication_failed",
        failure={
            "failure_code": "DOMAIN_PUBLICATION_FAILED",
            "failure_stage": "native_call",
            "error": "unsafe Surface subelement consumer",
        },
    )
    assert objects["Patch"].Shape.hashCode() == before_unsafe
    document.removeObject(unsafe.Name)

    final_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.surface.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": unsafe_prepared["revision"],
            "patch": {"amplitude": 10.0},
        },
    }
    final_prepared, _execution, _validated, _publication, accepted = _run_candidate(
        final_capture, service
    )
    objects, _ = _assert_live_contract(document, accepted)

    source_object.Shape = Part.makeBox(34, 26, 10)
    marked = mark_programs_stale_from_source(source_object, "Shape")
    assert set(marked) == set(identities.values())
    assert all(objects[name].VibeCADDerivedState == "stale" for name in identities)
    stale_digest = prepared["resolved_references"][0]["brep_sha256"]
    source_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.surface.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": final_prepared["revision"],
            "patch": {"thickness": 0.75},
        },
    }
    source_prepared, source_execution, _validated, _publication, accepted = (
        _run_candidate(source_capture, service)
    )
    assert source_prepared["resolved_references"][0]["brep_sha256"] != stale_digest
    source_face = next(
        item for item in source_execution["outputs"] if item["name"] == "SourceFace"
    )
    assert source_face["surface_data"]["reference"]["brep_sha256"] == source_prepared[
        "resolved_references"
    ][0]["brep_sha256"]
    assert all(objects[name].VibeCADDerivedState == "accepted" for name in identities)

    provider_context = complete_domain_context(domain_context_snapshot(service, "surface"))
    assert provider_context["domain"] == "surface"
    assert provider_context["surface_id"] == resolve_modeling_surface(
        "SurfaceWorkbench", "vibescript"
    ).surface_id
    source_candidate = next(
        item
        for item in provider_context["surface_input_candidates"]["objects"]
        if item["name"] == source_name
    )
    assert source_candidate["reference"] == reference
    assert source_candidate["eligible_surface_input"] is True
    assert source_candidate["facts"]["solids"] == 1
    assert source_candidate["selection_contract"] == "whole_shape_or_exact_subelement"
    program_context = next(
        item
        for item in provider_context["programs"]
        if item.get("program_id") == prepared["program_id"]
    )
    assert program_context["accepted_revision"] == source_prepared["revision"]

    reconfigured_source = _program_source(publish_source_face=False)
    reconfigure_capture = {
        **create_capture,
        "operation": "reconfigure_program",
        "tool_name": "vibescript.surface.reconfigure_program",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": source_prepared["revision"],
            "source": reconfigured_source,
            "input_schema": create_capture["arguments"]["input_schema"],
            "inputs": {
                "source": reference,
                "amplitude": 10.0,
                "thickness": 0.75,
            },
            "expected_outputs": EXPECTED_LIFECYCLE_OUTPUTS[1:],
        },
    }
    reconfigured, _execution, _validated, reconfigure_publication, accepted = (
        _run_candidate(reconfigure_capture, service)
    )
    assert reconfigure_publication["created_objects"] == []
    assert reconfigure_publication["retired_objects"] == [identities["SourceFace"]]
    assert document.getObject(identities["SourceFace"]) is None
    remaining_identities = {
        name: details["object_name"]
        for name, details in accepted["live_outputs"].items()
    }
    assert remaining_identities == {
        "Patch": identities["Patch"],
        "Extended": identities["Extended"],
        "Solid": identities["Solid"],
    }

    save_path = root / "surface-vibescript-production.FCStd"
    document.saveAs(str(save_path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(save_path))
    service.document = reopened
    reopened_source = reopened.getObject(source_name)
    reopened_objects = {
        name: reopened.getObject(object_name)
        for name, object_name in remaining_identities.items()
    }
    assert reopened_source is not None and all(reopened_objects.values())
    assert reopened.getObject(identities["SourceFace"]) is None
    assert reopened.getObject("NativeSurfaceConsumer").Source is reopened_objects["Patch"]
    for name, obj in reopened_objects.items():
        assert str(getattr(obj, PROP_PROGRAM_ID)) == prepared["program_id"]
        assert not obj.Shape.isNull() and obj.Shape.isValid(), name
        assert json.loads(obj.VibeCADSurfaceValidation)["shape_type"] == obj.Shape.ShapeType

    delete_capture = {
        **base,
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
        "operation": "delete_program",
        "tool_name": "vibescript.surface.delete_program",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": reconfigured["revision"],
            "reason": "Surface production integration cleanup",
        },
    }
    prepared_delete = None
    try:
        prepared_delete = prepare_delete(delete_capture)
        delete_live_program(service, prepared_delete)
    except RuntimeError as exc:
        assert "reference" in str(exc).lower()
        assert prepared_delete is not None
        restore_prepared_delete(prepared_delete)
    else:
        raise AssertionError("Surface deletion ignored a human-created consumer.")
    reopened.removeObject("NativeSurfaceConsumer")
    prepared_delete = prepare_delete(delete_capture)
    deletion = delete_live_program(service, prepared_delete)
    finished = finish_delete(prepared_delete, deletion)
    assert finished["artifacts_deleted"] is True
    assert all(reopened.getObject(name) is None for name in remaining_identities.values())
    assert reopened.getObject(source_name) is reopened_source
    App.closeDocument(reopened.Name)


def main() -> int:
    _exercise_source_api()
    root = Path(tempfile.mkdtemp(prefix="vibecad-surface-production-"))
    try:
        _exercise_isolated_native_operations(root)
        _exercise_semantic_reference_contract(root)
        _exercise_lifecycle(root)
    finally:
        shutil.rmtree(root)
    print("Surface VibeScript native API integration passed all explicit operations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
