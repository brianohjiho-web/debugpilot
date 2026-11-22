@echo off
title DebugPilot
cd /d "%~dp0"
set LOG=%~dp0start-log.txt
echo DebugPilot start log > "%LOG%"
echo %date% %time% >> "%LOG%"

rem Find a working Python (not the Microsoft Store shortcut).
set PY=
for %%C in (py python python3) do (
  if not defined PY (
    %%C -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul && set PY=%%C
  )
)
echo Python command: %PY% >> "%LOG%"
where py python python3 >> "%LOG%" 2>&1
if not defined PY goto :nopython
%PY% --version >> "%LOG%" 2>&1

echo.
echo  DebugPilot is setting up. The first time takes a few minutes...
echo.
echo --- pip install --- >> "%LOG%"
%PY% -m pip install -r requirements.txt >> "%LOG%" 2>&1 || goto :err
echo --- playwright install --- >> "%LOG%"
%PY% -m playwright install chromium >> "%LOG%" 2>&1 || goto :err

echo.
echo  Starting DebugPilot. Your browser will open in a few seconds.
echo  Keep this window open while you use it. Close it to stop.
echo.
start "" cmd /c "timeout /t 6 >nul & start http://localhost:8000/scan"
echo --- server --- >> "%LOG%"
%PY% -m uvicorn server:app --port 8000 --reload --reload-include "*.py" 2>> "%LOG%"
echo Server stopped. >> "%LOG%"
pause
exit /b

:nopython
echo NO WORKING PYTHON FOUND >> "%LOG%"
echo.
echo  Python 3.10 or newer isn't installed (or only the Microsoft Store shortcut is).
echo  Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
echo.
pause
exit /b

:err
echo SETUP FAILED >> "%LOG%"
echo.
echo  Setup failed. Tell Claude: the details are in start-log.txt in the DebugPilot folder.
echo.
pause
