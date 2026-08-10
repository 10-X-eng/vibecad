# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared topology contract for exact Native Model Body shapes."""

from __future__ import annotations

from typing import Any


def is_valid_solid_shape(shape: Any) -> bool:
    """Return whether *shape* is valid and contains at least one solid."""

    try:
        return (
            shape is not None
            and not shape.isNull()
            and shape.isValid()
            and len(shape.Solids) > 0
        )
    except Exception:
        return False


def is_valid_body_shape(body: Any, shape: Any | None = None) -> bool:
    """Apply the exact Body's saved ``AllowCompound`` topology policy."""

    candidate = getattr(body, "Shape", None) if shape is None else shape
    if not is_valid_solid_shape(candidate):
        return False
    try:
        solid_count = len(candidate.Solids)
        allow_compound = bool(getattr(body, "AllowCompound", False))
    except Exception:
        return False
    return allow_compound or solid_count == 1
