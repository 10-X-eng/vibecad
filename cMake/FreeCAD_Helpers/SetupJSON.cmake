macro(SetupJSON)

    if(FREECAD_USE_EXTERNAL_JSON)
        find_package(nlohmann_json REQUIRED)
        get_target_property(nlohmann_json_INCLUDE_DIRS
            nlohmann_json::nlohmann_json INTERFACE_INCLUDE_DIRECTORIES)
    else(FREECAD_USE_EXTERNAL_JSON)
        set(nlohmann_json_INCLUDE_DIRS ${CMAKE_SOURCE_DIR}/src/3rdParty/json/single_include)
    endif(FREECAD_USE_EXTERNAL_JSON)

endmacro(SetupJSON)
