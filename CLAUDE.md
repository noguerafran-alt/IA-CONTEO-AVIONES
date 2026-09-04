# Instrucciones para trabajar en este repo

## Antes de tocar nada: leer ESTADO.md

`ESTADO.md` es el traspaso entre sesiones. Dice dónde quedó todo, qué está sin
resolver y qué decisiones ya se tomaron **con la medición que las respalda**, para
no rediscutirlas sin datos nuevos.

Leerlo primero ahorra repetir errores ya cometidos. El más fácil de cometer es
confundir **dónde está la antena** con el material de video, que se llama
Aeroparque por todos lados:

- **Desde el 2026-09-03 (tarde) la antena está al costado de la pista**, a 266 m
  del eje y a mitad de campo, a ~6 m de altura y detrás de un doble vidrio.
  Preset `aeroparque-pista`.
- Entre el **2026-08-23 y el 2026-09-03** estuvo a 1153 m del umbral 13, preset
  `aeroparque`. Ese preset se deja definido porque es desde donde se grabó ese
  tramo del histórico.
- **Todo el histórico anterior se grabó desde San Isidro**, a 13,3 km. Esos datos
  siguen medidos desde ahí y no se pueden reinterpretar.
- **San Isidro sigue siendo el valor por defecto del código.** Un proceso que
  arranque sin `ADSB_RECEIVER` mide desde el lugar equivocado sin fallar. Por eso
  los lanzadores llaman a `configuracion.bat`, único lugar donde se define, y
  las cinco páginas muestran la franja de `webapp/static/franja_receptor.js`.

## Al terminar un cambio: actualizar ESTADO.md

**Esto no es opcional.** El proyecto se trabaja desde varias sesiones y varios
usuarios de la misma máquina, y las notas de una sesión no las ve la siguiente.
Un cambio que no quedó en `ESTADO.md` es un cambio que la próxima sesión va a
tener que redescubrir, o peor, va a deshacer sin saberlo.

Qué actualizar, según lo que se hizo:

- **Página, endpoint o módulo nuevo** → agregarlo a la tabla correspondiente.
- **Variable de entorno nueva** → a la tabla de variables, y a `.env.example`.
- **Una decisión tomada con datos** → a "Decisiones ya tomadas", **con el número
  que la respalda**. Sin el número no sirve: la próxima sesión no puede saber si
  sigue siendo cierta.
- **Una hipótesis que se probó y falló** → anotarla como descartada, también con
  el número. Vale tanto como una que funcionó: evita que alguien la reintente.
- **Un bug que costó encontrar** → a "Un bug ya arreglado que vale recordar", si
  es del tipo que se vuelve a cometer.
- **Algo que quedó a medias** → a "Ideas que quedaron sin hacer", dicho como lo
  que es, sin adornar.

Va en el mismo commit que el cambio, no en uno aparte: separarlos es como se
termina con un `ESTADO.md` que describe un sistema que ya no existe.

## El número de vuelo se congela en la operación

**No uses `ident.callsign` para decir qué vuelo fue una pierna.** Ese campo es
*el último distintivo escuchado de esa dirección* en toda la base, sin noción de
cuándo. En cuanto el avión vuelve a volar pasa a ser el de la **pierna
siguiente** y le pisa el número a la operación anterior. Costó 16 de 41
operaciones mal contra los listados oficiales del 03/09, y 14 de 17 en
aterrizajes: es el caso peor porque después de aterrizar el avión casi siempre
despega otra vez.

Lo que vale es `Operacion.callsign`, que sale de `distintivo_en()`: el distintivo
que la aeronave estaba transmitiendo **en el instante de la operación**, tomado
de tramos fechados. Una pierna posterior escribe su propio tramo en vez de pisar
el anterior.

`ident.callsign` **sigue siendo correcto** para lo que no cambia entre piernas:
matrícula y operador. Ahí la decisión del 2026-08 —resolver la identidad sobre el
historial completo— sigue en pie.

**El distintivo NO viaja con posición.** Medido: de **1261 mensajes con
distintivo en la base, 0 tienen latitud**. Viene en mensajes de identificación,
que no llevan lat/lon. De ahí salen dos reglas que parecen arbitrarias y no lo
son:

- `_anotar_distintivo()` se llama **antes** del filtro de posición de
  `acumular_en_cilindro()`.
- `LectorIncremental._absorber_cilindro()` se llama **antes** del filtro de
  posición, no al final.

**Si alguien mueve cualquiera de las dos después del filtro, el sistema se rompe
en silencio** y ningún test lo atrapa: los datos sintéticos traen distintivo y
posición en la misma observación, mientras que los reales nunca. El síntoma sería
la página en vivo publicando un número y la descarga otro.

**Un distintivo con `#` se descarta.** Es lo que deja el decodificador cuando no
pudo resolver el carácter, y está guardado así en la base para tres direcciones.
`########` llegó a publicarse como número de vuelo en el Excel del 03/09.

## Esta tabla no sirve para contar

El número de vuelo ya es confiable; **el conteo no**.

**El denominador hay que armarlo con cuidado, o el número miente para el lado
fácil.** La grabación no es continua: el 03/09, dentro de la ventana 10:09–16:42,
hay **56 minutos sin un solo mensaje**, uno de ellos de 33,7 min. Comparar contra
todas las operaciones de la ventana mete en el denominador tiempo en el que no
grabamos, y eso ya se hizo mal una vez (daba 49% y 22%).

Contando solo lo que ocurrió **mientras la antena grababa**: **56–58% de las
partidas y 25–26% de los arribos.** El rango depende de dónde se corte un hueco,
3 o 5 min; más allá de eso no se mueve.

**Y ese número es el techo, no el valor.** Un hueco sin mensajes no distingue
«la antena estaba apagada» de «estaba prendida y sorda». Se le da el beneficio de
la duda al sistema porque es lo que más lo favorece; si en algún hueco estaba
corriendo, esas operaciones son pérdidas reales y el porcentaje baja.

Antes de publicar un total, un ranking o un market share desde acá, decí de qué
porcentaje estás hablando. Un market share calculado sobre el 26% de los arribos
no es un market share.

## Cómo se escribe acá

**Comentarios y docstrings en español sin tildes** (en la interfaz sí van, es
texto para leer). Explican el **por qué**, no el qué: el qué ya lo dice el
código. Cuando hay un número medido, va en el comentario — es lo que permite
auditar la decisión después.

**Nada se descarta en silencio.** Si el sistema filtra, rechaza o recorta algo,
el número tiene que estar a la vista, y un cero tiene que distinguirse de "nunca
se evaluó". Este principio ya atrapó varios bugs reales.

**No inventar datos para que una pantalla se vea mejor.** Si no hay posiciones,
la página dice por qué está vacía. Si no se puede afirmar que un avión aterrizó,
se cuenta aparte y no entre los aterrizajes. Un número lindo pero no auditable
es peor que un cero explicado.

## Verificar ejecutando, no leyendo

Los cuatro archivos de test tienen que pasar antes de commitear:

```bash
cd "C:\Users\nogue\OneDrive\Desktop\CLAUDE\RADAR YPF" && .venv\Scripts\python.exe test_adsb.py && .venv\Scripts\python.exe test_adsb_events.py && .venv\Scripts\python.exe test_adsb_position.py && .venv\Scripts\python.exe test_adsb_incremental.py
```

**Correrlos también con `ADSB_RECEIVER=aeroparque`**, que es la configuración en
la que el sistema realmente opera. Nueve comprobaciones de distancia tenían
escritos los kilómetros vistos desde San Isidro y pasaban solo con el valor por
defecto: un test que no se corre en la configuración de producción no está
cubriendo la producción. Las distancias esperadas ahora se **calculan** con el
mismo `distance_km` que usa el código, así que lo que se afirma es la relación y
no un número.

**Hay una verdad externa, y es la única: los listados de Aeropuertos Argentina.**
`aeropuertosargentina.com/es/vuelos?movtp=arribos|partidas&idarpt=Aeroparque,AEP&fecha=DD-MM-AAAA`
publica hora real de aterrizaje y despegue por vuelo. Es lo que destapó el bug
del distintivo, y ninguna comprobación interna lo habría encontrado: el sistema
era **consistente consigo mismo** y estaba equivocado.

Dos cosas al usarlo. El texto de las filas viene **rasterizado**, así que hay que
transcribirlo (se renderiza la página con `pypdfium2` y se lee). Y el cruce se
hace **por hora, no por número de vuelo**: emparejar por número da por buena
justamente la columna que se quiere auditar. Con ±3 min alcanza — el desvío real
medido es de 0,5 min en promedio y 2 min como máximo.

**Cuando toques el acumulador, comprobá que las dos rutas no divergen.** La de
siempre (`aeropuerto.informe()`) y la incremental (`LectorIncremental`) tienen
que dar las mismas operaciones con el mismo distintivo y la misma procedencia,
tanto de una carga como avanzando por lotes. Sobre la base real son 141
operaciones idénticas. Es la comprobación que atrapó que `_absorber_cilindro()`
se llamaba tarde.

Para lo de ADS-B, verificar con **replay de mensajes hex** y no esperando que
pase un avión. Hay vectores conocidos: el par CPR clásico
`8D40621D58C382D690C8AC2863A7` / `8D40621D58C386435CC412692AD6` decodifica a
`52.2572021484375, 3.91937255859375` a partir del 6.º mensaje.

**Para saber si la antena recibe, contar SOLO el CRC verificable.** `python
adsb_iq.py --medir 30` lo hace y contesta sí o no. El conteo total de mensajes no
mide señal: medido el 2026-08-24 en la torre de YPF, con AGC el sistema reportaba
34 684 mensajes y 101 "aeronaves" —todas con 2 mensajes, una con altitud de
110 500 ft— y había **cero** DF17/18 con CRC válido. Era ruido al 100%. Solo
DF17/18 lleva un CRC comprobable contra un síndrome conocido; los formatos cortos
llevan la paridad XOR-eada con la dirección y el ruido los produce a montones.

**No elegir la ganancia de memoria.** Más no es mejor y menos tampoco: en la torre,
49,6 y 20,7 dieron los dos cero. `auto` es AGC y suele encontrar el punto solo. Se
decide midiendo con `--medir`, comparando el número de verificados.

**El dongle es exclusivo**: un solo proceso puede tomarlo. Antes de correr
`rtl_sdr.exe` o `rtl_adsb.exe`, verificar que la grabación de la webapp no esté
corriendo, o pararla desde `/adsb`.

**El CSV se ve mal en Excel en español.** `-34.6635411149364` aparece como
`-34.663.541.114.936.400` porque el punto es separador de miles en esa
configuración regional. El archivo está bien; hay que importarlo declarando el
punto como separador decimal. Y `registration` está vacía **por diseño**: la
matrícula no viaja por radio, se resuelve al leer cruzando el ICAO24 contra el
registro. Congelarla en la fila la dejaría mal para siempre — el registro completo
de OpenSky resolvió 39 matrículas más que el anterior.

**La base commitea cada 1,0 s de reloj** (antes cada 50 filas, que sin cota
temporal dejaba filas invisibles hasta **759,5 s — 12,7 min — medidos** con el
grabador funcionando normal). Abre en WAL. El retraso real está siempre a la vista: `lag_s` en los dos mapas y
`pending` / `seconds_since_commit` / `journal_mode` en `/api/adsb/status`. El
cambio entra en el **próximo arranque del grabador**: una grabación ya en curso
sigue con el comportamiento viejo. El CSV de `output/adsb/` se vuelca fila por
fila y nunca tuvo el problema.
