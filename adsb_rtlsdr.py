"""Read ADS-B directly from an RTL-SDR dongle: no separate GUI decoder needed.

This is the no-extra-software path. `rtl_adsb.exe` (from the official RTL-SDR
Blog release -- the one INSTALAR-ADSB.bat fetches, which bundles the drivers
the V4 dongle actually needs) does only one job: turn radio into raw AVR hex
lines on stdout, one per received message, like:

    *8D4840D6202CC371C32CE0576098;

Everything past that -- turning those hex messages into an aircraft's icao,
callsign, altitude, speed and position -- is decoded in Python by pyModeS's
PipeDecoder, which is stateful: a single message rarely carries a full
picture, so it holds recent messages per aircraft and fills in what it can as
they arrive.

Why this instead of RTL1090 or another GUI tool: those are a second program to
install, configure and keep running, each expecting its own port for the
BaseStation feed (adsb_sbs.py has to guess which). This needs only the single
official binary plus a Python decoder already in requirements.txt.

LA POSICION LLEGA POR DOS CAMINOS DISTINTOS, y eso es contraintuitivo:

  En vuelo (BDS 0,5 / TC 9-18) hace falta un par de tramas CPR par+impar Y
  ADEMAS esperar el bootstrap de pyModeS 3.6: no emite la primera posicion
  hasta juntar 3 candidatos CPR consistentes entre si
  (_BOOTSTRAP_MIN_CLUSTER_SIZE en _pipe.py). Medido con el par clasico
  ['8D40621D58C382D690C8AC2863A7', '8D40621D58C386435CC412692AD6'] repetido:
  la primera latitude sale en el 6to mensaje, no en el 2do, y el valor exacto
  del par (52.2572021484375, 3.91937255859375) en el 7mo. O sea que arrancar
  el receptor y no ver posiciones durante los primeros mensajes de una
  aeronave es lo normal, no una falla.

  En superficie (BDS 0,6 / TC 5-8) alcanza UN mensaje suelto, porque se
  resuelve contra surface_ref (ver receiver.py) por el camino de referencia
  unica de message.py, que no pasa por el bootstrap. Contrapartida honesta:
  los controles anti-FRUIT de pyModeS estan condicionados a bds == "0,5"
  (_pipe.py: el chequeo cruzado de altitud y el fallback de referencia local
  solo se aplican a posiciones en vuelo), asi que una trama con CRC afortunado
  puede emitir una posicion en pista sin corroboracion. Si eso molesta, el
  filtro barato es descartar posiciones de superficie a mas de unos pocos km
  de la referencia: un avion en pista siempre esta cerca. Sin surface_ref esos
  mensajes se reciben igual pero salen sin latitude, con el CPR crudo y nada
  mas.

  Lo de superficie esta verificado por replay de mensajes construidos (0.25 a
  0.62 m de error sobre los tres aeropuertos de la zona), pero NO validado con
  trafico real todavia: esta antena no recibio aun un solo avion en tierra.

CUIDADO con los nombres de las claves: pyModeS emite "latitude"/"longitude",
NO "lat"/"lon". Los nombres cortos son los de dump1090 aircraft.json, que
adsb.py si usa correctamente -- de ahi salio la confusion que dejo la posicion
en None el 100% de las veces durante toda la primera etapa del proyecto.

Uso:
  python adsb_rtlsdr.py --watch          escuchar y mostrar en vivo
  python adsb_rtlsdr.py --exe ruta.exe   si rtl_adsb.exe no esta en tools/
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pyModeS as pms

from adsb import DEFAULT_HISTORY_S, Observation
from receiver import parse_surface_ref, surface_ref_default

DEFAULT_EXE = Path(__file__).parent / "tools" / "rtlsdr" / "rtl_adsb.exe"
# *hex...; with optional leading @timestamp, both formats rtl_adsb can emit.
AVR_LINE_RE = re.compile(r"^(?:@[0-9A-Fa-f]+)?\*([0-9A-Fa-f]+);?$")


def parse_avr_line(line: str) -> str | None:
    """Extract the raw hex message from one AVR-format line, or None."""
    match = AVR_LINE_RE.match(line.strip())
    return match.group(1) if match else None


def decoded_to_observation(icao24: str, decoded: dict, timestamp: float,
                           signal_dbfs: float | None = None) -> Observation:
    """Merge one PipeDecoder result into an Observation.

    PipeDecoder tracks state per aircraft internally, but each call only
    returns what THIS message updated -- so decoded here is a partial view,
    same as one SBS-1 line. That is fine: the rolling history in AdsbRecorder/
    SbsRecorder/here already handles partial Observations by accumulating them
    over time per aircraft, and match_adsb.py treats missing fields as
    "unknown" rather than requiring a complete record.

    Todo se lee con .get() y se compara contra None, nunca con `in decoded`:
    pyModeS distingue "la clave no vino" de "la clave vino vacia", y el segundo
    caso es frecuente justo en los mensajes que mas interesan. Ver abajo.
    """
    # BDS 0,6 (superficie, TC 5-8) NO lleva altitud: su payload es movimiento,
    # track, T, F y CPR, y nada mas (bds06.py lo dice en su docstring, "No
    # altitude (aircraft is on the ground)"). O sea que la AUSENCIA de altitud
    # en un mensaje de superficie es la senal de que el avion esta en el suelo,
    # no un dato faltante -- y es la unica evidencia directa que hay de un
    # avion en pista o rodando, el caso de uso central de este proyecto.
    typecode = decoded.get("typecode")
    surface = decoded.get("bds") == "0,6" or (typecode is not None and 5 <= typecode <= 8)
    # 'vertical_status' (de acas.py, DF0/16) es la otra senal real de tierra,
    # con valores 'on-ground'/'airborne'. NO se usa 'vr_source': pyModeS solo
    # lo emite como "BARO" o "GNSS" (bds09.py), o sea el ORIGEN del regimen
    # vertical, nunca un estado de vuelo -- compararlo con "ground" era una
    # rama que no podia ejecutarse nunca.
    altitude = decoded.get("altitude")
    on_ground = (surface or decoded.get("vertical_status") == "on-ground"
                 or altitude == 0)

    # bds06.py siempre devuelve la clave "groundspeed", con valor None cuando
    # MOV==0 o MOV>124 (avion detenido o sin informacion de movimiento): un
    # avion parado en plataforma o en cabecera de pista. bds09.py hace lo mismo
    # con "vertical_rate" cuando la magnitud es 0, o sea vuelo nivelado, el
    # caso mas comun del aire. Con el guard viejo (`if clave in decoded`, que
    # es True con valor None) el float() lanzaba TypeError, la excepcion subia
    # hasta _loop, mataba rtl_adsb.exe y lo relanzaba a los 3 s -- con lo cual
    # el bootstrap CPR nunca llegaba a juntar sus 3 posiciones.
    groundspeed = decoded.get("groundspeed")
    vertical_rate = decoded.get("vertical_rate")
    return Observation(
        timestamp=timestamp,
        icao24=icao24,
        callsign=(decoded.get("callsign") or "").strip() or None,
        altitude_ft=float(altitude) if altitude is not None else (0.0 if on_ground else None),
        # Solo velocidad respecto al suelo. Se elimino el fallback a "ias":
        # no es una clave de salida de pyModeS (es un nombre interno del estado
        # por aeronave, _pipe.py lo escribe en self._state), asi que era codigo
        # muerto. Y las claves que si existen -- "airspeed", "true_airspeed" --
        # son velocidad AEREA: con 30 kt de viento no es lo mismo, y mezclarlas
        # aca ensuciaria max_speed_kt del informe.
        ground_speed_kt=float(groundspeed) if groundspeed is not None else None,
        vertical_rate_fpm=float(vertical_rate) if vertical_rate is not None else None,
        # "latitude"/"longitude", no "lat"/"lon": ver el docstring del modulo.
        latitude=decoded.get("latitude"),
        longitude=decoded.get("longitude"),
        # "track" es el rumbo sobre el SUELO (subtipo 1-2, derivado de la
        # velocidad GNSS) y "heading" es hacia donde apunta la nariz (subtipo
        # 3-4). Se prefiere track porque es el que describe por donde se esta
        # moviendo, que es lo que un mapa tiene que mostrar; con viento cruzado
        # los dos difieren varios grados y dibujar la nariz haria ver a los
        # aviones desalineados de su propia traza.
        track_deg=(decoded.get("track") if decoded.get("track") is not None
                   else decoded.get("heading")),
        signal_dbfs=signal_dbfs,
        registration=None,   # raw ADS-B carries ICAO24, not the tail number
    )


@dataclass
class RtlAdsbRecorder:
    """Runs rtl_adsb.exe as a subprocess and decodes its output with pyModeS.

    Mirrors AdsbRecorder/SbsRecorder's public surface (start/stop/around/
    snapshot/is_receiving/last_error/poll_count) so match_adsb.py and the
    rest of the pipeline do not need to know which physical source feeds them.
    """
    exe_path: Path = DEFAULT_EXE
    device_index: int = 0
    history_seconds: float = DEFAULT_HISTORY_S
    restart_delay: float = 3.0
    local_ref_window: float = 30.0
    # Referencia para posiciones en superficie. default_factory y no un valor
    # fijo para que ADSB_SURFACE_REF se lea en cada instancia, no una vez al
    # importar el modulo. Ver receiver.py para por que la referencia es la
    # antena y no un codigo de aeropuerto.
    surface_ref: tuple[float, float] | str | None = field(
        default_factory=surface_ref_default)

    _history: list[Observation] = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _process: subprocess.Popen | None = field(default=None, repr=False)
    _decoder: "pms.PipeDecoder | None" = field(default=None, repr=False)
    last_error: str | None = field(default=None, repr=False)
    poll_count: int = 0        # messages accepted (CRC valid or not verifiable)
    corrupt_count: int = 0     # messages with a bad checksum, discarded
    unverified_count: int = 0  # accepted without checksum: DF0/4/5/11/16
    phantom_count: int = 0     # direcciones vistas una sola vez y nunca repetidas
    # Cuanto se retiene una direccion sin confirmar antes de darla por ruido.
    # 60 s es lo que usa dump1090 para su cache de direcciones, y sobra: una
    # aeronave real emite varias veces por segundo, asi que si la direccion es
    # buena la segunda trama llega mucho antes.
    pendiente_ttl: float = 60.0
    # Direcciones que ya se creen reales. Una trama con CRC verificable
    # (DF17/18) la mete de una; una sin CRC verificable, recien al repetirse.
    _confirmadas: set = field(default_factory=set, repr=False)
    # Tramas sin CRC verificable de direcciones todavia no confirmadas, a la
    # espera de una segunda aparicion que las respalde. Se retienen, no se
    # descartan: si la direccion es real no se pierde el primer mensaje.
    _pendientes: dict = field(default_factory=dict, repr=False)
    waiting_for_device: bool = field(default=False, repr=False)
    # El hex crudo de la ULTIMA trama que produjo una posicion, por icao24.
    #
    # Hasta ahora el hex no se guardaba en ningun lado (parse_avr_line lo pasa
    # a decode y lo suelta), y por eso el diagnostico del bit corrupto de
    # e0b14a fue aritmetica sobre la traza y no una reproduccion: sin la trama
    # original el proximo caso tampoco se iba a poder meter en un test. Es un
    # dict de una entrada por aeronave y no un log: guardar todas las tramas
    # multiplicaria el tamano de la base por el peso del hex sin que nadie las
    # lea, y lo unico que hace falta es la que se acaba de rechazar.
    position_hex: dict[str, tuple[float, str]] = field(default_factory=dict, repr=False)

    def start(self) -> "RtlAdsbRecorder":
        if self._thread and self._thread.is_alive():
            return self
        # Resolved to absolute: a relative path here was observed to make
        # CreateProcess intermittently report "file not found" even though
        # the file exists, depending on the working directory the launcher
        # (a .bat, a different shell) started from. Absolute sidesteps that.
        self.exe_path = Path(self.exe_path).resolve()
        if not self.exe_path.exists():
            self.last_error = f"no se encontro {self.exe_path}"
            return self
        if self.surface_ref is not None:
            # pyModeS valida la referencia de forma PEREZOSA: construir el
            # PipeDecoder con surface_ref='SAXX' no falla, decodifica vuelo
            # normal sin problema, y revienta con ValueError recien en el
            # primer mensaje de superficie -- ya adentro del hilo lector, donde
            # el `except Exception` de _loop lo convierte en un relanzamiento
            # de rtl_adsb.exe cada 3 segundos para siempre. Un typo de
            # configuracion tiene que ser un mensaje, no un bucle de reinicios.
            error = self._validate_surface_ref()
            if error:
                self.last_error = error
                return self
        # surface_ref solo afecta BDS 0,6: message.py y _pipe.py chequean el
        # bds antes de usarla, asi que pasarla no puede degradar la
        # decodificacion de posiciones en vuelo.
        # max_speed_kt explicito aunque 1500 sea el default de pyModeS 3.6: es
        # el umbral con el que _motion_consistent (_pipe.py:644-666) tira
        # posiciones ANTES de que las vea nadie de este repo, y escrito se ve y
        # se puede tocar. Un default que decide que datos existen no deberia
        # estar invisible.
        self._decoder = pms.PipeDecoder(surface_ref=self.surface_ref,
                                        local_ref_window=self.local_ref_window,
                                        max_speed_kt=1500.0)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _validate_surface_ref(self) -> str | None:
        """Mensaje de error si la referencia de superficie no sirve, o None."""
        from pyModeS.position import resolve_surface_ref

        from receiver import MAX_KM_TO_ANY_AIRPORT, nearest_airport
        try:
            lat_ref, lon_ref = resolve_surface_ref(self.surface_ref)
        except (ValueError, TypeError) as exc:
            return f"surface_ref invalido ({self.surface_ref!r}): {exc}"
        # Chequeo de plausibilidad, no de rango: invertir lat/lon da un par
        # perfectamente valido que decodifica en silencio a 3246 km de donde
        # deberia. Lo unico que lo delata es que ningun aeropuerto del mundo
        # queda cerca -- el invertido de San Isidro cae a 1682 km de EGYP,
        # contra 7.3 km de SADF para el correcto. Medido.
        code, km = nearest_airport(lat_ref, lon_ref)
        if code is not None and km > MAX_KM_TO_ANY_AIRPORT:
            return (f"surface_ref ({lat_ref}, {lon_ref}) esta a {km:.0f} km del "
                    f"aeropuerto mas cercano ({code}). Revisa si lat y lon "
                    f"quedaron invertidas: el formato es 'lat,lon'.")
        return None

    def stop(self) -> None:
        self._stop.set()
        if self._process:
            self._process.terminate()
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_once()
            except Exception as exc:
                # A dongle unplugged, a driver hiccup, rtl_adsb crashing: log
                # and retry rather than dying, since the camera-side counting
                # keeps running regardless and should not be starved by a
                # flaky receiver.
                self.last_error = f"{type(exc).__name__}: {exc}"
            self._stop.wait(self.restart_delay)

    def _run_once(self) -> None:
        # stderr is captured, not discarded: rtl_adsb prints its actual reason
        # for exiting there (no device, wrong driver, device busy), and that
        # text is what turns "exited with code 1" into a message someone can
        # act on instead of a bare error code.
        # -e 1 instead of rtl_adsb's default of 5 allowed bit errors. Measured
        # on this antenna over 70-second captures, counting how many emitted
        # messages actually pass CRC:
        #
        #   -e 5 (default)   219 messages,  4 valid (1.8%),  2 position frames
        #   -e 1             139 messages, 79 valid (56.8%), 42 position frames
        #   -e 0              90 messages, 63 valid (70.0%), 28 position frames
        #
        # The default emits mostly noise -- its DF distribution is nearly
        # uniform across 16..31, which is what random bits look like, not real
        # traffic. -e 1 yields 20x more usable messages than the default; -e 0
        # is cleaner per message but throws away enough real ones to end up
        # with fewer, so -e 1 is the better operating point.
        self._process = subprocess.Popen(
            [str(self.exe_path), "-d", str(self.device_index), "-e", "1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self.last_error = None
        self.waiting_for_device = False
        try:
            for line in self._process.stdout:
                if self._stop.is_set():
                    break
                self._handle_line(line)
            code = self._process.wait(timeout=2)
            stderr_text = (self._process.stderr.read() or "").strip()
            if code not in (0, None) and not self._stop.is_set():
                message = self._describe_exit(code, stderr_text)
                self.waiting_for_device = message is None
                if message is not None:
                    self.last_error = message
                    raise RuntimeError(message)
        finally:
            if self._process.poll() is None:
                self._process.terminate()

    @staticmethod
    def _describe_exit(code: int, stderr_text: str) -> str | None:
        """Turn rtl_adsb's own stderr into a message that says what to do.

        Returns None for "no device connected" specifically: with no antenna
        plugged in yet, that is not a malfunction, it is the honest current
        state, and reporting it as a RuntimeError makes a normal situation
        read like a crash.
        """
        lowered = stderr_text.lower()
        if "no supported devices" in lowered:
            return None
        if "usb_claim_interface" in lowered or "already in use" in lowered:
            return ("el dongle esta en uso por otro programa (SDR#, RTL1090, etc). "
                    "Cerralo y volve a intentar.")
        if "error accessing" in lowered or "libusb" in lowered:
            return ("no se pudo abrir el dongle. Si es la primera vez, instala el "
                    "driver WinUSB con Zadig (ver INSTALAR-ADSB.bat).")
        detail = f": {stderr_text}" if stderr_text else ""
        return f"rtl_adsb.exe termino con codigo {code}{detail}"

    def _handle_line(self, line: str) -> None:
        hex_message = parse_avr_line(line)
        if not hex_message:
            return
        self._procesar_hex(hex_message)

    def _procesar_hex(self, hex_message: str, signal_dbfs: float | None = None) -> None:
        """Decodificar un mensaje hex y sumarlo al historial.

        Separado de _handle_line para que adsb_iq.IqRecorder pueda entrar por
        aca con su propio hex y su nivel de senal, y herede sin copiar la
        compuerta de confirmacion de direcciones, el historial y el conteo.
        Duplicar esa compuerta seria la peor clase de duplicacion: dos copias
        de una regla de aceptacion de datos que pueden divergir en silencio.
        """
        # Si llego un hex hasta aca, la cadena entera -USB, muestras,
        # demodulacion- esta funcionando AHORA, asi que el error anterior ya no
        # describe nada.
        #
        # Sin esto, _loop reintenta y se recupera pero last_error queda pegado
        # para siempre, y como is_receiving es "poll_count > 0 and last_error is
        # None", la pagina muestra "la grabacion esta activa pero NO entra nada"
        # mas el RuntimeError en rojo mientras los datos entran normalmente.
        #
        # Medido el 2026-09-03: con last_error pegado de una colision por el
        # dongle, /api/adsb/status seguia publicando el "usb_open error -3"
        # mientras positions_evaluated subia de 2075 a 2095 en 25 segundos. El
        # comentario de escuchar() dice que un tablero que dice grabar mientras
        # no recibe nada es peor que uno que se cae; al reves es igual de malo,
        # porque un error rojo permanente que convive con datos que entran
        # ensena a ignorar el renglon del error.
        if self.last_error is not None:
            self.last_error = None
        decoded = self._decoder.decode(hex_message, timestamp=time.time())
        # Tres estados, no dos. pyModeS solo fija crc_valid para DF17/18/20/21;
        # para DF0/4/5/11/16 la clave viene con valor None porque el campo de
        # paridad de esos formatos va XOR-eado con la direccion del avion y no
        # se puede verificar por si solo. El guard viejo era
        # `if not decoded.get("crc_valid")`, y `not None` es True: descartaba
        # familias enteras de mensajes sanos y los contaba como corruptos.
        # Medido con 4 mensajes reales: 1 valido, 3 "corruptos", entre ellos un
        # DF20 con altitude=30275 y groundspeed=438 y un DF0 con altitude=37000.
        crc_valid = decoded.get("crc_valid")
        if crc_valid is False:
            self.corrupt_count += 1
            return
        icao24 = decoded.get("icao")
        if not icao24:
            return
        icao24 = icao24.lower()
        ahora = time.time()

        if crc_valid is None:
            # Se acepta pero se cuenta aparte: sumarlo a los validos inflaria
            # la metrica de calidad de senal (-e 1 vs -e 5) con mensajes que
            # nadie verifico.
            self.unverified_count += 1
            if not self._direccion_creible(icao24, decoded, ahora, signal_dbfs):
                return
        else:
            # Una trama con CRC verificable es autoridad sobre la direccion:
            # el CRC de DF17/18 se comprueba contra un sindrome conocido, asi
            # que si valida, esos 24 bits son realmente de una aeronave.
            self._confirmadas.add(icao24)
            self._admitir_pendiente(icao24)

        self.poll_count += 1
        if decoded.get("latitude") is not None:
            self.position_hex[icao24] = (ahora, hex_message)
        self.add(decoded_to_observation(icao24, decoded, ahora, signal_dbfs))

    def _direccion_creible(self, icao24: str, decoded: dict, ahora: float,
                           signal_dbfs: float | None = None) -> bool:
        """Decidir si una trama sin CRC verificable se puede creer.

        DF0/4/5/11/16/20/21 llevan la paridad XOR-eada con la direccion del
        avion, asi que no hay manera de validarlas solas: la direccion que sale
        de una trama de ruido son 24 bits al azar, y cada una inventa una
        aeronave que no existe. Medido sobre lo grabado en esta antena: 2863 de
        3123 direcciones aparecieron UNA sola vez y nunca mas, contra 86 con 20
        o mas mensajes, que son el trafico de verdad. Sin esta compuerta el
        conteo de aeronaves salia 36 veces inflado.

        El umbral no es inventado, sale del espacio de direcciones. Si el ruido
        se reparte uniforme sobre 2^24, dos tramas de ruido cayendo en la MISMA
        direccion es raro: con las 3777 tramas no verificables grabadas el azar
        predice 0.43 direcciones repetidas y se observaron 133, o sea 313 veces
        mas. Por eso alcanza con ver una direccion dos veces para creerla, y
        una sola vez no alcanza nunca.

        Devuelve True si la trama se puede registrar ya.
        """
        if icao24 in self._confirmadas:
            return True
        pendiente = self._pendientes.pop(icao24, None)
        if pendiente is None:
            # Primera aparicion: se RETIENE, no se tira. Si la direccion es
            # real la segunda trama llega en segundos y este mensaje entra
            # igual, sin perder el dato.
            self._pendientes[icao24] = (
                ahora, decoded_to_observation(icao24, decoded, ahora, signal_dbfs))
            self._podar_pendientes(ahora)
            return False
        # Segunda aparicion: la direccion es real. Entra tambien la retenida.
        self._confirmadas.add(icao24)
        self.poll_count += 1
        self.add(pendiente[1])
        return True

    def _admitir_pendiente(self, icao24: str) -> None:
        """Soltar la trama retenida de una direccion que acaba de confirmarse."""
        pendiente = self._pendientes.pop(icao24, None)
        if pendiente is not None:
            self.poll_count += 1
            self.add(pendiente[1])

    def _podar_pendientes(self, ahora: float) -> None:
        """Dar por ruido las direcciones que nunca se repitieron.

        Se poda por tamano y no en cada mensaje porque recorrer el dict por
        cada trama seria O(n) sobre un flujo de miles: el ruido llena esto
        rapido y con revisarlo cada tanto alcanza.
        """
        if len(self._pendientes) < 256:
            return
        limite = ahora - self.pendiente_ttl
        vencidas = [i for i, (t, _) in self._pendientes.items() if t < limite]
        for i in vencidas:
            del self._pendientes[i]
            self.phantom_count += 1

    def add(self, observation: Observation) -> None:
        with self._lock:
            self._history.append(observation)
            self._prune()

    def _prune(self) -> None:
        if not self._history:
            return
        cutoff = max(o.timestamp for o in self._history) - self.history_seconds
        self._history = [o for o in self._history if o.timestamp >= cutoff]

    def around(self, moment: float, window_s: float = 60.0) -> list[Observation]:
        with self._lock:
            near = [o for o in self._history if abs(o.timestamp - moment) <= window_s]
        return sorted(near, key=lambda o: abs(o.timestamp - moment))

    def snapshot(self) -> list[Observation]:
        with self._lock:
            return list(self._history)

    @property
    def is_receiving(self) -> bool:
        return self.poll_count > 0 and self.last_error is None

    @property
    def decoder_stats(self) -> dict:
        """Lo que pyModeS descarta por su cuenta, que nadie leia en este repo.

        Es `stats`, PROPERTY y no metodo, y trae entre otras:
        position_rejected (posiciones tiradas por _motion_consistent),
        crc_fail, altitude_mismatch, velocity_mismatch.

        Publicarlo es independiente del filtro propio y mas urgente: pyModeS YA
        estaba descartando posiciones en silencio antes de que existiera nada
        de esto, asi que el numero real de posiciones tiradas en una grabacion
        puede ser MAYOR que el que cuenta PositionGate. Comprobado ejecutando
        _motion_consistent con la traza real de e0b14a mas dos fantasmas
        separados por 5 s: el primero se rechaza y el SEGUNDO se acepta, porque
        _update_position_history (_pipe.py:668-685) mete al historial tambien
        las rechazadas. O sea que el punto que llego a la base es casi seguro
        el segundo de una rafaga.

        Puede hacer quedar peor a la pagina en el corto plazo. Es el precio de
        no mentir.
        """
        return dict(self._decoder.stats) if self._decoder is not None else {}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--surface-ref", type=parse_surface_ref, default=None,
                        metavar="LAT,LON",
                        help="Referencia para posiciones en superficie: 'lat,lon' "
                             "o un codigo ICAO ('SADF'). Por defecto, la variable "
                             "ADSB_SURFACE_REF o San Isidro. Ver receiver.py.")
    args = parser.parse_args()

    # Omitir el argumento cuando es None en vez de pasarlo: pasar surface_ref=None
    # pisaria el default_factory (y con el la variable de entorno) con "sin
    # referencia", que apaga las posiciones en superficie en silencio.
    extra = {"surface_ref": args.surface_ref} if args.surface_ref else {}
    recorder = RtlAdsbRecorder(exe_path=args.exe, device_index=args.device,
                               **extra).start()
    if recorder.last_error and recorder.poll_count == 0 and not recorder._thread:
        print(f"No se pudo iniciar: {recorder.last_error}")
        if "surface_ref" not in (recorder.last_error or ""):
            print("Ejecuta INSTALAR-ADSB.bat primero, o pasa --exe con la ruta correcta.")
        raise SystemExit(1)

    print(f"Escuchando via {args.exe} (Ctrl+C para salir)")
    print(f"Referencia de superficie: {recorder.surface_ref}\n")
    try:
        while True:
            time.sleep(3)
            recent = recorder.around(time.time(), window_s=15)
            estado = f" | ERROR: {recorder.last_error}" if recorder.last_error else ""
            stats = recorder.decoder_stats
            print(f"--- {len(recent)} recientes, {recorder.poll_count} aceptados "
                  f"({recorder.unverified_count} sin CRC verificable), "
                  f"{recorder.corrupt_count} corruptos{estado}")
            print(f"    decoder: position_rejected={stats.get('position_rejected', 0)}, "
                  f"crc_fail={stats.get('crc_fail', 0)}, "
                  f"altitude_mismatch={stats.get('altitude_mismatch', 0)}, "
                  f"velocity_mismatch={stats.get('velocity_mismatch', 0)}")
            for o in recent[:10]:
                alt = "suelo" if o.is_on_ground else (f"{o.altitude_ft:.0f}ft" if o.altitude_ft else "-")
                pos = (f"  {o.latitude:.4f},{o.longitude:.4f}"
                       if o.latitude is not None else "")
                print(f"  {o.icao24}  {o.callsign or '-':9s}  {alt:>7s}{pos}")
    except KeyboardInterrupt:
        print("\ncortado")
    finally:
        recorder.stop()
