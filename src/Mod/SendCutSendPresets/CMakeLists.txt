# SPDX-License-Identifier: MIT
# Pure-Python SendCutSend Presets workbench (SheetMetal companion).

set(SendCutSendPresets_SRCS
    Init.py
    InitGui.py
    SCSCommand.py
    CustomPresets.py
    SCS_locator.py
    bend_actions.py
    bend_common.py
    pending_unfold.py
    sheetmetal_integration.py
    ui_theme.py
    package.xml
    metadata.txt
    README.md
    LICENSE
    CHANGELOG.md
    SMOKE.md
)

set(SendCutSendPresets_Data
    data/sendcutsend_bends.json
)

set(SendCutSendPresets_Icons
    resources/icons/SCS_Presets.svg
    resources/icons/SCS_Custom.svg
)

set(SendCutSendPresets_Tests
    tests/__init__.py
    tests/test_smoke.py
)

set(SendCutSendPresets_All
    ${SendCutSendPresets_SRCS}
    ${SendCutSendPresets_Data}
    ${SendCutSendPresets_Icons}
    ${SendCutSendPresets_Tests}
)

source_group("" FILES ${SendCutSendPresets_SRCS})

add_custom_target(SendCutSendPresets ALL
    SOURCES ${SendCutSendPresets_All}
)

fc_copy_sources(SendCutSendPresets "${CMAKE_BINARY_DIR}/Mod/SendCutSendPresets" ${SendCutSendPresets_All})

install(
    FILES ${SendCutSendPresets_SRCS}
    DESTINATION Mod/SendCutSendPresets
)

install(
    FILES ${SendCutSendPresets_Data}
    DESTINATION Mod/SendCutSendPresets/data
)

install(
    FILES ${SendCutSendPresets_Icons}
    DESTINATION Mod/SendCutSendPresets/resources/icons
)

if(BUILD_TEST)
    install(
        FILES ${SendCutSendPresets_Tests}
        DESTINATION Mod/SendCutSendPresets/tests
    )
endif()
