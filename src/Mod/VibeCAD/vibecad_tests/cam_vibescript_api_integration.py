# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native production gate for the canonical CAM VibeScript worker."""

from __future__ import annotations

from contextlib import ExitStack
import copy
import hashlib
import inspect
import json
from pathlib import Path as FilesystemPath
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

MODULE_ROOT = FilesystemPath(__file__).resolve().parent.parent
while str(MODULE_ROOT) in sys.path:
    sys.path.remove(str(MODULE_ROOT))
sys.path.insert(0, str(MODULE_ROOT))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402
import Path as PathModule  # noqa: E402
import Path.Main.Simulation as NativeSimulation  # noqa: E402

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    accept_candidate,
    capture_reference_inputs,
    execute_candidate,
    finish_delete,
    finalize_candidate,
    prepare_candidate,
    prepare_delete,
    retain_candidate,
    restore_prepared_delete,
    validate_candidate,
)
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    PROP_CAM_VALIDATION,
    delete_live_program,
    publish_candidate,
)
import VibeCADVibeScriptDomainPublication as publication_module  # noqa: E402
from VibeCADVibeScriptDomains import (  # noqa: E402
    PROP_PROGRAM_ID,
    PROP_PROGRAM_REVISION,
    complete_domain_context,
    domain_context_snapshot,
    get_domain_adapter,
)
from VibeCADVibeScriptDomains import get_vibescript_pack  # noqa: E402
from vibescript_cam_api import CAMAPIError, CAMDomainAPI  # noqa: E402
from vibescript_cam_worker import (  # noqa: E402
    CAMCandidateError,
    VALIDATION_SCHEMA,
    configure_cam_references,
    path_to_records,
    validate_and_build_cam,
    validate_cam_definition,
)
from vibescript_part_worker import part_shape_facts  # noqa: E402


EXPORTS = (
    "job",
    "stock",
    "tool",
    "operation",
    "generate_toolpath",
    "postprocess",
)
OUTPUT_TYPES = ("job", "stock", "tool", "operation", "toolpath")
EXPECTED_OUTPUTS = [
    {"name": "Job", "type": "job"},
    {"name": "Stock", "type": "stock"},
    {"name": "Tool", "type": "tool"},
    {"name": "Profile", "type": "operation"},
    {"name": "Toolpath", "type": "toolpath"},
]


class _Service:
    def __init__(self, root: FilesystemPath) -> None:
        self.root = root

    @staticmethod
    def _active_document():
        return App.ActiveDocument

    @staticmethod
    def active_workbench_name() -> str:
        return "CAMWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "cam-native-fixture-revision"

    def project_scope_snapshot(self) -> dict[str, str]:
        return {"root": str(self.root), "project_id": "cam-native-fixture"}

    @staticmethod
    def provider_working_set() -> dict[str, object]:
        return {"target_count": 0, "targets": []}

    @staticmethod
    def selection_summary() -> dict[str, object]:
        return {"selection": []}


def _sha256(path: FilesystemPath) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _api() -> CAMDomainAPI:
    return CAMDomainAPI(EXPORTS, OUTPUT_TYPES)


def _expect_error(fragment: str, call) -> None:
    try:
        call()
    except (TypeError, ValueError, RuntimeError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f"Expected CAM failure containing {fragment!r}.")


def _expect_candidate_error(stage: str, call) -> CAMCandidateError:
    try:
        call()
    except CAMCandidateError as exc:
        assert exc.details.get("stage") == stage, exc.details
        assert str(exc.details.get("correction") or "").strip(), exc.details
        return exc
    raise AssertionError(f"Expected CAM candidate failure at {stage!r}.")


def _exercise_api(document_uid: str) -> dict[str, object]:
    api = _api()
    assert api.exported_names == EXPORTS
    for redundant in (
        "output",
        "endmill",
        "ballend",
        "drill",
        "profile",
        "pocket",
        "face",
        "grbl",
        "linuxcnc",
        "export",
    ):
        assert not hasattr(api, redundant), redundant
    for name in EXPORTS:
        signature = str(inspect.signature(getattr(api, name)))
        assert "*args" not in signature and "**" not in signature, signature
        assert inspect.getdoc(getattr(api, name))

    reference = {"document_uid": document_uid, "object_name": "SourceSolid"}
    stock = api.stock(
        [reference],
        x_negative_mm=2,
        x_positive_mm=2,
        y_negative_mm=2,
        y_positive_mm=2,
        z_negative_mm=1,
        z_positive_mm=2,
        label="Validated Stock",
    )
    tool = api.tool(
        "endmill",
        diameter_mm=3,
        length_mm=30,
        flutes=2,
        tool_number=7,
        spindle_rpm=12000,
        horizontal_feed_mm_per_min=600,
        vertical_feed_mm_per_min=180,
        cutting_edge_height_mm=15,
        shank_diameter_mm=3,
        label="Three Millimeter Endmill",
    )
    profile = api.operation(
        "profile",
        tool,
        selections=[],
        start_depth_mm=10,
        final_depth_mm=0,
        step_down_mm=5,
        side="outside",
        label="Outside Profile",
    )
    generated = api.generate_toolpath(
        stock,
        [profile],
        simulation_resolution_mm=2,
        require_collision_free=True,
        label="Generated Path",
    )
    toolpath = api.postprocess(
        generated,
        processor="grbl",
        units="metric",
        comments=True,
        line_numbers=False,
        label="GRBL Program",
    )
    job = api.job(
        [reference],
        stock,
        [tool],
        [profile],
        toolpath,
        geometry_tolerance_mm=0.01,
        fixtures=["G54"],
        description="Native CAM worker integration fixture.",
        label="VibeScript CAM Job",
    )
    result = {
        "Job": job,
        "Stock": stock,
        "Tool": tool,
        "Profile": profile,
        "Toolpath": toolpath,
    }
    assert [
        validate_cam_definition(value)["operation"] for value in result.values()
    ] == ["job", "stock", "tool", "operation", "postprocess"]
    try:
        api.tool(
            "drill",
            diameter_mm=20,
            length_mm=1,
            flutes=2,
            tool_number=8,
            spindle_rpm=1000,
            horizontal_feed_mm_per_min=100,
            vertical_feed_mm_per_min=50,
            tip_angle_deg=118,
        )
    except CAMAPIError as exc:
        assert exc.details["stage"] == "source_validation"
        assert exc.details["operation"] == "tool"
        assert exc.details["parameter"] == "length_mm"
        assert "Change only the failing source expression" in exc.details[
            "correction"
        ]
    else:
        raise AssertionError("Expected one structured CAM source failure.")
    return result


def _exercise_native_worker() -> None:
    surface = resolve_modeling_surface("CAMWorkbench", "vibescript")
    assert surface.available, surface.unavailable_reason
    assert len(surface.tool_names) == 10
    assert tuple(
        name for name in surface.tool_names if name.startswith("vibescript.")
    ) == tuple(
        f"vibescript.cam.{operation}"
        for operation in (
            "create_program",
            "edit_source",
            "set_inputs",
            "reconfigure_program",
            "delete_program",
        )
    )
    assert not any(name.startswith("native.") for name in surface.tool_names)
    adapter = get_domain_adapter("cam")
    assert adapter is not None and adapter.production_ready
    description = adapter.describe_api()
    assert [item["name"] for item in description["runtime_exports"]] == list(
        EXPORTS
    )
    assert "explicit selectors" in description["redundancy_contract"]
    encoded_description = json.dumps(
        description,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded_description) < 32 * 1024
    assert "absolute Z coordinates" in description[
        "coordinate_and_depth_contract"
    ]["depths"]
    assert "initial job stock" in description["simulation_contract"][
        "operation_scope"
    ]
    assert "machine_configured=false" in description[
        "fixture_and_machine_contract"
    ]["machine"]
    assert "cannot switch workbench or engine" in description[
        "workbench_handoffs"
    ]["rule"]

    with tempfile.TemporaryDirectory(prefix="vibecad-cam-native-") as raw_root:
        root = FilesystemPath(raw_root).resolve()
        references = root / "references"
        references.mkdir()
        source_path = references / "source.brep"
        source_shape = Part.makeBox(20, 16, 10)
        source_shape.exportBrep(str(source_path))
        document_uid = "cam-native-worker-document"
        configure_cam_references(
            root,
            [
                {
                    "document_uid": document_uid,
                    "object_name": "SourceSolid",
                    "artifact_kind": "brep",
                    "artifact_path": "references/source.brep",
                    "brep_sha256": _sha256(source_path),
                    "shape_type": "Solid",
                    "label": "Source Solid",
                    "type_id": "Part::Feature",
                    "source_kind": "shape",
                    "source_revision": "native-worker-fixture-v1",
                    "transient_topology": False,
                    "requires_semantic_interfaces": False,
                    "published_interfaces": {},
                }
            ],
        )
        document = App.newDocument(
            "CAMVibeScriptNativeWorker",
            "CAM VibeScript Native Worker",
            True,
            True,
        )
        try:
            exact_result = _exercise_api(document_uid)
            exact_result["Unexpected"] = exact_result["Stock"]
            error = _expect_candidate_error(
                "result_contract",
                lambda: validate_and_build_cam(
                    document,
                    exact_result,
                    EXPECTED_OUTPUTS,
                    root,
                ),
            )
            assert error.details["received"][-1] == "Unexpected"
            recommended = description["recommended_patterns"][0]
            assert recommended["expected_outputs"] == EXPECTED_OUTPUTS
            namespace: dict[str, object] = {
                "api": _api(),
                "inputs": {
                    "solid": {
                        "document_uid": document_uid,
                        "object_name": "SourceSolid",
                    }
                },
            }
            exec(
                compile(str(recommended["source"]), "<cam-recommended-pattern>", "exec"),
                {"__builtins__": {}},
                namespace,
            )
            recommended_result = namespace.get("result")
            assert isinstance(recommended_result, dict)
            assert list(recommended_result) == [
                item["name"] for item in EXPECTED_OUTPUTS
            ]
            outputs, validation = validate_and_build_cam(
                document,
                recommended_result,
                EXPECTED_OUTPUTS,
                root,
            )
            assert validation["schema"] == VALIDATION_SCHEMA
            assert validation["output_count"] == 5
            assert validation["tool_count"] == 1
            assert validation["operation_count"] == 1
            assert validation["collision_free"] is True
            assert validation["postprocessor"] == "grbl"
            assert [item["name"] for item in outputs] == [
                item["name"] for item in EXPECTED_OUTPUTS
            ]
            by_name = {item["name"]: item for item in outputs}
            assert by_name["Job"]["cam_data"]["native_type"] == "Path::FeaturePython"
            assert by_name["Stock"]["artifact_kind"] == "brep"
            assert by_name["Tool"]["artifact_kind"] == "brep"
            assert by_name["Profile"]["cam_data"]["path_summary"][
                "cutting_command_count"
            ] > 0
            toolpath_data = by_name["Toolpath"]["cam_data"]
            assert toolpath_data["native_type"] == "Path::Feature"
            assert toolpath_data["path_summary"]["cutting_command_count"] > 0
            artifact = root / toolpath_data["postprocess"]["artifact_path"]
            assert artifact.is_file()
            assert _sha256(artifact) == toolpath_data["postprocess"][
                "artifact_sha256"
            ]
            assert artifact.stat().st_size == toolpath_data["postprocess"][
                "artifact_bytes"
            ]
        finally:
            App.closeDocument(document.Name)


def _exercise_selector_matrix_worker() -> None:
    """Exercise every canonical CAM selector through native construction."""

    with tempfile.TemporaryDirectory(prefix="vibecad-cam-selectors-") as raw_root:
        root = FilesystemPath(raw_root).resolve()
        references = root / "references"
        references.mkdir()
        source_path = references / "selector-source.brep"
        source_shape = Part.makeBox(24, 20, 10).cut(
            Part.makeCylinder(2, 10, App.Vector(12, 10, 0))
        )
        assert source_shape.isValid() and len(source_shape.Solids) == 1
        source_shape.exportBrep(str(source_path))
        document_uid = "cam-selector-worker-document"
        reference = {
            "document_uid": document_uid,
            "object_name": "SelectorSolid",
        }
        configure_cam_references(
            root,
            [
                {
                    **reference,
                    "artifact_kind": "brep",
                    "artifact_path": "references/selector-source.brep",
                    "brep_sha256": _sha256(source_path),
                    "shape_type": str(source_shape.ShapeType),
                    "label": "Selector source solid",
                    "type_id": "Part::Feature",
                    "source_kind": "shape",
                    "source_revision": "selector-worker-fixture-v1",
                    "transient_topology": False,
                    "requires_semantic_interfaces": False,
                    "published_interfaces": {},
                }
            ],
        )
        top_face = next(
            f"Face{index}"
            for index, face in enumerate(source_shape.Faces, start=1)
            if type(face.Surface).__name__ == "Plane"
            and abs(float(face.CenterOfMass.z) - 10.0) <= 1.0e-7
            and abs(float(face.normalAt(0.0, 0.0).z)) >= 1.0 - 1.0e-7
        )
        bore_face = next(
            f"Face{index}"
            for index, face in enumerate(source_shape.Faces, start=1)
            if type(face.Surface).__name__ == "Cylinder"
            and abs(float(face.Surface.Axis.z)) >= 1.0 - 1.0e-7
        )
        select_top = {
            "target": reference,
            "selection": {"type": "subelement", "name": top_face},
        }
        select_bore = {
            "target": reference,
            "selection": {"type": "subelement", "name": bore_face},
        }
        api = _api()
        stock = api.stock(
            [reference],
            x_negative_mm=2,
            x_positive_mm=2,
            y_negative_mm=2,
            y_positive_mm=2,
            z_negative_mm=1,
            z_positive_mm=2,
            label="Selector Stock",
        )

        def common_tool(kind: str, tool_number: int, **geometry) -> object:
            return api.tool(
                kind,
                diameter_mm=float(geometry.pop("diameter_mm", 4.0)),
                length_mm=30,
                flutes=2,
                tool_number=tool_number,
                spindle_rpm=10_000,
                horizontal_feed_mm_per_min=500,
                vertical_feed_mm_per_min=150,
                label=f"Selector {kind}",
                **geometry,
            )

        tools = {
            "Endmill": common_tool(
                "endmill",
                1,
                diameter_mm=3,
                cutting_edge_height_mm=15,
                shank_diameter_mm=3,
            ),
            "Ballend": common_tool(
                "ballend",
                2,
                cutting_edge_height_mm=15,
                shank_diameter_mm=4,
            ),
            "Drill": common_tool("drill", 3, tip_angle_deg=118),
            "Chamfer": common_tool(
                "chamfer",
                4,
                diameter_mm=6,
                cutting_edge_height_mm=10,
                shank_diameter_mm=6,
                cutting_edge_angle_deg=90,
                tip_diameter_mm=0.5,
            ),
            "VBit": common_tool(
                "vbit",
                5,
                diameter_mm=6,
                cutting_edge_height_mm=10,
                shank_diameter_mm=6,
                cutting_edge_angle_deg=60,
                tip_diameter_mm=0.5,
            ),
        }
        operations = {
            "Profile": api.operation(
                "profile",
                tools["Endmill"],
                start_depth_mm=10,
                final_depth_mm=8,
                step_down_mm=1,
                side="outside",
                label="Endmill Profile",
            ),
            "Pocket": api.operation(
                "pocket",
                tools["Ballend"],
                selections=[select_top],
                start_depth_mm=10,
                final_depth_mm=9,
                step_down_mm=0.5,
                step_over_percent=40,
                label="Ballend Pocket",
            ),
            "Drilling": api.operation(
                "drilling",
                tools["Drill"],
                selections=[select_bore],
                start_depth_mm=10,
                final_depth_mm=0,
                peck_depth_mm=2,
                label="Drill Bore",
            ),
            "Face": api.operation(
                "face",
                tools["Endmill"],
                start_depth_mm=12,
                final_depth_mm=10,
                step_down_mm=1,
                step_over_percent=40,
                boundary="stock",
                label="Stock Face",
            ),
            "ChamferProfile": api.operation(
                "profile",
                tools["Chamfer"],
                start_depth_mm=10,
                final_depth_mm=9.5,
                step_down_mm=0.5,
                side="outside",
                label="Chamfer Profile",
            ),
            "VBitProfile": api.operation(
                "profile",
                tools["VBit"],
                start_depth_mm=10,
                final_depth_mm=9.5,
                step_down_mm=0.5,
                side="outside",
                label="VBit Profile",
            ),
        }
        generated = api.generate_toolpath(
            stock,
            list(operations.values()),
            simulation_resolution_mm=5,
            require_collision_free=False,
            label="Selector Matrix Path",
        )
        toolpath = api.postprocess(
            generated,
            processor="linuxcnc",
            units="imperial",
            comments=False,
            line_numbers=True,
            label="LinuxCNC Selector Program",
        )
        job = api.job(
            [reference],
            stock,
            list(tools.values()),
            list(operations.values()),
            toolpath,
            label="Selector Matrix Job",
        )
        result = {
            "Job": job,
            "Stock": stock,
            **tools,
            **operations,
            "Toolpath": toolpath,
        }
        expected = [
            {"name": "Job", "type": "job"},
            {"name": "Stock", "type": "stock"},
            *({"name": name, "type": "tool"} for name in tools),
            *({"name": name, "type": "operation"} for name in operations),
            {"name": "Toolpath", "type": "toolpath"},
        ]
        document = App.newDocument(
            "CAMVibeScriptSelectorWorker",
            "CAM VibeScript Selector Worker",
            True,
            True,
        )
        try:
            outputs, validation = validate_and_build_cam(
                document,
                result,
                expected,
                root,
            )
            assert validation["postprocessor"] == "linuxcnc"
            assert validation["tool_count"] == len(tools)
            assert validation["operation_count"] == len(operations)
            by_name = {item["name"]: item for item in outputs}
            assert {
                name: by_name[name]["cam_data"]["kind"] for name in tools
            } == {
                "Endmill": "endmill",
                "Ballend": "ballend",
                "Drill": "drill",
                "Chamfer": "chamfer",
                "VBit": "vbit",
            }
            assert {
                by_name[name]["cam_data"]["strategy"] for name in operations
            } == {"profile", "pocket", "drilling", "face"}
            postprocess = by_name["Toolpath"]["cam_data"]["postprocess"]
            assert postprocess["processor"] == "linuxcnc"
            assert postprocess["processor_module"] == "linuxcnc_post"
            assert postprocess["processor_class"] == "Linuxcnc"
            assert postprocess["units"] == "imperial"
            assert postprocess["comments"] is False
            assert postprocess["line_numbers"] is True
            assert postprocess["arguments"] == [
                "--no-show-editor",
                "--no-header",
                "--inches",
                "--no-comments",
                "--line-numbers",
            ]
            assert postprocess["machine_configured"] is False
            assert postprocess["machine_limits_checked"] is False
            assert postprocess["configuration_scope"] == (
                "generic_postprocessor_defaults"
            )
            artifact = root / postprocess["artifact_path"]
            assert artifact.is_file() and _sha256(artifact) == postprocess[
                "artifact_sha256"
            ]
            gcode = artifact.read_text(encoding="utf-8")
            assert "G20" in gcode
            assert "(" not in gcode
            assert any(line.startswith("N") for line in gcode.splitlines())
        finally:
            App.closeDocument(document.Name)


def _exercise_native_circular_sweep_regressions() -> None:
    """Guard exact circular cutter sweeps at OCC's numerical edge cases."""

    chamfer = SimpleNamespace(
        Name="RegressionChamfer",
        ShapeID="chamfer",
        PropertiesList=[
            "Diameter",
            "CuttingEdgeHeight",
            "CuttingEdgeAngle",
            "TipDiameter",
        ],
        Diameter=6.0,
        CuttingEdgeHeight=10.0,
        CuttingEdgeAngle=90.0,
        TipDiameter=0.5,
    )
    chamfer_command = PathModule.Command(
        "G2",
        {
            "F": 8.333333333333334,
            "I": -2.1213201900765526,
            "J": -2.1213202607872326,
            "K": 0.0,
            "X": 26.999999949999996,
            "Y": 19.999999799999998,
            "Z": 9.500001,
        },
    )
    chamfer_start = App.Vector(
        26.99999995,
        24.242640320574465,
        9.50000105228711,
    )
    chamfer_sweep, chamfer_end = NativeSimulation._swept_tool(
        chamfer,
        chamfer_command,
        chamfer_start,
    )
    assert chamfer_sweep.ShapeType == "Solid"
    assert chamfer_sweep.isValid() and len(chamfer_sweep.Solids) == 1
    assert chamfer_sweep.Volume > NativeSimulation._axisymmetric_tool_solid(
        chamfer,
        App.Vector(1, 0, 0),
        chamfer_start,
    ).Volume
    assert (chamfer_end - App.Vector(26.99999995, 19.9999998, 9.500001)).Length < 1e-6

    vbit = SimpleNamespace(
        Name="RegressionVBit",
        ShapeID="v-bit",
        PropertiesList=list(chamfer.PropertiesList),
        Diameter=6.0,
        CuttingEdgeHeight=10.0,
        CuttingEdgeAngle=60.0,
        TipDiameter=0.5,
    )
    vbit_command = PathModule.Command(
        "G2",
        {
            "F": 8.333333333333334,
            "I": 1.6705786975990122e-07,
            "J": -2.999999832942134,
            "K": 0.0,
            "X": 26.121320307134418,
            "Y": 22.1213202278451,
            "Z": 9.500001,
        },
    )
    vbit_start = App.Vector(24.0, 23.0, 9.500000953674316)
    vbit_sweep, vbit_end = NativeSimulation._swept_tool(
        vbit,
        vbit_command,
        vbit_start,
    )
    assert vbit_sweep.ShapeType == "Solid"
    assert vbit_sweep.isValid() and len(vbit_sweep.Solids) == 1
    assert vbit_sweep.Volume > NativeSimulation._axisymmetric_tool_solid(
        vbit,
        App.Vector(1, 0, 0),
        vbit_start,
    ).Volume
    assert (
        vbit_end - App.Vector(26.121320307134418, 22.1213202278451, 9.500001)
    ).Length < 1e-6

    ball = SimpleNamespace(
        Name="RegressionBallEnd",
        ShapeID="ballend",
        PropertiesList=["Diameter", "CuttingEdgeHeight"],
        Diameter=4.0,
        CuttingEdgeHeight=15.0,
    )
    ball_start = App.Vector(1, 0, 0)
    ball_end = App.Vector(0, 1, 0)
    ball_edge = Part.Edge(
        Part.Arc(
            ball_start,
            App.Vector(2**-0.5, 2**-0.5, 0),
            ball_end,
        )
    )
    ball_sweep = NativeSimulation._planar_circular_axisymmetric_sweep(
        ball_edge,
        ball,
        ball_start,
        ball_end,
    )
    assert ball_sweep.ShapeType == "Solid"
    assert ball_sweep.isValid() and len(ball_sweep.Solids) == 1
    assert ball_sweep.Volume > NativeSimulation._axisymmetric_tool_solid(
        ball,
        App.Vector(1, 0, 0),
        ball_start,
    ).Volume


def _input_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "solid": {
                "type": "object",
                "x-vibecad-reference": True,
                "properties": {
                    "document_uid": {"type": "string", "minLength": 1},
                    "object_name": {"type": "string", "minLength": 1},
                },
                "required": ["document_uid", "object_name"],
                "additionalProperties": False,
            },
            "step_down": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 10,
            },
        },
        "required": ["solid", "step_down"],
        "additionalProperties": False,
    }


def _program_source() -> str:
    return (
        "stock = api.stock([inputs['solid']], x_negative_mm=2, "
        "x_positive_mm=2, y_negative_mm=2, y_positive_mm=2, "
        "z_negative_mm=1, z_positive_mm=2, label='Validated Stock')\n"
        "tool = api.tool('endmill', diameter_mm=3, length_mm=30, flutes=2, "
        "tool_number=7, spindle_rpm=12000, "
        "horizontal_feed_mm_per_min=600, vertical_feed_mm_per_min=180, "
        "cutting_edge_height_mm=15, shank_diameter_mm=3, "
        "label='Three Millimeter Endmill')\n"
        "profile = api.operation('profile', tool, selections=[], "
        "start_depth_mm=10, final_depth_mm=0, step_down_mm=inputs['step_down'], "
        "side='outside', label='Outside Profile')\n"
        "generated = api.generate_toolpath(stock, [profile], "
        "simulation_resolution_mm=2, require_collision_free=True, "
        "label='Generated Path')\n"
        "toolpath = api.postprocess(generated, processor='grbl', units='metric', "
        "comments=True, line_numbers=False, label='GRBL Program')\n"
        "job = api.job([inputs['solid']], stock, [tool], [profile], toolpath, "
        "geometry_tolerance_mm=0.01, fixtures=['G54'], "
        "description='Isolated lifecycle fixture.', label='VibeScript CAM Job')\n"
        "result = {'Job':job, 'Stock':stock, 'Tool':tool, "
        "'Profile':profile, 'Toolpath':toolpath}\n"
    )


def _captured(
    root: FilesystemPath,
    document,
    source,
    *,
    operation: str = "create_program",
    arguments: dict[str, object] | None = None,
) -> dict[str, object]:
    pack = get_vibescript_pack("CAMWorkbench")
    assert pack is not None
    if arguments is None:
        arguments = {
            "program_name": "Native CAM lifecycle fixture",
            "source": _program_source(),
            "input_schema": _input_schema(),
            "inputs": {
                "solid": {
                    "document_uid": str(document.Uid),
                    "object_name": str(source.Name),
                },
                "step_down": 5.0,
            },
            "expected_outputs": EXPECTED_OUTPUTS,
        }
    return {
        "tool_name": f"vibescript.cam.{operation}",
        "operation": operation,
        "arguments": arguments,
        "pack": pack,
        "project_root": str(root),
        "project_id": "cam-native-fixture",
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "cam-native-fixture-revision",
        "document_objects": [
            {"name": obj.Name, "label": obj.Label, "type_id": obj.TypeId}
            for obj in document.Objects
        ],
        "live_programs": [],
        "surface": resolve_modeling_surface(
            "CAMWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 120.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }


def _prepare_execute(
    captured: dict[str, object],
    service: _Service,
) -> tuple[dict[str, object], dict[str, object]]:
    prepared = prepare_candidate(captured)
    assert prepared["finalized"] is False
    snapshots = capture_reference_inputs(service, prepared)
    prepared = finalize_candidate(prepared, snapshots)
    staged_names = {
        path.name for path in FilesystemPath(prepared["staging"]).iterdir()
    }
    assert staged_names == {
        "request.json",
        "references",
        "worker.py",
        "vibescript_domain_api.py",
        "vibescript_cam_api.py",
        "vibescript_cam_worker.py",
        "vibescript_part_worker.py",
    }, sorted(staged_names)
    execution = execute_candidate(prepared, cancellation_check=None)
    return prepared, execution


def _prepare_execute_validate(
    captured: dict[str, object],
    service: _Service,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    prepared, execution = _prepare_execute(captured, service)
    assert execution.get("ok") is True, execution
    return prepared, execution, validate_candidate(prepared, execution)


def _publish_and_accept(
    service: _Service,
    document,
    prepared: dict[str, object],
    validated: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    retain_candidate(prepared, status="validated")
    assert document is service._active_document()
    publication = _publish_with_document_thread_guards(
        service,
        prepared,
        validated,
    )
    return publication, accept_candidate(prepared, publication)


def _publish_with_document_thread_guards(
    service: _Service,
    prepared: dict[str, object],
    validated: dict[str, object],
) -> dict[str, object]:
    """Fail if live CAM publication crosses its bounded-application boundary."""

    import subprocess

    import Path.Main.Job as NativeJob
    import Path.Main.Simulation as NativeSimulation
    import Path.Main.Stock as NativeStock
    import Path.Op.Drilling as NativeDrilling
    import Path.Op.MillFace as NativeFace
    import Path.Op.PocketShape as NativePocket
    import Path.Op.Profile as NativeProfile
    import Path.Post.Processor as NativePost
    import Path.Tool.Controller as NativeToolController
    import PathSimulator
    import vibescript_cam_worker as cam_worker

    guarded = [
        (FilesystemPath, "read_bytes"),
        (FilesystemPath, "read_text"),
        (FilesystemPath, "write_bytes"),
        (FilesystemPath, "write_text"),
        (subprocess, "Popen"),
        (subprocess, "run"),
        (subprocess, "call"),
        (subprocess, "check_call"),
        (subprocess, "check_output"),
        (NativeSimulation, "analyze_operation"),
        (NativePost.PostProcessorFactory, "get_post_processor"),
        (PathSimulator, "PathSim"),
        (cam_worker, "validate_and_build_cam"),
        (Part, "makeCompound"),
        (Part, "makeLoft"),
        (Part, "makeShell"),
        (Part, "makeSolid"),
    ]
    for module in (
        NativeJob,
        NativeStock,
        NativeDrilling,
        NativeFace,
        NativePocket,
        NativeProfile,
        NativeToolController,
    ):
        if hasattr(module, "Create"):
            guarded.append((module, "Create"))

    with ExitStack() as stack:
        for target, name in guarded:
            stack.enter_context(
                patch.object(
                    target,
                    name,
                    side_effect=AssertionError(
                        f"CAM document-thread publication called forbidden {name}."
                    ),
                )
            )
        return publish_candidate(service, prepared, validated)


def _live_outputs(document, accepted: dict[str, object]) -> dict[str, object]:
    result = {
        name: document.getObject(details["object_name"])
        for name, details in accepted["live_outputs"].items()
    }
    assert all(result.values())
    return result


def _managed_objects(document, program_id: str) -> list[object]:
    return [
        obj
        for obj in document.Objects
        if str(getattr(obj, PROP_PROGRAM_ID, "") or "") == program_id
    ]


def _rounded_native_state(value, *, digits: int):
    if isinstance(value, dict):
        return {
            str(key): _rounded_native_state(item, digits=digits)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rounded_native_state(item, digits=digits) for item in value]
    if isinstance(value, float):
        rounded = round(value, digits)
        return 0.0 if rounded == 0.0 else rounded
    return value


def _object_snapshot(obj) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "name": str(obj.Name),
        "label": str(obj.Label),
        "type_id": str(obj.TypeId),
        "revision": str(getattr(obj, PROP_PROGRAM_REVISION, "") or ""),
        "frozen": bool(obj.isFrozen()),
        "validation": str(getattr(obj, PROP_CAM_VALIDATION, "") or ""),
        "expressions": [
            [str(path), str(expression)]
            for path, expression in list(obj.ExpressionEngine or [])
        ],
        "human_note": str(getattr(obj, "HumanCAMNote", "") or ""),
        "human_length": str(getattr(obj, "HumanCAMLength", "") or ""),
    }
    shape = getattr(obj, "Shape", None)
    if shape is not None and not shape.isNull():
        snapshot["shape"] = _rounded_native_state(
            part_shape_facts(shape, max_subelements=64),
            digits=8,
        )
    path = getattr(obj, "Path", None)
    if path is not None and list(getattr(path, "Commands", []) or []):
        # Native Path persistence serializes through G-code text and can move
        # coordinates by sub-micron floating-point noise.  Normalize only the
        # numeric leaves; command order, names, annotations, and parameter keys
        # remain exact.
        snapshot["path"] = _rounded_native_state(
            path_to_records(path),
            digits=6,
        )
    for property_name in (
        "Stock",
        "Operations",
        "SetupSheet",
        "Model",
        "Tools",
        "Tool",
        "ToolController",
        "Base",
        "Group",
    ):
        if property_name not in obj.PropertiesList:
            continue
        property_type = str(obj.getTypeIdOfProperty(property_name) or "")
        value = getattr(obj, property_name)
        if "LinkSubList" in property_type:
            captured = [
                [str(target.Name), [str(item) for item in subelements]]
                for target, subelements in list(value or [])
            ]
        elif "LinkList" in property_type:
            captured = [str(item.Name) for item in list(value or [])]
        elif "Link" in property_type:
            captured = str(getattr(value, "Name", "") or "")
        else:
            continue
        snapshot[property_name] = captured
    return snapshot


def _exercise_isolated_lifecycle() -> None:
    with tempfile.TemporaryDirectory(prefix="vibecad-cam-lifecycle-") as raw_root:
        root = FilesystemPath(raw_root).resolve()
        document = App.newDocument("CAMVibeScriptLifecycle")
        try:
            source = document.addObject("Part::Feature", "SourceSolid")
            source.Label = "Human source solid"
            source.Shape = Part.makeBox(20, 16, 10)
            service = _Service(root)
            prepared, execution, validated = _prepare_execute_validate(
                _captured(root, document, source),
                service,
            )
            assert validated["cam_validation"] == execution["cam_validation"]
            assert validated["cam_validation"]["collision_free"] is True
            by_name = {item["name"]: item for item in validated["outputs"]}
            assert by_name["Stock"]["detached_shape"].isValid()
            assert by_name["Tool"]["detached_shape"].isValid()
            assert by_name["Profile"]["detached_path"] is not None
            assert by_name["Toolpath"]["detached_path"] is not None

            malformed = copy.deepcopy(execution)
            malformed["cam_validation"]["output_count"] -= 1
            _expect_error(
                "verdict is inconsistent",
                lambda: validate_candidate(prepared, malformed),
            )
            malformed = copy.deepcopy(execution)
            malformed["outputs"][3]["cam_data"]["path_commands"][0][
                "parameters"
            ]["X"] = 123.0
            _expect_error(
                "cam_validation.outputs",
                lambda: validate_candidate(prepared, malformed),
            )
            malformed = copy.deepcopy(execution)
            malformed["outputs"][1]["artifact_sha256"] = "0" * 64
            malformed["cam_validation"]["outputs"][1]["artifact_sha256"] = (
                "0" * 64
            )
            _expect_error(
                "unauthenticated",
                lambda: validate_candidate(prepared, malformed),
            )
            malformed = copy.deepcopy(execution)
            malformed["outputs"][4]["cam_data"]["postprocess"][
                "artifact_sha256"
            ] = "0" * 64
            malformed["cam_validation"]["outputs"][4]["native_state_sha256"] = (
                hashlib.sha256(
                    json.dumps(
                        malformed["outputs"][4]["cam_data"],
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest()
            )
            _expect_error(
                "unauthenticated",
                lambda: validate_candidate(prepared, malformed),
            )
            attempt = FilesystemPath(prepared["staging"])
            postprocess = by_name["Toolpath"]["cam_data"]["postprocess"]
            assert (attempt / postprocess["artifact_path"]).is_file()
            publication, accepted = _publish_and_accept(
                service, document, prepared, validated
            )
            attempt = FilesystemPath(accepted["attempt_directory"])
            assert attempt.is_dir()
            assert (attempt / postprocess["artifact_path"]).is_file()
            outputs = _live_outputs(document, accepted)
            assert {name: obj.TypeId for name, obj in outputs.items()} == {
                "Job": "Path::FeaturePython",
                "Stock": "Part::FeaturePython",
                "Tool": "Path::FeaturePython",
                "Profile": "Path::FeaturePython",
                "Toolpath": "Path::Feature",
            }
            import Path.Main.Job as NativeJob
            import Path.Main.Stock as NativeStock
            import Path.Op.Profile as NativeProfile
            import Path.Tool.Controller as NativeToolController

            assert isinstance(outputs["Job"].Proxy, NativeJob.ObjectJob)
            assert isinstance(outputs["Stock"].Proxy, NativeStock.StockFromBase)
            assert isinstance(outputs["Tool"].Proxy, NativeToolController.ToolController)
            assert isinstance(outputs["Profile"].Proxy, NativeProfile.ObjectProfile)
            assert getattr(outputs["Toolpath"], "Proxy", None) is None
            assert outputs["Job"].Stock is outputs["Stock"]
            assert outputs["Profile"].ToolController is outputs["Tool"]
            assert outputs["Tool"] in list(outputs["Job"].Tools.Group)
            assert outputs["Profile"] in list(outputs["Job"].Operations.Group)
            assert outputs["Job"].Model.Group
            assert all(obj.isFrozen() for obj in outputs.values())
            assert all(PROP_CAM_VALIDATION in obj.PropertiesList for obj in outputs.values())

            initial_names = {name: str(obj.Name) for name, obj in outputs.items()}
            invalid_selections = (
                "selections=[{'target':inputs['solid'],'selection':"
                "{'type':'subelement','name':'Face999'}}], "
            )
            failed, failed_execution = _prepare_execute(
                _captured(
                    root,
                    document,
                    source,
                    operation="edit_source",
                    arguments={
                        "program_id": prepared["program_id"],
                        "expected_revision": accepted["working_revision"],
                        "replacements": [
                            {"old": "selections=[], ", "new": invalid_selections}
                        ],
                    },
                ),
                service,
            )
            assert failed_execution["ok"] is False
            assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
            assert failed_execution["domain_failure_stage"] == "semantic_selection"
            failure_details = failed_execution["observed"]["details"]
            assert failure_details["requested"] == "Face999"
            assert "Face1" in failure_details["available_faces"]
            assert failed_execution["retry"]["required_changes"] == [
                failure_details["correction"]
            ]
            assert "reported available FaceN" in failure_details["correction"]
            retain_candidate(failed, status="failed", failure=failed_execution)
            for obj in outputs.values():
                assert str(getattr(obj, PROP_PROGRAM_REVISION)) == accepted[
                    "accepted_revision"
                ]

            recovered, _recovery_execution, recovered_validated = (
                _prepare_execute_validate(
                    _captured(
                        root,
                        document,
                        source,
                        operation="edit_source",
                        arguments={
                            "program_id": prepared["program_id"],
                            "expected_revision": failed["revision"],
                            "replacements": [
                                {"old": invalid_selections, "new": "selections=[], "}
                            ],
                        },
                    ),
                    service,
                )
            )
            recovery_publication, accepted = _publish_and_accept(
                service,
                document,
                recovered,
                recovered_validated,
            )
            assert recovery_publication["created_objects"] == []
            outputs = _live_outputs(document, accepted)
            assert {name: str(obj.Name) for name, obj in outputs.items()} == initial_names

            context = complete_domain_context(domain_context_snapshot(service, "cam"))
            assert context["domain"] == "cam"
            assert context["document_cam"]["objects"]
            assert context["cam_reference_candidates"]["objects"]
            accepted_objects = [
                item
                for item in context["document_cam"]["objects"]
                if item.get("program_id") == prepared["program_id"]
            ]
            assert accepted_objects
            assert any(item.get("accepted_validation") for item in accepted_objects)
            context_by_output = {
                item["program_output"]: item for item in accepted_objects
            }
            profile_validation = context_by_output["Profile"]["accepted_validation"]
            assert profile_validation["simulation"]["simulation_scope"] == (
                "single_operation_against_initial_job_stock"
            )
            assert profile_validation["simulation"]["holder_checked"] is False
            assert profile_validation["simulation"]["fixture_checked"] is False
            toolpath_validation = context_by_output["Toolpath"][
                "accepted_validation"
            ]
            assert toolpath_validation["postprocess"]["machine_configured"] is False
            assert toolpath_validation["postprocess"][
                "machine_limits_checked"
            ] is False
            encoded_context = json.dumps(context, sort_keys=True)
            assert "artifact_path" not in encoded_context
            assert "path_commands" not in encoded_context

            stable_names = {name: str(obj.Name) for name, obj in outputs.items()}
            for index, (name, obj) in enumerate(outputs.items(), start=1):
                obj.addProperty(
                    "App::PropertyString",
                    "HumanCAMNote",
                    "Human",
                    "Human-authored state preserved by CAM regeneration.",
                )
                obj.HumanCAMNote = f"preserve {name}"
                obj.addProperty(
                    "App::PropertyLength",
                    "HumanCAMLength",
                    "Human",
                    "Human-authored expression-backed state.",
                )
                obj.HumanCAMLength = float(index)
                obj.setExpression("HumanCAMLength", f"{index} mm + 2 mm")
            consumer = document.addObject("App::FeaturePython", "HumanCAMConsumer")
            consumer.addProperty("App::PropertyLinkList", "Sources")
            consumer.Sources = list(outputs.values())
            consumer_name = str(consumer.Name)
            source_name = str(source.Name)

            updated, _execution, update_validated = _prepare_execute_validate(
                _captured(
                    root,
                    document,
                    source,
                    operation="set_inputs",
                    arguments={
                        "program_id": prepared["program_id"],
                        "expected_revision": accepted["working_revision"],
                        "patch": {"step_down": 2.5},
                    },
                ),
                service,
            )
            update_publication, accepted = _publish_and_accept(
                service,
                document,
                updated,
                update_validated,
            )
            assert update_publication["created_objects"] == []
            outputs = _live_outputs(document, accepted)
            assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
            assert consumer.Sources == list(outputs.values())
            assert abs(float(outputs["Profile"].StepDown.Value) - 2.5) <= 1.0e-9
            for name, obj in outputs.items():
                assert obj.HumanCAMNote == f"preserve {name}"
                assert obj.isFrozen()

            accepted_snapshot = {
                str(obj.Name): _object_snapshot(obj)
                for obj in _managed_objects(document, prepared["program_id"])
            }
            failed, _execution, failed_validated = _prepare_execute_validate(
                _captured(
                    root,
                    document,
                    source,
                    operation="set_inputs",
                    arguments={
                        "program_id": prepared["program_id"],
                        "expected_revision": accepted["working_revision"],
                        "patch": {"step_down": 2.0},
                    },
                ),
                service,
            )
            retain_candidate(failed, status="validated")

            def fail_publication(stage: str, output_key: str, _obj) -> None:
                if stage == "before_freeze" and output_key == "Profile":
                    raise RuntimeError("injected CAM publication failure")

            with patch.object(
                publication_module,
                "_cam_publication_checkpoint",
                side_effect=fail_publication,
            ):
                _expect_error(
                    "injected CAM publication failure",
                    lambda: publish_candidate(
                        service,
                        failed,
                        failed_validated,
                    ),
                )
            observed_after_failure = {
                str(obj.Name): _object_snapshot(obj)
                for obj in _managed_objects(document, prepared["program_id"])
            }
            if observed_after_failure != accepted_snapshot:
                changed = {
                    name: {
                        "accepted": accepted_snapshot.get(name),
                        "observed": observed_after_failure.get(name),
                    }
                    for name in sorted(
                        set(accepted_snapshot) | set(observed_after_failure)
                    )
                    if accepted_snapshot.get(name)
                    != observed_after_failure.get(name)
                }
                raise AssertionError(
                    "CAM rollback changed accepted native state: "
                    + json.dumps(changed, sort_keys=True, indent=2)
                )
            assert consumer.Sources == list(outputs.values())

            recovered, _execution, recovered_validated = _prepare_execute_validate(
                _captured(
                    root,
                    document,
                    source,
                    operation="set_inputs",
                    arguments={
                        "program_id": prepared["program_id"],
                        "expected_revision": failed["revision"],
                        "patch": {"step_down": 2.5},
                    },
                ),
                service,
            )
            recovery_publication, accepted = _publish_and_accept(
                service,
                document,
                recovered,
                recovered_validated,
            )
            assert recovery_publication["created_objects"] == []
            outputs = _live_outputs(document, accepted)
            assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names

            save_snapshot = {
                str(obj.Name): _object_snapshot(obj)
                for obj in _managed_objects(document, prepared["program_id"])
            }
            save_path = root / "cam-native-publication.FCStd"
            document.recompute()
            document.saveAs(str(save_path))
            document_name = str(document.Name)
            App.closeDocument(document_name)
            document = App.openDocument(str(save_path))
            App.setActiveDocument(document.Name)
            document.recompute()
            reopened_snapshot = {
                str(obj.Name): _object_snapshot(obj)
                for obj in _managed_objects(document, prepared["program_id"])
            }
            if reopened_snapshot != save_snapshot:
                changed = {
                    name: {
                        "saved": save_snapshot.get(name),
                        "reopened": reopened_snapshot.get(name),
                    }
                    for name in sorted(set(save_snapshot) | set(reopened_snapshot))
                    if save_snapshot.get(name) != reopened_snapshot.get(name)
                }
                raise AssertionError(
                    "CAM save/reopen changed accepted native state: "
                    + json.dumps(changed, sort_keys=True, indent=2)
                )
            reopened_outputs = {
                name: document.getObject(object_name)
                for name, object_name in stable_names.items()
            }
            assert all(obj.isFrozen() for obj in reopened_outputs.values())
            assert isinstance(reopened_outputs["Job"].Proxy, NativeJob.ObjectJob)
            assert isinstance(
                reopened_outputs["Profile"].Proxy,
                NativeProfile.ObjectProfile,
            )
            reopened_consumer = document.getObject(consumer_name)
            assert reopened_consumer.Sources == list(reopened_outputs.values())

            reopened_source = document.getObject(source_name)
            delete_request = _captured(
                root,
                document,
                reopened_source,
                operation="delete_program",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": accepted["working_revision"],
                    "reason": "native CAM lifecycle gate",
                },
            )
            blocked_delete = prepare_delete(delete_request)
            try:
                _expect_error(
                    "Cannot delete",
                    lambda: delete_live_program(service, blocked_delete),
                )
            finally:
                restore_prepared_delete(blocked_delete)
            assert FilesystemPath(blocked_delete["program_directory"]).is_dir()
            document.removeObject(reopened_consumer.Name)
            prepared_delete = prepare_delete(delete_request)
            deletion = delete_live_program(service, prepared_delete)
            finished_delete = finish_delete(prepared_delete, deletion)
            assert finished_delete["deleted_objects"]
            assert finished_delete["artifacts_deleted"] is True
            assert not FilesystemPath(prepared_delete["program_directory"]).exists()
            assert not FilesystemPath(prepared_delete["trash_directory"]).exists()
            assert not _managed_objects(document, prepared["program_id"])
            assert document.getObject(source_name) is not None
        finally:
            App.closeDocument(document.Name)


def main() -> None:
    _exercise_native_circular_sweep_regressions()
    _exercise_native_worker()
    _exercise_selector_matrix_worker()
    _exercise_isolated_lifecycle()
    print("CAM VibeScript native API/worker integration passed")


if __name__ == "__main__":
    main()
