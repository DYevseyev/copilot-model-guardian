@echo off
chcp 65001 >nul 2>&1
:: Disable QuickEdit so clicking in this window doesn't freeze the guardian
reg add "HKCU\Console" /v QuickEdit /t REG_DWORD /d 0 /f >nul 2>&1
title Copilot Model Guardian - Minimize to keep running
cd /d "%~dp0"
echo =====================================================================
echo  Microsoft Copilot Model Guardian
echo  Enforcing: GPT 5.6 Think deeper
echo  Action Counter: Active (Tracks model enforcements in real time)
echo  TIP: Minimize this window - closing it will stop the guardian.
echo =====================================================================
if exist "%~dp0CopilotModelGuardian.exe" (
    "%~dp0CopilotModelGuardian.exe"
) else (
    python copilot_model_guardian.py
)
pause
