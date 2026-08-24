"""Leer el manifiesto de los datos publicados: de cuando son y desde donde.

Lo usa VER-DATOS-COMPARTIDOS.bat en la PC que solo mira. Esta en un modulo y no
embebido en el .bat porque un one-liner de Python dentro de un .bat necesita
escapar comillas, parentesis y signos de porcentaje a la vez, y ese escapado se
rompe al editarlo sin que nadie lo note hasta que corre.

DOS COSAS QUE HACE, Y POR QUE LAS DOS IMPORTAN:

  --exportar   Vuelca la ubicacion del receptor a un .bat que el lanzador llama.
               La ubicacion tiene que salir de la PC QUE GRABO, no de la que
               mira: si la que mira usara su propio preset, mostraria distancias
               medidas desde un lugar donde nunca hubo una antena. Es el mismo
               error que costo nueve horas de mediciones el 23/08, pero cruzando
               de maquina, donde nadie puede notarlo mirando por la ventana.

  sin flags    Imprime de cuando son los datos. Esa es la diferencia entre esta
               pantalla y la de la PC que graba: aca se ve una FOTO, y una foto
               sin fecha se lee como el estado actual.
"""
from __future__ import annotations

import json
from pathlib import Path


def _momento(texto: str | None) -> str:
    """'2026-08-24T09:52:41.499-03:00' -> '2026-08-24 09:52:41'."""
    if not texto:
        return "-"
    return texto[:19].replace("T", " ")


def describir(estado: dict) -> list[str]:
    """Las lineas que ve quien abre los datos compartidos."""
    receptor = estado.get("receptor") or {}
    lineas = [
        f"     publicado   : {_momento(estado.get('publicado'))}",
        f"     ultimo dato : {_momento(estado.get('ultimo_dato'))}",
        f"     {estado.get('filas', 0)} filas, {estado.get('aeronaves', 0)} aeronaves",
    ]
    lugar = receptor.get("nombre", "desconocido")
    if receptor.get("por_defecto"):
        # No se puede arreglar desde aca -hay que corregirlo en la PC que graba-
        # pero callarlo seria peor: quien mira estaria leyendo distancias
        # referidas a un lugar que nadie eligio, sin manera de sospecharlo.
        lugar += "   <-- POR DEFECTO, nadie lo configuro alla"
    lineas.append(f"     medido desde: {lugar}")
    lineas.append(f"     grabado en  : {estado.get('origen', '?')}")
    return lineas


def exportar_receptor(estado: dict, destino: Path) -> bool:
    """Escribir un .bat con la ubicacion del receptor que uso quien grabo."""
    receptor = estado.get("receptor") or {}
    lat, lon = receptor.get("lat"), receptor.get("lon")
    if lat is None or lon is None:
        return False
    # Como par lat,lon y no como la clave del preset: la clave podria no existir
    # en esta copia del repo si la otra PC quedo en otra version, y un preset que
    # no resuelve cae en San Isidro en silencio. El par siempre significa lo
    # mismo. receiver.py acepta las dos formas.
    lineas = [f"set ADSB_RECEIVER={lat},{lon}"]
    if receptor.get("nombre"):
        lineas.append(f"set ADSB_RECEIVER_NAME={receptor['nombre']}")
    if receptor.get("antena_m") is not None:
        lineas.append(f"set ADSB_ANTENNA_M={receptor['antena_m']}")
    # ascii con errors='replace': el nombre puede traer acentos y un .bat los
    # lee en la codepage de la consola, que no es UTF-8. Un nombre con un signo
    # de pregunta se entiende igual; un .bat ilegible no arranca.
    destino.write_text("\n".join(lineas) + "\n", encoding="ascii", errors="replace")
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("estado", type=Path, help="ruta de estado.json")
    parser.add_argument("--exportar", type=Path, default=None,
                        help="escribir un .bat con la ubicacion del receptor")
    args = parser.parse_args()

    try:
        datos = json.loads(args.estado.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"     no se pudo leer {args.estado.name}: {exc}")
        raise SystemExit(1)

    if args.exportar:
        raise SystemExit(0 if exportar_receptor(datos, args.exportar) else 1)

    for linea in describir(datos):
        print(linea)
