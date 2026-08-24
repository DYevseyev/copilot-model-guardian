@echo off
title Copilot Model Guardian
cd /d "%~dp0"
echo =====================================================================
echo  Starting Microsoft Copilot Model Guardian...
echo  Enforcing: "GPT 5.6 Think deeper"
echo =====================================================================
if exist "%~dp0CopilotModelGuardian.exe" (
    "%~dp0CopilotModelGuardian.exe"
) else (
    python copilot_model_guardian.py
)
pause
