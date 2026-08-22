"""Exercise landing/takeoff detection from ADS-B, especially the false positives.

No antenna needed: each scenario is built, so the awkward cases can be tested
deliberately instead of waiting for one to happen. The ones that matter are the
situations that look like an operation but are not -- a reception gap, a single
odd message, an aircraft descending toward the airport but never landing.
"""
import time

from adsb import Observation
from adsb_events import EventDetector, coverage_report, detect

T0 = 1_800_000_000.0
fallos = []


def revisar(nombre, condicion, detalle=""):
    print(f"  {'OK   ' if condicion else 'FALLA'} {nombre}" + (f"  -- {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def obs(offset, icao="e06541", alt=None, vr=None, gs=None, vuelo=None):
    return Observation(timestamp=T0 + offset, icao24=icao, callsign=vuelo,
                       altitude_ft=alt, vertical_rate_fpm=vr, ground_speed_kt=gs)


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

print("=" * 55)
print("TODO CORRECTO" if not fallos else f"FALLAS: {fallos}")
raise SystemExit(1 if fallos else 0)
