@echo off
REM Double-click this file to get an instant HTTPS link for your phone/WhatsApp
cd /d "%~dp0"
echo Checking Weather App server...
python mobile_link.py
pause
