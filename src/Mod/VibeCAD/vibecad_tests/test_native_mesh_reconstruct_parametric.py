# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for human-authorized parametric reconstruction input and output."""

from __future__ import annotations

import json

import pytest

from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshReconstructParametricSchema import (
    mesh_reconstruct_parametric_capability_definition,
    register_mesh_reconstruct_parametric_capability_definition,
)
from VibeCADNativeReconstructParametricRuntime import (
    prepare_printables_reconstruction,
    printables_ir_plan,
    reconstruction_output_requests,
)


def _ir() -> dict:
    return {
        "schema_version": 1,
        "units": "mm",
        "body": "Bracket",
        "class": "parametric",
        "expected_shells": 1,
        "forbidden": {"triangle_wrapped_step": True},
        "sketches": [
            {
                "id": "base",
                "origin_mm": [0.0, 0.0, 0.0],
                "normal": [0.0, 0.0, 1.0],
                "x_axis": [1.0, 0.0, 0.0],
                "y_axis": [0.0, 1.0, 0.0],
                "profiles": [
                    {
                        "id": "outer",
                        "role": "outer",
                        "entities": [
                            {
                                "type": "polyline",
                                "points_mm": [
                                    [0, 0],
                                    [20, 0],
                                    [20, 10],
                                    [0, 10],
                                    [0, 0],
                                ],
                            },
                        ],
                    }
                ],
            }
        ],
        "features": [
            {
                "id": "pad",
                "type": "extrude",
                "op": "add",
                "sketch": "base",
                "depth_mm": 5.0,
                "direction": [0.0, 0.0, 1.0],
            },
            {
                "id": "hole",
                "type": "hole",
                "diameter_mm": 3.0,
                "uv_mm": [5.0, 5.0],
            },
        ],
    }


def test_capability_is_a_background_mesh_surface_tool() -> None:
    definition = mesh_reconstruct_parametric_capability_definition()
    variant = definition.variants[0]

    assert definition.name == "mesh.reconstruct_parametric"
    assert variant.action_ids == frozenset({"Reen_PoissonReconstruction"})
    assert variant.surface_ids == frozenset({"mesh"})
    assert variant.transaction_behavior == "background"
    assert variant.background_required is True

    registry = NativeCapabilityRegistry()
    register_mesh_reconstruct_parametric_capability_definition(registry)
    assert registry.shared_definition_names == ("mesh.reconstruct_parametric",)


def test_preparation_claims_bounded_human_authorized_json(monkeypatch) -> None:
    import VibeCADNativeReconstructParametricRuntime as runtime

    payload = json.dumps(_ir()).encode("utf-8")
    calls: list[object] = []
    shape = object()
    monkeypatch.setattr(runtime, "_build_solid", lambda _plan: shape)

    class _Artifact:
        file_name = "bracket.ir.json"

        def read_bytes(self, *, maximum_bytes: int) -> bytes:
            calls.append(maximum_bytes)
            return payload

        def summary(self) -> dict:
            return {
                "file_name": self.file_name,
                "size_bytes": len(payload),
                "sha256": "0" * 64,
            }

    class _Authorization:
        def claim(self, request):
            calls.append(request)
            return _Artifact()

    progress: list[tuple[int, str]] = []
    prepared = prepare_printables_reconstruction(
        _Authorization(),
        cancelled=lambda: False,
        progress=lambda percent, message: progress.append((percent, message)),
    )

    assert prepared.plan["body"] == "Bracket"
    assert prepared.shape is shape
    assert prepared.plan["outer_uv"] == [
        (0.0, 0.0),
        (20.0, 0.0),
        (20.0, 10.0),
        (0.0, 10.0),
    ]
    assert prepared.input_summary["file_name"] == "bracket.ir.json"
    assert progress[0][0] < progress[-1][0]
    assert len(calls) == 2


def test_plan_rejects_unsupported_or_unbounded_reconstruction() -> None:
    unsupported = _ir()
    unsupported["features"].append({"id": "loft", "type": "loft"})
    with pytest.raises(NativeMeshError, match="supported extrude and hole"):
        printables_ir_plan(unsupported)

    multiple_shells = _ir()
    multiple_shells["expected_shells"] = 2
    with pytest.raises(NativeMeshError, match="exactly one solid"):
        printables_ir_plan(multiple_shells)

    open_profile = _ir()
    open_profile["sketches"][0]["profiles"][0]["entities"][0]["points_mm"].pop()
    with pytest.raises(NativeMeshError, match="closed outer profile"):
        printables_ir_plan(open_profile)


def test_output_paths_are_only_human_dialog_filename_suggestions() -> None:
    requests = reconstruction_output_requests(
        {
            "step_path": r"C:\untrusted\Bracket.step",
            "stl_path": "preview.stl",
        },
        label="Bracket",
    )

    assert [(kind, request.suggested_file_name) for kind, request in requests] == [
        ("step", "Bracket.step"),
        ("stl", "preview.stl"),
    ]
    assert [request.allowed_suffixes for _kind, request in requests] == [
        (".step", ".stp"),
        (".stl",),
    ]


def test_runtime_never_opens_the_provider_path_directly() -> None:
    import inspect
    import VibeCADNativeReconstructParametricRuntime as runtime

    source = inspect.getsource(runtime.NativeReconstructParametricRuntime.execute)
    assert "load_printables_ir(values" not in source
    assert "authorize_input" in source
    assert "background_manager" in source
    assert "isValid" not in inspect.getsource(runtime._valid_reconstruction_object)
