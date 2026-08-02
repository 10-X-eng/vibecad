
SET(Stylesheets_Files
    "defaults.qss"
    "VibeDark.qss"
    "VibeLight.qss"
)

SET(Parameters_Files
    "parameters/Dark.yaml"
    "parameters/Light.yaml"
)

SET(Overlay_Stylesheets_Files
    "overlay/VibeDark_Overlay.qss"
    "overlay/VibeLight_Overlay.qss"
)

# Find all the image files
FILE(GLOB Images_Files RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}/images_dark-light/*.svg"
    "${CMAKE_CURRENT_SOURCE_DIR}/images_dark-light/*.png"
)

SOURCE_GROUP("Images_dark-light" FILES ${Images_Files})

FILE(GLOB Overlay_icons RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}/overlay/icons/*.svg"
)

FILE(GLOB Overlay_Files RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}/overlay/*.svg"
    "${CMAKE_CURRENT_SOURCE_DIR}/overlay/*.png"
)

SOURCE_GROUP("overlay_icons" FILES ${Overlay_icons})
SOURCE_GROUP("overlay" FILES ${Overlay_Files})


ADD_CUSTOM_TARGET(Stylesheets_data ALL
    SOURCES ${Stylesheets_Files} ${Parameters_Files} ${Images_Files} ${Overlay_Stylesheets_Files} ${Overlay_icons} ${Overlay_Files}
)

ADD_CUSTOM_COMMAND(
    TARGET Stylesheets_data
    PRE_BUILD
    COMMAND ${CMAKE_COMMAND} -E rm -f
        "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/FreeCAD.qss"
        "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/parameters/FreeCAD Dark.yaml"
        "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/parameters/FreeCAD Light.yaml"
        "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/overlay/Freecad Overlay.qss"
    COMMAND ${CMAKE_COMMAND} -E remove_directory
        "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/images_classic"
    VERBATIM
)

fc_copy_sources(Stylesheets_data "${CMAKE_BINARY_DIR}/${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets"
                                  ${Stylesheets_Files}
                                  ${Parameters_Files}
                                  ${Images_Files}
                                  ${Overlay_Stylesheets_Files}
                                  ${Overlay_icons}
                                  ${Overlay_Files})

INSTALL(
    FILES
        ${Stylesheets_Files}
    DESTINATION
        ${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets
)
INSTALL(
        FILES
        ${Parameters_Files}
        DESTINATION
        ${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/parameters
)
INSTALL(
    FILES
        ${Overlay_Stylesheets_Files}
        ${Overlay_Files}
    DESTINATION
        ${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/overlay
)
INSTALL(
    FILES
        ${Overlay_icons}
    DESTINATION
        ${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/overlay/icons
)
INSTALL(
    FILES
         ${Images_Files}
    DESTINATION
        ${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/images_dark-light
)

add_dependencies(FreeCADGui Stylesheets_data)

INSTALL(CODE
    "file(REMOVE
        \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/FreeCAD.qss\"
        \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/parameters/FreeCAD Dark.yaml\"
        \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/parameters/FreeCAD Light.yaml\"
        \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/overlay/Freecad Overlay.qss\"
    )
    file(REMOVE_RECURSE
        \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_DATADIR}/Gui/Stylesheets/images_classic\"
    )"
)
