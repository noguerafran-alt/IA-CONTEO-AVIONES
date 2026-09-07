"""Market share de YPF en Aeroparque, sobre las partidas que publica AA2000.

SE MIDE EN VUELOS, NO EN PASAJEROS. El share es cuantas partidas abastece YPF
sobre el total. Los pasajeros NO entran en la cuenta: son un dato de OCUPACION
que alimenta el modelo de consumo de YPF -- que calcula por avion, ruta y
ocupacion -- y ese modelo se conecta aparte. Mezclar ocupacion en el share
mediria otra cosa y ademas mediria mal: solo 51 de 201 partidas informan
pasajeros.

LA RUTA SALE DEL NUMERO DE VUELO, y es lo que el modelo necesita. Medido sobre
457 numeros de vuelo, solo 8 tienen mas de un destino: un numero es en la
practica una ruta fija. La ruta se publica por vuelo y agregada por destino.

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
      "aerolineas": ["AR"],
      "rutas": {"WJ": ["MDZ", "IGR"]},
      "vuelos": ["AR 1234"],
      "no_ypf_vuelos": ["AR 1500"],
      "competidores": ["LA"]
    }

HAY DOS CLASES DE CLIENTE, y por eso hacen falta dos campos:

  `aerolineas`  cliente COMPLETO: todas sus partidas son de YPF.
  `rutas`       cliente PARCIAL: solo esos destinos. El resto de esa aerolinea
                NO es de YPF, y se cuenta como competencia y no como
                desconocido -- que una aerolinea figure en `rutas` significa que
                se conoce su alcance, no que se conoce una parte.

Los codigos de aerolinea son IATA de dos letras (idaerolinea) y los destinos son
IATA de tres (IATAdestorig), los mismos que publica AA2000.

`vuelos` y `no_ypf_vuelos` son las excepciones sueltas y PISAN todo lo demas: un
vuelo puntual que carga con YPF aunque su aerolinea no sea cliente, o al reves.

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
             "rutas": {}, "no_ypf_vuelos": [],
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
        # {aerolinea: [destinos]} -- cliente parcial. Se normaliza todo a
        # mayusculas por el mismo motivo que arriba.
        "rutas": {str(k).strip().upper(): mayus(v)
                  for k, v in (d.get("rutas") or {}).items() if str(k).strip()},
        "no_ypf_vuelos": [x.replace(" ", "")
                          for x in mayus(d.get("no_ypf_vuelos"))],
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
    # Las excepciones por vuelo mandan sobre todo lo demas, en los dos sentidos.
    if nro and nro in lista["vuelos"]:
        return "ypf"
    if nro and nro in lista.get("no_ypf_vuelos", ()):
        return "competencia"
    cod = (partida.get("aerolinea_id") or "").strip().upper()
    if cod and cod in lista["aerolineas"]:
        return "ypf"                       # cliente completo
    # Cliente PARCIAL: solo las rutas listadas. Fuera de esas es competencia y
    # no "desconocido", porque figurar en `rutas` declara el alcance completo de
    # esa aerolinea. Si de una aerolinea se conoce solo una parte, va en
    # `aerolineas` o no va: `rutas` es una afirmacion sobre el resto.
    if cod and cod in lista.get("rutas", {}):
        dst = (partida.get("otro_aeropuerto") or "").strip().upper()
        return "ypf" if dst and dst in lista["rutas"][cod] else "competencia"
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
                                         "ypf": 0, "competencia": 0,
                                         "sin_clasificar": 0})
        # POR CLASE Y NO UNA SOLA: un cliente parcial -- de YPF en algunas rutas
        # y no en otras -- tenia una unica etiqueta y quedaba dibujado como
        # nuestro por completo. Con los tres contadores la cobertura parcial se
        # ve en la fila.
        d[clasificar(p, lista)] += 1
        d["vuelos"] += 1
        if p.get("pasajeros") and p["pasajeros"] > 0:
            d["pasajeros"] += p["pasajeros"]
            d["vuelos_con_pax"] += 1
    for d in por_aerolinea.values():
        # "parcial" es un estado propio y no un detalle: es la diferencia entre
        # "este cliente es nuestro" y "de este cliente tenemos algunas rutas".
        d["clase"] = ("ypf" if d["ypf"] == d["vuelos"] else
                      "competencia" if d["competencia"] == d["vuelos"] else
                      "sin_clasificar" if d["sin_clasificar"] == d["vuelos"] else
                      "parcial")
    ranking = sorted(por_aerolinea.values(), key=lambda d: -d["vuelos"])

    # POR RUTA. El destino sale de otro_aeropuerto, que es IATAdestorig y no
    # `arpt`: `arpt` es el ORIGEN. Ver aa2000._a_fila(), donde ese campo estuvo
    # mal leido y la ruta salia "AEP -> AEP".
    por_ruta: dict[str, dict] = {}
    for p in partidas:
        dst = (p.get("otro_aeropuerto") or "??").upper()
        d = por_ruta.setdefault(dst, {
            "destino": dst, "nombre": p.get("destino_nombre"),
            "vuelos": 0, "ypf": 0, "cuerpos": {}, "aerolineas": set()})
        d["vuelos"] += 1
        if clasificar(p, lista) == "ypf":
            d["ypf"] += 1
        cu = p.get("cuerpo") or "?"
        d["cuerpos"][cu] = d["cuerpos"].get(cu, 0) + 1
        if p.get("aerolinea_id"):
            d["aerolineas"].add(p["aerolinea_id"].upper())
    rutas = sorted(por_ruta.values(), key=lambda d: -d["vuelos"])
    for d in rutas:
        d["aerolineas"] = sorted(d["aerolineas"])

    # POR VUELO, que es la unidad que consume el modelo de YPF: cada numero con
    # su ruta, su cuerpo y su ocupacion cuando la hay.
    por_vuelo = []
    for p in partidas:
        por_vuelo.append({
            "numero": p.get("numero"),
            "aerolinea_id": p.get("aerolinea_id"),
            "ruta": f"{p.get('aeropuerto') or '?'}-{p.get('otro_aeropuerto') or '?'}",
            "destino": p.get("otro_aeropuerto"),
            "destino_nombre": p.get("destino_nombre"),
            "cuerpo": p.get("cuerpo"),
            "matricula": p.get("matricula"),
            # La ocupacion va como dato para el modelo, NO al share. None cuando
            # la fuente no la informa, que es distinto de cero.
            "ocupacion": p.get("pasajeros"),
            "real": p.get("real"),
            "real_epoch": p.get("real_epoch"),
            "clase": clasificar(p, lista),
        })
    por_vuelo.sort(key=lambda x: x["real_epoch"] or 0, reverse=True)

    return {
        "total_partidas": total,
        "n_ypf": n_ypf,
        "n_competencia": len(grupos["competencia"]),
        "n_sin_clasificar": n_sin,
        # PISO y TECHO, no un numero, salvo que la lista se declare completa.
        # Ver el docstring: desconocido no es competencia.
        # None y NO 0.0 sin lista de clientes. Con la lista vacia el piso da
        # 0.0, y un 0.0 leido del JSON afirma que YPF no abastece a NINGUN
        # vuelo -- que es una afirmacion, no la ausencia de una. La pagina ya
        # miraba lista.existe, pero el campo mentia solo y cualquiera que
        # consumiera la API se llevaba un cero inventado. Es el mismo defecto
        # que tuvo cruce.py con la cobertura sin uptime.
        "share_piso": (n_ypf / total) if (total and lista["existe"]) else None,
        "share_techo": (((n_ypf + n_sin) / total)
                        if (total and lista["existe"]) else None),
        "share_exacto": bool(lista["exhaustiva"] and total),
        "pax_ypf": _pax(grupos["ypf"]),
        "pax_total": _pax(partidas),
        "por_aerolinea": ranking,
        "por_ruta": rutas,
        "por_vuelo": por_vuelo[:400],
        "n_rutas": len(rutas),
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
        # La ocupacion NO es parte del share: se informa como dato para el
        # modelo de consumo, con cuantos vuelos la traen.
        pt = r["pax_total"]
        print("")
        print(f"  rutas distintas    {r['n_rutas']}")
        print(f"  ocupacion (dato para el modelo, NO parte del share):")
        print(f"    la informan       {pt['vuelos_con_dato']} de {pt['vuelos']}"
              f" partidas   ({pt['suma']:,} pasajeros)")
        print(f"\n  lista actualizada  {li['actualizado'] or 'sin fecha'}"
              f"   fuente: {li['fuente'] or 'sin declarar'}")
    if detalle:
        print("")
        print("  POR AEROLINEA")
        print(f"  {'cod':4} {'aerolinea':24} {'tot':>4} {'YPF':>4} {'comp':>5} {'?':>3}  clase")
        for d in r["por_aerolinea"]:
            print(f"  {d['codigo']:4} {(d['nombre'] or '-')[:24]:24} "
                  f"{d['vuelos']:4} {d['ypf']:4} {d['competencia']:5} "
                  f"{d['sin_clasificar']:3}  {d['clase']}")
        print("")
        print(f"  POR RUTA  (desde {r['aeropuerto']}, {r['n_rutas']} destinos)")
        print(f"  {'dest':5} {'nombre':20} {'vuelos':>6} {'YPF':>4}  {'cuerpo':11} aerolineas")
        for d in r["por_ruta"][:25]:
            cu = " ".join(f"{k}:{v}" for k, v in sorted(d["cuerpos"].items()))
            print(f"  {d['destino']:5} {(d['nombre'] or '-')[:20]:20} {d['vuelos']:6} "
                  f"{d['ypf']:4}  {cu:11} {','.join(d['aerolineas'])}")
        print("")
        print("  POR VUELO  (la unidad que consume el modelo de YPF)")
        print(f"  {'vuelo':9} {'ruta':10} {'cuerpo':6} {'matricula':9} {'ocup':>5}  clase")
        for v in r["por_vuelo"][:15]:
            oc = "-" if v["ocupacion"] is None else str(v["ocupacion"])
            print(f"  {(v['numero'] or '-'):9} {v['ruta']:10} {(v['cuerpo'] or '-'):6} "
                  f"{(v['matricula'] or '-'):9} {oc:>5}  {v['clase']}")

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
