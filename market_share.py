"""Market share de YPF en Aeroparque, sobre las partidas que publica AA2000.

QUE MIDE, Y POR QUE LAS PARTIDAS. El avion carga combustible ANTES de irse, asi
que la operacion que importa para el negocio es el despegue. Y es ademas la mitad
medible: el que despega sube sobre la antena y se ve entero, el que llega viene
bajo y apantallado. Ver CLAUDE.md.

NO DEPENDE DE LA ANTENA, y eso es lo que lo hace publicable hoy. El numerador y
el denominador salen los dos de la MISMA lista de partidas oficiales de AA2000,
asi que ninguno se puede mover sin el otro y la cobertura del ADS-B no entra en
la cuenta. La antena sirve para enriquecer y para auditar la fuente, no para
calcular esto.

Esa es la diferencia con los dos porcentajes que este proyecto ya publico mal:
esos tenian un denominador que dependia de cuando la antena estaba prendida.

DESCONOCIDO NO ES COMPETENCIA. Es la decision central del modulo. Si la lista de
clientes de YPF no dice explicitamente que es COMPLETA, una aerolinea que no
figura en ella puede ser de YPF y no estar anotada. Tratarla como competencia
inflaria la porcion del competidor y bajaria el share de YPF sin evidencia.

Por eso, con una lista no exhaustiva el resultado es un RANGO y no un numero:

    piso  = partidas de clientes YPF / total
    techo = (clientes YPF + sin clasificar) / total

Con "exhaustiva": true el piso y el techo coinciden y recien ahi hay un numero
solo. Publicar el piso como si fuera el share, con la lista incompleta, seria
exactamente el numero lindo y no auditable que CLAUDE.md prohibe.

LA LISTA LA APORTA YPF y vive en ypf_clientes.json, fuera del codigo. Formato:

    {
      "actualizado": "2026-09-07",
      "fuente": "de donde salio, para poder auditarlo despues",
      "exhaustiva": false,
      "aerolineas": ["AR", "WJ"],
      "vuelos": ["AR 1234"],
      "competidores": ["LA"]
    }

`aerolineas` son codigos IATA de dos letras, que es lo que AA2000 publica en
idaerolinea. `vuelos` es para excepciones sueltas -- un vuelo puntual que carga
con otro proveedor -- y pisa lo que diga la aerolinea.

Uso:
  python market_share.py                    sobre todo lo acumulado
  python market_share.py --horas 24         solo las ultimas 24 h
  python market_share.py --detalle          por aerolinea
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LISTA_PATH = Path(os.environ.get("YPF_CLIENTES")
                  or (Path(__file__).parent / "ypf_clientes.json"))


def cargar_lista(ruta: Path | str = LISTA_PATH) -> dict:
    """La lista de clientes de YPF, o una vacia que lo dice.

    Nunca lanza: una lista que falta es un estado NORMAL de este sistema -- la
    aporta YPF y puede no haber llegado -- y tiene que poder mostrarse en
    pantalla como "falta el dato" en vez de romper la pagina.
    """
    ruta = Path(ruta)
    vacia = {"existe": False, "aerolineas": [], "vuelos": [], "competidores": [],
             "exhaustiva": False, "actualizado": None, "fuente": None,
             "error": None}
    if not ruta.exists():
        return vacia
    try:
        d = json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return dict(vacia, error=f"no se pudo leer {ruta.name}: {exc}")

    def mayus(xs):
        return [str(x).strip().upper() for x in (xs or []) if str(x).strip()]

    return {
        "existe": True,
        # Los codigos se normalizan a mayusculas: AA2000 publica "AR" y una
        # planilla puede traer "ar". Comparar sin normalizar daria cero
        # coincidencias y pareceria que ningun vuelo es de YPF.
        "aerolineas": mayus(d.get("aerolineas")),
        "competidores": mayus(d.get("competidores")),
        # Los numeros de vuelo se comparan sin espacios: "AR 1234" y "AR1234"
        # son el mismo vuelo y la planilla puede traer cualquiera de los dos.
        "vuelos": [x.replace(" ", "") for x in mayus(d.get("vuelos"))],
        "exhaustiva": bool(d.get("exhaustiva")),
        "actualizado": d.get("actualizado"),
        "fuente": d.get("fuente"),
        "error": None,
    }


def clasificar(partida: dict, lista: dict) -> str:
    """'ypf' | 'competencia' | 'sin_clasificar' para una partida.

    El numero de vuelo manda sobre la aerolinea: es para las excepciones, un
    vuelo puntual que carga con otro proveedor aunque la aerolinea sea cliente.
    """
    nro = (partida.get("numero") or "").replace(" ", "").upper()
    if nro and nro in lista["vuelos"]:
        return "ypf"
    cod = (partida.get("aerolinea_id") or "").strip().upper()
    if cod and cod in lista["aerolineas"]:
        return "ypf"
    if cod and cod in lista["competidores"]:
        return "competencia"
    # Con la lista declarada COMPLETA, lo que no es cliente es competencia. Sin
    # esa declaracion queda sin clasificar, y el share sale como rango.
    if lista["exhaustiva"]:
        return "competencia"
    return "sin_clasificar"


def _pax(xs: list[dict]) -> dict:
    """Suma de pasajeros, diciendo cuantos vuelos informaron.

    Los que no informan se cuentan APARTE: sumar tratando el faltante como cero
    subestimaria el volumen sin decirlo. Y un cero informado por la fuente
    tampoco es lo mismo que un faltante -- ver aa2000._pasajeros().
    """
    con = [x["pasajeros"] for x in xs
           if x.get("pasajeros") is not None and x["pasajeros"] > 0]
    return {"suma": sum(con), "vuelos_con_dato": len(con), "vuelos": len(xs)}


def calcular(partidas: list[dict], lista: dict) -> dict:
    """El share sobre una lista de partidas ya filtrada por ventana."""
    grupos = {"ypf": [], "competencia": [], "sin_clasificar": []}
    for p in partidas:
        grupos[clasificar(p, lista)].append(p)
    total = len(partidas)
    n_ypf = len(grupos["ypf"])
    n_sin = len(grupos["sin_clasificar"])

    por_aerolinea: dict[str, dict] = {}
    for p in partidas:
        k = (p.get("aerolinea_id") or "??").upper()
        d = por_aerolinea.setdefault(k, {"codigo": k, "nombre": p.get("aerolinea"),
                                         "vuelos": 0, "pasajeros": 0,
                                         "vuelos_con_pax": 0,
                                         "clase": clasificar(p, lista)})
        d["vuelos"] += 1
        if p.get("pasajeros") and p["pasajeros"] > 0:
            d["pasajeros"] += p["pasajeros"]
            d["vuelos_con_pax"] += 1
    ranking = sorted(por_aerolinea.values(), key=lambda d: -d["vuelos"])

    return {
        "total_partidas": total,
        "n_ypf": n_ypf,
        "n_competencia": len(grupos["competencia"]),
        "n_sin_clasificar": n_sin,
        # PISO y TECHO, no un numero, salvo que la lista se declare completa.
        # Ver el docstring: desconocido no es competencia.
        "share_piso": (n_ypf / total) if total else None,
        "share_techo": ((n_ypf + n_sin) / total) if total else None,
        "share_exacto": bool(lista["exhaustiva"] and total),
        "pax_ypf": _pax(grupos["ypf"]),
        "pax_total": _pax(partidas),
        "por_aerolinea": ranking,
        "lista": {k: v for k, v in lista.items() if k != "vuelos"},
        "n_vuelos_en_lista": len(lista["vuelos"]),
    }


def desde_base(db_oficial: str, horas: float | None = None,
               aeropuerto: str = "AEP", lista: dict | None = None) -> dict | None:
    """Leer las partidas con hora real y calcular. None si falta la base."""
    import aa2000
    conn = aa2000.abrir_lectura(db_oficial)
    if conn is None:
        return None
    try:
        filas = aa2000.operaciones(conn, aeropuerto=aeropuerto,
                                   movimiento="D", limite=100000)
    finally:
        conn.close()
    # SOLO las que ocurrieron. Una partida programada que todavia no despego no
    # es una carga de combustible, y meterla en el denominador moveria el share
    # segun la hora del dia en que se mire la pantalla.
    ocurridas = [f for f in filas if f.get("real_epoch")]
    if horas:
        import time
        corte = time.time() - horas * 3600
        ocurridas = [f for f in ocurridas if f["real_epoch"] >= corte]
    r = calcular(ocurridas, lista if lista is not None else cargar_lista())
    r["desde_epoch"] = min((f["real_epoch"] for f in ocurridas), default=None)
    r["hasta_epoch"] = max((f["real_epoch"] for f in ocurridas), default=None)
    r["aeropuerto"] = aeropuerto
    r["programadas_sin_ocurrir"] = len(filas) - len(ocurridas)
    return r


def _informe(r: dict | None, detalle: bool = False) -> None:
    if r is None:
        print("falta la base del poller: corre GRABAR-OFICIAL.bat primero")
        return

    def hora(e):
        return "-" if not e else datetime.fromtimestamp(
            e, timezone.utc).strftime("%d/%m %H:%M")

    li = r["lista"]
    print(f"\nMarket share YPF en {r['aeropuerto']} (partidas ocurridas)")
    print(f"  ventana            {hora(r['desde_epoch'])}  ..  {hora(r['hasta_epoch'])}")
    print(f"  partidas           {r['total_partidas']}"
          f"   (+{r['programadas_sin_ocurrir']} programadas que aun no despegaron)")
    print()
    if not li["existe"]:
        print("  NO HAY LISTA DE CLIENTES DE YPF.")
        print(f"  Sin ella no se puede calcular nada: falta {LISTA_PATH.name}.")
        if li.get("error"):
            print(f"  {li['error']}")
        print("  Ver el docstring de este modulo para el formato.")
    else:
        print(f"  clientes YPF       {r['n_ypf']}")
        print(f"  competencia        {r['n_competencia']}")
        print(f"  sin clasificar     {r['n_sin_clasificar']}")
        print()
        if r["share_piso"] is None:
            print("  sin partidas en la ventana: no hay share que calcular")
        elif r["share_exacto"]:
            print(f"  MARKET SHARE       {r['share_piso'] * 100:.1f}%"
                  f"   ({r['n_ypf']} de {r['total_partidas']})")
        else:
            print(f"  MARKET SHARE       entre {r['share_piso'] * 100:.1f}% y "
                  f"{r['share_techo'] * 100:.1f}%")
            print("  Es un RANGO porque la lista no se declaro completa: las")
            print(f"  {r['n_sin_clasificar']} sin clasificar pueden ser de YPF o no.")
        px, pt = r["pax_ypf"], r["pax_total"]
        print(f"\n  pasajeros YPF      {px['suma']:,}"
              f"   ({px['vuelos_con_dato']} de {px['vuelos']} vuelos informan)")
        print(f"  pasajeros total    {pt['suma']:,}"
              f"   ({pt['vuelos_con_dato']} de {pt['vuelos']} vuelos informan)")
        print(f"\n  lista actualizada  {li['actualizado'] or 'sin fecha'}"
              f"   fuente: {li['fuente'] or 'sin declarar'}")
    if detalle:
        print(f"\n  {'cod':4} {'aerolinea':26} {'vuelos':>6} {'pax':>9}  clase")
        for d in r["por_aerolinea"]:
            print(f"  {d['codigo']:4} {(d['nombre'] or '-')[:26]:26} "
                  f"{d['vuelos']:6} {d['pasajeros']:9,}  {d['clase']}")


def main() -> int:
    import aa2000
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--horas", type=float, default=None)
    p.add_argument("--detalle", action="store_true", help="por aerolinea")
    p.add_argument("--aeropuerto", default="AEP")
    p.add_argument("--oficial", default=str(aa2000.DB_PATH))
    a = p.parse_args()
    r = desde_base(a.oficial, a.horas, a.aeropuerto)
    _informe(r, a.detalle)
    return 0 if r else 1


if __name__ == "__main__":
    sys.exit(main())
