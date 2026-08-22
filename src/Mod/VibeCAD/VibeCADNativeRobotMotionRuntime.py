# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Robot home and simulation operations."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRobotIntent import expand_robot_motion_intent
from VibeCADNativeRobotMotion import (
    NativeRobotMotionError,
    evaluate_robot_simulation,
    mutate_robot_home,
    preflight_robot_home,
    preflight_robot_simulation,
    prepare_robot_home_spec,
    prepare_robot_simulation_spec,
    robot_home_is_noop,
    verify_robot_home,
    verify_robot_home_noop,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_HOME_ARGUMENTS = frozenset(
    {"robot"}
)
_SIMULATION_ARGUMENTS = frozenset(
    {
        "robot",
        "trajectory",
        "sample_times_s",
    }
)


def _require_current_ticket(
    context: NativeRuntimeContext,
    ticket: NativeCallTicket,
) -> None:
    if not isinstance(ticket, NativeCallTicket):
        raise TypeError("ticket must be a NativeCallTicket")
    current = context.state.current_revision(context.document_uid)
    if current != ticket.expected_revision:
        raise NativeRevisionConflict(ticket.expected_revision, current)


class NativeRobotMotionRuntime:
    """Apply Robot motion only on an exact frozen Robot-capable surface."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_motion(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "set_home_pos": _HOME_ARGUMENTS,
                "restore_home_pos": _HOME_ARGUMENTS,
                "simulate": _SIMULATION_ARGUMENTS,
            },
        )
        self._context.guard()
        _require_current_ticket(self._context, ticket)
        values = expand_robot_motion_intent(
            self._context.document,
            self._context.document_uid,
            operation,
            values,
        )
        if operation == "simulate":
            prepared_simulation = preflight_robot_simulation(
                self._context.document,
                prepare_robot_simulation_spec(self._context.document_uid, values),
            )
            result = evaluate_robot_simulation(
                self._context.document,
                prepared_simulation,
            )
            self._context.guard()
            _require_current_ticket(self._context, ticket)
            return result
        if operation not in {"set_home_pos", "restore_home_pos"}:
            raise NativeRobotMotionError(
                "The requested Robot motion operation is not implemented."
            )
        prepared_home = preflight_robot_home(
            self._context.document,
            prepare_robot_home_spec(
                self._context.document_uid,
                operation,
                values,
            ),
        )
        if robot_home_is_noop(prepared_home):
            return verify_robot_home_noop(
                self._context.document,
                prepared_home,
            )
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name=(
                "Set Native Robot Home"
                if operation == "set_home_pos"
                else "Restore Native Robot Home"
            ),
            mutate=partial(mutate_robot_home, prepared=prepared_home),
            verify=verify_robot_home,
        )
