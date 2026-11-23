@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup_windows.bat first.
  pause
  exit /b 1
)
echo ************************************************************
echo  WARNING: LAN HTTP is unencrypted. For real customer data, use HTTPS.
echo  Allows access from devices on the trusted private Wi-Fi only.
echo  Check your Windows firewall and LAN IP address with: ipconfig
ipconfig | findstr /i "IPv4"
echo ************************************************************
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
