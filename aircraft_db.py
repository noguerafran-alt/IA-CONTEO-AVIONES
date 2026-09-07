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
from functools import lru_cache as _lru_cache
import sys
import urllib.request
from pathlib import Path

CSV_URL = "https://s3.opensky-network.org/data-samples/metadata/aircraftDatabase.csv"
DB_PATH = Path(__file__).parent / "tools" / "aircraft_db.sqlite"

# EL ORDEN MANDA: de esta tupla salen el CREATE TABLE, los placeholders del
# INSERT y la tupla de cada fila, en los DOS caminos de construccion.
#
# Estaban escritos a mano: cuatro "INSERT OR REPLACE INTO aircraft VALUES
# (?,?,?,?,?,?,?,?)" con ocho signos de pregunta contados a ojo, mas dos listas
# de columnas separadas. Agregar una columna pedia acertar en seis lugares, y
# equivocarse en uno solo no da un error legible: da "table aircraft has 9
# columns but 8 values were supplied" a mitad de una importacion de 600 mil
# filas, o peor, corre los valores de lugar en silencio si el orden no coincide.
#
# serial_number entra el 2026-09-06. Medido sobre el CSV completo de OpenSky y
# las 32 operaciones de Aeroparque que estan en el: lo trae el 66%. Identifica el
# FUSELAJE fisico, que es mas estable que la matricula -- la matricula cambia de
# dueno y hasta de pais, el numero de serie no cambia nunca-, asi que sirve para
# saber si el LV-XXX de hoy es el mismo avion de la semana pasada. Es lo unico
# nuevo que el CSV aporta: built y firstFlightDate dan 0% para Argentina, medido
# y descartado con numeros en ESTADO.md.
COLUMNAS = ("icao24", "registration", "manufacturer", "model", "typecode",
            "operator", "operator_icao", "operator_iata", "serial_number")

SCHEMA = ("CREATE TABLE IF NOT EXISTS aircraft (\n    "
          + ",\n    ".join(c + " TEXT" + (" PRIMARY KEY" if c == "icao24" else "")
                           for c in COLUMNAS)
          + "\n);")

INSERT = ("INSERT OR REPLACE INTO aircraft VALUES ("
          + ",".join("?" * len(COLUMNAS)) + ")")


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


# Alias de columna: el export "complete" de OpenSky usa camelCase y el de
# data-samples minusculas, y los dos existen en el mundo. Se aceptan las dos
# formas en vez de elegir una, porque elegir una hace que el otro archivo se
# importe entero con todas las columnas en None y sin un solo error.
ALIAS = {
    "icao24": ("icao24",),
    "registration": ("registration", "reg"),
    "manufacturer": ("manufacturername", "manufacturer", "manufacturericao"),
    "model": ("model",),
    "typecode": ("typecode", "icaoaircraftclass", "icaoaircrafttype"),
    "operator": ("operator", "owner", "operatorcallsign"),
    "operator_icao": ("operatoricao",),
    "operator_iata": ("operatoriata",),
    # Solo el nombre camelCase del export completo: el de data-samples no
    # trae la columna, y _valor devuelve None sin romper nada.
    "serial_number": ("serialnumber",),
}


def _normalizar(nombre: str) -> str:
    """El encabezado del export completo viene entre comillas SIMPLES.

    csv.DictReader no las saca -no son comillas de CSV, son parte del texto- asi
    que sin esto las claves quedan "'icao24'" con comillas incluidas y ningun
    row.get() acierta nunca. El archivo se importa completo y todas las columnas
    salen vacias, sin un solo error que lo delate.
    """
    return nombre.strip().strip("'").strip('"').replace("_", "").lower()


def _valor(fila: dict, campo: str) -> str | None:
    for alias in ALIAS[campo]:
        v = fila.get(alias)
        if v:
            v = v.strip().strip("'")
            if v:
                return v
    return None


def build_desde_archivo(csv_path: Path | str, db_path: Path = DB_PATH, log=print) -> int:
    """Construir la base desde un CSV ya bajado, en vez de descargarlo.

    Existe porque el export COMPLETO de OpenSky no esta en la URL de
    data-samples que usa build(): son dos datasets distintos, y la diferencia se
    midio sobre trafico real de esta antena. Con 10 mensajes o mas por aeronave,
    la cobertura de matricula pasa de 61% a 89%; en las operaciones de Aeroparque,
    de 1 de 8 a 6 de 8. Los que faltaban eran los aviones matriculados hace poco
    -bloques e8 06 1x-3x de JetSMART, 0c de Copa- que ningun snapshot viejo tiene.

    El archivo se baja a mano de https://opensky-network.org/datasets/metadata/
    porque pide sesion: no se puede automatizar sin credenciales.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"no se encontro {csv_path}")

    # El campo "notes" de este export pasa los 131072 bytes que el modulo csv
    # permite por defecto, y el error salta a mitad del archivo dejando la base
    # a medio construir.
    csv.field_size_limit(10_000_000)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".sqlite.tmp")
    tmp_path.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp_path)
    conn.execute(SCHEMA)

    log(f"Importando {csv_path} ...")
    count = 0
    with io.open(csv_path, encoding="utf-8", errors="replace", newline="") as fh:
        lector = csv.reader(fh)
        cabecera = [_normalizar(c) for c in next(lector)]
        indices = {c: k for k, c in enumerate(cabecera)}
        batch = []
        for cruda in lector:
            fila = {c: (cruda[k] if k < len(cruda) else "") for c, k in indices.items()}
            icao24 = (_valor(fila, "icao24") or "").lower()
            if not icao24:
                continue
            # Desde COLUMNAS y no a mano: es lo que garantiza que el orden de
            # los valores sea el del CREATE TABLE. icao24 ya viene resuelto y en
            # minusculas, asi que se usa el de arriba en vez de releerlo.
            batch.append(tuple(icao24 if c == "icao24" else _valor(fila, c)
                               for c in COLUMNAS))
            count += 1
            if len(batch) >= 5000:
                conn.executemany(
                    INSERT, batch)
                batch.clear()
                if count % 100000 == 0:
                    log(f"  {count} aeronaves indexadas...")
        if batch:
            conn.executemany(INSERT, batch)

    conn.commit()
    conn.close()
    # Mismo intercambio casi atomico que build(): se construye en .tmp para que
    # una caida a mitad no deje una base incompleta contestando lookups mal.
    try:
        db_path.unlink(missing_ok=True)
        tmp_path.rename(db_path)
    except PermissionError:
        # Caso REAL y no hipotetico: en Windows el webapp mantiene abierta la
        # conexion a esta base (lookup la cachea) y el archivo no se puede
        # reemplazar mientras corre. El PermissionError crudo no dice que hacer,
        # y lo que hay que hacer es parar el servidor. El .tmp se conserva para
        # no perder los minutos de importacion.
        raise RuntimeError(
            f"No se pudo reemplazar {db_path}: el archivo esta en uso.\n"
            f"  Para el servidor (webapp/main.py) y volve a correr esto.\n"
            f"  La base nueva quedo lista en {tmp_path}: no hay que reimportar,\n"
            f"  alcanza con renombrarla cuando el archivo se libere.") from None
    return count


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
            # Las claves se normalizan y los valores salen de _valor(), igual que
            # en build_desde_archivo. Antes este camino leia row.get("...") con
            # los nombres de UN solo export escritos a mano: funcionaba para el
            # de data-samples y devolvia None en todo lo demas, que es como se
            # importa un archivo entero sin un solo error y con las columnas
            # vacias. ALIAS ya contempla las dos formas.
            fila = {_normalizar(k): v for k, v in row.items() if k}
            icao24 = (_valor(fila, "icao24") or "").lower()
            if not icao24:
                continue
            batch.append(tuple(icao24 if c == "icao24" else _valor(fila, c)
                               for c in COLUMNAS))
            count += 1
            if len(batch) >= 5000:
                conn.executemany(
                    INSERT, batch)
                batch.clear()
                if count % 50000 == 0:
                    log(f"  {count} aeronaves indexadas...")
        if batch:
            conn.executemany(INSERT, batch)

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


@_lru_cache(maxsize=8192)
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
    parser.add_argument("--desde", metavar="CSV",
                        help="Construir desde un CSV ya bajado (el export COMPLETO de "
                             "OpenSky: mucho mejor cobertura, ver build_desde_archivo)")
    parser.add_argument("--lookup", metavar="ICAO24", help="Probar una consulta")
    args = parser.parse_args()

    if args.desde:
        build_desde_archivo(args.desde)
    elif args.build:
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
