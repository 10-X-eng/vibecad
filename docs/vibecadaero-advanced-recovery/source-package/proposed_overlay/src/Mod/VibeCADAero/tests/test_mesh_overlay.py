from AeroMesh import subdivide_tri6


def test_quadratic_triangle_subdivision():
    tris = subdivide_tri6([0, 1, 2, 3, 4, 5])
    assert tris == [[0, 3, 5], [3, 1, 4], [5, 4, 2], [3, 4, 5]]
