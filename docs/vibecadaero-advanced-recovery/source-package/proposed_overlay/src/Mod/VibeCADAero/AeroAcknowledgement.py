# SPDX-License-Identifier: LGPL-2.1-or-later
"""Persistence contract for VibeCADAero's single first-use acknowledgement.

This module intentionally contains no license-purpose classification, solver-specific
checks, telemetry, version counter, expiry, or repeated acceptance mechanism.
The GUI/native entry surface owns presentation of the one checkbox and calls
``acknowledge()`` once the user dismisses the first-use notice.
"""

from __future__ import annotations

from typing import Any

PREFERENCE_GROUP = "User parameter:BaseApp/Preferences/Mod/VibeCADAero"
ACKNOWLEDGEMENT_KEY = "ThirdPartyNoticesAcknowledged"
ACKNOWLEDGEMENT_TEXT = "I understand."
PRODUCT_LICENSE_NOTICE = (
    "VibeCAD Aero can use third-party software with license terms separate from VibeCAD. "
    "Those terms apply only to the third-party components they govern and do not change "
    "the VibeCAD/VibeCADAero license or ownership of CAD designs created in VibeCAD. "
    "See Third-Party Notices for details."
)


def _preferences(store: Any | None = None) -> Any:
    if store is not None:
        return store
    import FreeCAD  # type: ignore
    return FreeCAD.ParamGet(PREFERENCE_GROUP)


def is_acknowledged(store: Any | None = None) -> bool:
    """Return the one persistent Aero acknowledgement bit."""
    return bool(_preferences(store).GetBool(ACKNOWLEDGEMENT_KEY, False))


def acknowledge(store: Any | None = None) -> None:
    """Persist that the informational notice was acknowledged. There is deliberately no version/expiry value."""
    _preferences(store).SetBool(ACKNOWLEDGEMENT_KEY, True)


def first_use_state(store: Any | None = None) -> dict[str, Any]:
    """Small UI/native contract; does not classify purpose or solver eligibility."""
    accepted = is_acknowledged(store)
    return {
        "show_notice": not accepted,
        "acknowledged": accepted,
        "product_license_notice": PRODUCT_LICENSE_NOTICE,
        "checkbox_text": ACKNOWLEDGEMENT_TEXT,
        "preference_key": ACKNOWLEDGEMENT_KEY,
        "versioned": False,
    }
