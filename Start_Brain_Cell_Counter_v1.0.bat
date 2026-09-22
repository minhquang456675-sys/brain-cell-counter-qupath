@echo off
rem Launch the source-tree GUI with the configured Windows Python interpreter.
rem Keep this compatibility filename so existing desktop shortcuts continue to work.
set "BCC_GUI_PYTHON=C:\Program Files\Python312\python.exe"
if not exist "%BCC_GUI_PYTHON%" (
  echo Windows GUI Python was not found at:
  echo %BCC_GUI_PYTHON%
  pause
  exit /b 1
)
cd /d "%~dp0"
"%BCC_GUI_PYTHON%" run_brain_cell_counter_v1.0.py
if errorlevel 1 pause
