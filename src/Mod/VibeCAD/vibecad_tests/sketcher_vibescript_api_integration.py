# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD integration gate for the Sketcher VibeScript domain."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile

MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from vibescript_domain_api import create_domain_api  # noqa: E402
from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    delete_live_program,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    accept_candidate,
    capture_reference_shapes,
    complete_inspection,
    execute_candidate,
    finalize_candidate,
    finish_delete,
    prepare_candidate,
    prepare_delete,
    retain_candidate,
    restore_prepared_delete,
    validate_candidate,
)
from VibeCADVibeScriptDomains import (  # noqa: E402
    _sketcher_document_snapshot,
    domain_context_snapshot,
    get_domain_adapter,
    get_vibescript_pack,
)
from vibescript_sketcher_worker import (  # noqa: E402
    SketcherCandidateError,
    configure_sketcher_references,
    populate_sketch_without_solving,
    sketch_external_reference_records,
    validate_and_solve_sketch,
)
from vibescript_part_worker import part_shape_facts  # noqa: E402


def _point(geometry, point: str) -> dict:
    return {"geometry": geometry, "point": point}


def _output(name: str, value) -> dict:
    return {"name": name, "type": value.output_type, "definition": value.to_payload()}


def _rectangle(api, *, width: float, height: float, require_fully_constrained: bool):
    bottom = api.line([0, 0], [width, 0], name="Bottom")
    right = api.line([width, 0], [width, height], name="Right")
    top = api.line([width, height], [0, height], name="Top")
    left = api.line([0, height], [0, 0], name="Left")
    constraints = [
        api.constraint(
            "coincident",
            [_point(bottom, "end"), _point(right, "start")],
            name="Corner1",
        ),
        api.constraint(
            "coincident",
            [_point(right, "end"), _point(top, "start")],
            name="Corner2",
        ),
        api.constraint(
            "coincident",
            [_point(top, "end"), _point(left, "start")],
            name="Corner3",
        ),
        api.constraint(
            "coincident",
            [_point(left, "end"), _point(bottom, "start")],
            name="Corner4",
        ),
        api.constraint("horizontal", [bottom], name="BottomHorizontal"),
        api.constraint("horizontal", [top], name="TopHorizontal"),
        api.constraint("vertical", [right], name="RightVertical"),
        api.constraint("vertical", [left], name="LeftVertical"),
        api.constraint("distance", [bottom], value=width, name="Width"),
        api.constraint("distance", [right], value=height, name="Height"),
        api.constraint(
            "coincident",
            [_point(bottom, "start"), "origin"],
            name="AtOrigin",
        ),
    ]
    return api.sketch(
        [bottom, right, top, left],
        constraints,
        require_fully_constrained=require_fully_constrained,
        require_closed_profile=True,
        label="Constrained Rectangle",
    )


def _exercise_rectangle(api) -> dict:
    import FreeCAD as App

    value = _rectangle(api, width=40.0, height=20.0, require_fully_constrained=True)
    document = App.newDocument("VibeScriptSketchRectangle", "Sketch Rectangle", True, True)
    try:
        outputs = [_output("Profile", value)]
        validation = validate_and_solve_sketch(document, {"Profile": value}, outputs)
        assert validation["solver_code"] == 0
        assert validation["fully_constrained"] is True
        assert validation["degrees_of_freedom"] == 0
        assert validation["profile_ready"] is True
        assert validation["closed_wire_count"] == 1
        assert validation["geometry_count"] == 4
        assert validation["constraint_count"] == 11
        assert [item["graph_id"] for item in validation["geometry"]] == [
            "g1",
            "g2",
            "g3",
            "g4",
        ]
        assert [item["graph_id"] for item in validation["constraints"]] == [
            f"c{index}" for index in range(1, 12)
        ]
        assert outputs[0]["sketch_validation"] == validation
        return validation
    finally:
        App.closeDocument(document.Name)


def _exercise_geometry_exports(api) -> None:
    import FreeCAD as App

    definitions = {
        "Arc": api.sketch([api.arc([0, 0], [5, 4], [10, 0], name="Arc")]),
        "Circle": api.sketch(
            [api.circle([2, 3], 4, construction=True, name="ConstructionCircle")]
        ),
        "Ellipse": api.sketch(
            [api.ellipse([0, 0], 6, 3, rotation_degrees=25, name="Ellipse")]
        ),
        "EllipticArc": api.sketch(
            [
                api.elliptic_arc(
                    [0, 0],
                    6,
                    3,
                    -1.2,
                    1.2,
                    rotation_degrees=20,
                    name="EllipticArc",
                )
            ]
        ),
        "HyperbolicArc": api.sketch(
            [
                api.hyperbolic_arc(
                    [0, 0],
                    5,
                    3,
                    -1,
                    1,
                    rotation_degrees=15,
                    name="HyperbolicArc",
                )
            ]
        ),
        "ParabolicArc": api.sketch(
            [
                api.parabolic_arc(
                    [0, 0],
                    2,
                    -4,
                    4,
                    rotation_degrees=10,
                    name="ParabolicArc",
                )
            ]
        ),
        "BSpline": api.sketch(
            [api.bspline([[0, 0], [3, 5], [7, 4], [10, 0]], name="Spline")]
        ),
        "ExactBSpline": api.sketch(
            [
                api.bspline(
                    [[0, 0], [3, 5], [7, 4], [10, 0]],
                    degree=3,
                    knots=[0, 1],
                    multiplicities=[4, 4],
                    weights=[1, 0.8, 1.2, 1],
                    name="ExactSpline",
                )
            ]
        ),
    }
    for name, value in definitions.items():
        document = App.newDocument(f"VibeScriptSketch{name}", name, True, True)
        try:
            outputs = [_output(name, value)]
            validation = validate_and_solve_sketch(document, {name: value}, outputs)
            assert validation["solver_code"] == 0, (name, validation)
            assert validation["geometry_count"] == 1
            if name == "Circle":
                assert validation["construction_geometry_count"] == 1
        finally:
            App.closeDocument(document.Name)


def _exercise_requirement_failure(api) -> None:
    import FreeCAD as App

    line = api.line([0, 0], [10, 0])
    value = api.sketch([line], require_fully_constrained=True)
    document = App.newDocument("VibeScriptSketchFailure", "Sketch Failure", True, True)
    try:
        try:
            validate_and_solve_sketch(
                document,
                {"Profile": value},
                [_output("Profile", value)],
            )
        except SketcherCandidateError as exc:
            assert exc.details["stage"] == "fully_constrained_requirement"
            assert int(exc.details["degrees_of_freedom"]) > 0
            assert exc.details["underconstraint_guidance"]["status"] == "available"
            assert exc.details["underconstraint_guidance"]["automatic_application"] is False
            assert "never apply every heuristic suggestion" in exc.details[
                "correction"
            ].lower()
        else:
            raise AssertionError("The worker accepted an underconstrained required sketch.")
    finally:
        App.closeDocument(document.Name)


def _exercise_model_guidance(api) -> None:
    import FreeCAD as App

    bottom = api.line([0, 0], [24, 0], name="GuideBottom")
    right = api.line([24, 0], [24, 12], name="GuideRight")
    top = api.line([24, 12], [0, 12], name="GuideTop")
    left = api.line([0, 12], [0, 0], name="GuideLeft")
    bottom_horizontal = api.constraint(
        "horizontal",
        [bottom],
        name="ExistingBottomHorizontal",
    )
    value = api.sketch(
        [bottom, right, top, left],
        [bottom_horizontal],
        label="Guidance Rectangle",
    )
    document = App.newDocument("VibeScriptSketchGuidance", "Guidance", True, True)
    try:
        validation = validate_and_solve_sketch(
            document,
            {"Guidance": value},
            [_output("Guidance", value)],
        )
        assert validation["solver_code"] == 0
        assert validation["degrees_of_freedom"] > 0
        guidance = validation["underconstraint_guidance"]
        assert guidance["status"] == "available"
        assert guidance["canonical_operation"] == "api.constraint"
        assert guidance["automatic_application"] is False
        assert guidance["workflow"] == [
            "connectivity",
            "orientation",
            "equality",
            "dimensions",
        ]
        assert guidance["detected_counts"] == {
            "connectivity": 4,
            "orientation": 4,
            "equality": 2,
        }
        assert guidance["filtered_existing_count"] >= 1
        suggestions = guidance["suggestions"]
        assert len(
            [item for item in suggestions if item["category"] == "connectivity"]
        ) == 4
        orientation = [
            item for item in suggestions if item["category"] == "orientation"
        ]
        assert len(orientation) == 3
        assert all(item["intent_required"] is True for item in suggestions)
        assert all(
            item["entities"][0]["name"] != "GuideBottom" for item in orientation
        )
        assert {
            entity["name"]
            for item in suggestions
            for entity in item["entities"]
        } <= {"GuideBottom", "GuideRight", "GuideTop", "GuideLeft"}
        assert validation["profile_ready"] is True
        assert validation["profile_open_vertices"]["status"] == "not_needed"
    finally:
        App.closeDocument(document.Name)

    first = api.line([0, 0], [10, 0], name="OpenFirst")
    second = api.line([10, 0], [10, 8], name="OpenSecond")
    third = api.line([10, 8], [2, 8], name="OpenThird")
    open_value = api.sketch(
        [first, second, third],
        require_closed_profile=True,
        label="Open Profile",
    )
    document = App.newDocument("VibeScriptSketchOpenProfile", "Open", True, True)
    try:
        try:
            validate_and_solve_sketch(
                document,
                {"OpenProfile": open_value},
                [_output("OpenProfile", open_value)],
            )
        except SketcherCandidateError as exc:
            assert exc.details["stage"] == "profile_requirement"
            diagnostics = exc.details["profile_open_vertices"]
            assert diagnostics["status"] == "available", diagnostics
            assert diagnostics["vertices"]
            assert any(
                vertex["candidate_endpoints"]
                for vertex in diagnostics["vertices"]
            )
            assert "profile_open_vertices.vertices[].candidate_endpoints" in exc.details[
                "correction"
            ]
        else:
            raise AssertionError("The worker accepted an open required profile.")
    finally:
        App.closeDocument(document.Name)


def _validate_constraint_case(name: str, value) -> dict:
    import FreeCAD as App

    document = App.newDocument(f"VibeScriptConstraint{name}", name, True, True)
    try:
        outputs = [_output(name, value)]
        validation = validate_and_solve_sketch(document, {name: value}, outputs)
        assert validation["solver_code"] == 0, (name, validation)
        assert validation["constraint_count"] >= 1
        assert not validation["conflicting_constraints"]
        assert not validation["redundant_constraints"]
        assert not validation["partially_redundant_constraints"]
        assert not validation["malformed_constraints"]
        return validation
    finally:
        App.closeDocument(document.Name)


def _exercise_constraint_families(api) -> None:
    cases = {}

    line = api.line([0, 0], [5, 1])
    cases["Horizontal"] = api.sketch(
        [line], [api.constraint("horizontal", [line], name="Horizontal")]
    )
    line = api.line([0, 0], [1, 5])
    cases["Vertical"] = api.sketch(
        [line], [api.constraint("vertical", [line], name="Vertical")]
    )
    first = api.line([0, 0], [5, 1])
    second = api.line([0, 3], [4, 5])
    cases["Parallel"] = api.sketch(
        [first, second], [api.constraint("parallel", [first, second])]
    )
    first = api.line([0, 0], [5, 1])
    second = api.line([2, -2], [3, 3])
    cases["Perpendicular"] = api.sketch(
        [first, second], [api.constraint("perpendicular", [first, second])]
    )
    tangent_line = api.line([-6, 5], [6, 5])
    tangent_circle = api.circle([0, 0], 5)
    cases["Tangent"] = api.sketch(
        [tangent_line, tangent_circle],
        [api.constraint("tangent", [tangent_line, tangent_circle])],
    )
    line = api.line([0, 0], [5, 0])
    cases["Distance"] = api.sketch(
        [line], [api.constraint("distance", [line], value=5, name="Length")]
    )
    line = api.line([2, 3], [7, 4])
    cases["DistanceX"] = api.sketch(
        [line],
        [api.constraint("distance_x", [_point(line, "start")], value=2)],
    )
    line = api.line([2, 3], [7, 4])
    cases["DistanceY"] = api.sketch(
        [line],
        [api.constraint("distance_y", [_point(line, "start")], value=3)],
    )
    line = api.line([0, 0], [5, 5 / (3**0.5)])
    cases["Angle"] = api.sketch(
        [line], [api.constraint("angle", [line], value=30, name="Direction")]
    )
    first = api.line([-5, 0], [5, 0])
    second = api.line([0, 0], [5, 5])
    intersection = api.point([0, 0], construction=True)
    cases["AngleViaPoint"] = api.sketch(
        [first, second, intersection],
        [
            api.constraint(
                "point_on_object",
                [_point(intersection, "point"), first],
            ),
            api.constraint(
                "point_on_object",
                [_point(intersection, "point"), second],
            ),
            api.constraint(
                "angle_via_point",
                [first, second, _point(intersection, "point")],
                value=45,
                name="IntersectionAngle",
            ),
        ],
    )
    circle = api.circle([0, 0], 3)
    cases["Radius"] = api.sketch(
        [circle], [api.constraint("radius", [circle], value=3)]
    )
    circle = api.circle([0, 0], 3)
    cases["Diameter"] = api.sketch(
        [circle], [api.constraint("diameter", [circle], value=6)]
    )
    first = api.line([0, 0], [5, 0])
    second = api.line([0, 2], [0, 7])
    cases["Equal"] = api.sketch(
        [first, second], [api.constraint("equal", [first, second])]
    )
    line = api.line([2, 0], [6, 3])
    cases["PointOnObject"] = api.sketch(
        [line],
        [api.constraint("point_on_object", [_point(line, "start"), "x_axis"])],
    )
    left = api.line([-2, 1], [-4, 3])
    right = api.line([2, 1], [4, 3])
    cases["Symmetric"] = api.sketch(
        [left, right],
        [
            api.constraint(
                "symmetric",
                [_point(left, "start"), _point(right, "start"), "y_axis"],
            )
        ],
    )
    line = api.line([1, 2], [4, 6])
    cases["Block"] = api.sketch([line], [api.constraint("block", [line])])
    circle = api.circle([0, 0], 3)
    cases["ReferenceRadius"] = api.sketch(
        [circle],
        [api.constraint("radius", [circle], value=3, driving=False, name="MeasuredRadius")],
    )
    line = api.line([0, 0], [5, 1])
    cases["Inactive"] = api.sketch(
        [line], [api.constraint("horizontal", [line], active=False)]
    )
    line = api.line([0, 0], [5, 1])
    cases["Virtual"] = api.sketch(
        [line], [api.constraint("horizontal", [line], virtual=True)]
    )
    line = api.line([0, 0], [5, 0])
    cases["Expression"] = api.sketch(
        [line],
        [
            api.constraint(
                "distance",
                [line],
                value=5,
                name="ExpressionLength",
                expression="6 mm",
            )
        ],
    )
    first = api.line([0, 0], [2, 0])
    second = api.line([0, 2], [2, 2])
    cases["Group"] = api.sketch(
        [first, second], [api.constraint("group", [first, second])]
    )
    line = api.line([0, 0], [2, 0])
    cases["Text"] = api.sketch(
        [line],
        [api.constraint("text", [line], text="Profile note", font="sans")],
    )
    control = api.circle([0, 0], 1, construction=True)
    cases["Weight"] = api.sketch(
        [control], [api.constraint("weight", [control], value=1)]
    )
    diameter = api.line([-6, 0], [6, 0], construction=True)
    ellipse = api.ellipse([0, 0], 6, 3)
    cases["EllipseInternalAlignment"] = api.sketch(
        [diameter, ellipse],
        [
            api.constraint(
                "internal_alignment",
                [diameter, ellipse],
                alignment="ellipse_major_diameter",
            )
        ],
    )
    minor_diameter = api.line([0, -3], [0, 3], construction=True)
    ellipse = api.ellipse([0, 0], 6, 3)
    cases["EllipseMinorAlignment"] = api.sketch(
        [minor_diameter, ellipse],
        [
            api.constraint(
                "internal_alignment",
                [minor_diameter, ellipse],
                alignment="ellipse_minor_diameter",
            )
        ],
    )
    major_diameter = api.line([-5, 0], [5, 0], construction=True)
    hyperbola = api.hyperbolic_arc([0, 0], 5, 3, -1, 1)
    cases["HyperbolaMajorAlignment"] = api.sketch(
        [major_diameter, hyperbola],
        [
            api.constraint(
                "internal_alignment",
                [major_diameter, hyperbola],
                alignment="hyperbola_major_diameter",
            )
        ],
    )
    minor_diameter = api.line([0, -3], [0, 3], construction=True)
    hyperbola = api.hyperbolic_arc([0, 0], 5, 3, -1, 1)
    cases["HyperbolaMinorAlignment"] = api.sketch(
        [minor_diameter, hyperbola],
        [
            api.constraint(
                "internal_alignment",
                [minor_diameter, hyperbola],
                alignment="hyperbola_minor_diameter",
            )
        ],
    )
    hyperbola_focus = api.point([34**0.5, 0], construction=True)
    hyperbola = api.hyperbolic_arc([0, 0], 5, 3, -1, 1)
    cases["HyperbolaFocusAlignment"] = api.sketch(
        [hyperbola_focus, hyperbola],
        [
            api.constraint(
                "internal_alignment",
                [_point(hyperbola_focus, "point"), hyperbola],
                alignment="hyperbola_focus",
            )
        ],
    )
    parabola_focus = api.point([2, 0], construction=True)
    parabola = api.parabolic_arc([0, 0], 2, -4, 4)
    cases["ParabolaFocusAlignment"] = api.sketch(
        [parabola_focus, parabola],
        [
            api.constraint(
                "internal_alignment",
                [_point(parabola_focus, "point"), parabola],
                alignment="parabola_focus",
            )
        ],
    )
    focal_axis = api.line([0, 0], [4, 0], construction=True)
    parabola = api.parabolic_arc([0, 0], 2, -4, 4)
    cases["ParabolaFocalAxisAlignment"] = api.sketch(
        [focal_axis, parabola],
        [
            api.constraint(
                "internal_alignment",
                [focal_axis, parabola],
                alignment="parabola_focal_axis",
            )
        ],
    )
    focus = api.point([27**0.5, 0], construction=True)
    ellipse = api.ellipse([0, 0], 6, 3)
    cases["EllipseFocusAlignment"] = api.sketch(
        [focus, ellipse],
        [
            api.constraint(
                "internal_alignment",
                [_point(focus, "point"), ellipse],
                alignment="ellipse_focus1",
            )
        ],
    )
    control = api.circle([0, 0], 0.5, construction=True)
    spline = api.bspline(
        [[0, 0], [3, 5], [7, 4], [10, 0]],
        degree=3,
        knots=[0, 1],
        multiplicities=[4, 4],
    )
    cases["BSplineControlAlignment"] = api.sketch(
        [control, spline],
        [
            api.constraint(
                "internal_alignment",
                [_point(control, "center"), spline],
                alignment="bspline_control_point",
                internal_index=0,
            )
        ],
    )
    knot = api.point([0, 0], construction=True)
    spline = api.bspline(
        [[0, 0], [3, 5], [7, 4], [10, 0]],
        degree=3,
        knots=[0, 1],
        multiplicities=[4, 4],
    )
    cases["BSplineKnotAlignment"] = api.sketch(
        [knot, spline],
        [
            api.constraint(
                "internal_alignment",
                [_point(knot, "point"), spline],
                alignment="bspline_knot_point",
                internal_index=0,
            )
        ],
    )
    first_ray = api.line([-5, -5], [0, 0])
    second_ray = api.line([0, 0], [5, -5])
    interface = api.line([0, -10], [0, 10], construction=True)
    cases["SnellsLaw"] = api.sketch(
        [first_ray, second_ray, interface],
        [
            api.constraint(
                "snells_law",
                [
                    _point(first_ray, "end"),
                    _point(second_ray, "start"),
                    interface,
                ],
                value=1,
            )
        ],
    )

    for name, value in cases.items():
        validation = _validate_constraint_case(name, value)
        if name == "ReferenceRadius":
            assert validation["constraints"][0]["driving"] is False
        elif name == "Inactive":
            assert validation["constraints"][0]["active"] is False
        elif name == "Virtual":
            assert validation["constraints"][0]["virtual"] is True
        elif name == "Expression":
            assert validation["constraints"][0]["expression"] == "6 mm"


def _exercise_internal_alignment_index_guards(api) -> None:
    """Prove invalid native indexes become feedback instead of C++ assertions."""

    import FreeCAD as App

    control = api.circle([0, 0], 0.5, construction=True)
    spline = api.bspline(
        [[0, 0], [3, 5], [7, 4], [10, 0]],
        degree=3,
        knots=[0, 1],
        multiplicities=[4, 4],
    )
    try:
        api.constraint(
            "internal_alignment",
            [_point(control, "center"), spline],
            alignment="bspline_control_point",
            internal_index=4,
        )
    except ValueError as exc:
        assert "internal_index" in str(exc)
        assert "0-3" in str(exc)
    else:
        raise AssertionError("The source API accepted an out-of-range B-spline pole.")

    valid = api.constraint(
        "internal_alignment",
        [_point(control, "center"), spline],
        alignment="bspline_control_point",
        internal_index=0,
    )
    payload = api.sketch([control, spline], [valid]).to_payload()
    payload["arguments"][1][0]["properties"]["internal_index"] = 999
    document = App.newDocument("VibeScriptSketchIndexGuard")
    try:
        sketch = document.addObject("Sketcher::SketchObject", "Candidate")
        try:
            populate_sketch_without_solving(sketch, payload, replace_existing=False)
        except SketcherCandidateError as exc:
            assert exc.details["stage"] == "constraint_internal_alignment"
            assert exc.details["native_count"] == 4
            assert exc.details["valid_range"] == [0, 3]
        else:
            raise AssertionError("The worker reached FreeCAD's invalid-index assertion.")

        # A valid candidate in the same process proves the guard returned cleanly.
        replacement = document.addObject("Sketcher::SketchObject", "ValidCandidate")
        payload["arguments"][1][0]["properties"]["internal_index"] = 0
        _geometry, constraints, _indexes, _externals = populate_sketch_without_solving(
            replacement,
            payload,
            replace_existing=False,
        )
        assert len(constraints) == 1
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
        return "SketcherWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "sketcher-production-revision"

    def project_scope_snapshot(self) -> dict:
        return {"root": str(self.project_root)}


def _program_source() -> str:
    return (
        "width = inputs['width']\n"
        "height = inputs['height']\n"
        "bottom = api.line([0,0], [width,0], name='Bottom')\n"
        "right = api.line([width,0], [width,height], name='Right')\n"
        "top = api.line([width,height], [0,height], name='Top')\n"
        "left = api.line([0,height], [0,0], name='Left')\n"
        "constraints = [\n"
        " api.constraint('coincident', "
        "[{'geometry':bottom,'point':'end'},{'geometry':right,'point':'start'}], "
        "name='Corner1'),\n"
        " api.constraint('coincident', "
        "[{'geometry':right,'point':'end'},{'geometry':top,'point':'start'}], "
        "name='Corner2'),\n"
        " api.constraint('coincident', "
        "[{'geometry':top,'point':'end'},{'geometry':left,'point':'start'}], "
        "name='Corner3'),\n"
        " api.constraint('coincident', "
        "[{'geometry':left,'point':'end'},{'geometry':bottom,'point':'start'}], "
        "name='Corner4'),\n"
        " api.constraint('horizontal', [bottom], name='BottomHorizontal'),\n"
        " api.constraint('horizontal', [top], name='TopHorizontal'),\n"
        " api.constraint('vertical', [right], name='RightVertical'),\n"
        " api.constraint('vertical', [left], name='LeftVertical'),\n"
        " api.constraint('distance', [bottom], value=width, name='Width'),\n"
        " api.constraint('distance', [right], value=height, name='Height'),\n"
        " api.constraint('coincident', "
        "[{'geometry':bottom,'point':'start'},'origin'], name='AtOrigin'),\n"
        "]\n"
        "profile = api.sketch([bottom,right,top,left], constraints, "
        "require_fully_constrained=True, require_closed_profile=True, "
        "label='Lifecycle Profile')\n"
        "result = {'Profile': profile}\n"
    )


def _base_capture(root: Path, document) -> dict:
    import FreeCAD as App

    pack = get_vibescript_pack("SketcherWorkbench")
    assert pack is not None
    return {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "sketcher-production-revision",
        "document_objects": [
            {"name": str(obj.Name), "label": str(obj.Label), "type_id": str(obj.TypeId)}
            for obj in document.Objects
        ],
        "surface": resolve_modeling_surface("SketcherWorkbench", "vibescript").summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }


def _run_candidate(captured: dict, service: _Service):
    prepared = prepare_candidate(captured)
    if prepared.get("reference_requirements") and not prepared.get("finalized"):
        prepared = finalize_candidate(
            prepared,
            capture_reference_shapes(service, prepared),
        )
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    return prepared, execution, publication, accepted


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


def _exercise_supported_sketch(root: Path) -> None:
    import FreeCAD as App
    import Part

    document = App.newDocument("VibeScriptSupportedSketch")
    support = document.addObject("Part::Feature", "NativeSupport")
    support.Label = "Native Support"
    support.Shape = Part.makeBox(30, 20, 5)
    service = _Service(document, root)
    base = _base_capture(root, document)
    reference = {
        "document_uid": str(document.Uid),
        "object_name": str(support.Name),
    }
    source = (
        "line = api.line([0,0], [12,0], name='DatumLine')\n"
        "fixed = api.constraint('block', [line], name='FixedLine')\n"
        "sketch = api.sketch([line], [fixed], support={\n"
        " 'reference': inputs['support'],\n"
        " 'selection': {'type':'subelements','subelements':['Face6']},\n"
        "}, map_mode='FlatFace', attachment_offset={\n"
        " 'position':[1,2,0], 'rotation':[0,0,0,1],\n"
        "}, require_fully_constrained=True, label='Supported Sketch')\n"
        "result = {'Supported': sketch}\n"
    )
    captured = {
        **base,
        "operation": "create_program",
        "tool_name": "vibescript.sketcher.create_program",
        "arguments": {
            "program_name": "Supported Profile",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {"support": _reference_schema()},
                "required": ["support"],
                "additionalProperties": False,
            },
            "inputs": {"support": reference},
            "expected_outputs": [{"name": "Supported", "type": "sketch"}],
        },
    }
    prepared, execution, _publication, accepted = _run_candidate(captured, service)
    resolved = execution["sketch_validation"]["support"]
    assert resolved["reference"] == reference
    assert resolved["resolved_subelements"] == ["Face6"]
    assert resolved["source_kind"] == "shape"
    live = document.getObject(accepted["live_outputs"]["Supported"]["object_name"])
    assert live is not None
    assert live.AttachmentSupport[0][0] is support
    assert list(live.AttachmentSupport[0][1]) == ["Face6"]
    assert live.MapMode == "FlatFace"
    assert abs(float(live.AttachmentOffset.Base.x) - 1.0) < 1.0e-9
    assert abs(float(live.AttachmentOffset.Base.y) - 2.0) < 1.0e-9
    document.recompute()
    assert live.FullyConstrained is True

    delete_capture = {
        **base,
        "operation": "delete_program",
        "tool_name": "vibescript.sketcher.delete_program",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": prepared["revision"],
            "reason": "supported-sketch cleanup",
        },
    }
    prepared_delete = prepare_delete(delete_capture)
    deletion = delete_live_program(service, prepared_delete)
    finish_delete(prepared_delete, deletion)

    adapter = get_domain_adapter("sketcher")
    assert adapter is not None
    recommended = adapter.describe_api()["recommended_patterns"][1]
    recommended_capture = {
        **_base_capture(root, document),
        "operation": "create_program",
        "tool_name": "vibescript.sketcher.create_program",
        "arguments": {
            "program_name": "Recommended Attached Circle",
            "source": recommended["source"],
            "input_schema": {
                "type": "object",
                "properties": {
                    "support": _reference_schema(),
                    "radius": {"type": "number", "exclusiveMinimum": 0},
                },
                "required": ["support", "radius"],
                "additionalProperties": False,
            },
            "inputs": {"support": reference, "radius": 4.0},
            "expected_outputs": recommended["expected_outputs"],
        },
    }
    recommended_prepared, recommended_execution, _publication, recommended_accepted = (
        _run_candidate(recommended_capture, service)
    )
    assert recommended_execution["sketch_validation"]["support"][
        "resolved_subelements"
    ] == ["Face6"]
    assert recommended_execution["sketch_validation"]["fully_constrained"] is True
    recommended_live = document.getObject(
        recommended_accepted["live_outputs"]["Profile"]["object_name"]
    )
    assert recommended_live is not None
    document.recompute()
    assert recommended_live.FullyConstrained is True
    assert recommended_live.MapMode == "FlatFace"
    recommended_delete = {
        **_base_capture(root, document),
        "operation": "delete_program",
        "tool_name": "vibescript.sketcher.delete_program",
        "arguments": {
            "program_id": recommended_prepared["program_id"],
            "expected_revision": recommended_prepared["revision"],
            "reason": "recommended attached-circle cleanup",
        },
    }
    recommended_prepared_delete = prepare_delete(recommended_delete)
    recommended_deletion = delete_live_program(service, recommended_prepared_delete)
    finish_delete(recommended_prepared_delete, recommended_deletion)
    App.closeDocument(document.Name)


def _exercise_external_geometry(root: Path) -> None:
    import FreeCAD as App
    import Part

    document = App.newDocument("VibeScriptSketchExternal")
    source = document.addObject("Part::Feature", "ExternalSource")
    source.Label = "Stable external geometry source"
    source.Shape = Part.makeBox(20, 10, 5)
    source_name = str(source.Name)
    reference = {
        "document_uid": str(document.Uid),
        "object_name": source_name,
    }
    service = _Service(document, root)
    base = _base_capture(root, document)
    program_source = (
        "external = api.external_geometry(inputs['source'], 'Edge1', name='DatumEdge')\n"
        "external_vertex = api.external_geometry(inputs['source'], 'Vertex1', "
        "name='DatumVertex')\n"
        "line = api.line([0,0], [5,0], name='DrivenLine')\n"
        "coincident = api.constraint('coincident', [\n"
        " {'geometry':line,'point':'start'},\n"
        " {'geometry':external,'point':'start'},\n"
        "], name='OnExternalStart')\n"
        "profile = api.sketch([external,external_vertex,line], [coincident], "
        "label='External Reference Sketch')\n"
        "result = {'Profile': profile}\n"
    )
    captured = {
        **base,
        "operation": "create_program",
        "tool_name": "vibescript.sketcher.create_program",
        "arguments": {
            "program_name": "External Reference Profile",
            "source": program_source,
            "input_schema": {
                "type": "object",
                "properties": {"source": _reference_schema()},
                "required": ["source"],
                "additionalProperties": False,
            },
            "inputs": {"source": reference},
            "expected_outputs": [{"name": "Profile", "type": "sketch"}],
        },
    }
    prepared, execution, _publication, accepted = _run_candidate(captured, service)
    validation = execution["sketch_validation"]
    assert validation["geometry_count"] == 3
    assert validation["native_geometry_count"] == 1
    assert validation["external_geometry_count"] == 2
    external = validation["external_geometry"][0]
    assert external["reference"] == reference
    assert external["resolved_subelement"] == "Edge1"
    assert external["native_geometry_id"] == -3
    external_vertex = validation["external_geometry"][1]
    assert external_vertex["reference"] == reference
    assert external_vertex["resolved_subelement"] == "Vertex1"
    assert external_vertex["native_geometry_id"] == -4
    live_name = accepted["live_outputs"]["Profile"]["object_name"]
    live = document.getObject(live_name)
    assert live is not None
    assert live.GeometryCount == 1
    assert live.ConstraintCount == 1
    assert len(live.ExternalGeometry) == 1
    assert live.ExternalGeometry[0][0] is source
    assert list(live.ExternalGeometry[0][1]) == ["Edge1", "Vertex1"]
    assert sketch_external_reference_records(live) == [
        (source, "Edge1"),
        (source, "Vertex1"),
    ]
    assert live.Constraints[0].First == 0
    assert live.Constraints[0].Second == -3
    document.recompute()
    assert live.MalformedConstraints == []

    update_capture = {
        **captured,
        "operation": "edit_source",
        "tool_name": "vibescript.sketcher.edit_source",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": prepared["revision"],
            "replacements": [
                {"old": "line = api.line([0,0], [5,0]", "new": "line = api.line([0,0], [8,0]"}
            ],
        },
    }
    update_prepared, update_execution, update_publication, accepted = _run_candidate(
        update_capture,
        service,
    )
    assert update_publication["created_objects"] == []
    assert update_execution["sketch_validation"]["external_geometry_count"] == 2
    assert accepted["live_outputs"]["Profile"]["object_name"] == live_name
    assert document.getObject(live_name) is live
    assert live.GeometryCount == 1
    assert len(live.ExternalGeometry) == 1
    assert live.ExternalGeometry[0][0] is source
    assert list(live.ExternalGeometry[0][1]) == ["Edge1", "Vertex1"]
    assert len(sketch_external_reference_records(live)) == 2
    assert live.Constraints[0].Second == -3
    assert abs(float(live.Geometry[0].EndPoint.x) - 8.0) < 1.0e-7
    context = _sketcher_document_snapshot(document)
    context_live = next(item for item in context["sketches"] if item["name"] == live_name)
    assert context_live["external_geometry_count"] == 2
    assert context_live["external_geometry_truncated"] is False
    assert context_live["external_geometry"] == [
        {
            "link_index": 0,
            "link_group_index": 0,
            "link_subelement_index": 0,
            "native_geometry_id": -3,
            "object_name": source_name,
            "object_label": source.Label,
            "object_type_id": source.TypeId,
            "subelements": ["Edge1"],
        },
        {
            "link_index": 1,
            "link_group_index": 0,
            "link_subelement_index": 1,
            "native_geometry_id": -4,
            "object_name": source_name,
            "object_label": source.Label,
            "object_type_id": source.TypeId,
            "subelements": ["Vertex1"],
        },
    ]
    provider_context = domain_context_snapshot(service, "sketcher")
    assert provider_context["domain"] == "sketcher"
    assert provider_context["workbench"] == "SketcherWorkbench"
    assert provider_context["surface_id"] == resolve_modeling_surface(
        "SketcherWorkbench",
        "vibescript",
    ).surface_id
    provider_live = next(
        item
        for item in provider_context["sketcher_document"]["sketches"]
        if item["name"] == live_name
    )
    assert provider_live["external_geometry_count"] == 2
    assert provider_context["sketch_support_shapes"]["object_count"] >= 2

    save_path = root / "sketcher-external-vibescript.FCStd"
    document.saveAs(str(save_path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(save_path))
    service.document = reopened
    reopened_live = reopened.getObject(live_name)
    reopened_source = reopened.getObject(source_name)
    assert reopened_live is not None and reopened_source is not None
    assert reopened_live.ExternalGeometry[0][0] is reopened_source
    assert list(reopened_live.ExternalGeometry[0][1]) == ["Edge1", "Vertex1"]

    delete_capture = {
        **base,
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
        "operation": "delete_program",
        "tool_name": "vibescript.sketcher.delete_program",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": update_prepared["revision"],
            "reason": "external-geometry cleanup",
        },
    }
    prepared_delete = prepare_delete(delete_capture)
    deletion = delete_live_program(service, prepared_delete)
    finish_delete(prepared_delete, deletion)
    assert reopened.getObject(live_name) is None
    assert reopened.getObject(source_name) is reopened_source
    App.closeDocument(reopened.Name)


def _exercise_semantic_external_geometry(root: Path, api) -> None:
    import FreeCAD as App
    import Part

    reference_root = root / "semantic-sketch-references"
    reference_root.mkdir()
    shape = Part.makeBox(20, 10, 5)
    artifact = reference_root / "ScriptedSource.brep"
    shape.exportBrep(str(artifact))
    reference = {
        "document_uid": "semantic-sketch",
        "object_name": "ScriptedSource",
    }
    configure_sketcher_references(
        reference_root,
        [
            {
                **reference,
                "label": "Regenerating source",
                "type_id": "Part::Feature",
                "shape_type": str(shape.ShapeType),
                "brep_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "artifact_path": artifact.name,
                "facts": part_shape_facts(shape, max_subelements=32),
                "source_kind": "scripted_publication",
                "source_revision": "a" * 64,
                "transient_topology": True,
                "requires_semantic_interfaces": True,
                "published_interfaces": {
                    "DatumEdge": {
                        "model_id": "semantic-model",
                        "publication_name": "Body",
                        "output_key": "Body",
                        "subelements": ["Edge1"],
                        "geometry": [{"geometry_type": "line"}],
                    },
                    "DatumVertex": {
                        "model_id": "semantic-model",
                        "publication_name": "Body",
                        "output_key": "Body",
                        "subelements": ["Vertex1"],
                        "geometry": [{"geometry_type": "point"}],
                    },
                },
            }
        ],
    )

    raw = api.external_geometry(reference, "Edge1")
    raw_sketch = api.sketch([raw])
    document = App.newDocument("VibeScriptSketchSemanticRejected")
    try:
        try:
            validate_and_solve_sketch(
                document,
                {"Profile": raw_sketch},
                [_output("Profile", raw_sketch)],
            )
        except SketcherCandidateError as exc:
            assert exc.details["stage"] == "external_geometry_semantics"
            assert exc.details["available_interfaces"] == ["DatumEdge", "DatumVertex"]
            assert "not stable" in str(exc)
        else:
            raise AssertionError("Raw Edge1 was accepted on a regenerating source.")
    finally:
        App.closeDocument(document.Name)

    first_external = api.external_geometry(
        reference,
        {"type": "published_interface", "interface_name": "DatumEdge"},
    )
    second_external = api.external_geometry(
        reference,
        {"type": "published_interface", "interface_name": "DatumVertex"},
    )
    repeated_source = api.sketch([first_external, second_external])
    document = App.newDocument("VibeScriptSketchExternalSourceCache")
    try:
        candidate = document.addObject("Sketcher::SketchObject", "Candidate")
        _geometry, _constraints, _indexes, external = populate_sketch_without_solving(
            candidate,
            repeated_source.to_payload(),
            replace_existing=False,
        )
        staged_targets = [
            obj for obj in document.Objects if obj.TypeId == "Part::Feature"
        ]
        assert len(staged_targets) == 1
        assert len(external) == 2
        assert len(sketch_external_reference_records(candidate)) == 2
    finally:
        App.closeDocument(document.Name)

    semantic = api.external_geometry(
        reference,
        {"type": "published_interface", "interface_name": "DatumEdge"},
    )
    local = api.line([0, 0], [2, 0])
    invalid_center = api.constraint(
        "coincident",
        [_point(local, "start"), _point(semantic, "center")],
    )
    invalid_center_sketch = api.sketch([semantic, local], [invalid_center])
    document = App.newDocument("VibeScriptSketchExternalPointRejected")
    try:
        try:
            validate_and_solve_sketch(
                document,
                {"Profile": invalid_center_sketch},
                [_output("Profile", invalid_center_sketch)],
            )
        except SketcherCandidateError as exc:
            assert exc.details["stage"] == "external_geometry_point_selector"
            assert exc.details["native_type"] == "Point"
            assert exc.details["selected_point"] == "center"
            assert exc.details["allowed_points"] == ["point", "start"]
            assert "use one of point, start" in str(exc)
        else:
            raise AssertionError("A line external geometry accepted a center selector.")
    finally:
        App.closeDocument(document.Name)

    semantic = api.external_geometry(
        reference,
        {"type": "published_interface", "interface_name": "DatumEdge"},
    )
    semantic_sketch = api.sketch([semantic])
    document = App.newDocument("VibeScriptSketchSemanticAccepted")
    try:
        outputs = [_output("Profile", semantic_sketch)]
        validation = validate_and_solve_sketch(
            document,
            {"Profile": semantic_sketch},
            outputs,
        )
        resolved = validation["external_geometry"][0]
        assert resolved["interface_name"] == "DatumEdge"
        assert resolved["resolved_subelement"] == "Edge1"
        assert resolved["source_kind"] == "scripted_publication"
        assert resolved["source_revision"] == "a" * 64
    finally:
        App.closeDocument(document.Name)


def _exercise_lifecycle(root: Path) -> None:
    import FreeCAD as App

    document = App.newDocument("VibeScriptSketchLifecycle")
    service = _Service(document, root)
    base = _base_capture(root, document)
    create_capture = {
        **base,
        "operation": "create_program",
        "tool_name": "vibescript.sketcher.create_program",
        "arguments": {
            "program_name": "Production Profile",
            "source": _program_source(),
            "input_schema": {
                "type": "object",
                "properties": {
                    "width": {"type": "number", "exclusiveMinimum": 0},
                    "height": {"type": "number", "exclusiveMinimum": 0},
                },
                "required": ["width", "height"],
                "additionalProperties": False,
            },
            "inputs": {"width": 40.0, "height": 20.0},
            "expected_outputs": [{"name": "Profile", "type": "sketch"}],
        },
    }
    prepared, execution, publication, accepted = _run_candidate(create_capture, service)
    assert execution["sketch_validation"]["fully_constrained"] is True
    assert publication["recompute_deferred"] is True
    live = accepted["live_outputs"]["Profile"]
    profile = document.getObject(live["object_name"])
    assert profile is not None and profile.TypeId == "Sketcher::SketchObject"
    assert profile.GeometryCount == 4
    assert profile.ConstraintCount == 11
    identity = str(profile.Name)
    document.recompute()
    assert profile.FullyConstrained is True
    assert len(profile.Shape.Wires) == 1
    assert profile.Shape.Wires[0].isClosed()

    tampered = deepcopy(execution)
    tampered["sketch_validation"]["geometry_count"] += 1
    tampered["outputs"][0]["sketch_validation"]["geometry_count"] += 1
    try:
        validate_candidate(prepared, tampered)
    except ValueError as exc:
        assert "geometry count" in str(exc)
    else:
        raise AssertionError("Host validation accepted a forged Sketcher geometry count.")

    tampered = deepcopy(execution)
    tampered["sketch_validation"]["underconstraint_guidance"][
        "canonical_operation"
    ] = "api.horizontal"
    tampered["outputs"][0]["sketch_validation"]["underconstraint_guidance"][
        "canonical_operation"
    ] = "api.horizontal"
    try:
        validate_candidate(prepared, tampered)
    except ValueError as exc:
        assert "canonical constraint operation" in str(exc)
    else:
        raise AssertionError("Host validation accepted forged Sketcher guidance.")

    height_constraint = (
        " api.constraint('distance', [right], value=height, name='Height'),\n"
    )
    missing_height_marker = " # height constraint intentionally removed\n"
    failed_capture = {
        **create_capture,
        "operation": "edit_source",
        "tool_name": "vibescript.sketcher.edit_source",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "replacements": [
                {"old": height_constraint, "new": missing_height_marker}
            ],
        },
    }
    failed_prepared = prepare_candidate(failed_capture)
    failed_execution = execute_candidate(failed_prepared, cancellation_check=None)
    assert failed_execution.get("ok") is False
    assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
    failure_details = failed_execution["observed"]["details"]
    assert failure_details["stage"] == "fully_constrained_requirement"
    assert failure_details["degrees_of_freedom"] > 0
    assert failed_execution["failure_stage"] == "external_process"
    assert failed_execution["domain_failure_stage"] == "fully_constrained_requirement"
    assert failed_execution["retry"]["same_call"] is False
    assert failed_execution["retry"]["required_changes"] == [
        failure_details["correction"]
    ]
    assert failure_details["underconstraint_guidance"]["automatic_application"] is False
    retain_candidate(failed_prepared, status="failed", failure=failed_execution)
    failed_inspection = complete_inspection(
        {
            "pack": create_capture["pack"],
            "program_id": prepared["program_id"],
            "project_root": str(root),
            "live_programs": [],
        }
    )
    assert failed_inspection["program"]["working_revision"] == failed_prepared["revision"]
    assert failed_inspection["program"]["accepted_revision"] == accepted["accepted_revision"]
    assert failed_inspection["program"]["latest_candidate"]["status"] == "failed"
    assert profile.VibeCADVibeScriptRevision == accepted["accepted_revision"]

    recovery_capture = {
        **create_capture,
        "operation": "edit_source",
        "tool_name": "vibescript.sketcher.edit_source",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": failed_prepared["revision"],
            "replacements": [
                {"old": missing_height_marker, "new": height_constraint}
            ],
        },
    }
    recovery_prepared, _execution, recovery_publication, accepted = _run_candidate(
        recovery_capture,
        service,
    )
    assert recovery_publication["created_objects"] == []
    assert document.getObject(identity) is profile

    consumer = document.addObject("App::FeaturePython", "NativeSketchConsumer")
    consumer.addProperty("App::PropertyLink", "Profile", "Native")
    consumer.Profile = profile
    update_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.sketcher.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": recovery_prepared["revision"],
            "patch": {"width": 55.0},
        },
    }
    update_prepared, _update_execution, update_publication, updated = _run_candidate(
        update_capture,
        service,
    )
    assert updated["live_outputs"]["Profile"]["object_name"] == identity
    assert document.getObject(identity) is profile
    assert consumer.Profile is profile
    assert update_publication["downstream_references"]["safe_whole_object_uses"]
    document.recompute()
    width_index = profile.getIndexByName("Width")
    assert width_index >= 0
    assert abs(float(profile.Constraints[width_index].Value) - 55.0) < 1.0e-7

    unsafe_consumer = document.addObject("App::FeaturePython", "UnsafeSketchEdgeConsumer")
    unsafe_consumer.addProperty("App::PropertyLinkSub", "ProfileEdge", "Native")
    unsafe_consumer.ProfileEdge = (profile, ["Edge1"])
    unsafe_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.sketcher.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": update_prepared["revision"],
            "patch": {"width": 56.0},
        },
    }
    unsafe_prepared = prepare_candidate(unsafe_capture)
    unsafe_execution = execute_candidate(unsafe_prepared, cancellation_check=None)
    assert unsafe_execution.get("ok") is True, unsafe_execution
    unsafe_validated = validate_candidate(unsafe_prepared, unsafe_execution)
    retain_candidate(unsafe_prepared, status="validated")
    try:
        publish_candidate(service, unsafe_prepared, unsafe_validated)
    except RuntimeError as exc:
        assert "Face/Edge/Vertex references" in str(exc)
        assert "UnsafeSketchEdgeConsumer" in str(exc)
    else:
        raise AssertionError("A transient Sketcher Edge1 consumer was silently accepted.")
    retain_candidate(
        unsafe_prepared,
        status="publication_failed",
        failure={
            "failure_code": "DOMAIN_PUBLICATION_FAILED",
            "failure_stage": "native_call",
            "error": "unsafe Sketcher subelement consumer",
        },
    )
    assert profile.VibeCADVibeScriptRevision == updated["accepted_revision"]
    document.removeObject(unsafe_consumer.Name)

    safe_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.sketcher.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": unsafe_prepared["revision"],
            "patch": {"width": 60.0},
        },
    }
    final_prepared, _execution, final_publication, updated = _run_candidate(
        safe_capture,
        service,
    )
    assert final_publication["created_objects"] == []
    assert document.getObject(identity) is profile
    document.recompute()
    assert abs(float(profile.Constraints[width_index].Value) - 60.0) < 1.0e-7

    inspection = complete_inspection(
        {
            **safe_capture,
            "program_id": prepared["program_id"],
            "live_programs": [],
        }
    )
    assert inspection.get("ok") is True, inspection
    assert inspection["program"]["accepted_revision"] == final_prepared["revision"]

    save_path = root / "sketcher-vibescript.FCStd"
    document.saveAs(str(save_path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(save_path))
    service.document = reopened
    reopened_profile = reopened.getObject(identity)
    assert reopened_profile is not None
    assert reopened_profile.GeometryCount == 4
    assert reopened_profile.ConstraintCount == 11
    assert reopened.getObject("NativeSketchConsumer").Profile is reopened_profile

    delete_capture = {
        **base,
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
        "operation": "delete_program",
        "tool_name": "vibescript.sketcher.delete_program",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": final_prepared["revision"],
            "reason": "integration cleanup",
        },
    }
    try:
        prepared_delete = prepare_delete(delete_capture)
        delete_live_program(service, prepared_delete)
    except RuntimeError as exc:
        assert "reference" in str(exc).lower()
        restore_prepared_delete(prepared_delete)
    else:
        raise AssertionError("Deletion ignored the human-created whole-object consumer.")
    reopened.removeObject("NativeSketchConsumer")
    prepared_delete = prepare_delete(delete_capture)
    deletion = delete_live_program(service, prepared_delete)
    finished = finish_delete(prepared_delete, deletion)
    assert finished["artifacts_deleted"] is True
    assert reopened.getObject(identity) is None
    App.closeDocument(reopened.Name)


def main() -> int:
    exports = (
        "point",
        "line",
        "arc",
        "circle",
        "ellipse",
        "elliptic_arc",
        "hyperbolic_arc",
        "parabolic_arc",
        "bspline",
        "external_geometry",
        "constraint",
        "sketch",
    )
    api = create_domain_api("sketcher", exports, ("sketch",))
    rectangle = _exercise_rectangle(api)
    _exercise_geometry_exports(api)
    _exercise_constraint_families(api)
    _exercise_internal_alignment_index_guards(api)
    _exercise_requirement_failure(api)
    _exercise_model_guidance(api)
    root = Path(tempfile.mkdtemp(prefix="vibecad-sketcher-production-"))
    try:
        _exercise_lifecycle(root)
        _exercise_supported_sketch(root)
        _exercise_external_geometry(root)
        _exercise_semantic_external_geometry(root, api)
    finally:
        shutil.rmtree(root)
    print(
        "Sketcher VibeScript native API integration passed: "
        f"{rectangle['geometry_count']} geometry, "
        f"{rectangle['constraint_count']} constraints, "
        f"DoF={rectangle['degrees_of_freedom']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
