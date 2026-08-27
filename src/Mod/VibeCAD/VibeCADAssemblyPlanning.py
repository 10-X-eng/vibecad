# SPDX-License-Identifier: LGPL-2.1-or-later

"""Installed compatibility facade for bounded Assembly planning contracts."""

from tool_impl.assembly_planning import *  # noqa: F401,F403


def read_live_planning_scenario(*args, **kwargs):
    """Load the Native document reader only when a live scenario is requested."""

    from VibeCADNativeAssemblyPlanningScenario import read_live_planning_scenario as read

    return read(*args, **kwargs)


def accept_joint_proposal_native(*args, **kwargs):
    """Load the optional Native owner adapter only when live acceptance is requested."""

    from VibeCADNativeAssemblyPlanning import accept_joint_proposal_native as accept

    return accept(*args, **kwargs)


def accept_coupling_proposal_native(*args, **kwargs):
    """Load the optional Native owner adapter only for live coupling acceptance."""

    from VibeCADNativeAssemblyPlanning import accept_coupling_proposal_native as accept

    return accept(*args, **kwargs)
