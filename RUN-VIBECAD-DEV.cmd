@rem SPDX-License-Identifier: LGPL-2.1-or-later
@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0Launch-VibeCAD-Dev.cmd" (
    echo.
    echo VibeCAD development launcher is missing:
    echo   %~dp0Launch-VibeCAD-Dev.cmd
    echo.
    pause
    exit /b 1
)

call "%~dp0Launch-VibeCAD-Dev.cmd" %*
exit /b %errorlevel%
