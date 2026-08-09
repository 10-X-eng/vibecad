# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused fake-host behavior for Sketch Active/Inactive tests."""

from __future__ import annotations


class FakeSketchActiveMixin:
    def setActive(self, index: int, active: bool) -> None:
        if type(index) is not int or not 0 <= index < len(self.Constraints):
            raise IndexError("Fake active index is outside the Sketch.")
        if type(active) is not bool:
            raise TypeError("Fake active state must be boolean.")
        self.Constraints[index].IsActive = active

    def getActive(self, index: int) -> bool:
        if type(index) is not int or not 0 <= index < len(self.Constraints):
            raise IndexError("Fake active index is outside the Sketch.")
        return bool(self.Constraints[index].IsActive)

    def toggleActive(self, index: int) -> None:
        self.setActive(index, not self.getActive(index))

    def diagnoseActiveChanges(self, changes):
        values = list(changes)
        if not 1 <= len(values) <= 16:
            raise ValueError("Fake active changes are unbounded.")
        indices = []
        states = []
        for item in values:
            if (
                not isinstance(item, (list, tuple))
                or len(item) != 2
                or type(item[0]) is not int
                or type(item[1]) is not bool
            ):
                raise TypeError("Fake active change is malformed.")
            index, state = item
            if (
                index in indices
                or not 0 <= index < len(self.Constraints)
                or bool(self.Constraints[index].IsActive) is state
            ):
                raise ValueError("Fake active change target is invalid.")
            indices.append(index)
            states.append(state)
        if self.FeasibilityOverride is not None:
            return dict(self.FeasibilityOverride)
        delta = sum(-1 if state else 1 for state in states)
        return {
            "accepted": True,
            "degrees_of_freedom": max(0, int(self.DoF) + delta),
            "solver_status": 0,
            "conflicting_constraint_indices": [],
            "redundant_constraint_indices": [],
            "partially_redundant_constraint_indices": [],
            "malformed_constraint_indices": [],
            "constraint_indices": indices,
            "active_states": states,
        }
