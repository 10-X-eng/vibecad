# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
import runpy

import pytest


def _runner() -> dict:
    return runpy.run_path(
        str(
            Path(__file__).resolve().parents[4]
            / "tools"
            / "run_analysis_fem_installed_lifecycle.py"
        ),
        run_name="analysis_fem_installed_lifecycle_runner_contract",
    )


def test_installed_lifecycle_runner_requires_every_refusal_and_rebound() -> None:
    report = _runner()["parse_report"](
        "progress\n"
        "VIBECAD_ANALYSIS_FEM_INSTALLED_LIFECYCLE_OK "
        '{"runtime":"installed-freecadcmd",'
        '"synthetic_result_fields":true,"physical_solver_validation":false,'
        '"refusals":['
        '{"case":"closed_source","refused":true},'
        '{"case":"switched_document","refused":true},'
        '{"case":"same_name_replacement_uid","refused":true},'
        '{"case":"solver_state_changed","refused":true},'
        '{"case":"history_changed","refused":true},'
        '{"case":"runtime_preference_changed","refused":true}],'
        '"publication":{"rebound":true,"claim_ceiling":"model_unqualified",'
        '"qualified":false}}\ntrailing host output\n'
    )

    assert report["publication"]["rebound"] is True
    assert len(report["refusals"]) == 6


def test_installed_lifecycle_runner_rejects_partial_success_marker() -> None:
    with pytest.raises(RuntimeError, match="incomplete"):
        _runner()["parse_report"](
            "VIBECAD_ANALYSIS_FEM_INSTALLED_LIFECYCLE_OK "
            '{"runtime":"installed-freecadcmd","synthetic_result_fields":true,'
            '"physical_solver_validation":false,"refusals":[],'
            '"publication":{"rebound":true,"claim_ceiling":"model_unqualified",'
            '"qualified":false}}'
        )
