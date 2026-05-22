@echo off
REM Run from repo installer\ — uses latest scripts here, no copy to Program Files.
set "APP=C:\Program Files\CornerstoneMock"
set "CFG=%APPDATA%\CornerstoneMock"
set "PS1=%~dp0scripts\install-services.ps1"

if not exist "%APP%\Bridge\cornerstone-bridge.exe" (
    echo CornerstoneMock not installed under %APP%
    echo Install the app first, then run this again.
    exit /b 1
)
if not exist "%PS1%" (
    echo Missing: %PS1%
    exit /b 1
)

echo Requesting Administrator to register Windows services...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','\"%PS1%\"','-AppDir','\"%APP%\"','-ConfigDir','\"%CFG%\"','-InstallBridge','-InstallWeb'); exit $p.ExitCode"
exit /b %ERRORLEVEL%
