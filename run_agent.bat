@echo off
setlocal
chcp 65001 >nul
color 0F
title FrontierOR Agent
cd /d "%~dp0"
"E:\my_evns\py312_torch28\python.exe" -X utf8 agent.py %*
set "agent_exit=%errorlevel%"
echo.
pause
exit /b %agent_exit%
