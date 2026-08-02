# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD integration gate for the Draft VibeScript domain."""

from __future__ import annotations

import copy
from contextlib import redirect_stdout
import hashlib
import io
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
from vibescript_draft_worker import (  # noqa: E402
    DraftCandidateError,
    configure_draft_references,
    validate_and_build_draft,
)
from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    delete_live_program,
    mark_programs_stale_from_source,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    DraftDomainAdapter,
    _validate_draft_execution,
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
    _draft_document_snapshot,
    complete_domain_context,
    domain_context_snapshot,
    get_vibescript_pack,
)


EXPORTS = ("wire", "circle", "rectangle", "bspline", "array", "text")
OUTPUT_TYPES = EXPORTS


def _api():
    return create_domain_api("draft", EXPORTS, OUTPUT_TYPES)


def _expect_error(fragment: str, call) -> None:
    try:
        call()
    except (TypeError, ValueError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f"Expected validation failure containing {fragment!r}.")


def _exercise_source_api() -> None:
    import inspect

    api = _api()
    assert api.exported_names == EXPORTS
    assert not hasattr(api, "output")
    for name in EXPORTS:
        signature = str(inspect.signature(getattr(api, name)))
        assert "*args" not in signature
        assert "**" not in signature
        assert inspect.getdoc(getattr(api, name))

    wire = api.wire([[0, 0, 0], [5, 0, 0]], label="Line")
    circle = api.circle(2)
    rectangle = api.rectangle(4, 3)
    spline = api.bspline([[0, 0, 0], [2, 3, 0], [5, 0, 0]])
    array = api.array(rectangle)
    text = api.text("Note")
    assert [
        value.to_payload()["properties"]["graph_id"]
        for value in (wire, circle, rectangle, spline, array, text)
    ] == [f"d{index}" for index in range(1, 7)]
    assert array.to_payload()["arguments"][0] == rectangle.to_payload()
    assert wire.to_payload()["properties"] | {
        "fillet_radius": 0.0,
        "chamfer_size": 0.0,
        "subdivisions": 0,
    } == wire.to_payload()["properties"]
    axis_angle = api.rectangle(
        4,
        2,
        placement={
            "position": [1, 2, 3],
            "axis": [0, 0, 2],
            "angle_degrees": 90,
        },
    ).to_payload()["properties"]["placement"]
    assert axis_angle["position"] == [1.0, 2.0, 3.0]
    assert all(
        abs(observed - expected) < 1.0e-12
        for observed, expected in zip(
            axis_angle["rotation"],
            [0.0, 0.0, 2**-0.5, 2**-0.5],
        )
    )

    _expect_error("2-4096 points", lambda: api.wire([[0, 0, 0]]))
    _expect_error(
        "preceding point",
        lambda: api.wire([[0, 0, 0], [0, 0, 0]]),
    )
    _expect_error(
        "use closed=True",
        lambda: api.wire([[0, 0, 0], [1, 0, 0], [0, 0, 0]]),
    )
    _expect_error(
        "requires closed=True",
        lambda: api.wire([[0, 0, 0], [1, 0, 0]], make_face=True),
    )
    _expect_error(
        "would generate",
        lambda: api.wire(
            [[float(index), 0, 0] for index in range(4096)],
            subdivisions=4096,
        ),
    )
    _expect_error(
        "must be 0 when fillet_radius",
        lambda: api.wire(
            [[0, 0, 0], [4, 0, 0], [4, 4, 0]],
            fillet_radius=0.5,
            subdivisions=1,
        ),
    )
    _expect_error(
        "choose one corner treatment",
        lambda: api.wire(
            [[0, 0, 0], [4, 0, 0], [4, 4, 0]],
            fillet_radius=0.5,
            chamfer_size=0.5,
        ),
    )
    _expect_error(
        "adjacent segment lengths and angles",
        lambda: api.wire(
            [[0, 0, 0], [4, 0, 0], [4, 4, 0]],
            fillet_radius=4,
        ),
    )
    _expect_error("greater than 0", lambda: api.circle(0))
    _expect_error(
        "full 360-degree circle",
        lambda: api.circle(2, start_angle=0, end_angle=90, make_face=True),
    )
    _expect_error("greater than 0", lambda: api.rectangle(0, 2))
    _expect_error(
        "half the shorter side",
        lambda: api.rectangle(4, 2, fillet_radius=1),
    )
    _expect_error(
        "3-4096 points",
        lambda: api.bspline([[0, 0, 0], [1, 0, 0]]),
    )
    _expect_error(
        "requires closed=True",
        lambda: api.bspline(
            [[0, 0, 0], [1, 1, 0], [2, 0, 0]],
            make_face=True,
        ),
    )
    _expect_error(
        "at most 1",
        lambda: api.bspline(
            [[0, 0, 0], [1, 1, 0], [2, 0, 0]],
            parameterization=2,
        ),
    )
    _expect_error("shape-producing", lambda: api.array(text))
    _expect_error("cannot be true", lambda: api.array(wire, use_link=True, fuse=True))
    _expect_error("must be non-zero", lambda: api.array(wire, kind="polar", axis=[0, 0, 0]))
    _expect_error("must be non-zero", lambda: api.array(wire, kind="polar", total_angle_degrees=0))
    _expect_error("product must be", lambda: api.array(wire, count_x=1, count_y=1))
    _expect_error(
        "produce",
        lambda: api.array(
            wire,
            kind="circular",
            radial_distance=1000,
            tangential_distance=0.001,
            number_circles=2,
        ),
    )
    _expect_error("only strings", lambda: api.text(["valid", 2]))
    _expect_error("must not contain empty", lambda: api.text([""]))
    _expect_error(
        "position and quaternion rotation",
        lambda: api.rectangle(2, 3, placement={"position": [0, 0, 0]}),
    )

    pack = get_vibescript_pack("DraftWorkbench")
    assert pack is not None
    description = DraftDomainAdapter(pack).describe_api()
    assert description["api_contract"] == "vibecad-vibescript-draft-api-v1"
    assert [item["name"] for item in description["runtime_exports"]] == list(EXPORTS)
    assert "*args" not in json.dumps(description["runtime_exports"])
    assert description["array_contract"]["input_reference_schema"][
        "x-vibecad-reference"
    ] is True


def _direct_output(name: str, value) -> dict:
    return {"name": name, "type": value.output_type, "definition": value.to_payload()}


def _exercise_isolated_native_objects(root: Path) -> None:
    import FreeCAD as App

    api = _api()
    placement = {"position": [3, 4, 5], "rotation": [0, 0, 0, 1]}
    wire = api.wire(
        [[0, 0, 0], [8, 0, 0], [8, 4, 0], [0, 4, 0]],
        closed=True,
        make_face=True,
        fillet_radius=0.5,
        placement=placement,
        label="Native Wire",
    )
    circle = api.circle(
        3,
        start_angle=-45,
        end_angle=135,
        placement=placement,
        label="Native Arc",
    )
    rectangle = api.rectangle(
        6,
        2,
        make_face=True,
        chamfer_size=0.25,
        label="Native Rectangle",
    )
    spline = api.bspline(
        [[0, 0, 0], [2, 3, 0], [5, 2, 0], [7, 0, 0]],
        parameterization=0.5,
        label="Native Spline",
    )
    copied = api.array(
        rectangle,
        kind="polar",
        count=4,
        total_angle_degrees=270,
        center=[0, 0, 0],
        interval_axis=[0, 0, 2],
        use_link=False,
        label="Copied Array",
    )
    chained = api.array(
        copied,
        interval_x=[20, 0, 0],
        interval_y=[0, 20, 0],
        count_x=2,
        count_y=1,
        use_link=True,
        label="Chained Array",
    )
    circular = api.array(
        rectangle,
        kind="circular",
        radial_distance=10,
        tangential_distance=8,
        number_circles=3,
        symmetry=2,
        use_link=True,
        label="Circular Array",
    )
    subdivided = api.wire(
        [[0, 6, 0], [8, 6, 0], [8, 10, 0]],
        subdivisions=1,
        label="Subdivided Wire",
    )
    note = api.text(
        ["Native", "Draft"],
        placement=placement,
        height=2.5,
        line_spacing=1.2,
        label="Native Text",
    )
    values = {
        "Wire": wire,
        "Arc": circle,
        "Rectangle": rectangle,
        "Spline": spline,
        "Copied": copied,
        "Chained": chained,
        "Circular": circular,
        "Subdivided": subdivided,
        "Note": note,
    }
    expected = [_direct_output(name, value) for name, value in values.items()]
    output_root = root / "direct-worker"
    (output_root / "outputs").mkdir(parents=True)
    configure_draft_references(output_root, [])
    document = App.newDocument("DraftDirectWorker", "Draft Direct Worker", True, True)
    try:
        outputs, validation = validate_and_build_draft(
            document,
            values,
            [{"name": item["name"], "type": item["type"]} for item in expected],
            output_root,
            max_shape_subelements=32,
        )
        assert validation["native_object_count"] == 9
        assert validation["shape_output_count"] == 8
        assert validation["array_output_count"] == 3
        by_name = {item["name"]: item for item in outputs}
        assert by_name["Wire"]["facts"]["shape_type"] == "Face"
        assert by_name["Wire"]["draft_data"]["fillet_radius"] == 0.5
        assert by_name["Wire"]["facts"]["edges"] == 8
        assert by_name["Arc"]["facts"]["shape_type"] == "Edge"
        assert by_name["Rectangle"]["facts"]["shape_type"] == "Face"
        assert by_name["Rectangle"]["draft_data"]["chamfer_size"] == 0.25
        assert by_name["Spline"]["facts"]["shape_type"] == "Edge"
        assert by_name["Copied"]["draft_data"]["use_link"] is False
        assert by_name["Copied"]["draft_data"]["count"] == 4
        assert by_name["Copied"]["draft_data"]["interval_axis"] == [0.0, 0.0, 2.0]
        assert by_name["Chained"]["draft_data"]["source"] == {
            "kind": "program_output",
            "graph_id": copied.to_payload()["properties"]["graph_id"],
            "output_name": "Copied",
        }
        assert by_name["Chained"]["draft_data"]["count"] == 2
        assert by_name["Circular"]["draft_data"]["array_kind"] == "circular"
        assert by_name["Circular"]["draft_data"]["count"] == 21
        assert by_name["Circular"]["draft_data"]["number_circles"] == 3
        assert by_name["Circular"]["draft_data"]["symmetry"] == 2
        assert by_name["Subdivided"]["draft_data"]["subdivisions"] == 1
        assert by_name["Subdivided"]["facts"]["edges"] == 4
        assert by_name["Note"]["draft_data"]["lines"] == ["Native", "Draft"]
        assert all(
            (output_root / item["artifact_path"]).is_file()
            for item in outputs
            if item["type"] != "text"
        )
    finally:
        App.closeDocument(document.Name)

    # Draft deliberately catches a non-planar face failure and leaves a wire;
    # the production worker must convert that silent downgrade into feedback.
    invalid_api = _api()
    invalid = invalid_api.bspline(
        [[0, 0, 0], [2, 2, 2], [4, 0, 0], [2, -2, 0]],
        closed=True,
        make_face=True,
    )
    invalid_root = root / "invalid-worker"
    (invalid_root / "outputs").mkdir(parents=True)
    configure_draft_references(invalid_root, [])
    document = App.newDocument("DraftInvalidWorker", "Draft Invalid Worker", True, True)
    try:
        try:
            validate_and_build_draft(
                document,
                {"Invalid": invalid},
                [{"name": "Invalid", "type": "bspline"}],
                invalid_root,
                max_shape_subelements=16,
            )
        except DraftCandidateError as exc:
            assert exc.details["stage"] == "native_shape_contract"
            assert exc.details["output_name"] == "Invalid"
            assert exc.details["expected_shape_types"] == ["Face"]
            assert "closed planar" in exc.details["correction"]
        else:
            raise AssertionError("A non-planar requested Draft face was silently downgraded.")
    finally:
        App.closeDocument(document.Name)

    # Native Draft retains an excessive corner property even when its geometry
    # helper silently does nothing. The worker must detect topology, not merely
    # echo the retained property value.
    effect_api = _api()
    ineffective = effect_api.wire(
        [[0, 0, 0], [8, 0, 0], [8, 4, 0], [0, 4, 0]],
        closed=True,
        make_face=True,
        fillet_radius=0.5,
    ).to_payload()
    ineffective["properties"]["fillet_radius"] = 100.0
    effect_root = root / "ineffective-property-worker"
    (effect_root / "outputs").mkdir(parents=True)
    configure_draft_references(effect_root, [])
    document = App.newDocument("DraftEffectWorker", "Draft Effect Worker", True, True)
    try:
        native_output = io.StringIO()
        try:
            with redirect_stdout(native_output):
                validate_and_build_draft(
                    document,
                    {"Ineffective": ineffective},
                    [{"name": "Ineffective", "type": "wire"}],
                    effect_root,
                    max_shape_subelements=16,
                )
        except DraftCandidateError as exc:
            assert exc.details["stage"] == "native_parametric_effect"
            assert exc.details["output_name"] == "Ineffective"
            assert exc.details["parameter"] == "fillet_radius"
            assert exc.details["expected_edge_count"] == 8
            assert exc.details["observed_edge_count"] == 4
            assert "Reduce api.wire" in exc.details["correction"]
            assert "too high" in native_output.getvalue()
        else:
            raise AssertionError("A retained but ineffective Draft fillet was accepted.")
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
        return "DraftWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "draft-production-revision"

    def project_scope_snapshot(self) -> dict:
        return {"root": str(self.project_root)}


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


def _program_source() -> str:
    return (
        "place = {'position':[1,2,0], 'axis':[0,0,1], 'angle_degrees':15}\n"
        "wire = api.wire([[0,0,0],[inputs['width'],0,0],"
        "[inputs['width'],inputs['height'],0],[0,inputs['height'],0]], "
        "closed=True, make_face=True, fillet_radius=0.5, "
        "placement=place, label='Panel Wire')\n"
        "guide = api.wire([[0,-5,0],[inputs['width'],-5,0]], "
        "subdivisions=2, label='Layout Guide')\n"
        "circle = api.circle(inputs['radius'], make_face=True, "
        "placement={'position':[15,0,0],'rotation':[0,0,0,1]}, "
        "label='Circle')\n"
        "rectangle = api.rectangle(inputs['width'], inputs['height'], "
        "make_face=True, chamfer_size=0.5, label='Rectangle')\n"
        "spline = api.bspline([[0,8,0],[3,12,0],[7,10,0],[10,8,0]], "
        "parameterization=0.5, label='Spline')\n"
        "ortho = api.array(rectangle, kind='orthogonal', "
        "interval_x=[inputs['pitch'],0,0], interval_y=[0,inputs['pitch'],0], "
        "count_x=2, count_y=2, count_z=1, use_link=True, "
        "label='Link Array')\n"
        "polar = api.array(inputs['external'], kind='polar', count=3, "
        "total_angle_degrees=180, center=[30,0,0], axis=[0,0,1], "
        "interval_axis=[0,0,2], use_link=False, label='External Array')\n"
        "chain = api.array(ortho, kind='orthogonal', interval_x=[0,0,15], "
        "count_x=2, count_y=1, count_z=1, use_link=True, label='Chain Array')\n"
        "circular = api.array(circle, kind='circular', radial_distance=12, "
        "tangential_distance=8, number_circles=3, symmetry=2, use_link=True, "
        "label='Circular Array')\n"
        "note = api.text(['Production Draft', 'Editable native objects'], "
        "placement={'position':[0,20,0],'rotation':[0,0,0,1]}, "
        "height=2.5, line_spacing=1.25, label='Note')\n"
        "result = {'Wire':wire, 'Guide':guide, 'Circle':circle, 'Rectangle':rectangle, "
        "'Spline':spline, 'Ortho':ortho, 'Polar':polar, 'Chain':chain, "
        "'Circular':circular, 'Note':note}\n"
    )


EXPECTED_OUTPUTS = [
    {"name": "Wire", "type": "wire"},
    {"name": "Guide", "type": "wire"},
    {"name": "Circle", "type": "circle"},
    {"name": "Rectangle", "type": "rectangle"},
    {"name": "Spline", "type": "bspline"},
    {"name": "Ortho", "type": "array"},
    {"name": "Polar", "type": "array"},
    {"name": "Chain", "type": "array"},
    {"name": "Circular", "type": "array"},
    {"name": "Note", "type": "text"},
]


def _base_capture(root: Path, document) -> dict:
    import FreeCAD as App

    pack = get_vibescript_pack("DraftWorkbench")
    assert pack is not None
    return {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "draft-production-revision",
        "document_objects": [
            {"name": str(obj.Name), "label": str(obj.Label), "type_id": str(obj.TypeId)}
            for obj in document.Objects
        ],
        "surface": resolve_modeling_surface("DraftWorkbench", "vibescript").summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }


def _run_candidate(captured: dict, service: _Service):
    prepared = prepare_candidate(captured)
    prepared = _finalize_prepared(prepared, service)
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    return prepared, execution, validated, publication, accepted


def _finalize_prepared(prepared: dict, service: _Service) -> dict:
    if prepared.get("reference_requirements") and not prepared.get("finalized"):
        return finalize_candidate(
            prepared,
            capture_reference_inputs(service, prepared),
        )
    return prepared


def _create_capture(base: dict, reference: dict) -> dict:
    return {
        **base,
        "operation": "create_program",
        "tool_name": "vibescript.draft.create_program",
        "arguments": {
            "program_name": "Production Draft",
            "source": _program_source(),
            "input_schema": {
                "type": "object",
                "properties": {
                    "width": {"type": "number", "exclusiveMinimum": 0},
                    "height": {"type": "number", "exclusiveMinimum": 0},
                    "radius": {"type": "number", "exclusiveMinimum": 0},
                    "pitch": {"type": "number", "exclusiveMinimum": 0},
                    "external": _reference_schema(),
                },
                "required": ["width", "height", "radius", "pitch", "external"],
                "additionalProperties": False,
            },
            "inputs": {
                "width": 10.0,
                "height": 6.0,
                "radius": 3.0,
                "pitch": 14.0,
                "external": reference,
            },
            "expected_outputs": EXPECTED_OUTPUTS,
        },
    }


def _assert_live_contract(document, accepted: dict, external) -> tuple[dict[str, Any], dict]:
    from draftutils.utils import get_type

    names = {
        name: value["object_name"] for name, value in accepted["live_outputs"].items()
    }
    objects = {name: document.getObject(object_name) for name, object_name in names.items()}
    expected = {
        "Wire": ("Part::FeaturePython", "Wire"),
        "Guide": ("Part::FeaturePython", "Wire"),
        "Circle": ("Part::Part2DObjectPython", "Circle"),
        "Rectangle": ("Part::Part2DObjectPython", "Rectangle"),
        "Spline": ("Part::FeaturePython", "BSpline"),
        "Ortho": ("Part::FeaturePython", "Array"),
        "Polar": ("Part::FeaturePython", "Array"),
        "Chain": ("Part::FeaturePython", "Array"),
        "Circular": ("Part::FeaturePython", "Array"),
        "Note": ("App::FeaturePython", "Text"),
    }
    for name, (type_id, draft_type) in expected.items():
        obj = objects[name]
        assert obj is not None
        assert obj.TypeId == type_id
        assert get_type(obj) == draft_type
        assert obj.Proxy is not None
        assert "VibeCADDraftValidation" in obj.PropertiesList
    assert objects["Wire"].Closed is True
    assert objects["Wire"].MakeFace is True
    assert len(objects["Wire"].Points) == 4
    assert float(objects["Wire"].FilletRadius) == 0.5
    assert objects["Wire"].Subdivisions == 0
    assert len(objects["Guide"].Points) == 2
    assert objects["Guide"].Subdivisions == 2
    assert len(objects["Guide"].Shape.Edges) == 3
    assert objects["Circle"].MakeFace is True
    assert float(objects["Rectangle"].ChamferSize) == 0.5
    assert abs(float(objects["Spline"].Parameterization) - 0.5) < 1.0e-9
    assert objects["Ortho"].Base is objects["Rectangle"]
    assert objects["Ortho"].Proxy.use_link is True
    assert objects["Ortho"].Count == 4
    assert objects["Polar"].Base is external
    assert objects["Polar"].Proxy.use_link is False
    assert objects["Polar"].Count == 3
    assert list(objects["Polar"].IntervalAxis) == [0.0, 0.0, 2.0]
    assert objects["Chain"].Base is objects["Ortho"]
    assert objects["Chain"].Proxy.use_link is True
    assert objects["Chain"].Count == 2
    assert objects["Circular"].Base is objects["Circle"]
    assert objects["Circular"].ArrayType == "circular"
    assert objects["Circular"].Count == 27
    assert objects["Circular"].NumberCircles == 3
    assert objects["Circular"].Symmetry == 2
    assert list(objects["Note"].Text) == ["Production Draft", "Editable native objects"]
    assert all(not objects[name].Shape.isNull() for name in expected if name != "Note")
    return objects, names


def _exercise_lifecycle(root: Path) -> None:
    import FreeCAD as App
    import Part

    document = App.newDocument("DraftProductionLifecycle")
    external = document.addObject("Part::Feature", "ExternalArrayBase")
    external.Label = "External array base"
    external.Shape = Part.makeBox(2, 3, 1)
    external_name = str(external.Name)
    reference = {
        "document_uid": str(document.Uid),
        "object_name": external_name,
    }
    service = _Service(document, root)
    base = _base_capture(root, document)
    create_capture = _create_capture(base, reference)
    prepared, execution, validated, publication, accepted = _run_candidate(
        create_capture, service
    )
    assert publication["recompute_deferred"] is True
    assert execution["draft_validation"]["native_object_count"] == 10
    assert execution["draft_validation"]["array_output_count"] == 4
    assert len(prepared["resolved_references"]) == 1
    tampered_outputs = []
    for item in validated["outputs"]:
        tampered_item = dict(item)
        tampered_item["draft_data"] = copy.deepcopy(item["draft_data"])
        tampered_outputs.append(tampered_item)
    next(
        item for item in tampered_outputs if item["name"] == "Circular"
    )["draft_data"]["radial_distance"] = 999.0
    try:
        _validate_draft_execution(prepared, execution, tampered_outputs)
    except ValueError as exc:
        assert "radial_distance changed" in str(exc), str(exc)
    else:
        raise AssertionError("Forged Draft circular-array readback was accepted.")
    objects, identities = _assert_live_contract(document, accepted, external)
    original_objects = dict(objects)

    inspection = complete_inspection(
        {**create_capture, "program_id": prepared["program_id"], "live_programs": []}
    )
    assert inspection["program"]["accepted_revision"] == prepared["revision"]

    # A source-side contract failure remains inspectable while the accepted
    # objects and identities stay untouched.
    failed_capture = {
        **create_capture,
        "operation": "edit_source",
        "tool_name": "vibescript.draft.edit_source",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": prepared["revision"],
            "source": create_capture["arguments"]["source"].replace(
                "closed=True, make_face=True",
                "closed=False, make_face=True",
            ),
        },
    }
    failed_prepared = _finalize_prepared(prepare_candidate(failed_capture), service)
    failed_execution = execute_candidate(failed_prepared, cancellation_check=None)
    assert failed_execution.get("ok") is False
    assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
    assert "api.wire" in failed_execution["error"], failed_execution
    assert "requires closed=True" in failed_execution["error"], failed_execution
    failure_details = failed_execution["observed"]["details"]
    assert failure_details["stage"] == "source_validation"
    assert failure_details["operation"] == "wire"
    assert failure_details["parameter"] == "make_face"
    assert failed_execution["domain_failure_stage"] == "source_validation"
    assert failed_execution["retry"]["required_changes"] == [
        failure_details["correction"]
    ]
    retain_candidate(failed_prepared, status="failed", failure=failed_execution)
    assert all(document.getObject(identities[name]) is original_objects[name] for name in identities)
    failed_inspection = complete_inspection(
        {**failed_capture, "program_id": prepared["program_id"], "live_programs": []}
    )
    assert failed_inspection["program"]["working_revision"] == failed_prepared["revision"]
    assert failed_inspection["program"]["accepted_revision"] == prepared["revision"]
    assert failed_inspection["program"]["latest_candidate"]["status"] == "failed"

    recovery_capture = {
        **create_capture,
        "operation": "edit_source",
        "tool_name": "vibescript.draft.edit_source",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": failed_prepared["revision"],
            "source": create_capture["arguments"]["source"],
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
    objects, recovered_identities = _assert_live_contract(document, accepted, external)
    assert recovered_identities == identities
    assert all(objects[name] is original_objects[name] for name in identities)

    # Link/copy mode controls native extension identity and is deliberately
    # rejected for an existing stable output.
    mode_capture = {
        **create_capture,
        "operation": "edit_source",
        "tool_name": "vibescript.draft.edit_source",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": recovery_prepared["revision"],
            "source": create_capture["arguments"]["source"].replace(
                "count_x=2, count_y=2, count_z=1, use_link=True",
                "count_x=2, count_y=2, count_z=1, use_link=False",
            ),
        },
    }
    mode_prepared = _finalize_prepared(prepare_candidate(mode_capture), service)
    mode_execution = execute_candidate(mode_prepared, cancellation_check=None)
    assert mode_execution.get("ok") is True, mode_execution
    mode_validated = validate_candidate(mode_prepared, mode_execution)
    retain_candidate(mode_prepared, status="validated")
    try:
        publish_candidate(service, mode_prepared, mode_validated)
    except RuntimeError as exc:
        assert "cannot change" in str(exc)
        assert "native type" in str(exc) or "array" in str(exc).lower()
    else:
        raise AssertionError("A stable Draft link array silently changed proxy mode.")
    retain_candidate(
        mode_prepared,
        status="publication_failed",
        failure={
            "failure_code": "DOMAIN_PUBLICATION_FAILED",
            "failure_stage": "native_call",
            "error": "Draft array mode drift",
        },
    )
    assert objects["Ortho"].Proxy.use_link is True

    restore_mode_capture = {
        **create_capture,
        "operation": "edit_source",
        "tool_name": "vibescript.draft.edit_source",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": mode_prepared["revision"],
            "source": create_capture["arguments"]["source"],
        },
    }
    restored_prepared, _execution, _validated, _publication, accepted = _run_candidate(
        restore_mode_capture, service
    )
    objects, _ = _assert_live_contract(document, accepted, external)

    consumer = document.addObject("App::FeaturePython", "NativeDraftConsumer")
    consumer.addProperty("App::PropertyLink", "Source", "Native")
    consumer.Source = objects["Ortho"]
    update_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.draft.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": restored_prepared["revision"],
            "patch": {"width": 12.0, "pitch": 16.0},
        },
    }
    update_prepared, _execution, _validated, update_publication, accepted = (
        _run_candidate(update_capture, service)
    )
    assert update_publication["created_objects"] == []
    assert update_publication["downstream_references"]["safe_whole_object_uses"]
    assert consumer.Source is objects["Ortho"]
    assert abs(float(objects["Rectangle"].Length) - 12.0) < 1.0e-9
    assert abs(float(objects["Ortho"].IntervalX.x) - 16.0) < 1.0e-9

    unsafe = document.addObject("App::FeaturePython", "UnsafeDraftEdgeConsumer")
    unsafe.addProperty("App::PropertyLinkSub", "SourceEdge", "Native")
    unsafe.SourceEdge = (objects["Wire"], ["Edge1"])
    unsafe_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.draft.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": update_prepared["revision"],
            "patch": {"height": 7.0},
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
        assert "UnsafeDraftEdgeConsumer" in str(exc)
    else:
        raise AssertionError("A transient Draft Edge1 consumer was silently accepted.")
    retain_candidate(
        unsafe_prepared,
        status="publication_failed",
        failure={
            "failure_code": "DOMAIN_PUBLICATION_FAILED",
            "failure_stage": "native_call",
            "error": "unsafe Draft subelement consumer",
        },
    )
    document.removeObject(unsafe.Name)

    final_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.draft.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": unsafe_prepared["revision"],
            "patch": {"height": 8.0},
        },
    }
    final_prepared, _execution, _validated, _publication, accepted = _run_candidate(
        final_capture, service
    )
    assert abs(float(objects["Rectangle"].Height) - 8.0) < 1.0e-9

    # External native changes visibly stale every accepted output, then the
    # same guarded inputs regenerate against a new BREP-bound revision.
    external.Shape = Part.makeBox(3, 4, 2)
    marked = mark_programs_stale_from_source(external, "Shape")
    assert set(marked) == set(identities.values())
    assert all(objects[name].VibeCADDerivedState == "stale" for name in identities)
    external_capture = {
        **create_capture,
        "operation": "set_inputs",
        "tool_name": "vibescript.draft.set_inputs",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": final_prepared["revision"],
            "patch": {"width": 12.0},
        },
    }
    (
        external_prepared,
        external_execution,
        _validated,
        _publication,
        accepted,
    ) = _run_candidate(external_capture, service)
    assert external_prepared["revision"] != final_prepared["revision"]
    polar = next(
        item for item in external_execution["outputs"] if item["name"] == "Polar"
    )
    assert polar["draft_data"]["source"]["brep_sha256"] == external_prepared[
        "resolved_references"
    ][0]["brep_sha256"]
    assert all(objects[name].VibeCADDerivedState == "accepted" for name in identities)

    snapshot = _draft_document_snapshot(document)
    live_snapshot = {item["name"]: item for item in snapshot["objects"]}
    assert live_snapshot[identities["Ortho"]]["base"]["name"] == identities["Rectangle"]
    assert live_snapshot[identities["Ortho"]]["count"] == 4
    assert live_snapshot[identities["Wire"]]["point_count"] == 4

    provider_snapshot = domain_context_snapshot(service, "draft")
    provider_context = complete_domain_context(provider_snapshot)
    assert provider_context["domain"] == "draft"
    assert provider_context["surface_id"] == resolve_modeling_surface(
        "DraftWorkbench", "vibescript"
    ).surface_id
    assert provider_context["document_draft_objects"]["object_count"] >= 8
    external_candidate = next(
        item
        for item in provider_context["array_source_candidates"]["objects"]
        if item["name"] == external_name
    )
    assert external_candidate["reference"] == reference
    assert external_candidate["eligible_array_source"] is True
    assert external_candidate["facts"]["solids"] == 1
    program_context = next(
        item
        for item in provider_context["programs"]
        if item.get("program_id") == prepared["program_id"]
    )
    assert program_context["accepted_revision"] == external_prepared["revision"]

    save_path = root / "draft-vibescript-production.FCStd"
    document.saveAs(str(save_path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(save_path))
    service.document = reopened
    reopened_external = reopened.getObject(external_name)
    reopened_objects = {
        name: reopened.getObject(object_name) for name, object_name in identities.items()
    }
    assert all(reopened_objects.values())
    assert reopened_objects["Ortho"].Base is reopened_objects["Rectangle"]
    assert reopened_objects["Polar"].Base is reopened_external
    assert reopened_objects["Chain"].Base is reopened_objects["Ortho"]
    assert reopened_objects["Circular"].Base is reopened_objects["Circle"]
    assert reopened_objects["Circular"].ArrayType == "circular"
    assert reopened_objects["Circular"].Count == 27
    assert reopened_objects["Ortho"].Proxy.use_link is True
    assert reopened_objects["Polar"].Proxy.use_link is False
    assert reopened.getObject("NativeDraftConsumer").Source is reopened_objects["Ortho"]
    assert all(
        str(getattr(obj, PROP_PROGRAM_ID, "")) == prepared["program_id"]
        for obj in reopened_objects.values()
    )

    delete_capture = {
        **base,
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
        "operation": "delete_program",
        "tool_name": "vibescript.draft.delete_program",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": external_prepared["revision"],
            "reason": "Draft production integration cleanup",
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
        raise AssertionError("Draft deletion ignored a human-created whole-object consumer.")
    reopened.removeObject("NativeDraftConsumer")
    prepared_delete = prepare_delete(delete_capture)
    deletion = delete_live_program(service, prepared_delete)
    finished = finish_delete(prepared_delete, deletion)
    assert finished["artifacts_deleted"] is True
    assert all(reopened.getObject(name) is None for name in identities.values())
    assert reopened.getObject(external_name) is reopened_external
    App.closeDocument(reopened.Name)


def main() -> int:
    _exercise_source_api()
    root = Path(tempfile.mkdtemp(prefix="vibecad-draft-production-"))
    try:
        _exercise_isolated_native_objects(root)
        _exercise_lifecycle(root)
    finally:
        shutil.rmtree(root)
    digest = hashlib.sha256(_program_source().encode("utf-8")).hexdigest()[:12]
    print(
        "Draft VibeScript native API integration passed: "
        f"{len(EXPECTED_OUTPUTS)} stable outputs, source={digest}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
