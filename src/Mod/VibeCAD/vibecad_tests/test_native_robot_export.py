# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeActionManifest import _operation_variant
from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeRobotExport import (
    NativeRobotExportError,
    prepare_robot_export_spec,
)
from VibeCADNativeRobotExportSchema import (
    ROBOT_EXPORT_CAPABILITY_NAME,
    register_robot_export_capability_definition,
    robot_export_capability_definition,
)


_DIGESTS = tuple(character * 64 for character in "abcd")


def _values() -> dict[str, object]:
    return {
        "robot": {"object_name": "Robot"},
        "trajectory": {"object_name": "Trajectory"},
        "expected_robot_setup_state_sha256": _DIGESTS[0],
        "expected_robot_state_sha256": _DIGESTS[1],
        "expected_trajectory_setup_state_sha256": _DIGESTS[2],
        "expected_trajectory_state_sha256": _DIGESTS[3],
    }


def test_kuka_schemas_are_exact_path_free_and_human_authorized() -> None:
    definition = robot_export_capability_definition()

    assert definition.name == ROBOT_EXPORT_CAPABILITY_NAME
    assert tuple(variant.operation for variant in definition.variants) == (
        "export_kuka_compact",
        "export_kuka_full",
    )
    assert tuple(variant.action_ids for variant in definition.variants) == (
        frozenset({"Robot_ExportKukaCompact"}),
        frozenset({"Robot_ExportKukaFull"}),
    )
    assert all(
        variant.surface_ids == frozenset({"manufacture"})
        and variant.transaction_behavior == "output"
        and variant.background_required is False
        and set(variant.parameters["properties"]) == set(_values())
        for variant in definition.variants
    )
    assert tuple(variant.exact_target_type for variant in definition.variants) == (
        "ExactRobotAndNonEmptyTrajectoryWithHumanAuthorizedOutput",
        "ExactRobotAndNonEmptyTrajectoryWithHumanAuthorizedOutputs",
    )
    assert _operation_variant("Robot_ExportKukaCompact") == "export_kuka_compact"
    assert _operation_variant("Robot_ExportKukaFull") == "export_kuka_full"

    serialized = repr(
        definition.provider_schema(
            tuple(variant.operation for variant in definition.variants)
        )
    ).casefold()
    for forbidden in (
        "path",
        "directory",
        "file_name",
        "selection",
        "preselection",
        "command_id",
        "workbench",
    ):
        assert forbidden not in serialized

    registry = NativeCapabilityRegistry()
    register_robot_export_capability_definition(registry)
    assert registry.definition_names == (ROBOT_EXPORT_CAPABILITY_NAME,)


@pytest.mark.parametrize(
    "operation",
    ("export_kuka_compact", "export_kuka_full"),
)
def test_kuka_specs_require_only_exact_frozen_source_state(operation: str) -> None:
    spec = prepare_robot_export_spec(
        "document-uid",
        operation,
        _values(),
    )

    assert spec.operation == operation
    assert spec.robot_ref.document_uid == "document-uid"
    assert spec.robot_ref.object_name == "Robot"
    assert spec.trajectory_ref.object_name == "Trajectory"
    assert spec.expected_robot_state_sha256 == _DIGESTS[1]
    assert spec.expected_trajectory_state_sha256 == _DIGESTS[3]

    malformed = _values()
    malformed["path"] = "/tmp/provider-controlled.src"
    with pytest.raises(NativeRobotExportError, match="fields are incorrect"):
        prepare_robot_export_spec(
            "document-uid",
            operation,
            malformed,
        )

    malformed = _values()
    malformed["expected_robot_state_sha256"] = "not-a-digest"
    with pytest.raises(NativeRobotExportError, match="SHA-256"):
        prepare_robot_export_spec(
            "document-uid",
            operation,
            malformed,
        )
