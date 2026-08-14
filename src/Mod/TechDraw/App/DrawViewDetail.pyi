# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from typing import Any

from Base.Metadata import export
from TechDraw.rawViewPart import DrawViewPart


@export(
    Include="Mod/TechDraw/App/DrawViewDetail.h",
    Namespace="TechDraw",
    FatherInclude="Mod/TechDraw/App/DrawViewPartPy.h",
)
class DrawViewDetail(DrawViewPart):
    """A TechDraw detail view with durable detached-computation state."""

    def getPrecomputedDetail(self) -> Any:
        """Return the exact accepted detail-cut shape."""
        ...

    def setPrecomputedDetail(self) -> Any:
        """Adopt an exact bounded detail-cut snapshot without recomputing it."""
        ...

    def requestPrecomputedDetailPaint(self) -> Any:
        """Paint an adopted detail and refresh its highlight on the base view."""
        ...
