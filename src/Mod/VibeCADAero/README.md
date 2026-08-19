# VibeCAD Aero

In-app aerodynamics workbench for the Voider-validated solver stack:

- NeuralFoil 2D viscous section (low Re, `model_size="large"`)
- AeroSandbox AeroBuildup + VortexLatticeMethod
- Momentum / actuator-disk hover power (not CFD)
- JSBSim 6DOF plant export

Use the **Aero** tab on the main ribbon (next to Parameters and Drawing).
The tab is Model plus Aero buttons; it does not replace the Model page.
Analyze writes results without moving CAD. **Propose repairs** creates a
one-shot preview, and **Apply repairs** commits that preview as one undoable
document change after verifying that its source geometry is still current.

CAD volume mass is reported as partial evidence by default, so flight-card
calculations continue to use declared AUW. Set `AeroConfig.cad_mass_complete`
only when every mass-carrying component is represented by the named CAD parts.

User guide: [`docs/vibecad-aero.md`](../../../docs/vibecad-aero.md)

Release builds install these dependencies automatically into VibeCAD's bundled
Python: [`requirements-aero.txt`](requirements-aero.txt)
