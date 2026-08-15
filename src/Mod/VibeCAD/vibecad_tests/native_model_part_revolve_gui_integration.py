# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for Native standalone Part Revolve."""

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
import VibeCADNativeModelPartRuntime as runtime_module
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelErrors import NativeModelError
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


def _close(left: float, right: float, tolerance: float = 1.0e-7) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def _shape_signature(shape) -> dict[str, object]:
    bounds = shape.BoundBox
    return {
        "shape_type": str(shape.ShapeType),
        "topology": (len(shape.Vertexes), len(shape.Edges), len(shape.Faces)),
        "bounds": tuple(
            float(getattr(bounds, name))
            for name in ("XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax")
        ),
        "measures": (float(shape.Length), float(shape.Area), float(shape.Volume)),
    }


def _assert_shape_signature(shape, expected: dict[str, object]) -> None:
    actual = _shape_signature(shape)
    assert actual["shape_type"] == expected["shape_type"]
    assert actual["topology"] == expected["topology"]
    for field in ("bounds", "measures"):
        assert all(
            _close(left, right)
            for left, right in zip(actual[field], expected[field], strict=True)
        ), (field, actual[field], expected[field])


def _turn() -> NativeTurnSnapshot:
    definition = model_part_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "r" * 64,
            ("Part_Revolve",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("revolve",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _publish_source(document, name: str, shape):
    obj = document.addObject("Part::Feature", name)
    obj.Label = name
    obj.Shape = shape
    PartDesign.initializeDesignDefinition(obj)
    document.publishProvisionalTimelineOperationBlock(obj, (), ())
    assert document.recompute([obj], True, True) is not False
    PartDesign.finalizeDesignDefinition(obj)
    assert PartGui.isModelingObjectActive(obj)
    return obj


def _profile(radius: float, z: float, width: float = 4.0, height: float = 3.0):
    return Part.makePolygon(
        [
            App.Vector(radius, 0, z),
            App.Vector(radius + width, 0, z),
            App.Vector(radius + width, 0, z + height),
            App.Vector(radius, 0, z + height),
        ],
        True,
    )


def _create_sources(document) -> tuple[dict[str, object], str]:
    document.openTransaction("Create Part Revolve gate sources")
    try:
        sources = {
            "CustomProfile": _publish_source(
                document,
                "CustomProfile",
                _profile(2, 0),
            ),
            "SymmetricProfile": _publish_source(
                document,
                "SymmetricProfile",
                _profile(10, 0),
            ),
            "LineAxisProfile": _publish_source(
                document,
                "LineAxisProfile",
                _profile(20, 0),
            ),
            "ArcAxisProfile": _publish_source(
                document,
                "ArcAxisProfile",
                _profile(32, 0),
            ),
            "WholeAxisProfile": _publish_source(
                document,
                "WholeAxisProfile",
                _profile(42, 0),
            ),
            "MultiProfileA": _publish_source(
                document,
                "MultiProfileA",
                _profile(50, 0),
            ),
            "MultiProfileB": _publish_source(
                document,
                "MultiProfileB",
                _profile(56, 0),
            ),
            "RollbackProfile": _publish_source(
                document,
                "RollbackProfile",
                _profile(64, 0),
            ),
            "SolidSource": _publish_source(
                document,
                "SolidSource",
                Part.makeBox(4, 4, 4, App.Vector(72, 0, 0)),
            ),
            "DirectionLine": _publish_source(
                document,
                "DirectionLine",
                Part.makeLine(App.Vector(18, 0, -2), App.Vector(18, 0, 6)),
            ),
            "DirectionArc": _publish_source(
                document,
                "DirectionArc",
                Part.makeCircle(
                    3,
                    App.Vector(30, 0, 0),
                    App.Vector(0, 0, 1),
                    0,
                    120,
                ),
            ),
            "WholeAxis": _publish_source(
                document,
                "WholeAxis",
                Part.makeLine(App.Vector(40, 0, -2), App.Vector(40, 0, 6)),
            ),
            "UnsupportedAxis": _publish_source(
                document,
                "UnsupportedAxis",
                Part.makeHelix(2, 5, 1),
            ),
        }
        stale = _publish_source(document, "TransientProfile", _profile(80, 0))
        stale_name = stale.Name
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    document.openTransaction("Delete Part Revolve stale source")
    try:
        document.removeObject(stale_name)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert document.getObject(stale_name) is None
    return sources, stale_name


def _custom_axis(
    *,
    base: tuple[float, float, float] = (0.0, 0.0, 0.0),
    direction: tuple[float, float, float] = (0.0, 0.0, 1.0),
) -> dict[str, object]:
    return {
        "kind": "custom",
        "base_mm": dict(zip(("x", "y", "z"), base, strict=True)),
        "direction": dict(zip(("x", "y", "z"), direction, strict=True)),
    }


def _edge_axis(name: str, subelement: str | None = "Edge1") -> dict[str, object]:
    reference = {"object_name": name}
    if subelement is not None:
        reference["subelement"] = subelement
    return {"kind": "edge", "reference": reference}


def _definition(
    sources: tuple[str, ...],
    *,
    axis: dict[str, object] | None = None,
    angle: float = 180.0,
    symmetric: bool = False,
    solid: bool = True,
) -> dict[str, object]:
    return {
        "sources": [{"object_name": name} for name in sources],
        "axis": axis or _custom_axis(),
        "angle_degrees": angle,
        "symmetric": symmetric,
        "solid": solid,
    }


def _cases() -> tuple[dict[str, object], ...]:
    return (
        {
            "label": "Gate Signed Revolve",
            "definition": _definition(("CustomProfile",), angle=-90.0),
        },
        {
            "label": "Gate Symmetric Revolve",
            "definition": _definition(
                ("SymmetricProfile",),
                angle=120.0,
                symmetric=True,
            ),
        },
        {
            "label": "Gate Line Axis Revolve",
            "definition": _definition(
                ("LineAxisProfile",),
                axis=_edge_axis("DirectionLine"),
                angle=90.0,
            ),
        },
        {
            "label": "Gate Arc Span Revolve",
            "definition": _definition(
                ("ArcAxisProfile",),
                axis=_edge_axis("DirectionArc"),
                angle=0.0,
                solid=False,
            ),
        },
        {
            "label": "Gate Whole Edge Revolve",
            "definition": _definition(
                ("WholeAxisProfile",),
                axis=_edge_axis("WholeAxis", None),
                angle=75.0,
            ),
        },
        {
            "label": "Gate Multi Revolve",
            "definition": _definition(
                ("MultiProfileA", "MultiProfileB"),
                angle=45.0,
            ),
        },
    )


def _widget(window, widget_type, name: str):
    widget = window.findChild(widget_type, name)
    assert widget is not None, name
    return widget


def _raw_value(window, name: str) -> float:
    return float(_widget(window, QtWidgets.QAbstractSpinBox, name).property("rawValue"))


def _assert_live_human_contract(document, source, sources) -> None:
    before = tuple(obj.Name for obj in document.Objects)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(source)
    Gui.runCommand("Part_Revolve", 0)
    _process_events(32)
    window = Gui.getMainWindow()
    group = _widget(window, QtWidgets.QGroupBox, "groupBox")
    assert group.title() == "Revolution Axis"
    assert tuple(
        _widget(window, QtWidgets.QPushButton, name).text()
        for name in ("btnX", "btnY", "btnZ")
    ) == ("X-Direction", "Y-Direction", "Z-Direction")
    select = _widget(window, QtWidgets.QPushButton, "selectLine")
    symmetric = _widget(window, QtWidgets.QCheckBox, "checkSymmetric")
    solid = _widget(window, QtWidgets.QCheckBox, "checkSolid")
    assert (select.text(), symmetric.text(), solid.text()) == (
        "Select Reference",
        "Symmetric angle",
        "Create solid",
    )
    assert tuple(_raw_value(window, name) for name in ("xPos", "yPos", "zPos")) == (
        0.0,
        0.0,
        0.0,
    )
    assert tuple(_raw_value(window, name) for name in ("xDir", "yDir", "zDir")) == (
        0.0,
        0.0,
        1.0,
    )
    assert _close(_raw_value(window, "angle"), 360.0)
    assert symmetric.isChecked() is False
    assert solid.isChecked() is True
    tree = _widget(window, QtWidgets.QTreeWidget, "treeWidget")
    selected = tree.selectedItems()
    assert len(selected) == 1 and selected[0].text(0) == source.Label

    axis_link = _widget(window, QtWidgets.QLineEdit, "txtAxisLink")
    axis_controls = tuple(
        _widget(window, QtWidgets.QAbstractSpinBox, name)
        for name in ("xPos", "yPos", "zPos", "xDir", "yDir", "zDir")
    )
    axis_link.setText(f"{sources['DirectionLine'].Name}:Edge1")
    _process_events(8)
    assert not any(control.isEnabled() for control in axis_controls)
    assert _close(_raw_value(window, "angle"), 360.0)
    axis_link.setText(f"{sources['DirectionArc'].Name}:Edge1")
    _process_events(8)
    assert not any(control.isEnabled() for control in axis_controls)
    assert _close(_raw_value(window, "angle"), 0.0)
    axis_link.clear()
    _process_events(8)
    assert all(control.isEnabled() for control in axis_controls)
    assert _close(_raw_value(window, "angle"), 0.0)

    Gui.Control.closeDialog()
    _process_events(16)
    Gui.Selection.clearSelection()
    assert tuple(obj.Name for obj in document.Objects) == before


def _result_objects(document, response) -> tuple[object, ...]:
    names = tuple(item["object_name"] for item in response["receipt"]["created"])
    results = tuple(document.getObject(name) for name in names)
    assert all(result is not None for result in results)
    return results


def _link_sub(value) -> tuple[object | None, tuple[str, ...]]:
    if not value:
        return None, ()
    target, names = value if isinstance(value, tuple) else (value, ())
    if isinstance(names, str):
        names = (names,) if names else ()
    return target, tuple(str(name) for name in names)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartRevolveGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_live_human_contract(document, sources["CustomProfile"], sources)
        source_signatures = {
            name: _shape_signature(obj.Shape) for name, obj in sources.items()
        }

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-revolve-gui")
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
                f"model-part-revolve-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments.get("label"),
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        before = tuple(obj.Name for obj in document.Objects)
        invalid_schema = native_call(
            {"operation": "revolve", "definition": _cases()[0]["definition"]},
            succeeds=False,
        )
        assert invalid_schema["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        records = []
        expected_response_fields = {
            "ok",
            "root",
            "source_count",
            "result_count",
            "resource_count",
            "axis_mode",
            "angle_degrees",
            "symmetric",
            "solid",
            "shape_types",
            "total_length_mm",
            "total_area_mm2",
            "total_volume_mm3",
            "receipt",
            "assistant_undo_available",
        }
        for case in _cases():
            definition = case["definition"]
            source_names = tuple(item["object_name"] for item in definition["sources"])
            assert all(bool(sources[name].Visibility) for name in source_names)
            response = native_call({"operation": "revolve", **case})
            assert set(response) == expected_response_fields
            assert response["source_count"] == len(source_names)
            assert response["result_count"] == len(source_names)
            assert response["resource_count"] == len(source_names) - 1
            assert response["axis_mode"] == definition["axis"]["kind"]
            assert _close(response["angle_degrees"], definition["angle_degrees"])
            assert response["symmetric"] is definition["symmetric"]
            assert response["solid"] is definition["solid"]
            assert response["assistant_undo_available"] is True
            assert len(response["receipt"]["created"]) == len(source_names)
            assert response["receipt"]["changed"] == []
            assert response["receipt"]["deleted"] == []
            results = _result_objects(document, response)
            root = document.getObject(response["root"]["object_name"])
            assert root is results[-1]
            assert root.VibeCADTimelineRole == "operation"
            assert str(root.VibeCADDefinitionId) and str(root.DesignId)
            assert root.getParentGeoFeatureGroup() is None
            assert tuple(root.VibeCADTimelineReplacedInputs) == tuple(
                sources[name] for name in source_names
            )
            assert all(not bool(sources[name].Visibility) for name in source_names)
            for index, result in enumerate(results):
                assert result.TypeId == "Part::Revolution"
                assert result.Label == (
                    case["label"] if result is root else f"{case['label']} — output {index + 1}"
                )
                assert result.Source is sources[source_names[index]]
                assert result.getParentGeoFeatureGroup() is None
                if result is root:
                    assert getattr(result, "VibeCADTimelineOwner", None) is None
                else:
                    assert result.VibeCADTimelineRole == "resource"
                    assert result.VibeCADTimelineOwner is root
                assert result.Shape.isValid() and not result.Shape.isNull()

            axis = definition["axis"]
            expected_link = (None, ())
            if axis["kind"] == "edge":
                reference = axis["reference"]
                expected_link = (
                    sources[reference["object_name"]],
                    (reference["subelement"],) if "subelement" in reference else (),
                )
            assert _link_sub(root.AxisLink) == expected_link
            result_records = tuple(
                {
                    "name": result.Name,
                    "label": str(result.Label),
                    "source_name": source_names[index],
                    "role": str(result.VibeCADTimelineRole),
                    "signature": _shape_signature(result.Shape),
                }
                for index, result in enumerate(results)
            )
            link_target, link_names = _link_sub(root.AxisLink)
            record = {
                "label": case["label"],
                "root_name": root.Name,
                "definition_id": str(root.VibeCADDefinitionId),
                "design_id": str(root.DesignId),
                "source_names": source_names,
                "axis_link": (link_target.Name if link_target else None, link_names),
                "base": tuple(float(value) for value in root.Base),
                "axis": tuple(float(value) for value in root.Axis),
                "angle": float(root.Angle),
                "symmetric": bool(root.Symmetric),
                "solid": bool(root.Solid),
                "results": result_records,
            }

            document.undo()
            _process_events()
            assert all(document.getObject(item["name"]) is None for item in result_records)
            assert all(bool(sources[name].Visibility) for name in source_names)
            document.redo()
            _process_events()
            root = document.getObject(record["root_name"])
            assert root is not None
            assert all(not bool(sources[name].Visibility) for name in source_names)
            for item in result_records:
                _assert_shape_signature(document.getObject(item["name"]).Shape, item["signature"])
            records.append(record)

        signed = document.getObject(records[0]["root_name"])
        assert _close(signed.Shape.BoundBox.YMax, 0.0)
        assert _close(signed.Shape.BoundBox.YMin, -6.0)
        symmetric = document.getObject(records[1]["root_name"])
        assert symmetric.Shape.BoundBox.YMin < -8.0
        assert symmetric.Shape.BoundBox.YMax > 8.0
        arc = document.getObject(records[3]["root_name"])
        assert _close(float(arc.Angle), 0.0)
        assert arc.Shape.ShapeType == "Shell"
        for name, signature in source_signatures.items():
            _assert_shape_signature(sources[name].Shape, signature)

        failure_cases = (
            (
                "Stale Part Revolve",
                _definition((stale_name,)),
                "NATIVE_TARGET_INVALID",
            ),
            (
                "Solid Part Revolve",
                _definition(("SolidSource",)),
                "NATIVE_MODEL_INVALID",
            ),
            (
                "Zero Line Part Revolve",
                _definition(
                    ("RollbackProfile",),
                    axis=_edge_axis("DirectionLine"),
                    angle=0.0,
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                "Unsupported Axis Part Revolve",
                _definition(
                    ("RollbackProfile",),
                    axis=_edge_axis("UnsupportedAxis"),
                    angle=90.0,
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                "Whole Wire Axis Part Revolve",
                _definition(
                    ("RollbackProfile",),
                    axis=_edge_axis("RollbackProfile", None),
                    angle=90.0,
                ),
                "NATIVE_MODEL_INVALID",
            ),
        )
        for label, definition, error_code in failure_cases:
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(
                {"operation": "revolve", "label": label, "definition": definition},
                succeeds=False,
            )
            assert response["error_code"] == error_code
            assert tuple(obj.Name for obj in document.Objects) == before
            assert document.HasPendingTransaction is False

        before = tuple(obj.Name for obj in document.Objects)
        assert bool(sources["RollbackProfile"].Visibility)
        original_verify = runtime_module.verify_part_revolve

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced exact Part Revolve postcondition failure.")

        runtime_module.verify_part_revolve = reject_after_creation
        try:
            rollback = native_call(
                {
                    "operation": "revolve",
                    "label": "Rollback Part Revolve",
                    "definition": _definition(("RollbackProfile",), angle=60.0),
                },
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_revolve = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False
        assert bool(sources["RollbackProfile"].Visibility)

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-part-revolve-"))
        save_path = save_directory / "ModelPartRevolve.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()

        for record in records:
            root = document.getObject(record["root_name"])
            assert root is not None and root.TypeId == "Part::Revolution"
            assert root.Label == record["label"]
            assert root.VibeCADTimelineRole == "operation"
            assert str(root.VibeCADDefinitionId) == record["definition_id"]
            assert str(root.DesignId) == record["design_id"]
            assert tuple(obj.Name for obj in root.VibeCADTimelineReplacedInputs) == record[
                "source_names"
            ]
            link_target, link_names = _link_sub(root.AxisLink)
            assert (link_target.Name if link_target else None, link_names) == record[
                "axis_link"
            ]
            assert all(
                _close(left, right)
                for left, right in zip(tuple(root.Base), record["base"], strict=True)
            )
            assert all(
                _close(left, right)
                for left, right in zip(tuple(root.Axis), record["axis"], strict=True)
            )
            assert _close(float(root.Angle), record["angle"])
            assert bool(root.Symmetric) is record["symmetric"]
            assert bool(root.Solid) is record["solid"]
            for item in record["results"]:
                result = document.getObject(item["name"])
                assert result is not None and result.TypeId == "Part::Revolution"
                assert str(result.Label) == item["label"]
                assert result.Source.Name == item["source_name"]
                assert str(result.VibeCADTimelineRole) == item["role"]
                if result is not root:
                    assert result.VibeCADTimelineOwner is root
                _assert_shape_signature(result.Shape, item["signature"])
        for name, signature in source_signatures.items():
            _assert_shape_signature(document.getObject(name).Shape, signature)

        print("VIBECAD_NATIVE_MODEL_PART_REVOLVE_GUI_OK", flush=True)
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
