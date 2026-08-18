@echo off
setlocal EnableExtensions
REM Agent entry for Windows. Prefer a running VibeCAD GUI over starting Cmd.
set "SCRIPT_DIR=%~dp0"
set "CLI=%SCRIPT_DIR%VibeCADAgentCli.py"

where python >nul 2>&1
if %ERRORLEVEL%==0 (
    python "%CLI%" --gui-only %*
    if %ERRORLEVEL%==0 exit /b 0
    if %ERRORLEVEL%==1 exit /b 1
)

set "CMD_EXE="
if defined VIBECAD_CMD set "CMD_EXE=%VIBECAD_CMD%"
if not defined CMD_EXE if exist "%SCRIPT_DIR%..\..\bin\VibeCADCmd.exe" set "CMD_EXE=%SCRIPT_DIR%..\..\bin\VibeCADCmd.exe"
if not defined CMD_EXE if exist "%SCRIPT_DIR%..\..\bin\FreeCADCmd.exe" set "CMD_EXE=%SCRIPT_DIR%..\..\bin\FreeCADCmd.exe"

if not defined CMD_EXE (
    echo {"ok": false, "failure_code": "CMD_NOT_FOUND", "failure_stage": "precondition", "error": "Neither a listening VibeCAD GUI nor FreeCADCmd.exe/VibeCADCmd.exe was found. Start VibeCAD.exe or set VIBECAD_CMD."}
    exit /b 1
)

"%CMD_EXE%" "%CLI%" --local %*
exit /b %ERRORLEVEL%
