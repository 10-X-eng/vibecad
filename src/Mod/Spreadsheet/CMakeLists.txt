# SPDX-License-Identifier: LGPL-2.1-or-later

add_subdirectory(App)
if(BUILD_GUI)
    add_subdirectory(Gui)
endif(BUILD_GUI)

set(Spreadsheet_Scripts
    Init.py
    TestSpreadsheet.py
    TestSpreadsheetGui.py
    SpreadsheetTests/__init__.py
    SpreadsheetTests/TestVibeCADRibbonTools.py
    importXLSX.py
    test_importXLSX.py
)

if(BUILD_GUI)
    list (APPEND Spreadsheet_Scripts InitGui.py)
endif(BUILD_GUI)

add_custom_target(SpreadsheetScripts ALL
    SOURCES ${Spreadsheet_Scripts}
)

fc_target_copy_resource(SpreadsheetScripts
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/Mod/Spreadsheet
    ${Spreadsheet_Scripts}
)

foreach(script IN LISTS Spreadsheet_Scripts)
    get_filename_component(script_directory "${script}" DIRECTORY)
    install(FILES "${script}"
        DESTINATION "Mod/Spreadsheet/${script_directory}"
    )
endforeach()
