"""Donde esta la antena. Un solo lugar, porque dos consumidores la necesitan.

La posicion del receptor se usa para dos cosas distintas y si cada una tuviera
su propia copia terminarian midiendo desde puntos diferentes:

  1. Decodificar posiciones en SUPERFICIE (BDS 0,6 / TC 5-8). El CPR de
     superficie codifica la posicion dentro de una zona de 90 grados y hace
     falta un punto cercano para elegir cual. La tolerancia medida barriendo
     la referencia es +-0.75 deg de latitud (83.5 km, = d_lat/2 con
     d_lat=90/60) y +-0.918 deg de longitud (84.3 km a esta latitud): los
     45 NM de DO-260B A.1.7.6. NO son 45 km.
  2. Medir a que distancia se recibio cada aeronave, que es la otra mitad de
     la pregunta de cobertura (la primera, "hasta que altura baja lo que
     escucho", ya la contestaba coverage_report).

Por que la referencia es la ANTENA y no un aeropuerto: con 45 NM de margen un
solo punto en San Isidro decodifica los tres aeropuertos de la zona a la vez.
Medido con haversine_m sobre las coordenadas de la tabla de pyModeS:

    San Fernando (SADF)    7.3 km
    Aeroparque   (SABE)   13.3 km
    Ezeiza       (SAEZ)   39.1 km

Elegir surface_ref='SADF' compila igual y es peor: descarta las otras dos sin
necesidad. Ezeiza queda a 39 km, o sea con ~44 km de margen todavia.

OJO: la antena esta en San Isidro, zona norte del Gran Buenos Aires. El repo
nombra Aeroparque en varios lugares pero eso es el material de VIDEO
(data/aeroparque_full.mp4), no la ubicacion del receptor.
"""
from __future__ import annotations

import math
import os

from match_adsb import haversine_m

# Centro de San Isidro. No hace falta afinar la coordenada al techo: con
# +-83 km de tolerancia cualquier punto del GBA decodifica lo mismo. La
# precision si importa para la distancia medida, que es un numero que se
# publica en el reporte de cobertura.
# Ubicaciones conocidas del receptor, con su altura de antena. Existen como
# tabla y no como comentario porque este proyecto tiene dos lugares reales y
# concretos, y escribir las coordenadas de memoria cada vez es exactamente como
# se termina midiendo el alcance desde el lugar equivocado.
#
# La ALTURA no es un detalle decorativo: manda mas que la cercania para ver
# aviones EN PISTA. El horizonte de radio a un blanco en el suelo es 13.0 km
# con la antena a 10 m y 52.2 km a 160 m, asi que desde la torre se ve la
# superficie de Aeroparque, San Fernando y Ezeiza, y desde un techo en San
# Isidro no se ve ni la de Aeroparque, que queda a 13.3 km: 300 metros mas
# lejos que el horizonte. Por eso nunca se decodifico una posicion en tierra.
# El caso de 'aeroparque' es el opuesto al de la torre: no gana por altura sino
# por cercania, y por eso es el unico donde la altura casi no importa. Esta a
# 1153 m del umbral 13 y a 1154 m del punto mas cercano del eje de pista
# (medido contra los umbrales reales de OurAirports que ya trae geografia.py).
# Con la antena a 1 m el horizonte al suelo ya son 4.1 km, o sea 3.6 veces la
# distancia a la pista: la conclusion "la pista se ve" no depende de acertarle
# a la altura, que es justo lo contrario de lo que pasa en San Isidro, donde
# 13.3 km contra 13.0 km de horizonte hacia que 300 metros decidieran todo.
# Los 3.0 m son una suposicion de armado portatil y conviene pisarlos con la
# altura real via ADSB_ANTENNA_M, pero para la pregunta de si se ven aviones en
# tierra da igual: cualquier valor >=1 alcanza.
#
# El angulo de elevacion es el otro numero que se da vuelta. Un avion a 1000 ft
# sobre la pista se ve a 1.31 grados desde San Isidro -lo tapa cualquier
# edificio, y por eso el minimo visto ahi fueron 2134 ft- y a ~15 grados desde
# aca. La obstruccion urbana deja de ser el limite.
UBICACIONES: dict[str, tuple[float, float, float, str]] = {
    "san-isidro": (-34.4708, -58.5128, 10.0, "San Isidro"),
    "ypf": (-34.605378, -58.362517, 160.0, "Torre YPF (Puerto Madero)"),
    "aeroparque": (-34.551378, -58.437306, 3.0, "Aeroparque (1,15 km de la pista)"),
    # LA UBICACION ACTUAL, desde el 2026-09-03 a la tarde. La antena se corrio
    # 2235 m respecto de 'aeroparque' y quedo AL COSTADO DE LA PISTA, a mitad de
    # campo: 266 m del eje, 1178 m del umbral 13 y 990 m del 31 (medido contra
    # los umbrales reales de OurAirports que trae geografia.py). Es la mejor
    # posicion que tuvo el proyecto.
    #
    # Se agrega como preset NUEVO en vez de corregir 'aeroparque' a proposito:
    # el historico se grabo desde aquel punto y la base todavia no guarda desde
    # donde se recibio cada fila (ver "Ideas que quedaron sin hacer"). Pisar el
    # preset haria que todas las filas viejas se midieran desde aca, que es un
    # error silencioso de 2,2 km sobre datos que ya no se pueden regrabar.
    #
    # Esta DETRAS DE UN DOBLE VIDRIO, o sea adentro. El vidrio atenua en 1090
    # MHz, pero a esta distancia sobra: medido el 2026-09-03, mediana -25.4 dBFS
    # y pico -2.7 sobre 1855 mensajes, con el 12.2% contra el techo del receptor.
    # La atenuacion del vidrio no es el limite; si algun dia lo fuera, se veria
    # como una mediana mucho mas baja, no como menos aeronaves.
    #
    # La altura sigue sin importar para la pregunta de si se ve la pista: con la
    # antena a 1 m el horizonte al suelo ya son 4.1 km, 15 veces los 266 m al
    # eje. Los 3.0 m son la misma suposicion de armado portatil que en el preset
    # anterior y se pisan con ADSB_ANTENNA_M si alguien la mide con cinta.
    "aeroparque-pista": (-34.56167115035011, -58.416347685490514, 3.0,
                         "Aeroparque (266 m del eje de pista)"),
}
UBICACION_DEFAULT = "san-isidro"


def _resolver_receptor() -> tuple[float, float, str]:
    """De donde escucha la antena: ADSB_RECEIVER, o San Isidro por defecto.

    Acepta una clave de UBICACIONES ('ypf'), un par 'lat,lon', o un codigo ICAO
    de aeropuerto. Que esto sea configurable no es lujo: mover la antena 20 km
    cambia TODAS las distancias, los anillos del mapa y el alcance informado, y
    con las coordenadas clavadas en el codigo la pagina seguiria midiendo desde
    el lugar viejo sin decir nada. Un sistema que mide mal en silencio es peor
    que uno que no mide.
    """
    crudo = (os.environ.get("ADSB_RECEIVER") or "").strip()
    if not crudo:
        lat, lon, _, nombre = UBICACIONES[UBICACION_DEFAULT]
        return lat, lon, nombre
    clave = crudo.lower().replace("_", "-")
    if clave in UBICACIONES:
        lat, lon, _, nombre = UBICACIONES[clave]
        return lat, lon, nombre
    # El par lat,lon se parsea aca mismo en vez de reusar parse_surface_ref:
    # esta funcion corre a nivel de modulo, antes de que parse_surface_ref exista
    # mas abajo, y llamarla desde aca daba NameError justo en el caso para el que
    # la variable se agrego. Verificado antes de arreglarlo.
    if "," in crudo:
        try:
            lat_txt, lon_txt = crudo.split(",", 1)
            lat, lon = float(lat_txt), float(lon_txt)
        except ValueError:
            lat = lon = None
        if lat is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            nombre = os.environ.get("ADSB_RECEIVER_NAME") or f"{lat:.4f}, {lon:.4f}"
            return lat, lon, nombre
    else:
        try:
            from pyModeS.position._airports import AIRPORTS
            codigo = crudo.upper()
            if codigo in AIRPORTS:
                lat, lon = AIRPORTS[codigo]
                return lat, lon, os.environ.get("ADSB_RECEIVER_NAME") or codigo
        except Exception:
            pass
    raise ValueError(
        f"ADSB_RECEIVER={crudo!r} no se entiende. Usa una clave conocida "
        f"({', '.join(sorted(UBICACIONES))}), un par 'lat,lon', o un codigo "
        f"ICAO de aeropuerto en mayusculas.")


RECEIVER_LAT, RECEIVER_LON, RECEIVER_NAME = _resolver_receptor()
# True si nadie movio la antena por configuracion. El mapa lo usa para avisar
# cuando lo que se esta midiendo NO es la ubicacion por defecto.
RECEIVER_ES_DEFAULT = not (os.environ.get("ADSB_RECEIVER") or "").strip()

DEFAULT_SURFACE_REF: tuple[float, float] = (RECEIVER_LAT, RECEIVER_LON)

# Si la referencia queda mas lejos que esto del aeropuerto mas cercano del
# mundo (la tabla de pyModeS trae 4904), es un error de tipeo y no una
# ubicacion real: nadie pone un receptor de ADS-B en el medio del oceano.
# Atrapa el caso que ninguna validacion de rango puede atrapar -- invertir
# lat/lon: (-58.5128, -34.4708) es un par perfectamente valido que decodifica
# en silencio a 3246 km del lugar correcto, y queda a 1682 km de EGYP, el
# aeropuerto mas cercano. Medido.
MAX_KM_TO_ANY_AIRPORT = 300.0

# Hasta que distancia del receptor se ANALIZA el trafico. Ojo: esto no es un
# filtro de correccion, es un recorte de alcance elegido. El filtro fisico
# (horizonte_km / limite_posicion_km) rechaza posiciones IMPOSIBLES y no se
# negocia; este numero decide de que porcion del cielo se quiere hablar.
#
# Mezclar los dos seria un error caro: la antena demostrablemente recibe hasta
# 72.4 km (medido), asi que un recorte a 50 km presentado como "alcance maximo"
# haria que el sistema mienta en la direccion contraria. Por eso lo que queda
# afuera se cuenta y se muestra aparte, nunca se descarta en silencio.
RADIO_ANALISIS_KM = float(os.environ.get("ADSB_ANALYSIS_KM", 50.0))

# Altura de la antena sobre el suelo. Casi no pesa y por eso no vale la pena
# medirla con cinta: con la antena a 0 m en vez de 10 m el horizonte del caso
# fantasma de e0b14a (19525 ft) baja de 331.2 a 318.1 km y la violacion sube de
# 2.38x a 2.48x. La conclusion no depende de este numero. Medido.
# La altura sale del preset de la ubicacion y no de un 10.0 fijo: es el numero
# que decide si se ven los aviones EN PISTA (13.0 km de horizonte a 10 m contra
# 52.2 km a 160 m), asi que mover la antena a la torre y dejar la altura vieja
# haria subestimar el alcance de superficie por cuatro. ADSB_ANTENNA_M lo pisa
# si hay que medirlo con cinta.
_ALTURA_PRESET = UBICACIONES.get(
    (os.environ.get("ADSB_RECEIVER") or UBICACION_DEFAULT).strip().lower().replace("_", "-"),
    (0.0, 0.0, 10.0, ""))[2]
ANTENA_M = float(os.environ.get("ADSB_ANTENNA_M", _ALTURA_PRESET))

# Cuanto mas alla del horizonte 4/3 se sigue aceptando una posicion.
#
# El horizonte NO es una pared: el ducting troposferico mete 1090 MHz bastante
# mas lejos, y esta pagina existe justamente para medir recepciones
# excepcionales, asi que un corte pegado al horizonte estaria descartando lo
# que se quiere ver. El 1.35 no es un numero elegido, es el que se puede citar:
# la imagen sdcard de PiAware corta a 360 NM = 666.7 km, y el horizonte 4/3 de
# un avion a 45000 ft es 496.0 km, o sea 1.344x. El paquete dump1090-fa corta a
# 300 NM, que serian 1.12x.
#
# Medido sobre las 783 posiciones de la base: cualquier factor entre 1.0 y 2.38
# rechaza exactamente la misma unica posicion (e0b14a a 789.5 km) y ningun
# legitimo, o sea dos ordenes de holgura. Con esa holgura conviene el que se
# puede citar y no el que apenas alcanza.
# 1.35 y no 3.0: la tabla de limite_posicion_km documenta x1.35 y estaba
# calculada con ese valor (331.2 km de horizonte a 19525 ft -> 447.1 km de
# limite), pero la constante decia 3.0, que da 993 km. Con 993 km de margen el
# fantasma de e0b14a -a 789.5 km con el avion a 19525 ft- pasaba el chequeo de
# horizonte y lo terminaba cazando el de velocidad, que necesita una posicion
# previa con la que comparar: si el fantasma hubiese sido la PRIMERA posicion
# de la aeronave, no lo agarraba nadie. El horizonte no necesita historia.
# Las recepciones record reales por ducting troposferico llegan a ~1.5x el
# horizonte optico, no a 3x.
MARGEN_DUCTING = 1.35

# Techo absoluto cuando la posicion viene SIN altitud y el avion no esta en
# tierra: sin altitud no hay horizonte que calcular. 300 NM es el default de
# fabrica de dump1090-fa (Modes.maxRange = 1852 * 300 en modesInitConfig), no
# un umbral nuestro. Hoy no se ejercita -- las 783 filas con posicion traen las
# 783 su altitud -- pero el esquema permite NULL y las fuentes json y sbs
# pueden traer posicion sin altitud.
TECHO_SIN_ALTITUD_KM = 555.6

# Media celda CPR de superficie: mas alla de esto una posicion en tierra no es
# una posicion mala, es una posicion sin sentido. Ver el punto 1 del docstring:
# la tolerancia de la referencia de superficie es +-0.75 deg de latitud
# (83.5 km) = d_lat/2 con d_lat = 90/60, los 45 NM de DO-260B A.1.7.6.
# dump1090-fa hace exactamente esto en decodeCPRrelative (cpr.c): return -1 si
# fabs(rlat - reflat) > AirDlat/2. Es aritmetico, no probabilistico, y por eso
# NO se mezcla con MARGEN_DUCTING en una sola constante.
MEDIA_CELDA_CPR_LAT_DEG = 0.75
MEDIA_CELDA_CPR_KM = 83.5


def horizonte_km(alt_ft: float) -> float:
    """Hasta donde llega la linea de vista a esa altitud, en km.

    4.124 * (sqrt(h1_m) + sqrt(h2_m)) km es la MISMA formula que la clasica
    1.23 * (sqrt(h1_ft) + sqrt(h2_ft)) NM -- verificado:
    4.124 * sqrt(0.3048) / 1.852 = 1.2294 -- y las dos ya llevan adentro el
    radio terrestre 4/3 por refraccion (3.57 * sqrt(4/3) = 4.1223). O sea que
    esto no es un umbral empirico, es geometria.

    Valores que produce, para poder discutir el corte sin recalcularlo:

        alt_ft   horizonte   limite (x1.35)
             0      13.0 km        17.6 km
          5000     174.0 km       234.9 km
         10000     240.7 km       325.0 km
         16750     307.7 km       415.4 km
         19525     331.2 km       447.1 km
         27950     393.7 km       531.5 km
         36000     445.0 km       600.8 km
         37025     451.1 km       609.0 km
         45000     496.0 km       669.6 km
    """
    return 4.124 * (math.sqrt(max(alt_ft, 0.0) * 0.3048) + math.sqrt(ANTENA_M))


def limite_posicion_km(alt_ft: float) -> float:
    """Distancia maxima plausible para una posicion recibida a esa altitud."""
    return MARGEN_DUCTING * horizonte_km(alt_ft)


def origen_configuracion() -> str:
    """De donde salio la ubicacion: 'ADSB_RECEIVER' o 'por defecto'.

    Se publica junto con el nombre porque las dos cosas se leen distinto: "San
    Isidro (por defecto)" quiere decir que NADIE eligio, y ese es el modo de
    falla real -- el 23/08 el servidor se arranco con el acceso directo a
    dashboard.bat, que no fija ninguna variable ADSB_*, y midio nueve horas
    desde San Isidro con la antena ya mudada a Aeroparque. El unico rastro que
    quedaba de eso era is_default=true en un JSON que ninguna pagina miraba.
    """
    return "ADSB_RECEIVER" if not RECEIVER_ES_DEFAULT else "por defecto"


def resumen_configuracion(codigo_aeropuerto: str | None = None) -> str:
    """Una linea con TODO lo que decide desde donde se mide.

    Existe porque la configuracion de ubicacion vive solo en el entorno del
    proceso: nada del repo la escribe en ningun archivo, y receiver.py no
    imprime nada al importarse. O sea que de un servidor ya arrancado no se
    podia saber desde donde mide sin pedirle un endpoint -- y el 23/08 hubo DOS
    procesos servidor vivos a la vez, uno con el Python del venv y otro con el
    del Store, sin forma de saber cual contestaba.
    """
    partes = [f"midiendo desde {RECEIVER_NAME} ({origen_configuracion()})",
              f"antena {ANTENA_M:.0f} m",
              f"horizonte al suelo {horizonte_km(0):.1f} km"]
    if codigo_aeropuerto:
        try:
            from pyModeS.position._airports import AIRPORTS
            if codigo_aeropuerto in AIRPORTS:
                lat, lon = AIRPORTS[codigo_aeropuerto]
                km = distance_km(lat, lon)
                if km is not None:
                    partes.append(f"{codigo_aeropuerto} a {km:.1f} km "
                                  f"({'la pista entra en el horizonte' if km <= horizonte_km(0) else 'la pista queda BAJO el horizonte'})")
        except Exception:
            pass
    return ", ".join(partes)


def parse_surface_ref(text: str | None) -> tuple[float, float] | str | None:
    """Interpreta 'lat,lon' o un codigo ICAO de aeropuerto ('SADF').

    Devuelve TUPLA, no lista: resolve_surface_ref chequea isinstance(ref, tuple)
    (pyModeS/position/_airports.py) y con una lista cae en AIRPORTS[ref] y
    revienta con TypeError: unhashable type: 'list'. Verificado.
    """
    if not text:
        return None
    if "," in text:
        lat_text, _, lon_text = text.partition(",")
        lat, lon = float(lat_text), float(lon_text)
        # Rango primero: un lat=-158 no lo detecta ninguna heuristica despues.
        if not -90.0 <= lat <= 90.0:
            raise ValueError(f"latitud fuera de rango: {lat}")
        if not -180.0 <= lon <= 180.0:
            raise ValueError(f"longitud fuera de rango: {lon}")
        return (lat, lon)
    return text.strip().upper()


def surface_ref_default() -> tuple[float, float] | str:
    """La referencia efectiva: variable de entorno si esta, San Isidro si no.

    Variable de entorno y no .env: la referencia no es un secreto (.env aca
    guarda una clave real) y es el unico canal que llega igual al CLI y al
    dashboard, que nunca ve argv.
    """
    return parse_surface_ref(os.environ.get("ADSB_SURFACE_REF")) or DEFAULT_SURFACE_REF


def resolve_ref(ref: tuple[float, float] | str | None = None) -> tuple[float, float]:
    """La referencia efectiva como par (lat, lon), resolviendo el codigo ICAO.

    Existe porque distance_km() cae por default en RECEIVER_LAT/RECEIVER_LON y
    hasta ahora todos sus llamadores la usaban asi: el reporte de cobertura
    media desde San Isidro aunque ADSB_SURFACE_REF dijera otra cosa. El filtro
    de posiciones no puede heredar ese hardcodeo -- si mide desde el lugar
    equivocado rechaza trafico legitimo en masa -- asi que resuelve la
    referencia UNA vez y la pasa explicita.

    Falla RUIDOSO, igual que _validate_surface_ref en adsb_rtlsdr.py: un codigo
    ICAO mal escrito que caiga en silencio al default de San Isidro es
    exactamente el modo de falla que hace mentir al filtro.
    """
    ref = surface_ref_default() if ref is None else ref
    if isinstance(ref, tuple):
        return ref
    try:
        from pyModeS.position._airports import AIRPORTS
    except Exception as exc:      # sin pyModeS no hay tabla que consultar
        raise ValueError(f"no se puede resolver el codigo ICAO {ref!r} sin "
                         f"pyModeS: {exc}") from exc
    if ref not in AIRPORTS:
        raise ValueError(f"ADSB_SURFACE_REF={ref!r} no esta en la tabla de "
                         f"aeropuertos de pyModeS ({len(AIRPORTS)} codigos). "
                         f"Usa 'lat,lon' o un codigo que exista.")
    return AIRPORTS[ref]


def nearest_airport(lat: float, lon: float) -> tuple[str | None, float]:
    """Codigo y distancia en km al aeropuerto mas cercano de la tabla de pyModeS.

    Barrer 4904 aeropuertos cuesta menos de 1 ms medido, asi que se hace en
    start() y no vale la pena indexar nada.
    """
    try:
        from pyModeS.position._airports import AIRPORTS
    except Exception:      # sin pyModeS no hay decodificacion que validar
        return None, 0.0
    mejor_code, mejor_km = None, float("inf")
    for code, (airport_lat, airport_lon) in AIRPORTS.items():
        km = haversine_m(lat, lon, airport_lat, airport_lon) / 1000.0
        if km < mejor_km:
            mejor_code, mejor_km = code, km
    return mejor_code, mejor_km


def distance_km(lat: float | None, lon: float | None,
                ref: tuple[float, float] | None = None) -> float | None:
    """Distancia de una posicion al receptor, o None si no hay posicion.

    Reusa el haversine de match_adsb.py en vez de escribir una segunda copia:
    dos implementaciones de la misma formula terminan dando numeros distintos
    en algun borde y nadie sabe cual creer.
    """
    if lat is None or lon is None:
        return None
    ref_lat, ref_lon = ref if ref is not None else (RECEIVER_LAT, RECEIVER_LON)
    return haversine_m(ref_lat, ref_lon, lat, lon) / 1000.0


if __name__ == "__main__":
    ref = surface_ref_default()
    print(f"referencia configurada : {ref}")
    if isinstance(ref, tuple):
        code, km = nearest_airport(*ref)
        print(f"aeropuerto mas cercano : {code} a {km:.1f} km")
        for nombre, code in (("San Fernando", "SADF"), ("Aeroparque", "SABE"),
                             ("Ezeiza", "SAEZ")):
            from pyModeS.position._airports import AIRPORTS
            lat, lon = AIRPORTS[code]
            print(f"  {nombre:13s} ({code}) a {distance_km(lat, lon, ref):5.1f} km")
