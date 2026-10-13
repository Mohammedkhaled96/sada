' Silent launcher: starts the app with no console window.
' Double-click this, or put a shortcut to it in your Startup folder.
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = here & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then
    MsgBox "لم يُعثر على بيئة بايثون في:" & vbCrLf & pyw & vbCrLf & vbCrLf & _
           "شغّل setup.bat أولًا.", 16, "برنامج التفريغ النصي"
    WScript.Quit 1
End If
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = here
Set env = sh.Environment("PROCESS")
env("PYTHONUTF8") = "1"
sh.Run """" & pyw & """ -m app.main", 0, False
