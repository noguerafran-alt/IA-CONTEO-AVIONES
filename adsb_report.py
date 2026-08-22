"""Resumir lo grabado por ADS-B: una fila por aeronave, no una por mensaje.

Un mensaje ADS-B suelto casi nunca sirve para nada: trae la altitud O la
velocidad O el distintivo de vuelo, rara vez dos. Medido sobre lo grabado en
esta antena, sobre 3523 observaciones de 75 aeronaves:

    campo             en los mensajes    en las aeronaves
    icao24                      100%                 100%
    altitud                      51%                  96%
    velocidad                    30%                  95%
    distintivo de vuelo           3%                  63%

Esa diferencia entre columnas es todo el punto de este modulo. El distintivo
aparece en 3 de cada 100 mensajes, pero acumulando en el tiempo se termina
conociendo el de casi dos tercios de las aeronaves. Por eso se resume por
aeronave (quedandose con el ultimo valor conocido de cada campo) en vez de
mostrar los mensajes crudos, que se ven casi vacios.

La matricula y el tipo NO viajan por la radio: el avion transmite su direccion
ICAO24, y con eso se busca en el registro local (aircraft_db.py). Si la base no
esta descargada, esas columnas quedan vacias y el resto igual funciona.

Uso:
  python adsb_report.py --db adsb_log.db
  python adsb_report.py output/adsb/adsb_2026-08-22.csv
"""
from __future__ import annotations

from dataclasses import dataclass, field

from adsb import Observation
from adsb_events import EventDetector, coverage_report

try:
    import aircraft_db
except Exception:      # la base es opcional: sin ella se informa igual
    aircraft_db = None


@dataclass
class AircraftSummary:
    """Todo lo que se sabe de una aeronave, juntando todos sus mensajes."""
    icao24: str
    messages: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    callsign: str | None = None
    registration: str | None = None
    aircraft_type: str | None = None
    operator: str | None = None
    min_altitude_ft: float | None = None
    max_altitude_ft: float | None = None
    max_speed_kt: float | None = None
    max_climb_fpm: float | None = None
    max_descent_fpm: float | None = None
    events: list = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.last_seen - self.first_seen

    @property
    def phase(self) -> str:
        """En que estaba la aeronave, resumido en una palabra."""
        if any(e.event_type == "landing" for e in self.events):
            return "aterrizo"
        if any(e.event_type == "takeoff" for e in self.events):
            return "despego"
        if self.min_altitude_ft is None:
            return "sin altitud"
        if self.min_altitude_ft <= 200:
            return "en tierra"
        if self.min_altitude_ft < 3000:
            return "maniobra baja"
        if self.min_altitude_ft < 10000:
            return "ascenso/descenso"
        return "en crucero"

    @property
    def identified(self) -> bool:
        return bool(self.callsign or self.registration)


def _merge(summary: AircraftSummary, observation: Observation) -> None:
    """Acumular una observacion parcial sobre lo ya conocido."""
    summary.messages += 1
    if not summary.first_seen:
        summary.first_seen = observation.timestamp
    summary.last_seen = max(summary.last_seen, observation.timestamp)
    if observation.callsign:
        summary.callsign = observation.callsign.strip()
    if observation.registration:
        summary.registration = observation.registration
    altitude = observation.altitude_ft
    if altitude is not None:
        summary.min_altitude_ft = (altitude if summary.min_altitude_ft is None
                                   else min(summary.min_altitude_ft, altitude))
        summary.max_altitude_ft = (altitude if summary.max_altitude_ft is None
                                   else max(summary.max_altitude_ft, altitude))
    speed = observation.ground_speed_kt
    if speed is not None:
        summary.max_speed_kt = speed if summary.max_speed_kt is None else max(summary.max_speed_kt, speed)
    rate = observation.vertical_rate_fpm
    if rate is not None:
        if rate > 0:
            summary.max_climb_fpm = rate if summary.max_climb_fpm is None else max(summary.max_climb_fpm, rate)
        elif rate < 0:
            summary.max_descent_fpm = rate if summary.max_descent_fpm is None else min(summary.max_descent_fpm, rate)


def _enrich(summary: AircraftSummary) -> None:
    """Completar matricula/tipo/operador desde el registro local por ICAO24."""
    if aircraft_db is None or not aircraft_db.available():
        return
    entry = aircraft_db.lookup(summary.icao24)
    if not entry:
        return
    summary.registration = summary.registration or (entry.get("registration") or None)
    summary.aircraft_type = aircraft_db.describe_type(entry)
    summary.operator = (entry.get("operator") or entry.get("owner") or None)


def summarize(observations: list[Observation]) -> list[AircraftSummary]:
    """Una fila por aeronave, ordenadas por la mas reciente primero."""
    ordered = sorted(observations, key=lambda o: o.timestamp)

    summaries: dict[str, AircraftSummary] = {}
    for observation in ordered:
        summary = summaries.get(observation.icao24)
        if summary is None:
            summary = summaries[observation.icao24] = AircraftSummary(icao24=observation.icao24)
        _merge(summary, observation)

    # Los eventos se detectan sobre el flujo completo, no por aeronave: el
    # detector necesita ver la secuencia en orden para reconocer transiciones.
    detector = EventDetector()
    for observation in ordered:
        event = detector.feed(observation)
        if event and event.icao24 in summaries:
            summaries[event.icao24].events.append(event)

    for summary in summaries.values():
        _enrich(summary)
    return sorted(summaries.values(), key=lambda s: s.last_seen, reverse=True)


def field_coverage(observations: list[Observation]) -> list[dict]:
    """Cuanto aporta cada campo, por mensaje y por aeronave.

    Las dos medidas juntas porque solas enganan: "3% de los mensajes traen
    distintivo" suena a que casi no se identifica nada, cuando en realidad se
    conoce el de la mayoria de las aeronaves.
    """
    total = len(observations) or 1
    aircraft = {o.icao24 for o in observations} or {""}
    campos = [
        ("icao24", "Direccion ICAO24", lambda o: bool(o.icao24), "transmitido en todos los mensajes"),
        ("callsign", "Distintivo de vuelo", lambda o: bool(o.callsign), "transmitido cada ~5 s"),
        ("altitude_ft", "Altitud", lambda o: o.altitude_ft is not None, "transmitido"),
        ("ground_speed_kt", "Velocidad", lambda o: o.ground_speed_kt is not None, "transmitido"),
        ("vertical_rate_fpm", "Regimen vertical", lambda o: o.vertical_rate_fpm is not None, "transmitido"),
        ("position", "Posicion", lambda o: o.latitude is not None, "PENDIENTE: no decodifica"),
    ]
    filas = []
    for clave, nombre, presente, origen in campos:
        con_dato = [o for o in observations if presente(o)]
        aviones = {o.icao24 for o in con_dato}
        filas.append({
            "key": clave, "name": nombre, "origin": origen,
            "messages": len(con_dato), "messages_pct": len(con_dato) / total * 100,
            "aircraft": len(aviones), "aircraft_pct": len(aviones) / len(aircraft) * 100,
        })
    return filas


def overview(observations: list[Observation]) -> dict:
    """Todo lo que la pagina de analisis necesita, en una sola pasada."""
    summaries = summarize(observations)
    cobertura = coverage_report(observations)
    return {
        "aircraft": summaries,
        "coverage": cobertura,
        "fields": field_coverage(observations),
        "observations": len(observations),
        "identified": sum(1 for s in summaries if s.identified),
        "with_registration": sum(1 for s in summaries if s.registration),
        "events": [e for s in summaries for e in s.events],
        "registry_available": bool(aircraft_db and aircraft_db.available()),
    }


if __name__ == "__main__":
    import argparse

    from adsb_events import _load_csv, _load_db

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", nargs="?", help="CSV grabado por adsb_record.py")
    parser.add_argument("--db", help="Leer de adsb_log.db en vez de un CSV")
    args = parser.parse_args()

    if args.db:
        datos, origen = _load_db(args.db), args.db
    elif args.csv:
        datos, origen = _load_csv(args.csv), args.csv
    else:
        parser.error("indica un CSV o --db")

    info = overview(datos)
    print(f"=== {origen}")
    print(f"{info['observations']} observaciones, {len(info['aircraft'])} aeronaves, "
          f"{info['identified']} identificadas\n")

    print(f"{'ICAO24':8s} {'VUELO':9s} {'MATRICULA':10s} {'TIPO':22s} "
          f"{'ALT MIN':>8s} {'ALT MAX':>8s} {'VEL':>6s}  ESTADO")
    print("-" * 100)
    for s in info["aircraft"]:
        def num(value):
            return f"{value:.0f}" if value is not None else "-"
        print(f"{s.icao24:8s} {(s.callsign or '-'):9s} {(s.registration or '-'):10s} "
              f"{(s.aircraft_type or '-')[:22]:22s} "
              f"{num(s.min_altitude_ft):>8s} {num(s.max_altitude_ft):>8s} "
              f"{num(s.max_speed_kt):>6s}  {s.phase}")
