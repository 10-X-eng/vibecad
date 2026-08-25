# SPDX-License-Identifier: LGPL-2.1-or-later

SET(VibeCADAero_SRCS
    Init.py
    InitGui.py
    AeroCommandLoader.py
    Commands.py
    VibeCADAero.py
    AeroConfig.py
    AeroAirfoil.py
    AeroIcons.py
    AeroSolvers.py
    AeroDetachedAnalysis.py
    AeroResults.py
    AeroJSBSim.py
    AeroWorkspace.py
    AeroRepair.py
    AeroStamp.py
    AeroMass.py
    AeroFlightCard.py
    AeroPreview.py
    requirements-aero.txt
    README.md
)

SET(VibeCADAero_Icons
    icons/vibecad-aero-analyze.svg
)

SET(VibeCADAero_Data
    data/e63.dat
)

SET(VibeCADAero_Tests
    tests/__init__.py
    tests/conftest.py
    tests/test_airfoil.py
    tests/test_assistant_push.py
    tests/test_config.py
    tests/test_detached_analysis.py
    tests/test_icons.py
    tests/test_jsbsim.py
    tests/test_public_api.py
    tests/test_repair.py
    tests/test_results.py
    tests/test_solvers.py
    tests/test_workbench.py
    tests/test_flight_card.py
    tests/test_honesty.py
)

SET(VibeCADAero_All
    ${VibeCADAero_SRCS}
    ${VibeCADAero_Data}
    ${VibeCADAero_Icons}
    ${VibeCADAero_Tests}
)

SOURCE_GROUP("" FILES ${VibeCADAero_SRCS})

ADD_CUSTOM_TARGET(VibeCADAero ALL
    SOURCES ${VibeCADAero_All}
)

fc_copy_sources(VibeCADAero "${CMAKE_BINARY_DIR}/Mod/VibeCADAero" ${VibeCADAero_All})

INSTALL(
    FILES
        ${VibeCADAero_SRCS}
    DESTINATION
        Mod/VibeCADAero
)

INSTALL(
    FILES
        ${VibeCADAero_Data}
    DESTINATION
        Mod/VibeCADAero/data
)

INSTALL(
    FILES
        ${VibeCADAero_Icons}
    DESTINATION
        Mod/VibeCADAero/icons
)

if(BUILD_TEST)
    INSTALL(
        FILES
            ${VibeCADAero_Tests}
        DESTINATION
            Mod/VibeCADAero/tests
    )
endif()
