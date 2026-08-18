# SPDX-License-Identifier: LGPL-2.1-or-later

"""Read the human-selected VibeCAD ribbon surface without changing it.

The C++ ribbon controller publishes the exact action graph used to build the
visible page.  Native assistant code consumes this module instead of inferring
capabilities from FreeCAD workbench names.  This module intentionally has no
activation API: only the human-facing ribbon may change the active surface.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence


MANIFEST_SCHEMA_VERSION = 1
ENVIRONMENT_SCHEMA_VERSION = 1
SURFACE_IDS = frozenset(
    {
        "model",
        "assemble",
        "mesh",
        "analyze",
        "manufacture",
        "drawing",
        "parameters",
        "aero",
        "sketch.setup",
        "sketch.edit",
        "unavailable",
    }
)
BUILD_FEATURE_KEYS = (
    "assembly",
    "cam",
    "fasteners",
    "fem",
    "fem_netgen",
    "fem_vtk",
    "fem_vtk_python",
    "flat_mesh",
    "inspection",
    "measure",
    "mesh",
    "mesh_part",
    "part",
    "part_design",
    "points",
    "reverse_engineering",
    "robot",
    "sketcher",
    "spreadsheet",
    "surface",
    "techdraw",
)
PREFERENCE_DEFAULTS_BY_SURFACE: dict[str, tuple[tuple[str, bool], ...]] = {
    "manufacture": (
        ("cam.default_simulator_legacy", False),
        ("cam.enable_advanced_ocl_features", False),
        ("cam.enable_experimental_features", False),
    ),
    "drawing": (
        ("techdraw.separated_dimensioning_tools", False),
        ("techdraw.single_dimensioning_tool", True),
    ),
}


class RibbonSurfaceError(RuntimeError):
    """The live ribbon did not publish a valid, self-consistent surface."""


@dataclass(frozen=True, slots=True)
class RibbonSurfaceEnvironment:
    """Exact build and surface-relevant preference state."""

    build_features: tuple[tuple[str, bool], ...]
    preferences: tuple[tuple[str, bool], ...]

    @classmethod
    def defaults(cls, surface_id: str) -> "RibbonSurfaceEnvironment":
        return cls(
            tuple((name, False) for name in BUILD_FEATURE_KEYS),
            PREFERENCE_DEFAULTS_BY_SURFACE.get(surface_id, ()),
        )

    @classmethod
    def from_mapping(
        cls,
        value: Any,
        *,
        surface_id: str,
    ) -> "RibbonSurfaceEnvironment":
        record = _mapping(value, "environment")
        if set(record) != {"schema_version", "build_features", "preferences"}:
            raise RibbonSurfaceError(
                "Ribbon surface environment must contain exactly schema_version, "
                "build_features, and preferences."
            )
        if record.get("schema_version") != ENVIRONMENT_SCHEMA_VERSION:
            raise RibbonSurfaceError(
                "Unsupported ribbon surface environment schema version "
                f"{record.get('schema_version')!r}."
            )
        raw_features = _mapping(
            record.get("build_features"),
            "environment.build_features",
        )
        if set(raw_features) != set(BUILD_FEATURE_KEYS):
            raise RibbonSurfaceError(
                "Ribbon surface build features do not match the exact supported set."
            )
        features = tuple(
            (
                name,
                _required_bool(
                    raw_features[name],
                    f"environment.build_features.{name}",
                ),
            )
            for name in BUILD_FEATURE_KEYS
        )

        expected_preferences = PREFERENCE_DEFAULTS_BY_SURFACE.get(surface_id, ())
        expected_names = tuple(name for name, _default in expected_preferences)
        raw_preferences = _mapping(
            record.get("preferences"),
            "environment.preferences",
        )
        if set(raw_preferences) != set(expected_names):
            raise RibbonSurfaceError(
                f"Ribbon surface {surface_id!r} preferences do not match the "
                "exact supported set."
            )
        preferences = tuple(
            (
                name,
                _required_bool(
                    raw_preferences[name],
                    f"environment.preferences.{name}",
                ),
            )
            for name in expected_names
        )
        return cls(features, preferences)

    @property
    def sha256(self) -> str:
        encoded = json.dumps(
            self.to_mapping(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": ENVIRONMENT_SCHEMA_VERSION,
            "build_features": dict(self.build_features),
            "preferences": dict(self.preferences),
        }


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RibbonSurfaceError(f"Ribbon surface field {field!r} is empty.")
    return text


def _required_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise RibbonSurfaceError(f"Ribbon surface field {field!r} must be boolean.")
    return value


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RibbonSurfaceError(f"Ribbon surface field {field!r} must be an object.")
    return value


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise RibbonSurfaceError(f"Ribbon surface field {field!r} must be an array.")
    return value


@dataclass(frozen=True, slots=True)
class RibbonAction:
    command_id: str
    label: str
    available: bool
    kind: str
    parent_command_id: str | None = None
    children: tuple["RibbonAction", ...] = ()

    @classmethod
    def from_mapping(
        cls,
        value: Any,
        *,
        field: str,
        expected_parent: str | None = None,
    ) -> "RibbonAction":
        record = _mapping(value, field)
        allowed = {
            "command_id",
            "label",
            "available",
            "kind",
            "parent_command_id",
            "children",
        }
        unexpected = sorted(set(record) - allowed)
        if unexpected:
            raise RibbonSurfaceError(
                f"Ribbon action {field!r} has unexpected fields: {unexpected}."
            )

        command_id = _required_text(record.get("command_id"), f"{field}.command_id")
        label = _required_text(record.get("label"), f"{field}.label")
        available = _required_bool(record.get("available"), f"{field}.available")
        kind = _required_text(record.get("kind"), f"{field}.kind")
        if kind not in {"command", "composite"}:
            raise RibbonSurfaceError(
                f"Ribbon action {command_id!r} has unsupported kind {kind!r}."
            )

        parent_value = record.get("parent_command_id")
        parent = _required_text(parent_value, f"{field}.parent_command_id") if parent_value else None
        if parent != expected_parent:
            raise RibbonSurfaceError(
                f"Ribbon action {command_id!r} declares parent {parent!r}; "
                f"expected {expected_parent!r}."
            )

        raw_children = record.get("children", ())
        child_values = _sequence(raw_children, f"{field}.children")
        children = tuple(
            cls.from_mapping(
                child,
                field=f"{field}.children[{index}]",
                expected_parent=command_id,
            )
            for index, child in enumerate(child_values)
        )
        if kind == "command" and children:
            raise RibbonSurfaceError(
                f"Ribbon command {command_id!r} cannot contain child actions."
            )
        if kind == "composite" and not children:
            raise RibbonSurfaceError(
                f"Ribbon composite {command_id!r} must contain child actions."
            )
        if any(child.kind != "command" for child in children):
            raise RibbonSurfaceError(
                f"Ribbon composite {command_id!r} contains a nested composite."
            )
        return cls(
            command_id=command_id,
            label=label,
            available=available,
            kind=kind,
            parent_command_id=parent,
            children=children,
        )

    def flattened(self) -> tuple["RibbonAction", ...]:
        return (self, *self.children)

    def to_mapping(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "command_id": self.command_id,
            "label": self.label,
            "available": self.available,
            "kind": self.kind,
        }
        if self.parent_command_id is not None:
            record["parent_command_id"] = self.parent_command_id
        if self.children:
            record["children"] = [child.to_mapping() for child in self.children]
        return record


@dataclass(frozen=True, slots=True)
class RibbonGroup:
    label: str
    actions: tuple[RibbonAction, ...]

    @classmethod
    def from_mapping(cls, value: Any, *, field: str) -> "RibbonGroup":
        record = _mapping(value, field)
        if set(record) != {"label", "actions"}:
            raise RibbonSurfaceError(
                f"Ribbon group {field!r} must contain exactly label and actions."
            )
        label = _required_text(record.get("label"), f"{field}.label")
        action_values = _sequence(record.get("actions"), f"{field}.actions")
        actions = tuple(
            RibbonAction.from_mapping(action, field=f"{field}.actions[{index}]")
            for index, action in enumerate(action_values)
        )
        if not actions:
            raise RibbonSurfaceError(f"Ribbon group {label!r} has no actions.")
        return cls(label=label, actions=actions)

    def flattened_actions(self) -> tuple[RibbonAction, ...]:
        return tuple(action for parent in self.actions for action in parent.flattened())

    def to_mapping(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "actions": [action.to_mapping() for action in self.actions],
        }


@dataclass(frozen=True, slots=True)
class RibbonSurface:
    surface_id: str
    revision: int
    groups: tuple[RibbonGroup, ...]
    environment: RibbonSurfaceEnvironment | None = None

    def __post_init__(self) -> None:
        if self.environment is None:
            object.__setattr__(
                self,
                "environment",
                RibbonSurfaceEnvironment.defaults(self.surface_id),
            )
        elif not isinstance(self.environment, RibbonSurfaceEnvironment):
            raise TypeError("environment must be a RibbonSurfaceEnvironment")

    @classmethod
    def from_manifest(
        cls,
        value: Any,
        *,
        revision: Any,
        environment: Any | None = None,
    ) -> "RibbonSurface":
        manifest = _mapping(value, "manifest")
        if set(manifest) != {"schema_version", "surface_id", "groups"}:
            raise RibbonSurfaceError(
                "Ribbon surface manifest must contain exactly schema_version, "
                "surface_id, and groups."
            )
        if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise RibbonSurfaceError(
                "Unsupported ribbon surface manifest schema version "
                f"{manifest.get('schema_version')!r}."
            )
        surface_id = _required_text(manifest.get("surface_id"), "manifest.surface_id")
        if surface_id not in SURFACE_IDS:
            raise RibbonSurfaceError(f"Unknown ribbon surface {surface_id!r}.")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise RibbonSurfaceError("Ribbon surface revision must be a positive integer.")

        group_values = _sequence(manifest.get("groups"), "manifest.groups")
        groups = tuple(
            RibbonGroup.from_mapping(group, field=f"manifest.groups[{index}]")
            for index, group in enumerate(group_values)
        )
        if surface_id != "unavailable" and not groups:
            raise RibbonSurfaceError(
                f"Ribbon surface {surface_id!r} must contain at least one group."
            )

        command_ids = [
            action.command_id
            for group in groups
            for action in group.flattened_actions()
        ]
        duplicates = sorted(
            command_id for command_id in set(command_ids) if command_ids.count(command_id) > 1
        )
        if duplicates:
            raise RibbonSurfaceError(
                f"Ribbon surface {surface_id!r} contains duplicate command IDs: {duplicates}."
            )
        parsed_environment = (
            RibbonSurfaceEnvironment.defaults(surface_id)
            if environment is None
            else RibbonSurfaceEnvironment.from_mapping(
                environment,
                surface_id=surface_id,
            )
        )
        return cls(
            surface_id=surface_id,
            revision=revision,
            groups=groups,
            environment=parsed_environment,
        )

    @property
    def actions(self) -> tuple[RibbonAction, ...]:
        return tuple(action for group in self.groups for action in group.flattened_actions())

    @property
    def command_ids(self) -> tuple[str, ...]:
        return tuple(action.command_id for action in self.actions)

    @property
    def token(self) -> str:
        return f"{self.surface_id}:{self.revision}"

    @property
    def manifest_sha256(self) -> str:
        encoded = json.dumps(
            self.to_manifest(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def authorization_token(self) -> str:
        return f"{self.token}:{self.manifest_sha256}:{self.environment_sha256}"

    @property
    def environment_sha256(self) -> str:
        environment = self.environment
        if environment is None:  # pragma: no cover - guarded by __post_init__
            raise AssertionError("Ribbon surface environment is unavailable")
        return environment.sha256

    def to_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "surface_id": self.surface_id,
            "groups": [group.to_mapping() for group in self.groups],
        }

    def to_environment(self) -> dict[str, Any]:
        environment = self.environment
        if environment is None:  # pragma: no cover - guarded by __post_init__
            raise AssertionError("Ribbon surface environment is unavailable")
        return environment.to_mapping()


def read_active_ribbon_surface(controller: Any | None = None) -> RibbonSurface:
    """Return the exact current ribbon surface without activating anything."""

    if controller is None:
        try:
            import FreeCADGui as Gui
            from PySide import QtCore
        except ImportError as exc:  # pragma: no cover - exercised in the GUI build
            raise RibbonSurfaceError("The VibeCAD GUI is unavailable.") from exc

        main_window = Gui.getMainWindow()
        controller = main_window.findChild(
            QtCore.QObject,
            "VibeCADRibbonController",
        )
    if controller is None:
        raise RibbonSurfaceError("The VibeCAD ribbon controller is unavailable.")

    manifest = controller.property("VibeCADActiveSurfaceManifest")
    revision = controller.property("VibeCADActiveSurfaceRevision")
    environment = controller.property("VibeCADActiveSurfaceEnvironment")
    if environment is None:
        raise RibbonSurfaceError(
            "The VibeCAD ribbon controller did not publish its build and "
            "preference environment."
        )
    declared_surface = _required_text(
        controller.property("VibeCADActiveSurfaceId"),
        "controller.VibeCADActiveSurfaceId",
    )
    surface = RibbonSurface.from_manifest(
        manifest,
        revision=revision,
        environment=environment,
    )
    if surface.surface_id != declared_surface:
        raise RibbonSurfaceError(
            f"Ribbon controller declares surface {declared_surface!r}, but its "
            f"manifest declares {surface.surface_id!r}."
        )
    return surface
