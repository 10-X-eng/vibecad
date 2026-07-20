# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contract tests for the unified Part Design VibeScript v2 domain."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import VibeCADVibeScriptDomainRuntime as runtime
import VibeCADVibeScriptDomains as domains
from vibescript_domain_api import DomainValue, create_domain_api
from vibescript_partdesign_api import PartDesignDomainAPI


PROGRAM_ID = "0123456789abcdef0123456789abcdef"


def _pack():
    pack = domains.get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None
    return pack


def _capture(root: Path, *, operation: str, arguments: dict) -> dict:
    pack = _pack()
    return {
        "pack": pack,
        "operation": operation,
        "tool_name": f"vibescript.partdesign.{operation}",
        "arguments": arguments,
        "project_root": str(root),
        "document_name": "PartDesignContractTest",
        "document_uid": "partdesign-contract-document",
        "document_revision": "document-revision-1",
        "document_objects": [],
        "surface": {
            "workbench": pack.workbench,
            "engine": "vibescript",
            "surface_id": pack.surface_id,
        },
        "freecad_home": str(root / "freecad-home"),
        "timeout_seconds": 30.0,
        "memory_limit_bytes": 512 * 1024 * 1024,
    }


def _write_v1_program(root: Path) -> Path:
    directory = root / "vibescript" / PROGRAM_ID
    directory.mkdir(parents=True)
    manifest = {
        "schema": domains.PARTDESIGN_V1_SCHEMA,
        "model_id": PROGRAM_ID,
        "model_name": "Saved Part",
        "source": "result = {'Part': output('Part')}",
        "parameters": {"radius": 4.0},
        "expected_outputs": ["Part"],
        "revision": "saved-v1-revision",
        "outputs": {
            "Part": {
                "object_name": "SavedPartResult",
                "type": "solid",
            }
        },
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return directory


def test_partdesign_uses_the_exact_common_v2_lifecycle() -> None:
    pack = _pack()
    assert pack.production_ready is True
    assert pack.surface_id == "vibescript:partdesign:v2"
    assert pack.tool_names == tuple(
        f"vibescript.partdesign.{operation}"
        for operation in domains.LIFECYCLE_OPERATIONS
    )
    assert domains.LIFECYCLE_OPERATIONS == (
        "describe_api",
        "inspect_program",
        "create_program",
        "edit_source",
        "set_inputs",
        "reconfigure_program",
        "delete_program",
    )


def test_partdesign_runtime_api_is_explicit_and_matches_describe_api() -> None:
    pack = _pack()
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    assert isinstance(api, PartDesignDomainAPI)
    assert api.exported_names == pack.api_exports
    signatures = {
        name: str(inspect.signature(getattr(api, name)))
        for name in api.exported_names
    }
    assert all("*args" not in signature for signature in signatures.values())
    assert all("**kwargs" not in signature for signature in signatures.values())
    assert all("**properties" not in signature for signature in signatures.values())
    description = domains.get_domain_adapter("partdesign").describe_api()
    assert {
        item["name"]: item["signature"]
        for item in description["runtime_exports"]
    } == signatures
    assert description["source_globals"] == ["doc", "inputs", "api"]
    assert description["accepted_output_types"] == ["solid"]


def test_additive_features_can_extend_an_existing_body_feature() -> None:
    api = PartDesignDomainAPI(
        PartDesignDomainAPI.exported_names,
        ("solid",),
    )
    base_profile = api.sketch([api.circle([0, 0], 10)])
    base = api.pad(base_profile, 5)
    boss_profile = api.sketch([api.circle([7, 0], 2)], z_offset_mm=5)
    boss = api.pad(boss_profile, 3, base=base)
    revolved = api.revolve(boss_profile, base=base, axis="V")
    lofted = api.loft(
        [
            api.sketch([api.circle([0, 0], 3)], z_offset_mm=5),
            api.sketch([api.circle([0, 0], 2)], z_offset_mm=10),
        ],
        base=base,
    )
    patterned = api.polar_pattern(boss, 4)

    assert boss.properties["base"] is base
    assert revolved.properties["base"] is base
    assert lofted.properties["base"] is base
    assert patterned.arguments[0] is boss
    assert api.body(patterned).output_type == "solid"


def test_partdesign_rejects_cross_domain_graphs_and_transient_topology_names() -> None:
    api = PartDesignDomainAPI(
        PartDesignDomainAPI.exported_names,
        ("solid",),
    )
    foreign = DomainValue(
        domain="part",
        operation="box",
        output_type="solid",
        arguments=(),
        properties={},
    )
    with pytest.raises(ValueError, match="returned by this Part Design api"):
        api.body(foreign)
    base = api.pad(api.sketch([api.circle([0, 0], 5)]), 5)
    with pytest.raises(ValueError, match="transient FaceN/EdgeN names are forbidden"):
        api.body(
            base,
            interfaces={"Top": {"selection": "Face6"}},
        )


def test_v1_saved_data_migrates_to_a_non_executable_v2_view(tmp_path: Path) -> None:
    directory = _write_v1_program(tmp_path)
    migrated = domains.migrate_program_manifest(
        json.loads((directory / "manifest.json").read_text(encoding="utf-8")),
        artifact_directory=directory,
    )
    assert migrated["schema"] == domains.PROGRAM_SCHEMA
    assert migrated["version"] == 2
    assert migrated["program_id"] == PROGRAM_ID
    assert migrated["domain"] == "partdesign"
    assert migrated["artifact_directory"] == str(directory)
    assert migrated["migration_required"] is True
    assert migrated["migration_action"] == (
        "vibescript.partdesign.reconfigure_program"
    )
    assert migrated["accepted_revision"] == "saved-v1-revision"
    assert migrated["live_outputs"]["Part"]["object_name"] == "SavedPartResult"


def test_v1_source_cannot_edit_set_inputs_or_execute(tmp_path: Path) -> None:
    _write_v1_program(tmp_path)
    for operation, extra in (
        (
            "edit_source",
            {"replacements": [{"old": "output('Part')", "new": "output('Other')"}]},
        ),
        ("set_inputs", {"patch": {"radius": 5.0}}),
    ):
        capture = _capture(
            tmp_path,
            operation=operation,
            arguments={
                "program_id": PROGRAM_ID,
                "expected_revision": "saved-v1-revision",
                **extra,
            },
        )
        with pytest.raises(runtime.DomainRuntimeFailure) as failure:
            runtime.prepare_candidate(capture)
        assert failure.value.payload["failure_code"] == (
            "PROGRAM_RECONFIGURATION_REQUIRED"
        )
        assert failure.value.payload["retry"]["required_changes"] == [
            {
                "tool": "vibescript.partdesign.reconfigure_program",
                "expected_revision": "saved-v1-revision",
                "replace": [
                    "source",
                    "input_schema",
                    "inputs",
                    "expected_outputs",
                ],
            }
        ]
    assert not (tmp_path / "vibescript" / PROGRAM_ID / "program.json").exists()


def test_reconfigure_stages_v2_in_the_existing_saved_program_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = _write_v1_program(tmp_path)
    monkeypatch.setattr(runtime, "_freecadcmd", lambda _home: Path("/FreeCADCmd"))
    source = (
        "profile = api.sketch([api.circle([0,0], inputs['radius'])])\n"
        "feature = api.pad(profile, inputs['height'])\n"
        "result = {'Part': api.body(feature, label='Part')}\n"
    )
    capture = _capture(
        tmp_path,
        operation="reconfigure_program",
        arguments={
            "program_id": PROGRAM_ID,
            "expected_revision": "saved-v1-revision",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {
                    "radius": {"type": "number", "exclusiveMinimum": 0},
                    "height": {"type": "number", "exclusiveMinimum": 0},
                },
                "required": ["radius", "height"],
                "additionalProperties": False,
            },
            "inputs": {"radius": 4.0, "height": 12.0},
            "expected_outputs": [{"name": "Part", "type": "solid"}],
        },
    )
    prepared = runtime.prepare_candidate(capture)
    assert prepared["program_directory"] == str(directory)
    assert prepared["worker_request"]["domain"] == "partdesign"
    assert prepared["worker_request"]["source"] == source
    persisted = json.loads((directory / "program.json").read_text(encoding="utf-8"))
    assert persisted["schema"] == domains.PROGRAM_SCHEMA
    assert persisted["source"] == source
    assert persisted["working_revision"] == prepared["revision"]
    assert "migration_required" not in persisted
    assert "migration_action" not in persisted
    runtime.abandon_prepared_candidate(prepared)
