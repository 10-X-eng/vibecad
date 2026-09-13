#!/bin/bash

set -e
set -x

conda_env="$(pwd)/../.pixi/envs/default/"

copy_dir="VibeCAD_Windows"
if [[ -z "${BUILD_TAG:-}" || ! "${BUILD_TAG}" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]]; then
  echo "BUILD_TAG must contain only letters, numbers, '.', '_', '+', and '-'." >&2
  exit 1
fi
artifact_base="$(python ../../../src/Tools/resolve_release_artifact_name.py ../../..)"
version_name="${artifact_base}-Windows-$(uname -m)"

# Make local rebuilds behave like the runner's fresh workspace. The artifact
# resolver validates every dynamic filename component before cleanup runs.
rm -rf -- "${copy_dir}" "${version_name}" ".nsis_tmp"
rm -f -- \
  "${version_name}.7z" \
  "${version_name}.7z-SHA256.txt" \
  "${version_name}-installer.exe" \
  "${version_name}-installer.exe-SHA256.txt" \
  "../../WindowsInstaller/${version_name}-installer.exe"
mkdir -p ${copy_dir}/bin

copy_tree() {
  local source="${1%/}"
  local target="${2%/}"

  if [[ ! -d "${source}" ]]; then
    echo "Missing required bundle directory: ${source}" >&2
    exit 1
  fi

  echo "Copying directory: ${source} -> ${target}"
  if command -v robocopy.exe >/dev/null 2>&1 && command -v cygpath >/dev/null 2>&1; then
    local source_win
    local target_win
    source_win="$(cygpath -w "${source}")"
    target_win="$(cygpath -w "${target}")"
    mkdir -p "${target}"
    set +e
    MSYS2_ARG_CONV_EXCL='*' robocopy.exe \
      "${source_win}" \
      "${target_win}" \
      /E \
      /COPY:DAT \
      /DCOPY:DAT \
      /R:2 \
      /W:2 \
      /NFL \
      /NDL \
      /NJH \
      /NJS \
      /NP
    local robocopy_status=$?
    set -e
    if [[ ${robocopy_status} -lt 8 ]]; then
      return 0
    fi
    echo "robocopy failed with exit code ${robocopy_status}" >&2
  fi

  if ! "${conda_env}/python.exe" - "${source}" "${target}" <<'PY'
import os
import shutil
import sys

source, target = sys.argv[1], sys.argv[2]
os.makedirs(target, exist_ok=True)
shutil.copytree(
    source,
    target,
    dirs_exist_ok=True,
    symlinks=False,
    ignore_dangling_symlinks=True,
)
PY
  then
    echo "Failed to copy directory: ${source} -> ${target}" >&2
    df -h . >&2 || true
    du -sh "${source}" "${target}" >&2 || true
    exit 1
  fi
}

copy_matching_files() {
  local source="${1%/}"
  local pattern="$2"
  local target="${3%/}"

  if [[ ! -d "${source}" ]]; then
    echo "Missing required file source directory: ${source}" >&2
    exit 1
  fi

  echo "Copying files: ${source}/${pattern} -> ${target}"
  if ! "${conda_env}/python.exe" - "${source}" "${pattern}" "${target}" <<'PY'
import fnmatch
import os
import shutil
import sys

source, pattern, target = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(target, exist_ok=True)
matches = [
    name
    for name in os.listdir(source)
    if os.path.isfile(os.path.join(source, name)) and fnmatch.fnmatchcase(name, pattern)
]
if not matches:
    print(f"No files matched {source}/{pattern}", file=sys.stderr)
    sys.exit(2)
for name in sorted(matches):
    shutil.copy2(os.path.join(source, name), os.path.join(target, name))
print(f"Copied {len(matches)} files.")
PY
  then
    echo "Failed to copy files: ${source}/${pattern} -> ${target}" >&2
    exit 1
  fi
}

../scripts/install_vibecad_provider_deps.sh "${conda_env}"
../scripts/install_vibecad_codex_runtime.sh \
  "${conda_env}/python.exe" \
  "${conda_env}/Library/Mod/VibeCAD"
../scripts/purge_vibecad_retired_authoring_artifacts.sh \
  "${conda_env}" \
  "${conda_env}/Library/Mod/VibeCAD"

# Copy Conda's Python and (U)CRT to FreeCAD/bin
copy_tree "${conda_env}/DLLs" "${copy_dir}/bin/DLLs"
copy_tree "${conda_env}/Lib" "${copy_dir}/bin/Lib"
copy_tree "${conda_env}/Scripts" "${copy_dir}/bin/Scripts"
copy_matching_files "${conda_env}" "python*.*" "${copy_dir}/bin"
copy_matching_files "${conda_env}" "msvc*.*" "${copy_dir}/bin"
copy_matching_files "${conda_env}" "ucrt*.*" "${copy_dir}/bin"
# Copy meaningful executables
cp -a "${conda_env}/Library/bin/ccx.exe" "${copy_dir}/bin"
cp -a "${conda_env}/Library/bin/gmsh.exe" "${copy_dir}/bin"
cp -a "${conda_env}/Library/bin/dot.exe" "${copy_dir}/bin"
cp -a "${conda_env}/Library/bin/unflatten.exe" "${copy_dir}/bin"
copy_matching_files "${conda_env}/Library/bin" "VibeCADGeometryWorker.exe" "${copy_dir}/bin"
copy_tree "${conda_env}/Library/mingw-w64/bin" "${copy_dir}/bin"
# Copy resources with Python instead of Git Bash cp; this avoids silent
# failures on Windows symlink/path metadata in deep share trees.
copy_tree "${conda_env}/Library/share" "${copy_dir}/share"
# get all the dependency .dlls
copy_matching_files "${conda_env}/Library/bin" "*.dll" "${copy_dir}/bin"
# Copy FreeCAD build
copy_matching_files "${conda_env}/Library/bin" "freecad*" "${copy_dir}/bin"
copy_matching_files "${conda_env}/Library/bin" "FreeCAD*" "${copy_dir}/bin"
# Keep upstream compatibility executables while providing a first-class
# VibeCAD process name for shortcuts, file associations, and Task Manager.
cp -a "${copy_dir}/bin/freecad.exe" "${copy_dir}/bin/VibeCAD.exe"
if [[ ! -x "${copy_dir}/bin/VibeCAD.exe" ]]; then
    echo "Branded VibeCAD executable was not created." >&2
    exit 1
fi
copy_tree "${conda_env}/Library/data" "${copy_dir}/data"
copy_tree "${conda_env}/Library/Ext" "${copy_dir}/Ext"
copy_tree "${conda_env}/Library/lib" "${copy_dir}/lib"
copy_tree "${conda_env}/Library/Mod" "${copy_dir}/Mod"
../scripts/purge_vibecad_retired_authoring_artifacts.sh \
  "${copy_dir}" \
  "${copy_dir}/Mod/VibeCAD"
if [[ ! -x "${copy_dir}/bin/pythonw.exe" ]]; then
  echo "Windowless Python executable is missing: ${copy_dir}/bin/pythonw.exe" >&2
  exit 1
fi
mkdir -p ${copy_dir}/doc
cp -a "${conda_env}"/Library/doc/{ThirdPartyLibraries.html,LICENSE.html} "${copy_dir}/doc"

# delete unnecessary stuff
find ${copy_dir} -name \*.a -delete
find ${copy_dir} -name \*.lib -delete
find ${copy_dir} -name \*arm\*.exe -delete # arm binaries that fail to extract unless using latest 7zip

# Apply Patches
mv ${copy_dir}/bin/Lib/ssl.py .ssl-orig.py
cp ssl-patch.py ${copy_dir}/bin/Lib/ssl.py

# Turn off the echo before we start actually calling "echo"
set +x

echo '[Paths]' >> ${copy_dir}/bin/qt6.conf
echo 'Prefix = ../lib/qt6' >> ${copy_dir}/bin/qt6.conf

# Deterministic root launchers built by the same CMake/MSVC recipe as VibeCAD.
# Do not depend on Chocolatey's runner-global, proprietary shim generator.
cp -a "${conda_env}/Library/bin/VibeCADPortableLauncher.exe" "${copy_dir}/VibeCAD.exe"
cp -a "${conda_env}/Library/bin/VibeCADCmdPortableLauncher.exe" "${copy_dir}/FreeCADCmd.exe"
if [[ ! -x "${copy_dir}/VibeCAD.exe" || ! -x "${copy_dir}/FreeCADCmd.exe" ]]; then
    echo "Portable VibeCAD launchers were not created." >&2
    exit 1
fi

echo -e "################"
echo -e "version_name:  ${version_name}"
echo -e "################"

pixi list -e default > ${copy_dir}/packages.txt
sed -i '1s/.*/\nLIST OF PACKAGES:/' ${copy_dir}/packages.txt

for move_attempt in 1 2 3 4 5 6 7 8 9 10; do
  if mv "${copy_dir}" "${version_name}"; then
    break
  fi
  if [[ "${move_attempt}" -eq 10 ]]; then
    echo "Windows kept the completed bundle tree locked after 10 rename attempts." >&2
    exit 1
  fi
  echo "Bundle rename attempt ${move_attempt} was blocked; retrying..." >&2
  sleep "${move_attempt}"
done

set -euo pipefail
SIGN_DIR="${version_name}"

echo "Running VibeCAD command-line smoke test..."
if ! "$SIGN_DIR/bin/freecadcmd.exe" --safe-mode --version; then
  echo "VibeCAD command-line smoke test failed; the Windows bundle cannot start."
  exit 1
fi
if ! "$SIGN_DIR/FreeCADCmd.exe" --safe-mode --version; then
  echo "VibeCAD portable command-line launcher smoke test failed."
  exit 1
fi
if [[ ! -x "$SIGN_DIR/Mod/McMasterInsert/McMasterCatalogWebView2.exe" ]]; then
  echo "VibeCAD McMaster WebView2 helper is missing from the Windows bundle."
  exit 1
fi
if ! "$SIGN_DIR/Mod/McMasterInsert/McMasterCatalogWebView2.exe" --smoke-test; then
  echo "VibeCAD McMaster WebView2 helper could not find the Edge WebView2 Runtime."
  exit 1
fi
if ! "$SIGN_DIR/Mod/McMasterInsert/McMasterCatalogWebView2.exe" --argument-parser-smoke-test --inbox=parser-smoke-inbox --profile parser-smoke-profile; then
  echo "VibeCAD McMaster WebView2 helper could not parse its launch paths."
  exit 1
fi
if ! "$SIGN_DIR/bin/freecadcmd.exe" --safe-mode -c "import importlib.util, anthropic, keyring, jsonschema, mcp, mcp_types, openai, tuf; import keyring.backends.Windows; assert importlib.util.find_spec('agents') is None; print('VibeCAD Python dependencies and OS keyring backend import ok')"; then
  echo "VibeCAD Python dependency/keyring smoke test failed; the Windows bundle is incomplete."
  exit 1
fi
if ! "$SIGN_DIR/bin/freecadcmd.exe" --safe-mode -c "from VibeCADProvider import _provider_subprocess_smoke; _provider_subprocess_smoke(); print('VibeCAD provider subprocess smoke ok')"; then
  echo "VibeCAD provider subprocess smoke test failed; the Windows bundle cannot run AI providers."
  exit 1
fi
if ! "$SIGN_DIR/bin/freecadcmd.exe" --safe-mode -c "from VibeCADCodex import runtime_execution_smoke; result = runtime_execution_smoke(); print('VibeCAD Codex app-server smoke ok', result['version'])"; then
  echo "VibeCAD Codex app-server smoke test failed; the Windows bundle cannot use ChatGPT subscriptions."
  exit 1
fi
if ! "$SIGN_DIR/bin/freecadcmd.exe" --safe-mode -c "from VibeCADGeometry import runtime_execution_smoke; result = runtime_execution_smoke(); print('VibeCAD geometry worker smoke ok', result['worker'])"; then
  echo "VibeCAD geometry worker smoke test failed; the Windows bundle cannot inspect geometry."
  exit 1
fi
if ! "$SIGN_DIR/bin/freecadcmd.exe" --safe-mode -c "from VibeCADProvider import _provider_subprocess_smoke; _provider_subprocess_smoke(prefer_windowless_python=True, require_windowless_python=True); print('VibeCAD windowless provider subprocess smoke ok')"; then
  echo "VibeCAD windowless provider subprocess smoke test failed; the Windows GUI bundle would show a Python console."
  exit 1
fi

# The portable archive is retained as an explicit compatibility option for
# developers, but release and validation builds produce only the installer.
if [[ "${MAKE_PORTABLE_ARCHIVE:-false}" == "true" ]]; then
    7z a -t7z -mx5 -mmt="${NUMBER_OF_PROCESSORS}" "${version_name}.7z" "${version_name}" -bb
    sha256sum "${version_name}.7z" > "${version_name}.7z-SHA256.txt"
fi

if [[ "${MAKE_INSTALLER:-true}" == "true" ]]; then
    # NSIS still opens source files through MAX_PATH-limited APIs. The GitHub
    # workspace plus the release name can push valid runtime files beyond that
    # limit, so compile from a short, temporary drive mapping without removing
    # anything from the distributable runtime.
    nsis_source_drive="$("${conda_env}/python.exe" - <<'PY' | tr -d '\r\n'
import ctypes

logical_drives = ctypes.windll.kernel32.GetLogicalDrives()
for drive_index in range(ord("Z") - ord("A"), ord("C") - ord("A"), -1):
    if not logical_drives & (1 << drive_index):
        print(f"{chr(ord('A') + drive_index)}:")
        break
else:
    raise SystemExit("No unused Windows drive letter is available for NSIS staging.")
PY
)"
    if [[ ! "${nsis_source_drive}" =~ ^[A-Z]:$ ]]; then
        echo "Could not select a valid unused Windows drive for NSIS staging: ${nsis_source_drive}" >&2
        exit 1
    fi
    bundle_source_windows="$(cygpath -w "$(pwd)/${version_name}")"
    cleanup_nsis_source_drive() {
        if [[ -n "${nsis_source_drive:-}" ]]; then
            MSYS2_ARG_CONV_EXCL='*' subst.exe "${nsis_source_drive}" /D >/dev/null 2>&1 || true
        fi
    }
    trap cleanup_nsis_source_drive EXIT
    MSYS2_ARG_CONV_EXCL='*' subst.exe "${nsis_source_drive}" "${bundle_source_windows}"
    FILES_FREECAD="${nsis_source_drive}"
    nsis_cpdir=$(pwd)/.nsis_tmp
    cp -r "${CONDA_PREFIX}/NSIS" "${nsis_cpdir}"
    # curl -L -o ".nsis-log.zip" http://prdownloads.sourceforge.net/nsis/nsis-3.11-log.zip # we use the log variant of the package already
    # curl -L -o ".nsis-strlen_8192.zip" "http://prdownloads.sourceforge.net/nsis/nsis-3.11-strlen_8192.zip"
    curl -L -o ".NsProcess.7z" "https://nsis.sourceforge.io/mediawiki/images/1/18/NsProcess.zip"
    if ! echo "fc19fc66a5219a233570fafd5daeb0c9b85387b379f6df5ac8898159a57c5944  .NsProcess.7z" | sha256sum --check --status; then
        echo "The downloaded NsProcess plugin failed its pinned SHA-256 check." >&2
        exit 1
    fi
    7z x .NsProcess.7z -o"${nsis_cpdir}" -y
    mv "${nsis_cpdir}"/Plugin/nsProcess.dll "${nsis_cpdir}"/Plugins/x86-ansi/nsProcess.dll
    mv "${nsis_cpdir}"/Plugin/nsProcessW.dll "${nsis_cpdir}"/Plugins/x86-unicode/nsProcess.dll
    "${nsis_cpdir}"/makensis.exe -V4 \
        -D"ExeFile=${version_name}-installer.exe" \
        -D"FILES_FREECAD=${FILES_FREECAD}" \
        -X'SetCompressor /FINAL lzma' \
        ../../WindowsInstaller/FreeCAD-installer.nsi
    mv ../../WindowsInstaller/${version_name}-installer.exe .
    echo "Created installer ${version_name}-installer.exe"

    sha256sum ${version_name}-installer.exe > ${version_name}-installer.exe-SHA256.txt
    rm -rf "${nsis_cpdir}"
    cleanup_nsis_source_drive
    trap - EXIT
fi
