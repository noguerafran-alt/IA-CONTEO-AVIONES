"""Run OCR over already-stored event thumbnails and fill in registration/airline.

Useful when events were recorded without --ocr, or after tweaking the airline
list / registration pattern in ocr.py.

Usage:
  python backfill_ocr.py              # only rows still missing both fields
  python backfill_ocr.py --all        # redo every row
  python backfill_ocr.py --upscale 3  # enlarge crops before OCR (helps small text)
"""

import argparse
from pathlib import Path

import cv2

import db
import ocr
import vlm_ocr


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="Re-process rows that already have values")
    parser.add_argument("--upscale", type=float, default=2.0, help="Scale factor applied before OCR")
    parser.add_argument("--verbose", action="store_true", help="Print the raw OCR text per crop")
    parser.add_argument("--vlm", action="store_true",
                        help="Read with the OpenRouter vision model instead of EasyOCR. Measured on this "
                             "footage: EasyOCR 0/3 tail numbers, vision model 6/6 (see validate_vlm.py).")
    parser.add_argument("--model", default=None, help="Override the vision model id")
    args = parser.parse_args()

    conn = db.get_connection()
    rows = conn.execute(
        "SELECT id, thumbnail_path, registration, airline FROM events "
        "WHERE thumbnail_path IS NOT NULL ORDER BY id"
    ).fetchall()

    pending = [
        row for row in rows
        if args.all or (row["registration"] is None and row["airline"] is None)
    ]
    if not pending:
        print("Nothing to do -- every event already has registration/airline.")
        conn.close()
        return

    print(f"Processing {len(pending)} of {len(rows)} events ...")
    hits = 0
    for row in pending:
        path = Path(row["thumbnail_path"])
        if not path.exists():
            print(f"  #{row['id']}: thumbnail missing ({path})")
            continue

        crop = cv2.imread(str(path))
        if crop is None:
            print(f"  #{row['id']}: unreadable image")
            continue

        if args.upscale != 1.0 and not args.vlm:
            crop = cv2.resize(crop, None, fx=args.upscale, fy=args.upscale, interpolation=cv2.INTER_CUBIC)

        if args.vlm:
            result = vlm_ocr.identify_consensus(crop)
            if result.get("error"):
                print(f"  #{row['id']}: error del modelo -> {result['error'][:80]}")
                continue
            found = {"registration": result.get("registration"),
                     "airline": result.get("airline"),
                     "unconfirmed": result.get("registration_unconfirmed"),
                     "aircraft_type": result.get("aircraft_type"),
                     "aircraft_type_unconfirmed": result.get("aircraft_type_unconfirmed"),
                     "raw_text": [str(result.get("votes"))]}
        else:
            found = ocr.identify(crop)
        db.update_event_identification(
            conn, row["id"],
            registration=found["registration"],
            airline=found["airline"],
            registration_unconfirmed=found.get("unconfirmed"),
            aircraft_type=found.get("aircraft_type"),
            aircraft_type_unconfirmed=found.get("aircraft_type_unconfirmed"),
        )

        if found["registration"] or found["airline"] or found.get("unconfirmed"):
            hits += 1
            mark = found["registration"] or (f"{found.get('unconfirmed')} (sin confirmar)"
                                             if found.get("unconfirmed") else None)
            tipo = found.get("aircraft_type") or (
                f"{found.get('aircraft_type_unconfirmed')} (sin confirmar)"
                if found.get("aircraft_type_unconfirmed") else "-")
            print(f"  #{row['id']}: airline={found['airline']} registration={mark} tipo={tipo}")
        elif args.verbose:
            print(f"  #{row['id']}: nothing matched. raw={found['raw_text']}")

    conn.close()
    print(f"\nIdentified something on {hits}/{len(pending)} events.")
    print("Blanks are expected for distant aircraft and stylized logos -- see README.")


if __name__ == "__main__":
    main()
