# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused fake-host behavior for Native Sketch Constraint Group tests."""

from __future__ import annotations


class FakeSketchGroupMixin:
    def enablePersistentGeometryTags(self) -> None:
        self._persistent_geometry_tags = True
        self._next_geometry_tag = 0
        for facade in self.GeometryFacadeList:
            facade.Tag = f"fake-geometry-{self._next_geometry_tag}"
            self._next_geometry_tag += 1

    @staticmethod
    def _constraint_geometry_indices(constraint) -> set[int]:
        return {
            int(item[0])
            for item in getattr(constraint, "Elements", ())
            if int(item[0]) >= 0
        }

    @staticmethod
    def _rewrite_constraint_geometry_indices(constraint, deleted: int) -> None:
        elements = tuple(
            (index - 1 if index > deleted else index, position)
            for index, position in constraint.Elements
        )
        constraint.Elements = elements
        padded = (*elements, (-2000, 0), (-2000, 0), (-2000, 0))
        constraint.First, constraint.FirstPos = padded[0]
        constraint.Second, constraint.SecondPos = padded[1]
        constraint.Third, constraint.ThirdPos = padded[2]

    def _delete_group_cleanup_geometry(self, index: int) -> None:
        del self.Geometry[index]
        del self.GeometryFacadeList[index]
        surviving = []
        for constraint in self.Constraints:
            if index in self._constraint_geometry_indices(constraint):
                continue
            self._rewrite_constraint_geometry_indices(constraint, index)
            surviving.append(constraint)
        self.Constraints = surviving
        self.GeometryCount = len(self.Geometry)
        self.ConstraintCount = len(self.Constraints)

    def deleteUnusedInternalGeometry(self, parent_index: int) -> None:
        children = []
        for constraint in self.Constraints:
            if (
                constraint.Type == "InternalAlignment"
                and constraint.Second == parent_index
            ):
                child = int(constraint.First)
                involvement = sum(
                    child in self._constraint_geometry_indices(candidate)
                    for candidate in self.Constraints
                )
                if involvement == 1:
                    children.append(child)
        for child in sorted(set(children), reverse=True):
            self._delete_group_cleanup_geometry(child)
