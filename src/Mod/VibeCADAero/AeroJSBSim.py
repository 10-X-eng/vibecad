# SPDX-License-Identifier: LGPL-2.1-or-later

"""Write a crude JSBSim 6DOF plant from solved coefficients."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

from AeroSolvers import bundled_python_command

MODEL_NAME = "vibecad_aero"


def write_plant(
    results: dict[str, Any],
    *,
    output_dir: str | Path | None = None,
    load_fn: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    dest = Path(output_dir) if output_dir is not None else default_output_dir()
    aircraft_dir = dest / MODEL_NAME
    engine_dir = dest / "engine"
    aircraft_dir.mkdir(parents=True, exist_ok=True)
    engine_dir.mkdir(parents=True, exist_ok=True)

    fdm_path = aircraft_dir / f"{MODEL_NAME}.xml"
    engine_path = engine_dir / "electric.xml"
    thruster_path = engine_dir / "direct.xml"
    fdm_path.write_text(_fdm_xml(results), encoding="utf-8")
    engine_path.write_text(_electric_engine_xml(results), encoding="utf-8")
    thruster_path.write_text(_direct_thruster_xml(), encoding="utf-8")

    loaded = False
    boot_error = ""
    try:
        if load_fn is not None:
            load_fn(str(fdm_path))
            loaded = True
        else:
            loaded, boot_error = try_load_model(dest, MODEL_NAME)
    except Exception as exc:
        loaded = False
        boot_error = str(exc)

    if not loaded and not boot_error:
        boot_error = "JSBSim did not load the plant."

    return {
        "fdm_path": str(fdm_path),
        "engine_path": str(engine_path),
        "thruster_path": str(thruster_path),
        "model": MODEL_NAME,
        "loaded": loaded,
        "boot_error": boot_error,
        "message": boot_error,
        "output_dir": str(dest),
    }


def default_output_dir(doc: Any | None = None) -> Path:
    filename = str(getattr(doc, "FileName", "") or "")
    if filename:
        return Path(filename).resolve().parent / "jsbsim"
    try:
        import FreeCAD

        root = Path(FreeCAD.getUserAppDataDir()) / "VibeCADAero" / "jsbsim"
        return root
    except Exception:
        return Path.home() / ".local" / "share" / "VibeCADAero" / "jsbsim"


def try_load_model(root: Path, model: str) -> tuple[bool, str]:
    jsbsim = _try_import_jsbsim()
    if jsbsim is None:
        return (
            False,
            "jsbsim is not installed. Install it into VibeCAD's bundled Python:\n"
            f"  {bundled_python_command('jsbsim')}\n"
            "The XML plant was still written.",
        )
    try:
        fdm = jsbsim.FGFDMExec(None)
        setter = getattr(fdm, "set_root_dir", None)
        if callable(setter):
            setter(str(root))
        if hasattr(fdm, "set_aircraft_path"):
            fdm.set_aircraft_path(str(root))
        if hasattr(fdm, "set_engine_path"):
            fdm.set_engine_path(str(root / "engine"))
        if hasattr(fdm, "set_systems_path"):
            fdm.set_systems_path(str(root))
        loaded = fdm.load_model(model)
        if hasattr(fdm, "run_ic"):
            fdm.run_ic()
        if loaded is False:
            return False, "jsbsim.FGFDMExec.load_model returned false."
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _try_import_jsbsim() -> Any | None:
    try:
        import jsbsim  # type: ignore
    except Exception:
        return None
    return jsbsim


def _fdm_xml(results: dict[str, Any]) -> str:
    area = float(results.get("reference_area_m2") or 0.09)
    span = float(results.get("span_m") or 0.5)
    chord = float(results.get("chord_m") or 0.09)
    mass = float(results.get("mass_kg") or 0.1496)
    alpha = float(results.get("alpha_deg") or 0.0)
    cl = float(results.get("CL") or 0.0)
    cd = float(results.get("CD") or 0.0)
    clalpha = float(results.get("CLalpha") or 0.0)
    cmalpha = float(results.get("Cmalpha") or 0.0)
    cl0 = cl - clalpha * math.radians(alpha)
    ref = list(results.get("xyz_ref") or [0.25 * chord, 0.0, 0.0])
    ixx = mass * (span**2) / 12.0
    iyy = mass * ((4.0 * chord) ** 2 + (2.0 * (ref[2] or 0.05)) ** 2) / 12.0
    izz = ixx + iyy
    return f"""<?xml version="1.0"?>
<fdm_config name="VibeCADAero" version="2.0" release="ALPHA"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:noNamespaceSchemaLocation="http://jsbsim.sourceforge.net/JSBSim.xsd">
  <fileheader>
    <author>VibeCAD Aero</author>
    <description>6DOF plant generated from VibeCAD Aero coefficients (not CFD).</description>
  </fileheader>
  <metrics>
    <wingarea unit="M2">{area:.6f}</wingarea>
    <wingspan unit="M">{span:.6f}</wingspan>
    <chord unit="M">{chord:.6f}</chord>
    <location name="AERORP" unit="M">
      <x>{ref[0]:.6f}</x>
      <y>{ref[1]:.6f}</y>
      <z>{ref[2]:.6f}</z>
    </location>
    <location name="EYEPOINT" unit="M">
      <x>{ref[0]:.6f}</x>
      <y>0.0</y>
      <z>{ref[2] + 0.05:.6f}</z>
    </location>
    <location name="VRP" unit="M">
      <x>0.0</x>
      <y>0.0</y>
      <z>0.0</z>
    </location>
  </metrics>
  <mass_balance>
    <ixx unit="KG*M2">{ixx:.8f}</ixx>
    <iyy unit="KG*M2">{iyy:.8f}</iyy>
    <izz unit="KG*M2">{izz:.8f}</izz>
    <ixy unit="KG*M2">0.0</ixy>
    <ixz unit="KG*M2">0.0</ixz>
    <iyz unit="KG*M2">0.0</iyz>
    <emptywt unit="KG">{mass:.6f}</emptywt>
    <location name="CG" unit="M">
      <x>{ref[0]:.6f}</x>
      <y>{ref[1]:.6f}</y>
      <z>{ref[2]:.6f}</z>
    </location>
  </mass_balance>
  <ground_reactions>
    <contact type="BOGEY" name="skid">
      <location unit="M">
        <x>0.0</x>
        <y>0.0</y>
        <z>-0.02</z>
      </location>
      <static_friction>0.8</static_friction>
      <dynamic_friction>0.5</dynamic_friction>
      <rolling_friction>0.02</rolling_friction>
      <spring_coeff unit="N/M">200</spring_coeff>
      <damping_coeff unit="N/M/SEC">20</damping_coeff>
      <max_steer unit="DEG">0.0</max_steer>
      <brake_group>NONE</brake_group>
      <retractable>0</retractable>
    </contact>
  </ground_reactions>
  <propulsion>
    <engine file="electric">
      <feed>0</feed>
      <thruster file="direct">
        <location unit="M">
          <x>{-0.05:.6f}</x>
          <y>0.0</y>
          <z>{ref[2]:.6f}</z>
        </location>
        <orient unit="DEG">
          <roll>0.0</roll>
          <pitch>0.0</pitch>
          <yaw>0.0</yaw>
        </orient>
      </thruster>
    </engine>
    <tank type="FUEL">
      <location unit="M">
        <x>{ref[0]:.6f}</x>
        <y>0.0</y>
        <z>{ref[2]:.6f}</z>
      </location>
      <capacity unit="LBS">0.02</capacity>
      <contents unit="LBS">0.02</contents>
    </tank>
  </propulsion>
  <flight_control name="VibeCAD stub"/>
  <aerodynamics>
    <axis name="LIFT">
      <function name="aero/force/Lift">
        <description>CL0 + CLalpha * alpha</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <sum>
            <value>{cl0:.6f}</value>
            <product>
              <value>{clalpha:.6f}</value>
              <property>aero/alpha-rad</property>
            </product>
          </sum>
        </product>
      </function>
    </axis>
    <axis name="DRAG">
      <function name="aero/force/Drag">
        <description>Constant CD from the Aero solve</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <value>{cd:.6f}</value>
        </product>
      </function>
    </axis>
    <axis name="PITCH">
      <function name="aero/moment/Pitch">
        <description>Cmalpha * alpha</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/cbarw-ft</property>
          <product>
            <value>{cmalpha:.6f}</value>
            <property>aero/alpha-rad</property>
          </product>
        </product>
      </function>
    </axis>
  </aerodynamics>
</fdm_config>
"""


def _electric_engine_xml(results: dict[str, Any]) -> str:
    power = max(float(results.get("P_hover") or 20.0), 10.0)
    return f"""<?xml version="1.0"?>
<electric_engine name="electric">
  <power unit="WATTS">{power:.3f}</power>
</electric_engine>
"""


def _direct_thruster_xml() -> str:
    return """<?xml version="1.0"?>
<direct name="direct">
</direct>
"""
