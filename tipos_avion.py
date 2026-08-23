"""Describir el tipo de avion con el Doc 8643 de la OACI, que es la fuente oficial.

Que agrega sobre el registro de OpenSky, medido sobre las 107 aeronaves con
typecode y 10 o mas mensajes de esta antena:

  27 ganan MODELO que el registro no tenia (A359 y C82S salian vacios).
  106 ganan CANTIDAD Y TIPO DE MOTORES, dato que el registro no trae.
  106 ganan CATEGORIA DE ESTELA, que clasifica el trafico por peso: en estos
      datos 82 medias, 13 pesadas, 10 ligeras.

VA EN UN ARCHIVO SEPARADO Y NO EN LA BASE DEL REGISTRO a proposito. El registro
se reconstruye entero cada vez que se importa un snapshot nuevo de OpenSky
(aircraft_db.build_desde_archivo borra y reemplaza), asi que una tabla de tipos
metida ahi se perderia en silencio en la proxima actualizacion, y el sintoma
seria que los motores y la estela desaparecen sin que nadie toque nada.

EL MODELO DEL DOC 8643 NO PISA AL DEL REGISTRO, solo lo rellena. Es la decision
mas importante de este modulo. El Doc 8643 tiene UN nombre por designador, y
cuando un designador cubre varias variantes le puede tocar la de VIP:

  B39M -> "737 MAX 9 BBJ"      y el registro dice "Boeing 737 MAX 9"
  A359 -> "Prestige (A-350-900)" y el registro dice "Airbus A350-941"

BBJ es Boeing Business Jet y Prestige es la version ejecutiva de Airbus: para un
avion de Copa o de LATAM son nombres equivocados. El registro sabe la variante
del avion CONCRETO; el Doc 8643 sabe la familia del designador. Cuando los dos
hablan, manda el registro.

Los datos son publicos: https://www.icao.int/publications/DOC8643/
Se importan con:
  python tipos_avion.py --importar doc8643AircraftTypes.csv doc8643Manufacturers.csv
"""
from __future__ import annotations

import csv
import io
import sqlite3
from functools import lru_cache
from pathlib import Path

DB_PATH = Path(__file__).parent / "tools" / "aircraft_types.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tipos (
    designador TEXT PRIMARY KEY,
    modelo TEXT,
    fabricante TEXT,
    motores INTEGER,
    tipo_motor TEXT,
    estela TEXT,
    clase TEXT,
    descripcion TEXT
);
"""

# Que significa cada categoria de estela, en palabras. Es un dato operativo real
# -define la separacion minima entre aviones en aproximacion- y sin la
# traduccion una H suelta no dice nada.
ESTELA = {
    "L": "ligera (hasta 7 t)",
    "M": "media (7 a 136 t)",
    "H": "pesada (mas de 136 t)",
    "J": "super (A380)",
    "L/M": "ligera o media segun version",
}


def available() -> bool:
    return DB_PATH.exists()


def importar(csv_tipos: Path | str, csv_fabricantes: Path | str | None = None,
             db_path: Path = DB_PATH, log=print) -> int:
    """Construir la tabla de tipos desde los CSV del Doc 8643."""
    csv_tipos = Path(csv_tipos)
    if not csv_tipos.exists():
        raise FileNotFoundError(f"no se encontro {csv_tipos}")

    # El codigo de fabricante del archivo de tipos a veces ya es un nombre
    # ("328 SUPPORT SERVICES") y a veces una sigla ("AAC"). La tabla de
    # fabricantes expande las siglas y agrega el pais; es opcional porque para
    # aviones comerciales el fabricante ya viene en el registro de OpenSky.
    fabricantes: dict[str, str] = {}
    if csv_fabricantes and Path(csv_fabricantes).exists():
        with io.open(csv_fabricantes, encoding="utf-8", errors="replace", newline="") as fh:
            for fila in csv.DictReader(fh):
                codigo = (fila.get("Code") or "").strip()
                nombre = (fila.get("Name") or "").strip()
                if codigo and nombre:
                    fabricantes[codigo] = nombre

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".sqlite.tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    conn.execute(SCHEMA)

    # Un designador tiene varias variantes -10020 filas para 2640 designadores- y
    # entre ellas estan las ejecutivas. Ni la primera ni la ultima sirve: para
    # B38M el archivo trae "BBJ (737 MAX 8)", "737 MAX 8" y "737 MAX 8 BBJ", o
    # sea que la base esta en el medio. Se elige descartando las que llevan
    # marcador de version VIP y quedandose con la mas corta de las que sobran.
    mejores: dict[str, tuple[int, tuple]] = {}

    def _puntaje(modelo: str) -> int:
        """Menor es mejor. Penaliza las versiones ejecutivas y la verborragia."""
        m = (modelo or "").upper()
        vip = any(marca in m for marca in
                  ("BBJ", "PRESTIGE", " ACJ", "ACJ ", "CORPORATE", "EXECUTIVE", "VIP"))
        return (1000 if vip else 0) + len(m)

    filas = []
    with io.open(csv_tipos, encoding="utf-8", errors="replace", newline="") as fh:
        for fila in csv.DictReader(fh):
            designador = (fila.get("Designator") or "").strip().upper()
            if not designador:
                continue
            codigo_fab = (fila.get("ManufacturerCode") or "").strip()
            try:
                motores = int((fila.get("EngineCount") or "").strip() or 0) or None
            except ValueError:
                motores = None
            modelo = (fila.get("ModelFullName") or "").strip() or None
            registro = (
                designador, modelo,
                fabricantes.get(codigo_fab, codigo_fab) or None,
                motores,
                (fila.get("EngineType") or "").strip() or None,
                (fila.get("WTC") or "").strip() or None,
                (fila.get("AircraftDescription") or "").strip() or None,
                (fila.get("Description") or "").strip() or None,
            )
            p = _puntaje(modelo or "")
            if designador not in mejores or p < mejores[designador][0]:
                mejores[designador] = (p, registro)
    filas = [v[1] for v in mejores.values()]
    conn.executemany("INSERT OR REPLACE INTO tipos VALUES (?,?,?,?,?,?,?,?)", filas)
    conn.commit()
    conn.close()

    try:
        db_path.unlink(missing_ok=True)
        tmp.rename(db_path)
    except PermissionError:
        raise RuntimeError(
            f"No se pudo reemplazar {db_path}: el archivo esta en uso.\n"
            f"  Para el servidor (webapp/main.py) y volve a correr esto.\n"
            f"  La tabla nueva quedo en {tmp}.") from None

    unicos = len({f[0] for f in filas})
    log(f"Listo: {unicos} designadores de tipo en {db_path} ({len(filas)} filas leidas)")
    return unicos


@lru_cache(maxsize=1)
def _conn() -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(DB_PATH.absolute().as_uri() + "?mode=ro",
                               uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


@lru_cache(maxsize=4096)
def buscar(designador: str | None) -> dict | None:
    """Lo que el Doc 8643 sabe de un designador de tipo, o None.

    Cacheado porque la pagina en vivo pregunta lo mismo una vez por aeronave en
    cada refresco, y la tabla no cambia mientras el programa corre.
    """
    if not designador:
        return None
    conn = _conn()
    if conn is None:
        return None
    fila = conn.execute("SELECT * FROM tipos WHERE designador = ?",
                        (designador.strip().upper(),)).fetchone()
    if fila is None:
        return None
    d = dict(fila)
    d["estela_texto"] = ESTELA.get(d.get("estela") or "", d.get("estela"))
    return d


def enriquecer(entrada: dict | None) -> dict:
    """Sumar a una entrada del registro lo que el Doc 8643 agregue.

    El modelo del Doc 8643 entra SOLO si el registro no tiene uno: ver el
    docstring del modulo. Los motores, la estela y la clase son datos nuevos y
    entran siempre, porque el registro no los trae y no hay conflicto posible.
    """
    entrada = dict(entrada or {})
    tipo = buscar(entrada.get("typecode"))
    if not tipo:
        return entrada
    if not (entrada.get("model") or "").strip() and tipo.get("modelo"):
        entrada["model"] = tipo["modelo"]
        entrada["model_source"] = "doc8643"
    entrada["motores"] = tipo.get("motores")
    entrada["tipo_motor"] = tipo.get("tipo_motor")
    entrada["estela"] = tipo.get("estela")
    entrada["estela_texto"] = tipo.get("estela_texto")
    entrada["clase"] = tipo.get("clase")
    return entrada


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--importar", nargs="+", metavar="CSV",
                        help="doc8643AircraftTypes.csv [doc8643Manufacturers.csv]")
    parser.add_argument("--buscar", metavar="DESIGNADOR",
                        help="probar una consulta, por ejemplo A21N")
    args = parser.parse_args()

    if args.importar:
        importar(args.importar[0],
                 args.importar[1] if len(args.importar) > 1 else None)
    elif args.buscar:
        t = buscar(args.buscar)
        if t is None:
            print("No esta." if available()
                  else "La tabla no esta importada. Corre: python tipos_avion.py --importar <csv>")
        else:
            print(f"{t['designador']}: {t['modelo']}")
            print(f"  fabricante : {t['fabricante']}")
            print(f"  motores    : {t['motores']} x {t['tipo_motor']}")
            print(f"  estela     : {t['estela']} ({t['estela_texto']})")
            print(f"  clase      : {t['clase']} / {t['descripcion']}")
    else:
        parser.error("indica --importar o --buscar")
