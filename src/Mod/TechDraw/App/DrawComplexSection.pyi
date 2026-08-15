# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from typing import Any

from Base.Metadata import export
from TechDraw.DrawViewSection import DrawViewSection


@export(
    Include="Mod/TechDraw/App/DrawComplexSection.h",
    Namespace="TechDraw",
    FatherInclude="Mod/TechDraw/App/DrawViewSectionPy.h",
)
class DrawComplexSection(DrawViewSection):
    """Feature for a profile-driven Technical Drawing complex section view."""

    def getPrecomputedComplexSection(self) -> Any:
        """Return the completed cut, faces, prepared shape, and centroid."""
        ...

    def setPrecomputedComplexSection(self) -> Any:
        """Adopt one bounded complex-section worker snapshot."""
        ...
