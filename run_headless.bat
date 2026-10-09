@echo off
setlocal enabledelayedexpansion

REM ============================================================================
REM  Headless Unity test - run this in YOUR OWN terminal (not through WorkBuddy)
REM
REM  Why: Unity's batchmode build host (bee_backend.exe) fails to complete its
REM  named-pipe IPC handshake when Unity is launched from inside WorkBuddy,
REM  producing "Scripts have compiler errors" with zero actual compiler errors.
REM  Launching outside that process context is the one thing that could not be
REM  tested from the inside, so this script settles it.
REM
REM  Just double-click it, or run it from cmd / PowerShell. Takes ~30-90 seconds.
REM ============================================================================

set "UNITY=C:\Program Files\Unity\Hub\Editor\6000.6.4f1\Editor\Unity.exe"
set "PROJ=C:\Users\user\Documents\Unity\Unity2DTest"
set "LOG=C:\Users\user\Documents\Unity\.workbuddy-ai\headless_user.log"

echo.
echo Running Unity in batchmode...
echo   project: %PROJ%
echo   log:     %LOG%
echo.

"%UNITY%" -batchmode -nographics -quit -disableDirectoryMonitor -projectPath "%PROJ%" -logFile "%LOG%"
set "RC=%ERRORLEVEL%"

echo.
echo Unity exit code: %RC%
echo.

findstr /C:"IPC_Client_InitializeAndConnectToParent" "%LOG%" >nul 2>&1
if %ERRORLEVEL%==0 (
    echo RESULT: FAILED - the SAME bee_backend IPC error occurred here.
    echo         So the problem is the machine/Unity, not WorkBuddy's context.
) else (
    findstr /C:"Scripts have compiler errors" "%LOG%" >nul 2>&1
    if !ERRORLEVEL!==0 (
        echo RESULT: compiled with errors, but NO IPC failure.
        echo         Headless IPC works outside WorkBuddy - the launch context was the cause.
    ) else (
        echo RESULT: PASSED - Unity compiled headlessly with no IPC failure.
        echo         Headless mode works when launched from your own terminal.
    )
)

echo.
echo Relevant log lines:
findstr /N /C:"Starting: " /C:"ExitCode:" /C:"bee_backend" /C:"Scripts have compiler errors" "%LOG%"
echo.
pause
