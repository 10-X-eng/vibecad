#!/bin/bash

set -euo pipefail

prefix="${1:?Usage: $0 PREFIX}"
library_dir="${prefix}/lib"

[ -d "${library_dir}" ] || exit 0

# Mesa loads the host's hardware driver at runtime. Bundled libdrm libraries can
# be older than that driver and shadow the matching host copies, preventing GLX
# and EGL initialization. Keep these hardware-facing libraries on the host.
find "${library_dir}" -maxdepth 1 \
    \( -type f -o -type l \) \
    -name 'libdrm*.so*' \
    -print \
    -delete
