@echo off
chcp 65001 >nul
cd /d "%~dp0"
wscript "%~dp0run_hidden.vbs"
echo เปิดบอทเบื้องหลังแล้ว (ถ้าเปิดอยู่แล้วจะไม่เปิดซ้ำ)
echo ดู log ได้ที่ brain\bot.log
timeout /t 4 >nul
