# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused fake-host behavior for Sketch Driving/Reference tests."""

from __future__ import annotations

import re


_DIMENSIONAL_TYPES = frozenset(
    {
        "Distance",
        "DistanceX",
        "DistanceY",
        "Radius",
        "Diameter",
        "Angle",
        "SnellsLaw",
        "Weight",
    }
)
_INDEX_PATH = re.compile(r"^Constraints\[(\d+)\]$")


class FakeSketchDrivingMixin:
    def _constraint_expression_index(self, path: object) -> int | None:
        normalized = str(path).lstrip(".")
        indexed = _INDEX_PATH.fullmatch(normalized)
        if indexed:
            return int(indexed.group(1))
        if normalized.startswith("Constraints."):
            name = normalized[len("Constraints.") :]
            matches = [
                index
                for index, constraint in enumerate(self.Constraints)
                if str(getattr(constraint, "Name", "") or "") == name
            ]
            return matches[0] if len(matches) == 1 else None
        return None

    def setDriving(self, index: int, driving: bool) -> None:
        if type(index) is not int or not 0 <= index < len(self.Constraints):
            raise IndexError("Fake driving index is outside the Sketch.")
        if type(driving) is not bool:
            raise TypeError("Fake driving state must be boolean.")
        constraint = self.Constraints[index]
        if constraint.Type not in _DIMENSIONAL_TYPES:
            raise ValueError("Fake constraint is not dimensional.")
        if driving and not any(
            int(getattr(constraint, field)) >= 0
            for field in ("First", "Second", "Third")
        ):
            raise ValueError("Fake external-only constraint cannot become driving.")
        constraint.Driving = driving
        if not driving:
            self.ExpressionEngine = [
                expression
                for expression in self.ExpressionEngine
                if self._constraint_expression_index(expression[0]) != index
            ]

    def toggleDriving(self, index: int) -> None:
        self.setDriving(index, not bool(self.Constraints[index].Driving))

    def diagnoseDrivingChanges(self, changes):
        values = list(changes)
        if not 1 <= len(values) <= 16:
            raise ValueError("Fake driving changes are unbounded.")
        indices = []
        states = []
        for item in values:
            if (
                not isinstance(item, (list, tuple))
                or len(item) != 2
                or type(item[0]) is not int
                or type(item[1]) is not bool
            ):
                raise TypeError("Fake driving change is malformed.")
            index, state = item
            if index in indices or not 0 <= index < len(self.Constraints):
                raise ValueError("Fake driving change target is invalid.")
            constraint = self.Constraints[index]
            if (
                constraint.Type not in _DIMENSIONAL_TYPES
                or bool(constraint.Driving) is state
                or (
                    state
                    and not any(
                        int(getattr(constraint, field)) >= 0
                        for field in ("First", "Second", "Third")
                    )
                )
            ):
                raise ValueError("Fake driving change state is invalid.")
            indices.append(index)
            states.append(state)
        if self.FeasibilityOverride is not None:
            return dict(self.FeasibilityOverride)
        delta = sum(1 if not state else -1 for state in states)
        return {
            "accepted": True,
            "degrees_of_freedom": max(0, int(self.DoF) + delta),
            "solver_status": 0,
            "conflicting_constraint_indices": [],
            "redundant_constraint_indices": [],
            "partially_redundant_constraint_indices": [],
            "malformed_constraint_indices": [],
            "constraint_indices": indices,
            "driving_states": states,
        }
