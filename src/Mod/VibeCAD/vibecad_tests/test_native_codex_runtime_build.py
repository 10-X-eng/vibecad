# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise the actual CMake target and installer against a verified local cache.

Set VIBECAD_CODEX_RUNTIME_TEST_CACHE to the release download cache to enable
runtime tests. Tests never download: an unexpected curl invocation fails.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.skipif(os.name != "posix", reason="Native Unix target")


@pytest.fixture
def project(tmp_path, monkeypatch):
    if not shutil.which("cmake") or not shutil.which("ninja"):
        pytest.skip("CMake and Ninja required")
    source = tmp_path / "source"
    source.mkdir()
    installer = Path("package/rattler-build/scripts/install_vibecad_codex_runtime.sh")
    (source / installer).parent.mkdir(parents=True)
    shutil.copy2(ROOT / installer, source / installer)
    shutil.copy2(ROOT / "src/Mod/VibeCAD/VibeCADCodex.py", source / "VibeCADCodex.py")
    cmake = (ROOT / "src/Mod/VibeCAD/CMakeLists.txt").read_text()
    start = cmake.index("option(VIBECAD_INSTALL_CODEX_RUNTIME")
    end = cmake.index("\ninstall(\n    FILES\n        ${VibeCAD_Scripts}", start)
    (source / "CMakeLists.txt").write_text(
        'cmake_minimum_required(VERSION 3.22)\nproject(RuntimeTest NONE)\n'
        'add_custom_target(VibeCADScripts ALL)\n' + cmake[start:end]
    )
    no_network = tmp_path / "no-network"
    no_network.mkdir()
    curl = no_network / "curl"
    curl.write_text('#!/bin/sh\necho "Unexpected download in runtime test" >&2\nexit 42\n')
    curl.chmod(0o755)
    monkeypatch.setenv("PATH", str(no_network) + os.pathsep + os.environ["PATH"])
    build = tmp_path / "build"

    def run(*args):
        result = subprocess.run(args, text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def configure(enabled=True):
        run("cmake", "-G", "Ninja", "-S", str(source), "-B", str(build),
            "-DPython3_EXECUTABLE=" + sys.executable,
            "-DVIBECAD_INSTALL_CODEX_RUNTIME=" + ("ON" if enabled else "OFF"))

    return configure, run, build


@pytest.fixture
def installed(project, monkeypatch):
    cache = os.environ.get("VIBECAD_CODEX_RUNTIME_TEST_CACHE")
    if not cache:
        pytest.skip("Set VIBECAD_CODEX_RUNTIME_TEST_CACHE for offline runtime tests")
    monkeypatch.setenv("VIBECAD_DOWNLOAD_CACHE", cache)
    configure, run, build = project
    configure()
    run("cmake", "--build", str(build), "--parallel", "12")
    return run, build, build / "Mod/VibeCAD/codex_runtime"


def test_native_runtime_opt_out_does_not_install_or_download(project):
    configure, run, build = project
    configure(enabled=False)
    run("cmake", "--build", str(build), "--parallel", "12")
    assert not (build / "Mod/VibeCAD/codex_runtime").exists()


def test_fresh_runtime_is_installed_with_companions(installed, tmp_path):
    run, build, runtime = installed
    assert "0.154.0" in run(str(runtime / "bin/codex-app-server"), "--version")
    assert (runtime / "bin/codex-code-mode-host").is_file()
    prefix = tmp_path / "installed"
    run("cmake", "--install", str(build), "--prefix", str(prefix))
    packaged = prefix / "Mod/VibeCAD/codex_runtime"
    assert "0.154.0" in run(str(packaged / "bin/codex-app-server"), "--version")
    assert (packaged / "bin/codex-code-mode-host").is_file()


@pytest.mark.parametrize("missing", ["bin/codex-app-server", "bin/codex-code-mode-host"])
def test_rebuild_repairs_missing_binary_despite_current_manifest(installed, missing):
    run, build, runtime = installed
    (runtime / missing).unlink()
    run("cmake", "--build", str(build), "--parallel", "12")
    assert (runtime / missing).is_file()


def test_rebuild_repairs_stale_version_metadata_with_newer_mtime(installed):
    run, build, runtime = installed
    manifest = runtime / "runtime.json"
    metadata = json.loads(manifest.read_text())
    metadata["version"] = "0.144.5"
    manifest.write_text(json.dumps(metadata))
    run("cmake", "--build", str(build), "--parallel", "12")
    assert json.loads(manifest.read_text())["version"] == "0.154.0"


def test_rebuild_replaces_old_executable_even_with_current_metadata(installed):
    run, build, runtime = installed
    binary = runtime / "bin/codex-app-server"
    binary.write_text('#!/bin/sh\nprintf "codex-app-server 0.144.5\\n"\n')
    binary.chmod(0o755)
    assert "0.144.5" in run(str(binary), "--version")
    run("cmake", "--build", str(build), "--parallel", "12")
    assert "0.154.0" in run(str(binary), "--version")


def test_current_runtime_is_verified_without_replacing_binaries(installed):
    run, build, runtime = installed
    binary = runtime / "bin/codex-app-server"
    before = binary.stat().st_mtime_ns
    output = run("cmake", "--build", str(build), "--parallel", "12")
    assert "runtime is current" in output
    assert binary.stat().st_mtime_ns == before
