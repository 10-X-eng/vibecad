# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD integration coverage for Part Design VibeScript v2."""

from __future__ import annotations

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
from VibeCADReferenceContracts import resolve_interface  # noqa: E402
from VibeCADScriptedPublication import (  # noqa: E402
    PROP_REVISION as PROP_PUBLISHED_REVISION,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    accept_candidate,
    complete_inspection,
    execute_candidate,
    finish_delete,
    prepare_candidate,
    prepare_delete,
    restore_prepared_delete,
    retain_candidate,
    validate_candidate,
)
from VibeCADVibeScriptDomains import (  # noqa: E402
    get_domain_adapter,
    get_vibescript_pack,
)
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    PROP_OUTPUT_TYPE,
    publish_candidate,
)
from vibescript_domain_api import create_domain_api  # noqa: E402
from vibescript_partdesign_worker import (  # noqa: E402
    PartDesignCandidateError,
    validate_and_build_partdesign,
)


class _Service:
    def __init__(self, document, project_root: Path) -> None:
        self.document = document
        self.project_root = project_root

    def _active_document(self):
        return self.document

    @staticmethod
    def active_workbench_name() -> str:
        return "PartDesignWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "partdesign-v2-integration-revision"

    def project_scope_snapshot(self) -> dict:
        return {"root": str(self.project_root)}

    def provider_working_set(self) -> dict:
        publications = [
            obj
            for obj in self.document.Objects
            if "VibeCADScriptedOutputKey" in list(obj.PropertiesList)
        ]
        return {
            "target_count": len(publications),
            "targets": [
                {
                    "name": str(obj.Name),
                    "label": str(obj.Label),
                    "type_id": str(obj.TypeId),
                }
                for obj in publications
            ],
        }

    @staticmethod
    def selection_summary() -> dict:
        return {"selection": []}

    @staticmethod
    def _partdesign_body_for_feature(_obj):
        return None


def _capture(base: dict, *, operation: str, arguments: dict) -> dict:
    return {
        **base,
        "operation": operation,
        "tool_name": f"vibescript.partdesign.{operation}",
        "arguments": arguments,
    }


def _run_candidate(captured: dict, service: _Service):
    prepared = prepare_candidate(captured)
    assert prepared["finalized"] is True
    assert prepared["reference_requirements"] == []
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    return prepared, publication, accepted


def _rectangle(api, x0: float, y0: float, x1: float, y1: float, **kwargs):
    return api.sketch(
        [
            api.line([x0, y0], [x1, y0]),
            api.line([x1, y0], [x1, y1]),
            api.line([x1, y1], [x0, y1]),
            api.line([x0, y1], [x0, y0]),
        ],
        **kwargs,
    )


def _fully_constrained_rectangle(api):
    bottom = api.line([0, 0], [10, 0], name="Bottom")
    right = api.line([10, 0], [10, 8], name="Right")
    top = api.line([10, 8], [0, 8], name="Top")
    left = api.line([0, 8], [0, 0], name="Left")
    constraints = [
        api.constraint(
            "coincident",
            [
                {"geometry": bottom, "point": "end"},
                {"geometry": right, "point": "start"},
            ],
        ),
        api.constraint(
            "coincident",
            [
                {"geometry": right, "point": "end"},
                {"geometry": top, "point": "start"},
            ],
        ),
        api.constraint(
            "coincident",
            [
                {"geometry": top, "point": "end"},
                {"geometry": left, "point": "start"},
            ],
        ),
        api.constraint(
            "coincident",
            [
                {"geometry": left, "point": "end"},
                {"geometry": bottom, "point": "start"},
            ],
        ),
        api.constraint("horizontal", [bottom]),
        api.constraint("horizontal", [top]),
        api.constraint("vertical", [right]),
        api.constraint("vertical", [left]),
        api.constraint("distance", [bottom], value=10, name="Width"),
        api.constraint("distance", [right], value=8, name="Depth"),
        api.constraint(
            "coincident",
            [{"geometry": bottom, "point": "start"}, "origin"],
            name="Anchored",
        ),
    ]
    return api.sketch(
        [bottom, right, top, left],
        constraints,
        require_fully_constrained=True,
        require_closed_profile=True,
    )


def _feature_case(api, case: str):
    if case == "constrained_pad":
        return api.pad(_fully_constrained_rectangle(api), 3)
    if case == "construction_point_pad":
        profile = api.sketch(
            [api.point([0, 0]), api.circle([0, 0], 5)],
            require_closed_profile=True,
        )
        return api.pad(profile, 3)
    if case == "arc_pad":
        profile = api.sketch(
            [
                api.arc([-5, 0], [0, 5], [5, 0]),
                api.line([5, 0], [-5, 0]),
            ]
        )
        return api.pad(profile, 3)
    if case == "ellipse_pad":
        return api.pad(api.sketch([api.ellipse([0, 0], 6, 3)]), 3)
    if case == "bspline_pad":
        profile = api.sketch(
            [
                api.bspline(
                    [[0, 0], [5, 0], [6, 4], [2, 7], [-2, 4]],
                    periodic=True,
                )
            ]
        )
        return api.pad(profile, 3)
    if case == "pocket":
        base = api.pad(api.sketch([api.circle([0, 0], 10)]), 5)
        cut = api.sketch([api.circle([0, 0], 2)], z_offset_mm=5)
        return api.pocket(base, cut, 3)
    if case == "canonical_extrude_add":
        return api.extrude(
            api.sketch([api.circle([0, 0], 5)]),
            4,
            operation="add_material",
        )
    if case == "canonical_extrude_remove":
        base = api.extrude(
            api.sketch([api.circle([0, 0], 5)]),
            4,
            operation="add_material",
        )
        cut = api.sketch([api.circle([0, 0], 2)], z_offset_mm=4)
        return api.extrude(
            cut,
            4,
            operation="remove_material",
            base=base,
        )
    if case == "revolve":
        return api.revolve(_rectangle(api, 2, -2, 4, 2), axis="V")
    if case == "groove":
        base = api.pad(api.sketch([api.circle([0, 0], 10)]), 5)
        cut = _rectangle(api, 8, 1, 12, 4, plane="XZ")
        return api.groove(base, cut, axis="V")
    if case == "loft":
        return api.loft(
            [
                api.sketch([api.circle([0, 0], 5)]),
                api.sketch([api.circle([0, 0], 3)], z_offset_mm=10),
            ]
        )
    if case == "additive_loft":
        base = api.pad(api.sketch([api.circle([0, 0], 6)]), 5)
        return api.loft(
            [
                api.sketch([api.circle([0, 0], 3)], z_offset_mm=5),
                api.sketch([api.circle([0, 0], 2)], z_offset_mm=10),
            ],
            base=base,
        )
    if case == "subtractive_loft":
        base = api.pad(api.sketch([api.circle([0, 0], 10)]), 10)
        return api.loft(
            [
                api.sketch([api.circle([0, 0], 2)]),
                api.sketch([api.circle([0, 0], 4)], z_offset_mm=10),
            ],
            base=base,
            subtractive=True,
        )
    if case in {"additive_sweep", "subtractive_sweep"}:
        profile = api.sketch([api.circle([2, 0], 0.5)])
        path = api.line_3d([2, 0, 0], [2, 0, 5])
        if case == "additive_sweep":
            return api.sweep(profile, path, operation="add_material")
        base = api.extrude(
            api.sketch([api.circle([0, 0], 5)]),
            5,
            operation="add_material",
        )
        return api.sweep(
            profile,
            path,
            operation="remove_material",
            base=base,
        )
    if case in {"additive_helix", "subtractive_helix"}:
        profile = api.sketch([api.circle([3, 0], 0.4)])
        if case == "additive_helix":
            return api.helix(
                profile,
                operation="add_material",
                pitch_mm=2,
                height_mm=6,
                radius_mm=3,
            )
        base = api.extrude(
            api.sketch([api.circle([0, 0], 5)]),
            6,
            operation="add_material",
        )
        return api.helix(
            profile,
            operation="remove_material",
            pitch_mm=2,
            height_mm=6,
            radius_mm=3,
            base=base,
        )
    if case in {"polar_pattern", "mirror"}:
        base = api.pad(api.sketch([api.circle([0, 0], 10)]), 5)
        boss = api.pad(
            api.sketch([api.circle([7, 0], 2)], z_offset_mm=5),
            3,
            base=base,
        )
        if case == "polar_pattern":
            return api.polar_pattern(boss, 4)
        return api.mirror(boss, "YZ")
    if case == "fillet":
        base = api.pad(_rectangle(api, 0, 0, 10, 8), 5)
        return api.fillet(base, {"type": "all_edges"}, 0.5)
    if case == "chamfer":
        base = api.pad(_rectangle(api, 0, 0, 10, 8), 5)
        return api.chamfer(base, {"type": "all_edges"}, 0.5)
    if case == "linear_pattern":
        base = api.extrude(
            _rectangle(api, 0, 0, 2, 2),
            2,
            operation="add_material",
        )
        return api.linear_pattern(
            base,
            3,
            4,
            direction=[1, 0, 0],
            result="union",
        )
    if case == "multi_transform":
        base = api.extrude(
            _rectangle(api, 0, 0, 2, 2),
            2,
            operation="add_material",
        )
        return api.multi_transform(
            base,
            [
                {"type": "translate", "vector": [2, 0, 0]},
                {"type": "translate", "vector": [2, 0, 0]},
            ],
            result="union",
        )
    if case == "thickness":
        base = api.extrude(
            _rectangle(api, 0, 0, 10, 8),
            5,
            operation="add_material",
        )
        top = api.find_subelements(
            element_type="face",
            expected_count=1,
            geometry_type="plane",
            normal=[0, 0, 1],
            min_area_mm2=70,
        )
        return api.thickness(base, top, 0.5, inward=True)
    if case in {"hole", "hole_dimensioned", "hole_countersink", "hole_counterbore"}:
        base = api.extrude(
            api.sketch([api.circle([0, 0], 5)]),
            8,
            operation="add_material",
        )
        locations = api.sketch([api.circle([0, 0], 1)], z_offset_mm=8)
        if case == "hole":
            return api.hole(base, locations, 2, through_all=True)
        if case == "hole_countersink":
            return api.hole(
                base,
                locations,
                2,
                depth_mm=4,
                countersink_diameter_mm=4,
                countersink_angle_degrees=90,
            )
        if case == "hole_counterbore":
            return api.hole(
                base,
                locations,
                2,
                depth_mm=4,
                counterbore_diameter_mm=4,
                counterbore_depth_mm=1,
            )
        return api.hole(base, locations, 2, depth_mm=4)
    if case == "draft":
        base = api.extrude(
            _rectangle(api, -5, -4, 5, 4),
            5,
            operation="add_material",
        )
        side = api.find_subelements(
            element_type="face",
            expected_count=1,
            geometry_type="plane",
            normal=[1, 0, 0],
            min_area_mm2=20,
        )
        return api.draft(base, side, 3, neutral_plane="XY", pull_direction="Z")
    raise AssertionError(f"Unknown Part Design feature case: {case}")


def _exercise_feature_families(root: Path, pack) -> dict[str, dict]:
    import FreeCAD as App

    expected_tips = {
        "constrained_pad": "PartDesign::Pad",
        "construction_point_pad": "PartDesign::Pad",
        "arc_pad": "PartDesign::Pad",
        "ellipse_pad": "PartDesign::Pad",
        "bspline_pad": "PartDesign::Pad",
        "pocket": "PartDesign::Pocket",
        "canonical_extrude_add": "PartDesign::Pad",
        "canonical_extrude_remove": "PartDesign::Pocket",
        "revolve": "PartDesign::Revolution",
        "groove": "PartDesign::Groove",
        "loft": "PartDesign::AdditiveLoft",
        "additive_loft": "PartDesign::AdditiveLoft",
        "subtractive_loft": "PartDesign::SubtractiveLoft",
        "additive_sweep": "PartDesign::Feature",
        "subtractive_sweep": "PartDesign::Feature",
        "additive_helix": "PartDesign::Feature",
        "subtractive_helix": "PartDesign::Feature",
        "polar_pattern": "PartDesign::PolarPattern",
        "mirror": "PartDesign::Mirrored",
        "fillet": "PartDesign::Fillet",
        "chamfer": "PartDesign::Chamfer",
        "linear_pattern": "PartDesign::Feature",
        "multi_transform": "PartDesign::Feature",
        "thickness": "PartDesign::Thickness",
        "hole": "PartDesign::Hole",
        "hole_dimensioned": "PartDesign::Hole",
        "hole_countersink": "PartDesign::Hole",
        "hole_counterbore": "PartDesign::Hole",
        "draft": "PartDesign::Draft",
    }
    evidence = {}
    for case, expected_tip in expected_tips.items():
        case_root = root / "feature-families" / case
        (case_root / "outputs").mkdir(parents=True)
        document = App.newDocument(f"PartDesignFeature_{case}")
        try:
            api = create_domain_api(
                pack.domain,
                pack.api_exports,
                pack.output_types,
            )
            body = api.body(_feature_case(api, case), label=case)
            outputs, validation = validate_and_build_partdesign(
                document,
                {"Result": body},
                [{"name": "Result", "type": "solid"}],
                case_root,
                max_shape_subelements=256,
            )
            output = outputs[0]
            facts = output["facts"]
            data = output["partdesign_data"]
            assert facts["shape_type"] == "Solid", (case, facts)
            assert facts["solids"] == 1, (case, facts)
            assert facts["volume_mm3"] > 0.0, (case, facts)
            assert data["tip_type_id"] == expected_tip, (case, data)
            assert validation["outputs"][0]["name"] == "Result"
            if case == "constrained_pad":
                sketch = data["sketches"][0]
                assert sketch["fully_constrained"] is True
                assert sketch["degrees_of_freedom"] == 0
            evidence[case] = {
                "tip": data["tip_type_id"],
                "volume_mm3": facts["volume_mm3"],
            }
        finally:
            App.closeDocument(document.Name)
    return evidence


def _exercise_unified_standalone_surface(root: Path, pack) -> dict[str, dict]:
    """Materialize every standalone/cross-kernel export through publication."""

    import FreeCAD as App
    import Materials
    import Part

    from vibescript_part_worker import part_shape_facts
    from vibescript_partdesign_worker import configure_partdesign_references

    reference_root = root / "references"
    reference_root.mkdir(parents=True)
    reference_path = reference_root / "source.brep"
    reference_shape = Part.makeBox(3, 4, 5)
    reference_shape.exportBrep(str(reference_path))
    configure_partdesign_references(
        reference_root,
        [
            {
                "document_uid": "partdesign-api-fixture",
                "object_name": "Source",
                "shape_type": "Solid",
                "brep_sha256": __import__("hashlib").sha256(
                    reference_path.read_bytes()
                ).hexdigest(),
                "artifact_path": "source.brep",
                "facts": part_shape_facts(reference_shape, max_subelements=32),
                "source_kind": "scripted_publication",
                "source_revision": "a" * 64,
                "transient_topology": True,
                "requires_semantic_interfaces": True,
                "published_interfaces": {
                    "DatumEdge": {
                        "model_id": "partdesign-api-fixture",
                        "publication_name": "Source",
                        "output_key": "Source",
                        "subelements": ["Edge1"],
                        "geometry": [{"geometry_type": "line"}],
                    }
                },
            }
        ],
    )

    def closed_wire(api, points):
        return api.wire(points, closed=True)

    def planar_face(api, points):
        return api.face(closed_wire(api, points))

    def cube_faces(api, size=4.0):
        return [
            planar_face(api, [[0, 0, 0], [size, 0, 0], [size, size, 0], [0, size, 0]]),
            planar_face(api, [[0, 0, size], [0, size, size], [size, size, size], [size, 0, size]]),
            planar_face(api, [[0, 0, 0], [0, 0, size], [size, 0, size], [size, 0, 0]]),
            planar_face(api, [[size, 0, 0], [size, 0, size], [size, size, size], [size, size, 0]]),
            planar_face(api, [[size, size, 0], [size, size, size], [0, size, size], [0, size, 0]]),
            planar_face(api, [[0, size, 0], [0, size, size], [0, 0, size], [0, 0, 0]]),
        ]

    def cases(api):
        lower = api.wire([api.circle_3d(2, center=[0, 0, 0])])
        upper = api.wire([api.circle_3d(1, center=[0, 0, 5])])
        reversed_lower = api.wire([api.circle_3d(1, center=[8, 0, 5])])
        reversed_upper = api.wire([api.circle_3d(2, center=[8, 0, 0])])
        box = api.box(4, 5, 6)
        overlapping = api.box(4, 5, 6, origin=[2, 0, 0])
        top_query = api.find_subelements(
            element_type="face",
            expected_count=1,
            geometry_type="plane",
            normal=[0, 0, 1],
            min_area_mm2=19,
        )
        bottom_query = api.find_subelements(
            element_type="face",
            expected_count=1,
            geometry_type="plane",
            normal=[0, 0, -1],
            min_area_mm2=19,
        )
        bottom_face = api.subshape(box, "face", bottom_query)
        shell = api.shell(cube_faces(api))
        direct_solid = api.solid(shell)
        boss = api.boolean(
            [
                api.box(10, 10, 5),
                api.cylinder(2, 3, origin=[5, 5, 5]),
            ],
            operation="union",
        )
        boss_faces = api.find_subelements(
            element_type="face",
            expected_count=2,
            max_area_mm2=40,
        )
        standalone_loft = api.loft([lower, upper], operation="new_solid")
        reverse_loft = api.loft(
            [reversed_lower, reversed_upper], operation="new_solid"
        )
        stitching = api.compound([standalone_loft, reverse_loft])
        stitch_check = api.measure(stitching, "solid_count", expected=2)
        stitch_volume_check = api.measure(
            stitching, "volume_mm3", minimum=70.0
        )
        face_profile = planar_face(
            api, [[0, 0, 0], [3, 0, 0], [3, 2, 0], [0, 2, 0]]
        )
        sweep_profile = api.wire([api.circle_3d(0.5)])
        sweep_path = api.wire([[0, 0, 0], [0, 0, 4]])
        projection_target = api.plane(20, 20, origin=[-10, -10, 0])
        union = api.boolean([box, overlapping], operation="union")
        external = api.external_geometry(
            {
                "document_uid": "partdesign-api-fixture",
                "object_name": "Source",
            },
            {"type": "published_interface", "interface_name": "DatumEdge"},
        )
        external_profile = api.sketch(
            [external, api.circle([0, 0], 1)],
            require_closed_profile=True,
        )
        return [
            ("from_object", api.from_object({"document_uid": "partdesign-api-fixture", "object_name": "Source"}, output_type="solid"), "solid", {"from_object"}, ()),
            ("box", box, "solid", {"box"}, ()),
            ("wedge", api.wedge(6, 4, 3, ridge_x=2), "solid", {"wedge"}, ()),
            ("plane", projection_target, "face", {"plane"}, ()),
            ("prism", api.prism(6, 3, 5), "solid", {"prism"}, ()),
            ("cylinder", api.cylinder(2, 5), "solid", {"cylinder"}, ()),
            ("cone", api.cone(3, 1, 5), "solid", {"cone"}, ()),
            ("sphere", api.sphere(3), "solid", {"sphere"}, ()),
            ("torus", api.torus(5, 1), "solid", {"torus"}, ()),
            ("line_3d", api.wire([api.line_3d([0, 0, 0], [2, 0, 0])]), "wire", {"line_3d", "wire"}, ()),
            ("arc_3d", api.wire([api.arc_3d([0, 0, 0], [1, 1, 0], [2, 0, 0])]), "wire", {"arc_3d"}, ()),
            ("circle_3d", lower, "wire", {"circle_3d"}, ()),
            ("ellipse_3d", api.wire([api.ellipse_3d(3, 1)]), "wire", {"ellipse_3d"}, ()),
            ("bezier_3d", api.wire([api.bezier_3d([[0, 0, 0], [1, 2, 0], [3, 0, 0]])]), "wire", {"bezier_3d"}, ()),
            ("bspline_3d", api.wire([api.bspline_3d([[0, 0, 0], [1, 2, 0], [2, -1, 0], [4, 0, 0]])]), "wire", {"bspline_3d"}, ()),
            ("nurbs_curve", api.wire([api.nurbs_curve([[0, 0, 0], [1, 2, 0], [3, 0, 0]], 2, [0, 1], [3, 3])]), "wire", {"nurbs_curve"}, ()),
            ("helix_curve", api.helix_curve(1, 4, 2), "wire", {"helix_curve"}, ()),
            ("face", face_profile, "face", {"face"}, ()),
            ("shell", shell, "shell", {"shell"}, ()),
            ("solid", direct_solid, "solid", {"solid"}, ()),
            ("compound", stitching, "compound", {"compound", "loft", "measure"}, (stitch_check, stitch_volume_check)),
            ("subshape", bottom_face, "face", {"subshape", "find_subelements"}, ()),
            ("extrude", api.extrude(face_profile, 4, operation="new_solid", vector=[0, 0, 1]), "solid", {"extrude"}, ()),
            ("extrude_surface", api.extrude(api.line_3d([0, 0, 0], [3, 0, 0]), 2, operation="new_surface", vector=[0, 0, 1]), "face", {"extrude"}, ()),
            ("external_geometry", api.extrude(external_profile, 1, operation="new_solid"), "solid", {"external_geometry"}, ()),
            ("revolve", api.revolve(planar_face(api, [[2, 0, 0], [4, 0, 0], [4, 0, 3], [2, 0, 3]]), operation="new_solid", axis="Z", axis_direction=[0, 0, 1]), "solid", {"revolve"}, ()),
            ("revolve_surface", api.revolve(api.line_3d([3, 0, 0], [3, 0, 2]), 180, operation="new_surface", axis="Z", axis_direction=[0, 0, 1]), "face", {"revolve"}, ()),
            ("loft_surface", api.loft([lower, upper], operation="new_surface"), "shell", {"loft"}, ()),
            ("sweep", api.sweep(sweep_profile, sweep_path, operation="new_solid"), "solid", {"sweep"}, ()),
            ("sweep_surface", api.sweep(sweep_profile, sweep_path, operation="new_surface"), "shell", {"sweep"}, ()),
            ("boolean_union", union, "solid", {"boolean"}, ()),
            ("boolean_subtract", api.boolean([box, api.cylinder(1, 6, origin=[2, 2, 0])], operation="subtract"), "solid", {"boolean"}, ()),
            ("boolean_intersect", api.boolean([box, overlapping], operation="intersect"), "solid", {"boolean"}, ()),
            ("section", api.section(box, overlapping), "compound", {"section"}, ()),
            ("general_fuse", api.general_fuse([box, overlapping]), "compound", {"general_fuse"}, ()),
            ("slice", api.slice(box, [0, 0, 1], [2, 4]), "compound", {"slice"}, ()),
            ("ruled_surface", api.ruled_surface(api.line_3d([0, 0, 0], [4, 0, 0]), api.line_3d([0, 2, 3], [4, 2, 3])), "face", {"ruled_surface"}, ()),
            ("filled_surface", api.filled_surface([bottom_face]), "face", {"filled_surface"}, ()),
            ("polar_pattern", api.polar_pattern(api.box(1, 1, 1, origin=[3, 0, 0]), 4), "compound", {"polar_pattern"}, ()),
            ("polar_pattern_union", api.polar_pattern(api.box(2, 2, 1, origin=[0, -1, 0]), 2, result="union"), "solid", {"polar_pattern"}, ()),
            ("linear_pattern", api.linear_pattern(api.box(1, 1, 1), 3, 4), "compound", {"linear_pattern"}, ()),
            ("linear_pattern_union", api.linear_pattern(api.box(1, 1, 1), 3, 2, result="union"), "solid", {"linear_pattern"}, ()),
            ("multi_transform", api.multi_transform(api.box(1, 1, 1), [{"type": "translate", "vector": [2, 0, 0]}, {"type": "translate", "vector": [2, 0, 0]}]), "compound", {"multi_transform"}, ()),
            ("multi_transform_union", api.multi_transform(api.box(1, 1, 1), [{"type": "translate", "vector": [1, 0, 0]}, {"type": "translate", "vector": [1, 0, 0]}], result="union"), "solid", {"multi_transform"}, ()),
            ("mirror", api.mirror(api.box(1, 2, 3, origin=[2, 0, 0]), "YZ"), "solid", {"mirror"}, ()),
            ("fillet", api.fillet(api.box(4, 4, 4), {"type": "all_edges"}, 0.25), "solid", {"fillet"}, ()),
            ("chamfer", api.chamfer(api.box(4, 4, 4), {"type": "all_edges"}, 0.25), "solid", {"chamfer"}, ()),
            ("thickness", api.thickness(box, top_query, 0.25, inward=True), "solid", {"thickness"}, ()),
            ("defeature", api.defeature(boss, boss_faces), "solid", {"defeature"}, ()),
            ("to_nurbs", api.to_nurbs(api.wire([api.circle_3d(2)])), "wire", {"to_nurbs"}, ()),
            ("reverse", api.reverse(api.wire([api.line_3d([0, 0, 0], [2, 0, 0])])), "wire", {"reverse"}, ()),
            ("sew", api.sew(cube_faces(api), output_type="shell"), "shell", {"sew"}, ()),
            ("repair", api.repair(api.box(2, 3, 4)), "solid", {"repair"}, ()),
            ("offset", api.offset(face_profile, 0.2), "shell", {"offset"}, ()),
            ("offset2d", api.offset2d(face_profile, 0.2, fill=True), "face", {"offset2d"}, ()),
            ("transform", api.transform(api.box(1, 2, 3), translation=[2, 0, 0], rotation_degrees=30), "solid", {"transform"}, ()),
            ("project", api.project(projection_target, api.circle_3d(2, center=[0, 0, 5]), [0, 0, -1]), "wire", {"project"}, ()),
            ("refine", api.refine(union), "solid", {"refine"}, ()),
        ]

    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    catalog_cards = sorted(
        list(Materials.MaterialManager().Materials.values()),
        key=lambda card: (str(card.Name), str(card.UUID)),
    )
    assert catalog_cards
    publication_material = api.material(str(catalog_cards[0].UUID))
    publication_appearance = api.appearance(color_rgb=[255, 255, 255])
    evidence: dict[str, dict] = {}
    covered = {
        "point",
        "line",
        "arc",
        "circle",
        "ellipse",
        "bspline",
        "constraint",
        "sketch",
        "helix",
        "hole",
        "draft",
        "body",
        "publish",
    }
    for name, shape, output_type, case_exports, checks in cases(api):
        case_root = root / "unified-surface" / name
        (case_root / "outputs").mkdir(parents=True)
        document = App.newDocument(f"PartDesignUnified_{name}")
        try:
            publication = api.publish(
                shape,
                checks=checks,
                material=publication_material if name == "box" else None,
                appearance=publication_appearance if name == "box" else None,
                label=name,
            )
            outputs, _validation = validate_and_build_partdesign(
                document,
                {"Result": publication},
                [{"name": "Result", "type": output_type}],
                case_root,
                max_shape_subelements=256,
            )
            facts = outputs[0]["facts"]
            assert facts["shape_type"].lower() == output_type, (name, facts)
            if output_type == "solid":
                assert facts["solids"] == 1 and facts["volume_mm3"] > 0, (
                    name,
                    facts,
                )
            if name == "compound":
                assert facts["solids"] == 2, facts
                assert facts["volume_mm3"] > 70.0, facts
                assert all(item["accepted"] for item in outputs[0]["partdesign_data"]["checks"])
            if name == "box":
                presentation = outputs[0]["partdesign_data"]["presentation"]
                assert presentation["physical_material"]["uuid"] == str(
                    catalog_cards[0].UUID
                )
                assert presentation["appearance"]["resolved"]["shape_color"] == [
                    1.0,
                    1.0,
                    1.0,
                ]
                covered.update({"material", "appearance"})
            evidence[name] = {
                "shape_type": facts["shape_type"],
                "solids": facts["solids"],
            }
            covered.update(case_exports)
        finally:
            App.closeDocument(document.Name)
    missing = set(pack.api_exports) - covered
    assert not missing, f"Unexercised Part Design VibeScript exports: {sorted(missing)}"
    return evidence


def _exercise_material_guardrails(root: Path, pack) -> dict:
    """Reject a valid-looking additive feature that adds no geometric region."""

    import FreeCAD as App

    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    base = api.extrude(
        api.sketch([api.circle([0, 0], 5)]),
        5,
        operation="add_material",
    )
    enclosed_profile = api.sketch([api.circle([0, 0], 1)])
    no_effect = api.extrude(
        enclosed_profile,
        2,
        operation="add_material",
        base=base,
    )
    publication = api.body(no_effect, label="Invalid No-Effect Additive Feature")
    case_root = root / "material-guardrails"
    (case_root / "outputs").mkdir(parents=True)
    document = App.newDocument("PartDesignMaterialGuardrail")
    try:
        try:
            validate_and_build_partdesign(
                document,
                {"Result": publication},
                [{"name": "Result", "type": "solid"}],
                case_root,
                max_shape_subelements=32,
            )
        except PartDesignCandidateError as error:
            assert "did not add material" in str(error)
            assert error.details["stage"] == "feature_postcondition"
            assert error.details["added_material_mm3"] <= 1.0e-7
            assert error.details["removed_material_mm3"] <= 1.0e-7
            return dict(error.details)
        raise AssertionError("A no-effect additive feature passed material validation.")
    finally:
        App.closeDocument(document.Name)


def _source(*, invalid_offset: bool = False, primary_label: str = "Base Extrusion") -> str:
    offset = "inputs['height'] + 100" if invalid_offset else "inputs['height']"
    return (
        "profile = api.sketch([api.circle([0,0], inputs['outer_radius'])], "
        "label='Base Profile')\n"
        "base = api.extrude(profile, inputs['height'], operation='add_material', "
        f"label={primary_label!r})\n"
        "hole_profile = api.sketch([api.circle([0,0], inputs['hole_radius'])], "
        f"z_offset_mm={offset}, label='Hole Profile')\n"
        "finished = api.extrude(hole_profile, inputs['hole_depth'], "
        "operation='remove_material', base=base, "
        "label='Bore')\n"
        "result = {'Part': api.body(finished, interfaces={"
        "'Top': {'selection': {'type':'query','element_type':'face',"
        "'expected_count':1,'geometry_type':'plane','normal':[0,0,1],"
        "'normal_tolerance_degrees':0.1,'min_area':100},"
        "'description':'Top mating face'},"
        "'Origin': {'selection': {'type':'origin'},'description':'Body origin'}"
        "}, label='Parametric Part')}\n"
    )


def _input_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "outer_radius": {"type": "number", "exclusiveMinimum": 0},
            "hole_radius": {"type": "number", "exclusiveMinimum": 0},
            "height": {"type": "number", "exclusiveMinimum": 0},
            "hole_depth": {"type": "number", "exclusiveMinimum": 0},
        },
        "required": ["outer_radius", "hole_radius", "height", "hole_depth"],
        "additionalProperties": False,
    }


def _document_consumers(document, published):
    whole = document.addObject("App::FeaturePython", "WholeObjectConsumer")
    whole.addProperty("App::PropertyLink", "SourceObject")
    whole.SourceObject = published
    specifications = (
        ("Assembly::AssemblyObject", "AssemblyConsumer"),
        ("Fem::FeaturePython", "FemConsumer"),
        ("Path::FeaturePython", "CamConsumer"),
        ("TechDraw::DrawViewPart", "TechDrawConsumer"),
        ("Robot::RobotObject", "RobotConsumer"),
        ("Inspection::Feature", "InspectionConsumer"),
    )
    consumers = [whole]
    for type_id, name in specifications:
        consumer = document.addObject(type_id, name)
        consumer.addProperty("App::PropertyLink", "VibeCADTestSource")
        consumer.VibeCADTestSource = published
        consumers.append(consumer)
    published.addProperty(
        "App::PropertyString",
        "HumanMaterialCard",
        "Material",
        "Human-authored physical material assignment.",
    )
    published.HumanMaterialCard = "urn:material:steel"
    return consumers


def _assert_consumers(document, consumers, published) -> None:
    assert document.getObject(published.Name) is published
    assert published.HumanMaterialCard == "urn:material:steel"
    for consumer in consumers:
        current = document.getObject(consumer.Name)
        assert current is not None
        if current.Name == "WholeObjectConsumer":
            assert current.SourceObject is published
        else:
            assert current.VibeCADTestSource is published


def _exercise_lifecycle(root: Path, pack) -> dict:
    import FreeCAD as App
    from pathlib import Path as LocalPath

    document = App.newDocument("PartDesignVibeScriptV2")
    service = _Service(document, root)
    base_capture = {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": service.provider_document_revision(),
        "document_objects": [],
        "surface": resolve_modeling_surface(
            "PartDesignWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(LocalPath(App.getHomePath()).resolve()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }
    create = _capture(
        base_capture,
        operation="create_program",
        arguments={
            "program_name": "Part Design v2 Lifecycle",
            "source": _source(),
            "input_schema": _input_schema(),
            "inputs": {
                "outer_radius": 10.0,
                "hole_radius": 2.0,
                "height": 12.0,
                "hole_depth": 5.0,
            },
            "expected_outputs": [{"name": "Part", "type": "solid"}],
        },
    )
    prepared, publication, accepted = _run_candidate(create, service)
    program_id = prepared["program_id"]
    identity = accepted["live_outputs"]["Part"]["object_name"]
    published = document.getObject(identity)
    assert published is not None
    assert published.Shape.ShapeType == "Solid"
    assert len(published.Shape.Solids) == 1
    assert publication["created_objects"]
    assert publication["recompute_deferred"] is True
    assert publication["interfaces"]["Top"]["resolved"]["object"] == identity
    assert publication["interfaces"]["Top"]["resolved"]["subelements"]
    top = resolve_interface(service, published, "Top")
    assert top["publication_name"] == identity
    assert top["interface_name"] == "Top"

    inspected = complete_inspection(
        {
            "pack": pack,
            "program_id": program_id,
            "project_root": str(root),
            "live_programs": [],
        }
    )
    assert inspected["ok"] is True
    assert inspected["model_state"]["status"] == "accepted_current"
    assert inspected["program"]["accepted_revision"] == accepted["accepted_revision"]

    edit = _capture(
        base_capture,
        operation="edit_source",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "replacements": [
                {"old": "label='Base Extrusion'", "new": "label='Primary Extrusion'"}
            ],
        },
    )
    _edited, edit_publication, accepted = _run_candidate(edit, service)
    assert edit_publication["created_objects"] == []
    assert accepted["live_outputs"]["Part"]["object_name"] == identity

    failed = _capture(
        base_capture,
        operation="edit_source",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "replacements": [
                {
                    "old": "z_offset_mm=inputs['height']",
                    "new": "z_offset_mm=inputs['height'] + 100",
                }
            ],
        },
    )
    failed_prepared = prepare_candidate(failed)
    failed_execution = execute_candidate(failed_prepared, cancellation_check=None)
    assert failed_execution["ok"] is False
    assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
    assert failed_execution["domain_failure_stage"] == "feature_postcondition"
    assert "did not remove material" in failed_execution["error"]
    retain_candidate(failed_prepared, status="failed", failure=failed_execution)
    assert getattr(published, PROP_PUBLISHED_REVISION) == accepted["accepted_revision"]

    recovery = _capture(
        base_capture,
        operation="edit_source",
        arguments={
            "program_id": program_id,
            "expected_revision": failed_prepared["revision"],
            "replacements": [
                {
                    "old": "z_offset_mm=inputs['height'] + 100",
                    "new": "z_offset_mm=inputs['height']",
                }
            ],
        },
    )
    _recovered, recovery_publication, accepted = _run_candidate(recovery, service)
    assert recovery_publication["created_objects"] == []
    assert document.getObject(identity) is published

    set_inputs = _capture(
        base_capture,
        operation="set_inputs",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "patch": {"height": 15.0},
        },
    )
    _inputs, inputs_publication, accepted = _run_candidate(set_inputs, service)
    assert inputs_publication["created_objects"] == []
    assert accepted["live_outputs"]["Part"]["object_name"] == identity

    consumers = _document_consumers(document, published)
    unsafe = document.addObject("Part::Feature", "UnsafeFaceConsumer")
    unsafe.addProperty("App::PropertyLinkSub", "SourceFace")
    unsafe.SourceFace = (published, ["Face1"])
    unsafe_update = _capture(
        base_capture,
        operation="set_inputs",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "patch": {"outer_radius": 11.0},
        },
    )
    unsafe_prepared = prepare_candidate(unsafe_update)
    unsafe_execution = execute_candidate(unsafe_prepared, cancellation_check=None)
    assert unsafe_execution["ok"] is True, unsafe_execution
    unsafe_validated = validate_candidate(unsafe_prepared, unsafe_execution)
    retain_candidate(unsafe_prepared, status="validated")
    try:
        publish_candidate(service, unsafe_prepared, unsafe_validated)
    except RuntimeError as exc:
        assert "Face/Edge/Vertex references" in str(exc)
        details = getattr(exc, "details", {})
        assert any(
            item.get("owner_name") == "UnsafeFaceConsumer"
            for item in details.get("unsafe_references", [])
        ), details
    else:
        raise AssertionError("An unmanaged Face1 consumer survived regeneration.")
    retain_candidate(
        unsafe_prepared,
        status="publication_failed",
        failure={
            "failure_code": "DOMAIN_PUBLICATION_FAILED",
            "failure_stage": "native_call",
            "error": "unmanaged transient topology consumer",
        },
    )
    assert getattr(published, PROP_PUBLISHED_REVISION) == accepted["accepted_revision"]
    document.removeObject(unsafe.Name)

    safe_update = _capture(
        base_capture,
        operation="set_inputs",
        arguments={
            "program_id": program_id,
            "expected_revision": unsafe_prepared["revision"],
            "patch": {"outer_radius": 11.5},
        },
    )
    _safe, safe_publication, accepted = _run_candidate(safe_update, service)
    assert safe_publication["created_objects"] == []
    _assert_consumers(document, consumers, published)
    assert resolve_interface(service, published, "Top")["subelements"]

    reconfigured_source = (
        "profile = api.sketch([api.circle([0,0], inputs['outer_radius'])])\n"
        "base = api.extrude(profile, inputs['height'], operation='add_material', "
        "label='Primary Extrusion')\n"
        "hole_profile = api.sketch([api.circle([0,0], inputs['hole_radius'])], "
        "z_offset_mm=inputs['height'])\n"
        "finished = api.extrude(hole_profile, operation='remove_material', "
        "base=base, through_all=True, label='Bore')\n"
        "result = {'Part': api.body(finished, interfaces={"
        "'Top': {'selection': {'type':'query','element_type':'face',"
        "'expected_count':1,'geometry_type':'plane','normal':[0,0,1],"
        "'min_area':100}}"
        "}, label='Parametric Part')}\n"
    )
    reconfigure = _capture(
        base_capture,
        operation="reconfigure_program",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "source": reconfigured_source,
            "input_schema": {
                "type": "object",
                "properties": {
                    "outer_radius": {"type": "number", "exclusiveMinimum": 0},
                    "hole_radius": {"type": "number", "exclusiveMinimum": 0},
                    "height": {"type": "number", "exclusiveMinimum": 0},
                },
                "required": ["outer_radius", "hole_radius", "height"],
                "additionalProperties": False,
            },
            "inputs": {
                "outer_radius": 11.5,
                "hole_radius": 2.0,
                "height": 15.0,
            },
            "expected_outputs": [{"name": "Part", "type": "solid"}],
        },
    )
    _reconfigured, reconfigure_publication, accepted = _run_candidate(
        reconfigure, service
    )
    assert reconfigure_publication["created_objects"] == []
    assert accepted["live_outputs"]["Part"]["object_name"] == identity
    _assert_consumers(document, consumers, published)

    saved_path = root / "partdesign-v2-lifecycle.FCStd"
    consumer_names = [str(item.Name) for item in consumers]
    document.saveAs(str(saved_path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(saved_path))
    assert reopened is not None
    service.document = reopened
    reopened_published = reopened.getObject(identity)
    assert reopened_published is not None
    reopened_consumers = [reopened.getObject(name) for name in consumer_names]
    assert all(item is not None for item in reopened_consumers)
    _assert_consumers(reopened, reopened_consumers, reopened_published)
    assert resolve_interface(service, reopened_published, "Top")["subelements"]

    reopened_capture = {
        **base_capture,
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
        "document_objects": [
            {
                "name": str(obj.Name),
                "label": str(obj.Label),
                "type_id": str(obj.TypeId),
            }
            for obj in reopened.Objects
        ],
    }
    reopened_update = _capture(
        reopened_capture,
        operation="set_inputs",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "patch": {"outer_radius": 12.0},
        },
    )
    _reopened_prepared, reopened_publication, accepted = _run_candidate(
        reopened_update, service
    )
    assert reopened_publication["created_objects"] == []
    assert accepted["live_outputs"]["Part"]["object_name"] == identity
    _assert_consumers(reopened, reopened_consumers, reopened_published)

    delete_capture = _capture(
        reopened_capture,
        operation="delete_program",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "reason": "Complete Part Design v2 lifecycle integration.",
        },
    )
    adapter = get_domain_adapter("partdesign")
    assert adapter is not None
    deletion = prepare_delete(delete_capture)
    try:
        adapter.delete(service, deletion, deletion["manifest"])
    except RuntimeError as exc:
        assert "Cannot delete" in str(exc)
        assert "WholeObjectConsumer" in str(exc)
    else:
        raise AssertionError("Part Design deletion ignored downstream links.")
    restore_prepared_delete(deletion)
    for consumer in reversed(reopened_consumers):
        reopened.removeObject(consumer.Name)
    deletion = prepare_delete(delete_capture)
    deletion_publication = adapter.delete(service, deletion, deletion["manifest"])
    deleted = finish_delete(deletion, deletion_publication)
    assert deleted["artifacts_deleted"] is True
    assert reopened.getObject(identity) is None
    assert not LocalPath(deletion["program_directory"]).exists()
    App.closeDocument(reopened.Name)
    return {
        "program_id": program_id,
        "stable_output_identity": identity,
        "failed_candidate_retained": failed_prepared["revision"],
        "unsafe_reference_rejected": True,
        "save_reopen_regenerated": True,
        "deleted": [item["object_name"] for item in deleted["deleted_objects"]],
    }


def _exercise_topology_publication(root: Path, pack) -> dict:
    """Prove non-solid Part Design outputs retain their exact live type metadata."""

    import FreeCAD as App
    from pathlib import Path as LocalPath

    document = App.newDocument("PartDesignTopologyPublication")
    service = _Service(document, root)
    base_capture = {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": service.provider_document_revision(),
        "document_objects": [],
        "surface": resolve_modeling_surface(
            "PartDesignWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(LocalPath(App.getHomePath()).resolve()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }
    source = (
        "face = api.plane(8, 6, label='Reference Face')\n"
        "size = inputs['thread_size']\n"
        "gap = inputs['thread_gap']\n"
        "left = api.box(size, size, size, label='Left Thread')\n"
        "right = api.box(size, size, size, origin=[size+gap,0,0], "
        "label='Right Thread')\n"
        "stitching = api.compound([left, right], label='Stitching')\n"
        "count = api.measure(stitching, 'solid_count', expected=2)\n"
        "result = {\n"
        " 'ReferenceFace': api.publish(face, label='Reference Face'),\n"
        " 'Stitching': api.publish(stitching, checks=[count], label='Stitching'),\n"
        "}\n"
    )
    create = _capture(
        base_capture,
        operation="create_program",
        arguments={
            "program_name": "Unified Topology Publication",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {
                    "thread_size": {"type": "number", "exclusiveMinimum": 0},
                    "thread_gap": {"type": "number", "exclusiveMinimum": 0},
                },
                "required": ["thread_size", "thread_gap"],
                "additionalProperties": False,
            },
            "inputs": {"thread_size": 2.0, "thread_gap": 2.0},
            "expected_outputs": [
                {"name": "ReferenceFace", "type": "face"},
                {"name": "Stitching", "type": "compound"},
            ],
        },
    )
    prepared, publication, accepted = _run_candidate(create, service)
    expected = {"ReferenceFace": "face", "Stitching": "compound"}
    identities = {}
    for name, output_type in expected.items():
        live = accepted["live_outputs"][name]
        assert live["output_type"] == output_type, live
        published = document.getObject(live["object_name"])
        assert published is not None
        assert str(getattr(published, PROP_OUTPUT_TYPE)) == output_type
        assert published.Shape.ShapeType.lower() == output_type
        identities[name] = str(published.Name)
    assert abs(
        float(accepted["live_outputs"]["Stitching"]["facts"]["volume_mm3"])
        - 16.0
    ) <= 1.0e-7
    assert publication["live_outputs"]["Stitching"]["partdesign_data"]["checks"][0][
        "accepted"
    ] is True
    update = _capture(
        base_capture,
        operation="set_inputs",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "patch": {"thread_size": 3.0},
        },
    )
    _updated, updated_publication, updated = _run_candidate(update, service)
    assert {
        name: row["object_name"] for name, row in updated["live_outputs"].items()
    } == identities
    assert {
        name: row["output_type"] for name, row in updated["live_outputs"].items()
    } == expected
    assert abs(
        float(updated["live_outputs"]["Stitching"]["facts"]["volume_mm3"])
        - 54.0
    ) <= 1.0e-7
    assert updated_publication["created_objects"] == []
    App.closeDocument(document.Name)
    return {"outputs": identities, "types": expected, "regenerated_volume_mm3": 54.0}


def _exercise_physical_material_publication(root: Path, pack) -> dict:
    """Publish one shared-catalog ShapeMaterial and preserve it on regeneration."""

    import FreeCAD as App
    import Materials
    from pathlib import Path as LocalPath

    cards = sorted(
        list(Materials.MaterialManager().Materials.values()),
        key=lambda card: (str(card.Name), str(card.UUID)),
    )
    assert cards
    card = cards[0]
    document = App.newDocument("PartDesignPhysicalMaterialPublication")
    service = _Service(document, root)
    base_capture = {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": service.provider_document_revision(),
        "document_objects": [],
        "surface": resolve_modeling_surface(
            "PartDesignWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(LocalPath(App.getHomePath()).resolve()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }
    source = (
        "shape = api.box(inputs['size'], 4, 2, label='Material Coupon')\n"
        "card = api.material(inputs['material_uuid'])\n"
        "result = {'Coupon': api.publish(shape, material=card, "
        "label='Material Coupon')}\n"
    )
    create = _capture(
        base_capture,
        operation="create_program",
        arguments={
            "program_name": "Part Design Physical Material",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {
                    "size": {"type": "number", "exclusiveMinimum": 0},
                    "material_uuid": {
                        "type": "string",
                        "enum": [str(card.UUID)],
                    },
                },
                "required": ["size", "material_uuid"],
                "additionalProperties": False,
            },
            "inputs": {"size": 6.0, "material_uuid": str(card.UUID)},
            "expected_outputs": [{"name": "Coupon", "type": "solid"}],
        },
    )
    try:
        prepared, _publication, accepted = _run_candidate(create, service)
        identity = accepted["live_outputs"]["Coupon"]["object_name"]
        published = document.getObject(identity)
        assert published is not None
        assert str(published.ShapeMaterial.UUID) == str(card.UUID)
        initial_volume = float(published.Shape.Volume)
        update = _capture(
            base_capture,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "patch": {"size": 9.0},
            },
        )
        _updated, update_publication, updated = _run_candidate(update, service)
        published = document.getObject(identity)
        assert published is not None
        assert update_publication["created_objects"] == []
        assert updated["live_outputs"]["Coupon"]["object_name"] == identity
        assert float(published.Shape.Volume) > initial_volume
        assert str(published.ShapeMaterial.UUID) == str(card.UUID)
        return {
            "object_name": identity,
            "material_uuid": str(card.UUID),
            "regenerated_volume_mm3": float(published.Shape.Volume),
        }
    finally:
        App.closeDocument(document.Name)


def main() -> int:
    dump_json = json.dumps
    remove_tree = shutil.rmtree
    pack = get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None and pack.production_ready
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    signatures = {
        name: str(inspect.signature(getattr(api, name)))
        for name in api.exported_names
    }
    assert tuple(signatures) == pack.api_exports
    assert all("*args" not in value and "**properties" not in value for value in signatures.values())
    external = api.external_geometry(
        {"document_uid": "source-document", "object_name": "SourcePart"},
        {"type": "published_interface", "interface_name": "MountEdge"},
    )
    assert external.domain == "partdesign"
    assert external.operation == "external_geometry"

    root = Path(tempfile.mkdtemp(prefix="vibecad-partdesign-v2-integration-"))
    try:
        feature_families = _exercise_feature_families(root, pack)
        unified_surface = _exercise_unified_standalone_surface(root, pack)
        material_guardrails = _exercise_material_guardrails(root, pack)
        topology_publication = _exercise_topology_publication(root, pack)
        physical_material = _exercise_physical_material_publication(root, pack)
        lifecycle = _exercise_lifecycle(root, pack)
        print(
            dump_json(
                {
                    "ok": True,
                    "domain": "partdesign",
                    "feature_families": feature_families,
                    "unified_surface": unified_surface,
                    "material_guardrails": material_guardrails,
                    "topology_publication": topology_publication,
                    "physical_material": physical_material,
                    "lifecycle": lifecycle,
                },
                sort_keys=True,
            )
        )
    finally:
        remove_tree(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
