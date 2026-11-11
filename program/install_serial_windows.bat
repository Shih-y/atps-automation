@echo off
echo pyserial is already bundled. This repair installer requires Internet.
cd /d "%~dp0"
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" goto bundled
py -3 -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>nul
if not errorlevel 1 goto launcher
python -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>nul
if errorlevel 1 goto missing
python -m pip install --target "%~dp0vendor" --upgrade pyserial==3.5
goto end
:launcher
py -3 -m pip install --target "%~dp0vendor" --upgrade pyserial==3.5
goto end
:bundled
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m pip install --target "%~dp0vendor" --upgrade pyserial==3.5
goto end
:missing
echo Python 3.10 or newer is required. Python 3.2 cannot run this program.
:end
pause
