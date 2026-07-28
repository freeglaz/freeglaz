@echo off
REM freeglaz - double-click launcher for Windows (web app in the browser).
REM
REM Runs from inside the extracted freeglaz folder: it locates that folder from
REM its own position (no hardcoded path), finds uv (a double-click starts with a
REM minimal PATH that may exclude uv), then launches `freeglaz web`. This console
REM window stays open while the server runs; closing it stops freeglaz. The
REM browser opens on its own.
REM
REM First run only: Windows SmartScreen may warn ("Windows protected your PC").
REM Click "More info" then "Run anyway" - see Docs\INSTALL_tarball_windows.md.

setlocal
cd /d "%~dp0"

REM Find uv without relying on PATH.
set "UV="
where uv >nul 2>nul && set "UV=uv"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" set "UV=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
if not defined UV if exist "%USERPROFILE%\scoop\shims\uv.exe" set "UV=%USERPROFILE%\scoop\shims\uv.exe"

if not defined UV (
    echo uv was not found. Install it first - see Docs\INSTALL_tarball_windows.md.
    echo Press any key to close this window.
    pause >nul
    exit /b 1
)

echo Starting freeglaz - closing this window stops it.
"%UV%" run python freeglaz web
if errorlevel 1 (
    echo.
    echo freeglaz exited with an error. Press any key to close.
    pause >nul
)
endlocal
