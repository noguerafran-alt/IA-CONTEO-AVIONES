"""Read ADS-B data over the SBS-1 (BaseStation) TCP feed.

Why this instead of dump1090's aircraft.json (see adsb.py): dump1090 itself
has no reliable, actively maintained Windows build -- the ones found are old,
need hand-copying missing DLLs, and don't clearly expose a JSON endpoint.

SBS-1 sidesteps that. It is a decades-old, plain-text, comma-separated feed
that almost every Windows ADS-B tool serves on TCP port 30003 by default:
RTL1090, PlanePlotter, Virtual Radar Server, dump1090 itself, and others. As
long as whichever one you install serves SBS on 30003 (or you point --sbs-port
elsewhere), this connects and works, without caring which program produced it.

Format of one line (comma-separated, no quoting):
  MSG,<transmission_type>,<session>,<aircraft>,<icao24>,<flight_db_id>,
  <date_gen>,<time_gen>,<date_log>,<time_log>,<callsign>,<altitude_ft>,
  <ground_speed_kt>,<track_deg>,<lat>,<lon>,<vertical_rate_fpm>,<squawk>,
  <alert>,<emergency>,<spi>,<is_on_ground>

Only "MSG" lines carry traffic; other line types (SEL, ID, AIR, STA, CLK) are
session/status chatter and are ignored. A single MSG line rarely carries every
field -- transmission type 1 has only the callsign, type 3 has altitude and
position, type 4 has speed and vertical rate. That is fine: each partial
Observation still lands in the same rolling history as everything else for
that aircraft, and match_adsb.py already treats missing fields as "unknown"
rather than requiring a complete record.

Uso:
  python adsb_sbs.py --watch                 conectar y mostrar en vivo
  python adsb_sbs.py --host 127.0.0.1 --port 30003
"""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field

from adsb import DEFAULT_HISTORY_S, Observation

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 30003

# Every Windows decoder picks its own port for the BaseStation feed, and which
# one is running is not knowable in advance -- so try the known ones rather
# than making the operator find out by trial and error:
#   30003  dump1090 and most Linux/Pi tools, the de-facto standard
#   31004  RTL1090 (its BaseStation port is 31004 + DeviceID*10, not 30003;
#          RTL1090v2 matters here because it ships with RTL-SDR Blog V4
#          support built in, so no DLL swapping)
#   31014  RTL1090 second device
#   30103  Virtual Radar Server rebroadcast default
CANDIDATE_PORTS = [30003, 31004, 31014, 30103]


def find_feed(host: str = DEFAULT_HOST, ports: list[int] | None = None,
              timeout: float = 1.5) -> int | None:
    """First port that accepts a connection AND sends parseable SBS traffic.

    Accepting the connection is not enough: some tools listen on a port while
    serving a different format, which would look connected but record nothing.
    """
    for port in (ports or CANDIDATE_PORTS):
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                buffer = b""
                deadline = time.time() + timeout
                while time.time() < deadline:
                    try:
                        chunk = sock.recv(2048)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    buffer += chunk
                    for raw in buffer.split(b"\n")[:-1]:
                        if parse_sbs_line(raw.decode("ascii", "ignore")):
                            return port
        except OSError:
            continue
    return None


def _to_float(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_sbs_line(line: str, now: float | None = None) -> Observation | None:
    """One SBS-1 line to an Observation, or None if it carries no traffic data."""
    fields = line.strip().split(",")
    if len(fields) < 22 or fields[0] != "MSG":
        return None

    icao24 = fields[4].strip().lower()
    if not icao24:
        return None

    on_ground = fields[21].strip() == "-1"
    altitude_ft = _to_float(fields[11])
    if on_ground and altitude_ft is None:
        altitude_ft = 0.0

    return Observation(
        timestamp=now if now is not None else time.time(),
        icao24=icao24,
        callsign=(fields[10].strip() or None),
        altitude_ft=altitude_ft,
        ground_speed_kt=_to_float(fields[12]),
        vertical_rate_fpm=_to_float(fields[16]),
        latitude=_to_float(fields[14]),
        longitude=_to_float(fields[15]),
        # SBS never carries the tail number, only the ICAO24 hex address.
        # A lookup against an aircraft database could fill this in later;
        # match_adsb.py works fine with icao24 alone in the meantime.
        registration=None,
    )


@dataclass
class SbsRecorder:
    """Connects to an SBS-1 feed in a background thread and keeps rolling history.

    Deliberately mirrors AdsbRecorder's public surface (start/stop/around/
    snapshot/is_receiving/last_error/poll_count) so match_adsb.py and anything
    built on top of it do not need to know or care which physical source of
    ADS-B data is in use.
    """
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    history_seconds: float = DEFAULT_HISTORY_S
    reconnect_delay: float = 3.0

    _history: list[Observation] = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    last_error: str | None = field(default=None, repr=False)
    poll_count: int = 0    # here: lines successfully parsed, not HTTP polls

    def start(self) -> "SbsRecorder":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._consume_one_connection()
            except Exception as exc:
                # A dropped connection is normal (the SBS server restarted, a
                # cable came loose); log it and reconnect rather than dying,
                # since the camera-side crossing detection keeps running
                # regardless and should not be starved by a flaky receiver.
                self.last_error = f"{type(exc).__name__}: {exc}"
            self._stop.wait(self.reconnect_delay)

    def _consume_one_connection(self) -> None:
        with socket.create_connection((self.host, self.port), timeout=5) as sock:
            sock.settimeout(1.0)
            self.last_error = None
            buffer = b""
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionError("el servidor SBS cerro la conexion")
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    observation = parse_sbs_line(raw.decode("ascii", "ignore"))
                    if observation:
                        self.add(observation)
                        self.poll_count += 1

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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    recorder = SbsRecorder(host=args.host, port=args.port).start()
    print(f"Conectando a {args.host}:{args.port} (formato SBS-1). Ctrl+C para salir.\n")
    try:
        while True:
            time.sleep(3)
            recent = recorder.around(time.time(), window_s=15)
            estado = f" | ERROR: {recorder.last_error}" if recorder.last_error else ""
            print(f"--- {len(recent)} observaciones recientes, "
                  f"{recorder.poll_count} lineas totales{estado}")
            for o in recent[:10]:
                alt = "suelo" if o.is_on_ground else (f"{o.altitude_ft:.0f}ft" if o.altitude_ft else "-")
                print(f"  {o.icao24}  {o.callsign or '-':9s}  {alt:>7s}")
    except KeyboardInterrupt:
        print("\ncortado")
    finally:
        recorder.stop()
