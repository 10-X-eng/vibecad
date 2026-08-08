/*
init.nsh

Initialization functions
*/

#--------------------------------
# User initialization

Var FCLangName

Function InitUser

  # Get FreeCAD language
  
  ReadRegStr $FCLangName SHELL_CONTEXT "${APP_REGKEY_SETUP}" "FreeCAD Language"
  
  ${If} $FCLangName != ""
    StrCpy $LangName $FCLangName
  ${EndIf}
  
FunctionEnd

#--------------------------------
# Installed-version discovery and clean replacement

Function FindInstalledVibeCAD

  StrCpy $OldVersionNumber ""
  StrCpy $VibeCADInstalledBuild ""
  StrCpy $VibeCADInstalledDisplayVersion ""
  StrCpy $VibeCADInstalledDisposition "none"
  StrCpy $VibeCADInstalledInstallRoot ""
  StrCpy $VibeCADInstalledPatch ""
  StrCpy $VibeCADInstalledReleaseVersion ""
  StrCpy $VibeCADInstalledUninstallString ""
  StrCpy $VibeCADInstalledUpdateVersion ""

  # Find the highest installed patch in this major/minor series. Historical
  # VibeCAD/FreeCAD installers used one registry key per patch release.
  IntOp $4 ${APP_VERSION_PATCH} + 20
  ${for} $5 0 $4
    StrCpy $R0 "${APP_VERSION_MAJOR}${APP_VERSION_MINOR}$5"
    StrCpy $R2 "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}$R0"
    ReadRegStr $0 SHCTX "$R2" "DisplayVersion"
    ${if} $0 == ""
      # Preserve discovery of the legacy emergency-release key shape.
      StrCpy $R0 "${APP_VERSION_MAJOR}${APP_VERSION_MINOR}$51"
      StrCpy $R2 "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}$R0"
      ReadRegStr $0 SHCTX "$R2" "DisplayVersion"
    ${endif}
    ${if} $0 != ""
      StrCpy $OldVersionNumber $R0
      StrCpy $VibeCADInstalledPatch $5
      StrCpy $VibeCADInstalledDisplayVersion $0
      ReadRegStr $VibeCADInstalledUninstallString SHCTX "$R2" "UninstallString"
      StrCpy $R3 "SOFTWARE\${APP_NAME}$OldVersionNumber"
      ReadRegStr $VibeCADInstalledInstallRoot SHCTX "$R3" ""
      ReadRegStr $VibeCADInstalledReleaseVersion SHCTX "$R3" "ReleaseVersion"
      ReadRegStr $VibeCADInstalledUpdateVersion SHCTX "$R3" "UpdateVersion"
      ClearErrors
      ReadRegDWORD $1 SHCTX "$R3" "Build"
      ${if} ${Errors}
        StrCpy $VibeCADInstalledBuild ""
        ClearErrors
      ${else}
        StrCpy $VibeCADInstalledBuild $1
      ${endif}
    ${endif}
  ${next}

FunctionEnd

Function SelectExistingVibeCADInstallMode

  # The MultiUser plug-in normally restores the install scope from the target
  # patch's registry key. Search the entire major/minor series as a migration
  # fallback so a new patch still updates the existing per-user/per-machine
  # installation instead of creating a second copy in another scope.
  StrCpy $6 ""
  StrCpy $7 ""
  IntOp $4 ${APP_VERSION_PATCH} + 20
  ${for} $5 0 $4
    StrCpy $R0 "${APP_VERSION_MAJOR}${APP_VERSION_MINOR}$5"
    ReadRegStr $0 HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}$R0" "DisplayVersion"
    ${if} $0 != ""
      ReadRegStr $6 HKLM "SOFTWARE\${APP_NAME}$R0" ""
    ${endif}
    ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}$R0" "DisplayVersion"
    ${if} $0 != ""
      ReadRegStr $7 HKCU "SOFTWARE\${APP_NAME}$R0" ""
    ${endif}
  ${next}

  ${if} $6 != ""
  ${andif} $7 == ""
    Call MultiUser.InstallMode.AllUsers
  ${elseif} $7 != ""
  ${andif} $6 == ""
    Call MultiUser.InstallMode.CurrentUser
  ${elseif} $6 != ""
  ${andif} $7 != ""
  ${andif} $VibeCADUpdateInstallRoot != ""
    GetFullPathName $6 "$6"
    GetFullPathName $7 "$7"
    GetFullPathName $0 "$VibeCADUpdateInstallRoot"
    ${if} $0 == $6
      Call MultiUser.InstallMode.AllUsers
    ${elseif} $0 == $7
      Call MultiUser.InstallMode.CurrentUser
    ${endif}
  ${endif}

FunctionEnd

Function ClassifyInstalledVibeCAD

  StrCpy $VibeCADInstalledDisposition "unknown"

  # Installers produced after this change persist a fully sortable numeric
  # identity. It includes semantic version, prerelease rank, and build.
  !if "${APP_VERSION_ORDER_KNOWN}" == "1"
    ${if} $VibeCADInstalledUpdateVersion != ""
      ${VersionCompare} "${APP_UPDATE_VERSION}" "$VibeCADInstalledUpdateVersion" $0
      ${if} $0 == "1"
        StrCpy $VibeCADInstalledDisposition "upgrade"
      ${elseif} $0 == "0"
        StrCpy $VibeCADInstalledDisposition "repair"
      ${else}
        StrCpy $VibeCADInstalledDisposition "downgrade"
      ${endif}
      Return
    ${endif}
  !endif

  # Compatibility with already-published installers: exact public releases
  # have always persisted ReleaseVersion and Build separately.
  ${if} $VibeCADInstalledReleaseVersion == "${APP_RELEASE_VERSION}"
  ${andif} $VibeCADInstalledBuild != ""
    ${if} $VibeCADInstalledBuild < ${APP_VERSION_BUILD}
      StrCpy $VibeCADInstalledDisposition "upgrade"
    ${elseif} $VibeCADInstalledBuild == ${APP_VERSION_BUILD}
      StrCpy $VibeCADInstalledDisposition "repair"
    ${else}
      StrCpy $VibeCADInstalledDisposition "downgrade"
    ${endif}
    Return
  ${endif}

  # Patch releases have an unambiguous order even for a legacy install.
  ${if} $VibeCADInstalledPatch != ""
    ${if} $VibeCADInstalledPatch < ${APP_VERSION_PATCH}
      StrCpy $VibeCADInstalledDisposition "upgrade"
      Return
    ${elseif} $VibeCADInstalledPatch > ${APP_VERSION_PATCH}
      StrCpy $VibeCADInstalledDisposition "downgrade"
      Return
    ${endif}
  ${endif}

  # A final release sorts after a legacy prerelease of the same patch. A
  # prerelease must never replace an installed final release automatically.
  !if "${APP_VERSION_SUFFIX}" == ""
    ${if} $VibeCADInstalledReleaseVersion != ""
    ${andif} $VibeCADInstalledReleaseVersion != "${APP_VERSION_MAJOR}.${APP_VERSION_MINOR}.${APP_VERSION_PATCH}"
      StrCpy $VibeCADInstalledDisposition "upgrade"
    ${endif}
  !else
    ${if} $VibeCADInstalledReleaseVersion == "${APP_VERSION_MAJOR}.${APP_VERSION_MINOR}.${APP_VERSION_PATCH}"
      StrCpy $VibeCADInstalledDisposition "downgrade"
    ${endif}
  !endif

FunctionEnd

Function BeginManualVibeCADReplacement

  StrCpy $VibeCADUpdateMode "manual"
  StrCpy $VibeCADUpdateInstallRoot $VibeCADInstalledInstallRoot
  Call ValidateVibeCADUpdateInstallRoot
  ${if} ${Errors}
    StrCpy $VibeCADUpdateMode "false"
    MessageBox MB_OK|MB_ICONSTOP "$(InvalidExistingInstall)" /SD IDOK
    SetErrorLevel 28
    Quit
  ${endif}
  ClearErrors

FunctionEnd

Function VibeCADDirectoryPagePre

  # A replacement must use the registered installation root. Skipping the
  # directory page prevents a clean upgrade from being redirected midway.
  ${if} $VibeCADUpdateMode == "install"
  ${orif} $VibeCADUpdateMode == "manual"
    Abort
  ${endif}

FunctionEnd

#--------------------------------
# MultiUser custom method

Function PostMultiUserPageInit
  Call FindInstalledVibeCAD

  ${if} $OldVersionNumber == ""
    Return
  ${endif}

  # The verified in-app updater already supplies a silent, pinned install root.
  # Preserve that path while sharing the same clean replacement sections.
  ${if} $VibeCADUpdateMode != "false"
    Return
  ${endif}

  Call ClassifyInstalledVibeCAD

  ${if} $VibeCADInstalledDisposition == "upgrade"
    MessageBox MB_OKCANCEL|MB_ICONINFORMATION "$(UpgradeInstalled)" /SD IDOK IDOK AcceptManualReplacement
    Goto CancelManualReplacement
  ${elseif} $VibeCADInstalledDisposition == "repair"
    MessageBox MB_YESNO|MB_ICONQUESTION "$(RepairInstalled)" /SD IDNO IDYES AcceptManualReplacement
    Goto CancelManualReplacement
  ${elseif} $VibeCADInstalledDisposition == "downgrade"
    MessageBox MB_OK|MB_ICONSTOP "$(DowngradeBlocked)" /SD IDOK
    SetErrorLevel 27
    Quit
  ${else}
    MessageBox MB_YESNO|MB_ICONEXCLAMATION "$(ReplaceUnknownInstalled)" /SD IDNO IDYES AcceptManualReplacement
    Goto CancelManualReplacement
  ${endif}

  AcceptManualReplacement:
    Call BeginManualVibeCADReplacement
    Return

  CancelManualReplacement:
    ${if} ${Silent}
      Quit
    ${else}
      Abort
    ${endif}
FunctionEnd


#--------------------------------
# visible installer sections

Section "!${APP_NAME}" SecCore
 SectionIn RO
SectionEnd

Section "$(SecFileAssocTitle)" SecFileAssoc
 StrCpy $CreateFileAssociations "true" 
SectionEnd

Section "$(SecDesktopTitle)" SecDesktop
 StrCpy $CreateDesktopIcon "true"
SectionEnd

# Section descriptions
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
!insertmacro MUI_DESCRIPTION_TEXT ${SecCore} "$(SecCoreDescription)"
!insertmacro MUI_DESCRIPTION_TEXT ${SecFileAssoc} "$(SecFileAssocDescription)"
!insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "$(SecDesktopDescription)"
!insertmacro MUI_FUNCTION_DESCRIPTION_END


# .onInit must be here after the section definition because we have to set
# the selection states of the dictionary sections
Function .onInit

  StrCpy $VibeCADUpdateMode "false"
  StrCpy $VibeCADUpdateInstallRoot ""
  StrCpy $OldVersionNumber ""
  ${GetParameters} $R8
  ClearErrors
  ${GetOptions} $R8 "/VIBECADUPDATE" $R9
  ${IfNot} ${Errors}
    StrCpy $VibeCADUpdateMode "install"
  ${EndIf}
  ClearErrors
  ${GetOptions} $R8 "/VIBECADROLLBACK" $R9
  ${IfNot} ${Errors}
    StrCpy $VibeCADUpdateMode "rollback"
  ${EndIf}
  ClearErrors
  ${GetOptions} $R8 "/VIBECADINSTALLROOT=" $VibeCADUpdateInstallRoot

  ${if} $VibeCADUpdateMode != "false"
   ${IfNot} ${Silent}
    SetErrorLevel 25
    Quit
   ${EndIf}
  ${endif}

  ReadRegStr $R0 HKLM "SOFTWARE\Microsoft\Windows NT\CurrentVersion" CurrentVersion
  ${if} $R0 == "5.0" # 2000
  ${orif} $R0 == "5.1" # XP
  ${orif} $R0 == "5.2" # 2003
  ${orif} $R0 == "6.0" # Vista
  ${orif} $R0 == "6.1" # 7
    MessageBox MB_OK|MB_ICONSTOP "${APP_NAME} ${APP_VERSION} requires Windows 8 or newer." /SD IDOK
    Quit
  ${endif}
  
  # check if it is a 64bit system
  ${if} ${RunningX64}
   SetRegView 64
   !define LIBRARY_X64
  ${endif}
  
  # Check that FreeCAD is not currently running
  StrCpy $R1 0
  CheckVibeCADProcess:
  ${nsProcess::FindProcess} ${BIN_FREECAD} $R0
  # if running result is '0', if not running it is '603'
  ${if} $R0 == "0"
   ${if} $VibeCADUpdateMode != "false"
    IntOp $R1 $R1 + 1
    ${if} $R1 >= 600
     ${nsProcess::Unload}
     SetErrorLevel 20
     Quit
    ${endif}
    Sleep 500
    Goto CheckVibeCADProcess
   ${else}
    MessageBox MB_OK|MB_ICONSTOP "$(UnInstallRunning)" /SD IDOK
    Abort
   ${endif}
  ${endif}
  # plugin must be unloaded
  ${nsProcess::Unload}
  
  # initialize the multi-user installer UI
  !insertmacro MULTIUSER_INIT
  Call SelectExistingVibeCADInstallMode

  # this can be reset to "true" in section SecDesktop
  StrCpy $CreateDesktopIcon "false"
  StrCpy $CreateFileAssociations "false"
 
  ${IfNot} ${Silent}
    # Show banner while installer is initializing 
    Banner::show /NOUNLOAD "Checking system"
    Banner::destroy
  ${EndIf}

  # if installer runs silent the post install mode page routine has to be called here
  ${If} ${Silent}
    Call PostMultiUserPageInit
  ${endif}

  ${if} $VibeCADUpdateMode != "false"
    Call ValidateVibeCADUpdateInstallRoot
    ${If} ${Errors}
      SetErrorLevel 25
      Quit
    ${EndIf}
  ${endif}

  ${if} $VibeCADUpdateMode == "rollback"
    Call RestoreVibeCADUpdateBackup
    SetErrorLevel 24
    ${IfNot} ${Errors}
      SetErrorLevel 0
    ${EndIf}
    Quit
  ${endif}

FunctionEnd

Function ValidateVibeCADUpdateInstallRoot

  ${if} $VibeCADUpdateInstallRoot == ""
    SetErrors
    Return
  ${endif}
  StrCpy $R2 "${APP_REGKEY}"
  ${if} $OldVersionNumber != ""
    StrCpy $R2 "SOFTWARE\${APP_NAME}$OldVersionNumber"
  ${endif}
  ReadRegStr $R3 SHCTX "$R2" ""
  ${if} $R3 == ""
    SetErrors
    Return
  ${endif}
  GetFullPathName $R3 "$R3"
  GetFullPathName $VibeCADUpdateInstallRoot "$VibeCADUpdateInstallRoot"
  StrCmp $R3 $VibeCADUpdateInstallRoot 0 ValidateVibeCADUpdateInstallRootFailed
  IfFileExists "$R3\bin\VibeCAD.exe" 0 ValidateVibeCADUpdateInstallRootFailed
  StrCpy $INSTDIR $R3
  ClearErrors
  Return

  ValidateVibeCADUpdateInstallRootFailed:
    SetErrors
    Return

FunctionEnd

Function RestoreVibeCADUpdateBackup

  StrCpy $VibeCADUpdateBackupDir "$INSTDIR.vibecad-rollback"
  StrCpy $VibeCADUpdateFailedDir "$INSTDIR.vibecad-failed"
  IfFileExists "$VibeCADUpdateBackupDir\bin\VibeCAD.exe" 0 RestoreVibeCADUpdateFailed
  IfFileExists "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" 0 RestoreVibeCADUpdateFailed
  ReadINIStr $R2 "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "UninstallKey"
  ReadINIStr $R3 "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "AppKey"
  ${if} $R2 == ""
  ${orif} $R3 == ""
    Goto RestoreVibeCADUpdateFailed
  ${endif}
  SetOutPath "$TEMP"
  RMDir /r "$VibeCADUpdateFailedDir"
  IfFileExists "$VibeCADUpdateFailedDir\*.*" 0 RestoreVibeCADUpdateFailedReady
    Goto RestoreVibeCADUpdateFailed
  RestoreVibeCADUpdateFailedReady:
  StrCpy $R5 "false"
  IfFileExists "$INSTDIR\*.*" 0 RestoreVibeCADUpdateBackupTree
  ClearErrors
  Rename "$INSTDIR" "$VibeCADUpdateFailedDir"
  IfErrors RestoreVibeCADUpdateFailed
  StrCpy $R5 "true"
  RestoreVibeCADUpdateBackupTree:
  ClearErrors
  Rename "$VibeCADUpdateBackupDir" "$INSTDIR"
  IfErrors 0 RestoreVibeCADUpdateTreeReady
    ${if} $R5 == "true"
      Rename "$VibeCADUpdateFailedDir" "$INSTDIR"
    ${endif}
    Goto RestoreVibeCADUpdateFailed
  RestoreVibeCADUpdateTreeReady:
  RMDir /r "$VibeCADUpdateFailedDir"

  DeleteRegKey SHCTX "${APP_UNINST_KEY}"
  DeleteRegKey SHCTX "${APP_REGKEY}"
  ReadINIStr $R4 "$INSTDIR\vibecad-update-registry.ini" "Registry" "DisplayName"
  WriteRegStr SHCTX "$R2" "DisplayName" "$R4"
  ReadINIStr $R4 "$INSTDIR\vibecad-update-registry.ini" "Registry" "DisplayVersion"
  WriteRegStr SHCTX "$R2" "DisplayVersion" "$R4"
  ReadINIStr $R4 "$INSTDIR\vibecad-update-registry.ini" "Registry" "UninstallString"
  WriteRegStr SHCTX "$R2" "UninstallString" "$R4"
  ReadINIStr $R4 "$INSTDIR\vibecad-update-registry.ini" "Registry" "QuietUninstallString"
  WriteRegStr SHCTX "$R2" "QuietUninstallString" "$R4"
  ReadINIStr $R4 "$INSTDIR\vibecad-update-registry.ini" "Registry" "DisplayIcon"
  WriteRegStr SHCTX "$R2" "DisplayIcon" "$R4"
  ReadINIStr $R4 "$INSTDIR\vibecad-update-registry.ini" "Registry" "StartMenu"
  WriteRegStr SHCTX "$R2" "StartMenu" "$R4"
  WriteRegStr SHCTX "$R2" "URLUpdateInfo" "${APP_WEBPAGE}"
  WriteRegStr SHCTX "$R2" "URLInfoAbout" "${APP_WEBPAGE}"
  WriteRegStr SHCTX "$R2" "Publisher" "${APP_NAME} Project"
  WriteRegStr SHCTX "$R2" "HelpLink" "${APP_WEBPAGE}/issues"
  WriteRegDWORD SHCTX "$R2" "NoModify" 0x00000001
  WriteRegDWORD SHCTX "$R2" "NoRepair" 0x00000001
  ReadINIStr $R4 "$INSTDIR\vibecad-update-registry.ini" "Registry" "InstallPath"
  WriteRegStr SHCTX "$R3" "" "$R4"
  ReadINIStr $R4 "$INSTDIR\vibecad-update-registry.ini" "Registry" "Version"
  WriteRegStr SHCTX "$R3" "Version" "$R4"
  ReadINIStr $R4 "$INSTDIR\vibecad-update-registry.ini" "Registry" "ReleaseVersion"
  WriteRegStr SHCTX "$R3" "ReleaseVersion" "$R4"
  ReadINIStr $R4 "$INSTDIR\vibecad-update-registry.ini" "Registry" "UpdateVersion"
  ${if} $R4 == ""
    DeleteRegValue SHCTX "$R3" "UpdateVersion"
  ${else}
    WriteRegStr SHCTX "$R3" "UpdateVersion" "$R4"
  ${endif}
  ReadINIStr $R4 "$INSTDIR\vibecad-update-registry.ini" "Registry" "Build"
  WriteRegDWORD SHCTX "$R3" "Build" $R4
  Delete "$INSTDIR\vibecad-update-registry.ini"
  ClearErrors
  Return

  RestoreVibeCADUpdateFailed:
    SetErrors
    Return

FunctionEnd

# this function is called at first after starting the uninstaller
Function un.onInit

  # Macro to investigate name of FreeCAD's preferences folders to be able remove them
  !insertmacro UnAppPreSuff $AppPre $AppSuff # macro from Utils.nsh

  !insertmacro MULTIUSER_UNINIT

  # Check that FreeCAD is not currently running
  ${nsProcess::FindProcess} ${BIN_FREECAD} $R0
  # if running result is '0', if not running it is '603'
  ${if} $R0 == "0"
   MessageBox MB_OK|MB_ICONSTOP "$(UnInstallRunning)" /SD IDOK
   Abort
  ${endif}
  # plugin must be unloaded
  ${nsProcess::Unload}
  
  # check if it is a 64bit system
  ${if} ${RunningX64}
   SetRegView 64
  ${endif}

  # Ascertain whether the user has sufficient privileges to uninstall.
  # abort when FreeCAD was installed with admin permissions but the user doesn't have administrator privileges
  ReadRegStr $0 HKLM "${APP_UNINST_KEY}" "DisplayVersion"
  ${if} $0 != ""
  ${andif} $MultiUser.Privileges != "Admin"
  ${andif} $MultiUser.Privileges != "Power"
   MessageBox MB_OK|MB_ICONSTOP "$(UnNotAdminLabel)" /SD IDOK
   Abort
  ${endif}
  # warning when FreeCAD couldn't be found in the registry
  ${if} $0 == "" # check in HKCU
   ReadRegStr $0 HKCU "${APP_UNINST_KEY}" "DisplayVersion"
   ${if} $0 == ""
     MessageBox MB_OK|MB_ICONEXCLAMATION "$(UnNotInRegistryLabel)" /SD IDOK
   ${endif}
  ${endif}

  # question message if the user really wants to uninstall FreeCAD
  MessageBox MB_ICONQUESTION|MB_YESNO|MB_DEFBUTTON2 "$(UnReallyRemoveLabel)" /SD IDYES IDYES +2 # continue if yes
  Abort

FunctionEnd
