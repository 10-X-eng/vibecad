# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from typing import Any

from Base.Metadata import export
from TechDraw.DrawViewSymbol import DrawViewSymbol


@export(
    Include="Mod/TechDraw/App/DrawViewDraft.h",
    Namespace="TechDraw",
    FatherInclude="Mod/TechDraw/App/DrawViewSymbolPy.h",
)
class DrawViewDraft(DrawViewSymbol):
    """Feature for rendering one Draft source on a TechDraw page."""

    def getPrecomputedDraft(self) -> Any:
        """Return the persisted isolated-worker SVG and exact source-state token."""
        ...

    def setPrecomputedDraft(self) -> Any:
        """Adopt a bounded SVG and source-state token from an isolated worker."""
        ...
