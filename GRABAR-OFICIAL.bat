@echo off
setlocal EnableExtensions
title IA-CONTEO-AVIONES - Verdad externa AA2000 (24/7)
cd /d "%~dp0"

REM ============================================================
REM   Sondea la API de Aeropuertos Argentina y acumula el
REM   historico de horas REALES de despegue y aterrizaje.
REM
REM   POR QUE TIENE QUE CORRER SIEMPRE. El feed de las pantallas
REM   arranca en "ahora" y va hacia adelante ~26 h. La hora real
REM   de un despegue aparece cuando ocurre y a las pocas horas
REM   DESAPARECE PARA SIEMPRE: no hay endpoint que la devuelva
REM   despues. Lo que este proceso no haya visto no se puede
REM   recuperar de ninguna manera.
REM
REM   No toca el dongle ni compite con la grabacion ADS-B: es
REM   HTTP contra un servidor ajeno y escribe en su propia base.
REM   Los dos pueden correr a la vez sin estorbarse.
REM ============================================================

echo.
echo ===============================================
echo   VERDAD EXTERNA - Aeropuertos Argentina
echo   Horas reales, matricula y pasajeros
echo ===============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo   ERROR: falta el entorno virtual.
    echo   Ejecuta primero INSTALAR-Y-EJECUTAR.bat
    echo.
    pause
    exit /b 1
)

REM configuracion.bat es el UNICO lugar donde se definen las rutas y el
REM receptor. Llamarlo aca y no repetir las variables es lo que evita que este
REM lanzador escriba en una base distinta de la que lee el tablero.
if exist "configuracion.bat" call "configuracion.bat"

echo   Base de la verdad externa: %ADSB_OFICIAL%
echo   Sondeo cada %AA2000_INTERVALO_S% s ^(vacio = 300 s por defecto^)
echo.
echo   Se queda corriendo. Ctrl+C para cortar; al cortar imprime el resumen.
echo.

".venv\Scripts\python.exe" aa2000.py --seguir

REM Si el proceso termina solo -un corte de red largo, o la API que cambia- el
REM codigo de salida queda a la vista, en vez de cerrarse la ventana y dejar la
REM impresion de que sigue grabando.
echo.
echo   El poller termino con codigo %ERRORLEVEL%.
echo.
pause
