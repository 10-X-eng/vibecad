#!/usr/bin/env bash

set -euo pipefail

if [[ "$#" -ne 2 ]]; then
    echo "Usage: $0 <environment-root> <VibeCAD-module-directory>" >&2
    exit 2
fi

environment_root="${1%/}"
module_directory="${2%/}"

if [[ -z "${environment_root}" || -z "${module_directory}" \
      || "${environment_root}" == "/" || "${module_directory}" == "/" \
      || "$(basename "${module_directory}")" != "VibeCAD" ]]; then
    echo "Refusing to purge an invalid VibeCAD release path." >&2
    exit 2
fi

retired_directories=(
    "${module_directory}/build123d_runtime"
    "${module_directory}/openscad_runtime"
    "${module_directory}/__pycache__"
    "${module_directory}/tool_impl/service/__pycache__"
    "$(dirname "${module_directory}")/OpenSCAD"
    "${environment_root}/Mod/OpenSCAD"
    "${environment_root}/share/Mod/OpenSCAD"
    "${environment_root}/Library/Mod/OpenSCAD"
    "${environment_root}/Library/share/Mod/OpenSCAD"
    "${environment_root}/src/Mod/OpenSCAD"
)

retired_files=(
    "${module_directory}/VibeCADBuild123d.py"
    "${module_directory}/VibeCADOpenSCAD.py"
    "${module_directory}/build123d-requirements.txt"
    "${module_directory}/build123d_worker.py"
    "${module_directory}/openscad_freecad_worker.py"
    "${module_directory}/tool_impl/service/build123d_create_model.py"
    "${module_directory}/tool_impl/service/build123d_delete_model.py"
    "${module_directory}/tool_impl/service/build123d_edit_source.py"
    "${module_directory}/tool_impl/service/build123d_inspect_model.py"
    "${module_directory}/tool_impl/service/build123d_reconfigure_model.py"
    "${module_directory}/tool_impl/service/build123d_set_inputs.py"
    "${module_directory}/tool_impl/service/build123d_set_parameters.py"
    "${module_directory}/tool_impl/service/openscad_create_model.py"
    "${module_directory}/tool_impl/service/openscad_delete_model.py"
    "${module_directory}/tool_impl/service/openscad_edit_source.py"
    "${module_directory}/tool_impl/service/openscad_inspect_model.py"
    "${module_directory}/tool_impl/service/openscad_set_conversion_mode.py"
    "${module_directory}/tool_impl/service/openscad_set_parameters.py"
)

for path in "${retired_directories[@]}"; do
    rm -rf -- "${path}"
done
for path in "${retired_files[@]}"; do
    rm -f -- "${path}"
done

for path in "${retired_directories[@]}" "${retired_files[@]}"; do
    if [[ -e "${path}" || -L "${path}" ]]; then
        echo "Retired VibeCAD authoring artifact remains: ${path}" >&2
        exit 1
    fi
done

echo "Retired build123d/OpenSCAD authoring artifacts are absent."
