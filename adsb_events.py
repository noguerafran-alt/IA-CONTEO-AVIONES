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

from dataclasses import dataclass, field, replace

from adsb import Observation
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
    return apply_position_gate(_read_db(path))


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
            ))
    return rows


def _read_db(path: str) -> list[Observation]:
    # Decision explicita sobre la columna on_ground, que se escribe y nadie
    # lee: NO se agrega a Observation. El portador de "esta en tierra" es
    # altitude_ft=0.0, que decoded_to_observation ya fija para los mensajes de
    # superficie (que por diseno no traen altitud) y que si viaja de ida y
    # vuelta por la base. Duplicar el estado en dos columnas invita a que se
    # contradigan. La columna on_ground queda para leer el CSV en Excel.
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
    rows = conn.execute("SELECT * FROM adsb_log ORDER BY epoch").fetchall()
    conn.close()
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
        )
        for row in rows
    ]


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
