@echo off
cd /d "%~dp0"
echo ======================================================
echo   PUSHING WEATHER AUTOMATION CODE TO GITHUB
echo ======================================================
echo.
git branch -M main
git push -u origin main
echo.
echo If finished, press any key to close this window.
pause
