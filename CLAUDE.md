# Instrucciones para trabajar en este repo

## Antes de tocar nada: leer ESTADO.md

`ESTADO.md` es el traspaso entre sesiones. Dice dónde quedó todo, qué está sin
resolver y qué decisiones ya se tomaron **con la medición que las respalda**, para
no rediscutirlas sin datos nuevos.

Leerlo primero ahorra repetir errores ya cometidos. El más fácil de cometer es
confundir **dónde está la antena** con el material de video, que se llama
Aeroparque por todos lados:

- **Desde el 2026-08-23 la antena está en Aeroparque**, a 1153 m del umbral 13.
- **Todo el histórico anterior se grabó desde San Isidro**, a 13,3 km. Esos datos
  siguen medidos desde ahí y no se pueden reinterpretar.
- **San Isidro sigue siendo el valor por defecto del código.** Un proceso que
  arranque sin `ADSB_RECEIVER` mide desde el lugar equivocado sin fallar. Por eso
  los lanzadores llaman a `ubicacion-antena.bat`, único lugar donde se define, y
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
cd C:\Users\nogue\OneDrive\Desktop\CLAUDE\runway-video-analytics && .venv\Scripts\python.exe test_adsb.py && .venv\Scripts\python.exe test_adsb_events.py && .venv\Scripts\python.exe test_adsb_position.py && .venv\Scripts\python.exe test_adsb_incremental.py
```

Para lo de ADS-B, verificar con **replay de mensajes hex** y no esperando que
pase un avión. Hay vectores conocidos: el par CPR clásico
`8D40621D58C382D690C8AC2863A7` / `8D40621D58C386435CC412692AD6` decodifica a
`52.2572021484375, 3.91937255859375` a partir del 6.º mensaje.

**El dongle es exclusivo**: un solo proceso puede tomarlo. Antes de correr
`rtl_sdr.exe` o `rtl_adsb.exe`, verificar que la grabación de la webapp no esté
corriendo, o pararla desde `/adsb`.

**La base commitea cada 1,0 s de reloj** (antes cada 50 filas, que sin cota
temporal dejaba filas invisibles hasta **759,5 s — 12,7 min — medidos** con el
grabador funcionando normal). Abre en WAL. El retraso real está siempre a la vista: `lag_s` en los dos mapas y
`pending` / `seconds_since_commit` / `journal_mode` en `/api/adsb/status`. El
cambio entra en el **próximo arranque del grabador**: una grabación ya en curso
sigue con el comportamiento viejo. El CSV de `output/adsb/` se vuelca fila por
fila y nunca tuvo el problema.
