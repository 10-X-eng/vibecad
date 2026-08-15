# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for exact Native Assembly malformed-joint diagnosis."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

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
    create_fixed_joint,
    create_slider_joint,
    dispatcher,
    joint_group,
    process_events,
    select_assemble_ribbon,
)


_OPERATION = "select_malformed_constraints"


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
        "expected_malformed_count": diagnosis["malformed_count"],
        "offset": offset,
        "limit": limit,
    }


def _send_mouse(widget, event_type, position, button, buttons) -> None:
    event = QtGui.QMouseEvent(
        event_type,
        position,
        widget.mapToGlobal(position),
        button,
        buttons,
        QtCore.Qt.NoModifier,
    )
    QtGui.QApplication.sendEvent(widget, event)


def _viewport_point(view, viewport, world_point) -> QtCore.QPoint:
    screen_x, screen_y = view.getPointOnScreen(world_point)
    _width, height = view.getSize()
    scale = viewport.devicePixelRatioF()
    return QtCore.QPoint(
        int(round(screen_x / scale)),
        int(round((height - screen_y - 1) / scale)),
    )


def _wait_events(milliseconds: int) -> None:
    Gui.updateGui()
    application = QtGui.QApplication.instance()
    if application is not None:
        application.processEvents()
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def _trigger_fixed_bundle_drag(
    document,
    assembly,
    component,
    components,
) -> tuple[dict, dict]:
    """Start and cancel the human drag lifecycle that produces malformed joints."""

    starting_placements = tuple(App.Placement(item.Placement) for item in components)
    view = Gui.activeDocument().activeView()
    view.viewAxonometric()
    view.fitAll()
    _wait_events(100)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(assembly, f"{component.Name}.")
    _wait_events(80)
    assert assembly.ViewObject.DraggerVisibility is False

    viewport = view.graphicsView().viewport()
    assert viewport.isVisible()
    position = _viewport_point(
        view,
        viewport,
        component.Placement.multVec(App.Vector(5, 5, 5)),
    )
    bounds = viewport.rect().adjusted(8, 8, -8, -8)
    assert bounds.contains(position)
    target = position + QtCore.QPoint(48, 24)
    assert bounds.contains(target)
    _send_mouse(
        viewport,
        QtCore.QEvent.MouseButtonPress,
        position,
        QtCore.Qt.LeftButton,
        QtCore.Qt.LeftButton,
    )
    _wait_events(20)
    _send_mouse(
        viewport,
        QtCore.QEvent.MouseMove,
        target,
        QtCore.Qt.NoButton,
        QtCore.Qt.LeftButton,
    )
    _wait_events(80)
    assert int(document.getBookedTransactionID()) != 0
    during = assembly.getSolverDiagnostics()
    assert during["has_malformed_constraints"] is True

    # A selection clear is the supported cancellation path for direct moves.
    # It reaches ViewProviderAssembly::endMoveDragger(), which aborts only the
    # move transaction while preserving the last solver diagnosis.
    Gui.Selection.clearSelection()
    _wait_events(100)
    assert not document.HasPendingTransaction, (
        bool(document.HasPendingTransaction),
        int(document.getBookedTransactionID()),
        int(document.UndoCount),
    )
    assert int(document.getBookedTransactionID()) == 0
    assert all(
        item.Placement.isSame(starting, 1.0e-9)
        for item, starting in zip(components, starting_placements)
    )
    assert Gui.activeDocument().getInEdit() is assembly.ViewObject
    after = assembly.getSolverDiagnostics()
    assert after["malformed_joints"] == during["malformed_joints"]
    return during, after


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
            prefix="vibecad-native-assembly-malformed-diagnosis-"
        )
        path = Path(temporary.name) / "native-assembly-malformed.FCStd"
        document = App.newDocument("NativeAssemblyMalformedGate")
        document.UndoMode = 1
        sources = []
        for index in range(3):
            source = document.addObject("Part::Box", f"MalformedSource{index}")
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
        document.openTransaction("Prepare malformed Assembly")
        components = []
        for index, source in enumerate(sources):
            component = assembly.newObject("App::Link", f"MalformedComponent{index}")
            component.LinkedObject = source
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
                "GroundToRigidBundle",
            ),
            create_fixed_joint(
                group,
                components[1],
                components[2],
                "RigidBundle",
            ),
            create_slider_joint(
                group,
                components[1],
                components[2],
                "SliderInsideRigidBundle",
            ),
        )
        document.recompute()
        document.commitTransaction()
        process_events(16)
        normal_code = int(assembly.solve(False))
        document.recompute()
        normal = assembly.getSolverDiagnostics()
        assert normal_code == 0
        assert normal["solver_status"] == 0
        assert normal["has_malformed_constraints"] is False

        during, diagnostics = _trigger_fixed_bundle_drag(
            document,
            assembly,
            components[1],
            components,
        )
        expected_malformed = [joints[1].Name, joints[2].Name]
        assert during["solver_status"] == 0
        assert diagnostics["solver_status"] == 0
        assert diagnostics["malformed_joints"] == expected_malformed
        assert diagnostics["has_conflicts"] is False
        assert diagnostics["has_redundancies"] is False
        assert diagnostics["has_partial_redundancies"] is False
        assert [item["joint"] for item in diagnostics["joints"]] == [joints[0].Name]
        document.clearUndos()

        Gui.Selection.clearSelection()
        Gui.runCommand("Assembly_SelectMalformedConstraints")
        process_events(12)
        human_selected = [obj.Name for obj in Gui.Selection.getSelection()]
        assert human_selected == expected_malformed
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(components[0])
        process_events(8)

        controller, surface = active_assemble_surface()
        assert "Assembly_SelectMalformedConstraints" in surface.command_ids
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
        assert variant.action_ids == frozenset({"Assembly_SelectMalformedConstraints"})

        native = dispatcher(
            document,
            surface,
            registry,
            controller,
            "native-assembly-malformed-diagnosis-gui",
            (_OPERATION,),
        )
        current = native.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-malformed-state",
        )
        assert current["ok"] is True, current
        summary = assembly_summary(current, assembly.Name)
        diagnosis = summary["diagnosis_state"]
        assert diagnosis["available"] is True
        assert diagnosis["solver_status"] == 0
        assert diagnosis["conflicting_count"] == 0
        assert diagnosis["redundant_count"] == 0
        assert diagnosis["partially_redundant_count"] == 0
        assert diagnosis["malformed_count"] == 2
        assert diagnosis["joint_count"] == 3

        before_objects = tuple(document.Objects)
        before_placements = tuple(component.Placement for component in components)
        before_selection = tuple(Gui.Selection.getSelection())
        stale_arguments = _arguments(summary)
        stale_arguments["expected_diagnosis_state_sha256"] = "0" * 64
        stale = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(stale_arguments, separators=(",", ":")),
            "assembly-malformed-stale",
        )
        assert stale["ok"] is False, stale
        assert stale["error_code"] == "NATIVE_ASSEMBLY_DIAGNOSIS_FAILED"
        wrong_count_arguments = _arguments(summary)
        wrong_count_arguments["expected_malformed_count"] = 1
        wrong_count = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(wrong_count_arguments, separators=(",", ":")),
            "assembly-malformed-wrong-count",
        )
        assert wrong_count["ok"] is False, wrong_count

        first_encoded = json.dumps(
            _arguments(summary, limit=1),
            separators=(",", ":"),
        )
        first = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            first_encoded,
            "assembly-malformed-first",
        )
        second = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(_arguments(summary, offset=1, limit=1), separators=(",", ":")),
            "assembly-malformed-second",
        )
        assert first["ok"] is True, first
        assert second["ok"] is True, second
        assert first["solver_scope"] == "most_recent_fixed_bundle_drag"
        assert first["solver_status"] == 0
        assert first["malformed_joint_count"] == 2
        assert first["returned_count"] == 1
        assert first["next_offset"] == 1
        assert "next_offset" not in second
        assert [
            first["malformed_joints"][0]["joint"]["object_name"],
            second["malformed_joints"][0]["joint"]["object_name"],
        ] == human_selected
        fixed = first["malformed_joints"][0]
        intra_bundle = second["malformed_joints"][0]
        assert fixed["joint_type"] == "Fixed"
        assert fixed["bundle_role"] == "fixed_bundle_constraint"
        assert intra_bundle["joint_type"] == "Slider"
        assert intra_bundle["bundle_role"] == "intra_bundle_constraint"
        assert fixed["reason_code"] == "same_solver_part_in_fixed_drag_bundle"
        assert intra_bundle["reason_code"] == fixed["reason_code"]
        assert fixed["first"]["component"]["object_name"] == components[1].Name
        assert fixed["second"]["component"]["object_name"] == components[2].Name
        replay = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            first_encoded,
            "assembly-malformed-first",
        )
        assert replay == first
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
        joints = tuple(document.getObject(name) for name in joint_names)
        assert assembly is not None
        assert all(component is not None for component in components)
        assert all(joint is not None for joint in joints)
        assert Gui.activeDocument().setEdit(assembly.Name)
        process_events(20)
        assert int(assembly.solve(False)) == 0
        document.recompute()
        assert assembly.getSolverDiagnostics()["malformed_joints"] == []
        _during, reopened_diagnostics = _trigger_fixed_bundle_drag(
            document,
            assembly,
            components[1],
            components,
        )
        assert reopened_diagnostics["malformed_joints"] == expected_malformed
        document.clearUndos()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(components[2])
        process_events(8)

        main_window = Gui.getMainWindow()
        select_assemble_ribbon(main_window)
        reopened_surface = read_active_ribbon_surface(controller)
        reopened_native = dispatcher(
            document,
            reopened_surface,
            registry,
            controller,
            "native-assembly-malformed-diagnosis-reopen",
            (_OPERATION,),
        )
        reopened_state = reopened_native.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-malformed-reopened-state",
        )
        assert reopened_state["ok"] is True, reopened_state
        reopened_summary = assembly_summary(reopened_state, assembly.Name)
        reopened_result = reopened_native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(_arguments(reopened_summary), separators=(",", ":")),
            "assembly-malformed-reopened-read",
        )
        assert reopened_result["ok"] is True, reopened_result
        assert [
            item["joint"]["object_name"] for item in reopened_result["malformed_joints"]
        ] == expected_malformed
        assert Gui.Selection.getSelection() == [components[2]]
        assert int(document.UndoCount) == 0

        print(
            "VIBECAD_NATIVE_ASSEMBLY_MALFORMED_DIAGNOSIS_GUI_OK "
            "components=3 joints=3 malformed=2 fixed_member=1 intra_bundle=1 "
            "solver_status=0 human_match=true pagination=true stale_noop=true "
            "selection=true transactions=0 reopen=true",
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
