# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI integration tests for the native document-wide feature timeline."""

import os
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign  # noqa: F401 - registers Part Design document types
import Sketcher  # noqa: F401 - registers Sketcher document types
from PySide import QtCore, QtGui


TREE_PARAMETER_PATH = "User parameter:BaseApp/Preferences/TreeView"
OBJECT_NAME_ROLE = int(QtCore.Qt.UserRole)
OWNER_NAME_ROLE = OBJECT_NAME_ROLE + 1
IS_LAST_ACTIVE_ROLE = OBJECT_NAME_ROLE + 2
IS_AFTER_POSITION_ROLE = OBJECT_NAME_ROLE + 3
OPERATION_INDEX_ROLE = OBJECT_NAME_ROLE + 4
IS_MARKER_ROLE = OBJECT_NAME_ROLE + 5
OBJECT_ID_ROLE = OBJECT_NAME_ROLE + 6
OWNER_ID_ROLE = OBJECT_NAME_ROLE + 7


def _event_step(milliseconds=10):
    Gui.updateGui()
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def _wait_until(predicate, timeout_ms=10000):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < timeout_ms:
        _event_step()
        try:
            result = predicate()
        except RuntimeError:
            # Timeline and tree refreshes replace item wrappers.
            result = None
        if result:
            return result
    return None


def _send_mouse_event(widget, event_type, position, button, buttons):
    event = QtGui.QMouseEvent(
        event_type,
        QtCore.QPointF(position),
        QtCore.QPointF(widget.mapToGlobal(position)),
        button,
        buttons,
        QtCore.Qt.NoModifier,
    )
    QtGui.QApplication.sendEvent(widget, event)


def _timeline_items(timeline):
    return [timeline.item(row) for row in range(timeline.count())]


def _object_items(timeline):
    return {
        item.data(OBJECT_NAME_ROLE): item
        for item in _timeline_items(timeline)
        if item.data(OBJECT_NAME_ROLE)
    }


def _current_state_marker(timeline):
    return next(
        (
            item
            for item in _timeline_items(timeline)
            if item.data(IS_MARKER_ROLE)
        ),
        None,
    )


def _marker_position(timeline):
    """Return the document operation boundary represented by the marker."""

    marker = _current_state_marker(timeline)
    return marker.data(OPERATION_INDEX_ROLE) if marker is not None else None


def _marker_row(timeline):
    marker = _current_state_marker(timeline)
    return timeline.row(marker) if marker is not None else -1


def _object_row(timeline, object_name):
    item = _object_items(timeline).get(object_name)
    return timeline.row(item) if item is not None else -1


def _ordered_object_names(timeline):
    return [
        item.data(OBJECT_NAME_ROLE)
        for item in _timeline_items(timeline)
        if item.data(OBJECT_NAME_ROLE)
    ]


def _icon_pixels(icon):
    image = icon.pixmap(QtCore.QSize(22, 22)).toImage().convertToFormat(
        QtGui.QImage.Format_ARGB32
    )
    return tuple(
        image.pixelColor(x, y).getRgb()
        for y in range(image.height())
        for x in range(image.width())
    )


def _has_colored_pixel(pixels):
    return any(
        alpha and (red != green or green != blue)
        for red, green, blue, alpha in pixels
    )


def _is_grayscale(pixels):
    return all(
        not alpha or (red == green == blue)
        for red, green, blue, alpha in pixels
    )


def _document_timeline(document):
    return next(
        (
            obj
            for obj in document.Objects
            if obj.TypeId == "App::DocumentTimeline"
        ),
        None,
    )


def _trigger_timeline_action(timeline, item, action_name):
    state = {}

    def trigger():
        popup = QtGui.QApplication.activePopupWidget()
        if popup is None:
            state["error"] = "No active timeline context menu"
            return
        try:
            action = next(
                (
                    candidate
                    for candidate in popup.actions()
                    if candidate.objectName() == action_name
                ),
                None,
            )
            if action is None:
                state["error"] = (
                    f"Timeline context menu omitted {action_name}"
                )
            else:
                action.trigger()
                state["triggered"] = True
        except Exception as error:  # pragma: no cover - diagnostic
            state["error"] = repr(error)
        finally:
            popup.close()

    timeline.scrollToItem(item)
    _event_step()
    QtCore.QTimer.singleShot(30, trigger)
    timeline.customContextMenuRequested.emit(
        timeline.visualItemRect(item).center()
    )
    if "error" in state:
        raise AssertionError(state["error"])
    if not state.get("triggered"):
        raise AssertionError(
            f"Timeline action {action_name!r} was not triggered"
        )


def _run_delete_and_capture_error():
    state = {}

    def close_message():
        message = QtGui.QApplication.activeModalWidget()
        if not isinstance(message, QtGui.QMessageBox):
            state["error"] = "Std_Delete did not open its failure message"
            return
        state["title"] = message.windowTitle()
        state["text"] = message.text()
        message.accept()

    QtCore.QTimer.singleShot(30, close_message)
    Gui.runCommand("Std_Delete", 0)
    _event_step()
    if "error" in state:
        raise AssertionError(state["error"])
    if not state.get("text"):
        raise AssertionError("Std_Delete returned without a failure message")
    return state


def _run_delete_and_answer_question(answer):
    state = {}

    def answer_question():
        message = QtGui.QApplication.activeModalWidget()
        if not isinstance(message, QtGui.QMessageBox):
            state["error"] = (
                "Std_Delete did not open its object-content question"
            )
            return
        state["title"] = message.windowTitle()
        state["text"] = message.text()
        button = message.button(answer)
        if button is None:
            state["error"] = "The deletion question omitted the requested answer"
            message.reject()
            return
        button.click()

    QtCore.QTimer.singleShot(30, answer_question)
    Gui.runCommand("Std_Delete", 0)
    _event_step()
    if "error" in state:
        raise AssertionError(state["error"])
    if not state.get("text"):
        raise AssertionError(
            "Std_Delete returned without its object-content question"
        )
    return state


def _arm_marker_drag_to_end(timeline):
    marker = _current_state_marker(timeline)
    if marker is None:
        raise AssertionError("Timeline has no current-state marker")
    timeline.scrollToItem(marker)
    _event_step()
    marker_position = timeline.visualItemRect(marker).center()
    end_position = QtCore.QPoint(
        max(1, timeline.viewport().width() - 2),
        marker_position.y(),
    )
    _send_mouse_event(
        timeline.viewport(),
        QtCore.QEvent.MouseButtonPress,
        marker_position,
        QtCore.Qt.LeftButton,
        QtCore.Qt.LeftButton,
    )
    _send_mouse_event(
        timeline.viewport(),
        QtCore.QEvent.MouseMove,
        end_position,
        QtCore.Qt.NoButton,
        QtCore.Qt.LeftButton,
    )
    return marker, end_position


def _visible_tree_labels():
    labels = []

    def collect(item):
        if item.isHidden():
            return
        labels.append(item.text(0))
        for index in range(item.childCount()):
            collect(item.child(index))

    main_window = Gui.getMainWindow()
    for tree in main_window.findChildren(QtGui.QTreeWidget):
        try:
            for index in range(tree.topLevelItemCount()):
                collect(tree.topLevelItem(index))
        except RuntimeError:
            continue
    return labels


class _TimelineExecutionProxy:
    def __init__(self):
        self.fail = False
        self.executions = 0

    def execute(self, obj):
        self.executions += 1
        if self.fail:
            raise RuntimeError("Deliberate timeline feature failure")
        obj.Shape = Part.makeBox(3, 3, 3)


class _HandledWithoutEditViewProxy:
    """Legacy handled callback that deliberately does not enter edit mode."""

    TRACE_PROBE = "vibecad_rejected_timeline_edit_trace_probe = True"

    def supportsDocumentTimelineEdit(self):
        return True

    def doubleClicked(self, _view_object):
        Gui.doCommandSkip(self.TRACE_PROBE)
        return True


class _NoPanelEditViewProxy:
    """A real ViewProvider editor which deliberately has no TaskDialog."""

    def supportsDocumentTimelineEdit(self):
        return True

    def doubleClicked(self, view_object):
        return Gui.activeDocument().setEdit(view_object.Object.Name)

    def setEdit(self, _view_object, _mode):
        return True

    def unsetEdit(self, _view_object, _mode):
        return True


class _RedirectedEditViewProxy:
    """A misleading callback which opens an editor for another object."""

    def __init__(self, target_name):
        self.target_name = target_name

    def supportsDocumentTimelineEdit(self):
        return True

    def doubleClicked(self, _view_object):
        return Gui.activeDocument().setEdit(self.target_name)


class _EnterEditThenThrowViewProxy:
    """Open this object's editor, then fail before returning to the caller."""

    def __init__(self):
        self.entered = 0
        self.unset = 0

    def supportsDocumentTimelineEdit(self):
        return True

    def doubleClicked(self, view_object):
        Gui.activeDocument().setEdit(view_object.Object.Name)
        raise RuntimeError("Deliberate failure after entering edit")

    def setEdit(self, _view_object, _mode):
        self.entered += 1
        return True

    def unsetEdit(self, _view_object, _mode):
        self.unset += 1
        return True


class _OwnerRequiredDeleteViewProxy:
    """Reject deletion unless the semantic owner is still live."""

    def __init__(self, owner_name, call_log=None, call_name=None):
        self.owner_name = owner_name
        self.call_log = call_log
        self.call_name = call_name
        self.calls = 0
        self.owner_was_live = False

    def onDelete(self, view_object, _subelements):
        self.calls += 1
        if self.call_log is not None:
            self.call_log.append(self.call_name or view_object.Object.Name)
        document = view_object.Object.Document
        self.owner_was_live = (
            document is not None
            and document.getObject(self.owner_name) is not None
        )
        return self.owner_was_live


class _TransactionCloseSuccessor:
    """Open one independently owned transaction after an exact close."""

    def __init__(self, document, expected_abort):
        self.document = document
        self.expected_abort = expected_abort
        self.armed = False
        self.successor_id = 0
        self.sentinel = None
        self.error = None

    def slotCloseTransaction(self, abort):
        if (
            not self.armed
            or self.successor_id
            or bool(abort) != self.expected_abort
        ):
            return
        try:
            self.document.openTransaction("Independent successor transaction")
            self.successor_id = self.document.getBookedTransactionID()
            self.sentinel = self.document.addObject(
                "App::FeaturePython",
                "TimelineSuccessorSentinel",
            )
        except Exception as error:  # pragma: no cover - diagnostic
            self.error = error


class TestFeatureTimeline(unittest.TestCase):
    """The human timeline must operate on native document objects."""

    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")

        self.tree_parameters = App.ParamGet(TREE_PARAMETER_PATH)
        self.previous_browser_preference = self.tree_parameters.GetBool(
            "OrganizeModelByType", True
        )
        self.tree_parameters.SetBool("OrganizeModelByType", True)
        self.macro_parameters = App.ParamGet(
            "User parameter:BaseApp/Preferences/Macro"
        )
        self.previous_script_to_python_console = (
            self.macro_parameters.GetBool("ScriptToPyConsole", True)
        )
        self.macro_parameters.SetBool("ScriptToPyConsole", True)

        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("FeatureTimeline")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)

        self.body = self.document.addObject("PartDesign::Body", "TimelineBody")
        self.body.Label = "Timeline Body"
        Gui.activeView().setActiveObject("pdbody", self.body)

        self.sketch = self.document.addObject(
            "Sketcher::SketchObject", "TimelineSketch"
        )
        self.sketch.Label = "Profile Sketch"
        self.body.addObject(self.sketch)

        self.first = self.document.addObject(
            "PartDesign::Feature", "FirstResult"
        )
        self.first.Label = "First Result"
        self.first.Shape = Part.makeBox(4, 4, 4)
        self.body.addObject(self.first)

        self.reference = self.document.addObject(
            "PartDesign::Plane", "ReferencePlane"
        )
        self.reference.Label = "Reference Plane"
        self.reference.AttachmentSupport = (
            self.document.XY_Plane,
            [""],
        )
        self.reference.MapMode = "FlatFace"
        self.body.addObject(self.reference)

        self.second = self.document.addObject(
            "PartDesign::Feature", "SecondResult"
        )
        self.second.Label = "Second Result"
        self.second.Shape = Part.makeBox(5, 5, 5)
        self.body.addObject(self.second)

        self.body.Tip = self.first
        self.first.Visibility = True
        self.second.Visibility = False
        self.document.recompute()
        timeline_object = _document_timeline(self.document)
        self.assertIsNotNone(
            timeline_object,
            "A modeling document must own its native document timeline",
        )
        self.assertEqual(
            [obj.Name for obj in timeline_object.Operations],
            [
                self.body.Name,
                self.sketch.Name,
                self.first.Name,
                self.reference.Name,
                self.second.Name,
            ],
        )
        # The persisted timeline is authoritative once present. Establish the
        # intentionally rolled-back fixture directly instead of expecting the
        # Body Tip to back-drive the document position.
        timeline_object.Position = 3

        main_window = Gui.getMainWindow()
        self.timeline_widget = _wait_until(
            lambda: main_window.findChild(
                QtGui.QWidget, "VibeCADFeatureTimeline"
            )
        )
        self.assertIsNotNone(self.timeline_widget)
        self.timeline = self.timeline_widget.findChild(
            QtGui.QListWidget, "VibeCADFeatureTimelineItems"
        )
        self.assertIsNotNone(self.timeline)
        expected = {
            self.body.Name,
            self.sketch.Name,
            self.first.Name,
            self.reference.Name,
            self.second.Name,
        }
        self.assertTrue(
            _wait_until(
                lambda: expected.issubset(set(_object_items(self.timeline)))
            ),
            [item.text() for item in _timeline_items(self.timeline)],
        )

    def tearDown(self):
        if App.GuiUp and Gui.activeDocument() is not None:
            if Gui.Control.activeDialog():
                try:
                    Gui.Control.activeTaskDialog().reject()
                except RuntimeError:
                    pass
            if Gui.activeDocument().getInEdit() is not None:
                Gui.activeDocument().resetEdit()
        Gui.Selection.clearSelection()
        if hasattr(self, "tree_parameters"):
            self.tree_parameters.SetBool(
                "OrganizeModelByType", self.previous_browser_preference
            )
        if hasattr(self, "macro_parameters"):
            self.macro_parameters.SetBool(
                "ScriptToPyConsole",
                self.previous_script_to_python_console,
            )
        try:
            document_name = (
                self.document.Name
                if getattr(self, "document", None) is not None
                else ""
            )
        except RuntimeError:
            document_name = ""
        open_documents = App.listDocuments()
        if document_name and document_name in open_documents:
            App.closeDocument(document_name)
        if (
            document_name != "FeatureTimeline"
            and "FeatureTimeline" in App.listDocuments()
        ):
            App.closeDocument("FeatureTimeline")
        if App.GuiUp:
            Gui.activateWorkbench("PartDesignWorkbench")

    def test_fixed_document_history_strip_order_state_and_selection(self):
        main_window = Gui.getMainWindow()
        workspace = main_window.findChild(QtGui.QWidget, "VibeCADWorkspace")
        viewport_canvas = main_window.findChild(
            QtGui.QWidget,
            "VibeCADViewportCanvas",
        )
        mdi_area = main_window.findChild(QtGui.QMdiArea)

        self.assertIsNotNone(workspace)
        self.assertIsNotNone(viewport_canvas)
        self.assertIs(self.timeline_widget.parent(), workspace)
        self.assertIs(viewport_canvas.parent(), workspace)
        self.assertIs(mdi_area.parent(), viewport_canvas)
        self.assertEqual(self.timeline_widget.minimumHeight(), 56)
        self.assertEqual(self.timeline_widget.maximumHeight(), 56)
        self.assertEqual(
            self.timeline.horizontalScrollBar().height(),
            6,
        )
        self.assertFalse(
            isinstance(self.timeline_widget.parent(), QtGui.QDockWidget)
        )

        items = _timeline_items(self.timeline)
        ordered_names = _ordered_object_names(self.timeline)
        self.assertEqual(
            ordered_names,
            [
                self.body.Name,
                self.sketch.Name,
                self.first.Name,
                self.reference.Name,
                self.second.Name,
            ],
        )

        marker_rows = [
            row
            for row, item in enumerate(items)
            if item.data(IS_MARKER_ROLE)
        ]
        self.assertEqual(len(marker_rows), 1)
        first_row = next(
            row
            for row, item in enumerate(items)
            if item.data(OBJECT_NAME_ROLE) == self.first.Name
        )
        self.assertEqual(marker_rows[0], first_row + 1)
        self.assertLessEqual(items[marker_rows[0]].sizeHint().width(), 24)

        object_items = _object_items(self.timeline)
        self.assertTrue(
            all(item.sizeHint().width() <= 40 for item in object_items.values())
        )
        self.assertEqual(self.timeline.iconSize(), QtCore.QSize(22, 22))
        self.assertTrue(
            all(not item.icon().isNull() for item in object_items.values())
        )

        self.assertIsNone(
            self.timeline_widget.findChild(
                QtGui.QComboBox,
                "VibeCADFeatureTimelineBody",
            ),
            "A document-wide history must never be filtered by a Body selector",
        )
        self.assertIsNone(
            self.timeline_widget.findChild(
                QtGui.QLabel, "VibeCADFeatureTimelineCurrent"
            ),
            "The reclaimed workspace edge must contain operations, not a "
            "redundant History label",
        )
        for object_name in (
            "VibeCADFeatureTimelineRecompute",
            "VibeCADFeatureTimelinePrevious",
            "VibeCADFeatureTimelineNext",
            "VibeCADFeatureTimelineEnd",
        ):
            button = self.timeline_widget.findChild(
                QtGui.QToolButton, object_name
            )
            self.assertIsNotNone(button)
            self.assertFalse(button.icon().isNull())
            self.assertTrue(button.toolTip())
            self.assertNotIn("Body Tip", button.toolTip())
        self.assertTrue(
            object_items[self.first.Name].data(IS_LAST_ACTIVE_ROLE)
        )
        self.assertFalse(
            object_items[self.sketch.Name].data(IS_AFTER_POSITION_ROLE)
        )
        self.assertTrue(
            object_items[self.reference.Name].data(IS_AFTER_POSITION_ROLE)
        )
        self.assertTrue(
            object_items[self.second.Name].data(IS_AFTER_POSITION_ROLE)
        )
        self.assertEqual(
            object_items[self.reference.Name].data(OPERATION_INDEX_ROLE), 3
        )
        self.assertEqual(
            object_items[self.first.Name].data(OBJECT_ID_ROLE),
            self.first.ID,
        )
        self.assertEqual(
            object_items[self.first.Name].data(OWNER_NAME_ROLE),
            self.body.Name,
        )
        self.assertEqual(
            object_items[self.first.Name].data(OWNER_ID_ROLE),
            self.body.ID,
        )
        timeline_object = _document_timeline(self.document)
        self.assertIsNotNone(timeline_object)
        self.assertEqual(
            [obj.Name for obj in timeline_object.Operations],
            ordered_names,
        )
        self.assertEqual(timeline_object.Position, 3)
        self.assertEqual(_marker_position(self.timeline), 3)

        second_item = object_items[self.second.Name]
        self.timeline.scrollToItem(second_item)
        _event_step()
        self.timeline.clearSelection()
        self.timeline.setCurrentItem(second_item)
        second_item.setSelected(True)
        _event_step()
        self.assertEqual(Gui.Selection.getSelection(), [self.second])

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.reference)
        reference_selected = _wait_until(
            lambda: _object_items(self.timeline)[
                self.reference.Name
            ].isSelected()
        )
        self.assertTrue(
            reference_selected,
            "External selection did not reach the timeline; GUI selection={!r}, "
            "document GUI selection={!r}, timeline selection={!r}".format(
                [obj.Name for obj in Gui.Selection.getSelection()],
                [
                    obj.Name
                    for obj in Gui.Selection.getSelection(self.document.Name)
                ],
                [
                    item.data(OBJECT_NAME_ROLE)
                    for item in self.timeline.selectedItems()
                ],
            ),
        )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(
            self.document.Name, self.second.Name, "Face1"
        )
        _event_step(50)
        self.assertNotIn(self.second.Label, _visible_tree_labels())

        Gui.runCommand("Std_ToggleBottomPanels")
        _event_step()
        self.assertFalse(self.timeline_widget.isHidden())
        Gui.runCommand("Std_ToggleBottomPanels")
        _event_step()
        self.assertFalse(self.timeline_widget.isHidden())

        Gui.activateWorkbench("SketcherWorkbench")
        _event_step(50)
        self.assertIs(
            main_window.findChild(
                QtGui.QWidget, "VibeCADFeatureTimeline"
            ),
            self.timeline_widget,
        )
        self.assertFalse(self.timeline_widget.isHidden())

    def test_history_icons_follow_owning_body_visibility(self):
        self.body.ViewObject.Visibility = True

        def body_items_with_state(state):
            items = [
                item
                for item in _object_items(self.timeline).values()
                if item.data(OWNER_NAME_ROLE) == self.body.Name
            ]
            return (
                items
                if items
                and all(
                    "Body visibility: {}".format(state) in item.toolTip()
                    for item in items
                )
                else None
            )

        visible_items = _wait_until(lambda: body_items_with_state("Visible"))
        self.assertIsNotNone(visible_items)
        visible_pixels = _icon_pixels(
            _object_items(self.timeline)[self.first.Name].icon()
        )

        self.body.ViewObject.Visibility = False
        hidden_items = _wait_until(lambda: body_items_with_state("Hidden"))
        self.assertIsNotNone(hidden_items)
        hidden_pixels = _icon_pixels(
            _object_items(self.timeline)[self.first.Name].icon()
        )

        self.assertTrue(_has_colored_pixel(visible_pixels))
        self.assertTrue(_is_grayscale(hidden_pixels))
        self.assertNotEqual(hidden_pixels, visible_pixels)

    def test_long_history_rebuild_reveals_current_state_marker(self):
        Gui.Selection.clearSelection()
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: _marker_position(self.timeline)
                == len(_document_timeline(self.document).Operations)
            )
        )
        shared_shape = Part.makeBox(1, 1, 1)
        long_results = []
        for index in range(32):
            feature = self.document.addObject(
                "PartDesign::Feature",
                "LongResult{:02d}".format(index),
            )
            feature.Label = "Long Result {:02d}".format(index)
            feature.Shape = shared_shape
            self.body.addObject(feature)
            long_results.append(feature)
        self.body.Tip = long_results[-1]
        self.document.recompute()

        self.assertTrue(
            _wait_until(
                lambda: long_results[-1].Name
                in _object_items(self.timeline)
            )
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is long_results[-1]
                and _marker_position(self.timeline)
                == len(_document_timeline(self.document).Operations)
            )
        )
        long_results[-1].Label = "Long Result Final"

        def marker_is_visible():
            marker = next(
                (
                    item
                    for item in _timeline_items(self.timeline)
                    if item.data(IS_MARKER_ROLE)
                ),
                None,
            )
            return (
                marker is not None
                and self.timeline.visualItemRect(marker).intersects(
                    self.timeline.viewport().rect()
                )
            )

        self.assertTrue(_wait_until(marker_is_visible))
        self.assertGreater(
            self.timeline.horizontalScrollBar().value(),
            0,
            "A long history should scroll forward to reveal its Tip marker",
        )

        Gui.Selection.addSelection(self.sketch)
        self.assertTrue(
            _wait_until(
                lambda: _object_items(self.timeline)[
                    self.sketch.Name
                ].isSelected()
                and self.timeline.visualItemRect(
                    _object_items(self.timeline)[self.sketch.Name]
                ).intersects(self.timeline.viewport().rect())
            ),
            "Selection scrolling must still override the marker reveal",
        )

    def test_native_edit_and_undoable_current_state_context_action(self):
        object_items = _object_items(self.timeline)
        self.timeline.itemDoubleClicked.emit(
            object_items[self.sketch.Name]
        )
        self.assertTrue(
            _wait_until(
                lambda: Gui.activeDocument().getInEdit() is not None
            )
        )
        self.assertIs(
            Gui.activeDocument().getInEdit().Object,
            self.sketch,
        )
        Gui.activeDocument().resetEdit()
        self.assertTrue(
            _wait_until(
                lambda: Gui.activeDocument().getInEdit() is None
            )
        )

        object_items = _object_items(self.timeline)
        second_item = object_items[self.second.Name]
        self.timeline.scrollToItem(second_item)
        _event_step()
        action_names = set()
        action_state = {}

        def trigger_set_current():
            popup = QtGui.QApplication.activePopupWidget()
            if popup is None:
                action_state["error"] = "No active timeline context menu"
                return
            try:
                actions = {
                    action.objectName(): action
                    for action in popup.actions()
                    if action.objectName()
                }
                action_names.update(actions)
                current_action = actions.get("VibeCADTimelineSetCurrent")
                if current_action is None:
                    action_state["error"] = (
                        "Timeline context menu omitted Set Current"
                    )
                else:
                    current_action.trigger()
                    action_state["triggered"] = True
            except Exception as error:
                action_state["error"] = repr(error)
            finally:
                popup.close()

        # A document change rebuilds the timeline while QMenu::exec() is
        # running its nested event loop. Menu actions must use immutable object
        # identities, never a QListWidgetItem that rebuild() has deleted.
        QtCore.QTimer.singleShot(
            0,
            lambda: setattr(
                self.second,
                "Label",
                "Second Result During Menu Refresh",
            ),
        )
        QtCore.QTimer.singleShot(30, trigger_set_current)
        self.timeline.customContextMenuRequested.emit(
            self.timeline.visualItemRect(second_item).center()
        )

        self.assertNotIn("error", action_state, action_state)
        self.assertTrue(action_state.get("triggered"), action_state)
        self.assertIn("VibeCADTimelineSetCurrent", action_names)
        self.assertNotIn(
            "VibeCADTimelineEdit",
            action_names,
            "A plain result with no parameter editor must not advertise an "
            "Edit action which cannot do anything",
        )
        self.assertIn("VibeCADTimelineDelete", action_names)
        self.assertIs(self.body.Tip, self.second)
        self.assertFalse(self.first.Visibility)
        self.assertTrue(self.second.Visibility)
        self.assertTrue(
            _wait_until(
                lambda: not _object_items(self.timeline)[
                    self.second.Name
                ].data(IS_AFTER_POSITION_ROLE)
            )
        )

        self.document.undo()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.first
                and self.first.Visibility
                and not self.second.Visibility
                and _object_items(self.timeline)[
                    self.second.Name
                ].data(IS_AFTER_POSITION_ROLE)
            )
        )

        self.document.redo()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and not self.first.Visibility
                and self.second.Visibility
                and not _object_items(self.timeline)[
                    self.second.Name
                ].data(IS_AFTER_POSITION_ROLE)
            )
        )

        group_order = [obj.Name for obj in self.body.Group]
        marker = next(
            item
            for item in _timeline_items(self.timeline)
            if item.data(IS_MARKER_ROLE)
        )
        reference_item = _object_items(self.timeline)[self.reference.Name]
        reference_position = reference_item.data(OPERATION_INDEX_ROLE)
        self.timeline.scrollToItem(marker)
        _event_step()
        marker_position = self.timeline.visualItemRect(marker).center()
        reference_rect = self.timeline.visualItemRect(reference_item)
        rollback_position = QtCore.QPoint(
            reference_rect.left(),
            reference_rect.center().y(),
        )
        _send_mouse_event(
            self.timeline.viewport(),
            QtCore.QEvent.MouseButtonPress,
            marker_position,
            QtCore.Qt.LeftButton,
            QtCore.Qt.LeftButton,
        )
        _send_mouse_event(
            self.timeline.viewport(),
            QtCore.QEvent.MouseMove,
            rollback_position,
            QtCore.Qt.NoButton,
            QtCore.Qt.LeftButton,
        )
        _send_mouse_event(
            self.timeline.viewport(),
            QtCore.QEvent.MouseButtonRelease,
            rollback_position,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoButton,
        )
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.first
                and self.first.Visibility
                and not self.second.Visibility
                and _object_items(self.timeline)[
                    self.second.Name
                ].data(IS_AFTER_POSITION_ROLE)
                and _marker_position(self.timeline) == reference_position
            )
        )
        self.assertEqual(
            [obj.Name for obj in self.body.Group],
            group_order,
            "Dragging the document marker must not reorder Body.Group",
        )

        self.document.undo()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and not self.first.Visibility
                and self.second.Visibility
                and not _object_items(self.timeline)[
                    self.second.Name
                ].data(IS_AFTER_POSITION_ROLE)
            )
        )

        previous_button = self.timeline_widget.findChild(
            QtGui.QToolButton, "VibeCADFeatureTimelinePrevious"
        )
        next_button = self.timeline_widget.findChild(
            QtGui.QToolButton, "VibeCADFeatureTimelineNext"
        )
        recompute_button = self.timeline_widget.findChild(
            QtGui.QToolButton, "VibeCADFeatureTimelineRecompute"
        )
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton, "VibeCADFeatureTimelineEnd"
        )
        previous_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.first
                and _marker_position(self.timeline) == 4
            )
        )
        previous_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.first
                and _marker_position(self.timeline) == 3
            )
        )
        previous_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is None
                and _marker_position(self.timeline) == 2
            ),
            "Previous must stop immediately after the sketch instead of "
            "skipping to the start of history",
        )
        next_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.first
                and _marker_position(self.timeline) == 3
            )
        )
        next_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.first
                and _marker_position(self.timeline) == 4
            ),
            "Next must stop immediately after the datum even though the "
            "preceding solid remains the Body Tip",
        )
        next_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and _marker_position(self.timeline) == 5
            )
        )
        self.second.touch()
        self.assertTrue(
            _wait_until(
                lambda: "Touched" in self.second.State
                and recompute_button.isEnabled()
            ),
            "A dirty document must expose the native recompute action",
        )
        recompute_button.click()
        self.assertTrue(
            _wait_until(lambda: "Touched" not in self.second.State),
            "The History recompute control did not recompute the document",
        )
        self.assertIs(self.body.Tip, self.second)
        self.assertEqual(
            _marker_position(self.timeline),
            5,
            "Recompute must not move the document-history boundary",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and _marker_position(self.timeline) == 5
            )
        )

        self.second.Label = "Second Result Renamed"
        self.assertTrue(
            _wait_until(
                lambda: "Second Result Renamed"
                in _object_items(self.timeline)[self.second.Name].toolTip()
            )
        )

    def test_marker_stops_after_sketch_and_datum_and_coordinates_body_tips(self):
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and _marker_position(self.timeline)
                == len(_document_timeline(self.document).Operations)
            )
        )

        second_body = self.document.addObject(
            "PartDesign::Body",
            "CoordinatedTimelineBody",
        )
        second_body.Label = "Coordinated Timeline Body"
        second_sketch = self.document.addObject(
            "Sketcher::SketchObject",
            "CoordinatedTimelineSketch",
        )
        second_sketch.Label = "Second Body Sketch"
        second_body.addObject(second_sketch)
        second_result = self.document.addObject(
            "PartDesign::Feature",
            "CoordinatedTimelineResult",
        )
        second_result.Label = "Second Body Result"
        second_result.Shape = Part.makeCylinder(2, 5)
        second_body.addObject(second_result)
        second_body.Tip = second_result

        first_tail = self.document.addObject(
            "PartDesign::Feature",
            "CoordinatedFirstBodyTail",
        )
        first_tail.Label = "First Body Tail"
        first_tail.Shape = Part.makeBox(6, 6, 6)
        self.body.addObject(first_tail)
        self.body.Tip = first_tail
        self.document.recompute()

        expected = {
            self.body.Name,
            self.sketch.Name,
            self.first.Name,
            self.reference.Name,
            self.second.Name,
            second_body.Name,
            second_sketch.Name,
            second_result.Name,
            first_tail.Name,
        }
        self.assertTrue(
            _wait_until(
                lambda: expected.issubset(_object_items(self.timeline))
            ),
            _ordered_object_names(self.timeline),
        )

        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is first_tail
                and second_body.Tip is second_result
            )
        )

        def assert_state_after(target, first_tip, second_tip):
            item = _object_items(self.timeline)[target.Name]
            operation_index = item.data(OPERATION_INDEX_ROLE)
            _trigger_timeline_action(
                self.timeline,
                item,
                "VibeCADTimelineSetCurrent",
            )
            self.assertTrue(
                _wait_until(
                    lambda: self.body.Tip is first_tip
                    and second_body.Tip is second_tip
                    and _marker_position(self.timeline)
                    == operation_index + 1
                    and _marker_row(self.timeline)
                    == _object_row(self.timeline, target.Name) + 1
                ),
                {
                    "marker": _marker_position(self.timeline),
                    "operation_index": operation_index,
                    "first_tip": (
                        self.body.Tip.Name
                        if self.body.Tip is not None
                        else None
                    ),
                    "second_tip": (
                        second_body.Tip.Name
                        if second_body.Tip is not None
                        else None
                    ),
                },
            )
            timeline_object = _document_timeline(self.document)
            self.assertIsNotNone(timeline_object)
            self.assertEqual(timeline_object.Position, operation_index + 1)

        # These are genuine document-history boundaries even though neither
        # object can legally be assigned as a PartDesign Body Tip.
        assert_state_after(self.sketch, None, None)
        assert_state_after(self.reference, self.first, None)

        # The one global position coordinates every Body. It is not a selector
        # for whichever Body happened to be active or selected most recently.
        assert_state_after(
            second_result,
            self.second,
            second_result,
        )
        assert_state_after(
            first_tail,
            first_tail,
            second_result,
        )

    def test_marker_suppresses_non_body_operation_and_restores_baseline(self):
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        previous_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        next_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineNext",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: _marker_position(self.timeline)
                == len(_document_timeline(self.document).Operations)
            )
        )

        operation = self.document.addObject(
            "Part::FeaturePython",
            "SuppressibleTimelineOperation",
        )
        operation.Label = "Suppressible Operation"
        operation.addExtension("App::SuppressibleExtensionPython")
        operation.Shape = Part.makeBox(2, 2, 2)
        operation.Visibility = True
        self.document.recompute()

        controller = _document_timeline(self.document)
        self.assertIs(controller.Operations[-1], operation)
        self.assertFalse(controller.SuppressionAtEnd[-1])
        operation_count = len(controller.Operations)
        self.assertTrue(
            _wait_until(
                lambda: _marker_position(self.timeline) == operation_count
                and operation.Name in _object_items(self.timeline)
            )
        )

        previous_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == operation_count - 1
                and operation.Suppressed
                and not operation.Visibility
            ),
            "Moving before a declared suppressible operation must make it "
            "computationally inactive, not merely hide it",
        )

        self.document.undo()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == operation_count
                and not operation.Suppressed
                and operation.Visibility
            )
        )
        self.document.redo()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == operation_count - 1
                and operation.Suppressed
                and not operation.Visibility
            )
        )

        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == operation_count
                and not operation.Suppressed
                and operation.Visibility
            )
        )

        operation.Suppressed = True
        self.assertTrue(
            _wait_until(lambda: controller.SuppressionAtEnd[-1]),
            "A user-selected end-of-history suppression state must be saved",
        )
        previous_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == operation_count - 1
                and operation.Suppressed
                and not operation.Visibility
            )
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == operation_count
                and operation.Suppressed
                and operation.Visibility
            ),
            "Returning to the end must restore the user's saved suppression "
            "state instead of forcing the operation active",
        )

    def test_future_native_operation_defers_recompute_until_marker_advances(self):
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        previous_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: _marker_position(self.timeline)
                == len(_document_timeline(self.document).Operations)
            )
        )

        operation = self.document.addObject(
            "Part::FeaturePython",
            "DeferredTimelineOperation",
        )
        operation.Label = "Deferred Operation"
        proxy = _TimelineExecutionProxy()
        operation.Proxy = proxy
        self.document.recompute()
        self.assertGreaterEqual(proxy.executions, 1)
        execution_count = proxy.executions

        previous_button.click()
        controller = _document_timeline(self.document)
        operation_index = list(controller.Operations).index(operation)
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == operation_index
                and not operation.Visibility
            )
        )

        proxy.fail = True
        operation.touch()
        self.document.recompute()
        self.assertEqual(
            proxy.executions,
            execution_count,
            "A future native feature must not execute while the document is "
            "rolled back before it",
        )
        self.assertIn(
            "Touched",
            operation.State,
            "Deferred work must remain dirty so it is not silently lost",
        )

        proxy.fail = False
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
                and proxy.executions == execution_count + 1
                and "Touched" not in operation.State
            ),
            "Advancing the marker must execute exactly the edits deferred "
            "while the feature was in the future",
        )

    def test_organizational_groups_are_not_history_operations(self):
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        end_button.click()
        controller = _document_timeline(self.document)
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
            )
        )
        operations_before = list(controller.Operations)

        group = self.document.addObject(
            "App::DocumentObjectGroup",
            "TimelineOrganization",
        )
        group.Label = "Organization"
        self.assertEqual(
            list(controller.Operations),
            operations_before,
            "A folder has no modeling effect and must not create a hidden "
            "history boundary",
        )

        child = self.document.addObject(
            "Part::Feature",
            "GroupedTimelineFeature",
        )
        child.Shape = Part.makeBox(1, 1, 1)
        group.addObject(child)
        self.document.recompute()
        self.assertEqual(
            list(controller.Operations),
            [*operations_before, child],
        )
        self.assertNotIn(group.Name, _object_items(self.timeline))
        self.assertTrue(
            _wait_until(
                lambda: child.Name in _object_items(self.timeline)
            )
        )

    def test_explicit_operation_container_is_one_visible_history_step(self):
        controller = _document_timeline(self.document)
        operation = self.document.addObject(
            "App::DocumentObjectGroup",
            "TimelineMultiOutputOperation",
        )
        self.assertNotIn(operation, controller.Operations)

        operation.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        operation.VibeCADTimelineRole = "operation"

        self.assertTrue(
            _wait_until(
                lambda: operation in controller.Operations
                and operation.Name in _object_items(self.timeline)
            ),
            "An explicitly classified domain controller must override the "
            "ordinary organizational-group rule",
        )
        self.assertEqual(
            [
                candidate
                for candidate in controller.Operations
                if candidate is operation
            ],
            [operation],
        )

        resource = self.document.addObject(
            "App::DocumentObjectGroup",
            "TimelineMultiOutputResources",
        )
        resource.addProperty(
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
            "Timeline",
        )
        resource.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        resource.VibeCADTimelineOwner = operation
        resource.VibeCADTimelineRole = "resource"

        self.assertTrue(
            _wait_until(
                lambda: resource in controller.Operations
                and operation.Name in _object_items(self.timeline)
                and resource.Name not in _object_items(self.timeline)
            ),
            "An explicitly owned container must retain timeline state without "
            "becoming a second visible history step",
        )

    def test_delete_owner_removes_nested_resources_deepest_first_and_reveals_input(self):
        source = self.document.addObject(
            "Part::Feature",
            "OwnerOnlyDeleteSource",
        )
        source.Shape = Part.makeBox(6, 5, 4)
        source.Visibility = False

        operation = self.document.addObject(
            "Part::FeaturePython",
            "OwnerOnlyDeleteOperation",
        )
        operation.Shape = Part.makeCylinder(3, 5)
        operation.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        operation.addProperty(
            "App::PropertyLinkListHidden",
            "VibeCADTimelineReplacedInputs",
            "Timeline",
        )
        operation.VibeCADTimelineRole = "operation"
        operation.VibeCADTimelineReplacedInputs = [source]
        operation.Visibility = True

        parent_resource = self.document.addObject(
            "Part::FeaturePython",
            "OwnerOnlyParentResource",
        )
        child_resource = self.document.addObject(
            "Part::FeaturePython",
            "OwnerOnlyChildResource",
        )
        call_log = []
        for resource, owner in (
            (parent_resource, operation),
            (child_resource, parent_resource),
        ):
            resource.Shape = Part.makeSphere(2)
            resource.addProperty(
                "App::PropertyString",
                "VibeCADTimelineRole",
                "Timeline",
            )
            resource.addProperty(
                "App::PropertyLinkHidden",
                "VibeCADTimelineOwner",
                "Timeline",
            )
            resource.VibeCADTimelineRole = "resource"
            resource.VibeCADTimelineOwner = owner
            resource.ViewObject.Proxy = _OwnerRequiredDeleteViewProxy(
                owner.Name,
                call_log,
                resource.Name,
            )
            resource.Visibility = False
        self.document.recompute()

        operation_name = operation.Name
        parent_name = parent_resource.Name
        child_name = child_resource.Name
        undo_count = int(self.document.UndoCount)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(operation)
        Gui.runCommand("Std_Delete", 0)
        _event_step()

        self.assertEqual(
            call_log,
            [child_name, parent_name],
            "Owner-only deletion must consume resources deepest-first while "
            "each resource owner is still live.",
        )
        self.assertIsNone(self.document.getObject(child_name))
        self.assertIsNone(self.document.getObject(parent_name))
        self.assertIsNone(self.document.getObject(operation_name))
        self.assertTrue(source.Visibility)
        self.assertEqual(int(self.document.UndoCount), undo_count + 1)

        self.document.undo()
        _event_step()
        restored_operation = self.document.getObject(operation_name)
        restored_parent = self.document.getObject(parent_name)
        restored_child = self.document.getObject(child_name)
        self.assertIsNotNone(restored_operation)
        self.assertIsNotNone(restored_parent)
        self.assertIsNotNone(restored_child)
        self.assertIs(restored_parent.VibeCADTimelineOwner, restored_operation)
        self.assertIs(restored_child.VibeCADTimelineOwner, restored_parent)
        self.assertTrue(
            restored_operation.Visibility,
            "Undo must restore the visible operation presentation recorded "
            "by the document timeline, not the deletion snapshot's "
            "transient hidden state.",
        )
        self.assertFalse(source.Visibility)

        self.document.redo()
        _event_step()
        self.assertIsNone(self.document.getObject(child_name))
        self.assertIsNone(self.document.getObject(parent_name))
        self.assertIsNone(self.document.getObject(operation_name))
        self.assertTrue(source.Visibility)

    def test_delete_owner_and_resource_selection_uses_one_owner_plan(self):
        source = self.document.addObject(
            "Part::Feature",
            "TimelineDeleteSource",
        )
        source.Shape = Part.makeBox(6, 5, 4)
        source.Visibility = False

        operation = self.document.addObject(
            "Part::FeaturePython",
            "TimelineDeleteOperation",
        )
        operation.Shape = Part.makeCylinder(3, 5)
        operation.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        operation.addProperty(
            "App::PropertyLinkListHidden",
            "VibeCADTimelineReplacedInputs",
            "Timeline",
        )
        operation.VibeCADTimelineRole = "operation"
        operation.VibeCADTimelineReplacedInputs = [source]
        operation.Visibility = True

        resource = self.document.addObject(
            "Part::FeaturePython",
            "TimelineDeleteResource",
        )
        resource.Shape = Part.makeSphere(2)
        resource.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        resource.addProperty(
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
            "Timeline",
        )
        resource.VibeCADTimelineRole = "resource"
        resource.VibeCADTimelineOwner = operation
        delete_proxy = _OwnerRequiredDeleteViewProxy(operation.Name)
        resource.ViewObject.Proxy = delete_proxy
        resource.Visibility = False
        self.document.recompute()

        self.assertTrue(
            _wait_until(
                lambda: operation.Name in _object_items(self.timeline)
                and resource.Name not in _object_items(self.timeline)
            )
        )
        operation_name = operation.Name
        resource_name = resource.Name
        undo_count = int(self.document.UndoCount)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(operation)
        Gui.Selection.addSelection(resource)
        Gui.runCommand("Std_Delete", 0)
        _event_step()

        self.assertEqual(delete_proxy.calls, 1)
        self.assertTrue(
            delete_proxy.owner_was_live,
            "The resource ViewProvider must be consulted before its semantic "
            "owner is removed.",
        )
        self.assertIsNone(self.document.getObject(resource_name))
        self.assertIsNone(self.document.getObject(operation_name))
        self.assertTrue(source.Visibility)
        self.assertEqual(int(self.document.UndoCount), undo_count + 1)

        self.document.undo()
        _event_step()
        self.assertIsNotNone(self.document.getObject(resource_name))
        self.assertIsNotNone(self.document.getObject(operation_name))
        self.assertFalse(source.Visibility)

    def test_delete_design_operation_removes_its_body_graph_and_undoes_atomically(self):
        self.document.openTransaction("Create global Design box")
        operation = self.document.addObject(
            "PartDesign::DesignBox",
            "TimelineDesignBox",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Length = 12
        operation.Width = 8
        operation.Height = 5
        PartDesign.setDesignOperationTargets(edit, "New Body", [])
        self.document.recompute()
        bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(len(bodies), 1)
        body = bodies[0]
        publication = body.Tip
        state = publication.CurrentState
        operation_name = operation.Name
        body_name = body.Name
        publication_name = publication.Name
        state_name = state.Name
        body_id = str(body.VibeCADBodyId)
        operation_id = str(operation.OperationId)
        self.assertAlmostEqual(body.Shape.Volume, 480.0)
        PartDesign.validateDesign(operation)

        undo_count = int(self.document.UndoCount)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(operation)
        Gui.runCommand("Std_Delete", 0)
        _event_step()

        for name in (
            operation_name,
            body_name,
            publication_name,
            state_name,
        ):
            self.assertIsNone(self.document.getObject(name))
        self.assertEqual(int(self.document.UndoCount), undo_count + 1)
        self.assertFalse(self.document.HasPendingTransaction)

        self.document.undo()
        _event_step()
        restored_operation = self.document.getObject(operation_name)
        restored_body = self.document.getObject(body_name)
        restored_publication = self.document.getObject(publication_name)
        restored_state = self.document.getObject(state_name)
        self.assertIsNotNone(restored_operation)
        self.assertIsNotNone(restored_body)
        self.assertIsNotNone(restored_publication)
        self.assertIsNotNone(restored_state)
        self.assertEqual(str(restored_operation.OperationId), operation_id)
        self.assertEqual(str(restored_body.VibeCADBodyId), body_id)
        self.assertIs(restored_body.Tip, restored_publication)
        self.assertIs(restored_publication.CurrentState, restored_state)
        self.assertAlmostEqual(restored_body.Shape.Volume, 480.0)
        PartDesign.validateDesign(restored_operation)

        self.document.redo()
        _event_step()
        for name in (
            operation_name,
            body_name,
            publication_name,
            state_name,
        ):
            self.assertIsNone(self.document.getObject(name))
        self.assertFalse(self.document.HasPendingTransaction)

    def test_delete_design_body_resolves_to_its_creating_history_operation(self):
        self.document.openTransaction("Create body-selected Design box")
        operation = self.document.addObject(
            "PartDesign::DesignBox",
            "BodySelectedDesignBox",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Length = 12
        operation.Width = 8
        operation.Height = 5
        PartDesign.setDesignOperationTargets(edit, "New Body", [])
        self.document.recompute()
        body = PartDesign.finalizeDesignOperationEdit(edit)[0]
        self.document.commitTransaction()

        publication = body.Tip
        state = publication.CurrentState
        operation_name = operation.Name
        body_name = body.Name
        publication_name = publication.Name
        state_name = state.Name
        body_id = str(body.VibeCADBodyId)
        operation_id = str(operation.OperationId)
        self.assertAlmostEqual(body.Shape.Volume, 480.0)
        PartDesign.validateDesign(operation)

        undo_count = int(self.document.UndoCount)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(body)
        Gui.runCommand("Std_Delete", 0)
        _event_step()

        for name in (
            operation_name,
            body_name,
            publication_name,
            state_name,
        ):
            self.assertIsNone(self.document.getObject(name))
        self.assertEqual(int(self.document.UndoCount), undo_count + 1)
        self.assertFalse(self.document.HasPendingTransaction)

        self.document.undo()
        _event_step()
        restored_operation = self.document.getObject(operation_name)
        restored_body = self.document.getObject(body_name)
        restored_publication = self.document.getObject(publication_name)
        restored_state = self.document.getObject(state_name)
        self.assertIsNotNone(restored_operation)
        self.assertIsNotNone(restored_body)
        self.assertIsNotNone(restored_publication)
        self.assertIsNotNone(restored_state)
        self.assertEqual(str(restored_operation.OperationId), operation_id)
        self.assertEqual(str(restored_body.VibeCADBodyId), body_id)
        self.assertIs(restored_body.Tip, restored_publication)
        self.assertIs(restored_publication.CurrentState, restored_state)
        self.assertAlmostEqual(restored_body.Shape.Volume, 480.0)
        PartDesign.validateDesign(restored_operation)

        self.document.redo()
        _event_step()
        for name in (
            operation_name,
            body_name,
            publication_name,
            state_name,
        ):
            self.assertIsNone(self.document.getObject(name))
        self.assertFalse(self.document.HasPendingTransaction)

    def test_design_body_keeps_one_publication_tip_across_history_navigation(self):
        end = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        previous = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        next_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineNext",
        )
        self.assertIsNotNone(end)
        self.assertIsNotNone(previous)
        self.assertIsNotNone(next_button)
        end.click()
        _event_step()

        self.document.openTransaction("Create navigable Design box")
        operation = self.document.addObject(
            "PartDesign::DesignBox",
            "NavigableDesignBox",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Length = 12
        operation.Width = 8
        operation.Height = 5
        PartDesign.setDesignOperationTargets(edit, "New Body", [])
        self.document.recompute()
        body = PartDesign.finalizeDesignOperationEdit(edit)[0]
        self.document.commitTransaction()

        publication = body.Tip
        state = publication.CurrentState
        controller = _document_timeline(self.document)
        block_start = list(controller.Operations).index(state)
        block_end = list(controller.Operations).index(operation) + 1
        publication_id = publication.ID
        publication_name = publication.Name
        self.assertAlmostEqual(body.Shape.Volume, 480.0)
        self.assertIs(body.Tip, publication)

        end.click()
        _event_step()
        previous.click()
        _event_step()
        self.assertEqual(controller.Position, block_start)
        self.assertIs(body.Tip, publication)
        self.assertEqual(body.Tip.ID, publication_id)
        self.assertEqual(body.Tip.Name, publication_name)
        self.assertIs(publication.CurrentState, state)
        self.assertTrue(body.Visibility)
        self.assertTrue(publication.Visibility)
        self.assertTrue(body.Shape.isNull())
        self.assertTrue(publication.Shape.isNull())

        next_button.click()
        _event_step()
        self.assertEqual(controller.Position, block_end)
        self.assertIs(body.Tip, publication)
        self.assertEqual(body.Tip.ID, publication_id)
        self.assertIs(publication.CurrentState, state)
        self.assertTrue(body.Visibility)
        self.assertTrue(publication.Visibility)
        self.assertAlmostEqual(body.Shape.Volume, 480.0)
        self.assertAlmostEqual(publication.Shape.Volume, 480.0)
        PartDesign.validateDesign(operation)

    def test_independent_part_container_deletion_keeps_legacy_questions(self):
        base = self.document.addObject(
            "Part::Box",
            "IndependentBooleanBase",
        )
        tool = self.document.addObject(
            "Part::Box",
            "IndependentBooleanTool",
        )
        tool.Placement.Base = App.Vector(2, 2, 0)
        boolean = self.document.addObject(
            "Part::Cut",
            "IndependentBoolean",
        )
        boolean.Base = base
        boolean.Tool = tool
        self.document.recompute()
        boolean_name = boolean.Name

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(boolean)
        question = _run_delete_and_answer_question(
            QtGui.QMessageBox.No
        )
        self.assertEqual(
            question["title"],
            "Delete Boolean operation content?",
        )
        self.assertIn("base and tool objects", question["text"])
        self.assertIsNone(self.document.getObject(boolean_name))
        self.assertIsNotNone(self.document.getObject(base.Name))
        self.assertIsNotNone(self.document.getObject(tool.Name))

        first = self.document.addObject(
            "Part::Box",
            "IndependentCompoundFirst",
        )
        second = self.document.addObject(
            "Part::Box",
            "IndependentCompoundSecond",
        )
        second.Placement.Base = App.Vector(15, 0, 0)
        compound = self.document.addObject(
            "Part::Compound",
            "IndependentCompound",
        )
        compound.Links = [first, second]
        self.document.recompute()
        compound_name = compound.Name

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(compound)
        question = _run_delete_and_answer_question(
            QtGui.QMessageBox.No
        )
        self.assertEqual(
            question["title"],
            "Delete compound content?",
        )
        self.assertIn("2 child objects", question["text"])
        self.assertIsNone(self.document.getObject(compound_name))
        self.assertIsNotNone(self.document.getObject(first.Name))
        self.assertIsNotNone(self.document.getObject(second.Name))

    def test_delete_refuses_a_standalone_timeline_resource_without_mutation(self):
        operation = self.document.addObject(
            "Part::Feature",
            "StandaloneResourceOwner",
        )
        operation.Label = "Standalone Owner Label"
        operation.Shape = Part.makeBox(4, 4, 4)
        operation.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        operation.VibeCADTimelineRole = "operation"
        operation.Visibility = True

        resource = self.document.addObject(
            "Part::Feature",
            "StandaloneTimelineResource",
        )
        resource.Shape = Part.makeSphere(2)
        resource.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        resource.addProperty(
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
            "Timeline",
        )
        resource.VibeCADTimelineRole = "resource"
        resource.VibeCADTimelineOwner = operation
        resource.Visibility = False
        self.document.recompute()

        before_objects = tuple(self.document.Objects)
        before_visibility = (
            operation.Visibility,
            resource.Visibility,
        )
        before_undo = int(self.document.UndoCount)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(resource)
        error = _run_delete_and_capture_error()

        self.assertEqual(error["title"], "Delete Failed")
        self.assertIn(
            "belongs to the history operation 'Standalone Owner Label'",
            error["text"],
        )
        self.assertIn(
            "Delete or edit that operation in History",
            error["text"],
        )
        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertEqual(
            (operation.Visibility, resource.Visibility),
            before_visibility,
        )
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_delete_rejects_malformed_orphaned_and_cyclic_resource_metadata(self):
        orphan = self.document.addObject(
            "Part::Feature",
            "OrphanedTimelineResource",
        )
        orphan.Shape = Part.makeBox(2, 2, 2)
        orphan.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        orphan.addProperty(
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
            "Timeline",
        )
        orphan.VibeCADTimelineRole = "resource"
        orphan.Visibility = False

        malformed = self.document.addObject(
            "Part::Feature",
            "MalformedTimelineResource",
        )
        malformed.Shape = Part.makeCone(2, 1, 3)
        malformed.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        malformed.addProperty(
            "App::PropertyString",
            "VibeCADTimelineOwner",
            "Timeline",
        )
        malformed.VibeCADTimelineRole = "resource"
        malformed.VibeCADTimelineOwner = "not a hidden object link"

        first = self.document.addObject(
            "Part::Feature",
            "CyclicDeleteResourceFirst",
        )
        second = self.document.addObject(
            "Part::Feature",
            "CyclicDeleteResourceSecond",
        )
        for resource in (first, second):
            resource.Shape = Part.makeCylinder(1, 3)
            resource.addProperty(
                "App::PropertyString",
                "VibeCADTimelineRole",
                "Timeline",
            )
            resource.addProperty(
                "App::PropertyLinkHidden",
                "VibeCADTimelineOwner",
                "Timeline",
            )
            resource.VibeCADTimelineRole = "resource"
        first.VibeCADTimelineOwner = second
        second.VibeCADTimelineOwner = first
        first.Visibility = True
        second.Visibility = False
        self.document.recompute()

        for selected, expected in (
            (orphan, "missing or malformed owner metadata"),
            (malformed, "missing or malformed owner metadata"),
            (first, "cyclic owner metadata"),
        ):
            with self.subTest(resource=selected.Name):
                before_objects = tuple(self.document.Objects)
                before_visibility = {
                    obj.Name: obj.Visibility
                    for obj in (orphan, malformed, first, second)
                }
                before_undo = int(self.document.UndoCount)
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(selected)
                error = _run_delete_and_capture_error()

                self.assertEqual(error["title"], "Delete Failed")
                self.assertIn(expected, error["text"])
                self.assertEqual(tuple(self.document.Objects), before_objects)
                self.assertEqual(
                    {
                        obj.Name: obj.Visibility
                        for obj in (orphan, malformed, first, second)
                    },
                    before_visibility,
                )
                self.assertEqual(int(self.document.UndoCount), before_undo)
                self.assertFalse(self.document.HasPendingTransaction)
                self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_semantic_operation_edits_its_owned_implementation_object(self):
        operation = self.document.addObject(
            "App::DocumentObjectGroup",
            "SemanticTimelineOperation",
        )
        operation.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        operation.VibeCADTimelineRole = "operation"

        editor = self.document.addObject(
            "App::FeaturePython",
            "SemanticTimelineEditor",
        )
        editor.ViewObject.Proxy = _NoPanelEditViewProxy()
        editor.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        editor.addProperty(
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
            "Timeline",
        )
        editor.VibeCADTimelineOwner = operation
        editor.VibeCADTimelineRole = "resource"
        operation.addProperty(
            "App::PropertyLinkHidden",
            "VibeCADTimelineEditor",
            "Timeline",
        )
        operation.VibeCADTimelineEditor = editor

        item = _wait_until(
            lambda: _object_items(self.timeline).get(operation.Name)
        )
        self.assertIsNotNone(item)
        self.assertNotIn(editor.Name, _object_items(self.timeline))
        self.assertEqual(
            operation.getTypeIdOfProperty("VibeCADTimelineEditor"),
            "App::PropertyLinkHidden",
        )

        self.timeline.itemDoubleClicked.emit(item)
        self.assertTrue(
            _wait_until(
                lambda: Gui.activeDocument().getInEdit() is not None
                and Gui.activeDocument().getInEdit().Object is editor
            ),
            "Editing the semantic step must reach its private parametric "
            "implementation object",
        )
        self.assertEqual(Gui.Selection.getSelection(), [editor])
        Gui.activeDocument().resetEdit()
        self.assertTrue(
            _wait_until(lambda: Gui.activeDocument().getInEdit() is None)
        )

        item = _wait_until(
            lambda: _object_items(self.timeline).get(operation.Name)
        )
        _trigger_timeline_action(
            self.timeline,
            item,
            "VibeCADTimelineEdit",
        )
        self.assertTrue(
            _wait_until(
                lambda: Gui.activeDocument().getInEdit() is not None
                and Gui.activeDocument().getInEdit().Object is editor
            ),
            "The semantic step's explicit Edit action must use the same "
            "owned implementation object",
        )
        self.assertEqual(Gui.Selection.getSelection(), [editor])
        Gui.activeDocument().resetEdit()
        self.assertTrue(
            _wait_until(lambda: Gui.activeDocument().getInEdit() is None)
        )

        # A stale or absent target must safely fall back to the semantic
        # operation; it must never edit a formerly linked resource.
        operation.VibeCADTimelineEditor = None
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(operation)
        item = _wait_until(
            lambda: _object_items(self.timeline).get(operation.Name)
        )
        self.timeline.itemDoubleClicked.emit(item)
        _event_step()
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertNotIn(editor, Gui.Selection.getSelection())

        first = self.document.addObject(
            "App::FeaturePython",
            "CyclicTimelineEditor",
        )
        second = self.document.addObject(
            "App::FeaturePython",
            "CyclicTimelineEditorOwner",
        )
        for resource in (first, second):
            resource.addProperty(
                "App::PropertyString",
                "VibeCADTimelineRole",
                "Timeline",
            )
            resource.addProperty(
                "App::PropertyLinkHidden",
                "VibeCADTimelineOwner",
                "Timeline",
            )
            resource.VibeCADTimelineRole = "resource"
        first.VibeCADTimelineOwner = second
        second.VibeCADTimelineOwner = first
        operation.VibeCADTimelineEditor = first

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(operation)
        item = _wait_until(
            lambda: _object_items(self.timeline).get(operation.Name)
        )
        self.timeline.itemDoubleClicked.emit(item)
        _event_step()
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertNotIn(first, Gui.Selection.getSelection())
        self.assertNotIn(second, Gui.Selection.getSelection())

    def test_semantic_replacement_restores_only_valid_live_inputs(self):
        controller = _document_timeline(self.document)
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        previous_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
            )
        )

        def roll_before(operation):
            end_button.click()
            self.assertTrue(
                _wait_until(
                    lambda: controller.Position == len(controller.Operations)
                )
            )
            operation_index = list(controller.Operations).index(operation)
            for _ in range(len(controller.Operations) + 1):
                if controller.Position <= operation_index:
                    break
                previous_button.click()
                _event_step()
            self.assertEqual(controller.Position, operation_index)

        source = self.document.addObject(
            "Part::Feature",
            "SemanticReplacementSource",
        )
        source.Shape = Part.makeBox(4, 5, 6)
        operation = self.document.addObject(
            "App::DocumentObjectGroup",
            "SemanticReplacementOperation",
        )
        operation.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        operation.VibeCADTimelineRole = "operation"
        operation.addProperty(
            "App::PropertyLinkListHidden",
            "VibeCADTimelineReplacedInputs",
            "Timeline",
        )
        operation.VibeCADTimelineReplacedInputs = [source]
        source.Visibility = False
        operation_visibility_at_end = operation.Visibility
        source_index = list(controller.Operations).index(source)
        self.assertFalse(controller.VisibilityAtEnd[source_index])

        roll_before(operation)
        self.assertTrue(source.Visibility)
        self.assertFalse(operation.Visibility)
        self.assertFalse(controller.VisibilityAtEnd[source_index])
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
            )
        )
        self.assertEqual(
            (source.Visibility, operation.Visibility),
            (False, operation_visibility_at_end),
            "Advancing through the semantic operation must restore the saved "
            "end-state visibility",
        )

        malformed_source = self.document.addObject(
            "Part::Feature",
            "MalformedReplacementSource",
        )
        malformed_source.Shape = Part.makeCylinder(2, 5)
        malformed = self.document.addObject(
            "App::DocumentObjectGroup",
            "MalformedReplacementOperation",
        )
        malformed.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        malformed.VibeCADTimelineRole = "operation"
        malformed.addProperty(
            "App::PropertyLinkList",
            "VibeCADTimelineReplacedInputs",
            "Timeline",
        )
        malformed.VibeCADTimelineReplacedInputs = [malformed_source]
        malformed_source.Visibility = False

        roll_before(malformed)
        self.assertFalse(
            malformed_source.Visibility,
            "A public dependency link must not masquerade as the explicit "
            "hidden replacement contract",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
            )
        )

        cyclic_source = self.document.addObject(
            "Part::Feature",
            "CyclicReplacementSource",
        )
        cyclic_source.Shape = Part.makeSphere(3)
        first = self.document.addObject(
            "App::DocumentObjectGroup",
            "CyclicReplacementFirst",
        )
        second = self.document.addObject(
            "App::DocumentObjectGroup",
            "CyclicReplacementSecond",
        )
        for candidate in (first, second):
            candidate.addProperty(
                "App::PropertyString",
                "VibeCADTimelineRole",
                "Timeline",
            )
            candidate.VibeCADTimelineRole = "operation"
            candidate.addProperty(
                "App::PropertyLinkListHidden",
                "VibeCADTimelineReplacedInputs",
                "Timeline",
            )
        first.VibeCADTimelineReplacedInputs = [cyclic_source, second]
        second.VibeCADTimelineReplacedInputs = [first]
        cyclic_source.Visibility = False

        roll_before(first)
        self.assertFalse(
            cyclic_source.Visibility,
            "A cyclic replacement graph must fail closed as one contract; "
            "its otherwise valid sibling link must not leak geometry",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
            )
        )

        owned_source = self.document.addObject(
            "Part::Feature",
            "CyclicOwnerReplacementSource",
        )
        owned_source.Shape = Part.makeCone(3, 1, 5)
        owner_operation = self.document.addObject(
            "App::DocumentObjectGroup",
            "CyclicOwnerReplacementOperation",
        )
        owner_operation.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        owner_operation.VibeCADTimelineRole = "operation"
        owner_operation.addProperty(
            "App::PropertyLinkListHidden",
            "VibeCADTimelineReplacedInputs",
            "Timeline",
        )
        first_resource = self.document.addObject(
            "App::FeaturePython",
            "CyclicReplacementResource",
        )
        second_resource = self.document.addObject(
            "App::FeaturePython",
            "CyclicReplacementResourceOwner",
        )
        for resource in (first_resource, second_resource):
            resource.addProperty(
                "App::PropertyString",
                "VibeCADTimelineRole",
                "Timeline",
            )
            resource.addProperty(
                "App::PropertyLinkHidden",
                "VibeCADTimelineOwner",
                "Timeline",
            )
            resource.VibeCADTimelineRole = "resource"
        first_resource.VibeCADTimelineOwner = second_resource
        second_resource.VibeCADTimelineOwner = first_resource
        owner_operation.VibeCADTimelineReplacedInputs = [
            owned_source,
            first_resource,
        ]
        owned_source.Visibility = False

        position_before_invalid_ownership = controller.Position
        self.assertTrue(
            _wait_until(lambda: not self.timeline.isEnabled()),
            "Cyclic semantic ownership invalidates exact History boundaries",
        )
        self.assertEqual(
            controller.Position,
            position_before_invalid_ownership,
            "Malformed ownership must fail closed without moving History",
        )
        self.assertFalse(
            owned_source.Visibility,
            "Malformed ownership must not reveal a replacement input",
        )

    def test_semantic_replacement_restores_body_native_representation(self):
        controller = _document_timeline(self.document)
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        previous_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
            )
        )

        source_body = self.document.addObject(
            "PartDesign::Body",
            "ReplacementSourceBody",
        )
        source_feature = self.document.addObject(
            "PartDesign::Feature",
            "ReplacementSourceFeature",
        )
        source_shape = Part.makeBox(8, 6, 4, App.Vector(-20, 0, 0))
        source_feature.Shape = source_shape
        source_body.addObject(source_feature)
        source_body.Tip = source_feature
        source_body.Visibility = True
        source_feature.Visibility = True
        self.document.recompute()
        source_brep = source_feature.Shape.exportBrepToString()

        operation = self.document.addObject(
            "App::DocumentObjectGroup",
            "BodyReplacementOperation",
        )
        operation.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        operation.VibeCADTimelineRole = "operation"
        operation.Visibility = True
        operation.addProperty(
            "App::PropertyLinkListHidden",
            "VibeCADTimelineReplacedInputs",
            "Timeline",
        )
        operation.VibeCADTimelineReplacedInputs = [source_body]

        output = self.document.addObject(
            "Part::Feature",
            "BodyReplacementOutput",
        )
        output.Shape = Part.makeCylinder(3, 12, App.Vector(20, 0, 0))
        output.addProperty(
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
            "Timeline",
        )
        output.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        output.VibeCADTimelineOwner = operation
        output.VibeCADTimelineRole = "resource"
        operation.addObject(output)
        output.Visibility = True
        source_body.Visibility = False
        self.document.recompute()

        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
            )
        )
        operation_index = list(controller.Operations).index(operation)
        for _ in range(len(controller.Operations) + 1):
            if controller.Position <= operation_index:
                break
            previous_button.click()
            _event_step()
        self.assertEqual(controller.Position, operation_index)

        self.assertTrue(source_body.Visibility)
        self.assertIs(source_body.Tip, source_feature)
        self.assertTrue(source_feature.Visibility)
        self.assertEqual(source_feature.Shape.exportBrepToString(), source_brep)
        self.assertFalse(output.Visibility)

        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
            )
        )
        self.assertFalse(source_body.Visibility)
        self.assertFalse(source_feature.Visibility)
        self.assertTrue(output.Visibility)
        self.assertAlmostEqual(output.Shape.Volume, Part.makeCylinder(3, 12).Volume)

    def test_owned_resources_follow_operation_without_becoming_history_steps(self):
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        previous_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        next_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineNext",
        )
        end_button.click()
        controller = _document_timeline(self.document)
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
            )
        )

        operation = self.document.addObject(
            "Part::FeaturePython",
            "TimelineDurableOperation",
        )
        operation.Label = "Durable Operation"
        operation.addExtension("App::SuppressibleExtensionPython")
        operation.Shape = Part.makeBox(2, 2, 2)

        resource = self.document.addObject(
            "Part::Feature",
            "TimelineOwnedResource",
        )
        resource.Label = "Owned Resource"
        resource.Shape = Part.makeBox(1, 1, 1)
        resource.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        resource.VibeCADTimelineRole = "resource"
        resource.addProperty(
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
            "Timeline",
        )
        resource.VibeCADTimelineOwner = operation
        resource.setEditorMode("VibeCADTimelineRole", 2)
        resource.setEditorMode("VibeCADTimelineOwner", 2)
        self.assertNotIn(
            operation,
            resource.OutList,
            "Timeline ownership metadata must not become a modeling dependency",
        )
        operation.Visibility = True
        resource.Visibility = True
        self.document.recompute()

        self.assertEqual(
            list(controller.Operations)[-2:],
            [operation, resource],
            "The persisted sequence may retain owned resources so legacy "
            "documents and link restoration remain lossless",
        )
        self.assertTrue(
            _wait_until(
                lambda: operation.Name in _object_items(self.timeline)
                and resource.Name not in _object_items(self.timeline)
            ),
            "An implementation resource must not masquerade as a user history step",
        )
        visible_operation_count = len(_object_items(self.timeline))
        self.assertIn(
            f"{visible_operation_count} of {visible_operation_count}",
            self.timeline.toolTip(),
            "History status must count durable semantic operations, not its "
            "hidden resource identities",
        )

        operation.Suppressed = True
        self.assertTrue(
            _wait_until(lambda: not resource.Visibility),
            "Suppressing an owner must hide its implementation resources",
        )
        operation.Suppressed = False
        self.assertTrue(
            _wait_until(lambda: resource.Visibility),
            "Unsuppressing an owner must restore each resource's accepted visibility",
        )
        operation.Visibility = False
        self.assertTrue(
            _wait_until(lambda: not resource.Visibility),
            "Hiding an owner must hide its implementation resources",
        )
        operation.Visibility = True
        self.assertTrue(
            _wait_until(lambda: resource.Visibility),
            "Showing an owner must restore each resource's accepted visibility",
        )

        _trigger_timeline_action(
            self.timeline,
            _object_items(self.timeline)[operation.Name],
            "VibeCADTimelineSetCurrent",
        )
        operations = list(controller.Operations)
        operation_boundary = operations.index(operation)
        block_end = operations.index(resource) + 1
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == block_end
                and operation.Visibility
                and resource.Visibility
                and not operation.Suppressed
            ),
            "Setting the state after an operation must advance past every "
            "owned implementation resource",
        )

        previous_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == operation_boundary
                and not operation.Visibility
                and not resource.Visibility
                and operation.Suppressed
            ),
            "One Previous action must move to the beginning of the complete "
            "legacy root-first block",
        )

        next_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == block_end
                and operation.Visibility
                and resource.Visibility
                and not operation.Suppressed
            ),
            "One Next action must activate the complete legacy root-first "
            "block without stopping on its hidden resource",
        )

        # A restored legacy document may persist its marker between a visible
        # root and one hidden resource. Navigation is based on the visible
        # root's semantic activity, not on that raw array position.
        controller.Position = operation_boundary + 1
        self.assertTrue(_wait_until(lambda: previous_button.isEnabled()))
        previous_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == operation_boundary
            ),
            "Previous must suppress an active root-first block even when the "
            "restored marker starts inside it",
        )

        # Canonical documents persist resources before their visible root.
        # At the corresponding inside-block marker the root is still inactive,
        # so one Next must activate the whole block.
        operations = list(controller.Operations)
        operations[operation_boundary : block_end] = [
            resource,
            operation,
        ]
        controller.Operations = operations
        controller.Position = operation_boundary + 1
        self.assertTrue(_wait_until(lambda: next_button.isEnabled()))
        next_button.click()
        self.assertTrue(
            _wait_until(lambda: controller.Position == block_end),
            "Next must activate an inactive resource-first block even when "
            "the restored marker starts inside it",
        )

    def test_invalid_resource_ownership_never_promotes_internal_objects(self):
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        end_button.click()
        controller = _document_timeline(self.document)
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
            )
        )

        owner = self.document.addObject(
            "Part::Feature",
            "TimelineDisposableOwner",
        )
        owner.Shape = Part.makeBox(2, 2, 2)
        orphan = self.document.addObject(
            "Part::Feature",
            "TimelineOrphanResource",
        )
        orphan.Shape = Part.makeBox(1, 1, 1)
        orphan.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        orphan.addProperty(
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
            "Timeline",
        )
        orphan.VibeCADTimelineOwner = owner
        orphan.VibeCADTimelineRole = "resource"
        orphan.Visibility = True

        malformed_owner = self.document.addObject(
            "Part::Feature",
            "TimelineMalformedOwner",
        )
        malformed_owner.Shape = Part.makeCylinder(1, 2)
        malformed = self.document.addObject(
            "Part::Feature",
            "TimelineMalformedResource",
        )
        malformed.Shape = Part.makeBox(1, 1, 1)
        malformed.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        malformed.addProperty(
            "App::PropertyLink",
            "VibeCADTimelineOwner",
            "Timeline",
        )
        malformed.VibeCADTimelineOwner = malformed_owner
        malformed.VibeCADTimelineRole = "resource"
        malformed.Visibility = True

        cycle_a = self.document.addObject(
            "Part::Feature",
            "TimelineResourceCycleA",
        )
        cycle_b = self.document.addObject(
            "Part::Feature",
            "TimelineResourceCycleB",
        )
        for resource in (cycle_a, cycle_b):
            resource.Shape = Part.makeBox(1, 1, 1)
            resource.addProperty(
                "App::PropertyString",
                "VibeCADTimelineRole",
                "Timeline",
            )
            resource.addProperty(
                "App::PropertyLinkHidden",
                "VibeCADTimelineOwner",
                "Timeline",
            )
        cycle_a.VibeCADTimelineOwner = cycle_b
        cycle_a.VibeCADTimelineRole = "resource"
        cycle_b.VibeCADTimelineOwner = cycle_a
        cycle_b.VibeCADTimelineRole = "resource"
        cycle_a.Visibility = True
        cycle_b.Visibility = True
        self.document.recompute()

        internal_names = {
            orphan.Name,
            malformed.Name,
            cycle_a.Name,
            cycle_b.Name,
        }
        self.assertTrue(
            _wait_until(
                lambda: internal_names.isdisjoint(_object_items(self.timeline))
                and malformed.Visibility is False
                and cycle_a.Visibility is False
                and cycle_b.Visibility is False
            ),
            _ordered_object_names(self.timeline),
        )

        orphan_name = orphan.Name
        malformed_name = malformed.Name
        cycle_a_name = cycle_a.Name
        cycle_b_name = cycle_b.Name
        self.document.removeObject(owner.Name)
        self.assertTrue(
            _wait_until(
                lambda: orphan.VibeCADTimelineOwner is None
                and orphan.Visibility is False
                and orphan.Name not in _object_items(self.timeline)
            ),
            "Deleting a resource owner must not promote its implementation "
            "object into document history",
        )

        with tempfile.TemporaryDirectory(
            prefix="vibecad_timeline_orphan_",
        ) as temporary_directory:
            path = os.path.join(
                temporary_directory,
                "timeline-resource-ownership.FCStd",
            )
            self.document.saveAs(path)
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(path)
            self.document.UndoMode = True
            self.body = self.document.getObject("TimelineBody")
            self.sketch = self.document.getObject("TimelineSketch")
            self.first = self.document.getObject("FirstResult")
            self.reference = self.document.getObject("ReferencePlane")
            self.second = self.document.getObject("SecondResult")

            restored_orphan = self.document.getObject(orphan_name)
            restored_malformed = self.document.getObject(malformed_name)
            restored_cycle_a = self.document.getObject(cycle_a_name)
            restored_cycle_b = self.document.getObject(cycle_b_name)
            self.assertIsNotNone(restored_orphan)
            self.assertIsNotNone(restored_malformed)
            self.assertIsNotNone(restored_cycle_a)
            self.assertIsNotNone(restored_cycle_b)
            self.assertIsNone(restored_orphan.VibeCADTimelineOwner)
            self.assertEqual(
                restored_malformed.getTypeIdOfProperty(
                    "VibeCADTimelineOwner"
                ),
                "App::PropertyLink",
            )
            self.assertIs(
                restored_cycle_a.VibeCADTimelineOwner,
                restored_cycle_b,
            )
            self.assertIs(
                restored_cycle_b.VibeCADTimelineOwner,
                restored_cycle_a,
            )
            self.assertTrue(
                _wait_until(
                    lambda: {
                        orphan_name,
                        malformed_name,
                        cycle_a_name,
                        cycle_b_name,
                    }.isdisjoint(_object_items(self.timeline))
                    and not restored_orphan.Visibility
                    and not restored_malformed.Visibility
                    and not restored_cycle_a.Visibility
                    and not restored_cycle_b.Visibility
                ),
                _ordered_object_names(self.timeline),
            )
            # Reopened Part shapes finish their restored display one queued
            # object at a time. Drain that finite queue before test teardown
            # destroys the application-level progress presenter.
            for _ in range(len(self.document.Objects) + 2):
                _event_step()

    def test_caller_booked_transaction_disables_history_changes_and_edit(self):
        """Timeline actions never replace a transaction owned by another caller."""

        previous_button = self.timeline_widget.findChild(
            QtGui.QToolButton, "VibeCADFeatureTimelinePrevious"
        )
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton, "VibeCADFeatureTimelineEnd"
        )
        self.assertIsNotNone(previous_button)
        self.assertIsNotNone(end_button)
        original_tip = self.body.Tip
        original_visibility = (
            self.first.Visibility,
            self.second.Visibility,
        )
        original_undo_count = self.document.UndoCount

        self.document.openTransaction("Caller-owned timeline transaction")
        caller_transaction_id = self.document.getBookedTransactionID()
        self.assertNotEqual(caller_transaction_id, 0)
        self.assertFalse(
            self.document.HasPendingTransaction,
            "The contract needs a booked transaction with no document delta",
        )

        try:
            _event_step()
            self.assertFalse(self.timeline.isEnabled())
            self.assertFalse(previous_button.isEnabled())
            self.assertFalse(end_button.isEnabled())
            previous_button.click()
            _event_step()

            sketch_item = _object_items(self.timeline)[self.sketch.Name]
            self.timeline.itemDoubleClicked.emit(sketch_item)
            _event_step()

            self.assertEqual(
                self.document.getBookedTransactionID(),
                caller_transaction_id,
            )
            self.assertFalse(self.document.HasPendingTransaction)
            self.assertIs(self.body.Tip, original_tip)
            self.assertEqual(
                (self.first.Visibility, self.second.Visibility),
                original_visibility,
            )
            self.assertIsNone(Gui.activeDocument().getInEdit())
            self.assertFalse(Gui.Control.activeDialog())
            self.assertEqual(self.document.UndoCount, original_undo_count)
        finally:
            if (
                self.document.getBookedTransactionID()
                == caller_transaction_id
            ):
                self.document.abortTransaction()
        self.assertTrue(
            _wait_until(
                lambda: self.timeline.isEnabled()
                and end_button.isEnabled()
            ),
            "Closing an empty caller transaction must re-enable history "
            "without requiring a model mutation",
        )

    def test_history_change_rejects_reentrant_transaction_operations(self):
        """Tip callbacks cannot replace, commit, or abort the history move."""

        document = self.document
        body = self.body

        class ReentrantTransactionObserver:
            def __init__(self):
                self.observed = None
                self.application_replacement = None
                self.error = None

            def slotChangedObject(self, obj, property_name):
                if (
                    self.observed is not None
                    or self.error is not None
                    or obj.Document.Name != document.Name
                    or obj.Name != body.Name
                    or property_name != "Tip"
                ):
                    return
                try:
                    transaction_id = document.getBookedTransactionID()
                    observed = [transaction_id]

                    document.openTransaction(
                        "Reentrant timeline replacement"
                    )
                    observed.append(document.getBookedTransactionID())

                    self.application_replacement = App.setActiveTransaction(
                        "Reentrant application replacement"
                    )
                    observed.append(document.getBookedTransactionID())

                    document.commitTransaction()
                    observed.append(document.getBookedTransactionID())

                    document.abortTransaction()
                    observed.append(document.getBookedTransactionID())
                    self.observed = tuple(observed)
                except Exception as error:  # pragma: no cover - diagnostic
                    self.error = error

        observer = ReentrantTransactionObserver()
        App.addDocumentObserver(observer)
        try:
            previous_button = self.timeline_widget.findChild(
                QtGui.QToolButton,
                "VibeCADFeatureTimelinePrevious",
            )
            self.assertIsNotNone(previous_button)
            previous_button.click()
            self.assertTrue(_wait_until(lambda: self.body.Tip is None))
        finally:
            App.removeDocumentObserver(observer)

        self.assertIsNone(observer.error)
        self.assertIsNotNone(observer.observed)
        transaction_id = observer.observed[0]
        self.assertNotEqual(0, transaction_id)
        self.assertEqual(
            (transaction_id,) * len(observer.observed),
            observer.observed,
        )
        self.assertEqual(0, observer.application_replacement)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(0, self.document.getBookedTransactionID())
        self.assertTrue(self.first.Visibility is False)
        self.assertTrue(self.second.Visibility is False)

    def test_rejected_history_move_does_not_abort_close_callback_successor(self):
        """Rollback may close only the timeline's exact transaction."""

        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and _marker_position(self.timeline)
                == len(_document_timeline(self.document).Operations)
            )
        )

        failing = self.document.addObject(
            "PartDesign::FeaturePython",
            "RejectedHistorySuccessorProbe",
        )
        failing.Label = "Rejected History Successor Probe"
        proxy = _TimelineExecutionProxy()
        failing.Proxy = proxy
        self.body.addObject(failing)
        self.document.recompute()
        self.assertTrue(
            _wait_until(
                lambda: failing.Name in _object_items(self.timeline)
            )
        )
        _trigger_timeline_action(
            self.timeline,
            _object_items(self.timeline)[self.first.Name],
            "VibeCADTimelineSetCurrent",
        )
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.first
                and self.first.Visibility
                and not self.second.Visibility
                and not failing.Visibility
            )
        )

        observer = _TransactionCloseSuccessor(
            self.document,
            expected_abort=True,
        )
        App.addDocumentObserver(observer)
        try:
            proxy.fail = True
            failing.touch()
            observer.armed = True
            end_button.click()
            self.assertTrue(
                _wait_until(
                    lambda: observer.successor_id
                    and self.document.getBookedTransactionID()
                    == observer.successor_id
                )
            )

            self.assertIsNone(observer.error)
            self.assertIsNotNone(observer.sentinel)
            self.assertIs(
                self.document.getObject(observer.sentinel.Name),
                observer.sentinel,
            )
            self.assertTrue(self.document.HasPendingTransaction)
            self.assertIs(self.body.Tip, self.first)
            self.assertTrue(self.first.Visibility)
            self.assertFalse(self.second.Visibility)
            self.assertFalse(failing.Visibility)
        finally:
            observer.armed = False
            App.removeDocumentObserver(observer)
            if (
                observer.successor_id
                and self.document.getBookedTransactionID()
                == observer.successor_id
            ):
                self.document.abortTransaction()
            _event_step()

    def test_no_undo_history_move_preserves_close_callback_successor(self):
        """A valid exact close cannot claim its callback's successor."""

        self.document.UndoMode = False
        observer = _TransactionCloseSuccessor(
            self.document,
            expected_abort=False,
        )
        App.addDocumentObserver(observer)
        try:
            observer.armed = True
            end_button = self.timeline_widget.findChild(
                QtGui.QToolButton,
                "VibeCADFeatureTimelineEnd",
            )
            end_button.click()
            self.assertTrue(
                _wait_until(
                    lambda: observer.successor_id
                    and self.document.getBookedTransactionID()
                    == observer.successor_id
                )
            )

            self.assertIsNone(observer.error)
            self.assertIs(self.body.Tip, self.second)
            self.assertFalse(self.first.Visibility)
            self.assertTrue(self.second.Visibility)
            self.assertIsNotNone(observer.sentinel)
            self.assertIs(
                self.document.getObject(observer.sentinel.Name),
                observer.sentinel,
            )
            self.assertTrue(self.document.HasPendingTransaction)
            self.assertTrue(
                self.document.UndoMode,
                "The private rollback journal must stay live while its "
                "independent successor is open",
            )
        finally:
            observer.armed = False
            App.removeDocumentObserver(observer)
            if (
                observer.successor_id
                and self.document.getBookedTransactionID()
                == observer.successor_id
            ):
                self.document.abortTransaction()
            _event_step()

        self.assertFalse(
            self.document.UndoMode,
            "Closing the independent successor must restore the user's "
            "original UndoMode=0 setting",
        )
        self.assertIs(self.body.Tip, self.second)

    def test_rejected_feature_edit_does_not_leave_a_pending_transaction(self):
        immutable = self.document.addObject(
            "PartDesign::FeatureBase",
            "ImmutableFeatureBase",
        )
        immutable.Label = "Immutable Imported Result"
        immutable.Shape = Part.makeCylinder(2, 4)
        immutable.setPropertyStatus("Placement", "ReadOnly")
        self.body.addObject(immutable)
        self.body.Tip = immutable
        self.document.recompute()

        item = _wait_until(
            lambda: _object_items(self.timeline).get(immutable.Name)
        )
        self.assertIsNotNone(item)
        self.assertFalse(self.document.HasPendingTransaction)

        self.timeline.itemDoubleClicked.emit(item)
        _event_step(50)

        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertTrue(self.timeline.isEnabled())

    def test_handled_callback_without_editor_is_rejected_exactly(self):
        misleading = self.document.addObject(
            "PartDesign::FeaturePython",
            "HandledWithoutEditor",
        )
        misleading.Label = "Handled Without Editor"
        misleading.Shape = Part.makeCylinder(2, 4)
        misleading.ViewObject.Proxy = _HandledWithoutEditViewProxy()
        self.body.addObject(misleading)
        self.document.recompute()

        item = _wait_until(
            lambda: _object_items(self.timeline).get(misleading.Name)
        )
        self.assertIsNotNone(item)
        _trigger_timeline_action(
            self.timeline,
            _object_items(self.timeline)[self.first.Name],
            "VibeCADTimelineSetCurrent",
        )
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.first
                and not misleading.Visibility
            )
        )
        item = _object_items(self.timeline)[misleading.Name]
        self.assertFalse(self.document.HasPendingTransaction)
        python_console = Gui.getMainWindow().findChild(
            QtGui.QPlainTextEdit,
            "Python console",
        )
        self.assertIsNotNone(python_console)
        self.assertNotIn(
            _HandledWithoutEditViewProxy.TRACE_PROBE,
            python_console.toPlainText(),
        )

        self.timeline.itemDoubleClicked.emit(item)
        _event_step(50)

        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertIs(self.body.Tip, self.first)
        self.assertTrue(self.timeline.isEnabled())
        self.assertNotIn(
            _HandledWithoutEditViewProxy.TRACE_PROBE,
            python_console.toPlainText(),
            "A rejected editor launch must discard its captured macro trace",
        )

    def test_redirected_editor_is_rejected_before_transaction_rollback(self):
        """Editing one timeline item cannot adopt another object's editor."""

        redirected_target = self.document.addObject(
            "PartDesign::FeaturePython",
            "RedirectedTimelineEditTarget",
        )
        redirected_target.Label = "Redirected Timeline Edit Target"
        redirected_target.Shape = Part.makeCylinder(1, 3)
        redirected_target.ViewObject.Proxy = _NoPanelEditViewProxy()
        self.body.addObject(redirected_target)

        misleading = self.document.addObject(
            "PartDesign::FeaturePython",
            "RedirectedTimelineEditSource",
        )
        misleading.Label = "Redirected Timeline Edit Source"
        misleading.Shape = Part.makeBox(2, 2, 2)
        misleading.ViewObject.Proxy = _RedirectedEditViewProxy(
            redirected_target.Name
        )
        self.body.addObject(misleading)
        self.document.recompute()

        item = _wait_until(
            lambda: _object_items(self.timeline).get(misleading.Name)
        )
        self.assertIsNotNone(item)
        _trigger_timeline_action(
            self.timeline,
            _object_items(self.timeline)[self.first.Name],
            "VibeCADTimelineSetCurrent",
        )
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.first
                and not redirected_target.Visibility
                and not misleading.Visibility
            )
        )
        item = _object_items(self.timeline)[misleading.Name]
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertFalse(self.document.HasPendingTransaction)

        self.timeline.itemDoubleClicked.emit(item)
        _event_step(50)

        self.assertIsNone(
            Gui.activeDocument().getInEdit(),
            "A redirected editor must be torn down before its borrowed "
            "transaction is rolled back",
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertIs(self.body.Tip, self.first)
        self.assertTrue(self.first.Visibility)
        self.assertFalse(redirected_target.Visibility)
        self.assertFalse(misleading.Visibility)
        self.assertTrue(self.timeline.isEnabled())

    def test_editor_without_task_panel_owns_and_closes_exact_transaction(self):
        """A real no-panel editor still has one complete edit lifecycle."""

        editable = self.document.addObject(
            "PartDesign::FeaturePython",
            "NoPanelTimelineEditor",
        )
        editable.Label = "No-panel Timeline Editor"
        editable.Shape = Part.makeCylinder(2, 4)
        editable.ViewObject.Proxy = _NoPanelEditViewProxy()
        self.body.addObject(editable)
        self.body.Tip = editable
        self.first.Visibility = False
        self.second.Visibility = False
        editable.Visibility = True
        self.document.recompute()

        item = _wait_until(
            lambda: _object_items(self.timeline).get(editable.Name)
        )
        self.assertIsNotNone(item)
        self.document.UndoMode = False

        self.timeline.itemDoubleClicked.emit(item)
        self.assertTrue(
            _wait_until(
                lambda: Gui.activeDocument().getInEdit() is not None
                and self.document.getBookedTransactionID() != 0
            )
        )
        owned_transaction = self.document.getBookedTransactionID()
        self.assertIs(
            Gui.activeDocument().getInEdit().Object,
            editable,
        )
        self.assertFalse(
            Gui.Control.activeDialog(),
            "The test editor intentionally has no TaskDialog",
        )
        self.assertFalse(
            self.timeline.isEnabled(),
            "History cannot be changed while any native editor is active",
        )

        Gui.activeDocument().resetEdit()
        self.assertTrue(
            _wait_until(
                lambda: Gui.activeDocument().getInEdit() is None
                and self.document.getBookedTransactionID() == 0
                and not self.document.HasPendingTransaction
            ),
            "Leaving a no-panel editor must close its exact transaction",
        )
        self.assertNotEqual(owned_transaction, 0)
        self.assertFalse(
            self.document.UndoMode,
            "The timeline's private rollback journal must restore the "
            "user's UndoMode=0 setting",
        )
        self.assertTrue(
            _wait_until(lambda: self.timeline.isEnabled()),
        )

    def test_context_action_resolves_target_identity_after_document_mutation(self):
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and _marker_position(self.timeline)
                == len(_document_timeline(self.document).Operations)
            )
        )
        second_item = _object_items(self.timeline)[self.second.Name]
        self.timeline.scrollToItem(second_item)
        _event_step()
        state = {}

        def mutate_history():
            inserted = self.document.addObject(
                "PartDesign::Feature",
                "InsertedDuringTimelineMenu",
            )
            inserted.Label = "Inserted During Timeline Menu"
            inserted.Shape = Part.makeCylinder(1, 3)
            self.body.addObject(inserted)
            inserted.Visibility = False
            self.document.recompute()
            state["inserted"] = inserted

        def trigger_set_current():
            popup = QtGui.QApplication.activePopupWidget()
            if popup is None:
                state["error"] = "No active timeline context menu"
                return
            try:
                action = next(
                    (
                        candidate
                        for candidate in popup.actions()
                        if candidate.objectName()
                        == "VibeCADTimelineSetCurrent"
                    ),
                    None,
                )
                if action is None:
                    state["error"] = "Set Current action was unavailable"
                else:
                    action.trigger()
                    state["triggered"] = True
            finally:
                popup.close()

        QtCore.QTimer.singleShot(0, mutate_history)
        QtCore.QTimer.singleShot(30, trigger_set_current)
        self.timeline.customContextMenuRequested.emit(
            self.timeline.visualItemRect(second_item).center()
        )

        self.assertNotIn("error", state, state)
        self.assertTrue(state.get("triggered"), state)
        self.assertIs(self.body.Tip, self.second)
        self.assertIsNot(self.body.Tip, state["inserted"])
        self.assertTrue(self.second.Visibility)
        self.assertFalse(state["inserted"].Visibility)
        timeline_object = _document_timeline(self.document)
        second_index = next(
            index
            for index, operation in enumerate(timeline_object.Operations)
            if operation is self.second
        )
        self.assertEqual(timeline_object.Position, second_index + 1)
        self.assertTrue(
            _wait_until(
                lambda: _marker_position(self.timeline)
                == second_index + 1
            ),
            {
                "document_position": timeline_object.Position,
                "marker_position": _marker_position(self.timeline),
            },
        )

    def test_context_action_refuses_target_after_active_document_changes(self):
        second_item = _object_items(self.timeline)[self.second.Name]
        self.timeline.scrollToItem(second_item)
        _event_step()
        state = {}

        def activate_other_document():
            other = App.newDocument("FeatureTimelineContextSwitch")
            other.Label = "Timeline Context Switch"
            state["other"] = other

        def trigger_set_current():
            popup = QtGui.QApplication.activePopupWidget()
            if popup is None:
                state["error"] = "No active timeline context menu"
                return
            try:
                action = next(
                    (
                        candidate
                        for candidate in popup.actions()
                        if candidate.objectName()
                        == "VibeCADTimelineSetCurrent"
                    ),
                    None,
                )
                if action is None:
                    state["error"] = "Set Current action was unavailable"
                else:
                    action.trigger()
                    state["triggered"] = True
            finally:
                popup.close()

        QtCore.QTimer.singleShot(0, activate_other_document)
        QtCore.QTimer.singleShot(30, trigger_set_current)
        self.timeline.customContextMenuRequested.emit(
            self.timeline.visualItemRect(second_item).center()
        )

        try:
            self.assertNotIn("error", state, state)
            self.assertTrue(state.get("triggered"), state)
            self.assertIn("other", state)
            self.assertEqual(
                App.ActiveDocument.Name,
                state["other"].Name,
            )
            self.assertIs(
                self.body.Tip,
                self.first,
                "A menu opened for an inactive document must not change it",
            )
            self.assertTrue(self.first.Visibility)
            self.assertFalse(self.second.Visibility)
            self.assertFalse(self.document.HasPendingTransaction)
        finally:
            if App.getDocument("FeatureTimelineContextSwitch") is not None:
                App.closeDocument("FeatureTimelineContextSwitch")
            if App.getDocument(self.document.Name) is not None:
                App.setActiveDocument(self.document.Name)
            _event_step()

    def test_context_action_refuses_same_name_replacement(self):
        """A rebuilt row must never retarget a newly created same-name object."""

        second_name = self.second.Name
        second_id = self.second.ID
        second_item = _object_items(self.timeline)[second_name]
        self.timeline.scrollToItem(second_item)
        _event_step()
        state = {}

        def replace_target():
            self.document.removeObject(second_name)
            replacement = self.document.addObject(
                "PartDesign::Feature",
                second_name,
            )
            replacement.Label = "Replacement With Reused Name"
            replacement.Shape = Part.makeCylinder(2, 5)
            self.body.addObject(replacement)
            controller = _document_timeline(self.document)
            controller.Position = next(
                index + 1
                for index, operation in enumerate(controller.Operations)
                if operation is self.first
            )
            self.body.Tip = self.first
            self.first.Visibility = True
            replacement.Visibility = False
            self.document.recompute()
            state["replacement"] = replacement

        def trigger_set_current():
            popup = QtGui.QApplication.activePopupWidget()
            if popup is None:
                state["error"] = "No active timeline context menu"
                return
            try:
                action = next(
                    (
                        candidate
                        for candidate in popup.actions()
                        if candidate.objectName()
                        == "VibeCADTimelineSetCurrent"
                    ),
                    None,
                )
                if action is None:
                    state["error"] = "Set Current action was unavailable"
                else:
                    action.trigger()
                    state["triggered"] = True
            finally:
                popup.close()

        QtCore.QTimer.singleShot(0, replace_target)
        QtCore.QTimer.singleShot(30, trigger_set_current)
        self.timeline.customContextMenuRequested.emit(
            self.timeline.visualItemRect(second_item).center()
        )

        self.assertNotIn("error", state, state)
        self.assertTrue(state.get("triggered"), state)
        replacement = state["replacement"]
        self.assertEqual(replacement.Name, second_name)
        self.assertNotEqual(replacement.ID, second_id)
        self.assertIs(self.body.Tip, self.first)
        self.assertTrue(self.first.Visibility)
        self.assertFalse(replacement.Visibility)
        self.assertFalse(self.document.HasPendingTransaction)
        controller = _document_timeline(self.document)
        self.assertEqual(
            controller.Position,
            next(
                index + 1
                for index, operation in enumerate(controller.Operations)
                if operation is self.first
            ),
        )

    def test_context_action_refuses_document_round_trip(self):
        """Leaving and returning to a document invalidates an open menu."""

        second_item = _object_items(self.timeline)[self.second.Name]
        self.timeline.scrollToItem(second_item)
        _event_step()
        state = {}

        def round_trip_active_document():
            other = App.newDocument("FeatureTimelineRoundTrip")
            state["other"] = other
            App.setActiveDocument(self.document.Name)

        def trigger_set_current():
            popup = QtGui.QApplication.activePopupWidget()
            if popup is None:
                state["error"] = "No active timeline context menu"
                return
            try:
                action = next(
                    (
                        candidate
                        for candidate in popup.actions()
                        if candidate.objectName()
                        == "VibeCADTimelineSetCurrent"
                    ),
                    None,
                )
                if action is None:
                    state["error"] = "Set Current action was unavailable"
                else:
                    action.trigger()
                    state["triggered"] = True
            finally:
                popup.close()

        QtCore.QTimer.singleShot(0, round_trip_active_document)
        QtCore.QTimer.singleShot(30, trigger_set_current)
        self.timeline.customContextMenuRequested.emit(
            self.timeline.visualItemRect(second_item).center()
        )

        try:
            self.assertNotIn("error", state, state)
            self.assertTrue(state.get("triggered"), state)
            self.assertEqual(App.ActiveDocument.Name, self.document.Name)
            self.assertIs(self.body.Tip, self.first)
            self.assertTrue(self.first.Visibility)
            self.assertFalse(self.second.Visibility)
            self.assertFalse(self.document.HasPendingTransaction)
        finally:
            if App.getDocument("FeatureTimelineRoundTrip") is not None:
                App.closeDocument("FeatureTimelineRoundTrip")
            _event_step()

    def test_marker_drag_refuses_active_document_switch(self):
        """A drag belongs to the exact document generation where it began."""

        self.assertIs(self.body.Tip, self.first)
        _marker, release_position = _arm_marker_drag_to_end(self.timeline)
        other = App.newDocument("FeatureTimelineMarkerSwitch")
        try:
            self.assertTrue(
                _wait_until(
                    lambda: App.ActiveDocument is other
                    and not self.timeline.isEnabled()
                )
            )
            _send_mouse_event(
                self.timeline.viewport(),
                QtCore.QEvent.MouseButtonRelease,
                release_position,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoButton,
            )
            _event_step(50)

            self.assertIs(self.body.Tip, self.first)
            self.assertTrue(self.first.Visibility)
            self.assertFalse(self.second.Visibility)
            self.assertFalse(self.document.HasPendingTransaction)
            self.assertFalse(other.HasPendingTransaction)
        finally:
            if App.getDocument(other.Name) is not None:
                App.closeDocument(other.Name)
            App.setActiveDocument(self.document.Name)
            self.assertTrue(
                _wait_until(
                    lambda: self.first.Name in _object_items(self.timeline)
                )
            )

    def test_marker_drag_is_cancelled_by_same_document_rebuild(self):
        """A rebuilt timeline cannot reinterpret an already armed drag."""

        self.assertIs(self.body.Tip, self.first)
        _marker, release_position = _arm_marker_drag_to_end(self.timeline)
        self.second.Label = "Second Result Rebuilt During Marker Drag"
        self.assertTrue(
            _wait_until(
                lambda: "Second Result Rebuilt During Marker Drag"
                in _object_items(self.timeline)[self.second.Name].toolTip()
            )
        )

        _send_mouse_event(
            self.timeline.viewport(),
            QtCore.QEvent.MouseButtonRelease,
            release_position,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoButton,
        )
        _event_step(50)

        self.assertIs(self.body.Tip, self.first)
        self.assertTrue(self.first.Visibility)
        self.assertFalse(self.second.Visibility)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_hidden_published_implementation_is_not_a_history_target(self):
        """Navigation uses exactly the result operations shown to the user."""

        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and _marker_position(self.timeline)
                == len(_document_timeline(self.document).Operations)
            )
        )

        def tag_scripted_object(obj, role):
            values = {
                "VibeCADScriptedRole": role,
                "VibeCADScriptedEngine": "vibescript:partdesign",
                "VibeCADScriptedModelId": "timeline-publication-model",
                "VibeCADScriptedOutputKey": "TimelineSolid",
                "VibeCADPublishedRevision": "accepted",
            }
            for name, value in values.items():
                if name not in obj.PropertiesList:
                    obj.addProperty(
                        "App::PropertyString",
                        name,
                        "VibeCAD Publication",
                    )
                setattr(obj, name, value)

        hidden = self.document.addObject(
            "PartDesign::Feature",
            "HiddenPublishedImplementation",
        )
        hidden.Label = "Hidden Published Implementation"
        hidden.Shape = Part.makeCylinder(2, 6)
        tag_scripted_object(hidden, "publication_target")
        self.body.addObject(hidden)
        publication = self.document.addObject(
            "App::Link",
            "TimelinePublishedOutput",
        )
        publication.Label = "Timeline Published Output"
        publication.LinkedObject = hidden
        tag_scripted_object(publication, "publication")
        hidden.Visibility = False
        self.document.recompute()
        self.assertTrue(
            _wait_until(
                lambda: hidden.Name not in _object_items(self.timeline)
                and publication.Name in _object_items(self.timeline)
                and self.first.Name in _object_items(self.timeline)
                and self.second.Name in _object_items(self.timeline)
            ),
            _ordered_object_names(self.timeline),
        )

        _trigger_timeline_action(
            self.timeline,
            _object_items(self.timeline)[self.first.Name],
            "VibeCADTimelineSetCurrent",
        )
        self.assertTrue(
            _wait_until(
                lambda: hidden.Name not in _object_items(self.timeline)
                and publication.Name in _object_items(self.timeline)
                and self.second.Name in _object_items(self.timeline)
                and self.body.Tip is self.first
            )
        )
        self.assertIsNotNone(end_button)
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and self.second.Visibility
                and not hidden.Visibility
                and publication.Visibility
            )
        )

    def test_document_close_invalidates_queued_timeline_refresh(self):
        """Closing the observed document leaves no queued object pointers."""

        other = App.newDocument("FeatureTimelineDeleted")
        other_body = other.addObject(
            "PartDesign::Body",
            "DeletedTimelineBody",
        )
        other_result = other.addObject(
            "PartDesign::Feature",
            "DeletedTimelineResult",
        )
        other_result.Shape = Part.makeBox(2, 2, 2)
        other_body.addObject(other_result)
        other.recompute()
        self.assertTrue(
            _wait_until(
                lambda: other_result.Name in _object_items(self.timeline)
            )
        )

        # Relabel schedules a zero-delay rebuild. Close the document before
        # that callback runs; the queued callback must resolve only the new
        # document context and never dereference the deleted result.
        other_result.Label = "Schedules Refresh Before Close"
        App.closeDocument(other.Name)
        self.assertTrue(
            _wait_until(
                lambda: App.ActiveDocument is self.document
                and self.first.Name in _object_items(self.timeline)
            ),
            "Closing the active document must resolve the queued refresh "
            "against the document FreeCAD reactivates, never against deleted "
            "objects",
        )
        self.assertTrue(self.timeline.isEnabled())

    def test_legacy_document_bootstrap_preserves_every_body_tip_boundary(self):
        legacy = App.newDocument("FeatureTimelineLegacy")
        legacy.UndoMode = True
        body = legacy.addObject("PartDesign::Body", "LegacyBody")
        sketch = legacy.addObject(
            "Sketcher::SketchObject",
            "LegacySketch",
        )
        body.addObject(sketch)
        first = legacy.addObject("PartDesign::Feature", "LegacyFirst")
        first.Shape = Part.makeBox(2, 2, 2)
        body.addObject(first)
        datum = legacy.addObject("PartDesign::Plane", "LegacyDatum")
        body.addObject(datum)
        second = legacy.addObject("PartDesign::Feature", "LegacySecond")
        second.Shape = Part.makeBox(3, 3, 3)
        body.addObject(second)
        body.Tip = first
        first.Visibility = True
        second.Visibility = False
        legacy.recompute()

        controller = _document_timeline(legacy)
        self.assertIsNotNone(controller)
        legacy.removeObject(controller.Name)
        self.assertIsNone(
            _document_timeline(legacy),
            "The fixture must actually model a file written before the "
            "document-timeline object existed",
        )
        expected_order = [
            body.Name,
            sketch.Name,
            first.Name,
            datum.Name,
            second.Name,
        ]

        reopened = None
        with tempfile.TemporaryDirectory(
            prefix="vibecad_legacy_document_timeline_",
        ) as temporary_directory:
            path = os.path.join(temporary_directory, "legacy.FCStd")
            legacy.saveAs(path)
            App.closeDocument(legacy.Name)
            reopened = App.openDocument(path)
            try:
                controller = _document_timeline(reopened)
                self.assertIsNotNone(controller)
                self.assertEqual(
                    [obj.Name for obj in controller.Operations],
                    expected_order,
                )
                self.assertEqual(
                    controller.Position,
                    3,
                    "Legacy migration must preserve Body+Sketch+First as the "
                    "active prefix instead of silently moving to end",
                )
            finally:
                if (
                    reopened is not None
                    and App.getDocument(reopened.Name) is not None
                ):
                    App.closeDocument(reopened.Name)
                App.setActiveDocument(self.document.Name)
                self.assertTrue(
                    _wait_until(
                        lambda: self.first.Name
                        in _object_items(self.timeline)
                    )
                )

    def test_order_and_position_survive_save_reopen_and_undo_redo(self):
        expected_order = [
            self.body.Name,
            self.sketch.Name,
            self.first.Name,
            self.reference.Name,
            self.second.Name,
        ]
        timeline_object = _document_timeline(self.document)
        self.assertIsNotNone(timeline_object)
        self.assertEqual(
            [obj.Name for obj in timeline_object.Operations],
            expected_order,
        )
        self.assertEqual(timeline_object.Position, 3)
        self.assertEqual(timeline_object.SchemaVersion, 2)
        self.assertEqual(
            list(timeline_object.SuppressionAtEnd),
            [False] * len(expected_order),
        )
        self.assertEqual(_marker_position(self.timeline), 3)

        with tempfile.TemporaryDirectory(
            prefix="vibecad_document_timeline_",
        ) as temporary_directory:
            path = os.path.join(temporary_directory, "timeline.FCStd")
            self.document.saveAs(path)
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(path)
            self.document.UndoMode = True
            self.body = self.document.getObject("TimelineBody")
            self.sketch = self.document.getObject("TimelineSketch")
            self.first = self.document.getObject("FirstResult")
            self.reference = self.document.getObject("ReferencePlane")
            self.second = self.document.getObject("SecondResult")

            self.assertTrue(
                _wait_until(
                    lambda: _ordered_object_names(self.timeline)
                    == expected_order
                    and _marker_position(self.timeline) == 3
                ),
                {
                    "order": _ordered_object_names(self.timeline),
                    "position": _marker_position(self.timeline),
                },
            )
            timeline_object = _document_timeline(self.document)
            self.assertIsNotNone(timeline_object)
            self.assertEqual(
                [obj.Name for obj in timeline_object.Operations],
                expected_order,
            )
            self.assertEqual(timeline_object.Position, 3)
            self.assertEqual(timeline_object.SchemaVersion, 2)
            self.assertEqual(
                list(timeline_object.SuppressionAtEnd),
                [False] * len(expected_order),
            )
            self.assertIs(self.body.Tip, self.first)

            end_button = self.timeline_widget.findChild(
                QtGui.QToolButton,
                "VibeCADFeatureTimelineEnd",
            )
            end_button.click()
            self.assertTrue(
                _wait_until(
                    lambda: timeline_object.Position == 5
                    and _marker_position(self.timeline) == 5
                    and self.body.Tip is self.second
                )
            )

            self.document.undo()
            self.assertTrue(
                _wait_until(
                    lambda: timeline_object.Position == 3
                    and _marker_position(self.timeline) == 3
                    and self.body.Tip is self.first
                )
            )

            self.document.redo()
            self.assertTrue(
                _wait_until(
                    lambda: timeline_object.Position == 5
                    and _marker_position(self.timeline) == 5
                    and self.body.Tip is self.second
                )
            )

    def test_selection_never_filters_interleaved_multi_body_timeline(self):
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and _marker_position(self.timeline)
                == len(_document_timeline(self.document).Operations)
            )
        )

        second_body = self.document.addObject(
            "PartDesign::Body",
            "SecondTimelineBody",
        )
        second_body.Label = "Second Timeline Body"
        second_result = self.document.addObject(
            "PartDesign::Feature",
            "SecondBodyResult",
        )
        second_result.Label = "Second Body Result"
        second_result.Shape = Part.makeCylinder(2, 5)
        second_body.addObject(second_result)
        second_body.Tip = second_result
        self.document.recompute()

        first_tail = self.document.addObject(
            "PartDesign::Feature",
            "FirstBodyTail",
        )
        first_tail.Label = "First Body Tail"
        first_tail.Shape = Part.makeBox(6, 6, 6)
        self.body.addObject(first_tail)
        self.body.Tip = first_tail
        self.document.recompute()

        expected = {
            self.body.Name,
            self.sketch.Name,
            self.first.Name,
            self.reference.Name,
            self.second.Name,
            second_body.Name,
            second_result.Name,
            first_tail.Name,
        }
        self.assertTrue(
            _wait_until(
                lambda: expected.issubset(_object_items(self.timeline))
            ),
            _ordered_object_names(self.timeline),
        )

        ordered = _ordered_object_names(self.timeline)
        self.assertLess(
            ordered.index(self.second.Name),
            ordered.index(second_result.Name),
        )
        self.assertLess(
            ordered.index(second_result.Name),
            ordered.index(first_tail.Name),
            "Operations from different Bodies must retain their interleaved "
            "document order",
        )
        self.assertEqual(
            _object_items(self.timeline)[second_result.Name].data(
                OWNER_NAME_ROLE
            ),
            second_body.Name,
        )
        self.assertEqual(
            _object_items(self.timeline)[first_tail.Name].data(
                OWNER_NAME_ROLE
            ),
            self.body.Name,
        )

        # Body activation and model-browser selection may highlight or scroll
        # the matching operation, but they must never replace the document's
        # complete sequence.
        Gui.activeView().setActiveObject("pdbody", self.body)
        original_order = tuple(ordered)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(second_body)
        self.assertTrue(
            _wait_until(
                lambda: tuple(_ordered_object_names(self.timeline))
                == original_order
            ),
            _ordered_object_names(self.timeline),
        )

        Gui.activeView().setActiveObject("pdbody", self.body)
        Gui.Selection.clearSelection()
        self.assertTrue(
            _wait_until(
                lambda: tuple(_ordered_object_names(self.timeline))
                == original_order
            ),
            _ordered_object_names(self.timeline),
        )

        Gui.Selection.addSelection(self.body)
        Gui.Selection.addSelection(second_body)
        self.assertTrue(
            _wait_until(
                lambda: tuple(_ordered_object_names(self.timeline))
                == original_order
            ),
            _ordered_object_names(self.timeline),
        )

    def test_base_feature_boundaries_follow_global_two_body_chronology(self):
        """A Body cannot use its external BaseFeature link as its native Tip."""

        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and _marker_position(self.timeline)
                == len(_document_timeline(self.document).Operations)
            )
        )

        source = self.document.addObject(
            "Part::Feature",
            "TimelineImportedSource",
        )
        source.Label = "Imported Source"
        source.Shape = Part.makeBox(3, 4, 5)

        imported_body = self.document.addObject(
            "PartDesign::Body",
            "TimelineImportedBody",
        )
        imported_body.Label = "Imported Body"
        imported_body.BaseFeature = source
        imported_proxy = next(
            obj
            for obj in imported_body.Group
            if obj.TypeId == "PartDesign::FeatureBase"
        )

        other_body = self.document.addObject(
            "PartDesign::Body",
            "TimelineInterleavedBody",
        )
        other_body.Label = "Interleaved Body"
        other_result = self.document.addObject(
            "PartDesign::AdditiveBox",
            "TimelineInterleavedResult",
        )
        other_result.Length = 2
        other_result.Width = 3
        other_result.Height = 4
        other_body.addObject(other_result)

        imported_tail = self.document.addObject(
            "PartDesign::Feature",
            "TimelineImportedTail",
        )
        imported_tail.Label = "Imported Body Result"
        imported_tail.Shape = Part.makeBox(4, 5, 6)
        imported_body.addObject(imported_tail)
        imported_body.Tip = imported_tail
        other_body.Tip = other_result
        self.document.recompute()

        expected = {
            source.Name,
            imported_body.Name,
            imported_proxy.Name,
            other_body.Name,
            other_result.Name,
            imported_tail.Name,
        }
        self.assertTrue(
            _wait_until(
                lambda: expected.issubset(_object_items(self.timeline))
            ),
            _ordered_object_names(self.timeline),
        )
        operations = _document_timeline(self.document).Operations
        operation_names = [obj.Name for obj in operations]
        self.assertLess(
            operation_names.index(source.Name),
            operation_names.index(imported_body.Name),
        )
        self.assertLess(
            operation_names.index(imported_body.Name),
            operation_names.index(imported_proxy.Name),
        )
        self.assertLess(
            operation_names.index(imported_proxy.Name),
            operation_names.index(other_body.Name),
        )
        self.assertLess(
            operation_names.index(other_body.Name),
            operation_names.index(other_result.Name),
        )
        self.assertLess(
            operation_names.index(other_result.Name),
            operation_names.index(imported_tail.Name),
        )

        def set_current_and_wait(target, imported_tip, other_tip):
            item = _object_items(self.timeline)[target.Name]
            operation_index = item.data(OPERATION_INDEX_ROLE)
            _trigger_timeline_action(
                self.timeline,
                item,
                "VibeCADTimelineSetCurrent",
            )
            self.assertTrue(
                _wait_until(
                    lambda: imported_body.Tip is imported_tip
                    and other_body.Tip is other_tip
                    and _marker_position(self.timeline)
                    == operation_index + 1
                ),
                {
                    "target": target.Name,
                    "imported_tip": (
                        imported_body.Tip.Name
                        if imported_body.Tip is not None
                        else None
                    ),
                    "other_tip": (
                        other_body.Tip.Name
                        if other_body.Tip is not None
                        else None
                    ),
                    "position": _document_timeline(
                        self.document
                    ).Position,
                },
            )

        # At the Body creation boundary, the external source link already
        # exists in the saved document, but it is not a child-scoped Body Tip.
        set_current_and_wait(imported_body, None, None)
        self.assertIs(imported_body.BaseFeature, source)

        # The native internal FeatureBase becomes the Tip only when its own
        # document-wide operation is active.
        set_current_and_wait(imported_proxy, imported_proxy, None)
        set_current_and_wait(
            other_result,
            imported_proxy,
            other_result,
        )
        set_current_and_wait(
            imported_tail,
            imported_tail,
            other_result,
        )

    def test_structural_body_uses_tracked_members_in_document_history(self):
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        end_button.click()
        controller = _document_timeline(self.document)
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
            )
        )

        self.document.openTransaction("Create structural Body history")
        structural_body = self.document.addObject(
            "PartDesign::Body",
            "TimelineStructuralBody",
        )
        self.document.classifyProvisionalTimelineInternalObject(
            structural_body
        )
        first = structural_body.newObject(
            "PartDesign::Feature",
            "TimelineStructuralFirst",
        )
        first.Shape = Part.makeBox(2, 3, 4)
        second = structural_body.newObject(
            "PartDesign::Feature",
            "TimelineStructuralSecond",
        )
        second.Shape = Part.makeBox(4, 5, 6)
        structural_body.Tip = second
        structural_body.Visibility = True
        first.Visibility = False
        second.Visibility = True
        self.document.recompute()
        first_volume = first.Shape.Volume
        second_volume = second.Shape.Volume
        self.document.commitTransaction()

        self.assertNotIn(structural_body, controller.Operations)
        self.assertIn(first, controller.Operations)
        self.assertIn(second, controller.Operations)
        self.assertTrue(
            _wait_until(
                lambda: structural_body.Name
                not in _object_items(self.timeline)
                and first.Name in _object_items(self.timeline)
                and second.Name in _object_items(self.timeline)
            ),
            _ordered_object_names(self.timeline),
        )

        _trigger_timeline_action(
            self.timeline,
            _object_items(self.timeline)[first.Name],
            "VibeCADTimelineSetCurrent",
        )
        first_boundary = list(controller.Operations).index(first) + 1
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == first_boundary
                and structural_body.Tip is first
                and not first.Suppressed
                and not second.Suppressed
                and first.Visibility
                and not second.Visibility
                and abs(first.Shape.Volume - first_volume) < 1.0e-9
                and abs(second.Shape.Volume - second_volume) < 1.0e-9
            ),
            {
                "position": controller.Position,
                "tip": (
                    structural_body.Tip.Name
                    if structural_body.Tip is not None
                    else None
                ),
                "first_suppressed": first.Suppressed,
                "second_suppressed": second.Suppressed,
            },
        )

        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
                and structural_body.Tip is second
                and not first.Suppressed
                and not second.Suppressed
                and not first.Visibility
                and second.Visibility
                and abs(first.Shape.Volume - first_volume) < 1.0e-9
                and abs(second.Shape.Volume - second_volume) < 1.0e-9
            )
        )

        self.document.undo()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == first_boundary
                and structural_body.Tip is first
                and not first.Suppressed
                and not second.Suppressed
            )
        )
        self.document.redo()
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == len(controller.Operations)
                and structural_body.Tip is second
                and not first.Suppressed
                and not second.Suppressed
            )
        )

        _trigger_timeline_action(
            self.timeline,
            _object_items(self.timeline)[first.Name],
            "VibeCADTimelineSetCurrent",
        )
        self.assertTrue(
            _wait_until(
                lambda: controller.Position == first_boundary
                and structural_body.Tip is first
                and not second.Suppressed
            )
        )
        with tempfile.TemporaryDirectory(
            prefix="vibecad_structural_body_timeline_",
        ) as temporary_directory:
            path = os.path.join(temporary_directory, "structural.FCStd")
            structural_body_name = structural_body.Name
            first_name = first.Name
            second_name = second.Name
            self.document.saveAs(path)
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(path)
            self.document.UndoMode = True
            structural_body = self.document.getObject(structural_body_name)
            first = self.document.getObject(first_name)
            second = self.document.getObject(second_name)
            controller = _document_timeline(self.document)
            self.assertTrue(
                _wait_until(
                    lambda: controller.Position == first_boundary
                    and structural_body.Tip is first
                    and not first.Suppressed
                    and not second.Suppressed
                    and abs(first.Shape.Volume - first_volume) < 1.0e-9
                    and abs(second.Shape.Volume - second_volume) < 1.0e-9
                    and structural_body_name
                    not in _object_items(self.timeline)
                    and first_name in _object_items(self.timeline)
                    and second_name in _object_items(self.timeline)
                ),
                {
                    "position": controller.Position,
                    "tip": (
                        structural_body.Tip.Name
                        if structural_body.Tip is not None
                        else None
                    ),
                    "history": _ordered_object_names(self.timeline),
                },
            )

            end_button = self.timeline_widget.findChild(
                QtGui.QToolButton,
                "VibeCADFeatureTimelineEnd",
            )
            end_button.click()
            self.assertTrue(
                _wait_until(
                    lambda: controller.Position
                    == len(controller.Operations)
                    and structural_body.Tip is second
                    and not first.Suppressed
                    and not second.Suppressed
                )
            )

    def test_internal_multitransform_child_is_never_a_result_target(self):
        """An internal transformation is implementation, not a Body result."""

        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and _marker_position(self.timeline)
                == len(_document_timeline(self.document).Operations)
            )
        )

        transform_body = self.document.addObject(
            "PartDesign::Body",
            "TimelineTransformBody",
        )
        original = self.document.addObject(
            "PartDesign::AdditiveBox",
            "TimelineTransformOriginal",
        )
        original.Length = 2
        original.Width = 2
        original.Height = 2
        transform_body.addObject(original)
        self.document.recompute()

        multi = self.document.addObject(
            "PartDesign::MultiTransform",
            "TimelineMultiTransform",
        )
        multi.Originals = [original]
        multi.Shape = original.Shape
        transform_body.addObject(multi)

        internal = self.document.addObject(
            "PartDesign::Scaled",
            "TimelineInternalScaled",
        )
        internal.Factor = 2
        internal.Occurrences = 2
        transform_body.addObject(internal)
        multi.Transformations = [internal]
        self.document.recompute()

        self.assertTrue(multi.isValid(), multi.getStatusString())
        self.assertTrue(internal.isValid(), internal.getStatusString())
        self.assertIn(internal, transform_body.Group)
        self.assertIs(transform_body.Tip, multi)
        self.assertEqual(internal.VibeCADTimelineRole, "resource")
        self.assertIs(internal.VibeCADTimelineOwner, multi)
        self.assertEqual(
            internal.getTypeIdOfProperty("VibeCADTimelineOwner"),
            "App::PropertyLinkHidden",
        )
        self.assertNotIn(
            multi,
            internal.OutList,
            "Timeline ownership must not become a modeling dependency",
        )
        self.assertIn(
            "Hidden",
            internal.getEditorMode("VibeCADTimelineRole"),
        )
        self.assertIn(
            "Hidden",
            internal.getEditorMode("VibeCADTimelineOwner"),
        )
        self.assertTrue(
            _wait_until(
                lambda: multi.Name in _object_items(self.timeline)
                and internal.Name not in _object_items(self.timeline)
            ),
            _ordered_object_names(self.timeline),
        )

        _trigger_timeline_action(
            self.timeline,
            _object_items(self.timeline)[original.Name],
            "VibeCADTimelineSetCurrent",
        )
        self.assertTrue(
            _wait_until(
                lambda: transform_body.Tip is original
                and _marker_position(self.timeline)
                == _object_items(self.timeline)[original.Name].data(
                    OPERATION_INDEX_ROLE
                )
                + 1
            )
        )
        self.assertTrue(internal.Suppressed)

        _trigger_timeline_action(
            self.timeline,
            _object_items(self.timeline)[multi.Name],
            "VibeCADTimelineSetCurrent",
        )
        operations = list(_document_timeline(self.document).Operations)
        multi_boundary = max(
            operations.index(multi),
            operations.index(internal),
        ) + 1
        self.assertTrue(
            _wait_until(
                lambda: transform_body.Tip is multi
                and not internal.Suppressed
                and _marker_position(self.timeline) == multi_boundary
            ),
            {
                "tip": (
                    transform_body.Tip.Name
                    if transform_body.Tip is not None
                    else None
                ),
                "internal_suppressed": internal.Suppressed,
                "position": _marker_position(self.timeline),
                "multi_boundary": multi_boundary,
            },
        )
        self.assertTrue(multi.isValid(), multi.getStatusString())
        self.assertTrue(internal.isValid(), internal.getStatusString())

        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: transform_body.Tip is multi
                and transform_body.Tip is not internal
                and internal.Name not in _object_items(self.timeline)
                and _marker_position(self.timeline)
                == len(_document_timeline(self.document).Operations)
            ),
            {
                "tip": (
                    transform_body.Tip.Name
                    if transform_body.Tip is not None
                    else None
                ),
                "operations": [
                    obj.Name
                    for obj in _document_timeline(
                        self.document
                    ).Operations
                ],
                "visible": _ordered_object_names(self.timeline),
            },
        )

    def test_edit_callback_that_throws_after_entering_edit_is_rolled_back(self):
        """A failed callback closes its editor before aborting its objects."""

        editable = self.document.addObject(
            "PartDesign::FeaturePython",
            "TimelineEnterEditThenThrow",
        )
        editable.Label = "Enter Edit Then Throw"
        editable.Shape = Part.makeCylinder(2, 4)
        edit_proxy = _EnterEditThenThrowViewProxy()
        editable.ViewObject.Proxy = edit_proxy
        self.body.addObject(editable)
        self.document.recompute()
        self.assertTrue(
            _wait_until(
                lambda: editable.Name in _object_items(self.timeline)
            )
        )

        _trigger_timeline_action(
            self.timeline,
            _object_items(self.timeline)[self.first.Name],
            "VibeCADTimelineSetCurrent",
        )
        controller = _document_timeline(self.document)
        accepted_position = controller.Position
        self.assertIs(self.body.Tip, self.first)
        self.assertFalse(editable.Visibility)
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertFalse(self.document.HasPendingTransaction)

        self.timeline.itemDoubleClicked.emit(
            _object_items(self.timeline)[editable.Name]
        )
        self.assertTrue(
            _wait_until(
                lambda: Gui.activeDocument().getInEdit() is None
                and self.document.getBookedTransactionID() == 0
                and not self.document.HasPendingTransaction
                and self.timeline.isEnabled()
            ),
            {
                "edit": Gui.activeDocument().getInEdit(),
                "transaction": self.document.getBookedTransactionID(),
                "pending": self.document.HasPendingTransaction,
            },
        )
        self.assertIs(
            self.document.getObject(editable.Name),
            editable,
            "Rollback must not leave a live editor pointing at a deleted "
            "or replaced object",
        )
        self.assertIs(self.body.Tip, self.first)
        self.assertFalse(editable.Visibility)
        self.assertEqual(controller.Position, accepted_position)
        self.assertEqual(edit_proxy.entered, 1)
        self.assertEqual(edit_proxy.unset, 1)

    def test_close_relabel_and_throwing_callbacks_are_safe_under_history_lock(self):
        """Synchronous callbacks cannot invalidate a locked timeline move."""

        document = self.document
        body = self.body

        class CloseAndRelabelObserver:
            def __init__(self):
                self.attempted = False
                self.close_refused = False
                self.error = None
                self.document_survived = None

            def slotChangedObject(self, obj, property_name):
                if (
                    self.attempted
                    or obj.Document is not document
                    or obj is not body
                    or property_name != "Tip"
                ):
                    return
                self.attempted = True
                try:
                    document.Label = "Relabeled During Locked History Move"
                    App.closeDocument(document.Name)
                except RuntimeError:
                    self.close_refused = True
                except Exception as error:  # pragma: no cover - diagnostic
                    self.error = error
                self.document_survived = (
                    document.Name in App.listDocuments()
                )

        class ThrowingTipObserver:
            def __init__(self):
                self.attempted = False

            def slotChangedObject(self, obj, property_name):
                if (
                    not self.attempted
                    and obj.Document is document
                    and obj is body
                    and property_name == "Tip"
                ):
                    self.attempted = True
                    raise RuntimeError(
                        "Deliberate observer failure during locked history move"
                    )

        close_observer = CloseAndRelabelObserver()
        throwing_observer = ThrowingTipObserver()
        App.addDocumentObserver(close_observer)
        App.addDocumentObserver(throwing_observer)
        try:
            previous_button = self.timeline_widget.findChild(
                QtGui.QToolButton,
                "VibeCADFeatureTimelinePrevious",
            )
            previous_button.click()
            self.assertTrue(
                _wait_until(
                    lambda: self.body.Tip is None
                    and _marker_position(self.timeline) == 2
                )
            )
        finally:
            App.removeDocumentObserver(throwing_observer)
            App.removeDocumentObserver(close_observer)

        self.assertTrue(close_observer.attempted)
        self.assertIsNone(close_observer.error)
        self.assertTrue(close_observer.close_refused)
        self.assertTrue(close_observer.document_survived)
        self.assertTrue(throwing_observer.attempted)
        self.assertIn(self.document.Name, App.listDocuments())
        self.assertEqual(
            self.document.Label,
            "Relabeled During Locked History Move",
        )
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertTrue(
            _wait_until(lambda: self.timeline.isEnabled()),
            "The timeline must unlock and recover after synchronous callbacks",
        )

    def test_lcs_creation_cannot_remove_the_document_timeline(self):
        """Nested datum creation cannot delete or duplicate timeline state."""

        controller = _document_timeline(self.document)
        self.assertIsNotNone(controller)
        controller_id = controller.ID
        baseline_operations = tuple(controller.Operations)

        class RemoveTimelineDuringNestedCreation:
            def __init__(self):
                self.attempted = False
                self.error = None
                self.created_nested = None
                self.before = None
                self.after = None

            def slotCreatedObject(self, obj):
                if (
                    self.attempted
                    or obj.Document is not self_document
                    or obj.TypeId == "App::DocumentTimeline"
                ):
                    return
                self.attempted = True
                self.created_nested = obj
                try:
                    self.before = self_document.getObject(
                        "VibeCADTimeline"
                    )
                    self_document.removeObject("VibeCADTimeline")
                    self.after = self_document.getObject(
                        "VibeCADTimeline"
                    )
                except Exception as error:  # pragma: no cover - diagnostic
                    self.error = error

        self_document = self.document
        observer = RemoveTimelineDuringNestedCreation()
        App.addDocumentObserver(observer)
        try:
            coordinate_system = self.document.addObject(
                "Part::LocalCoordinateSystem",
                "TimelineLocalCoordinateSystem",
            )
            self.body.addObject(coordinate_system)
            self.document.recompute()
        finally:
            App.removeDocumentObserver(observer)

        self.assertTrue(observer.attempted)
        self.assertIsNone(observer.error)
        self.assertIsNotNone(observer.created_nested)
        self.assertIn(
            observer.created_nested,
            coordinate_system.OriginFeatures,
            "The removal attempt must occur from a nested LCS infrastructure "
            "creation callback, not after the outer operation is complete",
        )
        self.assertIs(observer.before, controller)
        self.assertIs(observer.after, controller)
        self.assertIs(_document_timeline(self.document), controller)
        self.assertEqual(controller.ID, controller_id)

        self.assertIn(coordinate_system, self.body.Group)
        self.assertEqual(len(coordinate_system.OriginFeatures), 7)
        self.assertTrue(
            all(
                feature.Document is self.document
                for feature in coordinate_system.OriginFeatures
            )
        )
        self.assertTrue(coordinate_system.isValid())

        operations = tuple(controller.Operations)
        added_operations = [
            operation
            for operation in operations
            if operation not in baseline_operations
        ]
        self.assertEqual(
            added_operations,
            [coordinate_system],
            "Only the user-created coordinate system belongs in chronology; "
            "its controlled axes, planes, and point are infrastructure",
        )
        self.assertTrue(
            all(
                feature not in operations
                for feature in coordinate_system.OriginFeatures
            )
        )
        self.assertTrue(
            _wait_until(
                lambda: coordinate_system.Name
                in _object_items(self.timeline)
            )
        )

        # Deliberate removal outside the critical update remains supported for
        # migration/recovery code; only reentrant removal is refused.
        self.document.removeObject(controller.Name)
        self.assertIsNone(_document_timeline(self.document))
        self.assertIsNone(self.document.getObject("VibeCADTimeline"))

    def test_replacement_controller_callback_can_remove_outer_creation_safely(self):
        """First-controller callbacks cannot leave a deleted outer object live."""

        document_name = "FeatureTimelineControllerReplacement"
        document = App.newDocument(document_name)
        observer = None
        observer_registered = False
        try:
            seed = document.addObject(
                "Part::Feature",
                "ControllerReplacementSeed",
            )
            seed.Shape = Part.makeBox(1, 1, 1)
            initial_controller = _document_timeline(document)
            self.assertIsNotNone(initial_controller)

            # Recovery tools may deliberately remove a controller outside its
            # critical update. The next real operation must create a new one.
            document.removeObject(initial_controller.Name)
            self.assertIsNone(_document_timeline(document))

            outer_name = "OuterObjectRemovedDuringControllerCreation"

            class RemoveOuterWhenReplacementControllerIsCreated:
                def __init__(self):
                    self.signaled = False
                    self.replacement = None
                    self.error = None

                def slotCreatedObject(self, obj):
                    if (
                        self.signaled
                        or obj.Document is not document
                        or obj.TypeId != "App::DocumentTimeline"
                    ):
                        return
                    self.signaled = True
                    self.replacement = obj
                    try:
                        document.removeObject(outer_name)
                    except Exception as error:  # pragma: no cover - diagnostic
                        self.error = error

            observer = RemoveOuterWhenReplacementControllerIsCreated()
            App.addDocumentObserver(observer)
            observer_registered = True
            with self.assertRaisesRegex(
                RuntimeError,
                "removed while creating its timeline",
            ):
                document.addObject("Part::Feature", outer_name)
            App.removeDocumentObserver(observer)
            observer_registered = False

            self.assertTrue(observer.signaled)
            self.assertIsNone(observer.error)
            self.assertIsNone(document.getObject(outer_name))
            self.assertIs(
                _document_timeline(document),
                observer.replacement,
            )
            self.assertEqual(tuple(observer.replacement.Operations), ())

            App.closeDocument(document_name)
            self.assertNotIn(document_name, App.listDocuments())
            document = None
            App.setActiveDocument(self.document.Name)
            self.assertTrue(
                _wait_until(
                    lambda: self.first.Name
                    in _object_items(self.timeline)
                )
            )
        finally:
            if observer is not None and observer_registered:
                App.removeDocumentObserver(observer)
            if document_name in App.listDocuments():
                App.closeDocument(document_name)
            if self.document.Name in App.listDocuments():
                App.setActiveDocument(self.document.Name)

    def test_active_native_task_cannot_move_or_edit_history(self):
        Gui.activateWorkbench("PartDesignWorkbench")
        Gui.activeView().setActiveObject("pdbody", self.body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(
            self.document.Name,
            self.first.Name,
            "Edge1",
        )

        original_tip = self.body.Tip
        original_group = tuple(self.body.Group)
        controller = _document_timeline(self.document)
        original_operations = tuple(controller.Operations)
        original_position = controller.Position
        Gui.runCommand("PartDesign_Chamfer", 0)
        self.assertTrue(Gui.Control.activeDialog())
        temporary = self.document.ActiveObject
        self.assertIsNotNone(temporary)
        self.assertTrue(temporary.isDerivedFrom("PartDesign::Chamfer"))
        temporary_name = temporary.Name
        task_tip = self.body.Tip
        task_group = tuple(self.body.Group)

        self.assertTrue(
            _wait_until(
                lambda: not self.timeline.isEnabled()
                and temporary_name not in _object_items(self.timeline)
            )
        )
        self.assertEqual(
            [
                operation.Name
                for operation in original_operations
                if operation.Name in _object_items(self.timeline)
            ],
            [
                name
                for name in _ordered_object_names(self.timeline)
                if name in {operation.Name for operation in original_operations}
            ],
            "A task's provisional preview must not become committed document "
            "History before the task is accepted",
        )

        recompute_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineRecompute",
        )
        self.assertIsNotNone(recompute_button)
        self.assertFalse(recompute_button.isEnabled())

        # Exercise the history-navigation slots directly as well as their
        # disabled controls. Guarding only the widget state would still leave
        # queued signals able to commit the native task's pending transaction.
        for object_name in (
            "VibeCADFeatureTimelinePrevious",
            "VibeCADFeatureTimelineNext",
            "VibeCADFeatureTimelineEnd",
        ):
            button = self.timeline_widget.findChild(
                QtGui.QToolButton,
                object_name,
            )
            button.clicked.emit()
        self.timeline.itemDoubleClicked.emit(
            next(
                item
                for item in _timeline_items(self.timeline)
                if item.data(IS_MARKER_ROLE)
            )
        )
        self.timeline.customContextMenuRequested.emit(QtCore.QPoint(1, 1))
        _event_step(50)

        self.assertIs(self.body.Tip, task_tip)
        self.assertEqual(tuple(self.body.Group), task_group)
        self.assertTrue(Gui.Control.activeDialog())

        Gui.Control.activeTaskDialog().reject()
        self.assertTrue(
            _wait_until(lambda: not Gui.Control.activeDialog())
        )
        self.assertIs(self.body.Tip, original_tip)
        self.assertEqual(tuple(self.body.Group), original_group)
        self.assertIsNone(self.document.getObject(temporary_name))
        self.assertEqual(tuple(controller.Operations), original_operations)
        self.assertEqual(controller.Position, original_position)

    def test_history_move_without_undo_accepts_valid_result_and_restores_failure(self):
        self.document.UndoMode = False
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton, "VibeCADFeatureTimelineEnd"
        )

        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and not self.first.Visibility
                and self.second.Visibility
            ),
            "Disabling undo recording must not disable feature-history modeling",
        )

        failing = self.document.addObject(
            "PartDesign::FeaturePython",
            "RejectedTimelineResult",
        )
        failing.Label = "Rejected Timeline Result"
        proxy = _TimelineExecutionProxy()
        failing.Proxy = proxy
        self.body.addObject(failing)
        self.document.recompute()
        self.assertTrue(
            _wait_until(
                lambda: failing.Name in _object_items(self.timeline)
            )
        )
        _trigger_timeline_action(
            self.timeline,
            _object_items(self.timeline)[self.second.Name],
            "VibeCADTimelineSetCurrent",
        )
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and not self.first.Visibility
                and self.second.Visibility
                and not failing.Visibility
            )
        )

        proxy.fail = True
        failing.touch()
        visibility_before = {
            obj.Name: obj.Visibility
            for obj in self.body.Group
        }
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and all(
                    self.document.getObject(name).Visibility == visible
                    for name, visible in visibility_before.items()
                )
            ),
            "A rejected no-undo history move must restore the exact prior "
            "Tip and visibility state; Tip={!r}, expected visibility={!r}, "
            "actual visibility={!r}".format(
                self.body.Tip.Name if self.body.Tip is not None else None,
                visibility_before,
                {
                    name: self.document.getObject(name).Visibility
                    for name in visibility_before
                },
            ),
        )

    def test_timeline_marks_recompute_and_failed_operations(self):
        end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        end_button.click()
        self.assertTrue(
            _wait_until(
                lambda: self.body.Tip is self.second
                and _marker_position(self.timeline)
                == len(_document_timeline(self.document).Operations)
            )
        )
        feature = self.document.addObject(
            "PartDesign::FeaturePython",
            "TimelineStatusFeature",
        )
        feature.Label = "Status Feature"
        proxy = _TimelineExecutionProxy()
        feature.Proxy = proxy
        self.body.addObject(feature)
        self.body.Tip = feature
        self.document.recompute()

        normal_item = _wait_until(
            lambda: _object_items(self.timeline).get(feature.Name)
        )
        self.assertIsNotNone(normal_item)
        normal_icon = normal_item.icon().pixmap(22, 22).toImage()

        feature.touch()
        touched_item = _wait_until(
            lambda: (
                item
                if (
                    (item := _object_items(self.timeline).get(feature.Name))
                    and "Needs recompute" in item.toolTip()
                )
                else None
            )
        )
        self.assertIsNotNone(touched_item)
        touched_icon = touched_item.icon().pixmap(22, 22).toImage()
        self.assertNotEqual(touched_icon, normal_icon)

        proxy.fail = True
        try:
            self.document.recompute()
        except RuntimeError:
            # Recompute normally records a feature error instead of propagating,
            # but either path must still publish the failed state to the GUI.
            pass
        failed_item = _wait_until(
            lambda: (
                item
                if (
                    (item := _object_items(self.timeline).get(feature.Name))
                    and "Error:" in item.toolTip()
                )
                else None
            )
        )
        self.assertIsNotNone(failed_item)
        self.assertIn("error", failed_item.data(QtCore.Qt.AccessibleTextRole).lower())
        self.assertNotEqual(
            failed_item.icon().pixmap(22, 22).toImage(),
            normal_icon,
        )
