# SPDX-License-Identifier: LGPL-2.1-or-later

if (NOT FREECAD_USE_EXTERNAL_ONDSELSOLVER)
include_directories(
    ${CMAKE_SOURCE_DIR}/src/3rdParty/OndselSolver
)
else ()
    check_include_file_cxx(OndselSolver/enum.h HAVE_ONDSELSOLVER_H)
    if (NOT HAVE_ONDSELSOLVER_H)
        message(FATAL_ERROR "FREECAD_USE_EXTERNAL_ONDSELSOLVER is set but the required system includes are not available")
    endif ()
endif ()

add_subdirectory(App)

if(BUILD_GUI)
    add_subdirectory(Gui)
endif(BUILD_GUI)

set(Assembly_Scripts
    Init.py
    CommandCreateAssembly.py
    CommandCreateBom.py
    CommandInsertLink.py
    CommandInsertNewPart.py
    CommandSolveAssembly.py
    CommandCreateJoint.py
    CommandEditHistoryOperation.py
    CommandCreateView.py
    CommandCreateSimulation.py
    CommandExportASMT.py
    TestAssemblyWorkbench.py
    JointObject.py
    Preferences.py
    AssemblyImport.py
    SoSwitchMarker.py
    UtilsAssembly.py
)

if(BUILD_GUI)
    list (APPEND Assembly_Scripts InitGui.py)
endif(BUILD_GUI)

INSTALL(
    FILES
    ${Assembly_Scripts}
    DESTINATION
    Mod/Assembly
)

SET(AssemblyScripts_SRCS
    Assembly/__init__.py
)


SET(AssemblyTests_SRCS
    AssemblyTests/__init__.py
    AssemblyTests/TestCore.py
    AssemblyTests/TestCommandInsertLink.py
    AssemblyTests/TestVibeCADRibbonTools.py
    AssemblyTests/mocks/__init__.py
    AssemblyTests/mocks/MockGui.py
)


SET(all_files
    ${AssemblyTests_SRCS}
    ${AssemblyScripts_SRCS}
)

ADD_CUSTOM_TARGET(AssemblyScripts ALL
    SOURCES
        ${all_files}
        ${Assembly_Scripts}
)

ADD_CUSTOM_TARGET(AssemblyTests ALL)

# AssemblyScripts owns every staged Python file needed by the workbench and
# its tests, including the top-level runtime commands.
fc_copy_sources(
    AssemblyScripts
    "${CMAKE_BINARY_DIR}/Mod/Assembly"
    ${all_files}
    ${Assembly_Scripts}
)

# Keep the historical AssemblyTests target as a compatibility entry point.
# AssemblyScripts is the single owner of the copy commands, so targeted builds
# cannot race two writers for the same output.
add_dependencies(AssemblyTests AssemblyScripts)

# A targeted Assembly GUI build must stage the Python commands that implement
# its runtime behavior.  Previously only the test-copy target staged these
# files, so an incremental AssemblyGui build could run stale command code.
if(TARGET AssemblyGui)
    add_dependencies(AssemblyGui AssemblyScripts)
endif()

INSTALL(
    FILES
        ${AssemblyTests_SRCS}
    DESTINATION
        Mod/Assembly/AssemblyTests
)
INSTALL(
    FILES
        ${AssemblyScripts_SRCS}
    DESTINATION
        Mod/Assembly/Assembly
)
