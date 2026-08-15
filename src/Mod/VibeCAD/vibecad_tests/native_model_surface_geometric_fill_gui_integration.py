# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real VibeCAD GUI and provider lifecycle gate for Geometric Fill Surface."""

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
from VibeCADNativeSurfaceGeomFill import (
    create_surface_geometric_fill,
    preflight_surface_geometric_fill,
    prepare_surface_geometric_fill,
)
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger


_STYLE_NAME = {"stretched": "Stretched", "coons": "Coons", "curved": "Curved"}


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


def _bezier(points):
    curve = Part.BezierCurve()
    curve.setPoles([App.Vector(*point) for point in points])
    return curve.toShape()


def _quad_wire(x: float, y: float = 0.0, z: float = 0.0, size: float = 10.0):
    return Part.makePolygon(
        [
            App.Vector(x, y, z),
            App.Vector(x + size, y, z),
            App.Vector(x + size, y + size, z),
            App.Vector(x, y + size, z),
            App.Vector(x, y, z),
        ]
    )


def _triangle_wire(x: float, y: float = 0.0, z: float = 0.0):
    return Part.makePolygon(
        [
            App.Vector(x, y, z),
            App.Vector(x + 10, y, z),
            App.Vector(x + 5, y + 9, z + 1),
            App.Vector(x, y, z),
        ]
    )


def _two_curves(x: float):
    return Part.makeCompound(
        [
            _bezier(((x, 0, 0), (x + 5, -1, 1), (x + 10, 0, 0))),
            _bezier(((x, 8, 0), (x + 5, 9, 2), (x + 10, 8, 0))),
        ]
    )


def _bezier_quad_wire(x: float):
    return Part.Wire(
        [
            _bezier(((x, 0, 0), (x + 5, -1, 1), (x + 10, 0, 0))),
            _bezier(((x + 10, 0, 0), (x + 11, 5, 2), (x + 10, 10, 0))),
            _bezier(((x + 10, 10, 0), (x + 5, 11, 1), (x, 10, 0))),
            _bezier(((x, 10, 0), (x - 1, 5, 2), (x, 0, 0))),
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
    document.openTransaction("Create Geometric Fill Surface gate sources")
    try:
        sources = {
            "HumanBoundary": _publish_source(document, "HumanBoundary", _quad_wire(0)),
            "TwoBoundary": _publish_source(document, "TwoBoundary", _two_curves(20)),
            "ThreeBoundary": _publish_source(
                document, "ThreeBoundary", _triangle_wire(40)
            ),
            "FourBoundary": _publish_source(document, "FourBoundary", _quad_wire(60)),
            "PlacedBoundary": _publish_source(
                document,
                "PlacedBoundary",
                _quad_wire(0),
                placement=App.Placement(
                    App.Vector(90, -4, 3),
                    App.Rotation(App.Vector(0, 0, 1), 21),
                ),
            ),
            "HiddenBoundary": _publish_source(
                document, "HiddenBoundary", _quad_wire(120), visible=False
            ),
            "InactiveBoundary": _publish_source(
                document, "InactiveBoundary", _quad_wire(140)
            ),
            "RollbackBoundary": _publish_source(
                document, "RollbackBoundary", _quad_wire(160)
            ),
            "VertexOnly": _publish_source(
                document, "VertexOnly", Part.Vertex(App.Vector(180, 0, 0))
            ),
        }
        stale = _publish_source(document, "StaleGeomBoundary", _quad_wire(200))
        body, seed = _body_source(
            document,
            "BodyGeomBoundary",
            _bezier_quad_wire(220),
        )
        sources["BodyGeomBoundary"] = body
        sources["BodyGeomBoundarySeed"] = seed
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise

    document.openTransaction("Delete stale Geometric Fill source")
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
            "g" * 64,
            ("Surface_GeomFillSurface",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("geom_fill_surface",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _boundary(source, edge: str, reversed_value: bool = False):
    value = {
        "object_name": source.Name if hasattr(source, "Name") else str(source),
        "edge": edge,
    }
    if reversed_value:
        value["reversed"] = True
    return value


def _boundaries(source, count: int, reversed_indices=()):
    return tuple(
        _boundary(source, f"Edge{index}", index in reversed_indices)
        for index in range(1, count + 1)
    )


def _arguments(label: str, boundaries, *, style=None):
    definition = {"boundaries": list(boundaries)}
    if style is not None:
        definition["style"] = style
    return {
        "operation": "geom_fill_surface",
        "label": label,
        "definition": definition,
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
    assert Gui.isCommandActive("Surface_GeomFillSurface")
    Gui.runCommand("Surface_GeomFillSurface", 0)
    _process_events(32)
    assert Gui.Control.activeDialog()
    window = Gui.getMainWindow()
    boundary_list = window.findChild(QtWidgets.QListWidget, "listWidget")
    stretched = window.findChild(QtWidgets.QRadioButton, "fillType_stretch")
    coons = window.findChild(QtWidgets.QRadioButton, "fillType_coons")
    curved = window.findChild(QtWidgets.QRadioButton, "fillType_curved")
    add_edge = window.findChild(QtWidgets.QToolButton, "buttonEdgeAdd")
    remove_edge = window.findChild(QtWidgets.QToolButton, "buttonEdgeRemove")
    assert all(
        widget is not None
        for widget in (boundary_list, stretched, coons, curved, add_edge, remove_edge)
    )
    assert (stretched.text(), coons.text(), curved.text()) == (
        "Stretch",
        "Coons",
        "Curved",
    )
    assert stretched.isChecked() and add_edge.isChecked()
    for index in range(1, 5):
        Gui.Selection.addSelection(source, f"Edge{index}")
        _process_events(10)
    assert boundary_list.count() == 4
    second = boundary_list.item(1)
    boundary_list.setCurrentItem(second)
    boundary_list.itemDoubleClicked.emit(second)
    curved.click()
    _process_events(16)
    created = [obj for obj in document.Objects if obj.Name not in before]
    assert len(created) == 1 and created[0].TypeId == "Surface::GeomFillSurface"
    human_result = created[0]
    _finish_task(QtWidgets.QDialogButtonBox.Ok)
    assert human_result.isValid() and human_result.Shape.ShapeType == "Face"
    assert _flatten_links(human_result.BoundaryList) == tuple(
        (source, (f"Edge{index}",)) for index in range(1, 5)
    )
    actual_reversed = tuple(bool(value) for value in human_result.ReversedList)
    assert actual_reversed == (
        False,
        True,
        False,
        False,
    ), actual_reversed
    assert str(human_result.FillType) == "Curved"
    assert source.Visibility
    human_name = human_result.Name
    document.undo()
    _process_events()
    assert document.getObject(human_name) is None and source.Visibility

    before = tuple(obj.Name for obj in document.Objects)
    Gui.runCommand("Surface_GeomFillSurface", 0)
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
        "boundary_count",
        "style",
        "reversed_count",
        "edge_count",
        "area_mm2",
        "receipt",
        "assistant_undo_available",
    }
    boundaries = tuple(arguments["definition"]["boundaries"])
    style = arguments["definition"].get("style", "stretched")
    result = document.getObject(response["root"]["object_name"])
    assert result is not None and result.TypeId == "Surface::GeomFillSurface"
    assert result.Label == arguments["label"]
    assert result.getParentGeoFeatureGroup() is None
    assert result.isValid() and result.Shape.isValid()
    assert result.Shape.ShapeType == "Face" and len(result.Shape.Faces) == 1
    assert result.VibeCADTimelineRole == "operation"
    assert getattr(result, "VibeCADTimelineOwner", None) is None
    assert str(result.VibeCADDefinitionId) and str(result.DesignId)
    expected_links = tuple(
        (
            _target(sources[boundary["object_name"]]),
            (boundary["edge"],),
        )
        for boundary in boundaries
    )
    reversed_values = tuple(bool(boundary.get("reversed", False)) for boundary in boundaries)
    assert _flatten_links(result.BoundaryList) == expected_links
    assert tuple(bool(value) for value in result.ReversedList) == reversed_values
    assert str(result.FillType) == _STYLE_NAME[style]
    assert response["boundary_count"] == len(boundaries)
    assert response["style"] == style
    assert response["reversed_count"] == sum(reversed_values)
    assert response["edge_count"] == len(result.Shape.Edges)
    assert _close(response["area_mm2"], result.Shape.Area)
    assert response["assistant_undo_available"] is True
    assert [item["object_name"] for item in response["receipt"]["created"]] == [result.Name]
    assert response["receipt"]["changed"] == []
    assert response["receipt"]["deleted"] == []
    assert response["receipt"]["replaced"] == []
    PartDesign.validateDesign(result)
    return result, reversed_values, style


def _record(result, reversed_values, style, source_visibility):
    return {
        "name": result.Name,
        "label": str(result.Label),
        "definition_id": str(result.VibeCADDefinitionId),
        "design_id": str(result.DesignId),
        "boundaries": tuple(
            (target.Name, names) for target, names in _flatten_links(result.BoundaryList)
        ),
        "reversed": reversed_values,
        "style": style,
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
        document = App.newDocument("NativeModelSurfaceGeomFillGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_human_contract(document, sources["HumanBoundary"])

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-surface-geometric-fill-gui")
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
                f"model-surface-geometric-fill-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments,
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        cases = (
            _arguments(
                "Two Edge Stretched",
                _boundaries(sources["TwoBoundary"], 2),
            ),
            _arguments(
                "Three Edge Curved",
                _boundaries(sources["ThreeBoundary"], 3),
                style="curved",
            ),
            _arguments(
                "Four Edge Curved",
                _boundaries(sources["FourBoundary"], 4, (2, 4)),
                style="curved",
            ),
            _arguments(
                "Placed Geometric Fill",
                _boundaries(sources["PlacedBoundary"], 4),
                style="curved",
            ),
            _arguments(
                "Body Geometric Fill",
                _boundaries(sources["BodyGeomBoundary"], 4),
                style="coons",
            ),
            _arguments(
                "Hidden Input Geometric Fill",
                _boundaries(sources["HiddenBoundary"], 4),
            ),
        )
        records = []
        for arguments in cases:
            source_names = tuple(
                dict.fromkeys(
                    boundary["object_name"]
                    for boundary in arguments["definition"]["boundaries"]
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
            result, reversed_values, style = _assert_result(
                document, response, arguments, sources
            )
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
            record = _record(result, reversed_values, style, source_visibility)
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

        body_state = _target(sources["BodyGeomBoundary"])
        assert body_state is sources["BodyGeomBoundarySeed"]
        assert all(name == body_state.Name for name, _names in records[-2]["boundaries"])
        placed_signature = records[-3]["signature"]
        assert placed_signature[7] > 80.0 and placed_signature[11] >= 3.0 - 1.0e-7

        failure_cases = (
            (
                _arguments(
                    "Too Few",
                    (_boundary(sources["RollbackBoundary"], "Edge1"),),
                ),
                "NATIVE_ARGUMENTS_INVALID",
            ),
            (
                _arguments(
                    "Missing",
                    (
                        _boundary(stale_name, "Edge1"),
                        _boundary(stale_name, "Edge2"),
                    ),
                ),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments(
                    "No Edge",
                    (
                        _boundary(sources["VertexOnly"], "Edge1"),
                        _boundary(sources["RollbackBoundary"], "Edge1"),
                    ),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Duplicate Resolved",
                    (
                        _boundary(sources["BodyGeomBoundary"], "Edge1"),
                        _boundary(sources["BodyGeomBoundarySeed"], "Edge1"),
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
        assert not PartGui.isModelingObjectActive(sources["InactiveBoundary"])
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(
            _arguments(
                "Inactive",
                _boundaries(sources["InactiveBoundary"], 4),
            ),
            succeeds=False,
        )
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()

        stale_source = sources["RollbackBoundary"]
        stale_arguments = _arguments("Stale", _boundaries(stale_source, 4))
        stale_spec = prepare_surface_geometric_fill(
            str(document.Uid), stale_arguments["definition"]
        )
        stale_prepared = preflight_surface_geometric_fill(document, stale_spec)
        names_before = tuple(obj.Name for obj in document.Objects)
        document.openTransaction("Reject stale Geometric Fill Surface")
        try:
            stale_source.Shape = _quad_wire(161)
            try:
                create_surface_geometric_fill(
                    document,
                    label="Stale",
                    prepared=stale_prepared,
                )
            except NativeModelError:
                pass
            else:
                raise AssertionError("Changed Geometric Fill preflight was accepted")
        finally:
            document.abortTransaction()
        assert tuple(obj.Name for obj in document.Objects) == names_before

        rollback_arguments = _arguments(
            "Rollback Geometric Fill",
            _boundaries(sources["RollbackBoundary"], 4),
        )
        rollback_names = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_surface_geometric_fill

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced Geometric Fill postcondition failure.")

        runtime_module.verify_surface_geometric_fill = reject_after_creation
        try:
            rollback = native_call(rollback_arguments, succeeds=False)
        finally:
            runtime_module.verify_surface_geometric_fill = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == rollback_names
        assert not document.HasPendingTransaction

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-geometric-fill-"))
        save_path = save_directory / "ModelSurfaceGeometricFill.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()
        for record in records:
            result = document.getObject(record["name"])
            assert result is not None and result.TypeId == "Surface::GeomFillSurface"
            assert result.Label == record["label"]
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            assert tuple(
                (target.Name, names) for target, names in _flatten_links(result.BoundaryList)
            ) == record["boundaries"]
            assert tuple(bool(value) for value in result.ReversedList) == record["reversed"]
            assert str(result.FillType) == _STYLE_NAME[record["style"]]
            _assert_signature(_shape_signature(result.Shape), record["signature"])
            assert {
                name: bool(document.getObject(name).Visibility)
                for name in record["source_visibility"]
            } == record["source_visibility"]
            PartDesign.validateDesign(result)

        print("VIBECAD_NATIVE_MODEL_SURFACE_GEOMETRIC_FILL_GUI_OK", flush=True)
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
