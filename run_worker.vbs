' Loki — hidden background launcher (no console window).
' Double-click to start Loki in the background from the repo folder.
' For start-at-login, run:  .\setup.ps1 -Autostart
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = base
sh.Run """" & base & "\venv\Scripts\pythonw.exe"" -m loki", 0, False
