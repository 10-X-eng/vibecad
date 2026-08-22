# SPDX-License-Identifier: LGPL-2.1-or-later

"""Published Assembly connector discovery regressions."""

from __future__ import annotations


def test_published_axis_frame_is_ranked_as_an_axis_connector() -> None:
    from VibeCADAssemblyConnectorDiscovery import _interface_record

    record = _interface_record(
        {
            "name": "ShaftAxis",
            "selection_type": "frame",
            "connector_eligible": True,
            "geometry_type": "component_frame",
            "connector": {
                "kind": "axis",
                "allowed_joints": ["revolute", "gears"],
                "compatibility": "DRIVE_SHAFT",
            },
            "frame": {
                "origin_mm": [0.0, 0.0, 12.0],
                "axis_direction": [0.0, 0.0, 1.0],
            },
        }
    )

    assert record is not None
    assert record["geometry"] == "cylinder"
    assert record["selection"] == {
        "type": "published_interface",
        "interface_name": "ShaftAxis",
    }
