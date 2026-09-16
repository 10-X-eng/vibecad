# SPDX-License-Identifier: LGPL-2.1-or-later
# Keep the upstream command/module names for existing SheetMetal documents.
set(SheetMetal_SRCS
    ExtrudedCutout.py
    InitGui.py
    SheetMetalBaseCmd.py
    SheetMetalBaseShapeCmd.py
    SheetMetalBend.py
    SheetMetalBendCuts.py
    SheetMetalBendSolid.py
    SheetMetalCmd.py
    SheetMetalCornerReliefCmd.py
    SheetMetalCutGui.py
    SheetMetalCutHistory.py
    SheetMetalEditGeometry.py
    SheetMetalEditable.py
    SheetMetalExtendCmd.py
    SheetMetalFoldCmd.py
    SheetMetalFormingCmd.py
    SheetMetalFromSolid.py
    SheetMetalGui.py
    SheetMetalHistoryOperations.py
    SheetMetalHem.py
    SheetMetalJunction.py
    SheetMetalKfactor.py
    SheetMetalMapping.py
    SheetMetalNativeEdit.py
    SheetMetalNewUnfolder.py
    SheetMetalOperations.py
    SheetMetalPresentation.py
    SheetMetalPreparation.py
    SheetMetalRMFGClient.py
    SheetMetalRMFGAuth.py
    SheetMetalRMFGCredentialStore.py
    SheetMetalRMFGGui.py
    SheetMetalRMFGSnapshot.py
    SheetMetalRMFGExport.py
    SheetMetalRMFGExportChild.py
    SheetMetalRMFGJobs.py
    SheetMetalRMFGManufacturing.py
    SheetMetalRMFGManufacturingGui.py
    SheetMetalRelief.py
    SheetMetalSketch.py
    SheetMetalSourceFeatures.py
    SheetMetalSourceCreationGui.py
    SheetMetalSourceGui.py
    SheetMetalSourceOperations.py
    SheetMetalText.py
    SheetMetalTools.py
    SheetMetalUnfoldCmd.py
    SheetMetalUnfolder.py
    SketchOnSheetMetalCmd.py
    TestSheetMetal.py
    current.py
    engineering_mode.py
    lookup.py
    smwb_locator.py
    LICENSE
    package.xml
    UPSTREAM.md
)
file(GLOB_RECURSE SheetMetal_RESOURCES CONFIGURE_DEPENDS
    RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}"
    "Resources/icons/*.svg"
    "Resources/panels/*.ui"
    "Resources/translations/*.qm"
)
file(GLOB SheetMetal_TESTS CONFIGURE_DEPENDS
    RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}"
    "SMTests/*.py"
)
set(SheetMetal_FILES ${SheetMetal_SRCS} ${SheetMetal_RESOURCES} ${SheetMetal_TESTS})
add_custom_target(SheetMetal ALL SOURCES ${SheetMetal_FILES})
fc_copy_sources(SheetMetal "${CMAKE_BINARY_DIR}/Mod/SheetMetal" ${SheetMetal_FILES})

foreach(source_file IN LISTS SheetMetal_FILES)
    get_filename_component(source_directory "${source_file}" DIRECTORY)
    install(FILES "${source_file}"
        DESTINATION "Mod/SheetMetal/${source_directory}"
    )
endforeach()
