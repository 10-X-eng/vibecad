# SPDX-License-Identifier: LGPL-2.1-or-later

"""Immutable turn authorization for the future Native assistant mode.

This module freezes and rechecks identity only. It deliberately contains no
tool execution, workbench activation, ribbon switching, or domain behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistryError,
    NativeCapabilityRegistry,
    NativeProviderSurface,
    _provider_schema_operations,
    provider_visible_native_schema,
    project_native_provider_operations,
    project_native_provider_surface,
    resolve_native_provider_surface,
)
from VibeCADNativeSurface import (
    NativeSurfaceSnapshot,
    SURFACE_CHANGED,
    require_frozen_native_surface,
)
from VibeCADRibbonSurface import read_active_ribbon_surface


NATIVE_TURN_UNAVAILABLE = "NATIVE_TURN_UNAVAILABLE"


class NativeTurnUnavailable(RuntimeError):
    """A complete Native surface is not available for a new turn."""

    def __init__(self, reason: str) -> None:
        super().__init__(str(reason).strip() or "Native mode is unavailable.")

    def failure(self) -> dict[str, Any]:
        return {
            "error_code": NATIVE_TURN_UNAVAILABLE,
            "message": str(self),
        }


class NativeTurnChanged(RuntimeError):
    """The exact Native schema contract changed after turn start."""

    def __init__(self, current_surface: str) -> None:
        super().__init__(
            "The available CAD tools changed after this turn started. "
            "Continue in a new turn."
        )
        self.current_surface = str(current_surface)

    def failure(self) -> dict[str, Any]:
        return {
            "error_code": SURFACE_CHANGED,
            "message": str(self),
            "current_surface": self.current_surface,
            "repair": {"resume_next_turn": True},
        }


def _canonical_schemas(schemas: tuple[dict[str, Any], ...]) -> str:
    return json.dumps(
        schemas,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _captured_schema_operations(
    registry: NativeCapabilityRegistry,
    schema: dict[str, Any],
) -> tuple[str, ...]:
    operations = _provider_schema_operations(schema)
    if operations:
        return operations
    name = str(schema.get("name") or "")
    definition = registry.definition(name)
    if definition is None:
        raise NativeTurnUnavailable(
            f"The captured Native capability {name!r} has no definition."
        )
    matches = tuple(
        variant.operation
        for variant in definition.variants
        if provider_visible_native_schema(
            definition.provider_schema((variant.operation,))
        )
        == schema
    )
    if len(matches) != 1:
        raise NativeTurnUnavailable(
            f"The captured Native schema for {name!r} does not identify one exact operation."
        )
    return matches


@dataclass(frozen=True, slots=True)
class NativeTurnSnapshot:
    """Exact ribbon and provider schema identity frozen for one human turn."""

    surface: NativeSurfaceSnapshot
    tool_names: tuple[str, ...]
    schema_sha256: str
    _schemas_json: str = field(repr=False, compare=True)

    @classmethod
    def from_provider_surface(
        cls,
        provider_surface: NativeProviderSurface,
    ) -> "NativeTurnSnapshot":
        if not isinstance(provider_surface, NativeProviderSurface):
            raise TypeError("provider_surface must be a NativeProviderSurface")
        if not provider_surface.available:
            raise NativeTurnUnavailable(provider_surface.unavailable_reason)
        names = tuple(
            str(schema.get("name") or "").strip()
            for schema in provider_surface.schemas
        )
        if (
            not names
            or any(not name for name in names)
            or names != provider_surface.tool_names
            or len(names) != len(set(names))
        ):
            raise NativeTurnUnavailable(
                "The complete Native surface has an invalid provider schema set."
            )
        encoded = _canonical_schemas(provider_surface.schemas)
        return cls(
            surface=provider_surface.snapshot,
            tool_names=names,
            schema_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            _schemas_json=encoded,
        )

    @property
    def provider_schemas(self) -> tuple[dict[str, Any], ...]:
        return tuple(json.loads(self._schemas_json))

    def summary(self) -> dict[str, Any]:
        return {
            "mode": "native",
            "surface_id": self.surface.surface_id,
            "surface_revision": self.surface.revision,
            "schema_sha256": self.schema_sha256,
            "tool_count": len(self.tool_names),
        }


def freeze_native_turn(
    controller: Any | None = None,
    registry: NativeCapabilityRegistry | None = None,
    tool_names: tuple[str, ...] | None = None,
    active_state: dict[str, Any] | None = None,
    provider_schemas: tuple[dict[str, Any], ...] | None = None,
) -> NativeTurnSnapshot:
    """Freeze an exact validated Native surface without changing UI state."""

    surface = read_active_ribbon_surface(controller)
    provider_surface = resolve_native_provider_surface(surface, registry)
    if active_state is not None:
        from VibeCADNativeProviderContext import provider_authorized_native_surface

        provider_surface = provider_authorized_native_surface(
            provider_surface,
            active_state,
            registry=registry,
        )
    if tool_names is not None:
        try:
            provider_surface = project_native_provider_surface(
                provider_surface,
                tool_names,
            )
        except NativeCapabilityRegistryError as exc:
            raise NativeTurnUnavailable(str(exc)) from exc
    if provider_schemas is not None:
        if registry is None:
            raise NativeTurnUnavailable(
                "Freezing exact Native operations requires a capability registry."
            )
        operations_by_tool = {}
        for schema in provider_schemas:
            name = str(schema.get("name") or "")
            definition = registry.definition(name)
            if definition is None:
                raise NativeTurnUnavailable(
                    f"The captured Native capability {name!r} has no definition."
                )
            if len(definition.variants) > 1:
                operations_by_tool[name] = _captured_schema_operations(
                    registry,
                    schema,
                )
        try:
            provider_surface = project_native_provider_operations(
                provider_surface,
                registry,
                operations_by_tool,
            )
        except NativeCapabilityRegistryError as exc:
            raise NativeTurnUnavailable(str(exc)) from exc
    return NativeTurnSnapshot.from_provider_surface(provider_surface)


def require_frozen_native_turn(
    expected: NativeTurnSnapshot,
    controller: Any | None = None,
    registry: NativeCapabilityRegistry | None = None,
) -> NativeTurnSnapshot:
    """Reauthorize the exact ribbon and schemas before a future tool call."""

    if not isinstance(expected, NativeTurnSnapshot):
        raise TypeError("expected must be a NativeTurnSnapshot")
    current_surface = read_active_ribbon_surface(controller)
    require_frozen_native_surface(expected.surface, controller)
    current_provider = resolve_native_provider_surface(current_surface, registry)
    try:
        current_provider = project_native_provider_surface(
            current_provider,
            expected.tool_names,
        )
        if registry is not None:
            operations_by_tool = {
                str(schema.get("name") or ""): operations
                for schema in expected.provider_schemas
                if (operations := _provider_schema_operations(schema))
            }
            current_provider = project_native_provider_operations(
                current_provider,
                registry,
                operations_by_tool,
            )
        current = NativeTurnSnapshot.from_provider_surface(current_provider)
    except (NativeCapabilityRegistryError, NativeTurnUnavailable) as exc:
        raise NativeTurnChanged(current_surface.surface_id) from exc
    if (
        current.tool_names != expected.tool_names
        or current.schema_sha256 != expected.schema_sha256
    ):
        raise NativeTurnChanged(current_surface.surface_id)
    return current
