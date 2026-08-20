"""Read aircraft identities from ADS-B and keep a short history of what flew by.

Why this exists: a camera cannot always read a tail number. The lettering sits
on the side of the fuselage, so an aircraft presenting its nose or tail simply
does not show it, and night, rain or another aircraft in the way remove it too.
Those are physical limits, not software ones.

Aircraft broadcast their own identity continuously on 1090 MHz. A ~USD 20
RTL-SDR running dump1090 receives it, which turns identification from "read the
paint" into "listen to what the aircraft says". The camera still decides THAT an
operation happened and when; ADS-B says WHICH aircraft it was.

dump1090 exposes a JSON endpoint (usually http://127.0.0.1:8080/data/aircraft.json).
This module polls it and keeps a rolling window of observations, so a crossing
detected at 14:32:07 can be matched against what was flying at 14:32:07.

Uso:
  python adsb.py --once      una lectura y mostrar
  python adsb.py --watch     seguir en vivo
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_URL = "http://127.0.0.1:8080/data/aircraft.json"
# Cuanto tiempo se conservan las observaciones. Un avion tarda minutos entre que
# aparece en el radar y toca pista, asi que la ventana tiene que cubrir eso.
DEFAULT_HISTORY_S = 900.0


@dataclass
class Observation:
    """Un avion visto por ADS-B en un instante."""
    timestamp: float
    icao24: str
    registration: str | None = None
    callsign: str | None = None
    altitude_ft: float | None = None
    ground_speed_kt: float | None = None
    vertical_rate_fpm: float | None = None
    latitude: float | None = None
    longitude: float | None = None

    @property
    def is_on_ground(self) -> bool:
        return self.altitude_ft is not None and self.altitude_ft <= 0

    @property
    def is_descending(self) -> bool:
        return self.vertical_rate_fpm is not None and self.vertical_rate_fpm < -200

    @property
    def is_climbing(self) -> bool:
        return self.vertical_rate_fpm is not None and self.vertical_rate_fpm > 200


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_aircraft_json(payload: dict, now: float | None = None) -> list[Observation]:
    """Convert one dump1090 aircraft.json snapshot into observations.

    Field names differ between dump1090 forks (readsb and tar1090 renamed
    several), so each value is looked up under every spelling seen in the wild.
    """
    now = now if now is not None else time.time()
    stamp = _as_float(payload.get("now")) or now

    observations = []
    for entry in payload.get("aircraft", []):
        icao24 = (entry.get("hex") or entry.get("icao") or "").strip().lower()
        if not icao24:
            continue

        # "seen" is how many seconds ago this aircraft was last heard.
        age = _as_float(entry.get("seen")) or 0.0

        altitude = entry.get("alt_baro", entry.get("altitude"))
        # Both forks report a grounded aircraft as the string "ground".
        altitude_ft = 0.0 if altitude == "ground" else _as_float(altitude)

        observations.append(Observation(
            timestamp=stamp - age,
            icao24=icao24,
            registration=(entry.get("r") or entry.get("registration") or "").strip().upper() or None,
            callsign=(entry.get("flight") or "").strip() or None,
            altitude_ft=altitude_ft,
            ground_speed_kt=_as_float(entry.get("gs", entry.get("speed"))),
            vertical_rate_fpm=_as_float(entry.get("baro_rate", entry.get("vert_rate"))),
            latitude=_as_float(entry.get("lat")),
            longitude=_as_float(entry.get("lon")),
        ))
    return observations


def fetch(url: str = DEFAULT_URL, timeout: float = 5.0) -> list[Observation]:
    """One snapshot from dump1090. Raises on connection problems."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return parse_aircraft_json(json.loads(response.read().decode("utf-8")))


@dataclass
class AdsbRecorder:
    """Polls dump1090 in the background and keeps a rolling history.

    Runs in its own thread on purpose: the detection loop has to keep up with
    the camera, and a network read that blocks for a second would drop frames.
    """
    url: str = DEFAULT_URL
    poll_interval: float = 1.0
    history_seconds: float = DEFAULT_HISTORY_S

    _history: list[Observation] = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    last_error: str | None = field(default=None, repr=False)
    poll_count: int = 0

    def start(self) -> "AdsbRecorder":
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
                self.add(fetch(self.url))
                self.last_error = None
                self.poll_count += 1
            except Exception as exc:
                # Never let a receiver problem stop the recorder: the camera
                # keeps counting regardless, and ADS-B may come back.
                self.last_error = f"{type(exc).__name__}: {exc}"
            self._stop.wait(self.poll_interval)

    def add(self, observations: list[Observation]) -> None:
        with self._lock:
            self._history.extend(observations)
            self._prune()

    def _prune(self) -> None:
        if not self._history:
            return
        cutoff = max(o.timestamp for o in self._history) - self.history_seconds
        self._history = [o for o in self._history if o.timestamp >= cutoff]

    def around(self, moment: float, window_s: float = 60.0) -> list[Observation]:
        """Observations within +/- window_s of a moment, closest in time first."""
        with self._lock:
            near = [o for o in self._history if abs(o.timestamp - moment) <= window_s]
        return sorted(near, key=lambda o: abs(o.timestamp - moment))

    def snapshot(self) -> list[Observation]:
        with self._lock:
            return list(self._history)

    @property
    def is_receiving(self) -> bool:
        return self.poll_count > 0 and self.last_error is None


def _print_table(observations: list[Observation]) -> None:
    if not observations:
        print("  (ningun avion recibido)")
        return
    print(f"  {'ICAO24':8s} {'MATRICULA':10s} {'VUELO':9s} {'ALT':>7s} {'VEL':>6s} {'V/S':>7s}")
    for o in sorted(observations, key=lambda x: x.altitude_ft or 0):
        alt = "suelo" if o.is_on_ground else (f"{o.altitude_ft:.0f}ft" if o.altitude_ft else "-")
        speed = f"{o.ground_speed_kt:.0f}kt" if o.ground_speed_kt else "-"
        rate = f"{o.vertical_rate_fpm:+.0f}" if o.vertical_rate_fpm else "-"
        print(f"  {o.icao24:8s} {o.registration or '-':10s} {o.callsign or '-':9s} "
              f"{alt:>7s} {speed:>6s} {rate:>7s}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--watch", action="store_true", help="Seguir en vivo")
    args = parser.parse_args()

    try:
        if args.watch:
            recorder = AdsbRecorder(url=args.url).start()
            print(f"Escuchando {args.url} (Ctrl+C para salir)\n")
            while True:
                time.sleep(3)
                recent = recorder.around(time.time(), window_s=15)
                estado = f" | ERROR: {recorder.last_error}" if recorder.last_error else ""
                print(f"--- {len(recent)} observaciones recientes{estado}")
                _print_table(recent[:10])
        else:
            _print_table(fetch(args.url))
    except KeyboardInterrupt:
        print("\ncortado")
    except urllib.error.URLError as exc:
        print(f"No se pudo conectar a {args.url}")
        print(f"  {exc}")
        print("\n  Verifica que dump1090 este corriendo. Probalo con:")
        print("    dump1090-fa --net      (o dump1090 --net)")
