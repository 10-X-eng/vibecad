# SPDX-License-Identifier: LGPL-2.1-or-later

"""Keep the adapter and all portable runtime targets on the verified release."""

import ast
from pathlib import Path
import re
import json
import pytest


def test_codex_runtime_release_is_consistent():
    root = Path(__file__).resolve().parents[4]
    adapter = ast.parse((root / "src/Mod/VibeCAD/VibeCADCodex.py").read_text())
    version = next(
        ast.literal_eval(node.value)
        for node in adapter.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "CODEX_APP_SERVER_VERSION"
                for t in node.targets)
    )
    installer = (root / "package/rattler-build/scripts/install_vibecad_codex_runtime.sh").read_text()
    assert version == "0.153.4"
    assert f'codex_version="{version}"' in installer
    archives = re.findall(r'archive="([^"]+)"\s+archive_sha256="([a-f0-9]{64})"', installer)
    assert len(archives) == 6
    assert len({name for name, _ in archives}) == 6
    assert all(name.startswith('codex-app-server-package-') for name, _ in archives)


def test_native_cmake_installs_pinned_codex_runtime_into_build_module():
    root = Path(__file__).resolve().parents[4]
    cmake = (root / "src/Mod/VibeCAD/CMakeLists.txt").read_text()
    assert "install_vibecad_codex_runtime.sh" in cmake
    assert "VibeCADCodexRuntime" in cmake
    assert "CMAKE_BINARY_DIR}/Mod/VibeCAD" in cmake
    assert "ALL" in cmake


def test_complete_runtime_resolves_packaged_entrypoint_and_requires_companion(tmp_path, monkeypatch):
    import VibeCADCodex as codex
    monkeypatch.delenv(codex.CODEX_APP_SERVER_ENV, raising=False)
    monkeypatch.setattr(codex, 'bundled_runtime_root', lambda: tmp_path)
    (tmp_path / 'bin').mkdir()
    binary = tmp_path / 'bin' / codex._runtime_binary_name()
    binary.write_bytes(b'executable fixture')
    helper = binary.with_name('codex-code-mode-host' + binary.suffix)
    helper.write_bytes(b'companion fixture')
    (tmp_path / 'codex-package.json').write_text(json.dumps({
        'layoutVersion': 1, 'version': codex.CODEX_APP_SERVER_VERSION,
        'entrypoint': 'bin/' + binary.name}))
    (tmp_path / codex.CODEX_RUNTIME_MANIFEST).write_text(json.dumps({
        'version': codex.CODEX_APP_SERVER_VERSION}))
    assert codex.resolve_runtime_command().executable == binary
    helper.unlink()
    with pytest.raises(codex.CodexAppServerError, match='companion'):
        codex.resolve_runtime_command()
