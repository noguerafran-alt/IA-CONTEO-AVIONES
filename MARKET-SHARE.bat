@echo off
setlocal EnableExtensions
title MARKET SHARE YPF - Aeroparque (local)
cd /d "%~dp0"

REM ============================================================
REM   El market share, SOLO. No usa el dongle ni el ADS-B: es
REM   HTTP contra la API de Aeropuertos Argentina y una cuenta.
REM
REM   Por eso corre aparte y puede correr con el radar apagado,
REM   en cualquier maquina con internet. Un proceso: sondea cada
REM   5 min y sirve la pagina.
REM ============================================================

echo.
echo ===============================================
echo   MARKET SHARE YPF - Aeroparque
echo   Datos de Aeropuertos Argentina, en vivo
echo ===============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo   ERROR: falta el entorno virtual.
    echo   Ejecuta primero INSTALAR-Y-EJECUTAR.bat
    echo.
    pause
    exit /b 1
)

REM configuracion.bat define ADSB_OFICIAL, que es donde se acumula. Llamarlo y
REM no repetir la ruta evita que esta app escriba en una base distinta de la que
REM lee el tablero grande.
if exist "configuracion.bat" call "configuracion.bat"

if not exist "ypf_clientes.json" (
    echo   AVISO: no hay ypf_clientes.json, asi que NO se va a poder calcular
    echo   el share. La pagina igual sirve: muestra que aerolineas hay y con
    echo   que codigo, que es justo lo que hay que poner en esa lista.
    echo   Copia ypf_clientes.ejemplo.json y llenalo.
    echo.
)

start  http://127.0.0.1:8600/

".venv\Scripts\python.exe" ms_local.py

echo.
echo   Termino con codigo %ERRORLEVEL%.
echo.
pause
