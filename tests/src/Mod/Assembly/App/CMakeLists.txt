# SPDX-License-Identifier: LGPL-2.1-or-later

add_executable(Assembly_tests_run
        AssemblyObject.cpp
        SimulationFrameSnapshot.cpp
        SimulationPlaybackCache.cpp
)

# Ondsel's public matrix templates use the standard MSVC math constants.
target_compile_definitions(Assembly_tests_run PRIVATE _USE_MATH_DEFINES)
