# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from typing import Any

from Base.Metadata import export
from TechDraw.DrawViewPart import DrawViewPart


@export(
    Include="Mod/TechDraw/App/DrawViewSection.h",
    Namespace="TechDraw",
    FatherInclude="Mod/TechDraw/App/DrawViewPartPy.h",
)
class DrawViewSection(DrawViewPart):
    """Feature for creating and manipulating a straight Technical Drawing section view."""

    def getPrecomputedSection(self) -> Any:
        """
        Return the raw cut, page-aligned section faces, and source centroid
        needed to reproduce this completed simple section without cutting its
        source again.
        """
        ...

    def setPrecomputedSection(self) -> Any:
        """
        Adopt the bounded section snapshot returned by
        getPrecomputedSection(). The ordinary projected-edge cache must be
        current before this section-specific cache is adopted.
        """
        ...

    def requestPrecomputedSectionPaint(self) -> Any:
        """
        Repaint a fully adopted section and its base-view cut line after the
        owning document transaction has committed.
        """
        ...
