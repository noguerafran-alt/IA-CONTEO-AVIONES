@echo off
REM CONFIGURACION DEL SISTEMA. Unico lugar del repo donde se define DONDE esta
REM la antena y DONDE viven los datos.
REM
REM No se ejecuta solo: lo llaman con "call" los lanzadores (dashboard.bat,
REM MEDIR-EN-AEROPARQUE.bat, GRABAR-ADSB.bat, INSTALAR-Y-EJECUTAR.bat). Si se
REM muda la antena, se cambia ACA y nada mas.
REM
REM POR QUE UN ARCHIVO APARTE. La ubicacion entra por variable de entorno, y
REM MEDIR-EN-AEROPARQUE.bat ya decia por que eso es fragil: "una variable que
REM hay que acordarse de exportar a mano es una variable que algun dia no se va
REM a exportar. Ese dia el sistema mide todas las distancias desde San Isidro
REM sin avisar, y los numeros salen mal sin que nada falle."
REM
REM Ese dia fue el 23/08: la antena ya estaba en Aeroparque y el servidor se
REM arranco con el acceso directo a dashboard.bat, que no fijaba ninguna
REM variable ADSB_*. Midio nueve horas desde San Isidro, a 13,3 km de la pista,
REM mientras las operaciones detectadas estaban a 0,19-1,15 km.
REM
REM La solucion no es copiar la linea en cada .bat -las copias se desincronizan,
REM que es lo que ese archivo queria evitar- sino tener UNA definicion que todos
REM llamen. Asi nadie tiene que acordarse de nada.
REM
REM Valores posibles y el detalle de cada preset, en .env.example:
REM   san-isidro   (por defecto en el codigo) antena a 10 m
REM   aeroparque   1153 m del umbral 13, antena a 3 m
REM   ypf          Torre YPF, Puerto Madero, antena a 160 m
REM   -34.6054,-58.3625   o   SABE
REM Desde el 2026-09-03 a la tarde la antena esta AL COSTADO DE LA PISTA, a
REM 266 m del eje y a mitad de campo, detras de un doble vidrio. El preset
REM 'aeroparque' viejo -a 1153 m del umbral 13- se deja definido porque es desde
REM donde se grabo el historico, pero ya no es donde esta la antena.
set ADSB_RECEIVER=aeroparque-pista

REM ADSB_ANTENNA_M pisa la altura del preset. Descomentar solo si se midio con
REM cinta: a esta distancia no cambia la conclusion -con la antena a 1 m el
REM horizonte al suelo ya son 4.1 km contra 1.15 km a la pista- pero es el
REM numero que publica el mapa.
REM set ADSB_ANTENNA_M=3


REM ===========================================================================
REM DONDE VIVEN LOS DATOS
REM ===========================================================================
REM
REM EN DISCO LOCAL, NUNCA EN ONEDRIVE. No es preferencia: es que SQLite y una
REM carpeta que se sincroniza sola son incompatibles, y el modo en que fallan es
REM el peor posible -sin ruido, y recien al leer-.
REM
REM La base abre en WAL, o sea que en todo momento son TRES archivos:
REM
REM   adsb_log.db        lo que ya se consolido
REM   adsb_log.db-wal    lo recien escrito, todavia no integrado
REM   adsb_log.db-shm    el indice de bloqueos entre procesos
REM
REM OneDrive no sabe que los tres son UN objeto: sube cada uno cuando cambia,
REM por separado. Si sincroniza el .db sin el -wal que le corresponde, la copia
REM que baja la otra PC no queda visiblemente incompleta: queda CORRUPTA, y lo
REM dice recien cuando alguien la lee. Y el -shm es justamente el mecanismo con
REM el que SQLite evita que dos procesos se pisen: ese mecanismo NO cruza la
REM red, asi que si dos PC abren la misma base, las dos creen tener el candado.
REM
REM Por eso se graba local y se PUBLICA una copia consolidada a la carpeta
REM compartida con publicar_datos.py, que usa la API de backup de SQLite (una
REM copia de archivo con WAL activo puede salir incompleta sin avisar).
set ADSB_DB=C:\adsb-datos\adsb_log.db

REM Carpeta compartida donde se publica la copia para las otras PC. Va dentro de
REM OneDrive a proposito: ahi el archivo es un volcado quieto que nadie tiene
REM abierto, que es el unico uso de una carpeta sincronizada que es seguro.
set ADSB_COMPARTIDO=%OneDrive%\ADSB-AEROPARQUE

REM La base de la VERDAD EXTERNA: lo que publica Aeropuertos Argentina, que el
REM poller GRABAR-OFICIAL.bat acumula. Va SEPARADA de ADSB_DB a proposito -- ver
REM el docstring de aa2000.py -- porque es la referencia contra la que se mide el
REM sistema, y mezclarla con las mediciones propias hace posible confundirlas.
set ADSB_OFICIAL=C:\adsb-datos\aa2000_oficial.db
