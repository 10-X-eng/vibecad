# SPDX-License-Identifier: LGPL-2.1-or-later

"""Block behavior for the shared Native Sketch fake host."""

from __future__ import annotations


class FakeSketchBlockMixin:
    def diagnoseBlockConstraints(self, constraints):
        proposed = list(constraints) if isinstance(constraints, list) else [constraints]
        if self.FeasibilityOverride is not None:
            return dict(self.FeasibilityOverride)
        if any(
            item.Type != "Block"
            or item.FirstPos != 0
            or not 0 <= item.First < int(self.GeometryCount)
            for item in proposed
        ):
            raise ValueError("Fake Block feasibility target is invalid.")
        return {
            "accepted": True,
            "degrees_of_freedom": max(0, int(self.DoF) - len(proposed)),
            "solver_status": 0,
            "first_proposed_constraint_index": int(self.ConstraintCount),
            "proposed_constraint_count": len(proposed),
            "conflicting_constraint_indices": [],
            "redundant_constraint_indices": [],
            "partially_redundant_constraint_indices": [],
            "malformed_constraint_indices": [],
        }

    def _solve_block(self, constraint) -> None:
        self.GeometryFacadeList[constraint.First].Blocked = True
