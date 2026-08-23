# Estado del sistema ADS-B — para retomar en otra sesión

Es un traspaso, no documentación del proyecto: dice dónde quedó todo, qué está
sin resolver y qué decisiones ya se tomaron para no rediscutirlas. El
**README.md** sigue siendo la documentación de verdad.

> **Este archivo se actualiza con cada cambio del repo, en el mismo commit.** El
> proyecto se trabaja desde varias sesiones y varios usuarios de la misma
> máquina, y las notas de una sesión no las ve la siguiente: un cambio que no
> quedó acá es un cambio que la próxima sesión va a redescubrir, o va a deshacer
> sin saberlo. Qué corresponde anotar está en `CLAUDE.md`.

Última actualización: 2026-08-22, con el mapa del aeropuerto y el arreglo de la
costa.

---

## Lo primero que hay que saber

**La antena está en San Isidro, no en Aeroparque.** El repo nombra Aeroparque por
todos lados, pero eso es el material de *video* (`data/aeroparque_full.mp4`). Es
el error más fácil de cometer acá.

**Y hay una mudanza planeada:** Fran quiere poner la antena a ~300 m de la pista
de Aeroparque (iba a probar el 2026-08-23), y también evaluó la torre de YPF en
Puerto Madero. Por eso **nada que dependa de la ubicación está hardcodeado**.

Distancias medidas con haversine:

| desde | Aeroparque SABE | San Fernando SADF | Ezeiza SAEZ |
|---|---|---|---|
| San Isidro (actual) | 13,3 km | 7,3 km | 39,1 km |
| Torre YPF (futuro) | 7,1 km | 26,8 km | 28,8 km |

**El objetivo del proyecto:** saber qué aviones aterrizan y despegan de un
aeropuerto cercano a la antena, 24/7. Aeroparque en este caso.

---

## Cómo se levanta

```bash
cd C:\Users\nogue\OneDrive\Desktop\CLAUDE\runway-video-analytics\webapp && ..\.venv\Scripts\python.exe main.py
```

Cuatro páginas, en `http://127.0.0.1:8000`:

| ruta | qué contesta |
|---|---|
| `/` | dashboard: **el apartado de Aeroparque** arriba, más la parte de cámara |
| `/adsb` | en vivo: registro completo por aeronave, señal, resumen histórico |
| `/adsb/analisis` | todo lo grabado: cobertura por campo, alcance, descartes, apartado del aeropuerto |
| `/adsb/mapa` | mapa Plotly con aviones rotados al rumbo, costa y pistas reales |
| `/aeropuerto/mapa` | **mapa de un solo aeropuerto**: centrado en la pista, solo lo que operó ahí |

La grabación se arranca y se para desde `/adsb`. **El dongle es exclusivo**: un
solo proceso puede tomarlo.

### Variables de entorno

Todas documentadas en `.env.example`. Ninguna es secreta (no van en `.env`).

| variable | default | para qué |
|---|---|---|
| `ADSB_RECEIVER` | `san-isidro` | dónde está la antena. Acepta `ypf`, `lat,lon` o código ICAO |
| `ADSB_ANTENNA_M` | del preset | altura de la antena. **Decide si se ven aviones en pista** |
| `ADSB_GAIN` | `49.6` | ganancia del receptor, o `auto`. Solo para la fuente IQ |
| `ADSB_AIRPORT` | `SABE` | qué aeropuerto contar. `NINGUNO` apaga el apartado |
| `ADSB_AIRPORT_RADIUS_KM` | `8` | radio del cilindro de operaciones |
| `ADSB_AIRPORT_CEILING_FT` | `4000` | techo del cilindro |
| `ADSB_ANALYSIS_KM` | `50` | recorte del análisis. **No** es un filtro de corrección |
| `ADSB_SURFACE_REF` | la del receptor | referencia CPR para posiciones en superficie |

Para probar en Aeroparque:

```bash
cd C:\Users\nogue\OneDrive\Desktop\CLAUDE\runway-video-analytics\webapp && cmd /c "set ADSB_GAIN=auto && set ADSB_RECEIVER=-34.5592,-58.4156 && set ADSB_ANTENNA_M=3 && ..\.venv\Scripts\python.exe main.py"
```

---

## Las cinco fuentes de datos, y cuál usar

En el selector de `/adsb`. Cada una tiene su explicación al pasar el mouse.

| fuente | programa | ¿toma el dongle? | ¿mide señal? |
|---|---|---|---|
| Automático | decide solo | sí, vía `rtl_adsb` | **no** |
| Dongle directo | `rtl_adsb.exe -e 1` | sí, exclusivo | no |
| **IQ crudo** | `rtl_sdr.exe` + `adsb_iq.py` | sí, exclusivo | **sí, dBFS** |
| Feed SBS-1 | otro programa | no, lo tiene el otro | no |
| dump1090 JSON | dump1090 | no, lo tiene él | no lo leemos |

**Usar IQ crudo.** Misma tasa de mensajes que `rtl_adsb` (41 vs 43 DF17 en 30 s,
medido) pero decodifica todo el rango Mode-S y cada mensaje trae su nivel de
señal. Es el único camino que permite juzgar la ganancia.

---

## Decisiones ya tomadas — no volver a discutirlas sin datos nuevos

Cada una salió de una medición, no de una preferencia.

**Ruido: una dirección vista una sola vez se descarta.** Las tramas
DF0/4/5/11/16/20/21 llevan la paridad XOR-eada con la dirección, así que su CRC
no se puede validar sola y cada trama de ruido inventa una aeronave. Había 2894
direcciones de un solo mensaje contra 295 reales — el titular decía 3123, 36
veces inflado. El umbral sale del espacio de direcciones: sobre 2²⁴, el azar
predice 0,43 direcciones repetidas entre 3777 tramas no verificables y se
observaron 133, o sea 313× más. La trama se **retiene**, no se tira, así que
cuando la dirección se repite entra igual. Es el mismo mecanismo que dump1090.

**El nivel de señal NO sirve para filtrar ruido.** Se probó y se descartó: el
ADS-B con CRC válido da −15,0 dBFS de mediana y el ruido −18,3, con el **89% del
ruido llegando más fuerte que el ADS-B real más débil** y picos a −3,9. Lo que
pasa el test de preámbulo no es ruido térmico sino energía de radio real. Sirve
para medir la antena, no para filtrar.

**El recorte de 50 km es de alcance, no de corrección.** El filtro físico
(`horizonte_km`, `limite_posicion_km`) rechaza posiciones *imposibles* y no se
negocia. El recorte de 50 km decide de qué porción del cielo se habla. Mezclarlos
haría mentir al sistema: la antena llega a 72,4 km medidos. Lo que queda afuera
se cuenta y se explica.

**Un aterrizaje no es una aproximación.** Un motor y al aire baja *igual* que un
aterrizaje, hasta los mismos pies; lo único que los separa es que uno se va. Por
eso se mira el reascenso posterior al punto más bajo, y las aproximaciones que no
se pudieron resolver se cuentan **aparte**.

**Más ganancia no es mejor.** Lejos cada dB alcanza un avión más lejano; pegado a
la pista el receptor satura y se pierden mensajes. Medido: con ganancia 30 desde
San Isidro la mediana cae a −33,5 dBFS y entra **una** aeronave en 70 s, contra
6-8 con 49,6.

**Plotly se sirve desde `/static`, no desde un CDN**, y no se usan sus modos
geográficos: necesitan tiles o topojson de internet. La costa sale de Natural
Earth y las pistas de OurAirports, horneadas en `geografia.py`.

---

## El problema abierto más importante

**Desde San Isidro no se puede contar operaciones de Aeroparque, y el sistema lo
dice.** De 1413 posiciones grabadas: **cero** por debajo de 1500 ft y **cero** a
menos de 3 km de Aeroparque. Lo más bajo son 2134 ft sobre el campo.

No es el horizonte teórico (a 2000 ft daría 114 km) sino **obstrucción real**:
trece kilómetros de ciudad, y un avión a 1000 ft a esa distancia queda a 1,3°
sobre el horizonte, que lo tapa cualquier edificio.

Validación por contraste, mismos datos, mismo código:

| aeropuerto | distancia | ¿ve la pista? | mínima vista | resultado |
|---|---|---|---|---|
| San Fernando | 7,3 km | **sí** | **215 ft AGL** | 1 aproximación a 450 ft |
| Aeroparque | 13,3 km | no | 2134 ft AGL | 0 aterrizajes, con advertencia |

**A 300 m de la pista esto se da vuelta.** Ahí conviene: bajar la ganancia
(`ADSB_GAIN=auto` para arrancar), mirar el panel de señal de `/adsb`, y si las
"aproximaciones sin resolver" quedan altas, subir `ADSB_AIRPORT_RADIUS_KM`.

**Y lo que ninguna antena arregla:** la matrícula y el tipo no viajan por radio,
salen de cruzar el ICAO24 contra el registro de OpenSky. El 47% del tráfico real
no está en ese snapshot — Copa (`0c…`), parte de JetSmart (`e8…`), bloques
recién asignados. Se ve el **vuelo** (ARG1403) casi siempre; el **avión físico**
(LV-FVN) solo si está en el registro. Son dos problemas distintos.

---

## Un bug ya arreglado que vale recordar

**No ordenar una costa por latitud.** Parece inofensivo y la destruye: una costa
no es monótona en latitud (bahías, el delta, la vuelta de Punta del Este), así que
ordenar hace que el trazo salte de un lado al otro. Medido: la orilla uruguaya
pasaba de 394 km a **1346 km** de largo, 3,4×, y en el mapa se veía como rayas
horizontales cruzando el río. `geografia.py` conserva el orden del trazo de
Natural Earth, y las dos orillas concatenadas **tal cual** ya cierran el anillo
—vienen en sentidos opuestos— así que invertir una lo cruza en diagonal.

## Cosas que van a confundir si nadie las avisó

**La base commitea cada 50 filas.** Consultar `adsb_log.db` con sqlite mientras
graba puede mostrar datos viejos. Me hizo perder un rato buscando un bug que no
existía: había 22 filas escritas sin confirmar y la consulta daba 0. El CSV de
`output/adsb/` se vuelca fila por fila y no tiene el problema.

**Casi todos los rumbos del mapa dicen "calculado".** Las ~10 000 filas
históricas son anteriores a la columna `track_deg`. Lo que se grabe de ahora en
más sale transmitido.

**La regla de superficie sigue "sin ejercitar".** Nunca se decodificó una
posición en tierra, así que su contador en cero significa "nunca corrió", no "sin
rechazos".

**`tools/` y `webapp/static/plotly.min.js` están en `.gitignore`.** Son 34,5 MB
de base OpenSky, los binarios del dongle y 1 MB de Plotly. Se bajan con
`aircraft_db.py`, `INSTALAR-ADSB.bat` y `webapp/bajar_plotly.py`.

---

## Módulos que se escribieron en esta sesión

| archivo | qué hace |
|---|---|
| `adsb_iq.py` | demodula ADS-B del IQ crudo midiendo dBFS, siguiendo dump1090 |
| `receiver.py` | dónde está la antena, horizonte de radio, distancias |
| `geografia.py` | costa del Río de la Plata y pistas reales, con su fuente |
| `aeropuerto.py` | atribuir operaciones a UN aeropuerto |
| `test_adsb_position.py` | 8 escenarios de posición y CRC |
| `webapp/bajar_plotly.py` | baja Plotly una vez |

Los tres archivos de test pasan: `test_adsb.py`, `test_adsb_events.py`,
`test_adsb_position.py`.

---

## Ideas que quedaron sin hacer

- **Identificar la aerolínea por el prefijo del distintivo** (JES = JetSmart,
  ARG = Aerolíneas, GLO = Gol). Daría el operador del 100% de los aviones con
  callsign, sin depender del registro. Es el mejor camino para el 47% que no
  resuelve matrícula.
- **Corrección de fase de dump1090**, su otro mecanismo de recuperación. No se
  hizo porque ya hay paridad de tasa con `rtl_adsb`.
- **Leer el `rssi` del `aircraft.json` de dump1090** — tres líneas en `adsb.py`,
  solo útil si se usa dump1090.
- **Bajar el intervalo de commit o pasar a WAL** si molesta ver la base atrasada
  mientras graba.
