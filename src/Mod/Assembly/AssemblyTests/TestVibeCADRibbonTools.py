# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD behavior contracts for native commands on the Assemble ribbon."""

import unittest
import pathlib
import tempfile

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui

JOINT_COMMANDS = (
    "Assembly_CreateJointFixed",
    "Assembly_CreateJointRevolute",
    "Assembly_CreateJointCylindrical",
    "Assembly_CreateJointSlider",
    "Assembly_CreateJointBall",
    "Assembly_CreateJointDistance",
    "Assembly_CreateJointParallel",
    "Assembly_CreateJointPerpendicular",
    "Assembly_CreateJointAngle",
    "Assembly_CreateJointRackPinion",
    "Assembly_CreateJointScrew",
    "Assembly_CreateJointGears",
    "Assembly_CreateJointBelt",
    "Assembly_CreateJointGearBelt",
)

JOINT_TASK_COMMANDS = JOINT_COMMANDS[:-1]

ASSEMBLY_RIBBON_GROUPS = {
    "Assembly": (
        "Assembly_CreateAssembly",
        "Assembly_ActivateAssembly",
        "Assembly_Insert",
        "Assembly_SolveAssembly",
        "Assembly_CreateView",
        "Assembly_CreateSimulation",
        "Assembly_CreateBom",
    ),
    "Assembly Joints": (
        "Assembly_ToggleGrounded",
        "Separator",
        "Assembly_CreateJointFixed",
        "Assembly_CreateJointRevolute",
        "Assembly_CreateJointCylindrical",
        "Assembly_CreateJointSlider",
        "Assembly_CreateJointBall",
        "Separator",
        "Assembly_CreateJointDistance",
        "Assembly_CreateJointParallel",
        "Assembly_CreateJointPerpendicular",
        "Assembly_CreateJointAngle",
        "Separator",
        "Assembly_CreateJointRackPinion",
        "Assembly_CreateJointScrew",
        "Assembly_CreateJointGearBelt",
    ),
    "Assembly Diagnose": (
        "Assembly_SelectConflictingConstraints",
        "Assembly_SelectRedundantConstraints",
        "Assembly_SelectPartiallyRedundantConstraints",
        "Assembly_SelectMalformedConstraints",
        "Separator",
        "Assembly_SelectJointsOfComponent",
    ),
    "Standard Components": (
        "VibeCAD_InsertStandardFastener",
        "VibeCAD_EditStandardFastener",
    ),
}

VIBECAD_RIBBON_FASTENER_ADDITIONS = (
    "VibeCAD_CreateMatchingFastenerHole",
    "VibeCAD_AttachStandardFastener",
)

ASSEMBLY_MENU_ONLY_COMMANDS = (
    "Assembly_LinkSelectLinked",
    "Assembly_ExportASMT",
)

STANDARD_TOOLBAR_TITLES = {
    "File",
    "Edit",
    "Clipboard",
    "Workbench",
    "Macro",
    "View",
    "Individual Views",
    "Structure",
    "Help",
}

ASSEMBLY_COMPOSITES = {
    "Assembly_Insert": (
        "Assembly_InsertLink",
        "Assembly_InsertNewPart",
    ),
    "Assembly_CreateJointGearBelt": (
        "Assembly_CreateJointGears",
        "Assembly_CreateJointBelt",
    ),
}

ASSEMBLY_COMMAND_TIMELINE_BEHAVIOR = {
    "Assembly_CreateAssembly": frozenset({"operation", "standalone"}),
    "Assembly_ActivateAssembly": frozenset({"read-only"}),
    "Assembly_Insert": frozenset({"read-only"}),
    "Assembly_InsertLink": frozenset({"operation", "source-preserving"}),
    "Assembly_InsertNewPart": frozenset({"operation", "standalone"}),
    "Assembly_SolveAssembly": frozenset({"in-place"}),
    "Assembly_CreateView": frozenset({"operation", "resource", "source-preserving"}),
    "Assembly_CreateSimulation": frozenset({"operation", "resource", "source-preserving"}),
    "Assembly_CreateBom": frozenset({"operation", "source-preserving"}),
    "Assembly_ToggleGrounded": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointFixed": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointRevolute": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointCylindrical": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointSlider": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointBall": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointDistance": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointParallel": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointPerpendicular": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointAngle": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointRackPinion": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointScrew": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointGears": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointBelt": frozenset({"operation", "source-preserving"}),
    "Assembly_CreateJointGearBelt": frozenset({"read-only"}),
    "Assembly_SelectConflictingConstraints": frozenset({"read-only"}),
    "Assembly_SelectRedundantConstraints": frozenset({"read-only"}),
    "Assembly_SelectPartiallyRedundantConstraints": frozenset({"read-only"}),
    "Assembly_SelectMalformedConstraints": frozenset({"read-only"}),
    "Assembly_SelectJointsOfComponent": frozenset({"read-only"}),
    "VibeCAD_InsertStandardFastener": frozenset({"operation", "standalone"}),
    "VibeCAD_EditStandardFastener": frozenset({"in-place"}),
    "VibeCAD_CreateMatchingFastenerHole": frozenset({"operation", "body-history-step"}),
    "VibeCAD_AttachStandardFastener": frozenset({"in-place"}),
    "Assembly_LinkSelectLinked": frozenset({"read-only"}),
    "Assembly_ExportASMT": frozenset({"read-only"}),
}


def _action_command_id(action):
    for property_name in (
        "VibeCADCommandId",
        "CommandName",
        "FreeCADCommandGroupChildId",
    ):
        value = action.property(property_name)
        if value is None:
            continue
        if isinstance(value, QtCore.QByteArray):
            command_id = bytes(value).decode("utf-8")
        else:
            command_id = str(value)
        command_id = command_id.strip()
        if command_id:
            return command_id
    value = action.data()
    if value is not None:
        if isinstance(value, QtCore.QByteArray):
            command_id = bytes(value).decode("utf-8")
        else:
            command_id = str(value)
        command_id = command_id.strip()
        if command_id:
            return command_id
    return action.objectName().strip()


def _collect_assembly_menu_commands():
    menu_action = next(
        (
            action
            for action in Gui.getMainWindow().menuBar().actions()
            if action.text().replace("&", "") == "Assembly"
        ),
        None,
    )
    if menu_action is None or menu_action.menu() is None:
        return None

    commands = set()

    def collect(menu):
        for action in menu.actions():
            if action.isSeparator():
                continue
            if action.menu() is not None:
                collect(action.menu())
                continue
            command_id = _action_command_id(action)
            if command_id:
                commands.add(command_id)

    collect(menu_action.menu())
    return commands


@unittest.skipIf(not App.GuiUp, "VibeCAD Assemble ribbon tests require the GUI")
class TestVibeCADAssemblyRibbonTools(unittest.TestCase):
    """Assembly commands must own exactly their model attempt and task lifetime."""

    def setUp(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        self.document = App.newDocument("VibeCADAssemblyRibbonTools")
        self.temp_directory = tempfile.TemporaryDirectory()
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self._process_events()

    def tearDown(self):
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
            self._process_events()
        gui_document = Gui.activeDocument()
        if gui_document is not None and gui_document.getInEdit() is not None:
            gui_document.resetEdit()
            self._process_events()
        Gui.Selection.clearSelection()
        documents = App.listDocuments()
        if (
            getattr(self, "document", None) is not None
            and documents.get(self.document.Name) is self.document
        ):
            App.closeDocument(self.document.Name)
        if "VibeCADAssemblyRibbonTools" in App.listDocuments():
            App.closeDocument("VibeCADAssemblyRibbonTools")
        self._process_events()
        self.temp_directory.cleanup()

    @staticmethod
    def _process_events(wait_ms=30):
        Gui.updateGui()
        application = QtGui.QApplication.instance()
        if application is not None:
            application.processEvents()
        if wait_ms:
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(wait_ms, loop.quit)
            loop.exec()

    @staticmethod
    def _send_mouse(
        widget,
        event_type,
        position,
        button,
        buttons,
        modifiers=QtCore.Qt.NoModifier,
    ):
        global_position = widget.mapToGlobal(position)
        event = QtGui.QMouseEvent(
            event_type,
            position,
            global_position,
            button,
            buttons,
            modifiers,
        )
        QtGui.QApplication.sendEvent(widget, event)

    @staticmethod
    def _viewport_point(view, viewport, world_point):
        screen_x, screen_y = view.getPointOnScreen(world_point)
        _width, height = view.getSize()
        try:
            scale = viewport.devicePixelRatioF()
        except RuntimeError:
            scale = 1.0
        return QtCore.QPoint(
            int(round(screen_x / scale)),
            int(round((height - screen_y - 1) / scale)),
        )

    def _select_component_for_drag(self, assembly, component):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(component)
        self._process_events(50)
        self.assertEqual(Gui.Selection.getSelection(), [component])
        self.assertTrue(assembly.ViewObject.DraggerVisibility)

    def _press_assembly_dragger_handle(self, assembly, component):
        """Press the rendered X-axis handle and leave its gesture active."""
        view = Gui.activeDocument().activeView()
        graphics_view = view.graphicsView()
        viewport = graphics_view.viewport()
        self.assertTrue(viewport.isVisible())

        bounds = viewport.rect().adjusted(4, 4, -4, -4)
        dragger = assembly.ViewObject.getDragger()
        scale_field = dragger.getField("autoScaleResult")
        self.assertIsNotNone(scale_field)
        handle_scale = float(scale_field.getValue())
        self.assertGreater(handle_scale, 0.0)
        x_container = dragger.getPart("xTranslatorDragger", False)
        self.assertIsNotNone(x_container)
        x_dragger = x_container.getPart("dragger", False)
        self.assertIsNotNone(x_dragger)
        x_arrow = x_dragger.getPart("arrow", False)
        self.assertIsNotNone(x_arrow)
        cylinder_height = float(x_arrow.getField("cylinderHeight").getValue())
        cone_height = float(x_arrow.getField("coneHeight").getValue())
        pick_distance = handle_scale * (cylinder_height + cone_height / 2.0)
        dragger_placement = App.Placement(assembly.ViewObject.DraggerPlacement)
        position = self._viewport_point(
            view,
            viewport,
            dragger_placement.multVec(App.Vector(pick_distance, 0, 0)),
        )
        self.assertTrue(
            bounds.contains(position),
            "The rendered Assembly X-axis handle is outside the viewport",
        )
        self.assertEqual(Gui.Selection.getSelection(), [component])
        self.assertTrue(assembly.ViewObject.DraggerVisibility)
        self._send_mouse(
            viewport,
            QtCore.QEvent.MouseButtonPress,
            position,
            QtCore.Qt.LeftButton,
            QtCore.Qt.LeftButton,
        )
        self.assertNotEqual(
            self.document.getBookedTransactionID(),
            0,
            "The rendered Assembly X-axis handle did not begin a move",
        )
        return viewport, position

    def _move_pressed_dragger(self, viewport, press_position):
        bounds = viewport.rect().adjusted(8, 8, -8, -8)
        delta_x = 72 if press_position.x() < bounds.center().x() else -72
        delta_y = 36 if press_position.y() < bounds.center().y() else -36
        target = press_position + QtCore.QPoint(delta_x, delta_y)
        target.setX(max(bounds.left(), min(target.x(), bounds.right())))
        target.setY(max(bounds.top(), min(target.y(), bounds.bottom())))
        self._send_mouse(
            viewport,
            QtCore.QEvent.MouseMove,
            target,
            QtCore.Qt.NoButton,
            QtCore.Qt.LeftButton,
        )
        self._process_events(80)
        return target

    def _release_dragger(self, viewport, position):
        self._send_mouse(
            viewport,
            QtCore.QEvent.MouseButtonRelease,
            position,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoButton,
        )
        self._process_events(100)

    def _drag_rendered_component(
        self,
        component,
        *,
        delta_x=72,
        expect_start=True,
        expected_transaction=0,
        modifiers=QtCore.Qt.NoModifier,
        move=True,
        release=True,
    ):
        """Press and drag a visible Assembly occurrence without using its dragger."""
        view = Gui.activeDocument().activeView()
        view.viewAxonometric()
        view.fitAll()
        self._process_events(100)
        viewport = view.graphicsView().viewport()
        self.assertTrue(viewport.isVisible())

        linked = component.getLinkedObject()
        self.assertIsNotNone(linked)
        center = component.Placement.multVec(linked.Shape.BoundBox.Center)
        press_position = self._viewport_point(view, viewport, center)
        bounds = viewport.rect().adjusted(8, 8, -8, -8)
        self.assertTrue(
            bounds.contains(press_position),
            "The rendered Assembly occurrence is outside the viewport",
        )
        signed_delta_x = delta_x if press_position.x() < bounds.center().x() else -delta_x
        target = press_position + QtCore.QPoint(signed_delta_x, 0)
        target.setX(max(bounds.left(), min(target.x(), bounds.right())))

        self._send_mouse(
            viewport,
            QtCore.QEvent.MouseMove,
            press_position,
            QtCore.Qt.NoButton,
            QtCore.Qt.NoButton,
        )
        self._process_events(80)
        preselection = Gui.Selection.getPreselection()
        self.assertTrue(
            preselection.ObjectName,
            f"The rendered component was not preselected: {preselection}",
        )
        self._send_mouse(
            viewport,
            QtCore.QEvent.MouseButtonPress,
            press_position,
            QtCore.Qt.LeftButton,
            QtCore.Qt.LeftButton,
            modifiers,
        )
        if move:
            self._send_mouse(
                viewport,
                QtCore.QEvent.MouseMove,
                target,
                QtCore.Qt.NoButton,
                QtCore.Qt.LeftButton,
                modifiers,
            )
            self._process_events(80)
        transaction = self.document.getBookedTransactionID()
        if expect_start:
            self.assertNotEqual(
                transaction,
                0,
                "Direct manipulation did not begin a move transaction for "
                f"{preselection.ObjectName} {preselection.SubElementNames}",
            )
        else:
            self.assertEqual(transaction, expected_transaction)
        if release:
            self._send_mouse(
                viewport,
                QtCore.QEvent.MouseButtonRelease,
                target,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoButton,
                modifiers,
            )
            self._process_events(100)
        return viewport, target

    def _dismiss_task(self, *, accept):
        self.assertTrue(Gui.Control.activeDialog())
        standards = (
            (QtGui.QDialogButtonBox.Ok, QtGui.QDialogButtonBox.Save)
            if accept
            else (
                QtGui.QDialogButtonBox.Cancel,
                QtGui.QDialogButtonBox.Abort,
                QtGui.QDialogButtonBox.Close,
            )
        )
        button = None
        for button_box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox):
            if not button_box.isVisible():
                continue
            for standard in standards:
                candidate = button_box.button(standard)
                if candidate is not None and candidate.isVisible() and candidate.isEnabled():
                    button = candidate
                    break
            if button is not None:
                break
        self.assertIsNotNone(button)
        button.click()
        self._process_events(100)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def _choose_message_box(self, standard_button, before_click=None):
        state = {"clicked": False, "before_click": before_click}

        def choose():
            for widget in QtGui.QApplication.topLevelWidgets():
                if not isinstance(widget, QtGui.QMessageBox) or not widget.isVisible():
                    continue
                button = widget.button(standard_button)
                if button is None:
                    continue
                callback = state.pop("before_click", None)
                if callback is not None:
                    callback()
                state["clicked"] = True
                button.click()
                return
            QtCore.QTimer.singleShot(5, choose)

        QtCore.QTimer.singleShot(0, choose)
        return state

    def _create_assembly_with_components(self, count):
        Gui.runCommand("Assembly_CreateAssembly")
        self._process_events(100)
        assembly = next(
            obj for obj in self.document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        )

        self.document.UndoMode = False
        components = []
        for index in range(count):
            source = self.document.addObject(
                "Part::Feature",
                f"Source{index + 1}",
            )
            source.Shape = Part.makeBox(10, 8, 6)
            source.Placement.Base.x = index * 20
            component = assembly.newObject(
                "App::Link",
                f"Component{index + 1}",
            )
            component.LinkedObject = source
            components.append(component)
        self.document.recompute()
        self.document.UndoMode = True
        return assembly, components

    def _create_connected_component_delete_fixture(self):
        import JointObject

        assembly, components = self._create_assembly_with_components(2)
        self.document.UndoMode = False
        joint_group = next(
            child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
        )
        joint = joint_group.newObject(
            "App::FeaturePython",
            "DeleteAtomicityJoint",
        )
        JointObject.Joint(joint, 1)
        JointObject.ViewProviderJoint(joint.ViewObject)
        joint.Proxy.setJointConnectors(
            joint,
            [
                [components[0], ["Face1"]],
                [components[1], ["Face1"]],
            ],
        )
        blocker = self.document.addObject(
            "App::FeaturePython",
            "DeleteAtomicityBlocker",
        )
        blocker.addProperty(
            "App::PropertyLinkGlobal",
            "ProtectedComponent",
        )
        blocker.ProtectedComponent = components[0]
        self.document.recompute()
        self.document.UndoMode = True
        return assembly, components, joint, blocker

    def _create_tracked_subassembly_occurrence(self, *, rigid=True):
        import UtilsAssembly

        assembly, _components = self._create_assembly_with_components(0)
        self.document.UndoMode = False
        source_assembly = self.document.addObject(
            "Assembly::AssemblyObject",
            "TrackedSourceAssembly",
        )
        source_assembly.Type = "Assembly"
        source_shape = self.document.addObject(
            "Part::Feature",
            "TrackedSourceShape",
        )
        source_shape.Shape = Part.makeBox(8, 6, 4)
        source_component = source_assembly.newObject(
            "App::Link",
            "TrackedSourceComponent",
        )
        source_component.LinkedObject = source_shape
        self.document.recompute()
        self.document.UndoMode = True

        self.document.openTransaction("Insert tracked subassembly")
        try:
            occurrence = assembly.newObject(
                "Assembly::AssemblyLink",
                "TrackedOccurrence",
            )
            occurrence.LinkedObject = source_assembly
            occurrence.Rigid = rigid
            UtilsAssembly.finalizeInsertedComponentTimeline(occurrence)
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise
        return assembly, source_assembly, source_shape, occurrence

    def test_tracked_subassembly_sync_requires_transaction_and_noop_is_stable(self):
        import UtilsAssembly

        (
            _assembly,
            source_assembly,
            source_shape,
            occurrence,
        ) = self._create_tracked_subassembly_occurrence()
        timeline = self.document.getObject("VibeCADTimeline")
        before_operations = tuple(timeline.Operations)
        before_group = tuple(occurrence.Group)
        before_resources = tuple(UtilsAssembly._assemblyOccurrenceResources(occurrence))

        # A recompute with no structural change may refresh values, but it
        # must not rewrite either the native graph or History.
        occurrence.synchronizeContents()
        self.assertEqual(tuple(occurrence.Group), before_group)
        self.assertEqual(
            tuple(UtilsAssembly._assemblyOccurrenceResources(occurrence)),
            before_resources,
        )
        self.assertEqual(tuple(timeline.Operations), before_operations)

        added_source = source_assembly.newObject(
            "App::Link",
            "UncommittedSourceMembership",
        )
        added_source.LinkedObject = source_shape
        after_source_operations = tuple(timeline.Operations)
        self.assertIn(added_source, after_source_operations)
        self.assertEqual(tuple(occurrence.Group), before_group)
        with self.assertRaisesRegex(
            RuntimeError,
            "caller-owned transaction",
        ):
            occurrence.synchronizeContents()
        self.assertEqual(tuple(occurrence.Group), before_group)
        self.assertEqual(
            tuple(timeline.Operations),
            after_source_operations,
        )

    def test_same_document_source_membership_synchronizes_on_exact_commit(self):
        import UtilsAssembly

        (
            _assembly,
            source_assembly,
            source_shape,
            occurrence,
        ) = self._create_tracked_subassembly_occurrence()
        timeline = self.document.getObject("VibeCADTimeline")
        occurrence_name = occurrence.Name
        before_resource_names = tuple(
            resource.Name for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
        )

        self.document.openTransaction("Create occurrence downstream consumer")
        try:
            consumer = self.document.addObject(
                "App::FeaturePython",
                "OccurrenceDownstreamConsumer",
            )
            consumer.addProperty(
                "App::PropertyXLink",
                "Occurrence",
            )
            consumer.Occurrence = occurrence
            self.document.publishProvisionalTimelineOperationBlock(
                consumer,
                [],
            )
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise

        self.document.openTransaction("Add automatically synchronized source member")
        try:
            added_source = source_assembly.newObject(
                "App::Link",
                "AutomaticallySynchronizedSource",
            )
            added_source.LinkedObject = source_shape
            self.document.publishProvisionalTimelineOperationBlock(
                added_source,
                [],
            )
            # No Assembly synchronization helper is called. The source
            # operation and occurrence update must close as one exact change.
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise

        occurrence = self.document.getObject(occurrence_name)
        added_local = next(
            resource
            for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
            if getattr(
                resource,
                "VibeCADAssemblySourceObjectId",
                -1,
            )
            == int(added_source.ID)
        )
        operations = list(timeline.Operations)
        self.assertLess(
            operations.index(added_source),
            operations.index(added_local),
        )
        self.assertLess(
            operations.index(added_local),
            operations.index(occurrence),
        )
        self.assertLess(
            operations.index(occurrence),
            operations.index(consumer),
        )

        self.document.undo()
        occurrence = self.document.getObject(occurrence_name)
        self.assertIsNone(self.document.getObject("AutomaticallySynchronizedSource"))
        self.assertEqual(
            tuple(
                resource.Name for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
            ),
            before_resource_names,
        )
        operations = list(timeline.Operations)
        self.assertLess(
            operations.index(occurrence),
            operations.index(consumer),
        )

        self.document.redo()
        occurrence = self.document.getObject(occurrence_name)
        added_source = self.document.getObject("AutomaticallySynchronizedSource")
        self.assertIsNotNone(added_source)
        self.assertTrue(
            any(
                getattr(
                    resource,
                    "VibeCADAssemblySourceObjectId",
                    -1,
                )
                == int(added_source.ID)
                for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
            )
        )

    def test_clearing_tracked_subassembly_retires_complete_managed_closure(self):
        import UtilsAssembly

        (
            _assembly,
            source_assembly,
            _source_shape,
            occurrence,
        ) = self._create_tracked_subassembly_occurrence(rigid=False)
        old_resources = tuple(UtilsAssembly._assemblyOccurrenceResources(occurrence))
        self.assertTrue(old_resources)
        old_identities = {(int(resource.ID), resource.Name) for resource in old_resources}

        self.document.openTransaction("Clear linked subassembly")
        try:
            occurrence.LinkedObject = None
            result = UtilsAssembly.synchronizeAssemblyLinkTimelineResources(occurrence)
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise

        self.assertEqual(list(result["final_resources"]), [])
        self.assertEqual(
            {(int(object_id), str(object_name)) for object_id, object_name in result["retired"]},
            old_identities,
        )
        self.assertTrue(
            all(
                final_resource is None
                for _object_id, _object_name, final_resource in result["old_to_final"]
            )
        )
        self.assertEqual(
            UtilsAssembly._assemblyOccurrenceResources(occurrence),
            [],
        )
        for _object_id, object_name in old_identities:
            self.assertIsNone(self.document.getObject(object_name))

        occurrence_name = occurrence.Name
        self.document.undo()
        occurrence = self.document.getObject(occurrence_name)
        self.assertIsNotNone(occurrence)
        self.assertIs(occurrence.LinkedObject, source_assembly)
        self.assertEqual(
            {
                (int(resource.ID), resource.Name)
                for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
            },
            old_identities,
        )

    def test_source_identity_mismatch_never_transfers_resource_state(self):
        import UtilsAssembly

        (
            _assembly,
            source_assembly,
            source_shape,
            occurrence,
        ) = self._create_tracked_subassembly_occurrence()
        source_component = next(
            child for child in source_assembly.Group if child.TypeId == "App::Link"
        )
        old_resource = next(
            resource
            for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
            if resource.TypeId == "App::Link"
            and resource.VibeCADAssemblySourceObjectId == int(source_component.ID)
        )
        old_identity = (int(old_resource.ID), old_resource.Name)

        self.document.openTransaction("Reject stale AssemblyLink source identity")
        try:
            # Simulate a stale pointer cache whose persisted source identity
            # no longer identifies the source object at that address.  Native
            # reconciliation must treat this as retirement plus creation, not
            # as a replacement eligible to inherit History state.
            old_resource.VibeCADAssemblySourceDocument = source_shape.Document.Uid
            old_resource.VibeCADAssemblySourceObjectId = int(source_shape.ID)
            old_resource.VibeCADAssemblySourceObjectName = source_shape.Name
            result = UtilsAssembly.synchronizeAssemblyLinkTimelineResources(occurrence)

            old_mapping = next(
                mapping
                for mapping in result["old_to_final"]
                if (int(mapping[0]), str(mapping[1])) == old_identity
            )
            self.assertIsNone(old_mapping[2])
            replacement = next(
                resource
                for resource in result["final_resources"]
                if resource.TypeId == "App::Link"
                and resource.VibeCADAssemblySourceObjectId == int(source_component.ID)
            )
            self.assertIsNot(replacement, old_resource)
            self.assertEqual(
                replacement.VibeCADAssemblySourceObjectName,
                source_component.Name,
            )
        finally:
            self.document.abortTransaction()

    def test_suppressed_stale_joint_resource_is_retired(self):
        import JointObject
        import UtilsAssembly

        (
            _assembly,
            source_assembly,
            source_shape,
            occurrence,
        ) = self._create_tracked_subassembly_occurrence(rigid=False)
        local_joint_group = next(
            child for child in occurrence.Group if child.TypeId == "Assembly::JointGroup"
        )

        self.document.openTransaction("Add stale suppressed joint clone")
        try:
            stale_joint = local_joint_group.newObject(
                "App::FeaturePython",
                "StaleSuppressedJointClone",
            )
            JointObject.Joint(
                stale_joint,
                1,
                register_timeline_editor=False,
            )
            JointObject.ViewProviderJoint(stale_joint.ViewObject)
            stale_joint.Suppressed = True
            stale_joint.addProperty(
                "App::PropertyString",
                "VibeCADAssemblySourceDocument",
                "Assembly",
            )
            stale_joint.addProperty(
                "App::PropertyInteger",
                "VibeCADAssemblySourceObjectId",
                "Assembly",
            )
            stale_joint.addProperty(
                "App::PropertyString",
                "VibeCADAssemblySourceObjectName",
                "Assembly",
            )
            stale_joint.VibeCADAssemblySourceDocument = source_shape.Document.Uid
            stale_joint.VibeCADAssemblySourceObjectId = int(source_shape.ID)
            stale_joint.VibeCADAssemblySourceObjectName = source_shape.Name
            UtilsAssembly.markTimelineResource(
                stale_joint,
                occurrence,
            )
            self.document.finalizeProvisionalTimelineOperationBlock(
                occurrence,
                [stale_joint],
            )
            stale_identity = (int(stale_joint.ID), stale_joint.Name)
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise

        self.assertIsNone(self.document.getObject(stale_identity[1]))
        self.assertNotIn(
            stale_identity,
            {
                (int(resource.ID), resource.Name)
                for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
            },
        )
        self.assertEqual([], list(occurrence.Joints))

    def test_nested_flexible_occurrence_is_owned_before_rigid_callback(self):
        import UtilsAssembly

        target_assembly, _components = self._create_assembly_with_components(0)
        self.document.UndoMode = False
        leaf_assembly = self.document.addObject(
            "Assembly::AssemblyObject",
            "NestedLeafAssembly",
        )
        leaf_assembly.Type = "Assembly"
        leaf_shape = self.document.addObject(
            "Part::Feature",
            "NestedLeafShape",
        )
        leaf_shape.Shape = Part.makeBox(5, 4, 3)
        leaf_component = leaf_assembly.newObject(
            "App::Link",
            "NestedLeafComponent",
        )
        leaf_component.LinkedObject = leaf_shape

        source_assembly = self.document.addObject(
            "Assembly::AssemblyObject",
            "NestedSourceAssembly",
        )
        source_assembly.Type = "Assembly"
        source_nested = source_assembly.newObject(
            "Assembly::AssemblyLink",
            "SourceFlexibleOccurrence",
        )
        source_nested.LinkedObject = leaf_assembly
        source_nested.Rigid = False
        self.document.recompute()
        self.document.UndoMode = True

        self.document.openTransaction("Insert nested flexible subassembly")
        try:
            occurrence = target_assembly.newObject(
                "Assembly::AssemblyLink",
                "NestedTrackedOccurrence",
            )
            occurrence.LinkedObject = source_assembly
            UtilsAssembly.finalizeInsertedComponentTimeline(occurrence)
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise

        local_nested = next(
            child for child in occurrence.Group if child.TypeId == "Assembly::AssemblyLink"
        )
        self.assertFalse(local_nested.Rigid)
        self.assertTrue(occurrence.hasObject(local_nested))
        self.assertTrue(target_assembly.hasObject(occurrence))
        self.assertIs(local_nested.LinkedObject, source_nested)
        self.assertIn(
            local_nested,
            UtilsAssembly._assemblyOccurrenceResources(occurrence),
        )
        self.assertTrue(
            any(
                resource is not local_nested and resource.VibeCADTimelineOwner is occurrence
                for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
            )
        )

    def test_two_link_cycle_fails_closed_without_recursive_resolution(self):
        first = self.document.addObject(
            "Assembly::AssemblyLink",
            "TwoCycleFirst",
        )
        second = self.document.addObject(
            "Assembly::AssemblyLink",
            "TwoCycleSecond",
        )
        first.LinkedObject = second
        second.LinkedObject = first

        first.synchronizeContents()
        second.synchronizeContents()

        self.assertEqual(list(first.Group), [])
        self.assertEqual(list(second.Group), [])

    def test_longer_link_cycle_fails_closed_without_recursive_resolution(self):
        first = self.document.addObject(
            "Assembly::AssemblyLink",
            "LongCycleFirst",
        )
        second = self.document.addObject(
            "Assembly::AssemblyLink",
            "LongCycleSecond",
        )
        third = self.document.addObject(
            "Assembly::AssemblyLink",
            "LongCycleThird",
        )
        first.LinkedObject = second
        second.LinkedObject = third
        third.LinkedObject = first

        for occurrence in (first, second, third):
            occurrence.synchronizeContents()
            self.assertEqual(list(occurrence.Group), [])

    def test_acyclic_link_chain_still_resolves_terminal_assembly(self):
        terminal = self.document.addObject(
            "Assembly::AssemblyObject",
            "LinkChainTerminal",
        )
        terminal.Type = "Assembly"
        source_shape = self.document.addObject(
            "Part::Feature",
            "LinkChainShape",
        )
        source_shape.Shape = Part.makeBox(3, 4, 5)
        source_component = terminal.newObject(
            "App::Link",
            "LinkChainComponent",
        )
        source_component.LinkedObject = source_shape

        third = self.document.addObject(
            "Assembly::AssemblyLink",
            "LinkChainThird",
        )
        second = self.document.addObject(
            "Assembly::AssemblyLink",
            "LinkChainSecond",
        )
        first = self.document.addObject(
            "Assembly::AssemblyLink",
            "LinkChainFirst",
        )
        third.LinkedObject = terminal
        second.LinkedObject = third
        first.LinkedObject = second

        first.synchronizeContents()

        local_component = next(child for child in first.Group if child.TypeId == "App::Link")
        self.assertIs(local_component.LinkedObject, source_component)
        self.assertEqual(
            local_component.VibeCADAssemblySourceObjectId,
            int(source_component.ID),
        )

    def test_source_history_navigation_is_observational(self):
        import UtilsAssembly

        source_document = App.newDocument("AssemblyLinkSourceHistoryDocument")
        try:
            source_document.UndoMode = False
            source_assembly = source_document.addObject(
                "Assembly::AssemblyObject",
                "SourceHistoryAssembly",
            )
            source_assembly.Type = "Assembly"
            source_document.UndoMode = True

            source_document.openTransaction("Create source history component")
            try:
                source_component = source_assembly.newObject(
                    "Part::Box",
                    "SourceHistoryComponent",
                )
                source_document.publishProvisionalTimelineOperationBlock(
                    source_component,
                    [],
                )
                source_document.commitTransaction()
            except Exception:
                source_document.abortTransaction()
                raise

            source_document.saveAs(
                str(pathlib.Path(self.temp_directory.name) / "assembly-link-source-history.FCStd")
            )
            App.setActiveDocument(self.document.Name)
            target_assembly, _components = self._create_assembly_with_components(0)
            self.document.saveAs(
                str(pathlib.Path(self.temp_directory.name) / "assembly-link-target-history.FCStd")
            )
            self.document.openTransaction("Insert source history occurrence")
            try:
                occurrence = target_assembly.newObject(
                    "Assembly::AssemblyLink",
                    "SourceHistoryOccurrence",
                )
                occurrence.LinkedObject = source_assembly
                UtilsAssembly.finalizeInsertedComponentTimeline(occurrence)
                self.document.commitTransaction()
            except Exception:
                self.document.abortTransaction()
                raise

            resources_before = tuple(
                (
                    resource.Name,
                    int(resource.ID),
                    resource,
                )
                for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
            )
            local_component = next(
                resource
                for _name, _object_id, resource in resources_before
                if resource.TypeId == "App::Link"
            )
            source_timeline = source_document.getObject("VibeCADTimeline")
            source_index = list(source_timeline.Operations).index(source_component)
            source_undo = int(source_document.UndoCount)
            target_undo = int(self.document.UndoCount)

            source_timeline.Position = source_index
            source_document.recompute()
            occurrence.synchronizeContents()

            self.assertFalse(UtilsAssembly.isTimelineOperationActive(local_component))
            self.assertEqual(
                tuple(
                    (
                        resource.Name,
                        int(resource.ID),
                        resource,
                    )
                    for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
                ),
                resources_before,
            )
            for document, undo_count in (
                (source_document, source_undo),
                (self.document, target_undo),
            ):
                self.assertFalse(document.HasPendingTransaction)
                self.assertEqual(
                    int(document.getBookedTransactionID()),
                    0,
                )
                self.assertEqual(int(document.UndoCount), undo_count)

            source_timeline.Position = len(source_timeline.Operations)
            source_document.recompute()
            occurrence.synchronizeContents()

            self.assertTrue(UtilsAssembly.isTimelineOperationActive(local_component))
            self.assertEqual(
                tuple(
                    (
                        resource.Name,
                        int(resource.ID),
                        resource,
                    )
                    for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
                ),
                resources_before,
            )
            self.assertEqual(
                int(source_document.UndoCount),
                source_undo,
            )
            self.assertEqual(int(self.document.UndoCount), target_undo)
        finally:
            if source_document.Name in App.listDocuments():
                App.closeDocument(source_document.Name)

    def test_nested_link_metadata_remains_occurrence_local_during_sync(self):
        import UtilsAssembly

        source_document = App.newDocument("AssemblyLinkOccurrenceLocalMetadataSource")
        try:
            source_document.UndoMode = False
            source_assembly = source_document.addObject(
                "Assembly::AssemblyObject",
                "OccurrenceLocalSourceAssembly",
            )
            source_assembly.Type = "Assembly"
            leaf = source_document.addObject(
                "Part::Box",
                "OccurrenceLocalLeaf",
            )
            source_document.UndoMode = True

            source_document.openTransaction("Create nested managed source link")
            try:
                source_link = source_assembly.newObject(
                    "App::Link",
                    "NestedManagedSourceLink",
                )
                source_link.LinkedObject = leaf
                source_link.addProperty(
                    "App::PropertyString",
                    "VibeCADAssemblySourceDocument",
                    "Assembly",
                )
                source_link.addProperty(
                    "App::PropertyInteger",
                    "VibeCADAssemblySourceObjectId",
                    "Assembly",
                )
                source_link.addProperty(
                    "App::PropertyString",
                    "VibeCADAssemblySourceObjectName",
                    "Assembly",
                )
                source_link.VibeCADAssemblySourceDocument = source_document.Uid
                source_link.VibeCADAssemblySourceObjectId = int(leaf.ID)
                source_link.VibeCADAssemblySourceObjectName = leaf.Name
                for property_name in (
                    "VibeCADAssemblySourceDocument",
                    "VibeCADAssemblySourceObjectId",
                    "VibeCADAssemblySourceObjectName",
                ):
                    source_link.setPropertyStatus(
                        property_name,
                        ("Hidden", "LockDynamic", "NoRecompute"),
                    )
                    source_link.setEditorMode(property_name, 2)
                source_document.publishProvisionalTimelineOperationBlock(
                    source_link,
                    [],
                )
                source_document.commitTransaction()
            except Exception:
                source_document.abortTransaction()
                raise

            source_document.saveAs(
                str(
                    pathlib.Path(self.temp_directory.name)
                    / "assembly-link-local-metadata-source.FCStd"
                )
            )
            App.setActiveDocument(self.document.Name)
            target_assembly, _components = self._create_assembly_with_components(0)
            self.document.saveAs(
                str(
                    pathlib.Path(self.temp_directory.name)
                    / "assembly-link-local-metadata-target.FCStd"
                )
            )

            self.document.openTransaction("Insert occurrence-local metadata source")
            try:
                occurrence = target_assembly.newObject(
                    "Assembly::AssemblyLink",
                    "OccurrenceLocalMetadataOccurrence",
                )
                occurrence.LinkedObject = source_assembly
                occurrence.Rigid = True
                UtilsAssembly.finalizeInsertedComponentTimeline(occurrence)
                self.document.commitTransaction()
            except Exception:
                self.document.abortTransaction()
                raise

            local_source_link = next(
                resource
                for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
                if resource.TypeId == "App::Link" and resource.LinkedObject is source_link
            )
            source_identity_names = (
                "VibeCADAssemblySourceDocument",
                "VibeCADAssemblySourceObjectId",
                "VibeCADAssemblySourceObjectName",
            )
            for property_name in source_identity_names:
                self.assertIn(
                    property_name,
                    local_source_link.PropertiesList,
                )
            self.assertEqual(
                (
                    source_link.VibeCADAssemblySourceDocument,
                    int(source_link.VibeCADAssemblySourceObjectId),
                    source_link.VibeCADAssemblySourceObjectName,
                ),
                (
                    source_document.Uid,
                    int(leaf.ID),
                    leaf.Name,
                ),
            )
            self.assertEqual(
                (
                    local_source_link.VibeCADAssemblySourceDocument,
                    int(local_source_link.VibeCADAssemblySourceObjectId),
                    local_source_link.VibeCADAssemblySourceObjectName,
                ),
                (
                    source_document.Uid,
                    int(source_link.ID),
                    source_link.Name,
                ),
            )
            self.assertEqual(
                source_link.VibeCADTimelineRole,
                "operation",
            )
            self.assertEqual(
                local_source_link.VibeCADTimelineRole,
                "resource",
            )
            self.assertIs(
                local_source_link.VibeCADTimelineOwner,
                occurrence,
            )

            source_document.openTransaction("Create retained source operation")
            try:
                retained_source = source_assembly.newObject(
                    "Part::Box",
                    "RetainedSourceOperation",
                )
                source_document.publishProvisionalTimelineOperationBlock(
                    retained_source,
                    [],
                )
                source_document.commitTransaction()
            except Exception:
                source_document.abortTransaction()
                raise

            source_timeline = source_document.getObject("VibeCADTimeline")
            source_operations_before = tuple(source_timeline.Operations)
            source_undo_before = int(source_document.UndoCount)
            self.document.openTransaction("Synchronize occurrence-local metadata")
            try:
                synchronization = UtilsAssembly.synchronizeAssemblyLinkTimelineResources(occurrence)
                self.document.commitTransaction()
            except Exception:
                self.document.abortTransaction()
                raise

            local_retained_source = next(
                resource
                for resource in synchronization["final_resources"]
                if resource.TypeId == "App::Link" and resource.LinkedObject is retained_source
            )
            self.assertEqual(
                retained_source.VibeCADTimelineRole,
                "operation",
            )
            self.assertIn(
                "VibeCADTimelineRole",
                local_retained_source.PropertiesList,
            )
            self.assertIn(
                "VibeCADTimelineOwner",
                local_retained_source.PropertiesList,
            )
            self.assertEqual(
                local_retained_source.VibeCADTimelineRole,
                "resource",
            )
            self.assertIs(
                local_retained_source.VibeCADTimelineOwner,
                occurrence,
            )
            self.assertEqual(
                (
                    local_retained_source.VibeCADAssemblySourceDocument,
                    int(local_retained_source.VibeCADAssemblySourceObjectId),
                    local_retained_source.VibeCADAssemblySourceObjectName,
                ),
                (
                    source_document.Uid,
                    int(retained_source.ID),
                    retained_source.Name,
                ),
            )
            self.assertEqual(
                tuple(source_timeline.Operations),
                source_operations_before,
            )
            self.assertEqual(
                int(source_document.UndoCount),
                source_undo_before,
            )
        finally:
            if source_document.Name in App.listDocuments():
                App.closeDocument(source_document.Name)

    def test_tracked_subassembly_sync_preserves_unmanaged_resources_and_undo(self):
        import UtilsAssembly

        (
            _assembly,
            source_assembly,
            source_shape,
            occurrence,
        ) = self._create_tracked_subassembly_occurrence()
        timeline = self.document.getObject("VibeCADTimeline")

        self.document.openTransaction("Add occurrence-owned metadata")
        try:
            metadata = self.document.addObject(
                "App::FeaturePython",
                "OccurrenceCatalogDefinition",
            )
            UtilsAssembly.markTimelineResource(metadata, occurrence)
            self.document.finalizeProvisionalTimelineOperationBlock(
                occurrence,
                [metadata],
            )
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise
        metadata_identity = (metadata.Name, int(metadata.ID))
        old_managed = tuple(UtilsAssembly._assemblyOccurrenceResources(occurrence))

        self.document.openTransaction("Synchronize source membership")
        try:
            added_source = source_assembly.newObject(
                "App::Link",
                "AddedTrackedSourceMembership",
            )
            added_source.LinkedObject = source_shape
            UtilsAssembly.finalizeInsertedComponentTimeline(
                added_source,
                following_operation=occurrence,
            )
            result = UtilsAssembly.synchronizeAssemblyLinkTimelineResources(occurrence)
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise

        final_managed = tuple(result["final_resources"])
        self.assertGreater(len(final_managed), len(old_managed))
        self.assertIs(
            self.document.getObject(metadata_identity[0]),
            metadata,
        )
        self.assertEqual(int(metadata.ID), metadata_identity[1])
        self.assertIs(metadata.VibeCADTimelineOwner, occurrence)
        final_operations = list(timeline.Operations)
        self.assertLess(
            final_operations.index(added_source),
            final_operations.index(metadata),
        )
        self.assertLess(
            final_operations.index(metadata),
            final_operations.index(occurrence),
        )
        occurrence_name = occurrence.Name
        final_names = tuple(resource.Name for resource in final_managed)

        self.document.undo()
        occurrence = self.document.getObject(occurrence_name)
        self.assertIsNotNone(occurrence)
        self.assertEqual(
            tuple(
                resource.Name for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
            ),
            tuple(resource.Name for resource in old_managed),
        )
        self.assertIsNotNone(self.document.getObject(metadata_identity[0]))

        self.document.redo()
        occurrence = self.document.getObject(occurrence_name)
        self.assertEqual(
            tuple(
                resource.Name for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
            ),
            final_names,
        )
        self.assertIs(
            self.document.getObject(metadata_identity[0]).VibeCADTimelineOwner,
            occurrence,
        )

    def test_tracked_subassembly_sync_abort_and_restore_preserve_exact_graph(self):
        import UtilsAssembly

        (
            _assembly,
            source_assembly,
            source_shape,
            occurrence,
        ) = self._create_tracked_subassembly_occurrence(rigid=False)
        before_names = tuple(
            resource.Name for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
        )
        before_timeline = tuple(
            operation.Name for operation in self.document.getObject("VibeCADTimeline").Operations
        )
        occurrence_name = occurrence.Name

        self.document.openTransaction("Cancel occurrence synchronization")
        try:
            added_source = source_assembly.newObject(
                "App::Link",
                "CanceledSourceMembership",
            )
            added_source.LinkedObject = source_shape
            UtilsAssembly.finalizeInsertedComponentTimeline(
                added_source,
                following_operation=occurrence,
            )
            UtilsAssembly.synchronizeAssemblyLinkTimelineResources(occurrence)
        finally:
            self.document.abortTransaction()

        occurrence = self.document.getObject(occurrence_name)
        self.assertEqual(
            tuple(
                resource.Name for resource in UtilsAssembly._assemblyOccurrenceResources(occurrence)
            ),
            before_names,
        )
        self.assertEqual(
            tuple(
                operation.Name
                for operation in self.document.getObject("VibeCADTimeline").Operations
            ),
            before_timeline,
        )

        file_path = pathlib.Path(self.temp_directory.name) / "assembly-link-sync.FCStd"
        self.document.recompute()
        self.document.saveAs(str(file_path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(file_path))
        Gui.activateView("Gui::View3DInventor", True)
        restored = self.document.getObject(occurrence_name)
        self.assertIsNotNone(restored)
        restored_names = tuple(
            resource.Name for resource in UtilsAssembly._assemblyOccurrenceResources(restored)
        )
        self.assertEqual(restored_names, before_names)
        restored.synchronizeContents()
        self.assertEqual(
            tuple(
                resource.Name for resource in UtilsAssembly._assemblyOccurrenceResources(restored)
            ),
            before_names,
        )

    def test_exact_shipped_assembly_action_graph_is_registered(self):
        toolbars = Gui.activeWorkbench().getToolbarItems()
        for title, expected in ASSEMBLY_RIBBON_GROUPS.items():
            self.assertIn(title, toolbars)
            self.assertEqual(tuple(toolbars[title]), expected)

        registered = set(Gui.listCommands())
        expected_commands = {
            command
            for commands in ASSEMBLY_RIBBON_GROUPS.values()
            for command in commands
            if command != "Separator"
        }
        expected_commands.update(
            child for children in ASSEMBLY_COMPOSITES.values() for child in children
        )
        expected_commands.update(VIBECAD_RIBBON_FASTENER_ADDITIONS)
        expected_commands.update(ASSEMBLY_MENU_ONLY_COMMANDS)
        self.assertTrue(expected_commands <= registered)

    def test_assembly_command_timeline_matrix_is_exhaustive_and_disjoint(self):
        expected_toolbars = {
            title: tuple(command for command in commands if command != "Separator")
            for title, commands in ASSEMBLY_RIBBON_GROUPS.items()
        }
        all_toolbar_items = Gui.activeWorkbench().getToolbarItems()
        live_toolbars = {
            title: tuple(command for command in commands if command != "Separator")
            for title, commands in all_toolbar_items.items()
            if title not in STANDARD_TOOLBAR_TITLES
        }
        self.assertEqual(live_toolbars, expected_toolbars)
        top_level_commands = {
            command for commands in live_toolbars.values() for command in commands
        }
        live_composites = {}
        for command_name in sorted(top_level_commands):
            command = Gui.Command.get(command_name)
            self.assertIsNotNone(command, command_name)
            child_ids = tuple(
                _action_command_id(action)
                for action in command.getAction()
                if not action.isSeparator()
            )
            if len(child_ids) > 1:
                live_composites[command_name] = child_ids

        self.assertEqual(live_composites, ASSEMBLY_COMPOSITES)
        menu_commands = _collect_assembly_menu_commands()
        self.assertIsNotNone(menu_commands)
        expected_menu = top_level_commands - set(ASSEMBLY_COMPOSITES)
        expected_menu.update(
            child for children in ASSEMBLY_COMPOSITES.values() for child in children
        )
        expected_menu.update(ASSEMBLY_MENU_ONLY_COMMANDS)
        self.assertEqual(menu_commands, expected_menu)

        surfaced_commands = top_level_commands | menu_commands
        surfaced_commands.update(
            child for children in live_composites.values() for child in children
        )
        surfaced_commands.update(VIBECAD_RIBBON_FASTENER_ADDITIONS)
        self.assertEqual(len(surfaced_commands), 35)
        self.assertEqual(
            set(ASSEMBLY_COMMAND_TIMELINE_BEHAVIOR),
            surfaced_commands,
        )

        primary_behaviors = {
            "standalone",
            "source-preserving",
            "replacement",
            "body-history-step",
            "in-place",
            "read-only",
        }
        operation_behaviors = {
            "standalone",
            "source-preserving",
            "replacement",
            "body-history-step",
        }
        for command, behaviors in ASSEMBLY_COMMAND_TIMELINE_BEHAVIOR.items():
            with self.subTest(command=command):
                primary = behaviors & primary_behaviors
                self.assertEqual(len(primary), 1)
                self.assertFalse(behaviors - primary_behaviors - {"operation", "resource"})
                self.assertEqual(
                    "operation" in behaviors,
                    bool(primary & operation_behaviors),
                )
                if "resource" in behaviors:
                    self.assertIn("operation", behaviors)

    def test_history_edit_dispatch_opens_each_exact_assembly_operation(self):
        import CommandCreateSimulation
        import CommandCreateView
        import JointObject
        import UtilsAssembly

        actions = Gui.Command.get("Assembly_EditHistoryOperation").getAction()
        self.assertTrue(actions)
        self.assertTrue(
            all(action.property("VibeCADTimelineOperationEditor") is True for action in actions)
        )

        assembly, components = self._create_assembly_with_components(2)
        self.document.openTransaction("Create editable Assembly operations")
        try:
            view_group = UtilsAssembly.getViewGroup(assembly)
            exploded = view_group.newObject(
                "App::FeaturePython",
                "EditableExplodedView",
            )
            CommandCreateView.ExplodedView(exploded)
            CommandCreateView.ViewProviderExplodedView(exploded.ViewObject)

            simulation_group = UtilsAssembly.getSimulationGroup(assembly)
            simulation = simulation_group.newObject(
                "App::FeaturePython",
                "EditableSimulation",
            )
            CommandCreateSimulation.Simulation(simulation)
            CommandCreateSimulation.ViewProviderSimulation(simulation.ViewObject)

            joint_group = next(
                child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
            )
            joint = joint_group.newObject(
                "App::FeaturePython",
                "EditableJoint",
            )
            JointObject.Joint(joint, 1)
            JointObject.ViewProviderJoint(joint.ViewObject)
            joint.Proxy.setJointConnectors(
                joint,
                [
                    [components[0], ["Face1", "Vertex1"]],
                    [components[1], ["Face1", "Vertex1"]],
                ],
            )
            self.document.recompute()
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise

        cases = (
            (
                exploded,
                CommandCreateView.TaskAssemblyCreateView,
                lambda: getattr(
                    exploded.Proxy.stepsChangedCallback,
                    "__self__",
                    None,
                ),
                "viewObj",
            ),
            (
                simulation,
                CommandCreateSimulation.TaskAssemblyCreateSimulation,
                lambda: getattr(
                    simulation.Proxy.motionsChangedCallback,
                    "__self__",
                    None,
                ),
                "simFeaturePy",
            ),
            (
                joint,
                JointObject.TaskAssemblyCreateJoint,
                lambda: JointObject.activeTask,
                "joint",
            ),
        )
        for operation, panel_type, panel_getter, target_attribute in cases:
            with self.subTest(operation=operation.Name):
                self.assertEqual(
                    operation.VibeCADTimelineRole,
                    "operation",
                )
                self.assertEqual(
                    operation.VibeCADTimelineEditCommand,
                    "Assembly_EditHistoryOperation",
                )
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(operation)
                self.assertTrue(Gui.isCommandActive("Assembly_EditHistoryOperation"))

                label_before = operation.Label
                undo_before = int(self.document.UndoCount)
                Gui.runCommand("Assembly_EditHistoryOperation")
                self._process_events(100)

                self.assertTrue(Gui.Control.activeDialog())
                panel = panel_getter()
                self.assertIs(type(panel), panel_type)
                self.assertIs(
                    getattr(panel, target_attribute),
                    operation,
                )
                operation.Label = f"{label_before} accepted edit"
                self._dismiss_task(accept=True)
                self.assertEqual(
                    int(self.document.UndoCount),
                    undo_before + 1,
                )
                self.assertEqual(
                    operation.Label,
                    f"{label_before} accepted edit",
                )

                self.document.undo()
                self._process_events()
                self.assertEqual(operation.Label, label_before)

    def test_existing_view_and_simulation_resource_edits_survive_history_and_reopen(self):
        import CommandCreateSimulation
        import CommandCreateView
        import UtilsAssembly

        assembly, _components = self._create_assembly_with_components(1)
        view_group = UtilsAssembly.getViewGroup(assembly)
        simulation_group = UtilsAssembly.getSimulationGroup(assembly)

        self.document.openTransaction("Create editable Assembly operation graphs")
        exploded = view_group.newObject(
            "App::FeaturePython",
            "TrackedExplodedView",
        )
        CommandCreateView.ExplodedView(exploded)
        old_move = assembly.newObject(
            "App::FeaturePython",
            "TrackedExplodedMoveOld",
        )
        CommandCreateView.ExplodedViewStep(old_move)
        UtilsAssembly.markTimelineResource(old_move, exploded)
        exploded.Group = [old_move]
        self.document.finalizeProvisionalTimelineOperationBlock(
            exploded,
            [old_move, exploded],
        )

        simulation = simulation_group.newObject(
            "App::FeaturePython",
            "TrackedSimulation",
        )
        CommandCreateSimulation.Simulation(simulation)
        old_motion = assembly.newObject(
            "App::FeaturePython",
            "TrackedMotionOld",
        )
        CommandCreateSimulation.Motion(old_motion)
        UtilsAssembly.markTimelineResource(
            old_motion,
            simulation,
        )
        simulation.Group = [old_motion]
        self.document.finalizeProvisionalTimelineOperationBlock(
            simulation,
            [old_motion, simulation],
        )
        self.document.commitTransaction()

        timeline_object = self.document.getObject("VibeCADTimeline")
        initial_history = [(obj.Name, int(obj.ID)) for obj in timeline_object.Operations]
        self.document.openTransaction("Cancel retained Assembly resource edit")
        UtilsAssembly.stageTimelineResourceGroupEdit(exploded)
        self.document.abortTransaction()
        self.assertEqual(
            [(obj.Name, int(obj.ID)) for obj in timeline_object.Operations],
            initial_history,
        )

        self.document.openTransaction("Accept retained Assembly no-op edit")
        no_op_token = UtilsAssembly.stageTimelineResourceGroupEdit(simulation)
        UtilsAssembly.finalizeTimelineResourceGroupEdit(
            simulation,
            no_op_token,
            list(simulation.Group),
        )
        self.document.commitTransaction()
        self.assertEqual(
            [(obj.Name, int(obj.ID)) for obj in timeline_object.Operations],
            initial_history,
        )

        edit_cases = (
            (
                exploded,
                old_move,
                "TrackedExplodedMoveNew",
                CommandCreateView.ExplodedViewStep,
            ),
            (
                simulation,
                old_motion,
                "TrackedMotionNew",
                CommandCreateSimulation.Motion,
            ),
        )
        new_names = []
        old_names = []
        for owner, old_resource, new_name, proxy_type in edit_cases:
            old_names.append(old_resource.Name)
            self.document.openTransaction(f"Edit {owner.Label} resource graph")
            token = UtilsAssembly.stageTimelineResourceGroupEdit(owner)
            owner.Group = []
            self.document.removeObject(old_resource.Name)
            new_resource = assembly.newObject(
                "App::FeaturePython",
                new_name,
            )
            proxy_type(new_resource)
            UtilsAssembly.markTimelineResource(
                new_resource,
                owner,
            )
            owner.Group = [new_resource]
            UtilsAssembly.finalizeTimelineResourceGroupEdit(
                owner,
                token,
                [new_resource],
            )
            self.document.commitTransaction()
            new_names.append(new_resource.Name)

            timeline = list(self.document.getObject("VibeCADTimeline").Operations)
            self.assertEqual(
                timeline[timeline.index(owner) - 1],
                new_resource,
            )

        self.document.undo()
        self.document.undo()
        for old_name, new_name in zip(old_names, new_names):
            self.assertIsNotNone(self.document.getObject(old_name))
            self.assertIsNone(self.document.getObject(new_name))
        self.document.redo()
        self.document.redo()
        for owner, new_name in zip(
            (exploded, simulation),
            new_names,
        ):
            new_resource = self.document.getObject(new_name)
            self.assertIsNotNone(new_resource)
            self.assertIs(
                new_resource.VibeCADTimelineOwner,
                owner,
            )

        file_path = pathlib.Path(self.temp_directory.name) / "assembly-resource-edits.FCStd"
        owner_names = (exploded.Name, simulation.Name)
        self.document.saveAs(str(file_path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(file_path))
        App.setActiveDocument(self.document.Name)
        self._process_events()

        timeline = list(self.document.getObject("VibeCADTimeline").Operations)
        for owner_name, new_name in zip(
            owner_names,
            new_names,
        ):
            owner = self.document.getObject(owner_name)
            resource = self.document.getObject(new_name)
            self.assertIsNotNone(owner)
            self.assertIsNotNone(resource)
            self.assertIs(resource.VibeCADTimelineOwner, owner)
            self.assertEqual(
                timeline[timeline.index(owner) - 1],
                resource,
            )

    def test_creation_commands_refuse_a_caller_owned_transaction(self):
        for command_name in ("Assembly_CreateAssembly", "Assembly_CreateBom"):
            with self.subTest(command=command_name):
                self.assertTrue(Gui.isCommandActive(command_name))
                before = tuple(self.document.Objects)
                self.document.openTransaction(f"Caller transaction for {command_name}")
                transaction = self.document.getBookedTransactionID()
                self.assertNotEqual(transaction, 0)
                self._process_events()
                self.assertFalse(Gui.isCommandActive(command_name))
                Gui.runCommand(command_name)
                self._process_events()
                self.assertEqual(tuple(self.document.Objects), before)
                self.assertEqual(
                    self.document.getBookedTransactionID(),
                    transaction,
                )
                self.document.abortTransaction()
                self.assertFalse(self.document.HasPendingTransaction)

    def test_new_assembly_is_committed_before_persistent_assembly_edit(self):
        before_undo = int(self.document.UndoCount)

        Gui.runCommand("Assembly_CreateAssembly")
        self._process_events(100)

        assemblies = [
            obj for obj in self.document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        ]
        self.assertEqual(len(assemblies), 1)
        in_edit = Gui.activeDocument().getInEdit()
        self.assertIsNotNone(in_edit)
        self.assertEqual(in_edit, assemblies[0].ViewObject)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

    def test_repeated_assembly_edit_does_not_leave_workbench_callbacks(self):
        assembly, _components = self._create_assembly_with_components(0)
        gui_document = Gui.getDocument(self.document.Name)
        self.assertIsNotNone(gui_document)

        for _attempt in range(2):
            gui_document.resetEdit()
            self._process_events()
            self.assertIsNone(gui_document.getInEdit())
            self.assertTrue(gui_document.setEdit(assembly))
            self._process_events()
            self.assertIsNotNone(gui_document.getInEdit())

        gui_document.resetEdit()
        App.closeDocument(self.document.Name)
        self.document = None
        self._process_events(100)

        Gui.activateWorkbench("PartDesignWorkbench")
        self._process_events()
        Gui.activateWorkbench("AssemblyWorkbench")
        self._process_events()

    def test_transform_dragger_is_exactly_transactional_and_reusable(self):
        assembly, components = self._create_assembly_with_components(1)
        component = components[0]
        view = Gui.activeDocument().activeView()
        view.viewAxonometric()
        view.fitAll()
        self._process_events(100)
        self._select_component_for_drag(assembly, component)

        before_placement = App.Placement(component.Placement)
        before_undo = int(self.document.UndoCount)
        before_timeline = tuple(self.document.getObject("VibeCADTimeline").Operations)

        viewport, press_position = self._press_assembly_dragger_handle(
            assembly,
            component,
        )
        release_position = self._move_pressed_dragger(
            viewport,
            press_position,
        )
        self.assertNotEqual(component.Placement, before_placement)
        self._release_dragger(viewport, release_position)

        moved_placement = App.Placement(component.Placement)
        self.assertNotEqual(moved_placement, before_placement)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)
        self.assertEqual(
            tuple(self.document.getObject("VibeCADTimeline").Operations),
            before_timeline,
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

        self.document.undo()
        self._process_events(100)
        self.assertEqual(component.Placement, before_placement)
        self.document.redo()
        self._process_events(100)
        self.assertEqual(component.Placement, moved_placement)

        self._select_component_for_drag(assembly, component)
        before_noop_undo = int(self.document.UndoCount)
        viewport, press_position = self._press_assembly_dragger_handle(
            assembly,
            component,
        )
        self._release_dragger(viewport, press_position)
        self.assertEqual(component.Placement, moved_placement)
        self.assertEqual(int(self.document.UndoCount), before_noop_undo)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

        gui_document = Gui.getDocument(self.document.Name)
        gui_document.resetEdit()
        self._process_events(100)
        self.assertIsNone(gui_document.getInEdit())
        self.assertTrue(gui_document.setEdit(assembly))
        self._process_events(100)
        self._select_component_for_drag(assembly, component)

        before_reentry_undo = int(self.document.UndoCount)
        viewport, press_position = self._press_assembly_dragger_handle(
            assembly,
            component,
        )
        release_position = self._move_pressed_dragger(
            viewport,
            press_position,
        )
        self._release_dragger(viewport, release_position)
        self.assertNotEqual(component.Placement, moved_placement)
        self.assertEqual(
            int(self.document.UndoCount),
            before_reentry_undo + 1,
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_component_can_be_dragged_directly_outside_assembly_edit(self):
        assembly, components = self._create_assembly_with_components(1)
        component = components[0]
        gui_document = Gui.getDocument(self.document.Name)
        gui_document.resetEdit()
        self._process_events(100)
        self.assertIsNone(gui_document.getInEdit())

        before_placement = App.Placement(component.Placement)
        before_undo = int(self.document.UndoCount)
        self._drag_rendered_component(component)

        self.assertNotEqual(component.Placement, before_placement)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

        moved_placement = App.Placement(component.Placement)
        undo_button = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADRibbonUndo",
        )
        self.assertIsNotNone(undo_button)
        ribbon = Gui.getMainWindow().findChild(QtGui.QWidget, "VibeCADRibbon")
        self.assertIsNotNone(ribbon)
        undo_action = next(
            (
                action
                for action in ribbon.actions()
                if _action_command_id(action) == "Std_Undo"
            ),
            None,
        )
        self.assertIsNotNone(undo_action)
        self.assertEqual(
            undo_action.shortcut().toString(),
            QtGui.QKeySequence(QtGui.QKeySequence.Undo).toString(),
        )
        self.assertTrue(undo_action.isEnabled())
        undo_action.trigger()
        self._process_events(100)
        self.assertEqual(component.Placement, before_placement)
        self.document.redo()
        self._process_events(100)
        self.assertEqual(component.Placement, moved_placement)

    def test_gui_observer_preserves_assembly_view_provider_python_type(self):
        class ChangeObserver:
            def slotChangedObject(self, _view_provider, _property_name):
                pass

        observer = ChangeObserver()
        Gui.addDocumentObserver(observer)
        try:
            box = self.document.addObject("Part::Box", "ObservedBox")
            assembly, _components = self._create_assembly_with_components(1)
        finally:
            Gui.removeDocumentObserver(observer)

        self.assertEqual(
            f"{type(box.ViewObject).__module__}.{type(box.ViewObject).__name__}",
            "PartGui.ViewProviderPartExt",
        )
        self.assertEqual(
            f"{type(assembly.ViewObject).__module__}."
            f"{type(assembly.ViewObject).__name__}",
            "AssemblyGui.ViewProviderAssembly",
        )
        self.assertTrue(hasattr(assembly.ViewObject, "isInEditMode"))

    def test_component_can_be_dragged_directly_from_model_ribbon(self):
        _assembly, components = self._create_assembly_with_components(1)
        component = components[0]
        Gui.activeDocument().resetEdit()
        Gui.activateWorkbench("PartDesignWorkbench")
        self._process_events(100)

        before_placement = App.Placement(component.Placement)
        before_undo = int(self.document.UndoCount)
        self._drag_rendered_component(component)

        self.assertNotEqual(component.Placement, before_placement)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_direct_component_drag_preserves_clicks_modifiers_and_escape(self):
        _assembly, components = self._create_assembly_with_components(1)
        component = components[0]
        Gui.activeDocument().resetEdit()
        self._process_events(100)
        before_placement = App.Placement(component.Placement)
        before_undo = int(self.document.UndoCount)

        self._drag_rendered_component(
            component,
            expect_start=False,
            move=False,
        )
        self.assertEqual(component.Placement, before_placement)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertTrue(Gui.Selection.getSelectionEx())

        self._drag_rendered_component(
            component,
            expect_start=False,
            modifiers=QtCore.Qt.ControlModifier,
        )
        self.assertEqual(component.Placement, before_placement)
        self.assertEqual(int(self.document.UndoCount), before_undo)

        viewport, target = self._drag_rendered_component(component, release=False)
        self.assertNotEqual(component.Placement, before_placement)
        QtGui.QApplication.sendEvent(
            viewport,
            QtGui.QKeyEvent(
                QtCore.QEvent.KeyPress,
                QtCore.Qt.Key_Escape,
                QtCore.Qt.NoModifier,
            ),
        )
        self._process_events(100)
        self._send_mouse(
            viewport,
            QtCore.QEvent.MouseButtonRelease,
            target,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoButton,
        )
        self._process_events(100)
        self.assertEqual(component.Placement, before_placement)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_grounded_component_cannot_be_dragged_directly(self):
        import JointObject

        assembly, components = self._create_assembly_with_components(1)
        component = components[0]
        joint_group = next(
            child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
        )
        self.document.UndoMode = False
        grounded_joint = joint_group.newObject(
            "App::FeaturePython",
            "DirectManipulationGround",
        )
        JointObject.GroundedJoint(grounded_joint, component)
        JointObject.ViewProviderGroundedJoint(grounded_joint.ViewObject)
        self.document.recompute()
        self.document.UndoMode = True
        self._process_events(100)
        self.assertTrue(assembly.isPartGrounded(component))
        Gui.Selection.clearSelection()
        Gui.activeDocument().resetEdit()
        self._process_events(100)

        before_placement = App.Placement(component.Placement)
        before_undo = int(self.document.UndoCount)
        self._drag_rendered_component(component, expect_start=False)

        self.assertEqual(component.Placement, before_placement)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_revolute_component_drag_preserves_its_joint(self):
        import JointObject

        assembly, components = self._create_assembly_with_components(2)
        moving_component, fixed_component = components
        joint_group = next(
            child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
        )
        self.document.UndoMode = False
        grounded_joint = joint_group.newObject(
            "App::FeaturePython",
            "DirectManipulationGround",
        )
        JointObject.GroundedJoint(grounded_joint, fixed_component)
        JointObject.ViewProviderGroundedJoint(grounded_joint.ViewObject)
        revolute_joint = joint_group.newObject(
            "App::FeaturePython",
            "DirectManipulationRevolute",
        )
        JointObject.Joint(revolute_joint, 1)
        JointObject.ViewProviderJoint(revolute_joint.ViewObject)
        revolute_joint.Proxy.setJointConnectors(
            revolute_joint,
            [
                [fixed_component, ["Face1"]],
                [moving_component, ["Face2"]],
            ],
        )
        self.document.recompute()
        assembly.solve()
        self.document.recompute()
        joint_group.Visibility = False
        self.document.UndoMode = True
        Gui.Selection.clearSelection()
        Gui.activeDocument().resetEdit()
        self._process_events(100)

        before_fixed = App.Placement(fixed_component.Placement)
        before_moving = App.Placement(moving_component.Placement)
        before_undo = int(self.document.UndoCount)
        self._drag_rendered_component(moving_component)

        first_joint_frame = fixed_component.Placement * revolute_joint.Placement1
        second_joint_frame = moving_component.Placement * revolute_joint.Placement2
        first_axis = first_joint_frame.Rotation.multVec(App.Vector(0, 0, 1))
        second_axis = second_joint_frame.Rotation.multVec(App.Vector(0, 0, 1))
        self.assertEqual(fixed_component.Placement, before_fixed)
        self.assertNotEqual(moving_component.Placement, before_moving)
        self.assertLess(
            (first_joint_frame.Base - second_joint_frame.Base).Length,
            1e-5,
        )
        self.assertAlmostEqual(abs(first_axis.dot(second_axis)), 1.0, places=6)
        self.assertTrue(assembly.isValid())
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_direct_component_drag_does_not_take_over_a_caller_transaction(self):
        _assembly, components = self._create_assembly_with_components(1)
        component = components[0]
        Gui.activeDocument().resetEdit()
        self._process_events(100)
        before_placement = App.Placement(component.Placement)

        self.document.openTransaction("Caller-owned direct manipulation conflict")
        caller_transaction = self.document.getBookedTransactionID()
        self._drag_rendered_component(
            component,
            expect_start=False,
            expected_transaction=caller_transaction,
        )

        self.assertEqual(component.Placement, before_placement)
        self.assertEqual(
            self.document.getBookedTransactionID(),
            caller_transaction,
        )
        self.document.abortTransaction()
        self.assertFalse(self.document.HasPendingTransaction)

    def test_direct_component_drag_cancels_when_leaving_supported_ribbons(self):
        _assembly, components = self._create_assembly_with_components(1)
        component = components[0]
        Gui.activeDocument().resetEdit()
        self._process_events(100)
        before_placement = App.Placement(component.Placement)
        before_undo = int(self.document.UndoCount)

        viewport, target = self._drag_rendered_component(component, release=False)
        self.assertNotEqual(component.Placement, before_placement)
        Gui.activateWorkbench("PartDesignWorkbench")
        self._process_events(100)

        self.assertEqual(component.Placement, before_placement)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertFalse(self.document.HasPendingTransaction)
        self._send_mouse(
            viewport,
            QtCore.QEvent.MouseButtonRelease,
            target,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoButton,
        )

        viewport, target = self._drag_rendered_component(component, release=False)
        self.assertNotEqual(component.Placement, before_placement)
        Gui.activateWorkbench("TechDrawWorkbench")
        self._process_events(100)

        self.assertEqual(component.Placement, before_placement)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertFalse(self.document.HasPendingTransaction)
        self._send_mouse(
            viewport,
            QtCore.QEvent.MouseButtonRelease,
            target,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoButton,
        )

        Gui.activateWorkbench("AssemblyWorkbench")
        self._process_events(100)
        self._drag_rendered_component(component)
        self.assertNotEqual(component.Placement, before_placement)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

    def test_activate_assembly_refuses_a_caller_owned_transaction(self):
        Gui.runCommand("Assembly_CreateAssembly")
        self._process_events(100)
        Gui.activeDocument().resetEdit()
        self._process_events()
        self.assertTrue(Gui.isCommandActive("Assembly_ActivateAssembly"))

        self.document.openTransaction("Caller-owned assembly change")
        transaction = int(self.document.getBookedTransactionID())
        self.assertNotEqual(transaction, 0)
        self._process_events()
        self.assertFalse(Gui.isCommandActive("Assembly_ActivateAssembly"))

        Gui.runCommand("Assembly_ActivateAssembly")
        self._process_events()
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertEqual(
            self.document.getBookedTransactionID(),
            transaction,
        )

        App.closeActiveTransaction(True, transaction)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_future_assembly_cannot_be_activated_or_used_by_commands(self):
        import UtilsAssembly

        Gui.runCommand("Assembly_CreateAssembly")
        self._process_events(100)
        root = UtilsAssembly.activeAssembly()
        self.assertIsNotNone(root)

        Gui.runCommand("Assembly_CreateAssembly")
        self._process_events(100)
        assemblies = [
            obj for obj in self.document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        ]
        self.assertEqual(len(assemblies), 2)
        nested = next(obj for obj in assemblies if obj is not root)

        Gui.activeDocument().resetEdit()
        timeline = self.document.getObject("VibeCADTimeline")
        nested_index = list(timeline.Operations).index(nested)
        timeline.Position = nested_index
        self.document.recompute()
        self._process_events()

        self.assertFalse(UtilsAssembly.isTimelineOperationActive(nested))
        self.assertTrue(Gui.isCommandActive("Assembly_ActivateAssembly"))
        Gui.runCommand("Assembly_ActivateAssembly")
        self._process_events(100)
        self.assertIs(UtilsAssembly.activeAssembly(), root)
        self.assertTrue(Gui.isCommandActive("Assembly_InsertLink"))
        self.assertFalse(Gui.isCommandActive("Assembly_CreateView"))

    def test_suppressed_joint_and_owned_resource_are_not_active_inputs(self):
        import JointObject
        import UtilsAssembly

        assembly, _components = self._create_assembly_with_components(1)
        joint_group = next(
            child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
        )
        self.document.openTransaction("Create suppressible Assembly operation")
        try:
            joint = joint_group.newObject(
                "App::FeaturePython",
                "SuppressedJointOperation",
            )
            JointObject.Joint(joint, 0)
            JointObject.ViewProviderJoint(joint.ViewObject)
            resource = assembly.newObject(
                "App::FeaturePython",
                "SuppressedJointResource",
            )
            UtilsAssembly.markTimelineResource(resource, joint)
            self.document.recompute()
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise

        self.assertTrue(UtilsAssembly.isTimelineOperationActive(joint))
        self.assertTrue(UtilsAssembly.isTimelineOperationActive(resource))
        joint.Suppressed = True
        self.document.recompute()
        self.assertFalse(UtilsAssembly.isTimelineOperationActive(joint))
        self.assertFalse(UtilsAssembly.isTimelineOperationActive(resource))

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(joint)
        self._process_events()
        self.assertFalse(Gui.isCommandActive("Assembly_EditHistoryOperation"))
        self.assertFalse(joint.ViewObject.Proxy.doubleClicked(joint.ViewObject))
        self.assertFalse(Gui.Control.activeDialog())

    def test_motion_popup_owns_one_exact_transaction_and_refuses_another(self):
        import CommandCreateSimulation
        import JointObject

        assembly, components = self._create_assembly_with_components(2)
        joint_group = next(
            child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
        )
        simulation_group = next(
            (child for child in assembly.Group if child.TypeId == "Assembly::SimulationGroup"),
            None,
        )
        if simulation_group is None:
            import UtilsAssembly

            simulation_group = UtilsAssembly.getSimulationGroup(assembly)
        self.document.openTransaction("Create Assembly motion transaction fixture")
        try:
            joint = joint_group.newObject(
                "App::FeaturePython",
                "MotionTransactionJoint",
            )
            JointObject.Joint(joint, 3)
            joint.Proxy.setJointConnectors(
                joint,
                [
                    [components[0], ["Face1", "Vertex1"]],
                    [components[1], ["Face1", "Vertex1"]],
                ],
            )
            simulation = simulation_group.newObject(
                "App::FeaturePython",
                "MotionTransactionSimulation",
            )
            CommandCreateSimulation.Simulation(simulation)
            motion = assembly.newObject(
                "App::FeaturePython",
                "MotionTransactionResource",
            )
            CommandCreateSimulation.Motion(
                motion,
                joint=joint,
                formula="initialValue",
            )
            CommandCreateSimulation.ViewProviderMotion(motion.ViewObject)
            simulation.Group = [motion]
            self.document.recompute()
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise

        class AcceptedMotionDialog:
            calls = 0

            def __init__(
                self,
                _assembly,
                _motion_type,
                joint,
                _formula,
            ):
                type(self).calls += 1
                self.motionType = "Linear"
                self.joint = joint
                self.formula = "initialValue + time"

            def exec_(self):
                return True

        original_dialog = CommandCreateSimulation.MotionEditDialog
        CommandCreateSimulation.MotionEditDialog = AcceptedMotionDialog
        try:
            before_undo = int(self.document.UndoCount)
            self.assertTrue(motion.ViewObject.Proxy.openEditDialog())
            self.assertEqual(motion.MotionType, "Linear")
            self.assertEqual(motion.Formula, "initialValue + time")
            self.assertEqual(int(self.document.UndoCount), before_undo + 1)
            self.assertEqual(self.document.getBookedTransactionID(), 0)
            self.assertFalse(self.document.HasPendingTransaction)

            self.document.openTransaction("Unrelated caller-owned transaction")
            transaction = int(self.document.getBookedTransactionID())
            self.assertNotEqual(transaction, 0)
            self.assertFalse(motion.ViewObject.Proxy.openEditDialog())
            self.assertEqual(AcceptedMotionDialog.calls, 1)
            self.assertEqual(
                self.document.getBookedTransactionID(),
                transaction,
            )
            App.closeActiveTransaction(True, transaction)
        finally:
            CommandCreateSimulation.MotionEditDialog = original_dialog

    def test_bill_of_materials_cancel_is_an_exact_rollback(self):
        before = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)

        Gui.runCommand("Assembly_CreateBom")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertTrue(self.document.HasPendingTransaction)
        self.assertGreater(len(self.document.Objects), len(before))

        self._dismiss_task(accept=False)
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(int(self.document.UndoCount), before_undo)

    def test_bill_of_materials_cancel_is_bound_to_its_launch_document(self):
        before = tuple(self.document.Objects)
        task_document_name = self.document.Name
        other = None

        Gui.runCommand("Assembly_CreateBom")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        task = Gui.Control.activeTaskDialog()
        transaction = int(self.document.getBookedTransactionID())
        self.assertNotEqual(transaction, 0)

        try:
            other = App.newDocument("VibeCADAssemblyOtherDocument")
            probe = other.addObject("App::FeaturePython", "OtherProbe")
            probe.addProperty("App::PropertyString", "State")
            probe.State = "untouched"
            other.recompute()
            App.setActiveDocument(other.Name)
            self._process_events(60)

            # The task is hidden with its launch document, but its exact
            # transaction still belongs to that document and can be canceled
            # without touching the newly active document.
            task.reject()
            self._process_events(100)

            self.assertEqual(
                self.document.getBookedTransactionID(),
                0,
            )
            self.assertFalse(self.document.HasPendingTransaction)
            self.assertEqual(tuple(self.document.Objects), before)
            self.assertEqual(probe.State, "untouched")
            self.assertEqual(other.getBookedTransactionID(), 0)
            self.assertFalse(other.HasPendingTransaction)
        finally:
            if self.document.getBookedTransactionID() == transaction:
                App.closeActiveTransaction(True, transaction)
            if other is not None and other.Name in App.listDocuments():
                App.closeDocument(other.Name)
            if task_document_name in App.listDocuments():
                App.setActiveDocument(task_document_name)
            self._process_events(60)

    def test_bill_of_materials_accept_keeps_one_durable_result(self):
        before_undo = int(self.document.UndoCount)

        Gui.runCommand("Assembly_CreateBom")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        provisional = tuple(self.document.Objects)
        self.assertTrue(any(obj.TypeId == "Assembly::BomObject" for obj in provisional))

        self._dismiss_task(accept=True)
        results = [obj for obj in self.document.Objects if obj.TypeId == "Assembly::BomObject"]
        self.assertEqual(len(results), 1)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

    def test_bill_of_materials_keeps_its_exact_assembly_owner(self):
        import CommandCreateBom

        assembly, _components = self._create_assembly_with_components(1)
        Gui.runCommand("Assembly_CreateBom")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())

        self._dismiss_task(accept=True)
        results = [obj for obj in self.document.Objects if obj.TypeId == "Assembly::BomObject"]
        self.assertEqual(len(results), 1)
        self.assertIs(
            CommandCreateBom._findBomAssembly(results[0]),
            assembly,
        )

    def test_empty_insert_component_accept_and_cancel_leave_no_operation(self):
        self._create_assembly_with_components(1)

        for accept in (False, True):
            with self.subTest(accept=accept):
                before = tuple(self.document.Objects)
                before_undo = int(self.document.UndoCount)
                self.assertTrue(Gui.isCommandActive("Assembly_InsertLink"))

                Gui.runCommand("Assembly_InsertLink")
                self._process_events(100)

                self.assertTrue(Gui.Control.activeDialog())
                self.assertNotEqual(
                    self.document.getBookedTransactionID(),
                    0,
                )
                self._dismiss_task(accept=accept)
                self.assertEqual(tuple(self.document.Objects), before)
                self.assertEqual(int(self.document.UndoCount), before_undo)

    def test_new_part_accept_creates_one_empty_editable_component(self):
        assembly, _components = self._create_assembly_with_components(0)
        joint_group = next(
            child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
        )
        joints_before = tuple(joint_group.Group)
        self.document.saveAs(
            str(pathlib.Path(self.temp_directory.name) / "VibeCADAssemblyRibbonTools.FCStd")
        )
        before = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)

        self.assertTrue(Gui.isCommandActive("Assembly_InsertNewPart"))
        Gui.runCommand("Assembly_InsertNewPart")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())

        new_file = next(
            (
                checkbox
                for checkbox in Gui.getMainWindow().findChildren(QtGui.QCheckBox)
                if checkbox.isVisible() and "new file" in checkbox.text().lower()
            ),
            None,
        )
        self.assertIsNotNone(new_file)
        new_file.setChecked(False)
        self._dismiss_task(accept=True)

        new_parts = [
            obj for obj in self.document.Objects if obj.TypeId == "App::Part" and obj not in before
        ]
        self.assertEqual(len(new_parts), 1)
        bodies = [child for child in new_parts[0].Group if child.TypeId == "PartDesign::Body"]
        self.assertEqual(len(bodies), 1)
        self.assertEqual(tuple(bodies[0].Group), ())
        self.assertIs(
            Gui.getDocument(self.document.Name).activeView().getActiveObject("pdbody"),
            bodies[0],
        )
        links = [
            child
            for obj in self.document.Objects
            if obj.TypeId == "Assembly::AssemblyObject"
            for child in obj.Group
            if child.TypeId == "App::Link" and child.LinkedObject is new_parts[0]
        ]
        self.assertEqual(len(links), 1)
        part = new_parts[0]
        body = bodies[0]
        occurrence = links[0]
        self.assertEqual(part.VibeCADTimelineRole, "operation")
        self.assertEqual(body.VibeCADTimelineRole, "resource")
        self.assertIs(body.VibeCADTimelineOwner, part)
        self.assertEqual(occurrence.VibeCADTimelineRole, "resource")
        self.assertIs(occurrence.VibeCADTimelineOwner, part)
        timeline = self.document.getObject("VibeCADTimeline")
        operations = list(timeline.Operations)
        part_index = operations.index(part)
        self.assertEqual(
            operations[part_index - 2 : part_index + 1],
            [body, occurrence, part],
        )
        self.assertEqual(
            [
                operation
                for operation in (occurrence, body, part)
                if getattr(operation, "VibeCADTimelineRole", "") != "resource"
            ],
            [part],
        )
        self.assertEqual(tuple(joint_group.Group), joints_before)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

        self.document.undo()
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before)

    def test_new_part_cancel_is_an_exact_rollback(self):
        self._create_assembly_with_components(0)
        self.document.saveAs(
            str(pathlib.Path(self.temp_directory.name) / "VibeCADAssemblyCancel.FCStd")
        )
        before = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)

        Gui.runCommand("Assembly_InsertNewPart")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertGreater(len(self.document.Objects), len(before))
        import JointObject

        preview_joint = JointObject.activeTask.joint
        self.assertNotIn(
            preview_joint,
            list(self.document.getObject("VibeCADTimeline").Operations),
        )

        self._dismiss_task(accept=False)
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(int(self.document.UndoCount), before_undo)

    def test_canceled_external_part_save_leaves_no_orphan_document(self):
        import JointObject

        self._create_assembly_with_components(0)
        self.document.saveAs(
            str(pathlib.Path(self.temp_directory.name) / "VibeCADAssemblyExternalCancel.FCStd")
        )
        documents_before = set(App.listDocuments())
        objects_before = tuple(self.document.Objects)
        undo_before = int(self.document.UndoCount)

        Gui.runCommand("Assembly_InsertNewPart")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        task = JointObject.activeTask
        self.assertIsNotNone(task)
        save_attempts = []
        save_external_document = task._saveCreatedExternalDocument

        def cancel_save(document):
            save_attempts.append(document.Name)
            return save_external_document(
                document,
                save_callback=lambda: False,
            )

        task._saveCreatedExternalDocument = cancel_save
        Gui.Control.activeTaskDialog().accept()
        self._process_events(100)

        self.assertEqual(len(save_attempts), 1)
        self.assertEqual(set(App.listDocuments()), documents_before)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertNotEqual(self.document.getBookedTransactionID(), 0)

        Gui.Control.activeTaskDialog().reject()
        self._process_events(100)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(int(self.document.UndoCount), undo_before)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_correctable_new_part_failure_reuses_one_created_part(self):
        import JointObject

        self._create_assembly_with_components(0)
        self.document.saveAs(
            str(pathlib.Path(self.temp_directory.name) / "VibeCADAssemblyNewPartRetry.FCStd")
        )
        Gui.runCommand("Assembly_InsertNewPart")
        self._process_events(100)
        task = JointObject.activeTask
        self.assertIsNotNone(task)
        task.createInNewFileCheck.setChecked(False)

        activate_created_body = task._activateCreatedBody
        activation_attempts = 0

        def fail_once(body):
            nonlocal activation_attempts
            activation_attempts += 1
            if activation_attempts == 1:
                raise RuntimeError("intentional correctable activation failure")
            activate_created_body(body)

        task._activateCreatedBody = fail_once
        Gui.Control.activeTaskDialog().accept()
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        created_part = task.created_part
        self.assertIsNotNone(created_part)
        self.assertEqual(
            sum(obj.TypeId == "App::Part" for obj in self.document.Objects),
            1,
        )

        Gui.Control.activeTaskDialog().accept()
        self._process_events(100)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(activation_attempts, 2)
        self.assertIs(self.document.getObject(created_part.Name), created_part)
        self.assertEqual(
            sum(obj.TypeId == "App::Part" for obj in self.document.Objects),
            1,
        )
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_created_body_activation_uses_the_owning_gui_document(self):
        import CommandInsertNewPart

        assembly_view = Gui.getDocument(self.document.Name).activeView()
        self.assertIsNone(assembly_view.getActiveObject("pdbody"))
        external = App.newDocument("VibeCADAssemblyExternalPart")
        try:
            App.setActiveDocument(external.Name)
            Gui.activateView("Gui::View3DInventor", True)
            part = external.addObject("App::Part", "ExternalPart")
            body = part.newObject("PartDesign::Body", "Body")
            external.recompute()
            App.setActiveDocument(self.document.Name)

            CommandInsertNewPart.TaskAssemblyNewPart._activateCreatedBody(body)

            self.assertIsNone(assembly_view.getActiveObject("pdbody"))
            self.assertIs(
                Gui.getDocument(external.Name).activeView().getActiveObject("pdbody"),
                body,
            )
        finally:
            if external.Name in App.listDocuments():
                App.closeDocument(external.Name)
            App.setActiveDocument(self.document.Name)
            self._process_events()

    def test_subassembly_occurrence_owns_only_its_native_clone_graph(self):
        import UtilsAssembly

        assembly, _components = self._create_assembly_with_components(0)
        source_assembly = self.document.addObject(
            "Assembly::AssemblyObject",
            "SourceAssembly",
        )
        source_assembly.Type = "Assembly"
        source_shape = self.document.addObject(
            "Part::Feature",
            "SourceAssemblyShape",
        )
        source_shape.Shape = Part.makeBox(6, 7, 8)
        source_component = source_assembly.newObject(
            "App::Link",
            "SourceAssemblyComponent",
        )
        source_component.LinkedObject = source_shape
        self.document.recompute()

        timeline = self.document.getObject("VibeCADTimeline")
        before_operations = tuple(timeline.Operations)
        before_undo = int(self.document.UndoCount)

        self.document.openTransaction("Insert subassembly occurrence")
        try:
            occurrence = assembly.newObject(
                "Assembly::AssemblyLink",
                "InsertedSubassembly",
            )
            occurrence.LinkedObject = source_assembly
            self.document.recompute()
            structural_resources = UtilsAssembly._assemblyOccurrenceResources(occurrence)
            self.assertTrue(structural_resources)
            UtilsAssembly.finalizeInsertedComponentTimeline(occurrence)
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise

        operations = list(timeline.Operations)
        resources = [resource for resource in structural_resources if resource in operations]
        self.assertTrue(resources)
        self.assertEqual(occurrence.VibeCADTimelineRole, "operation")
        for resource in resources:
            self.assertEqual(resource.VibeCADTimelineRole, "resource")
            self.assertIs(resource.VibeCADTimelineOwner, occurrence)
        occurrence_index = operations.index(occurrence)
        self.assertEqual(
            operations[occurrence_index - len(resources) : occurrence_index + 1],
            resources + [occurrence],
        )
        self.assertEqual(
            tuple(operations[: len(before_operations)]),
            before_operations,
        )
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

        resource_names = [resource.Name for resource in resources]
        occurrence_name = occurrence.Name
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(occurrence)
        Gui.runCommand("Std_Delete", 0)
        self._process_events()
        self.assertIsNone(self.document.getObject(occurrence_name))
        for name in resource_names:
            self.assertIsNone(self.document.getObject(name))

        self.document.undo()
        self._process_events()
        occurrence = self.document.getObject(occurrence_name)
        self.assertIsNotNone(occurrence)
        for name in resource_names:
            resource = self.document.getObject(name)
            self.assertIsNotNone(resource)
            self.assertIs(resource.VibeCADTimelineOwner, occurrence)

    def test_subassembly_replay_preserves_occurrence_and_resource_types(self):
        import CommandCreateJoint
        import CommandInsertLink
        import UtilsAssembly

        assembly, _components = self._create_assembly_with_components(0)
        source_assembly = self.document.addObject(
            "Assembly::AssemblyObject",
            "ReplaySourceAssembly",
        )
        source_assembly.Type = "Assembly"
        source_shape = self.document.addObject(
            "Part::Feature",
            "ReplaySourceShape",
        )
        source_shape.Shape = Part.makeBox(4, 5, 6)
        source_component = source_assembly.newObject(
            "App::Link",
            "ReplaySourceComponent",
        )
        source_component.LinkedObject = source_shape
        self.document.recompute()

        self.document.openTransaction("Insert source occurrence")
        try:
            occurrence = assembly.newObject(
                "Assembly::AssemblyLink",
                "ReplaySubassembly",
            )
            occurrence.LinkedObject = source_assembly
            occurrence.Rigid = False
            self.document.recompute()
            UtilsAssembly.finalizeInsertedComponentTimeline(occurrence)
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise

        source_resources = UtilsAssembly._assemblyOccurrenceResources(occurrence)
        self.assertTrue(source_resources)
        replay_trace = CommandInsertLink.buildInsertedComponentReplayTrace(
            self.document,
            assembly,
            [
                {
                    "addedObject": occurrence,
                    "translation": App.Vector(1, 2, 3),
                }
            ],
        )
        self.assertIn("'Assembly::AssemblyLink'", replay_trace)
        self.assertIn("item.Rigid = False", replay_trace)

        before_objects = set(self.document.Objects)
        before_undo = int(self.document.UndoCount)
        namespace = {
            "App": App,
            "CommandCreateJoint": CommandCreateJoint,
            "UtilsAssembly": UtilsAssembly,
        }
        self.document.openTransaction("Replay subassembly occurrence")
        try:
            exec(
                compile(
                    replay_trace,
                    "<assembly-insert-replay>",
                    "exec",
                ),
                namespace,
            )
            self.document.recompute()
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise

        replayed = namespace["item"]
        self.assertEqual(replayed.TypeId, occurrence.TypeId)
        self.assertIs(replayed.LinkedObject, source_assembly)
        self.assertFalse(replayed.Rigid)
        self.assertEqual(
            replayed.Placement.Base,
            App.Vector(),
        )
        replay_resources = UtilsAssembly._assemblyOccurrenceResources(replayed)
        replayed_components = [
            resource for resource in replay_resources if resource.TypeId != "Assembly::JointGroup"
        ]
        self.assertTrue(replayed_components)
        self.assertTrue(
            any(
                resource.Placement.Base == App.Vector(1, 2, 3)
                for resource in replayed_components
                if "Placement" in resource.PropertiesList
            )
        )
        self.assertEqual(
            sorted(resource.TypeId for resource in replay_resources),
            sorted(resource.TypeId for resource in source_resources),
        )
        operations = list(self.document.getObject("VibeCADTimeline").Operations)
        replay_index = operations.index(replayed)
        ordered_resources = sorted(
            replay_resources,
            key=operations.index,
        )
        self.assertEqual(
            operations[replay_index - len(ordered_resources) : replay_index + 1],
            ordered_resources + [replayed],
        )
        for resource in replay_resources:
            self.assertEqual(resource.VibeCADTimelineRole, "resource")
            self.assertIs(resource.VibeCADTimelineOwner, replayed)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

        replay_names = [object_.Name for object_ in set(self.document.Objects) - before_objects]
        self.document.undo()
        self._process_events()
        for name in replay_names:
            self.assertIsNone(self.document.getObject(name))
        self.assertIsNotNone(self.document.getObject(occurrence.Name))

    def test_new_part_block_precedes_its_independent_completed_joint(self):
        import JointObject
        import UtilsAssembly

        assembly, components = self._create_assembly_with_components(1)
        joint_group = next(
            child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
        )
        before_undo = int(self.document.UndoCount)

        self.document.openTransaction("New part with joint")
        try:
            joint = joint_group.newObject(
                "App::FeaturePython",
                "NewPartJoint",
            )
            self.document.classifyProvisionalTimelineInternalObject(joint)
            JointObject.Joint(
                joint,
                0,
                register_timeline_editor=False,
            )
            JointObject.ViewProviderJoint(joint.ViewObject)
            self.assertNotIn(
                joint,
                list(self.document.getObject("VibeCADTimeline").Operations),
            )

            occurrence = assembly.newObject(
                "App::Link",
                "NewPartOccurrence",
            )
            part = self.document.addObject("App::Part", "NewPartDefinition")
            body = part.newObject("PartDesign::Body", "Body")
            occurrence.LinkedObject = part

            UtilsAssembly.finalizeNewPartTimeline(
                part,
                body,
                occurrence,
            )
            UtilsAssembly.markTimelineOperationEditor(
                joint,
                "Assembly_EditHistoryOperation",
            )
            self.document.finalizeProvisionalTimelineOperationBlock(
                joint,
                [joint],
            )
            joint.Proxy.setJointConnectors(
                joint,
                [
                    [occurrence, [""]],
                    [components[0], [""]],
                ],
            )
            self.document.recompute()
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise

        timeline = self.document.getObject("VibeCADTimeline")
        operations = list(timeline.Operations)
        joint_index = operations.index(joint)
        self.assertEqual(
            operations[joint_index - 3 : joint_index + 1],
            [body, occurrence, part, joint],
        )
        self.assertEqual(part.VibeCADTimelineRole, "operation")
        self.assertEqual(body.VibeCADTimelineRole, "resource")
        self.assertIs(body.VibeCADTimelineOwner, part)
        self.assertEqual(occurrence.VibeCADTimelineRole, "resource")
        self.assertIs(occurrence.VibeCADTimelineOwner, part)
        self.assertEqual(joint.VibeCADTimelineRole, "operation")
        if "VibeCADTimelineOwner" in joint.PropertiesList:
            self.assertIsNone(joint.VibeCADTimelineOwner)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

        part_name = part.Name
        body_name = body.Name
        occurrence_name = occurrence.Name
        joint_name = joint.Name
        self.document.undo()
        self._process_events()
        for name in (part_name, body_name, occurrence_name, joint_name):
            self.assertIsNone(self.document.getObject(name))
        self.document.redo()
        self._process_events()
        part = self.document.getObject(part_name)
        body = self.document.getObject(body_name)
        occurrence = self.document.getObject(occurrence_name)
        joint = self.document.getObject(joint_name)
        operations = list(self.document.getObject("VibeCADTimeline").Operations)
        joint_index = operations.index(joint)
        self.assertEqual(
            operations[joint_index - 3 : joint_index + 1],
            [body, occurrence, part, joint],
        )

        path = pathlib.Path(self.temp_directory.name) / "new-part-joint-timeline.FCStd"
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        App.setActiveDocument(self.document.Name)
        self._process_events()

        part = self.document.getObject(part_name)
        body = self.document.getObject(body_name)
        occurrence = self.document.getObject(occurrence_name)
        joint = self.document.getObject(joint_name)
        operations = list(self.document.getObject("VibeCADTimeline").Operations)
        joint_index = operations.index(joint)
        self.assertEqual(
            operations[joint_index - 3 : joint_index + 1],
            [body, occurrence, part, joint],
        )
        self.assertIs(body.VibeCADTimelineOwner, part)
        self.assertIs(occurrence.VibeCADTimelineOwner, part)

    def test_toggle_grounded_is_one_exact_undoable_operation(self):
        assembly, components = self._create_assembly_with_components(1)
        joint_group = next(
            child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
        )
        Gui.Selection.addSelection(components[0])
        self._process_events()
        before_undo = int(self.document.UndoCount)

        self.assertTrue(Gui.isCommandActive("Assembly_ToggleGrounded"))
        Gui.runCommand("Assembly_ToggleGrounded")
        self._process_events()

        grounded = [joint for joint in joint_group.Group if hasattr(joint, "ObjectToGround")]
        self.assertEqual(len(grounded), 1)
        self.assertEqual(grounded[0].ObjectToGround, components[0])
        self.assertEqual(
            grounded[0].VibeCADTimelineRole,
            "operation",
        )
        if "VibeCADTimelineOwner" in grounded[0].PropertiesList:
            self.assertIsNone(grounded[0].VibeCADTimelineOwner)
        self.assertIn(
            grounded[0],
            list(self.document.getObject("VibeCADTimeline").Operations),
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

        self.document.undo()
        self._process_events()
        self.assertFalse(any(hasattr(joint, "ObjectToGround") for joint in joint_group.Group))

    def test_component_delete_decline_is_pure_and_accept_is_atomic(self):
        (
            _assembly,
            components,
            joint,
            blocker,
        ) = self._create_connected_component_delete_fixture()
        component = components[0]
        component_name = component.Name
        component_id = int(component.ID)
        joint_name = joint.Name
        joint_id = int(joint.ID)
        before_objects = tuple(self.document.Objects)
        before_timeline = tuple(self.document.getObject("VibeCADTimeline").Operations)
        before_visibility = {obj.ID: bool(obj.Visibility) for obj in before_objects}
        before_undo = int(self.document.UndoCount)
        before_redo = int(self.document.RedoCount)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(component)
        declined = self._choose_message_box(QtGui.QMessageBox.No)
        Gui.runCommand("Std_Delete", 0)
        self._process_events()

        self.assertTrue(declined["clicked"])
        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertIs(
            self.document.getObject(component_name),
            component,
        )
        self.assertIs(self.document.getObject(joint_name), joint)
        self.assertIs(blocker.ProtectedComponent, component)
        self.assertEqual(
            tuple(self.document.getObject("VibeCADTimeline").Operations),
            before_timeline,
        )
        self.assertEqual(
            {obj.ID: bool(obj.Visibility) for obj in self.document.Objects},
            before_visibility,
        )
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertEqual(int(self.document.RedoCount), before_redo)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(component)
        accepted = self._choose_message_box(QtGui.QMessageBox.Yes)
        Gui.runCommand("Std_Delete", 0)
        self._process_events()

        self.assertTrue(accepted["clicked"])
        self.assertIsNone(self.document.getObject(component_name))
        self.assertIsNone(self.document.getObject(joint_name))
        self.assertIsNone(blocker.ProtectedComponent)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

        self.document.undo()
        self._process_events()
        restored_component = self.document.getObject(component_name)
        restored_joint = self.document.getObject(joint_name)
        self.assertIsNotNone(restored_component)
        self.assertIsNotNone(restored_joint)
        self.assertEqual(int(restored_component.ID), component_id)
        self.assertEqual(int(restored_joint.ID), joint_id)
        self.assertIs(
            blocker.ProtectedComponent,
            restored_component,
        )
        self.assertIs(
            restored_joint.Reference1[0],
            restored_component,
        )

    def test_component_delete_rejects_same_name_companion_replacement(self):
        (
            assembly,
            components,
            joint,
            blocker,
        ) = self._create_connected_component_delete_fixture()
        component = components[0]
        component_name = component.Name
        component_id = int(component.ID)
        joint_name = joint.Name
        joint_id = int(joint.ID)
        joint_group = next(
            child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
        )
        replacement = {}

        def replace_planned_joint():
            self.document.removeObject(joint_name)
            replacement["joint"] = joint_group.newObject(
                "App::FeaturePython",
                joint_name,
            )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(component)
        confirmed = self._choose_message_box(
            QtGui.QMessageBox.Yes,
            replace_planned_joint,
        )
        error_closed = self._choose_message_box(QtGui.QMessageBox.Ok)
        before_undo = int(self.document.UndoCount)
        Gui.runCommand("Std_Delete", 0)
        self._process_events()

        self.assertTrue(confirmed["clicked"])
        self.assertTrue(error_closed["clicked"])
        self.assertEqual(replacement["joint"].Name, joint_name)
        self.assertNotEqual(int(replacement["joint"].ID), joint_id)
        self.assertIs(
            self.document.getObject(component_name),
            component,
        )
        self.assertEqual(int(component.ID), component_id)
        self.assertIs(
            self.document.getObject(joint_name),
            replacement["joint"],
        )
        self.assertIs(blocker.ProtectedComponent, component)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_every_joint_type_refuses_a_caller_owned_transaction(self):
        self._create_assembly_with_components(1)
        for command_name in JOINT_COMMANDS:
            self.assertTrue(
                Gui.isCommandActive(command_name),
                command_name,
            )

        self.document.openTransaction("Caller-owned assembly change")
        transaction = int(self.document.getBookedTransactionID())
        self.assertNotEqual(transaction, 0)
        self._process_events()

        for command_name in JOINT_COMMANDS:
            self.assertFalse(
                Gui.isCommandActive(command_name),
                command_name,
            )

        App.closeActiveTransaction(True, transaction)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_every_concrete_joint_type_launches_and_cancels_cleanly(self):
        self._create_assembly_with_components(1)

        for command_name in JOINT_TASK_COMMANDS:
            with self.subTest(command=command_name):
                before = tuple(self.document.Objects)
                before_undo = int(self.document.UndoCount)
                self.assertTrue(Gui.isCommandActive(command_name))

                Gui.runCommand(command_name)
                self._process_events(100)

                self.assertTrue(Gui.Control.activeDialog())
                self.assertTrue(self.document.HasPendingTransaction)
                self.assertGreater(len(self.document.Objects), len(before))
                self._dismiss_task(accept=False)
                self.assertEqual(tuple(self.document.Objects), before)
                self.assertEqual(int(self.document.UndoCount), before_undo)

    def test_joint_task_restores_the_exact_prior_movement_mode(self):
        assembly, _components = self._create_assembly_with_components(1)
        assembly.ViewObject.MoveOnlyPreselected = True
        assembly.ViewObject.MoveInCommand = False

        Gui.runCommand("Assembly_CreateJointFixed")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        self._dismiss_task(accept=False)

        self.assertTrue(assembly.ViewObject.MoveOnlyPreselected)
        self.assertFalse(assembly.ViewObject.MoveInCommand)

    def test_exploded_view_cancel_is_an_exact_rollback(self):
        assembly, _components = self._create_assembly_with_components(2)
        assembly.ViewObject.EnableMovement = False
        assembly.ViewObject.DraggerVisibility = True
        before = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)

        self.assertTrue(Gui.isCommandActive("Assembly_CreateView"))
        Gui.runCommand("Assembly_CreateView")
        self._process_events(100)

        self.assertTrue(Gui.Control.activeDialog())
        self.assertTrue(self.document.HasPendingTransaction)
        self.assertGreater(len(self.document.Objects), len(before))
        self._dismiss_task(accept=False)
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertFalse(assembly.ViewObject.EnableMovement)
        self.assertTrue(assembly.ViewObject.DraggerVisibility)

    def test_simulation_cancel_is_an_exact_rollback(self):
        import JointObject

        assembly, components = self._create_assembly_with_components(2)
        joint_group = next(
            child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
        )
        self.document.UndoMode = False
        joint = joint_group.newObject(
            "App::FeaturePython",
            "ContractRevoluteJoint",
        )
        JointObject.Joint(joint, 1)
        JointObject.ViewProviderJoint(joint.ViewObject)
        joint.Proxy.setJointConnectors(
            joint,
            [
                [components[0], ["Face1"]],
                [components[1], ["Face1"]],
            ],
        )
        self.document.recompute()
        self.document.UndoMode = True

        before = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)
        self.assertTrue(Gui.isCommandActive("Assembly_CreateSimulation"))

        Gui.runCommand("Assembly_CreateSimulation")
        self._process_events(100)

        self.assertTrue(Gui.Control.activeDialog())
        self.assertTrue(self.document.HasPendingTransaction)
        self.assertGreater(len(self.document.Objects), len(before))
        self._dismiss_task(accept=False)
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(int(self.document.UndoCount), before_undo)

    def test_deleting_assembly_history_owners_removes_their_resources(self):
        import CommandCreateSimulation
        import CommandCreateView
        import UtilsAssembly

        assembly, components = self._create_assembly_with_components(2)

        view_group = UtilsAssembly.getViewGroup(assembly)
        exploded = view_group.newObject(
            "App::FeaturePython",
            "DeleteContractExplodedView",
        )
        CommandCreateView.ExplodedView(exploded)
        move = assembly.newObject(
            "App::FeaturePython",
            "DeleteContractExplodedMove",
        )
        CommandCreateView.ExplodedViewStep(move)
        exploded.Group = [move]

        simulation_group = UtilsAssembly.getSimulationGroup(assembly)
        simulation = simulation_group.newObject(
            "App::FeaturePython",
            "DeleteContractSimulation",
        )
        CommandCreateSimulation.Simulation(simulation)
        motion = assembly.newObject(
            "App::FeaturePython",
            "DeleteContractMotion",
        )
        CommandCreateSimulation.Motion(motion)
        simulation.Group = [motion]
        self.document.recompute()

        expected = {obj.Name: obj for obj in (exploded, move, simulation, motion)}
        exploded_name = exploded.Name
        move_name = move.Name
        simulation_name = simulation.Name
        motion_name = motion.Name
        self.assertEqual(move.VibeCADTimelineRole, "resource")
        self.assertIs(move.VibeCADTimelineOwner, exploded)
        self.assertEqual(motion.VibeCADTimelineRole, "resource")
        self.assertIs(motion.VibeCADTimelineOwner, simulation)

        before_undo = int(self.document.UndoCount)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(exploded)
        Gui.Selection.addSelection(simulation)
        Gui.runCommand("Std_Delete", 0)
        self._process_events()

        for name in expected:
            self.assertIsNone(self.document.getObject(name))
        self.assertTrue(all(component in assembly.Group for component in components))
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

        self.document.undo()
        self._process_events()
        restored = {name: self.document.getObject(name) for name in expected}
        self.assertTrue(all(obj is not None for obj in restored.values()))
        self.assertIs(
            restored[move_name].VibeCADTimelineOwner,
            restored[exploded_name],
        )
        self.assertIs(
            restored[motion_name].VibeCADTimelineOwner,
            restored[simulation_name],
        )

        self.document.redo()
        self._process_events()
        for name in expected:
            self.assertIsNone(self.document.getObject(name))

    def test_solve_preserves_free_motion_and_grounding_closes_exactly(self):
        assembly, components = self._create_assembly_with_components(1)
        before_undo = int(self.document.UndoCount)
        before_placement = components[0].Placement

        self.assertTrue(Gui.isCommandActive("Assembly_SolveAssembly"))
        Gui.runCommand("Assembly_SolveAssembly")
        self._process_events()

        self.assertEqual(components[0].Placement, before_placement)
        self.assertIn(int(self.document.UndoCount) - before_undo, (0, 1))
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        free_diagnostics = assembly.getSolverDiagnostics()
        self.assertEqual(int(free_diagnostics["solver_status"]), 0)
        self.assertGreater(
            int(free_diagnostics["remaining_degrees_of_freedom"]),
            0,
        )

        Gui.Selection.addSelection(components[0])
        self._process_events()
        Gui.runCommand("Assembly_ToggleGrounded")
        self._process_events()
        Gui.Selection.clearSelection()
        before_solve_undo = int(self.document.UndoCount)

        Gui.runCommand("Assembly_SolveAssembly")
        self._process_events()

        self.assertTrue(assembly.isValid())
        self.assertEqual(
            int(assembly.getSolverDiagnostics()["solver_status"]),
            0,
        )
        self.assertLess(
            int(assembly.getSolverDiagnostics()["remaining_degrees_of_freedom"]),
            int(free_diagnostics["remaining_degrees_of_freedom"]),
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertIn(
            int(self.document.UndoCount) - before_solve_undo,
            (0, 1),
        )
