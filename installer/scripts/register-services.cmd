@echo off
REM Re-register Bridge/Web services (always requests Administrator).
set "APP=%~dp0.."
set "CFG=%APPDATA%\CornerstoneMock"
set "PS1=%~dp0install-services.ps1"

if not exist "%PS1%" (
    echo Missing: %PS1%
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','\"%PS1%\"','-AppDir','\"%APP%\"','-ConfigDir','\"%CFG%\"','-InstallBridge','-InstallWeb'); exit $LASTEXITCODE"

exit /b %ERRORLEVEL%
