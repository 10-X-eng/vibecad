# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real VibeCAD GUI and provider lifecycle gate for standalone Part Loft."""

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
            len(shape.Shells),
            len(shape.Solids),
        ),
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


def _wire(z: float, half_size: float):
    return Part.makePolygon(
        [
            App.Vector(-half_size, -half_size, z),
            App.Vector(half_size, -half_size, z),
            App.Vector(half_size, half_size, z),
            App.Vector(-half_size, half_size, z),
            App.Vector(-half_size, -half_size, z),
        ]
    )


def _edge(z: float, length: float):
    return Part.makeLine(
        App.Vector(-0.5 * length, 0, z),
        App.Vector(0.5 * length, 0, z),
    )


def _circle_profile(center, normal, radius: float = 2.0):
    return Part.makeCircle(radius, App.Vector(*center), App.Vector(*normal))


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
    document.openTransaction("Create Part Loft gate sources")
    try:
        sources = {
            "HumanLower": _publish_source(document, "HumanLower", _wire(0, 4)),
            "HumanUpper": _publish_source(document, "HumanUpper", _wire(8, 2)),
            "SolidLower": _publish_source(document, "SolidLower", _wire(0, 4)),
            "SolidUpper": _publish_source(document, "SolidUpper", _wire(10, 2)),
            "RuledLower": _publish_source(document, "RuledLower", _edge(0, 8)),
            "RuledUpper": _publish_source(document, "RuledUpper", _edge(6, 4)),
            "ExactProfiles": _publish_source(
                document,
                "ExactProfiles",
                Part.makeCompound([_wire(0, 3), _wire(9, 1.5)]),
            ),
            "PlacedLower": _publish_source(
                document,
                "PlacedLower",
                _wire(0, 3),
                placement=App.Placement(App.Vector(30, 4, 2), App.Rotation()),
            ),
            "PlacedUpper": _publish_source(
                document,
                "PlacedUpper",
                _wire(0, 1.5),
                placement=App.Placement(App.Vector(30, 4, 11), App.Rotation()),
            ),
            "ClosedA": _publish_source(
                document,
                "ClosedA",
                _circle_profile((12, 0, 0), (0, 1, 0)),
            ),
            "ClosedB": _publish_source(
                document,
                "ClosedB",
                _circle_profile((0, 12, 0), (-1, 0, 0)),
            ),
            "ClosedC": _publish_source(
                document,
                "ClosedC",
                _circle_profile((-12, 0, 0), (0, -1, 0)),
            ),
            "ClosedD": _publish_source(
                document,
                "ClosedD",
                _circle_profile((0, -12, 0), (1, 0, 0)),
            ),
            "RollbackLower": _publish_source(
                document,
                "RollbackLower",
                _wire(0, 2.5),
            ),
            "RollbackUpper": _publish_source(
                document,
                "RollbackUpper",
                _wire(7, 1.5),
            ),
            "InactiveLower": _publish_source(
                document,
                "InactiveLower",
                _wire(0, 2),
            ),
            "InvalidSolid": _publish_source(
                document,
                "InvalidSolid",
                Part.makeBox(3, 4, 5),
            ),
        }
        stale = _publish_source(document, "StaleLoftProfile", _wire(4, 2))
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise

    document.openTransaction("Delete stale Part Loft source")
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
    definition = model_part_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "l" * 64,
            ("Part_Loft",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("loft",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _profile(name: str, subelement: str | None = None) -> dict[str, str]:
    result = {"object_name": name}
    if subelement is not None:
        result["subelement"] = subelement
    return result


def _arguments(
    label: str,
    profiles,
    *,
    solid: bool,
    ruled: bool = False,
    closed: bool = False,
) -> dict[str, object]:
    return {
        "operation": "loft",
        "label": label,
        "definition": {
            "profiles": list(profiles),
            "solid": solid,
            "ruled": ruled,
            "closed": closed,
        },
    }


def _resolved_profile(source, subelement: str | None):
    if subelement is None:
        return Part.getShape(source, transform=True)
    return Part.getShape(source, subelement, needSubElement=True, transform=True)


def _expected_shape(document, sources, profiles, *, solid, ruled, closed):
    """Use the retained human feature, then abort every oracle mutation."""
    document.openTransaction("Probe exact Part Loft feature")
    try:
        copied_profiles = []
        for index, profile in enumerate(profiles, start=1):
            copied = document.addObject("Part::Feature", f"LoftOracleProfile{index}")
            copied.Shape = _resolved_profile(
                sources[profile["object_name"]],
                profile.get("subelement"),
            ).copy()
            copied_profiles.append(copied)
        result = document.addObject("Part::Loft", "LoftOracle")
        result.Sections = tuple(copied_profiles)
        result.Solid = solid
        result.Ruled = ruled
        result.Closed = closed
        result.MaxDegree = 5
        result.Linearize = False
        assert document.recompute([result], True, True) is not False
        # The retained task and Native runner both execute once provisionally
        # and once for durable commit. Force the second pass on isolated
        # profiles rather than sharing OCC-adjustable handles with real inputs.
        result.touch()
        assert document.recompute([result], True, True) is not False
        assert result.isValid() and not result.Shape.isNull() and result.Shape.isValid()
        return result.Shape.copy()
    finally:
        document.abortTransaction()


def _link_sub(value) -> tuple[object | None, tuple[str, ...]]:
    if not value:
        return None, ()
    target, names = value if isinstance(value, tuple) else (value, ())
    if isinstance(names, str):
        names = (names,) if names else ()
    return target, tuple(str(name) for name in names)


def _profile_links(value) -> tuple[tuple[object | None, tuple[str, ...]], ...]:
    flattened = []
    for group in tuple(value):
        target, names = _link_sub(group)
        for name in names or ("",):
            flattened.append((target, (name,)))
    return tuple(flattened)


def _assert_human_contract(document, sources) -> None:
    before = tuple(obj.Name for obj in document.Objects)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(sources["HumanLower"])
    Gui.Selection.addSelection(sources["HumanUpper"])
    _process_events()
    assert Gui.isCommandActive("Part_Loft")
    Gui.runCommand("Part_Loft", 0)
    _process_events(32)
    assert Gui.Control.activeDialog()
    window = Gui.getMainWindow()
    selected = window.findChild(QtWidgets.QTreeWidget, "selectedTreeWidget")
    available = window.findChild(QtWidgets.QTreeWidget, "availableTreeWidget")
    solid = window.findChild(QtWidgets.QCheckBox, "checkSolid")
    ruled = window.findChild(QtWidgets.QCheckBox, "checkRuledSurface")
    closed = window.findChild(QtWidgets.QCheckBox, "checkClosed")
    assert all(widget is not None for widget in (selected, available, solid, ruled, closed))
    assert [selected.topLevelItem(index).text(0) for index in range(2)] == [
        "HumanLower",
        "HumanUpper",
    ]
    assert selected.topLevelItemCount() == 2
    assert available.topLevelItemCount() >= 2
    assert (solid.text(), ruled.text(), closed.text()) == (
        "Create solid",
        "Ruled surface",
        "Closed",
    )
    assert (solid.isChecked(), ruled.isChecked(), closed.isChecked()) == (
        True,
        False,
        False,
    )
    Gui.Control.closeDialog()
    _process_events(16)
    Gui.Selection.clearSelection()
    assert tuple(obj.Name for obj in document.Objects) == before
    assert sources["HumanLower"].Visibility and sources["HumanUpper"].Visibility


def _cases() -> tuple[dict[str, object], ...]:
    return (
        {
            "label": "Gate Solid Loft",
            "profiles": (_profile("SolidLower"), _profile("SolidUpper")),
            "solid": True,
            "ruled": False,
            "closed": False,
        },
        {
            "label": "Gate Ruled Loft",
            "profiles": (_profile("RuledLower"), _profile("RuledUpper")),
            "solid": False,
            "ruled": True,
            "closed": False,
        },
        {
            "label": "Gate Exact Subelement Loft",
            "profiles": (
                _profile("ExactProfiles", "Wire1"),
                _profile("ExactProfiles", "Wire2"),
            ),
            "solid": True,
            "ruled": False,
            "closed": False,
        },
        {
            "label": "Gate Placed Loft",
            "profiles": (_profile("PlacedLower"), _profile("PlacedUpper")),
            "solid": True,
            "ruled": False,
            "closed": False,
        },
        {
            "label": "Gate Closed Loft",
            "profiles": (
                _profile("ClosedA"),
                _profile("ClosedB"),
                _profile("ClosedC"),
                _profile("ClosedD"),
            ),
            "solid": False,
            "ruled": False,
            "closed": True,
        },
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartLoftGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_human_contract(document, sources)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-loft-gui")
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
                f"model-part-loft-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments.get("label"),
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        before = tuple(obj.Name for obj in document.Objects)
        invalid_schema = native_call(
            {
                "operation": "loft",
                "label": "Incomplete Loft",
                "definition": {
                    "profiles": [_profile("SolidLower"), _profile("SolidUpper")],
                    "solid": True,
                    "ruled": False,
                },
            },
            succeeds=False,
        )
        assert invalid_schema["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        expected_fields = {
            "ok",
            "root",
            "profile_count",
            "profile_types",
            "solid",
            "ruled",
            "closed",
            "shape_type",
            "face_count",
            "solid_count",
            "area_mm2",
            "volume_mm3",
            "receipt",
            "assistant_undo_available",
        }
        records = []
        for case in _cases():
            profiles = case["profiles"]
            expected = None
            if not case["closed"]:
                expected_shape = _expected_shape(
                    document,
                    sources,
                    profiles,
                    solid=case["solid"],
                    ruled=case["ruled"],
                    closed=False,
                )
                expected = _shape_signature(expected_shape)
            source_objects = tuple(
                dict.fromkeys(sources[profile["object_name"]] for profile in profiles)
            )
            source_signatures = tuple(
                _shape_signature(Part.getShape(source, transform=True))
                for source in source_objects
            )
            assert all(source.Visibility for source in source_objects)
            response = native_call(
                _arguments(
                    case["label"],
                    profiles,
                    solid=case["solid"],
                    ruled=case["ruled"],
                    closed=case["closed"],
                )
            )
            assert set(response) == expected_fields
            assert response["profile_count"] == len(profiles)
            assert response["profile_types"] == [
                str(
                    _resolved_profile(
                        sources[profile["object_name"]],
                        profile.get("subelement"),
                    ).ShapeType
                )
                for profile in profiles
            ]
            assert (response["solid"], response["ruled"], response["closed"]) == (
                case["solid"],
                case["ruled"],
                case["closed"],
            )
            assert response["assistant_undo_available"] is True
            assert len(response["receipt"]["created"]) == 1
            assert response["receipt"]["changed"] == []
            assert response["receipt"]["deleted"] == []

            result = document.getObject(response["root"]["object_name"])
            assert result is not None and result.TypeId == "Part::Loft"
            if expected is None:
                expected = _shape_signature(result.Shape)
                bounds = expected["bounds"]
                assert bounds[0] < -10 and bounds[1] > 10
                assert bounds[2] < -10 and bounds[3] > 10
                assert bounds[4] < -1 and bounds[5] > 1
                assert max(abs(value) for value in bounds) < 30
                assert expected["topology"][3] >= 1
                assert expected["measures"][1] > 100
            assert response["shape_type"] == expected["shape_type"]
            assert response["face_count"] == expected["topology"][3]
            assert response["solid_count"] == expected["topology"][5]
            assert _close(response["area_mm2"], expected["measures"][1])
            assert _close(response["volume_mm3"], expected["measures"][2])
            assert result.Label == case["label"]
            assert tuple(result.Sections) == tuple(
                sources[profile["object_name"]] for profile in profiles
            )
            expected_links = tuple(
                (
                    sources[profile["object_name"]],
                    (profile.get("subelement") or "",),
                )
                for profile in profiles
            )
            if any(profile.get("subelement") for profile in profiles):
                assert _profile_links(result.ProfileLinks) == expected_links
            else:
                assert tuple(result.ProfileLinks) == ()
            assert (bool(result.Solid), bool(result.Ruled), bool(result.Closed)) == (
                case["solid"],
                case["ruled"],
                case["closed"],
            )
            assert int(result.MaxDegree) == 5 and not bool(result.Linearize)
            assert result.getParentGeoFeatureGroup() is None
            assert result.VibeCADTimelineRole == "operation"
            assert getattr(result, "VibeCADTimelineOwner", None) is None
            assert str(result.VibeCADDefinitionId) and str(result.DesignId)
            assert tuple(result.VibeCADTimelineReplacedInputs) == source_objects
            assert tuple(result.ViewObject.claimChildren()) == source_objects
            assert not any(source.Visibility for source in source_objects)
            _assert_shape_signature(result.Shape, expected)
            for _index in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_shape_signature(result.Shape, expected)
            for source, signature in zip(source_objects, source_signatures, strict=True):
                _assert_shape_signature(Part.getShape(source, transform=True), signature)

            record = {
                "name": result.Name,
                "label": str(result.Label),
                "definition_id": str(result.VibeCADDefinitionId),
                "design_id": str(result.DesignId),
                "profiles": tuple(
                    (profile["object_name"], profile.get("subelement") or "")
                    for profile in profiles
                ),
                "source_names": tuple(source.Name for source in source_objects),
                "signature": expected,
                "controls": (case["solid"], case["ruled"], case["closed"]),
            }
            document.undo()
            _process_events()
            assert document.getObject(record["name"]) is None
            assert all(source.Visibility for source in source_objects)
            document.redo()
            _process_events()
            result = document.getObject(record["name"])
            assert result is not None
            assert not any(source.Visibility for source in source_objects)
            _assert_shape_signature(result.Shape, expected)
            records.append(record)

        failure_cases = (
            (
                _arguments(
                    "Missing Loft Profile",
                    (_profile(stale_name), _profile("RollbackUpper")),
                    solid=True,
                ),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments(
                    "Invalid Solid Loft Profile",
                    (_profile("InvalidSolid"), _profile("RollbackUpper")),
                    solid=False,
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Duplicate Loft Profiles",
                    (_profile("RollbackLower"), _profile("RollbackLower")),
                    solid=True,
                ),
                "NATIVE_ARGUMENTS_INVALID",
            ),
        )
        for arguments, error_code in failure_cases:
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(arguments, succeeds=False)
            assert response["error_code"] == error_code
            assert tuple(obj.Name for obj in document.Objects) == before
            assert document.HasPendingTransaction is False

        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        inactive = sources["InactiveLower"]
        timeline.Position = 0
        _process_events()
        assert not PartGui.isModelingObjectActive(inactive)
        before = tuple(obj.Name for obj in document.Objects)
        inactive_response = native_call(
            _arguments(
                "Inactive Loft",
                (_profile("InactiveLower"), _profile("RollbackUpper")),
                solid=True,
            ),
            succeeds=False,
        )
        assert inactive_response["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()
        assert PartGui.isModelingObjectActive(inactive)

        rollback_sources = (sources["RollbackLower"], sources["RollbackUpper"])
        rollback_signatures = tuple(
            _shape_signature(Part.getShape(source, transform=True))
            for source in rollback_sources
        )
        before = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_part_loft

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced exact Part Loft postcondition failure.")

        runtime_module.verify_part_loft = reject_after_creation
        try:
            rollback = native_call(
                _arguments(
                    "Rollback Loft",
                    tuple(_profile(source.Name) for source in rollback_sources),
                    solid=True,
                ),
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_loft = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False
        assert all(source.Visibility for source in rollback_sources)
        for source, signature in zip(rollback_sources, rollback_signatures, strict=True):
            _assert_shape_signature(Part.getShape(source, transform=True), signature)

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-loft-"))
        save_path = save_directory / "ModelPartLoft.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()

        for record in records:
            result = document.getObject(record["name"])
            assert result is not None and result.TypeId == "Part::Loft"
            assert result.Label == record["label"]
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            assert (bool(result.Solid), bool(result.Ruled), bool(result.Closed)) == record[
                "controls"
            ]
            assert int(result.MaxDegree) == 5 and not bool(result.Linearize)
            assert tuple(source.Name for source in result.Sections) == tuple(
                profile[0] for profile in record["profiles"]
            )
            if any(profile[1] for profile in record["profiles"]):
                actual_links = tuple(
                    (target.Name, names)
                    for target, names in _profile_links(result.ProfileLinks)
                )
                assert actual_links == tuple(
                    (name, (subelement,)) for name, subelement in record["profiles"]
                )
            else:
                assert tuple(result.ProfileLinks) == ()
            assert tuple(
                source.Name for source in result.VibeCADTimelineReplacedInputs
            ) == record["source_names"]
            assert tuple(
                source.Name for source in result.ViewObject.claimChildren()
            ) == record["source_names"]
            assert not any(document.getObject(name).Visibility for name in record["source_names"])
            _assert_shape_signature(result.Shape, record["signature"])
            for _index in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_shape_signature(result.Shape, record["signature"])

        print("VIBECAD_NATIVE_MODEL_PART_LOFT_GUI_OK", flush=True)
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
