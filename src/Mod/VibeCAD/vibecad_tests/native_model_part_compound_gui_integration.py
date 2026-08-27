# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real GUI/provider lifecycle gate for standalone Part Compound."""

from __future__ import annotations

import json
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
import VibeCADNativeModelPartRuntime as runtime_module
from VibeCADNativeModelPartSchema import model_part_capability_definition
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _dismiss_message_boxes() -> None:
    for box in Gui.getMainWindow().findChildren(QtWidgets.QMessageBox):
        box.done(QtWidgets.QMessageBox.Yes)


def _close(left: float, right: float, tolerance: float = 5.0e-3) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def _shape_signature(shape) -> dict[str, object]:
    bounds = shape.BoundBox
    return {
        "shape_type": str(shape.ShapeType),
        "topology": (
            len(shape.Vertexes),
            len(shape.Edges),
            len(shape.Wires),
            len(shape.Faces),
            len(shape.Shells),
            len(shape.Solids),
        ),
        "bounds": tuple(
            float(getattr(bounds, name))
            for name in ("XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax")
        ),
        "length": float(shape.Length),
        "area": float(shape.Area),
        "volume": float(shape.Volume),
    }


def _assert_shape_signature(shape, expected) -> None:
    actual = _shape_signature(shape)
    assert actual["shape_type"] == expected["shape_type"]
    assert actual["topology"] == expected["topology"]
    assert all(
        _close(left, right, tolerance=2.0e-2)
        for left, right in zip(actual["bounds"], expected["bounds"], strict=True)
    ), (actual["bounds"], expected["bounds"])
    for field in ("length", "area", "volume"):
        assert _close(actual[field], expected[field])


def _placement_signature(obj) -> tuple[float, ...]:
    placement = obj.Placement
    return (
        float(placement.Base.x),
        float(placement.Base.y),
        float(placement.Base.z),
        *(float(value) for value in placement.Rotation.Q),
    )


def _publish_object(document, obj):
    PartDesign.initializeDesignDefinition(obj)
    document.publishProvisionalTimelineOperationBlock(obj, (), ())
    assert document.recompute([obj], True, True) is not False
    PartDesign.finalizeDesignDefinition(obj)
    assert PartGui.isModelingObjectActive(obj)
    return obj


def _publish_source(document, name: str, shape, *, placement=None):
    source = document.addObject("Part::Feature", name)
    source.Label = name
    source.Shape = shape
    if placement is not None:
        source.Placement = placement
    return _publish_object(document, source)


def _create_sources(document) -> tuple[dict[str, object], str]:
    document.openTransaction("Create Part Compound gate sources")
    try:
        sources = {
            "HumanFirst": _publish_source(
                document,
                "HumanFirst",
                Part.makeBox(10, 8, 6),
            ),
            "HumanSecond": _publish_source(
                document,
                "HumanSecond",
                Part.makeCylinder(3, 8, App.Vector(14, 2, 0)),
            ),
            "SingleSphere": _publish_source(
                document,
                "SingleSphere",
                Part.makeSphere(4, App.Vector(30, 0, 0)),
            ),
            "OrderedBox": _publish_source(
                document,
                "OrderedBox",
                Part.makeBox(8, 7, 5, App.Vector(45, 0, 0)),
            ),
            "OrderedCylinder": _publish_source(
                document,
                "OrderedCylinder",
                Part.makeCylinder(3, 9, App.Vector(58, 2, 0)),
            ),
            "MixedFace": _publish_source(
                document,
                "MixedFace",
                Part.makePlane(7, 6, App.Vector(75, 0, 0)),
            ),
            "MixedEdge": _publish_source(
                document,
                "MixedEdge",
                Part.makeLine(App.Vector(86, 0, 1), App.Vector(94, 4, 3)),
            ),
            "MixedVertex": _publish_source(
                document,
                "MixedVertex",
                Part.Vertex(App.Vector(99, 3, 2)),
            ),
            "PlacedBox": _publish_source(
                document,
                "PlacedBox",
                Part.makeBox(7, 6, 5),
                placement=App.Placement(
                    App.Vector(115, 5, 2),
                    App.Rotation(App.Vector(0, 0, 1), 23),
                ),
            ),
            "NestedCompound": _publish_source(
                document,
                "NestedCompound",
                Part.makeCompound(
                    [
                        Part.makeBox(4, 4, 4, App.Vector(130, 0, 0)),
                        Part.makeSphere(2.5, App.Vector(140, 3, 2.5)),
                    ]
                ),
            ),
            "VisibleSource": _publish_source(
                document,
                "VisibleSource",
                Part.makeBox(6, 5, 4, App.Vector(155, 0, 0)),
            ),
            "HiddenSource": _publish_source(
                document,
                "HiddenSource",
                Part.makeBox(5, 4, 3, App.Vector(165, 0, 0)),
            ),
            "RollbackFirst": _publish_source(
                document,
                "RollbackFirst",
                Part.makeBox(5, 5, 5, App.Vector(180, 0, 0)),
            ),
            "RollbackSecond": _publish_source(
                document,
                "RollbackSecond",
                Part.makeSphere(3, App.Vector(192, 2, 2)),
            ),
            "InactiveSource": _publish_source(
                document,
                "InactiveSource",
                Part.makeBox(4, 4, 4, App.Vector(205, 0, 0)),
            ),
            "NullCompoundSource": _publish_source(
                document,
                "NullCompoundSource",
                Part.Shape(),
            ),
        }
        sources["HumanFirst"].ViewObject.ShapeColor = (0.18, 0.44, 0.73)
        sources["OrderedBox"].ViewObject.ShapeColor = (0.71, 0.25, 0.16)
        sources["HiddenSource"].Visibility = False
        stale = _publish_source(
            document,
            "StaleCompoundSource",
            Part.makeBox(4, 4, 4, App.Vector(220, 0, 0)),
        )
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    document.openTransaction("Delete stale Part Compound source")
    try:
        stale_name = stale.Name
        document.removeObject(stale_name)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return sources, stale_name


def _turn() -> NativeTurnSnapshot:
    definition = model_part_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "a" * 64,
            ("Part_Compound",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("compound",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _arguments(label: str, *sources: str) -> dict[str, object]:
    return {
        "operation": "compound",
        "label": label,
        "definition": {
            "sources": [{"object_name": name} for name in sources],
        },
    }


def _compound_signature(sources, names) -> dict[str, object]:
    shape = Part.makeCompound(
        [Part.getShape(sources[name], transform=True).copy() for name in names]
    )
    return _shape_signature(shape)


def _assert_ordered_children(shape, sources, names) -> None:
    children = tuple(shape.childShapes())
    assert len(children) == len(names)
    for child, name in zip(children, names, strict=True):
        _assert_shape_signature(
            child,
            _shape_signature(Part.getShape(sources[name], transform=True)),
        )


def _assert_human_contract(document, sources) -> None:
    before = tuple(obj.Name for obj in document.Objects)
    Gui.Selection.clearSelection()
    _process_events()
    assert not Gui.isCommandActive("Part_Compound")
    Gui.Selection.addSelection(sources["HumanFirst"])
    Gui.Selection.addSelection(sources["HumanSecond"])
    _process_events()
    assert Gui.isCommandActive("Part_Compound")
    Gui.runCommand("Part_Compound", 0)
    _process_events(24)
    assert not Gui.Control.activeDialog()
    created = [obj for obj in document.Objects if obj.Name not in before]
    assert len(created) == 1 and created[0].TypeId == "Part::Compound"
    result = created[0]
    assert tuple(Gui.Selection.getSelection()) == (result,)
    assert tuple(result.Links) == (sources["HumanFirst"], sources["HumanSecond"])
    assert tuple(result.ViewObject.claimChildren()) == tuple(result.Links)
    assert tuple(result.VibeCADTimelineReplacedInputs) == tuple(result.Links)
    assert not sources["HumanFirst"].Visibility
    assert not sources["HumanSecond"].Visibility
    _assert_ordered_children(result.Shape, sources, ("HumanFirst", "HumanSecond"))
    result_name = result.Name
    undo_before_delete = int(document.UndoCount)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(result)
    QtCore.QTimer.singleShot(0, _dismiss_message_boxes)
    Gui.runCommand("Std_Delete", 0)
    _process_events(24)
    assert document.getObject(result_name) is None
    assert document.getObject("HumanFirst") is sources["HumanFirst"]
    assert document.getObject("HumanSecond") is sources["HumanSecond"]
    assert sources["HumanFirst"].Visibility and sources["HumanSecond"].Visibility
    assert int(document.UndoCount) == undo_before_delete + 1
    document.undo()
    _process_events()
    result = document.getObject(result_name)
    assert result is not None
    assert not sources["HumanFirst"].Visibility
    assert not sources["HumanSecond"].Visibility
    document.redo()
    _process_events()
    assert document.getObject(result_name) is None
    assert sources["HumanFirst"].Visibility and sources["HumanSecond"].Visibility
    document.undo()
    _process_events()
    assert document.getObject(result_name) is not None
    document.undo()
    _process_events()
    assert tuple(obj.Name for obj in document.Objects) == before
    assert sources["HumanFirst"].Visibility and sources["HumanSecond"].Visibility
    Gui.Selection.clearSelection()


def _assert_explicit_replacement_inputs_delete(document) -> None:
    before = tuple(obj.Name for obj in document.Objects)
    document.openTransaction("Create ordinary Compound delete sources")
    try:
        first = document.addObject("Part::Feature", "OrdinaryDeleteFirst")
        first.Shape = Part.makeBox(7, 5, 3, App.Vector(235, 0, 0))
        second = document.addObject("Part::Feature", "OrdinaryDeleteSecond")
        second.Shape = Part.makeCylinder(2, 6, App.Vector(246, 1, 0))
        assert document.recompute([first, second], True, True) is not False
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise

    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(first)
    Gui.Selection.addSelection(second)
    _process_events()
    assert Gui.isCommandActive("Part_Compound")
    Gui.runCommand("Part_Compound", 0)
    _process_events(24)
    selected = tuple(Gui.Selection.getSelection())
    assert len(selected) == 1 and selected[0].TypeId == "Part::Compound"
    result = selected[0]
    result_name = result.Name
    first_name = first.Name
    second_name = second.Name
    assert tuple(result.VibeCADTimelineReplacedInputs) == (first, second)

    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(result)
    Gui.Selection.addSelection(first)
    Gui.Selection.addSelection(second)
    QtCore.QTimer.singleShot(0, _dismiss_message_boxes)
    Gui.runCommand("Std_Delete", 0)
    _process_events(24)
    assert document.getObject(result_name) is None
    assert document.getObject(first_name) is None
    assert document.getObject(second_name) is None

    document.undo()
    _process_events()
    assert document.getObject(result_name) is not None
    assert document.getObject("OrdinaryDeleteFirst") is not None
    assert document.getObject("OrdinaryDeleteSecond") is not None
    document.redo()
    _process_events()
    assert document.getObject(result_name) is None
    assert document.getObject("OrdinaryDeleteFirst") is None
    assert document.getObject("OrdinaryDeleteSecond") is None
    document.undo()
    _process_events()
    document.undo()
    _process_events()
    document.undo()
    _process_events()
    assert tuple(obj.Name for obj in document.Objects) == before
    Gui.Selection.clearSelection()


def _assert_exact_preflight_rejects_change(document, sources) -> None:
    spec = runtime_module.prepare_part_compound(
        str(document.Uid),
        _arguments(
            "Exactness Probe",
            "RollbackFirst",
            "RollbackSecond",
        )["definition"],
    )
    prepared = runtime_module.preflight_part_compound(document, spec)
    source = sources["RollbackFirst"]
    original = source.Placement
    before = tuple(obj.Name for obj in document.Objects)
    rejected = False
    try:
        moved = App.Placement(original)
        moved.Base.y += 1.0
        source.Placement = moved
        try:
            runtime_module.create_part_compound(
                document,
                label="Must Not Exist",
                prepared=prepared,
            )
        except NativeModelError as exc:
            rejected = "changed after preflight" in str(exc)
    finally:
        source.Placement = original
        assert document.recompute([source], True, True) is not False
    assert rejected and tuple(obj.Name for obj in document.Objects) == before


def _cases():
    return (
        ("Gate Single Compound", ("SingleSphere",)),
        ("Gate Ordered Compound", ("OrderedBox", "OrderedCylinder")),
        ("Gate Mixed Compound", ("MixedFace", "MixedEdge", "MixedVertex")),
        ("Gate Placed Nested Compound", ("PlacedBox", "NestedCompound")),
        ("Gate Hidden Input Compound", ("VisibleSource", "HiddenSource")),
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartCompoundGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_human_contract(document, sources)
        _assert_exact_preflight_rejects_change(document, sources)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-compound-gui")
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
        registry = build_native_capability_registry()
        runtimes = build_native_runtime_bindings(context, turn.tool_names)

        def new_dispatcher():
            return NativeTurnDispatcher(
                document=document,
                state=state,
                registry=registry,
                turn=turn,
                runtimes=runtimes,
                reauthorize_turn=lambda: None,
                active_document=lambda: App.ActiveDocument,
                debug_sink=debug_events.append,
            )

        dispatcher = new_dispatcher()
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                "model.part",
                json.dumps(arguments, separators=(",", ":")),
                f"model-part-compound-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments.get("label"),
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        before = tuple(obj.Name for obj in document.Objects)
        incomplete = native_call(
            {
                "operation": "compound",
                "label": "Incomplete Compound",
                "definition": {},
            },
            succeeds=False,
        )
        assert incomplete["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        expected_fields = {
            "ok",
            "root",
            "source_count",
            "shape_type",
            "solid_count",
            "face_count",
            "edge_count",
            "area_mm2",
            "volume_mm3",
            "receipt",
            "assistant_undo_available",
        }
        records = []
        for label, names in _cases():
            expected = _compound_signature(sources, names)
            input_state = {
                name: (
                    _shape_signature(Part.getShape(sources[name], transform=True)),
                    _placement_signature(sources[name]),
                    bool(sources[name].Visibility),
                )
                for name in names
            }
            response = native_call(_arguments(label, *names))
            assert set(response) == expected_fields
            assert response["source_count"] == len(names)
            assert response["shape_type"] == "Compound"
            assert response["solid_count"] == expected["topology"][5]
            assert response["face_count"] == expected["topology"][3]
            assert response["edge_count"] == expected["topology"][1]
            assert _close(response["area_mm2"], expected["area"])
            assert _close(response["volume_mm3"], expected["volume"])
            result = document.getObject(response["root"]["object_name"])
            assert result.TypeId == "Part::Compound"
            assert tuple(result.Links) == tuple(sources[name] for name in names)
            assert tuple(result.ViewObject.claimChildren()) == tuple(result.Links)
            replaced_names = tuple(
                name for name in names if input_state[name][2]
            )
            assert tuple(result.VibeCADTimelineReplacedInputs) == tuple(
                sources[name] for name in replaced_names
            )
            assert result.VibeCADTimelineRole == "operation"
            assert str(result.VibeCADDefinitionId) and str(result.DesignId)
            _assert_shape_signature(result.Shape, expected)
            _assert_ordered_children(result.Shape, sources, names)
            for name, (shape, placement, was_visible) in input_state.items():
                _assert_shape_signature(Part.getShape(sources[name], transform=True), shape)
                assert _placement_signature(sources[name]) == placement
                assert not sources[name].Visibility
            record = {
                "result": result.Name,
                "names": names,
                "replaced_names": replaced_names,
                "definition_id": str(result.VibeCADDefinitionId),
                "design_id": str(result.DesignId),
                "signature": expected,
                "visibility": {
                    name: was_visible
                    for name, (_shape, _placement, was_visible) in input_state.items()
                },
            }
            records.append(record)

        invalid_calls = (
            (
                _arguments("Stale Compound", stale_name),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments("Null Compound", "NullCompoundSource"),
                "NATIVE_MODEL_INVALID",
            ),
            (
                {
                    "operation": "compound",
                    "label": "Subelement Compound",
                    "definition": {
                        "sources": [
                            {"object_name": "OrderedBox", "subelement": "Face1"}
                        ]
                    },
                },
                "NATIVE_ARGUMENTS_INVALID",
            ),
            (
                _arguments("Duplicate Compound", "OrderedBox", "OrderedBox"),
                "NATIVE_ARGUMENTS_INVALID",
            ),
        )
        for arguments, error_code in invalid_calls:
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(arguments, succeeds=False)
            assert response["error_code"] == error_code, response
            assert tuple(obj.Name for obj in document.Objects) == before
            assert document.HasPendingTransaction is False

        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        timeline.Position = 0
        _process_events()
        dispatcher = new_dispatcher()
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(
            _arguments("Inactive Compound", "InactiveSource"),
            succeeds=False,
        )
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()
        dispatcher = new_dispatcher()

        before = tuple(obj.Name for obj in document.Objects)
        visibility = {
            name: bool(sources[name].Visibility)
            for name in ("RollbackFirst", "RollbackSecond")
        }
        original_verify = runtime_module.verify_part_compound

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced Part Compound postcondition failure.")

        runtime_module.verify_part_compound = reject_after_creation
        try:
            rollback = native_call(
                _arguments(
                    "Rollback Compound",
                    "RollbackFirst",
                    "RollbackSecond",
                ),
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_compound = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert all(
            bool(sources[name].Visibility) is was_visible
            for name, was_visible in visibility.items()
        )

        # Recompute and undo/redo are deliberate out-of-band document events.
        # Run their durability coverage after the frozen Native turn has made
        # every provider call; performing them between calls would correctly
        # invalidate that turn's revision guard.
        for record in reversed(records):
            document.undo()
            _process_events()
            assert document.getObject(record["result"]) is None
            for name, was_visible in record["visibility"].items():
                assert bool(sources[name].Visibility) is was_visible
        for record in records:
            document.redo()
            _process_events()
            assert document.getObject(record["result"]) is not None
            assert all(not sources[name].Visibility for name in record["names"])
        for record in records:
            result = document.getObject(record["result"])
            for _repeat in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_shape_signature(result.Shape, record["signature"])
                _assert_ordered_children(
                    result.Shape,
                    sources,
                    record["names"],
                )

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-compound-"))
        save_path = save_directory / "ModelPartCompound.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()
        for record in records:
            result = document.getObject(record["result"])
            reopened_sources = tuple(document.getObject(name) for name in record["names"])
            assert tuple(result.Links) == reopened_sources
            assert tuple(result.ViewObject.claimChildren()) == reopened_sources
            assert tuple(result.VibeCADTimelineReplacedInputs) == tuple(
                document.getObject(name) for name in record["replaced_names"]
            )
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            assert all(not source.Visibility for source in reopened_sources)
            _assert_shape_signature(result.Shape, record["signature"])
            _assert_ordered_children(
                result.Shape,
                {source.Name: source for source in reopened_sources},
                record["names"],
            )

        _assert_explicit_replacement_inputs_delete(document)

        print("VIBECAD_NATIVE_MODEL_PART_COMPOUND_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if save_directory is not None:
            shutil.rmtree(save_directory, ignore_errors=True)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
