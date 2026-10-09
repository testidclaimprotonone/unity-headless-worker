@echo off
setlocal
rem Removes the Startup entry so the worker no longer launches at logon.
rem Does NOT kill a worker that is already running for this session.

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

if exist "%STARTUP%\UnityHeadlessWorker.vbs" (
  del /Q "%STARTUP%\UnityHeadlessWorker.vbs" && echo Removed Startup entry.
) else (
  echo No Startup entry found.
)

echo.
echo To stop the worker running right now, close its console window, or run:
echo   taskkill /FI "IMAGENAME eq pythonw.exe"
echo.
pause
