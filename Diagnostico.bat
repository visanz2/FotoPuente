@echo off
title FotoPuente - modo diagnostico
cd /d "%~dp0"

echo.
echo   ============================================================
echo     MODO DIAGNOSTICO
echo   ============================================================
echo.
echo   Esta ventana muestra TODO lo que llega desde el movil.
echo.
echo   Que hacer:
echo     1. Deja esta ventana visible.
echo     2. En el iPhone abre la direccion que aparece abajo, pero
echo        cambiando el final por  /prueba
echo        Ejemplo:  https://192.168.1.141:8765/prueba
echo     3. Mira que aparece aqui y que aparece en el movil.
echo.
echo   Interpretacion:
echo     - No aparece NADA           -^> no llega la conexion (red/firewall)
echo     - "conexion rechazada"      -^> llega, pero falla el cifrado
echo     - "GET /prueba -^> 200"      -^> todo bien; el fallo esta en la pagina
echo.
echo   ============================================================
echo.

py -3 fotopuente.py --registro --sin-panel

echo.
echo   FotoPuente se ha cerrado.
pause
