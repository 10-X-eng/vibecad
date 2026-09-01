# SPDX-License-Identifier: LGPL-2.1-or-later

"""Frozen-schema and live-state context for the Native provider path."""

from __future__ import annotations

from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    NativeProviderSurface,
    project_native_provider_surface,
    provider_visible_native_schema,
    resolve_native_provider_surface,
)
import VibeCADRibbonSurface as ribbon_surface


def native_authoring_mode_availability() -> tuple[bool, str]:
    """Return whether the live human ribbon can enter Native authority.

    Authoring-mode UI only needs the validated ribbon identity. Building the
    complete provider registry here imports every Native binding and schema,
    even though no provider turn is starting.
    """

    try:
        surface = ribbon_surface.read_active_ribbon_surface()
    except Exception as exc:
        return False, f"The active VibeCAD ribbon is unavailable: {exc}"
    if surface.surface_id == "unavailable":
        return False, "The active VibeCAD ribbon has no Native authoring surface."
    return True, ""


def resolve_production_native_surface(
) -> tuple[NativeCapabilityRegistry, NativeProviderSurface]:
    from VibeCADNativeRegistry import build_native_capability_registry

    registry = build_native_capability_registry()
    surface = resolve_native_provider_surface(
        ribbon_surface.read_active_ribbon_surface(),
        registry,
    )
    return registry, surface


def provider_authorized_native_surface(
    surface: NativeProviderSurface,
    active_state: dict[str, Any] | None = None,
    *,
    registry: NativeCapabilityRegistry | None = None,
) -> NativeProviderSurface:
    """Keep ribbon choice human-owned, then apply exact document scope."""

    if not isinstance(surface, NativeProviderSurface):
        raise TypeError("surface must be a NativeProviderSurface")
    if not surface.available:
        return surface
    surface = project_native_provider_surface(
        surface,
        tuple(name for name in surface.tool_names if name != "workspace.switch"),
    )
    if active_state is not None and surface.snapshot.surface_id == "analyze":
        from VibeCADNativeAnalyzeProviderScope import scope_analyze_provider_surface

        surface = scope_analyze_provider_surface(
            surface,
            active_state,
            registry=registry,
        )
    if active_state is not None and surface.snapshot.surface_id == "manufacture":
        from VibeCADNativeManufactureProviderScope import (
            scope_manufacture_provider_surface,
        )

        surface = scope_manufacture_provider_surface(
            surface,
            active_state,
            registry=registry,
        )
    return surface


def native_provider_tool_schemas(
    active_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    registry, surface = resolve_production_native_surface()
    surface = provider_authorized_native_surface(
        surface,
        active_state,
        registry=registry,
    )
    return schemas_for_native_provider_surface(surface)


def schemas_for_native_provider_surface(
    surface: NativeProviderSurface,
) -> list[dict[str, Any]]:
    """Copy schemas from one already-resolved live manifest surface."""

    if not isinstance(surface, NativeProviderSurface):
        raise TypeError("surface must be a NativeProviderSurface")
    if not surface.available:
        return []
    return [provider_visible_native_schema(schema) for schema in surface.schemas]


def native_active_state(service: Any) -> dict[str, Any]:
    state = service.native_active_snapshot()
    if not isinstance(state, dict):
        raise RuntimeError("Native active state did not return an object.")
    return state


def provider_visible_native_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return only live facts that affect the provider's next decision."""

    if not isinstance(state, dict):
        raise TypeError("state must be a Native active-state object")
    if state.get("surface_id") == "analyze":
        from VibeCADNativeAnalyzeProviderState import compact_analyze_provider_state

        return compact_analyze_provider_state(state)
    if state.get("surface_id") == "drawing":
        from VibeCADNativeDrawingProviderState import compact_drawing_provider_state

        return compact_drawing_provider_state(state)
    if state.get("surface_id") == "manufacture":
        from VibeCADNativeManufactureProviderState import (
            compact_manufacture_provider_state,
        )

        return compact_manufacture_provider_state(state)
    return state
