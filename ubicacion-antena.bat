@echo off
REM DONDE ESTA LA ANTENA. Unico lugar del repo donde se define.
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
set ADSB_RECEIVER=aeroparque

REM ADSB_ANTENNA_M pisa la altura del preset. Descomentar solo si se midio con
REM cinta: a esta distancia no cambia la conclusion -con la antena a 1 m el
REM horizonte al suelo ya son 4.1 km contra 1.15 km a la pista- pero es el
REM numero que publica el mapa.
REM set ADSB_ANTENNA_M=3
