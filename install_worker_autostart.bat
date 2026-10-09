@echo off
setlocal
rem ============================================================================
rem  One-time install of the Unity headless worker.
rem
rem  WHY: Unity's build host cannot do its named-pipe IPC handshake when the
rem  agent launches Unity. It only works from a process the USER started. So the
rem  worker must live in your logon session - and this makes that automatic.
rem
rem  What it does:
rem    1. starts the worker right now (windowless)
rem    2. copies a launcher into your Windows Startup folder, so it also starts
rem       automatically at every future logon
rem
rem  After running this ONCE you never need to start anything by hand again.
rem ============================================================================

set "HERE=%~dp0"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

if not exist "%STARTUP%" (
  echo ERROR: Startup folder not found: "%STARTUP%"
  pause
  exit /b 1
)

echo Installing launcher into Startup folder...
copy /Y "%HERE%launch_worker_silent.vbs" "%STARTUP%\UnityHeadlessWorker.vbs" >nul
if errorlevel 1 (
  echo ERROR: could not copy launcher.
  pause
  exit /b 1
)
echo   -^> "%STARTUP%\UnityHeadlessWorker.vbs"

echo Starting worker now...
start "" wscript.exe "%STARTUP%\UnityHeadlessWorker.vbs"

echo.
echo Waiting for the worker to report in...
set "HB=%HERE%queue\_heartbeat.txt"
if exist "%HB%" del /Q "%HB%"
ping -n 8 127.0.0.1 >nul

if exist "%HB%" (
  echo.
  echo WORKER IS RUNNING:
  type "%HB%"
) else (
  echo.
  echo No heartbeat yet - check "%HERE%worker.log"
)

echo.
echo Done. The worker will now start automatically whenever you log in.
echo To remove it later, run uninstall_worker_autostart.bat
echo.
pause
