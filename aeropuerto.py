"""Contar las operaciones de UN aeropuerto, no de todo lo que pasa por la antena.

El problema que resuelve: la antena escucha todo lo que vuela cerca, y la
mayoria no aterriza ni despega de ningun lado -pasa de largo a diez mil metros
camino a otra parte-. Para contestar "que aviones aterrizan y despegan de
Aeroparque" hay que atribuir cada operacion a un aeropuerto, y el detector de
eventos de adsb_events.py no puede: mira solo altitud y regimen vertical, asi que
sabe que ALGO aterrizo pero no donde.

COMO SE ATRIBUYE, y por que no por transicion a tierra. Lo natural seria contar
un aterrizaje cuando la altitud llega a cero cerca del aeropuerto. Medido sobre
lo grabado desde San Isidro, eso no funciona: de 1413 posiciones, CERO estan por
debajo de 1500 ft y CERO a menos de 3 km de Aeroparque. Lo mas bajo que se ve
son 2325 ft a 5 km. No es el horizonte teorico -a 2000 ft da 114 km de alcance-
sino obstruccion real: trece kilometros de ciudad entre la antena y la pista, y
un avion a 1000 ft a esa distancia esta a 1.3 grados sobre el horizonte, que lo
tapa cualquier edificio o arbolado.

Asi que se atribuye por GEOMETRIA de la trayectoria: se define un cilindro sobre
el aeropuerto (un radio y un techo) y se mira que hizo la aeronave adentro. Si
venia bajando, es una aproximacion; si venia subiendo, una salida. Eso funciona
con datos de aproximacion y despegue aunque nunca se vea el toque, que es la
situacion real de una antena que no esta pegada a la pista.

Y se informa la ALTITUD MINIMA vista adentro del cilindro, siempre. Es el numero
que dice si lo que se esta contando son operaciones o sobrevuelos: una minima de
2300 ft significa que no se vio la fase final y que estas son aproximaciones
detectadas, no aterrizajes confirmados. Sin ese numero al lado, un conteo de
operaciones es una cifra que nadie puede auditar.

El rumbo ayuda cuando esta: una aeronave alineada con la pista (dentro de unos
grados de su rumbo verdadero, ver geografia.py) esta operando ahi y no pasando
por arriba. Cuando no hay rumbo no se descarta nada -seria tirar datos buenos-
pero la operacion queda marcada como sin confirmar por alineacion.

Configuracion:
  ADSB_AIRPORT=SABE           el aeropuerto a contar (vacio = todos)
  ADSB_AIRPORT_RADIUS_KM=8    radio del cilindro
  ADSB_AIRPORT_CEILING_FT=4000  techo del cilindro
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from math import cos, radians

from adsb import Observation

# Radio y techo del cilindro de operaciones. Los defaults no son redondos por
# gusto: 8 km cubre la aproximacion final tipica de un aeropuerto urbano sin
# empezar a morder el circuito de San Fernando, que esta a 20 km de Aeroparque;
# y 4000 ft esta por encima de cualquier tramo final pero por debajo del
# transito que cruza la zona en ruta, que va a 10000 ft o mas.
RADIO_KM = float(os.environ.get("ADSB_AIRPORT_RADIUS_KM", 8.0))
TECHO_FT = float(os.environ.get("ADSB_AIRPORT_CEILING_FT", 4000.0))

# Cuantos grados de diferencia con el rumbo de la pista se aceptan para decir
# que la aeronave estaba alineada. 30 es generoso a proposito: en el tramo
# final antes de alinearse el avion viene en diagonal, y un margen estrecho
# descartaria aproximaciones reales por no mirar el instante exacto.
TOLERANCIA_RUMBO_DEG = 30.0

# Cuanta altitud tiene que haber perdido o ganado dentro del cilindro para
# llamarlo aproximacion o salida. 300 ft filtra el ruido de la altitud
# barometrica -que oscila decenas de pies- sin exigir un descenso completo.
CAMBIO_MINIMO_FT = 300.0


# Aeroparque por defecto porque es el aeropuerto que este proyecto existe para
# contar. Se cambia con ADSB_AIRPORT, y ADSB_AIRPORT=NINGUNO apaga la seccion.
AEROPUERTO_DEFAULT = "SABE"


def objetivo() -> str | None:
    """El codigo del aeropuerto a contar, o None si se apago la seccion."""
    codigo = (os.environ.get("ADSB_AIRPORT") or AEROPUERTO_DEFAULT).strip().upper()
    return None if codigo in ("", "NINGUNO", "NONE", "NO") else codigo


@dataclass
class Operacion:
    """Una aproximacion o salida atribuida a un aeropuerto."""
    icao24: str
    tipo: str                      # 'aproximacion' | 'salida'
    timestamp: float               # el instante mas bajo dentro del cilindro
    callsign: str | None = None
    registration: str | None = None
    aircraft_type: str | None = None
    min_altitude_ft: float | None = None
    min_distance_km: float | None = None
    track_deg: float | None = None
    pista: str | None = None       # la pista con la que quedo alineada, si alguna
    alineada: bool = False
    posiciones: int = 0

    @property
    def confirmada(self) -> bool:
        """Si se puede afirmar que opero aca, y no solo que paso cerca y bajo.

        Alineada con una pista Y por debajo de 2000 ft. Las dos condiciones
        juntas porque cada una sola deja pasar casos obvios: un avion alineado a
        3500 ft todavia puede estar cruzando, y uno a 1000 ft sin alineacion
        puede venir de otro aeropuerto cercano.
        """
        return self.alineada and (self.min_altitude_ft is not None
                                  and self.min_altitude_ft <= 2000.0)


@dataclass
class Informe:
    """Las operaciones de un aeropuerto, con lo que hace falta para auditarlas."""
    codigo: str
    nombre: str
    lat: float
    lon: float
    radio_km: float
    techo_ft: float
    distancia_receptor_km: float | None = None
    horizonte_superficie_km: float | None = None
    ve_la_pista: bool = False
    operaciones: list[Operacion] = field(default_factory=list)
    # El numero que permite juzgar si esto son operaciones o sobrevuelos.
    min_altitude_vista_ft: float | None = None
    posiciones_en_cilindro: int = 0
    aeronaves_en_cilindro: int = 0
    sobrevuelos: int = 0           # entraron al cilindro sin subir ni bajar

    @property
    def aproximaciones(self) -> int:
        return sum(1 for o in self.operaciones if o.tipo == "aproximacion")

    @property
    def salidas(self) -> int:
        return sum(1 for o in self.operaciones if o.tipo == "salida")

    @property
    def confirmadas(self) -> int:
        return sum(1 for o in self.operaciones if o.confirmada)

    @property
    def advertencia(self) -> str | None:
        """Por que estos numeros pueden no ser lo que parecen, en una frase.

        Devuelve None solo cuando no hay nada que aclarar. Es la parte mas
        importante del informe: un conteo de operaciones sin esto invita a
        tratar como medicion algo que puede ser un artefacto de donde esta la
        antena.
        """
        if not self.posiciones_en_cilindro:
            return ("Ninguna posicion decodificada cayo dentro del cilindro de "
                    f"{self.radio_km:.0f} km y {self.techo_ft:.0f} ft sobre "
                    f"{self.codigo}. No es que no haya habido operaciones: es que "
                    "desde donde esta la antena no se reciben.")
        if self.min_altitude_vista_ft is not None and self.min_altitude_vista_ft > 1500:
            return (f"Lo mas bajo que se vio sobre {self.codigo} fueron "
                    f"{self.min_altitude_vista_ft:.0f} ft. La fase final no se "
                    "recibe desde esta ubicacion, asi que estas son aproximaciones "
                    "y salidas detectadas, no aterrizajes y despegues confirmados.")
        if not self.ve_la_pista:
            return (f"La pista de {self.codigo} esta a "
                    f"{self.distancia_receptor_km:.1f} km, mas que el horizonte de "
                    f"radio a un avion en tierra ({self.horizonte_superficie_km:.1f} "
                    "km). Los aviones EN la pista no se escuchan desde aca.")
        return None


def _rumbos_de_pista(codigo: str) -> list[tuple[str, float]]:
    """Los rumbos verdaderos de cada cabecera del aeropuerto.

    Salen de los dos umbrales reales (geografia.py, datos de OurAirports), no de
    la designacion: "13/31" es rumbo magnetico redondeado a diez grados y en
    Buenos Aires la declinacion ronda los -8, asi que deducirlo daria varios
    grados de error justo donde se usa para decidir si una aeronave esta alineada.
    """
    from math import atan2, degrees

    import geografia

    salida = []
    for pista in geografia.PISTAS:
        if pista["apt"] != codigo:
            continue
        for desde, hasta, nombre in ((pista["le"], pista["he"], pista["nombre"]),
                                     (pista["he"], pista["le"],
                                      "/".join(reversed(pista["nombre"].split("/"))))):
            lat_media = radians((desde[0] + hasta[0]) / 2)
            norte = (hasta[0] - desde[0]) * 111.32
            este = (hasta[1] - desde[1]) * 111.32 * cos(lat_media)
            salida.append((nombre, (degrees(atan2(este, norte)) + 360.0) % 360.0))
    return salida


def _alineada(track: float | None, pistas: list[tuple[str, float]]) -> tuple[bool, str | None]:
    """Si el rumbo cae dentro de la tolerancia de alguna cabecera."""
    if track is None or not pistas:
        return False, None
    mejor, mejor_dif = None, 999.0
    for nombre, rumbo in pistas:
        dif = abs(track - rumbo) % 360.0
        dif = min(dif, 360.0 - dif)
        if dif < mejor_dif:
            mejor, mejor_dif = nombre, dif
    return (mejor_dif <= TOLERANCIA_RUMBO_DEG), (mejor if mejor_dif <= TOLERANCIA_RUMBO_DEG else None)


def informe(observations: list[Observation], codigo: str | None = None) -> Informe | None:
    """Atribuir operaciones a un aeropuerto. None si no se puede ubicar el codigo."""
    import aircraft_db
    import receiver

    codigo = (codigo or objetivo() or "").strip().upper()
    if not codigo:
        return None
    try:
        from pyModeS.position._airports import AIRPORTS
        if codigo not in AIRPORTS:
            return None
        apt_lat, apt_lon = AIRPORTS[codigo]
    except Exception:
        return None

    nombres = {"SABE": "Aeroparque Jorge Newbery", "SADF": "San Fernando",
               "SAEZ": "Ezeiza Ministro Pistarini"}
    horizonte = receiver.horizonte_km(0)
    dist_receptor = receiver.distance_km(apt_lat, apt_lon)
    inf = Informe(
        codigo=codigo, nombre=nombres.get(codigo, codigo),
        lat=apt_lat, lon=apt_lon, radio_km=RADIO_KM, techo_ft=TECHO_FT,
        distancia_receptor_km=(round(dist_receptor, 1) if dist_receptor is not None else None),
        horizonte_superficie_km=round(horizonte, 1),
        ve_la_pista=(dist_receptor is not None and dist_receptor <= horizonte),
    )

    pistas = _rumbos_de_pista(codigo)

    # Las posiciones de cada aeronave DENTRO del cilindro, en orden. Se guarda la
    # observacion entera y no solo la distancia: para decidir si bajaba o subia
    # hace falta la altitud de cada punto, no la del conjunto.
    dentro: dict[str, list[tuple[Observation, float]]] = {}
    for o in sorted(observations, key=lambda o: o.timestamp):
        if o.latitude is None or o.longitude is None or o.altitude_ft is None:
            continue
        if o.altitude_ft > TECHO_FT:
            continue
        d = receiver.distance_km(o.latitude, o.longitude, (apt_lat, apt_lon))
        if d is None or d > RADIO_KM:
            continue
        dentro.setdefault(o.icao24, []).append((o, d))

    inf.posiciones_en_cilindro = sum(len(v) for v in dentro.values())
    inf.aeronaves_en_cilindro = len(dentro)
    if dentro:
        inf.min_altitude_vista_ft = min(o.altitude_ft for v in dentro.values() for o, _ in v)

    for icao24, puntos in dentro.items():
        alturas = [o.altitude_ft for o, _ in puntos]
        # Aproximacion o salida segun DONDE cae el punto mas bajo: si esta al
        # final, venia bajando; si esta al principio, se estaba yendo. Comparar
        # solo el primero con el ultimo se equivoca con el avion que toca y
        # vuelve a salir, que tiene el minimo en el medio.
        i_min = alturas.index(min(alturas))
        baja = alturas[0] - alturas[i_min]
        sube = alturas[-1] - alturas[i_min]
        if max(baja, sube) < CAMBIO_MINIMO_FT:
            inf.sobrevuelos += 1
            continue
        tipo = "aproximacion" if baja >= sube else "salida"

        o_min, d_min = puntos[i_min]
        # El rumbo del punto mas bajo si lo trae; si no, el ultimo conocido de
        # los puntos del cilindro. Fuera del cilindro no se busca: el rumbo de
        # crucero no dice nada sobre la alineacion con una pista.
        track = next((o.track_deg for o, _ in reversed(puntos) if o.track_deg is not None), None)
        alineada, pista = _alineada(track, pistas)
        entry = aircraft_db.lookup(icao24) if aircraft_db.available() else None
        inf.operaciones.append(Operacion(
            icao24=icao24, tipo=tipo, timestamp=o_min.timestamp,
            callsign=next((o.callsign.strip() for o, _ in reversed(puntos) if o.callsign), None),
            registration=(entry or {}).get("registration") or None,
            aircraft_type=aircraft_db.describe_type(entry) if entry else None,
            min_altitude_ft=o_min.altitude_ft, min_distance_km=round(d_min, 2),
            track_deg=track, pista=pista, alineada=alineada, posiciones=len(puntos),
        ))

    inf.operaciones.sort(key=lambda o: o.timestamp, reverse=True)
    return inf


def como_json(inf: Informe | None) -> dict | None:
    if inf is None:
        return None
    return {
        "codigo": inf.codigo, "nombre": inf.nombre, "lat": inf.lat, "lon": inf.lon,
        "radio_km": inf.radio_km, "techo_ft": inf.techo_ft,
        "distancia_receptor_km": inf.distancia_receptor_km,
        "horizonte_superficie_km": inf.horizonte_superficie_km,
        "ve_la_pista": inf.ve_la_pista,
        "aproximaciones": inf.aproximaciones, "salidas": inf.salidas,
        "confirmadas": inf.confirmadas, "sobrevuelos": inf.sobrevuelos,
        "min_altitude_vista_ft": inf.min_altitude_vista_ft,
        "posiciones_en_cilindro": inf.posiciones_en_cilindro,
        "aeronaves_en_cilindro": inf.aeronaves_en_cilindro,
        "advertencia": inf.advertencia,
        "operaciones": [
            {"icao24": o.icao24, "tipo": o.tipo, "timestamp": o.timestamp,
             "callsign": o.callsign, "registration": o.registration,
             "aircraft_type": o.aircraft_type,
             "min_altitude_ft": o.min_altitude_ft, "min_distance_km": o.min_distance_km,
             "track_deg": o.track_deg, "pista": o.pista, "alineada": o.alineada,
             "confirmada": o.confirmada, "posiciones": o.posiciones}
            for o in inf.operaciones
        ],
    }


if __name__ == "__main__":
    import argparse

    from adsb_events import load_db

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="adsb_log.db")
    parser.add_argument("--aeropuerto", default=None, help="codigo ICAO, ej SABE")
    args = parser.parse_args()

    datos, _ = load_db(args.db)
    inf = informe(datos, args.aeropuerto)
    if inf is None:
        raise SystemExit("indica un aeropuerto con --aeropuerto o ADSB_AIRPORT (ej SABE)")

    print(f"=== {inf.nombre} ({inf.codigo}) ===")
    print(f"a {inf.distancia_receptor_km} km del receptor; horizonte a un avion en "
          f"tierra: {inf.horizonte_superficie_km} km "
          f"-> {'SE VE la pista' if inf.ve_la_pista else 'pista BAJO el horizonte'}")
    print(f"cilindro: {inf.radio_km:.0f} km de radio, {inf.techo_ft:.0f} ft de techo")
    print(f"  {inf.posiciones_en_cilindro} posiciones de {inf.aeronaves_en_cilindro} aeronaves")
    print(f"  altitud minima vista adentro: "
          f"{inf.min_altitude_vista_ft if inf.min_altitude_vista_ft is not None else '-'} ft")
    print()
    print(f"aproximaciones {inf.aproximaciones} | salidas {inf.salidas} | "
          f"confirmadas por alineacion y altitud {inf.confirmadas} | "
          f"sobrevuelos descartados {inf.sobrevuelos}")
    if inf.advertencia:
        print(f"\n  OJO: {inf.advertencia}")
    if inf.operaciones:
        print()
        print(f"{'TIPO':13s} {'VUELO':9s} {'MATRICULA':10s} {'TIPO AVION':18s} "
              f"{'ALT':>7s} {'DIST':>6s} {'RUMBO':>6s} PISTA")
        print("-" * 92)
        for o in inf.operaciones[:25]:
            print(f"{o.tipo:13s} {(o.callsign or '-'):9s} {(o.registration or '-'):10s} "
                  f"{(o.aircraft_type or '-')[:18]:18s} "
                  f"{o.min_altitude_ft:7.0f} {o.min_distance_km:6.2f} "
                  f"{(f'{o.track_deg:.0f}' if o.track_deg is not None else '-'):>6s} "
                  f"{o.pista or '-'}{'  (confirmada)' if o.confirmada else ''}")
