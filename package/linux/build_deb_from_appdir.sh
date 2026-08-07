#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: build_deb_from_appdir.sh --appdir PATH --output-dir PATH --version VERSION [--arch ARCH] [--artifact-basename NAME]

Builds an installable VibeCAD Debian package from the Linux AppDir produced by
package/rattler-build/linux/create_bundle.sh.
EOF
}

appdir=""
output_dir=""
version=""
arch="$(uname -m)"
artifact_basename=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --appdir)
            appdir="${2:-}"
            shift 2
            ;;
        --output-dir)
            output_dir="${2:-}"
            shift 2
            ;;
        --version)
            version="${2:-}"
            shift 2
            ;;
        --arch)
            arch="${2:-}"
            shift 2
            ;;
        --artifact-basename)
            artifact_basename="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "$appdir" || -z "$output_dir" || -z "$version" ]]; then
    usage >&2
    exit 2
fi

if [[ -n "$artifact_basename" && ! "$artifact_basename" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]]; then
    echo "Artifact basename contains unsupported characters: $artifact_basename" >&2
    exit 2
fi

appdir="$(readlink -f "$appdir")"
output_dir="$(mkdir -p "$output_dir" && readlink -f "$output_dir")"

if [[ ! -x "$appdir/AppRun" ]]; then
    echo "AppDir does not contain an executable AppRun: $appdir" >&2
    exit 1
fi

case "$arch" in
    x86_64|amd64)
        deb_arch="amd64"
        ;;
    aarch64|arm64)
        deb_arch="arm64"
        ;;
    *)
        echo "Unsupported Debian architecture: $arch" >&2
        exit 1
        ;;
esac

sanitize_version() {
    local raw="$1"
    if [[ "$raw" =~ ^[vV]ibecad[-_]?([0-9].*)$ ]]; then
        raw="${BASH_REMATCH[1]}"
    elif [[ "$raw" =~ ^[vV][0-9] ]]; then
        raw="${raw:1}"
    fi
    raw="${raw//_/-}"
    raw="$(printf '%s' "$raw" | sed -E 's/[^A-Za-z0-9.+:~-]+/~/g')"
    if [[ ! "$raw" =~ ^[0-9] ]]; then
        raw="0~${raw}"
    fi
    printf '%s' "$raw"
}

deb_version="$(sanitize_version "$version")"
package_name="vibecad"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

pkgroot="$workdir/${package_name}_${deb_version}_${deb_arch}"
install_root="$pkgroot/opt/vibecad/freecad"
mkdir -p "$install_root"
cp -a "$appdir/." "$install_root/"

mkdir -p "$pkgroot/usr/bin"
cat > "$pkgroot/usr/bin/vibecad" <<'EOF'
#!/bin/sh
exec /opt/vibecad/freecad/AppRun "$@"
EOF
chmod 0755 "$pkgroot/usr/bin/vibecad"

mkdir -p "$pkgroot/usr/share/applications"
cat > "$pkgroot/usr/share/applications/vibecad.desktop" <<'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=VibeCAD
GenericName=AI-native CAD
Comment=Design 3D parts with VibeCAD
Exec=vibecad %F
Icon=vibecad
Terminal=false
Categories=Graphics;Engineering;Science;
MimeType=application/x-extension-fcstd;application/x-extension-fcstd1;
StartupNotify=true
EOF

mkdir -p "$pkgroot/usr/share/icons/hicolor/scalable/apps"
if [[ -f "$install_root/org.freecad.FreeCAD.svg" ]]; then
    cp "$install_root/org.freecad.FreeCAD.svg" "$pkgroot/usr/share/icons/hicolor/scalable/apps/vibecad.svg"
elif [[ -f "$install_root/usr/share/icons/hicolor/scalable/apps/org.freecad.FreeCAD.svg" ]]; then
    cp "$install_root/usr/share/icons/hicolor/scalable/apps/org.freecad.FreeCAD.svg" "$pkgroot/usr/share/icons/hicolor/scalable/apps/vibecad.svg"
fi

installed_size="$(du -sk "$pkgroot" | awk '{print $1}')"
mkdir -p "$pkgroot/DEBIAN"
cat > "$pkgroot/DEBIAN/control" <<EOF
Package: ${package_name}
Version: ${deb_version}
Section: graphics
Priority: optional
Architecture: ${deb_arch}
Maintainer: VibeCAD <support@10x.engineering>
Installed-Size: ${installed_size}
Depends: bash, ca-certificates, fontconfig, libegl1, libgl1, libglib2.0-0, libx11-6, libxcb1, libxkbcommon-x11-0
Description: AI-native parametric CAD platform
 VibeCAD bundles the integrated AI-native CAD workbench, VibeCAD themes,
 bundled Python environment, bundled CAD dependencies, and desktop launch
 integration.
EOF

cat > "$pkgroot/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
EOF
chmod 0755 "$pkgroot/DEBIAN/postinst"

cat > "$pkgroot/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
EOF
chmod 0755 "$pkgroot/DEBIAN/postrm"

if [[ -n "$artifact_basename" ]]; then
    deb_path="$output_dir/${artifact_basename}-Linux-${deb_arch}.deb"
else
    # Preserve the original standalone-script behavior for existing callers.
    deb_path="$output_dir/${package_name}_${deb_version}_${deb_arch}.deb"
fi
# Speed up packaging by lowering the xz compression level (dpkg-deb defaults to -6).
# Staying on xz (rather than switching to zstd) keeps the package installable by the
# same set of dpkg/apt versions, so the supported OS list is unchanged.
dpkg-deb -Zxz -z1 --build --root-owner-group "$pkgroot" "$deb_path"
deb_filename="$(basename "$deb_path")"
(
    cd "$output_dir"
    sha256sum "$deb_filename" > "${deb_filename}-SHA256.txt"
)

echo "$deb_path"
