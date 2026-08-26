import pytest
from AeroGeometryReadiness import GeometryReadiness, assessed


def test_exact_brep_does_not_imply_cfd_readiness():
    evidence = assessed(GeometryReadiness.BREP_ACCEPTED, checks=["brep_loaded"])
    with pytest.raises(ValueError):
        evidence.require(GeometryReadiness.SURFACE_WATERTIGHT)


def test_solver_input_ready_is_explicit():
    evidence = assessed(GeometryReadiness.SOLVER_INPUT_FROZEN, checks=["watertight", "mesh_valid", "input_hashed"])
    evidence.require(GeometryReadiness.MESH_READY)
