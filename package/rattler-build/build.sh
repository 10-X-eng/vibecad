# CI forwards CCACHE_DIR to a persisted cache directory; local builds get an
# empty value from the recipe, so drop it and let ccache use its own default.
if [[ -z "${CCACHE_DIR:-}" ]]; then
    unset CCACHE_DIR
fi

# Zero ccache stats up front so the end-of-build dump reflects only this build's
# hit/miss ratio (CI only; local builds leave CCACHE_DIR unset).
if [[ -n "${CCACHE_DIR:-}" ]] && command -v ccache >/dev/null 2>&1; then
    ccache -z >/dev/null 2>&1 || true
fi

VIBECAD_BUILD_PLATFORM="${VIBECAD_TARGET_PLATFORM:-${HOST:-}}"
# Rattler executes this script from the source root.
CMAKE_PRESET="$(bash package/rattler-build/scripts/select_cmake_preset.sh "${VIBECAD_BUILD_PLATFORM}")"

if [[ ${CMAKE_PRESET} == conda-linux-release ]]; then

    # The Linux conda preset builds with Clang, but conda compiler activation
    # can still provide GCC-only flags.
    for flags_var in CFLAGS CXXFLAGS DEBUG_CFLAGS DEBUG_CXXFLAGS; do
        if [[ -n "${!flags_var:-}" ]]; then
            export "${flags_var}=${!flags_var//-fno-merge-constants/}"
        fi
    done
fi

if [[ ${CMAKE_PRESET} == conda-macos-release ]]; then
    # add hacks for osx here!
    echo "adding hacks for osx"

    # Install 3DConnexion (SpaceMouse) driver
    # Note: For local builds, comment out the following lines if you encounter issues
    # installing the driver or don't need SpaceMouse support
    /usr/bin/curl -o /tmp/3dFW.dmg -L 'https://download.3dconnexion.com/drivers/mac/10-7-0_B564CC6A-6E81-42b0-82EC-418EA823B81A/3DxWareMac_v10-7-0_r3411.dmg'
    hdiutil attach -readonly /tmp/3dFW.dmg
    sudo installer -package /Volumes/3Dconnexion\ Software/Install\ 3Dconnexion\ software.pkg -target /
    diskutil eject /Volumes/3Dconnexion\ Software
    CMAKE_PLATFORM_FLAGS+=(-DFREECAD_USE_3DCONNEXION:BOOL=ON)
    CMAKE_PLATFORM_FLAGS+=(-D3DCONNEXIONCLIENT_FRAMEWORK:FILEPATH="/Library/Frameworks/3DconnexionClient.framework")

    # We bundle conda's libc++, whose symbols are newer than the system libc++.
    # Export explicitly: newer compiler environments may not export CXXFLAGS.
    export CXXFLAGS="${CXXFLAGS:-} -D_LIBCPP_DISABLE_AVAILABILITY"

    # Use MACOS_DEPLOYMENT_TARGET from environment, default to 11.0 for backwards compat.
    # Note that CI sets this per target: 10.13 (Intel), 11.0 (ARM legacy), 15.0 (ARM modern)
    # - macOS 10.13+ Intel: legacy QuickLook generator (.qlgenerator)
    # - macOS 11-14 ARM: legacy QuickLook generator (.qlgenerator)
    # - macOS 15+ ARM: modern QuickLook App Extensions (.appex)
    DEPLOY_TARGET="${MACOS_DEPLOYMENT_TARGET:-11.0}"
    CMAKE_PLATFORM_FLAGS+=(-DCMAKE_OSX_DEPLOYMENT_TARGET=${DEPLOY_TARGET})

    # Patch Qt6's FindWrapOpenGL.cmake to not link AGL on macOS 10.15+
    # AGL framework was removed in macOS 10.15 Catalina and Qt6's cmake
    # unconditionally tries to link it on Apple platforms, causing build failures.
    # Only apply this patch for deployment targets >= 10.15.
    # See: https://github.com/conda-forge/qt-main-feedstock/issues/240
    DEPLOY_MAJOR=$(echo "$DEPLOY_TARGET" | cut -d. -f1)
    DEPLOY_MINOR=$(echo "$DEPLOY_TARGET" | cut -d. -f2)
    if [[ "$DEPLOY_MAJOR" -gt 10 ]] || [[ "$DEPLOY_MAJOR" -eq 10 && "$DEPLOY_MINOR" -ge 15 ]]; then
        FIND_WRAP_OPENGL="$PREFIX/lib/cmake/Qt6/FindWrapOpenGL.cmake"
        if [[ -f "$FIND_WRAP_OPENGL" ]]; then
            echo "Patching Qt6 FindWrapOpenGL.cmake to remove AGL linkage (not available on macOS 10.15+)..."
            sed -i.bak \
                -e '/find_library(WrapOpenGL_AGL/,/target_link_libraries.*__opengl_agl_fw_path/d' \
                "$FIND_WRAP_OPENGL"
        fi
    fi
fi

unset CMAKE_GENERATOR
unset CMAKE_GENERATOR_PLATFORM

cmake \
    ${CMAKE_ARGS} \
    ${CMAKE_PLATFORM_FLAGS[@]} \
    --preset ${CMAKE_PRESET} \
    -D CMAKE_IGNORE_PREFIX_PATH="/opt/homebrew;/usr/local/homebrew" \
    -D CMAKE_INCLUDE_PATH:FILEPATH="$PREFIX/include" \
    -D CMAKE_INSTALL_LIBDIR:FILEPATH="$PREFIX/lib" \
    -D CMAKE_INSTALL_PREFIX:FILEPATH="$PREFIX" \
    -D CMAKE_LIBRARY_PATH:FILEPATH="$PREFIX/lib" \
    -D CMAKE_PREFIX_PATH:FILEPATH="$PREFIX" \
    -D INSTALL_TO_SITEPACKAGES:BOOL=ON \
    -D OCC_INCLUDE_DIR:FILEPATH="$PREFIX/include/opencascade" \
    -D OCC_LIBRARY_DIR:FILEPATH="$PREFIX/lib" \
    -D Python_EXECUTABLE:FILEPATH="$PYTHON" \
    -D Python3_EXECUTABLE:FILEPATH="$PYTHON" \
    -D BUILD_DYNAMIC_LINK_PYTHON:BOOL=OFF \
    -D ENABLE_DEVELOPER_TESTS:BOOL=OFF \
    -B build \
    -S .

cmake --build build
cmake --install build

mv ${PREFIX}/bin/FreeCAD ${PREFIX}/bin/freecad || true
mv ${PREFIX}/bin/FreeCADCmd ${PREFIX}/bin/freecadcmd || true

# Report ccache effectiveness for this build (hit/miss ratio).
if [[ -n "${CCACHE_DIR:-}" ]] && command -v ccache >/dev/null 2>&1; then
    echo "===== ccache statistics (this build) ====="
    ccache -s || true
fi
