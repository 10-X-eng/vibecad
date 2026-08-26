# SPDX-License-Identifier: LGPL-2.1-or-later

SET(VibeCADPrint_SRCS
    Init.py
    InitGui.py
    BambuStudio.py
    OrcaSlicer.py
    Commands.py
    PrintCommandLoader.py
    PrintIcons.py
    PrintPanel.py
    PrintPreferences.py
    PrintSetupDialog.py
    VibeCADPrint.py
)

SET(VibeCADPrint_Icons
    icons/vibecad-print-open.svg
    icons/vibecad-print-save.svg
    icons/vibecad-print-setup.svg
)

SET(VibeCADPrint_Tests
    tests/__init__.py
    tests/conftest.py
    tests/qt_dialog_layout.py
    tests/qt_bambu_setup_integration.py
    tests/qt_backend_switch_integration.py
    tests/qt_panel_integration.py
    tests/qt_panel_persistence_integration.py
    tests/qt_progress_integration.py
    tests/qt_selection_integration.py
    tests/qt_workbench_panel_integration.py
    tests/test_backend.py
    tests/test_bambu_backend.py
    tests/test_orca_backend.py
    tests/test_commands.py
    tests/test_export.py
    tests/test_preferences.py
    tests/test_workbench.py
)

SET(VibeCADPrint_All
    ${VibeCADPrint_SRCS}
    ${VibeCADPrint_Icons}
    ${VibeCADPrint_Tests}
)

SOURCE_GROUP("" FILES ${VibeCADPrint_SRCS})

ADD_CUSTOM_TARGET(VibeCADPrint ALL SOURCES ${VibeCADPrint_All})

fc_copy_sources(VibeCADPrint "${CMAKE_BINARY_DIR}/Mod/VibeCADPrint" ${VibeCADPrint_All})

INSTALL(FILES ${VibeCADPrint_SRCS} DESTINATION Mod/VibeCADPrint)
INSTALL(FILES ${VibeCADPrint_Icons} DESTINATION Mod/VibeCADPrint/icons)

if(BUILD_TEST)
    INSTALL(FILES ${VibeCADPrint_Tests} DESTINATION Mod/VibeCADPrint/tests)
endif()
