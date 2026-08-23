# IA-CONTEO-AVIONES

Detección, seguimiento y conteo de aterrizajes y despegues sobre video de pista, con identificación de aerolínea, matrícula y tipo de avión.

Usa [supervision](https://github.com/roboflow/supervision) + Ultralytics YOLO para la visión, y un modelo de visión-lenguaje para leer matrículas.

---

## Instalación desde cero en otra PC

### Forma rápida (Windows)

Doble clic en **`INSTALAR-Y-EJECUTAR.bat`**. Verifica Python, crea el entorno,
instala las dependencias, deja la configuración lista, arranca el servidor y
abre el navegador. La primera vez tarda varios minutos porque baja ~1,5 GB;
después arranca en segundos, porque detecta lo ya instalado y no lo repite.

Si no tenés Python, el propio script ofrece instalarlo.

### Forma manual

```bash
git clone https://github.com/noguerafran-alt/IA-CONTEO-AVIONES.git
cd IA-CONTEO-AVIONES

python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt        # Windows
# source .venv/bin/activate && pip install -r requirements.txt      # Linux/Mac
```

Eso es todo lo obligatorio. Los pesos del detector (`yolov8n.pt`) los descarga Ultralytics solo la primera vez que corrés el pipeline.

### Opcional: leer matrícula y tipo de avión

Requiere una clave gratuita de [OpenRouter](https://openrouter.ai/keys):

```bash
cp .env.example .env      # y editá .env con tu clave
```

Sin esto el sistema funciona igual: detecta, sigue y cuenta aviones, y lee la aerolínea con OCR local. Lo único que no hace es leer la matrícula.

### Opcional: acelerar con GPU Intel

```bash
.venv\Scripts\python.exe export_openvino.py --weights yolov8n.pt
```

Medido en una Intel Iris Xe: la detección pasa de 6,9 a 40,2 fps.

### Probar que quedó bien

```bash
.venv\Scripts\python.exe detect_track_count.py \
  --source tu_video.mp4 \
  --line-start 960,0 --line-end 960,1080 --no-display
```

Y para ver el dashboard, doble clic en `dashboard.bat` (Windows) o:

```bash
.venv\Scripts\python.exe -m uvicorn main:app --app-dir webapp --port 8000
```

### Qué NO está en el repositorio

Por tamaño o por derechos de terceros, y cómo obtenerlo:

| Falta | Por qué | Cómo se consigue |
|---|---|---|
| `data/` (videos) | Contenido de terceros, GB de peso | Poné tus propios videos ahí |
| `DATOS AVIONES/` (PDFs) | Material de un sitio de terceros | Los datos ya extraídos están en `aircraft_specs.json` |
| `.venv/` | Se recrea | `pip install -r requirements.txt` |
| `output/`, `dataset/`, `runs/` | Se generan | Corriendo el pipeline |
| `yolov8n.pt` | Se descarga solo | Automático en el primer uso |
| `.env` | **Contiene tu clave privada** | Copiá `.env.example` |

---

## Grabar datos ADS-B (funciona sin cámara)

Antes de tener cámara ya se puede recolectar. Sirve para responder con datos
—no con suposiciones— las preguntas que definen el resto del proyecto:
si la antena recibe desde esa ubicación, cuántas operaciones hay por hora, y
si las aeronaves de la zona transmiten matrícula o solo el código ICAO24.

### Con un RTL-SDR Blog V4 (o cualquier RTL-SDR): no hace falta programa externo

Este proyecto lee el dongle **directamente**, sin depender de un decodificador
de terceros. Usa el binario oficial `rtl_adsb.exe` de RTL-SDR Blog (que ya
incluye los drivers correctos del V4) solo para pasar de radio a mensajes en
crudo, y decodifica todo lo demás en Python con
[pyModeS](https://github.com/junzis/pyModeS).

```bash
INSTALAR-ADSB.bat      # una vez: baja rtl_adsb.exe + guía el driver del dongle
GRABAR-ADSB.bat        # cada vez: conectá el dongle y grabá
```

`INSTALAR-ADSB.bat` baja los binarios oficiales, y guía el único paso manual
que queda: instalar el driver **WinUSB** con **Zadig** (gratuito, se abre
solo). Windows reconoce el dongle por defecto como sintonizador de TV, que no
sirve para esto — hay que decirle que use WinUSB. Ese paso es igual para
cualquier programa de ADS-B, no es específico de este proyecto.

### Alternativa: un programa de ADS-B ya instalado

Si preferís usar `dump1090`, RTL1090 u otro decodificador que ya tengas
andando, `adsb_record.py` también sabe leerlos:

```bash
python adsb_record.py --source sbs     # feed BaseStation/SBS-1 (puerto autodetectado)
python adsb_record.py --source json    # dump1090 aircraft.json
```

Sin indicar `--source`, se detecta solo: si existe
`tools/rtlsdr/rtl_adsb.exe` usa el dongle directo, si no busca un feed SBS-1.

### Desde el navegador, en vez de la terminal

`http://localhost:8000/adsb` (con `dashboard.bat` corriendo) hace lo mismo
que `adsb_record.py` pero con página en vez de texto en la terminal:
contadores en vivo, tabla de aeronaves en rango, botón para iniciar/detener,
y **botón para descargar el CSV** sin ir a buscarlo a mano en `output/adsb/`.

### Grabando

Genera dos salidas en paralelo:

- **`output/adsb/adsb_AAAA-MM-DD.csv`** — una fila por observación, con
  volcado inmediato a disco: el archivo se puede abrir en Excel **mientras la
  grabación sigue corriendo**, y un corte de luz cuesta lo no escrito, no todo.
- **`adsb_log.db`** — los mismos datos en SQLite, para consultarlos junto a los
  de la cámara más adelante.

Los archivos rotan por día, así ninguno se vuelve inmanejable.

**Columnas:** `utc`, `epoch`, `icao24`, `registration`, `callsign`,
`altitude_ft`, `ground_speed_kt`, `vertical_rate_fpm`, `latitude`,
`longitude`, `on_ground`.

### Dónde está la antena: `ADSB_SURFACE_REF`

La posición de un avión no viaja completa en un mensaje: ADS-B la manda
codificada en CPR, que da la posición **dentro de una zona** y hay que
resolver cuál. Para los aviones **en vuelo** se resuelve con dos tramas
(par + impar) de la misma aeronave. Para los aviones **en pista y rodando**
—los que importan para contar operaciones— hace falta además un punto de
referencia cercano, y ese punto es **dónde está la antena**.

Por defecto se usa **San Isidro** (`-34.4708, -58.5128`). Para cambiarlo:

```bash
set ADSB_SURFACE_REF=-34.4708,-58.5128          # Windows, cmd
python adsb_record.py --surface-ref -34.60,-58.40
python adsb_record.py --surface-ref SADF        # o un código ICAO de aeropuerto
```

La variable de entorno es el canal que llega igual a la terminal y al
dashboard (el dashboard nunca ve los argumentos de línea de comandos). No va
en `.env` porque no es un secreto.

**Por qué la referencia es la antena y no un aeropuerto:** la tolerancia
medida es ±83 km (los 45 NM de DO-260B), así que un solo punto en San Isidro
alcanza para los tres aeropuertos de la zona a la vez — San Fernando a 7.3 km,
Aeroparque a 13.3 km y Ezeiza a 39.1 km, medidos con el haversine del repo.
Poner `--surface-ref SADF` funciona igual pero descarta los otros dos sin
necesidad. Ver [receiver.py](receiver.py), que es el único lugar donde vive
esta coordenada: el decodificador y la métrica de distancia la importan de
ahí, para no medir cada uno desde un punto distinto.

**Comportamiento contraintuitivo, medido y vale saberlo:** pyModeS 3.6 no
emite la primera posición de un avión en vuelo hasta juntar ~3 posiciones CPR
consistentes entre sí. Reproduciendo el par de mensajes clásico, la primera
latitud aparece en el **6.º mensaje**, no en el 2.º. En superficie, en cambio,
un mensaje suelto ya alcanza (se resuelve contra la referencia, sin esperar
pares). Que las primeras posiciones tarden en aparecer es normal.

Las posiciones **en superficie** están implementadas y verificadas por replay
de mensajes construidos (error de 0.25 a 0.62 m sobre los tres aeropuertos),
pero **todavía no se validaron con tráfico real**: esta antena, a 7 km de la
pista más cercana, no ha recibido un solo avión en tierra.

### Alcance medido: la mediana, no el percentil 95

Con la posición ya decodificando se puede medir hasta dónde llega la antena, y
la primera medición real dejó una lección sobre qué número publicar. Sobre 43
posiciones:

| | distancia al receptor |
|---|---|
| 39 posiciones | 9.9 – 31.9 km |
| 4 posiciones | 70.5 – 72.4 km |

Esas 4 son todas del mismo avión, en crucero a 36 000 ft. Con esa forma, el
percentil 95 cae **dentro** del grupo lejano (71.3 km): con n = 43 el p95 es
literalmente el tercer valor más grande, así que no recorta la cola, la
reporta. Publicarlo como "el alcance que la antena sostiene" era falso —
sostiene unos 20 km, no 71.

Por eso la interfaz muestra la **mediana** (19.7 km) junto al máximo, y el p95
solo aparece cuando hay muestra suficiente para que signifique algo. La brecha
entre mediana y máximo no es ruido: es altitud. A 70 km solo se escucha lo que
vuela alto, porque a un avión bajo lo tapa el horizonte.

### Filtro de posiciones imposibles: horizonte de radio, no "50 km"

Sobre 783 posiciones decodificadas hay **exactamente una** imposible:

| icao24 | hora (UTC) | posición | altitud | distancia |
|---|---|---|---|---|
| `e0b14a` | 20:08:27 | −28.07277, −54.90687 | 19 525 ft | **789.5 km** |

Ese mismo avión estaba a 50.0 km a las 20:07:03 y volvió a su ruta normal a las
20:08:53: un punto aislado entre dos tramos correctos. La entrada al punto son
748.7 km en 36 s (40 813 kt) y la salida 756.0 km en 27 s (54 968 kt). Es un
error de índice de zona CPR — la latitud emitida cae exactamente una zona impar
de más (360/59 = 6.101695° contra los +6.101982° medidos, 32 m de diferencia).

**Por qué 50 km era el corte equivocado.** Cortar a 50 km del aeropuerto tira
además estas cuatro, que son legítimas (trazas continuas, muchos puntos):

| icao24 | distancia | altitud | posiciones |
|---|---|---|---|
| `e49bff` | 72.4 km | 36 000 ft | 4 |
| `e8061b` | 70.6 km | 27 950 ft | 65 |
| `e49aa8` | 63.9 km | 16 750 ft | 25 |
| `e492aa` | 59.1 km | 37 025 ft | 15 |

Perderlas rompe justamente la medición de alcance de la antena, que es el
propósito declarado de `/adsb/mapa`. Y un "50 km" hardcodeado no se puede
defender: si la antena se muda, el número deja de tener sentido y nadie se
entera.

**La regla física.** El horizonte de radio a una altitud dada es geometría, no
un umbral:

```
horizonte_km(alt_ft) = 4.124 * (sqrt(alt_ft * 0.3048) + sqrt(ANTENA_M))
límite_km            = 1.35 * horizonte_km(alt_ft)
```

Es la misma fórmula que la clásica `1.23 * (sqrt(h1_ft) + sqrt(h2_ft))` en NM
(`4.124 * sqrt(0.3048) / 1.852 = 1.2294`) y las dos ya llevan adentro el radio
terrestre 4/3 por refracción (`3.57 * sqrt(4/3) = 4.1223`).

| altitud | horizonte | límite (×1.35) |
|---|---|---|
| 0 ft | 13.0 km | 17.6 km |
| 5 000 ft | 174.0 km | 234.9 km |
| 10 000 ft | 240.7 km | 325.0 km |
| 19 525 ft | 331.2 km | **447.1 km** |
| 27 950 ft | 393.7 km | 531.5 km |
| 36 000 ft | 445.0 km | 600.8 km |
| 45 000 ft | 496.0 km | 669.6 km |

El 1.35 existe porque el horizonte **no es una pared**: el ducting troposférico
mete 1090 MHz bastante más lejos, y esta página existe justamente para medir
recepciones excepcionales. El factor se puede citar en vez de defender: PiAware
corta a 360 NM = 666.7 km, que es 1.344 veces el horizonte 4/3 de un avión a
45 000 ft. Medido sobre las 783 posiciones, cualquier factor entre 1.0 y 2.38
rechaza exactamente la misma única posición: dos órdenes de holgura.

Con esa regla, las cuatro legítimas pasan con muchísimo margen:

| icao24 | distancia | límite | margen |
|---|---|---|---|
| `e49bff` | 72.4 km | 600.8 km | 8.3× |
| `e8061b` | 70.6 km | 531.5 km | 7.5× |
| `e49aa8` | 63.9 km | 415.4 km | 6.5× |
| `e492aa` | 59.1 km | 609.0 km | 10.3× |
| `e0b14a` (fantasma) | 789.5 km | 447.1 km | **0.57× — rechazada** |

**Segunda regla: continuidad de velocidad.** El horizonte solo atrapa el
fantasma que cae lejísimos. Una zona de *longitud* mal elegida a 36 000 ft puede
aterrizar a 200 km, muy por debajo del límite de 600.8 km, y el horizonte no la
ve. Para eso corre `speed_check` de dump1090-fa contra la última posición
**aceptada** (nunca la última recibida), con la velocidad que el propio avión
transmitió y un factor ×2 de holgura por el sello de tiempo.

Lo de "última aceptada" no es un detalle de estilo, está medido:

| referencia | posiciones rechazadas |
|---|---|
| última **aceptada** | 1 (solo el fantasma) |
| última **recibida** | 2 (el fantasma **y la posición buena de 20:08:53**) |

Es la razón por la que dump1090 nunca guarda una posición rechazada como
referencia — y de paso es un bug de pyModeS, cuyo `_update_position_history`
(`_pipe.py:668-685`) mete al historial también las rechazadas, así que el
*segundo* fantasma de una ráfaga pasa.

El ×2 también está medido, no elegido: con las constantes crudas de dump1090-fa
el peor caso legítimo de las 783 posiciones queda en 0.896 del límite (11 % de
margen, demasiado poco); con ×2 baja a 0.45 y el fantasma sigue a 47× del
límite. La razón física es que dump1090 sella tiempo con el reloj del SDR y acá
el sello es `time.time()` en el hilo de Python, después del buffer USB.

**Dos casos de borde más**, ambos declarados aunque hoy no se ejerciten:

| regla | umbral | de dónde sale | ejercitada hoy |
|---|---|---|---|
| sin altitud | 555.6 km (300 NM) | `Modes.maxRange` de dump1090-fa | no (783 de 783 filas traen altitud) |
| superficie | ±0.75° lat (83.5 km) | media celda CPR, DO-260B A.1.7.6 | **no** (126 filas `on_ground`, ninguna con posición) |

**Nada se descarta en silencio.** El filtro no borra la fila: anula `lat`/`lon`
y deja altitud, velocidad y régimen vertical, porque en el caso medido la trama
era auténtica (los 19 525 ft encajan con los −2048 fpm transmitidos en las dos
ramas del descenso) y lo único corrupto era el par lat/lon. El rechazo se
publica en cuatro lugares: la tarjeta de `/adsb/mapa`, el texto de cobertura de
`/adsb/analisis`, el CLI de `adsb_report.py` y `coverage_report()`. En el mapa
el punto se **dibuja** como cruz gris hueca, sin unirlo a la traza y fuera del
encuadre automático (era ese único punto el que escalaba el mapa entero a
790 km).

Corre en los dos lados: en **lectura** (`adsb_events.load_db`) es donde se
arregla lo que ya está en la base, y en **ingestión** (`adsb_record.Recorder`)
sin rechazar, solo escribiendo el motivo en la columna nueva `rejected_reason`.
Esa columna tiene **tres** estados y confundirlos hace mentir cualquier
consulta:

| valor | significa |
|---|---|
| `NULL` | fila anterior al filtro — **no evaluada** |
| `''` | evaluada y limpia |
| texto | el motivo (`horizonte`, `velocidad`, `superficie`, `sin_altitud`) |

La columna se agrega con `ALTER TABLE ADD COLUMN`, que es no destructivo e
instantáneo, pero **necesita que no haya una grabación en curso**: con otra
escribiendo, SQLite devuelve `database is locked` (reproducido). En ese caso el
recorder no arranca y dice exactamente eso, en vez de degradarse escribiendo
sin la columna — perder el motivo en silencio sería el bug que este cambio
existe para evitar. Detené la grabación, arrancala de nuevo y la migración
corre sola.

**Lo que este filtro NO arregla**, y conviene decirlo: 2151 de 2343 icao24
(91.8 %) aparecen una sola vez y ~2029 son direcciones fantasma nacidas de
errores de bit en DF4/DF11/DF20, donde la paridad va XOR-eada con la dirección
y no se puede verificar. Ninguna trae posición, así que ningún filtro de
posición las ve, y la página que dice "2343 aeronaves distintas" está inflada
~12×. Eso es otro filtro (`icao_verified`) y es un trabajo aparte.

Y desde ahora se publica también **lo que descarta pyModeS**
(`decoder.stats`: `position_rejected`, `crc_fail`, `altitude_mismatch`,
`velocity_mismatch`), que el repo no leía en ningún lado. Ese número puede ser
mayor que el del filtro propio: `_motion_consistent` ya venía tirando
posiciones en silencio. Publicarlo puede hacer quedar peor a la página en el
corto plazo; es el precio de no mentir.

El test que fija el porqué es `test_adsb_events.py`, secciones 14 a 18. Si
alguien "simplifica" esto a un corte fijo de 50 km, las cuatro legítimas se
ponen en rojo.

### Mapa: dónde estuvo lo que la antena escuchó

`/adsb/mapa` dibuja las trayectorias decodificadas sobre los tres aeropuertos
de la zona, con el receptor al centro y anillos de distancia. Cada traza se
colorea por la altitud **de ese tramo**, así se distingue de un vistazo una
aproximación de un vuelo de paso.

Es un SVG generado en el navegador, **sin tiles ni CDN**: el resto del sistema
funciona sin internet y un mapa con fondo de OpenStreetMap lo rompería justo
cuando más se lo necesita. Se pierde el fondo satelital; la costa del Río de la
Plata sale de **Natural Earth** y las pistas de **OurAirports**, con la fuente a
la vista y los dos umbrales reales de cada pista. Dibujadas a ojo se verían
igual de convincentes y estarían torcidas.

Por defecto el mapa se encuadra al grueso del tráfico y avisa cuántas aeronaves
quedaron fuera, con un botón para verlas: un solo avión de crucero a 72 km
obliga a abrir el encuadre tanto que las aproximaciones cercanas quedan
ilegibles. Nada se oculta en silencio — las de fuera de cuadro siguen en la
lista lateral y en los totales.

Si el mapa queda vacío, la página **no** dibuja un mapa vacío ni rellena con
posiciones simuladas: explica **cuál** de las causas posibles es. Son distintas y
no se pueden confundir — no llegó ninguna posición, llegaron y el filtro las
rechazó a todas, o las que hay quedaron fuera de la ventana de dibujo de 3 h por
antigüedad. En este último caso el número de lo recortado está escrito y hay un
botón **Ver la grabación entera** que lo pide.

**Se actualiza solo cada 5 s**, así que sirve para mirar mientras la antena
graba. Los detalles que hacen que el modo en vivo no moleste:

- El indicador de arriba dice si la captura está **corriendo o detenida**. Un
  mapa que se refresca con la grabación parada se ve idéntico a un cielo vacío;
  el punto de estado distingue los dos casos y enlaza a `/adsb` para arrancarla.
  Y publica `lag_s`: cuán vieja es la fila más nueva. Un poll exitoso cada 5 s
  sobre una base atrasada 12 h es la forma más convincente de mentir.
- **El zoom y el paneo sobreviven al refresco.** El mapa se redibuja con
  `Plotly.react` y un encuadre pegajoso, no con `newPlot`. Ya **no** hay pausa
  por hover: la justificación de esa pausa ("redibujar destruye el nodo bajo el
  cursor") es cierta para `newPlot` y falsa para `react` — medido, el tooltip
  sobrevive y los `<path class="js-line">` son los mismos nodos. La pausa era lo
  contrario del tiempo real: congelaba el mapa justo cuando alguien lo miraba.
- Con la pestaña en segundo plano no consulta nada, y al volver pide de nuevo
  para no mostrar una foto vieja.
- Ante un error del servidor reintenta con backoff (5 → 10 → 20 → 40 → 60 s) y
  el cartel dice cuántos segundos faltan, contando de verdad.

### Por qué no guarda todos los mensajes

Una aeronave transmite varias veces por segundo y la mayoría de esos mensajes
repiten lo mismo. Guardar todo infla los archivos sin agregar información, así
que solo se escribe una fila cuando pasaron unos segundos **o** cuando cambió
algo relevante: 100 pies de altitud o 10 nudos de velocidad. Esos cambios son
justamente los momentos que interesan —despegue, aproximación, aterrizaje— y
nunca se descartan.

### Sobre la matrícula: `registration` puede salir vacía en el CSV crudo

La transmisión ADS-B cruda lleva el **código ICAO24** de la aeronave, no la
matrícula ya traducida — por eso esta columna del CSV suele salir vacía.
`icao24` identifica al avión de forma única igual, así que no se pierde
información: [aircraft_db.py](aircraft_db.py) (ver más abajo) traduce ese
ICAO24 a matrícula, tipo y aerolínea, y es lo que usa `detect_track_count.py`
para completar las columnas del dashboard. Si en cambio usás `--source json`
contra un decodificador que publique `aircraft.json`, ese formato suele traer
la matrícula ya resuelta directamente.

## Tipo de avión y aerolínea también por ADS-B, para comparar

El dashboard ya mostraba matrícula por imagen vs. por ADS-B, una al lado de la
otra. Ahora hace lo mismo con **tipo de avión** y **aerolínea**.

La diferencia importante: el protocolo ADS-B **no transmite ni el modelo ni
el nombre de la aerolínea** — solo el ICAO24, un identificador fijo por avión.
Para conseguir ambos, [aircraft_db.py](aircraft_db.py) descarga una vez el
registro público de [OpenSky Network](https://opensky-network.org/) (~520.000
aeronaves) y arma una base local: dado un ICAO24, busca ahí la matrícula, el
modelo y el operador. Es una consulta local, sin conexión ni límite de uso.

```bash
python aircraft_db.py --build              # una vez, ~90 MB
python aircraft_db.py --lookup e80456       # probar una consulta
```

`INSTALAR-Y-EJECUTAR.bat` ya la descarga sola si no está.

### Cobertura real, medida contra la flota argentina

No es pareja entre los dos campos. Sobre las 1415 aeronaves de la base con
matrícula `LV-` (Argentina):

| Campo | Cobertura |
|---|---|
| Tipo de avión | **100%** (1415/1415) |
| Aerolínea (`operator`) | **5%** (64/1415) |

El **tipo** es confiable: encontrado y verificado cruzado contra una
identificación visual ya hecha en este proyecto (`LV-GUB`, identificado a ojo
como Boeing 737-800 → la base dice `737NG 800/W`, típecódigo `B738` —
coincide). La **aerolínea** por esta vía va a salir vacía la mayoría de las
veces: para eso sigue siendo mejor el OCR/modelo de visión sobre la imagen,
que ya tenía mucha mejor tasa de acierto. El cruce por ADS-B queda como
verificación extra cuando aparece, no como fuente principal.

## Identificación por ADS-B (la matrícula que la cámara no puede leer)

Una cámara no siempre puede leer la matrícula: está pintada al costado del
fuselaje, así que un avión que muestra la nariz o la cola simplemente no la
tiene en la imagen. Lo mismo de noche, con lluvia, o con otro avión delante.
Son límites físicos, no de software.

Los aviones transmiten su identidad continuamente en 1090 MHz. Un receptor
RTL-SDR de ~USD 20 con `dump1090` la recibe, y ahí la identificación deja de
depender de leer la pintura.

```bash
python detect_track_count.py --source rtsp://camara/stream \
  --adsb --camera-lat -34.5589 --camera-lon -58.4164 \
  --line-start 960,0 --line-end 960,1080
```

**División de tareas:** la cámara establece **que** hubo una operación y
**cuándo**; el ADS-B dice **qué avión** era. Ninguna de las dos sustituye a la
otra, y por eso se guardan en columnas separadas: cuando discrepan, el
dashboard lo marca. Esa discrepancia es información.

### Por qué el emparejado no es "el más cercano en el tiempo"

En un momento de tráfico hay varios aviones a segundos de distancia, y elegir
por tiempo pegaría la matrícula equivocada. [match_adsb.py](match_adsb.py)
puntúa cada candidato por:

- **comportamiento** — en un aterrizaje el avión debe estar descendiendo o en
  pista; en un despegue, ascendiendo o acelerando. Es la señal más fuerte,
  porque es el mismo evento visto por otro sensor.
- **tiempo** — cuán cerca está su reporte del cruce.
- **distancia** — cuán cerca está de la cámara.

Y sigue la misma regla que el OCR: **una matrícula equivocada es peor que
ninguna**. Si dos aviones son igual de plausibles, el evento queda sin
identificar y lo dice, en vez de adivinar.

Probado con escenarios construidos ([test_adsb.py](test_adsb.py)): dos aviones
a segundos de distancia, la telemetría desempatando, un avión lejano, el
receptor caído, y uno que reporta muchas veces sin poder ganar por repetición.

```bash
python test_adsb.py           # no necesita receptor
python test_adsb_events.py    # aterrizajes/despegues y sus falsos positivos
python test_adsb_position.py  # la posición, de la radio a la tabla
python adsb.py --watch        # ver lo que se está recibiendo
```

[test_adsb_position.py](test_adsb_position.py) reproduce mensajes hex conocidos
y compara contra el valor exacto esperado, porque durante toda la primera etapa
del proyecto la posición salió `None` en el 100% de los mensajes por dos bugs
encadenados —el decodificador leía `decoded['lat']` cuando pyModeS emite
`'latitude'`, y los cargadores descartaban las columnas al releerlas— y
arreglar uno solo no cambiaba nada observable. Por eso se prueba la cadena
completa: hex → `Observation` → SQLite/CSV → resumen → cobertura.

### Qué hace falta

Un dongle **RTL-SDR** (RTL2832U + R820T2) y una antena para 1090 MHz. Después:

```bash
dump1090-fa --net       # o dump1090 --net
```

**No es literalmente el 100%:** los aviones que no transmiten ADS-B no
aparecen. Para tráfico comercial la cobertura debería ser casi completa, pero
eso hay que medirlo con el receptor puesto.

## 1. Calibrar la línea virtual

Sacá un frame de referencia con grilla de píxeles para elegir los puntos de la línea:

```bash
./.venv/Scripts/python.exe get_frame.py --source data/tu_video.mp4 --output output/sample_frame.png
```

Abrí `output/sample_frame.png`, elegí dos puntos que crucen la pista (perpendicular a la dirección de rodaje).

## 2. Correr el pipeline

```bash
./.venv/Scripts/python.exe detect_track_count.py \
  --source data/tu_video.mp4 \
  --line-start 0,540 --line-end 1920,540 \
  --output output/annotated.mp4 \
  --no-display
```

Salida:
- `output/annotated.mp4`: video anotado (boxes, tracker id, trace, línea con contadores in/out).
- `output/events.csv`: log de eventos (`frame`, `time_s`, `tracker_id`, `event` = landing/takeoff), un evento por track (sin duplicados).

## Nota sobre landing vs takeoff

`LineZone` de supervision distingue cruces "in" vs "out" según el vector normal de la línea (definido por el orden de `--line-start`/`--line-end`), no según semántica de aterrizaje/despegue. Corré una vez, mirá el video anotado, y si landing/takeoff salen invertidos, invertí start/end (o mirá qué lado corresponde a qué maniobra en la cámara de tu pista) y volvé a correr — no requiere tocar el código.

`LineZone` usa por defecto las 4 esquinas del bbox para decidir si un objeto cruzó — con un avión, que ocupa casi todo el cuadro, eso casi nunca dispara. `detect_track_count.py` ya lo configura con `triggering_anchors=(sv.Position.CENTER,)` para evitar ese problema.

## Persistencia + dashboard

Cada evento de cruce se guarda automáticamente en `runway_events.db` (SQLite, ver [db.py](db.py)) junto con un recorte del avión en `output/thumbnails/`. Para desactivar esto: `--no-db`.

Para ver el dashboard local:

```bash
./.venv/Scripts/python.exe -m uvicorn main:app --app-dir webapp --host 127.0.0.1 --port 8000
```

Abrí `http://localhost:8000`. La página muestra:

- **Contadores** de landings / takeoffs / total.
- **Reproductor del video con el etiquetado** (recuadros, track id, trace, línea y contadores dibujados).
- **Tabla de eventos** con thumbnail, tipo, hora, track id, fuente, matrícula y aerolínea. **Clic en cualquier fila salta el video a ese evento** (arranca 2s antes para que se vea el cruce).

Y tres páginas de ADS-B, que funcionan sin cámara ni video:

| Página | Para qué |
|---|---|
| `/adsb` | Grabar en vivo desde el dongle y ver lo que entra ahora |
| `/adsb/analisis` | Una fila por aeronave sobre todo lo grabado, y qué aporta cada campo |
| `/adsb/mapa` | Dónde estuvo cada aeronave: trayectorias, alcance real y aeropuertos |

Para que el video se vea en el navegador hay que convertirlo primero: supervision escribe `mp4v`, que los navegadores no reproducen.

```bash
./.venv/Scripts/python.exe transcode_web.py
```

Eso deja copias H.264 en `output/web/`, que es de donde las sirve el dashboard.

## Acelerar con la GPU (Intel iGPU vía OpenVINO)

La GPU de esta máquina es una **Intel Iris Xe integrada**, así que CUDA/PyTorch-GPU no aplica. OpenVINO sí la usa, para **inferencia**:

```bash
./.venv/Scripts/python.exe export_openvino.py --weights yolov8n.pt

./.venv/Scripts/python.exe detect_track_count.py --source data/tu_video.mp4 \
  --model yolov8n_openvino_model/ --device intel:gpu \
  --line-start 320,0 --line-end 320,360 --no-display
```

Medido en esta máquina, solo la detección: **6.9 FPS en CPU → 40.2 FPS en la iGPU (~6x)**. Sobre el pipeline completo la mejora es bastante menor (121s → 105s en un clip de 90s), porque una vez acelerada la inferencia el cuello de botella pasa a ser el decode/encode de video y la anotación, que siguen en CPU.

**El entrenamiento sigue en CPU:** OpenVINO es solo inferencia, y PyTorch no puede entrenar en esta iGPU. Para entrenar rápido hace falta una GPU NVIDIA con el torch de CUDA.

## Ahorrar cómputo: gate de movimiento

En una cámara 24/7 la enorme mayoría de los frames no tienen nada pasando. El gate ([motion.py](motion.py)) compara el frame con el anterior (en gris y reducido, cuesta fracciones de milisegundo) y **saltea el detector cuando la escena está quieta**:

```bash
./.venv/Scripts/python.exe detect_track_count.py --source data/tu_video.mp4 \
  --motion-threshold 0.002 --line-start 250,100 --line-end 250,360 --no-display
```

También está `--detect-every N` para correr el detector 1 de cada N frames.

**Detalle de diseño importante:** el gate **nunca saltea frames mientras hay un avión trackeado**. La primera versión sí lo hacía y perdía eventos (8 landings/5 takeoffs → 7/3), porque cortar las actualizaciones del tracker en pleno paso rompe la continuidad del track y se pierden cruces. Con el guard de tracks activos, los conteos quedan **idénticos al baseline** y aun así se ahorra cómputo.

Medido en el clip de prueba (compilado con cámara en movimiento casi todo el tiempo, o sea el peor caso para el gate):

| Configuración | Tiempo | Frames con detector | Conteo |
|---|---|---|---|
| CPU, sin gate | 121s | 2701/2701 | 8 / 5 |
| iGPU, sin gate | 105s | 2701/2701 | 8 / 5 |
| iGPU + gate | 98s | 1744/2701 (**-35%**) | 8 / 5 |

En una cámara fija apuntando a una pista real, con largos períodos sin actividad, el ahorro va a ser **mucho mayor** que ese 35% — este video no tiene un solo momento verdaderamente quieto.

## Filtros anti-falsos-positivos

Solo cuentan los aviones **en movimiento cruzando la línea**. Un avión estacionado, o uno que rueda paralelo a la línea, no debe contar — pero el detector hace temblar su bounding box y eso disparaba cruces falsos. Cuatro filtros lo resuelven:

| Flag | Default | Qué descarta |
|---|---|---|
| `--min-speed` | 40 px/s | Aviones quietos (estacionados en plataforma) |
| `--line-margin` | 20 px | Banda de histéresis: el track tiene que despegarse de la línea para que cuente el cambio de lado |
| `--track-cooldown` | 5 s | Cruces del mismo track que se revierten en segundos — físicamente imposible |
| `--scene-cut` | 0.5 | Cortes de cámara: resetea el tracker para que un ID no salte a otro avión |

Medido sobre el clip de prueba (compilado de spotting, el peor caso):

```
sin filtros:   17 eventos  (incluía pares takeoff+landing del mismo track a 0.03s)
con filtros:    8 eventos  (ningún par imposible)
```

En una cámara fija de pista, `--scene-cut` no va a activarse nunca (no hay cortes) y los otros tres siguen siendo útiles contra el temblor del detector.

**Nota:** los conteos de este video de prueba no son verdad absoluta ni siquiera después de filtrar — es un compilado editado con saltos de cámara. Los filtros están validados contra artefactos identificables (pares imposibles), no contra un conteo real verificado a mano.

## Identificación de matrícula y aerolínea (OCR)

Pasá `--ocr` a `detect_track_count.py` para que, en cada evento, corra OCR sobre el recorte del avión y complete las columnas `registration`/`airline` de la DB:

```bash
./.venv/Scripts/python.exe detect_track_count.py --source data/tu_video.mp4 \
  --line-start 320,0 --line-end 320,360 --ocr --no-display
```

Cómo funciona ([ocr.py](ocr.py)): EasyOCR lee el texto del recorte; la matrícula sale por patrón regex (formato argentino `LV-XXX` / `LQ-XXX`) y la aerolínea por coincidencia contra una lista de nombres conocidos. No hay modelo entrenado acá — es lectura de texto + matching.

### La resolución es el factor decisivo

Comparación directa, **mismo avión, mismo frame**, una versión a 640x360 y otra a 1920x1080:

| | 640x360 | 1920x1080 |
|---|---|---|
| Texto detectado | `{de`, `"9 J`, `7` (basura) | `libertad de volar`, `LV-LKK` |
| Aerolínea | ninguna | **Flybondi** ✅ |
| Matrícula | ninguna | detectada pero **mal leída** |

A 360p no hay nada que hacer: las letras miden pocos píxeles y el reconocedor devuelve ruido. A 1080p la aerolínea sale bien.

### La matrícula: EasyOCR no puede, un modelo de visión sí

A 1080p el OCR clásico encuentra el patrón de matrícula pero **confunde caracteres**: leyó `LV-LKK` y `LV-HKM` en un avión que es `LV-HKN`. Probé escalas 6x y 10x, CLAHE, Otsu, lista blanca de caracteres y votación entre 20 cuadros. Ninguna acertó, y la votación **empeoró** las cosas: el error no es aleatorio sino un sesgo constante (`N` leída como `M` en la mayoría de los cuadros), y promediar no corrige un sesgo.

**La resolución nunca fue el problema.** Un recorte de 150x45 px ampliado se lee sin esfuerzo a simple vista (ver `output/matricula_zoom8.png`). El limitante era el reconocedor.

La solución fue [vlm_ocr.py](vlm_ocr.py): un modelo de visión-lenguaje lee con contexto en vez de carácter por carácter. Medido contra respuestas verificadas a ojo ([validate_vlm.py](validate_vlm.py)):

| Método | Matrículas correctas |
|---|---|
| EasyOCR | 0 / 3 |
| Modelo de visión (NVIDIA Nemotron, gratis en OpenRouter) | **6 / 6** |

### Por qué hay tres redes de seguridad y no una

Un modelo de lenguaje puede devolver una matrícula plausible con total seguridad para una imagen ilegible, y no trae score de confianza para filtrarla. Sobre los recortes reales del pipeline eso ocurrió: leyó `LV-4KN` donde decía `LV-HKN`. Por eso:

1. **Consenso entre dos modelos.** Solo se marca como confirmada si ambos leen lo mismo; si difieren, se muestra en gris con `?`.
2. **Validación de formato.** Las matrículas argentinas son `LV`/`LQ` más **tres letras**. Esto atrapó un `LV-600` en el que *ambos modelos coincidieron* — el consenso reduce errores, no los elimina.
3. **Normalización de aerolíneas.** El modelo devuelve texto libre (`Aerolineas Argentinas`, `Aeroline Argentinas`), que se mapea a un nombre canónico antes de guardar.

Una cuarta red posible, no implementada: contrastar contra el registro real de matrículas argentinas.

**Límite de uso:** los modelos gratuitos tienen cupo diario (`free-models-per-day`). Al agotarse, `backfill_ocr.py --vlm` retoma los pendientes al día siguiente sin repetir lo hecho.

### Otras limitaciones

- Los logos muy estilizados no se leen (el wordmark cursivo de flybondi nunca sale). Por eso la lista de [ocr.py](ocr.py) incluye **eslóganes** además de nombres: "libertad de volar" está pintado mucho más grande que el logo y sobrevive a distancias donde el logo no.
- Los aviones lejanos en aproximación no dan ningún texto, a ninguna resolución.
- El OCR corre sobre el **mejor recorte** de cada avión (el frame donde se ve más grande), no sobre el del momento del cruce, que suele ser el peor.
- **Lo que más influye no es la resolución sino cuánto dura el avión en cuadro.** Sobre 83 minutos de material procesado: el video con 2 cortes de cámara identificó el 59% de los aviones; el de 142 cortes, apenas el 12%. Mismo sistema, misma resolución. Una cámara fija no tiene cortes, así que ese 59% es el piso esperable, no el techo.

## Arranque rápido (Windows)

Doble clic en **`dashboard.bat`**: levanta el servidor y abre el navegador solo. El servidor queda en una ventana minimizada; cerrala para detenerlo.

## Revisar y corregir etiquetas

En `http://localhost:8000/label` hay una herramienta para revisar el dataset auto-etiquetado:

- **Arrastrar** sobre la imagen dibuja una caja nueva.
- **Clic dentro de una caja** la borra.
- Flechas ←/→ para navegar, `S` guardar, `Z` deshacer.

Guarda directo sobre los `.txt` YOLO del dataset, así que después entrenás normal con `train.py`. Esta es la única forma de que el modelo aprenda lo que el modelo base hace mal: mientras las etiquetas las genere el propio modelo, no puede superarse a sí mismo.

## Entrenar un detector propio

El detector COCO base confunde/pierde aviones en tomas difíciles. Para afinarlo con tu propio footage, sin etiquetar a mano, el flujo es auto-labeling + fine-tune:

```bash
# 1. Genera dataset YOLO auto-etiquetado (el modelo COCO actúa de "profesor")
./.venv/Scripts/python.exe build_dataset.py --source data/aeroparque_full.mp4 --stride 15 --min-confidence 0.5

# 2. Fine-tune
./.venv/Scripts/python.exe train.py --data dataset/data.yaml --epochs 30

# 3. Usar los pesos entrenados (ojo: modelo de 1 clase, va con --class-id 0)
./.venv/Scripts/python.exe detect_track_count.py --source data/tu_video.mp4 \
  --model runs/detect/runway/weights/best.pt --class-id 0 --line-start 320,0 --line-end 320,360 --no-display
```

**Qué esperar de esto:** el auto-labeling solo conserva detecciones de alta confianza, así que el modelo aprende de los casos donde el profesor ya acertaba. Eso lo hace más rápido y más especializado en *esta* pista/ángulo, pero **no le enseña a detectar los aviones que el modelo base ya no veía** (no puede superar a su profesor en esos casos). Para ganar ahí hace falta etiquetado manual real de los frames difíciles — Roboflow sirve para eso.

Sobre el video de Aeroparque completo (23:31, stride 15, conf 0.5) el dataset generado fue de **1846 imágenes de train + 462 de val**.

### Resultado del entrenamiento

20 épocas a 416px en CPU (~85 min). Pesos en `runs/detect/runway/weights/best.pt`:

| Métrica | Valor |
|---|---|
| Precision | 0.953 |
| Recall | 0.931 |
| mAP50 | 0.978 |
| mAP50-95 | 0.831 |

**Cuidado al interpretar estos números.** El conjunto de validación son las *mismas pseudo-etiquetas* generadas por el modelo profesor, así que un mAP50 de 0.978 significa **"coincide 97.8% con el modelo base"**, no "acierta el 97.8% de los aviones reales". Un modelo que replicara perfectamente los errores del profesor sacaría 1.000 acá. Para medir precisión real hace falta un conjunto de validación etiquetado a mano — para eso está la herramienta en `/label`.

**Nota de hardware:** el `torch` instalado es build CPU-only, así que el entrenamiento corre en CPU y es lento (por eso los defaults de arriba usan `--imgsz 416` y pocas épocas). Si tenés GPU NVIDIA, instalá el torch con CUDA y pasá `--device 0` para acelerarlo un orden de magnitud.

## Estado actual

Esta versión procesa **archivos de video**: le pasás un `.mp4`, lo procesa entero, y los resultados quedan en la base y el dashboard.

Hay dos fases que conviene no confundir:

- **Entrenar** (una vez, opcional): `build_dataset.py` → corregir en `/label` → `train.py`. No se repite por cada video.
- **Operar** (cada vez): `detect_track_count.py` sobre un video → eventos en SQLite → dashboard.

## Objetivo final: cámara en vivo 24/7

El destino del proyecto es una **cámara fija transmitiendo 24/7**, procesada en vivo, mostrando el video etiquetado en una pantalla y registrando cada aterrizaje/despegue con aerolínea y matrícula.

Lo que falta para llegar ahí:

1. **Entrada en vivo:** hoy `--source` es un archivo. Falta soportar RTSP/USB con reconexión automática ante cortes.
2. **Streaming al navegador:** el dashboard hoy reproduce un archivo ya procesado. Para vivo hace falta transmitir los frames anotados (MJPEG o WebRTC) y que los eventos aparezcan solos, sin recargar.
3. **Proceso permanente:** correr como servicio de Windows en vez de un comando puntual, con rotación de video/thumbnails para no llenar el disco.
4. **OCR asincrónico:** leer matrícula es lento; en vivo tiene que correr fuera del loop de tiempo real, sobre los recortes ya guardados (ver [backfill_ocr.py](backfill_ocr.py), que ya hace exactamente eso en diferido).

**Viabilidad medida:** el pipeline completo corrió a 27.6 FPS sobre video de 30 FPS con la iGPU, *incluyendo* escribir el video anotado a disco (que en vivo no haría falta). Sumado al gate de movimiento, una cámara debería entrar cómoda en tiempo real. Falta medirlo con un stream real.

## Otros pendientes

- Reducir el "churn" de tracker IDs cerca de la línea: cuando un avión se ocluye o pasa cerca de otros, ByteTrack le cambia el ID y eso infla el conteo. Se ataca ajustando `track_activation_threshold`/`lost_track_buffer`, o usando una zona (polígono) en vez de una línea.
- Clasificar aerolínea por librea/logo con un modelo de clasificación, para los casos donde el OCR no puede (logos cursivos como el de flybondi).
- Leer matrícula de verdad: necesita más resolución que 640x360, o zoom óptico sobre la zona de cola.
