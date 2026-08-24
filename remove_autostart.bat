@echo off
title Remove Copilot Guardian from Windows Startup
echo Removing Copilot Model Guardian from Windows Startup...
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "CopilotModelGuardian" /f
if %ERRORLEVEL% equ 0 (
    echo Successfully removed from Windows Startup.
) else (
    echo Registry entry was not present or could not be removed.
)
echo.
pause
