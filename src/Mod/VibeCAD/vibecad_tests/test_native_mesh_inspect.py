# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Mesh inspection result semantics."""

from __future__ import annotations

from types import SimpleNamespace

from VibeCADNativeMeshInspect import _evaluation_issue_report
from VibeCADNativeMeshState import mesh_object_state


def test_steep_normal_transitions_are_observations_not_repair_defects() -> None:
    report = _evaluation_issue_report(
        {
            "surface_fold_overs": {"count": 7, "sample_indices": [40, 82]},
            "duplicated_facets": {"count": 0},
        }
    )

    assert report == {
        "repair_defects_found": False,
        "issue_counts": {"duplicated_facets": 0},
        "issue_samples": {},
        "geometric_observations": {
            "steep_normal_transitions": {
                "count": 7,
                "threshold_degrees": 120,
                "sample_facet_indices": [40, 82],
            }
        },
    }


def test_structural_findings_remain_explicit_repair_defects() -> None:
    report = _evaluation_issue_report(
        {
            "surface_fold_overs": {"count": 0},
            "duplicated_facets": {"count": 2, "sample_indices": [3, 8]},
        }
    )

    assert report["repair_defects_found"] is True
    assert report["issue_counts"] == {"duplicated_facets": 2}
    assert report["issue_samples"] == {"duplicated_facets": [3, 8]}
    assert report["geometric_observations"] == {}


def test_empty_shape_is_not_read_as_mesh_domain_geometry(monkeypatch) -> None:
    class NullShape:
        def isNull(self) -> bool:
            return True

        @property
        def ShapeType(self):
            raise RuntimeError("cannot determine type of null shape")

    obj = SimpleNamespace(
        Name="EmptyFeature",
        Label="Empty Feature",
        TypeId="PartDesign::Feature",
        Shape=NullShape(),
        PropertiesList=(),
    )
    monkeypatch.setattr(
        "VibeCADNativeMeshState.concise_object",
        lambda _obj: {"object_name": "EmptyFeature", "label": "Empty Feature"},
    )

    state = mesh_object_state(obj)

    assert "topology" not in state
    assert "shape_type" not in state
    assert len(state["state_sha256"]) == 64
