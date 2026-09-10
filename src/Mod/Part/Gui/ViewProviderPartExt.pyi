# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from Base.Metadata import export, no_args
from Gui.ViewProviderGeometryObject import ViewProviderGeometryObject

@export(
    Include="Mod/Part/Gui/ViewProviderExt.h",
    Namespace="PartGui",
)
class ViewProviderPartExt(ViewProviderGeometryObject):
    """
    This is the ViewProvider geometry class

    Author: David Carter (dcarter@davidcarter.ca)
    Licence: LGPL
    """

    @no_args
    def getRenderedMeshSnapshot(self) -> object:
        """Retain the immutable displayed mesh, or None before it is available.

        Call on the GUI thread. Pass the opaque snapshot to
        PartGui.requestSectionMeshDisplay; it remains valid after view closure.
        """
        ...

    @no_args
    def getRenderedShapeSnapshot(self) -> object:
        """Return the cached native shape without reading document geometry.

        Call on the GUI thread. The snapshot retains geometry after the view
        closes; treat it as read-only and copy before modifying it. An empty
        shape means that no cached rendered generation is available yet.
        """
        ...
