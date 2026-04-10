Set shell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
psScript = """" & scriptDir & "\scripts\windows\start_njordhr.ps1"""
htaPath = """" & scriptDir & "\scripts\windows\startup_splash.hta"""
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & psScript
shell.Run "mshta.exe " & htaPath, 1, False
WScript.Sleep 200
shell.Run cmd, 0, False
