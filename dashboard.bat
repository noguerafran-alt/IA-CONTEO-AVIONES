@echo off
REM Arranca el dashboard y abre el navegador automaticamente.
REM Doble clic en este archivo y listo.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: no se encontro el entorno virtual en .venv
    echo Crealo con:  python -m venv .venv
    echo Y luego:     .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo Iniciando el servidor del dashboard...
start "Runway Dashboard - servidor" /min ".venv\Scripts\python.exe" -m uvicorn main:app --app-dir webapp --host 127.0.0.1 --port 8000

REM Le damos unos segundos a uvicorn antes de abrir el navegador.
timeout /t 4 /nobreak >nul

echo Abriendo el navegador en http://localhost:8000 ...
start "" "http://localhost:8000"

echo.
echo Listo. El servidor quedo corriendo en una ventana minimizada
echo llamada "Runway Dashboard - servidor".
echo Para detenerlo, cerra esa ventana.
echo.
timeout /t 5 /nobreak >nul
