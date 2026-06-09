' WatchRec 桌面静默启动器
' 双击即用：用 conda ics 环境的 pythonw 无窗口启动 desktop.py（不弹命令行黑窗）。
Option Explicit
Dim sh, fso, scriptDir, pyw, target
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' 优先 ics 环境，找不到回退到 base miniconda
pyw = "C:\Users\willy\.conda\envs\ics\pythonw.exe"
If Not fso.FileExists(pyw) Then pyw = "D:\ProgramData\miniconda3\pythonw.exe"

target = """" & pyw & """ """ & scriptDir & "\desktop.py"""
sh.CurrentDirectory = scriptDir
' 0 = 隐藏窗口，False = 不等待返回
sh.Run target, 0, False
