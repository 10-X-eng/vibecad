from pathlib import Path

from AeroCFDContracts import (
    AeroCase, Artifact, ComputeSpec, FlowConditions, GeometryArtifact,
    ReferenceQuantities, SolverSpec, Vector3,
)
from AeroLBM import FluidX3DBackend


def _case(tmp_path: Path, executable: str | None = None) -> AeroCase:
    stl = tmp_path / "g.stl"
    stl.write_text("solid g\nendsolid g\n", encoding="utf-8")
    settings = {"geometry_physical_size_m": 1.0}
    if executable is not None:
        settings["executable"] = executable
    return AeroCase(
        case_id="vendor-policy",
        geometry=GeometryArtifact(
            artifact=Artifact.from_file(stl, media_type="model/stl", role="solver_geometry"),
            geometry_revision="r1",
        ),
        flow=FlowConditions(Vector3(10, 0, 0), 1.225, 1.81e-5),
        references=ReferenceQuantities(1.0, 1.0, 1.0),
        solver=SolverSpec("fluidx3d", "fluidx3d", settings=settings),
        compute=ComputeSpec(),
    )


def test_default_resolves_vendored_bridge(tmp_path):
    vendor = tmp_path / "vendor" / "FluidX3D"
    bridge = vendor / "bin" / "VibeCADFluidX3D"
    bridge.parent.mkdir(parents=True)
    bridge.write_text("bridge", encoding="utf-8")
    backend = FluidX3DBackend(vendor_root=vendor)
    assert backend._resolve_executable(_case(tmp_path)) == bridge.resolve()


def test_explicit_external_bridge_overrides_vendored_bridge(tmp_path):
    vendor = tmp_path / "vendor" / "FluidX3D"
    vendored = vendor / "bin" / "VibeCADFluidX3D"
    vendored.parent.mkdir(parents=True)
    vendored.write_text("bridge", encoding="utf-8")
    external = tmp_path / "external" / "VibeCADFluidX3D"
    external.parent.mkdir(parents=True)
    external.write_text("bridge", encoding="utf-8")
    backend = FluidX3DBackend(vendor_root=vendor)
    assert backend._resolve_executable(_case(tmp_path, str(external))) == external.resolve()
