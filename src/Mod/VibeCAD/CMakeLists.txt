# SPDX-License-Identifier: LGPL-2.1-or-later

set(VibeCAD_Scripts
    Init.py
    InitGui.py
    VibeCADAuth.py
    VibeCADBuild123d.py
    VibeCADCodex.py
    VibeCADCore.py
    VibeCADDebug.py
    VibeCADDesignReview.py
    VibeCADGrid.py
    VibeCADGeometry.py
    VibeCADGui.py
    VibeCADIntentMemory.py
    VibeCADIntentMemoryCompiler.py
    VibeCADOpenSCAD.py
    VibeCADPreferences.py
    VibeCADPromptStarters.py
    VibeCADProvider.py
    VibeCADReferenceContracts.py
    VibeCADProject.py
    VibeCADSession.py
    VibeCADScriptedEditor.py
    VibeCADScriptedOwnership.py
    VibeCADScriptedProcess.py
    VibeCADScriptedPublication.py
    VibeCADTools.py
    VibeCADTransactions.py
    VibeCADVibeScript.py
    VibeCADWorkbenchTools.py
    build123d_worker.py
    openscad_freecad_worker.py
    vibescript_api.py
    vibescript_executor.py
    vibescript_worker.py
    build123d-requirements.txt
    requirements.txt
)

set(VibeCAD_Resources
    preferences-vibecad.svg
    vibecad-open-assistant.svg
    vibecad-send.svg
    vibecad-stop.svg
    vibecad-activity.svg
    vibecad-new-conversation.svg
    vibecad-prompt-starters.svg
)

file(GLOB_RECURSE VibeCAD_ToolImplScripts
    CONFIGURE_DEPENDS
    RELATIVE ${CMAKE_CURRENT_SOURCE_DIR}
    tool_impl/*.py
)

add_custom_target(VibeCADScripts ALL
    SOURCES ${VibeCAD_Scripts} ${VibeCAD_ToolImplScripts} ${VibeCAD_Resources}
)

fc_target_copy_resource(VibeCADScripts
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/Mod/VibeCAD
    ${VibeCAD_Scripts}
    ${VibeCAD_ToolImplScripts}
    ${VibeCAD_Resources}
)

install(
    FILES
        ${VibeCAD_Scripts}
        ${VibeCAD_Resources}
    DESTINATION
        Mod/VibeCAD
)

install(
    DIRECTORY
        tool_impl
    DESTINATION
        Mod/VibeCAD
    FILES_MATCHING
        PATTERN "*.py"
)

add_executable(VibeCADGeometryWorker
    VibeCADGeometryWorker.cpp
)

target_include_directories(VibeCADGeometryWorker
    PRIVATE
        ${OCC_INCLUDE_DIR}
        ${nlohmann_json_INCLUDE_DIRS}
)

target_link_directories(VibeCADGeometryWorker
    PRIVATE
        ${OCC_LIBRARY_DIR}
)

target_link_libraries(VibeCADGeometryWorker
    PRIVATE
        ${OCC_LIBRARIES}
        ${OCC_DEBUG_LIBRARIES}
)

if(FREECAD_WARN_ERROR)
    target_compile_warn_error(VibeCADGeometryWorker)
endif()

SET_BIN_DIR(VibeCADGeometryWorker VibeCADGeometryWorker)

install(
    TARGETS VibeCADGeometryWorker
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
)
