# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exhaustive VibeCAD behavior contracts for native Analyze ribbon actions.

These contracts describe the human ribbon behavior VibeCAD ships. They are
deliberately independent of historical FreeCAD command-lifecycle assumptions.
"""

import os
import tempfile
import unittest
from unittest import mock

import FemGui
import Fem
import FreeCAD as App
import FreeCADGui as Gui
import ObjectsFem
import Part
from PySide import QtCore, QtGui


REPRESENTATIVE_COMMANDS = (
    "FEM_Examples",  # global dialog
    "FEM_Analysis",  # document command
    "FEM_ConstraintSelfWeight",  # active-analysis command
    "FEM_MeshGmshFromShape",  # selection command
    "FEM_SolverCalculiX",  # solver command
)

MUTATING_COMMANDS = REPRESENTATIVE_COMMANDS[1:]

BASE_SHIPPED_COMMANDS = {
    "Model": (
        "FEM_Analysis",
        "FEM_MaterialSolid",
        "FEM_MaterialFluid",
        "FEM_MaterialMechanicalNonlinear",
        "FEM_MaterialReinforced",
        "FEM_MaterialEditor",
        "FEM_ElementGeometry1D",
        "FEM_ElementRotation1D",
        "FEM_ElementGeometry2D",
        "FEM_ElementFluid1D",
        "FEM_ConstantVacuumPermittivity",
    ),
    "Electromagnetic": (
        "FEM_CompEmConstraints",
        "FEM_ConstraintElectromagnetic",
        "FEM_ConstraintCurrentDensity",
        "FEM_ConstraintMagnetization",
        "FEM_ConstraintElectricChargeDensity",
    ),
    "Fluid": (
        "FEM_ConstraintInitialFlowVelocity",
        "FEM_ConstraintInitialPressure",
        "FEM_ConstraintFlowVelocity",
    ),
    "Geometry": (
        "FEM_ConstraintPlaneRotation",
        "FEM_ConstraintSectionPrint",
        "FEM_ConstraintTransform",
    ),
    "Mechanics": (
        "FEM_ConstraintFixed",
        "FEM_ConstraintRigidBody",
        "FEM_ConstraintDisplacement",
        "FEM_ConstraintContact",
        "FEM_ConstraintTie",
        "FEM_ConstraintSpring",
        "FEM_ConstraintForce",
        "FEM_ConstraintPressure",
        "FEM_ConstraintCentrif",
        "FEM_ConstraintSelfWeight",
    ),
    "Thermal": (
        "FEM_ConstraintInitialTemperature",
        "FEM_ConstraintHeatflux",
        "FEM_ConstraintTemperature",
        "FEM_ConstraintBodyHeatSource",
    ),
    "Mesh": (
        "FEM_MeshNetgenFromShape",
        "FEM_MeshGmshFromShape",
        "FEM_MeshRegion",
        "FEM_MeshGroup",
        "FEM_MeshGMSHRefinement",
        "FEM_MeshDistance",
        "FEM_MeshBoundaryLayer",
        "FEM_MeshShape",
        "FEM_MeshManipulate",
        "FEM_MeshAdvanced",
        "FEM_MeshTransfiniteCurve",
        "FEM_MeshTransfiniteSurface",
        "FEM_MeshTransfiniteVolume",
        "FEM_CreateElementsSet",
        "FEM_FEMMesh2Mesh",
    ),
    "Solve": (
        "FEM_CompSolvers",
        "FEM_SolverCalculiX",
        "FEM_SolverElmer",
        "FEM_SolverMystran",
        "FEM_SolverZ88",
        "FEM_CompMechEquations",
        "FEM_EquationElasticity",
        "FEM_EquationDeformation",
        "FEM_CompEmEquations",
        "FEM_EquationElectrostatic",
        "FEM_EquationElectricforce",
        "FEM_EquationMagnetodynamic",
        "FEM_EquationMagnetodynamic2D",
        "FEM_EquationStaticCurrent",
        "FEM_EquationFlow",
        "FEM_EquationFlux",
        "FEM_EquationHeat",
        "FEM_SolverControl",
        "FEM_SolverRun",
    ),
    "Utilities": (
        "FEM_ClippingPlaneAdd",
        "FEM_ClippingPlaneRemoveAll",
        "FEM_Examples",
    ),
}

VTK_COMMANDS = (
    "FEM_ResultsPurge",
    "FEM_ResultShow",
    "FEM_PostApplyChanges",
    "FEM_PostPipelineFromResult",
    "FEM_PostBranchFilter",
    "FEM_PostFilterWarp",
    "FEM_PostFilterClipScalar",
    "FEM_PostFilterCutFunction",
    "FEM_PostFilterClipRegion",
    "FEM_PostFilterContours",
    "FEM_PostFilterDataAlongLine",
    "FEM_PostFilterLinearizedStresses",
    "FEM_PostFilterDataAtPoint",
    "FEM_PostFilterCalculator",
    "FEM_PostCreateFunctions",
)

VTK_PYTHON_COMMANDS = (
    "FEM_PostFilterGlyph",
    "FEM_PostVisualization",
    "FEM_PostVisualizationTable",
    "FEM_PostVisualizationHistogram",
    "FEM_PostVisualizationLineplot",
)

POST_FUNCTION_ACTIONS = {
    "FEM_PostCreateFunctionPlane",
    "FEM_PostCreateFunctionSphere",
    "FEM_PostCreateFunctionCylinder",
    "FEM_PostCreateFunctionBox",
}

ANALYZE_COMPOSITE_ACTION_TARGETS = {
    "FEM_CompEmConstraints": (
        "FEM_ConstraintElectromagnetic",
        "FEM_ConstraintCurrentDensity",
        "FEM_ConstraintMagnetization",
        "FEM_ConstraintElectricChargeDensity",
    ),
    "FEM_MeshGMSHRefinement": (
        "FEM_MeshDistance",
        "FEM_MeshBoundaryLayer",
        "FEM_MeshShape",
        "FEM_MeshManipulate",
        "FEM_MeshAdvanced",
        "FEM_MeshTransfiniteCurve",
        "FEM_MeshTransfiniteSurface",
        "FEM_MeshTransfiniteVolume",
    ),
    "FEM_CompSolvers": (
        "FEM_SolverCalculiX",
        "FEM_SolverElmer",
        "FEM_SolverMystran",
        "FEM_SolverZ88",
    ),
    "FEM_CompMechEquations": (
        "FEM_EquationElasticity",
        "FEM_EquationDeformation",
    ),
    "FEM_CompEmEquations": (
        "FEM_EquationElectrostatic",
        "FEM_EquationElectricforce",
        "FEM_EquationMagnetodynamic",
        "FEM_EquationMagnetodynamic2D",
        "FEM_EquationStaticCurrent",
    ),
    "FEM_PostCreateFunctions": (
        "FEM_PostCreateFunctionPlane",
        "FEM_PostCreateFunctionSphere",
        "FEM_PostCreateFunctionCylinder",
        "FEM_PostCreateFunctionBox",
    ),
    "FEM_PostVisualization": (
        "FEM_PostVisualizationLineplot",
        "FEM_PostVisualizationHistogram",
        "FEM_PostVisualizationTable",
    ),
}

ANALYZE_BASE_TOOLBARS = {
    "Model": BASE_SHIPPED_COMMANDS["Model"][:-1],
    "Electromagnetic Boundary Conditions": ("FEM_CompEmConstraints",),
    "Fluid Boundary Conditions": BASE_SHIPPED_COMMANDS["Fluid"],
    "Geometrical Analysis Features": BASE_SHIPPED_COMMANDS["Geometry"],
    "Mechanical Boundary Conditions and Loads": BASE_SHIPPED_COMMANDS[
        "Mechanics"
    ],
    "Thermal Boundary Conditions and Loads": BASE_SHIPPED_COMMANDS["Thermal"],
    "Mesh": (
        "FEM_MeshNetgenFromShape",
        "FEM_MeshGmshFromShape",
        "FEM_MeshRegion",
        "FEM_MeshGroup",
        "FEM_MeshGMSHRefinement",
        "FEM_FEMMesh2Mesh",
    ),
    "Solve": (
        "FEM_CompSolvers",
        "FEM_CompMechEquations",
        "FEM_CompEmEquations",
        "FEM_EquationFlow",
        "FEM_EquationFlux",
        "FEM_EquationHeat",
        "FEM_SolverControl",
        "FEM_SolverRun",
    ),
    "Utilities": BASE_SHIPPED_COMMANDS["Utilities"],
}

POST_PREFERENCE_COMMAND = "FEM_PostApplyChanges"

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

ANALYZE_COMMAND_TIMELINE_BEHAVIOR = {
    "FEM_Analysis": frozenset(
        {"operation", "resource", "standalone"}
    ),
    "FEM_MaterialSolid": frozenset({"operation", "standalone"}),
    "FEM_MaterialFluid": frozenset({"operation", "standalone"}),
    "FEM_MaterialMechanicalNonlinear": frozenset({"operation", "source-preserving"}),
    "FEM_MaterialReinforced": frozenset({"operation", "standalone"}),
    "FEM_MaterialEditor": frozenset({"read-only"}),
    "FEM_ElementGeometry1D": frozenset({"operation", "standalone"}),
    "FEM_ElementRotation1D": frozenset({"operation", "standalone"}),
    "FEM_ElementGeometry2D": frozenset({"operation", "standalone"}),
    "FEM_ElementFluid1D": frozenset({"operation", "standalone"}),
    "FEM_ConstantVacuumPermittivity": frozenset(
        {"operation", "source-preserving"}
    ),
    "FEM_CompEmConstraints": frozenset({"read-only"}),
    "FEM_ConstraintElectromagnetic": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintCurrentDensity": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintMagnetization": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintElectricChargeDensity": frozenset(
        {"operation", "source-preserving"}
    ),
    "FEM_ConstraintInitialFlowVelocity": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintInitialPressure": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintFlowVelocity": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintPlaneRotation": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintSectionPrint": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintTransform": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintFixed": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintRigidBody": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintDisplacement": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintContact": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintTie": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintSpring": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintForce": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintPressure": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintCentrif": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintSelfWeight": frozenset({"operation", "standalone"}),
    "FEM_ConstraintInitialTemperature": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintHeatflux": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintTemperature": frozenset({"operation", "source-preserving"}),
    "FEM_ConstraintBodyHeatSource": frozenset({"operation", "source-preserving"}),
    "FEM_MeshNetgenFromShape": frozenset({"operation", "source-preserving"}),
    "FEM_MeshGmshFromShape": frozenset({"operation", "source-preserving"}),
    "FEM_MeshRegion": frozenset({"operation", "source-preserving"}),
    "FEM_MeshGroup": frozenset({"operation", "source-preserving"}),
    "FEM_MeshGMSHRefinement": frozenset({"read-only"}),
    "FEM_MeshDistance": frozenset({"operation", "source-preserving"}),
    "FEM_MeshBoundaryLayer": frozenset({"operation", "source-preserving"}),
    "FEM_MeshShape": frozenset({"operation", "source-preserving"}),
    "FEM_MeshManipulate": frozenset({"operation", "source-preserving"}),
    "FEM_MeshAdvanced": frozenset({"operation", "source-preserving"}),
    "FEM_MeshTransfiniteCurve": frozenset({"operation", "source-preserving"}),
    "FEM_MeshTransfiniteSurface": frozenset({"operation", "source-preserving"}),
    "FEM_MeshTransfiniteVolume": frozenset({"operation", "source-preserving"}),
    "FEM_CreateElementsSet": frozenset({"operation", "replacement"}),
    "FEM_FEMMesh2Mesh": frozenset({"operation", "replacement"}),
    "FEM_CompSolvers": frozenset({"read-only"}),
    "FEM_SolverCalculiX": frozenset({"operation", "standalone"}),
    "FEM_SolverElmer": frozenset({"operation", "standalone"}),
    "FEM_SolverMystran": frozenset({"operation", "standalone"}),
    "FEM_SolverZ88": frozenset({"operation", "standalone"}),
    "FEM_CompMechEquations": frozenset({"read-only"}),
    "FEM_EquationElasticity": frozenset({"operation", "source-preserving"}),
    "FEM_EquationDeformation": frozenset({"operation", "source-preserving"}),
    "FEM_CompEmEquations": frozenset({"read-only"}),
    "FEM_EquationElectrostatic": frozenset({"operation", "source-preserving"}),
    "FEM_EquationElectricforce": frozenset({"operation", "source-preserving"}),
    "FEM_EquationMagnetodynamic": frozenset({"operation", "source-preserving"}),
    "FEM_EquationMagnetodynamic2D": frozenset({"operation", "source-preserving"}),
    "FEM_EquationStaticCurrent": frozenset({"operation", "source-preserving"}),
    "FEM_EquationFlow": frozenset({"operation", "source-preserving"}),
    "FEM_EquationFlux": frozenset({"operation", "source-preserving"}),
    "FEM_EquationHeat": frozenset({"operation", "source-preserving"}),
    "FEM_SolverControl": frozenset({"in-place"}),
    "FEM_SolverRun": frozenset({"operation", "source-preserving"}),
    "FEM_ClippingPlaneAdd": frozenset({"read-only"}),
    "FEM_ClippingPlaneRemoveAll": frozenset({"read-only"}),
    "FEM_Examples": frozenset({"read-only"}),
    "FEM_ResultsPurge": frozenset({"in-place"}),
    "FEM_ResultShow": frozenset({"in-place"}),
    "FEM_PostApplyChanges": frozenset({"read-only"}),
    "FEM_PostPipelineFromResult": frozenset({"operation", "replacement"}),
    "FEM_PostBranchFilter": frozenset({"operation", "replacement"}),
    "FEM_PostFilterWarp": frozenset({"operation", "replacement"}),
    "FEM_PostFilterClipScalar": frozenset({"operation", "replacement"}),
    "FEM_PostFilterCutFunction": frozenset({"operation", "replacement"}),
    "FEM_PostFilterClipRegion": frozenset({"operation", "replacement"}),
    "FEM_PostFilterContours": frozenset({"operation", "replacement"}),
    "FEM_PostFilterDataAlongLine": frozenset({"operation", "source-preserving"}),
    "FEM_PostFilterLinearizedStresses": frozenset({"read-only"}),
    "FEM_PostFilterDataAtPoint": frozenset({"operation", "source-preserving"}),
    "FEM_PostFilterCalculator": frozenset({"operation", "replacement"}),
    "FEM_PostCreateFunctions": frozenset({"operation", "source-preserving"}),
    "FEM_PostFilterGlyph": frozenset({"operation", "replacement"}),
    "FEM_PostVisualization": frozenset({"read-only"}),
    "FEM_PostVisualizationTable": frozenset({"operation", "standalone"}),
    "FEM_PostVisualizationHistogram": frozenset({"operation", "standalone"}),
    "FEM_PostVisualizationLineplot": frozenset({"operation", "standalone"}),
    "FEM_PostCreateFunctionPlane": frozenset({"operation", "source-preserving"}),
    "FEM_PostCreateFunctionSphere": frozenset({"operation", "source-preserving"}),
    "FEM_PostCreateFunctionCylinder": frozenset({"operation", "source-preserving"}),
    "FEM_PostCreateFunctionBox": frozenset({"operation", "source-preserving"}),
}

PYTHON_TIMELINE_REPLAY_CONTRACTS = {
    "FEM_Analysis": (
        "_mark_timeline_operation",
        "publishProvisionalTimelineOperationBlock",
    ),
    "FEM_FEMMesh2Mesh": (
        "_mark_timeline_replaced_inputs",
    ),
    "FEM_PostFilterGlyph": (
        "_mark_timeline_replaced_inputs",
    ),
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


def _collect_named_menu_commands(menu_title):
    menu_action = next(
        (
            action
            for action in Gui.getMainWindow().menuBar().actions()
            if action.text().replace("&", "") == menu_title
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


PYTHON_ANALYSIS_TASK_COMMANDS = (
    "FEM_MaterialSolid",
    "FEM_MaterialFluid",
    "FEM_MaterialReinforced",
    "FEM_ElementGeometry1D",
    "FEM_ElementGeometry2D",
    "FEM_ElementFluid1D",
    "FEM_ConstraintElectromagnetic",
    "FEM_ConstraintCurrentDensity",
    "FEM_ConstraintMagnetization",
    "FEM_ConstraintElectricChargeDensity",
    "FEM_ConstraintInitialFlowVelocity",
    "FEM_ConstraintInitialPressure",
    "FEM_ConstraintFlowVelocity",
    "FEM_ConstraintSectionPrint",
    "FEM_ConstraintTie",
    "FEM_ConstraintCentrif",
    "FEM_ConstraintBodyHeatSource",
)

CPP_ANALYSIS_TASK_COMMANDS = (
    "FEM_ConstraintPlaneRotation",
    "FEM_ConstraintTransform",
    "FEM_ConstraintFixed",
    "FEM_ConstraintRigidBody",
    "FEM_ConstraintDisplacement",
    "FEM_ConstraintContact",
    "FEM_ConstraintSpring",
    "FEM_ConstraintForce",
    "FEM_ConstraintPressure",
    "FEM_ConstraintInitialTemperature",
    "FEM_ConstraintHeatflux",
    "FEM_ConstraintTemperature",
)

MESH_REFINEMENT_TASK_COMMANDS = (
    "FEM_MeshRegion",
    "FEM_MeshGroup",
    "FEM_MeshDistance",
    "FEM_MeshBoundaryLayer",
    "FEM_MeshShape",
    "FEM_MeshManipulate",
    "FEM_MeshAdvanced",
    "FEM_MeshTransfiniteCurve",
    "FEM_MeshTransfiniteSurface",
    "FEM_MeshTransfiniteVolume",
)

POST_FILTER_TASK_COMMANDS = (
    "FEM_PostBranchFilter",
    "FEM_PostFilterWarp",
    "FEM_PostFilterClipScalar",
    "FEM_PostFilterCutFunction",
    "FEM_PostFilterClipRegion",
    "FEM_PostFilterContours",
    "FEM_PostFilterDataAlongLine",
    "FEM_PostFilterDataAtPoint",
    "FEM_PostFilterCalculator",
)

DIRECT_SOLVER_COMMANDS = (
    "FEM_SolverCalculiX",
    "FEM_SolverElmer",
    "FEM_SolverMystran",
    "FEM_SolverZ88",
)

DIRECT_ANALYSIS_COMMANDS = (
    "FEM_ConstraintSelfWeight",
    "FEM_ElementRotation1D",
)

EQUATION_COMMANDS = (
    "FEM_EquationElasticity",
    "FEM_EquationDeformation",
    "FEM_EquationElectrostatic",
    "FEM_EquationElectricforce",
    "FEM_EquationMagnetodynamic",
    "FEM_EquationMagnetodynamic2D",
    "FEM_EquationStaticCurrent",
    "FEM_EquationFlow",
    "FEM_EquationFlux",
    "FEM_EquationHeat",
)

POST_FUNCTION_TYPES = (
    "Fem::FemPostPlaneFunction",
    "Fem::FemPostSphereFunction",
    "Fem::FemPostCylinderFunction",
    "Fem::FemPostBoxFunction",
)


class _BlockingTask:
    """Minimal task panel used to exercise the shared FEM command boundary."""

    def __init__(self):
        self.form = QtGui.QWidget()
        self.form.setWindowTitle("VibeCAD FEM command boundary")

    def accept(self):
        return True

    def reject(self):
        return True

    def getStandardButtons(self):
        return QtGui.QDialogButtonBox.Cancel


@unittest.skipIf(not App.GuiUp, "VibeCAD Analyze ribbon tests require the GUI")
class TestVibeCADFEMRibbonTools(unittest.TestCase):
    """FEM commands must not overlap another task or transaction owner."""

    maxDiff = None

    def setUp(self):
        Gui.activateWorkbench("FemWorkbench")
        self.extra_documents = []
        self.document = App.newDocument("VibeCADFEMRibbonTools")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)

        self.shape = self.document.addObject(
            "Part::Feature",
            "ContractShape",
        )
        self.shape.Shape = Part.makeBox(20.0, 16.0, 8.0)
        self.analysis = ObjectsFem.makeAnalysis(
            self.document,
            "ContractAnalysis",
        )
        FemGui.setActiveAnalysis(self.analysis)

        self.material = ObjectsFem.makeMaterialSolid(
            self.document,
            "ContractMaterial",
        )
        self.analysis.addObject(self.material)
        self.mesh = ObjectsFem.makeMeshGmsh(
            self.document,
            "ContractGmshMesh",
        )
        self.analysis.addObject(self.mesh)
        self.solver = ObjectsFem.makeSolverElmer(
            self.document,
            "ContractElmerSolver",
        )
        self.analysis.addObject(self.solver)
        self.result = ObjectsFem.makeResultMechanical(
            self.document,
            "ContractResult",
        )
        self.analysis.addObject(self.result)

        self.pipeline = None
        self.stress_line = None
        if "BUILD_FEM_VTK" in App.__cmake__:
            self.pipeline = self.document.addObject(
                "Fem::FemPostPipeline",
                "ContractPipeline",
            )
            self.analysis.addObject(self.pipeline)
            self.stress_line = self.document.addObject(
                "Fem::FemPostDataAlongLineFilter",
                "ContractStressLine",
            )
            self.stress_line.PlotData = "von Mises Stress"
            self.pipeline.addObject(self.stress_line)

        self.document.recompute()
        self._select_shape()

    def tearDown(self):
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
            self._process_events()

        Gui.Selection.clearSelection()
        for document in reversed(self.extra_documents):
            if document.Name not in App.listDocuments():
                continue
            booked = int(document.getBookedTransactionID())
            if booked:
                App.closeActiveTransaction(True, booked)
            App.closeDocument(document.Name)
        self.extra_documents = []
        if self.document is not None:
            booked = int(self.document.getBookedTransactionID())
            if booked:
                App.closeActiveTransaction(True, booked)
            if self.document.Name in App.listDocuments():
                App.closeDocument(self.document.Name)
        self.document = None
        self._process_events()

    @staticmethod
    def _process_events(rounds=3):
        for _ in range(rounds):
            Gui.updateGui()
            application = QtGui.QApplication.instance()
            if application is not None:
                application.processEvents(
                    QtCore.QEventLoop.AllEvents,
                    25,
                )

    def _select_shape(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.shape)
        self._process_events()

    def _select(self, obj=None):
        Gui.Selection.clearSelection()
        if obj is not None:
            Gui.Selection.addSelection(obj)
        self._process_events()

    def _cancel_active_task(self):
        self.assertTrue(Gui.Control.activeDialog())
        task = Gui.Control.activeTaskDialog()
        self.assertIsNotNone(task)
        task.reject()
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )

    def _visibility_state(self):
        return {
            obj.ID: bool(obj.ViewObject.Visibility)
            for obj in self.document.Objects
            if getattr(obj, "ViewObject", None) is not None
        }

    @staticmethod
    def _fem_mesh_signature(mesh):
        nodes = tuple(
            sorted(
                (
                    int(node_id),
                    float(point.x),
                    float(point.y),
                    float(point.z),
                )
                for node_id, point in mesh.Nodes.items()
            )
        )
        element_ids = tuple(
            sorted(
                {
                    *mesh.Edges,
                    *mesh.Faces,
                    *mesh.Volumes,
                }
            )
        )
        elements = tuple(
            (
                int(element_id),
                tuple(
                    int(node_id)
                    for node_id in mesh.getElementNodes(element_id)
                ),
            )
            for element_id in element_ids
        )
        return nodes, elements

    def _assert_replacement_contract(self, operation, source):
        self.assertEqual(operation.VibeCADTimelineRole, "operation")
        self.assertEqual(
            operation.getTypeIdOfProperty("VibeCADTimelineRole"),
            "App::PropertyString",
        )
        self.assertIn(
            "Hidden",
            operation.getEditorMode("VibeCADTimelineRole"),
        )
        self.assertEqual(
            list(operation.VibeCADTimelineReplacedInputs),
            [source],
        )
        self.assertEqual(
            operation.getTypeIdOfProperty(
                "VibeCADTimelineReplacedInputs"
            ),
            "App::PropertyLinkListHidden",
        )
        self.assertIn(
            "Hidden",
            operation.getEditorMode(
                "VibeCADTimelineReplacedInputs"
            ),
        )
        if "VibeCADTimelineOwner" in operation.PropertiesList:
            self.assertIsNone(operation.VibeCADTimelineOwner)

    def _assert_replacement_playback(self, operation, source):
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        operation_index = list(timeline.Operations).index(operation)
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
        self.assertTrue(previous.isEnabled())

        previous.click()
        self._process_events(20)
        self.assertEqual(timeline.Position, operation_index)
        self.assertTrue(source.ViewObject.Visibility)
        self.assertFalse(operation.ViewObject.Visibility)

        end.click()
        self._process_events(20)
        self.assertEqual(timeline.Position, len(timeline.Operations))
        self.assertFalse(source.ViewObject.Visibility)
        self.assertTrue(operation.ViewObject.Visibility)

    def _run_task_and_cancel(
        self,
        command_name,
        *,
        require_new_object=True,
    ):
        self._set_context_for(command_name)
        self.assertTrue(
            Gui.isCommandActive(command_name),
            command_name,
        )
        before = tuple(self.document.Objects)
        visibility = self._visibility_state()
        Gui.runCommand(command_name)
        self._process_events()
        self.assertTrue(
            Gui.Control.activeDialog(),
            command_name,
        )
        if require_new_object:
            self.assertGreater(
                len(self.document.Objects),
                len(before),
                command_name,
            )
        self._cancel_active_task()
        self.assertEqual(
            tuple(self.document.Objects),
            before,
            command_name,
        )
        self.assertEqual(
            self._visibility_state(),
            visibility,
            command_name,
        )

    @staticmethod
    def _registered_inventory():
        inventory = {
            command
            for group in BASE_SHIPPED_COMMANDS.values()
            for command in group
        }
        inventory.update(("FEM_ResultsPurge", "FEM_ResultShow"))
        if "BUILD_FEM_VTK" in App.__cmake__:
            inventory.update(VTK_COMMANDS)
        if "BUILD_FEM_VTK_PYTHON" in App.__cmake__:
            inventory.update(VTK_PYTHON_COMMANDS)
        return inventory

    @classmethod
    def _compiled_action_inventory(cls):
        inventory = cls._registered_inventory()
        if "BUILD_FEM_VTK" in App.__cmake__:
            inventory.update(POST_FUNCTION_ACTIONS)
        return inventory

    @staticmethod
    def _expected_toolbar_inventory():
        toolbars = {
            title: tuple(commands)
            for title, commands in ANALYZE_BASE_TOOLBARS.items()
        }
        results = [
            "FEM_ResultsPurge",
            "FEM_ResultShow",
        ]
        if "BUILD_FEM_VTK" in App.__cmake__:
            results.extend(
                (
                    "FEM_PostApplyChanges",
                    "FEM_PostPipelineFromResult",
                    "FEM_PostBranchFilter",
                    "FEM_PostFilterWarp",
                    "FEM_PostFilterClipScalar",
                    "FEM_PostFilterCutFunction",
                    "FEM_PostFilterClipRegion",
                    "FEM_PostFilterContours",
                )
            )
            if "BUILD_FEM_VTK_PYTHON" in App.__cmake__:
                results.append("FEM_PostFilterGlyph")
            results.extend(
                (
                    "FEM_PostFilterDataAlongLine",
                    "FEM_PostFilterLinearizedStresses",
                    "FEM_PostFilterDataAtPoint",
                    "FEM_PostFilterCalculator",
                    "FEM_PostCreateFunctions",
                )
            )
            if "BUILD_FEM_VTK_PYTHON" in App.__cmake__:
                results.append("FEM_PostVisualization")
        toolbars["Results"] = tuple(results)
        return toolbars

    def _set_context_for(self, command_name):
        if command_name in {
            "FEM_MeshNetgenFromShape",
            "FEM_MeshGmshFromShape",
        }:
            self._select(self.shape)
        elif command_name == "FEM_MaterialMechanicalNonlinear":
            self._select(self.material)
        elif command_name in {
            "FEM_MeshRegion",
            "FEM_MeshGroup",
            "FEM_MeshGMSHRefinement",
            "FEM_MeshDistance",
            "FEM_MeshBoundaryLayer",
            "FEM_MeshShape",
            "FEM_MeshManipulate",
            "FEM_MeshAdvanced",
            "FEM_MeshTransfiniteCurve",
            "FEM_MeshTransfiniteSurface",
            "FEM_MeshTransfiniteVolume",
            "FEM_CreateElementsSet",
            "FEM_FEMMesh2Mesh",
        }:
            self._select(self.mesh)
        elif command_name in {
            "FEM_CompMechEquations",
            "FEM_EquationElasticity",
            "FEM_EquationDeformation",
            "FEM_CompEmEquations",
            "FEM_EquationElectrostatic",
            "FEM_EquationElectricforce",
            "FEM_EquationMagnetodynamic",
            "FEM_EquationMagnetodynamic2D",
            "FEM_EquationStaticCurrent",
            "FEM_EquationFlow",
            "FEM_EquationFlux",
            "FEM_EquationHeat",
            "FEM_SolverControl",
            "FEM_SolverRun",
        }:
            self._select(self.solver)
        elif command_name in {
            "FEM_ResultShow",
            "FEM_PostPipelineFromResult",
        }:
            self._select(self.result)
        elif command_name == "FEM_PostFilterLinearizedStresses":
            self._select(self.stress_line)
        elif command_name in {
            "FEM_PostBranchFilter",
            "FEM_PostFilterWarp",
            "FEM_PostFilterClipScalar",
            "FEM_PostFilterCutFunction",
            "FEM_PostFilterClipRegion",
            "FEM_PostFilterContours",
            "FEM_PostFilterGlyph",
            "FEM_PostFilterDataAlongLine",
            "FEM_PostFilterDataAtPoint",
            "FEM_PostFilterCalculator",
        }:
            self._select(self.pipeline)
        else:
            self._select()

    def test_exact_compiled_ribbon_inventory_is_registered(self):
        command_inventory = self._registered_inventory()
        self.assertFalse(command_inventory - set(Gui.listCommands()))

        if "BUILD_FEM_VTK" in App.__cmake__:
            action_names = {
                action.objectName()
                for action in Gui.getMainWindow().findChildren(
                    QtGui.QAction
                )
            }
            self.assertFalse(POST_FUNCTION_ACTIONS - action_names)

        expected_toolbars = self._expected_toolbar_inventory()
        all_toolbar_items = Gui.activeWorkbench().getToolbarItems()
        live_toolbars = {
            title: tuple(
                command
                for command in commands
                if command != "Separator"
            )
            for title, commands in all_toolbar_items.items()
            if title not in STANDARD_TOOLBAR_TITLES
        }
        self.assertEqual(live_toolbars, expected_toolbars)

        toolbar_top_level = {
            command
            for commands in live_toolbars.values()
            for command in commands
        }
        live_composites = {}
        for command_name in sorted(toolbar_top_level):
            command = Gui.Command.get(command_name)
            self.assertIsNotNone(command, command_name)
            child_ids = tuple(
                _action_command_id(action)
                for action in command.getAction()
                if not action.isSeparator()
            )
            if len(child_ids) > 1:
                live_composites[command_name] = child_ids
        expected_composites = {
            parent: children
            for parent, children in ANALYZE_COMPOSITE_ACTION_TARGETS.items()
            if parent in toolbar_top_level
        }
        self.assertEqual(live_composites, expected_composites)

        menu_commands = set()
        for menu_title in ("Model", "Mesh", "Solve", "Results", "Utilities"):
            commands = _collect_named_menu_commands(menu_title)
            self.assertIsNotNone(commands, menu_title)
            menu_commands.update(commands)
        expected_surface = self._compiled_action_inventory()
        expected_menu = expected_surface - set(expected_composites)
        self.assertEqual(menu_commands, expected_menu)

        live_surface = toolbar_top_level | menu_commands
        live_surface.update(
            child
            for children in live_composites.values()
            for child in children
        )
        self.assertEqual(live_surface, expected_surface)

    def test_analyze_command_timeline_matrix_is_exhaustive_and_disjoint(self):
        full_action_catalog = {
            command
            for commands in BASE_SHIPPED_COMMANDS.values()
            for command in commands
        }
        full_action_catalog.update(VTK_COMMANDS)
        full_action_catalog.update(VTK_PYTHON_COMMANDS)
        full_action_catalog.update(POST_FUNCTION_ACTIONS)
        self.assertEqual(len(full_action_catalog), 97)
        self.assertEqual(
            set(ANALYZE_COMMAND_TIMELINE_BEHAVIOR),
            full_action_catalog,
        )

        primary_behaviors = {
            "standalone",
            "source-preserving",
            "replacement",
            "in-place",
            "read-only",
        }
        operation_behaviors = {
            "standalone",
            "source-preserving",
            "replacement",
        }
        for command, behaviors in ANALYZE_COMMAND_TIMELINE_BEHAVIOR.items():
            with self.subTest(command=command):
                primary = behaviors & primary_behaviors
                self.assertEqual(len(primary), 1)
                self.assertFalse(
                    behaviors
                    - primary_behaviors
                    - {"operation", "resource"}
                )
                self.assertEqual(
                    "operation" in behaviors,
                    bool(primary & operation_behaviors),
                )

        compiled_commands = self._registered_inventory()
        live_composites = {}
        for command_name in sorted(compiled_commands):
            command = Gui.Command.get(command_name)
            self.assertIsNotNone(command, command_name)
            child_ids = tuple(
                _action_command_id(action)
                for action in command.getAction()
                if not action.isSeparator()
            )
            if len(child_ids) > 1:
                live_composites[command_name] = child_ids
        self.assertEqual(
            live_composites,
            {
                parent: children
                for parent, children in (ANALYZE_COMPOSITE_ACTION_TARGETS.items())
                if parent in compiled_commands
            },
        )

        compiled_actions = set(compiled_commands)
        compiled_actions.update(
            child for children in live_composites.values() for child in children
        )
        self.assertEqual(
            compiled_actions,
            self._compiled_action_inventory(),
        )

        expected_compiled_count = (
            len(
                {
                    command
                    for commands in BASE_SHIPPED_COMMANDS.values()
                    for command in commands
                }
                | {"FEM_ResultsPurge", "FEM_ResultShow"}
            )
            + (
                len(set(VTK_COMMANDS) - {"FEM_ResultsPurge", "FEM_ResultShow"})
                + len(POST_FUNCTION_ACTIONS)
                if "BUILD_FEM_VTK" in App.__cmake__
                else 0
            )
            + (
                len(VTK_PYTHON_COMMANDS)
                if "BUILD_FEM_VTK_PYTHON" in App.__cmake__
                else 0
            )
        )
        self.assertEqual(len(compiled_actions), expected_compiled_count)
        self.assertEqual(
            set(ANALYZE_COMMAND_TIMELINE_BEHAVIOR) & compiled_actions,
            compiled_actions,
        )
        if "BUILD_FEM_VTK" not in App.__cmake__:
            self.assertTrue(
                (set(VTK_COMMANDS) - {"FEM_ResultsPurge", "FEM_ResultShow"}).isdisjoint(
                    compiled_actions
                )
            )
            self.assertTrue(POST_FUNCTION_ACTIONS.isdisjoint(compiled_actions))
        if "BUILD_FEM_VTK_PYTHON" not in App.__cmake__:
            self.assertTrue(set(VTK_PYTHON_COMMANDS).isdisjoint(compiled_actions))
        if "BUILD_FEM_VTK" in App.__cmake__ and "BUILD_FEM_VTK_PYTHON" in App.__cmake__:
            self.assertEqual(len(compiled_actions), 97)
            self.assertEqual(
                set(ANALYZE_COMMAND_TIMELINE_BEHAVIOR),
                compiled_actions,
            )

    def test_python_timeline_replay_contract_matrix_is_exhaustive(self):
        self.assertEqual(
            set(PYTHON_TIMELINE_REPLAY_CONTRACTS),
            {
                "FEM_Analysis",
                "FEM_FEMMesh2Mesh",
                "FEM_PostFilterGlyph",
            },
        )
        for command_name, tokens in (
            PYTHON_TIMELINE_REPLAY_CONTRACTS.items()
        ):
            with self.subTest(command_name=command_name):
                behaviors = ANALYZE_COMMAND_TIMELINE_BEHAVIOR[
                    command_name
                ]
                self.assertIn("operation", behaviors)
                self.assertTrue(tokens)

    def test_every_registered_ribbon_command_has_a_valid_context(self):
        from femcommands.commands import _netgen_backend_status

        netgen_available, _reason = _netgen_backend_status()
        for command_name in sorted(self._registered_inventory()):
            with self.subTest(command=command_name):
                self._set_context_for(command_name)
                self.assertEqual(
                    Gui.isCommandActive(command_name),
                    (
                        netgen_available
                        if command_name
                        == "FEM_MeshNetgenFromShape"
                        else True
                    ),
                    command_name,
                )

    def test_every_document_command_refuses_caller_transaction(self):
        for command_name in sorted(self._registered_inventory()):
            with self.subTest(command=command_name):
                self._set_context_for(command_name)
                if not Gui.isCommandActive(command_name):
                    self.assertEqual(
                        command_name,
                        "FEM_MeshNetgenFromShape",
                    )
                    continue
                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                before = tuple(self.document.Objects)
                before_undo = int(self.document.UndoCount)
                self.document.openTransaction(
                    f"Caller transaction for {command_name}"
                )
                transaction = int(
                    self.document.getBookedTransactionID()
                )
                self.assertNotEqual(transaction, 0)
                self._process_events()
                if command_name == POST_PREFERENCE_COMMAND:
                    self.assertTrue(
                        Gui.isCommandActive(command_name),
                        command_name,
                    )
                    App.closeActiveTransaction(True, transaction)
                    continue

                self.assertFalse(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                Gui.runCommand(command_name)
                self._process_events()
                self.assertEqual(tuple(self.document.Objects), before)
                self.assertEqual(int(self.document.UndoCount), before_undo)
                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    transaction,
                )
                self.assertFalse(
                    Gui.Control.activeDialog(),
                    command_name,
                )
                App.closeActiveTransaction(True, transaction)
                self.assertFalse(self.document.HasPendingTransaction)

    def _assert_representatives_active(self):
        self._select_shape()
        for command_name in REPRESENTATIVE_COMMANDS:
            self.assertTrue(
                Gui.isCommandActive(command_name),
                command_name,
            )

    def test_representative_families_are_eligible_in_valid_context(self):
        self._assert_representatives_active()

    def test_representative_families_refuse_a_caller_owned_transaction(self):
        self._assert_representatives_active()
        before_objects = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)

        self.document.openTransaction("Caller-owned FEM change")
        transaction = int(self.document.getBookedTransactionID())
        self.assertNotEqual(transaction, 0)
        self._process_events()

        for command_name in REPRESENTATIVE_COMMANDS:
            self.assertFalse(
                Gui.isCommandActive(command_name),
                command_name,
            )

        for command_name in MUTATING_COMMANDS:
            Gui.runCommand(command_name)
            self._process_events()
            self.assertEqual(
                tuple(self.document.Objects),
                before_objects,
                command_name,
            )
            self.assertEqual(
                int(self.document.getBookedTransactionID()),
                transaction,
                command_name,
            )
            self.assertEqual(
                int(self.document.UndoCount),
                before_undo,
                command_name,
            )
            self.assertFalse(
                Gui.Control.activeDialog(),
                command_name,
            )

        App.closeActiveTransaction(True, transaction)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self._assert_representatives_active()

    def test_representative_families_refuse_an_active_task(self):
        self._assert_representatives_active()
        before_objects = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)

        task = _BlockingTask()
        dialog = Gui.Control.showDialog(task)
        self.assertIsNotNone(dialog)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())

        for command_name in REPRESENTATIVE_COMMANDS:
            self.assertFalse(
                Gui.isCommandActive(command_name),
                command_name,
            )

        for command_name in MUTATING_COMMANDS:
            Gui.runCommand(command_name)
            self._process_events()
            self.assertEqual(
                tuple(self.document.Objects),
                before_objects,
                command_name,
            )
            self.assertEqual(
                int(self.document.UndoCount),
                before_undo,
                command_name,
            )
            self.assertEqual(
                self.document.getBookedTransactionID(),
                0,
                command_name,
            )
            self.assertTrue(
                Gui.Control.activeDialog(),
                command_name,
            )

        dialog.reject()
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog())
        self._assert_representatives_active()

    def test_every_document_command_refuses_an_active_task(self):
        before_objects = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)
        task = _BlockingTask()
        dialog = Gui.Control.showDialog(task)
        self.assertIsNotNone(dialog)
        self._process_events()

        for command_name in sorted(self._registered_inventory()):
            with self.subTest(command=command_name):
                self._set_context_for(command_name)
                if command_name == POST_PREFERENCE_COMMAND:
                    self.assertTrue(
                        Gui.isCommandActive(command_name),
                        command_name,
                    )
                    continue

                self.assertFalse(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                Gui.runCommand(command_name)
                self._process_events()
                self.assertEqual(
                    tuple(self.document.Objects),
                    before_objects,
                    command_name,
                )
                self.assertEqual(
                    int(self.document.UndoCount),
                    before_undo,
                    command_name,
                )
                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    0,
                    command_name,
                )
                self.assertTrue(
                    Gui.Control.activeDialog(),
                    command_name,
                )

        preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Fem"
        )
        original = preferences.GetBool("PostAutoRecompute", True)
        try:
            Gui.runCommand(POST_PREFERENCE_COMMAND, 0)
            self.assertFalse(
                preferences.GetBool("PostAutoRecompute", True)
            )
            Gui.runCommand(POST_PREFERENCE_COMMAND, 1)
            self.assertTrue(
                preferences.GetBool("PostAutoRecompute", False)
            )
        finally:
            preferences.SetBool("PostAutoRecompute", original)

        dialog.reject()
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog())

    def test_global_fem_dialog_does_not_require_a_document(self):
        name = self.document.Name
        Gui.Selection.clearSelection()
        App.closeDocument(name)
        self.document = None
        self._process_events()

        self.assertTrue(Gui.isCommandActive("FEM_Examples"))
        self.assertFalse(Gui.isCommandActive("FEM_Analysis"))

    def test_unknown_eligibility_state_is_safely_inactive(self):
        from femcommands.manager import CommandManager

        command = CommandManager()
        command.is_active = "future_state"
        self.assertFalse(command.IsActive())

    def test_selection_commands_require_live_objects_in_active_document(self):
        other = App.newDocument("VibeCADFEMOtherDocument")
        self.extra_documents.append(other)
        other_shape = other.addObject("Part::Feature", "OtherShape")
        other_shape.Shape = Part.makeBox(4.0, 5.0, 6.0)
        other_analysis = ObjectsFem.makeAnalysis(
            other,
            "OtherAnalysis",
        )
        other_mesh = ObjectsFem.makeMeshGmsh(other, "OtherMesh")
        other_analysis.addObject(other_mesh)
        other_solver = ObjectsFem.makeSolverElmer(
            other,
            "OtherSolver",
        )
        other_analysis.addObject(other_solver)
        other.recompute()

        App.setActiveDocument(self.document.Name)
        FemGui.setActiveAnalysis(self.analysis)
        self._select(other_shape)
        self.assertFalse(
            Gui.isCommandActive("FEM_MeshGmshFromShape")
        )

        self._select(other_mesh)
        self.assertFalse(
            Gui.isCommandActive("FEM_FEMMesh2Mesh")
        )

        FemGui.setActiveAnalysis(other_analysis)
        self._select()
        self.assertFalse(
            Gui.isCommandActive("FEM_ConstraintSelfWeight")
        )

        self._select(other_solver)
        self.assertFalse(
            Gui.isCommandActive("FEM_SolverControl")
        )

    def test_selected_solver_and_material_must_belong_to_active_analysis(self):
        other_analysis = ObjectsFem.makeAnalysis(
            self.document,
            "OtherAnalysisInDocument",
        )
        other_solver = ObjectsFem.makeSolverElmer(
            self.document,
            "OtherSolverInDocument",
        )
        other_material = ObjectsFem.makeMaterialSolid(
            self.document,
            "OtherMaterialInDocument",
        )
        other_analysis.addObject(other_solver)
        other_analysis.addObject(other_material)
        self.document.recompute()

        FemGui.setActiveAnalysis(self.analysis)
        self._select(other_solver)
        self.assertFalse(
            Gui.isCommandActive("FEM_SolverControl")
        )
        self.assertFalse(
            Gui.isCommandActive("FEM_EquationHeat")
        )

        self._select(other_material)
        self.assertFalse(
            Gui.isCommandActive(
                "FEM_MaterialMechanicalNonlinear"
            )
        )

        FemGui.setActiveAnalysis(other_analysis)
        self._select(other_solver)
        self.assertTrue(
            Gui.isCommandActive("FEM_SolverControl")
        )
        self.assertTrue(
            Gui.isCommandActive("FEM_EquationHeat")
        )
        self._select(other_material)
        self.assertTrue(
            Gui.isCommandActive(
                "FEM_MaterialMechanicalNonlinear"
            )
        )

    def test_removed_selection_and_analysis_are_never_reused(self):
        stale_mesh = ObjectsFem.makeMeshGmsh(
            self.document,
            "StaleMesh",
        )
        self._select(stale_mesh)
        self.assertTrue(
            Gui.isCommandActive("FEM_MeshRegion")
        )
        self.document.removeObject(stale_mesh.Name)
        self._process_events()
        before = tuple(self.document.Objects)
        self.assertFalse(
            Gui.isCommandActive("FEM_MeshRegion")
        )
        Gui.runCommand("FEM_MeshRegion")
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before)

        stale_analysis = ObjectsFem.makeAnalysis(
            self.document,
            "StaleAnalysis",
        )
        FemGui.setActiveAnalysis(stale_analysis)
        self.document.removeObject(stale_analysis.Name)
        self._process_events()
        self._select()
        self.assertFalse(
            Gui.isCommandActive("FEM_ConstraintSelfWeight")
        )
        before = tuple(self.document.Objects)
        Gui.runCommand("FEM_ConstraintSelfWeight")
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before)
        FemGui.setActiveAnalysis(self.analysis)

    def test_background_document_transaction_blocks_fem_commands(self):
        other = App.newDocument("VibeCADFEMBackgroundTransaction")
        self.extra_documents.append(other)
        App.setActiveDocument(self.document.Name)
        FemGui.setActiveAnalysis(self.analysis)
        self._select_shape()
        self.assertTrue(
            Gui.isCommandActive("FEM_ConstraintSelfWeight")
        )

        other.openTransaction("Caller-owned background work")
        transaction = int(other.getBookedTransactionID())
        self.assertNotEqual(transaction, 0)
        self._process_events()

        self.assertFalse(
            Gui.isCommandActive("FEM_ConstraintSelfWeight")
        )
        self.assertFalse(Gui.isCommandActive("FEM_Examples"))
        before = tuple(self.document.Objects)
        Gui.runCommand("FEM_ConstraintSelfWeight")
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(
            int(other.getBookedTransactionID()),
            transaction,
        )
        App.closeActiveTransaction(True, transaction)

    def test_successful_direct_commands_close_their_own_transaction(self):
        self._select()
        before = {obj.ID for obj in self.document.Objects}
        Gui.runCommand("FEM_ConstraintSelfWeight")
        self._process_events()
        created = [
            obj
            for obj in self.document.Objects
            if obj.ID not in before
        ]
        self.assertEqual(len(created), 1)
        self.assertIn(created[0], self.analysis.Group)
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )
        self.assertFalse(self.document.HasPendingTransaction)

    def test_purge_results_uses_one_native_history_aware_delete(self):
        import femresult.resulttools as resulttools

        targets = resulttools.purge_result_targets(self.analysis)
        expected = {self.result}
        if self.pipeline is not None:
            expected.update({self.pipeline, self.stress_line})
        self.assertEqual(set(targets), expected)
        self.assertNotIn(self.analysis, targets)
        self.assertNotIn(self.mesh, targets)

        target_identities = {
            target.Name: int(target.ID)
            for target in targets
        }
        pipeline_name = self.pipeline.Name if self.pipeline is not None else ""
        stress_line_name = (
            self.stress_line.Name
            if self.stress_line is not None
            else ""
        )
        undo_count = int(self.document.UndoCount)
        self.assertTrue(Gui.isCommandActive("FEM_ResultsPurge"))
        Gui.runCommand("FEM_ResultsPurge", 0)
        self._process_events()

        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertEqual(int(self.document.UndoCount), undo_count + 1)
        self.assertTrue(
            all(
                self.document.getObject(name) is None
                for name in target_identities
            )
        )
        self.assertIs(self.document.getObject(self.mesh.Name), self.mesh)
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertTrue(
            all(
                operation.Name not in target_identities
                for operation in timeline.Operations
            )
        )

        self.document.undo()
        self._process_events()
        restored = {
            name: self.document.getObject(name)
            for name in target_identities
        }
        self.assertTrue(all(restored.values()))
        self.assertEqual(
            {
                name: int(obj.ID)
                for name, obj in restored.items()
            },
            target_identities,
        )
        if self.pipeline is not None:
            self.assertIn(
                restored[stress_line_name],
                restored[pipeline_name].Group,
            )

    @unittest.skipUnless(
        "BUILD_FEM_VTK" in App.__cmake__,
        "Solver result graph contracts require VTK",
    )
    def test_solver_result_graph_survives_reopen_and_deletes_as_one_step(self):
        from femcommands.manager import (
            _finalize_timeline_result_graph,
        )

        transaction_id = 0
        try:
            self.document.openTransaction(
                "Import representative FEM solver results"
            )
            transaction_id = int(
                self.document.getBookedTransactionID()
            )
            self.assertNotEqual(transaction_id, 0)

            pipeline = self.document.addObject(
                "Fem::FemPostPipeline",
                "TrackedSolverResult",
            )
            output = self.document.addObject(
                "App::TextDocument",
                "TrackedSolverOutput",
            )
            output.Text = "solver output"
            self.analysis.addObject(pipeline)
            self.analysis.addObject(output)
            _finalize_timeline_result_graph(
                self.solver,
                pipeline,
                (output,),
            )
            results = list(self.solver.Results)
            results.extend((pipeline, output))
            self.solver.Results = results
            App.closeActiveTransaction(False, transaction_id)
            transaction_id = 0
        except Exception:
            if (
                transaction_id
                and self.document.getBookedTransactionID()
                == transaction_id
            ):
                App.closeActiveTransaction(True, transaction_id)
            raise

        timeline = self.document.getObject("VibeCADTimeline")
        self.assertEqual(
            list(timeline.Operations)[-2:],
            [output, pipeline],
        )
        self.assertEqual(pipeline.VibeCADTimelineRole, "operation")
        self.assertIs(pipeline.VibeCADResultSolver, self.solver)
        self.assertEqual(output.VibeCADTimelineRole, "resource")
        self.assertIs(output.VibeCADTimelineOwner, pipeline)

        names = {
            "analysis": self.analysis.Name,
            "solver": self.solver.Name,
            "pipeline": pipeline.Name,
            "output": output.Name,
        }
        identities = {
            name: int(self.document.getObject(object_name).ID)
            for name, object_name in names.items()
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            saved_file = os.path.join(
                temporary_directory,
                "fem_solver_result_graph.FCStd",
            )
            self.document.saveAs(saved_file)
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(saved_file)
            self._process_events(10)

            self.analysis = self.document.getObject(names["analysis"])
            self.solver = self.document.getObject(names["solver"])
            restored_pipeline = self.document.getObject(
                names["pipeline"]
            )
            restored_output = self.document.getObject(names["output"])
            FemGui.setActiveAnalysis(self.analysis)

            self.assertEqual(
                {
                    name: int(
                        self.document.getObject(object_name).ID
                    )
                    for name, object_name in names.items()
                },
                identities,
            )
            self.assertEqual(
                restored_pipeline.VibeCADTimelineRole,
                "operation",
            )
            self.assertIs(
                restored_pipeline.VibeCADResultSolver,
                self.solver,
            )
            self.assertEqual(
                restored_output.VibeCADTimelineRole,
                "resource",
            )
            self.assertIs(
                restored_output.VibeCADTimelineOwner,
                restored_pipeline,
            )
            restored_timeline = self.document.getObject(
                "VibeCADTimeline"
            )
            self.assertEqual(
                list(restored_timeline.Operations)[-2:],
                [restored_output, restored_pipeline],
            )

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(restored_pipeline)
            undo_count = int(self.document.UndoCount)
            Gui.runCommand("Std_Delete", 0)
            self._process_events()
            self.assertIsNone(
                self.document.getObject(names["pipeline"])
            )
            self.assertIsNone(
                self.document.getObject(names["output"])
            )
            self.assertIsNotNone(
                self.document.getObject(names["solver"])
            )
            self.assertEqual(
                int(self.document.UndoCount),
                undo_count + 1,
            )

            self.document.undo()
            self._process_events()
            restored_pipeline = self.document.getObject(
                names["pipeline"]
            )
            restored_output = self.document.getObject(names["output"])
            self.assertIsNotNone(restored_pipeline)
            self.assertIsNotNone(restored_output)
            self.assertIs(
                restored_output.VibeCADTimelineOwner,
                restored_pipeline,
            )

    @unittest.skipUnless(
        "BUILD_FEM_VTK" in App.__cmake__,
        "Existing solver result root contracts require VTK",
    )
    def test_rerun_can_add_an_exact_resource_to_its_existing_result_root(
        self,
    ):
        from femcommands.manager import (
            _finalize_timeline_result_graph,
        )

        def transact(label, action):
            transaction_id = 0
            try:
                self.document.openTransaction(label)
                transaction_id = int(
                    self.document.getBookedTransactionID()
                )
                self.assertNotEqual(transaction_id, 0)
                action()
                App.closeActiveTransaction(False, transaction_id)
                transaction_id = 0
            except Exception:
                if (
                    transaction_id
                    and self.document.getBookedTransactionID()
                    == transaction_id
                ):
                    App.closeActiveTransaction(
                        True,
                        transaction_id,
                    )
                raise

        root_holder = {}

        def create_root():
            root = self.document.addObject(
                "Fem::FemPostPipeline",
                "ExistingTrackedSolverResult",
            )
            self.analysis.addObject(root)
            _finalize_timeline_result_graph(
                self.solver,
                root,
            )
            root_holder["root"] = root

        transact("Create initial solver result", create_root)
        root = root_holder["root"]
        timeline = self.document.getObject("VibeCADTimeline")
        root_index = list(timeline.Operations).index(root)

        output_holder = {}

        def add_output():
            from femcommands.manager import (
                _stage_timeline_result_graph,
            )

            reconciliation = _stage_timeline_result_graph(root)
            output = self.document.addObject(
                "App::TextDocument",
                "LateTrackedSolverOutput",
            )
            self.analysis.addObject(output)
            _finalize_timeline_result_graph(
                self.solver,
                root,
                (output,),
                root_is_new=False,
                reconciliation=reconciliation,
            )
            output_holder["output"] = output

        transact("Attach later solver output", add_output)
        output = output_holder["output"]
        operations = list(timeline.Operations)
        self.assertEqual(operations.count(root), 1)
        self.assertEqual(
            operations[root_index:root_index + 2],
            [output, root],
        )
        self.assertEqual(output.VibeCADTimelineRole, "resource")
        self.assertIs(output.VibeCADTimelineOwner, root)

    def test_legacy_calculix_import_returns_its_exact_result_graph(self):
        import feminout.importCcxFrdResults as importCcxFrdResults
        from femcommands.manager import (
            _finalize_timeline_result_graph,
        )

        result_file = os.path.abspath(
            os.path.join(
                os.path.dirname(importCcxFrdResults.__file__),
                "..",
                "femtest",
                "data",
                "calculix",
                "box_static.frd",
            )
        )
        if not os.path.isfile(result_file):
            self.skipTest("The CalculiX result fixture is unavailable")

        solver = ObjectsFem.makeSolverCalculiXCcxTools(
            self.document,
            "TrackedLegacyCalculiX",
        )
        self.analysis.addObject(solver)
        preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Fem/General"
        )
        keep_results = preferences.GetBool(
            "KeepResultsOnReRun",
            False,
        )
        transaction_id = 0
        try:
            preferences.SetBool("KeepResultsOnReRun", True)
            self.document.openTransaction(
                "Import exact legacy CalculiX result graph"
            )
            transaction_id = int(
                self.document.getBookedTransactionID()
            )
            self.assertNotEqual(transaction_id, 0)
            legacy_result, root, resources, root_is_new = (
                importCcxFrdResults.importFrdResultGraph(
                    result_file,
                    self.analysis,
                    "TrackedCCX_",
                    "static",
                )
            )
            self.assertIsNotNone(legacy_result)
            self.assertIsNotNone(root)
            self.assertTrue(root_is_new)
            self.assertTrue(resources)
            exact_graph = [*resources, root]
            self.assertEqual(
                len({int(obj.ID) for obj in exact_graph}),
                len(exact_graph),
            )
            self.assertTrue(
                all(
                    obj.Document is self.document
                    and self.document.getObject(obj.Name) is obj
                    for obj in exact_graph
                )
            )
            _finalize_timeline_result_graph(
                solver,
                root,
                resources,
                root_is_new=root_is_new,
            )
            App.closeActiveTransaction(False, transaction_id)
            transaction_id = 0
        except Exception:
            if (
                transaction_id
                and self.document.getBookedTransactionID()
                == transaction_id
            ):
                App.closeActiveTransaction(True, transaction_id)
            raise
        finally:
            preferences.SetBool(
                "KeepResultsOnReRun",
                keep_results,
            )

        timeline = self.document.getObject("VibeCADTimeline")
        self.assertEqual(
            list(timeline.Operations)[-len(exact_graph):],
            exact_graph,
        )
        self.assertEqual(root.VibeCADTimelineRole, "operation")
        self.assertIs(root.VibeCADResultSolver, solver)
        for resource in resources:
            self.assertEqual(
                resource.VibeCADTimelineRole,
                "resource",
            )
            self.assertIs(resource.VibeCADTimelineOwner, root)

    def test_mystran_import_requires_and_tracks_the_returned_graph(self):
        from femcommands.manager import (
            _finalize_timeline_result_graph,
        )
        from femsolver.mystran import tasks as mystran_tasks

        solver = ObjectsFem.makeSolverMystran(
            self.document,
            "TrackedMystran",
        )
        self.analysis.addObject(solver)
        task = mystran_tasks.Results()
        task.solver = solver

        class ExactImporter:

            @staticmethod
            def import_neu(_filename):
                result = ObjectsFem.makeResultMechanical(
                    self.document,
                    "TrackedMystranResult",
                )
                result_mesh = ObjectsFem.makeMeshResult(
                    self.document,
                    "TrackedMystranResultMesh",
                )
                result.Mesh = result_mesh
                return result

        transaction_id = 0
        with tempfile.TemporaryDirectory() as temporary_directory:
            task.directory = temporary_directory
            result_path = os.path.join(
                temporary_directory,
                "tracked_mystran.NEU",
            )
            with open(result_path, "w", encoding="utf-8"):
                pass
            try:
                self.document.openTransaction(
                    "Import exact Mystran result graph"
                )
                transaction_id = int(
                    self.document.getBookedTransactionID()
                )
                self.assertNotEqual(transaction_id, 0)
                with (
                    mock.patch.object(
                        mystran_tasks,
                        "_inputFileName",
                        "tracked_mystran",
                    ),
                    mock.patch.object(
                        mystran_tasks,
                        "hfcMystranNeuIn",
                        ExactImporter,
                        create=True,
                    ),
                ):
                    root, resources = task.load_results()
                self.assertEqual(len(resources), 1)
                self.assertIs(root.Mesh, resources[0])
                _finalize_timeline_result_graph(
                    solver,
                    root,
                    resources,
                )
                App.closeActiveTransaction(False, transaction_id)
                transaction_id = 0
            except Exception:
                if (
                    transaction_id
                    and self.document.getBookedTransactionID()
                    == transaction_id
                ):
                    App.closeActiveTransaction(
                        True,
                        transaction_id,
                    )
                raise

        timeline = self.document.getObject("VibeCADTimeline")
        self.assertEqual(
            list(timeline.Operations)[-2:],
            [resources[0], root],
        )
        self.assertEqual(root.VibeCADTimelineRole, "operation")
        self.assertIs(root.VibeCADResultSolver, solver)
        self.assertEqual(
            resources[0].VibeCADTimelineRole,
            "resource",
        )
        self.assertIs(resources[0].VibeCADTimelineOwner, root)

    def test_mystran_rejects_a_nonprovisional_reported_resource(self):
        from femsolver.mystran import tasks as mystran_tasks

        solver = ObjectsFem.makeSolverMystran(
            self.document,
            "InvalidGraphMystran",
        )
        self.analysis.addObject(solver)
        stale_resource = self.document.addObject(
            "Fem::FeaturePython",
            "PreexistingMystranResource",
        )
        task = mystran_tasks.Results()
        task.solver = solver

        class InvalidGraphImporter:

            @staticmethod
            def import_neu(_filename):
                result = ObjectsFem.makeResultMechanical(
                    self.document,
                    "ReportedMystranResult",
                )
                return result, (stale_resource,)

        preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Fem/General"
        )
        keep_results = preferences.GetBool(
            "KeepResultsOnReRun",
            False,
        )
        before_ids = {obj.ID for obj in self.document.Objects}
        with tempfile.TemporaryDirectory() as temporary_directory:
            task.directory = temporary_directory
            result_path = os.path.join(
                temporary_directory,
                "invalid_graph_mystran.NEU",
            )
            with open(result_path, "w", encoding="utf-8"):
                pass
            try:
                preferences.SetBool("KeepResultsOnReRun", True)
                with (
                    mock.patch.object(
                        mystran_tasks,
                        "_inputFileName",
                        "invalid_graph_mystran",
                    ),
                    mock.patch.object(
                        mystran_tasks,
                        "hfcMystranNeuIn",
                        InvalidGraphImporter,
                        create=True,
                    ),
                    mock.patch.object(
                        mystran_tasks,
                        "result_reading",
                        True,
                    ),
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "invalid exact result resource",
                    ):
                        task.run()
            finally:
                preferences.SetBool(
                    "KeepResultsOnReRun",
                    keep_results,
                )

        self.assertEqual(
            {obj.ID for obj in self.document.Objects},
            before_ids,
        )
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )
        self.assertFalse(self.document.HasPendingTransaction)

    def test_direct_analysis_command_is_one_undoable_change(self):
        preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Fem/General"
        )
        original_solver = preferences.GetInt("DefaultSolver", 0)
        before_ids = {obj.ID for obj in self.document.Objects}
        try:
            preferences.SetInt("DefaultSolver", 0)
            self._select()
            original_do_command = Gui.doCommand
            with mock.patch.object(
                Gui,
                "doCommand",
                side_effect=original_do_command,
            ) as recorded:
                Gui.runCommand("FEM_Analysis")
            self._process_events()
            replay = "\n".join(
                str(call.args[0])
                for call in recorded.call_args_list
                if call.args
            )
            for token in PYTHON_TIMELINE_REPLAY_CONTRACTS[
                "FEM_Analysis"
            ]:
                self.assertIn(token, replay)
            created = [
                obj
                for obj in self.document.Objects
                if obj.ID not in before_ids
            ]
            self.assertEqual(len(created), 1)
            self.assertTrue(
                created[0].isDerivedFrom("Fem::FemAnalysis")
            )
            self.assertEqual(
                created[0].VibeCADTimelineRole,
                "operation",
            )
            self.assertIn(
                created[0],
                list(
                    self.document.getObject(
                        "VibeCADTimeline"
                    ).Operations
                ),
            )
            self.assertIs(FemGui.getActiveAnalysis(), created[0])
            self.assertEqual(
                int(self.document.getBookedTransactionID()),
                0,
            )
            self.assertFalse(self.document.HasPendingTransaction)

            analysis_name = created[0].Name
            self.document.undo()
            self._process_events()
            self.assertEqual(
                {obj.ID for obj in self.document.Objects},
                before_ids,
            )
            self.document.redo()
            self._process_events()
            restored_analysis = self.document.getObject(analysis_name)
            self.assertIsNotNone(restored_analysis)
            self.assertEqual(
                restored_analysis.VibeCADTimelineRole,
                "operation",
            )
            self.assertIn(
                restored_analysis,
                list(
                    self.document.getObject(
                        "VibeCADTimeline"
                    ).Operations
                ),
            )
        finally:
            preferences.SetInt("DefaultSolver", original_solver)
            FemGui.setActiveAnalysis(self.analysis)

    def test_default_solver_is_one_atomic_analysis_resource_block(self):
        preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Fem/General"
        )
        original_solver = preferences.GetInt("DefaultSolver", 0)
        before_ids = {obj.ID for obj in self.document.Objects}
        undo_before = int(self.document.UndoCount)
        try:
            # Elmer is a native solver type available in every shipped FEM
            # configuration and keeps this contract independent of external
            # solver binaries.
            preferences.SetInt("DefaultSolver", 2)
            self._select()
            original_do_command = Gui.doCommand
            with mock.patch.object(
                Gui,
                "doCommand",
                side_effect=original_do_command,
            ) as recorded:
                Gui.runCommand("FEM_Analysis")
            self._process_events(10)
            replay = "\n".join(
                str(call.args[0])
                for call in recorded.call_args_list
                if call.args
            )
            self.assertIn("_mark_timeline_operation", replay)
            self.assertIn("_mark_timeline_resource", replay)
            self.assertEqual(
                replay.count("publishProvisionalTimelineOperationBlock"),
                1,
            )
            self.assertNotIn("ActiveObject", replay)

            created = [
                obj
                for obj in self.document.Objects
                if obj.ID not in before_ids
            ]
            analyses = [
                obj
                for obj in created
                if obj.isDerivedFrom("Fem::FemAnalysis")
            ]
            solvers = [
                obj
                for obj in created
                if obj.isDerivedFrom("Fem::FemSolverObjectPython")
            ]
            self.assertEqual(len(analyses), 1)
            self.assertEqual(len(solvers), 1)
            analysis = analyses[0]
            solver = solvers[0]
            self.assertIn(solver, analysis.Group)
            self.assertEqual(analysis.VibeCADTimelineRole, "operation")
            self.assertEqual(solver.VibeCADTimelineRole, "resource")
            self.assertIs(solver.VibeCADTimelineOwner, analysis)

            timeline = self.document.getObject("VibeCADTimeline")
            operations = list(timeline.Operations)
            analysis_index = operations.index(analysis)
            self.assertGreater(analysis_index, 0)
            self.assertIs(operations[analysis_index - 1], solver)
            self.assertEqual(
                int(self.document.UndoCount),
                undo_before + 1,
            )

            history_items = Gui.getMainWindow().findChild(
                QtGui.QListWidget,
                "VibeCADFeatureTimelineItems",
            )
            self.assertIsNotNone(history_items)
            visible_names = {
                str(
                    history_items.item(row).data(
                        QtCore.Qt.UserRole,
                    )
                    or ""
                )
                for row in range(history_items.count())
            }
            self.assertIn(analysis.Name, visible_names)
            self.assertNotIn(solver.Name, visible_names)

            analysis_name = analysis.Name
            solver_name = solver.Name
            self.document.undo()
            self._process_events()
            self.assertEqual(
                {obj.ID for obj in self.document.Objects},
                before_ids,
            )
            self.assertIsNone(self.document.getObject(analysis_name))
            self.assertIsNone(self.document.getObject(solver_name))

            self.document.redo()
            self._process_events()
            analysis = self.document.getObject(analysis_name)
            solver = self.document.getObject(solver_name)
            self.assertIsNotNone(analysis)
            self.assertIsNotNone(solver)
            self.assertIn(solver, analysis.Group)
            self.assertEqual(solver.VibeCADTimelineRole, "resource")
            self.assertIs(solver.VibeCADTimelineOwner, analysis)
        finally:
            preferences.SetInt("DefaultSolver", original_solver)
            FemGui.setActiveAnalysis(self.analysis)

    def test_default_solver_factory_failure_rolls_back_whole_analysis(self):
        from femcommands import commands as fem_commands

        preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Fem/General"
        )
        original_solver = preferences.GetInt("DefaultSolver", 0)
        before_ids = {obj.ID for obj in self.document.Objects}
        before_undo = int(self.document.UndoCount)
        timeline = self.document.getObject("VibeCADTimeline")
        operations_before = tuple(timeline.Operations)
        create_solver = fem_commands.createDefaultSolverFeature

        def fail_after_creation(document, solver_name):
            create_solver(document, solver_name)
            raise RuntimeError("Injected default solver failure")

        try:
            preferences.SetInt("DefaultSolver", 2)
            command = fem_commands._Analysis()
            with mock.patch.object(
                fem_commands,
                "createDefaultSolverFeature",
                side_effect=fail_after_creation,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "default solver failure",
                ):
                    command.Activated()
            self._process_events()
            self.assertEqual(
                {obj.ID for obj in self.document.Objects},
                before_ids,
            )
            self.assertEqual(
                tuple(timeline.Operations),
                operations_before,
            )
            self.assertEqual(
                int(self.document.UndoCount),
                before_undo,
            )
            self.assertEqual(
                int(self.document.getBookedTransactionID()),
                0,
            )
            self.assertFalse(self.document.HasPendingTransaction)
            self.assertIs(FemGui.getActiveAnalysis(), self.analysis)
        finally:
            preferences.SetInt("DefaultSolver", original_solver)
            FemGui.setActiveAnalysis(self.analysis)

    def test_all_direct_analysis_features_are_undoable(self):
        for command_name in DIRECT_ANALYSIS_COMMANDS:
            with self.subTest(command=command_name):
                self._set_context_for(command_name)
                before_ids = {obj.ID for obj in self.document.Objects}
                Gui.runCommand(command_name)
                self._process_events()
                created = [
                    obj
                    for obj in self.document.Objects
                    if obj.ID not in before_ids
                ]
                self.assertEqual(len(created), 1, command_name)
                self.assertIn(
                    created[0],
                    self.analysis.Group,
                    command_name,
                )
                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    0,
                    command_name,
                )
                self.assertFalse(self.document.HasPendingTransaction)
                self.document.undo()
                self._process_events()
                self.assertEqual(
                    {obj.ID for obj in self.document.Objects},
                    before_ids,
                    command_name,
                )

    def test_all_equation_commands_target_the_selected_solver(self):
        for command_name in EQUATION_COMMANDS:
            with self.subTest(command=command_name):
                self._set_context_for(command_name)
                before_ids = {obj.ID for obj in self.document.Objects}
                Gui.runCommand(command_name)
                self._process_events()
                created = [
                    obj
                    for obj in self.document.Objects
                    if obj.ID not in before_ids
                ]
                self.assertEqual(len(created), 1, command_name)
                self.assertIn(
                    self.solver,
                    created[0].InList,
                    command_name,
                )
                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    0,
                    command_name,
                )
                self.document.undo()
                self._process_events()
                self.assertEqual(
                    {obj.ID for obj in self.document.Objects},
                    before_ids,
                    command_name,
                )

    def test_all_solver_creation_commands_are_undoable(self):
        for command_name in DIRECT_SOLVER_COMMANDS:
            with self.subTest(command=command_name):
                self._set_context_for(command_name)
                before_ids = {obj.ID for obj in self.document.Objects}
                Gui.runCommand(command_name)
                self._process_events()
                created = [
                    obj
                    for obj in self.document.Objects
                    if obj.ID not in before_ids
                ]
                self.assertEqual(len(created), 1, command_name)
                self.assertTrue(
                    created[0].isDerivedFrom(
                        "Fem::FemSolverObjectPython"
                    ),
                    command_name,
                )
                self.assertIn(
                    created[0],
                    self.analysis.Group,
                    command_name,
                )
                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    0,
                    command_name,
                )
                self.document.undo()
                self._process_events()
                self.assertEqual(
                    {obj.ID for obj in self.document.Objects},
                    before_ids,
                    command_name,
                )

    def test_nonlinear_material_creation_is_linked_and_undoable(self):
        self._set_context_for("FEM_MaterialMechanicalNonlinear")
        before_ids = {obj.ID for obj in self.document.Objects}
        Gui.runCommand("FEM_MaterialMechanicalNonlinear")
        self._process_events()
        created = [
            obj
            for obj in self.document.Objects
            if obj.ID not in before_ids
        ]
        self.assertEqual(len(created), 1)
        self.assertIs(self.material.Nonlinear, created[0])
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )
        self.document.undo()
        self._process_events()
        self.assertEqual(
            {obj.ID for obj in self.document.Objects},
            before_ids,
        )
        self.assertIsNone(self.material.Nonlinear)

    def test_factory_failure_rolls_back_partial_objects(self):
        original = ObjectsFem.makeConstraintSelfWeight

        def broken_factory(document, *args, **kwargs):
            del args, kwargs
            document.addObject(
                "Fem::FeaturePython",
                "PartialGravity",
            )
            raise RuntimeError("intentional FEM factory failure")

        before = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)
        ObjectsFem.makeConstraintSelfWeight = broken_factory
        try:
            self._select()
            Gui.runCommand("FEM_ConstraintSelfWeight")
            self._process_events()
        finally:
            ObjectsFem.makeConstraintSelfWeight = original

        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(int(self.document.UndoCount), before_undo)

        self._select(self.solver)
        before = {obj.ID for obj in self.document.Objects}
        Gui.runCommand("FEM_EquationHeat")
        self._process_events()
        self.assertTrue(
            [
                obj
                for obj in self.document.Objects
                if obj.ID not in before
            ]
        )
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )
        self.assertFalse(self.document.HasPendingTransaction)

    def test_constraint_and_mesh_task_cancel_remove_provisional_objects(self):
        self._select()
        before = tuple(self.document.Objects)
        Gui.runCommand("FEM_ConstraintFixed")
        self._process_events()
        self.assertGreater(len(self.document.Objects), len(before))
        self._cancel_active_task()
        self.assertEqual(tuple(self.document.Objects), before)

    def test_netgen_command_matches_the_selected_backend_capability(self):
        from femcommands.commands import _netgen_backend_status

        self._select_shape()
        available, reason = _netgen_backend_status()
        self.assertEqual(
            Gui.isCommandActive("FEM_MeshNetgenFromShape"),
            available,
        )
        if available:
            return

        actions = Gui.Command.get(
            "FEM_MeshNetgenFromShape"
        ).getAction()
        self.assertTrue(actions)
        tooltip = actions[0].toolTip()
        self.assertIn("unavailable", tooltip.lower())
        for phrase in reason.split():
            self.assertIn(phrase, tooltip)

        before = tuple(self.document.Objects)
        Gui.runCommand("FEM_MeshNetgenFromShape")
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )

    def test_cancel_restores_pre_command_visibility_for_distinct_task_types(self):
        """Task ViewProviders may preview visibility but Cancel must undo it."""

        for command_name in (
            "FEM_MaterialSolid",
            "FEM_MeshGmshFromShape",
        ):
            with self.subTest(command=command_name):
                self.shape.ViewObject.Visibility = True
                self.material.ViewObject.Visibility = False
                self.mesh.ViewObject.Visibility = True
                self.result.ViewObject.Visibility = False
                if self.pipeline is not None:
                    self.pipeline.ViewObject.Visibility = True
                self._process_events()

                before = self._visibility_state()
                self._set_context_for(command_name)
                Gui.runCommand(command_name)
                self._process_events()
                self.assertTrue(
                    Gui.Control.activeDialog(),
                    command_name,
                )
                self.assertFalse(
                    self.mesh.ViewObject.Visibility,
                    command_name,
                )

                self._cancel_active_task()
                self.assertEqual(
                    self._visibility_state(),
                    before,
                    command_name,
                )

    def test_all_analysis_task_commands_cancel_without_orphans(self):
        commands = (
            PYTHON_ANALYSIS_TASK_COMMANDS
            + CPP_ANALYSIS_TASK_COMMANDS
        )
        for command_name in commands:
            with self.subTest(command=command_name):
                self._run_task_and_cancel(command_name)

    def test_all_mesh_refinement_tasks_cancel_without_orphans(self):
        for command_name in MESH_REFINEMENT_TASK_COMMANDS:
            with self.subTest(command=command_name):
                refinements = tuple(self.mesh.MeshRefinementList)
                groups = tuple(self.mesh.MeshGroupList)
                self._run_task_and_cancel(command_name)
                self.assertEqual(
                    tuple(self.mesh.MeshRefinementList),
                    refinements,
                    command_name,
                )
                self.assertEqual(
                    tuple(self.mesh.MeshGroupList),
                    groups,
                    command_name,
                )

    def test_available_shape_mesh_tasks_cancel_without_orphans(self):
        for command_name in (
            "FEM_MeshNetgenFromShape",
            "FEM_MeshGmshFromShape",
        ):
            with self.subTest(command=command_name):
                self._set_context_for(command_name)
                if not Gui.isCommandActive(command_name):
                    continue
                self._run_task_and_cancel(command_name)

    def test_fem_mesh_conversion_creates_only_the_requested_mesh(self):
        source = Fem.FemMesh()
        source.addNode(0.0, 0.0, 0.0, 1)
        source.addNode(5.0, 0.0, 0.0, 2)
        source.addNode(0.0, 4.0, 0.0, 3)
        source.addFace([1, 2, 3], 1)
        self.mesh.FemMesh = source
        source_signature = self._fem_mesh_signature(
            self.mesh.FemMesh
        )
        self.mesh.ViewObject.Visibility = True
        self._select(self.mesh)

        before_ids = {obj.ID for obj in self.document.Objects}
        undo_before = int(self.document.UndoCount)
        original_do_command = Gui.doCommand
        with mock.patch.object(
            Gui,
            "doCommand",
            side_effect=original_do_command,
        ) as recorded:
            Gui.runCommand("FEM_FEMMesh2Mesh")
        self._process_events()
        replay = "\n".join(
            str(call.args[0])
            for call in recorded.call_args_list
            if call.args
        )
        for token in PYTHON_TIMELINE_REPLAY_CONTRACTS[
            "FEM_FEMMesh2Mesh"
        ]:
            self.assertIn(token, replay)
        created = [
            obj
            for obj in self.document.Objects
            if obj.ID not in before_ids
        ]
        self.assertEqual(len(created), 1)
        converted = created[0]
        self.assertTrue(converted.isDerivedFrom("Mesh::Feature"))
        self.assertEqual(converted.Mesh.CountFacets, 1)
        self.assertFalse(self.mesh.ViewObject.Visibility)
        self.assertEqual(
            self._fem_mesh_signature(self.mesh.FemMesh),
            source_signature,
        )
        self._assert_replacement_contract(converted, self.mesh)
        self.assertEqual(
            self._fem_mesh_signature(self.mesh.FemMesh),
            source_signature,
        )
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )
        self.assertEqual(
            int(self.document.UndoCount),
            undo_before + 1,
        )

        converted_name = converted.Name
        converted_id = int(converted.ID)
        self.document.undo()
        self._process_events()
        self.assertEqual(
            {obj.ID for obj in self.document.Objects},
            before_ids,
        )
        self.assertTrue(self.mesh.ViewObject.Visibility)

        self.document.redo()
        self._process_events()
        converted = self.document.getObject(converted_name)
        self.assertIsNotNone(converted)
        self.assertEqual(int(converted.ID), converted_id)
        self._assert_replacement_contract(converted, self.mesh)
        self._assert_replacement_playback(converted, self.mesh)

        self._select_shape()
        before = tuple(self.document.Objects)
        Gui.runCommand("FEM_MeshGmshFromShape")
        self._process_events()
        self.assertGreater(len(self.document.Objects), len(before))
        self._cancel_active_task()
        self.assertEqual(tuple(self.document.Objects), before)

    def test_python_mesh_replacement_survives_reopen_and_deletes_cleanly(
        self,
    ):
        source_mesh = Fem.FemMesh()
        source_mesh.addNode(0.0, 0.0, 0.0, 1)
        source_mesh.addNode(5.0, 0.0, 0.0, 2)
        source_mesh.addNode(0.0, 4.0, 0.0, 3)
        source_mesh.addFace([1, 2, 3], 1)
        self.mesh.FemMesh = source_mesh
        self.mesh.ViewObject.Visibility = True
        self._select(self.mesh)

        before_ids = {obj.ID for obj in self.document.Objects}
        Gui.runCommand("FEM_FEMMesh2Mesh")
        self._process_events()
        converted = [
            obj
            for obj in self.document.Objects
            if obj.ID not in before_ids
            and obj.isDerivedFrom("Mesh::Feature")
        ]
        self.assertEqual(len(converted), 1)
        converted = converted[0]
        self._assert_replacement_contract(converted, self.mesh)

        names = {
            "analysis": self.analysis.Name,
            "source": self.mesh.Name,
            "operation": converted.Name,
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            saved_file = os.path.join(
                temporary_directory,
                "fem_python_mesh_replacement.FCStd",
            )
            self.document.saveAs(saved_file)
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(saved_file)
            self._process_events(10)

            self.analysis = self.document.getObject(names["analysis"])
            self.mesh = self.document.getObject(names["source"])
            converted = self.document.getObject(names["operation"])
            FemGui.setActiveAnalysis(self.analysis)
            self._assert_replacement_contract(converted, self.mesh)
            self.assertFalse(self.mesh.ViewObject.Visibility)
            self.assertTrue(converted.ViewObject.Visibility)

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(converted)
            undo_count = int(self.document.UndoCount)
            Gui.runCommand("Std_Delete", 0)
            self._process_events()
            self.assertIsNone(
                self.document.getObject(names["operation"])
            )
            self.assertIsNotNone(
                self.document.getObject(names["source"])
            )
            self.assertTrue(self.mesh.ViewObject.Visibility)
            self.assertEqual(
                int(self.document.UndoCount),
                undo_count + 1,
            )

            self.document.undo()
            self._process_events()
            converted = self.document.getObject(names["operation"])
            self.assertIsNotNone(converted)
            self.assertFalse(self.mesh.ViewObject.Visibility)
            self.assertTrue(converted.ViewObject.Visibility)
            self._assert_replacement_contract(converted, self.mesh)

    def test_clipping_tools_handle_flat_and_empty_documents(self):
        from pivy import coin

        self.shape.Shape = Part.makePlane(20.0, 16.0)
        self.document.recompute()
        scene = Gui.getDocument(
            self.document.Name
        ).ActiveView.getSceneGraph()

        def clip_count(scene_graph):
            return sum(
                isinstance(node, coin.SoClipPlane)
                for node in scene_graph.getChildren()
            )

        before = clip_count(scene)
        Gui.runCommand("FEM_ClippingPlaneAdd")
        self._process_events()
        self.assertEqual(clip_count(scene), before + 1)
        Gui.runCommand("FEM_ClippingPlaneRemoveAll")
        self._process_events()
        self.assertEqual(clip_count(scene), 0)

        empty = App.newDocument("VibeCADFEMEmptyClipping")
        self.extra_documents.append(empty)
        Gui.activateView("Gui::View3DInventor", True)
        empty_scene = Gui.getDocument(
            empty.Name
        ).ActiveView.getSceneGraph()
        empty_before = clip_count(empty_scene)
        self.assertTrue(Gui.isCommandActive("FEM_ClippingPlaneAdd"))
        Gui.runCommand("FEM_ClippingPlaneAdd")
        self._process_events()
        self.assertEqual(
            clip_count(empty_scene),
            empty_before,
        )

        App.setActiveDocument(self.document.Name)
        FemGui.setActiveAnalysis(self.analysis)

    @unittest.skipUnless(
        "BUILD_FEM_VTK" in App.__cmake__,
        "Post-processing contracts require VTK",
    )
    def test_all_post_filter_tasks_use_the_selected_pipeline_and_cancel(self):
        second = self.document.addObject(
            "Fem::FemPostPipeline",
            "FilterTargetPipeline",
        )
        self.analysis.addObject(second)
        self.document.recompute()
        commands = list(POST_FILTER_TASK_COMMANDS)
        if "BUILD_FEM_VTK_PYTHON" in App.__cmake__:
            commands.append("FEM_PostFilterGlyph")

        for command_name in commands:
            with self.subTest(command=command_name):
                self._select(second)
                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                before = tuple(self.document.Objects)
                visibility = self._visibility_state()
                if command_name == "FEM_PostFilterGlyph":
                    original_do_command = Gui.doCommand
                    with mock.patch.object(
                        Gui,
                        "doCommand",
                        side_effect=original_do_command,
                    ) as recorded:
                        Gui.runCommand(command_name)
                else:
                    recorded = None
                    Gui.runCommand(command_name)
                self._process_events()
                if recorded is not None:
                    replay = "\n".join(
                        str(call.args[0])
                        for call in recorded.call_args_list
                        if call.args
                    )
                    for token in (
                        PYTHON_TIMELINE_REPLAY_CONTRACTS[
                            command_name
                        ]
                    ):
                        self.assertIn(token, replay)
                self.assertTrue(
                    Gui.Control.activeDialog(),
                    command_name,
                )
                created = [
                    obj
                    for obj in self.document.Objects
                    if obj not in before
                ]
                self.assertEqual(len(created), 1, command_name)
                self.assertIn(
                    created[0],
                    second.Group,
                    command_name,
                )
                self.assertNotIn(
                    created[0],
                    self.pipeline.Group,
                    command_name,
                )
                self._cancel_active_task()
                self.assertEqual(
                    tuple(self.document.Objects),
                    before,
                    command_name,
                )
                self.assertEqual(
                    self._visibility_state(),
                    visibility,
                    command_name,
                )

    @unittest.skipUnless(
        "BUILD_FEM_VTK" in App.__cmake__,
        "Post-processing contracts require VTK",
    )
    def test_accepted_post_filter_replaces_its_exact_pipeline_input(self):
        # A bare pipeline is group infrastructure rather than a timeline
        # operation. Create and accept one real post operation first so the
        # filter under test has an exact, active predecessor to restore.
        self.pipeline.ViewObject.Visibility = True
        self._select(self.pipeline)
        Gui.runCommand("FEM_PostBranchFilter")
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        predecessor = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "Fem::FemPostBranchFilter"
        ]
        self.assertEqual(len(predecessor), 1)
        predecessor = predecessor[0]
        Gui.Control.activeTaskDialog().accept()
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog())
        self.assertTrue(predecessor.ViewObject.Visibility)

        self._select(predecessor)
        before_ids = {obj.ID for obj in self.document.Objects}

        Gui.runCommand("FEM_PostBranchFilter")
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        created = [
            obj
            for obj in self.document.Objects
            if obj.ID not in before_ids
            and obj.TypeId == "Fem::FemPostBranchFilter"
        ]
        self.assertEqual(len(created), 1)
        post_filter = created[0]
        self._assert_replacement_contract(
            post_filter,
            predecessor,
        )

        Gui.Control.activeTaskDialog().accept()
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )
        self.assertFalse(predecessor.ViewObject.Visibility)
        self.assertTrue(post_filter.ViewObject.Visibility)
        self._assert_replacement_playback(
            post_filter,
            predecessor,
        )

    @unittest.skipUnless(
        "BUILD_FEM_VTK" in App.__cmake__,
        "Post-processing contracts require VTK",
    )
    def test_ambiguous_post_object_never_selects_a_first_pipeline(self):
        second = self.document.addObject(
            "Fem::FemPostPipeline",
            "AmbiguousSecondPipeline",
        )
        self.analysis.addObject(second)
        shared = self.document.addObject(
            "Fem::FemPostContoursFilter",
            "AmbiguousSharedFilter",
        )
        self.pipeline.addObject(shared)
        second.addObject(shared)
        self.document.recompute()
        self.assertIn(self.pipeline, shared.InList)
        self.assertIn(second, shared.InList)
        self._select(shared)

        for command_name in POST_FILTER_TASK_COMMANDS:
            with self.subTest(command=command_name):
                self.assertFalse(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
        if "BUILD_FEM_VTK_PYTHON" in App.__cmake__:
            self.assertFalse(
                Gui.isCommandActive("FEM_PostFilterGlyph")
            )
        self.assertFalse(
            Gui.isCommandActive("FEM_PostCreateFunctions")
        )

    @unittest.skipUnless(
        "BUILD_FEM_VTK" in App.__cmake__,
        "Post-processing contracts require VTK",
    )
    def test_all_post_function_types_are_finite_and_cancel_cleanly(self):
        for action_index, type_id in enumerate(POST_FUNCTION_TYPES):
            with self.subTest(type_id=type_id):
                self._select(self.pipeline)
                before = tuple(self.document.Objects)
                Gui.runCommand(
                    "FEM_PostCreateFunctions",
                    action_index,
                )
                self._process_events()
                self.assertTrue(Gui.Control.activeDialog())
                functions = [
                    obj
                    for obj in self.document.Objects
                    if obj not in before
                    and obj.TypeId == type_id
                ]
                self.assertEqual(len(functions), 1)
                for property_name in functions[0].PropertiesList:
                    value = getattr(functions[0], property_name)
                    if isinstance(value, float):
                        self.assertNotEqual(value, float("inf"))
                        self.assertNotEqual(value, float("-inf"))
                self._cancel_active_task()
                self.assertEqual(
                    tuple(self.document.Objects),
                    before,
                )

    @unittest.skipUnless(
        "BUILD_FEM_VTK_PYTHON" in App.__cmake__,
        "Python post-processing contracts require VTK Python",
    )
    def test_all_post_visualization_tasks_cancel_without_orphans(self):
        for command_name in (
            "FEM_PostVisualizationTable",
            "FEM_PostVisualizationHistogram",
            "FEM_PostVisualizationLineplot",
        ):
            with self.subTest(command=command_name):
                self._run_task_and_cancel(command_name)

    @unittest.skipUnless(
        "BUILD_FEM_VTK" in App.__cmake__,
        "Post-processing contracts require VTK",
    )
    def test_pipeline_from_result_is_one_undoable_change(self):
        self.shape.ViewObject.Visibility = True
        self.result.ViewObject.Visibility = True
        self._select(self.result)
        before_ids = {obj.ID for obj in self.document.Objects}
        visibility = self._visibility_state()
        undo_before = int(self.document.UndoCount)
        Gui.runCommand("FEM_PostPipelineFromResult")
        self._process_events()
        created = [
            obj
            for obj in self.document.Objects
            if obj.ID not in before_ids
        ]
        self.assertEqual(len(created), 1)
        self.assertTrue(
            created[0].isDerivedFrom("Fem::FemPostPipeline")
        )
        pipeline = created[0]
        self.assertIn(pipeline, self.analysis.Group)
        self._assert_replacement_contract(pipeline, self.result)
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )
        self.assertEqual(
            int(self.document.UndoCount),
            undo_before + 1,
        )

        pipeline_name = pipeline.Name
        pipeline_id = int(pipeline.ID)
        self.document.undo()
        self._process_events()
        self.assertEqual(
            {obj.ID for obj in self.document.Objects},
            before_ids,
        )
        self.assertEqual(self._visibility_state(), visibility)

        self.document.redo()
        self._process_events()
        pipeline = self.document.getObject(pipeline_name)
        self.assertIsNotNone(pipeline)
        self.assertEqual(int(pipeline.ID), pipeline_id)
        self._assert_replacement_contract(pipeline, self.result)
        self._assert_replacement_playback(pipeline, self.result)

    @unittest.skipUnless(
        "BUILD_FEM_VTK" in App.__cmake__,
        "Post-processing contracts require VTK",
    )
    def test_post_function_requires_and_targets_an_explicit_pipeline(self):
        def function_provider(pipeline):
            providers = [
                obj
                for obj in pipeline.Group
                if obj.TypeId == "Fem::FemPostFunctionProvider"
            ]
            self.assertLessEqual(len(providers), 1)
            return providers[0] if providers else None

        second = self.document.addObject(
            "Fem::FemPostPipeline",
            "SecondPipeline",
        )
        self.analysis.addObject(second)
        self.document.recompute()

        self._select()
        self.assertFalse(
            Gui.isCommandActive("FEM_PostCreateFunctions")
        )

        self._select(second)
        self.assertTrue(
            Gui.isCommandActive("FEM_PostCreateFunctions")
        )
        before = tuple(self.document.Objects)
        Gui.runCommand("FEM_PostCreateFunctions", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        self.assertIsNone(function_provider(self.pipeline))
        provider = function_provider(second)
        self.assertIsNotNone(provider)
        self.assertEqual(len(provider.Group), 1)
        self.assertEqual(
            provider.Group[0].TypeId,
            "Fem::FemPostPlaneFunction",
        )

        self._cancel_active_task()
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertIsNone(function_provider(second))

    @unittest.skipUnless(
        "BUILD_FEM_VTK" in App.__cmake__,
        "Post-processing contracts require VTK",
    )
    def test_invalid_pipeline_from_result_is_a_true_no_op(self):
        self.shape.ViewObject.Visibility = True
        self.result.ViewObject.Visibility = True
        self._select()
        before = tuple(self.document.Objects)
        visibility = {
            obj.ID: bool(obj.ViewObject.Visibility)
            for obj in self.document.Objects
            if getattr(obj, "ViewObject", None) is not None
        }
        Gui.runCommand("FEM_PostPipelineFromResult")
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(
            {
                obj.ID: bool(obj.ViewObject.Visibility)
                for obj in self.document.Objects
                if getattr(obj, "ViewObject", None) is not None
            },
            visibility,
        )

    def test_missing_solver_executable_is_reported_without_document_mutation(self):
        from femsolver import settings

        preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Fem/Elmer"
        )
        previous_grid_path = preferences.GetString("gridBinaryPath", "")
        missing_grid_path = os.path.join(
            tempfile.gettempdir(),
            "vibecad-deliberately-missing-elmergrid",
        )
        self.assertFalse(os.path.exists(missing_grid_path))

        timeline = self.document.getObject("VibeCADTimeline")
        before_objects = tuple(self.document.Objects)
        before_operations = (
            tuple(timeline.Operations)
            if timeline is not None
            else ()
        )
        before_undo_count = int(self.document.UndoCount)
        before_working_directory = str(self.solver.WorkingDirectory)
        before_tool = getattr(self.solver, "Tool", None)

        try:
            preferences.SetString("gridBinaryPath", missing_grid_path)
            self.assertEqual(
                settings.get_configured_binary("ElmerGrid"),
                missing_grid_path,
            )
            with self.assertRaises(
                settings.SolverExecutableNotFoundError
            ) as missing:
                settings.require_binary("ElmerGrid")
            self.assertIn(missing_grid_path, str(missing.exception))

            self._select(self.solver)
            self.assertTrue(Gui.isCommandActive("FEM_SolverRun"))
            with mock.patch.object(
                QtGui.QMessageBox,
                "critical",
            ) as critical:
                Gui.runCommand("FEM_SolverRun")
                self._process_events()

            critical.assert_called_once()
            _parent, title, message = critical.call_args.args
            self.assertEqual(title, "Can't start Solver")
            self.assertIn("ElmerGrid executable was not found", message)
            self.assertIn(missing_grid_path, message)
            self.assertIn("Preferences > FEM > Solver", message)
        finally:
            if previous_grid_path:
                preferences.SetString(
                    "gridBinaryPath",
                    previous_grid_path,
                )
            else:
                preferences.RemString("gridBinaryPath")

        self.assertEqual(tuple(self.document.Objects), before_objects)
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertEqual(
            tuple(timeline.Operations) if timeline is not None else (),
            before_operations,
        )
        self.assertEqual(int(self.document.UndoCount), before_undo_count)
        self.assertEqual(
            str(self.solver.WorkingDirectory),
            before_working_directory,
        )
        self.assertIs(getattr(self.solver, "Tool", None), before_tool)
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )
        self.assertFalse(self.document.HasPendingTransaction)

    def test_solver_control_reassignment_disconnects_every_old_machine_signal(
        self,
    ):
        from femsolver import run as solver_run
        from femsolver import solver_taskpanel

        class Machine:
            def __init__(self, solver, directory):
                self.solver = solver
                self.directory = directory
                self.status = ""
                self.time = None
                self.running = False
                self.state = solver_run.CHECK
                self.signalStatus = set()
                self.signalStatusCleared = set()
                self.signalStarted = set()
                self.signalStopped = set()
                self.signalState = set()

        framework_solver = ObjectsFem.makeSolverMystran(
            self.document,
            "SignalLifecycleMystran",
        )
        self.analysis.addObject(framework_solver)
        first = Machine(framework_solver, tempfile.gettempdir())
        second = Machine(framework_solver, tempfile.gettempdir())
        panel = solver_taskpanel.ControlTaskPanel(first)

        self.assertIn(panel._statusProxy, first.signalStatus)
        self.assertIn(
            panel._statusClearedProxy,
            first.signalStatusCleared,
        )
        self.assertIn(panel._startedProxy, first.signalStarted)
        self.assertIn(panel._stoppedProxy, first.signalStopped)
        self.assertIn(panel._stateProxy, first.signalState)

        panel.machine = second
        for signal in (
            first.signalStatus,
            first.signalStatusCleared,
            first.signalStarted,
            first.signalStopped,
            first.signalState,
        ):
            self.assertFalse(signal)
        self.assertIn(panel._statusProxy, second.signalStatus)
        self.assertIn(
            panel._statusClearedProxy,
            second.signalStatusCleared,
        )

        panel._disconnectMachine()
        panel._disconnectMachine()
        for signal in (
            second.signalStatus,
            second.signalStatusCleared,
            second.signalStarted,
            second.signalStopped,
            second.signalState,
        ):
            self.assertFalse(signal)
        panel.form.deleteLater()
        self._process_events()

    def test_modern_and_framework_solver_controls_open_and_cancel_cleanly(self):
        framework_solver = ObjectsFem.makeSolverMystran(
            self.document,
            "ControlLifecycleMystran",
        )
        self.analysis.addObject(framework_solver)
        self.document.recompute()

        for solver in (self.solver, framework_solver):
            with self.subTest(solver=solver.Name):
                before_objects = tuple(self.document.Objects)
                before_visibility = self._visibility_state()
                before_undo_count = int(self.document.UndoCount)
                before_simulation_type = (
                    str(solver.SimulationType)
                    if hasattr(solver, "SimulationType")
                    else None
                )

                self._select(solver)
                self.assertTrue(
                    Gui.isCommandActive("FEM_SolverControl"),
                    solver.Name,
                )
                Gui.runCommand("FEM_SolverControl")
                self.assertTrue(
                    Gui.Control.activeDialog(),
                    solver.Name,
                )
                gui_document = Gui.getDocument(self.document.Name)
                editing = gui_document.getInEdit()
                self.assertIsNotNone(editing)
                self.assertIs(
                    getattr(editing, "Object", None),
                    solver,
                )
                transaction_id = int(
                    self.document.getBookedTransactionID()
                )
                self.assertNotEqual(transaction_id, 0)
                self.assertTrue(
                    Gui.Control.ownsCommandTransaction(
                        gui_document,
                        transaction_id,
                    )
                )
                self._process_events()
                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    transaction_id,
                )
                if before_simulation_type is not None:
                    alternatives = [
                        value
                        for value in solver.getEnumerationsOfProperty(
                            "SimulationType"
                        )
                        if value != before_simulation_type
                    ]
                    self.assertTrue(alternatives)
                    solver.SimulationType = alternatives[0]
                    self.assertNotEqual(
                        str(solver.SimulationType),
                        before_simulation_type,
                    )

                self._cancel_active_task()
                self.assertEqual(
                    tuple(self.document.Objects),
                    before_objects,
                    solver.Name,
                )
                self.assertEqual(
                    self._visibility_state(),
                    before_visibility,
                    solver.Name,
                )
                self.assertEqual(
                    int(self.document.UndoCount),
                    before_undo_count,
                    solver.Name,
                )
                if before_simulation_type is not None:
                    self.assertEqual(
                        str(solver.SimulationType),
                        before_simulation_type,
                        solver.Name,
                    )


@unittest.skipIf(not App.GuiUp, "VibeCAD Analyze ribbon tests require the GUI")
class TestVibeCADFEMSuppressionContract(unittest.TestCase):
    """GUI and command behavior for disabled FEM computational inputs."""

    def setUp(self):
        Gui.activateWorkbench("FemWorkbench")
        self.document = App.newDocument("VibeCADFEMSuppression")
        self.analysis = ObjectsFem.makeAnalysis(
            self.document,
            "SuppressionAnalysis",
        )
        FemGui.setActiveAnalysis(self.analysis)
        self.solver = ObjectsFem.makeSolverElmer(
            self.document,
            "SuppressionElmer",
        )
        self.analysis.addObject(self.solver)
        self.document.recompute()

    def tearDown(self):
        Gui.Selection.clearSelection()
        if FemGui.getActiveAnalysis() is self.analysis:
            FemGui.setActiveAnalysis()
        if self.document.Name in App.listDocuments():
            App.closeDocument(self.document.Name)
        self.document = None
        Gui.updateGui()

    @staticmethod
    def _select(obj):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(obj)
        Gui.updateGui()

    def test_solver_and_equation_views_expose_suppression_state(self):
        objects = (
            ObjectsFem.makeSolverCalculiX(
                self.document,
                "SuppressionCalculiX",
            ),
            self.solver,
            ObjectsFem.makeSolverMystran(
                self.document,
                "SuppressionMystran",
            ),
            ObjectsFem.makeSolverZ88(
                self.document,
                "SuppressionZ88",
            ),
            ObjectsFem.makeEquationHeat(
                self.document,
                self.solver,
                "SuppressionEquation",
            ),
        )

        for obj in objects:
            with self.subTest(object=obj.Name):
                self.assertTrue(
                    obj.ViewObject.hasExtension(
                        "Gui::ViewProviderSuppressibleExtension"
                    ),
                    obj.Name,
                )

    def test_suppressed_solver_is_not_an_executable_command_target(self):
        self._select(self.solver)
        self.assertTrue(Gui.isCommandActive("FEM_SolverControl"))
        self.assertTrue(Gui.isCommandActive("FEM_SolverRun"))
        self.assertTrue(Gui.isCommandActive("FEM_EquationHeat"))

        self.solver.Suppressed = True
        Gui.updateGui()

        self.assertFalse(Gui.isCommandActive("FEM_SolverControl"))
        self.assertFalse(Gui.isCommandActive("FEM_SolverRun"))
        self.assertFalse(Gui.isCommandActive("FEM_EquationHeat"))
        self.assertTrue(Gui.isCommandActive("FEM_SolverCalculiX"))


@unittest.skipIf(not App.GuiUp, "VibeCAD Analyze timeline tests require the GUI")
class TestVibeCADFEMTimelineContract(unittest.TestCase):
    """The document marker controls native FEM inputs without deleting them."""

    def setUp(self):
        Gui.activateWorkbench("FemWorkbench")
        self.document = App.newDocument("VibeCADFEMTimeline")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)

        self.shape = self.document.addObject(
            "Part::Feature",
            "TimelineShape",
        )
        self.shape.Shape = Part.makeBox(20.0, 16.0, 8.0)
        self.analysis = ObjectsFem.makeAnalysis(
            self.document,
            "TimelineAnalysis",
        )
        FemGui.setActiveAnalysis(self.analysis)

        self.constraint = ObjectsFem.makeConstraintFixed(
            self.document,
            "TimelineConstraint",
        )
        self.material = ObjectsFem.makeMaterialSolid(
            self.document,
            "TimelineMaterial",
        )
        self.solver = ObjectsFem.makeSolverElmer(
            self.document,
            "TimelineSolver",
        )
        self.equation = ObjectsFem.makeEquationHeat(
            self.document,
            self.solver,
            "TimelineEquation",
        )
        self.mesh = ObjectsFem.makeMeshGmsh(
            self.document,
            "TimelineMesh",
        )

        for member in (
            self.constraint,
            self.material,
            self.solver,
            self.mesh,
        ):
            self.analysis.addObject(member)

        generated_mesh = Fem.FemMesh()
        generated_mesh.addNode(0.0, 0.0, 0.0, 1)
        generated_mesh.addNode(1.0, 0.0, 0.0, 2)
        generated_mesh.addEdge([1, 2], 1)
        self.mesh.FemMesh = generated_mesh

        self.operations = (
            self.constraint,
            self.material,
            self.solver,
            self.equation,
            self.mesh,
        )
        for operation in self.operations:
            operation.Visibility = True
        self.document.recompute()

        self.timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(self.timeline)
        timeline_operations = list(self.timeline.Operations)
        for operation in self.operations:
            self.assertIn(operation, timeline_operations)
            self.assertTrue(
                operation.hasExtension("App::SuppressibleExtension"),
                operation.Name,
            )

        main_window = Gui.getMainWindow()
        self.timeline_widget = self._wait_until(
            lambda: main_window.findChild(
                QtGui.QWidget,
                "VibeCADFeatureTimeline",
            )
        )
        self.assertIsNotNone(self.timeline_widget)
        self.previous_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        self.next_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineNext",
        )
        self.end_button = self.timeline_widget.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        self.assertIsNotNone(self.previous_button)
        self.assertIsNotNone(self.next_button)
        self.assertIsNotNone(self.end_button)
        self.assertTrue(
            self._wait_until(
                lambda: self.timeline.Position
                == len(self.timeline.Operations)
                and all(
                    self.timeline.VisibilityAtEnd[
                        list(self.timeline.Operations).index(operation)
                    ]
                    for operation in self.operations
                )
            ),
            "FEM operation visibility was not captured at the end of history",
        )

        self.temporary_directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        Gui.Selection.clearSelection()
        FemGui.setActiveAnalysis()
        if (
            self.document is not None
            and self.document.Name in App.listDocuments()
        ):
            App.closeDocument(self.document.Name)
        self.document = None
        if hasattr(self, "temporary_directory"):
            self.temporary_directory.cleanup()
        self._process_events()

    @staticmethod
    def _process_events(rounds=3):
        for _ in range(rounds):
            Gui.updateGui()
            application = QtGui.QApplication.instance()
            if application is not None:
                application.processEvents(
                    QtCore.QEventLoop.AllEvents,
                    25,
                )

    def _wait_until(self, predicate, timeout_ms=10000):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while timer.elapsed() < timeout_ms:
            self._process_events(1)
            try:
                result = predicate()
            except RuntimeError:
                result = None
            if result:
                return result
        return None

    def _move_to_position(self, position):
        while self.timeline.Position > 0:
            previous = int(self.timeline.Position)
            self.previous_button.click()
            self.assertTrue(
                self._wait_until(
                    lambda: int(self.timeline.Position) < previous
                ),
                f"The timeline did not retreat from position {previous}",
            )
        while self.timeline.Position < position:
            previous = int(self.timeline.Position)
            self.next_button.click()
            self.assertTrue(
                self._wait_until(
                    lambda: int(self.timeline.Position) > previous
                ),
                f"The timeline did not advance from position {previous}",
            )
        self.assertEqual(self.timeline.Position, position)

    def _history_item(self, operation):
        items = self.timeline_widget.findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(items)
        return self._wait_until(
            lambda: next(
                (
                    items.item(row)
                    for row in range(items.count())
                    if str(
                        items.item(row).data(QtCore.Qt.UserRole)
                        or ""
                    )
                    == operation.Name
                ),
                None,
            )
        )

    def test_history_edit_opens_only_exact_real_fem_editors(self):
        from femviewprovider import (
            view_base_femmeshelement,
            view_base_fempostvisualization,
        )
        from femobjects import base_femmeshelement
        from femcommands import manager as fem_command_manager

        # One native C++ task and one Python task prove both capability
        # paths enter edit on the exact selected operation.
        for operation in (self.constraint, self.material):
            with self.subTest(real_editor=operation.Name):
                item = self._history_item(operation)
                self.assertIsNotNone(item)
                self.timeline_widget.findChild(
                    QtGui.QListWidget,
                    "VibeCADFeatureTimelineItems",
                ).itemDoubleClicked.emit(item)
                self.assertTrue(
                    self._wait_until(
                        lambda: Gui.Control.activeDialog()
                        and Gui.activeDocument().getInEdit()
                        is operation.ViewObject
                    ),
                    operation.Name,
                )
                self.assertIs(
                    Gui.activeDocument().getInEdit(),
                    operation.ViewObject,
                )
                Gui.Control.activeTaskDialog().reject()
                self.assertTrue(
                    self._wait_until(
                        lambda: not Gui.Control.activeDialog()
                        and Gui.activeDocument().getInEdit() is None
                        and not self.document.HasPendingTransaction
                    )
                )

        # These proxies have no mode-0 task panel. A handled double-click or
        # inherited no-op setEdit must not be mistaken for an editor.
        self.document.UndoMode = False
        mesh_base = self.document.addObject(
            "App::FeaturePython",
            "NoPanelMeshElement",
        )
        mesh_proxy = base_femmeshelement.BaseFemMeshElement(mesh_base)
        mesh_proxy.Type = mesh_proxy.BaseType
        view_base_femmeshelement.VPBaseFemMeshElement(
            mesh_base.ViewObject
        )
        post_base = self.document.addObject(
            "App::FeaturePython",
            "NoPanelPostVisualization",
        )
        post_base.addExtension("App::GroupExtensionPython")
        view_base_fempostvisualization.VPPostVisualization(
            post_base.ViewObject
        )
        fem_command_manager._mark_timeline_operation(post_base)
        rotation = ObjectsFem.makeElementRotation1D(
            self.document,
            "NoPanelElementRotation",
        )
        self.analysis.addObject(rotation)
        self.document.recompute()
        self.document.UndoMode = True

        for operation in (mesh_base, post_base, rotation):
            with self.subTest(no_editor=operation.Name):
                self.assertFalse(
                    hasattr(
                        operation.ViewObject.Proxy,
                        "supportsDocumentTimelineEdit",
                    )
                )
                item = self._history_item(operation)
                self.assertIsNotNone(item)
                undo_before = int(self.document.UndoCount)
                self.timeline_widget.findChild(
                    QtGui.QListWidget,
                    "VibeCADFeatureTimelineItems",
                ).itemDoubleClicked.emit(item)
                self._process_events()
                self.assertIsNone(Gui.activeDocument().getInEdit())
                self.assertFalse(Gui.Control.activeDialog())
                self.assertFalse(self.document.HasPendingTransaction)
                self.assertEqual(
                    int(self.document.UndoCount),
                    undo_before,
                )

    def test_marker_filters_every_fem_input_and_preserves_mesh_output(self):
        from femsolver.elmer import writer
        from femtools import membertools

        expected_mesh_nodes = self.mesh.FemMesh.NodeCount
        expected_mesh_edges = self.mesh.FemMesh.EdgeCount

        self._move_to_position(0)
        self.assertTrue(
            self._wait_until(
                lambda: self.timeline.Position == 0
                and all(operation.Suppressed for operation in self.operations)
                and all(not operation.Visibility for operation in self.operations)
            ),
            "Moving before FEM history did not deactivate every FEM input",
        )
        self.assertEqual(membertools._active_group_members(self.analysis), [])
        self.assertEqual(
            membertools._active_group_members(self.solver),
            [],
        )
        self.assertEqual(self.mesh.FemMesh.NodeCount, expected_mesh_nodes)
        self.assertEqual(self.mesh.FemMesh.EdgeCount, expected_mesh_edges)

        self.end_button.click()
        self.assertTrue(
            self._wait_until(
                lambda: self.timeline.Position
                == len(self.timeline.Operations)
                and all(
                    not operation.Suppressed
                    for operation in self.operations
                )
                and all(operation.Visibility for operation in self.operations)
            ),
            "Returning to the end did not reactivate every FEM input",
        )
        self.assertEqual(
            membertools._active_group_members(self.analysis),
            [
                self.constraint,
                self.material,
                self.solver,
                self.mesh,
            ],
        )
        elmer_writer = writer.Writer.__new__(writer.Writer)
        elmer_writer.solver = self.solver
        self.assertEqual(
            elmer_writer._get_active_equations(),
            [self.equation],
        )
        self.assertIs(
            membertools.get_mesh_to_solve(self.analysis),
            self.mesh,
        )
        self.assertEqual(self.mesh.FemMesh.NodeCount, expected_mesh_nodes)
        self.assertEqual(self.mesh.FemMesh.EdgeCount, expected_mesh_edges)

    def test_marker_persists_and_future_solver_and_mesh_cannot_execute(self):
        from femmesh.gmshtools import GmshTools
        from femsolver import run

        solver_index = list(self.timeline.Operations).index(self.solver)
        self._move_to_position(solver_index)
        self.assertTrue(
            self._wait_until(
                lambda: not self.constraint.Suppressed
                and not self.material.Suppressed
                and self.solver.Suppressed
                and self.equation.Suppressed
                and self.mesh.Suppressed
            )
        )

        saved_file = os.path.join(
            self.temporary_directory.name,
            "fem_document_timeline.FCStd",
        )
        expected_position = int(self.timeline.Position)
        self.document.saveAs(saved_file)
        App.closeDocument(self.document.Name)
        self._process_events()

        self.document = App.openDocument(saved_file)
        self.timeline = self.document.getObject("VibeCADTimeline")
        self.solver = self.document.getObject("TimelineSolver")
        self.equation = self.document.getObject("TimelineEquation")
        self.mesh = self.document.getObject("TimelineMesh")
        self.assertEqual(self.timeline.Position, expected_position)
        self.assertTrue(self.solver.Suppressed)
        self.assertTrue(self.equation.Suppressed)
        self.assertTrue(self.mesh.Suppressed)
        self.assertEqual(self.mesh.FemMesh.NodeCount, 2)
        self.assertEqual(self.mesh.FemMesh.EdgeCount, 1)

        with self.assertRaisesRegex(ValueError, "suppressed FEM solver"):
            run.getMachine(self.solver)
        with self.assertRaisesRegex(ValueError, "cannot be executed"):
            GmshTools(self.mesh)

    def test_execution_guards_treat_timeline_future_as_inactive(self):
        from femmesh.gmshtools import GmshTools
        from femsolver import run
        from femtools import membertools

        solver_index = list(self.timeline.Operations).index(self.solver)
        self.timeline.Position = solver_index
        self.assertFalse(self.solver.Suppressed)
        self.assertFalse(self.mesh.Suppressed)
        self.assertTrue(membertools._is_suppressed(self.solver))
        self.assertTrue(membertools._is_suppressed(self.mesh))
        with self.assertRaisesRegex(ValueError, "suppressed FEM solver"):
            run.getMachine(self.solver)
        with self.assertRaisesRegex(ValueError, "cannot be executed"):
            GmshTools(self.mesh)

        self.timeline.Position = len(self.timeline.Operations)
        self.solver.Suppressed = True
        self.mesh.Suppressed = True
        with self.assertRaisesRegex(ValueError, "suppressed FEM solver"):
            run.getMachine(self.solver)
        with self.assertRaisesRegex(ValueError, "cannot be executed"):
            GmshTools(self.mesh)
