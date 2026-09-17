#!/usr/bin/env bash

set -euo pipefail

if [[ "$#" -ne 2 ]]; then
    echo "usage: $0 PYTHON_EXECUTABLE VIBECAD_MODULE_DIRECTORY" >&2
    exit 2
fi

python_executable="$1"
module_directory="$2"
script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_directory}/../../.." && pwd)"
download_cache="${VIBECAD_DOWNLOAD_CACHE:-${repository_root}/package/rattler-build/.download-cache}"
runtime_root="${module_directory}/codex_runtime"
stamp="${runtime_root}/runtime-spec.sha256"

codex_version="0.154.0"
release_tag="rust-v${codex_version}"
release_root="https://github.com/openai/codex/releases/download/${release_tag}"
license_url="https://raw.githubusercontent.com/openai/codex/${release_tag}/LICENSE"
license_sha256="d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc"

if [[ ! -x "${python_executable}" ]]; then
    echo "Codex runtime Python is not executable: ${python_executable}" >&2
    exit 1
fi

platform="$(${python_executable} -c 'import sys; print(sys.platform)')"
machine="$(${python_executable} -c 'import platform; print(platform.machine().lower())')"
case "${platform}:${machine}" in
    linux:x86_64|linux:amd64)
        archive="codex-app-server-package-x86_64-unknown-linux-musl.tar.gz"
        archive_sha256="b2450aaa4004d06790dd8a69d0246f4503ff1258400cc20de7d7a42ffe81b253"
        executable="${runtime_root}/bin/codex-app-server"
        ;;
    linux:aarch64|linux:arm64)
        archive="codex-app-server-package-aarch64-unknown-linux-musl.tar.gz"
        archive_sha256="295bb1b94a8b964b2d2461db9736b9907a9e4daa6ceb8e9bbb820b304fa897ed"
        executable="${runtime_root}/bin/codex-app-server"
        ;;
    win32:amd64|win32:x86_64)
        archive="codex-app-server-package-x86_64-pc-windows-msvc.tar.gz"
        archive_sha256="5f8b43e030c0aeeb7bdb3d5e03fff4c68ba94fa2df0ae437c490811f54660d74"
        executable="${runtime_root}/bin/codex-app-server.exe"
        ;;
    win32:arm64|win32:aarch64)
        archive="codex-app-server-package-aarch64-pc-windows-msvc.tar.gz"
        archive_sha256="7406aa3745acd5bd639b8921c0ee5e241861763606465cac643683944a849457"
        executable="${runtime_root}/bin/codex-app-server.exe"
        ;;
    darwin:arm64|darwin:aarch64)
        archive="codex-app-server-package-aarch64-apple-darwin.tar.gz"
        archive_sha256="7bf20c1843bdcff086c89a294299833f20146ebbdba03e7f49f020b7adbfff7b"
        executable="${runtime_root}/bin/codex-app-server"
        ;;
    darwin:x86_64|darwin:amd64)
        archive="codex-app-server-package-x86_64-apple-darwin.tar.gz"
        archive_sha256="4fddde3689d2aa0058c06138a84b05f87bdbff8cd556fba5816a97ac0469a4d4"
        executable="${runtime_root}/bin/codex-app-server"
        ;;
    *)
        echo "No pinned Codex app-server is available for ${platform}/${machine}." >&2
        exit 1
        ;;
esac

archive_url="${release_root}/${archive}"

# Portable SHA-256 helpers (GNU coreutils on Linux; BSD sha256sum on macOS).
# macOS /sbin/sha256sum rejects GNU long options such as --check/--status and
# does not read checksum lines from stdin the same way.
sha256_file() {
    sha256sum "$1" | awk '{print $1}'
}

sha256_matches() {
    local expected="$1"
    local path="$2"
    local actual
    actual="$(sha256_file "${path}")"
    [[ "${actual}" == "${expected}" ]]
}

runtime_spec="$({
    printf '%s\n' \
        "version=${codex_version}" \
        "archive=${archive}:${archive_sha256}" \
        "license=${license_sha256}"
    sha256_file "$0"
} | sha256sum | awk '{print $1}')"

smoke_runtime() {
    local output
    "${python_executable}" - "${runtime_root}" "${codex_version}" "${archive}" "${archive_sha256}" <<'PY' || return 1
import json
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
runtime = json.loads((root / "runtime.json").read_text())
expected = {
    "schema": "vibecad-codex-runtime-v1",
    "version": sys.argv[2],
    "release_tag": "rust-v" + sys.argv[2],
    "asset": sys.argv[3],
    "sha256": sys.argv[4],
}
if any(runtime.get(key) != value for key, value in expected.items()):
    raise SystemExit("Unexpected Codex runtime metadata")
manifest = json.loads((root / "codex-package.json").read_text())
if manifest.get("layoutVersion") != 1 or manifest.get("version") != sys.argv[2]:
    raise SystemExit("Unexpected Codex package layout or version")
suffix = ".exe" if sys.platform == "win32" else ""
required = ["bin/codex-app-server" + suffix, "bin/codex-code-mode-host" + suffix,
            "codex-path/rg" + suffix]
if suffix:
    required += ["codex-resources/codex-command-runner.exe",
                 "codex-resources/codex-windows-sandbox-setup.exe"]
if manifest.get("entrypoint") != required[0]:
    raise SystemExit("Unexpected Codex package entrypoint")
for name in required:
    if not (root / name).is_file():
        raise SystemExit(f"Missing Codex package companion: {name}")
    if not suffix and not os.access(root / name, os.X_OK):
        raise SystemExit(f"Codex package companion is not executable: {name}")
PY
    output="$("${executable}" --version)" || return 1
    if [[ "${output}" != *"${codex_version}"* ]]; then
        echo "Unexpected Codex app-server version: ${output}" >&2
        return 1
    fi
}

if [[ -f "${stamp}" ]] \
  && [[ "$(tr -d '\r\n' < "${stamp}")" == "${runtime_spec}" ]] \
  && [[ -x "${executable}" ]] \
  && [[ -f "${runtime_root}/LICENSE" ]] \
  && [[ -f "${runtime_root}/runtime.json" ]] \
  && smoke_runtime >/dev/null 2>&1; then
    echo "VibeCAD Codex app-server runtime is current"
    exit 0
fi

mkdir -p "${download_cache}"

download_verified() {
    local url="$1"
    local expected="$2"
    local destination="$3"
    if [[ -f "${destination}" ]] && sha256_matches "${expected}" "${destination}"; then
        return
    fi
    local temporary="${destination}.tmp"
    rm -f "${temporary}"
    curl --fail --location --retry 4 --retry-all-errors --output "${temporary}" "${url}"
    if ! sha256_matches "${expected}" "${temporary}"; then
        local actual
        actual="$(sha256_file "${temporary}")"
        rm -f "${temporary}"
        echo "SHA-256 mismatch for ${url}: expected ${expected}, got ${actual}" >&2
        exit 1
    fi
    mv "${temporary}" "${destination}"
}

archive_path="${download_cache}/${archive}"
license_path="${download_cache}/codex-LICENSE-${release_tag}"
download_verified "${archive_url}" "${archive_sha256}" "${archive_path}"
download_verified "${license_url}" "${license_sha256}" "${license_path}"

temporary_root="$(mktemp -d)"
cleanup() {
    rm -rf "${temporary_root}"
}
trap cleanup EXIT

"${python_executable}" - "${archive_path}" "${temporary_root}" <<'PY'
import pathlib
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
target_root = target.resolve()
with tarfile.open(archive, "r:gz") as package:
    for member in package.getmembers():
        destination = (target / member.name).resolve()
        if target_root != destination and target_root not in destination.parents:
            raise SystemExit(f"Unsafe path in Codex archive: {member.name}")
        if member.issym() or member.islnk():
            raise SystemExit(f"Links are not allowed in Codex archive: {member.name}")
    package.extractall(target)
PY

rm -rf "${runtime_root}"
mkdir -p "${runtime_root}"
# Preserve the official package layout: companion discovery is relative to bin.
cp -R "${temporary_root}/." "${runtime_root}/"
cp "${license_path}" "${runtime_root}/LICENSE"
chmod +x "${executable}"

cat > "${runtime_root}/runtime.json" <<EOF
{
  "schema": "vibecad-codex-runtime-v1",
  "version": "${codex_version}",
  "release_tag": "${release_tag}",
  "asset": "${archive}",
  "sha256": "${archive_sha256}"
}
EOF
printf '%s\n' "${runtime_spec}" > "${stamp}"

if [[ "${platform}" == "darwin" ]]; then
    available_architectures="$(lipo -archs "${executable}")"
    if [[ " ${available_architectures} " != *" ${machine} "* ]] \
      && ! { [[ "${machine}" == "arm64" ]] && [[ " ${available_architectures} " == *" arm64 "* ]]; }; then
        echo "Codex runtime lacks required ${machine} architecture: ${available_architectures}" >&2
        exit 1
    fi
fi

smoke_runtime
echo "VibeCAD Codex app-server ${codex_version} installed"
