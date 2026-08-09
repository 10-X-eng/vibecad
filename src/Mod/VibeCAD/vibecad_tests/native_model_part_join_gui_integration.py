# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real GUI/provider lifecycle gate for retained Part Join features."""

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
from BOPTools import JoinAPI
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelErrors import NativeModelError
import VibeCADNativeModelJoinRuntime as join_runtime_module
from VibeCADNativeModelJoinSchema import model_join_capability_definition
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


def _close(left: float, right: float, tolerance: float = 1.0e-6) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def _shape_signature(shape) -> tuple[object, ...]:
    if shape is None or shape.isNull():
        return ("Null",)
    bounds = shape.BoundBox
    return (
        str(shape.ShapeType),
        len(shape.Vertexes),
        len(shape.Edges),
        len(shape.Wires),
        len(shape.Faces),
        len(shape.Shells),
        len(shape.Solids),
        round(float(shape.Length), 7),
        round(float(shape.Area), 7),
        round(float(shape.Volume), 7),
        *(round(float(getattr(bounds, name)), 7) for name in (
            "XMin",
            "XMax",
            "YMin",
            "YMax",
            "ZMin",
            "ZMax",
        )),
    )


def _assert_shape_matches(actual, expected, tolerance: float = 5.0e-3) -> None:
    observed = _shape_signature(actual) if not isinstance(actual, tuple) else actual
    wanted = _shape_signature(expected) if not isinstance(expected, tuple) else expected
    assert observed[:7] == wanted[:7], (observed, wanted)
    assert all(
        _close(left, right, tolerance)
        for left, right in zip(observed[7:], wanted[7:], strict=True)
    ), (observed, wanted)


def _publish_shape(document, name: str, shape, *, placement=None, visible=True):
    obj = document.addObject("Part::Feature", name)
    obj.Label = name
    obj.Shape = shape
    if placement is not None:
        obj.Placement = placement
    PartDesign.initializeDesignDefinition(obj)
    document.publishProvisionalTimelineOperationBlock(obj, (), ())
    assert document.recompute([obj], True, True) is not False
    PartDesign.finalizeDesignDefinition(obj)
    obj.Visibility = visible
    assert PartGui.isModelingObjectActive(obj)
    return obj


def _create_sources(document):
    document.openTransaction("Create Part Join gate sources")
    try:
        sources = {
            "HumanConnectA": _publish_shape(
                document,
                "HumanConnectA",
                Part.makeBox(10, 10, 10),
            ),
            "HumanConnectB": _publish_shape(
                document,
                "HumanConnectB",
                Part.makeBox(10, 10, 10, App.Vector(5, 0, 0)),
            ),
            "HumanCompound": _publish_shape(
                document,
                "HumanCompound",
                Part.makeCompound(
                    [
                        Part.makeBox(8, 8, 8, App.Vector(22, 0, 0)),
                        Part.makeBox(8, 8, 8, App.Vector(26, 0, 0)),
                    ]
                ),
            ),
            "HumanEmbedBase": _publish_shape(
                document,
                "HumanEmbedBase",
                Part.makeBox(10, 10, 10, App.Vector(40, 0, 0)),
            ),
            "HumanEmbedTool": _publish_shape(
                document,
                "HumanEmbedTool",
                Part.makeBox(4, 4, 4, App.Vector(43, 3, 3)),
            ),
            "HumanCutoutBase": _publish_shape(
                document,
                "HumanCutoutBase",
                Part.makeBox(10, 10, 10, App.Vector(58, 0, 0)),
            ),
            "HumanCutoutTool": _publish_shape(
                document,
                "HumanCutoutTool",
                Part.makeBox(4, 4, 4, App.Vector(61, 3, 3)),
            ),
            "NativeConnectA": _publish_shape(
                document,
                "NativeConnectA",
                Part.makeBox(10, 10, 10, App.Vector(76, 0, 0)),
                placement=App.Placement(
                    App.Vector(2, 1, 0),
                    App.Rotation(App.Vector(0, 0, 1), 5),
                ),
            ),
            "NativeConnectB": _publish_shape(
                document,
                "NativeConnectB",
                Part.makeBox(10, 10, 10, App.Vector(81, 0, 0)),
            ),
            "NativeCompound": _publish_shape(
                document,
                "NativeCompound",
                Part.makeCompound(
                    [
                        Part.makeCylinder(4, 10, App.Vector(100, 0, 0)),
                        Part.makeCylinder(4, 10, App.Vector(104, 0, 0)),
                    ]
                ),
            ),
            "NativeEmbedBase": _publish_shape(
                document,
                "NativeEmbedBase",
                Part.makeBox(12, 12, 12, App.Vector(120, 0, 0)),
            ),
            "NativeEmbedTool": _publish_shape(
                document,
                "NativeEmbedTool",
                Part.makeBox(5, 5, 5, App.Vector(124, 3, 3)),
                visible=False,
            ),
            "NativeCutoutBase": _publish_shape(
                document,
                "NativeCutoutBase",
                Part.makeBox(12, 12, 12, App.Vector(142, 0, 0)),
            ),
            "NativeCutoutTool": _publish_shape(
                document,
                "NativeCutoutTool",
                Part.makeBox(5, 5, 5, App.Vector(146, 3, 3)),
            ),
            "MixedFace": _publish_shape(
                document,
                "MixedFace",
                Part.makePlane(8, 8, App.Vector(165, 0, 0)),
            ),
            "SingleSolid": _publish_shape(
                document,
                "SingleSolid",
                Part.makeBox(5, 5, 5, App.Vector(180, 0, 0)),
            ),
            "NullShape": _publish_shape(document, "NullShape", Part.Shape()),
            "StaleBase": _publish_shape(
                document,
                "StaleBase",
                Part.makeBox(8, 8, 8, App.Vector(195, 0, 0)),
            ),
            "StaleTool": _publish_shape(
                document,
                "StaleTool",
                Part.makeBox(4, 4, 4, App.Vector(198, 2, 2)),
            ),
            "RollbackBase": _publish_shape(
                document,
                "RollbackBase",
                Part.makeBox(8, 8, 8, App.Vector(215, 0, 0)),
            ),
            "RollbackTool": _publish_shape(
                document,
                "RollbackTool",
                Part.makeBox(4, 4, 4, App.Vector(218, 2, 2)),
            ),
            "InactiveA": _publish_shape(
                document,
                "InactiveA",
                Part.makeBox(6, 6, 6, App.Vector(235, 0, 0)),
            ),
            "InactiveB": _publish_shape(
                document,
                "InactiveB",
                Part.makeBox(6, 6, 6, App.Vector(238, 0, 0)),
            ),
        }
        document.commitTransaction()
        return sources
    except Exception:
        document.abortTransaction()
        raise


def _select(*objects) -> None:
    Gui.Selection.clearSelection()
    for obj in objects:
        Gui.Selection.addSelection(obj)
    _process_events()


def _expected_shape(operation: str, objects, *, refine: bool, tolerance_mm: float):
    shapes = [obj.Shape for obj in objects]
    if operation == "connect":
        result = JoinAPI.connect(shapes, tolerance_mm)
    elif operation == "embed":
        result = JoinAPI.embed_legacy(shapes[0], shapes[1], tolerance_mm)
    else:
        result = JoinAPI.cutout_legacy(shapes[0], shapes[1], tolerance_mm)
    return result.removeSplitter() if refine else result


def _assert_join_object(
    result,
    *,
    operation: str,
    operands,
    refine: bool,
    tolerance_mm: float,
    expected_shape,
    replaced,
) -> None:
    native_name = operation.title()
    assert result is not None and result.TypeId == "Part::FeaturePython"
    assert str(result.Proxy.Type) == f"Feature{native_name}"
    if operation == "connect":
        assert tuple(result.Objects) == tuple(operands)
    else:
        assert result.Base is operands[0] and result.Tool is operands[1]
    assert bool(result.Refine) is refine
    assert _close(float(result.Tolerance), tolerance_mm, 1.0e-9)
    assert result.isValid() and result.Shape.isValid() and not result.Shape.isNull()
    _assert_shape_matches(result.Shape, expected_shape)
    assert result.getParentGeoFeatureGroup() is None
    assert str(result.VibeCADTimelineRole) == "operation"
    assert getattr(result, "VibeCADTimelineOwner", None) is None
    assert str(result.VibeCADDefinitionId) and str(result.DesignId)
    assert tuple(result.VibeCADTimelineReplacedInputs) == tuple(replaced)
    assert tuple(result.ViewObject.Proxy.claimChildren()) == tuple(operands)
    assert all(not obj.Visibility for obj in operands)
    timeline = result.Document.getObject("VibeCADTimeline")
    assert timeline is not None and list(timeline.Operations).count(result) == 1


def _human_join(document, command: str, operation: str, operands):
    expected = _expected_shape(operation, operands, refine=False, tolerance_mm=0.0)
    _select(*operands)
    assert Gui.isCommandActive(command)
    before = tuple(obj.Name for obj in document.Objects)
    Gui.runCommand(command, 0)
    _process_events(40)
    assert not Gui.Control.activeDialog()
    result = document.ActiveObject
    assert result is not None and result.Name not in before
    _assert_join_object(
        result,
        operation=operation,
        operands=operands,
        refine=False,
        tolerance_mm=0.0,
        expected_shape=expected,
        replaced=operands,
    )
    return result


def _arguments(
    operation: str,
    label: str,
    operands,
    *,
    refine: bool,
    tolerance_mm: float,
):
    controls = {"refine": refine, "tolerance_mm": tolerance_mm}
    if operation == "connect":
        definition = {
            "sources": [{"object_name": obj.Name} for obj in operands],
            **controls,
        }
    else:
        definition = {
            "base": {"object_name": operands[0].Name},
            "tool": {"object_name": operands[1].Name},
            **controls,
        }
    return {"operation": operation, "label": label, "definition": definition}


def _turn() -> NativeTurnSnapshot:
    definition = model_join_capability_definition()
    operations = tuple(variant.operation for variant in definition.variants)
    action_ids = tuple(
        sorted(action_id for variant in definition.variants for action_id in variant.action_ids)
    )
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot("model", 1, "j" * 64, action_ids, (), ()),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(operations),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _record(result, operation: str, operands) -> dict[str, object]:
    return {
        "name": result.Name,
        "label": str(result.Label),
        "operation": operation,
        "proxy_type": str(result.Proxy.Type),
        "operand_names": [obj.Name for obj in operands],
        "refine": bool(result.Refine),
        "tolerance_mm": float(result.Tolerance),
        "shape": _shape_signature(result.Shape),
        "definition_id": str(result.VibeCADDefinitionId),
        "design_id": str(result.DesignId),
        "replaced_names": [obj.Name for obj in result.VibeCADTimelineReplacedInputs],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temp_directory = None
    exit_code = 1
    refine_parameters = App.ParamGet(
        "User parameter:BaseApp/Preferences/Mod/Part/Boolean"
    )
    old_refine = refine_parameters.GetBool("RefineModel", False)
    try:
        refine_parameters.SetBool("RefineModel", False)
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartJoinGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources = _create_sources(document)

        # The actual human commands are immediate retained features with ordered roles.
        human_connect = _human_join(
            document,
            "Part_JoinConnect",
            "connect",
            (sources["HumanConnectA"], sources["HumanConnectB"]),
        )
        human_compound = _human_join(
            document,
            "Part_JoinConnect",
            "connect",
            (sources["HumanCompound"],),
        )
        human_embed = _human_join(
            document,
            "Part_JoinEmbed",
            "embed",
            (sources["HumanEmbedBase"], sources["HumanEmbedTool"]),
        )
        human_cutout = _human_join(
            document,
            "Part_JoinCutout",
            "cutout",
            (sources["HumanCutoutBase"], sources["HumanCutoutTool"]),
        )
        assert human_connect.Label == "Connect"
        assert human_compound.Label == human_compound.Name
        assert human_compound.Name.startswith("Connect")
        assert human_embed.Label == "Embed"
        assert human_cutout.Label == "Cutout"

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-join-gui")
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
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                "model.join",
                json.dumps(arguments, separators=(",", ":")),
                f"model-part-join-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            return response

        def create_native(operation, label, operands, *, refine, tolerance_mm):
            expected = _expected_shape(
                operation,
                operands,
                refine=refine,
                tolerance_mm=tolerance_mm,
            )
            visible = tuple(obj for obj in operands if obj.Visibility)
            response = native_call(
                _arguments(
                    operation,
                    label,
                    operands,
                    refine=refine,
                    tolerance_mm=tolerance_mm,
                )
            )
            assert set(response) == {
                "ok",
                "root",
                "operation",
                "operand_count",
                "refined",
                "tolerance_mm",
                "shape_type",
                "solid_count",
                "face_count",
                "edge_count",
                "area_mm2",
                "volume_mm3",
                "receipt",
                "assistant_undo_available",
            }
            result = document.getObject(response["root"]["object_name"])
            _assert_join_object(
                result,
                operation=operation,
                operands=operands,
                refine=refine,
                tolerance_mm=tolerance_mm,
                expected_shape=expected,
                replaced=visible,
            )
            assert response["operation"] == operation
            assert response["operand_count"] == len(operands)
            assert response["refined"] is refine
            assert _close(response["tolerance_mm"], tolerance_mm, 1.0e-9)
            assert [item["object_name"] for item in response["receipt"]["created"]] == [
                result.Name
            ]
            assert [item["object_name"] for item in response["receipt"]["replaced"]] == [
                obj.Name for obj in visible
            ]
            assert response["assistant_undo_available"] is True
            return result

        # Closed-schema and semantic failures do not begin mutations.
        invalid = _arguments(
            "connect",
            "Invalid Join",
            (sources["SingleSolid"], sources["MixedFace"]),
            refine=False,
            tolerance_mm=0.0,
        )
        del invalid["definition"]["refine"]
        before = tuple(obj.Name for obj in document.Objects)
        failure = native_call(invalid, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        for arguments, expected_code in (
            (
                _arguments(
                    "connect",
                    "One Solid",
                    (sources["SingleSolid"],),
                    refine=False,
                    tolerance_mm=0.0,
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "connect",
                    "Mixed Dimensions",
                    (sources["SingleSolid"], sources["MixedFace"]),
                    refine=False,
                    tolerance_mm=0.0,
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                {
                    "operation": "embed",
                    "label": "Duplicate Pair",
                    "definition": {
                        "base": {"object_name": sources["SingleSolid"].Name},
                        "tool": {"object_name": sources["SingleSolid"].Name},
                        "refine": False,
                        "tolerance_mm": 0.0,
                    },
                },
                "NATIVE_MODEL_INVALID",
            ),
            (
                {
                    "operation": "connect",
                    "label": "Missing Source",
                    "definition": {
                        "sources": [
                            {"object_name": sources["SingleSolid"].Name},
                            {"object_name": "MissingJoinSource"},
                        ],
                        "refine": False,
                        "tolerance_mm": 0.0,
                    },
                },
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments(
                    "connect",
                    "Null Source",
                    (sources["SingleSolid"], sources["NullShape"]),
                    refine=False,
                    tolerance_mm=0.0,
                ),
                "NATIVE_MODEL_INVALID",
            ),
        ):
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(arguments, succeeds=False)
            assert response["error_code"] == expected_code
            assert tuple(obj.Name for obj in document.Objects) == before
            assert not document.HasPendingTransaction

        native_connect = create_native(
            "connect",
            "Native Refined Connect",
            (sources["NativeConnectA"], sources["NativeConnectB"]),
            refine=True,
            tolerance_mm=0.01,
        )
        connect_record = _record(
            native_connect,
            "connect",
            (sources["NativeConnectA"], sources["NativeConnectB"]),
        )
        connect_name = native_connect.Name
        document.undo()
        _process_events()
        assert document.getObject(connect_name) is None
        assert sources["NativeConnectA"].Visibility
        assert sources["NativeConnectB"].Visibility
        document.redo()
        _process_events()
        native_connect = document.getObject(connect_name)
        assert native_connect is not None
        assert not sources["NativeConnectA"].Visibility
        assert not sources["NativeConnectB"].Visibility
        _assert_shape_matches(native_connect.Shape, connect_record["shape"])

        native_compound = create_native(
            "connect",
            "Native Compound Connect",
            (sources["NativeCompound"],),
            refine=False,
            tolerance_mm=0.0,
        )
        native_embed = create_native(
            "embed",
            "Native Embed",
            (sources["NativeEmbedBase"], sources["NativeEmbedTool"]),
            refine=False,
            tolerance_mm=0.0,
        )
        assert tuple(native_embed.VibeCADTimelineReplacedInputs) == (
            sources["NativeEmbedBase"],
        )
        native_cutout = create_native(
            "cutout",
            "Native Refined Cutout",
            (sources["NativeCutoutBase"], sources["NativeCutoutTool"]),
            refine=True,
            tolerance_mm=0.0,
        )

        # Timeline position is an authority boundary for exact current operands.
        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        timeline.Position = 0
        _process_events()
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(
            _arguments(
                "connect",
                "Inactive Join",
                (sources["InactiveA"], sources["InactiveB"]),
                refine=False,
                tolerance_mm=0.0,
            ),
            succeeds=False,
        )
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()

        # Changes between preflight and mutation are rejected and rolled back.
        stale_before = _shape_signature(sources["StaleTool"].Shape)
        stale_visibility = (
            sources["StaleBase"].Visibility,
            sources["StaleTool"].Visibility,
        )
        before = tuple(obj.Name for obj in document.Objects)
        original_create = join_runtime_module.create_part_join

        def change_after_preflight(target_document, **kwargs):
            sources["StaleTool"].Shape = Part.makeSphere(3, App.Vector(199, 3, 3))
            return original_create(target_document, **kwargs)

        join_runtime_module.create_part_join = change_after_preflight
        try:
            stale = native_call(
                _arguments(
                    "cutout",
                    "Stale Cutout",
                    (sources["StaleBase"], sources["StaleTool"]),
                    refine=False,
                    tolerance_mm=0.0,
                ),
                succeeds=False,
            )
        finally:
            join_runtime_module.create_part_join = original_create
        assert stale["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        _assert_shape_matches(sources["StaleTool"].Shape, stale_before)
        assert (
            sources["StaleBase"].Visibility,
            sources["StaleTool"].Visibility,
        ) == stale_visibility

        # A failed postcondition removes the result and restores input visibility.
        rollback_visibility = (
            sources["RollbackBase"].Visibility,
            sources["RollbackTool"].Visibility,
        )
        before = tuple(obj.Name for obj in document.Objects)
        original_verify = join_runtime_module.verify_part_join

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced Part Join postcondition failure.")

        join_runtime_module.verify_part_join = reject_after_creation
        try:
            rejected = native_call(
                _arguments(
                    "embed",
                    "Rejected Embed",
                    (sources["RollbackBase"], sources["RollbackTool"]),
                    refine=False,
                    tolerance_mm=0.0,
                ),
                succeeds=False,
            )
        finally:
            join_runtime_module.verify_part_join = original_verify
        assert rejected["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert (
            sources["RollbackBase"].Visibility,
            sources["RollbackTool"].Visibility,
        ) == rollback_visibility
        assert not document.HasPendingTransaction

        records = (
            connect_record,
            _record(native_compound, "connect", (sources["NativeCompound"],)),
            _record(
                native_embed,
                "embed",
                (sources["NativeEmbedBase"], sources["NativeEmbedTool"]),
            ),
            _record(
                native_cutout,
                "cutout",
                (sources["NativeCutoutBase"], sources["NativeCutoutTool"]),
            ),
        )
        for record in records:
            result = document.getObject(record["name"])
            before_shape = _shape_signature(result.Shape)
            assert document.recompute([result], True, True) is not False
            _assert_shape_matches(result.Shape, before_shape)

        temp_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-part-join-"))
        save_path = temp_directory / "ModelPartJoin.FCStd"
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()
        for record in records:
            result = document.getObject(record["name"])
            assert result is not None and result.TypeId == "Part::FeaturePython"
            assert str(result.Label) == record["label"]
            assert str(result.Proxy.Type) == record["proxy_type"]
            operands = tuple(document.getObject(name) for name in record["operand_names"])
            if record["operation"] == "connect":
                assert tuple(result.Objects) == operands
            else:
                assert result.Base is operands[0] and result.Tool is operands[1]
            assert tuple(result.ViewObject.Proxy.claimChildren()) == operands
            assert bool(result.Refine) is record["refine"]
            assert _close(float(result.Tolerance), record["tolerance_mm"], 1.0e-9)
            _assert_shape_matches(result.Shape, record["shape"])
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            assert [obj.Name for obj in result.VibeCADTimelineReplacedInputs] == record[
                "replaced_names"
            ]
            assert all(not obj.Visibility for obj in operands)

        print("VIBECAD_NATIVE_MODEL_PART_JOIN_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        refine_parameters.SetBool("RefineModel", old_refine)
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temp_directory is not None:
            shutil.rmtree(temp_directory, ignore_errors=True)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
