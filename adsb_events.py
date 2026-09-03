"""Infer landings and takeoffs from ADS-B alone, with no camera.

The camera version watches an aircraft cross a line on screen. This watches the
aircraft's own telemetry cross a state boundary instead: an aircraft that was
airborne and descending, then reports itself on the ground, landed; one that was
on the ground, then reports climbing, took off.

Two properties of real ADS-B shape the whole design:

  Messages are partial. A single transmission carries altitude OR speed OR
  vertical rate, rarely all three -- confirmed in the recorded data, where 33 of
  48 observations had altitude and only 6 had vertical rate. So state is
  accumulated per aircraft across messages instead of read from any one of them.

  Reception has gaps. An aircraft drops out and comes back, and the two sides of
  that gap must not be joined into a transition that never happened: hearing an
  aircraft at 3000 ft, losing it, and hearing it on the ground twenty minutes
  later is not evidence of a landing that we witnessed. See MAX_GAP_S.

The same guards the camera needed apply here, for the same reason -- a single
noisy sample must not become an event:

  - a new state must be confirmed by several consecutive observations
  - an aircraft cannot register another event for a cooldown period
  - transitions across a long reception gap are refused, not guessed

IMPORTANT about coverage: this can only see what the antenna hears. ADS-B is
line-of-sight, so an antenna that does not have a clear view down to the runway
will never receive an aircraft at ground level, and will therefore never detect
a single landing or takeoff -- no matter how well this code works. Check
`coverage_report()` before trusting a zero.

Uso:
  python adsb_events.py output/adsb/adsb_2026-08-22.csv
  python adsb_events.py --db adsb_log.db
"""
from __future__ import annotations

import threading as _threading
import time as _time
from array import array
from bisect import bisect_right as _bisect_right
from bisect import insort as _insort
from dataclasses import dataclass, field, replace

from adsb import Observation, es_altitud_de_superficie

try:
    import aircraft_db as _aircraft_db
except Exception:                            # el registro de matriculas es opcional
    _aircraft_db = None
from receiver import (MEDIA_CELDA_CPR_KM, MEDIA_CELDA_CPR_LAT_DEG,
                      TECHO_SIN_ALTITUD_KM, distance_km, horizonte_km,
                      limite_posicion_km, resolve_ref)

# Beyond this many seconds between two observations of the same aircraft, the
# gap is treated as "we lost it" and no transition is inferred across it.
MAX_GAP_S = 120.0
# How many consecutive observations must agree before a state change counts.
CONFIRM_SAMPLES = 2
# An aircraft cannot produce another event within this window (a real landing
# and takeoff are minutes apart, not seconds).
COOLDOWN_S = 120.0
# Below this altitude an aircraft is treated as at runway level even if the
# on_ground flag is missing, which some transponders never send.
GROUND_ALT_FT = 200.0
# Vertical rate beyond which the aircraft is definitely climbing/descending.
CLIMB_FPM = 250.0


@dataclass
class AircraftState:
    """Latest known values for one aircraft, accumulated across partial messages."""
    icao24: str
    timestamp: float = 0.0
    altitude_ft: float | None = None
    ground_speed_kt: float | None = None
    vertical_rate_fpm: float | None = None
    on_ground: bool | None = None
    callsign: str | None = None

    def update(self, observation: Observation) -> None:
        """Merge one observation in, keeping fields it does not carry."""
        self.timestamp = observation.timestamp
        if observation.altitude_ft is not None:
            self.altitude_ft = observation.altitude_ft
            # An explicit altitude is also the freshest word on whether the
            # aircraft is down: a transponder reporting 8000 ft is airborne
            # regardless of a stale on_ground flag from an earlier message.
            self.on_ground = observation.altitude_ft <= GROUND_ALT_FT
        if observation.ground_speed_kt is not None:
            self.ground_speed_kt = observation.ground_speed_kt
        if observation.vertical_rate_fpm is not None:
            self.vertical_rate_fpm = observation.vertical_rate_fpm
        if observation.callsign:
            self.callsign = observation.callsign

    @property
    def phase(self) -> str | None:
        """'ground', 'climbing', 'descending', 'cruising', or None if unknown."""
        if self.on_ground is True:
            return "ground"
        if self.altitude_ft is not None and self.altitude_ft <= GROUND_ALT_FT:
            return "ground"
        if self.vertical_rate_fpm is not None:
            if self.vertical_rate_fpm >= CLIMB_FPM:
                return "climbing"
            if self.vertical_rate_fpm <= -CLIMB_FPM:
                return "descending"
        if self.altitude_ft is not None:
            return "cruising"
        return None


@dataclass
class AdsbEvent:
    icao24: str
    event_type: str          # 'landing' | 'takeoff'
    timestamp: float
    callsign: str | None
    altitude_ft: float | None
    reason: str


@dataclass
class EventDetector:
    """Turns a stream of observations into landing/takeoff events."""
    max_gap_s: float = MAX_GAP_S
    confirm_samples: int = CONFIRM_SAMPLES
    cooldown_s: float = COOLDOWN_S

    _state: dict[str, AircraftState] = field(default_factory=dict, repr=False)
    _phase: dict[str, str] = field(default_factory=dict, repr=False)
    _pending: dict[str, tuple[str, int]] = field(default_factory=dict, repr=False)
    _last_event: dict[str, float] = field(default_factory=dict, repr=False)

    # Counters for the honesty report: a zero means very different things
    # depending on which of these is non-zero.
    gaps_skipped: int = 0
    cooldown_skipped: int = 0

    def feed(self, observation: Observation) -> AdsbEvent | None:
        """Process one observation; return an event if this one completes a
        confirmed transition."""
        icao24 = observation.icao24
        state = self._state.get(icao24)

        if state is None:
            state = AircraftState(icao24=icao24)
            self._state[icao24] = state
            state.update(observation)
            # First sighting establishes a baseline only. Declaring an event
            # here would invent one for every aircraft that comes into range
            # already on the ground or already climbing.
            if state.phase:
                self._phase[icao24] = state.phase
            return None

        gap = observation.timestamp - state.timestamp
        state.update(observation)

        if gap > self.max_gap_s:
            # Lost and reacquired: reset the baseline instead of pretending we
            # watched whatever happened while we could not hear it.
            self.gaps_skipped += 1
            self._phase[icao24] = state.phase or ""
            self._pending.pop(icao24, None)
            return None

        phase = state.phase
        if not phase:
            return None

        previous = self._phase.get(icao24)
        if previous is None:
            self._phase[icao24] = phase
            return None

        if phase == previous:
            self._pending.pop(icao24, None)
            return None

        # Phase changed: require it to hold for several observations before
        # believing it, so one odd message cannot create an event.
        pending_phase, count = self._pending.get(icao24, (phase, 0))
        if pending_phase != phase:
            self._pending[icao24] = (phase, 1)
            return None
        count += 1
        self._pending[icao24] = (phase, count)
        if count < self.confirm_samples:
            return None

        self._pending.pop(icao24, None)
        self._phase[icao24] = phase

        event_type = self._classify(previous, phase)
        if event_type is None:
            return None

        last = self._last_event.get(icao24)
        if last is not None and observation.timestamp - last < self.cooldown_s:
            self.cooldown_skipped += 1
            return None
        self._last_event[icao24] = observation.timestamp

        return AdsbEvent(
            icao24=icao24, event_type=event_type,
            timestamp=observation.timestamp, callsign=state.callsign,
            altitude_ft=state.altitude_ft,
            reason=f"{previous} -> {phase}",
        )

    @staticmethod
    def _classify(previous: str, current: str) -> str | None:
        """Which transitions count as an operation, and which are just flight."""
        if current == "ground" and previous in ("descending", "cruising"):
            return "landing"
        if previous == "ground" and current == "climbing":
            return "takeoff"
        # Everything else -- cruising to climbing, descending to cruising --
        # is an aircraft manoeuvring in the air, not using the runway.
        return None


def detect(observations: list[Observation], **kwargs) -> list[AdsbEvent]:
    """Run the detector over a full history, oldest first."""
    detector = EventDetector(**kwargs)
    events = []
    for observation in sorted(observations, key=lambda o: o.timestamp):
        event = detector.feed(observation)
        if event:
            events.append(event)
    return events


# --------------------------------------------------------------------------
# Filtro de posiciones implausibles
# --------------------------------------------------------------------------
# El x2 de holgura sobre las constantes de speed_check de dump1090-fa
# (track.c). NO es un numero elegido: con las constantes crudas el PEOR caso
# legitimo de las 783 posiciones de la base queda en 0.896 del limite (e06542:
# 1.6 km recorridos contra 1.8 permitidos), o sea 11% de margen -- demasiado
# poco para otra tarde con mas huecos. Con x2 el peor legitimo baja a 0.45 y el
# fantasma de e0b14a sigue a 47x del limite. Medido.
#
# La razon fisica del factor: dump1090 sella tiempo con el reloj de muestreo
# del SDR, y aca el sello es time.time() en el hilo de Python
# (adsb_rtlsdr.py:_handle_line), DESPUES del buffer USB de rtl_adsb, asi que dt
# arrastra jitter de host que el +1 s de dump1090 no fue dimensionado para
# absorber.
#
# Si aparecen falsos positivos hay que SUBIRLO con evidencia, no bajarlo por
# corazonada. El contador visible es lo que hace posible esa correccion.
TOLERANCIA_JITTER = 2.0

# Por encima de este hueco no se compara: se acepta la posicion y se REINICIA
# la referencia. Sigue a dump1090, y esta motivado por un caso real de estos
# datos -- a90552 con un hueco de 8m38s dio una velocidad derivada de -112 kt,
# o sea negativa: con huecos largos el calculo se ensucia y rechazar con datos
# viejos seria peor que no mirar.
MAX_DT_CONTINUIDAD_S = 60.0

# Velocidad supuesta cuando la aeronave todavia no transmitio ninguna. Son las
# de dump1090-fa: generosas a proposito, porque el default solo tiene que
# evitar el falso positivo, no atrapar nada (para eso esta el horizonte).
GS_DEFAULT_VUELO_KT = 600.0
GS_DEFAULT_SUPERFICIE_KT = 100.0


@dataclass
class PositionRejection:
    """Una posicion que el filtro considera imposible, con su aritmetica.

    Se guarda entera y con los numeros que la condenan porque el rechazo se
    PUBLICA: un contador suelto no se puede auditar ni discutir, y si algun dia
    el filtro corta una recepcion excepcional real, esto es lo que permite
    verla y subir el margen con evidencia.
    """
    icao24: str
    timestamp: float
    latitude: float
    longitude: float
    altitude_ft: float | None
    km: float
    motivo: str                      # horizonte | velocidad | superficie | sin_altitud
    limite_km: float
    horizonte_km: float | None = None
    kt_implicita: float | None = None
    dt_s: float | None = None
    salto_km: float | None = None
    gs_transmitida_kt: float | None = None

    def as_dict(self) -> dict:
        return {
            "icao24": self.icao24, "t": self.timestamp,
            "lat": self.latitude, "lon": self.longitude,
            "alt_ft": self.altitude_ft, "km": round(self.km, 1),
            "motivo": self.motivo, "limite_km": round(self.limite_km, 1),
            "horizonte_km": (round(self.horizonte_km, 1)
                             if self.horizonte_km is not None else None),
            "kt_implicita": (round(self.kt_implicita) if self.kt_implicita is not None else None),
            "dt_s": (round(self.dt_s, 1) if self.dt_s is not None else None),
            "salto_km": (round(self.salto_km, 1) if self.salto_km is not None else None),
            "gs_transmitida_kt": self.gs_transmitida_kt,
        }


@dataclass
class PositionGate:
    """Descarta el par lat/lon de una posicion imposible, contando cada rechazo.

    Modelado sobre EventDetector, que es la otra parte del repo que cuenta lo
    que descarta: un filtro que tira datos sin dejar el numero a la vista es
    exactamente lo que este repo evita. Una pagina que publica "alcance maximo
    72 km" habiendo tirado una posicion de 789 km sin decirlo miente.

    DOS reglas independientes que corren juntas, mas dos casos de borde. Atacan
    fallas distintas y por eso no se pueden fusionar:

      R1 horizonte de radio -- atrapa el error de indice de zona CPR que cae
         LEJISIMOS. Es la unica que funciona en la PRIMERA posicion de una
         aeronave, cuando no hay historia, y la unica sin estado.
      R2 continuidad de velocidad -- atrapa el error de zona CPR que cae CERCA
         (una zona de longitud mal elegida a 36000 ft puede aterrizar a 200 km,
         muy por debajo del limite de horizonte de 600.8 km, y R1 no la ve).
         Tambien atrapa el reuso de una direccion icao24 por dos aviones.
      R1b techo sin altitud, R1c media celda CPR de superficie -- ver receiver.py.

    LO QUE NO HACE: no borra la fila. Anula lat/lon y deja altitud, velocidad y
    regimen vertical intactos, porque en el unico caso medido la trama era
    AUTENTICA -- la altitud de 19525 ft de e0b14a encaja con los -2048 fpm
    transmitidos en las dos ramas del descenso -- y lo unico corrupto era el
    par lat/lon. Tirar la observacion entera perderia datos buenos.
    """
    ref: tuple[float, float] = field(default_factory=resolve_ref)
    tolerancia_jitter: float = TOLERANCIA_JITTER
    max_dt_s: float = MAX_DT_CONTINUIDAD_S

    # Ultima posicion ACEPTADA por icao24, nunca la ultima RECIBIDA. Esto no es
    # un detalle: medido sobre estos datos, con la ultima recibida se rechazan
    # 2 posiciones (la mala de 20:08:27 y ademas la BUENA de 20:08:53, que se
    # compara contra el fantasma) y con la ultima aceptada se rechaza 1 sola.
    # Es la razon por la que dump1090 nunca guarda una rechazada como
    # referencia -- y de paso es el bug que tiene pyModeS, cuyo
    # _update_position_history (_pipe.py:668-685) mete al historial tambien las
    # rechazadas: por eso el SEGUNDO fantasma de una rafaga pasa (comprobado).
    _aceptada: dict[str, tuple[float, float, float]] = field(default_factory=dict, repr=False)
    # La velocidad se arrastra por icao24 desde CUALQUIER mensaje, no se lee de
    # la fila de la posicion. Las posiciones vienen de TC 9-18 y la velocidad de
    # TC 19: son mensajes DISTINTOS. Leyendola de la misma fila queda None casi
    # siempre, se cae al default y el filtro pasa de 1 rechazo a 540. Medido.
    _gs: dict[str, float] = field(default_factory=dict, repr=False)

    evaluadas: int = 0
    aceptadas: int = 0
    por_motivo: dict[str, int] = field(
        default_factory=lambda: {"horizonte": 0, "velocidad": 0,
                                 "superficie": 0, "sin_altitud": 0})
    rechazos: list[PositionRejection] = field(default_factory=list, repr=False)
    # Hoy la base tiene 126 filas con on_ground=1 y NINGUNA trae posicion, asi
    # que R1c nunca corrio con trafico real. Un contador en 0 que nadie sabe que
    # nunca se ejercito es una mentira por omision, y por eso se publica aparte.
    superficie_evaluadas: int = 0
    # DETECTOR DE ANTENA MAL UBICADA. Un blanco apoyado en el pavimento no puede
    # llegar de mas lejos que el horizonte de radio al suelo: no hay altitud que
    # lo levante sobre la curvatura. Asi que una posicion de SUPERFICIE que
    # llega desde mas lejos que horizonte_km(0) no dice "dato malo", dice "la
    # antena no esta donde dice la configuracion".
    #
    # Medido sobre adsb_log.db (779 posiciones de superficie aceptadas, 30
    # aeronaves): con el receptor por defecto (San Isidro, antena 10 m,
    # horizonte al suelo 13.04 km) 498 de ellas, de 27 aeronaves distintas,
    # llegan desde 12.23-14.52 km -- imposibles. Con ADSB_RECEIVER=aeroparque
    # (3 m, 7.14 km) son 0, peor caso 0.48x del horizonte; con ypf (160 m,
    # 52.16 km) tambien 0, peor caso 0.16x. El detector separa perfecto.
    #
    # NO se rechazan: la posicion es dato real y lo que esta mal es la config.
    # Se CUENTAN, porque nada se descarta ni se acepta en silencio -- y hasta
    # hoy /api/adsb/status publicaba rejected_by_reason.horizonte=0 mientras
    # entraban esas 498.
    #
    # Y NO se aplica MARGEN_DUCTING=1.35: esta citado de PiAware para un blanco
    # a 45000 ft (496.0 km de horizonte), y el ducting troposferico no mete en
    # linea de vista algo que esta en el suelo. Al ras seria perdonar 4.6 km
    # sobre 13.0, mas que el peor caso medido del error de sitio (1.11x).
    superficie_fuera_de_horizonte: int = 0
    superficie_fuera_max_km: float | None = None
    _superficie_fuera_icaos: set = field(default_factory=set, repr=False)

    def feed(self, observation: Observation) -> str | None:
        """Evalua una observacion y devuelve el motivo de rechazo, o None.

        Se le pasan TODAS las observaciones, tengan posicion o no: las que no la
        tienen son las que traen la velocidad con la que R2 mide.
        """
        if observation.ground_speed_kt is not None:
            self._gs[observation.icao24] = observation.ground_speed_kt
        if observation.latitude is None or observation.longitude is None:
            return None

        self.evaluadas += 1
        rechazo = self._evaluar(observation)
        if rechazo is not None and rechazo.motivo != "velocidad":
            # e0b14a viola las DOS reglas. Se CUENTA una sola vez, bajo la
            # primera que se evalua, para que los numeros sumen -- pero el
            # cartel que se le muestra a la persona trae la aritmetica de las
            # dos, porque "789 km a 19525 ft" y "748.7 km en 35.7 s = 40813 kt
            # transmitiendo 295 kt" convencen de cosas distintas y juntas no
            # dejan lugar a la duda. Esto no toca ningun contador.
            self._anotar_continuidad(observation, rechazo)
        if rechazo is None:
            self.aceptadas += 1
            self._aceptada[observation.icao24] = (observation.timestamp,
                                                  observation.latitude,
                                                  observation.longitude)
            return None
        self.por_motivo[rechazo.motivo] += 1
        self.rechazos.append(rechazo)
        return rechazo.motivo

    def _evaluar(self, o: Observation) -> PositionRejection | None:
        km = distance_km(o.latitude, o.longitude, self.ref)
        on_ground = o.is_on_ground

        # Orden fijo y documentado: e0b14a viola R1 y R2 a la vez, y se cuenta
        # UNA sola vez, bajo la primera que se evalua, para que los numeros
        # sumen. Si algun dia se invierte el orden, el reporte cambia de motivo
        # sin que cambie nada real.
        if on_ground:
            self.superficie_evaluadas += 1
            # El horizonte al suelo se evalua ACA y no en la rama de vuelo:
            # la de superficie miraba SOLO la media celda CPR (83.5 km, 6.4x el
            # horizonte de superficie), asi que el limite geometrico de un
            # blanco apoyado en el pavimento no se comparaba nunca. Ver
            # superficie_fuera_de_horizonte.
            horizonte_suelo = horizonte_km(0)
            if km > horizonte_suelo:
                self.superficie_fuera_de_horizonte += 1
                self._superficie_fuera_icaos.add(o.icao24)
                if (self.superficie_fuera_max_km is None
                        or km > self.superficie_fuera_max_km):
                    self.superficie_fuera_max_km = km
            fuera_de_celda = (abs(o.latitude - self.ref[0]) > MEDIA_CELDA_CPR_LAT_DEG
                              or km > MEDIA_CELDA_CPR_KM)
            if fuera_de_celda:
                return PositionRejection(
                    icao24=o.icao24, timestamp=o.timestamp, latitude=o.latitude,
                    longitude=o.longitude, altitude_ft=o.altitude_ft, km=km,
                    motivo="superficie", limite_km=MEDIA_CELDA_CPR_KM)
        elif o.altitude_ft is None:
            if km > TECHO_SIN_ALTITUD_KM:
                return PositionRejection(
                    icao24=o.icao24, timestamp=o.timestamp, latitude=o.latitude,
                    longitude=o.longitude, altitude_ft=None, km=km,
                    motivo="sin_altitud", limite_km=TECHO_SIN_ALTITUD_KM)
        else:
            limite = limite_posicion_km(o.altitude_ft)
            if km > limite:
                return PositionRejection(
                    icao24=o.icao24, timestamp=o.timestamp, latitude=o.latitude,
                    longitude=o.longitude, altitude_ft=o.altitude_ft, km=km,
                    motivo="horizonte", limite_km=limite,
                    horizonte_km=horizonte_km(o.altitude_ft))

        return self._continuidad(o, km, on_ground)

    def _medir_continuidad(self, o: Observation, on_ground: bool) -> tuple | None:
        """(dt, salto_km, permitido_km, kt_implicita, gs) o None si no aplica.

        Aparte de _continuidad porque los mismos numeros se usan para dos
        cosas: decidir el rechazo, y explicarlo en pantalla cuando la que
        rechaza es OTRA regla.
        """
        previa = self._aceptada.get(o.icao24)
        if previa is None:
            return None                     # primera posicion: no hay contra que medir
        t_previo, lat_previo, lon_previo = previa
        dt = o.timestamp - t_previo
        # Fuera de orden o hueco largo: no se compara, se reinicia la referencia
        # aceptando esta posicion. Ver MAX_DT_CONTINUIDAD_S.
        if dt < 0 or dt > self.max_dt_s:
            return None

        gs = self._gs.get(o.icao24)
        v_kt = gs if gs is not None else (GS_DEFAULT_SUPERFICIE_KT if on_ground
                                          else GS_DEFAULT_VUELO_KT)
        v_kt *= 4 / 3                       # el margen de maniobra de dump1090-fa
        if on_ground:
            v_kt = min(max(v_kt, 20.0), 150.0)
            base_km = 0.1
        else:
            v_kt = max(v_kt, 200.0)
            base_km = 0.5
        # El +1 s es el de dump1090-fa: absorbe el redondeo del sello de tiempo.
        permitido = self.tolerancia_jitter * (base_km + v_kt * 1.852 * (dt + 1) / 3600)
        salto = distance_km(o.latitude, o.longitude, (lat_previo, lon_previo))
        # El numero que hace evidente el absurdo sin tener que creerle al
        # filtro: 748.7 km en 35.7 s son 40813 kt, con 295 kt transmitidos.
        kt = (salto / 1.852) / (dt / 3600) if dt > 0 else None
        return dt, salto, permitido, kt, gs

    def _continuidad(self, o: Observation, km: float,
                     on_ground: bool) -> PositionRejection | None:
        """R2: speed_check de dump1090-fa contra la ultima posicion ACEPTADA."""
        medida = self._medir_continuidad(o, on_ground)
        if medida is None:
            return None
        dt, salto, permitido, kt, gs = medida
        if salto <= permitido:
            return None
        return PositionRejection(
            icao24=o.icao24, timestamp=o.timestamp, latitude=o.latitude,
            longitude=o.longitude, altitude_ft=o.altitude_ft, km=km,
            motivo="velocidad", limite_km=permitido,
            horizonte_km=(horizonte_km(o.altitude_ft)
                          if o.altitude_ft is not None else None),
            kt_implicita=kt, dt_s=dt, salto_km=salto, gs_transmitida_kt=gs)

    def _anotar_continuidad(self, o: Observation, rechazo: PositionRejection) -> None:
        """Completa los numeros de R2 en un rechazo que condeno otra regla."""
        medida = self._medir_continuidad(o, o.is_on_ground)
        if medida is None:
            return
        dt, salto, permitido, kt, gs = medida
        if salto <= permitido:
            return                          # R2 no la habria rechazado: no se anota
        rechazo.dt_s, rechazo.salto_km = dt, salto
        rechazo.kt_implicita, rechazo.gs_transmitida_kt = kt, gs
        if rechazo.horizonte_km is None and o.altitude_ft is not None:
            rechazo.horizonte_km = horizonte_km(o.altitude_ft)

    @property
    def rechazadas(self) -> int:
        return len(self.rechazos)

    @property
    def superficie_fuera_de_horizonte_aeronaves(self) -> int:
        return len(self._superficie_fuera_icaos)

    @property
    def antena_no_esta_donde_dice(self) -> bool:
        """Si las posiciones de superficie contradicen la ubicacion configurada.

        Umbral: al menos una posicion imposible de al menos 3 aeronaves
        distintas. El ">0" es geometria y no un ajuste -- para un blanco
        apoyado en el pavimento el horizonte 4/3 es la linea. El piso de 3
        aeronaves sale de la medicion: el caso real fueron 27 de 30 aeronaves,
        o sea 9x de margen contra "fue un decode raro".

        Lo que NO se puede afirmar con esto es DONDE esta la antena: descarta
        la ubicacion configurada, y con las mismas posiciones quedan varias
        compatibles (medido: aeroparque e ypf las dos dan 0 imposibles).
        """
        return (self.superficie_fuera_de_horizonte > 0
                and self.superficie_fuera_de_horizonte_aeronaves >= 3)

    @property
    def resumen(self) -> str:
        """Una linea para el CLI y el dashboard, en el estilo de EventDetector."""
        detalle = ", ".join(f"{k} {v}" for k, v in self.por_motivo.items() if v)
        return (f"posiciones: {self.aceptadas} aceptadas, {self.rechazadas} "
                f"descartada{'' if self.rechazadas == 1 else 's'}"
                + (f" ({detalle})" if detalle else ""))


def apply_position_gate(observations: list[Observation], ref=None
                        ) -> tuple[list[Observation], PositionGate]:
    """Corre el filtro sobre una historia completa y devuelve las dos mitades.

    Las observaciones vuelven ENTERAS: a las rechazadas solo se les anula
    lat/lon. El costo medido de recorrer las 8128 filas de la base con las dos
    reglas aritmeticas es ruido frente al SELECT que las trajo.
    """
    gate = PositionGate(**({"ref": ref} if ref is not None else {}))
    salida = []
    for o in sorted(observations, key=lambda x: x.timestamp):
        if gate.feed(o) is None:
            salida.append(o)
        else:
            salida.append(replace(o, latitude=None, longitude=None))
    return salida, gate


def imprimir_rechazos(gate: PositionGate) -> str:
    """El bloque de texto que el CLI imprime SIEMPRE, con rechazos o sin ellos.

    Sin rechazos igual se imprime, con el numero de evaluadas: "0 descartadas"
    a secas no se distingue de "el filtro no corrio". Y la regla de superficie
    se lista aparte porque su cero significa otra cosa -- que nunca se
    ejercito.
    """
    lineas = [f"  {gate.resumen}"]
    for r in gate.rechazos:
        hora = _hora(r.timestamp)
        if r.motivo == "velocidad":
            lineas.append(
                f"    {r.icao24} {hora}  {r.km:.1f} km  descartada por velocidad: "
                f"{r.salto_km:.1f} km en {r.dt_s:.1f} s = {r.kt_implicita:.0f} kt "
                f"(transmitia {r.gs_transmitida_kt or '?'} kt, limite {r.limite_km:.1f} km)")
        else:
            horizonte = (f", horizonte {r.horizonte_km:.0f} km"
                         if r.horizonte_km is not None else "")
            alt = f"{r.altitude_ft:.0f} ft" if r.altitude_ft is not None else "sin altitud"
            lineas.append(
                f"    {r.icao24} {hora}  {r.km:.1f} km a {alt}  descartada por "
                f"{r.motivo}: limite {r.limite_km:.1f} km{horizonte}")
            # Cuando ademas viola la continuidad se dice, aunque no se cuente
            # dos veces: son dos argumentos independientes sobre el mismo punto.
            if r.kt_implicita is not None:
                lineas.append(
                    f"      (y ademas {r.salto_km:.1f} km en {r.dt_s:.1f} s = "
                    f"{r.kt_implicita:.0f} kt, transmitiendo "
                    f"{r.gs_transmitida_kt or '?'} kt)")
    if not gate.superficie_evaluadas:
        # "0 posiciones CON on_ground" y no "0 en tierra": dos lineas mas
        # arriba el mismo informe dice "observaciones en tierra: 127", y las
        # dos cosas son ciertas -- hay 127 filas en tierra y NINGUNA trae
        # posicion, que es justamente por que esta regla nunca corrio.
        lineas.append("    regla de superficie: SIN EJERCITAR "
                      "(0 de las posiciones evaluadas venia con on_ground)")
    else:
        # El cero va SIEMPRE: es la unica forma de distinguir "ninguna posicion
        # de superficie llego de mas lejos que el horizonte al suelo" de "eso
        # no se mira". Ver PositionGate.superficie_fuera_de_horizonte.
        h = horizonte_km(0)
        n = gate.superficie_fuera_de_horizonte
        lineas.append(
            f"    superficie: {gate.superficie_evaluadas} posiciones evaluadas, "
            f"{n} de mas lejos que el horizonte al suelo ({h:.1f} km)"
            + (f", de {gate.superficie_fuera_de_horizonte_aeronaves} aeronaves "
               f"distintas, la peor a {gate.superficie_fuera_max_km:.1f} km" if n else ""))
        if gate.antena_no_esta_donde_dice:
            lineas.append(
                "    OJO: un avion apoyado en el pavimento no puede llegar de mas "
                "lejos que ese horizonte. LA ANTENA NO ESTA DONDE DICE LA "
                "CONFIGURACION (revisar ADSB_RECEIVER y ADSB_ANTENNA_M).")
    return "\n".join(lineas)


def _hora(timestamp: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%H:%M:%S")


def coverage_report(observations: list[Observation],
                    gate: PositionGate | None = None) -> dict:
    """Whether this data could contain runway operations at all.

    Zero events is meaningless without this: an antenna that never hears an
    aircraft below cruise altitude cannot show a landing, and the honest
    answer is "no coverage", not "no landings".

    Dos ejes, y hasta que la posicion se decodifico solo se podia medir uno.
    El vertical (hasta que altura baja lo que se escucha) dice si puede haber
    operaciones en los datos. El horizontal (hasta que distancia llega la
    antena) es la otra mitad de la pregunta, y es la que responde si mover la
    antena serviria de algo. Se mide contra receiver.py, la misma referencia
    que usa el decodificador para las posiciones en superficie: dos copias de
    la coordenada terminarian midiendo desde puntos distintos.

    El `gate` es opcional y se pasa cuando las observaciones YA pasaron por
    apply_position_gate: sin el, este informe no puede distinguir "no hubo
    rechazos" de "nadie evaluo nada", y las claves de rechazo salen en cero con
    positions_evaluated=0 para que la diferencia se vea. El cero se imprime
    SIEMPRE, explicito: un contador ausente no se distingue de uno que nunca
    corrio.
    """
    altitudes = [o.altitude_ft for o in observations if o.altitude_ft is not None]
    on_ground = sum(1 for o in observations if o.is_on_ground)
    low = sum(1 for a in altitudes if a < 3000)

    # La referencia se resuelve una vez y se pasa explicita. Antes se llamaba
    # sin ref y distance_km caia en RECEIVER_LAT/RECEIVER_LON, o sea que este
    # informe media desde San Isidro aunque ADSB_SURFACE_REF dijera otra cosa.
    ref = gate.ref if gate is not None else resolve_ref()
    distancias = sorted(
        d for o in observations
        if (d := distance_km(o.latitude, o.longitude, ref)) is not None
    )
    rechazos = gate.rechazos if gate is not None else []
    return {
        "observations": len(observations),
        "aircraft": len({o.icao24 for o in observations}),
        "with_altitude": len(altitudes),
        "min_altitude_ft": min(altitudes) if altitudes else None,
        "on_ground_observations": on_ground,
        "below_3000ft": low,
        "can_see_runway_level": on_ground > 0 or low > 0,
        "with_position": len(distancias),
        # Tres numeros y no uno porque miden cosas distintas y con pocas
        # posiciones se separan mucho.
        #
        # El maximo lo fija un solo mensaje: puede ser un rebote o una trama con
        # CRC afortunado.
        #
        # El p95 se puso aca para no depender del maximo, pero con n chico no
        # cumple: medido sobre 43 posiciones reales, 39 caian entre 9.9 y 31.9 km
        # y 4 entre 70.5 y 72.4 (todas del mismo avion en crucero a 36000 ft).
        # El p95 de esa muestra es el tercer valor mas grande -- 71.3 km, dentro
        # del grupo lejano. No filtra la cola, la reporta. Se sigue publicando
        # porque con miles de posiciones si sirve, pero acompanado de n para que
        # se pueda juzgar, y nunca solo.
        #
        # La mediana es la que aguanta la cola: 19.7 km en esa misma muestra.
        "max_distance_km": distancias[-1] if distancias else None,
        "p95_distance_km": (distancias[min(int(len(distancias) * 0.95),
                                           len(distancias) - 1)]
                            if distancias else None),
        "median_distance_km": distancias[len(distancias) // 2] if distancias else None,
        "min_distance_km": distancias[0] if distancias else None,

        # --- lo que el filtro descarto -------------------------------------
        # with_position de arriba cuenta solo las ACEPTADAS; observations y
        # aircraft NO cambian, porque la observacion se conserva entera y lo
        # unico que se anula es el par lat/lon.
        "positions_evaluated": gate.evaluadas if gate is not None else 0,
        "positions_rejected": len(rechazos),
        "rejected_by_reason": (dict(gate.por_motivo) if gate is not None else
                               {"horizonte": 0, "velocidad": 0,
                                "superficie": 0, "sin_altitud": 0}),
        "rejected_max_km": (round(max(r.km for r in rechazos), 1)
                            if rechazos else None),
        "rejected_detail": [r.as_dict() for r in rechazos],
        # Distinto de "sin rechazos": la regla de superficie no corrio nunca.
        # Ver PositionGate.superficie_evaluadas.
        "surface_rule_exercised": bool(gate is not None and gate.superficie_evaluadas),
        # --- la antena contra su propia configuracion ----------------------
        # Estas cuatro salen del lado REFERENCIADO AL RECEPTOR, que es el unico
        # lado donde puede salir: el cilindro de operaciones se define alrededor
        # del AEROPUERTO, asi que ningun conteo de aterrizajes o despegues
        # contiene informacion sobre donde esta la antena (medido: los conteos
        # son identicos con y sin ADSB_RECEIVER). Ver
        # PositionGate.superficie_fuera_de_horizonte.
        "surface_evaluated": (gate.superficie_evaluadas if gate is not None else 0),
        "surface_beyond_horizon": (gate.superficie_fuera_de_horizonte
                                   if gate is not None else 0),
        "surface_beyond_horizon_aircraft": (
            gate.superficie_fuera_de_horizonte_aeronaves if gate is not None else 0),
        "surface_beyond_horizon_max_km": (
            round(gate.superficie_fuera_max_km, 2)
            if gate is not None and gate.superficie_fuera_max_km is not None else None),
        "surface_horizon_km": round(horizonte_km(0), 2),
        "receiver_misplaced": bool(gate is not None and gate.antena_no_esta_donde_dice),
        "gate_applied": gate is not None,
    }


def load_csv(path: str) -> tuple[list[Observation], PositionGate]:
    """Igual que _load_csv pero devolviendo tambien lo que el filtro descarto."""
    return apply_position_gate(_read_csv(path))


def load_db(path: str) -> tuple[list[Observation], PositionGate]:
    """Igual que _load_db pero devolviendo tambien lo que el filtro descarto.

    Este es el cuello por el que pasan las DOS paginas (/api/adsb/analisis y
    /api/adsb/mapa) y el CLI, y es donde se arregla lo que ya esta en la base:
    la fila mala YA fue escrita, asi que un filtro solo en ingestion la dejaria
    en pantalla para siempre. Filtrar en lectura es reversible, idempotente y
    retroactivo, y permite devolver los rechazos junto con los datos.
    """
    # Se sigue leyendo la base ENTERA aca a proposito: los 8 llamadores de
    # load_db desempaquetan una tupla de 2 y esperan la lista completa de
    # Observation (el CLI, /adsb/analisis, aeropuerto.py y los tests las
    # recorren). El camino incremental que evita releerlas es
    # LectorIncremental, que no las conserva: mantiene el estado ya reducido.
    return apply_position_gate(_read_db(path)[0])


def _load_csv(path: str) -> list[Observation]:
    """Solo las aceptadas. Existe para los llamadores que no miran rechazos."""
    return load_csv(path)[0]


def _load_db(path: str) -> list[Observation]:
    """Solo las aceptadas. Existe para los llamadores que no miran rechazos."""
    return load_db(path)[0]


def _read_csv(path: str) -> list[Observation]:
    import csv as _csv

    def num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    rows = []
    with open(path, encoding="utf-8") as handle:
        for row in _csv.DictReader(handle):
            rows.append(Observation(
                timestamp=float(row["epoch"]),
                icao24=row["icao24"],
                registration=row.get("registration") or None,
                callsign=row.get("callsign") or None,
                altitude_ft=num(row.get("altitude_ft")),
                ground_speed_kt=num(row.get("ground_speed_kt")),
                vertical_rate_fpm=num(row.get("vertical_rate_fpm")),
                # num() devuelve None ante "" y ante la clave ausente, asi que
                # los CSV viejos (columna vacia en las 3523 filas grabadas
                # antes de arreglar el decodificador) siguen cargando igual.
                latitude=num(row.get("latitude")),
                longitude=num(row.get("longitude")),
                # "" -> None (el avion no lo dijo, o el CSV es anterior a la
                # columna), "1"/"0" -> True/False. Los CSV viejos no traen la
                # clave y row.get() da None, que es el valor correcto.
                on_ground_reported=(None if not (row.get("on_ground_reported") or "").strip()
                                    else row["on_ground_reported"].strip() == "1"),
            ))
    return rows


def _read_db(path: str, *, desde: int = 0) -> tuple[list[Observation], int]:
    """Las filas con id > desde y el maximo id leido.

    Devuelve una tupla y no una lista porque quien lee por tramos necesita
    saber hasta donde llego, y derivarlo del largo de la lista solo funciona
    mientras los ids sean consecutivos.
    """
    observaciones, _ids, cursor, _estado = _read_db_con_ids(path, desde=desde)
    return observaciones, cursor


def _read_db_con_ids(path: str, *, desde: int = 0
                     ) -> tuple[list[Observation], list[int], int, tuple[int, int]]:
    """Las filas con id > desde, sus ids, el maximo id leido, y (max_id, total).

    El cuarto elemento es el estado de la TABLA ENTERA, no del tramo leido, y
    esta para que LectorIncremental pueda ver si la base retrocedio. Medido de
    punta a punta sobre las 11 823 filas de hoy, el refresco vacio -- el 78 % de
    los refrescos -- pasa de 0.319 ms a 0.774 ms, casi todo preparacion de las
    dos sentencias. El count es el unico de los dos que escala con la tabla
    (7.2 ms a un millon de filas, recorriendo el indice) y se paga igual porque
    es lo unico que detecta un DELETE en el medio, que no mueve max(id): 7.2 ms
    cada 5 s son 0.14 % de un nucleo, contra un mapa que sirve filas borradas
    hasta que alguien se acuerde de reiniciar el servidor.

    Lo usa LectorIncremental, que necesita el id de cada punto para poder
    contestarle a cada cliente "que hay de nuevo desde tu cursor" sin
    guardar estado por pestana en el servidor.
    """
    # on_ground (la columna combinada) sigue SIN leerse a proposito: es
    # derivable de altitude_ft=0.0, que decoded_to_observation ya fija para los
    # mensajes de superficie, y duplicar un estado derivable en dos columnas
    # invita a que se contradigan. Queda para leer el CSV en Excel.
    #
    # on_ground_reported SI se lee, y no es la misma discusion: no es derivable
    # de nada. Es lo que el avion DECLARO, y ningun valor de altitud puede
    # reconstruirlo -- un avion en pista con QNH alto informa altitud negativa
    # y uno detenido en plataforma puede no informar altitud en absoluto. Es
    # dato nuevo, no una copia.
    import sqlite3

    # mode=ro y no sqlite3.connect(path) a secas: esto corre en el hilo de
    # request de FastAPI MIENTRAS el Recorder escribe en la misma base. Una
    # conexion de lectura-escritura sobre una base con journal abierto puede
    # disparar rollback recovery y ESCRIBIR en el archivo desde el lector.
    #
    # Se arma con Path.as_uri() y no interpolando el string: en Windows la ruta
    # trae barras invertidas y espacios ("OneDrive\Desktop\..."), que en una URI
    # de SQLite no son ruta valida. as_uri() devuelve file:///C:/... ya
    # escapado.
    from pathlib import Path as _Path
    conn = sqlite3.connect(_Path(path).absolute().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # ORDER BY id y no ORDER BY epoch, y con WHERE id > ?: los dos ordenes
    # coinciden sobre los datos de hoy (0 inversiones de epoch contra id sobre
    # 10973 filas, y en los empates SQLite recorria idx_adsb_epoch por rowid
    # ascendente), pero epoch no sirve de cursor -- tiene 598 duplicados
    # consecutivos, asi que `>` pierde filas y `>=` las repite. id es INTEGER
    # PRIMARY KEY AUTOINCREMENT: unico, monotono, y sigue siendo monotono
    # aunque el reloj del sistema salte hacia atras. La consulta incremental
    # mide 0.055-0.061 ms y se mantiene plana mientras la tabla crece 40x,
    # contra 39.7 ms de la misma consulta filtrando por utc, que no tiene
    # indice. Que los dos ordenes coincidan es una propiedad de ESTOS datos y
    # por eso la vigila test_adsb_incremental.py.
    rows = conn.execute("SELECT * FROM adsb_log WHERE id > ? ORDER BY id",
                        (desde,)).fetchall()
    # DESPUES del SELECT y a proposito: si el grabador inserto algo entremedio,
    # estos dos numeros salen mas GRANDES, nunca mas chicos. La prueba de
    # rebobinado de LectorIncremental es de un solo lado (`max_id < cursor`,
    # `total < esperado`), asi que una carrera con el grabador no la puede
    # disparar por error.
    # DOS sentencias y no "SELECT max(id), count(*)" en una: juntas obligan a
    # SQLite a recorrer la tabla leyendo todas las columnas. Medido sobre una
    # tabla de 1 000 000 de filas: juntas 33.9 ms, separadas 0.039 ms el max
    # (por indice, O(1)) + 7.2 ms el count (recorre solo el indice). Sobre las
    # 11 823 de hoy son 0.09 + 0.09 ms.
    max_id = conn.execute("SELECT max(id) FROM adsb_log").fetchone()[0] or 0
    total = conn.execute("SELECT count(*) FROM adsb_log").fetchone()[0] or 0
    estado = (max_id, total)
    conn.close()
    cursor = rows[-1]["id"] if rows else desde
    # El id de CADA fila viaja aparte, no se reconstruye contando desde el
    # cursor. Contar da lo mismo mientras los ids sean consecutivos, pero un
    # solo DELETE los deja con huecos y a partir de ahi los ids reconstruidos
    # quedarian por DEBAJO de los reales -- o sea que un cliente con el cursor
    # al dia dejaria de recibir puntos que si son nuevos. Es exactamente la
    # falla que no hace ruido: la traza se corta y el mapa se ve perfecto.
    ids = [row["id"] for row in rows]
    return [
        Observation(
            timestamp=row["epoch"], icao24=row["icao24"],
            registration=row["registration"], callsign=row["callsign"],
            altitude_ft=row["altitude_ft"], ground_speed_kt=row["ground_speed_kt"],
            vertical_rate_fpm=row["vertical_rate_fpm"],
            # La tabla tiene estas dos columnas desde el principio y
            # adsb_record.py las escribe bien, pero este cargador las
            # descartaba: la posicion llegaba a la base y se perdia al releerla.
            # Importa mas de lo que parece porque es este cargador el que
            # alimenta la tabla de cobertura de la pagina de analisis, o sea la
            # unica forma de distinguir "la antena no oye" de "el codigo pierde
            # el dato". Un cargador que tira columnas hace mentir al informe de
            # honestidad justamente sobre si mismo.
            latitude=row["latitude"], longitude=row["longitude"],
            # keys() y no row["signal_dbfs"] directo: una base grabada antes de
            # que existiera la columna no la tiene, y sqlite3.Row levanta
            # IndexError en vez de dar None. Sin esto, leer una base vieja
            # reventaria en vez de degradar a "esta fuente no lo media".
            signal_dbfs=(row["signal_dbfs"] if "signal_dbfs" in row.keys() else None),
            track_deg=(row["track_deg"] if "track_deg" in row.keys() else None),
            # bool() explicito y solo si no es None: SQLite devuelve 1/0/NULL,
            # y pasar el entero crudo haria que `is True` -- que es como se
            # consulta un tri-estado -- fallara contra un 1 legitimo.
            on_ground_reported=(
                None if "on_ground_reported" not in row.keys()
                or row["on_ground_reported"] is None
                else bool(row["on_ground_reported"])),
        )
        for row in rows
    ], ids, cursor, estado



# --- lectura incremental --------------------------------------------------
# Todo lo de aca abajo existe por UN numero medido: load_db('adsb_log.db')
# tardaba 153 ms sobre 10873 filas y crece LINEALMENTE (40x filas = 40.4x
# tiempo), o sea 3.64 s a 29 dias de grabacion 24/7. Un mapa que pollea cada 5 s
# no puede pagar eso, y menos cuando el 78 % de los refrescos medidos (279 de
# 360 sobre la ultima hora real) no trae ni una posicion nueva.

# Ventana de dibujo por defecto. NO recorta los agregados -- observations,
# mediana, p95 y maximo siguen siendo de TODA la grabacion -- solo cuanta traza
# viaja en la carga completa. Hoy hay 55 trazas en 17.2 h; a 24/7 serian ~77 por
# dia y la respuesta completa creceria sin techo. El numero que quedo afuera
# viaja SIEMPRE (trazas_fuera_de_ventana / puntos_fuera_de_ventana, con el cero
# explicito) porque una ventana silenciosa es un descarte silencioso.
VENTANA_DIBUJO_S = 3 * 3600.0

# Cada punto son 6 doubles: id, t, lat, lon, alt, km. El id va guardado porque
# es lo que permite contestar "que hay de nuevo desde el cursor N" a CADA
# cliente por separado, sin estado por pestana en el servidor.
CAMPOS_PUNTO = 6
_NADA = float("nan")


def _o_nada(valor):
    return _NADA if valor is None else float(valor)


def _o_none(valor):
    return None if valor != valor else valor      # NaN es el unico != a si mismo


class _Aeronave:
    """El estado por aeronave que hace falta para dibujarla, ya reducido.

    No guarda Observation: una lista de Observation mide 313 B/obs = 143 MB a
    30 dias de grabacion. Los mismos puntos en array('d') miden 61 B/punto
    medidos = 8.4 MB.
    """
    __slots__ = ("icao24", "puntos", "callsign", "registration", "track_deg",
                 "ultimo_t")

    def __init__(self, icao24: str):
        self.icao24 = icao24
        self.puntos = array("d")
        self.callsign = None
        self.registration = None
        self.track_deg = None
        self.ultimo_t = 0.0

    def __len__(self) -> int:
        return len(self.puntos) // CAMPOS_PUNTO

    def punto(self, i: int) -> dict:
        base = i * CAMPOS_PUNTO
        p = self.puntos
        return {"lat": p[base + 2], "lon": p[base + 3],
                "alt": _o_none(p[base + 4]), "t": p[base + 1],
                "km": _o_none(p[base + 5])}

    def desde_id(self, desde: int) -> int:
        """Indice del primer punto con id > desde. Busqueda binaria: los ids
        de una aeronave son crecientes porque se alimentan en orden de cursor.
        """
        lo, hi = 0, len(self)
        while lo < hi:
            medio = (lo + hi) // 2
            if self.puntos[medio * CAMPOS_PUNTO] <= desde:
                lo = medio + 1
            else:
                hi = medio
        return lo

    def desde_tiempo(self, corte: float) -> int:
        """Indice del primer punto con t >= corte (la ventana de dibujo)."""
        lo, hi = 0, len(self)
        while lo < hi:
            medio = (lo + hi) // 2
            if self.puntos[medio * CAMPOS_PUNTO + 1] < corte:
                lo = medio + 1
            else:
                hi = medio
        return lo


class LectorIncremental:
    """Un cursor sobre adsb_log que solo avanza, con el estado ya reducido.

    El cursor es la columna `id` y no `epoch` ni `utc`, y la eleccion no es de
    estilo. `epoch` NO es unico: 10275 valores distintos sobre 10873 filas, 598
    duplicados consecutivos, asi que un cursor por epoch con `>` PIERDE filas y
    con `>=` las REPITE -- y una posicion perdida deja un agujero en la traza
    que nadie va a notar mirando el mapa. `utc` es unica en la practica pero no
    tiene indice: la misma consulta filtrando por utc pasa de 0.46 ms a 39.7 ms
    cuando la tabla crece 40x (SCAN de tabla entera), contra 0.055 -> 0.061 ms
    filtrando por id. Y `id` es INTEGER PRIMARY KEY AUTOINCREMENT, o sea que
    sigue siendo monotono aunque el reloj del sistema salte hacia atras.

    Una instancia por proceso y por base, con lock: el PositionGate es un
    reductor con estado (la ultima posicion ACEPTADA de cada aeronave es la
    referencia de R2) y darle la misma fila dos veces le rebobina esa
    referencia. Por eso avanzar() es lo unico que escribe y solo va hacia
    adelante.

    Que alimentar el gate de a pedazos de lo mismo que alimentarlo de una no es
    una suposicion: se verifico contra load_db en lotes irregulares de 1, 3, 17,
    50 y 113 filas, comparando contadores, rechazos, json.dumps(tracks) y
    json.dumps(coverage_report). Lo vigila test_adsb_incremental.py, porque
    descansa en dos propiedades de ESTOS datos -- que epoch no decrece con id, y
    que en los empates SQLite recorria idx_adsb_epoch por rowid ascendente.
    """

    def __init__(self, path: str, ref=None, *, cilindro=None):
        self.path = str(path)
        self._ref = ref
        self._lock = _threading.RLock()
        self.cilindro = cilindro
        # Cuantas veces se detecto que la base RETROCEDIO y hubo que
        # reconstruir. Viaja al navegador: un lector que se reconstruyo solo
        # cambio de golpe todos los numeros de la pantalla, y que eso pase sin
        # decirlo es la misma clase de mentira que un recorte silencioso.
        self.rebobinados = 0
        self.ultimo_rebobinado: dict | None = None
        self.ms_reconstruccion = None
        self._reset_estado()

    def _reset_estado(self) -> None:
        """Todo el estado reducido, de cero. Lo usan __init__ y el rebobinado.

        Un metodo y no codigo repetido porque olvidarse UN contador aca deja un
        lector que mezcla la base vieja con la nueva, que es peor que el bug que
        esto arregla: los numeros quedarian plausibles y mal.
        """
        self.cursor = 0
        self.gate = PositionGate(**({"ref": self._ref} if self._ref is not None else {}))

        self.aviones: dict[str, _Aeronave] = {}
        # Los agregados de coverage_report, todos incrementales: contadores o
        # extremos monotonos. La unica excepcion son mediana y p95, que salen
        # de este array ordenado con bisect.insort -- EXACTAS, no aproximadas.
        # Un histograma de 0.1 km ahorraria memoria (47 KB constantes contra
        # 13.4 MB al ano) pero el error medido es 0.047 km en el p95 y la
        # pagina imprime con toFixed(1): moveria el digito que se ve, en la
        # pagina cuyo punto es que los numeros se puedan auditar.
        self.distancias = array("d")
        self.observaciones = 0
        self.icaos: set[str] = set()
        self.con_altitud = 0
        self.min_altitud = None
        self.en_tierra = 0
        self.bajo_3000 = 0
        self.ultimo_epoch = None
        self._ids_rechazo: list[int] = []

        # El resumen por aeronave que consume aeropuerto.informe(). Se acumula
        # aca y no se recalcula sobre las observaciones porque el estado
        # residente ya no las guarda. Es O(1) por aeronave: primera altura,
        # minima con su punto, ultima altura, conteo, ultimo rumbo y ultimo
        # distintivo DENTRO del cilindro.
        # Lo construye aeropuerto.acumular_en_cilindro(), que es EL MISMO
        # acumulador que usa la ruta de siempre. Antes habia una copia de esa
        # logica aca abajo y las dos tenian que producir el mismo dict, vigiladas
        # por test_adsb_incremental; con la segmentacion por pasada el estado
        # dejo de ser un dict plano por direccion -hay que recordar el ultimo
        # timestamp y el numero de pasada de cada una- y mantener eso duplicado
        # habria costado el doble y divergido igual.
        self.cil_estado: dict | None = None
        self._cache_informe = (None, None)      # (clave de cursor, Informe)

    # -- avance -----------------------------------------------------------
    def avanzar(self) -> dict:
        """Lee las filas con id > cursor y las reduce. Devuelve el delta.

        "Solo hacia adelante" vale mientras el ARCHIVO solo crezca, y no siempre
        crece: si alguien borra adsb_log.db y aprieta Iniciar en /adsb, el
        grabador corre en ESTE mismo proceso y los ids vuelven a 1 con el lector
        vivo. Medido antes de este chequeo: con el cursor en 8000 y la base
        recreada con 300 filas, tres polls seguidos daban nuevos=0 y hasta la
        carga completa contestaba 35 trazas, 777 puntos y observations=8000
        sobre una base de 300 filas. O sea el mapa sirviendo para siempre datos
        que ya no existen en disco, y el unico sintoma visible era lag_s
        creciendo -- la pagina diciendo "grabacion detenida" justo mientras la
        grabacion corre.
        """
        with self._lock:
            primera_vez = self.cursor == 0
            t0 = _time.perf_counter()
            nuevas, ids, cursor, (max_id, total) = _read_db_con_ids(
                self.path, desde=self.cursor)
            rebobinado = self._detectar_rebobinado(max_id, total, len(nuevas))
            if rebobinado is not None:
                # Reconstruir y no parchear: el gate es un reductor con estado
                # y no hay forma de "sacarle" las filas que ya no estan.
                self._reset_estado()
                nuevas, ids, cursor, _ = _read_db_con_ids(self.path, desde=0)
                primera_vez = True
            desde = self.cursor
            aceptadas = 0
            tocadas: dict[str, int] = {}
            rechazos_nuevos = 0
            for o, fila_id in zip(nuevas, ids):
                if self._absorber(o, fila_id):
                    aceptadas += 1
                    tocadas[o.icao24] = tocadas.get(o.icao24, 0) + 1
                elif len(self.gate.rechazos) > len(self._ids_rechazo):
                    self._ids_rechazo.append(fila_id)
                    rechazos_nuevos += 1
            self.cursor = cursor
            ms = (_time.perf_counter() - t0) * 1000.0
            if primera_vez:
                self.ms_reconstruccion = round(ms, 1)
            return {"desde": desde, "cursor": self.cursor, "filas": len(nuevas),
                    "posiciones": aceptadas, "tocadas": tocadas,
                    "rechazos_nuevos": rechazos_nuevos, "ms": round(ms, 2),
                    "rebobinado": rebobinado}

    def _detectar_rebobinado(self, max_id: int, total: int, leidas: int) -> dict | None:
        """La base retrocedio: hay MENOS de lo que este lector ya absorbio.

        Las dos pruebas son de un solo lado a proposito. `max_id < cursor`
        agarra el caso reproducido (archivo borrado y recreado, ids desde 1).
        `total < observaciones + leidas` agarra el otro que no mueve max_id: un
        DELETE en el medio, que deja el cursor valido y las trazas con puntos de
        filas que ya no estan. Como los dos numeros se leen DESPUES del SELECT,
        una insercion concurrente del grabador solo los agranda y nunca dispara
        un falso positivo.
        """
        esperado = self.observaciones + leidas
        if max_id >= self.cursor and total >= esperado:
            return None
        motivo = ("la base se recreo o se trunco: su id maximo es "
                  f"{max_id} y este lector iba por {self.cursor}"
                  if max_id < self.cursor else
                  f"se borraron filas: la base tiene {total} y este lector "
                  f"ya habia absorbido {esperado}")
        self.rebobinados += 1
        self.ultimo_rebobinado = {
            "motivo": motivo, "cursor_previo": self.cursor,
            "observaciones_previas": self.observaciones,
            "max_id": max_id, "filas_en_base": total,
            "epoch": _time.time(),
        }
        return dict(self.ultimo_rebobinado)

    def _absorber(self, o: Observation, fila_id: int) -> bool:
        self.observaciones += 1
        self.icaos.add(o.icao24)
        if o.altitude_ft is not None:
            self.con_altitud += 1
            if self.min_altitud is None or o.altitude_ft < self.min_altitud:
                self.min_altitud = o.altitude_ft
            if o.altitude_ft < 3000:
                self.bajo_3000 += 1
        if o.is_on_ground:
            self.en_tierra += 1
        if self.ultimo_epoch is None or o.timestamp > self.ultimo_epoch:
            self.ultimo_epoch = o.timestamp

        avion = self.aviones.get(o.icao24)
        if avion is None:
            avion = self.aviones[o.icao24] = _Aeronave(o.icao24)
        # El rumbo se toma de CUALQUIER observacion, tenga posicion o no: los
        # mensajes de velocidad y los de posicion son distintos y casi nunca
        # vienen juntos. Es el mismo criterio que adsb_report.tracks().
        if o.track_deg is not None:
            avion.track_deg = o.track_deg

        motivo = self.gate.feed(o)
        if motivo is not None or o.latitude is None or o.longitude is None:
            return False

        km = distance_km(o.latitude, o.longitude, self.gate.ref)
        if km is not None:
            _insort(self.distancias, km)
        avion.puntos.extend((float(fila_id), o.timestamp, o.latitude, o.longitude,
                             _o_nada(o.altitude_ft), _o_nada(km)))
        avion.ultimo_t = o.timestamp
        if o.callsign:
            avion.callsign = o.callsign.strip()
        if o.registration:
            avion.registration = o.registration
        self._absorber_cilindro(o)
        return True

    def _absorber_cilindro(self, o: Observation) -> None:
        """El estado O(1) por PASADA que aeropuerto.informe() necesita.

        No reimplementa nada: delega en aeropuerto.acumular_en_cilindro(), el
        unico acumulador. Tener dos copias de esto era la peor duplicacion
        posible -- son los conteos publicados (aterrizajes, despegues,
        frustradas) los que estan en juego, y dos implementaciones pueden
        divergir sin que nadie se entere hasta que las tarjetas de dos paginas
        no coinciden.
        """
        import aeropuerto
        if self.cilindro is None:
            return
        if self.cil_estado is None:
            self.cil_estado = aeropuerto.nuevo_resumen()
        aeropuerto.acumular_en_cilindro(self.cil_estado, o, self.cilindro)


    # -- lecturas ----------------------------------------------------------
    def coverage(self) -> dict:
        """Lo mismo que coverage_report(), sin tener las observaciones."""
        d = self.distancias
        n = len(d)
        rechazos = self.gate.rechazos
        return {
            "observations": self.observaciones,
            "aircraft": len(self.icaos),
            "with_altitude": self.con_altitud,
            "min_altitude_ft": self.min_altitud,
            "on_ground_observations": self.en_tierra,
            "below_3000ft": self.bajo_3000,
            "can_see_runway_level": self.en_tierra > 0 or self.bajo_3000 > 0,
            "with_position": n,
            "max_distance_km": d[-1] if n else None,
            "p95_distance_km": d[min(int(n * 0.95), n - 1)] if n else None,
            "median_distance_km": d[n // 2] if n else None,
            "min_distance_km": d[0] if n else None,
            "positions_evaluated": self.gate.evaluadas,
            "positions_rejected": len(rechazos),
            "rejected_by_reason": dict(self.gate.por_motivo),
            "rejected_max_km": (round(max(r.km for r in rechazos), 1)
                                if rechazos else None),
            "rejected_detail": [r.as_dict() for r in rechazos],
            "surface_rule_exercised": bool(self.gate.superficie_evaluadas),
            # Salen del gate, que es el mismo objeto en las dos rutas: por eso
            # no hay dos implementaciones que puedan divergir. Ver
            # coverage_report().
            "surface_evaluated": self.gate.superficie_evaluadas,
            "surface_beyond_horizon": self.gate.superficie_fuera_de_horizonte,
            "surface_beyond_horizon_aircraft":
                self.gate.superficie_fuera_de_horizonte_aeronaves,
            "surface_beyond_horizon_max_km": (
                round(self.gate.superficie_fuera_max_km, 2)
                if self.gate.superficie_fuera_max_km is not None else None),
            "surface_horizon_km": round(horizonte_km(0), 2),
            "receiver_misplaced": self.gate.antena_no_esta_donde_dice,
            "gate_applied": True,
        }

    def _traza(self, avion: _Aeronave, desde_indice: int) -> dict:
        rumbo, origen = avion.track_deg, "transmitido"
        if rumbo is None:
            rumbo, origen = self._rumbo_calculado(avion)
        matricula = avion.registration
        if matricula is None and _aircraft_db is not None and _aircraft_db.available():
            matricula = (_aircraft_db.lookup(avion.icao24) or {}).get("registration") or None
        return {
            "icao24": avion.icao24,
            "callsign": avion.callsign,
            "registration": matricula,
            "track_deg": rumbo,
            "track_source": origen,
            "points": [avion.punto(i) for i in range(desde_indice, len(avion))],
        }

    @staticmethod
    def _rumbo_calculado(avion: _Aeronave):
        """El respaldo de adsb_report._rumbo_entre, sobre los arrays."""
        if len(avion) < 2:
            return None, None
        from math import atan2, cos, degrees, radians
        b, a = avion.punto(len(avion) - 1), avion.punto(len(avion) - 2)
        lat_media = radians((a["lat"] + b["lat"]) / 2)
        norte = (b["lat"] - a["lat"]) * 111.32
        este = (b["lon"] - a["lon"]) * 111.32 * cos(lat_media)
        if (norte * norte + este * este) ** 0.5 < 0.2:
            return None, None
        return (degrees(atan2(este, norte)) + 360.0) % 360.0, "calculado"

    def trazas(self, *, ventana_s: float | None = None, ahora: float | None = None,
               solo: set | None = None) -> tuple[list[dict], int, int]:
        """Las trazas enteras, recortadas a la ventana. Devuelve tambien lo que
        quedo afuera: trazas completas descartadas y puntos recortados.
        """
        corte = 0.0
        if ventana_s is not None:
            base = ahora if ahora is not None else _time.time()
            corte = base - ventana_s
        salida, fuera_trazas, fuera_puntos = [], 0, 0
        for avion in self.aviones.values():
            total = len(avion)
            if not total or (solo is not None and avion.icao24 not in solo):
                continue
            i0 = avion.desde_tiempo(corte) if corte else 0
            fuera_puntos += i0
            if i0 >= total:
                fuera_trazas += 1
                continue
            salida.append(self._traza(avion, i0))
        # El mismo orden que adsb_report.tracks() para que la carga completa sea
        # comparable byte a byte con el camino de hoy. El cliente reordena por
        # ultima posicion: ESTE orden se da vuelta solo (38 de 55 pares vecinos
        # estan a una posicion de invertirse) y barajaria la lista lateral.
        salida.sort(key=lambda t: len(t["points"]), reverse=True)
        return salida, fuera_trazas, fuera_puntos

    def fuera_de_ventana(self, ventana_s: float, ahora: float | None = None,
                         solo: set | None = None) -> tuple[int, int]:
        """Cuanto deja afuera la ventana de dibujo, sin construir las trazas.

        Se publica SIEMPRE, con el cero explicito. Una ventana que recorta en
        silencio es un descarte en silencio, que es lo unico que este repo no
        se permite.
        """
        corte = (ahora if ahora is not None else _time.time()) - ventana_s
        trazas = puntos = 0
        for avion in self.aviones.values():
            total = len(avion)
            if not total or (solo is not None and avion.icao24 not in solo):
                continue
            i0 = avion.desde_tiempo(corte)
            puntos += i0
            if i0 >= total:
                trazas += 1
        return trazas, puntos

    def informe_aeropuerto(self, codigo: str | None = None):
        """aeropuerto.informe() sobre el resumen residente, cacheado por CURSOR.

        Por cursor y no por tiempo: informe() es funcion pura del conjunto de
        observaciones, asi que el cache por cursor es EXACTO -- no sirve un
        numero viejo sin decir cuan viejo es, que es lo que hace el cache por
        tiempo de adsb_service._resumen_historico. Y acierta igual: el 78 % de
        los refrescos medidos (279 de 360) no trae ni una fila.

        Incrementalizar la clasificacion aparte no vale la pena: son 1.6 ms hoy,
        17.8 ms a 7 dias y 69.8 ms a 29 dias, o sea 2.3-2.4 % del endpoint,
        contra el 91 % que se llevaba load_db.
        """
        if self.cilindro is None:
            return None
        clave = (self.cursor, codigo)
        if self._cache_informe[0] == clave:
            return self._cache_informe[1]
        import aeropuerto
        # Las identidades salen de self.aviones -el acumulador GLOBAL, que ve
        # todos los mensajes- y no del resumen del cilindro, que solo tiene lo de adentro
        # del cilindro. El distintivo viaja en el 3% de los mensajes y casi nunca
        # cae justo ahi.
        import identidad
        identidades = identidad.resolver_desde_resumen(
            {a.icao24: {"n": len(a.puntos) // 6 or 1, "callsign": a.callsign}
             for a in self.aviones.values()})
        estado = self.cil_estado or aeropuerto.nuevo_resumen()
        inf = aeropuerto.informe_desde_resumen(
            estado["por_pasada"], estado["posiciones"],
            codigo or self.cilindro["codigo"], identidades,
            # Los inferidos salen del MISMO estado acumulado, asi que esta ruta
            # y la de siempre no pueden divergir: es el mismo motivo por el que
            # hay un solo acumular_en_cilindro().
            aeropuerto.despegues_inferidos(estado))
        self._cache_informe = (clave, inf)
        return inf

    def delta_trazas(self, desde: int, solo: set | None = None) -> list[dict]:
        """Solo los puntos con id > desde, por aeronave tocada."""
        salida = []
        for avion in self.aviones.values():
            if solo is not None and avion.icao24 not in solo:
                continue
            i0 = avion.desde_id(desde)
            if i0 >= len(avion):
                continue
            salida.append(self._traza(avion, i0))
        salida.sort(key=lambda t: len(t["points"]), reverse=True)
        return salida

    def rechazos_desde(self, desde: int) -> list[dict]:
        i0 = _bisect_right(self._ids_rechazo, desde)
        return [r.as_dict() for r in self.gate.rechazos[i0:]]

    def lag_s(self, ahora: float | None = None) -> float | None:
        """Cuan vieja es la fila mas nueva que este lector ve.

        Es EL numero de la pagina. Un poll exitoso cada 5 s sobre una base
        atrasada 905 s es la forma mas convincente de mentir, y hasta que la
        grabacion arranque de nuevo con el commit por tiempo este numero va a
        seguir dando minutos -- que es la verdad medida, no un error del mapa.
        """
        if self.ultimo_epoch is None:
            return None
        return max(0.0, (ahora if ahora is not None else _time.time()) - self.ultimo_epoch)


if __name__ == "__main__":
    import argparse
    from datetime import datetime

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", nargs="?", help="CSV grabado por adsb_record.py")
    parser.add_argument("--db", help="Leer de adsb_log.db en vez de un CSV")
    args = parser.parse_args()

    if args.db:
        observations, gate = load_db(args.db)
        origen = args.db
    elif args.csv:
        observations, gate = load_csv(args.csv)
        origen = args.csv
    else:
        parser.error("indica un CSV o --db")

    cobertura = coverage_report(observations, gate)
    print(f"=== {origen}")
    print(f"{cobertura['observations']} observaciones, {cobertura['aircraft']} aeronaves")
    alt = cobertura["min_altitude_ft"]
    print(f"altitud minima recibida : {alt:.0f} ft" if alt is not None else "sin altitudes")
    print(f"observaciones en tierra : {cobertura['on_ground_observations']}")
    print(f"observaciones <3000 ft  : {cobertura['below_3000ft']}")
    print(f"con posicion            : {cobertura['with_position']}")
    if cobertura["with_position"]:
        print(f"alcance p95 / maximo    : {cobertura['p95_distance_km']:.1f} / "
              f"{cobertura['max_distance_km']:.1f} km del receptor")
    print(imprimir_rechazos(gate))
    print()

    if not cobertura["can_see_runway_level"]:
        print("ATENCION: esta grabacion no contiene ni una sola observacion a")
        print("nivel de pista. Sin recibir aviones cerca del suelo es imposible")
        print("detectar aterrizajes o despegues -- no porque no hayan ocurrido,")
        print("sino porque la antena no los escucha desde donde esta.")
        print()

    detector = EventDetector()
    eventos = [e for o in sorted(observations, key=lambda x: x.timestamp)
               if (e := detector.feed(o))]

    print(f"EVENTOS DETECTADOS: {len(eventos)}")
    for e in eventos:
        hora = datetime.fromtimestamp(e.timestamp).strftime("%H:%M:%S")
        print(f"  {hora}  {e.event_type:8s} {e.icao24}  {e.callsign or '-':9s} ({e.reason})")
    if detector.gaps_skipped or detector.cooldown_skipped:
        print(f"\n  transiciones descartadas por corte de senal: {detector.gaps_skipped}")
        print(f"  descartadas por enfriamiento               : {detector.cooldown_skipped}")
