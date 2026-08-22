"""Demodular ADS-B desde el IQ crudo del RTL-SDR, midiendo el nivel de senal.

Por que existe: rtl_adsb.exe entrega solo el hex y tira la magnitud de la senal
en el camino, asi que por ese camino es IMPOSIBLE saber si un mensaje llego
fuerte o raspando el ruido. Ese dato hace falta para dos cosas concretas de
este proyecto:

  - Medir la antena de verdad. "Alcance 72 km" no dice si llego justo o con
    margen; el dBFS a esa distancia si. Tambien delata si la ganancia esta
    saturando, que no se puede ver de ninguna otra forma.
  - Decidir donde poner la antena con numeros en vez de intuicion.

LO QUE EL NIVEL DE SENAL NO SIRVE PARA HACER, medido y descartado. La idea
original era usarlo para separar ruido de trafico real en el primer mensaje,
en vez de esperar a que una direccion se repita (ver la compuerta de
adsb_rtlsdr.py). No funciona, y no por poco. Sobre 40 s de aire:

    poblacion                            n   mediana        rango
    ADS-B DF17/18 con CRC valido        93   -15.0 dBFS   hasta -32.8
    ruido con forma de DF17, CRC mal    45   -18.3 dBFS   -34.0 a -3.9

El 89% del ruido llega MAS FUERTE que el ADS-B real mas debil, y el ruido
alcanza -3.9 dBFS, arriba de la mediana de lo real. En las tramas cortas pasa
lo mismo: las direcciones repetidas (reales) dan -17.9 de mediana contra -19.0
las vistas una sola vez, 1.1 dB de diferencia con las distribuciones
superpuestas.

Tiene sentido visto en retrospectiva: lo que pasa el test de preambulo no es
ruido termico -eso seria debil- sino energia de radio de verdad, interferencia
y mensajes Mode-S solapados que por casualidad tienen la forma. Un umbral de
dBFS tiraria trafico real y dejaria pasar ruido. La repeticion de direccion
sigue siendo el unico discriminador que funciona.

El metodo es el de dump1090 (github.com/antirez/dump1090, dump1090.c), leido
para escribir esto:

  - Magnitud: el IQ del RTL-SDR son dos bytes sin signo por muestra, centrados
    en 127. computeMagnitudeVector hace |i|, |q| y sqrt(i^2+q^2) por tabla de
    lookup de 129x129 escalada x360, porque en C un sqrt por muestra era caro.
    Aca la tabla se conserva NO por velocidad -numpy vectoriza el sqrt igual de
    rapido- sino para dar exactamente los mismos numeros que dump1090 y poder
    comparar contra el.
  - Preambulo: las 10 relaciones entre las primeras muestras (dump1090.c:1602)
    mas el chequeo de que las muestras del medio esten por debajo de 2/3 del
    promedio de los picos (dump1090.c:1624).
  - Bits: PPM a 1 bit por microsegundo. A 2 Msps son 2 muestras por bit, y el
    bit es 1 si la primera mitad pesa mas que la segunda (dump1090.c:1669).

Lo que el dump1090 de antirez NO trae, y se agrega aca: el nivel de senal por
mensaje. Su struct modesMessage no tiene ningun campo de senal -verificado
leyendo la fuente-; el RSSI aparecio despues, en dump1090-fa. Se calcula como
la potencia media de las muestras del mensaje sobre la escala completa, en
dBFS, que es la convencion de dump1090-fa para su campo rssi: 0 dBFS es
saturacion y los valores utiles caen entre -3 y -40 dBFS.

Uso:
  python adsb_iq.py --probar       autoverificacion sin antena
  python adsb_iq.py --escuchar     demodular en vivo mostrando el dBFS
"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

from adsb_rtlsdr import RtlAdsbRecorder

DEFAULT_EXE = Path(__file__).parent / "tools" / "rtlsdr" / "rtl_sdr.exe"

FREQ_HZ = 1_090_000_000
SAMPLE_RATE = 2_000_000        # 2 muestras por bit: el minimo que permite PPM
PREAMBLE_US = 8
PREAMBLE_SAMPLES = PREAMBLE_US * 2
LONG_BITS = 112
SHORT_BITS = 56
# Los DF que usan trama larga (112 bits). El resto son de 56.
DF_LARGOS = frozenset((16, 17, 18, 19, 20, 21, 24))
# DF cuya paridad va XOR-eada con la direccion del avion: su sindrome de CRC no
# es cero ni un error, es la direccion. No se pueden validar solas.
DF_VERIFICABLES_POR_DIRECCION = frozenset((0, 4, 5, 11, 16, 20, 21))

# Magnitud maxima posible: i y q llegan a 128 en valor absoluto, y la tabla de
# dump1090 escala x360 -> round(sqrt(128^2+128^2)*360) = 65167, que entra justo
# en uint16. Es el 0 dBFS de la escala.
MAX_MAG = int(round((128 ** 2 + 128 ** 2) ** 0.5 * 360))


def _tabla_magnitud() -> np.ndarray:
    """La tabla 129x129 de dump1090: maglut[i][q] = round(sqrt(i^2+q^2)*360)."""
    eje = np.arange(129, dtype=np.float64)
    return np.round(np.sqrt(eje[:, None] ** 2 + eje[None, :] ** 2) * 360).astype(np.uint16)


MAGLUT = _tabla_magnitud()


def magnitud(crudo: np.ndarray) -> np.ndarray:
    """IQ entrelazado de 8 bits -> vector de magnitud, como computeMagnitudeVector."""
    iq = crudo.astype(np.int16) - 127
    i = np.abs(iq[0::2])
    q = np.abs(iq[1::2])
    # El recorte a 128 es necesario y no cosmetico: un byte de 255 da 255-127 =
    # 128, justo el borde de la tabla, mientras 0 da -127 -> 127. Sin recortar,
    # un indice de 128 es valido pero uno mayor desbordaria la tabla.
    np.clip(i, 0, 128, out=i)
    np.clip(q, 0, 128, out=q)
    return MAGLUT[i, q]


def buscar_preambulos(m: np.ndarray) -> np.ndarray:
    """Indices donde arranca un preambulo Mode-S plausible.

    Vectorizado sobre todo el bloque en vez de recorrer muestra por muestra: en
    C el bucle es lo natural, en Python serian 2 millones de iteraciones por
    segundo de aire y no llegaria ni cerca del tiempo real. Medido: magnitud
    mas esta busqueda corren a ~26x tiempo real.
    """
    largo = len(m) - (PREAMBLE_SAMPLES + LONG_BITS * 2)
    if largo <= 0:
        return np.empty(0, dtype=np.int64)
    s = [m[k:k + largo].astype(np.int32) for k in range(10)]

    # Las 10 relaciones de dump1090.c:1602. Los picos del preambulo Mode-S
    # caen en los medios bits 0, 2, 7 y 9.
    ok = ((s[0] > s[1]) & (s[1] < s[2]) & (s[2] > s[3]) & (s[3] < s[0]) &
          (s[4] < s[0]) & (s[5] < s[0]) & (s[6] < s[0]) &
          (s[7] > s[8]) & (s[8] < s[9]) & (s[9] > s[6]))

    # Y las muestras entre los dos picos por debajo de 2/3 del promedio de los
    # picos. dump1090 lo escribe como suma/6, que es (suma/4) * (2/3).
    alto = (s[0] + s[2] + s[7] + s[9]) // 6
    ok &= (s[4] < alto) & (s[5] < alto)
    return np.flatnonzero(ok)


def _bits_y_errores(m: np.ndarray, j: int, nbits: int) -> tuple[np.ndarray, int]:
    """Demodular nbits desde el preambulo en j. PPM: 1 si la primera mitad pesa mas."""
    base = j + PREAMBLE_SAMPLES
    baja = m[base:base + nbits * 2:2].astype(np.int32)
    alta = m[base + 1:base + 1 + nbits * 2:2].astype(np.int32)
    bits = (baja > alta).astype(np.uint8)
    # Dos muestras iguales no son un bit: es la firma de haber tomado ruido por
    # preambulo (dump1090.c:1677). Se cuentan para descartar el candidato.
    return bits, int(np.count_nonzero(baja == alta))


def _a_hex(bits: np.ndarray) -> str:
    return np.packbits(bits).tobytes().hex().upper()


def nivel_dbfs(m: np.ndarray, j: int, nbits: int) -> float:
    """Nivel de senal del mensaje en dBFS: 0 es saturacion.

    Potencia media de las muestras del mensaje sobre la potencia de escala
    completa, en decibeles. Es la convencion de dump1090-fa para su campo rssi,
    y por eso estos numeros son comparables con los que reporta un dump1090.
    """
    seg = m[j + PREAMBLE_SAMPLES:j + PREAMBLE_SAMPLES + nbits * 2].astype(np.float64)
    if not seg.size:
        return -100.0
    potencia = float(np.mean(seg * seg)) / (MAX_MAG ** 2)
    return 10.0 * float(np.log10(potencia)) if potencia > 0 else -100.0


_TABLA_SINDROME: dict[int, dict[int, int]] = {}


def _tabla_sindrome(nbits: int) -> dict[int, int]:
    """Sindrome del CRC -> que bit vino dado vuelta.

    El CRC de Mode-S es lineal sobre GF(2): crc(m XOR e) == crc(m) XOR crc(e).
    Verificado ejecutando: dar vuelta el bit 40 de un mensaje valido deja
    sindrome 9553791, identico al crc del patron que tiene SOLO ese bit
    prendido. De ahi sale que el sindrome identifica el bit fallado, y corregir
    un bit es una busqueda en dict y no 112 recalculos de CRC por candidato.
    Eso importa: con ~6800 candidatos de preambulo por segundo de aire
    (medido), probar bit por bit serian 760 mil CRC por segundo y no habria
    tiempo real posible.

    Los 112 sindromes son distintos entre si, y los 56 tambien: no hay
    colisiones, asi que la correccion no es ambigua.
    """
    if nbits not in _TABLA_SINDROME:
        from pyModeS.util import crc
        nbytes = nbits // 8
        tabla = {}
        for k in range(nbits):
            patron = bytearray(nbytes)
            patron[k // 8] ^= 0x80 >> (k % 8)
            tabla[crc(patron.hex().upper())] = k
        _TABLA_SINDROME[nbits] = tabla
    return _TABLA_SINDROME[nbits]


def _corregir_un_bit(bits: np.ndarray, sindrome: int, nbits: int) -> np.ndarray | None:
    """Devolver los bits con el unico bit fallado corregido, o None."""
    posicion = _tabla_sindrome(nbits).get(sindrome)
    if posicion is None:
        return None
    arreglado = bits.copy()
    arreglado[posicion] ^= 1
    return arreglado


def demodular(m: np.ndarray, max_errores: int = 2) -> list[dict]:
    """Todos los mensajes con CRC valido de un bloque de magnitud.

    Solo devuelve lo que pasa CRC: en ruido puro los candidatos de preambulo
    son decenas por segundo y ninguno sobrevive un CRC de 24 bits.
    """
    from pyModeS.util import crc

    salida: list[dict] = []
    fin_anterior = -1
    for indice in buscar_preambulos(m):
        j = int(indice)
        if j <= fin_anterior:
            # Un mensaje ya decodificado ocupa estas muestras: un "preambulo"
            # dentro de el es su propio contenido, no una trama nueva.
            continue
        bits, errores = _bits_y_errores(m, j, LONG_BITS)
        if bits.size < LONG_BITS or errores > max_errores:
            continue
        df = int(np.packbits(bits[:8])[0]) >> 3
        nbits = LONG_BITS if df in DF_LARGOS else SHORT_BITS
        recorte = bits[:nbits]
        hexa = _a_hex(recorte)
        sindrome = crc(hexa)
        corregido = False

        if sindrome != 0:
            # Solo DF17/18 llevan la paridad SIN mezclar, asi que son los unicos
            # donde sindrome != 0 significa "trama fallada" y se puede corregir.
            # En DF0/4/5/16/20/21 la paridad va XOR-eada con la direccion del
            # avion, asi que el sindrome ES la direccion y nunca da cero: pedirle
            # cero a esas tramas era descartarlas todas. Se emiten con
            # crc_verificable=False y quien las consuma decide (adsb_rtlsdr.py
            # las cree recien cuando la direccion se repite).
            if df in (17, 18):
                arreglado = _corregir_un_bit(recorte, sindrome, nbits)
                if arreglado is None:
                    continue
                recorte = arreglado
                hexa = _a_hex(recorte)
                if crc(hexa) != 0:
                    continue
                corregido = True
            elif df not in DF_VERIFICABLES_POR_DIRECCION:
                continue

        salida.append({"hex": hexa, "df": df, "muestra": j,
                       "dbfs": round(nivel_dbfs(m, j, nbits), 1),
                       "crc_verificable": df in (17, 18),
                       "corregido": corregido})
        fin_anterior = j + PREAMBLE_SAMPLES + nbits * 2
    return salida


# ---------------------------------------------------------------------------
# Modulador: existe solo para poder verificar el demodulador sin antena.
# ---------------------------------------------------------------------------
def modular(hexa: str, amplitud: float = 1.0, ruido: float = 0.0,
            semilla: int = 0) -> np.ndarray:
    """Convertir un mensaje hex en IQ crudo, para probar el camino de vuelta.

    Sin esto la unica forma de verificar el demodulador seria enchufar la
    antena y esperar que pase un avion, y el dBFS no se podria comprobar de
    ninguna manera: al aire no se sabe con que potencia salio el mensaje.
    Modulando con amplitud conocida, el nivel medido se compara contra el
    esperado.
    """
    bits = np.unpackbits(np.frombuffer(bytes.fromhex(hexa), dtype=np.uint8))
    envolvente = np.zeros(PREAMBLE_SAMPLES + len(bits) * 2, dtype=np.float64)
    # Preambulo: pulsos en los medios bits 0, 2, 7 y 9.
    for k in (0, 2, 7, 9):
        envolvente[k] = 1.0
    for posicion, bit in enumerate(bits):
        # PPM: el pulso va en la primera mitad si el bit es 1, en la segunda si es 0.
        envolvente[PREAMBLE_SAMPLES + posicion * 2 + (0 if bit else 1)] = 1.0

    # Silencio a los costados: el demodulador necesita muestras alrededor para
    # evaluar las relaciones del preambulo.
    envolvente = np.concatenate([np.zeros(40), envolvente * amplitud, np.zeros(40)])
    if ruido:
        rng = np.random.default_rng(semilla)
        envolvente = envolvente + np.abs(rng.normal(0.0, ruido, envolvente.size))
    np.clip(envolvente, 0.0, 1.0, out=envolvente)

    # La magnitud pedida se reparte entre I y Q por igual: |i| = |q| = mag/sqrt(2).
    componente = envolvente * 128.0 / np.sqrt(2.0)
    iq = np.empty(envolvente.size * 2, dtype=np.uint8)
    valor = np.clip(127 + componente, 0, 255).astype(np.uint8)
    iq[0::2] = valor
    iq[1::2] = valor
    return iq


def escuchar(exe: Path = DEFAULT_EXE, ganancia: str = "49.6",
             segundos_por_bloque: float = 0.5):
    """Generador de mensajes demodulados en vivo desde rtl_sdr.exe."""
    exe = Path(exe).resolve()
    if not exe.exists():
        raise FileNotFoundError(f"no se encontro {exe}")
    bytes_bloque = int(SAMPLE_RATE * segundos_por_bloque) * 2
    proceso = subprocess.Popen(
        [str(exe), "-f", str(FREQ_HZ), "-s", str(SAMPLE_RATE), "-g", ganancia, "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=bytes_bloque * 2)
    try:
        # Se arrastra la cola del bloque anterior porque un mensaje puede caer
        # partido entre dos lecturas: sin esto se perderia uno cada tanto, de
        # forma dependiente del tamano de bloque, que es el peor tipo de bug.
        cola = np.empty(0, dtype=np.uint8)
        solapamiento = (PREAMBLE_SAMPLES + LONG_BITS * 2) * 2
        while True:
            datos = proceso.stdout.read(bytes_bloque)
            if not datos:
                break
            bloque = np.concatenate([cola, np.frombuffer(datos, dtype=np.uint8)])
            if bloque.size % 2:
                bloque = bloque[:-1]
            for mensaje in demodular(magnitud(bloque)):
                yield mensaje
            cola = bloque[-solapamiento:] if bloque.size > solapamiento else bloque
    finally:
        proceso.terminate()
        if proceso.stderr:
            error = (proceso.stderr.read() or b"").decode("utf-8", "replace").strip()
            if error:
                print(f"  rtl_sdr: {error}", file=sys.stderr)


class IqRecorder(RtlAdsbRecorder):
    """Grabador que lee el IQ crudo y anota el nivel de senal de cada mensaje.

    Hereda de RtlAdsbRecorder y NO reimplementa nada de la parte semantica: la
    compuerta de confirmacion de direcciones, el historial rodante, el conteo y
    la interfaz publica (start/stop/around/snapshot/is_receiving) son las
    mismas. Lo unico que cambia es de donde salen los mensajes: en vez de leer
    lineas AVR de rtl_adsb.exe, lee muestras de rtl_sdr.exe y las demodula aca,
    lo que ademas devuelve el dBFS que el otro camino tira.

    Tener dos copias de la compuerta de aceptacion seria la peor duplicacion
    posible: dos reglas sobre que datos entran, capaces de divergir en silencio.
    """
    ganancia: str = "49.6"
    segundos_por_bloque: float = 0.5

    def start(self) -> "IqRecorder":
        if self._thread and self._thread.is_alive():
            return self
        self.exe_path = Path(self.exe_path).resolve()
        if not self.exe_path.exists():
            self.last_error = f"no se encontro {self.exe_path}"
            return self
        self._validate_surface_ref()
        import pyModeS as pms
        self._decoder = pms.PipeDecoder(surface_ref=self.surface_ref,
                                        local_ref_window=self.local_ref_window)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _run_once(self) -> None:
        # El nivel de senal viaja hasta la Observation, que es todo el punto de
        # este camino. No se usa para filtrar: se midio y el ruido llega igual
        # de fuerte que el trafico real (ver el docstring del modulo), asi que
        # es un dato para medir la antena, no un criterio de aceptacion.
        for mensaje in escuchar(exe=self.exe_path, ganancia=self.ganancia,
                                segundos_por_bloque=self.segundos_por_bloque):
            if self._stop.is_set():
                break
            self._procesar_hex(mensaje["hex"], mensaje["dbfs"])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--escuchar", action="store_true", help="demodular en vivo")
    parser.add_argument("--probar", action="store_true", help="autoverificacion sin antena")
    parser.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    parser.add_argument("--ganancia", default="49.6")
    args = parser.parse_args()

    if args.probar:
        from pyModeS.util import crc
        print(f"escala completa: {MAX_MAG} (0 dBFS)\n")
        print(f"{'MENSAJE':30s} {'AMPLITUD':>9s} {'dBFS':>7s}  RESULTADO")
        print("-" * 70)
        original = "8D4840D6202CC371C32CE0576098"
        for amplitud in (1.0, 0.5, 0.25, 0.1, 0.05):
            iq = modular(original, amplitud=amplitud)
            hallados = demodular(magnitud(iq))
            if hallados:
                h = hallados[0]
                estado = "OK" if h["hex"] == original else f"DISTINTO {h['hex']}"
                print(f"{h['hex'][:28]:30s} {amplitud:9.2f} {h['dbfs']:7.1f}  {estado}")
            else:
                print(f"{'(no se demodulo)':30s} {amplitud:9.2f} {'-':>7s}  PERDIDO")
        print()
        # El dBFS tiene que caer ~6 dB por cada mitad de amplitud: la potencia
        # va con el cuadrado, y 10*log10(1/4) = -6.02 dB.
        n1 = demodular(magnitud(modular(original, amplitud=1.0)))[0]["dbfs"]
        n2 = demodular(magnitud(modular(original, amplitud=0.5)))[0]["dbfs"]
        print(f"caida al mitad de amplitud: {n2 - n1:.2f} dB (esperado ~-6.02)")
        raise SystemExit(0)

    if args.escuchar:
        print(f"Escuchando 1090 MHz via {args.exe} (Ctrl+C para salir)\n")
        print(f"{'HEX':30s} {'DF':>3s} {'dBFS':>7s}")
        try:
            for mensaje in escuchar(exe=args.exe, ganancia=args.ganancia):
                print(f"{mensaje['hex']:30s} {mensaje['df']:3d} {mensaje['dbfs']:7.1f}")
        except KeyboardInterrupt:
            print("\ncortado")
        raise SystemExit(0)

    parser.error("indica --probar o --escuchar")
