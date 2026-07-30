# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live GUI release gate for the VibeCAD ribbon and two-mode appearance."""

from __future__ import annotations

import os
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui, QtWidgets

_MODEL_COMPOSITES = {
    "PartDesign_CompSketches": (
        "PartDesign_NewSketch",
        "Sketcher_MapSketch",
        "Sketcher_EditSketch",
    ),
    "PartDesign_CompPrimitiveAdditive": (
        "PartDesign_AdditiveBox",
        "PartDesign_AdditiveCylinder",
        "PartDesign_AdditiveSphere",
        "PartDesign_AdditiveCone",
        "PartDesign_AdditiveEllipsoid",
        "PartDesign_AdditiveTorus",
        "PartDesign_AdditivePrism",
        "PartDesign_AdditiveWedge",
    ),
    "PartDesign_CompPrimitiveSubtractive": (
        "PartDesign_SubtractiveBox",
        "PartDesign_SubtractiveCylinder",
        "PartDesign_SubtractiveSphere",
        "PartDesign_SubtractiveCone",
        "PartDesign_SubtractiveEllipsoid",
        "PartDesign_SubtractiveTorus",
        "PartDesign_SubtractivePrism",
        "PartDesign_SubtractiveWedge",
    ),
    "Part_CompCompoundTools": (
        "Part_Compound",
        "Part_ExplodeCompound",
        "Part_CompoundFilter",
    ),
    "Part_CompJoinFeatures": (
        "Part_JoinConnect",
        "Part_JoinEmbed",
        "Part_JoinCutout",
    ),
    "Part_CompOffset": (
        "Part_Offset",
        "Part_Offset2D",
    ),
    "Part_CompSplitFeatures": (
        "Part_BooleanFragments",
        "Part_SliceApart",
        "Part_Slice",
        "Part_XOR",
    ),
}

_FEM_COMPOSITES = {
    "FEM_CompEmConstraints": (
        "FEM_ConstraintElectromagnetic",
        "FEM_ConstraintCurrentDensity",
        "FEM_ConstraintMagnetization",
        "FEM_ConstraintElectricChargeDensity",
    ),
    "FEM_CompEmEquations": (
        "FEM_EquationElectrostatic",
        "FEM_EquationElectricforce",
        "FEM_EquationMagnetodynamic",
        "FEM_EquationMagnetodynamic2D",
        "FEM_EquationStaticCurrent",
    ),
    "FEM_CompMechEquations": (
        "FEM_EquationElasticity",
        "FEM_EquationDeformation",
    ),
    "FEM_PostCreateFunctions": (
        "FEM_PostCreateFunctionPlane",
        "FEM_PostCreateFunctionSphere",
        "FEM_PostCreateFunctionCylinder",
        "FEM_PostCreateFunctionBox",
    ),
}

_COMPOSED_GROUP_COMMANDS = {
    "PartDesignWorkbench": {
        "SURFACE": (
            "Surface_Filling",
            "Surface_GeomFillSurface",
            "Surface_Sections",
            "Surface_ExtendFace",
            "Surface_CurveOnMesh",
            "Surface_BlendCurve",
        ),
    },
    "MeshWorkbench": {
        "POINTS": (
            "Points_Import",
            "Points_Export",
            "Points_Convert",
            "Points_Structure",
            "Points_Merge",
            "Points_PolyCut",
        ),
        "REBUILD": (
            "Reen_PoissonReconstruction",
            "Reen_ViewTriangulation",
        ),
        "SEGMENT": (
            "Reen_Segmentation",
            "Reen_SegmentationManual",
            "Reen_SegmentationFromComponents",
            "Reen_MeshBoundary",
        ),
        "APPROXIMATE": (
            "Reen_ApproxPlane",
            "Reen_ApproxCylinder",
            "Reen_ApproxSphere",
            "Reen_ApproxPolynomial",
            "Reen_ApproxSurface",
            "Reen_ApproxCurve",
        ),
    },
    "AssemblyWorkbench": {
        "ROBOT": (
            "Robot_Create",
            "Robot_AddToolShape",
            "Robot_SetDefaultOrientation",
            "Robot_SetDefaultValues",
        ),
        "TRAJECTORY": (
            "Robot_CreateTrajectory",
            "Robot_InsertWaypoint",
            "Robot_InsertWaypointPreselect",
            "Robot_Edge2Trac",
            "Robot_TrajectoryDressUp",
            "Robot_TrajectoryCompound",
        ),
        "MOTION": (
            "Robot_SetHomePos",
            "Robot_RestoreHomePos",
            "Robot_Simulate",
        ),
    },
    "CAMWorkbench": {
        "ROBOT": (
            "Robot_Edge2Trac",
            "Robot_TrajectoryDressUp",
            "Robot_TrajectoryCompound",
            "Robot_Simulate",
        ),
        "EXPORT": (
            "Robot_ExportKukaCompact",
            "Robot_ExportKukaFull",
        ),
    },
    "SpreadsheetWorkbench": {
        "SHEET": (
            "Spreadsheet_CreateSheet",
            "Spreadsheet_Import",
            "Spreadsheet_Export",
        ),
        "CELLS": (
            "Spreadsheet_MergeCells",
            "Spreadsheet_SplitCell",
            "Spreadsheet_CellProperties",
            "Spreadsheet_SetAlias",
        ),
        "ALIGN": (
            "Spreadsheet_AlignLeft",
            "Spreadsheet_AlignCenter",
            "Spreadsheet_AlignRight",
            "Spreadsheet_AlignTop",
            "Spreadsheet_AlignVCenter",
            "Spreadsheet_AlignBottom",
        ),
        "STYLE": (
            "Spreadsheet_StyleBold",
            "Spreadsheet_StyleItalic",
            "Spreadsheet_StyleUnderline",
        ),
    },
}

_MODEL_GROUP_COMMANDS = (
    (
        "VIEW",
        ("Std_ViewFitAll", "Std_ViewIsometric", "VibeCAD_ToggleGrid"),
    ),
    (
        "STRUCTURE",
        (
            "PartDesign_Body",
            "PartDesign_CompSketches",
            "Sketcher_ValidateSketch",
            "PartDesign_SubShapeBinder",
            "PartDesign_Clone",
        ),
    ),
    (
        "SOLIDS",
        (
            "PartDesign_Pad",
            "PartDesign_Revolution",
            "PartDesign_AdditiveLoft",
            "PartDesign_AdditivePipe",
            "PartDesign_AdditiveHelix",
            "PartDesign_CompPrimitiveAdditive",
            "PartDesign_Pocket",
            "PartDesign_Hole",
            "PartDesign_Groove",
            "PartDesign_SubtractiveLoft",
            "PartDesign_SubtractivePipe",
            "PartDesign_SubtractiveHelix",
            "PartDesign_CompPrimitiveSubtractive",
        ),
    ),
    (
        "FINISH",
        (
            "PartDesign_Fillet",
            "PartDesign_Chamfer",
            "PartDesign_Draft",
            "PartDesign_Thickness",
        ),
    ),
    (
        "TRANSFORM",
        (
            "PartDesign_Mirrored",
            "PartDesign_LinearPattern",
            "PartDesign_PolarPattern",
            "PartDesign_MultiTransform",
        ),
    ),
    (
        "GEOMETRY",
        (
            "Part_Tube",
            "Part_Primitives",
            "Part_Builder",
            "Part_Extrude",
            "Part_Revolve",
            "Part_Mirror",
            "Part_Scale",
            "Part_MakeFace",
            "Part_RuledSurface",
            "Part_Loft",
            "Part_Sweep",
            "Part_Section",
            "Part_CrossSections",
            "Part_CompOffset",
            "Part_ProjectionOnSurface",
        ),
    ),
    (
        "MODIFY",
        (
            "Part_CompCompoundTools",
            "Part_Boolean",
            "Part_Cut",
            "Part_Fuse",
            "Part_Common",
            "Part_CompJoinFeatures",
            "Part_CompSplitFeatures",
            "Part_Defeaturing",
        ),
    ),
    (
        "INSPECT",
        (
            "Std_Measure",
            "Std_MassProperties",
            "Inspection_VisualInspection",
            "Inspection_InspectElement",
            "Part_CheckGeometry",
        ),
    ),
    (
        "FASTENERS",
        (
            "VibeCAD_InsertStandardFastener",
            "VibeCAD_EditStandardFastener",
            "VibeCAD_CreateMatchingFastenerHole",
            "VibeCAD_AttachStandardFastener",
        ),
    ),
    (
        "SURFACE",
        (
            "Surface_Filling",
            "Surface_GeomFillSurface",
            "Surface_Sections",
            "Surface_ExtendFace",
            "Surface_CurveOnMesh",
            "Surface_BlendCurve",
        ),
    ),
)


def _expected_action_graph(command_ids):
    return tuple(
        (
            command_id,
            tuple((child_id, ()) for child_id in _MODEL_COMPOSITES.get(command_id, ())),
        )
        for command_id in command_ids
    )


def _assert_action_presentation(action):
    command_id = str(action.property("VibeCADCommandId") or "").strip()
    assert command_id, action.text()
    assert not bool(action.property("VibeCADUnavailable")), command_id
    assert not bool(action.property("VibeCADMissingIcon")), command_id
    assert str(action.toolTip() or "").strip(), command_id
    assert str(action.property("VibeCADAccessibleName") or "").strip(), command_id
    assert not action.icon().isNull(), command_id
    assert not action.icon().pixmap(24, 24).isNull(), command_id
    return command_id


def _menu_action_graph(menu):
    graph = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        command_id = _assert_action_presentation(action)
        children = (
            _menu_action_graph(action.menu()) if action.menu() is not None else ()
        )
        graph.append((command_id, children))
    return tuple(graph)


def _primary_action_graph(group):
    expanded = group.findChild(
        QtWidgets.QWidget,
        "VibeCADRibbonGroupExpanded",
    )
    assert expanded is not None
    graph = []
    for button in expanded.findChildren(QtWidgets.QToolButton):
        if not button.property("ribbonCommand"):
            continue
        action = button.defaultAction()
        assert action is not None
        command_id = _assert_action_presentation(action)
        assert str(button.property("VibeCADCommandId")) == command_id
        assert str(button.accessibleName() or "").strip(), command_id
        assert str(button.toolTip() or "").strip(), command_id
        children = (
            _menu_action_graph(button.menu()) if button.menu() is not None else ()
        )
        graph.append((command_id, children))
    return tuple(graph)


def _ordered_page_groups(page):
    groups = [
        child
        for child in page.children()
        if isinstance(child, QtWidgets.QFrame) and child.property("ribbonGroup")
    ]
    return sorted(groups, key=lambda group: int(group.property("ribbonOrder")))


def _flatten_action_graph(graph):
    command_ids = []
    for command_id, children in graph:
        command_ids.append(command_id)
        command_ids.extend(_flatten_action_graph(children))
    return command_ids


def _menu_action(menu, command_id):
    for action in menu.actions():
        if action.isSeparator():
            continue
        if str(action.property("VibeCADCommandId") or "") == command_id:
            return action
        if action.menu() is not None:
            child = _menu_action(action.menu(), command_id)
            if child is not None:
                return child
    return None


def _primary_action(page, command_id):
    for group in _ordered_page_groups(page):
        expanded = group.findChild(
            QtWidgets.QWidget,
            "VibeCADRibbonGroupExpanded",
        )
        if expanded is None:
            continue
        for button in expanded.findChildren(QtWidgets.QToolButton):
            action = button.defaultAction()
            if (
                button.property("ribbonCommand")
                and action is not None
                and str(action.property("VibeCADCommandId") or "") == command_id
            ):
                return action
    return None


def _page_menu_action(page, command_id):
    for group in _ordered_page_groups(page):
        button = group.findChild(
            QtWidgets.QToolButton,
            "VibeCADRibbonGroupMenu",
        )
        if button is None or button.menu() is None:
            continue
        action = _menu_action(button.menu(), command_id)
        if action is not None:
            return action
    return None


def _composite_wrappers(page, command_id):
    wrappers = []
    for group in _ordered_page_groups(page):
        for object_name in (
            "VibeCADRibbonGroupMenu",
            "VibeCADRibbonCollapsedGroup",
        ):
            button = group.findChild(QtWidgets.QToolButton, object_name)
            if button is None or button.menu() is None:
                continue
            action = _menu_action(button.menu(), command_id)
            if action is not None and action.menu() is not None:
                wrappers.append(action)
    return wrappers


def _composite_child_action(page, parent_id, child_id):
    wrappers = _composite_wrappers(page, parent_id)
    assert wrappers, parent_id
    for wrapper in wrappers:
        submenu = wrapper.menu()
        assert submenu is not None, parent_id
        action = _menu_action(submenu, child_id)
        if action is not None:
            return action
    return None


def _assert_synthetic_primitive_children(page):
    for parent_id in (
        "PartDesign_CompPrimitiveAdditive",
        "PartDesign_CompPrimitiveSubtractive",
    ):
        expected_children = _MODEL_COMPOSITES[parent_id]
        wrappers = _composite_wrappers(page, parent_id)
        assert len(wrappers) == 2, parent_id
        for wrapper in wrappers:
            children = [
                action
                for action in wrapper.menu().actions()
                if not action.isSeparator()
            ]
            assert (
                tuple(
                    str(action.property("VibeCADCommandId") or "")
                    for action in children
                )
                == expected_children
            )
            for index, (action, child_id) in enumerate(
                zip(children, expected_children, strict=True)
            ):
                assert bool(action.property("FreeCADCommandGroupSynthetic")), child_id
                assert (
                    str(action.property("FreeCADCommandGroupParentId") or "")
                    == parent_id
                )
                assert int(action.property("FreeCADCommandGroupChildIndex")) == index
                assert (
                    str(action.property("FreeCADCommandGroupChildId") or "") == child_id
                )
                assert bool(action.property("VibeCADSyntheticCommand")), child_id
                assert str(action.property("VibeCADParentCommandId") or "") == parent_id
                assert int(action.property("VibeCADCompositeChildIndex")) == index
                assert not bool(action.property("VibeCADUnavailable")), child_id


def _assert_fem_composite_children(page):
    for parent_id, expected_children in _FEM_COMPOSITES.items():
        wrappers = _composite_wrappers(page, parent_id)
        assert len(wrappers) == 2, parent_id
        for wrapper in wrappers:
            children = [
                action
                for action in wrapper.menu().actions()
                if not action.isSeparator()
            ]
            assert (
                tuple(
                    str(action.property("VibeCADCommandId") or "")
                    for action in children
                )
                == expected_children
            )
            for index, (action, child_id) in enumerate(
                zip(children, expected_children, strict=True)
            ):
                assert bool(action.property("FreeCADCommandGroupSynthetic")), child_id
                assert (
                    str(action.property("FreeCADCommandGroupParentId") or "")
                    == parent_id
                )
                assert int(action.property("FreeCADCommandGroupChildIndex")) == index
                assert (
                    str(action.property("FreeCADCommandGroupChildId") or "") == child_id
                )
                assert bool(action.property("VibeCADSyntheticCommand")), child_id
                assert str(action.property("VibeCADParentCommandId") or "") == parent_id
                assert int(action.property("VibeCADCompositeChildIndex")) == index
                assert not bool(action.property("VibeCADUnavailable")), child_id


def _assert_composite_wrapper_state(source, wrappers):
    assert source is not None
    assert wrappers
    for wrapper in wrappers:
        assert wrapper.isEnabled() == source.isEnabled()
        assert wrapper.isVisible() == source.isVisible()
        assert wrapper.text() == source.text()
        assert wrapper.icon().cacheKey() == source.icon().cacheKey()
        assert wrapper.toolTip() == source.toolTip()


def _assert_page_action_integrity(page):
    command_ids = []
    for group in _ordered_page_groups(page):
        group_menu = group.findChild(
            QtWidgets.QToolButton,
            "VibeCADRibbonGroupMenu",
        )
        collapsed = group.findChild(
            QtWidgets.QToolButton,
            "VibeCADRibbonCollapsedGroup",
        )
        assert group_menu is not None and group_menu.menu() is not None
        assert collapsed is not None and collapsed.menu() is not None
        assert group_menu.y() >= group_menu.parentWidget().height() // 2
        assert len(group_menu.text().split()) == 1
        assert not any(
            term.lower() in group_menu.text().lower()
            for term in (
                "Part Design",
                "PartDesign",
                "TechDraw",
                "Sketcher",
                "Workbench",
            )
        )
        group_graph = _menu_action_graph(group_menu.menu())
        assert _menu_action_graph(collapsed.menu()) == group_graph
        assert _primary_action_graph(group) == group_graph[:4]
        command_ids.extend(_flatten_action_graph(group_graph))
    assert len(command_ids) == len(set(command_ids)), command_ids


def _assert_composed_group_commands(page, workbench):
    groups_by_label = {}
    for group in _ordered_page_groups(page):
        group_menu = group.findChild(
            QtWidgets.QToolButton,
            "VibeCADRibbonGroupMenu",
        )
        assert group_menu is not None and group_menu.menu() is not None
        groups_by_label[group_menu.text()] = group

    for label, expected_commands in _COMPOSED_GROUP_COMMANDS.get(
        workbench,
        {},
    ).items():
        assert label in groups_by_label, (workbench, label, groups_by_label)
        actual_commands = _group_commands(groups_by_label[label])
        assert set(expected_commands).issubset(actual_commands), (
            workbench,
            label,
            actual_commands,
            expected_commands,
        )


def _assert_model_group_graphs(page):
    groups = _ordered_page_groups(page)
    actual_labels = []
    all_command_ids = []
    for group, (expected_label, command_ids) in zip(
        groups,
        _MODEL_GROUP_COMMANDS,
        strict=True,
    ):
        group_menu = group.findChild(
            QtWidgets.QToolButton,
            "VibeCADRibbonGroupMenu",
        )
        collapsed = group.findChild(
            QtWidgets.QToolButton,
            "VibeCADRibbonCollapsedGroup",
        )
        assert group_menu is not None and group_menu.menu() is not None
        assert collapsed is not None and collapsed.menu() is not None
        actual_labels.append(group_menu.text())
        expected_graph = _expected_action_graph(command_ids)
        menu_graph = _menu_action_graph(group_menu.menu())
        collapsed_graph = _menu_action_graph(collapsed.menu())
        primary_graph = _primary_action_graph(group)
        assert menu_graph == expected_graph, (
            expected_label,
            menu_graph,
            expected_graph,
        )
        assert collapsed_graph == expected_graph, (
            expected_label,
            collapsed_graph,
            expected_graph,
        )
        assert primary_graph == expected_graph[:4], (
            expected_label,
            primary_graph,
            expected_graph[:4],
        )
        all_command_ids.extend(command_ids)
    assert tuple(actual_labels) == tuple(
        label for label, _commands in _MODEL_GROUP_COMMANDS
    )
    assert len(all_command_ids) == len(set(all_command_ids))


def _assert_model_overflow_graph(page):
    expected_by_label = {
        label: _expected_action_graph(command_ids)
        for label, command_ids in _MODEL_GROUP_COMMANDS
    }
    hidden_groups = [
        group for group in _ordered_page_groups(page) if not group.isVisible()
    ]
    assert hidden_groups
    overflow = page.findChild(
        QtWidgets.QToolButton,
        "VibeCADRibbonPageMore",
    )
    assert overflow is not None and overflow.menu() is not None
    overflow_actions = [
        action for action in overflow.menu().actions() if not action.isSeparator()
    ]
    assert len(overflow_actions) == len(hidden_groups)
    for group, action in zip(hidden_groups, overflow_actions, strict=True):
        group_menu = group.findChild(
            QtWidgets.QToolButton,
            "VibeCADRibbonGroupMenu",
        )
        assert group_menu is not None
        label = group_menu.text()
        assert action.text().upper() == label
        assert action.menu() is not None
        assert _menu_action_graph(action.menu()) == expected_by_label[label]


def _assert_model_width_reachability(page, width):
    expected_by_label = {
        label: _expected_action_graph(command_ids)
        for label, command_ids in _MODEL_GROUP_COMMANDS
    }
    groups = _ordered_page_groups(page)
    assert len(groups) == len(_MODEL_GROUP_COMMANDS), width
    overflow = page.findChild(
        QtWidgets.QToolButton,
        "VibeCADRibbonPageMore",
    )
    assert overflow is not None and overflow.menu() is not None, width
    overflow_actions = {
        action.text().upper(): action
        for action in overflow.menu().actions()
        if not action.isSeparator()
    }
    canonical_ids = []

    for group, (label, _command_ids) in zip(
        groups,
        _MODEL_GROUP_COMMANDS,
        strict=True,
    ):
        expanded = group.findChild(
            QtWidgets.QWidget,
            "VibeCADRibbonGroupExpanded",
        )
        group_menu = group.findChild(
            QtWidgets.QToolButton,
            "VibeCADRibbonGroupMenu",
        )
        collapsed = group.findChild(
            QtWidgets.QToolButton,
            "VibeCADRibbonCollapsedGroup",
        )
        assert expanded is not None, (width, label)
        assert group_menu is not None and group_menu.menu() is not None, (
            width,
            label,
        )
        assert collapsed is not None and collapsed.menu() is not None, (
            width,
            label,
        )

        if group.isVisible():
            _assert_visible_inside(group, page)
            expanded_visible = expanded.isVisibleTo(page)
            collapsed_visible = collapsed.isVisibleTo(page)
            assert expanded_visible != collapsed_visible, (
                width,
                label,
                expanded_visible,
                collapsed_visible,
            )
            route = group_menu if expanded_visible else collapsed
            _assert_visible_inside(route, page)
            assert route.width() >= route.sizeHint().width(), (
                width,
                label,
                route.width(),
                route.sizeHint().width(),
            )
            graph = _menu_action_graph(route.menu())
            assert label not in overflow_actions, (width, label)
        else:
            assert not expanded.isVisibleTo(page), (width, label)
            assert not collapsed.isVisibleTo(page), (width, label)
            overflow_action = overflow_actions.get(label)
            assert overflow_action is not None, (width, label)
            assert overflow_action.menu() is not None, (width, label)
            graph = _menu_action_graph(overflow_action.menu())
        assert graph == expected_by_label[label], (width, label, graph)
        canonical_ids.extend(_flatten_action_graph(graph))

    hidden_groups = [group for group in groups if not group.isVisible()]
    assert overflow.isVisible() == bool(hidden_groups), width
    if hidden_groups:
        _assert_visible_inside(overflow, page)
        assert overflow.width() >= overflow.sizeHint().width(), (
            width,
            overflow.width(),
            overflow.sizeHint().width(),
        )
        assert len(overflow_actions) == len(hidden_groups), width
    else:
        assert not overflow_actions, width

    expected_ids = [
        command_id
        for label, _command_ids in _MODEL_GROUP_COMMANDS
        for command_id in _flatten_action_graph(expected_by_label[label])
    ]
    assert canonical_ids == expected_ids, width
    assert len(canonical_ids) == len(set(canonical_ids)), (
        width,
        canonical_ids,
    )


def _exercise_model_overflow_menu(page, width):
    overflow = page.findChild(
        QtWidgets.QToolButton,
        "VibeCADRibbonPageMore",
    )
    assert overflow is not None and overflow.isVisible(), width
    menu = overflow.menu()
    assert menu is not None
    menu.popup(overflow.mapToGlobal(overflow.rect().bottomLeft()))
    QtWidgets.QApplication.processEvents()
    assert menu.isVisible(), width
    assert QtWidgets.QApplication.activePopupWidget() is menu, width

    actions = [action for action in menu.actions() if not action.isSeparator()]
    assert actions, width
    for action in actions:
        assert action.isVisible(), (width, action.text())
        submenu = action.menu()
        assert submenu is not None, (width, action.text())
        submenu.popup(menu.mapToGlobal(menu.rect().topRight()))
        QtWidgets.QApplication.processEvents()
        assert submenu.isVisible(), (width, action.text())
        assert [
            child
            for child in submenu.actions()
            if not child.isSeparator() and child.isVisible()
        ], (width, action.text())
        submenu.hide()
        QtWidgets.QApplication.processEvents()
    menu.hide()
    QtWidgets.QApplication.processEvents()
    assert not menu.isVisible(), width


def _assert_application_strip_actions(main_window):
    strip = main_window.findChild(
        QtWidgets.QWidget,
        "VibeCADApplicationStrip",
    )
    assert strip is not None
    expected_commands = {
        "VibeCADRibbonOpen": "Std_Open",
        "VibeCADRibbonSave": "Std_Save",
        "VibeCADRibbonUndo": "Std_Undo",
        "VibeCADRibbonRedo": "Std_Redo",
        "VibeCADRibbonNew": "Std_New",
        "VibeCADRibbonAssistant": "VibeCAD_OpenAssistant",
        "VibeCADRibbonSettings": "VibeCAD_OpenPreferences",
    }
    command_ids = []
    actual_commands = {}
    for button in strip.findChildren(QtWidgets.QToolButton):
        if button.objectName() not in expected_commands:
            continue
        action = button.defaultAction()
        assert action is not None, button.objectName()
        command_id = _assert_action_presentation(action)
        assert str(button.property("VibeCADCommandId") or "") == command_id
        assert not bool(button.property("VibeCADUnavailable")), command_id
        assert not bool(button.property("VibeCADMissingIcon")), command_id
        assert str(button.toolTip() or "").strip(), command_id
        assert str(button.accessibleName() or "").strip(), command_id
        assert not button.icon().isNull(), command_id
        assert not button.icon().pixmap(20, 20).isNull(), command_id
        command_ids.append(command_id)
        actual_commands[button.objectName()] = command_id
    assert actual_commands == expected_commands
    assert len(command_ids) == len(set(command_ids)), command_ids

    for object_name in ("VibeCADRibbonUndo", "VibeCADRibbonRedo"):
        button = strip.findChild(QtWidgets.QToolButton, object_name)
        assert button is not None and button.defaultAction() is not None
        tool_action = button.defaultAction()
        assert tool_action.menu() is not None
        assert button.menu() is tool_action.menu()
        assert button.popupMode() == QtWidgets.QToolButton.MenuButtonPopup
        action_owner = tool_action.parent()
        assert action_owner is not None
        source_actions = [
            action
            for action in action_owner.findChildren(QtGui.QAction)
            if action is not tool_action
            and action.objectName() == tool_action.objectName()
            and action.parent() == action_owner
        ]
        assert len(source_actions) == 1, object_name
        source_action = source_actions[0]
        assert source_action.menu() is None
        assert tool_action.text() == source_action.text()
        assert tool_action.icon().cacheKey() == source_action.icon().cacheKey()
        assert tool_action.isEnabled() == source_action.isEnabled()
        assert tool_action.isVisible() == source_action.isVisible()
        assert tool_action.isCheckable() == source_action.isCheckable()
        assert tool_action.isChecked() == source_action.isChecked()

    for object_name in (
        "VibeCADAppButton",
        "VibeCADRibbonSearch",
        "VibeCADThemeToggle",
    ):
        button = strip.findChild(QtWidgets.QToolButton, object_name)
        assert button is not None
        assert str(button.toolTip() or "").strip(), object_name
        assert str(button.accessibleName() or "").strip(), object_name
        assert not button.icon().isNull(), object_name
        assert not button.icon().pixmap(20, 20).isNull(), object_name


def _visible_main_window_toolbars(main_window):
    return [
        toolbar
        for toolbar in main_window.findChildren(QtWidgets.QToolBar)
        if toolbar.isVisible()
        and (
            main_window.toolBarArea(toolbar) != QtCore.Qt.NoToolBarArea
            or toolbar.parentWidget() is main_window
        )
    ]


def _process_events():
    application = QtWidgets.QApplication.instance()
    application.processEvents()
    event_loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(100, event_loop.quit)
    event_loop.exec()
    application.processEvents()


def _save_window_screenshot(main_window, path):
    screen = main_window.screen() or QtWidgets.QApplication.primaryScreen()
    window_geometry = main_window.frameGeometry()
    screen_geometry = screen.virtualGeometry()
    assert screen_geometry.contains(window_geometry), (
        "The screenshot display must contain the complete VibeCAD window",
        screen_geometry.getRect(),
        window_geometry.getRect(),
    )
    assert screen.grabWindow(main_window.winId()).save(path)


def _key_click(widget, key):
    application = QtWidgets.QApplication.instance()
    application.sendEvent(
        widget,
        QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, QtCore.Qt.NoModifier),
    )
    application.sendEvent(
        widget,
        QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, key, QtCore.Qt.NoModifier),
    )


def _assert_visible_inside(widget, ancestor):
    assert widget is not None and widget.isVisible()
    top_left = widget.mapTo(ancestor, QtCore.QPoint(0, 0))
    bottom_right = top_left + QtCore.QPoint(
        max(0, widget.width() - 1), max(0, widget.height() - 1)
    )
    assert ancestor.rect().contains(top_left)
    assert ancestor.rect().contains(bottom_right)


def _group_commands(group):
    group_menu = group.findChild(QtWidgets.QToolButton, "VibeCADRibbonGroupMenu")
    assert group_menu is not None and group_menu.menu() is not None
    return {
        str(action.property("VibeCADCommandId"))
        for action in group_menu.menu().actions()
        if action.property("VibeCADCommandId")
    }


def _page_group_labels(page):
    assert page is not None
    assert page.objectName() == "VibeCADRibbonPage"
    labels = []
    for widget in _ordered_page_groups(page):
        group_menu = widget.findChild(QtWidgets.QToolButton, "VibeCADRibbonGroupMenu")
        assert group_menu is not None
        labels.append(group_menu.text())
    return labels


def _ribbon_page(main_window):
    root = main_window.findChild(QtWidgets.QWidget, "VibeCADRibbon")
    assert root is not None
    pages = root.findChildren(
        QtWidgets.QWidget,
        "VibeCADRibbonPage",
        QtCore.Qt.FindDirectChildrenOnly,
    )
    assert len(pages) == 1
    page = pages[0]
    for widget in _ordered_page_groups(page):
        assert widget.parentWidget() is page
        assert (
            widget.findChild(
                QtWidgets.QToolButton,
                "VibeCADRibbonGroupMenu",
            )
            is not None
        )
    return page


def _select_ribbon_workbench(main_window, tabs, workbench):
    index = next(
        index for index in range(tabs.count()) if str(tabs.tabData(index)) == workbench
    )
    tabs.setCurrentIndex(index)
    _process_events()
    assert Gui.activeWorkbench().name() == workbench
    return _ribbon_page(main_window)


def _run():
    application = QtWidgets.QApplication.instance()
    main_window = Gui.getMainWindow()
    document = None
    tree_document = None
    secondary_document = None
    secondary_name = None
    initial_mode = None
    sentinel = App.ParamGet(
        "User parameter:BaseApp/Preferences/Mod/Sketcher/VibeCADRibbonSmoke"
    )
    retired_theme_customization = App.ParamGet(
        "User parameter:BaseApp/Preferences/Themes"
    )

    try:
        print("VIBECAD_RIBBON_STAGE startup", flush=True)
        main_window.resize(1440, 900)
        main_window.show()
        _process_events()

        if os.environ.get("VIBECAD_VERIFY_SAVED_COMBINED_BROWSER"):
            assert main_window.findChild(QtWidgets.QDockWidget, "Std_TreeView") is None
            assert (
                main_window.findChild(QtWidgets.QDockWidget, "Std_PropertyView") is None
            )
            assert (
                main_window.findChild(QtWidgets.QDockWidget, "Std_ComboView")
                is not None
            )
            print(
                "VIBECAD_SAVED_BROWSER_LAYOUT_OK mode=Combined",
                flush=True,
            )
            exit_code = 0
            return

        ribbon = main_window.findChild(QtWidgets.QToolBar, "VibeCADRibbonToolBar")
        root = main_window.findChild(QtWidgets.QWidget, "VibeCADRibbon")
        tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
        document_tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADDocumentTabs")
        source_document_tabs = main_window.findChild(QtWidgets.QTabBar, "mdiAreaTabBar")
        feature_timeline = main_window.findChild(
            QtWidgets.QWidget, "VibeCADFeatureTimeline"
        )
        timeline_items = main_window.findChild(
            QtWidgets.QListWidget, "VibeCADFeatureTimelineItems"
        )
        theme_button = main_window.findChild(
            QtWidgets.QToolButton, "VibeCADThemeToggle"
        )
        search_button = main_window.findChild(
            QtWidgets.QToolButton, "VibeCADRibbonSearch"
        )
        new_document_button = main_window.findChild(
            QtWidgets.QToolButton, "VibeCADRibbonNew"
        )
        search = main_window.findChild(QtWidgets.QLineEdit, "VibeCADCommandSearch")
        assistant_button = main_window.findChild(
            QtWidgets.QToolButton, "VibeCADRibbonAssistant"
        )
        settings_button = main_window.findChild(
            QtWidgets.QToolButton, "VibeCADRibbonSettings"
        )
        assert ribbon is not None and ribbon.isVisible()
        assert root is not None and root.isVisible()
        assert tabs is not None
        assert document_tabs is not None and document_tabs.isVisible()
        assert source_document_tabs is not None
        assert not source_document_tabs.isVisible()
        assert source_document_tabs.minimumHeight() == 0
        assert source_document_tabs.maximumHeight() == 0
        assert feature_timeline is not None and feature_timeline.isVisible()
        assert feature_timeline.height() == 56
        assert timeline_items is not None and timeline_items.isVisible()
        assert (
            document_tabs.mapTo(root, QtCore.QPoint()).y()
            < tabs.mapTo(root, QtCore.QPoint()).y()
        )
        assert document_tabs.tabsClosable()
        assert document_tabs.isMovable()
        assert theme_button is not None
        assert search_button is not None and search_button.isVisible()
        assert new_document_button is not None and new_document_button.isVisible()
        assert search is not None and search.completer() is not None
        _assert_visible_inside(assistant_button, root)
        _assert_visible_inside(settings_button, root)
        _assert_visible_inside(document_tabs, root)
        _assert_visible_inside(search_button, root)
        _assert_visible_inside(new_document_button, root)
        assert assistant_button.toolButtonStyle() == QtCore.Qt.ToolButtonIconOnly
        assert settings_button.toolButtonStyle() == QtCore.Qt.ToolButtonIconOnly
        assert (
            assistant_button.defaultAction().property("VibeCADCommandId")
            == "VibeCAD_OpenAssistant"
        )
        assert (
            settings_button.defaultAction().property("VibeCADCommandId")
            == "VibeCAD_OpenPreferences"
        )
        assert not main_window.menuBar().isVisible()
        assert _visible_main_window_toolbars(main_window) == [ribbon]
        _assert_application_strip_actions(main_window)
        print("VIBECAD_RIBBON_STAGE application-strip", flush=True)

        expected_tabs = [
            "Model",
            "Assemble",
            "Mesh",
            "Analyze",
            "Manufacture",
            "Drawing",
            "Parameters",
        ]
        assert [tabs.tabText(index) for index in range(tabs.count())] == (expected_tabs)
        tabs.setCurrentIndex(0)
        _process_events()
        structure_group = main_window.findChild(
            QtWidgets.QFrame, "VibeCADRibbonGroup_Structure"
        )
        assert structure_group is not None and structure_group.isVisible()
        structure_commands = {
            str(button.defaultAction().property("VibeCADCommandId"))
            for button in structure_group.findChildren(QtWidgets.QToolButton)
            if button.property("ribbonCommand") and button.defaultAction() is not None
        }
        assert "PartDesign_CompSketches" in structure_commands
        sketch_tools = next(
            button
            for button in structure_group.findChildren(QtWidgets.QToolButton)
            if button.property("ribbonCommand")
            and button.defaultAction() is not None
            and button.defaultAction().property("VibeCADCommandId")
            == "PartDesign_CompSketches"
        )
        assert sketch_tools.menu() is not None
        sketch_tool_labels = {
            action.text().replace("&", "") for action in sketch_tools.menu().actions()
        }
        assert {"New Sketch", "Attach Sketch", "Edit Sketch"}.issubset(
            sketch_tool_labels
        )
        model_page = _ribbon_page(main_window)
        _assert_model_group_graphs(model_page)
        _assert_synthetic_primitive_children(model_page)
        del (
            model_page,
            sketch_tools,
            structure_group,
        )
        print("VIBECAD_RIBBON_STAGE model-graph", flush=True)

        Gui.activateWorkbench("SketcherWorkbench")
        _process_events()
        assert Gui.activeWorkbench().name() == "SketcherWorkbench"
        assert [
            tabs.tabText(index) for index in range(tabs.count())
        ] == expected_tabs + ["Sketch"]
        assert tabs.tabText(tabs.currentIndex()) == "Sketch"
        assert all(tabs.isTabEnabled(index) for index in range(tabs.count()))
        sketch_setup_page = _ribbon_page(main_window)
        assert _page_group_labels(sketch_setup_page) == [
            "VIEW",
            "SKETCH",
            "INSPECT",
        ]
        del sketch_setup_page
        print("VIBECAD_RIBBON_STAGE sketch-setup", flush=True)

        tabs.setCurrentIndex(0)
        _process_events()
        assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
        assert tabs.tabText(tabs.currentIndex()) == "Model"
        assert [tabs.tabText(index) for index in range(tabs.count())] == expected_tabs

        theme_selector = main_window.findChild(QtWidgets.QWidget, "ThemeSelectorWidget")
        if theme_selector is not None:
            assert sorted(
                button.text()
                for button in theme_selector.findChildren(QtWidgets.QToolButton)
            ) == ["Dark", "Light"]
            assert (
                "more themes"
                not in " ".join(
                    label.text()
                    for label in theme_selector.findChildren(QtWidgets.QLabel)
                ).lower()
            )

        completion_model = search.completer().model()
        completion_values = [
            str(
                completion_model.data(
                    completion_model.index(row, 0), QtCore.Qt.DisplayRole
                )
            )
            for row in range(completion_model.rowCount())
        ]
        assert any("Std_New" in value for value in completion_values)
        assert any("PartDesign_Body" in value for value in completion_values)

        theme_parameters = App.ParamGet("User parameter:BaseApp/Preferences/MainWindow")
        initial_mode = theme_parameters.GetString("AppearanceMode", "Dark")
        sentinel.SetInt("UnrelatedPreference", 8472)
        retired_theme_customization.SetUnsigned("ThemeAccentColor1", 0xFF00FFFF)
        retired_theme_customization.SetUnsigned("ThemeAccentColor2", 0x00FFFFFF)
        retired_theme_customization.SetUnsigned("ThemeAccentColor3", 0x0000FFFF)
        theme_button.click()
        _process_events()
        switched_mode = theme_parameters.GetString("AppearanceMode", "")
        assert switched_mode in {"Light", "Dark"}
        assert switched_mode != initial_mode
        assert theme_parameters.GetString("Theme", "") == switched_mode
        assert theme_parameters.GetString("StyleSheet", "") == (
            "VibeLight.qss" if switched_mode == "Light" else "VibeDark.qss"
        )
        if theme_selector is not None:
            assert [
                button.text()
                for button in theme_selector.findChildren(QtWidgets.QToolButton)
                if button.isChecked()
            ] == [switched_mode]
        assert sentinel.GetInt("UnrelatedPreference", 0) == 8472
        assert not any(
            name.startswith("ThemeAccentColor")
            for name in retired_theme_customization.GetUnsigneds()
        )
        switched_screenshot = os.environ.get("VIBECAD_RIBBON_SWITCHED_SCREENSHOT")
        if switched_screenshot:
            _save_window_screenshot(main_window, switched_screenshot)
        theme_button.click()
        _process_events()
        assert theme_parameters.GetString("AppearanceMode", "") == initial_mode
        print("VIBECAD_RIBBON_STAGE themes", flush=True)

        assert App.ActiveDocument is None
        tree_document = App.newDocument("VibeCADTreeVisibility")
        tree_document.addObject("Part::Box", "TreeVisibilityBox")
        tree_document.recompute()
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()
        _process_events()
        separate_tree_dock = main_window.findChild(
            QtWidgets.QDockWidget,
            "Std_TreeView",
        )
        assert separate_tree_dock is not None
        if separate_tree_dock is not None:
            tree_toggle = separate_tree_dock.toggleViewAction()
            original_tree_toggle_state = tree_toggle.isChecked()
            assert str(tree_toggle.data()) == "Std_TreeView"
            tree_visibility_preferences = App.ParamGet(
                "User parameter:BaseApp/MainWindow/DockWindows"
            )

            tree_splitter = separate_tree_dock.parentWidget()
            while tree_splitter is not None and not isinstance(
                tree_splitter,
                QtWidgets.QSplitter,
            ):
                tree_splitter = tree_splitter.parentWidget()
            tree_splitter_index = (
                tree_splitter.indexOf(separate_tree_dock)
                if tree_splitter is not None
                else -1
            )

            def assert_tree_rendered(expected_state):
                for _ in range(3):
                    _process_events()
                splitter_sizes = (
                    tree_splitter.sizes() if tree_splitter is not None else []
                )
                tree_state = {
                    "expected": expected_state,
                    "checked": tree_toggle.isChecked(),
                    "preference": tree_visibility_preferences.GetBool(
                        "Std_TreeView",
                        not expected_state,
                    ),
                    "visible": separate_tree_dock.isVisible(),
                    "hidden": separate_tree_dock.isHidden(),
                    "visible_region_empty": (
                        separate_tree_dock.visibleRegion().isEmpty()
                    ),
                    "splitter_sizes": splitter_sizes,
                    "splitter_visible": (
                        tree_splitter.isVisible() if tree_splitter is not None else None
                    ),
                }
                assert tree_toggle.isChecked() == expected_state, tree_state
                assert (
                    tree_visibility_preferences.GetBool(
                        "Std_TreeView",
                        not expected_state,
                    )
                    == expected_state
                ), tree_state
                assert (
                    separate_tree_dock.visibleRegion().isEmpty() != expected_state
                ), tree_state
                assert separate_tree_dock.isVisible() == expected_state, tree_state
                if tree_splitter_index >= 0:
                    assert (
                        splitter_sizes[tree_splitter_index] > 0
                    ) == expected_state, tree_state
                    assert tree_splitter.isVisible() == expected_state, tree_state
                if expected_state:
                    dock_rect = QtCore.QRect(
                        separate_tree_dock.mapToGlobal(QtCore.QPoint()),
                        separate_tree_dock.size(),
                    )
                    window_rect = QtCore.QRect(
                        main_window.mapToGlobal(QtCore.QPoint()),
                        main_window.size(),
                    )
                    assert dock_rect.intersects(window_rect)

            def assert_tree_state_survives_switches(expected_state):
                for workbench in (
                    "PartDesignWorkbench",
                    "MeshWorkbench",
                    "AssemblyWorkbench",
                    "PartDesignWorkbench",
                ):
                    _select_ribbon_workbench(
                        main_window,
                        tabs,
                        workbench,
                    )
                    assert (
                        main_window.findChild(
                            QtWidgets.QDockWidget,
                            "Std_TreeView",
                        )
                        is separate_tree_dock
                    )
                    assert_tree_rendered(expected_state)

            def assert_tree_state_survives_theme_refresh(expected_state):
                starting_mode = theme_parameters.GetString(
                    "AppearanceMode",
                    "",
                )
                theme_button.click()
                assert_tree_rendered(expected_state)
                assert (
                    theme_parameters.GetString(
                        "AppearanceMode",
                        "",
                    )
                    != starting_mode
                )
                theme_button.click()
                assert_tree_rendered(expected_state)
                assert (
                    theme_parameters.GetString(
                        "AppearanceMode",
                        "",
                    )
                    == starting_mode
                )

            if not tree_toggle.isChecked():
                tree_toggle.trigger()
                _process_events()
            assert_tree_rendered(True)
            assert_tree_state_survives_theme_refresh(True)

            assistant_button.click()
            _process_events()
            assistant_dock = main_window.findChild(
                QtWidgets.QDockWidget,
                "VibeCADAssistantPanel",
            )
            assert assistant_dock is not None
            assistant_dock.widget().setFocus(QtCore.Qt.OtherFocusReason)
            _process_events()
            assert_tree_rendered(True)
            assert_tree_state_survives_switches(True)

            tree_toggle.trigger()
            _process_events()
            assistant_dock.widget().setFocus(QtCore.Qt.OtherFocusReason)
            assert_tree_rendered(False)
            assert_tree_state_survives_theme_refresh(False)
            assert_tree_state_survives_switches(False)

            tree_toggle.trigger()
            _process_events()
            assert_tree_rendered(True)
            assert_tree_state_survives_switches(True)

            if not original_tree_toggle_state:
                tree_toggle.trigger()
                _process_events()
                assert_tree_rendered(False)

        App.closeDocument(tree_document.Name)
        tree_document = None
        _process_events()
        assert App.ActiveDocument is None

        for index in range(tabs.count()):
            print(f"VIBECAD_RIBBON_STAGE domain-{index}", flush=True)
            tabs.setCurrentIndex(index)
            _process_events()
            workbench = str(tabs.tabData(index))
            assert Gui.activeWorkbench().name() == workbench
            assert main_window.findChildren(QtWidgets.QFrame, "VibeCADRibbonGroup_View")
            inspect_group = main_window.findChild(
                QtWidgets.QFrame, "VibeCADRibbonGroup_Inspect"
            )
            assert inspect_group is not None
            inspect_commands = _group_commands(inspect_group)
            assert inspect_commands == {
                "Std_Measure",
                "Std_MassProperties",
                "Inspection_VisualInspection",
                "Inspection_InspectElement",
                "Part_CheckGeometry",
            }, inspect_commands
            page = _ribbon_page(main_window)
            _assert_page_action_integrity(page)
            _assert_composed_group_commands(page, workbench)
            if workbench == "AssemblyWorkbench":
                assert not _page_menu_action(
                    page,
                    "Assembly_CreateBom",
                ).isEnabled()
            elif workbench == "MeshWorkbench":
                assert not _page_menu_action(
                    page,
                    "Mesh_FromPartShape",
                ).isEnabled()
            elif workbench == "FemWorkbench":
                _assert_fem_composite_children(page)
                assert not _page_menu_action(
                    page,
                    "FEM_PostFilterLinearizedStresses",
                ).isEnabled()
                assert not _page_menu_action(
                    page,
                    "FEM_PostCreateFunctions",
                ).isEnabled()
            page_groups = [
                group
                for group in page.findChildren(QtWidgets.QFrame)
                if group.property("ribbonGroup")
            ]
            visible_page_groups = [group for group in page_groups if group.isVisible()]
            hidden_page_groups = [
                group for group in page_groups if not group.isVisible()
            ]
            for group in visible_page_groups:
                _assert_visible_inside(group, page)
            page_overflow = page.findChild(
                QtWidgets.QToolButton, "VibeCADRibbonPageMore"
            )
            assert page_overflow is not None
            assert page_overflow.isVisible() == bool(hidden_page_groups)
            if page_overflow.isVisible():
                _assert_visible_inside(page_overflow, page)
            if workbench == "MeshWorkbench":
                mesh_group_labels = _page_group_labels(page)
                assert mesh_group_labels == [
                    "VIEW",
                    "TOOLS",
                    "CONVERT",
                    "MODIFY",
                    "BOOLEAN",
                    "CUT",
                    "SEGMENT",
                    "ANALYZE",
                    "POINTS",
                    "REBUILD",
                    "APPROXIMATE",
                    "INSPECT",
                ], mesh_group_labels
                tools_group = main_window.findChild(
                    QtWidgets.QFrame, "VibeCADRibbonGroup_Tools"
                )
                assert {
                    "Mesh_Import",
                    "Mesh_Export",
                    "Mesh_BuildRegularSolid",
                }.issubset(_group_commands(tools_group))
                convert_group = main_window.findChild(
                    QtWidgets.QFrame, "VibeCADRibbonGroup_Convert"
                )
                assert {
                    "Mesh_FromPartShape",
                    "MeshPart_ShapeFromMesh",
                    "MeshPart_CurveOnMesh",
                }.issubset(_group_commands(convert_group))
                conversion_actions = {
                    str(action.property("VibeCADCommandId")): action
                    for action in convert_group.findChild(
                        QtWidgets.QToolButton,
                        "VibeCADRibbonGroupMenu",
                    )
                    .menu()
                    .actions()
                    if action.property("VibeCADCommandId")
                }
                for command_name in (
                    "Mesh_FromPartShape",
                    "MeshPart_ShapeFromMesh",
                    "MeshPart_CurveOnMesh",
                ):
                    assert not conversion_actions[command_name].icon().isNull()
                mesh_screenshot_path = os.environ.get("VIBECAD_RIBBON_MESH_SCREENSHOT")
                if mesh_screenshot_path:
                    _save_window_screenshot(main_window, mesh_screenshot_path)
                del (
                    conversion_actions,
                    convert_group,
                    mesh_group_labels,
                    tools_group,
                )
            assert _visible_main_window_toolbars(main_window) == [ribbon]
            del (
                group,
                hidden_page_groups,
                inspect_group,
                page,
                page_groups,
                page_overflow,
                visible_page_groups,
            )

        tabs.setCurrentIndex(0)
        _process_events()
        assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
        width_screenshot_directory = os.environ.get(
            "VIBECAD_RIBBON_WIDTH_SCREENSHOT_DIR"
        )
        if width_screenshot_directory:
            os.makedirs(width_screenshot_directory, exist_ok=True)
        for requested_width in (1440, 1024, 800):
            main_window.resize(requested_width, 760)
            _process_events()
            assert main_window.width() == requested_width, (
                requested_width,
                main_window.width(),
            )
            page = _ribbon_page(main_window)
            _assert_visible_inside(page, root)
            _assert_model_width_reachability(page, requested_width)
            if any(not group.isVisible() for group in _ordered_page_groups(page)):
                _exercise_model_overflow_menu(page, requested_width)
            if width_screenshot_directory:
                _save_window_screenshot(
                    main_window,
                    os.path.join(
                        width_screenshot_directory,
                        f"model-{requested_width}.png",
                    ),
                )
            print(
                "VIBECAD_RIBBON_STAGE "
                f"model-width-{requested_width} "
                f"page={page.width()}",
                flush=True,
            )
        del page

        main_window.resize(850, 760)
        _process_events()
        assert assistant_button.toolButtonStyle() == QtCore.Qt.ToolButtonIconOnly
        assert settings_button.toolButtonStyle() == QtCore.Qt.ToolButtonIconOnly
        assert not search.isVisible()
        _assert_visible_inside(search_button, root)
        _assert_visible_inside(document_tabs, root)
        _assert_visible_inside(assistant_button, root)
        _assert_visible_inside(settings_button, root)
        assert not source_document_tabs.isVisible()
        saw_collapsed_group = False
        for index in range(tabs.count()):
            print(f"VIBECAD_RIBBON_STAGE compact-{index}", flush=True)
            tabs.setCurrentIndex(index)
            _process_events()
            page = _ribbon_page(main_window)
            _assert_page_action_integrity(page)
            groups = [
                group
                for group in page.findChildren(QtWidgets.QFrame)
                if group.property("ribbonGroup")
            ]
            visible_groups = [group for group in groups if group.isVisible()]
            hidden_groups = [group for group in groups if not group.isVisible()]
            saw_collapsed_group = saw_collapsed_group or any(
                bool(group.property("collapsed")) for group in visible_groups
            )
            for group in visible_groups:
                _assert_visible_inside(group, page)
            overflow = page.findChild(QtWidgets.QToolButton, "VibeCADRibbonPageMore")
            assert overflow is not None
            assert overflow.isVisible() == bool(hidden_groups)
            if hidden_groups:
                assert len(overflow.menu().actions()) == len(hidden_groups)
                _assert_visible_inside(overflow, page)
            if str(tabs.tabData(index)) == "PartDesignWorkbench" and hidden_groups:
                _assert_model_overflow_graph(page)
            del (
                group,
                groups,
                hidden_groups,
                overflow,
                page,
                visible_groups,
            )
        assert saw_collapsed_group
        extension = ribbon.findChild(QtWidgets.QToolButton, "qt_toolbar_ext_button")
        assert extension is None or not extension.isVisible()

        main_window.resize(1440, 900)
        _process_events()
        tabs.setCurrentIndex(0)
        _process_events()
        assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
        rebuilt_model_page = _ribbon_page(main_window)
        _assert_model_group_graphs(rebuilt_model_page)
        _assert_synthetic_primitive_children(rebuilt_model_page)
        sketch_wrapper = _page_menu_action(
            rebuilt_model_page,
            "PartDesign_CompSketches",
        )
        assert sketch_wrapper is not None and not sketch_wrapper.isEnabled()
        del rebuilt_model_page, sketch_wrapper
        print("VIBECAD_RIBBON_STAGE lifecycle-rebuild", flush=True)
        document = App.newDocument("VibeCADRibbonSmoke")
        _process_events()
        print("VIBECAD_RIBBON_STAGE primary-document", flush=True)
        rebuilt_model_page = _ribbon_page(main_window)
        _assert_model_group_graphs(rebuilt_model_page)
        sketch_wrapper = _page_menu_action(
            rebuilt_model_page,
            "PartDesign_CompSketches",
        )
        assert sketch_wrapper is not None and sketch_wrapper.isEnabled()
        sketch_wrappers = _composite_wrappers(
            rebuilt_model_page,
            "PartDesign_CompSketches",
        )
        sketch_child_states = tuple(
            action.isEnabled()
            for action in sketch_wrappers[0].menu().actions()
            if not action.isSeparator()
        )
        assert sketch_child_states == (True, False, False)
        for wrapper in sketch_wrappers[1:]:
            assert (
                tuple(
                    action.isEnabled()
                    for action in wrapper.menu().actions()
                    if not action.isSeparator()
                )
                == sketch_child_states
            )
        del rebuilt_model_page, sketch_wrapper, sketch_wrappers

        assembly_page = _select_ribbon_workbench(
            main_window,
            tabs,
            "AssemblyWorkbench",
        )
        assert _page_menu_action(
            assembly_page,
            "Assembly_CreateBom",
        ).isEnabled()

        mesh_page = _select_ribbon_workbench(
            main_window,
            tabs,
            "MeshWorkbench",
        )
        mesh_from_shape = _page_menu_action(
            mesh_page,
            "Mesh_FromPartShape",
        )
        assert not mesh_from_shape.isEnabled()
        mesh_source = document.addObject("Part::Feature", "RibbonMeshSource")
        mesh_source.Shape = Part.makeBox(4, 5, 6)
        document.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(mesh_source, "Face1")
        _process_events()
        assert mesh_from_shape.isEnabled()
        Gui.Selection.clearSelection()
        document.removeObject(mesh_source.Name)
        document.recompute()
        _process_events()

        fem_page = _select_ribbon_workbench(
            main_window,
            tabs,
            "FemWorkbench",
        )
        _assert_fem_composite_children(fem_page)
        assert not _page_menu_action(
            fem_page,
            "FEM_PostFilterLinearizedStresses",
        ).isEnabled()
        assert not _page_menu_action(
            fem_page,
            "FEM_PostCreateFunctions",
        ).isEnabled()

        _select_ribbon_workbench(
            main_window,
            tabs,
            "PartDesignWorkbench",
        )
        assert document_tabs.count() == source_document_tabs.count()
        assert any(
            "VibeCADRibbonSmoke" in document_tabs.tabText(index)
            for index in range(document_tabs.count())
        )
        assert not source_document_tabs.isVisible()
        mdi_area = main_window.findChild(QtWidgets.QMdiArea)
        assert mdi_area is not None
        assert feature_timeline.parentWidget() is mdi_area.parentWidget()
        mdi_top = mdi_area.mapTo(main_window, QtCore.QPoint(0, 0)).y()
        mdi_bottom = mdi_area.mapTo(main_window, mdi_area.rect().bottomLeft()).y()
        for object_name in (
            "OverlayLeft",
            "OverlayLeftProxy",
            "OverlayRight",
            "OverlayRightProxy",
        ):
            overlay = main_window.findChild(QtWidgets.QWidget, object_name)
            assert overlay is not None
            overlay_top = overlay.mapTo(main_window, QtCore.QPoint(0, 0)).y()
            overlay_bottom = overlay.mapTo(main_window, overlay.rect().bottomLeft()).y()
            overlay_state = {
                "name": object_name,
                "overlay_top": overlay_top,
                "overlay_bottom": overlay_bottom,
                "mdi_top": mdi_top,
                "mdi_bottom": mdi_bottom,
                "visible": overlay.isVisible(),
                "hidden": overlay.isHidden(),
                "geometry": overlay.geometry().getRect(),
            }
            assert overlay_top >= mdi_top, overlay_state
            assert overlay_bottom <= mdi_bottom, overlay_state
        assert (
            feature_timeline.mapToGlobal(QtCore.QPoint(0, 0)).y()
            >= mdi_area.mapToGlobal(mdi_area.rect().bottomLeft()).y()
        )
        assert source_document_tabs.height() == 0
        assert (
            mdi_area.contentsRect().bottom() - mdi_area.viewport().geometry().bottom()
            <= 1
        )
        secondary_document = App.newDocument("VibeCADRibbonSecond")
        secondary_name = secondary_document.Name
        secondary_label = secondary_document.Label
        _process_events()
        print("VIBECAD_RIBBON_STAGE secondary-document", flush=True)
        assert document_tabs.count() == source_document_tabs.count()
        assert any(
            secondary_label in document_tabs.tabText(index)
            for index in range(document_tabs.count())
        )
        primary_tab = next(
            index
            for index in range(document_tabs.count())
            if "VibeCADRibbonSmoke" in document_tabs.tabText(index)
        )
        document_tabs.setCurrentIndex(primary_tab)
        _process_events()
        assert App.ActiveDocument.Name == document.Name
        secondary_tab = next(
            index
            for index in range(document_tabs.count())
            if secondary_label in document_tabs.tabText(index)
        )

        def discard_secondary_document():
            dialog = application.activeModalWidget()
            if isinstance(dialog, QtWidgets.QMessageBox):
                discard = dialog.button(QtWidgets.QMessageBox.Discard)
                if discard is not None:
                    discard.click()
                else:
                    dialog.reject()

        QtCore.QTimer.singleShot(250, discard_secondary_document)
        document_tabs.tabCloseRequested.emit(secondary_tab)
        _process_events()
        assert secondary_name not in App.listDocuments()
        secondary_document = None
        _process_events()
        assert document_tabs.count() == source_document_tabs.count()
        assert not any(
            secondary_label in document_tabs.tabText(index)
            for index in range(document_tabs.count())
        )
        assert not source_document_tabs.isVisible()
        print("VIBECAD_RIBBON_STAGE document-tabs", flush=True)
        sketch = document.addObject("Sketcher::SketchObject", "RibbonSketch")
        document.recompute()
        Gui.activeDocument().setEdit(sketch.Name)
        _process_events()
        assert Gui.activeWorkbench().name() == "SketcherWorkbench"
        assert tabs.tabText(tabs.currentIndex()) == "Sketch"
        assert [
            tabs.tabText(index) for index in range(tabs.count())
        ] == expected_tabs + ["Sketch"]
        assert all(
            not tabs.isTabEnabled(index)
            for index in range(tabs.count())
            if tabs.tabText(index) != "Sketch"
        )
        assert tabs.isTabEnabled(tabs.currentIndex())
        sketch_page = _ribbon_page(main_window)
        assert _page_group_labels(sketch_page) == [
            "VIEW",
            "FINISH",
            "GEOMETRY",
            "CONSTRAINTS",
            "MODIFY",
            "B-SPLINE",
            "VISUAL",
        ]
        finish_group = main_window.findChild(
            QtWidgets.QFrame, "VibeCADRibbonGroup_Finish"
        )
        assert {
            "Sketcher_LeaveSketch",
            "Sketcher_CancelSketch",
        }.issubset(_group_commands(finish_group))
        print("VIBECAD_RIBBON_STAGE sketch-edit", flush=True)

        Gui.runCommand("Sketcher_LeaveSketch")
        _process_events()
        assert Gui.activeDocument().getInEdit() is None
        assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
        assert tabs.tabText(tabs.currentIndex()) == "Model"
        assert [tabs.tabText(index) for index in range(tabs.count())] == expected_tabs
        assert all(tabs.isTabEnabled(index) for index in range(tabs.count()))
        print("VIBECAD_RIBBON_STAGE sketch-finish", flush=True)

        Gui.activeDocument().setEdit(sketch.Name)
        _process_events()
        assert tabs.tabText(tabs.currentIndex()) == "Sketch"
        Gui.runCommand("Sketcher_CancelSketch")
        _process_events()
        assert Gui.activeDocument().getInEdit() is None
        assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
        assert tabs.tabText(tabs.currentIndex()) == "Model"
        assert [tabs.tabText(index) for index in range(tabs.count())] == expected_tabs
        assert all(tabs.isTabEnabled(index) for index in range(tabs.count()))
        print("VIBECAD_RIBBON_STAGE sketch-cancel", flush=True)

        Gui.activateWorkbench("SketcherWorkbench")
        _process_events()
        Gui.activeDocument().setEdit(sketch.Name)
        _process_events()
        assert Gui.activeWorkbench().name() == "SketcherWorkbench"
        assert tabs.tabText(tabs.currentIndex()) == "Sketch"
        assert all(
            not tabs.isTabEnabled(index)
            for index in range(tabs.count())
            if tabs.tabText(index) != "Sketch"
        )
        Gui.runCommand("Sketcher_LeaveSketch")
        _process_events()
        assert Gui.activeDocument().getInEdit() is None
        assert Gui.activeWorkbench().name() == "SketcherWorkbench"
        assert tabs.tabText(tabs.currentIndex()) == "Sketch"
        assert [
            tabs.tabText(index) for index in range(tabs.count())
        ] == expected_tabs + ["Sketch"]
        assert all(tabs.isTabEnabled(index) for index in range(tabs.count()))
        assert _page_group_labels(_ribbon_page(main_window)) == [
            "VIEW",
            "SKETCH",
            "INSPECT",
        ]
        print("VIBECAD_RIBBON_STAGE sketch-workbench", flush=True)

        tabs.setCurrentIndex(0)
        _process_events()
        assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
        assert [tabs.tabText(index) for index in range(tabs.count())] == expected_tabs

        draft_objects_before = tuple(document.Objects)
        Gui.activateWorkbench("DraftWorkbench")
        _process_events()
        assert Gui.activeWorkbench().name() == "DraftWorkbench"
        for command_name in (
            "Draft_Line",
            "Draft_Wire",
            "Draft_Move",
            "Draft_Snap_Endpoint",
        ):
            actions = Gui.Command.get(command_name).getAction()
            assert actions and not actions[0].icon().isNull(), command_name
        assert _ribbon_page(main_window) is not None
        Gui.runCommand("Draft_Line")
        _process_events()
        assert Gui.Control.activeDialog()
        Gui.draftToolBar.escape()
        _process_events()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog(Gui.activeDocument())
            _process_events()
        assert not Gui.Control.activeDialog()
        assert tuple(document.Objects) == draft_objects_before
        assert (
            main_window.findChild(
                QtWidgets.QDockWidget,
                "Std_TreeView",
            )
            is separate_tree_dock
        )
        assert _visible_main_window_toolbars(main_window) == [ribbon]

        Gui.activateWorkbench("PartDesignWorkbench")
        _process_events()
        assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
        assert tabs.tabText(tabs.currentIndex()) == "Model"
        assert _visible_main_window_toolbars(main_window) == [ribbon]
        print("VIBECAD_RIBBON_STAGE draft-compatibility", flush=True)

        _key_click(main_window, QtCore.Qt.Key_F10)
        _process_events()
        assert main_window.menuBar().isVisible()
        _key_click(main_window, QtCore.Qt.Key_F10)
        _process_events()
        assert not main_window.menuBar().isVisible()
        assert QtWidgets.QApplication.activePopupWidget() is None
        print("VIBECAD_RIBBON_STAGE menu-bar", flush=True)

        preferences_check = {}

        def inspect_preferences_dialog():
            dialog = None
            try:
                dialog = next(
                    (
                        candidate
                        for candidate in application.topLevelWidgets()
                        if isinstance(candidate, QtWidgets.QDialog)
                        and candidate.isVisible()
                        and candidate.findChild(QtWidgets.QComboBox, "themesCombobox")
                        is not None
                    ),
                    None,
                )
                assert dialog is not None
                theme_combo = dialog.findChild(QtWidgets.QComboBox, "themesCombobox")
                assert [
                    theme_combo.itemText(index) for index in range(theme_combo.count())
                ] == [
                    "Light",
                    "Dark",
                ]
                tree_mode = dialog.findChild(QtWidgets.QComboBox, "treeMode")
                assert tree_mode is not None
                assert [
                    tree_mode.itemText(index) for index in range(tree_mode.count())
                ] == [
                    "Combined",
                    "Tree only",
                    "Tree and property",
                ]
                assert tree_mode.currentText() == "Tree only"
                for removed_object in (
                    "ImportConfig",
                    "SaveNewPreferencePack",
                    "ManagePreferencePacks",
                    "RevertToSavedConfig",
                    "moreThemesLabel",
                    "ThemeAccentColor1",
                    "ThemeAccentColor2",
                    "ThemeAccentColor3",
                    "StyleSheets",
                    "OverlayStyleSheets",
                    "themeEditorButton",
                ):
                    assert dialog.findChild(QtWidgets.QWidget, removed_object) is None
                preferences_check["ok"] = True
            except Exception:
                preferences_check["error"] = traceback.format_exc()
            finally:
                if dialog is None:
                    dialog = application.activeModalWidget()
                if isinstance(dialog, QtWidgets.QDialog):
                    dialog.reject()

        QtCore.QTimer.singleShot(500, inspect_preferences_dialog)
        Gui.runCommand("Std_DlgPreferences")
        assert preferences_check.get("ok"), preferences_check.get("error")
        _process_events()
        assert _visible_main_window_toolbars(main_window) == [ribbon]
        assert not main_window.menuBar().isVisible()
        print("VIBECAD_RIBBON_STAGE preferences", flush=True)

        tabs.setCurrentIndex(0)
        _process_events()
        screenshot_path = os.environ.get("VIBECAD_RIBBON_SCREENSHOT")
        if screenshot_path:
            _save_window_screenshot(main_window, screenshot_path)

        print(
            "VIBECAD_RIBBON_THEME_GUI_OK " f"tabs={tabs.count()} mode={initial_mode}",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
        exit_code = 1
    finally:
        sentinel.RemInt("UnrelatedPreference")
        for name in (
            "ThemeAccentColor1",
            "ThemeAccentColor2",
            "ThemeAccentColor3",
        ):
            retired_theme_customization.RemUnsigned(name)
        if secondary_document is not None:
            App.closeDocument(secondary_document.Name)
        if tree_document is not None:
            App.closeDocument(tree_document.Name)
        if initial_mode in {"Light", "Dark"}:
            current = main_window.findChild(QtWidgets.QToolButton, "VibeCADThemeToggle")
            parameters = App.ParamGet("User parameter:BaseApp/Preferences/MainWindow")
            if (
                current is not None
                and parameters.GetString("AppearanceMode", "") != initial_mode
            ):
                current.click()
                _process_events()
        if document is not None:
            if Gui.activeDocument() and Gui.activeDocument().getInEdit():
                Gui.activeDocument().resetEdit()
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1200, _run)
