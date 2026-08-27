# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from pathlib import Path
import runpy

from analysis_fem_publication_parity import (
    PUBLICATION_PARITY_DIMENSIONS,
    SUPPORTED_SOLVERS,
    normalize_publication_evidence,
)


def test_installed_publication_contract_covers_every_supported_solver_and_gate() -> None:
    assert SUPPORTED_SOLVERS == ("calculix", "elmer", "z88", "mystran")
    assert PUBLICATION_PARITY_DIMENSIONS == (
        "result object graph",
        "solver result membership",
        "canonical History order",
        "timeline ownership",
        "input and state hashes",
        "publication receipt presence",
        "public JSON",
        "save and reopen persistence",
    )


def test_publication_normalization_removes_host_object_identity_only() -> None:
    evidence = {
        "solver": "LegacyCalculix",
        "solver_id": 14,
        "root": "LegacyCalculixResult",
        "root_id": 15,
        "resources": [
            {"object_name": "LegacyCalculixOutput", "object_id": 16, "type_id": "App::TextDocument"}
        ],
        "result_membership": ["LegacyCalculixResult", "LegacyCalculixOutput"],
        "history_block": ["LegacyCalculixOutput", "LegacyCalculixResult", "LegacyCalculix"],
        "ownership": ["solver", "root"],
        "input_sha256": "a" * 64,
        "state_sha256": "b" * 64,
        "receipt": None,
        "public": {
            "solver": {"object_name": "LegacyCalculix", "object_id": 14, "state_sha256": "b" * 64},
            "result": {
                "object_name": "LegacyCalculixResult",
                "object_id": 15,
                "solver": "LegacyCalculix",
                "resources": [
                    {
                        "object_name": "LegacyCalculixOutput",
                        "object_id": 16,
                        "type_id": "App::TextDocument",
                    }
                ],
            },
            "execution": {"input_sha256": "a" * 64},
        },
    }

    normalized = normalize_publication_evidence(evidence, solver_key="calculix")

    assert json.dumps(normalized, sort_keys=True)
    assert normalized["solver"] == "calculix"
    assert normalized["result_membership"] == ["root", "resource:0"]
    assert normalized["history_block"] == ["resource:0", "root", "solver"]
    assert normalized["public"]["result"]["solver"] == "solver"
    assert "solver_id" not in normalized
    assert "root_id" not in normalized


def test_installed_runner_parses_marker_with_trailing_host_output() -> None:
    runner = runpy.run_path(
        str(
            Path(__file__).resolve().parents[4]
            / "tools"
            / "run_analysis_fem_installed_publication.py"
        ),
        run_name="analysis_fem_installed_runner_contract",
    )
    report = runner["parse_report"](
        "host progress\n"
        "VIBECAD_ANALYSIS_FEM_INSTALLED_PUBLICATION_OK "
        '{"runtime":"installed-freecadcmd","solvers":{"calculix":{}}}'
        "\nmore host progress\n"
    )

    assert report["solvers"] == {"calculix": {}}
