import numpy as np

from AeroSixDOF import ForceMomentBody, RigidBodyProperties, SixDOFReferenceSimulator


class ZeroProvider:
    def evaluate(self, state, controls, dt_s):
        return ForceMomentBody.zero()


def test_ned_gravity_moves_positive_down():
    sim = SixDOFReferenceSimulator(
        RigidBodyProperties(mass_kg=1.0, ix_kg_m2=1.0, iy_kg_m2=1.0, iz_kg_m2=1.0),
        ZeroProvider(),
    )
    sim.reset()
    rec = sim.step(0.1)
    assert sim.state.velocity_body_mps[2] > 0.0
    assert sim.state.position_ned_m[2] > 0.0
    assert abs(np.linalg.norm(sim.state.quaternion_bn) - 1.0) < 1e-12
