"""Verificar que la posicion sobrevive todo el camino, de la radio a la tabla.

Sin antena: se reproducen mensajes hex conocidos. Eso permite comparar contra
el valor esperado exacto en vez de contra "algo que parece una coordenada", y
permite probar los casos incomodos a proposito.

El historial que justifica cada caso: la posicion salio None en el 100% de los
mensajes durante toda la primera etapa del proyecto por DOS bugs encadenados.
adsb_rtlsdr.py leia decoded['lat'] cuando pyModeS emite 'latitude', y
adsb_events._load_db/_load_csv descartaban esas columnas al releerlas. Arreglar
uno solo no cambiaba nada observable, asi que la cadena entera se prueba de
punta a punta: hex -> Observation -> SQLite/CSV -> resumen -> cobertura.

Los mensajes de superficie son sinteticos con CRC recalculado, porque un avion
en pista es exactamente lo que esta antena todavia no escucha (esta en San
Isidro, a 7 km de la pista mas cercana) y no hay una captura real que usar.
"""
import csv as _csv
import math
import os
import sqlite3
import tempfile
from pathlib import Path

import pyModeS as pms

from adsb import Observation
from adsb_events import (AircraftState, _load_csv, _read_csv, _read_db,
                         coverage_report, detect, load_db)
from adsb_record import COLUMNS, SCHEMA, as_row
from adsb_report import field_coverage, summarize
from adsb_rtlsdr import RtlAdsbRecorder, decoded_to_observation
from receiver import DEFAULT_SURFACE_REF, distance_km, parse_surface_ref

# El par even/odd clasico de la documentacion de pyModeS y su resultado exacto.
PAR_CLASICO = ["8D40621D58C382D690C8AC2863A7", "8D40621D58C386435CC412692AD6"]
LAT_ESPERADA, LON_ESPERADA = 52.2572021484375, 3.91937255859375

# BDS 0,6 (posicion en superficie, TC 7) construidos sobre las coordenadas de
# la tabla de aeropuertos de pyModeS, con paridad CRC-24 recalculada.
SUPERFICIE = {
    "SADF": ("8DE0640D3A8A001FF233D315FE2C", (-34.4532, -58.5896)),
    "SABE": ("8DE0640D3A8A03D7706458F1003C", (-34.55942, -58.41554)),
    "SAEZ": ("8DE0640D3A8A03240C42D21A25B4", (-34.8222, -58.5358)),
}
# Mensajes que antes mataban el hilo lector con TypeError.
TC19_SIN_VR = "8D4850209948640C800005A6DDD5"   # vuelo nivelado: vertical_rate None
TC7_DETENIDO = "8D484175380A134566C4D542867F"  # avion parado: groundspeed None
DF0_REAL = "02E197B00179C7"                     # crc_valid None, no verificable

fallos = []


def revisar(nombre, condicion, detalle=""):
    print(f"  {'OK   ' if condicion else 'FALLA'} {nombre}" + (f"  -- {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def decodificar(mensajes, surface_ref=DEFAULT_SURFACE_REF, t0=1_800_000_000.0):
    """Reproduce una lista de hex y devuelve las Observations resultantes."""
    decoder = pms.PipeDecoder(surface_ref=surface_ref)
    observaciones = []
    for i, mensaje in enumerate(mensajes):
        decoded = decoder.decode(mensaje, timestamp=t0 + i)
        observaciones.append(
            decoded_to_observation(decoded["icao"].lower(), decoded, t0 + i))
    return observaciones


print("1. Par CPR clasico: la posicion llega al Observation con el valor exacto")
serie = decodificar(PAR_CLASICO * 5)
con_pos = [o for o in serie if o.latitude is not None]
revisar("decodifica la posicion", bool(con_pos), "ninguna observacion trajo latitude")
exacta = [o for o in serie
          if o.latitude == LAT_ESPERADA and o.longitude == LON_ESPERADA]
revisar("coincide con el valor esperado del par", bool(exacta),
        f"ninguna dio ({LAT_ESPERADA}, {LON_ESPERADA})")
# El bootstrap de pyModeS 3.6 exige ~3 posiciones CPR consistentes antes de
# emitir la primera, asi que los primeros mensajes salen sin posicion. Es
# contraintuitivo y se fija aca para que un cambio de version se note.
primera = next(i for i, o in enumerate(serie) if o.latitude is not None)
revisar("el bootstrap tarda mas de un par", primera >= 2, f"la primera fue en el indice {primera}")
print(f"       -> primera posicion en el mensaje {primera + 1} de {len(serie)}, "
      f"{len(con_pos)} con posicion\n")

print("2. Superficie (avion en pista): un mensaje suelto alcanza, si hay referencia")
for code, (mensaje, (lat_real, lon_real)) in SUPERFICIE.items():
    o = decodificar([mensaje])[0]
    if o.latitude is None:
        revisar(f"{code}: resuelve la posicion", False, "salio sin latitude")
        continue
    error_m = 2 * 6_371_000 * math.asin(math.sqrt(
        math.sin(math.radians(o.latitude - lat_real) / 2) ** 2
        + math.cos(math.radians(lat_real)) * math.cos(math.radians(o.latitude))
        * math.sin(math.radians(o.longitude - lon_real) / 2) ** 2))
    revisar(f"{code}: resuelve la posicion con error < 5 m", error_m < 5.0,
            f"error {error_m:.1f} m")
    # BDS 0,6 no lleva altitud porque el avion esta en el suelo: la ausencia es
    # la senal. Sin esto ningun avion en pista queda marcado en tierra y no se
    # puede inferir un solo despegue ni aterrizaje.
    revisar(f"{code}: lo marca en tierra", o.is_on_ground and o.altitude_ft == 0.0,
            f"alt={o.altitude_ft} suelo={o.is_on_ground}")
    print(f"       -> {code}: {o.latitude:.6f},{o.longitude:.6f} "
          f"({error_m:.2f} m de error, {distance_km(o.latitude, o.longitude):.1f} km del receptor)")

sin_ref = decodificar([SUPERFICIE["SADF"][0]], surface_ref=None)[0]
revisar("sin referencia de superficie no inventa una posicion", sin_ref.latitude is None,
        f"dio {sin_ref.latitude}")
revisar("sin referencia sigue marcando en tierra", sin_ref.is_on_ground)
print()

print("3. Claves presentes con valor None: no deben matar el hilo lector")
# pyModeS distingue "la clave no vino" de "la clave vino vacia", y el segundo
# caso es el mas comun del aire (vuelo nivelado) y de la pista (avion parado).
for nombre, mensaje in (("vuelo nivelado", TC19_SIN_VR), ("avion detenido", TC7_DETENIDO)):
    try:
        o = decodificar([mensaje])[0]
        revisar(f"{nombre}: no lanza excepcion", True)
        print(f"       -> {nombre}: gs={o.ground_speed_kt} vr={o.vertical_rate_fpm} "
              f"alt={o.altitude_ft}")
    except Exception as exc:
        revisar(f"{nombre}: no lanza excepcion", False, f"{type(exc).__name__}: {exc}")
print()

print("4. CRC no verificable: ni corrupto ni creido de entrada")
# pyModeS solo fija crc_valid para DF17/18/20/21; para DF0/4/5/11/16 viene None
# porque la paridad va XOR-eada con la direccion del avion. Tratar ese None
# como corrupto tiraba mensajes con altitud y velocidad utiles.
#
# Pero creerlo de entrada era peor, y se midio: como la direccion sale de bits
# que nadie puede verificar, cada trama de ruido inventa una aeronave. Sobre lo
# grabado en esta antena eso daba 2894 direcciones vistas UNA sola vez y nunca
# mas, contra 294 creibles: el titular de la pagina decia 3123 aeronaves, 36
# veces inflado. Ahora la trama no verificable se RETIENE hasta que la
# direccion se repita, o hasta que un DF17 con CRC valido la confirme, asi que
# no se pierde el dato de las aeronaves reales y el ruido no entra.
recorder = RtlAdsbRecorder()
recorder._decoder = pms.PipeDecoder(surface_ref=DEFAULT_SURFACE_REF)
recorder._handle_line(f"*{DF0_REAL};")
revisar("la trama sin CRC verificable no se registra todavia",
        recorder.poll_count == 0, f"registro {recorder.poll_count}")
revisar("pero no se descarta: queda retenida",
        len(recorder._pendientes) == 1, f"retuvo {len(recorder._pendientes)}")
revisar("no cuenta como corrupta", recorder.corrupt_count == 0,
        f"conto {recorder.corrupt_count}")
revisar("se cuenta como no verificable", recorder.unverified_count == 1,
        f"conto {recorder.unverified_count}")

# La MISMA direccion otra vez: se confirma y entran las dos, la retenida
# incluida. Que el primer mensaje no se pierda es el punto de retener en vez
# de descartar.
recorder._handle_line(f"*{DF0_REAL};")
revisar("al repetirse la direccion entran las dos tramas",
        recorder.poll_count == 2, f"registro {recorder.poll_count}")
revisar("y ya no queda nada retenido", not recorder._pendientes,
        f"quedaron {len(recorder._pendientes)}")

# Una direccion vista una sola vez no entra nunca: eso es exactamente el ruido.
solo = RtlAdsbRecorder()
solo._decoder = pms.PipeDecoder(surface_ref=DEFAULT_SURFACE_REF)
solo._handle_line(f"*{DF0_REAL};")
revisar("una direccion vista una sola vez no se registra",
        solo.poll_count == 0, f"registro {solo.poll_count}")

# Un DF17 con CRC valido es autoridad sobre la direccion: entra solo, sin
# necesitar repeticion.
df17 = RtlAdsbRecorder()
df17._decoder = pms.PipeDecoder(surface_ref=DEFAULT_SURFACE_REF)
df17._handle_line(f"*{PAR_CLASICO[0]};")
revisar("un DF17 con CRC valido entra de una",
        df17.poll_count == 1, f"registro {df17.poll_count}")
print("       -> retenida y liberada al repetirse; DF17 entra directo")
print()

print("5. Round trip por SQLite y por CSV: la posicion tiene que volver")
# Se leen con _read_db/_read_csv, los lectores CRUDOS, y no con _load_db/
# _load_csv, que corren el filtro de posiciones. No es para esquivar el filtro:
# es que estas observaciones son sinteticas y fisicamente imposibles a
# proposito. El par CPR clasico esta en el Mar del Norte (52.26 N, 3.92 E), a
# 11 600 km de la antena, y los tres mensajes de superficie son el MISMO icao24
# apareciendo en San Fernando, Aeroparque y Ezeiza con un segundo de
# diferencia. El filtro las rechaza, y hace bien -- eso se comprueba abajo. Lo
# que este caso prueba es otra cosa: que las columnas latitude/longitude
# sobreviven el viaje a SQLite y al CSV, que es el bug historico que motivo
# todo el archivo.
observaciones = decodificar(PAR_CLASICO * 5 + [m for m, _ in SUPERFICIE.values()])
esperadas = sum(1 for o in observaciones if o.latitude is not None)
with tempfile.TemporaryDirectory() as carpeta:
    db_path = Path(carpeta) / "prueba.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    hueco = ",".join("?" * len(COLUMNS))
    for o in observaciones:
        fila = as_row(o)
        conn.execute(f"INSERT INTO adsb_log ({','.join(COLUMNS)}) VALUES ({hueco})",
                     [fila[c] if fila[c] != "" else None for c in COLUMNS])
    conn.commit()
    conn.close()
    # _read_db devuelve tambien el cursor (el maximo id leido) desde que el
    # mapa lee por delta: aca no se usa, pero el desempaque tiene que estar.
    releidas, cursor_db = _read_db(str(db_path))
    revisar("_read_db devuelve el cursor de la ultima fila",
            cursor_db == len(releidas), f"dio {cursor_db} con {len(releidas)} filas")
    recuperadas = sum(1 for o in releidas if o.latitude is not None)
    revisar("_read_db devuelve la posicion", recuperadas == esperadas,
            f"grabadas {esperadas}, recuperadas {recuperadas}")

    # Y el camino que SI filtra, sobre esa misma base: tiene que rechazarlas
    # todas, contarlas y decir por que. Si alguien afloja el filtro hasta que
    # deje pasar un avion en el Mar del Norte visto desde San Isidro, esto se
    # pone en rojo.
    filtradas, gate = load_db(str(db_path))
    revisar("load_db rechaza las 5 del Mar del Norte por horizonte",
            gate.por_motivo["horizonte"] == 5, f"conto {gate.por_motivo}")
    revisar("y las 2 teletransportaciones entre aeropuertos por velocidad",
            gate.por_motivo["velocidad"] == 2, f"conto {gate.por_motivo}")
    # La que sobrevive es SADF a 7.3 km, y sobrevive con razon: es la PRIMERA
    # posicion de ese icao24, o sea que R2 no tiene contra que compararla, y
    # por si sola es perfectamente plausible (un avion en pista a 7 km de la
    # antena). Es el agujero conocido y aceptado del diseno: un fantasma que
    # caiga cerca Y en la primera posicion de una aeronave no lo atrapa nadie.
    # Taparlo exigiendo N posiciones antes de publicar la primera retrasaria
    # toda traza nueva. Queda fijado aca para que sea una decision y no un
    # olvido.
    quedan = [o for o in filtradas if o.latitude is not None]
    revisar("sobrevive solo la primera de superficie, que es plausible",
            len(quedan) == 1 and abs(distance_km(quedan[0].latitude,
                                                 quedan[0].longitude) - 7.3) < 0.5,
            f"quedaron {len(quedan)}")
    # La observacion NO se borra: el filtro anula lat/lon y deja el resto.
    revisar("pero conserva las observaciones enteras", len(filtradas) == len(releidas),
            f"{len(filtradas)} contra {len(releidas)}")
    print(f"       -> el filtro rechazo {gate.rechazadas} de {gate.evaluadas} "
          f"({', '.join(f'{k} {v}' for k, v in gate.por_motivo.items() if v)})")

    csv_path = Path(carpeta) / "prueba.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        escritor = _csv.DictWriter(handle, fieldnames=COLUMNS)
        escritor.writeheader()
        for o in observaciones:
            escritor.writerow(as_row(o))
    del_csv = sum(1 for o in _read_csv(str(csv_path)) if o.latitude is not None)
    revisar("_read_csv devuelve la posicion", del_csv == esperadas,
            f"grabadas {esperadas}, recuperadas {del_csv}")

    # Un CSV viejo, sin las columnas: tiene que cargar igual y no explotar.
    viejo = Path(carpeta) / "viejo.csv"
    viejo.write_text("utc,epoch,icao24,registration,callsign,altitude_ft\n"
                     "2026-08-22T00:00:00+00:00,1800000000,abc123,,,35000\n",
                     encoding="utf-8")
    antiguas = _load_csv(str(viejo))
    revisar("un CSV sin columnas de posicion sigue cargando",
            len(antiguas) == 1 and antiguas[0].latitude is None)
print(f"       -> {esperadas} posiciones grabadas, recuperadas por las dos vias\n")

print("6. Reporte: la fila de cobertura y la distancia por aeronave")
fila = next(f for f in field_coverage(releidas) if f["key"] == "position")
revisar("la fila Posicion ya no dice PENDIENTE", "PENDIENTE" not in fila["origin"],
        f"dice {fila['origin']!r}")
revisar("la fila Posicion cuenta mensajes", fila["messages"] == esperadas,
        f"conto {fila['messages']}")
resumen = {s.icao24: s for s in summarize(releidas)}
pista = resumen["e0640d"]
revisar("el resumen guarda la ultima posicion conocida",
        pista.last_latitude is not None and pista.last_longitude is not None)
# Los tres mensajes de superficie son San Fernando, Aeroparque y Ezeiza vistos
# desde San Isidro: 7.3, 13.3 y 39.1 km medidos con el haversine del repo.
revisar("la distancia minima es la del aeropuerto mas cercano",
        pista.min_distance_km is not None and abs(pista.min_distance_km - 7.3) < 0.5,
        f"dio {pista.min_distance_km}")
revisar("la distancia maxima es el alcance real",
        pista.max_distance_km is not None and abs(pista.max_distance_km - 39.1) < 0.5,
        f"dio {pista.max_distance_km}")
cobertura = coverage_report(releidas)
revisar("la cobertura informa cuantas posiciones hubo",
        cobertura["with_position"] == esperadas, f"dio {cobertura['with_position']}")
revisar("la cobertura informa alcance", cobertura["p95_distance_km"] is not None)
revisar("una grabacion sin posiciones no rompe la cobertura",
        coverage_report([o for o in releidas if o.latitude is None])["max_distance_km"] is None)
print(f"       -> {fila['messages']} mensajes con posicion ({fila['messages_pct']:.0f}%), "
      f"origen: {fila['origin']}")
print(f"       -> en pista: {pista.min_distance_km:.1f} a {pista.max_distance_km:.1f} km "
      f"del receptor\n")

print("7. Referencia de superficie: configurable, y un typo tiene que doler rapido")
revisar("'lat,lon' se interpreta como TUPLA", parse_surface_ref("-34.47,-58.51") == (-34.47, -58.51),
        f"dio {parse_surface_ref('-34.47,-58.51')!r}")
revisar("un codigo ICAO queda en mayusculas", parse_surface_ref(" sadf ") == "SADF")
revisar("vacio es sin referencia", parse_surface_ref("") is None)
for texto in ("-134.5,-58.5", "-34.5,-258.5"):
    try:
        parse_surface_ref(texto)
        revisar(f"rechaza {texto}", False, "lo acepto")
    except ValueError:
        revisar(f"rechaza {texto}", True)

os.environ["ADSB_SURFACE_REF"] = "SABE"
revisar("ADSB_SURFACE_REF llega al recorder", RtlAdsbRecorder().surface_ref == "SABE",
        f"dio {RtlAdsbRecorder().surface_ref!r}")
del os.environ["ADSB_SURFACE_REF"]
revisar("sin variable de entorno, el default es San Isidro",
        RtlAdsbRecorder().surface_ref == DEFAULT_SURFACE_REF)

# pyModeS valida la referencia recien en el primer mensaje de superficie, ya
# adentro del hilo lector: sin este chequeo un typo se volvia un relanzamiento
# de rtl_adsb.exe cada 3 segundos, para siempre, sin decir por que.
for mala, motivo in (("SAXX", "codigo inexistente"),
                     ((-58.5128, -34.4708), "lat y lon invertidas")):
    recorder = RtlAdsbRecorder(surface_ref=mala)
    error = recorder._validate_surface_ref()
    revisar(f"rechaza la referencia mala ({motivo})", error is not None, "la acepto")
    if error:
        print(f"       -> {error}")
print()

print("8. Lo que todo esto desbloquea: un avion en pista produce un despegue")
# Este es el punto del arreglo. Antes, un mensaje BDS 0,6 salia con
# altitude_ft=None e is_on_ground=False, asi que AircraftState.phase nunca daba
# 'ground' y era imposible inferir un despegue o un aterrizaje: justo el estado
# que hace falta para contar operaciones, a partir del unico tipo de mensaje que
# PRUEBA que el avion esta en el suelo.
en_pista = decodificar([SUPERFICIE["SADF"][0]] * 3, t0=1_800_000_000.0)
estado = AircraftState(icao24=en_pista[0].icao24)
for o in en_pista:
    estado.update(o)
revisar("solo con mensajes de superficie la fase es 'ground'", estado.phase == "ground",
        f"dio {estado.phase!r}")

despegando = en_pista + [
    Observation(timestamp=1_800_000_030.0 + i * 10, icao24=en_pista[0].icao24,
                altitude_ft=500 + i * 1000, vertical_rate_fpm=2500)
    for i in range(3)
]
eventos = detect(despegando)
revisar("la secuencia pista -> ascenso da un despegue",
        len(eventos) == 1 and eventos[0].event_type == "takeoff",
        f"dio {[(e.event_type, e.reason) for e in eventos]}")
print(f"       -> {[(e.event_type, e.reason) for e in eventos]}\n")

print("=" * 55)
print("TODO CORRECTO" if not fallos else f"FALLAS: {fallos}")
raise SystemExit(1 if fallos else 0)
