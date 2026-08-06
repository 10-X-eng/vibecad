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
# MultiUser custom method

Function PostMultiUserPageInit
  # check if this FreeCAD version is already installed
  ReadRegStr $0 SHCTX "${APP_UNINST_KEY}" "UninstallString"
  ${if} $0 != ""
   ${if} $VibeCADUpdateMode != "false"
    Goto ContinueInstall
   ${endif}
   # check if the uninstaller was accidentally deleted
   # if so, don't bother the user if they really want to install a new FreeCAD over an existing one
   # because they won't have a chance to deny this

   # remove quotes from uninstaller filename
   ${TrimQuotes} $0 $0
   # skip message box if uninstaller file is missing
   IfFileExists $0 0 ContinueInstall

   # installing over an existing installation of the same FreeCAD release is not necessary
   # if the users does this, they most probably have a problem with FreeCAD that can better be solved
   # by reinstalling FreeCAD
   # for beta and other test releases over-installing can even cause errors
   MessageBox MB_YESNOCANCEL "$(AlreadyInstalled)" /SD IDCANCEL IDYES ContinueInstall IDNO BackToMuiltUserPage
   Quit
   BackToMuiltUserPage:
   Abort
   ContinueInstall:
  ${endif}

  # check if there is an existing FreeCAD installation of the same FreeCAD series
  # we usually don't release more than 10 versions so with 20 we are safe to check if a newer version is installed
  IntOp $4 ${APP_VERSION_PATCH} + 20
  ${for} $5 0 $4
   ReadRegStr $0 SHCTX "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}${APP_VERSION_MAJOR}${APP_VERSION_MINOR}$5" "DisplayVersion"
   # also check for an emergency release
   ${if} $0 == ""
    ReadRegStr $0 SHCTX "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}${APP_VERSION_MAJOR}${APP_VERSION_MINOR}$51" "DisplayVersion"
   ${endif}
   ${if} $0 != ""
    StrCpy $R5 $0 # store the read version number
    StrCpy $OldVersionNumber "${APP_VERSION_MAJOR}${APP_VERSION_MINOR}$5"
    # we don't stop here because we want the latest installed version
   ${endif}
  ${next}

  # NSIS cannot handle numbers with leading zero, thus cut it off before comparing
  StrCpy $1 $OldVersionNumber "" 1
  StrCpy $2 ${APP_SERIES_KEY} "" 1
  ${if} $1 > $2
   # store the version number and reformat it temporarily for the error message
   StrCpy $R0 $OldVersionNumber
   StrCpy $OldVersionNumber $R5
   MessageBox MB_OK|MB_ICONSTOP "$(NewerInstalled)" /SD IDOK
   StrCpy $OldVersionNumber $R0
   Quit
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
