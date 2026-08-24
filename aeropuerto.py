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

from adsb import Observation, es_altitud_de_superficie

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

# Por debajo de cuantos pies SOBRE EL CAMPO se considera que la aeronave llego
# al suelo. 300 ft no es el suelo, es el ultimo tramo donde ya no queda otra
# cosa que hacer que aterrizar o irse al aire: un avion a 300 ft sobre la pista
# alineado con ella esta comprometido con la maniobra. Se usa ese margen y no
# cero porque la altitud ADS-B es barometrica y la ultima posicion recibida
# antes del toque casi nunca es exactamente cero.
UMBRAL_SUELO_FT = 300.0

# Cuanto tiene que volver a subir DESPUES de haber bajado al umbral para que no
# cuente como aterrizaje. Es la diferencia entre un aterrizaje y un motor y al
# aire: los dos bajan igual, y lo unico que los separa es que uno se va. 800 ft
# esta por encima de cualquier rebote o error barometrico y por debajo de
# cualquier frustrada real, que gana miles de pies.
REASCENSO_FT = 800.0


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
    # De donde salio cada dato de identidad. No es adorno: "LV-KEJ (registro)" y
    # "LV-KDI (probable, del distintivo)" son afirmaciones de fuerza muy distinta,
    # y mostrarlas iguales hace que la buena pierda credibilidad con la dudosa.
    registration_source: str | None = None
    # El operador es lo que MAS gana con la inferencia: 133 por distintivo contra
    # 17 por registro. Para contar operaciones por aerolinea alcanza, aunque no
    # diga que avion fisico es.
    operator: str | None = None
    operator_source: str | None = None
    operator_code: str | None = None
    # Del Doc 8643: motores y categoria de estela. La estela es un dato
    # operativo real -define la separacion minima en aproximacion- y ademas
    # clasifica el trafico por peso sin depender del modelo exacto.
    motores: int | None = None
    tipo_motor: str | None = None
    estela: str | None = None
    estela_texto: str | None = None
    # La altitud minima BAROMETRICA vista adentro del cilindro. None cuando de
    # esta aeronave solo llegaron mensajes de superficie: ahi no hay ninguna
    # altitud medida, y publicar 0 seria publicar el placeholder como si fuera
    # una medicion (ver adsb.ALTITUD_SUPERFICIE_PLACEHOLDER).
    min_altitude_ft: float | None = None
    min_distance_km: float | None = None
    track_deg: float | None = None
    pista: str | None = None       # la pista con la que quedo alineada, si alguna
    alineada: bool = False
    posiciones: int = 0
    # Cuantas de esas posiciones eran mensajes de SUPERFICIE (TC 5-8 / BDS 0,6).
    # Es la evidencia mas fuerte que existe de que la aeronave estuvo en el
    # suelo -- lo dice el formato del mensaje, no una altitud comparada contra
    # un umbral -- y por eso se cuenta aparte y no se suma con las otras.
    posiciones_superficie: int = 0

    @property
    def confirmada(self) -> bool:
        """Si se puede afirmar que opero aca, y no solo que paso cerca y bajo.

        Un aterrizaje o despegue -no una aproximacion sin resolver ni una
        frustrada- Y alineada con una pista. La alineacion hace falta porque
        bajar al suelo dentro del cilindro no alcanza: a 8 km de Aeroparque
        tambien esta el circuito de aviacion general, y un avion bajo pero
        cruzado a la pista no esta operando en ella.
        """
        return (self.tipo in ("aterrizaje", "despegue")
                and self.alineada)


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
    # Las dos evidencias no se suman: una altitud barometrica y un mensaje de
    # superficie dicen cosas de fuerza distinta, y hasta hoy entraban a la misma
    # serie aritmetica. Medido sobre adsb_log.db: de las 1010 posiciones del
    # cilindro de SABE, 779 son mensajes de superficie sin altitud y 231 traen
    # altitud barometrica.
    posiciones_en_superficie: int = 0
    aeronaves_en_superficie: int = 0
    elevacion_ft: float = 0.0
    # Los sobrevuelos son el unico tipo que NO entra a self.operaciones (no se
    # dibujan como operacion), asi que su contador no puede ser _cuantas().
    sobrevuelos: int = 0           # entraron al cilindro sin subir ni bajar

    def _cuantas(self, tipo: str) -> int:
        return sum(1 for o in self.operaciones if o.tipo == tipo)

    @property
    def aterrizajes(self) -> int:
        """Aterrizajes REALES: bajaron al suelo y no se volvieron a ir."""
        return self._cuantas("aterrizaje")

    @property
    def despegues(self) -> int:
        return self._cuantas("despegue")

    @property
    def operaciones_reales(self) -> int:
        """Lo unico que se puede afirmar que paso en esta pista."""
        return self.aterrizajes + self.despegues

    @property
    def aproximaciones(self) -> int:
        """Bajaban, pero nunca se las vio lo bastante abajo. NO son aterrizajes."""
        return self._cuantas("aproximacion")

    @property
    def salidas(self) -> int:
        return self._cuantas("salida")

    @property
    def frustradas(self) -> int:
        """Bajaron al final y se volvieron a ir (motor y al aire)."""
        return self._cuantas("frustrada")

    @property
    def en_tierra(self) -> int:
        """Aparecieron abajo y no subieron ni bajaron: quedaron en el aeropuerto.

        Existia como resultado de _clasificar desde el principio, pero no tenia
        propiedad ni clave en como_json, asi que NINGUNA pantalla podia
        mostrarlas aunque quisiera. De ahi salian las tres cuentas que no
        cerraban: 4 tarjetas que sumaban 24, 5 numeros que sumaban 31 y 6
        tarjetas que sumaban 39, todas contra 43 aeronaves en el cilindro. Y en
        /aeropuerto/mapa esas 4 trazas se DIBUJAN, con color y etiqueta propios.
        """
        return self._cuantas("en tierra")

    @property
    def confirmadas(self) -> int:
        return sum(1 for o in self.operaciones if o.confirmada)

    @property
    def suma_categorias(self) -> int:
        """La suma de las siete categorias. Tiene que dar aeronaves_en_cilindro.

        Se publica para que la igualdad este a la vista en las pantallas. Es lo
        unico que hace que un octavo tipo agregado a _clasificar rompa algo
        visible en vez de desaparecer de las cuatro pantallas a la vez, que es
        exactamente lo que paso con 'en tierra'.
        """
        return (self.aterrizajes + self.despegues + self.aproximaciones
                + self.salidas + self.frustradas + self.en_tierra + self.sobrevuelos)

    @property
    def categorias_cuadran(self) -> bool:
        return self.suma_categorias == self.aeronaves_en_cilindro

    @property
    def advertencia(self) -> str | None:
        """Por que estos numeros pueden no ser lo que parecen, en una frase.

        Devuelve None solo cuando no hay nada que aclarar. Es la parte mas
        importante del informe: un conteo de operaciones sin esto invita a
        tratar como medicion algo que puede ser un artefacto de donde esta la
        antena.
        """
        if not self.posiciones_en_cilindro:
            # Mismo cuidado que abajo: culpar a la ubicacion solo cuando la
            # geometria la acusa. Con la pista DENTRO del horizonte, un cilindro
            # vacio no prueba que no se reciba -lo mas probable es que todavia
            # no se haya grabado nada desde aca-, y afirmarlo mandaria a revisar
            # la antena por lo que en realidad es falta de datos.
            if self.ve_la_pista:
                return ("Ninguna posición decodificada cayó dentro del cilindro "
                        f"de {self.radio_km:.0f} km y {self.techo_ft:.0f} ft sobre "
                        f"{self.codigo}. La pista está a "
                        f"{self.distancia_receptor_km:.1f} km y entra en el "
                        "horizonte, así que esto no dice que no se reciba: todavía "
                        "no hay tráfico grabado que haya entrado al cilindro.")
            return ("Ninguna posición decodificada cayó dentro del cilindro de "
                    f"{self.radio_km:.0f} km y {self.techo_ft:.0f} ft sobre "
                    f"{self.codigo}. No es que no haya habido operaciones: es que "
                    "desde donde está la antena no se reciben.")
        if not self.operaciones_reales and self.aproximaciones:
            return (f"Ningun aterrizaje ni despegue confirmado, y "
                    f"{self.aproximaciones} aproximaciones que NO se pudieron "
                    f"resolver: lo mas bajo que se vio sobre {self.codigo} fueron "
                    f"{self.min_altitude_vista_ft:.0f} ft sobre el campo, y hasta "
                    f"{UMBRAL_SUELO_FT:.0f} ft no se puede distinguir un aterrizaje "
                    "de un motor y al aire. No es que no hayan aterrizado: es que "
                    "desde aca no se ve el tramo que lo prueba.")
        # Estas frases hablan en PRESENTE sobre "esta ubicacion", pero el minimo
        # que citan sale del HISTORICO, y la base no guarda desde donde se
        # recibio cada fila. El dia de la mudanza a Aeroparque la pagina se
        # contradecia en dos lineas contiguas: arriba "los aviones en la pista
        # si se escuchan desde aca" (geometria de AHORA, pista a 2.2 km contra
        # 7.1 km de horizonte) y abajo "la fase final no se recibe desde esta
        # ubicacion" (dato de ANTES, grabado a 13.3 km desde San Isidro).
        #
        # Cuando la geometria dice que la pista se ve, el minimo alto ya NO se
        # puede atribuir a donde esta la antena, y la frase tiene que decir de
        # donde sale el numero. Importa mas que un detalle de redaccion: quien
        # acaba de mudar la antena abre esta pagina antes de grabar nada, y la
        # version vieja le decia que la mudanza fracaso.
        if self.min_altitude_vista_ft is not None and self.min_altitude_vista_ft > 1500:
            if self.ve_la_pista:
                return (f"Lo más bajo que se vio sobre {self.codigo} fueron "
                        f"{self.min_altitude_vista_ft:.0f} ft sobre el campo, pero "
                        f"la pista está a {self.distancia_receptor_km:.1f} km y sí "
                        "entra en el horizonte: ese mínimo describe lo ya grabado, "
                        "no un límite de esta ubicación. Si la antena se movió "
                        "recién, hace falta grabar de nuevo para saber hasta dónde "
                        "llega desde acá.")
            return (f"Lo más bajo que se vio sobre {self.codigo} fueron "
                    f"{self.min_altitude_vista_ft:.0f} ft sobre el campo. La fase "
                    "final no se recibe desde esta ubicación.")
        # LA CONTRADICCION VA ANTES DE LA GEOMETRIA. Esta rama existe porque el
        # 23/08, con el servidor arrancado sin ADSB_RECEIVER, este mismo informe
        # trajo 14 aterrizajes, 9 despegues, 3 frustradas y 1010 posiciones en
        # el cilindro, y al lado la frase "los aviones EN la pista no se
        # escuchan desde aca" -- las dos cosas en el mismo objeto. Se llegaba
        # ahi porque la rama de abajo no miraba ningun conteo: repetia la
        # geometria de la configuracion como si fuera un hecho medido.
        #
        # Cuando hay operaciones reales o posiciones de superficie, la
        # afirmacion que cae NO es el conteo: es la ubicacion. Los datos se
        # midieron; la configuracion se tipeo.
        if not self.ve_la_pista and (self.operaciones_reales
                                     or self.posiciones_en_superficie):
            pruebas = []
            if self.operaciones_reales:
                pruebas.append(f"{self.aterrizajes} aterrizajes y "
                               f"{self.despegues} despegues clasificados")
            if self.posiciones_en_superficie:
                pruebas.append(f"{self.posiciones_en_superficie} posiciones de "
                               f"aeronaves EN SUPERFICIE de "
                               f"{self.aeronaves_en_superficie} aeronaves")
            return (f"Contradicción: la configuración dice que la pista de "
                    f"{self.codigo} está a {self.distancia_receptor_km:.1f} km, "
                    f"más que el horizonte de radio a un avión en tierra "
                    f"({self.horizonte_superficie_km:.1f} km) — o sea que no se "
                    f"debería oír nada en la pista — y sin embargo hay "
                    + ", y ".join(pruebas) +
                    ". Las dos cosas no pueden ser ciertas: la antena no está "
                    "donde dice la configuración (revisar ADSB_RECEIVER y "
                    "ADSB_ANTENNA_M).")
        if not self.ve_la_pista:
            return (f"La pista de {self.codigo} está a "
                    f"{self.distancia_receptor_km:.1f} km, más que el horizonte de "
                    f"radio a un avión en tierra ({self.horizonte_superficie_km:.1f} "
                    "km). Los aviones EN la pista no se escuchan desde acá.")
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


def _clasificar(minima_agl: float, baja: float, sube: float,
                en_superficie: bool = False) -> str:
    """Que hizo la aeronave dentro del cilindro, con nombre propio.

    La distincion que importa es aterrizaje contra intento de aterrizaje, y no
    se puede hacer mirando solo cuanto bajo: un motor y al aire baja IGUAL que
    un aterrizaje, hasta los mismos pies, y lo unico que los separa es que
    despues se va. Por eso se mira el reascenso posterior al punto mas bajo.

    Y hay un tercer caso que es el mas comun cuando la antena esta lejos: la
    aeronave venia bajando pero nunca se la vio lo bastante abajo para saber si
    llego. Eso NO es un aterrizaje y tampoco una frustrada: es una aproximacion
    sin resolver, y se cuenta aparte. Meterla entre los aterrizajes seria
    inventar operaciones que quizas no ocurrieron; descartarla en silencio seria
    esconder que la antena no alcanza a verlas.
    """
    # `en_superficie` es un mensaje TC 5-8 / BDS 0,6: el avion declara que esta
    # en el suelo, y eso no se compara contra ningun umbral. Es evidencia mucho
    # mas fuerte que UMBRAL_SUELO_FT y ademas no depende del clima: el umbral se
    # compara contra altitud de PRESION, asi que se mueve con la QNH (medido: un
    # avion detenido en la pista de SABE pasa los 300 ft solo si la QNH es >=
    # 1002,3 hPa, y el 23/08 la QNH estaba en ~1025 hPa, 12,2 hPa del otro lado).
    llego_al_suelo = en_superficie or minima_agl <= UMBRAL_SUELO_FT
    if llego_al_suelo:
        if sube >= REASCENSO_FT and baja >= CAMBIO_MINIMO_FT:
            return "frustrada"       # bajo hasta el final y se volvio a ir
        if baja >= CAMBIO_MINIMO_FT and sube < REASCENSO_FT:
            return "aterrizaje"      # bajo y se quedo
        if sube >= CAMBIO_MINIMO_FT and baja < CAMBIO_MINIMO_FT:
            return "despegue"        # arranco abajo y se fue
        return "en tierra"           # aparecio y quedo abajo, sin subir ni bajar
    # Nunca se la vio lo bastante abajo: no se puede afirmar que aterrizo.
    if baja >= CAMBIO_MINIMO_FT and baja >= sube:
        return "aproximacion"
    if sube >= CAMBIO_MINIMO_FT:
        return "salida"
    return "sobrevuelo"


def geometria_cilindro(codigo: str | None = None) -> dict | None:
    """El cilindro de este aeropuerto, en numeros y sin observaciones.

    Existe para que LectorIncremental pueda acumular el resumen por aeronave
    mientras lee, sin importar aeropuerto.py entero ni tener que volver a
    recorrer las observaciones -- que es justamente lo que el lector no guarda.
    """
    codigo = (codigo or objetivo() or "").strip().upper()
    if not codigo:
        return None
    try:
        from pyModeS.position._airports import AIRPORTS
        if codigo not in AIRPORTS:
            return None
        lat, lon = AIRPORTS[codigo]
    except Exception:
        return None
    import geografia
    return {"codigo": codigo, "lat": lat, "lon": lon,
            "radio_km": RADIO_KM, "techo_ft": TECHO_FT,
            "elevacion_ft": geografia.ELEVACION_FT.get(codigo, 0.0)}


def resumir_cilindro(observations: list[Observation], cilindro: dict) -> tuple[dict, int]:
    """El estado O(1) por aeronave que la clasificacion necesita.

    Es la mitad de informe() que ACUMULA, separada de la que CLASIFICA. La otra
    mitad -- LectorIncremental._absorber_cilindro -- construye exactamente este
    mismo dict fila por fila, y las dos terminan llamando al mismo
    _clasificar(). Se refactorizo en vez de duplicar la logica porque son los
    conteos publicados (aterrizajes, despegues, frustradas) los que estan en
    juego: dos implementaciones de la clasificacion pueden divergir y nadie se
    entera hasta que las tarjetas de las dos paginas no coinciden.

    No se puede seguir clasificando sobre la lista completa de observaciones
    porque el estado residente ya no las guarda: 313 B/obs son 143 MB a 30 dias
    y 1744 MB al ano.
    """
    import receiver

    por_icao: dict[str, dict] = {}
    posiciones = 0
    for o in sorted(observations, key=lambda o: o.timestamp):
        if o.latitude is None or o.longitude is None or o.altitude_ft is None:
            continue
        if o.altitude_ft > cilindro["techo_ft"]:
            continue
        d = receiver.distance_km(o.latitude, o.longitude,
                                 (cilindro["lat"], cilindro["lon"]))
        if d is None or d > cilindro["radio_km"]:
            continue
        posiciones += 1
        r = por_icao.get(o.icao24)
        if r is None:
            r = por_icao[o.icao24] = {
                "n": 0, "primera_alt": None, "ultima_alt": None,
                "min_alt": None, "min_t": o.timestamp, "min_d": d,
                "track": None, "callsign": None, "superficie": 0}
        r["n"] += 1
        # EL PLACEHOLDER DE SUPERFICIE NO ENTRA EN LA ARITMETICA DE ALTITUDES.
        # Es el 0.0 que adsb_rtlsdr escribe cuando el mensaje no trae altitud
        # (ver adsb.ALTITUD_SUPERFICIE_PLACEHOLDER), y restarlo contra una
        # altitud barometrica real fabrica descensos del tamano del offset de
        # presion: medido, ARG1686 (0 / -325 / 1450) y JES3088 (0 / -300 / 2400)
        # salian con baja=325 y baja=300 ft contra CAMBIO_MINIMO_FT=300 y
        # quedaban clasificados como motor y al aire cuando son despegues
        # normales. Y en el otro sentido, los 14 aterrizajes tenian ultima_alt=0
        # y min_alt hasta -350: un reascenso FABRICADO de 350 ft, que con la QNH
        # en 1043 hPa llegaria a REASCENSO_FT=800 y los volveria frustradas a
        # todos. Se cuenta aparte, que es evidencia mas fuerte, no menos.
        if es_altitud_de_superficie(o.altitude_ft):
            r["superficie"] += 1
            if o.track_deg is not None:
                r["track"] = o.track_deg
            if o.callsign:
                r["callsign"] = o.callsign.strip()
            continue
        if r["primera_alt"] is None:
            r["primera_alt"] = o.altitude_ft
        r["ultima_alt"] = o.altitude_ft
        # Estricto: alturas.index(min(alturas)) se queda con el PRIMER minimo.
        if r["min_alt"] is None or o.altitude_ft < r["min_alt"]:
            r["min_alt"], r["min_t"], r["min_d"] = o.altitude_ft, o.timestamp, d
        if o.track_deg is not None:
            r["track"] = o.track_deg
        # Truthy y no .strip() truthy: es el criterio exacto de la ruta de
        # siempre, next((o.callsign.strip() ... if o.callsign), None).
        if o.callsign:
            r["callsign"] = o.callsign.strip()
    return por_icao, posiciones


def informe_desde_resumen(por_icao: dict, posiciones: int,
                          codigo: str | None = None,
                          identidades: dict | None = None) -> Informe | None:
    """La mitad de informe() que CLASIFICA. Ver resumir_cilindro()."""
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

    # La identidad se resuelve sobre el historial COMPLETO de cada aeronave y no
    # sobre el resumen del cilindro. Es la diferencia entre saber el vuelo y no
    # saberlo: el distintivo viaja en el 3% de los mensajes y casi nunca cae
    # justo dentro del cilindro, asi que resolviendo solo con lo de adentro la
    # columna VUELO salia vacia incluso para aviones cuyo distintivo se conocia
    # perfectamente. Quien llama pasa las identidades ya resueltas; si no las
    # pasa, se cae al resumen del cilindro, que es peor pero no miente.
    import identidad
    if identidades is None:
        identidades = identidad.resolver_desde_resumen(por_icao)

    pistas = _rumbos_de_pista(codigo)
    # La altitud ADS-B es barometrica sobre el nivel del mar; para saber si una
    # aeronave toco hace falta su altura sobre LA PISTA. En Aeroparque son 16 ft
    # y da casi igual, pero en Ezeiza son 64 y en un aeropuerto de altura la
    # diferencia decide si un aterrizaje se cuenta o no.
    import geografia
    elevacion = geografia.ELEVACION_FT.get(codigo, 0.0)
    inf.elevacion_ft = elevacion

    inf.posiciones_en_cilindro = posiciones
    inf.aeronaves_en_cilindro = len(por_icao)
    inf.posiciones_en_superficie = sum(r.get("superficie", 0) for r in por_icao.values())
    inf.aeronaves_en_superficie = sum(1 for r in por_icao.values() if r.get("superficie"))
    reales = [r["min_alt"] for r in por_icao.values() if r["min_alt"] is not None]
    if reales:
        # Sobre EL CAMPO, no sobre el mar: es la altura que dice si se vio la
        # fase final, y compararla contra un umbral en AGL exige que este en AGL.
        #
        # OJO CON LA ETIQUETA: esto es altitud de PRESION (referida a 1013,25
        # hPa) menos la elevacion del campo, y eso no es AGL. El 23/08 la QNH
        # estuvo en ~1025 hPa, o sea un offset de -331 ft, veinte veces la
        # elevacion de 16 ft que se resta: este numero dio -366 ft cuando el AGL
        # real de esa observacion era -35 +/- 26 ft, o sea el avion EN la pista.
        # Corregirlo de verdad pide estimar la altitud de presion del campo
        # (percentil bajo de las barometricas dentro del perimetro; hoy son 12
        # medidas independientes en -314,6 ft con sd 25,9) o leer geo_minus_baro,
        # que la antena ya recibe y adsb_rtlsdr.py:111 tira. Esta en ESTADO.md.
        inf.min_altitude_vista_ft = min(reales) - elevacion

    for icao24, r in por_icao.items():
        # Alturas SOBRE EL CAMPO. El minimo NO es el primero contra el ultimo:
        # el avion que toca y vuelve a salir tiene el minimo en el medio, y
        # comparar los extremos lo daria como sobrevuelo.
        en_superficie = bool(r.get("superficie"))
        if r["min_alt"] is None:
            # De esta aeronave solo llegaron mensajes de superficie: no hay
            # ninguna altitud medida con la que calcular baja ni sube, y el
            # avion declaro estar en el suelo. Inventar un 0 para restarlo seria
            # publicar el placeholder como medicion. Medido: 3 de las 43
            # aeronaves del cilindro estan en este caso.
            minima, baja, sube = 0.0, 0.0, 0.0
        else:
            minima = r["min_alt"] - elevacion
            baja = r["primera_alt"] - r["min_alt"]
            sube = r["ultima_alt"] - r["min_alt"]

        tipo = _clasificar(minima, baja, sube, en_superficie=en_superficie)
        if tipo == "sobrevuelo":
            inf.sobrevuelos += 1
            continue

        # El rumbo del ultimo punto del cilindro que lo trajo. Fuera del
        # cilindro no se busca: el rumbo de crucero no dice nada sobre la
        # alineacion con una pista.
        alineada, pista = _alineada(r["track"], pistas)
        # La identidad sale de identidad.py y no de un lookup pelado al registro:
        # ese lookup deja sin operador al 47% del trafico real de esta antena
        # -las direcciones que OpenSky no tiene- y el distintivo, que el avion SI
        # transmite, lo resuelve. Medido: 133 operadores por distintivo contra 17
        # por registro.
        ident = identidades.get(icao24)
        inf.operaciones.append(Operacion(
            icao24=icao24, tipo=tipo, timestamp=r["min_t"],
            # El distintivo del historial completo, con el del cilindro como
            # respaldo: r["callsign"] solo tiene lo que llego DENTRO del
            # cilindro, y el distintivo viaja en el 3% de los mensajes.
            callsign=((ident.callsign if ident else None) or r["callsign"]),
            registration=(ident.registration if ident else None),
            registration_source=(ident.registration_source if ident else None),
            aircraft_type=(ident.aircraft_type if ident else None),
            operator=(ident.operator if ident else None),
            operator_source=(ident.operator_source if ident else None),
            operator_code=(ident.operator_code if ident else None),
            motores=(ident.motores if ident else None),
            tipo_motor=(ident.tipo_motor if ident else None),
            estela=(ident.estela if ident else None),
            estela_texto=(ident.estela_texto if ident else None),
            min_altitude_ft=r["min_alt"], min_distance_km=round(r["min_d"], 2),
            track_deg=r["track"], pista=pista, alineada=alineada, posiciones=r["n"],
            posiciones_superficie=r.get("superficie", 0),
        ))

    inf.operaciones.sort(key=lambda o: o.timestamp, reverse=True)
    return inf


def informe(observations: list[Observation], codigo: str | None = None) -> Informe | None:
    """Atribuir operaciones a un aeropuerto. None si no se puede ubicar el codigo.

    Partida en dos desde que el mapa lee por delta: resumir_cilindro() acumula
    y informe_desde_resumen() clasifica. Esta funcion es la ruta de siempre --
    la que recibe una lista de observaciones -- y llama a las mismas dos
    mitades, para que no existan dos implementaciones de la clasificacion.
    """
    cilindro = geometria_cilindro(codigo)
    if cilindro is None:
        return None
    por_icao, posiciones = resumir_cilindro(observations, cilindro)
    import identidad
    return informe_desde_resumen(por_icao, posiciones, cilindro["codigo"],
                                 identidad.resolver(observations))


def como_json(inf: Informe | None) -> dict | None:
    if inf is None:
        return None
    return {
        "codigo": inf.codigo, "nombre": inf.nombre, "lat": inf.lat, "lon": inf.lon,
        "radio_km": inf.radio_km, "techo_ft": inf.techo_ft,
        "distancia_receptor_km": inf.distancia_receptor_km,
        "horizonte_superficie_km": inf.horizonte_superficie_km,
        "ve_la_pista": inf.ve_la_pista,
        "aterrizajes": inf.aterrizajes, "despegues": inf.despegues,
        "operaciones_reales": inf.operaciones_reales,
        "aproximaciones": inf.aproximaciones, "salidas": inf.salidas,
        "frustradas": inf.frustradas,
        # 'en tierra' faltaba, y es la razon por la que las tarjetas de tres
        # pantallas sumaban 24, 31 y 39 contra 43 aeronaves en el cilindro.
        "en_tierra": inf.en_tierra,
        "confirmadas": inf.confirmadas, "sobrevuelos": inf.sobrevuelos,
        # La igualdad viaja resuelta para que las pantallas la puedan imprimir
        # sin recalcularla cada una a su manera. Ver Informe.suma_categorias.
        "suma_categorias": inf.suma_categorias,
        "categorias_cuadran": inf.categorias_cuadran,
        "elevacion_ft": inf.elevacion_ft,
        "umbral_suelo_ft": UMBRAL_SUELO_FT,
        "min_altitude_vista_ft": inf.min_altitude_vista_ft,
        "posiciones_en_cilindro": inf.posiciones_en_cilindro,
        # Las dos evidencias, separadas: un mensaje de superficie es mas fuerte
        # que una altitud barometrica baja, y sumarlas las iguala.
        "posiciones_en_superficie": inf.posiciones_en_superficie,
        "aeronaves_en_superficie": inf.aeronaves_en_superficie,
        "posiciones_con_altitud": (inf.posiciones_en_cilindro
                                   - inf.posiciones_en_superficie),
        "aeronaves_en_cilindro": inf.aeronaves_en_cilindro,
        "advertencia": inf.advertencia,
        "operaciones": [
            {"icao24": o.icao24, "tipo": o.tipo, "timestamp": o.timestamp,
             "callsign": o.callsign, "registration": o.registration,
             "aircraft_type": o.aircraft_type,
             "registration_source": o.registration_source,
             "operator": o.operator, "operator_source": o.operator_source,
             "operator_code": o.operator_code,
             "motores": o.motores, "tipo_motor": o.tipo_motor,
             "estela": o.estela, "estela_texto": o.estela_texto,
             "min_altitude_ft": o.min_altitude_ft, "min_distance_km": o.min_distance_km,
             "track_deg": o.track_deg, "pista": o.pista, "alineada": o.alineada,
             "confirmada": o.confirmada, "posiciones": o.posiciones,
             "posiciones_superficie": o.posiciones_superficie,
             "en_superficie": o.posiciones_superficie > 0}
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
    print(f"  {inf.posiciones_en_cilindro} posiciones de {inf.aeronaves_en_cilindro} aeronaves"
          f" ({inf.posiciones_en_superficie} de superficie, de "
          f"{inf.aeronaves_en_superficie} aeronaves, y "
          f"{inf.posiciones_en_cilindro - inf.posiciones_en_superficie} con altitud"
          f" barometrica)")
    # "altitud de presion menos la elevacion" y no "sobre el campo": son cosas
    # distintas y el 23/08 la diferencia fue de 331 ft (QNH ~1025 hPa) contra
    # los 16 ft de elevacion que se restan. Ver informe_desde_resumen.
    print(f"  altitud minima vista adentro: "
          f"{inf.min_altitude_vista_ft if inf.min_altitude_vista_ft is not None else '-'} ft "
          f"de PRESION menos la elevacion del campo ({inf.elevacion_ft:.0f} ft); "
          f"no es AGL: con QNH alta da negativo con el avion en la pista")
    print()
    print(f"ATERRIZAJES REALES {inf.aterrizajes} | despegues {inf.despegues} | "
          f"operaciones reales {inf.operaciones_reales} | "
          f"confirmados por alineacion {inf.confirmadas}")
    print(f"  sin resolver: {inf.aproximaciones} aproximaciones que bajaban pero "
          f"nunca se vieron bajo {UMBRAL_SUELO_FT:.0f} ft sobre el campo")
    print(f"  frustradas (motor y al aire) {inf.frustradas} | "
          f"salidas sin resolver {inf.salidas} | en tierra {inf.en_tierra} | "
          f"sobrevuelos {inf.sobrevuelos}")
    # La igualdad impresa es lo unico que hace que un tipo nuevo de _clasificar
    # rompa algo visible en vez de desaparecer de todas las pantallas a la vez,
    # que es lo que paso con 'en tierra': clasificado desde el principio, sin
    # contador ni clave, invisible en las cuatro superficies.
    print(f"  suma de las siete categorias: {inf.suma_categorias} contra "
          f"{inf.aeronaves_en_cilindro} aeronaves en el cilindro"
          f" -> {'CUADRA' if inf.categorias_cuadran else 'NO CUADRA'}")
    if inf.advertencia:
        print(f"\n  OJO: {inf.advertencia}")
    if inf.operaciones:
        print()
        # ALT MSL y DIST APT, con datum y origen en el encabezado: la columna es
        # altitud barometrica sobre el nivel del mar (el titular de arriba le
        # resta la elevacion) y la distancia es AL AEROPUERTO, no al receptor.
        # Leida al lado de "la pista esta a 13.3 km de la antena" invitaba a
        # comparar dos distancias con origenes distintos.
        print(f"{'TIPO':13s} {'VUELO':9s} {'MATRICULA':10s} {'TIPO AVION':18s} "
              f"{'ALT MSL':>8s} {'D.APT':>6s} {'RUMBO':>6s} {'SUP':>4s} PISTA")
        print("-" * 100)
        for o in inf.operaciones[:25]:
            print(f"{o.tipo:13s} {(o.callsign or '-'):9s} {(o.registration or '-'):10s} "
                  f"{(o.aircraft_type or '-')[:18]:18s} "
                  # "sin alt" y no 0: de esta aeronave solo llegaron mensajes de
                  # superficie, que no traen altitud.
                  f"{(f'{o.min_altitude_ft:.0f}' if o.min_altitude_ft is not None else 'sin alt'):>8s} "
                  f"{o.min_distance_km:6.2f} "
                  f"{(f'{o.track_deg:.0f}' if o.track_deg is not None else '-'):>6s} "
                  f"{o.posiciones_superficie:4d} "
                  f"{o.pista or '-'}{'  (confirmada)' if o.confirmada else ''}")
