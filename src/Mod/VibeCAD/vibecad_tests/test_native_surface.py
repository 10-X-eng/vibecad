# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSurface import (
    NativeSurfaceChanged,
    SURFACE_CHANGED,
    freeze_native_surface,
    require_frozen_native_surface,
)

from vibecad_tests.test_ribbon_surface import _Controller, _manifest


def test_snapshot_uses_exact_live_manifest_identity() -> None:
    controller = _Controller(_manifest(), revision=11)

    snapshot = freeze_native_surface(controller)

    assert snapshot.surface_id == "model"
    assert snapshot.revision == 11
    assert snapshot.command_ids == (
        "PartDesign_DesignPrimitive",
        "PartDesign::DesignBox",
        "PartDesign::DesignCylinder",
        "PartDesign::DesignSphere",
        "PartDesign::DesignCone",
        "PartDesign::DesignEllipsoid",
        "PartDesign::DesignTorus",
        "PartDesign::DesignPrism",
        "PartDesign::DesignWedge",
        "PartDesign::DesignTube",
        "Part_CheckGeometry",
    )
    assert snapshot.available_command_ids == snapshot.command_ids
    assert snapshot.unavailable_command_ids == ()
    assert len(snapshot.environment_sha256) == 64
    assert snapshot.modeling_surface_id.endswith(
        f"/{snapshot.manifest_sha256[:12]}/{snapshot.environment_sha256[:12]}"
    )
    assert snapshot.summary() == {
        "mode": "native",
        "surface_id": "model",
        "surface_revision": 11,
        "manifest_sha256": snapshot.manifest_sha256,
        "available": True,
        "action_count": 11,
        "available_action_count": 11,
    }


def test_exact_snapshot_remains_authorized() -> None:
    controller = _Controller(_manifest(), revision=4)
    expected = freeze_native_surface(controller)

    assert require_frozen_native_surface(expected, controller) == expected


@pytest.mark.parametrize(
    "change",
    ("surface", "revision", "manifest", "build_feature", "preference"),
)
def test_any_surface_identity_change_invalidates_without_fallback(change: str) -> None:
    controller = _Controller(_manifest(), revision=4)
    expected = freeze_native_surface(controller)
    if change == "surface":
        controller.values["VibeCADActiveSurfaceManifest"] = _manifest(
            surface_id="mesh"
        )
        controller.values["VibeCADActiveSurfaceId"] = "mesh"
    elif change == "revision":
        controller.values["VibeCADActiveSurfaceRevision"] = 5
    else:
        if change == "manifest":
            changed = _manifest()
            changed["groups"][1]["actions"][0]["label"] = "Geometry check"
            controller.values["VibeCADActiveSurfaceManifest"] = changed
        elif change == "build_feature":
            controller.values["VibeCADActiveSurfaceEnvironment"]["build_features"][
                "fem_vtk"
            ] = False
        else:
            controller.values["VibeCADActiveSurfaceManifest"] = _manifest(
                surface_id="drawing"
            )
            controller.values["VibeCADActiveSurfaceId"] = "drawing"
            controller.values["VibeCADActiveSurfaceEnvironment"]["preferences"] = {
                "techdraw.separated_dimensioning_tools": True,
                "techdraw.single_dimensioning_tool": True,
            }
            expected = freeze_native_surface(controller)
            controller.values["VibeCADActiveSurfaceEnvironment"]["preferences"][
                "techdraw.separated_dimensioning_tools"
            ] = False

    with pytest.raises(NativeSurfaceChanged) as caught:
        require_frozen_native_surface(expected, controller)

    assert caught.value.failure() == {
        "error_code": SURFACE_CHANGED,
        "message": (
            "The active VibeCAD tool surface changed after this turn started. "
            "Resume from the current ribbon in a new turn."
        ),
        "current_surface": (
            "mesh"
            if change == "surface"
            else "drawing"
            if change == "preference"
            else "model"
        ),
        "repair": {"resume_next_turn": True},
    }


def test_native_surface_module_exposes_no_activation_api() -> None:
    import VibeCADNativeSurface as module

    public_names = {name for name in vars(module) if not name.startswith("_")}
    assert not any(
        "activate" in name.lower() or "switch" in name.lower()
        for name in public_names
    )
