# SPDX-License-Identifier: LGPL-2.1-or-later

add_executable(HostRuntimeIsolationHelper
        HostRuntimeIsolationHelper.cpp
)

add_executable(App_tests_run
        AsyncRecompute.cpp
        Application.cpp
        ApplicationDirectories.cpp
        BackupPolicy.cpp
        Branding.cpp
        ComplexGeoData.cpp
        Document.cpp
        DocumentObject.cpp
        DocumentObserver.cpp
        Expression.cpp
        ExpressionParser.cpp
        ElementMap.cpp
        ElementNamingUtils.cpp
        IndexedName.cpp
        License.cpp
        Link.cpp
        MappedElement.cpp
        MappedName.cpp
        Metadata.cpp
        ProjectFile.cpp
        Property.h
        Property.cpp
        PropertyExpressionEngine.cpp
        SemanticDependencyOrder.cpp
        StringHasher.cpp
        VarSet.cpp
        VRMLObject.cpp
)

add_dependencies(App_tests_run HostRuntimeIsolationHelper)

target_compile_definitions(App_tests_run PRIVATE
    DATADIR="${CMAKE_SOURCE_DIR}/data"
    HOST_RUNTIME_ISOLATION_HELPER="$<TARGET_FILE:HostRuntimeIsolationHelper>"
)

target_link_libraries(App_tests_run PRIVATE
    GTest::gmock_main
    ${Python3_LIBRARIES}
    FreeCADApp
)
