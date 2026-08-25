@echo off
chcp 65001 >nul 2>&1
:: Disable QuickEdit so clicking in this window doesn't freeze execution
reg add "HKCU\Console" /v QuickEdit /t REG_DWORD /d 0 /f >nul 2>&1
title PuTTY to Copilot Smart Bridge (Interactive CLI)
cd /d "%~dp0"
echo =====================================================================
echo  PuTTY to Microsoft Copilot Smart Bridge
echo  Controls:
echo    [T]     - Toggle Autonomous Loop (ON / OFF)
echo    [Space] - Send currently staged terminal delta to Copilot
echo    [Q]     - Quit Bridge
echo =====================================================================
if exist "%~dp0PuttyCopilotBridge.exe" (
    "%~dp0PuttyCopilotBridge.exe"
) else (
    python putty_copilot_bridge.py
)
pause
