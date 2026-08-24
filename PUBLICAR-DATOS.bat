@echo off
REM Publica lo grabado a la carpeta compartida, para verlo desde otra PC.
REM Va en la PC QUE GRABA (la de Aeroparque). Doble clic y dejar abierto.
REM
REM Por que hace falta esto y no alcanza con grabar directo en OneDrive: la base
REM abre en WAL y son TRES archivos (.db, -wal, -shm). OneDrive los sube por
REM separado, sin saber que son un solo objeto, y una copia del .db sin el -wal
REM que le corresponde no queda incompleta a la vista: queda CORRUPTA, y lo dice
REM recien cuando alguien la lee. Ver publicar_datos.py.
REM
REM Se puede dejar corriendo junto con la grabacion: publicar abre la base en
REM solo lectura, asi que no puede tocar lo que se esta grabando.

cd /d "%~dp0"
title Publicando datos a la carpeta compartida

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: no se encontro el entorno virtual en .venv
    echo Ejecuta primero INSTALAR-Y-EJECUTAR.bat
    echo.
    pause
    exit /b 1
)

call "%~dp0configuracion.bat"

echo.
echo ===============================================
echo   PUBLICAR DATOS A LA CARPETA COMPARTIDA
echo ===============================================
echo.
echo   Grabando en   : %ADSB_DB%
echo   Publicando a  : %ADSB_COMPARTIDO%
echo   Midiendo desde: %ADSB_RECEIVER%
echo.
echo   Se republica cada 5 minutos. Dejar esta ventana abierta.
echo   Ctrl+C para cortar.
echo.

REM 300 s y no cada pocos segundos: cada publicacion reescribe la base entera y
REM el CSV, y OneDrive tiene que subir los dos. Publicar muy seguido lo deja
REM sincronizando permanentemente sin que nadie vea los datos mas rapido.
".venv\Scripts\python.exe" publicar_datos.py --cada 300

echo.
pause
