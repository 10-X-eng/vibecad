# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD integration coverage for Part Design VibeScript v2."""

from __future__ import annotations

import inspect
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
from VibeCADComponentCatalog import open_component_candidates  # noqa: E402
from VibeCADReferenceContracts import resolve_interface  # noqa: E402
from VibeCADScriptedPublication import (  # noqa: E402
    PROP_REVISION as PROP_PUBLISHED_REVISION,
    ROLE_IMPLEMENTATION,
    ROLE_MODEL,
    role_of,
    tag_object,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    _live_programs,
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
    PROGRAM_SCHEMA,
    PROGRAM_VERSION,
    PROP_PROGRAM_CONTRACT,
    decode_document_program_contract,
    get_domain_adapter,
    get_vibescript_pack,
    program_revision,
)
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    PROP_OUTPUT_TYPE,
    PROP_PARTDESIGN_MATERIAL_BASELINE,
    _delete_partdesign_program,
    _material_target_snapshot,
    _restore_material_target_snapshots,
    _set_physical_material_preserving_view,
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
    if prepared["reference_requirements"]:
        prepared = finalize_candidate(
            prepared,
            capture_reference_inputs(service, prepared),
        )
    assert prepared["finalized"] is True
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    progress = execution.get("worker_progress")
    assert isinstance(progress, dict), execution
    assert progress.get("completed") is True, progress
    assert progress.get("phase") == "completed", progress
    assert progress.get("current_graph_node") is None, progress
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
        return api.extrude(
            _fully_constrained_rectangle(api),
            3,
            operation="add_material",
        )
    if case == "construction_point_pad":
        profile = api.sketch(
            [api.point([0, 0]), api.circle([0, 0], 5)],
            require_closed_profile=True,
        )
        return api.extrude(profile, 3, operation="add_material")
    if case == "arc_pad":
        profile = api.sketch(
            [
                api.arc([-5, 0], [0, 5], [5, 0]),
                api.line([5, 0], [-5, 0]),
            ]
        )
        return api.extrude(profile, 3, operation="add_material")
    if case == "ellipse_pad":
        return api.extrude(
            api.sketch([api.ellipse([0, 0], 6, 3)]),
            3,
            operation="add_material",
        )
    if case == "bspline_pad":
        profile = api.sketch(
            [
                api.bspline(
                    [[0, 0], [5, 0], [6, 4], [2, 7], [-2, 4]],
                    periodic=True,
                )
            ]
        )
        return api.extrude(profile, 3, operation="add_material")
    if case == "pocket":
        base = api.extrude(
            api.sketch([api.circle([0, 0], 10)]),
            5,
            operation="add_material",
        )
        cut = api.sketch([api.circle([0, 0], 2)], z_offset_mm=5)
        return api.extrude(
            cut,
            3,
            operation="remove_material",
            base=base,
        )
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
        base = api.extrude(
            api.sketch([api.circle([0, 0], 10)]),
            5,
            operation="add_material",
        )
        cut = _rectangle(api, 8, 1, 12, 4, plane="XZ")
        return api.revolve(
            cut,
            operation="remove_material",
            base=base,
            axis="V",
        )
    if case == "loft":
        return api.loft(
            [
                api.sketch([api.circle([0, 0], 5)]),
                api.sketch([api.circle([0, 0], 3)], z_offset_mm=10),
            ]
        )
    if case == "additive_loft":
        base = api.extrude(
            api.sketch([api.circle([0, 0], 6)]),
            5,
            operation="add_material",
        )
        return api.loft(
            [
                api.sketch([api.circle([0, 0], 3)], z_offset_mm=5),
                api.sketch([api.circle([0, 0], 2)], z_offset_mm=10),
            ],
            base=base,
            operation="add_material",
        )
    if case == "subtractive_loft":
        base = api.extrude(
            api.sketch([api.circle([0, 0], 10)]),
            10,
            operation="add_material",
        )
        return api.loft(
            [
                api.sketch([api.circle([0, 0], 2)]),
                api.sketch([api.circle([0, 0], 4)], z_offset_mm=10),
            ],
            base=base,
            operation="remove_material",
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
        base = api.extrude(
            api.sketch([api.circle([0, 0], 10)]),
            5,
            operation="add_material",
        )
        boss = api.extrude(
            api.sketch([api.circle([7, 0], 2)], z_offset_mm=5),
            3,
            operation="add_material",
            base=base,
        )
        if case == "polar_pattern":
            return api.polar_pattern(boss, 4)
        return api.mirror(boss, "YZ")
    if case == "fillet":
        base = api.extrude(
            _rectangle(api, 0, 0, 10, 8),
            5,
            operation="add_material",
        )
        return api.fillet(base, {"type": "all_edges"}, 0.5)
    if case == "chamfer":
        base = api.extrude(
            _rectangle(api, 0, 0, 10, 8),
            5,
            operation="add_material",
        )
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
    if case in {"fastener", "fastener_simple"}:
        return api.fastener(
            "ISO4762",
            "M6",
            length_mm=20,
            model_thread=case == "fastener",
            label="ISO 4762 M6 x 20",
        )
    if case == "involute_gear":
        return api.involute_gear(
            24,
            2.0,
            8.0,
            bore_diameter_mm=10.0,
            label="24 tooth involute gear",
        )
    if case.startswith("fastener_hole_"):
        base = api.extrude(
            api.sketch([api.circle([0, 0], 12)]),
            8,
            operation="add_material",
        )
        locations = api.sketch([api.circle([0, 0], 1)], z_offset_mm=8)
        purpose = case.removeprefix("fastener_hole_")
        standard = "ISO10642" if purpose == "countersink" else "ISO4762"
        fastener = api.fastener(
            standard,
            "M6",
            length_mm=20,
        )
        return api.fastener_hole(
            base,
            locations,
            fastener,
            purpose=purpose,
            fit="normal",
            through_all=True,
        )
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
        "fastener": "PartDesign::FeaturePython",
        "fastener_simple": "PartDesign::FeaturePython",
        "involute_gear": "PartDesign::Feature",
        "fastener_hole_clearance": "PartDesign::Hole",
        "fastener_hole_tapped": "PartDesign::Hole",
        "fastener_hole_counterbore": "PartDesign::Hole",
        "fastener_hole_countersink": "PartDesign::Hole",
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
                "edges": facts["edges"],
            }
        finally:
            App.closeDocument(document.Name)
    assert evidence["fastener"]["edges"] > evidence["fastener_simple"]["edges"]
    assert evidence["involute_gear"]["edges"] > 100
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
        distance_check = api.minimum_distance(
            box,
            api.box(1, 1, 1, origin=[6, 0, 0]),
            expected=2,
        )
        face_profile = planar_face(
            api, [[0, 0, 0], [3, 0, 0], [3, 2, 0], [0, 2, 0]]
        )
        sweep_profile = api.wire([api.circle_3d(0.5)])
        sweep_path = api.wire([[0, 0, 0], [0, 0, 4]])
        projection_target = api.plane(20, 20, origin=[-10, -10, 0])
        union = api.boolean([box, overlapping], operation="union")
        placed_fastener = api.transform(
            api.fastener(
                "ISO4762",
                "M6",
                length_mm=20,
                model_thread=True,
            ),
            translation=[12, 4, 3],
            rotation_axis=[0, 1, 0],
            rotation_degrees=90,
        )
        deep_transform = api.box(1, 1, 1)
        for _index in range(24):
            deep_transform = api.transform(
                deep_transform,
                translation=[0.25, 0, 0],
            )
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
            (
                "compound",
                stitching,
                "compound",
                {"compound", "loft", "measure", "minimum_distance"},
                (stitch_check, stitch_volume_check, distance_check),
            ),
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
            ("fastener_transform", placed_fastener, "solid", {"fastener", "transform"}, ()),
            ("deep_transform", deep_transform, "solid", {"box", "transform"}, ()),
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
        "fastener",
        "fastener_hole",
        "involute_gear",
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
    component_reference = {
        "document_uid": "partdesign-api-fixture",
        "object_name": "MotorDefinition",
    }
    occurrence = api.component(component_reference, placement=[1, 2, 3])
    repeated = api.instances(component_reference, [[0, 0, 0], [10, 0, 0]])
    assert occurrence.output_type == "component_link"
    assert len(repeated) == 2
    covered.update({"component", "instances"})
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


def _exercise_placement_and_hole_direction(root: Path, pack) -> dict:
    """Prove explicit primitive roll and actionable native hole direction evidence."""

    import FreeCAD as App

    oriented_root = root / "explicit-primitive-frame"
    (oriented_root / "outputs").mkdir(parents=True)
    oriented_document = App.newDocument("PartDesignExplicitPrimitiveFrame")
    try:
        api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
        oriented = api.box(
            2,
            3,
            4,
            origin=[10, 20, 30],
            direction=[0, 1, 0],
            x_direction=[1, 0, 0],
        )
        outputs, _validation = validate_and_build_partdesign(
            oriented_document,
            {"Result": api.publish(oriented)},
            [{"name": "Result", "type": "solid"}],
            oriented_root,
            max_shape_subelements=32,
        )
        assert outputs[0]["facts"]["bounds_mm"] == {
            "min": [10.0, 20.0, 27.0],
            "max": [12.0, 24.0, 30.0],
            "size": [2.0, 4.0, 3.0],
        }
    finally:
        App.closeDocument(oriented_document.Name)

    failed_root = root / "hole-direction-failure"
    (failed_root / "outputs").mkdir(parents=True)
    failed_document = App.newDocument("PartDesignHoleDirectionFailure")
    try:
        api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
        base = api.extrude(
            api.sketch([api.circle([0, 0], 5)]),
            8,
            operation="add_material",
        )
        far_profile = api.sketch(
            [api.circle([0, 0], 1)],
            plane_offset_mm=108,
        )
        failed_hole = api.hole(base, far_profile, 2, depth_mm=4)
        try:
            validate_and_build_partdesign(
                failed_document,
                {"Result": api.body(failed_hole)},
                [{"name": "Result", "type": "solid"}],
                failed_root,
                max_shape_subelements=32,
            )
        except PartDesignCandidateError as error:
            assert error.details["stage"] == "feature_postcondition"
            assert error.details["profile_source_placement"] == {
                "plane": "XY",
                "plane_offset_mm": 108.0,
            }
            assert error.details["profile_frame"]["origin_mm"] == [0.0, 0.0, 108.0]
            direction = error.details["attempted_cut_directions"][0]
            assert direction["direction_global"] == [-0.0, -0.0, -1.0]
            assert direction["base_bounds_projection_from_profile_mm"] == [100.0, 108.0]
            assert direction["requested_reach_mm"] == 4.0
            assert direction["axial_reach_can_intersect_bounds"] is False
            failure_evidence = dict(error.details)
        else:
            raise AssertionError("A hole that cannot reach its base passed validation.")
    finally:
        App.closeDocument(failed_document.Name)

    reversed_root = root / "reversed-hole"
    (reversed_root / "outputs").mkdir(parents=True)
    reversed_document = App.newDocument("PartDesignReversedHole")
    try:
        api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
        base = api.extrude(
            api.sketch([api.circle([0, 0], 5)]),
            8,
            operation="add_material",
        )
        bottom_profile = api.sketch(
            [api.point([0, 0])],
            require_closed_profile=False,
        )
        reversed_hole = api.hole(
            base,
            bottom_profile,
            2,
            depth_mm=4,
            direction="along_normal",
        )
        outputs, _validation = validate_and_build_partdesign(
            reversed_document,
            {"Result": api.body(reversed_hole)},
            [{"name": "Result", "type": "solid"}],
            reversed_root,
            max_shape_subelements=32,
        )
        assert outputs[0]["partdesign_data"]["tip_type_id"] == "PartDesign::Hole"
        reversed_volume = float(outputs[0]["facts"]["volume_mm3"])
    finally:
        App.closeDocument(reversed_document.Name)

    symmetric_root = root / "single-point-symmetric-through-hole"
    (symmetric_root / "outputs").mkdir(parents=True)
    symmetric_document = App.newDocument("PartDesignSymmetricBoundaryHole")
    try:
        api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
        wall_profile = api.sketch(
            [
                api.line([-38, 0], [38, 0]),
                api.line([38, 0], [38, 8]),
                api.line([38, 8], [-38, 8]),
                api.line([-38, 8], [-38, 0]),
            ],
            plane="XY",
            plane_offset_mm=8,
        )
        wall = api.extrude(
            wall_profile,
            90,
            operation="add_material",
            direction="along_normal",
        )
        one_center = api.sketch(
            [api.point([0, 53])],
            plane="XZ",
            plane_offset_mm=-8,
            require_closed_profile=False,
        )
        boundary_hole = api.hole(
            wall,
            one_center,
            42,
            through_all=True,
            direction="symmetric",
        )
        outputs, _validation = validate_and_build_partdesign(
            symmetric_document,
            {"Result": api.body(boundary_hole)},
            [{"name": "Result", "type": "solid"}],
            symmetric_root,
            max_shape_subelements=32,
        )
        assert outputs[0]["partdesign_data"]["tip_type_id"] == "PartDesign::Hole"
        symmetric_volume = float(outputs[0]["facts"]["volume_mm3"])
        assert symmetric_volume < 76.0 * 8.0 * 90.0
    finally:
        App.closeDocument(symmetric_document.Name)

    stepped_root = root / "symmetric-through-hole-across-material-step"
    (stepped_root / "outputs").mkdir(parents=True)
    stepped_document = App.newDocument("PartDesignSymmetricSteppedHole")
    try:
        api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
        wall_profile = api.sketch(
            [
                api.line([-20, 0], [20, 0]),
                api.line([20, 0], [20, 40]),
                api.line([20, 40], [-20, 40]),
                api.line([-20, 40], [-20, 0]),
            ],
            plane="XZ",
        )
        wall = api.extrude(
            wall_profile,
            8,
            operation="add_material",
            direction="opposite_normal",
        )
        boss_profile = api.sketch(
            [api.circle([0, 20], 9)],
            plane="XZ",
            plane_offset_mm=-8,
        )
        stepped_base = api.extrude(
            boss_profile,
            6,
            operation="add_material",
            base=wall,
            direction="opposite_normal",
        )
        hole_locations = api.sketch(
            [api.point([0, 20])],
            plane="XZ",
            plane_offset_mm=-8,
            require_closed_profile=False,
        )
        stepped_hole = api.hole(
            stepped_base,
            hole_locations,
            6.4,
            through_all=True,
            direction="symmetric",
        )
        outputs, _validation = validate_and_build_partdesign(
            stepped_document,
            {"Result": api.body(stepped_hole)},
            [{"name": "Result", "type": "solid"}],
            stepped_root,
            max_shape_subelements=32,
        )
        stepped_volume = float(outputs[0]["facts"]["volume_mm3"])
        expected_volume = (
            40.0 * 40.0 * 8.0
            + math.pi * 9.0 * 9.0 * 6.0
            - math.pi * 3.2 * 3.2 * 14.0
        )
        assert abs(stepped_volume - expected_volume) <= 1.0e-5
    finally:
        App.closeDocument(stepped_document.Name)

    return {
        "oriented_bounds_verified": True,
        "failed_hole_stage": str(failure_evidence["stage"]),
        "failed_hole_direction_verified": True,
        "point_hole_explicit_direction_verified": True,
        "reversed_hole_volume_mm3": reversed_volume,
        "single_point_symmetric_through_hole_verified": True,
        "single_point_symmetric_volume_mm3": symmetric_volume,
        "symmetric_stepped_through_hole_verified": True,
        "symmetric_stepped_volume_mm3": stepped_volume,
    }


def _exercise_geometry_verification(root: Path, pack) -> dict:
    """Verify checks against regenerated BREP rather than helper arithmetic."""

    import FreeCAD as App

    case_root = root / "geometry-verification"
    (case_root / "outputs").mkdir(parents=True)
    document = App.newDocument("PartDesignGeometryVerification")
    try:
        api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
        block = api.box(10, 10, 10)
        separated = api.box(2, 2, 2, origin=[12, 0, 0])
        overlapping = api.box(3, 10, 10, origin=[9, 0, 0])
        cylinder = api.cylinder(2, 8)
        angled_profile = api.sketch(
            [api.circle([0, 0], 2)],
            placement={
                "origin": [20, 0, 0],
                "normal": [0, 1, 0],
                "x_direction": [1, 0, 0],
            },
        )
        angled = api.extrude(
            angled_profile,
            4,
            operation="new_solid",
        )
        positive_x = api.find_subelements(
            element_type="face",
            expected_count=1,
            geometry_type="Plane",
            normal=[1, 0, 0],
        )
        negative_x = api.find_subelements(
            element_type="face",
            expected_count=1,
            geometry_type="Plane",
            normal=[-1, 0, 0],
        )
        cylinder_side = api.find_subelements(
            element_type="face",
            expected_count=1,
            geometry_type="Cylinder",
            radius_mm=2,
        )
        steel = api.material(
            "90bbd8ef-8623-4d78-b3bf-e0bdb9b74dd3",
            require_physical_properties=["Density"],
        )
        checks = [
            api.measure(block, "bounds_size_x_mm", expected=10),
            api.measure(block, "center_of_mass_x_mm", expected=5),
            api.measure(
                block,
                "minimum_distance_mm",
                other=separated,
                expected=2,
            ),
            api.minimum_distance(block, separated, expected=2),
            api.measure(
                block,
                "interference_volume_mm3",
                other=overlapping,
                expected=100,
            ),
            api.measure(
                block,
                "minimum_wall_thickness_mm",
                selection=positive_x,
                other_selection=negative_x,
                expected=10,
            ),
            api.measure(
                cylinder,
                "diameter_mm",
                selection=cylinder_side,
                expected=4,
            ),
            api.measure(block, "mass_kg", material=steel, minimum=1.0e-12),
            api.measure(
                block,
                "inertia_xx_kg_mm2",
                material=steel,
                minimum=1.0e-12,
            ),
        ]
        outputs, validation = validate_and_build_partdesign(
            document,
            {
                "Verified": api.body(block, checks=checks, material=steel),
                "Angled": api.body(
                    angled,
                    checks=[api.measure(angled, "bounds_size_y_mm", expected=4)],
                ),
            },
            [
                {"name": "Verified", "type": "solid"},
                {"name": "Angled", "type": "solid"},
            ],
            case_root,
            max_shape_subelements=64,
        )
        evidence = outputs[0]["partdesign_data"]["checks"]
        assert len(evidence) == len(checks)
        assert all(item["accepted"] for item in evidence)
        by_quantity = {item["quantity"]: item for item in evidence}
        assert math.isclose(
            by_quantity["minimum_distance_mm"]["actual"], 2.0, abs_tol=1.0e-9
        )
        assert math.isclose(
            by_quantity["interference_volume_mm3"]["actual"],
            100.0,
            abs_tol=1.0e-9,
        )
        assert math.isclose(
            by_quantity["minimum_wall_thickness_mm"]["actual"],
            10.0,
            abs_tol=1.0e-9,
        )
        assert math.isclose(
            by_quantity["diameter_mm"]["actual"], 4.0, abs_tol=1.0e-9
        )
        assert by_quantity["mass_kg"]["actual"] > 0.0
        assert validation["outputs"][0]["name"] == "Verified"
        angled_checks = outputs[1]["partdesign_data"]["checks"]
        assert angled_checks[0]["quantity"] == "bounds_size_y_mm"
        assert angled_checks[0]["accepted"] is True
        return {
            name: by_quantity[name]["actual"]
            for name in (
                "minimum_distance_mm",
                "interference_volume_mm3",
                "minimum_wall_thickness_mm",
                "diameter_mm",
                "mass_kg",
            )
        }
    finally:
        App.closeDocument(document.Name)


def _exercise_attached_sketch_history(root: Path, pack) -> dict:
    """Keep a stable native support through isolation and history publication."""

    import FreeCAD as App
    import Part
    from pathlib import Path as LocalPath

    document = App.newDocument("PartDesignAttachedSketchHistory")
    service = _Service(document, root)
    support = document.addObject("Part::Feature", "MasterSupport")
    support.Label = "Master support"
    support.Shape = Part.makeBox(20, 20, 2)
    document.recompute()
    capture = {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": service.provider_document_revision(),
        "document_objects": [
            {
                "name": str(support.Name),
                "label": str(support.Label),
                "type_id": str(support.TypeId),
            }
        ],
        "surface": resolve_modeling_surface(
            "PartDesignWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(LocalPath(App.getHomePath()).resolve()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }
    source = """\
profile = api.sketch(
    [api.circle([10, 10], 2)],
    support={
        'reference': inputs['support'],
        'selection': {'type': 'subelements', 'subelements': ['Face6']},
    },
    map_mode='FlatFace',
    require_closed_profile=True,
    label='Attached profile',
)
feature = api.extrude(profile, 5, operation='add_material', label='Supported boss')
result = {'SupportedBoss': api.body(feature, label='Supported boss body')}
"""
    try:
        prepared, publication, accepted = _run_candidate(
            _capture(
                capture,
                operation="create_program",
                arguments={
                    "program_name": "Attached sketch",
                    "source": source,
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "support": {
                                "type": "object",
                                "x-vibecad-reference": True,
                                "properties": {
                                    "document_uid": {"type": "string"},
                                    "object_name": {"type": "string"},
                                },
                                "required": ["document_uid", "object_name"],
                                "additionalProperties": False,
                            }
                        },
                        "required": ["support"],
                        "additionalProperties": False,
                    },
                    "inputs": {
                        "support": {
                            "document_uid": str(document.Uid),
                            "object_name": str(support.Name),
                        }
                    },
                    "expected_outputs": [
                        {"name": "SupportedBoss", "type": "solid"}
                    ],
                },
            ),
            service,
        )
        assert len(prepared["reference_requirements"]) == 1
        body_name = publication["native_history"]["body_objects"]["SupportedBoss"]
        body = document.getObject(body_name)
        assert body is not None
        assert body.Tip is not None
        assert body.Tip.TypeId == "PartDesign::DesignBodyPublication"
        sketch_evidence = publication["live_outputs"]["SupportedBoss"][
            "partdesign_data"
        ]["sketches"]
        assert len(sketch_evidence) == 1
        resolved_support = sketch_evidence[0]["support"]
        assert resolved_support["reference"] == {
            "document_uid": str(document.Uid),
            "object_name": str(support.Name),
        }
        assert resolved_support["resolved_subelements"] == ["Face6"]
        assert resolved_support["map_mode"] == "FlatFace"
        program_id = str(prepared["program_id"])
        roots = [
            obj
            for obj in document.Objects
            if role_of(obj) == ROLE_MODEL
            and str(getattr(obj, "VibeCADScriptedModelId", "") or "")
            == program_id
        ]
        assert len(roots) == 1, [
            (
                str(obj.Name),
                role_of(obj),
                str(getattr(obj, "VibeCADScriptedModelId", "") or ""),
            )
            for obj in document.Objects
        ]
        portable = decode_document_program_contract(
            str(getattr(roots[0], PROP_PROGRAM_CONTRACT, "") or ""),
            pack,
            expected_program_id=program_id,
            expected_revision=str(prepared["revision"]),
        )
        assert portable["inputs"]["support"] == {
            "document_uid": str(document.Uid),
            "object_name": str(support.Name),
        }
        assert accepted["live_outputs"]["SupportedBoss"]["object_name"]
        return {
            "body": str(body.Name),
            "support": str(support.Name),
            "map_mode": str(resolved_support["map_mode"]),
            "source_retained": portable["source"] == source,
        }
    finally:
        App.closeDocument(document.Name)


def _exercise_native_sketch_history(root: Path, pack) -> dict:
    """Publish a three-profile loft as one source-owned Design operation."""

    import FreeCAD as App
    import PartDesign
    from pathlib import Path as LocalPath

    document = App.newDocument("PartDesignNativeSketchHistory")
    service = _Service(document, root)
    capture = {
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
    source = """\
W = inputs['overall_width_mm']
H = inputs['overall_height_mm']
T = inputs['stock_thickness_mm']
TW = inputs['top_width_mm']
SW = inputs['slot_width_mm']
SD = inputs['slot_depth_mm']
SP = inputs['slot_pitch_mm']
BH = inputs['bevel_height_mm']

r = SW / 2.0
wall = SD - r
c_right = SP / 2.0
c_left = -SP / 2.0
shoulder = (W - TW) / 2.0
bevel_half_width = W/2.0 - shoulder*(BH/H)

def blade_outline_sketch(z_offset, edge_y, edge_half_width, prefix, sketch_label):
    cutting_edge = api.line([-edge_half_width, edge_y], [edge_half_width, edge_y], name=prefix+'_CuttingEdge')
    right_shoulder = api.line([edge_half_width, edge_y], [TW/2.0, H], name=prefix+'_RightShoulder')
    top_right = api.line([TW/2.0, H], [c_right+r, H], name=prefix+'_TopRight')
    right_slot_wall_r = api.line([c_right+r, H], [c_right+r, H-wall], name=prefix+'_RightSlotWallR')
    right_slot_root = api.arc([c_right+r, H-wall], [c_right, H-SD], [c_right-r, H-wall], name=prefix+'_RightSlotRoot')
    right_slot_wall_l = api.line([c_right-r, H-wall], [c_right-r, H], name=prefix+'_RightSlotWallL')
    top_bridge = api.line([c_right-r, H], [c_left+r, H], name=prefix+'_TopBridge')
    left_slot_wall_r = api.line([c_left+r, H], [c_left+r, H-wall], name=prefix+'_LeftSlotWallR')
    left_slot_root = api.arc([c_left+r, H-wall], [c_left, H-SD], [c_left-r, H-wall], name=prefix+'_LeftSlotRoot')
    left_slot_wall_l = api.line([c_left-r, H-wall], [c_left-r, H], name=prefix+'_LeftSlotWallL')
    top_left = api.line([c_left-r, H], [-TW/2.0, H], name=prefix+'_TopLeft')
    left_shoulder = api.line([-TW/2.0, H], [-edge_half_width, edge_y], name=prefix+'_LeftShoulder')
    return api.sketch(
        [
            cutting_edge, right_shoulder, top_right,
            right_slot_wall_r, right_slot_root, right_slot_wall_l,
            top_bridge,
            left_slot_wall_r, left_slot_root, left_slot_wall_l,
            top_left, left_shoulder
        ],
        [],
        plane='XY',
        z_offset_mm=z_offset,
        require_fully_constrained=False,
        require_closed_profile=True,
        label=sketch_label
    )

lower_profile = blade_outline_sketch(-T/2.0, BH, bevel_half_width, 'Lower', 'Lower Face Blade Sketch')
cutting_profile = blade_outline_sketch(0.0, 0.0, W/2.0, 'Mid', 'Cutting Edge Blade Sketch')
upper_profile = blade_outline_sketch(T/2.0, BH, bevel_half_width, 'Upper', 'Upper Face Blade Sketch')

blade = api.loft(
    [lower_profile, cutting_profile, upper_profile],
    operation='new_solid',
    ruled=True,
    closed=False,
    refine=True,
    label='Sketch Lofted Double Bevel Blade'
)

steel = api.material('90bbd8ef-8623-4d78-b3bf-e0bdb9b74dd3', require_physical_properties=['Density','YoungsModulus','PoissonRatio'])

result = {
    'UtilityBlade': api.body(blade, material=steel, label='Utility Blade 38755A29')
}
"""
    input_schema = {
        "type": "object",
        "properties": {
            name: {"type": "number"}
            for name in (
                "overall_width_mm",
                "overall_height_mm",
                "stock_thickness_mm",
                "top_width_mm",
                "slot_width_mm",
                "slot_depth_mm",
                "slot_pitch_mm",
                "bevel_height_mm",
            )
        },
        "required": [
            "overall_width_mm",
            "overall_height_mm",
            "stock_thickness_mm",
            "top_width_mm",
            "slot_width_mm",
            "slot_depth_mm",
            "slot_pitch_mm",
            "bevel_height_mm",
        ],
        "additionalProperties": False,
    }
    inputs = {
        "overall_width_mm": 61.0,
        "overall_height_mm": 18.0,
        "stock_thickness_mm": 0.65,
        "top_width_mm": 38.0,
        "slot_width_mm": 5.0,
        "slot_depth_mm": 6.0,
        "slot_pitch_mm": 20.0,
        "bevel_height_mm": 2.0,
    }
    create = _capture(
        capture,
        operation="create_program",
        arguments={
            "program_name": "Native Three-Sketch Loft",
            "source": source,
            "input_schema": input_schema,
            "inputs": inputs,
            "expected_outputs": [{"name": "UtilityBlade", "type": "solid"}],
        },
    )
    try:
        _prepared, publication, accepted = _run_candidate(create, service)
        native = publication["native_history"]
        assert native["available"] is True
        assert native["strategy"] == "design_program_operation"
        operation = document.getObject(native["operation_object"])
        assert operation is not None
        assert operation.TypeId == "PartDesign::DesignScriptOperation"
        body_name = native["body_objects"]["UtilityBlade"]
        body = document.getObject(body_name)
        assert body is not None and body.TypeId == "PartDesign::Body"
        stable_output = accepted["live_outputs"]["UtilityBlade"]["object_name"]
        assert stable_output
        assert body.Tip is not None
        assert body.Tip.TypeId == "PartDesign::DesignBodyPublication"
        assert body.Tip.CurrentState.Operation is operation
        assert list(operation.ProgramOutputKeys) == ["UtilityBlade"]
        assert list(operation.ProgramOutputTypes) == ["solid"]
        assert not [
            obj
            for obj in document.Objects
            if obj.TypeId == "Sketcher::SketchObject"
        ]
        document.recompute()
        assert body.Shape.isValid() and body.Shape.Volume > 0.0
        published = document.getObject(stable_output)
        assert published is not None
        assert published.Shape.isValid()
        assert abs(body.Shape.Volume - published.Shape.Volume) <= max(
            1.0e-7,
            abs(published.Shape.Volume) * 1.0e-9,
        )
        root_object = next(
            obj for obj in document.Objects if role_of(obj) == ROLE_MODEL
        )
        document.openTransaction("Remove VibeScript Design operation")
        removed = PartDesign.removeDesignOperation(operation)
        document.commitTransaction()
        assert removed == [body_name]
        assert document.getObject(native["operation_object"]) is None
        assert document.getObject(body_name) is None
        assert not [
            obj
            for obj in document.Objects
            if obj.TypeId == "Sketcher::SketchObject"
        ]

        repair = _capture(
            {
                **capture,
                "live_programs": _live_programs(document, "partdesign"),
            },
            operation="reconfigure_program",
            arguments={
                "program_id": _prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "source": source,
                "input_schema": input_schema,
                "inputs": inputs,
                "expected_outputs": [
                    {"name": "UtilityBlade", "type": "solid"}
                ],
            },
        )
        repaired_prepared, repaired_publication, repaired = _run_candidate(
            repair,
            service,
        )
        assert repaired_prepared["native_history_repair_required"] is True
        assert repaired["working_revision"] == accepted["working_revision"]
        assert repaired["live_outputs"]["UtilityBlade"]["object_name"] == stable_output
        repaired_operation = document.getObject(
            repaired_publication["native_history"]["operation_object"]
        )
        assert repaired_operation is not None
        assert repaired_operation.TypeId == "PartDesign::DesignScriptOperation"
        repaired_body_name = repaired_publication["native_history"]["body_objects"][
            "UtilityBlade"
        ]
        repaired_body = document.getObject(repaired_body_name)
        assert repaired_body is not None
        assert repaired_body.Tip.TypeId == "PartDesign::DesignBodyPublication"
        assert repaired_body.Tip.CurrentState.Operation is repaired_operation
        assert not [
            obj
            for obj in document.Objects
            if obj.TypeId == "Sketcher::SketchObject"
        ]
        document.recompute()
        assert repaired_body.Shape.isValid()
        portable = decode_document_program_contract(
            str(getattr(root_object, PROP_PROGRAM_CONTRACT, "") or ""),
            pack,
            expected_program_id=str(_prepared["program_id"]),
            expected_revision=str(repaired_operation.ProgramRevision),
        )
        assert portable["source"] == source
        return {
            "body": repaired_body_name,
            "operation": str(repaired_operation.Name),
            "body_publication": str(repaired_body.Tip.Name),
            "source_owned_profiles": 3,
            "unchanged_revision_repaired": True,
        }
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
        "'AxisX': {'selection': {'type':'frame','origin':[0,0,0],"
        "'axis_direction':[1,0,0],'x_direction':[0,0,1]},"
        "'description':'Shaft rotation axis'},"
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
    assembly = document.addObject("App::Link", "AssemblyConsumer")
    assembly.LinkedObject = published
    specifications = (
        ("Fem::FeaturePython", "FemConsumer"),
        ("Path::FeaturePython", "CamConsumer"),
        ("TechDraw::DrawViewPart", "TechDrawConsumer"),
        ("Robot::RobotObject", "RobotConsumer"),
        ("Inspection::Feature", "InspectionConsumer"),
    )
    consumers = [whole, assembly]
    for type_id, name in specifications:
        consumer = document.addObject(type_id, name)
        if name == "TechDrawConsumer":
            consumer.Source = [published]
        elif name == "InspectionConsumer":
            consumer.Actual = published
            consumer.Nominals = [published]
        else:
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
        elif current.Name == "AssemblyConsumer":
            assert current.LinkedObject is published
        elif current.Name == "TechDrawConsumer":
            assert list(current.Source) == [published]
        elif current.Name == "InspectionConsumer":
            assert current.Actual is published
            assert list(current.Nominals) == [published]
        else:
            assert current.VibeCADTestSource is published


def _linked_object(value):
    if (
        isinstance(value, (tuple, list))
        and len(value) == 2
        and hasattr(value[0], "TypeId")
    ):
        return value[0]
    return value


def _assert_partdesign_timeline_graph(
    document,
    program_id: str,
    publication_name: str,
    *,
    expected_state: dict[str, object] | None = None,
) -> dict[str, object]:
    published = document.getObject(publication_name)
    assert published is not None
    roots = [
        obj
        for obj in document.Objects
        if role_of(obj) == ROLE_MODEL
        and str(getattr(obj, "VibeCADScriptedModelId", "") or "")
        == program_id
    ]
    assert len(roots) == 1
    root = roots[0]
    operations = [
        obj
        for obj in document.Objects
        if obj.TypeId == "PartDesign::DesignScriptOperation"
        and str(getattr(obj, "ProgramId", "") or "") == program_id
    ]
    assert len(operations) == 1
    operation = operations[0]
    bodies = [
        obj
        for obj in document.Objects
        if obj.TypeId == "PartDesign::Body"
        and role_of(obj) == ROLE_IMPLEMENTATION
        and str(getattr(obj, "VibeCADScriptedModelId", "") or "")
        == program_id
    ]
    assert len(bodies) == 1
    body = bodies[0]
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None
    timeline_operations = list(timeline.Operations)
    assert timeline_operations.count(operation) == 1
    assert str(operation.VibeCADTimelineRole) == "operation"
    assert str(operation.ResultOperation) == "Program Outputs"
    assert str(operation.ProgramObjectName) == str(root.Name)
    assert str(operation.ProgramId) == program_id
    assert list(operation.ProgramOutputKeys) == ["Part"]
    assert list(operation.ProgramOutputTypes) == ["solid"]
    assert list(operation.ScriptOutputKeys) == ["Part"]
    assert list(operation.OutputBodyIds) == [str(body.VibeCADBodyId)]
    assert str(operation.VibeCADTimelineEditCommand) == (
        "VibeCAD_EditScriptedModel"
    )
    assert str(operation.VibeCADTimelineDeleteCommand) == (
        "VibeCAD_DeleteScriptedModel"
    )

    body_publication = body.Tip
    assert body_publication is not None
    assert body_publication.TypeId == "PartDesign::DesignBodyPublication"
    assert body_publication in list(body.Group)
    state = body_publication.CurrentState
    assert state is not None
    assert state.TypeId == "PartDesign::DesignBodyState"
    assert state.Operation is operation
    assert str(state.BodyId) == str(body.VibeCADBodyId)
    assert str(body_publication.BodyId) == str(body.VibeCADBodyId)
    assert body.Shape.isValid() and published.Shape.isValid()
    assert abs(body.Shape.Volume - published.Shape.Volume) <= max(
        1.0e-7,
        abs(published.Shape.Volume) * 1.0e-9,
    )

    # Worker-side Pad/Pocket/Sketch objects are an execution detail. The
    # editable source program is the one semantic History operation, and only
    # its stable physical Body result is retained in the authoring document.
    generated_legacy_features = [
        obj
        for obj in document.Objects
        if obj.TypeId
        in {
            "Sketcher::SketchObject",
            "PartDesign::Pad",
            "PartDesign::Pocket",
        }
        and str(getattr(obj, "VibeCADScriptedModelId", "") or "")
        == program_id
    ]
    assert generated_legacy_features == []

    pack = get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None
    portable = decode_document_program_contract(
        str(getattr(root, PROP_PROGRAM_CONTRACT, "") or ""),
        pack,
        expected_program_id=program_id,
        expected_revision=str(operation.ProgramRevision),
    )

    graph_state = {
        "root": str(root.Name),
        "publication": str(published.Name),
        "operation": str(operation.Name),
        "operation_id": str(operation.OperationId),
        "body": str(body.Name),
        "body_id": str(body.VibeCADBodyId),
        "body_publication": str(body_publication.Name),
        "body_state": str(state.Name),
        "body_state_id": str(state.BodyStateId),
        "source": str(portable["source"]),
    }
    if expected_state is not None:
        assert graph_state == expected_state
    return graph_state


def _exercise_lifecycle(root: Path, pack) -> dict:
    import FreeCAD as App
    from pathlib import Path as LocalPath

    document = App.newDocument("PartDesignVibeScriptV2")
    document.UndoMode = True
    document.commitTransaction()
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
    lifecycle_source = _source()
    create = _capture(
        base_capture,
        operation="create_program",
        arguments={
            "program_name": "Part Design v2 Lifecycle",
            "source": lifecycle_source,
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
    published_frame = publication["interfaces"]["Top"]["resolved"][
        "connector_frame"
    ]
    assert published_frame["schema"] == "vibecad-connector-frame-v1"
    assert len(published_frame["matrix"]) == 16
    assert math.isclose(published_frame["origin_mm"][2], 12.0, abs_tol=1.0e-7)
    top = resolve_interface(service, published, "Top")
    assert top["publication_name"] == identity
    assert top["interface_name"] == "Top"
    assert top["connector_frame"] == published_frame
    origin = resolve_interface(service, published, "Origin")["connector_frame"]
    assert origin["origin_mm"] == [0.0, 0.0, 0.0]
    assert origin["axis_direction"] == [0.0, 0.0, 1.0]
    axis_x = resolve_interface(service, published, "AxisX")["connector_frame"]
    assert all(
        math.isclose(actual, expected, abs_tol=1.0e-12)
        for actual, expected in zip(
            axis_x["axis_direction"],
            [1.0, 0.0, 0.0],
            strict=True,
        )
    )
    assert all(
        math.isclose(actual, expected, abs_tol=1.0e-12)
        for actual, expected in zip(
            axis_x["x_direction"],
            [0.0, 0.0, 1.0],
            strict=True,
        )
    )
    created_state = _assert_partdesign_timeline_graph(
        document,
        program_id,
        identity,
    )
    catalog = {
        item["object_name"]: item
        for item in open_component_candidates(document)
        if dict(item.get("authoring_source") or {}).get("source_id") == program_id
    }
    assert identity in catalog
    assert str(created_state["body"]) not in catalog
    assert catalog[identity]["assembly_contract"]["solid_count"] == 1
    assert catalog[identity]["published_interfaces"] == ["AxisX", "Origin", "Top"]
    axis_descriptor = next(
        item
        for item in catalog[identity]["interfaces"]
        if item["name"] == "AxisX"
    )
    assert axis_descriptor["connector_eligible"] is True
    top_descriptor = next(
        item
        for item in catalog[identity]["interfaces"]
        if item["name"] == "Top"
    )
    assert top_descriptor["connector_eligible"] is True
    assert top_descriptor["frame"] == published_frame
    assert "label='Base Extrusion'" in str(created_state["source"])
    document.undo()
    assert document.getObject(identity) is None
    assert document.getObject(str(created_state["body"])) is None
    assert document.getObject(str(created_state["operation"])) is None
    document.redo()
    _assert_partdesign_timeline_graph(
        document,
        program_id,
        identity,
        expected_state=created_state,
    )
    published = document.getObject(identity)
    assert published is not None

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

    edited_source = lifecycle_source.replace(
        "label='Base Extrusion'",
        "label='Primary Extrusion'",
        1,
    )
    assert edited_source != lifecycle_source
    edit = _capture(
        base_capture,
        operation="edit_source",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "source": edited_source,
        },
    )
    _edited, edit_publication, accepted = _run_candidate(edit, service)
    assert edit_publication["created_objects"] == []
    assert accepted["live_outputs"]["Part"]["object_name"] == identity
    edited_state = _assert_partdesign_timeline_graph(
        document,
        program_id,
        identity,
    )
    assert "label='Primary Extrusion'" in str(edited_state["source"])
    document.undo()
    _assert_partdesign_timeline_graph(
        document,
        program_id,
        identity,
        expected_state=created_state,
    )
    document.redo()
    _assert_partdesign_timeline_graph(
        document,
        program_id,
        identity,
        expected_state=edited_state,
    )
    published = document.getObject(identity)
    assert published is not None

    failing_source = edited_source.replace(
        "z_offset_mm=inputs['height']",
        "z_offset_mm=inputs['height'] + 100",
        1,
    )
    assert failing_source != edited_source
    failed = _capture(
        base_capture,
        operation="edit_source",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "source": failing_source,
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
            "source": edited_source,
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
    reopened.UndoMode = True
    service.document = reopened
    reopened_published = reopened.getObject(identity)
    assert reopened_published is not None
    reopened_bodies = [
        obj
        for obj in reopened.Objects
        if obj.TypeId == "PartDesign::Body"
        and role_of(obj) == ROLE_IMPLEMENTATION
        and str(getattr(obj, "VibeCADScriptedModelId", "") or "") == program_id
    ]
    assert len(reopened_bodies) == 1
    reopened_body = reopened_bodies[0]
    assert reopened_body.Tip is not None
    assert reopened_body.Tip.TypeId == "PartDesign::DesignBodyPublication"
    reopened_graph = _assert_partdesign_timeline_graph(
        reopened,
        program_id,
        identity,
    )
    assert "through_all=True" in str(reopened_graph["source"])
    assert not [
        obj
        for obj in reopened.Objects
        if obj.TypeId
        in {
            "Sketcher::SketchObject",
            "PartDesign::Pad",
            "PartDesign::Pocket",
        }
        and str(getattr(obj, "VibeCADScriptedModelId", "") or "")
        == program_id
    ]
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
    reopened.commitTransaction()
    reopened_root = next(
        obj
        for obj in reopened.Objects
        if role_of(obj) == ROLE_MODEL
        and str(getattr(obj, "VibeCADScriptedModelId", "") or "")
        == program_id
    )
    pre_delete_state = _assert_partdesign_timeline_graph(
        reopened,
        program_id,
        identity,
    )
    implementation_names = {
        str(pre_delete_state[name])
        for name in (
            "operation",
            "body",
            "body_publication",
            "body_state",
        )
    }
    implementation_names.add(str(reopened_root.Name))
    body_containment_names: set[str] = set()

    def collect_body_containment(obj) -> None:
        name = str(getattr(obj, "Name", "") or "")
        if not name or name in body_containment_names:
            return
        body_containment_names.add(name)
        for child in list(getattr(obj, "Group", []) or []):
            collect_body_containment(child)

    collect_body_containment(reopened.getObject(str(pre_delete_state["body"])))
    deletion = prepare_delete(delete_capture)
    deletion_publication = adapter.delete(service, deletion, deletion["manifest"])
    deleted = finish_delete(deletion, deletion_publication)
    assert deleted["artifacts_deleted"] is True
    assert reopened.getObject(identity) is None
    assert not {
        name for name in implementation_names if reopened.getObject(name) is not None
    }
    assert not {
        name
        for name in body_containment_names
        if reopened.getObject(name) is not None
    }
    assert not LocalPath(deletion["program_directory"]).exists()
    reopened.undo()
    _assert_partdesign_timeline_graph(
        reopened,
        program_id,
        identity,
        expected_state=pre_delete_state,
    )
    reopened.redo()
    assert reopened.getObject(identity) is None
    assert not {
        name for name in implementation_names if reopened.getObject(name) is not None
    }
    assert not {
        name
        for name in body_containment_names
        if reopened.getObject(name) is not None
    }
    # Recover the exact partial state produced by an interrupted historical
    # deletion: the program container and its private publication target are
    # gone, while the stable link and native Design operation remain. Both the
    # editor and History delete commands must be able to finish this lifecycle.
    reopened.undo()
    restored_root = next(
        obj
        for obj in reopened.Objects
        if role_of(obj) == ROLE_MODEL
        and str(getattr(obj, "VibeCADScriptedModelId", "") or "")
        == program_id
    )
    reopened.openTransaction("Simulate interrupted VibeScript deletion")
    reopened.removeObject(str(restored_root.Name))
    reopened.commitTransaction()
    assert reopened.getObject(str(restored_root.Name)) is None
    assert reopened.getObject(identity) is not None
    assert reopened.getObject(str(pre_delete_state["operation"])) is not None
    rootless_deletion = _delete_partdesign_program(
        reopened,
        {"program_id": program_id},
    )
    assert rootless_deletion["ok"] is True
    assert reopened.getObject(identity) is None
    assert not [
        obj
        for obj in reopened.Objects
        if str(getattr(obj, "ProgramId", "") or "") == program_id
        or str(getattr(obj, "VibeCADScriptedModelId", "") or "")
        == program_id
        or str(getattr(obj, "VibeCADVibeScriptProgramId", "") or "")
        == program_id
    ]
    App.closeDocument(reopened.Name)
    return {
        "program_id": program_id,
        "stable_output_identity": identity,
        "failed_candidate_retained": failed_prepared["revision"],
        "unsafe_reference_rejected": True,
        "save_reopen_regenerated": True,
        "rootless_delete_recovered": True,
        "deleted": [item["object_name"] for item in deleted["deleted_objects"]],
    }


def _exercise_component_occurrence(root: Path, pack) -> dict:
    """Publish, reopen, and delete one reusable linked occurrence."""

    import FreeCAD as App
    import Part
    from pathlib import Path as LocalPath

    document = App.newDocument("PartDesignComponentOccurrence")
    document.UndoMode = True
    source = document.addObject("Part::Feature", "MotorDefinition")
    source.Label = "Catalog motor"
    source.Shape = Part.makeBox(20, 10, 8)
    source_name = str(source.Name)
    document.recompute()
    service = _Service(document, root)
    base_capture = {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": service.provider_document_revision(),
        "document_objects": [
            {
                "name": str(source.Name),
                "label": str(source.Label),
                "type_id": str(source.TypeId),
            }
        ],
        "surface": resolve_modeling_surface(
            "PartDesignWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(LocalPath(App.getHomePath()).resolve()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }
    input_schema = {
        "type": "object",
        "properties": {
            "motor": {
                "type": "object",
                "x-vibecad-reference": True,
                "properties": {
                    "document_uid": {"type": "string"},
                    "object_name": {"type": "string"},
                },
                "required": ["document_uid", "object_name"],
                "additionalProperties": False,
            }
        },
        "required": ["motor"],
        "additionalProperties": False,
    }
    inputs = {
        "motor": {
            "document_uid": str(document.Uid),
            "object_name": str(source.Name),
        }
    }
    initial_source = (
        "motor = api.component(inputs['motor'], label='Motor occurrence')\n"
        "result = {'Motor': motor}\n"
    )
    active_document = document
    try:
        prepared, _publication, accepted = _run_candidate(
            _capture(
                base_capture,
                operation="create_program",
                arguments={
                    "program_name": "Placed motor",
                    "source": initial_source,
                    "input_schema": input_schema,
                    "inputs": inputs,
                    "expected_outputs": [
                        {"name": "Motor", "type": "component_link"}
                    ],
                },
            ),
            service,
        )
        occurrence_name = accepted["live_outputs"]["Motor"]["object_name"]
        occurrence = document.getObject(occurrence_name)
        assert occurrence is not None
        assert occurrence.TypeId == "App::Link"
        assert occurrence.LinkedObject is source
        assert occurrence.Placement.Base == App.Vector(0, 0, 0)

        import Assembly

        del Assembly
        mechanism = document.addObject(
            "Assembly::AssemblyObject",
            "ComponentOccurrenceConsumer",
        )
        mechanism.addObject(occurrence)
        assert mechanism in list(occurrence.InList)

        occurrence.Placement = App.Placement(
            App.Vector(37, 4, 2),
            App.Rotation(App.Vector(0, 0, 1), 15),
        )
        label_edit = initial_source.replace("Motor occurrence", "Drive motor")
        _prepared, _publication, accepted = _run_candidate(
            _capture(
                base_capture,
                operation="edit_source",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": accepted["working_revision"],
                    "source": label_edit,
                },
            ),
            service,
        )
        assert document.getObject(occurrence_name) is occurrence
        assert math.isclose(occurrence.Placement.Base.x, 37.0, abs_tol=1.0e-9)
        assert math.isclose(occurrence.Placement.Base.y, 4.0, abs_tol=1.0e-9)

        moved_source = label_edit.replace(
            "inputs['motor'], label=",
            "inputs['motor'], placement=[0, 0, 0], label=",
        )
        _prepared, _publication, accepted = _run_candidate(
            _capture(
                base_capture,
                operation="edit_source",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": accepted["working_revision"],
                    "source": moved_source,
                },
            ),
            service,
        )
        assert occurrence.Placement.Base == App.Vector(0, 0, 0)
        assert mechanism in list(occurrence.InList)
        candidate = next(
            item
            for item in open_component_candidates(document)
            if item["object_name"] == occurrence_name
        )
        assert candidate["kind"] == "occurrence"
        assert candidate["reference"]["object_name"] == occurrence_name
        mechanism.removeObject(occurrence)
        assert mechanism not in list(occurrence.InList)
        document.removeObject(mechanism.Name)

        save_path = root / "partdesign-component-occurrence.FCStd"
        program_id = str(prepared["program_id"])
        accepted_revision = str(accepted["working_revision"])
        document.recompute()
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        reopened = App.openDocument(str(save_path))
        active_document = reopened
        reopened_occurrence = reopened.getObject(occurrence_name)
        reopened_source = reopened.getObject(source_name)
        assert reopened_occurrence is not None
        assert reopened_source is not None
        assert reopened_occurrence.LinkedObject is reopened_source
        assert reopened_occurrence.Placement.Base == App.Vector(0, 0, 0)

        reopened_service = _Service(reopened, root)
        delete_capture = _capture(
            {
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
            },
            operation="delete_program",
            arguments={
                "program_id": program_id,
                "expected_revision": accepted_revision,
                "reason": "Component occurrence lifecycle complete",
            },
        )
        adapter = get_domain_adapter("partdesign")
        assert adapter is not None
        deletion = prepare_delete(delete_capture)
        deletion_publication = adapter.delete(
            reopened_service,
            deletion,
            deletion["manifest"],
        )
        assert finish_delete(deletion, deletion_publication)["ok"] is True
        assert reopened.getObject(occurrence_name) is None
        assert reopened.getObject(source_name) is reopened_source
        return {
            "occurrence": occurrence_name,
            "source": source_name,
            "live_placement_preserved": True,
            "authored_placement_update_applied": True,
            "assembly_containment_survived_rebuild": True,
            "save_reopen_preserved_link": True,
            "deletion_preserved_definition": True,
        }
    finally:
        if str(active_document.Name) in App.listDocuments():
            App.closeDocument(active_document.Name)


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


def _exercise_output_local_interfaces_and_ownership_repair(root: Path, pack) -> dict:
    """Prove interfaces are local and stale advisory Body claims self-repair."""

    import FreeCAD as App
    from pathlib import Path as LocalPath

    document = App.newDocument("PartDesignLocalInterfaces")
    try:
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
            "size = inputs['size']\n"
            "left = api.box(size, size, size, origin=[-2*size,0,0], label='Left')\n"
            "right = api.box(size, size, size, origin=[size,0,0], label='Right')\n"
            "axis = {'RotationAxis': {'selection': {'type': 'origin'}}}\n"
            "result = {\n"
            " 'Left': api.body(left, interfaces=axis, label='Left Body'),\n"
            " 'Right': api.body(right, interfaces=axis, label='Right Body'),\n"
            "}\n"
        )
        create = _capture(
            base_capture,
            operation="create_program",
            arguments={
                "program_name": "Output-local interface ownership",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "size": {"type": "number", "exclusiveMinimum": 0}
                    },
                    "required": ["size"],
                    "additionalProperties": False,
                },
                "inputs": {"size": 4.0},
                "expected_outputs": [
                    {"name": "Left", "type": "solid"},
                    {"name": "Right", "type": "solid"},
                ],
            },
        )
        prepared, publication, accepted = _run_candidate(create, service)
        table = publication["interfaces"]
        assert table["_schema"] == "vibecad-published-interfaces-v2"
        assert "RotationAxis" not in table
        assert set(table["_outputs"]) == {"Left", "Right"}
        for output_name in ("Left", "Right"):
            published = document.getObject(
                accepted["live_outputs"][output_name]["object_name"]
            )
            resolved = resolve_interface(service, published, "RotationAxis")
            assert resolved["output_key"] == output_name
        catalog = {
            item["object_name"]: item
            for item in open_component_candidates(document)
        }
        for output_name in ("Left", "Right"):
            published_name = accepted["live_outputs"][output_name]["object_name"]
            assert catalog[published_name]["published_interfaces"] == [
                "RotationAxis"
            ]

        stale = document.addObject("PartDesign::Body", "StaleScriptBody")
        stale_name = str(stale.Name)
        tag_object(
            stale,
            role=ROLE_IMPLEMENTATION,
            engine="vibescript:partdesign",
            model_id=prepared["program_id"],
            output_key="Left",
            revision=accepted["working_revision"],
        )
        update = _capture(
            base_capture,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "patch": {"size": 5.0},
            },
        )
        _updated, updated_publication, _accepted = _run_candidate(update, service)
        repairs = updated_publication["native_history"]["ownership_repairs"]
        assert repairs == [
            {
                "object_name": stale_name,
                "claimed_output": "Left",
                "authoritative_object": updated_publication["native_history"][
                    "body_objects"
                ]["Left"],
            }
        ]
        assert document.getObject(stale_name) is None
        return {
            "outputs": ["Left", "Right"],
            "shared_local_interface": "RotationAxis",
            "repaired_body": stale_name,
        }
    finally:
        App.closeDocument(document.Name)


def _exercise_physical_material_publication(root: Path, pack) -> dict:
    """Publish one shared-catalog ShapeMaterial and preserve it on regeneration."""

    import FreeCAD as App
    import Materials
    from pathlib import Path as LocalPath

    cards = sorted(
        list(Materials.MaterialManager().Materials.values()),
        key=lambda card: (str(card.Name), str(card.UUID)),
    )
    assert len(cards) >= 2
    card = cards[0]
    drift_card = next(
        candidate
        for candidate in cards[1:]
        if str(candidate.UUID) != str(card.UUID)
    )
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
        "result = {'Coupon': api.body(shape, material=card, "
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
        prepared, publication, accepted = _run_candidate(create, service)
        document.recompute()
        identity = accepted["live_outputs"]["Coupon"]["object_name"]
        published = document.getObject(identity)
        body = document.getObject(
            publication["native_history"]["body_objects"]["Coupon"]
        )
        assert published is not None
        assert body is not None
        assert str(published.ShapeMaterial.UUID) == str(card.UUID)
        assert str(body.ShapeMaterial.UUID) == str(card.UUID)
        baseline_uuid = str(
            getattr(published, PROP_PARTDESIGN_MATERIAL_BASELINE).UUID
        )
        initial_volume = float(body.Shape.Volume)

        # A user or another workbench may change presentation independently.
        # That live drift must never make the owning VibeScript source
        # impossible to edit. The newly accepted source remains authoritative.
        _set_physical_material_preserving_view(published, drift_card)
        assert str(published.ShapeMaterial.UUID) == str(drift_card.UUID)
        edited_source = source.replace(
            "api.box(inputs['size'], 4, 2",
            "api.box(inputs['size'], 5, 2",
            1,
        )
        assert edited_source != source
        source_edit = _capture(
            base_capture,
            operation="edit_source",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "source": edited_source,
            },
        )
        _edited, edit_publication, edited = _run_candidate(
            source_edit,
            service,
        )
        document.recompute()
        published = document.getObject(identity)
        body = document.getObject(
            edit_publication["native_history"]["body_objects"]["Coupon"]
        )
        assert published is not None
        assert body is not None
        assert edit_publication["created_objects"] == []
        assert edited["live_outputs"]["Coupon"]["object_name"] == identity
        assert float(body.Shape.Volume) > initial_volume
        assert str(published.ShapeMaterial.UUID) == str(card.UUID)
        assert str(body.ShapeMaterial.UUID) == str(card.UUID)

        # Removing a source-owned material is an ordinary complete-source edit,
        # even if the live presentation drifted again after the previous build.
        _set_physical_material_preserving_view(published, drift_card)
        assert str(published.ShapeMaterial.UUID) == str(drift_card.UUID)
        material_removed_source = edited_source.replace(
            "card = api.material(inputs['material_uuid'])\n",
            "",
            1,
        ).replace(
            "api.body(shape, material=card, ",
            "api.body(shape, ",
            1,
        )
        assert material_removed_source != edited_source
        remove_material = _capture(
            base_capture,
            operation="edit_source",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": edited["working_revision"],
                "source": material_removed_source,
            },
        )
        _removed, remove_publication, removed = _run_candidate(
            remove_material,
            service,
        )
        document.recompute()
        published = document.getObject(identity)
        body = document.getObject(
            remove_publication["native_history"]["body_objects"]["Coupon"]
        )
        assert published is not None
        assert body is not None
        assert remove_publication["created_objects"] == []
        assert removed["live_outputs"]["Coupon"]["object_name"] == identity
        assert str(published.ShapeMaterial.UUID) == baseline_uuid
        assert str(body.ShapeMaterial.UUID) == baseline_uuid

        update = _capture(
            base_capture,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": removed["working_revision"],
                "patch": {"size": 9.0},
            },
        )
        _updated, update_publication, updated = _run_candidate(update, service)
        document.recompute()
        published = document.getObject(identity)
        body = document.getObject(
            update_publication["native_history"]["body_objects"]["Coupon"]
        )
        assert published is not None
        assert body is not None
        assert update_publication["created_objects"] == []
        assert updated["live_outputs"]["Coupon"]["object_name"] == identity
        assert float(body.Shape.Volume) > initial_volume
        assert str(published.ShapeMaterial.UUID) == baseline_uuid
        assert str(body.ShapeMaterial.UUID) == baseline_uuid
        return {
            "object_name": identity,
            "source_material_uuid": str(card.UUID),
            "restored_baseline_uuid": baseline_uuid,
            "external_drift_reconciled": True,
            "source_material_removed": True,
            "regenerated_volume_mm3": float(body.Shape.Volume),
        }
    finally:
        App.closeDocument(document.Name)


def _exercise_direct_solid_adoption_label(root: Path, pack) -> dict:
    """A direct solid keeps the operation label inside its editable Body."""

    import FreeCAD as App
    from pathlib import Path as LocalPath

    document = App.newDocument("PartDesignDirectSolidLabel")
    service = _Service(document, root)
    source = (
        "lower = api.wire([api.circle_3d(3, center=[0,0,0])])\n"
        "upper = api.wire([api.circle_3d(2, center=[0,0,5])])\n"
        "finished = api.loft([lower, upper], operation='new_solid', "
        "ruled=True, label='Finished Direct Loft')\n"
        "result = {'Part': api.body(finished, label='Published Direct Loft')}\n"
    )
    create = _capture(
        {
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
        },
        operation="create_program",
        arguments={
            "program_name": "Direct Solid Label",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "inputs": {},
            "expected_outputs": [{"name": "Part", "type": "solid"}],
        },
    )
    try:
        _prepared, publication, accepted = _run_candidate(create, service)
        body_name = publication["native_history"]["body_objects"]["Part"]
        body = document.getObject(body_name)
        assert body is not None and body.TypeId == "PartDesign::Body"
        assert str(body.Label) == "Published Direct Loft"
        assert body.Tip is not None
        assert body.Tip.TypeId == "PartDesign::DesignBodyPublication"
        operation = document.getObject(
            publication["native_history"]["operation_object"]
        )
        assert operation is not None
        assert body.Tip.CurrentState.Operation is operation
        assert publication["live_outputs"]["Part"]["partdesign_data"][
            "tip_label"
        ] == "Finished Direct Loft"
        assert not [
            obj
            for obj in document.Objects
            if obj.TypeId == "PartDesign::Feature"
            and str(getattr(obj, "VibeCADScriptedModelId", "") or "")
            == str(_prepared["program_id"])
        ]
        published = document.getObject(
            accepted["live_outputs"]["Part"]["object_name"]
        )
        assert published is not None and published.Shape.isValid()
        return {
            "body": str(body.Name),
            "operation": str(operation.Name),
            "body_publication": str(body.Tip.Name),
            "source_feature_label": "Finished Direct Loft",
        }
    finally:
        App.closeDocument(document.Name)


def _exercise_saved_source_compatibility(root: Path, pack) -> dict:
    """Replay an unchanged saved alias without exposing it to canonical source."""

    import FreeCAD as App
    from pathlib import Path as LocalPath

    document = App.newDocument("PartDesignSavedSourceCompatibility")
    service = _Service(document, root)
    program_id = "abcdefabcdefabcdefabcdefabcdefab"
    source = (
        "profile = api.sketch([api.circle([0,0], inputs['radius'])])\n"
        "feature = api.pad(profile, inputs['height'], label='Saved Feature')\n"
        "result = {'Part': api.body(feature, label='Saved Part')}\n"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "radius": {"type": "number", "exclusiveMinimum": 0},
            "height": {"type": "number", "exclusiveMinimum": 0},
        },
        "required": ["radius", "height"],
        "additionalProperties": False,
    }
    inputs = {"radius": 4.0, "height": 5.0}
    expected_outputs = [{"name": "Part", "type": "solid"}]
    revision = program_revision(
        domain=pack.domain,
        source=source,
        input_schema=input_schema,
        inputs=inputs,
        expected_outputs=expected_outputs,
    )
    directory = root / "vibescript" / pack.domain / program_id
    directory.mkdir(parents=True)
    (directory / "program.json").write_text(
        json.dumps(
            {
                "schema": PROGRAM_SCHEMA,
                "version": PROGRAM_VERSION,
                "program_id": program_id,
                "domain": pack.domain,
                "workbench": pack.workbench,
                "label": "Saved Compatibility Part",
                "source": source,
                "input_schema": input_schema,
                "inputs": inputs,
                "expected_outputs": expected_outputs,
                "working_revision": revision,
                "accepted_revision": revision,
                "accepted_contract": None,
                "live_outputs": {},
                "latest_candidate": {
                    "revision": revision,
                    "status": "accepted",
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
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
    update = _capture(
        base_capture,
        operation="set_inputs",
        arguments={
            "program_id": program_id,
            "expected_revision": revision,
            "patch": {"height": 7.0},
        },
    )
    try:
        prepared, publication, accepted = _run_candidate(update, service)
        assert prepared["worker_request"]["compatibility_methods"] == ["pad"]
        output = accepted["live_outputs"]["Part"]
        body_name = publication["native_history"]["body_objects"]["Part"]
        body = document.getObject(body_name)
        assert body is not None
        assert body.Tip is not None
        assert body.Tip.TypeId == "PartDesign::DesignBodyPublication"
        operation = document.getObject(
            publication["native_history"]["operation_object"]
        )
        assert operation is not None
        assert body.Tip.CurrentState.Operation is operation
        assert not [
            obj
            for obj in document.Objects
            if obj.TypeId == "PartDesign::Pad"
        ]
        assert publication["live_outputs"]["Part"]["partdesign_data"][
            "feature_count"
        ] >= 1
        return {
            "object_name": str(output["object_name"]),
            "body_name": str(body.Name),
            "operation": str(operation.Name),
            "native_tip": str(body.Tip.TypeId),
        }
    finally:
        App.closeDocument(document.Name)


def _exercise_provider_failed_source_lifecycle(root: Path, pack) -> dict:
    """A failed create remains readable, editable, buildable, and deletable."""

    import FreeCAD as App

    import VibeCADVibeScriptDomains as domains
    from VibeCADSession import make_provider_tool_runner
    from VibeCADTools import ToolRegistry
    from tool_impl import service as service_tools
    from tool_impl import sketcher as sketcher_tools

    document = App.newDocument("PartDesignProviderSourceLifecycle")

    class RunnerService(_Service):
        def __init__(self, active_document, project_root: Path) -> None:
            super().__init__(active_document, project_root)
            self.registry = ToolRegistry()
            service_tools.register_tools(self.registry, self)
            sketcher_tools.register_tools(self.registry, self)
            domains.register_domain_tools(self.registry, self)

        @staticmethod
        def provider_edit_object_summary():
            return None

        @staticmethod
        def design_review_enabled() -> bool:
            return True

        @staticmethod
        def note_provider_tool_targets(_arguments, _payload) -> None:
            return None

    service = RunnerService(document, root / "provider-source-lifecycle")
    failed_source = "\n".join(
        (
            "base = api.extrude(api.sketch([api.circle([0, 0], 5)]), 8, operation='add_material')",
            "far = api.sketch([api.point([0, 0])], plane_offset_mm=100, require_closed_profile=False)",
            "failed = api.hole(base, far, 2, depth_mm=1)",
            "result = {'Result': api.body(failed)}",
        )
    )
    valid_source = "\n".join(
        (
            "profile = api.sketch([api.circle([0, 0], 5)])",
            "solid = api.extrude(profile, 8, operation='add_material')",
            "result = {'Result': api.body(solid)}",
        )
    )
    runner = make_provider_tool_runner(
        service,
        tool_trace=[],
        progress_callback=None,
        cancellation_check=None,
        steering_check=None,
        question_callback=None,
        document_thread_dispatch=lambda operation: operation(),
        turn_editable_sources={
            "schema": "vibecad-editable-sources-v1",
            "domain": "partdesign",
            "workbench": "PartDesignWorkbench",
            "sources": [],
        },
    )
    try:
        created = runner(
            "vibescript.create_program",
            json.dumps(
                {
                    "program_name": "Failed Source Lifecycle",
                    "source": failed_source,
                    "input_schema": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    "inputs": {},
                    "expected_outputs": [{"name": "Result", "type": "solid"}],
                }
            ),
        )
        assert created["ok"] is False, created
        assert "source_id" in created, created
        source_id = str(created["source_id"])
        failed_revision = str(created["working_revision"])
        assert len(source_id) == 32
        assert len(failed_revision) == 64

        read_failed = runner(
            "vibescript.read_source",
            json.dumps({"source_id": source_id, "include_logs": False}),
        )
        assert read_failed["ok"] is True, read_failed
        assert read_failed["source"] == failed_source
        assert read_failed["current_revision"] == failed_revision

        edited = runner(
            "vibescript.edit_source",
            json.dumps(
                {
                    "source_id": source_id,
                    "expected_revision": failed_revision,
                    "source": valid_source,
                }
            ),
        )
        assert edited["ok"] is True, edited
        accepted_revision = str(edited["working_revision"])
        assert accepted_revision != failed_revision
        assert document.getObject(edited["live_outputs"]["Result"]["object_name"])

        deleted = runner(
            "vibescript.delete_program",
            json.dumps(
                {
                    "source_id": source_id,
                    "expected_revision": accepted_revision,
                    "reason": "Complete the provider source lifecycle integration test.",
                }
            ),
        )
        assert deleted["ok"] is True, deleted
        missing = runner(
            "vibescript.read_source",
            json.dumps({"source_id": source_id, "include_logs": False}),
        )
        assert missing["failure_code"] == "SOURCE_NOT_FOUND", missing
        return {
            "failed_source_read": True,
            "same_source_edited": True,
            "same_source_deleted": True,
        }
    finally:
        App.closeDocument(document.Name)


class PartDesignMaterialDriftIntegration(unittest.TestCase):
    """Focused GUI-hosted regression for source edits after presentation drift."""

    def test_source_edit_reconciles_external_material_change(self) -> None:
        pack = get_vibescript_pack("PartDesignWorkbench")
        self.assertIsNotNone(pack)
        root = Path(
            tempfile.mkdtemp(prefix="vibecad-partdesign-material-integration-")
        )
        try:
            result = _exercise_physical_material_publication(root, pack)
        finally:
            shutil.rmtree(root)
        self.assertTrue(result["external_drift_reconciled"])

    def test_link_presentation_rollback_resolves_objects_after_abort(self) -> None:
        import FreeCAD as App
        import Part

        document = App.newDocument("PartDesignPresentationRollback")
        try:
            primary = document.addObject("PartDesign::Feature", "Primary")
            primary.Shape = Part.makeBox(10, 8, 6)
            alternate = document.addObject("PartDesign::Feature", "Alternate")
            alternate.Shape = Part.makeCylinder(3, 9)
            published = document.addObject("App::Link", "Published")
            published.LinkedObject = primary
            document.recompute()
            primary.ViewObject.LineColor = (0.2, 0.3, 0.4)
            published.ViewObject.LineColor = (0.2, 0.3, 0.4)
            states = [
                _material_target_snapshot(primary),
                _material_target_snapshot(published),
            ]

            document.openTransaction("Failed publication relink")
            published.LinkedObject = alternate
            alternate.ViewObject.LineColor = (0.9, 0.1, 0.1)
            published.ViewObject.LineColor = (0.9, 0.1, 0.1)
            document.abortTransaction()

            _restore_material_target_snapshots(states)
            self.assertIs(published.LinkedObject, primary)
            for actual, expected in zip(
                tuple(primary.ViewObject.LineColor)[:3],
                (0.2, 0.3, 0.4),
                strict=True,
            ):
                self.assertAlmostEqual(actual, expected, places=6)
            for actual, expected in zip(
                tuple(published.ViewObject.LineColor)[:3],
                (0.2, 0.3, 0.4),
                strict=True,
            ):
                self.assertAlmostEqual(actual, expected, places=6)
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
        placement_and_hole_direction = _exercise_placement_and_hole_direction(
            root,
            pack,
        )
        geometry_verification = _exercise_geometry_verification(root, pack)
        attached_sketch_history = _exercise_attached_sketch_history(root, pack)
        native_sketch_history = _exercise_native_sketch_history(root, pack)
        topology_publication = _exercise_topology_publication(root, pack)
        local_interfaces = _exercise_output_local_interfaces_and_ownership_repair(
            root,
            pack,
        )
        physical_material = _exercise_physical_material_publication(root, pack)
        direct_solid_label = _exercise_direct_solid_adoption_label(root, pack)
        saved_source_compatibility = _exercise_saved_source_compatibility(
            root,
            pack,
        )
        component_occurrence = _exercise_component_occurrence(root, pack)
        lifecycle = _exercise_lifecycle(root, pack)
        provider_source_lifecycle = _exercise_provider_failed_source_lifecycle(
            root,
            pack,
        )
        print(
            dump_json(
                {
                    "ok": True,
                    "domain": "partdesign",
                    "feature_families": feature_families,
                    "unified_surface": unified_surface,
                    "material_guardrails": material_guardrails,
                    "placement_and_hole_direction": placement_and_hole_direction,
                    "geometry_verification": geometry_verification,
                    "attached_sketch_history": attached_sketch_history,
                    "native_sketch_history": native_sketch_history,
                    "topology_publication": topology_publication,
                    "output_local_interfaces": local_interfaces,
                    "physical_material": physical_material,
                    "direct_solid_label": direct_solid_label,
                    "saved_source_compatibility": saved_source_compatibility,
                    "component_occurrence": component_occurrence,
                    "lifecycle": lifecycle,
                    "provider_source_lifecycle": provider_source_lifecycle,
                },
                sort_keys=True,
            )
        )
    finally:
        remove_tree(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
