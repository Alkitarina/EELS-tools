Option Explicit
Dim shell, files, project, launcher, powershell, command, status
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
project = files.GetParentFolderName(WScript.ScriptFullName)
launcher = files.BuildPath(project, "start_web.ps1")
powershell = shell.ExpandEnvironmentStrings("%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
shell.CurrentDirectory = project
command = Chr(34) & powershell & Chr(34) & " -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & launcher & Chr(34)
' No data argument: open the workspace first, then import data in the browser.
status = shell.Run(command, 0, True)
If status <> 0 Then
    MsgBox "EELS could not start. Run start_web.ps1 in PowerShell for details." & vbCrLf & "If an older server is running, save its results, then run stop_web.ps1 and try again.", vbExclamation, "EELS startup"
End If
WScript.Quit status
