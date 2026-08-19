"""Extract aircraft dimensions from the 'DATOS AVIONES' PDFs into aircraft_specs.json.

Only the numbers are kept (length / wingspan / height in metres), which is all
the size-based identification needs.

The tables are not consistent between documents: units appear as m, mtr, mtrs,
metres or meters; several variants can share one line; the label sometimes runs
into the number ("Length56.7"); and some labels look right but measure
something else ("Cabin Length", "Fuselage Height") so they must be excluded.
"""
import json
import re
from pathlib import Path

import pypdf

SPECS_DIR = Path(__file__).parent / "DATOS AVIONES"
OUTPUT = Path(__file__).parent / "aircraft_specs.json"

UNIT = r"(?:m|mtr|mtrs|metre|metres|meter|meters)\b"
NUMBER = r"([0-9]{1,3}(?:[.,][0-9]{1,2})?)"

# Labels that look relevant but measure a sub-part, not the whole aircraft.
EXCLUDE = re.compile(r"(?i)\b(cabin|fuselage\s+height|interior|cargo|door|seat)\b")

LABELS = {
    "length": r"(?:overall\s+|fuselage\s+)?length",
    "wingspan": r"wing\s?span",
    "height": r"(?:tail\s+|aircraft\s+|overall\s+)?height",
}

# Plausible airliner ranges, in metres. Anything outside is a misparse.
RANGES = {"length": (15, 85), "wingspan": (15, 85), "height": (4, 25)}

FAMILY_RE = re.compile(r"^(.*?)\s*[-–]\s*Modern Airliners", re.IGNORECASE)


def values_for(text: str, key: str) -> list[float]:
    """Every plausible measurement reported for one dimension in the document."""
    label = LABELS[key]
    low, high = RANGES[key]
    found = []
    # A label may be followed by several "<number> <unit>" pairs on one line,
    # one per variant, so keep consuming pairs after a single label match.
    for match in re.finditer(rf"(?i){label}\s*:?\s*((?:{NUMBER}\s*{UNIT}[^0-9\n]{{0,28}}){{1,6}})", text):
        context_start = max(0, match.start() - 30)
        if EXCLUDE.search(text[context_start:match.start() + len(label) + 10]):
            continue
        for number in re.finditer(rf"(?i){NUMBER}\s*{UNIT}", match.group(1)):
            value = float(number.group(1).replace(",", "."))
            if low <= value <= high:
                found.append(value)
    return found


def parse_pdf(path: Path) -> dict | None:
    try:
        reader = pypdf.PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        print(f"  omitido {path.name}: {exc}")
        return None

    dims = {key: values_for(text, key) for key in LABELS}
    if not dims["length"] or not dims["wingspan"]:
        return None

    family_match = FAMILY_RE.match(path.stem)
    family = (family_match.group(1) if family_match else path.stem).strip()

    # When the document lists the same number of lengths and wingspans they are
    # variant-by-variant and can be paired; otherwise keep them as a range,
    # which is still enough to place the aircraft in a size class.
    variants = []
    if len(dims["length"]) == len(dims["wingspan"]):
        heights = dims["height"] if len(dims["height"]) == len(dims["length"]) else []
        for i, (length, span) in enumerate(zip(dims["length"], dims["wingspan"])):
            variant = {"length": length, "wingspan": span}
            if heights:
                variant["height"] = heights[i]
            variants.append(variant)

    return {
        "family": family,
        "length_min": min(dims["length"]), "length_max": max(dims["length"]),
        "wingspan_min": min(dims["wingspan"]), "wingspan_max": max(dims["wingspan"]),
        "variants": variants,
    }


def main():
    entries, empty = [], []
    for pdf in sorted(SPECS_DIR.glob("*.pdf")):
        parsed = parse_pdf(pdf)
        if parsed:
            entries.append(parsed)
        else:
            empty.append(pdf.stem.split("–")[0].strip())

    OUTPUT.write_text(json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(entries)} familias con medidas -> {OUTPUT.name}")
    if empty:
        print(f"sin medidas ({len(empty)}): {', '.join(empty)}")


if __name__ == "__main__":
    main()
