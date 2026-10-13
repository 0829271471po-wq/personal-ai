@echo off
chcp 65001 >nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'bot_loop\.bat|telegram_bot\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo หยุดบอทแล้ว
timeout /t 3 >nul
