# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2014 Yorik van Havre <yorik@uncreated.net>              *
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

from Path.Post.Processor import PostProcessorFactory  # PostProcessor,
from PySide import QtCore
from PySide.QtCore import QT_TRANSLATE_NOOP
import FreeCAD
import Path
import Path.Base.SetupSheet as PathSetupSheet
import Path.Base.Util as PathUtil
from Path.CommandBoundary import is_timeline_input_usable
import Path.Main.Stock as PathStock
import Path.Tool.Controller as PathToolController
import json
import time

if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())

translate = FreeCAD.Qt.translate


class JobTemplate:
    """Attribute and sub element strings for template export/import."""

    Description = "Desc"
    GeometryTolerance = "Tolerance"
    Job = "Job"
    PostProcessor = "Post"
    PostProcessorArgs = "PostArgs"
    PostProcessorOutputFile = "Output"
    Fixtures = "Fixtures"
    OrderOutputBy = "OrderOutputBy"
    SplitOutput = "SplitOutput"
    SetupSheet = "SetupSheet"
    Stock = "Stock"
    # TCs are grouped under Tools in a job, the template refers to them directly though
    ToolController = "ToolController"
    PostProcessorPropertyOverrides = "PostPropertyOverrides"
    Machine = "Machine"
    Version = "Version"


def isResourceClone(obj, propLink, resourceName):
    if hasattr(propLink, "PathResource") and (
        resourceName is None or resourceName == propLink.PathResource
    ):
        return True
    return False


def createResourceClone(obj, orig, name, icon, recompute=True):
    from draftobjects.clone import Clone
    from draftutils import utils as DraftUtils

    document = obj.Document
    if getattr(orig, "Document", None) is not document:
        raise RuntimeError(
            "A CAM Job resource cannot reference another document"
        )
    # Draft.make_clone() always creates in ActiveDocument.  Build the same
    # Draft clone object in the Job's captured document so a background-tab
    # task cannot leak its model resource into the foreground document.
    if (
        orig.isDerivedFrom("Part::Part2DObject")
        and DraftUtils.get_type(orig)
        not in ["BezCurve", "BSpline", "Wire"]
    ):
        clone = document.addObject(
            "Part::Part2DObjectPython",
            "Clone2D",
        )
        PathUtil.markTimelineResource(clone, obj)
    else:
        clone = document.addObject("Part::FeaturePython", "Clone")
        PathUtil.markTimelineResource(clone, obj)
        clone.addExtension("Part::AttachExtensionPython")
    Clone(clone)
    clone.Objects = [orig]
    if hasattr(orig, "Placement"):
        clone.Placement = orig.Placement
    if hasattr(clone, "LongName") and hasattr(orig, "LongName"):
        clone.LongName = orig.LongName
    if FreeCAD.GuiUp:
        from draftviewproviders.view_clone import ViewProviderClone

        ViewProviderClone(clone.ViewObject)
    clone.Label = "%s-%s" % (name, orig.Label)
    clone.addProperty("App::PropertyString", "PathResource")
    clone.PathResource = name
    if clone.ViewObject:
        import Path.Base.Gui.IconViewProvider

        Path.Base.Gui.IconViewProvider.Attach(clone.ViewObject, icon)
        clone.ViewObject.Visibility = False
        clone.ViewObject.DisplayMode = "Flat Lines"
        clone.ViewObject.ShapeColor = (0.447, 0.475, 0.502)
        clone.ViewObject.Transparency = 0
        clone.ViewObject.LineColor = (0.310, 0.333, 0.357)
        clone.ViewObject.ShapeMaterial.Shininess = 0.85
    if recompute:
        obj.Document.recompute()  # necessary to create the clone shape
    return clone


def createModelResourceClone(obj, orig):
    return createResourceClone(obj, orig, "Model", "BaseGeometry")


class NotificationClass(QtCore.QObject):
    updateTC = QtCore.Signal(object, object)


Notification = NotificationClass()


class ObjectJob:
    TREE_ROLE = "manufacture_setup"

    def __init__(
        self,
        obj,
        models,
        templateFile=None,
        createDefaultToolController=True,
        createDefaultStock=True,
        deferTimelinePublication=False,
    ):
        self.obj = obj
        self.tooltip = None
        self.tooltipArgs = None
        self._deferTimelinePublication = bool(
            deferTimelinePublication
        )
        self._initialTimelineResources = []
        obj.Proxy = self
        PathUtil.markTimelineOperation(obj)
        self.setupTreePresentation(obj)

        obj.addProperty(
            "App::PropertyFile",
            "PostProcessorOutputFile",
            "Output",
            QT_TRANSLATE_NOOP("App::Property", "The G-code output file for this project"),
        )
        obj.addProperty(
            "App::PropertyEnumeration",
            "PostProcessor",
            "Output",
            QT_TRANSLATE_NOOP("App::Property", "Select the Post Processor"),
        )
        obj.addProperty(
            "App::PropertyString",
            "PostProcessorArgs",
            "Output",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Arguments for the Post Processor (specific to the script)",
            ),
        )
        obj.addProperty(
            "App::PropertyString",
            "LastPostProcessDate",
            "Output",
            QT_TRANSLATE_NOOP("App::Property", "Last Time the Job was post processed"),
        )
        obj.setEditorMode("LastPostProcessDate", 2)  # Hide
        obj.addProperty(
            "App::PropertyString",
            "LastPostProcessOutput",
            "Output",
            QT_TRANSLATE_NOOP("App::Property", "Last Time the Job was post processed"),
        )
        obj.setEditorMode("LastPostProcessOutput", 2)  # Hide

        obj.addProperty(
            "App::PropertyString",
            "Description",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "An optional description for this job"),
        )
        obj.addProperty(
            "App::PropertyString",
            "CycleTime",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Job Cycle Time Estimation"),
        )
        obj.setEditorMode("CycleTime", 1)  # read-only
        obj.addProperty(
            "App::PropertyLength",
            "GeometryTolerance",
            "Geometry",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "For computing Paths; smaller increases accuracy, but slows down computation",
            ),
        )

        obj.addProperty(
            "App::PropertyLink",
            "Stock",
            "Base",
            QT_TRANSLATE_NOOP("App::Property", "Solid object to be used as stock."),
        )
        obj.addProperty(
            "App::PropertyLink",
            "Operations",
            "Base",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Compound path of all operations in the order they are processed.",
            ),
        )

        obj.addProperty(
            "App::PropertyEnumeration",
            "JobType",
            "Base",
            QT_TRANSLATE_NOOP("App::Property", "Select the Type of Job"),
        )
        obj.setEditorMode("JobType", 2)  # Hide

        obj.addProperty(
            "App::PropertyBool",
            "SplitOutput",
            "Output",
            QT_TRANSLATE_NOOP("App::Property", "Split output into multiple G-code files"),
        )
        obj.addProperty(
            "App::PropertyEnumeration",
            "OrderOutputBy",
            "WCS",
            QT_TRANSLATE_NOOP("App::Property", "If multiple WCS, order the output this way"),
        )
        obj.addProperty(
            "App::PropertyStringList",
            "Fixtures",
            "WCS",
            QT_TRANSLATE_NOOP("App::Property", "The Work Coordinate Systems for the Job"),
        )
        obj.addProperty(
            "App::PropertyString",
            "Machine",
            "Output",
            QT_TRANSLATE_NOOP("App::Property", "The Machine for the Job"),
        )
        obj.addProperty(
            "App::PropertyString",
            "PostProcessorPropertyOverrides",
            "Output",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "JSON dict of postprocessor properties that override machine defaults for this job",
            ),
        )
        obj.PostProcessorPropertyOverrides = "{}"

        obj.Fixtures = ["G54"]

        for n in self.propertyEnumerations():
            setattr(obj, n[0], n[1])

        obj.PostProcessorOutputFile = Path.Preferences.defaultOutputFile()
        postProcessors = Path.Preferences.allEnabledLegacyPostProcessors()
        # Add empty string as a valid enumeration option
        if "" not in postProcessors:
            postProcessors = [""] + postProcessors
        obj.PostProcessor = postProcessors
        defaultPostProcessor = Path.Preferences.defaultPostProcessor()
        # Check to see if default post processor hasn't been 'lost' (This can happen when Macro dir has changed)
        if defaultPostProcessor in postProcessors:
            obj.PostProcessor = defaultPostProcessor
        else:
            obj.PostProcessor = ""
        obj.PostProcessorArgs = Path.Preferences.defaultPostProcessorArgs()

        obj.GeometryTolerance = Path.Preferences.defaultGeometryTolerance()

        self.setupOperations(obj)
        self.setupSetupSheet(obj)
        self.setupBaseModel(obj, models)
        self.setupToolTable(obj)
        self.setFromTemplateFile(
            obj,
            templateFile,
            createDefaultToolController=createDefaultToolController,
        )
        if createDefaultStock:
            self.setupStock(obj)
        self.setupTimelineTracking(obj)

    def setupTreePresentation(self, obj):
        """Publish the Job as the authoritative Manufacture tree root."""

        if not hasattr(obj, "VibeCADTreeRole"):
            obj.addProperty(
                "App::PropertyString",
                "VibeCADTreeRole",
                "Tree",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Application tree presentation role",
                ),
            )
        obj.VibeCADTreeRole = self.TREE_ROLE
        obj.setEditorMode("VibeCADTreeRole", 2)

    def _captureInitialTimelineOperation(self, operation):
        """Defer initial Job role mutation to the atomic core publisher."""

        if not getattr(
            self,
            "_deferTimelinePublication",
            False,
        ):
            return False
        if operation is not self.obj:
            raise RuntimeError(
                "A CAM Job cannot capture another initial operation root"
            )
        return True

    def _captureInitialTimelineResource(self, resource):
        """Record one exact initial Job resource without mutating metadata."""

        if not getattr(
            self,
            "_deferTimelinePublication",
            False,
        ):
            return False
        document = self.obj.Document
        if (
            resource is self.obj
            or getattr(resource, "Document", None) is not document
            or document.getObject(resource.Name) is not resource
        ):
            raise RuntimeError(
                "A captured CAM Job resource must be one distinct live "
                "object in the Job document"
            )
        if not any(
            resource is existing
            for existing in self._initialTimelineResources
        ):
            self._initialTimelineResources.append(resource)
        return True

    def initialTimelineResources(self):
        """Return the exact authored resource order for initial publication."""

        if not getattr(
            self,
            "_deferTimelinePublication",
            False,
        ):
            return ()
        document = self.obj.Document
        resources = tuple(self._initialTimelineResources)
        if any(
            getattr(resource, "Document", None) is not document
            or document.getObject(resource.Name) is not resource
            for resource in resources
        ):
            raise RuntimeError(
                "A captured CAM Job resource changed before publication"
            )
        return resources

    def _releaseInitialTimelineResource(self, resource):
        """Forget an explicitly deleted resource before publication."""

        if not getattr(
            self,
            "_deferTimelinePublication",
            False,
        ):
            return False
        self._initialTimelineResources = [
            existing
            for existing in self._initialTimelineResources
            if existing is not resource
        ]
        return True

    @classmethod
    def propertyEnumerations(self, dataType="data"):
        """propertyEnumerations(dataType="data")... return property enumeration lists of specified dataType.
        Args:
            dataType = 'data', 'raw', 'translated'
        Notes:
        'data' is list of internal string literals used in code
        'raw' is list of (translated_text, data_string) tuples
        'translated' is list of translated string literals
        """

        enums = {
            "OrderOutputBy": [
                (translate("CAM_Job", "Fixture"), "Fixture"),
                (translate("CAM_Job", "Tool"), "Tool"),
                (translate("CAM_Job", "Operation"), "Operation"),
            ],
            "JobType": [
                (translate("CAM_Job", "2D"), "2D"),
                (translate("CAM_Job", "2.5D"), "2.5D"),
                (translate("CAM_Job", "Lathe"), "Lathe"),
                (translate("CAM_Job", "Multiaxis"), "Multiaxis"),
            ],
        }

        if dataType == "raw":
            return enums

        data = list()
        idx = 0 if dataType == "translated" else 1

        Path.Log.debug(enums)

        for k, v in enumerate(enums):
            data.append((v, [tup[idx] for tup in enums[v]]))
        Path.Log.debug(data)

        return data

    def setupOperations(self, obj):
        """setupOperations(obj)... setup the Operations group for the Job object."""
        # ops = FreeCAD.ActiveDocument.addObject(
        #     "Path::FeatureCompoundPython", "Operations"
        # )
        ops = obj.Document.addObject(
            "App::DocumentObjectGroup",
            "Operations",
        )
        PathUtil.markTimelineResource(ops, obj)
        if ops.ViewObject:
            # ops.ViewObject.Proxy = 0
            ops.ViewObject.Visibility = True

        obj.Operations = ops
        obj.setEditorMode("Operations", 2)  # hide
        obj.setEditorMode("Placement", 2)

    def setupSetupSheet(self, obj):
        if not getattr(obj, "SetupSheet", None):
            if not hasattr(obj, "SetupSheet"):
                obj.addProperty(
                    "App::PropertyLink",
                    "SetupSheet",
                    "Base",
                    QT_TRANSLATE_NOOP(
                        "App::Property", "SetupSheet holding the settings for this job"
                    ),
                )
            obj.SetupSheet = PathSetupSheet.Create(
                document=obj.Document,
                timelineOwner=obj,
            )
            if obj.SetupSheet.ViewObject:
                import Path.Base.Gui.IconViewProvider

                Path.Base.Gui.IconViewProvider.Attach(obj.SetupSheet.ViewObject, "SetupSheet")
            obj.SetupSheet.Label = "SetupSheet"
        PathUtil.markTimelineResource(obj.SetupSheet, obj)
        self.setupSheet = obj.SetupSheet.Proxy

    def setupBaseModel(self, obj, models=None):
        Path.Log.track(obj.Label, models)
        addModels = False

        if not hasattr(obj, "Model"):
            obj.addProperty(
                "App::PropertyLink",
                "Model",
                "Base",
                QT_TRANSLATE_NOOP("App::Property", "The base objects for all operations"),
            )
            addModels = True
        elif obj.Model is None:
            addModels = True

        if addModels:
            model = obj.Document.addObject(
                "App::DocumentObjectGroup",
                "Model",
            )
            PathUtil.markTimelineResource(model, obj)
            if model.ViewObject:
                model.ViewObject.Visibility = False
            if models:
                model.addObjects([createModelResourceClone(obj, base) for base in models])
            obj.Model = model
            obj.Model.Label = "Model"
        PathUtil.markTimelineResource(obj.Model, obj)
        for model in obj.Model.Group:
            PathUtil.markTimelineResource(model, obj)

        if hasattr(obj, "Base"):
            Path.Log.info("Converting Job.Base to new Job.Model for {}".format(obj.Label))
            obj.Model.addObject(obj.Base)
            obj.Base = None
            obj.removeProperty("Base")

    def setupToolTable(self, obj):
        addTable = False
        if not hasattr(obj, "Tools"):
            obj.addProperty(
                "App::PropertyLink",
                "Tools",
                "Base",
                QT_TRANSLATE_NOOP(
                    "App::Property", "Collection of all tool controllers for the job"
                ),
            )
            addTable = True
        elif obj.Tools is None:
            addTable = True

        if addTable:
            toolTable = obj.Document.addObject(
                "App::DocumentObjectGroup",
                "Tools",
            )
            PathUtil.markTimelineResource(toolTable, obj)
            toolTable.Label = "Tools"
            if toolTable.ViewObject:
                toolTable.ViewObject.Visibility = False
            if hasattr(obj, "ToolController"):
                toolTable.addObjects(obj.ToolController)
                obj.removeProperty("ToolController")
            obj.Tools = toolTable
        PathUtil.markTimelineResource(obj.Tools, obj)
        for controller in obj.Tools.Group:
            self.markToolControllerResource(controller)

    def setupStock(self, obj):
        """setupStock(obj)... setup the Stock for the Job object."""
        if not obj.Stock:
            stockTemplate = Path.Preferences.defaultStockTemplate()
            if stockTemplate:
                obj.Stock = PathStock.CreateFromTemplate(obj, json.loads(stockTemplate))
            if not obj.Stock:
                obj.Stock = PathStock.CreateFromBase(obj)
        PathUtil.markTimelineResource(obj.Stock, obj)
        PathStock.ApplyStockViewDefaults(obj.Stock)
        if obj.Stock and obj.Stock.ViewObject:
            obj.Stock.ViewObject.Visibility = True

    def removeBase(self, obj, base, removeFromModel):
        if isResourceClone(obj, base, None):
            PathUtil.clearExpressionEngine(base)
            if removeFromModel:
                obj.Model.removeObject(base)
            obj.Document.removeObject(base.Name)

    def modelBoundBox(self, obj):
        return PathStock.shapeBoundBox(obj.Model.Group)

    def onDelete(self, obj, arg2=None):
        """Called by the view provider, there doesn't seem to be a callback on the obj itself."""
        Path.Log.track(obj.Label, arg2)
        doc = obj.Document

        if getattr(obj, "Operations", None):
            # the first to tear down are the ops, they depend on other resources
            Path.Log.debug("taking down ops: %s" % [o.Name for o in self.allOperations()])
            while obj.Operations.Group:
                op = obj.Operations.Group[0]
                if (
                    not op.ViewObject
                    or not hasattr(op.ViewObject.Proxy, "onDelete")
                    or op.ViewObject.Proxy.onDelete(op.ViewObject, ())
                ):
                    PathUtil.clearExpressionEngine(op)
                    doc.removeObject(op.Name)
            obj.Operations.Group = []
            doc.removeObject(obj.Operations.Name)
            obj.Operations = None

        # stock could depend on Model, so delete it first
        if getattr(obj, "Stock", None):
            Path.Log.debug("taking down stock")
            PathUtil.clearExpressionEngine(obj.Stock)
            doc.removeObject(obj.Stock.Name)
            obj.Stock = None

        # base doesn't depend on anything inside job
        if getattr(obj, "Model", None):
            for base in obj.Model.Group:
                Path.Log.debug("taking down base %s" % base.Label)
                self.removeBase(obj, base, False)
            obj.Model.Group = []
            doc.removeObject(obj.Model.Name)
            obj.Model = None

        # Tool controllers might refer to either legacy tool or toolbit
        if getattr(obj, "Tools", None):
            Path.Log.debug("taking down tool controller")
            for tc in obj.Tools.Group:
                if hasattr(tc.Tool, "BitBody") and tc.Tool.BitBody:
                    tc.Tool.BitBody.removeObjectsFromDocument()
                    doc.removeObject(tc.Tool.BitBody.Name)
                if hasattr(tc.Tool, "Proxy"):
                    PathUtil.clearExpressionEngine(tc.Tool)
                    doc.removeObject(tc.Tool.Name)
                PathUtil.clearExpressionEngine(tc)
                tc.Proxy.onDelete(tc)
                doc.removeObject(tc.Name)
            obj.Tools.Group = []
            doc.removeObject(obj.Tools.Name)
            obj.Tools = None

        # SetupSheet
        if getattr(obj, "SetupSheet", None):
            PathUtil.clearExpressionEngine(obj.SetupSheet)
            doc.removeObject(obj.SetupSheet.Name)
            obj.SetupSheet = None

        return True

    def fixupOperations(self, obj):
        if getattr(obj.Operations, "ViewObject", None):
            try:
                obj.Operations.ViewObject.DisplayMode
            except Exception:
                document = obj.Document
                name = obj.Operations.Name
                label = obj.Operations.Label
                ops = document.addObject("Path::FeatureCompoundPython", "Operations")
                ops.ViewObject.Proxy = 0
                ops.Group = obj.Operations.Group
                obj.Operations.Group = []
                obj.Operations = ops
                document.removeObject(name)
                if label == "Unnamed":
                    ops.Label = "Operations"
                else:
                    ops.Label = label

    def ensureMachineProperty(self, obj):
        """Ensure the Machine property exists as a String.
        Migrates from Enumeration to String if needed (legacy documents)."""
        if not hasattr(obj, "Machine"):
            obj.addProperty(
                "App::PropertyString",
                "Machine",
                "Output",
                QT_TRANSLATE_NOOP("App::Property", "The Machine for the Job"),
            )
        elif obj.getTypeIdOfProperty("Machine") == "App::PropertyEnumeration":
            current_value = getattr(obj, "Machine", "") or ""
            obj.removeProperty("Machine")
            obj.addProperty(
                "App::PropertyString",
                "Machine",
                "Output",
                QT_TRANSLATE_NOOP("App::Property", "The Machine for the Job"),
            )
            obj.Machine = current_value

    def onDocumentRestored(self, obj):
        self.obj = obj
        # Initial publication capture exists only while constructing a new
        # Job inside its owning transaction.  Restored proxies bypass
        # __init__, so establish the non-capturing durable state before any
        # setup helper restores resource metadata.
        self._deferTimelinePublication = False
        self._initialTimelineResources = []
        self.setupTreePresentation(obj)
        self.setupBaseModel(obj)
        self.fixupOperations(obj)
        self.setupSetupSheet(obj)

        # Update PostProcessor enumeration to legacy-only posts
        postProcessors = Path.Preferences.allEnabledLegacyPostProcessors()
        if "" not in postProcessors:
            postProcessors = [""] + postProcessors
        obj.PostProcessor = postProcessors

        # Ensure Machine property exists as a String.
        # Old documents may have it as an Enumeration or not at all.
        self.ensureMachineProperty(obj)

        self.setupToolTable(obj)
        self.integrityCheck(obj)
        self.setupTimelineTracking(obj)

        obj.setEditorMode("Operations", 2)  # hide
        obj.setEditorMode("Placement", 2)

        if hasattr(obj, "Path"):
            obj.Path = Path.Path()

        if not hasattr(obj, "CycleTime"):
            obj.addProperty(
                "App::PropertyString",
                "CycleTime",
                "Path",
                QT_TRANSLATE_NOOP("App::Property", "Operations Cycle Time Estimation"),
            )
            obj.setEditorMode("CycleTime", 1)  # read-only

        if not hasattr(obj, "Fixtures"):
            obj.addProperty(
                "App::PropertyStringList",
                "Fixtures",
                "WCS",
                QT_TRANSLATE_NOOP("App::Property", "The Work Coordinate Systems for the Job"),
            )
            obj.Fixtures = ["G54"]

        if not hasattr(obj, "OrderOutputBy"):
            obj.addProperty(
                "App::PropertyEnumeration",
                "OrderOutputBy",
                "WCS",
                QT_TRANSLATE_NOOP("App::Property", "If multiple WCS, order the output this way"),
            )
            obj.OrderOutputBy = ["Fixture", "Tool", "Operation"]

        if not hasattr(obj, "SplitOutput"):
            obj.addProperty(
                "App::PropertyBool",
                "SplitOutput",
                "Output",
                QT_TRANSLATE_NOOP("App::Property", "Split output into multiple G-code files"),
            )
            obj.SplitOutput = False

        if not hasattr(obj, "JobType"):
            obj.addProperty(
                "App::PropertyEnumeration",
                "JobType",
                "Base",
                QT_TRANSLATE_NOOP("App::Property", "Select the type of Job"),
            )
            obj.setEditorMode("JobType", 2)  # Hide

        if not hasattr(obj, "Machine"):
            obj.addProperty(
                "App::PropertyString",
                "Machine",
                "Output",
                QT_TRANSLATE_NOOP("App::Property", "The Machine for the Job"),
            )
        if not hasattr(obj, "PostProcessorPropertyOverrides"):
            obj.addProperty(
                "App::PropertyString",
                "PostProcessorPropertyOverrides",
                "Output",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "JSON dict of postprocessor properties that override machine defaults for this job",
                ),
            )
            obj.PostProcessorPropertyOverrides = "{}"

        for n in self.propertyEnumerations():
            setattr(obj, n[0], n[1])

    def onChanged(self, obj, prop):
        if prop == "PostProcessor" and obj.PostProcessor:
            processor = PostProcessorFactory.get_post_processor(obj, obj.PostProcessor)
            self.tooltip = processor.tooltip
            self.tooltipArgs = processor.tooltipArgs

    def baseObject(self, obj, base):
        """Return the base object, not its clone."""
        if isResourceClone(obj, base, "Model") or isResourceClone(obj, base, "Base"):
            if hasattr(base, "Objects") and base.Objects:
                return base.Objects[0]
        return base

    def baseObjects(self, obj):
        """Return the base objects, not their clones."""
        return [self.baseObject(obj, base) for base in obj.Model.Group]

    def resourceClone(self, obj, base):
        """resourceClone(obj, base) ... Return the resource clone for base if it exists."""
        if isResourceClone(obj, base, None):
            return base
        for b in obj.Model.Group:
            if base == b.Objects[0]:
                return b
        return None

    def setFromTemplateFile(self, obj, template, createDefaultToolController=True):
        """setFromTemplateFile(obj, template) ... extract the properties from the given template file and assign to receiver.
        This will also create any TCs stored in the template."""
        tcs = []
        if template:
            with open(str(template), "rb") as fp:
                attrs = json.load(fp)

            if attrs.get(JobTemplate.Version) and 1 == int(attrs[JobTemplate.Version]):
                attrs = self.setupSheet.decodeTemplateAttributes(attrs)
                if attrs.get(JobTemplate.SetupSheet):
                    self.setupSheet.setFromTemplate(attrs[JobTemplate.SetupSheet])

                if attrs.get(JobTemplate.GeometryTolerance):
                    obj.GeometryTolerance = float(attrs.get(JobTemplate.GeometryTolerance))
                if attrs.get(JobTemplate.PostProcessor):
                    templatePost = attrs.get(JobTemplate.PostProcessor)
                    # Validate that the template's postprocessor exists in current enumeration
                    availablePosts = tuple(
                        obj.getEnumerationsOfProperty("PostProcessor") or ()
                    )
                    if templatePost in availablePosts:
                        obj.PostProcessor = templatePost
                    else:
                        Path.Log.warning(
                            f"PostProcessor '{templatePost}' from template not found in available postprocessors. Using default."
                        )
                        Path.Log.debug(f"Available postprocessors: {availablePosts}")
                        # Keep the default postprocessor that was already set
                    if attrs.get(JobTemplate.PostProcessorArgs):
                        obj.PostProcessorArgs = attrs.get(JobTemplate.PostProcessorArgs)
                    else:
                        obj.PostProcessorArgs = ""
                if attrs.get(JobTemplate.PostProcessorPropertyOverrides):
                    obj.PostProcessorPropertyOverrides = json.dumps(
                        attrs[JobTemplate.PostProcessorPropertyOverrides]
                    )
                if attrs.get(JobTemplate.PostProcessorOutputFile):
                    obj.PostProcessorOutputFile = attrs.get(JobTemplate.PostProcessorOutputFile)
                if attrs.get(JobTemplate.Machine):
                    obj.Machine = attrs.get(JobTemplate.Machine)
                if attrs.get(JobTemplate.Description):
                    obj.Description = attrs.get(JobTemplate.Description)

                if attrs.get(JobTemplate.ToolController):
                    for tc in attrs.get(JobTemplate.ToolController):
                        ctrl = PathToolController.FromTemplate(
                            tc,
                            document=obj.Document,
                            timelineOwner=obj,
                        )
                        if ctrl:
                            tcs.append(ctrl)
                        else:
                            Path.Log.debug(f"skipping TC {tc['name']}")
                if attrs.get(JobTemplate.Stock):
                    obj.Stock = PathStock.CreateFromTemplate(obj, attrs.get(JobTemplate.Stock))

                if attrs.get(JobTemplate.Fixtures):
                    obj.Fixtures = [x for y in attrs.get(JobTemplate.Fixtures) for x in y]

                if attrs.get(JobTemplate.OrderOutputBy):
                    obj.OrderOutputBy = attrs.get(JobTemplate.OrderOutputBy)

                if attrs.get(JobTemplate.SplitOutput):
                    obj.SplitOutput = attrs.get(JobTemplate.SplitOutput)

                Path.Log.debug("setting tool controllers (%d)" % len(tcs))
                if tcs:
                    obj.Tools.Group = tcs
            else:
                Path.Log.error(
                    "Unsupported PathJob template version {}".format(attrs.get(JobTemplate.Version))
                )

        if not tcs and createDefaultToolController:
            self.addToolController(
                PathToolController.Create(
                    document=obj.Document,
                    timelineOwner=obj,
                )
            )

    def templateAttrs(self, obj):
        """templateAttrs(obj) ... answer a dictionary with all properties of the receiver that should be stored in a template file."""
        attrs = {}
        attrs[JobTemplate.Version] = 1
        if obj.PostProcessor:
            attrs[JobTemplate.PostProcessor] = obj.PostProcessor
            attrs[JobTemplate.PostProcessorArgs] = obj.PostProcessorArgs
            attrs[JobTemplate.Fixtures] = [{f: True} for f in obj.Fixtures]
            attrs[JobTemplate.OrderOutputBy] = obj.OrderOutputBy
            attrs[JobTemplate.SplitOutput] = obj.SplitOutput
        if (
            hasattr(obj, "PostProcessorPropertyOverrides")
            and obj.PostProcessorPropertyOverrides
            and obj.PostProcessorPropertyOverrides != "{}"
        ):
            attrs[JobTemplate.PostProcessorPropertyOverrides] = json.loads(
                obj.PostProcessorPropertyOverrides
            )
        if obj.PostProcessorOutputFile:
            attrs[JobTemplate.PostProcessorOutputFile] = obj.PostProcessorOutputFile
        if hasattr(obj, "Machine") and obj.Machine:
            attrs[JobTemplate.Machine] = obj.Machine
        attrs[JobTemplate.GeometryTolerance] = str(obj.GeometryTolerance.Value)
        if obj.Description:
            attrs[JobTemplate.Description] = obj.Description
        return attrs

    def exportTemplateAttributes(
        self,
        obj,
        *,
        description=None,
        includePostProcessing=True,
        toolControllers=None,
        includeStock=True,
        includeStockExtent=True,
        includeStockPlacement=True,
        includeSettingToolRapid=True,
        includeSettingCoolant=True,
        includeSettingOperationHeights=True,
        includeSettingOperationDepths=True,
        includeSettingOperations=None,
    ):
        """Return the encoded version-1 template exported by the Job dialog.

        ``description=None`` retains the Job description. Supplying a string,
        including an empty string, applies the export-only dialog override.
        The helper is side-effect free and leaves path selection and file output
        to its caller.
        """

        attrs = self.templateAttrs(obj)
        if description is not None:
            value = str(description).strip()
            if value:
                attrs[JobTemplate.Description] = value
            else:
                attrs.pop(JobTemplate.Description, None)
        if not includePostProcessing:
            attrs.pop(JobTemplate.PostProcessor, None)
            attrs.pop(JobTemplate.PostProcessorArgs, None)
            attrs.pop(JobTemplate.PostProcessorOutputFile, None)
        controllers = (
            tuple(obj.Tools.Group or ())
            if toolControllers is None
            else tuple(toolControllers)
        )
        if controllers:
            attrs[JobTemplate.ToolController] = [
                controller.Proxy.templateAttrs(controller)
                for controller in controllers
            ]
        if includeStock:
            stockAttrs = PathStock.TemplateAttributes(
                obj.Stock,
                bool(includeStockExtent),
                bool(includeStockPlacement),
            )
            if stockAttrs:
                attrs[JobTemplate.Stock] = stockAttrs
        setupSheetAttrs = self.setupSheet.templateAttributes(
            bool(includeSettingToolRapid),
            bool(includeSettingCoolant),
            bool(includeSettingOperationHeights),
            bool(includeSettingOperationDepths),
            list(includeSettingOperations or ()),
        )
        if setupSheetAttrs:
            attrs[JobTemplate.SetupSheet] = setupSheetAttrs
        return self.setupSheet.encodeTemplateAttributes(attrs)

    def dumps(self):
        return None

    def loads(self, state):
        for obj in FreeCAD.ActiveDocument.Objects:
            if hasattr(obj, "Proxy") and obj.Proxy == self:
                self.obj = obj
                break
        return None

    def execute(self, obj):
        if not obj.GeometryTolerance:
            obj.GeometryTolerance = Path.Preferences.defaultGeometryTolerance()

        if getattr(obj, "Operations", None):
            # obj.Path = obj.Operations.Path
            self.getCycleTime()
            if hasattr(obj, "PathChanged"):
                obj.PathChanged = True

    def getCycleTime(self):
        seconds = 0

        if len(self.obj.Operations.Group):
            for op in self.obj.Operations.Group:

                # Skip inactive operations
                if PathUtil.opProperty(op, "Active") is False:
                    continue

                # Skip operations that don't have a cycletime attribute
                if PathUtil.opProperty(op, "CycleTime") is None:
                    continue

                formattedCycleTime = PathUtil.opProperty(op, "CycleTime")
                opCycleTime = 0
                try:
                    # Convert the formatted time from HH:MM:SS to just seconds
                    opCycleTime = sum(
                        x * int(t)
                        for x, t in zip([1, 60, 3600], reversed(formattedCycleTime.split(":")))
                    )
                except Exception:
                    continue

                if opCycleTime > 0:
                    seconds = seconds + opCycleTime

        cycleTimeString = time.strftime("%H:%M:%S", time.gmtime(seconds))
        self.obj.CycleTime = cycleTimeString

    def addOperation(self, op, before=None, removeBefore=False):
        PathUtil.restoreTimelineOperation(op)
        PathUtil.markTimelineParentJob(op, self.obj)
        group = self.obj.Operations.Group
        if op not in group:
            if before:
                try:
                    before_index = group.index(before)
                    if removeBefore:
                        PathUtil.markTimelineParentJob(
                            before,
                            self.obj,
                        )
                    group.insert(before_index, op)
                    if removeBefore:
                        group.remove(before)
                except Exception as e:
                    Path.Log.error(e)
                    group.append(op)
            else:
                group.append(op)
            self.obj.Operations.Group = group
            # op.Path.Center = self.obj.Operations.Path.Center

    def getMachine(self):
        """getMachine() ... returns an instantiated Machine object for this job.
        Returns None if no machine is configured or if the machine cannot be loaded.
        """
        # TODO: Once Machine property is added to Job, use it here
        # For now, return None since Machine property doesn't exist yet
        if not hasattr(self.obj, "Machine"):
            return None

        machine_name = self.obj.Machine
        if not machine_name:
            return None

        try:
            from Machine.models.machine import MachineFactory

            return MachineFactory.get_machine(machine_name)
        except Exception as e:
            Path.Log.error(f"Failed to load machine '{machine_name}': {e}")
            return None

    def nextToolNumber(self):
        # returns the next available toolnumber in the job
        group = self.obj.Tools.Group
        if len(group) > 0:
            return sorted([t.ToolNumber for t in group])[-1] + 1
        else:
            return 1

    def addToolController(self, tc):
        self.markToolControllerResource(tc)
        group = self.obj.Tools.Group
        Path.Log.debug("addToolController(%s): %s" % (tc.Label, [t.Label for t in group]))
        if tc.Name not in [str(t.Name) for t in group]:
            tc.setExpression(
                "VertRapid",
                "%s.%s"
                % (
                    self.obj.SetupSheet.Proxy.expressionReference(),
                    PathSetupSheet.Template.VertRapid,
                ),
            )
            tc.setExpression(
                "HorizRapid",
                "%s.%s"
                % (
                    self.obj.SetupSheet.Proxy.expressionReference(),
                    PathSetupSheet.Template.HorizRapid,
                ),
            )
            self.obj.Tools.addObject(tc)
            Notification.updateTC.emit(self.obj, tc)

    def markToolControllerResource(self, controller):
        """Attach a controller and its visual tool representation to this Job."""
        if controller is None:
            return
        PathUtil.markTimelineResource(controller, self.obj)
        tool = getattr(controller, "Tool", None)
        if tool is not None:
            PathUtil.markTimelineResourceTree(tool, self.obj)

    def setupTimelineTracking(self, obj):
        """Restore explicit history roles for a Job and all of its resources."""
        PathUtil.markTimelineOperation(obj)
        for resource in (
            getattr(obj, "Operations", None),
            getattr(obj, "SetupSheet", None),
            getattr(obj, "Model", None),
            getattr(obj, "Tools", None),
            getattr(obj, "Stock", None),
        ):
            if resource is not None:
                PathUtil.markTimelineResource(resource, obj)

        model = getattr(obj, "Model", None)
        for resource in getattr(model, "Group", ()):
            PathUtil.markTimelineResource(resource, obj)

        tools = getattr(obj, "Tools", None)
        for controller in getattr(tools, "Group", ()):
            self.markToolControllerResource(controller)

        operations = getattr(obj, "Operations", None)
        for operation in (
            *getattr(operations, "Group", ()),
            *self.allOperations(),
        ):
            PathUtil.restoreTimelineOperation(operation)

    def allOperations(self):
        ops = []

        def collectBaseOps(op):
            if hasattr(op, "TypeId"):
                if op.TypeId == "Path::FeaturePython":
                    ops.append(op)
                    if hasattr(op, "Base"):
                        collectBaseOps(op.Base)
                if op.TypeId == "Path::FeatureCompoundPython":
                    ops.append(op)
                    for sub in op.Group:
                        collectBaseOps(sub)

        if getattr(self.obj, "Operations", None) and getattr(self.obj.Operations, "Group", None):
            for op in self.obj.Operations.Group:
                collectBaseOps(op)

        return ops

    def setCenterOfRotation(self, center):
        if center != self.obj.Path.Center:
            job_path = self.obj.Path
            job_path.Center = center
            self.obj.Path = job_path
        for op in self.allOperations():
            if op.Path.Center != center:
                operation_path = op.Path
                operation_path.Center = center
                op.Path = operation_path

    def integrityCheck(self, job):
        """integrityCheck(job)... Return True if job has all expected children objects.  Attempts to restore any missing children."""
        suffix = ""
        if len(job.Name) > 3:
            suffix = job.Name[3:]

        def errorMessage(grp, job):
            Path.Log.error("{} corrupt in {} job.".format(grp, job.Name))

        if not job.Operations:
            self.setupOperations(job)
            job.Operations.Label = "Operations" + suffix
            if not job.Operations:
                errorMessage("Operations", job)
                return False
        if not job.SetupSheet:
            self.setupSetupSheet(job)
            job.SetupSheet.Label = "SetupSheet" + suffix
            if not job.SetupSheet:
                errorMessage("SetupSheet", job)
                return False
        if not job.Model:
            self.setupBaseModel(job)
            job.Model.Label = "Model" + suffix
            if not job.Model:
                errorMessage("Model", job)
                return False
        if not job.Stock:
            self.setupStock(job)
            job.Stock.Label = "Stock" + suffix
            if not job.Stock:
                errorMessage("Stock", job)
                return False
        if not job.Tools:
            self.setupToolTable(job)
            job.Tools.Label = "Tools" + suffix
            if not job.Tools:
                errorMessage("Tools", job)
                return False
        return True

    @classmethod
    def baseCandidates(cls):
        """Answer all objects in the current document which could serve as a Base for a job."""
        document = FreeCAD.ActiveDocument
        if document is None:
            return []
        return sorted(
            [
                obj
                for obj in document.Objects
                if cls.isBaseCandidate(obj)
                and is_timeline_input_usable(obj, document)
            ],
            key=lambda o: o.Label,
        )

    @classmethod
    def isBaseCandidate(cls, obj):
        """Answer true if the given object can be used as a Base for a job."""
        return PathUtil.isValidBaseObject(obj)


def Instances():
    """Instances() ... Return all Jobs in the current active document."""
    if FreeCAD.ActiveDocument:
        return [
            job
            for job in FreeCAD.ActiveDocument.Objects
            if hasattr(job, "Proxy") and isinstance(job.Proxy, ObjectJob)
        ]
    return []


def _Create(
    name,
    base,
    templateFile=None,
    createDefaultToolController=True,
    createDefaultStock=True,
    preparedStock=None,
):
    """Create(name, base, templateFile=None) ... creates a new job and all it's resources.
    If a template file is specified the new job is initialized with the values from the template."""
    if isinstance(base[0], str):
        document = FreeCAD.ActiveDocument
        if document is None:
            raise RuntimeError("A CAM Job requires a document")
        models = []
        for baseName in base:
            models.append(document.getObject(baseName))
    else:
        models = base
        document = base[0].Document
        if any(model.Document is not document for model in models):
            raise RuntimeError("A CAM Job cannot span multiple documents")

    atomic_publication = (
        document.getBookedTransactionID() != 0
        and hasattr(
            document,
            "publishProvisionalTimelineOperationBlock",
        )
    )
    obj = document.addObject("Path::FeaturePython", name)
    proxy = None
    try:
        obj.addExtension("App::GroupExtensionPython")
        proxy = ObjectJob(
            obj,
            models,
            templateFile,
            createDefaultToolController=createDefaultToolController,
            createDefaultStock=createDefaultStock and preparedStock is None,
            deferTimelinePublication=atomic_publication,
        )
        obj.Proxy = proxy
        if preparedStock is not None:
            if set(preparedStock) != {
                "shape",
                "source",
                "artifact_sha256",
                "shape_type",
                "topology",
            }:
                raise RuntimeError("A prepared CAM stock request is incomplete")
            obj.Stock = PathStock.CreateFromPreparedShape(
                obj,
                preparedStock["shape"],
                preparedStock["source"],
                preparedStock["artifact_sha256"],
                preparedStock["shape_type"],
                preparedStock["topology"],
            )
    except BaseException as creation_error:
        if atomic_publication and document.getObject(obj.Name) is obj:
            try:
                captured_proxy = proxy or getattr(obj, "Proxy", None)
                resources = (
                    captured_proxy.initialTimelineResources()
                    if isinstance(captured_proxy, ObjectJob)
                    else ()
                )
                document.publishProvisionalTimelineOperationBlock(
                    obj,
                    resources,
                )
                if isinstance(captured_proxy, ObjectJob):
                    captured_proxy._deferTimelinePublication = False
            except BaseException as tracking_error:
                raise tracking_error from creation_error
        raise

    if atomic_publication:
        document.publishProvisionalTimelineOperationBlock(
            obj,
            proxy.initialTimelineResources(),
        )
        proxy._deferTimelinePublication = False
    return obj


def Create(
    name,
    base,
    templateFile=None,
    createDefaultToolController=True,
    createDefaultStock=True,
):
    """Create a Job with the established default/template stock behavior."""

    return _Create(
        name,
        base,
        templateFile=templateFile,
        createDefaultToolController=createDefaultToolController,
        createDefaultStock=createDefaultStock,
    )


def CreateWithPreparedStock(
    name,
    base,
    *,
    shape,
    source,
    artifact_sha256,
    shape_type,
    topology,
    createDefaultToolController=True,
):
    """Create a Job whose initial stock is one verified solid snapshot."""

    return _Create(
        name,
        base,
        templateFile=None,
        createDefaultToolController=createDefaultToolController,
        createDefaultStock=False,
        preparedStock={
            "shape": shape,
            "source": source,
            "artifact_sha256": artifact_sha256,
            "shape_type": shape_type,
            "topology": topology,
        },
    )
