' ==============================================================================
' Microsoft Copilot Model Guardian - Silent Background Launcher
' ==============================================================================
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)

WshShell.CurrentDirectory = ScriptDir
ExeFile = ScriptDir & "\CopilotModelGuardian.exe"
PyScript = ScriptDir & "\copilot_model_guardian.py"
LogFile = ScriptDir & "\copilot_guardian.log"

If FSO.FileExists(ExeFile) Then
    Cmd = """" & ExeFile & """ --log-file """ & LogFile & """"
Else
    Cmd = "pythonw.exe """ & PyScript & """ --log-file """ & LogFile & """"
End If

WshShell.Run Cmd, 0, False
