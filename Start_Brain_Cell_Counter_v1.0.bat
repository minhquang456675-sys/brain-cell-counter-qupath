@echo off
rem Launch the real source tree so Windows Explorer does not depend on the
rem virtualized LocalAppData view used by the packaged Codex application.
set "BCC_SOURCE_ROOT=%~dp0"
set "BCC_GUI_PYTHON=%USERPROFILE%\miniforge3\envs\cellpose\pythonw.exe"
if not exist "%BCC_GUI_PYTHON%" set "BCC_GUI_PYTHON=C:\Program Files\Python312\pythonw.exe"
if not exist "%BCC_GUI_PYTHON%" (
  echo Brain Cell Counter Python was not found.
  echo Expected: %USERPROFILE%\miniforge3\envs\cellpose\pythonw.exe
  pause
  exit /b 1
)
if not exist "%BCC_SOURCE_ROOT%run_brain_cell_counter_v1.0.py" (
  echo Brain Cell Counter source launcher was not found in:
  echo %BCC_SOURCE_ROOT%
  pause
  exit /b 1
)
cd /d "%BCC_SOURCE_ROOT%"
start "Brain Cell Counter" "%BCC_GUI_PYTHON%" "%BCC_SOURCE_ROOT%run_brain_cell_counter_v1.0.py"
