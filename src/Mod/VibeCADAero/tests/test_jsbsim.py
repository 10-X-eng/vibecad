# SPDX-License-Identifier: LGPL-2.1-or-later

"""JSBSim plant export keeps XML even when the FDM boot fails."""

from __future__ import annotations

from pathlib import Path

import AeroJSBSim as jsbsim_export


def _sample_results():
    return {
        "CL": 0.8,
        "CD": 0.04,
        "CM": -0.02,
        "CLalpha": 5.0,
        "Cmalpha": -0.8,
        "alpha_deg": 4.0,
        "span_m": 0.5,
        "chord_m": 0.09,
        "reference_area_m2": 0.09,
        "mass_kg": 0.1496,
        "xyz_ref": [0.0225, 0.0, 0.063],
        "P_hover": 12.0,
    }


def test_write_plant_contains_metrics_aero_and_electric_stub(tmp_path):
    written = jsbsim_export.write_plant(_sample_results(), output_dir=tmp_path)
    xml = Path(written["fdm_path"]).read_text(encoding="utf-8")
    assert "<fdm_config" in xml
    assert "<metrics>" in xml
    assert "<wingarea" in xml
    assert "CLalpha" in xml or "CL_alpha" in xml or "aero/alpha-rad" in xml
    assert "electric" in xml.lower()
    assert "direct" in xml.lower()
    assert written["engine_path"]
    assert written["thruster_path"]


def test_load_failure_is_reported_without_deleting_xml(tmp_path):
    def boom(_path):
        raise RuntimeError("IC contains NaNs")

    written = jsbsim_export.write_plant(
        _sample_results(),
        output_dir=tmp_path,
        load_fn=boom,
    )
    assert Path(written["fdm_path"]).is_file()
    assert "NaN" in written["boot_error"] or "NaN" in written.get("message", "")
    assert written["loaded"] is False


def test_missing_jsbsim_package_still_writes_xml(tmp_path, monkeypatch):
    monkeypatch.setattr(jsbsim_export, "_try_import_jsbsim", lambda: None)
    written = jsbsim_export.write_plant(_sample_results(), output_dir=tmp_path)
    assert Path(written["fdm_path"]).is_file()
    assert written["loaded"] is False
    assert "jsbsim" in written["boot_error"].lower()
    assert "pip install" in written["boot_error"]
