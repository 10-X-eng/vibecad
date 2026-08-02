# ***************************************************************************
# *                                                                         *
# *   VibeCAD Drawing-ribbon behavior contracts.                            *
# *                                                                         *
# ***************************************************************************

from __future__ import annotations

import os
import pathlib
import re
import tempfile
import unittest

try:
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide import QtCore, QtGui
except ImportError:
    App = None
    Gui = None
    QtCore = None
    QtGui = None


OBJECT_NAME_ROLE = int(QtCore.Qt.UserRole) if QtCore is not None else 256


def _wait_until(predicate, timeout_ms=10000):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < timeout_ms:
        Gui.updateGui()
        try:
            result = predicate()
        except RuntimeError:
            result = None
        if result:
            return result
    return None


def _visible_history_names(timeline):
    return {
        timeline.item(row).data(OBJECT_NAME_ROLE)
        for row in range(timeline.count())
        if timeline.item(row).data(OBJECT_NAME_ROLE)
    }


def _history_item(timeline, object_name):
    for row in range(timeline.count()):
        item = timeline.item(row)
        if item.data(OBJECT_NAME_ROLE) == object_name:
            return item
    return None


def _timeline_context_action_names(timeline, item):
    state = {}

    def inspect_menu():
        popup = QtGui.QApplication.activePopupWidget()
        if popup is None:
            state["error"] = "No active timeline context menu"
            return
        try:
            state["actions"] = {
                action.objectName() for action in popup.actions() if action.objectName()
            }
        finally:
            popup.close()

    timeline.scrollToItem(item)
    Gui.updateGui()
    QtCore.QTimer.singleShot(30, inspect_menu)
    timeline.customContextMenuRequested.emit(timeline.visualItemRect(item).center())
    if "error" in state:
        raise AssertionError(state["error"])
    return state.get("actions", set())


def _run_command_without_modal_warning(command):
    """Run a GUI command and turn any modal warning into a prompt test failure."""
    state = {}
    watcher = QtCore.QTimer()
    watcher.setInterval(10)

    def reject_warning():
        dialog = QtGui.QApplication.activeModalWidget()
        if not isinstance(dialog, QtGui.QMessageBox):
            return
        state["warning"] = " — ".join(
            part
            for part in (
                dialog.windowTitle(),
                dialog.text(),
                dialog.informativeText(),
            )
            if part
        )
        dialog.reject()

    watcher.timeout.connect(reject_warning)
    watcher.start()
    try:
        Gui.runCommand(command)
        Gui.updateGui()
    finally:
        watcher.stop()
        reject_warning()
    if state.get("warning"):
        raise AssertionError(f"{command} opened an unexpected warning: " f"{state['warning']}")


SHIPPED_DRAWING_COMMANDS = {
    # Pages
    "TechDraw_PageDefault",
    "TechDraw_PageTemplate",
    "TechDraw_FillTemplateFields",
    "TechDraw_RedrawPage",
    "TechDraw_PrintAll",
    # Views
    "TechDraw_View",
    "TechDraw_BrokenView",
    "TechDraw_ActiveView",
    "TechDraw_SectionGroup",
    "TechDraw_DetailView",
    "TechDraw_DraftView",
    "TechDraw_ClipGroup",
    # Arrange
    "TechDraw_StackGroup",
    # Dimensions (both supported preference layouts)
    "TechDraw_Dimension",
    "TechDraw_CompDimensionTools",
    "TechDraw_LengthDimension",
    "TechDraw_HorizontalDimension",
    "TechDraw_VerticalDimension",
    "TechDraw_RadiusDimension",
    "TechDraw_DiameterDimension",
    "TechDraw_AngleDimension",
    "TechDraw_3PtAngleDimension",
    "TechDraw_AreaDimension",
    "TechDraw_ExtentGroup",
    "TechDraw_Balloon",
    "TechDraw_AxoLengthDimension",
    "TechDraw_DimensionRepair",
    # Attributes
    "TechDraw_ExtensionSelectLineAttributes",
    "TechDraw_ExtensionChangeLineAttributes",
    "TechDraw_ExtensionExtendShortenLineGroup",
    "TechDraw_ExtensionLockUnlockView",
    "TechDraw_ExtensionPositionSectionView",
    "TechDraw_ExtensionAreaAnnotation",
    "TechDraw_ExtensionArcLengthAnnotation",
    "TechDraw_ExtensionCustomizeFormat",
    # Centers
    "TechDraw_ExtensionCircleCenterLinesGroup",
    "TechDraw_ExtensionThreadsGroup",
    "TechDraw_CommandVertexCreationGroup",
    "TechDraw_ExtensionDrawCirclesGroup",
    "TechDraw_ExtensionLinePPGroup",
    # Extended dimensions
    "TechDraw_ExtensionCreateChainDimensionGroup",
    "TechDraw_ExtensionCreateCoordDimensionGroup",
    "TechDraw_ExtensionChamferDimensionGroup",
    "TechDraw_ExtensionCreateLengthArc",
    "TechDraw_ExtensionInsertPrefixGroup",
    "TechDraw_ExtensionIncreaseDecreaseGroup",
    # Files
    "TechDraw_ExportPageSVG",
    "TechDraw_ExportPageDXF",
    # Decoration
    "TechDraw_ToggleFrame",
    "TechDraw_Hatch",
    "TechDraw_GeometricHatch",
    # Annotation
    "TechDraw_RichTextAnnotation",
    "TechDraw_LeaderLine",
    "TechDraw_CosmeticVertexGroup",
    "TechDraw_CenterLineGroup",
    "TechDraw_2PointCosmeticLine",
    "TechDraw_DecorateLine",
    "TechDraw_ShowAll",
    "TechDraw_WeldSymbol",
    "TechDraw_SurfaceFinishSymbols",
    "TechDraw_HoleShaftFit",
}

SHARED_INSPECTION_COMMANDS = {
    "Std_Measure",
    "Std_MassProperties",
    "Inspection_VisualInspection",
    "Inspection_InspectElement",
    "Part_CheckGeometry",
}

SHIPPED_DRAWING_GROUP_CHILDREN = {
    "TechDraw_SectionGroup": {
        "TechDraw_SectionView",
        "TechDraw_ComplexSection",
    },
    "TechDraw_StackGroup": {
        "TechDraw_StackTop",
        "TechDraw_StackBottom",
        "TechDraw_StackUp",
        "TechDraw_StackDown",
    },
    "TechDraw_CompDimensionTools": {
        "TechDraw_Dimension",
        "TechDraw_LengthDimension",
        "TechDraw_HorizontalDimension",
        "TechDraw_VerticalDimension",
        "TechDraw_RadiusDimension",
        "TechDraw_DiameterDimension",
        "TechDraw_AngleDimension",
        "TechDraw_3PtAngleDimension",
        "TechDraw_AreaDimension",
        "TechDraw_ExtensionCreateLengthArc",
        "TechDraw_HorizontalExtentDimension",
        "TechDraw_VerticalExtentDimension",
        "TechDraw_ExtensionCreateHorizChainDimension",
        "TechDraw_ExtensionCreateVertChainDimension",
        "TechDraw_ExtensionCreateObliqueChainDimension",
        "TechDraw_ExtensionCreateHorizCoordDimension",
        "TechDraw_ExtensionCreateVertCoordDimension",
        "TechDraw_ExtensionCreateObliqueCoordDimension",
        "TechDraw_ExtensionCreateHorizChamferDimension",
        "TechDraw_ExtensionCreateVertChamferDimension",
    },
    "TechDraw_ExtentGroup": {
        "TechDraw_HorizontalExtentDimension",
        "TechDraw_VerticalExtentDimension",
    },
    "TechDraw_ExtensionExtendShortenLineGroup": {
        "TechDraw_ExtensionExtendLine",
        "TechDraw_ExtensionShortenLine",
    },
    "TechDraw_ExtensionCircleCenterLinesGroup": {
        "TechDraw_ExtensionCircleCenterLines",
        "TechDraw_ExtensionHoleCircle",
    },
    "TechDraw_CommandVertexCreationGroup": {
        "TechDraw_ExtensionVertexAtIntersection",
        "TechDraw_CommandAddOffsetVertex",
    },
    "TechDraw_ExtensionDrawCirclesGroup": {
        "TechDraw_CosmeticCircle",
        "TechDraw_ExtensionDrawCosmCircle",
        "TechDraw_ExtensionDrawCosmCircle3Points",
        "TechDraw_ExtensionDrawCosmArc",
    },
    "TechDraw_ExtensionLinePPGroup": {
        "TechDraw_ExtensionLineParallel",
        "TechDraw_ExtensionLinePerpendicular",
    },
    "TechDraw_ExtensionThreadsGroup": {
        "TechDraw_ExtensionThreadHoleSide",
        "TechDraw_ExtensionThreadHoleBottom",
        "TechDraw_ExtensionThreadBoltSide",
        "TechDraw_ExtensionThreadBoltBottom",
    },
    "TechDraw_ExtensionCreateChainDimensionGroup": {
        "TechDraw_ExtensionCreateHorizChainDimension",
        "TechDraw_ExtensionCreateVertChainDimension",
        "TechDraw_ExtensionCreateObliqueChainDimension",
    },
    "TechDraw_ExtensionCreateCoordDimensionGroup": {
        "TechDraw_ExtensionCreateHorizCoordDimension",
        "TechDraw_ExtensionCreateVertCoordDimension",
        "TechDraw_ExtensionCreateObliqueCoordDimension",
    },
    "TechDraw_ExtensionChamferDimensionGroup": {
        "TechDraw_ExtensionCreateHorizChamferDimension",
        "TechDraw_ExtensionCreateVertChamferDimension",
    },
    "TechDraw_ExtensionInsertPrefixGroup": {
        "TechDraw_ExtensionInsertDiameter",
        "TechDraw_ExtensionInsertSquare",
        "TechDraw_ExtensionInsertRepetition",
        "TechDraw_ExtensionRemovePrefixChar",
    },
    "TechDraw_ExtensionIncreaseDecreaseGroup": {
        "TechDraw_ExtensionIncreaseDecimal",
        "TechDraw_ExtensionDecreaseDecimal",
    },
    "TechDraw_CosmeticVertexGroup": {
        "TechDraw_CosmeticVertex",
        "TechDraw_Midpoints",
        "TechDraw_Quadrants",
    },
    "TechDraw_CenterLineGroup": {
        "TechDraw_FaceCenterLine",
        "TechDraw_2LineCenterLine",
        "TechDraw_2PointCenterLine",
    },
}

SHIPPED_DRAWING_CHILDREN = set().union(*SHIPPED_DRAWING_GROUP_CHILDREN.values())
SHIPPED_DRAWING_ACTIONS = (
    SHIPPED_DRAWING_COMMANDS | SHIPPED_DRAWING_CHILDREN | SHARED_INSPECTION_COMMANDS
)

DYNAMIC_CONTEXT_ACTION_POSITIONS = (
    "TechDrawContextEditBalloon",
    "TechDrawContextEditDimension",
    "TechDrawContextShowDrawing",
    "TechDrawContextToggleKeepUpdated",
    "TechDrawContextToggleKeepUpdated",
    "TechDrawContextToggleFrames",
    "TechDrawContextToggleGrid",
    "TechDrawContextExportSVG",
    "TechDrawContextExportDXF",
    "TechDrawContextExportPDF",
    "TechDrawContextPrintAll",
    "InspectionContextAnnotation",
    "InspectionContextLeaveInfoMode",
)

CONTEXT_IN_PLACE_EDIT_ACTIONS = {
    "TechDrawContextEditBalloon",
    "TechDrawContextEditDimension",
    "TechDrawContextToggleKeepUpdated",
}

CONTEXT_PRESENTATION_ACTIONS = {
    "TechDrawContextShowDrawing",
    "TechDrawContextToggleFrames",
    "TechDrawContextToggleGrid",
    "InspectionContextAnnotation",
    "InspectionContextLeaveInfoMode",
}

CONTEXT_READ_ONLY_OUTPUT_ACTIONS = {
    "TechDrawContextExportSVG",
    "TechDrawContextExportDXF",
    "TechDrawContextExportPDF",
    "TechDrawContextPrintAll",
}

# Every actual command ID exposed by the Drawing ribbon or its shared Inspect
# group has one semantic document-history contract. Group wrappers use the
# same contract as the children they dispatch; they are not fake operations.
STANDALONE_OPERATION_COMMANDS = {
    "TechDraw_PageDefault",
    "TechDraw_PageTemplate",
    "TechDraw_ActiveView",
    "TechDraw_ClipGroup",
    "TechDraw_RichTextAnnotation",
}

SOURCE_PRESERVING_OPERATION_COMMANDS = {
    "Std_Measure",
    "Std_MassProperties",
    "TechDraw_View",
    "TechDraw_BrokenView",
    "TechDraw_SectionGroup",
    "TechDraw_SectionView",
    "TechDraw_ComplexSection",
    "TechDraw_DetailView",
    "TechDraw_DraftView",
    "TechDraw_Dimension",
    "TechDraw_CompDimensionTools",
    "TechDraw_LengthDimension",
    "TechDraw_HorizontalDimension",
    "TechDraw_VerticalDimension",
    "TechDraw_RadiusDimension",
    "TechDraw_DiameterDimension",
    "TechDraw_AngleDimension",
    "TechDraw_3PtAngleDimension",
    "TechDraw_AreaDimension",
    "TechDraw_ExtentGroup",
    "TechDraw_HorizontalExtentDimension",
    "TechDraw_VerticalExtentDimension",
    "TechDraw_Balloon",
    "TechDraw_AxoLengthDimension",
    "TechDraw_ExtensionAreaAnnotation",
    "TechDraw_ExtensionArcLengthAnnotation",
    "TechDraw_ExtensionCreateChainDimensionGroup",
    "TechDraw_ExtensionCreateHorizChainDimension",
    "TechDraw_ExtensionCreateVertChainDimension",
    "TechDraw_ExtensionCreateObliqueChainDimension",
    "TechDraw_ExtensionCreateCoordDimensionGroup",
    "TechDraw_ExtensionCreateHorizCoordDimension",
    "TechDraw_ExtensionCreateVertCoordDimension",
    "TechDraw_ExtensionCreateObliqueCoordDimension",
    "TechDraw_ExtensionChamferDimensionGroup",
    "TechDraw_ExtensionCreateHorizChamferDimension",
    "TechDraw_ExtensionCreateVertChamferDimension",
    "TechDraw_ExtensionCreateLengthArc",
    "TechDraw_Hatch",
    "TechDraw_GeometricHatch",
    "TechDraw_LeaderLine",
    "TechDraw_WeldSymbol",
    "TechDraw_SurfaceFinishSymbols",
}

REPLACEMENT_OPERATION_COMMANDS = {
    "Inspection_VisualInspection",
}

IN_PLACE_EDIT_COMMANDS = {
    "TechDraw_FillTemplateFields",
    "TechDraw_DimensionRepair",
    "TechDraw_StackGroup",
    "TechDraw_StackTop",
    "TechDraw_StackBottom",
    "TechDraw_StackUp",
    "TechDraw_StackDown",
    "TechDraw_ExtensionChangeLineAttributes",
    "TechDraw_ExtensionExtendShortenLineGroup",
    "TechDraw_ExtensionExtendLine",
    "TechDraw_ExtensionShortenLine",
    "TechDraw_ExtensionLockUnlockView",
    "TechDraw_ExtensionPositionSectionView",
    "TechDraw_ExtensionCustomizeFormat",
    "TechDraw_ExtensionCircleCenterLinesGroup",
    "TechDraw_ExtensionCircleCenterLines",
    "TechDraw_ExtensionHoleCircle",
    "TechDraw_CommandVertexCreationGroup",
    "TechDraw_ExtensionVertexAtIntersection",
    "TechDraw_CommandAddOffsetVertex",
    "TechDraw_ExtensionDrawCirclesGroup",
    "TechDraw_CosmeticCircle",
    "TechDraw_ExtensionDrawCosmCircle",
    "TechDraw_ExtensionDrawCosmCircle3Points",
    "TechDraw_ExtensionDrawCosmArc",
    "TechDraw_ExtensionLinePPGroup",
    "TechDraw_ExtensionLineParallel",
    "TechDraw_ExtensionLinePerpendicular",
    "TechDraw_ExtensionThreadsGroup",
    "TechDraw_ExtensionThreadHoleSide",
    "TechDraw_ExtensionThreadHoleBottom",
    "TechDraw_ExtensionThreadBoltSide",
    "TechDraw_ExtensionThreadBoltBottom",
    "TechDraw_ExtensionInsertPrefixGroup",
    "TechDraw_ExtensionInsertDiameter",
    "TechDraw_ExtensionInsertSquare",
    "TechDraw_ExtensionInsertRepetition",
    "TechDraw_ExtensionRemovePrefixChar",
    "TechDraw_ExtensionIncreaseDecreaseGroup",
    "TechDraw_ExtensionIncreaseDecimal",
    "TechDraw_ExtensionDecreaseDecimal",
    "TechDraw_2PointCosmeticLine",
    "TechDraw_CosmeticVertexGroup",
    "TechDraw_CosmeticVertex",
    "TechDraw_Midpoints",
    "TechDraw_Quadrants",
    "TechDraw_CenterLineGroup",
    "TechDraw_FaceCenterLine",
    "TechDraw_2LineCenterLine",
    "TechDraw_2PointCenterLine",
    "TechDraw_DecorateLine",
    "TechDraw_ShowAll",
    "TechDraw_HoleShaftFit",
}

READ_ONLY_COMMANDS = {
    "TechDraw_RedrawPage",
    "TechDraw_PrintAll",
    "TechDraw_ExportPageSVG",
    "TechDraw_ExportPageDXF",
    "TechDraw_ToggleFrame",
    "TechDraw_ExtensionSelectLineAttributes",
    "Inspection_InspectElement",
    "Part_CheckGeometry",
}


def _find_techdraw_source() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve()
    candidates = [here.parent.parent]
    candidates.extend(parent / "src/Mod/TechDraw" for parent in here.parents)
    candidates.extend(
        pathlib.Path.cwd() / suffix for suffix in ("src/Mod/TechDraw", "Mod/TechDraw")
    )
    for candidate in candidates:
        if (candidate / "Gui/Workbench.cpp").is_file():
            return candidate
    raise RuntimeError("TechDraw source tree is unavailable")


def _without_line_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def _function_body(text: str, signature: str) -> str:
    start = text.index(signature)
    opening = text.index("{", start)
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1 : index]
    raise AssertionError(f"unterminated function: {signature}")


class TechDrawGuiBehaviorSourceContractTest(unittest.TestCase):
    """Product contracts for the VibeCAD Drawing ribbon, independent of legacy tests."""

    @classmethod
    def setUpClass(cls):
        cls.techdraw = _find_techdraw_source()
        cls.gui = cls.techdraw / "Gui"

    def test_shared_task_identity_rejects_reused_documents_and_objects(self):
        guard = (self.gui / "TaskDocumentGuard.h").read_text(encoding="utf-8")
        compact = "".join(guard.split())

        self.assertIn(
            'uid(document?document->Uid.getValueStr():"")',
            compact,
        )
        self.assertIn(
            "current==document" "&&current->Uid.getValueStr()==uid",
            compact,
        )
        self.assertIn("address(object)", compact)
        self.assertIn("object!=address", compact)
        self.assertIn(
            "!currentDocument->containsObject(object)",
            compact,
        )
        self.assertIn(
            "currentDocument->getObject(objectName.c_str())!=object",
            compact,
        )

    def test_exact_drawing_toolbar_inventory(self):
        workbench = (self.gui / "Workbench.cpp").read_text(encoding="utf-8")
        toolbar = workbench[
            workbench.index("Gui::ToolBarItem* Workbench::setupToolBars()") : workbench.index(
                "Gui::ToolBarItem* Workbench::setupCommandBars()"
            )
        ]
        actual = set(
            re.findall(
                r'"(TechDraw_[A-Za-z0-9_]+)"',
                _without_line_comments(toolbar),
            )
        )
        self.assertEqual(actual, SHIPPED_DRAWING_COMMANDS)

        ribbon = (self.techdraw.parents[1] / "Gui/VibeCADRibbon.cpp").read_text(encoding="utf-8")
        shared_inspection = _function_body(
            ribbon,
            "const std::vector<QString>& " "sharedInspectionCommands()",
        )
        actual_inspection = set(
            re.findall(
                r'QStringLiteral\("(.*?)"\)',
                shared_inspection,
            )
        )
        self.assertEqual(
            actual_inspection,
            SHARED_INSPECTION_COMMANDS,
        )
        self.assertIn(
            "resolveUniqueEntries(sharedInspectionCommands())",
            ribbon,
        )

    def test_command_child_graph_and_history_contract_are_exhaustive(self):
        command_sources = {
            path: path.read_text(encoding="utf-8") for path in self.gui.glob("Command*.cpp")
        }
        actual_children = {}
        for parent in SHIPPED_DRAWING_GROUP_CHILDREN:
            if parent == "TechDraw_CompDimensionTools":
                source = command_sources[self.gui / "CommandCreateDims.cpp"]
                body = source[
                    source.index("class CmdTechDrawCompDimensionTools") : source.index(
                        "//===========================================================================\n"
                        "// TechDraw_ExtentGroup"
                    )
                ]
                children = set(
                    re.findall(
                        r'addCommand\("(TechDraw_[A-Za-z0-9_]+)"\)',
                        body,
                    )
                )
            elif parent == "TechDraw_CommandVertexCreationGroup":
                source = (self.techdraw / "TechDrawTools/CommandVertexCreations.py").read_text(
                    encoding="utf-8"
                )
                body = re.search(
                    r"^\s+def GetCommands\(self\):" r"(?P<body>.*?)(?=^\s+def |\Z)",
                    source,
                    re.MULTILINE | re.DOTALL,
                ).group("body")
                children = set(
                    re.findall(
                        r'[\'"](TechDraw_[A-Za-z0-9_]+)[\'"]',
                        body,
                    )
                )
            else:
                command_class = None
                source = None
                constructor = re.compile(
                    r"(?P<class>Cmd[A-Za-z0-9_]+)::(?P=class)"
                    r"\([^)]*\)(?P<initializer>.{0,400}?)"
                    r'Command\("' + re.escape(parent) + r'"\)',
                    re.DOTALL,
                )
                for candidate in command_sources.values():
                    match = constructor.search(candidate)
                    if match:
                        command_class = match.group("class")
                        source = candidate
                        break
                self.assertIsNotNone(command_class, parent)
                body = _function_body(
                    source,
                    f"{command_class}::createAction",
                )
                children = set(
                    re.findall(
                        r"setObjectName\(QStringLiteral" r'\("(TechDraw_[A-Za-z0-9_]+)"\)\)',
                        body,
                    )
                )
            actual_children[parent] = children

        self.assertEqual(
            actual_children,
            SHIPPED_DRAWING_GROUP_CHILDREN,
        )
        self.assertEqual(len(SHIPPED_DRAWING_COMMANDS), 61)
        self.assertEqual(
            sum(map(len, SHIPPED_DRAWING_GROUP_CHILDREN.values())),
            64,
        )
        self.assertEqual(len(SHIPPED_DRAWING_CHILDREN), 54)
        self.assertEqual(len(SHIPPED_DRAWING_ACTIONS), 110)

        contracts = (
            STANDALONE_OPERATION_COMMANDS,
            SOURCE_PRESERVING_OPERATION_COMMANDS,
            REPLACEMENT_OPERATION_COMMANDS,
            IN_PLACE_EDIT_COMMANDS,
            READ_ONLY_COMMANDS,
        )
        for index, contract in enumerate(contracts):
            for other in contracts[index + 1 :]:
                self.assertTrue(contract.isdisjoint(other))
        self.assertEqual(set().union(*contracts), SHIPPED_DRAWING_ACTIONS)
        self.assertEqual(
            tuple(map(len, contracts)),
            (5, 43, 1, 53, 8),
        )

    def test_dynamic_context_actions_have_an_exhaustive_history_contract(self):
        balloon = (self.gui / "ViewProviderBalloon.cpp").read_text(encoding="utf-8")
        dimension = (self.gui / "ViewProviderDimension.cpp").read_text(encoding="utf-8")
        page_provider = (self.gui / "ViewProviderPage.cpp").read_text(encoding="utf-8")
        page_view = (self.gui / "MDIViewPage.cpp").read_text(encoding="utf-8")
        inspection = (self.techdraw.parent / "Inspection/Gui/ViewProviderInspection.cpp").read_text(
            encoding="utf-8"
        )

        provider_context_sources = {}
        for path in self.gui.glob("ViewProvider*.cpp"):
            source = path.read_text(encoding="utf-8")
            if re.search(
                r"\bvoid\s+[A-Za-z0-9_:]+::setupContextMenu\s*\(",
                _without_line_comments(source),
            ):
                provider_context_sources[path.name] = source
        self.assertEqual(
            set(provider_context_sources),
            {
                "ViewProviderBalloon.cpp",
                "ViewProviderDimension.cpp",
                "ViewProviderPage.cpp",
                "ViewProviderProjGroupItem.cpp",
            },
        )
        provider_context_bodies = tuple(
            _function_body(source, "::setupContextMenu")
            for source in provider_context_sources.values()
        )
        self.assertEqual(
            sum(
                len(
                    re.findall(
                        r"\bmenu->addAction\s*\(",
                        _without_line_comments(body),
                    )
                )
                for body in provider_context_bodies
            ),
            4,
        )

        context_event_sources = {
            path.name: path.read_text(encoding="utf-8")
            for path in self.gui.glob("*.cpp")
            if re.search(
                r"\bvoid\s+[A-Za-z0-9_:]+::contextMenuEvent\s*\(",
                _without_line_comments(path.read_text(encoding="utf-8")),
            )
        }
        self.assertEqual(
            set(context_event_sources),
            {"MDIViewPage.cpp", "QGVPage.cpp"},
        )
        qgv_context = _function_body(
            context_event_sources["QGVPage.cpp"],
            "void QGVPage::contextMenuEvent",
        )
        self.assertNotIn("addAction", qgv_context)
        qgv_pseudo_context = _function_body(
            context_event_sources["QGVPage.cpp"],
            "void QGVPage::pseudoContextEvent",
        )
        self.assertIn(
            "m_parentMDI->contextMenuEvent(m_saveContextEvent)",
            qgv_pseudo_context,
        )

        mdi_context = _function_body(
            page_view,
            "void MDIViewPage::contextMenuEvent",
        )
        mdi_action_members = re.findall(
            r"\bmenu\.addAction\((m_[A-Za-z0-9_]+)\)",
            mdi_context,
        )
        self.assertEqual(len(mdi_action_members), 7)
        self.assertEqual(len(set(mdi_action_members)), 7)
        mdi_constructor = _function_body(
            page_view,
            "MDIViewPage::MDIViewPage(",
        )
        for action_member in mdi_action_members:
            self.assertRegex(
                mdi_constructor,
                re.escape(action_member)
                + r'->setObjectName\(\s*QStringLiteral\("'
                + r"TechDrawContext[A-Za-z0-9]+"
                + r'"\)\s*\)',
            )

        inspection_context_files = {
            path.name
            for path in (self.techdraw.parent / "Inspection/Gui").glob("*.cpp")
            if re.search(
                r"\bQMenu\s+[A-Za-z_][A-Za-z0-9_]*\s*;",
                path.read_text(encoding="utf-8"),
            )
            and re.search(
                r"\b[A-Za-z_][A-Za-z0-9_]*\.exec\s*\(",
                path.read_text(encoding="utf-8"),
            )
        }
        self.assertEqual(
            inspection_context_files,
            {"ViewProviderInspection.cpp"},
        )

        surfaces = (
            _function_body(
                balloon,
                "void ViewProviderBalloon::setupContextMenu",
            ),
            _function_body(
                dimension,
                "void ViewProviderDimension::setupContextMenu",
            ),
            _function_body(
                page_provider,
                "void ViewProviderPage::setupContextMenu",
            ),
            _function_body(
                page_view,
                "MDIViewPage::MDIViewPage(",
            ),
            _function_body(
                inspection,
                "void ViewProviderInspection::inspectCallback",
            ),
        )
        actual_positions = []
        for surface in surfaces:
            actual_positions.extend(
                re.findall(
                    r'setObjectName\(\s*QStringLiteral\("'
                    r'((?:TechDraw|Inspection)Context[A-Za-z0-9]+)"\)\s*\)',
                    surface,
                )
            )
        self.assertCountEqual(
            actual_positions,
            DYNAMIC_CONTEXT_ACTION_POSITIONS,
        )

        contracts = (
            CONTEXT_IN_PLACE_EDIT_ACTIONS,
            CONTEXT_PRESENTATION_ACTIONS,
            CONTEXT_READ_ONLY_OUTPUT_ACTIONS,
        )
        for index, contract in enumerate(contracts):
            for other in contracts[index + 1 :]:
                self.assertTrue(contract.isdisjoint(other))
        self.assertEqual(
            set().union(*contracts),
            set(DYNAMIC_CONTEXT_ACTION_POSITIONS),
        )
        self.assertEqual(tuple(map(len, contracts)), (3, 5, 4))
        self.assertEqual(len(DYNAMIC_CONTEXT_ACTION_POSITIONS), 13)
        self.assertEqual(len(set(DYNAMIC_CONTEXT_ACTION_POSITIONS)), 12)

        for source in (balloon, dimension):
            setup = _function_body(source, "::setupContextMenu")
            self.assertIn("startDefaultEditMode()", setup)

        toggle = _function_body(
            page_provider,
            "bool ViewProviderPage::toggleKeepUpdated()",
        )
        self.assertIn("TaskInternal::OwnedDocumentTransaction", toggle)
        self.assertIn(
            "getBookedTransactionID() != App::NullTransaction",
            toggle,
        )
        self.assertIn("Gui::Command::doCommand(", toggle)
        self.assertIn(".KeepUpdated = %s", toggle)
        self.assertIn("TaskInternal::updateExactDocument(document)", toggle)
        self.assertIn("transaction.commit()", toggle)
        self.assertNotIn("KeepUpdated.setValue", toggle)
        page_edit = _function_body(
            page_provider,
            "bool ViewProviderPage::setEdit(const int ModNum)",
        )
        self.assertIn("toggleKeepUpdated()", page_edit)
        mdi_toggle = _function_body(
            page_view,
            "void MDIViewPage::toggleKeepUpdated()",
        )
        self.assertIn("m_vpPage->toggleKeepUpdated()", mdi_toggle)
        self.assertNotIn("KeepUpdated.setValue", mdi_toggle)

        inspection_context = surfaces[-1]
        self.assertNotIn("addObject", inspection_context)
        self.assertNotIn("openTransaction", inspection_context)
        self.assertNotIn("OwnedDocumentTransaction", inspection_context)

        rich_text = (self.gui / "mrichtextedit.cpp").read_text(encoding="utf-8")
        rich_text_editor = _function_body(
            rich_text,
            "MRichTextEdit::MRichTextEdit(",
        )
        self.assertEqual(
            len(re.findall(r"\bmenu->addAction\s*\(", rich_text_editor)),
            3,
        )
        self.assertNotIn("getDocument()", rich_text_editor)
        self.assertNotIn("openTransaction", rich_text_editor)
        self.assertNotIn("OwnedDocumentTransaction", rich_text_editor)

    def test_only_real_editors_opt_into_document_timeline_edit(self):
        view_provider = (self.techdraw.parents[1] / "Gui/ViewProvider.h").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "virtual bool supportsDocumentTimelineEdit() " "const noexcept",
            view_provider,
        )
        default_capability = _function_body(
            view_provider,
            "virtual bool supportsDocumentTimelineEdit() const noexcept",
        )
        self.assertIn("return false;", default_capability)

        feature_timeline = (self.techdraw.parents[1] / "Gui/FeatureTimeline.cpp").read_text(
            encoding="utf-8"
        )
        self.assertGreaterEqual(
            feature_timeline.count("supportsDocumentTimelineEdit()"),
            3,
        )

        editable_providers = {
            "ViewProviderAnnotation.h",
            "ViewProviderBalloon.h",
            "ViewProviderDimension.h",
            "ViewProviderGeomHatch.h",
            "ViewProviderHatch.h",
            "ViewProviderLeader.h",
            "ViewProviderProjGroup.h",
            "ViewProviderProjGroupItem.h",
            "ViewProviderRichAnno.h",
            "ViewProviderViewPart.h",
            "ViewProviderViewSection.h",
            "ViewProviderWeld.h",
        }
        double_click_providers = {
            path.name
            for path in self.gui.glob("ViewProvider*.h")
            if re.search(
                r"\bdoubleClicked\s*\(",
                path.read_text(encoding="utf-8"),
            )
        }
        self.assertEqual(
            double_click_providers,
            editable_providers | {"ViewProviderPage.h"},
        )
        for filename in editable_providers:
            source = (self.gui / filename).read_text(encoding="utf-8")
            if filename == "ViewProviderProjGroupItem.h":
                self.assertIn(
                    "supportsDocumentTimelineEdit() " "const noexcept override;",
                    source,
                )
                implementation = (self.gui / "ViewProviderProjGroupItem.cpp").read_text(
                    encoding="utf-8"
                )
                capability = _function_body(
                    implementation,
                    "bool ViewProviderProjGroupItem::"
                    "supportsDocumentTimelineEdit() "
                    "const noexcept",
                )
                self.assertIn(
                    "item && !item->getPGroup()",
                    capability,
                )
                continue
            capability = _function_body(
                source,
                "supportsDocumentTimelineEdit() const noexcept",
            )
            self.assertIn("return true;", capability, filename)

        page_provider = (self.gui / "ViewProviderPage.h").read_text(encoding="utf-8")
        self.assertNotIn(
            "supportsDocumentTimelineEdit",
            page_provider,
        )
        page_implementation = (self.gui / "ViewProviderPage.cpp").read_text(encoding="utf-8")
        page_double_click = _function_body(
            page_implementation,
            "bool ViewProviderPage::doubleClicked()",
        )
        self.assertIn("show()", page_double_click)
        self.assertNotIn(
            "startDefaultEditMode()",
            page_double_click,
        )

        inspection_provider = (
            self.techdraw.parent / "Inspection/Gui/ViewProviderInspection.h"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "supportsDocumentTimelineEdit",
            inspection_provider,
        )

    def test_every_timeline_editor_reaches_a_real_mode_zero_editor(self):
        editor_contracts = {
            "ViewProviderAnnotation.cpp": (
                "propertyIndexFromPath(",
                'QStringLiteral("Annotation")',
                'QStringLiteral("Text")',
                "button->click()",
            ),
            "ViewProviderBalloon.cpp": (
                "ModNum != ViewProvider::Default",
                "if (!qgivBalloon)",
                "new TaskDlgBalloon(",
                "TaskInternal::showDocumentDialog(",
            ),
            "ViewProviderDimension.cpp": (
                "ModNum != ViewProvider::Default",
                "if (!qgivDimension)",
                "new TaskDlgDimension(",
                "TaskInternal::showDocumentDialog(",
            ),
            "ViewProviderGeomHatch.cpp": (
                "new TaskDlgGeomHatch(",
                "TaskInternal::showDocumentDialog(",
            ),
            "ViewProviderHatch.cpp": (
                "ModNum != ViewProvider::Default",
                "new TaskDlgHatch(",
                "TaskInternal::showDocumentDialog(",
            ),
            "ViewProviderLeader.cpp": (
                "ModNum != ViewProvider::Default",
                "new TaskDlgLeaderLine(",
                "TaskInternal::showDocumentDialog(",
            ),
            "ViewProviderProjGroup.cpp": (
                "new TaskDlgProjGroup(",
                "TaskInternal::showDocumentDialog(",
            ),
            "ViewProviderRichAnno.cpp": (
                "ModNum != Gui::ViewProvider::Default",
                "new TaskDlgRichAnno(",
                "TaskInternal::showDocumentDialog(",
            ),
            "ViewProviderViewPart.cpp": (
                "ModNum != ViewProvider::Default",
                "new TaskDlgProjGroup(",
                "return setDetailEdit(ModNum, dvd)",
            ),
            "ViewProviderViewSection.cpp": (
                "ModNum != ViewProvider::Default",
                "new TaskDlgComplexSection(",
                "new TaskDlgSectionView(",
            ),
            "ViewProviderWeld.cpp": (
                "ModNum != ViewProvider::Default",
                "new TaskDlgWeldingSymbol(",
                "TaskInternal::showDocumentDialog(",
            ),
        }
        for filename, required in editor_contracts.items():
            source = (self.gui / filename).read_text(encoding="utf-8")
            class_name = filename.removeprefix("ViewProvider").removesuffix(".cpp")
            body = _function_body(
                source,
                f"bool ViewProvider{class_name}::setEdit(int ModNum)",
            )
            for token in required:
                self.assertIn(token, body, filename)

        view_part = (self.gui / "ViewProviderViewPart.cpp").read_text(encoding="utf-8")
        detail_edit = _function_body(
            view_part,
            "bool ViewProviderViewPart::setDetailEdit(" "int ModNum, DrawViewDetail* dvd)",
        )
        self.assertIn("new TaskDlgDetail(", detail_edit)

        projection_item = (self.gui / "ViewProviderProjGroupItem.cpp").read_text(encoding="utf-8")
        projection_edit = _function_body(
            projection_item,
            "bool ViewProviderProjGroupItem::setEdit(int ModNum)",
        )
        self.assertIn(
            "!item->getPGroup()",
            projection_edit,
        )
        self.assertIn(
            "ViewProviderViewPart::setEdit(ModNum)",
            projection_edit,
        )
        self.assertIn("return false;", projection_edit)
        projection_double_click = _function_body(
            projection_item,
            "bool ViewProviderProjGroupItem::doubleClicked()",
        )
        self.assertIn(
            "groupProvider->startDefaultEditMode()",
            projection_double_click,
        )

    def test_multi_output_commands_create_one_semantic_history_step(self):
        helpers = (self.gui / "CommandHelpers.cpp").read_text(encoding="utf-8")
        grouping = _function_body(
            helpers,
            "App::DocumentObjectGroup* " "CommandHelpers::groupTimelineOutputs",
        )
        self.assertIn("outputs.size() < 2", grouping)
        self.assertIn("'App::DocumentObjectGroup'", grouping)
        self.assertNotIn("App.getDocument(%s)", grouping)
        self.assertIn("App.getDocument('%1')", grouping)
        self.assertIn("runDocumentObjectCommand(", grouping)
        self.assertIn("resolveExactGroup", grouping)
        self.assertIn("resolveExactOutput", grouping)
        self.assertNotIn("addDynamicProperty", grouping)
        self.assertNotIn(".addProperty(", grouping)
        self.assertNotIn("VibeCADTimelineRole =", grouping)
        self.assertNotIn("VibeCADTimelineOwner =", grouping)
        self.assertIn(
            "timeline->publishProvisionalOperationBlock(" "group,liveOutputs)",
            "".join(grouping.split()),
        )
        self.assertIn(
            "publishProvisionalTimelineOperationBlock(",
            grouping,
        )
        self.assertLess(
            grouping.index("timeline->publishProvisionalOperationBlock("),
            grouping.index("publishProvisionalTimelineOperationBlock("),
        )
        self.assertIn("liveOutputs.reserve(identities.size())", grouping)
        self.assertIn(
            "liveOutputs.push_back(resolveExactOutput(expectedOutput,outputId))",
            "".join(grouping.split()),
        )

        command = (self.gui / "Command.cpp").read_text(encoding="utf-8")
        for signature in (
            "void CmdTechDrawPageDefault::activated(int iMsg)",
            "void CmdTechDrawPageTemplate::activated(int iMsg)",
        ):
            create_page = _function_body(command, signature)
            self.assertLess(
                create_page.index("addObject<TechDraw::DrawSVGTemplate>"),
                create_page.index("addObject<TechDraw::DrawPage>"),
                signature,
            )
            self.assertIn(
                "timeline->finalizeProvisionalOperationBlock(" "page,{svgTemplate,page})",
                "".join(create_page.split()),
                signature,
            )
            self.assertIn(
                "DU::markAsTimelineResource(svgTemplate,page)",
                "".join(create_page.split()),
                signature,
            )

        page_source = (self.techdraw / "App/DrawPage.cpp").read_text(encoding="utf-8")
        page_changed = _function_body(
            page_source,
            "void DrawPage::onChanged(const App::Property* prop)",
        )
        self.assertNotIn("markAsTimelineResource", page_changed)
        page_unsetup = _function_body(
            page_source,
            "void DrawPage::unsetupObject()",
        )
        self.assertIn(
            "App::DocumentTimeline::timelineOwner(tmp) == this",
            page_unsetup,
        )

        draw_util = (self.techdraw / "App/DrawUtil.cpp").read_text(encoding="utf-8")
        resource_marker = _function_body(
            draw_util,
            "/*static*/ void DrawUtil::markAsTimelineResource(",
        )
        self.assertIn(
            "ownerRole->setValue(" "App::DocumentTimeline::OperationRole)",
            "".join(resource_marker.split()),
        )
        for status in ("Hidden", "LockDynamic", "NoRecompute"):
            self.assertIn(
                f"property->setStatus(App::Property::{status},true)",
                "".join(resource_marker.split()),
            )

        view = _function_body(
            command,
            "void CmdTechDrawView::activated(int iMsg)",
        )
        self.assertIn("createdViews.push_back(sheetView)", view)
        self.assertIn("createdViews.push_back(dvp)", view)
        self.assertNotIn("App.getDocument(%s)", view)
        self.assertEqual(
            view.count("CommandHelpers::groupTimelineOutputs("),
            2,
        )
        draft = _function_body(
            command,
            "void CmdTechDrawDraftView::activated(int iMsg)",
        )
        self.assertIn("createdViews.push_back(draftView)", draft)
        self.assertNotIn("App.getDocument(%s)", draft)
        self.assertIn(
            "CommandHelpers::groupTimelineOutputs(",
            draft,
        )

        extensions = (self.gui / "CommandExtensionDims.cpp").read_text(encoding="utf-8")
        multi_dimension_helpers = (
            "execCreateHorizChainDimension",
            "execCreateVertChainDimension",
            "execCreateObliqueChainDimension",
            "execCreateHorizCoordDimension",
            "execCreateVertCoordDimension",
            "execCreateObliqueCoordDimension",
        )
        for function in multi_dimension_helpers:
            body = _function_body(
                extensions,
                f"void {function}(Gui::Command* cmd)",
            )
            self.assertIn(
                "CommandHelpers::groupTimelineOutputs(",
                body,
                function,
            )
        self.assertEqual(
            extensions.count("CommandHelpers::groupTimelineOutputs("),
            len(multi_dimension_helpers),
        )

        projection = (self.gui / "TaskProjection.cpp").read_text(encoding="utf-8")
        project_shapes = _function_body(
            projection,
            "bool TaskProjection::accept()",
        )
        self.assertIn(
            "createdProjections.push_back(projection)",
            project_shapes,
        )
        self.assertIn(
            "TechDraw::CommandHelpers::groupTimelineOutputs(",
            project_shapes,
        )
        self.assertLess(
            project_shapes.index("createdProjections.push_back(projection)"),
            project_shapes.index("TechDraw::CommandHelpers::groupTimelineOutputs("),
        )

        dimensions = (self.gui / "CommandCreateDims.cpp").read_text(encoding="utf-8")
        interactive_finalize = _function_body(
            dimensions,
            "void finalizeCommand()",
        )
        self.assertIn(
            "CommandHelpers::groupTimelineOutputs(",
            interactive_finalize,
        )
        self.assertLess(
            interactive_finalize.index("CommandHelpers::groupTimelineOutputs("),
            interactive_finalize.index("commitSessionTransaction()"),
        )

    def test_macro_replay_quotes_document_names_and_user_text(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in self.gui.glob("*.cpp"))
        forbidden_unquoted_fragments = (
            "App.getDocument(%s)",
            ".HatchPattern = %s",
            ".ImageFile = %s",
            "codecs.open(%s",
            "writeDXFPage(%s, %s)",
            ".SectionSymbol = %s",
            ".SectionDirection = %s",
            ".Label = %s",
        )
        for fragment in forbidden_unquoted_fragments:
            self.assertNotIn(fragment, source, fragment)

    def test_shared_inspection_commands_follow_their_history_contracts(self):
        module_root = self.techdraw.parent

        inspection_init = (module_root / "Inspection/InitGui.py").read_text(encoding="utf-8")
        self.assertIn(
            'FreeCAD.__unit_test__ += ["TestInspectionGui"]',
            inspection_init,
        )

        visual_inspection = (module_root / "Inspection/Gui/VisualInspection.cpp").read_text(
            encoding="utf-8"
        )
        recorded = _function_body(
            visual_inspection,
            "void recordAcceptedVisualInspection(",
        )
        self.assertIn(
            "__vibecad_inspection_resources.append(",
            recorded,
        )
        self.assertLess(
            recorded.index("'Inspection::Feature'"),
            recorded.index("'Inspection::Group'"),
        )
        self.assertNotIn("VibeCADTimelineOwner", recorded)
        self.assertNotIn("VibeCADTimelineRole", recorded)
        self.assertIn(
            "publishProvisionalTimelineOperationBlock(",
            recorded,
        )
        self.assertIn(
            "for __vibecad_inspection in " "__vibecad_inspection_resources:",
            recorded,
        )
        self.assertLess(
            recorded.index("'Inspection::Group'"),
            recorded.index("publishProvisionalTimelineOperationBlock("),
        )

        accept = _function_body(
            visual_inspection,
            "void VisualInspection::accept()",
        )
        self.assertIn(
            'Gui::ExactTransaction>(*document, "Visual Inspection")',
            accept,
        )
        self.assertNotIn("markTimelineOperation(*group)", accept)
        self.assertNotIn("markTimelineResource(*inspection, *group)", accept)
        self.assertIn(
            "markTimelineReplacedInputs(*group, replacedInputs)",
            accept,
        )
        self.assertIn(
            "timeline->publishProvisionalOperationBlock(" "group,timelineResources)",
            "".join(accept.split()),
        )
        self.assertLess(
            accept.index("publishProvisionalOperationBlock"),
            accept.index("transaction->commit()"),
        )

        inspection_commands = (module_root / "Inspection/Gui/Command.cpp").read_text(
            encoding="utf-8"
        )
        inspect_element = _function_body(
            inspection_commands,
            "void CmdInspectElement::activated(int)",
        )
        self.assertIn("viewer->addEventCallback(", inspect_element)
        self.assertNotIn("addObject", inspect_element)
        self.assertNotIn("openTransaction", inspect_element)
        self.assertNotIn("ExactTransaction", inspect_element)

        part_commands = (module_root / "Part/Gui/Command.cpp").read_text(encoding="utf-8")
        check_geometry = _function_body(
            part_commands,
            "void CmdCheckGeometry::activated(int iMsg)",
        )
        self.assertIn("TaskCheckGeometryDialog", check_geometry)
        self.assertNotIn("addObject", check_geometry)
        self.assertNotIn("openCommand", check_geometry)

        measure = (module_root / "Measure/Gui/TaskMeasure.cpp").read_text(encoding="utf-8")
        save_measurement = _function_body(
            measure,
            "bool TaskMeasure::apply(bool reset)",
        )
        self.assertIn("ensureGroup(_mMeasureObject)", save_measurement)
        self.assertIn(
            "finishPreviewTransaction(true)",
            save_measurement,
        )
        self.assertIn(
            "markCommandInteractionStateDurable()",
            save_measurement,
        )
        self.assertIn(
            "beginPreviewTransaction()",
            save_measurement,
        )

        mass_properties = (module_root / "Measure/Gui/TaskMassProperties.cpp").read_text(
            encoding="utf-8"
        )
        save_mass_properties = _function_body(
            mass_properties,
            "void TaskMassProperties::saveResult()",
        )
        self.assertIn(
            "OwnedMassPropertiesTransaction>(\n" "        *doc,\n" '        "Add Mass Properties"',
            save_mass_properties,
        )
        self.assertEqual(
            save_mass_properties.count('doc->addObject("Measure::Result", ' '"MassProperties")'),
            1,
        )
        self.assertIn(
            "finishDurableResult(std::move(transaction))",
            save_mass_properties,
        )

    def test_print_all_stays_bound_to_the_invoking_document(self):
        command = (self.gui / "Command.cpp").read_text(encoding="utf-8")
        activated = _function_body(
            command,
            "void CmdTechDrawPrintAll::activated(int iMsg)",
        )
        self.assertIn("MDIViewPage::printAllPages(getDocument())", activated)

        mdi = (self.gui / "MDIViewPage.cpp").read_text(encoding="utf-8")
        member = _function_body(mdi, "void MDIViewPage::printAll()")
        self.assertIn("page->getDocument()", member)
        exact = _function_body(
            mdi,
            "void MDIViewPage::printAllPages(App::Document* document)",
        )
        self.assertIn("documentName = document->getName()", exact)
        self.assertIn("liveDocument != document", exact)
        self.assertNotIn("getActiveDocument()", exact)

    def test_every_shipped_command_and_group_child_is_registered(self):
        command_text = "\n".join(
            path.read_text(encoding="utf-8") for path in self.gui.glob("Command*.cpp")
        )
        registered = set(
            re.findall(
                r'(?:Command|GroupCommand)\("(TechDraw_[A-Za-z0-9_]+)"\)',
                command_text,
            )
        )
        python_sources = []
        for path in (self.techdraw / "TechDrawTools").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            python_sources.append(source)
            registered.update(
                re.findall(
                    r'Gui\.addCommand\([\'"](TechDraw_[A-Za-z0-9_]+)',
                    source,
                )
            )
        group_children = set(
            re.findall(
                r'addCommand\("(TechDraw_[A-Za-z0-9_]+)"\)',
                command_text,
            )
        )
        group_children.update(
            re.findall(
                r'[\'"](TechDraw_[A-Za-z0-9_]+)[\'"]',
                "\n".join(
                    match.group("body")
                    for source in python_sources
                    if (
                        match := re.search(
                            r"^\s+def GetCommands\(self\):" r"(?P<body>.*?)(?=^\s+def |\Z)",
                            source,
                            re.MULTILINE | re.DOTALL,
                        )
                    )
                ),
            )
        )
        self.assertFalse(SHIPPED_DRAWING_COMMANDS - registered)
        self.assertFalse(group_children - registered)

    def test_mutating_commands_use_the_global_caller_transaction_gate(self):
        command_sources = "\n".join(
            path.read_text(encoding="utf-8") for path in self.gui.glob("Command*.cpp")
        )
        active_type_overrides = re.findall(
            r"^\s*eType\s*[|]?=",
            _without_line_comments(command_sources),
            re.MULTILINE,
        )
        self.assertEqual(active_type_overrides, [])

        global_command = (self.techdraw.parents[1] / "Gui/Command.cpp").read_text(encoding="utf-8")
        self.assertIn("bool Command::canInvoke()", global_command)
        self.assertIn("getBookedTransactionID()", global_command)
        self.assertIn("ownedEnclosingTransactionId(document)", global_command)

    def test_python_ribbon_tools_own_their_exact_document_transactions(self):
        tools = self.techdraw / "TechDrawTools"
        sources = {
            name: (tools / name).read_text(encoding="utf-8")
            for name in (
                "CommandAxoLengthDimension.py",
                "CommandPositionSectionView.py",
                "TaskAddOffsetVertex.py",
                "TaskFillTemplateFields.py",
            )
        }
        for name, source in sources.items():
            self.assertNotRegex(
                source,
                r"App\.ActiveDocument\." r"(?:openTransaction|commitTransaction|abortTransaction)",
                name,
            )
        for name in (
            "CommandAxoLengthDimension.py",
            "CommandPositionSectionView.py",
            "TaskFillTemplateFields.py",
        ):
            self.assertIn(
                "_OwnedDocumentTransaction",
                sources[name],
                name,
            )

        axonometric = sources["CommandAxoLengthDimension.py"]
        self.assertLess(
            axonometric.index("if not self.IsActive():"),
            axonometric.index("_OwnedDocumentTransaction("),
        )
        self.assertNotIn("HalfPie", axonometric)
        self.assertIn("view.touch()", axonometric)

        hole_command = (tools / "CommandHoleShaftFit.py").read_text(encoding="utf-8")
        hole_task = (tools / "TaskHoleShaftFit.py").read_text(encoding="utf-8")
        self.assertIn(
            "Gui.Control.showDialog(\n"
            "            self.ui,\n"
            "            self.ui.gui_document,",
            hole_command,
        )
        self.assertIn("self.document = self.dimension.Document", hole_task)
        self.assertIn("self.gui_document.openCommand(", hole_task)
        self.assertNotRegex(
            hole_task,
            r"(?:commit|abort)(?:Transaction|Command)?\(",
        )
        self.assertNotIn("_OwnedDocumentTransaction", hole_task)
        offset_task = sources["TaskAddOffsetVertex.py"]
        self.assertIn(
            "self.document = view.Document",
            offset_task,
        )
        self.assertIn(
            "self.gui_document.openCommand(",
            offset_task,
        )
        self.assertNotRegex(
            offset_task,
            r"(?:commit|abort)(?:Transaction|Command)?\(",
        )

        template_task = sources["TaskFillTemplateFields.py"]
        self.assertIn(
            "def __init__(self, document=None):",
            template_task,
        )
        self.assertIn(
            "updated_texts = dict(self.texts)",
            template_task,
        )

        transaction_owner = (
            self.techdraw.parent / "VibeCAD/VibeCADNativeTransaction.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "document.openTransaction(name)",
            transaction_owner,
        )
        self.assertIn(
            "document.getBookedTransactionID()",
            transaction_owner,
        )
        self.assertNotIn(
            "int(document.openTransaction(name))",
            transaction_owner,
        )
        self.assertNotIn(
            "App.setActiveTransaction(name)",
            transaction_owner,
        )

    def test_immediate_creation_tasks_retain_one_exact_transaction(self):
        command = (self.gui / "Command.cpp").read_text(encoding="utf-8")
        view_creation = _function_body(
            command,
            "void CmdTechDrawView::activated(int iMsg)",
        )
        view_task = view_creation[
            view_creation.index('QT_TRANSLATE_NOOP("Command", "Create view")') :
        ]
        model_view_task = view_task[view_task.index('getUniqueObjectName("View")') :]
        projection_creation = _function_body(
            command,
            "void CmdTechDrawProjectionGroup::activated(int iMsg)",
        )
        projection_task = projection_creation[
            projection_creation.index(
                'QT_TRANSLATE_NOOP("Command", ' '"Create projection group")'
            ) :
        ]
        self.assertNotIn("commitCommand", model_view_task)
        self.assertNotIn("commitCommand", projection_task)
        self.assertIn("dvp->isError()", model_view_task)
        self.assertIn("anchor->isError()", projection_task)
        self.assertIn("showDocumentDialog", model_view_task)
        self.assertIn("showDocumentDialog", projection_task)

        projection_editor = (self.gui / "TaskProjGroup.cpp").read_text(encoding="utf-8")
        projection_accept = _function_body(
            projection_editor,
            "bool TaskProjGroup::accept()",
        )
        self.assertIn(
            "timeline->finalizeProvisionalOperationBlock(",
            projection_accept,
        )
        conversion = _function_body(
            projection_editor,
            "void TaskProjGroup::turnViewToProjGroup()",
        )
        self.assertNotIn("markAsTimelineResource", conversion)
        projection_source = (self.techdraw / "App/DrawProjGroup.cpp").read_text(encoding="utf-8")
        projection_unsetup = _function_body(
            projection_source,
            "void DrawProjGroup::unsetupObject()",
        )
        self.assertIn(
            "App::DocumentTimeline::timelineOwner(child) == this",
            projection_unsetup,
        )
        self.assertIn("page->addView(child, false)", projection_unsetup)

        weld_editor = (self.gui / "TaskWeldingSymbol.cpp").read_text(encoding="utf-8")
        weld_accept = _function_body(
            weld_editor,
            "bool TaskWeldingSymbol::accept()",
        )
        self.assertIn(
            "timeline->finalizeProvisionalOperationBlock(",
            weld_accept,
        )
        weld_update = _function_body(
            weld_editor,
            "void TaskWeldingSymbol::updateTiles()",
        )
        self.assertNotIn("addObject", weld_update)

        detail = (self.gui / "TaskDetail.cpp").read_text(encoding="utf-8")
        detail_create = _function_body(detail, "void TaskDetail::createDetail()")
        detail_update = _function_body(detail, "void TaskDetail::updateDetail()")
        self.assertNotIn("openActiveDocumentCommand", detail_create)
        self.assertIn("getBookedTransactionID()", detail_create)
        self.assertNotIn("commitCommand", detail_create)
        self.assertNotIn("openActiveDocumentCommand", detail_update)
        self.assertNotIn("commitCommand", detail_update)

        center = (self.gui / "TaskCenterLine.cpp").read_text(encoding="utf-8")
        center_create = _function_body(
            center,
            "void TaskCenterLine::createCenterLine()",
        )
        center_reject = _function_body(
            center,
            "bool TaskCenterLine::reject()",
        )
        self.assertNotIn("openActiveDocumentCommand", center_create)
        self.assertIn("getBookedTransactionID()", center_create)
        self.assertNotIn("commitCommand", center_create)
        self.assertNotIn("abortCommand", center_create)
        self.assertNotIn("undo(", center_reject)

        projection_task = (self.gui / "TaskProjGroup.cpp").read_text(encoding="utf-8")
        reject = _function_body(
            projection_task,
            "bool TaskProjGroup::reject()",
        )
        opened = _function_body(
            projection_task,
            "void TaskDlgProjGroup::open()",
        )
        self.assertNotIn("abortCommand", reject)
        self.assertNotIn("removeObject", reject)
        self.assertNotIn("setActiveTransaction", opened)

    def test_section_tasks_keep_creation_and_live_updates_in_one_transaction(self):
        command = (self.gui / "Command.cpp").read_text(encoding="utf-8")
        simple_signature = "void execSimpleSection(Gui::Command* cmd)"
        simple = _function_body(
            command[command.rindex(simple_signature) :],
            simple_signature,
        )
        complex_signature = "void execComplexSection(Gui::Command* cmd)"
        complex_section = _function_body(
            command[command.rindex(complex_signature) :],
            complex_signature,
        )
        for body in (simple, complex_section):
            self.assertLess(
                body.index("openCommand"),
                body.index("showDocumentDialog"),
            )
            self.assertNotIn("commitCommand", body)

        contracts = (
            ("TaskSectionView.cpp", "TaskSectionView"),
            ("TaskComplexSection.cpp", "TaskComplexSection"),
        )
        for filename, class_name in contracts:
            source = (self.gui / filename).read_text(encoding="utf-8")
            create = _function_body(
                source,
                f"{'TechDraw::DrawViewSection*' if class_name == 'TaskSectionView' else 'void'} "
                f"{class_name}::create"
                f"{'SectionView(void)' if class_name == 'TaskSectionView' else 'ComplexSection()'}",
            )
            update = _function_body(
                source,
                f"void {class_name}::update"
                f"{'SectionView()' if class_name == 'TaskSectionView' else 'ComplexSection()'}",
            )
            reject = _function_body(source, f"bool {class_name}::reject()")
            dialog_accept = _function_body(
                source,
                f"bool TaskDlg"
                f"{'SectionView' if class_name == 'TaskSectionView' else 'ComplexSection'}"
                "::accept()",
            )
            self.assertNotIn("openActiveDocumentCommand", create)
            self.assertNotIn("commitCommand", create)
            self.assertNotIn("openActiveDocumentCommand", update)
            self.assertNotIn("commitCommand", update)
            self.assertNotIn("removeObject", reject)
            self.assertNotIn("restoreSectionState", reject)
            self.assertIn("return widget->accept()", dialog_accept)

    def test_hatch_tasks_use_atomic_accept_and_cancel(self):
        command = (self.gui / "CommandDecorate.cpp").read_text(encoding="utf-8")
        image_hatch = _function_body(
            command,
            "void CmdTechDrawHatch::activated(int iMsg)",
        )
        geometric_hatch = _function_body(
            command,
            "void CmdTechDrawGeometricHatch::activated(int iMsg)",
        )
        for body in (image_hatch, geometric_hatch):
            self.assertLess(
                body.index("openCommand"),
                body.index("showDocumentDialog"),
            )
            self.assertNotIn("commitCommand", body)

        hatch_task = (self.gui / "TaskHatch.cpp").read_text(encoding="utf-8")
        for signature in (
            "void TaskHatch::createHatch()",
            "void TaskHatch::updateHatch()",
        ):
            body = _function_body(hatch_task, signature)
            self.assertNotIn("openActiveDocumentCommand", body)
            self.assertNotIn("commitCommand", body)
        hatch_reject = _function_body(hatch_task, "bool TaskHatch::reject()")
        self.assertNotIn("restoreHatchState", hatch_reject)
        self.assertNotIn("removeObject", hatch_reject)

        geometric_task = (self.gui / "TaskGeomHatch.cpp").read_text(encoding="utf-8")
        geometric_reject = _function_body(
            geometric_task,
            "bool TaskGeomHatch::reject()",
        )
        self.assertNotIn("removeObject", geometric_reject)
        self.assertNotIn("m_orig", geometric_reject)

    def test_double_click_editors_use_the_exact_edit_entry_boundary(self):
        expected_editors = {
            "ViewProviderAnnotation.cpp",
            "ViewProviderBalloon.cpp",
            "ViewProviderDimension.cpp",
            "ViewProviderGeomHatch.cpp",
            "ViewProviderHatch.cpp",
            "ViewProviderLeader.cpp",
            "ViewProviderProjGroup.cpp",
            "ViewProviderProjGroupItem.cpp",
            "ViewProviderRichAnno.cpp",
            "ViewProviderViewPart.cpp",
            "ViewProviderViewSection.cpp",
            "ViewProviderWeld.cpp",
        }
        for filename in expected_editors:
            source = (self.gui / filename).read_text(encoding="utf-8")
            match = re.search(
                r"bool\s+[A-Za-z0-9_:]+::doubleClicked\(\)\s*\{" r"(?P<body>.*?)\n\}",
                source,
                re.DOTALL,
            )
            self.assertIsNotNone(match, filename)
            body = match.group("body")
            self.assertIn("startDefaultEditMode()", body, filename)
            self.assertNotIn("setEdit(", body, filename)

    def test_skip_recompute_is_scoped_and_restores_prior_state(self):
        command = (self.gui / "Command.cpp").read_text(encoding="utf-8")
        self.assertEqual(
            command.count("App::Document::Status::SkipRecompute"),
            3,
        )
        self.assertGreaterEqual(command.count("ScopedDocumentStatus"), 4)
        self.assertNotRegex(
            command,
            r"setStatus\s*\(\s*App::Document::Status::SkipRecompute",
        )
        scope = _function_body(command, "class ScopedDocumentStatus")
        self.assertIn("original(document.testStatus(status))", scope)
        self.assertIn("document->setStatus(status, original)", scope)

    def test_broken_view_merges_sources_into_the_matching_container(self):
        command = (self.gui / "Command.cpp").read_text(encoding="utf-8")
        broken = _function_body(
            command,
            "void CmdTechDrawBrokenView::activated(int iMsg)",
        )
        self.assertIn(
            "xShapes.insert(xShapes.end(), " "xShapesFromBase.begin(), xShapesFromBase.end())",
            broken,
        )
        self.assertNotIn("shapes.insert(xShapes.end()", broken)

    def test_page_scene_keeps_non_view_hatch_attachment_reachable(self):
        scene = (self.gui / "QGSPage.cpp").read_text(encoding="utf-8")
        attach = _function_body(
            scene,
            "bool QGSPage::attachView(App::DocumentObject* obj)",
        )
        self.assertIn(
            "DrawUtil::isActiveInDocumentTimeline(obj)",
            attach,
        )
        self.assertIn(
            "drawingView && !drawingView->isActiveInDocumentTimeline()",
            attach,
        )
        self.assertNotIn(
            "!drawingView || !drawingView->isActiveInDocumentTimeline()",
            attach,
        )
        self.assertIn(
            "freecad_cast<TechDraw::DrawHatch*>(obj)",
            attach,
        )

    def test_page_scene_uses_recursive_active_collection_flattening(self):
        scene = (self.gui / "QGSPage.cpp").read_text(encoding="utf-8")
        add_children = _function_body(
            scene,
            "void QGSPage::addChildrenToPage()",
        )
        self.assertIn("getAllActiveViews()", add_children)
        self.assertNotIn("Views.getValues()", add_children)

    def test_page_scene_orphan_cleanup_uses_document_owned_timeline_query(self):
        scene = (self.gui / "QGSPage.cpp").read_text(encoding="utf-8")
        fix_orphans = _function_body(
            scene,
            "void QGSPage::fixOrphans(bool force)",
        )
        self.assertIn("!doc->containsObject(obj)", fix_orphans)
        self.assertIn("obj->getDocument() != doc", fix_orphans)
        self.assertLess(
            fix_orphans.index("!doc->containsObject(obj)"),
            fix_orphans.index("obj->getDocument() != doc"),
        )
        self.assertIn(
            "DrawUtil::isActiveInDocumentTimeline(obj)",
            fix_orphans,
        )
        self.assertNotIn(
            "obj->isActiveInDocumentTimeline()",
            fix_orphans,
        )

    def test_command_target_resolution_excludes_future_drawing_objects(self):
        utility = (self.gui / "DrawGuiUtil.cpp").read_text(encoding="utf-8")
        find_page = _function_body(
            utility,
            "TechDraw::DrawPage* DrawGuiUtil::findPage",
        )
        need_page = _function_body(
            utility,
            "bool DrawGuiUtil::needPage",
        )
        need_view = _function_body(
            utility,
            "bool DrawGuiUtil::needView",
        )
        self.assertIn("removeInactiveDrawingObjects(docPages)", find_page)
        self.assertIn("isActiveDrawingObject(object)", find_page)
        self.assertIn("removeInactiveDrawingObjects(docPages)", need_page)
        self.assertIn("removeInactiveDrawingObjects(selPages)", need_page)
        self.assertIn("removeInactiveDrawingObjects(selParts)", need_view)

        helpers = (self.gui / "CommandHelpers.cpp").read_text(encoding="utf-8")
        self.assertIn(
            "DrawUtil::isActiveInDocumentTimeline(docobj)",
            _function_body(
                helpers,
                "TechDraw::DrawView* CommandHelpers::firstViewInSelection",
            ),
        )
        self.assertIn(
            "dvp->isActiveInDocumentTimeline()",
            _function_body(
                helpers,
                "std::vector<std::string> CommandHelpers::getSelectedSubElements",
            ),
        )

    def test_python_factories_retain_their_exact_returned_objects(self):
        expected_factory_counts = {
            "Command.cpp": 10,
            "CommandCreateDims.cpp": 1,
            "CommandExtensionDims.cpp": 1,
            "CommandHelpers.cpp": 1,
            "CommandDecorate.cpp": 2,
            "QGSPage.cpp": 1,
            "CommandExtensionPack.cpp": 1,
            "TaskComplexSection.cpp": 1,
            "TaskLeaderLine.cpp": 1,
            "TaskRichAnno.cpp": 1,
            "TaskSectionView.cpp": 1,
            "TaskProjGroup.cpp": 2,
            "CommandAnnotate.cpp": 1,
            "TaskDetail.cpp": 1,
            "TaskHatch.cpp": 1,
            "TaskProjection.cpp": 1,
        }
        predicted_name_lookup = re.compile(
            r"\b(?:document|doc)->getObject\(\s*" r"[A-Za-z_][A-Za-z0-9_]*(?:\.c_str\(\))?\s*\)"
        )
        for filename, expected_count in expected_factory_counts.items():
            source = (self.gui / filename).read_text(encoding="utf-8")
            self.assertGreaterEqual(
                source.count("runDocumentObjectCommand("),
                expected_count,
                filename,
            )
            self.assertNotRegex(
                source,
                predicted_name_lookup,
                filename,
            )

        delayed_creation_contracts = {
            "TaskComplexSection.cpp": (
                "void TaskComplexSection::createComplexSection()",
                "m_sectionName = m_section->getNameInDocument()",
            ),
            "TaskLeaderLine.cpp": (
                "void TaskLeaderLine::createLeaderFeature(",
                "m_leaderName = m_lineFeat->getNameInDocument()",
            ),
            "TaskRichAnno.cpp": (
                "void TaskRichAnno::createAnnoFeature(",
                "annoName = m_annoFeat->getNameInDocument()",
            ),
            "TaskSectionView.cpp": (
                "TechDraw::DrawViewSection* " "TaskSectionView::createSectionView(void)",
                "m_sectionName = m_section->getNameInDocument()",
            ),
            "TaskDetail.cpp": (
                "void TaskDetail::createDetail()",
                "m_detailName = dvd->getNameInDocument()",
            ),
            "TaskHatch.cpp": (
                "void TaskHatch::createHatch()",
                "FeatName = m_hatch->getNameInDocument()",
            ),
        }
        for filename, (signature, actual_name_assignment) in delayed_creation_contracts.items():
            source = (self.gui / filename).read_text(encoding="utf-8")
            body = _function_body(source, signature)
            self.assertIn("runDocumentObjectCommand(", body, filename)
            self.assertIn(actual_name_assignment, body, filename)
            self.assertNotRegex(body, predicted_name_lookup, filename)

        active_view = (self.gui / "TaskActiveView.cpp").read_text(encoding="utf-8")
        active_view_factory = _function_body(
            active_view,
            "TechDraw::DrawViewImage* TaskActiveView::createActiveView()",
        )
        self.assertIn(
            "auto* newObj = pageDocument->addObject(",
            active_view_factory,
        )
        self.assertNotRegex(
            active_view_factory,
            predicted_name_lookup,
        )

    def test_generated_history_blocks_use_exact_resources_not_history_scans(self):
        projection = (self.gui / "TaskProjGroup.cpp").read_text(encoding="utf-8")
        projection_accept = _function_body(
            projection,
            "bool TaskProjGroup::accept()",
        )
        self.assertIn("multiView->Views.getValues()", projection_accept)
        self.assertNotIn("Operations.getValues()", projection_accept)

        welding = (self.gui / "TaskWeldingSymbol.cpp").read_text(encoding="utf-8")
        welding_accept = _function_body(
            welding,
            "bool TaskWeldingSymbol::accept()",
        )
        self.assertIn("{m_arrowFeat, m_otherFeat}", welding_accept)
        self.assertNotIn("Operations.getValues()", welding_accept)

        section = (self.techdraw.parent / "Part/Gui/SectionCutting.cpp").read_text(encoding="utf-8")
        finalize = _function_body(
            section,
            "void SectionCut::finalizeSemanticTimeline()",
        )
        self.assertIn("newResourceIdentities", finalize)
        self.assertIn("identity.resolve(doc)", finalize)
        self.assertNotIn("Operations.getValues()", finalize)
        self.assertNotIn("getObjects()", finalize)

        fragments = _function_body(
            section,
            "App::DocumentObject* " "SectionCut::CreateBooleanFragments(App::Document* doc)",
        )
        self.assertIn("runDocumentObjectCommand(", fragments)
        self.assertIn("setSemanticResource(CompoundName, object)", fragments)
        self.assertNotIn("doc->getObject(", fragments)

        owner_lookup = _function_body(
            section,
            "Part::Compound* SectionCut::findSemanticOwner() const",
        )
        self.assertLess(
            owner_lookup.index("semanticOwnerIdentity.resolve(doc)"),
            owner_lookup.index("timeline->Operations.getValues()"),
        )
        self.assertIn(
            "semanticOwnerIdentity.capture(result)",
            owner_lookup,
        )


@unittest.skipIf(App is None or Gui is None, "FreeCAD GUI runtime is unavailable")
class TechDrawGuiBehaviorRuntimeContractTest(unittest.TestCase):
    def setUp(self):
        Gui.activateWorkbench("TechDrawWorkbench")
        self.document = App.newDocument("TechDrawBehaviorContract")

    def tearDown(self):
        if App.getDocument(self.document.Name):
            App.closeDocument(self.document.Name)

    def test_shipped_inventory_is_registered(self):
        self.assertFalse(SHIPPED_DRAWING_COMMANDS - set(Gui.listCommands()))

    def test_page_command_refuses_a_caller_owned_transaction(self):
        command = "TechDraw_PageTemplate"
        self.assertTrue(Gui.isCommandActive(command))
        self.document.openTransaction("Caller owned")
        transaction = self.document.getBookedTransactionID()
        self.assertNotEqual(transaction, 0)
        self.assertFalse(Gui.isCommandActive(command))
        self.assertEqual(
            self.document.getBookedTransactionID(),
            transaction,
        )
        App.closeActiveTransaction(True, transaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_context_keep_updated_is_one_in_place_undoable_edit(self):
        from .TechDrawTestUtilities import createPageWithSVGTemplate

        page = createPageWithSVGTemplate(self.document)
        self.document.recompute()
        page.ViewObject.Visibility = True
        Gui.updateGui()
        actions = Gui.getMainWindow().findChildren(
            QtGui.QAction,
            "TechDrawContextToggleKeepUpdated",
        )
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertTrue(action.isEnabled())
        self.assertTrue(page.KeepUpdated)

        timeline = self.document.getObject("VibeCADTimeline")
        operations_before = tuple(operation.Name for operation in timeline.Operations)
        self.document.UndoMode = True
        undo_before = self.document.UndoCount
        action.trigger()
        self.assertTrue(_wait_until(lambda: not page.KeepUpdated))
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.assertEqual(
            tuple(operation.Name for operation in timeline.Operations),
            operations_before,
        )

        self.document.undo()
        Gui.updateGui()
        self.assertTrue(page.KeepUpdated)
        self.document.redo()
        Gui.updateGui()
        self.assertFalse(page.KeepUpdated)

        self.document.openTransaction("Caller owned")
        transaction = self.document.getBookedTransactionID()
        action.trigger()
        Gui.updateGui()
        self.assertFalse(page.KeepUpdated)
        self.assertEqual(
            self.document.getBookedTransactionID(),
            transaction,
        )
        App.closeActiveTransaction(True, transaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_embedded_template_is_rendered_after_reopen(self):
        from .TechDrawTestUtilities import createPageWithSVGTemplate

        page = createPageWithSVGTemplate(self.document)
        self.document.recompute()
        page_name = page.Name
        with tempfile.TemporaryDirectory() as directory:
            filename = os.path.join(directory, "drawing-template-reopen.FCStd")
            self.document.saveAs(filename)
            App.closeDocument(self.document.Name)

            self.document = App.openDocument(filename)
            restored_page = self.document.getObject(page_name)
            restored_template = restored_page.Template
            self.assertTrue(os.path.isfile(str(restored_template.PageResult)))
            self.assertGreater(
                os.path.getsize(str(restored_template.PageResult)),
                0,
            )

            restored_page.ViewObject.Visibility = True
            Gui.updateGui()
            graphics_view = _wait_until(
                lambda: Gui.getMainWindow().findChild(
                    QtGui.QGraphicsView,
                    f"{restored_page.Name}View",
                )
            )
            self.assertIsNotNone(graphics_view)
            template_type = int(QtGui.QGraphicsItem.UserType) + 30
            template_items = [
                item
                for item in graphics_view.scene().items()
                if item.type() == template_type
            ]
            self.assertEqual(len(template_items), 1)
            self.assertFalse(template_items[0].boundingRect().isEmpty())
            rendered_svg_items = [
                item
                for item in template_items[0].childItems()
                if item.type() == 13
            ]
            self.assertEqual(
                len(rendered_svg_items),
                1,
            )
            self.assertFalse(rendered_svg_items[0].boundingRect().isEmpty())

    def test_projection_group_resources_do_not_become_visible_history_steps(self):
        from .TechDrawTestUtilities import createPageWithSVGTemplate

        page = createPageWithSVGTemplate(self.document)
        group = self.document.addObject("TechDraw::DrawProjGroup", "TimelineProjectionGroup")
        page.addView(group)
        front = group.addProjection("Front")
        self.document.recompute()

        timeline = _wait_until(
            lambda: Gui.getMainWindow().findChild(
                QtGui.QListWidget,
                "VibeCADFeatureTimelineItems",
            )
        )
        self.assertIsNotNone(timeline)
        self.assertTrue(
            _wait_until(
                lambda: page.Name in _visible_history_names(timeline)
                and group.Name in _visible_history_names(timeline)
                and front.Name not in _visible_history_names(timeline)
            ),
            _visible_history_names(timeline),
        )
        self.assertEqual(front.VibeCADTimelineRole, "resource")
        self.assertEqual(front.VibeCADTimelineOwner, group)
        self.assertEqual(
            front.getTypeIdOfProperty("VibeCADTimelineOwner"),
            "App::PropertyLinkHidden",
        )
        self.assertNotIn(group, front.OutList)

        group_name = group.Name
        front_name = front.Name
        self.document.UndoMode = True
        undo_before = self.document.UndoCount
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(group)
        _run_command_without_modal_warning("Std_Delete")
        self.assertIsNone(self.document.getObject(group_name))
        self.assertIsNone(self.document.getObject(front_name))
        self.assertIsNotNone(self.document.getObject(page.Name))
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        self.document.undo()
        Gui.updateGui()
        restored_group = self.document.getObject(group_name)
        restored_front = self.document.getObject(front_name)
        self.assertIsNotNone(restored_group)
        self.assertIsNotNone(restored_front)
        self.assertIs(restored_front.VibeCADTimelineOwner, restored_group)

        self.document.redo()
        Gui.updateGui()
        self.assertIsNone(self.document.getObject(group_name))
        self.assertIsNone(self.document.getObject(front_name))

    def test_page_deletes_its_owned_template_without_independent_warning(self):
        page = self.document.addObject(
            "TechDraw::DrawPage",
            "OwnedTemplatePage",
        )
        template = self.document.addObject(
            "TechDraw::DrawSVGTemplate",
            "OwnedPageTemplate",
        )
        page.Template = template
        page.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        page.VibeCADTimelineRole = "operation"
        template.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        template.addProperty(
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
            "Timeline",
        )
        template.VibeCADTimelineRole = "resource"
        template.VibeCADTimelineOwner = page
        self.document.recompute()

        page_name = page.Name
        template_name = template.Name
        self.document.UndoMode = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(page)
        _run_command_without_modal_warning("Std_Delete")
        self.assertIsNone(self.document.getObject(page_name))
        self.assertIsNone(self.document.getObject(template_name))

        self.document.undo()
        Gui.updateGui()
        restored_page = self.document.getObject(page_name)
        restored_template = self.document.getObject(template_name)
        self.assertIsNotNone(restored_page)
        self.assertIsNotNone(restored_template)
        self.assertIs(
            restored_template.VibeCADTimelineOwner,
            restored_page,
        )

    def test_multi_view_command_is_one_durable_history_operation(self):
        import Spreadsheet  # noqa: F401 - registers spreadsheet types

        from .TechDrawTestUtilities import createPageWithSVGTemplate

        page = createPageWithSVGTemplate(self.document)
        first_sheet = self.document.addObject(
            "Spreadsheet::Sheet",
            "DrawingSourceOne",
        )
        second_sheet = self.document.addObject(
            "Spreadsheet::Sheet",
            "DrawingSourceTwo",
        )
        first_sheet.set("A1", "First")
        second_sheet.set("A1", "Second")
        self.document.recompute()
        self.document.UndoMode = True

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(first_sheet)
        Gui.Selection.addSelection(second_sheet)
        self.assertTrue(Gui.isCommandActive("TechDraw_View"))
        _run_command_without_modal_warning("TechDraw_View")

        controller = self.document.getObject("DrawingViews")
        self.assertIsNotNone(controller)
        self.assertEqual(
            controller.VibeCADTimelineRole,
            "operation",
        )
        outputs = list(controller.Group)
        self.assertEqual(len(outputs), 2)
        self.assertEqual(
            {output.TypeId for output in outputs},
            {"TechDraw::DrawViewSpreadsheet"},
        )
        self.assertEqual(
            {output.Source.Name for output in outputs},
            {first_sheet.Name, second_sheet.Name},
        )
        for output in outputs:
            self.assertEqual(
                output.VibeCADTimelineRole,
                "resource",
            )
            self.assertIs(
                output.VibeCADTimelineOwner,
                controller,
            )
            self.assertNotIn(controller, output.OutList)

        timeline_widget = _wait_until(
            lambda: Gui.getMainWindow().findChild(
                QtGui.QListWidget,
                "VibeCADFeatureTimelineItems",
            )
        )
        self.assertIsNotNone(timeline_widget)
        self.assertTrue(
            _wait_until(
                lambda: controller.Name in _visible_history_names(timeline_widget)
                and all(
                    output.Name not in _visible_history_names(timeline_widget) for output in outputs
                )
            )
        )

        controller_name = controller.Name
        output_names = [output.Name for output in outputs]
        self.document.undo()
        Gui.updateGui()
        self.assertIsNone(self.document.getObject(controller_name))
        for name in output_names:
            self.assertIsNone(self.document.getObject(name))

        self.document.redo()
        Gui.updateGui()
        controller = self.document.getObject(controller_name)
        self.assertIsNotNone(controller)
        outputs = list(controller.Group)
        self.assertEqual(
            {output.Name for output in outputs},
            set(output_names),
        )
        for output in outputs:
            self.assertIs(
                output.VibeCADTimelineOwner,
                controller,
            )

        timeline = self.document.getObject("VibeCADTimeline")
        operations = list(timeline.Operations)
        operation_index = operations.index(controller)
        output_indices = [operations.index(output) for output in outputs]
        self.assertEqual(
            sorted(output_indices),
            list(
                range(
                    operation_index - len(outputs),
                    operation_index,
                )
            ),
        )
        self.assertEqual(int(timeline.Position), len(operations))
        previous = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        end = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        self.assertIsNotNone(previous)
        self.assertIsNotNone(end)
        previous.click()
        Gui.updateGui()
        self.assertEqual(
            int(timeline.Position),
            operation_index - len(outputs),
        )
        active_view_names = {view.Name for view in page.getAllActiveViews()}
        self.assertTrue({output.Name for output in outputs}.isdisjoint(active_view_names))

        end.click()
        Gui.updateGui()
        self.assertTrue(
            {output.Name for output in outputs}.issubset(
                {view.Name for view in page.getAllActiveViews()}
            )
        )

    def test_timeline_advertises_real_drawing_editors_but_not_fake_ones(self):
        from .TechDrawTestUtilities import createPageWithSVGTemplate

        page = createPageWithSVGTemplate(self.document)
        annotation = self.document.addObject(
            "TechDraw::DrawViewAnnotation",
            "EditableDrawingAnnotation",
        )
        annotation.Text = ["Editable drawing note"]
        page.addView(annotation)

        controller = self.document.addObject(
            "App::DocumentObjectGroup",
            "GroupedDrawingOutputs",
        )
        controller.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        controller.VibeCADTimelineRole = "operation"
        controller.Label = "Grouped Drawing Outputs"
        self.document.recompute()

        timeline = _wait_until(
            lambda: Gui.getMainWindow().findChild(
                QtGui.QListWidget,
                "VibeCADFeatureTimelineItems",
            )
        )
        self.assertIsNotNone(timeline)
        annotation_item = _wait_until(lambda: _history_item(timeline, annotation.Name))
        controller_item = _wait_until(lambda: _history_item(timeline, controller.Name))
        self.assertIsNotNone(annotation_item)
        self.assertIsNotNone(controller_item)

        self.assertIn(
            "VibeCADTimelineEdit",
            _timeline_context_action_names(
                timeline,
                annotation_item,
            ),
        )
        controller_item = _wait_until(lambda: _history_item(timeline, controller.Name))
        self.assertNotIn(
            "VibeCADTimelineEdit",
            _timeline_context_action_names(
                timeline,
                controller_item,
            ),
        )
        controller_item = _wait_until(lambda: _history_item(timeline, controller.Name))
        timeline.itemDoubleClicked.emit(controller_item)
        Gui.updateGui()
        self.assertIsNone(Gui.activeDocument().getInEdit())
