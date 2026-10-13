@echo off
rem รันบอทเบื้องหลัง ถ้าล่มจะเปิดใหม่เองใน 10 วินาที (log อยู่ที่ brain\bot.log)
cd /d "%~dp0"
:loop
"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe" -X utf8 -u telegram_bot.py >> brain\bot.log 2>&1
if %errorlevel%==3 exit /b
ping -n 11 127.0.0.1 >nul
goto loop
