@echo off
rem Install the lightweight application files before installing large dependencies.
title Brain Cell Counter v2.2.2 - Core Installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install_Core.ps1"
if errorlevel 1 pause
