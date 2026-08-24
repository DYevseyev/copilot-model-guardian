@echo off
title Stop Copilot Model Guardian
echo Stopping Copilot Model Guardian...
taskkill /F /IM CopilotModelGuardian.exe >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object CommandLine -like '*copilot_model_guardian.py*' | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Stopped PID: ' + $_.ProcessId) }"
echo Done.
ping 127.0.0.1 -n 2 >nul
