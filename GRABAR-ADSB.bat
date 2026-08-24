@echo off
setlocal EnableExtensions
title IA-CONTEO-AVIONES - Grabacion ADS-B
cd /d "%~dp0"

echo.
echo ===============================================
echo   GRABACION DE DATOS ADS-B
echo   Registra cada aeronave en CSV y base de datos
echo ===============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo   ERROR: falta el entorno virtual.
    echo   Ejecuta primero INSTALAR-Y-EJECUTAR.bat
    echo.
    pause
    exit /b 1
)

echo   Antes de continuar, tiene que estar corriendo el software
echo   que lee el RTL-SDR y publica los datos ^(ver README^).
echo.
echo   Por defecto se conecta al feed SBS-1 en 127.0.0.1:30003
echo.

REM Desde donde se mide. Lo que se grabe con la ubicacion equivocada queda mal
REM para siempre: las distancias se calculan al guardar. Ver ubicacion-antena.bat.
call "%~dp0ubicacion-antena.bat"
echo Midiendo desde: %ADSB_RECEIVER%

".venv\Scripts\python.exe" adsb_record.py %*

echo.
pause
