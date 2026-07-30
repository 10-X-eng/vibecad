if(BUILD_ADDONMGR)
  if( NOT EXISTS "${CMAKE_SOURCE_DIR}/src/Mod/AddonManager/CMakeLists.txt" )
    message(FATAL_ERROR "The Addon Manager has been moved into a git submodule. Please run
    git submodule update --init" )
  endif()
  add_subdirectory(AddonManager)
  if(BUILD_TEST)
    file(GLOB_RECURSE AddonManagerTest_SRCS
      CONFIGURE_DEPENDS
      RELATIVE ${CMAKE_CURRENT_SOURCE_DIR}
      AddonManager/AddonManagerTest/*
    )

    add_custom_target(AddonManagerTestScripts ALL
      SOURCES ${AddonManagerTest_SRCS}
    )

    fc_target_copy_resource(AddonManagerTestScripts
      ${CMAKE_CURRENT_SOURCE_DIR}
      ${CMAKE_BINARY_DIR}/Mod
      ${AddonManagerTest_SRCS}
    )

    install(DIRECTORY AddonManager/AddonManagerTest DESTINATION Mod/AddonManager)
  endif()
endif(BUILD_ADDONMGR)

if(BUILD_FASTENERS)
  if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/Fasteners/Init.py"
     OR NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/Fasteners/LICENSE")
    message(FATAL_ERROR "The pinned Fasteners workbench git submodule is not available. Please run
    git submodule update --init src/Mod/Fasteners")
  endif()

  file(GLOB_RECURSE FastenersModule_SRCS
    CONFIGURE_DEPENDS
    LIST_DIRECTORIES false
    RELATIVE ${CMAKE_CURRENT_SOURCE_DIR}
    Fasteners/*
  )
  list(FILTER FastenersModule_SRCS EXCLUDE REGEX
    "^Fasteners/(\\.git($|/)|.*__pycache__($|/)|.*\\.pyc$)"
  )

  add_custom_target(FastenersModule ALL
    SOURCES ${FastenersModule_SRCS}
  )

  fc_target_copy_resource(FastenersModule
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/Mod
    ${FastenersModule_SRCS}
  )

  install(
    DIRECTORY Fasteners/
    DESTINATION Mod/Fasteners
    PATTERN ".git*" EXCLUDE
    PATTERN "__pycache__" EXCLUDE
    PATTERN "*.pyc" EXCLUDE
  )
endif(BUILD_FASTENERS)

if(BUILD_ASSEMBLY)
  add_subdirectory(Assembly)
endif(BUILD_ASSEMBLY)

if(BUILD_DRAFT)
  add_subdirectory(Draft)
endif(BUILD_DRAFT)

if(BUILD_FEM)
    add_subdirectory(Fem)
endif(BUILD_FEM)

if(BUILD_HELP)
  add_subdirectory(Help)
endif(BUILD_HELP)

if(BUILD_IMPORT)
  add_subdirectory(Import)
endif(BUILD_IMPORT)

if(BUILD_INSPECTION)
  add_subdirectory(Inspection)
endif(BUILD_INSPECTION)

if(BUILD_JTREADER)
  add_subdirectory(JtReader)
endif(BUILD_JTREADER)

if(BUILD_MATERIAL)
  add_subdirectory(Material)
endif(BUILD_MATERIAL)

if(BUILD_MESH)
  add_subdirectory(Mesh)
endif(BUILD_MESH)

if(BUILD_MESH_PART)
  add_subdirectory(MeshPart)
endif(BUILD_MESH_PART)

if(BUILD_PART)
  add_subdirectory(Part)
endif(BUILD_PART)

if(BUILD_PART_DESIGN)
  add_subdirectory(PartDesign)
endif(BUILD_PART_DESIGN)

if(BUILD_CAM)
    add_subdirectory(CAM)
endif(BUILD_CAM)

if(BUILD_PLOT)
  add_subdirectory(Plot)
endif(BUILD_PLOT)

if(BUILD_POINTS)
  add_subdirectory(Points)
endif(BUILD_POINTS)

if(BUILD_REVERSEENGINEERING)
  add_subdirectory(ReverseEngineering)
endif(BUILD_REVERSEENGINEERING)

if(BUILD_ROBOT)
    if(EIGEN3_FOUND)
      add_subdirectory(Robot)
    else(EIGEN3_FOUND)
      MESSAGE("Due to the missing Eigen3 library the Robot module won't be built")
    endif(EIGEN3_FOUND)
endif(BUILD_ROBOT)

if(BUILD_SHOW)
    add_subdirectory(Show)
endif(BUILD_SHOW)

if(BUILD_SKETCHER)
    if(EIGEN3_FOUND)
      add_subdirectory(Sketcher)
    else(EIGEN3_FOUND)
      MESSAGE("Due to the missing Eigen3 library the Sketcher module won't be built")
    endif(EIGEN3_FOUND)
endif(BUILD_SKETCHER)

if(BUILD_SPREADSHEET)
  add_subdirectory(Spreadsheet)
endif(BUILD_SPREADSHEET)

if(BUILD_START)
    add_subdirectory(Start)
endif(BUILD_START)

if(BUILD_SURFACE)
  add_subdirectory(Surface)
endif(BUILD_SURFACE)

if(BUILD_MEASURE)
    add_subdirectory(Measure)
endif(BUILD_MEASURE)

if(BUILD_TECHDRAW)
    add_subdirectory(TechDraw)
endif(BUILD_TECHDRAW)

add_subdirectory(VibeCAD)

#add_subdirectory(TemplatePyMod)

if(BUILD_TEST)
  add_subdirectory(Test)
endif(BUILD_TEST)

if(BUILD_TUX)
    add_subdirectory(Tux)
endif(BUILD_TUX)

if(BUILD_WEB)
    add_subdirectory(Web)
endif(BUILD_WEB)
