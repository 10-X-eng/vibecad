# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for isolated, human-authorized CAM post output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import VibeCADNativeManufacturePostWorker as Worker
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufacturePostSchema import (
    manufacture_post_capability_definition,
)


def test_schema_is_exact_job_only_and_exposes_no_process_or_output_controls() -> None:
    definition = manufacture_post_capability_definition()
    schema = definition.provider_schema(("complete_job",))
    branch = schema["parameters"]["oneOf"][0]

    assert definition.primary_classification == "export"
    assert branch["required"] == ["operation", "job"]
    assert branch["additionalProperties"] is False
    assert set(branch["properties"]) == {"operation", "job"}
    assert branch["properties"]["job"]["required"] == [
        "object_name",
        "expected_state_sha256",
    ]
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    for forbidden in (
        '"path"',
        '"processor"',
        '"executable"',
        '"command"',
        '"file_name"',
        '"options"',
    ):
        assert forbidden not in encoded


def test_selected_schema_requires_one_ordered_exact_operation_subset() -> None:
    definition = manufacture_post_capability_definition()
    schema = definition.provider_schema(("selected_operations",))
    branch = schema["parameters"]["oneOf"][0]

    assert branch["required"] == ["operation", "job", "operations"]
    assert branch["additionalProperties"] is False
    assert set(branch["properties"]) == {"operation", "job", "operations"}
    operations = branch["properties"]["operations"]
    assert operations["minItems"] == 1
    assert operations["maxItems"] == 64
    assert operations["uniqueItems"] is True
    assert operations["items"]["required"] == [
        "object_name",
        "expected_state_sha256",
    ]
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    for forbidden in (
        '"path"',
        '"processor"',
        '"executable"',
        '"command"',
        '"file_name"',
        '"options"',
    ):
        assert forbidden not in encoded


def test_result_reader_preserves_child_failure_code_without_exposing_diagnostics(
    tmp_path: Path,
) -> None:
    result = tmp_path / "result.json"
    result.write_text(
        json.dumps(
            {
                "ok": False,
                "error_code": "NATIVE_MANUFACTURE_POST_PROCESSOR_UNSUPPORTED",
                "message": "Configure a modern class-based processor.",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(NativeManufactureError) as raised:
        Worker._read_result(result)

    assert raised.value.failure() == {
        "error_code": "NATIVE_MANUFACTURE_POST_PROCESSOR_UNSUPPORTED",
        "message": "Configure a modern class-based processor.",
    }


def test_result_reader_rejects_symlinks_and_over_limit_metadata(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text('{"ok":true}', encoding="utf-8")
    link = tmp_path / "result.json"
    link.symlink_to(target)
    with pytest.raises(NativeManufactureError, match="unreadable result metadata"):
        Worker._read_result(link)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b" " * (Worker.MAX_POST_RESULT_BYTES + 1) + b"}")
    with pytest.raises(NativeManufactureError) as raised:
        Worker._read_result(oversized)
    assert raised.value.failure()["error_code"] == "NATIVE_MANUFACTURE_POST_LIMIT"


def test_output_requests_are_host_derived_bounded_basenames(tmp_path: Path) -> None:
    source = tmp_path / "private.bin"
    source.write_bytes(b"G21\nM30\n")
    prepared = Worker.PreparedPostOutput(
        frozen=object(),  # The request builder consumes only authenticated files.
        files=(
            Worker.PreparedPostFile(
                path=source,
                file_name="Setup-0.ngc",
                suffix=".ngc",
                section="Setup",
                size_bytes=source.stat().st_size,
                sha256="0" * 64,
            ),
        ),
        total_size_bytes=source.stat().st_size,
    )

    requests = Worker.output_requests(prepared)

    assert len(requests) == 1
    request = requests[0]
    assert request.suggested_file_name == "Setup-0.ngc"
    assert request.allowed_suffixes == (".ngc",)
    assert request.maximum_bytes == Worker.MAX_POST_OUTPUT_BYTES
    assert not hasattr(request, "destination")


def test_cam_post_is_not_a_proven_toolpath() -> None:
    from VibeCADNativeManufacturePostRuntime import stamp_cam_post_unproven

    stamped = stamp_cam_post_unproven({"outputs": [{"file_name": "job.ngc"}]})
    assert stamped["claim_ceiling"] == "not_proven_toolpath"
    assert stamped["proven_toolpath"] is False
    assert stamped["manufacturable"] is False

