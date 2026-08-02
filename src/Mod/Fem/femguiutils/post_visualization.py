# ***************************************************************************
# *   Copyright (c) 2025 Stefan Tröger <stefantroeger@gmx.net>              *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

__title__ = "FreeCAD visualization registry"
__author__ = "Stefan Tröger"
__url__ = "https://www.freecad.org"

## @package post_visualization
#  \ingroup FEM
#  \brief A registry to collect visualizations for use in menus

# Note: This file is imported from FreeCAD App files. Do not import any FreeCADGui
#       directly to support cmd line use.

import copy
from dataclasses import dataclass

from PySide import QtCore

import FreeCAD

# Registry to handle visualization commands
# #########################################

_registry = {}


@dataclass
class _Extraction:

    name: str
    icon: str
    dimension: str
    extracttype: str
    module: str
    factory: str


@dataclass
class _Visualization:

    name: str
    icon: str
    module: str
    factory: str
    extractions: list[_Extraction]


# Register a visualization by type, icon and factory function
def register_visualization(visualization_type, icon, module, factory):
    if visualization_type in _registry:
        raise ValueError("Visualization type already registered")

    _registry[visualization_type] = _Visualization(visualization_type, icon, module, factory, [])


def register_extractor(
    visualization_type, extraction_type, icon, dimension, etype, module, factory
):

    if not visualization_type in _registry:
        raise ValueError("visualization not registered yet")

    extraction = _Extraction(extraction_type, icon, dimension, etype, module, factory)
    _registry[visualization_type].extractions.append(extraction)


def get_registered_visualizations():
    return copy.deepcopy(_registry)


def _to_command_name(name):
    return "FEM_PostVisualization" + name


class _VisualizationGroupCommand:

    def GetCommands(self):
        visus = _registry.keys()
        cmds = [_to_command_name(v) for v in visus]
        return cmds

    def GetDefaultCommand(self):
        return 0

    def GetResources(self):
        return {
            "MenuText": QtCore.QT_TRANSLATE_NOOP("FEM", "Data Visualizations"),
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "FEM", "Different visualizations to show post processing data in"
            ),
        }

    def IsActive(self):
        import FemGui
        from femcommands.manager import (
            _active_document,
            _is_live_in_document,
            can_start_command,
        )

        document = _active_document()
        return (
            can_start_command()
            and _is_live_in_document(
                FemGui.getActiveAnalysis(),
                document,
            )
        )


class _VisualizationCommand:

    def __init__(self, visualization_type):
        self._visualization_type = visualization_type

    def GetResources(self):

        cmd = _to_command_name(self._visualization_type)
        vis = _registry[self._visualization_type]
        tooltip = f"Create a {self._visualization_type} post processing data visualization"

        return {
            "Pixmap": vis.icon,
            "MenuText": QtCore.QT_TRANSLATE_NOOP(cmd, "Create {}".format(self._visualization_type)),
            "Accel": "",
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(cmd, tooltip),
            "CmdType": "AlterDoc",
        }

    def IsActive(self):
        import FemGui
        from femcommands.manager import (
            _active_document,
            _is_live_in_document,
            can_start_command,
        )

        document = _active_document()
        return (
            can_start_command()
            and _is_live_in_document(
                FemGui.getActiveAnalysis(),
                document,
            )
        )

    def Activated(self):
        if not self.IsActive():
            return

        import FreeCADGui
        import FemGui
        from femcommands.manager import (
            _active_document,
            _close_exact_transaction,
            _document_expression,
            _is_live_in_document,
            _object_expression,
            _open_exact_transaction,
            _require_provisional_timeline_identity,
        )

        vis = _registry[self._visualization_type]
        document = _active_document()
        analysis = FemGui.getActiveAnalysis()
        transaction_id = _open_exact_transaction(
            document,
            f"Create {vis.name}",
        )
        try:
            FreeCADGui.addModule(vis.module)
            FreeCADGui.addModule("FemGui")
            obj = FreeCADGui.runDocumentObjectCommand(
                document,
                f"{vis.module}.{vis.factory}("
                f"{_document_expression(document)})",
            )
            _require_provisional_timeline_identity(
                obj,
                document,
                "The FEM visualization factory",
            )
            if not _is_live_in_document(analysis, document):
                raise RuntimeError(
                    "The active FEM analysis is no longer available"
                )
            FreeCADGui.doCommand(
                f"{_object_expression(analysis)}"
                f".addObject({_object_expression(obj)})"
            )
            if obj not in analysis.Group:
                raise RuntimeError(
                    "The visualization was not added to its analysis"
                )
            FreeCADGui.Selection.clearSelection()
            gui_document = FreeCADGui.getDocument(document.Name)
            if gui_document is None or gui_document.setEdit(obj) is False:
                raise RuntimeError(
                    "The visualization editor could not be opened"
                )
        except Exception:
            _close_exact_transaction(
                document,
                transaction_id,
                True,
            )
            raise


def setup_commands(toplevel_name):
    # creates all visualization commands and registers them. The
    # toplevel group command will have the name provided to this function.

    import FreeCADGui

    # first all visualization and extraction commands
    for vis in _registry:
        FreeCADGui.addCommand(_to_command_name(vis), _VisualizationCommand(vis))

    # build the group command!
    FreeCADGui.addCommand("FEM_PostVisualization", _VisualizationGroupCommand())
