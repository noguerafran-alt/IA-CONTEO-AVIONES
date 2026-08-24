"""Publicar lo grabado a una carpeta compartida, para verlo desde otra PC.

POR QUE NO ALCANZA CON PONER LA BASE EN ONEDRIVE. Es lo primero que uno intenta
y es exactamente lo que no hay que hacer. La base abre en WAL, asi que en todo
momento son TRES archivos:

    adsb_log.db        lo que ya se consolido
    adsb_log.db-wal    lo recien escrito, todavia no integrado
    adsb_log.db-shm    el indice de bloqueos entre procesos

OneDrive no sabe que los tres son UN objeto: sube cada uno cuando cambia, por
separado. Si sincroniza el .db sin el -wal que le corresponde, la copia que baja
la otra PC no queda visiblemente incompleta -queda CORRUPTA, y lo dice recien
cuando alguien la lee-. Y el -shm es el mecanismo con el que SQLite evita que
dos procesos se pisen: no cruza la red, asi que con dos PC abriendo la misma
base las dos creen tener el candado.

QUE HACE ESTE MODULO. Se graba local, y se publica a la carpeta compartida una
copia QUIETA: un solo archivo consolidado que nadie tiene abierto. Ese es el
unico uso de una carpeta sincronizada que es seguro.

La copia se hace con la API de backup de SQLite y no copiando el archivo. Con
WAL activo hay filas que viven solo en el -wal, y una copia de archivo no las
ve: saldria incompleta sin avisar. La API las integra.

SE ESCRIBE A UN TEMPORAL Y RECIEN AHI SE RENOMBRA. Escribir directo sobre el
destino deja una ventana de segundos donde el archivo esta a medias, y OneDrive
sincroniza justo eso. El renombrado dentro de la misma carpeta es atomico: la
otra PC ve la copia anterior completa, o la nueva completa, nunca una a medias.

Uso:
  python publicar_datos.py                 publicar una vez
  python publicar_datos.py --cada 300      republicar cada 5 minutos
  python publicar_datos.py --destino RUTA  a otra carpeta
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from adsb_record import DB_PATH

# La carpeta compartida sale del entorno (configuracion.bat la fija) y NO se
# escribe aca: en otra PC el OneDrive cuelga de otro usuario, y una ruta fija
# publicaria a un lugar que ahi no existe.
DESTINO_DEFAULT = Path(os.environ.get("ADSB_COMPARTIDO")
                       or (Path(os.environ.get("OneDrive", Path.home())) / "ADSB-AEROPARQUE"))

NOMBRE_BASE = "adsb_log.db"
NOMBRE_CSV = "adsb_datos.csv"
NOMBRE_ESTADO = "estado.json"


def _consolidar(origen: Path, destino: Path) -> int:
    """Copiar la base con la API de backup y devolver cuantas filas quedaron.

    El conteo se lee del DESTINO y no del origen a proposito: es lo que permite
    afirmar que la copia sirve. Contar el origen y suponer que la copia salio
    igual es como se publica una base corrupta creyendo que esta completa.
    """
    temporal = destino.with_suffix(destino.suffix + ".parcial")
    if temporal.exists():
        temporal.unlink()

    # El origen se abre en SOLO LECTURA: publicar no puede ser una operacion con
    # capacidad de tocar la base que esta grabando en ese momento.
    fuente = sqlite3.connect(f"file:{origen}?mode=ro", uri=True)
    copia = sqlite3.connect(temporal)
    try:
        fuente.backup(copia)
    finally:
        copia.close()
        fuente.close()

    revision = sqlite3.connect(temporal)
    try:
        estado = revision.execute("PRAGMA integrity_check").fetchone()[0]
        if estado != "ok":
            raise RuntimeError(f"la copia salio corrupta (integrity_check: {estado})")
        filas = revision.execute("SELECT COUNT(*) FROM adsb_log").fetchone()[0]
        # Sin WAL en la copia publicada: el destino es un volcado para leer, y
        # dejarlo en WAL reintroduce los archivos sueltos que este modulo existe
        # para evitar.
        revision.execute("PRAGMA journal_mode=delete")
    finally:
        revision.close()

    # Renombrado atomico. replace() pisa el destino si ya estaba, en una sola
    # operacion del sistema de archivos: no hay instante en que el archivo falte
    # ni este a medias.
    temporal.replace(destino)
    return filas


def _exportar_csv(base: Path, destino: Path) -> int:
    """Volcar la base a CSV, que se abre desde cualquier lado sin instalar nada.

    Existe porque la otra PC puede no tener el sistema instalado: el CSV se abre
    con Excel y no necesita ni Python ni el repo. utf-8-sig y no utf-8 pelado
    porque Excel en Windows sin BOM interpreta el archivo como ANSI y rompe
    cualquier acento.
    """
    conexion = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
    conexion.row_factory = sqlite3.Row
    temporal = destino.with_suffix(".parcial")
    filas = 0
    try:
        cursor = conexion.execute("SELECT * FROM adsb_log ORDER BY epoch")
        with open(temporal, "w", newline="", encoding="utf-8-sig") as salida:
            escritor = None
            for fila in cursor:
                if escritor is None:
                    escritor = csv.DictWriter(salida, fieldnames=fila.keys())
                    escritor.writeheader()
                escritor.writerow(dict(fila))
                filas += 1
    finally:
        conexion.close()
    temporal.replace(destino)
    return filas


def _estado(base: Path, filas: int) -> dict:
    """El manifiesto: que hay en esta copia y desde donde se midio.

    Sin esto la otra PC ve una base y no puede saber si es de hace cinco minutos
    o de hace tres dias, ni desde donde se midio -y una distancia no significa
    nada sin saber desde donde se cuenta-. Es el mismo problema que hubo que
    resolver en las paginas con la franja del receptor.
    """
    import receiver

    conexion = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
    try:
        primero, ultimo, aeronaves = conexion.execute(
            "SELECT MIN(epoch), MAX(epoch), COUNT(DISTINCT icao24) FROM adsb_log"
        ).fetchone()
    finally:
        conexion.close()

    def cuando(marca):
        if not marca:
            return None
        return datetime.fromtimestamp(marca, timezone.utc).astimezone().isoformat()

    return {
        "publicado": datetime.now().astimezone().isoformat(),
        "filas": filas,
        "aeronaves": aeronaves,
        "primer_dato": cuando(primero),
        "ultimo_dato": cuando(ultimo),
        "receptor": {
            "nombre": receiver.RECEIVER_NAME,
            "lat": receiver.RECEIVER_LAT,
            "lon": receiver.RECEIVER_LON,
            "antena_m": receiver.ANTENA_M,
            "por_defecto": receiver.RECEIVER_ES_DEFAULT,
        },
        "origen": os.environ.get("COMPUTERNAME") or "desconocido",
    }


def publicar(origen: Path | None = None, destino: Path | None = None, log=print) -> dict:
    """Publicar una vez. Devuelve el manifiesto de lo que quedo."""
    origen = Path(origen or DB_PATH)
    destino = Path(destino or DESTINO_DEFAULT)

    if not origen.exists():
        raise FileNotFoundError(f"no existe la base {origen}")
    destino.mkdir(parents=True, exist_ok=True)

    filas = _consolidar(origen, destino / NOMBRE_BASE)
    filas_csv = _exportar_csv(destino / NOMBRE_BASE, destino / NOMBRE_CSV)
    estado = _estado(destino / NOMBRE_BASE, filas)
    (destino / NOMBRE_ESTADO).write_text(
        json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")

    if estado["receptor"]["por_defecto"]:
        # Se avisa al publicar y no solo en las paginas: lo que se publique con
        # la ubicacion por defecto se lo lleva la otra PC, y alla el error ya no
        # se puede notar mirando la antena.
        log("  ATENCION: nadie configuro donde esta la antena. Lo publicado dice "
            f"que se midio desde {estado['receptor']['nombre']}, que es el valor por defecto.")

    log(f"  publicado en {destino}")
    log(f"  {filas} filas, {estado['aeronaves']} aeronaves, CSV con {filas_csv}")
    return estado


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--destino", type=Path, default=None,
                        help=f"carpeta compartida (por defecto {DESTINO_DEFAULT})")
    parser.add_argument("--origen", type=Path, default=None,
                        help=f"base a publicar (por defecto {DB_PATH})")
    parser.add_argument("--cada", type=float, default=None, metavar="SEGUNDOS",
                        help="republicar cada tantos segundos en vez de una sola vez")
    args = parser.parse_args()

    if args.cada is None:
        publicar(args.origen, args.destino)
        raise SystemExit(0)

    print(f"Publicando cada {args.cada:.0f} s. Ctrl+C para cortar.\n")
    while True:
        try:
            print(datetime.now().strftime("%H:%M:%S"))
            publicar(args.origen, args.destino)
        except KeyboardInterrupt:
            print("\ncortado")
            break
        except Exception as exc:
            # Un fallo al publicar no puede cortar el ciclo: la carpeta puede
            # quedar un rato sin responder mientras OneDrive la toca, y eso se
            # resuelve solo en la vuelta siguiente.
            print(f"  no se pudo publicar: {type(exc).__name__}: {exc}")
        try:
            time.sleep(args.cada)
        except KeyboardInterrupt:
            print("\ncortado")
            break
