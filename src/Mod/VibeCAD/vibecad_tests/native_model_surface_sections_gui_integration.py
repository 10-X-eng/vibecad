# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real VibeCAD GUI and provider lifecycle gate for Surface Sections."""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign
import PartGui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelErrors import NativeModelError
import VibeCADNativeModelSurfaceRuntime as runtime_module
from VibeCADNativeModelSurfaceSchema import model_surface_capability_definition
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeSurfaceSections import (
    create_surface_sections,
    preflight_surface_sections,
    prepare_surface_sections,
)
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _close(left: float, right: float, tolerance: float = 1.0e-7) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1.0e-9, abs_tol=tolerance)


def _shape_signature(shape) -> tuple[object, ...]:
    bounds = shape.BoundBox
    return (
        str(shape.ShapeType),
        len(shape.Vertexes),
        len(shape.Edges),
        len(shape.Wires),
        len(shape.Faces),
        float(shape.Length),
        float(shape.Area),
        float(bounds.XMin),
        float(bounds.XMax),
        float(bounds.YMin),
        float(bounds.YMax),
        float(bounds.ZMin),
        float(bounds.ZMax),
    )


def _assert_signature(actual, expected) -> None:
    assert actual[:5] == expected[:5]
    assert all(
        _close(left, right)
        for left, right in zip(actual[5:], expected[5:], strict=True)
    )


def _section_stack(x: float, count: int):
    return Part.makeCompound(
        [
            Part.makeCircle(
                4.0 + (index % 3),
                App.Vector(x, 0, 12.0 * index),
            )
            for index in range(count)
        ]
    )


def _publish_object(document, obj):
    PartDesign.initializeDesignDefinition(obj)
    document.publishProvisionalTimelineOperationBlock(obj, (), ())
    assert document.recompute([obj], True, True) is not False
    PartDesign.finalizeDesignDefinition(obj)
    assert PartGui.isModelingObjectActive(obj)
    return obj


def _publish_source(document, name: str, shape, *, placement=None, visible=True):
    source = document.addObject("Part::Feature", name)
    source.Label = name
    source.Shape = shape
    if placement is not None:
        source.Placement = placement
    source.Visibility = visible
    return _publish_object(document, source)


def _body_source(document, name: str, shape):
    body = document.addObject("PartDesign::Body", name)
    seed = body.newObject("PartDesign::Feature", f"{name}Seed")
    seed.Label = f"{name} Seed"
    seed.Shape = shape
    assert document.recompute([seed, body], True, True) is not False
    body.Visibility = True
    assert PartGui.isModelingObjectActive(body)
    return body, seed


def _create_sources(document):
    document.openTransaction("Create Surface Sections gate sources")
    try:
        sources = {
            "HumanSections": _publish_source(
                document, "HumanSections", _section_stack(0, 3)
            ),
            "TwoSections": _publish_source(
                document, "TwoSections", _section_stack(30, 2)
            ),
            "FourSections": _publish_source(
                document, "FourSections", _section_stack(60, 4)
            ),
            "PlacedSections": _publish_source(
                document,
                "PlacedSections",
                _section_stack(0, 3),
                placement=App.Placement(
                    App.Vector(90, -4, 3),
                    App.Rotation(App.Vector(0, 0, 1), 21),
                ),
            ),
            "HiddenSections": _publish_source(
                document, "HiddenSections", _section_stack(120, 3), visible=False
            ),
            "InactiveSections": _publish_source(
                document, "InactiveSections", _section_stack(150, 3)
            ),
            "RollbackSections": _publish_source(
                document, "RollbackSections", _section_stack(180, 3)
            ),
            "VertexOnly": _publish_source(
                document, "VertexOnly", Part.Vertex(App.Vector(210, 0, 0))
            ),
        }
        stale = _publish_source(document, "StaleSections", _section_stack(220, 3))
        body, seed = _body_source(
            document,
            "BodySections",
            _section_stack(240, 3),
        )
        sources["BodySections"] = body
        sources["BodySectionsSeed"] = seed
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise

    document.openTransaction("Delete stale Surface Sections source")
    try:
        stale_name = stale.Name
        document.removeObject(stale_name)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert document.getObject(stale_name) is None
    return sources, stale_name


def _turn() -> NativeTurnSnapshot:
    definition = model_surface_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "s" * 64,
            ("Surface_Sections",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("sections",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _section(source, edge: str):
    return {
        "object_name": source.Name if hasattr(source, "Name") else str(source),
        "edge": edge,
    }


def _sections(source, count: int, *, reverse=False):
    indices = range(count, 0, -1) if reverse else range(1, count + 1)
    return tuple(_section(source, f"Edge{index}") for index in indices)


def _arguments(label: str, sections):
    return {
        "operation": "sections",
        "label": label,
        "definition": {"sections": list(sections)},
    }


def _link_sub(value):
    if not value:
        return None, ()
    target, names = value if isinstance(value, tuple) else (value, ())
    if isinstance(names, str):
        names = (names,) if names else ()
    return target, tuple(str(name) for name in names)


def _flatten_links(value):
    flattened = []
    for group in tuple(value):
        target, names = _link_sub(group)
        for name in names or ("",):
            flattened.append((target, (name,)))
    return tuple(flattened)


def _task_button(role):
    for box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        button = box.button(role)
        if button is not None and button.isVisible():
            return button
    return None


def _finish_task(role) -> None:
    button = _task_button(role)
    assert button is not None and button.isEnabled()
    button.click()
    _process_events(40)
    assert not Gui.Control.activeDialog()


def _assert_human_contract(document, source) -> None:
    before = tuple(obj.Name for obj in document.Objects)
    Gui.Selection.clearSelection()
    assert Gui.isCommandActive("Surface_Sections")
    Gui.runCommand("Surface_Sections", 0)
    _process_events(32)
    assert Gui.Control.activeDialog()
    window = Gui.getMainWindow()
    section_list = window.findChild(QtWidgets.QListWidget, "listSections")
    add_edge = window.findChild(QtWidgets.QToolButton, "buttonEdgeAdd")
    remove_edge = window.findChild(QtWidgets.QToolButton, "buttonEdgeRemove")
    assert section_list is not None and add_edge is not None and remove_edge is not None
    assert add_edge.text() == "Add Edge" and remove_edge.text() == "Remove Edge"
    assert add_edge.isChecked()
    for index in range(1, 4):
        Gui.Selection.addSelection(source, f"Edge{index}")
        _process_events(10)
    assert section_list.count() == 3

    remove_edge.click()
    _process_events()
    assert remove_edge.isChecked() and not add_edge.isChecked()
    Gui.Selection.addSelection(source, "Edge2")
    _process_events(12)
    assert section_list.count() == 2
    add_edge.click()
    _process_events()
    Gui.Selection.addSelection(source, "Edge2")
    _process_events(12)
    assert section_list.count() == 3
    model = section_list.model()
    assert model.moveRow(QtCore.QModelIndex(), 2, QtCore.QModelIndex(), 1)
    _process_events(16)

    created = [obj for obj in document.Objects if obj.Name not in before]
    assert len(created) == 1 and created[0].TypeId == "Surface::Sections"
    human_result = created[0]
    assert _flatten_links(human_result.NSections) == tuple(
        (source, (f"Edge{index}",)) for index in range(1, 4)
    )
    _finish_task(QtWidgets.QDialogButtonBox.Ok)
    assert human_result.isValid() and human_result.Shape.ShapeType == "Face"
    assert source.Visibility
    human_name = human_result.Name
    document.undo()
    _process_events()
    assert document.getObject(human_name) is None and source.Visibility

    before = tuple(obj.Name for obj in document.Objects)
    Gui.runCommand("Surface_Sections", 0)
    _process_events(24)
    assert Gui.Control.activeDialog()
    _finish_task(QtWidgets.QDialogButtonBox.Cancel)
    assert tuple(obj.Name for obj in document.Objects) == before


def _target(source):
    return PartGui.resolveModelingObject(source)


def _assert_result(document, response, arguments, sources):
    assert set(response) == {
        "ok",
        "root",
        "section_count",
        "edge_count",
        "area_mm2",
        "receipt",
        "assistant_undo_available",
    }
    sections = tuple(arguments["definition"]["sections"])
    result = document.getObject(response["root"]["object_name"])
    assert result is not None and result.TypeId == "Surface::Sections"
    assert result.Label == arguments["label"]
    assert result.getParentGeoFeatureGroup() is None
    assert result.isValid() and result.Shape.isValid()
    assert result.Shape.ShapeType == "Face" and len(result.Shape.Faces) == 1
    assert result.VibeCADTimelineRole == "operation"
    assert getattr(result, "VibeCADTimelineOwner", None) is None
    assert str(result.VibeCADDefinitionId) and str(result.DesignId)
    expected_links = tuple(
        (
            _target(sources[section["object_name"]]),
            (section["edge"],),
        )
        for section in sections
    )
    assert _flatten_links(result.NSections) == expected_links
    assert response["section_count"] == len(sections)
    assert response["edge_count"] == len(result.Shape.Edges)
    assert _close(response["area_mm2"], result.Shape.Area)
    assert response["assistant_undo_available"] is True
    assert [item["object_name"] for item in response["receipt"]["created"]] == [
        result.Name
    ]
    assert response["receipt"]["changed"] == []
    assert response["receipt"]["deleted"] == []
    assert response["receipt"]["replaced"] == []
    PartDesign.validateDesign(result)
    return result


def _record(result, source_visibility):
    return {
        "name": result.Name,
        "label": str(result.Label),
        "definition_id": str(result.VibeCADDefinitionId),
        "design_id": str(result.DesignId),
        "sections": tuple(
            (target.Name, names) for target, names in _flatten_links(result.NSections)
        ),
        "source_visibility": source_visibility,
        "signature": _shape_signature(result.Shape),
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("SurfaceWorkbench")
        document = App.newDocument("NativeModelSurfaceSectionsGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_human_contract(document, sources["HumanSections"])

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-surface-sections-gui")
        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: "model",
            edit_or_task_active=lambda: False,
        )
        turn = _turn()
        debug_events = []
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
            debug_sink=debug_events.append,
        )
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                "model.surface",
                json.dumps(arguments, separators=(",", ":")),
                f"model-surface-sections-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments,
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        cases = (
            _arguments("Two Section Surface", _sections(sources["TwoSections"], 2)),
            _arguments(
                "Reverse Ordered Four Sections",
                _sections(sources["FourSections"], 4, reverse=True),
            ),
            _arguments(
                "Placed Section Surface",
                _sections(sources["PlacedSections"], 3),
            ),
            _arguments(
                "Body Section Surface",
                _sections(sources["BodySections"], 3),
            ),
            _arguments(
                "Hidden Input Section Surface",
                _sections(sources["HiddenSections"], 3),
            ),
        )
        records = []
        for arguments in cases:
            source_names = tuple(
                dict.fromkeys(
                    section["object_name"]
                    for section in arguments["definition"]["sections"]
                )
            )
            source_visibility = {
                name: bool(sources[name].Visibility) for name in source_names
            }
            source_signatures = {
                name: _shape_signature(Part.getShape(sources[name], transform=True))
                for name in source_names
            }
            response = native_call(arguments)
            result = _assert_result(document, response, arguments, sources)
            signature = _shape_signature(result.Shape)
            assert signature[6] > 0.0
            for _index in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_signature(_shape_signature(result.Shape), signature)
            for name, expected in source_signatures.items():
                _assert_signature(
                    _shape_signature(Part.getShape(sources[name], transform=True)),
                    expected,
                )
            assert {
                name: bool(sources[name].Visibility) for name in source_names
            } == source_visibility
            record = _record(result, source_visibility)
            document.undo()
            _process_events()
            assert document.getObject(record["name"]) is None
            assert {
                name: bool(sources[name].Visibility) for name in source_names
            } == source_visibility
            document.redo()
            _process_events()
            result = document.getObject(record["name"])
            assert result is not None
            _assert_signature(_shape_signature(result.Shape), signature)
            PartDesign.validateDesign(result)
            records.append(record)

        body_state = _target(sources["BodySections"])
        assert body_state is sources["BodySectionsSeed"]
        assert all(name == body_state.Name for name, _names in records[-2]["sections"])
        placed_signature = records[-3]["signature"]
        assert placed_signature[7] > 80.0 and placed_signature[11] >= 3.0 - 1.0e-7

        failure_cases = (
            (
                _arguments(
                    "Too Few",
                    (_section(sources["RollbackSections"], "Edge1"),),
                ),
                "NATIVE_ARGUMENTS_INVALID",
            ),
            (
                _arguments(
                    "Missing",
                    (_section(stale_name, "Edge1"), _section(stale_name, "Edge2")),
                ),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments(
                    "No Edge",
                    (
                        _section(sources["VertexOnly"], "Edge1"),
                        _section(sources["RollbackSections"], "Edge1"),
                    ),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Duplicate Resolved",
                    (
                        _section(sources["BodySections"], "Edge1"),
                        _section(sources["BodySectionsSeed"], "Edge1"),
                    ),
                ),
                "NATIVE_MODEL_INVALID",
            ),
        )
        for arguments, error_code in failure_cases:
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(arguments, succeeds=False)
            assert response["error_code"] == error_code
            assert tuple(obj.Name for obj in document.Objects) == before
            assert not document.HasPendingTransaction

        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        timeline.Position = 0
        _process_events()
        assert not PartGui.isModelingObjectActive(sources["InactiveSections"])
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(
            _arguments(
                "Inactive",
                _sections(sources["InactiveSections"], 3),
            ),
            succeeds=False,
        )
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()

        stale_source = sources["RollbackSections"]
        stale_arguments = _arguments("Stale", _sections(stale_source, 3))
        stale_spec = prepare_surface_sections(
            str(document.Uid), stale_arguments["definition"]
        )
        stale_prepared = preflight_surface_sections(document, stale_spec)
        names_before = tuple(obj.Name for obj in document.Objects)
        document.openTransaction("Reject stale Surface Sections")
        try:
            stale_source.Shape = _section_stack(181, 3)
            try:
                create_surface_sections(
                    document,
                    label="Stale",
                    prepared=stale_prepared,
                )
            except NativeModelError:
                pass
            else:
                raise AssertionError("Changed Surface Sections preflight was accepted")
        finally:
            document.abortTransaction()
        assert tuple(obj.Name for obj in document.Objects) == names_before

        rollback_arguments = _arguments(
            "Rollback Surface Sections",
            _sections(sources["RollbackSections"], 3),
        )
        rollback_names = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_surface_sections

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced Surface Sections postcondition failure.")

        runtime_module.verify_surface_sections = reject_after_creation
        try:
            rollback = native_call(rollback_arguments, succeeds=False)
        finally:
            runtime_module.verify_surface_sections = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == rollback_names
        assert not document.HasPendingTransaction

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-sections-"))
        save_path = save_directory / "ModelSurfaceSections.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()
        for record in records:
            result = document.getObject(record["name"])
            assert result is not None and result.TypeId == "Surface::Sections"
            assert result.Label == record["label"]
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            assert tuple(
                (target.Name, names) for target, names in _flatten_links(result.NSections)
            ) == record["sections"]
            _assert_signature(_shape_signature(result.Shape), record["signature"])
            assert {
                name: bool(document.getObject(name).Visibility)
                for name in record["source_visibility"]
            } == record["source_visibility"]
            PartDesign.validateDesign(result)

        print("VIBECAD_NATIVE_MODEL_SURFACE_SECTIONS_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if save_directory is not None:
            shutil.rmtree(save_directory, ignore_errors=True)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
