# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic frame-parallel mechanism collision contracts."""

from __future__ import annotations

import threading
import time

import VibeCADMechanismGeometry as geometry


def _frames(count: int) -> list[dict]:
    return [
        {
            "frame_index": index,
            "frame_kind": "input" if index == 0 else "solver_output",
            "nominal_time_s": None if index == 0 else float(index - 1),
            "component_placements": {
                "First": {
                    "position_mm": [float(index), 0.0, 0.0],
                    "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
                },
                "Second": {
                    "position_mm": [float(index + 1), 0.0, 0.0],
                    "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
                },
            },
        }
        for index in range(count + 1)
    ]


def test_dynamic_collision_frames_use_isolated_parallel_evaluators(
    monkeypatch,
) -> None:
    lock = threading.Lock()
    instances = []
    active = 0
    maximum_active = 0

    class FakeEvaluator:
        component_names = ["First", "Second"]
        collision_mesh_statistics = {
            "unique_collision_mesh_count": 2,
            "unique_collision_mesh_triangle_count": 24,
            "collision_mesh_angular_deflection_radians": 0.5,
        }

        def __init__(self, _components, *, definition_keys=None) -> None:
            del definition_keys
            self.active = 0
            self.maximum_active = 0
            self.surface_worker_limits = []
            instances.append(self)

        def fork(self):
            return FakeEvaluator({})

        def precompute_strict_containment(
            self,
            _frames,
            *,
            excluded_pairs,
            progress_callback=None,
        ):
            del excluded_pairs, progress_callback
            return set()

        def evaluate_with_known_pairs(
            self,
            placements,
            *,
            known_pair_results,
            excluded_pairs,
            containment_pairs,
            pair_progress_callback=None,
            surface_worker_limit=None,
        ):
            nonlocal active, maximum_active
            del (
                known_pair_results,
                excluded_pairs,
                containment_pairs,
                pair_progress_callback,
            )
            self.surface_worker_limits.append(surface_worker_limit)
            with lock:
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                frame_index = int(placements["First"]["position_mm"][0])
                # Deliberately finish in a different order from the trace.
                time.sleep(0.005 * (9 - frame_index))
                return {
                    "broad_phase_candidate_count": 1,
                    "exact_common_count": 0,
                    "surface_proximity_count": 1,
                    "containment_collision_count": 0,
                    "collisions": [],
                }
            finally:
                with lock:
                    self.active -= 1
                    active -= 1

    monkeypatch.setattr(geometry, "DynamicCollisionEvaluator", FakeEvaluator)
    progress = []

    result = geometry.evaluate_dynamic_collisions(
        {"First": object(), "Second": object()},
        _frames(8),
        frame_workers=3,
        aggregate_progress_callback=lambda completed, total: progress.append(
            (completed, total)
        ),
    )

    assert maximum_active >= 2
    assert len(instances) == 3
    assert all(instance.maximum_active == 1 for instance in instances)
    assert all(
        limit == 1
        for instance in instances
        for limit in instance.surface_worker_limits
    )
    assert [frame["frame_index"] for frame in result["frames"]] == list(
        range(1, 9)
    )
    assert progress[0] == (0, 8)
    assert progress[-1] == (8, 8)
    assert [completed for completed, _total in progress] == list(range(9))


def test_recommended_frame_workers_balance_cpu_use_and_evaluator_setup() -> None:
    assert geometry.recommended_collision_frame_workers(100, cpu_count=1) == 1
    assert geometry.recommended_collision_frame_workers(100, cpu_count=8) == 6
    assert geometry.recommended_collision_frame_workers(4, cpu_count=56) == 2
    assert geometry.recommended_collision_frame_workers(100, cpu_count=56) == 10
    assert geometry.recommended_collision_frame_workers(141, cpu_count=56) == 12


def test_skipped_collision_summary_cannot_claim_collision_free() -> None:
    warning = {
        "code": "COLLISION_ANALYSIS_SKIPPED",
        "stage": "simulation_collision",
        "message": "Collision analysis was explicitly disabled for this simulation.",
    }

    summary = geometry.skipped_dynamic_collision_summary(
        ["First", "Second"],
        requested_frame_count=8,
        warning=warning,
    )

    assert summary["evaluation_mode"] == "off"
    assert summary["status"] == "not_checked"
    assert summary["analysis_complete"] is False
    assert summary["collision_free"] is False
    assert summary["requested_frame_count"] == 8
    assert summary["evaluated_frame_count"] == 0
    assert summary["warning_count"] == 1
    assert summary["warnings"] == [warning]
