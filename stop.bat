@echo off
title Lohela â€” Stopping
echo.
echo  ============================
echo   LOHELA â€” Shutting down...
echo  ============================
echo.

cd /d "%~dp0"

echo [1/3] Closing frontend window and Vite process...
taskkill /fi "windowtitle eq Lohela Frontend*" /t /f >nul 2>&1
taskkill /im node.exe /f >nul 2>&1
taskkill /im npm.cmd /f >nul 2>&1

echo [2/3] Stopping backend services...
docker compose down

echo [3/3] Done.
echo.
echo  ============================
echo   All services stopped.
echo   Your data is safe in the
echo   Docker volume (postgres_data).
echo  ============================
echo.
timeout /t 3 /nobreak >nul
exit
