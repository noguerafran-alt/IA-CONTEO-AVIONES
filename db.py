"""SQLite persistence for landing/takeoff events."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "runway_events.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    source TEXT NOT NULL,
    video_time_s REAL NOT NULL,
    tracker_id INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('landing', 'takeoff')),
    thumbnail_path TEXT,
    registration TEXT,
    airline TEXT,
    annotated_video TEXT,
    registration_unconfirmed TEXT,
    aircraft_type TEXT,
    aircraft_type_unconfirmed TEXT,
    silhouette_path TEXT,
    adsb_registration TEXT,
    adsb_callsign TEXT,
    adsb_icao24 TEXT,
    adsb_note TEXT,
    wall_clock TEXT,
    adsb_aircraft_type TEXT,
    adsb_airline TEXT
);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    # Keep databases created before annotated_video existed usable.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
    for column in ("annotated_video", "registration_unconfirmed",
                   "aircraft_type", "aircraft_type_unconfirmed", "silhouette_path",
                   "adsb_registration", "adsb_callsign", "adsb_icao24", "adsb_note",
                   "wall_clock", "adsb_aircraft_type", "adsb_airline"):
        if column not in columns:
            conn.execute(f"ALTER TABLE events ADD COLUMN {column} TEXT")
    conn.commit()
    return conn


def insert_event(conn: sqlite3.Connection, *, source: str, video_time_s: float,
                  tracker_id: int, event_type: str, thumbnail_path: str | None = None,
                  annotated_video: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO events (source, video_time_s, tracker_id, event_type, thumbnail_path, annotated_video) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source, video_time_s, tracker_id, event_type, thumbnail_path, annotated_video),
    )
    conn.commit()
    return cur.lastrowid


def update_event_identification(conn: sqlite3.Connection, event_id: int, *,
                                 registration: str | None, airline: str | None,
                                 registration_unconfirmed: str | None = None,
                                 aircraft_type: str | None = None,
                                 aircraft_type_unconfirmed: str | None = None) -> None:
    """Store the identification. `registration` is only for readings two models
    agreed on; a lone reading goes to `registration_unconfirmed` so the dashboard
    can show it without it passing as verified."""
    conn.execute(
        "UPDATE events SET registration = ?, airline = ?, registration_unconfirmed = ?, "
        "aircraft_type = ?, aircraft_type_unconfirmed = ? WHERE id = ?",
        (registration, airline, registration_unconfirmed,
         aircraft_type, aircraft_type_unconfirmed, event_id),
    )
    conn.commit()


def update_event_silhouette(conn: sqlite3.Connection, event_id: int, path: str) -> None:
    """Crop showing the whole airframe, used to recognise the model."""
    conn.execute("UPDATE events SET silhouette_path = ? WHERE id = ?", (path, event_id))
    conn.commit()


def update_event_adsb(conn: sqlite3.Connection, event_id: int, *,
                      registration: str | None, callsign: str | None,
                      icao24: str | None, note: str | None,
                      aircraft_type: str | None = None, airline: str | None = None) -> None:
    """Identity reported by the aircraft itself, kept apart from what the
    camera read. They are independent sources: storing them in separate
    columns is what lets them be compared instead of silently overwriting
    each other.

    aircraft_type/airline come from a registry lookup by icao24 (see
    aircraft_db.py), not from the radio transmission itself -- raw ADS-B
    carries no field for either."""
    conn.execute(
        "UPDATE events SET adsb_registration = ?, adsb_callsign = ?, "
        "adsb_icao24 = ?, adsb_note = ?, adsb_aircraft_type = ?, adsb_airline = ? WHERE id = ?",
        (registration, callsign, icao24, note, aircraft_type, airline, event_id),
    )
    conn.commit()


def update_event_thumbnail(conn: sqlite3.Connection, event_id: int, thumbnail_path: str) -> None:
    conn.execute("UPDATE events SET thumbnail_path = ? WHERE id = ?", (thumbnail_path, event_id))
    conn.commit()


def list_events(conn: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def counts_by_type(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT event_type, COUNT(*) AS n FROM events GROUP BY event_type"
    ).fetchall()
    result = {"landing": 0, "takeoff": 0}
    for row in rows:
        result[row["event_type"]] = row["n"]
    return result
