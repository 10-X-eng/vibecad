from AeroStripTheory import StripWing


def test_semi_span_mirroring_is_explicit():
    wing = StripWing([0.0, 1.0], [1.0, 1.0], mirror=True)
    assert wing.area_m2 == 2.0
    half = StripWing([0.0, 1.0], [1.0, 1.0], mirror=False)
    assert half.area_m2 == 1.0
