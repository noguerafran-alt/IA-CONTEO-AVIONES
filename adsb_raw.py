"""Guardar el HEX CRUDO de la antena, antes de decodificar nada.

Por que existe: hasta ahora el hex se decodificaba y se soltaba. adsb_rtlsdr.py
lo dice en su propio comentario (`position_hex`): la unica trama que sobrevivia
era la ultima con posicion de cada aeronave, en memoria. Todo lo demas se
perdia en el acto.

La consecuencia es que TODO lo que el decodificador no extraia en el momento
era irrecuperable. Las ~10 000 filas grabadas hasta hoy tienen 8 campos porque
`decoded_to_observation` extrae 8 campos, y no hay forma de sacarles un noveno:
el mensaje que lo traia ya no existe. Un cambio en el decodificador no se puede
aplicar a lo ya grabado, solo a lo que venga.

Con el hex guardado eso se invierte. El archivo pasa a ser la fuente, el
decodificador una funcion sobre el archivo, y cualquier campo que hoy se ignore
se puede extraer manana sobre el mismo aire. Cuesta ~30 bytes por mensaje.

Formato: CSV de `epoch,hex,dbfs`. Deliberadamente lo mas tonto posible --
ninguna decision de interpretacion se toma aca, porque cualquier decision que se
tome aca es una que no se va a poder revisar despues.

POR DEFECTO DEMODULA EL IQ CRUDO (adsb_iq.escuchar) Y NO rtl_adsb.exe, y la
diferencia no es de matiz. Medido el 2026-08-25 desde Aeroparque, misma antena,
mismo rato:

    rtl_adsb.exe -e 1     5 mensajes en 600 s   (0.008/s)
    IQ crudo, AGC         5 VERIFICADOS en 40 s (0.125/s)

Unas 15 veces mas por el camino de IQ, y ademas trae el nivel de senal de cada
mensaje, que rtl_adsb tira en el camino. `--rtl-adsb` conserva el camino viejo
para poder comparar, que es la unica razon por la que sigue existiendo.

Uso:
  python adsb_raw.py                     grabar hasta Ctrl-C (IQ crudo)
  python adsb_raw.py --seconds 600       grabar 10 minutos
  python adsb_raw.py --rtl-adsb          el demodulador viejo, para comparar
"""
from __future__ import annotations

import argparse
import csv
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from adsb_rtlsdr import DEFAULT_EXE, parse_avr_line

RAW_DIR = Path(__file__).parent / "output" / "adsb_raw"
COLUMNAS = ["epoch", "hex", "dbfs"]
# -e 1 y no el 5 por defecto: esta medido sobre ESTA antena en adsb_rtlsdr.py
# (-e 5 da 219 mensajes con 4 validos; -e 1 da 139 con 79). El default emite
# casi puro ruido, y guardarlo llenaria el archivo de direcciones inventadas.
ERROR_BITS = "1"


def _ruta_compatible(base: Path) -> Path:
    """La primera ruta cuyo encabezado coincida con COLUMNAS, o una nueva.

    Mismo motivo que en adsb_record.py: el archivo se abre en append, y un
    archivo escrito con otro juego de columnas recibiria filas desalineadas
    bajo el encabezado equivocado, sin un solo error.
    """
    candidata, sufijo = base, 0
    while candidata.exists() and candidata.stat().st_size > 0:
        try:
            with candidata.open(encoding="utf-8", newline="") as fh:
                if next(csv.reader(fh), None) == COLUMNAS:
                    return candidata
        except OSError:
            pass
        sufijo += 1
        candidata = base.with_name(f"{base.stem}.{sufijo}{base.suffix}")
    return candidata


def capturar_iq(out: Path | None = None, seconds: float | None = None,
                ganancia: str | None = None) -> Path:
    """Grabar hex crudo demodulando el IQ. Es el camino bueno; ver el docstring."""
    import adsb_iq

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if out is None:
        out = RAW_DIR / f"raw_{datetime.now().strftime('%Y-%m-%d')}.csv"
    out = _ruta_compatible(out)
    nuevo = not out.exists() or out.stat().st_size == 0

    fin = None if seconds is None else time.time() + seconds
    total = 0
    with out.open("a", newline="", encoding="utf-8") as fh:
        escritor = csv.writer(fh)
        if nuevo:
            escritor.writerow(COLUMNAS)
        fh.flush()
        print(f"grabando hex crudo (IQ) en {out}", flush=True)
        ultimo_flush = time.monotonic()
        try:
            for mensaje in adsb_iq.escuchar(ganancia=ganancia):
                escritor.writerow([f"{time.time():.3f}", mensaje["hex"],
                                   f"{mensaje['dbfs']:.1f}"])
                total += 1
                ahora = time.monotonic()
                if ahora - ultimo_flush >= 2.0:
                    fh.flush()
                    ultimo_flush = ahora
                    print(f"  {total} mensajes", flush=True)
                # Aca el chequeo por reloj SI alcanza, al reves que en el camino
                # de rtl_adsb: escuchar() entrega por bloques de 0,5 s hasta con
                # el aire vacio, asi que el bucle no se queda bloqueado
                # esperando un mensaje que no llega.
                if fin is not None and time.time() > fin:
                    break
        except KeyboardInterrupt:
            pass
        fh.flush()
    print(f"{total} mensajes guardados en {out}")
    return out


def capturar(exe: Path = DEFAULT_EXE, out: Path | None = None,
             seconds: float | None = None, error_bits: str = ERROR_BITS) -> Path:
    """Grabar hex crudo hasta el timeout o hasta Ctrl-C. Devuelve el archivo."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if out is None:
        dia = datetime.now().strftime("%Y-%m-%d")
        out = RAW_DIR / f"raw_{dia}.csv"

    out = _ruta_compatible(out)
    nuevo = not out.exists() or out.stat().st_size == 0
    proceso = subprocess.Popen(
        [str(Path(exe).resolve()), "-e", error_bits],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1)

    total = 0
    parar = {"si": False}

    # El plazo se hace cumplir TERMINANDO el proceso desde un timer, y no
    # chequeando el reloj adentro del bucle. Es la diferencia entre un plazo
    # real y uno aparente: `for linea in proceso.stdout` se bloquea esperando
    # datos, asi que un chequeo adentro del bucle solo corre cuando llega un
    # mensaje -- y si el aire se calla, no llega ninguno y el plazo no vence
    # nunca. Medido en carne propia: una captura de 720 s quedo colgada.
    # Al terminar el proceso, el pipe da EOF, el bucle sale solo y el finally
    # cierra el archivo con todo adentro.
    temporizador = None
    if seconds is not None:
        temporizador = threading.Timer(seconds, proceso.terminate)
        temporizador.daemon = True
        temporizador.start()

    def _handler(*_):
        parar["si"] = True
    try:
        signal.signal(signal.SIGINT, _handler)
    except ValueError:
        pass  # no es el hilo principal; --seconds sigue funcionando

    try:
        with out.open("a", newline="", encoding="utf-8") as fh:
            escritor = csv.writer(fh)
            if nuevo:
                escritor.writerow(COLUMNAS)
            fh.flush()  # el encabezado, YA: un archivo de 0 bytes durante
            # minutos es indistinguible de un capturador roto, y el flush por
            # lotes de mas abajo no alcanza para descartarlo.
            print(f"grabando hex crudo en {out}", flush=True)
            ultimo_flush = time.monotonic()
            for linea in proceso.stdout:
                # El timestamp se toma al LEER la linea, no al recibir la onda:
                # rtl_adsb no expone el reloj de muestreo por esta salida, asi
                # que esto lleva el jitter del buffer del pipe (milisegundos).
                # Alcanza de sobra para ordenar mensajes y agruparlos por
                # aeronave; NO alcanza para multilateracion, que necesita
                # nanosegundos y ademas varios receptores.
                ahora = time.time()
                hex_msg = parse_avr_line(linea)
                if hex_msg:
                    # dbfs vacio y no 0: rtl_adsb tira la magnitud, no la
                    # mide como cero. Un 0 ahi seria una medicion inventada.
                    escritor.writerow([f"{ahora:.3f}", hex_msg, ""])
                    total += 1
                    # Por TIEMPO y no cada N mensajes. Un umbral por cantidad
                    # no tiene cota temporal: con el cielo flojo, 200 mensajes
                    # pueden ser diez minutos, y en esos diez minutos el
                    # archivo miente sobre lo que ya se recibio. Es el mismo
                    # razonamiento que el commit_interval_s de adsb_record.py.
                    ahora_m = time.monotonic()
                    if ahora_m - ultimo_flush >= 2.0:
                        fh.flush()
                        ultimo_flush = ahora_m
                        print(f"  {total} mensajes", flush=True)
                if parar["si"]:
                    break
            fh.flush()
    finally:
        if temporizador is not None:
            temporizador.cancel()
        proceso.terminate()
        try:
            proceso.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proceso.kill()
        err = (proceso.stderr.read() or "").strip() if proceso.stderr else ""
        if total == 0 and err:
            # Sin esto un dongle desenchufado se ve igual que un cielo vacio.
            print(f"rtl_adsb no entrego mensajes. Dijo:\n{err}", file=sys.stderr)

    print(f"{total} mensajes guardados en {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--error-bits", default=ERROR_BITS)
    ap.add_argument("--ganancia", default=None,
                    help="'auto' (AGC) por defecto; ver adsb_iq.py")
    ap.add_argument("--rtl-adsb", action="store_true",
                    help="usar rtl_adsb.exe en vez del IQ crudo (peor; para comparar)")
    args = ap.parse_args()
    if args.rtl_adsb:
        capturar(exe=args.exe, out=args.out, seconds=args.seconds,
                 error_bits=args.error_bits)
    else:
        capturar_iq(out=args.out, seconds=args.seconds, ganancia=args.ganancia)


if __name__ == "__main__":
    main()
