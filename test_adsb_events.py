"""Exercise landing/takeoff detection from ADS-B, especially the false positives.

No antenna needed: each scenario is built, so the awkward cases can be tested
deliberately instead of waiting for one to happen. The ones that matter are the
situations that look like an operation but are not -- a reception gap, a single
odd message, an aircraft descending toward the airport but never landing.
"""
import time

from adsb import Observation
from adsb_events import (EventDetector, PositionGate, apply_position_gate,
                         coverage_report, detect)
from receiver import horizonte_km, limite_posicion_km

T0 = 1_800_000_000.0
fallos = []


def revisar(nombre, condicion, detalle=""):
    print(f"  {'OK   ' if condicion else 'FALLA'} {nombre}" + (f"  -- {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def obs(offset, icao="e06541", alt=None, vr=None, gs=None, vuelo=None,
        lat=None, lon=None):
    return Observation(timestamp=T0 + offset, icao24=icao, callsign=vuelo,
                       altitude_ft=alt, vertical_rate_fpm=vr, ground_speed_kt=gs,
                       latitude=lat, longitude=lon)


print("1. Aterrizaje: desciende y toca pista")
serie = [
    obs(0, alt=3000, vr=-800), obs(10, alt=2000, vr=-900), obs(20, alt=1000, vr=-700),
    obs(30, alt=300, vr=-500), obs(40, alt=0), obs(50, alt=0), obs(60, alt=0, gs=15),
]
eventos = detect(serie)
revisar("detecta un aterrizaje", len(eventos) == 1 and eventos[0].event_type == "landing",
        f"dio {[(e.event_type, e.reason) for e in eventos]}")
print(f"       -> {eventos[0].event_type if eventos else 'nada'}"
      + (f" ({eventos[0].reason})" if eventos else "") + "\n")

print("2. Despegue: en pista y empieza a subir")
serie = [
    obs(0, alt=0, gs=5), obs(10, alt=0, gs=40), obs(20, alt=0, gs=120),
    obs(30, alt=500, vr=2500), obs(40, alt=1500, vr=2800), obs(50, alt=3000, vr=2600),
]
eventos = detect(serie)
revisar("detecta un despegue", len(eventos) == 1 and eventos[0].event_type == "takeoff",
        f"dio {[(e.event_type, e.reason) for e in eventos]}")
print(f"       -> {eventos[0].event_type if eventos else 'nada'}\n")

print("3. Avion en crucero que solo pasa: NO es un evento")
serie = [obs(i * 10, alt=35000) for i in range(10)]
eventos = detect(serie)
revisar("no inventa eventos en crucero", len(eventos) == 0, f"dio {len(eventos)}")
print(f"       -> {len(eventos)} eventos\n")

print("4. Desciende hacia el aeropuerto pero NO aterriza (aproximacion frustrada)")
serie = [
    obs(0, alt=8000, vr=-1200), obs(10, alt=6000, vr=-1500), obs(20, alt=4000, vr=-1000),
    obs(30, alt=3000, vr=-800), obs(40, alt=3500, vr=900), obs(50, alt=5000, vr=1400),
]
eventos = detect(serie)
revisar("no cuenta un aterrizaje que no ocurrio",
        all(e.event_type != "landing" for e in eventos),
        f"dio {[(e.event_type, e.reason) for e in eventos]}")
print(f"       -> {[e.event_type for e in eventos] or 'nada'}\n")

print("5. Corte de senal: se pierde en el aire y reaparece en tierra")
serie = [
    obs(0, alt=5000, vr=-600), obs(10, alt=4000, vr=-700),
    # 15 minutos sin recibir nada
    obs(900, alt=0), obs(910, alt=0), obs(920, alt=0),
]
detector = EventDetector()
eventos = [e for o in serie if (e := detector.feed(o))]
revisar("no inventa un aterrizaje a traves del corte", len(eventos) == 0, f"dio {len(eventos)}")
revisar("registra que hubo un corte", detector.gaps_skipped == 1, f"conto {detector.gaps_skipped}")
print(f"       -> {len(eventos)} eventos, {detector.gaps_skipped} cortes detectados\n")

print("6. Mensaje suelto erroneo: una sola lectura rara no crea un evento")
serie = [
    obs(0, alt=10000), obs(10, alt=10000), obs(20, alt=0),   # lectura anomala
    obs(30, alt=10000), obs(40, alt=10000),
]
eventos = detect(serie)
revisar("ignora la lectura aislada", len(eventos) == 0, f"dio {[(e.event_type, e.reason) for e in eventos]}")
print(f"       -> {len(eventos)} eventos\n")

print("7. Avion que aparece ya en tierra: no cuenta como aterrizaje")
serie = [obs(0, alt=0), obs(10, alt=0), obs(20, alt=0, gs=10)]
eventos = detect(serie)
revisar("no inventa aterrizaje al primer avistaje", len(eventos) == 0, f"dio {len(eventos)}")
print(f"       -> {len(eventos)} eventos\n")

print("8. Mensajes parciales: altitud y velocidad vertical llegan por separado")
# Asi llegan de verdad -- en los datos reales grabados, 33 de 48 observaciones
# traian altitud y solo 6 traian velocidad vertical.
serie = [
    obs(0, alt=3000), obs(5, vr=-800), obs(10, alt=1500),
    obs(15, vr=-900), obs(20, alt=200), obs(25, alt=0), obs(30, alt=0),
]
eventos = detect(serie)
revisar("arma el estado con mensajes incompletos",
        len(eventos) == 1 and eventos[0].event_type == "landing",
        f"dio {[(e.event_type, e.reason) for e in eventos]}")
print(f"       -> {eventos[0].event_type if eventos else 'nada'}\n")

print("9. Dos aviones a la vez: no se mezclan entre si")
serie = sorted(
    [obs(i, icao="aaa111", alt=3000 - i * 100, vr=-800) for i in range(0, 30, 10)]
    + [obs(i, icao="aaa111", alt=0) for i in (30, 40)]
    + [obs(i, icao="bbb222", alt=0, gs=100) for i in (0, 10)]
    + [obs(i, icao="bbb222", alt=1000 + i * 50, vr=2500) for i in (20, 30, 40)],
    key=lambda o: o.timestamp)
eventos = detect(serie)
tipos = {e.icao24: e.event_type for e in eventos}
revisar("aterrizaje del primero", tipos.get("aaa111") == "landing", f"dio {tipos}")
revisar("despegue del segundo", tipos.get("bbb222") == "takeoff", f"dio {tipos}")
print(f"       -> {tipos}\n")

print("10. Reporte de cobertura: distingue 'sin eventos' de 'sin alcance'")
alto = [obs(i * 10, alt=30000) for i in range(5)]
cob = coverage_report(alto)
revisar("marca que no llega a nivel de pista", not cob["can_see_runway_level"])
bajo = [obs(i * 10, alt=0) for i in range(5)]
revisar("marca que si llega", coverage_report(bajo)["can_see_runway_level"])
print(f"       -> solo en altura: alcanza pista = {cob['can_see_runway_level']}\n")

print("11. Cobertura horizontal: hasta donde llega la antena, no solo hasta que altura")
# El otro eje de la misma pregunta, y el que no se podia medir mientras la
# posicion salia None. Coordenadas de los tres aeropuertos de la zona vistos
# desde San Isidro: 7.3, 13.3 y 39.1 km medidos con el haversine del repo.
cerca_y_lejos = [
    obs(0, lat=-34.4532, lon=-58.5896, alt=0),      # San Fernando,  7.3 km
    obs(10, lat=-34.55942, lon=-58.41554, alt=0),   # Aeroparque,   13.3 km
    obs(20, lat=-34.8222, lon=-58.5358, alt=0),     # Ezeiza,       39.1 km
]
cob = coverage_report(cerca_y_lejos)
revisar("cuenta las observaciones con posicion", cob["with_position"] == 3,
        f"conto {cob['with_position']}")
revisar("la minima es el aeropuerto mas cercano", abs(cob["min_distance_km"] - 7.3) < 0.5,
        f"dio {cob['min_distance_km']}")
revisar("la maxima es el alcance real", abs(cob["max_distance_km"] - 39.1) < 0.5,
        f"dio {cob['max_distance_km']}")
# Sin posiciones no hay alcance que informar, y tiene que quedar en None en vez
# de en 0: un cero se leeria como "la antena no llega a ninguna parte".
sin_pos = coverage_report([obs(i * 10, alt=30000) for i in range(5)])
revisar("sin posiciones informa None, no 0",
        sin_pos["with_position"] == 0 and sin_pos["max_distance_km"] is None,
        f"dio {sin_pos['max_distance_km']}")
revisar("una lista vacia no rompe", coverage_report([])["p95_distance_km"] is None)
print(f"       -> alcance {cob['min_distance_km']:.1f} a {cob['max_distance_km']:.1f} km, "
      f"p95 {cob['p95_distance_km']:.1f} km\n")

print("12. La mediana aguanta la cola larga; el p95, con pocas muestras, no")
# Reproduce la forma real de lo grabado: casi todo cerca y unas pocas
# posiciones de un avion de crucero muy lejos. Con esa forma el p95 cae DENTRO
# del grupo lejano en vez de recortarlo, que es la razon por la que la interfaz
# muestra la mediana. Si alguna vez el p95 vuelve a parecer el numero correcto
# para mostrar, este test explica por que no lo es.
cerca = [obs(i, lat=-34.4532, lon=-58.5896, alt=1000) for i in range(39)]   # 7.3 km
lejos = [obs(100 + i, lat=-35.10, lon=-58.42, alt=36000) for i in range(4)]  # ~70 km
cob = coverage_report(cerca + lejos)
revisar("la mediana se queda con el grueso", abs(cob["median_distance_km"] - 7.3) < 0.5,
        f"dio {cob['median_distance_km']}")
revisar("el p95 se va con la cola", cob["p95_distance_km"] > 60,
        f"dio {cob['p95_distance_km']}")
revisar("y por eso mediana y p95 no son intercambiables",
        cob["p95_distance_km"] - cob["median_distance_km"] > 50)
revisar("sin posiciones la mediana tambien es None",
        coverage_report([])["median_distance_km"] is None)
print(f"       -> mediana {cob['median_distance_km']:.1f} km vs p95 "
      f"{cob['p95_distance_km']:.1f} km sobre {cob['with_position']} posiciones\n")

print("13. tracks(): agrupa por aeronave y descarta las que no tienen posicion")
import adsb_report

mezcla = [
    obs(0, icao="aaa111", lat=-34.50, lon=-58.50, alt=2000),
    obs(10, icao="bbb222", alt=30000),                        # sin posicion nunca
    obs(20, icao="aaa111", lat=-34.52, lon=-58.52, alt=1500),
    obs(30, icao="ccc333", lat=-34.60, lon=-58.40, alt=0),    # una sola posicion
]
trazas = adsb_report.tracks(mezcla)
revisar("solo las aeronaves con posicion", {t["icao24"] for t in trazas} == {"aaa111", "ccc333"},
        f"dio {[t['icao24'] for t in trazas]}")
revisar("junta todos los puntos de una aeronave",
        next(t for t in trazas if t["icao24"] == "aaa111")["points"].__len__() == 2)
# Ordenados por tiempo y no por orden de llegada: el mapa dibuja la polilinea
# en el orden en que vienen, y desordenados dibujaria un zigzag inventado.
puntos = next(t for t in trazas if t["icao24"] == "aaa111")["points"]
revisar("en orden cronologico", puntos[0]["t"] < puntos[1]["t"])
revisar("cada punto trae su distancia al receptor", all(p["km"] is not None for p in puntos))
# La altitud va pegada al punto, no tomada del ultimo valor de la aeronave: el
# color del trazo codifica la altitud DE ESE TRAMO.
revisar("la altitud es la del punto, no la ultima de la aeronave",
        [p["alt"] for p in puntos] == [2000, 1500], f"dio {[p['alt'] for p in puntos]}")
revisar("sin observaciones no hay trazas", adsb_report.tracks([]) == [])
print(f"       -> {len(trazas)} trazas de 3 aeronaves ("
      f"{[t['icao24'] + ':' + str(len(t['points'])) for t in trazas]})\n")

print("14. El caso real: una posicion imposible entre dos tramos correctos")
# NO es un escenario inventado. Son las filas textuales de adsb_log.db para
# e0b14a, un avion en descenso hacia Aeroparque la tarde del 2026-08-22, con
# sus epochs y sus coordenadas exactas. La del medio dice que en 35.7 segundos
# se fue a Misiones y volvio: es un error de indice de zona CPR, la latitud
# emitida cae exactamente una zona impar de mas (360/59 = 6.101695 deg contra
# los +6.101982 deg medidos, 32 m de diferencia).
#
# Este caso es el que justifica que exista el filtro. Si alguien lo
# "simplifica" a un corte fijo de 50 km, las cuatro legitimas del punto 15 se
# ponen en rojo.
REAL_E0B14A = [
    (1787429265.515, -34.15214538574219, -58.27546411631059, 20950.0, None),
    (1787429271.406, -34.155303955078125, -58.28448860012755, 20725.0, None),
    (1787429277.578, None, None, None, 297.0),          # solo velocidad: TC 19
    (1787429287.867, None, None, None, 295.0),
    (1787429307.067, -28.07276838916843, -54.906867532169116, 19525.0, None),  # el fantasma
    (1787429333.8, -34.18880139367059, -58.37871551513672, 18650.0, None),
    (1787429337.727, -34.19087219238281, -58.38459871253189, 18525.0, None),
    (1787429341.798, -34.193023681640625, -58.39070845623406, 18400.0, None),
]
traza = [Observation(timestamp=t, icao24="e0b14a", latitude=la, longitude=lo,
                     altitude_ft=alt, ground_speed_kt=gs)
         for t, la, lo, alt, gs in REAL_E0B14A]

filtradas, gate = apply_position_gate(traza)
revisar("rechaza exactamente una posicion", gate.rechazadas == 1,
        f"rechazo {gate.rechazadas}: {[(r.icao24, round(r.km)) for r in gate.rechazos]}")
r = gate.rechazos[0] if gate.rechazos else None
revisar("y es la de 789.5 km", r is not None and abs(r.km - 789.5) < 0.5,
        f"dio {r.km if r else None}")
revisar("el motivo es el horizonte de radio", r is not None and r.motivo == "horizonte",
        f"dio {r.motivo if r else None}")
# 2.384x el horizonte. El salto entre esto y la segunda peor de toda la base
# (0.208, e49aa8) es de 11.5x: no es un caso de borde, es otro mundo.
revisar("viola el horizonte por mas del doble",
        r is not None and r.km / horizonte_km(19525) > 2.0,
        f"ratio {r.km / horizonte_km(19525) if r else None:.2f}")
# El filtro NO borra la fila: la trama era autentica y la altitud de 19525 ft
# encaja con los -2048 fpm transmitidos en las dos ramas del descenso. Lo unico
# corrupto era el par lat/lon.
mala = next(o for o in filtradas if o.timestamp == 1787429307.067)
revisar("conserva la observacion, solo anula lat/lon",
        mala.latitude is None and mala.altitude_ft == 19525.0,
        f"lat={mala.latitude} alt={mala.altitude_ft}")
revisar("las dos posiciones buenas de despues quedan",
        sum(1 for o in filtradas if o.latitude is not None) == 5,
        f"quedaron {sum(1 for o in filtradas if o.latitude is not None)}")
print(f"       -> {r.km:.1f} km a {r.altitude_ft:.0f} ft; horizonte "
      f"{horizonte_km(19525):.0f} km, limite {r.limite_km:.0f} km\n")

print("15. Las cuatro legitimas lejanas TIENEN que pasar")
# El usuario propuso cortar a 50 km de cada aeropuerto. Estas cuatro son
# posiciones reales de la base, con trazas continuas de varios puntos, y un
# corte de 50 km las tiraria todas -- rompiendo justamente la medicion de
# alcance de la antena, que es el proposito declarado de /adsb/mapa.
#
# Con el horizonte de radio ninguna se acerca a su limite: la peor pasa con
# 6.5x de margen.
LEGITIMAS = [
    ("e49bff", -35.12045132911811, -58.45499038696289, 36000.0, 72.4),
    ("e8061b", -34.563720703125, -59.274883659518494, 27950.0, 70.6),
    ("e49aa8", -34.022889218087926, -58.077049255371094, 16750.0, 63.9),
    ("e492aa", -34.05784606933594, -58.10764234893176, 37025.0, 59.1),
]
for icao, la, lo, alt, km_esperado in LEGITIMAS:
    g = PositionGate()
    motivo = g.feed(Observation(timestamp=T0, icao24=icao, latitude=la,
                                longitude=lo, altitude_ft=alt))
    limite = limite_posicion_km(alt)
    margen = limite / km_esperado
    revisar(f"{icao} a {km_esperado} km @{alt:.0f} ft pasa", motivo is None,
            f"la rechazo por {motivo}")
    # No alcanza con que pase: tiene que pasar HOLGADA. Si algun dia el margen
    # se acerca a 1, el filtro esta rozando trafico real y hay que discutirlo.
    revisar(f"{icao} pasa con margen de sobra", margen > 5.0, f"margen {margen:.1f}x")
    print(f"       -> {icao}: {km_esperado} km, horizonte {horizonte_km(alt):.0f} km, "
          f"limite {limite:.0f} km, margen {margen:.1f}x")
print()

print("16. La referencia es la ultima ACEPTADA, no la ultima recibida")
# Este es el detalle que hace la diferencia entre 1 rechazo y 2, medido sobre
# los datos reales de arriba. Si se compara contra la ultima RECIBIDA, la
# posicion BUENA de 20:08:53 se mide contra el fantasma de Misiones y tambien
# cae: un falso positivo en cadena. Es la razon por la que dump1090 nunca
# guarda una posicion rechazada como referencia, y es el bug que tiene pyModeS
# (_update_position_history, _pipe.py:668-685, mete al historial tambien las
# rechazadas, y por eso el SEGUNDO fantasma de una rafaga pasa).
#
# Si alguien "simplifica" el gate para guardar siempre la ultima, este test se
# pone en rojo y muestra cual es la buena que se pierde.
class GateUltimaRecibida(PositionGate):
    def feed(self, observation):
        motivo = super().feed(observation)
        if observation.latitude is not None:
            self._aceptada[observation.icao24] = (observation.timestamp,
                                                  observation.latitude,
                                                  observation.longitude)
        return motivo

ingenuo = GateUltimaRecibida()
for o in traza:
    ingenuo.feed(o)
revisar("con la ultima aceptada se rechaza 1", gate.rechazadas == 1,
        f"dio {gate.rechazadas}")
revisar("con la ultima recibida se rechazan 2", ingenuo.rechazadas == 2,
        f"dio {ingenuo.rechazadas}")
perdida = [r for r in ingenuo.rechazos if r.timestamp == 1787429333.8]
revisar("y la de mas es la posicion BUENA de 20:08:53", len(perdida) == 1,
        f"rechazo {[r.timestamp for r in ingenuo.rechazos]}")
print(f"       -> ultima aceptada: {gate.rechazadas} rechazo | "
      f"ultima recibida: {ingenuo.rechazadas} rechazos\n")

print("17. La continuidad atrapa lo que el horizonte nunca ve")
# Una zona de LONGITUD mal elegida a 36000 ft puede aterrizar a 200 km, muy por
# debajo del limite de horizonte de 600.8 km: R1 la deja pasar sin chistar. Es
# la falla que justifica que R2 exista como regla separada y no como un segundo
# umbral de la misma.
cerca = [
    Observation(timestamp=T0, icao24="cccddd", latitude=-34.50, longitude=-58.50,
                altitude_ft=36000, ground_speed_kt=450),
    Observation(timestamp=T0 + 10, icao24="cccddd", latitude=-34.50,
                longitude=-56.30, altitude_ft=36000),   # 200 km de golpe
]
_, g = apply_position_gate(cerca)
revisar("el salto cercano lo atrapa la velocidad", g.por_motivo["velocidad"] == 1,
        f"dio {g.por_motivo}")
revisar("y NO el horizonte (200 km < 600 km de limite)",
        g.por_motivo["horizonte"] == 0, f"dio {g.por_motivo}")
saltada = g.rechazos[0]
revisar("informa los kt imposibles", saltada.kt_implicita > 20000,
        f"dio {saltada.kt_implicita}")

# Un hueco largo no se compara: se acepta y se reinicia la referencia. Un caso
# real de estos datos lo motiva -- a90552 con un hueco de 8m38s dio una
# velocidad derivada de -112 kt, o sea negativa.
hueco = [
    Observation(timestamp=T0, icao24="eeefff", latitude=-34.50, longitude=-58.50,
                altitude_ft=30000, ground_speed_kt=450),
    Observation(timestamp=T0 + 600, icao24="eeefff", latitude=-34.90,
                longitude=-58.90, altitude_ft=30000),
]
_, g_hueco = apply_position_gate(hueco)
revisar("un hueco largo no genera falso positivo", g_hueco.rechazadas == 0,
        f"dio {g_hueco.por_motivo}")

# Sin altitud no hay horizonte que calcular: el unico numero defendible es el
# default de fabrica de dump1090-fa, 300 NM. Hoy no se ejercita (las 783 filas
# con posicion traen las 783 su altitud) pero json y sbs pueden traer una.
sin_alt = [Observation(timestamp=T0, icao24="111222", latitude=-25.00,
                       longitude=-52.00, altitude_ft=None)]
_, g_sin = apply_position_gate(sin_alt)
revisar("sin altitud aplica el techo de 300 NM", g_sin.por_motivo["sin_altitud"] == 1,
        f"dio {g_sin.por_motivo}")

# Superficie: mas alla de media celda CPR (+-0.75 deg de latitud) la posicion
# no es mala, es ininterpretable. Ver receiver.py.
en_tierra = [Observation(timestamp=T0, icao24="333444", latitude=-33.00,
                         longitude=-58.50, altitude_ft=0.0)]
_, g_sup = apply_position_gate(en_tierra)
revisar("superficie fuera de media celda CPR se rechaza",
        g_sup.por_motivo["superficie"] == 1, f"dio {g_sup.por_motivo}")
revisar("y una en pista de verdad no", apply_position_gate(
    [Observation(timestamp=T0, icao24="555666", latitude=-34.4532,
                 longitude=-58.5896, altitude_ft=0.0)])[1].rechazadas == 0)
print(f"       -> salto cercano: {saltada.salto_km:.0f} km en {saltada.dt_s:.0f} s "
      f"= {saltada.kt_implicita:.0f} kt\n")

print("18. El conteo de rechazos aparece en coverage_report, y el cero tambien")
cob = coverage_report(filtradas, gate)
revisar("publica cuantas rechazo", cob["positions_rejected"] == 1,
        f"dio {cob['positions_rejected']}")
revisar("publica cuantas evaluo", cob["positions_evaluated"] == 6,
        f"dio {cob['positions_evaluated']}")
revisar("desglosa por motivo",
        cob["rejected_by_reason"] == {"horizonte": 1, "velocidad": 0,
                                      "superficie": 0, "sin_altitud": 0},
        f"dio {cob['rejected_by_reason']}")
revisar("publica el km descartado mas grande", abs(cob["rejected_max_km"] - 789.5) < 0.5,
        f"dio {cob['rejected_max_km']}")
revisar("y el detalle auditable", len(cob["rejected_detail"]) == 1
        and cob["rejected_detail"][0]["icao24"] == "e0b14a")
# with_position cuenta solo las ACEPTADAS; observations y aircraft no cambian,
# porque la observacion se conserva entera.
revisar("with_position cuenta solo las aceptadas", cob["with_position"] == 5,
        f"dio {cob['with_position']}")
revisar("observations no cambia", cob["observations"] == len(traza),
        f"dio {cob['observations']}")
# El maximo publicado ya no puede ser el fantasma. Ese es todo el punto.
revisar("el alcance maximo ya no es el fantasma", cob["max_distance_km"] < 100,
        f"dio {cob['max_distance_km']}")
# Un contador en 0 que nadie sabe que nunca corrio es una mentira por omision.
revisar("declara que la regla de superficie no se ejercito",
        cob["surface_rule_exercised"] is False)
revisar("y declara que el filtro SI corrio", cob["gate_applied"] is True)

# Sin gate, las claves salen igual pero con gate_applied=False: "no hubo
# rechazos" y "nadie evaluo" no se pueden ver iguales.
sin_gate = coverage_report(filtradas)
revisar("sin filtro el cero se distingue de 'no evaluado'",
        sin_gate["positions_rejected"] == 0 and sin_gate["gate_applied"] is False)

# Lista vacia: ni el filtro ni el informe pueden romperse con cero datos.
vacias, g_vacio = apply_position_gate([])
revisar("una lista vacia no rompe el filtro",
        vacias == [] and g_vacio.rechazadas == 0 and g_vacio.evaluadas == 0)
cob_vacia = coverage_report([], g_vacio)
revisar("ni el informe", cob_vacia["positions_rejected"] == 0
        and cob_vacia["rejected_max_km"] is None
        and cob_vacia["max_distance_km"] is None)
revisar("y el desglose sale con los cuatro motivos en cero",
        set(cob_vacia["rejected_by_reason"]) == {"horizonte", "velocidad",
                                                 "superficie", "sin_altitud"},
        f"dio {cob_vacia['rejected_by_reason']}")
print(f"       -> {cob['positions_rejected']} de {cob['positions_evaluated']} "
      f"descartadas, maximo publicado {cob['max_distance_km']:.1f} km "
      f"(descartado {cob['rejected_max_km']} km)\n")

print("19. LectorIncremental: el cursor no puede perder ni repetir una fila")
# El mapa dejo de releer la base entera en cada refresco (153 ms sobre 10 873
# filas, y crece linealmente: 3.64 s a 29 dias) y pasa a avanzar un cursor. La
# falla mas probable de un cursor no hace ruido: una fila perdida deja un
# agujero en la traza y una repetida dibuja al avion dos veces en el mismo
# lugar, y las dos se ven perfectamente bien en pantalla.
#
# La equivalencia completa contra load_db -contadores, rechazos, tracks,
# coverage e informe del aeropuerto- vive en test_adsb_incremental.py, que corre
# solo. Aca queda lo que no puede faltar en el archivo del modulo que cambio.
import sqlite3 as _sq3
import tempfile as _tmp
from pathlib import Path as _P

from adsb_events import LectorIncremental
from adsb_record import COLUMNS, SCHEMA, as_row

_T = 1_800_000_000.0
_LAT, _LON = -34.47, -58.53


def _serie():
    filas = []
    for i in range(30):
        t = _T + i * 5.0
        icao = ["e06541", "e80413"][i % 2]
        filas.append(Observation(timestamp=t, icao24=icao, altitude_ft=8000.0 - i * 40,
                                 latitude=_LAT + 0.10 - i * 0.001,
                                 longitude=_LON - 0.10 + i * 0.001))
        # Mismo epoch, sin posicion: la base real tiene 598 pares consecutivos
        # con epoch repetido, y por eso el cursor NO puede ser epoch.
        filas.append(Observation(timestamp=t, icao24=icao, ground_speed_kt=250.0))
    return filas


with _tmp.TemporaryDirectory(ignore_cleanup_errors=True) as _dir:
    _db = _P(_dir) / "cursor.db"
    _conn = _sq3.connect(_db)
    _conn.executescript(SCHEMA)
    _conn.commit()

    def _escribir(obs):
        hueco = ",".join("?" * len(COLUMNS))
        for o in obs:
            fila = as_row(o)
            _conn.execute(f"INSERT INTO adsb_log ({','.join(COLUMNS)}) VALUES ({hueco})",
                          [fila[c] if fila[c] != "" else None for c in COLUMNS])
        _conn.commit()

    _todas = _serie()
    _lec = LectorIncremental(str(_db))

    d = _lec.avanzar()
    revisar("cursor vacio sobre base vacia: cero filas y cursor 0",
            d["filas"] == 0 and d["cursor"] == 0, f"dio {d}")

    _escribir(_todas[:20])
    d = _lec.avanzar()
    revisar("primera carga lee las 20 filas y deja el cursor en 20",
            d["desde"] == 0 and d["filas"] == 20 and d["cursor"] == 20, f"dio {d}")

    d = _lec.avanzar()
    revisar("cursor al dia: delta vacio, con el cero explicito",
            d["filas"] == 0 and d["posiciones"] == 0 and d["cursor"] == 20, f"dio {d}")

    _escribir(_todas[20:])
    d = _lec.avanzar()
    revisar("filas nuevas entre dos consultas: solo lee las nuevas",
            d["desde"] == 20 and d["filas"] == len(_todas) - 20, f"dio {d}")

    _completo = {t["icao24"]: t["points"] for t in _lec.trazas()[0]}
    revisar("un cursor viejo devuelve un sufijo exacto de cada traza",
            all(p == _completo[i][len(_completo[i]) - len(p):]
                for i, p in ((t["icao24"], t["points"])
                             for t in _lec.delta_trazas(13))))
    revisar("el delta desde 0 es exactamente la carga completa",
            {t["icao24"]: t["points"] for t in _lec.delta_trazas(0)} == _completo)
    revisar("el delta desde el ultimo id no trae nada",
            _lec.delta_trazas(_lec.cursor) == [])

    # Un cliente polleando de verdad: cada consulta manda como desde el cursor
    # que le devolvio la anterior. La union tiene que ser la carga completa.
    _lec2 = LectorIncremental(str(_db))
    _union, _c = {}, 0
    for _ in range(3):
        _lec2.avanzar()
        for t in _lec2.delta_trazas(_c):
            _union.setdefault(t["icao24"], []).extend(t["points"])
        _c = _lec2.cursor
    revisar("la union de los deltas encadenados es la carga completa",
            _union == {t["icao24"]: t["points"] for t in _lec2.trazas()[0]},
            f"{sum(len(v) for v in _union.values())} puntos contra "
            f"{sum(len(v) for v in _completo.values())}")
    revisar("y ningun punto viene repetido",
            all(len(v) == len({(p['t'], p['lat']) for p in v}) for v in _union.values()))

    # La ventana de dibujo NUNCA recorta en silencio: el numero de lo que quedo
    # afuera se publica, con el cero explicito cuando no descarto nada.
    _ft, _fp = _lec.fuera_de_ventana(10 ** 9)
    revisar("una ventana enorme no deja nada afuera, y lo dice con ceros",
            _ft == 0 and _fp == 0, f"dio {_ft}, {_fp}")
    _ft, _fp = _lec.fuera_de_ventana(1.0, ahora=_T + 10 ** 6)
    revisar("una ventana chica publica cuantas trazas y puntos recorto",
            _ft == len(_completo) and _fp == sum(len(v) for v in _completo.values()),
            f"dio {_ft}, {_fp}")
    print(f"       -> {sum(len(v) for v in _completo.values())} puntos, "
          f"cursor {_lec.cursor}, union de deltas identica a la completa")
    _conn.close()
print()

print("=" * 55)
print("TODO CORRECTO" if not fallos else f"FALLAS: {fallos}")
raise SystemExit(1 if fallos else 0)
