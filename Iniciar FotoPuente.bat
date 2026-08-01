@echo off
title FotoPuente
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   No se encuentra Python. Instalalo desde https://www.python.org/downloads/
    echo   y marca la casilla "Add Python to PATH" durante la instalacion.
    echo.
    pause
    exit /b 1
)

py -3 fotopuente.py %*

echo.
echo   FotoPuente se ha cerrado.
pause
