# SPDX-License-Identifier: LGPL-2.1-or-later

"""Linux packaging must treat aarch64/arm64 as a native target.

These source-contract tests read the real Linux bundle, Debian, pixi, Codex,
and README paths. They also drive the shipped ``build_deb_from_appdir.sh`` so
arch mapping is asserted without compiling FreeCAD.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from VibeCADUpdate import (
    ReleaseIdentity,
    UpdateAsset,
    UpdateRelease,
    normalize_architecture,
)

ROOT = Path(__file__).resolve().parents[4]
TOOLS = ROOT / "src" / "Tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from generate_update_manifest import _classify_asset, generate_manifest  # noqa: E402


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _write_minimal_appdir(appdir: Path) -> None:
    appdir.mkdir(parents=True, exist_ok=True)
    apprun = appdir / "AppRun"
    apprun.write_text("#!/bin/sh\nexec true\n", encoding="utf-8")
    apprun.chmod(apprun.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _build_deb(*, arch: str, version: str = "26.3.1") -> tuple[Path, str]:
    script = ROOT / "package" / "linux" / "build_deb_from_appdir.sh"
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        appdir = work / "AppDir"
        output_dir = work / "out"
        output_dir.mkdir()
        _write_minimal_appdir(appdir)
        completed = subprocess.run(
            [
                str(script),
                "--appdir",
                str(appdir),
                "--output-dir",
                str(output_dir),
                "--version",
                version,
                "--arch",
                arch,
                "--artifact-basename",
                "VibeCAD-26.3.1-build0",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        deb_path = Path(completed.stdout.strip().splitlines()[-1])
        assert deb_path.is_file(), completed.stdout
        copied = Path(tempfile.mkdtemp(prefix="vibecad-deb-")) / deb_path.name
        shutil.copy2(deb_path, copied)
        return copied, completed.stdout


def _dpkg_field(deb_path: Path, field: str) -> str:
    inspect = subprocess.run(
        ["dpkg-deb", "-I", str(deb_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    prefix = f"{field}:"
    for line in inspect.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.split(":", 1)[1].strip()
    raise AssertionError(f"{field} missing from {deb_path.name}:\n{inspect.stdout}")


def test_pixi_workspaces_list_linux_aarch64_and_linux_64() -> None:
    root_pixi = _source("pixi.toml")
    package_pixi = _source("package/rattler-build/pixi.toml")
    recipe = _source("package/rattler-build/recipe.yaml")

    for source in (root_pixi, package_pixi):
        assert '"linux-aarch64"' in source or "'linux-aarch64'" in source
        assert '"linux-64"' in source or "'linux-64'" in source
        assert "[target.linux-aarch64.dependencies]" in source or (
            "[feature.package.target.linux-aarch64.dependencies]" in source
        )
        assert "[target.linux-64.dependencies]" in source or (
            "[feature.package.target.linux-64.dependencies]" in source
        )

    assert "linux-aarch64" in root_pixi
    assert "[target.linux-aarch64.tasks]" in root_pixi
    assert "[target.linux-64.tasks]" in root_pixi
    assert "conda-linux-release" in root_pixi
    assert "if: linux and aarch64" in recipe
    assert "if: linux and x86_64" in recipe
    assert "sysroot_linux-aarch64" in recipe
    assert "sysroot_linux-64" in recipe


def test_linux_bundle_names_appimage_from_uname_m() -> None:
    linux = _source("package/rattler-build/linux/create_bundle.sh")

    assert 'version_name="${artifact_base}-Linux-$(uname -m)"' in linux
    assert 'appimagetool_path="./appimagetool-$(uname -m)"' in linux
    assert "appimagetool-$(uname -m).AppImage" in linux
    assert "Linux-x86_64" not in linux or "$(uname -m)" in linux
    assert "amd64" not in linux
    assert "cp ${conda_env}/bin_tmp/VibeCADGeometryWorker ${conda_env}/bin/" in linux
    assert "install_vibecad_codex_runtime.sh" in linux
    assert "${conda_env}/bin/freecadcmd" in linux
    assert "usr/bin/freecadcmd" in linux or "bin/freecadcmd" in linux


def test_codex_runtime_pins_linux_aarch64_and_x86_64() -> None:
    installer = _source("package/rattler-build/scripts/install_vibecad_codex_runtime.sh")

    assert "linux:aarch64|linux:arm64)" in installer
    assert "codex-app-server-package-aarch64-unknown-linux-musl.tar.gz" in installer
    assert "linux:x86_64|linux:amd64)" in installer
    assert "codex-app-server-package-x86_64-unknown-linux-musl.tar.gz" in installer


def test_deb_builder_maps_uname_to_debian_architecture() -> None:
    builder = _source("package/linux/build_deb_from_appdir.sh")

    assert 'arch="$(uname -m)"' in builder
    assert "x86_64|amd64)" in builder
    assert 'deb_arch="amd64"' in builder
    assert "aarch64|arm64)" in builder
    assert 'deb_arch="arm64"' in builder
    assert 'deb_path="$output_dir/${artifact_basename}-Linux-${deb_arch}.deb"' in builder


@pytest.mark.parametrize(
    ("host_arch", "debian_arch"),
    (
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
        ("x86_64", "amd64"),
        ("amd64", "amd64"),
    ),
)
def test_build_deb_from_appdir_emits_native_architecture_package(
    host_arch: str, debian_arch: str
) -> None:
    if shutil.which("dpkg-deb") is None:
        pytest.skip("dpkg-deb is required to drive the shipped Debian packager")

    deb_path, stdout = _build_deb(arch=host_arch)
    try:
        assert deb_path.name == f"VibeCAD-26.3.1-build0-Linux-{debian_arch}.deb"
        assert _dpkg_field(deb_path, "Architecture") == debian_arch
        assert _dpkg_field(deb_path, "Package") == "vibecad"
        assert debian_arch in stdout or deb_path.name in stdout
    finally:
        shutil.rmtree(deb_path.parent, ignore_errors=True)


def test_linux_install_docs_are_not_amd64_only() -> None:
    readme = _source("README.md")

    assert "amd64" in readme
    assert "arm64" in readme
    assert "x86_64" in readme or "amd64" in readme
    assert "aarch64" in readme or "arm64" in readme
    assert "vibecad_*_amd64.deb" not in readme
    assert "dpkg --print-architecture" in readme
    assert "Linux-$(dpkg --print-architecture).deb" in readme
    assert "Linux-$(uname -m).AppImage" in readme


def test_update_manifest_classifies_linux_aarch64_and_x86_64() -> None:
    assert _classify_asset("VibeCAD-26.3.1-build0-Linux-aarch64.AppImage") == (
        "linux",
        "aarch64",
        "appimage",
    )
    assert _classify_asset("VibeCAD-26.3.1-build0-Linux-x86_64.AppImage") == (
        "linux",
        "x86_64",
        "appimage",
    )
    assert _classify_asset("VibeCAD-26.3.1-build0-Linux-arm64.deb") == (
        "linux",
        "aarch64",
        "deb",
    )
    assert _classify_asset("VibeCAD-26.3.1-build0-Linux-amd64.deb") == (
        "linux",
        "x86_64",
        "deb",
    )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        assets = root / "assets"
        assets.mkdir()
        (root / "version.json").write_text(
            '{"name":"VibeCAD","version_major":26,"version_minor":3,'
            '"version_patch":1,"version_suffix":"","build_version":0}',
            encoding="utf-8",
        )
        (assets / "VibeCAD-26.3.1-build0-Linux-aarch64.AppImage").write_bytes(b"arm")
        (assets / "VibeCAD-26.3.1-build0-Linux-x86_64.AppImage").write_bytes(b"x86")
        (assets / "VibeCAD-26.3.1-build0-Linux-arm64.deb").write_bytes(b"deb-arm")
        (assets / "VibeCAD-26.3.1-build0-Linux-amd64.deb").write_bytes(b"deb-x86")

        manifest = generate_manifest(
            root,
            assets,
            repository="10-X-eng/vibecad",
            published_at="2026-09-12T00:00:00Z",
        )
        identities = {
            (asset["platform"], asset["architecture"], asset["kind"])
            for asset in manifest["assets"]
        }
        assert identities == {
            ("linux", "aarch64", "appimage"),
            ("linux", "x86_64", "appimage"),
            ("linux", "aarch64", "deb"),
            ("linux", "x86_64", "deb"),
        }


def test_update_client_selects_native_linux_appimage() -> None:
    assert normalize_architecture("aarch64") == "aarch64"
    assert normalize_architecture("arm64") == "aarch64"
    assert normalize_architecture("x86_64") == "x86_64"
    assert normalize_architecture("amd64") == "x86_64"

    release = UpdateRelease(
        identity=ReleaseIdentity("26.3.1", 0),
        channel="stable",
        release_tag="v26.3.1-build0",
        release_url="https://github.com/10-X-eng/vibecad/releases/tag/v26.3.1-build0",
        published_at="2026-09-12T00:00:00Z",
        assets=(
            UpdateAsset(
                "linux",
                "x86_64",
                "appimage",
                "VibeCAD-26.3.1-build0-Linux-x86_64.AppImage",
                "https://example.invalid/x86",
                1,
                "a" * 64,
            ),
            UpdateAsset(
                "linux",
                "aarch64",
                "appimage",
                "VibeCAD-26.3.1-build0-Linux-aarch64.AppImage",
                "https://example.invalid/arm",
                1,
                "b" * 64,
            ),
        ),
    )
    selected = release.asset_for(system="Linux", machine="aarch64")
    assert selected is not None
    assert selected.architecture == "aarch64"
    assert selected.name.endswith("-Linux-aarch64.AppImage")
    x86 = release.asset_for(system="Linux", machine="x86_64")
    assert x86 is not None
    assert x86.architecture == "x86_64"


def test_native_linux_build_entry_points_launch_freecadcmd() -> None:
    build_script = _source("tools/build_vibecad.sh")
    local_release = _source(
        "package/rattler-build/scripts/build_vibecad_local_release.sh"
    )
    pixi = _source("pixi.toml")

    assert '"${build_dir}/bin/FreeCADCmd" --version' in build_script
    assert "qemu" not in build_script
    assert "linux-aarch64" in pixi
    assert "build-release-native" in pixi
    assert '"${freecadcmd_executable}" --safe-mode --version' in local_release
    assert "install_vibecad_codex_runtime.sh" in local_release
    assert "from VibeCADGeometry import runtime_execution_smoke" in local_release


def test_windows_and_macos_bundle_scripts_remain_present() -> None:
    windows = _source("package/rattler-build/windows/create_bundle.sh")
    macos = _source("package/rattler-build/osx/create_bundle.sh")

    assert 'version_name="${artifact_base}-Windows-$(uname -m)"' in windows
    assert "VibeCAD.exe" in windows
    assert "macOS" in macos
    assert "VibeCAD.app" in macos or "FreeCAD.app" in macos
