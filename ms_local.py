"""El market share, solo. Sondea AA2000 y lo muestra, sin nada del radar.

POR QUE APARTE. El lado de AA2000 no toca la antena: no necesita el dongle, ni
pyModeS, ni el cilindro del aeropuerto, ni el registro de uptime. Y el market
share tampoco -- su numerador y su denominador salen los dos de la misma lista
de partidas oficiales.

Meterlo en webapp/main.py funcionaba, pero ese modulo importa adsb_record,
aeropuerto y adsb_service, y arrastra el camino de la camara. Para mirar un share
en la oficina no hace falta levantar el sistema entero, y sobre todo: esto tiene
que poder correr CON EL RADAR APAGADO, en cualquier maquina con internet.

Un proceso, una ventana, un clic:

  - un hilo que sondea AA2000 cada 5 minutos y acumula en su propia base;
  - un servidor que sirve /market-share y /oficial y nada mas.

NO DUPLICA NADA. El sondeo es aa2000.sondear() y la cuenta es
market_share.desde_base(), los mismos que usa el sistema completo. Si esta app
tuviera su propia copia del calculo, un dia darian numeros distintos y nadie
sabria cual creer -- que es justo el problema que este repo ya tuvo con dos
acumuladores del cilindro.

Uso:
  python ms_local.py                 sondea y sirve en 127.0.0.1:8600
  python ms_local.py --puerto 9000
  python ms_local.py --sin-sondeo    solo mira lo ya acumulado
"""
from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import aa2000
import market_share

RAIZ = Path(__file__).parent
PLANTILLAS = RAIZ / "webapp" / "templates"
ESTATICO = RAIZ / "webapp" / "static"

app = FastAPI(title="Market share YPF (local)")
templates = Jinja2Templates(directory=str(PLANTILLAS))
if ESTATICO.exists():
    # Se monta el mismo /static que la app completa: la plantilla pide
    # franja_receptor.js y sin el archivo tira un 404 en la consola. Aca no hay
    # receptor que mostrar -- la API no manda `receptor` -- y la plantilla ya
    # trata eso como opcional, pero el 404 igual ensucia.
    app.mount("/static", StaticFiles(directory=str(ESTATICO)), name="static")

# El estado del sondeo, para poder decir en pantalla cuando fue la ultima vuelta
# y si fallo. Un tablero que no dice cuando se actualizo por ultima vez invita a
# leer un numero viejo como si fuera de ahora.
ESTADO = {"ultimo": None, "proximo": None, "error": None, "vueltas": 0,
          "recibidas": 0, "sondeando": False}


def _sondear_siempre(intervalo: float, aeropuerto: str) -> None:
    """El hilo del poller. Nunca muere por una excepcion.

    Si una vuelta falla -- se cayo internet, la API cambio -- se anota en ESTADO
    y se sigue. Que el hilo muriera en silencio dejaria la pantalla mostrando el
    ultimo dato bueno para siempre, sin decir que dejo de actualizarse.
    """
    conn = aa2000.abrir()
    ESTADO["sondeando"] = True
    while True:
        try:
            t = aa2000.sondear(conn, aeropuerto, log=lambda *a: None)
            ESTADO["error"] = "; ".join(t["errores"])[:300] if t["errores"] else None
            ESTADO["recibidas"] = t["recibidas"]
        except Exception as exc:                     # noqa: BLE001
            ESTADO["error"] = f"{type(exc).__name__}: {exc}"
        ESTADO["ultimo"] = time.time()
        ESTADO["proximo"] = ESTADO["ultimo"] + intervalo
        ESTADO["vueltas"] += 1
        time.sleep(intervalo)


@app.get("/api/market-share")
def api_market_share(horas: float = 0):
    r = market_share.desde_base(str(aa2000.DB_PATH), horas=(horas or None))
    if r is None:
        return JSONResponse({"ms": None, "sondeo": ESTADO,
                             "empty_reason": "todavia no hay base: esperando la "
                                             "primera vuelta del sondeo."})
    return JSONResponse({"ms": r, "sondeo": ESTADO})


@app.get("/api/oficial")
def api_oficial(movimiento: str = "", solo_reales: bool = False):
    conn = aa2000.abrir_lectura()
    if conn is None:
        return JSONResponse({"resumen": None, "vuelos": [], "sondeo": ESTADO,
                             "empty_reason": "todavia no hay base: esperando la "
                                             "primera vuelta del sondeo."})
    try:
        return JSONResponse({
            "resumen": aa2000.resumen(conn),
            "vuelos": aa2000.operaciones(conn, movimiento=(movimiento or None),
                                         solo_reales=bool(solo_reales), limite=600),
            "sondeo": ESTADO,
        })
    finally:
        conn.close()


@app.get("/")
@app.get("/market-share")
def pagina_ms(request: Request):
    return templates.TemplateResponse(request, "market_share.html", {})


@app.get("/oficial")
def pagina_oficial(request: Request):
    return templates.TemplateResponse(request, "oficial.html", {})


def main() -> int:
    import uvicorn
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--puerto", type=int, default=8600)
    p.add_argument("--aeropuerto", default=aa2000.AEROPUERTO)
    p.add_argument("--intervalo", type=float, default=aa2000.INTERVALO_S)
    p.add_argument("--sin-sondeo", action="store_true",
                   help="no sondear: solo mirar lo ya acumulado")
    a = p.parse_args()

    print(f"  base        {aa2000.DB_PATH}")
    print(f"  aeropuerto  {a.aeropuerto}")
    print(f"  lista YPF   {market_share.LISTA_PATH}"
          f"   {'OK' if market_share.LISTA_PATH.exists() else 'FALTA'}")
    if a.sin_sondeo:
        print("  sondeo      APAGADO (--sin-sondeo)")
    else:
        print(f"  sondeo      cada {a.intervalo:.0f} s")
        # daemon: el hilo no tiene que impedir que el proceso cierre con Ctrl+C.
        threading.Thread(target=_sondear_siempre, daemon=True,
                         args=(a.intervalo, a.aeropuerto)).start()
    print(f"\n  Abri  http://127.0.0.1:{a.puerto}/\n")
    uvicorn.run(app, host="127.0.0.1", port=a.puerto, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
