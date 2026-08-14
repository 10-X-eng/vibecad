# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2017 sliptonic <shopinthewoods@gmail.com>               *
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

from PySide import QtGui
from PySide.QtCore import QT_TRANSLATE_NOOP
import FreeCAD
import FreeCADGui
import Path
from Path.CommandBoundary import (
    ExactDocumentObjectIdentity,
    active_jobs,
    begin_task_launch,
    can_start_document_command,
    can_start_ui_command,
)
import Path.Main.Gui.JobDlg as PathJobDlg
import Path.Main.Job as PathJob
import json
import os

translate = FreeCAD.Qt.translate

if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())


class CommandJobCreate:
    """
    Command used to create a command.
    When activated the command opens a dialog allowing the user to select a base object (has to be a solid)
    and a template to be used for the initial creation.
    """

    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "CAM_Job",
            "MenuText": QT_TRANSLATE_NOOP("CAM_Job", "New Job"),
            "Accel": "P, J",
            "ToolTip": QT_TRANSLATE_NOOP("CAM_Job", "Creates a CAM job"),
        }

    def IsActive(self):
        return can_start_document_command()

    def Activated(self):
        if not self.IsActive():
            return

        dialog = PathJobDlg.JobCreate()
        dialog.setupTemplate()
        dialog.setupModel()
        if dialog.exec_() == 1:
            models = dialog.getModels()
            if models:
                self.Execute(models, dialog.getTemplate())
                models[0].Document.recompute()

    @classmethod
    def Execute(cls, base, template):
        if not base:
            return
        document = base[0].Document
        if any(obj.Document is not document for obj in base):
            raise RuntimeError(
                "A CAM Job cannot span multiple documents"
            )
        base_identities = [
            ExactDocumentObjectIdentity(obj, document)
            for obj in base
        ]
        for identity in base_identities:
            identity.resolve(require_timeline=True)
        launch = begin_task_launch("Create CAM Job", document)
        FreeCADGui.addModule("Path.Main.Gui.Job")
        if template:
            template = "'%s'" % template
        else:
            template = "None"
        try:
            base = [
                identity.resolve(require_timeline=True)
                for identity in base_identities
            ]
            base_expression = ", ".join(
                "FreeCAD.getDocument(%r).getObject(%r)"
                % (document.Name, obj.Name)
                for obj in base
            )
            FreeCADGui.doCommand(
                "Path.Main.Gui.Job.Create([%s], %s)"
                % (base_expression, template)
            )
            for identity in base_identities:
                identity.resolve(require_timeline=True)
            launch.require_claimed()
        except Exception:
            launch.abort()
            raise


class CommandJobTemplateExport:
    """
    Command to export a template of a given job.
    Opens a dialog to select the file to store the template in. If the template is stored in Path's
    file path (see preferences) and named in accordance with job_*.json it will automatically be found
    on Job creation and be available for selection.
    """

    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "CAM_ExportTemplate",
            "MenuText": QT_TRANSLATE_NOOP("CAM_ExportTemplate", "Export Template"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "CAM_ExportTemplate",
                "Exports the CAM job as a template to be used for other jobs",
            ),
        }

    def GetJob(self):
        # if there's only one Job in the document ...
        jobs = active_jobs()
        if not jobs:
            return None
        if len(jobs) == 1:
            return jobs[0]
        # more than one job, is one of them selected?
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) == 1:
            job = sel[0]
            if (
                job in jobs
                and hasattr(job, "Proxy")
                and isinstance(job.Proxy, PathJob.ObjectJob)
            ):
                return job
        return None

    def IsActive(self):
        return can_start_ui_command() and self.GetJob() is not None

    def Activated(self):
        if not self.IsActive():
            return
        job = self.GetJob()
        dialog = PathJobDlg.JobTemplateExport(job)
        if dialog.exec_() == 1:
            self.SaveDialog(job, dialog)

    @classmethod
    def SaveDialog(cls, job, dialog):
        identity = ExactDocumentObjectIdentity(
            job,
            job.Document,
        )
        foo = QtGui.QFileDialog.getSaveFileName(
            QtGui.QApplication.activeWindow(),
            "Path - Job Template",
            str(Path.Preferences.getTemplateDirectory()),
            "job_*.json",
        )[0]
        if foo:
            if not os.path.basename(foo).startswith("job_"):
                foo = os.path.join(os.path.dirname(foo), "job_" + os.path.basename(foo))
            if not foo.endswith(".json"):
                foo = foo + ".json"
            job = identity.resolve(require_timeline=True)
            cls.Execute(job, foo, dialog)

    @classmethod
    def Execute(cls, job, path, dialog=None):
        encoded = job.Proxy.exportTemplateAttributes(
            job,
            description=(dialog.description() if dialog else None),
            includePostProcessing=(dialog.includePostProcessing() if dialog else True),
            toolControllers=(dialog.includeToolControllers() if dialog else None),
            includeStock=(dialog.includeStock() if dialog else True),
            includeStockExtent=(dialog.includeStockExtent() if dialog else True),
            includeStockPlacement=(dialog.includeStockPlacement() if dialog else True),
            includeSettingToolRapid=(
                dialog.includeSettingToolRapid() if dialog else True
            ),
            includeSettingCoolant=(dialog.includeSettingCoolant() if dialog else True),
            includeSettingOperationHeights=(
                dialog.includeSettingOperationHeights() if dialog else True
            ),
            includeSettingOperationDepths=(
                dialog.includeSettingOperationDepths() if dialog else True
            ),
            includeSettingOperations=(
                dialog.includeSettingOpsSettings() if dialog else None
            ),
        )
        # write template
        with open(str(path), "w") as fp:
            json.dump(encoded, fp, sort_keys=True, indent=2)


if FreeCAD.GuiUp:
    # register the FreeCAD command
    FreeCADGui.addCommand("CAM_Job", CommandJobCreate())
    FreeCADGui.addCommand("CAM_ExportTemplate", CommandJobTemplateExport())

FreeCAD.Console.PrintLog("Loading PathJobCmd… done\n")
