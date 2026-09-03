# Usar la antena: paso a paso

Para salir a medir con el RTL-SDR. Escrito para seguirlo con el celular en la
mano mientras armás.

La configuración de Aeroparque ya está hecha: no hay que escribir coordenadas ni
acordarse de variables. Es enchufar, doble clic y mirar.

---

## Antes de salir

**Llevá:** el dongle RTL-SDR, la antena con su cable, la notebook cargada, y algo
para apoyar la antena lo más alto que se pueda.

**Cerrá el servidor que tengas abierto.** Si dejás uno corriendo de antes, sigue
usando la configuración vieja y mide **todas las distancias desde San Isidro**
sin avisarte. El `.bat` lo detecta y te frena, pero es más rápido cerrarlo ahora:
buscá la ventana que dice *"Runway Dashboard - servidor"* y cerrala.

---

## En el lugar

### 1. Dónde poner la antena

Lo que decide si esto funciona **no es la altura, es qué tenés adelante.**

Desde el punto de medición la pista de Aeroparque queda hacia el **este-sudeste,
en el sector 104°–117°**. Esa es la dirección que tiene que estar despejada.

| a qué altura vuela el avión | ángulo desde donde estás |
|---|---|
| en la pista | 0,24° |
| 500 ft | 7,55° |
| 1000 ft | 14,84° |

Y lo que tapa un edificio de 30 m, según a qué distancia esté de vos:

| el edificio está a | tapa hasta |
|---|---|
| 100 m | 16,7° |
| 300 m | 5,71° |
| 1 km | 1,72° |

O sea: para ver **aproximaciones y despegues** (500–1000 ft) alcanza con no tener
un edificio pegado. Para ver **aviones rodando en la pista**, que es lo que nunca
se logró, hace falta que no haya prácticamente nada entre vos y la pista.

La altura de la antena casi no importa acá: con la antena a 1 m del piso el
horizonte al suelo ya son 4,1 km, y la pista está a 1,15 km. Ponela lo más alto
que puedas, pero no pierdas tiempo: **importa más despejar el este-sudeste que
subir un metro.**

### 2. Enchufar

Conectá el dongle **antes** de arrancar el programa. Si lo enchufás después, la
página te lo va a decir, pero es un paso de más.

### 3. Arrancar

Doble clic en **`MEDIR-EN-AEROPARQUE.bat`**.

Se abre solo el navegador en la página de ADS-B en vivo. La ventana negra que
queda minimizada es el servidor: **no la cierres** mientras grabás.

### 4. Darle Iniciar

La fuente ya viene elegida en **"Dongle RTL-SDR + nivel de señal (IQ crudo)"**.
No la cambies: es la única que mide el nivel de señal, y sin eso no se puede
decidir la ganancia estando tan cerca de la pista.

Apretá **Iniciar**.

---

## Cómo saber si está andando

**A los 30 segundos tendrían que aparecer aeronaves.** Desde San Isidro entraban
6 a 8 en 70 segundos; desde acá, más cerca del tráfico, debería ser igual o
mejor.

Mirá el encabezado de la página:

| dice | significa |
|---|---|
| **grabando** (punto verde) | anda bien |
| **la grabación está activa pero NO entra nada** | hay un problema, el texto rojo de abajo dice cuál |
| **esperando el dongle** | no está enchufado |
| **detenido** | no le diste Iniciar |

Si aparece el segundo, leé el mensaje rojo: dice qué hacer.

---

## Ajustar la ganancia

**Ya está medida: dejala en `49.6`.** Esta guía decía antes que pegado a la pista
convenía bajarla. Se midió el 2026-09-03 y es al revés:

| ganancia | mensajes verificados en 30 s | aeronaves |
|---|---|---|
| **49,6** | **129** | **6** |
| `auto` | 67 | 5 |
| 30 | 34 | 4 |
| 20 | 24 | 2 |

El máximo del R820T da **3,8× más** que 30 y casi el doble que `auto`. La teoría
de la saturación es real, pero en este lugar no se cumple, y adiviné mal.

**Te va a aparecer un cartel que dice "llega tan fuerte que puede estar
saturando". No le hagas caso** — o mejor, hacé lo que él mismo pide: comparar el
número. Salió en las cuatro mediciones, hasta con ganancia 20, porque mira el
pico y no la cantidad de mensajes decodificados.

Y no uses la mediana en dBFS para decidir: **empeora al subir la ganancia**
(−19,3 con 49,6 contra −13,4 con 20) y aun así 49,6 decodifica cinco veces más.
No es que la señal sea peor — con mucha ganancia entran también los aviones
lejanos y débiles, que bajan la mediana mientras suben el total.

Si algún día hay que volver a decidirlo, con otra antena u otra ubicación, es
con un comando y no a ojo:

```bash
.venv\Scripts\python.exe adsb_iq.py --medir 30
```

Cuenta **sólo los mensajes con CRC verificable**, que es lo único que prueba que
la antena recibe. El total de mensajes no mide nada: en la torre de YPF el
sistema informaba 34 684 mensajes y 101 "aeronaves" con **cero** CRC válido. Era
ruido al 100%.

---

## Qué mirar, y qué estamos buscando hoy

Tres páginas, todas se actualizan solas cada 5 segundos. Podés hacer zoom y
moverte por el mapa sin que el refresco te lo resetee.

**`/adsb`** — lo que entra ahora, aeronave por aeronave, con el nivel de señal.

**`/adsb/mapa`** — dónde estuvo todo lo que se escuchó. El **círculo verde
punteado** es hasta dónde se puede ver un avión *en la pista*. Con esta
configuración son 7,1 km, así que la pista de Aeroparque tiene que quedar
adentro. Si querés ver la grabación vieja, el mapa dibuja las últimas 3 horas y
hay un botón para pedir todo.

**`/aeropuerto/mapa`** — sólo lo que operó en Aeroparque, con aterrizajes,
despegues y sobrevuelos contados.

### Lo que nunca se pudo, y hoy se puede intentar

1. **Ver aviones por debajo de 1500 ft sobre el campo.** Desde San Isidro lo más
   bajo fueron **2134 ft**, nunca menos. Si hoy aparecen lecturas de 500 o 200
   ft, la mudanza funcionó.

2. **Decodificar una posición en tierra.** Nunca pasó, ni una vez. Si aparece,
   es la primera del proyecto. En `/adsb` el contador de "regla de superficie"
   dice *sin ejercitar* justamente porque nunca corrió.

3. **Contar un aterrizaje de verdad.** Hasta ahora el sistema cuenta 0
   aterrizajes en Aeroparque y lo dice explícitamente, porque no se ve el tramo
   que lo prueba.

Dejalo grabando el mayor tiempo posible. Cuanto más tiempo, más operaciones
entran y mejor se mide el alcance real desde este punto.

---

## Si algo falla

| lo que ves | qué pasa | qué hacer |
|---|---|---|
| El `.bat` dice que el puerto 8000 está ocupado | quedó un servidor viejo, con la configuración vieja | cerrá la ventana del servidor y volvé a ejecutar el `.bat` |
| *"la grabación está activa pero NO entra nada"* + `usb_open error` | otro proceso tiene el dongle, **o** falta el driver | cerrá cualquier otra ventana del servidor o `rtl_sdr.exe`. Si no hay ninguna, corré `INSTALAR-ADSB.bat` e instalá WinUSB con Zadig |
| *"esperando el dongle"* | no está enchufado o no lo reconoce | reenchufalo, probá otro puerto USB, y dale Iniciar de nuevo |
| Entran aeronaves pero **ninguna** posición | normal al principio | en vuelo hacen falta varias tramas CPR: la primera posición aparece recién en el 6.º mensaje de esa aeronave. Esperá |
| El mapa dice que no hay posiciones en la ventana de 3 h | la grabación estuvo parada más de 3 h | es sólo un recorte por antigüedad, no falla de recepción. El botón *"Ver la grabación entera"* las dibuja |
| Muchas aeronaves pero pocas con matrícula | esperable | la matrícula no viaja por radio: sale de cruzar el ICAO24 contra el registro de OpenSky, y el 60% del tráfico local no está ahí. El **vuelo** (ARG1403) se ve casi siempre; el **avión** (LV-FVN) no |

---

## Al terminar

Apretá **Detener** en `/adsb`, y después cerrá la ventana del servidor.

Los datos quedan en dos lados: `adsb_log.db` y un CSV por día en
`output/adsb/`. El CSV se puede abrir en Excel incluso mientras grabás.

---

## Una cosa a tener en cuenta después

La base **no guarda desde dónde se recibió cada fila**. Al mudar la antena, las
11 823 filas viejas grabadas en San Isidro pasan a medirse desde Aeroparque. El
corrimiento es chico —la mediana se mueve 6% y el máximo 1%, porque los dos
puntos están a 20 km— pero está, y conviene saberlo al comparar el "antes" con
el "después". Está anotado como pendiente en `ESTADO.md`.

Lo más limpio para comparar es mirar **sólo lo grabado hoy**, que es todo desde
la ubicación nueva.
