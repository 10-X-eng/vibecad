# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact, human-authorized Robot setup."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeInput import NativeInputError
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
    """Create Robots only in the exact frozen Assemble document."""

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
                )
            },
        )
        if operation != "create":
            raise NativeRobotSetupError(
                "The requested Robot setup operation is not implemented."
            )
        self._context.guard()
        _require_current_ticket(self._context, ticket)
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
