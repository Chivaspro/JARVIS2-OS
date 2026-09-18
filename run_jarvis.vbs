Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
base = FSO.GetParentFolderName(WScript.ScriptFullName)
script = base & "\\main.pyw"
pythonw = "pythonw.exe"
If FSO.FileExists(script) Then
    WshShell.Run Chr(34) & pythonw & Chr(34) & " " & Chr(34) & script & Chr(34), 0, False
Else
    MsgBox "main.pyw was not found:" & vbCrLf & script, 16, "J.A.R.V.I.S"
End If
