# SPDX-License-Identifier: LGPL-2.1-or-later

"""Frozen-schema and live-state context for the Native provider path."""

from __future__ import annotations

import json
from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    NativeProviderSurface,
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
    _registry, surface = resolve_production_native_surface()
    return schemas_for_native_provider_surface(
        surface,
        interaction_mode=interaction_mode,
    )


def schemas_for_native_provider_surface(
    surface: NativeProviderSurface,
    *,
    interaction_mode: str,
) -> list[dict[str, Any]]:
    """Copy schemas from one already-resolved live manifest surface."""

    if not isinstance(surface, NativeProviderSurface):
        raise TypeError("surface must be a NativeProviderSurface")
    # Native planning needs its own read/view-only frozen turn contract. Keep
    # it unavailable until that contract is implemented instead of leaking
    # mutation schemas into Plan mode.
    if str(interaction_mode or "build").strip().lower() != "build":
        return []
    if not surface.available:
        return []
    return json.loads(json.dumps(surface.schemas))


def native_active_state(service: Any) -> dict[str, Any]:
    state = service.native_active_snapshot()
    if not isinstance(state, dict):
        raise RuntimeError("Native active state did not return an object.")
    return state
