/*

Settings for FreeCAD installer

These typically need to be modified for each FreeCAD release

*/

# Make the installer as small as possible
# Using /SOLID is usually better for file size but it can't be used if the original size is
# more than 2GB, if building with /SOLID fails try disabling it
# comment this or use /DFC_TEST_BUILD command line option for testing builds since it will reduce
# the time to create an installer a lot at the cost of a much greater file size.
# So assure it is active for release builds!
!ifndef FC_TEST_BUILD
    SetCompressor /SOLID lzma
!endif

#--------------------------------
# File locations
# !!! you may need to adjust them to the folders in your Windows system !!!
# can be specified with /D command line argument to makensis.exe
!ifndef FILES_FREECAD
    !define FILES_FREECAD "${__FILEDIR__}\FreeCAD"
!endif
!ifndef FILES_THUMBS
    !define FILES_THUMBS "${__FILEDIR__}\thumbnail"
!endif

# msvc redistributables location is required for LibPack builds but not conda
# when using a LibPack build set the redistributables directory location here
# or with /D command line argument to makensis.exe
#!define FILES_DEPS "${__FILEDIR__}\MSVCRedist"

#--------------------------------
# get version info from freecadcmd
!ifdef VIBECAD_VERSION_NSH
    # Release validation can inject a generated, disposable version file so the
    # NSIS script can be preprocessed without executing the packaged runtime.
    !include "${VIBECAD_VERSION_NSH}"
!else
    # Use the bundled interpreter directly. freecadcmd accepts a Python file as
    # an open-document argument and exits successfully without executing it.
    !system "$\"${FILES_FREECAD}\bin\python.exe$\" $\"${__FILEDIR__}\write_version_nsh.py$\"" = 0
    !include "${__FILEDIR__}\version.nsh"
    !delfile "${__FILEDIR__}\version.nsh"
!endif

!define APP_VERSION_EMERGENCY "" # legacy emergency-release compatibility
!define APP_EMERGENCY_DOT ""

!if "${APP_VERSION_SUFFIX}" == ""
    !define APP_RELEASE_VERSION "${APP_VERSION_MAJOR}.${APP_VERSION_MINOR}.${APP_VERSION_PATCH}"
!else
    !define APP_RELEASE_VERSION "${APP_VERSION_MAJOR}.${APP_VERSION_MINOR}.${APP_VERSION_PATCH}-${APP_VERSION_SUFFIX}"
!endif
!define APP_VERSION "${APP_RELEASE_VERSION} (Build ${APP_VERSION_BUILD})" # Version to display
!define APP_UPDATE_VERSION "${APP_VERSION_MAJOR}.${APP_VERSION_MINOR}.${APP_VERSION_PATCH}.${APP_VERSION_RELEASE_RANK}.${APP_VERSION_BUILD}"

#--------------------------------
# Installer file name
# Typical names for the release are "FreeCAD-020-Installer-1.exe" etc.

!ifndef ExeFile
    !define ExeFile "${APP_NAME}-${APP_RELEASE_VERSION}-build${APP_VERSION_BUILD}-Windows-x86_64-installer.exe"
!endif

#--------------------------------
# installer bit type - FreeCAD is only provided as 64bit build
!define MULTIUSER_USE_PROGRAMFILES64
