"""Local lookup of aircraft type and airline by ICAO24, from OpenSky's registry.

Raw ADS-B does not transmit the aircraft model or the airline name -- the
protocol has no field for either. It only gives the ICAO24 address (a fixed
hex ID assigned to that specific airframe) and, separately, a callsign that
identifies the flight, not the operator.

OpenSky publishes a free, offline snapshot of the world aircraft registry
(icao24 -> registration, manufacturer, model, typecode, operator) built from
official sources. This downloads it once, keeps only the columns this project
needs in a local SQLite file, and looks up an ICAO24 against it -- no network
call per lookup, no API key, works with the antenna's dongle unplugged.

Uso:
  python aircraft_db.py --build              descargar y construir (una vez)
  python aircraft_db.py --lookup e80456       probar una consulta
"""
from __future__ import annotations

import csv
import io
import sqlite3
import sys
import urllib.request
from pathlib import Path

CSV_URL = "https://s3.opensky-network.org/data-samples/metadata/aircraftDatabase.csv"
DB_PATH = Path(__file__).parent / "tools" / "aircraft_db.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS aircraft (
    icao24 TEXT PRIMARY KEY,
    registration TEXT,
    manufacturer TEXT,
    model TEXT,
    typecode TEXT,
    operator TEXT,
    operator_icao TEXT,
    operator_iata TEXT
);
"""


def _iter_csv_rows(source):
    """Yield dict rows from the OpenSky CSV, tolerating its quirks.

    The file quotes every field, including empty ones ("",""), which the
    standard csv module handles fine -- but real-world copies of this export
    have occasionally carried stray encoding issues, so decoding is lenient
    (errors="replace") rather than raising mid-download and losing the rows
    read so far.
    """
    reader = csv.DictReader(source)
    for row in reader:
        yield row


def build(csv_url: str = CSV_URL, db_path: Path = DB_PATH, log=print) -> int:
    """Download the registry and (re)build the local lookup database.

    Returns the number of aircraft indexed.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".sqlite.tmp")
    tmp_path.unlink(missing_ok=True)

    log(f"Descargando {csv_url} ...")
    conn = sqlite3.connect(tmp_path)
    conn.execute(SCHEMA)

    count = 0
    with urllib.request.urlopen(csv_url, timeout=60) as response:
        text_stream = io.TextIOWrapper(response, encoding="utf-8", errors="replace", newline="")
        batch = []
        for row in _iter_csv_rows(text_stream):
            icao24 = (row.get("icao24") or "").strip().lower()
            if not icao24:
                continue
            batch.append((
                icao24,
                (row.get("registration") or "").strip() or None,
                (row.get("manufacturername") or "").strip() or None,
                (row.get("model") or "").strip() or None,
                (row.get("typecode") or "").strip() or None,
                (row.get("operator") or "").strip() or None,
                (row.get("operatoricao") or "").strip() or None,
                (row.get("operatoriata") or "").strip() or None,
            ))
            count += 1
            if len(batch) >= 5000:
                conn.executemany(
                    "INSERT OR REPLACE INTO aircraft VALUES (?,?,?,?,?,?,?,?)", batch)
                batch.clear()
                if count % 50000 == 0:
                    log(f"  {count} aeronaves indexadas...")
        if batch:
            conn.executemany("INSERT OR REPLACE INTO aircraft VALUES (?,?,?,?,?,?,?,?)", batch)

    conn.commit()
    conn.close()

    # Atomic-ish swap: build into a .tmp file so a crash mid-download never
    # leaves a half-written database silently answering wrong lookups.
    db_path.unlink(missing_ok=True)
    tmp_path.rename(db_path)
    log(f"Listo: {count} aeronaves en {db_path}")
    return count


_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection | None:
    global _conn
    if _conn is None:
        if not DB_PATH.exists():
            return None
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def available() -> bool:
    return DB_PATH.exists()


def lookup(icao24: str) -> dict | None:
    """Registration/manufacturer/model/typecode/operator for one ICAO24, or
    None if the database isn't built yet or the aircraft isn't in it (common
    for military, some GA, or aircraft registered after the snapshot)."""
    conn = _get_conn()
    if conn is None:
        return None
    row = conn.execute(
        "SELECT * FROM aircraft WHERE icao24 = ?", (icao24.strip().lower(),)
    ).fetchone()
    return dict(row) if row else None


def describe_type(entry: dict) -> str | None:
    """Human-readable model string ('Boeing 737-800'), best available field."""
    if not entry:
        return None
    if entry.get("model"):
        return entry["model"]
    if entry.get("typecode"):
        return entry["typecode"]
    return None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true", help="Descargar y construir la base")
    parser.add_argument("--lookup", metavar="ICAO24", help="Probar una consulta")
    args = parser.parse_args()

    if args.build:
        build()
    elif args.lookup:
        entry = lookup(args.lookup)
        if entry is None:
            if not available():
                print("La base no esta construida. Corre: python aircraft_db.py --build")
            else:
                print(f"{args.lookup}: no encontrado en la base")
        else:
            print(f"icao24         : {entry['icao24']}")
            print(f"matricula      : {entry['registration'] or '-'}")
            print(f"tipo           : {describe_type(entry) or '-'}")
            print(f"fabricante     : {entry['manufacturer'] or '-'}")
            print(f"aerolinea      : {entry['operator'] or '-'}")
            print(f"aerolinea ICAO : {entry['operator_icao'] or '-'}")
    else:
        parser.print_help()
