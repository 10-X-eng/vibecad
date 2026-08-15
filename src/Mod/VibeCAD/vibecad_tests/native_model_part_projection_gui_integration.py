# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real GUI/provider lifecycle gate for Part Projection on Surface."""

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
from VibeCADNativePartHistory import flatten_link_sub_list, link_sub
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


def _task_button(standard_button):
    _process_events()
    for button_box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        if button_box.isVisible():
            button = button_box.button(standard_button)
            if button is not None and button.isVisible() and button.isEnabled():
                return button
    return None


def _close(left: float, right: float, tolerance: float = 1.0e-7) -> bool:
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
        _close(left, right, 5.0e-3)
        for left, right in zip(actual["bounds"], expected["bounds"], strict=True)
    ), (actual["bounds"], expected["bounds"])
    for field in ("length", "area", "volume"):
        assert _close(actual[field], expected[field], 5.0e-3)


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


def _plane(x: float, y: float, z: float, length=30.0, width=30.0):
    return Part.makePlane(length, width, App.Vector(x, y, z))


def _create_sources(document) -> tuple[dict[str, object], str]:
    placed = App.Placement(
        App.Vector(170, 5, 0),
        App.Rotation(App.Vector(0, 0, 1), 17),
    )
    document.openTransaction("Create Projection on Surface gate sources")
    try:
        sources = {
            "HumanTarget": _publish_source(document, "HumanTarget", _plane(0, 0, 0)),
            "AllTarget": _publish_source(document, "AllTarget", _plane(40, 0, 0)),
            "AllFace": _publish_source(
                document,
                "AllFace",
                _plane(45, 5, 10, 10, 8),
            ),
            "FacesTarget": _publish_source(
                document,
                "FacesTarget",
                _plane(80, 0, 0),
            ),
            "FacesFace": _publish_source(
                document,
                "FacesFace",
                _plane(85, 5, 10, 10, 8),
            ),
            "EdgesTarget": _publish_source(
                document,
                "EdgesTarget",
                _plane(120, 0, 0),
            ),
            "EdgesFace": _publish_source(
                document,
                "EdgesFace",
                _plane(125, 5, 10, 10, 8),
            ),
            "MultiTarget": _publish_source(
                document,
                "MultiTarget",
                _plane(160, 0, 0),
            ),
            "MultiFace": _publish_source(
                document,
                "MultiFace",
                _plane(164, 4, 10, 8, 7),
            ),
            "MultiEdge": _publish_source(
                document,
                "MultiEdge",
                Part.makeLine(App.Vector(174, 18, 10), App.Vector(184, 18, 10)),
            ),
            "PlacedTarget": _publish_source(
                document,
                "PlacedTarget",
                _plane(0, 0, 0),
                placement=placed,
            ),
            "PlacedFace": _publish_source(
                document,
                "PlacedFace",
                _plane(5, 5, 10, 10, 8),
                placement=placed,
            ),
            "RollbackTarget": _publish_source(
                document,
                "RollbackTarget",
                _plane(220, 0, 0),
            ),
            "RollbackFace": _publish_source(
                document,
                "RollbackFace",
                _plane(225, 5, 10, 10, 8),
            ),
            "InactiveTarget": _publish_source(
                document,
                "InactiveTarget",
                _plane(260, 0, 0),
            ),
            "InactiveFace": _publish_source(
                document,
                "InactiveFace",
                _plane(265, 5, 10, 10, 8),
            ),
            "AwayTarget": _publish_source(
                document,
                "AwayTarget",
                _plane(300, 0, 0, 10, 10),
            ),
            "AwayFace": _publish_source(
                document,
                "AwayFace",
                _plane(330, 30, 10, 5, 5),
            ),
            "NullProjectionSource": _publish_source(
                document,
                "NullProjectionSource",
                Part.Shape(),
            ),
        }
        stale = _publish_source(
            document,
            "StaleProjectionSource",
            _plane(350, 0, 10, 4, 4),
        )
        sources["MultiEdge"].Visibility = False
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    document.openTransaction("Delete stale Projection source")
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
            "f" * 64,
            ("Part_ProjectionOnSurface",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("project_surface",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _reference(name: str, subelement: str) -> dict[str, str]:
    return {"object_name": name, "subelement": subelement}


def _definition(
    target: str,
    sources: list[tuple[str, str]],
    mode: str,
    height: float,
    offset: float,
    direction: list[float] | None = None,
):
    return {
        "target": _reference(target, "Face1"),
        "sources": [_reference(name, subelement) for name, subelement in sources],
        "mode": mode,
        "height_mm": height,
        "offset_mm": offset,
        "direction_xyz": direction or [0.0, 0.0, -1.0],
    }


def _arguments(label, definition):
    return {"operation": "project_surface", "label": label, "definition": definition}


def _expected_shape(document, sources, definition):
    document.openTransaction("Probe exact Projection on Surface feature")
    try:
        result = document.addObject("Part::ProjectOnSurface", "ProjectionOracle")
        target = sources[definition["target"]["object_name"]]
        result.SupportFace = (target, [definition["target"]["subelement"]])
        result.Projection = [
            (sources[item["object_name"]], [item["subelement"]])
            for item in definition["sources"]
        ]
        result.Mode = {"all": "All", "faces": "Faces", "edges": "Edges"}[
            definition["mode"]
        ]
        result.Height = definition["height_mm"]
        result.Offset = definition["offset_mm"]
        direction = App.Vector(*definition["direction_xyz"])
        direction.normalize()
        result.Direction = direction
        assert document.recompute([result], True, True) is not False
        assert result.isValid() and not result.Shape.isNull() and result.Shape.isValid()
        return result.Shape.copy()
    finally:
        document.abortTransaction()


def _assert_human_contract(document, sources) -> None:
    before = tuple(obj.Name for obj in document.Objects)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(sources["HumanTarget"], "Face1")
    _process_events()
    assert Gui.isCommandActive("Part_ProjectionOnSurface")
    Gui.runCommand("Part_ProjectionOnSurface", 0)
    _process_events(32)
    assert Gui.Control.activeDialog()
    created = [obj for obj in document.Objects if obj.Name not in before]
    assert len(created) == 1 and created[0].TypeId == "Part::ProjectOnSurface"
    provisional = created[0]
    assert provisional.SupportFace is None and not provisional.Projection
    assert str(provisional.Mode) == "All"
    assert _close(provisional.Height, 0.0) and _close(provisional.Offset, 0.0)
    assert tuple(float(value) for value in provisional.Direction) == (0.0, 0.0, 1.0)

    window = Gui.getMainWindow()
    names = (
        "pushButtonAddProjFace",
        "pushButtonAddFace",
        "pushButtonAddWire",
        "pushButtonAddEdge",
    )
    buttons = [window.findChild(QtWidgets.QPushButton, name) for name in names]
    assert all(button is not None and button.isCheckable() for button in buttons)
    assert [button.text() for button in buttons] == [
        "Select Projection Surface",
        "Add Face",
        "Add Wire",
        "Add Edge",
    ]
    show_all = window.findChild(QtWidgets.QRadioButton, "radioButtonShowAll")
    show_faces = window.findChild(QtWidgets.QRadioButton, "radioButtonFaces")
    show_edges = window.findChild(QtWidgets.QRadioButton, "radioButtonEdges")
    height = window.findChild(QtWidgets.QDoubleSpinBox, "doubleSpinBoxExtrudeHeight")
    offset = window.findChild(QtWidgets.QDoubleSpinBox, "doubleSpinBoxSolidDepth")
    directions = [
        window.findChild(QtWidgets.QDoubleSpinBox, f"doubleSpinBoxDir{axis}")
        for axis in "XYZ"
    ]
    assert all(widget is not None for widget in (show_all, show_faces, show_edges))
    assert show_all.isChecked() and not show_faces.isChecked() and not show_edges.isChecked()
    assert height is not None and (height.minimum(), height.maximum(), height.value()) == (
        0.0,
        999.0,
        0.0,
    )
    assert offset is not None and (offset.minimum(), offset.maximum(), offset.value()) == (
        -999.0,
        999.0,
        0.0,
    )
    assert all(spin is not None and not spin.isEnabled() for spin in directions)
    assert tuple(spin.value() for spin in directions) == (0.0, 0.0, 1.0)
    cancel = _task_button(QtWidgets.QDialogButtonBox.Cancel)
    assert cancel is not None
    cancel.click()
    _process_events(32)
    assert not Gui.Control.activeDialog()
    assert tuple(obj.Name for obj in document.Objects) == before
    assert sources["HumanTarget"].Visibility
    Gui.Selection.clearSelection()


def _assert_exact_preflight_rejects_change(document, sources) -> None:
    definition = _definition(
        "RollbackTarget",
        [("RollbackFace", "Face1")],
        "all",
        3.0,
        0.0,
    )
    spec = runtime_module.prepare_part_projection(str(document.Uid), definition)
    prepared = runtime_module.preflight_part_projection(document, spec)
    source = sources["RollbackFace"]
    original = source.Placement
    before = tuple(obj.Name for obj in document.Objects)
    rejected = False
    try:
        moved = App.Placement(original)
        moved.Base.x += 1.0
        source.Placement = moved
        try:
            runtime_module.create_part_projection(
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
        (
            "Gate All Solid Projection",
            _definition("AllTarget", [("AllFace", "Face1")], "all", 5.0, 2.0, [0, 0, -0.5]),
        ),
        (
            "Gate Faces Projection",
            _definition("FacesTarget", [("FacesFace", "Face1")], "faces", 8.0, -1.5),
        ),
        (
            "Gate Edges Projection",
            _definition("EdgesTarget", [("EdgesFace", "Face1")], "edges", 4.0, 0.0),
        ),
        (
            "Gate Multiple Projection Sources",
            _definition(
                "MultiTarget",
                [("MultiFace", "Face1"), ("MultiEdge", "Edge1")],
                "all",
                0.0,
                0.0,
            ),
        ),
        (
            "Gate Placed Projection",
            _definition("PlacedTarget", [("PlacedFace", "Face1")], "all", 2.5, 0.5),
        ),
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartProjectionGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_human_contract(document, sources)
        _assert_exact_preflight_rejects_change(document, sources)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-projection-gui")
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
                "model.part",
                json.dumps(arguments, separators=(",", ":")),
                f"model-part-projection-call-{call_number}",
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
                "operation": "project_surface",
                "label": "Incomplete Projection",
                "definition": {"target": _reference("AllTarget", "Face1")},
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
        for label, definition in _cases():
            expected = _shape_signature(_expected_shape(document, sources, definition))
            input_names = {
                definition["target"]["object_name"],
                *(item["object_name"] for item in definition["sources"]),
            }
            input_state = {
                name: (
                    _shape_signature(Part.getShape(sources[name], transform=True)),
                    _placement_signature(sources[name]),
                    bool(sources[name].Visibility),
                )
                for name in input_names
            }
            response = native_call(_arguments(label, definition))
            assert set(response) == expected_fields
            assert response["source_count"] == len(definition["sources"])
            assert response["shape_type"] == expected["shape_type"]
            assert response["solid_count"] == expected["topology"][4]
            assert response["face_count"] == expected["topology"][3]
            assert response["edge_count"] == expected["topology"][1]
            assert _close(response["area_mm2"], expected["area"], 5.0e-3), (
                label,
                response["area_mm2"],
                expected["area"],
            )
            assert _close(response["volume_mm3"], expected["volume"], 5.0e-3), (
                label,
                response["volume_mm3"],
                expected["volume"],
            )
            result = document.getObject(response["root"]["object_name"])
            target = sources[definition["target"]["object_name"]]
            expected_sources = tuple(
                (sources[item["object_name"]], (item["subelement"],))
                for item in definition["sources"]
            )
            assert result.TypeId == "Part::ProjectOnSurface"
            assert link_sub(result.SupportFace) == (target, ("Face1",))
            assert flatten_link_sub_list(result.Projection) == expected_sources
            assert str(result.Mode) == {
                "all": "All",
                "faces": "Faces",
                "edges": "Edges",
            }[definition["mode"]]
            assert _close(result.Height, definition["height_mm"])
            assert _close(result.Offset, definition["offset_mm"])
            direction = App.Vector(*definition["direction_xyz"])
            direction.normalize()
            assert all(
                _close(left, right)
                for left, right in zip(result.Direction, direction, strict=True)
            )
            assert result.VibeCADTimelineRole == "operation"
            assert "VibeCADTimelineReplacedInputs" not in result.PropertiesList
            assert str(result.VibeCADDefinitionId) and str(result.DesignId)
            _assert_shape_signature(result.Shape, expected)
            for _repeat in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_shape_signature(result.Shape, expected)
            for name, (shape, placement, visible) in input_state.items():
                _assert_shape_signature(Part.getShape(sources[name], transform=True), shape)
                assert _placement_signature(sources[name]) == placement
                assert bool(sources[name].Visibility) is visible
            record = {
                "result": result.Name,
                "target": target.Name,
                "source_names": tuple(item[0].Name for item in expected_sources),
                "source_subs": tuple(item[1][0] for item in expected_sources),
                "mode": str(result.Mode),
                "height": float(result.Height),
                "offset": float(result.Offset),
                "direction": tuple(float(value) for value in result.Direction),
                "definition_id": str(result.VibeCADDefinitionId),
                "design_id": str(result.DesignId),
                "signature": expected,
            }
            document.undo()
            _process_events()
            assert document.getObject(record["result"]) is None
            for name, (_shape, _placement, visible) in input_state.items():
                assert bool(sources[name].Visibility) is visible
            document.redo()
            _process_events()
            assert document.getObject(record["result"]) is not None
            records.append(record)

        invalid_definitions = (
            (
                _definition("AllTarget", [(stale_name, "Face1")], "all", 1, 0),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _definition("AllTarget", [("NullProjectionSource", "Edge1")], "all", 1, 0),
                "NATIVE_MODEL_INVALID",
            ),
            (
                {
                    **_definition("AllTarget", [("AllFace", "Face1")], "all", 1, 0),
                    "target": _reference("AllTarget", "Face99"),
                },
                "NATIVE_MODEL_INVALID",
            ),
            (
                _definition("AllTarget", [("AllFace", "Vertex1")], "all", 1, 0),
                "NATIVE_ARGUMENTS_INVALID",
            ),
            (
                _definition(
                    "AllTarget",
                    [("AllFace", "Face1")],
                    "all",
                    1,
                    0,
                    [0, 0, 0],
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _definition("AwayTarget", [("AwayFace", "Face1")], "all", 1, 0),
                "NATIVE_MODEL_INVALID",
            ),
        )
        for definition, error_code in invalid_definitions:
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(
                _arguments("Invalid Projection", definition),
                succeeds=False,
            )
            assert response["error_code"] == error_code, (definition, response)
            assert tuple(obj.Name for obj in document.Objects) == before
            assert document.HasPendingTransaction is False

        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        timeline.Position = 0
        _process_events()
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(
            _arguments(
                "Inactive Projection",
                _definition(
                    "InactiveTarget",
                    [("InactiveFace", "Face1")],
                    "all",
                    1,
                    0,
                ),
            ),
            succeeds=False,
        )
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()

        before = tuple(obj.Name for obj in document.Objects)
        input_visibility = {
            name: bool(sources[name].Visibility)
            for name in ("RollbackTarget", "RollbackFace")
        }
        original_verify = runtime_module.verify_part_projection

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced Projection postcondition failure.")

        runtime_module.verify_part_projection = reject_after_creation
        try:
            rollback = native_call(
                _arguments(
                    "Rollback Projection",
                    _definition(
                        "RollbackTarget",
                        [("RollbackFace", "Face1")],
                        "all",
                        2,
                        0,
                    ),
                ),
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_projection = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert all(
            bool(sources[name].Visibility) is visible
            for name, visible in input_visibility.items()
        )

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-projection-"))
        save_path = save_directory / "ModelPartProjection.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()
        for record in records:
            result = document.getObject(record["result"])
            target = document.getObject(record["target"])
            assert link_sub(result.SupportFace) == (target, ("Face1",))
            assert flatten_link_sub_list(result.Projection) == tuple(
                (
                    document.getObject(name),
                    (subelement,),
                )
                for name, subelement in zip(
                    record["source_names"],
                    record["source_subs"],
                    strict=True,
                )
            )
            assert str(result.Mode) == record["mode"]
            assert _close(result.Height, record["height"])
            assert _close(result.Offset, record["offset"])
            assert all(
                _close(left, right)
                for left, right in zip(
                    result.Direction,
                    record["direction"],
                    strict=True,
                )
            )
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            assert "VibeCADTimelineReplacedInputs" not in result.PropertiesList
            _assert_shape_signature(result.Shape, record["signature"])

        print("VIBECAD_NATIVE_MODEL_PART_PROJECTION_GUI_OK", flush=True)
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
