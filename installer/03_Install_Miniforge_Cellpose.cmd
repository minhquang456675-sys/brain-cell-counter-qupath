@echo off
rem Install the dedicated Python, PyTorch, and Cellpose-SAM environment.
title Miniforge and Cellpose-SAM Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install_Miniforge_Cellpose.ps1"
if errorlevel 1 pause
