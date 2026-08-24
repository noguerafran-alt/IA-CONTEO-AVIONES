@echo off
REM Abre el dashboard con los datos publicados desde Aeroparque.
REM Va en la OTRA PC, la que solo mira. Doble clic y listo.
REM
REM COPIA LA BASE A DISCO LOCAL ANTES DE ABRIRLA, y no la abre donde esta. No es
REM precaucion de mas: SQLite coordina el acceso entre procesos con archivos de
REM bloqueo, y esos bloqueos NO cruzan una carpeta sincronizada. Abrirla ahi
REM mismo mientras OneDrive la baja de nuevo es como se corrompe una base sin
REM que nadie haga nada raro.
REM
REM La copia ademas deja seguir mirando si OneDrive se desconecta.

cd /d "%~dp0"
title Datos de Aeroparque (solo lectura)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: no se encontro el entorno virtual en .venv
    echo Ejecuta primero INSTALAR-Y-EJECUTAR.bat
    echo.
    pause
    exit /b 1
)

call "%~dp0configuracion.bat"

if not exist "%ADSB_COMPARTIDO%\adsb_log.db" (
    echo.
    echo   No se encontro la base publicada en:
    echo     %ADSB_COMPARTIDO%
    echo.
    echo   Revisa que:
    echo    - En la PC de Aeroparque este corriendo PUBLICAR-DATOS.bat
    echo    - OneDrive ya haya terminado de bajar la carpeta ^(icono sin flechas^)
    echo    - ADSB_COMPARTIDO en configuracion.bat apunte a la carpeta correcta
    echo.
    pause
    exit /b 1
)

REM A LOCALAPPDATA y no a la carpeta del repo: el repo puede estar el mismo
REM dentro de OneDrive, y copiar de una carpeta sincronizada a otra no resuelve
REM nada.
set "COPIA_LOCAL=%LOCALAPPDATA%\adsb-visor"
if not exist "%COPIA_LOCAL%" mkdir "%COPIA_LOCAL%"

echo.
echo Copiando los datos publicados a disco local...
copy /y "%ADSB_COMPARTIDO%\adsb_log.db" "%COPIA_LOCAL%\adsb_log.db" >nul
if errorlevel 1 (
    echo   ERROR: no se pudo copiar. Puede que OneDrive todavia la este bajando.
    pause
    exit /b 1
)

REM De cuando son los datos. Se muestra ANTES de abrir el navegador porque es la
REM diferencia entre esta pantalla y la de la PC que graba: aca se ve una foto, y
REM sin decir de cuando es, una foto vieja se lee como el estado actual.
if exist "%ADSB_COMPARTIDO%\estado.json" (
    echo.
    echo   Los datos publicados dicen:
    ".venv\Scripts\python.exe" mostrar_publicado.py "%ADSB_COMPARTIDO%\estado.json"
)

REM La ubicacion del receptor la manda la PC QUE GRABO, no esta. Si esta PC
REM midiera desde su propio preset, mostraria distancias calculadas desde un
REM lugar donde nunca hubo una antena.
".venv\Scripts\python.exe" mostrar_publicado.py "%ADSB_COMPARTIDO%\estado.json" --exportar "%TEMP%\adsb_receptor.bat" >nul 2>&1
if exist "%TEMP%\adsb_receptor.bat" (
    call "%TEMP%\adsb_receptor.bat"
    del "%TEMP%\adsb_receptor.bat"
)

set "ADSB_DB=%COPIA_LOCAL%\adsb_log.db"

echo.
echo Abriendo el dashboard ^(solo lectura^)...
start "Datos de Aeroparque - servidor" /min ".venv\Scripts\python.exe" -m uvicorn main:app --app-dir webapp --host 127.0.0.1 --port 8000

timeout /t 4 /nobreak >nul
start "" "http://localhost:8000/adsb/analisis"

echo.
echo ===============================================
echo   LISTO - los datos estan en
echo   http://localhost:8000/adsb/analisis
echo.
echo   Es una FOTO de los datos, no tiempo real.
echo   Volve a ejecutar este archivo para actualizarla.
echo.
echo   NO arranques la grabacion desde aca: no hay
echo   antena en esta PC.
echo ===============================================
echo.
pause
