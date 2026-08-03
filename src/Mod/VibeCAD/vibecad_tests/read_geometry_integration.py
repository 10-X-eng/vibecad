# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD gate for universal, read-only B-rep inspection."""

from __future__ import annotations

import json

import FreeCAD as App
import Part

from VibeCADDocumentReferences import reference_for_target
from VibeCADGeometryInspection import (
    GeometryInspectionError,
    capture_geometry_read,
    complete_geometry_read,
)
from VibeCADSession import _run_universal_vibescript_tool


class _Service:
    def __init__(self, document):
        self.document = document

    def _active_document(self):
        return self.document


def main() -> int:
    document = App.newDocument("ReadGeometryContract")
    try:
        imported = document.addObject("Part::Feature", "ImportedMotor")
        imported.Label = "Imported STEP Motor"
        imported.Shape = Part.makeBox(10.0, 20.0, 30.0)
        imported.Placement = App.Placement(
            App.Vector(5.0, 6.0, 7.0),
            App.Rotation(App.Vector(0.0, 0.0, 1.0), 90.0),
        )
        document.recompute()
        service = _Service(document)
        reference = reference_for_target(document, imported)

        summary_capture = capture_geometry_read(service, {"reference": reference})
        captured_hash = summary_capture["shape_hash"]
        imported.Shape = Part.makeSphere(2.0)
        document.recompute()
        summary = complete_geometry_read(summary_capture)
        assert summary["ok"] is True, summary
        assert summary["reference"] == reference
        assert summary["object"]["name"] == "ImportedMotor"
        assert summary["object"]["label"] == "Imported STEP Motor"
        assert summary["object"]["type"] == "Part::Feature"
        assert summary["shape_revision"]["shape_hash"] == captured_hash
        assert summary["placement"]["translation_mm"] == [5.0, 6.0, 7.0]
        facts = summary["geometry"]
        assert facts["solids"] == 1
        assert facts["faces"] == 6
        assert facts["edges"] == 12
        assert facts["volume_mm3"] == 6000.0
        assert sorted(round(value, 6) for value in facts["bounds_mm"]["size"]) == [
            10.0,
            20.0,
            30.0,
        ]
        assert facts["face_details"] == []
        assert facts["edge_details"] == []
        assert summary["execution"]["mode"] == "isolated_geometry_worker"

        imported.Shape = Part.makeBox(10.0, 20.0, 30.0)
        document.recompute()
        topology_only = complete_geometry_read(
            capture_geometry_read(
                service,
                {
                    "reference": reference,
                    "analysis_level": "topology",
                },
            )
        )
        topology_facts = topology_only["geometry"]
        assert topology_facts["analysis_level"] == "topology"
        assert topology_facts["faces"] == 6
        assert topology_facts["bounds_mm"]["size"] == [10.0, 20.0, 30.0]
        assert "valid" not in topology_facts
        assert "volume_mm3" not in topology_facts

        imported.Shape = Part.makeBox(10.0, 20.0, 30.0)
        document.recompute()
        detailed = complete_geometry_read(
            capture_geometry_read(
                service,
                {
                    "reference": reference,
                    "include_subelements": True,
                    "max_subelements": 3,
                },
            )
        )
        assert len(detailed["geometry"]["face_details"]) == 3
        assert len(detailed["geometry"]["edge_details"]) == 3
        assert detailed["geometry"]["subelement_details_truncated"] is True
        assert detailed["subelement_selectors"]["index_base"] == 1
        assert detailed["subelement_selectors"]["shape_hash"] == detailed[
            "shape_revision"
        ]["shape_hash"]

        imported.Shape = Part.makeCylinder(4.0, 12.0)
        document.recompute()
        queried = complete_geometry_read(
            capture_geometry_read(
                service,
                {
                    "reference": reference,
                    "queries": [
                        {
                            "name": "top_face",
                            "element_type": "face",
                            "geometry_type": "Plane",
                            "normal": [0.0, 0.0, 1.0],
                            "expected_count": 1,
                        },
                        {
                            "name": "shaft_surface",
                            "element_type": "face",
                            "geometry_type": "Cylinder",
                            "axis_direction": [0.0, 0.0, 1.0],
                            "radius_mm": 4.0,
                            "expected_count": 1,
                        },
                        {
                            "name": "rim_edges",
                            "element_type": "edge",
                            "geometry_type": "Circle",
                            "axis_direction": [0.0, 0.0, 1.0],
                            "radius_mm": 4.0,
                            "expected_count": 2,
                        },
                    ],
                },
            )
        )
        query_results = {
            result["name"]: result
            for result in queried["geometry"]["query_results"]
        }
        assert query_results["top_face"]["cardinality_ok"] is True
        assert query_results["shaft_surface"]["cardinality_ok"] is True
        assert query_results["rim_edges"]["cardinality_ok"] is True
        top = query_results["top_face"]["matches"][0]
        assert top["sketch_placement"] == {
            "origin": [float(value) for value in top["origin_mm"]],
            "normal": [float(value) for value in top["normal"]],
            "x_direction": [float(value) for value in top["x_direction"]],
        }
        shaft = query_results["shaft_surface"]["matches"][0]
        assert shaft["radius_mm"] == 4.0
        assert shaft["axis_direction"] == [0.0, 0.0, 1.0]
        assert shaft["axis_placement"] == {
            "origin": [float(value) for value in shaft["origin_mm"]],
            "axis_direction": [0.0, 0.0, 1.0],
            "x_direction": [float(value) for value in shaft["x_direction"]],
        }
        assert shaft["source_selector"]["geometry_type"] == "Cylinder"
        assert shaft["source_selector"]["expected_count"] == 1

        imported.Shape = Part.makeBox(10.0, 20.0, 30.0)
        document.recompute()
        routed = _run_universal_vibescript_tool(
            service,
            "PartDesignWorkbench",
            "vibescript.read_geometry",
            {
                "reference": reference,
                "include_subelements": True,
                "max_subelements": 2,
            },
            document_thread_dispatch=None,
            cancellation_check=None,
            progress_callback=None,
        )
        assert routed["ok"] is True, routed
        assert routed["tool"] == "vibescript.read_geometry"
        assert routed["geometry"]["volume_mm3"] == 6000.0
        assert len(routed["geometry"]["face_details"]) == 2

        unsupported = document.addObject("App::FeaturePython", "NoGeometry")
        try:
            capture_geometry_read(
                service,
                {"reference": reference_for_target(document, unsupported)},
            )
        except GeometryInspectionError as exc:
            assert exc.code == "GEOMETRY_TYPE_UNSUPPORTED"
        else:
            raise AssertionError("An object without Shape was accepted.")

        print(
            json.dumps(
                {
                    "ok": True,
                    "integration": "read_geometry",
                    "summary_detached_from_document": True,
                    "subelements_bounded": True,
                    "inspection_process_isolated": True,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        App.closeDocument(document.Name)


if __name__ == "__main__":
    raise SystemExit(main())
