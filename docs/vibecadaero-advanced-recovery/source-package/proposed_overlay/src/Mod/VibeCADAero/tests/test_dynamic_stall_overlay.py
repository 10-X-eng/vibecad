import math
import numpy as np

from AeroDynamicStall import AirfoilDynamicParams, DynamicStallEngineeringModel, VectorizedDynamicStallEngineeringModel


def test_scalar_vectorized_same_equations():
    params = [AirfoilDynamicParams(chord_m=0.2), AirfoilDynamicParams(chord_m=0.3)]
    scalar = [DynamicStallEngineeringModel(p) for p in params]
    vector = VectorizedDynamicStallEngineeringModel(params)
    alphas = np.radians(np.array([5.0, 7.0]))
    for m, a in zip(scalar, alphas):
        m.reset(alpha_rad=float(a))
    vector.reset(alphas)
    for step in range(20):
        a = alphas + math.radians(0.1 * step)
        scalar_out = [
            m.step(alpha_rad=float(ai), pitch_rate_rad_s=0.2, speed_mps=15.0, dt_s=0.002)
            for m, ai in zip(scalar, a)
        ]
        vo = vector.step(alpha_rad=a, pitch_rate_rad_s=0.2, speed_mps=15.0, dt_s=0.002)
        assert np.allclose([o.cl for o in scalar_out], vo["cl"], rtol=1e-12, atol=1e-12)
        assert np.allclose([o.cd for o in scalar_out], vo["cd"], rtol=1e-12, atol=1e-12)
        assert np.allclose([o.cm for o in scalar_out], vo["cm"], rtol=1e-12, atol=1e-12)
