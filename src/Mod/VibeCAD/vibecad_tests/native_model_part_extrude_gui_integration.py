# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for Native standalone Part Extrude."""

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
            "e" * 64,
            ("Part_Extrude",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("extrude",)),),
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


def _publish_circle_source(document, name: str):
    circle = document.addObject("Part::Circle", name)
    circle.Label = name
    circle.Radius = 2.5
    circle.Placement = App.Placement(
        App.Vector(72.0, 5.0, 0.0),
        App.Rotation(),
    )
    PartDesign.initializeDesignDefinition(circle)
    document.publishProvisionalTimelineOperationBlock(circle, (), ())
    assert document.recompute([circle], True, True) is not False
    PartDesign.finalizeDesignDefinition(circle)
    assert PartGui.isModelingObjectActive(circle)
    return circle


def _rectangle(x: float, y: float, width: float = 5.0, height: float = 4.0):
    return Part.makePolygon(
        [
            App.Vector(x, y, 0),
            App.Vector(x + width, y, 0),
            App.Vector(x + width, y + height, 0),
            App.Vector(x, y + height, 0),
        ],
        True,
    )


def _create_sources(document) -> tuple[dict[str, object], str]:
    document.openTransaction("Create Part Extrude gate sources")
    try:
        sources = {
            "NormalProfile": _publish_source(
                document,
                "NormalProfile",
                _rectangle(0, 0),
            ),
            "CustomProfile": _publish_source(
                document,
                "CustomProfile",
                _rectangle(10, 0, 6, 5),
            ),
            "EdgeProfile": _publish_source(
                document,
                "EdgeProfile",
                _rectangle(22, 0),
            ),
            "SymmetricProfile": _publish_source(
                document,
                "SymmetricProfile",
                _rectangle(32, 0),
            ),
            "MultiProfileA": _publish_source(
                document,
                "MultiProfileA",
                _rectangle(42, 0),
            ),
            "MultiProfileB": _publish_source(
                document,
                "MultiProfileB",
                _rectangle(52, 0),
            ),
            "RollbackProfile": _publish_source(
                document,
                "RollbackProfile",
                _rectangle(62, 0),
            ),
            "NonPlanarProfile": _publish_source(
                document,
                "NonPlanarProfile",
                Part.makePolygon(
                    [
                        App.Vector(0, 15, 0),
                        App.Vector(5, 15, 1),
                        App.Vector(5, 19, 0),
                        App.Vector(0, 19, 0),
                    ],
                    True,
                ),
            ),
            "SolidSource": _publish_source(
                document,
                "SolidSource",
                Part.makeBox(4, 4, 4, App.Vector(10, 15, 0)),
            ),
            "DirectionLine": _publish_source(
                document,
                "DirectionLine",
                Part.makeLine(App.Vector(0, 0, 0), App.Vector(0, 0, 7)),
            ),
            "DirectionCircle": _publish_source(
                document,
                "DirectionCircle",
                Part.Wire([Part.makeCircle(3, App.Vector(25, 15, 0))]),
            ),
            "ParametricCircle": _publish_circle_source(
                document,
                "ParametricCircle",
            ),
        }
        stale = _publish_source(
            document,
            "TransientProfile",
            _rectangle(80, 0),
        )
        stale_name = stale.Name
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    document.openTransaction("Delete Part Extrude stale source")
    try:
        document.removeObject(stale_name)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert document.getObject(stale_name) is None
    return sources, stale_name


def _definition(
    sources: tuple[str, ...],
    *,
    direction: dict[str, object] | None = None,
    along: float = 10.0,
    against: float = 0.0,
    symmetric: bool = False,
    reversed_value: bool = False,
    taper_along: float = 0.0,
    taper_against: float = 0.0,
    solid: bool = True,
) -> dict[str, object]:
    return {
        "sources": [{"object_name": name} for name in sources],
        "direction": direction or {"kind": "normal"},
        "length_along_mm": along,
        "length_against_mm": against,
        "symmetric": symmetric,
        "reversed": reversed_value,
        "taper_along_degrees": taper_along,
        "taper_against_degrees": taper_against,
        "solid": solid,
    }


def _cases() -> tuple[dict[str, object], ...]:
    return (
        {
            "label": "Gate Normal Extrude",
            "definition": _definition(("NormalProfile",)),
        },
        {
            "label": "Gate Custom Taper Extrude",
            "definition": _definition(
                ("CustomProfile",),
                direction={
                    "kind": "custom",
                    "vector": {"x": 0.0, "y": 0.0, "z": 2.0},
                },
                along=8.0,
                against=2.0,
                taper_along=4.0,
                taper_against=-3.0,
            ),
        },
        {
            "label": "Gate Edge Magnitude Extrude",
            "definition": _definition(
                ("EdgeProfile",),
                direction={
                    "kind": "edge",
                    "edge": {
                        "object_name": "DirectionLine",
                        "subelement": "Edge1",
                    },
                },
                along=0.0,
                against=0.0,
                reversed_value=True,
            ),
        },
        {
            "label": "Gate Symmetric Extrude",
            "definition": _definition(
                ("SymmetricProfile",),
                along=12.0,
                against=5.0,
                symmetric=True,
            ),
        },
        {
            "label": "Gate Multi Extrude",
            "definition": _definition(
                ("MultiProfileA", "MultiProfileB"),
                direction={
                    "kind": "custom",
                    "vector": {"x": 0.0, "y": 0.0, "z": 1.0},
                },
                along=6.0,
            ),
        },
        {
            "label": "Gate Parametric Circle Extrude",
            "definition": _definition(("ParametricCircle",), along=5.0),
        },
    )


def _widget(window, widget_type, name: str):
    widget = window.findChild(widget_type, name)
    assert widget is not None, name
    return widget


def _assert_live_human_contract(document, source) -> None:
    before = tuple(obj.Name for obj in document.Objects)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(source)
    Gui.runCommand("Part_Extrude", 0)
    _process_events(32)
    window = Gui.getMainWindow()
    normal = _widget(window, QtWidgets.QRadioButton, "rbDirModeNormal")
    edge = _widget(window, QtWidgets.QRadioButton, "rbDirModeEdge")
    custom = _widget(window, QtWidgets.QRadioButton, "rbDirModeCustom")
    assert (normal.text(), edge.text(), custom.text()) == (
        "Along normal",
        "Along edge",
        "Custom direction",
    )
    reversed_control = _widget(window, QtWidgets.QCheckBox, "chkReversed")
    symmetric_control = _widget(window, QtWidgets.QCheckBox, "chkSymmetric")
    solid = _widget(window, QtWidgets.QCheckBox, "chkSolid")
    assert (reversed_control.text(), symmetric_control.text(), solid.text()) == (
        "Reversed",
        "Symmetric",
        "Create solid",
    )
    direction_components = tuple(
        _widget(window, QtWidgets.QDoubleSpinBox, name)
        for name in ("dirX", "dirY", "dirZ")
    )
    direction_link = _widget(window, QtWidgets.QLineEdit, "txtLink")
    normal.click()
    _process_events(4)
    assert not any(control.isEnabled() for control in direction_components)
    assert direction_link.isEnabled() is False
    edge.click()
    _process_events(4)
    assert direction_link.isEnabled() is True
    assert not any(control.isEnabled() for control in direction_components)
    custom.click()
    _process_events(4)
    assert all(control.isEnabled() for control in direction_components)
    assert direction_link.isEnabled() is False
    forward = _widget(window, QtWidgets.QAbstractSpinBox, "spinLenFwd")
    reverse = _widget(window, QtWidgets.QAbstractSpinBox, "spinLenRev")
    assert _close(forward.property("rawValue"), 10.0)
    assert _close(reverse.property("rawValue"), 0.0)
    symmetric_control.setChecked(True)
    _process_events(4)
    assert reverse.isEnabled() is False
    assert _widget(window, QtWidgets.QAbstractSpinBox, "spinTaperAngle")
    assert _widget(window, QtWidgets.QAbstractSpinBox, "spinTaperAngleRev")
    tree = _widget(window, QtWidgets.QTreeWidget, "treeWidget")
    selected = tree.selectedItems()
    assert len(selected) == 1 and selected[0].text(0) == source.Label
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
        document = App.newDocument("NativeModelPartExtrudeGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_live_human_contract(document, sources["NormalProfile"])
        source_signatures = {
            name: _shape_signature(obj.Shape) for name, obj in sources.items()
        }

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-extrude-gui")
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
                "model.part",
                json.dumps(arguments, separators=(",", ":")),
                f"model-part-extrude-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments.get("label"), response)
            return response

        before = tuple(obj.Name for obj in document.Objects)
        invalid_schema = native_call(
            {"operation": "extrude", "definition": _cases()[0]["definition"]},
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
            "direction_mode",
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
            response = native_call({"operation": "extrude", **case})
            assert set(response) == expected_response_fields
            assert response["source_count"] == len(source_names)
            assert response["result_count"] == len(source_names)
            assert response["resource_count"] == len(source_names) - 1
            assert response["direction_mode"] == definition["direction"]["kind"]
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
                assert result.TypeId == "Part::Extrusion"
                if result is root:
                    assert result.Label == case["label"]
                else:
                    assert result.Label.startswith(case["label"])
                    assert result.Label != case["label"]
                assert result.Base is sources[source_names[index]]
                assert result.getParentGeoFeatureGroup() is None
                if result is root:
                    assert getattr(result, "VibeCADTimelineOwner", None) is None
                else:
                    assert result.VibeCADTimelineRole == "resource"
                    assert result.VibeCADTimelineOwner is root
                assert result.Shape.isValid() and not result.Shape.isNull()
            if definition["direction"]["kind"] == "edge":
                assert _link_sub(root.DirLink) == (sources["DirectionLine"], ("Edge1",))
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
            record = {
                "label": case["label"],
                "root_name": root.Name,
                "definition_id": str(root.VibeCADDefinitionId),
                "design_id": str(root.DesignId),
                "source_names": source_names,
                "direction_mode": str(root.DirMode),
                "dir_link": (
                    _link_sub(root.DirLink)[0].Name if _link_sub(root.DirLink)[0] else None,
                    _link_sub(root.DirLink)[1],
                ),
                "lengths": (float(root.LengthFwd.Value), float(root.LengthRev.Value)),
                "tapers": (float(root.TaperAngle.Value), float(root.TaperAngleRev.Value)),
                "symmetric": bool(root.Symmetric),
                "reversed": bool(root.Reversed),
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

        normal = document.getObject(records[0]["root_name"])
        assert _close(normal.Shape.BoundBox.ZMin, 0.0)
        assert _close(normal.Shape.BoundBox.ZMax, 10.0)
        edge_result = document.getObject(records[2]["root_name"])
        assert _close(edge_result.Shape.BoundBox.ZMin, -7.0)
        assert _close(edge_result.Shape.BoundBox.ZMax, 0.0)
        symmetric_result = document.getObject(records[3]["root_name"])
        assert _close(symmetric_result.Shape.BoundBox.ZMin, -6.0)
        assert _close(symmetric_result.Shape.BoundBox.ZMax, 6.0)
        for name, signature in source_signatures.items():
            _assert_shape_signature(sources[name].Shape, signature)

        failure_cases = (
            (
                "Stale Part Extrude",
                _definition((stale_name,)),
                "NATIVE_TARGET_INVALID",
            ),
            (
                "Solid Part Extrude",
                _definition(("SolidSource",)),
                "NATIVE_MODEL_INVALID",
            ),
            (
                "Nonplanar Normal Part Extrude",
                _definition(("NonPlanarProfile",)),
                "NATIVE_MODEL_INVALID",
            ),
            (
                "Curved Edge Part Extrude",
                _definition(
                    ("RollbackProfile",),
                    direction={
                        "kind": "edge",
                        "edge": {
                            "object_name": "DirectionCircle",
                            "subelement": "Edge1",
                        },
                    },
                ),
                "NATIVE_MODEL_INVALID",
            ),
        )
        for label, definition, error_code in failure_cases:
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(
                {"operation": "extrude", "label": label, "definition": definition},
                succeeds=False,
            )
            assert response["error_code"] == error_code
            assert tuple(obj.Name for obj in document.Objects) == before
            assert document.HasPendingTransaction is False

        before = tuple(obj.Name for obj in document.Objects)
        assert bool(sources["RollbackProfile"].Visibility)
        original_verify = runtime_module.verify_part_extrude

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced exact Part Extrude postcondition failure.")

        runtime_module.verify_part_extrude = reject_after_creation
        try:
            rollback = native_call(
                {
                    "operation": "extrude",
                    "label": "Rollback Part Extrude",
                    "definition": _definition(("RollbackProfile",)),
                },
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_extrude = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False
        assert bool(sources["RollbackProfile"].Visibility)

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-part-extrude-"))
        save_path = save_directory / "ModelPartExtrude.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()

        for record in records:
            root = document.getObject(record["root_name"])
            assert root is not None and root.TypeId == "Part::Extrusion"
            assert root.Label == record["label"]
            assert root.VibeCADTimelineRole == "operation"
            assert str(root.VibeCADDefinitionId) == record["definition_id"]
            assert str(root.DesignId) == record["design_id"]
            assert tuple(obj.Name for obj in root.VibeCADTimelineReplacedInputs) == record[
                "source_names"
            ]
            assert str(root.DirMode) == record["direction_mode"]
            link_target, link_names = _link_sub(root.DirLink)
            assert (
                link_target.Name if link_target else None,
                link_names,
            ) == record["dir_link"]
            assert all(
                _close(left, right)
                for left, right in zip(
                    (float(root.LengthFwd.Value), float(root.LengthRev.Value)),
                    record["lengths"],
                    strict=True,
                )
            )
            assert all(
                _close(left, right)
                for left, right in zip(
                    (float(root.TaperAngle.Value), float(root.TaperAngleRev.Value)),
                    record["tapers"],
                    strict=True,
                )
            )
            assert bool(root.Symmetric) is record["symmetric"]
            assert bool(root.Reversed) is record["reversed"]
            assert bool(root.Solid) is record["solid"]
            for item in record["results"]:
                result = document.getObject(item["name"])
                assert result is not None and result.TypeId == "Part::Extrusion"
                assert str(result.Label) == item["label"]
                assert result.Base.Name == item["source_name"]
                assert str(result.VibeCADTimelineRole) == item["role"]
                if result is not root:
                    assert result.VibeCADTimelineOwner is root
                _assert_shape_signature(result.Shape, item["signature"])
        for name, signature in source_signatures.items():
            _assert_shape_signature(document.getObject(name).Shape, signature)

        print("VIBECAD_NATIVE_MODEL_PART_EXTRUDE_GUI_OK", flush=True)
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
