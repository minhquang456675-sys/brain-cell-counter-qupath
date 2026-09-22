@echo off
rem Install the lightweight application files before installing large dependencies.
title Brain Cell Counter v2.1.0 - Core Installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install_Core.ps1"
if errorlevel 1 pause
