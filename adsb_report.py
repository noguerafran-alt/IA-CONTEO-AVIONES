"""Resumir lo grabado por ADS-B: una fila por aeronave, no una por mensaje.

Un mensaje ADS-B suelto casi nunca sirve para nada: trae la altitud O la
velocidad O el distintivo de vuelo, rara vez dos. Medido sobre lo grabado en
esta antena, sobre 3523 observaciones de 75 aeronaves:

    campo             en los mensajes    en las aeronaves
    icao24                      100%                 100%
    altitud                      51%                  96%
    velocidad                    30%                  95%
    distintivo de vuelo           3%                  63%
    posicion                      0%                   0%

Esa diferencia entre columnas es todo el punto de este modulo. El distintivo
aparece en 3 de cada 100 mensajes, pero acumulando en el tiempo se termina
conociendo el de casi dos tercios de las aeronaves. Por eso se resume por
aeronave (quedandose con el ultimo valor conocido de cada campo) en vez de
mostrar los mensajes crudos, que se ven casi vacios.

El 0% de posicion de esa tabla NO es una medicion de la antena: esas 3523
observaciones se grabaron con dos bugs encadenados que hacian imposible que
llegara una posicion (adsb_rtlsdr.py leia decoded['lat'] cuando pyModeS emite
'latitude', y los cargadores de adsb_events.py descartaban las columnas al
releerlas). Ambos estan arreglados, asi que la fila de posicion de una
grabacion NUEVA va a decir otra cosa; la de estas 3523 se queda en 0 para
siempre porque el dato nunca se escribio. Numeros de posicion medidos aparte,
por replay: el par CPR clasico da la primera posicion en el 6to mensaje (el
bootstrap de pyModeS 3.6 exige ~3 posiciones consistentes antes de emitir), y
un mensaje de superficie con surface_ref puesto la da con uno solo.

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
from adsb_events import EventDetector, PositionGate, coverage_report
from receiver import RADIO_ANALISIS_KM
from receiver import distance_km, resolve_ref

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
    # Ultima posicion conocida, igual que el callsign: la posicion llega en
    # pocos mensajes y el ultimo valor es el que sirve para saber donde esta.
    last_latitude: float | None = None
    last_longitude: float | None = None
    # min_distance_km es el dato con sentido operativo (que tan cerca del
    # receptor paso esta aeronave); max_distance_km es el alcance real medido
    # de la antena, que es lo que hay que saber antes de pensar en moverla.
    # El nivel de senal MAS FUERTE que se recibio de esta aeronave, en dBFS.
    # El mas fuerte y no el promedio ni el ultimo: la pregunta que contesta es
    # "cuan bien llega esta aeronave cuando llega bien", que es lo que dice si
    # la antena la esta tomando con margen o al borde. El promedio lo ensucian
    # los mensajes captados justo al entrar y salir del alcance.
    signal_dbfs: float | None = None
    min_distance_km: float | None = None
    max_distance_km: float | None = None
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
    def confirmada(self) -> bool:
        """Si se cree que esta direccion es una aeronave real y no ruido.

        Las tramas DF0/4/5/11/16/20/21 llevan la paridad XOR-eada con la
        direccion, asi que no se pueden validar solas y el ruido inventa una
        direccion nueva por trama. Se cree una direccion si se repitio (2+
        mensajes) o si trajo un campo que solo viaja en DF17/18, donde el CRC
        si es verificable: distintivo de vuelo o posicion.

        La MATRICULA no cuenta como confirmacion aunque parezca la mas fuerte:
        no viaja por radio, sale de buscar la direccion en el registro local, y
        el 3.8% de las direcciones de ruido cae en una direccion registrada por
        puro azar. Al revés tambien enguana: solo el 37% de las aeronaves
        confirmadas por DF17 estan en el registro.
        """
        return self.messages >= 2 or bool(self.callsign) or self.last_latitude is not None

    @property
    def identified(self) -> bool:
        return bool(self.callsign or self.registration)


def _merge(summary: AircraftSummary, observation: Observation, ref) -> None:
    """Acumular una observacion parcial sobre lo ya conocido.

    `ref` se recibe resuelta y no se calcula aca: distance_km cae por default
    en las constantes de San Isidro, y con ADSB_SURFACE_REF puesto esta columna
    estaria midiendo desde otro lugar que el resto del informe.
    """
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
    senal = getattr(observation, "signal_dbfs", None)
    if senal is not None:
        summary.signal_dbfs = (senal if summary.signal_dbfs is None
                               else max(summary.signal_dbfs, senal))
    rate = observation.vertical_rate_fpm
    if rate is not None:
        if rate > 0:
            summary.max_climb_fpm = rate if summary.max_climb_fpm is None else max(summary.max_climb_fpm, rate)
        elif rate < 0:
            summary.max_descent_fpm = rate if summary.max_descent_fpm is None else min(summary.max_descent_fpm, rate)
    if observation.latitude is not None and observation.longitude is not None:
        # Las observaciones llegan ordenadas por tiempo desde summarize(), asi
        # que la ultima que pisa estos campos es la mas reciente.
        summary.last_latitude = observation.latitude
        summary.last_longitude = observation.longitude
        km = distance_km(observation.latitude, observation.longitude, ref)
        if km is not None:
            summary.min_distance_km = (km if summary.min_distance_km is None
                                       else min(summary.min_distance_km, km))
            summary.max_distance_km = (km if summary.max_distance_km is None
                                       else max(summary.max_distance_km, km))


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
    ref = resolve_ref()

    summaries: dict[str, AircraftSummary] = {}
    for observation in ordered:
        summary = summaries.get(observation.icao24)
        if summary is None:
            summary = summaries[observation.icao24] = AircraftSummary(icao24=observation.icao24)
        _merge(summary, observation, ref)

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


def tracks(observations: list[Observation]) -> list[dict]:
    """Las posiciones de cada aeronave en orden, para dibujarlas en el mapa.

    Aparte de summarize() porque contesta otra pregunta: summarize se queda con
    la ULTIMA posicion (donde esta la aeronave) y el mapa necesita TODAS (por
    donde paso). Una aeronave con una sola posicion se dibuja como punto, no
    como linea; las que no tienen ninguna no aparecen, que es lo correcto: no
    hay nada que dibujar y no se inventa.

    La altitud viaja pegada a cada punto porque el color del trazo la codifica,
    y viene de la MISMA observacion que la posicion -- no del ultimo valor
    conocido de la aeronave, que puede ser de otro momento del vuelo.
    """
    ref = resolve_ref()
    con_posicion = [o for o in observations
                    if o.latitude is not None and o.longitude is not None]
    por_avion: dict[str, list[Observation]] = {}
    for observation in sorted(con_posicion, key=lambda o: o.timestamp):
        por_avion.setdefault(observation.icao24, []).append(observation)

    resultado = []
    for icao24, puntos in por_avion.items():
        etiqueta = next((o.callsign.strip() for o in reversed(puntos) if o.callsign), None)
        matricula = next((o.registration for o in reversed(puntos) if o.registration), None)
        if aircraft_db is not None and aircraft_db.available() and not matricula:
            entry = aircraft_db.lookup(icao24)
            matricula = (entry or {}).get("registration") or None
        resultado.append({
            "icao24": icao24,
            "callsign": etiqueta,
            "registration": matricula,
            "points": [
                {"lat": o.latitude, "lon": o.longitude, "alt": o.altitude_ft,
                 "t": o.timestamp,
                 "km": distance_km(o.latitude, o.longitude, ref)}
                for o in puntos
            ],
        })
    return sorted(resultado, key=lambda t: len(t["points"]), reverse=True)


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
        # No dice "transmitido" a secas porque el mecanismo es distinto y
        # explica por que esta fila puede tener un porcentaje por mensaje bajo
        # con un porcentaje por aeronave alto -- justamente la distincion que
        # esta tabla existe para mostrar. En vuelo hacen falta dos tramas CPR
        # par+impar Y el bootstrap de pyModeS 3.6, que no emite la primera
        # posicion hasta juntar 3 consistentes: medido por replay, la primera
        # sale en el 6to mensaje. En pista alcanza un mensaje suelto, pero solo
        # si esta configurada la referencia de superficie (ADSB_SURFACE_REF).
        ("position", "Posicion", lambda o: o.latitude is not None,
         "par de tramas CPR (en pista, una sola)"),
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


def overview(observations: list[Observation],
             gate: PositionGate | None = None) -> dict:
    """Todo lo que la pagina de analisis necesita, en una sola pasada.

    El gate viene de adsb_events.load_db/load_csv, que son quienes lo corren.
    Si no se pasa, el informe sale igual pero con gate_applied=False, para que
    la pagina no pueda confundir "no hubo rechazos" con "nadie evaluo".
    """
    summaries = summarize(observations)
    cobertura = coverage_report(observations, gate)

    # Tres grupos, no uno. Antes se devolvia todo junto y el titular decia
    # "3123 aeronaves" cuando 2996 de esas direcciones se habian visto una
    # sola vez y nunca mas: ruido contado como trafico, el numero mas visible
    # de la pagina inflado 36 veces.
    creibles     = [s for s in summaries if s.confirmada]
    no_creibles  = [s for s in summaries if not s.confirmada]
    radio        = RADIO_ANALISIS_KM
    dentro       = [s for s in creibles
                    if s.min_distance_km is not None and s.min_distance_km <= radio]
    fuera        = [s for s in creibles
                    if s.min_distance_km is not None and s.min_distance_km > radio]
    sin_posicion = [s for s in creibles if s.min_distance_km is None]

    return {
        # El analisis habla del radio elegido; lo de afuera se informa por
        # separado para que recortar no se confunda con no haber recibido.
        "aircraft": dentro + sin_posicion,
        "aircraft_all": summaries,
        "analysis_radius_km": radio,
        "within_radius": len(dentro),
        "outside_radius": len(fuera),
        "outside_radius_max_km": (round(max(s.min_distance_km for s in fuera), 1)
                                  if fuera else None),
        "without_position": len(sin_posicion),
        "unconfirmed": len(no_creibles),
        "unconfirmed_messages": sum(s.messages for s in no_creibles),
        "confirmed": len(creibles),
        "coverage": cobertura,
        "fields": field_coverage(observations),
        "observations": len(observations),
        "identified": sum(1 for s in creibles if s.identified),
        "with_registration": sum(1 for s in creibles if s.registration),
        "events": [e for s in creibles for e in s.events],
        "registry_available": bool(aircraft_db and aircraft_db.available()),
    }


def resumen_historico(db_path: str) -> dict:
    """Cuanto se vio en TODA la historia grabada, con SQL y sin cargar nada.

    La pagina en vivo se refresca cada dos segundos. Cargar las diez mil
    observaciones de la base y volver a resumirlas en cada refresco para
    contestar siempre lo mismo seria absurdo, asi que esto son agregados de
    SQLite: cuesta milisegundos y no crece con el historial.

    La regla de credibilidad es LA MISMA que la de AircraftSummary.confirmada,
    escrita en SQL: una direccion se cree si se repitio (2+ mensajes) o si trajo
    un campo que solo viaja en DF17/18, donde el CRC si es verificable. Tiene que
    ser la misma o la pagina en vivo y la de analisis darian dos totales
    distintos para lo mismo, que es la peor forma de perder la confianza de quien
    las mira.
    """
    import sqlite3
    from pathlib import Path as _Path

    vacio = {"observations": 0, "aircraft": 0, "identified": 0,
             "with_registration": 0, "with_position": 0, "noise": 0,
             "first_seen": None, "last_seen": None, "days": 0}
    if not _Path(db_path).exists():
        return vacio

    # mode=ro: esto corre en el hilo de request MIENTRAS el Recorder escribe en
    # la misma base. Una conexion de lectura-escritura sobre una base con journal
    # abierto puede disparar rollback recovery y escribir desde el lector.
    conn = sqlite3.connect(_Path(db_path).absolute().as_uri() + "?mode=ro", uri=True)
    try:
        fila = conn.execute("""
            SELECT COUNT(*) obs, MIN(epoch) desde, MAX(epoch) hasta
              FROM adsb_log
        """).fetchone()
        if not fila or not fila[0]:
            return vacio
        obs, desde, hasta = fila

        # Una pasada agrupando por direccion, y arriba se cuenta cuantos grupos
        # caen en cada categoria. Hacerlo en subconsulta y no con varios SELECT
        # evita recorrer la tabla una vez por numero.
        cred = conn.execute("""
            SELECT
              SUM(creible) aeronaves,
              SUM(creible AND tiene_vuelo) identificadas,
              SUM(creible AND tiene_posicion) con_posicion,
              SUM(NOT creible) ruido
            FROM (
              SELECT
                (COUNT(*) >= 2
                 OR MAX(callsign IS NOT NULL)
                 OR MAX(latitude IS NOT NULL)) creible,
                MAX(callsign IS NOT NULL) tiene_vuelo,
                MAX(latitude IS NOT NULL) tiene_posicion
              FROM adsb_log GROUP BY icao24
            )
        """).fetchone()

        # La matricula NO se cuenta con SQL aunque la tabla tenga la columna:
        # esa columna queda vacia por el camino del dongle, porque la matricula
        # no viaja por radio. Sale de buscar la direccion en el registro local,
        # asi que hay que preguntarle al registro. Contar la columna daba cero y
        # contradecia a la pagina de analisis, que si enriquece.
        creibles = [f[0] for f in conn.execute("""
            SELECT icao24 FROM adsb_log GROUP BY icao24
             HAVING COUNT(*) >= 2
                 OR MAX(callsign IS NOT NULL)
                 OR MAX(latitude IS NOT NULL)
        """)]
    finally:
        conn.close()

    aeronaves, identificadas, con_posicion, ruido = [int(v or 0) for v in cred]
    # lookup tiene cache, asi que a partir del segundo refresco esto es gratis.
    con_matricula = (sum(1 for i in creibles if aircraft_db.lookup(i))
                     if aircraft_db and aircraft_db.available() else 0)
    return {
        "observations": int(obs),
        "aircraft": aeronaves,
        "identified": identificadas,
        "with_registration": con_matricula,
        "with_position": con_posicion,
        "noise": ruido,
        "first_seen": desde,
        "last_seen": hasta,
        "days": max(1, round((hasta - desde) / 86400.0)) if hasta and desde else 0,
    }


def resumen_en_vivo(observaciones: list) -> list:
    """El registro COMPLETO de cada aeronave de la ventana en vivo.

    Es la misma funcion que alimenta la pagina de analisis, corrida sobre la
    ventana rodante en vez de sobre la base. No es una comodidad: la tabla de la
    pagina en vivo se armaba con el ULTIMO mensaje de cada avion, y un mensaje
    ADS-B suelto trae la altitud O la velocidad O el distintivo, casi nunca dos,
    asi que se veia casi vacia mientras el receptor recibia perfecto. Acumulando
    sobre la ventana, cada fila se completa sola con el tiempo.

    Ordenadas por la mas reciente primero, que es lo que se quiere mirar cuando
    se esta parado al lado de la antena.
    """
    return summarize(observaciones)


if __name__ == "__main__":
    import argparse

    from adsb_events import imprimir_rechazos, load_csv, load_db

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", nargs="?", help="CSV grabado por adsb_record.py")
    parser.add_argument("--db", help="Leer de adsb_log.db en vez de un CSV")
    args = parser.parse_args()

    if args.db:
        (datos, gate), origen = load_db(args.db), args.db
    elif args.csv:
        (datos, gate), origen = load_csv(args.csv), args.csv
    else:
        parser.error("indica un CSV o --db")

    info = overview(datos, gate)
    print(f"=== {origen}")
    print(f"{info['observations']} observaciones, {len(info['aircraft'])} aeronaves, "
          f"{info['identified']} identificadas\n")

    cobertura = info["coverage"]
    if cobertura["with_position"]:
        print(f"posiciones decodificadas: {cobertura['with_position']} | alcance "
              f"mediana {cobertura['median_distance_km']:.1f} km, maximo "
              f"{cobertura['max_distance_km']:.1f} km del receptor")
    # Siempre, aunque no haya rechazos: el maximo publicado arriba solo es
    # honesto si al lado esta lo que se saco de esa cuenta.
    print(imprimir_rechazos(gate))
    print()

    print(f"{'ICAO24':8s} {'VUELO':9s} {'MATRICULA':10s} {'TIPO':22s} "
          f"{'ALT MIN':>8s} {'ALT MAX':>8s} {'VEL':>6s} {'DIST':>7s}  ESTADO")
    print("-" * 108)
    for s in info["aircraft"]:
        def num(value, decimales=0):
            return f"{value:.{decimales}f}" if value is not None else "-"
        print(f"{s.icao24:8s} {(s.callsign or '-'):9s} {(s.registration or '-'):10s} "
              f"{(s.aircraft_type or '-')[:22]:22s} "
              f"{num(s.min_altitude_ft):>8s} {num(s.max_altitude_ft):>8s} "
              f"{num(s.max_speed_kt):>6s} {num(s.min_distance_km, 1):>7s}  {s.phase}")
