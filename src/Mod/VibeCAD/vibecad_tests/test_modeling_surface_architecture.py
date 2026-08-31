# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact modeling-surface and VibeScript v2 architecture contracts."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import zipfile

import pytest

from VibeCADModelingSurface import (
    ASSEMBLY_PLAYBACK_TOOL,
    ASSEMBLY_STOP_PLAYBACK_TOOL,
    COMPONENT_CATALOG_TOOL,
    CORE_CONVERSATION_VIEW_TOOLS,
    FASTENER_CATALOG_TOOL,
    MATERIAL_CATALOG_TOOL,
    PROVIDER_READ_TOOL_OWNERS,
    provider_read_tool_is_visible,
    resolve_modeling_surface,
    validate_surface_names,
)
from VibeCADTools import SafetyLevel, ToolSpec
import VibeCADVibeScriptDomains as domains

USER_WORKBENCHES = tuple(domains.VIBESCRIPT_WORKBENCH_PACKS)
PRODUCTION_READY_VIBESCRIPT_WORKBENCHES = frozenset(
    {
        "PartWorkbench",
        "PartDesignWorkbench",
        "SketcherWorkbench",
        "DraftWorkbench",
        "SurfaceWorkbench",
        "AssemblyWorkbench",
        "SpreadsheetWorkbench",
        "MaterialWorkbench",
        "MeshWorkbench",
        "MeshPartWorkbench",
        "PointsWorkbench",
        "ReverseEngineeringWorkbench",
        "InspectionWorkbench",
        "RobotWorkbench",
        "FemWorkbench",
        "TechDrawWorkbench",
    }
)


def test_vibescript_surface_resolution_has_no_retired_native_pack_dependency() -> None:
    source = (
        Path(__file__).resolve().parent.parent / "VibeCADModelingSurface.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )

    assert "VibeCADWorkbenchTools" not in imported_modules
    assert "get_tool_pack" not in source


def test_complete_workbench_shaped_vibescript_surface_matrix() -> None:
    assert len(USER_WORKBENCHES) == 17
    observed_ready = set()
    for workbench in USER_WORKBENCHES:
        scripted = resolve_modeling_surface(workbench, "vibescript")
        domain_pack = domains.get_vibescript_pack(workbench)
        assert domain_pack is not None
        assert scripted.domain == domain_pack.domain
        expected_core = set(CORE_CONVERSATION_VIEW_TOOLS)
        if workbench in {"PartDesignWorkbench", "AssemblyWorkbench"}:
            expected_core.add(FASTENER_CATALOG_TOOL)
        if workbench in {
            "PartDesignWorkbench",
            "AssemblyWorkbench",
            "RobotWorkbench",
        }:
            expected_core.add(COMPONENT_CATALOG_TOOL)
        if workbench in {"PartDesignWorkbench", "AssemblyWorkbench"}:
            expected_core.add(ASSEMBLY_PLAYBACK_TOOL)
            expected_core.add(ASSEMBLY_STOP_PLAYBACK_TOOL)
        if workbench in {
            "PartDesignWorkbench",
            "AssemblyWorkbench",
            "MaterialWorkbench",
        }:
            expected_core.add(MATERIAL_CATALOG_TOOL)
        assert set(scripted.core_tool_names) == expected_core
        if domain_pack.production_ready:
            observed_ready.add(workbench)
            focused_reads = tuple(
                name
                for name in PROVIDER_READ_TOOL_OWNERS
                if provider_read_tool_is_visible(
                    name,
                    workbench=workbench,
                    engine="vibescript",
                )
            )
            assert scripted.available is True
            assert scripted.unavailable_reason == ""
            assert scripted.cad_tool_names == (
                *domain_pack.provider_tool_names,
                *focused_reads,
            )
            validate_surface_names(
                workbench=workbench,
                engine="vibescript",
                names=scripted.tool_names,
                allowed_names=scripted.tool_names,
            )
            assert len(scripted.cad_tool_names) == len(
                domain_pack.provider_tool_names
            ) + len(focused_reads)
            # The component-capable surfaces add only their exact catalog/interface
            # controls; Assembly alone also exposes saved playback.
            ceiling = (
                25
                if workbench in {"PartDesignWorkbench", "AssemblyWorkbench"}
                else 21
            )
            assert len(scripted.tool_names) <= ceiling
            assert "core.inspect" not in scripted.tool_names
            namespaces = {
                name.split(".")[1]
                for name in scripted.cad_tool_names
                if name.startswith("vibescript.") and name.count(".") == 2
            }
            assert namespaces == set()
            assert set(domain_pack.provider_tool_names) <= set(scripted.cad_tool_names)
        else:
            assert scripted.available is False
            assert scripted.cad_tool_names == ()
            assert scripted.tool_names == scripted.core_tool_names
            assert "production-readiness gate" in scripted.unavailable_reason
            assert not any(
                name.startswith("vibescript.") for name in scripted.tool_names
            )
    assert observed_ready == PRODUCTION_READY_VIBESCRIPT_WORKBENCHES


@pytest.mark.parametrize(
    "workbench",
    (None, "NoneWorkbench", "TestWorkbench", "UnregisteredWorkbench"),
)
def test_unsupported_surfaces_are_precise_and_core_only(
    workbench: str | None,
) -> None:
    surface = resolve_modeling_surface(workbench, "vibescript")
    assert surface.available is False
    assert surface.cad_tool_names == ()
    assert surface.unavailable_reason
    assert set(surface.tool_names) == set(CORE_CONVERSATION_VIEW_TOOLS)


def test_mixed_and_cross_domain_surfaces_are_rejected() -> None:
    part = resolve_modeling_surface("PartWorkbench", "vibescript")
    foreign_workbench_tool = "mesh.list_meshes"
    with pytest.raises(ValueError, match="foreign read"):
        validate_surface_names(
            workbench="PartWorkbench",
            engine="vibescript",
            names=[*part.tool_names, foreign_workbench_tool],
            allowed_names=[*part.tool_names, foreign_workbench_tool],
        )
    with pytest.raises(ValueError, match="active domain namespace"):
        validate_surface_names(
            workbench="PartWorkbench",
            engine="vibescript",
            names=[
                "vibescript.part.create_program",
                "vibescript.assembly.create_program",
            ],
        )


def test_domain_lifecycle_schemas_are_stable_and_domain_specific() -> None:
    for workbench in USER_WORKBENCHES:
        pack = domains.get_vibescript_pack(workbench)
        assert pack is not None
        specs = domains.domain_tool_specs(pack)
        assert tuple(spec["name"] for spec in specs) == tuple(
            name for name in pack.tool_names if name.count(".") == 2
        )
        assert len(specs) == 4
        for raw in specs:
            spec = ToolSpec.from_mapping(raw)
            assert spec.workbench == workbench
            assert spec.parameters["additionalProperties"] is False
        create = next(spec for spec in specs if spec["name"].endswith("create_program"))
        output_enum = create["parameters"]["properties"]["expected_outputs"]["items"][
            "properties"
        ]["type"]["enum"]
        assert output_enum == list(pack.output_types)


def test_shared_vibescript_lifecycle_is_unambiguous_for_the_operating_model() -> None:
    universal = {spec["name"]: spec for spec in domains.universal_tool_specs()}
    assert set(universal) == {
        "vibescript.read_source",
        "vibescript.read_operation",
        "vibescript.read_api",
        "vibescript.read_geometry",
        "vibescript.read_placement",
        "vibescript.create_part",
        "vibescript.create_assembly",
        "vibescript.create_program",
        "vibescript.build_program",
        "vibescript.edit_source",
        "vibescript.set_inputs",
        "vibescript.reconfigure_program",
        "vibescript.delete_output",
        "vibescript.delete_program",
        "vibescript.delete_object",
    }
    build = universal["vibescript.build_program"]
    assert build["parameters"]["required"] == [
        "program",
        "expected_revision",
    ]
    read_geometry = universal["vibescript.read_geometry"]
    assert read_geometry["parameters"]["required"] == ["reference"]
    geometry_properties = read_geometry["parameters"]["properties"]
    assert geometry_properties["reference"]["x-vibecad-reference"] is True
    assert geometry_properties["analysis_level"]["enum"] == ["topology", "full"]
    assert geometry_properties["max_subelements"]["maximum"] == 32
    query_schema = geometry_properties["queries"]["items"]
    assert query_schema["required"] == ["name", "element_type"]
    assert query_schema["properties"]["element_type"]["enum"] == ["face", "edge"]
    assert query_schema["properties"]["max_results"]["maximum"] == 16
    read_placement = universal["vibescript.read_placement"]
    assert read_placement["parameters"]["required"] == ["operation"]
    placement_properties = read_placement["parameters"]["properties"]
    assert placement_properties["operation"]["enum"] == ["sketch", "box", "wedge"]
    assert placement_properties["placement"]["required"] == [
        "origin",
        "normal",
        "x_direction",
    ]
    edit = universal["vibescript.edit_source"]
    assert edit["parameters"]["required"] == [
        "program",
        "expected_revision",
        "source",
    ]
    assert "replacements" not in edit["parameters"]["properties"]
    assert {
        "input_schema",
        "inputs",
        "expected_outputs",
    } <= set(edit["parameters"]["properties"])
    delete_output = universal["vibescript.delete_output"]
    assert delete_output["parameters"]["required"] == [
        "program",
        "expected_revision",
        "output_name",
        "source",
        "reason",
    ]
    delete_object = universal["vibescript.delete_object"]
    assert delete_object["parameters"]["required"] == ["reference", "reason"]
    assert (
        delete_object["parameters"]["properties"]["reference"][
            "x-vibecad-reference"
        ]
        is True
    )
    for write_name in (
        "vibescript.create_program",
        "vibescript.edit_source",
        "vibescript.set_inputs",
        "vibescript.reconfigure_program",
        "vibescript.delete_output",
        "vibescript.delete_program",
    ):
        assert universal[write_name]["safety"] == "SAFE_WRITE"

    for workbench in USER_WORKBENCHES:
        pack = domains.get_vibescript_pack(workbench)
        assert pack is not None
        expected_provider_tools = set(universal)
        if workbench in {"PartDesignWorkbench", "AssemblyWorkbench"}:
            expected_provider_tools.remove("vibescript.create_program")
        else:
            expected_provider_tools -= {
                "vibescript.create_part",
                "vibescript.create_assembly",
            }
        assert set(pack.provider_tool_names) == expected_provider_tools
        specs = {
            spec["name"].rsplit(".", 1)[-1]: spec
            for spec in domains.domain_tool_specs(pack)
        }
        assert "Change only input values" in specs["set_inputs"]["description"]
        assert "Compatibility alias" in specs["reconfigure_program"]["description"]

        adapter = domains.get_domain_adapter(pack.domain)
        assert adapter is not None
        description = adapter.describe_api()
        operating = description["model_operating_contract"]
        assert "authoring_sequence" not in operating
        assert "vibescript.read_source" in operating["context_first"]
        assert "vibescript.read_api" in operating["context_first"]
        assert "vibescript.read_geometry" in operating["context_first"]
        assert "vibescript.read_placement" in operating["context_first"]
        assert set(operating["mutation_selection"]) == {
            "edit_source",
            "set_inputs",
            "reconfigure_program",
        }
        assert "failed candidate revision" in operating["revision_rule"]
        reference_schema = operating["input_schema_templates"][
            "stable_reference_property"
        ]
        assert reference_schema["x-vibecad-reference"] is True
        assert reference_schema["required"] == ["document_uid", "object_name"]
        assert reference_schema["properties"]["document_path"] == {
            "type": "string"
        }


def test_read_geometry_analysis_is_process_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADGeometry as geometry_worker
    import VibeCADGeometryInspection as inspection

    captured_request = {}

    def execute_job(request_path, result_path, **kwargs):
        captured_request.update(json.loads(Path(request_path).read_text()))
        assert kwargs["cancellation_check"] is None
        return {
            "ok": True,
            "geometry": {
                "faces": 1,
                "edges": 4,
                "query_results": [
                    {
                        "name": "mount",
                        "element_type": "face",
                        "matched_count": 1,
                        "matches": [
                            {
                                "index": 1,
                                "geometry_type": "Plane",
                                "center_mm": [1.0, 2.0, 3.0],
                                "origin_mm": [0.0, 0.0, 3.0],
                                "normal": [0.0, 0.0, 1.0],
                                "axis_direction": [0.0, 0.0, 1.0],
                                "x_direction": [1.0, 0.0, 0.0],
                            }
                        ],
                    }
                ],
            },
            "elapsed_seconds": 0.1,
            "elapsed_ms": 90,
        }

    monkeypatch.setattr(geometry_worker, "execute_job", execute_job)
    artifact_directory = tmp_path / "captured-brep"
    artifact_directory.mkdir()
    shape_path = artifact_directory / "shape.brep"
    shape_path.write_text("detached test shape")
    result = inspection.complete_geometry_read(
        {
            "artifact_directory": str(artifact_directory),
            "shape_path": str(shape_path),
            "shape_hash": 42,
            "reference": {"document_uid": "document", "object_name": "Motor"},
            "object": {"name": "Motor"},
            "placement": None,
            "include_subelements": True,
            "max_subelements": 7,
            "queries": [
                {
                    "name": "mount",
                    "element_type": "face",
                    "geometry_type": "Plane",
                    "max_results": 16,
                }
            ],
        }
    )

    assert captured_request["operation"] == "inspect_brep"
    assert captured_request["max_subelements"] == 7
    assert captured_request["queries"][0]["name"] == "mount"
    assert result["execution"]["mode"] == "isolated_geometry_worker"
    match = result["geometry"]["query_results"][0]["matches"][0]
    assert match["source_selector"] == {
        "type": "query",
        "element_type": "face",
        "expected_count": 1,
        "geometry_type": "Plane",
        "near_point": [1.0, 2.0, 3.0],
        "max_distance": 1.0e-6,
    }
    assert match["sketch_placement"] == {
        "origin": [0.0, 0.0, 3.0],
        "normal": [0.0, 0.0, 1.0],
        "x_direction": [1.0, 0.0, 0.0],
    }
    assert match["axis_placement"] == {
        "origin": [0.0, 0.0, 3.0],
        "axis_direction": [0.0, 0.0, 1.0],
        "x_direction": [1.0, 0.0, 0.0],
    }
    assert not artifact_directory.exists()


def test_geometry_worker_release_smoke_executes_real_brep_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADGeometry as geometry_worker

    shape = object()
    monkeypatch.setitem(
        sys.modules,
        "Part",
        SimpleNamespace(makeBox=lambda length, width, height: shape),
    )
    monkeypatch.setattr(
        geometry_worker,
        "worker_executable",
        lambda: Path("/runtime/bin/VibeCADGeometryWorker"),
    )
    expected_worker = str(Path("/runtime/bin/VibeCADGeometryWorker"))

    def validate(candidate, **kwargs):
        assert candidate is shape
        assert kwargs == {"deadline_seconds": 10.0}
        return {"ok": True, "valid": True, "elapsed_seconds": 0.125}

    monkeypatch.setattr(geometry_worker, "validate_shape", validate)

    assert geometry_worker.runtime_execution_smoke() == {
        "worker": expected_worker,
        "valid": True,
        "elapsed_seconds": 0.125,
    }


def test_every_domain_description_is_copy_ready_for_the_operating_model() -> None:
    for workbench in USER_WORKBENCHES:
        pack = domains.get_vibescript_pack(workbench)
        assert pack is not None
        adapter = domains.get_domain_adapter(pack.domain)
        assert adapter is not None
        description = adapter.describe_api()

        exports = description["runtime_exports"]
        export_names = [item["name"] for item in exports]
        assert export_names == list(pack.api_exports)
        assert len(export_names) == len(set(export_names))
        assert all(item["description"] for item in exports)
        assert all(
            "*args" not in item["signature"] and "**" not in item["signature"]
            for item in exports
        )
        assert description["accepted_output_types"] == list(pack.output_types)
        assert "exactly match expected_outputs" in description["result_contract"]
        assert description["source_value_contract"]["type"] == "DomainValue"
        assert description["source_global_contracts"]["doc"]["members"] == [
            "doc.Name",
            "doc.Objects",
            "doc.getObject(exact_name)",
            "object.Name",
            "object.Label",
            "object.TypeId",
        ]
        groups = description["api_groups"]
        grouped_names = [name for names in groups.values() for name in names]
        assert grouped_names == list(dict.fromkeys(grouped_names))
        assert set(grouped_names) == set(pack.api_exports)
        assert "redundan" in json.dumps(description).lower()
        assert len(json.dumps(description, separators=(",", ":")).encode()) < 52_000

        handoffs = json.dumps(description["workbench_handoffs"]).lower()
        assert "active workbench determines the available api" in handoffs
        assert "modeling engine" not in handoffs
        error_contract = json.dumps(description["error_contract"]).lower()
        assert "correct" in error_contract

        patterns = description.get("recommended_patterns", [])
        for pattern in patterns:
            source = pattern["source"]
            expected_outputs = pattern["expected_outputs"]
            assert pattern["goal"]
            assert expected_outputs
            domains.validate_program_source(source)
            tree = ast.parse(source)
            result_assignments = [
                node
                for node in tree.body
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "result"
                    for target in node.targets
                )
            ]
            assert len(result_assignments) == 1
            result_value = result_assignments[0].value
            assert isinstance(result_value, ast.Dict)
            result_names = [ast.literal_eval(key) for key in result_value.keys]
            expected_names = [item["name"] for item in expected_outputs]
            assert result_names == expected_names
            assert all(item["type"] in pack.output_types for item in expected_outputs)

            api_calls = {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "api"
            }
            assert api_calls <= set(pack.api_exports)


def test_inspect_program_returns_machine_readable_model_state() -> None:
    pack = domains.get_vibescript_pack("AssemblyWorkbench")
    assert pack is not None
    adapter = domains.get_domain_adapter(pack.domain)
    assert adapter is not None
    program_id = "a" * 32
    accepted_revision = "b" * 64
    accepted = adapter.inspect(
        {},
        {
            "program_id": program_id,
            "domain": pack.domain,
            "workbench": pack.workbench,
            "working_revision": accepted_revision,
            "accepted_revision": accepted_revision,
            "latest_candidate": {"status": "accepted"},
        },
    )
    assert accepted["model_state"] == {
        "status": "accepted_current",
        "candidate_status": "accepted",
        "accepted_is_current": True,
        "accepted_live_state_preserved": True,
        "next_write_expected_revision": accepted_revision,
        "mutation_selection": {
            "source_only": "vibescript.edit_source",
            "input_values_only": "vibescript.assembly.set_inputs",
            "contract_or_outputs": "vibescript.edit_source",
        },
        "instruction": (
            "The accepted contract is current; verify domain-specific live evidence."
        ),
    }

    failed_revision = "c" * 64
    failed = adapter.inspect(
        {},
        {
            "program_id": program_id,
            "domain": pack.domain,
            "workbench": pack.workbench,
            "working_revision": failed_revision,
            "accepted_revision": accepted_revision,
            "latest_candidate": {"status": "failed", "failure": {"error": "bad"}},
        },
    )
    assert failed["model_state"]["status"] == "working_candidate_not_accepted"
    assert failed["model_state"]["accepted_live_state_preserved"] is True
    assert failed["model_state"]["next_write_expected_revision"] == failed_revision
    assert failed["program"]["latest_candidate"]["failure"]["error"] == "bad"


def test_universal_read_source_returns_complete_code_and_declared_outputs() -> (
    None
):
    import VibeCADSession as session

    source = "feature = api.box(10, 20, 30)\nresult = {'Body': feature}\n"
    payload = session._read_source_payload(
        {
            "ok": True,
            "program": {
                "program_id": "a" * 32,
                "domain": "partdesign",
                "workbench": "PartDesignWorkbench",
                "label": "Bracket",
                "source": source,
                "input_schema": {"type": "object"},
                "inputs": {},
                "expected_outputs": [{"name": "Body", "type": "solid"}],
                "working_revision": "b" * 64,
                "accepted_revision": "b" * 64,
                "live_outputs": {
                    "Body": {
                        "object_name": "VibePartdesign_Body",
                        "label": "Bracket",
                        "type_id": "PartDesign::Body",
                    },
                    "Guide": {
                        "object_name": "VibePartdesign_Guide",
                        "label": "Guide",
                        "type_id": "PartDesign::Feature",
                    },
                },
            },
        }
    )

    assert payload["source_id"] == "a" * 32
    assert payload["current_revision"] == "b" * 64
    assert payload["source"] == source
    assert [item["name"] for item in payload["affected_outputs"]] == ["Body"]
    assert "object_name" not in payload["affected_outputs"][0]
    assert payload["edit_source"]["target_arguments"] == {
        "source_id": "a" * 32,
        "expected_revision": "b" * 64,
    }
    assert payload["build_program"]["arguments"] == {
        "source_id": "a" * 32,
        "expected_revision": "b" * 64,
    }
    assert payload["source_range"] == {
        "line_start": 1,
        "line_end": 2,
        "total_lines": 2,
        "complete": True,
    }
    assert payload["_vibecad_complete_source_result"] is True

    diagnostic_payload = session._read_source_payload(
        {
            "ok": True,
            "program": {
                **{
                    key: value
                    for key, value in {
                        "program_id": "a" * 32,
                        "domain": "partdesign",
                        "workbench": "PartDesignWorkbench",
                        "label": "Bracket",
                        "source": source,
                        "input_schema": {"type": "object"},
                        "inputs": {},
                        "expected_outputs": [{"name": "Body", "type": "solid"}],
                        "working_revision": "b" * 64,
                        "accepted_revision": "b" * 64,
                    }.items()
                },
                "live_outputs": {
                    "Body": {
                        "object_name": "VibePartdesign_Body",
                        "label": "Bracket",
                        "type_id": "PartDesign::Body",
                    },
                    "Guide": {
                        "object_name": "VibePartdesign_Guide",
                        "label": "Guide",
                        "type_id": "PartDesign::Feature",
                        "internal": True,
                    },
                },
            },
        },
        include_logs=True,
    )
    assert [item["name"] for item in diagnostic_payload["affected_outputs"]] == [
        "Body",
        "Guide",
    ]
    assert diagnostic_payload["affected_outputs"][0]["object_name"] == (
        "VibePartdesign_Body"
    )


def test_universal_read_source_keeps_assembly_solver_scope_explicit() -> None:
    import VibeCADSession as session

    validation_scope = {
        "scope": "joint_constraint_consistency",
        "constraints_consistent": True,
        "mechanical_operation_verified": False,
        "advisory": "A solved joint graph does not prove proper mechanism operation.",
        "required_evidence": [
            "collision_and_clearance",
            "motion_over_operating_range",
        ],
    }
    payload = session._read_source_payload(
        {
            "ok": True,
            "program": {
                "program_id": "a" * 32,
                "domain": "assembly",
                "workbench": "AssemblyWorkbench",
                "label": "Mechanism",
                "source": "result = {}\n",
                "working_revision": "b" * 64,
                "accepted_revision": "b" * 64,
                "expected_outputs": [
                    {"name": "Diagnostics", "type": "solver_diagnostics"}
                ],
                "live_state": {
                    "outputs": [
                        {
                            "name": "Diagnostics",
                            "object_name": "Diagnostics",
                            "label": "Diagnostics",
                            "output_type": "solver_diagnostics",
                            "accepted_state": {
                                "validation": {
                                    "validation_scope": validation_scope,
                                }
                            },
                        }
                    ]
                },
            },
        }
    )

    assert payload["affected_outputs"] == [
        {
            "name": "Diagnostics",
            "label": "Diagnostics",
            "output_type": "solver_diagnostics",
            "validation_scope": validation_scope,
        }
    ]


def test_universal_source_and_api_focused_reads_are_small_and_explicit() -> None:
    import VibeCADSession as session

    source = "first = 1\nsecond = 2\nthird = 3\n"
    focused_source = session._read_source_payload(
        {
            "ok": True,
            "program": {
                "program_id": "a" * 32,
                "domain": "partdesign",
                "workbench": "PartDesignWorkbench",
                "source": source,
                "working_revision": "b" * 64,
                "latest_candidate": {
                    "status": "failed",
                    "failure": {"stderr": "one\ntwo\nthree\n", "error": "bad"},
                },
            },
        },
        line_start=2,
        line_end=2,
        include_logs=False,
    )
    assert focused_source["source"] == "second = 2\n"
    assert focused_source["source_range"] == {
        "line_start": 2,
        "line_end": 2,
        "total_lines": 3,
        "complete": False,
    }
    assert focused_source["_vibecad_complete_source_result"] is False
    failure = focused_source["latest_candidate"]["failure"]
    assert failure["error"] == "bad"
    assert "stderr" not in failure

    pack = domains.get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None
    description = domains.get_domain_adapter(pack.domain).describe_api()
    focused_api = session._filtered_api_payload(
        "vibescript.read_api",
        description,
        names=["api.sketch"],
        groups=["verification"],
    )
    selected = [item["name"] for item in focused_api["runtime_exports"]]
    assert selected == [
        "sketch",
        "find_subelements",
        "measure",
        "minimum_distance",
    ]
    assert set(focused_api["api_details"]) == set(selected)
    assert focused_api["_vibecad_complete_api_result"] is False

    assembly_pack = domains.get_vibescript_pack("AssemblyWorkbench")
    assert assembly_pack is not None
    assembly_description = domains.get_domain_adapter(
        assembly_pack.domain
    ).describe_api()
    assembly_api = session._filtered_api_payload(
        "vibescript.read_api",
        assembly_description,
        names=["component"],
        groups=[],
    )
    assert [item["name"] for item in assembly_api["runtime_exports"]] == [
        "component",
        "assembly",
        "solve",
    ]

    wrong_surface = session._filtered_api_payload(
        "vibescript.read_api",
        description,
        names=["connector", "solve"],
        groups=[],
    )
    assert wrong_surface["failure_code"] == "API_FILTER_UNKNOWN"
    assert wrong_surface["candidates"] == [
        {
            "domain": "assembly",
            "workbench": "AssemblyWorkbench",
            "matching_names": ["connector", "solve"],
            "matching_groups": [],
        }
    ]
    assert wrong_surface["retry"]["required_changes"] == [
        "Switch to AssemblyWorkbench and retry this read unchanged."
    ]


def test_universal_build_program_replays_exact_saved_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session
    import VibeCADVibeScriptDomainRuntime as runtime

    source_id = "a" * 32
    revision = "b" * 64
    program = "Active/partdesign/Saved Part"
    monkeypatch.setattr(runtime, "capture_inspection_state", lambda *_args: {})
    monkeypatch.setattr(
        runtime,
        "complete_inspection",
        lambda _captured: {
            "ok": True,
            "program": {
                "program_id": source_id,
                "working_revision": revision,
                "source": "result = {'Part': api.box(1, 2, 3)}",
            },
        },
    )
    observed = {}

    def run_internal(_service, tool_name, arguments, **kwargs):
        observed.update(
            tool_name=tool_name,
            arguments=arguments,
            allow_unchanged_revision=kwargs["allow_unchanged_revision"],
        )
        return {"ok": True, "tool": tool_name, "program_id": source_id}

    monkeypatch.setattr(session, "_run_domain_vibescript_tool", run_internal)
    document = type(
        "Document",
        (),
        {"Uid": "active-document", "Name": "Active", "FileName": "", "Objects": []},
    )()
    service = type("Service", (), {"_active_document": lambda self: document})()
    result = session._run_universal_vibescript_tool(
        service,
        "PartDesignWorkbench",
        "vibescript.build_program",
        {"program": program, "expected_revision": revision},
        editable_sources={
            "sources": [
                {
                    "source_id": source_id,
                    "program": program,
                    "label": "Saved Part",
                    "domain": "partdesign",
                    "current_revision": revision,
                    "affected_outputs": [],
                }
            ]
        },
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert observed == {
        "tool_name": "vibescript.partdesign.edit_source",
        "arguments": {
            "program_id": source_id,
            "expected_revision": revision,
            "source": "result = {'Part': api.box(1, 2, 3)}",
        },
        "allow_unchanged_revision": True,
    }
    assert result["tool"] == "vibescript.build_program"
    assert result["source_id"] == source_id
    assert result["requested_action"] == "build_program"


def test_universal_create_program_targets_explicit_domain_without_ribbon_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    observed = {}

    def run_internal(bound_service, tool_name, arguments, **_kwargs):
        observed.update(
            workbench=bound_service.active_workbench_name(),
            tool_name=tool_name,
            arguments=arguments,
        )
        return {
            "ok": True,
            "tool": tool_name,
            "program_id": "a" * 32,
            "domain": "assembly",
        }

    monkeypatch.setattr(session, "_run_domain_vibescript_tool", run_internal)
    document = type("Document", (), {"Objects": []})()
    service = type(
        "Service",
        (),
        {"_active_document": lambda self: document},
    )()
    result = session._run_universal_vibescript_tool(
        service,
        "PartDesignWorkbench",
        "vibescript.create_program",
        {
            "domain": "assembly",
            "program_name": "Robot Arm Assembly",
            "source": "result = {'Arm': api.assembly([])}",
            "input_schema": {"type": "object"},
            "inputs": {},
            "expected_outputs": [{"name": "Arm", "type": "assembly"}],
        },
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert observed == {
        "workbench": "AssemblyWorkbench",
        "tool_name": "vibescript.assembly.create_program",
        "arguments": {
            "program_name": "Robot Arm Assembly",
            "source": "result = {'Arm': api.assembly([])}",
            "input_schema": {"type": "object"},
            "inputs": {},
            "expected_outputs": [{"name": "Arm", "type": "assembly"}],
        },
    }
    assert result["tool"] == "vibescript.create_program"
    assert result["source_id"] == "a" * 32


def test_universal_read_api_accepts_explicit_model_or_assembly_domain() -> None:
    import VibeCADSession as session
    import VibeCADVibeScriptDomains as domains
    from VibeCADTools import ToolSpec

    read_api_spec = next(
        item
        for item in domains.universal_tool_specs()
        if item["name"] == "vibescript.read_api"
    )
    ToolSpec.from_mapping(read_api_spec).validate_arguments(
        {"domain": "assembly", "names": ["api.joint"]}
    )

    service = type("Service", (), {"_active_document": lambda self: None})()
    result = session._run_universal_vibescript_tool(
        service,
        "AssemblyWorkbench",
        "vibescript.read_api",
        {"domain": "partdesign", "names": ["api.component", "api.instances"]},
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert result["ok"] is True
    assert result["domain"] == "partdesign"
    assert [item["name"] for item in result["runtime_exports"]] == [
        "component",
        "instances",
    ]


def test_unified_source_context_keeps_failed_programs_from_both_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    captured_workbenches = []

    def capture(bound_service, domain):
        captured_workbenches.append(bound_service.active_workbench_name())
        return {"domain": domain, "workbench": bound_service.active_workbench_name()}

    def complete(snapshot):
        domain = str(snapshot["domain"])
        return {
            "schema": "vibecad-editable-sources-v1",
            "domain": domain,
            "workbench": str(snapshot["workbench"]),
            "source_count": 1,
            "sources": [
                {
                    "source_id": ("a" if domain == "partdesign" else "b") * 32,
                    "domain": domain,
                    "status": "build_failed",
                }
            ],
            "tools": {},
        }

    monkeypatch.setattr(domains, "capture_editable_sources_snapshot", capture)
    monkeypatch.setattr(domains, "complete_editable_sources_snapshot", complete)
    service = type("Service", (), {})()

    captured = session._capture_editable_sources_for_workbench(
        service,
        "PartDesignWorkbench",
    )
    completed = session._complete_editable_sources_for_workbench(captured)

    assert captured_workbenches == ["PartDesignWorkbench", "AssemblyWorkbench"]
    assert completed["domain"] == "partdesign"
    assert completed["source_count"] == 1
    assert completed["authoring_domains"] == ["partdesign", "assembly"]
    assert [item["domain"] for item in completed["all_sources"]] == [
        "assembly",
        "partdesign",
    ]
    assert completed["all_source_count"] == 2


def test_deferred_publication_recompute_uses_exact_worker_safe_targets() -> None:
    import VibeCADSession as session

    class DocumentObject:
        State: list[str] = []

        def __init__(self, name: str) -> None:
            self.Name = name

    class Document:
        Name = "Engine"
        Recomputing = False
        RecomputePending = False

        def __init__(self) -> None:
            self.objects = {
                name: DocumentObject(name) for name in ("RotorLink", "HousingLink")
            }
            self.requests: list[tuple[list[DocumentObject], bool]] = []

        def getObject(self, name: str) -> DocumentObject | None:
            return self.objects.get(name)

        def recomputeAsync(
            self, targets: list[DocumentObject], recursive: bool
        ) -> int:
            self.requests.append((targets, recursive))
            return 1

    document = Document()
    service = type(
        "Service", (), {"_active_document": lambda _self: document}
    )()
    events: list[dict[str, object]] = []

    result = session._deferred_publication_recompute(
        service,
        {
            "recompute_deferred": True,
            "downstream_references": {
                "part_recompute_objects": [
                    "RotorLink",
                    "HousingLink",
                    "RotorLink",
                ]
            },
        },
        dispatch=lambda operation: operation(),
        cancellation_check=lambda: True,
        progress_callback=events.append,
    )

    assert result["mode"] == "worker"
    assert result["completed"] is True
    assert result["target_count"] == 2
    assert len(document.requests) == 1
    targets, recursive = document.requests[0]
    assert [target.Name for target in targets] == ["RotorLink", "HousingLink"]
    assert recursive is True
    assert events[-1]["event"] == "vibescript_domain_deferred_recompute_completed"


def test_deferred_publication_recompute_has_exact_document_thread_fallback() -> (
    None
):
    import VibeCADSession as session

    class DocumentObject:
        State: list[str] = []

        def __init__(self, name: str) -> None:
            self.Name = name

    class Document:
        Name = "Engine"
        Recomputing = False
        RecomputePending = False

        def __init__(self) -> None:
            self.target = DocumentObject("ThreadAffineLink")
            self.fallback_requests: list[
                tuple[list[DocumentObject], bool, bool]
            ] = []

        def getObject(self, name: str) -> DocumentObject | None:
            return self.target if name == self.target.Name else None

        def recomputeAsync(
            self, _targets: list[DocumentObject], _recursive: bool
        ) -> int:
            raise RuntimeError("dependency requires the document thread")

        def recompute(
            self,
            targets: list[DocumentObject],
            recursive: bool,
            raise_on_error: bool,
        ) -> int:
            self.fallback_requests.append((targets, recursive, raise_on_error))
            return 1

    document = Document()
    service = type(
        "Service", (), {"_active_document": lambda _self: document}
    )()

    result = session._deferred_publication_recompute(
        service,
        {
            "recompute_deferred": True,
            "downstream_references": {
                "part_recompute_objects": ["ThreadAffineLink"]
            },
        },
        dispatch=lambda operation: operation(),
        cancellation_check=None,
        progress_callback=None,
    )

    assert result["mode"] == "document_thread_fallback"
    assert result["completed"] is True
    assert "document thread" in result["async_rejection"]
    assert len(document.fallback_requests) == 1
    targets, recursive, raise_on_error = document.fallback_requests[0]
    assert [target.Name for target in targets] == ["ThreadAffineLink"]
    assert recursive is True
    assert raise_on_error is True


def test_native_tool_runner_reports_document_thread_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    class Spec:
        requires_document = False

        @staticmethod
        def validate_arguments(_arguments: dict[str, object]) -> None:
            return None

        @staticmethod
        def supports_edit_mode(_edit_mode: str) -> bool:
            return True

    tool = type(
        "Tool",
        (),
        {
            "safety": SafetyLevel.READ,
            "workbench": "PartDesignWorkbench",
            "spec": Spec(),
        },
    )()

    class Registry:
        @staticmethod
        def get(_name: str) -> object:
            return tool

        @staticmethod
        def call(_name: str, **_arguments: object) -> dict[str, object]:
            return {"ok": True, "value": 42}

    class Service:
        registry = Registry()

        @staticmethod
        def note_provider_tool_targets(
            _arguments: dict[str, object], _payload: dict[str, object]
        ) -> None:
            return None

    monkeypatch.setattr(
        session,
        "_live_provider_surface_state",
        lambda _service: {
            "workbench": "PartDesignWorkbench",
            "engine": "native",
            "surface_id": "native-test",
            "runtime_state": {"edit_mode": "none"},
            "tool_names": ["model.read_test"],
        },
    )
    monkeypatch.setattr(
        session,
        "_minimal_runtime_state",
        lambda _service: {"edit_mode": "none"},
    )
    events: list[dict[str, object]] = []
    trace: list[dict[str, object]] = []
    runner = session.make_provider_tool_runner(
        Service(),
        tool_trace=trace,
        progress_callback=events.append,
        cancellation_check=None,
        steering_check=None,
        question_callback=None,
        document_thread_dispatch=lambda operation: operation(),
    )

    result = runner("model.read_test", "{}")

    assert result["ok"] is True
    assert result["document_thread_elapsed_seconds"] >= 0.0
    assert [
        event["event"]
        for event in events
        if str(event["event"]).startswith("native_tool_document_phase_")
    ] == [
        "native_tool_document_phase_started",
        "native_tool_document_phase_completed",
    ]
    assert trace[0]["result"]["document_thread_elapsed_seconds"] >= 0.0


def test_vibescript_operation_manager_reports_progress_conflicts_and_result() -> None:
    import VibeCADSession as session

    manager = session._VibeScriptOperationManager()
    started = threading.Event()
    finish = threading.Event()

    def execute(operation_id: str) -> dict[str, object]:
        started.set()
        manager.record_progress(
            operation_id,
            {"event": "worker_started", "phase": "building"},
        )
        assert finish.wait(2.0)
        result = {
            "ok": True,
            "working_revision": "a" * 64,
            "source": "result = {'Arm': api.box(1, 1, 1)}",
        }
        manager.record_progress(
            operation_id,
            {
                "event": "tool_call_completed",
                "tool_name": "vibescript.edit_source",
                "ok": True,
                "result": result,
            },
        )
        return result

    response = manager.start(
        "vibescript.edit_source",
        {"program": "test/partdesign/Arm", "source": "result = {}"},
        execute,
    )
    assert response["ok"] is True
    assert response["operation"]["operation_id"] == "operation-1"
    assert response["operation"]["target"] == "test/partdesign/Arm"
    assert "result = {}" not in json.dumps(response)
    assert started.wait(1.0)

    active = manager.active()
    assert active is not None
    assert active["progress"]["phase"] == "building"
    conflict = manager.start(
        "vibescript.build_program",
        {"program": "test/partdesign/Other"},
        lambda _operation_id: {"ok": True},
    )
    assert conflict["failure_code"] == "VIBESCRIPT_OPERATION_ACTIVE"
    assert conflict["active_operation"]["operation_id"] == "operation-1"

    finish.set()
    completed = manager.read("operation-1", wait_seconds=1)
    assert completed["ok"] is True
    assert completed["operation"]["status"] == "succeeded"
    assert completed["result"]["working_revision"] == "a" * 64
    assert completed["operation"]["progress"] == {
        "event": "tool_call_completed",
        "tool_name": "vibescript.edit_source",
        "ok": True,
    }
    assert json.dumps(completed).count("result = {'Arm': api.box(1, 1, 1)}") == 1
    assert manager.active() is None


def test_provider_tool_runner_authorizes_a_failed_source_created_in_the_same_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    source_id = "a" * 32
    revision = "b" * 64
    program = "Active/partdesign/Probe"

    class Spec:
        requires_document = False

        @staticmethod
        def validate_arguments(_arguments: dict[str, object]) -> None:
            return None

        @staticmethod
        def supports_edit_mode(_edit_mode: str) -> bool:
            return True

    tool = type(
        "Tool",
        (),
        {
            "safety": SafetyLevel.WRITE,
            "workbench": "PartDesignWorkbench",
            "spec": Spec(),
        },
    )()

    class Registry:
        @staticmethod
        def get(_name: str) -> object:
            return tool

    class Service:
        registry = Registry()

        @staticmethod
        def note_provider_tool_targets(
            _arguments: dict[str, object], _payload: dict[str, object]
        ) -> None:
            return None

    editable = {
        "schema": "vibecad-editable-sources-v1",
        "domain": "partdesign",
        "workbench": "PartDesignWorkbench",
        "sources": [
            {
                "source_id": source_id,
                "program": program,
                "label": "Probe",
                "domain": "partdesign",
                "current_revision": revision,
                "affected_outputs": [],
            }
        ],
    }
    observed_indexes: list[dict[str, object] | None] = []

    def fail_index(_captured: object) -> dict[str, object]:
        raise RuntimeError("index unavailable")

    def run_universal(_service, _workbench, tool_name, _args, **kwargs):
        source_index = kwargs.get("editable_sources")
        observed_indexes.append(
            dict(source_index) if isinstance(source_index, dict) else None
        )
        if tool_name == "vibescript.create_program":
            return {
                "ok": False,
                "tool": tool_name,
                "failure_code": "CANDIDATE_VALIDATION_FAILED",
                "failure_stage": "validation",
                "error": "probe failed",
                "source_id": source_id,
                "program": program,
                "program_name": "Probe",
                "working_revision": revision,
                "_vibecad_source_lifecycle_result": True,
            }
        return {
            "ok": True,
            "tool": tool_name,
            "source_id": source_id,
            "program": program,
        }

    monkeypatch.setattr(session, "_run_universal_vibescript_tool", run_universal)
    monkeypatch.setattr(
        session,
        "_live_provider_surface_state",
        lambda _service: {
            "workbench": "PartDesignWorkbench",
            "engine": "vibescript",
            "surface_id": "vibescript-partdesign-v2",
            "runtime_state": {"edit_mode": "none"},
            "tool_names": [
                "vibescript.create_program",
                "vibescript.edit_source",
                "vibescript.read_operation",
            ],
        },
    )
    monkeypatch.setattr(
        session,
        "_minimal_runtime_state",
        lambda _service: {"edit_mode": "none"},
    )
    monkeypatch.setattr(
        domains,
        "capture_editable_sources_snapshot",
        lambda _service, _domain: {"captured": True},
    )
    monkeypatch.setattr(
        domains,
        "complete_editable_sources_snapshot",
        fail_index,
    )

    runner = session.make_provider_tool_runner(
        Service(),
        tool_trace=[],
        progress_callback=None,
        cancellation_check=None,
        steering_check=None,
        question_callback=None,
        document_thread_dispatch=lambda operation: operation(),
        turn_editable_sources={**editable, "sources": []},
    )
    failed_started = runner("vibescript.create_program", "{}")
    while True:
        failed_status = runner(
            "vibescript.read_operation",
            json.dumps(
                {
                    "operation_id": failed_started["operation"]["operation_id"],
                    "wait_seconds": 1,
                }
            ),
        )
        assert "operation" in failed_status, failed_status
        if failed_status["operation"]["status"] != "running":
            break
    failed = failed_status["result"]
    edited_started = runner(
        "vibescript.edit_source",
        json.dumps(
            {
                "program": program,
                "expected_revision": revision,
                "source": "result = {'Probe': api.box(1, 1, 1)}",
            }
        ),
    )
    while True:
        edited_status = runner(
            "vibescript.read_operation",
            json.dumps(
                {
                    "operation_id": edited_started["operation"]["operation_id"],
                    "wait_seconds": 1,
                }
            ),
        )
        assert "operation" in edited_status, edited_status
        if edited_status["operation"]["status"] != "running":
            break
    edited = edited_status["result"]

    assert failed["failure_code"] == "CANDIDATE_VALIDATION_FAILED"
    assert failed["source_id"] == source_id
    assert failed["working_revision"] == revision
    assert failed["_vibecad_source_lifecycle_result"] is True
    assert not {
        "source_id",
        "working_revision",
    } & set(failed["observed"].get("tool_details", {}))
    assert edited["ok"] is True
    assert observed_indexes[0]["sources"] == []
    assert observed_indexes[1]["sources"][0]["program"] == program


def test_universal_edit_source_maps_to_the_active_domain_with_complete_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    observed = {}

    def run_internal(_service, tool_name, arguments, **_kwargs):
        observed["tool_name"] = tool_name
        observed["arguments"] = arguments
        return {"ok": True, "program_id": arguments["program_id"]}

    monkeypatch.setattr(session, "_run_domain_vibescript_tool", run_internal)
    document = type(
        "Document",
        (),
        {"Uid": "active-document", "Name": "Active", "FileName": "", "Objects": []},
    )()
    service = type("Service", (), {"_active_document": lambda self: document})()
    program = "Active/partdesign/Renamed Part"
    result = session._run_universal_vibescript_tool(
        service,
        "PartDesignWorkbench",
        "vibescript.edit_source",
        {
            "program": program,
            "expected_revision": "b" * 64,
            "source": "result = {'Renamed': api.box(inputs['x'], 2, 3)}",
            "inputs": {"x": 1.0},
            "expected_outputs": [{"name": "Renamed", "type": "solid"}],
        },
        editable_sources={
            "sources": [
                {
                    "source_id": "a" * 32,
                    "program": program,
                    "label": "Renamed Part",
                    "domain": "partdesign",
                    "current_revision": "b" * 64,
                    "affected_outputs": [],
                }
            ]
        },
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert observed == {
        "tool_name": "vibescript.partdesign.edit_source",
        "arguments": {
            "program_id": "a" * 32,
            "expected_revision": "b" * 64,
            "source": "result = {'Renamed': api.box(inputs['x'], 2, 3)}",
            "inputs": {"x": 1.0},
            "expected_outputs": [{"name": "Renamed", "type": "solid"}],
        },
    }
    assert result["source_id"] == "a" * 32


def test_universal_delete_output_reconfigures_the_remaining_exact_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session
    import VibeCADVibeScriptDomainRuntime as runtime

    source_id = "a" * 32
    revision = "b" * 64
    program = "Active/partdesign/Two Output Part"
    revised_source = "result = {'Kept': api.box(4, 5, 6)}"
    monkeypatch.setattr(runtime, "capture_inspection_state", lambda *_args: {})
    monkeypatch.setattr(
        runtime,
        "complete_inspection",
        lambda _captured: {
            "ok": True,
            "program": {
                "program_id": source_id,
                "working_revision": revision,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "inputs": {},
                "expected_outputs": [
                    {"name": "Obsolete", "type": "solid"},
                    {"name": "Kept", "type": "solid"},
                ],
            },
        },
    )
    observed = {}

    def run_internal(_service, tool_name, arguments, **_kwargs):
        observed.update(tool_name=tool_name, arguments=arguments)
        return {
            "ok": True,
            "tool": tool_name,
            "program_id": source_id,
            "working_revision": "c" * 64,
        }

    monkeypatch.setattr(session, "_run_domain_vibescript_tool", run_internal)
    document = type(
        "Document",
        (),
        {"Uid": "active-document", "Name": "Active", "FileName": "", "Objects": []},
    )()
    service = type("Service", (), {"_active_document": lambda self: document})()
    result = session._run_universal_vibescript_tool(
        service,
        "PartDesignWorkbench",
        "vibescript.delete_output",
        {
            "program": program,
            "expected_revision": revision,
            "output_name": "Obsolete",
            "source": revised_source,
            "reason": "Superseded by the retained body.",
        },
        editable_sources={
            "sources": [
                {
                    "source_id": source_id,
                    "program": program,
                    "label": "Two Output Part",
                    "domain": "partdesign",
                    "current_revision": revision,
                    "affected_outputs": ["Obsolete", "Kept"],
                }
            ]
        },
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert observed == {
        "tool_name": "vibescript.partdesign.reconfigure_program",
        "arguments": {
            "program_id": source_id,
            "expected_revision": revision,
            "source": revised_source,
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "inputs": {},
            "expected_outputs": [{"name": "Kept", "type": "solid"}],
        },
    }
    assert result["tool"] == "vibescript.delete_output"
    assert result["source_id"] == source_id
    assert result["deleted_output"] == "Obsolete"


@pytest.mark.parametrize(
    ("tool_name", "operation", "arguments"),
    (
        (
            "vibescript.create_program",
            "create_program",
            {
                "program_name": "Assembly",
                "source": "result = {'Assembly': api.assembly([], [])}",
                "input_schema": {"type": "object"},
                "inputs": {},
                "expected_outputs": [{"name": "Assembly", "type": "assembly"}],
            },
        ),
        (
            "vibescript.set_inputs",
            "set_inputs",
            {
                "program": "Active/assembly/Assembly",
                "expected_revision": "b" * 64,
                "patch": {"spacing_mm": 12.0},
            },
        ),
        (
            "vibescript.reconfigure_program",
            "reconfigure_program",
            {
                "program": "Active/assembly/Assembly",
                "expected_revision": "b" * 64,
                "source": "result = {'Assembly': api.assembly([], [])}",
                "input_schema": {"type": "object"},
                "inputs": {},
                "expected_outputs": [{"name": "Assembly", "type": "assembly"}],
            },
        ),
        (
            "vibescript.delete_program",
            "delete_program",
            {
                "program": "Active/assembly/Assembly",
                "expected_revision": "b" * 64,
                "reason": "Remove obsolete mechanism",
            },
        ),
    ),
)
def test_universal_lifecycle_maps_to_the_active_domain(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    operation: str,
    arguments: dict,
) -> None:
    import VibeCADSession as session

    observed = {}

    def run_internal(_service, qualified_name, domain_arguments, **_kwargs):
        observed["tool_name"] = qualified_name
        observed["arguments"] = domain_arguments
        return {
            "ok": True,
            "tool": qualified_name,
            "program_id": domain_arguments.get("program_id", "c" * 32),
        }

    monkeypatch.setattr(session, "_run_domain_vibescript_tool", run_internal)
    document = type(
        "Document",
        (),
        {"Uid": "active-document", "Name": "Active", "FileName": "", "Objects": []},
    )()
    service = type("Service", (), {"_active_document": lambda self: document})()
    result = session._run_universal_vibescript_tool(
        service,
        "AssemblyWorkbench",
        tool_name,
        arguments,
        editable_sources=(
            {
                "sources": [
                        {
                            "source_id": "a" * 32,
                            "program": "Active/assembly/Assembly",
                            "label": "Assembly",
                        "domain": "assembly",
                        "current_revision": "b" * 64,
                        "affected_outputs": [],
                    }
                ]
            }
            if "program" in arguments
            else None
        ),
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert observed["tool_name"] == f"vibescript.assembly.{operation}"
    expected_arguments = dict(arguments)
    if "program" in expected_arguments:
        expected_arguments.pop("program")
        expected_arguments["program_id"] = "a" * 32
    assert observed["arguments"] == expected_arguments
    assert result["tool"] == tool_name
    assert result["source_id"] == result["program_id"]


def test_universal_delete_uses_owning_index_domain_for_outputless_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    source_id = "7" * 32
    revision = "6" * 64
    program = "Active/partdesign/Failed Part"
    observed = {}

    def run_internal(_service, qualified_name, domain_arguments, **_kwargs):
        observed["tool_name"] = qualified_name
        observed["arguments"] = domain_arguments
        return {
            "ok": True,
            "tool": qualified_name,
            "program_id": domain_arguments["program_id"],
            "source_deleted": True,
            "deleted_objects": [],
            "cad_objects_removed": 0,
            "artifacts_deleted": True,
        }

    monkeypatch.setattr(session, "_run_domain_vibescript_tool", run_internal)
    document = type(
        "Document",
        (),
        {"Uid": "active-document", "Name": "Active", "FileName": "", "Objects": []},
    )()
    service = type("Service", (), {"_active_document": lambda self: document})()
    arguments = {
        "program": program,
        "expected_revision": revision,
        "reason": "Remove failed source.",
    }

    result = session._run_universal_vibescript_tool(
        service,
        "PartDesignWorkbench",
        "vibescript.delete_program",
        arguments,
        editable_sources={
            "domain": "partdesign",
            "workbench": "PartDesignWorkbench",
            "sources": [
                {
                    "source_id": source_id,
                    "program": program,
                    "label": "Failed Part",
                    "domain": "partdesign",
                    "current_revision": revision,
                    "affected_outputs": [],
                }
            ],
        },
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert observed == {
        "tool_name": "vibescript.partdesign.delete_program",
        "arguments": {
            "program_id": source_id,
            "expected_revision": revision,
            "reason": "Remove failed source.",
        },
    }
    assert result["ok"] is True
    assert result["source_id"] == source_id
    assert result["source_target"]["program"] == program
    assert result["source_target"]["affected_outputs"] == []


def test_universal_source_tools_route_outputless_source_across_unified_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    source_id = "8" * 32
    revision = "7" * 64
    program = "Active/assembly/Failed Assembly"
    observed = {}

    def run_internal(bound_service, qualified_name, arguments, **_kwargs):
        observed.update(
            workbench=bound_service.active_workbench_name(),
            tool_name=qualified_name,
            arguments=arguments,
        )
        return {
            "ok": True,
            "tool": qualified_name,
            "program_id": source_id,
            "source_deleted": True,
        }

    monkeypatch.setattr(session, "_run_domain_vibescript_tool", run_internal)
    document = type(
        "Document",
        (),
        {"Uid": "active-document", "Name": "Active", "FileName": "", "Objects": []},
    )()
    service = type("Service", (), {"_active_document": lambda self: document})()
    result = session._run_universal_vibescript_tool(
        service,
        "PartDesignWorkbench",
        "vibescript.delete_program",
        {
            "program": program,
            "expected_revision": revision,
            "reason": "Remove failed Assembly source.",
        },
        editable_sources={
            "domain": "partdesign",
            "workbench": "PartDesignWorkbench",
            "authoring_domains": ["partdesign", "assembly"],
            "sources": [],
            "all_sources": [
                {
                    "source_id": source_id,
                    "program": program,
                    "label": "Failed Assembly",
                    "domain": "assembly",
                    "current_revision": revision,
                    "affected_outputs": [],
                }
            ],
        },
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert result["ok"] is True
    assert observed["workbench"] == "AssemblyWorkbench"
    assert observed["tool_name"] == "vibescript.assembly.delete_program"
    assert result["source_target"]["program"] == program


def test_universal_source_rejects_conflicting_row_and_index_domains() -> None:
    import VibeCADSession as session

    source_id = "5" * 32
    program = "Active/mesh/Conflicting Source"
    document = type(
        "Document",
        (),
        {"Uid": "active-document", "Name": "Active", "FileName": "", "Objects": []},
    )()
    service = type("Service", (), {"_active_document": lambda self: document})()
    result = session._run_universal_vibescript_tool(
        service,
        "PartDesignWorkbench",
        "vibescript.read_source",
        {"program": program, "include_logs": False},
        editable_sources={
            "domain": "partdesign",
            "workbench": "PartDesignWorkbench",
            "sources": [
                {
                    "source_id": source_id,
                    "program": program,
                    "label": "Conflicting Source",
                    "domain": "mesh",
                }
            ],
        },
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert result["ok"] is False
    assert result["failure_code"] == "SOURCE_DOMAIN_MISMATCH"
    assert result["observed"] == {
        "source_id": source_id,
        "record_domain": "mesh",
        "index_domain": "partdesign",
    }


def test_universal_source_accepts_exact_local_program_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    source_id = "6" * 32
    revision = "5" * 64
    program = "Active/assembly/Robot_Base_Assembly"

    class SourceOutput:
        VibeCADVibeScriptProgramId = source_id
        VibeCADVibeScriptProgramLabel = "Robot_Base_Assembly"
        VibeCADVibeScriptDomain = "assembly"
        VibeCADVibeScriptRevision = revision
        VibeCADVibeScriptOutputName = "assembly"

    document = type(
        "Document",
        (),
        {
            "Uid": "active-document",
            "Name": "Active",
            "FileName": "/project/active.FCStd",
            "Objects": [SourceOutput()],
        },
    )()
    fake_freecad = type(
        "FreeCAD",
        (),
        {"listDocuments": staticmethod(lambda: {"Active": document})},
    )()
    monkeypatch.setitem(sys.modules, "FreeCAD", fake_freecad)
    service = type("Service", (), {"_active_document": lambda self: document})()

    result = session._run_universal_vibescript_tool(
        service,
        "AssemblyWorkbench",
        "vibescript.read_api",
        {"program": "Robot_Base_Assembly", "names": ["component"]},
        editable_sources={
            "domain": "assembly",
            "workbench": "AssemblyWorkbench",
            "sources": [
                {
                    "source_id": source_id,
                    "program": program,
                    "label": "Robot_Base_Assembly",
                    "domain": "assembly",
                    "current_revision": revision,
                    "affected_outputs": [{"name": "assembly"}],
                }
            ],
        },
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert result["ok"] is True
    assert result["source_target"]["program"] == program


def test_read_source_local_name_lookup_reports_absence_without_failure() -> None:
    import VibeCADSession as session

    document = type(
        "Document",
        (),
        {"Uid": "active-document", "Name": "Active", "FileName": "", "Objects": []},
    )()
    service = type("Service", (), {"_active_document": lambda self: document})()
    result = session._run_universal_vibescript_tool(
        service,
        "AssemblyWorkbench",
        "vibescript.read_source",
        {"program": "Missing Program"},
        editable_sources={
            "domain": "assembly",
            "workbench": "AssemblyWorkbench",
            "sources": [],
        },
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert result == {
        "ok": True,
        "found": False,
        "program_name": "Missing Program",
    }


def test_universal_source_tools_route_to_the_exact_open_authoring_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    source_id = "9" * 32
    revision = "8" * 64

    class SourceOutput:
        VibeCADVibeScriptProgramId = source_id
        VibeCADVibeScriptProgramLabel = "Gear Program"
        VibeCADVibeScriptDomain = "partdesign"
        VibeCADVibeScriptRevision = revision
        VibeCADVibeScriptOutputName = "Gear"

    assembly_document = type(
        "Document",
        (),
        {
            "Uid": "assembly-document",
            "Name": "Assembly",
            "FileName": "/project/assembly.FCStd",
            "Objects": [],
        },
    )()
    source_document = type(
        "Document",
        (),
        {
            "Uid": "source-document",
            "Name": "GearSource",
            "FileName": "/project/gear.FCStd",
            "Objects": [SourceOutput()],
        },
    )()
    fake_freecad = type(
        "FreeCAD",
        (),
        {
            "listDocuments": staticmethod(
                lambda: {"Assembly": assembly_document, "GearSource": source_document}
            )
        },
    )()
    monkeypatch.setitem(sys.modules, "FreeCAD", fake_freecad)
    service = type(
        "Service",
        (),
        {"_active_document": lambda self: assembly_document},
    )()
    program = "GearSource/partdesign/Gear Program"
    catalog = {
        "candidates": [
            {
                "reference": {
                    "document_uid": "source-document",
                    "object_name": "PublishedGear",
                    "document_path": "gear.FCStd",
                },
                "authoring_source": {
                    "source_id": source_id,
                    "program": program,
                    "domain": "partdesign",
                    "output_name": "Gear",
                },
            }
        ]
    }

    result = session._run_universal_vibescript_tool(
        service,
        "AssemblyWorkbench",
        "vibescript.read_api",
        {"program": program, "names": ["body"]},
        component_catalog=catalog,
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert result["ok"] is True
    assert result["domain"] == "partdesign"
    assert result["source_target"] == {
        "program": program,
        "workbench": "PartDesignWorkbench",
        "document_path": "/project/gear.FCStd",
        "current_revision": revision,
        "affected_outputs": ["Gear"],
    }
    assert service._active_document() is assembly_document


def test_universal_source_write_binds_the_authoring_document_without_switching_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    source_id = "5" * 32
    revision = "4" * 64

    class SourceOutput:
        VibeCADVibeScriptProgramId = source_id
        VibeCADVibeScriptProgramLabel = "Shaft Program"
        VibeCADVibeScriptDomain = "partdesign"
        VibeCADVibeScriptRevision = revision
        VibeCADVibeScriptOutputName = "Shaft"

    class Document:
        def __init__(self, uid, name, path, objects):
            self.Uid = uid
            self.Name = name
            self.FileName = path
            self.Objects = objects
            self.recompute_count = 0

        def recompute(self):
            self.recompute_count += 1

    assembly_document = Document(
        "assembly-document",
        "Assembly",
        "/project/assembly.FCStd",
        [],
    )
    source_document = Document(
        "source-document",
        "ShaftSource",
        "/project/shaft.FCStd",
        [SourceOutput()],
    )
    fake_freecad = type(
        "FreeCAD",
        (),
        {
            "listDocuments": staticmethod(
                lambda: {"Assembly": assembly_document, "ShaftSource": source_document}
            )
        },
    )()
    monkeypatch.setitem(sys.modules, "FreeCAD", fake_freecad)

    class Service:
        def _active_document(self):
            return assembly_document

        @staticmethod
        def provider_document_revision_for(document, **_kwargs):
            return f"revision:{document.Uid}"

    observed = {}

    def run_internal(bound_service, tool_name, arguments, **_kwargs):
        observed.update(
            document=bound_service._active_document(),
            workbench=bound_service.active_workbench_name(),
            revision=bound_service.provider_document_revision(),
            tool_name=tool_name,
            arguments=arguments,
        )
        return {
            "ok": True,
            "program_id": source_id,
            "working_revision": "3" * 64,
        }

    monkeypatch.setattr(session, "_run_domain_vibescript_tool", run_internal)
    program = "ShaftSource/partdesign/Shaft Program"
    result = session._run_universal_vibescript_tool(
        Service(),
        "AssemblyWorkbench",
        "vibescript.edit_source",
        {
            "program": program,
            "expected_revision": revision,
            "source": "result = {'Shaft': api.body(api.box(1, 2, 3))}",
        },
        component_catalog={
            "candidates": [
                {
                    "reference": {
                        "document_uid": "source-document",
                        "object_name": "PublishedShaft",
                        "document_path": "shaft.FCStd",
                    },
                    "authoring_source": {
                        "source_id": source_id,
                        "program": program,
                        "domain": "partdesign",
                        "output_name": "Shaft",
                    },
                }
            ]
        },
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert observed == {
        "document": source_document,
        "workbench": "PartDesignWorkbench",
        "revision": "revision:source-document",
        "tool_name": "vibescript.partdesign.edit_source",
        "arguments": {
            "program_id": source_id,
            "expected_revision": revision,
            "source": "result = {'Shaft': api.body(api.box(1, 2, 3))}",
        },
    }
    assert result["source_target"]["program"] == program
    assert result["source_target"]["current_revision"] == "3" * 64
    assert result["referencing_document_refreshed"] is True
    assert assembly_document.recompute_count == 1
    assert source_document.recompute_count == 0


def test_universal_source_routing_rejects_closed_and_unknown_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    source_id = "7" * 32
    assembly_document = type(
        "Document",
        (),
        {
            "Uid": "assembly-document",
            "Name": "Assembly",
            "FileName": "/project/assembly.FCStd",
            "Objects": [],
        },
    )()
    fake_freecad = type(
        "FreeCAD",
        (),
        {"listDocuments": staticmethod(lambda: {"Assembly": assembly_document})},
    )()
    monkeypatch.setitem(sys.modules, "FreeCAD", fake_freecad)
    service = type(
        "Service",
        (),
        {"_active_document": lambda self: assembly_document},
    )()
    program = "GearSource/partdesign/Gear Program"
    catalog = {
        "candidates": [
            {
                "reference": {
                    "document_uid": "closed-source",
                    "object_name": "PublishedGear",
                    "document_path": "gear.FCStd",
                },
                "authoring_source": {
                    "source_id": source_id,
                    "program": program,
                    "domain": "partdesign",
                    "output_name": "Gear",
                },
            }
        ]
    }

    closed = session._run_universal_vibescript_tool(
        service,
        "AssemblyWorkbench",
        "vibescript.read_api",
        {"program": program},
        component_catalog=catalog,
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )
    unknown = session._run_universal_vibescript_tool(
        service,
        "AssemblyWorkbench",
        "vibescript.read_api",
        {"program": "Assembly/partdesign/Unknown Program"},
        component_catalog=catalog,
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert closed["failure_code"] == "SOURCE_DOCUMENT_NOT_OPEN"
    assert closed["observed"]["documents"][0]["document_uid"] == "closed-source"
    assert unknown["failure_code"] == "PROGRAM_NOT_FOUND"


def test_assembly_connector_compatibility_uses_only_explicit_contracts() -> None:
    from vibescript_assembly_api import explicit_connector_compatibility

    legacy = explicit_connector_compatibility("revolute", [None, None])
    assert legacy == {
        "ok": True,
        "joint_type": "revolute",
        "validation": "native_joint_connector_validation",
        "contracts": [None, None],
    }
    allowed = explicit_connector_compatibility(
        "revolute",
        [
            {
                "kind": "axis",
                "allowed_joints": ["revolute"],
                "compatibility": "shaft-v1",
            },
            {
                "kind": "axis",
                "allowed_joints": ["revolute", "fixed"],
                "compatibility": "shaft-v1",
            },
        ],
    )
    assert allowed["ok"] is True
    assert allowed["validation"] == "explicit_connector_contract"
    multi_use = [
        {
            "kind": "axis",
            "allowed_joints": ["revolute", "gears"],
            "compatibility": {
                "revolute": "bearing-v1",
                "gears": "gear-module-v1",
            },
        },
        {
            "kind": "axis",
            "allowed_joints": ["revolute", "gears"],
            "compatibility": {
                "revolute": "bearing-v1",
                "gears": "other-gear-module",
            },
        },
    ]
    assert explicit_connector_compatibility("revolute", multi_use)["ok"] is True
    assert explicit_connector_compatibility("gears", multi_use)["ok"] is False
    disallowed = explicit_connector_compatibility(
        "fixed",
        [{"kind": "axis", "allowed_joints": ["revolute"]}, None],
    )
    assert disallowed["ok"] is False
    assert "explicitly disallows" in disallowed["reason"]
    generic_mate = explicit_connector_compatibility(
        "fixed",
        [
            {
                "kind": "frame",
                "allowed_joints": ["fixed"],
                "compatibility": "purpose-specific-mount-v1",
            },
            None,
        ],
    )
    assert generic_mate["ok"] is True
    assert generic_mate["validation"] == "explicit_connector_contract"
    mismatch = explicit_connector_compatibility(
        "revolute",
        [
            {"kind": "axis", "compatibility": "shaft-v1"},
            {"kind": "axis", "compatibility": "shaft-v2"},
        ],
    )
    assert mismatch["ok"] is False
    assert mismatch["compatibility"] == ["shaft-v1", "shaft-v2"]


def test_editable_sources_indexes_hidden_outputs_and_sources_without_outputs() -> None:
    class View:
        Visibility = False

    class Output:
        PropertiesList = [
            domains.PROP_PROGRAM_ID,
            domains.PROP_PROGRAM_DOMAIN,
            domains.PROP_PROGRAM_WORKBENCH,
            domains.PROP_PROGRAM_REVISION,
            domains.PROP_PROGRAM_OUTPUT,
            domains.PROP_PROGRAM_LABEL,
            domains.PROP_PROGRAM_CONTRACT,
            domains.PROP_PROGRAM_EDITOR_DRAFT,
        ]
        Name = "HiddenBody"
        Label = "Hidden Body"
        TypeId = "PartDesign::Body"
        ViewObject = View()
        VibeCADVibeScriptProgramId = "a" * 32
        VibeCADVibeScriptDomain = "partdesign"
        VibeCADVibeScriptWorkbench = "PartDesignWorkbench"
        VibeCADVibeScriptRevision = "b" * 64
        VibeCADVibeScriptOutputName = "Body"
        VibeCADVibeScriptProgramLabel = "Body Source"
        VibeCADVibeScriptProgramContract = "{}"
        VibeCADVibeScriptEditorDraft = ""

    class DraftOnly:
        PropertiesList = list(Output.PropertiesList)
        Name = "SourceDraft"
        Label = "Source Draft"
        TypeId = "App::FeaturePython"
        ViewObject = View()
        VibeCADVibeScriptProgramId = "c" * 32
        VibeCADVibeScriptDomain = "partdesign"
        VibeCADVibeScriptWorkbench = "PartDesignWorkbench"
        VibeCADVibeScriptRevision = "d" * 64
        VibeCADVibeScriptOutputName = ""
        VibeCADVibeScriptProgramLabel = "Draft Source"
        VibeCADVibeScriptProgramContract = "{}"
        VibeCADVibeScriptEditorDraft = "{}"

    class ForeignMeshSource:
        PropertiesList = list(Output.PropertiesList)
        Name = "ForeignMesh"
        Label = "Foreign Mesh"
        TypeId = "Mesh::Feature"
        ViewObject = View()
        VibeCADVibeScriptProgramId = "e" * 32
        VibeCADVibeScriptDomain = "mesh"
        VibeCADVibeScriptWorkbench = "MeshWorkbench"
        VibeCADVibeScriptRevision = "f" * 64
        VibeCADVibeScriptOutputName = "Mesh"
        VibeCADVibeScriptProgramLabel = "Foreign Mesh Source"
        VibeCADVibeScriptProgramContract = "{}"
        VibeCADVibeScriptEditorDraft = ""

    class Service:
        def active_workbench_name(self):
            return "PartDesignWorkbench"

        def _active_document(self):
            return type(
                "Document",
                (),
                {
                    "Name": "Design",
                    "Uid": "design-document",
                    "Objects": [Output(), DraftOnly(), ForeignMeshSource()],
                },
            )()

    index = domains.editable_sources_snapshot(Service(), "partdesign")

    assert index["tools"]["read_source"] == "vibescript.read_source"
    assert index["tools"]["read_api"] == "vibescript.read_api"
    assert index["tools"]["read_geometry"] == "vibescript.read_geometry"
    assert index["tools"]["read_placement"] == "vibescript.read_placement"
    assert index["tools"]["create_program"] == "vibescript.create_part"
    assert index["tools"]["edit_source"] == "vibescript.edit_source"
    assert index["tools"]["set_inputs"] == "vibescript.set_inputs"
    assert index["tools"]["reconfigure_program"] == ("vibescript.reconfigure_program")
    assert index["tools"]["delete_output"] == "vibescript.delete_output"
    assert index["tools"]["delete_program"] == "vibescript.delete_program"
    assert index["workbench"] == "PartDesignWorkbench"
    assert index["domain"] == "partdesign"
    assert [item["source_id"] for item in index["sources"]] == [
        "a" * 32,
        "c" * 32,
    ]
    hidden, draft = index["sources"]
    assert hidden["domain"] == "partdesign"
    assert hidden["workbench"] == "PartDesignWorkbench"
    assert hidden["source_kind"] == "vibescript_program"
    assert hidden["read_tool"] == "vibescript.read_source"
    assert hidden["edit_tool"] == "vibescript.edit_source"
    assert hidden["affected_outputs"] == [
        {
            "name": "Body",
            "object_name": "HiddenBody",
            "label": "Hidden Body",
            "type_id": "PartDesign::Body",
            "visible": False,
        }
    ]
    assert hidden["read_arguments"] == {
        "program": "Design/partdesign/Body Source",
        "include_logs": False,
    }
    assert hidden["build_tool"] == "vibescript.build_program"
    assert hidden["build_arguments"] == {
        "program": "Design/partdesign/Body Source",
        "expected_revision": "b" * 64,
    }
    assert hidden["edit_target_arguments"] == {
        "program": "Design/partdesign/Body Source",
        "expected_revision": "b" * 64,
    }
    assert hidden["delete_target_arguments"] == {
        "program": "Design/partdesign/Body Source",
        "expected_revision": "b" * 64,
        "reason": "Remove this source and its owned outputs.",
    }
    assert draft["affected_outputs"] == []
    assert draft["status"] == "editor_draft"
    assert all("source" not in item for item in index["sources"])


def test_editable_sources_include_failed_and_unpublished_persisted_programs(
    tmp_path: Path,
) -> None:
    failed_id = "1" * 32
    validated_id = "2" * 32
    failed_revision = "a" * 64
    validated_revision = "b" * 64
    program_root = tmp_path / "vibescript" / "partdesign"

    def write_program(program_id: str, manifest: dict[str, object]) -> None:
        directory = program_root / program_id
        directory.mkdir(parents=True)
        (directory / "program.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

    common = {
        "schema": domains.PROGRAM_SCHEMA,
        "version": domains.PROGRAM_VERSION,
        "domain": "partdesign",
        "workbench": "PartDesignWorkbench",
        "source": "result = {'Body': api.box(1, 2, 3)}",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "inputs": {},
        "expected_outputs": [{"name": "Body", "type": "solid"}],
        "accepted_revision": "",
        "live_outputs": {},
    }
    write_program(
        failed_id,
        {
            **common,
            "program_id": failed_id,
            "label": "Failed source",
            "working_revision": failed_revision,
            "latest_candidate": {
                "status": "failed",
                "revision": failed_revision,
                "attempt_id": "failed-attempt",
                "failure": {
                    "failure_code": "RUN_FAILED",
                    "failure_stage": "external_process",
                    "error": "Candidate did not build.",
                    "observed": {"stdout": "must not enter provider context"},
                },
            },
        },
    )
    write_program(
        validated_id,
        {
            **common,
            "program_id": validated_id,
            "label": "Validated source",
            "working_revision": validated_revision,
            "latest_candidate": {
                "status": "validated",
                "revision": validated_revision,
                "attempt_id": "validated-attempt",
            },
        },
    )

    class Service:
        def active_workbench_name(self):
            return "PartDesignWorkbench"

        def project_scope_snapshot(self):
            return {"root": str(tmp_path)}

        def _active_document(self):
            return None

    captured = domains.capture_editable_sources_snapshot(
        Service(),
        "partdesign",
    )
    assert captured["native_program_count"] == 0

    index = domains.complete_editable_sources_snapshot(captured)

    assert index["source_count"] == 2
    by_id = {item["source_id"]: item for item in index["sources"]}
    failed = by_id[failed_id]
    validated = by_id[validated_id]
    assert failed["status"] == "build_failed"
    assert failed["domain"] == "partdesign"
    assert failed["workbench"] == "PartDesignWorkbench"
    assert failed["current_revision"] == failed_revision
    assert failed["affected_outputs"] == []
    assert failed["latest_candidate"] == {
        "status": "failed",
        "revision": failed_revision,
        "attempt_id": "failed-attempt",
        "failure": {
            "failure_code": "RUN_FAILED",
            "failure_stage": "external_process",
            "error": "Candidate did not build.",
        },
    }
    assert validated["status"] == "validated_unpublished"
    assert validated["current_revision"] == validated_revision
    assert validated["affected_outputs"] == []
    assert "result = {'Body'" not in json.dumps(index)
    assert "observed" not in json.dumps(index)


def test_component_catalog_finds_literal_substrings_in_saved_project_files(
    tmp_path: Path,
) -> None:
    from VibeCADComponentCatalog import search_captured_component_catalog

    owner = tmp_path / "assembly.FCStd"
    owner.write_bytes(b"owner")
    component = tmp_path / "components" / "drive-bracket.FCStd"
    component.parent.mkdir()
    document_xml = """<?xml version="1.0" encoding="utf-8"?>
<Document>
  <Properties>
    <Property name="Label" type="App::PropertyString"><String value="Drive Module"/></Property>
    <Property name="Uid" type="App::PropertyUUID"><Uuid value="component-uid"/></Property>
  </Properties>
  <Objects>
    <Object type="PartDesign::Body" name="BracketBody" id="1"/>
    <Object type="PartDesign::Pad" name="Pad" id="2"/>
  </Objects>
  <ObjectData>
    <Object name="BracketBody">
      <Properties>
        <Property name="Label" type="App::PropertyString"><String value="M3 Motor Bracket"/></Property>
        <Property name="PartNumber" type="App::PropertyString"><String value="DRV-BRK-003"/></Property>
        <Property name="VibeCADVibeScriptProgramId" type="App::PropertyString"><String value="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"/></Property>
        <Property name="VibeCADVibeScriptDomain" type="App::PropertyString"><String value="partdesign"/></Property>
        <Property name="VibeCADVibeScriptOutputName" type="App::PropertyString"><String value="Bracket"/></Property>
      </Properties>
    </Object>
  </ObjectData>
</Document>
"""
    with zipfile.ZipFile(component, "w") as archive:
        archive.writestr("Document.xml", document_xml)
    captured = {
        "project_directory": str(tmp_path),
        "owner_file": str(owner),
        "open_document_files": [str(owner)],
        "open_candidates": [],
    }
    result = search_captured_component_catalog(captured, "m3 brk", limit=10)
    assert result["match_count"] == 1
    match = result["matches"][0]
    assert match["object_name"] == "BracketBody"
    assert match["live_validated"] is False
    assert match["reference"] == {
        "document_uid": "component-uid",
        "object_name": "BracketBody",
        "document_path": "components/drive-bracket.FCStd",
    }
    assert all(item["object_name"] != "Pad" for item in result["matches"])


def test_live_component_discovery_defers_expensive_brep_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADComponentCatalog as catalog

    class FakeShape:
        Solids = [object()]

        @staticmethod
        def isNull() -> bool:
            return False

        @staticmethod
        def isValid() -> bool:
            raise AssertionError("Catalog discovery must not run full BREP validation.")

    class Document:
        Name = "ImportedEquipment"
        Label = "Imported Equipment"
        FileName = "/project/imported-equipment.FCStd"
        Uid = "imported-equipment-document"

    document = Document()

    class Object:
        Name = "ImportedMotor"
        Label = "Imported Motor"
        TypeId = "Part::Feature"
        Shape = FakeShape()
        Document = document
        PropertiesList: list[str] = []

        @staticmethod
        def isDerivedFrom(type_id: str) -> bool:
            return type_id in {"App::Feature", "Part::Feature"}

    monkeypatch.setattr(
        catalog,
        "reference_for_target",
        lambda _owner, _object: {
            "document_uid": document.Uid,
            "object_name": "ImportedMotor",
        },
    )

    candidate = catalog._live_component_candidate(document, document, Object())

    assert candidate is not None
    assert candidate["live_validated"] is False
    assert candidate["geometry_validation"] == "deferred_until_use"


def test_live_component_discovery_rejects_internal_body_state() -> None:
    import VibeCADComponentCatalog as catalog

    class InternalState:
        TypeId = "PartDesign::DesignBodyState"
        VibeCADScriptedRole = ""

        @staticmethod
        def isDerivedFrom(type_id: str) -> bool:
            return type_id == "Part::Feature"

    assert (
        catalog._live_component_candidate(object(), object(), InternalState())
        is None
    )


def test_saved_catalog_uses_publication_not_private_implementation(
    tmp_path: Path,
) -> None:
    from html import escape

    from VibeCADComponentCatalog import search_captured_component_catalog

    owner = tmp_path / "assembly.FCStd"
    owner.write_bytes(b"owner")
    component = tmp_path / "gearbox.FCStd"
    frame = {
        "schema": "vibecad-connector-frame-v1",
        "origin_mm": [0, 0, 0],
        "x_direction": [1, 0, 0],
        "axis_direction": [0, 0, 1],
        "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    }
    interface_table = escape(
        json.dumps(
            {
                "RotationAxis": {
                    "output": "PlanetMaster",
                    "selection": {"type": "origin"},
                    "resolved": {
                        "object": "PublishedPlanet",
                        "subelements": [],
                        "geometry": [],
                        "connector_frame": frame,
                    },
                }
            },
            separators=(",", ":"),
        ),
        quote=True,
    )
    document_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<Document>
  <Properties>
    <Property name="Label" type="App::PropertyString"><String value="Gearbox"/></Property>
    <Property name="Uid" type="App::PropertyUUID"><Uuid value="gearbox-uid"/></Property>
  </Properties>
  <Objects>
    <Object type="App::Part" name="ProgramRoot" id="1"/>
    <Object type="PartDesign::Body" name="InternalPlanetBody" id="2"/>
    <Object type="App::Link" name="PublishedPlanet" id="3"/>
  </Objects>
  <ObjectData>
    <Object name="ProgramRoot"><Properties>
      <Property name="VibeCADScriptedRole" type="App::PropertyString"><String value="model"/></Property>
      <Property name="VibeCADPublishedInterfaces" type="App::PropertyString"><String value="{interface_table}"/></Property>
    </Properties></Object>
    <Object name="InternalPlanetBody"><Properties>
      <Property name="Label" type="App::PropertyString"><String value="Planet Master Body"/></Property>
      <Property name="VibeCADScriptedRole" type="App::PropertyString"><String value="implementation"/></Property>
    </Properties></Object>
    <Object name="PublishedPlanet"><Properties>
      <Property name="Label" type="App::PropertyString"><String value="Planet Master"/></Property>
      <Property name="VibeCADScriptedRole" type="App::PropertyString"><String value="publication"/></Property>
      <Property name="VibeCADVibeScriptProgramId" type="App::PropertyString"><String value="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"/></Property>
      <Property name="VibeCADVibeScriptDomain" type="App::PropertyString"><String value="partdesign"/></Property>
      <Property name="VibeCADVibeScriptOutputName" type="App::PropertyString"><String value="PlanetMaster"/></Property>
      <Property name="VibeCADVibeScriptOutputType" type="App::PropertyString"><String value="solid"/></Property>
    </Properties></Object>
  </ObjectData>
</Document>
"""
    with zipfile.ZipFile(component, "w") as archive:
        archive.writestr("Document.xml", document_xml)
    result = search_captured_component_catalog(
        {
            "project_directory": str(tmp_path),
            "owner_file": str(owner),
            "open_document_files": [str(owner)],
            "open_candidates": [],
        },
        "planet master",
    )
    assert [item["object_name"] for item in result["matches"]] == [
        "PublishedPlanet"
    ]
    match = result["matches"][0]
    assert match["assembly_contract"]["vibescript_output_type"] == "solid"
    assert match["published_interfaces"] == ["RotationAxis"]
    assert match["interfaces"][0]["frame"] == frame


def test_saved_catalog_keeps_managed_model_occurrences_reusable(
    tmp_path: Path,
) -> None:
    from VibeCADComponentCatalog import search_captured_component_catalog

    owner = tmp_path / "assembly.FCStd"
    owner.write_bytes(b"owner")
    component = tmp_path / "layout.FCStd"
    document_xml = """<?xml version="1.0" encoding="utf-8"?>
<Document>
  <Properties>
    <Property name="Label" type="App::PropertyString"><String value="Cell Layout"/></Property>
    <Property name="Uid" type="App::PropertyUUID"><Uuid value="layout-uid"/></Property>
  </Properties>
  <Objects>
    <Object type="App::Link" name="PlacedRail" id="1"/>
  </Objects>
  <ObjectData>
    <Object name="PlacedRail"><Properties>
      <Property name="Label" type="App::PropertyString"><String value="Placed Linear Rail"/></Property>
      <Property name="VibeCADScriptedRole" type="App::PropertyString"><String value="implementation"/></Property>
      <Property name="VibeCADVibeScriptProgramId" type="App::PropertyString"><String value="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"/></Property>
      <Property name="VibeCADVibeScriptDomain" type="App::PropertyString"><String value="partdesign"/></Property>
      <Property name="VibeCADVibeScriptOutputName" type="App::PropertyString"><String value="Rail"/></Property>
      <Property name="VibeCADVibeScriptOutputType" type="App::PropertyString"><String value="component_link"/></Property>
    </Properties></Object>
  </ObjectData>
</Document>
"""
    with zipfile.ZipFile(component, "w") as archive:
        archive.writestr("Document.xml", document_xml)

    result = search_captured_component_catalog(
        {
            "project_directory": str(tmp_path),
            "owner_file": str(owner),
            "open_document_files": [str(owner)],
            "open_candidates": [],
        },
        "placed rail",
    )

    assert result["match_count"] == 1
    assert result["matches"][0]["kind"] == "occurrence"
    assert result["matches"][0]["reference"] == {
        "document_uid": "layout-uid",
        "object_name": "PlacedRail",
        "document_path": "layout.FCStd",
    }


def test_component_inventory_is_copy_ready_and_prepared_search_does_not_rescan(
    tmp_path: Path,
) -> None:
    from VibeCADComponentCatalog import (
        component_inventory,
        prepare_captured_component_catalog,
        search_prepared_component_catalog,
    )

    candidate = {
        "document_label": "Drive Module",
        "object_name": "BracketBody",
        "label": "Motor Bracket",
        "kind": "definition",
        "type_id": "PartDesign::Body",
        "source": "open_document",
        "live_validated": True,
        "portable": True,
        "reference": {
            "document_uid": "component-uid",
            "object_name": "BracketBody",
        },
        "authoring_source": {
            "source_id": "a" * 32,
            "domain": "partdesign",
            "output_name": "Bracket",
        },
        "published_interfaces": ["MountAxis", "MountFace"],
    }
    prepared = prepare_captured_component_catalog(
        {
            "owner_document_uid": "component-uid",
            "project_directory": "",
            "owner_file": "",
            "open_document_files": [],
            "open_candidates": [candidate],
        }
    )
    inventory = component_inventory(prepared)
    catalog_candidate = {**candidate, "catalog_key": "component-1"}

    assert inventory == {
        "schema": "vibecad-available-components-v1",
        "component_count": 1,
        "components_included": 1,
        "components_truncated": False,
        "project_file_search_available": False,
        "components": [
            {
                "catalog_key": "component-1",
                "occurrence_key": "Motor_Bracket",
                "label": "Motor Bracket",
                "kind": "definition",
                "reference": candidate["reference"],
                "published_interfaces": ["MountAxis", "MountFace"],
            }
        ],
        "usage": (
            "create_assembly components use catalog_key; assembly.connectors uses "
            "reference."
        ),
    }
    assert search_prepared_component_catalog(prepared, "mount")["match_count"] == 1
    assert search_prepared_component_catalog(prepared, "motor bracket")["matches"] == [
        catalog_candidate
    ]

    import VibeCADSession as session

    resolved = session._resolve_component_catalog_inputs(
        {
            "input_schema": {
                "properties": {
                    "base": {"type": "object", "x-vibecad-reference": True}
                },
                "additionalProperties": False,
            },
            "inputs": {"base": {"catalog_key": "component-1"}},
        },
        prepared,
    )
    assert resolved["inputs"] == {"base": candidate["reference"]}


def test_provider_component_inventory_uses_model_facing_joint_names() -> None:
    import VibeCADSession as session

    inventory = {
        "schema": "vibecad-available-components-v1",
        "component_count": 1,
        "components": [
            {
                "catalog_key": "component-1",
                "occurrence_key": "Gear",
                "label": "24 Tooth Gear",
                "kind": "definition",
                "published_interfaces": ["PitchAxis"],
                "reference": {
                    "document_uid": "component-uid",
                    "object_name": "GearBody",
                },
                "interfaces": [
                    {
                        "name": "PitchAxis",
                        "selection_type": "frame",
                        "subelements": [],
                        "connector_eligible": True,
                        "description": "Gear pitch axis",
                        "connector": {
                            "kind": "axis",
                            "allowed_joints": ["gears", "revolute"],
                            "compatibility": {
                                "gears": "gear-family-v1",
                                "revolute": "bearing-family-v1",
                            },
                            "pitch_radius_mm": 12.5,
                        },
                        "geometry_type": "component_frame",
                        "frame": {"matrix": list(range(16))},
                    }
                ],
                "interfaces_truncated": False,
            }
        ],
    }

    visible = session._provider_component_inventory_payload(inventory)

    assert visible["components"] == [
        {
            "catalog_key": "component-1",
            "label": "24 Tooth Gear",
            "interfaces": [
                {
                    "name": "PitchAxis",
                    "description": "Gear pitch axis",
                    "connector": {
                        "kind": "axis",
                        "allowed_joints": ["gear", "revolute"],
                        "compatibility": {
                            "gear": "gear-family-v1",
                            "revolute": "bearing-family-v1",
                        },
                        "pitch_radius_mm": 12.5,
                    },
                }
            ],
        }
    ]
    assert inventory["components"][0]["interfaces"][0]["connector"][
        "allowed_joints"
    ] == ["gears", "revolute"]


def test_provider_omits_cross_domain_sources_when_active_domain_is_empty() -> None:
    import VibeCADSession as session

    visible = session._provider_editable_sources_payload(
        {
            "schema": "vibecad-editable-sources-v1",
            "domain": "assembly",
            "workbench": "AssemblyWorkbench",
            "source_count": 0,
            "sources": [],
            "all_source_count": 1,
            "all_sources": [
                {
                    "source_id": "a" * 32,
                    "domain": "partdesign",
                    "program": "Robot/partdesign/Components",
                }
            ],
        }
    )

    assert visible == {
        "schema": "vibecad-editable-sources-v1",
        "domain": "assembly",
        "source_count": 0,
    }


def test_component_inventory_removes_generated_carrier_names() -> None:
    from VibeCADComponentCatalog import (
        component_inventory,
        prepare_captured_component_catalog,
    )

    reference = {
        "document_uid": "robot-uid",
        "object_name": "VibePartdesign_ea661316_FixedBase_Source",
    }
    prepared = prepare_captured_component_catalog(
        {
            "owner_document_uid": "robot-uid",
            "project_directory": "",
            "owner_file": "",
            "open_document_files": [],
            "open_candidates": [
                {
                    "document_label": "Robot",
                    "object_name": reference["object_name"],
                    "label": reference["object_name"],
                    "kind": "definition",
                    "type_id": "Part::Feature",
                    "source": "open_document",
                    "live_validated": False,
                    "geometry_validation": "deferred_until_use",
                    "portable": True,
                    "reference": reference,
                }
            ],
        }
    )

    assert component_inventory(prepared)["components"] == [
        {
            "catalog_key": "component-1",
            "label": "FixedBase",
            "kind": "definition",
            "reference": reference,
            "occurrence_key": "FixedBase",
        }
    ]


def test_component_catalog_states_exact_mechanism_boundaries() -> None:
    from VibeCADComponentCatalog import _assembly_component_contract

    rigid = _assembly_component_contract(
        "Part::Feature",
        solid_count=4,
        output_type="compound",
    )
    assert rigid["default_behavior"] == "rigid_occurrence"
    assert rigid["solid_count"] == 4
    assert rigid["movable_unit_count"] == 1
    assert rigid["child_solids_independently_movable"] is False
    assert rigid["flexible_occurrence_supported"] is False
    assert "api.instances" in rigid["authoring_correction"]

    subassembly = _assembly_component_contract(
        "Assembly::AssemblyObject",
        solid_count=9,
    )
    assert subassembly["default_behavior"] == "rigid_occurrence"
    assert subassembly["flexible_occurrence_supported"] is True
    assert subassembly["internal_occurrences_independently_movable"] is True
    assert "flexible=True" in subassembly["flexible_behavior"]


def test_component_catalog_large_inventory_has_explicit_bounded_pagination() -> None:
    import VibeCADSession as session

    from VibeCADComponentCatalog import (
        MAX_COMPONENT_SEARCH_RESULTS,
        ComponentCatalogError,
        prepare_captured_component_catalog,
        search_prepared_component_catalog,
    )
    from tool_impl.service.component_catalog_search import TOOL_SPEC

    candidates = [
        {
            "document_label": "Engine Parts",
            "object_name": f"Body{index:03d}",
            "label": f"Engine Component {index:03d}",
            "type_id": "PartDesign::Body",
            "source": "open_document",
            "live_validated": True,
            "portable": True,
            "reference": {
                "document_uid": "engine-parts-uid",
                "object_name": f"Body{index:03d}",
            },
            "description": f"Full metadata for engine component {index:03d}",
        }
        for index in range(344)
    ]
    prepared = prepare_captured_component_catalog(
        {
            "owner_document_uid": "assembly-uid",
            "project_directory": "",
            "owner_file": "",
            "open_document_files": [],
            "open_candidates": candidates,
        }
    )

    first = search_prepared_component_catalog(
        prepared,
        "Body",
        limit=200,
        detail="references",
    )
    second = search_prepared_component_catalog(
        prepared,
        "Body",
        limit=200,
        offset=first["next_offset"],
        detail="references",
    )

    assert MAX_COMPONENT_SEARCH_RESULTS == 200
    assert first["match_count"] == 344
    assert first["returned_count"] == 200
    assert first["next_offset"] == 200
    assert set(first["matches"][0]) == {
        "catalog_key",
        "kind",
        "label",
        "reference",
    }
    assert first["matches"][0]["catalog_key"] == "component-1"
    assert second["matches"][0]["catalog_key"] == "component-201"
    assert second["offset"] == 200
    assert second["returned_count"] == 144
    assert second["next_offset"] is None
    assert [
        item["reference"]["object_name"]
        for item in [*first["matches"], *second["matches"]]
    ] == [f"Body{index:03d}" for index in range(344)]

    properties = TOOL_SPEC["parameters"]["properties"]
    assert properties["limit"]["maximum"] == 200
    assert properties["offset"]["minimum"] == 0
    assert properties["detail"]["enum"] == ["references", "full"]
    visible_properties = session._provider_schema_copy(TOOL_SPEC)["parameters"][
        "properties"
    ]
    assert visible_properties["limit"]["maximum"] == 200
    tool_spec = ToolSpec.from_mapping(TOOL_SPEC)
    tool_spec.validate_arguments(
        {"query": "Body", "limit": 200, "offset": 0, "detail": "references"}
    )
    with pytest.raises(ComponentCatalogError, match="between 1 and 200"):
        search_prepared_component_catalog(prepared, limit=201)


def test_component_catalog_hides_unrelated_files_until_explicitly_requested() -> None:
    from VibeCADComponentCatalog import (
        component_inventory,
        search_prepared_component_catalog,
    )

    prepared = {
        "schema": "vibecad-component-catalog-snapshot-v1",
        "owner_document_uid": "active-document-uid",
        "project_file_search_available": True,
        "saved_documents_skipped": 1,
        "candidates": [
            {
                "document_label": "Active Document",
                "object_name": "ActiveBody",
                "label": "Active Body",
                "kind": "definition",
                "reference": {
                    "document_uid": "active-document-uid",
                    "object_name": "ActiveBody",
                },
            },
            {
                "document_label": "Unrelated Document",
                "object_name": "UnrelatedBody",
                "label": "Unrelated Body",
                "kind": "definition",
                "reference": {
                    "document_uid": "unrelated-document-uid",
                    "object_name": "UnrelatedBody",
                    "document_path": "unrelated.FCStd",
                },
            },
        ],
        "errors": [
            {
                "document_path": "broken.FCStd",
                "error": "invalid Document.xml size",
            }
        ],
    }

    general = search_prepared_component_catalog(prepared)
    exact = search_prepared_component_catalog(
        prepared,
        document_path="broken.FCStd",
    )
    inventory = component_inventory(prepared)

    assert "errors" not in general
    assert "saved_documents_skipped" not in general
    assert "catalog_health" not in general
    assert exact["errors"] == prepared["errors"]
    assert [
        item["reference"]["object_name"] for item in inventory["components"]
    ] == ["ActiveBody"]
    assert "1 unrelated saved document was not indexed" in inventory["catalog_health"]
    assert search_prepared_component_catalog(prepared, "Unrelated Body")[
        "matches"
    ][0]["reference"]["object_name"] == "UnrelatedBody"


def test_component_catalog_never_loses_matches_to_provider_byte_boundary() -> None:
    import VibeCADProvider as provider

    from VibeCADComponentCatalog import (
        MAX_COMPONENT_SEARCH_RESPONSE_BYTES,
        prepare_captured_component_catalog,
        search_prepared_component_catalog,
    )

    candidates = [
        {
            "document_label": "Large Engine Catalog",
            "object_name": f"EngineBody{index:03d}",
            "label": f"Engine Component {index:03d}",
            "type_id": "PartDesign::Body",
            "source": "open_document",
            "live_validated": True,
            "portable": True,
            "reference": {
                "document_uid": "large-engine-catalog-uid",
                "object_name": f"EngineBody{index:03d}",
            },
            "description": f"Component {index:03d} " + ("x" * 2000),
        }
        for index in range(200)
    ]
    prepared = prepare_captured_component_catalog(
        {
            "owner_document_uid": "assembly-uid",
            "project_directory": "",
            "owner_file": "",
            "open_document_files": [],
            "open_candidates": candidates,
        }
    )

    result = search_prepared_component_catalog(prepared, limit=200, detail="full")
    visible = provider._provider_visible_tool_result({"ok": True, **result})

    assert result["returned_count"] < 200
    assert result["returned_count"] > 0
    assert result["page_byte_limited"] is True
    assert result["next_offset"] == result["returned_count"]
    assert provider._provider_json_bytes({"ok": True, **result}) <= (
        MAX_COMPONENT_SEARCH_RESPONSE_BYTES
    )
    assert isinstance(visible["matches"], list)
    assert len(visible["matches"]) == result["returned_count"]
    assert "vibecad_result_boundary" not in visible


def test_partdesign_vibescript_schema_golden_fixture() -> None:
    fixture_path = Path(__file__).with_name("partdesign_vibescript_schema_sha256.json")
    expected = json.loads(fixture_path.read_text(encoding="utf-8"))
    pack = domains.get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None
    observed: dict[str, str] = {}
    for raw in (*domains.universal_tool_specs(), *domains.domain_tool_specs(pack)):
        schema = ToolSpec.from_mapping(raw).to_schema(
            active_workbench="PartDesignWorkbench"
        )
        digest = hashlib.sha256(
            json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        observed[str(schema["name"])] = digest
    assert observed == expected


def test_schema_v1_migrates_to_partdesign_without_relocation(tmp_path: Path) -> None:
    v1_directory = tmp_path / "vibescript" / ("a" * 32)
    migrated = domains.migrate_program_manifest(
        {
            "schema": domains.PARTDESIGN_V1_SCHEMA,
            "model_id": "a" * 32,
            "model_name": "Saved v1 model",
            "source": "result = {}",
            "parameters": {"Length": 10.0},
            "expected_outputs": ["Body"],
            "revision": "b" * 64,
        },
        artifact_directory=v1_directory,
    )
    assert migrated["schema"] == domains.PROGRAM_SCHEMA
    assert migrated["version"] == 2
    assert migrated["domain"] == "partdesign"
    assert migrated["workbench"] == "PartDesignWorkbench"
    assert migrated["artifact_directory"] == str(v1_directory)
    assert migrated["expected_outputs"] == [{"name": "Body", "type": "solid"}]
    assert migrated["migration_required"] is True
    assert migrated["migration_action"] == "vibescript.edit_source"

    v1_directory.mkdir(parents=True)
    (v1_directory / "model.py").write_text("result = {}\n", encoding="utf-8")
    (v1_directory / "parameters.json").write_text('{"Length":12}', encoding="utf-8")
    artifact_backed = domains.migrate_program_manifest(
        {
            "schema": domains.PARTDESIGN_V1_SCHEMA,
            "model_id": "a" * 32,
            "model_name": "Saved v1 model",
            "expected_outputs": ["Body"],
            "revision": "b" * 64,
        },
        artifact_directory=v1_directory,
    )
    assert artifact_backed["source"] == "result = {}\n"
    assert artifact_backed["inputs"] == {"Length": 12}
    assert artifact_backed["artifact_directory"] == str(v1_directory)


def test_source_and_input_policy_blocks_escape_hatches() -> None:
    domains.validate_program_source("import api\nresult = api.box(1, 2, 3)")
    domains.validate_program_source("from api import *\nresult = box(1, 2, 3)")
    for source in (
        "import os\nresult = {}",
        "result = open('/tmp/value')",
        "result = {'x': doc.saveAs('x.FCStd')}",
        "result = {'x': api._domain}",
    ):
        with pytest.raises(ValueError, match="policy violation"):
            domains.validate_program_source(source)
    with pytest.raises(ValueError, match="raw filesystem path"):
        domains.validate_inputs({"source": "/tmp/cloud.xyz"})
    with pytest.raises(ValueError, match="arbitrary object"):
        domains.validate_inputs({"source": {"path": "artifact.xyz"}})
    assert domains.validate_inputs(
        {"source": {"document_uid": "uid", "object_name": "Cloud"}}
    )
    assert domains.validate_inputs(
        {
            "source": {
                "document_uid": "uid",
                "object_name": "Bracket",
                "document_path": "parts/bracket.FCStd",
            }
        }
    )
    for invalid_path in (
        "../bracket.FCStd",
        "./bracket.FCStd",
        "parts//bracket.FCStd",
    ):
        with pytest.raises(ValueError, match="segments"):
            domains.validate_inputs(
                {
                    "source": {
                        "document_uid": "uid",
                        "object_name": "Bracket",
                        "document_path": invalid_path,
                    }
                }
            )
    with pytest.raises(ValueError, match="must require"):
        domains.validate_input_schema(
            {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "object",
                        "x-vibecad-reference": True,
                        "properties": {
                            "document_uid": {"type": "string"},
                            "object_name": {"type": "string"},
                        },
                        "additionalProperties": False,
                    }
                },
                "additionalProperties": False,
            }
        )


def test_input_reference_deduplication_preserves_one_unambiguous_locator() -> None:
    from VibeCADVibeScriptDomainRuntime import _input_references

    legacy = {
        "document_uid": "component-document",
        "object_name": "Bracket",
    }
    portable = {
        **legacy,
        "document_path": "parts/bracket.FCStd",
    }
    assert _input_references({"first": legacy, "second": portable}) == [portable]
    assert _input_references({"first": portable, "second": legacy}) == [portable]

    with pytest.raises(ValueError, match="conflicting document_path locators"):
        _input_references(
            {
                "first": portable,
                "second": {
                    **legacy,
                    "document_path": "alternate/bracket.FCStd",
                },
            }
        )


def test_worker_result_values_must_come_from_the_active_domain_api() -> None:
    from vibescript_domain_worker import _payload

    forged = {
        "domain": "part",
        "operation": "box",
        "output_type": "solid",
        "arguments": [1, 1, 1],
        "properties": {},
    }
    with pytest.raises(TypeError, match="active domain api"):
        _payload(forged)


def test_part_api_is_explicit_documented_and_generated_from_the_runtime() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("PartWorkbench")
    assert pack is not None and pack.production_ready
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    adapter = domains.get_domain_adapter(pack.domain)
    assert adapter is not None and adapter.production_ready
    description = adapter.describe_api()
    exports = description["runtime_exports"]

    assert description["api_contract"] == "vibecad-vibescript-part-api-v2"
    assert description["units"] == {
        "length": "millimetres",
        "angle": "degrees",
        "tolerance": "millimetres",
    }
    assert description["topology_selection"]["index_base"] == 1
    assert [item["name"] for item in exports] == list(pack.api_exports)
    assert tuple(api.exported_names) == pack.api_exports
    assert len(exports) == 49
    sweep_export = next(item for item in exports if item["name"] == "sweep")
    assert "DomainValue | Sequence[DomainValue]" in sweep_export["signature"]
    assert "one or more ordered wire profiles" in sweep_export["description"]
    assert "long_helix" not in pack.api_exports
    assert "project_parallel" not in pack.api_exports
    assert "project_perspective" not in pack.api_exports
    assert {"helix", "project"} <= set(pack.api_exports)
    assert all(item["description"] for item in exports)
    assert all("*args" not in item["signature"] for item in exports)
    assert all("**properties" not in item["signature"] for item in exports)
    grouped = {
        name for names in description["operation_groups"].values() for name in names
    }
    assert grouped == set(pack.api_exports)
    selection = description["operation_selection"]
    assert selection["one_or_more_profiles_along_path"].startswith("api.sweep")
    assert selection["intersection_edges_only"] == "api.section"
    assert selection["parallel_planar_cross_sections"] == "api.slice"
    assert selection["all_touching_boolean_fragments_with_provenance"] == (
        "api.general_fuse"
    )
    assert selection["join_touching_faces_or_shells"] == "api.sew"
    assert selection["remove_redundant_boolean_splitters"] == "api.refine"
    assert "api.helix(representation=...)" in selection["redundancy_contract"]
    assert "api.project(mode=...)" in selection["redundancy_contract"]
    assert "Canonical operations:" in selection["redundancy_contract"]
    assert description["composition_contract"]["construction_order"][-1].startswith(
        "Return only semantic publication outputs"
    )
    assert (
        "never cycle through guessed indexes"
        in description["model_verification_contract"]["selection_repair"]
    )
    assert "active workbench determines" in description["workbench_handoffs"]["rule"]
    assert (
        len(
            json.dumps(description, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        < 32_000
    )
    assert len(description["recommended_patterns"]) >= 2

    assert not hasattr(api, "long_helix")
    assert not hasattr(api, "project_parallel")
    assert not hasattr(api, "project_perspective")


def test_part_api_reports_operation_and_parameter_before_kernel_execution() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("PartWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)

    cases = (
        (
            lambda: api.from_object({"object_name": "Body"}, output_type="solid"),
            r"api\.from_object.*reference",
        ),
        (lambda: api.box(-1, 2, 3), r"api\.box.*length"),
        (lambda: api.wedge(2, 3, 4, ridge_x=3), r"api\.wedge.*ridge_x"),
        (lambda: api.cylinder(2, 3, direction=[0, 0, 0]), r"api\.cylinder.*direction"),
        (lambda: api.wire([[0, 0, 0]]), r"api\.wire.*items"),
        (
            lambda: api.sweep([], api.wire([[0, 0, 0], [0, 0, 1]])),
            r"api\.sweep.*profile",
        ),
        (lambda: api.fillet(object(), 1), r"api\.fillet.*shape"),
        (
            lambda: api.repair(
                api.box(1, 1, 1),
                working_tolerance=1.0e-2,
                maximum_tolerance=1.0e-3,
            ),
            r"api\.repair.*tolerance",
        ),
        (
            lambda: api.bezier([[0, 0, 0], [1, 1, 0]], weights=[1.0]),
            r"api\.bezier.*weights",
        ),
        (
            lambda: api.nurbs_curve(
                [[0, 0, 0], [1, 1, 0], [2, 0, 0]],
                2,
                [0.0, 1.0],
                [2, 2],
            ),
            r"api\.nurbs_curve.*multiplicities",
        ),
        (
            lambda: api.transform(api.box(1, 1, 1), scale=[1, 0, 1]),
            r"api\.transform.*scale",
        ),
        (
            lambda: api.helix(1, 10, 2, representation="adaptive"),
            r"api\.helix.*representation",
        ),
        (
            lambda: api.project(
                api.plane(10, 10),
                api.circle(2),
                [0, 0, 1],
                mode="orthographic",
            ),
            r"api\.project.*mode",
        ),
    )
    for invoke, pattern in cases:
        with pytest.raises(ValueError, match=pattern):
            invoke()


def test_surface_api_is_explicit_typed_and_generated_from_runtime() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("SurfaceWorkbench")
    assert pack is not None and pack.production_ready
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    adapter = domains.get_domain_adapter(pack.domain)
    assert adapter is not None and adapter.production_ready
    description = adapter.describe_api()

    assert description["api_contract"] == "vibecad-vibescript-surface-api-v1"
    assert tuple(api.exported_names) == pack.api_exports
    assert [item["name"] for item in description["runtime_exports"]] == list(
        pack.api_exports
    )
    assert len(description["runtime_exports"]) == 18
    assert len(set(pack.api_exports)) == len(pack.api_exports)
    assert all(item["description"] for item in description["runtime_exports"])
    assert all(
        "*args" not in item["signature"] and "**properties" not in item["signature"]
        for item in description["runtime_exports"]
    )
    assert set(description["typed_output_contracts"]) == set(pack.output_types)
    assert "Surface::Filling" in description["filling_contract"]["fill"]
    assert "Surface::Sewing" in description["derived_operation_contracts"]["shell"]
    assert (
        description["input_reference_contract"]["schema"]["x-vibecad-reference"] is True
    )
    selection = description["operation_selection"]
    assert selection["point_grid_surface"].startswith("api.surface")
    assert selection[
        "variational_patch_with_continuity_or_internal_constraints"
    ].startswith("api.fill")
    assert "one api.surface" in selection["redundancy_contract"]
    assert "not aliases" in selection["redundancy_contract"]
    assert description["composition_contract"]["construction_order"][-1].startswith(
        "Return only semantic publishable outputs"
    )
    assert (
        "never retry by guessing"
        in description["model_verification_contract"]["reference"].lower()
    )
    assert "active workbench determines" in description["workbench_handoffs"]["rule"]
    assert (
        len(
            json.dumps(description, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        < 32_000
    )
    assert len(description["recommended_patterns"]) >= 2
    for pattern in description["recommended_patterns"]:
        domains.validate_program_source(pattern["source"])

    line = api.line([0, 0, 0], [10, 0, 0])
    wire = api.wire(
        [[0, 0, 0], [10, 0, 0], [10, 5, 0], [0, 5, 0]],
        closed=True,
    )
    face = api.face(wire)
    boundary = api.boundary(line)
    fill = api.fill([boundary])
    assert line.output_type == "edge"
    assert face.output_type == "face"
    assert fill.output_type == "fill"
    with pytest.raises(TypeError):
        line.properties["label"] = "changed"


def test_surface_api_reports_exact_source_errors_before_native_execution() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("SurfaceWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    edge = api.line([0, 0, 0], [10, 0, 0])
    wire = api.wire([[0, 0, 0], [10, 0, 0], [10, 5, 0]], closed=True)
    face = api.face(wire)

    cases = (
        (lambda: api.line([0, 0, 0], [0, 0, 0]), r"api\.line.*end"),
        (lambda: api.circle([0, 0, 0], 0), r"api\.circle.*radius"),
        (
            lambda: api.circle([0, 0, 0], 2, normal=[0, 0, 0]),
            r"api\.circle.*normal",
        ),
        (lambda: api.bezier([[0, 0, 0]]), r"api\.bezier.*poles"),
        (
            lambda: api.bspline([[0, 0, 0], [1, 0, 0]]),
            r"api\.bspline.*points",
        ),
        (
            lambda: api.from_object({"object_name": "Body"}, "face"),
            r"api\.from_object.*reference",
        ),
        (
            lambda: api.from_object(
                {"document_uid": "doc", "object_name": "Body"},
                "face",
                subelement="Face1",
                interface="TopFace",
            ),
            r"api\.from_object.*mutually exclusive",
        ),
        (lambda: api.face(edge), r"api\.face.*outer"),
        (
            lambda: api.surface(
                [
                    [[0, 0, 0], [1, 0, 0]],
                    [[0, 1, 0]],
                ]
            ),
            r"api\.surface.*points\[1\]",
        ),
        (
            lambda: api.surface(
                [
                    [[0, 0, 0], [1, 0, 0]],
                    [[0, 1, 0], [1, 1, 0]],
                ],
                degree_min=6,
                degree_max=5,
            ),
            r"api\.surface.*degree_min",
        ),
        (lambda: api.boundary(edge, continuity="G1"), r"api\.boundary.*support_face"),
        (
            lambda: api.fill([api.boundary(edge)], degree=9, maximum_degree=8),
            r"api\.fill.*degree",
        ),
        (lambda: api.blend([edge, edge], style="unknown"), r"api\.blend.*style"),
        (
            lambda: api.blend([edge, edge], reversed=[True]),
            r"api\.blend.*reversed",
        ),
        (lambda: api.extend(edge), r"api\.extend.*face"),
        (lambda: api.loft([edge]), r"api\.loft.*sections"),
        (lambda: api.thicken(face, 0), r"api\.thicken.*thickness"),
        (
            lambda: api.thicken(face, 1, remove_faces=[1, 1]),
            r"api\.thicken.*remove_faces",
        ),
        (lambda: api.shell([edge]), r"api\.shell.*faces"),
    )
    for invoke, pattern in cases:
        with pytest.raises(ValueError, match=pattern):
            invoke()

    with pytest.raises(ValueError, match=r"api\.thicken.*thickness") as failure:
        api.thicken(face, 0)
    assert failure.value.details["stage"] == "source_validation"
    assert failure.value.details["operation"] == "thicken"
    assert failure.value.details["parameter"] == "thickness"
    assert (
        "Change only the failing source expression"
        in failure.value.details["correction"]
    )


def test_spreadsheet_api_is_explicit_atomic_and_generated_from_runtime() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("SpreadsheetWorkbench")
    assert pack is not None and pack.production_ready
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    adapter = domains.get_domain_adapter(pack.domain)
    assert adapter is not None and adapter.production_ready
    description = adapter.describe_api()

    assert description["api_contract"] == "vibecad-vibescript-spreadsheet-api-v1"
    assert tuple(api.exported_names) == pack.api_exports
    assert [item["name"] for item in description["runtime_exports"]] == list(
        pack.api_exports
    )
    assert all(item["description"] for item in description["runtime_exports"])
    assert all(
        "*args" not in item["signature"] and "**properties" not in item["signature"]
        for item in description["runtime_exports"]
    )
    assert description["native_object"] == "Spreadsheet::Sheet"
    assert description["native_limits"]["address_range"] == "A1:ZZ16384"
    assert description["operation_selection"]["shared_rectangular_formatting"] == (
        "api.range_style"
    )
    assert (
        description["operation_selection"]["publish_or_update_one_stable_native_sheet"]
        == "api.sheet"
    )
    assert "single best form" in description["redundancy_contract"]["no_set_cell_alias"]
    assert (
        "complete desired final batch"
        in description["redundancy_contract"]["no_structural_edit_aliases"]
    )
    assert (
        "optional final layout state"
        in description["redundancy_contract"]["merge_is_sheet_state"]
    )
    assert "top-left anchor" in description["formatting_contract"]["merged_ranges"]
    assert "aliases exist" in description["formula_contract"]["ordering"]
    assert (
        "stable result names"
        in description["composition_contract"]["construction_order"][0]
    )
    assert "transaction" in description["publication_contract"]["atomicity"]
    assert "working_revision" in description["model_verification_contract"]["success"]
    assert (
        "domain_failure_stage"
        in description["model_verification_contract"]["failure_repair"]
    )
    assert "active workbench determines" in description["workbench_handoffs"]["rule"]
    assert len(json.dumps(description, sort_keys=True)) < 32_768
    assert len(set(api.exported_names)) == len(api.exported_names)
    for pattern in description["recommended_patterns"]:
        domains.validate_program_source(pattern["source"])

    length = api.cell(
        "a1",
        10,
        unit="mm",
        alias="length",
        style="italic|bold",
    )
    doubled = api.cell("B1", expression="=length * 2", display_unit="cm")
    header = api.range_style("b2:a1", alignment="center|vcenter")
    sheet = api.sheet(
        [length, doubled],
        range_styles=[header],
        column_widths={"B": 90, "A": 120},
        row_heights={2: 35, 1: 30},
    )
    payload = sheet.to_payload()
    assert payload["arguments"][0][0]["arguments"] == ["A1"]
    assert payload["arguments"][0][0]["properties"]["style"] == [
        "bold",
        "italic",
    ]
    assert payload["properties"]["range_styles"][0]["arguments"] == ["A1:B2"]
    assert list(payload["properties"]["column_widths"]) == ["A", "B"]
    merged = api.sheet(
        [api.cell("A1", "Schedule")],
        merged_ranges=["c2:a1"],
    ).to_payload()
    assert merged["properties"]["merged_ranges"] == ["A1:C2"]
    with pytest.raises(TypeError):
        length.properties["alias"] = "changed"


def test_spreadsheet_api_reports_exact_source_errors_before_native_execution() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("SpreadsheetWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    cases = (
        (lambda: api.cell("AAA1", 1), r"api\.cell.*A1 through ZZ16384"),
        (lambda: api.cell("A16385", 1), r"api\.cell.*A1 through ZZ16384"),
        (lambda: api.cell("A1", "=B1"), r"api\.cell.*expression="),
        (
            lambda: api.cell("A1", 1, expression="B1"),
            r"api\.cell.*mutually exclusive",
        ),
        (lambda: api.cell("A1", "one", unit="mm"), r"api\.cell.*numeric literal"),
        (lambda: api.cell("A1", 1, alias="B2"), r"api\.cell.*cell address"),
        (
            lambda: api.cell("A1", 1, alignment="left|right"),
            r"api\.cell.*horizontal",
        ),
        (
            lambda: api.cell("A1", 1, background=[0, 0, 2]),
            r"api\.cell.*inclusive range 0-1",
        ),
        (lambda: api.range_style("A1:B2"), r"api\.range_style.*at least one"),
        (
            lambda: api.range_style("A1:ZZ16384", style="bold"),
            r"api\.range_style.*at most 10000",
        ),
        (
            lambda: api.sheet([api.cell("A1"), api.cell("a1")]),
            r"api\.sheet.*duplicates cell address",
        ),
        (
            lambda: api.sheet(
                [api.cell("A1", alias="Length"), api.cell("A2", alias="length")]
            ),
            r"api\.sheet.*duplicates alias",
        ),
        (
            lambda: api.sheet([api.cell("A1")], merged_ranges=["A1"]),
            r"api\.sheet.*merged_ranges\[0\].*at least two",
        ),
        (
            lambda: api.sheet([api.cell("A1")], merged_ranges=["A1:B2", "B2:C3"]),
            r"api\.sheet.*merged_ranges\[1\].*overlaps",
        ),
        (
            lambda: api.sheet([api.cell("B1", "not anchor")], merged_ranges=["A1:B1"]),
            r"api\.sheet.*cells\[0\].*non-anchor",
        ),
    )
    for invoke, pattern in cases:
        with pytest.raises(ValueError, match=pattern):
            invoke()

    with pytest.raises(ValueError, match=r"api\.cell.*expression=") as failure:
        api.cell("A1", "=B1")
    assert failure.value.details["stage"] == "source_validation"
    assert failure.value.details["operation"] == "cell"
    assert failure.value.details["parameter"] == "value"
    assert (
        "Change only the failing source expression"
        in failure.value.details["correction"]
    )


def test_material_api_is_explicit_separated_and_generated_from_runtime() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("MaterialWorkbench")
    assert pack is not None and pack.production_ready
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    adapter = domains.get_domain_adapter(pack.domain)
    assert adapter is not None and adapter.production_ready
    description = adapter.describe_api()

    assert description["api_contract"] == "vibecad-vibescript-material-api-v1"
    assert (
        tuple(api.exported_names)
        == pack.api_exports
        == (
            "material",
            "assign",
            "appearance",
        )
    )
    assert not hasattr(api, "output")
    assert [item["name"] for item in description["runtime_exports"]] == list(
        pack.api_exports
    )
    assert all(
        "*args" not in item["signature"] and "**properties" not in item["signature"]
        for item in description["runtime_exports"]
    )
    assert "preserves" in description["publication_contract"]["separation"]
    assert (
        "never changes ShapeMaterial"
        in description["publication_contract"]["separation"]
    )
    selection = description["operation_selection"]
    assert selection["own_physical_engineering_properties"].startswith("api.assign")
    assert selection["own_visible_style"].startswith("api.appearance")
    assert "single canonical operation" in selection["redundancy_contract"]
    assert (
        "not a publishable result" in selection["select_and_validate_one_catalog_card"]
    )
    assert description["composition_contract"]["construction_order"][-1].startswith(
        "Return only assign/appearance values"
    )
    assert (
        "next_write_expected_revision"
        in description["model_verification_contract"]["failure_repair"]
    )
    assert "active workbench determines" in description["workbench_handoffs"]["rule"]
    assert (
        len(
            json.dumps(description, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        < 32_000
    )
    for pattern in description["recommended_patterns"]:
        domains.validate_program_source(pattern["source"])

    reference = {"document_uid": "document", "object_name": "Chassis"}
    card = api.material(
        "0051bddf-6f62-4406-b8c9-569322880564",
        require_physical_properties=["Density", "YoungsModulus"],
    )
    assignment = api.assign(reference, card, label="Physical")
    appearance = api.appearance(
        reference,
        card,
        shape_color=[0.1, 0.2, 0.3],
        transparency=5,
        line_width=2,
        selectable=False,
    )
    assert assignment.output_type == "material_assignment"
    assert assignment.to_payload()["arguments"][1]["output_type"] == "material_card"
    assert appearance.output_type == "appearance"
    assert appearance.to_payload()["arguments"][1]["output_type"] == "material_card"
    assert appearance.to_payload()["properties"]["shape_color"] == [0.1, 0.2, 0.3]
    assert "label" not in card.properties
    with pytest.raises(TypeError):
        card.properties["label"] = "changed"


def test_material_api_reports_exact_source_errors_before_native_execution() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("MaterialWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    reference = {"document_uid": "document", "object_name": "Chassis"}
    card = api.material("0051bddf-6f62-4406-b8c9-569322880564")
    cases = (
        (lambda: api.material("not-a-uuid"), r"api\.material.*material_uuid"),
        (
            lambda: api.material(
                "0051bddf-6f62-4406-b8c9-569322880564",
                require_physical_properties="Density",
            ),
            r"api\.material.*require_physical_properties",
        ),
        (
            lambda: api.assign({"object_name": "Chassis"}, card),
            r"api\.assign.*document_uid",
        ),
        (lambda: api.assign(reference, object()), r"api\.assign.*api.material"),
        (lambda: api.appearance(reference, object()), r"api\.appearance.*api.material"),
        (lambda: api.appearance(reference), r"api\.appearance.*at least one"),
        (
            lambda: api.appearance(reference, shape_color=[0, 0, 2]),
            r"api\.appearance.*inclusive range 0-1",
        ),
        (
            lambda: api.appearance(reference, transparency=101),
            r"api\.appearance.*0 through 100",
        ),
        (
            lambda: api.appearance(reference, line_width=0),
            r"api\.appearance.*inclusive range 1-64",
        ),
        (
            lambda: api.appearance(reference, selectable=1),
            r"api\.appearance.*true, false",
        ),
    )
    for invoke, pattern in cases:
        with pytest.raises(ValueError, match=pattern):
            invoke()

    with pytest.raises(ValueError, match=r"api\.material.*material_uuid") as failure:
        api.material("not-a-uuid")
    assert failure.value.details["stage"] == "source_validation"
    assert failure.value.details["operation"] == "material"
    assert failure.value.details["parameter"] == "material_uuid"
    assert (
        "Change only the failing source expression"
        in failure.value.details["correction"]
    )


def test_material_catalog_context_is_comparison_ready_but_path_free(
    monkeypatch,
) -> None:
    import sys
    from types import SimpleNamespace

    from vibescript_material_worker import (
        material_catalog_index,
        search_material_catalog,
    )

    card = SimpleNamespace(
        UUID="0051bddf-6f62-4406-b8c9-569322880564",
        Name="Production Alloy",
        Description="Bounded test material",
        Parent="",
        LibraryName="Fixture",
        PhysicalModels=[],
        AppearanceModels=[],
        Tags=["metal", "structural"],
        PhysicalProperties={
            "Density": "2700 kg/m^3",
            "YoungsModulus": "69 GPa",
            "PoissonRatio": "0." + ("1" * 300),
            "CustomFatigueModel": "fixture-only",
        },
        AppearanceProperties={
            "DiffuseColor": "(0.7, 0.7, 0.72, 1.0)",
            "TexturePath": "/private/catalog/texture.png",
        },
        Properties={"SourceURL": "https://invalid.example/material"},
    )
    manager = SimpleNamespace(Materials={"fixture": card})
    monkeypatch.setitem(
        sys.modules,
        "Materials",
        SimpleNamespace(MaterialManager=lambda: manager),
    )

    catalog = material_catalog_index()
    assert catalog["cards_truncated"] is False
    assert "inspect accepted output validation" in catalog["selection_contract"]
    record = catalog["cards"][0]
    assert record["selection_physical_values"] == {
        "Density": "2700 kg/m^3",
        "YoungsModulus": "69 GPa",
        "PoissonRatio": ("0." + ("1" * 300))[:256],
    }
    assert record["selection_appearance_values"] == {
        "DiffuseColor": "(0.7, 0.7, 0.72, 1.0)"
    }
    assert record["selection_physical_values_truncated"] == ["PoissonRatio"]
    assert record["selection_appearance_values_truncated"] == []
    assert "CustomFatigueModel" in record["physical_property_names"]
    assert "TexturePath" in record["appearance_property_names"]
    assert "/private/catalog" not in json.dumps(catalog)

    search = search_material_catalog(
        "prod 2700 struct",
        require_physical_properties=["Density"],
        limit=5,
    )
    assert search["match_count"] == 1
    assert search["materials"][0]["constructor"] == {
        "material_uuid": "0051bddf-6f62-4406-b8c9-569322880564",
        "require_physical_properties": ["Density"],
        "require_appearance_properties": [],
    }
    assert "/private/catalog" not in json.dumps(search)

    from tool_impl.service import material_catalog_search

    tool_result = material_catalog_search.run(
        None,
        query="alloy",
        require_physical_properties=["Density"],
    )
    assert tool_result["ok"] is True
    assert tool_result["returned_count"] == 1


def test_nickel_alloy_718_card_is_packaged_for_hot_section_design() -> None:
    module_root = Path(__file__).resolve().parents[2]
    card_path = (
        module_root
        / "Material"
        / "Resources"
        / "Materials"
        / "Standard"
        / "Metal"
        / "Alloys"
        / "Nickel-Alloy-718.FCMat"
    )
    card = card_path.read_text(encoding="utf-8")
    assert 'UUID: "db767dc3-7a48-4fbe-a284-78181f5f05df"' in card
    assert 'SourceURL: "https://www.specialmetals.com/' in card
    assert '"UNS N07718"' in card
    for property_name in (
        "Density",
        "YoungsModulus",
        "PoissonRatio",
        "SpecificHeat",
        "ThermalConductivity",
        "ThermalExpansionCoefficient",
    ):
        assert f"{property_name}:" in card

    material_cmake = (module_root / "Material" / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    relative_card = "Resources/Materials/Standard/Metal/Alloys/Nickel-Alloy-718.FCMat"
    assert relative_card in material_cmake


def test_material_worker_errors_always_provide_one_model_repair() -> None:
    from vibescript_material_worker import MaterialCandidateError

    failure = MaterialCandidateError(
        "Unsupported view field.",
        details={
            "stage": "target_capability",
            "target": {"document_uid": "d", "object_name": "Chassis"},
        },
    )
    assert "Chassis" in failure.details["correction"]
    assert "unsupported field" in failure.details["correction"]

    explicit = MaterialCandidateError(
        "Catalog unavailable.",
        details={"stage": "catalog_open", "correction": "Repair fixture catalog."},
    )
    assert explicit.details["correction"] == "Repair fixture catalog."


def test_assembly_api_is_explicit_graph_based_and_generated_from_runtime() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("AssemblyWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    adapter = domains.get_domain_adapter(pack.domain)
    assert adapter is not None
    description = adapter.describe_api()
    core_api = domains._core_api_snapshot(pack)

    assert description["api_contract"] == "vibecad-vibescript-assembly-api-v1"
    assert list(core_api["api"]) == [
        "component",
        "connector",
        "joint",
        "assembly",
        "solve",
        "mechanism_check",
        "motion",
        "simulation",
        "bill_of_materials",
    ]
    assert "Mapping[str, DomainValue]" in core_api["api"]["assembly"]
    assert "Mapping[str, DomainValue]" in core_api["api"]["simulation"]
    assert "Sequence[str | Mapping[str, str]]" in core_api["api"][
        "bill_of_materials"
    ]
    assert all(not call.startswith("ONLY ") for call in core_api["api"].values())
    assert "inputs['name']" in core_api["source"]
    assert "solver_diagnostics" in core_api["source"]
    assert "Define main()" in core_api["source"]
    assert "Return {'assembly': model" in core_api["source"]
    assert "End with result=" not in core_api["source"]
    assert core_api["create_program"]["reference_input"] == {
        "input_schema": {
            "properties": {
                "base": {"type": "object", "x-vibecad-reference": True},
                "moving": {"type": "object", "x-vibecad-reference": True},
            },
            "required": ["base", "moving"],
            "additionalProperties": False,
        },
        "inputs": {
            "base": {"catalog_key": "component-1"},
            "moving": {"catalog_key": "component-2"},
        },
        "source": "inputs['base']; inputs['moving']",
    }
    assert core_api["create_program"]["result_example"] == (
        "return {'assembly': model, 'solver_diagnostics': api.solve(model)}"
    )
    create_tool = next(
        spec
        for spec in domains.universal_tool_specs()
        if spec["name"] == "vibescript.create_program"
    )
    source_description = create_tool["parameters"]["properties"]["source"][
        "description"
    ]
    assert "Define main()" in source_description
    assert "result=" not in source_description
    inputs_description = create_tool["parameters"]["properties"]["inputs"][
        "description"
    ]
    assert "catalog_key" in inputs_description
    assert "api.component(inputs['name'])" in inputs_description
    assert tuple(api.exported_names) == pack.api_exports
    assert [item["name"] for item in description["runtime_exports"]] == list(
        pack.api_exports
    )
    assert all(item["description"] for item in description["runtime_exports"])
    assert all(
        "*args" not in item["signature"] and "**properties" not in item["signature"]
        for item in description["runtime_exports"]
    )
    exports = {item["name"]: item for item in description["runtime_exports"]}
    assert exports["component"]["description"].startswith(
        "Create one occurrence from api.component(inputs['name'])"
    )
    assert exports["connector"]["description"].startswith(
        "Use a named interface or exact selection as one JCS."
    )
    assert "Mapping[str, Any]" in exports["connector"]["signature"]
    assert "api.solve" in exports["assembly"]["description"]
    assert "solver_diagnostics" in exports["assembly"]["description"]
    assert set(description["joint_types"]["coupled_motion"]) == {
        "rack_pinion",
        "screw",
        "gears",
        "belt",
    }
    joint_details = description["api_details"]["joint"]
    assert joint_details["kinds"] == [
        "fixed",
        "revolute",
        "cylindrical",
        "slider",
        "ball",
        "distance",
        "parallel",
        "perpendicular",
        "angle",
        "rack_pinion",
        "screw",
        "gears",
        "belt",
    ]
    assert joint_details["required_parameters"]["fixed"] == []
    assert joint_details["required_parameters"]["distance"] == ["distance_mm"]
    assert joint_details["required_parameters"]["gears"] == [
        "radius1_mm",
        "radius2_mm",
    ]
    assert joint_details["limit_parameters"]["angle_limits_degrees"] == [
        "revolute",
        "cylindrical",
    ]
    assert "one rigid mechanism body" in description["api_details"]["component"][
        "definition_rule"
    ]
    assert description["solver_codes"]["-6"] == "no_grounded_component"
    assert description["capability_inventory"]["joint_graph"]["status"] == "supported"
    assert any(
        "vertex anchors" in feature
        for feature in description["capability_inventory"]["joint_graph"]["features"]
    )
    assert (
        "Slider joint"
        in description["joint_types"]["coupled_joint_dependencies"]["screw"]
    )
    assert [step["action"] for step in description["model_workflow"]] == [
        "discover",
        "plan_frames",
        "author_graph",
        "solve",
        "simulate",
        "present",
        "document",
        "repair",
        "verify",
    ]
    assert description["operation_selection"]["named_parts_table"] == (
        "api.bill_of_materials"
    )
    assert description["operation_selection"]["standard_hardware_occurrence"] == (
        "api.fastener"
    )
    assert description["operation_selection"]["repeated_source_occurrences"] == (
        "api.instances"
    )
    assert "catalog_key" in description["input_reference_contract"]["purpose"]
    assert description["input_reference_contract"]["schema"] == {
        "type": "object",
        "x-vibecad-reference": True,
    }
    assert "fastener_catalog.search" in description["standard_hardware"]["selection"]
    assert "no aliases" in description["operation_selection"]["redundancy_contract"]
    assert "failed_segment_index" in description["nested_subassemblies"]["repair"]
    assert any(
        "nested flexible links" in feature
        for feature in description["capability_inventory"]["component_occurrences"][
            "features"
        ]
    )
    assert (
        "collinear slider"
        in description["joint_selection_guide"]["couple_linear_rack_to_rotation"]
    )
    assert "axis" in description["coordinate_system"]["placement"]["rotation"]
    assert "angle_degrees" in description["coordinate_system"]["placement"]["rotation"]
    assert (
        description["capability_inventory"]["kinematic_simulation"]["status"]
        == "supported"
    )
    assert description["capability_inventory"]["exploded_views"]["status"] == (
        "supported"
    )
    assert (
        description["capability_inventory"]["bills_of_materials"]["status"]
        == "supported"
    )
    assert (
        "exploded views"
        not in description["capability_inventory"]["not_yet_provider_exposed"]
    )
    assert description["capability_inventory"]["not_yet_provider_exposed"] == []
    assert (
        "no separate add-column"
        in description["bills_of_materials"]["single_operation_rule"]
    )
    assert (
        "available_occurrence_paths" in description["bills_of_materials"]["inspection"]
    )
    assert description["publication_contract"]["native_types"]["bom"].startswith(
        "stable frozen Assembly::BomObject"
    )
    assert description["units"]["angular_motion_formula"] == "radians"
    assert description["units"]["linear_motion_formula"] == "millimetres"
    assert any(
        pattern["goal"] == "joint to a nested occurrence in a flexible subassembly"
        for pattern in description["recommended_patterns"]
    )
    for pattern in description["recommended_patterns"]:
        domains.validate_program_source(pattern["source"])

    def reference(name: str) -> dict[str, str]:
        return {"document_uid": "document", "object_name": name}

    base = api.component(reference("BaseSource"), grounded=True, label="Base")
    bolt = api.fastener(
        "ISO4762",
        "M6",
        length_mm=20,
        model_thread=True,
        placement=[0, 0, 5],
        label="Mounting Bolt",
    )
    arm = api.component(
        reference("ArmSource"),
        placement={"position": [0, 0, 20], "rotation": [0, 0, 0, 2]},
        label="Arm",
    )
    repeated = api.instances(
        {
            "document_uid": "component-document",
            "object_name": "Bracket",
            "document_path": "parts/bracket.FCStd",
        },
        [[0, 0, 0], [25, 0, 0], [50, 0, 0]],
        grounded_index=0,
        labels=["Bracket 1", "Bracket 2", "Bracket 3"],
    )
    hinge = api.joint(
        "revolute",
        api.connector(base, "Face1"),
        api.connector(arm, {"type": "exact_subelement", "subelement": "Face2"}),
        angle_limits_degrees=[-90, 90],
        label="Hinge",
    )
    model = api.assembly([base, arm], [hinge], label="Robot Arm")
    diagnostics = api.solve(model)
    verification = api.mechanism_check(
        model,
        requirements=[
            {
                "type": "minimum_clearance",
                "first": base,
                "second": arm,
                "minimum_mm": 0.25,
                "tolerance_mm": 0.01,
            }
        ],
        label="Static clearance",
    )
    allowed_contact = api.mechanism_check(
        model,
        contacts=[
            {
                "first": base,
                "second": arm,
                "policy": "allowed",
                "first_interface": "MatingFace",
                "second_interface": "SeatFace",
                "tolerance_mm": 0.01,
            }
        ],
    )
    drive = api.motion(hinge, "initialValue + pi/2*time")
    simulation = api.simulation(
        model,
        [drive],
        end_time_s=2,
        time_step_s=0.1,
    )
    exploded = api.exploded_view(
        model,
        [
            {"components": [arm], "transform": [0, 0, 40]},
            {"components": [base, arm], "radial_distance_mm": 15},
        ],
        label="Service View",
    )
    bill = api.bill_of_materials(
        model,
        columns=[
            "index",
            "name",
            "quantity",
            {"property": "PartNumber", "heading": "Part Number"},
            {"heading": "Description"},
        ],
        row_overrides=[
            {
                "occurrence_path": "Arm",
                "values": {"Description": "Moving link"},
            }
        ],
        label="Service BOM",
    )

    assert base.properties["grounded"] is True
    assert bolt.operation == "fastener"
    assert bolt.output_type == "component_link"
    assert bolt.arguments == ("ISO4762", "M6")
    assert bolt.properties["length_mm"] == 20.0
    assert bolt.properties["model_thread"] is True
    assert bolt.properties["placement"]["position"] == (0.0, 0.0, 5.0)
    assert arm.properties["placement"]["rotation"] == (0.0, 0.0, 0.0, 1.0)
    assert len(repeated) == 3
    assert all(item.operation == "component" for item in repeated)
    assert all(item.output_type == "component_link" for item in repeated)
    assert repeated[0].properties["grounded"] is True
    assert repeated[1].properties["grounded"] is False
    assert repeated[2].properties["placement"]["position"] == (50.0, 0.0, 0.0)
    assert repeated[1].arguments[0] == {
        "document_uid": "component-document",
        "object_name": "Bracket",
        "document_path": "parts/bracket.FCStd",
    }
    assert model.properties["components"] == (base, arm)
    assert model.properties["joints"] == (hinge,)
    assert diagnostics.arguments == (model,)
    assert verification.arguments == (model,)
    assert verification.output_type == "mechanism_verification"
    assert verification.properties["requirements"][0]["first"] is base
    assert verification.properties["requirements"][0]["minimum_mm"] == 0.25
    assert allowed_contact.properties["contacts"][0]["policy"] == "allowed"
    assert allowed_contact.properties["contacts"][0]["first_interface"] == "MatingFace"
    assert drive.arguments == (hinge,)
    assert drive.properties["motion_type"] == "angular"
    assert simulation.arguments == (model,)
    assert simulation.properties["motions"] == (drive,)
    assert simulation.properties["estimated_frame_limit"] == 22
    assert exploded.arguments == (model,)
    assert exploded.properties["moves"][0]["kind"] == "normal"
    assert exploded.properties["moves"][0]["components"] == (arm,)
    assert exploded.properties["moves"][0]["transform"]["position"] == (
        0.0,
        0.0,
        40.0,
    )
    assert exploded.properties["moves"][1]["kind"] == "radial"
    assert exploded.properties["moves"][1]["radial_distance_mm"] == 15.0
    assert bill.arguments == (model,)
    assert bill.output_type == "bom"
    assert [dict(column) for column in bill.properties["columns"]] == [
        {
            "kind": "builtin",
            "key": "index",
            "heading": "Index",
            "native_name": "Index",
        },
        {
            "kind": "builtin",
            "key": "name",
            "heading": "Name",
            "native_name": "Name",
        },
        {
            "kind": "builtin",
            "key": "quantity",
            "heading": "Quantity",
            "native_name": "Quantity",
        },
        {
            "kind": "property",
            "property": "PartNumber",
            "heading": "Part Number",
            "native_name": ".PartNumber",
        },
        {
            "kind": "custom",
            "heading": "Description",
            "native_name": "Description",
        },
    ]
    assert [
        {
            "occurrence_path": str(item["occurrence_path"]),
            "values": dict(item["values"]),
        }
        for item in bill.properties["row_overrides"]
    ] == [
        {
            "occurrence_path": "Arm",
            "values": {"Description": "Moving link"},
        }
    ]
    with pytest.raises(TypeError):
        model.properties["components"][0] = arm


def test_assembly_api_exposes_native_signed_parameters_anchors_and_open_limits() -> (
    None
):
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("AssemblyWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)

    def reference(name: str) -> dict[str, str]:
        return {"document_uid": "document", "object_name": name}

    first = api.component(
        reference("First"),
        placement={
            "position": [1, 2, 3],
            "axis": [0, 0, 2],
            "angle_degrees": 90,
        },
    )
    second = api.component(reference("Second"))
    rotation = first.properties["placement"]["rotation"]
    assert tuple(rotation) == pytest.approx((0.0, 0.0, 2**-0.5, 2**-0.5))

    anchored = api.connector(first, "Edge1", anchor="Vertex1")
    assert anchored.properties["anchor"] == "Vertex1"
    assert anchored.properties["selection"] == {
        "type": "exact_subelement",
        "subelement": "Edge1",
    }
    measured = api.connector(
        first,
        {
            "type": "query",
            "element_type": "face",
            "expected_count": 1,
            "geometry_type": "Cylinder",
            "radius": 20.0,
            "radius_tolerance": 1.0e-6,
            "near_point": [0.0, 0.0, 31.0],
            "max_distance": 1.0e-6,
        },
    )
    assert measured.properties["selection"] == {
        "type": "query",
        "element_type": "face",
        "expected_count": 1,
        "geometry_type": "Cylinder",
        "radius": 20.0,
        "radius_tolerance": 1.0e-6,
        "near_point": (0.0, 0.0, 31.0),
        "max_distance": 1.0e-6,
    }

    slider = api.joint(
        "slider",
        api.connector(first),
        api.connector(second),
        length_limits_mm={"minimum": None, "maximum": 25},
    )
    revolute = api.joint(
        "revolute",
        api.connector(first),
        api.connector(second),
        angle_limits_degrees=[-45, None],
    )
    distance = api.joint(
        "distance",
        api.connector(first),
        api.connector(second),
        distance_mm=-8,
    )
    rack = api.joint(
        "rack_pinion",
        api.connector(first),
        api.connector(second),
        pitch_radius_mm=-4,
    )
    screw = api.joint(
        "screw",
        api.connector(first),
        api.connector(second),
        thread_pitch_mm=-2,
    )

    assert slider.properties["length_limits_mm"] == (None, 25.0)
    assert revolute.properties["angle_limits_degrees"] == (-45.0, None)
    assert distance.properties["parameters"]["distance_mm"] == -8.0
    assert rack.properties["parameters"]["pitch_radius_mm"] == -4.0
    assert screw.properties["parameters"]["thread_pitch_mm"] == -2.0


def test_assembly_api_rejects_ambiguous_graphs_and_wrong_joint_parameters() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("AssemblyWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)

    def reference(name: str) -> dict[str, str]:
        return {"document_uid": "document", "object_name": name}

    first = api.component(reference("First"))
    second = api.component(reference("Second"))

    with pytest.raises(ValueError, match=r"api\.component.*source"):
        api.component({"object_name": "First"})
    with pytest.raises(ValueError, match=r"api\.instances.*placements"):
        api.instances(reference("First"), [])
    with pytest.raises(ValueError, match=r"api\.instances.*grounded_index"):
        api.instances(reference("First"), [[0, 0, 0]], grounded_index=1)
    with pytest.raises(ValueError, match=r"api\.instances.*labels"):
        api.instances(
            reference("First"),
            [[0, 0, 0], [1, 0, 0]],
            labels=["Only one"],
        )
    with pytest.raises(ValueError, match=r"api\.connector.*selection"):
        api.connector(first, "Face0")
    with pytest.raises(ValueError, match=r"api\.connector.*anchor.*exact"):
        api.connector(first, "Face1", anchor="center")
    with pytest.raises(ValueError, match=r"api\.connector.*anchor.*only"):
        api.connector(first, "origin", anchor="Vertex1")
    with pytest.raises(ValueError, match=r"api\.component.*axis.*supplied together"):
        api.component(reference("AxisOnly"), placement={"axis": [0, 0, 1]})
    with pytest.raises(
        ValueError, match=r"api\.component.*rotation cannot be combined"
    ):
        api.component(
            reference("MixedRotation"),
            placement={
                "rotation": [0, 0, 0, 1],
                "axis": [0, 0, 1],
                "angle_degrees": 30,
            },
        )
    with pytest.raises(ValueError, match=r"api\.joint.*different component"):
        api.joint("fixed", api.connector(first), api.connector(first))
    with pytest.raises(ValueError, match=r"api\.joint.*distance_mm.*required"):
        api.joint("distance", api.connector(first), api.connector(second))
    with pytest.raises(ValueError, match=r"api\.joint.*distance_mm.*does not apply"):
        api.joint(
            "revolute",
            api.connector(first),
            api.connector(second),
            distance_mm=2,
        )
    with pytest.raises(ValueError, match=r"api\.joint.*length_limits_mm"):
        api.joint(
            "revolute",
            api.connector(first),
            api.connector(second),
            length_limits_mm=[0, 5],
        )
    with pytest.raises(ValueError, match=r"api\.joint.*length_limits_mm.*at least one"):
        api.joint(
            "slider",
            api.connector(first),
            api.connector(second),
            length_limits_mm=[None, None],
        )
    with pytest.raises(ValueError, match=r"api\.joint.*pitch_radius_mm.*non-zero"):
        api.joint(
            "rack_pinion",
            api.connector(first),
            api.connector(second),
            pitch_radius_mm=0,
        )
    with pytest.raises(ValueError, match=r"api\.joint.*radius1_mm.*greater than"):
        api.joint(
            "gears",
            api.connector(first),
            api.connector(second),
            radius1_mm=-1,
            radius2_mm=2,
        )
    joint = api.joint("fixed", api.connector(first), api.connector(second))
    third = api.component(reference("Third"))
    with pytest.raises(ValueError, match=r"api\.assembly.*not listed"):
        api.assembly([first, third], [joint])
    with pytest.raises(ValueError, match=r"api\.assembly.*same graph value"):
        api.assembly([first, first])

    revolute = api.joint("revolute", api.connector(first), api.connector(second))
    slider = api.joint("slider", api.connector(first), api.connector(second))
    fixed = api.joint("fixed", api.connector(first), api.connector(second))
    cylindrical = api.joint("cylindrical", api.connector(first), api.connector(second))
    mechanism = api.assembly([first, second], [revolute])
    scoped_mechanism = api.assembly(
        {"Base": first, "Arm": second},
        {"Hinge": revolute},
    )
    assert scoped_mechanism.properties["component_names"] == ("Base", "Arm")
    assert scoped_mechanism.properties["joint_names"] == ("Hinge",)
    with pytest.raises(ValueError, match=r"api\.assembly.*stable-key mapping"):
        api.assembly({"Base": first, "Arm": second}, [revolute])
    with pytest.raises(ValueError, match=r"api\.assembly.*unique across the graph"):
        api.assembly({"Member": first, "Arm": second}, {"Member": revolute})
    drive = api.motion(revolute, "initialValue + pi/2*time")
    assert drive.properties["formula"] == "initialValue + pi/2*time"
    assert (
        api.motion(slider, "initialValue + 10*time").properties["motion_type"]
        == "linear"
    )
    with pytest.raises(ValueError, match=r"api\.motion.*joint.*supported only"):
        api.motion(fixed, "time")
    with pytest.raises(ValueError, match=r"api\.motion.*cylindrical.*explicit"):
        api.motion(cylindrical, "time")
    with pytest.raises(ValueError, match=r"api\.motion.*motion_type"):
        api.motion(revolute, "time", motion_type="linear")
    for formula in (
        "__import__('os')",
        "time.real",
        "sqrt(time)",
        "[time]",
    ):
        with pytest.raises(ValueError, match=r"api\.motion.*formula"):
            api.motion(revolute, formula)
    with pytest.raises(ValueError, match=r"api\.simulation.*same graph value"):
        api.simulation(mechanism, [drive, drive])
    scoped_simulation = api.simulation(scoped_mechanism, {"HingeDrive": drive})
    assert scoped_simulation.properties["motion_names"] == ("HingeDrive",)
    with pytest.raises(ValueError, match=r"api\.simulation.*greater than"):
        api.simulation(mechanism, [drive], start_time_s=1, end_time_s=1)
    with pytest.raises(ValueError, match=r"api\.simulation.*10000 native frames"):
        api.simulation(mechanism, [drive], end_time_s=100, time_step_s=0.001)
    with pytest.raises(ValueError, match=r"api\.exploded_view.*1 through 4096"):
        api.exploded_view(mechanism, [])
    with pytest.raises(ValueError, match=r"api\.exploded_view.*exactly one"):
        api.exploded_view(mechanism, [{"components": [first]}])
    with pytest.raises(ValueError, match=r"api\.exploded_view.*exactly one"):
        api.exploded_view(
            mechanism,
            [
                {
                    "components": [first],
                    "transform": [0, 0, 1],
                    "radial_distance_mm": 2,
                }
            ],
        )
    with pytest.raises(ValueError, match=r"api\.exploded_view.*unknown keys"):
        api.exploded_view(
            mechanism,
            [{"components": [first], "transform": [0, 0, 1], "distance": 2}],
        )
    with pytest.raises(ValueError, match=r"api\.exploded_view.*same graph value"):
        api.exploded_view(
            mechanism,
            [{"components": [first, first], "transform": [0, 0, 1]}],
        )
    foreign = api.component(reference("Foreign"))
    with pytest.raises(ValueError, match=r"api\.exploded_view.*not listed"):
        api.exploded_view(
            mechanism,
            [{"components": [foreign], "transform": [0, 0, 1]}],
        )
    with pytest.raises(ValueError, match=r"api\.exploded_view.*translate or rotate"):
        api.exploded_view(
            mechanism,
            [{"components": [first], "transform": [0, 0, 0]}],
        )
    with pytest.raises(ValueError, match=r"api\.exploded_view.*greater than"):
        api.exploded_view(
            mechanism,
            [{"components": [first], "radial_distance_mm": 0}],
        )
    with pytest.raises(ValueError, match=r"api\.bill_of_materials.*include the 'name'"):
        api.bill_of_materials(mechanism, columns=["quantity"])
    with pytest.raises(
        ValueError,
        match=r"api\.bill_of_materials.*duplicates column identity.*keep one best version",
    ):
        api.bill_of_materials(
            mechanism,
            columns=[
                "name",
                {"property": "PartNumber", "heading": "Part Number"},
                {"property": "PartNumber", "heading": "PN"},
            ],
        )
    with pytest.raises(
        ValueError, match=r"api\.bill_of_materials.*undeclared custom headings"
    ):
        api.bill_of_materials(
            mechanism,
            columns=["name", {"heading": "Description"}],
            row_overrides=[{"occurrence_path": "First", "values": {"Notes": "Base"}}],
        )
    with pytest.raises(ValueError, match=r"api\.bill_of_materials.*is duplicated"):
        api.bill_of_materials(
            mechanism,
            columns=["name", {"heading": "Description"}],
            row_overrides=[
                {"occurrence_path": "First", "values": {"Description": "Base"}},
                {"occurrence_path": "First", "values": {"Description": "Fixed"}},
            ],
        )
    with pytest.raises(ValueError, match=r"api\.bill_of_materials.*assembly"):
        api.bill_of_materials(first)
    with pytest.raises(
        ValueError,
        match=r"api\.mechanism_check.*at least one explicit pair",
    ):
        api.mechanism_check(mechanism)
    with pytest.raises(
        ValueError,
        match=r"api\.mechanism_check.*tolerance_mm",
    ):
        api.mechanism_check(
            mechanism,
            requirements=[
                {
                    "type": "collision_free",
                    "first": first,
                    "second": second,
                }
            ],
        )
    with pytest.raises(
        ValueError,
        match=r"api\.mechanism_check.*duplicates the unordered pair",
    ):
        api.mechanism_check(
            mechanism,
            requirements=[
                {
                    "type": "collision_free",
                    "first": first,
                    "second": second,
                    "tolerance_mm": 0.01,
                }
            ],
            contacts=[
                {
                    "first": second,
                    "second": first,
                    "policy": "prohibited",
                    "tolerance_mm": 0.01,
                }
            ],
        )
    with pytest.raises(
        ValueError,
        match=r"api\.mechanism_check.*evaluated requirement",
    ):
        api.mechanism_check(
            mechanism,
            contacts=[
                {
                    "first": first,
                    "second": second,
                    "policy": "ignored",
                    "reason": "Reference envelope",
                }
            ],
        )


@pytest.mark.parametrize(
    "workbench",
    ["PartDesignWorkbench", "AssemblyWorkbench", "RobotWorkbench"],
)
def test_component_capable_domains_share_one_placement_vocabulary(
    workbench: str,
) -> None:
    import math

    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack(workbench)
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    reference = {"document_uid": "components", "object_name": "LinearRail"}

    occurrence = api.component(
        reference,
        placement={
            "position": [10, 20, 30],
            "axis": [0, 0, 1],
            "angle_degrees": 90,
        },
        label="Rail",
    )
    implicit = api.component(reference)
    repeated = api.instances(
        reference,
        [[0, 0, 0], {"position": [100, 0, 0], "rotation": [0, 0, 0, 2]}],
        labels=["Rail 1", "Rail 2"],
    )

    assert occurrence.operation == "component"
    assert occurrence.output_type == "component_link"
    assert occurrence.arguments[0] == reference
    assert occurrence.properties["placement"]["position"] == (10.0, 20.0, 30.0)
    assert math.isclose(
        occurrence.properties["placement"]["rotation"][2],
        math.sqrt(0.5),
        abs_tol=1.0e-12,
    )
    assert occurrence.properties["placement_authored"] is True
    assert implicit.properties["placement_authored"] is False
    assert len(repeated) == 2
    assert repeated[1].properties["placement"]["position"] == (100.0, 0.0, 0.0)
    assert repeated[1].properties["placement"]["rotation"] == (0.0, 0.0, 0.0, 1.0)


def test_assembly_bom_planner_keeps_model_paths_exact_and_actionable() -> None:
    from VibeCADAssemblyBOM import AssemblyBOMError, plan_assembly_bom

    root_identity = {"document_uid": "source-document", "object_name": "Module"}
    gear_identity = {"document_uid": "source-document", "object_name": "Gear"}
    hierarchy = {
        "schema": "vibecad-assembly-source-hierarchy-v1",
        "root_node_id": "node-module",
        "nodes": [
            {
                "node_id": "node-module",
                "identity": root_identity,
                "kind": "assembly",
                "label": "Drive Module",
                "document_file_name": "drive-module.FCStd",
                "bom_properties": [
                    {
                        "name": "PartNumber",
                        "property_type": "App::PropertyString",
                        "kind": "string",
                        "value": "MOD-001",
                    }
                ],
                "occurrences": [
                    {
                        "name": "GearLeft",
                        "source_node_id": "node-gear",
                        "scale": 1.0,
                    },
                    {
                        "name": "GearRight",
                        "source_node_id": "node-gear",
                        "scale": 1.0,
                    },
                ],
            },
            {
                "node_id": "node-gear",
                "identity": gear_identity,
                "kind": "shape",
                "label": "Gear",
                "document_file_name": "gear.FCStd",
                "bom_properties": [
                    {
                        "name": "PartNumber",
                        "property_type": "App::PropertyString",
                        "kind": "string",
                        "value": "GEAR-020",
                    }
                ],
                "occurrences": [],
            },
        ],
    }
    component_sources = [
        {
            "output_name": "Module",
            "reference": {
                **root_identity,
                "assembly_hierarchy": hierarchy,
            },
        }
    ]
    columns = [
        {
            "kind": "builtin",
            "key": "index",
            "heading": "Index",
            "native_name": "Index",
        },
        {
            "kind": "builtin",
            "key": "name",
            "heading": "Name",
            "native_name": "Name",
        },
        {
            "kind": "builtin",
            "key": "quantity",
            "heading": "Quantity",
            "native_name": "Quantity",
        },
        {
            "kind": "property",
            "property": "PartNumber",
            "heading": "Part Number",
            "native_name": ".PartNumber",
        },
        {
            "kind": "custom",
            "heading": "Description",
            "native_name": "Description",
        },
    ]
    contract = plan_assembly_bom(
        component_sources,
        columns=columns,
        detail_subassemblies=True,
        detail_parts=True,
        only_parts=False,
        row_overrides=[
            {
                "occurrence_path": "Module/GearLeft",
                "values": {"Description": "Matched gear"},
            }
        ],
    )
    assert contract["row_count"] == 2
    assert contract["used_range"] == ["A1", "E3"]
    assert contract["rows"][0]["occurrence_paths"] == ["Module"]
    assert contract["rows"][1]["occurrence_paths"] == [
        "Module/GearLeft",
        "Module/GearRight",
    ]
    assert contract["rows"][1]["cells"] == {
        "Index": "1.1",
        "Name": "Gear",
        "Quantity": "2",
        "Part Number": "GEAR-020",
        "Description": "Matched gear",
    }
    assert len(contract["table_sha256"]) == 64
    assert contract == plan_assembly_bom(
        component_sources,
        columns=columns,
        detail_subassemblies=True,
        detail_parts=True,
        only_parts=False,
        row_overrides=[
            {
                "occurrence_path": "Module/GearLeft",
                "values": {"Description": "Matched gear"},
            }
        ],
    )

    with pytest.raises(AssemblyBOMError) as conflict:
        plan_assembly_bom(
            component_sources,
            columns=columns,
            detail_subassemblies=True,
            detail_parts=True,
            only_parts=False,
            row_overrides=[
                {
                    "occurrence_path": "Module/GearLeft",
                    "values": {"Description": "Left gear"},
                },
                {
                    "occurrence_path": "Module/GearRight",
                    "values": {"Description": "Right gear"},
                },
            ],
        )
    assert conflict.value.details["stage"] == "bom_row_overrides"
    assert conflict.value.details["heading"] == "Description"
    assert conflict.value.details["conflicting_occurrence_paths"] == [
        "Module/GearLeft",
        "Module/GearRight",
    ]
    assert "omit 'quantity'" in conflict.value.details["correction"]

    with pytest.raises(AssemblyBOMError) as unknown:
        plan_assembly_bom(
            component_sources,
            columns=columns,
            detail_subassemblies=True,
            detail_parts=True,
            only_parts=False,
            row_overrides=[
                {
                    "occurrence_path": "Module/GearCenter",
                    "values": {"Description": "Center gear"},
                }
            ],
        )
    assert unknown.value.details["requested_path"] == "Module/GearCenter"
    assert unknown.value.details["available_occurrence_paths"] == [
        "Module",
        "Module/GearLeft",
        "Module/GearRight",
    ]
    assert unknown.value.details["settings"] == {
        "detail_subassemblies": True,
        "detail_parts": True,
        "only_parts": False,
    }

    separate_columns = [column for column in columns if column.get("key") != "quantity"]
    separate = plan_assembly_bom(
        component_sources,
        columns=separate_columns,
        detail_subassemblies=True,
        detail_parts=True,
        only_parts=False,
        row_overrides=[
            {
                "occurrence_path": "Module/GearLeft",
                "values": {"Description": "Left gear"},
            },
            {
                "occurrence_path": "Module/GearRight",
                "values": {"Description": "Right gear"},
            },
        ],
    )
    assert [row["occurrence_paths"] for row in separate["rows"]] == [
        ["Module"],
        ["Module/GearLeft"],
        ["Module/GearRight"],
    ]
    assert [row["cells"]["Description"] for row in separate["rows"][1:]] == [
        "Left gear",
        "Right gear",
    ]

    only_containers = plan_assembly_bom(
        component_sources,
        columns=columns,
        detail_subassemblies=True,
        detail_parts=True,
        only_parts=True,
        row_overrides=[],
    )
    assert [row["occurrence_paths"] for row in only_containers["rows"]] == [["Module"]]

    with pytest.raises(AssemblyBOMError) as unavailable_hierarchy:
        plan_assembly_bom(
            [
                {
                    "output_name": "Module",
                    "reference": {
                        **root_identity,
                        "source_kind": "assembly",
                        "label": "Drive Module",
                        "document_file_name": "drive-module.FCStd",
                        "bom_properties": [],
                    },
                }
            ],
            columns=columns,
            detail_subassemblies=True,
            detail_parts=True,
            only_parts=False,
            row_overrides=[],
        )
    assert unavailable_hierarchy.value.details["stage"] == "bom_source_hierarchy"
    assert unavailable_hierarchy.value.details["occurrence_path"] == "Module"
    assert (
        "detail_subassemblies=False"
        in unavailable_hierarchy.value.details["correction"]
    )

    with pytest.raises(AssemblyBOMError) as oversized:
        plan_assembly_bom(
            [
                {
                    "output_name": f"Component{index:03d}",
                    "reference": {
                        "document_uid": "source-document",
                        "object_name": f"Source{index:03d}",
                        "source_kind": "shape",
                        "label": "X" * 4096,
                        "document_file_name": "large-module.FCStd",
                        "bom_properties": [],
                    },
                }
                for index in range(100)
            ],
            columns=[columns[1]],
            detail_subassemblies=False,
            detail_parts=False,
            only_parts=False,
            row_overrides=[],
        )
    assert oversized.value.details["stage"] == "bom_budget"
    assert (
        oversized.value.details["observed_contract_bytes"]
        > (oversized.value.details["maximum_contract_bytes"])
    )
    assert "split the design" in oversized.value.details["correction"]


def test_assembly_occurrence_global_placement_failure_is_never_silently_local() -> None:
    from vibescript_assembly_worker import (
        AssemblyCandidateError,
        _global_placement_fact,
    )

    class BrokenOccurrence:
        Name = "NestedGear"

        @staticmethod
        def getGlobalPlacement():
            raise RuntimeError("native placement unavailable")

    with pytest.raises(AssemblyCandidateError) as failure:
        _global_placement_fact(
            BrokenOccurrence(),
            context="component output 'Drive' occurrence 'Core/Gear'",
        )
    assert failure.value.details["stage"] == "assembly_occurrence_placement"
    assert failure.value.details["native_object"] == "NestedGear"
    assert "same stable occurrence_path" in failure.value.details["correction"]


def test_assembly_solver_distinguishes_benign_redundancy_from_conflicts() -> None:
    from vibescript_assembly_worker import _diagnostics_reject_solution

    assert not _diagnostics_reject_solution(
        {
            "has_redundancies": True,
            "has_partial_redundancies": True,
            "has_conflicts": False,
            "has_malformed_constraints": False,
        }
    )
    assert _diagnostics_reject_solution({"has_conflicts": True})
    assert _diagnostics_reject_solution({"has_malformed_constraints": True})


def test_mesh_api_is_explicit_bounded_and_generated_from_runtime() -> None:
    from vibescript_domain_api import create_domain_api
    from vibescript_meshpart_worker import validate_meshpart_definition
    from vibescript_mesh_worker import validate_mesh_definition

    pack = domains.get_vibescript_pack("MeshWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    adapter = domains.get_domain_adapter(pack.domain)
    assert adapter is not None
    description = adapter.describe_api()

    assert description["api_contract"] == "vibecad-vibescript-mesh-api-v1"
    assert pack.output_types == (
        "mesh",
        "solid",
        "shell",
        "face",
        "wire",
        "compound",
    )
    assert pack.api_exports[-2:] == ("mesh_from_shape", "shape_from_mesh")
    assert tuple(api.exported_names) == pack.api_exports
    assert [item["name"] for item in description["runtime_exports"]] == list(
        pack.api_exports
    )
    assert all(item["description"] for item in description["runtime_exports"])
    assert all(
        "*args" not in item["signature"] and "**" not in item["signature"]
        for item in description["runtime_exports"]
    )
    assert "self-intersection" in description["operation_contracts"]["diagnostics"]
    assert "FreeCADCmd" in description["evaluation_model"]
    assert set(description["operation_selection"]) == set(pack.api_exports)
    assert "default-only diagnostics" in description["redundancy_contract"]
    assert "One api.repair call consolidates" in description["redundancy_contract"]
    assert (
        "next_write_expected_revision"
        in description["model_verification_contract"]["failure_repair"]
    )
    assert "active workbench determines" in description["workbench_handoffs"]["rule"]
    assert (
        len(
            json.dumps(description, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        < 32_000
    )
    for pattern in description["recommended_patterns"]:
        domains.validate_program_source(pattern["source"])

    tetrahedron = [
        [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        [[0, 0, 0], [0, 0, 1], [1, 0, 0]],
        [[0, 0, 0], [0, 1, 0], [0, 0, 1]],
        [[1, 0, 0], [0, 0, 1], [0, 1, 0]],
    ]
    raw = api.mesh(tetrahedron, label="Raw")
    imported = api.from_object(
        {"document_uid": "document", "object_name": "ExistingMesh"},
        label="Imported",
    )
    assert imported.arguments[0] == {
        "document_uid": "document",
        "object_name": "ExistingMesh",
    }
    assert validate_mesh_definition(imported, require_domain_value=True) == (
        imported.to_payload()
    )
    transformed = api.transform(
        raw,
        translation=[10, 20, 30],
        rotation=[0, 0, 0, 2],
        scale=[2, 3, 4],
        label="Moved",
    )
    union = api.union(
        raw,
        transformed,
        linear_deflection=0.05,
        angular_deflection_degrees=20,
        relative=True,
        label="Combined",
    )
    difference = api.difference(raw, transformed, label="Subtracted")
    intersection = api.intersection(raw, transformed, label="Shared")
    for boolean in (union, difference, intersection):
        assert (
            validate_mesh_definition(
                boolean,
                require_domain_value=True,
            )
            == boolean.to_payload()
        )
        assert len(boolean.arguments) == 2
    assert union.properties["linear_deflection"] == 0.05
    assert union.properties["angular_deflection_degrees"] == 20.0
    assert union.properties["relative"] is True
    repaired = api.repair(
        transformed,
        remove_non_manifolds=True,
        fix_self_intersections=True,
        fill_holes_max_edges=12,
        decimate_reduction=0.25,
        decimate_tolerance=0.1,
        label="Repaired",
    )
    checked = api.diagnostics(
        repaired,
        require_solid=True,
        require_closed=True,
        require_manifold=True,
        require_consistent_orientation=True,
        require_no_self_intersections=True,
        max_components=1,
        max_open_edges=0,
        label="Checked",
    )
    payload = checked.to_payload()
    assert payload["properties"]["max_open_edges"] == 0
    assert payload["arguments"][0]["properties"]["fill_holes_max_edges"] == 12
    assert payload["arguments"][0]["arguments"][0]["properties"]["rotation"] == [
        0.0,
        0.0,
        0.0,
        1.0,
    ]
    huge_rotation = api.transform(raw, rotation=[1.0e308, 0, 0, 0])
    assert huge_rotation.properties["rotation"] == (1.0, 0.0, 0.0, 0.0)
    assert (
        validate_mesh_definition(
            checked,
            require_domain_value=True,
        )
        == payload
    )
    conversion_reference = {
        "document_uid": "document",
        "object_name": "ConversionSource",
    }
    meshed = api.mesh_from_shape(conversion_reference, label="Converted Mesh")
    recovered = api.shape_from_mesh(
        conversion_reference,
        output_type="solid",
        label="Recovered Solid",
    )
    for conversion in (meshed, recovered):
        assert conversion.domain == "mesh"
        assert (
            validate_meshpart_definition(
                conversion,
                definition_domain="mesh",
            )
            == conversion.to_payload()
        )
    with pytest.raises(
        ValueError,
        match=r"api\.transform.*publish.*api\.from_object",
    ):
        api.transform(meshed)
    with pytest.raises(TypeError):
        raw.arguments[0][0][0] = (9.0, 9.0, 9.0)


def test_meshpart_compatibility_context_remains_exactly_unchanged() -> None:
    from vibescript_domain_api import create_domain_api
    from vibescript_meshpart_worker import validate_meshpart_definition

    pack = domains.get_vibescript_pack("MeshPartWorkbench")
    assert pack is not None
    assert pack.domain == "meshpart"
    assert pack.api_exports == ("mesh_from_shape", "shape_from_mesh")
    assert pack.output_types == (
        "mesh",
        "solid",
        "shell",
        "face",
        "wire",
        "compound",
    )
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    reference = {"document_uid": "document", "object_name": "LegacySource"}
    meshed = api.mesh_from_shape(reference)
    recovered = api.shape_from_mesh(reference, output_type="solid")
    assert meshed.domain == recovered.domain == "meshpart"
    assert validate_meshpart_definition(meshed) == meshed.to_payload()
    assert validate_meshpart_definition(recovered) == recovered.to_payload()


def test_mesh_api_rejects_malformed_or_unbounded_operations() -> None:
    from vibescript_domain_api import create_domain_api
    from vibescript_mesh_worker import validate_mesh_definition

    pack = domains.get_vibescript_pack("MeshWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    triangle = [[[0, 0, 0], [1, 0, 0], [0, 1, 0]]]
    raw = api.mesh(triangle)

    with pytest.raises(
        ValueError, match=r"api\.mesh.*1-200000 triangles"
    ) as source_failure:
        api.mesh([])
    assert source_failure.value.details["stage"] == "source_validation"
    assert source_failure.value.details["operation"] == "mesh"
    assert source_failure.value.details["parameter"] == "triangles"
    assert (
        "Change only the failing source expression"
        in source_failure.value.details["correction"]
    )
    with pytest.raises(ValueError, match=r"api\.from_object.*object_name"):
        api.from_object(
            {"document_uid": "document", "object_name": "not an internal name"}
        )
    with pytest.raises(ValueError, match=r"triangles\[0\].*exactly three"):
        api.mesh([[[0, 0, 0], [1, 0, 0]]])
    with pytest.raises(ValueError, match=r"finite"):
        api.mesh([[[0, 0, 0], [float("nan"), 0, 0], [0, 1, 0]]])
    with pytest.raises(ValueError, match=r"scale.*greater than 0"):
        api.transform(raw, scale=[1, 0, 1])
    with pytest.raises(ValueError, match=r"rotation.*non-zero"):
        api.transform(raw, rotation=[0, 0, 0, 0])
    for operation in ("union", "difference", "intersection"):
        identical = getattr(api, operation)(raw, raw)
        assert (
            validate_mesh_definition(
                identical,
                require_domain_value=True,
            )
            == identical.to_payload()
        )
    with pytest.raises(ValueError, match=r"linear_deflection.*greater than 0"):
        api.difference(
            raw, api.transform(raw, translation=[1, 0, 0]), linear_deflection=0
        )
    with pytest.raises(ValueError, match=r"angular_deflection_degrees.*at most 180"):
        api.intersection(
            raw,
            api.transform(raw, translation=[1, 0, 0]),
            angular_deflection_degrees=181,
        )
    with pytest.raises(ValueError, match=r"relative.*true or false"):
        api.union(
            raw,
            api.transform(raw, translation=[1, 0, 0]),
            relative=1,
        )
    with pytest.raises(ValueError, match=r"fill_holes_max_edges.*integer"):
        api.repair(raw, fill_holes_max_edges=True)
    with pytest.raises(ValueError, match=r"must both be zero"):
        api.repair(raw, decimate_reduction=0.5)
    with pytest.raises(ValueError, match=r"max_components.*integer"):
        api.diagnostics(raw, max_components=1.5)
    with pytest.raises(ValueError, match=r"require_closed.*true or false"):
        api.diagnostics(raw, require_closed=1)


def test_mesh_worker_failures_always_give_the_model_one_exact_correction() -> None:
    from vibescript_mesh_worker import MeshCandidateError

    requirement = MeshCandidateError(
        "Mesh is open.",
        details={
            "stage": "diagnostic_requirements",
            "failures": ["mesh is not closed"],
        },
    )
    assert "preserve the requirement" in requirement.details["correction"]

    reference = MeshCandidateError(
        "Source is unavailable.",
        details={"stage": "reference_selection", "object_name": "Source"},
    )
    assert "document_meshes" in reference.details["correction"]
    assert "api.from_object" in reference.details["correction"]

    explicit = MeshCandidateError(
        "Storage unavailable.",
        details={"stage": "artifact_export", "correction": "Repair fixture storage."},
    )
    assert explicit.details["correction"] == "Repair fixture storage."


def test_mesh_boolean_traces_bind_both_graph_branches_and_native_backend() -> None:
    from VibeCADVibeScriptDomainRuntime import _validate_mesh_trace
    from vibescript_domain_api import create_domain_api
    from vibescript_mesh_worker import _native_boolean

    pack = domains.get_vibescript_pack("MeshWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    tetrahedron = [
        [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        [[0, 0, 0], [0, 0, 1], [1, 0, 0]],
        [[0, 0, 0], [0, 1, 0], [0, 0, 1]],
        [[1, 0, 0], [0, 0, 1], [0, 1, 0]],
    ]
    first = api.mesh(tetrahedron, label="First")
    second_local = api.mesh(tetrahedron, label="Second")
    second = api.transform(second_local, translation=[0.25, 0, 0])
    quick = {
        "points": 4,
        "facets": 4,
        "open_edges": 0,
        "degenerated_facets": 0,
        "duplicated_facets": 0,
        "duplicated_points": 0,
        "components": 1,
    }
    operand_facts = {
        "points": 4,
        "facets": 4,
        "open_edges": 0,
        "components": 1,
        "is_solid": True,
        "volume_mm3": 1.0 / 6.0,
        "bounds": {
            "minimum": [0.0, 0.0, 0.0],
            "maximum": [1.0, 1.0, 1.0],
        },
    }
    second_facts = {
        **operand_facts,
        "bounds": {
            "minimum": [0.25, 0.0, 0.0],
            "maximum": [1.25, 1.0, 1.0],
        },
    }
    result_facts = {
        **operand_facts,
        "points": 8,
        "facets": 8,
        "volume_mm3": 0.25,
        "bounds": {
            "minimum": [0.0, 0.0, 0.0],
            "maximum": [1.25, 1.0, 1.0],
        },
    }
    observed_result = {
        **result_facts,
        "degenerated_facets": 0,
        "duplicated_facets": 0,
        "duplicated_points": 0,
    }

    def sha256(definition: dict) -> str:
        encoded = json.dumps(
            definition,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    for operation in ("union", "difference", "intersection"):
        definition = getattr(api, operation)(
            first,
            second,
            linear_deflection=0.05,
            angular_deflection_degrees=20,
            relative=False,
        ).to_payload()
        first_definition, second_definition = definition["arguments"]
        trace = [
            {
                "operation": "mesh",
                "input_facets": 4,
                "result": dict(quick),
            },
            {
                "operation": "mesh",
                "input_facets": 4,
                "result": dict(quick),
            },
            {
                "operation": "transform",
                "translation": [0.25, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "scale": [1.0, 1.0, 1.0],
                "before": dict(quick),
                "after": dict(quick),
            },
            {
                "operation": operation,
                "first_definition_sha256": sha256(first_definition),
                "second_definition_sha256": sha256(second_definition),
                "first": dict(operand_facts),
                "second": dict(second_facts),
                "tessellation": {
                    "linear_deflection": 0.05,
                    "angular_deflection_degrees": 20.0,
                    "relative": False,
                },
                "backend": "MeshPart::Boolean/OpenCASCADE",
                "result": dict(result_facts),
            },
        ]
        _validate_mesh_trace(
            trace,
            definition,
            output_name="Mesh",
            observed=observed_result,
            source_references={},
        )
        forged = json.loads(json.dumps(trace))
        forged[-1]["second_definition_sha256"] = "0" * 64
        with pytest.raises(ValueError, match="changed an operand"):
            _validate_mesh_trace(
                forged,
                definition,
                output_name="Mesh",
                observed=observed_result,
                source_references={},
            )

    native_source = inspect.getsource(_native_boolean)
    assert 'addObject("MeshPart::Boolean"' in native_source
    assert "tempfile" not in native_source
    assert "OpenSCAD" not in native_source
    assert not hasattr(api, "boolean")
    assert not hasattr(api, "csg")


def test_meshpart_api_is_canonical_typed_and_generated_from_runtime() -> None:
    from vibescript_domain_api import create_domain_api
    from vibescript_meshpart_worker import validate_meshpart_definition

    pack = domains.get_vibescript_pack("MeshPartWorkbench")
    assert pack is not None and pack.production_ready
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    adapter = domains.get_domain_adapter(pack.domain)
    assert adapter is not None and adapter.production_ready
    description = adapter.describe_api()

    assert description["api_contract"] == "vibecad-vibescript-meshpart-api-v1"
    assert (
        tuple(api.exported_names)
        == pack.api_exports
        == (
            "mesh_from_shape",
            "shape_from_mesh",
        )
    )
    assert [item["name"] for item in description["runtime_exports"]] == list(
        pack.api_exports
    )
    assert all(item["description"] for item in description["runtime_exports"])
    assert all(
        "*args" not in item["signature"] and "**" not in item["signature"]
        for item in description["runtime_exports"]
    )
    assert "consolidated" in description["redundancy_contract"]
    assert description["native_safety_contract"]["no_synchronous_fallback"] is True
    assert set(description["canonical_operations"]) == set(pack.api_exports)
    assert set(description["operation_selection"]) == set(pack.api_exports)
    assert (
        "Do not generate several mesher variants"
        in description["canonical_operations"]["mesh_from_shape"]["method_rule"]
    )
    assert (
        "Do not pass mesh_from_shape directly"
        in description["composition_contract"]["independent_sources"]
    )
    assert (
        "next_write_expected_revision"
        in description["model_verification_contract"]["failure_repair"]
    )
    assert "active workbench determines" in description["workbench_handoffs"]["rule"]
    assert (
        len(
            json.dumps(description, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        < 32_000
    )
    for pattern in description["recommended_patterns"]:
        domains.validate_program_source(pattern["source"])

    reference = {"document_uid": "document", "object_name": "Source"}
    mesh = api.mesh_from_shape(
        reference,
        subelements=["Face3", "Face1"],
        preserve_face_groups=True,
    )
    assert mesh.output_type == "mesh"
    assert mesh.properties["subelements"] == ("Face1", "Face3")
    assert mesh.properties["linear_deflection"] == 0.1
    assert mesh.properties["growth_rate"] is None
    assert validate_meshpart_definition(mesh) == mesh.to_payload()

    wire = api.shape_from_mesh(
        reference,
        output_type="wire",
        facet_indices=[3, 1, 2],
    )
    assert wire.properties["representation"] == "boundary"
    assert wire.properties["facet_indices"] == (1, 2, 3)
    assert wire.properties["tolerance"] is None
    assert validate_meshpart_definition(wire) == wire.to_payload()
    solid = api.shape_from_mesh(reference, output_type="solid")
    assert solid.properties["representation"] == "surface"
    assert solid.properties["require_closed"] is True
    assert solid.properties["tolerance"] == 0.01
    with pytest.raises(TypeError):
        mesh.properties["method"] = "max_area"


def test_meshpart_api_rejects_irrelevant_ambiguous_or_unbounded_operations() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("MeshPartWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    reference = {"document_uid": "document", "object_name": "Source"}
    with pytest.raises(
        ValueError, match=r"api\.mesh_from_shape.*source\.document_uid"
    ) as source_failure:
        api.mesh_from_shape({"document_uid": 1, "object_name": "Source"})
    assert source_failure.value.details["stage"] == "source_validation"
    assert source_failure.value.details["operation"] == "mesh_from_shape"
    assert source_failure.value.details["parameter"] == "source.document_uid"
    assert (
        "Change only the failing source expression"
        in source_failure.value.details["correction"]
    )
    cases = (
        (
            lambda: api.mesh_from_shape(reference, subelements=["Face1", "Shell1"]),
            r"api\.mesh_from_shape.*one topology class",
        ),
        (
            lambda: api.mesh_from_shape(reference, method="max_area"),
            r"api\.mesh_from_shape.*max_area.*required",
        ),
        (
            lambda: api.mesh_from_shape(
                reference, method="max_area", max_area=1, relative=True
            ),
            r"api\.mesh_from_shape.*relative.*not used",
        ),
        (
            lambda: api.mesh_from_shape(reference, fineness="fine"),
            r"api\.mesh_from_shape.*fineness.*not used",
        ),
        (
            lambda: api.mesh_from_shape(
                reference,
                method="netgen_fineness",
                second_order=True,
                allow_quad=True,
            ),
            r"api\.mesh_from_shape.*cannot both be true",
        ),
        (
            lambda: api.shape_from_mesh(reference, facet_indices=[1], segment_index=1),
            r"api\.shape_from_mesh.*mutually exclusive",
        ),
        (
            lambda: api.shape_from_mesh(reference, facet_indices=[1, 1]),
            r"api\.shape_from_mesh.*duplicate index",
        ),
        (
            lambda: api.shape_from_mesh(reference, output_type="wire", tolerance=0.1),
            r"api\.shape_from_mesh.*tolerance.*must be omitted",
        ),
    )
    for invoke, pattern in cases:
        with pytest.raises(ValueError, match=pattern):
            invoke()


def test_meshpart_worker_failures_always_give_the_model_one_exact_correction() -> None:
    from vibescript_meshpart_worker import MeshPartCandidateError

    selection = MeshPartCandidateError(
        "Segment is unavailable.",
        details={"stage": "mesh_selection", "available_segment_count": 6},
    )
    assert "reported 1-based segment" in selection.details["correction"]
    assert "never infer indices" in selection.details["correction"]

    solid = MeshPartCandidateError(
        "Mesh is open.",
        details={"stage": "solid_construction", "shell_count": 1},
    )
    assert "single connected, closed" in solid.details["correction"]
    assert "rather than weakening solid semantics" in solid.details["correction"]

    explicit = MeshPartCandidateError(
        "Netgen is unavailable.",
        details={
            "stage": "native_mesher_capability",
            "required_changes": ["Use method='standard'."],
        },
    )
    assert explicit.details["correction"] == "Use method='standard'."


def test_sketcher_api_is_explicit_complete_and_generated_from_runtime() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("SketcherWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    adapter = domains.get_domain_adapter(pack.domain)
    assert adapter is not None
    description = adapter.describe_api()

    assert description["api_contract"] == "vibecad-vibescript-sketcher-api-v1"
    assert tuple(api.exported_names) == pack.api_exports
    exports = description["runtime_exports"]
    assert [item["name"] for item in exports] == list(pack.api_exports)
    assert len(exports) == 12
    assert all(item["description"] for item in exports)
    assert all(
        "*args" not in item["signature"] and "**properties" not in item["signature"]
        for item in exports
    )
    assert len(json.dumps(description, separators=(",", ":"))) < 32 * 1024
    selection = description["operation_selection"]
    assert selection["any_geometric_dimensional_or_annotation_relation"].startswith(
        "api.constraint"
    )
    assert "one api.constraint operation" in selection["redundancy_contract"]
    assert "no model-facing rectangle" in selection["redundancy_contract"]
    assert description["constraint_forms"]["coincident"] == "[point, point]"
    assert "value required" in description["constraint_forms"]["angle_via_point"]
    assert description["model_verification_contract"]["underconstrained"].endswith(
        "Never apply every suggestion in one edit."
    )
    assert "active workbench determines" in description["workbench_handoffs"]["rule"]
    assert set(description["geometry"]) >= {
        "point",
        "line",
        "arc",
        "circle",
        "ellipse",
        "elliptic_arc",
        "hyperbolic_arc",
        "parabolic_arc",
        "bspline",
        "external_geometry",
        "construction",
    }
    external_contract = description["external_geometry_contract"]
    assert "x-vibecad-reference" in external_contract["input"]
    assert external_contract["regenerating_selection"]["schema"] == {
        "type": "published_interface",
        "interface_name": "DatumEdge",
    }
    assert "-3, -4" in external_contract["identity"]
    internal = description["constraints"]["internal_alignment"]
    assert set(internal["hyperbola"]) == {
        "hyperbola_major_diameter",
        "hyperbola_minor_diameter",
        "hyperbola_focus",
    }
    assert set(internal["parabola"]) == {
        "parabola_focus",
        "parabola_focal_axis",
    }
    rectangle_source = description["recommended_patterns"][0]["source"]
    domains.validate_program_source(rectangle_source)
    assert "constraints = [" in rectangle_source
    assert "# Add coincidence" not in rectangle_source
    external_source = description["recommended_patterns"][2]["source"]
    domains.validate_program_source(external_source)
    assert "api.external_geometry" in external_source


def test_sketcher_api_reports_exact_source_errors_before_native_execution() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("SketcherWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    line = api.line([0, 0], [5, 0])
    circle = api.circle([0, 0], 2)

    cases = (
        (lambda: api.line([0, 0], [0, 0]), r"api\.line.*end"),
        (
            lambda: api.elliptic_arc([0, 0], 2, 3, 0, 1),
            r"api\.elliptic_arc.*major_radius",
        ),
        (
            lambda: api.hyperbolic_arc([0, 0], 2, 1, 0, 21),
            r"api\.hyperbolic_arc.*start_parameter/end_parameter",
        ),
        (
            lambda: api.constraint("horizontal", [circle]),
            r"api\.constraint.*line geometry",
        ),
        (
            lambda: api.constraint("coincident", [line, circle]),
            r"api\.constraint.*explicit points",
        ),
        (
            lambda: api.constraint(
                "angle_via_point",
                [line, circle, circle],
                value=30,
            ),
            r"api\.constraint.*angle_via_point.*explicit point",
        ),
        (
            lambda: api.constraint("group", [circle]),
            r"api\.constraint.*entities\[0\]",
        ),
        (
            lambda: api.constraint(
                "radius",
                [circle],
                value=2,
                driving=False,
                expression="2 mm",
            ),
            r"api\.constraint.*expression.*reference",
        ),
        (
            lambda: api.external_geometry(
                {"document_uid": "doc", "object_name": "Source"},
                "Face1",
            ),
            r"api\.external_geometry.*selection\.subelements.*EdgeN or VertexN",
        ),
    )
    for invoke, pattern in cases:
        with pytest.raises(ValueError, match=pattern):
            invoke()

    control = api.circle([0, 0], 0.5, construction=True)
    spline = api.bspline(
        [[0, 0], [2, 3], [4, 2], [6, 0]],
        degree=3,
        knots=[0, 1],
        multiplicities=[4, 4],
    )
    with pytest.raises(ValueError, match=r"api\.constraint.*internal_index.*0-3"):
        api.constraint(
            "internal_alignment",
            [{"geometry": control, "point": "center"}, spline],
            alignment="bspline_control_point",
            internal_index=4,
        )
    other = api.line([0, 0], [1, 0])
    foreign_constraint = api.constraint("horizontal", [other])
    with pytest.raises(ValueError, match=r"api\.sketch.*not listed"):
        api.sketch([line], [foreign_constraint])


def test_sketcher_live_publication_boundary_never_solves_or_recomputes() -> None:
    import VibeCADVibeScriptDomainPublication as publication
    from vibescript_sketcher_worker import populate_sketch_without_solving

    configure_source = inspect.getsource(publication._configure_sketch)
    populate_source = inspect.getsource(populate_sketch_without_solving)
    for source in (configure_source, populate_source):
        assert ".solve(" not in source
        assert ".recompute(" not in source
        assert "subprocess" not in source
    assert "addConstraint(native_constraints)" in populate_source
    assert populate_source.count("addConstraint(") == 1


def test_generic_publication_accepts_non_assembly_and_cleans_failed_creations(
    monkeypatch,
) -> None:
    import VibeCADVibeScriptDomainPublication as publication

    class Object:
        next_id = 1

        def __init__(self, name: str, type_id: str):
            self.Name = name
            self.Label = name
            self.TypeId = type_id
            self.InList = []
            self.PropertiesList = []
            self.property_types = {}
            self.ID = Object.next_id
            Object.next_id += 1

        def addProperty(self, property_type, name, _group, _description):
            self.PropertiesList.append(str(name))
            self.property_types[str(name)] = str(property_type)

        def getTypeIdOfProperty(self, name):
            return self.property_types.get(str(name), "")

        def setPropertyStatus(self, _name, _status):
            pass

    class Document:
        Name = "PublicationDocument"
        Uid = "publication-document-uid"

        def __init__(self):
            self.objects = {}
            self.removed = []
            self.commits = 0
            self.aborts = 0

        def addObject(self, type_id, name):
            obj = Object(str(name), str(type_id))
            self.objects[obj.Name] = obj
            return obj

        def getObject(self, name):
            return self.objects.get(str(name))

        def removeObject(self, name):
            self.removed.append(str(name))
            self.objects.pop(str(name), None)

        def openTransaction(self, _label):
            pass

        def commitTransaction(self):
            self.commits += 1

        def abortTransaction(self):
            self.aborts += 1

        def isProvisionallyEnrolledInTimelineByCurrentTransaction(self, _obj):
            return False

    class Service:
        def __init__(self, document):
            self.document = document

        def _active_document(self):
            return self.document

        def provider_document_revision(self):
            return "document-revision"

    pack = type("Pack", (), {"domain": "mesh", "title": "Mesh"})()
    prepared = {
        "document_name": Document.Name,
        "document_uid": Document.Uid,
        "document_revision": "document-revision",
        "pack": pack,
        "program_id": "a" * 32,
        "program_name": "Mesh source",
        "revision": "b" * 64,
    }
    validated = {
        "outputs": [
            {
                "name": "Mesh",
                "type": "mesh",
                "definition": {
                    "operation": "mesh",
                    "properties": {"label": "Mesh output"},
                },
            }
        ],
        "stdout": "",
        "budget": {},
    }

    monkeypatch.setattr(publication, "_surface_still_matches", lambda *_args: None)
    monkeypatch.setattr(publication, "_objects_by_output", lambda *_args: {})
    monkeypatch.setattr(publication, "_retired_program_objects", lambda *_args: [])
    monkeypatch.setattr(publication, "_program_objects", lambda *_args: [])
    monkeypatch.setattr(publication, "_preflight_output_updates", lambda *_args: [])
    monkeypatch.setattr(
        publication, "_refresh_external_consumers", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(publication, "_set_metadata", lambda *_args: None)
    assembly_arguments = []

    def create_object(
        document,
        _prepared,
        _output_name,
        _output_type,
        _definition,
        _assembly,
        assembly_fastener_sources,
    ):
        assembly_arguments.append(assembly_fastener_sources)
        return document.addObject("Mesh::Feature", "CandidateMesh")

    monkeypatch.setattr(publication, "_create_object", create_object)
    monkeypatch.setattr(publication, "_configure_object", lambda *_args: None)

    successful_document = Document()
    result = publication.publish_candidate(
        Service(successful_document),
        prepared,
        validated,
    )

    assert result["ok"] is True
    assert result["created_objects"] == ["CandidateMesh"]
    assert successful_document.getObject("CandidateMesh") is not None
    assert successful_document.commits == 1
    assert assembly_arguments == [None]

    failed_document = Document()

    def fail_configuration(*_args):
        raise RuntimeError("injected non-Assembly publication failure")

    monkeypatch.setattr(publication, "_configure_object", fail_configuration)
    with pytest.raises(
        RuntimeError,
        match="injected non-Assembly publication failure",
    ):
        publication.publish_candidate(
            Service(failed_document),
            prepared,
            validated,
        )

    assert failed_document.aborts == 1
    assert failed_document.getObject("CandidateMesh") is None
    assert failed_document.removed == ["CandidateMesh"]
    assert assembly_arguments == [None, None]


def test_failed_publication_cleanup_is_dependency_safe_and_exact() -> None:
    import VibeCADVibeScriptDomainPublication as publication

    class Object:
        def __init__(self, name):
            self.Name = name
            self.InList = []

    class Document:
        def __init__(self):
            self.source = Object("CreatedSource")
            self.consumer = Object("CreatedConsumer")
            self.source.InList = [self.consumer]
            self.objects = {
                self.source.Name: self.source,
                self.consumer.Name: self.consumer,
                "AcceptedObject": Object("AcceptedObject"),
            }
            self.removed = []

        def getObject(self, name):
            return self.objects.get(str(name))

        def removeObject(self, name):
            self.removed.append(str(name))
            self.objects.pop(str(name), None)

    document = Document()
    removed = publication._remove_failed_domain_creations(
        document,
        [
            "CreatedSource",
            "AlreadyRemovedByTransaction",
            "CreatedConsumer",
            "CreatedSource",
        ],
    )

    assert removed == ["CreatedConsumer", "CreatedSource"]
    assert document.removed == ["CreatedConsumer", "CreatedSource"]
    assert document.getObject("AcceptedObject") is not None


def test_draft_api_is_canonical_model_guided_and_generated_from_runtime() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("DraftWorkbench")
    assert pack is not None and pack.production_ready
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    adapter = domains.get_domain_adapter(pack.domain)
    assert adapter is not None and adapter.production_ready
    description = adapter.describe_api()
    exports = description["runtime_exports"]

    assert description["api_contract"] == "vibecad-vibescript-draft-api-v1"
    assert tuple(api.exported_names) == pack.api_exports
    assert [item["name"] for item in exports] == list(pack.api_exports)
    assert len(exports) == 6
    assert all(item["description"] for item in exports)
    assert all(
        "*args" not in item["signature"] and "**properties" not in item["signature"]
        for item in exports
    )
    assert set(pack.api_exports) == {
        "wire",
        "circle",
        "rectangle",
        "bspline",
        "array",
        "text",
    }
    selection = description["operation_selection"]
    assert selection["full_circle_circular_arc_or_disc"].startswith("api.circle")
    assert selection["orthogonal_polar_or_concentric_ring_repetition"].startswith(
        "api.array"
    )
    assert "one api.array operation" in selection["redundancy_contract"]
    assert "no model-facing" in selection["redundancy_contract"].lower()
    assert "interval_axis" in description["array_contract"]["polar"]
    assert "kind='circular'" in description["array_contract"]["circular"]
    assert description["composition_contract"]["construction_order"][1].startswith(
        "Return every graph value"
    )
    assert (
        "next_write_expected_revision"
        in description["model_verification_contract"]["failure_repair"]
    )
    assert "active workbench determines" in description["workbench_handoffs"]["rule"]
    assert (
        len(
            json.dumps(description, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        < 32_000
    )
    for pattern in description["recommended_patterns"]:
        domains.validate_program_source(pattern["source"])


def test_draft_api_errors_identify_one_exact_model_repair_target() -> None:
    from vibescript_domain_api import create_domain_api

    pack = domains.get_vibescript_pack("DraftWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    with pytest.raises(ValueError, match=r"api\.rectangle.*fillet_radius") as failure:
        api.rectangle(4, 2, fillet_radius=1)
    details = failure.value.details
    assert details == {
        "stage": "source_validation",
        "operation": "rectangle",
        "parameter": "fillet_radius",
        "reason": "must be less than half the shorter side (1)",
        "correction": details["correction"],
    }
    assert "Change only the failing source expression" in details["correction"]


def test_draft_live_publication_boundary_never_executes_or_recomputes() -> None:
    import VibeCADVibeScriptDomainPublication as publication

    source = inspect.getsource(publication._configure_draft)
    for forbidden in (
        ".execute(",
        ".recompute(",
        "subprocess",
        "exportBrep(",
        "importBrep(",
    ):
        assert forbidden not in source
    assert "detached_shape" in source
    assert "PlacementList" in source


def test_surface_live_publication_boundary_never_constructs_or_recomputes() -> None:
    import VibeCADVibeScriptDomainPublication as publication

    source = inspect.getsource(publication._configure_surface)
    for forbidden in (
        ".execute(",
        ".recompute(",
        "subprocess",
        "exportBrep(",
        "importBrep(",
        "Part.",
        "makeLoft",
        "makeThickness",
        "makeOffsetShape",
    ):
        assert forbidden not in source
    assert "detached_shape" in source


def test_spreadsheet_live_publication_boundary_never_recomputes_or_waits() -> None:
    import VibeCADVibeScriptDomainPublication as publication
    from vibescript_spreadsheet_worker import (
        _populate_sheet_without_recomputing,
        populate_sheet_without_recomputing,
        restore_sheet_without_recomputing,
        sheet_readback,
    )

    sources = (
        inspect.getsource(publication._configure_sheet),
        inspect.getsource(_populate_sheet_without_recomputing),
        inspect.getsource(populate_sheet_without_recomputing),
        inspect.getsource(restore_sheet_without_recomputing),
        inspect.getsource(sheet_readback),
    )
    for source in sources:
        for forbidden in (
            ".recompute(",
            "subprocess",
            ".wait(",
            "exportBrep(",
            "importBrep(",
            "read_text(",
            "write_text(",
            "Part.",
            "Mesh.",
        ):
            assert forbidden not in source
    configure_source = sources[0]
    assert "populate_sheet_without_recomputing" in configure_source
    assert "readback_sha256" in configure_source
    assert "transaction was aborted" in configure_source


def test_material_document_thread_boundary_never_opens_catalog_or_recomputes() -> None:
    import VibeCADVibeScriptDomainPublication as publication
    import VibeCADVibeScriptDomainRuntime as runtime

    document_thread_sources = (
        inspect.getsource(publication._publish_material_candidate),
        inspect.getsource(publication._configure_material_carrier),
        inspect.getsource(publication._delete_material_program),
        inspect.getsource(publication._set_physical_material_preserving_view),
    )
    for source in document_thread_sources:
        for forbidden in (
            "MaterialManager",
            ".recompute(",
            "subprocess",
            ".wait(",
            "read_text(",
            "write_text(",
            "exportBrep(",
            "importBrep(",
            "Part.",
            "Mesh.",
        ):
            assert forbidden not in source
    validation_source = inspect.getsource(runtime._validate_material_execution)
    assert "MaterialManager" in validation_source
    assert "MATERIAL_CATALOG_LOCK" in validation_source
    assert "native_material" in validation_source


def test_mesh_document_thread_boundary_only_assigns_detached_native_state() -> None:
    import VibeCADVibeScriptDomainPublication as publication
    import VibeCADVibeScriptDomainRuntime as runtime

    document_thread_sources = (
        inspect.getsource(publication._configure_mesh),
        inspect.getsource(publication._mesh_rollback_states),
        inspect.getsource(publication._restore_mesh_rollback_states),
        inspect.getsource(domains._mesh_document_snapshot),
    )
    for source in document_thread_sources:
        for forbidden in (
            ".write(",
            ".read(",
            "read_bytes(",
            "write_bytes(",
            "subprocess",
            ".wait(",
            ".recompute(",
            "hasSelfIntersections(",
            "getSelfIntersections(",
            "fixSelfIntersections(",
            "fillupHoles(",
            "decimate(",
        ):
            assert forbidden not in source
    configure_source = document_thread_sources[0]
    assert "detached_mesh" in configure_source
    assert "obj.Mesh = detached" in configure_source
    validation_source = inspect.getsource(runtime._validate_mesh_execution)
    assert "mesh_diagnostics(detached)" in validation_source
    assert "artifact_sha256" in validation_source


def test_rollback_property_digest_ignores_zip_timestamp_not_content() -> None:
    from io import BytesIO
    import zipfile

    import VibeCADVibeScriptDomainPublication as publication

    def persisted(timestamp: tuple[int, int, int, int, int, int], text: str) -> bytes:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            info = zipfile.ZipInfo("Persistence.xml", timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, text.encode("utf-8"))
        return buffer.getvalue()

    first = persisted((2024, 1, 1, 0, 0, 0), "<Property value='3 mm'/>")
    later = persisted((2026, 7, 19, 12, 30, 0), "<Property value='3 mm'/>")
    changed = persisted((2026, 7, 19, 12, 30, 0), "<Property value='4 mm'/>")
    assert publication._property_content_sha256(first) == (
        publication._property_content_sha256(later)
    )
    assert publication._property_content_sha256(first) != (
        publication._property_content_sha256(changed)
    )


def test_cam_document_thread_boundary_only_applies_validated_native_state() -> None:
    import VibeCADVibeScriptDomainPublication as publication

    document_thread_sources = (
        inspect.getsource(publication._publish_cam_candidate),
        inspect.getsource(publication._restore_cam_rollback_states),
        inspect.getsource(publication._delete_cam_program),
    )
    for source in document_thread_sources:
        for forbidden in (
            "validate_and_build_cam(",
            "analyze_operation(",
            "PostProcessorFactory",
            "PathSimulator",
            "subprocess",
            ".wait(",
            ".recompute(",
            "read_bytes(",
            "read_text(",
            "write_bytes(",
            "write_text(",
            "exportBrep(",
            "importBrep(",
            ".makePipeShell(",
            ".makeOffset2D(",
            ".fuse(",
            ".cut(",
            ".solve(",
        ):
            assert forbidden not in source
    publication_source = document_thread_sources[0]
    assert "detached_shape" in publication_source
    assert "detached_path" in publication_source
    assert 'Path = item["detached_path"]' in publication_source


def test_techdraw_document_thread_only_installs_precomputed_native_state() -> None:
    import VibeCADVibeScriptDomainPublication as publication
    import VibeCADVibeScriptDomains as domains

    document_thread_sources = (
        inspect.getsource(publication._publish_techdraw_candidate),
        inspect.getsource(publication._restore_techdraw_rollback_states),
        inspect.getsource(publication._remove_techdraw_objects),
        inspect.getsource(publication._delete_techdraw_program),
    )
    for source in document_thread_sources:
        for forbidden in (
            "validate_and_build_techdraw(",
            "addProjection(",
            ".recompute(",
            "recomputeFeature(",
            "getProjectedElementDescriptors(",
            "getRawValue(",
            "getText(",
            "subprocess",
            ".wait(",
            "read_bytes(",
            "read_text(",
            "write_bytes(",
            "write_text(",
            "exportBrep(",
            "importBrep(",
        ):
            assert forbidden not in source
    publication_source = document_thread_sources[0]
    assert "addPrecomputedProjection" in publication_source
    assert "addPrecomputedView" in publication_source
    assert "setPrecomputedProjection" in publication_source
    assert "setPrecomputedDimension" in publication_source
    assert ".addView(" not in publication_source

    context_source = inspect.getsource(domains._techdraw_document_snapshot)
    for forbidden in (
        "getPrecomputedProjection(",
        "getPrecomputedDimension(",
        "getRawValue(",
        "getText(",
        ".recompute(",
        "read_bytes(",
        "read_text(",
        "write_bytes(",
        "write_text(",
    ):
        assert forbidden not in context_source


def test_reference_revision_binds_assembly_semantic_connector_contract() -> None:
    base = {
        "document_uid": "document",
        "object_name": "Arm",
        "brep_sha256": "a" * 64,
    }
    geometry_only = domains.program_revision_with_references(
        contract_revision="b" * 64,
        references=[base],
    )
    first_contract = domains.program_revision_with_references(
        contract_revision="b" * 64,
        references=[{**base, "reference_contract_sha256": "c" * 64}],
    )
    second_contract = domains.program_revision_with_references(
        contract_revision="b" * 64,
        references=[{**base, "reference_contract_sha256": "d" * 64}],
    )

    assert len({geometry_only, first_contract, second_contract}) == 3
    with pytest.raises(ValueError, match="reference contracts require a SHA-256"):
        domains.program_revision_with_references(
            contract_revision="b" * 64,
            references=[{**base, "reference_contract_sha256": "not-a-digest"}],
        )


def test_reference_revision_accepts_exact_linked_component_identity() -> None:
    base = {
        "document_uid": "document",
        "object_name": "ImportedMotor",
        "artifact_kind": "component_identity",
        "type_id": "PartDesign::Body",
    }
    first = domains.program_revision_with_references(
        contract_revision="a" * 64,
        references=[base],
    )
    second = domains.program_revision_with_references(
        contract_revision="a" * 64,
        references=[{**base, "type_id": "Part::Feature"}],
    )

    assert first != second
    with pytest.raises(ValueError, match="component identities require"):
        domains.program_revision_with_references(
            contract_revision="a" * 64,
            references=[{**base, "type_id": ""}],
        )


def test_domain_api_graph_and_worker_inputs_are_deeply_immutable() -> None:
    from vibescript_domain_api import create_domain_api
    from vibescript_domain_worker import _execute_source, _immutable_input

    pack = domains.get_vibescript_pack("PartWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    value = api.box(2, 3, 4, origin=[1, 2, 3])

    with pytest.raises(TypeError):
        value.properties["origin"] = (9, 9, 9)
    with pytest.raises(TypeError):
        value.properties["origin"][0] = 9
    with pytest.raises((AttributeError, TypeError)):
        api.box = None

    inputs = _immutable_input(
        {
            "dimensions": [2, 3, 4],
            "source": {"document_uid": "doc", "object_name": "Body"},
        }
    )
    with pytest.raises(TypeError):
        inputs["dimensions"] = (1, 1, 1)
    with pytest.raises(TypeError):
        inputs["source"]["object_name"] = "Other"

    with pytest.raises(TypeError, match="does not support item assignment"):
        _execute_source(
            source="inputs['dimensions'][0] = 99\nresult = {}",
            document_name="ImmutableFixture",
            document_objects=[],
            inputs={"dimensions": [2, 3, 4]},
            api=api,
            expected_output_names=[],
            max_operations=1_000,
            max_seconds=1.0,
        )
    with pytest.raises(TypeError, match="does not support item assignment"):
        _execute_source(
            source=(
                "value = api.box(1, 2, 3, origin=[0, 0, 0])\n"
                "value.properties['origin'][0] = 99\n"
                "result = {'Body': value}"
            ),
            document_name="ImmutableFixture",
            document_objects=[],
            inputs={},
            api=api,
            expected_output_names=["Body"],
            max_operations=1_000,
            max_seconds=1.0,
        )


def test_source_operation_budget_excludes_trusted_domain_api_frames() -> None:
    from vibescript_domain_worker import _execute_source

    class TrustedAPI:
        @staticmethod
        def build() -> int:
            total = 0
            for value in range(100_000):
                total += value % 7
            return total

    result, _stdout, budget = _execute_source(
        source="value = api.build()\nresult = {'Value': value}\n",
        document_name="BudgetFixture",
        document_objects=[],
        inputs={},
        api=TrustedAPI(),
        expected_output_names=["Value"],
        max_operations=10,
        max_seconds=1.0,
    )
    assert result["Value"] > 0
    assert 1 <= budget["operations"] <= 10

    with pytest.raises(RuntimeError, match=r"exceeded its 10 operation budget"):
        _execute_source(
            source=(
                "value = 0\n"
                "for item in range(100):\n"
                "    value += item\n"
                "result = {'Value': value}\n"
            ),
            document_name="BudgetFixture",
            document_objects=[],
            inputs={},
            api=TrustedAPI(),
            expected_output_names=["Value"],
            max_operations=10,
            max_seconds=1.0,
        )


def test_domain_context_merges_live_identity_without_losing_persisted_facts(
    tmp_path: Path,
) -> None:
    program_id = "a" * 32
    directory = tmp_path / "vibescript" / "part" / program_id
    directory.mkdir(parents=True)
    (directory / "program.json").write_text(
        json.dumps(
            {
                "schema": domains.PROGRAM_SCHEMA,
                "version": domains.PROGRAM_VERSION,
                "program_id": program_id,
                "domain": "part",
                "workbench": "PartWorkbench",
                "label": "Context fixture",
                "source": "result = {}",
                "input_schema": {},
                "inputs": {},
                "expected_outputs": [{"name": "Body", "type": "solid"}],
                "working_revision": "b" * 64,
                "accepted_revision": "b" * 64,
                "live_outputs": {
                    "Body": {
                        "object_name": "OldBody",
                        "label": "Old label",
                        "type_id": "Part::Feature",
                        "output_type": "solid",
                        "facts": {"shape_type": "Solid", "volume_mm3": 24.0},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    context = domains.complete_domain_context(
        {
            "_vibecad_deferred_vibescript_domain_context": True,
            "domain": "part",
            "workbench": "PartWorkbench",
            "surface_id": "vibescript:part:v2",
            "project_root": str(tmp_path),
            "contract": {},
            "native_programs": [
                {
                    "program_id": program_id,
                    "domain": "part",
                    "workbench": "PartWorkbench",
                    "working_revision": "b" * 64,
                    "live_outputs": [
                        {
                            "name": "Body",
                            "object_name": "LiveBody",
                            "label": "Live label",
                            "type_id": "Part::Feature",
                        }
                    ],
                }
            ],
        }
    )
    output = context["programs"][0]["live_outputs"]["Body"]
    assert output["object_name"] == "LiveBody"
    assert output["label"] == "Live label"
    assert output["output_type"] == "solid"
    assert output["facts"] == {"shape_type": "Solid", "volume_mm3": 24.0}


def test_domain_context_is_aggregate_bounded_and_points_to_exact_inspection(
    tmp_path: Path,
) -> None:
    target_program_id = f"{1:032x}"
    root = tmp_path / "vibescript" / "part"
    for index in range(35):
        program_id = f"{index + 1:032x}"
        directory = root / program_id
        directory.mkdir(parents=True)
        manifest = {
            "schema": domains.PROGRAM_SCHEMA,
            "version": domains.PROGRAM_VERSION,
            "program_id": program_id,
            "domain": "part",
            "workbench": "PartWorkbench",
            "label": f"Program {index + 1}",
            "source": "result = {}",
            "input_schema": {},
            "inputs": {},
            "expected_outputs": [{"name": "Body", "type": "solid"}],
            "working_revision": "b" * 64,
            "accepted_revision": "b" * 64,
            "live_outputs": {},
        }
        if program_id == target_program_id:
            manifest["inputs"] = {"values": ["x" * 1_000 for _ in range(20)]}
            manifest["resolved_references"] = [
                {
                    "document_uid": "document",
                    "object_name": f"Source{reference_index}",
                    "facts": {
                        "shape_type": "Solid",
                        "face_details": [{"index": 1}],
                        "edge_details": [{"index": 1}],
                    },
                }
                for reference_index in range(20)
            ]
            manifest["live_outputs"] = {
                "Body": {
                    "object_name": "Body",
                    "output_type": "solid",
                    "facts": {
                        "shape_type": "Solid",
                        "faces": 6,
                        "edges": 12,
                        "face_details": [{"index": 1}],
                        "edge_details": [{"index": 1}],
                    },
                }
            }
        (directory / "program.json").write_text(json.dumps(manifest), encoding="utf-8")

    context = domains.complete_domain_context(
        {
            "_vibecad_deferred_vibescript_domain_context": True,
            "domain": "part",
            "workbench": "PartWorkbench",
            "surface_id": "vibescript:part:v2",
            "project_root": str(tmp_path),
            "contract": {},
            "native_program_count": 1,
            "native_programs": [
                {
                    "program_id": target_program_id,
                    "domain": "part",
                    "workbench": "PartWorkbench",
                    "live_outputs": [],
                }
            ],
        }
    )
    assert context["program_limit"] == domains.MAX_DOMAIN_CONTEXT_PROGRAMS == 32
    assert context["program_count"] == 35
    assert len(context["programs"]) == 32
    assert context["programs_truncated"] is True
    assert context["programs_omitted"] == 3
    target = next(
        item for item in context["programs"] if item["program_id"] == target_program_id
    )
    assert target["inputs"]["_vibecad_context_omitted"] is True
    assert len(target["resolved_references"]) == 16
    assert target["resolved_references_omitted"] == 4
    output_facts = target["live_outputs"]["Body"]["facts"]
    assert "face_details" not in output_facts
    assert "edge_details" not in output_facts
    assert output_facts["subelement_details_context_omitted"] is True
    assert "vibescript.read_source" in output_facts["subelement_details_guidance"]


def test_nonproduction_packs_cannot_surface_unfinished_domains() -> None:
    for workbench, pack in domains.VIBESCRIPT_WORKBENCH_PACKS.items():
        if pack.production_ready:
            continue
        adapter = domains.get_domain_adapter(pack.domain)
        assert adapter is not None
        available, reason = domains.domain_availability(workbench)
        assert available is False
        assert "production-readiness gate" in reason
        surface = resolve_modeling_surface(workbench, "vibescript")
        assert surface.cad_tool_names == ()
        assert surface.tool_names == surface.core_tool_names


def test_nested_stable_inputs_are_reauthorized_against_the_live_document() -> None:
    from VibeCADVibeScriptDomainRuntime import _validate_stable_references

    captured = {
        "document_uid": "live-document",
        "document_objects": [{"name": "Body"}],
    }
    _validate_stable_references(
        {
            "source": {
                "document_uid": "live-document",
                "object_name": "Body",
            }
        },
        captured,
        "inputs",
    )
    with pytest.raises(ValueError, match="different document uid"):
        _validate_stable_references(
            {
                "source": {
                    "document_uid": "stale-document",
                    "object_name": "Body",
                }
            },
            captured,
            "inputs",
        )


def test_reusable_component_domains_defer_external_reference_authentication() -> None:
    from VibeCADVibeScriptDomainRuntime import _validate_stable_references

    reference = {
        "source": {
            "document_uid": "source-document",
            "object_name": "MotorBody",
            "document_path": "components/motor.FCStd",
        }
    }
    for domain in ("partdesign", "assembly", "robot"):
        captured = {
            "pack": type("Pack", (), {"domain": domain})(),
            "document_uid": "design-document",
            "document_objects": [{"name": "LocalBody"}],
        }
        _validate_stable_references(reference, captured, "inputs")

    captured = {
        "pack": type("Pack", (), {"domain": "partdesign"})(),
        "document_uid": "design-document",
        "document_objects": [{"name": "LocalBody"}],
    }
    with pytest.raises(ValueError, match="missing object 'MissingBody'"):
        _validate_stable_references(
            {
                "source": {
                    "document_uid": "design-document",
                    "object_name": "MissingBody",
                }
            },
            captured,
            "inputs",
        )


def test_domain_publication_has_no_worker_or_artifact_io_fallback() -> None:
    import VibeCADVibeScriptDomainPublication as publication

    source = inspect.getsource(publication)
    for forbidden in (
        "subprocess.",
        "run_process(",
        ".wait(",
        "read_text(",
        "write_text(",
        "importBrep(",
        "exportBrep(",
        ".recompute(",
        ".solve(",
    ):
        assert forbidden not in source


def test_part_reference_capture_only_detaches_live_shapes() -> None:
    import VibeCADVibeScriptDomainRuntime as runtime

    source = inspect.getsource(runtime.capture_reference_inputs)
    for forbidden in (
        "exportBrep(",
        "importBrep(",
        "part_shape_facts(",
        "read_text(",
        "write_text(",
        "subprocess.",
        ".wait(",
    ):
        assert forbidden not in source
    assert ".copy()" in source


@pytest.mark.parametrize(
    ("domain", "domain_files"),
    (
        ("part", {"vibescript_part_api.py", "vibescript_part_worker.py"}),
        (
            "assembly",
            {
                "VibeCADAssemblyBOM.py",
                "VibeCADDocumentReferences.py",
                "VibeCADFasteners.py",
                "VibeCADMechanismEngine.py",
                "VibeCADMechanismGeometry.py",
                "fasteners-provenance.json",
                "vibescript_assembly_api.py",
                "vibescript_assembly_worker.py",
                "vibescript_component_api.py",
                "vibescript_part_worker.py",
            },
        ),
        (
            "sketcher",
            {
                "vibescript_sketcher_api.py",
                "vibescript_sketcher_worker.py",
                "vibescript_part_worker.py",
            },
        ),
        (
            "draft",
            {
                "vibescript_draft_api.py",
                "vibescript_draft_worker.py",
                "vibescript_part_worker.py",
            },
        ),
        (
            "surface",
            {
                "vibescript_surface_api.py",
                "vibescript_surface_worker.py",
                "vibescript_part_worker.py",
            },
        ),
        (
            "spreadsheet",
            {
                "vibescript_spreadsheet_api.py",
                "vibescript_spreadsheet_worker.py",
            },
        ),
        (
            "material",
            {
                "vibescript_material_api.py",
                "vibescript_material_worker.py",
            },
        ),
        (
            "mesh",
            {
                "vibescript_mesh_api.py",
                "vibescript_mesh_worker.py",
                "vibescript_meshpart_api.py",
                "vibescript_meshpart_worker.py",
                "vibescript_part_worker.py",
            },
        ),
        (
            "meshpart",
            {
                "vibescript_meshpart_api.py",
                "vibescript_meshpart_worker.py",
                "vibescript_mesh_worker.py",
                "vibescript_part_worker.py",
            },
        ),
        ("points", {"vibescript_points_api.py", "vibescript_points_worker.py"}),
        (
            "reverse_engineering",
            {
                "vibescript_reverse_engineering_api.py",
                "vibescript_reverse_engineering_worker.py",
                "vibescript_points_api.py",
                "vibescript_points_worker.py",
                "vibescript_meshpart_api.py",
                "vibescript_meshpart_worker.py",
                "vibescript_mesh_worker.py",
                "vibescript_part_worker.py",
            },
        ),
        (
            "inspection",
            {
                "vibescript_inspection_api.py",
                "vibescript_inspection_worker.py",
                "vibescript_points_worker.py",
            },
        ),
        (
            "robot",
            {
                "vibescript_component_api.py",
                "vibescript_component_worker.py",
                "vibescript_robot_api.py",
                "vibescript_robot_worker.py",
            },
        ),
        ("fem", {"vibescript_fem_api.py", "vibescript_fem_worker.py"}),
        (
            "cam",
            {
                "vibescript_cam_api.py",
                "vibescript_cam_worker.py",
                "vibescript_part_worker.py",
            },
        ),
        (
            "techdraw",
            {
                "vibescript_techdraw_api.py",
                "vibescript_techdraw_worker.py",
                "vibescript_part_worker.py",
            },
        ),
    ),
)
def test_worker_staging_contains_only_the_active_domain_bundle(
    tmp_path: Path,
    domain: str,
    domain_files: set[str],
) -> None:
    import VibeCADVibeScriptDomainRuntime as runtime

    staging = tmp_path / domain
    staging.mkdir()
    copied = runtime._stage_worker_bundle(
        Path(runtime.__file__).resolve().parent,
        staging,
        domain,
    )
    expected = {
        "worker.py",
        "vibescript_domain_api.py",
        "vibescript_worker_progress.py",
        *domain_files,
    }
    assert set(copied) == expected
    assert {path.name for path in staging.iterdir()} == expected
    assert not any(
        path.name.startswith("vibescript_")
        and path.name.endswith(("_api.py", "_worker.py"))
        and path.name not in expected
        for path in staging.iterdir()
    )


def test_every_isolated_worker_dependency_is_packaged() -> None:
    import VibeCADVibeScriptDomainRuntime as runtime

    module_root = Path(runtime.__file__).resolve().parent
    cmake = (module_root / "CMakeLists.txt").read_text(encoding="utf-8")
    required = {
        "vibescript_domain_api.py",
        "vibescript_domain_worker.py",
        "vibescript_worker_progress.py",
    }
    for domain_files in runtime._DOMAIN_WORKER_BUNDLES.values():
        required.update(domain_files)

    for filename in sorted(required):
        assert (module_root / filename).is_file(), filename
        assert f"    {filename}\n" in cmake, filename


def test_runtime_object_deletion_helper_is_packaged() -> None:
    import VibeCADObjectDeletion as object_deletion

    module_root = Path(object_deletion.__file__).resolve().parent
    cmake = (module_root / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "    VibeCADObjectDeletion.py\n" in cmake


def test_assembly_reference_facts_skip_expensive_shape_inspection() -> None:
    from vibescript_part_worker import part_shape_reference_facts

    class Bounds:
        XMin = -1.0
        YMin = -2.0
        ZMin = -3.0
        XMax = 4.0
        YMax = 5.0
        ZMax = 6.0
        XLength = 5.0
        YLength = 7.0
        ZLength = 9.0

    class Shape:
        ShapeType = "Solid"
        BoundBox = Bounds()

        @staticmethod
        def countElement(element: str) -> int:
            return {
                "Solid": 1,
                "Shell": 1,
                "Face": 7356,
                "Wire": 7356,
                "Edge": 17805,
                "Vertex": 35610,
            }[element]

        @staticmethod
        def isNull() -> bool:
            return False

        @staticmethod
        def isValid() -> bool:
            raise AssertionError("The isolated Assembly worker owns BREP validation.")

        def __getattr__(self, name: str):
            if name in {
                "Area",
                "CenterOfMass",
                "Edges",
                "Faces",
                "Length",
                "Volume",
            }:
                raise AssertionError(f"Assembly reference facts must not read {name}.")
            raise AttributeError(name)

    facts = part_shape_reference_facts(Shape(), assume_valid=True)
    assert facts == {
        "shape_type": "Solid",
        "valid": True,
        "null": False,
        "solids": 1,
        "shells": 1,
        "faces": 7356,
        "wires": 7356,
        "edges": 17805,
        "vertices": 35610,
        "bounds_center_mm": [1.5, 1.5, 1.5],
        "bounds_mm": {
            "min": [-1.0, -2.0, -3.0],
            "max": [4.0, 5.0, 6.0],
            "size": [5.0, 7.0, 9.0],
        },
        "subelement_detail_limit": 0,
        "subelement_details_truncated": True,
    }


def test_worker_staging_rejects_an_undeclared_domain(tmp_path: Path) -> None:
    import VibeCADVibeScriptDomainRuntime as runtime

    with pytest.raises(ValueError, match="no isolated worker bundle"):
        runtime._stage_worker_bundle(
            Path(runtime.__file__).resolve().parent,
            tmp_path,
            "not-a-domain",
        )


def test_points_api_collapses_ingest_and_processing_into_one_exact_operation() -> None:
    from vibescript_domain_api import create_domain_api
    from vibescript_points_worker import validate_points_definition

    pack = domains.get_vibescript_pack("PointsWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    adapter = domains.get_domain_adapter(pack.domain)
    assert adapter is not None
    description = adapter.describe_api()

    assert tuple(api.exported_names) == ("point_cloud",)
    assert [item["name"] for item in description["runtime_exports"]] == ["point_cloud"]
    for redundant in ("load_artifact", "transform", "filter", "downsample", "points"):
        assert not hasattr(api, redundant)
    value = api.point_cloud(
        [[0, 0, 0], [1, 1, 1]],
        pipeline=[
            {"op": "filter", "method": "deduplicate", "tolerance": 0.01},
            {"op": "sample", "method": "stride", "step": 2},
        ],
        invalid_points="drop",
        preserve_attributes=False,
    )
    assert validate_points_definition(value) == value.to_payload()
    assert value.properties["pipeline"][0]["method"] == "deduplicate"
    with pytest.raises(ValueError, match="raw|source"):
        api.point_cloud("/tmp/not-an-approved-input.xyz")
    with pytest.raises(ValueError, match="identity transform"):
        api.point_cloud([[0, 0, 0]], pipeline=[{"op": "transform"}])
    with pytest.raises(ValueError, match="unused by sample method"):
        api.point_cloud(
            [[0, 0, 0]],
            pipeline=[
                {
                    "op": "sample",
                    "method": "limit",
                    "max_points": 1,
                    "step": 2,
                }
            ],
        )


def test_reverse_engineering_api_collapses_redundant_algorithm_tools() -> None:
    from vibescript_domain_api import create_domain_api
    from vibescript_reverse_engineering_worker import validate_reverse_definition

    pack = domains.get_vibescript_pack("ReverseEngineeringWorkbench")
    assert pack is not None
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    adapter = domains.get_domain_adapter(pack.domain)
    assert adapter is not None
    description = adapter.describe_api()

    assert tuple(api.exported_names) == (
        "fit_curve",
        "fit_surface",
        "reconstruct",
        "segment",
        "fit_metrics",
    )
    assert [item["name"] for item in description["runtime_exports"]] == list(
        api.exported_names
    )
    for redundant in (
        "approximate_curve",
        "approximate_surface",
        "triangulate",
        "output",
    ):
        assert not hasattr(api, redundant)
    curve = api.fit_curve(
        [[0, 0, 0], [1, 0.2, 0], [2, 0, 0]],
        min_degree=2,
        max_degree=4,
        continuity="c1",
    )
    metrics = api.fit_metrics(curve, tolerance=0.05)
    reconstruction = api.reconstruct(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]],
        method="structured_grid",
        parameters={"grid_size": [2, 2], "diagonal": "shortest"},
    )
    referenced_segments = api.segment(
        {"document_uid": "document", "object_name": "Mesh"},
        method="connected_components",
    )
    assert validate_reverse_definition(curve) == curve.to_payload()
    assert validate_reverse_definition(metrics) == metrics.to_payload()
    assert validate_reverse_definition(reconstruction) == reconstruction.to_payload()
    assert (
        validate_reverse_definition(referenced_segments)
        == referenced_segments.to_payload()
    )
    with pytest.raises(ValueError, match="fields unused"):
        api.reconstruct(
            [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]],
            method="structured_grid",
            parameters={"grid_size": [2, 2], "search_radius": 1.0},
        )
    with pytest.raises(ValueError, match="stable point/mesh reference"):
        api.fit_curve("/tmp/raw-scan.xyz")


def test_reverse_fit_metric_comparison_uses_only_occ_resolution_tolerance() -> None:
    from VibeCADVibeScriptDomainRuntime import _mesh_values_match

    metric_path = "outputs.Surface.fit_metrics.minimum_distance"
    _mesh_values_match(5.0e-8, 0.0, path=metric_path)
    with pytest.raises(ValueError, match="differs"):
        _mesh_values_match(2.0e-7, 0.0, path=metric_path)
    with pytest.raises(ValueError, match="differs"):
        _mesh_values_match(5.0e-8, 0.0, path="outputs.Mesh.bounds.minimum")


def test_inspection_api_has_one_canonical_distance_operation() -> None:
    from vibescript_domain_api import create_domain_api
    from vibescript_inspection_worker import validate_inspection_definition

    pack = domains.get_vibescript_pack("InspectionWorkbench")
    assert pack is not None and pack.production_ready
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    adapter = domains.get_domain_adapter(pack.domain)
    assert adapter is not None
    description = adapter.describe_api()

    assert tuple(api.exported_names) == (
        "comparison",
        "group",
        "measurement",
        "report",
    )
    assert [item["name"] for item in description["runtime_exports"]] == list(
        api.exported_names
    )
    assert description["api_contract"] == "vibecad-vibescript-inspection-api-v1"
    for redundant in ("inspection", "compare", "tolerance", "output"):
        assert not hasattr(api, redundant)
    actual = {"document_uid": "document", "object_name": "Actual"}
    nominal = {"document_uid": "document", "object_name": "Nominal"}
    comparison = api.comparison(
        actual,
        [nominal],
        search_radius=1.0,
        tolerance=[-0.1, 0.2],
    )
    group = api.group([comparison])
    measurement = api.measurement(comparison, metric="rms")
    report = api.report(group)
    assert [
        validate_inspection_definition(value)["operation"]
        for value in (comparison, group, measurement, report)
    ] == list(api.exported_names)
    with pytest.raises(ValueError, match="inside search_radius"):
        api.comparison(
            actual,
            [nominal],
            search_radius=0.1,
            tolerance=0.2,
        )
    with pytest.raises(ValueError, match="duplicate definitions"):
        api.group([comparison, comparison])


def test_point_artifact_input_schema_is_explicit_and_bounded() -> None:
    schema = {
        "type": "object",
        "properties": {
            "source": {
                "oneOf": [
                    {
                        "type": "object",
                        "x-vibecad-reference": True,
                        "properties": {
                            "document_uid": {"type": "string"},
                            "object_name": {"type": "string"},
                        },
                        "required": ["document_uid", "object_name"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "x-vibecad-point-artifact": True,
                        "properties": {
                            "artifact_id": {
                                "type": "string",
                                "pattern": "^[0-9a-f]{32}$",
                            }
                        },
                        "required": ["artifact_id"],
                        "additionalProperties": False,
                    },
                ]
            }
        },
        "required": ["source"],
        "additionalProperties": False,
    }
    assert domains.validate_input_schema(schema) == schema
    assert domains.validate_inputs({"source": {"artifact_id": "a" * 32}})
    malformed = json.loads(json.dumps(schema))
    malformed["properties"]["source"]["oneOf"][1]["properties"]["artifact_id"][
        "pattern"
    ] = ".*"
    with pytest.raises(ValueError, match="exact bounded"):
        domains.validate_input_schema(malformed)
    with pytest.raises(ValueError, match="invalid stable artifact"):
        domains.validate_inputs({"source": {"artifact_id": "not-an-id"}})
    with pytest.raises(ValueError, match="only one bounded oneOf"):
        domains.validate_input_schema(
            {
                **schema,
                "anyOf": [{"type": "string"}],
            }
        )


def test_point_artifact_registry_authenticates_guards_and_rolls_back(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import VibeCADPointArtifacts as artifacts

    source = tmp_path / "source.xyz"
    source.write_text("0 0 0\n1 2 3\n", encoding="utf-8")
    approved = artifacts.approve_point_artifact(tmp_path, source, label="Scan")
    summary = artifacts.point_artifacts_summary(tmp_path)
    assert summary["artifact_count"] == 1
    assert "path" not in summary["artifacts"][0]
    resolved = artifacts.resolve_point_artifacts(tmp_path, [approved["artifact_id"]])[0]
    assert Path(resolved["path"]).is_file()

    program_id = "b" * 32
    program = tmp_path / "vibescript" / "points" / program_id
    program.mkdir(parents=True)
    (program / "program.json").write_text(
        json.dumps(
            {
                "program_id": program_id,
                "label": "Uses scan",
                "inputs": {"source": {"artifact_id": approved["artifact_id"]}},
                "working_revision": "c" * 64,
                "accepted_revision": "c" * 64,
                "accepted_contract": {
                    "inputs": {"source": {"artifact_id": approved["artifact_id"]}}
                },
            }
        ),
        encoding="utf-8",
    )
    references = artifacts.point_artifact_program_references(
        tmp_path, approved["artifact_id"]
    )
    assert references[0]["accepted_reference"] is True
    with pytest.raises(ValueError, match="programs reference it"):
        artifacts.remove_point_artifact(tmp_path, approved["artifact_id"])

    (program / "program.json").write_text(
        json.dumps(
            {
                "program_id": program_id,
                "inputs": {},
                "accepted_contract": None,
            }
        ),
        encoding="utf-8",
    )
    original_write = artifacts._write_manifest
    calls = []

    def fail_first_write(project_root, values):
        calls.append(True)
        if len(calls) == 1:
            raise OSError("injected manifest failure")
        return original_write(project_root, values)

    monkeypatch.setattr(artifacts, "_write_manifest", fail_first_write)
    with pytest.raises(OSError, match="injected manifest failure"):
        artifacts.remove_point_artifact(tmp_path, approved["artifact_id"])
    assert artifacts.resolve_point_artifacts(tmp_path, [approved["artifact_id"]])
    monkeypatch.setattr(artifacts, "_write_manifest", original_write)
    removed = artifacts.remove_point_artifact(tmp_path, approved["artifact_id"])
    assert removed["artifact_copy_deleted"] is True
    assert artifacts.point_artifacts_summary(tmp_path)["artifact_count"] == 0


def test_gui_document_observer_marks_vibescript_dependencies_stale(monkeypatch) -> None:
    import VibeCADGui as gui
    import VibeCADVibeScriptDomainPublication as publication

    observed = []
    refreshed = []
    source = object()

    def mark(obj, property_name):
        observed.append((obj, property_name))
        return ["DependentOutput"]

    monkeypatch.setattr(publication, "mark_programs_stale_from_source", mark)
    monkeypatch.setattr(
        gui,
        "_schedule_assistant_document_refresh",
        lambda: refreshed.append(True),
    )
    gui._VibeCADDocumentObserver().slotChangedObject(source, "Shape")
    assert observed == [(source, "Shape")]
    assert refreshed == [True]


@pytest.mark.parametrize(
    "property_name",
    ["_GroupTouched", "_LinkTouched", "ShowInTree", "Visibility"],
)
def test_dependency_invalidation_ignores_presentation_notifications(
    property_name: str,
) -> None:
    import VibeCADVibeScriptDomainPublication as publication

    class Source:
        @property
        def InList(self):
            raise AssertionError(
                "A derived group recompute must not inspect dependents"
            )

    assert publication.mark_programs_stale_from_source(Source(), property_name) == []


def test_gui_dependency_observer_ignores_transient_link_notifications(
    monkeypatch,
) -> None:
    import VibeCADGui as gui
    import VibeCADVibeScriptDomainPublication as publication

    class Service:
        def invalidate_vibescript_reference_snapshots(self, _obj):
            raise AssertionError("A transient link pulse must not evict source snapshots")

    class Source:
        Document = object()

    monkeypatch.setattr(gui, "get_service", lambda: Service())
    monkeypatch.setattr(
        publication,
        "mark_programs_stale_from_source",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("A transient link pulse must not mark programs stale")
        ),
    )
    gui._VibeCADDocumentObserver().slotChangedObject(Source(), "_LinkTouched")


def test_occurrence_screenshot_isolation_hides_definition_but_preserves_body_tip() -> None:
    from tool_impl.service import core_set_view

    class View:
        def __init__(self, visible: bool = True):
            self.Visibility = visible

    class Object:
        def __init__(self, name: str, type_id: str):
            self.Name = name
            self.TypeId = type_id
            self.ViewObject = View()
            self.Tip = None
            self.Group = []
            self.LinkedObject = None

        def getParentGeoFeatureGroup(self):
            return None

        def getParentGroup(self):
            return None

    tip = Object("ImportedFeature", "Part::Feature")
    definition = Object("ImportedBody", "PartDesign::Body")
    definition.Tip = tip
    occurrence = Object("PlacedMotor", "App::Link")
    occurrence.LinkedObject = definition
    unrelated = Object("OtherBody", "PartDesign::Body")
    objects = [tip, definition, occurrence, unrelated]

    class Document:
        Objects = objects

        @staticmethod
        def getObject(name):
            return next((obj for obj in objects if obj.Name == name), None)

    with core_set_view.temporarily_isolate_objects(Document(), [occurrence.Name]):
        assert occurrence.ViewObject.Visibility is True
        assert definition.ViewObject.Visibility is False
        assert tip.ViewObject.Visibility is True
        assert unrelated.ViewObject.Visibility is False

    assert all(obj.ViewObject.Visibility is True for obj in objects)


def test_screenshot_isolation_restores_children_hidden_by_container_side_effects() -> None:
    from tool_impl.service import core_set_view

    class View:
        def __init__(self):
            self._visible = True
            self.children = []

        @property
        def Visibility(self):
            return self._visible

        @Visibility.setter
        def Visibility(self, visible):
            self._visible = bool(visible)
            if not self._visible:
                for child in self.children:
                    child.Visibility = False

    class Object:
        def __init__(self, name: str, type_id: str = "Part::Feature"):
            self.Name = name
            self.TypeId = type_id
            self.ViewObject = View()
            self.Tip = None

        def getParentGeoFeatureGroup(self):
            return None

        def getParentGroup(self):
            return None

    target = Object("FixtureBlock")
    operation = Object("FaceOperation", "Path::FeaturePython")
    controller = Object("ToolController", "Path::FeaturePython")
    job = Object("Job", "App::DocumentObjectGroupPython")
    job.ViewObject.children = [operation.ViewObject, controller.ViewObject]
    objects = [target, job, operation, controller]

    class Document:
        Objects = objects

        @staticmethod
        def getObject(name):
            return next((obj for obj in objects if obj.Name == name), None)

    with core_set_view.temporarily_isolate_objects(Document(), [target.Name]):
        assert target.ViewObject.Visibility is True
        assert job.ViewObject.Visibility is False
        assert operation.ViewObject.Visibility is False
        assert controller.ViewObject.Visibility is False

    assert all(obj.ViewObject.Visibility is True for obj in objects)


def test_all_screenshot_frame_uses_current_visibility_without_unhiding_models() -> None:
    from tool_impl.service import core_set_view

    class Bounds:
        XMin = YMin = ZMin = 0.0
        XMax = YMax = ZMax = 1.0

    class Shape:
        BoundBox = Bounds()

        @staticmethod
        def isNull():
            return False

    class Object:
        def __init__(self, name: str, visible: bool, type_id="PartDesign::Body"):
            self.Name = name
            self.TypeId = type_id
            self.Shape = None if type_id == "App::Link" else Shape()
            self.ViewObject = type("View", (), {"Visibility": visible})()

        def getParentGeoFeatureGroup(self):
            return None

    visible = Object("VisibleBody", True)
    hidden = Object("HiddenBody", False)
    occurrence = Object("VisibleOccurrence", True, "App::Link")
    objects = [visible, hidden, occurrence]

    class Document:
        Objects = objects

        @staticmethod
        def getObject(name):
            return next((obj for obj in objects if obj.Name == name), None)

    resolved = core_set_view.resolve_frame_objects(
        object(),
        Document(),
        object(),
        "all",
        None,
    )
    assert resolved == {
        "ok": True,
        "object_names": ["VisibleBody", "VisibleOccurrence"],
    }
    assert hidden.ViewObject.Visibility is False


def test_all_screenshot_fit_never_reveals_hidden_body_implementation() -> None:
    from tool_impl.service import core_set_view

    class ViewState:
        def __init__(self, visible: bool):
            self.Visibility = visible

    class Object:
        TypeId = "PartDesign::Body"

        def __init__(self, name: str, visible: bool):
            self.Name = name
            self.ViewObject = ViewState(visible)
            self.Tip = None

        @staticmethod
        def getParentGeoFeatureGroup():
            return None

        @staticmethod
        def getParentGroup():
            return None

    body = Object("VisibleBody", True)
    hidden_tip = Object("HiddenTip", False)
    hidden_tip.TypeId = "PartDesign::Feature"
    body.Tip = hidden_tip
    unrelated = Object("OtherBody", True)
    objects = [body, hidden_tip, unrelated]

    class Document:
        Objects = objects

        @staticmethod
        def getObject(name):
            return next((obj for obj in objects if obj.Name == name), None)

    observed = []

    class View:
        @staticmethod
        def fitAll():
            observed.append(
                {
                    obj.Name: bool(obj.ViewObject.Visibility)
                    for obj in objects
                }
            )

    result = core_set_view.frame_view(
        object(),
        View(),
        Document(),
        "all",
        [body.Name],
    )

    assert result["method"] == "visible_model_target_fit"
    assert observed == [
        {"VisibleBody": True, "HiddenTip": False, "OtherBody": False}
    ]
    assert body.ViewObject.Visibility is True
    assert hidden_tip.ViewObject.Visibility is False
    assert unrelated.ViewObject.Visibility is True


def test_new_assembly_presentation_shows_only_component_occurrences() -> None:
    import VibeCADVibeScriptDomainPublication as publication

    class View:
        def __init__(self, visible: bool):
            self.Visibility = visible

    class Object:
        def __init__(self, visible: bool):
            self.ViewObject = View(visible)

    assembly = Object(False)
    component = Object(False)
    joint = Object(False)
    publication._configure_new_assembly_presentation(
        assembly,
        [
            {"name": "Component", "type": "component_link"},
            {"name": "Joint", "type": "joint"},
        ],
        {"Component": component, "Joint": joint},
    )

    assert assembly.ViewObject.Visibility is True
    assert component.ViewObject.Visibility is True
    assert joint.ViewObject.Visibility is False


def test_gui_document_observer_ignores_properties_restored_from_file(
    monkeypatch,
) -> None:
    import VibeCADGui as gui
    import VibeCADVibeScriptDomainPublication as publication

    observed = []
    monkeypatch.setattr(gui.App, "isRestoring", lambda: True, raising=False)
    monkeypatch.setattr(
        publication,
        "mark_programs_stale_from_source",
        lambda obj, property_name: observed.append((obj, property_name)),
    )

    gui._VibeCADDocumentObserver().slotChangedObject(object(), "Shape")

    assert observed == []
