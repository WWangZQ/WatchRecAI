Option Explicit
Dim sh, fso, folder, pyw
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = folder & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then
    MsgBox "Run setup.bat first.", 48, "WatchRec Open Source"
    WScript.Quit 1
End If
sh.CurrentDirectory = folder
sh.Run Chr(34) & pyw & Chr(34) & " " & Chr(34) & folder & "\desktop.py" & Chr(34), 0, False
