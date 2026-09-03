"""El mapa incremental no puede cambiar ni un numero, y el cursor no puede
perder ni repetir una fila.

Por que existe este archivo. Los dos mapas dejaron de releer la base entera en
cada refresco y pasaron a avanzar un cursor: eso baja el costo por refresco de
164 ms y 208 028 B a un SELECT de 0.06 ms y ~40 B, pero solo sirve si el
resultado es IDENTICO al de load_db. Y la falla mas probable de un cursor no
hace ruido: un delta que se saltea una posicion deja un agujero en la traza que
nadie va a notar mirando el mapa, y un delta que la repite dibuja al avion dos
veces en el mismo lugar, que se ve igual de bien.

Asi que se comprueban dos cosas distintas:

  EQUIVALENCIA. Sobre una base sintetica y sobre la base real, el camino
  incremental tiene que dar exactamente lo mismo que load_db: contadores del
  filtro, la lista completa de rechazos, json.dumps(tracks) y
  json.dumps(coverage) con sort_keys, la mediana y el p95 exactas, y
  aeropuerto.informe() por las dos rutas. El troceo se hace en lotes de tamano
  IRREGULAR -1, 3, 17, 50, 113- porque el PositionGate tiene estado y un
  reductor con estado puede depender del tamano del lote sin que se note con
  lotes parejos.

  CURSOR. Primera carga con cursor vacio, delta con el cursor al dia, delta con
  un cursor viejo, filas nuevas entre dos consultas, y -la que importa- que la
  UNION de todos los deltas sea exactamente la carga completa, sin faltantes y
  sin repetidos.

Se corre solo:  .venv/Scripts/python.exe test_adsb_incremental.py
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import adsb_report
import aeropuerto
from adsb import Observation
from adsb_events import (LectorIncremental, coverage_report, load_db)
from adsb_record import COLUMNS, SCHEMA, as_row

fallos = []


def revisar(nombre, condicion, detalle=""):
    print(f"  {'OK   ' if condicion else 'FALLA'} {nombre}"
          + (f"  -- {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def jd(x):
    return json.dumps(x, sort_keys=True, default=str)


# --- una grabacion sintetica, con las trampas adentro ----------------------
# No se inventa "trafico lindo": la base tiene que traer las cosas que rompen un
# cursor. Epochs REPETIDOS (598 duplicados consecutivos en la base real, que es
# la razon por la que el cursor no puede ser epoch), filas sin posicion -las que
# traen la velocidad con la que mide la regla de continuidad- y una posicion
# imposible para que el filtro tenga algo que rechazar.
T0 = 1_800_000_000.0
REF_LAT, REF_LON = -34.47, -58.53         # cerca del receptor por defecto


def grabacion() -> list[Observation]:
    filas = []
    for i in range(120):
        t = T0 + i * 4.0
        icao = ["e06541", "e80413", "abc123"][i % 3]
        # Un avion que se acerca en linea recta desde el noroeste.
        lat = REF_LAT + 0.30 - i * 0.0018
        lon = REF_LON - 0.30 + i * 0.0020
        filas.append(Observation(timestamp=t, icao24=icao,
                                 callsign=f"AR{1000 + (i % 3)}",
                                 altitude_ft=9000.0 - i * 55.0,
                                 latitude=lat, longitude=lon,
                                 track_deg=(120.0 + i) % 360 if i % 4 == 0 else None))
        # Mensaje de velocidad SIN posicion, con el MISMO epoch que el anterior:
        # asi la base repite epoch igual que la real.
        filas.append(Observation(timestamp=t, icao24=icao,
                                 ground_speed_kt=240.0 + i))
    # La imposible: 800 km al norte a 20 000 ft. Tiene que ser rechazada por
    # horizonte, y tiene que serlo en las dos rutas y en el mismo orden.
    filas.append(Observation(timestamp=T0 + 500.0, icao24="e0b14a",
                             altitude_ft=20000.0, latitude=REF_LAT + 7.2,
                             longitude=REF_LON))
    # Y una aeronave que entra bajo y cerca de Aeroparque, para que el informe
    # del aeropuerto tenga algo que clasificar por las dos rutas.
    try:
        from pyModeS.position._airports import AIRPORTS
        alat, alon = AIRPORTS["SABE"]
    except Exception:
        alat, alon = -34.5592, -58.4156
    for i in range(14):
        filas.append(Observation(
            timestamp=T0 + 600.0 + i * 6.0, icao24="e49406", callsign="ARG1234",
            altitude_ft=3200.0 - i * 220.0,
            latitude=alat + 0.045 - i * 0.0032, longitude=alon + 0.010,
            track_deg=175.0 if i % 3 == 0 else None))
    return filas


def escribir(conn, observaciones):
    hueco = ",".join("?" * len(COLUMNS))
    for o in observaciones:
        fila = as_row(o)
        conn.execute(f"INSERT INTO adsb_log ({','.join(COLUMNS)}) VALUES ({hueco})",
                     [fila[c] if fila[c] != "" else None for c in COLUMNS])
    conn.commit()


print("1. La base sintetica trae las trampas que un cursor tiene que aguantar")
# ignore_cleanup_errors: en Windows las conexiones de SQLite del propio test
# mantienen el archivo abierto y el rmtree final tira PermissionError.
carpeta = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
db = Path(carpeta.name) / "prueba.db"
conn = sqlite3.connect(db)
conn.executescript(SCHEMA)
todas = grabacion()
escribir(conn, todas)
epochs = [f[0] for f in conn.execute("SELECT epoch FROM adsb_log ORDER BY id")]
repetidos = sum(1 for a, b in zip(epochs, epochs[1:]) if a == b)
revisar("hay epochs repetidos consecutivos (por eso el cursor no es epoch)",
        repetidos > 0, f"conto {repetidos}")
revisar("epoch no decrece con id (la propiedad de la que depende el troceo)",
        all(b >= a for a, b in zip(epochs, epochs[1:])))
print(f"       -> {len(todas)} filas, {repetidos} pares con el mismo epoch")
print()


print("2. Equivalencia con load_db, troceando en lotes irregulares")
obs_ref, gate_ref = load_db(str(db))
cov_ref = coverage_report(obs_ref, gate_ref)
tr_ref = {t["icao24"]: t for t in adsb_report.tracks(obs_ref)}
inf_ref = aeropuerto.informe(obs_ref, "SABE")

# El troceo se simula reescribiendo la base de a lotes: avanzar() lee de SQLite,
# asi que la unica forma honesta de trocear es que las filas aparezcan de a poco,
# igual que en una grabacion real.
db2 = Path(carpeta.name) / "troceada.db"
conn2 = sqlite3.connect(db2)
conn2.executescript(SCHEMA)
conn2.commit()
lector = LectorIncremental(str(db2), cilindro=aeropuerto.geometria_cilindro("SABE"))
LOTES = [1, 3, 17, 50, 113]
i, k, avances = 0, 0, 0
while i < len(todas):
    n = LOTES[k % len(LOTES)]
    escribir(conn2, todas[i:i + n])
    d = lector.avanzar()
    revisar(f"lote de {n}: el cursor queda en la ultima fila escrita",
            d["cursor"] == min(i + n, len(todas)), f"cursor {d['cursor']}")
    i += n
    k += 1
    avances += 1
print(f"       -> {avances} avances con lotes de {LOTES} (ciclicos)")

revisar("el cursor termina en la ultima fila",
        lector.cursor == len(todas), f"dio {lector.cursor} de {len(todas)}")
revisar("mismas posiciones aceptadas", lector.gate.aceptadas == gate_ref.aceptadas,
        f"{lector.gate.aceptadas} contra {gate_ref.aceptadas}")
revisar("mismas evaluadas", lector.gate.evaluadas == gate_ref.evaluadas,
        f"{lector.gate.evaluadas} contra {gate_ref.evaluadas}")
revisar("mismo desglose de rechazos por motivo",
        dict(lector.gate.por_motivo) == dict(gate_ref.por_motivo),
        f"{dict(lector.gate.por_motivo)} contra {dict(gate_ref.por_motivo)}")
revisar("los rechazos son los MISMOS, uno por uno",
        jd([r.as_dict() for r in lector.gate.rechazos])
        == jd([r.as_dict() for r in gate_ref.rechazos]))
revisar("y hubo al menos uno (si no, esto no probaria nada)",
        len(gate_ref.rechazos) >= 1, f"hubo {len(gate_ref.rechazos)}")

cov_inc = lector.coverage()
revisar("coverage_report identico campo por campo",
        jd(cov_inc) == jd(cov_ref),
        next((f"{k}: {cov_ref[k]!r} contra {cov_inc.get(k)!r}" for k in cov_ref
              if jd(cov_ref[k]) != jd(cov_inc.get(k))), ""))
# La mediana y el p95 salen de un array ordenado con bisect.insort, no de un
# histograma: son exactas, no aproximadas. Una aproximacion de 0.047 km moveria
# el digito que la pagina imprime con toFixed(1).
revisar("mediana exacta", cov_inc["median_distance_km"] == cov_ref["median_distance_km"],
        f"{cov_inc['median_distance_km']} contra {cov_ref['median_distance_km']}")
revisar("p95 exacto", cov_inc["p95_distance_km"] == cov_ref["p95_distance_km"],
        f"{cov_inc['p95_distance_km']} contra {cov_ref['p95_distance_km']}")

tr_inc = {t["icao24"]: t for t in lector.trazas()[0]}
revisar("las mismas aeronaves con traza", set(tr_inc) == set(tr_ref),
        f"{sorted(set(tr_ref) ^ set(tr_inc))}")
distintas = [k for k in tr_ref if jd(tr_ref[k]) != jd(tr_inc.get(k))]
revisar("cada traza identica: puntos, rumbo, origen del rumbo y matricula",
        not distintas, f"difieren {distintas}")

inf_inc = lector.informe_aeropuerto("SABE")
revisar("aeropuerto.informe() identico por las dos rutas",
        jd(aeropuerto.como_json(inf_ref)) == jd(aeropuerto.como_json(inf_inc)))
revisar("y el informe clasifico algo (si no, la comparacion es vacia)",
        inf_ref is not None and len(inf_ref.operaciones) >= 1,
        f"{0 if inf_ref is None else len(inf_ref.operaciones)} operaciones")
print(f"       -> {cov_ref['with_position']} posiciones, {len(tr_ref)} trazas, "
      f"{len(gate_ref.rechazos)} rechazo(s), "
      f"{len(inf_ref.operaciones)} operacion(es) en SABE: todo igual")
print()


print("3. El cursor: sin filas perdidas y sin filas repetidas")
# La union de los deltas tiene que ser EXACTAMENTE la carga completa. Esto es lo
# unico que separa un mapa correcto de uno con un agujero invisible.
db3 = Path(carpeta.name) / "cursor.db"
conn3 = sqlite3.connect(db3)
conn3.executescript(SCHEMA)
conn3.commit()
lec3 = LectorIncremental(str(db3))

d0 = lec3.avanzar()
revisar("cursor vacio sobre una base vacia: cursor 0 y cero filas",
        d0["cursor"] == 0 and d0["filas"] == 0 and d0["posiciones"] == 0,
        f"dio {d0}")

escribir(conn3, todas[:40])
d1 = lec3.avanzar()
revisar("primera carga: lee las 40 filas que hay",
        d1["desde"] == 0 and d1["filas"] == 40 and d1["cursor"] == 40, f"dio {d1}")

d2 = lec3.avanzar()
revisar("cursor al dia: delta vacio, y el cero es explicito",
        d2["filas"] == 0 and d2["posiciones"] == 0 and d2["cursor"] == 40,
        f"dio {d2}")

# Filas nuevas entre dos consultas.
escribir(conn3, todas[40:95])
d3 = lec3.avanzar()
revisar("filas nuevas entre dos consultas: lee solo las 55 nuevas",
        d3["desde"] == 40 and d3["filas"] == 55 and d3["cursor"] == 95, f"dio {d3}")

escribir(conn3, todas[95:])
lec3.avanzar()

# Cursor viejo: el delta desde una fila vieja tiene que traer TODOS los puntos
# posteriores, ni uno mas ni uno menos.
completo = {t["icao24"]: t["points"] for t in lec3.trazas()[0]}
total_puntos = sum(len(v) for v in completo.values())
for viejo in (0, 1, 7, 40, 95, len(todas) - 1, len(todas)):
    delta = {t["icao24"]: t["points"] for t in lec3.delta_trazas(viejo)}
    # Los puntos del delta tienen que ser un SUFIJO exacto de los completos: si
    # faltara uno del medio la traza quedaria con un agujero, y si se repitiera
    # uno el avion se dibujaria dos veces en el mismo lugar. Las dos fallas se
    # ven bien en pantalla, que es justamente el problema.
    ok = True
    for icao, pts in delta.items():
        base = completo[icao]
        if pts != base[len(base) - len(pts):]:
            ok = False
            break
    revisar(f"delta desde id={viejo} es un sufijo exacto de cada traza", ok)
revisar("delta desde 0 == carga completa",
        jd(lec3.delta_trazas(0)) == jd(lec3.trazas()[0]))
revisar("delta desde el ultimo id no trae nada",
        lec3.delta_trazas(lec3.cursor) == [])

# LA COMPROBACION QUE IMPORTA: un cliente real polleando. Cada consulta manda
# como ?desde= el cursor que le devolvio la anterior, y la union de todas las
# respuestas tiene que ser EXACTAMENTE la carga completa. Ni un punto de menos
# (agujero invisible en la traza) ni uno de mas (el avion dibujado dos veces en
# el mismo lugar). Las filas van entrando de a lotes irregulares mientras tanto,
# que es lo que pasa cuando el grabador esta corriendo.
db4 = Path(carpeta.name) / "polleo.db"
conn4 = sqlite3.connect(db4)
conn4.executescript(SCHEMA)
conn4.commit()
lec4 = LectorIncremental(str(db4))
union, cursor_cliente, consultas = {}, 0, 0
i, k = 0, 0
while i < len(todas):
    n = LOTES[k % len(LOTES)]
    escribir(conn4, todas[i:i + n])
    lec4.avanzar()
    for t in lec4.delta_trazas(cursor_cliente):
        union.setdefault(t["icao24"], []).extend(t["points"])
    cursor_cliente = lec4.cursor
    consultas += 1
    i += n
    k += 1
    # Una consulta de mas sin filas nuevas en el medio: el delta vacio no puede
    # devolver nada, y menos repetir lo anterior.
    lec4.avanzar()
    for t in lec4.delta_trazas(cursor_cliente):
        union.setdefault(t["icao24"], []).extend(t["points"])
    consultas += 1

completo4 = {t["icao24"]: t["points"] for t in lec4.trazas()[0]}
revisar("la union de los deltas es la carga completa, sin faltantes ni repetidos",
        jd({k2: union[k2] for k2 in sorted(union)})
        == jd({k2: completo4[k2] for k2 in sorted(completo4)}),
        f"{sum(len(v) for v in union.values())} puntos contra "
        f"{sum(len(v) for v in completo4.values())}")
revisar("y ningun punto aparece dos veces",
        all(len(v) == len({(p["t"], p["lat"], p["lon"]) for p in v})
            for v in union.values()))
print(f"       -> {consultas} consultas encadenadas, "
      f"{sum(len(v) for v in union.values())} puntos, sin faltantes ni repetidos")
print()


print("4. Los endpoints, contra la base real")
sys.path.insert(0, str(Path(__file__).resolve().parent / "webapp"))
try:
    from fastapi.testclient import TestClient
    import main as webmain
    cliente = TestClient(webmain.app)
except Exception as exc:                       # pragma: no cover
    print(f"       -> sin TestClient disponible ({exc}); se saltea")
    cliente = None

if cliente is not None and (Path(__file__).resolve().parent / "adsb_log.db").exists():
    for ruta in ("/api/adsb/mapa", "/api/aeropuerto/mapa"):
        completa = cliente.get(ruta).json()
        revisar(f"{ruta} sin ?desde responde base completa",
                completa.get("base") == "completa", f"dio {completa.get('base')}")
        cursor = completa["cursor"]
        revisar(f"{ruta} publica el cursor", isinstance(cursor, int) and cursor > 0,
                f"dio {cursor!r}")
        # lag_s viaja SIEMPRE: es lo unico que impide que la pagina diga "en
        # vivo" sobre una base atrasada.
        revisar(f"{ruta} publica lag_s", "lag_s" in completa)
        revisar(f"{ruta} publica los ceros explicitos",
                "nuevos" in completa and "nuevas_posiciones" in completa)
        revisar(f"{ruta} publica lo que la ventana dejo afuera",
                "trazas_fuera_de_ventana" in completa
                and "puntos_fuera_de_ventana" in completa)

        d = cliente.get(f"{ruta}?desde={cursor}").json()
        revisar(f"{ruta}?desde=<al dia> responde delta", d.get("base") == "delta",
                f"dio {d.get('base')}")
        revisar(f"{ruta} el delta hace eco de desde", d.get("desde") == cursor,
                f"dio {d.get('desde')}")
        nuevos = d.get("nuevos", -1)
        revisar(f"{ruta} el delta declara cuantas filas trajo", nuevos >= 0)
        # La base esta viva: si entraron filas entre las dos consultas, el delta
        # tiene que traerlas y no cero.
        revisar(f"{ruta} el delta solo trae aeronaves tocadas",
                nuevos > 0 or not d.get("tracks"),
                f"nuevos={nuevos} pero mando {len(d.get('tracks') or [])} trazas")

        viejo = max(0, cursor - 500)
        dv = cliente.get(f"{ruta}?desde={viejo}").json()
        revisar(f"{ruta}?desde=<viejo> sigue siendo delta", dv.get("base") == "delta")
        revisar(f"{ruta}?desde=<viejo> trae al menos tanto como el delta al dia",
                dv.get("nuevas_posiciones", 0) >= d.get("nuevas_posiciones", 0))

        futuro = cursor + 1_000_000
        df = cliente.get(f"{ruta}?desde={futuro}").json()
        revisar(f"{ruta} con un cursor del futuro el SERVIDOR impone completa",
                df.get("base") == "completa" and df.get("forzada"),
                f"dio base={df.get('base')} forzada={df.get('forzada')!r}")

    # La suma de los deltas encadenados sobre el endpoint tiene que dar los
    # mismos puntos por aeronave que una sola carga completa.
    base = cliente.get("/api/adsb/mapa?ventana_h=0").json()
    esperado = {t["icao24"]: len(t["points"]) for t in base["tracks"]}
    cursor0 = base["cursor"]
    # El endpoint no acepta un "hasta", asi que la comprobacion que si se puede
    # hacer contra la base viva es la fuerte: el delta desde 0 tiene que traer
    # exactamente los mismos puntos por aeronave que la carga completa.
    acumulado = {}
    for t in cliente.get("/api/adsb/mapa?desde=0&ventana_h=0").json()["tracks"]:
        acumulado[t["icao24"]] = len(t["points"])
    revisar("delta desde 0 devuelve los mismos puntos por aeronave que la completa",
            acumulado == esperado,
            f"{sum(acumulado.values())} contra {sum(esperado.values())} puntos")
    print(f"       -> {len(esperado)} aeronaves, {sum(esperado.values())} puntos, "
          f"cursor {cursor0}")
else:
    print("       -> no hay adsb_log.db; los endpoints no se ejercitan")
print()

print("5. La base que RETROCEDE: el lector no puede servir lo que ya no existe")
# El caso real: alguien borra adsb_log.db y aprieta Iniciar en /adsb. El
# grabador corre en el MISMO proceso que la webapp, asi que el lector sigue vivo
# con su cursor apuntando a ids de una base que ya no esta. Antes de esto: con
# el cursor en 8000 y la base recreada con 300 filas, tres polls seguidos daban
# nuevos=0 y hasta la carga completa contestaba las trazas viejas.
db5 = Path(carpeta.name) / "rebobina.db"
conn5 = sqlite3.connect(db5)
conn5.executescript(SCHEMA)
escribir(conn5, todas)
lector5 = LectorIncremental(str(db5))
lector5.avanzar()
cursor_lleno, obs_lleno = lector5.cursor, lector5.observaciones
puntos_lleno = sum(len(t["points"]) for t in lector5.trazas()[0])
revisar("con la base entera el lector la absorbe", cursor_lleno == len(todas)
        and puntos_lleno > 0, f"cursor={cursor_lleno} puntos={puntos_lleno}")
revisar("y no reporta ningun rebobinado", lector5.rebobinados == 0)

# La base se BORRA y se recrea desde cero: los ids vuelven a 1.
conn5.close()
db5.unlink()
conn5 = sqlite3.connect(db5)
conn5.executescript(SCHEMA)
escribir(conn5, todas[:30])
av = lector5.avanzar()
revisar("el lector detecta que la base retrocedio", av["rebobinado"] is not None,
        f"rebobinado={av['rebobinado']!r}")
revisar("y lo dice con el motivo y los dos numeros",
        av["rebobinado"] is not None
        and str(cursor_lleno) in av["rebobinado"]["motivo"]
        and av["rebobinado"]["max_id"] == 30,
        f"{av['rebobinado']!r}")
revisar("el cursor vuelve al de la base nueva", lector5.cursor == 30,
        f"dio {lector5.cursor}")
revisar("los agregados se reconstruyen y no se acumulan sobre los viejos",
        lector5.observaciones == 30 and lector5.observaciones < obs_lleno,
        f"observaciones={lector5.observaciones} contra {obs_lleno}")
# Y lo mismo que si el lector hubiera nacido ahora: reconstruir tiene que dar
# exactamente lo de una lectura limpia, o el mapa quedaria mezclando dos bases.
limpio = LectorIncremental(str(db5)); limpio.avanzar()
revisar("y dan lo mismo que un lector nuevo sobre la base nueva",
        jd(lector5.coverage()) == jd(limpio.coverage())
        and jd(lector5.trazas()[0]) == jd(limpio.trazas()[0]))

# El otro rebobinado, el que NO mueve max(id): un DELETE en el medio.
conn5.execute("DELETE FROM adsb_log WHERE id BETWEEN 5 AND 14")
conn5.commit()
av = lector5.avanzar()
revisar("un DELETE en el medio tambien se detecta (max(id) no cambia)",
        av["rebobinado"] is not None and lector5.observaciones == 20,
        f"rebobinado={av['rebobinado']!r} observaciones={lector5.observaciones}")
revisar("el contador de rebobinados se publica y no se pisa",
        lector5.rebobinados == 2, f"dio {lector5.rebobinados}")

# Y el camino normal no puede quedar tocado: filas nuevas siguen entrando.
escribir(conn5, todas[30:50])
av = lector5.avanzar()
revisar("despues de reconstruir, las filas nuevas siguen entrando",
        av["rebobinado"] is None and av["filas"] == 20,
        f"rebobinado={av['rebobinado']!r} filas={av['filas']}")
print(f"       -> {lector5.rebobinados} rebobinados detectados, "
      f"cursor final {lector5.cursor}")
print()


print("6. ventana_h invalido: se explica, no revienta ni se reinterpreta callado")
if cliente is not None and (Path(__file__).resolve().parent / "adsb_log.db").exists():
    for ruta in ("/api/adsb/mapa", "/api/aeropuerto/mapa"):
        # 1e400 llegaba como inf y reventaba con HTTP 500 al serializar
        # ("Out of range float values are not JSON compliant"); nan y -5 caian
        # por `ventana_s > 0` = False y devolvian la grabacion ENTERA sin avisar.
        for valor in ("1e400", "nan", "-5"):
            r = cliente.get(f"{ruta}?ventana_h={valor}")
            revisar(f"{ruta}?ventana_h={valor} responde 200", r.status_code == 200,
                    f"dio {r.status_code}")
            if r.status_code != 200:
                continue
            d = r.json()
            revisar(f"{ruta}?ventana_h={valor} dice que lo rechazo",
                    bool(d.get("ventana_nota")), f"nota={d.get('ventana_nota')!r}")
            revisar(f"{ruta}?ventana_h={valor} cae en la ventana por defecto",
                    d.get("ventana_h") == 3.0, f"dio {d.get('ventana_h')!r}")
        # 0 sigue significando "toda la grabacion", sin nota
        d0 = cliente.get(f"{ruta}?ventana_h=0").json()
        revisar(f"{ruta}?ventana_h=0 sigue pidiendo la grabacion entera",
                d0.get("ventana_h") is None and not d0.get("ventana_nota"),
                f"ventana_h={d0.get('ventana_h')!r} nota={d0.get('ventana_nota')!r}")
    print("       -> inf, nan y -5 contestan 200 y publican el motivo")
else:
    print("       -> no hay adsb_log.db; los endpoints no se ejercitan")
print()

# ---------------------------------------------------------------------------
# 8. Pasadas y carrera de pista: los dos bugs del 2026-09-03
#
# Se prueba con observaciones construidas y no esperando que pase un avion,
# porque lo que hay que fijar es el CRITERIO, no lo que dio un dia. Los dos
# bugs eran silenciosos: no fallaba nada, solo salian numeros equivocados.
# ---------------------------------------------------------------------------
print("8. Pasadas y carrera de pista")

import aeropuerto as _apt

_CIL = _apt.geometria_cilindro("SABE")
if _CIL is None:
    print("       -> no se pudo ubicar SABE; no se ejercita")
else:
    _LAT, _LON = _CIL["lat"], _CIL["lon"]

    def _obs(t, alt, *, vel=None, icao="e8061b", lat=None, lon=None):
        """Una observacion sobre la pista de SABE salvo que se diga otra cosa."""
        return Observation(timestamp=t, icao24=icao,
                           latitude=(_LAT if lat is None else lat),
                           longitude=(_LON if lon is None else lon),
                           altitude_ft=alt, ground_speed_kt=vel)

    def _tipos(filas):
        inf = _apt.informe(filas, "SABE")
        return sorted((o.pasada, o.tipo) for o in inf.operaciones), inf

    # -- el bug 1: dos visitas separadas son dos operaciones ----------------
    # Un aterrizaje (baja 2000 -> 100) y, tres horas despues, un despegue
    # (sube 100 -> 2000). Agrupando por direccion los extremos dan
    # baja=1900 y sube=1900 sobre una minima de 100 ft: la firma exacta de un
    # motor y al aire, que es como se fabricaban las 7 frustradas del tablero.
    T = 1_780_000_000.0
    aterriza = [_obs(T + i * 10, a) for i, a in enumerate((2100.0, 1200.0, 400.0, 116.0))]
    despega = [_obs(T + 10800 + i * 10, a) for i, a in enumerate((116.0, 400.0, 1200.0, 2100.0))]

    tipos, inf = _tipos(aterriza + despega)
    revisar("dos visitas separadas por 3 h dan DOS operaciones",
            len(tipos) == 2, f"dio {tipos}")
    revisar("y son aterrizaje y despegue, no una frustrada",
            [t for _, t in tipos] == ["aterrizaje", "despegue"], f"dio {tipos}")
    revisar("las pasadas se numeran 0 y 1",
            [p for p, _ in tipos] == [0, 1], f"dio {tipos}")
    revisar("pasadas_en_cilindro=2 con aeronaves_en_cilindro=1",
            inf.pasadas_en_cilindro == 2 and inf.aeronaves_en_cilindro == 1,
            f"pasadas={inf.pasadas_en_cilindro} aeronaves={inf.aeronaves_en_cilindro}")
    revisar("y la suma de categorias cuadra contra PASADAS",
            inf.categorias_cuadran,
            f"{inf.suma_categorias} vs {inf.pasadas_en_cilindro}")

    # Juntas dentro de la misma pasada SI tienen que dar una frustrada: el
    # criterio no cambio, solo dejo de aplicarse a dias distintos.
    seguido = aterriza + [_obs(T + 40 + i * 10, a) for i, a in
                          enumerate((400.0, 1200.0, 2100.0))]
    tipos_seguido, _ = _tipos(seguido)
    revisar("bajar y volver a subir SIN hueco sigue siendo una frustrada",
            [t for _, t in tipos_seguido] == ["frustrada"], f"dio {tipos_seguido}")

    # El hueco es configurable y el default son 600 s. Con 601 s corta.
    justo = aterriza + [_obs(T + 30 + _apt.HUECO_PASADA_S + 1 + i * 10, a)
                        for i, a in enumerate((116.0, 400.0, 1200.0, 2100.0))]
    revisar(f"un hueco de {_apt.HUECO_PASADA_S:.0f} s + 1 ya corta la pasada",
            len(_tipos(justo)[0]) == 2, f"dio {_tipos(justo)[0]}")

    # -- el bug 2: sin altitud, la carrera de pista dice el sentido ---------
    # Mensajes de superficie: altitud 0.0 es el placeholder, no una medicion,
    # asi que no hay baja ni sube. Antes esto salia "en tierra" siempre.
    frena = [_obs(T + i * 10, 0.0, vel=v) for i, v in enumerate((90.0, 60.0, 20.0, 4.0))]
    acelera = [_obs(T + i * 10, 0.0, vel=v) for i, v in enumerate((5.0, 25.0, 70.0, 130.0))]
    rodaje = [_obs(T + i * 10, 0.0, vel=v) for i, v in enumerate((8.0, 12.0, 9.0, 6.0))]

    revisar("frenando de 90 a 4 kt sobre el campo es un ATERRIZAJE",
            [t for _, t in _tipos(frena)[0]] == ["aterrizaje"], f"dio {_tipos(frena)[0]}")
    revisar("acelerando de 5 a 130 kt es un DESPEGUE",
            [t for _, t in _tipos(acelera)[0]] == ["despegue"], f"dio {_tipos(acelera)[0]}")
    revisar("rodando por debajo de la carrera queda 'en tierra', no se inventa",
            [t for _, t in _tipos(rodaje)[0]] == ["en tierra"], f"dio {_tipos(rodaje)[0]}")
    revisar("y la operacion publica la evidencia que la sostiene",
            _apt.informe(frena, "SABE").operaciones[0].carrera == "frena")
    revisar("sentido_de_carrera no opina cuando no hay velocidad",
            _apt.sentido_de_carrera(
                {"sup_vel_max": None, "sup_vel_primera": None,
                 "sup_vel_ultima": None}) is None)
    print(f"       -> {len(tipos)} operaciones donde antes habia 1, y la carrera "
          f"de pista resuelve el caso sin altitud")
print()

conn.close(); conn2.close(); conn3.close(); conn4.close(); conn5.close()
carpeta.cleanup()
print("=" * 55)
print("TODO CORRECTO" if not fallos else f"FALLAS: {fallos}")
raise SystemExit(1 if fallos else 0)
