#!/bin/bash

set -e
set -x

conda_env="AppDir/usr"
artifact_base="$(python ../../../src/Tools/resolve_release_artifact_name.py ../../..)"
version_name="${artifact_base}-Linux-$(uname -m)"

# Build (and finalize) the AppDir. This is the shared input for both the AppImage
# and the Debian package, so it is a separate phase that must complete before
# either packaging step reads it. Once this returns the AppDir is not modified
# again, so the two packagers can run in parallel.
build_appdir() {
    rm -rf -- "AppDir"
    mkdir -p ${conda_env}
    cat > AppDir/AppRun <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
export PREFIX=${HERE}/usr
# export LD_LIBRARY_PATH=${HERE}/usr/lib${LD_LIBRARY_PATH:+':'}$LD_LIBRARY_PATH
export PYTHONHOME=${HERE}/usr
export PATH_TO_FREECAD_LIBDIR=${HERE}/usr/lib
# export QT_QPA_PLATFORM_PLUGIN_PATH=${HERE}/usr/plugins
# export QT_XKB_CONFIG_ROOT=${HERE}/usr/lib
export FONTCONFIG_FILE=/etc/fonts/fonts.conf
export FONTCONFIG_PATH=/etc/fonts

# Fix: Use X to run on Wayland
export QT_QPA_PLATFORM=xcb

# Show packages info if DEBUG env variable is set
if [ "$DEBUG" = 1 ]; then
    cat ${HERE}/packages.txt
fi

# SSL
# https://forum.freecad.org/viewtopic.php?f=4&t=34873&start=20#p327416
export SSL_CERT_FILE=$PREFIX/ssl/cacert.pem
# https://github.com/FreeCAD/FreeCAD-AppImage/pull/20
export GIT_SSL_CAINFO=$HERE/usr/ssl/cacert.pem

# Support for launching other applications (from /usr/bin)
# https://github.com/FreeCAD/FreeCAD-AppImage/issues/30
if [ ! -z "$1" ] && [ -e "$HERE/usr/bin/$1" ] ; then
    MAIN="$HERE/usr/bin/$1" ; shift
else
    MAIN="$HERE/usr/bin/freecad"
fi

exec "${MAIN}" "$@"
EOF

    ../scripts/install_vibecad_provider_deps.sh ../.pixi/envs/default
    ../scripts/install_vibecad_codex_runtime.sh \
        "../.pixi/envs/default/bin/python" \
        "../.pixi/envs/default/Mod/VibeCAD"
    ../scripts/purge_vibecad_retired_authoring_artifacts.sh \
        "../.pixi/envs/default" \
        "../.pixi/envs/default/Mod/VibeCAD"
    cp -a ../.pixi/envs/default/* ${conda_env}
    ../scripts/purge_vibecad_retired_authoring_artifacts.sh \
        "${conda_env}" \
        "${conda_env}/Mod/VibeCAD"

    echo -e "\nDelete unnecessary stuff"
    rm -rf ${conda_env}/include
    find ${conda_env} -name \*.a -delete

    mv ${conda_env}/bin ${conda_env}/bin_tmp
    mkdir ${conda_env}/bin
    cp ${conda_env}/bin_tmp/freecad ${conda_env}/bin/
    cp ${conda_env}/bin_tmp/freecadcmd ${conda_env}/bin
    cp ${conda_env}/bin_tmp/ccx ${conda_env}/bin/
    cp ${conda_env}/bin_tmp/python ${conda_env}/bin/
    cp ${conda_env}/bin_tmp/pip ${conda_env}/bin/
    cp ${conda_env}/bin_tmp/pyside6-rcc ${conda_env}/bin/
    cp ${conda_env}/bin_tmp/gmsh ${conda_env}/bin/
    cp ${conda_env}/bin_tmp/dot ${conda_env}/bin/
    cp ${conda_env}/bin_tmp/unflatten ${conda_env}/bin/
    cp ${conda_env}/bin_tmp/VibeCADGeometryWorker ${conda_env}/bin/
    rm -rf ${conda_env}/bin_tmp

    sed -i '1s|.*|#!/usr/bin/env python|' ${conda_env}/bin/pip

    echo -e "\nCopying Icon and Desktop file"
    cp ${conda_env}/share/applications/org.freecad.FreeCAD.desktop AppDir/
    sed -i 's/Exec=FreeCAD/Exec=AppRun/g' AppDir/org.freecad.FreeCAD.desktop
    cp ${conda_env}/share/icons/hicolor/scalable/apps/org.freecad.FreeCAD.svg AppDir/

    # Remove __pycache__ folders and .pyc files
    find . -path "*/__pycache__/*" -delete
    find . -name "*.pyc" -type f -delete

    # reduce size
    rm -rf ${conda_env}/conda-meta/
    rm -rf "${conda_env}/etc/conda/test-files"
    rm -rf ${conda_env}/doc/global/
    rm -rf ${conda_env}/share/gtk-doc/
    rm -rf ${conda_env}/lib/cmake/

    find . -name "*.h" -type f -delete
    find . -name "*.cmake" -type f -delete

    echo -e "\################"
    echo -e "version_name:  ${version_name}"
    echo -e "################"

    pixi list -e default > AppDir/packages.txt
    sed -i "1s/.*/\nLIST OF PACKAGES:/" AppDir/packages.txt

    echo "Running VibeCAD command-line smoke test..."
    if ! "${conda_env}/bin/freecadcmd" --safe-mode --version; then
        echo "VibeCAD command-line smoke test failed; the Linux bundle cannot start."
        exit 1
    fi
    for dependency in \
        anthropic \
        keyring \
        jsonschema \
        mcp \
        mcp_types \
        openai \
        tuf \
        secretstorage \
        keyring.backends.SecretService; do
        if ! "${conda_env}/bin/freecadcmd" --safe-mode -c "import importlib; importlib.import_module('${dependency}'); print('${dependency} import ok')"; then
            echo "VibeCAD Python dependency '${dependency}' failed to import; the Linux bundle is incomplete."
            exit 1
        fi
    done
    if ! "${conda_env}/bin/freecadcmd" --safe-mode -c "import importlib.util; assert importlib.util.find_spec('agents') is None; print('Retired OpenAI Agents module is absent')"; then
        echo "The retired OpenAI Agents module remains in the Linux bundle."
        exit 1
    fi
    if ! "${conda_env}/bin/freecadcmd" --safe-mode -c "from VibeCADProvider import _provider_subprocess_smoke; _provider_subprocess_smoke(); print('VibeCAD provider subprocess smoke ok')"; then
        echo "VibeCAD provider subprocess smoke test failed; the Linux bundle cannot run AI providers."
        exit 1
    fi
    if ! "${conda_env}/bin/freecadcmd" --safe-mode -c "from VibeCADCodex import runtime_execution_smoke; result = runtime_execution_smoke(); print('VibeCAD Codex app-server smoke ok', result['version'])"; then
        echo "VibeCAD Codex app-server smoke test failed; the Linux bundle cannot use ChatGPT subscriptions."
        exit 1
    fi
    if ! "${conda_env}/bin/freecadcmd" --safe-mode -c "from VibeCADGeometry import runtime_execution_smoke; result = runtime_execution_smoke(); print('VibeCAD geometry worker smoke ok', result['worker'])"; then
        echo "VibeCAD geometry worker smoke test failed; the Linux bundle cannot inspect geometry."
        exit 1
    fi
    if ! env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH \
        /usr/bin/python3 -m py_compile \
        "${conda_env}/Mod/McMasterInsert/McMasterCatalogWebKit.py"; then
        echo "VibeCAD McMaster WebKit helper is not valid Python."
        exit 1
    fi
    rm -rf -- "${conda_env}/Mod/McMasterInsert/__pycache__"

    # Finalize AppDir here so the packaging phases only ever read it.
    chmod a+x ./AppDir/AppRun
}

# Compress the finalized AppDir into an AppImage. Reads AppDir only.
make_appimage() {
    local appimagetool_path="./appimagetool-$(uname -m)"
    curl -fL \
      -o "${appimagetool_path}" \
      "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$(uname -m).AppImage"
    chmod a+x "${appimagetool_path}"

    echo -e "\nCreate the appimage"
    # export GPG_TTY=$(tty)
    # zstd compression level 19 (max "normal" level): the "ultra" level 22 roughly
    # doubles the mksquashfs time for a marginal size gain on a 4-core runner.
    "${appimagetool_path}" \
      --comp zstd \
      --mksquashfs-opt -Xcompression-level \
      --mksquashfs-opt 19 \
      -u "gh-releases-zsync|10-X-eng|vibecad|${GH_UPDATE_TAG}|VibeCAD*$(uname -m)*.AppImage.zsync" \
      AppDir ${version_name}.AppImage
      # -s --sign-key ${GPG_KEY_ID} \

    rm -f "${appimagetool_path}"

    echo -e "\nCreate hash"
    sha256sum ${version_name}.AppImage > ${version_name}.AppImage-SHA256.txt
    sha256sum ${version_name}.AppImage.zsync > ${version_name}.AppImage.zsync-SHA256.txt

}

# Phase dispatch. Defaults to the full pipeline so local invocations and other
# callers behave exactly as before; CI runs the phases separately so it can
# compress the AppImage and the .deb in parallel.
case "${CREATE_BUNDLE_PHASE:-all}" in
    appdir)
        build_appdir
        ;;
    appimage)
        make_appimage
        ;;
    all)
        build_appdir
        make_appimage
        ;;
    *)
        echo "Unknown CREATE_BUNDLE_PHASE: ${CREATE_BUNDLE_PHASE}" >&2
        exit 2
        ;;
esac
