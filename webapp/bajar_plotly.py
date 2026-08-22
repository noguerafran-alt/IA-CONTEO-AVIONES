"""Bajar plotly.min.js al disco, una sola vez.

El mapa lo carga desde /static y no desde un CDN a proposito: este sistema
tiene que andar sin internet, y una pagina en blanco porque no llego un script
de un tercero no sirve para algo que corre al lado de una pista. Es el mismo
criterio con que el repo trata rtl_adsb.exe y la base de OpenSky: se bajan una
vez, quedan en .gitignore y no viajan en el repositorio.

Se usa el bundle "basic" y no el completo: trae scatter, que es todo lo que el
mapa necesita, y pesa 1.0 MB contra 4.3 MB. La version esta clavada para que la
pagina no cambie de comportamiento sola.

Uso:
  python webapp/bajar_plotly.py
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

VERSION = "2.35.2"
URL = f"https://cdn.plot.ly/plotly-basic-{VERSION}.min.js"
DESTINO = Path(__file__).parent / "static" / "plotly.min.js"
# Piso de tamano para no dejar un HTML de error guardado como si fuera la
# libreria: un 404 de un CDN pesa unos pocos kilobytes y se veria como exito.
MINIMO_BYTES = 500_000


def main() -> int:
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    if DESTINO.exists() and DESTINO.stat().st_size >= MINIMO_BYTES:
        print(f"ya esta: {DESTINO} ({DESTINO.stat().st_size // 1024} KB)")
        return 0
    print(f"bajando {URL}")
    with urllib.request.urlopen(URL, timeout=60) as respuesta:
        datos = respuesta.read()
    if len(datos) < MINIMO_BYTES:
        print(f"  lo que llego pesa {len(datos)} bytes, muy poco para ser plotly.")
        print("  No se guarda: seria dejar una pagina de error en lugar de la libreria.")
        return 1
    DESTINO.write_bytes(datos)
    print(f"guardado en {DESTINO} ({len(datos) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
