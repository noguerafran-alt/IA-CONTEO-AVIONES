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
from adsb_record import CSV_DIR, DB_PATH, Recorder, build_source

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

            self._recorder = Recorder(min_interval_s=kwargs.get("min_interval", 5.0))
            self._latest = {}
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
                    if observation.timestamp > visto_hasta:
                        self._recorder.record(observation)
                    with self._lock:
                        self._latest[observation.icao24] = observation
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
        now = time.time()
        with self._lock:
            live = sorted(self._latest.values(), key=lambda o: -o.timestamp)
            live = [o for o in live if now - o.timestamp <= LIVE_TIMEOUT_S]
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
            "live": [
                {
                    "icao24": o.icao24,
                    "registration": o.registration,
                    "callsign": o.callsign,
                    "altitude_ft": o.altitude_ft,
                    "ground_speed_kt": o.ground_speed_kt,
                    "vertical_rate_fpm": o.vertical_rate_fpm,
                    "on_ground": o.is_on_ground,
                    "seconds_ago": round(now - o.timestamp, 1),
                }
                for o in live
            ],
        }


# One instance per process, shared by every request the dashboard handles --
# see the module docstring for why this must not be per-request state.
service = AdsbService()
