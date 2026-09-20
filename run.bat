@echo off
REM Start the Weather Automation Server
REM Double-click this file to start the server
cd /d "%~dp0"
echo Starting Weather Automation Server...
echo Open http://127.0.0.1:5000 in your browser
python app.py
pause
