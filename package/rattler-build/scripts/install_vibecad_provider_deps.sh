#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rattler_root="$(cd "${script_dir}/.." && pwd)"
repo_root="$(cd "${rattler_root}/../.." && pwd)"

env_root="${1:-${rattler_root}/.pixi/envs/default}"
if [[ ! -d "${env_root}" ]]; then
    echo "VibeCAD runtime environment not found: ${env_root}" >&2
    exit 1
fi
env_root="$(cd "${env_root}" && pwd)"

python_exe=""
if [[ -x "${env_root}/bin/python" ]]; then
    python_exe="${env_root}/bin/python"
elif [[ -x "${env_root}/python.exe" ]]; then
    python_exe="${env_root}/python.exe"
else
    echo "No Python executable found in VibeCAD runtime environment: ${env_root}" >&2
    exit 1
fi

requirements="${repo_root}/src/Mod/VibeCAD/requirements.txt"
aero_requirements="${repo_root}/src/Mod/VibeCADAero/requirements-aero.txt"
for requirements_file in "${requirements}" "${aero_requirements}"; do
    if [[ ! -f "${requirements_file}" ]]; then
        echo "VibeCAD requirements file not found: ${requirements_file}" >&2
        exit 1
    fi
done

echo "Removing the retired OpenAI Agents SDK from ${env_root}"
"${python_exe}" -m pip uninstall --yes openai-agents

echo "Installing VibeCAD Python and Aero dependencies into ${env_root}"
"${python_exe}" -m pip install \
    --disable-pip-version-check \
    --upgrade \
    --prefer-binary \
    -r "${requirements}" \
    -r "${aero_requirements}"
"${python_exe}" -m pip check
"${python_exe}" - <<'PY'
import importlib
import importlib.util
import sys

for module_name in (
    "anthropic",
    "keyring",
    "jsonschema",
    "mcp",
    "mcp_types",
    "openai",
    "tuf",
    "numpy",
    "casadi",
    "neuralfoil",
    "aerosandbox",
    "jsbsim",
):
    importlib.import_module(module_name)

numpy = importlib.import_module("numpy")
if int(numpy.__version__.split(".", 1)[0]) >= 2:
    raise RuntimeError(
        f"NumPy 2 is not compatible with this VibeCAD runtime: {numpy.__version__}"
    )

if sys.platform == "win32":
    importlib.import_module("keyring.backends.Windows")
elif sys.platform == "darwin":
    macos_backend = importlib.import_module("keyring.backends.macOS")
    if macos_backend.Keyring.priority <= 0:
        raise RuntimeError("The macOS Keychain keyring backend is unavailable.")
else:
    importlib.import_module("secretstorage")
    importlib.import_module("keyring.backends.SecretService")

for removed_module in ("agents",):
    if importlib.util.find_spec(removed_module) is not None:
        raise RuntimeError(
            f"The retired OpenAI Agents module {removed_module!r} is still present."
        )

print("VibeCAD Python, Aero, and OS keyring dependencies import ok")
PY
