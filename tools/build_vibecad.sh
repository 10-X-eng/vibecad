#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"

build_dir="${VIBECAD_BUILD_DIR:-${repo_root}/build/release}"
build_type="${VIBECAD_BUILD_TYPE:-Release}"
generator="${VIBECAD_CMAKE_GENERATOR:-Ninja}"
jobs="${VIBECAD_BUILD_JOBS:-$(nproc)}"
vibecad_requirements="${repo_root}/src/Mod/VibeCAD/requirements.txt"

clean=0
run_tests=0
install_prefix=""

usage() {
    cat <<EOF
Usage: tools/build_vibecad.sh [options]

Options:
  --clean                 Explicitly remove the build directory before configuring.
  --incremental           Reuse the existing build directory. This is the default.
  --test                  Run ctest after the build completes.
  --install PREFIX        Run cmake --install into PREFIX after building.
  -h, --help              Show this help.

Environment:
  VIBECAD_BUILD_DIR       Build directory. Default: <repo>/build/release
  VIBECAD_BUILD_TYPE      CMake build type. Default: Release
  VIBECAD_CMAKE_GENERATOR CMake generator. Default: Ninja
  VIBECAD_BUILD_JOBS      Parallel build jobs. Default: nproc
EOF
}

while (($#)); do
    case "$1" in
        --clean)
            clean=1
            shift
            ;;
        --incremental)
            clean=0
            shift
            ;;
        --test)
            run_tests=1
            shift
            ;;
        --install)
            if [[ $# -lt 2 || -z "${2:-}" ]]; then
                echo "error: --install requires a prefix" >&2
                exit 2
            fi
            install_prefix="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ! -f "${repo_root}/CMakeLists.txt" ]]; then
    echo "error: could not find repo CMakeLists.txt at ${repo_root}" >&2
    exit 1
fi

if ((clean)); then
    rm -rf -- "${build_dir}"
elif [[ -f "${build_dir}/CMakeCache.txt" ]]; then
    cached_source="$(
        sed -n 's/^CMAKE_HOME_DIRECTORY:INTERNAL=//p' "${build_dir}/CMakeCache.txt" | head -n 1
    )"
    if [[ -n "${cached_source}" && "$(cd -- "${cached_source}" 2>/dev/null && pwd || true)" != "${repo_root}" ]]; then
        echo "error: ${build_dir} belongs to another source checkout: ${cached_source}" >&2
        echo "Run again with --clean only if you explicitly intend to replace it." >&2
        exit 1
    fi
fi

git -C "${repo_root}" submodule update --init --recursive \
    src/3rdParty/GSL \
    src/3rdParty/OndselSolver \
    src/Mod/AddonManager \
    src/Mod/Fasteners

cmake \
    -S "${repo_root}" \
    -B "${build_dir}" \
    -G "${generator}" \
    -DCMAKE_BUILD_TYPE="${build_type}" \
    -DINSTALL_TO_SITEPACKAGES=OFF

cmake --build "${build_dir}" --parallel "${jobs}"

if [[ -f "${vibecad_requirements}" ]]; then
    mkdir -p "${build_dir}/Ext"
    python3 -m pip install \
        --disable-pip-version-check \
        --upgrade \
        --target "${build_dir}/Ext" \
        --requirement "${vibecad_requirements}"
    find "${build_dir}/Ext" -maxdepth 1 \
        \( -name openai -o -name 'openai-*.dist-info' \
        -o -name agents -o -name 'openai_agents-*.dist-info' \) \
        -exec rm -rf -- {} +
fi

"${build_dir}/bin/FreeCADCmd" --version

"${build_dir}/bin/FreeCADCmd" -c "import anthropic, keyring, jsonschema; print('VibeCAD Python dependencies import OK')"

if ((run_tests)); then
    ctest --test-dir "${build_dir}" --output-on-failure
fi

if [[ -n "${install_prefix}" ]]; then
    cmake --install "${build_dir}" --prefix "${install_prefix}"
    if [[ -f "${vibecad_requirements}" ]]; then
        mkdir -p "${install_prefix}/Ext"
        python3 -m pip install \
            --disable-pip-version-check \
            --upgrade \
            --target "${install_prefix}/Ext" \
            --requirement "${vibecad_requirements}"
        find "${install_prefix}/Ext" -maxdepth 1 \
            \( -name openai -o -name 'openai-*.dist-info' \
            -o -name agents -o -name 'openai_agents-*.dist-info' \) \
            -exec rm -rf -- {} +
    fi
fi

echo "VibeCAD build complete: ${build_dir}"
