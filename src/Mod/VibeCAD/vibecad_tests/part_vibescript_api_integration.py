# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD integration coverage for the production Part domain API."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import shutil
import sys
import tempfile

MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    PROP_INPUT_OBJECTS,
    mark_programs_stale_from_source,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    accept_candidate,
    capture_reference_inputs,
    complete_inspection,
    execute_candidate,
    finalize_candidate,
    finish_delete,
    prepare_delete,
    prepare_candidate,
    retain_candidate,
    restore_prepared_delete,
    validate_candidate,
)
from VibeCADVibeScriptDomains import (  # noqa: E402
    complete_domain_context,
    domain_context_snapshot,
    get_vibescript_pack,
)
from vibescript_domain_api import create_domain_api  # noqa: E402
from vibescript_part_worker import (  # noqa: E402
    PartOperationError,
    build_part_shape,
    configure_part_references,
    part_shape_facts,
)


def _shape(value):
    shape = build_part_shape(value.to_payload())
    assert not shape.isNull()
    assert shape.isValid()
    return shape


def _closed_wire(api, points):
    return api.wire(points, closed=True)


def _face(api, points):
    return api.face(_closed_wire(api, points))


def _near(value, expected, tolerance=1.0e-7):
    assert abs(float(value) - float(expected)) <= tolerance, (value, expected)


def _exercise_every_export(api) -> dict[str, dict]:
    shapes = {}

    import Part

    reference_root = Path(tempfile.mkdtemp(prefix="vibecad-part-reference-api-"))
    try:
        reference_path = reference_root / "source.brep"
        Part.makeBox(3, 4, 5).exportBrep(str(reference_path))
        configure_part_references(
            reference_root,
            [
                {
                    "document_uid": "api-fixture",
                    "object_name": "Source",
                    "shape_type": "Solid",
                    "brep_sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest(),
                    "artifact_path": "source.brep",
                }
            ],
        )
        referenced = _shape(
            api.from_object(
                {"document_uid": "api-fixture", "object_name": "Source"},
                output_type="solid",
            )
        )
        _near(referenced.Volume, 60.0)
        shapes["from_object"] = referenced
    finally:
        shutil.rmtree(reference_root)

    primitives = {
        "box": api.box(10, 8, 6),
        "wedge": api.wedge(10, 4, 6, ridge_x=2),
        "prism": api.prism(6, 5, 8, center=[2, 3, 4], rotation_degrees=30),
        "cylinder": api.cylinder(3, 8, angle=270),
        "cone": api.cone(4, 2, 7),
        "sphere": api.sphere(5, latitude1=-60, latitude2=75, longitude=300),
        "torus": api.torus(8, 2, sweep=270),
    }
    for name, value in primitives.items():
        shape = _shape(value)
        assert len(shape.Solids) == 1, name
        shapes[name] = shape
    plane = _shape(
        api.plane(
            10,
            6,
            origin=[1, 2, 3],
            normal=[0, 1, 1],
            x_direction=[1, 0, 0],
        )
    )
    assert plane.ShapeType == "Face"
    _near(plane.Area, 60)
    shapes["plane"] = plane
    _near(shapes["wedge"].Volume, 120.0)
    _near(shapes["prism"].Volume, 3 * (3**0.5) * 25 * 8 / 2)

    curves = {
        "line": api.line([0, 0, 0], [5, 0, 0]),
        "arc": api.arc([0, 0, 0], [3, 2, 0], [6, 0, 0]),
        "circle": api.circle(4, start_angle=15, end_angle=300),
        "ellipse": api.ellipse(6, 3, normal=[0, 1, 1]),
        "bezier": api.bezier(
            [[0, 0, 0], [2, 4, 0], [6, 0, 0]],
            weights=[1.0, 0.5, 1.0],
        ),
        "bspline": api.bspline([[0, 0, 0], [2, 3, 0], [4, -1, 0], [7, 2, 0]]),
        "nurbs_curve": api.nurbs_curve(
            [[0, 0, 0], [2, 4, 0], [5, -1, 0], [8, 2, 0]],
            2,
            [0.0, 0.5, 1.0],
            [3, 1, 3],
            weights=[1.0, 0.8, 0.7, 1.0],
        ),
    }
    for name, value in curves.items():
        shape = _shape(value)
        assert len(shape.Edges) == 1, name
        shapes[name] = shape
    assert list(shapes["bezier"].Edges[0].Curve.getWeights()) == [1.0, 0.5, 1.0]
    nurbs_geometry = shapes["nurbs_curve"].Edges[0].Curve
    assert int(nurbs_geometry.Degree) == 2
    assert list(nurbs_geometry.getWeights()) == [1.0, 0.8, 0.7, 1.0]
    helix = _shape(api.helix(2, 10, 4, angle=3, vertical_height=True))
    assert len(helix.Edges) >= 1
    _near(helix.BoundBox.ZLength, 10.0)
    shapes["helix"] = helix
    segmented_helix = _shape(
        api.helix(1.5, 60, 3, representation="segmented")
    )
    assert segmented_helix.ShapeType == "Wire"
    assert len(segmented_helix.Edges) > 1
    shapes["segmented_helix"] = segmented_helix

    point_wire = _closed_wire(api, [[0, 0, 0], [8, 0, 0], [8, 6, 0], [0, 6, 0]])
    edge_wire = api.wire(
        [
            api.line([0, 0, 0], [8, 0, 0]),
            api.line([8, 0, 0], [8, 6, 0]),
            api.line([8, 6, 0], [0, 6, 0]),
            api.line([0, 6, 0], [0, 0, 0]),
        ],
        closed=True,
    )
    assert _shape(point_wire).isClosed()
    assert _shape(edge_wire).isClosed()

    outer = _closed_wire(api, [[0, 0, 0], [12, 0, 0], [12, 10, 0], [0, 10, 0]])
    hole = _closed_wire(api, [[3, 3, 0], [9, 3, 0], [9, 7, 0], [3, 7, 0]])
    face = api.face(outer, holes=[hole])
    face_shape = _shape(face)
    assert len(face_shape.Faces) == 1
    assert len(face_shape.Wires) == 2

    cube_faces = [
        _face(api, [[0, 0, 0], [4, 0, 0], [4, 4, 0], [0, 4, 0]]),
        _face(api, [[0, 0, 4], [0, 4, 4], [4, 4, 4], [4, 0, 4]]),
        _face(api, [[0, 0, 0], [0, 0, 4], [4, 0, 4], [4, 0, 0]]),
        _face(api, [[4, 0, 0], [4, 0, 4], [4, 4, 4], [4, 4, 0]]),
        _face(api, [[4, 4, 0], [4, 4, 4], [0, 4, 4], [0, 4, 0]]),
        _face(api, [[0, 4, 0], [0, 4, 4], [0, 0, 4], [0, 0, 0]]),
    ]
    shell = api.shell(cube_faces)
    shell_shape = _shape(shell)
    assert len(shell_shape.Shells) == 1
    sewn_shell = _shape(api.sew(cube_faces, output_type="shell"))
    assert sewn_shell.ShapeType == "Shell"
    assert len(sewn_shell.Faces) == 6
    shapes["sew"] = sewn_shell
    sewn_solid = _shape(api.sew(cube_faces, output_type="solid"))
    assert sewn_solid.ShapeType == "Solid"
    _near(sewn_solid.Volume, 64.0)
    open_shell = api.shell(cube_faces[:5])
    filled_shell_offset = api.offset(open_shell, 0.2, fill=True)
    assert filled_shell_offset.output_type == "solid"
    assert _shape(filled_shell_offset).ShapeType == "Solid"
    solid = api.solid(shell)
    assert len(_shape(solid).Solids) == 1
    compound = api.compound([solid, api.sphere(1, center=[8, 0, 0])])
    assert _shape(compound).ShapeType == "Compound"
    selected_face = api.subshape(api.box(4, 5, 6), "face", 1)
    assert selected_face.output_type == "face"
    assert _shape(selected_face).ShapeType == "Face"
    selected_edge = api.subshape(api.box(4, 5, 6), "edge", 12)
    assert selected_edge.output_type == "edge"
    assert _shape(selected_edge).ShapeType == "Edge"

    profile = _face(api, [[0, 0, 0], [5, 0, 0], [5, 3, 0], [0, 3, 0]])
    extruded = api.extrude(profile, [0, 0, 7])
    assert extruded.output_type == "solid"
    assert len(_shape(extruded).Solids) == 1
    edge_extrusion = api.extrude(api.line([0, 0, 0], [5, 0, 0]), [0, 0, 3])
    assert edge_extrusion.output_type == "face"
    assert _shape(edge_extrusion).ShapeType == "Face"
    wire_extrusion = api.extrude(api.wire([[0, 0, 0], [5, 0, 0], [5, 3, 0]]), [0, 0, 3])
    assert wire_extrusion.output_type == "shell"
    assert _shape(wire_extrusion).ShapeType == "Shell"
    face_offset = api.offset(profile, 0.25)
    assert face_offset.output_type == "shell"
    face_offset_shape = _shape(face_offset)
    assert face_offset_shape.ShapeType == "Shell", face_offset_shape.ShapeType
    planar_face_offset = api.offset2d(profile, 0.25, fill=True)
    assert planar_face_offset.output_type == "face"
    planar_face_offset_shape = _shape(planar_face_offset)
    assert planar_face_offset_shape.ShapeType == "Face", planar_face_offset_shape.ShapeType
    open_wire = api.wire([[0, 0, 0], [6, 0, 0], [6, 4, 0]])
    wire_offset = api.offset2d(open_wire, 0.25, open_result=True)
    assert wire_offset.output_type == "wire"
    wire_offset_shape = _shape(wire_offset)
    assert wire_offset_shape.ShapeType == "Wire", wire_offset_shape.ShapeType
    revolve_profile = _face(api, [[2, 0, 0], [4, 0, 0], [4, 0, 5], [2, 0, 5]])
    revolved = api.revolve(revolve_profile, [0, 0, 0], [0, 0, 1])
    assert revolved.output_type == "solid"
    assert len(_shape(revolved).Solids) == 1
    edge_revolution = api.revolve(api.line([3, 0, 0], [3, 0, 4]), [0, 0, 0], [0, 0, 1], angle=180)
    assert edge_revolution.output_type == "face"
    assert _shape(edge_revolution).ShapeType == "Face"
    lower = _closed_wire(api, [[0, 0, 0], [6, 0, 0], [6, 4, 0], [0, 4, 0]])
    upper = _closed_wire(api, [[1, 1, 6], [5, 1, 6], [5, 3, 6], [1, 3, 6]])
    loft = api.loft(
        [lower, upper],
        solid=True,
        max_degree=3,
        output_type="solid",
    )
    assert len(_shape(loft).Solids) == 1
    sweep_profile = api.wire([api.circle(1.5)])
    sweep_path = api.wire([[0, 0, 0], [0, 0, 8]])
    sweep = api.sweep(sweep_profile, sweep_path, solid=True, output_type="solid")
    assert len(_shape(sweep).Solids) == 1
    upper_sweep_profile = api.wire([api.circle(0.75, center=[0, 0, 8])])
    multi_profile_sweep = api.sweep(
        [sweep_profile, upper_sweep_profile],
        sweep_path,
        solid=True,
        output_type="solid",
    )
    multi_profile_sweep_shape = _shape(multi_profile_sweep)
    assert multi_profile_sweep_shape.ShapeType == "Solid"
    assert len(multi_profile_sweep_shape.Solids) == 1
    _near(multi_profile_sweep_shape.BoundBox.ZLength, 8.0)
    shapes["multi_profile_sweep"] = multi_profile_sweep_shape
    ruled = api.ruled_surface(
        api.line([0, 0, 0], [8, 0, 0]),
        api.line([0, 3, 4], [8, 3, 4]),
    )
    assert ruled.output_type == "face"
    assert _shape(ruled).ShapeType == "Face"
    filled = api.filled_surface([api.subshape(api.box(8, 6, 2), "face", 1)])
    assert filled.output_type == "face"
    assert _shape(filled).ShapeType == "Face"

    left = api.box(8, 8, 8)
    right = api.box(8, 8, 8, origin=[4, 0, 0])
    fused = api.fuse([left, right])
    fused_shape = _shape(fused)
    assert len(fused_shape.Solids) == 1
    cut = api.cut(left, api.cylinder(2, 8, origin=[4, 4, 0]))
    assert len(_shape(cut).Solids) == 1
    common = api.common([left, right])
    assert len(_shape(common).Solids) == 1
    section = api.section(left, right)
    assert len(_shape(section).Edges) >= 1
    general_fuse_diagnostics = {}
    fragments = build_part_shape(
        api.general_fuse([left, right], tolerance=1.0e-7).to_payload(),
        diagnostics=general_fuse_diagnostics,
    )
    assert fragments.ShapeType == "Compound"
    assert len(fragments.Solids) == 3
    assert general_fuse_diagnostics == {
        "general_fuse": {
            "input_count": 2,
            "source_fragment_counts": [2, 2],
            "result_solid_count": 3,
            "result_face_count": len(fragments.Faces),
        }
    }
    bounded_facts = part_shape_facts(fragments, max_subelements=2)
    assert bounded_facts["subelement_detail_limit"] == 2
    assert len(bounded_facts["face_details"]) == 2
    assert len(bounded_facts["edge_details"]) == 2
    assert bounded_facts["subelement_details_truncated"] is True
    shapes["general_fuse"] = fragments
    fuzzy_union = _shape(
        api.fuse(
            [api.box(1, 1, 1), api.box(1, 1, 1, origin=[1.0 + 1.0e-6, 0, 0])],
            tolerance=1.0e-5,
            output_type="solid",
        )
    )
    assert fuzzy_union.ShapeType == "Solid"
    multi_cut = _shape(
        api.cut(
            api.box(10, 10, 10),
            [
                api.cylinder(1, 10, origin=[3, 3, 0]),
                api.cylinder(1, 10, origin=[7, 7, 0]),
            ],
            tolerance=1.0e-7,
        )
    )
    assert multi_cut.ShapeType == "Solid"
    sliced = api.slice(api.box(8, 8, 8), [0, 0, 1], [2, 6])
    sliced_shape = _shape(sliced)
    assert sliced_shape.ShapeType == "Compound"
    assert len(sliced_shape.Wires) == 2

    bossed = api.fuse(
        [
            api.box(10, 10, 5),
            api.cylinder(2, 3, origin=[5, 5, 5]),
        ]
    )
    bossed_shape = _shape(bossed)
    feature_face_indices = [
        index
        for index, face_item in enumerate(bossed_shape.Faces, start=1)
        if face_item.CenterOfMass.z > 5.0 + 1.0e-7
    ]
    assert len(feature_face_indices) == 2, feature_face_indices
    defeatured = api.defeature(bossed, feature_face_indices)
    defeatured_shape = _shape(defeatured)
    assert defeatured_shape.ShapeType == "Solid"
    _near(defeatured_shape.Volume, 500)
    nurbs = api.to_nurbs(api.cylinder(3, 5))
    assert nurbs.output_type == "solid"
    assert _shape(nurbs).ShapeType == "Solid"
    reversed_solid = api.reverse(api.box(2, 3, 4))
    reversed_shape = _shape(reversed_solid)
    assert reversed_shape.ShapeType == "Solid"
    repaired_shape = _shape(api.repair(api.box(2, 3, 4)))
    assert repaired_shape.ShapeType == "Solid"
    assert repaired_shape.isValid()
    shapes["repair"] = repaired_shape

    fillet = api.fillet(api.box(10, 10, 10), 1, edges=[1, 2, 3, 4])
    assert len(_shape(fillet).Solids) == 1
    chamfer = api.chamfer(api.box(10, 10, 10), 1, edges=[1, 2, 3, 4])
    assert len(_shape(chamfer).Solids) == 1
    offset = api.offset(api.box(4, 4, 4), 0.5, output_type="solid")
    assert offset.output_type == "solid"
    assert len(_shape(offset).Solids) == 1
    thickened = api.thicken(api.box(8, 8, 8), [6], 1)
    assert len(_shape(thickened).Solids) == 1

    transformed = api.transform(
        api.box(2, 3, 4, origin=[10, 10, 10]),
        translation=[1, 2, 3],
        scale=[2.0, 3.0, 0.5],
        pivot=[10, 10, 10],
    )
    transformed_shape = _shape(transformed)
    assert len(transformed_shape.Solids) == 1
    _near(transformed_shape.BoundBox.XMin, 11)
    _near(transformed_shape.BoundBox.YMin, 12)
    _near(transformed_shape.BoundBox.ZMin, 13)
    _near(transformed_shape.BoundBox.XLength, 4)
    _near(transformed_shape.BoundBox.YLength, 9)
    _near(transformed_shape.BoundBox.ZLength, 2)
    rotated_shape = _shape(
        api.transform(
            api.box(2, 4, 1),
            rotation_axis=[0, 0, 1],
            rotation_degrees=90,
            pivot=[0, 0, 0],
        )
    )
    _near(rotated_shape.BoundBox.XMin, -4)
    _near(rotated_shape.BoundBox.XMax, 0)
    _near(rotated_shape.BoundBox.YMin, 0)
    _near(rotated_shape.BoundBox.YMax, 2)
    mirrored = api.mirror(api.box(2, 3, 4, origin=[2, 0, 0]), [0, 0, 0], [1, 0, 0])
    mirrored_shape = _shape(mirrored)
    assert len(mirrored_shape.Solids) == 1
    _near(mirrored_shape.BoundBox.XMin, -4)
    _near(mirrored_shape.BoundBox.XMax, -2)
    projection_target = api.plane(20, 20, origin=[-10, -10, 0])
    parallel_projection = _shape(
        api.project(
            projection_target,
            api.circle(4, center=[0, 0, 5]),
            [0, 0, -1],
            mode="parallel",
        )
    )
    assert parallel_projection.ShapeType == "Wire"
    _near(parallel_projection.BoundBox.ZMin, 0.0)
    shapes["project_parallel"] = parallel_projection
    perspective_projection = _shape(
        api.project(
            projection_target,
            api.circle(2, center=[0, 0, 5]),
            [0, 0, 10],
            mode="perspective",
        )
    )
    assert perspective_projection.ShapeType == "Wire"
    _near(perspective_projection.BoundBox.XLength, 8.0)
    shapes["project_perspective"] = perspective_projection
    refined = api.refine(api.fuse([api.box(4, 4, 4), api.box(4, 4, 4, origin=[4, 0, 0])]))
    assert len(_shape(refined).Solids) == 1

    return {
        name: {"shape_type": shape.ShapeType, "edges": len(shape.Edges)}
        for name, shape in shapes.items()
    }


class _Service:
    def __init__(self, document, project_root: Path):
        self.document = document
        self.project_root = project_root

    def _active_document(self):
        return self.document

    def active_workbench_name(self):
        return "PartWorkbench"

    def modeling_engine(self):
        return "vibescript"

    def provider_document_revision(self):
        return "part-integration-revision"

    def project_scope_snapshot(self):
        return {"root": str(self.project_root)}

    def provider_working_set(self):
        target = self.document.getObject("NativeSeed")
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

    def selection_summary(self):
        return {"selection": []}


def _run_candidate(captured, service):
    prepared = prepare_candidate(captured)
    if prepared.get("reference_requirements") and not prepared.get("finalized"):
        prepared = finalize_candidate(prepared, capture_reference_inputs(service, prepared))
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    return prepared, publication, accept_candidate(prepared, publication)


def _candidate_capture(base, *, operation, tool_name, arguments):
    return {
        **base,
        "operation": operation,
        "tool_name": tool_name,
        "arguments": arguments,
    }


def _exercise_isolated_lifecycle(root: Path, pack) -> dict:
    import FreeCAD as App
    import Part

    document = App.newDocument("VibeScriptPartProduction")
    native_seed = document.addObject("Part::Feature", "NativeSeed")
    native_seed.Label = "Native seed"
    native_seed.Shape = Part.makeBox(30, 20, 12)
    reference = {
        "document_uid": str(document.Uid),
        "object_name": str(native_seed.Name),
    }
    source = (
        "seed = api.from_object(inputs['base'], output_type='solid')\n"
        "base = api.transform(seed, scale=[inputs['length']/30,1,1])\n"
        "bore = api.cylinder(3, 12, origin=[inputs['length']/2, 10, 0])\n"
        "housing = api.fillet(api.cut(base, bore), 1, edges=[1,2,3,4], label='Housing')\n"
        "lower = api.wire([[0,0,0],[8,0,0],[8,5,0],[0,5,0]], closed=True)\n"
        "upper = api.wire([[1,1,8],[7,1,8],[7,4,8],[1,4,8]], closed=True)\n"
        "loft = api.loft([lower, upper], solid=True, output_type='solid', label='Loft')\n"
        "section = api.general_fuse([base, api.box(10,30,4, origin=[5,-5,4])], "
        "tolerance=1e-7, label='Section')\n"
        "result = {'Housing': housing, 'Loft': loft, 'Section': section}\n"
    )
    base_capture = {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "part-integration-revision",
        "document_objects": [
            {
                "name": str(native_seed.Name),
                "label": str(native_seed.Label),
                "type_id": str(native_seed.TypeId),
            }
        ],
        "surface": resolve_modeling_surface("PartWorkbench", "vibescript").summary(),
        "freecad_home": str(Path(App.getHomePath()).resolve()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }
    create_capture = _candidate_capture(
        base_capture,
        operation="create_program",
        tool_name="vibescript.part.create_program",
        arguments={
            "program_name": "Production Part",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {
                    "length": {"type": "number", "exclusiveMinimum": 10},
                    "base": {
                        "type": "object",
                        "x-vibecad-reference": True,
                        "properties": {
                            "document_uid": {"type": "string", "minLength": 1},
                            "object_name": {"type": "string", "minLength": 1},
                        },
                        "required": ["document_uid", "object_name"],
                        "additionalProperties": False,
                    },
                },
                "required": ["length", "base"],
                "additionalProperties": False,
            },
            "inputs": {"length": 30.0, "base": reference},
            "expected_outputs": [
                {"name": "Housing", "type": "solid"},
                {"name": "Loft", "type": "solid"},
                {"name": "Section", "type": "compound"},
            ],
        },
    )
    service = _Service(document, root)
    prepared, _publication, accepted = _run_candidate(create_capture, service)
    assert len(prepared["resolved_references"]) == 1
    assert prepared["resolved_references"][0]["object_name"] == native_seed.Name
    assert prepared["resolved_references"][0]["facts"]["volume_mm3"] == 7200.0
    identities = {
        name: details["object_name"] for name, details in accepted["live_outputs"].items()
    }
    assert set(identities) == {"Housing", "Loft", "Section"}
    assert all(document.getObject(name) is not None for name in identities.values())
    for object_name in identities.values():
        output = document.getObject(object_name)
        assert list(getattr(output, PROP_INPUT_OBJECTS)) == [native_seed]
        assert output.VibeCADDerivedState == "accepted"
    housing_facts = accepted["live_outputs"]["Housing"]["facts"]
    assert housing_facts["shape_type"] == "Solid", housing_facts
    assert housing_facts["solids"] == 1
    assert housing_facts["volume_mm3"] > 0.0
    assert len(housing_facts["bounds_mm"]["size"]) == 3
    assert [item["index"] for item in housing_facts["face_details"]] == list(
        range(1, housing_facts["faces"] + 1)
    )
    assert [item["index"] for item in housing_facts["edge_details"]] == list(
        range(1, housing_facts["edges"] + 1)
    )
    assert all(item["surface_type"] for item in housing_facts["face_details"])
    assert all(item["curve_type"] for item in housing_facts["edge_details"])
    section_diagnostics = accepted["live_outputs"]["Section"]["operation_diagnostics"]
    assert section_diagnostics["general_fuse"]["input_count"] == 2
    assert len(section_diagnostics["general_fuse"]["source_fragment_counts"]) == 2

    context_snapshot = domain_context_snapshot(service, "part")
    assert context_snapshot["part_document_shapes"]["objects"][0]["name"] == "NativeSeed"
    context = complete_domain_context(context_snapshot)
    assert context["document"] == {
        "name": str(document.Name),
        "uid": str(document.Uid),
    }
    native_context = next(
        item for item in context["document_shapes"]["objects"] if item["name"] == "NativeSeed"
    )
    assert native_context["reference"] == reference
    assert native_context["facts"]["shape_type"] == "Solid"
    assert native_context["facts"]["volume_mm3"] == 7200.0
    assert native_context["facts"]["face_details"][0]["index"] == 1
    assert native_context["facts"]["edge_details"][0]["index"] == 1
    assert "_detached_shape" not in native_context
    assert context["document_shapes"]["object_limit"] == 24

    inspected = complete_inspection(
        {
            "pack": pack,
            "program_id": prepared["program_id"],
            "project_root": str(root),
            "live_programs": [],
        }
    )
    assert inspected["ok"] is True
    assert inspected["program"]["accepted_revision"] == accepted["accepted_revision"]
    assert inspected["program"]["live_outputs"]["Housing"]["facts"] == housing_facts

    failed_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.part.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "replacements": [{"old": "edges=[1,2,3,4]", "new": "edges=[99]"}],
        },
    )
    failed_prepared = prepare_candidate(failed_capture)
    failed_prepared = finalize_candidate(
        failed_prepared,
        capture_reference_inputs(service, failed_prepared),
    )
    failed_execution = execute_candidate(failed_prepared, cancellation_check=None)
    assert failed_execution.get("ok") is False
    assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
    assert failed_execution["failure_stage"] == "external_process"
    assert failed_execution["domain_failure_stage"] == "part_topology_selection"
    assert "api.fillet" in failed_execution["error"]
    assert "outside 1.." in failed_execution["error"]
    failure_details = failed_execution["observed"]["details"]
    assert failure_details["stage"] == "part_topology_selection"
    assert failure_details["operation"] == "fillet"
    assert failure_details["parameter"] == "edges"
    assert failed_execution["retry"]["same_call"] is False
    assert failed_execution["retry"]["required_changes"] == [
        failure_details["correction"]
    ]
    assert "latest accepted" in failure_details["correction"]
    retain_candidate(failed_prepared, status="failed", failure=failed_execution)
    failed_inspection = complete_inspection(
        {
            "pack": pack,
            "program_id": prepared["program_id"],
            "project_root": str(root),
            "live_programs": [],
        }
    )
    assert failed_inspection["program"]["working_revision"] == failed_prepared["revision"]
    assert failed_inspection["program"]["accepted_revision"] == accepted["accepted_revision"]
    assert failed_inspection["program"]["latest_candidate"]["status"] == "failed"
    for object_name in identities.values():
        obj = document.getObject(object_name)
        assert obj is not None
        assert obj.VibeCADVibeScriptRevision == accepted["accepted_revision"]

    edit_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.part.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": failed_prepared["revision"],
            "replacements": [
                {"old": "edges=[99]", "new": "edges=[1,2,3,4]"},
                {
                    "old": "base = api.transform(seed, scale=[inputs['length']/30,1,1])",
                    "new": "base = api.transform(seed, scale=[inputs['length']/30,1.2,1])",
                },
            ],
        },
    )
    _edited, edit_publication, accepted = _run_candidate(edit_capture, service)
    assert edit_publication["created_objects"] == []
    assert {
        name: details["object_name"] for name, details in accepted["live_outputs"].items()
    } == identities
    inputs_capture = _candidate_capture(
        base_capture,
        operation="set_inputs",
        tool_name="vibescript.part.set_inputs",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "patch": {"length": 36.0},
        },
    )
    _inputs_prepared, inputs_publication, accepted = _run_candidate(inputs_capture, service)
    assert inputs_publication["created_objects"] == []
    assert {
        name: details["object_name"] for name, details in accepted["live_outputs"].items()
    } == identities
    previous_reference_digest = _inputs_prepared["resolved_references"][0]["brep_sha256"]

    # The source/input contract is unchanged, but a new detached source shape
    # must produce a new guarded revision and regenerate stable live identities.
    native_seed.Shape = Part.makeBox(30, 22, 12)
    mark_programs_stale_from_source(native_seed, "Shape")
    for object_name in identities.values():
        output = document.getObject(object_name)
        assert output.VibeCADDerivedState == "stale"
        assert "NativeSeed.Shape" in output.VibeCADStaleReason
    dependency_capture = _candidate_capture(
        base_capture,
        operation="set_inputs",
        tool_name="vibescript.part.set_inputs",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "patch": {"length": 36.0},
        },
    )
    dependency_prepared, dependency_publication, accepted = _run_candidate(
        dependency_capture,
        service,
    )
    assert dependency_publication["created_objects"] == []
    assert dependency_prepared["resolved_references"][0]["brep_sha256"] != (
        previous_reference_digest
    )
    assert {
        name: details["object_name"] for name, details in accepted["live_outputs"].items()
    } == identities
    for object_name in identities.values():
        assert document.getObject(object_name).VibeCADDerivedState == "accepted"

    housing_object = document.getObject(identities["Housing"])
    assert housing_object is not None
    unsafe_consumer = document.addObject("Part::Feature", "UnsafeFaceConsumer")
    unsafe_consumer.addProperty("App::PropertyLinkSub", "SourceFace")
    unsafe_consumer.SourceFace = (housing_object, ["Face1"])
    unsafe_capture = _candidate_capture(
        base_capture,
        operation="set_inputs",
        tool_name="vibescript.part.set_inputs",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "patch": {"length": 37.0},
        },
    )
    unsafe_prepared = prepare_candidate(unsafe_capture)
    unsafe_prepared = finalize_candidate(
        unsafe_prepared,
        capture_reference_inputs(service, unsafe_prepared),
    )
    unsafe_execution = execute_candidate(unsafe_prepared, cancellation_check=None)
    assert unsafe_execution.get("ok") is True, unsafe_execution
    unsafe_validated = validate_candidate(unsafe_prepared, unsafe_execution)
    retain_candidate(unsafe_prepared, status="validated")
    try:
        publish_candidate(service, unsafe_prepared, unsafe_validated)
    except RuntimeError as exc:
        assert "Face/Edge/Vertex references" in str(exc)
        assert "UnsafeFaceConsumer" in str(exc)
    else:
        raise AssertionError("A transient Face1 consumer was silently accepted.")
    retain_candidate(
        unsafe_prepared,
        status="publication_failed",
        failure={
            "failure_code": "DOMAIN_PUBLICATION_FAILED",
            "failure_stage": "native_call",
            "error": "unsafe subelement consumer",
        },
    )
    assert housing_object.VibeCADVibeScriptRevision == accepted["accepted_revision"]
    document.removeObject(unsafe_consumer.Name)

    whole_consumer = document.addObject("App::FeaturePython", "WholeObjectConsumer")
    whole_consumer.addProperty("App::PropertyLink", "SourceObject")
    whole_consumer.SourceObject = housing_object
    housing_object.addProperty(
        "App::PropertyString",
        "HumanMaterialCard",
        "Material",
        "Human-authored material assignment that VibeScript must preserve.",
    )
    housing_object.HumanMaterialCard = "urn:material:human-assigned-steel"
    engineering_consumer_specs = (
        ("Fem::FeaturePython", "FemConsumer", True),
        ("Path::FeaturePython", "CamConsumer", True),
        ("TechDraw::DrawViewPart", "TechDrawConsumer", True),
        ("Robot::RobotObject", "RobotConsumer", True),
        ("Inspection::Feature", "InspectionConsumer", True),
        ("Assembly::AssemblyObject", "AssemblyConsumer", False),
    )
    engineering_consumers = []
    for type_id, name, _should_be_stale in engineering_consumer_specs:
        consumer = document.addObject(type_id, name)
        consumer.addProperty("App::PropertyLink", "VibeCADTestSource")
        consumer.VibeCADTestSource = housing_object
        engineering_consumers.append(consumer)
    recovery_capture = _candidate_capture(
        base_capture,
        operation="set_inputs",
        tool_name="vibescript.part.set_inputs",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": unsafe_prepared["revision"],
            "patch": {"length": 38.0},
        },
    )
    _recovery_prepared, recovery_publication, accepted = _run_candidate(
        recovery_capture,
        service,
    )
    assert whole_consumer.SourceObject is housing_object
    assert "WholeObjectConsumer" in recovery_publication["downstream_references"]["touched"]
    assert housing_object.HumanMaterialCard == "urn:material:human-assigned-steel"
    stale_names = set(recovery_publication["downstream_references"]["marked_stale"])
    touched_names = set(recovery_publication["downstream_references"]["touched"])
    for consumer, (_type_id, name, should_be_stale) in zip(
        engineering_consumers,
        engineering_consumer_specs,
    ):
        assert consumer.Name == name
        assert consumer.VibeCADTestSource is housing_object
        assert name in touched_names
        if should_be_stale:
            assert name in stale_names
            assert consumer.VibeCADDerivedState == "stale"
            assert "regenerate this derived result" in consumer.VibeCADStaleReason
        else:
            assert name not in stale_names

    reconfigured_source = (
        "seed = api.from_object(inputs['base'], output_type='solid')\n"
        "base = api.transform(seed, scale=[inputs['length']/30,1.2,1])\n"
        "bore = api.cylinder(3, 12, origin=[inputs['length']/2, 10, 0])\n"
        "housing = api.fillet(api.cut(base, bore), 1, edges=[1,2,3,4], label='Housing')\n"
        "lower = api.wire([[0,0,0],[8,0,0],[8,5,0],[0,5,0]], closed=True)\n"
        "upper = api.wire([[1,1,8],[7,1,8],[7,4,8],[1,4,8]], closed=True)\n"
        "loft = api.loft([lower, upper], solid=True, output_type='solid', label='Loft')\n"
        "result = {'Housing': housing, 'Loft': loft}\n"
    )
    reconfigure_capture = _candidate_capture(
        base_capture,
        operation="reconfigure_program",
        tool_name="vibescript.part.reconfigure_program",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "source": reconfigured_source,
            "input_schema": {
                "type": "object",
                "properties": {
                    "length": {"type": "number", "exclusiveMinimum": 10},
                    "base": {
                        "type": "object",
                        "x-vibecad-reference": True,
                        "properties": {
                            "document_uid": {"type": "string", "minLength": 1},
                            "object_name": {"type": "string", "minLength": 1},
                        },
                        "required": ["document_uid", "object_name"],
                        "additionalProperties": False,
                    },
                },
                "required": ["length", "base"],
                "additionalProperties": False,
            },
            "inputs": {"length": 36.0, "base": reference},
            "expected_outputs": [
                {"name": "Housing", "type": "solid"},
                {"name": "Loft", "type": "solid"},
            ],
        },
    )
    _reconfigured, reconfigure_publication, accepted = _run_candidate(reconfigure_capture, service)
    assert reconfigure_publication["created_objects"] == []
    assert reconfigure_publication["retired_objects"] == [identities["Section"]]
    assert document.getObject(identities["Section"]) is None
    remaining_identities = {
        name: details["object_name"] for name, details in accepted["live_outputs"].items()
    }
    assert remaining_identities == {
        "Housing": identities["Housing"],
        "Loft": identities["Loft"],
    }

    path = root / "part-production.FCStd"
    document.saveAs(str(path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(path))
    assert reopened is not None
    assert all(reopened.getObject(name) is not None for name in remaining_identities.values())
    assert reopened.getObject(identities["Section"]) is None
    reopened_seed = reopened.getObject("NativeSeed")
    assert reopened_seed is not None
    for object_name in remaining_identities.values():
        output = reopened.getObject(object_name)
        assert list(getattr(output, PROP_INPUT_OBJECTS)) == [reopened_seed]
        assert output.VibeCADDerivedState == "accepted"
    reopened_housing = reopened.getObject(remaining_identities["Housing"])
    assert reopened_housing.HumanMaterialCard == "urn:material:human-assigned-steel"
    for _type_id, name, should_be_stale in engineering_consumer_specs:
        consumer = reopened.getObject(name)
        assert consumer is not None
        assert consumer.VibeCADTestSource is reopened_housing
        if should_be_stale:
            assert consumer.VibeCADDerivedState == "stale"

    service.document = reopened
    reopened_capture = {
        **base_capture,
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
        "document_objects": [
            {
                "name": str(reopened_seed.Name),
                "label": str(reopened_seed.Label),
                "type_id": str(reopened_seed.TypeId),
            }
        ],
    }
    regenerate_capture = _candidate_capture(
        reopened_capture,
        operation="set_inputs",
        tool_name="vibescript.part.set_inputs",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "patch": {"length": 39.0},
        },
    )
    _reopened_prepared, reopened_publication, accepted = _run_candidate(
        regenerate_capture,
        service,
    )
    assert reopened_publication["created_objects"] == []
    assert {
        name: details["object_name"] for name, details in accepted["live_outputs"].items()
    } == remaining_identities
    reopened_consumer = reopened.getObject("WholeObjectConsumer")
    assert reopened_consumer is not None
    assert reopened_consumer.SourceObject is reopened.getObject(remaining_identities["Housing"])
    assert "WholeObjectConsumer" in reopened_publication["downstream_references"]["touched"]
    assert reopened.getObject(remaining_identities["Housing"]).HumanMaterialCard == (
        "urn:material:human-assigned-steel"
    )
    for _type_id, name, should_be_stale in engineering_consumer_specs:
        consumer = reopened.getObject(name)
        assert consumer.VibeCADTestSource is reopened.getObject(remaining_identities["Housing"])
        if should_be_stale:
            assert name in reopened_publication["downstream_references"]["marked_stale"]

    delete_capture = {
        **reopened_capture,
        "operation": "delete_program",
        "tool_name": "vibescript.part.delete_program",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "reason": "Complete lifecycle integration cleanup.",
        },
    }
    deletion = prepare_delete(delete_capture)
    from VibeCADVibeScriptDomains import get_domain_adapter

    domain_adapter = get_domain_adapter("part")
    assert domain_adapter is not None
    try:
        domain_adapter.delete(service, deletion, deletion["manifest"])
    except RuntimeError as exc:
        assert "Cannot delete" in str(exc)
        assert "WholeObjectConsumer" in str(exc)
    else:
        raise AssertionError("Deletion ignored an external whole-object link.")
    restore_prepared_delete(deletion)
    reopened.removeObject("WholeObjectConsumer")
    for _type_id, name, _should_be_stale in reversed(engineering_consumer_specs):
        reopened.removeObject(name)
    deletion = prepare_delete(delete_capture)
    deletion_publication = domain_adapter.delete(service, deletion, deletion["manifest"])
    deleted = finish_delete(deletion, deletion_publication)
    assert deleted["artifacts_deleted"] is True
    assert all(reopened.getObject(name) is None for name in remaining_identities.values())
    assert not Path(deletion["program_directory"]).exists()
    App.closeDocument(reopened.Name)
    return {
        "created": identities,
        "stable_after_edit_inputs_and_reconfigure": remaining_identities,
        "retired": identities["Section"],
        "deleted": sorted(item["object_name"] for item in deleted["deleted_objects"]),
    }


def main() -> int:
    pack = get_vibescript_pack("PartWorkbench")
    assert pack is not None and pack.production_ready
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    signatures = {name: str(inspect.signature(getattr(api, name))) for name in api.exported_names}
    assert all(
        "*args" not in signature and "**properties" not in signature
        for signature in signatures.values()
    )
    try:
        api.box(-1, 2, 3)
    except ValueError as exc:
        assert "api.box" in str(exc) and "length" in str(exc)
    else:
        raise AssertionError("Invalid Part dimensions were accepted.")
    try:
        _shape(api.fillet(api.box(2, 2, 2), 100, edges=[99]))
    except (ValueError, PartOperationError) as exc:
        assert "api.fillet" in str(exc) and "1..12" in str(exc)
    else:
        raise AssertionError("Invalid Part edge selection was accepted.")

    operation_facts = _exercise_every_export(api)
    root = Path(tempfile.mkdtemp(prefix="vibecad-part-production-"))
    try:
        identities = _exercise_isolated_lifecycle(root, pack)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(
        json.dumps(
            {
                "ok": True,
                "integration": "part_vibescript_api",
                "export_count": len(api.exported_names),
                "exports": list(api.exported_names),
                "operation_facts": operation_facts,
                "stable_outputs": identities,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
