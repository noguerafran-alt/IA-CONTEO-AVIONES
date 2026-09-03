"""Record everything ADS-B hears, to CSV and SQLite, continuously.

This runs before there is any camera. Its job is to answer, with real data
rather than assumption, the questions that decide the rest of the project:

  - Does the antenna actually receive traffic from this location?
  - How many aircraft per hour, and at what hours?
  - Do the aircraft here transmit registrations, or only ICAO24 addresses?

Two outputs, on purpose:

  CSV     one row per observation, appended as they arrive and flushed
          immediately, so the file is complete and openable in Excel even
          while recording is still running.
  SQLite  the same data, in the same database the camera writes to, so both
          sensors can later be queried together.

Files roll over by day (adsb_2026-08-19.csv), which keeps any single file
openable and means a crash or a full disk costs one day, not everything.

Uso:
  python adsb_record.py                    fuente SBS-1 en localhost:30003
  python adsb_record.py --json             fuente dump1090 aircraft.json
  python adsb_record.py --minutes 60       parar solo despues de una hora
"""
from __future__ import annotations

import argparse
import csv
import os
import signal
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from adsb import Observation
from adsb_events import PositionGate, imprimir_rechazos

CSV_DIR = Path(__file__).parent / "output" / "adsb"
# La ruta de la base sale del entorno igual que ADSB_RECEIVER, ADSB_SURFACE_REF
# y ADSB_AIRPORT, y no solo de __file__. Sin ADSB_DB la unica forma de
# ejercitar el sistema montado con filas que entran era escribir en el
# adsb_log.db real -- 11 823 filas que no se pueden volver a capturar -- o
# duplicar el arbol entero. Un sistema que no se puede probar sin tocar el dato
# del usuario es un problema en si mismo. La lee webapp/main.py tambien: es LA
# definicion, no una copia.
DB_PATH = Path(os.environ.get("ADSB_DB") or (Path(__file__).parent / "adsb_log.db"))

COLUMNS = [
    "utc", "epoch", "icao24", "registration", "callsign",
    "altitude_ft", "ground_speed_kt", "vertical_rate_fpm",
    "latitude", "longitude", "on_ground", "on_ground_reported",
]

# on_ground_reported SI va al CSV, y por eso existe _ruta_compatible(): el
# archivo se abre en append, y hasta ahora agregar una columna dejaba las filas
# nuevas con un campo de mas bajo el encabezado viejo. Eso se resolvia
# prohibiendose agregar columnas; ahora se comprueba el encabezado y, si no
# coincide, se rota a un archivo nuevo del mismo dia. La prohibicion pasa a ser
# una comprobacion.
#
# rejected_reason sigue yendo SOLO a la base, pero por otro motivo: no es un
# dato del avion sino el veredicto de un filtro que corre despues, y el CSV es
# el registro de lo que se recibio.
# signal_dbfs va SOLO a la base, igual que rejected_reason: el CSV es el
# formato de intercambio y agregarle una columna rompe a quien lo lea por
# posicion. Es None salvo por el camino de adsb_iq.py, el unico que lo mide.
COLUMNS_DB = COLUMNS + ["rejected_reason", "signal_dbfs", "track_deg"]

# rejected_reason tiene TRES estados y hay que respetarlos al consultar:
#   NULL  fila escrita antes de que existiera el filtro -> NO EVALUADA.
#         Las 8128 filas que ya estaban quedan asi. Tratarlas como limpias es
#         mentir: nadie las miro cuando se escribieron (se las revisa en
#         lectura, que es otro camino).
#   ''    evaluada y limpia.
#   texto el motivo del rechazo ('horizonte', 'velocidad', ...).
SCHEMA = """
CREATE TABLE IF NOT EXISTS adsb_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    utc TEXT NOT NULL,
    epoch REAL NOT NULL,
    icao24 TEXT NOT NULL,
    registration TEXT,
    callsign TEXT,
    altitude_ft REAL,
    ground_speed_kt REAL,
    vertical_rate_fpm REAL,
    latitude REAL,
    longitude REAL,
    on_ground INTEGER,
    on_ground_reported INTEGER,
    rejected_reason TEXT,
    signal_dbfs REAL,
    track_deg REAL
);
CREATE INDEX IF NOT EXISTS idx_adsb_epoch ON adsb_log(epoch);
CREATE INDEX IF NOT EXISTS idx_adsb_icao ON adsb_log(icao24);
"""


def migrar_esquema(conn: sqlite3.Connection, db_path: Path | str = DB_PATH) -> bool:
    """Agrega rejected_reason a una base vieja. Devuelve True si la agrego.

    ALTER TABLE ADD COLUMN y no una tabla nueva: es no destructivo, instantaneo
    (SQLite no reescribe las filas) y deja las que ya estaban en NULL, que es
    justamente el valor que significa "no evaluada".
    """
    columnas = {fila[1] for fila in conn.execute("PRAGMA table_info(adsb_log)")}
    if not columnas:
        return False
    # Una lista y no un solo ALTER: cada columna nueva que aparezca con el
    # tiempo tiene que poder sumarse a una base vieja sin reescribirla, y sin
    # que agregar la segunda rompa la migracion de la primera.
    faltantes = [(nombre, tipo) for nombre, tipo in
                 (("rejected_reason", "TEXT"), ("signal_dbfs", "REAL"),
                  ("track_deg", "REAL"), ("on_ground_reported", "INTEGER"))
                 if nombre not in columnas]
    if not faltantes:
        return False
    try:
        for nombre, tipo in faltantes:
            conn.execute(f"ALTER TABLE adsb_log ADD COLUMN {nombre} {tipo}")
    except sqlite3.OperationalError as exc:
        # Caso REAL, reproducido: con una grabacion en curso el ALTER choca
        # contra la conexion de escritura del otro Recorder y sale "database is
        # locked". No se degrada escribiendo sin la columna -- eso perderia el
        # motivo del rechazo en silencio, que es justo lo que este cambio
        # existe para evitar -- y no se reintenta, porque el candado no es
        # transitorio: el otro proceso lo sostiene hasta que se lo detenga.
        raise RuntimeError(
            f"no se pudo agregar la columna rejected_reason a {db_path}: {exc}. "
            f"Hay otra grabacion escribiendo en la misma base. Detenela "
            f"(hay una sola antena, dos grabaciones se pelean por ella) y "
            f"volve a arrancar esta.") from exc
    conn.commit()
    return True


def as_row(observation: Observation) -> dict:
    return {
        "utc": datetime.fromtimestamp(observation.timestamp, timezone.utc)
                       .isoformat(timespec="seconds"),
        "epoch": round(observation.timestamp, 3),
        "icao24": observation.icao24,
        "registration": observation.registration or "",
        "callsign": observation.callsign or "",
        "altitude_ft": observation.altitude_ft if observation.altitude_ft is not None else "",
        "ground_speed_kt": observation.ground_speed_kt if observation.ground_speed_kt is not None else "",
        "vertical_rate_fpm": observation.vertical_rate_fpm if observation.vertical_rate_fpm is not None else "",
        "latitude": observation.latitude if observation.latitude is not None else "",
        "longitude": observation.longitude if observation.longitude is not None else "",
        "on_ground": 1 if observation.is_on_ground else 0,
        # Tri-estado y no booleano: "" es "el avion no lo dijo". Es la unica
        # columna del CSV donde el vacio no significa "no se recibio el dato"
        # sino "el mensaje no hablaba del tema", y hay que respetarlo al
        # analizar: contar los vacios como 0 volveria a mezclar lo declarado
        # con lo inferido, que es lo que esta columna existe para separar.
        # on_ground (la de al lado) sigue siendo la respuesta combinada:
        # la bandera si la hay, la altitud si no.
        "on_ground_reported": ("" if observation.on_ground_reported is None
                               else (1 if observation.on_ground_reported else 0)),
    }


class Recorder:
    """Writes observations to a per-day CSV and to SQLite, skipping repeats.

    An aircraft transmits several times a second and most of those messages
    say the same thing. Storing every one would bloat the files without adding
    information, so a row is only written when something actually changed or
    enough time has passed -- see `_is_new`.
    """

    def __init__(self, min_interval_s: float = 5.0, csv_dir: Path = CSV_DIR,
                 db_path: Path = DB_PATH):
        self.min_interval_s = min_interval_s
        self.csv_dir = csv_dir
        self.csv_dir.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False: the web dashboard creates this connection
        # on a FastAPI request thread (adsb_service.start()), writes happen
        # from the background recording thread, and it's closed from another
        # request thread (stop()). SQLite refuses cross-thread use by default
        # ("SQLite objects created in a thread can only be used in that same
        # thread"). Safe here because those three never touch it at once --
        # stop() joins the recording thread before close() runs.
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        # WAL, y se CHEQUEA lo que devolvio el PRAGMA. No es ceremonia: SQLite
        # no puede cambiar el modo de journal si otra conexion tiene la base
        # abierta, y en ese caso no falla -- devuelve el modo en el que quedo.
        # Quedarse en 'delete' creyendo estar en WAL es exactamente el descarte
        # silencioso que este repo no se permite, asi que el modo REAL se
        # publica en adsb_service.status() y no se asume.
        #
        # WAL no baja el retraso por si solo: medido, journal=wal con commit
        # cada 50 filas da el mismo retraso visible que journal=delete con
        # commit cada 50 (mediana 0.26 s contra 0.25 s). Lo que hace es abaratar
        # el commit 5.5x (2.46 -> 0.45 ms) y borrar los picos de 117.8 ms en los
        # que el lector se quedaba bloqueado (1.04 ms en WAL). Es el habilitador
        # del commit por tiempo de mas abajo, no un sustituto.
        try:
            self.journal_mode = self.conn.execute(
                "PRAGMA journal_mode=wal").fetchone()[0]
        except sqlite3.Error as exc:
            self.journal_mode = f"error: {exc}"
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.schema_migrated = migrar_esquema(self.conn, db_path)

        # El filtro corre en ingestion pero NO rechaza: escribe el motivo y la
        # fila se guarda igual, con su lat/lon intactos. Borrar antes de
        # escribir destruiria la evidencia para siempre -- es exactamente lo
        # que readsb evita con --position-persistence -- y ademas dejaria sin
        # forma de auditar el filtro. Quien decide que se MUESTRA es el mismo
        # gate corriendo en lectura (adsb_events.load_db).
        self.gate = PositionGate()

        self._last: dict[str, tuple[float, float | None, float | None, float | None]] = {}
        self._csv_day: str | None = None
        self._csv_file = None
        self._csv_writer: csv.DictWriter | None = None

        self.written = 0
        self.skipped = 0
        # Commit por TIEMPO y no cada N filas. Aca vivia TODO el retraso del
        # mapa: el INSERT puro mide 0.003 ms y el commit 2.46 ms, y hasta que
        # no ocurre el commit ningun lector ve nada. Medido en vivo: 44 filas
        # escritas e invisibles, la mas vieja con 878 s (14.6 min) de espera.
        #
        # Y re-medido sobre las 11 823 filas de adsb_log.db, reconstruyendo los
        # lotes de 50 por id (236 lotes completos): cada fila esperaba mediana
        # 38.7 s, p90 186.0 s, p99 410.5 s y MAXIMO 759.5 s (12.7 min). Esos
        # numeros excluyen los 18 lotes que cruzan un corte de grabacion (hueco
        # > 300 s), y hay que excluirlos: contandolos el maximo da 14 902.8 s
        # (4 h 8 min), pero ese numero NO es una fila esperando -- sale entero
        # del hueco de 14 742.9 s entre id=3523 (2026-08-22T13:59:12Z) e
        # id=3524 (18:04:55Z), cuatro horas con CERO filas, o sea el grabador
        # apagado. Si el grabador se detiene, close() commitea; si se cae sin
        # cerrar, esas filas no llegan a la base. Ninguna fila espero 4 h.
        # (Contando todos los lotes: mediana 40.6 s, p90 223.0 s, p99 2 114.9 s.)
        #
        # Por tiempo y no por cantidad porque un umbral por cantidad NO TIENE
        # COTA TEMPORAL: 759.5 s de retraso medidos con el grabador funcionando
        # normal ya lo prueban. Y empeora cuando baja el trafico: la hora mas
        # floja con el grabador claramente encendido (2026-08-23T02 UTC = 23:00
        # local, 340 filas, hueco interno maximo 258 s) da 5.67 filas/min, con
        # lo que un lote de 50 tarda 529 s -- o sea que el mapa se atrasaria mas
        # justo cuando el cielo esta vacio y cada avion importa mas. A las 22:00
        # local (01:00 UTC) son 527 filas = 8.78 filas/min y el lote tarda 342 s.
        # El temporizador acota el retraso en 1 s pase lo que pase.
        #
        # Lo que cuesta: al pico real medido (3.8 filas/s) es a lo sumo 1
        # commit/s = 2.46 ms/s = 0.25 % de un nucleo, y 0.45 ms/s en WAL. Un dia
        # entero al volumen de hoy son 26.7 s de commit contra 0.4 s.
        self.commit_interval_s = 1.0
        self._last_commit = time.monotonic()
        self.pending = 0
        self.aircraft: set[str] = set()
        self.with_registration: set[str] = set()

    def _ruta_compatible(self, base: Path) -> Path:
        """La primera ruta del dia cuyo encabezado coincida con COLUMNS.

        El CSV se abre en APPEND. Un archivo del dia escrito con un juego de
        columnas viejo recibiria filas con un campo de mas bajo el encabezado
        equivocado, y quien lo lea por posicion cobra el desfasaje sin que
        salte un solo error -- silencioso, que es la peor clase.

        Rotar a adsb_2026-08-25.1.csv es feo y es lo correcto: el archivo viejo
        queda intacto y legible con SU encabezado, el nuevo arranca con el
        actual, y ninguno de los dos miente. Un archivo vacio se reutiliza (se
        le escribe el encabezado nuevo) porque no hay nada que preservar.
        """
        candidata = base
        sufijo = 0
        while candidata.exists() and candidata.stat().st_size > 0:
            try:
                with candidata.open(encoding="utf-8", newline="") as fh:
                    encabezado = next(csv.reader(fh), None)
            except OSError:
                encabezado = None
            if encabezado == COLUMNS:
                return candidata
            sufijo += 1
            candidata = base.with_name(f"{base.stem}.{sufijo}{base.suffix}")
        return candidata

    def _csv_for(self, timestamp: float) -> csv.DictWriter:
        day = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
        if day != self._csv_day:
            if self._csv_file:
                self._csv_file.close()
            path = self._ruta_compatible(self.csv_dir / f"adsb_{day}.csv")
            nuevo = not path.exists() or path.stat().st_size == 0
            self._csv_file = path.open("a", newline="", encoding="utf-8")
            self._csv_writer = csv.DictWriter(self._csv_file, fieldnames=COLUMNS)
            if nuevo:
                self._csv_writer.writeheader()
            self._csv_day = day
            print(f"  -> escribiendo en {path.name}")
        return self._csv_writer

    def _is_new(self, observation: Observation) -> bool:
        """True when this observation adds something over the last stored one."""
        previous = self._last.get(observation.icao24)
        if previous is None:
            return True
        last_time, last_alt, last_speed, last_track = previous
        if observation.timestamp - last_time >= self.min_interval_s:
            return True
        # Altitude or speed changing materially means the aircraft is doing
        # something (climbing, descending, accelerating) -- exactly the moments
        # this data exists to capture, so never rate-limit those away.
        if last_alt is not None and observation.altitude_ft is not None:
            if abs(observation.altitude_ft - last_alt) >= 100:
                return True
        if last_speed is not None and observation.ground_speed_kt is not None:
            if abs(observation.ground_speed_kt - last_speed) >= 10:
                return True
        # El rumbo entra por el mismo motivo que la altitud y la velocidad -- una
        # curva es tambien "el avion haciendo algo"-- y por uno mas fuerte: el
        # rumbo viaja SOLO en los mensajes de velocidad, que son una minoria del
        # flujo. Con la ventana de 5 s casi siempre caian dentro del limite de
        # uno de posicion y se descartaban, asi que el rumbo transmitido no se
        # grababa NUNCA: medido, 0 filas con track_deg sobre 75 s de aire real.
        # La primera aparicion siempre se guarda; despues, cada 5 grados.
        if observation.track_deg is not None:
            if last_track is None:
                return True
            # El rumbo es circular: entre 359 y 1 grado hay 2 grados, no 358.
            delta = abs(observation.track_deg - last_track) % 360.0
            if min(delta, 360.0 - delta) >= 5.0:
                return True
        return False

    def record(self, observation: Observation) -> None:
        if not self._is_new(observation):
            self.skipped += 1
            return

        previo = self._last.get(observation.icao24)
        self._last[observation.icao24] = (
            observation.timestamp,
            observation.altitude_ft,
            observation.ground_speed_kt,
            # El ultimo rumbo CONOCIDO, no el de esta observacion: la mayoria de
            # las tramas no traen rumbo, y guardar None pisaria la referencia y
            # haria que la siguiente con rumbo pareciera "la primera" de nuevo,
            # escribiendo una fila por cada mensaje de velocidad.
            observation.track_deg if observation.track_deg is not None
            else (previo[3] if previo and len(previo) > 3 else None),
        )
        self.aircraft.add(observation.icao24)
        if observation.registration:
            self.with_registration.add(observation.icao24)

        row = as_row(observation)
        writer = self._csv_for(observation.timestamp)
        writer.writerow(row)
        # Flushed per row so the CSV is always complete and openable, even if
        # the recorder is killed or the machine loses power mid-run.
        self._csv_file.flush()

        # '' (evaluada y limpia) y no NULL: NULL ya significa "no evaluada" en
        # las 8128 filas anteriores al filtro, y hacer que los dos casos se
        # escriban igual borraria la unica forma de distinguirlos.
        motivo = self.gate.feed(observation) or ""
        valores = ([row[c] if row[c] != "" else None for c in COLUMNS]
                   + [motivo, observation.signal_dbfs, observation.track_deg])
        self.conn.execute(
            f"INSERT INTO adsb_log ({','.join(COLUMNS_DB)}) "
            f"VALUES ({','.join('?' * len(COLUMNS_DB))})",
            valores,
        )
        self.written += 1
        self.pending += 1
        if time.monotonic() - self._last_commit >= self.commit_interval_s:
            self.conn.commit()
            self._last_commit = time.monotonic()
            self.pending = 0

    def seconds_since_commit(self) -> float:
        """Cuanto hace que nadie confirma. Lo publica status() para que el
        atraso sea un numero en pantalla y no una advertencia en prosa."""
        return time.monotonic() - self._last_commit

    def close(self) -> None:
        self.conn.commit()
        self.pending = 0
        self._last_commit = time.monotonic()
        # TRUNCATE y no PASSIVE: en WAL el .db se queda ATRAS del -wal hasta el
        # checkpoint (el automatico recien salta a las 1 000 paginas), y
        # adsb_log.db esta TRACKEADA en git -- `git ls-files --error-unmatch
        # adsb_log.db` la devuelve. Cerrar dejando cola en el -wal significa que
        # un `git add` sube un archivo al que le falta el final.
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass                      # en journal=delete el PRAGMA no aplica
        self.conn.close()
        if self._csv_file:
            self._csv_file.close()


def build_source(mode: str = "auto", *, exe: str | None = None, device: int = 0,
                 json_url: str | None = None, host: str = "127.0.0.1",
                 port: int | None = None, surface_ref: str | None = None,
                 log=print):
    """Start and return an ADS-B source, resolving 'auto' the same way the
    CLI and the web dashboard both do -- one place, so they cannot drift
    apart into picking different sources for the same situation.

    Returns (source, description). Raises RuntimeError with a message meant
    to be shown as-is (in a terminal or in the dashboard) if nothing usable
    could be started.
    """
    if json_url and mode == "auto":
        mode = "json"

    if mode == "auto":
        from adsb_rtlsdr import DEFAULT_EXE
        exe_candidato = Path(exe) if exe else DEFAULT_EXE
        mode = "rtlsdr" if exe_candidato.exists() else "sbs"
        log(f"Fuente automatica: {mode}"
            + ("" if mode == "rtlsdr" else " (no se encontro tools/rtlsdr/rtl_adsb.exe;"
                                           " correr INSTALAR-ADSB.bat para usar el dongle directo)"))

    if mode == "json":
        from adsb import AdsbRecorder
        url = json_url or "http://127.0.0.1:8080/data/aircraft.json"
        source = AdsbRecorder(url=url).start()
        return source, f"dump1090 JSON en {url}"

    if mode == "iq":
        # Mismo dongle que "rtlsdr", distinto programa y distinto dato: en vez
        # de pedirle el hex ya masticado a rtl_adsb.exe, se le piden las
        # muestras crudas a rtl_sdr.exe y se demodula en Python. Cuesta mas CPU
        # (medido: ~10x tiempo real, con margen sobrado) y a cambio cada mensaje
        # trae su nivel de senal en dBFS, que el otro camino tira y no hay forma
        # de recuperar despues.
        from adsb_iq import DEFAULT_EXE as IQ_EXE, IqRecorder
        from receiver import parse_surface_ref
        exe_path = Path(exe) if exe else IQ_EXE
        extra = ({"surface_ref": parse_surface_ref(surface_ref)}
                 if surface_ref else {})
        source = IqRecorder(exe_path=exe_path, device_index=device, **extra).start()
        if source.last_error and not source._thread:
            ayuda = ("" if "surface_ref" in source.last_error else
                     "\n  Corre INSTALAR-ADSB.bat para bajar rtl_sdr.exe, "
                     "o indica la ruta correcta.")
            raise RuntimeError(f"{source.last_error}{ayuda}")
        return source, f"IQ crudo con nivel de senal via {exe_path}"

    if mode == "rtlsdr":
        from adsb_rtlsdr import DEFAULT_EXE, RtlAdsbRecorder
        from receiver import parse_surface_ref
        exe_path = exe or DEFAULT_EXE
        # Se omite el argumento cuando no vino en vez de pasar None: pasar
        # surface_ref=None pisaria el default_factory del dataclass (y con el la
        # variable ADSB_SURFACE_REF) con "sin referencia", que apaga en silencio
        # las posiciones de los aviones en pista.
        extra = ({"surface_ref": parse_surface_ref(surface_ref)}
                 if surface_ref else {})
        source = RtlAdsbRecorder(exe_path=exe_path, device_index=device,
                                 **extra).start()
        if source.last_error and not source._thread:
            # start() ahora tambien rechaza una referencia de superficie mal
            # escrita, y para ese caso el consejo de bajar rtl_adsb.exe manda
            # a la persona a arreglar lo que no esta roto.
            ayuda = ("" if "surface_ref" in source.last_error else
                     "\n  Corre INSTALAR-ADSB.bat para bajar rtl_adsb.exe, "
                     "o indica la ruta correcta.")
            raise RuntimeError(f"{source.last_error}{ayuda}")
        return source, f"dongle RTL-SDR directo via {exe_path}"

    # sbs
    from adsb_sbs import CANDIDATE_PORTS, SbsRecorder, find_feed

    puerto = port
    if puerto is None:
        log(f"Buscando el feed en {host} (puertos {CANDIDATE_PORTS})...")
        puerto = find_feed(host)
        if puerto is None:
            raise RuntimeError(
                "No se encontro ningun feed SBS-1 con datos.\n"
                "  1. Verifica que el software de ADS-B este corriendo.\n"
                "  2. Revisa en su configuracion que puerto publica BaseStation/SBS,\n"
                "     y pasalo con --port NUMERO.\n"
                "  3. Si el dongle es un RTL-SDR Blog V4, asegurate de que el\n"
                "     software tenga drivers actualizados: con los viejos el V4\n"
                "     no recibe nada. RTL1090v2 ya los trae."
            )
        log(f"  encontrado en el puerto {puerto}")

    source = SbsRecorder(host=host, port=puerto).start()
    return source, f"feed SBS-1 en {host}:{puerto}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["auto", "rtlsdr", "sbs", "json"], default="auto",
                        help="auto (por defecto): usa el dongle directamente si "
                             "tools/rtlsdr/rtl_adsb.exe existe (ver INSTALAR-ADSB.bat), "
                             "si no busca un feed SBS-1. rtlsdr/sbs/json fuerzan una fuente.")
    parser.add_argument("--exe", default=None, help="Ruta a rtl_adsb.exe (fuente rtlsdr)")
    parser.add_argument("--device", type=int, default=0, help="Indice del dongle (fuente rtlsdr)")
    parser.add_argument("--surface-ref", default=None, metavar="LAT,LON",
                        help="Donde esta la antena, para decodificar la posicion de "
                             "los aviones EN PISTA: 'lat,lon' o un codigo ICAO "
                             "('SADF'). Por defecto usa la variable de entorno "
                             "ADSB_SURFACE_REF, y si no esta, San Isidro. Ver receiver.py.")
    parser.add_argument("--json", nargs="?", const="http://127.0.0.1:8080/data/aircraft.json",
                        default=None, metavar="URL",
                        help="Atajo para --source json con esta URL")
    parser.add_argument("--host", default="127.0.0.1", help="Host del feed SBS-1")
    parser.add_argument("--port", type=int, default=None,
                        help="Puerto del feed SBS-1. Si se omite, se autodetecta "
                             "(30003 de dump1090, 31004 de RTL1090, y otros).")
    parser.add_argument("--minutes", type=float, default=None,
                        help="Parar despues de N minutos (por defecto, hasta Ctrl+C)")
    parser.add_argument("--min-interval", type=float, default=5.0,
                        help="Segundos minimos entre registros del mismo avion")
    args = parser.parse_args()

    try:
        source, descripcion = build_source(
            args.source, exe=args.exe, device=args.device, json_url=args.json,
            host=args.host, port=args.port, surface_ref=args.surface_ref,
        )
    except RuntimeError as exc:
        print(f"\n{exc}")
        raise SystemExit(1)
    print(f"Fuente: {descripcion}")

    try:
        recorder = Recorder(min_interval_s=args.min_interval)
    except (RuntimeError, ValueError) as exc:
        # Base bloqueada por otra grabacion, o ADSB_SURFACE_REF mal escrito.
        # Las dos son de configuracion y las dos tienen que salir como un
        # mensaje leible, no como un traceback.
        source.stop()
        print(f"\n{exc}")
        raise SystemExit(1)
    print(f"Guardando en {CSV_DIR} y en {DB_PATH.name}")
    print("Ctrl+C para terminar.\n")

    corriendo = True

    def parar(*_):
        nonlocal corriendo
        corriendo = False

    signal.signal(signal.SIGINT, parar)

    inicio = time.time()
    visto_hasta = 0.0
    ultimo_reporte = 0.0

    try:
        while corriendo:
            time.sleep(1.0)

            for observation in source.snapshot():
                if observation.timestamp > visto_hasta:
                    recorder.record(observation)
            snapshot = source.snapshot()
            if snapshot:
                visto_hasta = max(o.timestamp for o in snapshot)

            ahora = time.time()
            if ahora - ultimo_reporte >= 10:
                ultimo_reporte = ahora
                minutos = (ahora - inicio) / 60
                # "sin dongle conectado" no es un error: es el estado normal
                # antes de enchufar la antena, y mostrarlo como ERROR asusta
                # sin necesidad. Se distingue de una falla real del receptor.
                if getattr(source, "waiting_for_device", False):
                    estado = " | esperando el dongle (sin dispositivo conectado)"
                elif source.last_error:
                    estado = f" | ERROR: {source.last_error}"
                else:
                    estado = ""
                print(f"[{minutos:5.1f} min] {len(recorder.aircraft):3d} aeronaves | "
                      f"{recorder.written:5d} registros | "
                      f"{len(recorder.with_registration)} con matricula{estado}")

            if args.minutes and (ahora - inicio) >= args.minutes * 60:
                break
    finally:
        source.stop()
        recorder.close()

        duracion = (time.time() - inicio) / 60
        print(f"\n{'='*52}")
        print(f"Duracion            : {duracion:.1f} minutos")
        print(f"Aeronaves distintas : {len(recorder.aircraft)}")
        print(f"Con matricula       : {len(recorder.with_registration)}")
        print(f"Registros escritos  : {recorder.written}")
        print(f"Repetidos omitidos  : {recorder.skipped}")
        # Se imprime siempre, con cero incluido: "0 descartadas" solo significa
        # algo al lado de cuantas se evaluaron.
        print(imprimir_rechazos(recorder.gate))
        # Lo que descarta pyModeS y hasta ahora no leia nadie. Puede ser mayor
        # que el numero de arriba: _motion_consistent (_pipe.py) ya venia
        # tirando posiciones en silencio antes de que existiera este filtro.
        stats = getattr(source, "decoder_stats", None)
        if stats:
            print(f"  decoder: position_rejected={stats.get('position_rejected', 0)}, "
                  f"crc_fail={stats.get('crc_fail', 0)}, "
                  f"altitude_mismatch={stats.get('altitude_mismatch', 0)}, "
                  f"velocity_mismatch={stats.get('velocity_mismatch', 0)}")
            print(f"  radio  : {getattr(source, 'corrupt_count', 0)} corruptos, "
                  f"{getattr(source, 'unverified_count', 0)} sin CRC verificable")
        print(f"CSV                 : {CSV_DIR}")
        print(f"Base                : {DB_PATH}")
        if not recorder.aircraft:
            if getattr(source, "waiting_for_device", False):
                print("\nNo se detecto el dongle todavia -- es normal si aun no lo")
                print("conectaste. Conectalo, revisa que Zadig haya instalado el")
                print("driver WinUSB (INSTALAR-ADSB.bat), y volve a correr esto.")
            else:
                print("\nNo se recibio nada. Verifica que el software de ADS-B este")
                print("corriendo y sirviendo el feed, y que la antena tenga vista al cielo.")


if __name__ == "__main__":
    main()
