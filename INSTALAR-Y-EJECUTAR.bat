@echo off
setlocal EnableExtensions
title IA-CONTEO-AVIONES - Instalador y arranque
cd /d "%~dp0"

echo.
echo ===============================================
echo   IA-CONTEO-AVIONES
echo   Instalacion y arranque automatico
echo ===============================================
echo.

REM ---------- 1. Buscar Python ----------
echo [1/5] Buscando Python...
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )

if not defined PY (
    echo.
    echo   No se encontro Python en esta PC.
    echo.
    choice /C SN /M "   Queres que intente instalarlo automaticamente"
    if errorlevel 2 goto :sin_python
    echo   Instalando Python 3.12 ...
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    if errorlevel 1 goto :sin_python
    echo.
    echo   Python quedo instalado. CERRA esta ventana y volve a
    echo   ejecutar este archivo para que tome el nuevo PATH.
    echo.
    pause
    exit /b 0
)

REM ---------- 2. Verificar version (hace falta 3.10 o mas) ----------
%PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERROR: el codigo necesita Python 3.10 o superior.
    %PY% --version
    echo   Actualizalo desde https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo       OK - %%v

REM ---------- 3. Entorno virtual ----------
echo [2/5] Preparando el entorno virtual...
if not exist ".venv\Scripts\python.exe" (
    echo       Creando .venv ^(tarda unos segundos^)...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo   ERROR: no se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
) else (
    echo       Ya existia.
)
set "VPY=.venv\Scripts\python.exe"

REM ---------- 4. Dependencias ----------
echo [3/5] Verificando dependencias...
"%VPY%" -c "import supervision, ultralytics, cv2, fastapi, uvicorn, jinja2" >nul 2>&1
if errorlevel 1 (
    echo       Faltan librerias. Instalando...
    echo       ^(la primera vez baja ~1.5 GB y puede tardar varios minutos^)
    echo.
    "%VPY%" -m pip install --upgrade pip --quiet
    "%VPY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   ERROR instalando dependencias. Revisa tu conexion a internet.
        pause
        exit /b 1
    )
    echo.
    echo       Instalacion completada.
) else (
    echo       Todo instalado.
)

REM ---------- 5. Configuracion opcional ----------
echo [4/5] Configuracion...
if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo       Se creo .env desde la plantilla.
        echo       Para leer matriculas, edita .env y pone tu clave de OpenRouter.
        echo       Sin eso el sistema igual detecta, sigue y cuenta aviones.
    )
) else (
    echo       .env ya configurado.
)

REM Modelo acelerado para GPU Intel (opcional, mejora la velocidad)
if not exist "yolov8n_openvino_model\" (
    if exist "yolov8n.pt" (
        echo       Generando modelo acelerado para GPU Intel...
        "%VPY%" export_openvino.py --weights yolov8n.pt >nul 2>&1
        if errorlevel 1 (
            echo       No se pudo generar ^(no es critico, se usa la CPU^).
        ) else (
            echo       Listo.
        )
    )
)

REM ---------- 6. Arrancar ----------
echo [5/5] Iniciando el dashboard...
set "LISTO="
start "IA-CONTEO-AVIONES - servidor" /min "%VPY%" -m uvicorn main:app --app-dir webapp --host 127.0.0.1 --port 8000

echo       Esperando a que levante el servidor...
REM Un solo PowerShell con reintentos adentro: lanzarlo en bucle desde el
REM .bat cuesta ~1s por arranque y hacia que la espera se pasara de largo.
powershell -NoProfile -Command "for($i=0;$i -lt 30;$i++){ try{ [void](Invoke-WebRequest http://127.0.0.1:8000 -UseBasicParsing -TimeoutSec 2); exit 0 }catch{ Start-Sleep -Milliseconds 700 } }; exit 1"
if errorlevel 1 ( set "LISTO=" ) else ( set "LISTO=1" )

if defined LISTO (
    start "" "http://localhost:8000"
    echo.
    echo ===============================================
    echo   LISTO - el dashboard esta en
    echo   http://localhost:8000
    echo.
    echo   El servidor quedo en una ventana minimizada
    echo   llamada "IA-CONTEO-AVIONES - servidor".
    echo   Cerra esa ventana para detenerlo.
    echo ===============================================
) else (
    echo.
    echo   El servidor tarda mas de lo normal en responder.
    echo   Proba abrir http://localhost:8000 a mano, y si no
    echo   funciona revisa la ventana del servidor por errores.
)
echo.
pause
exit /b 0

:sin_python
echo.
echo   Instala Python 3.10 o superior desde:
echo     https://www.python.org/downloads/
echo.
echo   IMPORTANTE: en el instalador, tilda la casilla
echo   "Add Python to PATH" antes de continuar.
echo.
pause
exit /b 1
