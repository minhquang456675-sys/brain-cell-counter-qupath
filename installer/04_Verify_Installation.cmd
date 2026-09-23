@echo off
rem Verify every installed component and save a machine-readable status report.
title Brain Cell Counter v2.2.2 - Verify Installation
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Verify_Brain_Cell_Counter_v1.0.ps1"
pause
