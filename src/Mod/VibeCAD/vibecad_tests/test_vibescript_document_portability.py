# SPDX-License-Identifier: LGPL-2.1-or-later

"""Portable FCStd persistence contracts for the VibeScript editor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import VibeCADVibeScriptDomainRuntime as runtime
import VibeCADVibeScriptDomains as domains


PROGRAM_ID = "a" * 32
REVISION = "b" * 64
SOURCE = (
    "w = inputs['width']\n"
    "result = {'Result': api.box(w, w, w)}\n"
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "width": {
            "type": "number",
            "exclusiveMinimum": 0,
        }
    },
    "required": ["width"],
    "additionalProperties": False,
}
EXPECTED_OUTPUTS = [{"name": "Result", "type": "solid"}]


def _pack() -> domains.VibeScriptWorkbenchPack:
    pack = domains.get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None
    return pack


def _portable_contract() -> str:
    return domains.encode_document_program_contract(
        _pack(),
        program_id=PROGRAM_ID,
        label="Portable cube",
        revision=REVISION,
        source=SOURCE,
        input_schema=INPUT_SCHEMA,
        inputs={"width": 10.0},
        expected_outputs=EXPECTED_OUTPUTS,
    )


def test_portable_contract_round_trips_complete_source_and_inputs() -> None:
    manifest = domains.decode_document_program_contract(
        _portable_contract(),
        _pack(),
        expected_program_id=PROGRAM_ID,
        expected_revision=REVISION,
    )

    assert manifest["schema"] == domains.PROGRAM_SCHEMA
    assert manifest["source"] == SOURCE
    assert manifest["input_schema"] == INPUT_SCHEMA
    assert manifest["inputs"] == {"width": 10.0}
    assert manifest["expected_outputs"] == EXPECTED_OUTPUTS
    assert manifest["working_revision"] == REVISION
    assert manifest["accepted_revision"] == REVISION
    assert manifest["accepted_contract"]["source"] == SOURCE


def test_portable_contract_rejects_tampered_source() -> None:
    payload = json.loads(_portable_contract())
    payload["source"] = payload["source"].replace("api.box", "api.sphere")

    with pytest.raises(ValueError, match="digest changed"):
        domains.decode_document_program_contract(
            json.dumps(payload),
            _pack(),
            expected_program_id=PROGRAM_ID,
            expected_revision=REVISION,
        )


def test_editor_draft_round_trips_invalid_source_and_input_json() -> None:
    encoded = domains.encode_editor_draft(
        program_id=PROGRAM_ID,
        domain="partdesign",
        base_revision=REVISION,
        source="result = (\n",
        input_schema=INPUT_SCHEMA,
        inputs_json='{"width": ',
        expected_outputs=EXPECTED_OUTPUTS,
    )

    draft = domains.decode_editor_draft(
        encoded,
        expected_program_id=PROGRAM_ID,
        expected_domain="partdesign",
    )

    assert draft["source"] == "result = (\n"
    assert draft["inputs_json"] == '{"width": '
    assert draft["base_revision"] == REVISION


def test_edited_input_names_replace_generated_required_fields() -> None:
    generated = {
        "type": "object",
        "properties": {
            "width": {"type": "number", "exclusiveMinimum": 0},
            "depth": {"type": "number", "exclusiveMinimum": 0},
        },
        "required": ["width", "depth"],
        "additionalProperties": False,
    }
    values = {
        "overall_width_mm": 61.0,
        "stock_thickness_mm": 0.65,
    }

    synchronized = domains.synchronize_input_schema(generated, values)

    assert list(synchronized["properties"]) == list(values)
    assert synchronized["required"] == list(values)
    assert "width" not in synchronized["properties"]
    assert domains.validate_input_schema(synchronized) == synchronized
    assert domains.validate_program_contract(
        _pack(),
        source=(
            "w = inputs['overall_width_mm']\n"
            "t = inputs['stock_thickness_mm']\n"
            "result = {'Result': api.box(w, t, t)}\n"
        ),
        input_schema=synchronized,
        inputs=values,
        expected_outputs=EXPECTED_OUTPUTS,
    )["inputs"] == values


def test_edited_input_values_keep_existing_constraints() -> None:
    synchronized = domains.synchronize_input_schema(
        INPUT_SCHEMA,
        {"width": 15.0},
    )

    assert synchronized["properties"]["width"]["exclusiveMinimum"] == 0
    assert synchronized["required"] == ["width"]


def test_runtime_hydrates_missing_local_artifact_from_document(
    tmp_path: Path,
) -> None:
    captured = {
        "project_root": str(tmp_path),
        "document_program": {"contract": _portable_contract()},
        "live_programs": [
            {
                "program_id": PROGRAM_ID,
                "revisions": [REVISION],
            }
        ],
    }

    manifest = runtime._load_captured_manifest(
        captured,
        _pack(),
        PROGRAM_ID,
        hydrate_document_contract=True,
    )

    assert manifest["source"] == SOURCE
    artifact = tmp_path / "vibescript" / "partdesign" / PROGRAM_ID / "program.json"
    assert artifact.is_file()
    assert json.loads(artifact.read_text(encoding="utf-8"))["source"] == SOURCE


def test_compatible_local_working_draft_wins_over_accepted_document(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "vibescript" / "partdesign" / PROGRAM_ID / "program.json"
    artifact.parent.mkdir(parents=True)
    local = domains.decode_document_program_contract(
        _portable_contract(),
        _pack(),
    )
    local["source"] = "result = {'Result': api.box(20, 20, 20)}\n"
    local["working_revision"] = "c" * 64
    local["latest_candidate"] = {
        "revision": "c" * 64,
        "status": "failed",
    }
    artifact.write_text(json.dumps(local), encoding="utf-8")
    captured = {
        "project_root": str(tmp_path),
        "document_program": {"contract": _portable_contract()},
        "live_programs": [
            {
                "program_id": PROGRAM_ID,
                "revisions": [REVISION],
            }
        ],
    }

    manifest = runtime._load_captured_manifest(captured, _pack(), PROGRAM_ID)

    assert manifest["working_revision"] == "c" * 64
    assert manifest["accepted_revision"] == REVISION
    assert "20, 20, 20" in manifest["source"]


def test_explicit_editor_build_executes_an_unchanged_revision(
    tmp_path: Path,
) -> None:
    clean = domains.validate_program_contract(
        _pack(),
        source=SOURCE,
        input_schema=INPUT_SCHEMA,
        inputs={"width": 10.0},
        expected_outputs=EXPECTED_OUTPUTS,
    )
    revision = domains.program_revision(domain="partdesign", **clean)
    staging = tmp_path / "vibescript" / "partdesign" / ".staging" / "attempt"
    staging.mkdir(parents=True)
    program_directory = tmp_path / "vibescript" / "partdesign" / PROGRAM_ID
    prepared = {
        "tool_name": "vibescript.partdesign.reconfigure_program",
        "pack": _pack(),
        "program_id": PROGRAM_ID,
        "program_name": "Portable cube",
        "revision": "",
        "contract_revision": revision,
        "base_revision": revision,
        "source": clean["source"],
        "input_schema": clean["input_schema"],
        "inputs": clean["inputs"],
        "expected_outputs": clean["expected_outputs"],
        "manifest": {
            "schema": domains.PROGRAM_SCHEMA,
            "version": domains.PROGRAM_VERSION,
            "program_id": PROGRAM_ID,
            "domain": "partdesign",
            "workbench": "PartDesignWorkbench",
            "label": "Portable cube",
            **clean,
            "working_revision": revision,
            "accepted_revision": revision,
        },
        "program_directory": str(program_directory),
        "staging": str(staging),
        "attempt_id": "attempt",
        "worker_request": {},
        "base_revision_before": revision,
        "native_history_repair_required": False,
        "allow_unchanged_revision": True,
        "finalized": False,
    }

    finalized = runtime.finalize_candidate(prepared, [])

    assert finalized["revision"] == revision
    assert finalized["finalized"] is True
    assert finalized["document_program_contract"]
    assert (
        json.loads((program_directory / "program.json").read_text(encoding="utf-8"))[
            "working_revision"
        ]
        == revision
    )
