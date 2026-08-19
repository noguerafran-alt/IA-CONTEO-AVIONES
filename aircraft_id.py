"""Estimate an aircraft type from its measured size.

Two data sources feed this:

  aircraft_specs.json  extracted from the PDFs in DATOS AVIONES/. Broad coverage
                       (33 families) but shallow: several documents tabulate only
                       one variant, so the A320 page yields A319 numbers and the
                       ATR page yields ATR-42 numbers.

  FLEET below          a hand-written table of the types that actually operate at
                       Aeroparque, which is what the identification has to choose
                       between. Verify these against your own source before
                       trusting the output.

What this can and cannot do is a matter of geometry, not code quality:
a bounding box measures apparent size, so real dimensions require knowing the
distance to the aircraft (see estimate_dimensions). And even with perfect
measurement, a 737-800 (39.5 m) and an A320 (37.6 m) differ by 5%, which is
below the noise of a detector box. Size gives a size CLASS reliably; it gives an
exact model only when combined with something else, such as the airline.
"""
import json
from pathlib import Path

SPECS_FILE = Path(__file__).parent / "aircraft_specs.json"

# length_m, wingspan_m. Types commonly seen at Aeroparque (AEP).
FLEET = [
    {"model": "ATR 42", "family": "ATR",        "length": 22.7, "wingspan": 24.6},
    {"model": "ATR 72", "family": "ATR",        "length": 27.2, "wingspan": 27.1},
    {"model": "Embraer E190", "family": "Embraer E-Jet",  "length": 36.2, "wingspan": 28.7},
    {"model": "Embraer E195", "family": "Embraer E-Jet",  "length": 38.7, "wingspan": 28.7},
    {"model": "Bombardier CRJ900", "family": "Bombardier CRJ", "length": 36.4, "wingspan": 24.9},
    {"model": "Airbus A320", "family": "Airbus A320",   "length": 37.6, "wingspan": 35.8},
    {"model": "Airbus A321", "family": "Airbus A320",   "length": 44.5, "wingspan": 35.8},
    {"model": "Boeing 737-800", "family": "Boeing 737","length": 39.5, "wingspan": 35.8},
    {"model": "Boeing 737 MAX 8", "family": "Boeing 737", "length": 39.5, "wingspan": 35.9},
    {"model": "Airbus A330-200", "family": "Airbus A330",  "length": 58.8, "wingspan": 60.3},
    {"model": "Boeing 787-9", "family": "Boeing 787",  "length": 62.8, "wingspan": 60.1},
]

# Which types each airline actually flies at AEP. This is what turns a size
# class into a model: Flybondi operates a single type, so the airline alone
# decides it; Aerolineas flies two, and those two differ 25% in wingspan.
AIRLINE_FLEET = {
    "Flybondi": ["Boeing 737-800", "Boeing 737 MAX 8"],
    "Aerolíneas Argentinas": ["Boeing 737-800", "Boeing 737 MAX 8", "Embraer E190"],
    "Austral Líneas Aéreas": ["Embraer E190"],
    "JetSMART": ["Airbus A320", "Airbus A321"],
    "LATAM": ["Airbus A320", "Airbus A321"],
}

SIZE_CLASSES = [
    ("turboprop",  0, 30, "Turbohélice regional (ATR, Dash 8)"),
    ("regional",  30, 37, "Jet regional (E190, CRJ)"),
    ("narrowbody",37, 50, "Pasillo único (737, A320/A321)"),
    ("widebody",  50, 90, "Fuselaje ancho (A330, 787, 777)"),
]


def size_class(length_m: float) -> tuple[str, str]:
    for name, low, high, label in SIZE_CLASSES:
        if low <= length_m < high:
            return name, label
    return "unknown", "Fuera de rango"


def estimate_dimensions(box_width_px: float, box_height_px: float,
                        px_per_meter: float) -> dict:
    """Convert a bounding box to metres using a known scale at the measurement line.

    px_per_meter must be calibrated for the point where aircraft are measured:
    pixels alone cannot yield metres, because a small aircraft nearby and a large
    one further away produce the same box. On a fixed camera whose traffic always
    passes the same spot, one factor for that spot is a fair approximation; a full
    ground-plane homography is more accurate but needs survey points.

    Viewed side-on, box width tracks length and box height tracks tail height.
    A head-on view measures wingspan instead, so only measure where the aircraft
    presents the same aspect every time.
    """
    if px_per_meter <= 0:
        raise ValueError("px_per_meter must be positive")
    return {
        "length_m": round(box_width_px / px_per_meter, 1),
        "height_m": round(box_height_px / px_per_meter, 1),
    }


def candidates(length_m: float, airline: str | None = None,
               tolerance: float = 0.12) -> list[dict]:
    """Types whose length is within `tolerance` of the measurement, best first.

    Restricted to the airline's fleet when the airline is known, which is what
    makes the answer specific rather than a size bracket.
    """
    pool = FLEET
    if airline and airline in AIRLINE_FLEET:
        allowed = set(AIRLINE_FLEET[airline])
        pool = [a for a in FLEET if a["model"] in allowed] or FLEET

    scored = []
    for aircraft in pool:
        error = abs(aircraft["length"] - length_m) / aircraft["length"]
        if error <= tolerance:
            scored.append({**aircraft, "error_pct": round(error * 100, 1)})
    return sorted(scored, key=lambda a: a["error_pct"])


def identify(length_m: float, airline: str | None = None) -> dict:
    name, label = size_class(length_m)
    matches = candidates(length_m, airline)
    # Only call it decided when one candidate stands alone; otherwise report the
    # class and let the airline or a visual classifier break the tie.
    decided = matches[0]["model"] if len(matches) == 1 else None

    # Variants of one family (737-800 vs MAX 8) are the same size by design, so
    # size can never separate them -- but naming the family is still a real
    # answer, and often the useful one.
    families = {m["family"] for m in matches}
    family = families.pop() if len(families) == 1 else None

    return {
        "length_m": length_m,
        "size_class": name,
        "size_class_label": label,
        "airline": airline,
        "model": decided,
        "family": family,
        "candidates": matches,
    }


def load_pdf_specs() -> list[dict]:
    if not SPECS_FILE.exists():
        return []
    return json.loads(SPECS_FILE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    print("Escenarios de identificación por tamaño\n")
    for length, airline in [(39.5, "Flybondi"), (39.5, None), (36.2, "Aerolíneas Argentinas"),
                            (27.2, None), (58.8, None), (37.6, None)]:
        result = identify(length, airline)
        models = ", ".join(f"{c['model']} ({c['error_pct']}%)" for c in result["candidates"]) or "-"
        answer = result["model"] or result["family"] or "SIN DECIDIR"
        print(f"{length:5.1f} m  aerolinea={airline or '-':<22} clase={result['size_class']:<11}"
              f" -> {answer}")
        print(f"          candidatos: {models}\n")
