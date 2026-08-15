# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADRibbonSurface import (
    BUILD_FEATURE_KEYS,
    PREFERENCE_DEFAULTS_BY_SURFACE,
    RibbonSurface,
    RibbonSurfaceError,
    read_active_ribbon_surface,
)


def _manifest(*, surface_id: str = "model") -> dict[str, object]:
    return {
        "schema_version": 1,
        "surface_id": surface_id,
        "groups": [
            {
                "label": "Solids",
                "actions": [
                    {
                        "command_id": "PartDesign_DesignPrimitive",
                        "kind": "composite",
                        "label": "Create primitive",
                        "available": True,
                        "children": [
                            {
                                "command_id": "PartDesign::DesignBox",
                                "kind": "command",
                                "label": "Box",
                                "available": True,
                                "parent_command_id": "PartDesign_DesignPrimitive",
                            },
                            {
                                "command_id": "PartDesign::DesignCylinder",
                                "kind": "command",
                                "label": "Cylinder",
                                "available": True,
                                "parent_command_id": "PartDesign_DesignPrimitive",
                            },
                            *[
                                {
                                    "command_id": command_id,
                                    "kind": "command",
                                    "label": label,
                                    "available": True,
                                    "parent_command_id": "PartDesign_DesignPrimitive",
                                }
                                for command_id, label in (
                                    ("PartDesign::DesignSphere", "Sphere"),
                                    ("PartDesign::DesignCone", "Cone"),
                                    ("PartDesign::DesignEllipsoid", "Ellipsoid"),
                                    ("PartDesign::DesignTorus", "Torus"),
                                    ("PartDesign::DesignPrism", "Prism"),
                                    ("PartDesign::DesignWedge", "Wedge"),
                                    ("PartDesign::DesignTube", "Tube"),
                                )
                            ],
                        ],
                    }
                ],
            },
            {
                "label": "Inspect",
                "actions": [
                    {
                        "command_id": "Part_CheckGeometry",
                        "kind": "command",
                        "label": "Check geometry",
                        "available": True,
                    }
                ],
            },
        ],
    }


def _environment(*, surface_id: str = "model") -> dict[str, object]:
    return {
        "schema_version": 1,
        "build_features": {name: True for name in BUILD_FEATURE_KEYS},
        "preferences": dict(PREFERENCE_DEFAULTS_BY_SURFACE.get(surface_id, ())),
    }


class _Controller:
    def __init__(self, manifest: dict[str, object], revision: int = 7) -> None:
        surface_id = str(manifest["surface_id"])
        self.values = {
            "VibeCADActiveSurfaceManifest": manifest,
            "VibeCADActiveSurfaceRevision": revision,
            "VibeCADActiveSurfaceId": surface_id,
            "VibeCADActiveSurfaceEnvironment": _environment(
                surface_id=surface_id
            ),
        }

    def property(self, name: str) -> object:
        return self.values.get(name)


def test_manifest_preserves_order_and_flattens_composite_children() -> None:
    surface = RibbonSurface.from_manifest(
        _manifest(),
        revision=7,
        environment=_environment(),
    )

    assert surface.surface_id == "model"
    assert surface.token == "model:7"
    assert surface.authorization_token == (
        f"model:7:{surface.manifest_sha256}:{surface.environment_sha256}"
    )
    assert len(surface.manifest_sha256) == 64
    assert len(surface.environment_sha256) == 64
    assert surface.to_environment() == _environment()
    assert [group.label for group in surface.groups] == ["Solids", "Inspect"]
    assert surface.command_ids == (
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
    assert surface.actions[0].kind == "composite"
    assert surface.actions[1].parent_command_id == "PartDesign_DesignPrimitive"
    assert surface.to_manifest() == _manifest()


def test_controller_surface_and_manifest_must_agree() -> None:
    controller = _Controller(_manifest())
    controller.values["VibeCADActiveSurfaceId"] = "mesh"

    with pytest.raises(RibbonSurfaceError, match="controller declares"):
        read_active_ribbon_surface(controller)


def test_controller_requires_exact_build_and_preference_environment() -> None:
    controller = _Controller(_manifest())
    controller.values.pop("VibeCADActiveSurfaceEnvironment")
    with pytest.raises(RibbonSurfaceError, match="did not publish"):
        read_active_ribbon_surface(controller)

    controller = _Controller(_manifest())
    environment = controller.values["VibeCADActiveSurfaceEnvironment"]
    environment["build_features"].pop("fem_vtk")
    with pytest.raises(RibbonSurfaceError, match="build features"):
        read_active_ribbon_surface(controller)

    controller = _Controller(_manifest())
    environment = controller.values["VibeCADActiveSurfaceEnvironment"]
    environment["preferences"]["cam.enable_experimental_features"] = True
    with pytest.raises(RibbonSurfaceError, match="exact supported set"):
        read_active_ribbon_surface(controller)


def test_surface_relevant_preference_set_is_exact_per_domain() -> None:
    for surface_id, expected in PREFERENCE_DEFAULTS_BY_SURFACE.items():
        controller = _Controller(_manifest(surface_id=surface_id))
        surface = read_active_ribbon_surface(controller)
        assert surface.to_environment()["preferences"] == dict(expected)

    model = read_active_ribbon_surface(_Controller(_manifest()))
    assert model.to_environment()["preferences"] == {}


def test_duplicate_command_ids_are_rejected_across_groups() -> None:
    manifest = _manifest()
    manifest["groups"][1]["actions"][0]["command_id"] = "PartDesign::DesignBox"

    with pytest.raises(RibbonSurfaceError, match="duplicate command IDs"):
        RibbonSurface.from_manifest(manifest, revision=2)


def test_composite_child_must_declare_its_exact_parent() -> None:
    manifest = _manifest()
    manifest["groups"][0]["actions"][0]["children"][0][
        "parent_command_id"
    ] = "WrongParent"

    with pytest.raises(RibbonSurfaceError, match="expected"):
        RibbonSurface.from_manifest(manifest, revision=3)


@pytest.mark.parametrize("revision", (None, 0, -1, True, "4"))
def test_revision_must_be_a_positive_integer(revision: object) -> None:
    with pytest.raises(RibbonSurfaceError, match="positive integer"):
        RibbonSurface.from_manifest(_manifest(), revision=revision)


def test_unknown_surface_is_rejected() -> None:
    with pytest.raises(RibbonSurfaceError, match="Unknown ribbon surface"):
        RibbonSurface.from_manifest(_manifest(surface_id="DraftWorkbench"), revision=1)


def test_reader_exposes_no_surface_activation_api() -> None:
    import VibeCADRibbonSurface as module

    public_names = {name for name in vars(module) if not name.startswith("_")}
    assert not any("activate" in name.lower() or "switch" in name.lower() for name in public_names)
