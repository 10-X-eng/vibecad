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
