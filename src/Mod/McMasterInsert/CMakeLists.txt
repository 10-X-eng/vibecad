# SPDX-License-Identifier: LGPL-2.1-or-later

SET(McMasterInsert_SRCS
    Init.py
    InitGui.py
    Commands.py
    InstallUI.py
    McMasterInsert.py
    package.xml
    README.md
    McMasterWebKit.swift
    McMasterCatalog.swift
    McMasterCatalog.entitlements
)

if(WIN32 AND BUILD_GUI)
    # WebView2 is kept out of the main VibeCAD process. This small native
    # helper uses the installed Evergreen Edge runtime and a VibeCAD-owned
    # profile, avoiding any changes to FreeCAD's Qt/PySide dependency graph.
    set(MCMASTER_WEBVIEW2_VERSION "1.0.4129.50")
    set(MCMASTER_WEBVIEW2_SHA256
        "d3934f482d484b89fb4825df720c710664e1143a1e90f7b3a60794ef33f473d2"
    )
    set(MCMASTER_WEBVIEW2_ROOT
        "${CMAKE_BINARY_DIR}/_deps/mcmaster-webview2-${MCMASTER_WEBVIEW2_VERSION}"
    )
    set(MCMASTER_WEBVIEW2_PACKAGE
        "${MCMASTER_WEBVIEW2_ROOT}/microsoft.web.webview2.nupkg"
    )
    set(MCMASTER_WEBVIEW2_HEADER
        "${MCMASTER_WEBVIEW2_ROOT}/build/native/include/WebView2.h"
    )
    string(CONCAT MCMASTER_WEBVIEW2_URL
        "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/"
        "${MCMASTER_WEBVIEW2_VERSION}/microsoft.web.webview2."
        "${MCMASTER_WEBVIEW2_VERSION}.nupkg"
    )
    if(NOT EXISTS "${MCMASTER_WEBVIEW2_HEADER}")
        file(MAKE_DIRECTORY "${MCMASTER_WEBVIEW2_ROOT}")
        file(DOWNLOAD
            "${MCMASTER_WEBVIEW2_URL}"
            "${MCMASTER_WEBVIEW2_PACKAGE}"
            EXPECTED_HASH "SHA256=${MCMASTER_WEBVIEW2_SHA256}"
            STATUS MCMASTER_WEBVIEW2_DOWNLOAD_STATUS
            TLS_VERIFY ON
        )
        list(GET MCMASTER_WEBVIEW2_DOWNLOAD_STATUS 0 MCMASTER_WEBVIEW2_DOWNLOAD_CODE)
        if(NOT MCMASTER_WEBVIEW2_DOWNLOAD_CODE EQUAL 0)
            list(GET MCMASTER_WEBVIEW2_DOWNLOAD_STATUS 1 MCMASTER_WEBVIEW2_DOWNLOAD_ERROR)
            message(FATAL_ERROR "Could not download the pinned Microsoft WebView2 SDK: "
                "${MCMASTER_WEBVIEW2_DOWNLOAD_ERROR}")
        endif()
        file(ARCHIVE_EXTRACT
            INPUT "${MCMASTER_WEBVIEW2_PACKAGE}"
            DESTINATION "${MCMASTER_WEBVIEW2_ROOT}"
        )
    endif()

    if(CMAKE_SIZEOF_VOID_P EQUAL 4)
        set(MCMASTER_WEBVIEW2_ARCH "x86")
    elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "^(ARM64|arm64|aarch64)$")
        set(MCMASTER_WEBVIEW2_ARCH "arm64")
    else()
        set(MCMASTER_WEBVIEW2_ARCH "x64")
    endif()

    set(VIBECAD_ICON_PATH "${CMAKE_SOURCE_DIR}/src/Main/vibecad.ico")
    configure_file(
        McMasterCatalogWebView2.rc.cmake
        "${CMAKE_CURRENT_BINARY_DIR}/McMasterCatalogWebView2.rc"
    )
    add_executable(McMasterCatalogWebView2 WIN32
        McMasterCatalogWebView2.cpp
        "${CMAKE_CURRENT_BINARY_DIR}/McMasterCatalogWebView2.rc"
    )
    target_include_directories(McMasterCatalogWebView2 PRIVATE
        "${MCMASTER_WEBVIEW2_ROOT}/build/native/include"
    )
    target_link_libraries(McMasterCatalogWebView2 PRIVATE
        "${MCMASTER_WEBVIEW2_ROOT}/build/native/${MCMASTER_WEBVIEW2_ARCH}/WebView2LoaderStatic.lib"
        advapi32
        gdi32
        ole32
        shell32
        shlwapi
        user32
    )
    target_compile_definitions(McMasterCatalogWebView2 PRIVATE UNICODE _UNICODE)
    set_target_properties(McMasterCatalogWebView2 PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/Mod/McMasterInsert"
    )
    foreach(config DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
        set_target_properties(McMasterCatalogWebView2 PROPERTIES
            "RUNTIME_OUTPUT_DIRECTORY_${config}"
            "${CMAKE_BINARY_DIR}/Mod/McMasterInsert"
        )
    endforeach()
    INSTALL(TARGETS McMasterCatalogWebView2
        RUNTIME DESTINATION Mod/McMasterInsert
    )
endif()

SET(McMasterInsert_Icons
    icons/catalog.svg
    icons/import.svg
    icons/cache.svg
    icons/mcmaster-workbench.svg
)

SET(McMasterInsert_Tests
    tests/__init__.py
    tests/test_catalog.py
    tests/test_ribbon.py
)

SET(McMasterInsert_All
    ${McMasterInsert_SRCS}
    ${McMasterInsert_Icons}
    ${McMasterInsert_Tests}
)

SOURCE_GROUP("" FILES ${McMasterInsert_SRCS})

ADD_CUSTOM_TARGET(McMasterInsert ALL
    SOURCES ${McMasterInsert_All}
)

fc_copy_sources(McMasterInsert "${CMAKE_BINARY_DIR}/Mod/McMasterInsert" ${McMasterInsert_All})

INSTALL(
    FILES
        ${McMasterInsert_SRCS}
    DESTINATION
        Mod/McMasterInsert
)

INSTALL(
    FILES
        ${McMasterInsert_Icons}
    DESTINATION
        Mod/McMasterInsert/icons
)

if(BUILD_TEST)
    INSTALL(
        FILES
            ${McMasterInsert_Tests}
        DESTINATION
            Mod/McMasterInsert/tests
    )
endif()
