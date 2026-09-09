# SPDX-License-Identifier: LGPL-2.1-or-later

configure_file(freecad.rc.cmake ${CMAKE_CURRENT_BINARY_DIR}/freecad.rc)
configure_file(freecadCmd.rc.cmake ${CMAKE_CURRENT_BINARY_DIR}/freecadCmd.rc)
file(COPY vibecad.ico DESTINATION ${CMAKE_CURRENT_BINARY_DIR})

if(WIN32)
    set(VIBECAD_LAUNCHER_DESCRIPTION "VibeCAD portable launcher")
    set(VIBECAD_LAUNCHER_FILENAME "VibeCAD.exe")
    configure_file(
        vibecadPortableLauncher.rc.cmake
        ${CMAKE_CURRENT_BINARY_DIR}/vibecadPortableLauncher.rc
    )
    set(VIBECAD_LAUNCHER_DESCRIPTION "VibeCAD command-line portable launcher")
    set(VIBECAD_LAUNCHER_FILENAME "FreeCADCmd.exe")
    configure_file(
        vibecadPortableLauncher.rc.cmake
        ${CMAKE_CURRENT_BINARY_DIR}/vibecadCmdPortableLauncher.rc
    )
endif()

######################## FreeCADMain ########################
if(BUILD_GUI)

    SET(FreeCAD_SRCS
        ${CMAKE_CURRENT_BINARY_DIR}/freecad.rc
        vibecad.ico
        MainGui.cpp
    )

    SET(FreeCAD_LIBS
        FreeCADGui
    )

    if(NOT BUILD_DYNAMIC_LINK_PYTHON)
        # executables have to be linked against python libraries,
        # because extension modules are not.
        list(APPEND FreeCAD_LIBS
            ${Python3_LIBRARIES}
        )
    endif(NOT BUILD_DYNAMIC_LINK_PYTHON)

    add_executable(FreeCADMain WIN32 ${FreeCAD_SRCS})
    target_link_libraries(FreeCADMain ${FreeCAD_LIBS})
    if(BUILD_TEST)
        add_dependencies(FreeCADMain TestSources)
        add_dependencies(FreeCADMain TestGuiSources)
    endif()
    if (FREECAD_WARN_ERROR)
        target_compile_warn_error(FreeCADMain)
    endif()

    SET_BIN_DIR(FreeCADMain FreeCAD)

    if(WIN32)
        INSTALL(TARGETS FreeCADMain
            RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
            LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
        )
    elseif(APPLE AND NOT BUILD_WITH_CONDA)
        INSTALL(TARGETS FreeCADMain
        RUNTIME DESTINATION MacOS
            LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
        )
    else()
        INSTALL(TARGETS FreeCADMain
            RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
        )
    endif()
endif(BUILD_GUI)
######################## FreeCADMainCmd ########################

SET(FreeCADMainCmd_SRCS
    ${CMAKE_CURRENT_BINARY_DIR}/freecadCmd.rc
    vibecad.ico
    MainCmd.cpp
)

add_executable(FreeCADMainCmd ${FreeCADMainCmd_SRCS})
if(BUILD_TEST)
    add_dependencies(FreeCADMainCmd TestSources)
endif()

SET(FreeCADMainCmd_LIBS
    FreeCADApp
    ${QtCore_LIBRARIES}
    ${QtXml_LIBRARIES}
)

if(NOT BUILD_DYNAMIC_LINK_PYTHON)
    # executables have to be linked against python libraries,
    # because extension modules are not.
    list(APPEND FreeCADMainCmd_LIBS
        ${Python3_LIBRARIES}
    )
endif(NOT BUILD_DYNAMIC_LINK_PYTHON)

target_link_libraries(FreeCADMainCmd
    ${FreeCADMainCmd_LIBS}
)
if (FREECAD_WARN_ERROR)
    target_compile_warn_error(FreeCADMainCmd)
endif()

SET_BIN_DIR(FreeCADMainCmd FreeCADCmd)

if(WIN32)
    INSTALL(TARGETS FreeCADMainCmd
        RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
        LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    )
elseif(APPLE AND NOT BUILD_WITH_CONDA)
    INSTALL(TARGETS FreeCADMainCmd
        RUNTIME DESTINATION MacOS
        LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    )
else()
    INSTALL(TARGETS FreeCADMainCmd
        RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
    )
endif()

######################## VibeCAD portable launchers ########################
if(WIN32)
    if(BUILD_GUI)
        add_executable(VibeCADPortableLauncher WIN32
            VibeCADPortableLauncher.cpp
            ${CMAKE_CURRENT_BINARY_DIR}/vibecadPortableLauncher.rc
            vibecad.ico
        )
        target_compile_definitions(VibeCADPortableLauncher PRIVATE VIBECAD_GUI_LAUNCHER)
        target_link_libraries(VibeCADPortableLauncher PRIVATE user32)
        if(FREECAD_WARN_ERROR)
            target_compile_warn_error(VibeCADPortableLauncher)
        endif()
        SET_BIN_DIR(VibeCADPortableLauncher VibeCADPortableLauncher)
        INSTALL(TARGETS VibeCADPortableLauncher RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
    endif()

    add_executable(VibeCADCmdPortableLauncher
        VibeCADPortableLauncher.cpp
        ${CMAKE_CURRENT_BINARY_DIR}/vibecadCmdPortableLauncher.rc
        vibecad.ico
    )
    if(FREECAD_WARN_ERROR)
        target_compile_warn_error(VibeCADCmdPortableLauncher)
    endif()
    SET_BIN_DIR(VibeCADCmdPortableLauncher VibeCADCmdPortableLauncher)
    INSTALL(TARGETS VibeCADCmdPortableLauncher RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
endif()

######################## FreeCADMainPy ########################

SET(FreeCADMainPy_SRCS
    MainPy.cpp
)

add_library(FreeCADMainPy SHARED ${FreeCADMainPy_SRCS})

target_link_libraries(FreeCADMainPy FreeCADApp)
if (FREECAD_WARN_ERROR)
    target_compile_warn_error(FreeCADMainPy)
endif()

SET_BIN_DIR(FreeCADMainPy FreeCAD)
SET_PYTHON_PREFIX_SUFFIX(FreeCADMainPy)

if(MSVC)
    # The executable and Python module share a basename, but must not write
    # the same linker PDB when the two targets build in parallel.
    set_target_properties(FreeCADMainPy PROPERTIES
        PDB_NAME "FreeCADMainPy"
        PDB_NAME_DEBUG "FreeCADMainPy_d")
endif()

if(WIN32)
    INSTALL(TARGETS FreeCADMainPy
        RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
        LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    )
else(WIN32)
    INSTALL(TARGETS FreeCADMainPy
        LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    )
endif(WIN32)

######################## FreeCADGuiPy ########################
if(BUILD_GUI)

    SET(FreeCADGuiPy_SRCS
        FreeCADGuiPy.cpp
    )

    add_library(FreeCADGuiPy SHARED ${FreeCADGuiPy_SRCS})

    target_link_libraries(FreeCADGuiPy FreeCADGui)
    if (FREECAD_WARN_ERROR)
        target_compile_warn_error(FreeCADGuiPy)
    endif()

    SET_BIN_DIR(FreeCADGuiPy FreeCADGui)
    SET_PYTHON_PREFIX_SUFFIX(FreeCADGuiPy)
    if(WIN32)
        # Name clash with target "FreeCADGui"
        # Must be called "FreeCADGuiPy_d" and "FreeCADGuiPy" to work so override default
        set_target_properties(FreeCADGuiPy PROPERTIES PDB_NAME_DEBUG "FreeCADGuiPy_d")
        set_target_properties(FreeCADGuiPy PROPERTIES PDB_NAME_RELEASE "FreeCADGuiPy")
    endif(WIN32)

    if(WIN32)
        INSTALL(TARGETS FreeCADGuiPy
            RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
            LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
        )
    else(WIN32)
        INSTALL(TARGETS FreeCADGuiPy
            LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
        )
    endif(WIN32)
endif(BUILD_GUI)
