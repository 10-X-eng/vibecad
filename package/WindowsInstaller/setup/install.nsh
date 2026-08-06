/*

install.nsh

Installation of program files, dictionaries and external components

*/

#--------------------------------
# Program files
!include LogicLib.nsh

Section -PrepareVibeCADUpdate

  ${if} $VibeCADUpdateMode == "install"
    StrCpy $VibeCADUpdateBackupDir "$INSTDIR.vibecad-rollback"
    IfFileExists "$VibeCADUpdateBackupDir\*.*" 0 PreviousRollbackRemoved
      RMDir /r "$VibeCADUpdateBackupDir"
      IfFileExists "$VibeCADUpdateBackupDir\*.*" 0 PreviousRollbackRemoved
        SetErrorLevel 21
        Quit
    PreviousRollbackRemoved:
    IfFileExists "$INSTDIR\*.*" 0 UpdateInstallDirectoryReady
      StrCpy $R2 "${APP_UNINST_KEY}"
      StrCpy $R3 "${APP_REGKEY}"
      ${if} $OldVersionNumber != ""
        StrCpy $R2 "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}$OldVersionNumber"
        StrCpy $R3 "SOFTWARE\${APP_NAME}$OldVersionNumber"
      ${endif}
      SetOutPath "$TEMP"
      ClearErrors
      Rename "$INSTDIR" "$VibeCADUpdateBackupDir"
      IfErrors 0 +3
        SetErrorLevel 22
        Quit
      WriteINIStr "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "UninstallKey" "$R2"
      WriteINIStr "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "AppKey" "$R3"
      ReadRegStr $R4 SHCTX "$R2" "DisplayName"
      WriteINIStr "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "DisplayName" "$R4"
      ReadRegStr $R4 SHCTX "$R2" "DisplayVersion"
      WriteINIStr "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "DisplayVersion" "$R4"
      ReadRegStr $R4 SHCTX "$R2" "UninstallString"
      WriteINIStr "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "UninstallString" "$R4"
      ReadRegStr $R4 SHCTX "$R2" "QuietUninstallString"
      WriteINIStr "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "QuietUninstallString" "$R4"
      ReadRegStr $R4 SHCTX "$R2" "DisplayIcon"
      WriteINIStr "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "DisplayIcon" "$R4"
      ReadRegStr $R4 SHCTX "$R2" "StartMenu"
      WriteINIStr "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "StartMenu" "$R4"
      ReadRegStr $R4 SHCTX "$R3" ""
      WriteINIStr "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "InstallPath" "$R4"
      ReadRegStr $R4 SHCTX "$R3" "Version"
      WriteINIStr "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "Version" "$R4"
      ReadRegStr $R4 SHCTX "$R3" "ReleaseVersion"
      WriteINIStr "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "ReleaseVersion" "$R4"
      ReadRegDWORD $R4 SHCTX "$R3" "Build"
      WriteINIStr "$VibeCADUpdateBackupDir\vibecad-update-registry.ini" "Registry" "Build" "$R4"
      IfErrors 0 VibeCADUpdateRegistrySaved
        ClearErrors
        Rename "$VibeCADUpdateBackupDir" "$INSTDIR"
        SetErrorLevel 26
        Quit
      VibeCADUpdateRegistrySaved:
      CreateDirectory "$INSTDIR"
      IfErrors 0 UpdateInstallDirectoryReady
        ClearErrors
        Rename "$VibeCADUpdateBackupDir" "$INSTDIR"
        SetErrorLevel 26
        Quit
    UpdateInstallDirectoryReady:
  ${endif}

SectionEnd

Section -ProgramFiles SecProgramFiles

  # if the $INSTDIR does not contain "FreeCAD" we must add a subfolder to avoid that FreeCAD will e.g.
  # be installed directly to C:\programs - the uninstaller will then delete the whole
  # C:\programs directory
  StrCpy $String "$INSTDIR"
  StrCpy $Search "${APP_NAME}"
  Call StrPoint # function from Utils.nsh
  ${if} $Pointer == "-1"
   StrCpy $INSTDIR "$INSTDIR\${APP_DIR}"
  ${endif}
  
  # turn on logging
  # Note that this can first be done here since the log file is written to $INSTDIR
  # to $INSTDIR must have a valid path before logging can be turned on
  LogSet on

  # Install and register the core FreeCAD files
  
  # Initializes the plug-ins dir ($PLUGINSDIR) if not already initialized.
  # $PLUGINSDIR is automatically deleted when the installer exits.
  InitPluginsDir
  
  # Binaries
  SetOutPath "$INSTDIR\bin"
  # recursively copy all files under bin
  File /r "${FILES_FREECAD}\bin\*.*"
  
  # MSVC redistributable DLLs
  !ifdef FILES_DEPS
    !echo "Including MSVC Redist files from ${FILES_DEPS}"
    SetOutPath "$INSTDIR\bin"
    File "${FILES_DEPS}\*.*"
  !endif
  
  # Others
  SetOutPath "$INSTDIR\data"
  File /r "${FILES_FREECAD}\data\*.*"
  SetOutPath "$INSTDIR\doc"
  File /r "${FILES_FREECAD}\doc\*.*"
  SetOutPath "$INSTDIR\Ext"
  File /r "${FILES_FREECAD}\Ext\*.*"
  SetOutPath "$INSTDIR\lib"
  File /r "${FILES_FREECAD}\lib\*.*"
  SetOutPath "$INSTDIR\Mod"
  File /r "${FILES_FREECAD}\Mod\*.*"
  SetOutPath "$INSTDIR"
  File /r "${FILES_THUMBS}"
    
  # Create uninstaller
  WriteUninstaller "$INSTDIR\${SETUP_UNINSTALLER}"

SectionEnd

Section -ValidateVibeCADUpdate

  ${if} $VibeCADUpdateMode == "install"
    IfErrors RollbackVibeCADUpdate
    IfFileExists "$INSTDIR\bin\freecadcmd.exe" 0 RollbackVibeCADUpdate
    ExecWait '"$INSTDIR\bin\freecadcmd.exe" --safe-mode -c "import VibeCADUpdate"' $R0
    ${if} $R0 != 0
      Goto RollbackVibeCADUpdate
    ${endif}
    Goto VibeCADUpdateValidated

    RollbackVibeCADUpdate:
      Call RestoreVibeCADUpdateBackup
      SetErrorLevel 23
      Quit

    VibeCADUpdateValidated:
  ${endif}

SectionEnd
