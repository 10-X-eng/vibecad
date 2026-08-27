# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared assertions for compact provider and exact dispatch schemas."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import NativeCapabilityDefinition


def exact_provider_branches(
    definition: NativeCapabilityDefinition,
    operations: tuple[str, ...],
) -> dict[str, dict]:
    """Return the exact dispatch branch for each requested provider operation."""

    variants = {variant.operation: variant for variant in definition.variants}
    missing = [operation for operation in operations if operation not in variants]
    if missing:
        raise AssertionError(f"Missing exact provider variants: {missing!r}")
    return {
        operation: variants[operation].provider_parameters()
        for operation in operations
    }
