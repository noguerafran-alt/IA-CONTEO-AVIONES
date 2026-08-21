@echo off
setlocal EnableExtensions
title IA-CONTEO-AVIONES - Instalador ADS-B (RTL-SDR)
cd /d "%~dp0"

echo.
echo ===============================================
echo   INSTALADOR ADS-B PARA RTL-SDR (incluye V4)
echo ===============================================
echo.
echo   Esto baja los binarios oficiales de RTL-SDR Blog
echo   (rtl_adsb.exe y sus drivers, compatibles con el V4)
echo   y el instalador del driver USB.
echo.
echo   No hace falta ningun programa externo de ADS-B:
echo   este proyecto lee el dongle directamente.
echo.

set "TOOLS=%~dp0tools"
set "RTLDIR=%TOOLS%\rtlsdr"

if exist "%RTLDIR%\rtl_adsb.exe" (
    echo   Ya esta instalado en %RTLDIR%
    echo   Para reinstalar, borra esa carpeta y volve a correr esto.
    echo.
    goto :zadig
)

echo [1/3] Descargando binarios de RTL-SDR Blog...
if not exist "%RTLDIR%" mkdir "%RTLDIR%"
set "ZIP=%TEMP%\rtlsdr_release.zip"

powershell -NoProfile -Command ^
  "try { Invoke-WebRequest 'https://github.com/rtlsdrblog/rtl-sdr-blog/releases/latest/download/Release.zip' -OutFile '%ZIP%' -UseBasicParsing; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 (
    echo.
    echo   ERROR: no se pudo descargar. Revisa la conexion a internet, o
    echo   bajalo a mano desde:
    echo     https://github.com/rtlsdrblog/rtl-sdr-blog/releases
    echo   y copia rtl_adsb.exe + rtlsdr.dll + msvcr100.dll + pthreadVC2.dll
    echo   a la carpeta tools\rtlsdr\
    echo.
    pause
    exit /b 1
)

echo [2/3] Extrayendo...
set "EXTRACT=%TEMP%\rtlsdr_extract"
if exist "%EXTRACT%" rmdir /s /q "%EXTRACT%" >nul 2>&1
powershell -NoProfile -Command "Expand-Archive -Path '%ZIP%' -DestinationPath '%EXTRACT%' -Force"
if errorlevel 1 (
    echo   ERROR al extraer el archivo descargado.
    pause
    exit /b 1
)

REM Los binarios de 64 bits son los correctos para Windows moderno.
copy /y "%EXTRACT%\x64\rtl_adsb.exe"    "%RTLDIR%\" >nul
copy /y "%EXTRACT%\x64\rtlsdr.dll"      "%RTLDIR%\" >nul
copy /y "%EXTRACT%\x64\msvcr100.dll"    "%RTLDIR%\" >nul
copy /y "%EXTRACT%\x64\pthreadVC2.dll"  "%RTLDIR%\" >nul
copy /y "%EXTRACT%\x64\rtl_sdr.exe"     "%RTLDIR%\" >nul 2>&1
copy /y "%EXTRACT%\x64\rtl_test.exe"    "%RTLDIR%\" >nul 2>&1

if not exist "%RTLDIR%\rtl_adsb.exe" (
    echo   ERROR: no aparecio rtl_adsb.exe tras extraer. La estructura del
    echo   paquete puede haber cambiado. Revisa %EXTRACT% a mano.
    pause
    exit /b 1
)

del "%ZIP%" >nul 2>&1
rmdir /s /q "%EXTRACT%" >nul 2>&1
echo       Instalado en %RTLDIR%

echo [3/3] Verificando que el ejecutable corre...
"%RTLDIR%\rtl_adsb.exe" -d 0 >"%TEMP%\rtl_adsb_check.txt" 2>&1
findstr /C:"No supported devices" "%TEMP%\rtl_adsb_check.txt" >nul
if not errorlevel 1 (
    echo       OK - el programa corre bien. ^(Falta el driver del dongle,
    echo       eso lo resuelve el paso siguiente.^)
) else (
    echo       Corrio sin el mensaje esperado; revisalo cuando conectes el
    echo       dongle real, puede ser normal.
)
del "%TEMP%\rtl_adsb_check.txt" >nul 2>&1
echo.

:zadig
echo ===============================================
echo   PASO MANUAL: instalar el driver del dongle
echo ===============================================
echo.
echo   Windows no trae un driver para "modo SDR" -- por defecto reconoce
echo   el dongle como sintonizador de TV, que no sirve para esto. Zadig
echo   reemplaza ese driver por WinUSB, que es el que necesita rtl_adsb.
echo.
echo   1. Conecta el dongle RTL-SDR ahora, si todavia no lo hiciste.
echo   2. Se va a abrir Zadig. Ahi:
echo        - Opciones ^(menu^) -^> tildar "List All Devices"
echo        - En el desplegable, elegi "Bulk-in, Interface (Interface 0)"
echo          (o el nombre que tenga el dongle, ej. "RTL2838UHIDIR")
echo        - A la derecha, confirma que dice "WinUSB"
echo        - Click en "Replace Driver" (o "Install Driver")
echo   3. Cerra Zadig cuando termine.
echo.
pause

set "ZADIG=%TEMP%\zadig.exe"
if not exist "%ZADIG%" (
    echo   Descargando Zadig...
    powershell -NoProfile -Command ^
      "try { Invoke-WebRequest 'https://github.com/pbatard/libwdi/releases/latest/download/zadig-2.9.exe' -OutFile '%ZADIG%' -UseBasicParsing; exit 0 } catch { exit 1 }"
    if errorlevel 1 (
        echo   No se pudo descargar Zadig automaticamente.
        echo   Bajalo a mano de: https://zadig.akeo.ie/
        pause
        goto :final
    )
)
start "" "%ZADIG%"
echo.
echo   Zadig se abrio en otra ventana. Segui los pasos de arriba ahi.
echo.
pause

:final
echo.
echo ===============================================
echo   LISTO
echo ===============================================
echo.
echo   Con el dongle conectado y el driver instalado, doble clic en
echo   GRABAR-ADSB.bat para empezar a grabar.
echo.
pause
