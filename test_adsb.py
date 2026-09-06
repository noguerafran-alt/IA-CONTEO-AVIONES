"""Exercise the ADS-B matching against built scenarios, including the hard ones.

No receiver needed: every scenario is constructed, so the awkward cases can be
tested on purpose rather than waited for. The one that matters is two aircraft
seconds apart -- that is where naive time-matching attaches the wrong tail
number, and where this has to answer "I cannot tell" instead of guessing.
"""
import time

from adsb import Observation, parse_aircraft_json
from match_adsb import match_event

AHORA = 1_800_000_000.0          # instante fijo, para que el test sea reproducible
CAM_LAT, CAM_LON = -34.5589, -58.4164   # Aeroparque, aproximado

fallos = []


def revisar(nombre, condicion, detalle=""):
    print(f"  {'OK   ' if condicion else 'FALLA'} {nombre}" + (f"  -- {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def avion(icao, matricula, t_offset, alt=None, vs=None, gs=None, lat=None, lon=None, vuelo=None):
    return Observation(timestamp=AHORA + t_offset, icao24=icao, registration=matricula,
                       callsign=vuelo, altitude_ft=alt, vertical_rate_fpm=vs,
                       ground_speed_kt=gs, latitude=lat, longitude=lon)


print("1. Un solo avion aterrizando")
obs = [avion("e80456", "LV-HKN", -2, alt=300, vs=-700, gs=140, lat=CAM_LAT, lon=CAM_LON, vuelo="FO5239")]
m = match_event(obs, AHORA, "landing", CAM_LAT, CAM_LON)
revisar("identifica LV-HKN", m.registration == "LV-HKN", f"dio {m.registration}")
revisar("no lo marca ambiguo", not m.ambiguous)
print(f"       -> {m.registration} ({m.reason})\n")

print("2. Dos aviones a segundos de distancia (el caso dificil)")
obs = [
    avion("e80456", "LV-HKN", -3, alt=250, vs=-650, gs=140, lat=CAM_LAT, lon=CAM_LON),
    avion("e80999", "LV-CEV", +4, alt=280, vs=-700, gs=138, lat=CAM_LAT, lon=CAM_LON),
]
m = match_event(obs, AHORA, "landing", CAM_LAT, CAM_LON)
revisar("detecta la ambiguedad", m.ambiguous, "acepto uno sin margen suficiente")
revisar("no informa matricula dudosa", m.registration is None, f"informo {m.registration}")
print(f"       -> {m.reason}\n")

print("3. La telemetria desempata: uno aterriza, el otro solo pasa")
obs = [
    avion("e80456", "LV-HKN", -3, alt=250, vs=-650, gs=140, lat=CAM_LAT, lon=CAM_LON),
    avion("e80999", "LV-CEV", +2, alt=9000, vs=0, gs=300, lat=CAM_LAT, lon=CAM_LON),
]
m = match_event(obs, AHORA, "landing", CAM_LAT, CAM_LON)
revisar("elige el que desciende", m.registration == "LV-HKN", f"dio {m.registration}")
print(f"       -> {m.registration} ({m.reason})\n")

print("4. Despegue: distingue del que esta taxiando")
obs = [
    avion("e80111", "LV-GUB", -1, alt=0, vs=None, gs=8, lat=CAM_LAT, lon=CAM_LON),     # taxi
    avion("e80222", "LV-FUA", +1, alt=400, vs=+2200, gs=170, lat=CAM_LAT, lon=CAM_LON), # despega
]
m = match_event(obs, AHORA, "takeoff", CAM_LAT, CAM_LON)
revisar("elige el que asciende", m.registration == "LV-FUA", f"dio {m.registration}")
print(f"       -> {m.registration} ({m.reason})\n")

print("5. Avion lejano: no debe atribuirse")
obs = [avion("e80777", "LV-XXX", 0, alt=500, vs=-600, gs=140, lat=-34.80, lon=-58.53)]
m = match_event(obs, AHORA, "landing", CAM_LAT, CAM_LON)
revisar("descarta por distancia", m.registration is None, f"informo {m.registration}")
print(f"       -> {m.reason}\n")

print("6. Sin datos ADS-B (receptor caido)")
m = match_event([], AHORA, "landing", CAM_LAT, CAM_LON)
revisar("no rompe y avisa", m.registration is None and "sin datos" in m.reason)
print(f"       -> {m.reason}\n")

print("7. Fuera de la ventana de tiempo")
obs = [avion("e80456", "LV-HKN", -300, alt=300, vs=-700, lat=CAM_LAT, lon=CAM_LON)]
m = match_event(obs, AHORA, "landing", CAM_LAT, CAM_LON)
revisar("ignora lo viejo", m.registration is None, f"informo {m.registration}")
print(f"       -> {m.reason}\n")

print("8. Un avion que reporta muchas veces no debe ganar por repeticion")
obs = ([avion("e80999", "LV-CEV", i * 0.5, alt=9000, vs=0, gs=300,
              lat=CAM_LAT, lon=CAM_LON) for i in range(-8, 8)]
       + [avion("e80456", "LV-HKN", -1, alt=250, vs=-650, gs=140, lat=CAM_LAT, lon=CAM_LON)])
m = match_event(obs, AHORA, "landing", CAM_LAT, CAM_LON)
revisar("gana el que aterriza, no el mas hablador", m.registration == "LV-HKN", f"dio {m.registration}")
print(f"       -> {m.registration}\n")

print("9. Parseo del JSON real de dump1090")
payload = {"now": AHORA, "aircraft": [
    {"hex": "e80456", "r": "LV-HKN", "flight": "FO5239 ", "alt_baro": 1200,
     "gs": 145.2, "baro_rate": -768, "lat": CAM_LAT, "lon": CAM_LON, "seen": 0.4},
    {"hex": "e80888", "r": "LV-GUB", "alt_baro": "ground", "gs": 12, "seen": 1.1},
    {"hex": "", "r": "SIN-HEX"},   # descartable
]}
parsed = parse_aircraft_json(payload)
revisar("descarta entradas sin ICAO24", len(parsed) == 2, f"parseo {len(parsed)}")
revisar("limpia el callsign", parsed[0].callsign == "FO5239", f"dio {parsed[0].callsign!r}")
revisar("interpreta 'ground' como suelo", parsed[1].is_on_ground)
revisar("detecta descenso", parsed[0].is_descending)
print()

print("10. Registro de uptime del grabador")
import sqlite3 as _sqlite3
import tempfile as _tempfile
from pathlib import Path as _Path

import adsb_uptime

_tmp = _Path(_tempfile.mkdtemp()) / "uptime.db"
_conn = _sqlite3.connect(_tmp)
adsb_uptime.crear_esquema(_conn)

# T0 es una marca fija: todo se afirma en relacion a T0 y no con time.time(),
# asi que el test no depende de cuando se corre.
T0 = 1_757_000_000.0

# Una sesion que cerro ordenada: 10 min arriba.
s1 = adsb_uptime.abrir_sesion(_conn, receptor="aeroparque-pista", fuente="iq",
                              latido_cada_s=30.0, ahora=T0)
adsb_uptime.cerrar_sesion(_conn, s1, "detenido", ahora=T0 + 600)

# Una que se CAYO: late hasta T0+1800 y nunca cierra.
s2 = adsb_uptime.abrir_sesion(_conn, receptor="aeroparque-pista", fuente="iq",
                              latido_cada_s=30.0, ahora=T0 + 1200)
adsb_uptime.latir(_conn, s2, ahora=T0 + 1800)

# La ventana es la hora entre T0 y T0+3600, mirada desde T0+3600.
r = adsb_uptime.resumen(_conn, desde=T0, hasta=T0 + 3600, ahora=T0 + 3600)

revisar("suma los dos intervalos (600 + 600 s)",
        r["segundos_arriba"] == 1200.0, f"dio {r['segundos_arriba']}")
revisar("la cobertura es 1200/3600",
        r["cobertura"] == round(1200 / 3600, 4), f"dio {r['cobertura']}")
# La que se cayo cuenta como caida; la que cerro ordenada NO.
revisar("cuenta una sola caida", r["caidas"] == 1, f"dio {r['caidas']}")
revisar("no cree que algo este corriendo", r["corriendo"] is False)
# El hueco mas largo es T0+1800 -> T0+3600 = 1800 s, mas largo que el de
# 600->1200. Es el numero que decide si un share por franja se puede publicar.
revisar("el hueco maximo son 1800 s", r["hueco_max_s"] == 1800.0,
        f"dio {r['hueco_max_s']}")

# EL CASO QUE JUSTIFICA TODO EL MODULO: prendida y sorda. Una sesion con
# latidos y CERO filas en adsb_log tiene que dar cobertura completa -- el
# silencio de datos no la puede borrar del registro.
_conn.execute("DELETE FROM grabador_sesion")
s3 = adsb_uptime.abrir_sesion(_conn, latido_cada_s=30.0, ahora=T0)
adsb_uptime.latir(_conn, s3, ahora=T0 + 3600)
sorda = adsb_uptime.resumen(_conn, desde=T0, hasta=T0 + 3600, ahora=T0 + 3600)
revisar("prendida y sorda: cobertura 100% aunque no entro un solo mensaje",
        sorda["cobertura"] == 1.0, f"dio {sorda['cobertura']}")
revisar("y la reconoce corriendo, no caida",
        sorda["corriendo"] is True and sorda["caidas"] == 0,
        f"corriendo={sorda['corriendo']} caidas={sorda['caidas']}")

# Sin sesiones, la cobertura es None y NO 0.0: un cero seria afirmar que no
# grabo nada, y con la tabla vacia no hay con que afirmarlo.
_conn.execute("DELETE FROM grabador_sesion")
vacio = adsb_uptime.resumen(_conn, desde=T0, hasta=T0 + 3600, ahora=T0 + 3600)
revisar("tabla vacia: cobertura None, no 0.0", vacio["cobertura"] is None,
        f"dio {vacio['cobertura']!r}")
revisar("y el hueco es la ventana entera", vacio["hueco_max_s"] == 3600.0,
        f"dio {vacio['hueco_max_s']}")
_conn.close()
print()

print("=" * 55)
print("TODO CORRECTO" if not fallos else f"FALLAS: {fallos}")
raise SystemExit(1 if fallos else 0)
