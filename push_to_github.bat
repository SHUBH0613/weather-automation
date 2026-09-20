@echo off
cd /d "%~dp0"
echo ======================================================
echo   PUSHING WEATHER AUTOMATION CODE TO GITHUB
echo ======================================================
echo.

git status --short
echo.
echo Staging updated files...
git add -A

echo Committing updates...
git commit -m "Fix AccuWeather cloud timeout with meteorological fallback and fix slide layout overlap" || echo No new changes to commit.

echo.
echo Pushing to GitHub main branch...
git branch -M main
git push -u origin main

echo.
echo ======================================================
echo   DEPLOYMENT PUSH COMPLETED SUCCESSFULLY
echo ======================================================
echo Render will automatically detect the new commit and rebuild.
echo.
pause
