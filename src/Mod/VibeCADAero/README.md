# VibeCAD Aero

In-app aerodynamics workbench for the Voider-validated solver stack:

- NeuralFoil 2D viscous section (low Re, `model_size="large"`)
- AeroSandbox AeroBuildup + VortexLatticeMethod
- Momentum / actuator-disk hover power (not CFD)
- JSBSim 6DOF plant export

Use the **Aero** tab on the main ribbon (next to Parameters and Drawing).
The tab is Model plus Aero buttons; it does not replace the Model page.
Analyze defaults to repairing pitch-unstable geometry (tail volume, boom,
avionics CG, upper-wing stagger/decalage) and tells the user and assistant
what changed. User guide: [`docs/vibecad-aero.md`](../../../docs/vibecad-aero.md)

Release builds install these dependencies automatically into VibeCAD's bundled
Python: [`requirements-aero.txt`](requirements-aero.txt)
