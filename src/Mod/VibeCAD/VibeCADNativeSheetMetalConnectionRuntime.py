# SPDX-License-Identifier: LGPL-2.1-or-later
"""No document changes or credential material in native connection responses."""

from concurrent.futures import Future

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeRuntimeContext import NativeRuntimeContext


class NativeSheetMetalConnectionRuntime:
    def __init__(self, context):
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(self, arguments, *, asynchronous=False):
        operation, _ = strict_variant_arguments(arguments, {"status": frozenset(), "show_connection": frozenset()})
        self._context.guard()
        import SheetMetalRMFGGui as Connection
        controller = Connection.connection_controller()
        if operation == "show_connection":
            Connection.show_connection()
            status = controller.status()
            return {**status, "panel_open": True, "requires_user_action": status["state"] != "connected"}
        if controller.busy and controller.status()["state"] != "checking":
            status = controller.status()
            return {**status, "pending": True,
                    "requires_user_action": status["state"] in ("starting", "authorizing")}
        if not asynchronous:
            # Synchronous callers can poll an initial read to completion.
            # Restarting the worker on every poll would only return checking.
            if controller.status()["state"] == "unknown":
                controller.refresh()
            return {**controller.status(), "pending": controller.busy, "cached": True}
        future = Future()
        def completed():
            if controller.busy:
                return
            controller.changed.disconnect(completed)
            if not future.set_running_or_notify_cancel():
                return
            try:
                self._context.guard()
                result = controller.status()
            except Exception as error:
                future.set_exception(error)
            else:
                future.set_result(result)
        controller.changed.connect(completed)
        controller.refresh()
        return future
