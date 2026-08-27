# SPDX-License-Identifier: LGPL-2.1-or-later

"""Temporary internal FEM execution routing for extraction rollback exercises.

This module deliberately exposes no preference, environment variable, command,
or provider field.  The extracted Analysis Runtime remains the default.  Tests
and tightly bounded recovery exercises may temporarily select the preserved
legacy execution path in the current Python context, after which the previous
route is restored automatically.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator


ANALYSIS_RUNTIME_FEM = "analysis_runtime_fem"
LEGACY_FEM_EXECUTION = "legacy_fem_execution"
_VALID_ROUTES = frozenset({ANALYSIS_RUNTIME_FEM, LEGACY_FEM_EXECUTION})
_CURRENT_ROUTE: ContextVar[str] = ContextVar(
    "vibecad_analysis_fem_execution_route",
    default=ANALYSIS_RUNTIME_FEM,
)


def current_fem_execution_route() -> str:
    """Return the internal route captured by a newly submitted FEM job."""

    return _CURRENT_ROUTE.get()


@contextmanager
def temporary_fem_execution_route(route: str) -> Iterator[str]:
    """Select one internal route only for the dynamic extent of this context."""

    clean_route = str(route or "").strip()
    if clean_route not in _VALID_ROUTES:
        raise ValueError(f"Unsupported internal FEM execution route: {clean_route!r}")
    token = _CURRENT_ROUTE.set(clean_route)
    try:
        yield clean_route
    finally:
        _CURRENT_ROUTE.reset(token)
