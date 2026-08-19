# SPDX-License-Identifier: LGPL-2.1-or-later

"""Freeze and recheck the human-selected ribbon for Native assistant calls.

This module owns identity only. Capability classification and execution live in
separate modules, and Native mode remains unavailable until those layers pass
their complete rollout gate. There is deliberately no ribbon/workbench
activation function here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADRibbonSurface import RibbonSurface, read_active_ribbon_surface


SURFACE_CHANGED = "NATIVE_SURFACE_CHANGED"


@dataclass(frozen=True, slots=True)
class NativeSurfaceSnapshot:
    """Immutable authorization identity captured at human turn start."""

    surface_id: str
    revision: int
    manifest_sha256: str
    command_ids: tuple[str, ...]
    available_command_ids: tuple[str, ...]
    unavailable_command_ids: tuple[str, ...]
    environment_sha256: str = "0" * 64

    @classmethod
    def from_surface(cls, surface: RibbonSurface) -> "NativeSurfaceSnapshot":
        available = tuple(
            action.command_id for action in surface.actions if action.available
        )
        unavailable = tuple(
            action.command_id for action in surface.actions if not action.available
        )
        return cls(
            surface_id=surface.surface_id,
            revision=surface.revision,
            manifest_sha256=surface.manifest_sha256,
            command_ids=surface.command_ids,
            available_command_ids=available,
            unavailable_command_ids=unavailable,
            environment_sha256=surface.environment_sha256,
        )

    @property
    def available(self) -> bool:
        return self.surface_id != "unavailable"

    @property
    def authorization_token(self) -> str:
        return ":".join(
            (
                self.surface_id,
                str(self.revision),
                self.manifest_sha256,
                self.environment_sha256,
            )
        )

    @property
    def modeling_surface_id(self) -> str:
        """Provider-visible identity for this exact Native environment."""

        return "/".join(
            (
                "vibecad",
                "surface",
                "native",
                self.surface_id,
                str(self.revision),
                self.manifest_sha256[:12],
                self.environment_sha256[:12],
            )
        )

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "mode": "native",
            "surface_id": self.surface_id,
            "surface_revision": self.revision,
            "manifest_sha256": self.manifest_sha256,
            "available": self.available,
            "action_count": len(self.command_ids),
            "available_action_count": len(self.available_command_ids),
        }
        if self.unavailable_command_ids:
            result["unavailable_action_count"] = len(self.unavailable_command_ids)
        return result


class NativeSurfaceChanged(RuntimeError):
    """The human-selected surface no longer matches a frozen turn."""

    def __init__(
        self,
        expected: NativeSurfaceSnapshot,
        current: NativeSurfaceSnapshot,
    ) -> None:
        super().__init__(
            "The available CAD tools changed after this turn started. "
            "Continue in a new turn."
        )
        self.expected = expected
        self.current = current

    def failure(self) -> dict[str, Any]:
        return {
            "error_code": SURFACE_CHANGED,
            "message": str(self),
            "current_surface": self.current.surface_id,
            "repair": {"resume_next_turn": True},
        }


def freeze_native_surface(controller: Any | None = None) -> NativeSurfaceSnapshot:
    """Capture the exact live ribbon without changing UI state."""

    return NativeSurfaceSnapshot.from_surface(read_active_ribbon_surface(controller))


def require_frozen_native_surface(
    expected: NativeSurfaceSnapshot,
    controller: Any | None = None,
) -> NativeSurfaceSnapshot:
    """Return the current snapshot or reject an invalidated Native turn."""

    if not isinstance(expected, NativeSurfaceSnapshot):
        raise TypeError("expected must be a NativeSurfaceSnapshot")
    current = freeze_native_surface(controller)
    if current.authorization_token != expected.authorization_token:
        raise NativeSurfaceChanged(expected, current)
    return current
