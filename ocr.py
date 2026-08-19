"""Best-effort registration (matrícula) + airline reading from an airplane crop.

No training involved: runs OCR (EasyOCR) over the crop and pattern-matches the
recognized text against an Argentine registration format and a list of known
airline names and slogans.

Measured behaviour on the Aeroparque footage:

  640x360   nothing readable at all (garbage like 'MrosojnO')
  1920x1080 airline reliably readable; registration detected but MISREAD
            ('LV-LKK' for a real LV-HKN), at confidence ~0.09-0.17

Because a wrong registration is worse than no registration, every read is
filtered by the recognizer's own confidence. Airline text (large, on the
fuselage) clears the bar; registration lettering at this distance does not.
Reading registrations reliably needs 4K, optical zoom, or a closer camera.
"""
import difflib
import re
import unicodedata

import numpy as np

_reader = None

# Argentine civil registrations: LV- or LQ- plus 3 letters/digits (LV-HKN).
REGISTRATION_RE = re.compile(r"\b(L[VQ])[\s-]?([A-Z0-9]{3})\b")

# Registration needs a confidence floor because there is nothing to validate a
# tail number against: any three characters look like a plausible registration,
# so a low-confidence read is indistinguishable from a wrong one. Measured on
# this footage, a real misread ('LV-HKH' for LV-HKN) scored 0.55, so the bar
# sits above that. A wrong registration is worse than a missing one.
MIN_CONFIDENCE_REGISTRATION = 0.60

# Airline text is the opposite case: it is matched against a closed list, and
# matching a long name is itself the evidence -- noise does not accidentally
# resemble "aerolineas argentinas". Confidence is therefore a poor filter here
# (a perfectly correct 'AeRolineAS ARgentinAS' scored only 0.20 because of the
# odd casing), so similarity is used instead of the recognizer's own score.
MIN_AIRLINE_SIMILARITY = 0.80
# Below this length a name is too short for fuzzy matching to be safe
# ("gol", "azul" would match random noise), so those require an exact hit.
FUZZY_MIN_KEYWORD_LEN = 6

# Fuselage text -> airline. Slogans count: they are painted far larger than
# the logo and survive at distances where a stylized wordmark does not.
KNOWN_AIRLINES = {
    "aerolineas argentinas": "Aerolíneas Argentinas",
    "aerolineas": "Aerolíneas Argentinas",
    "austral": "Austral Líneas Aéreas",
    "flybondi": "Flybondi",
    "libertad de volar": "Flybondi",
    "jetsmart": "JetSMART",
    "latam": "LATAM",
    "gol": "GOL Linhas Aéreas",
    "avianca": "Avianca",
    "azul": "Azul",
    "american airlines": "American Airlines",
    "copa": "Copa Airlines",
    "iberia": "Iberia",
    "air europa": "Air Europa",
    "sky airline": "Sky Airline",
    "paranair": "Paranair",
}


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _reader


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return text.lower().strip()


def read_text(crop: np.ndarray) -> list[tuple[str, float]]:
    """Return [(text, confidence), ...] found in the crop."""
    if crop is None or crop.size == 0:
        return []
    results = _get_reader().readtext(crop, detail=1)
    return [(str(text), float(confidence)) for _, text, confidence in results]


def extract_registration(readings: list[tuple[str, float]]) -> str | None:
    for text, confidence in readings:
        if confidence < MIN_CONFIDENCE_REGISTRATION:
            continue
        match = REGISTRATION_RE.search(text.upper())
        if match:
            return f"{match.group(1)}-{match.group(2)}"
    return None


def _best_similarity(haystack: str, keyword: str) -> float:
    """Highest similarity between the keyword and any same-length window of the text."""
    if len(haystack) < len(keyword):
        return difflib.SequenceMatcher(None, haystack, keyword).ratio()
    best = 0.0
    for start in range(len(haystack) - len(keyword) + 1):
        window = haystack[start:start + len(keyword)]
        best = max(best, difflib.SequenceMatcher(None, window, keyword).ratio())
        if best == 1.0:
            break
    return best


def extract_airline(readings: list[tuple[str, float]]) -> str | None:
    text = " ".join(_normalize(t) for t, _ in readings)
    if not text:
        return None

    # Longest key first so "aerolineas argentinas" wins over "aerolineas".
    for keyword in sorted(KNOWN_AIRLINES, key=len, reverse=True):
        if keyword in text:
            return KNOWN_AIRLINES[keyword]

    # Nothing matched exactly: allow for OCR slips ("liberlad de volar" for
    # "libertad de volar"), but only on names long enough that a close match
    # cannot happen by chance.
    for keyword in sorted(KNOWN_AIRLINES, key=len, reverse=True):
        if len(keyword) < FUZZY_MIN_KEYWORD_LEN:
            continue
        if _best_similarity(text, keyword) >= MIN_AIRLINE_SIMILARITY:
            return KNOWN_AIRLINES[keyword]
    return None


def identify(crop: np.ndarray) -> dict:
    readings = read_text(crop)
    return {
        "registration": extract_registration(readings),
        "airline": extract_airline(readings),
        "raw_text": [text for text, _ in readings],
        "readings": readings,
    }


def canonical_airline(name: str | None) -> str | None:
    """Map free-text airline output to one canonical spelling.

    The vision model writes the name as it reads it, so one operator arrives as
    'Aerolíneas Argentinas', 'Aerolineas Argentinas' and 'Aeroline Argentinas'.
    Grouping by raw text would count those as three airlines.
    """
    if not name:
        return None
    text = _normalize(name)
    for keyword in sorted(KNOWN_AIRLINES, key=len, reverse=True):
        if keyword in text:
            return KNOWN_AIRLINES[keyword]
    for keyword in sorted(KNOWN_AIRLINES, key=len, reverse=True):
        if len(keyword) >= FUZZY_MIN_KEYWORD_LEN and _best_similarity(text, keyword) >= MIN_AIRLINE_SIMILARITY:
            return KNOWN_AIRLINES[keyword]
    return name.strip()


# Registrations are LV/LQ plus three LETTERS. An all-digit tail like 'LV-600'
# is a misread, not an aircraft, and must not pass as confirmed.
VALID_REGISTRATION_RE = re.compile(r"^L[VQ]-[A-Z]{3}$")


def registration_looks_valid(registration: str | None) -> bool:
    return bool(registration and VALID_REGISTRATION_RE.match(registration.upper()))
