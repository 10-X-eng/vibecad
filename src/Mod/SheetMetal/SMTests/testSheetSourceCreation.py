# SPDX-License-Identifier: LGPL-2.1-or-later
"""Owned source creation retains editable parameters, exact inputs and History."""

import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui, QtWidgets

from SMTests import testSheetNativeEdit


class TestSheetSourceCreation(unittest.TestCase):
    def setUp(self):
        self.fixture = testSheetNativeEdit.TestSheetNativeEdit()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.context, self.model = self.fixture.context, self.fixture.model
        self.doc = self.model.doc

    def arguments(self, **changes):
        return {"operation": "base_shape", "shape_type": "L-Shape", "thickness": 1.6,
                "bend_radius": 2, "width": 50, "length": 70, "height": 25,
                "flange_width": 8, "origin": "0,0", "fill_gaps": True, **changes}

    def prepare(self, arguments=None):
        import SheetMetalSourceOperations as Sources
        return Sources.prepare(self.doc, self.arguments() if arguments is None else arguments,
                               expected_revision=Sources.capture_revision(self.doc))

    def start(self, prepared=None):
        import SheetMetalNativeEdit
        prepared = self.prepare() if prepared is None else prepared
        ticket = self.context.state.begin_call(self.doc.Uid, "sheet_metal.create")
        self.addCleanup(lambda: self.context.state.cancel_mutation(ticket))
        return ticket, SheetMetalNativeEdit.start_source_creation(self.context, ticket, prepared)

    def wait(self, future):
        return self.fixture.wait(future)

    def history(self, source):
        from SMTests import testSheetHistory
        helper = testSheetHistory.TestSheetHistory()
        helper.sheet, helper.model = source, self.model
        helper.fixture = self.fixture.fixture.fixture
        return helper

    def flange_input(self):
        _ticket, future = self.start(self.prepare(self.arguments(shape_type="Flat")))
        result = self.wait(future)
        self.assertEqual(result["phase"], "ready", result)
        source = self.doc.getObject(result["object_name"])
        face = next(f"Face{i}" for i, face in enumerate(source.Shape.Faces, 1)
                    if isinstance(face.Surface, Part.Plane) and face.normalAt(0, 0).x > .9)
        return source, {"operation": "add_flange", "object_name": source.Name,
            "subelements": [face], "length": 20, "bend_radius": 2,
            "bend_angle": 90, "invert": False, "bend_type": "Material Outside",
            "length_spec": "Leg"}

    def test_new_flange_keeps_parent_and_builds_editable_folded_flat_geometry(self):
        import SheetMetalSourceOperations as Sources
        import SheetMetalSourceFeatures as Features
        import SheetMetalOperations as Operations
        import SheetMetalEditable as Editable
        from SheetMetalCmd import SMBendWall

        source, arguments = self.flange_input()
        parent = source.Shape.exportBrepToString()
        undo = self.doc.UndoCount
        _ticket, future = self.start(self.prepare(arguments))
        self.assertFalse(future.done())
        self.assertFalse(self.doc.HasPendingTransaction)
        result = self.wait(future)
        self.assertEqual(result["phase"], "ready", result)
        flange = self.doc.getObject(result["object_name"])
        self.assertIsInstance(flange.Proxy, SMBendWall)
        self.assertEqual(flange.baseObject, (source, arguments["subelements"]))
        self.assertEqual(source.Shape.exportBrepToString(), parent)
        self.assertEqual(self.doc.UndoCount, undo+1)
        self.assertEqual(flange.VibeCADTimelineRole, "operation")
        self.assertEqual(flange.VibeCADTimelineEditCommand, "SheetMetal_EditSource")
        self.assertEqual(flange.ViewObject.Proxy.claimChildren(), [source])
        self.assertEqual(Sources.arguments(flange), arguments)
        self.assertEqual(result["source_geometry"]["thickness_mm"], 1.6)
        self.assertGreater(flange.Shape.BoundBox.ZLength, 15)
        face = result["source_geometry"]["reference_faces"][0]["name"]
        run = Operations.start_creation(flange, face,
            expected_revision=Operations.capture_source_revision(flange))
        ready = self.wait(run.future)
        self.assertEqual(ready["phase"], "ready", ready)
        sheet = self.doc.getObject(ready["object_name"])
        geometry = Editable.get_state_geometry(sheet)
        for shape in (geometry.folded, geometry.flat):
            self.assertTrue(shape.isValid())
            self.assertEqual(len(shape.Solids), 1)
        old_hash = sheet.PreparedInputHash
        run = Sources.update(flange, {"length": 25, "bend_angle": 60},
            expected_revision=Operations.capture_source_revision(flange))
        self.assertEqual(self.wait(run.future)["phase"], "ready")
        self.assertNotEqual(sheet.PreparedInputHash, old_hash)
        self.assertEqual(source.Shape.exportBrepToString(), parent)
        self.assertEqual(float(flange.length), 25)
        self.assertEqual(float(flange.angle), 60)
        self.assertEqual(Features.get_prepared_source(flange)["solid_count"], 1)
        self.assertTrue(Editable.get_state_geometry(sheet).flat.isValid())
        self.doc.undo()
        self.model.recompute()
        self.assertEqual(float(flange.length), 20)
        self.assertEqual(float(flange.angle), 90)
        self.assertEqual(sheet.PreparedInputHash, old_hash)

    def test_plain_solid_flange_retains_source_and_supports_shared_sheet_creation(self):
        import SheetMetalSourceOperations as Sources
        import SheetMetalOperations as Operations
        import SheetMetalEditable as Editable
        base, arguments = self.flange_input()

        def plain_source():
            source = self.doc.addObject("Part::Feature", "ImportedPlate")
            source.Shape = base.Shape.copy()
            return source

        source = self.model.edit(plain_source)
        self.assertFalse(hasattr(source, "Proxy"))
        original = source.Shape.exportBrepToString()
        arguments["object_name"] = source.Name
        request = Sources.prepare(self.doc, arguments, expected_revision=Sources.capture_revision(self.doc))
        run = Sources.start(request)
        result = self.wait(run.future)
        self.assertEqual(result["phase"], "ready", result)
        flange = self.doc.getObject(result["object_name"])
        face = max(((f"Face{i}", face) for i, face in enumerate(flange.Shape.Faces, 1)
                    if isinstance(face.Surface, Part.Plane)), key=lambda item: item[1].Area)[0]
        run = Operations.start_creation(flange, face,
            expected_revision=Operations.capture_source_revision(flange))
        result = self.wait(run.future)
        self.assertEqual(result["phase"], "ready", result)
        sheet = self.doc.getObject(result["object_name"])
        geometry = Editable.get_state_geometry(sheet)
        for shape in (geometry.folded, geometry.flat):
            self.assertTrue(shape.isValid())
            self.assertEqual(len(shape.Solids), 1)
        self.assertAlmostEqual(geometry.mapping.thickness, 1.6)
        self.assertIs(sheet.SourceFace[0], flange)
        self.assertIs(flange.baseObject[0], source)
        self.assertEqual(source.Shape.exportBrepToString(), original)

    def test_new_flange_rejects_invalid_angles_and_subobjects_without_history(self):
        source, arguments = self.flange_input()
        before = tuple(self.doc.Objects), self.doc.UndoCount, source.Shape.exportBrepToString()
        for changes in ({"bend_angle": 0}, {"bend_angle": 181}, {"length": 0},
                        {"subelements": ["Face99999"]}, {"subelements": []},
                        {"subelements": ["Vertex1"]}, {"invert": "false"}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.prepare({**arguments, **changes})
                self.assertEqual((tuple(self.doc.Objects), self.doc.UndoCount,
                                  source.Shape.exportBrepToString()), before)

    def test_native_flange_request_uses_the_registered_async_source_path(self):
        from VibeCADNativeSheetMetalCreateRuntime import NativeSheetMetalCreateRuntime
        source, arguments = self.flange_input()
        ticket = self.context.state.begin_call(self.doc.Uid, "sheet_metal.create")
        self.addCleanup(lambda: self.context.state.cancel_mutation(ticket))
        future = NativeSheetMetalCreateRuntime(self.context).execute_async(arguments, ticket=ticket)
        self.assertFalse(future.done())
        ready = self.wait(future)
        self.assertEqual(ready["phase"], "ready", ready)
        flange = self.doc.getObject(ready["object_name"])
        self.assertIs(flange.baseObject[0], source)
        self.assertTrue(flange.Shape.isValid())
        self.assertIsNotNone(self.context.state.completed_mutation_receipt(ticket))

    def test_flange_after_a_cut_preserves_history_and_reopens_with_editable_stock(self):
        import SheetMetalSourceFeatures as Features
        import SheetMetalSourceOperations as Sources
        import SheetMetalOperations as Operations
        import SheetMetalEditable as Editable
        import SheetMetalHistoryOperations as Shared

        source, arguments = self.flange_input()
        face = Features.get_prepared_source(source)["reference_faces"][0]["name"]
        run = Operations.start_creation(source, face,
            expected_revision=Operations.capture_source_revision(source))
        ready = self.wait(run.future)
        self.assertEqual(ready["phase"], "ready", ready)
        root = self.doc.getObject(ready["object_name"])
        center = Editable.get_state_geometry(root).flat_face.CenterOfMass
        cut_request = Shared.prepare(root, {"operation": "add_circle", "center": list(center), "radius": 3},
            expected_revision=Shared.capture_revision(root))
        run = Shared.start(cut_request)
        ready = self.wait(run.future)
        self.assertEqual(ready["phase"], "ready", ready)
        cut = self.doc.getObject(ready["object_name"])
        parent = (cut.Shape.exportBrepToString(), cut.FlatShape.exportBrepToString(),
                  cut.PreparedInputHash, cut.Operation)
        flange_face = next(f"Face{i}" for i, face in enumerate(cut.Shape.Faces, 1)
                          if isinstance(face.Surface, Part.Plane) and face.normalAt(0, 0).x > .9)
        arguments.update(object_name=cut.Name, subelements=[flange_face])
        _ticket, future = self.start(self.prepare(arguments))
        ready = self.wait(future)
        self.assertEqual(ready["phase"], "ready", ready)
        flange = self.doc.getObject(ready["object_name"])
        self.assertIs(flange.baseObject[0], cut)
        self.assertEqual((cut.Shape.exportBrepToString(), cut.FlatShape.exportBrepToString(),
                          cut.PreparedInputHash, cut.Operation), parent)
        face = ready["source_geometry"]["reference_faces"][0]["name"]
        run = Operations.start_creation(flange, face,
            expected_revision=Operations.capture_source_revision(flange))
        ready = self.wait(run.future)
        self.assertEqual(ready["phase"], "ready", ready)
        sheet = self.doc.getObject(ready["object_name"])
        self.assertTrue(Editable.get_state_geometry(sheet).flat.isValid())
        names = source.Name, root.Name, cut.Name, flange.Name, sheet.Name
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory)/"flange-after-cut.FCStd")
            self.doc.saveAs(filename)
            self.model.settle()
            App.closeDocument(self.doc.Name)
            self.doc = self.model.doc = App.openDocument(filename)
            self.model.settle()
            source, root, cut, flange, sheet = (self.doc.getObject(name) for name in names)
            self.assertIs(flange.baseObject[0], cut)
            self.assertIs(cut.BaseSheet, root)
            self.assertIs(sheet.SourceFace[0], flange)
            self.assertEqual(Sources.arguments(flange), arguments)
            # Recompute the retained feature chain after a real upstream stock
            # edit; do not replace saved solids or discard the circle history.
            self.model.edit(lambda: setattr(source, "thickness", 1.8))
            self.assertEqual(Features.get_prepared_source(flange)["thickness_mm"], 1.8)
            self.assertTrue(Editable.get_state_geometry(sheet).flat.isValid())
            self.assertEqual(float(cut.Radius), 3)
            self.assertEqual(float(flange.length), 20)

    def test_source_creation_has_one_owned_receipt_and_undoable_history(self):
        undo = self.doc.UndoCount
        ticket, future = self.start()
        self.assertFalse(future.done())
        self.assertFalse(self.doc.HasPendingTransaction)
        self.assertIsNone(self.context.state.completed_mutation_receipt(ticket))
        result = self.wait(future)
        obj = self.doc.getObject(result["object_name"])
        self.assertEqual(result["phase"], "ready")
        self.assertEqual(self.doc.UndoCount, undo+1)
        self.assertTrue(result["assistant_undo_available"])
        self.assertTrue(obj.Shape.isValid())
        self.assertEqual(float(obj.height), 25)
        self.assertEqual(obj.VibeCADTimelineRole, "operation")
        self.assertEqual(obj.VibeCADTimelineEditCommand, "SheetMetal_EditSource")
        self.assertEqual(obj.ViewObject.Proxy.claimChildren(), [])
        self.assertGreater(result["source_geometry"]["volume_mm3"], 0)
        self.context.undo_ledger.undo_latest(ticket=self.context.state.begin_call(self.doc.Uid, "native.undo"),
            document=self.doc, state=self.context.state, reauthorize_turn=self.context.guard,
            active_document=self.context.active_document)
        self.model.recompute()
        self.assertIsNone(self.doc.getObject(result["object_name"]))

    def test_omitted_return_flange_width_matches_ribbon_defaults(self):
        from SheetMetalSourceCreationGui import _DEFAULTS
        for shape in ("L-Shape", "Tub", "Hat"):
            with self.subTest(shape=shape):
                arguments = self.arguments(shape_type=shape)
                arguments.pop("flange_width")
                _, future = self.start(self.prepare(arguments))
                result = self.wait(future)
                source = self.doc.getObject(result["object_name"])
                self.assertEqual(float(source.flangeWidth), _DEFAULTS["base_shape"]["flange_width"])
                self.assertEqual(result["source_geometry"]["solid_count"], 1)
                self.assertTrue(source.Shape.isValid())
                _, future = self.start(self.prepare(self.arguments(shape_type=shape)))
                explicit = self.doc.getObject(self.wait(future)["object_name"])
                self.assertAlmostEqual(source.Shape.Volume, explicit.Shape.Volume)
                for axis in ("XLength", "YLength", "ZLength"):
                    self.assertAlmostEqual(getattr(source.Shape.BoundBox, axis),
                                           getattr(explicit.Shape.BoundBox, axis))

    def shared_base(self, kind="L-Shape", **dimensions):
        import SheetMetalNativeEdit
        import SheetMetalOperations as Operations
        _, future = self.start(self.prepare(self.arguments(shape_type=kind, **dimensions)))
        result = self.wait(future)
        source = self.doc.getObject(result["object_name"])
        face = result["source_geometry"]["reference_faces"][0]["name"]
        ticket = self.context.state.begin_call(self.doc.Uid, "sheet_metal.create")
        self.addCleanup(lambda: self.context.state.cancel_mutation(ticket))
        future = SheetMetalNativeEdit.start_creation(self.context, ticket, source, face,
            expected_revision=Operations.capture_source_revision(source))
        sheet = self.doc.getObject(self.wait(future)["object_name"])
        self.fixture.fixture.fixture.wait_for(lambda: sheet.ViewObject.Proxy.ready)
        return source, sheet

    def test_flat_native_height_edit_changes_flange_and_preserves_base_length(self):
        import SheetMetalEditable as Editable
        import SheetMetalHistoryOperations as Shared
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
        source, sheet = self.shared_base()
        Shared.switch(sheet, "flat")
        original_hash = sheet.PreparedInputHash
        original_volume = sheet.FlatShape.Volume
        before = self.doc.UndoCount
        ticket = self.context.state.begin_call(self.doc.Uid, "sheet_metal.edit")
        self.addCleanup(lambda: self.context.state.cancel_mutation(ticket))
        runtime = build_native_runtime_bindings(self.context, ("sheet_metal.edit",))["sheet_metal.edit"]
        future = build_native_capability_registry().implementation("sheet_metal.edit").async_handler(
            SimpleNamespace(runtime=runtime, ticket=ticket, arguments={"operation": "set_parameters",
                "object_name": sheet.Name, "changes": {"height": 35, "width": 60}}))
        self.assertFalse(future.done())
        self.assertFalse(self.doc.HasPendingTransaction)
        result = self.wait(future)
        self.assertEqual(result["phase"], "ready")
        self.assertEqual(self.doc.UndoCount, before+1)
        self.assertEqual(float(source.length), 70)
        self.assertEqual(float(source.height), 35)
        self.assertEqual(float(source.width), 60)
        self.assertAlmostEqual(sheet.Shape.BoundBox.ZLength, 35)
        self.assertAlmostEqual(sheet.Shape.BoundBox.YLength, 60)
        self.assertGreater(sheet.FlatShape.Volume, original_volume)
        self.assertNotEqual(sheet.PreparedInputHash, original_hash)
        self.assertTrue(Editable.get_prepared(sheet).flat.isValid())
        self.assertEqual(sheet.ViewObject.Proxy.mode, "flat")
        self.doc.undo()
        self.model.recompute()
        self.assertEqual(float(source.height), 25)
        self.assertAlmostEqual(sheet.FlatShape.Volume, original_volume)
        self.doc.redo()
        self.model.recompute()
        self.assertEqual(float(source.height), 35)
        self.assertAlmostEqual(sheet.Shape.BoundBox.ZLength, 35)

    def test_shared_base_panel_names_length_and_edits_height_without_extra_history(self):
        import SheetMetalGui
        source, sheet = self.shared_base()
        panel = SheetMetalGui.SheetPanel(sheet, "parameters", history=True)
        self.addCleanup(panel.close)
        self.assertEqual(panel.parameters["height"].value(), 25)
        self.assertEqual(panel.parameters["width"].value(), 50)
        self.assertNotIn("flange_width", panel.parameters)  # L has no return flange.
        self.assertEqual(panel.fields_layout.labelForField(panel.parameters["flange_length"]).text(),
                         "Base length (mm)")
        panel.parameters["height"].setValue(32)
        panel.apply()
        self.assertIsNotNone(panel.run, panel.message.text())
        self.assertEqual(self.wait(panel.run.future)["phase"], "ready")
        self.assertEqual(float(source.height), 32)
        self.assertEqual(float(source.length), 70)
        self.assertAlmostEqual(sheet.Shape.BoundBox.ZLength, 32)

    def test_base_dimension_inspection_and_invalid_requests_preserve_legacy_meanings(self):
        import SheetMetalHistoryOperations as Shared
        source, sheet = self.shared_base()
        parameters = Shared.inspect(sheet)["parameters"]
        self.assertEqual(parameters["height"]["property"], "height")
        self.assertEqual(parameters["width"]["property"], "width")
        self.assertEqual(parameters["flange_length"]["property"], "length")
        before = self.doc.UndoCount, sheet.PreparedInputHash
        for changes in ({"height": 0}, {"width": -1}, {"height": True}, {"flange_width": 10}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Shared.prepare(sheet, {"operation": "set_parameters", "changes": changes},
                               expected_revision=Shared.capture_revision(sheet))
        self.assertEqual((self.doc.UndoCount, sheet.PreparedInputHash), before)
        prepared = Shared.prepare(sheet, {"operation": "set_parameters", "changes": {"flange_length": 80}},
                                  expected_revision=Shared.capture_revision(sheet))
        run = Shared.start(prepared)
        self.assertEqual(self.wait(run.future)["phase"], "ready")
        self.assertEqual(float(source.length), 80)
        self.assertEqual(float(source.height), 25)

    def test_base_dimensions_match_shape_kind_and_return_flange_geometry(self):
        import SheetMetalEditable as Editable
        import SheetMetalHistoryOperations as Shared
        source, sheet = self.shared_base("Flat")
        parameters = Shared.inspect(sheet)["parameters"]
        self.assertIn("width", parameters)
        self.assertNotIn("height", parameters)
        self.assertNotIn("flange_width", parameters)
        with self.assertRaises(ValueError):
            Shared.prepare(sheet, {"operation": "set_parameters", "changes": {"height": 30}},
                           expected_revision=Shared.capture_revision(sheet))
        source, sheet = self.shared_base("Hat")
        parameters = Shared.inspect(sheet)["parameters"]
        self.assertEqual(parameters["flange_width"]["property"], "flangeWidth")
        volume = sheet.FlatShape.Volume
        prepared = Shared.prepare(sheet, {"operation": "set_parameters", "changes": {"flange_width": 10}},
                                  expected_revision=Shared.capture_revision(sheet))
        run = Shared.start(prepared)
        self.assertEqual(self.wait(run.future)["phase"], "ready")
        self.assertEqual(float(source.flangeWidth), 10)
        self.assertGreater(sheet.FlatShape.Volume, volume)
        self.assertTrue(Editable.get_prepared(sheet).folded.isValid())

    def test_shared_base_dimensions_survive_save_reopen(self):
        import SheetMetalEditable as Editable
        import SheetMetalHistoryOperations as Shared
        source, sheet = self.shared_base()
        prepared = Shared.prepare(sheet, {"operation": "set_parameters", "changes": {"height": 33}},
                                  expected_revision=Shared.capture_revision(sheet))
        run = Shared.start(prepared)
        self.assertEqual(self.wait(run.future)["phase"], "ready")
        source_name, sheet_name = source.Name, sheet.Name
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory)/"shared-base-dimensions.FCStd")
            self.doc.saveAs(filename)
            self.model.settle()
            App.closeDocument(self.doc.Name)
            self.doc = self.model.doc = App.openDocument(filename)
            self.model.recompute()
            source, sheet = self.doc.getObject(source_name), self.doc.getObject(sheet_name)
            self.assertIs(sheet.SourceFace[0], source)
            self.assertEqual(Shared.inspect(sheet)["parameters"]["height"]["value"], 33)
            self.assertAlmostEqual(sheet.Shape.BoundBox.ZLength, 33)
            self.assertTrue(sheet.FlatShape.isValid())
            self.assertEqual(sheet.VibeCADTimelineEditCommand, "SheetMetal_EditParameters")
            # Restored solids retain their dimensions. The next actual edit
            # rebuilds transient mapping through the same asynchronous path.
            prepared = Shared.prepare(sheet, {"operation": "set_parameters", "changes": {"height": 34}},
                                      expected_revision=Shared.capture_revision(sheet))
            run = Shared.start(prepared)
            self.assertEqual(self.wait(run.future)["phase"], "ready")
            self.assertAlmostEqual(sheet.Shape.BoundBox.ZLength, 34)
            self.assertTrue(Editable.get_prepared(sheet).flat.isValid())

    def test_native_assembly_places_a_linked_sheet_and_keeps_its_editable_history(self):
        import json
        from dataclasses import replace
        import SheetMetalEditable as Editable
        import SheetMetalHistoryOperations as Shared
        from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
        from VibeCADNativeProviderContext import provider_authorized_native_surface
        from VibeCADNativeDispatch import NativeTurnDispatcher
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
        from VibeCADNativeTurn import NativeTurnSnapshot
        from VibeCADRibbonSurface import read_active_ribbon_surface

        source, sheet = self.shared_base()
        center = Editable.get_prepared(sheet).flat_face.CenterOfMass
        prepared = Shared.prepare(sheet, {"operation": "add_circle", "center": list(center), "radius": 3},
                                  expected_revision=Shared.capture_revision(sheet))
        run = Shared.start(prepared)
        hole = self.doc.getObject(self.wait(run.future)["object_name"])
        self.fixture.fixture.fixture.wait_for(lambda: hole.ViewObject.Proxy.ready)
        definition = sheet.Definition
        before_hash = hole.PreparedInputHash
        before_volume = hole.Shape.Volume
        source_history = (sheet.VibeCADTimelineEditCommand, hole.VibeCADTimelineEditCommand)
        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("AssemblyWorkbench")
        self.fixture.fixture.fixture.wait_for(lambda: read_active_ribbon_surface().surface_id == "assemble")
        self.addCleanup(lambda: Gui.activeDocument().resetEdit())
        registry = build_native_capability_registry()
        provider = provider_authorized_native_surface(
            resolve_native_provider_surface(read_active_ribbon_surface(), registry))
        self.assertTrue(provider.available, provider.debug_summary())
        context = replace(self.context,
            active_surface_id=lambda: read_active_ribbon_surface().surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()))
        turn = NativeTurnSnapshot.from_provider_surface(provider)
        dispatcher = NativeTurnDispatcher(document=self.doc, state=context.state, registry=registry,
            turn=turn, runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=context.guard, active_document=context.active_document)
        def call(name, arguments, call_id):
            result = dispatcher.call(name, json.dumps(arguments), call_id)
            self.assertTrue(result.get("ok"), result)
            return result
        result = call("assembly.create", {"label": "Sheet cabinet assembly"}, "create-sheet-assembly")
        assembly = self.doc.getObject(result["assembly"]["object_name"])
        result = call("assembly.insert", {
            "assembly": {"object_name": assembly.Name},
            "source": {"document_name": self.doc.Name, "object_name": hole.Name},
            "placement": {"origin_mm": {"x": 15, "y": 20, "z": 50},
                          "rotation": {"axis": {"x": 0, "y": 0, "z": 1}, "angle_degrees": 90}},
        }, "insert-sheet-component")
        occurrence = next(obj for obj in assembly.Group
                          if obj.TypeId == "App::Link" and obj.LinkedObject is hole)
        self.assertEqual(list(occurrence.Placement.Base), [15, 20, 50])
        rotated = occurrence.Placement.Rotation.multVec(App.Vector(1, 0, 0))
        self.assertAlmostEqual(rotated.y, 1)
        self.assertAlmostEqual(occurrence.Shape.Volume, before_volume)
        self.assertEqual(hole.PreparedInputHash, before_hash)
        self.assertEqual(sheet.Definition, definition)
        self.assertEqual((sheet.VibeCADTimelineEditCommand, hole.VibeCADTimelineEditCommand), source_history)
        self.assertIs(hole.BaseSheet, sheet)
        self.assertIs(sheet.SourceFace[0], source)
        # Editing the original shared chain must update the occurrence without
        # replacing the component or losing its placement and cut History.
        Gui.activeDocument().resetEdit()
        Gui.activateWorkbench("SMWorkbench")
        prepared = Shared.prepare(hole, {"operation": "set_parameters", "changes": {"height": 35}},
                                  expected_revision=Shared.capture_revision(hole))
        run = Shared.start(prepared)
        self.assertEqual(self.wait(run.future)["phase"], "ready")
        self.assertIs(occurrence.LinkedObject, hole)
        self.assertEqual(list(occurrence.Placement.Base), [15, 20, 50])
        self.assertGreater(occurrence.Shape.Volume, before_volume)
        self.assertTrue(Editable.get_state_geometry(hole).flat.isValid())

    def test_reopened_base_shape_prepares_its_cut_chain_without_changing_saved_inputs(self):
        self._check_reopened_base_preparation("L-Shape")

    def test_reopened_u_shape_prepares_its_cut_chain_without_changing_saved_inputs(self):
        self._check_reopened_base_preparation("U-Shape")

    def test_reopened_tub_prepares_its_cut_chain_without_changing_saved_inputs(self):
        self._check_reopened_base_preparation("Tub")

    def test_reopened_flat_prepares_its_cut_chain_without_changing_saved_inputs(self):
        self._check_reopened_base_preparation("Flat")

    def test_reopened_hat_prepares_its_cut_chain_without_changing_saved_inputs(self):
        self._check_reopened_base_preparation("Hat")

    def test_reopened_box_prepares_its_cut_chain_without_changing_saved_inputs(self):
        self._check_reopened_base_preparation("Box")

    def test_reopened_cabinet_sized_box_prepares_its_cut_chain(self):
        self._check_reopened_base_preparation("Box", width=400, length=500, height=600,
                                            thickness=1.2, bend_radius=1.5)

    def _check_reopened_base_preparation(self, kind, **dimensions):
        import SheetMetalEditable as Editable
        import SheetMetalHistoryOperations as Shared
        import SheetMetalPreparation as Preparation

        source, sheet = self.shared_base(kind, **dimensions)
        original_brep = tuple(Editable._persistent_brep(shape)
                              for shape in (sheet.Shape, sheet.FlatShape))
        center = Editable.get_prepared(sheet).flat_face.CenterOfMass
        request = Shared.prepare(sheet, {"operation": "add_circle", "center": list(center), "radius": 3},
                                 expected_revision=Shared.capture_revision(sheet))
        run = Shared.start(request)
        result = self.wait(run.future)
        self.assertEqual(result["phase"], "ready")
        self.assertEqual(tuple(Editable._persistent_brep(shape)
                               for shape in (sheet.Shape, sheet.FlatShape)), original_brep)
        hole = self.doc.getObject(result["object_name"])
        names = source.Name, sheet.Name, hole.Name
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory)/"reopened-base-cut.FCStd")
            self.doc.saveAs(filename)
            self.model.settle()
            App.closeDocument(self.doc.Name)
            self.doc = self.model.doc = App.openDocument(filename)
            self.model.settle()
            source, sheet, hole = (self.doc.getObject(name) for name in names)
            self.assertIsNone(sheet.Proxy._geometry)
            before = (Shared.capture_revision(hole), self.doc.UndoCount, self.doc.isTouched(),
                      source.Shape.exportBrepToString(True), sheet.PreparedInputHash,
                      hole.PreparedInputHash)
            payload = Preparation._payload
            def displaced_snapshot(observed):
                root_inputs, cuts, seed = payload(observed)
                inputs, saved_source, expected = root_inputs
                # Same topology and volume, but genuinely displaced inputs:
                # the intact saved root must not conceal this mismatch.
                inputs[0].translate(App.Vector(0.0001, 0, 0))
                return (inputs, saved_source, expected), cuts, seed
            with patch.object(Preparation, "_payload", side_effect=displaced_snapshot):
                run = Preparation.start_preparation(hole, expected_revision=Shared.capture_revision(hole))
                with self.assertRaisesRegex(RuntimeError, "fingerprint"):
                    self.wait(run.future)
            self.assertIsNone(sheet.Proxy._geometry)
            self.assertIsNone(hole.Proxy._geometry)
            run = Preparation.start_preparation(hole, expected_revision=Shared.capture_revision(hole))
            self.wait(run.future)
            self.assertEqual((Shared.capture_revision(hole), self.doc.UndoCount, self.doc.isTouched(),
                              source.Shape.exportBrepToString(True), sheet.PreparedInputHash,
                              hole.PreparedInputHash), before)
            self.assertIs(hole.BaseSheet, sheet)
            self.assertTrue(Editable.get_state_geometry(hole).folded.isValid())
            self.assertIs(Editable.get_state_geometry(hole).mapping,
                          Editable.get_state_geometry(sheet).mapping)
            request = Shared.prepare(hole, {"operation": "update_circle",
                "operation_id": hole.OperationId, "radius": 4},
                expected_revision=Shared.capture_revision(hole))
            run = Shared.start(request)
            self.assertEqual(self.wait(run.future)["phase"], "ready")
            self.assertEqual(self.doc.UndoCount, before[1]+1)
            self.assertEqual(float(hole.Radius), 4)
            self.assertTrue(Editable.get_state_geometry(hole).flat.isValid())

    def test_sketch_source_keeps_its_body_and_exact_child(self):
        def sketch_source():
            body = self.doc.addObject("PartDesign::Body", "SheetBody")
            sketch = self.doc.addObject("Sketcher::SketchObject", "SourceSketch")
            body.addObject(sketch)
            sketch.addGeometry(Part.LineSegment(App.Vector(), App.Vector(40, 0)), False)
            sketch.addGeometry(Part.LineSegment(App.Vector(40, 0), App.Vector(40, 30)), False)
            return body, sketch
        body, sketch = self.model.edit(sketch_source)
        prepared = self.prepare({"operation": "base_from_sketch", "object_name": sketch.Name,
            "thickness": 1.6, "bend_radius": 2, "length": 25, "bend_side": "Inside",
            "midplane": False, "reverse": False})
        _, future = self.start(prepared)
        obj = self.doc.getObject(self.wait(future)["object_name"])
        self.assertIs(obj.BendSketch, sketch)
        self.assertIs(obj.getParentGeoFeatureGroup(), body)
        self.assertIs(body.Tip, obj)
        self.assertEqual(obj.ViewObject.Proxy.claimChildren(), [sketch])
        self.assertFalse(sketch.Visibility)
        import SheetMetalSourceGui as SourceGui
        panel = SourceGui.SourcePanel(obj)
        self.addCleanup(panel.reject)
        panel.fields["length"].setValue(30)
        panel.apply()
        self.assertIsNotNone(panel.run, panel.message.text())
        self.fixture.fixture.fixture.wait_for(panel.run.future.done)
        self.assertEqual(panel.run.status()["phase"], "ready")
        self.assertEqual(float(obj.Length), 30)

    def test_solid_conversion_keeps_part_container_and_replaced_input(self):
        def solid_source():
            container = self.doc.addObject("App::Part", "SheetPart")
            source = self.doc.addObject("Part::Feature", "Solid")
            container.addObject(source)
            source.Shape = Part.makeBox(50, 40, 30)
            return container, source
        container, source = self.model.edit(solid_source)
        face = next(f"Face{i+1}" for i, f in enumerate(source.Shape.Faces)
                    if f.normalAt(0, 0).z > .9)
        _, future = self.start(self.prepare({"operation": "from_solid", "object_name": source.Name,
            "subelements": [face], "thickness": 1.6, "bend_radius": 2, "invert": False}))
        obj = self.doc.getObject(self.wait(future)["object_name"])
        self.assertIs(obj.getParentGeoFeatureGroup(), container)
        self.assertEqual(obj.baseObject, (source, [face]))
        self.assertEqual(list(obj.VibeCADTimelineReplacedInputs), [source])
        self.assertEqual(obj.ViewObject.Proxy.claimChildren(), [source])
        self.assertFalse(source.Visibility)
        history = self.history(obj)
        history.button("Previous")
        self.assertTrue(source.Visibility)
        self.assertFalse(obj.Visibility)
        history.button("End")
        self.assertFalse(source.Visibility)
        self.assertTrue(obj.Visibility)

    def test_invalid_or_stale_requests_do_not_add_objects_or_undo(self):
        import SheetMetalSourceOperations as Sources
        before = tuple(self.doc.Objects), self.doc.UndoCount
        for changes in ({"thickness": True}, {"shape_type": "Cone"}, {"height": -1},
                        {"origin": "missing"}, {"fill_gaps": 1}, {"width": float("nan")}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, RuntimeError)):
                self.prepare(self.arguments(**changes))
        self.assertEqual(before, (tuple(self.doc.Objects), self.doc.UndoCount))
        prepared = self.prepare()
        self.model.sheet.Label = "Changed since preparation"
        with self.assertRaises(RuntimeError):
            Sources.start(prepared)
        self.assertEqual(before, (tuple(self.doc.Objects), self.doc.UndoCount))

    def test_concurrent_edit_cannot_be_included_in_creation_receipt(self):
        import SheetMetalNativeEdit
        ticket, future = self.start()
        self.model.sheet.Label = "External edit while preparing source"
        with self.assertRaises(SheetMetalNativeEdit.NativeSheetEditError):
            self.wait(future)
        self.assertIsNone(self.context.state.completed_mutation_receipt(ticket))

    def test_failed_geometry_keeps_the_editable_source_without_success_receipt(self):
        import SheetMetalNativeEdit
        with patch("SheetMetalSourceFeatures._validate_shape", side_effect=RuntimeError("Bad solid")):
            ticket, future = self.start()
            with self.assertRaises(SheetMetalNativeEdit.NativeSheetEditError) as caught:
                self.wait(future)
        self.assertTrue(caught.exception.parameters_committed)
        obj = self.doc.getObject(caught.exception.object_name)
        self.assertEqual(float(obj.height), 25)
        self.assertEqual(obj.VibeCADTimelineEditCommand, "SheetMetal_EditSource")
        self.assertIsNone(self.context.state.completed_mutation_receipt(ticket))
        repair = caught.exception.failure()["repair"]
        self.assertIn("does not replace", repair)
        self.assertIn("Modeling", repair)
        self.assertIn("model.history", repair)

    def test_failed_source_can_be_discarded_through_existing_model_history(self):
        from dataclasses import replace
        import SheetMetalNativeEdit
        from VibeCADNativeModelHistoryRuntime import NativeModelHistoryRuntime
        from VibeCADRibbonSurface import read_active_ribbon_surface

        with patch("SheetMetalSourceFeatures._validate_shape", side_effect=RuntimeError("Bad solid")):
            _ticket, future = self.start()
            with self.assertRaises(SheetMetalNativeEdit.NativeSheetEditError) as caught:
                self.wait(future)
        name = caught.exception.object_name
        before = {obj.Name for obj in self.doc.Objects}
        self.assertIn(name, before)
        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("PartDesignWorkbench")
        context = replace(self.context,
            active_surface_id=lambda: read_active_ribbon_surface().surface_id)
        runtime = NativeModelHistoryRuntime(context)
        ticket = context.state.begin_call(self.doc.Uid, "model.history")
        runtime.control_history({"operation": "delete_features", "targets": [{"object_name": name}]},
                                ticket=ticket)
        self.model.recompute()
        self.assertIsNone(self.doc.getObject(name))
        self.assertEqual({obj.Name for obj in self.doc.Objects}, before-{name})

    def test_tree_and_history_command_open_exact_source_without_an_open_transaction(self):
        import SheetMetalSourceGui as SourceGui
        _, future = self.start()
        obj = self.doc.getObject(self.wait(future)["object_name"])
        Gui.Selection.clearSelection()

        Gui.Selection.addSelection(self.model.sheet)
        original, opened = SourceGui.SourcePanel, []
        def panel(source):
            value = original(source)
            opened.append(value)
            return value
        with patch.object(SourceGui, "SourcePanel", side_effect=panel):
            self.assertTrue(obj.ViewObject.Proxy.doubleClicked(obj.ViewObject))
        self.assertIs(opened[0].source, obj)
        self.assertFalse(self.doc.HasPendingTransaction)
        opened[0].reject()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(obj)
        with patch.object(SourceGui, "SourcePanel", side_effect=panel):
            Gui.runCommand("SheetMetal_EditSource")
        self.assertIs(opened[1].source, obj)
        opened[1].reject()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.model.sheet)
        widget, item = self.history(obj).item()
        widget.scrollToItem(item)
        position = QtCore.QPointF(widget.visualItemRect(item).center())
        with patch.object(SourceGui, "SourcePanel", side_effect=panel):
            for kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease,
                         QtCore.QEvent.MouseButtonDblClick):
                event = QtGui.QMouseEvent(kind, position, position, QtCore.Qt.LeftButton,
                                         QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
                QtWidgets.QApplication.sendEvent(widget.viewport(), event)
        self.assertEqual(len(opened), 3)
        self.assertIs(opened[2].source, obj)
        opened[2].reject()
        Gui.Selection.clearSelection()

    def test_source_panel_applies_dimensions_as_one_async_edit(self):
        import SheetMetalSourceGui as SourceGui
        _, future = self.start()
        obj = self.doc.getObject(self.wait(future)["object_name"])
        panel = SourceGui.SourcePanel(obj)
        self.addCleanup(panel.reject)
        undo, volume = self.doc.UndoCount, obj.Shape.Volume
        panel.fields["height"].setValue(32)
        panel.apply()
        self.assertFalse(self.doc.HasPendingTransaction)
        self.assertIsNotNone(panel.run)
        self.fixture.fixture.fixture.wait_for(panel.run.future.done)
        self.assertEqual(panel.run.status()["phase"], "ready")
        self.assertEqual(float(obj.height), 32)
        self.assertGreater(obj.Shape.Volume, volume)
        self.assertEqual(self.doc.UndoCount, undo+1)

    def test_reopen_preserves_source_history_and_its_exact_tree_editor(self):
        import SheetMetalSourceGui as SourceGui
        _, future = self.start()
        name = self.wait(future)["object_name"]
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory)/"source-history.FCStd")
            self.doc.saveAs(filename)
            self.model.settle()
            App.closeDocument(self.doc.Name)
            self.doc = self.model.doc = App.openDocument(filename)
            self.model.settle()
            obj = self.doc.getObject(name)
            self.assertEqual(obj.VibeCADTimelineRole, "operation")
            self.assertEqual(obj.VibeCADTimelineEditCommand, "SheetMetal_EditSource")
            self.assertIs(obj.ViewObject.Proxy.Object, obj)
            panel = SourceGui.SourcePanel(obj)
            self.assertIs(panel.source, obj)
            self.assertEqual(panel.fields["height"].value(), 25)
            panel.reject()

    def test_source_edit_reports_a_failed_dependent_folded_flat_sheet(self):
        import SheetMetalOperations as Operations
        import SheetMetalSourceGui as SourceGui
        _, future = self.start()
        obj = self.doc.getObject(self.wait(future)["object_name"])
        planes = [(face.Area, i+1) for i, face in enumerate(obj.Shape.Faces)
                  if isinstance(face.Surface, Part.Plane)]
        face = f"Face{max(planes)[1]}"
        creation = Operations.start_creation(obj, face,
            expected_revision=Operations.capture_source_revision(obj))
        self.fixture.fixture.fixture.wait_for(creation.future.done)
        self.assertEqual(creation.status()["phase"], "ready", creation.status())
        sheet = self.doc.getObject(creation.status()["object_name"])
        panel = SourceGui.SourcePanel(obj)
        self.addCleanup(panel.reject)
        panel.fields["height"].setValue(32)
        with patch("SheetMetalEditable.SheetGeometry.prepare",
                   side_effect=RuntimeError("Downstream sheet failed")) as rebuild:
            panel.apply()
            self.assertIsNotNone(panel.run, panel.message.text())
            self.fixture.fixture.fixture.wait_for(panel.run.future.done)
        rebuild.assert_called()
        self.assertIn("Invalid", sheet.State)
        self.assertEqual(panel.run.status()["phase"], "failed")
        self.assertNotEqual(panel.message.text(), "Updated")
        self.assertTrue(obj.Shape.isValid())
        self.assertEqual(float(obj.height), 32)

    def test_superseded_panel_cannot_overwrite_newer_dimensions(self):
        import SheetMetalSourceGui as SourceGui
        _, future = self.start()
        obj = self.doc.getObject(self.wait(future)["object_name"])
        panel = SourceGui.SourcePanel(obj)
        self.addCleanup(panel.reject)
        panel.fields["height"].setValue(32)
        panel.apply()
        obj.height = 34
        self.fixture.fixture.fixture.wait_for(panel.run.future.done)
        self.assertEqual(panel.run.status()["phase"], "superseded")
        self.assertFalse(panel.apply_button.isEnabled())
        panel.apply()
        self.assertEqual(float(obj.height), 34)

    def test_registered_source_variants_create_valid_sources_without_mcp(self):
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
        def inputs():
            sketch = self.doc.addObject("Sketcher::SketchObject", "ToolProfile")
            sketch.addGeometry(Part.LineSegment(App.Vector(), App.Vector(40, 0)), False)
            sketch.addGeometry(Part.LineSegment(App.Vector(40, 0), App.Vector(40, 30)), False)
            solid = self.doc.addObject("Part::Feature", "ToolSolid")
            solid.Shape = Part.makeBox(50, 40, 30)
            return sketch, solid
        sketch, solid = self.model.edit(inputs)
        face = next(f"Face{i+1}" for i, item in enumerate(solid.Shape.Faces) if item.normalAt(0, 0).z > .9)
        requests = (
            self.arguments(),
            {"operation": "base_from_sketch", "object_name": sketch.Name, "thickness": 1.6,
             "bend_radius": 2, "length": 25, "bend_side": "Inside", "midplane": False, "reverse": False},
            {"operation": "from_solid", "object_name": solid.Name, "subelements": [face],
             "thickness": 1.6, "bend_radius": 2, "invert": False},
        )
        registry = build_native_capability_registry()
        runtime = build_native_runtime_bindings(self.context, ("sheet_metal.create",))["sheet_metal.create"]
        for arguments in requests:
            with self.subTest(operation=arguments["operation"]):
                ticket = self.context.state.begin_call(self.doc.Uid, "sheet_metal.create")
                self.addCleanup(lambda ticket=ticket: self.context.state.cancel_mutation(ticket))
                undo = self.doc.UndoCount
                future = registry.implementation("sheet_metal.create").async_handler(SimpleNamespace(
                    runtime=runtime, ticket=ticket, arguments=arguments))
                self.assertFalse(future.done())
                self.assertFalse(self.doc.HasPendingTransaction)
                result = self.wait(future)
                obj = self.doc.getObject(result["object_name"])
                self.assertTrue(obj.Shape.isValid())
                self.assertEqual(result["source_geometry"]["solid_count"], 1)
                self.assertEqual(result["operation"], arguments["operation"])
                self.assertEqual(self.doc.UndoCount, undo+1)
                self.assertTrue(result["assistant_undo_available"])
                self.assertEqual(obj.VibeCADTimelineEditCommand, "SheetMetal_EditSource")
                if arguments["operation"] == "base_from_sketch":
                    self.assertIs(obj.BendSketch, sketch)
                elif arguments["operation"] == "from_solid":
                    self.assertEqual(obj.baseObject, (solid, [face]))
