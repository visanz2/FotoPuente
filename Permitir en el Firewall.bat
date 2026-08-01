@echo off
title FotoPuente - permiso de red
cd /d "%~dp0"

:: Necesita permisos de administrador para tocar el firewall de Windows.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Pidiendo permisos de administrador...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0firewall.ps1"

pause
