#!/usr/bin/env bash

set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_directory}/../../.." && pwd)"
build_root="${repository_root}/build/release"
environment_root="${repository_root}/.pixi/envs/default"
module_directory="${build_root}/Mod/VibeCAD"

if [[ -x "${environment_root}/bin/python" ]]; then
    python_executable="${environment_root}/bin/python"
elif [[ -x "${environment_root}/python.exe" ]]; then
    python_executable="${environment_root}/python.exe"
else
    echo "VibeCAD release Python is missing from ${environment_root}." >&2
    exit 1
fi

cmake --build "${build_root}"

freecadcmd_executable=""
for candidate in \
    "${build_root}/bin/FreeCADCmd" \
    "${build_root}/bin/FreeCADCmd.exe" \
    "${build_root}/bin/Release/FreeCADCmd.exe"; do
    if [[ -x "${candidate}" ]]; then
        freecadcmd_executable="${candidate}"
        break
    fi
done

if [[ -z "${freecadcmd_executable}" ]]; then
    echo "The VibeCAD release build produced no FreeCADCmd executable." >&2
    exit 1
fi
if [[ ! -f "${module_directory}/VibeCADCodex.py" ]]; then
    echo "The VibeCAD release module is incomplete: ${module_directory}." >&2
    exit 1
fi

"${script_directory}/install_vibecad_provider_deps.sh" "${environment_root}"
"${script_directory}/install_vibecad_build123d_runtime.sh" \
    "${python_executable}" \
    "${module_directory}"
"${script_directory}/install_vibecad_openscad_runtime.sh" \
    "${python_executable}" \
    "${module_directory}"
"${script_directory}/install_vibecad_codex_runtime.sh" \
    "${python_executable}" \
    "${module_directory}"
"${python_executable}" \
    "${script_directory}/write_vibecad_build123d_manifest.py" \
    "${module_directory}/build123d_runtime" \
    "${environment_root}" \
    "${python_executable}"

"${freecadcmd_executable}" --safe-mode --version
"${freecadcmd_executable}" --safe-mode -c \
    "import anthropic, jsonschema, keyring, openai; print('VibeCAD provider imports ok')"
"${freecadcmd_executable}" --safe-mode -c \
    "from VibeCADProvider import _provider_subprocess_smoke; _provider_subprocess_smoke(); print('VibeCAD provider subprocess smoke ok')"
"${freecadcmd_executable}" --safe-mode -c \
    "from VibeCADBuild123d import runtime_execution_smoke; result = runtime_execution_smoke(); print('VibeCAD build123d runtime smoke ok', result['version'])"
"${freecadcmd_executable}" --safe-mode -c \
    "from VibeCADOpenSCAD import runtime_execution_smoke; result = runtime_execution_smoke(); print('VibeCAD OpenSCAD runtime smoke ok', result['version'])"
"${freecadcmd_executable}" --safe-mode -c \
    "from VibeCADCodex import runtime_execution_smoke; result = runtime_execution_smoke(); print('VibeCAD Codex app-server smoke ok', result['version'])"

echo "VibeCAD local release is runtime-complete: ${build_root}"
