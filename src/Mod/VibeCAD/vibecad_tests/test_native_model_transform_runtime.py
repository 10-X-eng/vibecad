# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import VibeCADNativeModelTransformRuntime as runtime_module
import VibeCADNativeDesignScale as scale_module
from VibeCADNativeDesignCircularPattern import (
    preflight_design_circular_pattern,
    prepare_design_circular_pattern,
)
from VibeCADNativeDesignLinearPattern import (
    preflight_design_linear_pattern,
    prepare_design_linear_pattern,
)
from VibeCADNativeDesignMirror import preflight_design_mirror, prepare_design_mirror
from VibeCADNativeDesignScale import (
    create_design_scale,
    preflight_design_scale,
    prepare_design_scale,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelTransformRuntime import NativeModelTransformRuntime
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Shape:
    Solids = (object(),)
    Placement = object()
    Orientation = "Forward"

    def isNull(self) -> bool:
        return False

    def isValid(self) -> bool:
        return True

    def copy(self):
        return self

    def isPartner(self, other) -> bool:
        return other is self

    @staticmethod
    def getElement(name: str):
        if name == "Face1":
            return SimpleNamespace(
                ShapeType="Face",
                Surface=SimpleNamespace(TypeId="Part::GeomPlane"),
            )
        if name == "Edge1":
            return SimpleNamespace(
                ShapeType="Edge",
                Curve=SimpleNamespace(TypeId="Part::GeomLine"),
            )
        if name == "Edge2":
            return SimpleNamespace(
                ShapeType="Edge",
                Curve=SimpleNamespace(TypeId="Part::GeomCircle"),
            )
        raise RuntimeError(name)


class _Object:
    def __init__(self, document, name: str, type_id: str, derived=()):
        self.Document = document
        self.Name = name
        self.TypeId = type_id
        self.Shape = _Shape()
        self.ResultOperation = "New Body"
        self.AxisCount = 3
        self._derived = frozenset((type_id, *derived))
        self._global_placement = object()
        if type_id == "PartDesign::Body":
            self.VibeCADBodyId = f"body-{name}"
            self.Tip = SimpleNamespace(
                CurrentState=SimpleNamespace(Document=document, Name=f"{name}State")
            )

    def isDerivedFrom(self, expected: str) -> bool:
        return expected in self._derived

    def isValid(self) -> bool:
        return True

    def getGlobalPlacement(self):
        return self._global_placement


class _Document:
    Uid = "document-transform"
    Name = "DocumentTransform"

    def __init__(self):
        self.objects = {
            "SourceBody": _Object(self, "SourceBody", "PartDesign::Body"),
            "TargetBody": _Object(self, "TargetBody", "PartDesign::Body"),
            "AdditiveSource": _Object(
                self,
                "AdditiveSource",
                "PartDesign::DesignBox",
                ("PartDesign::FeatureAddSub", "Part::Feature"),
            ),
            "PlaneSketch": _Object(
                self,
                "PlaneSketch",
                "Sketcher::SketchObject",
                ("Part::Part2DObject",),
            ),
            "ReferenceState": _Object(
                self,
                "ReferenceState",
                "PartDesign::DesignBodyState",
                ("Part::Feature",),
            ),
        }

    def getObject(self, name: str):
        return self.objects.get(name)


def _runtime():
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("model-transform-unit")
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
    return NativeModelTransformRuntime(context), state, document


def _arguments() -> dict[str, object]:
    return {
        "operation": "pattern",
        "label": "Mirrored Body",
        "source": {
            "kind": "body",
            "body": {"object_name": "SourceBody"},
        },
        "definition": {
            "kind": "mirror",
            "plane": {
                "kind": "explicit",
                "origin_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
                "normal": {"x": 1.0, "y": 0.0, "z": 0.0},
            },
        },
    }


def _scale_arguments(*, non_uniform: bool = False) -> dict[str, object]:
    definition = {
        "kind": "non_uniform",
        "x_factor": 2.0,
        "y_factor": 3.0,
        "z_factor": 4.0,
        "center_mm": {"x": 1.0, "y": 2.0, "z": 3.0},
    }
    if not non_uniform:
        definition = {
            "kind": "uniform",
            "factor": 2.0,
            "center_mm": {"x": 1.0, "y": 2.0, "z": 3.0},
        }
    return {
        "operation": "scale",
        "label": "Scaled Bodies",
        "targets": [
            {"object_name": "SourceBody"},
            {"object_name": "TargetBody"},
        ],
        "definition": definition,
    }


def test_transform_runtime_routes_scale_with_exact_preflight(monkeypatch) -> None:
    runtime, state, document = _runtime()
    observed = {}
    prepared = SimpleNamespace(spec=SimpleNamespace(uniform=False))
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "preflight_design_scale",
        lambda target_document, spec: observed.update(
            document=target_document,
            spec=spec,
        )
        or prepared,
    )
    monkeypatch.setattr(
        runtime_module,
        "create_design_scale",
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

    result = runtime.mutate_transform(
        _scale_arguments(non_uniform=True),
        ticket=state.begin_call(document.Uid, "model.transform"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Design Scale"
    assert observed["document"] is document
    assert observed["spec"].axis_factors == (2.0, 3.0, 4.0)
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Scaled Bodies"
    assert draft.prepared is prepared


def test_scale_parser_preserves_uniform_and_non_uniform_controls() -> None:
    uniform_arguments = _scale_arguments()
    uniform_arguments.pop("operation")
    non_uniform_arguments = _scale_arguments(non_uniform=True)
    non_uniform_arguments.pop("operation")
    uniform = prepare_design_scale("document-transform", uniform_arguments)
    non_uniform = prepare_design_scale(
        "document-transform",
        non_uniform_arguments,
    )

    assert [item.object_name for item in uniform.target_refs] == [
        "SourceBody",
        "TargetBody",
    ]
    assert uniform.uniform is True
    assert uniform.uniform_factor == 2.0
    assert uniform.axis_factors == (1.0, 1.0, 1.0)
    assert uniform.center == (1.0, 2.0, 3.0)
    assert non_uniform.uniform is False
    assert non_uniform.uniform_factor == 1.0
    assert non_uniform.axis_factors == (2.0, 3.0, 4.0)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda values: values["targets"].append({"object_name": "SourceBody"}),
            "repeat",
        ),
        (
            lambda values: values["definition"].update(factor=True),
            "must be a number",
        ),
        (
            lambda values: values["definition"].update(factor=0.0),
            "must be from",
        ),
        (
            lambda values: values["definition"].update(x_factor=2.0),
            "do not match",
        ),
    ),
)
def test_scale_parser_rejects_ambiguous_or_invalid_controls(mutate, message) -> None:
    arguments = _scale_arguments()
    arguments.pop("operation")
    mutate(arguments)

    with pytest.raises(NativeModelError, match=message):
        prepare_design_scale("document-transform", arguments)


def test_scale_preflight_requires_active_exact_solid_bodies(monkeypatch) -> None:
    _runtime_value, _state, document = _runtime()
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(
            isModelingObjectActive=lambda _body: True,
            resolveModelingObject=lambda body: body.Tip.CurrentState,
        ),
    )
    arguments = _scale_arguments()
    arguments.pop("operation")
    spec = prepare_design_scale(document.Uid, arguments)
    prepared = preflight_design_scale(document, spec)

    assert tuple(target.body for target in prepared.targets) == (
        document.objects["SourceBody"],
        document.objects["TargetBody"],
    )
    assert tuple(target.body_id for target in prepared.targets) == (
        "body-SourceBody",
        "body-TargetBody",
    )
    assert tuple(target.frame for target in prepared.targets) == tuple(
        target.body.getGlobalPlacement() for target in prepared.targets
    )

    document.objects["TargetBody"].Shape.Solids = ()
    with pytest.raises(NativeModelError, match="one exact current solid"):
        preflight_design_scale(document, spec)

    document.objects["TargetBody"].Shape.Solids = (object(), object())
    with pytest.raises(NativeModelError, match="one exact current solid"):
        preflight_design_scale(document, spec)


def test_scale_preflight_respects_each_body_allow_compound_setting(monkeypatch) -> None:
    _runtime_value, _state, document = _runtime()
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(
            isModelingObjectActive=lambda _body: True,
            resolveModelingObject=lambda body: body.Tip.CurrentState,
        ),
    )
    arguments = _scale_arguments()
    arguments.pop("operation")
    spec = prepare_design_scale(document.Uid, arguments)
    target = document.objects["TargetBody"]
    target.Shape.Solids = (object(), object())
    target.AllowCompound = True

    prepared = preflight_design_scale(document, spec)
    assert tuple(item.body for item in prepared.targets)[1] is target

    target.AllowCompound = False
    with pytest.raises(NativeModelError, match="solid-bearing Body state"):
        preflight_design_scale(document, spec)


def test_scale_creation_rechecks_the_exact_body_state_shape_and_frame(monkeypatch) -> None:
    _runtime_value, _state, document = _runtime()
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(
            isModelingObjectActive=lambda _body: True,
            resolveModelingObject=lambda body: body.Tip.CurrentState,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "FreeCAD",
        SimpleNamespace(Vector=lambda *values: values),
    )
    arguments = _scale_arguments()
    arguments.pop("operation")
    prepared = preflight_design_scale(
        document,
        prepare_design_scale(document.Uid, arguments),
    )
    captured = {}
    monkeypatch.setattr(
        scale_module,
        "create_design_operation",
        lambda target_document, **kwargs: captured.update(
            document=target_document,
            kwargs=kwargs,
        )
        or {"created": True},
    )

    assert create_design_scale(
        document,
        label="Exact Scale",
        prepared=prepared,
    ) == {"created": True}
    assert captured["document"] is document

    document.objects["TargetBody"]._global_placement = object()
    with pytest.raises(NativeModelError, match="changed after preflight"):
        create_design_scale(
            document,
            label="Stale Scale",
            prepared=prepared,
        )

def test_transform_runtime_preflights_then_routes_one_immediate_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    observed = []
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "preflight_design_mirror",
        lambda target_document, spec: observed.append((target_document, spec)),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_design_mirror",
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

    preview = runtime.mutate_transform(
        _arguments(),
        ticket=state.begin_call(document.Uid, "model.transform"),
    )
    assert preview["applied"] is False
    apply_arguments = dict(_arguments())
    apply_arguments["stage"] = "apply"
    apply_arguments["preview_id"] = preview["preview_id"]
    result = runtime.mutate_transform(
        apply_arguments,
        ticket=state.begin_call(document.Uid, "model.transform"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Design Mirror"
    assert observed[0][0] is document
    assert observed[0][1].source.source_ref.object_name == "SourceBody"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Mirrored Body"
    assert draft.spec.plane.normal == (1.0, 0.0, 0.0)


def test_mirror_preflight_accepts_exact_feature_targets_and_plane_forms() -> None:
    _runtime_value, _state, document = _runtime()
    arguments = _arguments()
    arguments["source"] = {
        "kind": "feature",
        "operation": {"object_name": "AdditiveSource"},
        "targets": [{"object_name": "TargetBody"}],
    }
    arguments["definition"] = {
        "kind": "mirror",
        "plane": {"kind": "object", "object_name": "PlaneSketch"},
    }
    prepared = prepare_design_mirror(document.Uid, arguments)
    preflight_design_mirror(document, prepared)

    arguments["definition"] = {
        "kind": "mirror",
        "plane": {
            "kind": "subelement",
            "object_name": "ReferenceState",
            "subelement": "Face1",
        },
    }
    preflight_design_mirror(
        document,
        prepare_design_mirror(document.Uid, arguments),
    )


def test_transform_preflight_rejects_stale_source_before_transaction(monkeypatch) -> None:
    runtime, state, document = _runtime()
    document.objects.pop("SourceBody")
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    preview = runtime.mutate_transform(
        _arguments(),
        ticket=state.begin_call(document.Uid, "model.transform"),
    )
    assert preview["applied"] is False
    apply_arguments = dict(_arguments())
    apply_arguments["stage"] = "apply"
    apply_arguments["preview_id"] = preview["preview_id"]
    with pytest.raises(RuntimeError, match="no longer exists"):
        runtime.mutate_transform(
            apply_arguments,
            ticket=state.begin_call(document.Uid, "model.transform"),
        )


def _linear_arguments() -> dict[str, object]:
    arguments = _arguments()
    arguments["label"] = "Linear Body Copies"
    arguments["definition"] = {
        "kind": "linear",
        "direction": {
            "kind": "explicit",
            "vector": {"x": 1.0, "y": 0.0, "z": 0.0},
        },
        "spacing_mm": 12.0,
        "occurrences": 4,
        "centered": False,
    }
    return arguments


def test_transform_runtime_routes_linear_pattern_with_exact_preflight(monkeypatch) -> None:
    runtime, state, document = _runtime()
    observed = []
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "preflight_design_linear_pattern",
        lambda target_document, spec: observed.append((target_document, spec)),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_design_linear_pattern",
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

    preview = runtime.mutate_transform(
        _linear_arguments(),
        ticket=state.begin_call(document.Uid, "model.transform"),
    )
    assert preview["applied"] is False
    apply_arguments = dict(_linear_arguments())
    apply_arguments["stage"] = "apply"
    apply_arguments["preview_id"] = preview["preview_id"]
    result = runtime.mutate_transform(
        apply_arguments,
        ticket=state.begin_call(document.Uid, "model.transform"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Design Linear Pattern"
    assert observed[0][0] is document
    assert observed[0][1].occurrences == 4
    draft = captured["mutate"](document)
    assert draft.spec.spacing_mm == 12.0


def test_linear_preflight_accepts_sketch_axes_and_straight_edges() -> None:
    _runtime_value, _state, document = _runtime()
    for direction in (
        {"kind": "object", "object_name": "PlaneSketch"},
        {
            "kind": "subelement",
            "object_name": "PlaneSketch",
            "subelement": "Axis2",
        },
        {
            "kind": "subelement",
            "object_name": "ReferenceState",
            "subelement": "Edge1",
        },
    ):
        arguments = _linear_arguments()
        arguments["definition"]["direction"] = direction
        preflight_design_linear_pattern(
            document,
            prepare_design_linear_pattern(document.Uid, arguments),
        )


def _circular_arguments() -> dict[str, object]:
    arguments = _arguments()
    arguments["label"] = "Circular Body Copies"
    arguments["definition"] = {
        "kind": "circular",
        "axis": {
            "kind": "explicit",
            "origin_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
            "direction": {"x": 0.0, "y": 0.0, "z": 1.0},
        },
        "angle_degrees": 180.0,
        "occurrences": 3,
        "reversed": False,
    }
    return arguments


def test_transform_runtime_routes_circular_pattern_with_exact_preflight(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    observed = []
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "preflight_design_circular_pattern",
        lambda target_document, spec: observed.append((target_document, spec)),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_design_circular_pattern",
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

    preview = runtime.mutate_transform(
        _circular_arguments(),
        ticket=state.begin_call(document.Uid, "model.transform"),
    )
    assert preview["applied"] is False
    apply_arguments = dict(_circular_arguments())
    apply_arguments["stage"] = "apply"
    apply_arguments["preview_id"] = preview["preview_id"]
    result = runtime.mutate_transform(
        apply_arguments,
        ticket=state.begin_call(document.Uid, "model.transform"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Design Circular Pattern"
    assert observed[0][0] is document
    assert observed[0][1].angle_degrees == 180.0
    draft = captured["mutate"](document)
    assert draft.spec.occurrences == 3


def test_circular_preflight_accepts_sketch_straight_and_circular_axes() -> None:
    _runtime_value, _state, document = _runtime()
    for axis in (
        {"kind": "object", "object_name": "PlaneSketch"},
        {
            "kind": "subelement",
            "object_name": "PlaneSketch",
            "subelement": "Axis2",
        },
        {
            "kind": "subelement",
            "object_name": "ReferenceState",
            "subelement": "Edge1",
        },
        {
            "kind": "subelement",
            "object_name": "ReferenceState",
            "subelement": "Edge2",
        },
    ):
        arguments = _circular_arguments()
        arguments["definition"]["axis"] = axis
        preflight_design_circular_pattern(
            document,
            prepare_design_circular_pattern(document.Uid, arguments),
        )


def test_mirror_preflight_rejects_nonplanar_face() -> None:
    _runtime_value, _state, document = _runtime()
    arguments = _arguments()
    arguments["definition"] = {
        "kind": "mirror",
        "plane": {
            "kind": "subelement",
            "object_name": "ReferenceState",
            "subelement": "Face1",
        },
    }
    document.objects["ReferenceState"].Shape.getElement = lambda _name: SimpleNamespace(
        ShapeType="Face",
        Surface=SimpleNamespace(TypeId="Part::GeomCylinder"),
    )

    with pytest.raises(NativeModelError, match="must be planar"):
        preflight_design_mirror(
            document,
            prepare_design_mirror(document.Uid, arguments),
        )
