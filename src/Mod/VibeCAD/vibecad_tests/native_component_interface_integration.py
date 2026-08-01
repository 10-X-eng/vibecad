# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD gate for explicit component-interface publication."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile

import FreeCAD as App
import Part
import PartDesign  # noqa: F401

MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from VibeCADComponentCatalog import (  # noqa: E402
    capture_component_catalog,
    prepare_captured_component_catalog,
)
from VibeCADDocumentReferences import reference_for_target  # noqa: E402
from VibeCADReferenceContracts import native_interface_definitions  # noqa: E402
from tool_impl.service.component_publish_interface import run as publish_interface  # noqa: E402


class _Service:
    def __init__(self, document):
        self.document = document

    def _active_document(self):
        return self.document


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="vibecad-native-interface-"))
    document = App.newDocument("NativeInterfaceContract")
    try:
        body = document.addObject("PartDesign::Body", "Shaft")
        feature = body.newObject("PartDesign::Feature", "ShaftSolid")
        feature.Shape = Part.makeCylinder(5.0, 20.0)
        lcs = body.newObject("PartDesign::CoordinateSystem", "ShaftAxisLCS")
        lcs.Placement = App.Placement(
            App.Vector(0.0, 0.0, 10.0),
            App.Rotation(),
        )
        document.recompute()
        service = _Service(document)
        result = publish_interface(
            service,
            reference_for_target(document, body),
            reference_for_target(document, lcs),
            "RotationAxis",
            "axis",
            ["revolute", "fixed"],
            "shaft-v1",
        )
        assert result["ok"] is True, result
        definition = native_interface_definitions(body)["RotationAxis"]
        assert definition["connector"] == {
            "kind": "axis",
            "allowed_joints": ["revolute", "fixed"],
            "compatibility": "shaft-v1",
        }
        assert definition["resolved"]["connector_frame"]["origin_mm"] == [
            0.0,
            0.0,
            10.0,
        ]

        captured = capture_component_catalog(service)
        candidate = next(
            item for item in captured["open_candidates"] if item["object_name"] == body.Name
        )
        assert candidate["published_interfaces"] == ["RotationAxis"]
        assert candidate["interfaces"][0]["connector"] == definition["connector"]
        assert candidate["local_coordinate_systems"] == [
            {
                "object_name": lcs.Name,
                "label": lcs.Label,
                "reference": reference_for_target(document, lcs),
                "published_interface": "RotationAxis",
            }
        ]

        body_name = str(body.Name)
        path = root / "native-interface.FCStd"
        document.saveAs(str(path))
        App.closeDocument(document.Name)
        saved_catalog = prepare_captured_component_catalog(
            {
                "owner_document_uid": "assembly-owner",
                "project_directory": str(root),
                "owner_file": "",
                "open_document_files": [],
                "open_candidates": [],
            }
        )
        saved_candidate = next(
            item
            for item in saved_catalog["candidates"]
            if item["object_name"] == body_name
        )
        assert saved_candidate["published_interfaces"] == ["RotationAxis"]
        assert saved_candidate["interfaces"][0]["connector"] == definition["connector"]
        assert (
            saved_candidate["interfaces"][0]["frame"]
            == definition["resolved"]["connector_frame"]
        )
        assert saved_candidate["local_coordinate_systems"][0][
            "published_interface"
        ] == "RotationAxis"
        document = App.openDocument(str(path))
        reopened_body = document.getObject(body_name)
        reopened = native_interface_definitions(reopened_body)
        assert reopened["RotationAxis"]["connector"] == definition["connector"]
        assert (
            reopened["RotationAxis"]["resolved"]["connector_frame"]
            == definition["resolved"]["connector_frame"]
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "integration": "native_component_interface",
                    "component": reopened_body.Name,
                    "interface": "RotationAxis",
                    "save_reopen_preserved": True,
                    "catalog_discovery": True,
                    "closed_document_catalog_discovery": True,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        if document is not None and App.getDocument(document.Name) is document:
            App.closeDocument(document.Name)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
