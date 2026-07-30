# SPDX-License-Identifier: LGPL-2.1-or-later

"""Dispatch contracts for the canonical consolidated modeling tools."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from tool_impl.service import (
    domain_runtime,
    model_boolean,
    model_chamfer,
    model_extrude,
    model_fillet,
    model_find_subelements,
    model_helix,
    model_loft,
    model_measure,
    model_mirror,
    model_revolve,
    model_sweep,
    model_thickness,
)


def test_part_result_adoption_keeps_old_callers_and_tracks_exact_replacements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adopted: list[Any] = []
    replacements: list[tuple[Any, list[Any]]] = []
    result = object()
    visible_input = object()

    monkeypatch.setitem(
        sys.modules,
        "PartDesignGui",
        SimpleNamespace(
            adoptPartResult=lambda obj: adopted.append(obj) or "Body",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(
            setModelingReplacedInputs=lambda obj, inputs: (
                replacements.append((obj, list(inputs))) or True
            ),
        ),
    )

    assert domain_runtime.adopt_part_result(result) == "Body"
    assert adopted == [result]
    assert replacements == []

    assert (
        domain_runtime.adopt_part_result(
            result,
            replaced_inputs=[visible_input],
        )
        == "Body"
    )
    assert adopted == [result, result]
    assert replacements == [(result, [visible_input])]


def test_empty_replacement_set_does_not_create_timeline_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(
            setModelingReplacedInputs=lambda *_args: calls.append(_args),
        ),
    )

    assert domain_runtime.mark_modeling_replaced_inputs(object(), []) is False
    assert calls == []


def _recorder(monkeypatch: pytest.MonkeyPatch, module: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def run(_service: Any, **arguments: Any) -> dict[str, Any]:
        calls.append(arguments)
        return {"ok": True, "native_operation": arguments.get("operation", "")}

    monkeypatch.setattr(module, "run", run)
    return calls


def _extrude_arguments(**overrides: Any) -> dict[str, Any]:
    arguments = {
        "profile_name": "Profile",
        "operation": "add_material",
        "extent": {"type": "distance", "distance_mm": 10.0},
        "side": "one_side",
        "direction": None,
        "reversed": False,
        "taper_angle_degrees": 0.0,
        "second_taper_angle_degrees": 0.0,
        "refine": True,
        "label": "Extrusion",
    }
    arguments.update(overrides)
    return arguments


@pytest.mark.parametrize(
    ("intent", "native_operation", "type_id"),
    (
        ("add_material", "pad", "PartDesign::Pad"),
        ("remove_material", "pocket", "PartDesign::Pocket"),
    ),
)
def test_extrude_dispatches_body_material_intent(
    monkeypatch: pytest.MonkeyPatch,
    intent: str,
    native_operation: str,
    type_id: str,
) -> None:
    calls = _recorder(monkeypatch, model_extrude.partdesign_linear_feature)

    result = model_extrude.run(object(), **_extrude_arguments(operation=intent))

    assert calls[0]["operation"] == native_operation
    assert calls[0]["type_id"] == type_id
    assert calls[0]["extent"] == {"type": "length", "length": 10.0}
    assert result["operation"] == "extrude"
    assert result["material_operation"] == intent


@pytest.mark.parametrize(
    ("intent", "solid"),
    (("new_solid", True), ("new_surface", False)),
)
def test_extrude_dispatches_standalone_geometry(
    monkeypatch: pytest.MonkeyPatch,
    intent: str,
    solid: bool,
) -> None:
    calls = _recorder(monkeypatch, model_extrude.part_extrude)

    result = model_extrude.run(
        object(),
        **_extrude_arguments(
            operation=intent,
            side="symmetric",
            direction={"x": 0.0, "y": 0.0, "z": 1.0},
            reversed=True,
            refine=False,
        ),
    )

    assert calls[0]["profile_object_name"] == "Profile"
    assert calls[0]["extent"] == {"type": "symmetric", "total_length_mm": 10.0}
    assert calls[0]["direction"] == {"x": -0.0, "y": -0.0, "z": -1.0}
    assert calls[0]["solid"] is solid
    assert result["material_operation"] == intent


@pytest.mark.parametrize(
    ("intent", "native_operation", "type_id"),
    (
        ("add_material", "revolution", "PartDesign::Revolution"),
        ("remove_material", "groove", "PartDesign::Groove"),
    ),
)
def test_revolve_dispatches_body_material_intent(
    monkeypatch: pytest.MonkeyPatch,
    intent: str,
    native_operation: str,
    type_id: str,
) -> None:
    calls = _recorder(monkeypatch, model_revolve.partdesign_rotational_feature)

    result = model_revolve.run(
        object(),
        profile_name="Profile",
        operation=intent,
        axis={"source": "profile", "axis": "V"},
        extent={"type": "angle", "angle_degrees": 180.0},
        midplane=False,
        reversed=False,
        label="Revolve",
    )

    assert calls[0]["operation"] == native_operation
    assert calls[0]["type_id"] == type_id
    assert result["operation"] == "revolve"
    assert result["material_operation"] == intent


def test_revolve_dispatches_standalone_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _recorder(monkeypatch, model_revolve.part_revolve)
    result = model_revolve.run(
        object(),
        profile_name="Profile",
        operation="new_surface",
        axis={
            "source": "global",
            "point": {"x": 0.0, "y": 0.0, "z": 0.0},
            "direction": {"x": 0.0, "y": 1.0, "z": 0.0},
        },
        extent={"type": "angle", "angle_degrees": 90.0},
        midplane=True,
        reversed=True,
        label="Surface",
    )

    assert calls[0]["axis_direction"] == {"x": -0.0, "y": -1.0, "z": -0.0}
    assert calls[0]["solid"] is False
    assert calls[0]["symmetric"] is True
    assert result["material_operation"] == "new_surface"


def test_standalone_tools_reject_body_only_refine_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extrude_calls = _recorder(monkeypatch, model_extrude.part_extrude)
    mirror_calls = _recorder(monkeypatch, model_mirror.part_mirror)

    extrusion = model_extrude.run(
        object(),
        **_extrude_arguments(
            operation="new_solid",
            direction={"x": 0.0, "y": 0.0, "z": 1.0},
            refine=True,
        ),
    )
    mirror = model_mirror.run(
        object(),
        result_mode="standalone_shape",
        source_object_name="Source",
        plane_point={"x": 0.0, "y": 0.0, "z": 0.0},
        plane_normal={"x": 1.0, "y": 0.0, "z": 0.0},
        refine=True,
        label="Mirror",
    )

    assert extrusion["ok"] is False
    assert "refine" in extrusion["error"]
    assert mirror["ok"] is False
    assert "refine" in mirror["error"]
    assert extrude_calls == []
    assert mirror_calls == []


@pytest.mark.parametrize(
    ("tool", "native_module", "intent", "native_operation", "type_id"),
    (
        (
            model_loft,
            model_loft.partdesign_loft_feature,
            "remove_material",
            "subtractive_loft",
            "PartDesign::SubtractiveLoft",
        ),
        (
            model_sweep,
            model_sweep.partdesign_pipe_feature,
            "add_material",
            "additive_pipe",
            "PartDesign::AdditivePipe",
        ),
        (
            model_helix,
            model_helix.partdesign_helix_feature,
            "remove_material",
            "subtractive_helix",
            "PartDesign::SubtractiveHelix",
        ),
    ),
)
def test_loft_sweep_and_helix_choose_one_native_material_variant(
    monkeypatch: pytest.MonkeyPatch,
    tool: Any,
    native_module: Any,
    intent: str,
    native_operation: str,
    type_id: str,
) -> None:
    calls = _recorder(monkeypatch, native_module)

    result = tool.run(object(), operation=intent, label="Feature")

    assert calls[0]["operation"] == native_operation
    assert calls[0]["type_id"] == type_id
    assert result["material_operation"] == intent


@pytest.mark.parametrize(
    ("tool", "intent", "solid"),
    (
        (model_loft, "new_solid", True),
        (model_loft, "new_surface", False),
        (model_sweep, "new_solid", True),
        (model_sweep, "new_surface", False),
    ),
)
def test_loft_and_sweep_dispatch_standalone_geometry(
    monkeypatch: pytest.MonkeyPatch,
    tool: Any,
    intent: str,
    solid: bool,
) -> None:
    calls: list[dict[str, Any]] = []

    def run_standalone(_service: Any, **arguments: Any) -> dict[str, Any]:
        calls.append(arguments)
        return {"ok": True}

    monkeypatch.setattr(tool, "_run_standalone", run_standalone)

    result = tool.run(object(), operation=intent, label="Standalone")

    assert calls == [{"solid": solid, "label": "Standalone"}]
    assert result["material_operation"] == intent
    assert set(tool.TOOL_SPEC["parameters"]["properties"]["operation"]["enum"]) == {
        "new_solid",
        "new_surface",
        "add_material",
        "remove_material",
    }


def test_mirror_dispatches_body_and_standalone_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body_calls = _recorder(monkeypatch, model_mirror.partdesign_mirror)
    part_calls = _recorder(monkeypatch, model_mirror.part_mirror)

    body_result = model_mirror.run(
        object(),
        result_mode="body_features",
        feature_names=["Pad"],
        body_plane={"source": "origin", "plane": "XZ"},
        transform_mode="transform_body",
        refine=True,
        label="Body Mirror",
    )
    part_result = model_mirror.run(
        object(),
        result_mode="standalone_shape",
        source_object_name="Source",
        plane_point={"x": 0.0, "y": 0.0, "z": 0.0},
        plane_normal={"x": 1.0, "y": 0.0, "z": 0.0},
        label="Shape Mirror",
    )

    assert body_calls[0]["feature_names"] == ["Pad"]
    assert part_calls[0]["source_object_name"] == "Source"
    assert body_result["result_mode"] == "body_features"
    assert part_result["result_mode"] == "standalone_shape"


@pytest.mark.parametrize(
    ("wrapper", "winner"),
    (
        (model_boolean, model_boolean.part_boolean),
        (model_fillet, model_fillet.partdesign_fillet),
        (model_chamfer, model_chamfer.partdesign_chamfer),
        (model_thickness, model_thickness.partdesign_thickness),
        (model_find_subelements, model_find_subelements.partdesign_find_subelements),
        (model_measure, model_measure.partdesign_measure),
    ),
)
def test_canonical_wrappers_dispatch_to_the_selected_superior_implementation(
    monkeypatch: pytest.MonkeyPatch,
    wrapper: Any,
    winner: Any,
) -> None:
    calls = _recorder(monkeypatch, winner)

    result = wrapper.run(object(), exact_name="Feature")

    assert calls == [{"exact_name": "Feature"}]
    assert result["ok"] is True
