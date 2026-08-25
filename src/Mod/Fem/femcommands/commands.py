# ***************************************************************************
# *   Copyright (c) 2016 Bernd Hahnebach <bernd@bimstatik.org>              *
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

__title__ = "FreeCAD FEM command definitions"
__author__ = "Bernd Hahnebach"
__url__ = "https://www.freecad.org"

## @package commands
#  \ingroup FEM
#  \brief FreeCAD FEM command definitions

from functools import lru_cache
import math
import subprocess

import FreeCAD
import FreeCADGui
import FemGui
from FreeCAD import Qt

from .manager import (
    CommandManager,
    _active_document,
    _close_exact_transaction,
    _document_expression,
    _is_live_in_document,
    _object_expression,
    _open_exact_transaction,
    _require_provisional_timeline_identity,
    can_start_command,
)
from femtools.femutils import expandParentObject
from femtools.femutils import is_of_type
from femsolver.settings import get_default_solver

# Python command definitions:
# for C++ command definitions see src/Mod/Fem/Command.cpp
# TODO, may be even more generic class creation
# with type() and identifier instead of class for
# the commands which add new document objects.
# see https://www.python-course.eu/python3_classes_and_type.php
# Translation:
# some information in the regard of translation can be found in forum post
# https://forum.freecad.org/viewtopic.php?f=18&t=62449&p=543845#p543593


@lru_cache(maxsize=8)
def _python_has_netgen(executable):
    """Return whether an exact Python interpreter provides Netgen."""

    try:
        completed = subprocess.run(
            [
                executable,
                "-E",
                "-c",
                "import netgen, pyngcore",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _netgen_backend_status():
    """Return availability and an actionable reason for the selected backend."""

    preferences = FreeCAD.ParamGet(
        "User parameter:BaseApp/Preferences/Mod/Fem/Netgen"
    )
    if preferences.GetBool("UseLegacyNetgen", True):
        if "BUILD_FEM_NETGEN" in FreeCAD.__cmake__:
            return True, ""
        return (
            False,
            "the built-in Netgen backend is not included in this build",
        )

    from freecad import utils

    executable = preferences.GetString("NetgenPythonPath", "")
    if not executable:
        executable = utils.get_python_exe()
    if _python_has_netgen(executable):
        return True, ""
    return (
        False,
        "the configured Python interpreter has no Netgen bindings",
    )


def createMeshFeature(document, name, mesh):
    """Create one exact Mesh feature from an already converted mesh."""
    if document is None:
        raise RuntimeError("A document is required for the FEM mesh conversion")
    created = document.addObject("Mesh::Feature", name)
    created.Mesh = mesh
    return created


def _capture_exact_object_identity(document, obj, description):
    """Capture one live object by its immutable document name and ID pair."""

    if (
        not _is_live_in_document(obj, document)
        or int(getattr(obj, "ID", -1)) <= 0
        or document.getObject(int(obj.ID)) is not obj
    ):
        raise RuntimeError(f"{description} is not one exact live object")
    return str(obj.Name), int(obj.ID)


def _resolve_exact_object_identity(document, identity, description):
    """Resolve a captured name+ID pair without ambient active-object state."""

    name, object_id = identity
    by_name = document.getObject(name)
    by_id = document.getObject(object_id)
    if (
        by_name is None
        or by_name is not by_id
        or not _is_live_in_document(by_name, document)
        or str(by_name.Name) != name
        or int(by_name.ID) != object_id
    ):
        raise RuntimeError(f"{description} changed exact identity")
    return by_name


def createDefaultSolverFeature(document, solver_name):
    """Create and configure the exact solver requested by New Analysis."""

    if (
        document is None
        or FreeCAD.getDocument(document.Name) is not document
    ):
        raise RuntimeError(
            "A live document is required to create the default FEM solver"
        )

    import ObjectsFem

    solver_name = str(solver_name or "")
    if solver_name == "CalculiX":
        ccx_prefs = FreeCAD.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Fem/Ccx"
        )
        make_solver = (
            "makeSolverCalculiX"
            if ccx_prefs.GetBool("ResultAsPipeline", True)
            else "makeSolverCalculiXCcxTools"
        )
        solver = getattr(ObjectsFem, make_solver)(document)
        settings = {
            "AnalysisType": ccx_prefs.GetInt("AnalysisType", 0),
            "EigenmodesCount": ccx_prefs.GetInt(
                "EigenmodesCount",
                10,
            ),
            "EigenmodeLowLimit": ccx_prefs.GetFloat(
                "EigenmodeLowLimit",
                0.0,
            ),
            "EigenmodeHighLimit": ccx_prefs.GetFloat(
                "EigenmodeHighLimit",
                1000000.0,
            ),
            "IncrementsMaximum": ccx_prefs.GetInt(
                "StepMaxIncrements",
                2000,
            ),
            "TimeInitialIncrement": ccx_prefs.GetFloat(
                "TimeInitialIncrement",
                1.0,
            ),
            "TimePeriod": ccx_prefs.GetFloat(
                "TimePeriod",
                1.0,
            ),
            "TimeMinimumIncrement": ccx_prefs.GetFloat(
                "TimeMinimumIncrement",
                0.00001,
            ),
            "TimeMaximumIncrement": ccx_prefs.GetFloat(
                "TimeMaximumIncrement",
                1.0,
            ),
            "ThermoMechSteadyState": ccx_prefs.GetBool(
                "StaticAnalysis",
                True,
            ),
            "IterationsControlParameterTimeUse": (
                ccx_prefs.GetBool(
                    "UseNonCcxIterationParam",
                    False,
                )
            ),
            "SplitInputWriter": ccx_prefs.GetBool(
                "SplitInputWriter",
                False,
            ),
            "MatrixSolverType": ccx_prefs.GetInt("Solver", 0),
            "Output3d": ccx_prefs.GetBool(
                "BeamShellOutput",
                True,
            ),
            "GeometricalNonlinearity": ccx_prefs.GetBool(
                "NonlinearGeometry",
                False,
            ),
            "MaterialNonlinearity": True,
        }
        for property_name, value in settings.items():
            setattr(solver, property_name, value)
    elif solver_name == "Elmer":
        solver = ObjectsFem.makeSolverElmer(document)
        elmer_prefs = FreeCAD.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Fem/Elmer"
        )
        solver.BinaryOutput = elmer_prefs.GetBool(
            "BinaryOutput",
            False,
        )
        solver.SaveGeometryIndex = elmer_prefs.GetBool(
            "SaveGeometryIndex",
            False,
        )
    elif solver_name == "OpenFOAM":
        solver = ObjectsFem.makeSolverOpenFOAM(document)
    elif solver_name == "Mystran":
        solver = ObjectsFem.makeSolverMystran(document)
    elif solver_name == "Z88":
        solver = ObjectsFem.makeSolverZ88(document)
        z88_prefs = FreeCAD.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Fem/Z88"
        )
        solver.SolverType = z88_prefs.GetString(
            "Solver",
            "sorcg",
        )
        solver.MatrixMaximum = z88_prefs.GetInt(
            "MaxGS",
            100000000,
        )
        solver.VectorMaximum = z88_prefs.GetInt(
            "MaxKOI",
            2800000,
        )
    else:
        raise ValueError(
            f"Unsupported default FEM solver: {solver_name!r}"
        )

    if (
        not _is_live_in_document(solver, document)
        or not solver.isDerivedFrom("Fem::FemSolverObjectPython")
    ):
        raise RuntimeError(
            "The default FEM solver factory returned an invalid object"
        )
    return solver


class _Analysis(CommandManager):
    "The FEM_Analysis command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_Analysis", "New Analysis")
        self.accel = "S, A"
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_Analysis", "Creates an analysis container with default solver"
        )
        self.is_active = "with_document"

    def Activated(self):
        if not self.IsActive():
            return

        document = _active_document()
        default_solver = get_default_solver()
        analysis = None
        solver = None
        analysis_identity = None
        solver_identity = None
        transaction_id = _open_exact_transaction(
            document,
            "Create Analysis",
        )
        try:
            FreeCADGui.addModule("FemGui")
            FreeCADGui.addModule("ObjectsFem")
            analysis = FreeCADGui.runDocumentObjectCommand(
                document,
                "ObjectsFem.makeAnalysis("
                f"{_document_expression(document)}, 'Analysis')",
                "Fem::FemAnalysis",
            )
            # FemAnalysis is a native group, so the document deliberately
            # excludes it from automatic History enrollment until the
            # command assigns its explicit operation role below.  Capture
            # the exact factory return here; publication later proves that
            # this identity was created by this transaction.
            analysis_identity = _capture_exact_object_identity(
                document,
                analysis,
                "The new FEM analysis",
            )

            if default_solver:
                FreeCADGui.addModule("femcommands.commands")
                solver = FreeCADGui.runDocumentObjectCommand(
                    document,
                    "femcommands.commands.createDefaultSolverFeature("
                    f"{_document_expression(document)}, "
                    f"{default_solver!r})",
                    "Fem::FemSolverObjectPython",
                )
                _require_provisional_timeline_identity(
                    solver,
                    document,
                    "The default solver factory",
                )
                solver_identity = _capture_exact_object_identity(
                    document,
                    solver,
                    "The new default FEM solver",
                )
                analysis = _resolve_exact_object_identity(
                    document,
                    analysis_identity,
                    "The new FEM analysis",
                )
                FreeCADGui.doCommand(
                    f"{_object_expression(analysis)}"
                    f".addObject({_object_expression(solver)})"
                )
                analysis = _resolve_exact_object_identity(
                    document,
                    analysis_identity,
                    "The new FEM analysis",
                )
                solver = _resolve_exact_object_identity(
                    document,
                    solver_identity,
                    "The new default FEM solver",
                )
                if solver not in analysis.Group:
                    raise RuntimeError(
                        "The default solver was not added to its new analysis"
                    )

            FreeCADGui.addModule("femcommands.manager")
            FreeCADGui.doCommand(
                "femcommands.manager._mark_timeline_operation("
                f"{_object_expression(analysis)})"
            )
            analysis = _resolve_exact_object_identity(
                document,
                analysis_identity,
                "The new FEM analysis",
            )
            if solver is not None:
                solver = _resolve_exact_object_identity(
                    document,
                    solver_identity,
                    "The new default FEM solver",
                )
                FreeCADGui.doCommand(
                    "femcommands.manager._mark_timeline_resource("
                    f"{_object_expression(solver)}, "
                    f"{_object_expression(analysis)})"
                )
                analysis = _resolve_exact_object_identity(
                    document,
                    analysis_identity,
                    "The new FEM analysis",
                )
                solver = _resolve_exact_object_identity(
                    document,
                    solver_identity,
                    "The new default FEM solver",
                )
                resources_expression = f"[{_object_expression(solver)}]"
                owners_expression = f"[{_object_expression(analysis)}]"
            else:
                resources_expression = "[]"
                owners_expression = "[]"
            FreeCADGui.doCommand(
                f"{_document_expression(document)}"
                ".publishProvisionalTimelineOperationBlock("
                f"{_object_expression(analysis)}, "
                f"{resources_expression}, {owners_expression})"
            )
            analysis = _resolve_exact_object_identity(
                document,
                analysis_identity,
                "The new FEM analysis",
            )
            if solver is not None:
                solver = _resolve_exact_object_identity(
                    document,
                    solver_identity,
                    "The new default FEM solver",
                )
                if (
                    str(solver.VibeCADTimelineRole) != "resource"
                    or solver.VibeCADTimelineOwner is not analysis
                ):
                    raise RuntimeError(
                        "The default solver was not published as an exact "
                        "resource of its analysis"
                    )
            if str(analysis.VibeCADTimelineRole) != "operation":
                raise RuntimeError(
                    "The new FEM analysis was not published as one operation"
                )

            timeline = document.getObject("VibeCADTimeline")
            operations = list(
                getattr(timeline, "Operations", ()) or ()
            )
            if analysis not in operations:
                raise RuntimeError(
                    "The new FEM analysis is absent from document History"
                )
            analysis_index = operations.index(analysis)
            if solver is not None and (
                analysis_index == 0
                or operations[analysis_index - 1] is not solver
            ):
                raise RuntimeError(
                    "The default FEM solver and analysis are not one "
                    "canonical resource-first History block"
                )

            document.recompute()
            analysis = _resolve_exact_object_identity(
                document,
                analysis_identity,
                "The new FEM analysis",
            )
            if solver is not None:
                solver = _resolve_exact_object_identity(
                    document,
                    solver_identity,
                    "The new default FEM solver",
                )
            FemGui.setActiveAnalysis(analysis)
            _close_exact_transaction(
                document,
                transaction_id,
                False,
            )
        except Exception:
            if (
                analysis is not None
                and FemGui.getActiveAnalysis() is analysis
            ):
                FemGui.setActiveAnalysis()
            _close_exact_transaction(
                document,
                transaction_id,
                True,
            )
            raise

        if _is_live_in_document(analysis, document):
            gui_document = FreeCADGui.getDocument(document.Name)
            if gui_document is not None:
                gui_document.toggleTreeItem(analysis, 2)
            document.recompute()


class _ClippingPlaneAdd(CommandManager):
    "The FEM_ClippingPlaneAdd command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_ClippingPlaneAdd", "Clipping Plane on Face")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ClippingPlaneAdd", "Adds a clipping plane on a selected face"
        )
        self.is_active = "with_document"

    def GetResources(self):
        resources = super().GetResources()
        resources["CmdType"] = "ForEdit | Alter3DView"
        return resources

    def Activated(self):
        if not self.IsActive():
            return

        document = _active_document()
        gui_document = FreeCADGui.getDocument(document.Name)
        if gui_document is None:
            return

        from .clipping import add_clipping_plane
        from femtools.femutils import getSelectedFace

        aFace = getSelectedFace(
            FreeCADGui.Selection.getSelectionEx(document.Name)
        )
        if aFace:
            f_CoM = aFace.CenterOfMass
            f_uvCoM = aFace.Surface.parameter(f_CoM)  # u,v at CoM for normalAt calculation
            f_normal = aFace.normalAt(f_uvCoM[0], f_uvCoM[1])
        else:
            f_CoM = FreeCAD.Vector(0, 0, 0)
            f_normal = FreeCAD.Vector(0, 0, 1)
        try:
            add_clipping_plane(gui_document, document, f_CoM, f_normal)
        except RuntimeError:
            return


class _ClippingPlaneRemoveAll(CommandManager):
    "The FEM_ClippingPlaneRemoveAll command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_ClippingPlaneRemoveAll", "Remove All Clipping Planes"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ClippingPlaneRemoveAll", "Removes all clipping planes"
        )
        self.is_active = "with_document"

    def GetResources(self):
        resources = super().GetResources()
        resources["CmdType"] = "ForEdit | Alter3DView"
        return resources

    def Activated(self):
        if not self.IsActive():
            return

        document = _active_document()
        gui_document = FreeCADGui.getDocument(document.Name)
        if gui_document is None:
            return
        from .clipping import remove_all_clipping_planes

        remove_all_clipping_planes(gui_document)


class _ConstantVacuumPermittivity(CommandManager):
    "The FEM_ConstantVacuumPermittivity command definition"

    def __init__(self):
        super().__init__()
        self.pixmap = "fem-solver-analysis-thermomechanical.svg"
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstantVacuumPermittivity", "Constant Vacuum Permittivity"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstantVacuumPermittivity",
            "Creates a constant vacuum permittivity to overwrite standard value",
        )
        self.is_active = "with_document"
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_noset_edit"


class _ConstraintBodyHeatSource(CommandManager):
    "The FEM_ConstraintBodyHeatSource command definition"

    def __init__(self):
        super().__init__()
        self.pixmap = "FEM_ConstraintBodyHeatSource"
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_ConstraintBodyHeatSource", "Body Heat Source")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintBodyHeatSource", "Creates a body heat source"
        )
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ConstraintCentrif(CommandManager):
    "The FEM_ConstraintCentrif command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_ConstraintCentrif", "Centrifugal Load")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_ConstraintCentrif", "Creates a centrifugal load")
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ConstraintCurrentDensity(CommandManager):
    "The FEM_ConstraintCurrentDensity command definition"

    def __init__(self):
        super().__init__()
        self.pixmap = "FEM_ConstraintCurrentDensity"
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintCurrentDensity", "Current Density Boundary Condition"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintCurrentDensity",
            "Creates a current density boundary condition",
        )
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ConstraintElectricChargeDensity(CommandManager):
    "The FEM_ConstraintElectricChargeDensity command definition"

    def __init__(self):
        super().__init__()
        self.pixmap = "FEM_ConstraintElectricChargeDensity"
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintElectricChargeDensity", "Electric Charge Density"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintElectricChargeDensity", "Creates an electric charge density"
        )
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ConstraintElectromagnetic(CommandManager):
    "The FEM_ConstraintElectromagnetic command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintElectromagnetic",
            "Electromagnetic Boundary Condition",
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintElectromagnetic",
            "Creates an electromagnetic boundary condition",
        )
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ConstraintFlowVelocity(CommandManager):
    "The FEM_ConstraintFlowVelocity command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintFlowVelocity", "Flow Velocity Boundary Condition"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintFlowVelocity", "Creates a flow velocity boundary condition"
        )
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ConstraintInitialFlowVelocity(CommandManager):
    "The FEM_ConstraintInitialFlowVelocity command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintInitialFlowVelocity", "Initial Flow Velocity Condition"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintInitialFlowVelocity",
            "Creates an initial flow velocity condition",
        )
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ConstraintInitialPressure(CommandManager):
    "The FEM_ConstraintInitialPressure command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintInitialPressure", "Initial Pressure Condition"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintInitialPressure", "Creates an initial pressure condition"
        )
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ConstraintMagnetization(CommandManager):
    "The FEM_ConstraintMagnetization command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintMagnetization", "Magnetization Boundary Condition"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintMagnetization", "Creates a magnetization boundary condition"
        )
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ConstraintSectionPrint(CommandManager):
    "The FEM_ConstraintSectionPrint command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_ConstraintSectionPrint", "Section Print Feature")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ConstraintSectionPrint", "Creates a section print feature"
        )
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ConstraintSelfWeight(CommandManager):
    "The FEM_ConstraintSelfWeight command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_ConstraintSelfWeight", "Gravity Load")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_ConstraintSelfWeight", "Creates a gravity load")
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_noset_edit"


class _ConstraintTie(CommandManager):
    "The FEM_ConstraintTie command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_ConstraintTie", "Tie Constraint")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_ConstraintTie", "Creates a tie constraint")
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ElementFluid1D(CommandManager):
    "The FEM_ElementFluid1D command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_ElementFluid1D", "Fluid Section for 1D Flow")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ElementFluid1D", "Creates a fluid section for 1D flow"
        )
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ElementGeometry1D(CommandManager):
    "The Fem_ElementGeometry1D command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_ElementGeometry1D", "Beam Cross Section")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_ElementGeometry1D", "Creates a beam cross section")
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ElementGeometry2D(CommandManager):
    "The FEM_ElementGeometry2D command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_ElementGeometry2D", "Shell Plate Thickness")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ElementGeometry2D", "Creates a shell plate thickness"
        )
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _ElementRotation1D(CommandManager):
    "The Fem_ElementRotation1D command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_ElementRotation1D", "Beam Rotation")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_ElementRotation1D", "Creates a beam rotation")
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_noset_edit"


class _EquationDeformation(CommandManager):
    "The FEM_EquationDeformation command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_EquationDeformation", "Deformation Equation")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_EquationDeformation",
            "Creates an equation for deformation (nonlinear elasticity)",
        )
        self.is_active = "with_solver_elmer"
        self.do_activated = "add_obj_on_gui_selobj_expand_noset_edit"


class _EquationElasticity(CommandManager):
    "The FEM_EquationElasticity command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_EquationElasticity", "Elasticity Equation")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_EquationElasticity", "Creates an equation for elasticity (stress)"
        )
        self.is_active = "with_solver_elmer"
        self.do_activated = "add_obj_on_gui_selobj_expand_noset_edit"


class _EquationElectricforce(CommandManager):
    "The FEM_EquationElectricforce command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_EquationElectricforce", "Electricforce Equation")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_EquationElectricforce", "Creates an equation for electric forces"
        )
        self.is_active = "with_solver_elmer"
        self.do_activated = "add_obj_on_gui_selobj_expand_noset_edit"


class _EquationElectrostatic(CommandManager):
    "The FEM_EquationElectrostatic command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_EquationElectrostatic", "Electrostatic Equation")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_EquationElectrostatic", "Creates an equation for electrostatic"
        )
        self.is_active = "with_solver_elmer"
        self.do_activated = "add_obj_on_gui_selobj_expand_noset_edit"


class _EquationFlow(CommandManager):
    "The FEM_EquationFlow command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_EquationFlow", "Flow Equation")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_EquationFlow", "Creates an equation for flow")
        self.is_active = "with_solver_elmer"
        self.do_activated = "add_obj_on_gui_selobj_expand_noset_edit"


class _EquationFlux(CommandManager):
    "The FEM_EquationFlux command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_EquationFlux", "Flux Equation")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_EquationFlux", "Creates an equation for flux")
        self.is_active = "with_solver_elmer"
        self.do_activated = "add_obj_on_gui_selobj_expand_noset_edit"


class _EquationHeat(CommandManager):
    "The FEM_EquationHeat command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_EquationHeat", "Heat Equation")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_EquationHeat", "Creates an equation for heat")
        self.is_active = "with_solver_elmer"
        self.do_activated = "add_obj_on_gui_selobj_expand_noset_edit"


class _EquationMagnetodynamic(CommandManager):
    "The FEM_EquationMagnetodynamic command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_EquationMagnetodynamic", "Magnetodynamic Equation"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_EquationMagnetodynamic",
            "Creates an equation for magnetodynamic forces",
        )
        self.is_active = "with_solver_elmer"
        self.do_activated = "add_obj_on_gui_selobj_expand_noset_edit"


class _EquationMagnetodynamic2D(CommandManager):
    "The FEM_EquationMagnetodynamic2D command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_EquationMagnetodynamic2D", "Magnetodynamic 2D Equation"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_EquationMagnetodynamic2D",
            "Creates an equation for 2D magnetodynamic forces",
        )
        self.is_active = "with_solver_elmer"
        self.do_activated = "add_obj_on_gui_selobj_expand_noset_edit"


class _EquationStaticCurrent(CommandManager):
    "The FEM_EquationStaticCurrent command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_EquationStaticCurrent", "Static Current Equation")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_EquationStaticCurrent", "Creates an equation for static current"
        )
        self.is_active = "with_solver_elmer"
        self.do_activated = "add_obj_on_gui_selobj_expand_noset_edit"


class _Examples(CommandManager):
    "The FEM_Examples command definition"

    def __init__(self):
        super().__init__()
        self.pixmap = "FemWorkbench"
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_Examples", "FEM Examples")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_Examples", "Opens the FEM examples")
        self.is_active = "always"

    def Activated(self):
        if not self.IsActive():
            return
        FreeCADGui.addModule("femexamples.examplesgui")
        FreeCADGui.doCommand("femexamples.examplesgui.show_examplegui()")


class _MaterialEditor(CommandManager):
    "The FEM_MaterialEditor command definition"

    def __init__(self):
        super().__init__()
        self.pixmap = "FEM_Material_Group"
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MaterialEditor", "Material Editor")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_MaterialEditor", "Opens the FreeCAD material editor"
        )
        self.is_active = "always"

    def Activated(self):
        if not self.IsActive():
            return
        FreeCADGui.addModule("MaterialEditor")
        FreeCADGui.doCommand("MaterialEditor.openEditor()")


class _MaterialFluid(CommandManager):
    "The FEM_MaterialFluid command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MaterialFluid", "Fluid Material")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_MaterialFluid", "Creates a fluid material")
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _MaterialMechanicalNonlinear(CommandManager):
    "The FEM_MaterialMechanicalNonlinear command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_MaterialMechanicalNonlinear", "Non-Linear Mechanical Material"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_MaterialMechanicalNonlinear", "Add non-linear mechanical properties to material"
        )
        self.is_active = "with_material_solid"

    def IsActive(self):
        return super().IsActive() and self.selobj.Nonlinear is None

    def Activated(self):
        if not self.IsActive():
            return

        document = _active_document()
        material = self.selobj
        transaction_id = _open_exact_transaction(
            document,
            "Create FemMaterialMechanicalNonlinear",
        )
        try:
            FreeCADGui.addModule("ObjectsFem")
            nonlinear = FreeCADGui.runDocumentObjectCommand(
                document,
                "ObjectsFem.makeMaterialMechanicalNonlinear("
                f"{_document_expression(document)},"
                f" {_object_expression(material)})",
                "Fem::FeaturePython",
            )
            _require_provisional_timeline_identity(
                nonlinear,
                document,
                "The nonlinear-material factory",
            )
            if (
                material.Nonlinear is not nonlinear
            ):
                raise RuntimeError(
                    "The nonlinear material was not attached "
                    "to the selected material"
                )
            document.recompute()
            _close_exact_transaction(document, transaction_id, False)
            expandParentObject()
            FreeCADGui.Selection.clearSelection()
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise


class _MaterialReinforced(CommandManager):
    "The FEM_MaterialReinforced command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_MaterialReinforced", "Reinforced Material (Concrete)"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_MaterialReinforced",
            "Creates a material for reinforced matrix material such as concrete",
        )
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _MaterialSolid(CommandManager):
    "The FEM_MaterialSolid command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MaterialSolid", "Solid Material")
        self.accel = "M, S"
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_MaterialSolid", "Creates a solid material")
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_set_edit"


class _FEMMesh2Mesh(CommandManager):
    "The FEM_FEMMesh2Mesh command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_FEMMesh2Mesh", "FEM Mesh to Mesh")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_FEMMesh2Mesh", "Converts the surface of a FEM mesh to a mesh"
        )
        self.is_active = "with_femmesh_andor_res"

    def Activated(self):
        if not self.IsActive():
            return

        document = _active_document()
        femmesh = self.selobj
        result = self.selobj2
        transaction_id = _open_exact_transaction(
            document,
            "Create Mesh from FEMMesh",
        )
        source_was_visible = bool(femmesh.ViewObject.Visibility)
        try:
            FreeCADGui.addModule("femmesh.femmesh2mesh")
            arguments = f"{_object_expression(femmesh)}.FemMesh"
            if result is not None:
                arguments += f", {_object_expression(result)}"
            FreeCADGui.doCommand(
                "out_mesh = femmesh.femmesh2mesh.femmesh_2_mesh("
                f"{arguments})"
            )
            FreeCADGui.addModule("Mesh")
            FreeCADGui.addModule("femcommands.commands")
            mesh_name = document.getUniqueObjectName("Mesh")
            converted = FreeCADGui.runDocumentObjectCommand(
                document,
                "femcommands.commands.createMeshFeature("
                f"{_document_expression(document)}, {mesh_name!r}, "
                "Mesh.Mesh(out_mesh))",
                "Mesh::Feature",
            )
            _require_provisional_timeline_identity(
                converted,
                document,
                "The FEM mesh conversion",
            )
            if source_was_visible:
                FreeCADGui.addModule("femcommands.manager")
                FreeCADGui.doCommand(
                    "femcommands.manager."
                    "_mark_timeline_replaced_inputs("
                    f"{_object_expression(converted)}, "
                    f"[{_object_expression(femmesh)}])"
                )
            FreeCADGui.doCommand(
                f"{_object_expression(femmesh)}.ViewObject.hide()"
            )
            document.recompute()
            _close_exact_transaction(document, transaction_id, False)
            FreeCADGui.Selection.clearSelection()
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise


class _MeshBoundaryLayer(CommandManager):
    "The FEM_MeshBoundaryLayer command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MeshBoundaryLayer", "2D Boundary Layer")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_MeshBoundaryLayer",
            "Adds a structured layer of mesh elements on 2D model boundaries",
        )
        self.is_active = "with_gmsh_femmesh"
        self.do_activated = "add_obj_on_gui_selobj_set_edit"


class _MeshClear(CommandManager):
    "The FEM_MeshClear command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MeshClear", "Clear FEM Mesh")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_MeshClear", "Clears the mesh of a FEM mesh object")
        self.is_active = "with_femmesh"

    def Activated(self):
        if not self.IsActive():
            return

        document = _active_document()
        mesh = self.selobj
        transaction_id = _open_exact_transaction(
            document,
            "Clear FEM mesh",
        )
        try:
            FreeCADGui.addModule("Fem")
            FreeCADGui.doCommand(
                f"{_object_expression(mesh)}.FemMesh = Fem.FemMesh()"
            )
            document.recompute()
            _close_exact_transaction(document, transaction_id, False)
            FreeCADGui.Selection.clearSelection()
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise


class _MeshClearGroups(CommandManager):
    "The FEM_MeshClearGroups command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MeshClearGroups", "Clear Mesh Groups")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_MeshClearGroups", "Remove groups from FEM mesh")
        self.is_active = "with_femmesh"

    def Activated(self):
        if not self.IsActive():
            return

        document = _active_document()
        mesh = self.selobj
        transaction_id = _open_exact_transaction(
            document,
            "ClearGroups FEM mesh",
        )
        try:
            mesh_expression = _object_expression(mesh)
            FreeCADGui.doCommand(
                f"tuple(map({mesh_expression}.FemMesh.removeGroup,"
                f" {mesh_expression}.FemMesh.Groups))"
            )
            document.recompute()
            _close_exact_transaction(document, transaction_id, False)
            FreeCAD.Console.PrintMessage(
                f"Groups cleared: Now {mesh.Name} has "
                f"{mesh.FemMesh.GroupCount} groups\n"
            )
            FreeCADGui.Selection.clearSelection()
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise


class _MeshDisplayInfo(CommandManager):
    "The FEM_MeshDisplayInfo command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MeshDisplayInfo", "Display Mesh Info")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_MeshDisplayInfo", "Displays FEM mesh information")
        self.is_active = "with_femmesh"

    def Activated(self):
        if not self.IsActive():
            return

        from PySide import QtWidgets

        mesh = self.selobj
        mesh_info = str(mesh.FemMesh)
        FreeCAD.Console.PrintMessage(f"{mesh_info}\n")
        QtWidgets.QMessageBox.information(
            None,
            "FEM Mesh Info",
            mesh_info,
        )


class _MeshGmshFromShape(CommandManager):
    "The FEM_MeshGmshFromShape command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MeshGmshFromShape", "Mesh From Shape by Gmsh")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_MeshGmshFromShape", "Creates a FEM mesh from a shape by Gmsh mesher"
        )
        self.is_active = "with_part_feature"

    def Activated(self):
        if not self.IsActive():
            return

        document = _active_document()
        shape = self.selobj
        analysis = FemGui.getActiveAnalysis()
        if not _is_live_in_document(analysis, document):
            analysis = None
        transaction_id = _open_exact_transaction(
            document,
            "Create FEM mesh by Gmsh",
        )
        mesh_obj_name = "FEMMeshGmsh"
        try:
            FreeCADGui.addModule("ObjectsFem")
            mesh = FreeCADGui.runDocumentObjectCommand(
                document,
                "ObjectsFem.makeMeshGmsh("
                f"{_document_expression(document)}, {mesh_obj_name!r})",
                "Fem::FemMeshShapeBaseObjectPython",
            )
            _require_provisional_timeline_identity(
                mesh,
                document,
                "The Gmsh mesh factory",
            )
            mesh.Shape = shape
            mesh.ElementOrder = "2nd"
            # Curved second-order meshes retain better Jacobians when the
            # mid-side nodes are allowed to follow the source geometry.
            mesh.SecondOrderLinear = False
            if mesh.Shape is not shape:
                raise RuntimeError(
                    "Gmsh mesh did not retain its source shape"
                )
            if analysis is not None:
                analysis.addObject(mesh)
                if mesh not in analysis.Group:
                    raise RuntimeError(
                        "Gmsh mesh was not added to its analysis"
                    )
            FreeCADGui.Selection.clearSelection()
            self._start_edit(document, mesh)
        except Exception:
            _close_exact_transaction(
                document,
                transaction_id,
                True,
            )
            raise


class _MeshGroup(CommandManager):
    "The FEM_MeshGroup command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MeshGroup", "Mesh Group")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_MeshGroup", "Creates a mesh group")
        self.is_active = "with_gmsh_femmesh"
        self.do_activated = "add_obj_on_gui_selobj_set_edit"


class _MeshNetgenFromShape(CommandManager):
    "The FEM_MeshNetgenFromShape command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MeshNetgenFromShape", "Mesh From Shape by Netgen")
        self._available_tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_MeshNetgenFromShape",
            "Creates a FEM mesh from a solid or face shape by Netgen internal mesher",
        )
        self.tooltip = self._available_tooltip
        self.is_active = "with_part_feature"

    def GetResources(self):
        available, reason = _netgen_backend_status()
        self.tooltip = (
            self._available_tooltip
            if available
            else f"Netgen mesh is unavailable: {reason}."
        )
        self.resources = None
        return super().GetResources()

    def IsActive(self):
        available, _reason = _netgen_backend_status()
        return available and super().IsActive()

    def Activated(self):
        available, reason = _netgen_backend_status()
        if not available:
            FreeCAD.Console.PrintWarning(
                f"Netgen mesh is unavailable: {reason}.\n"
            )
            return
        if not self.IsActive():
            return

        document = _active_document()
        shape = self.selobj
        analysis = FemGui.getActiveAnalysis()
        if not _is_live_in_document(analysis, document):
            analysis = None
        netgen_prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Netgen")
        transaction_id = _open_exact_transaction(
            document,
            "Create FEM mesh Netgen",
        )
        mesh_obj_name = "FEMMeshNetgen"
        try:
            FreeCADGui.addModule("ObjectsFem")
            factory = (
                "makeMeshNetgenLegacy"
                if netgen_prefs.GetBool("UseLegacyNetgen", True)
                else "makeMeshNetgen"
            )
            expected_type = (
                "Fem::FemMeshShapeNetgenObject"
                if factory == "makeMeshNetgenLegacy"
                else "Fem::FemMeshShapeBaseObjectPython"
            )
            mesh = FreeCADGui.runDocumentObjectCommand(
                document,
                f"ObjectsFem.{factory}("
                f"{_document_expression(document)}, {mesh_obj_name!r})",
                expected_type,
            )
            _require_provisional_timeline_identity(
                mesh,
                document,
                "The Netgen mesh factory",
            )
            if factory == "makeMeshNetgen":
                mesh.EndStep = "OptimizeVolume"
            mesh.Shape = shape
            mesh.Fineness = "Moderate"
            if mesh.Shape is not shape:
                raise RuntimeError(
                    "Netgen mesh did not retain its source shape"
                )
            if analysis is not None:
                analysis.addObject(mesh)
                if mesh not in analysis.Group:
                    raise RuntimeError(
                        "Netgen mesh was not added to its analysis"
                    )
            FreeCADGui.Selection.clearSelection()
            self._start_edit(document, mesh)
        except Exception:
            _close_exact_transaction(
                document,
                transaction_id,
                True,
            )
            raise


class _MeshRegion(CommandManager):
    "The FEM_MeshRefinement command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MeshRegion", "Mesh Refinement")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_MeshRegion", "Creates a FEM mesh refinement")
        self.is_active = "with_femmesh"
        self.do_activated = "add_obj_on_gui_selobj_set_edit"


class _MeshDistance(CommandManager):
    "The FEM_MeshRefinement command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MeshDistance", "Distance-Based Refinement")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_MeshDistance", "Sets mesh size based on the distance to vertices, edges, and faces"
        )
        self.is_active = "with_gmsh_femmesh"
        self.do_activated = "add_obj_on_gui_selobj_set_edit"


class _MeshManipulate(CommandManager):
    "The FEM_MeshManipulate command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MeshManipulate", "Manipulate Refinement")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_MeshManipulate", "Allows to manipulate the output of a refinement in various ways"
        )
        self.is_active = "with_gmsh_femmesh"
        self.do_activated = "add_obj_on_gui_selobj_set_edit"


class _MeshAdvanced(CommandManager):
    "The FEM_MeshAdvanced command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MeshAdvanced", "Advanced Refinement Types")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_MeshAdvanced", "Allows to define the mesh size by various advanced means"
        )
        self.is_active = "with_gmsh_femmesh"
        self.do_activated = "add_obj_on_gui_selobj_set_edit"


class _MeshShape(CommandManager):
    "The FEM_MeshRefinement command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_MeshShape", "Shape-Based Refinement")
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_MeshSphere",
            "Sets mesh size within and outside of a geometric shape (box, sphere, cylinder)",
        )
        self.is_active = "with_gmsh_femmesh"
        self.do_activated = "add_obj_on_gui_selobj_set_edit"


class _MeshTransfiniteCurve(CommandManager):
    "The FEM_MeshTransfiniteCurve command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_MeshTransfiniteCurve", "Structured Transfinite Curve"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_MeshTransfiniteCurve",
            "Creates a fixed number of nodes on an edge with a structured algorithm",
        )
        self.is_active = "with_gmsh_femmesh"
        self.do_activated = "add_obj_on_gui_selobj_set_edit"


class _MeshTransfiniteSurface(CommandManager):
    "The FEM_MeshTransfiniteSurface command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_MeshTransfiniteSurface", "Structured Transfinite Surface"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_MeshTransfiniteSurface", "Creates a structured mesh on a face"
        )
        self.is_active = "with_gmsh_femmesh"
        self.do_activated = "add_obj_on_gui_selobj_set_edit"


class _MeshTransfiniteVolume(CommandManager):
    "The FEM_MeshTransfiniteVolume command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_MeshTransfiniteVolume", "Structured Transfinite Volume"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_MeshTransfiniteVolume",
            "Creates a structured mesh in a 4- or 5-sided volume bounded by transfinite surfaces",
        )
        self.is_active = "with_gmsh_femmesh"
        self.do_activated = "add_obj_on_gui_selobj_set_edit"


class _GMSHRefine:
    # Group command for all gmsh special refinements

    def GetCommands(self):
        return [
            "FEM_MeshDistance",
            "FEM_MeshBoundaryLayer",
            "FEM_MeshShape",
            "FEM_MeshManipulate",
            "FEM_MeshAdvanced",
            "FEM_MeshTransfiniteCurve",
            "FEM_MeshTransfiniteSurface",
            "FEM_MeshTransfiniteVolume",
        ]

    def GetDefaultCommand(self):
        return 0

    def GetResources(self):
        return {
            "MenuText": "GMSH Refinements",
            "ToolTip": "Mesh refinements for the GMSH mesh generation",
        }

    def IsActive(self):
        if not can_start_command() or not FreeCADGui.ActiveDocument:
            return False

        sel = FreeCADGui.Selection.getSelection()
        document = _active_document()
        if (
            len(sel) == 1
            and _is_live_in_document(sel[0], document)
            and sel[0].isDerivedFrom("Fem::FemMeshObject")
        ):
            # must be GMSH mesh
            return is_of_type(sel[0], "Fem::FemMeshGmsh")

        return False


class _ResultShow(CommandManager):
    "The FEM_ResultShow command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_ResultShow", "Show Result")
        self.accel = "R, S"
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ResultShow", "Shows and visualizes the selected result data"
        )
        self.is_active = "with_selresult"

    def Activated(self):
        if not self.IsActive():
            return

        document = _active_document()
        gui_document = FreeCADGui.getDocument(document.Name)
        result = self.selobj
        if gui_document is None or not _is_live_in_document(
            result,
            document,
        ):
            return

        gui_document.setEdit(result, 0)


class _ResultsPurge(CommandManager):
    "The FEM_ResultsPurge command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_ResultsPurge", "Purge Results")
        self.accel = "R, P"
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_ResultsPurge", "Purges all results from the active analysis"
        )
        self.is_active = "with_analysis"

    def Activated(self):
        if not self.IsActive():
            return

        import femresult.resulttools as resulttools

        document = _active_document()
        analysis = self.active_analysis
        plan = resulttools.plan_result_graph_purge(analysis)
        if plan.blockers:
            raise RuntimeError(plan.blockers[0])
        if not plan.targets:
            return

        target_identities = [
            (str(target.Name), int(target.ID))
            for target in plan.targets
        ]
        transaction_id = _open_exact_transaction(
            document,
            "Purge FEM Results",
        )
        try:
            resulttools.apply_result_graph_purge(plan)
            recompute_targets = [
                analysis,
                *(solver for solver, _roots in plan.solver_roots),
            ]
            if document.recompute(recompute_targets, True, True) is False:
                raise RuntimeError("The retained FEM analysis failed to recompute")
            survivors = [
                name
                for name, object_id in target_identities
                if (
                    (candidate := document.getObject(name)) is not None
                    and int(candidate.ID) == object_id
                )
            ]
            if survivors:
                raise RuntimeError(
                    "The FEM result graph was not purged: "
                    + ", ".join(sorted(survivors))
                )
            _close_exact_transaction(document, transaction_id, False)
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise


class _SolverCalculixContextManager:

    def __init__(self, make_name, cli_obj_ref_name):
        self.make_name = make_name
        self.cli_name = cli_obj_ref_name
        self.document = None
        self.analysis = None
        self.transaction_id = 0
        self.solver = None

    def __enter__(self):
        self.document = _active_document()
        self.analysis = FemGui.getActiveAnalysis()
        if (
            not can_start_command()
            or not _is_live_in_document(
                self.analysis,
                self.document,
            )
        ):
            raise RuntimeError(
                "The active FEM analysis is no longer available"
            )

        ccx_prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Ccx")
        self.transaction_id = _open_exact_transaction(
            self.document,
            "Create SolverCalculiX",
        )
        try:
            FreeCADGui.addModule("ObjectsFem")
            FreeCADGui.addModule("FemGui")
            self.solver = FreeCADGui.runDocumentObjectCommand(
                self.document,
                f"ObjectsFem.{self.make_name}("
                f"{_document_expression(self.document)})",
                "Fem::FemSolverObjectPython",
            )
            _require_provisional_timeline_identity(
                self.solver,
                self.document,
                "The CalculiX solver factory",
            )
            settings = {
                "AnalysisType": ccx_prefs.GetInt("AnalysisType", 0),
                "EigenmodesCount": ccx_prefs.GetInt(
                    "EigenmodesCount",
                    10,
                ),
                "EigenmodeLowLimit": ccx_prefs.GetFloat(
                    "EigenmodeLowLimit",
                    0.0,
                ),
                "EigenmodeHighLimit": ccx_prefs.GetFloat(
                    "EigenmodeHighLimit",
                    1000000.0,
                ),
                "IncrementsMaximum": ccx_prefs.GetInt(
                    "StepMaxIncrements",
                    2000,
                ),
                "TimeInitialIncrement": ccx_prefs.GetFloat(
                    "TimeInitialIncrement",
                    1.0,
                ),
                "TimePeriod": ccx_prefs.GetFloat(
                    "TimePeriod",
                    1.0,
                ),
                "TimeMinimumIncrement": ccx_prefs.GetFloat(
                    "TimeMinimumIncrement",
                    0.00001,
                ),
                "TimeMaximumIncrement": ccx_prefs.GetFloat(
                    "TimeMaximumIncrement",
                    1.0,
                ),
                "ThermoMechSteadyState": ccx_prefs.GetBool(
                    "StaticAnalysis",
                    True,
                ),
                "IterationsControlParameterTimeUse": (
                    ccx_prefs.GetBool(
                        "UseNonCcxIterationParam",
                        False,
                    )
                ),
                "SplitInputWriter": ccx_prefs.GetBool(
                    "SplitInputWriter",
                    False,
                ),
                "MatrixSolverType": ccx_prefs.GetInt("Solver", 0),
                "Output3d": ccx_prefs.GetBool(
                    "BeamShellOutput",
                    True,
                ),
                "GeometricalNonlinearity": ccx_prefs.GetBool(
                    "NonlinearGeometry",
                    False,
                ),
            }
            for property_name, value in settings.items():
                setattr(self.solver, property_name, value)
        except Exception:
            _close_exact_transaction(
                self.document,
                self.transaction_id,
                True,
            )
            raise

        return self

    def __exit__(self, exc_type, exc_value, trace):
        if exc_type is not None:
            _close_exact_transaction(
                self.document,
                self.transaction_id,
                True,
            )
            return False

        try:
            if (
                not _is_live_in_document(
                    self.analysis,
                    self.document,
                )
                or not _is_live_in_document(
                    self.solver,
                    self.document,
                )
            ):
                raise RuntimeError(
                    "The CalculiX command target is no longer available"
                )
            self.analysis.addObject(self.solver)
            if self.solver not in self.analysis.Group:
                raise RuntimeError(
                    "CalculiX was not added to its analysis"
                )
            self.document.recompute()
            _close_exact_transaction(
                self.document,
                self.transaction_id,
                False,
            )
            expandParentObject()
        except Exception:
            _close_exact_transaction(
                self.document,
                self.transaction_id,
                True,
            )
            raise
        return False


class _SolverCcxTools(CommandManager):
    "The FEM_SolverCalculix ccx tools command definition"

    def __init__(self):
        super().__init__()
        self.pixmap = "FEM_SolverStandard"
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_SolverCalculiXCcxTools", "Solver CalculiX Standard"
        )
        self.accel = "S, X"
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_SolverCalculiXCcxTools",
            "Creates a standard FEM solver CalculiX with ccx tools",
        )
        self.is_active = "with_analysis"

    def Activated(self):
        if not self.IsActive():
            return
        with _SolverCalculixContextManager("makeSolverCalculiXCcxTools", "solver") as cm:
            cm.solver.MaterialNonlinearity = True


class _SolverCalculiX(CommandManager):
    "The FEM_SolverCalculiX command definition"

    def __init__(self):
        super().__init__()
        self.pixmap = "FEM_SolverStandard"
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_SolverCalculiX", "Solver CalculiX")
        self.accel = "S, C"
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_SolverCalculiX",
            "Creates a FEM solver CalculiX",
        )
        self.is_active = "with_analysis"

    def Activated(self):
        if not self.IsActive():
            return

        ccx_prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Ccx")
        if ccx_prefs.GetBool("ResultAsPipeline", True):
            make_solver = "makeSolverCalculiX"
        else:
            make_solver = "makeSolverCalculiXCcxTools"

        with _SolverCalculixContextManager(make_solver, "solver") as cm:
            cm.solver.MaterialNonlinearity = True


class _SolverControl(CommandManager):
    "The FEM_SolverControl command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_SolverControl", "Solver Job Control")
        self.accel = "S, T"
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_SolverControl",
            "Changes solver attributes and runs the calculations for the selected solver",
        )
        self.is_active = "with_solver"

    def Activated(self):
        if not self.IsActive():
            return

        document = _active_document()
        gui_document = FreeCADGui.getDocument(document.Name)
        solver = self.selobj
        if gui_document is None or not _is_live_in_document(
            solver,
            document,
        ):
            return

        transaction_id = _open_exact_transaction(
            document,
            "Edit FEM solver",
        )
        try:
            if not gui_document.setEdit(solver, 0):
                raise RuntimeError(
                    "The selected FEM solver editor could not be opened"
                )
            if not FreeCADGui.Control.ownsCommandTransaction(
                gui_document,
                transaction_id,
            ):
                raise RuntimeError(
                    "The selected FEM solver editor could not adopt its "
                    "command transaction"
                )
            editing = gui_document.getInEdit()
            task = FreeCADGui.Control.activeTaskDialog(
                gui_document,
            )
            if (
                editing is None
                or getattr(editing, "Object", None) is not solver
                or task is None
                or not task.ownsCommandTransaction(
                    transaction_id,
                )
            ):
                raise RuntimeError(
                    "The selected FEM solver editor did not adopt its "
                    "command transaction"
                )
        except Exception:
            task = FreeCADGui.Control.activeTaskDialog(
                gui_document,
            )
            editing = gui_document.getInEdit()
            if (
                task is not None
                and editing is not None
                and getattr(editing, "Object", None) is solver
            ):
                task.reject()
            elif (
                editing is not None
                and getattr(editing, "Object", None) is solver
            ):
                gui_document.resetEdit()
            _close_exact_transaction(
                document,
                transaction_id,
                True,
            )
            raise


class _SolverElmer(CommandManager):
    "The FEM_SolverElmer command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_SolverElmer", "Solver Elmer")
        self.accel = "S, E"
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_SolverElmer", "Creates a FEM solver Elmer")
        self.is_active = "with_analysis"

    def Activated(self):
        if not self.IsActive():
            return

        document = _active_document()
        analysis = self.active_analysis
        transaction_id = _open_exact_transaction(
            document,
            "Create Fem SolverElmer",
        )
        elmer_prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Elmer")
        try:
            FreeCADGui.addModule("ObjectsFem")
            solver = FreeCADGui.runDocumentObjectCommand(
                document,
                "ObjectsFem.makeSolverElmer("
                f"{_document_expression(document)})",
                "Fem::FemSolverObjectPython",
            )
            _require_provisional_timeline_identity(
                solver,
                document,
                "The Elmer solver factory",
            )
            solver.BinaryOutput = elmer_prefs.GetBool(
                "BinaryOutput",
                False,
            )
            solver.SaveGeometryIndex = elmer_prefs.GetBool(
                "SaveGeometryIndex",
                False,
            )
            analysis.addObject(solver)
            if solver not in analysis.Group:
                raise RuntimeError(
                    "Elmer was not added to its analysis"
                )
            document.recompute()
            _close_exact_transaction(document, transaction_id, False)
            expandParentObject()
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(solver)
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise


class _SolverOpenFOAM(CommandManager):
    "The FEM_SolverOpenFOAM command definition"

    def __init__(self):
        super().__init__()
        self.pixmap = "FEM_SolverStandard"
        self.menutext = Qt.QT_TRANSLATE_NOOP(
            "FEM_SolverOpenFOAM", "Solver OpenFOAM"
        )
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_SolverOpenFOAM", "Creates an OpenFOAM CFD solver"
        )
        self.is_active = "with_analysis"

    def Activated(self):
        if not self.IsActive():
            return

        document = _active_document()
        analysis = self.active_analysis
        transaction_id = _open_exact_transaction(
            document,
            "Create Fem SolverOpenFOAM",
        )
        try:
            FreeCADGui.addModule("ObjectsFem")
            solver = FreeCADGui.runDocumentObjectCommand(
                document,
                "ObjectsFem.makeSolverOpenFOAM("
                f"{_document_expression(document)})",
                "Fem::FemSolverObjectPython",
            )
            _require_provisional_timeline_identity(
                solver,
                document,
                "The OpenFOAM solver factory",
            )
            analysis.addObject(solver)
            if solver not in analysis.Group:
                raise RuntimeError("OpenFOAM was not added to its analysis")
            document.recompute()
            _close_exact_transaction(document, transaction_id, False)
            expandParentObject()
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(solver)
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise


class _SolverMystran(CommandManager):
    "The FEM_SolverMystran command definition"

    def __init__(self):
        super().__init__()
        self.pixmap = "FEM_SolverMystran"
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_SolverMystran", "Solver Mystran")
        self.accel = "S, M"
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_SolverMystran", "Creates a FEM solver Mystran")
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_expand_noset_edit"


class _SolverRun(CommandManager):
    "The FEM_SolverRun command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_SolverRun", "Run Solver")
        self.accel = "S, R"
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_SolverRun", "Runs the calculations for the selected solver"
        )
        self.is_active = "with_solver"
        self.tool = None

    def Activated(self):
        if not self.IsActive():
            return

        from femsolver.run import run_fem_solver

        document = _active_document()
        solver = self.selobj
        run_fem_solver(solver)
        FreeCADGui.Selection.clearSelection()
        if _is_live_in_document(solver, document):
            document.recompute()


class _SolverZ88(CommandManager):
    "The FEM_SolverZ88 command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_SolverZ88", "Solver Z88")
        self.accel = "S, Z"
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_SolverZ88", "Creates a FEM solver Z88")
        self.is_active = "with_analysis"
        self.do_activated = "add_obj_on_gui_expand_noset_edit"

    def Activated(self):
        if not self.IsActive():
            return

        document = _active_document()
        analysis = self.active_analysis
        transaction_id = _open_exact_transaction(
            document,
            "Create Fem SolverZ88",
        )
        z88_prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Z88")
        try:
            FreeCADGui.addModule("ObjectsFem")
            solver = FreeCADGui.runDocumentObjectCommand(
                document,
                "ObjectsFem.makeSolverZ88("
                f"{_document_expression(document)})",
                "Fem::FemSolverObjectPython",
            )
            _require_provisional_timeline_identity(
                solver,
                document,
                "The Z88 solver factory",
            )
            solver.SolverType = z88_prefs.GetString(
                "Solver",
                "sorcg",
            )
            solver.MatrixMaximum = z88_prefs.GetInt(
                "MaxGS",
                100000000,
            )
            solver.VectorMaximum = z88_prefs.GetInt(
                "MaxKOI",
                2800000,
            )
            analysis.addObject(solver)
            if solver not in analysis.Group:
                raise RuntimeError(
                    "Z88 was not added to its analysis"
                )
            document.recompute()
            _close_exact_transaction(document, transaction_id, False)
            expandParentObject()
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(solver)
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise


class _PostFilterGlyph(CommandManager):
    "The FEM_PostFilterGlyph command definition"

    def __init__(self):
        super().__init__()
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_PostFilterGlyph", "Glyph Filter")
        self.accel = "F, G"
        self.tooltip = Qt.QT_TRANSLATE_NOOP(
            "FEM_PostFilterGlyph",
            "Adds a post-processing filter that adds glyphs to the mesh vertices for vertex data visualization",
        )
        self.is_active = "with_vtk_selresult"
        self.do_activated = "add_filter_set_edit"


class _CompSolvers(CommandManager):
    def __init__(self):
        super().__init__()
        self.pixmap = ""
        self.menutext = Qt.QT_TRANSLATE_NOOP("FEM_CompSolvers", "Solvers")
        self.tooltip = Qt.QT_TRANSLATE_NOOP("FEM_CompSolvers", "Creates a FEM solver")
        self.is_active = "with_analysis"
        self.commands = [
            "FEM_SolverCalculiX",
            "FEM_SolverElmer",
            "FEM_SolverMystran",
            "FEM_SolverZ88",
            "FEM_SolverOpenFOAM",
        ]

    def Activated(self, i):
        if not self.IsActive() or not 0 <= i < len(self.commands):
            return
        FreeCADGui.runCommand(self.commands[i])

    def GetCommands(self):
        return self.commands

    def GetDefaultCommand(self):
        gen_prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/General")
        # DefaultSolver == 0 is "None"
        index = gen_prefs.GetInt("DefaultSolver", 0)
        return (
            index - 1
            if 1 <= index <= len(self.commands)
            else 0
        )


# the string in add command will be the page name on FreeCAD wiki
FreeCADGui.addCommand("FEM_Analysis", _Analysis())
FreeCADGui.addCommand("FEM_ClippingPlaneAdd", _ClippingPlaneAdd())
FreeCADGui.addCommand("FEM_ClippingPlaneRemoveAll", _ClippingPlaneRemoveAll())
FreeCADGui.addCommand("FEM_ConstantVacuumPermittivity", _ConstantVacuumPermittivity())
FreeCADGui.addCommand("FEM_ConstraintBodyHeatSource", _ConstraintBodyHeatSource())
FreeCADGui.addCommand("FEM_ConstraintCentrif", _ConstraintCentrif())
FreeCADGui.addCommand("FEM_ConstraintCurrentDensity", _ConstraintCurrentDensity())
FreeCADGui.addCommand("FEM_ConstraintElectricChargeDensity", _ConstraintElectricChargeDensity())
FreeCADGui.addCommand("FEM_ConstraintElectromagnetic", _ConstraintElectromagnetic())
FreeCADGui.addCommand("FEM_ConstraintFlowVelocity", _ConstraintFlowVelocity())
FreeCADGui.addCommand("FEM_ConstraintInitialFlowVelocity", _ConstraintInitialFlowVelocity())
FreeCADGui.addCommand("FEM_ConstraintInitialPressure", _ConstraintInitialPressure())
FreeCADGui.addCommand("FEM_ConstraintMagnetization", _ConstraintMagnetization())
FreeCADGui.addCommand("FEM_ConstraintSectionPrint", _ConstraintSectionPrint())
FreeCADGui.addCommand("FEM_ConstraintSelfWeight", _ConstraintSelfWeight())
FreeCADGui.addCommand("FEM_ConstraintTie", _ConstraintTie())
FreeCADGui.addCommand("FEM_ElementFluid1D", _ElementFluid1D())
FreeCADGui.addCommand("FEM_ElementGeometry1D", _ElementGeometry1D())
FreeCADGui.addCommand("FEM_ElementGeometry2D", _ElementGeometry2D())
FreeCADGui.addCommand("FEM_ElementRotation1D", _ElementRotation1D())
FreeCADGui.addCommand("FEM_EquationDeformation", _EquationDeformation())
FreeCADGui.addCommand("FEM_EquationElasticity", _EquationElasticity())
FreeCADGui.addCommand("FEM_EquationElectricforce", _EquationElectricforce())
FreeCADGui.addCommand("FEM_EquationElectrostatic", _EquationElectrostatic())
FreeCADGui.addCommand("FEM_EquationFlow", _EquationFlow())
FreeCADGui.addCommand("FEM_EquationFlux", _EquationFlux())
FreeCADGui.addCommand("FEM_EquationHeat", _EquationHeat())
FreeCADGui.addCommand("FEM_EquationMagnetodynamic", _EquationMagnetodynamic())
FreeCADGui.addCommand("FEM_EquationMagnetodynamic2D", _EquationMagnetodynamic2D())
FreeCADGui.addCommand("FEM_EquationStaticCurrent", _EquationStaticCurrent())
FreeCADGui.addCommand("FEM_Examples", _Examples())
FreeCADGui.addCommand("FEM_MaterialEditor", _MaterialEditor())
FreeCADGui.addCommand("FEM_MaterialFluid", _MaterialFluid())
FreeCADGui.addCommand("FEM_MaterialMechanicalNonlinear", _MaterialMechanicalNonlinear())
FreeCADGui.addCommand("FEM_MaterialReinforced", _MaterialReinforced())
FreeCADGui.addCommand("FEM_MaterialSolid", _MaterialSolid())
FreeCADGui.addCommand("FEM_FEMMesh2Mesh", _FEMMesh2Mesh())
FreeCADGui.addCommand("FEM_MeshBoundaryLayer", _MeshBoundaryLayer())
FreeCADGui.addCommand("FEM_MeshClear", _MeshClear())
FreeCADGui.addCommand("FEM_MeshClearGroups", _MeshClearGroups())
FreeCADGui.addCommand("FEM_MeshDisplayInfo", _MeshDisplayInfo())
FreeCADGui.addCommand("FEM_MeshGmshFromShape", _MeshGmshFromShape())
FreeCADGui.addCommand("FEM_MeshGroup", _MeshGroup())
FreeCADGui.addCommand("FEM_MeshNetgenFromShape", _MeshNetgenFromShape())
FreeCADGui.addCommand("FEM_MeshRegion", _MeshRegion())
FreeCADGui.addCommand("FEM_MeshDistance", _MeshDistance())
FreeCADGui.addCommand("FEM_MeshManipulate", _MeshManipulate())
FreeCADGui.addCommand("FEM_MeshAdvanced", _MeshAdvanced())
FreeCADGui.addCommand("FEM_MeshShape", _MeshShape())
FreeCADGui.addCommand("FEM_MeshTransfiniteCurve", _MeshTransfiniteCurve())
FreeCADGui.addCommand("FEM_MeshTransfiniteSurface", _MeshTransfiniteSurface())
FreeCADGui.addCommand("FEM_MeshTransfiniteVolume", _MeshTransfiniteVolume())
FreeCADGui.addCommand("FEM_MeshGMSHRefinement", _GMSHRefine())
FreeCADGui.addCommand("FEM_ResultShow", _ResultShow())
FreeCADGui.addCommand("FEM_ResultsPurge", _ResultsPurge())
FreeCADGui.addCommand("FEM_SolverCalculiXCcxTools", _SolverCcxTools())
FreeCADGui.addCommand("FEM_SolverCalculiX", _SolverCalculiX())
FreeCADGui.addCommand("FEM_SolverControl", _SolverControl())
FreeCADGui.addCommand("FEM_SolverElmer", _SolverElmer())
FreeCADGui.addCommand("FEM_SolverOpenFOAM", _SolverOpenFOAM())
FreeCADGui.addCommand("FEM_SolverMystran", _SolverMystran())
FreeCADGui.addCommand("FEM_SolverRun", _SolverRun())
FreeCADGui.addCommand("FEM_SolverZ88", _SolverZ88())
FreeCADGui.addCommand("FEM_CompSolvers", _CompSolvers())

if "BUILD_FEM_VTK_PYTHON" in FreeCAD.__cmake__:
    FreeCADGui.addCommand("FEM_PostFilterGlyph", _PostFilterGlyph())

    # setup all visualization commands (register by importing)
    import femobjects.post_lineplot
    import femobjects.post_histogram
    import femobjects.post_table

    from femguiutils import post_visualization

    post_visualization.setup_commands("FEM_PostVisualization")
