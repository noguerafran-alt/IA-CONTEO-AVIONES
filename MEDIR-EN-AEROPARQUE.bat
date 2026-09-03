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

REM La ubicacion ya no se fija aca: salio a configuracion.bat para que este
REM archivo y dashboard.bat no puedan discrepar. Eran dos copias de la misma
REM linea y la de dashboard.bat directamente faltaba, que es como el 23/08 se
REM midieron nueve horas desde San Isidro con la antena ya en Aeroparque.
call "%~dp0configuracion.bat"

REM Ganancia automatica y no 49.6. El maximo del R820T es lo correcto LEJOS;
REM pegado a la pista satura y se pierden mensajes. Medido en la otra punta:
REM con ganancia 30 desde San Isidro entraba UNA aeronave en 70 s contra 6-8 con
REM 49.6, asi que tampoco se puede bajar a ciegas. 'auto' es el punto de partida
REM y el panel de senal de /adsb es donde se decide si moverla.
REM Ganancia 49.6, el maximo del R820T. Estuvo en 20 y estaba MAL.
REM
REM El 20 salia de extrapolar: desde San Isidro, a 13.3 km, con auto el 11.5%%
REM de los mensajes pasaba de -6 dBFS, y a 1.15 km la senal llega unos 21 dB
REM mas fuerte (20*log10(13300/1150)), asi que se predijo saturacion total. La
REM cuenta esta bien y la conclusion es falsa. Medido EN LA AEROPLANTA DE YPF
REM el 2026-09-03 con adsb_iq.py --medir, que cuenta SOLO CRC verificable:
REM
REM     49.6 -> 129 verificados (4.3/s), 6 aeronaves, mediana -19.3 dBFS
REM     auto ->  67 verificados (2.7/s), 5 aeronaves, mediana  -8.2 dBFS
REM     30   ->  34 verificados (1.3/s), 4 aeronaves, mediana -16.7 dBFS
REM     20   ->  24 verificados (0.6/s), 2 aeronaves, mediana -13.4 dBFS
REM
REM Por que fallo la prediccion: los +21 dB valen para un avion EN la pista, y
REM esos son un punado. La mayoria de lo que se decodifica esta a decenas de km
REM y llega debil igual. Bajar la ganancia para proteger al caso raro y fuerte
REM mata a los muchos lejanos, y el conteo total cae 5x.
REM
REM El cartel de "puede estar saturando" salio en las CUATRO corridas, hasta con
REM ganancia 20: mira el pico (-3 a -4 dBFS siempre), no los verificados, asi
REM que aca no distingue nada. Y la mediana empeora al SUBIR la ganancia
REM (-19.3 con 49.6 contra -13.4 con 20) porque entran los debiles lejanos, no
REM porque la senal sea peor. El unico numero que decide es el de --medir.
set ADSB_GAIN=49.6

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
