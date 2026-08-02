set(VibeCAD_Theme_Files
    "Dark.cfg"
    "Light.cfg"
)

add_custom_target(VibeCAD_Themes_data ALL
    SOURCES ${VibeCAD_Theme_Files}
)

add_custom_command(
    TARGET VibeCAD_Themes_data
    PRE_BUILD
    COMMAND ${CMAKE_COMMAND} -E remove_directory
        "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}/Gui/PreferencePacks"
    VERBATIM
)

fc_copy_sources(
    VibeCAD_Themes_data
    "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}/Gui/Themes"
    ${VibeCAD_Theme_Files}
)

install(
    FILES ${VibeCAD_Theme_Files}
    DESTINATION ${CMAKE_INSTALL_DATADIR}/Gui/Themes
)

add_dependencies(FreeCADGui VibeCAD_Themes_data)

install(CODE
    "file(REMOVE_RECURSE \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_DATADIR}/Gui/PreferencePacks\")"
)
