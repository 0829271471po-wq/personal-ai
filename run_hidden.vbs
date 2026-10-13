' เปิดบอทแบบซ่อนหน้าต่าง (ใช้ตอนเปิดเครื่อง)
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run """" & dir & "\bot_loop.bat""", 0, False
