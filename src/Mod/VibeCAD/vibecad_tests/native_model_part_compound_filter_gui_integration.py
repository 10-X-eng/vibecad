# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for Native Model Compound Filter."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADNativeModelPartRuntime as runtime_module
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelPartSchema import model_part_capability_definition
from VibeCADNativePartCompoundFilter import (
    create_part_compound_filter,
    preflight_part_compound_filter,
    prepare_part_compound_filter,
)
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


def _publish_shape(document, name, shape, *, placement=None, visible=True):
    document.openTransaction(f"Create {name}")
    try:
        obj = document.addObject("Part::Feature", name)
        obj.Label = name
        obj.Shape = shape
        if placement is not None:
            obj.Placement = placement
        PartDesign.initializeDesignDefinition(obj)
        document.publishProvisionalTimelineOperationBlock(obj, (), ())
        document.recompute([obj], True, True)
        PartDesign.finalizeDesignDefinition(obj)
        obj.Visibility = visible
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return obj


def _compound(document, name, children, *, placement=None, visible=True):
    return _publish_shape(
        document,
        name,
        Part.makeCompound(list(children)),
        placement=placement,
        visible=visible,
    )


def _select(*objects, subelement=None) -> None:
    Gui.Selection.clearSelection()
    for obj in objects:
        if subelement:
            Gui.Selection.addSelection(obj, subelement)
        else:
            Gui.Selection.addSelection(obj)
    _process_events(4)


def _shape_signature(shape):
    bounds = shape.BoundBox
    return {
        "type": str(shape.ShapeType),
        "topology": (
            len(shape.Vertexes),
            len(shape.Edges),
            len(shape.Faces),
            len(shape.Solids),
        ),
        "measure": (
            float(shape.Volume),
            float(shape.Area),
            float(shape.Length),
        ),
        "bounds": tuple(
            float(getattr(bounds, name))
            for name in ("XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax")
        ),
    }


def _assert_signature(actual, expected):
    assert actual["type"] == expected["type"]
    assert actual["topology"] == expected["topology"]
    for left, right in zip(actual["measure"], expected["measure"]):
        assert abs(left - right) <= max(1.0e-7, abs(right) * 1.0e-9)
    for left, right in zip(actual["bounds"], expected["bounds"]):
        assert abs(left - right) <= max(1.0e-7, abs(right) * 1.0e-9)


def _assert_filter(
    result,
    source,
    *,
    native_mode,
    output_count,
    stencil=None,
    replaced=None,
):
    assert result.TypeId == "Part::FeaturePython"
    assert result.Proxy.Type == "CompoundFilter"
    assert result.Base is source
    assert result.Stencil is stencil
    assert result.FilterType == native_mode
    assert result.getParentGeoFeatureGroup() is None
    assert result.isValid(), result.getStatusString()
    assert not result.Shape.isNull() and result.Shape.isValid()
    assert result.VibeCADTimelineRole == "operation"
    assert getattr(result, "VibeCADTimelineOwner", None) is None
    assert str(result.VibeCADDefinitionId) and str(result.DesignId)
    assert list(result.VibeCADTimelineReplacedInputs) == list(replaced or [source])
    assert not source.Visibility
    if stencil is not None and stencil in list(replaced or []):
        assert not stencil.Visibility
    assert tuple(result.ViewObject.claimChildren()) == tuple(
        item for item in (source, stencil) if item is not None
    )
    assert output_count >= 1
    PartDesign.validateDesign(result)


def _turn():
    definition = model_part_capability_definition()
    schema = definition.provider_schema(("compound_filter",))
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "e" * 64,
            ("Part_CompoundFilter",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=("model.part",),
        schemas=(schema,),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _dispatcher(document):
    service = get_service()
    service.select_modeling_engine("native")
    state = service.native_document_state_store()
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("native-model-part-compound-filter-gui")
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
    return NativeTurnDispatcher(
        document=document,
        state=state,
        registry=build_native_capability_registry(),
        turn=turn,
        runtimes=build_native_runtime_bindings(context, turn.tool_names),
        reauthorize_turn=lambda: None,
        active_document=lambda: App.ActiveDocument,
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartCompoundFilterGate")
        document.UndoMode = True
        VibeGui._connect_document_observer()
        _process_events()

        # The real ribbon command is immediate. One Compound defaults to the
        # top 80–100% volume window; a second shape defaults to collision-pass.
        human_single = document.addObject("Part::Feature", "HumanFilterSource")
        human_single.Shape = Part.makeCompound(
            [
                Part.makeBox(5, 5, 5),
                Part.makeBox(8, 8, 8, App.Vector(15, 0, 0)),
            ]
        )
        document.recompute()
        _select(human_single, subelement="Face1")
        assert Gui.isCommandActive("Part_CompoundFilter")
        before = {obj.Name for obj in document.Objects}
        source_brep = human_single.Shape.exportBrepToString()
        Gui.runCommand("Part_CompoundFilter", 0)
        _process_events()
        assert not Gui.Control.activeDialog()
        assert not document.HasPendingTransaction
        human_filter = next(
            obj
            for obj in document.Objects
            if obj.Name not in before and obj.TypeId == "Part::FeaturePython"
        )
        _assert_filter(
            human_filter,
            human_single,
            native_mode="window-volume",
            output_count=1,
        )
        assert human_filter.WindowFrom == 80.0
        assert human_filter.WindowTo == 100.0
        assert human_filter.OverrideMaxVal == 0.0
        assert human_filter.Invert is False
        assert abs(human_filter.Shape.Volume - 512.0) < 1.0e-7
        assert human_single.Shape.exportBrepToString() == source_brep

        human_collision = document.addObject("Part::Feature", "HumanCollisionSource")
        human_collision.Shape = Part.makeCompound(
            [
                Part.makeBox(6, 6, 6),
                Part.makeBox(6, 6, 6, App.Vector(20, 0, 0)),
            ]
        )
        human_stencil = document.addObject("Part::Feature", "HumanCollisionStencil")
        human_stencil.Shape = Part.makeBox(3, 3, 3, App.Vector(21, 1, 1))
        document.recompute()
        _select(human_collision, human_stencil)
        assert Gui.isCommandActive("Part_CompoundFilter")
        before = {obj.Name for obj in document.Objects}
        Gui.runCommand("Part_CompoundFilter", 0)
        _process_events()
        collision_filter = next(
            obj
            for obj in document.Objects
            if obj.Name not in before and obj.TypeId == "Part::FeaturePython"
        )
        _assert_filter(
            collision_filter,
            human_collision,
            native_mode="collision-pass",
            output_count=1,
            stencil=human_stencil,
            replaced=[human_collision, human_stencil],
        )
        assert abs(collision_filter.Shape.BoundBox.XMin - 20.0) < 1.0e-7

        dispatcher = _dispatcher(document)
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                "model.part",
                json.dumps(arguments, separators=(",", ":")),
                f"model-compound-filter-call-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            return result

        def arguments(label, definition):
            return {
                "operation": "compound_filter",
                "label": label,
                "definition": definition,
            }

        # Schema and mode-field failures are no-ops.
        before = tuple(obj.Name for obj in document.Objects)
        malformed = native_call(
            {
                "operation": "compound_filter",
                "label": "Missing definition",
            },
            succeeds=False,
        )
        assert malformed["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        bad_mode_source = _compound(
            document,
            "BadModeSource",
            [Part.makeBox(2, 2, 2), Part.makeBox(3, 3, 3, App.Vector(8, 0, 0))],
        )
        before = tuple(obj.Name for obj in document.Objects)
        bad_fields = native_call(
            arguments(
                "Bad fields",
                {
                    "source": {"object_name": bad_mode_source.Name},
                    "mode": "bypass",
                    "invert": False,
                },
            ),
            succeeds=False,
        )
        assert bad_fields["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        solid_source = _publish_shape(document, "NotACompound", Part.makeBox(4, 4, 4))
        invalid = native_call(
            arguments(
                "Invalid solid source",
                {"source": {"object_name": solid_source.Name}, "mode": "bypass"},
            ),
            succeeds=False,
        )
        assert invalid["error_code"] == "NATIVE_MODEL_INVALID"

        missing = native_call(
            arguments(
                "Missing source",
                {"source": {"object_name": "MissingCompound"}, "mode": "bypass"},
            ),
            succeeds=False,
        )
        assert missing["error_code"] == "NATIVE_TARGET_INVALID"

        # Bypass preserves every direct child and transformed source state.
        bypass_source = _compound(
            document,
            "BypassSource",
            [
                Part.Vertex(App.Vector(0, 0, 0)),
                Part.makeLine(App.Vector(3, 0, 0), App.Vector(7, 0, 0)),
                Part.makeBox(2, 3, 4, App.Vector(12, 0, 0)),
            ],
            placement=App.Placement(
                App.Vector(8, -3, 2),
                App.Rotation(App.Vector(0, 0, 1), 19),
            ),
        )
        bypass_shape = _shape_signature(bypass_source.Shape)
        bypass_placement = bypass_source.Placement
        bypass_result = native_call(
            arguments(
                "Bypass Compound",
                {"source": {"object_name": bypass_source.Name}, "mode": "bypass"},
            )
        )
        bypass = document.getObject(bypass_result["root"]["object_name"])
        _assert_filter(
            bypass,
            bypass_source,
            native_mode="bypass",
            output_count=3,
        )
        assert bypass_result["input_child_count"] == 3
        assert bypass_result["output_child_count"] == 3
        _assert_signature(_shape_signature(bypass_source.Shape), bypass_shape)
        assert bypass_source.Placement == bypass_placement

        # Typed indices and Python-style slices replace the legacy raw string.
        specific_source = _compound(
            document,
            "SpecificSource",
            [
                Part.makeBox(index + 1, 2, 2, App.Vector(index * 10, 0, 0))
                for index in range(5)
            ],
        )
        specific_result = native_call(
            arguments(
                "Specific Children",
                {
                    "source": {"object_name": specific_source.Name},
                    "mode": "specific_items",
                    "selectors": [0, [2, 5, 2]],
                    "invert": False,
                },
            )
        )
        specific = document.getObject(specific_result["root"]["object_name"])
        _assert_filter(
            specific,
            specific_source,
            native_mode="specific items",
            output_count=3,
        )
        assert specific.items == "0;2:5:2"
        assert specific_result["output_child_count"] == 3

        inverse_source = _compound(
            document,
            "InverseSpecificSource",
            [
                Part.makeBox(index + 1, 2, 2, App.Vector(index * 10, 0, 0))
                for index in range(5)
            ],
        )
        inverse_result = native_call(
            arguments(
                "Inverse Specific Children",
                {
                    "source": {"object_name": inverse_source.Name},
                    "mode": "specific_items",
                    "selectors": [0, [2, 4]],
                    "invert": True,
                },
            )
        )
        inverse = document.getObject(inverse_result["root"]["object_name"])
        _assert_filter(
            inverse,
            inverse_source,
            native_mode="specific items",
            output_count=2,
        )
        assert inverse.Invert is True
        assert inverse_result["output_child_count"] == 2

        out_of_range_source = _compound(
            document,
            "OutOfRangeSource",
            [Part.makeBox(2, 2, 2), Part.makeBox(3, 3, 3, App.Vector(8, 0, 0))],
        )
        before = tuple(obj.Name for obj in document.Objects)
        out_of_range = native_call(
            arguments(
                "Out of range",
                {
                    "source": {"object_name": out_of_range_source.Name},
                    "mode": "specific_items",
                    "selectors": [9],
                    "invert": False,
                },
            ),
            succeeds=False,
        )
        assert out_of_range["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        # Collision and inverted collision both use the exact stencil.
        collision_source = _compound(
            document,
            "NativeCollisionSource",
            [
                Part.makeBox(5, 5, 5),
                Part.makeBox(5, 5, 5, App.Vector(15, 0, 0)),
                Part.makeBox(5, 5, 5, App.Vector(30, 0, 0)),
            ],
        )
        collision_stencil = _publish_shape(
            document,
            "NativeCollisionStencil",
            Part.makeBox(3, 3, 3, App.Vector(16, 1, 1)),
        )
        collision_result = native_call(
            arguments(
                "Native Collision",
                {
                    "source": {"object_name": collision_source.Name},
                    "mode": "collision",
                    "stencil": {"object_name": collision_stencil.Name},
                    "invert": False,
                },
            )
        )
        collision = document.getObject(collision_result["root"]["object_name"])
        _assert_filter(
            collision,
            collision_source,
            native_mode="collision-pass",
            output_count=1,
            stencil=collision_stencil,
            replaced=[collision_source, collision_stencil],
        )
        assert collision_result["output_child_count"] == 1

        inverted_collision_source = _compound(
            document,
            "InvertedCollisionSource",
            [
                Part.makeBox(5, 5, 5),
                Part.makeBox(5, 5, 5, App.Vector(15, 0, 0)),
                Part.makeBox(5, 5, 5, App.Vector(30, 0, 0)),
            ],
        )
        inverted_stencil = _publish_shape(
            document,
            "InvertedCollisionStencil",
            Part.makeBox(3, 3, 3, App.Vector(16, 1, 1)),
        )
        inverted_collision_result = native_call(
            arguments(
                "Inverted Native Collision",
                {
                    "source": {"object_name": inverted_collision_source.Name},
                    "mode": "collision",
                    "stencil": {"object_name": inverted_stencil.Name},
                    "invert": True,
                },
            )
        )
        assert inverted_collision_result["output_child_count"] == 2

        # Every metric window, optional stencil maximum, override, and inversion.
        window_records = []
        for mode in ("volume", "area", "length"):
            source = _compound(
                document,
                f"{mode.title()}WindowSource",
                [
                    Part.makeBox(2, 2, 2),
                    Part.makeBox(4, 4, 4, App.Vector(10, 0, 0)),
                    Part.makeBox(8, 8, 8, App.Vector(25, 0, 0)),
                ],
            )
            stencil = (
                _publish_shape(
                    document,
                    f"{mode.title()}WindowStencil",
                    Part.makeBox(10, 10, 10),
                )
                if mode == "area"
                else None
            )
            window_percent = [60.0, 70.0] if mode == "area" else [40.0, 60.0]
            result = native_call(
                arguments(
                    f"{mode.title()} Window",
                    {
                        "source": {"object_name": source.Name},
                        "mode": mode,
                        "stencil": (
                            {"object_name": stencil.Name} if stencil else None
                        ),
                        "window_percent": window_percent,
                        "maximum": 1_000.0 if mode == "volume" else None,
                        "invert": mode == "length",
                    },
                )
            )
            filtered = document.getObject(result["root"]["object_name"])
            replaced = [source, stencil] if stencil else [source]
            _assert_filter(
                filtered,
                source,
                native_mode=f"window-{mode}",
                output_count=result["output_child_count"],
                stencil=stencil,
                replaced=replaced,
            )
            assert filtered.WindowFrom == window_percent[0]
            assert filtered.WindowTo == window_percent[1]
            assert filtered.OverrideMaxVal == (
                1_000.0 if mode == "volume" else 0.0
            )
            window_records.append((filtered, source, stencil, result))

        distance_source = _compound(
            document,
            "DistanceWindowSource",
            [
                Part.makeBox(3, 3, 3),
                Part.makeBox(3, 3, 3, App.Vector(20, 0, 0)),
                Part.makeBox(3, 3, 3, App.Vector(50, 0, 0)),
            ],
        )
        distance_stencil = _publish_shape(
            document,
            "DistanceWindowStencil",
            Part.makeBox(2, 2, 2, App.Vector(21, 0, 0)),
            visible=False,
        )
        distance_result = native_call(
            arguments(
                "Distance Window",
                {
                    "source": {"object_name": distance_source.Name},
                    "mode": "distance",
                    "stencil": {"object_name": distance_stencil.Name},
                    "window_percent": [0.0, 10.0],
                    "maximum": None,
                    "invert": False,
                },
            )
        )
        distance = document.getObject(distance_result["root"]["object_name"])
        _assert_filter(
            distance,
            distance_source,
            native_mode="window-distance",
            output_count=distance_result["output_child_count"],
            stencil=distance_stencil,
            replaced=[distance_source],
        )
        assert list(distance.VibeCADTimelineReplacedInputs) == [distance_source]
        assert not distance_stencil.Visibility

        # A window that passes nothing is rejected before a transaction.
        empty_source = _compound(
            document,
            "EmptyWindowSource",
            [Part.makeBox(2, 2, 2), Part.makeBox(4, 4, 4, App.Vector(10, 0, 0))],
        )
        before = tuple(obj.Name for obj in document.Objects)
        empty = native_call(
            arguments(
                "Empty Window",
                {
                    "source": {"object_name": empty_source.Name},
                    "mode": "volume",
                    "stencil": None,
                    "window_percent": [200.0, 300.0],
                    "maximum": None,
                    "invert": False,
                },
            ),
            succeeds=False,
        )
        assert empty["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        # Exact preflight catches geometry and presentation changes.
        changed_source = _compound(
            document,
            "ChangedFilterSource",
            [Part.makeBox(2, 2, 2), Part.makeBox(4, 4, 4, App.Vector(10, 0, 0))],
        )
        spec = prepare_part_compound_filter(
            document.Uid,
            {"source": {"object_name": changed_source.Name}, "mode": "bypass"},
        )
        prepared = preflight_part_compound_filter(document, spec)
        changed_source.Visibility = False
        before = tuple(obj.Name for obj in document.Objects)
        document.openTransaction("Reject changed Compound Filter preflight")
        try:
            try:
                create_part_compound_filter(
                    document,
                    label="Must Not Exist",
                    prepared=prepared,
                )
            except NativeModelError:
                pass
            else:
                raise AssertionError("Changed Compound Filter preflight was accepted")
        finally:
            document.abortTransaction()
        assert tuple(obj.Name for obj in document.Objects) == before

        # Forced verifier failure rolls back result and input visibility.
        rollback_source = _compound(
            document,
            "RollbackFilterSource",
            [Part.makeBox(2, 2, 2), Part.makeBox(4, 4, 4, App.Vector(10, 0, 0))],
        )
        rollback_names = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_part_compound_filter

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced Compound Filter postcondition failure")

        runtime_module.verify_part_compound_filter = reject_after_creation
        try:
            rollback = native_call(
                arguments(
                    "Rollback Filter",
                    {"source": {"object_name": rollback_source.Name}, "mode": "bypass"},
                ),
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_compound_filter = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == rollback_names
        assert rollback_source.Visibility
        assert not document.HasPendingTransaction

        # Recompute, one-step undo/redo, and FCStd persistence retain the
        # Python proxy, exact controls, source identity, and result geometry.
        persistent_source = _compound(
            document,
            "PersistentFilterSource",
            [
                Part.makeBox(2, 2, 2),
                Part.makeBox(4, 4, 4, App.Vector(10, 0, 0)),
                Part.makeBox(6, 6, 6, App.Vector(25, 0, 0)),
            ],
        )
        persistent_result = native_call(
            arguments(
                "Persistent Filter",
                {
                    "source": {"object_name": persistent_source.Name},
                    "mode": "specific_items",
                    "selectors": [-1, 0],
                    "invert": False,
                },
            )
        )
        result_name = persistent_result["root"]["object_name"]
        result = document.getObject(result_name)
        accepted_definition_id = str(result.VibeCADDefinitionId)
        accepted_shape = _shape_signature(result.Shape)
        source_name = persistent_source.Name
        for _index in range(3):
            document.recompute()
            _process_events(4)
            result = document.getObject(result_name)
            _assert_filter(
                result,
                persistent_source,
                native_mode="specific items",
                output_count=2,
            )
            _assert_signature(_shape_signature(result.Shape), accepted_shape)

        document.undo()
        _process_events()
        assert document.getObject(result_name) is None
        assert persistent_source.Visibility
        document.redo()
        _process_events()
        result = document.getObject(result_name)
        persistent_source = document.getObject(source_name)
        _assert_filter(
            result,
            persistent_source,
            native_mode="specific items",
            output_count=2,
        )
        assert str(result.VibeCADDefinitionId) == accepted_definition_id
        _assert_signature(_shape_signature(result.Shape), accepted_shape)

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-filter-"))
        save_path = save_directory / "NativeCompoundFilter.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        document.recompute()
        _process_events()
        result = document.getObject(result_name)
        persistent_source = document.getObject(source_name)
        _assert_filter(
            result,
            persistent_source,
            native_mode="specific items",
            output_count=2,
        )
        assert result.items == "-1;0"
        assert str(result.VibeCADDefinitionId) == accepted_definition_id
        _assert_signature(_shape_signature(result.Shape), accepted_shape)
        assert not document.HasPendingTransaction
        assert not Gui.Control.activeDialog()

        print("VIBECAD_NATIVE_MODEL_PART_COMPOUND_FILTER_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            Gui.Control.activeTaskDialog().reject()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
