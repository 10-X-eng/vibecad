# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for exact Native Assembly partial-redundancy diagnosis."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import CommandCreateJoint
import JointObject
import Preferences
import UtilsAssembly
from VibeCADNativeAssemblyDiagnosisBindings import (
    ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADRibbonSurface import read_active_ribbon_surface
from native_assembly_diagnosis_gui_support import (
    active_assemble_surface,
    assembly_summary,
    create_cylindrical_joint,
    create_slider_joint,
    dispatcher,
    joint_group,
    process_events,
    select_assemble_ribbon,
)


_OPERATION = "select_partially_redundant_constraints"


def _arguments(summary: dict, *, offset: int = 0, limit: int = 32) -> dict:
    diagnosis = summary["diagnosis_state"]
    assert diagnosis["available"] is True
    return {
        "operation": _OPERATION,
        "assembly": {"object_name": summary["object_name"]},
        "expected_diagnosis_state_sha256": diagnosis["state_sha256"],
        "expected_component_count": diagnosis["component_count"],
        "expected_grounded_count": diagnosis["grounded_count"],
        "expected_joint_count": diagnosis["joint_count"],
        "expected_partially_redundant_count": diagnosis[
            "partially_redundant_count"
        ],
        "offset": offset,
        "limit": limit,
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    preferences = Preferences.preferences()
    prior_solve_creation = preferences.GetBool("SolveInJointCreation", True)
    prior_solve_recompute = preferences.GetBool("SolveOnRecompute", True)
    exit_code = 1
    try:
        preferences.SetBool("SolveInJointCreation", False)
        preferences.SetBool("SolveOnRecompute", False)
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-assembly-partial-redundancy-diagnosis-"
        )
        path = Path(temporary.name) / "native-assembly-partial-redundancy.FCStd"
        document = App.newDocument("NativeAssemblyPartialRedundancyGate")
        document.UndoMode = 1
        sources = []
        for index in range(2):
            source = document.addObject("Part::Box", f"PartialSource{index}")
            source.Length = 10.0
            source.Width = 10.0
            source.Height = 10.0
            sources.append(source)
        document.recompute()
        document.saveAs(str(path))

        Gui.runCommand("Assembly_CreateAssembly")
        process_events(24)
        assembly = next(
            obj for obj in document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        )
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject
        document.openTransaction("Prepare partially redundant Assembly")
        components = []
        for index, source in enumerate(sources):
            component = assembly.newObject("App::Link", f"PartialComponent{index}")
            component.LinkedObject = source
            component.Placement.Base = App.Vector(float(index * 10), 0, 0)
            UtilsAssembly.finalizeInsertedComponentTimeline(component)
            components.append(component)
        ground = CommandCreateJoint.createGroundedJointFeature(components[0], assembly)
        JointObject.ensureViewProviderGroundedJoint(ground)
        group = joint_group(assembly)
        joints = (
            create_cylindrical_joint(
                group,
                components[0],
                components[1],
                "CylindricalFirst",
            ),
            create_slider_joint(
                group,
                components[0],
                components[1],
                "SliderSecond",
            ),
        )
        document.recompute()
        document.commitTransaction()
        process_events(16)
        solver_code = int(assembly.solve(False))
        document.recompute()
        diagnostics = assembly.getSolverDiagnostics()
        expected_partial = list(diagnostics["partially_redundant_joints"])
        expected_redundant = list(diagnostics["redundant_joints"])
        assert solver_code == 0
        assert diagnostics["solver_status"] == 0
        assert diagnostics["has_partial_redundancies"] is True
        assert diagnostics["has_redundancies"] is True
        assert diagnostics["has_conflicts"] is False
        assert expected_partial == [joints[1].Name]
        assert expected_redundant == expected_partial
        by_name = {item["joint"]: item for item in diagnostics["joints"]}
        cylindrical = by_name[joints[0].Name]
        assert cylindrical["status"] == "satisfied"
        assert cylindrical["constraint_count"] == 4
        assert cylindrical["redundant_constraint_count"] == 0
        assert cylindrical["removed_degrees_of_freedom"] == 4
        slider = by_name[joints[1].Name]
        assert slider["status"] == "redundant"
        assert slider["constraint_count"] == 5
        assert slider["redundant_constraint_count"] == 4
        assert slider["removed_degrees_of_freedom"] == 1
        assert sum(item["redundant"] for item in slider["constraints"]) == 4
        document.clearUndos()

        Gui.Selection.clearSelection()
        Gui.runCommand("Assembly_SelectPartiallyRedundantConstraints")
        process_events(12)
        human_selected = [obj.Name for obj in Gui.Selection.getSelection()]
        assert human_selected == expected_partial
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(components[0])
        process_events(8)

        controller, surface = active_assemble_surface()
        assert "Assembly_SelectPartiallyRedundantConstraints" in surface.command_ids
        registry = build_native_capability_registry()
        production = resolve_native_provider_surface(surface, registry)
        assert (
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME
            not in production.missing_definition_names
        )
        assert (
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME
            not in production.missing_implementation_names
        )
        assert (
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME not in production.incomplete_definition_names
        )
        definition = registry.definition(ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME)
        assert definition is not None
        variant = next(
            item for item in definition.variants if item.operation == _OPERATION
        )
        assert variant.action_ids == frozenset(
            {"Assembly_SelectPartiallyRedundantConstraints"}
        )

        native = dispatcher(
            document,
            surface,
            registry,
            controller,
            "native-assembly-partial-redundancy-diagnosis-gui",
            (_OPERATION,),
        )
        current = native.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-partial-redundancy-state",
        )
        assert current["ok"] is True, current
        summary = assembly_summary(current, assembly.Name)
        diagnosis = summary["diagnosis_state"]
        assert diagnosis["available"] is True
        assert diagnosis["solver_status"] == 0
        assert diagnosis["conflicting_count"] == 0
        assert diagnosis["redundant_count"] == 1
        assert diagnosis["partially_redundant_count"] == 1
        assert diagnosis["joint_count"] == 2

        before_objects = tuple(document.Objects)
        before_placements = tuple(component.Placement for component in components)
        before_selection = tuple(Gui.Selection.getSelection())
        stale_arguments = _arguments(summary)
        stale_arguments["expected_diagnosis_state_sha256"] = "0" * 64
        stale = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(stale_arguments, separators=(",", ":")),
            "assembly-partial-redundancy-stale",
        )
        assert stale["ok"] is False, stale
        assert stale["error_code"] == "NATIVE_ASSEMBLY_DIAGNOSIS_FAILED"
        wrong_count_arguments = _arguments(summary)
        wrong_count_arguments["expected_partially_redundant_count"] = 0
        wrong_count = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(wrong_count_arguments, separators=(",", ":")),
            "assembly-partial-redundancy-wrong-count",
        )
        assert wrong_count["ok"] is False, wrong_count

        encoded = json.dumps(_arguments(summary, limit=1), separators=(",", ":"))
        result = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            encoded,
            "assembly-partial-redundancy-read",
        )
        assert result["ok"] is True, result
        assert result["solver_status"] == 0
        assert result["remaining_degrees_of_freedom"] == 1
        assert result["partially_redundant_joint_count"] == 1
        assert result["returned_count"] == 1
        assert [
            item["joint"]["object_name"]
            for item in result["partially_redundant_joints"]
        ] == human_selected
        item = result["partially_redundant_joints"][0]
        assert item["diagnostic_status"] == "redundant"
        assert item["also_in_redundant_set"] is True
        assert item["constraint_count"] == 5
        assert item["redundant_constraint_count"] == 4
        assert item["removed_degrees_of_freedom"] == 1
        assert item["first"]["component"]["object_name"] == components[0].Name
        assert item["second"]["component"]["object_name"] == components[1].Name
        replay = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            encoded,
            "assembly-partial-redundancy-read",
        )
        assert replay == result
        assert tuple(document.Objects) == before_objects
        assert all(
            component.Placement.isSame(before, 1.0e-9)
            for component, before in zip(components, before_placements)
        )
        assert tuple(Gui.Selection.getSelection()) == before_selection
        assert int(document.UndoCount) == 0
        assert not document.HasPendingTransaction
        assert int(document.getBookedTransactionID()) == 0
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        assembly_name = assembly.Name
        component_names = tuple(component.Name for component in components)
        joint_names = tuple(joint.Name for joint in joints)
        Gui.Selection.clearSelection()
        Gui.activeDocument().resetEdit()
        process_events(16)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        process_events(24)
        assembly = document.getObject(assembly_name)
        components = tuple(document.getObject(name) for name in component_names)
        assert all(component is not None for component in components)
        assert all(document.getObject(name) is not None for name in joint_names)
        assert Gui.activeDocument().setEdit(assembly.Name)
        process_events(20)
        reopened_code = int(assembly.solve(False))
        document.recompute()
        assert reopened_code == 0
        reopened_diagnostics = assembly.getSolverDiagnostics()
        assert reopened_diagnostics["partially_redundant_joints"] == expected_partial
        assert reopened_diagnostics["redundant_joints"] == expected_redundant
        document.clearUndos()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(components[1])
        process_events(8)

        main_window = Gui.getMainWindow()
        select_assemble_ribbon(main_window)
        reopened_surface = read_active_ribbon_surface(controller)
        reopened_native = dispatcher(
            document,
            reopened_surface,
            registry,
            controller,
            "native-assembly-partial-redundancy-diagnosis-reopen",
            (_OPERATION,),
        )
        reopened_state = reopened_native.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-partial-redundancy-reopened-state",
        )
        assert reopened_state["ok"] is True, reopened_state
        reopened_summary = assembly_summary(reopened_state, assembly.Name)
        reopened_result = reopened_native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(_arguments(reopened_summary), separators=(",", ":")),
            "assembly-partial-redundancy-reopened-read",
        )
        assert reopened_result["ok"] is True, reopened_result
        assert [
            item["joint"]["object_name"]
            for item in reopened_result["partially_redundant_joints"]
        ] == expected_partial
        assert Gui.Selection.getSelection() == [components[1]]
        assert int(document.UndoCount) == 0

        print(
            "VIBECAD_NATIVE_ASSEMBLY_PARTIAL_REDUNDANCY_DIAGNOSIS_GUI_OK "
            "components=2 joints=2 partial=1 redundant_overlap=1 solver_status=0 "
            "human_match=true aggregate=4_of_5 stale_noop=true selection=true "
            "transactions=0 reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        preferences.SetBool("SolveInJointCreation", prior_solve_creation)
        preferences.SetBool("SolveOnRecompute", prior_solve_recompute)
        if document is not None:
            try:
                Gui.activeDocument().resetEdit()
            except (AttributeError, RuntimeError):
                pass
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
