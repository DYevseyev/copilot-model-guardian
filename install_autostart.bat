@echo off
title Install Copilot Guardian to Windows Startup
cd /d "%~dp0"
echo Adding Copilot Model Guardian to Windows Startup...
set SCRIPT_PATH=%~dp0start_guardian_background.vbs
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "CopilotModelGuardian" /t REG_SZ /d "wscript.exe \"%SCRIPT_PATH%\"" /f
if %ERRORLEVEL% equ 0 (
    echo.
    echo Successfully registered to Windows Startup!
    echo Copilot Model Guardian will now launch automatically when you sign in.
) else (
    echo Failed to add registry key.
)
echo.
pause
