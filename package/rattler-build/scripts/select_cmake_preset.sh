#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

set -euo pipefail

platform="${1:-}"
if [[ -z "${platform}" ]]; then
    platform="$(uname -s)"
fi

case "${platform}" in
    linux-*|*linux*|Linux)
        echo "conda-linux-release"
        ;;
    osx-*|*darwin*|Darwin)
        echo "conda-macos-release"
        ;;
    *)
        echo "Unsupported build platform: ${platform}" >&2
        exit 2
        ;;
esac
