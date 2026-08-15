# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real VibeCAD GUI and provider lifecycle gate for standalone Part Sweep."""

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


def _circle_wire(z: float, radius: float, *, x: float = 0.0, y: float = 0.0):
    edge = Part.makeCircle(radius, App.Vector(x, y, z), App.Vector(0, 0, 1))
    return Part.Wire([edge])


def _line_profile(z: float, half_length: float):
    return Part.makeLine(
        App.Vector(-half_length, 0, z),
        App.Vector(half_length, 0, z),
    )


def _path(*points):
    return Part.makePolygon([App.Vector(*point) for point in points])


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


def _create_sources(document) -> tuple[dict[str, object], str, str]:
    document.openTransaction("Create Part Sweep gate sources")
    try:
        placed = App.Placement(App.Vector(30, 4, 2), App.Rotation())
        sources = {
            "HumanProfile": _publish_source(
                document, "HumanProfile", _circle_wire(0, 2)
            ),
            "HumanPath": _publish_source(
                document, "HumanPath", Part.makeLine(App.Vector(), App.Vector(0, 0, 8))
            ),
            "SolidProfile": _publish_source(
                document, "SolidProfile", _circle_wire(0, 3)
            ),
            "SolidPath": _publish_source(
                document, "SolidPath", Part.makeLine(App.Vector(), App.Vector(0, 0, 12))
            ),
            "SurfaceProfile": _publish_source(
                document, "SurfaceProfile", _line_profile(0, 3)
            ),
            "SurfacePath": _publish_source(
                document, "SurfacePath", _path((0, 0, 0), (0, 0, 4), (1, 0, 9))
            ),
            "ExactProfiles": _publish_source(
                document,
                "ExactProfiles",
                Part.makeCompound([_circle_wire(0, 2.5), _circle_wire(10, 1.5)]),
            ),
            "ExactPath": _publish_source(
                document, "ExactPath", _path((0, 0, 0), (0, 0, 5), (0, 0, 10))
            ),
            "PlacedProfile": _publish_source(
                document, "PlacedProfile", _circle_wire(0, 2.25), placement=placed
            ),
            "PlacedPath": _publish_source(
                document,
                "PlacedPath",
                Part.makeLine(App.Vector(), App.Vector(0, 0, 9)),
                placement=placed,
            ),
            "CompoundProfile": _publish_source(
                document, "CompoundProfile", _circle_wire(0, 1.75)
            ),
            "CompoundPath": _publish_source(
                document,
                "CompoundPath",
                Part.makeCompound(
                    [
                        Part.makeLine(App.Vector(), App.Vector(0, 0, 4)),
                        Part.makeLine(App.Vector(0, 0, 4), App.Vector(0, 0, 8)),
                    ]
                ),
            ),
            "OpenSolidProfile": _publish_source(
                document, "OpenSolidProfile", _line_profile(0, 2)
            ),
            "OpenSolidPath": _publish_source(
                document,
                "OpenSolidPath",
                Part.makeLine(App.Vector(), App.Vector(0, 0, 8)),
            ),
            "DisconnectedPath": _publish_source(
                document,
                "DisconnectedPath",
                Part.makeCompound(
                    [
                        Part.makeLine(App.Vector(), App.Vector(0, 0, 3)),
                        Part.makeLine(App.Vector(10, 0, 0), App.Vector(10, 0, 3)),
                    ]
                ),
            ),
            "InvalidProfile": _publish_source(
                document, "InvalidProfile", Part.makeBox(3, 4, 5)
            ),
            "InvalidPath": _publish_source(
                document, "InvalidPath", Part.makeBox(2, 3, 4)
            ),
            "RollbackProfile": _publish_source(
                document, "RollbackProfile", _circle_wire(0, 2)
            ),
            "RollbackPath": _publish_source(
                document,
                "RollbackPath",
                Part.makeLine(App.Vector(), App.Vector(0, 0, 7)),
            ),
            "InactiveProfile": _publish_source(
                document, "InactiveProfile", _circle_wire(0, 1.5)
            ),
        }
        stale_profile = _publish_source(
            document, "StaleSweepProfile", _circle_wire(0, 1)
        )
        stale_path = _publish_source(
            document,
            "StaleSweepPath",
            Part.makeLine(App.Vector(), App.Vector(0, 0, 5)),
        )
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise

    document.openTransaction("Delete stale Part Sweep sources")
    try:
        stale_profile_name = stale_profile.Name
        stale_path_name = stale_path.Name
        document.removeObject(stale_profile_name)
        document.removeObject(stale_path_name)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert document.getObject(stale_profile_name) is None
    assert document.getObject(stale_path_name) is None
    return sources, stale_profile_name, stale_path_name


def _turn() -> NativeTurnSnapshot:
    definition = model_part_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "s" * 64,
            ("Part_Sweep",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("sweep",)),),
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


def _path_target(name: str, subelements=()) -> dict[str, object]:
    result = {"object_name": name}
    if subelements:
        result["subelements"] = list(subelements)
    return result


def _arguments(
    label: str,
    profiles,
    path,
    *,
    solid: bool,
    frenet: bool,
) -> dict[str, object]:
    return {
        "operation": "sweep",
        "label": label,
        "definition": {
            "profiles": list(profiles),
            "path": path,
            "solid": solid,
            "frenet": frenet,
        },
    }


def _resolved_profile(source, subelement: str | None):
    if subelement is None:
        return Part.getShape(source, transform=True)
    return Part.getShape(source, subelement, needSubElement=True, transform=True)


def _resolved_path(source, subelements):
    if not subelements:
        return Part.getShape(source, transform=True)
    return Part.Wire(
        [
            Part.getShape(source, name, needSubElement=True, transform=True)
            for name in subelements
        ]
    )


def _expected_shape(document, sources, profiles, path, *, solid, frenet):
    """Use an isolated retained human feature, then abort every oracle mutation."""
    document.openTransaction("Probe exact Part Sweep feature")
    try:
        copied_profiles = []
        for index, profile in enumerate(profiles, start=1):
            copied = document.addObject("Part::Feature", f"SweepOracleProfile{index}")
            copied.Shape = _resolved_profile(
                sources[profile["object_name"]],
                profile.get("subelement"),
            ).copy()
            copied_profiles.append(copied)
        copied_path = document.addObject("Part::Feature", "SweepOraclePath")
        copied_path.Shape = _resolved_path(
            sources[path["object_name"]],
            path.get("subelements", ()),
        ).copy()
        result = document.addObject("Part::Sweep", "SweepOracle")
        result.Sections = tuple(copied_profiles)
        result.Spine = (copied_path, [])
        result.Solid = solid
        result.Frenet = frenet
        result.Transition = "Right corner"
        result.Linearize = False
        assert document.recompute([result], True, True) is not False
        result.touch()
        assert document.recompute([result], True, True) is not False
        assert result.isValid() and not result.Shape.isNull() and result.Shape.isValid()
        if solid:
            assert result.Shape.Solids
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
    Gui.Selection.addSelection(sources["HumanProfile"])
    _process_events()
    assert Gui.isCommandActive("Part_Sweep")
    Gui.runCommand("Part_Sweep", 0)
    _process_events(32)
    assert Gui.Control.activeDialog()
    window = Gui.getMainWindow()
    selected = window.findChild(QtWidgets.QTreeWidget, "selectedTreeWidget")
    available = window.findChild(QtWidgets.QTreeWidget, "availableTreeWidget")
    path_button = window.findChild(QtWidgets.QPushButton, "buttonPath")
    solid = window.findChild(QtWidgets.QCheckBox, "checkSolid")
    frenet = window.findChild(QtWidgets.QCheckBox, "checkFrenet")
    assert all(
        widget is not None
        for widget in (selected, available, path_button, solid, frenet)
    )
    assert selected.topLevelItemCount() == 1
    assert selected.topLevelItem(0).text(0) == "HumanProfile"
    assert available.topLevelItemCount() >= 2
    assert path_button.text() == "Sweep Path"
    assert (solid.text(), frenet.text()) == ("Create solid", "Frenet")
    assert (solid.isChecked(), frenet.isChecked()) == (True, True)
    Gui.Control.closeDialog()
    _process_events(16)
    Gui.Selection.clearSelection()
    assert tuple(obj.Name for obj in document.Objects) == before
    assert sources["HumanProfile"].Visibility and sources["HumanPath"].Visibility


def _assert_exact_preflight_rejects_change(document, sources) -> None:
    spec = runtime_module.prepare_part_sweep(
        str(document.Uid),
        _arguments(
            "Exactness Probe",
            (_profile("RollbackProfile"),),
            _path_target("RollbackPath"),
            solid=True,
            frenet=True,
        )["definition"],
    )
    prepared = runtime_module.preflight_part_sweep(document, spec)
    source = sources["RollbackProfile"]
    original_placement = source.Placement
    before = tuple(obj.Name for obj in document.Objects)
    rejected = False
    try:
        moved = App.Placement(original_placement)
        moved.Base.x += 1.0
        source.Placement = moved
        try:
            runtime_module.create_part_sweep(
                document,
                label="Must Not Exist",
                prepared=prepared,
            )
        except NativeModelError as exc:
            rejected = "changed after preflight" in str(exc)
    finally:
        source.Placement = original_placement
        assert document.recompute([source], True, True) is not False
    assert rejected
    assert tuple(obj.Name for obj in document.Objects) == before


def _cases() -> tuple[dict[str, object], ...]:
    return (
        {
            "label": "Gate Solid Sweep",
            "profiles": (_profile("SolidProfile"),),
            "path": _path_target("SolidPath"),
            "solid": True,
            "frenet": True,
        },
        {
            "label": "Gate Surface Sweep",
            "profiles": (_profile("SurfaceProfile"),),
            "path": _path_target("SurfacePath", ("Edge1", "Edge2")),
            "solid": False,
            "frenet": False,
        },
        {
            "label": "Gate Exact Multisection Sweep",
            "profiles": (
                _profile("ExactProfiles", "Wire1"),
                _profile("ExactProfiles", "Wire2"),
            ),
            "path": _path_target("ExactPath", ("Edge1", "Edge2")),
            "solid": True,
            "frenet": True,
        },
        {
            "label": "Gate Placed Sweep",
            "profiles": (_profile("PlacedProfile"),),
            "path": _path_target("PlacedPath"),
            "solid": True,
            "frenet": False,
        },
        {
            "label": "Gate Whole Compound Path Sweep",
            "profiles": (_profile("CompoundProfile"),),
            "path": _path_target("CompoundPath"),
            "solid": True,
            "frenet": True,
        },
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartSweepGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_profile_name, stale_path_name = _create_sources(document)
        _assert_human_contract(document, sources)
        _assert_exact_preflight_rejects_change(document, sources)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-sweep-gui")
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
                f"model-part-sweep-call-{call_number}",
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
                "operation": "sweep",
                "label": "Incomplete Sweep",
                "definition": {
                    "profiles": [_profile("SolidProfile")],
                    "path": _path_target("SolidPath"),
                    "solid": True,
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
            "path_type",
            "path_edge_count",
            "solid",
            "frenet",
            "shape_type",
            "edge_count",
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
            path = case["path"]
            expected = _shape_signature(
                _expected_shape(
                    document,
                    sources,
                    profiles,
                    path,
                    solid=case["solid"],
                    frenet=case["frenet"],
                )
            )
            source_objects = tuple(
                dict.fromkeys(
                    [sources[profile["object_name"]] for profile in profiles]
                    + [sources[path["object_name"]]]
                )
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
                    path,
                    solid=case["solid"],
                    frenet=case["frenet"],
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
            assert response["path_type"] in {"Edge", "Wire"}
            assert response["path_edge_count"] == len(
                path.get("subelements", ())
            ) or response["path_edge_count"] == len(
                Part.getShape(sources[path["object_name"]], transform=True).Edges
            )
            assert (response["solid"], response["frenet"]) == (
                case["solid"],
                case["frenet"],
            )
            assert response["assistant_undo_available"] is True
            assert len(response["receipt"]["created"]) == 1
            assert response["receipt"]["changed"] == []
            assert response["receipt"]["deleted"] == []

            result = document.getObject(response["root"]["object_name"])
            assert result is not None and result.TypeId == "Part::Sweep"
            assert response["shape_type"] == expected["shape_type"]
            assert response["edge_count"] == expected["topology"][1]
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
            assert _link_sub(result.Spine) == (
                sources[path["object_name"]],
                tuple(path.get("subelements", ())),
            )
            assert (bool(result.Solid), bool(result.Frenet)) == (
                case["solid"],
                case["frenet"],
            )
            assert str(result.Transition) == "Right corner"
            assert not bool(result.Linearize)
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
                "path": (
                    path["object_name"],
                    tuple(path.get("subelements", ())),
                ),
                "source_names": tuple(source.Name for source in source_objects),
                "signature": expected,
                "controls": (case["solid"], case["frenet"]),
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
                    "Missing Sweep Profile",
                    (_profile(stale_profile_name),),
                    _path_target("RollbackPath"),
                    solid=True,
                    frenet=True,
                ),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments(
                    "Missing Sweep Path",
                    (_profile("RollbackProfile"),),
                    _path_target(stale_path_name),
                    solid=True,
                    frenet=True,
                ),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments(
                    "Invalid Sweep Profile",
                    (_profile("InvalidProfile"),),
                    _path_target("RollbackPath"),
                    solid=False,
                    frenet=True,
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Invalid Sweep Path",
                    (_profile("RollbackProfile"),),
                    _path_target("InvalidPath"),
                    solid=False,
                    frenet=True,
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Disconnected Sweep Path",
                    (_profile("RollbackProfile"),),
                    _path_target("DisconnectedPath", ("Edge1", "Edge2")),
                    solid=False,
                    frenet=True,
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Invalid Solid Sweep",
                    (_profile("OpenSolidProfile"),),
                    _path_target("OpenSolidPath"),
                    solid=True,
                    frenet=True,
                ),
                "NATIVE_MODEL_INVALID",
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
        inactive = sources["InactiveProfile"]
        timeline.Position = 0
        _process_events()
        assert not PartGui.isModelingObjectActive(inactive)
        before = tuple(obj.Name for obj in document.Objects)
        inactive_response = native_call(
            _arguments(
                "Inactive Sweep",
                (_profile("InactiveProfile"),),
                _path_target("RollbackPath"),
                solid=True,
                frenet=True,
            ),
            succeeds=False,
        )
        assert inactive_response["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()
        assert PartGui.isModelingObjectActive(inactive)

        rollback_sources = (sources["RollbackProfile"], sources["RollbackPath"])
        rollback_signatures = tuple(
            _shape_signature(Part.getShape(source, transform=True))
            for source in rollback_sources
        )
        before = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_part_sweep

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced exact Part Sweep postcondition failure.")

        runtime_module.verify_part_sweep = reject_after_creation
        try:
            rollback = native_call(
                _arguments(
                    "Rollback Sweep",
                    (_profile("RollbackProfile"),),
                    _path_target("RollbackPath"),
                    solid=True,
                    frenet=True,
                ),
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_sweep = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False
        assert all(source.Visibility for source in rollback_sources)
        for source, signature in zip(rollback_sources, rollback_signatures, strict=True):
            _assert_shape_signature(Part.getShape(source, transform=True), signature)

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-sweep-"))
        save_path = save_directory / "ModelPartSweep.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()

        for record in records:
            result = document.getObject(record["name"])
            assert result is not None and result.TypeId == "Part::Sweep"
            assert result.Label == record["label"]
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            assert (bool(result.Solid), bool(result.Frenet)) == record["controls"]
            assert str(result.Transition) == "Right corner"
            assert not bool(result.Linearize)
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
            spine, subelements = _link_sub(result.Spine)
            assert (spine.Name, subelements) == record["path"]
            assert tuple(
                source.Name for source in result.VibeCADTimelineReplacedInputs
            ) == record["source_names"]
            assert tuple(
                source.Name for source in result.ViewObject.claimChildren()
            ) == record["source_names"]
            assert not any(
                document.getObject(name).Visibility for name in record["source_names"]
            )
            _assert_shape_signature(result.Shape, record["signature"])
            for _index in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_shape_signature(result.Shape, record["signature"])

        print("VIBECAD_NATIVE_MODEL_PART_SWEEP_GUI_OK", flush=True)
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
