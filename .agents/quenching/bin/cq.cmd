@echo off
rem cq launcher for a Windows PATH: runs ..\assets\bin\cq with Python and hands back its exit code.
setlocal
set "CQ=%~dp0..\assets\bin\cq"
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 "%CQ%" %*
) else (
  python "%CQ%" %*
)
exit /b %ERRORLEVEL%
