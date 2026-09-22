@echo off
rem Install the pinned QuPath release and the required BIOP extensions.
title QuPath 0.7.0 and BIOP Extensions Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install_QuPath_and_Extensions.ps1"
if errorlevel 1 pause
