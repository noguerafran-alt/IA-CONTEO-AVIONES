@echo off
REM Arranca el sistema configurado para medir DESDE AEROPARQUE, a 1153 m de la
REM pista, y no desde San Isidro. Doble clic y listo.
REM
REM Por que un .bat aparte y no dashboard.bat: la ubicacion de la antena entra
REM por variable de entorno, y una variable que hay que acordarse de exportar a
REM mano es una variable que algun dia no se va a exportar. Ese dia el sistema
REM mide todas las distancias desde San Isidro sin avisar, y los numeros salen
REM mal sin que nada falle.

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

REM Si quedo un servidor viejo levantado, sigue corriendo el CODIGO viejo y con
REM la configuracion vieja: la pagina se veria bien y estaria midiendo desde San
REM Isidro. Por eso se avisa en vez de arrancar un segundo servidor que no va a
REM poder tomar el puerto.
netstat -ano | findstr /r /c:"127.0.0.1:8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo ATENCION: ya hay algo escuchando en el puerto 8000.
    echo.
    echo   Ese proceso arranco con OTRA configuracion y otro codigo. Si lo dejas
    echo   corriendo, el sistema va a seguir midiendo desde San Isidro.
    echo.
    echo   Cerra esa ventana ^(la que dice "servidor"^) y volve a ejecutar este
    echo   archivo.
    echo.
    pause
    exit /b 1
)

REM ADSB_RECEIVER=aeroparque trae de una las coordenadas y la altura del preset
REM de receiver.py. La altura de 3 m es una suposicion de armado portatil: si la
REM medis con cinta, descomenta la linea de abajo y poné el valor real. A esta
REM distancia igual no cambia la conclusion -con la antena a 1 m el horizonte al
REM suelo ya son 4.1 km contra 1.15 km a la pista-, pero el numero que publica
REM el mapa sale de ahi.
set ADSB_RECEIVER=aeroparque
REM set ADSB_ANTENNA_M=3

REM Ganancia automatica y no 49.6. El maximo del R820T es lo correcto LEJOS;
REM pegado a la pista satura y se pierden mensajes. Medido en la otra punta:
REM con ganancia 30 desde San Isidro entraba UNA aeronave en 70 s contra 6-8 con
REM 49.6, asi que tampoco se puede bajar a ciegas. 'auto' es el punto de partida
REM y el panel de senal de /adsb es donde se decide si moverla.
REM Ganancia 20 y NO auto. Medido desde San Isidro, a 13.3 km: con auto el
REM 11.5%% de los mensajes pasaba de -6 dBFS y el pico llegaba a -4.1, o sea
REM contra el techo del receptor. A 1.15 km del umbral la senal llega unos
REM 21 dB mas fuerte (20*log10(13300/1150)), asi que auto daria +17 dBFS:
REM saturacion total, y saturar hace PERDER mensajes. Con 25 el pico era
REM -23.6 y la saturacion 0%%, asi que 20 deja margen.
REM Si en el dashboard entran pocas aeronaves y la saturacion figura en 0%%,
REM subila. El panel de senal de /adsb es el instrumento, no la intuicion.
set ADSB_GAIN=20

REM La fuente por defecto seria "auto", que resuelve a rtl_adsb y NUNCA elige
REM IQ. Hay que fijarla o el camino recomendado queda como un paso manual que se
REM olvida, y olvidarse no falla: graba igual, peor y sin avisar. IQ es el unico
REM que mide dBFS -sin eso no se puede decidir la ganancia, que es LA decision
REM estando pegado a la pista- y el unico que corrige errores de un bit en la
REM direccion: 141 de los 143 fantasmas del historico entraron por rtl_adsb.
set ADSB_SOURCE=iq

echo.
echo   Receptor : Aeroparque, 1153 m del umbral 13
echo   Antena   : 3 m ^(horizonte al suelo 7.1 km^)
echo   Ganancia : auto
echo   Fuente   : IQ crudo ^(mide senal y corrige 1 bit^)
echo   Objetivo : SABE
echo.
echo Iniciando el servidor...
start "Runway Dashboard - Aeroparque" /min ".venv\Scripts\python.exe" -m uvicorn main:app --app-dir webapp --host 127.0.0.1 --port 8000

timeout /t 4 /nobreak >nul

echo Abriendo el navegador...
start "" "http://localhost:8000/adsb"

echo.
echo Listo. La pagina que se abrio es /adsb: ahi se ARRANCA LA GRABACION con el
echo boton, eligiendo la fuente "IQ crudo" ^(es la unica que mide el nivel de
echo senal, que es lo que hace falta para juzgar la ganancia de cerca^).
echo.
echo Despues, para ver si entran aviones EN LA PISTA, mira /adsb/mapa: el
echo circulo verde punteado es el horizonte al suelo y la pista tiene que quedar
echo adentro.
echo.
timeout /t 8 /nobreak >nul
