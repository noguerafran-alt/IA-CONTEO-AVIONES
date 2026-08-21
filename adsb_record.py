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
import signal
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from adsb import Observation

CSV_DIR = Path(__file__).parent / "output" / "adsb"
DB_PATH = Path(__file__).parent / "adsb_log.db"

COLUMNS = [
    "utc", "epoch", "icao24", "registration", "callsign",
    "altitude_ft", "ground_speed_kt", "vertical_rate_fpm",
    "latitude", "longitude", "on_ground",
]

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
    on_ground INTEGER
);
CREATE INDEX IF NOT EXISTS idx_adsb_epoch ON adsb_log(epoch);
CREATE INDEX IF NOT EXISTS idx_adsb_icao ON adsb_log(icao24);
"""


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
        self.conn.executescript(SCHEMA)
        self.conn.commit()

        self._last: dict[str, tuple[float, float | None, float | None]] = {}
        self._csv_day: str | None = None
        self._csv_file = None
        self._csv_writer: csv.DictWriter | None = None

        self.written = 0
        self.skipped = 0
        self.aircraft: set[str] = set()
        self.with_registration: set[str] = set()

    def _csv_for(self, timestamp: float) -> csv.DictWriter:
        day = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
        if day != self._csv_day:
            if self._csv_file:
                self._csv_file.close()
            path = self.csv_dir / f"adsb_{day}.csv"
            nuevo = not path.exists()
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
        last_time, last_alt, last_speed = previous
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
        return False

    def record(self, observation: Observation) -> None:
        if not self._is_new(observation):
            self.skipped += 1
            return

        self._last[observation.icao24] = (observation.timestamp,
                                          observation.altitude_ft,
                                          observation.ground_speed_kt)
        self.aircraft.add(observation.icao24)
        if observation.registration:
            self.with_registration.add(observation.icao24)

        row = as_row(observation)
        writer = self._csv_for(observation.timestamp)
        writer.writerow(row)
        # Flushed per row so the CSV is always complete and openable, even if
        # the recorder is killed or the machine loses power mid-run.
        self._csv_file.flush()

        self.conn.execute(
            f"INSERT INTO adsb_log ({','.join(COLUMNS)}) "
            f"VALUES ({','.join('?' * len(COLUMNS))})",
            [row[c] if row[c] != "" else None for c in COLUMNS],
        )
        self.written += 1
        if self.written % 50 == 0:
            self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
        if self._csv_file:
            self._csv_file.close()


def build_source(mode: str = "auto", *, exe: str | None = None, device: int = 0,
                 json_url: str | None = None, host: str = "127.0.0.1",
                 port: int | None = None, log=print):
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

    if mode == "rtlsdr":
        from adsb_rtlsdr import DEFAULT_EXE, RtlAdsbRecorder
        exe_path = exe or DEFAULT_EXE
        source = RtlAdsbRecorder(exe_path=exe_path, device_index=device).start()
        if source.last_error and not source._thread:
            raise RuntimeError(
                f"{source.last_error}\n"
                "  Corre INSTALAR-ADSB.bat para bajar rtl_adsb.exe, o indica la ruta correcta."
            )
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
            host=args.host, port=args.port,
        )
    except RuntimeError as exc:
        print(f"\n{exc}")
        raise SystemExit(1)
    print(f"Fuente: {descripcion}")

    recorder = Recorder(min_interval_s=args.min_interval)
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
