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

codex_version="0.144.5"
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
        archive="codex-app-server-x86_64-unknown-linux-musl.tar.gz"
        archive_sha256="834a0c85947cd1840141f347f4b7368e2e21bf9c1b85934bcc8c397ece93ee74"
        executable="${runtime_root}/codex-app-server"
        ;;
    linux:aarch64|linux:arm64)
        archive="codex-app-server-aarch64-unknown-linux-musl.tar.gz"
        archive_sha256="d2230513fcbe363e6230a4cb53917fafd68c2d2bad953035d99059eb18c07117"
        executable="${runtime_root}/codex-app-server"
        ;;
    win32:amd64|win32:x86_64)
        archive="codex-app-server-x86_64-pc-windows-msvc.exe.tar.gz"
        archive_sha256="dd79c88858523619273faeb50d4d79923dca53095d88e2ad0b477d5222fcf19d"
        executable="${runtime_root}/codex-app-server.exe"
        ;;
    win32:arm64|win32:aarch64)
        archive="codex-app-server-aarch64-pc-windows-msvc.exe.tar.gz"
        archive_sha256="6dc2fa9de0b0f88d9578d66319b2fa9069bdc9f61c7ce1f0897fdea0e8861801"
        executable="${runtime_root}/codex-app-server.exe"
        ;;
    darwin:arm64|darwin:aarch64)
        archive="codex-app-server-aarch64-apple-darwin.tar.gz"
        archive_sha256="ec98c5647ff482cde7fe7b4091950a23f19ffceb0b343612a1bab0de0857f5d1"
        executable="${runtime_root}/codex-app-server"
        ;;
    darwin:x86_64|darwin:amd64)
        archive="codex-app-server-x86_64-apple-darwin.tar.gz"
        archive_sha256="6900e9f59347d9ea0909cffd56a8e6659dd89c793e33f70372ff5fb2c00081da"
        executable="${runtime_root}/codex-app-server"
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
    output="$("${executable}" --version)"
    if [[ "${output}" != *"${codex_version}"* ]]; then
        echo "Unexpected Codex app-server version: ${output}" >&2
        exit 1
    fi
}

if [[ -f "${stamp}" ]] \
  && [[ "$(tr -d '\r\n' < "${stamp}")" == "${runtime_spec}" ]] \
  && [[ -x "${executable}" ]] \
  && [[ -f "${runtime_root}/LICENSE" ]] \
  && [[ -f "${runtime_root}/runtime.json" ]]; then
    smoke_runtime
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

source_executable="$(${python_executable} - "${temporary_root}" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
candidates = [
    path
    for path in root.rglob("*")
    if path.is_file() and path.name.startswith("codex-app-server-")
]
if len(candidates) != 1:
    raise SystemExit(
        f"Codex archive must contain exactly one app-server executable; found {candidates}"
    )
print(candidates[0])
PY
)"

rm -rf "${runtime_root}"
mkdir -p "${runtime_root}"
cp "${source_executable}" "${executable}"
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
