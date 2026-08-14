# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact structured CAM Custom operation creation."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import finite_number
from VibeCADNativeManufactureProgram import (
    PreparedProgramBoundary,
    assert_program_boundary_current,
    preflight_program_boundary,
    program_error,
    program_label,
    program_mutation_draft,
    verify_program_operation,
)
from VibeCADNativeManufactureState import (
    resolve_tool_controller_target,
    tool_controller_state,
)
from VibeCADNativeMutation import NativeMutationDraft


MAX_CUSTOM_BLOCKS = 64
MAX_CUSTOM_PARAMETERS = 16
MAX_CUSTOM_COMMENT_CHARACTERS = 256
CUSTOM_COOLANT_MODES = frozenset({"none", "flood", "mist"})
CUSTOM_PARAMETER_WORDS = frozenset(
    {
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
        "H",
        "I",
        "J",
        "K",
        "L",
        "P",
        "Q",
        "R",
        "S",
        "T",
        "U",
        "V",
        "W",
        "X",
        "Y",
        "Z",
    }
)
_CUSTOM_CODE = re.compile(r"^[GM][0-9]{1,4}(?:\.[0-9]{1,3})?$")
_HUMAN_COOLANT = {"none": "None", "flood": "Flood", "mist": "Mist"}


@dataclass(frozen=True, slots=True)
class CustomCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    coolant: Any
    blocks: Any


@dataclass(frozen=True, slots=True)
class PreparedCustomBlock:
    kind: str
    command_name: str
    gcode: str
    parameter_count: int


@dataclass(frozen=True, slots=True)
class PreparedCustomCreate:
    label: str
    controller: Any
    controller_before: Mapping[str, Any]
    coolant: str
    blocks: tuple[PreparedCustomBlock, ...]
    boundary: PreparedProgramBoundary


def _safe_ascii(
    value: Any,
    noun: str,
    *,
    maximum_length: int,
) -> str:
    if not isinstance(value, str):
        program_error(
            f"{noun} must be one string.",
            repair={"field": noun, "expected_type": "string"},
        )
    result = value.strip()
    if not result or len(result) > maximum_length:
        program_error(
            f"{noun} must contain 1 through {maximum_length} characters.",
            repair={
                "field": noun,
                "minimum_length": 1,
                "maximum_length": maximum_length,
            },
        )
    rejected = next(
        (
            character
            for character in result
            if ord(character) < 0x20
            or ord(character) > 0x7E
            or character in "()"
        ),
        None,
    )
    if rejected is not None:
        program_error(
            f"{noun} must use printable ASCII without parentheses or line breaks.",
            repair={
                "field": noun,
                "accepted": "printable ASCII 0x20 through 0x7e except ( and )",
                "rejected_codepoint": f"U+{ord(rejected):04X}",
            },
        )
    return result


def _coolant(value: Any) -> str:
    if not isinstance(value, str) or value not in CUSTOM_COOLANT_MODES:
        program_error(
            "coolant must be exactly 'none', 'flood', or 'mist'.",
            repair={
                "field": "coolant",
                "allowed_values": ["none", "flood", "mist"],
            },
        )
    return value


def _command_block(index: int, value: Mapping[str, Any]) -> PreparedCustomBlock:
    if set(value) != {"kind", "code", "parameters"}:
        program_error(
            f"Custom command block {index} must contain exactly kind, code, and parameters."
        )
    code = value.get("code")
    if not isinstance(code, str) or _CUSTOM_CODE.fullmatch(code) is None:
        program_error(
            f"Custom command block {index} code must be one uppercase G or M code.",
            repair={
                "field": f"blocks[{index}].code",
                "accepted_pattern": "G or M followed by 1-4 digits and optional . plus 1-3 digits",
                "example": "G4",
            },
        )
    raw_parameters = value.get("parameters")
    if (
        not isinstance(raw_parameters, list)
        or len(raw_parameters) > MAX_CUSTOM_PARAMETERS
    ):
        program_error(
            f"Custom command block {index} requires 0 through 16 parameters."
        )
    parameters: dict[str, float] = {}
    for parameter_index, item in enumerate(raw_parameters):
        if not isinstance(item, Mapping) or set(item) != {"word", "value"}:
            program_error(
                f"Custom parameter {index}.{parameter_index} must contain exactly word and value."
            )
        word = item.get("word")
        if not isinstance(word, str) or word not in CUSTOM_PARAMETER_WORDS:
            program_error(
                f"Custom parameter {index}.{parameter_index} has an unsupported word.",
                repair={
                    "field": f"blocks[{index}].parameters[{parameter_index}].word",
                    "allowed_values": sorted(CUSTOM_PARAMETER_WORDS),
                },
            )
        if word in parameters:
            program_error(
                f"Custom command block {index} repeats parameter word {word}."
            )
        parameters[word] = finite_number(
            item.get("value"),
            f"Custom parameter {index}.{parameter_index} value",
            minimum=-1_000_000_000.0,
            maximum=1_000_000_000.0,
        )
    try:
        import Path

        command = Path.Command(code, parameters)
        gcode = str(command.toGCode())
    except Exception as exc:
        raise NativeManufactureError(
            f"Custom command block {index} is not a valid structured Path command.",
            error_code="NATIVE_ARGUMENTS_INVALID",
            repair={
                "field": f"blocks[{index}]",
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:240],
            },
        ) from exc
    if not gcode or dict(command.Parameters) != parameters:
        program_error(
            f"Custom command block {index} did not retain its exact parameters."
        )
    return PreparedCustomBlock(
        kind="command",
        command_name=str(command.Name),
        gcode=gcode,
        parameter_count=len(parameters),
    )


def _comment_block(index: int, value: Mapping[str, Any]) -> PreparedCustomBlock:
    if set(value) != {"kind", "comment"}:
        program_error(
            f"Custom comment block {index} must contain exactly kind and comment."
        )
    comment = _safe_ascii(
        value.get("comment"),
        f"blocks[{index}].comment",
        maximum_length=MAX_CUSTOM_COMMENT_CHARACTERS,
    )
    import Path

    command = Path.Command(f"({comment})")
    return PreparedCustomBlock(
        kind="comment",
        command_name=str(command.Name),
        gcode=str(command.toGCode()),
        parameter_count=0,
    )


def _blocks(value: Any) -> tuple[PreparedCustomBlock, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_CUSTOM_BLOCKS:
        program_error("blocks must contain 1 through 64 ordered Custom blocks.")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            program_error(f"Custom block {index} must be one closed object.")
        kind = item.get("kind")
        if kind == "command":
            result.append(_command_block(index, item))
        elif kind == "comment":
            result.append(_comment_block(index, item))
        else:
            program_error(
                f"Custom block {index} kind must be exactly 'command' or 'comment'."
            )
    return tuple(result)


def preflight_custom_create(
    document: Any,
    spec: CustomCreateSpec,
) -> PreparedCustomCreate:
    if not isinstance(spec, CustomCreateSpec):
        raise TypeError("spec must be a CustomCreateSpec")
    label = program_label(spec.label)
    coolant = _coolant(spec.coolant)
    blocks = _blocks(spec.blocks)
    boundary = preflight_program_boundary(
        document,
        spec.job,
        noun="CAM Custom operation",
    )
    controller, controller_before = resolve_tool_controller_target(
        document,
        spec.tool_controller,
    )
    if controller not in tuple(boundary.job.Tools.Group or ()) or str(
        controller_before.get("job_name") or ""
    ) != str(boundary.job.Name):
        program_error(
            "The exact tool controller is not owned by the exact CAM Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return PreparedCustomCreate(
        label=label,
        controller=controller,
        controller_before=controller_before,
        coolant=coolant,
        blocks=blocks,
        boundary=boundary,
    )


def _assert_custom_current(document: Any, prepared: PreparedCustomCreate) -> None:
    assert_program_boundary_current(document, prepared.boundary)
    if (
        prepared.controller not in tuple(prepared.boundary.job.Tools.Group or ())
        or tool_controller_state(prepared.controller).get("state_sha256")
        != prepared.controller_before.get("state_sha256")
    ):
        program_error(
            "The exact CAM tool controller changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )


def create_custom(
    document: Any,
    *,
    prepared: PreparedCustomCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedCustomCreate):
        raise TypeError("prepared must be a PreparedCustomCreate")
    _assert_custom_current(document, prepared)
    boundary = prepared.boundary
    try:
        import Path.Op.Gui.Custom as CustomGui

        operation = CustomGui.CreateInTransaction(
            document,
            boundary.job,
            name="Custom",
            label=prepared.label,
            tool_controller=prepared.controller,
            coolant_mode=_HUMAN_COOLANT[prepared.coolant],
            gcode=tuple(block.gcode for block in prepared.blocks),
        )
        CustomGui._validate_custom_result(
            document,
            boundary.job,
            operation,
            prepared.controller,
            _HUMAN_COOLANT[prepared.coolant],
            require_path=False,
        )
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM Custom factory could not create the requested operation.",
            error_code="NATIVE_MANUFACTURE_OPERATION_CREATE_FAILED",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    return program_mutation_draft(
        boundary,
        operation,
        value={"prepared": prepared},
    )


def _expected_path(
    prepared: PreparedCustomCreate,
    *,
    actual_label: str,
) -> tuple[str, ...]:
    import Path

    commands = [
        Path.Command(f"({actual_label})"),
        Path.Command("(Begin Custom)"),
        *(Path.Command(block.gcode) for block in prepared.blocks),
        Path.Command("(End Custom)"),
    ]
    if prepared.coolant != "none":
        feed_indices = [
            index
            for index, command in enumerate(commands)
            if command.Name in Path.Geom.CmdMove or command.Name in {"G74", "G84"}
        ]
        if feed_indices:
            commands.insert(
                feed_indices[0],
                Path.Command("M8" if prepared.coolant == "flood" else "M7"),
            )
            commands.insert(feed_indices[-1] + 2, Path.Command("M9"))
    return tuple(str(command.toGCode()) for command in commands)


def verify_created_custom(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedCustomCreate) or operation is None:
        raise TypeError("draft must contain one exact prepared CAM Custom operation")

    import Path.Base.Util as PathUtil
    import Path.Op.Custom as PathCustom
    import Path.Op.Gui.Base as PathOpGui

    state, after_job = verify_program_operation(
        document,
        prepared.boundary,
        operation,
        label=prepared.label,
        proxy_type=PathCustom.ObjectCustom,
        view_proxy_type=PathOpGui.ViewProvider,
        allow_numeric_label_suffix=True,
    )
    stored = tuple(str(line) for line in operation.Gcode)
    actual = tuple(
        str(command.toGCode())
        for command in tuple(getattr(operation.Path, "Commands", ()) or ())
    )
    expected_stored = tuple(block.gcode for block in prepared.blocks)
    expected_path = _expected_path(
        prepared,
        actual_label=str(operation.Label),
    )
    controller_after = tool_controller_state(prepared.controller)
    if (
        isinstance(operation.Proxy, PathCustom.ObjectEmbeddedPath)
        or PathUtil.toolControllerForOp(operation) is not prepared.controller
        or PathUtil.coolantModeForOp(operation) != _HUMAN_COOLANT[prepared.coolant]
        or str(operation.Source) != "Text"
        or str(operation.GcodeFile)
        or stored != expected_stored
        or actual != expected_path
        or controller_after.get("state_sha256")
        != prepared.controller_before.get("state_sha256")
    ):
        program_error(
            "The created CAM Custom operation did not retain its exact structured command stream.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return {
        "operation": "custom",
        "object_name": str(operation.Name),
        "label": str(operation.Label)[:160],
        "job_object_name": str(prepared.boundary.job.Name),
        "tool_controller_object_name": str(prepared.controller.Name),
        "coolant": prepared.coolant,
        "block_count": len(prepared.blocks),
        "machine_command_count": sum(
            block.kind == "command" for block in prepared.blocks
        ),
        "comment_block_count": sum(
            block.kind == "comment" for block in prepared.blocks
        ),
        "parameter_count": sum(
            block.parameter_count for block in prepared.blocks
        ),
        "path_command_count": len(actual),
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
        "tool_controller_state_sha256": controller_after.get("state_sha256"),
    }
