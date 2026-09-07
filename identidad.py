"""Sacarle a cada vuelo todo lo que se puede saber, diciendo de dónde salió cada cosa.

El objetivo del proyecto es saber que avion aterriza y despega, y para eso hay
tres fuentes de identidad con MUY distinta confianza. Mezclarlas sin decir cual
es cual convierte un dato duro en una adivinanza con cara de dato:

  1. LA RADIO. El ICAO24 esta en el 100% de los mensajes y el distintivo de
     vuelo en el 3% (medido sobre 12096 mensajes de esta antena). Los dos los
     transmite el avion: no hay nada que suponer.

  2. EL REGISTRO. La matricula, el tipo y el operador NO viajan por radio: salen
     de buscar el ICAO24 en el registro de OpenSky. Es tan duro como la radio
     cuando la direccion esta ahi, y no existe cuando no: el 47% del trafico
     real de esta antena no esta en ese snapshot -Copa, parte de JetSMART,
     bloques recien asignados-.

  3. LA INFERENCIA, que es lo que agrega este modulo, y va SIEMPRE marcada.
     Recupera dos cosas distintas con dos niveles de confianza distintos.

AEROLINEA DESDE EL DISTINTIVO -- solido. Los tres primeros caracteres de un
distintivo comercial son el designador ICAO del operador: JES3104 es JetSMART.
Recupera 94 de las 106 aeronaves que no resuelven matricula. La tabla no se
inventa: se deriva del propio registro de OpenSky, que trae operator_icao en
41403 filas y da 1458 designadores.

MATRICULA DESDE EL DISTINTIVO -- probable, y con dos frenos medidos. La
aviacion general usa su matricula COMO distintivo: LVHCQ es LV-HCQ. Validado
contra los casos donde el registro sabe la verdad: 6 casos, 4 aciertos.

  Los dos fallos ensenan como hacerlo seguro:
    a01de4 decia LVKCV y era N1064B. El bloque a0 es EEUU y LV es Argentina:
      INCOHERENTE, y el chequeo de pais lo caza.
    a88552 decia N680XP y era N6480G. Los dos son matriculas norteamericanas
      validas y distintas: el chequeo de pais NO lo caza.
  De ahi las dos reglas: el bloque del ICAO24 tiene que concordar con el pais de
  la matricula, y las N- norteamericanas se excluyen porque ahi el distintivo
  con frecuencia no es la matricula. Con eso quedan 4 de 4 en el patron LV.
  Muestra chica: n=4. Se informa como probable, nunca como confirmada.

UN VUELO PUEDE APARECER BAJO VARIAS DIRECCIONES, y eso hay que resolverlo o se
cuenta el mismo avion dos veces. Medido: de 190 distintivos, 18 aparecen bajo
mas de una direccion, con 33 direcciones secundarias. Ocho estan a UN BIT de la
fuerte -o sea la misma direccion con un bit dado vuelta, como e0b198 y e8b198- y
el resto mas lejos. Pero lo que vale para todas es el desbalance: la secundaria
tiene 1 a 5 mensajes y la real 130 a 238. Por eso se resuelve por dominancia y
no por distancia de bits, que solo explicaria 8 de 33.
"""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DB_REGISTRO = Path(__file__).parent / "tools" / "aircraft_db.sqlite"

# Un distintivo comercial es tres letras y despues algo con numeros. Los de
# aviacion general no tienen ese numero pegado a las tres letras.
RE_COMERCIAL = re.compile(r"^([A-Z]{3})\d")

# Matriculas usadas como distintivo, por pais. Las N- norteamericanas quedan
# AFUERA a proposito: se midio que ahi el distintivo con frecuencia no es la
# matricula (a88552 decia N680XP y era N6480G).
RE_MATRICULA = re.compile(r"^(LV|LQ|CC|CP|PP|PR|PS|PT|HC|HK|OB|YV|ZP)([A-Z]{3})$")

# Bloques de direcciones ICAO24 por pais, para el chequeo de coherencia. Solo
# los de la region: fuera de estos no se infiere matricula y punto.
BLOQUES = {
    "e0": ("LV", "LQ"), "e1": ("LV", "LQ"), "e2": ("LV", "LQ"),
    "e4": ("PP", "PR", "PS", "PT"), "e7": ("PP", "PR", "PS", "PT"),
    "e8": ("CC",), "e9": ("ZP",), "e5": ("CP",),
    "e6": ("OB",), "0c": ("HP",), "0d": ("XA", "XB", "XC"),
}

# Designadores que el registro de OpenSky no trae y son de esta zona. Solo van
# los que se pueden afirmar; el resto se muestra con su codigo crudo, que es
# mas honesto que un nombre inventado.
AEROLINEAS_LOCALES = {
    "ARG": "Aerolíneas Argentinas",
    "AEP": "Aeropuerto (código no comercial)",
}

# Cuantas veces mas mensajes tiene que tener una direccion para considerarla la
# dueña del vuelo. 3 es holgado: lo medido son secundarias de 1 a 5 mensajes
# contra fuertes de 130 a 238, o sea factores de 26 a 238.
FACTOR_DOMINANCIA = 3


@lru_cache(maxsize=1)
def _tabla_aerolineas() -> dict[str, str]:
    """Designador ICAO -> nombre del operador, derivado del registro de OpenSky.

    Se arma una sola vez y se cachea: consultar medio millon de filas por avion
    en cada refresco de la pagina en vivo no tiene sentido, y la tabla no cambia
    mientras el programa corre.

    Sale del registro y no de una lista escrita a mano porque una lista a mano
    de 1458 operadores seria imposible de mantener y de auditar. Los pocos que
    el registro no trae van en AEROLINEAS_LOCALES.
    """
    tabla = dict(AEROLINEAS_LOCALES)
    if not DB_REGISTRO.exists():
        return tabla
    try:
        conn = sqlite3.connect(DB_REGISTRO.absolute().as_uri() + "?mode=ro", uri=True)
    except Exception:
        return tabla
    try:
        # Se queda con el nombre MAS FRECUENTE por designador: el registro tiene
        # variantes de escritura del mismo operador, y el mas repetido es el
        # canonico. Los locales se cargaron antes y no se pisan.
        filas = conn.execute("""
            SELECT operator_icao, operator, COUNT(*) n FROM aircraft
             WHERE operator_icao IS NOT NULL AND operator_icao != ''
               AND operator IS NOT NULL AND operator != ''
             GROUP BY operator_icao, operator
        """).fetchall()
    except Exception:
        return tabla
    finally:
        conn.close()

    mejor: dict[str, tuple[str, int]] = {}
    for codigo, nombre, n in filas:
        codigo = (codigo or "").strip().upper()
        if len(codigo) != 3 or not codigo.isalpha():
            continue
        if codigo not in mejor or n > mejor[codigo][1]:
            mejor[codigo] = (nombre.strip(), n)
    for codigo, (nombre, _) in mejor.items():
        tabla.setdefault(codigo, nombre)
    return tabla


def aerolinea_de_distintivo(callsign: str | None) -> tuple[str | None, str | None]:
    """(nombre del operador, designador) a partir del distintivo de vuelo.

    Devuelve el designador incluso cuando no hay nombre: "JES" ya dice mas que
    nada, y mostrar el codigo crudo es mas honesto que inventarle un nombre.
    """
    if not callsign:
        return None, None
    m = RE_COMERCIAL.match(callsign.strip().upper())
    if not m:
        return None, None
    codigo = m.group(1)
    return _tabla_aerolineas().get(codigo), codigo


def matricula_de_distintivo(callsign: str | None, icao24: str | None) -> str | None:
    """Matricula PROBABLE cuando el distintivo es la matricula, o None.

    Exige que el bloque de la direccion concuerde con el pais de la matricula.
    Sin ese chequeo, a01de4 (bloque a0, EEUU) con distintivo LVKCV habria dado
    "LV-KCV" cuando el avion es N1064B.
    """
    if not callsign or not icao24:
        return None
    m = RE_MATRICULA.match(callsign.strip().upper())
    if not m:
        return None
    prefijo = m.group(1)
    esperados = BLOQUES.get(icao24[:2].lower())
    if not esperados or prefijo not in esperados:
        return None
    return f"{prefijo}-{m.group(2)}"


@dataclass
class Identidad:
    """Todo lo que se sabe de una aeronave, con la procedencia de cada dato.

    La procedencia no es un adorno: "matricula LV-KEJ (registro)" y "matricula
    LV-KDI (probable, del distintivo)" son afirmaciones de fuerza muy distinta, y
    presentarlas iguales hace que la buena pierda credibilidad junto con la
    dudosa.
    """
    icao24: str
    callsign: str | None = None
    registration: str | None = None
    registration_source: str | None = None      # 'registro' | 'distintivo (probable)'
    aircraft_type: str | None = None
    # El numero de serie del FUSELAJE, del registro de OpenSky. No lleva
    # procedencia como la matricula porque no se puede inferir de ninguna otra
    # cosa: o esta en el registro o no esta, y nunca se deduce.
    #
    # Vale la pena aunque no llegue a todos porque es MAS ESTABLE que la
    # matricula: la matricula cambia de dueno y hasta de pais, el numero de serie
    # no cambia nunca. Es lo que permite decir si el LV-XXX de hoy es el mismo
    # avion de la semana pasada. Medido el 2026-09-06 sobre las 58 aeronaves que
    # aterrizaron o despegaron de Aeroparque: lo tienen 31, o sea el 53%.
    serial_number: str | None = None
    operator: str | None = None
    operator_source: str | None = None          # 'registro' | 'distintivo'
    operator_code: str | None = None
    # Del Doc 8643 de la OACI (tipos_avion.py). Son datos que el registro de
    # OpenSky no trae, asi que no hay conflicto posible: se suman siempre.
    motores: int | None = None
    tipo_motor: str | None = None
    estela: str | None = None            # L / M / H / J
    estela_texto: str | None = None      # "media (7 a 136 t)"
    messages: int = 0
    # Direcciones que trajeron este mismo distintivo con muchos menos mensajes.
    # Se informan en vez de borrarse: son la huella de tramas con la direccion
    # corrupta, y esconderlas seria esconder que el conteo se dedujo.
    alias: list[str] = field(default_factory=list)

    @property
    def identificada(self) -> bool:
        return bool(self.registration or self.callsign)

    @property
    def confirmada(self) -> bool:
        """Si la matricula viene del registro y no de una inferencia."""
        return self.registration_source == "registro"


def resolver(observaciones: list, icao24: str | None = None) -> dict[str, Identidad]:
    """Una Identidad por direccion, resolviendo los vuelos duplicados.

    Recorre las observaciones una vez para juntar distintivo y conteo por
    direccion, y despues resuelve por dominancia: si un mismo distintivo aparece
    bajo varias direcciones y una tiene al menos FACTOR_DOMINANCIA veces mas
    mensajes que otra, la chica se marca como alias de la grande y no cuenta
    como aeronave aparte.

    Por dominancia y no por distancia de bits porque los bits solo explican 8 de
    33 casos medidos: hay secundarias a 9, 11 y 15 bits de la fuerte, y el
    desbalance de mensajes las delata a todas.
    """
    conteo: dict[str, int] = {}
    distintivo: dict[str, str] = {}
    for o in observaciones:
        conteo[o.icao24] = conteo.get(o.icao24, 0) + 1
        if o.callsign:
            distintivo[o.icao24] = o.callsign.strip()
    return resolver_desde_conteo(conteo, distintivo, icao24)


def resolver_desde_resumen(por_icao: dict, icao24: str | None = None) -> dict[str, Identidad]:
    """Igual que resolver() pero desde un resumen ya agregado por direccion.

    Existe por el mismo motivo que informe_desde_resumen en aeropuerto.py: el
    camino incremental no tiene las observaciones crudas a mano, tiene el resumen
    acumulado, y volver a leer la base para esto anularia la razon de ser del
    cursor. Lo unico que la resolucion necesita de cada direccion es su
    distintivo y su cantidad de mensajes, y eso el resumen ya lo trae.
    """
    conteo = {d: (r.get("n") or 0) for d, r in por_icao.items()}
    distintivo = {d: r["callsign"].strip() for d, r in por_icao.items()
                  if r.get("callsign")}
    return resolver_desde_conteo(conteo, distintivo, icao24)


def resolver_desde_conteo(conteo: dict[str, int], distintivo: dict[str, str],
                          icao24: str | None = None) -> dict[str, Identidad]:
    """El nucleo compartido: de (mensajes por direccion, distintivo) a Identidad."""
    import aircraft_db

    # Agrupar direcciones por distintivo para encontrar los duplicados.
    por_distintivo: dict[str, list[str]] = {}
    for direccion, cs in distintivo.items():
        por_distintivo.setdefault(cs, []).append(direccion)

    alias_de: dict[str, str] = {}     # direccion secundaria -> direccion dueña
    for cs, direcciones in por_distintivo.items():
        if len(direcciones) < 2:
            continue
        fuerte = max(direcciones, key=lambda d: conteo.get(d, 0))
        n_fuerte = conteo.get(fuerte, 0)
        for d in direcciones:
            if d != fuerte and n_fuerte >= max(1, conteo.get(d, 0)) * FACTOR_DOMINANCIA:
                alias_de[d] = fuerte

    salida: dict[str, Identidad] = {}
    for direccion, n in conteo.items():
        if direccion in alias_de:
            continue      # no es una aeronave: es la misma con la direccion fallada
        cs = distintivo.get(direccion)
        ident = Identidad(icao24=direccion, callsign=cs, messages=n,
                          alias=[d for d, dueña in alias_de.items() if dueña == direccion])

        entrada = aircraft_db.lookup(direccion) if aircraft_db.available() else None
        # El Doc 8643 rellena el modelo cuando el registro no lo tiene -27 de 107
        # aeronaves con typecode en estos datos- y agrega motores y categoria de
        # estela, que el registro no trae. Nunca pisa el modelo del registro: el
        # Doc 8643 tiene un nombre por designador y puede tocarle la version
        # ejecutiva, que para un avion de linea es un nombre equivocado.
        if entrada:
            try:
                import tipos_avion
                entrada = tipos_avion.enriquecer(entrada)
            except Exception:
                pass
        if entrada:
            ident.registration = entrada.get("registration") or None
            ident.serial_number = entrada.get("serial_number") or None
            ident.aircraft_type = aircraft_db.describe_type(entrada)
            ident.operator = (entrada.get("operator") or entrada.get("owner") or None)
            if ident.registration:
                ident.registration_source = "registro"
            if ident.operator:
                ident.operator_source = "registro"
            ident.motores = entrada.get("motores")
            ident.tipo_motor = entrada.get("tipo_motor")
            ident.estela = entrada.get("estela")
            ident.estela_texto = entrada.get("estela_texto")

        # La inferencia SOLO rellena lo que el registro dejo vacio. Nunca pisa un
        # dato duro con uno probable.
        if not ident.registration:
            probable = matricula_de_distintivo(cs, direccion)
            if probable:
                ident.registration = probable
                ident.registration_source = "distintivo (probable)"
        if not ident.operator:
            nombre, codigo = aerolinea_de_distintivo(cs)
            ident.operator_code = codigo
            if nombre or codigo:
                ident.operator = nombre or codigo
                ident.operator_source = "distintivo"

        salida[direccion] = ident

    if icao24:
        uno = salida.get(icao24)
        return {icao24: uno} if uno else {}
    return salida


def como_json(ident: Identidad) -> dict:
    return {
        "icao24": ident.icao24, "callsign": ident.callsign,
        "registration": ident.registration,
        "registration_source": ident.registration_source,
        "aircraft_type": ident.aircraft_type,
        "operator": ident.operator, "operator_source": ident.operator_source,
        "operator_code": ident.operator_code,
        "motores": ident.motores, "tipo_motor": ident.tipo_motor,
        "estela": ident.estela, "estela_texto": ident.estela_texto,
        "messages": ident.messages, "alias": ident.alias,
        "confirmada": ident.confirmada,
    }


if __name__ == "__main__":
    import argparse

    from adsb_events import load_db

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("ADSB_DB", "adsb_log.db"))
    parser.add_argument("--solo-inferidas", action="store_true",
                        help="mostrar solo las que ganaron algo por inferencia")
    args = parser.parse_args()

    datos, _ = load_db(args.db)
    ident = resolver(datos)
    filas = sorted(ident.values(), key=lambda i: -i.messages)
    if args.solo_inferidas:
        filas = [i for i in filas
                 if i.registration_source == "distintivo (probable)"
                 or i.operator_source == "distintivo"]

    total = len(ident)
    con_reg = sum(1 for i in ident.values() if i.registration_source == "registro")
    con_prob = sum(1 for i in ident.values() if i.registration_source == "distintivo (probable)")
    con_op_reg = sum(1 for i in ident.values() if i.operator_source == "registro")
    con_op_cs = sum(1 for i in ident.values() if i.operator_source == "distintivo")
    alias = sum(len(i.alias) for i in ident.values())

    print(f"=== {args.db}: {total} aeronaves (ya resueltos los vuelos duplicados)")
    print(f"  matricula del registro          : {con_reg}")
    print(f"  matricula PROBABLE del distintivo: {con_prob}")
    print(f"  operador del registro           : {con_op_reg}")
    print(f"  operador del distintivo         : {con_op_cs}")
    print(f"  direcciones fusionadas como alias: {alias}")
    print()
    print(f"{'ICAO24':8s} {'VUELO':9s} {'MATRICULA':11s} {'FUENTE':22s} "
          f"{'OPERADOR':26s} {'FUENTE':12s} MSJS")
    print("-" * 108)
    for i in filas[:40]:
        print(f"{i.icao24:8s} {(i.callsign or '-'):9s} {(i.registration or '-'):11s} "
              f"{(i.registration_source or '-'):22s} "
              f"{(i.operator or '-')[:26]:26s} {(i.operator_source or '-'):12s} {i.messages}")
