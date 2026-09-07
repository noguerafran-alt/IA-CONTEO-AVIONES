"""Cruzar lo que la antena detecto contra lo que Aeropuertos Argentina publica.

ES LA UNICA FORMA DE SABER SI EL SISTEMA ANDA. Todo lo demas que mide este
proyecto lo mide contra si mismo, y ya se demostro que eso no alcanza: el bug
del distintivo hacia que 16 de 41 operaciones tuvieran el numero de vuelo de la
pierna SIGUIENTE, y el sistema era perfectamente consistente consigo mismo
mientras estaba equivocado. Lo destapo la comparacion contra los listados
oficiales, y ninguna comprobacion interna lo habria encontrado.

SE CRUZA POR HORA, NO POR NUMERO DE VUELO. No es una preferencia: emparejar por
numero da por buena justamente la columna que se quiere auditar. Si el numero
esta mal -y estuvo mal- un cruce por numero no empareja nada y parece que el
sistema no detecto el vuelo, cuando en realidad lo detecto y le puso otro
nombre. La hora es independiente del bug.

TOLERANCIA 180 s. El desvio medido entre la hora de la operacion detectada y la
oficial fue de 0,5 min en promedio y 2 min como maximo, asi que 3 min cubre lo
observado con margen sin empezar a emparejar vuelos distintos: en Aeroparque las
operaciones se separan por varios minutos.

EL DENOMINADOR EXCLUYE EL TIEMPO APAGADO, y eso es lo que hace que el numero se
pueda publicar. CLAUDE.md tiene una regla explicita al respecto porque ya se
publicaron dos porcentajes equivocados: el 03/09 la antena se prendio y apago
muchas veces probando ganancias, y contar contra TODAS las operaciones del dia
mide cuantas veces se apago, no lo que la antena recibe. Una operacion oficial
que ocurrio con el grabador caido NO es una perdida del sistema, y se cuenta
aparte -- nunca se descarta en silencio.

Uso:
  python cruce.py                    el cruce sobre todo lo acumulado
  python cruce.py --horas 24         solo las ultimas 24 h
  python cruce.py --detalle          listar acierto por acierto
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Cuanto se acepta de diferencia entre la hora detectada y la oficial. Ver el
# docstring del modulo para el numero medido que lo respalda.
TOLERANCIA_S = float(os.environ.get("ADSB_CRUCE_TOLERANCIA_S", 180.0))

# Que tipo nuestro corresponde a que movimiento oficial. Un despegue nuestro
# solo puede emparejar con una partida oficial: si se permitiera cruzar tipos,
# un aterrizaje detectado a la misma hora que una partida contaria como acierto
# y el numero seria mas alto y mentiroso.
EQUIVALE = {"despegue": "D", "aterrizaje": "A"}


def _utc(e: float | None) -> str | None:
    if not e:
        return None
    return datetime.fromtimestamp(e, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coincide(nuestro: str | None, oficial: str | None) -> bool | None:
    """Si los dos numeros de vuelo son el mismo. None si no se puede comparar.

    Los formatos difieren: la antena escucha "ARG1243" y AA2000 publica
    "AR 1243". El prefijo tampoco es el mismo -- ARG es el codigo OACI de tres
    letras y AR el IATA de dos-- asi que se compara SOLO la parte numerica, que
    es la que identifica la pierna. Comparar los prefijos daria 0% siempre y no
    diria nada del bug que se quiere auditar.
    """
    if not nuestro or not oficial:
        return None
    dn = "".join(c for c in nuestro if c.isdigit())
    do = "".join(c for c in oficial if c.isdigit())
    if not dn or not do:
        return None
    return dn.lstrip("0") == do.lstrip("0")


def intervalos_arriba(conn_adsb: sqlite3.Connection,
                      desde: float | None = None,
                      hasta: float | None = None) -> list[tuple[float, float]]:
    """Cuando estuvo ARRIBA el grabador, como intervalos unidos.

    Devuelve [] si no hay registro de uptime, y eso NO es lo mismo que "estuvo
    caido todo el tiempo": el llamador tiene que distinguirlos, porque con []
    no se puede afirmar cobertura de nada. Ver adsb_uptime.py.
    """
    try:
        import adsb_uptime
    except Exception:
        return []
    try:
        ses = adsb_uptime.sesiones(conn_adsb, desde, hasta)
    except sqlite3.Error:
        return []            # la tabla todavia no existe en esta base
    crudos = sorted((s["arranque"], s["fin"]) for s in ses
                    if s.get("arranque") and s.get("fin"))
    unidos: list[list[float]] = []
    for a, b in crudos:
        if unidos and a <= unidos[-1][1]:
            unidos[-1][1] = max(unidos[-1][1], b)
        else:
            unidos.append([a, b])
    return [(a, b) for a, b in unidos]


def _dentro(e: float, intervalos: list[tuple[float, float]]) -> bool:
    return any(a <= e <= b for a, b in intervalos)


def cruzar(operaciones: list[dict], oficiales: list[dict],
           intervalos: list[tuple[float, float]],
           tolerancia_s: float = TOLERANCIA_S) -> dict:
    """Emparejar por hora. Devuelve aciertos, perdidas y lo que sobra de cada lado.

    `operaciones` son las nuestras: dicts con `tipo` y `timestamp`.
    `oficiales` son las de AA2000 con hora real: dicts con `movimiento` y
    `real_epoch`.

    EL EMPAREJAMIENTO ES UNO A UNO Y POR CERCANIA. Se arman todos los pares
    posibles dentro de la tolerancia, se ordenan por diferencia absoluta y se
    toman de menor a mayor descartando los que usan algo ya emparejado. Es
    codicioso y no optimo, pero es EXPLICABLE: cada acierto se justifica
    diciendo "es el mas cercano que quedaba libre". Un emparejamiento optimo por
    costo total puede mover un par para mejorar la suma, y entonces un acierto
    deja de tener explicacion local.
    """
    nuestras = [o for o in operaciones
                if o.get("tipo") in EQUIVALE and o.get("timestamp")]
    ofi = [o for o in oficiales if o.get("real_epoch") and o.get("movimiento")]

    # Los pares candidatos: mismo movimiento y dentro de la tolerancia.
    pares = []
    for i, n in enumerate(nuestras):
        mov = EQUIVALE[n["tipo"]]
        for j, f in enumerate(ofi):
            if f["movimiento"] != mov:
                continue
            d = abs(n["timestamp"] - f["real_epoch"])
            if d <= tolerancia_s:
                pares.append((d, i, j))
    pares.sort()

    usadas_n: set[int] = set()
    usadas_f: set[int] = set()
    aciertos = []
    for d, i, j in pares:
        if i in usadas_n or j in usadas_f:
            continue
        usadas_n.add(i)
        usadas_f.add(j)
        n, f = nuestras[i], ofi[j]
        aciertos.append({
            "tipo": n["tipo"],
            "nuestra_epoch": n["timestamp"],
            "nuestra_utc": _utc(n["timestamp"]),
            "nuestro_vuelo": n.get("callsign"),
            "nuestra_matricula": n.get("registration"),
            "oficial_epoch": f["real_epoch"],
            "oficial_utc": _utc(f["real_epoch"]),
            "oficial_vuelo": f.get("numero"),
            "oficial_matricula": f.get("matricula"),
            "oficial_destino": f.get("otro_aeropuerto"),
            "pasajeros": f.get("pasajeros"),
            "delta_s": round(n["timestamp"] - f["real_epoch"], 1),
            # El numero coincide o no, y eso NO decide el emparejamiento: se
            # informa como RESULTADO. Es la unica forma de auditar la columna
            # del distintivo sin usarla para emparejar.
            "vuelo_coincide": _coincide(n.get("callsign"), f.get("numero")),
        })

    # Las oficiales que no emparejaron se parten en dos por el uptime, y esa
    # division es todo el punto del modulo.
    perdidas, sin_ventana = [], []
    for j, f in enumerate(ofi):
        if j in usadas_f:
            continue
        fila = {"movimiento": f["movimiento"], "epoch": f["real_epoch"],
                "utc": _utc(f["real_epoch"]), "vuelo": f.get("numero"),
                "matricula": f.get("matricula"),
                "destino": f.get("otro_aeropuerto"),
                "programada": f.get("programada"), "estado": f.get("estado")}
        (perdidas if _dentro(f["real_epoch"], intervalos)
         else sin_ventana).append(fila)

    # Las nuestras sin par oficial. NO son "falsos positivos" sin mas: la
    # ventana del feed oficial es corta y el poller pudo no haber visto ese
    # vuelo nunca. Se cuentan aparte y se dicen por lo que son.
    sobrantes = [{"tipo": n["tipo"], "epoch": n["timestamp"],
                  "utc": _utc(n["timestamp"]), "vuelo": n.get("callsign"),
                  "matricula": n.get("registration")}
                 for i, n in enumerate(nuestras) if i not in usadas_n]

    aciertos.sort(key=lambda a: a["oficial_epoch"], reverse=True)
    for lista in (perdidas, sin_ventana, sobrantes):
        lista.sort(key=lambda x: x["epoch"], reverse=True)

    n_ac, n_pe = len(aciertos), len(perdidas)
    denom = n_ac + n_pe
    por_tipo = {}
    for t, mov in EQUIVALE.items():
        a = sum(1 for x in aciertos if x["tipo"] == t)
        p = sum(1 for x in perdidas if x["movimiento"] == mov)
        por_tipo[t] = {"aciertos": a, "perdidas": p,
                       "cobertura": (a / (a + p)) if (a + p) else None}
    return {
        "aciertos": aciertos, "perdidas": perdidas,
        "sin_ventana": sin_ventana, "sobrantes": sobrantes,
        "n_aciertos": n_ac, "n_perdidas": n_pe,
        "n_sin_ventana": len(sin_ventana), "n_sobrantes": len(sobrantes),
        # None en DOS casos, y el segundo es el que importa:
        #
        #   1. denom == 0: no hay nada contra que comparar. None y no 0.0,
        #      porque un cero ahi afirma que el sistema no detecto nada.
        #   2. SIN INTERVALOS DE UPTIME: aunque haya aciertos, el denominador no
        #      significa nada. Sin saber cuando el grabador estuvo arriba, TODAS
        #      las oficiales sin par se van a "fuera de ventana" sin evidencia, y
        #      entonces perdidas queda en cero y la cobertura sale 100%.
        #
        # El caso 2 lo destapo el test: con 2 aciertos, 2 oficiales sin par y
        # ningun intervalo, este campo devolvia 1.0. La pagina y el CLI ya
        # filtraban por hay_uptime, pero el campo mentia solo, y cualquiera que
        # leyera el JSON se llevaba un 100% inventado. Es exactamente el error
        # que este modulo existe para no repetir.
        "cobertura": (n_ac / denom) if (denom and intervalos) else None,
        "por_tipo": por_tipo,
        "tolerancia_s": tolerancia_s,
        "hay_uptime": bool(intervalos),
        "segundos_arriba": sum(b - a for a, b in intervalos),
        # Cuantos numeros de vuelo coinciden ENTRE los aciertos. Es la auditoria
        # de la columna del distintivo, y por eso el cruce no la usa para
        # emparejar: si la usara, este numero seria 100% por construccion.
        "vuelo_coincide": sum(1 for a in aciertos if a["vuelo_coincide"]),
        "vuelo_comparable": sum(1 for a in aciertos
                                if a["vuelo_coincide"] is not None),
    }


def desde_bases(db_adsb: str, db_oficial: str, horas: float | None = None,
                codigo: str | None = None) -> dict | None:
    """Armar el cruce leyendo las dos bases. None si falta alguna."""
    if not Path(db_adsb).exists() or not Path(db_oficial).exists():
        return None
    import aa2000
    import aeropuerto
    from adsb_events import load_db

    conn_of = aa2000.abrir_lectura(db_oficial)
    if conn_of is None:
        return None
    try:
        oficiales = [o for o in aa2000.operaciones(conn_of, limite=100000)
                     if o.get("real_epoch")]
    finally:
        conn_of.close()
    if not oficiales:
        return {"vacio": "el poller todavia no capturo ninguna hora real"}

    # La ventana se acota a lo que el feed oficial cubre. Sin esto, las
    # operaciones de agosto -- de cuando el poller no existia -- apareceran
    # todas como "sobrantes" y ese numero no diria nada.
    desde = min(o["real_epoch"] for o in oficiales) - TOLERANCIA_S
    hasta = max(o["real_epoch"] for o in oficiales) + TOLERANCIA_S
    if horas:
        import time
        desde = max(desde, time.time() - horas * 3600)
        oficiales = [o for o in oficiales if o["real_epoch"] >= desde]

    obs, _ = load_db(db_adsb)
    inf = aeropuerto.informe(obs, codigo)
    nuestras = []
    if inf is not None:
        for o in inf.operaciones:
            if o.tipo in EQUIVALE and desde <= o.timestamp <= hasta:
                nuestras.append({"tipo": o.tipo, "timestamp": o.timestamp,
                                 "callsign": o.callsign,
                                 "registration": o.registration})

    conn_ad = sqlite3.connect(f"file:{Path(db_adsb).as_posix()}?mode=ro", uri=True)
    conn_ad.row_factory = sqlite3.Row
    try:
        intervalos = intervalos_arriba(conn_ad, desde, hasta)
    finally:
        conn_ad.close()

    r = cruzar(nuestras, oficiales, intervalos)
    r["ventana_desde"], r["ventana_hasta"] = _utc(desde), _utc(hasta)
    r["aeropuerto"] = (inf.codigo if inf else None)
    r["n_nuestras"] = len(nuestras)
    return r


def _informe(r: dict | None, detalle: bool = False) -> None:
    if r is None:
        print("falta alguna de las dos bases")
        return
    if r.get("vacio"):
        print(r["vacio"])
        return
    print(f"\nCruce ADS-B vs AA2000 ({r['aeropuerto']})")
    print(f"  ventana            {r['ventana_desde']}  ..  {r['ventana_hasta']}")
    print(f"  tolerancia         +/- {r['tolerancia_s']:.0f} s")
    print(f"  operaciones nuestras en esa ventana: {r['n_nuestras']}")
    print()
    print(f"  ACIERTOS           {r['n_aciertos']}")
    print(f"  PERDIDAS           {r['n_perdidas']}   (oficiales con el grabador ARRIBA)")
    print(f"  fuera de ventana   {r['n_sin_ventana']}   (grabador caido: NO son perdidas)")
    print(f"  nuestras sin par   {r['n_sobrantes']}")
    print()
    pc = r["cobertura"]
    if not r["hay_uptime"]:
        print("  SIN REGISTRO DE UPTIME: no se puede afirmar cobertura, porque no")
        print("  hay forma de saber si una oficial sin par ocurrio con el grabador")
        print("  apagado. Todas las que no emparejaron cayeron en 'fuera de ventana'.")
    elif pc is None:
        print("  cobertura          sin datos para calcularla")
    else:
        print(f"  COBERTURA          {pc * 100:.1f}%   "
              f"({r['n_aciertos']} de {r['n_aciertos'] + r['n_perdidas']})")
        for t, d in r["por_tipo"].items():
            c = d["cobertura"]
            print(f"    {t:12}     " + ("sin datos" if c is None else
                  f"{c * 100:5.1f}%   ({d['aciertos']} de {d['aciertos'] + d['perdidas']})"))
    if r["vuelo_comparable"]:
        print(f"\n  numero de vuelo coincide en {r['vuelo_coincide']} de "
              f"{r['vuelo_comparable']} aciertos comparables")
    if detalle:
        print("\n  --- aciertos ---")
        for a in r["aciertos"][:40]:
            marca = "" if a["vuelo_coincide"] is None else (
                "" if a["vuelo_coincide"] else "   <-- NUMERO DISTINTO")
            print(f"    {a['tipo']:11} {a['oficial_utc']}  "
                  f"{a['oficial_vuelo'] or '-':9} vs {a['nuestro_vuelo'] or '-':9} "
                  f"{a['delta_s']:+7.1f}s{marca}")
        print("\n  --- perdidas (con el grabador arriba) ---")
        for p in r["perdidas"][:40]:
            print(f"    {p['movimiento']}  {p['utc']}  {p['vuelo'] or '-':9} "
                  f"{p['matricula'] or '-':8} {p['destino'] or '-'}")


def main() -> int:
    import aa2000
    import adsb_record
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--horas", type=float, default=None,
                   help="acotar a las ultimas N horas")
    p.add_argument("--detalle", action="store_true", help="listar los pares")
    p.add_argument("--aeropuerto", default=None)
    p.add_argument("--db", default=str(adsb_record.DB_PATH))
    p.add_argument("--oficial", default=str(aa2000.DB_PATH))
    a = p.parse_args()
    r = desde_bases(a.db, a.oficial, a.horas, a.aeropuerto)
    _informe(r, a.detalle)
    return 0 if r and not r.get("vacio") else 1


if __name__ == "__main__":
    sys.exit(main())
