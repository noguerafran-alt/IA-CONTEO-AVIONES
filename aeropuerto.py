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


# UNA PASADA, NO UNA AERONAVE. Hasta el 2026-09-03 el resumen se agrupaba por
# ICAO24, asi que una direccion tenia UNA sola operacion en toda la grabacion:
# el avion que aterrizo el 22, despego el 23 y volvio a despegar hoy contaba una
# vez. Medido sobre adsb_log.db con 3 dias parciales: 32 operaciones reales
# agrupando por direccion contra 46 agrupando por pasada, o sea un 44% perdido,
# y 92 de las 4166 direcciones aparecian en mas de un dia.
#
# Peor que perder: INVENTAR. Las 7 "motor y al aire" que publicaba el tablero
# venian todas de direcciones vistas en 2 o 3 dias distintos, y separadas por dia
# ninguna era un motor y al aire -- daban aterrizajes y despegues limpios. Es
# obvio en retrospectiva: mezclar el aterrizaje del dia 22 con el despegue del 23
# produce exactamente la firma de una frustrada (toco abajo y despues subio
# mucho). Agrupando por pasada las frustradas pasaron de 7 a 0.
#
# 10 minutos sin emitir DENTRO del cilindro cierra la pasada. El numero lo eligio
# Fran; medido, cortar por dia o por hueco de 10 o de 30 minutos daba los mismos
# 46, asi que el resultado no es sensible al valor exacto con estos datos. La
# segmentacion por hueco es igual la correcta: un avion que opera cuatro veces en
# un dia bueno necesita el hueco, y con 3 dias parciales eso todavia no se puede
# distinguir.
HUECO_PASADA_S = float(os.environ.get("ADSB_PASS_GAP_S", 600.0))

# --- Inferir un despegue cruzando DOS pasadas -------------------------------
#
# Hay despegues de los que NO ENTRA NI UNA POSICION al cilindro. Medido el
# 2026-09-03 con un despegue visto a ojo desde el campo: entre la ultima
# posicion de superficie (14:05:51, detenido a 0 kt sobre el campo) y la primera
# del ascenso (16:03:05, 5425 ft a 14,85 km) hay 117 minutos sin nada. Se
# perdieron los primeros dos o tres minutos de la subida, que es justo el tramo
# que cruza el cilindro. Causa probable: el bootstrap CPR de posiciones aereas
# necesita 3 pares consistentes y arranca de cero al pasar de superficie a
# aereo.
#
# Con UNA pasada ese despegue es inclasificable, porque no hay pasada. Cruzando
# DOS si se puede afirmar: la aeronave estaba demostrablemente EN EL CAMPO y
# despues estaba demostrablemente SUBIENDO Y ALEJANDOSE, y lo unico que hay
# entre esas dos cosas es un despegue.
#
# ES UNA CATEGORIA DE EVIDENCIA DISTINTA y se trata como tal: va marcada como
# inferida -igual que registration_source- y se cuenta APARTE, nunca entre los
# despegues medidos. Meterla con los otros seria exactamente lo que este modulo
# no hace: publicar como observado algo que se dedujo.
#
# Hasta cuanto despues del ultimo contacto en tierra se acepta el ascenso. Con
# el limite muy alto se uniria un contacto en tierra de hoy con un ascenso de
# pasado manana, cuando en el medio la aeronave pudo volar a otro lado y volver
# sin que la antena la escuchara. 6 h es holgado contra los 117 min medidos.
HUECO_INFERENCIA_MAX_S = float(os.environ.get("ADSB_INFER_GAP_S", 6 * 3600.0))

# El ascenso tiene que EMPEZAR cerca del campo. Si la primera posicion aerea ya
# esta a 80 km, no es evidencia de haber salido de aca. 40 km es mas del doble
# de los 14,85 km medidos, y sigue muy por dentro del alcance de la antena.
RADIO_INFERENCIA_KM = float(os.environ.get("ADSB_INFER_RADIUS_KM", 40.0))

# Cuanto tiene que ganar entre la primera y la ultima posicion del ascenso para
# llamarlo despegue y no ruido barometrico ni un avion de paso. 2000 ft esta muy
# por encima de cualquier oscilacion y muy por debajo de los 28 500 ft medidos.
ASCENSO_INFERIDO_FT = float(os.environ.get("ADSB_INFER_CLIMB_FT", 2000.0))

# UNA CARRERA DE PISTA, NO UN RODAJE. Los mensajes de superficie (BDS 0,6) no
# traen altitud pero SI traen velocidad respecto al suelo, y eso alcanza para
# saber en que sentido fue la operacion sin depender de ninguna altitud:
#
#   frena de mucho a poco   -> carrera de aterrizaje
#   acelera de poco a mucho -> carrera de despegue
#
# Medido en JES3882 (e8061b) el 2026-09-03: 28 posiciones de superficie a 0,08 -
# 0,46 km de SABE con la velocidad cayendo 90 -> 21 -> 8 -> 0 kt. Es un
# aterrizaje que se veia con toda claridad en los datos y que se contaba como
# "en tierra", porque sin altitud no habia baja ni sube que comparar.
#
# El umbral separa la carrera del rodaje: un avion rodando a plataforma anda por
# debajo de 30 kt y una carrera pasa los 100. El delta exige que el cambio sea
# grande para afirmar el sentido, en vez de leerle intencion a dos mediciones
# parecidas.
CARRERA_KT = 60.0
DELTA_CARRERA_KT = 40.0

# Aeroparque por defecto porque es el aeropuerto que este proyecto existe para
# contar. Se cambia con ADSB_AIRPORT, y ADSB_AIRPORT=NINGUNO apaga la seccion.
AEROPUERTO_DEFAULT = "SABE"


def objetivo() -> str | None:
    """El codigo del aeropuerto a contar, o None si se apago la seccion."""
    codigo = (os.environ.get("ADSB_AIRPORT") or AEROPUERTO_DEFAULT).strip().upper()
    return None if codigo in ("", "NINGUNO", "NONE", "NO") else codigo


@dataclass
class Operacion:
    """UNA PASADA de una aeronave por el cilindro de un aeropuerto.

    Una aeronave puede tener varias: aterriza a la manana y despega al mediodia
    son dos operaciones de la misma direccion. Ver HUECO_PASADA_S.
    """
    icao24: str
    # Los siete que devuelve _clasificar: aterrizaje, despegue, frustrada,
    # en tierra, aproximacion, salida, sobrevuelo. El comentario decia
    # "'aproximacion' | 'salida'" desde antes de que existieran los otros cinco.
    tipo: str
    timestamp: float               # el instante mas bajo dentro del cilindro
    callsign: str | None = None
    # De donde sale el distintivo que se publica. Mismo criterio que
    # registration_source: "ARG1674 (transmitido en la operacion)" y "ARG1674
    # (lo mas cercano, a 94 min)" son afirmaciones de fuerza muy distinta. Ver
    # distintivo_en() para por que un distintivo puede no ser el de esta pierna.
    callsign_source: str | None = None
    registration: str | None = None
    aircraft_type: str | None = None
    # De donde salio cada dato de identidad. No es adorno: "LV-KEJ (registro)" y
    # "LV-KDI (probable, del distintivo)" son afirmaciones de fuerza muy distinta,
    # y mostrarlas iguales hace que la buena pierda credibilidad con la dudosa.
    registration_source: str | None = None
    # El numero de serie del fuselaje, del registro. Sin procedencia: no se
    # infiere de nada, o el registro lo tiene o no. Medido el 2026-09-06: lo
    # tienen 31 de las 58 aeronaves que operaron en Aeroparque, el 53%.
    serial_number: str | None = None
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
    # Numero de pasada de esta aeronave por el cilindro, desde 0. Sirve para
    # distinguir dos operaciones de la misma direccion sin mirarles la hora, y
    # para que se vea que la segmentacion esta actuando.
    pasada: int = 0
    # 'frena' | 'acelera' | None: el sentido de la carrera de pista leido de la
    # velocidad de los mensajes de superficie. Ver CARRERA_KT. Se publica porque
    # es la evidencia que sostiene la clasificacion cuando no hubo altitud.
    carrera: str | None = None

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
    # Cuantas VISITAS, que es distinto de cuantas aeronaves y es el numero
    # contra el que cuadran las categorias. Ver HUECO_PASADA_S.
    pasadas_en_cilindro: int = 0
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
    # Despegues DEDUCIDOS cruzando dos pasadas, no medidos dentro del cilindro.
    # Van en su propia lista y NO en `operaciones`: no tienen pasada -- ese es
    # justo el motivo por el que hay que inferirlos-- asi que sumarlos alla
    # rompería el cuadre contra pasadas_en_cilindro, que es el control que
    # detecta que falta una categoría. Ver HUECO_INFERENCIA_MAX_S.
    inferidas: list = field(default_factory=list)
    # Deducciones que se descartaron por corresponder a un despegue YA medido.
    # Se publica en vez de descartarse en silencio: un 0 acá con inferidas en 0
    # significa "no hubo nada que deducir", y un número alto significa que el
    # ascenso casi siempre se ve y la deducción casi nunca hace falta.
    inferidas_descartadas: int = 0

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
        """La suma de las siete categorias. Tiene que dar pasadas_en_cilindro.

        Se publica para que la igualdad este a la vista en las pantallas. Es lo
        unico que hace que un octavo tipo agregado a _clasificar rompa algo
        visible en vez de desaparecer de las cuatro pantallas a la vez, que es
        exactamente lo que paso con 'en tierra'.
        """
        return (self.aterrizajes + self.despegues + self.aproximaciones
                + self.salidas + self.frustradas + self.en_tierra + self.sobrevuelos)

    @property
    def categorias_cuadran(self) -> bool:
        # Contra PASADAS, no contra aeronaves. Cada pasada produce exactamente
        # una clasificacion, asi que es pasadas lo que tiene que cerrar; desde
        # que se segmenta, una aeronave que entro tres veces aporta tres
        # categorias y comparar contra aeronaves daria "no cuadra" siempre.
        return self.suma_categorias == self.pasadas_en_cilindro

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
                en_superficie: bool = False,
                carrera: str | None = None) -> str:
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
        # Sin altitud que comparar, pero con una carrera de pista medida: la
        # velocidad de los mensajes de superficie dice el sentido. Es evidencia
        # mas fuerte que el delta de altitud, no un respaldo debil -- un avion
        # frenando de 90 a 0 kt sobre el campo esta aterrizando, y no hay
        # interpretacion barometrica de por medio.
        #
        # Medido: JES3882 el 2026-09-03 tenia 28 posiciones de superficie a 80 -
        # 460 m de SABE con la velocidad cayendo de 90 a 0 kt, y salia "en
        # tierra" porque min_alt era None y baja y sube valian 0. Era un
        # aterrizaje.
        if carrera == "frena":
            return "aterrizaje"
        if carrera == "acelera":
            return "despegue"
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


def nuevo_resumen() -> dict:
    """El estado que acumula acumular_en_cilindro(). Ver esa funcion."""
    # "suelo" y "ascenso" son para inferir despegues que no dejaron ni una
    # posicion en el cilindro. Ver HUECO_INFERENCIA_MAX_S e inferir_despegues().
    return {"por_pasada": {}, "ultimo_t": {}, "pasada": {}, "posiciones": 0,
            "suelo": {}, "ascenso": {}, "distintivos": {}}


def _anotar_distintivo(estado: dict, o: Observation) -> None:
    """Guardar QUE distintivo transmitia cada direccion y DESDE CUANDO HASTA CUANDO.

    EL BUG QUE ARREGLA. Hasta el 2026-09-04 la operacion publicaba "el ultimo
    distintivo escuchado de esa direccion", que es el de la pierna SIGUIENTE en
    cuanto el avion vuelve a volar. Medido sobre la base del 03/09 contra los
    listados de Aeropuertos Argentina: de 41 operaciones verificables, 16 salian
    con el numero de otro vuelo. Los aterrizajes eran los peores -- 14 de 17 --
    porque despues de aterrizar el avion casi siempre despega otra vez.
    Ejemplo: e08594 despego a las 10:24 y se publicaba JES3638, que es el vuelo
    que hizo a las 15:00; a las 10:24 estaba transmitiendo JES3102.

    POR QUE NO ALCANZA CON MIRAR EL CILINDRO. Los mensajes de identificacion no
    traen posicion -- medido: de 1261 mensajes con distintivo en la base, 0
    tienen latitud -- asi que nunca pasan el filtro de acumular_en_cilindro() y
    el distintivo "de la pasada" salia vacio en las 80 operaciones. Lo que si se
    puede es fecharlos, y eso es lo que se guarda aca.

    POR TRAMOS y no una lista de muestras: se fusionan las repeticiones
    consecutivas del mismo distintivo. Medido sobre 13 dias de base, el maximo
    son 7 tramos por direccion, asi que no compromete el O(1) por pasada que
    resumir_cilindro() existe para sostener (ver 313 B/obs alli).
    """
    cs = (o.callsign or "").strip()
    # '#' es lo que deja el decodificador cuando no pudo resolver el caracter.
    # No es un filtro cosmetico: '########' se publico como numero de vuelo en
    # el Excel del 03/09 (direccion e0645a), y con la eleccion por tiempo un
    # tramo basura puede ganarle a uno bueno por estar mas cerca.
    if not cs or "#" in cs:
        return
    tramos = estado["distintivos"].setdefault(o.icao24, [])
    if tramos and tramos[-1][2] == cs:
        tramos[-1][1] = max(tramos[-1][1], o.timestamp)
    else:
        tramos.append([o.timestamp, o.timestamp, cs])


def distintivo_en(distintivos: dict | None, icao24: str,
                  t: float) -> tuple[str | None, str | None]:
    """(distintivo, de donde salio) para la direccion `icao24` en el instante `t`.

    Si `t` cae DENTRO de un tramo, ese es el distintivo que la aeronave estaba
    transmitiendo: es un dato observado y fechado, no una atribucion.

    Si no cae en ninguno se devuelve el tramo mas cercano CON SU DISTANCIA EN
    MINUTOS, que es lo que permite no creerle. Un tramo a 2 min es el mismo
    vuelo; uno a 94 min es probablemente otra pierna. No se descarta por un
    umbral inventado: se publica la distancia y decide quien lee, que es el
    criterio de "nada se descarta en silencio" de CLAUDE.md.
    """
    tramos = (distintivos or {}).get(icao24) or []
    if not tramos:
        return None, None
    for t0, t1, cs in tramos:
        if t0 <= t <= t1:
            return cs, "transmitido en la operación"
    t0, t1, cs = min(tramos, key=lambda s: min(abs(s[0] - t), abs(s[1] - t)))
    minutos = min(abs(t0 - t), abs(t1 - t)) / 60.0
    return cs, f"el más cercano, a {minutos:.0f} min"


def acumular_en_cilindro(estado: dict, o: Observation, cilindro: dict) -> bool:
    """Sumar UNA observacion al resumen por pasada. True si cayo adentro.

    EL UNICO ACUMULADOR. Antes habia dos copias de esta logica -- esta y
    adsb_events.LectorIncremental._absorber_cilindro -- que tenian que producir
    el mismo dict y se vigilaban con un test que las compara campo por campo.
    Al segmentar por pasada el estado dejo de ser un dict plano por direccion
    (hay que recordar el ultimo timestamp y el numero de pasada de cada una), y
    mantener eso duplicado es exactamente la clase de duplicacion que este
    modulo evita: son los conteos publicados -- aterrizajes, despegues,
    frustradas -- los que estan en juego, y dos implementaciones pueden divergir
    sin que nadie se entere hasta que las tarjetas de dos paginas no coinciden.

    Las observaciones tienen que llegar en orden de timestamp. Ya era asi antes
    de las pasadas -- primera_alt y ultima_alt dependen del orden -- pero ahora
    importa mas, porque una fila fuera de orden puede abrir una pasada de mas.
    """
    import receiver

    # ANTES del filtro de posicion, a proposito: el distintivo viaja en mensajes
    # de identificacion que NO traen posicion, asi que si se anotara despues no
    # se anotaria nunca. Ver _anotar_distintivo().
    _anotar_distintivo(estado, o)

    if o.altitude_ft is None or o.latitude is None or o.longitude is None:
        return False
    # La distancia se calcula ANTES de descartar por techo, y no despues como
    # antes: lo que queda afuera del cilindro tambien se mira, para poder
    # inferir un despegue del que no entro ni una posicion. Es una haversine mas
    # por observacion aerea; el costo es despreciable al lado de decodificar.
    d = receiver.distance_km(o.latitude, o.longitude,
                             (cilindro["lat"], cilindro["lon"]))
    if d is None:
        return False
    if o.altitude_ft > cilindro["techo_ft"] or d > cilindro["radio_km"]:
        _anotar_ascenso(estado, o, d)
        return False

    estado["posiciones"] += 1
    icao24 = o.icao24
    # El hueco se mide sobre lo que entro AL CILINDRO, no sobre todo lo que
    # emitio la aeronave. Es la definicion honesta de "otra pasada por este
    # aeropuerto": irse del cilindro diez minutos y volver son dos visitas, y
    # que mientras tanto se la siguiera escuchando en crucero no las une.
    anterior = estado["ultimo_t"].get(icao24)
    if anterior is not None and o.timestamp - anterior > HUECO_PASADA_S:
        estado["pasada"][icao24] = estado["pasada"].get(icao24, 0) + 1
    estado["ultimo_t"][icao24] = o.timestamp

    clave = (icao24, estado["pasada"].get(icao24, 0))
    r = estado["por_pasada"].get(clave)
    if r is None:
        r = estado["por_pasada"][clave] = {
            "n": 0, "primera_alt": None, "ultima_alt": None,
            "min_alt": None, "min_t": o.timestamp, "min_d": d,
            "track": None, "callsign": None, "superficie": 0,
            # La velocidad de los mensajes de superficie, que es lo que permite
            # distinguir una carrera de aterrizaje de una de despegue sin
            # ninguna altitud. Ver CARRERA_KT.
            "sup_vel_primera": None, "sup_vel_ultima": None, "sup_vel_max": None}
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
        # Ultimo contacto en tierra SOBRE EL CAMPO: es el ancla de la
        # inferencia. Un ascenso posterior se mide desde aca, y volver a tocar
        # tierra borra el ascenso acumulado -- si aterrizo de nuevo, lo que
        # hubiera pasado antes ya no es "el despegue que sigue a este contacto".
        estado["suelo"][icao24] = o.timestamp
        estado["ascenso"].pop(icao24, None)
        if o.ground_speed_kt is not None:
            v = float(o.ground_speed_kt)
            if r["sup_vel_primera"] is None:
                r["sup_vel_primera"] = v
            r["sup_vel_ultima"] = v
            if r["sup_vel_max"] is None or v > r["sup_vel_max"]:
                r["sup_vel_max"] = v
        if o.track_deg is not None:
            r["track"] = o.track_deg
        if o.callsign:
            r["callsign"] = o.callsign.strip()
        return True

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
    return True


def _anotar_ascenso(estado: dict, o: Observation, d: float) -> None:
    """Registrar una posicion de AFUERA del cilindro, si sigue a un contacto en
    tierra sobre el campo.

    Solo se guardan dos puntos por aeronave -el primero y el ultimo- y no la
    traza: la inferencia necesita saber si gano altitud y se alejo, y eso se
    contesta con los extremos. Guardar la traza entera reintroduciria el consumo
    de memoria que resumir_cilindro() existe para evitar (313 B/obs son 143 MB a
    30 dias).
    """
    icao24 = o.icao24
    suelo = estado["suelo"].get(icao24)
    if suelo is None or o.timestamp <= suelo:
        return                      # nunca estuvo en el campo, o esto es previo
    if o.timestamp - suelo > HUECO_INFERENCIA_MAX_S:
        return                      # demasiado tarde para atribuirlo a ese suelo
    if es_altitud_de_superficie(o.altitude_ft):
        return                      # superficie fuera del cilindro: no es ascenso

    a = estado["ascenso"].get(icao24)
    if a is None:
        # El ascenso tiene que EMPEZAR cerca. Si la primera posicion aerea ya
        # aparece lejos, no es evidencia de haber salido de este campo.
        if d > RADIO_INFERENCIA_KM:
            return
        estado["ascenso"][icao24] = {
            "t0": o.timestamp, "alt0": o.altitude_ft, "d0": d,
            "t1": o.timestamp, "alt1": o.altitude_ft, "d1": d,
            "n": 1, "suelo_t": suelo,
            "callsign": (o.callsign or "").strip() or None,
            "track": o.track_deg}
        return
    a["t1"], a["alt1"], a["d1"] = o.timestamp, o.altitude_ft, d
    a["n"] += 1
    if o.callsign:
        a["callsign"] = o.callsign.strip()
    if o.track_deg is not None and a["track"] is None:
        a["track"] = o.track_deg


def despegues_inferidos(estado: dict) -> list[dict]:
    """Los despegues que se deducen cruzando dos pasadas, con su evidencia.

    Devuelve dicts y no Operacion para que esta funcion no dependa del registro
    ni de las pistas: quien arma el informe los completa. Cada uno lleva la
    evidencia en texto, porque un numero inferido sin su razon no se puede
    auditar despues -- y este es el unico numero del sistema que no se midio.
    """
    salida = []
    for icao24, a in estado["ascenso"].items():
        gano = a["alt1"] - a["alt0"]
        if gano < ASCENSO_INFERIDO_FT:
            continue                      # no subio lo suficiente
        if a["d1"] <= a["d0"]:
            continue                      # no se alejo: puede estar dando vueltas
        if a["n"] < 2:
            continue                      # un punto suelto no muestra tendencia
        hueco_min = (a["t0"] - a["suelo_t"]) / 60.0
        salida.append({
            "icao24": icao24,
            "timestamp": a["t0"],
            # El instante del contacto en tierra que ancla la deduccion. Lo
            # necesita quien filtra los que YA se midieron: sin esto no se puede
            # saber si el despegue de la lista es este mismo.
            "suelo_t": a["suelo_t"],
            "callsign": a["callsign"],
            "track_deg": a["track"],
            "min_distance_km": a["d0"],
            "min_altitude_ft": a["alt0"],
            "evidencia": (
                f"en tierra sobre el campo y {hueco_min:.0f} min despues "
                f"subiendo de {a['alt0']:.0f} a {a['alt1']:.0f} ft mientras se "
                f"alejaba de {a['d0']:.1f} a {a['d1']:.1f} km"),
        })
    return sorted(salida, key=lambda x: x["timestamp"], reverse=True)


def sentido_de_carrera(r: dict) -> str | None:
    """'frena', 'acelera' o None, leyendo la velocidad de superficie.

    Separada para que la ruta de siempre y la incremental la apliquen igual, y
    para poder probarla sin construir un informe entero. Ver CARRERA_KT.
    """
    vmax = r.get("sup_vel_max")
    if vmax is None or vmax < CARRERA_KT:
        return None
    v0, v1 = r.get("sup_vel_primera"), r.get("sup_vel_ultima")
    if v0 is None or v1 is None:
        return None
    if v0 - v1 >= DELTA_CARRERA_KT:
        return "frena"
    if v1 - v0 >= DELTA_CARRERA_KT:
        return "acelera"
    return None


def resumir_cilindro(observations: list[Observation],
                     cilindro: dict) -> tuple[dict, int, list, dict]:
    """El estado O(1) por PASADA que la clasificacion necesita.

    Es la mitad de informe() que ACUMULA, separada de la que CLASIFICA. Las dos
    rutas -- esta y LectorIncremental -- llaman al mismo
    acumular_en_cilindro() y terminan en el mismo _clasificar().

    No se puede seguir clasificando sobre la lista completa de observaciones
    porque el estado residente ya no las guarda: 313 B/obs son 143 MB a 30 dias
    y 1744 MB al ano.

    El dict que devuelve esta indexado por (icao24, pasada) y NO por icao24.
    Ver HUECO_PASADA_S para por que, y con que numeros.
    """
    estado = nuevo_resumen()
    for o in sorted(observations, key=lambda o: o.timestamp):
        acumular_en_cilindro(estado, o, cilindro)
    return (estado["por_pasada"], estado["posiciones"],
            despegues_inferidos(estado), estado["distintivos"])


def informe_desde_resumen(por_icao: dict, posiciones: int,
                          codigo: str | None = None,
                          identidades: dict | None = None,
                          inferidas: list | None = None,
                          distintivos: dict | None = None) -> Informe | None:
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
    # PASADAS y AERONAVES ya no son el mismo numero, y el que tiene que cuadrar
    # contra las categorias es PASADAS: cada pasada produce una clasificacion, y
    # una aeronave que entro tres veces produce tres. Publicar los dos deja a la
    # vista cuanta actividad repetida hay -- 46 pasadas de 39 aeronaves dice algo
    # que ninguno de los dos numeros solo dice.
    inf.pasadas_en_cilindro = len(por_icao)
    inf.aeronaves_en_cilindro = len({k[0] for k in por_icao})
    # Los inferidos se completan con identidad y pista igual que los medidos
    # -quien los mira necesita saber QUE avion fue- pero quedan en su propia
    # lista y fuera de todos los conteos de categoria.
    inf.posiciones_en_superficie = sum(r.get("superficie", 0) for r in por_icao.values())
    inf.aeronaves_en_superficie = len({k[0] for k, r in por_icao.items() if r.get("superficie")})
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

    for (icao24, pasada), r in por_icao.items():
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

        tipo = _clasificar(minima, baja, sube, en_superficie=en_superficie,
                           carrera=sentido_de_carrera(r))
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
        # EL DISTINTIVO SE CONGELA EN EL INSTANTE DE LA OPERACION. Es lo que la
        # aeronave estaba transmitiendo cuando aterrizo o despego, fechado, y no
        # se puede pisar despues: una pierna posterior escribe su propio tramo,
        # no encima de este. Ver _anotar_distintivo() para el bug que arregla y
        # los numeros que lo miden.
        cs_operacion, cs_fuente = distintivo_en(distintivos, icao24, r["min_t"])
        # El historial completo queda de ULTIMO recurso, no de primero: sigue
        # cubriendo a la aeronave de la que no se fecho ningun distintivo -- que
        # es el caso que resolvia la decision del 2026-08 -- pero ya no le gana
        # a un dato observado en la operacion. Y va marcado como lo que es.
        respaldo = (ident.callsign if ident else None) or r["callsign"]
        inf.operaciones.append(Operacion(
            icao24=icao24, tipo=tipo, timestamp=r["min_t"],
            callsign=(cs_operacion or respaldo),
            callsign_source=(cs_fuente if cs_operacion else
                             ("el último escuchado de esta dirección"
                              if respaldo else None)),
            registration=(ident.registration if ident else None),
            serial_number=(ident.serial_number if ident else None),
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
            pasada=pasada, carrera=sentido_de_carrera(r),
            posiciones_superficie=r.get("superficie", 0),
        ))

    for d in (inferidas or []):
        # NO INFERIR LO QUE YA SE MIDIO. La deduccion existe para los despegues
        # que no dejaron NI UNA posicion en el cilindro; si el mismo avion tiene
        # un despegue medido alrededor de ese contacto en tierra, es el MISMO
        # evento y publicarlo dos veces seria peor que no publicarlo.
        #
        # No es hipotetico: sin este filtro, sobre la base del 2026-09-03 salian
        # 11 inferidos y los 11 eran duplicados de los 12 despegues medidos --
        # aviones cuyo ascenso SI se vio, apenas afuera del radio de 8 km.
        #
        # La ventana es generosa (+-20 min) a proposito: el instante que publica
        # una operacion medida es el punto mas bajo de su pasada, que no tiene
        # por que caer cerca del primer punto del ascenso. Ante la duda, se
        # descarta la deduccion: perder una es barato, duplicarla no.
        margen = 2 * HUECO_PASADA_S
        ya_medido = any(
            op.icao24 == d["icao24"] and op.tipo in ("despegue", "frustrada")
            and d["suelo_t"] - margen <= op.timestamp <= d["timestamp"] + margen
            for op in inf.operaciones)
        if ya_medido:
            inf.inferidas_descartadas += 1
            continue
        ident = identidades.get(d["icao24"]) if identidades else None
        # SIN PISTA a proposito. El unico rumbo que se conoce de estos vuelos es
        # el de la primera posicion del ascenso, medida a 8-15 km del campo y
        # miles de pies arriba: ahi el avion ya viro a su ruta y su rumbo no
        # dice nada de la cabecera que uso. Alinearlo igual publicaria una pista
        # que no se puede sostener, que es peor que no publicar ninguna.
        # Mismo criterio que en las medidas: el distintivo fechado en el instante
        # del despegue, y el historial solo si no hay ninguno.
        cs_inf, cs_inf_fuente = distintivo_en(distintivos, d["icao24"], d["timestamp"])
        inf.inferidas.append({
            **d,
            "tipo": "despegue inferido",
            "inferida": True,
            "callsign": (cs_inf or getattr(ident, "callsign", None) or d.get("callsign")),
            "callsign_source": (cs_inf_fuente if cs_inf
                                else "el último escuchado de esta dirección"),
            "registration": getattr(ident, "registration", None),
            "aircraft_type": getattr(ident, "aircraft_type", None),
            "operator": getattr(ident, "operator", None),
        })

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
    por_icao, posiciones, inferidas, distintivos = resumir_cilindro(observations, cilindro)
    import identidad
    return informe_desde_resumen(por_icao, posiciones, cilindro["codigo"],
                                 identidad.resolver(observations), inferidas,
                                 distintivos)


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
        "pasadas_en_cilindro": inf.pasadas_en_cilindro,
        # Aparte de "operaciones" a proposito: son deducidos, no medidos, y
        # mezclarlos rompería tanto el cuadre como la promesa de no publicar
        # como observado algo que no se observó.
        "inferidas": inf.inferidas,
        "inferidas_descartadas": inf.inferidas_descartadas,
        "advertencia": inf.advertencia,
        "operaciones": [
            {"icao24": o.icao24, "tipo": o.tipo, "timestamp": o.timestamp,
             "callsign": o.callsign, "callsign_source": o.callsign_source,
             "registration": o.registration,
             "serial_number": o.serial_number,
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
             "pasada": o.pasada, "carrera": o.carrera,
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
