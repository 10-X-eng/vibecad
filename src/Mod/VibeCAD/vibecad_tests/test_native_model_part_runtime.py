# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeModelPartRuntime as runtime_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelPartRuntime import NativeModelPartRuntime
from VibeCADNativePartPrimitives import prepare_part_primitive
from VibeCADNativePartBuilder import prepare_part_builder
from VibeCADNativePartExtrude import prepare_part_extrude
from VibeCADNativePartRevolve import prepare_part_revolve
from VibeCADNativePartMirror import prepare_part_mirror
from VibeCADNativePartMakeFace import prepare_part_make_face
from VibeCADNativePartRuledSurface import prepare_part_ruled_surface
from VibeCADNativePartLoft import prepare_part_loft
from VibeCADNativePartSweep import prepare_part_sweep
from VibeCADNativePartCrossSections import prepare_part_cross_sections
from VibeCADNativePartOffset import prepare_part_offset, prepare_part_offset_2d
from VibeCADNativePartProjection import prepare_part_projection
from VibeCADNativePartCompound import prepare_part_compound
from VibeCADNativePartCompoundFilter import prepare_part_compound_filter
from VibeCADNativePartDefeature import prepare_part_defeature
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Document:
    Uid = "document-part"
    Name = "DocumentPart"


def _runtime():
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("model-part-unit")
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
    return NativeModelPartRuntime(context), state, document


def _placement():
    return {
        "origin_mm": {"x": 1.0, "y": 2.0, "z": 3.0},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": 15.0,
        },
    }


def _plane_arguments():
    return {
        "operation": "primitive",
        "label": "Exact Plane",
        "placement": _placement(),
        "definition": {"kind": "plane", "length_mm": 12.0, "width_mm": 8.0},
    }


def _builder_arguments():
    return {
        "operation": "builder",
        "label": "Exact Edge",
        "definition": {
            "kind": "edge_from_vertices",
            "inputs": [
                {
                    "object_name": "Points",
                    "subelements": ["Vertex1", "Vertex2"],
                }
            ],
        },
    }


def _extrude_arguments():
    return {
        "operation": "extrude",
        "label": "Exact Extrude",
        "definition": {
            "sources": [{"object_name": "Profile"}],
            "direction": {
                "kind": "custom",
                "vector": {"x": 1.0, "y": 2.0, "z": 3.0},
            },
            "length_along_mm": 14.0,
            "length_against_mm": 3.0,
            "symmetric": False,
            "reversed": True,
            "taper_along_degrees": 4.0,
            "taper_against_degrees": -2.0,
            "solid": True,
        },
    }


def _revolve_arguments():
    return {
        "operation": "revolve",
        "label": "Exact Revolve",
        "definition": {
            "sources": [{"object_name": "Profile"}],
            "axis": {
                "kind": "custom",
                "base_mm": {"x": 1.0, "y": 2.0, "z": 3.0},
                "direction": {"x": 0.0, "y": 1.0, "z": 0.0},
            },
            "angle_degrees": -135.0,
            "symmetric": True,
            "solid": False,
        },
    }


def _mirror_arguments():
    return {
        "operation": "mirror",
        "label": "Exact Mirror",
        "definition": {
            "sources": [{"object_name": "Solid"}],
            "plane": {
                "kind": "yz",
                "base_mm": {"x": 4.0, "y": 5.0, "z": 6.0},
            },
        },
    }


def _make_face_arguments():
    return {
        "operation": "make_face",
        "label": "Exact Face",
        "definition": {
            "sources": [
                {"object_name": "OuterWire"},
                {"object_name": "InnerWire"},
            ]
        },
    }


def _ruled_surface_arguments():
    return {
        "operation": "ruled_surface",
        "label": "Exact Ruled Surface",
        "definition": {
            "curves": [
                {"object_name": "Curves", "subelement": "Edge1"},
                {"object_name": "Curves", "subelement": "Edge2"},
            ]
        },
    }


def _loft_arguments():
    return {
        "operation": "loft",
        "label": "Exact Loft",
        "definition": {
            "profiles": [
                {"object_name": "Profiles", "subelement": "Wire1"},
                {"object_name": "SecondProfile"},
                {"object_name": "Profiles", "subelement": "Face2"},
            ],
            "solid": True,
            "ruled": False,
            "closed": False,
        },
    }


def _sweep_arguments():
    return {
        "operation": "sweep",
        "label": "Exact Sweep",
        "definition": {
            "profiles": [
                {"object_name": "Profiles", "subelement": "Wire1"},
                {"object_name": "SecondProfile"},
                {"object_name": "Profiles", "subelement": "Face2"},
            ],
            "path": {
                "object_name": "Path",
                "subelements": ["Edge1", "Edge2"],
            },
            "solid": True,
            "frenet": False,
        },
    }


def _cross_sections_arguments():
    return {
        "operation": "cross_sections",
        "label": "Exact Cross Sections",
        "definition": {
            "sources": [
                {"object_name": "WholeSource"},
                {
                    "object_name": "SelectedSource",
                    "subelements": ["Face1", "Solid2"],
                },
            ],
            "plane": "xz",
            "distribution": {
                "kind": "series",
                "position_mm": 4.0,
                "count": 3,
                "distance_mm": 2.0,
                "both_sides": True,
            },
        },
    }


def _offset_arguments():
    return {
        "operation": "offset_3d",
        "label": "Exact 3D Offset",
        "definition": {
            "source": {"object_name": "OffsetSource"},
            "value_mm": -2.5,
            "mode": "pipe",
            "join": "tangent",
            "intersection": True,
            "self_intersection": False,
            "fill": True,
        },
    }


def _offset_2d_arguments():
    return {
        "operation": "offset_2d",
        "label": "Exact 2D Offset",
        "definition": {
            "source": {"object_name": "PlanarSource"},
            "value_mm": 2.5,
            "mode": "pipe",
            "join": "intersection",
            "intersection": True,
            "fill": False,
        },
    }


def _projection_arguments():
    return {
        "operation": "project_surface",
        "label": "Exact Surface Projection",
        "definition": {
            "target": {"object_name": "Target", "subelement": "Face2"},
            "sources": [
                {"object_name": "Profile", "subelement": "Face1"},
                {"object_name": "Edges", "subelement": "Edge3"},
            ],
            "mode": "faces",
            "height_mm": 12.5,
            "offset_mm": -3.0,
            "direction_xyz": [0.0, 0.0, -0.5],
        },
    }


def _compound_arguments():
    return {
        "operation": "compound",
        "label": "Exact Compound",
        "definition": {
            "sources": [
                {"object_name": "FirstShape"},
                {"object_name": "SecondShape"},
            ]
        },
    }


def _compound_filter_arguments():
    return {
        "operation": "compound_filter",
        "label": "Exact Compound Filter",
        "definition": {
            "source": {"object_name": "CompoundSource"},
            "mode": "specific_items",
            "selectors": [0, [2, 8, 2], [None, None, -1]],
            "invert": False,
        },
    }


def _defeature_arguments():
    return {
        "operation": "defeature",
        "label": "Healed Housings",
        "definition": {
            "sources": [
                {
                    "object_name": "FirstHousing",
                    "faces": ["Face3", "Face4"],
                },
                {
                    "object_name": "SecondHousing",
                    "faces": ["Face8"],
                },
            ]
        },
    }


def test_part_runtime_prepares_before_routing_one_immediate_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    placement = object()
    spec = object()
    captured = {}

    monkeypatch.setattr(runtime_module, "prepare_part_primitive", lambda value: spec)
    monkeypatch.setattr(
        runtime_module,
        "part_placement_from_mapping",
        lambda value: placement,
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_primitive",
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

    result = runtime.mutate_part(
        _plane_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part Primitive"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Plane"
    assert draft.placement is placement
    assert draft.spec is spec
    assert captured["verify"] is runtime_module.verify_part_primitive


def test_retired_or_cross_family_operation_is_rejected_without_compatibility() -> None:
    runtime, state, document = _runtime()
    arguments = _plane_arguments()
    arguments["operation"] = "plane"

    with pytest.raises(NativeArgumentError, match="operation is unavailable"):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        (
            {
                "kind": "line",
                "start_x_mm": 1.0,
                "start_y_mm": 2.0,
                "start_z_mm": 3.0,
                "end_x_mm": 1.0,
                "end_y_mm": 2.0,
                "end_z_mm": 3.0,
            },
            "distinct endpoints",
        ),
        (
            {
                "kind": "ellipse",
                "major_radius_mm": 2.0,
                "minor_radius_mm": 3.0,
                "start_degrees": 0.0,
                "end_degrees": 180.0,
            },
            "minor radius",
        ),
        (
            {
                "kind": "helix",
                "pitch_mm": 1.0,
                "height_mm": 10.0,
                "radius_mm": 1.0,
                "taper_degrees": -45.0,
                "handedness": "right",
            },
            "positive end radius",
        ),
        (
            {
                "kind": "circle",
                "radius_mm": 2.0,
                "start_degrees": 180.0,
                "end_degrees": 90.0,
            },
            "increasing start and end",
        ),
    ),
)
def test_cross_parameter_failures_are_detected_before_transaction(
    monkeypatch,
    definition,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _plane_arguments()
    arguments["definition"] = definition
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


@pytest.mark.parametrize(
    ("definition", "kind", "native"),
    (
        (
            {"kind": "plane", "length_mm": 4.0, "width_mm": 3.0},
            "plane",
            {"Length": 4.0, "Width": 3.0},
        ),
        (
            {
                "kind": "helix",
                "pitch_mm": 2.0,
                "height_mm": 8.0,
                "radius_mm": 3.0,
                "taper_degrees": 5.0,
                "handedness": "left",
            },
            "helix",
            {
                "Pitch": 2.0,
                "Height": 8.0,
                "Radius": 3.0,
                "Angle": 5.0,
                "LocalCoord": 1,
                "Style": 1,
            },
        ),
        (
            {"kind": "spiral", "growth_mm": 1.0, "rotations": 2.5, "radius_mm": 0.0},
            "spiral",
            {"Growth": 1.0, "Rotations": 2.5, "Radius": 0.0},
        ),
        (
            {
                "kind": "regular_polygon",
                "sides": 6,
                "circumradius_mm": 2.0,
            },
            "regular_polygon",
            {"Polygon": 6, "Circumradius": 2.0},
        ),
    ),
)
def test_primitive_preparation_preserves_exact_native_parameters(
    definition,
    kind,
    native,
) -> None:
    spec = prepare_part_primitive(definition)

    assert spec.kind == kind
    assert spec.parameters == native


def test_definition_rejects_extra_fields_before_any_object_can_be_created() -> None:
    with pytest.raises(NativeModelError, match="does not match its kind"):
        prepare_part_primitive(
            {
                "kind": "plane",
                "length_mm": 4.0,
                "width_mm": 3.0,
                "legacy_height_mm": 2.0,
            }
        )


def test_part_builder_runtime_preflights_before_one_immediate_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(runtime_module, "prepare_part_builder", lambda uid, value: spec)
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_builder",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_builder_shape",
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

    result = runtime.mutate_part(
        _builder_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part Builder Shape"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Edge"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_builder_shape


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        (
            {
                "kind": "edge_from_vertices",
                "inputs": [
                    {
                        "object_name": "Points",
                        "subelements": ["Vertex1"],
                    }
                ],
            },
            "exactly two vertices",
        ),
        (
            {
                "kind": "face_from_edges",
                "inputs": [
                    {
                        "object_name": "Wire",
                        "subelements": ["Vertex1"],
                    }
                ],
                "planar": True,
            },
            "wrong subelement type",
        ),
        (
            {
                "kind": "solid_from_shell",
                "source": {"object_name": "Shell"},
                "refine": False,
                "legacy_copy": True,
            },
            "does not match its kind",
        ),
    ),
)
def test_part_builder_definition_failures_precede_preflight_and_transaction(
    monkeypatch,
    definition,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _builder_arguments()
    arguments["definition"] = definition
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_builder",
        lambda *_args: pytest.fail("preflight started"),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_part_builder_preparation_preserves_exact_controls_and_targets() -> None:
    spec = prepare_part_builder(
        "document-part",
        {
            "kind": "shell_from_faces",
            "inputs": [
                {"object_name": "BoxA", "subelements": ["Face1", "Face2"]},
                {"object_name": "BoxB", "subelements": ["Face3"]},
            ],
            "all_faces": True,
            "refine": False,
        },
    )

    assert spec.kind == "shell_from_faces"
    assert spec.all_faces is True
    assert spec.refine is False
    assert tuple(item.object_ref.object_name for item in spec.inputs) == (
        "BoxA",
        "BoxB",
    )
    assert tuple(item.subelements for item in spec.inputs) == (
        ("Face1", "Face2"),
        ("Face3",),
    )


def test_part_extrude_runtime_preflights_before_one_immediate_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(runtime_module, "prepare_part_extrude", lambda uid, value: spec)
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_extrude",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Part Extrude preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_extrude",
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

    result = runtime.mutate_part(
        _extrude_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part Extrude"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Extrude"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_extrude


def test_part_extrude_preparation_preserves_every_live_control() -> None:
    spec = prepare_part_extrude(
        "document-part",
        _extrude_arguments()["definition"],
    )

    assert tuple(ref.object_name for ref in spec.source_refs) == ("Profile",)
    assert spec.direction_kind == "custom"
    assert spec.direction_vector == (1.0, 2.0, 3.0)
    assert spec.direction_edge is None
    assert spec.length_along == 14.0
    assert spec.length_against == 3.0
    assert spec.symmetric is False
    assert spec.reversed is True
    assert spec.taper_along == 4.0
    assert spec.taper_against == -2.0
    assert spec.solid is True


def test_part_extrude_edge_direction_retains_exact_current_subelement() -> None:
    definition = dict(_extrude_arguments()["definition"])
    definition["direction"] = {
        "kind": "edge",
        "edge": {"object_name": "Direction", "subelement": "Edge3"},
    }

    spec = prepare_part_extrude("document-part", definition)

    assert spec.direction_kind == "edge"
    assert spec.direction_vector is None
    assert spec.direction_edge is not None
    assert spec.direction_edge.object_ref.object_name == "Direction"
    assert spec.direction_edge.subelement == "Edge3"


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (
            {"direction": {"kind": "custom", "vector": {"x": 0, "y": 0, "z": 0}}},
            "non-zero",
        ),
        (
            {"length_along_mm": 5.0, "length_against_mm": -5.0},
            "total length",
        ),
        (
            {
                "direction": {
                    "kind": "edge",
                    "edge": {"object_name": "Direction", "subelement": "Face1"},
                }
            },
            "exact EdgeN",
        ),
        (
            {
                "sources": [
                    {"object_name": "Profile"},
                    {"object_name": "Profile"},
                ]
            },
            "must be unique",
        ),
    ),
)
def test_part_extrude_definition_failures_precede_preflight_and_transaction(
    monkeypatch,
    change,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _extrude_arguments()
    arguments["definition"] = {**arguments["definition"], **change}
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_extrude",
        lambda *_args: pytest.fail("preflight started"),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_part_revolve_runtime_preflights_before_one_immediate_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(runtime_module, "prepare_part_revolve", lambda uid, value: spec)
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_revolve",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Part Revolve preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_revolve",
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

    result = runtime.mutate_part(
        _revolve_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part Revolve"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Revolve"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_revolve


def test_part_revolve_preparation_preserves_every_live_control() -> None:
    spec = prepare_part_revolve(
        "document-part",
        _revolve_arguments()["definition"],
    )

    assert tuple(ref.object_name for ref in spec.source_refs) == ("Profile",)
    assert spec.axis.kind == "custom"
    assert spec.axis.base == (1.0, 2.0, 3.0)
    assert spec.axis.direction == (0.0, 1.0, 0.0)
    assert spec.axis.reference is None
    assert spec.axis.subelement is None
    assert spec.angle == -135.0
    assert spec.symmetric is True
    assert spec.solid is False


@pytest.mark.parametrize("with_subelement", (False, True))
def test_part_revolve_edge_axis_retains_exact_current_reference(with_subelement) -> None:
    definition = dict(_revolve_arguments()["definition"])
    reference = {"object_name": "Axis"}
    if with_subelement:
        reference["subelement"] = "Edge3"
    definition["axis"] = {"kind": "edge", "reference": reference}
    definition["angle_degrees"] = 0.0

    spec = prepare_part_revolve("document-part", definition)

    assert spec.axis.kind == "edge"
    assert spec.axis.base is None
    assert spec.axis.direction is None
    assert spec.axis.reference is not None
    assert spec.axis.reference.object_name == "Axis"
    assert spec.axis.subelement == ("Edge3" if with_subelement else None)
    assert spec.angle == 0.0


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (
            {
                "axis": {
                    "kind": "custom",
                    "base_mm": {"x": 0, "y": 0, "z": 0},
                    "direction": {"x": 0, "y": 0, "z": 0},
                }
            },
            "non-zero",
        ),
        ({"angle_degrees": 0.0}, "angle must be non-zero"),
        (
            {
                "axis": {
                    "kind": "edge",
                    "reference": {"object_name": "Axis", "subelement": "Face1"},
                }
            },
            "exact EdgeN",
        ),
        (
            {
                "sources": [
                    {"object_name": "Profile"},
                    {"object_name": "Profile"},
                ]
            },
            "must be unique",
        ),
    ),
)
def test_part_revolve_definition_failures_precede_preflight_and_transaction(
    monkeypatch,
    change,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _revolve_arguments()
    arguments["definition"] = {**arguments["definition"], **change}
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_revolve",
        lambda *_args: pytest.fail("preflight started"),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_part_mirror_runtime_preflights_before_one_immediate_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(runtime_module, "prepare_part_mirror", lambda uid, value: spec)
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_mirror",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Part Mirror preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_mirror",
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

    result = runtime.mutate_part(
        _mirror_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part Mirror"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Mirror"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_mirror


def test_part_mirror_preparation_preserves_axis_plane_and_base() -> None:
    spec = prepare_part_mirror("document-part", _mirror_arguments()["definition"])

    assert tuple(ref.object_name for ref in spec.source_refs) == ("Solid",)
    assert spec.plane.kind == "yz"
    assert spec.plane.base == (4.0, 5.0, 6.0)
    assert spec.plane.reference is None
    assert spec.plane.subelement is None


@pytest.mark.parametrize("subelement", (None, "Face3", "Edge4"))
def test_part_mirror_reference_retains_exact_object_and_optional_subelement(
    subelement,
) -> None:
    definition = dict(_mirror_arguments()["definition"])
    reference = {"object_name": "MirrorPlane"}
    if subelement is not None:
        reference["subelement"] = subelement
    definition["plane"] = {"kind": "reference", "reference": reference}

    spec = prepare_part_mirror("document-part", definition)

    assert spec.plane.kind == "reference"
    assert spec.plane.base is None
    assert spec.plane.reference is not None
    assert spec.plane.reference.object_name == "MirrorPlane"
    assert spec.plane.subelement == subelement


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (
            {
                "plane": {
                    "kind": "yz",
                    "base_mm": {"x": 0, "y": 0, "z": 0},
                    "normal": {"x": 1, "y": 0, "z": 0},
                }
            },
            "do not match",
        ),
        (
            {
                "plane": {
                    "kind": "reference",
                    "reference": {
                        "object_name": "MirrorPlane",
                        "subelement": "Vertex1",
                    },
                }
            },
            "exact FaceN or EdgeN",
        ),
        (
            {
                "sources": [
                    {"object_name": "Solid"},
                    {"object_name": "Solid"},
                ]
            },
            "must be unique",
        ),
    ),
)
def test_part_mirror_definition_failures_precede_preflight_and_transaction(
    monkeypatch,
    change,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _mirror_arguments()
    arguments["definition"] = {**arguments["definition"], **change}
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_mirror",
        lambda *_args: pytest.fail("preflight started"),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_make_face_runtime_preflights_before_one_immediate_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(runtime_module, "prepare_part_make_face", lambda uid, value: spec)
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_make_face",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Face From Wires preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_make_face",
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

    result = runtime.mutate_part(
        _make_face_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Face From Wires"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Face"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_make_face


def test_make_face_preparation_preserves_exact_ordered_whole_object_sources() -> None:
    spec = prepare_part_make_face(
        "document-part",
        _make_face_arguments()["definition"],
    )

    assert tuple(ref.object_name for ref in spec.source_refs) == (
        "OuterWire",
        "InnerWire",
    )


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        ({"sources": []}, "requires 1 to 32"),
        (
            {
                "sources": [
                    {"object_name": "Wire"},
                    {"object_name": "Wire"},
                ]
            },
            "must be unique",
        ),
        (
            {
                "sources": [{"object_name": "Wire", "subelement": "Wire1"}],
            },
            "source target is invalid",
        ),
        (
            {
                "sources": [{"object_name": "Wire"}],
                "face_maker": "Part::FaceMakerSimple",
            },
            "exact sources",
        ),
    ),
)
def test_make_face_definition_failures_precede_preflight_and_transaction(
    monkeypatch,
    definition,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _make_face_arguments()
    arguments["definition"] = definition
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_make_face",
        lambda *_args: pytest.fail("preflight started"),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_ruled_surface_runtime_preflights_before_one_immediate_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(
        runtime_module,
        "prepare_part_ruled_surface",
        lambda uid, value: spec,
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_ruled_surface",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Ruled Surface preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_ruled_surface",
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

    result = runtime.mutate_part(
        _ruled_surface_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Ruled Surface"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Ruled Surface"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_ruled_surface


def test_ruled_surface_preparation_preserves_ordered_exact_curve_targets() -> None:
    spec = prepare_part_ruled_surface(
        "document-part",
        _ruled_surface_arguments()["definition"],
    )

    assert tuple(curve.object_ref.object_name for curve in spec.curves) == (
        "Curves",
        "Curves",
    )
    assert tuple(curve.subelement for curve in spec.curves) == ("Edge1", "Edge2")


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        ({"curves": [{"object_name": "First"}]}, "exactly two"),
        (
            {
                "curves": [
                    {"object_name": "Curve"},
                    {"object_name": "Curve"},
                ]
            },
            "must be distinct",
        ),
        (
            {
                "curves": [
                    {"object_name": "First", "subelement": "Face1"},
                    {"object_name": "Second"},
                ]
            },
            "exact EdgeN or WireN",
        ),
        (
            {
                "curves": [
                    {"object_name": "First"},
                    {"object_name": "Second"},
                ],
                "orientation": "Forward",
            },
            "exact curves",
        ),
    ),
)
def test_ruled_surface_definition_failures_precede_preflight_and_transaction(
    monkeypatch,
    definition,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _ruled_surface_arguments()
    arguments["definition"] = definition
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_ruled_surface",
        lambda *_args: pytest.fail("preflight started"),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_part_loft_runtime_preflights_before_one_immediate_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(runtime_module, "prepare_part_loft", lambda uid, value: spec)
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_loft",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Part Loft preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_loft",
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

    result = runtime.mutate_part(
        _loft_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part Loft"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Loft"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_loft


def test_part_loft_preparation_preserves_ordered_exact_profiles_and_controls() -> None:
    spec = prepare_part_loft("document-part", _loft_arguments()["definition"])

    assert tuple(profile.object_ref.object_name for profile in spec.profiles) == (
        "Profiles",
        "SecondProfile",
        "Profiles",
    )
    assert tuple(profile.subelement for profile in spec.profiles) == (
        "Wire1",
        None,
        "Face2",
    )
    assert spec.solid is True
    assert spec.ruled is False
    assert spec.closed is False


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        (
            {
                "profiles": [{"object_name": "OnlyOne"}],
                "solid": False,
                "ruled": False,
                "closed": False,
            },
            "requires 2 to 32",
        ),
        (
            {
                "profiles": [
                    {"object_name": "Same", "subelement": "Edge1"},
                    {"object_name": "Same", "subelement": "Edge1"},
                ],
                "solid": False,
                "ruled": False,
                "closed": False,
            },
            "must be distinct",
        ),
        (
            {
                "profiles": [
                    {"object_name": "First", "subelement": "Shell1"},
                    {"object_name": "Second"},
                ],
                "solid": False,
                "ruled": False,
                "closed": False,
            },
            "exact VertexN, EdgeN, WireN, or FaceN",
        ),
        (
            {
                "profiles": [
                    {"object_name": "First"},
                    {"object_name": "Second"},
                ],
                "solid": 1,
                "ruled": False,
                "closed": False,
            },
            "solid must be true or false",
        ),
        (
            {
                "profiles": [
                    {"object_name": "First"},
                    {"object_name": "Second"},
                ],
                "solid": False,
                "ruled": False,
                "closed": False,
                "max_degree": 5,
            },
            "exact controls",
        ),
    ),
)
def test_part_loft_definition_failures_precede_preflight_and_transaction(
    monkeypatch,
    definition,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _loft_arguments()
    arguments["definition"] = definition
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_loft",
        lambda *_args: pytest.fail("preflight started"),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_part_sweep_runtime_preflights_before_one_immediate_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(runtime_module, "prepare_part_sweep", lambda uid, value: spec)
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_sweep",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Part Sweep preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_sweep",
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

    result = runtime.mutate_part(
        _sweep_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part Sweep"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Sweep"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_sweep


def test_part_sweep_preparation_preserves_ordered_exact_inputs_and_controls() -> None:
    spec = prepare_part_sweep("document-part", _sweep_arguments()["definition"])

    assert tuple(profile.object_ref.object_name for profile in spec.profiles) == (
        "Profiles",
        "SecondProfile",
        "Profiles",
    )
    assert tuple(profile.subelement for profile in spec.profiles) == (
        "Wire1",
        None,
        "Face2",
    )
    assert spec.path.object_ref.object_name == "Path"
    assert spec.path.subelements == ("Edge1", "Edge2")
    assert spec.solid is True
    assert spec.frenet is False


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        (
            {
                "profiles": [],
                "path": {"object_name": "Path"},
                "solid": False,
                "frenet": True,
            },
            "requires 1 to 32",
        ),
        (
            {
                "profiles": [
                    {"object_name": "Same", "subelement": "Edge1"},
                    {"object_name": "Same", "subelement": "Edge1"},
                ],
                "path": {"object_name": "Path"},
                "solid": False,
                "frenet": True,
            },
            "must be distinct",
        ),
        (
            {
                "profiles": [{"object_name": "Profile", "subelement": "Shell1"}],
                "path": {"object_name": "Path"},
                "solid": False,
                "frenet": True,
            },
            "exact VertexN, EdgeN, WireN, or FaceN",
        ),
        (
            {
                "profiles": [{"object_name": "Profile"}],
                "path": {"object_name": "Path", "subelements": ["Wire1"]},
                "solid": False,
                "frenet": True,
            },
            "distinct exact EdgeN",
        ),
        (
            {
                "profiles": [{"object_name": "Shared", "subelement": "Edge2"}],
                "path": {"object_name": "Shared", "subelements": ["Edge1", "Edge2"]},
                "solid": False,
                "frenet": True,
            },
            "both a Part Sweep profile and its path",
        ),
        (
            {
                "profiles": [{"object_name": "Profile"}],
                "path": {"object_name": "Path"},
                "solid": 1,
                "frenet": True,
            },
            "solid must be true or false",
        ),
        (
            {
                "profiles": [{"object_name": "Profile"}],
                "path": {"object_name": "Path"},
                "solid": False,
                "frenet": True,
                "transition": "round",
            },
            "exact controls",
        ),
    ),
)
def test_part_sweep_definition_failures_precede_preflight_and_transaction(
    monkeypatch,
    definition,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _sweep_arguments()
    arguments["definition"] = definition
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_sweep",
        lambda *_args: pytest.fail("preflight started"),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_part_cross_sections_runtime_preflights_before_one_immediate_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(
        runtime_module,
        "prepare_part_cross_sections",
        lambda uid, value: spec,
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_cross_sections",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Part Cross Sections preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_cross_sections",
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

    result = runtime.mutate_part(
        _cross_sections_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part Cross Sections"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Cross Sections"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_cross_sections


def test_part_cross_sections_preparation_preserves_sources_and_live_plane_formula() -> None:
    spec = prepare_part_cross_sections(
        "document-part",
        _cross_sections_arguments()["definition"],
    )

    assert tuple(source.object_ref.object_name for source in spec.sources) == (
        "WholeSource",
        "SelectedSource",
    )
    assert tuple(source.subelements for source in spec.sources) == (
        (),
        ("Face1", "Solid2"),
    )
    assert spec.plane == "xz"
    assert spec.distribution == "series"
    assert spec.position == 4.0
    assert spec.count == 3
    assert spec.distance == 2.0
    assert spec.both_sides is True
    assert spec.positions == (2.0, 4.0, 6.0)


def test_part_cross_sections_single_distribution_preserves_one_position() -> None:
    definition = dict(_cross_sections_arguments()["definition"])
    definition["distribution"] = {"kind": "single", "position_mm": -12.5}

    spec = prepare_part_cross_sections("document-part", definition)

    assert spec.distribution == "single"
    assert spec.positions == (-12.5,)
    assert spec.count == 1
    assert spec.distance == 0.0
    assert spec.both_sides is False


@pytest.mark.parametrize(
    ("change", "message"),
    (
        ({"sources": []}, "requires 1 to 32"),
        (
            {
                "sources": [
                    {"object_name": "Same"},
                    {"object_name": "Same", "subelements": ["Face1"]},
                ]
            },
            "must be unique",
        ),
        (
            {
                "sources": [
                    {"object_name": "Source", "subelements": ["Bogus1"]}
                ]
            },
            "distinct exact shape subelements",
        ),
        ({"plane": "custom"}, "must be xy, xz, or yz"),
        (
            {
                "distribution": {
                    "kind": "single",
                    "position_mm": 0.0,
                    "count": 2,
                }
            },
            "fields do not match",
        ),
        (
            {
                "distribution": {
                    "kind": "series",
                    "position_mm": 0.0,
                    "count": 10_001,
                    "distance_mm": 1.0,
                    "both_sides": False,
                }
            },
            "count must be 1 to 10000",
        ),
        (
            {
                "distribution": {
                    "kind": "series",
                    "position_mm": 999_999.0,
                    "count": 3,
                    "distance_mm": 2.0,
                    "both_sides": False,
                }
            },
            "derived positions exceed",
        ),
    ),
)
def test_part_cross_sections_definition_failures_precede_transaction(
    monkeypatch,
    change,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _cross_sections_arguments()
    arguments["definition"] = {**arguments["definition"], **change}
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_cross_sections",
        lambda *_args: pytest.fail("preflight started"),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_part_3d_offset_runtime_preflights_before_one_immediate_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(runtime_module, "prepare_part_offset", lambda uid, value: spec)
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_offset",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Part 3D Offset preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_offset",
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

    result = runtime.mutate_part(
        _offset_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part 3D Offset"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact 3D Offset"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_offset


def test_part_3d_offset_preparation_preserves_every_live_control() -> None:
    spec = prepare_part_offset("document-part", _offset_arguments()["definition"])

    assert spec.source_ref.object_name == "OffsetSource"
    assert spec.value == -2.5
    assert spec.mode == "pipe"
    assert spec.join == "tangent"
    assert spec.intersection is True
    assert spec.self_intersection is False
    assert spec.fill is True


@pytest.mark.parametrize(
    ("change", "message"),
    (
        ({"source": {"object_name": "Source", "subelement": "Face1"}}, "source target"),
        ({"value_mm": True}, "value must be a number"),
        ({"value_mm": 1_000_001.0}, "outside its finite range"),
        ({"mode": "surface"}, "mode must be one of"),
        ({"join": "round"}, "join must be one of"),
        ({"fill": 1}, "fill must be true or false"),
    ),
)
def test_part_3d_offset_definition_failures_precede_transaction(
    monkeypatch,
    change,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _offset_arguments()
    arguments["definition"] = {**arguments["definition"], **change}
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_offset",
        lambda *_args: pytest.fail("preflight started"),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_part_2d_offset_runtime_uses_the_shared_exact_lifecycle(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(
        runtime_module,
        "prepare_part_offset_2d",
        lambda uid, value: spec,
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_offset",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Part 2D Offset preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_offset_2d",
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

    result = runtime.mutate_part(
        _offset_2d_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part 2D Offset"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact 2D Offset"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_offset_2d


def test_part_2d_offset_preparation_preserves_only_live_controls() -> None:
    spec = prepare_part_offset_2d(
        "document-part",
        _offset_2d_arguments()["definition"],
    )

    assert spec.source_ref.object_name == "PlanarSource"
    assert spec.value == 2.5
    assert spec.mode == "pipe"
    assert spec.join == "intersection"
    assert spec.intersection is True
    assert spec.self_intersection is False
    assert spec.fill is False
    assert spec.two_dimensional is True


@pytest.mark.parametrize(
    ("change", "message"),
    (
        ({"mode": "recto_verso"}, "mode must be skin or pipe"),
        ({"self_intersection": False}, "exact controls"),
        ({"intersection": 1}, "intersection must be true or false"),
    ),
)
def test_part_2d_offset_definition_failures_precede_preflight(
    monkeypatch,
    change,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _offset_2d_arguments()
    arguments["definition"] = {**arguments["definition"], **change}
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_offset",
        lambda *_args: pytest.fail("preflight started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_part_projection_runtime_preflights_before_one_immediate_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(
        runtime_module,
        "prepare_part_projection",
        lambda uid, value: spec,
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_projection",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Part projection preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_projection",
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

    result = runtime.mutate_part(
        _projection_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Projection on Surface"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Surface Projection"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_projection


def test_part_projection_preparation_preserves_and_normalizes_every_control() -> None:
    spec = prepare_part_projection(
        "document-part",
        _projection_arguments()["definition"],
    )

    assert spec.target.object_ref.object_name == "Target"
    assert spec.target.subelement == "Face2"
    assert tuple(
        (source.object_ref.object_name, source.subelement)
        for source in spec.sources
    ) == (("Profile", "Face1"), ("Edges", "Edge3"))
    assert spec.mode == "faces"
    assert spec.height == 12.5
    assert spec.offset == -3.0
    assert spec.direction == (0.0, 0.0, -1.0)


@pytest.mark.parametrize(
    ("change", "message"),
    (
        ({"target": {"object_name": "Target", "subelement": "Edge1"}}, "target subelement"),
        ({"sources": []}, "1 to 64"),
        (
            {
                "sources": [
                    {"object_name": "Edges", "subelement": "Edge1"},
                    {"object_name": "Edges", "subelement": "Edge1"},
                ]
            },
            "distinct",
        ),
        ({"mode": "wire"}, "all, faces, or edges"),
        ({"height_mm": -1.0}, "outside its finite range"),
        ({"offset_mm": 1000.0}, "outside its finite range"),
        ({"direction_xyz": [0.0, 0.0, 0.0]}, "non-zero"),
    ),
)
def test_part_projection_definition_failures_precede_preflight(
    monkeypatch,
    change,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _projection_arguments()
    arguments["definition"] = {**arguments["definition"], **change}
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_projection",
        lambda *_args: pytest.fail("preflight started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_part_compound_runtime_preflights_before_one_immediate_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(
        runtime_module,
        "prepare_part_compound",
        lambda uid, value: spec,
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_compound",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Part Compound preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_compound",
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

    result = runtime.mutate_part(
        _compound_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part Compound"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Compound"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_compound


def test_part_compound_preparation_preserves_ordered_whole_sources() -> None:
    spec = prepare_part_compound(
        "document-part",
        _compound_arguments()["definition"],
    )

    assert tuple(ref.object_name for ref in spec.source_refs) == (
        "FirstShape",
        "SecondShape",
    )


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        ({"sources": []}, "1 to 64"),
        (
            {
                "sources": [
                    {"object_name": "Repeated"},
                    {"object_name": "Repeated"},
                ]
            },
            "distinct",
        ),
        (
            {"sources": [{"object_name": "Source", "subelement": "Face1"}]},
            "source target",
        ),
        ({"sources": [{"object_name": "Source"}], "refine": True}, "exact sources"),
    ),
)
def test_part_compound_definition_failures_precede_preflight(
    monkeypatch,
    definition,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _compound_arguments()
    arguments["definition"] = definition
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_compound",
        lambda *_args: pytest.fail("preflight started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_part_compound_filter_runtime_preflights_before_one_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "prepare_part_compound_filter",
        lambda uid, value: spec,
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_compound_filter",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Compound Filter preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_compound_filter",
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

    result = runtime.mutate_part(
        _compound_filter_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part Compound Filter"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Compound Filter"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_compound_filter


def test_part_compound_filter_typed_selectors_canonicalize_to_native_slices() -> None:
    spec = prepare_part_compound_filter(
        "document-part",
        _compound_filter_arguments()["definition"],
    )

    assert spec.mode == "specific_items"
    assert spec.native_mode == "specific items"
    assert spec.native_items == "0;2:8:2;::-1"
    assert spec.stencil_ref is None
    assert spec.window_percent is None
    assert spec.invert is False


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        (
            {
                "source": {"object_name": "CompoundSource"},
                "mode": "specific_items",
                "selectors": [[0, 2, 0]],
                "invert": False,
            },
            "step cannot be zero",
        ),
        (
            {
                "source": {"object_name": "CompoundSource"},
                "mode": "collision",
                "stencil": None,
                "invert": False,
            },
            "requires a stencil",
        ),
        (
            {
                "source": {"object_name": "CompoundSource"},
                "mode": "bypass",
                "invert": False,
            },
            "fields do not match",
        ),
        (
            {
                "source": {"object_name": "CompoundSource"},
                "mode": "volume",
                "stencil": None,
                "window_percent": [80.0, 100.0],
                "maximum": 0.0,
                "invert": False,
            },
            "positive, bounded, or null",
        ),
    ),
)
def test_part_compound_filter_definition_failures_precede_preflight(
    monkeypatch,
    definition,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _compound_filter_arguments()
    arguments["definition"] = definition
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_compound_filter",
        lambda *_args: pytest.fail("preflight started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )


def test_part_defeature_runtime_preflights_before_one_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "prepare_part_defeature",
        lambda uid, value: spec,
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_defeature",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Part Defeaturing preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_defeature",
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

    result = runtime.mutate_part(
        _defeature_arguments(),
        ticket=state.begin_call(document.Uid, "model.part"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part Defeaturing"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Healed Housings"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_defeature


def test_part_defeature_preparation_preserves_grouped_faces_and_order() -> None:
    spec = prepare_part_defeature(
        "document-part",
        _defeature_arguments()["definition"],
    )

    assert tuple(source.object_ref.object_name for source in spec.sources) == (
        "FirstHousing",
        "SecondHousing",
    )
    assert tuple(source.faces for source in spec.sources) == (
        ("Face3", "Face4"),
        ("Face8",),
    )


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        ({"sources": []}, "1 to 32"),
        (
            {
                "sources": [
                    {"object_name": "Repeated", "faces": ["Face1"]},
                    {"object_name": "Repeated", "faces": ["Face2"]},
                ]
            },
            "appear once",
        ),
        (
            {"sources": [{"object_name": "Source", "faces": []}]},
            "1 to 64",
        ),
        (
            {
                "sources": [
                    {
                        "object_name": "Source",
                        "faces": ["Face1", "Face1"],
                    }
                ]
            },
            "distinct exact FaceN",
        ),
        (
            {"sources": [{"object_name": "Source", "faces": ["Edge1"]}]},
            "distinct exact FaceN",
        ),
    ),
)
def test_part_defeature_definition_failures_precede_preflight(
    monkeypatch,
    definition,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _defeature_arguments()
    arguments["definition"] = definition
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_defeature",
        lambda *_args: pytest.fail("preflight started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_part(
            arguments,
            ticket=state.begin_call(document.Uid, "model.part"),
        )
