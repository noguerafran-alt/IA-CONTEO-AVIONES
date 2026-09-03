# Estado del sistema ADS-B — para retomar en otra sesión

Es un traspaso, no documentación del proyecto: dice dónde quedó todo, qué está
sin resolver y qué decisiones ya se tomaron para no rediscutirlas. El
**README.md** sigue siendo la documentación de verdad.

> **Este archivo se actualiza con cada cambio del repo, en el mismo commit.** El
> proyecto se trabaja desde varias sesiones y varios usuarios de la misma
> máquina, y las notas de una sesión no las ve la siguiente: un cambio que no
> quedó acá es un cambio que la próxima sesión va a redescubrir, o va a deshacer
> sin saberlo. Qué corresponde anotar está en `CLAUDE.md`.

Última actualización: 2026-09-03, con las operaciones segmentadas por pasada y la carrera de pista.

---

## Lo primero que hay que saber

**Que el repo diga "Aeroparque" por todos lados no dice dónde está la antena.**
Eso es el material de *video* (`data/aeroparque_full.mp4`), y es el error más
fácil de cometer acá. Durante toda la historia del proyecto la antena estuvo en
**San Isidro**, a 13,3 km: todo el histórico se grabó desde ahí.

**La mudanza se hace el 2026-08-23** (hoy), a `-34.551378, -58.437306`, que está
a **1153 m del umbral 13** de Aeroparque. Quedó como preset `aeroparque` en
`receiver.py` y se arranca con `MEDIR-EN-AEROPARQUE.bat`. También se evaluó la
torre de YPF en Puerto Madero. Por eso **nada que dependa de la ubicación está
hardcodeado**.

Distancias medidas con haversine, a la **referencia** del aeropuerto:

| desde | Aeroparque SABE | San Fernando SADF | Ezeiza SAEZ |
|---|---|---|---|
| San Isidro (donde estuvo) | 13,3 km | 7,3 km | 39,1 km |
| **Aeroparque (desde hoy)** | **2,2 km** | 17,7 km | 31,4 km |
| Torre YPF (evaluada) | 7,1 km | 26,8 km | 28,8 km |

**Lo que arregla la mudanza no es el horizonte, es el ángulo.** Es la
corrección más importante de entender acá, porque el horizonte solo *parecía*
explicar el problema: desde San Isidro la pista de SABE quedaba a 12,26 km del
eje contra 13,0 km de horizonte, o sea que geométricamente entraba, y sin
embargo lo más bajo que se vio fueron 2134 ft. El límite era la obstrucción
urbana, y eso lo gobierna el ángulo de elevación:

| avión sobre la pista de SABE | desde San Isidro (12,26 km) | desde el punto nuevo (1,15 km) |
|---|---|---|
| en la pista | 0,02° | 0,24° |
| 500 ft | 0,71° | **7,55°** |
| 1000 ft | 1,42° | **14,84°** |
| 2134 ft (lo mínimo visto) | 3,04° | 29,49° |

Un edificio de 30 m tapa hasta 16,7° si está a 100 m, 5,7° a 300 m y 1,7° a 1 km.
Por eso a 1,42° no se veía nada y a 14,84° se ve casi todo.

**Lo que la mudanza NO arregla:** un avión *en la pista* se ve a 0,24°, que un
edificio de 30 m a 1 km sigue tapando. Las posiciones en tierra —las que nunca
se decodificaron— dependen de tener línea de vista limpia hacia el **sector
104–117° (ESE)**, que es donde cae la pista desde ese punto. Si siguen sin
aparecer, el sospechoso es la obstrucción, no el código.

La altura de antena, en cambio, deja de importar acá: con 1 m el horizonte al
suelo ya son 4,1 km contra 1,15 km a la pista. Es lo contrario de San Isidro,
donde 300 m de diferencia decidían todo. Los 3 m del preset son una suposición
de armado portátil; `ADSB_ANTENNA_M` la pisa si se mide.

**El objetivo del proyecto:** saber qué aviones aterrizan y despegan de un
aeropuerto cercano a la antena, 24/7. Aeroparque en este caso.

---

## Cómo se levanta

```bash
cd "C:\Users\nogue\OneDrive\Desktop\CLAUDE\RADAR YPF\webapp" && ..\.venv\Scripts\python.exe main.py
```

Pero para uso normal, **doble clic en `dashboard.bat`**: levanta el servidor con
la ubicación de la antena ya puesta. Arrancarlo a mano como arriba lo deja
midiendo desde San Isidro (ver «Nueve horas midiendo desde el lugar equivocado»).

Seis páginas, en `http://127.0.0.1:8000`:

| ruta | qué contesta |
|---|---|
| `/` | dashboard: **el apartado de Aeroparque** arriba, más la parte de cámara |
| `/adsb` | en vivo: registro completo por aeronave, señal, resumen histórico |
| `/adsb/analisis` | todo lo grabado: cobertura por campo, alcance, descartes, apartado del aeropuerto |
| `/adsb/mapa` | mapa Plotly con aviones rotados al rumbo, costa y pistas reales |
| `/aeropuerto` | **una fila por aeronave que operó ahí**, con todo lo que se sabe de ella |
| `/aeropuerto/mapa` | **mapa de un solo aeropuerto**: centrado en la pista, solo lo que operó ahí |

Las **cinco** que publican números referidos a la antena (todas menos `/label`)
muestran arriba la franja de `webapp/static/franja_receptor.js`, que dice desde
dónde se está midiendo y se pone **roja** si nadie lo eligió.

La grabación se arranca y se para desde `/adsb`. **El dongle es exclusivo**: un
solo proceso puede tomarlo.

### Variables de entorno

Todas documentadas en `.env.example`. Ninguna es secreta (no van en `.env`).

| variable | default | para qué |
|---|---|---|
| `ADSB_RECEIVER` | `san-isidro` | dónde está la antena. Acepta `ypf`, `lat,lon` o código ICAO. **No se pone a mano**: se define en `configuracion.bat`, que llaman los cuatro lanzadores. El valor por defecto ya no es dónde está la antena |
| `ADSB_ANTENNA_M` | del preset | altura de la antena. **Decide si se ven aviones en pista** |
| `ADSB_DB` | `C:\adsb-datos\adsb_log.db` | dónde graba. **En disco local, nunca en OneDrive** |
| `ADSB_COMPARTIDO` | `%OneDrive%\ADSB-AEROPARQUE` | carpeta donde se **publica** la copia para las otras PC |
| `ADSB_GAIN` | `49.6` | ganancia del receptor, o `auto`. Solo para la fuente IQ |
| `ADSB_AIRPORT` | `SABE` | qué aeropuerto contar. `NINGUNO` apaga el apartado |
| `ADSB_AIRPORT_RADIUS_KM` | `8` | radio del cilindro de operaciones |
| `ADSB_AIRPORT_CEILING_FT` | `4000` | techo del cilindro |
| `ADSB_ANALYSIS_KM` | `50` | recorte del análisis. **No** es un filtro de corrección |
| `ADSB_SURFACE_REF` | la del receptor | referencia CPR para posiciones en superficie |
| `ADSB_DB` | `adsb_log.db` junto al código | dónde vive la base. La define `adsb_record.py` y `webapp/main.py` la importa de ahí |
| `ADSB_SOURCE` | `auto` | qué fuente usar al iniciar. **Poner `iq`**: `auto` resuelve a `rtl_adsb` y nunca elige IQ |

Para medir en Aeroparque, doble clic en **`MEDIR-EN-AEROPARQUE.bat`**, que fija
las cuatro variables que hacen falta y frena si quedó un servidor viejo tomando
el puerto — ese servidor seguiría midiendo desde San Isidro sin decirlo. El
equivalente a mano:

```bash
cd "C:\Users\nogue\OneDrive\Desktop\CLAUDE\RADAR YPF\webapp" && cmd /c "set ADSB_RECEIVER=aeroparque && set ADSB_GAIN=20 && set ADSB_SOURCE=iq && ..\.venv\Scripts\python.exe main.py"
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

**Buena parte de ese "ruido" son errores de UN bit en la dirección, y se pueden
recuperar en vez de descartar.** Medido sobre 10 873 filas / 3252 direcciones:
llamando *sólidas* a las 109 con ≥10 mensajes o con posición decodificada, de las
3143 débiles hay **143 (4,5%) a exactamente 1 bit de distancia** de una sólida.
El azar predice 0,01% (control con 20 000 direcciones aleatorias): **450× de
enriquecimiento**, así que no es coincidencia. Otras 25 (0,8%) están a 2 bits y
2975 (94,7%) a ≥3, que sí es ruido de verdad.

Se ve a simple vista en los pares: `c065d3`→`e065d3` (ambas ARG1718),
`e0914a`/`e0f14a`/`e0b14e`→`e0b14a` (todas ARG1650), `e03419`→`e03409` (ARG1477).
Contraste con el bloque argentino `E00000–E3FFFF`, que es el único donde puede
haber un LV- de Aerolíneas: de los 57 distintivos `ARG*`, los **39 dentro** del
bloque tienen mediana de 73 mensajes y 17 con posición; los **18 fuera**, mediana
de **1** mensaje y **cero** posiciones.

**141 de esos 143 vinieron del camino `rtl_adsb`, que no corrige errores.**
`adsb_iq.py` ya tiene la corrección por síndrome (`_tabla_sindrome`,
`_corregir_un_bit`), pero el histórico es casi todo de la otra fuente (10 028
filas contra 845). Es un argumento nuevo y medido para la decisión de usar IQ. **Lo
que todavía NO está probado** es que el camino IQ los elimine: tiene sólo 27
direcciones débiles, 2 de ellas a 1 bit, y con n=27 no se puede distinguir 7,4% de
4,5%. Hace falta más grabación por IQ para cerrarlo.

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

**`ADSB_GAIN=auto` NO sirve para medir de cerca — usar 20.** Medido desde San
Isidro, a 13,3 km: con `auto` el **11,5% de los mensajes pasaba de −6 dBFS** y el
pico llegaba a **−4,1**, o sea contra el techo del receptor; con 25 el pico era
−23,6 y la saturación **0%**. A 1,15 km del umbral la señal llega unos **21 dB
más fuerte** (`20·log₁₀(13300/1150)`), así que `auto` daría **+17 dBFS**:
saturación total, y saturar hace *perder* mensajes. `MEDIR-EN-AEROPARQUE.bat`
fija **20** por eso. Si entran pocas aeronaves y la saturación figura en 0%,
subirla — el panel de señal de `/adsb` es el instrumento.

**La aerolínea sale del distintivo; la matrícula casi nunca.** `JES3104` es un
número de vuelo, no un avión, y ese vínculo no viaja por radio. Pero los tres
primeros caracteres **sí** son el designador ICAO del operador, y eso recupera
mucho: medido, **133 operadores por distintivo contra 17 por registro** — casi
ocho veces más. La tabla se deriva del propio registro de OpenSky
(`operator_icao`, 41 403 filas, 1458 designadores), no de una lista a mano.

La matrícula desde el distintivo solo aplica a **aviación general**, que la usa
*como* distintivo (`LVHCQ` → `LV-HCQ`). Validado contra los casos con verdad
conocida: **6 casos, 4 aciertos**. Los 2 fallos definieron las reglas: `a01de4`
decía `LVKCV` y era `N1064B` (bloque `a0`=EEUU vs `LV`=Argentina → **el chequeo
de país lo caza**), y `a88552` decía `N680XP` y era `N6480G` (los dos EEUU → **el
chequeo NO lo caza**, por eso las `N-` se excluyen). Quedan 4/4 en el patrón
`LV`, con **n=4**: se informa como *probable*, nunca como confirmada, y en la
interfaz va con punteado y el origen en el tooltip.

**El Doc 8643 de la OACI agrega lo que el registro no tiene.** Medido sobre las
107 aeronaves con typecode y 10+ mensajes: **27 ganan modelo** que el registro no
traía (`A359` y `C82S` salían vacíos), y **106 ganan motores y categoría de
estela**, datos que el registro no incluye. La estela clasifica el tráfico por
peso: 82 medias, 13 pesadas, 10 ligeras.

Dos decisiones que importan. **El modelo del Doc 8643 no pisa al del registro**,
solo rellena: el Doc tiene un nombre por designador y cuando el designador cubre
varias variantes le puede tocar la ejecutiva — `B38M` → "737 MAX 8 **BBJ**",
`A359` → "**Prestige** (A-350-900)", nombres equivocados para un avión de línea.
El registro sabe la variante del avión concreto; el Doc sabe la familia.

Y entre las variantes de un mismo designador **ni la primera ni la última sirve**:
para `B38M` el archivo trae "BBJ (737 MAX 8)", "737 MAX 8" y "737 MAX 8 BBJ" — la
base está en el medio. Se elige descartando las que llevan marcador VIP y tomando
la más corta de las que sobran, y así salen "737 MAX 8", "A-350-900 XWB",
"777-200". Va en `tools/aircraft_types.sqlite`, **separado del registro**, porque
`--desde` reconstruye el registro entero y una tabla ahí se perdería en silencio.

**El `aircraftDatabase-2024-04`: NO sirve.** Aporta **1 sola** matrícula
(`3935e2`, francesa, 2 mensajes) sobre las 198 que faltaban. Y confirma algo útil:
de esas 198, **165 tienen 1–2 mensajes y cero posiciones** — son ruido, no aviones.
Solo 13 tienen 10+ mensajes, y tres las cubre la inferencia por distintivo. El
hueco real son unos 5-7 aviones, no 198.

**El registro que importa es el export COMPLETO de OpenSky, no el de
`data-samples`.** Son dos datasets distintos y la diferencia es grande. Medido
sobre tráfico real de esta antena:

| tráfico | con `data-samples` | con el completo |
|---|---|---|
| ≥10 mensajes | 61% | **89%** |
| ≥50 mensajes | 60% | **90%** |
| operaciones de Aeroparque | 1 de 8 | **6 de 8** |

Los que faltaban eran los aviones matriculados hace poco —bloques `e8 06 1x–3x`
de JetSMART, `0c…` de Copa— que ningún snapshot viejo tiene. Chequeo de regresión
sobre las 331 aeronaves creíbles: **+39 ganadas, 1 perdida** (`3935e2`, francesa,
2 mensajes). Y el modelo mejora: "Airbus A321-271NX" en vez de "737NG 8SH/W".

Se importa con `python aircraft_db.py --desde <csv>`. **El archivo se baja a mano**
de <https://opensky-network.org/datasets/metadata/> porque pide sesión; no se
puede automatizar sin credenciales. Tres cosas rompían el importador viejo y están
resueltas: el encabezado viene entre **comillas simples** (sin sacarlas, todas las
columnas salen vacías y no hay un solo error que lo delate), los nombres están en
**camelCase**, y el campo `notes` **pasa los 131 072 bytes** que el módulo `csv`
permite por defecto. Y **hay que parar el servidor antes**: mantiene la base
abierta y en Windows el archivo no se puede reemplazar en uso — el error ahora
dice eso y avisa que el `.tmp` quedó listo para no reimportar.

**Interpolar la matrícula desde la dirección: PROBADA Y DESCARTADA.** No por
mala, sino porque el balance no cierra — y conviene no reintentarla.

El hallazgo es real y fuerte: en el bloque argentino `e0…` la dirección crece con
la matrícula de forma **100% monótona** (1122 subidas contra 3 bajadas sobre 1126
pares del registro), con pendiente exactamente 1,0 por tramos. Prueba de
exclusión sobre los 1128 casos conocidos: de los 661 predecibles —los que tienen
sus dos vecinos en un tramo contiguo— **659 aciertos exactos, 99,7%**. Esa
validación es de otro orden que el n=4 del distintivo.

**Por qué se descarta igual.** Recupera solo **6 matrículas de 236 faltantes**, y
`e8…` (Chile), `e4…` (Brasil) y `e1…` dan **cero** — su asignación no es
contigua. De esas 6, `e0b14e` es un **alias conocido** de `e0b14a` (ARG1650, a un
bit): la interpolación le pondría "LV-KEN" con total confianza, una matrícula real
de un avión que nunca estuvo ahí. Otras tres tienen 2–3 mensajes y son dudosas.
Quedan 1 o 2 recuperaciones sólidas, bajo el 1% de lo faltante.

El problema de fondo, que ninguna mejora del algoritmo resuelve: **predice bien la
matrícula de una dirección que existe, pero no sabe si la dirección existe.** A una
dirección corrupta le pone matrícula igual, y fabricar matrículas creíbles envenena
la confianza en las 206 que sí vienen del registro y son duras.

Si alguien la reintenta: el número que hay que mirar no es el 99,7% de acierto,
es que solo 6 direcciones caen en tramos predecibles y una de esas seis es ruido.

**Un vuelo puede aparecer bajo varias direcciones y hay que fusionarlo.** Medido:
de 190 distintivos, **18 aparecen bajo más de una dirección**, con 33 secundarias.
Ocho están a **un bit** de la fuerte (`e0b198` y `e8b198`), pero el resto a 9–15
bits, así que la distancia de bits solo explica 8 de 33. Lo que vale para todas es
el desbalance: la secundaria tiene **1–5 mensajes** y la real **130–238**. Por eso
se resuelve por dominancia (factor 3), no por bits.

**La identidad se resuelve sobre el historial completo, no sobre el cilindro.**
El distintivo viaja en el 3% de los mensajes y casi nunca cae justo dentro del
cilindro de operaciones: resolviéndola solo con lo de adentro, las 8 operaciones
de Aeroparque salían con `VUELO -` y `MATRICULA -` aunque el distintivo se
conociera perfectamente. Ahora salen JAT734, JES3104, TAM8141, SKU536…

**Más ganancia no es mejor.** Lejos cada dB alcanza un avión más lejano; pegado a
la pista el receptor satura y se pierden mensajes. Medido: con ganancia 30 desde
San Isidro la mediana cae a −33,5 dBFS y entra **una** aeronave en 70 s, contra
6-8 con 49,6.

**Pero en Aeroparque la ganancia que gana sigue siendo 49,6, y el aviso de
saturación de la pantalla apunta al lado equivocado.** Medido el 2026-09-03 con
`adsb_iq.py --medir`, que es el único método que vale porque cuenta sólo CRC
verificable:

| ganancia | verificados | tasa | aeronaves | mediana |
|---|---|---|---|---|
| **49,6** | **129** | **4,3/s** | **6** | −19,3 dBFS |
| `auto` (AGC) | 67 | 2,7/s | 5 | −8,2 dBFS |
| 30 | 34 | 1,3/s | 4 | −16,7 dBFS |
| 20 | 24 | 0,6/s | 2 | −13,4 dBFS |

O sea 3,8× más que con 30 y casi el doble que con AGC. **La hipótesis de que
pegado a la pista había que bajarla queda descartada con números para esta
ubicación** — era lo que recomendaba `USAR-LA-ANTENA.md`, y estaba mal.

En las cuatro corridas salió el cartel *"llega tan fuerte que puede estar
saturando"*, incluso con ganancia 20. Mira el **pico** (−3 a −4 dBFS en todas),
no el conteo de verificados, así que acá no discrimina nada. Está bien redactado
—pide comparar el número, no bajar a ciegas— pero su recomendación apuntaba al
revés de lo que dio la medición.

Detalle contraintuitivo que confirma que la mediana no sirve para decidir: sube
al **bajar** la ganancia (−19,3 con 49,6 contra −13,4 con 20). No es que mejore
la señal: con poca ganancia sólo sobreviven los aviones fuertes y cercanos; con
mucha entran también los lejanos y débiles, que tiran la mediana para abajo
mientras el total de decodificados sube.

**Plotly se sirve desde `/static`, no desde un CDN**, y no se usan sus modos
geográficos: necesitan tiles o topojson de internet. La costa sale de Natural
Earth y las pistas de OurAirports, horneadas en `geografia.py`.

---

## RESUELTO el 2026-09-03: hay posiciones en tierra

**Era el problema abierto más importante del proyecto y dejó de estarlo.** Desde
la **aeroplanta de YPF en Aeroparque**, en 90 minutos:

| | San Isidro (13,3 km) | Aeroplanta YPF (1,15 km) |
|---|---|---|
| altitud mínima vista | 2134 ft | **0 ft** |
| posiciones en tierra | **0**, nunca ninguna | **177** |
| aeronaves en tierra ubicadas | 0 | 10 |

Y caen donde tienen que caer. Distancia al **eje real de la pista 13/31**
(umbrales de OurAirports, vía `geografia.py`): mínima **4 m**, mediana 151 m,
máxima 379 m. Los de 4–65 m van a 20 kt y 5 kt — un avión en la pista. Los de
144–279 m van a 0–10 kt: calles de rodaje y plataforma.

La regla de superficie, que estuvo *"sin ejercitar"* durante todo el proyecto
—su contador en cero significaba "nunca corrió", no "sin rechazos"— ahora corre.

Sigue entrando con la configuración actual: 21 posiciones en tierra en los
últimos 15 minutos, con `ADSB_SOURCE=iq` y `ADSB_GAIN=49.6`.

**Lo que queda por hacer con esto.** Primero una corrección: acá decía que los
aterrizajes y despegues *no* se contaban sobre estas posiciones. Era una
suposición sin verificar y es falsa — `aeropuerto.py` ya las usa, y sobre la base
viva da 24 aterrizajes, 37 despegues y 2058 posiciones en superficie de 57
aeronaves. Lo que hay son tres huecos concretos, medidos:

- **19 de los 24 aterrizajes quedan sin confirmar**, casi todos con `pista=None` y
  `min_alt=None`, a 0,08–0,7 km del campo. La hipótesis es que son pasadas hechas
  sólo de posiciones de superficie, que por diseño del protocolo (TC 5-8) **no
  traen altitud**, así que toda la confirmación por altitud no puede dispararse.
  Pero ahora hay coordenadas con precisión de metros: la pista se podría asignar
  por geometría.
- **Una "en tierra" con `min_alt=1850 ft` y `min_dist=4,11 km`.** Un avión a 1850
  ft y 4 km no está en tierra: o es un bug de segmentación, o la categoría
  significa otra cosa que su nombre.
- **`e8062c` aparece dos veces**, como aterrizaje sin confirmar (0,3 km) y como
  "en tierra" (4,11 km). Son dos pasadas, lo cual es esperable con
  `HUECO_PASADA_S=600`, pero hay que confirmar que parta donde corresponde.

### El diagnóstico viejo, que sigue explicando por qué

**Desde San Isidro no se puede contar operaciones de Aeroparque, y el sistema lo
decía.** De 1413 posiciones grabadas: **cero** por debajo de 1500 ft y **cero** a
menos de 3 km de Aeroparque. Lo más bajo son 2134 ft sobre el campo.

No es el horizonte teórico (a 2000 ft daría 114 km) sino **obstrucción real**:
trece kilómetros de ciudad, y un avión a 1000 ft a esa distancia queda a 1,3°
sobre el horizonte, que lo tapa cualquier edificio.

Validación por contraste, mismos datos, mismo código:

| aeropuerto | distancia | ¿ve la pista? | mínima vista | resultado |
|---|---|---|---|---|
| San Fernando | 7,3 km | **sí** | **215 ft AGL** | 1 aproximación a 450 ft |
| Aeroparque | 13,3 km | no | 2134 ft AGL | 0 aterrizajes, con advertencia |

**A 1,15 km de la pista se dio vuelta**, y está confirmado arriba con las 177
posiciones en tierra. Lo que se predijo mal fue la ganancia: se recomendaba
bajarla a 20 por la saturación esperada, y medido en el lugar da 24 mensajes
verificados contra 129 con 49,6. Va `ADSB_GAIN=49.6`. Si las "aproximaciones sin
resolver" quedan altas, subir `ADSB_AIRPORT_RADIUS_KM`.

**Y lo que ninguna antena arregla:** la matrícula y el tipo no viajan por radio,
salen de cruzar el ICAO24 contra el registro de OpenSky. El 47% del tráfico real
no está en ese snapshot — Copa (`0c…`), parte de JetSmart (`e8…`), bloques
recién asignados. Se ve el **vuelo** (ARG1403) casi siempre; el **avión físico**
(LV-FVN) solo si está en el registro. Son dos problemas distintos.

---

## El glosario de columnas sale de la misma lista que las columnas

`/aeropuerto` tiene 21 columnas en tres grupos, y varias no se entienden solas:
una altitud **negativa** en la pista, una matrícula que no se transmite, una
alineación «sin confirmar» que no significa que la operación no ocurrió.

La explicación de cada una vive en el campo `ayuda` de `COLUMNAS`, junto al
título y a la celda. De ahí salen **las tres cosas**: el `title` del encabezado
(la explicación aparece donde está la duda), el panel «Qué es cada columna», y
los `colspan` de la fila de grupos. Un glosario escrito aparte se queda
describiendo columnas que ya no existen, que es peor que no tenerlo.

Los `colspan` estaban escritos a mano en `7/7/7`. Agregar una columna los
desalineaba **sin que nada fallara**, y una tabla con los grupos corridos afirma
que un número es de la operación cuando es de toda la historia de la aeronave.
Ahora se cuentan de `COLUMNAS`, y una columna sin `g` válido hace que la fila
diga *«columnas sin grupo: X»* en vez de pintarse corrida — probado en el
navegador agregando una columna huérfana.

---

## Ver los datos desde otra PC

Se graba en **disco local** (`C:\adsb-datos\`) y se **publica** una copia a una
carpeta de OneDrive. No al revés, y no es una preferencia:

**Por qué la base no puede vivir en OneDrive.** Abre en WAL, así que en todo
momento son tres archivos que valen solo juntos:

| archivo | qué tiene |
|---|---|
| `adsb_log.db` | lo ya consolidado |
| `adsb_log.db-wal` | lo recién escrito, sin integrar |
| `adsb_log.db-shm` | el índice de bloqueos entre procesos |

OneDrive no sabe que los tres son **un** objeto: sube cada uno cuando cambia,
por separado. Un `.db` sincronizado sin el `-wal` que le corresponde no queda
visiblemente incompleto — queda **corrupto**, y lo dice recién cuando alguien lo
lee. Y el `-shm` es justamente el mecanismo con que SQLite evita que dos
procesos se pisen: **no cruza la red**, así que con dos PC abriendo la misma
base las dos creen tener el candado.

**Lo que sí es seguro** es publicar un volcado *quieto*: un archivo consolidado
que nadie tiene abierto. Eso hace `publicar_datos.py`, con tres cuidados que no
son decorativos:

- **API de backup de SQLite, no copiar el archivo.** Con WAL activo hay filas
  que viven solo en el `-wal`; una copia de archivo no las ve y sale incompleta
  sin avisar.
- **Escribe a `.parcial` y recién ahí renombra.** Escribir directo deja segundos
  con el archivo a medias, y OneDrive sincroniza justo eso. El renombrado en la
  misma carpeta es atómico.
- **Cuenta las filas del destino, no del origen**, y corre `integrity_check`.
  Contar el origen y suponer que la copia salió igual es como se publica una
  base corrupta creyendo que está completa.

Quedan tres archivos en la carpeta compartida: la base, un CSV (se abre con
Excel sin instalar nada) y `estado.json` con **de cuándo son los datos y desde
dónde se midieron**. Ese manifiesto no es adorno: la PC que mira ve una *foto*,
y una foto sin fecha se lee como el estado actual. La ubicación del receptor
sale de ahí y no del preset local — si la PC que mira usara el suyo, mostraría
distancias medidas desde un lugar donde nunca hubo una antena.

| dónde | qué se ejecuta |
|---|---|
| PC de Aeroparque (graba) | `PUBLICAR-DATOS.bat` — republica cada 5 min, se deja abierto |
| La otra PC (solo mira) | `VER-DATOS-COMPARTIDOS.bat` — copia a disco local y abre el dashboard |

Verificado de punta a punta el 2026-08-24: la PC que mira lee la copia publicada
y ve **371 aeronaves, 18 950 mensajes, 161 matrículas, 30 operaciones**, midiendo
desde Aeroparque y no desde su propio valor por defecto.

**Si hace falta tiempo real** en vez de una foto, el camino es otro: la PC que
graba sirve el dashboard en la red y la otra entra por `http://<IP>:8000`. Nada
se copia, no hay nada que corromper. Requiere red común o VPN, y no está armado.

---

## Un bug ya arreglado que vale recordar

**Nueve horas midiendo desde el lugar equivocado.** El 2026-08-23 la antena ya
estaba en Aeroparque y el servidor se arrancó con el acceso directo a
`dashboard.bat`, que no fijaba ninguna variable `ADSB_*`. Midió **nueve horas
desde San Isidro**, a 13,3 km de la pista, mientras las operaciones detectadas
estaban a **0,19–1,15 km**. Nada falló: cada distancia, anillo y alcance se
publicó con confianza, referido a un lugar donde la antena ya no estaba.

Por qué se pudo dar, y qué lo cierra:

| causa | arreglo |
|---|---|
| La ubicación vivía **solo** en el entorno del proceso; ningún archivo del repo la escribía | `configuracion.bat`: **único** lugar donde se define. Lo llaman `dashboard.bat`, `MEDIR-EN-AEROPARQUE.bat`, `GRABAR-ADSB.bat` e `INSTALAR-Y-EJECUTAR.bat` |
| De un servidor ya levantado no se podía saber desde dónde medía | `GET /api/receptor` + la franja en las cinco páginas |
| `/adsb/mapa` era la única que nombraba al receptor, **y avisaba al revés**: pintaba el cartel solo si `is_default` era `false`, o sea que se callaba justo en el modo de falla | Reemplazado por la franja compartida. La versión **por defecto es la ruidosa** (roja): si nadie eligió, eso es lo que hay que gritar |
| Dos copias de `set ADSB_RECEIVER` se habrían desincronizado | Una sola definición, llamada con `call` |

La lección general: **avisar no es arreglar**. La franja hace visible el problema,
pero el arranque además tiene que ser correcto solo — si depende de que alguien se
acuerde de una variable, algún día no se va a acordar.

**No ordenar una costa por latitud.** Parece inofensivo y la destruye: una costa
no es monótona en latitud (bahías, el delta, la vuelta de Punta del Este), así que
ordenar hace que el trazo salte de un lado al otro. Medido: la orilla uruguaya
pasaba de 394 km a **1346 km** de largo, 3,4×, y en el mapa se veía como rayas
horizontales cruzando el río. `geografia.py` conserva el orden del trazo de
Natural Earth, y las dos orillas concatenadas **tal cual** ya cierran el anillo
—vienen en sentidos opuestos— así que invertir una lo cruza en diagonal.

**No afirmar en presente sobre "esta ubicación" con datos de otra.** Encontrado
el día de la mudanza a Aeroparque: `/aeropuerto/mapa` se contradecía en dos
líneas contiguas. Arriba, *"la pista está a 2,2 km… los aviones en la pista sí
se escuchan desde acá"* —geometría de **ahora**—; abajo, *"la fase final no se
recibe desde esta ubicación"* —mínimo de 2134 ft grabado **antes**, desde San
Isidro a 13,3 km—. La segunda le decía a quien acababa de mudar la antena que
la mudanza había fracasado, antes de grabar un solo mensaje.

La causa de fondo sigue abierta: **la base no guarda desde dónde se recibió cada
fila**, así que las distancias de todo el histórico se recalculan desde el
receptor actual. Medido entre San Isidro y Aeroparque, sobre las mismas 1985
posiciones: la mediana se corre 6% (17,6 → 18,7 km) y el máximo 1%. Es chico
porque los dos puntos están a 20 km y el tráfico está mucho más lejos, pero
crece si algún día la antena se muda lejos.

El arreglo aplicado es acotado: `advertencia` en `aeropuerto.py` sólo culpa a la
ubicación cuando la geometría la acusa (`ve_la_pista` falso). Con la pista
dentro del horizonte dice que el mínimo describe lo ya grabado y que hace falta
grabar de nuevo. Vale para las dos frases: la del cilindro vacío tenía el mismo
defecto y podía mandar a revisar la antena por lo que era falta de datos.

**El dongle tomado por otro proceso se veía como "grabando".** El peor modo de
falla que tuvo el sistema, y salió al probar el arranque de la mudanza. Si algo
ya tiene el dongle —otra pestaña, otro servidor, un `rtl_sdr.exe` colgado—,
`rtl_sdr` imprime `usb_open error -3 / Failed to open rtlsdr device #0`, su
stdout cierra al instante y el generador de `escuchar()` terminaba **normal**:
como un stream que se acabó, no como un fallo. El error quedaba sólo en la
consola del servidor. Resultado medido: `/adsb` mostró el punto verde con
`running: true` y `error: null` durante **65 s sin un solo mensaje**.

Tres cosas tenían que cambiar, y las tres eran necesarias:

1. `escuchar()` (`adsb_iq.py`) ahora **lanza** si el subproceso murió sin
   entregar una sola muestra, con un mensaje que nombra las dos causas reales
   en orden de probabilidad: otro proceso tiene el dongle (el dongle es
   exclusivo), o falta el driver WinUSB de Zadig.
2. `status()` publica `start_error` **o** `source.last_error`. Sólo la primera
   dejaba invisible este caso, porque `start()` ya había devuelto `running:
   true` y la falla ocurre después, en el hilo lector.
3. En `adsb.html`, la rama de `error` va **antes** que la de `running`. Con el
   orden viejo quedaba muerta justo en el caso peligroso, que es
   `running: true` **con** error. Ahora el encabezado dice *"la grabación está
   activa pero NO entra nada"*.

Un tablero que dice que graba mientras no recibe nada es peor que uno que se
cae: nadie va a ir a mirar.

## Cosas que van a confundir si nadie las avisó

**La base ya NO commitea cada 50 filas: commitea cada 1,0 s de reloj.**
Resuelto, con el número. El commit por cantidad no tenía cota temporal: medido
en vivo, había 44 filas escritas e invisibles y la más vieja llevaba 878 s
esperando (14,6 min). Re-medido sobre las 11 823 filas de `adsb_log.db`
reconstruyendo los lotes de 50 por `id` (236 lotes completos): cada fila
esperaba mediana **38,7 s**, p90 **186,0 s**, p99 **410,5 s** y máximo
**759,5 s (12,7 min)**, excluyendo los 18 lotes que cruzan un corte de grabación
(hueco > 300 s). **Corrección de un número que estaba mal en este archivo:** el
"máximo 14 903 s (4 h 8 min)" que decía antes reproduce aritméticamente pero su
premisa es falsa — sale entero del hueco de 14 742,9 s entre `id=3523`
(2026-08-22T13:59:12Z) e `id=3524` (18:04:55Z), cuatro horas con CERO filas, o
sea el grabador apagado. `Recorder.close()` commitea al cerrar, así que ninguna
fila esperó 4 h. Contando también esos lotes da mediana 40,6 s, p90 223,0 s,
p99 2 114,9 s. **Y el otro número corregido:** decía "a las 22:00, con 0,27
filas/min, un lote de 50 tarda 11 250 s" — a las 22:00 local (01:00 UTC) hay 527
filas = **8,78 filas/min**, o sea 342 s por lote, no 11 250. Estaba inflado 33×
y con la unidad cambiada (los 0,27 son *posiciones por ventana de 5 s*, otra
cosa). El argumento de fondo sigue en pie con números reales: la hora más floja
con el grabador claramente encendido (2026-08-23T02 UTC = 23:00 local, 340
filas, hueco interno máximo 258 s) da 5,67 filas/min y un lote de 50 tarda
529 s. Ahora el retraso está acotado en 1 s pase lo que pase. Cuesta, al pico real medido de
3,8 filas/s, a lo sumo 1 commit/s = 2,46 ms/s (0,45 ms/s en WAL) = 0,25 % de un
núcleo.

**La base abre en WAL, y el modo REAL se publica.** WAL no baja el retraso por
sí solo (medido: wal + commit cada 50 da mediana 0,26 s de retraso visible
contra 0,25 s de delete + commit cada 50); lo que hace es abaratar el commit
5,5× y borrar los picos de 117,8 ms en que el lector quedaba bloqueado (1,04 ms
en WAL). Es el habilitador del commit por tiempo, no un sustituto. El PRAGMA
puede fallar en silencio —SQLite no cambia el modo si otra conexión tiene la
base y devuelve el modo en que quedó— así que se chequea el valor devuelto y
`/api/adsb/status` publica `journal_mode` real, junto con `pending` y
`seconds_since_commit`. En `.gitignore` se agregaron `*.db-wal` y `*.db-shm`:
`adsb_log.db` está **trackeada**, y en WAL el `.db` se queda atrás del `-wal`
hasta el checkpoint, así que versionar la base mientras graba sube un archivo al
que le falta la cola. `Recorder.close()` hace `wal_checkpoint(TRUNCATE)`.

**Ojo: el cambio entra en el PRÓXIMO arranque del grabador.** Una grabación ya
en curso sigue con el commit cada 50 filas, y los dos mapas van a mostrar un
`lag_s` de minutos que es real y correcto, no un fallo de la mejora.

**El nuevo techo de latencia es el `time.sleep(1.0)` de `AdsbService._loop`**
(adsb_service.py:120), que mira `snapshot()` una vez por segundo. Arreglar el
commit no lo toca. Si alguien mide 1 s de retraso residual y lo va a buscar a
`adsb_record.py`, no lo va a encontrar.

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
| `identidad.py` | todo lo que se puede saber de cada vuelo, con su procedencia |
| `tipos_avion.py` | tipo de avión por el Doc 8643 de la OACI: modelo, motores, estela |
| `test_adsb_position.py` | 8 escenarios de posición y CRC |
| `test_adsb_incremental.py` | el cursor no pierde ni repite filas, y da igual que `load_db` |
| `webapp/bajar_plotly.py` | baja Plotly una vez |
| `webapp/static/franja_receptor.js` | la franja de «desde dónde se mide», igual en las cinco páginas |
| `configuracion.bat` | **único** lugar donde se define dónde está la antena y dónde viven los datos |
| `publicar_datos.py` | publica un volcado quieto (base + CSV + manifiesto) a la carpeta compartida |
| `mostrar_publicado.py` | lee el manifiesto: de cuándo son los datos y desde dónde se midieron |

Los cuatro archivos de test pasan: `test_adsb.py`, `test_adsb_events.py`,
`test_adsb_position.py`, `test_adsb_incremental.py`.

---

## Los mapas leen por cursor, no releen la base entera

Los dos mapas (`/adsb/mapa` y `/aeropuerto/mapa`) pasaron de "redibujar todo
cada N segundos" a "avanzar un cursor y parchear". **La cadencia es 5 s en los
dos** (antes 10 s y 20 s): el número sale del piso del pipeline, no de que suene
rápido — 1 s del `time.sleep(1.0)` de `AdsbService._loop` + 1 s del commit por
tiempo + hasta 5 s de espera del poll = 7 s de peor caso extremo a extremo. Dos
cadencias distintas sobre la misma base solo generaban la pregunta de cuál de
las dos pantallas estaba bien.

**El contrato del endpoint.** `GET /api/adsb/mapa` y `GET /api/aeropuerto/mapa`
aceptan `?desde=<id>` y `?ventana_h=<horas>` (`0` = la grabación entera).

- Sin `desde`: `base:"completa"`, con `receiver` + `geo` + `airports`,
  `estatico_v` (hash corto de los estáticos) y `reconstruido_en_ms`.
- Con `desde`: `base:"delta"`, con eco de `desde`, `cursor`, `nuevos`,
  `nuevas_posiciones`, solo las aeronaves tocadas y solo sus puntos nuevos,
  `rejected_nuevos`, y `coverage` **solo si cambió** (cambia exactamente cuando
  entró al menos una fila, así que la condición es exacta y no una heurística).
- **El servidor impone completa** si `desde` es mayor que su cursor —se
  reinició— y lo dice en `forzada`. Un solo endpoint con parámetro y no dos,
  porque el único que sabe si se reinició es el servidor: con dos endpoints el
  cliente adivina, y adivinar mal deja una pantalla desincronizada que nadie
  nota.
- `lag_s` viaja SIEMPRE, en los dos modos. Es lo único que impide que la página
  diga "en vivo" sobre una base atrasada; el cartel sale del número, no de que
  el fetch haya respondido.
- `ventana_nota` viaja SIEMPRE (normalmente `null`). Dice que el `ventana_h`
  pedido no era válido y con cuál se dibujó. Antes `ventana_h=1e400` llegaba
  como `inf` y reventaba con **HTTP 500** al serializar ("Out of range float
  values are not JSON compliant"), y `nan` o `-5` caían por `ventana_s > 0` =
  False y devolvían la grabación ENTERA sin avisar. Ahora los tres contestan 200
  con la nota, que es lo que ya hacía `forzada` para un cursor inválido.
- `rebobinados` (contador, cero explícito) y `rebobinado` (el detalle de la
  última, o `null`) viajan SIEMPRE. Ver la sección de abajo.
- `trazas_fuera_de_ventana` y `puntos_fuera_de_ventana` viajan siempre, con el
  cero explícito, y la página los escribe con un botón para pedir todo. Ninguna
  ventana es gratis. Los agregados (observations, mediana, p95, máximo) siguen
  siendo de TODA la grabación y eso también se dice.
- Los estáticos (receptor 226 B + costa/pistas 5 476 B + aeropuertos 299 B) van
  una vez por carga de página, no 360 veces por hora. En el delta viaja solo su
  hash: si cambia, el cliente pide completa, así se sigue delatando un
  `ADSB_SURFACE_REF` movido a mitad de corrida.

**El cursor es la columna `id`, y no `epoch` ni `utc`.** `epoch` NO es único
(10 275 valores distintos sobre 10 873 filas, 598 duplicados consecutivos): con
`>` pierde filas, con `>=` las repite, y una posición perdida deja un agujero en
la traza que nadie nota mirando. `utc` no tiene índice (SCAN de tabla entera:
0,46 ms → 39,7 ms al crecer 40×, contra 0,055 → 0,061 ms de `id`). Y `id` es
`INTEGER PRIMARY KEY AUTOINCREMENT`: monótono aunque el reloj salte hacia atrás.

**`LectorIncremental` (adsb_events.py)** es una instancia por proceso con lock
que posee el `PositionGate`, el cursor, las trazas y los agregados, y solo
avanza. NO guarda `Observation` (313 B/obs = 143 MB a 30 días): los puntos van
en `array('d')` de 6 doubles —id, t, lat, lon, alt, km— a 61 B/punto. Mediana y
p95 salen de un `array('d')` ordenado con `bisect.insort`: **exactas**, no
aproximadas — un histograma de 0,1 km ahorraría memoria pero su error de
0,047 km movería el dígito que la página imprime con `toFixed(1)`.
`load_db()` mantiene su firma y sigue leyendo la base entera: sus 8 llamadores
—el CLI, `/adsb/analisis`, `aeropuerto.py`, los tests— desempaquetan una tupla
de 2 y necesitan la lista completa. El kwarg `desde=` está en `_read_db`, que
ahora devuelve `(observaciones, cursor)`.

**`aeropuerto.informe()` se partió en dos** —`resumir_cilindro()` acumula,
`informe_desde_resumen()` clasifica— y las dos rutas llaman al MISMO
`_clasificar`, para que no existan dos implementaciones de los conteos
publicados (aterrizajes, despegues, frustradas). El informe se cachea **por
cursor** y no por tiempo: por cursor es exacto (es función pura del conjunto de
observaciones) y acierta igual el 78 % de las veces, que es el porcentaje de
refrescos que no trae ni una posición.

### Medido, antes y después (11 423 filas, 1 832 posiciones, 59 aeronaves)

| | antes | después |
|---|---|---|
| `/api/adsb/mapa` refresco | 84–185 ms · 208 028 B | **3,8–6,0 ms · 291 B** |
| `/api/aeropuerto/mapa` refresco | 76–79 ms · 59 400 B | **3,2–3,7 ms · 2 811 B** |
| carga completa (ventana 3 h) | — | 6,1 ms · 77 181 B |
| carga completa (`ventana_h=0`) | — | 10,5 ms · 212 821 B |
| reconstrucción tras reiniciar | 153 ms (cada refresco) | 88,8 ms (una vez) |

El delta del aeropuerto es más grande que el del mapa general a propósito: lleva
el informe entero en cada respuesta porque **la reclasificación no es opcional**
—`_clasificar` mira `sube = alturas[-1] - alturas[i_min]`, así que UN punto
nuevo convierte un sobrevuelo en aterrizaje— y el cambio se anuncia en pantalla,
porque una traza que cambia de color sola se lee como que la página se
contradice.

## El zoom sobrevive al refresco (verificado en el navegador)

`Plotly.react` en vez de `newPlot`, `uirevision` constante entre refrescos, `uid`
estable en cada traza (`"ac-"+icao24` y uno fijo por traza de fondo),
`datarevision` derivada del dato (puntos + timestamp máximo) y el encuadre
calculado UNA vez y guardado en `ENCUADRE`. `uirevision` solo cambia cuando el
usuario pide otro encuadre ("Ver todo" / "Volver al grueso" / la grabación
entera): si no cambiara nunca, esos botones no harían nada.

Por qué cada pieza:

- **`newPlot` + `uirevision` no alcanza**: `newPlot` arranca con `Plots.purge` y
  se lleva `_fullLayout` entero, incluido `_preGUI`, que es donde vive lo que
  tocó el usuario. Comprobado en el navegador: con el mismo dibujo y el mismo
  encuadre, `react` conserva el zoom, `newPlot` lo devuelve al original y
  `_preGUI` queda vacío.
- **El rango tiene que ser pegajoso**: con `uirevision` puesto pero el rango
  recalculado, el zoom no se pierde limpio, se MEZCLA — `_preGUI` compara
  `range[0]` y `range[1]` por separado, así que el usuario queda con un extremo
  suyo y otro nuestro. No se lee como un bug, se lee como que el mapa se movió
  solo.
- **`uid` en todas las trazas**: `adsb_report.tracks()` ordena por cantidad de
  puntos y 38 de 55 pares vecinos están a UNA posición de darse vuelta. Sin uid,
  Plotly resuelve la identidad por índice. Por lo mismo NO se usan
  `extendTraces`/`addTraces`: direccionan por índice y le pegarían los puntos de
  una aeronave a la traza de otra, en silencio.
- **`datarevision` derivada del dato y no un contador a mano**: con
  `datarevision` igual y datos distintos, `react` no redibuja y NO avisa.
- **`Plotly.purge` antes de cualquier `innerHTML` sobre `#mapa`**: antes andaba
  de casualidad porque después venía un `newPlot`. Con `react` el div queda
  vacío y no tira excepción — y es el ciclo real de un mapa en vivo, que arranca
  sin posiciones.

Verificado en el navegador contra la instancia del 8001, con un `WheelEvent`
real sobre la capa de arrastre: ancho del eje X **136,743 km → 123,730 km** al
hacer zoom, y **123,730 km después de 6 refrescos** (delta, completa y uno con
datos nuevos de verdad), con el rango byte a byte idéntico en los seis. En
`/aeropuerto/mapa`, 26,637 → 24,102 km y 24,102 tras 4 refrescos.

**Corrección de lo que decía este archivo:** antes acá se afirmaba que "el eje Y
no se mueve ni un decimal". Es falso, y solo parecía cierto porque el zoom con
la rueda mueve los dos ejes de forma simétrica. Con un **paneo vertical** el eje
Y SÍ se perdía en el refresco siguiente: medido con un arrastre real, Y quedaba
en `[-13,487, 154,714]` y el refresco lo devolvía a `[-95,138, 73,063]` mientras
el X sobrevivía intacto. La causa es `yaxis.scaleanchor: "x"`: el eje atado no
tiene rango propio, Plotly lo **rehace** en cada `react` a partir del X, del
aspecto en píxeles y del **centro que le mandamos** — o sea del `ENCUADRE`
original, que la edición del usuario no llegaba a pisar. Aislado: pasa llamando
solo a `dibujar()`, sin nada del resto del ciclo.

El arreglo es de una línea de idea: **el encuadre que se remanda es el que el
usuario TIENE, no el que había al principio.** Antes de cada dibujo se relee
`gd._fullLayout.xaxis.range` / `yaxis.range` y eso pasa a ser `ENCUADRE`. Así no
hay nada que "restaurar" y el eje atado deja de tener a dónde volver. `ENCUADRE`
se recalcula desde el dato solo cuando vale `null`, que es el primer dibujo y lo
que hacen los botones de reencuadre. Verificado en las dos páginas con un paneo
diagonal + rueda: rango idéntico byte a byte tras un delta y tras una completa,
y los botones "Ver todo" / "Ver la grabación entera" siguen descartando el zoom
a pedido explícito.

**Se eliminó la pausa por hover** de `/adsb/mapa`. El comentario que la
justificaba era cierto para `newPlot` y falso para `react`: verificado, el nodo
`path.js-line` bajo el cursor es el MISMO objeto después del refresco. La pausa
era lo contrario del tiempo real — congelaba el mapa exactamente cuando alguien
lo estaba mirando.

**Las dos listas laterales se parchean nodo por nodo** en vez de rehacerse con
`innerHTML`, con los eventos delegados en el `<ul>` y el resaltado en una
variable de módulo. El `innerHTML` destruía el `<li>` con el mouse encima, su
`mouseleave` nunca llegaba y las trazas quedaban apagadas para siempre (53 de 54
medidas). Verificado: con el mouse sobre un `<li>`, 18 trazas apagadas; tras un
refresco completo el nodo es el mismo y al salir el mouse quedan 0 apagadas. Las
dos listas ordenan por **última posición más reciente** y no por cantidad de
puntos, que es el orden que se da vuelta solo.

---

## Lo que encontró la verificación adversarial del cursor, y cómo quedó

Todo esto salió de revisar el cambio del cursor incremental **contra el código y
contra la base real**, con la grabación detenida hacía 11,9 h. Ese estado —toda
la grabación más vieja que la ventana de dibujo de 3 h— es el que destapó casi
todo: es el caso que nadie prueba porque hay que esperar tres horas para
llegar a él.

### 1. Las dos páginas mentían cuando la ventana de 3 h dejaba todo afuera

`/adsb/mapa` imprimía **"Todavía no hay ninguna posición decodificada"** con
1 984 posiciones decodificadas en la misma respuesta, y daba una **causa física
inventada** ("faltan las tramas CPR par e impar") para lo que era un recorte por
antigüedad. Tres centímetros más abajo la misma pantalla decía "quedaron fuera
66 trazas y 1 984 puntos", y las tarjetas publicaban "17,6 km alcance típico"
calculado justo con esas 1 984.

`/aeropuerto/mapa` era peor porque **no había forma de enterarse**: decía
"Ninguna aeronave entró al cilindro… es que desde donde está la antena no se
reciben" mientras las tarjetas mostraban 7 SOBREVUELOS y el JSON traía
`aeronaves_en_cilindro=15`, `posiciones_en_cilindro=54`, `salidas=8`. Los
`trazas_fuera_de_ventana=8` y `puntos_fuera_de_ventana=506` viajaban en la
respuesta pero el **único** lugar que los escribía estaba en `pintarLista()`
después de su `return` temprano, y esa página **nunca mandaba `ventana_h`** ni
tenía el botón "Ver la grabación entera".

Cómo quedó:

- El mapa vacío distingue las causas y las dice. En `/adsb/mapa`: "nunca se
  decodificó una posición", "se decodificaron y el filtro las rechazó a todas"
  (con el desglose por motivo) y "todas quedaron fuera de la ventana de N h".
  En `/aeropuerto/mapa`: "quedaron fuera de la ventana", "entraron al cilindro
  pero ninguna quedó con operación atribuida" y "no entró ninguna" — y esta
  última afirma la causa de la antena **solo si `ve_la_pista` es falso**; con la
  pista dentro del horizonte, decir "desde acá no se reciben" era inventar.
- `/aeropuerto/mapa` ahora manda `ventana_h`, tiene el cartel `#ventana` que se
  escribe SIEMPRE (con el cero explícito) y el botón "Ver la grabación entera".
- La tarjeta "Aeronaves ubicadas" y la nota de la lista de `/adsb/mapa` pasaron a
  contar **toda la grabación**, como las dos tarjetas de al lado; lo dibujado se
  dice aparte ("1 986 posiciones · 2 dentro de la ventana"). Antes la tarjeta
  marcaba 0 al lado de "17,6 km ALCANCE TÍPICO", y la nota decía "1 de 3 290
  aeronaves tienen posición… las otras 3 289 no llegaron las tramas CPR",
  atribuyéndole a la radio 66 trazas que sí estaban decodificadas.
- La barra de estado de `/aeropuerto/mapa` decía "0 aeronaves en el cilindro"
  con 15 adentro: ahora imprime el número del cilindro y, aparte, cuántas se
  dibujan.
- **Faltaba una tarjeta**: `salidas` no se contaba en ninguna, y son justo las
  que este mapa DIBUJA (8 en la grabación de hoy). Ocho trazas de colores sin un
  número que les correspondiera.

### 2. El lector no detectaba que la base RETROCEDIÓ

`avanzar()` solo hacía `WHERE id > cursor` y nunca miraba el estado real de la
tabla. Si `adsb_log.db` se borra y se vuelve a crear con el proceso web vivo
—que es exactamente lo que pasa si alguien resetea la grabación y después
aprieta Iniciar, porque `adsb_service` graba desde ESTE mismo proceso— el lector
se quedaba con el cursor viejo y **servía datos borrados para siempre**.
Reproducido: con el cursor en 8 000 y la base recreada con 300 filas, tres polls
seguidos daban `nuevos=0`, y hasta la carga completa contestaba 35 trazas, 777
puntos y `observations=8000` sobre una base de 300 filas. El único síntoma era
`lag_s` creciendo, o sea la página diciendo "grabación detenida" justo mientras
la grabación corría. Y no había forma de reconciliar sin reiniciar el servidor.

Ahora `_read_db_con_ids()` devuelve además `(max(id), count(*))` de la tabla
entera y `_detectar_rebobinado()` corre dos pruebas **de un solo lado**:
`max_id < cursor` (archivo recreado o truncado) y `total < observaciones +
leídas` (DELETE en el medio, que no mueve `max(id)`). Son de un solo lado a
propósito: los dos números se leen DESPUÉS del `SELECT`, así que una inserción
concurrente del grabador solo los agranda y no puede disparar un falso positivo.
Al detectarlo, el lector se **reconstruye entero** (`_reset_estado()`, un método
y no código repetido: olvidarse un contador dejaría un lector mezclando dos
bases, que es peor que el bug) y el endpoint fuerza `base:"completa"` con el
motivo en `forzada`. Se publica `rebobinados` y `rebobinado`, y las dos páginas
lo escriben en la barra de estado: un lector que se reconstruyó solo cambió de
golpe todos los números de la pantalla.

Costo medido: el refresco vacío —el 78 % de los refrescos— pasa de **0,319 ms a
0,774 ms** sobre las 11 823 filas de hoy. Las dos agregaciones van en sentencias
SEPARADAS porque juntas obligan a un scan de la tabla completa: sobre 1 000 000
de filas, juntas 33,9 ms, separadas 0,039 ms el `max` (por índice) + 7,2 ms el
`count`. Los 7,2 ms cada 5 s son 0,14 % de un núcleo y se pagan porque el
`count` es lo único que detecta un DELETE en el medio.

### 3. `ADSB_DB`: el sistema ya se puede ejercitar sin tocar el dato del usuario

La ruta de la base estaba escrita a mano en seis lugares (`adsb_record.py:40` y
cinco `ROOT / "adsb_log.db"` en `webapp/main.py`) y no había variable de
entorno, a diferencia de `ADSB_RECEIVER`, `ADSB_SURFACE_REF` o `ADSB_AIRPORT`.
Consecuencia concreta: la única forma de comprobar de punta a punta que al mapa
le entran filas nuevas era **escribir en las 11 823 filas irrepetibles**, o
duplicar el árbol entero.

Ahora `adsb_record.py` define `DB_PATH = Path(os.environ.get("ADSB_DB") or …)` y
`webapp/main.py` la **importa** en vez de rearmarla — dos definiciones de la
misma ruta es como se termina con la webapp leyendo un archivo y el grabador
escribiendo otro. Verificado de punta a punta contra una copia: `ADSB_DB=<copia>
uvicorn main:app --port 8002`, carga completa con cursor 11 823, se insertan dos
filas con `epoch` de ahora en la copia, y `?desde=11823` devuelve `base=delta`,
`nuevos=2`, `nuevas_posiciones=2`, `cursor=11825`, `lag_s=0.0`, con la traza
nueva y sola. Eso —que llegan filas nuevas por el camino HTTP completo— no se
había podido verificar nunca.

### 4. El contador de reintento decía siempre "0 s"

`pintarEstado()` se llama desde el `catch` de `cargar()`, o sea **antes** de que
`programar()` actualice `proximoEn`; con el valor viejo ya vencido, `faltan`
daba 0 y el texto no se repintaba hasta el intento siguiente. Durante esperas
reales de 20, 40 y 60 s la página decía "reintento en 0 s" fijo — el único
número que ese cartel tiene que acertar. Ahora la espera se calcula en un solo
lugar (`esperaMs()`, que usan `programar()` y el `catch`), `proximoEn` se fija en
el `catch`, y el cartel se repinta cada segundo. Verificado: 4 s → 2 s → 0 s con
un fallo, 8 s → 6 s → 4 s con dos. Y `/aeropuerto/mapa` no agregaba la clase
`error` ni tenía `.estado.error` en su CSS: una caída total del servidor se veía
con el mismo gris que "todo bien". Las dos cosas están.

---

## Ideas que quedaron sin hacer

- **Guardar en cada fila desde dónde se recibió.** Hoy la base no lo guarda, así
  que las distancias de todo el histórico se recalculan desde el receptor
  actual: al mudarse, 11 823 filas grabadas en San Isidro pasan a medirse desde
  Aeroparque. Medido, el corrimiento es chico (mediana 6%, máximo 1%) porque los
  puntos están a 20 km, pero es un error silencioso y crece con la distancia.
  Sería una columna `rx_lat`/`rx_lon` escrita por el grabador; lo viejo queda en
  NULL y se puede asumir San Isidro. Sin esto, cualquier análisis que mezcle las
  dos ubicaciones compara contra un origen que para la mitad de las filas es
  falso. Es lo que hizo aparecer el bug de la advertencia contradictoria.

- **Identificar la aerolínea por el prefijo del distintivo** (JES = JetSmart,
  ARG = Aerolíneas, GLO = Gol). Daría el operador del 100% de los aviones con
  callsign, sin depender del registro. Es el mejor camino para el 47% que no
  resuelve matrícula.

  **Ya está dimensionado con datos** (2026-08-22, sobre las 171 aeronaves con
  posición o distintivo, que es el criterio de "real"):

  | | aeronaves | |
  |---|---|---|
  | resuelven matrícula contra OpenSky | 66 | 39% |
  | transmiten distintivo | 164 | **96%** |
  | distintivo **pero sin** matrícula | 101 | 59% ← lo que rescata el prefijo |
  | ni una cosa ni la otra | 4 | 2% |

  De esas 101, **90 (89%) traen un prefijo `AAA###` válido**. O sea que el prefijo
  llevaría la identificación del operador del 39% al 92%.

  Dos obstáculos encontrados al mirar la fuente, que hay que resolver antes:
  **(1)** el snapshot de OpenSky tiene `operator_icao` para 1705 operadores, pero
  **`ARG` está en blanco** — 56 aviones cargados y ningún nombre, justo el prefijo
  más frecuente del aire local (77 aeronaves observadas). Sale gratis para TAM,
  LAN, GLO, AZU, KLM, CMP, AVA, AAL, JES y SKU, pero no para el que más importa.
  **(2)** los nombres tienen mojibake: `Gol Transportes A�reos`, `Aerov�as` — el
  CSV se decodificó con el encoding equivocado al construir `tools/aircraft_db.sqlite`.

  Y una distinción que no hay que perder al implementarlo: el distintivo identifica
  **el vuelo y su operador**, no el avión físico. Presentar una aerolínea deducida
  del prefijo como si fuera la identidad del airframe sería mezclar dos
  afirmaciones con confiabilidad distinta.
- **Corrección de fase de dump1090**, su otro mecanismo de recuperación. No se
  hizo porque ya hay paridad de tasa con `rtl_adsb`.
- **Leer el `rssi` del `aircraft.json` de dump1090** — tres líneas en `adsb.py`,
  solo útil si se usa dump1090.
- ~~**Bajar el intervalo de commit o pasar a WAL**~~ — hecho. Commit cada 1,0 s
  de reloj y `journal_mode=wal` con el valor devuelto chequeado. Ver "Cosas que
  van a confundir".
- **Persistir un snapshot del estado del `LectorIncremental`.** Cuando el
  proceso del servidor se reinicia, el cursor vuelve a 0 y el primer request
  reconstruye toda la grabación: 88,8 ms medidos sobre 11 423 filas, pero crece
  linealmente (40× filas = 40,4× tiempo), o sea ~3,6 s a 29 días. La respuesta
  completa ya lleva `reconstruido_en_ms` a la vista en vez de disimularlo; el
  snapshot es el arreglo de fondo. La ventana de retención NO ayuda acá: el gate
  y los agregados son históricos por definición.
- **Acotar el `array('d')` de distancias.** Es el único componente del estado
  residente que la ventana de retención no acota: 13,4 MB al año, y la inserción
  con `bisect.insort` pasa de 0,50 µs a 612 µs por punto en ese horizonte (2,0
  ms de CPU por minuto a la tasa actual, aceptable; a varios años no).
- **Medir cuánto permanece una aeronave en alcance**, que es lo que debería
  fijar la ventana de dibujo de 3 h. Hoy las 3 h son un número elegido para que
  la carga completa quede acotada (77 KB en vez de crecer sin techo), no una
  medición. Va con `trazas_fuera_de_ventana` a la vista y un botón que pide la
  grabación entera, así que es reversible sin tocar código.
- **Los cinco pedazos de estado de interfaz de los mapas viven en tres lugares**
  —Plotly vía `uirevision`, variables de módulo, y el DOM parcheado—. Si el
  próximo cambio agrega un sexto y lo pone en el DOM sin pensarlo, vuelve el bug
  del resaltado con otra cara.

---

## Sesión 2026-08-24: la torre de YPF no recibe, y por qué

**Resultado: cero ADS-B desde el piso 13 de la torre de YPF.** No es configuración
ni ganancia: no llega señal de 1090 MHz. La antena estaba a **6 m de la ventana**.

Medido, 40 s por punto, con el único criterio que el ruido no puede falsificar:

| ganancia | DF17/18 con **CRC válido** | otras tramas |
|---|---|---|
| 49,6 | **0** | 334 |
| AGC (`-g 0`) | **0** | 73 604 |

**Diagnóstico: el vidrio.** El piso de ruido daba −36 dBFS, o sea que la antena
está conectada y entra energía de radio — falta específicamente 1090 MHz. El
vidrio con capa metálica de una torre moderna atenúa 20–40 dB, y desde San Isidro
el margen sobre el piso de ruido era de 16–20 dB: el vidrio se lo come entero y
sobra. **Antes de volver: la antena tiene que estar pegada al vidrio o afuera**, y
la ventana tiene que mirar al noreste, donde queda Aeroparque desde Puerto Madero.

### La trampa que costó una hora: el conteo de mensajes no mide señal

Con AGC el dashboard reportaba **34 684 mensajes y 101 "aeronaves"**, todas con
exactamente 2 mensajes, sin distintivo, y una con altitud de **110 500 ft**. Era
ruido al 100%. Mirar el conteo total manda a buscar el problema en la ganancia
cuando el problema es que no hay señal.

Solo cuenta el **CRC verificable de DF17/18**: 24 bits contra un síndrome
conocido, 1 en 16,7 millones de que una trama de ruido pase. Los formatos cortos
(DF0/4/5/11/16/20/21) llevan la paridad XOR-eada con la dirección del avión, no se
validan solos, y el ruido los produce a montones.

**Herramienta nueva: `python adsb_iq.py --medir 30`.** Contesta sí o no en 30
segundos contando las dos poblaciones separadas, y si no hay señal lista qué
probar. Es para usar parado al lado de la antena. Requiere parar la grabación
(el dongle es exclusivo).

### La compuerta de repetición se rompe a tasa alta de mensajes

Está calibrada sobre 3777 tramas no verificables, donde el azar predice **0,43**
direcciones repetidas y se observaron 133 (313× sobre el azar). Pero con AGC
llegan ~1576 tramas/s: en un minuto son ~94 000, y el azar predice **~263**
colisiones. A esa tasa la regla de "dos apariciones" deja de discriminar, y de ahí
salieron las 101 aeronaves fantasma. **Pendiente:** el umbral debería depender de
la tasa de tramas, no ser fijo.

### `ADSB_GAIN=auto` funcionaba por accidente

La cadena se pasaba cruda a `-g auto`; `rtl_sdr` le hacía `atof`, daba 0, y 0 es
AGC. Andaba apoyado en cómo falla un parseo. Ya está explícito (`auto`/`agc`/`""`
→ `"0"`).

### Dos correcciones a lo que decía este archivo

**"Ganancia 20, no auto"** salió de medir saturación desde San Isidro. En la torre
20,7 dio **cero** igual que 49,6: sin señal la ganancia es irrelevante y esa
recomendación no se sostiene. La regla sigue siendo medir con `--medir`, no elegir
de memoria.

**Durante la medición de Aeroparque el servidor corrió como San Isidro.** Arrancó
sin `ADSB_RECEIVER` (el acceso directo del Escritorio apunta a `dashboard.bat`,
que no fija ninguna variable). Los datos están bien —las posiciones son
absolutas— pero todo lo relativo al receptor salía 13 km corrido, y el
diagnóstico decía *"los aviones en la pista no se escuchan"* con 23 operaciones a
menos de 1 km en la misma pantalla.

### La medición de Aeroparque está respaldada

`respaldos/aeroparque-2026-08-23-medicion.db` — **18 950 filas, 4782 con
posición**. Hecho con `VACUUM INTO` y no con un `cp`: la base abre en **WAL**, y
copiar solo el `.db` puede dejar afuera lo que vive en el `-wal` y perderlo en
silencio. Verificado que el conteo coincida. `respaldos/` está en `.gitignore`:
son datos, y esa medición es irrepetible sin volver con la antena.

Leída con la configuración correcta (`ADSB_RECEIVER=aeroparque`): **14
aterrizajes, 11 despegues**, 1 motor y al aire, 5 sobrevuelos, `ve_la_pista=True`,
2,2 km a la referencia, altitud mínima **−366 ft sobre el campo**.

### Dos cosas del CSV que parecen bugs y no lo son

**`registration` y `callsign` vacíos.** La matrícula **no viaja por radio**: el
avión transmite su ICAO24. La columna existe pero el camino del dongle la deja
vacía siempre, y la matrícula se resuelve al *leer*, cruzando contra el registro.
Es deliberado: si se hubiera congelado en las filas de ayer, seguirían mal hoy —
el registro completo de OpenSky resolvió 39 más. Y `callsign` viaja en el 3% de
los mensajes, así que la mayoría de las filas están legítimamente vacías.

**Excel destroza los números.** `-34.6635411149364` se muestra como
`-34.663.541.114.936.400` porque en español el punto es separador de miles. El
archivo está bien; la interpretación no. Para analizar en Excel hay que importar
declarando el punto como separador decimal.

### Pendiente de esta sesión

- **La página de operaciones de Aeroparque** (solo lo que aterrizó o despegó, con
  el registro completo por aeronave) quedó **sin implementar**: el agente que la
  construía murió por límite de sesión. El diagnóstico previo sí se completó.
- **Las tarjetas del apartado en `/adsb/analisis` están viejas.** Muestran
  "aproximaciones / salidas / confirmadas / sobrevuelos" y **nunca** los
  aterrizajes ni los despegues, que son el titular. Da un imposible visible: 11
  confirmadas con 0 aproximaciones y 8 salidas. En `/aeropuerto/mapa` están bien.
- **`PositionGate` no evalúa el horizonte para blancos en tierra.** La rama
  `if on_ground:` solo chequea la media celda CPR (83,5 km), 6,4× el horizonte de
  superficie. Medido: **498 de 779** posiciones de superficie entraron desde más
  lejos que el horizonte al suelo desde San Isidro (63,9%, 27 aeronaves de 30), y
  0 desde Aeroparque. Ese contraste es un detector de sitio equivocado que
  funciona, y hoy no existe ningún contador para esa banda.
- **Ubicación de la PC para saber dónde está la antena** (pedido explícito). No
  implementado. Cuando se haga, debe ser *sugerencia que se confirma*, no cambio
  automático: una posición vieja en caché mediría en silencio desde el lugar
  equivocado, que es el bug que se intenta matar.

---

## Sesión 2026-08-25: la bandera de superficie, y el hex crudo que ya no se tira

### La bandera de superficie dejó de descartarse

`adsb_rtlsdr.py` calculaba la bandera real de tierra (TC 5-8 / BDS 0,6, o
`vertical_status` de DF0/16) **y la tiraba**; todo el sistema contestaba "en
tierra" por `altitude_ft <= 0`. Con el offset barométrico del 23/08 eso en
realidad significaba "a menos de 331 ft sobre la pista".

Ahora `Observation.on_ground_reported` es un **tri-estado**: `True`/`False` solo
si el mensaje lo declaró, `None` si el mensaje no hablaba del tema. `None` NO es
`False`, y esa distinción es el punto: las ~10 000 filas históricas quedan en
`None` porque nadie las escuchó decirlo.

- `is_on_ground` ahora prefiere la bandera y sólo cae a la altitud si no hay.
- La propagan las tres fuentes: `adsb_rtlsdr.py` (por formato de mensaje),
  `adsb_sbs.py` (campo 21, que antes aplastaba vacío y `'0'` en el mismo
  `False`) y `adsb.py` (el literal `"ground"` de dump1090).
- Viaja a la base y al CSV, y `adsb_events.py` la lee de vuelta por los dos
  caminos.
- **`altitude == 0` quedó deliberadamente fuera** de la bandera: es justamente
  la inferencia de la que hay que poder distinguirla.

**Sigue sin ejercitarse con tráfico real.** Es la misma advertencia de siempre:
esta antena todavía no decodificó un avión en tierra, así que el contador en
cero significa "nunca corrió".

### El CSV ya puede ganar columnas sin romper a quien lo lea

Antes había una prohibición escrita: no agregar columnas, porque el archivo se
abre en *append* y las filas nuevas quedarían con un campo de más bajo el
encabezado viejo. La prohibición se cambió por una **comprobación**:
`Recorder._ruta_compatible()` lee el encabezado del archivo del día y, si no
coincide con `COLUMNS`, rota a `adsb_2026-08-25.1.csv`. El viejo queda intacto y
legible con su encabezado; ninguno de los dos miente.

### El hex crudo ahora se guarda (`adsb_raw.py`)

Este era el agujero de fondo. El hex se decodificaba y se soltaba, así que
**todo lo que el decodificador no extraía en el momento era irrecuperable**: las
~10 000 filas grabadas tienen 8 campos porque `decoded_to_observation` extrae 8
campos, y no hay forma de sacarles un noveno. Un cambio en el decodificador no
se podía aplicar a lo ya grabado.

`adsb_raw.py` graba `epoch,hex` y nada más — ninguna decisión de interpretación
se toma ahí, porque cualquier decisión que se tome ahí es una que no se va a
poder revisar después. Cuesta ~30 bytes por mensaje.

### Decodificación completa a Excel (`adsb_decode_full.py` + `adsb_catalogo.py`)

Vuelca el diccionario **entero** que devuelve pyModeS, campo por campo. Las
columnas no están escritas a mano: son la unión de las claves que realmente
aparecieron, ordenadas por `adsb_catalogo.ORDEN`. Un campo que el catálogo no
prevé igual se exporta, y el diccionario lo marca "sin catalogar" — que es como
aparecieron `icao_verified` y `selected_altitude_mcp`, hoy ya catalogados.

Cuatro hojas: `mensajes`, `diccionario` (qué es cada columna, unidad, de qué
mensaje sale, y en qué % de filas viene con dato), `aeronaves` y `procedencia`.
El diccionario va DENTRO del archivo: un Excel que no se explica a sí mismo
obliga a adivinar.

**Medido: 76 columnas** contra las 11 del CSV operativo.

`output/adsb/CATALOGO-columnas-ADSB.xlsx` es el catálogo de columnas, generado
con **mensajes de referencia públicos, NO capturados por esta antena** — lo dice
su propia hoja de procedencia. Cubre las 9 familias de mensaje.

### Los cuatro tests dependen de `ADSB_RECEIVER` y nadie lo avisaba

**Medido hoy:** con `ADSB_RECEIVER=aeroparque`, `test_adsb_events.py` falla 6
casos y `test_adsb_position.py` falla 3. Sin la variable (default San Isidro),
los cuatro dan TODO CORRECTO. Los valores esperados están calculados desde San
Isidro.

O sea que **el resultado de la suite depende de cómo se la invoque**, y el
comando que documenta `CLAUDE.md` no fija la variable. Un test que pasa o falla
según el entorno no está midiendo lo que dice medir. Sin resolver: hay que
decidir si los tests fijan su propio receptor o si los esperados se recalculan.

### La antena está recibiendo mal, y hay que mirarlo

**Medido el 2026-08-25 desde Aeroparque, con `adsb_iq.py --medir 40`:**

| ganancia | verificados en 40 s | aeronaves | señal mediana |
|---|---|---|---|
| 49,6 dB | 2 | 1 | −31,3 dBFS |
| `auto` (AGC) | 5 | 1 | −15,9 dBFS |

`auto` gana claro: 2,5× más mensajes y 15 dB más de señal.

### rtl_adsb.exe es un demodulador mucho peor que el propio, y estaba medido sin querer

El hallazgo salió de comparar dos capturas del mismo rato con la misma antena:

| camino | mensajes | ventana | tasa |
|---|---|---|---|
| `rtl_adsb.exe -e 1` | 5 | 600 s | 0,008/s |
| IQ crudo (`adsb_iq.escuchar`), AGC | 5 **verificados** | 40 s | 0,125/s |

**Unas 15 veces más por el camino de IQ**, y encima el de IQ mide el nivel de
señal de cada mensaje, que `rtl_adsb` tira. Por eso `adsb_raw.py` graba por IQ
**por defecto**, y `--rtl-adsb` queda sólo para poder rehacer esta comparación.

Esto reencuadra lo de arriba: buena parte de "la antena recibe mal" era el
demodulador, no la antena. Lo que sigue abierto es cuánto queda de brecha real
contra los **93 DF17/18 en 40 s** de la medición histórica, y eso hay que
volver a medirlo por el camino de IQ antes de salir a revisar cables.

### Dos bugs propios que valen como recordatorio

- **Flush por cantidad sin cota temporal.** `adsb_raw.py` flusheaba cada 200
  mensajes; con el cielo flojo eso son diez minutos con el archivo en 0 bytes,
  indistinguible de un capturador roto. Ahora flushea por tiempo (2 s), igual
  que el `commit_interval_s` de `adsb_record.py`. Es el mismo error, cometido de
  nuevo en un archivo nuevo.
- **Un plazo que no vencía.** El `--seconds` se chequeaba dentro de
  `for linea in proceso.stdout`, que se bloquea esperando datos: si el aire se
  calla, no llega ningún mensaje y el plazo no vence nunca. Una captura de 720 s
  quedó colgada. Ahora un `threading.Timer` termina el proceso, el pipe da EOF y
  el bucle sale solo.

---

## Sesión 2026-09-03: un `rtl_sdr.exe` colgado deja el receptor muerto, y el tablero mentía al revés

Arrancó con una captura de `/adsb`: el punto decía **"la grabación está activa
pero NO entra nada"** y abajo, en rojo, `RuntimeError: rtl_sdr no entregó
ninguna muestra` con esta cola:

```
Enabled direct sampling mode, input 2
[R82XX] PLL not locked!
Disabled direct sampling mode
Tuned to 1090000000 Hz.
Tuner gain set to 49.60 dB.
Reading samples in async mode...
cb transfer status: 5, canceling...   (×15)
Library error -5, exiting...
```

### Lo que NO era el problema (para que nadie más lo persiga)

**Las líneas de `direct sampling mode` y `PLL not locked!`.** Asustan, dicen
"not locked" y caen justo antes del error, así que parecen la causa. No lo son:
aparecen **idénticas en las corridas que funcionan**. Verificado corriendo
`rtl_sdr.exe` a mano con el dongle libre — mismas seis líneas, y después
8.000.000 de bytes capturados limpios (`-n 4000000`, 2 bytes por muestra). Es un
sondeo de init de este build de librtlsdr y termina solo en
`Disabled direct sampling mode` → `Tuned to 1090000000 Hz`.

**La ganancia.** El log dice `49.60 dB` y el selector de la página dice
"Automatico", lo que parece un cableado roto. No lo es: ese selector es de
**fuente** (`ADSB_SOURCE`), no de ganancia. La ganancia sale de
`GANANCIA_DEFAULT = os.environ.get("ADSB_GAIN") or "49.6"` y nadie tenía
`ADSB_GAIN` puesta. Son dos cosas distintas con la misma palabra encima.

### El diagnóstico real

`cb transfer status: 5` es `LIBUSB_TRANSFER_NO_DEVICE` y `Library error -5` es
`LIBUSB_ERROR_NOT_FOUND`. Los dos dicen **no hay dispositivo**, no "no pude
configurarlo": el dongle se abrió bien —tuner encontrado, frecuencia y ganancia
puestas— y **desapareció del bus USB en medio de la lectura**.

Eso es una falla pasajera de alimentación o conexión. Lo que la volvió permanente
fue otra cosa: **el `rtl_sdr.exe` que falló nunca murió.**

| medición | valor |
|---|---|
| PID colgado | 16444, hijo vivo del server (17720) |
| arrancado | 10:07:59, con `-g 49.6` |
| seguía vivo a las | 10:56 — **49 minutos** |
| prueba standalone con él vivo | `usb_open error -3` |
| descriptor del dongle con él vivo | `0:  , , SN:` (vacío) |
| descriptor con el dongle libre | `Realtek, RTL2838UHIDIR, SN: 00000001` |

**Ese descriptor vacío es el mejor indicador que tenemos de "otro proceso lo
tiene abierto".** Cuando el dongle está libre, `rtl_sdr` lee fabricante, modelo y
número de serie; cuando está tomado, los tres vuelven en blanco. Si aparece
`0:  , , SN:` en un log, no busques el driver: buscá el proceso.

`taskkill /PID 16444 /F` liberó el dongle y el server lo retomó solo en segundos.

### Arreglo 1 — confirmar que el hijo murió (`adsb_iq.py`, `escuchar()`)

El `finally` llamaba a `proceso.terminate()` **y nada más**: ni `wait()`, ni
verificación. En Windows, `TerminateProcess` sobre un proceso trabado dentro del
driver USB puede dejarlo en un limbo que conserva los handles del kernel — y
como el dongle es **exclusivo**, un hijo en ese limbo deja el receptor inservible
hasta que alguien lo mate a mano. Una falla de USB de un segundo se convertía en
un receptor roto hasta el próximo reinicio.

Ahora: `terminate()` → `wait(timeout=3)` → `kill()` → `wait(timeout=3)`, y si ni
así murió lo **dice**, con el PID y el `taskkill` listo para copiar. Suponer que
el hijo murió era el bug.

También se separó la pista del error en dos casos, que antes se confundían:

- `usb_open error -3` / `Failed to open` → **no se pudo abrir**: casi siempre
  otro proceso lo tiene. Ese es el caso del descriptor vacío.
- `transfer status` / `Library error` → **se abrió y después desapareció**: es
  alimentación o conexión. Puerto USB directo, sin hub ni alargue (a 2 Msps pide
  unos 300 mA y se calienta), y revisar que el ahorro de energía de USB no lo
  esté suspendiendo. **No es la ganancia ni la antena**, que es exactamente donde
  uno va a mirar primero si el mensaje no lo aclara.

### Arreglo 2 — el tablero mentía, pero al revés (`adsb_rtlsdr.py`, `_procesar_hex()`)

`_loop` reintenta y se recupera, pero **nunca borraba `last_error`**. Y como
`is_receiving` es `poll_count > 0 and last_error is None`, un solo fallo dejaba
la página diciendo "la grabación está activa pero NO entra nada" **para siempre**,
con el error en rojo, mientras los datos entraban normalmente.

Medido: con `last_error` pegado de una colisión por el dongle,
`/api/adsb/status` seguía publicando el `usb_open error -3` mientras
`positions_evaluated` subía de 2075 a 2095 en 25 segundos (~0,8 posiciones/s).

`escuchar()` ya tenía un comentario diciendo que un tablero que dice grabar
mientras no recibe nada es peor que uno que se cae. **Al revés es igual de malo**:
un error rojo permanente que convive con datos que entran enseña a ignorar el
renglón del error, que es justo el renglón que importa la próxima vez.

Ahora, si llega un hex hasta `_procesar_hex`, la cadena entera —USB, muestras,
demodulación— está funcionando ahora mismo, así que el error anterior ya no
describe nada y se limpia ahí. Verificado: con `last_error` puesto a mano,
dos mensajes lo dejan en `None`, `poll_count` en 2 y `is_receiving` en `True`.

### El intérprete: `.venv` del repo, no el del sistema

Los tests fallan con el `python` del sistema y con el de WindowsApps —los dos
dicen `ModuleNotFoundError: No module named 'pyModeS'`—. **pyModeS 3.6.0 está en
`.venv` dentro del repo**, y ahí los cuatro archivos dan `TODO CORRECTO`:

```
./.venv/Scripts/python.exe test_adsb.py
```

Perdí un rato creyendo que había roto algo. Los venvs de `%USERPROFILE%\venvs`
son de MAPA-NEGOCIO y no tienen pyModeS.

### Estado del árbol al cerrar

`adsb_iq.py` quedó con dos hunks, los dos de esta sesión. `adsb_rtlsdr.py` tiene
tres: uno de esta sesión en `RtlAdsbRecorder`, y dos en
`decoded_to_observation` que ya estaban sin commitear de la sesión del
2026-08-25, igual que `adsb.py`, `adsb_events.py`, `adsb_record.py`,
`adsb_sbs.py`, `adsb_catalogo.py`, `adsb_decode_full.py` y `adsb_raw.py`.
**No se commiteó nada** para no mezclar trabajo ajeno en curso con estos dos
arreglos.

### El receptor, ahora

Recibiendo. 282 aeronaves, 2208 registros, 2095 posiciones evaluadas con **3
rechazadas**. `with_registration` sigue en 0, que es lo esperado con el registro
tal como está y no un síntoma de esto.

### Pendiente que salió de acá

- **Detectar un `rtl_sdr.exe` ajeno ANTES de arrancar** y decirlo con el PID, en
  vez de fallar y recién entonces sugerir que lo busque. Los datos para hacerlo
  ya están: el descriptor vacío basta.
- **Ver por qué el dongle se cae del bus.** Esta vez pasó una vez; si se repite,
  es puerto, cable o calor, y conviene anotar cuándo.

---

## Sesión 2026-09-03 (2): "hace cuánto despegó" sí, la edad del avión no

Pedido: ver en el análisis **la edad de la aeronave** y **hace cuánto despegó**.
Son dos preguntas con respuestas muy distintas.

### "Hace cuánto" ya estaba calculado y no se mostraba

`Operacion.timestamp` existe desde siempre —el instante más bajo dentro del
cilindro, que en un despegue es la carrera— y `como_json()` ya lo publicaba.
Faltaba la columna. Se agregó **Hace cuánto** al lado de **Cuándo**, en el grupo
de la operación, en `webapp/templates/aeropuerto_operaciones.html`.

Van las dos columnas y no una: "03/09 11:42" **ubica** el hecho y "hace 27 min"
dice si **todavía importa**. Son preguntas distintas y obligar a hacer la resta
de cabeza es lo que hace que nadie mire la columna.

Tres decisiones que valen anotarse:

- **Se calcula en el navegador, no en el servidor.** El valor envejece: si
  viniera resuelto en el JSON, una pestaña abierta media hora seguiría diciendo
  "hace 2 min" para siempre, que es peor que no mostrarlo.
- **Se refrescan SOLO esas celdas** (`refrescarHace()` sobre `[data-hace]`, cada
  30 s), no la tabla entera. Repintar tira el scroll y la selección de texto: una
  página que se sacude sola cada 30 segundos por una columna es un downgrade.
- **Ordena por el `timestamp`, no por el texto.** Alfabéticamente "hace 9 min"
  va después de "hace 10 min". Una columna de tiempo que ordena mal es peor que
  una que no se puede ordenar.

Verificado en vivo sobre las 33 operaciones reales: la columna aparece, los
`colspan` del grupo se reajustaron solos de 7 a 8 (se cuentan desde `COLUMNAS`,
no están escritos a mano), el orden invierte bien en los dos sentidos, y
envejeciendo un dato 45 min a mano la celda pasó de "hace 6 min" a "hace 52 min".

Efecto secundario útil: la columna **hace visible que la grabación mezcla épocas**.
Hay operaciones de "hace 11 d" —la era de San Isidro— junto a las de hoy desde
Aeroparque. Antes eso estaba en la tabla y no se notaba.

### La edad del avión NO se puede dar hoy

`tools/aircraft_db.sqlite` tiene **609 357 aeronaves y ocho columnas**: `icao24`,
`registration`, `manufacturer`, `model`, `typecode`, `operator`, `operator_icao`,
`operator_iata`. **Ninguna fecha.** No es que venga vacía: no existe.

El CSV completo de OpenSky **sí** trae `built` y `firstflightdate` (y también
`serialnumber`, `linenumber`, `registered`, `reguntil`, `status`). El `ALIAS` de
`aircraft_db.py` no los mapea, así que la importación los descarta en silencio.

Y el archivo fuente **ya no está** en `~/Downloads`. Para tener la edad hacen
falta tres cosas, en este orden:

1. Volver a bajar `aircraft-database-complete-*.csv` de OpenSky.
2. Agregar `built` (y quizá `firstflightdate`) al `SCHEMA` y al `ALIAS`.
3. Reconstruir la base — con el server parado, porque tiene el archivo tomado
   (ese `PermissionError` ya pasó una vez).

**Antes de prometer la columna hay que medir la cobertura de `built`.** En OpenSky
está bastante poblada para aviones de línea y bastante vacía para aviación
general, y este es un repo donde una columna que resuelve el 20% se cuenta, no se
muestra como si resolviera todo.

### Cobertura del registro: el 9% era un artefacto

Midiendo mal daba **9%**; midiendo bien da **95%**. La diferencia importa:

| población | resuelven | |
|---|---|---|
| 3984 icao24 distintos en toda la base | 372 | **9%** |
| 120 que emitieron **al menos una posición** | 114 | **95%** |

Los 3984 incluyen las direcciones fantasma del ruido de la torre de YPF, que
aparecían con dos mensajes y sin posición nunca. Esas direcciones no existen, así
que pedirle al registro que las resuelva y contar el fracaso es medirse mal a uno
mismo. **El filtro correcto para "tráfico real" es tener posición emitida.**

### El server que corre puede estar viejo, y no lo dice

Buscando la página me dio 404 en `/aeropuerto/operaciones` —la ruta es
`/aeropuerto`, sin sufijo— pero además **el server de las 10:07 daba 404 en las
dos**: no tenía la ruta registrada aunque `@app.get("/aeropuerto")` está en HEAD.
uvicorn corre sin `--reload`, así que el código se congela en el arranque; con el
repo dentro de OneDrive, los archivos pueden llegar después de que el proceso
importó.

Para verificar sin cortar la grabación se levantó una **segunda instancia en el
puerto 8010** con la misma configuración. Es seguro: `webapp/main.py` **no
arranca el grabador solo** (no hay `on_event`, `lifespan` ni `.start()`), así que
una segunda instancia no le pelea el dongle a la primera mientras nadie toque
"Iniciar". Queda como la forma de probar cambios de la webapp con una grabación
en curso.

Corolario operativo: **si una página tira 404 o el error rojo no se va, primero
reiniciar el server.** El estado que publica `/api/adsb/status` es del proceso, no
del disco.

---

## Descartado con números: la edad del avión NO sale de OpenSky

Se bajó el CSV completo (`aircraft-database-complete-2025-08.csv`, 108 MB, la
**última** que publica OpenSky: el listado del bucket no tiene nada posterior a
2025-08) y se midió. La respuesta es no, y conviene entender por qué para no
reintentarlo.

### `built` es un campo de Estados Unidos

| población | con `built` | |
|---|---|---|
| CSV completo | 162 932 / 603 658 | 27,0 % |
| **Estados Unidos** | 162 806 / 365 942 | **44,5 %** |
| Canadá | 16 / 38 699 | 0,0 % |
| Alemania | 2 / 33 048 | 0,0 % |
| Reino Unido | 2 / 26 830 | 0,0 % |
| Francia, Italia, China, Rusia | 0 | 0,0 % |
| Brasil | 7 / 5 659 | 0,1 % |
| **Argentina** | **0 / 1 813** | **0,0 %** |
| Uruguay, Paraguay, Bolivia, Panamá | 0 | 0,0 % |

**El 99,92 % de los valores de `built` son de matrículas estadounidenses.** Es
casi con seguridad un volcado del registro de la FAA, que publica año de
fabricación; los demás registros civiles no le pasan ese campo a OpenSky. No es
que esté incompleto para Argentina: está en **cero exacto** sobre 1813 aeronaves.

### Sobre nuestro tráfico

| población | con `built` |
|---|---|
| 122 direcciones con posición emitida | 6 (5 %) |
| **33 que aterrizaron o despegaron en Aeroparque** | **0 (0 %)** |

Las 6 que sí tienen fecha son **todas matrículas N**: tres 777 y un 787 de
American, un A330 de Delta, un Cessna 210 de Sky West. Ninguna operó en
Aeroparque — son sobrevuelos.

### Y la fecha no es una fecha

De los 162 932 valores de `built`, **162 836 terminan en `-01-01`**. O sea que el
campo es un **año** rellenado a formato fecha. Mostrar "nacido 2019-01-01" sería
publicar una precisión inventada de día y mes, que es justo lo que este repo no
hace.

`firstFlightDate` es todavía peor: **457 filas en todo el archivo (0,1 %)**, y 0
en nuestro tráfico.

### Conclusión

**No se agrega `built` al esquema.** Una columna que resuelve 0 de 33 operaciones
no es una columna con poca cobertura: es una columna vacía con un encabezado. Si
alguna vez hace falta la edad del avión para tráfico argentino, hay que buscarla
en otra fuente —el registro de ANAC, o una base de flotas comercial—, no acá.

### Lo que sí apareció: número de serie

Midiendo todos los campos del CSV sobre las 32 operaciones que están en él:

| campo | con dato | ¿ya lo usamos? |
|---|---|---|
| `typecode` | 100 % | sí |
| `country` | 100 % | se deriva del bloque del ICAO24 |
| `operatorIcao` | 81 % | sí |
| `model` | 81 % | sí |
| **`serialNumber`** | **66 %** | **no — es lo único nuevo que sirve** |
| `owner` | 66 % | sí (alias de `operator`) |
| `built`, `firstFlightDate`, `lineNumber`, `registered`, `regUntil`, `status`, `categoryDescription`, `engines` | 0 % | — |

`serialNumber` identifica el **fuselaje físico**, que es más estable que la
matrícula (la matrícula cambia de dueño, el serial no). Queda como candidato real
para "saber todo lo posible de cada vuelo". `engines` viene en 0 %, así que los
motores tienen que seguir saliendo del Doc 8643 como ahora.

Agregarlo pide reconstruir la base de 49 MB **con el server parado**, porque
tiene el archivo tomado.

---

## Los dos bugs que hacían que un despegue visto con los ojos no se contara

Fran vio despegar al JES3882 de Aeroparque y el tablero no lo contó. Buscando por
qué aparecieron dos bugs distintos, uno de ellos grave.

### Bug 1: una sola operación por ICAO24 en TODA la grabación

`resumir_cilindro()` agrupaba por dirección, así que una aeronave tenía como
máximo **una** operación en la historia entera. Verificado: el máximo de
operaciones por dirección era 1, y **92 de las 4166 direcciones** aparecían en más
de un día. `e8061b` figuraba en 3 días y su única operación era la del 22/08.

Y no solo perdía: **inventaba**. Las **7 "motor y al aire"** del tablero venían
todas de direcciones vistas en 2 o 3 días distintos. Reclasificando día por día,
**ninguna** era un motor y al aire:

| dirección | junto | separado por día |
|---|---|---|
| e082d6 ARG1874 | frustrada | despegue + despegue |
| e0b392 ARG1770 | frustrada | aterrizaje + despegue |
| e8062a JES3881 | frustrada | aterrizaje + en tierra |
| e8061d JES3049 | frustrada | salida + despegue + despegue |
| e06459 ARG1680 | frustrada | aterrizaje + en tierra |
| e07583 ARG1883 | frustrada | aterrizaje + despegue |
| e8062f JES3182 | frustrada | aterrizaje + despegue |

Es obvio una vez visto: mezclar el aterrizaje del 22 con el despegue del 23 da
`baja` grande, `sube` grande y mínima baja — la firma exacta de una frustrada. El
sistema afirmaba siete frustradas inexistentes, que es **lo contrario** de lo que
se pidió cuando se pidió contar aterrizajes reales y no intentos.

**Arreglo:** el resumen se indexa por `(icao24, pasada)`. Una pasada se cierra
cuando la dirección deja de emitir **dentro del cilindro** por más de
`HUECO_PASADA_S` (600 s, elegido por Fran; `ADSB_PASS_GAP_S` lo cambia). El hueco
se mide sobre lo que entró al cilindro y no sobre todo lo que emitió la aeronave:
irse diez minutos y volver son dos visitas, y que mientras tanto se la siguiera
escuchando en crucero no las une.

### Bug 2: sin altitud no se podía decir el sentido

Los mensajes de superficie (BDS 0,6) **no traen altitud por formato**. Cuando de
una pasada solo llegaban esos, `min_alt` era `None`, `baja` y `sube` valían 0, y
la pasada caía en "en tierra" sin importar qué hubiera hecho.

Medido en el JES3882 del 03/09: **28 posiciones de superficie a 0,08 – 0,46 km de
SABE, con la velocidad cayendo 90 → 21 → 8 → 0 kt.** Un aterrizaje que estaba
escrito con toda claridad en los datos.

**Arreglo:** los mensajes de superficie sí traen velocidad respecto al suelo, y
eso da el sentido sin ninguna altitud. `sentido_de_carrera()` devuelve `frena`
(carrera de aterrizaje), `acelera` (carrera de despegue) o `None`. Umbrales:
`CARRERA_KT = 60` separa la carrera del rodaje —un avión rodando anda bajo 30 kt y
una carrera pasa los 100— y `DELTA_CARRERA_KT = 40` exige que el cambio sea grande
antes de afirmar el sentido. Es evidencia **más fuerte** que el delta de altitud,
no un respaldo débil: no interviene ninguna interpretación barométrica.

### Los números

| | antes | después |
|---|---|---|
| aterrizajes | 9 | **23** |
| despegues | 23 | **36** |
| **operaciones reales** | **32** | **59** |
| motor y al aire | 7 | **0** |
| pasadas por el cilindro | (no existía) | 110 |
| aeronaves en el cilindro | 69 | 69 |

**+84 % de operaciones reales y 7 frustradas falsas eliminadas.** El bug 1 solo
llevaba de 32 a 46; los 13 restantes los aporta la carrera de pista, que resolvió
**32 pasadas** con un patrón perfectamente consistente: `frena` dio aterrizaje y
`acelera` dio despegue, sin una excepción.

### Un acumulador, no dos

`aeropuerto.resumir_cilindro` y `adsb_events.LectorIncremental._absorber_cilindro`
tenían **la misma lógica escrita dos veces**, vigiladas por un test que las compara
campo por campo. Con la segmentación por pasada el estado dejó de ser un dict
plano por dirección —hay que recordar el último timestamp y el número de pasada— y
duplicar eso costaba el doble y divergía igual. Ahora las dos llaman a
`aeropuerto.acumular_en_cilindro()` y `_absorber_cilindro` no reimplementa nada.

### El cuadre cambió de referencia

`suma_categorias` tiene que dar **`pasadas_en_cilindro`**, no
`aeronaves_en_cilindro`: cada pasada produce exactamente una clasificación, y una
aeronave que entró tres veces aporta tres categorías. Comparar contra aeronaves
daría "NO CUADRA" siempre. Se publican los dos números —110 pasadas de 69
aeronaves dice algo que ninguno de los dos solo dice— y las **cuatro** pantallas
que muestran el cuadre se actualizaron: `index.html`, `adsb_analisis.html`,
`aeropuerto_mapa.html` y `aeropuerto_operaciones.html`.

### Campos nuevos

- `Operacion.pasada` — el número de pasada, desde 0.
- `Operacion.carrera` — `frena` / `acelera` / `None`, la evidencia que sostiene la
  clasificación cuando no hubo altitud. Va en el JSON y **se muestra** en la
  columna **Carrera** de `/aeropuerto`: lo que sostiene una afirmación va a la
  vista, no en un tooltip.
- `Informe.pasadas_en_cilindro`.
- Variable de entorno nueva: **`ADSB_PASS_GAP_S`** (default 600).

### Lo que NO se arregló, y por qué

**El despegue que Fran vio no se cuenta todavía.** Hoy ese avión tiene tres
pasadas y solo una toca el cilindro:

| pasada | qué es | ¿dentro del cilindro de 8 km / 4000 ft? |
|---|---|---|
| 13:45–13:49, 10500 → 5475 ft, 35 → 12 km | aproximación | **no**, mínimo 12,3 km |
| 14:00–14:05, 28 posiciones de superficie, 90 → 0 kt | aterrizaje — ahora sí se cuenta | sí |
| 16:03–16:20, 5425 → 33925 ft, 15 → 128 km | **el despegue** | **no**, la primera posición ya está a 5425 ft |

Del despegue de las 16:00 **no hay ni una posición dentro del cilindro**: entre la
última de superficie (14:05:51, parado a 0 kt) y la primera del ascenso
(16:03:05, 5425 ft a 14,85 km) hay un hueco de 117 minutos. Se perdieron los
primeros dos o tres minutos del ascenso, que es justo el tramo que cruza el
cilindro. Causa probable: el bootstrap CPR de posiciones aéreas necesita 3 pares
consistentes y arranca de cero al pasar de superficie a aéreo.

**Se podría inferir** —estaba demostrablemente en el campo a las 14:05 y
demostrablemente subiendo y alejándose a las 16:03, y lo único que hay entre esas
dos cosas es un despegue— pero eso es una categoría de evidencia nueva: cruzar
DOS pasadas en vez de clasificar una. No se implementó. Si se hace, tiene que
quedar marcada como inferida, como ya se hace con `registration_source`.

### Verificación

Los cuatro archivos de test dan `TODO CORRECTO`, incluido `test_adsb_incremental`,
que compara `como_json()` de las dos rutas campo por campo — la prueba de que
unificar el acumulador no rompió la equivalencia.

Se agregaron **12 aserciones** en `test_adsb_incremental.py`, sección 8, con
observaciones construidas y no esperando que pase un avión. Las dos que más valen
son los controles negativos:

- **bajar y volver a subir SIN hueco sigue siendo una frustrada** — el criterio de
  motor y al aire no se debilitó, solo dejó de aplicarse a días distintos.
- **rodar por debajo de `CARRERA_KT` queda "en tierra"** — el rodaje lento no se
  promueve a operación.

Las cuatro páginas se verificaron en una segunda instancia en el puerto 8011, sin
cortar la grabación en curso. `/aeropuerto/mapa` ejercita el camino
**incremental** y dio los mismos 110/69, o sea que el acumulador compartido
funciona por las dos rutas.

### Confirmado en producción: el error pegado

El grabador se reinició y `/api/adsb/status` ahora publica `error: None` con la
grabación andando. Antes del arreglo de `last_error` ese campo se quedaba con el
`usb_open error -3` para siempre. Los dos arreglos del commit b1ff14a están vivos.
