"""Run ADS-B recording inside the dashboard's own process, controllable via HTTP.

adsb_record.py is the terminal tool: start it, watch text scroll, Ctrl+C to
stop. This wraps the same building blocks (build_source, Recorder) in a
background-thread service with a start()/stop()/status() surface, so the
FastAPI app in webapp/main.py can offer the same recording from a page in the
browser instead of a terminal window -- live counts, a table of aircraft
currently in range, and a button to download the CSV.

One service instance is shared by the whole dashboard process (see
webapp/main.py), because there is exactly one antenna: two independent
recordings would both try to open the same RTL-SDR device and fight over it.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from adsb import Observation
from adsb_events import PositionGate
from adsb_record import CSV_DIR, DB_PATH, Recorder, build_source
from receiver import distance_km, resolve_ref

# An aircraft not heard in this long drops off the "in range now" table --
# otherwise a plane that flew out of reception would sit there forever.
LIVE_TIMEOUT_S = 60.0


@dataclass
class AdsbService:
    # RLock, not Lock: start() and stop() call status() while still holding
    # the lock, which would deadlock on a plain Lock (it isn't reentrant --
    # a thread can't reacquire one it's already holding). Caught by testing
    # the endpoint with a hard timeout instead of trusting it "should work".
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _source = None
    _recorder: Recorder | None = None
    _latest: dict[str, Observation] = field(default_factory=dict, repr=False)
    _historico_cache: tuple | None = field(default=None, repr=False)
    # Motivo de rechazo de la ULTIMA posicion de cada aeronave, o None.
    #
    # La tabla "En rango ahora" se arma desde _latest y no pasa por la base, o
    # sea que sin esto el dashboard seguiria mostrando el fantasma a 789 km
    # mientras las paginas que leen de la base ya no lo muestran: dos numeros
    # distintos para el mismo avion, y el que esta en pantalla es el falso.
    _rejected: dict[str, str | None] = field(default_factory=dict, repr=False)
    _rejected_hex: dict[str, str] = field(default_factory=dict, repr=False)
    # Gate PROPIO, distinto del que tiene el Recorder, y a proposito: el del
    # Recorder solo ve las filas que sobreviven al limitador de repetidos
    # (_is_new), y esta tabla muestra todos los mensajes. Compartirlo daria a
    # R2 una referencia mas vieja que la que corresponde a lo que se ve.
    _gate: PositionGate | None = field(default=None, repr=False)

    description: str = ""
    start_error: str | None = None
    started_at: float | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, mode: str = "auto", **kwargs) -> dict:
        with self._lock:
            if self.running:
                return self.status()
            self.start_error = None
            try:
                self._source, self.description = build_source(mode, log=lambda *_: None, **kwargs)
            except RuntimeError as exc:
                self.start_error = str(exc)
                return self.status()

            # El filtro de posiciones resuelve ADSB_SURFACE_REF al construirse
            # y falla RUIDOSO si es un codigo ICAO que no existe. Ruidoso tiene
            # que significar "cartel en la pagina", no un 500 en el navegador
            # con el motivo en el log del servidor: es un error de
            # configuracion y quien lo cometio esta mirando esta pantalla.
            try:
                self._recorder = Recorder(min_interval_s=kwargs.get("min_interval", 5.0))
                self._gate = PositionGate()
            except ValueError as exc:
                self._source.stop()
                self.start_error = f"referencia de posicion invalida: {exc}"
                return self.status()
            except RuntimeError as exc:
                # Migracion de esquema bloqueada por otra grabacion en curso.
                # Ver migrar_esquema en adsb_record.py.
                self._source.stop()
                self.start_error = str(exc)
                return self.status()
            self._latest = {}
            self._rejected = {}
            self._rejected_hex = {}
            self._stop.clear()
            self.started_at = time.time()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            self._stop.set()
            thread = self._thread
        if thread:
            thread.join(timeout=5)
        with self._lock:
            if self._source:
                self._source.stop()
            if self._recorder:
                self._recorder.close()
            self._thread = None
            return self.status()

    def _loop(self) -> None:
        visto_hasta = 0.0
        try:
            while not self._stop.is_set():
                time.sleep(1.0)
                snapshot = self._source.snapshot()
                for observation in snapshot:
                    # Solo las nuevas. snapshot() devuelve la ventana ENTERA
                    # cada segundo, y darle a un gate con estado la misma
                    # posicion diez veces le rebobina la referencia de R2.
                    if observation.timestamp <= visto_hasta:
                        continue
                    self._recorder.record(observation)
                    motivo = self._gate.feed(observation)
                    with self._lock:
                        self._latest[observation.icao24] = observation
                        self._rejected[observation.icao24] = motivo
                        if motivo:
                            # El hex de la trama que produjo la posicion mala.
                            # Sin esto el proximo caso tampoco se va a poder
                            # reproducir en un test -- ver position_hex.
                            crudo = getattr(self._source, "position_hex", {})
                            par = crudo.get(observation.icao24)
                            if par:
                                self._rejected_hex[observation.icao24] = par[1]
                if snapshot:
                    visto_hasta = max(o.timestamp for o in snapshot)
        except Exception as exc:
            # A crash here must not take the whole dashboard process down
            # with it -- surface it in status() instead, same as a source
            # that failed to start.
            with self._lock:
                self.start_error = f"{type(exc).__name__}: {exc}"

    def status(self) -> dict:
        recorder = self._recorder
        source = self._source
        gate = self._gate
        now = time.time()
        ref = resolve_ref()
        with self._lock:
            live = sorted(self._latest.values(), key=lambda o: -o.timestamp)
            live = [o for o in live if now - o.timestamp <= LIVE_TIMEOUT_S]
            rechazos = dict(self._rejected)
            hexes = dict(self._rejected_hex)
        return {
            "running": self.running,
            "description": self.description,
            "error": self.start_error,
            "waiting_for_device": bool(getattr(source, "waiting_for_device", False)),
            "started_at": self.started_at,
            "uptime_s": (now - self.started_at) if self.started_at else 0,
            "aircraft_total": len(recorder.aircraft) if recorder else 0,
            "with_registration": len(recorder.with_registration) if recorder else 0,
            "written": recorder.written if recorder else 0,
            "skipped": recorder.skipped if recorder else 0,
            "csv_dir": str(CSV_DIR),
            "db_path": str(DB_PATH),
            # Los contadores del filtro, con el cero explicito: un contador
            # ausente no se distingue de uno que nunca corrio.
            "positions_evaluated": gate.evaluadas if gate else 0,
            "positions_rejected": gate.rechazadas if gate else 0,
            "rejected_by_reason": (dict(gate.por_motivo) if gate else
                                   {"horizonte": 0, "velocidad": 0,
                                    "superficie": 0, "sin_altitud": 0}),
            "surface_rule_exercised": bool(gate and gate.superficie_evaluadas),
            # Lo que descarta pyModeS antes que nadie y que este repo no leia
            # en ningun lado. Puede ser MAYOR que positions_rejected.
            "decoder_stats": getattr(source, "decoder_stats", {}) or {},
            "corrupt_count": getattr(source, "corrupt_count", 0),
            "unverified_count": getattr(source, "unverified_count", 0),
            "live": [
                {
                    "icao24": o.icao24,
                    "registration": o.registration,
                    "callsign": o.callsign,
                    "altitude_ft": o.altitude_ft,
                    "ground_speed_kt": o.ground_speed_kt,
                    "vertical_rate_fpm": o.vertical_rate_fpm,
                    "on_ground": o.is_on_ground,
                    # El camino en vivo perdia la posicion por su cuenta, aparte
                    # del camino de la base: la tabla "En rango ahora" se
                    # alimenta solo de aca. La distancia es el numero que dice
                    # si la antena esta escuchando cerca o lejos AHORA, que es
                    # distinto del alcance historico del reporte de cobertura.
                    # Si el filtro rechazo esta posicion, la fila sale SIN
                    # lat/lon y sin distancia, igual que en la base -- pero con
                    # el motivo y los numeros al lado, nunca en silencio. La
                    # altitud, la velocidad y el regimen vertical se conservan:
                    # en el caso medido la trama era autentica y lo unico
                    # corrupto era el par lat/lon.
                    "latitude": None if rechazos.get(o.icao24) else o.latitude,
                    "longitude": None if rechazos.get(o.icao24) else o.longitude,
                    "distance_km": (None if rechazos.get(o.icao24)
                                    else distance_km(o.latitude, o.longitude, ref)),
                    "rejected_reason": rechazos.get(o.icao24),
                    "rejected_km": (round(distance_km(o.latitude, o.longitude, ref), 1)
                                    if rechazos.get(o.icao24) and o.latitude is not None
                                    else None),
                    "rejected_hex": hexes.get(o.icao24) if rechazos.get(o.icao24) else None,
                    "seconds_ago": round(now - o.timestamp, 1),
                }
                for o in live
            ],
            # El registro ACUMULADO de cada aeronave de la ventana, y el resumen
            # de todo lo grabado hasta ahora. Ver _registro_acumulado.
            "aircraft": self._registro_acumulado(now, ref),
            "historico": self._resumen_historico(),
            "senal": self._salud_de_senal(),
        }

    def _registro_acumulado(self, now: float, ref) -> list[dict]:
        """Una fila por aeronave con TODO lo que se sabe de ella, no su ultimo mensaje.

        La tabla en vivo se armaba desde _latest, o sea el ultimo mensaje de cada
        avion, y un mensaje ADS-B suelto trae la altitud O la velocidad O el
        distintivo de vuelo, casi nunca dos: la tabla se veia casi vacia mientras
        el receptor recibia perfecto. Acumulando sobre la ventana rodante, cada
        fila se completa sola a medida que llegan mensajes.

        Se reusa adsb_report.summarize, la MISMA funcion que alimenta la pagina
        de analisis, corrida sobre la ventana en vez de sobre la base. Escribir
        una segunda version aca serian dos acumuladores capaces de divergir, y la
        pagina en vivo y la historica mostrarian distinto para el mismo avion.
        Medido: 15 ms para 500 observaciones, y la ventana rodante no pasa de
        unos pocos miles.
        """
        source = self._source
        if source is None:
            return []
        try:
            import adsb_report
            resumenes = adsb_report.summarize(source.snapshot())
        except Exception:
            # Nunca dejar que un error del resumen tumbe el estado: sin esto, la
            # pagina entera se queda sin datos -incluido el boton de detener-
            # por un problema en una tabla informativa.
            return []
        filas = []
        for r in resumenes:
            if now - r.last_seen > LIVE_TIMEOUT_S * 5:
                continue     # ya no esta en rango: vive en el historico, no aca
            filas.append({
                "icao24": r.icao24, "callsign": r.callsign,
                "registration": r.registration, "aircraft_type": r.aircraft_type,
                "operator": r.operator, "messages": r.messages,
                "min_altitude_ft": r.min_altitude_ft, "max_altitude_ft": r.max_altitude_ft,
                "max_speed_kt": r.max_speed_kt,
                "max_climb_fpm": r.max_climb_fpm, "max_descent_fpm": r.max_descent_fpm,
                "latitude": r.last_latitude, "longitude": r.last_longitude,
                "min_distance_km": r.min_distance_km,
                "signal_dbfs": r.signal_dbfs,
                "phase": r.phase, "events": len(r.events),
                "duration_s": round(r.duration_s, 1),
                "seconds_ago": round(now - r.last_seen, 1),
            })
        return filas

    def _salud_de_senal(self) -> dict:
        """Si la ganancia esta bien puesta, medido y no supuesto.

        Es el numero que hace falta al mover la antena cerca de una pista. De
        lejos conviene ganancia maxima porque cada dB alcanza un avion mas
        lejano; pegado a la pista es al revés y el receptor recorta, que se
        siente como "recibo menos" justo cuando deberia recibir mejor. Sin esta
        medicion eso cuesta un dia de pruebas confundido.
        """
        source = self._source
        if source is None:
            return {}
        try:
            import adsb_iq
            umbral = adsb_iq.UMBRAL_SATURACION_DBFS
            niveles = [o.signal_dbfs for o in source.snapshot()
                       if o.signal_dbfs is not None]
        except Exception:
            return {}
        if not niveles:
            # Distinto de "todo bien": esta fuente no mide senal.
            return {"mide": False}
        niveles.sort()
        saturados = sum(1 for n in niveles if n >= umbral)
        return {
            "mide": True,
            "ganancia": getattr(source, "ganancia", None),
            "mensajes": len(niveles),
            "mediana_dbfs": round(niveles[len(niveles) // 2], 1),
            "max_dbfs": round(niveles[-1], 1),
            "min_dbfs": round(niveles[0], 1),
            "saturados": saturados,
            "saturados_pct": round(saturados / len(niveles) * 100, 1),
            "umbral_dbfs": umbral,
            # Con mas del 5% de los mensajes contra el techo ya conviene bajar:
            # no es que se pierda todo, es que se empieza a perder y no se nota.
            "bajar_ganancia": saturados / len(niveles) > 0.05,
        }

    def _resumen_historico(self) -> dict:
        """Lo grabado en toda la historia, cacheado unos segundos.

        Son agregados de SQL y cuestan 9 ms, pero la pagina se refresca cada dos
        segundos y este numero no cambia lo suficiente para justificar
        recalcularlo cada vez. Diez segundos de cache alcanzan: si entraron
        mensajes nuevos, el contador de la izquierda ya lo dice en vivo.
        """
        ahora = time.time()
        if self._historico_cache and ahora - self._historico_cache[0] < 10.0:
            return self._historico_cache[1]
        try:
            import adsb_report
            resumen = adsb_report.resumen_historico(str(DB_PATH))
        except Exception:
            resumen = {}
        self._historico_cache = (ahora, resumen)
        return resumen


# One instance per process, shared by every request the dashboard handles --
# see the module docstring for why this must not be per-request state.
service = AdsbService()
