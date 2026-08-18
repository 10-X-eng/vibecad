# SPDX-License-Identifier: LGPL-2.1-or-later

"""Frozen-schema and live-state context for the Native provider path."""

from __future__ import annotations

import json
from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    NativeProviderSurface,
    provider_visible_native_schema,
    resolve_native_provider_surface,
)
from VibeCADNativeRegistry import build_native_capability_registry
import VibeCADRibbonSurface as ribbon_surface


def resolve_production_native_surface(
) -> tuple[NativeCapabilityRegistry, NativeProviderSurface]:
    registry = build_native_capability_registry()
    surface = resolve_native_provider_surface(
        ribbon_surface.read_active_ribbon_surface(),
        registry,
    )
    return registry, surface


def native_provider_tool_schemas(
    *,
    interaction_mode: str,
) -> list[dict[str, Any]]:
    registry, surface = resolve_production_native_surface()
    return schemas_for_native_provider_surface(
        surface,
        interaction_mode=interaction_mode,
        registry=registry,
    )


def schemas_for_native_provider_surface(
    surface: NativeProviderSurface,
    *,
    interaction_mode: str,
    registry: NativeCapabilityRegistry | None = None,
) -> list[dict[str, Any]]:
    """Copy schemas from one already-resolved live manifest surface."""

    if not isinstance(surface, NativeProviderSurface):
        raise TypeError("surface must be a NativeProviderSurface")
    if not surface.available:
        return []
    mode = str(interaction_mode or "build").strip().lower()
    if mode == "build":
        schemas = surface.schemas
    elif mode == "plan":
        selected_registry = registry or build_native_capability_registry()
        schemas = []
        for name, schema in zip(
            surface.tool_names,
            surface.schemas,
            strict=True,
        ):
            definition = selected_registry.definition(name)
            if (
                definition is not None
                and definition.primary_classification in {"read", "view"}
            ):
                schemas.append(schema)
    else:
        raise ValueError(f"Unknown Native interaction mode {mode!r}.")
    return [provider_visible_native_schema(schema) for schema in schemas]


def native_active_state(service: Any) -> dict[str, Any]:
    state = service.native_active_snapshot()
    if not isinstance(state, dict):
        raise RuntimeError("Native active state did not return an object.")
    return state
