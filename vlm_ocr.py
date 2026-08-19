"""Read registration / airline from an aircraft crop using a vision model on OpenRouter.

Why this exists: EasyOCR fails on this material in a specific way. It reads the
tail number character by character and, on the blurred low-contrast lettering of
a moving aircraft, it misreads 'N' as 'M' in most frames -- consistently enough
that voting across frames still produces the wrong answer. The pixels do carry
the information (a human reads LV-HKN from the same crop), so the weakness is
the recognizer, not the resolution.

A vision-language model reads with context instead of per character, which is
the property we need. The risk is the opposite one: it can return a confident,
plausible-looking registration for an illegible crop. The prompt therefore
demands an explicit legibility verdict, and everything is validated against
crops whose answer we already know (see validate_vlm.py).

Setup:
  echo OPENROUTER_API_KEY=sk-or-... > .env      (the file is gitignored)
"""
import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

API_URL = "https://openrouter.ai/api/v1/chat/completions"
# Both free NVIDIA vision models read these tail numbers correctly; the omni
# one scored 6/6 with no timeouts against 4/6 for the 12B VL, so it leads and
# the other is the fallback. Measured by validate_vlm.py.
DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
FALLBACK_MODEL = "nvidia/nemotron-nano-12b-v2-vl:free"
ENV_FILE = Path(__file__).parent / ".env"

REGISTRATION_RE = re.compile(r"\b(L[VQ])[\s-]?([A-Z0-9]{3})\b")

PROMPT = """You are reading an aircraft photograph taken from an airport spotting camera.

Report exactly three things:
1. registration: the tail number painted on the aircraft (Argentine ones look like LV-ABC).
2. airline: the operator, from the livery, logo or fuselage text.
3. aircraft_type: the model, from the airframe shape -- nose profile, engine nacelles,
   wingtip devices, tail, number of engines, jet vs turboprop. Use the common name
   ("Boeing 737-800", "Airbus A320", "Embraer E190", "ATR 72"). If you can tell the
   family but not the variant, give the family ("Boeing 737"). Null if you cannot tell.

Rules that matter more than giving an answer:
- Report only what is actually legible in the image. Do NOT guess, complete or
  correct a partially visible registration, and do not infer one from the airline.
- If the registration is not clearly readable, set it to null. A null is a correct
  answer; an invented tail number is a serious error.
- Set "registration_legible" to true only if you can read every character.

The same caution applies to the type: report what the shape shows, not what the
airline usually flies. Inferring the model from the operator is a guess, not a reading.

Reply with JSON only:
{"registration": "LV-ABC" or null, "airline": "name" or null,
 "aircraft_type": "model" or null, "registration_legible": true/false,
 "notes": "what you could actually see"}"""


def load_api_key() -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key.strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("OPENROUTER_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def encode_image(image: np.ndarray, max_width: int = 1600, upscale_small: int = 700) -> str:
    """PNG data URI for the crop. Small crops are enlarged first, since the
    lettering is only a few pixels tall at native size."""
    height, width = image.shape[:2]
    if width < upscale_small:
        factor = min(4.0, upscale_small / max(width, 1))
        image = cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_LANCZOS4)
    elif width > max_width:
        factor = max_width / width
        image = cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("could not encode image")
    return "data:image/png;base64," + base64.b64encode(buffer).decode("ascii")


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def identify(image: np.ndarray, model: str = DEFAULT_MODEL, timeout: int = 90,
             retries: int = 3, fallback: bool = True) -> dict:
    """Read one crop, retrying transient upstream failures and rate limits."""
    import time as _time
    attempts = [model] + ([FALLBACK_MODEL] if fallback and model != FALLBACK_MODEL else [])
    last = {}
    for candidate in attempts:
        for attempt in range(retries):
            last = _identify_once(image, candidate, timeout)
            if not last.get("error"):
                return {**last, "model": candidate}
            # 429 = rate limited, 5xx / idle timeout = upstream hiccup: both worth waiting out.
            _time.sleep(3 * (attempt + 1))
    return last


def _identify_once(image: np.ndarray, model: str, timeout: int) -> dict:
    api_key = load_api_key()
    if not api_key:
        return {"error": "missing OPENROUTER_API_KEY (put it in .env)"}

    payload = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": encode_image(image)}},
        ]}],
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return {"error": f"unexpected response: {str(body)[:200]}"}

    parsed = _extract_json(content if isinstance(content, str) else str(content))
    registration = parsed.get("registration")
    if registration:
        # Keep it only if it really looks like a registration and the model said
        # it could read it; otherwise treat it as not found.
        match = REGISTRATION_RE.search(str(registration).upper())
        registration = (f"{match.group(1)}-{match.group(2)}"
                        if match and parsed.get("registration_legible") else None)

    return {
        "registration": registration,
        "airline": parsed.get("airline") or None,
        "aircraft_type": parsed.get("aircraft_type") or None,
        "legible": bool(parsed.get("registration_legible")),
        "notes": parsed.get("notes"),
        "raw": content,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        sys.exit("uso: python vlm_ocr.py <imagen.jpg> [modelo]")
    img = cv2.imread(sys.argv[1])
    if img is None:
        sys.exit(f"no pude leer {sys.argv[1]}")
    result = identify(img, sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL)
    print(json.dumps(result, indent=1, ensure_ascii=False))


def identify_consensus(image: np.ndarray, models: list[str] | None = None) -> dict:
    """Ask two independent models and only trust a registration they agree on.

    Necessary because accuracy on hand-picked crops does not carry over to the
    crops the pipeline actually produces: on a validation set of tight crops both
    models scored perfectly, yet on pipeline output one returned 'LV-4KN' for an
    aircraft it had read correctly as 'LV-HKN' elsewhere. The failure is silent --
    a wrong tail number looks exactly like a right one -- so agreement between
    two models is used as the confidence signal that a single model does not give.

    Returns agreed=True only when both read the same registration.
    """
    models = models or [DEFAULT_MODEL, FALLBACK_MODEL]
    readings = []
    for model in models:
        result = identify(image, model=model, fallback=False)
        if not result.get("error"):
            readings.append((model, result))

    if not readings:
        return {"registration": None, "airline": None, "agreed": False,
                "error": "no model responded", "votes": []}

    regs = [r.get("registration") for _, r in readings]
    airlines = [r.get("airline") for _, r in readings if r.get("airline")]
    named = [r for r in regs if r]
    agreed = len(named) == len(readings) and len(set(named)) == 1 and len(readings) > 1

    # Same consensus rule for the type: two models naming the same aircraft is
    # evidence, one model naming it alone is a guess worth showing but flagging.
    types = [r.get("aircraft_type") for _, r in readings if r.get("aircraft_type")]
    types_norm = [t.strip().lower() for t in types]
    type_agreed = len(types) == len(readings) and len(set(types_norm)) == 1 and len(readings) > 1

    return {
        # Only surface a registration when the models agree; a lone reading is
        # kept separately so the dashboard can show it as unconfirmed.
        "registration": named[0] if agreed else None,
        "registration_unconfirmed": named[0] if (named and not agreed) else None,
        "airline": airlines[0] if airlines else None,
        "aircraft_type": types[0] if type_agreed else None,
        "aircraft_type_unconfirmed": (types[0] if (types and not type_agreed) else None),
        "agreed": agreed,
        "votes": [{"model": m.split("/")[-1], "registration": r.get("registration"),
                   "aircraft_type": r.get("aircraft_type")} for m, r in readings],
    }
