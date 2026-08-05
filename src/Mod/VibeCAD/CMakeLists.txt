# SPDX-License-Identifier: LGPL-2.1-or-later

set(VibeCAD_Scripts
    Init.py
    InitGui.py
    VibeCADAuth.py
    VibeCADCodex.py
    VibeCADComponentCatalog.py
    VibeCADCore.py
    VibeCADDebug.py
    VibeCADDesignReview.py
    VibeCADDocumentReferences.py
    VibeCADEditState.py
    VibeCADFasteners.py
    VibeCADFastenersGui.py
    VibeCADGrid.py
    VibeCADGeometry.py
    VibeCADGeometryInspection.py
    VibeCADPlacementInspection.py
    VibeCADGui.py
    VibeCADLegacyArchitecture.py
    VibeCADIntentMemory.py
    VibeCADIntentMemoryCompiler.py
    VibeCADMechanismEngine.py
    VibeCADMechanismGeometry.py
    VibeCADMCP.py
    VibeCADModelingSurface.py
    VibeCADNativeTransaction.py
    VibeCADObjectDeletion.py
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
    VibeCADVibeScriptDomains.py
    VibeCADVibeScriptDomainRuntime.py
    VibeCADVibeScriptDomainPublication.py
    VibeCADVibeScriptCAM.py
    vibescript_assembly_api.py
    vibescript_assembly_worker.py
    VibeCADAssemblyHierarchy.py
    VibeCADAssemblyBOM.py
    VibeCADAssemblySolverPolicy.py
    vibescript_domain_api.py
    vibescript_domain_worker.py
    vibescript_component_api.py
    vibescript_component_worker.py
    vibescript_worker_progress.py
    vibescript_draft_api.py
    vibescript_draft_worker.py
    vibescript_part_api.py
    vibescript_part_worker.py
    vibescript_partdesign_api.py
    vibescript_partdesign_worker.py
    vibescript_sketcher_api.py
    vibescript_sketcher_worker.py
    vibescript_surface_api.py
    vibescript_surface_worker.py
    vibescript_spreadsheet_api.py
    vibescript_spreadsheet_worker.py
    vibescript_material_api.py
    vibescript_material_worker.py
    vibescript_mesh_api.py
    vibescript_mesh_worker.py
    vibescript_meshpart_api.py
    vibescript_meshpart_worker.py
    vibescript_points_api.py
    vibescript_points_worker.py
    vibescript_reverse_engineering_api.py
    vibescript_reverse_engineering_worker.py
    vibescript_inspection_api.py
    vibescript_inspection_worker.py
    vibescript_robot_api.py
    vibescript_robot_worker.py
    vibescript_fem_api.py
    vibescript_fem_worker.py
    vibescript_cam_api.py
    vibescript_cam_worker.py
    vibescript_techdraw_api.py
    vibescript_techdraw_worker.py
    VibeCADPointArtifacts.py
    VibeCADWorkbenchTools.py
    requirements.txt
)

set(VibeCAD_Resources
    fasteners-provenance.json
    preferences-vibecad.svg
    vibecad-fastener-attach.svg
    vibecad-fastener-edit.svg
    vibecad-fastener-hole.svg
    vibecad-fastener-insert.svg
    vibecad-open-assistant.svg
    vibecad-send.svg
    vibecad-stop.svg
    vibecad-activity.svg
    vibecad-new-conversation.svg
    vibecad-prompt-starters.svg
)

set(VibeCAD_TestScripts)
if(BUILD_TEST AND BUILD_GUI)
    list(APPEND VibeCAD_TestScripts
        vibecad_tests/__init__.py
        vibecad_tests/TestDirectDomainTimeline.py
        vibecad_tests/TestDirectModelTimeline.py
        vibecad_tests/assembly_simulation_playback_gui_integration.py
        vibecad_tests/grid_gui_integration.py
        vibecad_tests/mcp_gui_integration.py
        vibecad_tests/native_component_interface_integration.py
        vibecad_tests/partdesign_vibescript_api_integration.py
        vibecad_tests/qt_ribbon_theme_integration.py
        vibecad_tests/read_geometry_integration.py
    )
endif()

file(GLOB_RECURSE VibeCAD_ToolImplScripts
    CONFIGURE_DEPENDS
    RELATIVE ${CMAKE_CURRENT_SOURCE_DIR}
    tool_impl/*.py
)

add_custom_target(VibeCADScripts ALL
    SOURCES
        ${VibeCAD_Scripts}
        ${VibeCAD_ToolImplScripts}
        ${VibeCAD_Resources}
        ${VibeCAD_TestScripts}
)

fc_target_copy_resource(VibeCADScripts
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/Mod/VibeCAD
    ${VibeCAD_Scripts}
    ${VibeCAD_ToolImplScripts}
    ${VibeCAD_Resources}
    ${VibeCAD_TestScripts}
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
