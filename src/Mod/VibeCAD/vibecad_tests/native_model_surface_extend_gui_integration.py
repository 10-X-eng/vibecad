# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real VibeCAD GUI and provider lifecycle gate for Surface Extend Face."""

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
from VibeCADNativeSurfaceExtend import (
    create_surface_extend,
    preflight_surface_extend,
    prepare_surface_extend,
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


def _plane(x: float):
    return Part.makePlane(10, 8, App.Vector(x, 0, 0))


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
    document.openTransaction("Create Surface Extend gate sources")
    try:
        sources = {
            "HumanPlane": _publish_source(document, "HumanPlane", _plane(0)),
            "DefaultPlane": _publish_source(document, "DefaultPlane", _plane(30)),
            "ControlledPlane": _publish_source(
                document, "ControlledPlane", _plane(60)
            ),
            "PlacedPlane": _publish_source(
                document,
                "PlacedPlane",
                _plane(0),
                placement=App.Placement(
                    App.Vector(90, -4, 3),
                    App.Rotation(App.Vector(0, 0, 1), 21),
                ),
            ),
            "HiddenPlane": _publish_source(
                document, "HiddenPlane", _plane(120), visible=False
            ),
            "InactivePlane": _publish_source(
                document, "InactivePlane", _plane(150)
            ),
            "RollbackPlane": _publish_source(
                document, "RollbackPlane", _plane(180)
            ),
            "EdgeOnly": _publish_source(
                document,
                "EdgeOnly",
                Part.makeLine(App.Vector(210, 0, 0), App.Vector(220, 0, 0)),
            ),
        }
        stale = _publish_source(document, "StalePlane", _plane(230))
        body, seed = _body_source(document, "BodyPlane", _plane(250))
        sources["BodyPlane"] = body
        sources["BodyPlaneSeed"] = seed
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise

    document.openTransaction("Delete stale Surface Extend source")
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
            "e" * 64,
            ("Surface_ExtendFace",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("extend_face",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _definition(source, **controls):
    return {
        "object_name": source.Name if hasattr(source, "Name") else str(source),
        "face": "Face1",
        **controls,
    }


def _arguments(label: str, definition):
    return {"operation": "extend_face", "label": label, "definition": definition}


def _link_sub(value):
    if not value:
        return None, ()
    target, names = value if isinstance(value, tuple) else (value, ())
    if isinstance(names, str):
        names = (names,) if names else ()
    return target, tuple(str(name) for name in names)


def _expected_controls(definition):
    return {
        "u_negative": float(definition.get("u_negative", 0.05)),
        "u_positive": float(definition.get("u_positive", 0.05)),
        "u_symmetric": bool(definition.get("u_symmetric", True)),
        "v_negative": float(definition.get("v_negative", 0.05)),
        "v_positive": float(definition.get("v_positive", 0.05)),
        "v_symmetric": bool(definition.get("v_symmetric", True)),
        "tolerance": float(definition.get("tolerance", 0.1)),
        "samples_u": int(definition.get("samples_u", 32)),
        "samples_v": int(definition.get("samples_v", 32)),
    }


def _assert_human_contract(document, source) -> None:
    Gui.Selection.clearSelection()
    _process_events()
    assert not Gui.isCommandActive("Surface_ExtendFace")
    Gui.Selection.addSelection(source, "Face1")
    _process_events()
    assert Gui.isCommandActive("Surface_ExtendFace")
    before = tuple(obj.Name for obj in document.Objects)
    undo_before = int(document.UndoCount)
    Gui.runCommand("Surface_ExtendFace", 0)
    _process_events(32)
    created = [obj for obj in document.Objects if obj.Name not in before]
    assert len(created) == 1 and created[0].TypeId == "Surface::Extend"
    result = created[0]
    assert not Gui.Control.activeDialog()
    assert _link_sub(result.Face) == (source, ("Face1",))
    assert (
        float(result.ExtendUNeg),
        float(result.ExtendUPos),
        bool(result.ExtendUSymetric),
        float(result.ExtendVNeg),
        float(result.ExtendVPos),
        bool(result.ExtendVSymetric),
        float(result.Tolerance),
        int(result.SampleU),
        int(result.SampleV),
    ) == (0.05, 0.05, True, 0.05, 0.05, True, 0.1, 32, 32)
    assert result.isValid() and result.Shape.ShapeType == "Face"
    assert source.Visibility and document.UndoCount == undo_before + 1
    result_name = result.Name
    signature = _shape_signature(result.Shape)
    document.undo()
    _process_events()
    assert document.getObject(result_name) is None and source.Visibility
    document.redo()
    _process_events()
    result = document.getObject(result_name)
    assert result is not None
    _assert_signature(_shape_signature(result.Shape), signature)


def _target(source):
    return PartGui.resolveModelingObject(source)


def _assert_result(document, response, arguments, sources):
    assert set(response) == {
        "ok",
        "root",
        "source",
        "face",
        "u_extension",
        "v_extension",
        "sample_grid",
        "tolerance",
        "area_mm2",
        "receipt",
        "assistant_undo_available",
    }
    definition = arguments["definition"]
    controls = _expected_controls(definition)
    result = document.getObject(response["root"]["object_name"])
    source = sources[definition["object_name"]]
    target = _target(source)
    assert result is not None and result.TypeId == "Surface::Extend"
    assert result.Label == arguments["label"]
    assert result.getParentGeoFeatureGroup() is None
    assert result.isValid() and result.Shape.isValid()
    assert result.Shape.ShapeType == "Face" and len(result.Shape.Faces) == 1
    assert result.VibeCADTimelineRole == "operation"
    assert getattr(result, "VibeCADTimelineOwner", None) is None
    assert str(result.VibeCADDefinitionId) and str(result.DesignId)
    assert _link_sub(result.Face) == (target, (definition["face"],))
    assert response["source"]["object_name"] == target.Name
    assert response["face"] == definition["face"]
    assert response["u_extension"] == [
        controls["u_negative"],
        controls["u_positive"],
        controls["u_symmetric"],
    ]
    assert response["v_extension"] == [
        controls["v_negative"],
        controls["v_positive"],
        controls["v_symmetric"],
    ]
    assert response["sample_grid"] == [controls["samples_u"], controls["samples_v"]]
    assert _close(response["tolerance"], controls["tolerance"])
    assert (
        float(result.ExtendUNeg),
        float(result.ExtendUPos),
        bool(result.ExtendUSymetric),
        float(result.ExtendVNeg),
        float(result.ExtendVPos),
        bool(result.ExtendVSymetric),
        float(result.Tolerance),
        int(result.SampleU),
        int(result.SampleV),
    ) == (
        controls["u_negative"],
        controls["u_positive"],
        controls["u_symmetric"],
        controls["v_negative"],
        controls["v_positive"],
        controls["v_symmetric"],
        controls["tolerance"],
        controls["samples_u"],
        controls["samples_v"],
    )
    assert _close(response["area_mm2"], result.Shape.Area)
    assert response["assistant_undo_available"] is True
    assert [item["object_name"] for item in response["receipt"]["created"]] == [
        result.Name
    ]
    assert response["receipt"]["changed"] == []
    assert response["receipt"]["deleted"] == []
    assert response["receipt"]["replaced"] == []
    PartDesign.validateDesign(result)
    return result, controls


def _record(result, controls, source_visibility):
    return {
        "name": result.Name,
        "label": str(result.Label),
        "definition_id": str(result.VibeCADDefinitionId),
        "design_id": str(result.DesignId),
        "face": (_link_sub(result.Face)[0].Name, _link_sub(result.Face)[1]),
        "controls": controls,
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
        document = App.newDocument("NativeModelSurfaceExtendGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_human_contract(document, sources["HumanPlane"])

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-surface-extend-gui")
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
                f"model-surface-extend-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments,
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        cases = (
            _arguments("Default Surface Extend", _definition(sources["DefaultPlane"])),
            _arguments(
                "Controlled Surface Extend",
                _definition(
                    sources["ControlledPlane"],
                    u_negative=-0.1,
                    u_positive=0.2,
                    u_symmetric=False,
                    v_negative=0.15,
                    v_positive=0.15,
                    v_symmetric=True,
                    tolerance=0.05,
                    samples_u=24,
                    samples_v=18,
                ),
            ),
            _arguments(
                "Placed Surface Extend",
                _definition(
                    sources["PlacedPlane"],
                    u_negative=0.1,
                    u_positive=0.1,
                    v_negative=0.2,
                    v_positive=0.2,
                    samples_u=16,
                    samples_v=20,
                ),
            ),
            _arguments("Body Surface Extend", _definition(sources["BodyPlane"])),
            _arguments("Hidden Input Extend", _definition(sources["HiddenPlane"])),
        )
        records = []
        for arguments in cases:
            source_name = arguments["definition"]["object_name"]
            source = sources[source_name]
            source_visibility = {source_name: bool(source.Visibility)}
            source_signature = _shape_signature(Part.getShape(source, transform=True))
            response = native_call(arguments)
            result, controls = _assert_result(document, response, arguments, sources)
            signature = _shape_signature(result.Shape)
            assert signature[6] > 0.0
            for _index in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_signature(_shape_signature(result.Shape), signature)
            _assert_signature(
                _shape_signature(Part.getShape(source, transform=True)),
                source_signature,
            )
            assert bool(source.Visibility) is source_visibility[source_name]
            record = _record(result, controls, source_visibility)
            document.undo()
            _process_events()
            assert document.getObject(record["name"]) is None
            assert bool(source.Visibility) is source_visibility[source_name]
            document.redo()
            _process_events()
            result = document.getObject(record["name"])
            assert result is not None
            _assert_signature(_shape_signature(result.Shape), signature)
            PartDesign.validateDesign(result)
            records.append(record)

        body_state = _target(sources["BodyPlane"])
        assert body_state is sources["BodyPlaneSeed"]
        assert records[-2]["face"] == (body_state.Name, ("Face1",))
        placed_signature = records[-3]["signature"]
        assert placed_signature[7] > 80.0 and placed_signature[11] >= 3.0 - 1.0e-7

        failure_cases = (
            (
                _arguments("Missing", _definition(stale_name)),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments("No Face", _definition(sources["EdgeOnly"])),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Unequal Symmetry",
                    _definition(
                        sources["RollbackPlane"],
                        u_negative=0.1,
                        u_positive=0.2,
                    ),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Too Many Samples",
                    _definition(sources["RollbackPlane"], samples_u=513),
                ),
                "NATIVE_ARGUMENTS_INVALID",
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
        assert not PartGui.isModelingObjectActive(sources["InactivePlane"])
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(
            _arguments("Inactive", _definition(sources["InactivePlane"])),
            succeeds=False,
        )
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()

        stale_source = sources["RollbackPlane"]
        stale_arguments = _arguments("Stale", _definition(stale_source))
        stale_spec = prepare_surface_extend(
            str(document.Uid), stale_arguments["definition"]
        )
        stale_prepared = preflight_surface_extend(document, stale_spec)
        names_before = tuple(obj.Name for obj in document.Objects)
        document.openTransaction("Reject stale Surface Extend")
        try:
            stale_source.Shape = _plane(181)
            try:
                create_surface_extend(
                    document,
                    label="Stale",
                    prepared=stale_prepared,
                )
            except NativeModelError:
                pass
            else:
                raise AssertionError("Changed Surface Extend preflight was accepted")
        finally:
            document.abortTransaction()
        assert tuple(obj.Name for obj in document.Objects) == names_before

        rollback_arguments = _arguments(
            "Rollback Surface Extend",
            _definition(sources["RollbackPlane"]),
        )
        rollback_names = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_surface_extend

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced Surface Extend postcondition failure.")

        runtime_module.verify_surface_extend = reject_after_creation
        try:
            rollback = native_call(rollback_arguments, succeeds=False)
        finally:
            runtime_module.verify_surface_extend = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == rollback_names
        assert not document.HasPendingTransaction

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-extend-"))
        save_path = save_directory / "ModelSurfaceExtend.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()
        for record in records:
            result = document.getObject(record["name"])
            assert result is not None and result.TypeId == "Surface::Extend"
            assert result.Label == record["label"]
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            face_target, face_names = _link_sub(result.Face)
            assert (face_target.Name, face_names) == record["face"]
            controls = record["controls"]
            actual_controls = (
                float(result.ExtendUNeg),
                float(result.ExtendUPos),
                bool(result.ExtendUSymetric),
                float(result.ExtendVNeg),
                float(result.ExtendVPos),
                bool(result.ExtendVSymetric),
                float(result.Tolerance),
                int(result.SampleU),
                int(result.SampleV),
            )
            expected_controls = (
                controls["u_negative"],
                controls["u_positive"],
                controls["u_symmetric"],
                controls["v_negative"],
                controls["v_positive"],
                controls["v_symmetric"],
                controls["tolerance"],
                controls["samples_u"],
                controls["samples_v"],
            )
            assert actual_controls == expected_controls, (
                result.Name,
                actual_controls,
                expected_controls,
            )
            _assert_signature(_shape_signature(result.Shape), record["signature"])
            assert {
                name: bool(document.getObject(name).Visibility)
                for name in record["source_visibility"]
            } == record["source_visibility"]
            PartDesign.validateDesign(result)

        print("VIBECAD_NATIVE_MODEL_SURFACE_EXTEND_GUI_OK", flush=True)
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
