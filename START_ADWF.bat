@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if not errorlevel 1 goto use_python

echo [ADWF] Python 3 was not found.
echo Install Python 3 and enable the Python launcher or add python.exe to PATH.
pause
exit /b 1

:use_py
py -3 ".adwf\adwf.py" --help >nul 2>nul
if errorlevel 1 goto launch_failed
start "ADWF Executive Portal" /min py -3 ".adwf\adwf.py" dashboard serve --bind 127.0.0.1 --port 8765
py -3 ".adwf\scripts\wait_portal.py" --url "http://127.0.0.1:8765/" --expect "ADWF v1.6 Executive Portal" --timeout 15
if errorlevel 1 goto portal_failed
goto open_browser

:use_python
python ".adwf\adwf.py" --help >nul 2>nul
if errorlevel 1 goto launch_failed
start "ADWF Executive Portal" /min python ".adwf\adwf.py" dashboard serve --bind 127.0.0.1 --port 8765
python ".adwf\scripts\wait_portal.py" --url "http://127.0.0.1:8765/" --expect "ADWF v1.6 Executive Portal" --timeout 15
if errorlevel 1 goto portal_failed
goto open_browser

:open_browser
start "" "http://127.0.0.1:8765/"
exit /b 0

:portal_failed
echo [ADWF] Owner Portal did not start correctly on http://127.0.0.1:8765/.
echo Close any program using port 8765 and try again.
pause
exit /b 1

:launch_failed
echo [ADWF] Python can run, but ADWF failed its startup preflight.
pause
exit /b 1
