# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI integration coverage for Part Design VibeScript presentation state."""

from pathlib import Path
import shutil
import tempfile
import unittest

import FreeCAD as App
import Materials

from VibeCADModelingSurface import resolve_modeling_surface
from VibeCADScriptedPublication import publication_target
from VibeCADVibeScriptDomains import get_vibescript_pack
from VibeCADVibeScriptDomainPublication import (
    PROP_PARTDESIGN_APPEARANCE_BASELINE,
    PROP_PARTDESIGN_MATERIAL_BASELINE,
    PROP_PARTDESIGN_PRESENTATION_STATE,
    _material_card_state,
    _shape_appearance_sha256,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (
    accept_candidate,
    execute_candidate,
    prepare_candidate,
    retain_candidate,
    validate_candidate,
)


class _Service:
    def __init__(self, document, project_root: Path) -> None:
        self.document = document
        self.project_root = project_root

    def _active_document(self):
        return self.document

    @staticmethod
    def active_workbench_name() -> str:
        return "PartDesignWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "partdesign-presentation-integration-revision"

    def project_scope_snapshot(self) -> dict:
        return {"root": str(self.project_root)}

    def provider_working_set(self) -> dict:
        publications = [
            obj
            for obj in self.document.Objects
            if "VibeCADScriptedOutputKey" in list(obj.PropertiesList)
        ]
        return {
            "target_count": len(publications),
            "targets": [
                {
                    "name": str(obj.Name),
                    "label": str(obj.Label),
                    "type_id": str(obj.TypeId),
                }
                for obj in publications
            ],
        }

    @staticmethod
    def selection_summary() -> dict:
        return {"selection": []}

    @staticmethod
    def _partdesign_body_for_feature(_obj):
        return None


def _capture(base: dict, *, operation: str, arguments: dict) -> dict:
    return {
        **base,
        "operation": operation,
        "tool_name": f"vibescript.partdesign.{operation}",
        "arguments": arguments,
    }


def _run_candidate(captured: dict, service: _Service):
    prepared = prepare_candidate(captured)
    execution = execute_candidate(prepared, cancellation_check=None)
    if not execution.get("ok"):
        raise AssertionError(execution)
    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    return prepared, publication, accepted


def _source() -> str:
    return (
        "cover = api.box(inputs['cover_size'], inputs['cover_size'], 1, "
        "label='Leather Cover Geometry')\n"
        "left = api.cylinder(0.2, 4, origin=[2,2,1])\n"
        "right = api.cylinder(0.2, 4, origin=[4,2,1])\n"
        "stitches = api.compound([left,right], label='Double Stitches Geometry')\n"
        "card = api.material(inputs['material_uuid']) if "
        "inputs['material_enabled'] else None\n"
        "cover_style = api.appearance(color_rgb=inputs['cover_color'], "
        "line_color_rgb=[0,128,255], point_color_rgb=[0,255,128], "
        "transparency_percent=7, line_width=2.5, point_size=3.5, "
        "display_mode='Flat Lines', visible=True, selectable=False) if "
        "inputs['style_enabled'] else None\n"
        "stitch_style = api.appearance(color_rgb=inputs['stitch_color']) if "
        "inputs['style_enabled'] else None\n"
        "result = {\n"
        " 'LeatherCover': api.publish(cover, material=card, "
        "appearance=cover_style, label='Leather Cover'),\n"
        " 'DoubleStitches': api.publish(stitches, appearance=stitch_style, "
        "label='108 Double Stitches'),\n"
        "}\n"
    )


def _schema(material_uuid: str) -> dict:
    color = {
        "type": "array",
        "items": {"type": "integer", "minimum": 0, "maximum": 255},
        "minItems": 3,
        "maxItems": 3,
    }
    return {
        "type": "object",
        "properties": {
            "cover_size": {"type": "number", "exclusiveMinimum": 0},
            "cover_color": color,
            "stitch_color": color,
            "style_enabled": {"type": "boolean"},
            "material_enabled": {"type": "boolean"},
            "material_uuid": {"type": "string", "enum": [material_uuid]},
        },
        "required": [
            "cover_size",
            "cover_color",
            "stitch_color",
            "style_enabled",
            "material_enabled",
            "material_uuid",
        ],
        "additionalProperties": False,
    }


def _color(view) -> tuple[float, float, float]:
    materials = list(view.ShapeAppearance)
    if not materials:
        raise AssertionError("The native view has no ShapeAppearance material.")
    return tuple(float(value) for value in tuple(materials[0].DiffuseColor)[:3])


def _transparency(view) -> float:
    materials = list(view.ShapeAppearance)
    if not materials:
        raise AssertionError("The native view has no ShapeAppearance material.")
    return float(materials[0].Transparency) * 100.0


def _assert_color(
    testcase: unittest.TestCase,
    observed,
    expected,
) -> None:
    testcase.assertEqual(len(observed), len(expected))
    for actual, wanted in zip(observed, expected):
        testcase.assertAlmostEqual(float(actual), float(wanted), places=5)


class TestVibeScriptPresentation(unittest.TestCase):
    def testPartDesignMaterialAndColorRegenerateAndPersist(self):
        cards = sorted(
            list(Materials.MaterialManager().Materials.values()),
            key=lambda card: (str(card.Name), str(card.UUID)),
        )
        self.assertTrue(cards)
        card = cards[0]
        root = Path(tempfile.mkdtemp(prefix="vibecad-partdesign-presentation-"))
        document = App.newDocument("PartDesignVibeScriptPresentation")
        service = _Service(document, root)
        pack = get_vibescript_pack("PartDesignWorkbench")
        self.assertIsNotNone(pack)
        base_capture = {
            "pack": pack,
            "project_root": str(root),
            "document_name": str(document.Name),
            "document_uid": str(document.Uid),
            "document_revision": service.provider_document_revision(),
            "document_objects": [],
            "surface": resolve_modeling_surface(
                "PartDesignWorkbench",
                "vibescript",
            ).summary(),
            "freecad_home": str(Path(App.getHomePath()).resolve()),
            "timeout_seconds": 60.0,
            "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
        }
        initial_inputs = {
            "cover_size": 10.0,
            "cover_color": [255, 255, 255],
            "stitch_color": [255, 0, 0],
            "style_enabled": False,
            "material_enabled": False,
            "material_uuid": str(card.UUID),
        }
        try:
            create = _capture(
                base_capture,
                operation="create_program",
                arguments={
                    "program_name": "Colored Baseball Components",
                    "source": _source(),
                    "input_schema": _schema(str(card.UUID)),
                    "inputs": initial_inputs,
                    "expected_outputs": [
                        {"name": "LeatherCover", "type": "solid"},
                        {"name": "DoubleStitches", "type": "compound"},
                    ],
                },
            )
            prepared, _publication, accepted = _run_candidate(create, service)
            cover_name = accepted["live_outputs"]["LeatherCover"]["object_name"]
            stitch_name = accepted["live_outputs"]["DoubleStitches"]["object_name"]
            cover = document.getObject(cover_name)
            stitches = document.getObject(stitch_name)
            self.assertIsNotNone(cover)
            self.assertIsNotNone(stitches)
            baseline_color = _color(cover.ViewObject)
            baseline_override = bool(cover.ViewObject.OverrideMaterial)
            baseline_material = _material_card_state(cover.ShapeMaterial)
            baseline_volume = float(cover.Shape.Volume)
            implementation = publication_target(cover)
            baseline_line_color = tuple(implementation.ViewObject.LineColor)
            baseline_point_color = tuple(implementation.ViewObject.PointColor)
            baseline_display_mode = str(implementation.ViewObject.DisplayMode)
            baseline_line_width = float(cover.ViewObject.LineWidth)
            baseline_point_size = float(cover.ViewObject.PointSize)
            baseline_selectable = bool(cover.ViewObject.Selectable)

            enable = _capture(
                base_capture,
                operation="set_inputs",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": accepted["working_revision"],
                    "patch": {
                        "cover_size": 12.0,
                        "style_enabled": True,
                        "material_enabled": True,
                    },
                },
            )
            _enabled_prepared, _enabled_publication, enabled = _run_candidate(
                enable,
                service,
            )
            cover = document.getObject(cover_name)
            stitches = document.getObject(stitch_name)
            self.assertEqual(
                enabled["live_outputs"]["LeatherCover"]["object_name"],
                cover_name,
            )
            self.assertEqual(
                enabled["live_outputs"]["DoubleStitches"]["object_name"],
                stitch_name,
            )
            self.assertGreater(float(cover.Shape.Volume), baseline_volume)
            self.assertEqual(_color(cover.ViewObject), (1.0, 1.0, 1.0))
            self.assertEqual(_color(stitches.ViewObject), (1.0, 0.0, 0.0))
            self.assertAlmostEqual(_transparency(cover.ViewObject), 7.0, places=5)
            self.assertTrue(bool(cover.ViewObject.OverrideMaterial))
            _assert_color(
                self,
                tuple(implementation.ViewObject.LineColor)[:3],
                (0.0, 128.0 / 255.0, 1.0),
            )
            _assert_color(
                self,
                tuple(implementation.ViewObject.PointColor)[:3],
                (0.0, 1.0, 128.0 / 255.0),
            )
            self.assertAlmostEqual(float(cover.ViewObject.LineWidth), 2.5)
            self.assertAlmostEqual(float(cover.ViewObject.PointSize), 3.5)
            self.assertEqual(
                str(implementation.ViewObject.DisplayMode),
                "Flat Lines",
            )
            self.assertFalse(bool(cover.ViewObject.Selectable))
            self.assertEqual(str(cover.ShapeMaterial.UUID), str(card.UUID))
            self.assertIn(PROP_PARTDESIGN_PRESENTATION_STATE, cover.PropertiesList)
            self.assertTrue(
                list(getattr(cover, PROP_PARTDESIGN_APPEARANCE_BASELINE))
            )

            disable = _capture(
                base_capture,
                operation="set_inputs",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": enabled["working_revision"],
                    "patch": {
                        "style_enabled": False,
                        "material_enabled": False,
                    },
                },
            )
            _disabled_prepared, _disabled_publication, disabled = _run_candidate(
                disable,
                service,
            )
            cover = document.getObject(cover_name)
            stitches = document.getObject(stitch_name)
            self.assertEqual(_color(cover.ViewObject), baseline_color)
            self.assertEqual(
                bool(cover.ViewObject.OverrideMaterial),
                baseline_override,
            )
            self.assertEqual(
                _material_card_state(cover.ShapeMaterial),
                baseline_material,
            )
            implementation = publication_target(cover)
            self.assertEqual(
                tuple(implementation.ViewObject.LineColor),
                baseline_line_color,
            )
            self.assertEqual(
                tuple(implementation.ViewObject.PointColor),
                baseline_point_color,
            )
            self.assertEqual(
                str(implementation.ViewObject.DisplayMode),
                baseline_display_mode,
            )
            self.assertEqual(float(cover.ViewObject.LineWidth), baseline_line_width)
            self.assertEqual(float(cover.ViewObject.PointSize), baseline_point_size)
            self.assertEqual(bool(cover.ViewObject.Selectable), baseline_selectable)
            self.assertEqual(
                _shape_appearance_sha256(cover.ViewObject.ShapeAppearance),
                _shape_appearance_sha256(
                    getattr(cover, PROP_PARTDESIGN_APPEARANCE_BASELINE)
                ),
            )
            self.assertEqual(
                _material_card_state(cover.ShapeMaterial),
                _material_card_state(
                    getattr(cover, PROP_PARTDESIGN_MATERIAL_BASELINE)
                ),
            )

            reenable = _capture(
                base_capture,
                operation="set_inputs",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": disabled["working_revision"],
                    "patch": {
                        "style_enabled": True,
                        "material_enabled": True,
                    },
                },
            )
            _final_prepared, _final_publication, final = _run_candidate(
                reenable,
                service,
            )
            self.assertEqual(
                final["live_outputs"]["LeatherCover"]["object_name"],
                cover_name,
            )
            save_path = root / "presentation.FCStd"
            document.recompute()
            document.saveAs(str(save_path))
            App.closeDocument(document.Name)
            document = App.openDocument(str(save_path))
            cover = document.getObject(cover_name)
            stitches = document.getObject(stitch_name)
            self.assertEqual(_color(cover.ViewObject), (1.0, 1.0, 1.0))
            self.assertEqual(_color(stitches.ViewObject), (1.0, 0.0, 0.0))
            self.assertAlmostEqual(_transparency(cover.ViewObject), 7.0, places=5)
            self.assertTrue(bool(cover.ViewObject.OverrideMaterial))
            implementation = publication_target(cover)
            _assert_color(
                self,
                tuple(implementation.ViewObject.LineColor)[:3],
                (0.0, 128.0 / 255.0, 1.0),
            )
            _assert_color(
                self,
                tuple(implementation.ViewObject.PointColor)[:3],
                (0.0, 1.0, 128.0 / 255.0),
            )
            self.assertEqual(
                str(implementation.ViewObject.DisplayMode),
                "Flat Lines",
            )
            self.assertEqual(str(cover.ShapeMaterial.UUID), str(card.UUID))
            self.assertIn(PROP_PARTDESIGN_PRESENTATION_STATE, cover.PropertiesList)

            service.document = document
            accepted_volume = float(cover.Shape.Volume)
            cover.ViewObject.LineWidth = 4.5
            conflicting_update = _capture(
                {
                    **base_capture,
                    "document_name": str(document.Name),
                    "document_uid": str(document.Uid),
                },
                operation="set_inputs",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": final["working_revision"],
                    "patch": {"cover_size": 14.0},
                },
            )
            conflict_prepared = prepare_candidate(conflicting_update)
            conflict_execution = execute_candidate(
                conflict_prepared,
                cancellation_check=None,
            )
            self.assertTrue(conflict_execution.get("ok"), conflict_execution)
            conflict_validated = validate_candidate(
                conflict_prepared,
                conflict_execution,
            )
            retain_candidate(conflict_prepared, status="validated")
            with self.assertRaisesRegex(
                RuntimeError,
                "controlled display properties",
            ):
                publish_candidate(
                    service,
                    conflict_prepared,
                    conflict_validated,
                )
            self.assertEqual(float(cover.Shape.Volume), accepted_volume)
        finally:
            if document is not None:
                try:
                    live_document = App.getDocument(document.Name)
                except NameError:
                    live_document = None
                if live_document is not None:
                    App.closeDocument(document.Name)
            shutil.rmtree(root, ignore_errors=True)
