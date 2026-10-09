@echo off
REM ===========================================================================
REM  Start the Unity headless worker IN YOUR OWN TERMINAL.
REM
REM  Unity's batchmode build host cannot complete its IPC handshake when Unity
REM  is launched from inside WorkBuddy's process context, but it works fine when
REM  launched from a terminal you opened yourself. This worker lives in that
REM  good context and runs Unity jobs on the agent's behalf.
REM
REM  Just double-click this file and leave the window open. Ctrl-C to stop.
REM ===========================================================================

set "PY=C:\Users\user\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe"
set "WORKER=C:\Users\user\Documents\Unity\.workbuddy-ai\unity_headless_worker.py"

echo.
echo Starting Unity headless worker...
echo   python: %PY%
echo   worker: %WORKER%
echo.
echo Leave this window open. Press Ctrl-C to stop the worker.
echo.

"%PY%" -u "%WORKER%"

echo.
echo Worker stopped.
pause
