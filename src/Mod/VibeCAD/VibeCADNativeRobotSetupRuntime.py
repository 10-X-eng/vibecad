# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact, human-authorized Robot setup."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeInput import NativeInputError
from VibeCADNativeRobotDefaults import (
    prepare_robot_motion_defaults,
    prepare_robot_orientation_defaults,
    set_robot_motion_defaults,
    set_robot_orientation_defaults,
)
from VibeCADNativeRobotSetup import (
    NativeRobotSetupError,
    RobotCreateSpec,
    create_robot,
    finalize_robot_create_preflight,
    preflight_robot_create_boundary,
    robot_kinematic_input_request,
    robot_visual_input_request,
    verify_created_robot,
)
from VibeCADNativeRobotTool import (
    attach_robot_tool_shape,
    preflight_robot_tool_shape,
    prepare_robot_tool_shape_spec,
    robot_tool_shape_is_noop,
    verify_robot_tool_shape_attachment,
    verify_robot_tool_shape_noop,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


def _require_current_ticket(
    context: NativeRuntimeContext,
    ticket: NativeCallTicket,
) -> None:
    if not isinstance(ticket, NativeCallTicket):
        raise TypeError("ticket must be a NativeCallTicket")
    current = context.state.current_revision(context.document_uid)
    if current != ticket.expected_revision:
        raise NativeRevisionConflict(ticket.expected_revision, current)


class NativeRobotSetupRuntime:
    """Configure Robots only on the exact frozen Assemble surface."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_setup(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "create": frozenset(
                    {
                        "label",
                        "expected_state_sha256",
                        "expected_robot_count",
                    }
                ),
                "add_tool_shape": frozenset(
                    {
                        "robot",
                        "tool_shape",
                        "expected_setup_state_sha256",
                        "expected_robot_state_sha256",
                        "expected_tool_shape_state_sha256",
                    }
                ),
                "set_default_orientation": frozenset(
                    {"expected_defaults_state_sha256", "placement"}
                ),
                "set_default_values": frozenset(
                    {
                        "expected_defaults_state_sha256",
                        "speed_mm_per_s",
                        "continuous",
                        "acceleration_mm_per_s2",
                    }
                ),
            },
        )
        self._context.guard()
        _require_current_ticket(self._context, ticket)
        if operation == "add_tool_shape":
            prepared = preflight_robot_tool_shape(
                self._context.document,
                prepare_robot_tool_shape_spec(self._context.document_uid, values),
            )
            if robot_tool_shape_is_noop(prepared):
                return verify_robot_tool_shape_noop(
                    self._context.document,
                    prepared,
                )
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Attach Native Robot Tool",
                mutate=partial(attach_robot_tool_shape, prepared=prepared),
                verify=verify_robot_tool_shape_attachment,
            )
        if operation == "set_default_orientation":
            return set_robot_orientation_defaults(
                self._context,
                prepare_robot_orientation_defaults(values),
            )
        if operation == "set_default_values":
            return set_robot_motion_defaults(
                self._context,
                prepare_robot_motion_defaults(values),
            )
        if operation != "create":
            raise NativeRobotSetupError(
                "The requested Robot setup operation is not implemented."
            )
        boundary = preflight_robot_create_boundary(
            self._context.document,
            RobotCreateSpec(
                label=values["label"],
                expected_state_sha256=values["expected_state_sha256"],
                expected_robot_count=values["expected_robot_count"],
            ),
        )
        authorizer = self._context.authorize_input
        if authorizer is None:
            raise NativeRobotSetupError(
                "Human input authorization is unavailable in this VibeCAD session."
            )
        visual_request = robot_visual_input_request()
        try:
            visual_authorization = authorizer(visual_request)
            if visual_authorization is None:
                raise NativeRobotSetupError(
                    "The human cancelled Robot visual-definition authorization."
                )
            visual = visual_authorization.claim(visual_request)
        except NativeInputError as exc:
            raise NativeRobotSetupError(str(exc)) from exc
        self._context.guard()
        _require_current_ticket(self._context, ticket)
        kinematic_request = robot_kinematic_input_request()
        try:
            kinematic_authorization = authorizer(kinematic_request)
            if kinematic_authorization is None:
                raise NativeRobotSetupError(
                    "The human cancelled Robot kinematic-definition authorization."
                )
            kinematics = kinematic_authorization.claim(kinematic_request)
        except NativeInputError as exc:
            raise NativeRobotSetupError(str(exc)) from exc
        self._context.guard()
        _require_current_ticket(self._context, ticket)
        prepared = finalize_robot_create_preflight(
            self._context.document,
            boundary,
            visual,
            kinematics,
        )
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Create Native Robot",
            mutate=partial(create_robot, prepared=prepared),
            verify=verify_created_robot,
        )
