@echo off
rem Start the legacy all-in-one installer and preserve its exit status.
title Brain Cell Counter v2.2.2 Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install_Brain_Cell_Counter_v1.0.ps1"
set "BCC_EXIT=%ERRORLEVEL%"
if not "%BCC_EXIT%"=="0" (
  echo.
  echo Installation did not complete. Review the error above.
  pause
)
exit /b %BCC_EXIT%
