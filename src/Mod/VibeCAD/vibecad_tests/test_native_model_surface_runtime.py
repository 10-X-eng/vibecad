# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeModelSurfaceRuntime as runtime_module
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelSurfaceRuntime import NativeModelSurfaceRuntime
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeSurfaceFilling import prepare_surface_filling
from VibeCADNativeSurfaceGeomFill import prepare_surface_geometric_fill
from VibeCADNativeSurfaceSections import prepare_surface_sections
from VibeCADNativeSurfaceExtend import prepare_surface_extend
from VibeCADNativeSurfaceCurveOnMesh import prepare_surface_curve_on_mesh
from VibeCADNativeSurfaceBlendCurve import prepare_surface_blend_curve
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Document:
    Uid = "document-surface"
    Name = "DocumentSurface"


def _runtime():
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("model-surface-unit")
    context = NativeRuntimeContext(
        service=SimpleNamespace(),
        document=document,
        state=state,
        undo_ledger=ledger,
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: "model",
        edit_or_task_active=lambda: False,
    )
    return NativeModelSurfaceRuntime(context), state, document


def _arguments():
    return {
        "operation": "filling",
        "label": "Fair Patch",
        "definition": {
            "constraints": [
                {
                    "kind": "boundary_edge",
                    "object_name": "Boundary",
                    "subelement": "Edge1",
                    "support_face": "Face1",
                    "continuity": "G1",
                },
                {
                    "kind": "curve_edge",
                    "object_name": "Guide",
                    "subelement": "Edge2",
                },
                {
                    "kind": "face",
                    "object_name": "Support",
                    "subelement": "Face2",
                    "continuity": "G2",
                },
                {
                    "kind": "point",
                    "object_name": "Datum",
                    "subelement": "Vertex3",
                },
            ],
            "initial_face": {"object_name": "Seed", "face": "Face1"},
            "degree": 4,
            "points_on_curve": 18,
            "iterations": 3,
            "anisotropy": True,
            "tolerance_2d": 0.00002,
            "tolerance_3d": 0.0002,
            "angular_tolerance": 0.02,
            "curvature_tolerance": 0.2,
            "maximum_degree": 9,
            "maximum_segments": 12,
        },
    }


def _geometric_fill_arguments():
    return {
        "operation": "geom_fill_surface",
        "label": "Curved Boundary Patch",
        "definition": {
            "boundaries": [
                {"object_name": "FirstCurve", "edge": "Edge1"},
                {
                    "object_name": "SecondCurve",
                    "edge": "Edge2",
                    "reversed": True,
                },
                {"object_name": "ThirdCurve", "edge": "Edge1"},
            ],
            "style": "curved",
        },
    }


def _sections_arguments():
    return {
        "operation": "sections",
        "label": "Fair Section Surface",
        "definition": {
            "sections": [
                {"object_name": "FirstSection", "edge": "Edge2"},
                {"object_name": "SecondSection", "edge": "Edge1"},
                {"object_name": "ThirdSection", "edge": "Edge3"},
            ]
        },
    }


def _extend_arguments():
    return {
        "operation": "extend_face",
        "label": "Extended Fairing Face",
        "definition": {
            "object_name": "Fairing",
            "face": "Face2",
            "u_negative": -0.1,
            "u_positive": 0.2,
            "u_symmetric": False,
            "v_negative": 0.15,
            "v_positive": 0.15,
            "v_symmetric": True,
            "tolerance": 0.05,
            "samples_u": 24,
            "samples_v": 18,
        },
    }


def _curve_on_mesh_arguments():
    return {
        "operation": "curve_on_mesh",
        "label": "Fair Mesh Seam",
        "definition": {
            "object_name": "CowlingMesh",
            "anchors": [
                {"origin_mm": [1, 2, 20], "direction": [0, 0, -2]},
                {"origin_mm": [5, 4, 20], "direction": [0, 0, -3]},
                {"origin_mm": [9, 7, 20], "direction": [0, 0, -4]},
            ],
            "closed": True,
            "approximate": False,
            "maximum_degree": 7,
            "continuity": "C1",
            "tolerance": 0.05,
            "split_angle_degrees": 60,
        },
    }


def _blend_curve_arguments():
    return {
        "operation": "blend_curve",
        "label": "Fair Transition Curve",
        "definition": {
            "start": {
                "object_name": "FirstRail",
                "edge": "Edge2",
                "parameter": 0.25,
                "continuity": "G1",
                "size": 1.5,
            },
            "end": {
                "object_name": "SecondRail",
                "edge": "Edge3",
                "parameter": 0.75,
                "continuity": "G4",
                "size": -0.5,
            },
        },
    }


def test_surface_filling_runtime_preflights_before_one_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}
    monkeypatch.setattr(runtime_module, "prepare_surface_filling", lambda uid, value: spec)
    monkeypatch.setattr(
        runtime_module,
        "preflight_surface_filling",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Surface Filling preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_surface_filling",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    result = runtime.mutate_surface(
        _arguments(),
        ticket=state.begin_call(document.Uid, "model.surface"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Surface Filling"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Fair Patch"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_surface_filling


def test_surface_filling_preparation_preserves_constraints_and_controls() -> None:
    spec = prepare_surface_filling("document-surface", _arguments()["definition"])

    assert tuple(item.kind for item in spec.constraints) == (
        "boundary_edge",
        "curve_edge",
        "face",
        "point",
    )
    assert tuple(item.subelement for item in spec.constraints) == (
        "Edge1",
        "Edge2",
        "Face2",
        "Vertex3",
    )
    assert spec.constraints[0].support_face == "Face1"
    assert spec.constraints[0].continuity == "G1"
    assert spec.constraints[1].support_face is None
    assert spec.constraints[1].continuity == "C0"
    assert spec.initial_face is not None
    assert spec.initial_face[0].object_name == "Seed"
    assert spec.initial_face[1] == "Face1"
    assert (spec.degree, spec.maximum_degree, spec.maximum_segments) == (4, 9, 12)


def test_surface_filling_preparation_uses_human_defaults() -> None:
    spec = prepare_surface_filling(
        "document-surface",
        {
            "constraints": [
                {
                    "kind": "boundary_edge",
                    "object_name": "Circle",
                    "subelement": "Edge1",
                }
            ]
        },
    )

    assert spec.degree == 3
    assert spec.points_on_curve == 15
    assert spec.iterations == 2
    assert spec.anisotropy is False
    assert spec.tolerance_2d == 1.0e-5
    assert spec.tolerance_3d == 1.0e-4
    assert spec.angular_tolerance == 0.01
    assert spec.curvature_tolerance == 0.1
    assert spec.maximum_degree == 8
    assert spec.maximum_segments == 9


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        ({"constraints": []}, "1 to 256"),
        (
            {
                "constraints": [
                    {"kind": "point", "object_name": "Point", "subelement": "Vertex1"}
                ]
            },
            "at least one boundary edge",
        ),
        (
            {
                "constraints": [
                    {
                        "kind": "boundary_edge",
                        "object_name": "Boundary",
                        "subelement": "Face1",
                    }
                ]
            },
            "requires one exact EdgeN",
        ),
        (
            {
                "constraints": [
                    {
                        "kind": "boundary_edge",
                        "object_name": "Boundary",
                        "subelement": "Edge1",
                        "continuity": "G1",
                    }
                ]
            },
            "needs a support face",
        ),
        (
            {
                "constraints": [
                    {
                        "kind": "boundary_edge",
                        "object_name": "Boundary",
                        "subelement": "Edge1",
                    }
                ],
                "degree": 9,
                "maximum_degree": 8,
            },
            "must not exceed",
        ),
    ),
)
def test_surface_filling_definition_rejects_malformed_calls(
    definition,
    message,
) -> None:
    with pytest.raises(NativeModelError, match=message):
        prepare_surface_filling("document-surface", definition)


def test_surface_geometric_fill_runtime_preflights_before_one_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "prepare_surface_geometric_fill",
        lambda uid, value: spec,
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_surface_geometric_fill",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Geometric Fill Surface preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_surface_geometric_fill",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    result = runtime.mutate_surface(
        _geometric_fill_arguments(),
        ticket=state.begin_call(document.Uid, "model.surface"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Geometric Fill Surface"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Curved Boundary Patch"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_surface_geometric_fill


def test_surface_geometric_fill_preparation_preserves_order_and_defaults() -> None:
    spec = prepare_surface_geometric_fill(
        "document-surface",
        {
            "boundaries": [
                {"object_name": "First", "edge": "Edge2"},
                {"object_name": "Second", "edge": "Edge1", "reversed": True},
            ]
        },
    )

    assert tuple(item.object_ref.object_name for item in spec.boundaries) == (
        "First",
        "Second",
    )
    assert tuple(item.edge for item in spec.boundaries) == ("Edge2", "Edge1")
    assert tuple(item.reversed for item in spec.boundaries) == (False, True)
    assert spec.style == "stretched"


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        ({"boundaries": []}, "2 to 4"),
        (
            {
                "boundaries": [
                    {"object_name": "First", "edge": "Face1"},
                    {"object_name": "Second", "edge": "Edge1"},
                ]
            },
            "exact EdgeN",
        ),
        (
            {
                "boundaries": [
                    {"object_name": "First", "edge": "Edge1"},
                    {"object_name": "Second", "edge": "Edge1"},
                ],
                "style": "flat",
            },
            "style is invalid",
        ),
        (
            {
                "boundaries": [
                    {"object_name": "First", "edge": "Edge1", "reversed": 1},
                    {"object_name": "Second", "edge": "Edge1"},
                ]
            },
            "reversed must be boolean",
        ),
    ),
)
def test_surface_geometric_fill_definition_rejects_malformed_calls(
    definition,
    message,
) -> None:
    with pytest.raises(NativeModelError, match=message):
        prepare_surface_geometric_fill("document-surface", definition)


def test_surface_sections_runtime_preflights_before_one_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "prepare_surface_sections",
        lambda uid, value: spec,
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_surface_sections",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Surface Sections preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_surface_sections",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    result = runtime.mutate_surface(
        _sections_arguments(),
        ticket=state.begin_call(document.Uid, "model.surface"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Surface Sections"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Fair Section Surface"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_surface_sections


def test_surface_sections_preparation_preserves_exact_order() -> None:
    spec = prepare_surface_sections(
        "document-surface",
        _sections_arguments()["definition"],
    )

    assert tuple(item.object_ref.object_name for item in spec.sections) == (
        "FirstSection",
        "SecondSection",
        "ThirdSection",
    )
    assert tuple(item.edge for item in spec.sections) == ("Edge2", "Edge1", "Edge3")


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        ({"sections": []}, "2 to 256"),
        (
            {
                "sections": [
                    {"object_name": "First", "edge": "Face1"},
                    {"object_name": "Second", "edge": "Edge1"},
                ]
            },
            "exact EdgeN",
        ),
        (
            {
                "sections": [
                    {"object_name": "First", "edge": "Edge1", "reverse": True},
                    {"object_name": "Second", "edge": "Edge1"},
                ]
            },
            "invalid fields",
        ),
        (
            {
                "sections": [
                    {"object_name": "First", "edge": "Edge1"},
                    {"object_name": "First", "edge": "Edge1"},
                ]
            },
            "must be distinct",
        ),
    ),
)
def test_surface_sections_definition_rejects_malformed_calls(
    definition,
    message,
) -> None:
    with pytest.raises(NativeModelError, match=message):
        prepare_surface_sections("document-surface", definition)


def test_surface_extend_runtime_preflights_before_one_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}
    monkeypatch.setattr(runtime_module, "prepare_surface_extend", lambda uid, value: spec)
    monkeypatch.setattr(
        runtime_module,
        "preflight_surface_extend",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Surface Extend preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_surface_extend",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    result = runtime.mutate_surface(
        _extend_arguments(),
        ticket=state.begin_call(document.Uid, "model.surface"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Surface Extend Face"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Extended Fairing Face"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_surface_extend


def test_surface_extend_preparation_preserves_controls_and_human_defaults() -> None:
    spec = prepare_surface_extend(
        "document-surface",
        _extend_arguments()["definition"],
    )
    assert spec.object_ref.object_name == "Fairing" and spec.face == "Face2"
    assert (spec.u_negative, spec.u_positive, spec.u_symmetric) == (-0.1, 0.2, False)
    assert (spec.v_negative, spec.v_positive, spec.v_symmetric) == (0.15, 0.15, True)
    assert (spec.tolerance, spec.samples_u, spec.samples_v) == (0.05, 24, 18)

    defaults = prepare_surface_extend(
        "document-surface",
        {"object_name": "Plane", "face": "Face1"},
    )
    assert (
        defaults.u_negative,
        defaults.u_positive,
        defaults.u_symmetric,
        defaults.v_negative,
        defaults.v_positive,
        defaults.v_symmetric,
        defaults.tolerance,
        defaults.samples_u,
        defaults.samples_v,
    ) == (0.05, 0.05, True, 0.05, 0.05, True, 0.1, 32, 32)


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        ({"object_name": "Plane", "face": "Edge1"}, "exact FaceN"),
        (
            {
                "object_name": "Plane",
                "face": "Face1",
                "u_negative": 0.1,
                "u_positive": 0.2,
            },
            "Symmetric.*U values must be equal",
        ),
        (
            {"object_name": "Plane", "face": "Face1", "samples_u": 1},
            "integer from 2 to 512",
        ),
        (
            {"object_name": "Plane", "face": "Face1", "u_symmetric": 1},
            "must be boolean",
        ),
        (
            {"object_name": "Plane", "face": "Face1", "tolerance": float("inf")},
            "finite range",
        ),
    ),
)
def test_surface_extend_definition_rejects_malformed_calls(
    definition,
    message,
) -> None:
    with pytest.raises(NativeModelError, match=message):
        prepare_surface_extend("document-surface", definition)


def test_surface_curve_on_mesh_runtime_preflights_before_one_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "prepare_surface_curve_on_mesh",
        lambda uid, value: spec,
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_surface_curve_on_mesh",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Curve on Mesh preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_surface_curve_on_mesh",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    result = runtime.mutate_surface(
        _curve_on_mesh_arguments(),
        ticket=state.begin_call(document.Uid, "model.surface"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Curve on Mesh"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Fair Mesh Seam"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_surface_curve_on_mesh


def test_surface_curve_on_mesh_preparation_preserves_controls_and_defaults() -> None:
    spec = prepare_surface_curve_on_mesh(
        "document-surface",
        _curve_on_mesh_arguments()["definition"],
    )
    assert spec.object_ref.object_name == "CowlingMesh"
    assert tuple(anchor.origin_mm for anchor in spec.anchors) == (
        (1.0, 2.0, 20.0),
        (5.0, 4.0, 20.0),
        (9.0, 7.0, 20.0),
    )
    assert all(anchor.direction == (0.0, 0.0, -1.0) for anchor in spec.anchors)
    assert (
        spec.closed,
        spec.approximate,
        spec.maximum_degree,
        spec.continuity,
        spec.tolerance,
        spec.split_angle_degrees,
    ) == (True, False, 7, "C1", 0.05, 60.0)

    defaults = prepare_surface_curve_on_mesh(
        "document-surface",
        {
            "object_name": "Mesh",
            "anchors": [
                {"origin_mm": [0, 0, 1], "direction": [0, 0, -1]},
                {"origin_mm": [1, 0, 1], "direction": [0, 0, -1]},
            ],
        },
    )
    assert (
        defaults.closed,
        defaults.approximate,
        defaults.maximum_degree,
        defaults.continuity,
        defaults.tolerance,
        defaults.split_angle_degrees,
    ) == (False, True, 5, "C2", 0.2, 45.0)


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        ({"object_name": "Mesh", "anchors": []}, "2 to 64"),
        (
            {
                "object_name": "Mesh",
                "anchors": [
                    {"origin_mm": [0, 0, 1], "direction": [0, 0, 0]},
                    {"origin_mm": [1, 0, 1], "direction": [0, 0, -1]},
                ],
            },
            "direction must be nonzero",
        ),
        (
            {
                "object_name": "Mesh",
                "anchors": [
                    {"origin_mm": [0, 0, 1], "direction": [0, 0, -1]},
                    {"origin_mm": [1, 0, 1], "direction": [0, 0, -1]},
                ],
                "maximum_degree": 9,
            },
            "integer from 1 to 8",
        ),
        (
            {
                "object_name": "Mesh",
                "anchors": [
                    {"origin_mm": [0, 0, 1], "direction": [0, 0, -1]},
                    {"origin_mm": [1, 0, 1], "direction": [0, 0, -1]},
                ],
                "continuity": "G1",
            },
            "must be C0, C1, C2, or C3",
        ),
        (
            {
                "object_name": "Mesh",
                "anchors": [
                    {"origin_mm": [0, 0, 1], "direction": [0, 0, -1]},
                    {"origin_mm": [1, 0, 1], "direction": [0, 0, -1]},
                ],
                "split_angle_degrees": 181,
            },
            "outside its finite range",
        ),
    ),
)
def test_surface_curve_on_mesh_definition_rejects_malformed_calls(
    definition,
    message,
) -> None:
    with pytest.raises(NativeModelError, match=message):
        prepare_surface_curve_on_mesh("document-surface", definition)


def test_surface_blend_curve_runtime_preflights_before_one_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "prepare_surface_blend_curve",
        lambda uid, value: spec,
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_surface_blend_curve",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Blend Curve preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_surface_blend_curve",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    result = runtime.mutate_surface(
        _blend_curve_arguments(),
        ticket=state.begin_call(document.Uid, "model.surface"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Blend Curve"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Fair Transition Curve"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_surface_blend_curve


def test_surface_blend_curve_preparation_preserves_controls_and_human_defaults() -> None:
    spec = prepare_surface_blend_curve(
        "document-surface",
        _blend_curve_arguments()["definition"],
    )
    assert spec.start.object_ref.object_name == "FirstRail"
    assert (spec.start.edge, spec.start.parameter, spec.start.continuity, spec.start.size) == (
        "Edge2",
        0.25,
        "G1",
        1.5,
    )
    assert spec.end.object_ref.object_name == "SecondRail"
    assert (spec.end.edge, spec.end.parameter, spec.end.continuity, spec.end.size) == (
        "Edge3",
        0.75,
        "G4",
        -0.5,
    )

    defaults = prepare_surface_blend_curve(
        "document-surface",
        {
            "start": {"object_name": "First", "edge": "Edge1"},
            "end": {"object_name": "Second", "edge": "Edge1"},
        },
    )
    assert (
        defaults.start.parameter,
        defaults.start.continuity,
        defaults.start.size,
        defaults.end.parameter,
        defaults.end.continuity,
        defaults.end.size,
    ) == (0.0, "G2", 1.0, 0.0, "G2", 1.0)


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        ({"start": {}, "end": {}}, "start requires one exact edge"),
        (
            {
                "start": {"object_name": "First", "edge": "Face1"},
                "end": {"object_name": "Second", "edge": "Edge1"},
            },
            "start requires exact EdgeN",
        ),
        (
            {
                "start": {
                    "object_name": "First",
                    "edge": "Edge1",
                    "continuity": "C2",
                },
                "end": {"object_name": "Second", "edge": "Edge1"},
            },
            "continuity must be C0, G1, G2, G3, or G4",
        ),
        (
            {
                "start": {
                    "object_name": "First",
                    "edge": "Edge1",
                    "parameter": 1.1,
                },
                "end": {"object_name": "Second", "edge": "Edge1"},
            },
            "parameter is outside its finite range",
        ),
        (
            {
                "start": {"object_name": "First", "edge": "Edge1"},
                "end": {"object_name": "First", "edge": "Edge1"},
            },
            "start and end edges must be distinct",
        ),
    ),
)
def test_surface_blend_curve_definition_rejects_malformed_calls(
    definition,
    message,
) -> None:
    with pytest.raises(NativeModelError, match=message):
        prepare_surface_blend_curve("document-surface", definition)
