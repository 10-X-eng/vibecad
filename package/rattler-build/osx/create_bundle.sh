#!/usr/bin/env bash

set -euo pipefail
set -x

app_name="VibeCAD.app"
default_env="../.pixi/envs/default"
conda_env="${app_name}/Contents/Resources"
module_directory="${conda_env}/Mod/VibeCAD"

rm -rf "${app_name}"

default_env_absolute="$(cd "${default_env}" && pwd)"
mkdir -p "$(dirname "${conda_env}")"
conda_env_absolute="$(cd "$(dirname "${conda_env}")" && pwd)/$(basename "${conda_env}")"

# Relocate the conda environment directly for its final app prefix. A raw directory
# copy preserves the source prefix in Mach-O load commands and Python metadata.
python ../scripts/relocate_conda_environment.py \
    "${default_env_absolute}" \
    "${conda_env_absolute}"

../scripts/install_vibecad_provider_deps.sh "${conda_env}"
../scripts/install_vibecad_codex_runtime.sh \
    "${conda_env}/bin/python" \
    "${module_directory}"
../scripts/purge_vibecad_retired_authoring_artifacts.sh \
    "${conda_env}" \
    "${module_directory}"

# delete unnecessary stuff
rm -rf "${conda_env}/include"
rm -rf "${conda_env}/conda-meta"
rm -rf "${conda_env}/etc/conda/test-files"
find "${conda_env}" -name \*.a -delete

mv "${conda_env}/bin" "${conda_env}/bin_tmp"
mkdir "${conda_env}/bin"
cp "${conda_env}/bin_tmp/freecad" "${conda_env}/bin/"
cp "${conda_env}/bin_tmp/freecadcmd" "${conda_env}/bin/"
cp "${conda_env}/bin_tmp/ccx" "${conda_env}/bin/"
cp "${conda_env}/bin_tmp/python" "${conda_env}/bin/"
cp "${conda_env}/bin_tmp/pip" "${conda_env}/bin/"
cp "${conda_env}/bin_tmp/pyside6-rcc" "${conda_env}/bin/"
cp "${conda_env}/bin_tmp/gmsh" "${conda_env}/bin/"
cp "${conda_env}/bin_tmp/dot" "${conda_env}/bin/"
cp "${conda_env}/bin_tmp/unflatten" "${conda_env}/bin/"
cp "${conda_env}/bin_tmp/VibeCADGeometryWorker" "${conda_env}/bin/"
rm -rf "${conda_env}/bin_tmp"

sed -i '1s|.*|#!/usr/bin/env python|' "${conda_env}/bin/pip"

# copy resources
cp resources/* "${conda_env}"

iconset="$(mktemp -d)/VibeCAD.iconset"
mkdir -p "${iconset}"
"${conda_env}/bin/python" - \
    "../../../src/Gui/Icons/vibecad.svg" \
    "${iconset}" <<'PY'
from pathlib import Path
import sys

from PySide6 import QtCore, QtGui, QtSvg

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
renderer = QtSvg.QSvgRenderer(str(source))
if not renderer.isValid():
    raise SystemExit(f"VibeCAD app icon is not a valid SVG: {source}")
outputs = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}
for name, size in outputs.items():
    image = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
    image.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(image)
    renderer.render(painter)
    painter.end()
    if not image.save(str(destination / name), "PNG"):
        raise SystemExit(f"Could not write VibeCAD app icon: {name}")
PY
iconutil -c icns --output "${conda_env}/vibecad.icns" "${iconset}"
rm -rf "$(dirname "${iconset}")"

# Remove __pycache__ folders and .pyc files
find "${conda_env}" -path "*/__pycache__/*" -delete
find "${conda_env}" -name "*.pyc" -type f -delete

# Repair relocatable identities and dependencies only in the top-level library
# directory. Recursively rewriting every native extension can remove load paths
# required by wheel-private runtimes.
# see https://github.com/FreeCAD/FreeCAD/issues/10144#issuecomment-1836686775
# and https://github.com/FreeCAD/FreeCAD-Bundle/pull/203
# and https://github.com/FreeCAD/FreeCAD-Bundle/issues/375
python ../scripts/fix_macos_lib_paths.py \
    "${conda_env}/lib" \
    --bundle-prefix "${conda_env_absolute}" \
    --source-prefix "${default_env_absolute}"

# build and install the launcher
cmake -B build launcher
cmake --build build
mkdir -p "${app_name}/Contents/MacOS"
cp build/FreeCAD "${app_name}/Contents/MacOS/FreeCAD"

# Add deployment target suffix to artifact name (e.g., "-macOS11" or "-macOS15")
deploy_target="${MACOS_DEPLOYMENT_TARGET:-11.0}"
artifact_base="$(python ../../../src/Tools/resolve_release_artifact_name.py ../../..)"
version_name="${artifact_base}-macOS${deploy_target%%.*}-$(uname -m)"
application_menu_name="VibeCAD"

echo -e "\################"
echo -e "version_name:  ${version_name}"
echo -e "################"

# Keep version parsing out of this shell script. macOS ships Bash 3.2, whose
# parsing of a quoted heredoc nested in process substitution corrupted the
# Python dictionary used by the previous implementation.
version_output="$(
    python3 ../scripts/resolve_macos_bundle_version.py ../../../version.json
)"
if [[ "${version_output}" != *"|"* ]] || [[ "${version_output#*|}" == *"|"* ]]; then
    echo "Invalid macOS bundle version output: ${version_output}" >&2
    exit 1
fi
short_version="${version_output%%|*}"
bundle_version="${version_output#*|}"

cp Info.plist.template "${conda_env}/../Info.plist"
sed -i "s/VIBECAD_SHORT_VERSION/${short_version}/" "${conda_env}/../Info.plist"
sed -i "s/VIBECAD_BUILD_VERSION/${bundle_version}/" "${conda_env}/../Info.plist"
sed -i "s/APPLICATION_MENU_NAME/${application_menu_name}/" "${conda_env}/../Info.plist"

pixi list -e default > "${app_name}/Contents/packages.txt"
sed -i '1s/.*/\nLIST OF PACKAGES:/' "${app_name}/Contents/packages.txt"

# move plugins into their final location (Library only exists for macOS < 15.0 builds)
if [ -d "${conda_env}/Library" ]; then
    mv "${conda_env}/Library" "${conda_env}/.."
fi

# move App Extensions (PlugIns) to the correct location for macOS registration
if [ -d "${conda_env}/PlugIns" ]; then
    mv "${conda_env}/PlugIns" "${conda_env}/.."
fi

python ../scripts/audit_macos_bundle.py \
    "${app_name}" \
    --forbid-prefix "${default_env_absolute}"

runtime_validator="$(cd ../scripts && pwd)/validate_vibecad_macos_runtime.py"

run_standalone_runtime_check() {
    local check="$1"
    "${conda_env}/bin/python" \
        "${runtime_validator}" \
        --prefix "${conda_env_absolute}" \
        --check "${check}"
}

run_freecad_runtime_check() {
    local check="$1"
    VIBECAD_RUNTIME_PREFIX="${conda_env_absolute}" \
    VIBECAD_RUNTIME_CHECK="${check}" \
    VIBECAD_RUNTIME_VALIDATOR="${runtime_validator}" \
        "${conda_env}/bin/freecadcmd" --safe-mode -c \
        "import os, runpy, sys; sys.argv = ['validator', '--prefix', os.environ['VIBECAD_RUNTIME_PREFIX'], '--check', os.environ['VIBECAD_RUNTIME_CHECK']]; runpy.run_path(os.environ['VIBECAD_RUNTIME_VALIDATOR'], run_name='__main__')"
}

echo "Running isolated VibeCAD macOS runtime smoke tests..."
for check in python anthropic keyring jsonschema mcp mcp-types openai tuf macos-keyring gemini-sdk; do
    run_standalone_runtime_check "${check}"
done

if ! "${conda_env}/bin/freecadcmd" --safe-mode --version; then
    echo "VibeCAD command-line smoke test failed; the macOS bundle cannot start." >&2
    exit 1
fi
if ! "${conda_env}/bin/freecadcmd" --safe-mode -c "from VibeCADGeometry import runtime_execution_smoke; result = runtime_execution_smoke(); print('VibeCAD geometry worker smoke ok', result['worker'])"; then
    echo "VibeCAD geometry worker smoke test failed; the macOS bundle cannot inspect geometry." >&2
    exit 1
fi
for check in \
    python pivy anthropic keyring jsonschema mcp mcp-types openai tuf macos-keyring gemini-sdk \
    provider-subprocess codex; do
    run_freecad_runtime_check "${check}"
done

echo "Running VibeCAD app launcher smoke test..."
"${app_name}/Contents/MacOS/FreeCAD" --vibecad-launcher-smoke

if [[ "${MACOS_SIGN_RELEASE:-false}" == "true" ]]; then
    # create the signed dmg
    ../../scripts/macos_sign_and_notarize.zsh \
        -p "${MACOS_KEYCHAIN_PROFILE:-VibeCAD}" \
        -k "${MACOS_SIGNING_KEY_ID:?MACOS_SIGNING_KEY_ID is required for signing}" \
        -n "${app_name}" \
        -v "VibeCAD" \
        -o "${version_name}.dmg"
else
    # Ad-hoc sign for local builds (required for QuickLook extensions to register)
    if [ -d "${app_name}/Contents/PlugIns" ]; then
        echo "Ad-hoc signing App Extensions with entitlements..."
        codesign --force --sign - \
            --entitlements ../../../src/MacAppBundle/QuickLook/modern/ThumbnailExtension.entitlements \
            "${app_name}/Contents/PlugIns/FreeCADThumbnailExtension.appex"
        codesign --force --sign - \
            --entitlements ../../../src/MacAppBundle/QuickLook/modern/PreviewExtension.entitlements \
            "${app_name}/Contents/PlugIns/FreeCADPreviewExtension.appex"
    fi
    echo "Ad-hoc signing app bundle..."
    codesign --force --sign - "${app_name}/Contents/packages.txt"
    if [ -f "${app_name}/Contents/Library/QuickLook/QuicklookFCStd.qlgenerator/Contents/MacOS/QuicklookFCStd" ]; then
        codesign --force --sign - "${app_name}/Contents/Library/QuickLook/QuicklookFCStd.qlgenerator/Contents/MacOS/QuicklookFCStd"
    fi
    codesign --force --deep --sign - "${app_name}"
    codesign --verify --deep --strict "${app_name}"
    "${app_name}/Contents/MacOS/FreeCAD" --vibecad-launcher-smoke

    # create the dmg
    echo "Staged macOS application size before DMG creation:"
    du -sk "${app_name}"
    dmg_size="$(
        python3 ../../scripts/calculate_macos_dmg_size.py "${app_name}"
    )"
    dmgbuild \
        -s dmg_settings.py \
        -Dapp_name="${app_name}" \
        -Dimage_size="${dmg_size}" \
        "VibeCAD" \
        "${version_name}.dmg"
fi

# create hash
sha256sum ${version_name}.dmg > ${version_name}.dmg-SHA256.txt
