# ***************************************************************************
# *   Copyright (c) 2017 Bernd Hahnebach <bernd@bimstatik.org>              *
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

__title__ = "Fem Tools for results"
__author__ = "Bernd Hahnebach"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import numpy as np
from dataclasses import dataclass
from math import isnan

import FreeCAD

from femtools.femutils import is_of_type


@dataclass(frozen=True)
class ResultPurgePlan:
    """Exact FEM result graph selected for one atomic purge."""

    analysis: object
    targets: tuple
    solver_roots: tuple
    ordinary_operations: tuple
    operation_resources: tuple
    untracked: tuple
    blockers: tuple


def _is_live(document, obj):
    if document is None or obj is None:
        return False
    try:
        return (
            obj.Document is document
            and document.getObject(str(obj.Name)) is obj
            and document.getObject(int(obj.ID)) is obj
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return False


def _is_derived(obj, type_name):
    try:
        return bool(obj.isDerivedFrom(type_name))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _proxy_type(obj):
    return str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")


def _is_solver(obj):
    return _proxy_type(obj).startswith("Fem::Solver") and "Results" in tuple(
        getattr(obj, "PropertiesList", ()) or ()
    )


def _is_result_artifact(obj):
    return (
        _is_derived(obj, "Fem::FemResultObject")
        or _is_derived(obj, "Fem::FemPostPipeline")
        or _is_derived(obj, "Fem::FemPostBranchFilter")
        or _is_derived(obj, "Fem::FemPostFilter")
        or _is_derived(obj, "Fem::FemPostFunctionProvider")
        or _is_derived(obj, "Fem::FemPostFunction")
        or _proxy_type(obj) in {"Fem::MeshResult", "Fem::FemPostVisualization"}
        or str(getattr(obj, "TypeId", "") or "") == "App::TextDocument"
    )


def _timeline_role(obj):
    return str(getattr(obj, "VibeCADTimelineRole", "") or "")


def _timeline_owner(obj):
    return getattr(obj, "VibeCADTimelineOwner", None)


def _timeline_root(obj, document):
    current = obj
    visited = set()
    while _is_live(document, current) and _timeline_role(current) == "resource":
        identity = int(current.ID)
        if identity in visited:
            return None
        visited.add(identity)
        current = _timeline_owner(current)
    return current if _is_live(document, current) else None


def _append_live(document, values, obj):
    if _is_live(document, obj) and obj not in values:
        values.append(obj)


def _append_group_graph(document, values, root):
    pending = [root]
    visited = set()
    while pending:
        current = pending.pop()
        if not _is_live(document, current):
            continue
        identity = int(current.ID)
        if identity in visited:
            continue
        visited.add(identity)
        _append_live(document, values, current)
        pending.extend(tuple(getattr(current, "Group", ()) or ()))


def _label(obj):
    return str(getattr(obj, "Label", getattr(obj, "Name", "FEM object")))


def _solver_result_graph(document, solver, blockers):
    linked_results = tuple(getattr(solver, "Results", ()) or ())
    if not linked_results:
        return (), ()

    from femcommands import manager

    try:
        _document, resources, _direct_roots = manager._timeline_owned_resource_graph(
            solver
        )
    except Exception as exc:
        blockers.append(f"{_label(solver)}: {exc}")
        return (), ()
    if len(linked_results) != len(set(linked_results)):
        blockers.append(
            f"{_label(solver)}: solver.Results contains duplicate identities"
        )
        return (), ()
    if any(
        not _is_live(document, result)
        or result not in resources
        or _timeline_root(result, document) is not solver
        for result in linked_results
    ):
        blockers.append(
            f"{_label(solver)}: solver.Results contains an identity outside its "
            "canonical History resource graph"
        )
        return (), ()
    roots = tuple(
        result for result in linked_results if _timeline_owner(result) is solver
    )
    if not roots:
        blockers.append(
            f"{_label(solver)}: solver.Results has no direct result root owned by the solver"
        )
        return (), ()
    if any(
        not any(
            manager._timeline_owner_chain_contains(result, root, document)
            for root in roots
        )
        for result in linked_results
    ):
        blockers.append(
            f"{_label(solver)}: solver.Results does not form complete rooted result graphs"
        )
        return (), ()

    selected = []
    for root in roots:
        _append_live(document, selected, root)
        for resource in resources:
            try:
                if manager._timeline_owner_chain_contains(resource, root, document):
                    _append_live(document, selected, resource)
            except Exception as exc:
                blockers.append(f"{_label(root)}: {exc}")
                break
    return roots, tuple(selected)


def plan_result_graph_purge(analysis):
    """Plan the complete exact result/post graph of one FEM analysis.

    Planning is read-only.  Canonical solver-owned results, independent
    post-processing operations, nested resources, and legacy untracked result
    artifacts are distinguished so a caller can either report blockers or
    execute one dependency-safe transaction.
    """

    document = getattr(analysis, "Document", None)
    if not _is_live(document, analysis) or not _is_derived(
        analysis, "Fem::FemAnalysis"
    ):
        raise ValueError("A result purge requires one exact live FEM analysis")
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    blockers = []
    targets = []
    solver_roots = []
    solver_resources = set()
    solvers = set()
    members = tuple(getattr(analysis, "Group", ()) or ())

    for member in members:
        if not _is_solver(member):
            continue
        solvers.add(member)
        roots, resources = _solver_result_graph(document, member, blockers)
        if roots:
            solver_roots.append((member, roots))
        for resource in resources:
            solver_resources.add(resource)
            _append_live(document, targets, resource)

    for member in members:
        if not _is_result_artifact(member):
            continue
        _append_group_graph(document, targets, member)
        if _is_derived(member, "Fem::FemResultObject"):
            mesh = getattr(member, "Mesh", None)
            if _proxy_type(mesh) == "Fem::MeshResult":
                _append_live(document, targets, mesh)

    ordinary_operations = [
        candidate
        for candidate in targets
        if candidate not in solver_resources
        and _timeline_role(candidate) == "operation"
        and candidate in operations
    ]
    ordinary_operations.sort(key=operations.index, reverse=True)
    operation_resources = []
    ordinary_resource_set = set()
    for operation in ordinary_operations:
        resources = tuple(
            candidate
            for candidate in operations
            if candidate is not operation
            and _timeline_role(candidate) == "resource"
            and _timeline_root(candidate, document) is operation
        )
        for resource in resources:
            _append_live(document, targets, resource)
        operation_resources.append((operation, resources))
        ordinary_resource_set.update(resources)

    untracked = tuple(
        candidate
        for candidate in targets
        if candidate not in solver_resources
        and candidate not in ordinary_operations
        and candidate not in ordinary_resource_set
    )
    allowed = {analysis, timeline, *targets, *solvers}
    for target in targets:
        unexpected = [
            consumer
            for consumer in tuple(getattr(target, "InList", ()) or ())
            if consumer not in allowed
        ]
        if unexpected:
            labels = ", ".join(_label(value) for value in unexpected[:4])
            blockers.append(
                f"{_label(target)} has downstream consumers outside the purge graph: "
                f"{labels}"
            )
    return ResultPurgePlan(
        analysis,
        tuple(targets),
        tuple(solver_roots),
        tuple(ordinary_operations),
        tuple(operation_resources),
        untracked,
        tuple(dict.fromkeys(blockers)),
    )


def _object_identity(obj):
    return str(obj.Name), int(obj.ID)


def _plan_signature(plan):
    return (
        _object_identity(plan.analysis),
        tuple(_object_identity(value) for value in plan.targets),
        tuple(
            (_object_identity(solver), tuple(_object_identity(root) for root in roots))
            for solver, roots in plan.solver_roots
        ),
        tuple(_object_identity(value) for value in plan.ordinary_operations),
        tuple(
            (
                _object_identity(operation),
                tuple(_object_identity(resource) for resource in resources),
            )
            for operation, resources in plan.operation_resources
        ),
        tuple(_object_identity(value) for value in plan.untracked),
        plan.blockers,
    )


def _remove_exact(document, obj):
    name, identity = _object_identity(obj)
    if not _is_live(document, obj):
        raise RuntimeError(f"Result object {name!r} changed before purge")
    document.removeObject(name)
    survivor = document.getObject(name)
    if survivor is not None and int(survivor.ID) == identity:
        raise RuntimeError(f"Result object {name!r} refused deletion")


def _remove_dependency_graph(document, objects):
    remaining = list(objects)
    while remaining:
        remaining = [value for value in remaining if _is_live(document, value)]
        if not remaining:
            break
        removable = next(
            (
                candidate
                for candidate in remaining
                if not any(
                    consumer in remaining
                    for consumer in tuple(getattr(candidate, "InList", ()) or ())
                )
            ),
            None,
        )
        if removable is None:
            names = ", ".join(_label(value) for value in remaining[:6])
            raise RuntimeError(
                f"The FEM result graph contains a dependency cycle: {names}"
            )
        _remove_exact(document, removable)
        remaining.remove(removable)


def apply_result_graph_purge(plan):
    """Apply one previously planned purge inside the caller's transaction."""

    if not isinstance(plan, ResultPurgePlan):
        raise TypeError("plan must be a ResultPurgePlan")
    if plan.blockers:
        raise RuntimeError(plan.blockers[0])
    if not plan.targets:
        return ()
    current = plan_result_graph_purge(plan.analysis)
    if _plan_signature(current) != _plan_signature(plan):
        raise RuntimeError("The exact FEM result graph changed after purge planning")

    document = plan.analysis.Document
    target_identities = tuple(_object_identity(value) for value in plan.targets)
    resources_by_operation = dict(plan.operation_resources)
    for operation in plan.ordinary_operations:
        for resource in resources_by_operation.get(operation, ()):
            if _is_live(document, resource):
                _remove_exact(document, resource)
        if _is_live(document, operation):
            _remove_exact(document, operation)
    _remove_dependency_graph(
        document,
        tuple(value for value in plan.untracked if _is_live(document, value)),
    )

    from femcommands import manager

    for solver, roots in plan.solver_roots:
        manager._purge_timeline_result_roots(solver, roots)
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
            "The exact FEM result graph was not fully purged: "
            + ", ".join(survivors[:8])
        )
    return target_identities


def purge_result_targets(analysis):
    """Return the exact live result graph removed by ``Purge Results``.

    The GUI command passes this complete set through the normal native delete
    command so ViewProviders, document-history ownership, undo, and restored
    replacement visibility all use the same deletion contract as every other
    ribbon tool.
    """

    document = getattr(analysis, "Document", None)
    if document is None:
        return []

    result = []
    seen = set()
    visited_groups = set()

    def append(obj):
        if (
            obj is None
            or getattr(obj, "Document", None) is not document
            or document.getObject(getattr(obj, "Name", "")) is not obj
        ):
            return
        identity = int(obj.ID)
        if identity in seen:
            return
        seen.add(identity)
        result.append(obj)

    def append_post_group(group):
        group_identity = int(group.ID)
        if group_identity in visited_groups:
            return
        visited_groups.add(group_identity)
        for child in list(getattr(group, "Group", []) or []):
            append_post_group(child)
            append(child)

    for member in list(getattr(analysis, "Group", []) or []):
        if member.isDerivedFrom("Fem::FemResultObject"):
            mesh = getattr(member, "Mesh", None)
            if mesh is not None and is_of_type(mesh, "Fem::MeshResult"):
                append(mesh)
            append(member)
        elif (
            is_of_type(member, "Fem::MeshResult")
            or is_of_type(member, "App::TextDocument")
            or is_of_type(member, "Fem::FemPostVisualization")
        ):
            append(member)
        elif member.isDerivedFrom("Fem::FemPostPipeline"):
            append_post_group(member)
            append(member)
    return result


def purge_result_objects(analysis):
    """Removes all result objects and result meshes from an analysis group.

    Parameters
    ----------
    analysis : Fem::FemAnalysis
        analysis group as a container for all  objects needed for the analysis
    """

    # result object
    for m in analysis.Group:
        if m.isDerivedFrom("Fem::FemResultObject"):
            if m.Mesh and is_of_type(m.Mesh, "Fem::MeshResult"):
                analysis.Document.removeObject(m.Mesh.Name)
            analysis.Document.removeObject(m.Name)
    analysis.Document.recompute()

    # result mesh
    for m in analysis.Group:
        if is_of_type(m, "Fem::MeshResult"):
            analysis.Document.removeObject(m.Name)
    analysis.Document.recompute()

    # text object
    for m in analysis.Group:
        if is_of_type(m, "App::TextDocument"):
            analysis.Document.removeObject(m.Name)
    analysis.Document.recompute()


def purge_postprocessing_objects(analysis):
    """Removes all postprocessing objects and visualizations form the analysis

    Parameters
    ----------
    analysis : Fem::FemAnalysis
        analysis group as a container for all  objects needed for the analysis
    """

    # result pipeline and filter
    for m in analysis.Group:
        if is_of_type(m, "Fem::FemPostPipeline"):
            # delete the pipeline itself (it removes all filters itself)
            analysis.Document.removeObject(m.Name)
    analysis.Document.recompute()

    # remove visualizations
    for m in analysis.Group:
        if is_of_type(m, "Fem::FemPostVisualization"):
            analysis.Document.removeObject(m.Name)
    analysis.Document.recompute()


def purge_results(analysis):
    """Removes all result and postprocessing objects and result meshes from an analysis group.

    Parameters
    ----------
    analysis : Fem::FemAnalysis
        analysis group as a container for all  objects needed for the analysis
    """

    # if analysis type check is used, result mesh
    # without result obj is created in the analysis
    # we could run into trouble in one loop because
    # we will delete objects and try to access them later

    purge_result_objects(analysis)
    purge_postprocessing_objects(analysis)


def reset_mesh_deformation(resultobj):
    """Resets result mesh deformation.

    Parameters
    ----------
    resultobj : Fem::ResultMechanical
        FreeCAD FEM mechanical result object
    """

    if FreeCAD.GuiUp:
        if resultobj.Mesh:
            resultobj.Mesh.ViewObject.applyDisplacement(0.0)


def reset_mesh_color(resultobj):
    """Resets result mesh color

    Parameters
    ----------
    resultobj : Fem::ResultMechanical
        FreeCAD FEM mechanical result object
    """

    if FreeCAD.GuiUp:
        if resultobj.Mesh:
            resultobj.Mesh.ViewObject.NodeColor = {}
            resultobj.Mesh.ViewObject.ElementColor = {}
            node_numbers = resultobj.Mesh.FemMesh.Nodes.keys()
            zero_values = [0] * len(node_numbers)
            resultobj.Mesh.ViewObject.setNodeColorByScalars(node_numbers, zero_values)


def show_displacement(resultobj, displacement_factor=0.0):
    if FreeCAD.GuiUp:
        if resultobj.Mesh.ViewObject.Visibility is False:
            resultobj.Mesh.ViewObject.Visibility = True
        resultobj.Mesh.ViewObject.setNodeDisplacementByVectors(
            resultobj.NodeNumbers, resultobj.DisplacementVectors
        )
        resultobj.Mesh.ViewObject.applyDisplacement(displacement_factor)


def show_result(resultobj, result_type="Sabs", limit=None):
    """Sets mesh color using selected type of results.

    Parameters
    ----------
    resultobj : Fem::ResultMechanical
        FreeCAD FEM mechanical result object
    result_type : str, optional
        default is Sabs
        FreeCAD FEM mechanical result object
        - U1, U2, U3 - deformation
        - Uabs - absolute deformation
        - Sabs - Von Mises stress
    limit : float
        limit cutoff value. All values over the limit are treated
        as equal to the limit. Useful for filtering out hotspots.
    """

    if result_type == "None":
        reset_mesh_color(resultobj.Mesh)
        return
    if resultobj:
        if result_type == "Sabs":
            values = resultobj.vonMises
        elif result_type == "Uabs":
            values = resultobj.DisplacementLengths
        # TODO: the result object does have more result types to show, implement them
        else:
            match = {"U1": 0, "U2": 1, "U3": 2}
            d = zip(*resultobj.DisplacementVectors)
            values = list(d[match[result_type]])
        show_color_by_scalar_with_cutoff(resultobj, values, limit)
    else:
        FreeCAD.Console.PrintError("Error, No result object given.\n")


def show_color_by_scalar_with_cutoff(resultobj, values, limit=None):
    """Sets mesh color using list of values. Internally used by show_result function.

    Parameters
    ----------
    resultobj : Fem::ResultMechanical
        FreeCAD FEM mechanical result object
    values : list of floats
        the values to be colored and cutoff
        has to be the same length as resultobj.NodeNumbers
        resultobj.NodeNumbers has to be present in the resultobj
    limit : float
        limit cutoff value. All values over the limit are treated
        as equal to the limit. Useful for filtering out hotspots.
    """

    if limit:
        filtered_values = []
        for v in values:
            if v > limit:
                filtered_values.append(limit)
            else:
                filtered_values.append(v)
    else:
        filtered_values = values
    if FreeCAD.GuiUp:
        if resultobj.Mesh.ViewObject.Visibility is False:
            resultobj.Mesh.ViewObject.Visibility = True
        resultobj.Mesh.ViewObject.setNodeColorByScalars(resultobj.NodeNumbers, filtered_values)


def get_stats(res_obj, result_type):
    """Returns minimum and maximum value for provided result type

    Parameters
    ----------
    resultobj : Fem::ResultMechanical
        FreeCAD FEM mechanical result object
    result_type : str
        type of FEM result
        allowed are: see dict keys in def get_all_stats()
        None - always return (0.0, 0.0)

    """

    match_table = get_all_stats(res_obj)
    match_table["None"] = (0.0, 0.0)
    stats = ()
    if result_type in match_table:
        stats = match_table[result_type]
    return stats


#  - U1, U2, U3 - deformation
#  - Uabs - absolute deformation
#  - Sabs - Von Mises stress
#  - Prin1 - Principal stress 1
#  - Prin2 - Principal stress 2
#  - Prin3 - Principal stress 3
#  - MaxSear - maximum shear stress
#  - Peeq - Equivalent plastic strain
#  - Temp - Temperature
#  - MFlow - MassFlowRate
#  - NPress - NetworkPressure
def get_all_stats(res_obj):
    """Returns all stats for provided result type.

    - U1, U2, U3 - deformation
    - Uabs - absolute deformation
    - Sabs - Von Mises stress
    - MaxPrin - Principal stress 1
    - MidPrin - Principal stress 2
    - MinPrin - Principal stress 3
    - MaxShear - maximum shear stress
    - Peeq - peeq strain
    - Temp - Temperature
    - MFlow - MassFlowRate
    - NPress - NetworkPressure

    for more information on result types and names
    see in code file src/Mod/Fem/App/FemVTKTools.cpp
    the methods _getFreeCADMechResultVectorProperties()
    and _getFreeCADMechResultScalarProperties()
    as well as forum topic
    https://forum.freecad.org/viewtopic.php?f=18&t=33106&start=30#p277434

    Parameters
    ----------
    resultobj : Fem::ResultMechanical
        FreeCAD FEM mechanical result object
    """

    m = res_obj.Stats
    stats_dict = {
        "U1": (m[0], m[1]),
        "U2": (m[2], m[3]),
        "U3": (m[4], m[5]),
        "Uabs": (m[6], m[7]),
        "Sabs": (m[8], m[9]),
        "MaxPrin": (m[10], m[11]),
        "MidPrin": (m[12], m[13]),
        "MinPrin": (m[14], m[15]),
        "MaxShear": (m[16], m[17]),
        "Peeq": (m[18], m[19]),
        "Temp": (m[20], m[21]),
        "MFlow": (m[22], m[23]),
        "NPress": (m[24], m[25]),
    }
    return stats_dict


def fill_femresult_stats(res_obj):
    """Fills a FreeCAD FEM mechanical result object with stats data.

    Parameters
    ----------
    resultobj : Fem::ResultMechanical
        FreeCAD FEM mechanical result object
    """

    FreeCAD.Console.PrintLog("Calculate stats list for result obj: " + res_obj.Name + "\n")
    # set stats values to 0, they may not exist in res_obj
    x_min = y_min = z_min = x_max = y_max = z_max = 0
    a_max = a_min = s_max = s_min = 0
    p1_min = p1_max = p2_min = p2_max = p3_min = p3_max = 0
    ms_min = ms_max = peeq_min = peeq_max = 0
    temp_min = temp_max = 0
    mflow_min = mflow_max = npress_min = npress_max = 0

    if res_obj.DisplacementVectors:
        x_max, y_max, z_max = map(max, zip(*res_obj.DisplacementVectors))
        x_min, y_min, z_min = map(min, zip(*res_obj.DisplacementVectors))
    if res_obj.DisplacementLengths:
        a_min = min(res_obj.DisplacementLengths)
        a_max = max(res_obj.DisplacementLengths)
    if res_obj.vonMises:
        s_min = min(res_obj.vonMises)
        s_max = max(res_obj.vonMises)
    if res_obj.PrincipalMax:
        p1_min = min(res_obj.PrincipalMax)
        p1_max = max(res_obj.PrincipalMax)
    if res_obj.PrincipalMed:
        p2_min = min(res_obj.PrincipalMed)
        p2_max = max(res_obj.PrincipalMed)
    if res_obj.PrincipalMin:
        p3_min = min(res_obj.PrincipalMin)
        p3_max = max(res_obj.PrincipalMin)
    if res_obj.MaxShear:
        ms_min = min(res_obj.MaxShear)
        ms_max = max(res_obj.MaxShear)
    if res_obj.Peeq:
        peeq_min = min(res_obj.Peeq)
        peeq_max = max(res_obj.Peeq)
    if res_obj.Temperature:
        temp_min = min(res_obj.Temperature)
        temp_max = max(res_obj.Temperature)
    if res_obj.MassFlowRate:
        # DisplacementVectors is empty, no_of_values needs to be set
        mflow_min = min(res_obj.MassFlowRate)
        mflow_max = max(res_obj.MassFlowRate)
    if res_obj.NetworkPressure:
        npress_min = min(res_obj.NetworkPressure)
        npress_max = max(res_obj.NetworkPressure)

    res_obj.Stats = [
        x_min,
        x_max,
        y_min,
        y_max,
        z_min,
        z_max,
        a_min,
        a_max,
        s_min,
        s_max,
        p1_min,
        p1_max,
        p2_min,
        p2_max,
        p3_min,
        p3_max,
        ms_min,
        ms_max,
        peeq_min,
        peeq_max,
        temp_min,
        temp_max,
        mflow_min,
        mflow_max,
        npress_min,
        npress_max,
    ]
    """
    stat_types = [
        "U1",
        "U2",
        "U3",
        "Uabs",
        "Sabs",
        "MaxPrin",
        "MidPrin",
        "MinPrin",
        "MaxShear",
        "Peeq",
        "Temp",
        "MFlow",
        "NPress"
    ]
    """
    # len(stat_types) == 13*3 == 39
    # do not forget to adapt initialization of all Stats items in modules:
    # - module femobjects/_FemResultMechanical.py
    # do not forget to adapt the def get_stats in:
    # - get_stats in module femresult/resulttools.py
    # - module femtest/testccxtools.py
    # TODO: all stats stuff should be reimplemented
    # maybe a dictionary would be far more robust than a list

    FreeCAD.Console.PrintLog("Stats list for result obj: " + res_obj.Name + " calculated\n")
    return res_obj


def add_disp_apps(res_obj):
    res_obj.DisplacementLengths = calculate_disp_abs(res_obj.DisplacementVectors)
    FreeCAD.Console.PrintLog("Added DisplacementLengths.\n")
    return res_obj


def add_von_mises(res_obj):
    mstress = []
    iterator = zip(
        res_obj.NodeStressXX,
        res_obj.NodeStressYY,
        res_obj.NodeStressZZ,
        res_obj.NodeStressXY,
        res_obj.NodeStressXZ,
        res_obj.NodeStressYZ,
    )
    for Sxx, Syy, Szz, Sxy, Sxz, Syz in iterator:
        mstress.append(calculate_von_mises((Sxx, Syy, Szz, Sxy, Sxz, Syz)))
    res_obj.vonMises = mstress
    FreeCAD.Console.PrintLog("Added von Mises stress.\n")
    return res_obj


def add_principal_stress_std(res_obj):
    # saved into PrincipalMax, PrincipalMed, PrincipalMin
    # TODO may be use only one container for principal stresses in result object
    # https://forum.freecad.org/viewtopic.php?f=18&t=33106&p=416006#p416006
    # but which one is better
    prinstress1 = []
    prinstress2 = []
    prinstress3 = []
    shearstress = []
    iterator = zip(
        res_obj.NodeStressXX,
        res_obj.NodeStressYY,
        res_obj.NodeStressZZ,
        res_obj.NodeStressXY,
        res_obj.NodeStressXZ,
        res_obj.NodeStressYZ,
    )
    for Sxx, Syy, Szz, Sxy, Sxz, Syz in iterator:
        prin1, prin2, prin3, shear = calculate_principal_stress_std((Sxx, Syy, Szz, Sxy, Sxz, Syz))
        prinstress1.append(prin1)
        prinstress2.append(prin2)
        prinstress3.append(prin3)
        shearstress.append(shear)
    res_obj.PrincipalMax = prinstress1
    res_obj.PrincipalMed = prinstress2
    res_obj.PrincipalMin = prinstress3
    res_obj.MaxShear = shearstress
    FreeCAD.Console.PrintLog("Added standard principal stresses and max shear values.\n")

    #
    # Add critical strain ratio using the Stress Modified Critical Strain (SMCS) criterion
    #   Forum Discussion: https://forum.freecad.org/viewtopic.php?f=18&t=35893#p303392
    #   Background: https://www.vtt.fi/inf/julkaisut/muut/2017/VTT-R-01177-17.pdf
    #
    #   critical strain ratio = peeq / critical_strain (>1.0 indicates ductile rupture)
    #       peeq = equivalent plastic strain
    #       critical strain = alpha * np.exp(-beta * T)
    #           alpha and beta are material parameters,
    #           where alpha can be related to unixial test data (user input) and
    #           beta is normally kept fixed at 1.5,
    #           unless available from extensive research experiments
    #           T = pressure / von Mises stress (stress triaxiality)
    #

    MatMechNon = FreeCAD.ActiveDocument.getObject("MaterialMechanicalNonlinear")
    if MatMechNon:
        stress_strain = MatMechNon.YieldPoints
        if stress_strain:
            i = -1
            while stress_strain[i] == "":
                i -= 1
            critical_uniaxial_strain = float(stress_strain[i].split(",")[1])
            # stress triaxiality T = 1/3 for uniaxial test
            alpha = np.sqrt(np.e) * critical_uniaxial_strain
            beta = 1.5
            if res_obj.Peeq:
                res_obj.CriticalStrainRatio = calculate_csr(
                    prinstress1, prinstress2, prinstress3, alpha, beta, res_obj
                )

    return res_obj


def calculate_csr(ps1, ps2, ps3, alpha, beta, res_obj):
    """Calculate critical strain ratio.

    Forum Discussion: https://forum.freecad.org/viewtopic.php?f=18&t=35893#p303392
    Background: https://www.vtt.fi/inf/julkaisut/muut/2017/VTT-R-01177-17.pdf

    critical strain ratio = peeq / critical_strain (>1.0 indicates ductile rupture)
        peeq = equivalent plastic strain
        critical strain = alpha * np.exp(-beta * T)
            alpha and beta are material parameters,
            where alpha can be related to unixial test data (user input) and
            beta is normally kept fixed at 1.5,
            unless available from extensive research experiments
            T = pressure / von Mises stress (stress triaxiality)
    """
    csr = []  # critical strain ratio
    nsr = len(ps1)  # number of stress results
    for i in range(nsr):
        p = (ps1[i] + ps2[i] + ps3[i]) / 3.0  # pressure
        svm = np.sqrt(
            1.5 * (ps1[i] - p) ** 2 + 1.5 * (ps2[i] - p) ** 2 + 1.5 * (ps3[i] - p) ** 2
        )  # von Mises stress: https://en.wikipedia.org/wiki/Von_Mises_yield_criterion
        if svm != 0.0:
            T = p / svm  # stress triaxiality
        else:
            T = 0.0
        critical_strain = alpha * np.exp(-beta * T)  # critical strain
        csr.append(abs(res_obj.Peeq[i]) / critical_strain)  # critical strain ratio
    return csr


def get_concrete_nodes(res_obj):
    """Determine concrete / non-concrete nodes."""
    from femmesh.meshtools import get_femnodes_by_refshape

    femmesh = res_obj.Mesh.FemMesh
    nsr = femmesh.NodeCount  # nsr number of stress results

    # ic[iic]:
    # ic = flag for material type; iic = node number
    # ic = 0: NOT ASSIGNED
    # ic = 1: ReinforcedMaterial
    # ic = 2: NOT ReinforcedMaterial
    ic = np.zeros(nsr)

    for obj in res_obj.getParentGroup().Group:
        if obj.isDerivedFrom("App::MaterialObjectPython") and is_of_type(
            obj, "Fem::MaterialReinforced"
        ):
            FreeCAD.Console.PrintMessage("ReinforcedMaterial\n")
            if obj.References == []:
                for iic in range(nsr):
                    if ic[iic] == 0:
                        ic[iic] = 1
            else:
                for ref in obj.References:
                    concrete_nodes = get_femnodes_by_refshape(femmesh, ref)
                    for cn in concrete_nodes:
                        ic[cn - 1] = 1
        elif obj.isDerivedFrom("App::MaterialObjectPython") and is_of_type(
            obj, "Fem::MaterialCommon"
        ):
            FreeCAD.Console.PrintMessage("No ReinforcedMaterial\n")
            if obj.References == []:
                for iic in range(nsr):
                    if ic[iic] == 0:
                        ic[iic] = 2
            else:
                for ref in obj.References:
                    non_concrete_nodes = get_femnodes_by_refshape(femmesh, ref)
                    for ncn in non_concrete_nodes:
                        ic[ncn - 1] = 2
    return ic


def add_principal_stress_reinforced(res_obj):
    #
    # determine concrete / non-concrete nodes
    #
    ic = get_concrete_nodes(res_obj)

    #
    # calculate principal and max Shear and fill them in res_obj
    #
    # saved into PS1Vector, PS2Vector, PS3Vector
    # TODO may be use only one container for principal stresses in result object
    # https://forum.freecad.org/viewtopic.php?f=18&t=33106&p=416006#p416006
    # but which one is better
    prinstress1 = []
    prinstress2 = []
    prinstress3 = []
    shearstress = []
    ps1v = []
    ps2v = []
    ps3v = []
    #
    # additional arrays to hold reinforcement ratios
    # and mohr coulomb stress
    #
    rhx = []
    rhy = []
    rhz = []
    moc = []

    # material parameter
    for obj in res_obj.getParentGroup().Group:
        if is_of_type(obj, "Fem::MaterialReinforced"):
            matrix_af = float(
                FreeCAD.Units.Quantity(obj.Material["AngleOfFriction"]).getValueAs("rad")
            )
            matrix_cs = float(
                FreeCAD.Units.Quantity(obj.Material["CompressiveStrength"]).getValueAs("MPa")
            )
            reinforce_yield = float(
                FreeCAD.Units.Quantity(obj.Reinforcement["YieldStrength"]).getValueAs("MPa")
            )
    # print(matrix_af)
    # print(matrix_cs)
    # print(reinforce_yield)

    iterator = zip(
        res_obj.NodeStressXX,
        res_obj.NodeStressYY,
        res_obj.NodeStressZZ,
        res_obj.NodeStressXY,
        res_obj.NodeStressXZ,
        res_obj.NodeStressYZ,
    )
    for isv, stress_tensor in enumerate(iterator):

        rhox = 0.0
        rhoy = 0.0
        rhoz = 0.0
        mc = 0.0

        if ic[isv] == 1:
            #
            # for concrete scxx etc. are affected by
            # reinforcement (see calculate_rho(stress_tensor)). for all other
            # materials scxx etc. are the original stresses
            #
            rhox, rhoy, rhoz = calculate_rho(stress_tensor, reinforce_yield)

        prin1, prin2, prin3, shear, psv = calculate_principal_stress_reinforced(stress_tensor)

        prinstress1.append(prin1)
        prinstress2.append(prin2)
        prinstress3.append(prin3)
        shearstress.append(shear)
        ps1v.append(psv[0])
        ps2v.append(psv[1])
        ps3v.append(psv[2])

        #
        # reinforcement ratios and mohr coulomb criterion
        #
        rhx.append(rhox)
        rhy.append(rhoy)
        rhz.append(rhoz)
        if ic[isv] == 1:
            mc = calculate_mohr_coulomb(prin1, prin3, matrix_af, matrix_cs)
        moc.append(mc)

    res_obj.PrincipalMax = prinstress1
    res_obj.PrincipalMed = prinstress2
    res_obj.PrincipalMin = prinstress3
    res_obj.MaxShear = shearstress
    #
    # additional concrete and principal stress plot
    # results for use in _ViewProviderFemResultMechanical
    #
    res_obj.ReinforcementRatio_x = rhx
    res_obj.ReinforcementRatio_y = rhy
    res_obj.ReinforcementRatio_z = rhz
    res_obj.MohrCoulomb = moc

    res_obj.PS1Vector = ps1v
    res_obj.PS2Vector = ps2v
    res_obj.PS3Vector = ps3v

    FreeCAD.Console.PrintLog(
        "Added reinforcement principal stresses and max shear values as well as "
        "reinforcment ratios, Mohr Coloumb values.\n"
    )
    return res_obj


def compact_result(res_obj):
    """
    compacts result.Mesh and appropriate result.NodeNumbers
    """
    # as workaround for https://www.freecad.org/tracker/view.php?id=2873

    # get compact mesh data
    from femmesh.meshtools import compact_mesh

    compact_femmesh_data = compact_mesh(res_obj.Mesh.FemMesh)
    compact_femmesh = compact_femmesh_data[0]
    node_map = compact_femmesh_data[1]
    # FreeCAD result obj does not support elem results ATM
    # elem_map = compact_femmesh_data[2]

    # set result mesh
    res_obj.Mesh.FemMesh = compact_femmesh

    # set result node numbers
    new_node_numbers = []
    for old_node_id in res_obj.NodeNumbers:
        new_node_numbers.append(node_map[old_node_id])
    res_obj.NodeNumbers = new_node_numbers

    return res_obj


def calculate_von_mises(stress_tensor):
    """Calculate Von mises stress.
    See http://en.wikipedia.org/wiki/Von_Mises_yield_criterion
    Simplification: https://forum.freecad.org/viewtopic.php?f=18&t=33974&p=296542#p296542

    stress_tensor ... (Sxx, Syy, Szz, Sxy, Sxz, Syz)
    """
    normal = stress_tensor[:3]
    shear = stress_tensor[3:]
    pressure = np.average(normal)
    von_mises = np.sqrt(
        1.5 * np.linalg.norm(normal - pressure) ** 2 + 3.0 * np.linalg.norm(shear) ** 2
    )
    return von_mises


def calculate_principal_stress_std(stress_tensor):
    # if NaN is inside the array, which can happen on Calculix frd result files return NaN
    # https://forum.freecad.org/viewtopic.php?f=22&t=33911&start=10#p284229
    # https://forum.freecad.org/viewtopic.php?f=18&t=32649#p274291
    for s in stress_tensor:
        if isnan(s) is True:
            return (float("NaN"), float("NaN"), float("NaN"), float("NaN"))

    s11 = stress_tensor[0]  # Sxx
    s22 = stress_tensor[1]  # Syy
    s33 = stress_tensor[2]  # Szz
    s12 = stress_tensor[3]  # Sxy
    s31 = stress_tensor[4]  # Sxz
    s23 = stress_tensor[5]  # Syz
    sigma = np.array(
        [[s11, s12, s31], [s12, s22, s23], [s31, s23, s33]]
    )  # https://forum.freecad.org/viewtopic.php?f=18&t=24637&start=10#p240408

    eigvals = list(np.linalg.eigvalsh(sigma))
    eigvals.sort()
    eigvals.reverse()
    maxshear = (eigvals[0] - eigvals[2]) / 2.0
    return (eigvals[0], eigvals[1], eigvals[2], maxshear)


def calculate_principal_stress_reinforced(stress_tensor):
    """Calculate principal stress vectors and values.

    For total stresses use:
    stress_tensor[0], stress_tensor[1], stress_tensor[2]
    on the diagonal of the stress tensor

    Difference with the original method:
    https://forum.freecad.org/viewtopic.php?f=18&t=33106&start=90#p296539
    """

    s11 = stress_tensor[0]  # Sxx
    s22 = stress_tensor[1]  # Syy
    s33 = stress_tensor[2]  # Szz
    s12 = stress_tensor[3]  # Sxy
    s31 = stress_tensor[4]  # Sxz
    s23 = stress_tensor[5]  # Syz
    sigma = np.array(
        [[s11, s12, s31], [s12, s22, s23], [s31, s23, s33]]
    )  # https://forum.freecad.org/viewtopic.php?f=18&t=24637&start=10#p240408

    eigenvalues, eigenvectors = np.linalg.eig(sigma)

    #
    #   suppress complex eigenvalue and vectors that may occur for
    #   near-zero (numerical noise) stress fields
    #

    eigenvalues = eigenvalues.real
    eigenvectors = eigenvectors.real

    eigenvectors[:, 0] = eigenvalues[0] * eigenvectors[:, 0]
    eigenvectors[:, 1] = eigenvalues[1] * eigenvectors[:, 1]
    eigenvectors[:, 2] = eigenvalues[2] * eigenvectors[:, 2]

    idx = eigenvalues.argsort()[::-1]
    eigenvalues = eigenvalues[idx]
    eigenvectors = eigenvectors[:, idx]

    maxshear = (eigenvalues[0] - eigenvalues[2]) / 2.0

    return (
        eigenvalues[0],
        eigenvalues[1],
        eigenvalues[2],
        maxshear,
        tuple([tuple(row) for row in eigenvectors.T]),
    )


def calculate_rho(stress_tensor, fy):
    """Calculation of Reinforcement Ratios and Concrete Stresses
    (in accordance with http://heronjournal.nl/53-4/3.pdf)

    Parameters
    ----------
    - fy: factored yield strength of reinforcement bars

    See post:
    https://forum.freecad.org/viewtopic.php?f=18&t=28821

    """

    rmin = 1.0e9
    eqmin = 14

    sxx = stress_tensor[0]
    syy = stress_tensor[1]
    szz = stress_tensor[2]
    sxy = stress_tensor[3]
    syz = stress_tensor[5]
    sxz = stress_tensor[4]

    rhox = np.zeros(15)
    rhoy = np.zeros(15)
    rhoz = np.zeros(15)

    # i1=sxx+syy+szz NOT USED
    # i2=sxx*syy+syy*szz+szz*sxx-sxy**2-sxz**2-syz**2 NOT USED
    i3 = sxx * syy * szz + 2 * sxy * sxz * syz - sxx * syz**2 - syy * sxz**2 - szz * sxy**2

    # Solution (5)
    d = sxx * syy - sxy**2
    if d != 0.0:
        rhoz[0] = i3 / d / fy

    # Solution (6)
    d = sxx * szz - sxz**2
    if d != 0.0:
        rhoy[1] = i3 / d / fy

    # Solution (7)
    d = syy * szz - syz**2
    if d != 0.0:
        rhox[2] = i3 / d / fy

    # Solution (9)
    if sxx != 0.0:
        fc = sxz * sxy / sxx - syz
        fxy = sxy**2 / sxx
        fxz = sxz**2 / sxx

        # Solution (9+)
        rhoy[3] = syy - fxy + fc
        rhoy[3] /= fy
        rhoz[3] = szz - fxz + fc
        rhoz[3] /= fy

        # Solution (9-)
        rhoy[4] = syy - fxy - fc
        rhoy[4] /= fy
        rhoz[4] = szz - fxz - fc
        rhoz[4] /= fy

    # Solution (10)
    if syy != 0.0:
        fc = syz * sxy / syy - sxz
        fxy = sxy**2 / syy
        fyz = syz**2 / syy

        # Solution (10+)
        rhox[5] = sxx - fxy + fc
        rhox[5] /= fy
        rhoz[5] = szz - fyz + fc
        rhoz[5] /= fy

        # Solution (10-)vm
        rhox[6] = sxx - fxy - fc

        rhox[6] /= fy
        rhoz[6] = szz - fyz - fc
        rhoz[6] /= fy

    # Solution (11)
    if szz != 0.0:
        fc = sxz * syz / szz - sxy
        fxz = sxz**2 / szz
        fyz = syz**2 / szz

        # Solution (11+)
        rhox[7] = sxx - fxz + fc
        rhox[7] /= fy
        rhoy[7] = syy - fyz + fc
        rhoy[7] /= fy

        # Solution (11-)
        rhox[8] = sxx - fxz - fc
        rhox[8] /= fy
        rhoy[8] = syy - fyz - fc
        rhoy[8] /= fy

    # Solution (13)
    rhox[9] = (sxx + sxy + sxz) / fy
    rhoy[9] = (syy + sxy + syz) / fy
    rhoz[9] = (szz + sxz + syz) / fy

    # Solution (14)
    rhox[10] = (sxx + sxy - sxz) / fy
    rhoy[10] = (syy + sxy - syz) / fy
    rhoz[10] = (szz - sxz - syz) / fy

    # Solution (15)
    rhox[11] = (sxx - sxy - sxz) / fy
    rhoy[11] = (syy - sxy + syz) / fy
    rhoz[11] = (szz - sxz + syz) / fy

    # Solution (16)
    rhox[12] = (sxx - sxy + sxz) / fy
    rhoy[12] = (syy - sxy - syz) / fy
    rhoz[12] = (szz + sxz - syz) / fy

    # Solution (17)
    if syz != 0.0:
        rhox[13] = (sxx - sxy * sxz / syz) / fy
    if sxz != 0.0:
        rhoy[13] = (syy - sxy * syz / sxz) / fy
    if sxy != 0.0:
        rhoz[13] = (szz - sxz * syz / sxy) / fy

    for ir in range(0, rhox.size):

        if rhox[ir] >= -1.0e-10 and rhoy[ir] >= -1.0e-10 and rhoz[ir] > -1.0e-10:

            # Concrete Stresses
            scxx = sxx - rhox[ir] * fy
            scyy = syy - rhoy[ir] * fy
            sczz = szz - rhoz[ir] * fy
            ic1 = scxx + scyy + sczz
            ic2 = scxx * scyy + scyy * sczz + sczz * scxx - sxy**2 - sxz**2 - syz**2
            ic3 = (
                scxx * scyy * sczz
                + 2 * sxy * sxz * syz
                - scxx * syz**2
                - scyy * sxz**2
                - sczz * sxy**2
            )

            if ic1 <= 1.0e-6 and ic2 >= -1.0e-6 and ic3 <= 1.0e-6:

                rsum = rhox[ir] + rhoy[ir] + rhoz[ir]

                if rsum < rmin and rsum > 0.0:
                    rmin = rsum
                    eqmin = ir

    return rhox[eqmin], rhoy[eqmin], rhoz[eqmin]


def calculate_mohr_coulomb(prin1, prin3, phi, fck):
    """Calculation of Mohr Coulomb yield criterion to judge
    concrete crushing and shear failure.

    Parameters
    ----------
    - phi: angle of internal friction
    - fck: factored compressive strength of the matrix material (usually concrete)
    """

    coh = fck * (1 - np.sin(phi)) / 2 / np.cos(phi)

    mc_stress = (prin1 - prin3) + (prin1 + prin3) * np.sin(phi) - 2.0 * coh * np.cos(phi)

    if mc_stress < 0.0:
        mc_stress = 0.0

    return mc_stress


def calculate_disp_abs(displacements):
    # see https://forum.freecad.org/viewtopic.php?f=18&t=33106&start=100#p296657
    return [np.linalg.norm(nd) for nd in displacements]


##  @}
