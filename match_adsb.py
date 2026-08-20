"""Decide which ADS-B aircraft corresponds to a crossing seen by the camera.

The camera establishes THAT an operation happened and exactly when. ADS-B says
which aircraft were around. Joining them is not simply "closest in time":
during a busy period several aircraft are within seconds of each other, and
picking the nearest timestamp would attach the wrong tail number.

So a candidate is scored on evidence that it is the aircraft the camera saw:

  behaviour   a landing should show an aircraft descending or touching down,
              a takeoff one climbing or accelerating. This is the strongest
              signal, because it is the same event seen by another sensor.
  time        how close its report is to the crossing.
  proximity   how close it is to the camera, when the camera position is known.

A wrong tail number is worse than none -- the same rule used for OCR -- so a
match is only accepted when the best candidate is clearly ahead of the runner
up. If two aircraft are equally plausible, the event stays unidentified and
says so, instead of guessing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from adsb import Observation

# Nudge a candidate whose behaviour matches the manoeuvre the camera saw.
BEHAVIOUR_BONUS = 0.6
# Below this score nothing is reported: the evidence is too thin.
MIN_SCORE = 0.35
# The winner must beat the runner up by this much, otherwise it is ambiguous.
MIN_MARGIN = 0.15
# Beyond this many seconds an observation is not about this crossing.
DEFAULT_WINDOW_S = 45.0


@dataclass
class Match:
    observation: Observation | None
    score: float
    reason: str
    ambiguous: bool = False
    runner_up: Observation | None = None

    @property
    def registration(self) -> str | None:
        return self.observation.registration if self.observation and not self.ambiguous else None

    @property
    def callsign(self) -> str | None:
        return self.observation.callsign if self.observation and not self.ambiguous else None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres between two coordinates."""
    radius = 6_371_000.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(a))


def _behaviour_fits(observation: Observation, event_type: str) -> bool:
    """Does the aircraft's own telemetry agree with what the camera saw?"""
    if event_type == "landing":
        return observation.is_descending or observation.is_on_ground
    if event_type == "takeoff":
        return observation.is_climbing or (
            observation.is_on_ground
            and observation.ground_speed_kt is not None
            and observation.ground_speed_kt > 50  # rolling for departure, not taxiing
        )
    return False


def score_candidate(observation: Observation, moment: float, event_type: str,
                    camera_lat: float | None = None, camera_lon: float | None = None,
                    window_s: float = DEFAULT_WINDOW_S,
                    max_distance_m: float = 5000.0) -> tuple[float, str]:
    """Score 0..1+ for this aircraft being the one the camera saw, plus why."""
    gap = abs(observation.timestamp - moment)
    if gap > window_s:
        return 0.0, "fuera de la ventana de tiempo"

    reasons = []
    score = 1.0 - (gap / window_s)          # 1.0 exactly on time, 0.0 at the edge
    reasons.append(f"{gap:.0f}s de diferencia")

    if _behaviour_fits(observation, event_type):
        score += BEHAVIOUR_BONUS
        if event_type == "landing":
            reasons.append("descendiendo" if observation.is_descending else "en pista")
        else:
            reasons.append("ascendiendo" if observation.is_climbing else "acelerando en pista")
    else:
        reasons.append("sin confirmar por telemetria")

    if (camera_lat is not None and camera_lon is not None
            and observation.latitude is not None and observation.longitude is not None):
        distance = haversine_m(camera_lat, camera_lon, observation.latitude, observation.longitude)
        if distance > max_distance_m:
            return 0.0, f"a {distance/1000:.1f} km, demasiado lejos"
        score += 0.4 * (1.0 - distance / max_distance_m)
        reasons.append(f"a {distance:.0f} m")

    return score, ", ".join(reasons)


def match_event(observations: list[Observation], moment: float, event_type: str,
                camera_lat: float | None = None, camera_lon: float | None = None,
                window_s: float = DEFAULT_WINDOW_S) -> Match:
    """Pick the aircraft behind one crossing, or report that it is undecidable."""
    if not observations:
        return Match(None, 0.0, "sin datos ADS-B")

    # One aircraft reports many times per minute; keep only its best-scoring
    # report so a chatty transponder cannot outvote a quiet one.
    best_per_aircraft: dict[str, tuple[float, str, Observation]] = {}
    for observation in observations:
        score, reason = score_candidate(observation, moment, event_type,
                                        camera_lat, camera_lon, window_s)
        if score <= 0:
            continue
        previous = best_per_aircraft.get(observation.icao24)
        if previous is None or score > previous[0]:
            best_per_aircraft[observation.icao24] = (score, reason, observation)

    if not best_per_aircraft:
        return Match(None, 0.0, "ningun avion compatible en la ventana")

    ranked = sorted(best_per_aircraft.values(), key=lambda x: -x[0])
    score, reason, observation = ranked[0]

    if score < MIN_SCORE:
        return Match(None, score, f"evidencia debil ({reason})")

    if len(ranked) > 1:
        margin = score - ranked[1][0]
        if margin < MIN_MARGIN:
            otro = ranked[1][2]
            return Match(observation, score,
                         f"ambiguo: {observation.registration or observation.icao24} y "
                         f"{otro.registration or otro.icao24} igual de probables",
                         ambiguous=True, runner_up=otro)

    return Match(observation, score, reason)
