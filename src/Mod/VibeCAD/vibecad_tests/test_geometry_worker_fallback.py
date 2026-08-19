# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from VibeCADGeometryInspection import complete_geometry_read


REPO = Path(__file__).resolve().parents[4]


class _Vector:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


class _BoundBox:
    def __init__(
        self,
        minimum: tuple[float, float, float],
        maximum: tuple[float, float, float],
    ) -> None:
        self.XMin, self.YMin, self.ZMin = (float(value) for value in minimum)
        self.XMax, self.YMax, self.ZMax = (float(value) for value in maximum)
        self.XLength = self.XMax - self.XMin
        self.YLength = self.YMax - self.YMin
        self.ZLength = self.ZMax - self.ZMin


class _Surface:
    def __init__(
        self,
        name: str,
        *,
        origin: tuple[float, float, float],
        axis: tuple[float, float, float],
        x_direction: tuple[float, float, float] | None = None,
        radius: float | None = None,
        reference_radius: float | None = None,
        semi_angle: float | None = None,
        major_radius: float | None = None,
        minor_radius: float | None = None,
    ) -> None:
        self.__name__ = name
        self.Position = _Vector(*origin)
        self.Center = _Vector(*origin)
        self.Location = _Vector(*origin)
        self.Axis = _Vector(*axis)
        self.XAxis = _Vector(*(x_direction or (1.0, 0.0, 0.0)))
        if radius is not None:
            self.Radius = float(radius)
        if reference_radius is not None:
            self.RefRadius = float(reference_radius)
        if semi_angle is not None:
            self.SemiAngle = float(semi_angle)
        if major_radius is not None:
            self.MajorRadius = float(major_radius)
        if minor_radius is not None:
            self.MinorRadius = float(minor_radius)

    def __class_getitem__(cls, _item):  # pragma: no cover - not used
        return cls


class _NamedType(_Surface):
    pass


class _Face:
    def __init__(
        self,
        *,
        geometry_type: str,
        origin: tuple[float, float, float],
        axis: tuple[float, float, float],
        normal: tuple[float, float, float],
        center: tuple[float, float, float],
        area: float,
        bounds: tuple[tuple[float, float, float], tuple[float, float, float]],
        radius: float | None = None,
        x_direction: tuple[float, float, float] = (1.0, 0.0, 0.0),
        orientation: str = "Forward",
        edge_count: int = 4,
        wire_count: int = 1,
    ) -> None:
        surface_type = type(geometry_type, (_NamedType,), {})
        self.Surface = surface_type(
            geometry_type,
            origin=origin,
            axis=axis,
            x_direction=x_direction,
            radius=radius,
        )
        self.Orientation = orientation
        self.Area = float(area)
        self.CenterOfMass = _Vector(*center)
        self.BoundBox = _BoundBox(*bounds)
        self.Edges = [object()] * edge_count
        self.Wires = [object()] * wire_count
        self.ParameterRange = (0.0, 1.0, 0.0, 1.0)
        self._normal = _Vector(*normal)

    def normalAt(self, _u: float, _v: float) -> _Vector:
        return self._normal


class _Edge:
    def __init__(
        self,
        *,
        geometry_type: str,
        origin: tuple[float, float, float],
        axis: tuple[float, float, float],
        direction: tuple[float, float, float],
        center: tuple[float, float, float],
        length: float,
        bounds: tuple[tuple[float, float, float], tuple[float, float, float]],
        endpoints: list[tuple[float, float, float]],
        radius: float | None = None,
        closed: bool = False,
        orientation: str = "Forward",
    ) -> None:
        curve_type = type(geometry_type, (_NamedType,), {})
        self.Curve = curve_type(
            geometry_type,
            origin=origin,
            axis=axis,
            radius=radius,
        )
        self.Orientation = orientation
        self.Length = float(length)
        self.CenterOfMass = _Vector(*center)
        self.BoundBox = _BoundBox(*bounds)
        self.Vertexes = [SimpleNamespace(Point=_Vector(*point)) for point in endpoints]
        self.ParameterRange = (0.0, 1.0)
        self._direction = _Vector(*direction)
        self._closed = closed

    def tangentAt(self, _parameter: float) -> _Vector:
        return self._direction

    def isClosed(self) -> bool:
        return self._closed


class _Shape:
    def __init__(
        self,
        *,
        shape_type: str = "Solid",
        solids: int = 1,
        shells: int = 1,
        faces: list[_Face] | None = None,
        wires: int = 6,
        edges: list[_Edge] | None = None,
        vertices: int = 8,
        volume: float = 6000.0,
        area: float = 2200.0,
        length: float = 240.0,
        center: tuple[float, float, float] = (5.0, 10.0, 15.0),
        bounds: tuple[tuple[float, float, float], tuple[float, float, float]] = (
            (0.0, 0.0, 0.0),
            (10.0, 20.0, 30.0),
        ),
        valid: bool = True,
        distance: float = 2.0,
    ) -> None:
        self.ShapeType = shape_type
        self.Solids = [object()] * solids
        self.Shells = [object()] * shells
        self.Faces = list(faces or [])
        self.Wires = [object()] * wires
        self.Edges = list(edges or [])
        self.Vertexes = [object()] * vertices
        self.Volume = float(volume)
        self.Area = float(area)
        self.Length = float(length)
        self.CenterOfMass = _Vector(*center)
        self.BoundBox = _BoundBox(*bounds)
        self._valid = valid
        self._distance = distance

    def isNull(self) -> bool:
        return False

    def isValid(self) -> bool:
        return self._valid

    def distToShape(self, _other: object) -> tuple[float, list[tuple[_Vector, _Vector]], list]:
        return (
            self._distance,
            [(_Vector(0.0, 0.0, 0.0), _Vector(self._distance, 0.0, 0.0))],
            [],
        )


def _box_shape() -> _Shape:
    faces = [
        _Face(
            geometry_type="Plane",
            origin=(0.0, 0.0, 30.0),
            axis=(0.0, 0.0, 1.0),
            normal=(0.0, 0.0, 1.0),
            center=(5.0, 10.0, 30.0),
            area=200.0,
            bounds=((0.0, 0.0, 30.0), (10.0, 20.0, 30.0)),
        ),
        _Face(
            geometry_type="Plane",
            origin=(0.0, 0.0, 0.0),
            axis=(0.0, 0.0, -1.0),
            normal=(0.0, 0.0, -1.0),
            center=(5.0, 10.0, 0.0),
            area=200.0,
            bounds=((0.0, 0.0, 0.0), (10.0, 20.0, 0.0)),
        ),
    ]
    while len(faces) < 6:
        faces.append(
            _Face(
                geometry_type="Plane",
                origin=(0.0, 0.0, 0.0),
                axis=(1.0, 0.0, 0.0),
                normal=(1.0, 0.0, 0.0),
                center=(10.0, 10.0, 15.0),
                area=600.0,
                bounds=((10.0, 0.0, 0.0), (10.0, 20.0, 30.0)),
            )
        )
    edges = [
        _Edge(
            geometry_type="Line",
            origin=(0.0, 0.0, 0.0),
            axis=(1.0, 0.0, 0.0),
            direction=(1.0, 0.0, 0.0),
            center=(5.0, 0.0, 0.0),
            length=10.0,
            bounds=((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            endpoints=[(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)],
        )
        for _ in range(12)
    ]
    return _Shape(faces=faces, edges=edges)


def _cylinder_shape() -> _Shape:
    faces = [
        _Face(
            geometry_type="Plane",
            origin=(0.0, 0.0, 12.0),
            axis=(0.0, 0.0, 1.0),
            normal=(0.0, 0.0, 1.0),
            center=(0.0, 0.0, 12.0),
            area=50.265482,
            bounds=((-4.0, -4.0, 12.0), (4.0, 4.0, 12.0)),
        ),
        _Face(
            geometry_type="Cylinder",
            origin=(0.0, 0.0, 0.0),
            axis=(0.0, 0.0, 1.0),
            normal=(1.0, 0.0, 0.0),
            center=(0.0, 0.0, 6.0),
            area=301.592895,
            bounds=((-4.0, -4.0, 0.0), (4.0, 4.0, 12.0)),
            radius=4.0,
        ),
        _Face(
            geometry_type="Plane",
            origin=(0.0, 0.0, 0.0),
            axis=(0.0, 0.0, -1.0),
            normal=(0.0, 0.0, -1.0),
            center=(0.0, 0.0, 0.0),
            area=50.265482,
            bounds=((-4.0, -4.0, 0.0), (4.0, 4.0, 0.0)),
        ),
    ]
    edges = [
        _Edge(
            geometry_type="Circle",
            origin=(0.0, 0.0, 12.0),
            axis=(0.0, 0.0, 1.0),
            direction=(1.0, 0.0, 0.0),
            center=(0.0, 0.0, 12.0),
            length=25.132741,
            bounds=((-4.0, -4.0, 12.0), (4.0, 4.0, 12.0)),
            endpoints=[],
            radius=4.0,
            closed=True,
        ),
        _Edge(
            geometry_type="Circle",
            origin=(0.0, 0.0, 0.0),
            axis=(0.0, 0.0, 1.0),
            direction=(1.0, 0.0, 0.0),
            center=(0.0, 0.0, 0.0),
            length=25.132741,
            bounds=((-4.0, -4.0, 0.0), (4.0, 4.0, 0.0)),
            endpoints=[],
            radius=4.0,
            closed=True,
        ),
    ]
    return _Shape(
        faces=faces,
        edges=edges,
        wires=3,
        vertices=0,
        volume=603.185789,
        area=402.123859,
        length=50.265482,
        center=(0.0, 0.0, 6.0),
        bounds=((-4.0, -4.0, 0.0), (4.0, 4.0, 12.0)),
    )


def _inspect_request(
    tmp_path: Path,
    *,
    analysis_level: str = "full",
    max_subelements: int = 0,
    queries: list[dict] | None = None,
) -> tuple[Path, Path]:
    shape_path = tmp_path / "shape.brep"
    shape_path.write_text("CASCADED-BREP", encoding="utf-8")
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(
        json.dumps(
            {
                "schema": "vibecad-geometry-job-v1",
                "operation": "inspect_brep",
                "shape": {"format": "brep", "path": str(shape_path)},
                "max_subelements": max_subelements,
                "queries": queries or [],
                "analysis_level": analysis_level,
                "result_path": str(result_path),
                "deadline_ms": 5000,
            }
        ),
        encoding="utf-8",
    )
    return request_path, result_path


def _missing_worker() -> Path:
    raise RuntimeError(
        "The VibeCAD geometry worker is missing from /missing/bin. "
        "Rebuild or reinstall VibeCAD."
    )


def test_cmake_installs_geometry_fallback_with_other_scripts() -> None:
    cmake = (REPO / "src" / "Mod" / "VibeCAD" / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    geometry = cmake.index("VibeCADGeometry.py")
    fallback = cmake.index("VibeCADGeometryFallback.py")
    runner = cmake.index("VibeCADGeometryFallbackRunner.py")
    inspection = cmake.index("VibeCADGeometryInspection.py")
    assert geometry < fallback < runner < inspection


def test_freecadcmd_runner_stamps_isolated_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADGeometryFallbackRunner as runner

    result_path = tmp_path / "result.json"
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema": "vibecad-geometry-job-v1",
                "operation": "validate_brep",
                "result_path": str(result_path),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner,
        "execute_request",
        lambda _request: {
            "ok": False,
            "failure_stage": "in_process_fallback",
        },
    )

    result = runner.run(request_path)

    assert result["execution_mode"] == "isolated_freecadcmd_fallback"
    assert result["failure_stage"] == "fallback_process"
    assert json.loads(result_path.read_text(encoding="utf-8")) == result


def test_worker_executable_still_raises_when_binary_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADGeometry as geometry

    monkeypatch.setattr(
        geometry,
        "App",
        SimpleNamespace(getHomePath=lambda: "/empty-install"),
        raising=False,
    )
    monkeypatch.setattr("VibeCADGeometry.sys.platform", "linux")
    monkeypatch.setitem(
        __import__("sys").modules,
        "FreeCAD",
        SimpleNamespace(getHomePath=lambda: "/empty-install"),
    )

    with pytest.raises(RuntimeError, match="geometry worker is missing"):
        geometry.worker_executable()


def test_execute_job_uses_isolated_freecadcmd_fallback_when_worker_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADGeometry as geometry

    request_path, result_path = _inspect_request(tmp_path)
    command = tmp_path / "FreeCADCmd"
    runner = tmp_path / "VibeCADGeometryFallbackRunner.py"
    popen_calls: list[object] = []

    class _Input:
        def __init__(self):
            self.text = ""

        def write(self, value):
            self.text += value

        def flush(self):
            return None

        def close(self):
            return None

    class _Process:
        returncode = 0

        def __init__(self):
            self.input_stream = _Input()
            self.stdin = self.input_stream

        def poll(self) -> int:
            return 0

        def communicate(self) -> tuple[str, str]:
            return ("", "")

    process = _Process()

    def popen(args, **kwargs):
        popen_calls.append((args, kwargs))
        result_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "operation": "inspect_brep",
                    "geometry": {"solids": 1, "faces": 6, "edges": 12},
                }
            ),
            encoding="utf-8",
        )
        return process

    monkeypatch.setattr(geometry, "worker_executable", _missing_worker)
    monkeypatch.setattr(
        geometry,
        "fallback_command",
        lambda: (command, runner),
        raising=False,
    )
    monkeypatch.setattr(geometry.subprocess, "Popen", popen)

    result = geometry.execute_job(request_path, result_path)

    assert len(popen_calls) == 1
    assert popen_calls[0][0] == [str(command), "-c"]
    assert str(runner).replace("\\", "\\\\") in process.input_stream.text
    assert str(request_path).replace("\\", "\\\\") in process.input_stream.text
    assert result["ok"] is True
    assert result["operation"] == "inspect_brep"
    assert result["execution_mode"] == "isolated_freecadcmd_fallback"


def test_freecadcmd_fallback_can_be_cancelled_while_geometry_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADGeometry as geometry

    request_path, result_path = _inspect_request(tmp_path)
    command = tmp_path / "FreeCADCmd"
    runner = tmp_path / "VibeCADGeometryFallbackRunner.py"

    class _Process:
        returncode = -1
        pid = 123
        terminated = False

        class _Input:
            def write(self, _value):
                return None

            def flush(self):
                return None

            def close(self):
                return None

        def __init__(self):
            self.stdin = self._Input()

        def poll(self):
            return self.returncode if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.terminated = True
            return self.returncode

        def kill(self):
            self.terminated = True

        def communicate(self):
            return ("", "")

    process = _Process()
    monkeypatch.setattr(geometry, "worker_executable", _missing_worker)
    monkeypatch.setattr(
        geometry,
        "fallback_command",
        lambda: (command, runner),
        raising=False,
    )
    monkeypatch.setattr(geometry.subprocess, "Popen", lambda *_args, **_kwargs: process)

    result = geometry.execute_job(
        request_path,
        result_path,
        cancellation_check=lambda: True,
    )

    assert result["ok"] is False
    assert result["failure_code"] == "RUN_CANCELLED"
    assert result["failure_stage"] == "external_process"
    assert process.terminated is True


def test_execute_job_uses_isolated_worker_when_binary_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADGeometry as geometry
    import VibeCADGeometryFallback as fallback

    request_path, result_path = _inspect_request(tmp_path)
    worker = tmp_path / "VibeCADGeometryWorker"
    fallback_calls: list[object] = []

    class _Process:
        returncode = 0

        def poll(self) -> int:
            return 0

        def communicate(self) -> tuple[str, str]:
            return ("", "")

    def popen(args, **_kwargs):
        assert args[0] == str(worker)
        result_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "operation": "inspect_brep",
                    "execution_mode": "isolated_geometry_worker",
                    "geometry": {"solids": 1, "faces": 6, "edges": 12},
                }
            ),
            encoding="utf-8",
        )
        return _Process()

    monkeypatch.setattr(geometry, "worker_executable", lambda: worker)
    monkeypatch.setattr(geometry.subprocess, "Popen", popen)
    monkeypatch.setattr(
        fallback,
        "execute_request",
        lambda request: fallback_calls.append(request) or {"ok": False},
    )

    result = geometry.execute_job(request_path, result_path)

    assert fallback_calls == []
    assert result["ok"] is True
    assert result["execution_mode"] == "isolated_geometry_worker"
    assert result["geometry"]["faces"] == 6


def test_inspect_brep_fallback_omits_mass_properties_for_topology(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADGeometryFallback as fallback

    monkeypatch.setattr(fallback, "load_brep", lambda _path: _box_shape())
    request_path, _result_path = _inspect_request(
        tmp_path,
        analysis_level="topology",
        max_subelements=2,
    )
    request = json.loads(request_path.read_text(encoding="utf-8"))

    result = fallback.execute_request(request)

    assert result["ok"] is True
    facts = result["geometry"]
    assert facts["analysis_level"] == "topology"
    assert facts["faces"] == 6
    assert facts["bounds_mm"]["min"] == [0.0, 0.0, 0.0]
    assert len(facts["face_details"]) == 2
    assert "volume_mm3" not in facts
    assert "area_mm2" not in facts
    assert "valid" not in facts


def test_inspect_brep_fallback_answers_face_and_edge_queries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADGeometryFallback as fallback

    monkeypatch.setattr(fallback, "load_brep", lambda _path: _cylinder_shape())
    request_path, _result_path = _inspect_request(
        tmp_path,
        queries=[
            {
                "name": "top_face",
                "element_type": "face",
                "geometry_type": "Plane",
                "normal": [0.0, 0.0, 1.0],
                "expected_count": 1,
                "max_results": 16,
            },
            {
                "name": "shaft_surface",
                "element_type": "face",
                "geometry_type": "Cylinder",
                "axis_direction": [0.0, 0.0, 1.0],
                "radius_mm": 4.0,
                "expected_count": 1,
                "max_results": 16,
            },
            {
                "name": "rim_edges",
                "element_type": "edge",
                "geometry_type": "Circle",
                "axis_direction": [0.0, 0.0, 1.0],
                "radius_mm": 4.0,
                "expected_count": 2,
                "max_results": 16,
            },
        ],
    )
    request = json.loads(request_path.read_text(encoding="utf-8"))

    result = fallback.execute_request(request)

    assert result["ok"] is True
    by_name = {item["name"]: item for item in result["geometry"]["query_results"]}
    assert by_name["top_face"]["cardinality_ok"] is True
    assert by_name["shaft_surface"]["cardinality_ok"] is True
    assert by_name["rim_edges"]["cardinality_ok"] is True
    shaft = by_name["shaft_surface"]["matches"][0]
    assert shaft["radius_mm"] == 4.0
    assert shaft["axis_direction"] == [0.0, 0.0, 1.0]
    assert shaft["origin_mm"] == [0.0, 0.0, 0.0]


def test_validate_brep_and_minimum_distance_fallback_schemas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADGeometryFallback as fallback

    first = tmp_path / "first.brep"
    second = tmp_path / "second.brep"
    first.write_text("A", encoding="utf-8")
    second.write_text("B", encoding="utf-8")
    monkeypatch.setattr(fallback, "load_brep", lambda _path: _box_shape())

    validation = fallback.execute_request(
        {
            "schema": "vibecad-geometry-job-v1",
            "operation": "validate_brep",
            "shape": {"format": "brep", "path": str(first)},
        }
    )
    distance = fallback.execute_request(
        {
            "schema": "vibecad-geometry-job-v1",
            "operation": "minimum_distance",
            "first": {"format": "brep", "path": str(first)},
            "second": {"format": "brep", "path": str(second)},
        }
    )

    assert validation["ok"] is True
    assert validation["valid"] is True
    assert validation["brep"]["valid"] is True
    assert validation["bop"]["performed"] is False
    assert distance["ok"] is True
    assert distance["distance"] == 2.0
    assert distance["closest_point_pairs"] == [
        {"first": [0.0, 0.0, 0.0], "second": [2.0, 0.0, 0.0]}
    ]


def test_complete_geometry_read_succeeds_when_worker_binary_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADGeometry as geometry
    import VibeCADGeometryFallback as fallback

    artifact_directory = tmp_path / "captured-brep"
    artifact_directory.mkdir()
    (artifact_directory / "shape.brep").write_text("detached box", encoding="utf-8")
    monkeypatch.setattr(geometry, "worker_executable", _missing_worker)
    monkeypatch.setattr(fallback, "load_brep", lambda _path: _box_shape())

    def run_fallback(request_path, result_path, **_kwargs):
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        result = fallback.execute_request(request)
        result["execution_mode"] = "isolated_freecadcmd_fallback"
        Path(result_path).write_text(json.dumps(result), encoding="utf-8")
        return result

    monkeypatch.setattr(geometry, "_execute_job_fallback_process", run_fallback)

    result = complete_geometry_read(
        {
            "artifact_directory": str(artifact_directory),
            "shape_path": str(artifact_directory / "shape.brep"),
            "shape_hash": 42,
            "reference": {"document_uid": "document", "object_name": "Box"},
            "object": {"name": "Box"},
            "placement": None,
            "include_subelements": True,
            "max_subelements": 3,
            "queries": [
                {
                    "name": "top",
                    "element_type": "face",
                    "geometry_type": "Plane",
                    "normal": [0.0, 0.0, 1.0],
                    "max_results": 16,
                }
            ],
            "analysis_level": "full",
        }
    )

    assert result["ok"] is True
    assert result["tool"] == "vibescript.read_geometry"
    assert result["execution"]["mode"] == "isolated_freecadcmd_fallback"
    facts = result["geometry"]
    assert facts["solids"] == 1
    assert facts["faces"] == 6
    assert facts["edges"] == 12
    assert facts["volume_mm3"] == 6000.0
    assert facts["area_mm2"] == 2200.0
    assert facts["bounds_mm"]["size"] == [10.0, 20.0, 30.0]
    assert len(facts["face_details"]) == 3
    top = facts["query_results"][0]["matches"][0]
    assert top["source_selector"]["geometry_type"] == "Plane"
    assert top["sketch_placement"]["normal"] == [0.0, 0.0, 1.0]
    assert not artifact_directory.exists()
