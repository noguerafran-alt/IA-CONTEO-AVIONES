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

from dataclasses import dataclass, field

from adsb import Observation

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


def coverage_report(observations: list[Observation]) -> dict:
    """Whether this data could contain runway operations at all.

    Zero events is meaningless without this: an antenna that never hears an
    aircraft below cruise altitude cannot show a landing, and the honest
    answer is "no coverage", not "no landings".
    """
    altitudes = [o.altitude_ft for o in observations if o.altitude_ft is not None]
    on_ground = sum(1 for o in observations if o.is_on_ground)
    low = sum(1 for a in altitudes if a < 3000)
    return {
        "observations": len(observations),
        "aircraft": len({o.icao24 for o in observations}),
        "with_altitude": len(altitudes),
        "min_altitude_ft": min(altitudes) if altitudes else None,
        "on_ground_observations": on_ground,
        "below_3000ft": low,
        "can_see_runway_level": on_ground > 0 or low > 0,
    }


def _load_csv(path: str) -> list[Observation]:
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
            ))
    return rows


def _load_db(path: str) -> list[Observation]:
    import sqlite3

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM adsb_log ORDER BY epoch").fetchall()
    conn.close()
    return [
        Observation(
            timestamp=row["epoch"], icao24=row["icao24"],
            registration=row["registration"], callsign=row["callsign"],
            altitude_ft=row["altitude_ft"], ground_speed_kt=row["ground_speed_kt"],
            vertical_rate_fpm=row["vertical_rate_fpm"],
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
        observations = _load_db(args.db)
        origen = args.db
    elif args.csv:
        observations = _load_csv(args.csv)
        origen = args.csv
    else:
        parser.error("indica un CSV o --db")

    cobertura = coverage_report(observations)
    print(f"=== {origen}")
    print(f"{cobertura['observations']} observaciones, {cobertura['aircraft']} aeronaves")
    alt = cobertura["min_altitude_ft"]
    print(f"altitud minima recibida : {alt:.0f} ft" if alt is not None else "sin altitudes")
    print(f"observaciones en tierra : {cobertura['on_ground_observations']}")
    print(f"observaciones <3000 ft  : {cobertura['below_3000ft']}")
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
