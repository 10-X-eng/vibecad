# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from typing import Final

from Base.Metadata import export

from App.Part import Part
from App.DocumentObject import DocumentObject

@export(
    Include="Mod/Assembly/App/AssemblyLink.h",
    Namespace="Assembly",
)
class AssemblyLink(Part):
    """
    This class handles document objects in Assembly

    Author: Ondsel (development@ondsel.com)
    License: LGPL-2.1-or-later
    """

    Joints: Final[list]
    """A list of all joints this assembly link has."""

    def synchronizeContents(self) -> None:
        """Synchronize linked components and joints without recomputing the document.

        This is intended for bounded publishers that already hold validated
        placements and must not trigger unrelated document execution.
        """
        ...

    def synchronizeContentsWithResourceMap(
        self,
        ordered_old_resources: list[DocumentObject],
        /,
    ) -> dict:
        """Atomically synchronize and return the native managed-resource graph.

        The result contains ``final_resources`` in canonical retained/new
        order, ``old_to_final`` tuples of ``(old_id, old_name, final_or_none)``,
        and explicit ``retired`` ``(old_id, old_name)`` identities. Mapping is
        recorded by the native reuse/create/replace branches while they run;
        generated names and document deltas are never used. For a published
        occurrence, the method stages and reconciles its complete History
        resource block and preserves every resource not managed by native
        AssemblyLink synchronization.
        """
        ...
