# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native access to application-level RMFG connection status and browser panel."""

from VibeCADNativeCapabilityRegistry import NativeCapabilityDefinition, NativeCapabilityVariant
from VibeCADNativeDesignSchema import parameters_schema


NAME = "sheet_metal.connection"


def sheetmetal_connection_capability_definition():
    return NativeCapabilityDefinition(name=NAME, primary_classification="read",
        description="Check RMFG connection or open its browser sign-in panel. No credentials are returned.",
        variants=tuple(NativeCapabilityVariant(operation=operation, description=description,
            action_ids=frozenset({"SheetMetal_RMFGConnection"}), surface_ids=frozenset({"sheet_metal"}),
            exact_target_type=None, transaction_behavior="none", background_required=False,
            parameters=parameters_schema({}, ()))
            for operation, description in (
                ("status", "Read connection status without refreshing tokens. A pending browser approval requires the user's action."),
                ("show_connection", "Open the RMFG connection panel so the user can sign in through their browser. Does not upload geometry, request payment permissions or place an order."))))
