# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native GUI production gate for the BIM VibeScript domain."""

from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
import shutil
import sys
import tempfile

MODULE_ROOT = Path(__file__).resolve().parent.parent
while str(MODULE_ROOT) in sys.path:
    sys.path.remove(str(MODULE_ROOT))
sys.path.insert(0, str(MODULE_ROOT))

import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
import VibeCADVibeScriptDomainPublication as publication_module  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    delete_live_program,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    BIMDomainAdapter,
    accept_candidate,
    complete_inspection,
    execute_candidate,
    finish_delete,
    prepare_candidate,
    prepare_delete,
    restore_prepared_delete,
    retain_candidate,
    validate_candidate,
)
from VibeCADVibeScriptDomains import (  # noqa: E402
    PROP_PROGRAM_ID,
    PROP_PROGRAM_OUTPUT,
    _bim_document_snapshot,
    get_vibescript_pack,
    validate_program_source,
)
from vibescript_bim_api import BIMDomainAPI  # noqa: E402
from vibescript_bim_worker import (  # noqa: E402
    BIMCandidateError,
    validate_bim_graph,
)


EXPORTS = ("site", "building", "level", "wall", "slab", "structure", "opening")
EXPECTED_OUTPUTS = [
    {"name": "Site", "type": "site"},
    {"name": "Building", "type": "building"},
    {"name": "Ground", "type": "level"},
    {"name": "Wall", "type": "wall"},
    {"name": "Slab", "type": "slab"},
    {"name": "Column", "type": "structure"},
    {"name": "Opening", "type": "opening"},
]


class _Service:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _active_document():
        return App.ActiveDocument

    @staticmethod
    def active_workbench_name() -> str:
        return "BIMWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "bim-native-fixture-revision"

    def project_scope_snapshot(self) -> dict[str, str]:
        return {"root": str(self.root), "project_id": "bim-native-fixture"}


def _captured(
    root: Path,
    document,
    *,
    operation: str,
    arguments: dict,
) -> dict:
    pack = get_vibescript_pack("BIMWorkbench")
    assert pack is not None
    return {
        "tool_name": f"vibescript.bim.{operation}",
        "operation": operation,
        "arguments": arguments,
        "pack": pack,
        "project_root": str(root),
        "project_id": "bim-native-fixture",
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "bim-native-fixture-revision",
        "document_objects": [
            {
                "name": str(obj.Name),
                "label": str(obj.Label),
                "type_id": str(obj.TypeId),
            }
            for obj in document.Objects
        ],
        "live_programs": [],
        "surface": resolve_modeling_surface("BIMWorkbench", "vibescript").summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 90.0,
        "memory_limit_bytes": 3 * 1024 * 1024 * 1024,
    }


def _prepare_execute_validate(captured: dict):
    prepared = prepare_candidate(captured)
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    return prepared, execution, validate_candidate(prepared, execution)


def _api() -> BIMDomainAPI:
    return BIMDomainAPI(EXPORTS, EXPORTS)


def _expect_error(fragment: str, call) -> None:
    try:
        call()
    except (TypeError, ValueError, BIMCandidateError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(
            f"Expected BIM validation failure containing {fragment!r}."
        )


def _exercise_source_api() -> None:
    api = _api()
    assert api.exported_names == EXPORTS
    assert not hasattr(api, "output")
    for export in EXPORTS:
        member = getattr(api, export)
        signature = str(inspect.signature(member))
        assert "*args" not in signature and "**" not in signature
        assert inspect.getdoc(member)
    site = api.site(city="Chicago", latitude=41.8781, longitude=-87.6298)
    building = api.building(site)
    level = api.level(building, 0, height=3000)
    wall = api.wall(level, [[0, 0], [4000, 0]], width=200, height=2800)
    slab = api.slab(level, [[0, 0], [4, 0], [4, 3], [0, 3]])
    structure = api.structure(
        level,
        300,
        300,
        2800,
        placement={"position": [200, 200, 0], "rotation": [0, 0, 0, 1]},
        role="column",
    )
    opening = api.opening(wall, 900, 1200, offset=800, sill=900)
    assert [
        value.output_type
        for value in (site, building, level, wall, slab, structure, opening)
    ] == list(EXPORTS)
    assert opening.to_payload()["arguments"][0]["properties"]["graph_id"] == "bim4"
    try:
        wall.properties["width"] = 1
    except TypeError:
        pass
    else:
        raise AssertionError("BIM graph values must be immutable.")

    _expect_error("BIM site", lambda: api.building(level))
    _expect_error(
        "segments 0 and 2 intersect",
        lambda: api.wall(level, [[0, 0], [4, 4], [0, 4], [4, 0]], closed=True),
    )
    _expect_error(
        "segments 0 and 2 intersect",
        lambda: api.slab(level, [[0, 0], [4, 4], [0, 4], [4, 0]]),
    )
    _expect_error("greater than 0", lambda: api.structure(level, 0, 2, 3))
    _expect_error("inclusive range", lambda: api.opening(wall, 1, 1, segment=-1))

    pack = get_vibescript_pack("BIMWorkbench")
    assert pack is not None
    adapter = BIMDomainAdapter(pack)
    description = adapter.describe_api()
    assert description["api_contract"] == "vibecad-vibescript-bim-api-v1"
    assert [item["name"] for item in description["runtime_exports"]] == list(EXPORTS)
    assert "FreeCADCmd" in description["evaluation_model"]
    assert "ArchWindow._Window" in description["native_object_contracts"]["opening"]
    assert description["recommended_patterns"]
    assert "single canonical selector" in description["operation_selection"][
        "rectangular_structural_member"
    ]
    assert "not a visible door" in description["operation_selection"][
        "hosted_wall_void"
    ]
    for pattern in description["recommended_patterns"]:
        validate_program_source(pattern["source"])


def _source() -> str:
    return (
        "site = api.site(address=inputs['address'], city=inputs['city'], "
        "latitude=inputs['latitude'], longitude=inputs['longitude'], label='Project Site')\n"
        "building = api.building(site, label='Main Building')\n"
        "ground = api.level(building, 0, height=3000, label='Ground Floor')\n"
        "wall = api.wall(ground, [[0,0],[inputs['length'],0]], width=200, "
        "height=2800, label='South Wall')\n"
        "slab = api.slab(ground, [[0,0],[inputs['length'],0],"
        "[inputs['length'],3000],[0,3000]], thickness=200, label='Ground Slab')\n"
        "column = api.structure(ground, 300, 300, 2800, "
        "placement={'position':[200,200,0],'rotation':[0,0,0,1]}, "
        "role='column', label='Main Column')\n"
        "opening = api.opening(wall, 900, 1200, offset=800, sill=900, "
        "label='Window Opening')\n"
        "result = {'Site':site,'Building':building,'Ground':ground,'Wall':wall,"
        "'Slab':slab,'Column':column,'Opening':opening}\n"
    )


def _create_arguments() -> dict:
    return {
        "program_name": "Native BIM Building",
        "source": _source(),
        "input_schema": {
            "type": "object",
            "properties": {
                "length": {"type": "number", "minimum": 1000, "maximum": 10000},
                "address": {"type": "string", "maxLength": 1024},
                "city": {"type": "string", "maxLength": 256},
                "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                "longitude": {"type": "number", "minimum": -180, "maximum": 180},
            },
            "required": ["length", "address", "city", "latitude", "longitude"],
            "additionalProperties": False,
        },
        "inputs": {
            "length": 4000,
            "address": "100 Production Way",
            "city": "Chicago",
            "latitude": 41.8781,
            "longitude": -87.6298,
        },
        "expected_outputs": EXPECTED_OUTPUTS,
    }


def _object_map(document, live_outputs: dict) -> dict:
    result = {
        name: document.getObject(details["object_name"])
        for name, details in live_outputs.items()
    }
    assert all(result.values())
    return result


def _assert_native_graph(objects: dict, *, length: float) -> None:
    from draftutils.utils import get_type

    expected = {
        "Site": ("Part::FeaturePython", "_Site", "Site", "Site"),
        "Building": ("App::GeometryPython", "BuildingPart", "BuildingPart", "Building"),
        "Ground": (
            "App::GeometryPython",
            "BuildingPart",
            "BuildingPart",
            "Building Storey",
        ),
        "Wall": ("Part::FeaturePython", "_Wall", "Wall", "Wall"),
        "Slab": ("Part::FeaturePython", "_Structure", "Structure", "Slab"),
        "Column": ("Part::FeaturePython", "_Structure", "Structure", "Column"),
        "Opening": (
            "Part::FeaturePython",
            "_Window",
            "Window",
            "Opening Element",
        ),
    }
    for name, contract in expected.items():
        obj = objects[name]
        assert (
            obj.TypeId,
            type(getattr(obj, "Proxy", None)).__name__,
            str(get_type(obj) or ""),
            str(obj.IfcType),
        ) == contract
        assert "VibeCADBIMValidation" in obj.PropertiesList
    assert objects["Building"] in objects["Site"].Group
    assert objects["Ground"] in objects["Building"].Group
    for name in ("Wall", "Slab", "Column", "Opening"):
        assert objects[name] in objects["Ground"].Group
    assert list(objects["Opening"].Hosts) == [objects["Wall"]]
    assert objects["Opening"].Shape.isNull()
    assert objects["Wall"].Base is not None and get_type(objects["Wall"].Base) == "Wire"
    assert objects["Slab"].Base is not None and get_type(objects["Slab"].Base) == "Wire"
    assert objects["Opening"].Base is not None
    assert len(objects["Wall"].Shape.Solids) >= 1
    assert len(objects["Slab"].Shape.Solids) == 1
    assert len(objects["Column"].Shape.Solids) == 1
    expected_wall_volume = length * 200.0 * 2800.0 - 900.0 * 1200.0 * 200.0
    assert abs(float(objects["Wall"].Shape.Volume) - expected_wall_volume) <= 1.0e-3
    assert abs(float(objects["Slab"].Shape.Volume) - length * 3000.0 * 200.0) <= 1.0e-3
    assert abs(float(objects["Column"].Shape.Volume) - 300.0 * 300.0 * 2800.0) <= 1.0e-3


def _exercise_native_variants(root: Path) -> None:
    """Cover every exposed BIM enum/placement/host orientation in native code."""

    from draftutils.utils import get_type

    expected_outputs = [
        {"name": "Site", "type": "site"},
        {"name": "Building", "type": "building"},
        {"name": "Level", "type": "level"},
        {"name": "LeftWall", "type": "wall"},
        {"name": "RightWall", "type": "wall"},
        {"name": "ClosedWall", "type": "wall"},
        {"name": "Slab", "type": "slab"},
        {"name": "Column", "type": "structure"},
        {"name": "Beam", "type": "structure"},
        {"name": "Member", "type": "structure"},
        {"name": "VerticalOpening", "type": "opening"},
        {"name": "ReverseOpening", "type": "opening"},
    ]
    source = (
        "site=api.site(address='1 Variant Way',postal_code='60601',city='Chicago',"
        "region='Illinois',country='US',latitude=41.88,longitude=-87.63,"
        "elevation=125.5,label='Variant Site')\n"
        "building=api.building(site,label='Variant Building')\n"
        "level=api.level(building,3500,height=3200,label='Upper Level')\n"
        "left=api.wall(level,[[0,0],[4000,0],[4000,3000]],width=240,height=3000,"
        "alignment='left',offset=40,label='Left Wall')\n"
        "right=api.wall(level,[[0,6000],[3500,6000]],width=220,height=2900,"
        "alignment='right',offset=-30,label='Right Wall')\n"
        "closed=api.wall(level,[[8000,0],[12000,0],[12000,2500],[8000,2500]],"
        "closed=True,width=180,height=2800,label='Closed Wall')\n"
        "slab=api.slab(level,[[0,0],[0,3000],[4000,3000],[4000,0]],"
        "thickness=225,top_offset=200,label='Clockwise Slab')\n"
        "column=api.structure(level,350,350,3000,placement={'position':[500,500,0],"
        "'rotation':[0,0,0,1]},role='column',label='Column')\n"
        "beam=api.structure(level,4000,250,400,placement={'position':[0,0,2600],"
        "'rotation':[0,0,0.7071067811865476,0.7071067811865476]},"
        "role='beam',label='Beam')\n"
        "member=api.structure(level,200,200,1500,placement={'position':[1000,1000,0],"
        "'rotation':[0,0,0,2]},role='member',label='Member')\n"
        "vertical=api.opening(left,800,1000,segment=1,offset=500,sill=1100,"
        "hole_depth=400,label='Vertical Opening')\n"
        "reverse=api.opening(closed,900,1200,segment=2,offset=1000,sill=800,"
        "label='Reverse Opening')\n"
        "result={'Site':site,'Building':building,'Level':level,'LeftWall':left,"
        "'RightWall':right,'ClosedWall':closed,'Slab':slab,'Column':column,"
        "'Beam':beam,'Member':member,'VerticalOpening':vertical,"
        "'ReverseOpening':reverse}\n"
    )
    document = App.newDocument("VibeScriptBIMVariants")
    service = _Service(root)
    try:
        captured = _captured(
            root,
            document,
            operation="create_program",
            arguments={
                "program_name": "Native BIM Variants",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "inputs": {},
                "expected_outputs": expected_outputs,
            },
        )
        prepared, execution, validated = _prepare_execute_validate(captured)
        assert execution["bim_validation"]["native_object_count"] == 12
        assert execution["bim_validation"]["native_base_count"] == 6
        retain_candidate(prepared, status="validated")
        accepted = accept_candidate(
            prepared,
            publish_candidate(service, prepared, validated),
        )
        objects = _object_map(document, accepted["live_outputs"])
        assert float(objects["Site"].Elevation.Value) == 125.5
        assert str(objects["Site"].PostalCode) == "60601"
        assert str(objects["Site"].Region) == "Illinois"
        assert str(objects["Site"].Country) == "US"
        assert float(objects["Level"].Placement.Base.z) == 3500.0
        assert str(objects["LeftWall"].Align) == "Left"
        assert float(objects["LeftWall"].Offset.Value) == 40.0
        assert str(objects["RightWall"].Align) == "Right"
        assert float(objects["RightWall"].Offset.Value) == -30.0
        assert bool(objects["ClosedWall"].Base.Closed) is True
        assert all(float(point.z) == 200.0 for point in objects["Slab"].Base.Points)
        assert [
            str(objects[name].IfcType) for name in ("Column", "Beam", "Member")
        ] == ["Column", "Beam", "Member"]
        assert list(objects["VerticalOpening"].Hosts) == [objects["LeftWall"]]
        assert list(objects["ReverseOpening"].Hosts) == [objects["ClosedWall"]]
        assert float(objects["VerticalOpening"].HoleDepth.Value) == 400.0
        for name in ("LeftWall", "RightWall", "ClosedWall", "Slab"):
            assert get_type(objects[name].Base) == "Wire"
        validated_by_name = {item["name"]: item for item in validated["outputs"]}
        for name in ("LeftWall", "ClosedWall"):
            expected_volume = float(validated_by_name[name]["bim_data"]["final_volume"])
            assert abs(float(objects[name].Shape.Volume) - expected_volume) <= 1.0e-4
        document.recompute()
        for name in ("LeftWall", "ClosedWall"):
            expected_volume = float(validated_by_name[name]["bim_data"]["final_volume"])
            assert abs(float(objects[name].Shape.Volume) - expected_volume) <= 1.0e-4
        assert len(_managed_names(document, prepared["program_id"])) == 18

        delete_capture = _captured(
            root,
            document,
            operation="delete_program",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": prepared["revision"],
                "reason": "BIM variant coverage complete",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        assert (
            finish_delete(
                prepared_delete,
                delete_live_program(service, prepared_delete),
            )["ok"]
            is True
        )
        assert not _managed_names(document, prepared["program_id"])
    finally:
        App.closeDocument(document.Name)


def _managed_names(document, program_id: str) -> set[str]:
    return {
        str(obj.Name)
        for obj in document.Objects
        if str(getattr(obj, PROP_PROGRAM_ID, "") or "") == program_id
    }


def _snapshot(objects: dict, document, program_id: str) -> dict:
    return {
        "names": {name: str(obj.Name) for name, obj in objects.items()},
        "managed_names": sorted(_managed_names(document, program_id)),
        "wall_volume": float(objects["Wall"].Shape.Volume),
        "slab_volume": float(objects["Slab"].Shape.Volume),
        "groups": {
            name: [str(child.Name) for child in list(objects[name].Group)]
            for name in ("Site", "Building", "Ground")
        },
        "hosts": [str(host.Name) for host in list(objects["Opening"].Hosts)],
        "bases": {
            name: str(objects[name].Base.Name) for name in ("Wall", "Slab", "Opening")
        },
        "revisions": {
            name: str(obj.VibeCADVibeScriptRevision) for name, obj in objects.items()
        },
    }


def _assert_snapshot(objects: dict, document, program_id: str, expected: dict) -> None:
    observed = _snapshot(objects, document, program_id)
    for key, expected_value in expected.items():
        assert observed[key] == expected_value, json.dumps(
            {
                "key": key,
                "expected": expected_value,
                "observed": observed[key],
            },
            sort_keys=True,
        )


def main() -> int:
    _exercise_source_api()
    root = Path(tempfile.mkdtemp(prefix="vibecad-bim-native-"))
    document = App.newDocument("VibeScriptBIMNative")
    service = _Service(root)
    try:
        _exercise_native_variants(root)
        App.setActiveDocument(document.Name)
        create_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments=_create_arguments(),
        )
        prepared, execution, validated = _prepare_execute_validate(create_capture)
        assert execution["bim_validation"]["native_object_count"] == 7
        assert execution["bim_validation"]["native_base_count"] == 3
        assert len(validated["outputs"]) == 7

        malformed = copy.deepcopy(execution)
        malformed["outputs"][3]["bim_data"]["final_volume"] += 10_000.0
        try:
            validate_candidate(prepared, malformed)
        except ValueError as exc:
            assert "volume" in str(exc)
        else:
            raise AssertionError("Malformed BIM volume diagnostics were accepted.")

        graph_definitions = {
            item["name"]: copy.deepcopy(item["definition"])
            for item in execution["outputs"]
        }
        graph_definitions["Opening"]["arguments"][0]["properties"]["label"] = "Forged"
        _expect_error(
            "modified copy of parent",
            lambda: validate_bim_graph(
                graph_definitions,
                EXPECTED_OUTPUTS,
                require_domain_values=False,
            ),
        )

        retain_candidate(prepared, status="validated")
        publication = publish_candidate(service, prepared, validated)
        accepted = accept_candidate(prepared, publication)
        live_names = {
            name: details["object_name"]
            for name, details in accepted["live_outputs"].items()
        }
        objects = _object_map(document, accepted["live_outputs"])
        _assert_native_graph(objects, length=4000.0)
        assert len(_managed_names(document, prepared["program_id"])) == 10
        internal_outputs = {
            str(getattr(obj, PROP_PROGRAM_OUTPUT, "") or "")
            for obj in document.Objects
            if str(getattr(obj, PROP_PROGRAM_ID, "") or "") == prepared["program_id"]
        }
        assert {"Wall.__base", "Slab.__base", "Opening.__base"} <= internal_outputs

        inspection = complete_inspection(
            {
                **create_capture,
                "program_id": prepared["program_id"],
                "live_programs": [],
            }
        )
        assert inspection["ok"] is True
        assert inspection["program"]["accepted_revision"] == prepared["revision"]
        inspected_wall = inspection["program"]["live_outputs"]["Wall"]["bim_data"]
        assert inspected_wall["opening_graph_ids"]
        expected_delta = float(inspected_wall["expected_opening_volume_delta"])
        observed_delta = float(inspected_wall["observed_opening_volume_delta"])
        assert abs(expected_delta - observed_delta) <= max(
            1.0e-4,
            abs(expected_delta) * 1.0e-7,
        )
        inspected_opening = inspection["program"]["live_outputs"]["Opening"][
            "bim_data"
        ]
        assert inspected_opening["host_count"] == 1
        assert inspected_opening["opening_shape_null"] is True

        # The provider graph is exact, but human-created whole-object links and
        # foreign members in a managed Storey remain intact across regeneration.
        consumer = document.addObject("App::FeaturePython", "HumanWallConsumer")
        consumer.addProperty("App::PropertyLink", "Source")
        consumer.Source = objects["Wall"]
        foreign_member = document.addObject("App::FeaturePython", "HumanStoreyMember")
        objects["Ground"].addObject(foreign_member)

        failed_capture = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": prepared["revision"],
                "patch": {"length": 1200},
            },
        )
        failed_prepared = prepare_candidate(failed_capture)
        failed_execution = execute_candidate(failed_prepared, cancellation_check=None)
        assert failed_execution["ok"] is False
        assert failed_execution["observed"]["details"]["stage"] == "opening_fit"
        assert failed_execution["domain_failure_stage"] == "opening_fit"
        assert failed_execution["retry"]["required_changes"] == [
            failed_execution["observed"]["details"]["correction"]
        ]
        assert "adjust offset/width" in failed_execution["retry"][
            "required_changes"
        ][0]
        retain_candidate(failed_prepared, status="rejected", failure=failed_execution)
        assert consumer.Source is objects["Wall"]
        _assert_native_graph(objects, length=4000.0)

        update_capture = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": failed_prepared["revision"],
                "patch": {"length": 4500},
            },
        )
        update_prepared, update_execution, update_validated = _prepare_execute_validate(
            update_capture
        )
        retain_candidate(update_prepared, status="validated")
        before_fault = _snapshot(objects, document, prepared["program_id"])
        original_configure = publication_module._configure_bim

        def fail_after_wall(*args, **kwargs):
            original_configure(*args, **kwargs)
            item = args[1]
            if item["name"] == "Wall":
                raise RuntimeError("injected BIM publication failure")

        publication_module._configure_bim = fail_after_wall
        try:
            try:
                publish_candidate(service, update_prepared, update_validated)
            except RuntimeError as exc:
                assert "injected BIM publication failure" in str(exc)
            else:
                raise AssertionError("Injected BIM publication failure did not fire.")
        finally:
            publication_module._configure_bim = original_configure
        objects = {
            name: document.getObject(object_name)
            for name, object_name in live_names.items()
        }
        _assert_snapshot(objects, document, prepared["program_id"], before_fault)

        updated_publication = publish_candidate(
            service,
            update_prepared,
            update_validated,
        )
        updated = accept_candidate(update_prepared, updated_publication)
        assert {
            name: details["object_name"]
            for name, details in updated["live_outputs"].items()
        } == live_names
        objects = _object_map(document, updated["live_outputs"])
        _assert_native_graph(objects, length=4500.0)
        assert consumer.Source is objects["Wall"]
        assert foreign_member in objects["Ground"].Group

        context = _bim_document_snapshot(document)
        assert context["object_count"] == 7
        assert len(context["objects"]) == 7
        opening_context = next(
            item for item in context["objects"] if item["ifc_type"] == "Opening Element"
        )
        assert opening_context["hosts"] == [objects["Wall"].Name]
        assert opening_context["parent_groups"] == [objects["Ground"].Name]

        save_path = root / "bim-production.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        reopened = App.openDocument(str(save_path))
        assert reopened is not None
        reopened.recompute()
        reopened_objects = {
            name: reopened.getObject(object_name)
            for name, object_name in live_names.items()
        }
        _assert_native_graph(reopened_objects, length=4500.0)
        consumer = reopened.getObject("HumanWallConsumer")
        foreign_member = reopened.getObject("HumanStoreyMember")
        assert consumer.Source is reopened_objects["Wall"]
        assert foreign_member in reopened_objects["Ground"].Group

        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": update_prepared["program_id"],
                "expected_revision": update_prepared["revision"],
                "reason": "BIM production integration complete",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        try:
            delete_live_program(service, prepared_delete)
        except RuntimeError as exc:
            assert "reference" in str(exc).lower()
            restore_prepared_delete(prepared_delete)
        else:
            raise AssertionError("A human Wall consumer did not block BIM deletion.")
        consumer.Source = None
        reopened.removeObject(consumer.Name)

        # A native transaction alone is not a sufficient rollback contract.
        # Remove one managed object, fail, and require the complete accepted
        # hierarchy (including foreign group members) to be restored exactly.
        before_delete_fault = _snapshot(
            reopened_objects, reopened, update_prepared["program_id"]
        )
        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": update_prepared["program_id"],
                "expected_revision": update_prepared["revision"],
                "reason": "exercise BIM deletion rollback",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        original_remove = publication_module._remove_owned_objects

        def fail_after_one_removal(active_document, managed_objects):
            first = next(iter(managed_objects))
            active_document.removeObject(first.Name)
            raise RuntimeError("injected BIM deletion failure")

        publication_module._remove_owned_objects = fail_after_one_removal
        try:
            try:
                delete_live_program(service, prepared_delete)
            except RuntimeError as exc:
                assert "injected BIM deletion failure" in str(exc)
                restore_prepared_delete(prepared_delete)
            else:
                raise AssertionError("Injected BIM deletion failure did not fire.")
        finally:
            publication_module._remove_owned_objects = original_remove
        reopened_objects = {
            name: reopened.getObject(object_name)
            for name, object_name in live_names.items()
        }
        _assert_snapshot(
            reopened_objects,
            reopened,
            update_prepared["program_id"],
            before_delete_fault,
        )
        assert foreign_member in reopened_objects["Ground"].Group

        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": update_prepared["program_id"],
                "expected_revision": update_prepared["revision"],
                "reason": "BIM production integration complete",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        deletion = delete_live_program(service, prepared_delete)
        finished = finish_delete(prepared_delete, deletion)
        assert finished["ok"] is True
        assert not _managed_names(reopened, update_prepared["program_id"])
        assert reopened.getObject(foreign_member.Name) is foreign_member
        App.closeDocument(reopened.Name)
        print(
            json.dumps(
                {
                    "ok": True,
                    "integration": "bim_vibescript_api",
                    "stable_outputs": live_names,
                    "native_objects": 7,
                    "stable_internal_bases": 3,
                    "explicit_publication_rollback": True,
                    "explicit_deletion_rollback": True,
                    "save_reopen_recompute": True,
                    "external_reference_guard": True,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        if App.ActiveDocument is not None:
            App.closeDocument(App.ActiveDocument.Name)
        shutil.rmtree(root, ignore_errors=True)
        if hasattr(Gui, "getMainWindow"):
            Gui.getMainWindow().close()


if __name__ == "__main__":
    result_code = main()
    if result_code:
        raise RuntimeError(f"BIM VibeScript integration failed with {result_code}.")
